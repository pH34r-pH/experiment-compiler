# Experiment Compiler

[![Lifecycle](https://github.com/pH34r-pH/experiment-compiler/actions/workflows/lifecycle.yml/badge.svg)](https://github.com/pH34r-pH/experiment-compiler/actions/workflows/lifecycle.yml)
[![License](https://img.shields.io/github/license/pH34r-pH/experiment-compiler)](LICENSE)

<p align="center">
  <img src="docs/assets/hero.webp" alt="Experiment Compiler — portable reproducible experiment packaging" width="100%">
</p>

**Compile reviewed research inputs into portable, inspectable, integrity-verifiable experiment artifacts.**

Experiment Compiler packages explicit source bytes, standards metadata, dependency/resource closure, and provenance into versioned Compiled Experiments. It verifies those artifacts without inventing scientific semantics and provides a deliberately bounded execution handoff for reviewed lifecycle packages.

It does **not** define the science, fill in a missing method, infer a conclusion, or require a hosted service.

## Quick start

Python 3.11+ is sufficient for the core compiler/verifier.

```sh
python -m experiment_compiler compile examples/linear-regression-v1/experiment.json

python -m experiment_compiler verify \
  dist/stdlib-linear-regression-v1-compiled-experiment.zip \
  --recipe examples/linear-regression-v1/experiment.json

python -m unittest discover -s tests -v
```

The self-contained linear-regression fixture embeds the implementation, tests, synthetic data, environment/configuration, protocol, acceptance criteria, reference result, resource evidence, Croissant metadata, and RO-Crate / Process Run Crate provenance.

For the historical #164 compatibility package and its narrower claim scope, see [docs/poc-scope.md](docs/poc-scope.md).

## What “compiled” means

```text
reviewed source -> versioned recipe -> deterministic package
                                      |
                                      +--> manifest
                                      +--> provenance
                                      +--> dependency/resource closure
                                      |
                                      v
                                  verify bytes
                                      |
                              optional bounded run
                                      |
                                      v
                           reviewed result artifact
```

The compiler is intentionally conservative about claim strength:

- **compile** establishes the package was built from the declared inputs under the selected profile;
- **verify** establishes byte/inventory consistency and, with an external pin, package identity;
- **run** records a bounded execution attempt;
- **finalize** carries source-authored interpretation/review;
- **publication** occurs through normal reviewed repository promotion.

Each admitted run creates a controller-owned `<output-stem>.attempt/runner-receipt.json`
before launch, using the packaged attempt receipt schema. The terminal receipt and
bounded logs remain there even if result compilation fails. A receipt still marked
`started` records an incomplete attempt after interruption; it does not establish
completion. Existing result ZIP and `.source` evidence remain available for
successfully packaged attempts. Attempt directories are never overwritten.

Output collection rejects members above 16 MiB and shares the remaining 48 MiB
recipe source budget between outputs and CWL provenance, reserving space for the
plan, bounded logs and attempt metadata.

None of those steps alone proves that a scientific conclusion is correct.

## Profiles

- `poc-v1` — historical package compatibility.
- `compiled-experiment-v1` — self-contained reproducibility package.
- `compiled-experiment-lifecycle-v1` — prospective plans, attempts, revisions, results, and interpretation using existing standards.

Historical artifacts remain immutable; new semantics require a new versioned identity.

Read the [architecture diagrams](docs/architecture.md), the [Wiki](https://github.com/pH34r-pH/experiment-compiler/wiki), and the [build recipe contract](docs/build-recipe.md) for the full lifecycle.

## Standards and closure

The project reuses Croissant, RO-Crate, Process Run Crate, Schema.org, PROV-O, and CWL rather than inventing a parallel ontology where an existing standard fits.

Required runtime inputs are explicitly classified as embedded, immutable public, host/ABI prerequisites, or unavailable. Unknown required resources remain unknown and block admission instead of silently becoming zero/default.

See [resources and closure](docs/resources-and-closure.md).

## Public compiler, private research

A private lab can invoke an exact pinned public compiler revision inside its own trust boundary. Public promotion builds a new artifact from an explicitly reviewed shareable closure; the public compiler never needs private-repository credentials.

This keeps packaging mechanics public and auditable without turning unfinished/private research inputs into public artifacts.

## Public catalog projection

The Pages build writes `/data/experiments.json` from the discovered authoritative recipes and lifecycle crates. Projection schema version 2 includes one stable detail route per record, exact ZIP SHA-256 and size, source repository and commit, plus any backlinks declared by the source record. The JSON Schema lives at [`site/data/experiments.schema.json`](site/data/experiments.schema.json) and ships beside the projection. The catalog contains no separately maintained experiment registry. Lifecycle records expose exact execution statuses and the full authoritative protocol when summary headings are absent; see [the projection contract](docs/catalog-projection.md).

## Repository map

- `experiment_compiler/` — compiler, verifier, lifecycle, runner, and catalog implementation.
- `examples/` — compatibility, reproducibility, and lifecycle examples.
- `tests/` — executable contracts.
- `docs/` — authoritative package/standards/closure documentation.
- `docs/wiki/` — canonical source for the GitHub Wiki.
- `site/` — derived project/catalog presentation.
- `scripts/` — fixture and standards validation helpers.

## Contributing, security, citation

Read [CONTRIBUTING.md](CONTRIBUTING.md), use [SECURITY.md](SECURITY.md) for sensitive reports, and cite research use with [CITATION.cff](CITATION.cff).

Apache-2.0. Copyright 2026 Tyler J.H.G. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
