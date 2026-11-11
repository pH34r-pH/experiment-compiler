#!/usr/bin/env python3
"""Thin Actions entrypoint; inputs are data and are never interpolated into shell."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiment_compiler.core import PackageError, canonical, json_value
from experiment_compiler.lifecycle_components import component_command


def main():
    parameters = json_value(os.environ.get("COMPONENT_PARAMETERS", "{}").encode())
    stage = sys.argv[1]
    if stage == "publication":
        from experiment_compiler.attestation import verify_attestation
        required = {"package", "expected-sha256", "repository", "source-commit", "signer-workflow", "output"}
        if not isinstance(parameters, dict) or set(parameters) != required:
            raise PackageError("Publication verification parameters are incomplete")
        report = verify_attestation(parameters["package"], parameters["expected-sha256"],
            repository=parameters["repository"], source_commit=parameters["source-commit"],
            signer_workflow=parameters["signer-workflow"], output=parameters["output"])
        print(canonical(report).decode(), end="")
        return 0
    command = component_command(stage, parameters)
    os.environ["PYTHONPATH"] = str(ROOT)
    os.execv(sys.executable, command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PackageError, KeyError, ValueError) as exc:
        print("Lifecycle component rejected: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
