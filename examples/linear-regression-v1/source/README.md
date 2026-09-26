# Self-contained linear-regression reproduction

This example is a deliberately small scientific reproduction fixture for Experiment Compiler.

## Hypothesis

A one-dimensional linear model trained by 32 full-batch gradient-descent updates on the embedded training split will recover the relation `y = 2x + 1` closely enough that the held-out evaluation mean-squared error is at most `1e-15`, with both learned parameters within `1e-9` of their declared targets.

The point is not that this is a difficult scientific problem. The point is that every dependency needed to reproduce the claim is public, auditable, and small enough to inspect directly.

## Entrypoint

From this directory:

```sh
python reproduce.py --output-dir reproduction
```

That command runs the included conformance tests, performs the exact-rational training run, compares the result with the embedded reference result, and writes a new result plus a scoped reproduction receipt.

No network access, package installation, GPU, random seed, clock, locale, or external data is required.
