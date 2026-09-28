# Quickstart

The compiler's core compile/verify path is offline and does not require a private research repository.

## Historical compatibility package

```sh
python -m experiment_compiler compile examples/issue-164/experiment.json \
  --receipt dist/build-receipt.json

python -m experiment_compiler verify dist/issue-164-experiment-package.zip \
  --recipe examples/issue-164/experiment.json
```

This reproduces the historical package envelope and digest. It demonstrates deterministic package-build compatibility and integrity, not a self-contained scientific reproduction.

## Self-contained example

```sh
python -m experiment_compiler compile examples/linear-regression-v1/experiment.json

python -m experiment_compiler verify \
  dist/stdlib-linear-regression-v1-compiled-experiment.zip \
  --recipe examples/linear-regression-v1/experiment.json
```

After extraction, the package's declared reproduction entrypoint can be run to reproduce the deliberately small synthetic study and compare result bytes with the embedded reference evidence.

## Tests

```sh
python -m unittest discover -s tests -v
```

For an installed CLI, create a virtual environment and install the package locally. Network access may be needed for installation tooling; compile and verify themselves do not fetch remote experiment inputs.

Read [docs/poc-scope.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/poc-scope.md) before treating the historical package as a reproduction artifact.
