# Build recipe and verification contract

A build recipe is local packaging configuration, not an ontology or a substitute for Croissant or RO-Crate. Accepted profiles are `poc-v1`, which preserves the historical #164 package envelope byte-for-byte; `compiled-experiment-v1`, the first self-contained public reproduction example; and `compiled-experiment-lifecycle-v1`, the plan/attempt profile described below.

Required keys are `buildRecipeVersion` (integer 1), `id` (portable lowercase slug), `title`, `profile`, `manifest`, and nonempty `members`. Each member declares a relative `source` under the recipe directory, archive `path`, SHA-256 `sha256`, and byte `size`. No globs, shell commands, remote fetches or environment expansion are supported. Optional `expectedPackage` pins the complete ZIP hash and size for a compatibility fixture.

The historical `poc-v1` manifest header uses `schemaVersion`, `packageType`, `status`, `source`, `standards`, and `evidence`. `compiled-experiment-v1` adds an explicit `profile` field and requires `packageType: compiled-experiment` plus `status: reproducible`. The lifecycle profile uses manifest `schemaVersion: 2` and has no overloaded top-level status field. The compiler validates only the packaging envelope, the lifecycle relationships below, and the byte inventory; scientific meaning stays in the packaged standards/protocol/evidence files. Source repository/commit and evidence values are declared input, not independently authenticated by this tool. The compiler derives the complete `members` inventory; callers cannot inject a second generated manifest.

Compilation checks hashes before packaging, preserves raw input bytes, sorts archive paths, emits sorted/indented UTF-8 JSON with a trailing newline, sets every ZIP timestamp to 1980-01-01, uses Unix regular-file mode 0644 and DEFLATE level 9, and omits host paths/times from the package. An existing output is reused only when identical; different output bytes are never silently overwritten. Compression implementations can change output bytes across toolchains, so the POC's historical checksum remains a required gate. The build receipt records Python, zlib and compiler versions separately from the immutable ZIP.

Verification checks the exact member inventory, digests, sizes and JSON syntax. It rejects duplicate keys, duplicate/case-colliding/unsafe paths, symlinks, encrypted ZIPs, special files, missing/unlisted members and bounded-size violations. Limits are 1,024 ZIP entries, 16 MiB per member and 64 MiB total archive/uncompressed content. The verifier never extracts files or runs packaged code. Local source directories must not be concurrently modified by an adversary (this is not an operating-system sandbox).

`verify --recipe ...` also compares the manifest with the reviewed recipe. `verify --expected-sha256 ...` checks an external complete-package checksum. Without either external pin, success means self-consistency only: an attacker could change both content and its hashes. Hashes do not establish authorship or scientific validity. Receipts always state `scope: package-integrity-only` and `scientificReproduction: not-run`.

This release preserves the initial POC wire format. A future complete research package must use an explicitly versioned profile and a new digest; never silently “repair” historical member bytes or relabel a new scientific run as historical evidence.


## Lifecycle profile: plans, attempts and interpretation

`compiled-experiment-lifecycle-v1` is a versioned application profile over RO-Crate 1.3, Process Run Crate 0.6, Schema.org and PROV-O. It requires the conventional root member `ro-crate-metadata.json`; the RO-Crate root Dataset identifies the protocol as `mainEntity`, and that protocol CreativeWork carries Schema.org `creativeWorkStatus`. The profile does not define a second experiment-status enum.

A prospective crate may describe `potentialAction` with explicit `https://schema.org/PotentialActionStatus`. It must not claim Process Run Crate 0.6 conformance without at least one actual execution `CreateAction`. Every recorded execution action has an explicit Schema.org `actionStatus`, since Process Run Crate 0.6 treats an omitted value as success. `ActiveActionStatus`, `CompletedActionStatus`, and `FailedActionStatus` describe execution state; process success is not a scientific conclusion.

