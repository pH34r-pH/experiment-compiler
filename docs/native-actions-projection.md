# Offline native Actions projection

`project-actions` derives an inert controller handoff from independently pinned
package and profile bytes. It performs no dispatch, asset download, member
execution, or attempt creation. The result always has `dispatchAuthorized: false`.
This is preparation for the native runner in #119, not native runner qualification.
The existing live runner remains container-only.

```sh
python -m experiment_compiler project-actions plan.zip \
  --expected-sha256 "$PLAN_SHA256" \
  --profile worker-profile.json \
  --expected-profile-sha256 "$PROFILE_SHA256" \
  --output projection.json
```

Successful exit means projection succeeded; it does not mean execution is admitted.
Output is deterministic and cannot overwrite different existing bytes. Both input
hashes must be selected independently by the caller. Candidate package contents
cannot select or authenticate a worker profile.

## Source and controller contracts

The source-owned `experiment/runner.json` must explicitly contain these four fields:

```json
{"runner":"fleet","executionMode":"fleet-native-v1","workerProfileId":"example-cpu","maxWallSeconds":120}
```

CWL remains the executable specification: one CWL v1.2 `CommandLineTool`, the
existing restricted CWL/job admission rules, explicit resource and time limits,
and disabled network access. A source-owned `DockerRequirement` is rejected;
the projection never translates away a container requirement. CWL and job documents
are each bounded to 1 MiB before parsing. Package verification and archive size
limits remain in force.

The separately pinned JSON worker profile has exactly these fields:

| Field | Contract |
| --- | --- |
| `schemaVersion` | Integer `1` |
| `id` | Lowercase literal slug, at most 64 characters |
| `executionMode` | `fleet-native-v1` |
| `platform` | `os`: `linux` or `windows`; `arch`: `x86_64` or `aarch64` |
| `workflow` | `owner/repo/.github/workflows/name.yml@` plus a full 40-character commit |
| `limits` | Positive finite `cores`, `ramMiB`, `tmpdirMiB`, `outdirMiB`; integer `wallSeconds` up to 86400 |
| `capabilities` | All eight capability states listed below |
| `qualificationEvidenceSha256` | Lowercase SHA-256 or `null` |
| `stagedAssets` | At most 128 logical asset identity records |

Capability keys are `foregroundExclusion`, `processTreeCleanup`,
`resourceEnforcement`, `networkIsolation`, `candidateCredentialIsolation`,
`assetVerification`, `startedTerminalRetention`, and `lossRecovery`. Each value is
`qualified`, `unknown`, or `unsupported`. Qualification claims and a supplied
evidence digest are retained as profile identity, not authenticated by this command.
Even an entirely self-declared qualified profile produces a blocked projection.

The dependency closure must declare `runtimeNetworkRequired: false`, no
`classifications.unavailable` entries, and `classifications.external` matching
`stagedAssets` exactly. Each external asset record has only `path`, `sha256`, and
positive integer byte `size`. Paths are **archive-relative**: a CWL job File path
`models/model.gguf` maps to asset path `experiment/models/model.gguf`. Embedded and
staged paths together must have no duplicates, case collisions, traversal, or
file/directory collisions. No physical controller path or asset bytes are read.

## Execution and evidence gate

The output binds package, profile, CWL, and job hashes, limits, platform, staged
asset identities, and blockers. Its `jobs` fragment targets the pinned controller
workflow with a single-profile matrix and maximum parallelism one. It contains no
workflow triggers, credentials, or dispatch operation. Matrix concurrency is not
proof of whole-host resource exclusion.

Before any native execution, the owning Fleet controller must authenticate profile
qualification, verify staged bytes, enforce exclusion and process/resource/network
boundaries, and qualify native attempt retention and loss recovery. Existing
container receipts cannot be relabeled as native evidence. A missing terminal
record after worker loss remains unknown; projection success supplies neither a
started nor a terminal record.

Qualification on 2026-10-04 used offline fixtures only. The regression suite covers
pinned identities, preserved Docker semantics, resource/network admission, staged
File references, hostile paths, parser limits, and absence of dispatch. It makes
no physical-worker or model-performance claim.

## Controller receipt retention API

`experiment_compiler.native_receipts` supplies an explicit
`experiment-compiler.native-runner/v1` producer for future controller integration.
It launches nothing and does not admit a worker. There is no dispatch or native
executor attached to this API. Only offline fixture records have been tested.

The controller calls `start_native_attempt(directory, identity, started_at=...)`
after its own admission and before launch, then
`finish_native_attempt(directory, observations, expected_started_sha256=...)`
after observing the process outcome. Both return the SHA-256 of the snapshot
written. The parent directory must already exist and be owned by the controller,
outside candidate write access. Persistence currently requires POSIX directory
`fsync`; native Windows persistence still needs a qualified implementation.

