# Protocol

## Question

Can an independent runner reproduce the declared small linear-regression result using only the files in this package?

## Data

`data.csv` has an explicit `split` column. Rows marked `train` are the complete training set; rows marked `eval` are held out from optimization and used only for the final acceptance metric. The data are synthetic and CC0-1.0.

## Model and training

The model is `y_hat = weight * x + bias`.

Initialize weight and bias from `experiment-config.json`. For exactly 32 updates:

1. evaluate every training row;
2. compute full-batch mean-squared-error gradients for weight and bias;
3. update both parameters with the declared learning rate.

The reference implementation uses Python `fractions.Fraction`, so arithmetic is exact and no floating-point tolerance is needed to define the training trajectory. There is no stochasticity or data reordering.

## Acceptance

After the 32nd update:

- evaluate MSE only on the `eval` split;
- require evaluation MSE <= the fraction in `acceptance.json`;
- require absolute error of weight and bias versus the declared targets <= the parameter threshold;
- require the deterministic scientific result object to equal `expected-result.json` exactly.

A reproduction receipt may contain environment-specific runtime/resource observations and therefore is not expected to be byte-identical to the reference receipt.

## Entrypoint

Run:

```sh
python reproduce.py --output-dir reproduction
```

The entrypoint first runs `test_experiment.py`, then executes the training/evaluation implementation and emits `result.json` plus `reproduction-receipt.json`.
