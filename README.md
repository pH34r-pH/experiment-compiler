# Experiment Compiler

Turn reviewed research inputs into a portable Compiled Experiment that another person or agent can inspect, verify and use as the starting point for reproduction.

The compiler packages files and standards metadata. It does not define the science, execute training, invent missing methods, or require a hosted service.

## Build the first POC

Python 3.11 or later with zlib is sufficient. From a checkout, no installation or network access is needed:

```sh
python -m experiment_compiler compile examples/issue-164/experiment.json \
  --receipt dist/build-receipt.json
python -m experiment_compiler verify dist/issue-164-experiment-package.zip \
  --recipe examples/issue-164/experiment.json
python -m unittest discover -s tests -v
```

The result is the same initial POC already published through Portfolio:

```text
File:    dist/issue-164-experiment-package.zip
Bytes:   15296
SHA-256: 45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341
```

The eight reviewed source files are committed under `examples/issue-164/inputs/`. The compiler generates the manifest and ZIP from those files; it does not download or copy a prebuilt ZIP. The expected digest is a historical compatibility assertion and is checked before writing the result.

For an installed command, use a virtual environment and `python -m pip install .`, then run `experiment-compiler compile examples/issue-164/experiment.json`. Installing build tooling can use the network; compiling and verifying do not. No PyPI publication is implied by the local package name.

## What the first package contains

The ZIP contains Croissant TaskProblem/TaskSolution JSON-LD, RO-Crate/Process Run Crate metadata, the normative AdamW update contract, a semantic audit, a standards gap register, validation instructions and a contract-receipt runner. A generated manifest binds the eight members and preserves the historical source and evidence hashes.

This first POC proves **package-build compatibility and integrity**, not a self-contained scientific reproduction. Some data, implementation, tests, environment and evidence are references to the original research repository rather than embedded files. The included receipt runner expects tests that are not in this ZIP. The compiler verifies JSON syntax and byte integrity, not full standards conformance. Read [the scope and limitations](docs/poc-scope.md) before handing this POC to an agent.

A useful agent instruction is: “Inspect the package and its gap register. Verify the digest and inventory. Identify included files versus external prerequisites, and report what is still needed before attempting an independent reproduction. Do not substitute missing scientific semantics.”

## Reproduce a self-contained Compiled Experiment

The second example is intentionally tiny so the complete scientific closure can live inside one package. It trains a one-dimensional linear model on embedded synthetic CC0 data using exact rational arithmetic and evaluates a held-out split.

Build it:

```sh
python -m experiment_compiler compile examples/linear-regression-v1/experiment.json
python -m experiment_compiler verify dist/stdlib-linear-regression-v1-compiled-experiment.zip \
  --recipe examples/linear-regression-v1/experiment.json
```

The frozen package identity is:

```text
File:    dist/stdlib-linear-regression-v1-compiled-experiment.zip
Bytes:   14010
SHA-256: 251a43f8719a17bb0898a5e5e51f4d8c22f9280b29febadd72d98ffbba20e544
```

Then extract the ZIP and, from its `experiment/` directory, run:

```sh
python reproduce.py --output-dir reproduction
```

The package contains the training implementation, conformance tests, train/eval data and license, environment and training configuration, protocol, acceptance criteria, reference result bytes, a public-CI reproduction receipt, resource measurements, Croissant 1.1 dataset metadata, and RO-Crate 1.3 / Process Run Crate 0.6 provenance. `dependency-closure.json` classifies every required item as embedded or an immutable public standards/source reference; its unavailable set is empty.

The reference public CI matrix observed peak process RSS between about 18.9 and 20.7 MiB on Ubuntu 24.04 across Python 3.11/3.13/3.14. The package records 32 MiB as conservative planning headroom. It is CPU-only and requires 0 bytes of VRAM.

This example is a bounded reproducibility demonstration, not evidence that an arbitrary ML experiment becomes reproducible merely by putting files in a ZIP. The scientific claim is deliberately simple enough that the dependency closure is inspectable.

## How it is structured

`experiment.json` is a small local build recipe: an ID, the `poc-v1` compatibility profile, source/evidence metadata and a list of explicitly selected files with hashes, sizes and archive paths. Scientific meaning stays in the research documents and existing standards. There is no #164-specific logic in the compiler.

The `poc-v1` envelope is preserved for compatibility with the initial release. It is not presented as a new universal research-object standard. The standards used by the historical documents are [RO-Crate](https://www.researchobject.org/ro-crate/), [Workflow Run Crate / Process Run Crate](https://www.researchobject.org/workflow-run-crate/), and [MLCommons Croissant](https://github.com/mlcommons/croissant).

See [the recipe contract](docs/build-recipe.md), [origins](ORIGINS.md) and [contributing](CONTRIBUTING.md).

## One build lifecycle

The `Experiment Compiler lifecycle` GitHub Actions workflow tests and rebuilds the POC on pull requests, pushes to `main`, and manual dispatch. Manual rebuilds need no copied IDs or credentials. CI checks the known historical checksum, builds twice, verifies the inventory, tests the installed wheel, and uploads the ZIP, receipts and Python wheel. A passing run qualifies those packaging checks only.

The public compiler is now the reviewed source used by the Fleet/Portfolio publication path for the active self-contained Compiled Experiment. Fleet pins an exact public compiler revision, rebuilds and verifies the package, reproduces it from the extracted ZIP, and derives Portfolio display metadata from these authoritative artifacts rather than maintaining a second catalog by hand. The historical #164 POC remains available as a compatibility artifact.

This repository itself still needs no private DSL checkout, GitHub App key, Azure identity, GPU, PyTorch, or Fleet access. The derived project site is built from the same authoritative metadata projection; GitHub Pages/custom-domain activation is an operational deployment step rather than a separate content source.

## License

Apache-2.0. Copyright 2026 Tyler J.H.G. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
