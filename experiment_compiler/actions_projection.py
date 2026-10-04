"""Offline native Actions projection; never dispatches or creates attempt evidence."""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .core import (
    MANIFEST,
    PackageError,
    bounded_read,
    json_value,
    safe_path,
    unique_paths,
)
from .runner import _admit_job, _admit_workflow, _read_package

PROFILE_LIMIT = 1024 * 1024
_LIMITS = {"cores", "ramMiB", "tmpdirMiB", "outdirMiB", "wallSeconds"}
_CAPABILITIES = {"foregroundExclusion", "processTreeCleanup", "resourceEnforcement",
                 "networkIsolation", "candidateCredentialIsolation", "assetVerification",
                 "startedTerminalRetention", "lossRecovery"}


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise PackageError(f"{label} requires a SHA-256 digest")
    return value


def _pinned_profile(path: Path, expected_sha256: str) -> dict:
    data = bounded_read(path, PROFILE_LIMIT)
    if hashlib.sha256(data).hexdigest() != _digest(expected_sha256, "profile"):
        raise PackageError("native profile digest mismatch")
    profile = json_value(data)
    fields = {"schemaVersion", "id", "executionMode", "platform", "workflow", "limits",
              "capabilities", "qualificationEvidenceSha256", "stagedAssets"}
    if not isinstance(profile, dict) or set(profile) != fields or type(profile["schemaVersion"]) is not int:
        raise PackageError("native profile fields are incomplete or unsupported")
    if profile["schemaVersion"] != 1 or profile["executionMode"] != "fleet-native-v1":
        raise PackageError("unsupported native projection profile")
    if not isinstance(profile["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", profile["id"]):
        raise PackageError("native profile requires a bounded literal ID")
    _profile_target(profile)
    _profile_limits(profile["limits"])
    _profile_capabilities(profile)
    return profile


def _profile_target(profile):
    platform = profile["platform"]
    if (not isinstance(platform, dict) or set(platform) != {"os", "arch"}
            or platform["os"] not in ("linux", "windows")
            or platform["arch"] not in ("x86_64", "aarch64")):
        raise PackageError("unsupported native platform")
    workflow = profile["workflow"]
    if not isinstance(workflow, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[A-Za-z0-9_-]+\.ya?ml@[0-9a-f]{40}", workflow):
        raise PackageError("controller workflow must be pinned to a full commit")


def _profile_limits(limits):
    if not isinstance(limits, dict) or set(limits) != _LIMITS:
        raise PackageError("native profile needs explicit resource limits")
    for value in limits.values():
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        except OverflowError:
            valid = False
        if not valid:
            raise PackageError("native resource limits must be finite positive numbers")
    if type(limits["wallSeconds"]) is not int or limits["wallSeconds"] > 86400:
        raise PackageError("native wall budget must be an integer up to 86400 seconds")


def _profile_capabilities(profile):
    capabilities = profile["capabilities"]
    if (not isinstance(capabilities, dict) or set(capabilities) != _CAPABILITIES
            or any(value not in ("qualified", "unknown", "unsupported") for value in capabilities.values())):
        raise PackageError("native profile requires explicit capability states")
    evidence = profile["qualificationEvidenceSha256"]
    if evidence is not None:
        _digest(evidence, "qualification evidence")


def _assets(items, label):
    if not isinstance(items, list) or len(items) > 128:
        raise PackageError(f"{label} assets must be a bounded list")
    result = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise PackageError(f"{label} assets require logical path, digest and size only")
        path = safe_path(item["path"])
        _digest(item["sha256"], "asset")
        if type(item["size"]) is not int or not 0 < item["size"] < 2**63 or path in result:
            raise PackageError("invalid or duplicate native asset")
        result[path] = item
    return result


def _closure(payload, profile):
    closure = json_value(payload.get("dependency-closure.json", b"null"))
    if not isinstance(closure, dict) or closure.get("runtimeNetworkRequired") is not False:
        raise PackageError("native projection requires a no-network dependency closure")
    classes = closure.get("classifications")
    if not isinstance(classes, dict) or classes.get("unavailable") != []:
        raise PackageError("native projection has unresolved required dependencies")
    required = _assets(classes.get("external"), "source-required")
    staged = _assets(profile["stagedAssets"], "controller-staged")
    unique_paths([MANIFEST, *payload, *required])
    if required != staged:
        raise PackageError("staged asset identities do not match source-required closure")
    return required


def _native_plan(payload, profile):
    try:
        import yaml
    except ImportError as exc:
        raise PackageError("native projection requires the runner YAML parser") from exc
    runner = json_value(payload.get("experiment/runner.json", b"null"))
    if (not isinstance(runner, dict) or set(runner) != {"runner", "executionMode", "workerProfileId", "maxWallSeconds"}
            or runner.get("runner") != "fleet" or runner.get("executionMode") != "fleet-native-v1"
            or runner.get("workerProfileId") != profile["id"]):
        raise PackageError("plan must explicitly select the matching native profile")
    maximum = runner["maxWallSeconds"]
    if type(maximum) is not int or not 0 < maximum <= profile["limits"]["wallSeconds"]:
        raise PackageError("plan wall budget exceeds native profile")
    if any(len(payload.get(path, b"")) > PROFILE_LIMIT
           for path in ("experiment/workflow.cwl", "experiment/job.yml")):
        raise PackageError("native CWL/job document exceeds 1 MiB")
    try:
        workflow = yaml.safe_load(payload.get("experiment/workflow.cwl", b""))
        job = yaml.safe_load(payload.get("experiment/job.yml", b""))
    except (ValueError, RecursionError, yaml.YAMLError) as exc:
        raise PackageError("invalid native CWL/job document") from exc
    if not isinstance(workflow, dict) or workflow.get("cwlVersion") != "v1.2":
        raise PackageError("native projection requires CWL v1.2")
    inputs = _admit_workflow(workflow, profile["limits"], None, native_projection=True)
    if workflow["requirements"]["ToolTimeLimit"]["timelimit"] > maximum:
        raise PackageError("CWL time limit exceeds plan wall budget")
    return job, inputs, maximum


def project_actions(package: Path, profile_path: Path, *, expected_sha256: str,
                    expected_profile_sha256: str) -> dict:
    """Derive inert Actions job data from independently pinned source/profile bytes.

    The caller selects trusted Fleet profile bytes outside the candidate package.
    Qualification references are not authenticated here. No asset is read/fetched,
    no workflow is dispatched, and no started/terminal receipt is manufactured.
    """
    _digest(expected_sha256, "package")
    _, payload = _read_package(bounded_read(package), expected_sha256)
    profile = _pinned_profile(profile_path, expected_profile_sha256)
    assets = _closure(payload, profile)
    job, inputs, maximum = _native_plan(payload, profile)
    # Logical staged members are admitted for projection only. No placeholder is
    # written to disk or passed to run_package; that path remains container-only.
    _admit_job(job, {**payload, **{path: b"" for path in assets}}, inputs)
    blockers = [f"{key}: {value}" for key, value in sorted(profile["capabilities"].items())
                if value != "qualified"]
    if profile["qualificationEvidenceSha256"] is None:
        blockers.append("missing profile qualification evidence")
    blockers.extend(["worker must authenticate qualification and verify staged asset bytes",
                     "native execution and compatible attempt-receipt transport require qualification"])
    return {
        "schemaVersion": 1, "kind": "fleet-native-actions-projection", "dispatchAuthorized": False,
        "planPackageSha256": expected_sha256, "workerProfileSha256": expected_profile_sha256,
        "workflowSha256": hashlib.sha256(payload["experiment/workflow.cwl"]).hexdigest(),
        "jobSha256": hashlib.sha256(payload["experiment/job.yml"]).hexdigest(),
        "platform": profile["platform"], "resourceLimits": profile["limits"],
        "stagedAssets": [assets[key] for key in sorted(assets)], "blockers": blockers,
        "jobs": {"native": {"uses": profile["workflow"], "needs": [],
                            "strategy": {"max-parallel": 1, "matrix": {"profile": [profile["id"]]}},
                            "with": {"plan-sha256": expected_sha256, "profile-sha256": expected_profile_sha256,
                                     "max-wall-seconds": maximum}}},
    }
