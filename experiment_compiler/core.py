"""Compile reviewed local files; verify bytes without executing package contents.

poc-v1 preserves the historical packaging envelope. Scientific semantics remain
in the supplied standards documents, not in this build recipe or in Python code.
"""
from __future__ import annotations

import hashlib
import io
import json
import platform
import re
import stat
import zipfile
import zlib
from pathlib import Path
from typing import Any

from . import __version__

MANIFEST = "experiment-package-manifest.json"
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_MEMBERS = 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")
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


def bounded_read(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PackageError(f"Expected regular file: {path}")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise PackageError(f"File exceeds {maximum} byte limit: {path.name}")
    return data


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
    return type(value) is int and 0 <= value <= MAX_FILE


def validate_header(header: Any) -> str:
    historical = {"schemaVersion", "packageType", "status", "source", "standards", "evidence"}
    compiled = historical | {"profile"}
    if not isinstance(header, dict) or set(header) not in (historical, compiled):
        raise PackageError("Manifest header has unexpected or missing fields")
    if type(header["schemaVersion"]) is not int or header["schemaVersion"] != 1:
        raise PackageError("Unsupported manifest schema version")
    if set(header) == historical:
        if (header["packageType"] != "experiment-compiler-qualification-candidate" or
                header["status"] != "qualification-candidate"):
            raise PackageError("Unsupported poc-v1 manifest profile")
        profile = "poc-v1"
    else:
        if (header["profile"] != "compiled-experiment-v1" or
                header["packageType"] != "compiled-experiment" or
                header["status"] != "reproducible"):
            raise PackageError("Unsupported compiled experiment manifest profile")
        profile = "compiled-experiment-v1"
    source = header["source"]
    if (not isinstance(source, dict) or set(source) != {"repository", "commit"} or
            not isinstance(source["repository"], str) or not source["repository"] or
            not isinstance(source["commit"], str) or not SHA1.fullmatch(source["commit"])):
        raise PackageError("Source must declare a repository and full commit SHA")
    if not isinstance(header["standards"], dict) or not isinstance(header["evidence"], dict):
        raise PackageError("Standards and evidence must be JSON objects")
    return profile


def load_recipe(path: Path) -> dict:
    recipe = json_value(bounded_read(path, MAX_FILE))
    required = {"buildRecipeVersion", "id", "title", "profile", "manifest", "members"}
    if not isinstance(recipe, dict) or not required <= set(recipe) or set(recipe) - required - {"expectedPackage"}:
        raise PackageError("Recipe has unexpected or missing fields")
    if (type(recipe["buildRecipeVersion"]) is not int or recipe["buildRecipeVersion"] != 1 or
            recipe["profile"] not in {"poc-v1", "compiled-experiment-v1"}):
        raise PackageError("Unsupported recipe version/profile")
    if (not isinstance(recipe["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", recipe["id"]) or
            not isinstance(recipe["title"], str) or not recipe["title"]):
        raise PackageError("Recipe requires a portable ID and title")
    if validate_header(recipe["manifest"]) != recipe["profile"]:
        raise PackageError("Recipe profile differs from manifest profile")
    members = recipe["members"]
    if not isinstance(members, list) or not 1 <= len(members) < MAX_MEMBERS:
        raise PackageError("Recipe requires a bounded, nonempty member list")
    for item in members:
        if (not isinstance(item, dict) or set(item) != {"source", "path", "sha256", "size"} or
                not _digest(item["sha256"]) or not _size(item["size"])):
            raise PackageError("Recipe member must pin source, path, SHA-256 and size")
        safe_path(item["source"])
    unique_paths([MANIFEST] + [item["path"] for item in members])
    if sum(item["size"] for item in members) > MAX_TOTAL - MAX_FILE:
        raise PackageError("Recipe exceeds total byte limit")
    expected = recipe.get("expectedPackage")
    if expected is not None and (not isinstance(expected, dict) or set(expected) != {"sha256", "size"} or
            not _digest(expected["sha256"]) or type(expected["size"]) is not int or
            not 0 < expected["size"] <= MAX_TOTAL):
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


def write_once(path: Path, data: bytes) -> None:
    """Idempotent for identical bytes; never overwrite a different existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if bounded_read(path, MAX_TOTAL) == data:
            return
        raise PackageError(f"Output already exists with different bytes: {path}")
    try:
        with path.open("xb") as out:
            out.write(data)
    except OSError:
        # Do not remove a path another process may have created.
        raise


def compile_package(recipe_path: Path, output: Path) -> dict:
    recipe = load_recipe(recipe_path)
    root = recipe_path.parent.resolve()
    files: dict[str, bytes] = {}
    for item in recipe["members"]:
        data = bounded_read(_source(root, item["source"]), MAX_FILE)
        if len(data) != item["size"] or sha256(data) != item["sha256"]:
            raise PackageError(f"Source integrity mismatch: {item['source']}")
        if item["path"].endswith((".json", ".jsonld")):
            if not isinstance(json_value(data), (dict, list)):
                raise PackageError("JSON members must have an object or array root")
        files[item["path"]] = data
    files[MANIFEST] = canonical(manifest_for(recipe))
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, EPOCH)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    data = target.getvalue()
    expected = recipe.get("expectedPackage")
    if expected and (sha256(data), len(data)) != (expected["sha256"], expected["size"]):
        raise PackageError("Built ZIP differs from pinned compatibility target; do not update the target blindly")
    report = verify_bytes(data, recipe=recipe)
    write_once(output, data)
    return {**report, "build": {"compilerVersion": __version__, "python": platform.python_version(),
            "zlib": zlib.ZLIB_RUNTIME_VERSION, "recipeSha256": sha256(bounded_read(recipe_path, MAX_FILE)),
            "profile": recipe["profile"]}}


def verify_bytes(data: bytes, *, expected_sha256: str | None = None, recipe: dict | None = None) -> dict:
    """Check complete inventory and hashes. This is not scientific qualification."""
    if len(data) > MAX_TOTAL:
        raise PackageError("ZIP exceeds total byte limit")
    actual = sha256(data)
    if expected_sha256 is not None and (not _digest(expected_sha256) or actual != expected_sha256):
        raise PackageError("Package SHA-256 mismatch")
    expected = recipe.get("expectedPackage") if recipe else None
    if expected and (actual, len(data)) != (expected["sha256"], expected["size"]):
        raise PackageError("Package differs from recipe compatibility target")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if not 2 <= len(infos) <= MAX_MEMBERS:
                raise PackageError("Invalid ZIP member count")
            unique_paths([i.orig_filename for i in infos])
            if any(i.filename != i.orig_filename or i.is_dir() or i.flag_bits & 1 or
                    stat.S_IFMT(i.external_attr >> 16) not in (0, stat.S_IFREG) or
                    i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                    i.file_size > MAX_FILE for i in infos) or sum(i.file_size for i in infos) > MAX_TOTAL:
                raise PackageError("Unsupported or oversized ZIP member")
            manifest_raw = z.read(MANIFEST)
            manifest = json_value(manifest_raw)
            if not isinstance(manifest, dict) or "members" not in manifest:
                raise PackageError("Missing manifest member inventory")
            profile = validate_header({k: v for k, v in manifest.items() if k != "members"})
            members = manifest["members"]
            if not isinstance(members, list) or not members:
                raise PackageError("Manifest member list must be nonempty")
            for item in members:
                if (not isinstance(item, dict) or set(item) != {"path", "sha256", "size"} or
                        not _digest(item["sha256"]) or not _size(item["size"])):
                    raise PackageError("Malformed manifest member")
            names = [MANIFEST] + [item["path"] for item in members]
            unique_paths(names)
            if set(names) != set(z.namelist()):
                raise PackageError("ZIP inventory differs from manifest (missing or unlisted file)")
            for item in members:
                payload = z.read(item["path"])
                if (sha256(payload), len(payload)) != (item["sha256"], item["size"]):
                    raise PackageError(f"Member integrity mismatch: {item['path']}")
                if item["path"].endswith((".json", ".jsonld")):
                    if not isinstance(json_value(payload), (dict, list)):
                        raise PackageError("JSON members must have an object or array root")
            if recipe and (profile != recipe["profile"] or manifest != manifest_for(recipe)):
                raise PackageError("Package manifest differs from reviewed recipe")
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise PackageError(f"Invalid package: {exc}") from exc
    return {"schemaVersion": 1, "status": "integrity-verified", "scope": "package-integrity-only",
            "scientificReproduction": "not-run", "profile": profile,
            "packageSha256": actual, "packageSizeBytes": len(data),
            "memberCount": len(infos), "manifestSha256": sha256(manifest_raw),
            "source": manifest["source"], "matchedExpectedPackage": bool(expected or expected_sha256)}
