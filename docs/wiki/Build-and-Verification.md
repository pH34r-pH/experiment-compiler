# Build and verification

A build recipe is local packaging configuration. It chooses exact source members and records their expected hashes, sizes, and archive paths.

## Deterministic compilation

Compilation:

- validates selected source bytes before packaging;
- preserves raw input bytes;
- sorts archive paths;
- emits canonicalized generated JSON;
- fixes ZIP timestamps and file modes;
- uses deterministic compression settings;
- excludes host paths and wall-clock metadata;
- refuses to silently overwrite a different existing output.

The historical compatibility fixture additionally pins the complete ZIP digest.

## Verification

The verifier checks inventory, member digests/sizes, JSON syntax, path safety, duplicate/case collisions, symlinks/special files, and encryption flags. It hashes package members incrementally and does not impose a Compiled Experiment byte ceiling.

It never extracts or executes package code.

With `--recipe`, verification also compares the package with the reviewed local recipe. With an externally supplied expected SHA-256, it checks a complete-package pin.

## Trust boundary

A package that is internally self-consistent can still be malicious or scientifically wrong. Hashes establish byte identity, not authorship, correctness, or interpretation.

Receipts therefore describe their scope rather than claiming scientific reproduction.

See [docs/build-recipe.md](https://github.com/pH34r-pH/experiment-compiler/blob/main/docs/build-recipe.md).
