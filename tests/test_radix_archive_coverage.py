"""Synthetic report fixtures for the public archived radix readback."""
import tempfile
import unittest
import zipfile
from pathlib import Path

from examples.radix_archive_coverage import GRID_AXES, report_archive, source_coverage
from experiment_compiler.core import PackageError


class RadixArchiveCoverageTests(unittest.TestCase):
    def _fixture(self):
        capture = {
            "model": "synthetic-model", "revision": "fixture-model-revision", "prompts": ["synthetic prompt"],
            "tokenizer": "synthetic-tokenizer", "tokenizer_revision": "fixture-tokenizer-revision",
            "modules": {"layers": [0], "suffixes": ["query", "output"]},
        }
        protocol = {
            "frozen_pairs": [{"id": f"pair-{index}"} for index in range(4)],
            "non_counting_calibration_pair": {"id": "calibration"},
            "source_sweep": {"aggregate_csv_sha256": "a" * 64, "product_csv_sha256": "b" * 64},
            "held_out_capture": capture,
            "bank_cost_grid": dict(zip(GRID_AXES, [list(range(n)) for n in (4, 3, 2, 3, 3, 2, 3)], strict=True)),
        }
        summary = {
            "capture_status": [{
                **capture, "status": "captured", "tokenizer_model": capture["tokenizer"],
                "modules": ["layer.0.query", "layer.0.output"], "trace_count": 2,
            }],
            "held_out_trace_count": 2,
            "pair_decisions": [
                {"pair_id": pair["id"], "configuration_count": 1296, "pair_validates": False}
                for pair in protocol["frozen_pairs"]
            ],
            "decision": "kill", "validated_pair_count": 0, "required_pair_count": 3,
        }
        return protocol, summary

    def test_observed_negative_results_preserve_the_separate_unreported_calibration(self):
        protocol, summary = self._fixture()
        report = source_coverage(protocol, summary, 2)
        self.assertEqual(report["sourceDecision"], {"decision": "kill", "validated": 0, "frozenPairs": 4, "required": 3})
        self.assertEqual([row["status"] for row in report["obligations"]], ["observed"] * 4 + ["unreported"])
        self.assertEqual(report["obligations"][-1]["role"], "diagnostic")
        self.assertEqual(report["screening"]["recoverability"], "checksum-only")
        self.assertEqual(report["sensitivityGrid"], {"settingsPerPair": 1296, "role": "configuration-grid"})
        self.assertEqual(report["heldOutCapture"]["traceShards"], 2)
        self.assertEqual(report["scientificReproduction"], "not-run")

    def test_changed_archive_is_rejected_before_reading_study_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unrelated.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("fixture.txt", "different bytes")
            with self.assertRaisesRegex(PackageError, "Package SHA-256 mismatch"):
                report_archive(path)

    def test_missing_or_duplicate_primary_results_fail_instead_of_filling_gaps(self):
        for keep_duplicate in (False, True):
            with self.subTest(keep_duplicate=keep_duplicate):
                protocol, summary = self._fixture()
                missing = summary["pair_decisions"].pop()
                if keep_duplicate:
                    summary["pair_decisions"].extend([missing, missing])
                with self.assertRaisesRegex(PackageError, "primary pair roster"):
                    source_coverage(protocol, summary, 2)

    def test_trace_count_and_model_identity_must_match_the_frozen_scope(self):
        protocol, summary = self._fixture()
        with self.assertRaisesRegex(PackageError, "trace count"):
            source_coverage(protocol, summary, 1)
        summary["capture_status"][0]["revision"] = "different-revision"
        with self.assertRaisesRegex(PackageError, "capture identities"):
            source_coverage(protocol, summary, 2)
