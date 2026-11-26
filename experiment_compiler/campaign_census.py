"""Reconcile source-declared campaign attempts with retained lifecycle evidence."""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from .core import PackageError, SHA256, canonical, sha256, verify_bytes
from .revision import _read_parent_attempt

MEASURED = {"included", "measured-excluded"}
DISPOSITIONS = MEASURED | {"failed", "interrupted", "not-admitted", "lost", "unknown"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _fields(value: object, names: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise PackageError(f"{label} has missing or unsupported fields")
    return value


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PackageError(f"{label} is invalid")
    return value


def _instant(value: object, label: str) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None and "T" in value:
                return parsed
        except ValueError:
            pass
    raise PackageError(f"{label} must be an ISO timestamp with an explicit offset")


def attempt_member(digest: str) -> str:
    return f"evidence/campaign-attempts/{digest}.zip"


def _issued_attempts(inventory: dict, cutoff: datetime) -> tuple[dict[str, dict], dict[str, datetime]]:
    rows = inventory["issued"]
    if not isinstance(rows, list) or not rows:
        raise PackageError("Campaign inventory requires a nonempty issued-attempt list")
    issued: dict[str, dict] = {}
    issued_times: dict[str, datetime] = {}
    roots: set[str] = set()
    for row in rows:
        _fields(row, {"attemptId", "replicateId", "issuedAt", "retryOf"}, "Issued attempt")
        attempt_id = _id(row["attemptId"], "Issued attempt ID")
        replicate_id = _id(row["replicateId"], "Source replicate ID")
        issued_time = _instant(row["issuedAt"], "Attempt issuance")
        if attempt_id in issued or issued_time > cutoff:
            raise PackageError("Issued attempt is duplicated or beyond the campaign cutoff")
        if row["retryOf"] is None:
            if replicate_id in roots:
                raise PackageError("A replicate must have a single issued root attempt")
            roots.add(replicate_id)
        else:
            _id(row["retryOf"], "Retry parent ID")
        issued[attempt_id] = row
        issued_times[attempt_id] = issued_time

    for attempt_id, row in issued.items():
        parent_id = row["retryOf"]
        if parent_id is None:
            continue
        parent = issued.get(parent_id)
        if parent is None and inventory["scope"] == "partial":
            continue
        # Strict temporal ordering also prevents cycles, independent of list order.
        if (
            parent is None or parent["replicateId"] != row["replicateId"]
            or issued_times[parent_id] >= issued_times[attempt_id]
        ):
            raise PackageError("Retry must reference an earlier issued attempt for the same replicate")
    return issued, issued_times


def _package_receipt(
    loader: Callable[[str], bytes], digest: str, attempt_id: str, plan_sha256: str,
) -> dict:
    package = loader(digest)
    verification = verify_bytes(package, expected_sha256=digest)
    if verification["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("Campaign evidence must use the lifecycle attempt profile")
    _, receipt, _ = _read_parent_attempt(package, attempt_id)
    if receipt["planPackageSha256"] != plan_sha256:
        raise PackageError("Campaign attempt evidence belongs to a different plan")
    return receipt


def assess_campaign(
    source: object, *, plan_sha256: str, selected_id: str, selected_sha256: str,
    selected_receipt: dict, loader: Callable[[str], bytes],
) -> tuple[dict, dict[str, dict]]:
    campaign = _fields(source, {"inventory", "inventorySha256", "outcomes"}, "Campaign record")
    inventory = _fields(
        campaign["inventory"], {"campaignId", "planSha256", "cutoff", "scope", "issued"},
        "Campaign inventory",
    )
    _id(inventory["campaignId"], "Campaign ID")
    scope = inventory["scope"]
    if (
        inventory["planSha256"] != plan_sha256 or not isinstance(scope, str)
        or scope not in {"complete", "partial"}
        or campaign["inventorySha256"] != sha256(canonical(inventory))
    ):
        raise PackageError("Campaign inventory has the wrong plan, scope or digest")
    cutoff = _instant(inventory["cutoff"], "Campaign cutoff")
    issued, issued_times = _issued_attempts(inventory, cutoff)
    outcomes = campaign["outcomes"]
    if not isinstance(outcomes, list):
        raise PackageError("Campaign outcomes must be an array")

    dispositions: dict[str, dict] = {}
    observation_receipts: dict[str, dict] = {}
    for outcome in outcomes:
        _fields(outcome, {"attemptId", "status", "packageSha256", "reason"}, "Campaign outcome")
        attempt_id = _id(outcome["attemptId"], "Campaign outcome attempt ID")
        if attempt_id not in issued or attempt_id in dispositions:
            raise PackageError("Campaign outcome references an unissued or duplicate attempt")
        status = outcome["status"]
        if not isinstance(status, str) or status not in DISPOSITIONS:
            raise PackageError("Campaign outcome disposition is unsupported")
        reason = outcome["reason"]
        if status == "included":
            if reason is not None:
                raise PackageError("An included outcome cannot carry an exclusion reason")
        elif not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise PackageError("Every non-included campaign outcome requires a bounded reason")

        digest = outcome["packageSha256"]
        if digest is not None and (not isinstance(digest, str) or SHA256.fullmatch(digest) is None):
            raise PackageError("Campaign attempt package digest is invalid")
        if attempt_id == selected_id and (status != "included" or digest != selected_sha256):
            raise PackageError("Campaign must account for the exact selected parent as included")
        receipt = None
        if status in MEASURED:
            if digest is None:
                raise PackageError("A measured outcome needs an exact retained attempt package")
            receipt = selected_receipt if attempt_id == selected_id else _package_receipt(
                loader, digest, attempt_id, plan_sha256,
            )
            if receipt["status"] != "succeeded" or not receipt.get("outputs"):
                raise PackageError("A measured outcome needs a successful execution with retained outputs")
            observation_receipts[attempt_id] = receipt
        elif digest is not None:
            if status != "failed":
                raise PackageError("Only measured or terminal failed outcomes may bind an attempt package")
            receipt = _package_receipt(loader, digest, attempt_id, plan_sha256)
            if receipt["status"] not in {"failed", "timed-out"}:
                raise PackageError("Failed campaign outcome conflicts with retained execution evidence")
        if receipt is not None:
            start = _instant(receipt.get("startedAt"), "Retained attempt start")
            end = _instant(receipt.get("endedAt"), "Retained attempt end")
            if not issued_times[attempt_id] <= start <= end <= cutoff:
                raise PackageError("Retained attempt timing is outside the issued campaign interval")
        dispositions[attempt_id] = outcome

    if selected_id not in dispositions:
        raise PackageError("Campaign has no outcome for the selected parent")
    if scope == "complete" and set(dispositions) != set(issued):
        raise PackageError("Source-declared complete campaign has unaccounted issued attempts")
    attempts = [
        {**row, **dispositions.get(attempt_id, {"status": "unreported", "packageSha256": None, "reason": None})}
        for attempt_id, row in issued.items()
    ]
    return {
        "campaignId": inventory["campaignId"],
        "inventorySha256": campaign["inventorySha256"],
        "cutoff": inventory["cutoff"],
        "scope": scope,
        "campaignDenominator": len(issued) if scope == "complete" else None,
        "recordedIssuedAttempts": len(issued),
        "recordedReplicates": len({row["replicateId"] for row in issued.values()}),
        "recordedRetries": sum(row["retryOf"] is not None for row in issued.values()),
        "unresolvedRetryParents": sorted({
            row["retryOf"] for row in issued.values()
            if row["retryOf"] is not None and row["retryOf"] not in issued
        }),
        "attempts": attempts,
        "inventoryAuthority": "source-declared",
    }, observation_receipts
