from __future__ import annotations

import json
import unittest
from fractions import Fraction
from pathlib import Path

import run_experiment

ROOT = Path(__file__).resolve().parent


class ExperimentTests(unittest.TestCase):
    def test_declared_split_is_complete(self):
        rows = run_experiment.load_rows(ROOT / "data.csv")
        self.assertEqual(rows["train"], [(Fraction(-1), Fraction(-1)), (Fraction(1), Fraction(3))])
        self.assertEqual(rows["eval"], [(Fraction(0), Fraction(1)), (Fraction(2), Fraction(5))])

    def test_first_gradient_update(self):
        rows = run_experiment.load_rows(ROOT / "data.csv")
        grad_weight, grad_bias = run_experiment.gradients(Fraction(0), Fraction(0), rows["train"])
        self.assertEqual((grad_weight, grad_bias), (Fraction(-4), Fraction(-2)))
        learning_rate = Fraction(1, 4)
        self.assertEqual(Fraction(0) - learning_rate * grad_weight, Fraction(1))
        self.assertEqual(Fraction(0) - learning_rate * grad_bias, Fraction(1, 2))

    def test_frozen_32_step_result_and_acceptance(self):
        result = run_experiment.scientific_result(ROOT)
        self.assertEqual(result["parameters"]["weight"], {"numerator": 4294967295, "denominator": 2147483648})
        self.assertEqual(result["parameters"]["bias"], {"numerator": 4294967295, "denominator": 4294967296})
        self.assertEqual(result["metrics"]["trainMse"], {"numerator": 5, "denominator": 18446744073709551616})
        self.assertEqual(result["metrics"]["evalMse"], {"numerator": 13, "denominator": 18446744073709551616})
        self.assertTrue(result["acceptancePassed"])

    def test_expected_result_is_exact(self):
        expected = json.loads((ROOT / "expected-result.json").read_text())
        self.assertEqual(run_experiment.scientific_result(ROOT), expected)


if __name__ == "__main__":
    unittest.main()
