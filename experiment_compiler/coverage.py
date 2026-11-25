"""Validate a source-authored, selected-attempt protocol coverage record.

This first version accounts for obligations against one verified attempt. It
does not certify a campaign census, interpret results, or infer omitted science.
"""
from __future__ import annotations

import io
import re
import zipfile

from .core import PackageError, json_value, safe_path, sha256, verify_bytes
from .revision import _root_and_protocol

FROZEN_SCHEMA = "compiled-experiment-obligations/v1"
RESOLUTION_SCHEMA = "compiled-experiment-coverage/v1"
ROLES = {"primary", "secondary", "control", "diagnostic", "exclusion", "stopping-rule"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise PackageError(f"{label} has missing or unsupported fields")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise PackageError(f"{label} must be nonempty bounded text")
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
        _text(item["applicability"], "Obligation applicability")
        result[identifier] = item
    return result


def _resolution(item, obligations, receipt, attempt_id):
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
        _text(item["reason"], "Omission or pending reason")
    elif status == "observed":
        if item["attemptId"] != attempt_id:
            raise PackageError("Observed coverage references a different attempt")
        path = safe_path(item["outputPath"])
        outputs = [out for out in receipt.get("outputs", []) if isinstance(out, dict) and out.get("@id") == path]
        if len(outputs) != 1 or outputs[0].get("sha256") != item["outputSha256"]:
            raise PackageError("Observed coverage does not reference an exact selected-attempt output")
        _text(item["finding"], "Source-authored finding")
    return identifier


def assess_coverage(raw: bytes, parent_bytes: bytes, receipt: dict, attempt_id: str) -> dict:
    """Check plan-frozen obligations and return a non-scientific coverage readout."""
    record = _object(json_value(raw), {"schema", "planSha256", "selectedAttemptId", "frozen", "resolutions"},
                     "Coverage record")
    if record["schema"] != RESOLUTION_SCHEMA:
        raise PackageError("Unsupported coverage record schema")
    if record["planSha256"] != receipt.get("planPackageSha256") or record["selectedAttemptId"] != attempt_id:
        raise PackageError("Coverage record binds a different plan or selected attempt")
    with zipfile.ZipFile(io.BytesIO(parent_bytes)) as parent:
        ref = receipt["planPackageArchive"]
        plan_bytes = _member(parent, ref["@id"], receipt["planPackageSha256"], "Embedded plan")
    verify_bytes(plan_bytes, expected_sha256=receipt["planPackageSha256"])
    obligations = _frozen(plan_bytes, record["frozen"])
    entries = record["resolutions"]
    if not isinstance(entries, list):
        raise PackageError("Coverage resolutions must be an array")
    resolved = {}
    for item in entries:
        identifier = _resolution(item, obligations, receipt, attempt_id)
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
    statuses = [item["status"] for item in readout]
    return {"obligations": readout, "sourceAccountedFor": all(s in {"observed", "omitted"} for s in statuses),
            "sourceReportedObservedAsPlanned": all(s == "observed" for s in statuses),
            "campaignAttemptInventory": "unassessed", "scientificInterpretation": "source-owned"}


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
    return assess_coverage(raw, parent_bytes, receipt, attempt_id)
