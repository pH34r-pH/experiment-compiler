#!/usr/bin/env python3
"""Build the static Experiment Compiler site from authoritative artifacts."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiment_compiler.catalog import describe_catalog, discover_recipes
from experiment_compiler.core import canonical, compile_package


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

    recipes_root = ROOT / "examples"
    catalog = describe_catalog(recipes_root)
    package_dir = destination / "packages"
    package_dir.mkdir(parents=True)
    for recipe_path, descriptor in zip(discover_recipes(recipes_root), catalog["experiments"], strict=True):
        package_path = package_dir / f"{descriptor['package']['sha256']}.zip"
        compile_package(recipe_path, package_path)

    data = {
        "schemaVersion": 1,
        "project": {
            "name": "Experiment Compiler",
            "repository": "https://github.com/pH34r-pH/experiment-compiler",
        },
        "experiments": catalog["experiments"],
    }
    data_dir = destination / "data"
    data_dir.mkdir()
    (data_dir / "experiments.json").write_bytes(canonical(data))
    (destination / ".nojekyll").write_text("")
    print(json.dumps({"experimentCount": len(catalog["experiments"]), "site": str(destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
