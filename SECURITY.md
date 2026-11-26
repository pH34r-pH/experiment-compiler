# Security policy

Please report security vulnerabilities through GitHub private vulnerability reporting / Security Advisories when available. Do not publish a weaponized package, private research payload, secret, or exploit in an ordinary issue.

Security-sensitive surfaces include archive/path validation, digest/inventory verification, lifecycle provenance, runner admission, CWL/reference handling, package extraction by downstream tooling, and CI privilege boundaries.

The verifier intentionally does not execute or extract package members. The bounded runner is not a general sandbox for arbitrary adversarial workflows; stronger untrusted execution requires stronger disposable isolation.

Archive verification can consume memory and CPU. Operators can set expanded-byte,
member-count, and compression-ratio budgets for verification or runner admission.
See the [verification contract](docs/build-recipe.md) for the CLI options and library API.
Limits include the manifest and typed metadata. Python reads ZIP directory records
before the member-count check, and JSON text and Python objects require additional
memory beyond the encoded bytes. A large or costly archive may still require a
separate process with controlled memory, CPU, and time limits.

The local CWL runner verifies current cgroup v2 bounds under a trusted launcher
and host. The observation covers the visible hierarchy and the process sampled
at admission. A privileged host can change those controls after observation.
Fleet qualification must independently establish launcher, host, and any
container-backed worker identity. Workflow-image checks and tool network policy
cover the inner CWL execution boundary.
