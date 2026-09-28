"""Shared deterministic source-closure assembly for lifecycle artifacts."""
from __future__ import annotations

import shutil
from pathlib import Path

from .core import PackageError, canonical, compile_package, safe_path, sha256, write_once


def compile_source_closure(temporary: Path, output: Path, source_directory: Path,
                           files: dict[str, bytes], recipe: dict,
                           package_name: str) -> dict:
    """Compile files plus a source-owned recipe, then expose both atomically."""
    stage = temporary / "source"
    stage.mkdir()
    members = []
    for package_path, data in sorted(files.items()):
        target = stage.joinpath(*safe_path(package_path).split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        members.append({
            "path": package_path,
            "source": package_path,
            "sha256": sha256(data),
            "size": len(data),
        })

    compiled_recipe = {
        key: value for key, value in recipe.items()
        if key not in {"members", "expectedPackage"}
    }
    compiled_recipe["members"] = members
    recipe_path = stage / "experiment.json"
    recipe_path.write_bytes(canonical(compiled_recipe))

    temporary_package = temporary / package_name
    result = compile_package(recipe_path, temporary_package)
    compiled_recipe["expectedPackage"] = {
        "sha256": result["packageSha256"],
        "size": result["packageSizeBytes"],
    }
    recipe_path.write_bytes(canonical(compiled_recipe))

    shutil.copytree(stage, source_directory)
    try:
        write_once(output, temporary_package.read_bytes())
    except (OSError, PackageError):
        shutil.rmtree(source_directory, ignore_errors=True)
        raise
    return result
