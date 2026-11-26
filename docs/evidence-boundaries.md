# Comparison and evidence boundaries

Compilation and offline verification establish retained bytes and package integrity.
They do not validate a study's comparison rule, scientific acceptance, or winner.
The study owns its endpoint, units, denominator, reference selection, uncertainty
rule, checks, and interpretation. A completed execution can produce a failed
scientific check. Missing reference bytes or mismatched samples/units can leave a
source-authored comparison inconclusive even when the package verifies.

## Optional selected-attempt coverage record

Finalization can carry a source-authored coverage record alongside the
scientific decision. The v1 record covers one selected attempt and labels the
campaign inventory `unassessed`. The optional v2 record adds an issued-attempt
census, retry lineage, dispositions, and verified outputs from other attempts.

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

## Campaign census and cross-attempt observations

Keep the same finalization command and change the coverage schema to
`compiled-experiment-coverage/v2`. Add a `campaign` object with
`inventory`, `inventorySha256`, and `outcomes`. Hash the embedded inventory
with the Compiler's `canonical` JSON encoding. The inventory carries
`campaignId`, the same `planSha256`, an offset-bearing ISO timestamp
`cutoff`, a `scope` of `complete` or `partial`, and the `issued` array.

Each issued entry identifies `attemptId`, a study-defined `replicateId`,
`issuedAt`, and `retryOf`. Use `null` for a root attempt. A retry must link
to an earlier issuance in the same replicate. The readout reports issued
attempts, retries and study-defined replicates separately. A complete
source-declared census requires a disposition for every issued ID. A
partial census keeps the campaign denominator unknown and exposes any
recorded attempt with a missing disposition or an unavailable retry parent.

Each outcome contains `attemptId`, `status`, `packageSha256`, and `reason`.
The selected parent must be `included` at its exact digest, with a `null`
reason. An included or `measured-excluded` outcome requires a successful
lifecycle attempt with retained outputs. All non-included outcomes need a
reason. The source can record `failed`, `interrupted`, `not-admitted`,
`lost`, or `unknown` with a `null` package digest. A failed outcome can
also point to a retained failed or timed-out lifecycle attempt.

Place any additional referenced ZIP next to the coverage file as
`<packageSha256>.zip`. Finalization verifies the exact bytes, runner
receipt, selected action, plan and timestamps. The added ZIP is retained
under `evidence/campaign-attempts/` and linked in the final crate. An
`observed` obligation can reference an exact retained output of an included
or measured-excluded attempt. Readback validates the same relationships
using only the final package and its expected digest.

The digest identifies the source census declaration, whose completeness
depends on the source producer. An interruption recorded without a package
is a source disposition. Amendments to a frozen protocol still require a
separate revision and a statement of what outcomes were available at the
time of the amendment.

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
