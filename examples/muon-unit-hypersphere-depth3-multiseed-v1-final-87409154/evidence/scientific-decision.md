# Decision: Muon multi-seed qualifying comparison

## Selected evidence

This decision concerns attempt `87409154e60d`, result package SHA-256 `0fb8db51740f3f48e2bbcf20b4cbb0cbbe6ac9dc2b931439ba71617c3fb94b89`, compiled from plan SHA-256 `fec4f9d2270a40deebe580e376dfbf23ddb799059b4b67cadef211fa4d9c833a`. It uses seeds 17, 31, and 47 on eight paired validation documents. Seed 17 had already been observed during infrastructure qualification before the three-seed analysis was frozen; that chronology limits any prospective interpretation.

## Observations

The primary endpoint is paired, per-document trapezoidal AULC across updates 0, 8, 16, 32, 64, 96, and 128. The statistic is Muon minus the corresponding frozen historical AdamW result; positive values favor AdamW. The mean was +0.01620 nats per original byte (document-bootstrap 95% interval +0.01486 to +0.01773; eight source-document units). This exceeds the frozen 0.01 materiality reference in the direction of AdamW.

The secondary endpoint is final byte-normalized NLL at update 128, with negative values favoring Muon. The mean difference was -0.02266 nats per original byte (95% interval -0.02518 to -0.01903). This exceeds the same materiality reference in the direction of Muon.

For each source document, its paired difference was first averaged over seeds 17/31/47; the reported intervals use 65,536 deterministic bootstrap replicates with seed 12,365,536. The intervals describe document-sampling uncertainty under this analysis, not uncertainty over the broader population of models, datasets, or training settings.

## Interpretation and disposition

The endpoints disagree: the full learning-curve area favors the historical AdamW control, while the final checkpoint loss favors the Muon treatment. The result therefore does not establish a general optimizer winner or resolve the optimizer comparison. Record the two endpoint-specific effects and classify the overall comparison as mixed/inconclusive. Make no optimizer-superiority, training-cost, or cost-efficiency claim.

The experiment measured no wall-clock comparison against fresh matched AdamW runs, hardware energy, accelerator utilization, or monetary cost. It used frozen historical AdamW controls, ran one treatment arm, and stopped at update 128. It did not test continuation to update 256 or a new hybrid schedule. Official test data remained sealed. These limitations prevent claims about cheaper training, later convergence, or out-of-sample test performance.

This decision records the observed evidence and its limits. It does not alter the frozen protocol, accept a broader scientific hypothesis, or authorize another run.
