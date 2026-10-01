"""Bounded checks for changed living documentation and tracked artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

LIVING_DOC_EXCEPTIONS = {"README.md", "AGENTS.md"}
ROOT_LIVING_DOCS = {"README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md"}
HISTORICAL_DOCS = {"ORIGINS.md", "docs/poc-scope.md"}
SUSPICIOUS_PARTS = {
    ".cache", ".pytest_cache", ".venv", "__pycache__", "build", "cache",
    "dist", "node_modules", "quality-audit", "temp", "tmp", "_site",
}
FORBIDDEN_NAMES = {".DS_Store", "Thumbs.db", ".jupyterlite.doit.db"}
FORBIDDEN_SUFFIXES = {".bak", ".log", ".pyc", ".pyo", ".tmp"}
BAD_LIVING_STEMS = {"draft", "new", "notes", "tmp", "temp", "untitled"}
DESCRIPTIVE_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SEQUENCED_NAME = re.compile(r"^\d+-[a-z0-9]+(?:-[a-z0-9]+)*$")
CONVENTIONAL_NAME = re.compile(r"^(?:adr|spec)-?\d+$", re.IGNORECASE)


def _relative(path: str | Path, root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve().relative_to(root.resolve())
    return candidate


def is_historical_doc(path: str | Path) -> bool:
    relative = Path(path).as_posix()
    if Path(relative).name == "AGENTS.md":
        return False
    return relative in HISTORICAL_DOCS or relative.startswith("examples/")


def is_living_doc(path: Path) -> bool:
    if path.suffix.lower() not in {".md", ".markdown"}:
        return False
    if path.name in LIVING_DOC_EXCEPTIONS:
        return True
    if is_historical_doc(path):
        return False
    if not path.parts:
        return False
    return path.as_posix() in ROOT_LIVING_DOCS or path.parts[0] in {"docs", "scripts", "site"}


def _is_new_living_doc(path: Path, new_paths: set[str]) -> bool:
    return path.as_posix() in new_paths and is_living_doc(path)


def _check_doc_name(path: Path, new_paths: set[str]) -> list[str]:
    if not _is_new_living_doc(path, new_paths) or path.name in LIVING_DOC_EXCEPTIONS:
        return []
    stem = path.stem.lower()
    if stem in BAD_LIVING_STEMS or re.fullmatch(r"(?:issue-)?\d+", stem):
        return [f"new living document needs a descriptive name: {path}"]
    if (not DESCRIPTIVE_SLUG.fullmatch(stem) and
            not SEQUENCED_NAME.fullmatch(stem) and
            not CONVENTIONAL_NAME.fullmatch(path.stem)):
        return [f"new living document needs a descriptive name: {path}"]
    return []


def _check_artifact(path: Path) -> list[str]:
    if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return [f"new or changed path is an unapproved temporary/build artifact: {path}"]
    if any(part in SUSPICIOUS_PARTS for part in path.parts):
        return [f"new or changed path is an unapproved temporary/build artifact: {path}"]
    return []


def check_paths(root: Path, paths: list[str | Path], new_paths: set[str] | None = None) -> list[str]:
    new_paths = new_paths or set()
    errors: list[str] = []
    for raw_path in paths:
        path = _relative(raw_path, root)
        errors.extend(_check_artifact(path))
        errors.extend(_check_doc_name(path, new_paths))
    return errors


def changed_files(root: Path, since: str) -> tuple[list[str], set[str]]:
    command = ["git", "diff", "--name-status", "-z", "--find-renames", "--find-copies",
               "--diff-filter=ACMR", f"{since}...HEAD"]
    fields = subprocess.check_output(command, cwd=root).decode().split("\0")
    records: list[tuple[str, str]] = []
    index = 0
    while index < len(fields) - 1:
        status = fields[index]
        index += 1
        if not status:
            continue
        if status.startswith(("R", "C")):
            index += 1
            records.append((status[0], fields[index]))
        else:
            records.append((status[0], fields[index]))
        index += 1
    return [path for _, path in records], {path for status, path in records if status in {"A", "C", "R"}}


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--changed-since")
    source.add_argument("--paths", nargs="+")
    parser.add_argument("--list-living-docs", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.paths is not None:
        paths, new_paths = args.paths, set(args.paths)
    else:
        paths, new_paths = changed_files(root, args.changed_since)
    if args.list_living_docs:
        selected = [path for path in paths if is_living_doc(Path(path))]
        if selected:
            print("\n".join(selected))
        return 0
    errors = check_paths(root, paths, new_paths)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Documentation/artifact hygiene passed for {len(paths)} changed path(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
