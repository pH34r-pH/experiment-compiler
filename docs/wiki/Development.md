# Development

## Repository map

- `experiment_compiler/` — compiler, verifier, lifecycle, runner, catalog, and supporting library code.
- `examples/` — historical compatibility, self-contained reproduction, and lifecycle examples.
- `tests/` — unit/integration contract coverage.
- `docs/` — package, scope, resource, and closure contracts.
- `docs/wiki/` — canonical GitHub Wiki source.
- `scripts/` — fixture and standards validation helpers.
- `site/` — derived project/catalog presentation.
- `.github/workflows/` — lifecycle and structural quality gates.

## Engineering rules

Prefer versioned profiles over silent behavior changes. Keep compile/verify non-executing. Reject ambiguous or unavailable required inputs rather than guessing. Preserve immutable historical artifacts. Keep scientific interpretation source-authored and reviewable.

A new profile should have an explicit use case, version identifier, tests for invalid boundaries, and migration/compatibility reasoning.

Read [CONTRIBUTING.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/CONTRIBUTING.md) before submitting changes.
