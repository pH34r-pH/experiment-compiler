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
      v
public review / catalog
```

Private bytes do not become public simply because the same tool built both artifacts.

## Catalog

Public catalog discovery derives entries from existing authoritative package artifacts/recipes by convention. A separate hand-maintained catalog is avoided where metadata can be projected from those sources.

This is the same anti-drift principle used throughout the project: compile and derive views from source artifacts instead of creating another status file to remember to update.
