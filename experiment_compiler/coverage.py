"""Validate source-authored obligations and optional campaign attempt accounting."""
from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable

from .campaign_census import IDENTIFIER, assess_campaign, attempt_member
from .core import PackageError, json_value, safe_path, sha256, verify_bytes
from .protocol_amendments import ROLES, assess_amendments, assess_interpretation, evidence_text, matches_output
from .revision import _root_and_protocol

FROZEN_SCHEMA = "compiled-experiment-obligations/v1"
RESOLUTION_SCHEMA = "compiled-experiment-coverage/v1"
CAMPAIGN_COVERAGE_SCHEMA = "compiled-experiment-coverage/v2"
AMENDED_COVERAGE_SCHEMA = "compiled-experiment-coverage/v3"


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise PackageError(f"{label} has missing or unsupported fields")
    return value


def _member(archive, path, digest, label):
    path = safe_path(path)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PackageError(f"{label} digest is invalid")
    try:
        raw = archive.read(path)
    except KeyError as exc:
        raise PackageError(f"{label} is absent from its verified package") from exc
    if sha256(raw) != digest:
        raise PackageError(f"{label} digest does not match its verified package")
    return raw


def _frozen(plan_bytes, binding):
    _object(binding, {"path", "sha256"}, "Frozen obligation binding")
    with zipfile.ZipFile(io.BytesIO(plan_bytes)) as plan:
        raw = _member(plan, binding["path"], binding["sha256"], "Frozen obligation record")
        frozen = _object(json_value(raw), {"schema", "protocol", "obligations"}, "Frozen obligation record")
        if frozen["schema"] != FROZEN_SCHEMA:
            raise PackageError("Unsupported frozen obligation schema")
        protocol = _object(frozen["protocol"], {"path", "sha256"}, "Frozen protocol binding")
        _, main_protocol = _root_and_protocol(json_value(plan.read("ro-crate-metadata.json")))
        if main_protocol.get("@id") != protocol["path"]:
            raise PackageError("Frozen obligations reference a different plan protocol")
        _member(plan, protocol["path"], protocol["sha256"], "Frozen protocol")
    obligations = frozen["obligations"]
    if not isinstance(obligations, list) or not obligations:
        raise PackageError("Frozen obligations must be a nonempty array")
    result = {}
    for item in obligations:
        _object(item, {"id", "role", "applicability"}, "Frozen obligation")
        identifier = item["id"]
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in result:
            raise PackageError("Frozen obligation ID is invalid or duplicated")
        if not isinstance(item["role"], str) or item["role"] not in ROLES:
            raise PackageError("Frozen obligation role is unsupported")
        evidence_text(item["applicability"], "Obligation applicability")
        result[identifier] = item
    return result


def _check_observed_output(item, receipts, *, campaign):
    observed_attempt = item["attemptId"]
    receipt = receipts.get(observed_attempt) if isinstance(observed_attempt, str) else None
    if receipt is None:
        if campaign:
            raise PackageError("Observed coverage does not identify a verified measured campaign attempt")
        raise PackageError("Observed coverage references a different attempt")
    if not matches_output(receipt, item):
        owner = "campaign-attempt" if campaign else "selected-attempt"
        raise PackageError(f"Observed coverage does not reference an exact {owner} output")
    evidence_text(item["finding"], "Source-authored finding")


def _resolution(item, obligations, receipts, *, campaign):
    if not isinstance(item, dict):
        raise PackageError("Coverage resolution must be an object")
    status = item.get("status")
    if not isinstance(status, str) or status not in {"observed", "omitted", "pending"}:
        raise PackageError("Coverage resolution status is unsupported")
    fields = {"id", "status", "reason"} if status in {"omitted", "pending"} else {
        "id", "status", "attemptId", "outputPath", "outputSha256", "finding"}
    _object(item, fields, "Coverage resolution")
    identifier = item["id"]
    if not isinstance(identifier, str) or identifier not in obligations:
        raise PackageError("Coverage resolution references an unknown obligation")
    if status in {"omitted", "pending"}:
        evidence_text(item["reason"], "Omission or pending reason")
    else:
        _check_observed_output(item, receipts, campaign=campaign)
    return identifier


def _amended_frozen_id(item, obligations, versions):
    _object(item, {"id", "status", "amendmentId"}, "Amended disposition")
    identifier, amendment_id = item["id"], item["amendmentId"]
    if (
        not isinstance(identifier, str) or identifier not in obligations
        or not isinstance(amendment_id, str) or identifier not in versions.get(amendment_id, {})
    ):
        raise PackageError("Amended disposition must reference an amendment of that frozen obligation")
    return identifier


