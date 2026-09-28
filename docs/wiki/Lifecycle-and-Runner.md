# Lifecycle and runner

Compile/verify and execution are intentionally separate.

## Plans and attempts

A prospective lifecycle package can describe a planned action and its protocol without claiming an execution occurred. An attempt adds an actual execution action and immutable evidence.

Process status records whether execution completed or failed. It is not a scientific interpretation.

## Runner handoff

`experiment-runner run` is an explicit opt-in boundary. Before invoking packaged CWL, it verifies the reviewed plan digest/profile, requires a bounded worker and explicit no-network declaration, checks pinned runner/tooling configuration, and ensures declared CPU/RAM/storage/time requirements fit the worker.

The current adapter is intentionally narrow. Unsupported workflow/reference shapes fail admission rather than expanding the sandbox implicitly.

## Result artifact

Each run produces a separate immutable result package and source closure containing the exact reviewed members needed to rebuild/verify the package. Failed processes still produce bounded failure evidence.

## Revision and finalization

A revision creates a new prospective plan linked to a selected prior attempt. Finalization attaches source-authored interpretation and review to an exact selected attempt. Neither command invents a conclusion.

Merging a reviewed public package is the publication event; process status alone is not.

See the lifecycle section in [docs/build-recipe.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/build-recipe.md).
