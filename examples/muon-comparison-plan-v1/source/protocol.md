# Prospective protocol: controlled optimizer comparison

## Question

Under matched model, data, objective, precision, update or compute budget, and selection policy, does a source-owner-selected Muon treatment change the learning trajectory and held-out quality relative to the existing AdamW result, rather than only producing a favorable endpoint?

## Hypothesis and status

The candidate hypothesis is that a specified Muon treatment may improve trajectory and held-out quality per matched training budget. This direction is not a confirmed claim. The exact treatment, control compatibility, estimand, analysis, and decision criteria remain proposed and unfrozen.

## Candidate controls and outcomes

- Candidate historical control: the existing DLS issue #164 `unit_hypersphere_depth3` AdamW arm (seeds 17, 31, 47; 128 updates; reported endpoint validation NLL/original byte 3.658432). Issue #18 documents the historical result and its learning-curve caveat. A fresh bit-identical replay is not required just to begin the Muon work.
- Control-use rule: before making a controlled optimizer claim, verify that the historical arm's model, data and split, initialization, objective, precision, update budget, evaluation, and source closure match this protocol. The retained trace records seeds and outputs but no trained checkpoints; cross-implementation initialization correspondence is not yet established. If the historical arm cannot be shown to match, run a contemporaneous AdamW arm from the same declared initial states as Muon, or describe the result as a comparison with a historical reference rather than a matched optimizer effect.
- Candidate Muon treatment: one implementation and named-parameter group policy selected and frozen by the source owner. Each optimizer arm starts from the same declared initial state; do not switch from AdamW to Muon partway through one training trajectory. No implementation or version is bundled here.
- Candidate outcomes: per-seed held-out quality over the full training trajectory and versus consumed training bytes or a validated compute proxy; record throughput, update, and memory diagnostics as secondary measurements. The primary metric, units, aggregation, tuning separation, and materiality threshold are not yet frozen.

## Experimental units and selection

The source and model/data/split identities, independent experimental unit, initialization correspondence, precision, per-arm compute budget, tuning budget, checkpoint schedule, and checkpoint selection rule are not yet closed in this package. The historical run used seeds 17, 31, and 47; the new plan must state whether those same initial states can be reconstructed or whether a matched two-arm rerun is required. Freeze the complete policy before any Muon outcome is observed. Do not rank arms by a single favorable endpoint.

## Stopping, invalidation, and decisions

Stopping rule, invalid-run criteria, material improvement threshold, and rules for positive, negative, or inconclusive outcomes remain unresolved. Source-owner review must freeze these rules before execution. No outcome or result is included in this package.

## Prerequisites and readiness gate

1. Reuse the recorded #164 AdamW result as the historical control reference and import/hash its available result, protocol, and source identities into the private experiment closure. Issue #18's separate historical packaging/replay qualification remains useful but is not a prerequisite for starting this comparison.
2. Establish whether the historical result has enough input and initialization correspondence to support a controlled optimizer claim. If not, include a fresh AdamW control arm alongside Muon from the same declared initial states. Issue #38 is diagnostic context, not an execution prerequisite; extract the selected #164 AdamW semantics from its own source/protocol.
3. Select and freeze the exact Muon implementation, version, parameter groups, hyperparameters, and tuning policy from source-owned material.
4. Establish an authorized, licensed, shareable data/model/evaluation closure and fixed split policy.
5. Run a bounded pilot on a named backend; estimate the full arms × seeds × tuning campaign from measured pilot evidence, with uncertainty and assumptions.
6. Freeze analysis and decision rules, then compile a new immutable plan revision and obtain explicit run authorization.
