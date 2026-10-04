"""Compile reviewed local files; verify bytes without executing package contents.

poc-v1 preserves the historical packaging envelope. Scientific semantics remain
in the supplied standards documents, not in this build recipe or in Python code.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import Any

from . import __version__

MANIFEST = "experiment-package-manifest.json"
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")
FAMILY_URL_PREFIX = "https://experiments.tyharbin.com/#family-"
FAMILY_ID = re.compile(re.escape(FAMILY_URL_PREFIX) + r"[a-z0-9]+(?:-[a-z0-9]+)*")
EPOCH = (1980, 1, 1, 0, 0, 0)


class PackageError(ValueError):
    """Input is malformed, unsafe, unsupported or differs from its pinned bytes."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _pairs(items: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise PackageError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise PackageError(f"Non-finite JSON number: {value}")


def json_value(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PackageError(f"Invalid JSON: {exc}") from exc


def bounded_read(path: Path, maximum: int | None = None) -> bytes:
    """Read a regular file, optionally under an explicit caller-owned resource budget.

    Experiment Compiler does not impose a format-level byte ceiling. Callers such
    as execution-log collection may supply an operational budget; compile/verify
    use no implicit maximum.
    """
    if path.is_symlink() or not path.is_file():
        raise PackageError(f"Expected regular file: {path}")
    with path.open("rb") as stream:
        if maximum is None:
            return stream.read()
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise PackageError(f"File exceeds caller resource budget of {maximum} bytes: {path.name}")
    return data


def sha256_file(path: Path) -> str:
    """Hash a regular file incrementally without imposing an artifact-size limit."""
    if path.is_symlink() or not path.is_file():
        raise PackageError(f"Expected regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 1024 or
            any(ord(c) < 32 for c in value) or any(c in value for c in '\\:*?"<>|') or
            any(p in ("", ".", "..") or p.endswith((".", " ")) for p in value.split("/"))):
        raise PackageError(f"Unsafe or noncanonical relative path: {value!r}")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}
    if any(p.split(".")[0].upper() in reserved for p in value.split("/")):
        raise PackageError(f"Nonportable path: {value!r}")
    return value


def unique_paths(names: list[str]) -> None:
    folded: set[str] = set()
    for name in names:
        safe_path(name)
        key = name.casefold()
        if key in folded:
            raise PackageError(f"Duplicate or case-colliding path: {name}")
        folded.add(key)
    for name in folded:
        parts = name.split("/")
        if any("/".join(parts[:n]) in folded for n in range(1, len(parts))):
            raise PackageError(f"File/directory path collision: {name}")


def _digest(value: Any) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _size(value: Any) -> bool:
    return type(value) is int and value >= 0


def validate_header(header: Any) -> str:
    historical = {"schemaVersion", "packageType", "status", "source", "standards", "evidence"}
    compiled = historical | {"profile"}
    lifecycle = {"schemaVersion", "packageType", "profile", "source", "standards", "evidence"}
    if not isinstance(header, dict) or set(header) not in (historical, compiled, lifecycle):
        raise PackageError("Manifest header has unexpected or missing fields")
    if set(header) == historical:
        if type(header["schemaVersion"]) is not int or header["schemaVersion"] != 1:
            raise PackageError("Unsupported manifest schema version")
        if (header["packageType"] != "experiment-compiler-qualification-candidate" or
                header["status"] != "qualification-candidate"):
            raise PackageError("Unsupported poc-v1 manifest profile")
        profile = "poc-v1"
    elif set(header) == compiled:
        if type(header["schemaVersion"]) is not int or header["schemaVersion"] != 1:
            raise PackageError("Unsupported manifest schema version")
        if (header["profile"] != "compiled-experiment-v1" or
                header["packageType"] != "compiled-experiment" or
                header["status"] != "reproducible"):
            raise PackageError("Unsupported compiled experiment manifest profile")
        profile = "compiled-experiment-v1"
    else:
        if type(header["schemaVersion"]) is not int or header["schemaVersion"] != 2:
            raise PackageError("Unsupported lifecycle manifest schema version")
        if (header["profile"] != "compiled-experiment-lifecycle-v1" or
                header["packageType"] != "compiled-experiment"):
            raise PackageError("Unsupported lifecycle Compiled Experiment profile")
        profile = "compiled-experiment-lifecycle-v1"
    source = header["source"]
    if (not isinstance(source, dict) or set(source) != {"repository", "commit"} or
            not isinstance(source["repository"], str) or not source["repository"] or
            not isinstance(source["commit"], str) or not SHA1.fullmatch(source["commit"])):
        raise PackageError("Source must declare a repository and full commit SHA")
    if not isinstance(header["standards"], dict) or not isinstance(header["evidence"], dict):
        raise PackageError("Standards and evidence must be JSON objects")
    return profile


