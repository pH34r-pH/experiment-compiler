# Limits and trust boundaries

Experiment Compiler is intentionally strict about what it proves.

## Compile does not prove

Compilation does not prove scientific validity, completeness of a method, standards conformance, authorship, safe execution, or reproducibility.

## Verify does not prove

Verification proves the package satisfies the selected byte/inventory contract and, when externally pinned, matches an expected package identity. It does not establish that the expected bytes are trustworthy.

## Runner does not prove

A completed process establishes that the admitted workflow ran within the encoded runner/evidence scope. It does not turn process success into a scientific conclusion.

## Finalization does not prove

Finalization carries source-authored interpretation and review. It does not independently validate that interpretation or grant publication authorization.

## Sandbox boundary

The current runner path is deliberately bounded and not a general service for arbitrary adversarial workflows. Stronger untrusted-workload support requires stronger isolation, such as disposable credential-free compute, rather than relaxing admission checks.

## Artifact size and operational limits

Compiled Experiment validity has no arbitrary member-count, per-file, or total-byte ceiling. Research artifacts range from small code bundles to multi-gigabyte datasets/checkpoints, and the packaging contract should follow the selected venue/repository rather than a local constant.

Operational execution remains resource-bounded: the reviewed CWL/worker envelope controls CPU, RAM, temporary/output storage, and wall time. Publication targets may also impose real transport quotas. Those are execution or destination constraints, not properties of RO-Crate/CWL or of a scientifically valid Compiled Experiment.
