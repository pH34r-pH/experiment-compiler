#!/usr/bin/env python3
"""Build the static Experiment Compiler site from authoritative artifacts."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from experiment_compiler.catalog import describe_recipe
from experiment_compiler.core import canonical, compile_package


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "examples/linear-regression-v1/experiment.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()

    destination = args.output
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copytree(ROOT / "site/assets", destination / "assets")
    shutil.copy2(ROOT / "site/index.html", destination / "index.html")

    descriptor = describe_recipe(RECIPE)
    package_sha = descriptor["package"]["sha256"]
    package_path = destination / "packages" / f"{package_sha}.zip"
    package_path.parent.mkdir(parents=True)
    compile_package(RECIPE, package_path)

    data = {
        "schemaVersion": 1,
        "project": {
            "name": "Experiment Compiler",
            "repository": "https://github.com/pH34r-pH/experiment-compiler",
        },
        "experiments": [descriptor],
    }
    data_dir = destination / "data"
    data_dir.mkdir()
    (data_dir / "experiments.json").write_bytes(canonical(data))
    (destination / ".nojekyll").write_text("")
    print(json.dumps({"packageSha256": package_sha, "site": str(destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
