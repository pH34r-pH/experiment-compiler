# Muon multi-seed qualifying comparison

## Status and chronology

Status: **Draft**. This package extends the already-qualified seed-17 infrastructure pilot into the frozen three-seed optimizer comparison for `unit_hypersphere_depth3`. The optimizer treatment, data, model condition, 128-update budget, checkpoints, evaluation documents, runtime image and resource boundary are unchanged.

Seed 17 Muon outcomes were observed during infrastructure qualification before this multi-seed analysis record was frozen. That observation is disclosed rather than erased. Seeds 31 and 47 were not used to tune the treatment, runner, hyperparameters or aggregate statistic. The historical AdamW controls predate the Muon treatment.

## Treatment and control

For each seed 17, 31 and 47, start from the exact initialization fingerprint retained in the corresponding frozen #164 `unit_hypersphere_depth3` AdamW cell. Muon receives only:
- `blocks.0.self_attn.in_proj_weight`
- `blocks.0.self_attn.out_proj.weight`
- `blocks.0.linear1.weight`
- `blocks.0.linear2.weight`

Muon settings remain lr 0.001, weight decay 0.01, momentum 0.95, Nesterov enabled, Newton-Schulz coefficients (3.4445,-4.775,2.0315), epsilon 1e-7, five Newton-Schulz steps and `adjust_lr_fn="match_rms_adamw"`. All other trainable parameters retain AdamW lr 0.001, weight decay 0.01, betas (0.9,0.999), epsilon 1e-8, AMSGrad false. Global gradient clipping remains 1.0.

The control is the matching frozen #164 AdamW cell for each seed. No fresh AdamW or topology arm is authorized.

## Outcomes and aggregation

The primary outcome is paired per-source-document trapezoidal AULC difference (Muon minus historical AdamW) across checkpoints 0, 8, 16, 32, 64, 96 and 128. Final byte-normalized NLL difference is secondary. Negative favors Muon.

The statistical unit is the source document. For each document, first average its paired treatment-control difference across seeds 17/31/47, then perform a deterministic 65,536-replicate paired-document bootstrap (seed 12,365,536). Report the mean and two-sided percentile 95% interval for primary and secondary outcomes. The existing 0.01 nats/original-byte value is retained as a materiality reference, not silently converted into a new topology decision rule.

This experiment does not rerun or reinterpret #164 topology selection. Scientific disposition of the optimizer comparison requires a separate source-authored review record after execution; the runner emits measurements and uncertainty but does not declare optimizer superiority.

## Execution boundary

Official test data remains sealed. All frozen input hashes and per-seed initialization hashes must match before a cell is admitted. Execution uses the already-qualified digest-pinned PyTorch image, CPU-only rootless Podman, CWL NetworkAccess=false and the existing bounded private runner. No package installation or network access is permitted.