The protocol and analysis plan contain the question, hypothesis, controls, acceptance/stop rules and unresolved prerequisites. Actual attempts link immutable protocol/input identities and their Process Run Crate evidence. The source-owned decision document records interpretation, uncertainty and limitations. Package integrity, standards validation, dependency closure, independent reproduction and publication review remain distinct evidence scopes. The compiler does not infer scientific interpretation, readiness, authorization, or publication eligibility from an action status.

Protocol revisions use new package identities and preserve prior bytes; link their relationship with PROV-O `wasRevisionOf`. Do not modify an archived plan when results arrive: build a new result package that includes its selected plan and retained attempts. A plan with unavailable required inputs remains a useful documented plan, but it is not admitted as executable by this profile. CWL/resource closure and execution admission are defined separately in issues #12/#13.

The local lifecycle check is a narrow contract check, not RO-Crate or Process Run Crate certification. Existing v1 recipes and ZIPs retain their exact contracts and digests.

## Execution handoff prototype

`experiment-runner run` is a separate, opt-in command; compile and verify never execute package contents. It verifies the reviewed plan package digest and lifecycle profile, requires a declared bounded worker, an explicit no-network declaration, pinned cwltool version and CWL CPU/RAM/storage/time maxima that fit within the worker limits, then invokes the packaged CWL workflow. The current bounded adapter admits one self-contained `CommandLineTool` document only; CWL `Workflow`, `run` references and `$import`/`$include`/`$schemas`/`$graph`/`$base` document references are rejected before cwltool starts. Its job order is restricted to declared input names and packaged relative File objects; SALAD references, `cwl:tool`, and `cwltool:overrides` are rejected before execution. Each invocation creates a new result ZIP and sibling `.source/` directory containing the compiler recipe and all hashed package members, so `verify --recipe result.source/experiment.json` and catalog discovery can independently rebuild the exact package. The result package retains the original plan bytes, a distinct attempt ID, Process Run Crate metadata, CWL provenance files, logs, outputs and a runner receipt. A timeout or failed process still receives a separate failure artifact; the process status is not a scientific decision.

### Revising a plan after an attempt

The `experiment-compiler revise` command creates a new prospective plan from a source-authored recipe and one explicitly selected attempt package:

```sh
experiment-compiler revise attempt.zip \
  --expected-sha256 "$(sha256sum attempt.zip | cut -d ' ' -f 1)" \
  --attempt-id ATTEMPT_ID \
  --recipe revision-source/experiment.json \
  --output revision-plan.zip
```

The selected ID must match both the package's runner receipt and an executed Schema.org `CreateAction`; the receipt's embedded plan and output digests are checked against packaged bytes. The command carries the exact prior attempt package into the new crate and links the new protocol and package with PROV-O. The new plan remains prospective: its own attempt count is zero, and it contains no inferred interpretation or authorization. Source-authored changes, including the new recipe ID, protocol, resource estimates, and any future run configuration, remain reviewable in the `.source/` closure. Because the parent package is one ZIP member, this transition currently requires that package to fit the compiler's 16 MiB per-member limit. Repeated iterations remain bounded by the normal package limits; no limit is relaxed to accommodate them.

### Finalizing an attempt for review

After an attempt has been selected, the source owner supplies an interpretation record, a short Schema.org `abstract`, a publication review record, its `reviewBody`, and the reviewer's name:

```sh
experiment-compiler finalize attempt.zip \
  --expected-sha256 "$(sha256sum attempt.zip | cut -d ' ' -f 1)" \
  --attempt-id ATTEMPT_ID \
  --id experiment-final-v1 --title "Final experiment artifact" \
  --decision decision.md \
  --decision-summary "Source-authored interpretation and limitations." \
  --review publication-review.md \
  --review-summary "Source-authored review of this exact attempt for release." \
  --reviewer-name "Reviewer name" \
  --output final.zip
```

The final package carries the exact selected attempt ZIP and its complete verified member closure. Its RO-Crate describes the decision as a CreativeWork about the selected executed action, and the review as a Schema.org Review whose `itemReviewed` is the digest-addressed attempt file. PROV-O links both records and the final root to that package. The source-authored summary appears directly in catalog metadata; no conclusion is inferred from the runner's process status. The protocol CreativeWork remains `Draft` while the artifact awaits repository review. The attached review is source-authored evidence, not verified publication authorization; merging the reviewed package to `main` is the public release event. Failed, negative, or inconclusive experiments can all be finalized.

