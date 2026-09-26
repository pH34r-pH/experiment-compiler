#!/usr/bin/env python3
"""Hermetic-ish local receipt runner for the #164 AdamW conformance contract.

The runner does not install dependencies and does not contact the network.
It records the exact test/contract bytes, interpreter/Torch/platform identity,
command, exit status, stdout/stderr, and a canonical receipt hash.

This is an execution receipt, not a claim that arbitrary host state is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
TEST = ROOT / "tests" / "test_issue_164_adamw_contract.py"
CONTRACT = ROOT / "research" / "reproducibility" / "issue_164" / "adamw_contract_v1.md"
RECEIPT = ROOT / "research" / "reproducibility" / "issue_164" / "adamw_contract_receipt.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: list[str]) -> str:
    """Return git metadata when available without making receipt execution depend on it."""
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def main() -> int:
    import torch

    command = [sys.executable, "-m", "pytest", "-q", str(TEST.relative_to(ROOT))]
    env = os.environ.copy()
    env.update({
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    })
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    receipt = {
        "schema_version": "dsl-local-test-receipt/v1",
        "contract_id": "dsl.issue164.adamw-update/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": {
            "head_sha": _git(["rev-parse", "HEAD"]),
            "branch": _git(["branch", "--show-current"]),
            "status_porcelain": _git(["status", "--porcelain"]),
            "remote_origin": _git(["remote", "get-url", "origin"]),
        },
        "inputs": {
            "contract": {
                "path": str(CONTRACT.relative_to(ROOT)),
                "sha256": sha256(CONTRACT),
            },
            "test": {
                "path": str(TEST.relative_to(ROOT)),
                "sha256": sha256(TEST),
            },
        },
        "execution": {
            "command": command,
            "cwd": ".",
            "environment_overrides": {
                "PYTHONHASHSEED": "0",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
            },
            "python": sys.version,
            "python_executable": sys.executable,
            "torch": torch.__version__,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cuda_available": torch.cuda.is_available(),
            "cuda_version": torch.version.cuda,
        },
        "result": {
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    }
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    receipt["receipt_sha256_without_self"] = hashlib.sha256(canonical).hexdigest()
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(RECEIPT.relative_to(ROOT))
    print(receipt["receipt_sha256_without_self"])
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
