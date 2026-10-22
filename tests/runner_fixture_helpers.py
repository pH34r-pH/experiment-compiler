"""Shared fake runner evidence for lifecycle tests; never contacts an engine."""

from experiment_compiler.core import canonical
from experiment_compiler.runner import _persist_attempt_receipt, sha256_bytes


def prepare_container_journal(attempt_directory, attempt_id, config):
    (attempt_directory / "engine-cids").mkdir(mode=0o700)
    engine = "podman" if config["executionMode"] == "cwltool-podman" else "docker"
    return {
        "schema": "cwltool-container-journal/v1", "attemptId": attempt_id,
        "engine": engine, "enginePath": "/usr/bin/" + engine,
        "workerImageRef": config.get("workerImageBase"),
        "workerImageId": "sha256:" + "a" * 64,
        "reportedWorkerImageId": "sha256:fixture",
        "controller": {"pid": -1, "bootId": None, "processStartTicks": None},
        "state": "prepared", "createdAt": "2026-01-01T00:00:00+00:00",
        "updatedAt": "2026-01-01T00:00:00+00:00",
        "cidfilePrefix": attempt_id + "-", "containers": [], "cancellation": None,
    }


def prepare_recovery_attempt(attempt_directory, *, attempt_id, image_ref, image_id,
                             reported_image_id, controller, cwl_boot_id=None, container_id=None):
    """Build a private retained attempt fixture without contacting a container engine."""
    from datetime import datetime, timezone

    attempt_directory.mkdir(mode=0o700)
    (attempt_directory / "engine-cids").mkdir(mode=0o700)
    (attempt_directory / "runner-home").mkdir(mode=0o700)
    if container_id is not None:
        (attempt_directory / "engine-cids" / f"{attempt_id}--fixture.cid").write_text(container_id + "\n")
    journal = {
        "schema": "cwltool-container-journal/v1", "attemptId": attempt_id,
        "engine": "podman", "enginePath": "/usr/bin/podman",
        "workerImageRef": image_ref, "workerImageId": image_id,
        "reportedWorkerImageId": reported_image_id, "controller": controller,
        "state": "running", "launchIntentPersisted": True,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cidfilePrefix": f"{attempt_id}-", "containers": [], "cancellation": None,
    }
    if cwl_boot_id is not None:
        journal["cwlProcessIdentity"] = {
            "pid": 2345, "bootId": cwl_boot_id, "processStartTicks": "20",
            "processGroupId": 2345, "sessionId": 2345,
        }
    journal_bytes = canonical(journal)
    (attempt_directory / "container-journal.json").write_bytes(journal_bytes)
    receipt = {
        "schemaVersion": 1, "attemptId": attempt_id, "status": "started",
        "planPackageSha256": "f" * 64,
        "runner": {"name": "cwltool", "version": "3.2.20260720092025",
                   "workerImageBase": image_ref},
        "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
        "workerContext": {"reportedImageId": reported_image_id}, "collectionErrors": [],
        "containerJournal": {"path": "container-journal.json", "sha256": sha256_bytes(journal_bytes)},
    }
    _persist_attempt_receipt(attempt_directory, receipt)
    return journal
