"""Disclosure admission and cross-record identity; scientific acceptance stays external."""
from __future__ import annotations

import re

from .core import PackageError

PUBLIC_CONFORMANCE_PACKET = "sha256:87f1a81ab8207ce271752e79cd93e8f1c18bd282a035e2ab56c498fd2d3aec66"


def _require(condition, reason):
    if not condition:
        raise PackageError(reason)


def _public_sources(value):
    if isinstance(value, dict):
        if "repository_id" in value:
            _require(value.get("visibility") == "public" and value.get("data_class") == "public",
                     "Private repository identities cannot enter public evidence packages")
        if "data_class" in value:
            _require(value["data_class"] == "public", "Private context cannot enter public packages")
        for item in value.values():
            _public_sources(item)
    elif isinstance(value, list):
        for item in value:
            _public_sources(item)


def _public_packet(packet):
    checks = {item["predicate_id"]: item for item in packet["checks"]}
    _require(len(checks) == len(packet["checks"]), "Duplicate repository observations")
    legacy = packet["evidence"]
    ids = [item["predicate_id"] for item in legacy.get("checks", [])]
    _require(len(ids) == len(set(ids)) and set(ids) == set(checks), "Legacy observations differ from typed evidence")
    for item in legacy.get("checks", []):
        expected = checks.get(item["predicate_id"])
        _require(expected is not None and item.get("command", []) == []
                 and item.get("detail", "") == "repository check " + expected["outcome"]
                 and item["state"] == expected["state"], "Raw commands/private diagnostic text are not public evidence")
    for key in ("verified", "unresolved", "regressions"):
        _require(set(legacy.get(key, [])) <= set(checks), "Legacy free text is not a predicate reference")
    _require(set(legacy["verified"]) == {key for key, item in checks.items() if item["state"] == "PASS"}
             and set(legacy["unresolved"]) == {key for key, item in checks.items() if item["state"] == "UNKNOWN"},
             "Legacy certification differs from typed evidence")
    _public_outcomes(packet, checks)


def _public_outcomes(packet, checks):
    outcomes = {"pass": "PASS", "fail": "FAIL"}
    for item in checks.values():
        _require(item["state"] == outcomes.get(item["outcome"], "UNKNOWN"), "Outcome contradicts predicate state")
        for key in ("check_fingerprint", "stdout_digest", "stderr_digest"):
            _require(item.get(key) is None or re.fullmatch(r"sha256:[0-9a-f]{64}", item[key]),
                     "Raw trace payload is not a content fingerprint")
    known_reasons = {"candidate scope violation", "evaluator exceeded work-contract wall budget"}
    for item in checks.values():
        reason = item["predicate_id"] + ":" + item["outcome"]
        known_reasons.add(reason + (":" + item["diagnostic"] if item.get("diagnostic") else ""))
    _require(set(packet.get("invalid_reasons", [])) <= known_reasons, "Raw private invalidation text is not admitted")


def validate_disclosure(documents, audience):
    sealed = documents.get("repository-evaluator-sealed")
    if audience != "private-evaluator":
        _require(sealed is None, "Sealed evaluator definitions cannot enter worker/public packages")
        _public_sources(documents)
    public = documents.get("repository-evaluator-public")
    if public is not None:
        _require(all(check["requirement"]["classification"] == "PUBLIC" for check in public["public_checks"]),
                 "Public evaluator projection contains a sealed check")
    if audience == "worker":
        _require("repository-evidence" not in documents, "Worker package cannot carry evaluator outcome evidence")
    if "repository-evidence" in documents and audience != "private-evaluator":
        _public_packet(documents["repository-evidence"])
    context = documents.get("repository-context")
    if context is not None and audience != "private-evaluator":
        _require(context["selection"].get("admitted_data_classes", ["public"]) == ["public"],
                 "Public context admission includes private data classes")
    if audience != "private-evaluator":
        _private_commitments(documents)


def _private_commitments(documents):
    """Bare private-check digests leak low-entropy inputs; await upstream #245."""
    from .agent_profiles import projection_fingerprint
    task = documents.get("repository-task")
    public = documents.get("repository-evaluator-public")
    packet = documents.get("repository-evidence")
    if task is not None:
        _require(task.get("sealed_evaluator") is None,
                 "Private evaluator commitments are not qualified for worker/public disclosure")
    if public is not None:
        _require(all(item["classification"] != "SEALED" for item in public["requirements"]),
                 "Private evaluator requirements await a qualified guarded upstream projection")
    if packet is not None:
        _require(projection_fingerprint(packet) == PUBLIC_CONFORMANCE_PACKET,
                 "Only the exact already-public #243 conformance packet is disclosure-qualified")
    if "repository-candidate" in documents:
        _require(packet is not None and documents["repository-candidate"] == packet["candidate"],
                 "Transitive candidate commitments require qualified disclosure evidence")


def _matching(actual, expected, label):
    _require(actual == expected, "Stale or mismatched repository evidence " + label)


def validate_bindings(documents):
    from .agent_profiles import projection_fingerprint
    task = documents.get("repository-task")
    context = documents.get("repository-context")
    public = documents.get("repository-evaluator-public")
    sealed = documents.get("repository-evaluator-sealed")
    candidate = documents.get("repository-candidate")
    packet = documents.get("repository-evidence")
    if task is not None:
        for document, field in ((context, "context_manifest"), (public, "public_evaluation"), (sealed, "sealed_evaluator")):
            if document is not None:
                _require(task.get(field) is not None, "Missing evaluator/context reference for retained record")
                _matching(task[field]["digest"], projection_fingerprint(document), field)
        if context is not None:
            _matching((task["source"], task["task"], task["environment"]),
                      (context["source"], context["selection"]["task"], context["selection"]["environment"]), "context binding")
    if packet is not None:
        _matching(packet["evidence"]["candidate"]["fingerprint"], projection_fingerprint(packet["candidate"]), "candidate fingerprint")
        _matching(packet["evidence"]["candidate"]["reference"], "git:" + packet["candidate"]["source"]["commit"], "candidate commit")
        _matching(packet["contract_fingerprint"], packet["candidate"]["contract_fingerprint"], "contract")
        if candidate is not None:
            _matching(packet["candidate"], candidate, "candidate record")
        if task is not None:
            _matching((packet["task"], packet["context_manifest"], packet["candidate"]["base"]),
                      (task["task"], task["context_manifest"], task["source"]), "task/context/source")
            _matching(packet["evaluator_profile"], task["sealed_evaluator"], "sealed evaluator reference")
        for document, field in ((sealed, "evaluator_profile"), (context, "context_manifest")):
            if document is not None:
                _matching(packet[field]["digest"], projection_fingerprint(document), field)
        observed = {item["predicate_id"]: item for item in packet["checks"]}
        for check in (sealed["checks"] if sealed is not None else public["public_checks"] if public is not None else []):
            actual = observed.get(check["requirement"]["predicate_id"])
            _require(actual is not None, "Evaluator check lacks a retained observation")
            _matching((actual["check_fingerprint"], actual["classification"]),
                      (projection_fingerprint(check), check["requirement"]["classification"]), "check identity")
