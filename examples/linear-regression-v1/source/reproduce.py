#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

import run_experiment


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("reproduction"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent

    suite = unittest.defaultTestLoader.discover(str(root), pattern="test_experiment.py")
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    if not outcome.wasSuccessful():
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    receipt = run_experiment.execute(
        root,
        args.output_dir / "result.json",
        args.output_dir / "reproduction-receipt.json",
        root / "expected-result.json",
    )
    print(json.dumps({
        "result": str(args.output_dir / "result.json"),
        "receipt": str(args.output_dir / "reproduction-receipt.json"),
        "scientificMatch": receipt["scientificMatch"],
        "status": receipt["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
