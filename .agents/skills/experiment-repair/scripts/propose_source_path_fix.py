#!/usr/bin/env python3
"""Print a same-byte source path correction; optionally write a new recipe copy."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[4]
if (REPOSITORY / "experiment_compiler").is_dir():
    sys.path.insert(0, str(REPOSITORY))

try:
    from experiment_compiler.core import PackageError, _source, load_recipe, safe_path
except ImportError as error:
    PackageError = Exception  # type: ignore[assignment,misc]
    _source = load_recipe = safe_path = None  # type: ignore[assignment]
    IMPORT_ERROR = str(error)
else:
    IMPORT_ERROR = None


def _identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _write_new_recipe(destination: Path, content: bytes) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {destination}")
    with destination.open("xb") as stream:
        stream.write(content)


def _selected_member(recipe_path: Path, package_path: str) -> tuple[dict, dict]:
    recipe = load_recipe(recipe_path)
    selected = [item for item in recipe["members"] if item["path"] == package_path]
    if len(selected) != 1:
        raise ValueError("the exact package member did not select one recipe row")
    return recipe, selected[0]


def _prepare_change(recipe_path: Path, package_path: str, replacement_source: str) -> tuple[str, str] | None:
    recipe, member = _selected_member(recipe_path, package_path)
    safe_path(replacement_source)
    replacement = _source(recipe_path.parent, replacement_source)
    if not replacement.is_file():
        raise ValueError("replacement source is missing or is not a regular file")
    digest, size = _identity(replacement)
    if digest != member["sha256"] or size != member["size"]:
        raise ValueError("replacement bytes differ from the declared SHA-256 or size; no repair proposed")
    if member["source"] == replacement_source:
        return None

    original_text = recipe_path.read_text(encoding="utf-8")
    updated = json.loads(original_text)
    updated_member = next(item for item in updated["members"] if item["path"] == package_path)
    updated_member["source"] = replacement_source
    proposed_text = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    diff = "".join(difflib.unified_diff(
        original_text.splitlines(keepends=True), proposed_text.splitlines(keepends=True),
        fromfile=recipe_path.name, tofile=f"{recipe_path.name} (proposed)",
    ))
    return diff, proposed_text


def _write_approved_copy(recipe_path: Path, output: Path, proposed_text: str) -> None:
    destination = output if output.is_absolute() else recipe_path.parent / output
    destination = destination.absolute()
    if destination.parent.resolve() != recipe_path.parent.resolve():
        raise ValueError("new recipe output must be in the original recipe directory")
    if destination == recipe_path:
        raise ValueError("output must be a new recipe file, not the original")
    _write_new_recipe(destination, proposed_text.encode("utf-8"))
    print(f"Wrote new recipe: {destination.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--member", required=True, help="exact package path from the recipe")
    parser.add_argument("--to", required=True, help="explicit replacement source path relative to recipe")
    parser.add_argument("--output", type=Path,
                        help="approved new recipe filename in the same directory; never overwrite")
    args = parser.parse_args(argv)
    if load_recipe is None:
        print(f"REPAIR REFUSED: Experiment Compiler unavailable: {IMPORT_ERROR}", file=sys.stderr)
        return 2
    recipe_path = args.recipe.resolve()
    try:
        change = _prepare_change(recipe_path, args.member, args.to)
        if change is None:
            print("No repair needed: the recipe already names this source path.")
            return 0
        diff, proposed_text = change
        print(f"Proposed same-byte source mapping repair for {args.member}:")
        print(diff, end="")
        if args.output is not None:
            _write_approved_copy(recipe_path, args.output, proposed_text)
    except (PackageError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"REPAIR REFUSED: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
