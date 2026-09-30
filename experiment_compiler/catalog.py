"""Derive display metadata from authoritative Compiled Experiment artifacts.

This module stores no experiment-specific facts. It projects reviewed recipe
and packaged scientific artifacts into display-friendly JSON so sites and
publishers do not maintain duplicate metadata.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from .core import (
    MAX_FILE,
    PackageError,
    _execution_action_nodes,
    _types,
    _validate_related_articles,
    bounded_read,
    compile_package,
    json_value,
    load_recipe,
    validate_lifecycle_crate,
)


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
        "backlinks": recipe.get("relatedArticles", []),
    }


def _describe_lifecycle_recipe(recipe: dict, recipe_path: Path) -> dict:
    """Project lifecycle from the source-owned RO-Crate graph, without inference."""
    expected = recipe.get("expectedPackage")
    if expected is not None:
        # Verify every source member and the deterministic package identity before
        # projecting metadata. This prevents a changed .source file from silently
        # changing catalog output while the pinned ZIP remains unchanged.
        with tempfile.TemporaryDirectory(prefix="compiled-experiment-catalog-") as temporary:
            compile_package(recipe_path, Path(temporary) / "verified.zip")
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
    # Keep execution status separate from source-owned scientific interpretation.
    # A result reference must identify retained bytes, not an unavailable URL.
    members = {item["path"] for item in recipe["members"]}
    attempts = []
    for action in _execution_action_nodes(graph):
        raw_results = action.get("result", [])
        results = raw_results if isinstance(raw_results, list) else [raw_results]
        identifiers = []
        for result in results:
            identifier = result.get("@id") if isinstance(result, dict) else None
            entity = entities.get(identifier) if isinstance(identifier, str) else None
            if (identifier not in members or entity is None or
                    "File" not in _types(entity.get("@type"))):
                raise PackageError("Catalog execution result must resolve to a packaged File")
            if identifier in identifiers:
                raise PackageError("Catalog execution result references must be unique")
            identifiers.append(identifier)
        attempts.append({"id": action["@id"], "actionStatus": action["actionStatus"],
                         "result": identifiers})
    interpretations = []
    for node in graph:
        if (not isinstance(node, dict) or "File" not in _types(node.get("@type")) or
                "CreativeWork" not in _types(node.get("@type")) or
                node.get("name") != "Scientific decision and interpretation"):
            continue
        summary = node.get("abstract")
        about = node.get("about")
        about_ids = [item.get("@id") for item in (about if isinstance(about, list) else [about])
                     if isinstance(item, dict)]
        about_attempts = [identifier for identifier in about_ids
                    if identifier in entities and "CreateAction" in _types(entities[identifier].get("@type"))]
        if isinstance(summary, str) and summary.strip() and len(about_attempts) == 1:
            interpretations.append({"record": node["@id"], "summary": summary,
                                    "aboutAttempt": about_attempts[0]})
    return {
        "schemaVersion": 1,
        "id": recipe["id"],
        "title": recipe["title"],
        "profile": recipe["profile"],
        "hypothesis": _markdown_section(readme, "Hypothesis"),
        "question": _markdown_section(protocol, "Question"),
        "method": _markdown_section(protocol, "Method"),
        "protocol": {"record": main_id, "text": protocol},
        "lifecycle": lifecycle,
        "executionAttempts": attempts,
        "contents": None if closure is None else closure.get("classifications"),
        "unavailablePrerequisites": None if closure is None else closure.get("classifications", {}).get("unavailable"),
        "scientificInterpretation": interpretations or None,
        "source": recipe["manifest"]["source"],
        "standards": recipe["manifest"]["standards"],
        "package": None if recipe.get("expectedPackage") is None else recipe["expectedPackage"],
        "backlinks": recipe.get("relatedArticles", []),
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
        "schemaVersion": 2,
        "experiments": [describe_recipe(path) for path in discover_recipes(root)],
    }


def resolve_article_reference(catalog: dict, reference: dict, *, article: dict | None = None) -> dict:
    """Resolve a v1 exact article reference against a v2 public projection.

    Optional article assertions require the source-owned backlink to round-trip;
    sourceCommit is article provenance, never scientific qualification.
    """
    if not isinstance(catalog, dict):
        raise PackageError("Public projection must be an object")
    if type(catalog.get("schemaVersion")) is not int or catalog["schemaVersion"] != 2:
        raise PackageError("Unsupported public projection version")
    if catalog.get("project") != {"name": "Experiment Compiler",
                                  "repository": "https://github.com/pH34r-pH/experiment-compiler"}:
        raise PackageError("Public projection authority mismatch")
    _validate_article_reference(reference)
    record = _exact_projection_record(catalog, reference["ref"])
    expected = reference.get("expected", {})
    actual = _projected_identity(record)
    if any(actual[key] != value for key, value in expected.items()):
        raise PackageError("Expected experiment identity mismatch")
    backlinks = record.get("backlinks")
    _validate_related_articles(backlinks)
    if article is not None:
        _validate_related_articles([article])
        if not any(item["url"] == article["url"] and
                   item["sourceCommit"] == article["sourceCommit"] for item in backlinks):
            raise PackageError("Canonical article relationship missing or mismatched")
    return record


def _validate_article_reference(reference: dict) -> None:
    if not isinstance(reference, dict) or not {"ref"} <= reference.keys() or reference.keys() - {"ref", "expected"}:
        raise PackageError("Article reference requires an exact ref")
    identifier = reference["ref"]
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", identifier) or "latest" in identifier.split("-"):
        raise PackageError("Article reference must be immutable and exact")
    _validate_expected_identity(reference.get("expected", {}))


def _validate_expected_identity(expected: dict) -> None:
    if not isinstance(expected, dict) or expected.keys() - {"sha256", "profile", "source"}:
        raise PackageError("Malformed expected identity assertions")
    if "sha256" in expected and (not isinstance(expected["sha256"], str) or
                                 not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"])):
        raise PackageError("Expected digest must be SHA-256")
    if "profile" in expected and expected["profile"] not in ("compiled-experiment-v1", "compiled-experiment-lifecycle-v1"):
        raise PackageError("Unknown expected profile")
    if "source" in expected:
        _validate_expected_source(expected["source"])


def _validate_expected_source(source: dict) -> None:
    if (not isinstance(source, dict) or set(source) != {"repository", "commit"} or
            not isinstance(source["repository"], str) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repository"]) or
            not isinstance(source["commit"], str) or
            not re.fullmatch(r"[0-9a-f]{40}", source["commit"])):
        raise PackageError("Expected source requires repository and exact commit")


def _exact_projection_record(catalog: dict, identifier: str) -> dict:
    records = catalog.get("experiments")
    if not isinstance(records, list):
        raise PackageError("Projection experiments must be a list")
    indexed = {}
    for record in records:
        key = record.get("id") if isinstance(record, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", key):
            raise PackageError("Malformed projection identity")
        if key in indexed:
            raise PackageError("Duplicate projection identity")
        indexed[key] = record
    record = indexed.get(identifier)
    if record is None:
        raise PackageError("Unknown exact experiment identity")
    if record.get("detailUrl") != f"/experiments/{identifier}/":
        raise PackageError("Experiment detail identity mismatch")
    return record


def _projected_identity(record: dict) -> dict:
    package = record.get("package")
    if not isinstance(package, dict):
        raise PackageError("Missing or invalid package identity")
    actual = {"sha256": package.get("sha256"), "profile": record.get("profile"),
              "source": record.get("source")}
    _validate_expected_identity(actual)
    if type(package.get("size")) is not int or package["size"] < 1:
        raise PackageError("Missing or invalid package size")
    return actual
