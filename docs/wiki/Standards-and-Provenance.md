# Standards and provenance

Experiment Compiler reuses existing standards where they fit instead of inventing a new research ontology.

## Croissant

Dataset metadata uses MLCommons Croissant where appropriate, including explicit dataset identity, files, fields, and licensing.

## RO-Crate

RO-Crate describes the package as a research object and links its constituent entities.

## Process Run Crate

Process Run Crate describes actual executions and their relationships. A planned package does not claim process-run conformance until a real execution record exists.

## Schema.org and PROV-O

Schema.org lifecycle/action fields describe prospective versus completed/failed actions. PROV-O links revisions, derivations, attempts, and related artifacts.

## Validation boundary

CI runs the pinned Croissant validator for Croissant metadata. For current RO-Crate / Process Run Crate profile combinations not fully supported by an external validator, the repository implements a narrow MUST-level contract check and labels it accordingly rather than presenting it as standards certification.

This distinction is deliberate: “valid JSON,” “matches our package contract,” “conforms to an external standard,” “reproduces,” and “is scientifically correct” are separate claims.
