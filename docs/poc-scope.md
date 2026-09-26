# What this POC proves

A public checkout can rebuild the first published Compiled Experiment byte for byte. This removes the original compiler's dependence on a private Git checkout for fetching its frozen package members.

The ZIP includes eight files under `experiment/`:

| File | Purpose |
| --- | --- |
| `VALIDATION.md` | Historical instructions and canonical receipt conditions. |
| `adamw_contract_v1.md` | Frozen optimizer parameters, update ordering, schedule and exclusions. |
| `adamw_semantic_audit.md` | Audit of optimizer semantics against existing standards. |
| `croissant-task-problem.jsonld` | Task/problem encoding attempt and external references. |
| `croissant-task-solution.jsonld` | Historical solution and output references. |
| `gap_register.md` | Explicit unresolved semantics and standards limitations. |
| `ro-crate-metadata.json` | Historical source, protocol, run and evidence references. |
| `run_adamw_contract_receipt.py` | Historical runner that expects tests from the original checkout. |

`experiment-package-manifest.json` is generated at the ZIP root and binds those member bytes plus historical evidence identities. The example recipe and reference manifest stay outside the ZIP to preserve its published digest.

## Not included or not established

The complete training implementation, test module invoked by the runner, dataset bytes, full environment closure and historical scientific result bytes are not bundled. Some references resolve only with original repository access. A receipt hash is not the receipt's content. Packaging verification neither re-executes training nor proves the scientific claim, and it does not certify the JSON-LD documents against external standards validators.

Independent scientific replay requires a separate reviewed public input closure, defined external prerequisites or embedded data, a pinned environment, executable protocol and independent result comparison. Those changes should produce a new artifact, with provenance connecting it to this POC. They must not alter the historical compatibility fixture.

Do not present compiler-build memory usage as the experiment's peak RAM/VRAM requirement. Resource estimates for scientific reproduction require their own measurement basis, hardware context and uncertainty.
