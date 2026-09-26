# #164 standards gap register — pass 2

Issue: #342

This register records only unresolved or demonstrated gaps discovered while encoding the frozen #164 AdamW experiment. Absence from this file means the competency is either covered by an existing standard/profile or has not yet been evaluated.

## Rules

- **UNRESOLVED** is not a standards gap.
- Promote to **GAP** only with primary-specification evidence that the required scientific semantic cannot be expressed natively or via the standard's supported profile/extension mechanism.
- Do not create a local field/schema merely to make the encoding aesthetically complete.

## Current register

| Competency | Status | Evidence / next test |
|---|---|---|
| Optimizer algorithm identity (AdamW/Muon as a scientific treatment) | UNRESOLVED | Croissant Tasks provides TaskProblem/TaskSolution/implementation abstractions, but its published focus is evaluation. Check current Tasks schema plus MLSpec/ML-Schema training metadata before extending. |
| Exact optimizer parameter selection/grouping | UNRESOLVED | Critical for future Muon lane. Test whether existing task/training metadata can identify semantic parameter roles rather than implementation-only tensor-name filters. |
| LR schedule / clipping / update semantics | UNRESOLVED | MLSpec explicitly covers training/tuning metadata and hyperparameters at a broad level; test whether semantics are interoperable enough for conceptual reproduction. |
| RNG stream semantics | UNRESOLVED | Seeds can be recorded as execution parameters, but algorithm/stream partition semantics may need a training profile. |
| Shared-parameter recurrent-depth identity | UNRESOLVED | Can always be preserved by reference implementation; test whether conceptual task description can express it independently enough for a second implementation. |
| Unit-hypersphere/tangent update identity | UNRESOLVED | Same issue: implementation is preserved technically; conceptual reproduction needs a stable high-level description. |
| Sealed-test non-access | UNRESOLVED | Historical result records `test_accessed=false`; determine whether policy/constraint semantics should live in Task Problem, provenance, or both. |
| Paired source-document bootstrap + simultaneous family | UNRESOLVED | Reproducible as code/artifact today; determine whether conceptual reproduction requires formal statistical-method metadata or a cited executable method artifact is sufficient. |

## Confirmed non-gaps

- Research-object packaging: RO-Crate / Process Run Crate.
- Historical execution identity: Process Run Crate `CreateAction`.
- Input/output artifact linkage: `object` / `result`.
- Source implementation/version: `SoftwareSourceCode` / `SoftwareApplication`.
- Dataset identity/structure: Croissant.
- Detailed retrospective step provenance, if needed: Provenance Run Crate.
- Lower compiler/backend IR: existing ONNX/StableHLO/MLIR ecosystem; out of scope for local invention.


## Pass 3 — schema-level findings

### Croissant Tasks

The current upstream ontology and SHACL implementation were inspected directly at commit `401f6fff81db26a49c0d1704f02bffc4e4fa8fe2`.

The current task vocabulary contains:
- `TaskProblem`, `TaskSolution`, `EvaluationTask`, `EvaluationResult`;
- `input`, `output`, `implementation`, `execution`, `evaluation`, `subTask`;
- `InputSpec`, `OutputSpec`, `ImplementationSpec`, `ExecutionSpec`, `EvaluationSpec`;
- implementation requirements via `tests` and `environment`.

It contains **no optimizer or training-procedure vocabulary**. Repository search for `optimizer` finds ordinary example/notebook code, not a Croissant Tasks ontology term. Therefore the first problem/solution files in this directory intentionally do not encode AdamW, LR, clipping, parameter groups, RNG streams, or the 128-update regime as invented Croissant properties.

This establishes a **Croissant Tasks coverage gap**, but not yet a need for a new DSL schema: another existing standard/profile may supply the semantics, or an upstream Croissant Tasks training profile may be the appropriate remedy.

### MLSpec / ML-Schema

MLSpec explicitly recognizes model training/tuning and its repository contains training metadata fields such as `learning_rate`, `loss`, `batch_size`, `epoch`, `optimizer`, and generic hyperparameter key/value lists.

That is sufficient evidence that MLSpec can **record** an optimizer name and ordinary training hyperparameters. It is not yet evidence of portable algorithm semantics:
- `optimizer` is represented as generic/example metadata rather than a normative AdamW/Muon algorithm identity;
- no inspected construct specifies semantic parameter-role selection/grouping;
- generic hyperparameter key/value maps do not define RNG stream semantics, optimizer update equations, or cross-framework equivalence.

Accordingly:

| Competency | Updated status |
|---|---|
| record optimizer name / LR / ordinary hyperparameters | NATIVE metadata in MLSpec |
| portable AdamW/Muon algorithm semantics | **CANDIDATE GAP** |
| semantic optimizer parameter grouping/selection | **CANDIDATE GAP** |
| named RNG stream semantics | **CANDIDATE GAP** |
| update/schedule/clipping semantics sufficient for independent implementation | **CANDIDATE GAP** |

`CANDIDATE GAP` means the two strongest identified high-level standards were checked and neither currently provides the required normative semantics. Before creating local vocabulary, #342 should search specifically for an existing optimizer/training-procedure ontology or interoperable training-plan standard, and otherwise prefer proposing an upstream profile/extension.

