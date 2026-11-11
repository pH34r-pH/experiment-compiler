"""Independent exact-tree package rebuilds; never execute packaged members."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .core import PackageError, json_value, safe_path, sha256_file, verify_package


def _git_context(destination):
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise PackageError("System Git is required for exact-source reconstruction")
    executable = str(Path(executable).resolve(strict=True))
    home = destination.parent / "git-home"
    home.mkdir(mode=0o700)
    environment = {"PATH": os.defpath, "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "", "LANG": "C.UTF-8"}
    command = [executable, "--no-pager", "--no-replace-objects", "-c", "credential.helper=",
        "-c", "protocol.allow=never", "-c", "core.hooksPath=" + os.devnull,
        "-c", "core.fsmonitor=false", "-c", "core.attributesFile=" + os.devnull]
    return command, environment


def _object_view(repository, commit, destination, command, environment):
    """Use standard Git alternates without importing the caller repository's config."""
    directory = repository.resolve()
    candidate = directory / ".git" if (directory / ".git").exists() else directory
    # --resolve-git-dir is a standalone plumbing mode: unlike repository setup,
    # it resolves a gitfile without loading that repository's configuration.
    resolved = subprocess.run([*command, "rev-parse", "--resolve-git-dir", str(candidate)],
        cwd=destination.parent, env=environment, capture_output=True, check=True, timeout=10).stdout.decode().strip()
    git_directory = Path(resolved)
    common = git_directory / "commondir"
    if common.is_file():
        git_directory = (git_directory / common.read_text().strip()).resolve(strict=True)
    objects = (git_directory / "objects").resolve(strict=True)
    if not objects.is_absolute() or any(char in str(objects) for char in "\r\n\x00"):
        raise PackageError("Source object database path is not a local absolute identity")
    view = destination.parent / "object-view.git"
    algorithm = "sha1" if len(commit) == 40 else "sha256"
    subprocess.run([*command, "init", "--quiet", "--bare", "--template=", "--object-format=" + algorithm, str(view)],
        env=environment, capture_output=True, check=True, timeout=10)
    (view / "objects/info/alternates").write_text(str(objects) + "\n")
    return [*command, "--git-dir=" + str(view)]


def _source_tree(repository, commit, destination):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise PackageError("Clean rebuild requires an independently selected exact Git commit")
    command, environment = _git_context(destination)
    archive_command = _object_view(repository, commit, destination, command, environment)
    source_type = subprocess.run([*archive_command, "cat-file", "-t", commit], env=environment,
        capture_output=True, check=True, timeout=10).stdout.strip()
    if source_type != b"commit":
        raise PackageError("Clean rebuild source must be a commit object")
    archive_path = destination.parent / "source.tar"
    with archive_path.open("xb") as output:
        subprocess.run([*archive_command, "archive", "--format=tar", commit],
                       env=environment, stdout=output, stderr=subprocess.PIPE, check=True, timeout=60)
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                raise PackageError("Clean build source contains a link or special file")
            safe_path(member.name.rstrip("/"))
        archive.extractall(destination, filter="data")
    for name in ("__init__.py", "core.py"):
        if not (destination / "experiment_compiler" / name).is_file():
            raise PackageError("Selected archive omits the compiler module; installed fallback is forbidden")
    return {"gitExecutableSha256": sha256_file(Path(command[0])), "archiveSha256": sha256_file(archive_path),
            "method": "Git archive with committed attributes and isolated object/config view"}


def clean_rebuild(repository, commit, recipe, package, expected_sha256):
    """Rebuild in a clean archived source tree and isolated interpreter environment."""
    recipe = safe_path(recipe)
    package = Path(package).resolve()
    checked = verify_package(package, expected_sha256=expected_sha256)
    program = (
        "import json,sys;from pathlib import Path;root=Path(sys.argv[1]);sys.path.insert(0,str(root));"
        "import experiment_compiler.core as core;"
        "assert Path(core.__file__).resolve()==root/'experiment_compiler/core.py','compiler source mismatch';"
        "print(json.dumps(core.compile_package(root/sys.argv[2],Path(sys.argv[3]))))"
    )
    with tempfile.TemporaryDirectory(prefix="compiler-clean-rebuild-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        source_receipt = _source_tree(Path(repository), commit, source)
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
                "compilerModuleSha256": sha256_file(source / "experiment_compiler/core.py"),
                "sourceReconstruction": source_receipt,
                "packageSha256": expected_sha256, "packageSizeBytes": checked["packageSizeBytes"],
                "environment": rebuilt["build"], "scientificReproduction": "not-run",
                "publicationAuthorized": False}
