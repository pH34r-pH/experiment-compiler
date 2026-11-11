"""Offline package projections of exact Long-Haul repository evidence contracts."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .core import PackageError, json_value, safe_path, sha256

SOURCE = {"repository": "pH34r-pH/long-haul", "commit": "458bc2f1ec669392963b7fe29e4c252e2e70776c"}
SCHEMAS = {
    "repository-task": "repository-work-v1.schema.json",
    "repository-context": "repository-context-v1.schema.json",
    "repository-candidate": "frozen-repository-candidate-v1.schema.json",
    "repository-evaluator-public": "repository-evaluation-v1.schema.json",
    "repository-evaluator-sealed": "repository-evaluation-v1.schema.json",
    "repository-evidence": "repository-evidence-v1.schema.json",
}
AUDIENCES = {"worker", "private-evaluator", "public-evidence"}


def projection_fingerprint(value):
    """The pinned FrozenProjection uses compact sorted JSON, not ZIP member bytes."""
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + sha256(data)


def _strict_objects(value):
    if isinstance(value, dict):
        if "properties" in value:
            value.setdefault("additionalProperties", False)
            value["required"] = list(value["properties"])  # Require complete normalized model exports.
        for item in value.values():
            _strict_objects(item)
    elif isinstance(value, list):
        for item in value:
            _strict_objects(item)


def _source_pin():
    directory = Path(__file__).parent / "profiles"
    pin = json_value((directory / "source-pin.json").read_bytes())
    if {key: pin[key] for key in SOURCE} != SOURCE:
        raise PackageError("Long-Haul schema source pin changed")
    return directory, pin


def public_conformance_packet():
    _, pin = _source_pin()
    return pin["publicConformancePacket"]["fingerprint"]


def _schema(kind):
    directory, pin = _source_pin()
    name = SCHEMAS[kind]
    raw = (directory / name).read_bytes()
    if sha256(raw) != pin["schemas"][name]:
        raise PackageError("Long-Haul schema bytes differ from their exact source pin")
    schema = copy.deepcopy(json_value(raw))
    if kind == "repository-evaluator-public":
        schema = {"type": "object", "$defs": schema["$defs"], "properties": {
            key: schema["properties"][key] for key in ("profile", "evaluator_id", "evaluator_version", "task")
        }}
        schema["properties"].update(requirements={"type": "array", "items": {"$ref": "#/$defs/AcceptanceRequirement"}},
                                    public_checks={"type": "array", "items": {"$ref": "#/$defs/RepositoryCheck"}})
        schema["required"] = list(schema["properties"])
    _strict_objects(schema)  # Legacy evidence's free-form extras are not disclosure-admitted.
    return schema


def _validate_record(kind, document):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        raise PackageError("Repository evidence profiles require the offline repository extra") from exc
    schema = _schema(kind)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise PackageError("Repository evidence record differs from pinned schema: " + kind)


def _declaration(value):
    fields = {"schemaVersion", "source", "audience", "records", "references"}
    if (not isinstance(value, dict) or set(value) != fields or value["schemaVersion"] != 1
            or type(value["schemaVersion"]) is not int or value["source"] != SOURCE
            or not isinstance(value["audience"], str) or value["audience"] not in AUDIENCES
            or not isinstance(value["records"], list)
            or not value["records"] or not isinstance(value["references"], list)):
        raise PackageError("Repository evidence declaration is incomplete or has an unpinned source")
    return value


def _records(archive, declaration):
    documents = {}
    paths = set()
    for record in declaration["records"]:
        if (not isinstance(record, dict) or set(record) != {"kind", "path", "fingerprint"}
                or not isinstance(record["kind"], str) or record["kind"] not in SCHEMAS):
            raise PackageError("Unknown repository evidence record kind")
        path = safe_path(record["path"])
        if path in paths or record["kind"] in documents:
            raise PackageError("Repository evidence records must have unique paths and kinds")
        paths.add(path)
        document = json_value(archive.read(path))
        _validate_record(record["kind"], document)
        if projection_fingerprint(document) != record["fingerprint"]:
            raise PackageError("Repository record fingerprint mismatch")
        documents[record["kind"]] = document
    # These profiles package typed evidence only. Arbitrary source, traces, logs,
    # schemas, hidden tests and unclassified payloads are never admitted members.
    from .core import MANIFEST
    if set(archive.namelist()) != paths | {MANIFEST}:
        raise PackageError("Repository evidence package contains unclassified or sealed payloads")
    return documents


def _references(references):
    for reference in references:
        fields = {"kind", "source", "reference", "digest", "qualification"}
        if (not isinstance(reference, dict) or set(reference) != fields
                or reference["kind"] not in ("semantic-trajectory", "governed-corpus")
                or reference["qualification"] != "upstream-pending"
                or not isinstance(reference["source"], dict)
                or set(reference["source"]) != {"repository", "commit"}
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(reference["source"]["repository"]))
                or not re.fullmatch(r"[0-9a-f]{40}", str(reference["source"]["commit"]))
                or not re.fullmatch(r"(urn|artifact|git):[A-Za-z0-9._:/+@-]+", str(reference["reference"]))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(reference["digest"]))):
            raise PackageError("Trajectory/corpus references require exact upstream identity; payloads are not qualified")


def validate_agent_profiles(archive, manifest):
    declaration = manifest["evidence"].get("repositoryAgent")
    if declaration is None:
        return None
    declaration = _declaration(declaration)
    documents = _records(archive, declaration)
    _references(declaration["references"])
    from .agent_disclosure import validate_bindings, validate_disclosure
    validate_bindings(documents)
    validate_disclosure(documents, declaration["audience"])
    return {"schemaVersion": 1, "source": SOURCE, "audience": declaration["audience"],
            "records": declaration["records"], "references": declaration["references"],
            "scope": "schema-identity-and-disclosure-only", "acceptance": "not-evaluated",
            "trainingEligibility": "not-granted"}
