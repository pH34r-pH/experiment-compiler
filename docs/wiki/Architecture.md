# Architecture

Experiment Compiler's canonical architecture diagrams live in the repository documentation:

- [Project map, immutable lifecycle, package anatomy, trust boundaries, and public research ↔ reproduction surface](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/architecture.md)

The diagrams are maintained as Mermaid source under:

- [`docs/diagrams/`](https://github.com/pH34r-pH/experiment-compiler/tree/main/docs/diagrams)

The core architectural boundaries are:

- source-authored research semantics remain outside the compiler;
- compile/verify never execute packaged code;
- execution crosses an explicit digest-pinned runner boundary;
- plans, attempts, revisions, and finalized artifacts are immutable lineage objects;
- process success, scientific interpretation, publication, and independent reproduction remain distinct;
- the public catalog is derived from authoritative artifacts rather than a second registry;
- Portfolio/MyST owns explanation and browser interaction, while Experiment Compiler owns exact reproduction identity, closure, provenance, and execution/verification handoff.