def _validate_related_articles(related_articles: Any) -> None:
    if not isinstance(related_articles, list):
        raise PackageError("relatedArticles must be a list")
    for article in related_articles:
        if (not isinstance(article, dict) or set(article) != {"title", "url", "sourceCommit"} or
                not isinstance(article["title"], str) or not article["title"].strip() or
                not isinstance(article["url"], str) or not isinstance(article["sourceCommit"], str) or
                not re.fullmatch(r"[0-9a-f]{40}", article["sourceCommit"])):
            raise PackageError("Related article requires a title, URL and exact source commit")
        # Match the raw URL: URL parsers can silently strip control characters.
        if not re.fullmatch(r"https://tyharbin\.com/articles/[a-z0-9-]+/", article["url"]):
            raise PackageError("Related article URL must be a canonical tyharbin.com article route")


def _validate_is_part_of(is_part_of: Any) -> None:
    if (not isinstance(is_part_of, dict) or set(is_part_of) != {"@id", "@type", "name"} or
            is_part_of.get("@type") != "CreativeWork" or
            not isinstance(is_part_of.get("name"), str) or not is_part_of["name"].strip()):
        raise PackageError("isPartOf must declare exactly @id, @type, and a nonblank name")
    identifier = is_part_of["@id"]
    if (not isinstance(identifier, str) or len(identifier) > 80 or
            FAMILY_ID.fullmatch(identifier) is None):
        raise PackageError("isPartOf @id must be a canonical bounded family URL")


