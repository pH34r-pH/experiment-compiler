---
artifact: implementation-plan
revision: 3
acceptance: candidate-review
---

# Archive read policy implementation plan

This plan owns the technical choices for the archive read policy. Behavior and acceptance live in its specification; requirements are referenced by ID.

## Implementation targets and direct path

**Component and consumers.** `experiment_compiler/archive_policy.py` (policy, errors, member reader) and `experiment_compiler/archive_policy_cli.py` (shared options) implement ARP-FR-001 through ARP-FR-014. Real consumers are package verification in `core.py`, the runner's package opening, and the `verify`, trajectory, corpus and `experiment-runner run` command parsers; those consumers are unspecified areas.

**Current and target implementation.** The current implementation is Python (3.11.4 or later) using only the standard library: `dataclasses`, `hashlib` and `zipfile`. This is the accepted implementation decision; no port is planned.

**Reuse.** `zipfile.ZipInfo` supplies declared `file_size` and `compress_size`; `hashlib.sha256` supplies digests; `argparse` supplies option parsing. No third-party dependency.

**Direct path and state.** `PackageReadPolicy` is a frozen dataclass of four optional integers validated in `__post_init__`. `ArchiveMemberReader` owns one mutable counter, the running total of observed bytes, for the lifetime of one archive read; constructing it with a policy checks the inventory immediately. `read` streams each member in chunks of at most 1 MiB, updating the digest and the counters, checking the policy after each chunk and before extending captured bytes. Budgets are runtime inputs; the chunk size and check order are fixed.

**Layer dispositions.**

| Layer | Disposition | Duty |
| --- | --- | --- |
| `PackageReadPolicy` dataclass | Retain | Validated, immutable budgets (ARP-FR-001, ARP-FR-002). |
| `ResourcePolicyError` subclass of `PackageError` | Retain | Lets callers distinguish a local refusal from a malformed package while still catching both, and reports the observed value (ARP-FR-007, ARP-FR-010). |
| `ArchiveMemberReader` | Retain | Single-pass hashing with optional capture and observed-byte checks (ARP-FR-005, ARP-FR-008). |
| CLI option helpers | Retain | One definition shared by every package-reading command (ARP-FR-009). |

**Objective and preserved acceptance.** Preserve every ARP requirement; no performance claim is made.

**Unresolved decisions and local discretion.** None open. Implementation may choose the chunk size and private helper names; the public interface and message format below are fixed.

## Public interface

Other modules and tests depend on these exact names; they are part of the interoperability contract.

- `archive_policy.PackageError(ValueError)`: malformed, unsafe or mismatched package input.
- `archive_policy.ResourcePolicyError(PackageError)`: constructed as `ResourcePolicyError(budget: str, limit: int, *, member: str | None = None, observed: int | None = None)`, exposing `budget`, `limit`, `member` and `observed`.
- `archive_policy.PackageReadPolicy`: frozen dataclass with fields `max_member_bytes`, `max_total_bytes`, `max_members`, `max_compression_ratio`, each `int | None`, defaulting to `None`; equality compares fields.
- `archive_policy.ArchiveMemberReader(archive: zipfile.ZipFile, policy: PackageReadPolicy | None)`: checks the declared inventory on construction; `read(info: zipfile.ZipInfo, *, capture: bool = False) -> tuple[str, int, bytearray]` returns `(sha256 hex digest, expanded size, captured bytes)`; attribute `total_bytes: int` is the running observed total.
- `archive_policy_cli.add_archive_policy_options(parser: argparse.ArgumentParser) -> None` adds the four options in one argument group; their `argparse` destinations are the field names.
- `archive_policy_cli.archive_policy_from_args(args: argparse.Namespace) -> PackageReadPolicy | None`.
- `archive_policy_cli.ARCHIVE_POLICY_OPTIONS: frozenset[str]` equals `{"max-member-bytes", "max-total-bytes", "max-members", "max-compression-ratio"}`.

Checks run in this order (ARP-FR-013): member count, then per member in archive order its member bytes, the running total and its ratio. While a member is read the same order applies after each chunk. The message format is `Local resource policy refused package[ for member 'NAME']: BUDGET exceeds LIMIT (observed VALUE)`.

## Decisions that follow Python semantics

The command-line integer parser is `int(text)` followed by a positivity check, so accepted forms are exactly those Python's `int()` accepts (ARP-FR-014). The observed ratio is `ceil(expanded / compressed)` computed with integer arithmetic, with the expanded byte count reported when the compressed size is 0 (ARP-FR-011).

## Prior-art decisions

Inclusive budgets checked against declared ZIP sizes first and observed expanded bytes second follow the standard defense against ZIP bombs: declared sizes are untrusted, so observed bytes are the authority, and declared sizes allow an early refusal. The Python `zipfile` documentation for 3.11 and later ([Decompression pitfalls](https://docs.python.org/3/library/zipfile.html#decompression-pitfalls)) describes the risk; no code is copied.
