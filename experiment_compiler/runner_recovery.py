"""Crash and interruption recovery for retained CWL runner attempts."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import PackageError, bounded_read, json_value
from .runner_admission import sha256_bytes
from . import runner_journal
from .runner_journal import (
    _CONTAINER_ID,
    _CONTAINER_JOURNAL_SCHEMA,
    _MAX_ATTEMPT_CONTAINERS,
    _cleanup_container_journal,
    _controller_is_alive,
    _journal_receipt_reference,
    _journal_update,
    _normalize_engine_image_id,
    _retain_journal_snapshot,
)
from .runner_process import _reconcile_cwl_process_group
from .runner_receipts import (
    _fsync_directory,
    _persist_attempt_receipt,
    _validate_private_receipt_file,
    _validate_receipt_staging_files,
)

_MAX_RECOVERY_AGE_SECONDS = 7 * 24 * 60 * 60


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

def _adopt_pending_journal_update(attempt_directory: Path, receipt: dict, attempt_id: str,
                                  journal_digest: str) -> None:
    reference = receipt.get("containerJournal")
    if (not isinstance(reference, dict) or reference.get("path") != "container-journal.json" or
            not isinstance(reference.get("sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])):
        raise PackageError("Container journal is not bound by the retained runner receipt")
    marker_path = attempt_directory / "container-journal.pending"
    if marker_path.is_symlink():
        raise PackageError("Container journal transaction marker is not a regular owned file")
    marker = json_value(bounded_read(marker_path, 4096)) if marker_path.exists() else None
    if journal_digest != reference["sha256"]:
        if (not isinstance(marker, dict) or
                marker.get("schema") != "cwltool-container-journal-transaction/v1" or
                marker.get("attemptId") != attempt_id or
                marker.get("fromSha256") != reference["sha256"] or marker.get("toSha256") != journal_digest):
            raise PackageError("Journal/receipt mismatch has no matching durable update marker")
        receipt["containerJournal"] = {"path": "container-journal.json", "sha256": journal_digest}
        _persist_attempt_receipt(attempt_directory, receipt)
    elif marker is not None:
        _validate_current_journal_marker(marker, attempt_id, journal_digest)
    if marker_path.exists():
        marker_path.unlink()
        _fsync_directory(attempt_directory)

def _adopt_recovery_journal(attempt_directory: Path, receipt: dict, journal: dict,
                            journal_bytes: bytes) -> str:
    """Bind a pending journal update to its receipt and return the current digest."""
    attempt_id = _validate_recovery_journal_identity(receipt, journal)
    journal_digest = sha256_bytes(journal_bytes)
    _adopt_pending_journal_update(attempt_directory, receipt, attempt_id, journal_digest)
    return journal_digest

def _load_recovery_state(attempt_directory: Path) -> tuple[dict, dict, str]:
    """Read and bind a private attempt receipt and journal."""
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
    _validate_private_receipt_file(receipt_path, "Recovery receipt")
    _validate_receipt_staging_files(attempt_directory)
    receipt_bytes = bounded_read(receipt_path, 1024 * 1024)
    journal_bytes = bounded_read(journal_path, 1024 * 1024)
    receipt = json_value(receipt_bytes)
    journal = json_value(journal_bytes)
    if not isinstance(receipt, dict) or not isinstance(journal, dict):
        raise PackageError("Recovery receipt and journal must be JSON objects")
    journal_digest = _adopt_recovery_journal(attempt_directory, receipt, journal, journal_bytes)
    return receipt, journal, journal_digest

def _preflight_receipt_engine(receipt: dict) -> str:
    expected_modes = {
        "podman": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
        "docker": "cwltool orchestrator on worker; CWL CommandLineTool uses Docker runtime",
    }
    engine = "podman" if receipt.get("executionMode") == expected_modes["podman"] else "docker"
    if (receipt.get("executionMode") != expected_modes[engine] or
            not isinstance(receipt.get("attemptId"), str) or
            not re.fullmatch(r"[0-9a-f]{12}", receipt["attemptId"]) or
            not isinstance(receipt.get("planPackageSha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", receipt["planPackageSha256"]) or
            not isinstance(receipt.get("runner"), dict) or receipt["runner"].get("name") != "cwltool"):
        raise PackageError("Retained receipt does not prove a typed preflight no-start outcome")
    return engine

def _validate_not_started_cleanup(receipt: dict, engine: str) -> None:
    cleanup = receipt.get("containerCleanup")
    expected = {"state": "not-started", "engine": engine, "containerCount": 0,
                "absentCount": 0, "allAbsent": False}
    if (receipt.get("status") != "failed" or receipt.get("executionPhase") != "not-started" or
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
    _validate_private_receipt_file(receipt_path, "Preflight recovery receipt")
    if any((attempt_directory / name).exists() for name in
           ("container-journal.json", "container-journal.pending", "container-journal.json.tmp")):
        raise PackageError("Preflight no-start receipt conflicts with container journal state")
    _validate_receipt_staging_files(attempt_directory)
    receipt = json_value(bounded_read(receipt_path, 1024 * 1024))
    _validate_not_started_receipt(receipt)
    _require_empty_preflight_cid_directory(attempt_directory)
    receipt["containerJournal"] = {
        "availability": "not-applicable-not-started", "storePath": None, "sha256": None,
    }
    return receipt

def _validate_recovery_age_and_controller(journal: dict) -> None:
    try:
        created = datetime.fromisoformat(journal["createdAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("Recovery journal has no valid creation time") from exc
    age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if age < -300 or age > _MAX_RECOVERY_AGE_SECONDS:
        raise PackageError("Recovery journal is stale or has a future timestamp")
    _controller_is_alive(journal.get("controller"))

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
    for entry in containers:
        cidfile = entry.get("cidfile") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or entry.get("engine") != journal["engine"] or
                not isinstance(entry.get("id"), str) or not _CONTAINER_ID.fullmatch(entry["id"]) or
                entry.get("imageId") != journal["workerImageId"] or
                entry.get("imageRef") != journal["workerImageRef"] or
                not isinstance(cidfile, str) or not re.fullmatch(r"engine-cids/[A-Za-z0-9_.-]+\.cid", cidfile) or
                not Path(cidfile).name.startswith(f"{journal['attemptId']}--")):
            raise PackageError("Recovery journal contains an unknown, malformed, or cross-attempt container ID")
    _validate_recovery_directories(attempt_directory)

def _validate_recovery_receipt_worker(receipt: dict, journal: dict) -> None:
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

def _validate_recovery_worker(attempt_directory: Path, receipt: dict, journal: dict) -> None:
    """Require the retained plan identity, worker image, and container inventory to match."""
    _validate_recovery_receipt_worker(receipt, journal)
    _validate_recovery_age_and_controller(journal)
    _validate_recovery_container_inventory(attempt_directory, journal)
    image = runner_journal._engine_call(attempt_directory, journal,
                                        ["image", "inspect", "--format", "{{.Id}}", journal["workerImageRef"]])
    if (image.returncode != 0 or
            _normalize_engine_image_id(image.stdout) != journal["workerImageId"]):
        raise PackageError("Recovery engine no longer resolves the exact retained workflow image ID")

def recover_attempt(attempt_directory: Path) -> dict:
    """Reconcile one retained attempt's exact CWL containers without running science."""
    if not (attempt_directory / "container-journal.json").exists():
        receipt = _load_not_started_receipt(attempt_directory)
        return receipt
    receipt, journal, original_journal_sha = _load_recovery_state(attempt_directory)
    _validate_recovery_worker(attempt_directory, receipt, journal)
    errors: list[str] = []
    # A CID file may have been written by the engine immediately before a hard
    # controller loss, before the controller appended it to the journal.
    # `journal_digest` is the digest of the current sidecar. If recovery
    # completed a pending journal/receipt transaction above, `reference`
    # still points at the old JSON object while the validated journal has
    # already been adopted into the receipt.
    _retain_journal_snapshot(attempt_directory, original_journal_sha)
    try:
        _reconcile_cwl_process_group(journal)
    except (PackageError, OSError) as exc:
        errors.append(f"cwltool process recovery remains unresolved: {type(exc).__name__}: {str(exc)[:512]}")
    cid_directory = attempt_directory / "engine-cids"
    cid_files = list(cid_directory.glob(f"{journal['cidfilePrefix']}*.cid"))
    if errors or (journal.get("launchIntentPersisted") is True and
                  not journal.get("containers") and not cid_files):
        if not errors:
            errors.append("Hard loss after launch intent has no container identity; cleanup remains unknown")
        journal["state"] = "unknown"
        _journal_update(attempt_directory, journal, receipt)
    else:
        _cleanup_container_journal(attempt_directory, journal, receipt, errors)
        if errors:
            journal["state"] = "cleanup-failed"
            _journal_update(attempt_directory, journal, receipt)
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
        "allAbsent": (journal["state"] == "clean" and
                      all(entry["cleanup"].get("absentAfterCleanup") is True
                          for entry in journal["containers"])),
    }
    final_digest = _journal_receipt_reference(attempt_directory, journal, receipt)
    _retain_journal_snapshot(attempt_directory, final_digest)
    return receipt
