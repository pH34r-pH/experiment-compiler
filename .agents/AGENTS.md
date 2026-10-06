# Agent skill package

`.agents/skills/` is the canonical source for the repository's portable Experiment Compiler workflows.

- `experiment-onboarding` gathers source-owned scientific and technical inputs and prepares a recipe only from supplied facts.
- `experiment-doctor` runs read-only diagnostics by default; its optional compiler check writes only to a temporary directory and invokes the existing compile/verify CLI.
- `experiment-repair` supports only a reviewable same-byte source-path correction. Its default is a diff on stdout; a requested output recipe is a new file and never overwrites the original.

Keep each mode focused, keep client-specific claims in `docs/agent-onboarding.md`, and retain scripts/templates inside their skill folder. Do not add network, execution, dependency installation, API, or permission-changing behavior to these helpers.
