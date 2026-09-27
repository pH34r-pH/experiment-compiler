#!/usr/bin/env python3
"""Validate the current RO-Crate 1.3 / Process Run Crate 0.6 MUST contract.

roc-validator 0.11.2 currently ships Process Run Crate 0.5 and no RO-Crate
1.3 profile, so this small fail-closed check covers the current profile's
normative MUSTs that are relevant to this crate. It does not claim to replace
a future official 1.3/0.6 validator.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RO = "https://w3id.org/ro/crate/1.3"
PROCESS = "https://w3id.org/ro/wfrun/process/0.6"
CONTEXT = "https://w3id.org/ro/crate/1.3/context"
RUN_CONTEXT = "https://w3id.org/ro/terms/workflow-run/context"


def fail(message: str) -> None:
    raise SystemExit("current RO profile validation failed: " + message)


def one_id(value):
    if isinstance(value, dict) and set(value) == {"@id"} and isinstance(value["@id"], str):
        return value["@id"]
    fail("expected a single @id reference")


def refs(value):
    items = value if isinstance(value, list) else [value]
    return [one_id(item) for item in items]


def types(entity):
    value = entity.get("@type")
    return set(value if isinstance(value, list) else [value])


def local_file(root: Path, identifier: str) -> None:
    if identifier.startswith(("#", "http://", "https://")):
        return
    path = root / identifier
    if not path.is_file():
        fail(f"referenced local file is absent: {identifier}")


def main(argv=None) -> int:
    if len(sys.argv if argv is None else argv) != 2:
        fail("usage: validate_current_ro_profiles.py CRATE_DIR")
    root = Path((sys.argv if argv is None else argv)[1]).resolve()
    metadata = json.loads((root / "ro-crate-metadata.json").read_text())
    if metadata.get("@context") != [CONTEXT, RUN_CONTEXT]:
        fail("crate must use the RO-Crate 1.3 and workflow-run contexts")

    graph = metadata.get("@graph")
    if not isinstance(graph, list):
        fail("@graph must be an array")
    entities = {}
    for entity in graph:
        if not isinstance(entity, dict) or not isinstance(entity.get("@id"), str):
            fail("every graph entity must have a string @id")
        if entity["@id"] in entities:
            fail("duplicate graph @id: " + entity["@id"])
        entities[entity["@id"]] = entity

    descriptor = entities.get("ro-crate-metadata.json")
    root_entity = entities.get("./")
    if not descriptor or not root_entity:
        fail("metadata descriptor and root dataset are required")
    if "CreativeWork" not in types(descriptor):
        fail("metadata descriptor must be CreativeWork")
    if one_id(descriptor.get("about")) != "./":
        fail("metadata descriptor must describe the root dataset")
    if one_id(descriptor.get("conformsTo")) != RO:
        fail("metadata descriptor must declare RO-Crate 1.3")

    if "Dataset" not in types(root_entity):
        fail("root entity must be Dataset")
    for key in ("name", "description", "datePublished", "license"):
        if key not in root_entity:
            fail("root entity missing required property: " + key)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", root_entity["datePublished"]):
        fail("datePublished must be an ISO 8601 date")
    root_profiles = set(refs(root_entity.get("conformsTo", [])))
    if PROCESS not in root_profiles:
        fail("root entity must declare Process Run Crate 0.6")
    license_id = one_id(root_entity["license"])
    if license_id not in entities:
        fail("root license must be described as a contextual entity")
    for identifier in refs(root_entity.get("hasPart", [])):
        local_file(root, identifier)

    profile = entities.get(PROCESS)
    if not profile or "CreativeWork" not in types(profile) or profile.get("version") != "0.6":
        fail("Process Run Crate 0.6 profile entity is required")

    actions = [entity for entity in graph if "CreateAction" in types(entity)]
    if not actions:
        fail("Process Run Crate requires at least one CreateAction")
    for action in actions:
        instrument_id = one_id(action.get("instrument"))
        instrument = entities.get(instrument_id)
        if not instrument:
            fail("CreateAction instrument is not described: " + instrument_id)
        if not ({"SoftwareApplication", "SoftwareSourceCode"} & types(instrument)):
            fail("CreateAction instrument must describe executable software")
        for relation in ("object", "result"):
            if relation in action:
                for identifier in refs(action[relation]):
                    if identifier not in entities:
                        fail(f"{relation} entity is not described: {identifier}")
                    local_file(root, identifier)

    print(json.dumps({
        "profile": PROCESS,
        "roCrate": RO,
        "status": "current-profile-required-contract-passed",
        "createActions": len(actions),
        "note": "Checks current normative MUSTs relevant to this crate; not a substitute for a future official 1.3/0.6 validator."
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
