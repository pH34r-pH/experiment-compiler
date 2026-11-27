"""Persist and validate retained CWL attempt receipts and container journals."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import runner_admission
from .core import PackageError, bounded_read, canonical, json_value


_CONTAINER_JOURNAL_SCHEMA = "cwltool-container-journal/v1"
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ATTEMPT_CONTAINERS = 64
_RECOVERY_RESULT_NOTE = "Attempt recovery ran without re-executing science; completion is not established"
_CWL_EXECUTION_DESCRIPTIONS = {
    "podman": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
    "docker": "cwltool orchestrator on worker; CWL CommandLineTool uses Docker runtime",
}


class _AttemptStatus(StrEnum):
    STARTED = "started"
    FAILED = "failed"
    TIMED_OUT = "timed-out"
    SUCCEEDED = "succeeded"


class _ProcessPhase(StrEnum):
    PREFLIGHT = "preflight"
    NOT_STARTED = "not-started"
    LAUNCH_INTENT = "launch-intent"
    RUNNING = "running"


class _CleanupState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation-requested"
    CLEANUP_FAILED = "cleanup-failed"
    CLEAN = "clean"
    UNKNOWN = "unknown"
    NOT_STARTED = "not-started"


class _ContainerCleanupPhase(StrEnum):
    PENDING = "pending"
    STOPPING = "stopping"
    REMOVING = "removing"
    ABSENT = "absent"
    FAILED = "cleanup-failed"


_RECOVERY_STATUS = {
    _AttemptStatus.STARTED: _AttemptStatus.FAILED,
    _AttemptStatus.FAILED: _AttemptStatus.FAILED,
    _AttemptStatus.TIMED_OUT: _AttemptStatus.TIMED_OUT,
}


_PROCESS_TRANSITIONS = {
    _ProcessPhase.PREFLIGHT: {_ProcessPhase.NOT_STARTED, _ProcessPhase.LAUNCH_INTENT},
    _ProcessPhase.LAUNCH_INTENT: {_ProcessPhase.RUNNING},
}


_SETTLEMENT_STATES = {_CleanupState.UNKNOWN, _CleanupState.CLEANUP_FAILED, _CleanupState.CLEAN}


_CLEANUP_TRANSITIONS = {
    _CleanupState.PREPARED: _SETTLEMENT_STATES | {_CleanupState.RUNNING, _CleanupState.CANCELLATION_REQUESTED},
    _CleanupState.RUNNING: _SETTLEMENT_STATES | {_CleanupState.CANCELLATION_REQUESTED},
    _CleanupState.CANCELLATION_REQUESTED: _SETTLEMENT_STATES,
    _CleanupState.UNKNOWN: _SETTLEMENT_STATES | {_CleanupState.CANCELLATION_REQUESTED},
    _CleanupState.CLEANUP_FAILED: _SETTLEMENT_STATES,
    _CleanupState.CLEAN: _SETTLEMENT_STATES,
}


def _transition_process(receipt: dict, phase: _ProcessPhase) -> None:
    previous = receipt.get("executionPhase")
    if phase not in _PROCESS_TRANSITIONS.get(previous, ()):
        raise PackageError(f"Invalid runner process phase transition: {previous!r} to {phase.value}")
    receipt.update(executionPhase=phase, executionNeverStarted=phase == _ProcessPhase.NOT_STARTED,
                   launchIntentPersisted=phase in {_ProcessPhase.LAUNCH_INTENT, _ProcessPhase.RUNNING})


def _finish_attempt(receipt: dict, status: _AttemptStatus) -> None:
    if receipt.get("status") != _AttemptStatus.STARTED or status not in {
        _AttemptStatus.FAILED, _AttemptStatus.TIMED_OUT, _AttemptStatus.SUCCEEDED,
    }:
        raise PackageError("A terminal attempt outcome requires a started receipt")
    receipt["status"] = status


def _validate_cleanup_transition(previous: str, state: _CleanupState) -> None:
    if previous not in _CLEANUP_TRANSITIONS:
        raise PackageError(f"Invalid runner cleanup state: {previous!r}")
    if state != previous and state not in _CLEANUP_TRANSITIONS[previous]:
        raise PackageError(f"Invalid runner cleanup transition: {previous!r} to {state.value}")


def _transition_container_cleanup(cleanup: dict, phase: _ContainerCleanupPhase, **details) -> None:
    previous = cleanup.get("status")
    if (previous not in tuple(_ContainerCleanupPhase)
            or previous == _ContainerCleanupPhase.ABSENT or phase == _ContainerCleanupPhase.PENDING):
        raise PackageError(f"Invalid container cleanup transition: {previous!r} to {phase.value}")
    cleanup.update(details, status=phase, absentAfterCleanup=phase == _ContainerCleanupPhase.ABSENT)


def _record_first_cancellation(attempt_directory: Path, journal: dict, receipt: dict) -> None:
    if journal["cancellation"] is None:
        _journal_update(attempt_directory, journal, receipt, state=_CleanupState.CANCELLATION_REQUESTED,
                        cancellation={"requestedAt": datetime.now(timezone.utc).isoformat()})


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


def _journal_receipt_reference(attempt_directory: Path, journal: dict, receipt: dict) -> str:
    encoded = canonical(journal)
    digest = sha256_bytes(encoded)
    previous = receipt.get("containerJournal")
    previous_digest = previous.get("sha256") if isinstance(previous, dict) else None
    marker = {
        "schema": "cwltool-container-journal-transaction/v1",
        "attemptId": journal.get("attemptId"),
        "fromSha256": previous_digest,
        "toSha256": digest,
    }
    marker_path = attempt_directory / "container-journal.pending"
    marker_tmp = attempt_directory / "container-journal.pending.tmp"
    fd = os.open(marker_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(marker))
        stream.flush()
        os.fsync(stream.fileno())
    marker_tmp.replace(marker_path)
    _fsync_directory(attempt_directory)
    digest = _write_container_journal(attempt_directory, journal)
    receipt["containerJournal"] = {"path": "container-journal.json", "sha256": digest}
    _persist_attempt_receipt(attempt_directory, receipt)
    marker_path.unlink(missing_ok=True)
    _fsync_directory(attempt_directory)
    return digest


def _retain_journal_snapshot(attempt_directory: Path, digest: str) -> str:
    """Keep an immutable private copy for any result receipt that cites this digest."""
    current = attempt_directory / "container-journal.json"
    if current.is_symlink() or not current.is_file():
        raise PackageError("Container journal is not a regular private sidecar")
    data = bounded_read(current, 1024 * 1024)
    if sha256_bytes(data) != digest:
        raise PackageError("Container journal changed before its digest snapshot could be retained")
    history = attempt_directory / "container-journal-history"
    history.mkdir(mode=0o700, exist_ok=True)
    if (history.is_symlink() or not history.is_dir() or
            (os.name == "posix" and history.stat().st_mode & 0o077) or
            (hasattr(os, "getuid") and history.stat().st_uid != os.getuid())):
        raise PackageError("Container journal history directory is unsafe")
    snapshot = history / f"{digest}.json"
    if not os.path.lexists(snapshot):
        _publish_journal_snapshot(history, snapshot, data)
    _validate_private_file(snapshot, "Existing container journal history snapshot")
    if sha256_bytes(bounded_read(snapshot, 1024 * 1024)) != digest:
        raise PackageError("Existing container journal history digest does not match its filename")
    _fsync_directory(history)
    _fsync_directory(attempt_directory)
    return f"container-journal-history/{digest}.json"


def _publish_journal_snapshot(history: Path, snapshot: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(prefix=".journal-", dir=history, delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            try:
                os.link(staged, snapshot)
            except FileExistsError:
                # The caller validates the snapshot that won the race.
                pass
        finally:
            stream.close()
            staged.unlink(missing_ok=True)


def _container_cleanup_summary(journal: dict) -> dict:
    containers = journal.get("containers", [])
    state = journal.get("state")
    if not isinstance(state, str) or state not in tuple(_CleanupState):
        state = _CleanupState.UNKNOWN
    engine = journal.get("engine")
    entries = containers if isinstance(containers, list) else []
    absent = sum(1 for entry in entries if isinstance(entry, dict)
                 and isinstance(entry.get("cleanup"), dict) and entry["cleanup"].get("absentAfterCleanup") is True)
    return {
        "engine": engine if engine in ("docker", "podman") else None,
        "state": state,
        "containerCount": len(entries),
        "absentCount": absent,
        "allAbsent": (state == _CleanupState.CLEAN and
                      isinstance(containers, list) and absent == len(entries)),
    }


def _journal_update(attempt_directory: Path, journal: dict, receipt: dict, **changes) -> None:
    if "state" in changes:
        _validate_cleanup_transition(journal.get("state"), changes["state"])
    journal.update(changes)
    journal["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _journal_receipt_reference(attempt_directory, journal, receipt)


def _validate_current_journal_marker(marker: Any, attempt_id: str, journal_digest: str) -> None:
    marker_from = marker.get("fromSha256") if isinstance(marker, dict) else None
    marker_to = marker.get("toSha256") if isinstance(marker, dict) else None
    valid = (
        isinstance(marker, dict) and
        marker.get("schema") == "cwltool-container-journal-transaction/v1" and
        marker.get("attemptId") == attempt_id and
        isinstance(marker_to, str) and re.fullmatch(r"[0-9a-f]{64}", marker_to) and
        (marker_to == journal_digest or marker_from == journal_digest)
    )
    if not valid:
        raise PackageError("Journal transaction marker does not match the retained journal")


def _validate_recovery_journal_identity(receipt: dict, journal: dict) -> str:
    attempt_id = receipt.get("attemptId")
    if (journal.get("schema") != _CONTAINER_JOURNAL_SCHEMA or
            not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{12}", attempt_id) or
            journal.get("attemptId") != attempt_id or journal.get("cidfilePrefix") != f"{attempt_id}-"):
        raise PackageError("Container journal belongs to a different attempt")
    return attempt_id


def _validated_recovery_marker(attempt_directory: Path, receipt: dict, attempt_id: str,
                               journal_digest: str) -> Path | None:
    reference = receipt.get("containerJournal")
    if (not isinstance(reference, dict) or reference.get("path") != "container-journal.json" or
            not isinstance(reference.get("sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])):
        raise PackageError("Container journal is not bound by the retained runner receipt")
    marker_path = attempt_directory / "container-journal.pending"
    marker = None
    if os.path.lexists(marker_path):
        _validate_private_file(marker_path, "Container journal transaction marker")
        marker = json_value(bounded_read(marker_path, 4096))
        if not isinstance(marker, dict):
            raise PackageError("Container journal transaction marker is not a JSON object")
    if journal_digest != reference["sha256"]:
        if (not isinstance(marker, dict) or
                marker.get("schema") != "cwltool-container-journal-transaction/v1" or
                marker.get("attemptId") != attempt_id or
                marker.get("fromSha256") != reference["sha256"] or marker.get("toSha256") != journal_digest):
            raise PackageError("Journal/receipt mismatch has no matching durable update marker")
    elif marker is not None:
        _validate_current_journal_marker(marker, attempt_id, journal_digest)
    return marker_path if marker is not None else None


def _settle_recovery_marker(attempt_directory: Path, receipt: dict, journal_digest: str,
                            marker: Path | None) -> None:
    if marker is None:
        return
    if receipt["containerJournal"]["sha256"] != journal_digest:
        receipt["containerJournal"] = {"path": "container-journal.json", "sha256": journal_digest}
        _persist_attempt_receipt(attempt_directory, receipt)
    marker.unlink()
    _fsync_directory(attempt_directory)


def _load_recovery_state(attempt_directory: Path) -> tuple[dict, dict, str, Path | None]:
    """Read the private receipt, journal and pending binding without modifying them."""
    if attempt_directory.is_symlink() or not attempt_directory.is_dir():
        raise PackageError("Recovery requires an existing regular attempt directory")
    if hasattr(os, "getuid") and attempt_directory.stat().st_uid != os.getuid():
        raise PackageError("Recovery attempt directory must be owned by the current runner user")
    if attempt_directory.stat().st_mode & 0o077:
        raise PackageError("Recovery attempt directory must not be accessible to group or other users")
    receipt_path = attempt_directory / "runner-receipt.json"
    journal_path = attempt_directory / "container-journal.json"
    _validate_private_file(receipt_path, "Recovery receipt")
    _validate_private_file(journal_path, "Recovery journal")
    for name in ("container-journal.json.tmp", "container-journal.pending.tmp"):
        staging = attempt_directory / name
        if os.path.lexists(staging):
            _validate_private_file(staging, "Recovery journal staging file")
    _validate_receipt_staging_files(attempt_directory)
    receipt_bytes = bounded_read(receipt_path, 1024 * 1024)
    journal_bytes = bounded_read(journal_path, 1024 * 1024)
    receipt = json_value(receipt_bytes)
    journal = json_value(journal_bytes)
    if not isinstance(receipt, dict) or not isinstance(journal, dict):
        raise PackageError("Recovery receipt and journal must be JSON objects")
    attempt_id = _validate_recovery_journal_identity(receipt, journal)
    journal_digest = sha256_bytes(journal_bytes)
    marker = _validated_recovery_marker(attempt_directory, receipt, attempt_id, journal_digest)
    return receipt, journal, journal_digest, marker


def _receipt_engine(receipt: dict) -> str | None:
    return next((engine for engine, description in _CWL_EXECUTION_DESCRIPTIONS.items()
                 if receipt.get("executionMode") == description), None)


def _preflight_receipt_engine(receipt: dict) -> str:
    engine = _receipt_engine(receipt)
    if (engine is None or
            not isinstance(receipt.get("attemptId"), str) or
            not re.fullmatch(r"[0-9a-f]{12}", receipt["attemptId"]) or
            not isinstance(receipt.get("planPackageSha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", receipt["planPackageSha256"]) or
            not isinstance(receipt.get("runner"), dict) or receipt["runner"].get("name") != "cwltool"):
        raise PackageError("Retained receipt does not prove a typed preflight no-start outcome")
    return engine


def _validate_not_started_cleanup(receipt: dict, engine: str) -> None:
    cleanup = receipt.get("containerCleanup")
    expected = {"state": _CleanupState.NOT_STARTED, "engine": engine, "containerCount": 0,
                "absentCount": 0, "allAbsent": False}
    if (receipt.get("status") != _AttemptStatus.FAILED or
            receipt.get("executionPhase") != _ProcessPhase.NOT_STARTED or
            receipt.get("executionNeverStarted") is not True or
            receipt.get("launchIntentPersisted") is not False or cleanup != expected):
        raise PackageError("Retained receipt does not prove a typed preflight no-start outcome")


def _validate_not_started_receipt(receipt: Any) -> None:
    if not isinstance(receipt, dict):
        raise PackageError("Retained receipt does not prove a typed preflight no-start outcome")
    engine = _preflight_receipt_engine(receipt)
    _validate_not_started_cleanup(receipt, engine)


def _require_empty_preflight_cid_directory(attempt_directory: Path) -> None:
    cid_directory = attempt_directory / "engine-cids"
    if (cid_directory.is_symlink() or not cid_directory.is_dir() or cid_directory.stat().st_mode & 0o077
            or (hasattr(os, "getuid") and cid_directory.stat().st_uid != os.getuid())
            or any(cid_directory.iterdir())):
        raise PackageError("Preflight CID directory is unsafe or contains possible container evidence")


def _load_not_started_receipt(attempt_directory: Path) -> dict:
    """Accept only the durable typed preflight outcome, never missing state alone."""
    if (attempt_directory.is_symlink() or not attempt_directory.is_dir() or
            (hasattr(os, "getuid") and attempt_directory.stat().st_uid != os.getuid()) or
            attempt_directory.stat().st_mode & 0o077):
        raise PackageError("Recovery requires an owned private attempt directory")
    receipt_path = attempt_directory / "runner-receipt.json"
    _validate_private_file(receipt_path, "Preflight recovery receipt")
    if any(os.path.lexists(attempt_directory / name) for name in
           ("container-journal.json", "container-journal.pending",
            "container-journal.json.tmp", "container-journal.pending.tmp")):
        raise PackageError("Preflight no-start receipt conflicts with container journal state")
    _validate_receipt_staging_files(attempt_directory)
    receipt = json_value(bounded_read(receipt_path, 1024 * 1024))
    _validate_not_started_receipt(receipt)
    _require_empty_preflight_cid_directory(attempt_directory)
    receipt["containerJournal"] = {
        "availability": "not-applicable-not-started", "storePath": None, "sha256": None,
    }
    return receipt


def _validate_recovery_directories(attempt_directory: Path) -> None:
    for protected_name in ("engine-cids", "runner-home"):
        protected = attempt_directory / protected_name
        if (protected.is_symlink() or not protected.is_dir() or protected.stat().st_mode & 0o077 or
                (hasattr(os, "getuid") and protected.stat().st_uid != os.getuid())):
            raise PackageError(f"Protected {protected_name} directory is absent or unsafe; refusing recovery")


def _validate_recovery_container_inventory(attempt_directory: Path, journal: dict) -> None:
    containers = journal.get("containers")
    if not isinstance(containers, list) or len(containers) > _MAX_ATTEMPT_CONTAINERS:
        raise PackageError("Recovery journal has an invalid bounded container list")
    state = journal.get("state")
    if not isinstance(state, str) or state not in tuple(_CleanupState) or state == _CleanupState.NOT_STARTED:
        raise PackageError("Recovery journal has an invalid cleanup state")
    for entry in containers:
        _validate_recovery_container(entry, journal)
    _validate_recovery_directories(attempt_directory)


def _validate_recovery_container(entry: Any, journal: dict) -> None:
    cidfile = entry.get("cidfile") if isinstance(entry, dict) else None
    if (not isinstance(entry, dict) or entry.get("engine") != journal["engine"] or
            not isinstance(entry.get("id"), str) or not _CONTAINER_ID.fullmatch(entry["id"]) or
            entry.get("imageId") != journal["workerImageId"] or
            entry.get("imageRef") != journal["workerImageRef"] or
            not isinstance(cidfile, str) or not re.fullmatch(r"engine-cids/[A-Za-z0-9_.-]+\.cid", cidfile) or
            not Path(cidfile).name.startswith(journal["cidfilePrefix"])):
        raise PackageError("Recovery journal contains an unknown, malformed, or cross-attempt container ID")
    cleanup = entry.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("status") not in tuple(_ContainerCleanupPhase):
        raise PackageError("Recovery journal has an invalid container cleanup record")
    if cleanup.get("absentAfterCleanup") is not (cleanup["status"] == _ContainerCleanupPhase.ABSENT):
        raise PackageError("Recovery journal has inconsistent container absence evidence")


def _validate_recovery_receipt_worker(receipt: dict, journal: dict) -> None:
    status = receipt.get("status")
    if not isinstance(status, str) or status not in _RECOVERY_STATUS:
        raise PackageError("Only incomplete, failed, or timed-out attempts can be recovered")
    plan_digest = receipt.get("planPackageSha256")
    runner = receipt.get("runner")
    worker = receipt.get("workerContext")
    if (not isinstance(plan_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_digest) or
            not isinstance(runner, dict) or runner.get("name") != "cwltool" or
            not isinstance(worker, dict) or not isinstance(worker.get("reportedImageId"), str) or
            worker.get("reportedImageId") in {"", "unknown"} or
            worker.get("reportedImageId") != journal.get("reportedWorkerImageId")):
        raise PackageError("Recovery journal does not match the retained plan and worker identity")


def _validate_recovery_engine(receipt: dict, journal: dict) -> None:
    expected_engine = _receipt_engine(receipt)
    if (expected_engine is None or journal.get("engine") != expected_engine or
            journal.get("workerImageRef") != receipt["runner"].get("workerImageBase") or
            not isinstance(journal.get("workerImageId"), str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", journal["workerImageId"])):
        raise PackageError("Recovery engine or pinned image identity differs from the retained receipt")


@contextmanager
def _exclusive_recovery(attempt_directory: Path):
    if os.name != "posix":
        yield
        return
    import fcntl

    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(attempt_directory, flags)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PackageError("Another recovery is active for this attempt") from exc
        yield
    finally:
        os.close(descriptor)


def _validate_recovery_process_evidence(receipt: dict) -> None:
    phase = receipt.get("executionPhase")
    if phase is not None and phase not in tuple(_ProcessPhase):
        raise PackageError("Retained receipt has an unknown process phase")
    if phase == _ProcessPhase.NOT_STARTED or receipt.get("executionNeverStarted") is True:
        raise PackageError("Retained preflight no-start receipt conflicts with the journal")
    expectation = {
        _ProcessPhase.PREFLIGHT: False,
        _ProcessPhase.LAUNCH_INTENT: True,
        _ProcessPhase.RUNNING: True,
    }
    if phase in expectation and receipt.get("launchIntentPersisted") is not expectation[phase]:
        raise PackageError("Retained process phase disagrees with the launch intent flag")


def _possible_workflow_launch(receipt: dict, journal: dict) -> bool:
    # Older receipts omit the launch flags. A running or unsettled journal is
    # still evidence of a possible launch, even when no container was recorded.
    return (
        journal.get("state") in {
            _CleanupState.RUNNING, _CleanupState.CANCELLATION_REQUESTED,
            _CleanupState.UNKNOWN, _CleanupState.CLEANUP_FAILED,
        }
        or receipt.get("launchIntentPersisted") is True
        or journal.get("launchIntentPersisted") is True
        or any(journal.get(key) is not None for key in ("cwlProcessIdentity", "cwlProcessExit", "processStartedAt"))
    )


def _record_recovery_result(attempt_directory: Path, receipt: dict, journal: dict,
                            errors: list[str]) -> dict:
    prior_errors = receipt.get("collectionErrors", [])
    if not isinstance(prior_errors, list):
        prior_errors = []
    new_errors = [error for error in dict.fromkeys([_RECOVERY_RESULT_NOTE, *errors]) if error not in prior_errors]
    cleanup = _container_cleanup_summary(journal)
    cleanup.pop("engine")  # Existing recovery receipts record the engine in the journal.
    recovered = {
        **receipt,
        "status": _RECOVERY_STATUS[receipt["status"]],
        "endedAt": receipt.get("endedAt") or datetime.now(timezone.utc).isoformat(),
        "collectionErrors": prior_errors + new_errors,
        "containerCleanup": cleanup,
    }
    _retain_journal_snapshot(attempt_directory, recovered["containerJournal"]["sha256"])
    if recovered != receipt:
        _persist_attempt_receipt(attempt_directory, recovered)
    return recovered


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
        "executionMode": _CWL_EXECUTION_DESCRIPTIONS[runner_admission._container_engine(runner)],
        "workerContext": {
            "reportedSandbox": os.environ.get("EXPERIMENT_RUNNER_SANDBOX", "unspecified by caller"),
            "reportedImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
            "attestation": "caller-reported; not independently verified by the adapter",
            "outerWorker": execution.get("workerObservation", {"status": "unverified"}),
        },
        "status": (_AttemptStatus.TIMED_OUT if execution["timedOut"] else
                   _AttemptStatus.FAILED if execution["returnCode"] != 0 or execution["collectionErrors"]
                   else _AttemptStatus.SUCCEEDED),
        "collectionErrors": execution["collectionErrors"],
        "logCapture": execution.get("logCapture"),
        "containerJournal": container_journal,
        "containerCleanup": execution.get("containerCleanup", {"state": "unknown", "allAbsent": False}),
        "scratchStorage": execution.get("scratchStorage", {"enforcement": "unverified"}),
        "outputStorage": execution.get("outputStorage", {"enforcement": "worker-tmpfs-only", "hardCap": False}),
    }


def _attempt_evidence(package: runner_admission._VerifiedRunPackage, expected_sha256: str,
                      closure: dict, runner: dict, execution: dict,
                      timing: dict) -> tuple[dict[str, runner_admission._MemberSource], str, list[str]]:
    created = timing["created"]
    ended = timing["ended"]
    elapsed = timing["elapsed"]
    run_id = hashlib.sha256((expected_sha256 + created.isoformat()).encode()).hexdigest()[:12]
    evidence_root = f"evidence/attempts/{run_id}"
    augmented: dict[str, runner_admission._MemberSource] = {**package.members, **package.admission}
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


def _validate_private_file(path: Path, description: str) -> os.stat_result:
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
        info = _validate_private_file(path, "Retained attempt receipt staging file")
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
        _validate_private_file(target, "Existing attempt receipt")
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
    _validate_private_file(pending, "Attempt receipt staging file")
    pending.replace(target)
    _fsync_directory(attempt_directory)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
