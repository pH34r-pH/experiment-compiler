#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("reproduction"))
    parser.add_argument("--implementation", type=Path, required=True)
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--expected-result", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linear-regression-source-") as temp_dir:
        root = Path(temp_dir)
        sources = {
            "run_experiment.py": args.implementation,
            "test_experiment.py": args.tests,
            "data.csv": args.data,
            "experiment-config.json": args.configuration,
            "acceptance.json": args.acceptance,
            "expected-result.json": args.expected_result,
        }
        for name, source in sources.items():
            shutil.copyfile(source, root / name)
        sys.path.insert(0, str(root))
        import run_experiment

        suite = unittest.defaultTestLoader.discover(str(root), pattern="test_experiment.py")
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
        if not outcome.wasSuccessful():
            return 1
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
