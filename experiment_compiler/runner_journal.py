"""Container engine inspection, durable container journal, and cleanup."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import PackageError, bounded_read, canonical
from .runner_admission import sha256_bytes
from .runner_receipts import _fsync_directory, _persist_attempt_receipt

_CONTAINER_JOURNAL_SCHEMA = "cwltool-container-journal/v1"
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_ENGINE_IMAGE_ID = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})\Z")
_MAX_ATTEMPT_CONTAINERS = 64


def _container_engine(runner: dict) -> str:
    mode = runner.get("executionMode")
    if mode == "cwltool-podman":
        return "podman"
    if mode == "cwltool-docker":
        return "docker"
    raise PackageError("unsupported container engine in runner configuration")

def _normalize_engine_image_id(value: str) -> str | None:
    """Canonicalize a complete engine image ID; never accept a short ID."""
    if not isinstance(value, str):
        return None
    match = _ENGINE_IMAGE_ID.fullmatch(value.strip())
    if not match:
        return None
    return f"sha256:{match.group(1).lower()}"

def _engine_image_reference_matches(observed: str, pinned: str) -> bool:
    """Allow Podman to omit a tag while preserving the pinned repository and digest."""
    if not isinstance(observed, str) or not isinstance(pinned, str):
        return False
    digest_pattern = re.compile(r"^(.+)@(sha256:[0-9a-f]{64})$")
    observed_match = digest_pattern.fullmatch(observed)
    pinned_match = digest_pattern.fullmatch(pinned)
    if observed_match is None or pinned_match is None:
        return False
    if observed == pinned:
        return True
    pinned_name, pinned_digest = pinned_match.groups()
    observed_name, observed_digest = observed_match.groups()
    if observed_digest != pinned_digest:
        return False
    # A registry port is before the final slash; only a colon in the final
    # path component can be the optional image tag.
    last_slash = pinned_name.rfind("/")
    tag_separator = pinned_name.rfind(":")
    if tag_separator <= last_slash:
        return False
    tag = pinned_name[tag_separator + 1:]
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        return False
    return observed_name == pinned_name[:tag_separator]

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
    if not history.exists():
        history.mkdir(mode=0o700)
    if (history.is_symlink() or not history.is_dir() or
            (os.name == "posix" and history.stat().st_mode & 0o077) or
            (hasattr(os, "getuid") and history.stat().st_uid != os.getuid())):
        raise PackageError("Container journal history directory is unsafe")
    snapshot = history / f"{digest}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(snapshot, flags, 0o600)
    except FileExistsError:
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PackageError("Existing container journal history snapshot is not a regular file")
        if sha256_bytes(bounded_read(snapshot, 1024 * 1024)) != digest:
            raise PackageError("Existing container journal history digest does not match its filename")
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(history)
    return f"container-journal-history/{digest}.json"

def _container_cleanup_summary(journal: dict) -> dict:
    containers = journal.get("containers", [])
    absent = sum(1 for entry in containers
                 if isinstance(entry, dict) and entry.get("cleanup", {}).get("absentAfterCleanup") is True)
    return {
        "engine": journal.get("engine"),
        "state": journal.get("state", "unknown"),
        "containerCount": len(containers) if isinstance(containers, list) else 0,
        "absentCount": absent,
        "allAbsent": (journal.get("state") == "clean" and
                      isinstance(containers, list) and absent == len(containers)),
    }

def _new_container_journal(attempt_directory: Path, attempt_id: str, runner: dict) -> dict:
    cid_directory = attempt_directory / "engine-cids"
    cid_directory.mkdir(mode=0o700)
    cid_directory.chmod(0o700)
    if cid_directory.is_symlink() or not cid_directory.is_dir():
        raise PackageError("container cidfile directory is not a regular controller-owned directory")
    if hasattr(os, "getuid") and cid_directory.stat().st_uid != os.getuid():
        raise PackageError("container cidfile directory is not owned by the current runner")
    engine = _container_engine(runner)
    engine_path = shutil.which(engine, path="/usr/bin:/bin")
    if not engine_path or not Path(engine_path).is_absolute():
        raise PackageError(f"Configured container engine {engine!r} is not available on the trusted PATH")
    journal = {
        "schema": _CONTAINER_JOURNAL_SCHEMA,
        "attemptId": attempt_id,
        "engine": engine,
        "enginePath": str(Path(engine_path).resolve(strict=True)),
        "workerImageRef": runner.get("workerImageBase"),
        "reportedWorkerImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
        "workerImageId": None,
        "controller": _controller_identity(),
        "state": "prepared",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cidfilePrefix": f"{attempt_id}-",
        "containers": [],
        "cancellation": None,
    }
    image = _engine_call(attempt_directory, journal,
                         ["image", "inspect", "--format", "{{.Id}}", str(journal["workerImageRef"])])
    image_id = _normalize_engine_image_id(image.stdout)
    if image.returncode != 0 or image_id is None:
        raise PackageError("Pinned workflow image could not be verified by the configured engine")
    journal["workerImageId"] = image_id
    return journal

def _controller_identity() -> dict:
    identity = {"pid": os.getpid(), "bootId": None, "processStartTicks": None}
    try:
        identity["bootId"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{os.getpid()}/stat").read_text()
        identity["processStartTicks"] = stat[stat.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        pass
    return identity

def _controller_is_alive(identity: Any) -> bool:
    if not isinstance(identity, dict) or type(identity.get("pid")) is not int or identity["pid"] <= 0:
        raise PackageError("Attempt journal has no trusted controller process identity")
    current = _controller_identity()
    if (not isinstance(identity.get("bootId"), str) or
            not re.fullmatch(r"[0-9a-fA-F-]{36}", identity["bootId"]) or
            not isinstance(identity.get("processStartTicks"), str) or
            not identity["processStartTicks"].isdigit() or
            not isinstance(current.get("bootId"), str) or
            not isinstance(current.get("processStartTicks"), str)):
        raise PackageError("Attempt journal lacks a verifiable Linux controller identity")
    if identity.get("bootId") != current.get("bootId"):
        return False
    pid = identity["pid"]
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PackageError(f"Cannot verify prior controller process state: {exc}") from exc
    try:
        ticks = stat[stat.rfind(")") + 2:].split()[19]
    except IndexError as exc:
        raise PackageError("Prior controller process identity is malformed") from exc
    if ticks != identity.get("processStartTicks"):
        return False
    raise PackageError("The original attempt controller is still alive; refusing concurrent cleanup")

def _journal_update(attempt_directory: Path, journal: dict, receipt: dict, **changes) -> None:
    journal.update(changes)
    journal["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _journal_receipt_reference(attempt_directory, journal, receipt)

def _discover_container_ids(attempt_directory: Path, journal: dict,
                            receipt: dict, errors: list[str]) -> None:
    cid_directory = attempt_directory / "engine-cids"
    if cid_directory.is_symlink() or not cid_directory.is_dir():
        errors.append("Container cidfile directory is missing or unsafe")
        journal["state"] = "cleanup-failed"
        _journal_update(attempt_directory, journal, receipt)
        return
    files = sorted(cid_directory.glob(f"{journal['cidfilePrefix']}*.cid"))
    if len(files) > _MAX_ATTEMPT_CONTAINERS:
        errors.append("Container journal exceeds its bounded CID count")
        journal["state"] = "cleanup-failed"
        _journal_update(attempt_directory, journal, receipt)
        return
    known = {entry.get("id") for entry in journal["containers"] if isinstance(entry, dict)}
    for path in files:
        try:
            if path.is_symlink() or not path.is_file():
                raise PackageError("CID file is not a regular file")
            if path.stat().st_size > 128:
                raise PackageError("CID file exceeds its limit")
            container_id = path.read_text(encoding="ascii").strip()
            if not _CONTAINER_ID.fullmatch(container_id):
                raise PackageError("CID file does not contain a full lowercase container ID")
        except (OSError, UnicodeError, PackageError) as exc:
            errors.append(f"Container CID evidence rejected ({path.name}): {exc}")
            continue
        if container_id in known:
            continue
        entry = {
            "engine": journal["engine"],
            "id": container_id,
            "imageId": journal["workerImageId"],
            "imageRef": journal["workerImageRef"],
            "cidfile": f"engine-cids/{path.name}",
            "discoveredAt": datetime.now(timezone.utc).isoformat(),
            "inspectState": "unknown",
            "cleanup": {"status": "pending", "absentAfterCleanup": False},
        }
        journal["containers"].append(entry)
        known.add(container_id)
        # Record every newly discovered engine identity before contacting it.
        _journal_update(attempt_directory, journal, receipt)

def _container_engine_environment(attempt_directory: Path) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(attempt_directory / "runner-home"),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1"}
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "LD_LIBRARY_PATH"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env

def _engine_call(attempt_directory: Path, journal: dict, arguments: list[str]) -> subprocess.CompletedProcess:
    executable = journal.get("enginePath")
    expected = shutil.which(journal.get("engine", ""), path="/usr/bin:/bin")
    if (not isinstance(executable, str) or not Path(executable).is_absolute() or
            not expected or Path(executable).resolve(strict=True) != Path(expected).resolve(strict=True)):
        raise PackageError("Container cleanup engine path differs from the trusted engine binary")
    try:
        return subprocess.run([executable, *arguments], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=8, check=False,
                              env=_container_engine_environment(attempt_directory))
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackageError(f"Container engine request failed: {type(exc).__name__}: {str(exc)[:512]}") from exc

def _container_present(attempt_directory: Path, journal: dict, container_id: str) -> tuple[bool, str | None]:
    inspected = _engine_call(attempt_directory, journal,
                             ["inspect", "--format", "{{.Id}} {{.State.Status}} {{.Image}} {{.Config.Image}}", container_id])
    if inspected.returncode == 0:
        fields = inspected.stdout.strip().split()
        if len(fields) != 4 or fields[0].lower() != container_id:
            raise PackageError("Container inspect returned a different or malformed ID")
        inspected_image_id = _normalize_engine_image_id(fields[2])
        if (inspected_image_id != journal["workerImageId"]
                or not _engine_image_reference_matches(fields[3], journal["workerImageRef"])):
            raise PackageError("Journaled container image differs from the pinned and receipt-bound worker image")
        return True, fields[1].lower()
    listing = _engine_call(attempt_directory, journal, ["ps", "--all", "--no-trunc", "--format", "{{.ID}}"])
    if listing.returncode != 0:
        raise PackageError("Container engine could not prove whether the exact journaled ID remains")
    ids = {line.strip().lower() for line in listing.stdout.splitlines() if line.strip()}
    if container_id in ids:
        raise PackageError("Container engine lists the ID but inspect cannot establish its state")
    return False, None

def _cleanup_container_journal(attempt_directory: Path, journal: dict, receipt: dict,
                               errors: list[str]) -> None:
    _discover_container_ids(attempt_directory, journal, receipt, errors)
    for entry in journal["containers"]:
        container_id = entry["id"]
        cleanup = entry["cleanup"]
        if cleanup.get("absentAfterCleanup") is True:
            continue
        try:
            present, state = _container_present(attempt_directory, journal, container_id)
            entry["inspectState"] = state or "absent"
            if present and state == "running":
                cleanup.update(status="stopping", requestedAt=datetime.now(timezone.utc).isoformat())
                _journal_update(attempt_directory, journal, receipt)
                stopped = _engine_call(attempt_directory, journal,
                                       ["stop", "--time", "5", container_id])
                present, state = _container_present(attempt_directory, journal, container_id)
                if present and state == "running":
                    raise PackageError(f"Container stop did not stop exact ID (exit {stopped.returncode})")
            if present:
                if state not in {"created", "exited", "stopped"}:
                    raise PackageError(f"Container state {state!r} is busy or unknown; refusing removal")
                cleanup.update(status="removing", removeRequestedAt=datetime.now(timezone.utc).isoformat())
                _journal_update(attempt_directory, journal, receipt)
                removed = _engine_call(attempt_directory, journal, ["rm", container_id])
                present, state = _container_present(attempt_directory, journal, container_id)
                if present:
                    raise PackageError(f"Container removal did not remove exact ID (exit {removed.returncode}, state {state})")
            cleanup.update(status="absent", absentAfterCleanup=True,
                           completedAt=datetime.now(timezone.utc).isoformat())
            entry["inspectState"] = "absent"
            _journal_update(attempt_directory, journal, receipt)
        except PackageError as exc:
            cleanup.update(status="cleanup-failed", error=f"{type(exc).__name__}: {str(exc)[:512]}",
                           absentAfterCleanup=False)
            errors.append(f"Container cleanup failed for exact ID {container_id}: {exc}")
            _journal_update(attempt_directory, journal, receipt)
    journal["state"] = "cleanup-failed" if errors else "clean"
    _journal_update(attempt_directory, journal, receipt)
