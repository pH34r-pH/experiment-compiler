#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
from fractions import Fraction
from pathlib import Path

EXPERIMENT_ID = "stdlib-linear-regression-v1"


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frac(value: str) -> Fraction:
    return Fraction(value)


def fraction_json(value: Fraction) -> dict:
    return {"denominator": value.denominator, "numerator": value.numerator}


def load_rows(path: Path):
    rows = {"train": [], "eval": []}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["split", "x", "y"]:
            raise ValueError("data.csv must have split,x,y columns")
        for row in reader:
            split = row["split"]
            if split not in rows:
                raise ValueError("split must be train or eval")
            rows[split].append((Fraction(row["x"]), Fraction(row["y"])))
    if not rows["train"] or not rows["eval"]:
        raise ValueError("both train and eval splits are required")
    return rows


def gradients(weight: Fraction, bias: Fraction, rows):
    errors = [(weight * x + bias - y, x) for x, y in rows]
    scale = Fraction(2, len(rows))
    grad_weight = scale * sum((error * x for error, x in errors), Fraction())
    grad_bias = scale * sum((error for error, _ in errors), Fraction())
    return grad_weight, grad_bias


def mse(weight: Fraction, bias: Fraction, rows) -> Fraction:
    return sum(((weight * x + bias - y) ** 2 for x, y in rows), Fraction()) / len(rows)


def train(config: dict, rows):
    weight = frac(config["initialWeight"])
    bias = frac(config["initialBias"])
    learning_rate = frac(config["learningRate"])
    steps = config["steps"]
    if type(steps) is not int or steps < 1:
        raise ValueError("steps must be a positive integer")
    for _ in range(steps):
        grad_weight, grad_bias = gradients(weight, bias, rows["train"])
        weight -= learning_rate * grad_weight
        bias -= learning_rate * grad_bias
    return weight, bias


def scientific_result(root: Path) -> dict:
    rows = load_rows(root / "data.csv")
    config = json.loads((root / "experiment-config.json").read_text())
    acceptance = json.loads((root / "acceptance.json").read_text())
    weight, bias = train(config, rows)
    train_mse = mse(weight, bias, rows["train"])
    eval_mse = mse(weight, bias, rows["eval"])
    target_weight = frac(acceptance["targetWeight"])
    target_bias = frac(acceptance["targetBias"])
    parameter_limit = frac(acceptance["targetParameterAbsoluteErrorMaximum"])
    eval_limit = frac(acceptance["evalMseMaximum"])
    passed = (
        eval_mse <= eval_limit
        and abs(weight - target_weight) <= parameter_limit
        and abs(bias - target_bias) <= parameter_limit
    )
    return {
        "acceptancePassed": passed,
        "experimentId": EXPERIMENT_ID,
        "metrics": {
            "evalMse": fraction_json(eval_mse),
            "trainMse": fraction_json(train_mse),
        },
        "model": "y_hat = weight * x + bias",
        "parameters": {
            "bias": fraction_json(bias),
            "weight": fraction_json(weight),
        },
        "schemaVersion": 1,
        "training": {
            "arithmetic": "exact-rational",
            "learningRate": config["learningRate"],
            "steps": config["steps"],
        },
    }


def peak_rss_bytes():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, AttributeError):
        return None, "unavailable on this platform"
    if sys.platform == "darwin":
        return int(value), "resource.getrusage(RUSAGE_SELF).ru_maxrss (bytes on macOS)"
    return int(value) * 1024, "resource.getrusage(RUSAGE_SELF).ru_maxrss (KiB on Linux, normalized to bytes)"


def execute(root: Path, output: Path, receipt: Path, expected: Path) -> dict:
    result = scientific_result(root)
    expected_value = json.loads(expected.read_text())
    scientific_match = result == expected_value
    result_bytes = canonical(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result_bytes)
    peak, basis = peak_rss_bytes()
    tracked = [
        "data.csv", "experiment-config.json", "acceptance.json", "environment.json",
        "protocol.md", "run_experiment.py", "test_experiment.py", "reproduce.py",
        "expected-result.json",
    ]
    receipt_value = {
        "experimentId": EXPERIMENT_ID,
        "inputs": {name: sha256(root / name) for name in tracked},
        "referenceResultSha256": sha256(expected),
        "resultSha256": hashlib.sha256(result_bytes).hexdigest(),
        "resources": {
            "peakRssBytes": peak,
            "peakRssMeasurement": basis,
            "peakVramBytes": 0,
            "peakVramMeasurement": "CPU-only standard-library implementation; no accelerator runtime is used",
        },
        "runtime": {
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "pythonVersion": platform.python_version(),
        },
        "schemaVersion": 1,
        "scientificMatch": scientific_match,
        "scope": "bounded-independent-reproduction",
        "status": "passed" if result["acceptancePassed"] and scientific_match else "failed",
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(canonical(receipt_value))
    if receipt_value["status"] != "passed":
        raise SystemExit("reproduction failed acceptance or reference comparison")
    return receipt_value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args(argv)
    expected = args.expected or args.root / "expected-result.json"
    receipt = execute(args.root, args.output, args.receipt, expected)
    sys.stdout.buffer.write(canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
