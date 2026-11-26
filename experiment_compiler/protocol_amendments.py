"""Bind source protocol amendments to frozen obligations and retained outcomes."""
from __future__ import annotations

from datetime import datetime

from .campaign_census import _fields, _id, _instant
from .core import PackageError, SHA256, canonical, safe_path, sha256

ROLES = {"primary", "secondary", "control", "diagnostic", "exclusion", "stopping-rule"}
OUTPUT_FIELDS = {"attemptId", "outputPath", "outputSha256"}


def evidence_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise PackageError(f"{label} must be nonempty bounded text")
    return value


def matches_output(receipt: dict, reference: dict) -> bool:
    path = safe_path(reference["outputPath"])
    outputs = [
        output for output in receipt.get("outputs", [])
        if isinstance(output, dict) and output.get("@id") == path
    ]
    return len(outputs) == 1 and outputs[0].get("sha256") == reference["outputSha256"]


def _observed_before(rows: object, receipts: dict, available: set[str]) -> None:
    if not isinstance(rows, list):
        raise PackageError("Amendment observed outputs must be an array")
    seen = set()
    for row in rows:
        _fields(row, OUTPUT_FIELDS, "Amendment observed output")
        attempt_id = _id(row["attemptId"], "Amendment output attempt ID")
        if attempt_id not in available or not matches_output(receipts[attempt_id], row):
            raise PackageError("Amendment observed output lacks exact evidence available at its recorded time")
        key = attempt_id, row["outputPath"]
        if key in seen:
            raise PackageError("Amendment observed output is duplicated")
        seen.add(key)


def _timing(campaign: dict, available: set[str], recorded_at: datetime) -> str:
    if available:
        return "post-observation"
    if campaign["scope"] == "partial":
        return "unknown"
    for attempt in campaign["attempts"]:
        if (
            attempt["status"] != "not-admitted"
            and _instant(attempt["issuedAt"], "Attempt issuance") <= recorded_at
        ):
            return "unknown"
    return "prospective"


def _change(row: object, active: dict, known: set[str], seen: set[str]) -> str:
    change = _fields(row, {"id", "action", "role", "applicability"}, "Amendment change")
    identifier = _id(change["id"], "Amended obligation ID")
    action = change["action"]
    if identifier in seen or action not in ("add", "revise", "withdraw"):
        raise PackageError("Amendment contains a duplicate obligation or unknown change")
    if action == "add" and identifier in known:
        raise PackageError("Amendment cannot reuse an existing or withdrawn obligation ID")
    if action != "add" and identifier not in active:
        raise PackageError("Amendment changes an obligation that is not active")
    if action == "withdraw":
        if change["role"] is not None or change["applicability"] is not None:
            raise PackageError("A withdrawn obligation cannot redefine role or applicability")
        del active[identifier]
    else:
        role = change["role"]
        if not isinstance(role, str) or role not in ROLES:
            raise PackageError("Amended obligation role is unsupported")
        applicability = evidence_text(change["applicability"], "Amended applicability")
        version = {"id": identifier, "role": role, "applicability": applicability}
        if active.get(identifier) == version:
            raise PackageError("Amendment revision must change the obligation")
        active[identifier] = version
    known.add(identifier)
    seen.add(identifier)
    return identifier


def _changes(rows: object, active: dict, known: set[str]) -> dict:
    if not isinstance(rows, list) or not rows:
        raise PackageError("Amendment requires a nonempty change list")
    changed: set[str] = set()
    versions = {}
    for row in rows:
        identifier = _change(row, active, known, changed)
        versions[identifier] = active.get(identifier)
    return versions


def assess_amendments(
    rows: object, frozen_sha256: str, frozen: dict, campaign: dict, receipts: dict,
) -> tuple[list[dict], dict[str, dict]]:
    if not isinstance(rows, list):
        raise PackageError("Protocol amendments must be an array")
    cutoff = _instant(campaign["cutoff"], "Campaign cutoff")
    prior_time = None
    previous = frozen_sha256
    active = dict(frozen)
    known = set(frozen)
    history = []
    versions: dict[str, dict] = {}
    ends = {attempt_id: _instant(receipt["endedAt"], "Retained attempt end")
            for attempt_id, receipt in receipts.items()}
    for row in rows:
        _fields(row, {"id", "predecessorSha256", "recordedAt", "reason", "observedOutputs", "changes"},
                "Protocol amendment")
        amendment_id = _id(row["id"], "Protocol amendment ID")
        if amendment_id in versions or row["predecessorSha256"] != previous:
            raise PackageError("Protocol amendment has a duplicate ID or wrong predecessor digest")
        recorded_at = _instant(row["recordedAt"], "Amendment recorded time")
        if recorded_at > cutoff or (prior_time is not None and recorded_at <= prior_time):
            raise PackageError("Protocol amendment times must increase within the campaign cutoff")
        evidence_text(row["reason"], "Protocol amendment reason")
        available = {attempt_id for attempt_id, ended_at in ends.items() if ended_at <= recorded_at}
        _observed_before(row["observedOutputs"], receipts, available)
        versions[amendment_id] = _changes(row["changes"], active, known)
        previous = sha256(canonical(row))
        prior_time = recorded_at
        history.append({**row, "sha256": previous, "timing": _timing(campaign, available, recorded_at),
                        "availableAttemptIds": sorted(available)})
    return history, versions


def _interpretation_endpoint(item: object, frozen: dict, versions: dict) -> tuple[str, str | None]:
    endpoint = _fields(item, {"obligationId", "amendmentId", "role"}, "Interpretation endpoint")
    obligation_id = _id(endpoint["obligationId"], "Endpoint obligation ID")
    revision = endpoint["amendmentId"]
    if revision is None:
        obligation = frozen.get(obligation_id)
    else:
        revision = _id(revision, "Endpoint amendment ID")
        obligation = versions.get(revision, {}).get(obligation_id)
    if obligation is None or endpoint["role"] != obligation["role"]:
        raise PackageError("Interpretation endpoint does not match its declared obligation and role")
    return obligation_id, revision


def assess_interpretation(value: object, frozen: dict, versions: dict) -> dict:
    interpretation = _fields(
        value, {"comparator", "unitOfAnalysis", "uncertaintyMethod", "limits", "endpoints", "supersedes"},
        "Source interpretation",
    )
    for field in ("comparator", "unitOfAnalysis", "uncertaintyMethod"):
        evidence_text(interpretation[field], f"Interpretation {field}")
    limits = interpretation["limits"]
    if not isinstance(limits, list) or not limits:
        raise PackageError("Interpretation requires its explicit evidence limits")
    for limit in limits:
        evidence_text(limit, "Interpretation evidence limit")
    endpoints = interpretation["endpoints"]
    if not isinstance(endpoints, list) or not endpoints:
        raise PackageError("Interpretation must identify an endpoint")
    identities = [_interpretation_endpoint(item, frozen, versions) for item in endpoints]
    if len(set(identities)) != len(identities):
        raise PackageError("Interpretation endpoint is duplicated")
    supersedes = interpretation["supersedes"]
    if supersedes is not None:
        _fields(supersedes, {"finalPackageSha256", "reason"}, "Interpretation supersession")
        digest = supersedes["finalPackageSha256"]
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise PackageError("Interpretation supersession needs an exact final package SHA-256")
        evidence_text(supersedes["reason"], "Interpretation supersession reason")
    return interpretation
