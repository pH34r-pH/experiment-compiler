# Private lab to publication

The compiler is public, while active research may remain private.

## Private workspace

A private lab owns private protocols, inputs, runs, unfinished results, credentials, and operational state. It can invoke an exact pinned public compiler revision inside its own trust boundary.

The public compiler does not need private-repository credentials and should not fetch private source.

## Promotion

Public promotion is a deliberate new artifact built from a reviewed shareable closure:

```text
private source/result
      |
      | review + disclosure decision
      v
shareable source closure
      |
      v
public compiler recipe
      |
      v
immutable public package
      |
      +--> live derived reproduction page at experiments.tyharbin.com
      |
      v
source-owner disclosure / finalization review
      |
      v
exact-byte archival copy in pH34r-pH/compiled-experiments
      |
      v
human-reviewed GitHub Release
      |
      v
Zenodo archival record / DOI
```

Private bytes do not become public simply because the same tool built both artifacts.

## Catalog

Public catalog discovery derives entries from existing authoritative package artifacts/recipes by convention. A separate hand-maintained catalog is avoided where metadata can be projected from those sources.

This is the same anti-drift principle used throughout the project: compile and derive views from source artifacts instead of creating another status file to remember to update.

## Archive boundary

[pH34r-pH/compiled-experiments](https://github.com/pH34r-pH/compiled-experiments) is a downstream archival release surface, not another research workspace or compiler. It accepts only exact finalized public bytes after the source owner approves disclosure, verifies their digests/receipts/provenance, and preserves them under an immutable release identity. It must not rebuild or execute the artifact.

The reviewed GitHub Release is the explicit human handoff to Zenodo. DOI creation remains outside Experiment Compiler. After Zenodo archives a release, its exact DOI/record identity can be projected back into source/manuscript/article/experiment metadata without creating another status registry.

Cross-repository integration: [compiled-experiments#1](https://github.com/pH34r-pH/compiled-experiments/issues/1).
