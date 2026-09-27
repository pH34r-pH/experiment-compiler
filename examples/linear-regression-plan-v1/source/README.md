# Prospective linear-regression plan

## Hypothesis

A one-dimensional linear model trained with 32 exact-rational full-batch gradient steps on the included synthetic training split will recover `y = 2x + 1` within the parameter and held-out loss bounds in `acceptance.json`.

This package is deliberately a plan: it contains no run result, execution receipt, or Process Run Crate claim. It is a public, tiny workflow fixture for exercising the plan → attempt → new result package pipeline. It does not validate a large-scale training workflow.

## Run after review

The runner is separate from `experiment-compiler compile/verify`. The public CI smoke test pre-pulls the digest-pinned Python tool image and lets cwltool's Docker executor apply the declared CPU, memory, time and no-network requirements. The workflow-runner host has no experiment or deployment credentials. The CLI opt-in documents that code in the package will execute. Review the exact package digest before execution.
