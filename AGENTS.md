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
