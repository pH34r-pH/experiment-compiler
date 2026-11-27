"""Explicit, separate CWL execution handoff for reviewed Compiled Experiments.

This module is deliberately not imported by compile/verify commands. Execution
requires an exact package digest, explicit opt-in, and a declared bounded worker.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive_policy import PackageReadPolicy
from .core import PackageError, json_value
from .runner_admission import (
    _ADMISSION_MEMBERS,
    _WORKFLOW_MEMBERS,
    _MemberSource,
    _VerifiedRunPackage,
    _admit,
    _admit_container_requirement,
    _admit_job,
    _admit_workflow,
    _collect_execution_tree,
    _collect_tree,
    _dependency_closure,
    _finite_number,
    _is_document_reference_key,
    _materialize,
    _open_run_package,
    _read_execution_log,
    _read_package,
    _require_bounded_output_tmpfs,
    _require_bounded_tmpfs,
    _require_dedicated_tmpfs,
    _runner_config,
    _values_for_key,
    _worker_admission,
    sha256_bytes,
)
from .runner_journal import (
    _CONTAINER_ID,
    _CONTAINER_JOURNAL_SCHEMA,
    _ENGINE_IMAGE_ID,
    _MAX_ATTEMPT_CONTAINERS,
    _cleanup_container_journal,
    _container_cleanup_summary,
    _container_engine,
    _container_engine_environment,
    _container_present,
    _controller_identity,
    _controller_is_alive,
    _discover_container_ids,
    _engine_call,
    _engine_image_reference_matches,
    _journal_receipt_reference,
    _journal_update,
    _new_container_journal,
    _normalize_engine_image_id,
    _retain_journal_snapshot,
    _write_container_journal,
)
from .runner_logs import LOG_STREAM_LIMIT, LogCapture
from .runner_process import (
    _CwlCommandRequest,
    _CwlExecutionPaths,
    _CwlExecutionRequest,
    _current_cwl_process_fields,
    _cwl_process_environment,
    _cwl_process_identity,
    _defer_operator_signals,
    _kernel_boot_uuid,
    _process_group_exists,
    _reconcile_cwl_process_group,
    _replay_operator_signal,
    _run_command,
    _signal_retained_cwl_group,
    _terminate_process_group,
    _validated_cwl_process_identity,
)
from .runner_receipts import (
    _AttemptExecution,
    _attempt_evidence,
    _attempt_recipe,
    _attempt_receipt_runtime,
    _augment_result_crate,
    _compile_attempt_result,
    _fsync_directory,
    _media_type,
    _persist_attempt_receipt,
    _refs,
    _retain_failed_attempt,
    _validate_private_receipt_file,
    _validate_receipt_staging_files,
)
from .runner_recovery import (
    _MAX_RECOVERY_AGE_SECONDS,
    _adopt_pending_journal_update,
    _adopt_recovery_journal,
    _load_not_started_receipt,
    _load_recovery_state,
    _preflight_receipt_engine,
    _require_empty_preflight_cid_directory,
    _validate_current_journal_marker,
    _validate_not_started_cleanup,
    _validate_not_started_receipt,
    _validate_recovery_age_and_controller,
    _validate_recovery_container_inventory,
    _validate_recovery_directories,
    _validate_recovery_journal_identity,
    _validate_recovery_receipt_worker,
    _validate_recovery_worker,
    recover_attempt,
)


def _bounded_output_root(tmpfs_root: str, limits: dict) -> Path | None:
    value = os.environ.get("EXPERIMENT_RUNNER_OUTPUT_TMPFS_ROOT")
    if not value:
        return None
    output_root = _require_bounded_output_tmpfs(Path(value), limits["outdirMiB"])
    scratch_root = Path(tmpfs_root).resolve(strict=True)
    if (output_root == scratch_root or output_root.is_relative_to(scratch_root) or
            scratch_root.is_relative_to(output_root)):
        raise PackageError("execution deferred: bounded output tmpfs must be separate from CWL scratch storage")
    return output_root

def _handle_cwl_interrupt(paths: _CwlExecutionPaths, process: Any, interruption: KeyboardInterrupt) -> None:
    group_remaining = False
    if process is not None:
        for error in _terminate_process_group(process):
            interruption.add_note(error)
        group_remaining = _process_group_exists(process.pid)
    errors: list[str] = []
    if group_remaining:
        paths.journal["state"] = "unknown"
        _journal_update(paths.attempt_directory, paths.journal, paths.receipt)
        errors.append("Container cleanup skipped because the CWL process group remains alive")
    else:
        _cleanup_container_journal(paths.attempt_directory, paths.journal, paths.receipt, errors)
    for error in errors:
        interruption.add_note(error)
    _remove_bounded_output(paths.output_attempt, interruption)

def _handle_cwl_error(paths: _CwlExecutionPaths, process: Any, exc: Exception) -> None:
    errors = _terminate_process_group(process) if process is not None else []
    container_errors: list[str] = []
    if process is not None and _process_group_exists(process.pid):
        paths.journal["state"] = "unknown"
        _journal_update(paths.attempt_directory, paths.journal, paths.receipt)
        container_errors.append("Container cleanup skipped because the CWL process group remains alive")
    else:
        _cleanup_container_journal(paths.attempt_directory, paths.journal, paths.receipt, container_errors)
    _remove_bounded_output(paths.output_attempt, exc)
    for error in errors + container_errors:
        if hasattr(exc, "add_note"):
            exc.add_note(error)
    if not isinstance(exc, (OSError, subprocess.SubprocessError, PackageError)):
        raise PackageError(f"CWL runner could not complete its controller handoff: {exc}") from exc

def _record_cwl_exit(paths: _CwlExecutionPaths, return_code: int | None, *, unresolved: bool) -> None:
    if unresolved:
        paths.journal["cwlProcessGroupUnresolved"] = True
        _journal_update(paths.attempt_directory, paths.journal, paths.receipt, state="unknown")
    else:
        paths.journal["cwlProcessExit"] = {"returnCode": return_code}
        _journal_update(paths.attempt_directory, paths.journal, paths.receipt)

def _wait_cwl_process(paths: _CwlExecutionPaths, process: Any, timeout: int) -> tuple[int, bool, list[str]]:
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        errors = _terminate_process_group(process)
        _record_cwl_exit(paths, process.returncode, unresolved=_process_group_exists(process.pid))
        return process.returncode or 124, True, errors
    group_exists = _process_group_exists(process.pid)
    errors = _terminate_process_group(process) if group_exists else []
    _record_cwl_exit(paths, return_code, unresolved=group_exists and _process_group_exists(process.pid))
    return return_code, False, errors

def _finish_cwl_logs(paths: _CwlExecutionPaths, capture: LogCapture,
                     original: BaseException | None = None) -> None:
    if paths.receipt.get("logCapture") is None:
        with _defer_operator_signals(original):
            capture.finish()
            paths.receipt["logCapture"] = capture.summary()
            paths.receipt.setdefault("collectionErrors", []).extend(capture.errors())

@contextmanager
def _capture_cwl_logs(paths: _CwlExecutionPaths):
    with ExitStack() as resources:
        capture = LogCapture(resources, paths.stdout_path, paths.stderr_path)
        try:
            with _defer_operator_signals():
                capture.start()
            yield capture
        except BaseException as exc:
            _finish_cwl_logs(paths, capture, exc)
            raise
        else:
            _finish_cwl_logs(paths, capture)

def _run_cwl_process(paths: _CwlExecutionPaths, timeout: int, capture: LogCapture) -> tuple[int, bool, list[str]]:
    process = None
    try:
        with _defer_operator_signals():
            process = subprocess.Popen(
                paths.command, cwd=paths.work, env=paths.environment,
                stdout=capture.streams["stdout"].writer, stderr=capture.streams["stderr"].writer,
                start_new_session=True,
            )
            capture.close_writers()
            paths.journal["cwlProcessIdentity"] = _cwl_process_identity(process.pid)
            paths.receipt.update(executionPhase="running", executionNeverStarted=False,
                                 launchIntentPersisted=True)
            _journal_update(paths.attempt_directory, paths.journal, paths.receipt,
                            state="running", processStartedAt=datetime.now(timezone.utc).isoformat())
        outcome = _wait_cwl_process(paths, process, timeout)
        paths.receipt.update(exitCode=outcome[0], timedOut=outcome[1])
        _finish_cwl_logs(paths, capture)
        return outcome
    except KeyboardInterrupt as interruption:
        with _defer_operator_signals(interruption):
            if process is not None and paths.journal["cancellation"] is None:
                paths.journal["cancellation"] = {"requestedAt": datetime.now(timezone.utc).isoformat()}
                _journal_update(paths.attempt_directory, paths.journal, paths.receipt, state="cancellation-requested")
            _handle_cwl_interrupt(paths, process, interruption)
        raise
    except Exception as exc:
        _handle_cwl_error(paths, process, exc)
        raise

def _remove_bounded_output(output_attempt: Path | None, error_target: BaseException) -> None:
    if output_attempt is None or not output_attempt.exists():
        return
    try:
        shutil.rmtree(output_attempt)
    except OSError as exc:
        note = f"bounded output tmpfs cleanup failed: {type(exc).__name__}: {str(exc)[:512]}"
        if hasattr(error_target, "add_note"):
            error_target.add_note(note)

def _prepare_attempt_context(request: _CwlExecutionRequest) -> tuple[Path, dict, dict]:
    temporary, runner = request.temporary, request.runner
    context = request.context
    if context is not None:
        attempt_directory, journal, receipt = context
        if not (attempt_directory / "engine-cids").exists():
            (attempt_directory / "engine-cids").mkdir(mode=0o700)
        return attempt_directory, journal, receipt
    attempt_directory, journal, receipt = None, None, None
    if attempt_directory is None:
        attempt_directory = temporary / "attempt-evidence"
        attempt_directory.mkdir(mode=0o700)
    cid_directory = attempt_directory / "engine-cids"
    if not cid_directory.exists():
        cid_directory.mkdir(mode=0o700)
    if journal is None:
        journal = {
            "schema": _CONTAINER_JOURNAL_SCHEMA, "attemptId": "test-attempt",
            "engine": _container_engine(runner),
            "enginePath": "/usr/bin/podman" if runner["executionMode"] == "cwltool-podman" else "/usr/bin/docker",
            "workerImageRef": runner.get("workerImageBase"), "workerImageId": None,
            "reportedWorkerImageId": "unspecified", "state": "prepared",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "cidfilePrefix": "test-attempt-", "containers": [], "cancellation": None,
        }
    return attempt_directory, journal, receipt or {}

def _prepare_cwl_paths(request: _CwlExecutionRequest) -> _CwlExecutionPaths:
    temporary, payload = request.temporary, request.payload
    runner, limits, tmpfs_root = request.runner, request.limits, request.tmpfs_root
    attempt_directory, journal, receipt = _prepare_attempt_context(request)
    _require_bounded_tmpfs(temporary, Path(tmpfs_root), limits["tmpdirMiB"])
    output_root = _bounded_output_root(tmpfs_root, limits)
    work = temporary / "package"
    work.mkdir()
    _materialize(work, payload)
    output_attempt = output_root / journal["attemptId"] if output_root else None
    if output_attempt is not None:
        if output_attempt.exists() or output_attempt.is_symlink():
            raise PackageError("bounded output attempt path already exists; refusing reuse")
        output_attempt.mkdir(mode=0o700)
    attempt_out = output_attempt / "outputs" if output_attempt else temporary / "outputs"
    prefix = output_attempt / "cwl-out-" if output_attempt else None
    provenance = temporary / "cwlprov"
    command = _run_command(_CwlCommandRequest(
        temporary, work, attempt_out, provenance, runner,
        attempt_directory / "engine-cids", journal["cidfilePrefix"], prefix,
    ))
    return _CwlExecutionPaths(
        attempt_directory, journal, receipt, output_attempt, attempt_out, provenance,
        temporary / "stdout.log", temporary / "stderr.log", command, work,
        _cwl_process_environment(temporary),
    )

def _execute_cwl(request: _CwlExecutionRequest) -> dict:
    limits = request.limits
    paths = _prepare_cwl_paths(request)
    with _capture_cwl_logs(paths) as capture:
        return_code, timed_out, cleanup_errors = _run_cwl_process(paths, request.runner["maxWallSeconds"], capture)
    attempt_directory, journal, receipt = paths.attempt_directory, paths.journal, paths.receipt
    output_attempt, attempt_out, provenance = paths.output_attempt, paths.attempt_out, paths.provenance
    stdout_path, stderr_path = paths.stdout_path, paths.stderr_path
    collection_errors = [*cleanup_errors, *receipt["collectionErrors"]]
    if journal.get("cwlProcessGroupUnresolved") is True:
        journal["state"] = "unknown"
        _journal_update(attempt_directory, journal, receipt)
        collection_errors.append("Container cleanup skipped because the CWL process group remains alive")
    else:
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
        "logCapture": receipt["logCapture"],
        "containerJournal": receipt.get("containerJournal"),
        "containerCleanup": _container_cleanup_summary(journal),
        "scratchStorage": {"enforcement": "dedicated-mounted-tmpfs", "capMiB": limits["tmpdirMiB"]},
        "outputStorage": ({"enforcement": "separate-mounted-tmpfs", "capMiB": limits["outdirMiB"]}
                          if output_attempt is not None else {"enforcement": "worker-tmpfs-only", "hardCap": False}),
    }

def _run_retained_attempt(state: _AttemptExecution) -> dict:
    runtime_payload: dict[str, _MemberSource] = {name: state.package.members[name] for name in state.job_members}
    runtime_payload.update({name: state.package.admission[name] for name in _WORKFLOW_MEMBERS})
    with tempfile.TemporaryDirectory(prefix="compiled-experiment-run-") as temporary_value:
        temporary = Path(temporary_value)
        try:
            execution = _execute_cwl(_CwlExecutionRequest(
                temporary, runtime_payload, state.runner, state.limits, state.tmpfs_root,
                (state.attempt_directory, state.journal, state.receipt),
            ))
            execution["workerObservation"] = state.limits["workerObservation"]
            execution.setdefault("containerJournal", state.receipt.get("containerJournal"))
            execution.setdefault("containerCleanup", _container_cleanup_summary(state.journal))
            execution["containerJournalStorePath"] = _retain_journal_snapshot(
                state.attempt_directory, execution["containerJournal"]["sha256"]
            )
            ended = datetime.now(timezone.utc)
            timing = {"created": state.created, "ended": ended,
                      "elapsed": time.monotonic() - state.started}
            augmented, run_id, attempt_members = _attempt_evidence(
                state.package, state.expected_sha256, state.closure, state.runner, execution, timing,
            )
            archived_receipt = json_value(augmented[state.receipt_key])
            state.receipt.update({key: value for key, value in archived_receipt.items()
                                  if key not in {"containerJournal", "containerCleanup"}})
            state.receipt["containerCleanup"] = execution.get(
                "containerCleanup", {"state": "unknown", "allAbsent": False}
            )
            for label in ("stdout", "stderr"):
                (state.attempt_directory / f"{label}.log").write_bytes(execution[label])
            pending = dict(state.receipt, status="started", endedAt=None, wallSeconds=None)
            _persist_attempt_receipt(state.attempt_directory, pending)
            experiment_id, title = _augment_result_crate(
                augmented, state.expected_sha256, run_id, attempt_members,
                state.created, ended, execution,
            )
            result = _compile_attempt_result(state, augmented, run_id, experiment_id, title, execution)
        except (Exception, KeyboardInterrupt) as exc:
            with _defer_operator_signals(exc):
                _retain_failed_attempt(state.attempt_directory, temporary, state.receipt, state.started, exc)
            raise
        with _defer_operator_signals():
            _persist_attempt_receipt(state.attempt_directory, state.receipt)
        return result

def run_package(package: Path, output: Path, *, expected_sha256: str,
                allow_workflow_execution: bool = False, policy: PackageReadPolicy | None = None) -> dict:
    """Run a digest-pinned lifecycle package and create a new immutable result package."""
    if not allow_workflow_execution:
        raise PackageError("workflow execution requires explicit opt-in after reviewing the package")
    source_directory = output.with_name(output.stem + ".source")
    if output.exists() or source_directory.exists():
        raise PackageError("run output package or source directory already exists; attempts are never overwritten")
    staging_parent = next((parent for parent in output.absolute().parents if parent.is_dir()), None)
    if staging_parent is None:
        raise PackageError("run output must have an accessible parent directory for package intake")
    with tempfile.TemporaryDirectory(prefix=".compiled-experiment-intake-", dir=staging_parent) as intake:
        snapshot = Path(intake) / "plan.zip"
        with _open_run_package(package, snapshot, expected_sha256, policy) as opened:
            return _run_verified_package(opened, output, source_directory, expected_sha256)

def _run_verified_package(package: _VerifiedRunPackage, output: Path, source_directory: Path,
                          expected_sha256: str) -> dict:
    closure, runner, limits, tmpfs_root, job_members = _admit(package.admission, package.members)
    attempt_directory = output.with_name(output.stem + ".attempt")
    if attempt_directory.exists():
        raise PackageError("run attempt directory already exists; attempts are never overwritten")
    created = datetime.now(timezone.utc)
    started = time.monotonic()
    execution = {"returnCode": None, "timedOut": False, "stdout": b"", "stderr": b"",
                 "outputs": {}, "provenance": {}, "collectionErrors": [],
                 "workerObservation": limits["workerObservation"]}
    timing = {"created": created, "ended": created, "elapsed": 0}
    initial, run_id, _ = _attempt_evidence(
        package, expected_sha256, closure, runner, execution, timing,
    )
    receipt_key = f"evidence/attempts/{run_id}/runner-receipt.json"
    receipt = json_value(initial[receipt_key])
    receipt.update(status="started", endedAt=None, wallSeconds=None,
                   executionPhase="preflight", executionNeverStarted=False,
                   launchIntentPersisted=False)
    attempt_directory.mkdir(parents=True)
    attempt_directory.chmod(0o700)
    (attempt_directory / "runner-home").mkdir(mode=0o700)
    (attempt_directory / "runner-home").chmod(0o700)
    _persist_attempt_receipt(attempt_directory, receipt)
    try:
        journal = _new_container_journal(attempt_directory, run_id, runner)
    except (PackageError, OSError, subprocess.SubprocessError) as exc:
        receipt.update(status="failed", endedAt=datetime.now(timezone.utc).isoformat(),
                       executionPhase="not-started", executionNeverStarted=True,
                       launchIntentPersisted=False,
                       containerCleanup={"state": "not-started", "engine": _container_engine(runner),
                                        "containerCount": 0, "absentCount": 0, "allAbsent": False},
                       collectionErrors=[f"Attempt preflight failed before workflow launch: {type(exc).__name__}: {str(exc)[:1024]}"])
        _persist_attempt_receipt(attempt_directory, receipt)
        raise
    journal["launchIntentPersisted"] = True
    receipt.update(executionPhase="launch-intent", launchIntentPersisted=True)
    _journal_receipt_reference(attempt_directory, journal, receipt)
    return _run_retained_attempt(_AttemptExecution(
        output=output, source_directory=source_directory, attempt_directory=attempt_directory,
        package=package, job_members=job_members,
        expected_sha256=expected_sha256, closure=closure, runner=runner, limits=limits,
        tmpfs_root=tmpfs_root, journal=journal, receipt=receipt, receipt_key=receipt_key,
        created=created, started=started,
    ))
