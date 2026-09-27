"""Command-line wrapper for the explicit experiment-runner handoff."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import PackageError, canonical, write_once
from .runner import run_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment-runner")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a digest-pinned lifecycle package through its declared CWL workflow")
    run.add_argument("package", type=Path)
    run.add_argument("--output", type=Path, required=True,
                     help="new immutable result ZIP; writes a sibling .source/ folder for verify/catalog/promotion")
    run.add_argument("--expected-sha256", required=True, help="exact reviewed plan package digest")
    run.add_argument("--allow-workflow-execution", action="store_true",
                     help="confirm that the packaged CWL workflow and its tool code may execute")
    args = parser.parse_args(argv)
    try:
        result = run_package(args.package, args.output, expected_sha256=args.expected_sha256,
                             allow_workflow_execution=args.allow_workflow_execution)
        sys.stdout.buffer.write(canonical(result))
        return 0 if result["executionStatus"] == "succeeded" else 2
    except (PackageError, OSError) as exc:
        print(f"experiment-runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
