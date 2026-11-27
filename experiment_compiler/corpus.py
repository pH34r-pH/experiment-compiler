"""Read back an authorized local copy of a pinned Croissant file inventory."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from pathlib import Path

from .archive_policy import PackageReadPolicy
from .core import SHA256, PackageError, bounded_read, json_value, sha256, unique_paths, verify_package

CROISSANT_SOURCE = {
    "repository": "pH34r-pH/long-haul",
    "commit": "71f75bca00aacebf75c73155e3fca1d007ffd501",
}
CROISSANT_REFERENCE = "artifact:croissant-1.1"
CROISSANT_VERSION = "http://mlcommons.org/croissant/1.1"
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def is_bound_corpus(reference: dict) -> bool:
    return (
        reference["kind"] == "governed-corpus"
        and reference["source"] == CROISSANT_SOURCE
        and reference["reference"] == CROISSANT_REFERENCE
    )


def _file_identity(file: object) -> tuple[str, str]:
    fields = {"@id", "@type", "name", "contentUrl", "encodingFormat", "sha256"}
    if not isinstance(file, dict) or file.keys() != fields:
        raise PackageError("Expected direct Croissant FileObject records")
    name, digest = file["name"], file["sha256"]
    if (not isinstance(name, str) or not FILE_NAME.fullmatch(name)
            or not isinstance(digest, str) or not SHA256.fullmatch(digest)
            or file["@id"] != "files/" + name or file["@type"] != "cr:FileObject"):
        raise PackageError("Croissant file identity or digest is invalid")
    return name, digest


def _inventory(raw: bytes) -> dict[str, str]:
    try:
        value = json_value(raw)
    except PackageError as exc:
        raise PackageError("Retained Croissant metadata is invalid JSON") from exc
    if (not isinstance(value, dict) or value.get("@type") != "sc:Dataset"
            or value.get("conformsTo") != CROISSANT_VERSION):
        raise PackageError("Expected the pinned Croissant 1.1 dataset projection")
    files = value.get("distribution")
    if not isinstance(files, list) or not files:
        raise PackageError("Croissant metadata lacks a file inventory")
    pairs = [_file_identity(file) for file in files]
    try:
        unique_paths([name for name, _ in pairs])
    except PackageError as exc:
        raise PackageError("Croissant snapshot needs unique portable file names") from exc
    return dict(pairs)


def _mode(path: Path) -> int:
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise PackageError("Croissant snapshot cannot contain linked paths")
    return info.st_mode


def _verify_files(snapshot: Path, inventory: dict[str, str], budget: int) -> int:
    try:
        if not stat.S_ISDIR(_mode(snapshot)):
            raise PackageError("Expected a local Croissant snapshot directory")
        # Stop at the first extra entry, without materializing an unbounded directory.
        missing = set(inventory)
        with os.scandir(snapshot) as entries:
            for entry in entries:
                if entry.name not in missing or not stat.S_ISREG(_mode(Path(entry.path))):
                    raise PackageError("Croissant snapshot differs from the exact regular-file inventory")
                missing.remove(entry.name)
        if missing:
            raise PackageError("Croissant snapshot differs from the exact regular-file inventory")
        size = 0
        for name, expected in inventory.items():
            digest = hashlib.sha256()
            with (snapshot / name).open("rb") as stream:
                while chunk := stream.read(min(1024 * 1024, budget - size + 1)):
                    size += len(chunk)
                    if size > budget:
                        raise PackageError("Croissant snapshot exceeds the caller's read budget")
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise PackageError("Croissant snapshot file differs from its reviewed digest")
    except OSError as exc:
        raise PackageError("Could not read the retained Croissant snapshot") from exc
    return size


def verify_corpus(
    package: Path, metadata: Path, snapshot: Path, *, expected_sha256: str,
    expected_metadata_sha256: str, max_bytes: int, policy: PackageReadPolicy | None = None,
) -> dict:
    """Check exact package, metadata, and local file identity without granting use."""
    if not all(isinstance(pin, str) and SHA256.fullmatch(pin)
               for pin in (expected_sha256, expected_metadata_sha256)):
        raise PackageError("Corpus readback requires independent package and Croissant metadata pins")
    if type(max_bytes) is not int or not 0 < max_bytes < sys.maxsize:
        raise PackageError("Corpus readback requires a positive byte budget")
    report = verify_package(package, expected_sha256=expected_sha256, policy=policy)
    agent = report.get("repositoryAgent", {})
    if agent.get("audience") != "private-evaluator":
        raise PackageError("Corpus readback requires a private-evaluator repository package")
    from .trajectory import BOUND_QUALIFICATION
    if not any(is_bound_corpus(item) and item["qualification"] == BOUND_QUALIFICATION
               and item["digest"] == "sha256:" + expected_metadata_sha256 for item in agent["references"]):
        raise PackageError("Repository package lacks the bound Croissant metadata digest")
    try:
        raw = bounded_read(metadata, max_bytes)
    except (PackageError, OSError) as exc:
        raise PackageError("Could not read retained Croissant metadata within the caller's budget") from exc
    if sha256(raw) != expected_metadata_sha256:
        raise PackageError("Croissant metadata differs from its independently reviewed digest")
    inventory = _inventory(raw)
    size = _verify_files(snapshot, inventory, max_bytes - len(raw))
    return {
        "schemaVersion": 1,
        "scope": "corpus-file-integrity-only",
        "packageSha256": report["packageSha256"],
        "metadataSha256": expected_metadata_sha256,
        "croissantSource": dict(CROISSANT_SOURCE),
        "fileCount": len(inventory),
        "snapshotSizeBytes": size,
        "corpusAdmission": "not-granted",
        "trainingEligibility": "not-granted",
    }
