"""Use the maintained GitHub CLI for signature verification; no local crypto engine."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .core import (
    PackageError,
    canonical,
    json_value,
    sha256_file,
    verify_package,
    write_once,
)

GH_EXECUTABLE = Path("/usr/bin/gh")  # Ubuntu publication worker tool, never caller-controlled PATH.


def _failure_receipt(output, digest, commit, reason):
    write_once(Path(output), canonical({"schemaVersion": 1, "scope": "build-provenance-verification",
        "status": "failed", "reason": reason, "packageSha256": digest,
        "builderSourceCommit": commit, "publicationAuthorized": False}))


def _verification_environment(root):
    home, config = root / "home", root / "gh-config"
    home.mkdir(mode=0o700)
    config.mkdir(mode=0o700)
    environment = {"PATH": os.defpath, "HOME": str(home), "GH_CONFIG_DIR": str(config),
        "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1", "LANG": "C.UTF-8", "NO_COLOR": "1"}
    if "GH_TOKEN" in os.environ:
        environment["GH_TOKEN"] = os.environ["GH_TOKEN"]
    return environment


def verify_attestation(package, expected_sha256, *, repository, source_commit, signer_workflow, output):
    """Verify signed provenance against independently supplied builder identities."""
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or not re.fullmatch(r"[0-9a-f]{40}", source_commit)
            or signer_workflow != repository + "/.github/workflows/lifecycle.yml"):
        raise PackageError("Attestation verification requires exact admitted repository/source/workflow")
    candidate = verify_package(Path(package), expected_sha256=expected_sha256)
    if candidate.get("repositoryAgent", {}).get("audience") == "private-evaluator":
        raise PackageError("Sealed evaluator packages cannot be publication candidates")
    executable = GH_EXECUTABLE
    if not executable.is_file() or executable.is_symlink():
        _failure_receipt(output, expected_sha256, source_commit, "verifier-unavailable")
        raise PackageError("Maintained GitHub CLI with attestation verification is required")
    command = [str(executable), "attestation", "verify", str(Path(package).resolve()), "--repo", repository,
               "--source-digest", source_commit, "--source-ref", "refs/heads/main",
               "--signer-workflow", signer_workflow, "--signer-digest", source_commit,
               "--deny-self-hosted-runners", "--hostname", "github.com", "--format", "json", "--limit", "10"]
    try:
        with tempfile.TemporaryDirectory(prefix="compiler-attestation-") as temporary:
            result = subprocess.run(command, env=_verification_environment(Path(temporary)),
                capture_output=True, check=True, timeout=120)
        verified = json_value(result.stdout)
        if not isinstance(verified, list) or not verified:
            raise PackageError("GitHub CLI returned no verified attestations")
    except (OSError, subprocess.SubprocessError, PackageError) as exc:
        _failure_receipt(output, expected_sha256, source_commit, "native-verification-failed")
        raise PackageError("Signed provenance verification failed; no publication is authorized") from exc
    if sha256_file(Path(package)) != expected_sha256:
        _failure_receipt(output, expected_sha256, source_commit, "candidate-changed")
        raise PackageError("Candidate changed during signature verification")
    # Retain native CLI output, not a worker-supplied assertion that it passed.
    write_once(Path(output), canonical({"schemaVersion": 1, "scope": "verified-build-provenance",
        "packageSha256": expected_sha256, "builderSourceCommit": source_commit,
        "signerWorkflow": signer_workflow, "verifierSha256": sha256_file(Path(executable)),
        "verification": verified, "publicationAuthorized": False}))
    return {"status": "signature-verified", "packageSha256": expected_sha256, "publicationAuthorized": False}
