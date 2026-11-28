---
artifact: independent-test-contract
revision: 3
acceptance: candidate-review
---

# Archive read policy independent test contract

Cases are authored from the accepted specification. Expected results never come from running or reading the implementation. Tests bind only to the public interface named in the plan: `PackageReadPolicy`, `ResourcePolicyError`, `PackageError` and `ArchiveMemberReader` (including `total_bytes`) in `experiment_compiler.archive_policy`, and `add_archive_policy_options`, `archive_policy_from_args` and `ARCHIVE_POLICY_OPTIONS` in `experiment_compiler.archive_policy_cli`. The executable cases are in `tests/test_spec_archive_read_policy.py`.

| Case | Requirements | Stimulus | Expected observable result |
| --- | --- | --- | --- |
| ARP-001 | ARP-FR-001, ARP-SC-001 | Read a large, highly compressible member with no policy and with a policy whose budgets are all unset. | Both reads succeed; no resource refusal. |
| ARP-002 | ARP-FR-002, ARP-SC-001 | Construct policies with 0, -1, True, 1.5 and "3" for each budget, and with a positive integer. | Each invalid value raises a value error naming the budget; positive integers are accepted. |
| ARP-003 | ARP-FR-003, ARP-FR-004, ARP-SC-001 | A two-member archive. For each budget, a policy at exactly the declared value, then one below it, applied when a reader is constructed. | At the exact value the reader is constructed; one below raises a resource-policy error for that budget without opening any member. |
| ARP-004 | ARP-FR-005, ARP-SC-001 | A one-member archive of 10 bytes, total budget 15. Read the member twice, capturing on the second read. | After the first read `total_bytes` is 10; the second raises a resource-policy error for the total budget naming the member instead of returning captured bytes. |
| ARP-005 | ARP-FR-006, ARP-FR-003, ARP-SC-001 | A compressible member with expanded size E and compressed size C. Ratio budgets ceil(E/C) and ceil(E/C) - 1. | The first is accepted; the second is refused for the ratio budget naming the member. |
| ARP-006 | ARP-FR-007, ARP-SC-001 | Trigger a member-bytes refusal, a total-bytes refusal and a member-count refusal. | All are resource-policy errors and package errors with budget and limit; the member-bytes and total-bytes refusals carry and name the member; the count refusal has no member; messages state the local refusal and which budget exceeded which limit. |
| ARP-007 | ARP-FR-008, ARP-SC-001 | Read a member with and without capture; read a member whose declared size is larger than its content. | Digest equals SHA-256 of the content; size is the content length; bytes are empty without capture and equal the content with capture; the size mismatch raises a package error naming the member. |
| ARP-008 | ARP-FR-009, ARP-SC-001 | Parse each option with valid and invalid values, and parse no options; inspect the exposed option-name set. | Valid values produce a policy with exactly those budgets; 0, -1, 1.5 and text are usage errors mentioning a positive integer; no options produce no policy; `ARCHIVE_POLICY_OPTIONS` is exactly the four names without dashes. |
| ARP-010 | ARP-FR-011, ARP-SC-001 | A member declared as 100 expanded and 7 compressed bytes with ratio budget 3; a member declared as 5 expanded and 0 compressed bytes with ratio budget 1; a member declared as 0 expanded and 0 compressed bytes with ratio budget 1. | The first refusal has `observed` 15 (ceiling of 100/7); the second is refused with `observed` 5; the third is not refused. |
| ARP-011 | ARP-FR-012, ARP-SC-001 | A one-member archive of 10 bytes, total budget 15. Read it twice. | The second read is refused for the total budget and `total_bytes` is then greater than 15. |
| ARP-012 | ARP-FR-013, ARP-SC-001 | Two members with `max_members` 1 and `max_member_bytes` 1: one member of 1000 bytes with `max_member_bytes`, `max_total_bytes` and `max_compression_ratio` all exceeded; the same member with only total bytes and ratio exceeded. | Member count is named first; then member bytes; then total bytes when member bytes is not exceeded. |
| ARP-013 | ARP-FR-014, ARP-SC-001 | Parse `+3`, ` 3 ` and `1_000` as `--max-members`. | They are accepted as 3, 3 and 1000. |
| ARP-009 | ARP-FR-010, ARP-SC-001 | Trigger a member-bytes refusal (5 bytes, limit 4) and a member-count refusal (2 members, limit 1). | Each refusal carries `observed` equal to the value that exceeded the limit (5 and 2), and each message states that observed value. |
