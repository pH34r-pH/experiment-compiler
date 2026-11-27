"""Offline readback of separately retained Long-Haul trajectory evidence."""
from __future__ import annotations

import re
import sys
from importlib import metadata, util
from pathlib import Path

from .archive_policy import PackageReadPolicy
from .core import PackageError, bounded_read, json_value, sha256, verify_package

TRAJECTORY_SOURCE = {
    "repository": "pH34r-pH/long-haul",
    "commit": "64c81d31fc8193cd2ff9e96a8df1751f86f8a21c",
}
BOUND_QUALIFICATION = "readback-required"
TRACE_REFERENCE = "artifact:semantic-trace-v1"
RESULT_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
HARNESS_SHA256 = "8f5ead47da8c4552928bbe061c174e43298c982039513db49e30656960d5cb0d"


def is_bound_trajectory(reference: dict) -> bool:
    identity = reference["reference"]
    return (
        reference["kind"] == "semantic-trajectory"
        and reference["source"] == TRAJECTORY_SOURCE
        and isinstance(identity, str)
        and identity.startswith(TRACE_REFERENCE + "/")
        and RESULT_FINGERPRINT.fullmatch(identity.removeprefix(TRACE_REFERENCE + "/")) is not None
    )


def _pinned_verifier():
    try:
        dist = metadata.distribution("long-haul")
        source = json_value((dist.read_text("direct_url.json") or "{}").encode())
        vcs = source.get("vcs_info") if isinstance(source, dict) else None
        module = util.find_spec("long_haul")
    except (ImportError, ValueError, OSError) as exc:
        raise PackageError("Install the pinned trajectory extra in a trusted environment") from exc
    package = Path(dist.locate_file("long_haul")).resolve()
    if (
        not isinstance(vcs, dict)
        or source.get("url") != "https://github.com/" + TRAJECTORY_SOURCE["repository"]
        or vcs.get("vcs") != "git"
        or vcs.get("commit_id") != TRAJECTORY_SOURCE["commit"]
        or module is None
        or not module.origin
        or Path(module.origin).resolve() != package / "__init__.py"
    ):
        raise PackageError("Installed Long-Haul differs from the pinned trajectory source")
    harness_bytes = bounded_read(package / "work" / "harness.py").replace(b"\r\n", b"\n")
    if sha256(harness_bytes) != HARNESS_SHA256:
        raise PackageError("Installed Long-Haul verifier bytes differ from the source pin")
    from long_haul.work.harness import verify_semantic_trace

    return verify_semantic_trace


def _bound_reference(report: dict, expected: str) -> dict:
    agent = report.get("repositoryAgent", {})
    if agent.get("audience") != "private-evaluator":
        raise PackageError("Trajectory readback requires a private-evaluator repository package")
    matches = [
        item for item in agent["references"]
        if item["qualification"] == BOUND_QUALIFICATION
        and is_bound_trajectory(item)
        and item["reference"] == TRACE_REFERENCE + "/" + expected
    ]
    if len(matches) != 1:
        raise PackageError("Package requires one bound reference for the expected trajectory result")
    return matches[0]


def _check_task_binding(result, binding: dict) -> None:
    attempt = result.attempt
    if (
        attempt.repository_id != binding["repositoryId"]
        or attempt.repository_extension_digest != binding["taskFingerprint"]
        or attempt.context_manifest.model_dump(mode="json") != binding["contextManifest"]
    ):
        raise PackageError("Verified trajectory belongs to a different repository task or context")
    expected_candidate = binding["candidateFingerprint"]
    if expected_candidate is not None and (
        result.candidate is None
        or result.candidate.reference != "artifact:frozen-worker-candidate"
        or result.candidate.digest != expected_candidate
    ):
        raise PackageError("Verified trajectory belongs to a different frozen candidate")


def verify_trajectory(
    package: Path, result: Path, trace: Path, *, expected_sha256: str,
    expected_result_fingerprint: str, max_bytes: int, policy: PackageReadPolicy | None = None,
) -> dict:
    """Verify a digest-pinned package and its separately retained result and trace."""
    if (not expected_sha256 or not isinstance(expected_result_fingerprint, str)
            or not RESULT_FINGERPRINT.fullmatch(expected_result_fingerprint)):
        raise PackageError("Trajectory readback requires independent package and result pins")
    if type(max_bytes) is not int or not 0 < max_bytes < sys.maxsize:
        raise PackageError("Trajectory readback requires a positive byte budget")
    report = verify_package(package, expected_sha256=expected_sha256, policy=policy)
    reference = _bound_reference(report, expected_result_fingerprint)
    result_bytes = bounded_read(result, max_bytes)
    trace_bytes = bounded_read(trace, max_bytes - len(result_bytes))
    verifier = _pinned_verifier()
    try:
        retained, _ = verifier(
            result_bytes, trace_bytes, expected_result_fingerprint=expected_result_fingerprint,
            max_bytes=max_bytes,
        )
    except ValueError as exc:
        raise PackageError("Pinned Long-Haul verifier rejected the retained trajectory") from exc
    if retained.trace.digest != reference["digest"]:
        raise PackageError("Verified trajectory bytes differ from the package trace digest")
    _check_task_binding(retained, report["repositoryAgent"]["trajectoryBinding"])
    return {
        "schemaVersion": 1,
        "scope": "semantic-trajectory-integrity-only",
        "packageSha256": report["packageSha256"],
        "verifierSource": dict(TRAJECTORY_SOURCE),
        "verifierHarnessLfSha256": HARNESS_SHA256,
        "resultFingerprint": expected_result_fingerprint,
        "traceDigest": retained.trace.digest,
        "eventCount": retained.event_count,
        "terminal": retained.terminal,
        "candidateDisposition": retained.candidate_disposition,
        "acceptance": retained.acceptance,
        "trainingEligibility": "not-granted",
    }
