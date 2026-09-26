# Contributing

Human and agent contributions are welcome under Apache-2.0; preserve attribution and provenance. Open an issue describing the reproducibility problem and the existing open standard/profile that should address it before extending the wire format.

Use Python 3.11+ and run `python -m unittest discover -s tests -v`. There are no runtime or test dependencies outside the standard library. The optional wheel build uses setuptools declared in pyproject.toml.

Add small, source-owned examples and tests instead of hard-coded scientific facts in the compiler. Hash-sensitive fixtures must have explicit reviewed provenance. Do not use public issues for private credentials or data; report only a minimal redacted reproducer and arrange a private maintainer channel for sensitive details.
