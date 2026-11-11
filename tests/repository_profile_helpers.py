"""Synthetic cross-record compositions; not new executions of upstream evidence."""
import copy
import hashlib
import json
from pathlib import Path

from experiment_compiler.agent_profiles import projection_fingerprint


def reference(name):
    return {"reference": "urn:fixture:" + name, "digest": "sha256:" + "1" * 64}


def composed_records(packet):
    packet = copy.deepcopy(packet)
    environment = json.loads((Path(__file__).parent / "fixtures/repository-evaluator-environment.json").read_bytes())["environment"]
    environment.update(runtime="long-haul-gvisor", runtime_entrypoint="runsc", network="none",
                       candidate_access="read-only", toolchain="isolated-python3")
    environment["limits"]["cpus"] = float(environment["limits"]["cpus"])
    checks = []
    for name, classification, stdin, expected in (("public", "PUBLIC", "[1, 2]\n", b"3\n"),
                                                 ("sealed", "SEALED", "[-5, 2]\n", b"-3\n")):
        checks.append({"requirement": {"predicate_id": name, "description": classification + " arithmetic behavior",
            "classification": classification}, "kind": "command", "artifact_path": None, "artifact_digest": None,
            "command": {"program": "program.py", "arguments": [], "stdin": stdin,
                "expected_stdout_digest": "sha256:" + hashlib.sha256(expected).hexdigest(),
                "expected_exit_code": 0, "result_schema": "bounded-bytes-v1"}})
    for name, classification, description, kind in (("scope", "POLICY", "Changed paths stay within contract", "scope"),
            ("inspect", "INSPECTION", "Reviewed evidence remains exact", "artifact")):
        artifact = next(item for item in packet["candidate"]["files"] if item["path"] == "README.md")
        checks.append({"requirement": {"predicate_id": name, "description": description, "classification": classification},
            "kind": kind, "command": None, "artifact_path": "README.md" if kind == "artifact" else None,
            "artifact_digest": artifact["digest"] if kind == "artifact" else None})
    sealed = {"schema_version": 1, "profile": "repository-evaluation-v1", "evaluator_id": "fixture-arithmetic",
        "evaluator_version": "1", "task": packet["task"], "environment": environment,
        "implementation": reference("implementation"), "sandbox": packet["sandbox_implementation"],
        "checks": checks, "deterministic": True, "seed": 0, "publication_policy": "requirements-and-hashed-outcomes"}
    public = {key: sealed[key] for key in ("profile", "evaluator_id", "evaluator_version", "task")}
    public.update(requirements=[item["requirement"] for item in checks], public_checks=[checks[0]])
    context = {"schema_version": 1, "profile": "repository-context-v1", "source": packet["candidate"]["base"],
        "selection": {"task": packet["task"], "environment": reference("worker-environment"),
            "budget": {"max_bytes": 8192, "max_items": 16, "max_tokens": None, "max_delivery_bytes": 16384},
            "lookups": [], "use": {"caller_id": "fixture-caller", "crew_id": "fixture-crew",
                "contract_id": "fixture-contract", "mission_id": "fixture-mission", "purpose": "fixture"},
            "created_by": reference("selector"), "created_at": "2026-11-11T00:00:00Z",
            "admitted_data_classes": ["public"], "admitted_authority_classes": ["repository-reference"]},
        "provider": {"name": "fixture", "version": "1", "tool_versions": [],
            "implementation": reference("provider"), "configuration": []}, "items": [], "omissions": []}
    packet["context_manifest"]["digest"] = projection_fingerprint(context)
    packet["evaluator_profile"]["digest"] = projection_fingerprint(sealed)
    task = {"schema_version": 1, "profile": "repository-work-v1", "source": packet["candidate"]["base"],
        "task": packet["task"], "environment": reference("worker-environment"), "toolchain": reference("toolchain"),
        "context_manifest": packet["context_manifest"], "public_evaluation": {
            "reference": "urn:long-haul:repository-evaluator:public", "digest": projection_fingerprint(public)},
        "sealed_evaluator": packet["evaluator_profile"], "candidate_form": "tree", "dependency_policy": "offline",
        "cache_policy": "disabled", "dependency_cache": None, "network_policy": "none", "network_profile": None,
        "partition_references": []}
    return {"repository-task": task, "repository-context": context, "repository-evaluator-public": public,
            "repository-evaluator-sealed": sealed, "repository-evidence": packet}
