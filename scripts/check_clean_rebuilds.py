#!/usr/bin/env python3
"""Rebuild exact catalog candidates from a separately archived Git source tree."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiment_compiler.catalog import discover_recipes
from experiment_compiler.clean_rebuild import clean_rebuild
from experiment_compiler.core import (
    PackageError,
    canonical,
    json_value,
    load_recipe,
    write_once,
)


def publication_candidates(site):
    catalog = json_value((site / "data/experiments.json").read_bytes())
    recipes = {load_recipe(path)["id"]: path for path in discover_recipes(ROOT / "examples")}
    records = catalog["experiments"]
    if {record["id"] for record in records} != set(recipes):
        raise PackageError("Publication catalog differs from the exact source recipe inventory")
    candidates = []
    for record in records:
        digest = record["package"]["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise PackageError("Publication package requires an exact SHA-256 identity")
        candidates.append((recipes[record["id"]], site / "packages" / (digest + ".zip"), digest))
    if set((site / "packages").iterdir()) != {item[1] for item in candidates}:
        raise PackageError("Unlisted publication package is not admitted")
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for recipe, package, digest in publication_candidates(args.site):
        output = args.output / (digest + ".clean-rebuild.json")
        try:
            receipt = clean_rebuild(ROOT, args.source_commit, recipe.relative_to(ROOT).as_posix(), package, digest)
        except (PackageError, OSError, subprocess.SubprocessError) as exc:
            write_once(output, canonical({"schemaVersion": 1, "status": "failed",
                "scope": "exact-source-clean-rebuild", "builderSourceCommit": args.source_commit,
                "recipe": recipe.relative_to(ROOT).as_posix(), "packageSha256": digest,
                "failureClass": type(exc).__name__, "publicationAuthorized": False}))
            raise
        write_once(output, canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
