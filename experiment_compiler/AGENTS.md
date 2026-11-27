# `experiment_compiler/` map

This directory is the implementation of the public compiler and its explicit runner boundary. Read the repository [`AGENTS.md`](../AGENTS.md), [`docs/architecture.md`](../docs/architecture.md), and [`docs/build-recipe.md`](../docs/build-recipe.md) before changing behavior.

## Ownership and relationships

| File | Role | Downstream consumers |
| --- | --- | --- |
| `__main__.py` | CLI dispatch for `compile`, `verify`, `describe`, `catalog`, `revise`, and `finalize`. | Users, CI, and package scripts. |
| `core.py` | Canonical JSON/path/size checks, recipe loading, deterministic manifest construction, and lifecycle crate validation. | `assembly.py`, `catalog.py`, tests, CLI. |
| `assembly.py` | Copies declared source bytes into a deterministic ZIP without executing them. | `core.compile_package`, compiler tests. |
| `runner_cli.py` | Explicit opt-in command-line adapter for execution and operator signals. | `runner.py`, lifecycle CI. |
| `runner.py` | Run and recovery orchestration, attempt staging, and result package creation. | `runner_cli.py`, runner tests, lifecycle workflow. |
| `runner_admission.py` | Package verification, dependency and worker admission, CWL requirements, job inputs, and engine mode. | Runner, native controller, and Actions projection. |
| `runner_process.py` | Bounded CWL launch and collection, operator signals, process groups, and container reconciliation. | Runner and controller fixtures. |
| `runner_receipts.py` | Attempt receipt writes, container journals, lifecycle transitions, and retained evidence validation. | Runner, native receipts, and recovery fixtures. |
| `revision.py` / `finalization.py` | New immutable plan and finalized identities derived from verified attempts. | Lifecycle catalog and revision tests. |
| `catalog.py` | Read-only projection of authoritative recipes and lifecycle crates. | `scripts/build_pages_site.py`, site tests. |

## Data flow and invariants

Recipes are loaded and checked by `core.py`, assembled by `assembly.py`, and verified from ZIP bytes by `core.py`; no packaged member is executed during compile or verify. The CWL runner crosses the execution boundary after explicit opt-in, digest, resource, worker, and no-network admission. Attempt receipts are persisted before launch and retained across terminal failure; result, revision, and finalization packages are distinct identities. Catalog output is derived, never an independent registry.

Keep source paths relative and bounded, preserve declared member bytes, reject unsafe/duplicate paths and untrusted JSON, and keep package integrity distinct from scientific validity. Do not weaken a hostile-input test to make an implementation pass.

## Change routing and validation

- Recipe/header/path/manifest rules: `core.py` + `tests/test_compiler.py`.
- Execution admission and worker limits: `runner_admission.py` and `worker_limits.py` + `tests/test_runner.py` and `tests/test_worker_limits.py`.
- Process handling and collection: `runner_process.py` and `runner_logs.py` + `tests/test_runner.py` and `tests/test_runner_cli.py`.
- Receipt transitions and persistence: `runner_receipts.py` + `tests/test_runner.py` and `tests/test_runner_transitions.py`.
- Plan lineage or source-authored interpretation: `revision.py` / `finalization.py` + `tests/test_revision.py`.
- Catalog/detail projection: `catalog.py` + `tests/test_site_projection.py` and `tests/test_compiler.py`.

Focused commands:

```sh
python -m unittest discover -s tests -v
python -m experiment_compiler --help
```

For execution changes, also use the reviewed lifecycle path in [`.github/workflows/lifecycle.yml`](../.github/workflows/lifecycle.yml); do not execute private or historical research inputs locally as part of a documentation change.
