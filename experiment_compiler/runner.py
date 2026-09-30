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
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import zipfile

from .core import (MANIFEST, MAX_FILE, MAX_MEMBERS, MAX_TOTAL, PackageError, bounded_read, canonical, compile_package,
                   json_value, safe_path, verify_bytes)


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


def _admit_workflow(document: Any, limits: Any, expected_image: Any) -> set[str]:
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
        docker = requirements.get("DockerRequirement")
        docker_pull = docker.get("dockerPull") if isinstance(docker, dict) else None
        if (not isinstance(docker_pull, str) or "@sha256:" not in docker_pull or
                docker_pull != expected_image):
            raise PackageError("execution deferred: each CommandLineTool needs a digest-pinned Docker image")
        if set(docker) != {"dockerPull"}:
            raise PackageError("execution deferred: unsupported DockerRequirement fields")
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
                 runner: dict) -> list[str]:
    runtime_args = ["--podman"] if runner["executionMode"] == "cwltool-podman" else []
    return [
        sys.executable, "-m", "cwltool", *runtime_args, "--disable-pull",
        "--strict-memory-limit", "--strict-cpu-limit",
        "--disable-host-provenance", "--disable-user-provenance",
        "--basedir", str(work / "experiment"),
        "--tmpdir-prefix", str(temporary / "cwl-tmp-"),
        "--tmp-outdir-prefix", str(temporary / "cwl-out-"),
        "--outdir", str(attempt_out), "--provenance", str(provenance),
        str(work / "experiment/workflow.cwl"), str(work / "experiment/job.yml"),
    ]


def _execute_cwl(temporary: Path, payload: dict[str, bytes], runner: dict,
                 limits: dict, tmpfs_root: str, plan_size: int = 0) -> dict:
    _require_bounded_tmpfs(temporary, Path(tmpfs_root), limits["tmpdirMiB"])
    work = temporary / "package"
    work.mkdir()
    _materialize(work, payload)
    attempt_out = temporary / "outputs"
    provenance = temporary / "cwlprov"
    command = _run_command(temporary, work, attempt_out, provenance, runner)
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
    inherited_xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if inherited_xdg_data_home:
        process_environment["XDG_DATA_HOME"] = inherited_xdg_data_home
    try:
        with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
            process = subprocess.Popen(
                command, cwd=work, env=process_environment,
                stdout=stdout_stream, stderr=stderr_stream, start_new_session=True,
            )
            timed_out = False
            try:
                return_code = process.wait(timeout=runner["maxWallSeconds"])
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, 15)
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, 9)
                    except ProcessLookupError:
                        pass
                    process.wait()
                return_code = process.returncode if process.returncode else 124
    except subprocess.SubprocessError as exc:
        raise PackageError(f"CWL runner could not start: {exc}") from exc
    collection_errors: list[str] = []
    # Recipe source members have a 48 MiB aggregate budget. Reserve bounded
    # logs (8 MiB) and controller metadata (1 MiB), plus the archived plan.
    budget = max(0, MAX_TOTAL - MAX_FILE - sum(map(len, payload.values())) -
                 plan_size - 9 * 1024 * 1024)
    output_files = _collect_execution_tree(attempt_out, "output", collection_errors, budget)
    provenance_files = _collect_execution_tree(
        provenance, "provenance", collection_errors,
        budget - sum(map(len, output_files.values())),
    )
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
    }


def _collect_execution_tree(root: Path, label: str, errors: list[str],
                            max_bytes: int = MAX_TOTAL - MAX_FILE) -> dict[str, bytes]:
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
    prepared_package = temporary / "result.zip"
    result = compile_package(recipe_path, prepared_package)
    source_identity = None
    output_identity = None
    try:
        source_directory.mkdir()
        source_identity = source_directory.stat()
        shutil.copytree(stage, source_directory, dirs_exist_ok=True)
        with output.open("xb") as stream:
            output_identity = os.fstat(stream.fileno())
            stream.write(bounded_read(prepared_package, MAX_TOTAL))
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


