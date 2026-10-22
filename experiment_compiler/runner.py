"""Explicit, separate CWL execution handoff for reviewed Compiled Experiments.

This module is deliberately not imported by compile/verify commands. Execution
requires an exact package digest, explicit opt-in, and a declared bounded worker.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import zipfile

from .core import (MANIFEST, PackageError, bounded_read, canonical, compile_package,
                   json_value, safe_path, verify_bytes)


_CONTAINER_JOURNAL_SCHEMA = "cwltool-container-journal/v1"
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ATTEMPT_CONTAINERS = 64
_MAX_RECOVERY_AGE_SECONDS = 7 * 24 * 60 * 60


def _read_package(data: bytes, expected_sha256: str) -> tuple[dict, dict[str, bytes]]:
    verified = verify_bytes(data, expected_sha256=expected_sha256)
    if verified["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("runner accepts only compiled-experiment-lifecycle-v1 plans and result artifacts")
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if name != MANIFEST}
        manifest = json_value(archive.read(MANIFEST))
    return manifest, payload


def _dependency_closure(payload: dict[str, bytes]) -> dict:
    for required in ("dependency-closure.json", "ro-crate-metadata.json"):
        if required not in payload:
            raise PackageError(f"execution admission requires packaged {required}")
    closure = json_value(payload["dependency-closure.json"])
    if not isinstance(closure, dict) or not isinstance(closure.get("classifications"), dict):
        raise PackageError("dependency closure has no classifications")
    classes = closure["classifications"]
    if classes.get("unavailable"):
        raise PackageError("execution deferred: dependency closure has unavailable prerequisites")
    if classes.get("external"):
        raise PackageError("execution deferred: this local adapter admits embedded runtime dependencies only")
    if closure.get("runtimeNetworkRequired") is not False:
        raise PackageError("execution deferred: local adapter requires a declared no-network protocol")
    return closure


def _runner_config(payload: dict[str, bytes]) -> dict:
    for required in ("experiment/workflow.cwl", "experiment/job.yml", "experiment/runner.json"):
        if required not in payload:
            raise PackageError(f"execution deferred: plan has no admitted executable component {required}")
    runner = json_value(payload["experiment/runner.json"])
    if not isinstance(runner, dict) or runner.get("runner") != "cwltool" or not isinstance(runner.get("version"), str):
        raise PackageError("runner.json must pin the cwltool runner and exact version")
    if runner.get("executionMode") not in {"cwltool-docker", "cwltool-podman"}:
        raise PackageError("unsupported execution mode; this adapter requires cwltool Docker or Podman execution")
    timeout_seconds = runner.get("maxWallSeconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 86400:
        raise PackageError("runner.json must declare maxWallSeconds between 1 and 86400")
    try:
        installed = importlib.metadata.version("cwltool")
    except importlib.metadata.PackageNotFoundError as exc:
        raise PackageError("pinned cwltool is not installed in this runner environment") from exc
    if installed != runner["version"]:
        raise PackageError(f"cwltool version mismatch: package requires {runner['version']}, installed {installed}")
    return runner


def _worker_admission(payload: dict[str, bytes], runner: dict) -> tuple[dict, str, set[str]]:
    workflow = payload["experiment/workflow.cwl"]
    if len(workflow) > 1024 * 1024:
        raise PackageError("CWL workflow exceeds the execution admission size limit")
    sandbox = os.environ.get("EXPERIMENT_RUNNER_SANDBOX")
    image_id = os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE")
    raw_limits = os.environ.get("EXPERIMENT_RUNNER_RESOURCE_LIMITS")
    tmpfs_root = os.environ.get("EXPERIMENT_RUNNER_TMPFS_ROOT")
    if not sandbox or not image_id or not raw_limits or not tmpfs_root:
        raise PackageError("execution deferred: run inside the configured bounded worker and report its limits")
    try:
        import yaml
    except ImportError as exc:
        raise PackageError("execution admission requires the pinned CWL runner's YAML parser") from exc
    try:
        limits = json.loads(raw_limits)
        workflow_document = yaml.safe_load(workflow)
    except (ValueError, yaml.YAMLError) as exc:
        raise PackageError(f"execution admission cannot parse worker limits or CWL: {exc}") from exc
    input_ids = _admit_workflow(workflow_document, limits, runner.get("workerImageBase"))
    try:
        job_document = yaml.safe_load(payload["experiment/job.yml"])
    except (ValueError, yaml.YAMLError) as exc:
        raise PackageError(f"execution admission cannot parse the CWL job file: {exc}") from exc
    _admit_job(job_document, payload, input_ids)
    return limits, tmpfs_root, input_ids


def _admit(payload: dict[str, bytes]) -> tuple[dict, dict, dict, str]:
    closure = _dependency_closure(payload)
    runner = _runner_config(payload)
    limits, tmpfs_root, _ = _worker_admission(payload, runner)
    return closure, runner, limits, tmpfs_root


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _admit_workflow(document: Any, limits: Any, expected_image: Any, *, native_projection: bool = False) -> set[str]:
    # This MVP intentionally supports one self-contained CommandLineTool. A
    # Workflow can point `run` at another CWL document; inspecting only the
    # entrypoint would let that document bypass all requirements below.
    if not isinstance(document, dict) or document.get("class") != "CommandLineTool":
        raise PackageError("execution deferred: only a single top-level CWL CommandLineTool is admitted")
    declared_inputs = document.get("inputs")
    if not isinstance(declared_inputs, dict) or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)
            for name in declared_inputs):
        raise PackageError("execution deferred: CommandLineTool inputs must be a mapping with simple names")

    def reject_document_references(value: Any, seen: set[int]) -> None:
        if isinstance(value, dict):
            if id(value) in seen:
                return
            seen.add(id(value))
            for key, child in value.items():
                if _is_document_reference_key(key):
                    raise PackageError(f"execution deferred: CWL document reference {key!r} is outside the single-tool subset")
                reject_document_references(child, seen)
        elif isinstance(value, list):
            if id(value) in seen:
                return
            seen.add(id(value))
            for child in value:
                reject_document_references(child, seen)

    reject_document_references(document, set())
    required_limits = {"cores", "ramMiB", "tmpdirMiB", "outdirMiB", "wallSeconds"}
    if not isinstance(limits, dict) or not required_limits <= set(limits):
        raise PackageError("worker must declare CPU, memory, temporary/output storage and wall-time limits")
    numeric_limits = {key: limits[key] for key in required_limits}
    if any(not _finite_number(value) or value <= 0 for value in numeric_limits.values()):
        raise PackageError("worker resource limits must be finite positive numbers")
    tools: list[dict] = []

    def walk(value: Any, seen: set[int]) -> None:
        if isinstance(value, dict):
            if id(value) in seen:
                return
            seen.add(id(value))
            if value.get("class") == "CommandLineTool":
                tools.append(value)
            for child in value.values():
                walk(child, seen)
        elif isinstance(value, list):
            if id(value) in seen:
                return
            seen.add(id(value))
            for child in value:
                walk(child, seen)

    walk(document, set())
    if not tools:
        raise PackageError("execution admission requires at least one CWL CommandLineTool")
    for tool in tools:
        requirements = tool.get("requirements", {})
        if not isinstance(requirements, dict):
            raise PackageError("CWL requirements must be a mapping")
        if tool.get("hints"):
            raise PackageError("execution deferred: CWL hints are outside this adapter's reviewed subset")
        allowed = {"NetworkAccess", "ResourceRequirement", "ToolTimeLimit", "DockerRequirement"}
        unknown = set(requirements) - allowed
        if unknown:
            raise PackageError(f"execution deferred: unsupported CWL requirements: {sorted(unknown)}")
        network = requirements.get("NetworkAccess")
        if not isinstance(network, dict) or network.get("networkAccess") is not False:
            raise PackageError("execution deferred: every CommandLineTool must explicitly disable network access")
        if set(network) != {"networkAccess"}:
            raise PackageError("execution deferred: unsupported NetworkAccess fields")
        _admit_container_requirement(requirements, expected_image, native_projection)
        resources = requirements.get("ResourceRequirement")
        time_limit = requirements.get("ToolTimeLimit")
        if not isinstance(resources, dict) or not isinstance(time_limit, dict):
            raise PackageError("execution deferred: each CommandLineTool needs explicit resources and a time limit")
        resource_fields = {"coresMin", "coresMax", "ramMin", "ramMax", "tmpdirMin", "tmpdirMax",
                           "outdirMin", "outdirMax"}
        if set(resources) != resource_fields or set(time_limit) != {"timelimit"}:
            raise PackageError("execution deferred: unsupported or incomplete resource/time fields")
        mapping = (("coresMin", "coresMax", "cores"),
                   ("ramMin", "ramMax", "ramMiB"),
                   ("tmpdirMin", "tmpdirMax", "tmpdirMiB"),
                   ("outdirMin", "outdirMax", "outdirMiB"))
        for minimum, maximum, worker_key in mapping:
            requested_min = resources.get(minimum)
            requested_max = resources.get(maximum)
            if (not _finite_number(requested_min) or not _finite_number(requested_max) or
                    requested_min <= 0 or requested_max < requested_min or
                    requested_max > numeric_limits[worker_key]):
                raise PackageError(f"execution deferred: CWL {minimum}/{maximum} exceeds or omits worker limit {worker_key}")
        if numeric_limits["outdirMiB"] > numeric_limits["tmpdirMiB"]:
            raise PackageError("execution deferred: output directory limit exceeds the bounded tmpfs")
        wall_seconds = time_limit.get("timelimit")
        if (type(wall_seconds) is not int or wall_seconds <= 0 or
                wall_seconds > min(numeric_limits["wallSeconds"], 86400)):
            raise PackageError("execution deferred: CWL ToolTimeLimit exceeds worker wall-time limit")
        for value_from in _values_for_key(tool, "valueFrom"):
            if re.search(r"\$\(|\$\{", value_from) and not re.fullmatch(
                    r"\$\((?:inputs\.[A-Za-z][A-Za-z0-9_]*\.path|runtime\.outdir)\)", value_from):
                raise PackageError("execution deferred: CWL expression is outside the reviewed path-only subset")
    return set(declared_inputs)


def _admit_container_requirement(requirements: dict, expected_image: Any, native_projection: bool) -> None:
    if native_projection:
        if "DockerRequirement" in requirements:
            raise PackageError("native projection cannot remove a source-owned DockerRequirement")
        return
    docker = requirements.get("DockerRequirement")
    docker_pull = docker.get("dockerPull") if isinstance(docker, dict) else None
    if (not isinstance(docker_pull, str) or "@sha256:" not in docker_pull or
            docker_pull != expected_image):
        raise PackageError("execution deferred: each CommandLineTool needs a digest-pinned Docker image")
    if set(docker) != {"dockerPull"}:
        raise PackageError("execution deferred: unsupported DockerRequirement fields")


def _values_for_key(value: Any, key: str, _seen: set[int] | None = None) -> list[str]:
    seen = set() if _seen is None else _seen
    found: list[str] = []
    if isinstance(value, dict):
        if id(value) in seen:
            return found
        seen.add(id(value))
        if key in value:
            if not isinstance(value[key], str):
                raise PackageError(f"CWL {key} values must be strings")
            found.append(value[key])
        for child in value.values():
            found.extend(_values_for_key(child, key, seen))
    elif isinstance(value, list):
        if id(value) in seen:
            return found
        seen.add(id(value))
        for child in value:
            found.extend(_values_for_key(child, key, seen))
    return found


def _admit_job(job: Any, payload: dict[str, bytes], input_ids: set[str]) -> None:
    if not isinstance(job, dict):
        raise PackageError("execution admission requires a mapping CWL job document")
    if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) for key in job):
        raise PackageError("execution deferred: job order keys must be declared simple input names")
    unknown_inputs = set(job) - input_ids
    if unknown_inputs:
        raise PackageError(f"execution deferred: job order contains undeclared inputs or directives: {sorted(unknown_inputs)}")
    files: list[dict] = []
    directive_keys = {
        "$import", "$include", "$schemas", "$graph", "$base",
        "cwl:tool", "cwltool:overrides",
        "https://w3id.org/cwl/cwl#tool", "https://w3id.org/cwl/cwl#overrides",
        "https://w3id.org/cwl/cwl#base",
    }

    def walk(value: Any, seen: set[int]) -> None:
        if isinstance(value, dict):
            if id(value) in seen:
                return
            seen.add(id(value))
            if any(key in directive_keys or _is_document_reference_key(key) for key in value):
                raise PackageError("execution deferred: job order contains a CWL document reference or override")
            if value.get("class") == "File":
                files.append(value)
            if value.get("class") == "Directory":
                raise PackageError("execution deferred: Directory inputs are outside this adapter's reviewed subset")
            for child in value.values():
                walk(child, seen)
        elif isinstance(value, list):
            if id(value) in seen:
                return
            seen.add(id(value))
            for child in value:
                walk(child, seen)

    walk(job, set())
    for item in files:
        path = item.get("path")
        if set(item) - {"class", "path", "basename", "format"} or not isinstance(path, str):
            raise PackageError("execution deferred: job File inputs must use packaged relative paths only")
        try:
            relative = safe_path(path)
        except PackageError as exc:
            raise PackageError("execution deferred: unsafe job File path") from exc
        if f"experiment/{relative}" not in payload:
            raise PackageError(f"execution deferred: job File input is not embedded: {relative}")


def _is_document_reference_key(key: Any) -> bool:
    if key in {"run", "$import", "$include", "$schemas", "$graph", "$base",
               "cwl:tool", "cwltool:overrides"}:
        return True
    if not isinstance(key, str):
        return False
    return any(key.endswith(f"#{suffix}") for suffix in
               ("run", "import", "include", "schemas", "graph", "base", "tool", "overrides"))


def _materialize(root: Path, payload: dict[str, bytes]) -> None:
    for relative, data in payload.items():
        safe_path(relative)
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _run_command(temporary: Path, work: Path, attempt_out: Path, provenance: Path,
                 runner: dict, *, cidfile_dir: Path, cidfile_prefix: str,
                 tmp_outdir_prefix: Path | None = None) -> list[str]:
    runtime_args = ["--podman"] if runner["executionMode"] == "cwltool-podman" else []
    command = [
        sys.executable, "-m", "cwltool", *runtime_args, "--disable-pull",
        "--strict-memory-limit", "--strict-cpu-limit",
        "--disable-host-provenance", "--disable-user-provenance",
        "--leave-container", "--cidfile-dir", str(cidfile_dir),
        "--cidfile-prefix", cidfile_prefix,
        "--basedir", str(work / "experiment"),
        "--tmpdir-prefix", str(temporary / "cwl-tmp-"),
        "--tmp-outdir-prefix", str(tmp_outdir_prefix or temporary / "cwl-out-"),
        "--outdir", str(attempt_out), "--provenance", str(provenance),
        str(work / "experiment/workflow.cwl"), str(work / "experiment/job.yml"),
    ]
    return command


def _terminate_process_group(process: Any) -> list[str]:
    """Attempt bounded orchestrator cleanup without losing cancellation evidence."""
    errors: list[str] = []
    for signal in (15, 9):
        try:
            os.killpg(process.pid, signal)
        except ProcessLookupError:
            pass
        except (OSError, KeyboardInterrupt) as exc:
            errors.append(f"Process group signal {signal} failed: {type(exc).__name__}: {str(exc)[:1024]}")
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            errors.append(f"Process cleanup wait after signal {signal} failed: {type(exc).__name__}: {str(exc)[:1024]}")
    # A failed wait is evidence of incomplete cleanup, not proof of termination.
    return errors


def _execute_cwl(temporary: Path, payload: dict[str, bytes], runner: dict,
                 limits: dict, tmpfs_root: str, attempt_directory: Path | None = None,
                 journal: dict | None = None, receipt: dict | None = None) -> dict:
    _require_bounded_tmpfs(temporary, Path(tmpfs_root), limits["tmpdirMiB"])
    output_root_value = os.environ.get("EXPERIMENT_RUNNER_OUTPUT_TMPFS_ROOT")
    output_root = _require_bounded_output_tmpfs(Path(output_root_value), limits["outdirMiB"]) if output_root_value else None
    if output_root is not None:
        scratch_root = Path(tmpfs_root).resolve(strict=True)
        if (output_root == scratch_root or output_root.is_relative_to(scratch_root) or
                scratch_root.is_relative_to(output_root)):
            raise PackageError("execution deferred: bounded output tmpfs must be separate from CWL scratch storage")
    if attempt_directory is None:
        attempt_directory = temporary / "attempt-evidence"
        attempt_directory.mkdir(mode=0o700)
        (attempt_directory / "engine-cids").mkdir(mode=0o700)
    if journal is None:
        journal = {
            "schema": _CONTAINER_JOURNAL_SCHEMA,
            "attemptId": "test-attempt",
            "engine": _container_engine(runner),
            "enginePath": "/usr/bin/podman" if runner["executionMode"] == "cwltool-podman" else "/usr/bin/docker",
            "workerImageRef": runner.get("workerImageBase"),
            "workerImageId": None,
            "reportedWorkerImageId": "unspecified",
            "state": "prepared",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "cidfilePrefix": "test-attempt-",
            "containers": [],
            "cancellation": None,
        }
    if receipt is None:
        receipt = {}
    if not (attempt_directory / "engine-cids").exists():
        (attempt_directory / "engine-cids").mkdir(mode=0o700)
    work = temporary / "package"
    work.mkdir()
    _materialize(work, payload)
    output_attempt = output_root / journal["attemptId"] if output_root else None
    if output_attempt is not None:
        if output_attempt.exists() or output_attempt.is_symlink():
            raise PackageError("bounded output attempt path already exists; refusing reuse")
        output_attempt.mkdir(mode=0o700)
    attempt_out = (output_attempt / "outputs") if output_attempt else temporary / "outputs"
    tmp_outdir_prefix = (output_attempt / "cwl-out-") if output_attempt else None
    provenance = temporary / "cwlprov"
    cid_directory = attempt_directory / "engine-cids"
    command = _run_command(temporary, work, attempt_out, provenance, runner,
                           cidfile_dir=cid_directory, cidfile_prefix=journal["cidfilePrefix"],
                           tmp_outdir_prefix=tmp_outdir_prefix)
    stdout_path = temporary / "stdout.log"
    stderr_path = temporary / "stderr.log"
    runner_home = temporary / "runner-home"
    runner_home.mkdir()
    process_environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(runner_home),
        "TMPDIR": str(temporary),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    python_lib = str(Path(sys.executable).resolve().parent.parent / "lib")
    inherited_ld_library_path = os.environ.get("LD_LIBRARY_PATH")
    process_environment["LD_LIBRARY_PATH"] = (
        f"{python_lib}:{inherited_ld_library_path}"
        if inherited_ld_library_path else python_lib
    )
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            process_environment[name] = value
    try:
        with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
            process = subprocess.Popen(
                command, cwd=work, env=process_environment,
                stdout=stdout_stream, stderr=stderr_stream, start_new_session=True,
            )
            _journal_update(attempt_directory, journal, receipt,
                            state="running", processStartedAt=datetime.now(timezone.utc).isoformat())
            timed_out = False
            cleanup_errors: list[str] = []
            try:
                return_code = process.wait(timeout=runner["maxWallSeconds"])
            except subprocess.TimeoutExpired:
                timed_out = True
                cleanup_errors = _terminate_process_group(process)
                return_code = process.returncode if process.returncode else 124
            except KeyboardInterrupt:
                journal["cancellation"] = {"requestedAt": datetime.now(timezone.utc).isoformat()}
                _journal_update(attempt_directory, journal, receipt, state="cancellation-requested")
                raise
    except KeyboardInterrupt as interruption:
        if "process" in locals():
            for error in _terminate_process_group(process):
                interruption.add_note(error)
        container_errors: list[str] = []
        _cleanup_container_journal(attempt_directory, journal, receipt, container_errors)
        for error in container_errors:
            interruption.add_note(error)
        if output_attempt is not None and output_attempt.exists():
            try:
                shutil.rmtree(output_attempt)
            except OSError as exc:
                interruption.add_note(f"bounded output tmpfs cleanup failed: {type(exc).__name__}: {str(exc)[:512]}")
        raise
    except Exception as exc:
        if "process" in locals():
            process_errors = _terminate_process_group(process)
        else:
            process_errors = []
        container_errors: list[str] = []
        _cleanup_container_journal(attempt_directory, journal, receipt, container_errors)
        if output_attempt is not None and output_attempt.exists():
            shutil.rmtree(output_attempt)
        cleanup_errors = process_errors + container_errors
        detail = f"; cleanup: {'; '.join(cleanup_errors)}" if cleanup_errors else ""
        raise PackageError(f"CWL runner could not complete its controller handoff: {exc}{detail}") from exc
    collection_errors: list[str] = cleanup_errors
    _cleanup_container_journal(attempt_directory, journal, receipt, collection_errors)
    receipt["containerCleanup"] = _container_cleanup_summary(journal)
    _journal_receipt_reference(attempt_directory, journal, receipt)
    # Execution collection is bounded by the reviewed worker/CWL resource
    # envelope, not by a Compiled Experiment archive-size policy.
    output_budget = limits.get("outdirMiB", limits["tmpdirMiB"]) * 1024 * 1024
    provenance_budget = limits["tmpdirMiB"] * 1024 * 1024
    output_files = _collect_execution_tree(
        attempt_out, "output", collection_errors, output_budget
    )
    provenance_files = _collect_execution_tree(
        provenance, "provenance", collection_errors, provenance_budget
    )
    if output_attempt is not None and output_attempt.exists():
        try:
            shutil.rmtree(output_attempt)
        except OSError as exc:
            collection_errors.append(f"bounded output tmpfs cleanup failed: {type(exc).__name__}: {str(exc)[:512]}")
    if not output_files and return_code == 0:
        collection_errors.append("CWL runner succeeded without producing any admitted result files")
    return {
        "returnCode": return_code,
        "timedOut": timed_out,
        "stdout": _read_execution_log(stdout_path, "stdout", collection_errors),
        "stderr": _read_execution_log(stderr_path, "stderr", collection_errors),
        "outputs": output_files,
        "provenance": provenance_files,
        "collectionErrors": collection_errors,
        "containerJournal": receipt.get("containerJournal"),
        "containerCleanup": _container_cleanup_summary(journal),
        "outputStorage": ({"enforcement": "separate-mounted-tmpfs", "capMiB": limits["outdirMiB"]}
                          if output_root else {"enforcement": "worker-tmpfs-only", "hardCap": False}),
    }


def _collect_execution_tree(root: Path, label: str, errors: list[str],
                            max_bytes: int | None = None) -> dict[str, bytes]:
    collected: dict[str, bytes] = {}
    if not root.exists():
        return collected
    try:
        _collect_tree(root, collected, max_bytes)
    except (PackageError, OSError) as exc:
        collected.clear()
        errors.append(f"{label} collection rejected: {exc}")
    return collected


def _read_execution_log(path: Path, label: str, errors: list[str]) -> bytes:
    try:
        return bounded_read(path, 4 * 1024 * 1024)
    except (PackageError, OSError) as exc:
        errors.append(f"{label} rejected: {exc}")
        return b""


def _container_engine(runner: dict) -> str:
    mode = runner.get("executionMode")
    if mode == "cwltool-podman":
        return "podman"
    if mode == "cwltool-docker":
        return "docker"
    raise PackageError("unsupported container engine in runner configuration")


def _write_container_journal(attempt_directory: Path, journal: dict) -> str:
    """Durably replace the controller-owned journal and return its digest."""
    if journal.get("schema") != _CONTAINER_JOURNAL_SCHEMA:
        raise PackageError("container journal schema mismatch")
    path = attempt_directory / "container-journal.json"
    pending = attempt_directory / "container-journal.json.tmp"
    encoded = canonical(journal)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(pending, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    pending.replace(path)
    _fsync_directory(attempt_directory)
    return sha256_bytes(encoded)


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _journal_receipt_reference(attempt_directory: Path, journal: dict, receipt: dict) -> None:
    digest = _write_container_journal(attempt_directory, journal)
    receipt["containerJournal"] = {"path": "container-journal.json", "sha256": digest}
    _persist_attempt_receipt(attempt_directory, receipt)


def _retain_journal_snapshot(attempt_directory: Path, digest: str) -> str:
    """Keep an immutable private copy for any result receipt that cites this digest."""
    current = attempt_directory / "container-journal.json"
    if current.is_symlink() or not current.is_file():
        raise PackageError("Container journal is not a regular private sidecar")
    data = bounded_read(current, 1024 * 1024)
    if sha256_bytes(data) != digest:
        raise PackageError("Container journal changed before its digest snapshot could be retained")
    history = attempt_directory / "container-journal-history"
    if not history.exists():
        history.mkdir(mode=0o700)
    if (history.is_symlink() or not history.is_dir() or
            (os.name == "posix" and history.stat().st_mode & 0o077) or
            (hasattr(os, "getuid") and history.stat().st_uid != os.getuid())):
        raise PackageError("Container journal history directory is unsafe")
    snapshot = history / f"{digest}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(snapshot, flags, 0o600)
    except FileExistsError:
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PackageError("Existing container journal history snapshot is not a regular file")
        if sha256_bytes(bounded_read(snapshot, 1024 * 1024)) != digest:
            raise PackageError("Existing container journal history digest does not match its filename")
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(history)
    return f"container-journal-history/{digest}.json"


def _container_cleanup_summary(journal: dict) -> dict:
    containers = journal.get("containers", [])
    absent = sum(1 for entry in containers
                 if isinstance(entry, dict) and entry.get("cleanup", {}).get("absentAfterCleanup") is True)
    return {
        "engine": journal.get("engine"),
        "state": journal.get("state", "unknown"),
        "containerCount": len(containers) if isinstance(containers, list) else 0,
        "absentCount": absent,
        "allAbsent": isinstance(containers, list) and absent == len(containers),
    }


def _new_container_journal(attempt_directory: Path, attempt_id: str, runner: dict) -> dict:
    cid_directory = attempt_directory / "engine-cids"
    cid_directory.mkdir(mode=0o700)
    cid_directory.chmod(0o700)
    if cid_directory.is_symlink() or not cid_directory.is_dir():
        raise PackageError("container cidfile directory is not a regular controller-owned directory")
    if hasattr(os, "getuid") and cid_directory.stat().st_uid != os.getuid():
        raise PackageError("container cidfile directory is not owned by the current runner")
    engine = _container_engine(runner)
    engine_path = shutil.which(engine, path="/usr/bin:/bin")
    if not engine_path or not Path(engine_path).is_absolute():
        raise PackageError(f"Configured container engine {engine!r} is not available on the trusted PATH")
    journal = {
        "schema": _CONTAINER_JOURNAL_SCHEMA,
        "attemptId": attempt_id,
        "engine": engine,
        "enginePath": str(Path(engine_path).resolve(strict=True)),
        "workerImageRef": runner.get("workerImageBase"),
        "reportedWorkerImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
        "workerImageId": None,
        "controller": _controller_identity(),
        "state": "prepared",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cidfilePrefix": f"{attempt_id}-",
        "containers": [],
        "cancellation": None,
    }
    image = _engine_call(attempt_directory, journal,
                         ["image", "inspect", "--format", "{{.Id}}", str(journal["workerImageRef"])])
    image_id = image.stdout.strip().lower()
    if image.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise PackageError("Pinned workflow image could not be verified by the configured engine")
    journal["workerImageId"] = image_id
    return journal


def _controller_identity() -> dict:
    identity = {"pid": os.getpid(), "bootId": None, "processStartTicks": None}
    try:
        identity["bootId"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{os.getpid()}/stat").read_text()
        identity["processStartTicks"] = stat[stat.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        pass
    return identity


def _controller_is_alive(identity: Any) -> bool:
    if not isinstance(identity, dict) or type(identity.get("pid")) is not int or identity["pid"] <= 0:
        raise PackageError("Attempt journal has no trusted controller process identity")
    current = _controller_identity()
    if (not isinstance(identity.get("bootId"), str) or
            not re.fullmatch(r"[0-9a-fA-F-]{36}", identity["bootId"]) or
            not isinstance(identity.get("processStartTicks"), str) or
            not identity["processStartTicks"].isdigit() or
            not isinstance(current.get("bootId"), str) or
            not isinstance(current.get("processStartTicks"), str)):
        raise PackageError("Attempt journal lacks a verifiable Linux controller identity")
    if identity.get("bootId") != current.get("bootId"):
        return False
    pid = identity["pid"]
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PackageError(f"Cannot verify prior controller process state: {exc}") from exc
    try:
        ticks = stat[stat.rfind(")") + 2:].split()[19]
    except IndexError as exc:
        raise PackageError("Prior controller process identity is malformed") from exc
    if ticks != identity.get("processStartTicks"):
        return False
    raise PackageError("The original attempt controller is still alive; refusing concurrent cleanup")


def _journal_update(attempt_directory: Path, journal: dict, receipt: dict, **changes) -> None:
    journal.update(changes)
    journal["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _journal_receipt_reference(attempt_directory, journal, receipt)


def _discover_container_ids(attempt_directory: Path, journal: dict,
                            receipt: dict, errors: list[str]) -> None:
    cid_directory = attempt_directory / "engine-cids"
    if cid_directory.is_symlink() or not cid_directory.is_dir():
        errors.append("Container cidfile directory is missing or unsafe")
        journal["state"] = "cleanup-failed"
        _journal_update(attempt_directory, journal, receipt)
        return
    files = sorted(cid_directory.glob(f"{journal['cidfilePrefix']}*.cid"))
    if len(files) > _MAX_ATTEMPT_CONTAINERS:
        errors.append("Container journal exceeds its bounded CID count")
        journal["state"] = "cleanup-failed"
        _journal_update(attempt_directory, journal, receipt)
        return
    known = {entry.get("id") for entry in journal["containers"] if isinstance(entry, dict)}
    for path in files:
        try:
            if path.is_symlink() or not path.is_file():
                raise PackageError("CID file is not a regular file")
            if path.stat().st_size > 128:
                raise PackageError("CID file exceeds its limit")
            container_id = path.read_text(encoding="ascii").strip()
            if not _CONTAINER_ID.fullmatch(container_id):
                raise PackageError("CID file does not contain a full lowercase container ID")
        except (OSError, UnicodeError, PackageError) as exc:
            errors.append(f"Container CID evidence rejected ({path.name}): {exc}")
            continue
        if container_id in known:
            continue
        entry = {
            "engine": journal["engine"],
            "id": container_id,
            "imageId": journal["workerImageId"],
            "imageRef": journal["workerImageRef"],
            "cidfile": f"engine-cids/{path.name}",
            "discoveredAt": datetime.now(timezone.utc).isoformat(),
            "inspectState": "unknown",
            "cleanup": {"status": "pending", "absentAfterCleanup": False},
        }
        journal["containers"].append(entry)
        known.add(container_id)
        # Record every newly discovered engine identity before contacting it.
        _journal_update(attempt_directory, journal, receipt)


def _container_engine_environment(attempt_directory: Path) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(attempt_directory / "runner-home"),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1"}
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "LD_LIBRARY_PATH"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def _engine_call(attempt_directory: Path, journal: dict, arguments: list[str]) -> subprocess.CompletedProcess:
    executable = journal.get("enginePath")
    expected = shutil.which(journal.get("engine", ""), path="/usr/bin:/bin")
    if (not isinstance(executable, str) or not Path(executable).is_absolute() or
            not expected or Path(executable).resolve(strict=True) != Path(expected).resolve(strict=True)):
        raise PackageError("Container cleanup engine path differs from the trusted engine binary")
    try:
        return subprocess.run([executable, *arguments], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=8, check=False,
                              env=_container_engine_environment(attempt_directory))
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackageError(f"Container engine request failed: {type(exc).__name__}: {str(exc)[:512]}") from exc


def _container_present(attempt_directory: Path, journal: dict, container_id: str) -> tuple[bool, str | None]:
    inspected = _engine_call(attempt_directory, journal,
                             ["inspect", "--format", "{{.Id}} {{.State.Status}} {{.Image}} {{.Config.Image}}", container_id])
    if inspected.returncode == 0:
        fields = inspected.stdout.strip().split()
        if len(fields) != 4 or fields[0].lower() != container_id:
            raise PackageError("Container inspect returned a different or malformed ID")
        if fields[2].lower() != journal["workerImageId"] or fields[3] != journal["workerImageRef"]:
            raise PackageError("Journaled container image differs from the pinned and receipt-bound worker image")
        return True, fields[1].lower()
    listing = _engine_call(attempt_directory, journal, ["ps", "--all", "--no-trunc", "--format", "{{.ID}}"])
    if listing.returncode != 0:
        raise PackageError("Container engine could not prove whether the exact journaled ID remains")
    ids = {line.strip().lower() for line in listing.stdout.splitlines() if line.strip()}
    if container_id in ids:
        raise PackageError("Container engine lists the ID but inspect cannot establish its state")
    return False, None


def _cleanup_container_journal(attempt_directory: Path, journal: dict, receipt: dict,
                               errors: list[str]) -> None:
    _discover_container_ids(attempt_directory, journal, receipt, errors)
    for entry in journal["containers"]:
        container_id = entry["id"]
        cleanup = entry["cleanup"]
        if cleanup.get("absentAfterCleanup") is True:
            continue
        try:
            present, state = _container_present(attempt_directory, journal, container_id)
            entry["inspectState"] = state or "absent"
            if present and state == "running":
                cleanup.update(status="stopping", requestedAt=datetime.now(timezone.utc).isoformat())
                _journal_update(attempt_directory, journal, receipt)
                stopped = _engine_call(attempt_directory, journal,
                                       ["stop", "--time", "5", container_id])
                present, state = _container_present(attempt_directory, journal, container_id)
                if present and state == "running":
                    raise PackageError(f"Container stop did not stop exact ID (exit {stopped.returncode})")
            if present:
                if state not in {"created", "exited", "stopped"}:
                    raise PackageError(f"Container state {state!r} is busy or unknown; refusing removal")
                cleanup.update(status="removing", removeRequestedAt=datetime.now(timezone.utc).isoformat())
                _journal_update(attempt_directory, journal, receipt)
                removed = _engine_call(attempt_directory, journal, ["rm", container_id])
                present, state = _container_present(attempt_directory, journal, container_id)
                if present:
                    raise PackageError(f"Container removal did not remove exact ID (exit {removed.returncode}, state {state})")
            cleanup.update(status="absent", absentAfterCleanup=True,
                           completedAt=datetime.now(timezone.utc).isoformat())
            entry["inspectState"] = "absent"
            _journal_update(attempt_directory, journal, receipt)
        except PackageError as exc:
            cleanup.update(status="cleanup-failed", error=f"{type(exc).__name__}: {str(exc)[:512]}",
                           absentAfterCleanup=False)
            errors.append(f"Container cleanup failed for exact ID {container_id}: {exc}")
            _journal_update(attempt_directory, journal, receipt)
    journal["state"] = "cleanup-failed" if errors else "clean"
    _journal_update(attempt_directory, journal, receipt)


def recover_attempt(attempt_directory: Path) -> dict:
    """Reconcile one retained attempt's exact CWL containers without running science."""
    if attempt_directory.is_symlink() or not attempt_directory.is_dir():
        raise PackageError("Recovery requires an existing regular attempt directory")
    if hasattr(os, "getuid") and attempt_directory.stat().st_uid != os.getuid():
        raise PackageError("Recovery attempt directory must be owned by the current runner user")
    if attempt_directory.stat().st_mode & 0o077:
        raise PackageError("Recovery attempt directory must not be accessible to group or other users")
    receipt_path = attempt_directory / "runner-receipt.json"
    journal_path = attempt_directory / "container-journal.json"
    if receipt_path.is_symlink() or journal_path.is_symlink():
        raise PackageError("Recovery receipt and journal must be regular owned files")
    receipt_bytes = bounded_read(receipt_path, 1024 * 1024)
    journal_bytes = bounded_read(journal_path, 1024 * 1024)
    receipt = json_value(receipt_bytes)
    journal = json_value(journal_bytes)
    reference = receipt.get("containerJournal") if isinstance(receipt, dict) else None
    if (not isinstance(reference, dict) or reference.get("path") != "container-journal.json" or
            reference.get("sha256") != sha256_bytes(journal_bytes)):
        raise PackageError("Container journal is not bound by the retained runner receipt")
    if not isinstance(journal, dict) or journal.get("schema") != _CONTAINER_JOURNAL_SCHEMA:
        raise PackageError("Unsupported container journal schema")
    attempt_id = receipt.get("attemptId")
    if (not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{12}", attempt_id) or
            journal.get("attemptId") != attempt_id or journal.get("cidfilePrefix") != f"{attempt_id}-"):
        raise PackageError("Container journal belongs to a different attempt")
    if receipt.get("status") not in {"started", "failed"}:
        raise PackageError("Only incomplete or failed attempts can be recovered")
    plan_digest = receipt.get("planPackageSha256")
    runner = receipt.get("runner")
    worker = receipt.get("workerContext")
    if (not isinstance(plan_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_digest) or
            not isinstance(runner, dict) or runner.get("name") != "cwltool" or
            not isinstance(worker, dict) or not isinstance(worker.get("reportedImageId"), str) or
            worker.get("reportedImageId") in {"", "unknown"} or
            worker.get("reportedImageId") != journal.get("reportedWorkerImageId")):
        raise PackageError("Recovery journal does not match the retained plan and worker identity")
    expected_engine = "podman" if receipt.get("executionMode", "").endswith("Podman runtime") else "docker"
    if (journal.get("engine") != expected_engine or
            journal.get("workerImageRef") != runner.get("workerImageBase") or
            not isinstance(journal.get("workerImageId"), str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", journal["workerImageId"])):
        raise PackageError("Recovery engine or pinned image identity differs from the retained receipt")
    try:
        created = datetime.fromisoformat(journal["createdAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("Recovery journal has no valid creation time") from exc
    age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if age < -300 or age > _MAX_RECOVERY_AGE_SECONDS:
        raise PackageError("Recovery journal is stale or has a future timestamp")
    _controller_is_alive(journal.get("controller"))
    containers = journal.get("containers")
    if not isinstance(containers, list) or len(containers) > _MAX_ATTEMPT_CONTAINERS:
        raise PackageError("Recovery journal has an invalid bounded container list")
    for entry in containers:
        if (not isinstance(entry, dict) or entry.get("engine") != journal["engine"] or
                not isinstance(entry.get("id"), str) or not _CONTAINER_ID.fullmatch(entry["id"]) or
                entry.get("imageId") != journal["workerImageId"] or
                entry.get("imageRef") != journal["workerImageRef"] or
                not isinstance(entry.get("cidfile"), str) or
                not re.fullmatch(r"engine-cids/[A-Za-z0-9_.-]+\.cid", entry["cidfile"]) or
                not Path(entry["cidfile"]).name.startswith(f"{attempt_id}--")):
            raise PackageError("Recovery journal contains an unknown, malformed, or cross-attempt container ID")
    for protected_name in ("engine-cids", "runner-home"):
        protected = attempt_directory / protected_name
        if (protected.is_symlink() or not protected.is_dir() or
                protected.stat().st_mode & 0o077 or
                (hasattr(os, "getuid") and protected.stat().st_uid != os.getuid())):
            raise PackageError(f"Protected {protected_name} directory is absent or unsafe; refusing recovery")
    image = _engine_call(attempt_directory, journal,
                         ["image", "inspect", "--format", "{{.Id}}", journal["workerImageRef"]])
    if image.returncode != 0 or image.stdout.strip().lower() != journal["workerImageId"]:
        raise PackageError("Recovery engine no longer resolves the exact retained workflow image ID")
    errors: list[str] = []
    # A CID file may have been written by the engine immediately before a hard
    # controller loss, before the controller appended it to the journal.
    original_journal_sha = reference["sha256"]
    _retain_journal_snapshot(attempt_directory, original_journal_sha)
    _cleanup_container_journal(attempt_directory, journal, receipt, errors)
    receipt["status"] = "failed"
    receipt["endedAt"] = receipt.get("endedAt") or datetime.now(timezone.utc).isoformat()
    prior_errors = receipt.get("collectionErrors", [])
    if not isinstance(prior_errors, list):
        prior_errors = []
    receipt["collectionErrors"] = prior_errors + [
        "Attempt recovery ran without re-executing science; completion is not established"
    ] + errors
    receipt["containerCleanup"] = {
        "state": journal["state"],
        "containerCount": len(journal["containers"]),
        "absentCount": sum(1 for entry in journal["containers"]
                            if entry["cleanup"].get("absentAfterCleanup") is True),
        "allAbsent": all(entry["cleanup"].get("absentAfterCleanup") is True
                          for entry in journal["containers"]),
    }
    _journal_receipt_reference(attempt_directory, journal, receipt)
    return receipt


def _attempt_evidence(payload: dict[str, bytes], package_bytes: bytes, expected_sha256: str,
                      closure: dict, runner: dict, execution: dict,
                      timing: dict) -> tuple[dict[str, bytes], str, list[str]]:
    created = timing["created"]
    ended = timing["ended"]
    elapsed = timing["elapsed"]
    run_id = hashlib.sha256((expected_sha256 + created.isoformat()).encode()).hexdigest()[:12]
    evidence_root = f"evidence/attempts/{run_id}"
    augmented = dict(payload)
    plan_archive = f"{evidence_root}/plan-package.zip"
    output_files = execution["outputs"]
    provenance_files = execution["provenance"]
    augmented[plan_archive] = package_bytes
    augmented.update({f"{evidence_root}/{name}": value for name, value in output_files.items()})
    augmented.update({
        f"{evidence_root}/workflow-run/{name}": value
        for name, value in provenance_files.items()
    })
    augmented[f"{evidence_root}/stdout.log"] = execution["stdout"]
    augmented[f"{evidence_root}/stderr.log"] = execution["stderr"]
    output_index = [
        {"@id": f"{evidence_root}/{name}", "sha256": sha256_bytes(value), "contentSize": len(value)}
        for name, value in sorted(output_files.items())
    ]
    receipt = {
        "schemaVersion": 1,
        "attemptId": run_id,
        "planPackageSha256": expected_sha256,
        "planPackageArchive": {
            "@id": plan_archive, "sha256": expected_sha256, "contentSize": len(package_bytes),
        },
        "workflowSha256": sha256_bytes(payload["experiment/workflow.cwl"]),
        "jobSha256": sha256_bytes(payload["experiment/job.yml"]),
        "runner": {
            "name": "cwltool", "version": runner["version"],
            "python": platform.python_version(), "workerImageBase": runner.get("workerImageBase"),
        },
        "executionMode": (
            "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime"
            if runner["executionMode"] == "cwltool-podman"
            else "cwltool orchestrator on worker; CWL CommandLineTool uses Docker runtime"
        ),
        "networkPolicy": "CWL NetworkAccess=false is applied to tool containers; cwltool host expressions are not independently network-isolated",
        "workerContext": {
            "reportedSandbox": os.environ.get("EXPERIMENT_RUNNER_SANDBOX", "unspecified by caller"),
            "reportedImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
            "attestation": "caller-reported; not independently verified by the adapter",
        },
        "status": (
            "timed-out" if execution["timedOut"]
            else "failed" if execution["returnCode"] != 0 or execution["collectionErrors"]
            else "succeeded"
        ),
        "collectionErrors": execution["collectionErrors"],
        "containerJournal": ({"availability": "private-store",
                              "storePath": execution.get("containerJournalStorePath"),
                              "sha256": execution["containerJournal"]["sha256"]}
                             if isinstance(execution.get("containerJournal"), dict) and
                             isinstance(execution["containerJournal"].get("sha256"), str)
                             else {"availability": "unavailable"}),
        "containerCleanup": execution.get("containerCleanup", {"state": "unknown", "allAbsent": False}),
        "outputStorage": execution.get("outputStorage", {"enforcement": "worker-tmpfs-only", "hardCap": False}),
        "exitCode": execution["returnCode"],
        "timedOut": execution["timedOut"],
        "startedAt": created.isoformat(),
        "endedAt": ended.isoformat(),
        "wallSeconds": elapsed,
        "resourceMeasurements": {"peakProcessMemory": "unknown; not measured by this adapter"},
        "outputs": output_index,
        "workflowRunCrateFiles": sorted(provenance_files),
        "availableClosure": closure["classifications"],
    }
    receipt_path = f"{evidence_root}/runner-receipt.json"
    augmented[receipt_path] = canonical(receipt)
    attempt_members = [f"{evidence_root}/{name}" for name in output_files]
    attempt_members += [f"{evidence_root}/workflow-run/{name}" for name in provenance_files]
    attempt_members += [
        plan_archive, f"{evidence_root}/stdout.log", f"{evidence_root}/stderr.log", receipt_path,
    ]
    return augmented, run_id, attempt_members


def _augment_result_crate(augmented: dict[str, bytes], expected_sha256: str, run_id: str,
                          attempt_members: list[str], created: datetime, ended: datetime,
                          execution: dict) -> tuple[str, str]:
    crate = json_value(augmented["ro-crate-metadata.json"])
    graph = crate["@graph"]
    root = next(node for node in graph if node.get("@id") == "./")
    context = crate.get("@context", [])
    contexts = context if isinstance(context, list) else [context]
    run_context = "https://w3id.org/ro/terms/workflow-run/context"
    if run_context not in contexts:
        contexts.append(run_context)
    crate["@context"] = contexts
    experiment_id = root.get("identifier")
    title = root.get("name")
    if (not isinstance(experiment_id, str) or
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", experiment_id) or
            not isinstance(title, str) or not title):
        raise PackageError("RO-Crate root must declare a portable identifier and name before execution")
    root["conformsTo"] = [
        {"@id": "https://w3id.org/ro/crate/1.3"},
        {"@id": "https://w3id.org/ro/wfrun/process/0.6"},
    ]
    root["datePublished"] = created.date().isoformat()
    root["https://www.w3.org/ns/prov#wasDerivedFrom"] = {"@id": f"urn:sha256:{expected_sha256}"}
    parts = _refs(root.get("hasPart"))
    known_parts = {item.get("@id") for item in parts}
    for name in attempt_members:
        if name not in known_parts:
            parts.append({"@id": name})
            graph.append({"@id": name, "@type": "File", "encodingFormat": _media_type(name)})
    root["hasPart"] = parts
    if not any(node.get("@id") == "https://w3id.org/ro/wfrun/process/0.6" for node in graph):
        graph.append({
            "@id": "https://w3id.org/ro/wfrun/process/0.6",
            "@type": ["CreativeWork", "Profile"], "version": "0.6",
        })
    license_id = root.get("license", {}).get("@id") if isinstance(root.get("license"), dict) else None
    if isinstance(license_id, str) and not any(node.get("@id") == license_id for node in graph):
        graph.append({"@id": license_id, "@type": "CreativeWork", "name": "Apache License 2.0"})
    workflow_node = next((node for node in graph if node.get("@id") == "experiment/workflow.cwl"), None)
    if workflow_node is None:
        graph.append({
            "@id": "experiment/workflow.cwl", "@type": ["File", "SoftwareSourceCode"],
            "programmingLanguage": "Common Workflow Language", "name": "CWL execution workflow",
        })
        root["hasPart"].append({"@id": "experiment/workflow.cwl"})
    graph.append({
        "@id": f"#attempt-{run_id}",
        "@type": "CreateAction",
        "name": f"CWL attempt {run_id}",
        "actionStatus": (
            "https://schema.org/CompletedActionStatus"
            if execution["returnCode"] == 0 and not execution["timedOut"] and not execution["collectionErrors"]
            else "https://schema.org/FailedActionStatus"
        ),
        "instrument": {"@id": "experiment/workflow.cwl"},
        "object": {"@id": "experiment/protocol.md"},
        "result": [{"@id": name} for name in attempt_members],
        "startTime": created.isoformat(),
        "endTime": ended.isoformat(),
    })
    augmented["ro-crate-metadata.json"] = canonical(crate)
    return experiment_id, title


def _compile_attempt_result(temporary: Path, output: Path, source_directory: Path,
                            augmented: dict[str, bytes], manifest: dict, expected_sha256: str,
                            run_id: str, experiment_id: str, title: str, execution: dict) -> dict:
    stage = temporary / "result-source"
    _materialize(stage, augmented)
    recipe_path = stage / "experiment.json"
    recipe = {
        "buildRecipeVersion": 1,
        "id": f"{experiment_id[:60]}-attempt-{run_id}",
        "title": f"{title} — attempt {run_id}",
        "profile": "compiled-experiment-lifecycle-v1",
        "manifest": {
            "schemaVersion": 2,
            "packageType": "compiled-experiment",
            "profile": "compiled-experiment-lifecycle-v1",
            "source": manifest["source"],
            "standards": manifest["standards"],
            "evidence": {"planPackageSha256": expected_sha256, "attemptId": run_id},
        },
        "members": [
            {"source": name, "path": name, "sha256": sha256_bytes(content), "size": len(content)}
            for name, content in sorted(augmented.items())
        ],
    }
    recipe_path.write_bytes(canonical(recipe))
    # Prepare the ZIP before exposing either publication destination. Claim
    # destinations exclusively and remove only paths owned by this attempt if
    # publishing the retained source or package fails.
    # The result ZIP can be large and the runner's private temp tree is the
    # worker-bounded tmpfs. Stage the package beside its publication target so
    # ZIP assembly and the immutable copy do not consume that scratch budget.
    with tempfile.TemporaryDirectory(prefix=".compiled-experiment-result-", dir=output.parent) as package_temp:
        prepared_package = Path(package_temp) / "result.zip"
        result = compile_package(recipe_path, prepared_package)
        source_identity = None
        output_identity = None
        try:
            source_directory.mkdir()
            source_identity = source_directory.stat()
            shutil.copytree(stage, source_directory, dirs_exist_ok=True)
            with output.open("xb") as stream:
                output_identity = os.fstat(stream.fileno())
                with prepared_package.open("rb") as prepared:
                    shutil.copyfileobj(prepared, stream, length=1024 * 1024)
        except BaseException:
            if output_identity is not None and output.exists():
                current = output.stat()
                if (current.st_dev, current.st_ino) == (output_identity.st_dev, output_identity.st_ino):
                    output.unlink()
            if source_identity is not None and source_directory.exists():
                current = source_directory.stat()
                if (current.st_dev, current.st_ino) == (source_identity.st_dev, source_identity.st_ino):
                    shutil.rmtree(source_directory)
            raise
    result["executionStatus"] = (
        "failed" if execution["returnCode"] or execution["timedOut"] or execution["collectionErrors"]
        else "succeeded"
    )
    if result["executionStatus"] == "failed":
        result["exitCode"] = execution["returnCode"]
        result["timedOut"] = execution["timedOut"]
    return result


def _persist_attempt_receipt(attempt_directory: Path, value: dict) -> None:
    pending = attempt_directory / "runner-receipt.json.tmp"
    with pending.open("wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(attempt_directory / "runner-receipt.json")
    _fsync_directory(attempt_directory)


def _replay_operator_signal(handler, pending, original, failure):
    number, frame = pending
    try:
        handler(number, frame)
    except KeyboardInterrupt as interruption:
        if not isinstance(original, KeyboardInterrupt):
            if failure is not None:
                interruption.add_note(f"Attempt evidence write failed: {type(failure).__name__}: {str(failure)[:1024]}")
            raise
        original.add_note(f"Deferred signal during evidence retention: {str(interruption)[:1024]}")
    # Raise outside the deferred handler's except block so an earlier
    # interruption stays its exception context/cause and retains its notes.
    if isinstance(original, KeyboardInterrupt):
        if failure is not None:
            original.add_note(f"Attempt evidence write failed: {type(failure).__name__}: {str(failure)[:1024]}")
        raise original


@contextmanager
def _defer_operator_signals(original: BaseException | None = None):
    """Deliver the first handled signal after controller evidence writes finish."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "pthread_sigmask"):
        yield
        return
    handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)
                if callable(signal.getsignal(number))}
    if not handlers:
        yield
        return
    pending = None
    failure = None

    def defer(number, frame):
        nonlocal pending
        if pending is None:
            pending = (number, frame)

    try:
        installation_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
        try:
            for number in handlers:
                signal.signal(number, defer)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, installation_mask)
        yield
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        raise
    finally:
        # The evidence phase records delivery order. Block only the short
        # restore/replay transition so a later signal cannot replace its first.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
        try:
            for number, handler in handlers.items():
                signal.signal(number, handler)
            if pending is not None:
                _replay_operator_signal(handlers[pending[0]], pending, original, failure)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _retain_failed_attempt(attempt_directory: Path, temporary: Path, receipt: dict,
                           started: float, exc: BaseException) -> None:
    receipt.update(status="failed", endedAt=datetime.now(timezone.utc).isoformat(),
                   wallSeconds=time.monotonic() - started)
    receipt["collectionErrors"] = list(receipt["collectionErrors"]) + [
        f"Attempt failed: {type(exc).__name__}: {str(exc)[:4096]}"
    ]
    if isinstance(exc, KeyboardInterrupt):
        receipt["collectionErrors"].append("Operator cancellation requested; scientific completion not established")
        receipt["collectionErrors"].extend(note[:4096] for note in getattr(exc, "__notes__", []))
    # Read only bounded controller logs if execution/collection raised.
    for label in ("stdout", "stderr"):
        path = temporary / f"{label}.log"
        if path.exists():
            data = _read_execution_log(path, label, receipt["collectionErrors"])
            (attempt_directory / f"{label}.log").write_bytes(data)
    _persist_attempt_receipt(attempt_directory, receipt)


def run_package(package: Path, output: Path, *, expected_sha256: str,
                allow_workflow_execution: bool = False) -> dict:
    """Run a digest-pinned lifecycle package and create a new immutable result package."""
    if not allow_workflow_execution:
        raise PackageError("workflow execution requires explicit opt-in after reviewing the package")
    source_directory = output.with_name(output.stem + ".source")
    if output.exists() or source_directory.exists():
        raise PackageError("run output package or source directory already exists; attempts are never overwritten")
    package_bytes = bounded_read(package)
    manifest, payload = _read_package(package_bytes, expected_sha256)
    closure, runner, limits, tmpfs_root = _admit(payload)
    attempt_directory = output.with_name(output.stem + ".attempt")
    if attempt_directory.exists():
        raise PackageError("run attempt directory already exists; attempts are never overwritten")
    created = datetime.now(timezone.utc)
    started = time.monotonic()
    execution = {"returnCode": None, "timedOut": False, "stdout": b"", "stderr": b"",
                 "outputs": {}, "provenance": {}, "collectionErrors": []}
    timing = {"created": created, "ended": created, "elapsed": 0}
    initial, run_id, _ = _attempt_evidence(
        payload, package_bytes, expected_sha256, closure, runner, execution, timing,
    )
    receipt_key = f"evidence/attempts/{run_id}/runner-receipt.json"
    receipt = json_value(initial[receipt_key])
    receipt.update(status="started", endedAt=None, wallSeconds=None)
    attempt_directory.mkdir(parents=True)
    attempt_directory.chmod(0o700)
    (attempt_directory / "runner-home").mkdir(mode=0o700)
    (attempt_directory / "runner-home").chmod(0o700)
    _persist_attempt_receipt(attempt_directory, receipt)
    try:
        journal = _new_container_journal(attempt_directory, run_id, runner)
    except (PackageError, OSError, subprocess.SubprocessError) as exc:
        receipt.update(status="failed", endedAt=datetime.now(timezone.utc).isoformat(),
                       collectionErrors=[f"Attempt preflight failed before workflow launch: {type(exc).__name__}: {str(exc)[:1024]}"])
        _persist_attempt_receipt(attempt_directory, receipt)
        raise
    _journal_receipt_reference(attempt_directory, journal, receipt)
    with tempfile.TemporaryDirectory(prefix="compiled-experiment-run-") as temporary_value:
        temporary = Path(temporary_value)
        try:
            execution = _execute_cwl(temporary, payload, runner, limits, tmpfs_root,
                                     attempt_directory, journal, receipt)
            execution["containerJournalStorePath"] = _retain_journal_snapshot(
                attempt_directory, execution["containerJournal"]["sha256"]
            )
            ended = datetime.now(timezone.utc)
            timing = {"created": created, "ended": ended, "elapsed": time.monotonic() - started}
            augmented, run_id, attempt_members = _attempt_evidence(
                payload, package_bytes, expected_sha256, closure, runner, execution, timing,
            )
            archived_receipt = json_value(augmented[receipt_key])
            receipt.update({key: value for key, value in archived_receipt.items()
                            if key not in {"containerJournal", "containerCleanup"}})
            receipt["containerCleanup"] = execution.get(
                "containerCleanup", {"state": "unknown", "allAbsent": False}
            )
            for label in ("stdout", "stderr"):
                (attempt_directory / f"{label}.log").write_bytes(execution[label])
            _persist_attempt_receipt(attempt_directory, dict(receipt, status="started", endedAt=None, wallSeconds=None))
            experiment_id, title = _augment_result_crate(
                augmented, expected_sha256, run_id, attempt_members, created, ended, execution,
            )
            result = _compile_attempt_result(
                temporary, output, source_directory, augmented, manifest, expected_sha256,
                run_id, experiment_id, title, execution,
            )
        except (Exception, KeyboardInterrupt) as exc:
            with _defer_operator_signals(exc):
                _retain_failed_attempt(attempt_directory, temporary, receipt, started, exc)
            raise
        # Publication succeeded. A controller receipt I/O failure must not
        # reclassify the valid published execution as a failed attempt.
        with _defer_operator_signals():
            _persist_attempt_receipt(attempt_directory, receipt)
        return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collect_tree(root: Path, collected: dict[str, bytes], max_bytes: int | None) -> None:
    total = sum(len(value) for value in collected.values())
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PackageError("CWL output contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        safe_path(relative)
        remaining = None if max_bytes is None else max_bytes - total
        if remaining is not None and (remaining < 0 or path.stat().st_size > remaining):
            raise PackageError("CWL output exceeds the declared worker resource budget")
        content = bounded_read(path, remaining)
        total += len(content)
        if max_bytes is not None and total > max_bytes:
            raise PackageError("CWL output exceeds the declared worker resource budget")
        collected[relative] = content


def _require_bounded_tmpfs(temporary: Path, root: Path, limit_mib: int) -> None:
    resolved_root = root.resolve(strict=True)
    resolved_temporary = temporary.resolve(strict=True)
    if not resolved_temporary.is_relative_to(resolved_root):
        raise PackageError("execution deferred: runner temporary directory is outside the declared tmpfs")
    capacity = shutil.disk_usage(resolved_root).total
    if capacity < limit_mib * 1024 * 1024:
        raise PackageError("execution deferred: temporary filesystem capacity is below the declared worker limit")


def _require_bounded_output_tmpfs(root: Path, limit_mib: int) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise PackageError("execution deferred: output root must be an existing regular directory")
    resolved = root.resolve(strict=True)
    current = root.absolute()
    while current != current.parent:
        if current.is_symlink():
            raise PackageError("execution deferred: output root path contains a symlink")
        current = current.parent
    try:
        entries = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PackageError("execution deferred: cannot verify the output tmpfs mount") from exc
    mount_type = None
    for line in entries:
        try:
            before, after = line.split(" - ", 1)
            fields = before.split()
            mountpoint = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
            if Path(mountpoint) == resolved:
                mount_type = after.split()[0]
                break
        except (IndexError, ValueError):
            continue
    if mount_type != "tmpfs":
        raise PackageError("execution deferred: output root is not a dedicated tmpfs mountpoint")
    capacity = shutil.disk_usage(resolved).total
    cap_bytes = int(limit_mib * 1024 * 1024)
    if capacity != cap_bytes:
        raise PackageError("execution deferred: output tmpfs size must exactly match the declared worker output cap")
    return resolved


def _refs(value: Any) -> list[dict]:
    values = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [item for item in values if isinstance(item, dict)]


def _media_type(name: str) -> str:
    if name.endswith(".zip"):
        return "application/zip"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".log"):
        return "text/plain"
    return "application/octet-stream"
