#!/usr/bin/env python3
"""Invoke the reusable publication verifier after clean build and OIDC signing."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_clean_rebuilds import publication_candidates

from experiment_compiler.core import PackageError, json_value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    for _, package, digest in publication_candidates(args.site):
        clean = json_value((args.output / (digest + ".clean-rebuild.json")).read_bytes())
        if (clean["builderSourceCommit"], clean["packageSha256"], clean["status"]) != (args.source_commit, digest, "verified"):
            raise PackageError("Signed promotion requires an exact successful clean rebuild")
        parameters = {"package": str(package), "expected-sha256": digest, "repository": repository,
            "source-commit": args.source_commit, "signer-workflow": repository + "/.github/workflows/lifecycle.yml",
            "output": str(args.output / (digest + ".attestation.json"))}
        subprocess.run([sys.executable, str(ROOT / "scripts/lifecycle_component.py"), "publication"],
                       env={**os.environ, "COMPONENT_PARAMETERS": json.dumps(parameters)}, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
