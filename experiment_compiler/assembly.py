"""Shared deterministic source-closure assembly for lifecycle artifacts."""
from __future__ import annotations

import shutil
from pathlib import Path

from .core import MANIFEST, _publish_file_once, canonical, compile_package, sha256, unique_paths


def compile_source_closure(temporary: Path, output: Path, source_directory: Path,
                           files: dict[str, bytes], recipe: dict,
                           package_name: str) -> dict:
    """Compile a source closure and publish it with a new, exclusive package."""
    unique_paths([MANIFEST, "experiment.json", *files])
    stage = temporary / "source"
    stage.mkdir()
    members = []
    for package_path, data in sorted(files.items()):
        target = stage.joinpath(*package_path.split("/"))
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

    source_directory.mkdir(parents=True)
    try:
        shutil.copytree(stage, source_directory, dirs_exist_ok=True)
        _publish_file_once(temporary_package, output, reuse_identical=False)
    except BaseException:
        shutil.rmtree(source_directory, ignore_errors=True)
        raise
    return result