### Process Run Crate correction

The first crate skeleton originally targeted Process Run Crate 0.3 / RO-Crate 1.1. The current primary specification is Process Run Crate 0.6 over RO-Crate 1.3. The encoding has been corrected to:
- use both the RO-Crate 1.3 and workflow-run JSON-LD contexts;
- put base-profile conformance on the metadata descriptor;
- put Process Run Crate 0.6 conformance on the root Dataset;
- include the Process Run profile CreativeWork entity;
- mark the historical action completed.

This correction is exactly why standards validation precedes adding local semantics.


## Pass 4 — focused optimizer/training-plan prior-art search

A focused search was performed for optimizer ontologies, training-plan interchange formats, hyperparameter ontologies, and cross-framework optimizer specifications.

### OpenML Flow / Setup

OpenML is important prior art and narrows the gap:
- a **Flow** describes an algorithm/model/pipeline, including components, dependencies, implementation/library version, parameter names/defaults and parameter metadata;
- a **Setup** is a Flow with all hyperparameter values fixed;
- OpenML can reinstantiate a run's model with the same parameter settings when the corresponding framework/connector implementation is available.

This is strong **implementation-bound reproducibility** and is more semantically structured than a generic key/value run log.

However, OpenML's own flow-serialization model intentionally treats the workbench/library implementation as part of identity: flow name + external version identify the implementation, and reinstantiation requires the correct library/connector version. Hyperparameter values live at setup/run level. This does not supply a framework-independent mathematical definition of AdamW/Muon or prove equivalence of two independent implementations.

Therefore OpenML should be recorded as prior art for:
- algorithm/component identity;
- hierarchical hyperparameter definitions;
- exact configured setup identity;
- framework-specific reinstantiation.

It does **not** eliminate the candidate gap for conceptual/cross-framework optimizer semantics.

### Framework-specific TrainingPlan abstractions

Frameworks such as scvi-tools explicitly expose a `TrainingPlan` object with optimizer choice (including AdamW or custom creator) and hyperparameters. This confirms that "training plan" is established framework vocabulary, not a novel DSL concept. Like OLMo/TorchTitan configs, it is an implementation API rather than an interchange standard.

### Updated gap statement

The strongest defensible gap is now narrower:

> We have not found a maintained, framework-independent standard that gives optimizer/training algorithms a normative semantic identity precise enough for independent implementations to claim equivalence, while also describing semantic parameter-role selection/grouping, update schedule/clipping order, and named RNG streams.

Existing systems cover adjacent layers:
- Croissant Tasks: problem/solution/execution/evaluation shape;
- MLSpec: broad training metadata and hyperparameters;
- OpenML Flow/Setup: structured algorithm + configured hyperparameters with framework/version-bound reinstantiation;
- training frameworks: executable training-plan/config APIs;
- ONNX/StableHLO: lower computation after high-level optimizer identity can disappear.

This is still **CANDIDATE GAP**, not permission to invent a local standard.

### Preferred next action if the gap survives

Do not create `DSLTrainingPlan`. First investigate whether Croissant Tasks' existing `ImplementationSpec`, `ExecutionSpec`, `subTask`, and extension/profile mechanism can host a small **training-task profile** whose optimizer identity points to stable algorithm definitions and whose concrete TaskSolutions remain framework-specific.

For #164, technical reproducibility remains satisfied by the historical implementation + Process Run Crate. The profile is only needed if conceptual reproduction from an independent implementation cannot otherwise preserve the frozen training semantics.


## Pass 5 — AdamW semantic materiality test

The candidate optimizer-semantics gap was tested against current PyTorch, Optax/JAX, and Keras AdamW definitions plus the frozen #164 implementation.

Result: **scientifically material for conceptual reproduction**.

The bare name `AdamW` does not uniquely determine a cross-framework training treatment:
- PyTorch #164 uses one full-model parameter group, LR 1e-3, weight decay 1e-2, PyTorch defaults for betas/epsilon/variants, and external global-norm clipping before `step()`.
- Optax exposes both `eps` and `eps_root`, optional accumulator dtype, decay masks, and Nesterov; it explicitly documents a weight-decay scaling convention that differs from the original AdamW paper while matching PyTorch.
- Keras defaults to epsilon 1e-7 and weight decay 0.004, and its optimizer API can own clipping, gradient accumulation, EMA and loss scaling.

Therefore:
- technical reproduction remains NATIVE through exact implementation/environment identity;
- optimizer name/basic hyperparameters remain NATIVE metadata;
- **framework-independent conceptual optimizer semantics is promoted from CANDIDATE GAP to CONFIRMED INTEROPERABILITY GAP for #342's use case**.

This does **not** justify a new general optimizer language. The minimum missing contract is a way for a Task Problem to require a stable algorithm definition plus parameter-role selection, ordered update transforms, schedule clock, and relevant numerical state, while Task Solutions remain framework-specific implementations and can be checked by conformance fixtures.

See `adamw_semantic_audit.md`.
