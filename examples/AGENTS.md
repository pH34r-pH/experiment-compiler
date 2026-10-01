# `examples/` map

Examples are source-owned research/package fixtures, not generic test data. Read the repository [`AGENTS.md`](../AGENTS.md), [`docs/poc-scope.md`](../docs/poc-scope.md), and [`docs/build-recipe.md`](../docs/build-recipe.md) before editing a recipe or member.

## Boundaries

- `issue-164/` is the historical #164/AdamW compatibility package. Its member bytes, reference manifest, terminology, dates, and hashes are immutable regression evidence. Do not “repair” it or add new execution claims.
- `linear-regression-v1/` is the self-contained reproducibility package used by compiler and independent-reproduction checks.
- `linear-regression-plan-v1/` and `linear-regression-frozen-lifecycle-v1/` are reviewed plan/attempt lifecycle fixtures used by CI.
- `muon-comparison-plan-v1/` is a prospective comparison plan with explicit external prerequisites; its missing inputs must remain documented as unavailable.
- `muon-unit-hypersphere-depth3-multiseed-v1-final-87409154/` is a retained published artifact and evidence boundary; preserve its package and receipts.
- `pipeline-finalization-fixture/` and `resource-profile/` support synthetic lifecycle/resource validation only.

Each `experiment.json` declares the profile, exact member paths, hashes, sizes, and (where applicable) expected package identity. `source/`, `inputs/`, `evidence/`, and metadata files are package members only when declared. A new scientific or protocol state requires a new example identity and provenance link.

## Change routing and validation

- Recipe or member contract: update the owning example and its documentation, then run `python -m unittest discover -s tests -v`.
- Historical #164 compatibility: run `python -m experiment_compiler compile examples/issue-164/experiment.json --output /tmp/experiment-compiler-issue-164.zip` and `python -m experiment_compiler verify /tmp/experiment-compiler-issue-164.zip --recipe examples/issue-164/experiment.json`; do not modify the fixture to satisfy the check.
- Lifecycle crate metadata: run `python scripts/validate_current_ro_profiles.py "$RUNNER_TEMP/runner-output/linear-regression-frozen-lifecycle-v1/result.source"` in the reviewed lifecycle workflow path; the checked-in plan fixture is not itself a Process Run Crate.
- Source closure or package identity changes: update the relevant evidence-boundary documentation and test the resulting exact digest/manifest.

Never copy credentials, private source trees, deployment logic, or non-public datasets into an example.
