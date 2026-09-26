# Build recipe and verification contract

A build recipe is local packaging configuration, not an ontology or a substitute for Croissant or RO-Crate. Two profiles are currently accepted: `poc-v1`, which preserves the historical #164 package envelope byte-for-byte, and `compiled-experiment-v1`, which marks the first self-contained public reproduction example. Use the checked-in examples as the executable references.

Required keys are `buildRecipeVersion` (integer 1), `id` (portable lowercase slug), `title`, `profile` (`poc-v1` or `compiled-experiment-v1`), `manifest`, and nonempty `members`. Each member declares a relative `source` under the recipe directory, archive `path`, SHA-256 `sha256`, and byte `size`. No globs, shell commands, remote fetches or environment expansion are supported. Optional `expectedPackage` pins the complete ZIP hash and size for a compatibility fixture.

The historical `poc-v1` manifest header uses `schemaVersion`, `packageType`, `status`, `source`, `standards`, and `evidence`. `compiled-experiment-v1` adds an explicit `profile` field and requires `packageType: compiled-experiment` plus `status: reproducible`. The compiler validates only this packaging envelope and the byte inventory; scientific meaning stays in the packaged standards/protocol/evidence files. Source repository/commit and evidence values are declared input, not independently authenticated by this tool. Scientific semantics remain in the supplied documents. The compiler derives the complete `members` inventory; callers cannot inject a second generated manifest.

Compilation checks hashes before packaging, preserves raw input bytes, sorts archive paths, emits sorted/indented UTF-8 JSON with a trailing newline, sets every ZIP timestamp to 1980-01-01, uses Unix regular-file mode 0644 and DEFLATE level 9, and omits host paths/times from the package. An existing output is reused only when identical; different output bytes are never silently overwritten. Compression implementations can change output bytes across toolchains, so the POC's historical checksum remains a required gate. The build receipt records Python, zlib and compiler versions separately from the immutable ZIP.

Verification checks the exact member inventory, digests, sizes and JSON syntax. It rejects duplicate keys, duplicate/case-colliding/unsafe paths, symlinks, encrypted ZIPs, special files, missing/unlisted members and bounded-size violations. Limits are 1,024 ZIP entries, 16 MiB per member and 64 MiB total archive/uncompressed content. The verifier never extracts files or runs packaged code. Local source directories must not be concurrently modified by an adversary (this is not an operating-system sandbox).

`verify --recipe ...` also compares the manifest with the reviewed recipe. `verify --expected-sha256 ...` checks an external complete-package checksum. Without either external pin, success means self-consistency only: an attacker could change both content and its hashes. Hashes do not establish authorship or scientific validity. Receipts always state `scope: package-integrity-only` and `scientificReproduction: not-run`.

This release preserves the initial POC wire format. A future complete research package must use an explicitly versioned profile and a new digest; never silently “repair” historical member bytes or relabel a new scientific run as historical evidence.


## Self-contained reproduction profile

`compiled-experiment-v1` does not authorize the compiler to execute package content. The public lifecycle separately exercises the known reviewed example after compilation: it verifies the ZIP, extracts it with Python's standard library, invokes the declared `experiment/reproduce.py` entrypoint, and compares the newly produced result bytes with the embedded reference result.

A self-contained example should make its dependency closure explicit. The first example embeds code, tests, data/license/splits, environment, configuration, protocol, acceptance criteria, reference evidence/receipt, and resource measurements. Open-standard specifications may remain immutable public references. Missing prerequisites belong in an explicit unavailable list rather than being inferred or silently fetched.
