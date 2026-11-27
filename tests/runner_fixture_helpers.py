"""Shared fake runner evidence for lifecycle tests; never contacts an engine."""

import os
from contextlib import contextmanager
from fractions import Fraction
from unittest.mock import patch

from experiment_compiler.runner import _persist_attempt_receipt, _write_container_journal
from experiment_compiler.worker_limits import ObservedWorker


RECOVERY_IDENTITY = {
    "attempt_id": "0123456789ab",
    "image_ref": "registry.example/tool@sha256:" + "d" * 64,
    "image_id": "sha256:" + "a" * 64,
    "reported_image_id": "sha256:" + "e" * 64,
    "controller": {"pid": -1, "bootId": None, "processStartTicks": None},
}


def synthetic_worker_observation(_approval):
    return ObservedWorker(Fraction(1), 64 * 1024 * 1024, 128), {"observationSource": "synthetic fixture"}


@contextmanager
def synthetic_controller_environment(directory):
    with patch.dict(os.environ, {
            "EXPERIMENT_RUNNER_SANDBOX": "native test fixture",
            "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
            "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
            "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
    }), patch("experiment_compiler.runner.importlib.metadata.version", return_value="3.2.20260720092025"), \
            patch("experiment_compiler.runner.observe_worker", side_effect=synthetic_worker_observation):
        yield


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


def prepare_recovery_attempt(attempt_directory, identity, *, before_launch=False):
    """Build a private retained attempt fixture without contacting a container engine."""
    from datetime import datetime, timezone

    attempt_id = identity["attempt_id"]
    image_ref = identity["image_ref"]
    image_id = identity["image_id"]
    reported_image_id = identity["reported_image_id"]
    controller = identity["controller"]
    cwl_boot_id = identity.get("cwl_boot_id")
    container_id = identity.get("container_id")
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
        "state": "prepared" if before_launch else "running", "launchIntentPersisted": not before_launch,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cidfilePrefix": f"{attempt_id}-", "containers": [], "cancellation": None,
    }
    if cwl_boot_id is not None:
        journal["cwlProcessIdentity"] = {
            "pid": 2345, "bootId": cwl_boot_id, "processStartTicks": "20",
            "processGroupId": 2345, "sessionId": 2345,
        }
    journal_digest = _write_container_journal(attempt_directory, journal)
    receipt = {
        "schemaVersion": 1, "attemptId": attempt_id, "status": "started",
        "planPackageSha256": "f" * 64,
        "runner": {"name": "cwltool", "version": "3.2.20260720092025",
                   "workerImageBase": image_ref},
        "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
        "workerContext": {"reportedImageId": reported_image_id}, "collectionErrors": [],
        "containerJournal": {"path": "container-journal.json", "sha256": journal_digest},
    }
    _persist_attempt_receipt(attempt_directory, receipt)
    return journal