The identity contains a 12-character hexadecimal scientific `attemptId`, exact
`sourceRepository` and 40-character `sourceCommit`, and SHA-256 pins named
`planPackageSha256`, `profileSha256`, `runtimeSha256`, `stagedAssetsSha256`, and
`requestSha256`. The runtime pin identifies the controller's exact runtime artifact;
the staged-assets pin identifies its canonical asset inventory. The controller
must independently verify those bytes; the receipt API only validates digest
syntax. `controller` separately binds its repository, `.github/workflows/*.yml`
(or `.yaml`) path, 40-character commit, positive `runId` and `runAttempt`, and job
identifier. A GitHub retry is not authorization to reuse a scientific attempt ID.
The caller owns new attempt allocation and protocol attempt budgets.

The started snapshot fixes all identities and the timezone-aware `startedAt`.
Terminal observations supply `status`, timezone-aware `endedAt`, nonnegative
monotonic `wallSeconds`, nullable integer `exitCode`, bounded `collectionErrors`,
and `resourceMeasurements` containing `peakProcessMemoryBytes` and `cpuSeconds`.
Unmeasured resources are `null`, never inferred from a requested resource limit.
Terminal states are `succeeded`, `failed`, `timed-out`, and `cancelled`; process
success requires exit zero and no collection errors. Output rejection can be
retained as failure with collection errors even after exit zero. Cancellation
and timeout remain distinct even if cleanup exits successfully.

The existing owning runner persistence helper writes the latest
`runner-receipt.json`. Native attempts additionally retain write-once
`started-receipt.json` and `terminal-receipt.json`; terminal records bind the exact
started digest in `startedReceiptSha256`. Started evidence cannot be rewritten,
terminal evidence cannot be replaced, and unexpected or symlinked members reject
writes. A persistence exception must stop launch; a retained snapshot after an
exception is not an acknowledgement of durable remote archival. Controllers must
retain started evidence through their existing archive before disposable cleanup.

A missing terminal after worker loss remains an unresolved started attempt;
the API does not fabricate an end time, duration, exit code, or success. These
receipts explicitly use controller-reported identity, not independent attestation.
Even `succeeded` proves neither result-package completion nor scientific acceptance.
Result packaging, qualified execution, and archive transport remain separate work.
Existing container receipt and package bytes are unchanged.

## Trusted native controller consumer

`experiment_compiler.native_controller.run_native_controller` coordinates one
native attempt through an explicitly injected Fleet authority. It reuses the
owning package/profile/CWL/job admission and native receipt APIs. It supplies no
default executor, scheduler, dispatch credentials, isolation implementation, or
native CLI. The offline `project-actions` output remains inert and unchanged.

The caller supplies package/profile paths, a controller-owned attempt directory,
and `selection` containing `packageSha256`, `profileSha256`, `runtimeSha256`,
`attemptId`, and the receipt `controller` identity. Package and profile bytes are
verified against those independent pins. The scientific repository and commit
come from the verified package manifest; controller repository/workflow/commit
must match the profile's pinned workflow exactly. All required profile capability
states must be `qualified` with a qualification-evidence digest, but those source
assertions alone never authorize execution.

The API constructs an immutable `NativeRequest`: verified package bytes, verified
profile bytes, and canonical specification bytes binding exact source, runtime,
asset inventory, CWL/job hashes, resource limits, wall budget and controller
identity. This request is submitted to `authority.lease(request)`. A trusted
Fleet implementation must independently authenticate the qualification evidence,
verify current target runtime/asset bytes, admit protocol attempt/resource/deadline
budgets, acquire shared foreground ownership, and enforce candidate credential,
network and process boundaries. Unknown, expired or unsupported qualification
must raise before entering the lease. The API checks that the returned lease
binds the exact request and qualification-evidence digests; it does not treat an
echoed digest as proof of physical qualification. The authority implementation and
Python environment must remain outside candidate control.

Within that qualified lease, the controller performs these steps:

1. Persist the immutable started snapshot using the existing native receipt API.
2. Call `lease.retain(receipt_bytes, expected_sha256=...)` and require an exact
   digest acknowledgement of retention outside disposable worker state.
3. Call `lease.execute(request)` once. The trusted adapter owns bounded execution,
   observed exit/cancellation/timeout status, cleanup, and measured resources. It
   returns the native receipt observation fields; unknown outcome must raise.
4. Validate and persist the immutable terminal snapshot, then retain those exact
   bytes through the same transport acknowledgement.
5. Release the authority's exclusive lifetime. Return receipt hashes and the
   observed operational status, never a scientific or result-package success claim.

Any admission, persistence or started-retention failure prevents the executor
call. Execution transport loss or invalid observations leave the started snapshot
unresolved. Terminal transport loss preserves the locally observed terminal
snapshot and raises; it neither rewrites that observation nor retries execution.
The trusted authority must retain failures and release ownership even when a
callback raises. Successful cleanup cannot change an observed failed process into
success. A missing terminal is not evidence of cancellation or timeout.

The consumer is qualified only by injected offline fixtures here. A real Fleet
authority, executable native adapter, durable transport, and physical profile
qualification are still necessary. Its local receipt store remains POSIX-only.
No workflow is wired to this consumer by this change, and no physical workload
was launched in its tests.
