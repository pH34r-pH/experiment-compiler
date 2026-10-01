# Working in Experiment Compiler

Read README.md, ORIGINS.md and docs/poc-scope.md first.

- Preserve the initial POC member bytes and checksum. Never edit frozen inputs to make a failing test green.
- Keep experiment IDs, hypotheses and evidence selections in recipes/standards documents, not generic compiler code.
- Compiler and verifier perform no network calls, dependency installs, training or execution of package members.
- Treat package paths/JSON as untrusted. Keep bounded inventory/digest validation and hostile-input tests.
- Do not copy credentials, private source trees, deployment logic or non-public datasets into this repository.
- Preserve open-standard provenance and distinguish integrity checks from standards conformance and scientific reproduction.
- One .github/workflows/lifecycle.yml is the build operator entrypoint. Do not add one-off workflows.
- Run python -m unittest discover -s tests -v and build/verify the documented POC before proposing a merge.
- Package receipts must not claim a new execution is historical evidence. Retain important verified outcomes in Git documentation, not only expiring CI artifacts.

## Repository map

| Boundary | Responsibility | Read with |
| --- | --- | --- |
| `experiment_compiler/` | Recipe loading, deterministic assembly, integrity verification, lifecycle validation, bounded execution handoff, revision/finalization, and catalog projection. | [`experiment_compiler/AGENTS.md`](experiment_compiler/AGENTS.md) |
| `examples/` | Source-owned recipes and package members, including the frozen #164/AdamW compatibility fixture and current lifecycle fixtures. | [`examples/AGENTS.md`](examples/AGENTS.md) |
| `tests/` | Offline contracts for packaging, hostile inputs, lifecycle identity, runner boundaries, and site projection. | [`tests/AGENTS.md`](tests/AGENTS.md) |
| `docs/` | Normative package, standards, evidence-boundary, and lifecycle explanations; `docs/wiki/` is the wiki source. | [`docs/AGENTS.md`](docs/AGENTS.md) |
| `scripts/` | Fixture generation, RO-Crate profile checks, documentation/artifact hygiene, mutation-report validation, and static-site build helpers. | [`scripts/AGENTS.md`](scripts/AGENTS.md) |
| `site/` | Hand-authored presentation assets and schemas consumed by the derived Pages build; catalog data is generated. | [`site/AGENTS.md`](site/AGENTS.md) |
| `.github/workflows/` | Existing lifecycle, structural-audit, and wiki-sync entrypoints. | [`docs/architecture.md`](docs/architecture.md) |

## Architecture and data flow

```text
recipe + declared source members
        -> core.load_recipe / manifest_for
        -> assembly.compile_package
        -> deterministic ZIP + manifest
        -> core.verify_bytes

verified lifecycle plan
        -> runner_cli + runner admission
        -> bounded attempt/result package + receipt
        -> revision or finalization
        -> catalog.describe_catalog / scripts/build_pages_site.py
        -> derived public site
```

`__main__.py` is the CLI dispatcher. `core.py` owns recipe, package, and lifecycle integrity; `assembly.py` owns source-closure packaging; `runner.py` is the explicit execution boundary; `revision.py` and `finalization.py` create new immutable lifecycle identities; `catalog.py` derives display records. The full trust-boundary model is in [`docs/architecture.md`](docs/architecture.md) and the package contract is in [`docs/build-recipe.md`](docs/build-recipe.md).

## Change routing and invariants

- Change recipe semantics or package identity rules in `experiment_compiler/core.py`, then update the relevant profile documentation and tests.
- Change execution admission, receipts, bounded collection, or signal handling in `experiment_compiler/runner.py`; keep `compile` and `verify` non-executing.
- Change public presentation through `catalog.py`, `scripts/build_pages_site.py`, or `site/`; do not hand-maintain generated catalog records.
- Change historical compatibility only with explicit evidence review. `examples/issue-164/` and its recorded hashes are immutable regression inputs; current AdamW package work must remain intact.
- A plan revision, attempt, or finalization is a new artifact with provenance. Never edit an archived package to attach later results.
- Process success, package integrity, scientific interpretation, publication, and independent reproduction remain separate claims.

## Focused validation

Run from the repository root. Use the narrowest command covering the changed boundary, and run the full workflow before merge:

- Documentation/maps: `git diff --check`.
- Compiler, verifier, lifecycle, or tests: `python -m unittest discover -s tests -v`.
- Historical POC contract: `python -m experiment_compiler compile examples/issue-164/experiment.json --output /tmp/experiment-compiler-issue-164.zip` followed by `python -m experiment_compiler verify /tmp/experiment-compiler-issue-164.zip --recipe examples/issue-164/experiment.json`.
- Current lifecycle profile: run `python scripts/validate_current_ro_profiles.py` on a generated `result.source` crate as in [`.github/workflows/lifecycle.yml`](.github/workflows/lifecycle.yml); the frozen plan fixture alone is not a Process Run Crate.
- Derived site: `python scripts/build_pages_site.py --output /tmp/experiment-compiler-site`.
- Documentation/artifact hygiene: `python scripts/test_docs_hygiene.py`; `.github/workflows/documentation-hygiene.yml` is the single changed-Markdown/artifact integration.
- CI source of truth: `.github/workflows/lifecycle.yml` for package behavior and `.github/workflows/documentation-hygiene.yml` for changed living Markdown; do not create another guard.
