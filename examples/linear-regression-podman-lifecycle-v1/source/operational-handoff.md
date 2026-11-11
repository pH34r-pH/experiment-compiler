# Podman reuse handoff

This prospective operational revision prepares the existing frozen
linear-regression study for the `cwltool-podman` adapter. It supports the second
same-boundary reuse check in Experiment Compiler #87. It does not define another
scientific experiment or authorize a run.

## Preserved science and exact parent

The embedded parent is the original `linear-regression-frozen-lifecycle-v1`
Docker plan, SHA-256
`ce2c5582c3d0ddf1b9d48041694a72360a7492436513914c85d985faa0744d20`,
11,316 bytes, compiled from the public recipe at Compiler revision
`01326a28d9694742de7555724bdad424def51d3e`.

The scientific source remains `pH34r-pH/experiment-compiler` at
`a4bebe3fff748268f037e5fa622b51ad40322a91`. Implementation, reproduction wrapper,
tests, data, configuration, acceptance, expected result, scientific protocol,
environment declaration, CWL, job order, README and data license are byte-identical
to that frozen parent. The RO-Crate root retains its experiment identifier and
links the exact parent package using PROV-O `wasRevisionOf`.

The new recipe/package identity records the backend change. Only
`experiment/runner.json` changes executable configuration, from `cwltool-docker`
to the already supported `cwltool-podman`. The unchanged CWL `DockerRequirement`
still selects the same OCI image. Metadata records the new operational plan,
this handoff and parent bytes. There is no actual execution action, Process Run
Crate claim, terminal receipt or new result in this plan.

## Admission before launch

Use the owning source's existing reviewed plan/archive/request path. Independently
pin this recipe's package digest, source/package-storage revisions, compiler
revision, selected worker qualification and a new attempt identity. Compile and
verify do not grant any of those authorities. A receipt/index update must not
launch the package again.

The selected boundary must separately qualify Podman, cwltool
`3.2.20260720092025`, the exact image already declared in the CWL, credential and
network isolation, CPU/RAM/storage enforcement, foreground exclusion, cancellation,
worker-loss recovery and durable evidence retention. A qualified different image
or a successful public CI fixture does not establish this qualification.

The unchanged tool envelope is one CPU, 64 MiB maximum RAM, 4 MiB requested
temporary storage, 1 MiB requested output storage and 120 seconds. CWL storage
requests are not enforcement. The owner must select and attest a compatible
bounded worker envelope and enforce its actual storage limits. Unknown required
capabilities, unavailable image bytes or missing retention acknowledgements keep
execution blocked. No new workflow, service, registry or native executor is needed.

## Required retained outcome

Before launching, retain the controller-owned started evidence and request binding.
After completion, retain the new immutable attempt ZIP and complete `.source/`
closure, private `.attempt/` receipt and container-journal snapshots, bounded logs,
exact output identities, and the owning archive's readback acknowledgement.

The existing `reproduce.py` runs its tests and produces a source-owned exact
comparison against the frozen expected result. Preserve `result.json` and
`reproduction-receipt.json` from this new attempt. Compare the retained result
bytes with the packaged expected-result bytes independently; process exit alone
does not establish agreement. A mismatch or failure is an inspectable outcome,
not permission to change the target or retry without a new authorized attempt.

Use the existing verify, crate-validation, finalization and catalog operations on
the retained attempt. Interpretation and disclosure review must refer to that
exact attempt. A valid failed or negative attempt may be finalized, but missing
terminal/evaluation evidence cannot establish successful reuse. Public promotion
requires a separately reviewed shareable closure. Raw operational evidence remains
with its qualified owner.

The #87 acceptance remains open until the same qualified physical boundary has
actually executed this fixture and its exact evidence has completed the existing
retention, verification and presentation path. This handoff is preparation only.
