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
python -m pip install '.[runner,mutation]'
```

The initial pilot intentionally targets the small deterministic resource conversion module:

```sh
python -m pytest -q tests
irradiate run experiment_compiler/resources.py \
  --report json --output irradiate-report.json \
  --verify-survivors
python scripts/validate_mutation_report.py irradiate-report.json --require-mutants
```

The lifecycle mutation job also targets the verifier's archive read policy and
the runner's worker-limit admission. It uses the existing archive tests and
synthetic cgroup observations without launching a workload. Run the next pass
from a fresh checkout, or move aside the `.irradiate` cache first. Irradiate
0.4.3 can reuse coverage collected for another path.

```sh
irradiate run experiment_compiler/archive_policy.py experiment_compiler/worker_limits.py \
  --report json --output irradiate-admission-report.json \
  --verify-survivors --workers 2 \
  --pytest-args=-k --pytest-args='archive_policy or verifier_opens_lifecycle or WorkerLimitTests'
python scripts/validate_mutation_report.py irradiate-admission-report.json \
  --require-file experiment_compiler/archive_policy.py \
  --require-file experiment_compiler/worker_limits.py
```

The two reports retain the resource-conversion baseline and show the observed
results by source file. The admission report must include a killed or surviving
mutant for each target. The extra pass has a four-minute step timeout in the
existing ten-minute job. Run Irradiate on Linux, where its worker process uses Unix
sockets.

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
