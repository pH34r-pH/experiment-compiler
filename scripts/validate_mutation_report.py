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

    print(
        json.dumps(
            {
                "schemaVersion": report["schemaVersion"],
                "framework": report.get("framework", {}).get("name"),
                "mutants": total,
                "statuses": dict(sorted(statuses.items())),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
