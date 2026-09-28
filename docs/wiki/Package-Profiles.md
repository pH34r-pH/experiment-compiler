# Package profiles

Experiment Compiler uses explicit versioned profiles so historical compatibility and newer reproducibility semantics do not silently overwrite one another.

## `poc-v1`

Preserves the historical #164 package envelope and expected complete-package digest. It is valuable as a compatibility fixture and provenance artifact.

It is **not** a complete scientific reproduction: some method, environment, tests, data, or evidence remain references outside the package.

## `compiled-experiment-v1`

Represents a self-contained reproducibility package with an explicit dependency closure. The reference linear-regression example embeds the implementation, tests, data, license, environment/configuration, protocol, acceptance criteria, reference result, resource evidence, Croissant metadata, and RO-Crate/Process Run Crate provenance.

## `compiled-experiment-lifecycle-v1`

Represents plans, attempts, revisions, results, and source-authored interpretation without inventing a second scientific-status vocabulary. Schema.org action/lifecycle fields and PROV-O relationships describe process state; process success is not automatically a scientific conclusion.

## Compatibility rule

A new profile or protocol revision gets a new versioned identity and package digest. Historical package bytes are not “repaired” in place.

See [docs/build-recipe.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/build-recipe.md).
