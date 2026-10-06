# `tests/` map

Tests are executable contracts for the repository [`AGENTS.md`](../AGENTS.md) invariants. They must remain offline unless a test explicitly exercises a controlled local subprocess boundary.

## Test ownership

- `test_compiler.py` covers recipe loading, deterministic package bytes, manifests, path/JSON limits, verifier behavior, lifecycle crate rules, catalog derivation, and source tampering.
- `test_runner.py` covers worker/resource admission, CWL restrictions, bounded collection, process cleanup, attempt receipts, cancellation, and result retention.
- `test_runner_cli.py` covers command dispatch and operator-signal behavior at the CLI boundary.
- `test_revision.py` covers immutable attempt lineage, plan revision, finalization, and source-owned decision/review binding.
- `test_site_projection.py` covers catalog/detail rendering and public claim boundaries.
- `test_agent_modes.py` covers first-user installed use, read-only diagnostic cases, and the exact-byte-only recipe repair helper.
- `fixtures/` contains test-only inputs; historical package evidence lives under [`examples/`](../examples/), not here.

When behavior changes, route the assertion to the smallest owning test module and retain tests for rejected hostile inputs and claim-separation boundaries. Do not remove or broaden a test merely to accommodate a new implementation.

## Validation

```sh
python -m unittest discover -s tests -v
```

For a focused edit, use the corresponding unittest method or module first, then run the full command before proposing a merge. The CI entrypoint is [`.github/workflows/lifecycle.yml`](../.github/workflows/lifecycle.yml).
