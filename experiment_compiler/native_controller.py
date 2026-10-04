"""Native lifecycle orchestration for a separately trusted, qualified Fleet authority.

No default authority, executor, scheduling, isolation, or transport is supplied.
Only the injected authority crosses those boundaries; compile/verify stay offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import ContextManager, Protocol

from .actions_projection import PROFILE_LIMIT, _pinned_profile, project_actions
from .core import PackageError, bounded_read, canonical, json_value
from .native_receipts import _identity, finish_native_attempt, start_native_attempt
from .runner import _read_package


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class NativeRequest:
    """Immutable verified bytes; specification binds package, profile and runtime."""

    package: bytes
    profile: bytes
    specification: bytes

    @property
    def sha256(self) -> str:
        return _sha(self.specification)


class NativeLease(Protocol):
    """Authority-owned exclusive lifetime, not a candidate-supplied implementation."""

    request_sha256: str
    qualification_evidence_sha256: str

    def retain(self, receipt: bytes, *, expected_sha256: str) -> str:
        """Persist exact bytes outside disposable worker state; acknowledge their digest."""
        ...

    def execute(self, request: NativeRequest) -> dict:
        """Return native receipt observations after bounded execution and cleanup.

        Raise on unknown outcome/transport loss. Do not turn cleanup success into
        execution success. The authority enforces wall/resource/attempt budgets.
        """
        ...


class NativeAuthority(Protocol):
    def lease(self, request: NativeRequest) -> ContextManager[NativeLease]:
        """Independently authenticate qualification before entering exclusive ownership.

        Verify target assets/runtime and isolation, enforce protocol retry budgets,
        and reject unavailable/expired execution. Hold ownership through retention.
        No profile assertion alone constitutes authentication.
        """
        ...


def _profile_admission(profile, controller):
    if any(value != "qualified" for value in profile["capabilities"].values()):
        raise PackageError("native controller requires independently qualified capabilities")
    evidence = profile["qualificationEvidenceSha256"]
    if evidence is None:
        raise PackageError("native controller requires a qualification evidence pin")
    workflow = f"{controller['repository']}/{controller['workflow']}@{controller['commit']}"
    if workflow != profile["workflow"]:
        raise PackageError("native controller identity differs from pinned profile workflow")
    return evidence


def _prepare(package: Path, profile_path: Path, selection: dict):
    fields = {"packageSha256", "profileSha256", "runtimeSha256", "attemptId", "controller"}
    if not isinstance(selection, dict) or set(selection) != fields:
        raise PackageError("native controller requires explicit selected identities")
    # Reuse owning projection admission without interpreting dispatchAuthorized=false
    # as an authorization. Only the independent authority below can admit execution.
    projection = project_actions(package, profile_path, expected_sha256=selection["packageSha256"],
                                 expected_profile_sha256=selection["profileSha256"])
    package_bytes = bounded_read(package)
    manifest, _ = _read_package(package_bytes, selection["packageSha256"])
    profile = _pinned_profile(profile_path, selection["profileSha256"])
    profile_bytes = bounded_read(profile_path, PROFILE_LIMIT)
    if _sha(profile_bytes) != selection["profileSha256"]:
        raise PackageError("native profile changed before request construction")
    identity = {"attemptId": selection["attemptId"], "controller": selection["controller"],
                "sourceRepository": manifest["source"]["repository"], "sourceCommit": manifest["source"]["commit"],
                "planPackageSha256": selection["packageSha256"], "profileSha256": selection["profileSha256"],
                "runtimeSha256": selection["runtimeSha256"],
                "stagedAssetsSha256": _sha(canonical(projection["stagedAssets"])), "requestSha256": "0" * 64}
    _identity(identity)
    evidence = _profile_admission(profile, identity["controller"])
    del identity["requestSha256"]
    specification = canonical({"schemaVersion": 1, "kind": "fleet-native-controller-request",
                               "identity": identity, "qualificationEvidenceSha256": evidence,
                               "workflowSha256": projection["workflowSha256"],
                               "jobSha256": projection["jobSha256"], "platform": projection["platform"],
                               "resourceLimits": projection["resourceLimits"],
                               "maxWallSeconds": projection["jobs"]["native"]["with"]["max-wall-seconds"],
                               "stagedAssets": projection["stagedAssets"]})
    request = NativeRequest(package_bytes, profile_bytes, specification)
    return request, evidence


def _retain(lease: NativeLease, path: Path, digest: str):
    raw = bounded_read(path, 65536)
    if _sha(raw) != digest:
        raise PackageError("native receipt changed before transport")
    if lease.retain(raw, expected_sha256=digest) != digest:
        raise PackageError("native receipt transport did not acknowledge exact bytes")


def run_native_controller(package: Path, profile: Path, attempt_directory: Path, *,
                          selection: dict, authority: NativeAuthority) -> dict:
    """Coordinate one admitted native attempt through an explicitly trusted authority.

    Controller-owned store and authority must be outside candidate write access.
    An exception leaves the strongest locally observed snapshot intact. Execution
    or transport uncertainty never manufactures a terminal observation or retry.
    """
    request, evidence = _prepare(package, profile, selection)
    specification = json_value(request.specification)
    identity = dict(specification["identity"], requestSha256=request.sha256)
    with authority.lease(request) as lease:
        if lease.request_sha256 != request.sha256 or lease.qualification_evidence_sha256 != evidence:
            raise PackageError("native authority lease differs from admitted request or qualification")
        started = start_native_attempt(attempt_directory, identity, started_at=datetime.now(timezone.utc).isoformat())
        _retain(lease, attempt_directory / "started-receipt.json", started)
        observations = lease.execute(request)
        terminal = finish_native_attempt(attempt_directory, observations, expected_started_sha256=started)
        _retain(lease, attempt_directory / "terminal-receipt.json", terminal)
    return {"requestSha256": request.sha256, "startedReceiptSha256": started,
            "terminalReceiptSha256": terminal,
            "status": json_value(bounded_read(attempt_directory / "terminal-receipt.json", 65536))["status"],
            "packageOutcome": "not established by controller receipts"}