def _obligation_readout(entries, obligations, receipts, *, campaign, versions=None):
    if not isinstance(entries, list):
        raise PackageError("Coverage resolutions must be an array")
    resolved = {}
    for item in entries:
        if versions is not None and isinstance(item, dict) and item.get("status") == "amended":
            identifier = _amended_frozen_id(item, obligations, versions)
        else:
            identifier = _resolution(item, obligations, receipts, campaign=campaign)
        if identifier in resolved:
            raise PackageError("Coverage resolution ID is duplicated")
        resolved[identifier] = item
    readout = []
    for identifier, obligation in obligations.items():
        item = resolved.get(identifier)
        entry = {"id": identifier, "role": obligation["role"], "applicability": obligation["applicability"],
                 "status": "unreported" if item is None else item["status"]}
        if item is not None:
            entry.update({key: value for key, value in item.items() if key not in {"id", "status"}})
        readout.append(entry)
    return readout


def _coverage_record(raw: bytes, plan_sha256: str, attempt_id: str) -> dict:
    record = json_value(raw)
    schema = record.get("schema") if isinstance(record, dict) else None
    if schema not in (RESOLUTION_SCHEMA, CAMPAIGN_COVERAGE_SCHEMA, AMENDED_COVERAGE_SCHEMA):
        raise PackageError("Unsupported coverage record schema")
    keys = {"schema", "planSha256", "selectedAttemptId", "frozen", "resolutions"}
    if schema != RESOLUTION_SCHEMA:
        keys.add("campaign")
    if schema == AMENDED_COVERAGE_SCHEMA:
        keys.update(("amendments", "amendedResolutions", "interpretation"))
    _object(record, keys, "Coverage record")
    if record["planSha256"] != plan_sha256 or record["selectedAttemptId"] != attempt_id:
        raise PackageError("Coverage record binds a different plan or selected attempt")
    return record


def _read_amended_version(row, versions, entries, receipts):
    obligations = _obligation_readout(
        entries, {key: version for key, version in versions.items() if version is not None},
        receipts, campaign=True,
    )
    for item in obligations:
        if item["status"] == "observed":
            item["availableBeforeAmendment"] = item["attemptId"] in row["availableAttemptIds"]
    return {"amendmentId": row["id"], "obligations": obligations}


def _amended_readout(entries, history, versions, receipts):
    if not isinstance(entries, list):
        raise PackageError("Amended resolutions must be an array")
    by_version: dict[str, list] = {revision: [] for revision in versions}
    for item in entries:
        _object(item, {"amendmentId", "resolution"}, "Amended resolution binding")
        revision = item["amendmentId"]
        if not isinstance(revision, str) or revision not in by_version:
            raise PackageError("Amended resolution references an unknown amendment")
        by_version[revision].append(item["resolution"])
    return [
        _read_amended_version(row, versions[row["id"]], by_version[row["id"]], receipts)
        for row in history
    ]


def _summarize(record, obligations, receipts, campaign):
    result = {"campaignAttemptInventory": "unassessed", "scientificInterpretation": "source-owned"}
    if campaign is not None:
        result.update(campaignAttemptInventory=campaign["scope"], campaign=campaign)
    versions = None
    if record["schema"] == AMENDED_COVERAGE_SCHEMA:
        history, versions = assess_amendments(
            record["amendments"], record["frozen"]["sha256"], obligations, campaign, receipts,
        )
        result.update(
            amendments=history,
            amendedObligations=_amended_readout(record["amendedResolutions"], history, versions, receipts),
            interpretation=assess_interpretation(record["interpretation"], obligations, versions),
        )
    readout = _obligation_readout(
        record["resolutions"], obligations, receipts, campaign=campaign is not None, versions=versions,
    )
    result.update(
        obligations=readout,
        sourceAccountedFor=all(row["status"] in {"observed", "omitted", "amended"} for row in readout),
        sourceReportedObservedAsPlanned=all(row["status"] == "observed" for row in readout),
    )
    return result


def assess_coverage(
    raw: bytes, parent_bytes: bytes, receipt: dict, attempt_id: str, *,
    load_campaign_attempt: Callable[[str], bytes] | None = None,
) -> dict:
    """Check plan-frozen obligations and return a non-scientific coverage readout."""
    record = _coverage_record(raw, receipt.get("planPackageSha256"), attempt_id)
    with zipfile.ZipFile(io.BytesIO(parent_bytes)) as parent:
        ref = receipt["planPackageArchive"]
        plan_bytes = _member(parent, ref["@id"], receipt["planPackageSha256"], "Embedded plan")
    verify_bytes(plan_bytes, expected_sha256=receipt["planPackageSha256"])
    obligations = _frozen(plan_bytes, record["frozen"])
    campaign = None
    receipts = {attempt_id: receipt}
    if "campaign" in record:
        if load_campaign_attempt is None:
            raise PackageError("Campaign coverage requires a retained attempt member reader")
        campaign, receipts = assess_campaign(
            record["campaign"], plan_sha256=receipt["planPackageSha256"], selected_id=attempt_id,
            selected_sha256=sha256(parent_bytes), selected_receipt=receipt, loader=load_campaign_attempt,
        )
    return _summarize(record, obligations, receipts, campaign)


