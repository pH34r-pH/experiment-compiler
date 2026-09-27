"""Derive display metadata from authoritative Compiled Experiment artifacts.

This module stores no experiment-specific facts. It projects reviewed recipe
and packaged scientific artifacts into display-friendly JSON so sites and
publishers do not maintain duplicate metadata.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .core import (MAX_FILE, PackageError, _types, bounded_read, json_value, load_recipe,
                   validate_lifecycle_crate)


def _member_source(recipe: dict, recipe_path: Path, package_path: str) -> Path:
    match = next((item for item in recipe["members"] if item["path"] == package_path), None)
    if match is None:
        raise PackageError(f"compiled-experiment-v1 lacks required member: {package_path}")
    return recipe_path.parent / match["source"]


def _json_member(recipe: dict, recipe_path: Path, package_path: str) -> Any:
    value = json_value(bounded_read(_member_source(recipe, recipe_path, package_path), MAX_FILE))
    if not isinstance(value, (dict, list)):
        raise PackageError(f"Expected JSON object/array: {package_path}")
    return value


def _text_member(recipe: dict, recipe_path: Path, package_path: str) -> str:
    try:
        return bounded_read(_member_source(recipe, recipe_path, package_path), MAX_FILE).decode("utf-8")
    except UnicodeError as exc:
        raise PackageError(f"Expected UTF-8 text: {package_path}") from exc


def _markdown_section(text: str, title: str) -> str | None:
    pattern = re.compile(
        rf"^##[ \t]+{re.escape(title)}[ \t]*\n(?P<body>.*?)(?=^##[ \t]+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    body = match.group("body").strip()
    return body or None


def describe_recipe(recipe_path: Path) -> dict:
    """Project a reviewed compiled-experiment-v1 recipe into display metadata."""
    recipe = load_recipe(recipe_path)
    if recipe["profile"] == "compiled-experiment-lifecycle-v1":
        return _describe_lifecycle_recipe(recipe, recipe_path)
    if recipe["profile"] != "compiled-experiment-v1":
        raise PackageError("describe requires a Compiled Experiment profile")
    expected = recipe.get("expectedPackage")
    if expected is None:
        raise PackageError("Publishable compiled-experiment-v1 recipe must pin expectedPackage")

    readme = _text_member(recipe, recipe_path, "experiment/README.md")
    protocol = _text_member(recipe, recipe_path, "experiment/protocol.md")
    environment = _json_member(recipe, recipe_path, "experiment/environment.json")
    acceptance = _json_member(recipe, recipe_path, "experiment/acceptance.json")
    result = _json_member(recipe, recipe_path, "evidence/expected-result.json")
    resources = _json_member(recipe, recipe_path, "evidence/resource-requirements.json")
    closure = _json_member(recipe, recipe_path, "dependency-closure.json")

    entrypoint = next(
        (
            item["path"]
            for item in closure.get("classifications", {}).get("embedded", [])
            if item.get("item") == "one-command reproduction entrypoint"
        ),
        None,
    )

    return {
        "schemaVersion": 1,
        "id": recipe["id"],
        "title": recipe["title"],
        "profile": recipe["profile"],
        "hypothesis": _markdown_section(readme, "Hypothesis"),
        "question": _markdown_section(protocol, "Question"),
        "method": _markdown_section(protocol, "Model and training"),
        "acceptance": acceptance,
        "result": result,
        "environment": environment,
        "resources": resources,
        "contents": closure.get("classifications", {}),
        "reproduction": {
            "entrypoint": entrypoint,
            "networkRequired": closure.get("runtimeNetworkRequired"),
        },
        "source": recipe["manifest"]["source"],
        "standards": recipe["manifest"]["standards"],
        "package": expected,
    }


def _describe_lifecycle_recipe(recipe: dict, recipe_path: Path) -> dict:
    """Project lifecycle from the source-owned RO-Crate graph, without inference."""
    metadata = _json_member(recipe, recipe_path, "ro-crate-metadata.json")
    lifecycle = validate_lifecycle_crate(metadata)
    graph = metadata["@graph"]
    root = next(node for node in graph if isinstance(node, dict) and node.get("@id") == "./")
    main_id = root["mainEntity"]["@id"]
    protocol = _text_member(recipe, recipe_path, "experiment/protocol.md")
    readme = _text_member(recipe, recipe_path, "experiment/README.md")
    closure = None
    if any(item["path"] == "dependency-closure.json" for item in recipe["members"]):
        closure = _json_member(recipe, recipe_path, "dependency-closure.json")
    entities = {node["@id"]: node for node in graph if isinstance(node, dict) and isinstance(node.get("@id"), str)}
    interpretations = []
    for node in graph:
        if not isinstance(node, dict) or "CreativeWork" not in _types(node.get("@type")):
            continue
        summary = node.get("abstract")
        about = node.get("about")
        about_ids = [item.get("@id") for item in (about if isinstance(about, list) else [about])
                     if isinstance(item, dict)]
        attempts = [identifier for identifier in about_ids
                    if identifier in entities and "CreateAction" in _types(entities[identifier].get("@type"))]
        if isinstance(summary, str) and summary.strip() and attempts:
            interpretations.append({"record": node["@id"], "summary": summary,
                                    "aboutAttempt": attempts[0]})
    return {
        "schemaVersion": 1,
        "id": recipe["id"],
        "title": recipe["title"],
        "profile": recipe["profile"],
        "hypothesis": _markdown_section(readme, "Hypothesis"),
        "question": _markdown_section(protocol, "Question"),
        "method": _markdown_section(protocol, "Method"),
        "lifecycle": lifecycle,
        "contents": None if closure is None else closure.get("classifications"),
        "unavailablePrerequisites": None if closure is None else closure.get("classifications", {}).get("unavailable"),
        "scientificInterpretation": interpretations or None,
        "source": recipe["manifest"]["source"],
        "standards": recipe["manifest"]["standards"],
        "package": None if recipe.get("expectedPackage") is None else recipe["expectedPackage"],
    }


def discover_recipes(root: Path) -> list[Path]:
    """Discover compiled-experiment-v1 recipes by convention, with no registry file."""
    root = root.resolve()
    recipes: list[Path] = []
    ids: set[str] = set()
    for path in sorted(root.rglob("experiment.json")):
        recipe = load_recipe(path)
        if recipe["profile"] not in {"compiled-experiment-v1", "compiled-experiment-lifecycle-v1"}:
            continue
        if recipe["id"] in ids:
            raise PackageError(f"Duplicate compiled experiment id: {recipe['id']}")
        ids.add(recipe["id"])
        recipes.append(path)
    if not recipes:
        raise PackageError(f"No compiled-experiment-v1 recipes found under: {root}")
    return recipes


def describe_catalog(root: Path) -> dict:
    """Derive a deterministic public catalog from all discovered compiled experiments."""
    return {
        "schemaVersion": 1,
        "experiments": [describe_recipe(path) for path in discover_recipes(root)],
    }
