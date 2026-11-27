"""Explicit, separate CWL execution handoff for reviewed Compiled Experiments.

This module is deliberately not imported by compile/verify commands. Execution
requires an exact package digest, explicit opt-in, and a declared bounded worker.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import runner_admission, runner_process, runner_receipts
from .archive_policy import PackageReadPolicy
from .core import PackageError, canonical, compile_package, json_value, sha256_file
from .diagnostics import failure_diagnostics, report_failure
from .runner_receipts import sha256_bytes as sha256_bytes


_LOGGER = logging.getLogger(__name__)
_WORKFLOW_MEMBERS = ("experiment/workflow.cwl", "experiment/job.yml")


@dataclass
class _AttemptExecution:
    output: Path
    source_directory: Path
    attempt_directory: Path
    package: runner_admission._VerifiedRunPackage
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


def recover_attempt(attempt_directory: Path) -> dict:
    """Reconcile one retained attempt's exact CWL containers without running science."""
    with (
        failure_diagnostics(_LOGGER, "recover", "reconciliation"),
        runner_receipts._exclusive_recovery(attempt_directory),
    ):
        return _recover_retained_attempt(attempt_directory)


def _recover_retained_attempt(attempt_directory: Path) -> dict:
    if not os.path.lexists(attempt_directory / "container-journal.json"):
        return runner_receipts._load_not_started_receipt(attempt_directory)
    receipt, journal, original_journal_sha, marker = runner_receipts._load_recovery_state(attempt_directory)
    runner_process._validate_recovery_worker(attempt_directory, receipt, journal)
    runner_receipts._settle_recovery_marker(attempt_directory, receipt, original_journal_sha, marker)
    runner_receipts._retain_journal_snapshot(attempt_directory, original_journal_sha)
    errors: list[str] = []
    process_error = None
    try:
        runner_process._reconcile_cwl_process_group(journal)
    except (PackageError, OSError) as exc:
        process_error = exc
        errors.append(f"cwltool process recovery remains unresolved: {type(exc).__name__}: {str(exc)[:512]}")
    cid_directory = attempt_directory / "engine-cids"
    cid_files = list(cid_directory.glob(f"{journal['cidfilePrefix']}*.cid"))
    if errors or (runner_receipts._possible_workflow_launch(receipt, journal) and
                  not journal.get("containers") and not cid_files):
        if not errors:
            errors.append("Hard loss after launch intent has no container identity; cleanup remains unknown")
        if journal.get("state") != runner_receipts._CleanupState.UNKNOWN:
            runner_receipts._journal_update(
                attempt_directory, journal, receipt, state=runner_receipts._CleanupState.UNKNOWN,
            )
    else:
        runner_process._cleanup_container_journal(attempt_directory, journal, receipt, errors)
    if journal.get("state") != runner_receipts._CleanupState.CLEAN:
        report_failure(_LOGGER, "recover", "cleanup", receipt=receipt, error=process_error)
    return runner_receipts._record_recovery_result(attempt_directory, receipt, journal, errors)