def _coverage_graph(archive, coverage_path):
    names = archive.namelist()
    if coverage_path not in names:
        if "ro-crate-metadata.json" in names:
            crate = json_value(archive.read("ro-crate-metadata.json"))
            graph = crate.get("@graph", []) if isinstance(crate, dict) else []
            if isinstance(graph, list) and any(isinstance(node, dict) and node.get("@id") == coverage_path
                                               for node in graph):
                raise PackageError("Coverage graph entry has no retained record")
        return None
    if "ro-crate-metadata.json" not in names:
        raise PackageError("Coverage record requires a lifecycle RO-Crate")
    crate = json_value(archive.read("ro-crate-metadata.json"))
    graph = crate.get("@graph") if isinstance(crate, dict) else None
    if not isinstance(graph, list):
        raise PackageError("Coverage record requires a valid lifecycle graph")
    nodes = {}
    for node in graph:
        if not isinstance(node, dict) or not isinstance(node.get("@id"), str) or node["@id"] in nodes:
            raise PackageError("Coverage graph contains an invalid or duplicate identity")
        nodes[node["@id"]] = node
    return crate, nodes


def _coverage_action(node, nodes):
    about = node.get("about")
    action_id = about.get("@id") if isinstance(about, dict) else None
    if not isinstance(action_id, str) or not action_id.startswith("#attempt-"):
        raise PackageError("Coverage record has no selected attempt")
    action = nodes.get(action_id)
    action_type = action.get("@type") if isinstance(action, dict) else None
    if not (action_type == "CreateAction" or isinstance(action_type, list) and "CreateAction" in action_type):
        raise PackageError("Coverage record does not reference a described execution")
    return action_id


def _coverage_parent(root, node, nodes):
    derived = node.get("https://www.w3.org/ns/prov#wasDerivedFrom")
    parent_path = derived.get("@id") if isinstance(derived, dict) else None
    root_parent = root.get("https://www.w3.org/ns/prov#wasDerivedFrom")
    if not isinstance(parent_path, str) or not isinstance(root_parent, dict) or root_parent.get("@id") != parent_path:
        raise PackageError("Coverage parent conflicts with final crate provenance")
    parent_node = nodes.get(parent_path)
    if not isinstance(parent_node, dict) or parent_node.get("@type") != "File":
        raise PackageError("Coverage record lacks its exact parent package")
    return parent_path, parent_node


def inspect_coverage(package_bytes: bytes, *, expected_sha256: str) -> dict:
    """Read an exact final package; absent coverage is explicitly unassessed."""
    from .revision import _read_parent_attempt

    verify_bytes(package_bytes, expected_sha256=expected_sha256)
    coverage_path = "evidence/protocol-coverage.json"
    with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
        graph = _coverage_graph(archive, coverage_path)
        if graph is None:
            return {"status": "unassessed", "campaignAttemptInventory": "unassessed"}
        crate, nodes = graph
        node = nodes.get(coverage_path)
        if not isinstance(node, dict) or node.get("@type") != "File":
            raise PackageError("Coverage record lacks its described File entity")
        root, _ = _root_and_protocol(crate)
        parts = root.get("hasPart", [])
        parts = [parts] if isinstance(parts, dict) else parts
        if not isinstance(parts, list) or not any(isinstance(part, dict) and part.get("@id") == coverage_path
                                                  for part in parts):
            raise PackageError("Coverage record is not part of the final crate")
        raw = _member(archive, coverage_path, node.get("sha256"), "Coverage record")
        action_id = _coverage_action(node, nodes)
        parent_path, parent_node = _coverage_parent(root, node, nodes)
        parent_bytes = _member(archive, parent_path, parent_node.get("sha256"), "Parent attempt package")
        verify_bytes(parent_bytes, expected_sha256=parent_node["sha256"])
        attempt_id = action_id.removeprefix("#attempt-")
        _, receipt, _ = _read_parent_attempt(parent_bytes, attempt_id)

        def load_campaign_attempt(digest: str) -> bytes:
            name = attempt_member(digest)
            attempt_node = nodes.get(name)
            if (
                not isinstance(attempt_node, dict) or attempt_node.get("@type") != "File"
                or attempt_node.get("sha256") != digest
                or {"@id": name} not in parts
            ):
                raise PackageError("Campaign attempt lacks its exact File entity in the final crate")
            return _member(archive, name, digest, "Campaign attempt package")

        return assess_coverage(
            raw, parent_bytes, receipt, attempt_id, load_campaign_attempt=load_campaign_attempt,
        )
