# Experiment Compiler

Experiment Compiler turns reviewed research inputs into a **portable, inspectable, integrity-verifiable experiment artifact**.

It is intentionally a compiler/packager rather than a scientific oracle: it does not invent a method, infer missing semantics, decide whether a result is scientifically valid, or require a hosted service.

## The mental model

```text
reviewed source material
        |
        v
versioned build recipe
        |
        v
compile + deterministic package
        |
        +--> manifest / standards metadata
        +--> dependency & resource closure
        +--> provenance / receipts
        |
        v
verify package integrity
        |
        +--> optional bounded execution handoff
        |
        v
reviewed result / publication artifact
```

## Start here

- [Quickstart](Quickstart.md)
- [Package profiles](Package-Profiles.md)
- [Build and verification](Build-and-Verification.md)
- [Lifecycle and runner](Lifecycle-and-Runner.md)
- [Dependency closure and resources](Dependency-Closure-and-Resources.md)
- [Standards and provenance](Standards-and-Provenance.md)
- [Private lab to publication](Private-Lab-to-Publication.md)
- [Limits and trust boundaries](Limits-and-Trust-Boundaries.md)
- [Development](Development.md)

The public compiler owns packaging mechanics and portable contracts. Scientific meaning stays in source-authored protocols, methods, evidence, and interpretation.
