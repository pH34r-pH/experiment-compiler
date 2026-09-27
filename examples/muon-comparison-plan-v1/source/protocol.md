# Prospective protocol: controlled optimizer comparison

## Question

Under matched model, data, objective, precision, update or compute budget, and selection policy, does a source-owner-selected Muon treatment change the learning trajectory and held-out quality relative to a reproducible AdamW baseline, rather than only producing a favorable endpoint?

## Hypothesis and status

The candidate hypothesis is that a specified Muon treatment may improve trajectory and held-out quality per matched training budget. This direction is not a confirmed claim. The exact treatment, comparator replay, estimand, analysis, and decision criteria remain proposed and unfrozen.

## Candidate controls and outcomes

- Candidate control: an exact, replayed AdamW baseline derived from the qualified small-task package in issue #18.
- Candidate treatment: one Muon implementation and parameter-group policy selected and frozen by the source owner. No implementation or version is bundled here.
- Candidate outcomes: per-seed held-out quality over the full training trajectory and versus consumed training bytes or a validated compute proxy; record throughput, update, and memory diagnostics as secondary measurements. The primary metric, units, aggregation, tuning separation, and materiality threshold are not yet frozen.

## Experimental units and selection

The model/data/split identities, independent experimental unit, seeds, initialization policy, precision, per-arm update and compute budgets, tuning budget, checkpoint schedule, and checkpoint selection rule are unknown. Freeze them from the qualified baseline before any comparison outcome is observed. Do not rank arms by a single favorable endpoint.

## Stopping, invalidation, and decisions

Stopping rule, invalid-run criteria, material improvement threshold, and rules for positive, negative, or inconclusive outcomes remain unresolved. Source-owner review must freeze these rules before execution. No outcome or result is included in this package.

## Prerequisites and readiness gate

1. Complete and qualify the exact #18 baseline closure and replay.
2. Review the optimizer semantics and evidence status in #38.
3. Select and freeze the exact Muon implementation, version, parameter groups, hyperparameters, and tuning policy from source-owned material.
4. Establish an authorized, licensed, shareable data/model/evaluation closure and fixed split policy.
5. Run a bounded pilot on a named backend; estimate the full arms × seeds × tuning campaign from measured pilot evidence, with uncertainty and assumptions.
6. Freeze analysis and decision rules, then compile a new immutable plan revision and obtain explicit run authorization.
