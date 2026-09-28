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

## Package limits

Entry and byte limits are security/operability boundaries. Large-model research should inventory closure first and design an explicit scalable artifact strategy rather than simply increasing ZIP limits until a package fits.
