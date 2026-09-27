"""Explicit, separate CWL execution handoff for reviewed Compiled Experiments.

This module is deliberately not imported by compile/verify commands. Execution
requires an exact package digest, explicit opt-in, and a declared bounded worker.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
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

from .core import (MANIFEST, MAX_MEMBERS, MAX_TOTAL, PackageError, bounded_read, canonical, compile_package,
                   json_value, safe_path, verify_bytes)


def _read_package(data: bytes, expected_sha256: str) -> tuple[dict, dict[str, bytes]]:
    verified = verify_bytes(data, expected_sha256=expected_sha256)
    if verified["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("runner accepts only compiled-experiment-lifecycle-v1 plans and result artifacts")
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if name != MANIFEST}
        manifest = json_value(archive.read(MANIFEST))
    return manifest, payload


def _admit(payload: dict[str, bytes]) -> tuple[dict, dict, dict, str]:
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
    for required in ("experiment/workflow.cwl", "experiment/job.yml", "experiment/runner.json"):
        if required not in payload:
            raise PackageError(f"execution deferred: plan has no admitted executable component {required}")
    runner = json_value(payload["experiment/runner.json"])
    if not isinstance(runner, dict) or runner.get("runner") != "cwltool" or not isinstance(runner.get("version"), str):
        raise PackageError("runner.json must pin the cwltool runner and exact version")
    if runner.get("executionMode") != "cwltool-docker":
        raise PackageError("unsupported execution mode; this adapter requires cwltool Docker execution")
    timeout_seconds = runner.get("maxWallSeconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 86400:
        raise PackageError("runner.json must declare maxWallSeconds between 1 and 86400")
    try:
        installed = importlib.metadata.version("cwltool")
    except importlib.metadata.PackageNotFoundError as exc:
        raise PackageError("pinned cwltool is not installed in this runner environment") from exc
    if installed != runner["version"]:
        raise PackageError(f"cwltool version mismatch: package requires {runner['version']}, installed {installed}")
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
    _admit_workflow(workflow_document, limits, runner.get("workerImageBase"))
    try:
        job_document = yaml.safe_load(payload["experiment/job.yml"])
    except (ValueError, yaml.YAMLError) as exc:
        raise PackageError(f"execution admission cannot parse the CWL job file: {exc}") from exc
    _admit_job(job_document, payload)
    return closure, runner, limits, tmpfs_root


def _admit_workflow(document: Any, limits: Any, expected_image: Any) -> None:
    required_limits = {"cores", "ramMiB", "tmpdirMiB", "outdirMiB", "wallSeconds"}
    if not isinstance(limits, dict) or not required_limits <= set(limits):
        raise PackageError("worker must declare CPU, memory, temporary/output storage and wall-time limits")
    numeric_limits = {key: limits[key] for key in required_limits}
    if any(type(value) not in (int, float) or value <= 0 for value in numeric_limits.values()):
        raise PackageError("worker resource limits must be positive numbers")
    tools: list[dict] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("class") == "CommandLineTool":
                tools.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
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
            if (type(requested_min) not in (int, float) or type(requested_max) not in (int, float) or
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


def _values_for_key(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if key in value:
            if not isinstance(value[key], str):
                raise PackageError(f"CWL {key} values must be strings")
            found.append(value[key])
        for child in value.values():
            found.extend(_values_for_key(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_values_for_key(child, key))
    return found


def _admit_job(job: Any, payload: dict[str, bytes]) -> None:
    if not isinstance(job, dict):
        raise PackageError("execution admission requires a mapping CWL job document")
    files: list[dict] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("class") == "File":
                files.append(value)
            if value.get("class") == "Directory":
                raise PackageError("execution deferred: Directory inputs are outside this adapter's reviewed subset")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(job)
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


def _materialize(root: Path, payload: dict[str, bytes]) -> None:
    for relative, data in payload.items():
        safe_path(relative)
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def run_package(package: Path, output: Path, *, expected_sha256: str,
                allow_workflow_execution: bool = False) -> dict:
    """Run a digest-pinned lifecycle package and create a new immutable result package.

    Each CWL CommandLineTool must declare a digest-pinned container, explicit
    network denial, and resource/time maxima. cwltool is invoked with strict
    CPU/memory limits and image pulling disabled. Callers must explicitly opt in.
    """
    if not allow_workflow_execution:
        raise PackageError("workflow execution requires explicit opt-in after reviewing the package")
    source_directory = output.with_name(output.stem + ".source")
    if output.exists() or source_directory.exists():
        raise PackageError("run output package or source directory already exists; attempts are never overwritten")
    package_bytes = bounded_read(package, MAX_TOTAL)
    manifest, payload = _read_package(package_bytes, expected_sha256)
    closure, runner, limits, tmpfs_root = _admit(payload)
    created = datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="compiled-experiment-run-") as temporary:
            _require_bounded_tmpfs(Path(temporary), Path(tmpfs_root), limits["tmpdirMiB"])
            work = Path(temporary) / "package"
            work.mkdir()
            _materialize(work, payload)
            attempt_out = Path(temporary) / "outputs"
            provenance = Path(temporary) / "cwlprov"
            command = [
                sys.executable, "-m", "cwltool", "--disable-pull", "--strict-memory-limit", "--strict-cpu-limit",
                "--disable-host-provenance", "--disable-user-provenance",
                "--basedir", str(work / "experiment"),
                "--tmpdir-prefix", str(Path(temporary) / "cwl-tmp-"),
                "--tmp-outdir-prefix", str(Path(temporary) / "cwl-out-"),
                "--outdir", str(attempt_out), "--provenance", str(provenance),
                str(work / "experiment/workflow.cwl"), str(work / "experiment/job.yml"),
            ]
            stdout_path = Path(temporary) / "stdout.log"
            stderr_path = Path(temporary) / "stderr.log"
            with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                runner_home = Path(temporary) / "runner-home"
                runner_home.mkdir()
                process_environment = {
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(runner_home),
                    "TMPDIR": temporary,
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
                process = subprocess.Popen(command, cwd=work, env=process_environment,
                                            stdout=stdout_stream, stderr=stderr_stream,
                                            start_new_session=True)
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
            ended = datetime.now(timezone.utc)
            elapsed = time.monotonic() - started
            stdout_bytes = bounded_read(stdout_path, 4 * 1024 * 1024)
            stderr_bytes = bounded_read(stderr_path, 4 * 1024 * 1024)
            output_files: dict[str, bytes] = {}
            if attempt_out.exists():
                _collect_tree(attempt_out, output_files, MAX_TOTAL)
            provenance_files: dict[str, bytes] = {}
            if provenance.exists():
                _collect_tree(provenance, provenance_files, MAX_TOTAL)
            if not output_files and return_code == 0:
                raise PackageError("CWL runner succeeded without producing any declared result files")
            run_id = hashlib.sha256((expected_sha256 + created.isoformat()).encode()).hexdigest()[:12]
            evidence_root = f"evidence/attempts/{run_id}"
            augmented = dict(payload)
            plan_archive = f"{evidence_root}/plan-package.zip"
            augmented[plan_archive] = package_bytes
            augmented.update({f"{evidence_root}/{name}": value for name, value in output_files.items()})
            augmented.update({f"{evidence_root}/workflow-run/{name}": value
                              for name, value in provenance_files.items()})
            augmented[f"{evidence_root}/stdout.log"] = stdout_bytes
            augmented[f"{evidence_root}/stderr.log"] = stderr_bytes
            output_index = [{"@id": f"{evidence_root}/{name}", "sha256": sha256_bytes(value), "contentSize": len(value)}
                            for name, value in sorted(output_files.items())]
            receipt = {
                "schemaVersion": 1,
                "attemptId": run_id,
                "planPackageSha256": expected_sha256,
                "planPackageArchive": {"@id": plan_archive, "sha256": expected_sha256,
                                       "contentSize": len(package_bytes)},
                "workflowSha256": sha256_bytes(payload["experiment/workflow.cwl"]),
                "jobSha256": sha256_bytes(payload["experiment/job.yml"]),
                "runner": {"name": "cwltool", "version": runner["version"], "python": platform.python_version(),
                           "workerImageBase": runner.get("workerImageBase")},
                "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses DockerExecutor",
                "networkPolicy": "CWL NetworkAccess=false is applied to tool containers; cwltool host expressions are not independently network-isolated",
                "workerContext": {
                    "reportedSandbox": os.environ.get("EXPERIMENT_RUNNER_SANDBOX", "unspecified by caller"),
                    "reportedImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
                    "attestation": "caller-reported; not independently verified by the adapter",
                },
                "status": "timed-out" if timed_out else "succeeded" if return_code == 0 else "failed",
                "exitCode": return_code,
                "timedOut": timed_out,
                "startedAt": created.isoformat(),
                "endedAt": ended.isoformat(),
                "wallSeconds": elapsed,
                "resourceMeasurements": {"peakProcessMemory": "unknown; not measured by this adapter"},
                "outputs": output_index,
                "workflowRunCrateFiles": sorted(provenance_files),
                "availableClosure": closure["classifications"],
            }
            augmented[f"{evidence_root}/runner-receipt.json"] = canonical(receipt)
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
            if (not isinstance(experiment_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", experiment_id) or
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
            attempt_members = [f"{evidence_root}/{name}" for name in output_files]
            attempt_members += [f"{evidence_root}/workflow-run/{name}" for name in provenance_files]
            attempt_members += [plan_archive, f"{evidence_root}/stdout.log", f"{evidence_root}/stderr.log",
                                f"{evidence_root}/runner-receipt.json"]
            for name in attempt_members:
                if name not in known_parts:
                    parts.append({"@id": name})
                    graph.append({"@id": name, "@type": "File", "encodingFormat": _media_type(name)})
            root["hasPart"] = parts
            if not any(node.get("@id") == "https://w3id.org/ro/wfrun/process/0.6" for node in graph):
                graph.append({"@id": "https://w3id.org/ro/wfrun/process/0.6",
                              "@type": ["CreativeWork", "Profile"], "version": "0.6"})
            license_id = root.get("license", {}).get("@id") if isinstance(root.get("license"), dict) else None
            if isinstance(license_id, str) and not any(node.get("@id") == license_id for node in graph):
                graph.append({"@id": license_id, "@type": "CreativeWork", "name": "Apache License 2.0"})
            workflow_node = next((node for node in graph if node.get("@id") == "experiment/workflow.cwl"), None)
            if workflow_node is None:
                workflow_node = {"@id": "experiment/workflow.cwl", "@type": ["File", "SoftwareSourceCode"],
                                 "programmingLanguage": "Common Workflow Language", "name": "CWL execution workflow"}
                graph.append(workflow_node)
                root["hasPart"].append({"@id": "experiment/workflow.cwl"})
            graph.append({"@id": f"#attempt-{run_id}", "@type": "CreateAction",
                          "name": f"CWL attempt {run_id}",
                          "actionStatus": "https://schema.org/CompletedActionStatus" if return_code == 0 and not timed_out
                          else "https://schema.org/FailedActionStatus",
                          "instrument": {"@id": "experiment/workflow.cwl"},
                          "object": {"@id": "experiment/protocol.md"},
                          "result": [{"@id": name} for name in attempt_members],
                          "startTime": created.isoformat(), "endTime": ended.isoformat()})
            augmented["ro-crate-metadata.json"] = canonical(crate)
            stage = Path(temporary) / "result-source"
            _materialize(stage, augmented)
            recipe_path = stage / "experiment.json"
            recipe = {
                "buildRecipeVersion": 1,
                "id": f"{experiment_id[:60]}-attempt-{run_id}",
                "title": f"{title} — attempt {run_id}",
                "profile": "compiled-experiment-lifecycle-v1",
                "manifest": {"schemaVersion": 2, "packageType": "compiled-experiment",
                    "profile": "compiled-experiment-lifecycle-v1", "source": manifest["source"],
                    "standards": manifest["standards"],
                    "evidence": {"planPackageSha256": expected_sha256, "attemptId": run_id}},
                "members": [{"source": name, "path": name, "sha256": sha256_bytes(content), "size": len(content)}
                            for name, content in sorted(augmented.items())],
            }
            recipe_path.write_bytes(canonical(recipe))
            result = compile_package(recipe_path, output)
            recipe["expectedPackage"] = {"sha256": result["packageSha256"],
                                          "size": result["packageSizeBytes"]}
            recipe_path.write_bytes(canonical(recipe))
            shutil.copytree(stage, source_directory)
            if return_code or timed_out:
                result["executionStatus"] = "failed"
                result["exitCode"] = return_code
                result["timedOut"] = timed_out
            else:
                result["executionStatus"] = "succeeded"
            return result
    except subprocess.SubprocessError as exc:
        raise PackageError(f"CWL runner could not start: {exc}") from exc


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
        content = bounded_read(path, max_bytes)
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
    if capacity > limit_mib * 1024 * 1024:
        raise PackageError("execution deferred: temporary filesystem capacity exceeds the declared worker limit")


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
