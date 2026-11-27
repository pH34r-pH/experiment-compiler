# Comparison and evidence boundaries

Compilation and offline verification establish retained bytes and package integrity.
They do not validate a study's comparison rule, scientific acceptance, or winner.
The study owns its endpoint, units, denominator, reference selection, uncertainty
rule, checks, and interpretation. A completed execution can produce a failed
scientific check. Missing reference bytes or mismatched samples/units can leave a
source-authored comparison inconclusive even when the package verifies.

## Controller diagnostics

Pass `--verbose` to `experiment-runner run` or `experiment-runner recover` for
failure diagnostics on stderr. Each record includes the operation, phase,
available attempt ID and package digest, and the exception class if available.
The publication attestation helper accepts the same flag for native verifier
failures. Python callers can configure the `experiment_compiler` logger.
Use the retained receipts for evidence and the CLI's standalone JSON for automation.

## Alpha release provenance

The owner can select `alpha-provenance` in the existing
[lifecycle workflow](../.github/workflows/lifecycle.yml) on `main`. It uses the
same run and attempt's Python 3.12 build to sign five verified subjects: the
historical POC ZIP, its checksum and two scoped receipts, and the Compiler
wheel. The signing job checks the downloaded archive digest and verifies every
GitHub attestation against the exact source commit and workflow. The candidate,
bundle, and signature readback are retained together on success.

The ordinary `pages` operation uses the existing Pages environment. An alpha
provenance run prepares a new release candidate for owner review. A draft from
an earlier build retains its own producer identity. Compare its five hashes
against the signed candidate and use a release target that matches the signed
source commit. GitHub Release publication still requires the owner decision.

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
is a source disposition.

## Protocol amendments and interpretation

Use `compiled-experiment-coverage/v3` with the same v2 campaign and frozen
plan bindings. Add `amendments`, `amendedResolutions`, and `interpretation`.
The amendment list is ordered by its source-declared `recordedAt` timestamps.
The first predecessor is the frozen obligation file's SHA-256. Later
predecessors are the SHA-256 of the previous amendment in the Compiler's
`canonical` JSON encoding.

For example, a study can revise the applicability of a retained diagnostic.

```json
{
  "id": "calibration-gate",
  "predecessorSha256": "<frozen obligation SHA-256>",
  "recordedAt": "2026-01-01T12:00:00Z",
  "reason": "Specify when to run calibration",
  "observedOutputs": [],
  "changes": [
    {"id": "calibration", "action": "revise", "role": "diagnostic",
     "applicability": "When instrumentation reports a discrepancy"}
  ]
}
```

A change may `add`, `revise`, or `withdraw` one obligation. A withdrawal
uses `null` for `role` and `applicability`. The same ID continues through
revisions. Earlier declarations and output evidence remain in the package.
Use the existing plan revision path when a change needs a new execution
protocol or input identity.

Each `observedOutputs` entry identifies an `attemptId`, `outputPath` and
`outputSha256` verified in an exact attempt that ended by the recorded time.
The source records which outputs it had observed. The Compiler separately
reports the verified attempt IDs already available then. Timing is
`post-observation` when an exact measured attempt had finished, and
`prospective` when a complete source census shows that execution had yet
to begin. A partial census or earlier unresolved execution leaves timing
`unknown` unless a verified outcome was already available.

A frozen obligation may carry `{"id":"calibration","status":"amended",
"amendmentId":"calibration-gate"}` in `resolutions`. This accounts for its
disposition while preserving the frozen role and applicability and leaving
it unobserved as planned. For the new declaration, include a wrapper with
`amendmentId` and a normal `resolution` in `amendedResolutions`. Readback
reports amended observations separately and shows whether each output was
available before the amendment.

`interpretation` carries source-authored `comparator`, `unitOfAnalysis`,
`uncertaintyMethod`, `limits`, and `endpoints`. Each endpoint binds an
`obligationId`, its `role` and the exact `amendmentId` or `null` for
the frozen declaration. `supersedes` is either `null` or the source's
`{finalPackageSha256, reason}` reference to an earlier finalization.
The interpretation continues to be a source declaration beside the
verified package identities and the runner's process outcomes.

## Reading the historical radix archive

Run the read-only [radix example](../examples/radix_archive_coverage.py) on
the canonical ZIP from the [original release](https://github.com/pH34r-pH/compiled-experiments/releases/tag/dsl-issue-152-v1.0.0).

```sh
python -m examples.radix_archive_coverage /path/to/issue152-replay.zip
```

The report verifies the release digest and binds its protocol, result
summary and trace manifest by SHA-256. The four primary pairs have
recorded failures and the separate calibration pair is unreported.
The frozen decision is zero of four validated, with three required.
Eight retained trace shards from the pinned model and tokenizer support
replay. Full model capture requires those external assets. The screening
tables are known by checksum and their original bytes are unavailable in
the archive. The 1,296 settings per pair describe the sensitivity grid.
Their scope is engineering proxies.

The command reports coverage from the archived evidence and leaves the
original release bytes intact. The archive owns its independent replay
record. Any later calibration result needs its own dated evidence and a
link to the original study.

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
