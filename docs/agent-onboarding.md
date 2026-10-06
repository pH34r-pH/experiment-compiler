# Agent-first onboarding

Use the packaged skills to prepare a research package with a local coding agent that can read this repository and run a local Python command. The skills are reusable instructions and a small deterministic preflight/repair helper; they are not a hosted agent service, execution runner, or subscription bridge.

## A first clean install and compile

Python 3.11 or newer is required. The compiler core has no runtime dependencies; package installation uses the project's Python build backend, which standard `pip` may need to fetch on a new machine.

```sh
git clone https://github.com/pH34r-pH/experiment-compiler.git
cd experiment-compiler
python -m venv .venv
```

Install the local checkout:

```sh
# macOS/Linux
.venv/bin/python -m pip install .

# Windows PowerShell
.venv\Scripts\python.exe -m pip install .
```

Compile and verify the self-contained public example. This package is a small synthetic tutorial, not the researcher's own study.

```sh
# macOS/Linux
.venv/bin/python -m experiment_compiler compile examples/linear-regression-v1/experiment.json
.venv/bin/python -m experiment_compiler verify \
  dist/stdlib-linear-regression-v1-compiled-experiment.zip \
  --recipe examples/linear-regression-v1/experiment.json

# Windows PowerShell: use .venv\Scripts\python.exe in place of .venv/bin/python
```

The existing lifecycle workflow also installs the built package into a fresh virtual environment, then compiles/verifies both the frozen POC and this example. Compile and verify do not execute package members or install experiment dependencies.

## Prepare your own experiment

Open the repository in a local agent client and invoke `experiment-onboarding`. It starts from [the intake template](../.agents/skills/experiment-onboarding/assets/experiment-intake.md). Put answers in the study owner's words. Unknown scientific choices remain unresolved; the skill does not invent hypotheses, methods, observations, or results.

Example request:

> Use experiment-onboarding. Help me prepare a recipe for my study from `study-intake.md` and `src/`. Ask me about missing scientific decisions. Do not execute the experiment. Show the recipe and file list for review before saving.

The mode gathers research question and source-owned hypothesis, outcomes/controls/analysis, exact input identities and permissions, implementation/tests, environment/dependency closure, runtime/resource evidence, intended claims, and review ownership. It prepares a new recipe and source tree from those answers, computes member hashes from exact bytes, preserves unknowns, then waits for review. A compile/verify result says nothing by itself about scientific correctness or reproduction.

Use the other modes by explicit request:

- `experiment-doctor` for read-only environment/recipe/closure/runtime/resource diagnosis. The default preflight checks source presence and reports packaged evidence. Add `--compiler-check` only when you want a temporary compile/verify check; it can read all declared files and use local disk/CPU proportional to their size. It removes the temporary package and never executes package members.
- `experiment-repair` to propose a narrowly scoped repair. The bundled helper supports only a source-path correction to a different file whose bytes exactly match the recipe's existing digest and size. It prints a diff and writes nothing by default. After review and explicit approval of the exact change and destination, it can write a new recipe file; it refuses to overwrite the original or an existing destination. It does not update any digest or result.

Any repair involving changed bytes, science, analysis, resource claims, access, or publication stops for source-owner review and a new identity. Execution, secrets, dependency installation, paid compute, publication, and permission changes require separate, explicit authorization.

## Client support and installation

The canonical package is in `.agents/skills/`; each directory contains a standard `SKILL.md`, and the onboarding skill bundles the intake template. Client-specific metadata is optional and kept in `agents/openai.yaml`. The common skill format supports shared instructions, but discovery paths and invocation differ by client.

| Client verified in provider documentation | Discovery path and use | Boundary |
| --- | --- | --- |
| OpenAI Codex CLI/IDE | Repository `.agents/skills/`; use `/skills` to inspect or `$experiment-onboarding`, `$experiment-doctor`, `$experiment-repair` to invoke. | Skills can be selected from their descriptions, but use explicit invocation for the repair mode. Codex does not get permission to run commands merely because a skill exists. |
| GitHub Copilot CLI and documented Copilot agent surfaces | Repository `.agents/skills/`; list with `/skills list`, then invoke `/experiment-onboarding`, `/experiment-doctor`, or `/experiment-repair`. | Availability still depends on the Copilot surface, plan/policy, and repository access. A skill guides the agent; it does not provide tools or execute a workflow itself. |
| Claude Code local project session | Copy the canonical skill folders into `.claude/skills/` before starting Claude Code; invoke `/experiment-onboarding`, `/experiment-doctor`, or `/experiment-repair`. | Claude Code discovers project skills from `.claude/skills/`, not this repository's `.agents/skills/` path. Re-copy after updating this repository. Cloud sessions need skills committed to the user's repo or enabled through that account's skill settings. |

For Claude Code on macOS/Linux, run from this repository:

```sh
mkdir -p .claude/skills
cp -R .agents/skills/experiment-onboarding .agents/skills/experiment-doctor \
  .agents/skills/experiment-repair .claude/skills/
```

For PowerShell:

```powershell
New-Item -ItemType Directory -Force .claude\skills | Out-Null
Copy-Item -Recurse .agents\skills\experiment-onboarding,.agents\skills\experiment-doctor,.agents\skills\experiment-repair .claude\skills\
```

To use the skills in a separate study repository, copy the relevant whole skill directories there or into the user's supported skill directory. Install the compiler package in that project's Python environment as a separate step. These instructions cover local coding-agent clients verified above; a plain browser chat session does not automatically gain access to local repository files, shell tools, or the user's subscription entitlements.

## Packaging research and boundaries

Provider docs agree on folder-based skills containing a `SKILL.md` with metadata/instructions and optional scripts or resources. Codex and Copilot CLI discover repository skills under `.agents/skills`; Claude Code documents `.claude/skills` as its project path. That difference makes `.agents/skills` the useful canonical source for Codex/Copilot and requires an explicit Claude copy step. This is a packaging inference from their current documented discovery paths; it is not a claim that one installation activates in every client.

We use three small skills instead of provider-specific subagent definitions or a new plugin/service: the work is instruction-guided and needs no new external tool capability. Client-side skill selection may be automatic when a description matches, but the installation cannot select the same mode everywhere, and no model name or subscription tier is forced. Use an explicit mode name when a predictable workflow matters.

Primary provider references (checked 2026-10-06): [Codex: build skills](https://developers.openai.com/codex/build-skills), [Claude Code: skills](https://code.claude.com/docs/en/skills), [GitHub Copilot: agent skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills), and [Copilot CLI customization comparison](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/comparing-cli-features).

## Local deterministic preflight

From the compiler repository root:

```sh
python .agents/skills/experiment-doctor/scripts/preflight.py examples/linear-regression-v1/experiment.json
python .agents/skills/experiment-doctor/scripts/preflight.py examples/linear-regression-v1/experiment.json --compiler-check
```

The first command reports Python version, recipe/profile, declared member presence, dependency classifications, runtime contract, and resource evidence without changing project files. The second runs the existing compiler/verifier in a temporary directory. Neither command executes experiment members, installs dependencies, accesses secrets, or contacts a service. Resource data are reported as authored, including unknowns and measurement bases; preflight does not certify runtime sufficiency.

The same-byte repair proposal can be inspected with:

```sh
python .agents/skills/experiment-repair/scripts/propose_source_path_fix.py \
  path/to/experiment.json --member experiment/protocol.md --to source/protocol.md
```

It is only a proposal until you explicitly provide `--output path/to/new-recipe.json` after approving the exact change.
