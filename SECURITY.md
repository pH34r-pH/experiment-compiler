# Security policy

Please report security vulnerabilities through GitHub private vulnerability reporting / Security Advisories when available. Do not publish a weaponized package, private research payload, secret, or exploit in an ordinary issue.

Security-sensitive surfaces include archive/path validation, digest/inventory verification, lifecycle provenance, runner admission, CWL/reference handling, package extraction by downstream tooling, and CI privilege boundaries.

The verifier intentionally does not execute or extract package members. The bounded runner is not a general sandbox for arbitrary adversarial workflows; stronger untrusted execution requires stronger disposable isolation.