def load_recipe(path: Path) -> dict:
    recipe = json_value(bounded_read(path))
    required = {"buildRecipeVersion", "id", "title", "profile", "manifest", "members"}
    optional = {"expectedPackage", "relatedArticles", "isPartOf"}
    if not isinstance(recipe, dict) or not required <= set(recipe) or set(recipe) - required - optional:
        raise PackageError("Recipe has unexpected or missing fields")
    if (type(recipe["buildRecipeVersion"]) is not int or recipe["buildRecipeVersion"] != 1 or
            recipe["profile"] not in {"poc-v1", "compiled-experiment-v1", "compiled-experiment-lifecycle-v1"}):
        raise PackageError("Unsupported recipe version/profile")
    if (not isinstance(recipe["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", recipe["id"]) or
            not isinstance(recipe["title"], str) or not recipe["title"]):
        raise PackageError("Recipe requires a portable ID and title")
    if validate_header(recipe["manifest"]) != recipe["profile"]:
        raise PackageError("Recipe profile differs from manifest profile")
    _validate_related_articles(recipe.get("relatedArticles", []))
    if "isPartOf" in recipe:
        _validate_is_part_of(recipe["isPartOf"])
    members = recipe["members"]
    if not isinstance(members, list) or not members:
        raise PackageError("Recipe requires a nonempty member list")
    for item in members:
        if (not isinstance(item, dict) or set(item) != {"source", "path", "sha256", "size"} or
                not _digest(item["sha256"]) or not _size(item["size"])):
            raise PackageError("Recipe member must pin source, path, SHA-256 and size")
        safe_path(item["source"])
    unique_paths([MANIFEST] + [item["path"] for item in members])
    expected = recipe.get("expectedPackage")
    if expected is not None and (not isinstance(expected, dict) or set(expected) != {"sha256", "size"} or
            not _digest(expected["sha256"]) or type(expected["size"]) is not int or
            expected["size"] <= 0):
        raise PackageError("Invalid expected package identity")
    return recipe


def manifest_for(recipe: dict) -> dict:
    return {**recipe["manifest"], "members": [
        {key: item[key] for key in ("path", "sha256", "size")}
        for item in sorted(recipe["members"], key=lambda x: x["path"])]}


def _source(root: Path, relative: str) -> Path:
    path = root
    for part in safe_path(relative).split("/"):
        path = path / part
        if path.is_symlink():
            raise PackageError(f"Source symlinks are not permitted: {relative}")
    if not path.resolve().is_relative_to(root):
        raise PackageError("Source escapes recipe directory")
    return path


def _is_git_lfs_pointer(data: bytes) -> bool:
    """Identify a Git LFS pointer so its small text is never mistaken for payload bytes."""
    if not data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        return False
    lines = data.splitlines()
    return (len(lines) == 3 and lines[1].startswith(b"oid sha256:") and
            len(lines[1]) == len(b"oid sha256:") + 64 and
            lines[2].startswith(b"size ") and lines[2][5:].isdigit())


def write_once(path: Path, data: bytes) -> None:
    """Idempotent for identical bytes; never overwrite a different existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if bounded_read(path) == data:
            return
        raise PackageError(f"Output already exists with different bytes: {path}")
    try:
        with path.open("xb") as out:
            out.write(data)
    except OSError:
        # Do not remove a path another process may have created.
        raise


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, EPOCH)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _validate_source_member(root: Path, item: dict) -> Path:
    source = _source(root, item["source"])
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise PackageError(f"Expected regular file: {source}") from exc
    if source.is_symlink() or not source.is_file():
        raise PackageError(f"Expected regular file: {source}")
    if size != item["size"] or sha256_file(source) != item["sha256"]:
        raise PackageError(f"Source integrity mismatch: {item['source']}")
    if size <= 1024 and _is_git_lfs_pointer(bounded_read(source)):
        raise PackageError(f"Git LFS pointer is not the referenced payload: {item['source']}")
    if item["path"].endswith((".json", ".jsonld")):
        if not isinstance(json_value(bounded_read(source)), (dict, list)):
            raise PackageError("JSON members must have an object or array root")
    return source


def _legacy_compatibility_bytes(recipe: dict, root: Path) -> bytes:
    """Preserve byte-identical historical fixtures that already pin whole ZIP bytes."""
    files: dict[str, bytes] = {}
    for item in recipe["members"]:
        source = _validate_source_member(root, item)
        files[item["path"]] = bounded_read(source)
    files[MANIFEST] = canonical(manifest_for(recipe))
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(_zip_info(name), data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return target.getvalue()


def _write_streaming_zip(recipe: dict, root: Path, target: Path) -> None:
    sources = {
        item["path"]: _validate_source_member(root, item)
        for item in recipe["members"]
    }
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True
    ) as archive:
        for name, source in sorted(sources.items()):
            info = _zip_info(name)
            size = source.stat().st_size
            info.file_size = size
            with source.open("rb") as src, archive.open(
                info, "w", force_zip64=size >= zipfile.ZIP64_LIMIT
            ) as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        archive.writestr(
            _zip_info(MANIFEST),
            canonical(manifest_for(recipe)),
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )


def _files_equal(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as a, right.open("rb") as b:
        while True:
            a_chunk = a.read(1024 * 1024)
            b_chunk = b.read(1024 * 1024)
            if a_chunk != b_chunk:
                return False
            if not a_chunk:
                return True


def _publish_file_once(source: Path, output: Path) -> None:
    """Publish a completed package without overwriting different existing bytes."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        if output.is_file() and not output.is_symlink() and _files_equal(source, output):
            return
        raise PackageError(f"Output already exists with different bytes: {output}")
    created = False
    try:
        with source.open("rb") as src, output.open("xb") as dst:
            created = True
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise


def compile_package(recipe_path: Path, output: Path) -> dict:
    recipe = load_recipe(recipe_path)
    root = recipe_path.parent.resolve()
    expected = recipe.get("expectedPackage")

    # Preserve the historical deterministic ZIP byte layout for ordinary small
    # packages. This threshold chooses an assembly implementation only; it is
    # not an artifact validity limit. Larger packages transparently stream.
    use_legacy_assembly = sum(item["size"] for item in recipe["members"]) <= 64 * 1024 * 1024
    if expected is not None or use_legacy_assembly:
        data = _legacy_compatibility_bytes(recipe, root)
        if expected is not None and (sha256(data), len(data)) != (expected["sha256"], expected["size"]):
            raise PackageError("Built ZIP differs from pinned compatibility target; do not update the target blindly")
        report = verify_bytes(data, recipe=recipe)
        write_once(output, data)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".experiment-compiler-", suffix=".zip", dir=output.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            _write_streaming_zip(recipe, root, temporary)
            report = verify_package(temporary, recipe=recipe)
            _publish_file_once(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)

    return {
        **report,
        "build": {
            "compilerVersion": __version__,
            "python": platform.python_version(),
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
            "recipeSha256": sha256_file(recipe_path),
            "profile": recipe["profile"],
        },
    }

def _lifecycle_graph(crate: Any) -> tuple[list, dict[str, dict]]:
    if not isinstance(crate, dict) or not isinstance(crate.get("@graph"), list):
        raise PackageError("Lifecycle metadata requires an RO-Crate @graph array")
    graph = crate["@graph"]
    raw_context = crate.get("@context")
    if raw_context not in (
            ["https://w3id.org/ro/crate/1.3/context"],
            ["https://w3id.org/ro/crate/1.3/context", "https://w3id.org/ro/terms/workflow-run/context"]):
        raise PackageError("Lifecycle crate must use the RO-Crate 1.3 context, with Workflow Run context only as needed")
    nodes: dict[str, dict] = {}
    for node in graph:
        if not isinstance(node, dict) or not isinstance(node.get("@id"), str):
            raise PackageError("Every lifecycle graph entity must have a string @id")
        if node["@id"] in nodes:
            raise PackageError(f"Duplicate lifecycle graph @id: {node['@id']}")
        nodes[node["@id"]] = node
    return graph, nodes


def _lifecycle_root(nodes: dict[str, dict]) -> dict:
    root = nodes.get("./")
    if not isinstance(root, dict) or "Dataset" not in _types(root.get("@type")):
        raise PackageError("Lifecycle RO-Crate requires a root Dataset")
    descriptor = nodes.get("ro-crate-metadata.json")
    if (descriptor is None or "CreativeWork" not in _types(descriptor.get("@type")) or
            not isinstance(descriptor.get("about"), dict) or descriptor["about"].get("@id") != "./"):
        raise PackageError("Lifecycle crate requires an RO-Crate metadata descriptor about the root Dataset")
    if not any(ref.get("@id") == "https://w3id.org/ro/crate/1.3" for ref in _as_refs(descriptor.get("conformsTo"))):
        raise PackageError("Lifecycle metadata descriptor must declare RO-Crate 1.3")
    if not any(ref.get("@id") == "https://w3id.org/ro/crate/1.3" for ref in _as_refs(root.get("conformsTo"))):
        raise PackageError("Lifecycle root Dataset must declare RO-Crate 1.3")
    return root


def _lifecycle_protocol(root: dict, nodes: dict[str, dict]) -> Any:
    main = root.get("mainEntity")
    if not isinstance(main, dict) or not isinstance(main.get("@id"), str):
        raise PackageError("Lifecycle root Dataset must identify its main protocol entity")
    protocol = nodes.get(main["@id"])
    if not isinstance(protocol, dict) or not _types(protocol.get("@type")) & {
            "CreativeWork", "ScholarlyArticle", "SoftwareSourceCode"}:
        raise PackageError("Lifecycle mainEntity must be a protocol CreativeWork")
    maturity = protocol.get("creativeWorkStatus")
    if not (isinstance(maturity, str) and maturity.strip()) and not (
            isinstance(maturity, dict) and isinstance(maturity.get("@id"), str)):
        raise PackageError("Protocol CreativeWork must declare Schema.org creativeWorkStatus")
    return maturity


def _measurement_node(value: dict, nodes: dict[str, dict]) -> dict:
    measurement = nodes.get(value.get("@id")) if isinstance(value.get("@id"), str) else value
    if (not isinstance(measurement, dict) or "PropertyValue" not in _types(measurement.get("@type")) or
            not isinstance(measurement.get("propertyID"), str) or not measurement["propertyID"].strip() or
            not isinstance(measurement.get("measurementTechnique"), str) or
            not measurement["measurementTechnique"].strip() or "value" not in measurement):
        raise PackageError("Root Dataset variableMeasured must reference documented Schema.org PropertyValue records")
    return measurement


def _measurement_evidence(measurement: dict) -> None:
    amount = measurement["value"]
    if isinstance(amount, bool) or not isinstance(amount, (str, int, float)):
        raise PackageError("Resource PropertyValue must use a number or explicit text such as unknown")
    technique = measurement["measurementTechnique"].strip().casefold()
    if isinstance(amount, str) and amount.strip().casefold() == "unknown":
        if "unitText" in measurement:
            raise PackageError("Unknown resource PropertyValue must not invent a unit or zero quantity")
        return
    if not (isinstance(measurement.get("unitText"), str) and measurement["unitText"].strip()):
        raise PackageError("Numeric resource PropertyValue must declare Schema.org unitText")
    if amount == 0 and any(term in technique for term in ("unknown", "unmeasured", "not measured")):
        raise PackageError("Unknown resource PropertyValue must not be encoded as zero")
    evidence = measurement.get("valueReference")
    if not ((isinstance(evidence, str) and evidence.strip()) or
            (isinstance(evidence, dict) and isinstance(evidence.get("@id"), str))):
        raise PackageError("Numeric resource PropertyValue must link its evidence or estimate basis")


def _resource_measurements(root: dict, nodes: dict[str, dict]) -> list[dict]:
    result = []
    for value in _as_refs(root.get("variableMeasured")):
        measurement = _measurement_node(value, nodes)
        _measurement_evidence(measurement)
        result.append({
            "propertyID": measurement["propertyID"],
            "value": measurement["value"],
            "unitText": measurement.get("unitText"),
            "measurementTechnique": measurement["measurementTechnique"],
            "evidence": measurement.get("valueReference"),
        })
    return result


def _execution_action_nodes(graph: list) -> list[dict]:
    actions = [
        node for node in graph
        if isinstance(node, dict)
        and "CreateAction" in _types(node.get("@type"))
        and node.get("actionStatus") != "https://schema.org/PotentialActionStatus"
    ]
    ids = [node.get("@id") for node in actions]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise PackageError("Each execution CreateAction must have a unique @id")
    return actions


def _validate_execution_action(action: dict, nodes: dict[str, dict]) -> None:
    allowed = {
        "https://schema.org/ActiveActionStatus",
        "https://schema.org/CompletedActionStatus",
        "https://schema.org/FailedActionStatus",
    }
    status = action.get("actionStatus")
    if not isinstance(status, str) or status not in allowed:
        raise PackageError("Every CreateAction must carry an explicit Schema.org actionStatus")
    instrument = action.get("instrument")
    if not isinstance(instrument, dict) or not isinstance(instrument.get("@id"), str):
        raise PackageError("Every execution CreateAction must identify its instrument")
    instrument_node = nodes.get(instrument["@id"])
    if instrument_node is None or not ({"SoftwareApplication", "SoftwareSourceCode"} &
                                       _types(instrument_node.get("@type"))):
        raise PackageError("CreateAction instrument must resolve to described executable software")


def _validate_run_profile(root: dict, nodes: dict[str, dict], actions: list[dict]) -> bool:
    run_profile = "https://w3id.org/ro/wfrun/process/0.6"
    declares_run = any(ref.get("@id") == run_profile for ref in _as_refs(root.get("conformsTo")))
    if actions and not declares_run:
        raise PackageError("Execution actions require Process Run Crate 0.6 provenance")
    if declares_run and not actions:
        raise PackageError("Prospective-only crate cannot claim Process Run Crate 0.6 conformance")
    if declares_run:
        profile_node = nodes.get(run_profile)
        if (profile_node is None or profile_node.get("version") != "0.6" or
                "Profile" not in _types(profile_node.get("@type"))):
            raise PackageError("Process Run Crate 0.6 conformance requires its described profile entity")
    return declares_run


def _execution_actions(graph: list, nodes: dict[str, dict], root: dict) -> tuple[list[dict], bool]:
    actions = _execution_action_nodes(graph)
    for action in actions:
        _validate_execution_action(action, nodes)
    return actions, _validate_run_profile(root, nodes, actions)


def _validate_lifecycle_members(graph: list, root: dict) -> None:
    members = {
        node.get("@id")
        for node in graph
        if isinstance(node, dict) and "File" in _types(node.get("@type"))
    }
    for part in _as_refs(root.get("hasPart")):
        identifier = part.get("@id")
        if isinstance(identifier, str) and not identifier.startswith(("#", "http://", "https://")):
            if identifier not in members:
                raise PackageError(f"RO-Crate root hasPart does not resolve to a described File: {identifier}")


def _potential_action_count(graph: list, nodes: dict[str, dict]) -> int:
    potential = []
    for node in graph:
        if not isinstance(node, dict):
            continue
        for ref in _as_refs(node.get("potentialAction")):
            action = nodes.get(ref.get("@id"))
            if action is None:
                raise PackageError("potentialAction references a missing graph node")
            if action.get("actionStatus") != "https://schema.org/PotentialActionStatus":
                raise PackageError("Unexecuted potentialAction must use PotentialActionStatus")
            potential.append(action.get("@id"))
    return len(potential)


def validate_lifecycle_crate(crate: Any) -> dict:
    """Validate lifecycle facts without interpreting scientific results."""
    graph, nodes = _lifecycle_graph(crate)
    root = _lifecycle_root(nodes)
    maturity = _lifecycle_protocol(root, nodes)
    resource_measurements = _resource_measurements(root, nodes)
    actions, declares_run = _execution_actions(graph, nodes, root)
    _validate_lifecycle_members(graph, root)
    potential_action_count = _potential_action_count(graph, nodes)
    return {
        "creativeWorkStatus": maturity,
        "attemptCount": len(actions),
        "processRunCrate": declares_run,
        "potentialActionCount": potential_action_count,
        "resourceMeasurements": resource_measurements,
    }


def _types(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _as_refs(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _hash_stream(stream) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _verify_archive(archive: zipfile.ZipFile, *, package_sha256: str, package_size: int,
                    expected_sha256: str | None = None, recipe: dict | None = None) -> dict:
    if expected_sha256 is not None and (not _digest(expected_sha256) or package_sha256 != expected_sha256):
        raise PackageError("Package SHA-256 mismatch")
    expected = recipe.get("expectedPackage") if recipe else None
    if expected and (package_sha256, package_size) != (expected["sha256"], expected["size"]):
        raise PackageError("Package differs from recipe compatibility target")

    infos = archive.infolist()
    if len(infos) < 2:
        raise PackageError("Invalid ZIP member count")
    unique_paths([item.orig_filename for item in infos])
    if any(
        item.filename != item.orig_filename
        or item.is_dir()
        or item.flag_bits & 1
        or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
        or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
        for item in infos
    ):
        raise PackageError("Unsupported ZIP member")

    manifest_raw = archive.read(MANIFEST)
    manifest = json_value(manifest_raw)
    if not isinstance(manifest, dict) or "members" not in manifest:
        raise PackageError("Missing manifest member inventory")
    profile = validate_header({key: value for key, value in manifest.items() if key != "members"})
    members = manifest["members"]
    if not isinstance(members, list) or not members:
        raise PackageError("Manifest member list must be nonempty")
    for item in members:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or not _digest(item["sha256"])
            or not _size(item["size"])
        ):
            raise PackageError("Malformed manifest member")

    names = [MANIFEST] + [item["path"] for item in members]
    unique_paths(names)
    if set(names) != set(archive.namelist()):
        raise PackageError("ZIP inventory differs from manifest (missing or unlisted file)")

    by_name = {item.filename: item for item in infos}
    for item in members:
        info = by_name[item["path"]]
        if info.file_size != item["size"]:
            raise PackageError(f"Member size mismatch: {item['path']}")
        with archive.open(item["path"]) as stream:
            digest, size = _hash_stream(stream)
        if (digest, size) != (item["sha256"], item["size"]):
            raise PackageError(f"Member integrity mismatch: {item['path']}")
        if item["path"].endswith((".json", ".jsonld")):
            if not isinstance(json_value(archive.read(item["path"])), (dict, list)):
                raise PackageError("JSON members must have an object or array root")

    lifecycle_projection = None
    if profile == "compiled-experiment-lifecycle-v1":
        metadata_path = "ro-crate-metadata.json"
        if metadata_path not in {item["path"] for item in members}:
            raise PackageError(f"Lifecycle profile requires {metadata_path}")
        lifecycle_projection = validate_lifecycle_crate(json_value(archive.read(metadata_path)))
    if recipe and (profile != recipe["profile"] or manifest != manifest_for(recipe)):
        raise PackageError("Package manifest differs from reviewed recipe")

    return {
        "schemaVersion": 1,
        "status": "integrity-verified",
        "scope": "package-integrity-only",
        "scientificReproduction": "not-run",
        "profile": profile,
        "packageSha256": package_sha256,
        "packageSizeBytes": package_size,
        "memberCount": len(infos),
        "manifestSha256": sha256(manifest_raw),
        "source": manifest["source"],
        "matchedExpectedPackage": bool(expected or expected_sha256),
        **({"lifecycle": lifecycle_projection} if lifecycle_projection is not None else {}),
    }


def verify_package(package: Path, *, expected_sha256: str | None = None,
                   recipe: dict | None = None) -> dict:
    """Verify a package from disk with streaming member hashing and no format byte ceiling."""
    if package.is_symlink() or not package.is_file():
        raise PackageError(f"Expected regular package file: {package}")
    package_sha = sha256_file(package)
    package_size = package.stat().st_size
    try:
        with zipfile.ZipFile(package) as archive:
            return _verify_archive(
                archive,
                package_sha256=package_sha,
                package_size=package_size,
                expected_sha256=expected_sha256,
                recipe=recipe,
            )
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise PackageError(f"Invalid package: {exc}") from exc


def verify_bytes(data: bytes, *, expected_sha256: str | None = None, recipe: dict | None = None) -> dict:
    """Compatibility API for callers that already hold package bytes in memory."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _verify_archive(
                archive,
                package_sha256=sha256(data),
                package_size=len(data),
                expected_sha256=expected_sha256,
                recipe=recipe,
            )
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise PackageError(f"Invalid package: {exc}") from exc

