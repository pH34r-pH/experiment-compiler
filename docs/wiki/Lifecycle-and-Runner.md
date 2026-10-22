# Lifecycle and runner

Compile/verify and execution are intentionally separate.

## Plans and attempts

A prospective lifecycle package can describe a planned action and its protocol without claiming an execution occurred. An attempt adds an actual execution action and immutable evidence.

Process status records whether execution completed or failed. It is not a scientific interpretation.

## Runner handoff

`experiment-runner run` is an explicit opt-in boundary. Before invoking packaged CWL, it verifies the reviewed plan digest/profile, requires a bounded worker and explicit no-network declaration, checks pinned runner/tooling configuration, and ensures declared CPU/RAM/storage/time requirements fit the worker.

The current adapter is intentionally narrow. Unsupported workflow/reference shapes fail admission rather than expanding the sandbox implicitly.

The CWL orchestrator receives a fixed client environment with an attempt-owned
`HOME`, fixed `PATH` and attempt temporary directory. It preserves only nonempty
caller `XDG_RUNTIME_DIR`, `XDG_CONFIG_HOME` and `XDG_DATA_HOME` values so Podman
uses the runtime/configuration/cache context selected by the trusted caller.
Existing interpreter library handling also retains nonempty `LD_LIBRARY_PATH`.
Credentials and remote-engine selectors remain excluded.
These variables configure the host engine client; no CWL environment-preservation
flag forwards them into task containers. The synthetic `HOME` remains: when an
XDG value is absent, engine defaults can differ from the caller's home-based defaults.
The adapter does not infer or synthesize those directories. Mocked handoff tests
establish this allowlist, not physical container or workflow qualification.

## Result artifact

Each run produces a separate immutable result package and source closure containing the exact reviewed members needed to rebuild/verify the package. Failed processes still produce bounded failure evidence.

## Revision and finalization

A revision creates a new prospective plan linked to a selected prior attempt. Finalization attaches source-authored interpretation and review to an exact selected attempt. Neither command invents a conclusion.

Merging a reviewed public package is the publication event; process status alone is not.

See the lifecycle section in [docs/build-recipe.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/build-recipe.md).

## Operator interruption and physical boundaries

A KeyboardInterrupt during the launched CWL process wait requests operator
cancellation. The adapter sends TERM then KILL to the orchestrator process group,
with bounded waits. A repeated interruption during the TERM wait does not skip
the KILL attempt. Cleanup failures remain explicit receipt diagnostics and do not
replace the original operator interruption or establish successful termination.
The adapter retains bounded controller logs and a failed receipt carrying
an explicit cancellation diagnostic, and re-raises the interrupt. Even after the
leader exits, its remaining process group receives KILL. This establishes neither
scientific completion nor a scientific negative result. An interruption before
launch similarly retains a failed invocation; historical started-only receipts
remain unchanged.

These hermetic tests do not establish SIGTERM handling, hard worker-loss recovery,
container-runtime cleanup, network isolation, resource isolation or concurrency
qualification. Fleet owns those physical boundaries. The temporary-directory
check establishes containment within the declared root and a backing-capacity
floor only. It does not prove a tmpfs mount or enforce a storage quota. CWL
`tmpdirMin` is a requested minimum; the separately declared worker `tmpdirMiB`
requires physical enforcement by the worker. When the optional
`EXPERIMENT_RUNNER_OUTPUT_TMPFS_ROOT` is set, the adapter verifies a dedicated
tmpfs mount sized exactly to `outdirMiB`; cwltool's output staging and final
output directory share that mount. The controller records a persistent CID
journal beside the attempt receipt and retains containers until exact-ID cleanup
is verified. `experiment-runner recover <attempt-dir>` rescans protected CID
files and reconciles a matching incomplete attempt without re-running its
workflow; stale or unverifiable identities fail closed. The private attempt
receipt binds the complete sidecar journal by digest. The result ZIP exposes only
that digest, a relative snapshot path under the sibling `.attempt` private store,
`private-store` availability, and a sanitized cleanup/absence summary; it does
not include the host-specific journal. The filesystem and
container journal improve controller evidence, but do not by themselves prove
that task containers cannot access the host or enforce other isolation. A valid
different kernel boot ID proves that the old cwltool process group cannot still
exist, so recovery does not inspect or signal its potentially reused numeric
PID and continues only with exact journaled container IDs. A distro or PID
namespace restart that retains the same kernel boot ID remains conservative:
ambiguous PID or process-group identity fails closed. Fixture success does not
satisfy Fleet #678 or authorize its gated real execution.
