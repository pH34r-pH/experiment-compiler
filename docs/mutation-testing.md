# Mutation-guided testing

Experiment Compiler uses existing mutation and test-generation tools rather than a repository-specific mutation framework.

## Standard report contract

Machine-readable mutation evidence uses the Stryker / Mutation Testing Elements
`mutation-testing-report-schema`. The repository pins the upstream
`mutation-testing-report-schema` 3.9.0 JSON Schema verbatim at
`schemas/mutation-testing-report-schema-3.9.0.json`.

Reports are validated with:

```sh
python scripts/validate_mutation_report.py path/to/report.json
```

The schema is the interchange contract. Tool-native caches and internal formats remain tool-owned.

## Python mutation engine

The first evaluated engine is `irradiate==0.4.3` because it emits
Mutation Testing Elements schema-v2 JSON directly and supports diff-scoped CI runs.
It is still an alpha-stage project, so adoption is evidence-driven rather than exclusive;
`mutmut` remains a mature fallback if the pilot exposes compatibility or correctness problems.

Install the bounded mutation toolchain with:

```sh
python -m pip install '.[mutation]'
```

The initial pilot intentionally targets the small deterministic resource conversion module:

```sh
python -m pytest -q
irradiate run experiment_compiler/resources.py \
  --report json --output irradiate-report.json \
  --verify-survivors
python scripts/validate_mutation_report.py irradiate-report.json --require-mutants
```

No mutation-score threshold is enforced during the pilot. Surviving mutants are evidence to review,
not a requirement to manufacture tests until the score reaches 100%.

## Test generation

Candidate generators are installed separately:

```sh
python -m pip install '.[testgen]'
```

- Pynguin provides automated Python test generation and mutation-analysis assertion filtering.
- Hypothesis is preferred when a compact property or invariant can replace many concrete examples.

Generated tests are candidates, not automatically trusted repository truth. An accepted generated test
must be deterministic, pass the unmodified implementation, exercise intended public behavior, and
provide useful behavioral discrimination without overfitting to a particular mutant.

## Planned ratchet

After the pilot is understood, PR checks should prefer changed-code mutation analysis and compare
against an established baseline. We should not introduce a global 100% mutation-score target.
