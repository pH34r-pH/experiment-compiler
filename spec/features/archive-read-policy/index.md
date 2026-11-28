---
schema_version: 1
document_id: experiment-compiler/archive-read-policy
role: feature
feature_id: archive-read-policy
revision: 3
target_status: mature-complete-product
approval_status: proposed
artifact_acceptance: candidate-review
delivery_status: designed-only
delivery_evidence: []
qualifiers: [living-spec-paradigm, brownfield-onboarded]
---

# Archive read policy

## Product description

An archive read policy is an optional, caller-owned set of operational budgets applied while reading a Compiled Experiment ZIP archive. It protects the reading process from oversized or highly compressed archives. It is a local resource decision, not a property of a valid Compiled Experiment: a package refused under one policy can be valid and readable under another.

A policy has four independent budgets, each optional: the maximum expanded bytes of one member, the maximum expanded bytes across all members, the maximum number of members, and the maximum expanded-to-compressed ratio of one member. Members include the package manifest. An unset budget imposes no limit, and reading without a policy imposes none.

Readers check the archive's declared inventory against the policy before opening any member, then count the bytes actually expanded while reading and check them again before keeping them. A member reader returns each member's SHA-256 digest and size, and keeps the member's bytes only when the caller asks for them. Operators set budgets with command-line options on the commands that read packages.

## Actors and acceptance scenarios

| Story | Actor and outcome | Independent acceptance scenarios |
| --- | --- | --- |
| ARP-US1 | An operator reads an untrusted package under local budgets | AS1: a package whose declared and observed sizes, member count and compression ratios are all at or below the set budgets is read normally. AS2: a package exceeding any set budget is refused with a resource-policy refusal that names the budget, its limit, the observed value and the member when one applies, before the oversized data is kept. AS3: with no budgets set, no resource refusal occurs. |
| ARP-US2 | A library caller hashes members in one pass | AS1: reading a member returns its SHA-256 digest and size, and returns its bytes only when capture is requested. AS2: a member whose expanded size differs from its declared size is rejected as a malformed package. |
| ARP-US3 | An operator sets budgets on the command line | AS1: each budget option accepts a positive integer. AS2: a zero, negative or non-integer value is rejected as a usage error. AS3: giving no budget options produces no policy. AS4: integer text written with a plus sign, surrounding spaces or underscores between digits is accepted as the integer it denotes. |

## Functional requirements

| ID | Required observable behavior |
| --- | --- |
| ARP-FR-001 | A policy has four optional budgets: maximum member bytes, maximum total bytes, maximum members and maximum compression ratio. An unset budget imposes no limit, and reading without a policy applies no resource limit. |
| ARP-FR-002 | Constructing a policy with a set budget that is not a positive integer fails with a value error naming the budget. Booleans are not integers for this purpose. |
| ARP-FR-003 | Budgets are inclusive: a value equal to its budget is accepted and a value greater than its budget is refused. |
| ARP-FR-004 | Before any member is opened, the member count (including the manifest), each member's declared expanded size, the running total of declared expanded sizes and each member's declared compression ratio are checked against the policy. |
| ARP-FR-005 | While a member is read, its observed expanded bytes and the reader's running total of observed expanded bytes across all reads are checked against the member, total and ratio budgets before those bytes are kept. The reader exposes that running total as `total_bytes`. |
| ARP-FR-006 | A member exceeds the compression-ratio budget when its expanded bytes are greater than its compressed bytes multiplied by the budget. |
| ARP-FR-007 | A budget refusal is a resource-policy error, which is a kind of package error, carrying the budget name, the limit and the member name. Member-bytes, total-bytes and compression-ratio refusals name the member whose bytes reached the budget; a member-count refusal names no member. Its message states that local resource policy refused the package, names the member when present, and states which budget exceeded which limit. |
| ARP-FR-008 | Reading a member returns its lowercase hexadecimal SHA-256 digest and its expanded size, and returns its bytes only when capture is requested; otherwise the returned bytes are empty. A member whose observed expanded size differs from its declared size is rejected with a package error naming the member. |
| ARP-FR-009 | The command-line options `--max-member-bytes`, `--max-total-bytes`, `--max-members` and `--max-compression-ratio` each accept a positive integer and reject anything else as a usage error stating that a positive integer is expected. When none is given, no policy is created; when any is given, a policy with exactly the given budgets is created. The set of these option names without leading dashes is exposed as `ARCHIVE_POLICY_OPTIONS` so other command definitions can refer to them. |
| ARP-FR-010 | A budget refusal also carries the observed value that exceeded the limit as `observed`, and its message states that observed value. |
| ARP-FR-011 | For a compression-ratio refusal, the observed value is the member's expanded bytes divided by its compressed bytes, rounded up. A member whose compressed size is 0 is refused for the ratio budget whenever its expanded bytes are greater than 0, and its observed value is its expanded bytes; a member with 0 expanded bytes is never refused for the ratio budget. |
| ARP-FR-012 | The reader's `total_bytes` counts every observed expanded byte it has read, including the bytes whose reading triggered a refusal, so after a refusal for the total budget `total_bytes` is greater than the limit. |
| ARP-FR-013 | When more than one budget is exceeded at once, the refusal names the first in this order: member count, then for each member in archive order its member bytes, total bytes and compression ratio. The same order applies while a member is read. |
| ARP-FR-014 | A command-line budget value is accepted when it denotes a positive integer in base 10 as Python's `int()` reads it: an optional plus sign, surrounding whitespace and single underscores between digits are allowed. Anything else is a usage error. |

## Success criteria

| ID | Observable measure |
| --- | --- |
| ARP-SC-001 | Every independent archive-read-policy case passes against the implementation. |

## Boundaries

Refusal behavior for corrupt or truncated archives, which the ZIP library reports itself, is unspecified. This feature does not decide whether a package is valid, which commands expose the options, or how verification and execution use the reader; those areas are unspecified. It does not bound JSON parsing memory or process resources.
