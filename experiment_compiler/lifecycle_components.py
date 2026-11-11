"""Argument-safe reusable projections onto the existing lifecycle CLIs."""
from __future__ import annotations

import sys
from pathlib import Path

from .core import PackageError

STAGES = {
    "compile": ("experiment_compiler", "compile", "recipe", {"output", "receipt"}, set()),
    "verify": ("experiment_compiler", "verify", "package", {"recipe", "receipt", "expected-sha256"}, set()),
    "execute": ("experiment_compiler.runner_cli", "run", "package", {"output", "expected-sha256"},
                {"output", "expected-sha256"}),
    "finalize": ("experiment_compiler", "finalize", "parent-package", {
        "expected-sha256", "attempt-id", "id", "title", "decision", "decision-summary", "review",
        "review-summary", "reviewer-name", "output"}, {
        "expected-sha256", "attempt-id", "id", "title", "decision", "decision-summary", "review",
        "review-summary", "reviewer-name", "output"}),
}

BOOTSTRAP = ("import runpy,sys;root=sys.argv.pop(1);module=sys.argv.pop(1);"
             "sys.path.insert(0,root);sys.argv[0]=module;runpy.run_module(module,run_name='__main__')")


def _cli_value(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PackageError("Lifecycle CLI values must be nonempty strings")
    return value


def _execution_flag(parameters):
    admitted = parameters.get("allow-workflow-execution", False)
    if type(admitted) is not bool:
        raise PackageError("Execution opt-in must be an explicit boolean")
    return ["--allow-workflow-execution"] if admitted else []


def component_command(stage, parameters):
    if stage not in STAGES or not isinstance(parameters, dict):
        raise PackageError("Unsupported lifecycle component or parameter object")
    module, operation, positional, optional, required = STAGES[stage]
    flags = {"allow-workflow-execution"} if stage == "execute" else set()
    if (set(parameters) - optional - flags - {positional}
            or not required | {positional} <= set(parameters)):
        raise PackageError("Lifecycle component parameters are incomplete or unsupported")
    arguments = [sys.executable, "-I", "-c", BOOTSTRAP, str(Path(__file__).resolve().parents[1]), module, operation]
    for key in sorted(optional & parameters.keys()):
        arguments.append("--" + key + "=" + _cli_value(parameters[key]))
    if flags:
        arguments.extend(_execution_flag(parameters))
    return arguments + ["--", _cli_value(parameters[positional])]
