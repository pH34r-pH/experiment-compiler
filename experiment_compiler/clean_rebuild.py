"""Independent exact-tree package rebuilds; never execute packaged members."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .core import PackageError, json_value, safe_path, sha256_file, verify_package


def _source_tree(repository, commit, destination):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise PackageError("Clean rebuild requires an independently selected exact Git commit")
    archive_path = destination.parent / "source.tar"
    with archive_path.open("xb") as output:
        subprocess.run(["git", "-C", str(repository), "--no-replace-objects", "archive", "--format=tar", commit],
                       stdout=output, stderr=subprocess.PIPE, check=True, timeout=60)
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                raise PackageError("Clean build source contains a link or special file")
            safe_path(member.name.rstrip("/"))
        archive.extractall(destination, filter="data")


def clean_rebuild(repository, commit, recipe, package, expected_sha256):
    """Rebuild in a clean archived source tree and isolated interpreter environment."""
    recipe = safe_path(recipe)
    package = Path(package).resolve()
    checked = verify_package(package, expected_sha256=expected_sha256)
    program = (
        "import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
        "from experiment_compiler.core import compile_package;"
        "print(json.dumps(compile_package(Path(sys.argv[1])/sys.argv[2],Path(sys.argv[3]))))"
    )
    with tempfile.TemporaryDirectory(prefix="compiler-clean-rebuild-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        _source_tree(Path(repository), commit, source)
        home = root / "home"
        home.mkdir(mode=0o700)
        result = subprocess.run([sys.executable, "-I", "-c", program, str(source), recipe, str(root / "rebuilt.zip")],
                                cwd=root, env={"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"},
                                capture_output=True, check=True, timeout=120)
        rebuilt = json_value(result.stdout)
        if rebuilt["packageSha256"] != expected_sha256 or sha256_file(package) != expected_sha256:
            raise PackageError("Independent clean rebuild differs from the frozen candidate")
        return {"schemaVersion": 1, "scope": "exact-source-clean-rebuild", "status": "verified",
                "builderSourceCommit": commit, "recipe": recipe,
                "recipeSha256": rebuilt["build"]["recipeSha256"],
                "packageSha256": expected_sha256, "packageSizeBytes": checked["packageSizeBytes"],
                "environment": rebuilt["build"], "scientificReproduction": "not-run",
                "publicationAuthorized": False}
