# Comparison and evidence boundaries

Compilation and offline verification establish retained bytes and package integrity.
They do not validate a study's comparison rule, scientific acceptance, or winner.
The study owns its endpoint, units, denominator, reference selection, uncertainty
rule, checks, and interpretation. A completed execution can produce a failed
scientific check. Missing reference bytes or mismatched samples/units can leave a
source-authored comparison inconclusive even when the package verifies.

`test_source_owned_comparison_failures_remain_integrity_valid_evidence` uses small,
explicitly illustrative source reports through the existing lifecycle profile. It
retains comparison/check JSON, attempt status JSON, and interpretation Markdown as
package members linked from the attempt. The cases cover mismatched reference
samples, completed execution with a failed check, missing reference evidence,
non-comparable units, failed execution, and source cancellation. They assert exact
payload retention, exact source-authored negative/inconclusive interpretation,
separate execution status, integrity-only verification, and reproduction `not-run`.
These reports are test inputs, not a new generic comparison schema or scientific
validator. An unavailable scientific reference is recorded inside the retained
source report; it is not represented as an unretained execution-result File.

Cancellation uses existing Schema.org FailedActionStatus for unsuccessful execution
and retains the source's precise `cancelled` status and termination reason in the
attempt evidence. It does not introduce a new Schema.org action status. Existing
plan-only tests separately establish zero actual attempts and no inferred scientific
interpretation; runner tests retain interrupted `started` receipts as incomplete.

These regressions establish a packaging boundary. They do not close the real-study
workflow acceptance, independent scientific reproduction, source compatibility
review, or the gated reuse campaign. Historical POC members and retained real Muon
evidence are unchanged.
