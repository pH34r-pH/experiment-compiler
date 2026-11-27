"""Attempt receipts, RO-Crate augmentation, and immutable result packaging."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import PackageError, canonical, compile_package, json_value, sha256_file
from .runner_admission import (
    _MemberSource,
    _VerifiedRunPackage,
    _materialize,
    _read_execution_log,
    sha256_bytes,
)


@dataclass
class _AttemptExecution:
    output: Path
    source_directory: Path
    attempt_directory: Path
    package: _VerifiedRunPackage
    job_members: set[str]
    expected_sha256: str
    closure: dict
    runner: dict
    limits: dict
    tmpfs_root: str
    journal: dict
    receipt: dict
    receipt_key: str
    created: datetime
    started: float

def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

def _validate_private_receipt_file(path: Path, description: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise PackageError(f"{description} is absent") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise PackageError(f"{description} is not a regular file")
    if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise PackageError(f"{description} is not owned by the current runner with mode 0600")
    return info

def _validate_receipt_staging_files(attempt_directory: Path) -> None:
    """Retain interrupted private staging files while rejecting unsafe entries."""
    staging = [path for path in attempt_directory.iterdir()
               if path.name == "runner-receipt.json.tmp" or
               path.name.startswith("runner-receipt.json.tmp.")]
    if len(staging) > 64:
        raise PackageError("Attempt has too many retained receipt staging files")
    for path in staging:
        info = _validate_private_receipt_file(path, "Retained attempt receipt staging file")
        if info.st_size > 1024 * 1024:
            raise PackageError("Retained attempt receipt staging file exceeds its size limit")

def _persist_attempt_receipt(attempt_directory: Path, value: dict) -> None:
    if attempt_directory.is_symlink() or not attempt_directory.is_dir():
        raise PackageError("Attempt receipt directory is missing or unsafe")
    if os.name == "posix":
        directory_info = attempt_directory.stat()
        if (directory_info.st_uid != os.getuid() or directory_info.st_mode & 0o077):
            raise PackageError("Attempt receipt directory is not private to the current runner")
    _validate_receipt_staging_files(attempt_directory)
    pending = attempt_directory / f"runner-receipt.json.tmp.{uuid.uuid4().hex}"
    target = attempt_directory / "runner-receipt.json"
    try:
        existing = target.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        _validate_private_receipt_file(target, "Existing attempt receipt")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(pending, flags, 0o600)
    except FileExistsError as exc:
        raise PackageError("Attempt receipt pending file already exists; refusing to overwrite it") from exc
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
            created = os.fstat(descriptor)
            if (not stat.S_ISREG(created.st_mode) or created.st_uid != os.getuid() or
                    stat.S_IMODE(created.st_mode) != 0o600):
                raise PackageError("Attempt receipt staging file is not a private regular file")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _validate_private_receipt_file(pending, "Attempt receipt staging file")
    pending.replace(target)
    _fsync_directory(attempt_directory)

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

def _attempt_receipt_runtime(runner: dict, execution: dict) -> dict:
    journal = execution.get("containerJournal")
    container_journal = (
        {"availability": "private-store", "storePath": execution.get("containerJournalStorePath"),
         "sha256": journal["sha256"]}
        if isinstance(journal, dict) and isinstance(journal.get("sha256"), str)
        else {"availability": "unavailable"}
    )
    return {
        "runner": {"name": "cwltool", "version": runner["version"],
                   "python": platform.python_version(), "workerImageBase": runner.get("workerImageBase")},
        "executionMode": (
            "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime"
            if runner["executionMode"] == "cwltool-podman"
            else "cwltool orchestrator on worker; CWL CommandLineTool uses Docker runtime"
        ),
        "workerContext": {
            "reportedSandbox": os.environ.get("EXPERIMENT_RUNNER_SANDBOX", "unspecified by caller"),
            "reportedImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
            "attestation": "caller-reported; not independently verified by the adapter",
            "outerWorker": execution.get("workerObservation", {"status": "unverified"}),
        },
        "status": ("timed-out" if execution["timedOut"] else
                   "failed" if execution["returnCode"] != 0 or execution["collectionErrors"] else "succeeded"),
        "collectionErrors": execution["collectionErrors"],
        "logCapture": execution.get("logCapture"),
        "containerJournal": container_journal,
        "containerCleanup": execution.get("containerCleanup", {"state": "unknown", "allAbsent": False}),
        "scratchStorage": execution.get("scratchStorage", {"enforcement": "unverified"}),
        "outputStorage": execution.get("outputStorage", {"enforcement": "worker-tmpfs-only", "hardCap": False}),
    }

def _attempt_evidence(package: _VerifiedRunPackage, expected_sha256: str,
                      closure: dict, runner: dict, execution: dict,
                      timing: dict) -> tuple[dict[str, _MemberSource], str, list[str]]:
    created = timing["created"]
    ended = timing["ended"]
    elapsed = timing["elapsed"]
    run_id = hashlib.sha256((expected_sha256 + created.isoformat()).encode()).hexdigest()[:12]
    evidence_root = f"evidence/attempts/{run_id}"
    augmented: dict[str, _MemberSource] = {**package.members, **package.admission}
    plan_archive = f"{evidence_root}/plan-package.zip"
    output_files = execution["outputs"]
    provenance_files = execution["provenance"]
    augmented[plan_archive] = package.snapshot
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
            "@id": plan_archive, "sha256": expected_sha256, "contentSize": package.snapshot.stat().st_size,
        },
        "workflowSha256": sha256_bytes(package.admission["experiment/workflow.cwl"]),
        "jobSha256": sha256_bytes(package.admission["experiment/job.yml"]),
        "networkPolicy": "CWL NetworkAccess=false is applied to tool containers; cwltool host expressions are not independently network-isolated",
        **_attempt_receipt_runtime(runner, execution),
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

def _augment_result_crate(augmented: dict[str, _MemberSource], expected_sha256: str, run_id: str,
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
    else:
        existing_types = workflow_node.get("@type")
        workflow_types = existing_types if isinstance(existing_types, list) else [existing_types]
        if ("SoftwareApplication" not in workflow_types and "SoftwareSourceCode" not in workflow_types
                and "File" in workflow_types and "experiment/workflow.cwl" in augmented):
            workflow_node["@type"] = [*workflow_types, "SoftwareSourceCode"]
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

def _attempt_recipe(stage: Path, state: _AttemptExecution, augmented: dict[str, _MemberSource],
                    run_id: str, experiment_id: str, title: str) -> dict:
    manifest = state.package.manifest
    members = []
    for name in sorted(augmented):
        path = stage.joinpath(*name.split("/"))
        members.append({"source": name, "path": name, "sha256": sha256_file(path), "size": path.stat().st_size})
    return {
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
            "evidence": {"planPackageSha256": state.expected_sha256, "attemptId": run_id},
        },
        "members": members,
    }

def _compile_attempt_result(state: _AttemptExecution, augmented: dict[str, _MemberSource],
                            run_id: str, experiment_id: str, title: str, execution: dict) -> dict:
    output, source_directory = state.output, state.source_directory
    # Prepare the ZIP before exposing either publication destination. Claim
    # destinations exclusively and remove only paths owned by this attempt if
    # publishing the retained source or package fails.
    # Stage the source and ZIP beside their destination. Only the workflow's
    # declared files and execution state belong on the bounded worker tmpfs.
    with tempfile.TemporaryDirectory(prefix=".compiled-experiment-result-", dir=output.parent) as package_temp:
        publication_stage = Path(package_temp)
        stage = publication_stage / "result-source"
        _materialize(stage, augmented)
        recipe_path = stage / "experiment.json"
        recipe_path.write_bytes(canonical(_attempt_recipe(stage, state, augmented, run_id, experiment_id, title)))
        prepared_package = publication_stage / "result.zip"
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
