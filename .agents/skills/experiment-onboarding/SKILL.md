---
name: experiment-onboarding
description: Help a researcher prepare their first Experiment Compiler recipe from their own reviewed scientific and technical inputs. Use for study intake, source closure, recipe preparation, or first compile/verify; never invent a hypothesis, protocol, result, or missing evidence.
---

# Experiment onboarding

Help the researcher prepare a reproducible package from facts they provide. The researcher owns scientific choices and must review the recipe before compilation.

## Start with the intake template

Read `assets/experiment-intake.md` and ask the researcher to fill it in or answer its sections. Keep their wording for the research question and hypothesis. If a field is unknown, write `unknown` or `not yet decided`, preserve it as unresolved, and explain whether it blocks a package, execution, or a scientific claim. Do not complete a missing field by guessing.

Collect, at minimum:

- the question, source-authored hypothesis (if any), primary outcome, controls, analysis rule, stopping rule, and intended claim;
- exact source/data/model identities, owners, access and redistribution permissions, licenses, and any required public immutable references;
- protocol, split/selection rules, implementation entrypoint, tests, expected-output provenance, and which inputs/results actually exist;
- a declared dependency/runtime closure, network needs, hardware/ABI prerequisites, resource evidence or estimates, and explicit unknowns;
- acceptance checks, package profile, and the researcher who will review the prepared recipe.

Never turn a plan into a completed result, create fake observations, or call a package scientifically reproducible merely because compile/verify succeeds.

## Prepare, then validate

1. Work only in the researcher-selected project directory. Preserve all existing recipes, source bytes, packages, receipts, and result artifacts.
2. Show a short intake summary and list unresolved scientific/technical decisions. Ask the researcher to supply missing scientific decisions; do not suggest a preferred outcome.
3. Prepare a new recipe and source tree using the current [`build-recipe`](../../../docs/build-recipe.md) contract and the selected profile. Compute member size/SHA-256 from the exact supplied bytes. Keep unavailable closure items and unknown resources explicit; do not encode unknown as zero.
4. Present the proposed recipe/files and their intended paths for review. Do not overwrite an existing file. Do not update expected package, evidence, or result digests unless a source-authored record supplies the new exact identity.
5. After the researcher requests validation, use the existing deterministic commands:

   ```sh
   python -m experiment_compiler compile path/to/experiment.json --output path/to/new-check.zip
   python -m experiment_compiler verify path/to/new-check.zip --recipe path/to/experiment.json
   ```

   Choose a new output path; the compiler will not replace a different existing package.

Compile and verify package bytes only. They do not execute members, install dependencies, establish resource sufficiency, or validate the scientific method. Keep their receipts and output separate from scientific results.

## Stop and ask

Stop before consequential execution, secret access, dependency installation, paid compute, publication, or permission changes. Use the project's separately authorized runner/lifecycle boundary only after the owner explicitly authorizes the exact action, artifact digest, and resource envelope. A request to prepare or verify a recipe does not authorize those actions.
