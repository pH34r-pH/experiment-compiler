---
schema_version: 1
document_id: experiment-compiler/overview
role: overview
revision: 1
target_status: mature-complete-product
approval_status: proposed
artifact_acceptance: unspecified
delivery_status: designed-only
delivery_evidence: []
qualifiers: [living-spec-paradigm, brownfield-onboarded]
---

# Experiment Compiler

Experiment Compiler compiles reviewed research inputs into portable, inspectable, integrity-verifiable Compiled Experiment packages. It packages explicit source bytes, standards metadata, dependency and resource closure, and provenance, verifies those packages without inventing scientific semantics, and provides a bounded execution handoff for reviewed lifecycle packages. It does not define the science, fill in a missing method, infer a conclusion or require a hosted service.

This specification is being onboarded one area at a time. The [archive read policy](features/archive-read-policy/index.md) is the first specified area. Every other part of the repository, including recipe loading, assembly, verification, the runner, revision, finalization and catalog projection, is explicitly unspecified until it is onboarded; the existing documentation under `docs/` remains descriptive guidance, not specification authority.
