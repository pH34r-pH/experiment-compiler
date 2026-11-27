#!/usr/bin/env python3
"""Stage the exact five reviewed alpha assets from the lifecycle build."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
from pathlib import Path
from shutil import copyfile
import sys
import tomllib
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiment_compiler.core import PackageError, json_value, load_recipe, sha256_file, verify_package

PACKAGE = "issue-164-experiment-package.zip"
RECEIPTS = ("build-receipt.json", "verification.json")
RECEIPT_FIELDS = ("packageSha256", "packageSizeBytes", "manifestSha256", "status", "scope", "scientificReproduction")


def check_wheel(wheel: Path, distribution: str, version: str) -> None:
    with ZipFile(wheel) as archive:
        entry = f"{distribution.replace('-', '_')}-{version}.dist-info/METADATA"
        matches = [item for item in archive.infolist() if item.filename == entry]
        if len(matches) != 1 or matches[0].file_size > 64 * 1024:
            raise PackageError("Wheel must contain one bounded distribution metadata entry")
        metadata = BytesParser().parsebytes(archive.read(matches[0]))
    if (metadata.get_all("Name"), metadata.get_all("Version")) != ([distribution], [version]):
        raise PackageError("Wheel metadata differs from the exact source project and version")


def stage_assets(source: Path, destination: Path) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    distribution, version = project["name"], project["version"]
    wheel = f"{distribution.replace('-', '_')}-{version}-py3-none-any.whl"
    names = (PACKAGE, "SHA256SUMS", *RECEIPTS, wheel)
    for name in names:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise PackageError(f"Missing ordinary release asset {name}")
    fingerprints = {name: sha256_file(source / name) for name in names}

    recipe = load_recipe(ROOT / "examples/issue-164/experiment.json")
    digest = recipe["expectedPackage"]["sha256"]
    checked = verify_package(source / PACKAGE, recipe=recipe, expected_sha256=digest)
    if (source / "SHA256SUMS").read_bytes() != f"{digest}  dist/{PACKAGE}\n".encode():
        raise PackageError("Historical checksum asset differs from the lifecycle output")
    for name in RECEIPTS:
        receipt = json_value((source / name).read_bytes())
        if not isinstance(receipt, dict) or any(receipt.get(key) != checked[key] for key in RECEIPT_FIELDS):
            raise PackageError(f"Scoped package verification differs in {name}")
    check_wheel(source / wheel, distribution, version)

    destination.mkdir(parents=True)
    for name in names:
        staged = destination / name
        copyfile(source / name, staged)
        if sha256_file(staged) != fingerprints[name]:
            raise PackageError(f"Staged asset changed during copy: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        stage_assets(args.input, args.output)
    except (OSError, BadZipFile, PackageError) as error:
        parser.exit(1, f"Alpha asset staging failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