The public CI integration executes two repository-owned linear-regression fixtures through the same adapter: a prospective plan and a frozen known study. It pre-pulls the digest-pinned CWL tool image and invokes cwltool with pulling disabled, strict CPU/memory limits, explicit no-network access, a dedicated 64 MiB tmpfs, and workflow/CI wall-time limits. The workflow-runner host has no experiment-source or deployment credentials. It verifies that each original plan is unchanged, verifies each result package, checks Process Run Crate 0.6's current required contract, and derives catalog entries from the result source closures. The receipt includes the tool image ID and caller-reported runner context; it marks that context as unverified by the adapter. Direct CLI execution on a host is not sandboxed, and digest equality is integrity evidence rather than authorization. This prototype is not a service for arbitrary uploads. General adversarial workflows require a disposable credential-free VM or stronger isolation. CWL temporary/output directory requests are allocations, not hard quotas; the smoke fixture's actual temporary/output storage is also constrained by tmpfs and the compiler caps package/output bytes.

`examples/muon-comparison-plan-v1/` shows how a genuinely unstarted candidate can be represented before it is runnable: public rationale and a proposed question are bundled with source-owned unknowns and explicit missing prerequisites. It contains no CWL method, result, execution record, or numeric resource estimate. The adapter rejects it from its unavailable dependency inventory before checking for an executable workflow. Its status uses Schema.org lifecycle fields; no parallel Phase 3 status vocabulary is added to the artifact.

The retained finalization CI artifact is the completed handoff record. To publish it through normal repository discovery, promote the final package's extracted members, generated `experiment.json`, and package ZIP in a reviewed pull request. The package remains immutable; later protocol edits or iterations produce new IDs and link back with PROV-O `wasRevisionOf`. Scientific decision and publication review remain source-authored records; merge of the reviewed package is the publication event. A private lab may run this same pinned machinery inside its own trust boundary and retain plans, attempts, and unfinished results privately. Public promotion must use an explicitly reviewed shareable closure and a public recipe; the public compiler never fetches private source or credentials.

Resource field mapping, byte closure classes, private-workspace use, and the publication boundary are documented in [resources-and-closure.md](resources-and-closure.md). CWL v1.2 expresses executable requirements; resource evidence and estimate basis stay with the source-owned experiment/provenance records.


## Self-contained reproduction profile

`compiled-experiment-v1` does not authorize the compiler to execute package content. The public lifecycle separately exercises the known reviewed example after compilation: it verifies the ZIP, extracts it with Python's standard library, invokes the declared `experiment/reproduce.py` entrypoint, and compares the newly produced result bytes with the embedded reference result.

A self-contained example should make its dependency closure explicit. The first example embeds code, tests, data/license/splits, environment, configuration, protocol, acceptance criteria, reference evidence/receipt, and resource measurements. Open-standard specifications may remain immutable public references. Missing prerequisites belong in an explicit unavailable list rather than being inferred or silently fetched.


## Standards validation boundary

The self-contained example uses Croissant 1.1, RO-Crate 1.3 and Process Run Crate 0.6.

CI runs the pinned MLCommons `mlcroissant==1.1.0` validator against the packaged Croissant metadata. For RO-Crate / Process Run Crate, the repository gates the normative MUST-level contract relevant to this crate with `scripts/validate_current_ro_profiles.py`, directly matching the current profile URIs and required root/software/action relationships.

As of this implementation, `roc-validator==0.11.2` ships a Process Run Crate 0.5 profile and does not ship RO-Crate 1.3 / Process Run Crate 0.6 profiles. It therefore must not be presented as evidence of 1.3/0.6 conformance: auto-detection falls back to an older RO-Crate profile. When an external validator implements the current 1.3/0.6 pair, it should replace or supplement the local MUST gate. The local gate explicitly reports that it is a contract check, not standards certification.