def _augment_result_crate(augmented: dict[str, runner_admission._MemberSource], expected_sha256: str, run_id: str,
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


def _attempt_recipe(stage: Path, state: _AttemptExecution, augmented: dict[str, runner_admission._MemberSource],
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


def _compile_attempt_result(state: _AttemptExecution, augmented: dict[str, runner_admission._MemberSource],
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
        runner_process._materialize(stage, augmented)
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


def _retain_failed_attempt(attempt_directory: Path, temporary: Path, receipt: dict,
                           started: float, exc: BaseException) -> None:
    runner_receipts._finish_attempt(receipt, runner_receipts._AttemptStatus.FAILED)
    receipt.update(endedAt=datetime.now(timezone.utc).isoformat(), wallSeconds=time.monotonic() - started)
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
            data = runner_process._read_execution_log(path, label, receipt["collectionErrors"])
            (attempt_directory / f"{label}.log").write_bytes(data)
    runner_receipts._persist_attempt_receipt(attempt_directory, receipt)


def _run_retained_attempt(state: _AttemptExecution) -> dict:
    runtime_payload: dict[str, runner_admission._MemberSource] = {
        name: state.package.members[name] for name in state.job_members
    }
    runtime_payload.update({name: state.package.admission[name] for name in _WORKFLOW_MEMBERS})
    with tempfile.TemporaryDirectory(prefix="compiled-experiment-run-") as temporary_value:
        temporary = Path(temporary_value)
        phase = "execution"
        try:
            execution = runner_process._execute_cwl(runner_process._CwlExecutionRequest(
                temporary, runtime_payload, state.runner, state.limits, state.tmpfs_root,
                (state.attempt_directory, state.journal, state.receipt),
            ))
            execution["workerObservation"] = state.limits["workerObservation"]
            execution.setdefault("containerJournal", state.receipt.get("containerJournal"))
            execution.setdefault("containerCleanup", runner_receipts._container_cleanup_summary(state.journal))
            phase = "journal-persistence"
            execution["containerJournalStorePath"] = runner_receipts._retain_journal_snapshot(
                state.attempt_directory, execution["containerJournal"]["sha256"]
            )
            phase = "evidence-assembly"
            ended = datetime.now(timezone.utc)
            timing = {"created": state.created, "ended": ended,
                      "elapsed": time.monotonic() - state.started}
            augmented, run_id, attempt_members = runner_receipts._attempt_evidence(
                state.package, state.expected_sha256, state.closure, state.runner, execution, timing,
            )
            archived_receipt = json_value(augmented[state.receipt_key])
            private_fields = {"containerJournal", "status", "endedAt", "wallSeconds"}
            state.receipt.update({key: value for key, value in archived_receipt.items() if key not in private_fields})
            state.receipt["containerCleanup"] = execution.get(
                "containerCleanup", {"state": "unknown", "allAbsent": False}
            )
            phase = "receipt-persistence"
            for label in ("stdout", "stderr"):
                (state.attempt_directory / f"{label}.log").write_bytes(execution[label])
            runner_receipts._persist_attempt_receipt(state.attempt_directory, state.receipt)
            phase = "result-packaging"
            experiment_id, title = _augment_result_crate(
                augmented, state.expected_sha256, run_id, attempt_members,
                state.created, ended, execution,
            )
            result = _compile_attempt_result(state, augmented, run_id, experiment_id, title, execution)
        except (Exception, KeyboardInterrupt) as exc:
            with runner_process._defer_operator_signals(exc):
                report_failure(_LOGGER, "run", phase, receipt=state.receipt, error=exc)
                with failure_diagnostics(_LOGGER, "run", "failure-receipt", receipt=state.receipt):
                    _retain_failed_attempt(state.attempt_directory, temporary, state.receipt, state.started, exc)
            raise
        with (runner_process._defer_operator_signals(),
              failure_diagnostics(_LOGGER, "run", "terminal-receipt", receipt=state.receipt)):
            runner_receipts._finish_attempt(state.receipt, runner_receipts._AttemptStatus(archived_receipt["status"]))
            state.receipt.update(endedAt=archived_receipt["endedAt"], wallSeconds=archived_receipt["wallSeconds"])
            runner_receipts._persist_attempt_receipt(state.attempt_directory, state.receipt)
        return result


def run_package(package: Path, output: Path, *, expected_sha256: str,
                allow_workflow_execution: bool = False, policy: PackageReadPolicy | None = None) -> dict:
    """Run a digest-pinned lifecycle package and create a new immutable result package."""
    with ExitStack() as resources:
        with failure_diagnostics(_LOGGER, "run", "admission", digest=expected_sha256):
            if not allow_workflow_execution:
                raise PackageError("workflow execution requires explicit opt-in after reviewing the package")
            source_directory = output.with_name(output.stem + ".source")
            if output.exists() or source_directory.exists():
                raise PackageError("run output package or source directory already exists; attempts are never overwritten")
            staging_parent = next((parent for parent in output.absolute().parents if parent.is_dir()), None)
            if staging_parent is None:
                raise PackageError("run output must have an accessible parent directory for package intake")
            intake = resources.enter_context(
                tempfile.TemporaryDirectory(prefix=".compiled-experiment-intake-", dir=staging_parent)
            )
            snapshot = Path(intake) / "plan.zip"
            opened = resources.enter_context(
                runner_admission._open_run_package(package, snapshot, expected_sha256, policy)
            )
        return _run_verified_package(opened, output, source_directory, expected_sha256)


def _run_verified_package(package: runner_admission._VerifiedRunPackage, output: Path, source_directory: Path,
                          expected_sha256: str) -> dict:
    with failure_diagnostics(_LOGGER, "run", "admission", digest=expected_sha256):
        closure, runner, limits, tmpfs_root, job_members = runner_admission._admit(package.admission, package.members)
        attempt_directory = output.with_name(output.stem + ".attempt")
        if attempt_directory.exists():
            raise PackageError("run attempt directory already exists; attempts are never overwritten")
    created = datetime.now(timezone.utc)
    started = time.monotonic()
    execution = {"returnCode": None, "timedOut": False, "stdout": b"", "stderr": b"",
                 "outputs": {}, "provenance": {}, "collectionErrors": [],
                 "workerObservation": limits["workerObservation"]}
    timing = {"created": created, "ended": created, "elapsed": 0}
    initial, run_id, _ = runner_receipts._attempt_evidence(
        package, expected_sha256, closure, runner, execution, timing,
    )
    receipt_key = f"evidence/attempts/{run_id}/runner-receipt.json"
    receipt = json_value(initial[receipt_key])
    receipt.update(status=runner_receipts._AttemptStatus.STARTED, endedAt=None, wallSeconds=None,
                   executionPhase=runner_receipts._ProcessPhase.PREFLIGHT, executionNeverStarted=False,
                   launchIntentPersisted=False)
    with failure_diagnostics(_LOGGER, "run", "initial-receipt", receipt=receipt):
        attempt_directory.mkdir(parents=True)
        attempt_directory.chmod(0o700)
        (attempt_directory / "runner-home").mkdir(mode=0o700)
        (attempt_directory / "runner-home").chmod(0o700)
        runner_receipts._persist_attempt_receipt(attempt_directory, receipt)
    try:
        journal = runner_process._new_container_journal(attempt_directory, run_id, runner)
    except (PackageError, OSError, subprocess.SubprocessError) as exc:
        report_failure(_LOGGER, "run", "controller-preflight", receipt=receipt, error=exc)
        runner_receipts._transition_process(receipt, runner_receipts._ProcessPhase.NOT_STARTED)
        runner_receipts._finish_attempt(receipt, runner_receipts._AttemptStatus.FAILED)
        receipt.update(endedAt=datetime.now(timezone.utc).isoformat(),
                       containerCleanup={"state": runner_receipts._CleanupState.NOT_STARTED, "engine": runner_admission._container_engine(runner),
                                        "containerCount": 0, "absentCount": 0, "allAbsent": False},
                       collectionErrors=[f"Attempt preflight failed before workflow launch: {type(exc).__name__}: {str(exc)[:1024]}"])
        with failure_diagnostics(_LOGGER, "run", "failure-receipt", receipt=receipt):
            runner_receipts._persist_attempt_receipt(attempt_directory, receipt)
        raise
    journal["launchIntentPersisted"] = True
    runner_receipts._transition_process(receipt, runner_receipts._ProcessPhase.LAUNCH_INTENT)
    with failure_diagnostics(_LOGGER, "run", "launch-journal", receipt=receipt):
        runner_receipts._journal_receipt_reference(attempt_directory, journal, receipt)
    return _run_retained_attempt(_AttemptExecution(
        output=output, source_directory=source_directory, attempt_directory=attempt_directory,
        package=package, job_members=job_members,
        expected_sha256=expected_sha256, closure=closure, runner=runner, limits=limits,
        tmpfs_root=tmpfs_root, journal=journal, receipt=receipt, receipt_key=receipt_key,
        created=created, started=started,
    ))


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
