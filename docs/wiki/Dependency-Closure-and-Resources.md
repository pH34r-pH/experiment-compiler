# Dependency closure and resources

Reproducibility requires knowing both **which bytes/capabilities are required** and **what resources execution needs**.

## Closure classes

Required runtime items are classified as:

1. embedded bytes in the immutable package;
2. immutable public bytes identified by exact version/revision, digest, size, access/license terms, and retrieval method;
3. explicit host/ABI prerequisites;
4. unavailable requirements that block execution-ready claims.

A URL, image tag, LFS pointer, or checkpoint digest identifies something; it does not make the bytes available.

## Resource requests

Executable CPU/RAM/storage/time requirements use CWL v1.2 fields when they are enforceable. Unknown required quantities stay unknown and block admission rather than becoming zero/default by accident.

CWL core has no portable GPU/VRAM requirement, so accelerator-dependent packages need a named supported runner/profile instead of pretending portability.

## Estimates versus measurements

Observed peaks, planning estimates, and execution limits are different evidence classes. Resource records should state operation, basis, environment, units, and evidence location.

The tiny reference example's measured memory use is evidence for that example only; it is not a default estimate for unrelated ML training.

See [docs/resources-and-closure.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/resources-and-closure.md).
