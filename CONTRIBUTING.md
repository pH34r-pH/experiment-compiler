# Contributing

Human and agent contributions are welcome under Apache-2.0; preserve attribution and provenance. Open an issue describing the reproducibility problem and the existing open standard/profile that should address it before extending the wire format.

Use Python 3.11+ and run `python -m unittest discover -s tests -v`. There are no runtime or test dependencies outside the standard library. The optional wheel build uses setuptools declared in pyproject.toml.

Before adding a normative package constraint (size/count limits, required metadata, archive layout, publication rule, etc.), identify its authority: an adopted external standard/profile, a selected venue/repository requirement, or an explicit compatibility target. If it is only an implementation or security safeguard, keep it in the operator/runner layer, make its scope explicit, and do not encode it as scientific artifact validity. Prefer established community distribution patterns (e.g. archival DOI repositories and canonical immutable model/dataset revisions) over a new local packaging rule.

Add small, source-owned examples and tests instead of hard-coded scientific facts in the compiler. Hash-sensitive fixtures must have explicit reviewed provenance. Do not use public issues for private credentials or data; report only a minimal redacted reproducer and arrange a private maintainer channel for sensitive details.
