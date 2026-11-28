"""Synthetic cross-record compositions; not new executions of upstream evidence."""
import copy
import hashlib
import json
from datetime import datetime, timezone
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


def retained_trajectory(task, terminal, *, repository_id=None, candidate=None):
    """Produce canonical synthetic evidence using the actual pinned upstream models."""
    from long_haul.work.harness import (
        HarnessAttempt, HarnessCleanup, HarnessState, SemanticActionEventV1, semantic_trace_bytes,
    )
    from long_haul.work.repository_models import RepositoryWorkExtensionV1

    extension = RepositoryWorkExtensionV1.model_validate(task)
    assert extension.fingerprint == projection_fingerprint(task)
    attempt = HarnessAttempt(
        attempt_id="attempt", contract_id="contract", mission_id="mission", session_id="session",
        crew_id="crew", repository_id=repository_id or extension.source.repository_id,
        contract_digest=reference("contract")["digest"], repository_extension_digest=extension.fingerprint,
        context_manifest=extension.context_manifest, harness={
            "harness_id": "fixture", "package_name": "fixture", "package_version": "1", "protocol": "fixture-v1",
            "source": reference("source"), "artifact": reference("artifact"), "configuration": reference("configuration"),
        },
    )
    events = () if terminal == "provider_failure" else (SemanticActionEventV1(
        attempt_id=attempt.attempt_id, session_id=attempt.session_id, contract_id=attempt.contract_id,
        native_id_mode="none", sequence=1, wire_sequence=1, phase="lifecycle", tool="unknown", action="unknown",
        timestamp=datetime(2026, 11, 27, tzinfo=timezone.utc), outcome="unknown", progress="no_progress",
        arguments={"argument_fingerprint": "hmac-sha256:" + "0" * 64}, summary="settled",
        consequence="unknown", policy_receipt=reference("policy"), normalization=reference("normalization"),
        harness_digest=attempt.harness.fingerprint,
    ),)
    state = HarnessState(attempt)
    state.settle(terminal)
    cleanup = HarnessCleanup(attempt_id=attempt.attempt_id, session_id=attempt.session_id, boundary="fixture",
                             process_tree_stopped=True, evidence=reference("cleanup"))
    return state.finish(events, cleanup, candidate), semantic_trace_bytes(events)


def projected_trajectory(task):
    """Produce synthetic wire events through the pinned upstream projector."""
    from datetime import timedelta

    from long_haul.adapters.fixture_harness import FixtureCodingHarness
    from long_haul.adapters.harness_trace import TraceNormalizationPolicy, TraceNormalizer, TraceUseAdmission
    from long_haul.resource_policy import PolicyRule, ResourceIdentity, ResourcePolicyProfile, ResourceUseRequest
    from long_haul.work import WorkContract, WorkSession
    from long_haul.work.harness import HarnessCleanup, HarnessIdentity, harness_attempt, semantic_trace_bytes
    from long_haul.work.repository_models import DigestReference, content_digest

    def digest_reference(name):
        return DigestReference(reference="urn:fixture:" + name, digest=content_digest(name.encode()))

    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    contract = WorkContract(
        contract_id="contract", mission_id="mission", objective="synthetic fixture",
        allowed_paths=["fixture"], capabilities=["read"], budget={"wall_seconds": 30},
        repository_work=task, success_predicates=[{
            "predicate_id": "tests", "kind": "test", "description": "external fixture",
            "command": ["trusted-check"],
        }],
    )
    session = WorkSession(contract_id="contract", crew_id="crew", session_id="session")
    identity = HarnessIdentity(
        harness_id="fixture", package_name="fixture", package_version="1", protocol="fixture-v1",
        source=digest_reference("source"), artifact=digest_reference("artifact"),
        configuration=digest_reference("configuration"),
    )
    attempt = harness_attempt(contract, session, identity, "attempt")
    profile = ResourcePolicyProfile(
        identity=ResourceIdentity(resource_id="urn:fixture:trace", resource_type="operational-trace"),
        authority_class="authoritative-control", lifecycle="active", policy_id="urn:fixture:policy",
        policy_assigner="urn:fixture:operator", permissions=[PolicyRule(
            rule_id="trace", actions=["annotate"], purposes=["operational-trace"],
            crew_ids=["crew"], contract_ids=["contract"],
        )],
    )
    request = ResourceUseRequest(
        request_id="trace-request", resource_id=profile.identity.resource_id, caller_id="session",
        crew_id="crew", contract_id="contract", mission_id="mission", repository_id=attempt.repository_id,
        action="annotate", purpose="operational-trace", now=now,
    )
    normalizer = TraceNormalizer(
        contract, attempt, TraceNormalizationPolicy(key_reference=digest_reference("key")),
        b"synthetic fixture key material 32 bytes long!", TraceUseAdmission(profile, profile.fingerprint, request),
    )
    records = (
        {"type": "tool_execution_start", "toolCallId": "one", "toolName": "read",
         "args": {"path": "fixture/code.py"}},
        {"type": "tool_execution_end", "toolCallId": "one", "toolName": "read", "isError": False,
         "result": {"content": [{"type": "text", "text": "SYNTHETIC_PRIVATE_CANARY"}]}},
        {"type": "agent_settled", "aborted": False},
    )
    harness = FixtureCodingHarness(normalizer, records)
    harness.start(attempt)
    for index in range(len(records)):
        harness.poll(now + timedelta(milliseconds=index), index * 1_000_000)
    cleanup = HarnessCleanup(
        attempt_id=attempt.attempt_id, session_id=attempt.session_id, boundary="fixture",
        process_tree_stopped=True, evidence=digest_reference("cleanup"),
    )
    return harness.finish(cleanup), semantic_trace_bytes(harness.projector.events)
