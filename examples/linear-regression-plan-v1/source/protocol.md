# Protocol

## Question

Can an independently invoked CWL workflow run the declared exact-rational linear-regression plan and emit machine-readable result bytes?

## Model and training

Use the `y_hat = weight * x + bias` model, the included synthetic train/eval rows, initial parameters and learning rate. Run exactly the configured 32 full-batch updates using Python `fractions.Fraction`; do not shuffle or use randomness.

## Acceptance

After training, require the held-out MSE and parameter errors to meet `acceptance.json`. A process exit code of zero means only that the CWL command completed. The scientific decision is recorded separately in `result.json` and is not inferred from Process Run status.

## Execution and resource declaration

`workflow.cwl` is the executable contract. It requests one CPU core and 32 MiB minimum RAM. Temporary and output storage are bounded to 4 MiB and 1 MiB for this tiny fixture. These are CWL requests for this example, not general defaults for other experiments.
