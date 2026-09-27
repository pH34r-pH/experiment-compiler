"""Create a temporary source-owned revision of the linear-regression CI fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--id", required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"output directory already exists: {args.output_dir}")
    shutil.copytree(args.source, args.output_dir)
    recipe_path = args.output_dir / "experiment.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    recipe["id"] = args.id
    recipe["title"] = "Prospective linear-regression pipeline iteration fixture"
    recipe.pop("expectedPackage", None)
    protocol_path = args.output_dir / "source/protocol.md"
    protocol_path.write_text(
        protocol_path.read_text(encoding="utf-8") +
        "\n## Iteration fixture update\n"
        "The protocol records a source-owned revision after the previous attempt.\n",
        encoding="utf-8",
    )
    protocol = protocol_path.read_bytes()
    for member in recipe["members"]:
        if member["source"] == "source/protocol.md":
            member["sha256"] = hashlib.sha256(protocol).hexdigest()
            member["size"] = len(protocol)
    recipe_path.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
