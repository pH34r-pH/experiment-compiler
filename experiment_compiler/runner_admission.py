"""Package intake, CWL/job admission, and bounded storage enforcement."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Container

from .archive_policy import PackageReadPolicy
from .core import (
    MANIFEST,
    PackageError,
    _verify_file_stream,
    bounded_read,
    json_value,
    safe_path,
    verify_bytes,
)
from .runner_logs import LOG_STREAM_LIMIT
from .worker_limits import observe_worker

_ADMISSION_MEMBERS = (
    "dependency-closure.json",
    "ro-crate-metadata.json",
    "experiment/runner.json",
    "experiment/workflow.cwl",
    "experiment/job.yml",
)
_WORKFLOW_MEMBERS = ("experiment/workflow.cwl", "experiment/job.yml")
_MemberSource = bytes | Path | zipfile.Path


def _read_package(data: bytes, expected_sha256: str,
                  policy: PackageReadPolicy | None = None) -> tuple[dict, dict[str, bytes]]:
    verified = verify_bytes(data, expected_sha256=expected_sha256, policy=policy)
    if verified["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("runner accepts only compiled-experiment-lifecycle-v1 plans and result artifacts")
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if name != MANIFEST}
        manifest = json_value(archive.read(MANIFEST))
    return manifest, payload

@dataclass(frozen=True)
class _VerifiedRunPackage:
    snapshot: Path
    manifest: dict
    admission: dict[str, bytes]
    members: dict[str, zipfile.Path]

@contextmanager
def _open_run_package(package: Path, snapshot: Path, expected_sha256: str,
                      policy: PackageReadPolicy | None):
    if package.is_symlink() or not package.is_file():
        raise PackageError(f"Expected regular package file: {package}")
    with snapshot.open("xb+") as retained:
        with package.open("rb") as source:
            shutil.copyfileobj(source, retained, length=1024 * 1024)
        verified = _verify_file_stream(retained, expected_sha256=expected_sha256, policy=policy)
        if verified["profile"] != "compiled-experiment-lifecycle-v1":
            raise PackageError("runner accepts only compiled-experiment-lifecycle-v1 plans and result artifacts")
        retained.seek(0)
        with zipfile.ZipFile(retained) as archive:
            manifest = json_value(archive.read(MANIFEST))
            members = {name: zipfile.Path(archive, at=name) for name in archive.namelist() if name != MANIFEST}
            admission = {name: archive.read(name) for name in _ADMISSION_MEMBERS if name in members}
            yield _VerifiedRunPackage(snapshot, manifest, admission, members)

def _dependency_closure(payload: dict[str, bytes], member_names: Container[str] | None = None) -> dict:
    for required in ("dependency-closure.json", "ro-crate-metadata.json"):
        if required not in payload:
            raise PackageError(f"execution admission requires packaged {required}")
    closure = json_value(payload["dependency-closure.json"])
    if not isinstance(closure, dict) or not isinstance(closure.get("classifications"), dict):
        raise PackageError("dependency closure has no classifications")
    classes = closure["classifications"]
    unavailable = classes.get("unavailable")
    if not isinstance(unavailable, list):
        raise PackageError("execution deferred: dependency closure requires an explicit unavailable list")
    if unavailable:
        raise PackageError("execution deferred: dependency closure has unavailable prerequisites")
    if classes.get("external") != []:
        raise PackageError("execution deferred: this local adapter admits embedded runtime dependencies only")
    embedded = classes.get("embedded")
    names = payload if member_names is None else member_names
    if not isinstance(embedded, list) or any(
        not isinstance(member, dict) or safe_path(member.get("path")) not in names for member in embedded
    ):
        raise PackageError("execution deferred: embedded dependencies must name packaged files")
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

def _worker_admission(payload: dict[str, bytes], runner: dict,
                      member_names: Container[str] | None = None) -> tuple[dict, str, set[str]]:
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
    if runner["maxWallSeconds"] > limits["wallSeconds"]:
        raise PackageError("execution deferred: runner wall budget exceeds the approved worker limit")
    try:
        job_document = yaml.safe_load(payload["experiment/job.yml"])
    except (ValueError, yaml.YAMLError) as exc:
        raise PackageError(f"execution admission cannot parse the CWL job file: {exc}") from exc
    job_members = _admit_job(job_document, payload if member_names is None else member_names, input_ids)
    worker, observation = observe_worker(os.environ.get("EXPERIMENT_RUNNER_WORKER_ENVELOPE"))
    resources = workflow_document["requirements"]["ResourceRequirement"]
    worker.check_workload(resources["coresMax"], resources["ramMax"])
    return {**limits, "workerObservation": observation}, tmpfs_root, job_members

def _admit(payload: dict[str, bytes], members: Container[str]) -> tuple[dict, dict, dict, str, set[str]]:
    closure = _dependency_closure(payload, members)
    runner = _runner_config(payload)
    limits, tmpfs_root, job_members = _worker_admission(payload, runner, members)
    return closure, runner, limits, tmpfs_root, job_members

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

def _admit_job(job: Any, members: Container[str], input_ids: set[str]) -> set[str]:
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
    job_members = set()
    for item in files:
        path = item.get("path")
        if set(item) - {"class", "path", "basename", "format"} or not isinstance(path, str):
            raise PackageError("execution deferred: job File inputs must use packaged relative paths only")
        try:
            relative = safe_path(path)
        except PackageError as exc:
            raise PackageError("execution deferred: unsafe job File path") from exc
        member = f"experiment/{relative}"
        if member not in members:
            raise PackageError(f"execution deferred: job File input is not embedded: {relative}")
        job_members.add(member)
    return job_members

def _is_document_reference_key(key: Any) -> bool:
    if key in {"run", "$import", "$include", "$schemas", "$graph", "$base",
               "cwl:tool", "cwltool:overrides"}:
        return True
    if not isinstance(key, str):
        return False
    return any(key.endswith(f"#{suffix}") for suffix in
               ("run", "import", "include", "schemas", "graph", "base", "tool", "overrides"))

def _materialize(root: Path, payload: dict[str, _MemberSource]) -> None:
    for relative, data in payload.items():
        safe_path(relative)
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            with data.open("rb") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)

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
        return bounded_read(path, LOG_STREAM_LIMIT)
    except (PackageError, OSError) as exc:
        errors.append(f"{label} rejected: {exc}")
        return b""

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
    _require_dedicated_tmpfs(root, limit_mib, "scratch")
    if resolved_temporary.stat().st_dev != resolved_root.stat().st_dev:
        raise PackageError("execution deferred: runner temporary directory must use the admitted scratch filesystem")

def _require_bounded_output_tmpfs(root: Path, limit_mib: int) -> Path:
    return _require_dedicated_tmpfs(root, limit_mib, "output")

def _require_dedicated_tmpfs(root: Path, limit_mib: int, role: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise PackageError(f"execution deferred: {role} root must be an existing regular directory")
    resolved = root.resolve(strict=True)
    current = root.absolute()
    while current != current.parent:
        if current.is_symlink():
            raise PackageError(f"execution deferred: {role} root path contains a symlink")
        current = current.parent
    try:
        entries = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PackageError(f"execution deferred: cannot verify the {role} tmpfs mount") from exc
    mount_types = []
    for line in entries:
        try:
            before, after = line.split(" - ", 1)
            fields = before.split()
            mountpoint = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
            if Path(mountpoint) == resolved:
                mount_types.append(after.split()[0])
        except (IndexError, ValueError):
            continue
    if mount_types != ["tmpfs"]:
        raise PackageError(f"execution deferred: {role} root must be an unambiguous dedicated tmpfs mountpoint")
    capacity = shutil.disk_usage(resolved).total
    cap_bytes = int(limit_mib * 1024 * 1024)
    if capacity != cap_bytes:
        raise PackageError(f"execution deferred: {role} tmpfs size must exactly match the declared worker {role} cap")
    return resolved
