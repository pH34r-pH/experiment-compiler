#!/usr/bin/env python3
"""Validate a Mutation Testing Elements report against the pinned upstream schema."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "mutation-testing-report-schema-3.9.0.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--require-mutants", action="store_true")
    parser.add_argument("--require-file", action="append", default=[], metavar="REPO_PATH",
                        help="require mutants for this exact repository-relative path (repeatable)")
    args = parser.parse_args()

    schema = json.loads(SCHEMA.read_text())
    report = json.loads(args.report.read_text())

    Draft7Validator.check_schema(schema)
    Draft7Validator(schema).validate(report)

    statuses = Counter(
        mutant["status"]
        for file_result in report["files"].values()
        for mutant in file_result["mutants"]
    )
    total = sum(statuses.values())
    if args.require_mutants and total == 0:
        raise SystemExit("mutation report is schema-valid but contains no mutants")

    required_counts = {
        filename: len(report["files"].get(filename, {}).get("mutants", []))
        for filename in args.require_file
    }
    for filename, count in required_counts.items():
        if count == 0:
            raise SystemExit(f"mutation report contains no mutants for {filename}")

    print(
        json.dumps(
            {
                "schemaVersion": report["schemaVersion"],
                "framework": report.get("framework", {}).get("name"),
                "mutants": total,
                "statuses": dict(sorted(statuses.items())),
                **({"requiredFileMutants": required_counts} if required_counts else {}),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
