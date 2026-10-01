# `docs/` map

Documentation is the durable explanation of package scope and evidence boundaries. Read the repository [`AGENTS.md`](../AGENTS.md) before changing it.

## Canonical documents

- [`architecture.md`](architecture.md) is the current system/data-flow/trust-boundary map and points to the Mermaid sources in [`diagrams/`](diagrams/).
- [`build-recipe.md`](build-recipe.md) is the recipe, deterministic packaging, verification, lifecycle, and runner contract.
- [`poc-scope.md`](poc-scope.md) and [`../ORIGINS.md`](../ORIGINS.md) preserve the historical #164 compatibility scope and lineage.
- [`evidence-boundaries.md`](evidence-boundaries.md), [`resources-and-closure.md`](resources-and-closure.md), and [`real-study-walkthrough.md`](real-study-walkthrough.md) explain distinct evidence and disclosure boundaries.
- [`catalog-projection.md`](catalog-projection.md) documents the derived public projection.
- [`wiki/`](wiki/) is the source copied by the existing wiki-sync workflow; keep its pages consistent with repository documents.

Preserve historical/scientific records and their caveats. A later interpretation belongs in a new dated or versioned document with an explicit relationship, not a silent rewrite of a frozen record. Link a superseded plan only to a replacement that exists and is proven by repository evidence.

## Validation and routing

Documentation-only changes: `git diff --check` and `python scripts/test_docs_hygiene.py`. If a page changes package or lifecycle semantics, run `python -m unittest discover -s tests -v` and the relevant generated-crate/profile validation. The current CI and wiki publication entrypoints are [`.github/workflows/lifecycle.yml`](../.github/workflows/lifecycle.yml), [`.github/workflows/structural-quality-audit.yml`](../.github/workflows/structural-quality-audit.yml), and [`.github/workflows/wiki-sync.yml`](../.github/workflows/wiki-sync.yml); keep the structural workflow as the single documentation guard.
