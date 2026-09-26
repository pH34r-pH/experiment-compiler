# Build recipe and verification contract

The current recipe is local packaging configuration for `poc-v1`, not an ontology or a substitute for Croissant or RO-Crate. Use the checked-in example as the complete executable reference.

Required keys are `buildRecipeVersion` (integer 1), `id` (portable lowercase slug), `title`, `profile` (`poc-v1`), `manifest`, and nonempty `members`. Each member declares a relative `source` under the recipe directory, archive `path`, SHA-256 `sha256`, and byte `size`. No globs, shell commands, remote fetches or environment expansion are supported. Optional `expectedPackage` pins the complete ZIP hash and size for a compatibility fixture.

The manifest header uses the historical envelope: `schemaVersion`, `packageType`, `status`, `source`, `standards`, and `evidence`. Source repository/commit and evidence values are declared input, not independently authenticated by this tool. Scientific semantics remain in the supplied documents. The compiler derives the complete `members` inventory; callers cannot inject a second generated manifest.

Compilation checks hashes before packaging, preserves raw input bytes, sorts archive paths, emits sorted/indented UTF-8 JSON with a trailing newline, sets every ZIP timestamp to 1980-01-01, uses Unix regular-file mode 0644 and DEFLATE level 9, and omits host paths/times from the package. An existing output is reused only when identical; different output bytes are never silently overwritten. Compression implementations can change output bytes across toolchains, so the POC's historical checksum remains a required gate. The build receipt records Python, zlib and compiler versions separately from the immutable ZIP.

Verification checks the exact member inventory, digests, sizes and JSON syntax. It rejects duplicate keys, duplicate/case-colliding/unsafe paths, symlinks, encrypted ZIPs, special files, missing/unlisted members and bounded-size violations. Limits are 1,024 ZIP entries, 16 MiB per member and 64 MiB total archive/uncompressed content. The verifier never extracts files or runs packaged code. Local source directories must not be concurrently modified by an adversary (this is not an operating-system sandbox).

`verify --recipe ...` also compares the manifest with the reviewed recipe. `verify --expected-sha256 ...` checks an external complete-package checksum. Without either external pin, success means self-consistency only: an attacker could change both content and its hashes. Hashes do not establish authorship or scientific validity. Receipts always state `scope: package-integrity-only` and `scientificReproduction: not-run`.

This release preserves the initial POC wire format. A future complete research package must use an explicitly versioned profile and a new digest; never silently “repair” historical member bytes or relabel a new scientific run as historical evidence.
