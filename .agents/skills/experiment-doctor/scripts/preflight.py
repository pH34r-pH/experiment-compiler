#!/usr/bin/env python3
"""Read-only Experiment Compiler preflight; optional compile/verify uses temp files only."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[4]
if (REPOSITORY / "experiment_compiler").is_dir():
    sys.path.insert(0, str(REPOSITORY))

try:
    from experiment_compiler.core import PackageError, json_value, load_recipe
except ImportError as error:  # Allow clear diagnostics when the package is absent.
    PackageError = Exception  # type: ignore[assignment,misc]
    json_value = None  # type: ignore[assignment]
    load_recipe = None  # type: ignore[assignment]
    IMPORT_ERROR = str(error)
else:
    IMPORT_ERROR = None


def _read_object(path: Path) -> dict | None:
    try:
        value = json_value(path.read_bytes())
    except (OSError, PackageError):
        return None
    return value if isinstance(value, dict) else None


def _source_path(root: Path, source: str) -> Path:
    relative = PurePosixPath(source)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"unsafe source path: {source}")
    path = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"source symlink is not permitted: {source}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"source escapes recipe directory: {source}")
    return path


def _summarize(recipe_path: Path, recipe: dict) -> tuple[dict, list[str]]:
    root = recipe_path.parent
    members = recipe["members"]
    listed = []
    errors = []
    metadata: dict[str, dict] = {}
    for member in members:
        source = member["source"]
        try:
            path = _source_path(root, source)
            present = path.is_file()
            readable = present and os.access(path, os.R_OK)
        except (OSError, ValueError) as error:
            present = False
            readable = False
            errors.append(str(error))
            path = root
        listed.append({"packagePath": member["path"], "source": source,
                       "present": present, "readable": readable})
        if not present or not readable:
            errors.append(f"declared source is missing or unreadable: {source}")
            continue
        key = member["path"].lower()
        if key.endswith("dependency-closure.json"):
            value = _read_object(path)
            if value is None:
                errors.append(f"dependency closure is not a readable JSON object: {source}")
            else:
                metadata["dependencyClosure"] = value
        elif key.endswith("environment.json") or key.endswith("runtime.json"):
            value = _read_object(path)
            if value is None:
                errors.append(f"runtime contract is not a readable JSON object: {source}")
            else:
                metadata.setdefault("runtime", {})[member["path"]] = value
        elif key.endswith("resource-requirements.json"):
            value = _read_object(path)
            if value is None:
                errors.append(f"resource requirements are not a readable JSON object: {source}")
            else:
                metadata.setdefault("resourceRequirements", {})[member["path"]] = value
    counts = {}
    closure = metadata.get("dependencyClosure")
    if isinstance(closure, dict) and isinstance(closure.get("classifications"), dict):
        counts = {name: len(items) if isinstance(items, list) else None
                  for name, items in closure["classifications"].items()}
    report = {
        "recipe": {"id": recipe["id"], "title": recipe["title"], "profile": recipe["profile"],
                   "memberCount": len(members), "expectedPackagePin": recipe.get("expectedPackage")},
        "members": listed,
        "dependencyClosure": {"classificationCounts": counts,
                              "unavailable": closure.get("classifications", {}).get("unavailable")
                              if isinstance(closure, dict) and isinstance(closure.get("classifications"), dict)
                              else None,
                              "runtimeNetworkRequired": closure.get("runtimeNetworkRequired")
                              if isinstance(closure, dict) else None},
        "runtime": metadata.get("runtime"),
        "resourceRequirements": metadata.get("resourceRequirements"),
    }
    return report, errors


def _compiler_check(recipe_path: Path) -> dict:
    if load_recipe is None:
        return {"status": "not_run", "reason": IMPORT_ERROR}
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPOSITORY) + (os.pathsep + current_pythonpath if current_pythonpath else "")
    result = {"status": "failed", "compile": None, "verify": None, "temporaryPackageRemoved": True}
    with tempfile.TemporaryDirectory(prefix="experiment-compiler-doctor-") as directory:
        package = Path(directory) / "preflight.zip"
        commands = [
            ([sys.executable, "-m", "experiment_compiler", "compile", str(recipe_path), "--output", str(package)], "compile"),
            ([sys.executable, "-m", "experiment_compiler", "verify", str(package), "--recipe", str(recipe_path)], "verify"),
        ]
        for command, name in commands:
            completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
            details = None
            if completed.returncode == 0:
                try:
                    details = json.loads(completed.stdout)
                except json.JSONDecodeError:
                    details = None
            result[name] = {"status": "passed" if completed.returncode == 0 else "failed",
                            "returnCode": completed.returncode}
            if isinstance(details, dict):
                for field in ("status", "scope", "packageSha256", "packageSizeBytes", "matchedExpectedPackage"):
                    if field in details:
                        result[name]["compilerStatus" if field == "status" else field] = details[field]
            elif completed.returncode != 0:
                result[name]["diagnostic"] = (completed.stderr or completed.stdout).strip()[-1200:]
            if completed.returncode != 0:
                break
        else:
            result["status"] = "passed"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path, help="recipe JSON to inspect")
    parser.add_argument("--compiler-check", action="store_true",
                        help="run existing compile and verify commands using a temporary package")
    args = parser.parse_args(argv)
    recipe_path = args.recipe.resolve()
    report: dict = {
        "status": "blocked",
        "environment": {"python": sys.version.split()[0], "supportedPython": sys.version_info >= (3, 11),
                        "compilerDistribution": None},
        "findings": [],
        "compilerCheck": {"status": "not_run", "reason": "pass --compiler-check to build and verify in a temporary directory"},
    }
    try:
        report["environment"]["compilerDistribution"] = importlib.metadata.version("experiment-compiler")
    except importlib.metadata.PackageNotFoundError:
        report["environment"]["compilerDistribution"] = "source checkout or module-only installation"
    if sys.version_info < (3, 11):
        report["findings"].append({"severity": "error", "message": "Python 3.11 or newer is required."})
    if load_recipe is None:
        report["findings"].append({"severity": "error", "message": f"Experiment Compiler is unavailable: {IMPORT_ERROR}"})
    else:
        try:
            recipe = load_recipe(recipe_path)
            details, errors = _summarize(recipe_path, recipe)
            report.update(details)
            report["findings"].extend({"severity": "error", "message": message} for message in errors)
            if args.compiler_check and not errors and sys.version_info >= (3, 11):
                report["compilerCheck"] = _compiler_check(recipe_path)
            elif args.compiler_check:
                report["compilerCheck"] = {"status": "not_run", "reason": "resolve preflight errors first"}
        except (PackageError, OSError, ValueError) as error:
            report["findings"].append({"severity": "error", "message": str(error)})
    if not any(item["severity"] == "error" for item in report["findings"]):
        if report["compilerCheck"]["status"] == "failed":
            report["status"] = "blocked"
        elif report["compilerCheck"]["status"] == "passed":
            report["status"] = "compiler-verified"
        else:
            report["status"] = "diagnostics-complete"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
