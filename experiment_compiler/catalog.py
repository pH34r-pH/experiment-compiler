"""Derive display metadata from authoritative Compiled Experiment artifacts.

This module stores no experiment-specific facts. It projects reviewed recipe
and packaged scientific artifacts into display-friendly JSON so sites and
publishers do not maintain duplicate metadata.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .core import MAX_FILE, PackageError, bounded_read, json_value, load_recipe


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
    if recipe["profile"] != "compiled-experiment-v1":
        raise PackageError("describe currently requires compiled-experiment-v1")
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
