"""Shared fake runner evidence for lifecycle tests; never contacts an engine."""


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
