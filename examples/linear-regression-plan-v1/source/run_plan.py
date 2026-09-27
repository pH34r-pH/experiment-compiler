#!/usr/bin/env python3
"""Run the plan's deterministic scientific calculation and write new result bytes."""
from __future__ import annotations

import sys
import importlib.util
import shutil
import tempfile
from pathlib import Path


def main() -> int:
    out = Path(sys.argv[1])
    inputs = [Path(path) for path in sys.argv[2:6]]
    with tempfile.TemporaryDirectory(prefix="linear-plan-inputs-") as directory:
        root = Path(directory)
        for source, name in zip(inputs[:3], ("data.csv", "experiment-config.json", "acceptance.json")):
            shutil.copyfile(source, root / name)
        spec = importlib.util.spec_from_file_location("run_experiment", inputs[3])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.scientific_result(root)
        result_bytes = module.canonical(result)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_bytes(result_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
