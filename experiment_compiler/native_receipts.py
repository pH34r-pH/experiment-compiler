"""Controller-reported native attempt observations; no execution or attestation."""
from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime
from pathlib import Path

from .core import PackageError, bounded_read, canonical, json_value
from .runner import _persist_attempt_receipt

PRODUCER = "experiment-compiler.native-runner/v1"
IDENTITY_FIELDS = {"attemptId", "planPackageSha256", "profileSha256", "runtimeSha256",
                   "stagedAssetsSha256", "requestSha256", "sourceCommit", "sourceRepository", "controller"}
OBSERVATION_FIELDS = {"status", "startedAt", "endedAt", "wallSeconds", "exitCode",
                      "collectionErrors", "resourceMeasurements"}
FIELDS = IDENTITY_FIELDS | OBSERVATION_FIELDS | {"schemaVersion", "producer", "identityBasis", "startedReceiptSha256"}
IDENTITY_BASIS = "controller-reported; not independently authenticated"
TERMINAL = {"succeeded", "failed", "timed-out", "cancelled"}


def _shape(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise PackageError("unsupported native receipt fields")


def _literal(value, pattern):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise PackageError("invalid native receipt identity")


def _identity(receipt):
    _literal(receipt["attemptId"], r"[0-9a-f]{12}")
    for key in IDENTITY_FIELDS - {"attemptId", "controller", "sourceCommit", "sourceRepository"}:
        _literal(receipt[key], r"[0-9a-f]{64}")
    _literal(receipt["sourceCommit"], r"[0-9a-f]{40}")
    _literal(receipt["sourceRepository"], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
    controller = receipt["controller"]
    _shape(controller, {"repository", "workflow", "commit", "runId", "job", "runAttempt"})
    _literal(controller["repository"], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
    _literal(controller["workflow"], r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml")
    _literal(controller["commit"], r"[0-9a-f]{40}")
    _literal(controller["job"], r"[A-Za-z_][A-Za-z0-9_-]{0,99}")
    for key in ("runId", "runAttempt"):
        if type(controller[key]) is not int or controller[key] < 1:
            raise PackageError("invalid native controller run identity")


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise PackageError("invalid native receipt timestamp")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PackageError("invalid native receipt timestamp") from exc
    if stamp.tzinfo is None:
        raise PackageError("native receipt timestamp needs timezone")
    return stamp


def _nonnegative(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _observations(receipt):
    errors = receipt["collectionErrors"]
    if not isinstance(errors, list) or len(errors) > 32 or any(
            not isinstance(item, str) or len(item) > 4096 for item in errors):
        raise PackageError("invalid native collection errors")
    measurements = receipt["resourceMeasurements"]
    _shape(measurements, {"peakProcessMemoryBytes", "cpuSeconds"})
    memory = measurements["peakProcessMemoryBytes"]
    if memory is not None and (type(memory) is not int or memory < 0):
        raise PackageError("native memory bytes must be a nonnegative integer or null")
    cpu = measurements["cpuSeconds"]
    if cpu is not None and not _nonnegative(cpu):
        raise PackageError("invalid native CPU measurement; use null for unmeasured")
    if receipt["exitCode"] is not None and type(receipt["exitCode"]) is not int:
        raise PackageError("invalid native exit code")


def _state(receipt):
    start = _timestamp(receipt["startedAt"])
    status = receipt["status"]
    if status == "started":
        _started_state(receipt)
        return
    if not isinstance(status, str) or status not in TERMINAL:
        raise PackageError("unknown native outcome; retain started receipt after worker loss")
    _literal(receipt["startedReceiptSha256"], r"[0-9a-f]{64}")
    if _timestamp(receipt["endedAt"]) < start or not _nonnegative(receipt["wallSeconds"]):
        raise PackageError("invalid native terminal timing")
    if status == "succeeded" and (receipt["exitCode"] != 0 or receipt["collectionErrors"]):
        raise PackageError("contradictory native success")
    if status == "failed" and receipt["exitCode"] in (None, 0) and not receipt["collectionErrors"]:
        raise PackageError("native failure needs observed failure evidence")


def _started_state(receipt):
    if any(receipt[key] is not None for key in ("endedAt", "wallSeconds", "exitCode", "startedReceiptSha256")):
        raise PackageError("started native receipt cannot claim completion")
    if receipt["collectionErrors"] or any(v is not None for v in receipt["resourceMeasurements"].values()):
        raise PackageError("started native receipt cannot claim observations")


def validate_native_receipt(receipt):
    """Validate shape/truth consistency, not authenticity or worker qualification."""
    _shape(receipt, FIELDS)
    if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1:
        raise PackageError("unsupported native receipt schema")
    if receipt["producer"] != PRODUCER or receipt["identityBasis"] != IDENTITY_BASIS:
        raise PackageError("unsupported native receipt producer or attestation")
    if len(canonical(receipt)) > 65536:
        raise PackageError("native receipt exceeds 64 KiB")
    _identity(receipt)
    _observations(receipt)
    _state(receipt)
    return receipt["status"]


def _snapshot(directory, name, receipt):
    allowed = {"started-receipt.json", "runner-receipt.json"} if name == "terminal-receipt.json" else set()
    members = list(directory.iterdir())
    if any(path.name not in allowed or path.is_symlink() or not path.is_file() for path in members):
        raise PackageError("unexpected native attempt member; refusing overwrite")
    raw = canonical(receipt)
    with (directory / name).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _persist_attempt_receipt(directory, receipt)
    return hashlib.sha256(raw).hexdigest()


def start_native_attempt(directory: Path, identity: dict, *, started_at: str) -> str:
    """Persist started evidence before a controller launches an admitted workload.

    Store must be controller-owned, outside candidate write access.
    Caller owns admission and authenticating identity; this function executes nothing.
    A returned digest indicates local persistence, not remote archive acknowledgement.
    """
    if os.name != "posix":
        raise PackageError("native receipt persistence requires qualified POSIX directory fsync")
    _shape(identity, IDENTITY_FIELDS)
    receipt = dict(identity, schemaVersion=1, producer=PRODUCER, identityBasis=IDENTITY_BASIS,
                   status="started", startedReceiptSha256=None, startedAt=started_at, endedAt=None, wallSeconds=None,
                   exitCode=None, collectionErrors=[],
                   resourceMeasurements={"peakProcessMemoryBytes": None, "cpuSeconds": None})
    validate_native_receipt(receipt)
    if any(part.is_symlink() for part in (directory, *directory.parents)):
        raise PackageError("native attempt directory must not contain symlinks")
    directory.mkdir(exist_ok=False)
    descriptor = os.open(directory.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _snapshot(directory, "started-receipt.json", receipt)


def finish_native_attempt(directory: Path, observations: dict, *, expected_started_sha256: str) -> str:
    """Retain one terminal observation without changing the original started identity."""
    _shape(observations, OBSERVATION_FIELDS - {"startedAt"})
    if any(part.is_symlink() for part in (directory, *directory.parents)):
        raise PackageError("native attempt directory must not contain symlinks")
    raw = bounded_read(directory / "started-receipt.json", 65536)
    if hashlib.sha256(raw).hexdigest() != expected_started_sha256:
        raise PackageError("native started receipt digest mismatch")
    started = json_value(raw)
    if validate_native_receipt(started) != "started":
        raise PackageError("native terminal needs a started receipt")
    latest = directory / "runner-receipt.json"
    if latest.exists() and bounded_read(latest, 65536) != raw:
        raise PackageError("native latest receipt differs from started identity")
    receipt = dict(started, **observations, startedReceiptSha256=expected_started_sha256)
    if validate_native_receipt(receipt) not in TERMINAL:
        raise PackageError("native terminal observation required")
    return _snapshot(directory, "terminal-receipt.json", receipt)