def run_package(package: Path, output: Path, *, expected_sha256: str,
                allow_workflow_execution: bool = False) -> dict:
    """Run a digest-pinned lifecycle package and create a new immutable result package."""
    if not allow_workflow_execution:
        raise PackageError("workflow execution requires explicit opt-in after reviewing the package")
    source_directory = output.with_name(output.stem + ".source")
    if output.exists() or source_directory.exists():
        raise PackageError("run output package or source directory already exists; attempts are never overwritten")
    package_bytes = bounded_read(package, MAX_TOTAL)
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
    receipt_path = attempt_directory / "runner-receipt.json"

    def persist_receipt(value: dict) -> None:
        pending = attempt_directory / "runner-receipt.json.tmp"
        with pending.open("wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(receipt_path)
        directory_fd = os.open(attempt_directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    persist_receipt(receipt)
    with tempfile.TemporaryDirectory(prefix="compiled-experiment-run-") as temporary_value:
        temporary = Path(temporary_value)
        try:
            execution = _execute_cwl(temporary, payload, runner, limits, tmpfs_root, len(package_bytes))
            ended = datetime.now(timezone.utc)
            timing = {"created": created, "ended": ended, "elapsed": time.monotonic() - started}
            augmented, run_id, attempt_members = _attempt_evidence(
                payload, package_bytes, expected_sha256, closure, runner, execution, timing,
            )
            receipt = json_value(augmented[receipt_key])
            for label in ("stdout", "stderr"):
                (attempt_directory / f"{label}.log").write_bytes(execution[label])
            persist_receipt(dict(receipt, status="started", endedAt=None, wallSeconds=None))
            experiment_id, title = _augment_result_crate(
                augmented, expected_sha256, run_id, attempt_members, created, ended, execution,
            )
            result = _compile_attempt_result(
                temporary, output, source_directory, augmented, manifest, expected_sha256,
                run_id, experiment_id, title, execution,
            )
        except Exception as exc:
            receipt.update(status="failed", endedAt=datetime.now(timezone.utc).isoformat(),
                           wallSeconds=time.monotonic() - started)
            receipt["collectionErrors"] = list(receipt["collectionErrors"]) + [
                f"Attempt failed: {type(exc).__name__}: {str(exc)[:4096]}"
            ]
            # Read only bounded controller logs if execution/collection raised.
            for label in ("stdout", "stderr"):
                path = temporary / f"{label}.log"
                if path.exists():
                    data = _read_execution_log(path, label, receipt["collectionErrors"])
                    (attempt_directory / f"{label}.log").write_bytes(data)
            persist_receipt(receipt)
            raise
        # Publication succeeded. A controller receipt I/O failure must not
        # reclassify the valid published execution as a failed attempt.
        persist_receipt(receipt)
        return result



def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collect_tree(root: Path, collected: dict[str, bytes], max_bytes: int) -> None:
    total = sum(len(value) for value in collected.values())
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PackageError("CWL output contains a symlink")
        if not path.is_file():
            continue
        if len(collected) >= MAX_MEMBERS:
            raise PackageError("CWL output contains too many files")
        relative = path.relative_to(root).as_posix()
        safe_path(relative)
        content = bounded_read(path, min(MAX_FILE, max_bytes))
        total += len(content)
        if total > max_bytes:
            raise PackageError("CWL output and provenance exceed the package size limit")
        collected[relative] = content


def _require_bounded_tmpfs(temporary: Path, root: Path, limit_mib: int) -> None:
    resolved_root = root.resolve(strict=True)
    resolved_temporary = temporary.resolve(strict=True)
    if not resolved_temporary.is_relative_to(resolved_root):
        raise PackageError("execution deferred: runner temporary directory is outside the declared tmpfs")
    capacity = shutil.disk_usage(resolved_root).total
    if capacity < limit_mib * 1024 * 1024:
        raise PackageError("execution deferred: temporary filesystem capacity is below the declared worker limit")


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
