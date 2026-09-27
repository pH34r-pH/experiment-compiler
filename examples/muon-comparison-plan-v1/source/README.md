# Muon optimizer comparison candidate

## Hypothesis

On the existing DLS #164 `unit_hypersphere_depth3` AdamW reference, a source-owner-selected Muon treatment may improve the learning trajectory and held-out quality per matched training budget. A favorable endpoint alone will not establish an improvement. Reuse the historical result when its inputs and initialization match; otherwise run a matched AdamW control alongside Muon.

This is a blocked Phase 3 planning candidate compiled from public research direction only. It is not a frozen preregistration, implementation specification, resource request, execution authorization, result, or Process Run record. No exact Muon variant or Muon implementation is present in this package.

The historical AdamW result already exists; a fresh exact replay is a separate qualification task, not a prerequisite to start Muon. This public candidate remains blocked until the private control/input closure, Muon implementation, protocol decisions, and measured resource envelope are supplied and reviewed. Unknown resource quantities are recorded as unknown; the runner must not schedule this plan.

## Public rationale

The historical endpoint, its trajectory caveat, and replay-qualification status are summarized in issue [#18](https://github.com/pH34r-pH/experiment-compiler/issues/18); the prospective controlled comparison is tracked in [#72](https://github.com/pH34r-pH/experiment-compiler/issues/72). Notebook 005 reports an endpoint anomaly; Notebook 006 adds trajectory, regularization, and selection corrections. This public candidate does not include private input bytes or checkpoints.

## Readiness

There is no executable workflow in this plan because the treatment implementation, private input closure, control compatibility decision, selection policy, and resource envelope are unresolved. Compile and verify package identity only. Do not invoke `experiment-runner` until a reviewed protocol revision contains the complete admitted closure and an explicit bounded resource budget.
