# Comparison and evidence boundaries

Compilation and offline verification establish retained bytes and package integrity.
They do not validate a study's comparison rule, scientific acceptance, or winner.
The study owns its endpoint, units, denominator, reference selection, uncertainty
rule, checks, and interpretation. A completed execution can produce a failed
scientific check. Missing reference bytes or mismatched samples/units can leave a
source-authored comparison inconclusive even when the package verifies.

## Optional selected-attempt coverage record

Finalization can optionally carry a source-authored coverage record without
changing historical packages or deciding scientific validity. This first slice
supports **one selected attempt**. It does not reconcile a campaign inventory,
retry lineage, protocol amendments or other attempts; its readout labels the
campaign attempt inventory `unassessed`.

Before execution, the study can include a JSON member in its exact plan. The
member binds the plan's actual RO-Crate main protocol by path and SHA-256 and
enumerates stable obligation IDs, roles and applicability, for example:

```json
{
  "schema": "compiled-experiment-obligations/v1",
  "protocol": {"path": "experiment/protocol.md", "sha256": "<protocol sha256>"},
  "obligations": [
    {"id": "primary-1", "role": "primary", "applicability": "always"},
    {"id": "calibration", "role": "diagnostic", "applicability": "always"}
  ]
}
```

Use actual lowercase SHA-256 strings in place of placeholders. The protocol and
obligation file must be members of the selected attempt's digest-verified plan;
the obligation file's own digest is supplied in the coverage record. The format
requires explicit applicability text but does not evaluate it. A plan's bytes
alone do not prove when a scientist saw an outcome or whether its design was
appropriate.

At finalization, supply `--coverage-record path/to/coverage.json`, or the
`coverage-record` lifecycle component parameter. The JSON binds the exact plan,
selected attempt and frozen obligation member:

```json
{
  "schema": "compiled-experiment-coverage/v1",
  "planSha256": "<plan sha256>",
  "selectedAttemptId": "<attempt id>",
  "frozen": {"path": "experiment/obligations.json", "sha256": "<obligation file sha256>"},
  "resolutions": [
    {"id": "primary-1", "status": "observed", "attemptId": "<attempt id>",
     "outputPath": "<selected receipt output path>", "outputSha256": "<output sha256>",
     "finding": "source reports a failed primary check"},
    {"id": "calibration", "status": "omitted", "reason": "not collected"}
  ]
}
```

An `observed` resolution must point to an output actually listed, hash-checked
and retained by the selected attempt. It is a **source assertion** about what
that file means; the compiler does not parse the output to verify the finding.
`omitted` and `pending` require reasons; an obligation with no resolution is
`unreported`. Duplicate IDs, unknown obligations, wrong plans/attempts and output
identity mismatches fail before finalization writes a package. Only `observed`
and reasoned `omitted` are source-accounted; only `observed` contributes to
`sourceReportedObservedAsPlanned`. Neither flag says the finding passed.

The exact coverage JSON is retained as `evidence/protocol-coverage.json`,
linked to the selected action and exact parent package in the final RO-Crate.
The finalization receipt returns the per-obligation readout. To retrieve it later:

```sh
python -m experiment_compiler inspect-coverage final.zip --expected-sha256 <final-sha256>
```

The command verifies the final package, its parent attempt,
embedded plan, protocol and coverage links without running package members.
Packages without a coverage record report `unassessed`; their original bytes
and identities are unchanged. This opt-in local record is not a new registry or
an automatic public catalog or publication action.

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
