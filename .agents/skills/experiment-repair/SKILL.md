---
name: experiment-repair
description: Propose a narrow, reviewable repair to a reproducibility package issue. Only same-byte source-path corrections are permitted by the bundled helper; preserve existing artifacts and all digest/authorization boundaries.
---

# Experiment repair

Use this mode only after the researcher identifies the affected recipe member and the exact intended replacement path. The default is diagnosis and a proposal; never modify an existing recipe, package, source, receipt, result, or digest in place.

## Permitted helper

The bundled helper can propose a source-path correction only when the replacement file's exact SHA-256 and byte count match the recipe's existing member declaration:

```sh
python .agents/skills/experiment-repair/scripts/propose_source_path_fix.py \
  path/to/experiment.json --member experiment/protocol.md --to source/protocol.md
```

Review the diff and its unchanged digest/size before discussing whether to apply it. If, and only if, the researcher explicitly approves this exact path correction and a new recipe filename, the helper can write a new recipe:

```sh
python .agents/skills/experiment-repair/scripts/propose_source_path_fix.py \
  path/to/experiment.json --member experiment/protocol.md --to source/protocol.md \
  --output path/to/experiment.repaired.json
```

The helper refuses an existing output path. It changes only the selected member's `source` field; it does not change the bytes, digest, size, expected package pin, evidence, result, permissions, or any existing artifact. Run doctor and the existing compile/verify commands on the new recipe after review.

## Refuse broader repairs

If the replacement bytes differ, a digest/size is stale, a source is missing or ambiguous, or the requested fix changes a hypothesis, protocol, data, result, resource claim, permission, secret, or publication state, do not edit or recalculate pins. Explain the mismatch and prepare a proposed new identity only from source-owner-supplied facts and records. Never manufacture a result or use a repair to bypass authorization, access, signature, or digest checks.
