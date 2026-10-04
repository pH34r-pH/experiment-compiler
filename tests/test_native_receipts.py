"""Offline receipt fixtures only; no physical native-worker claims."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment_compiler.core import PackageError
from experiment_compiler.native_receipts import (
    finish_native_attempt, start_native_attempt, validate_native_receipt,
)


class NativeReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "fixture.attempt"
        self.identity = {"attemptId": "a" * 12, "sourceCommit": "a" * 40, "sourceRepository": "example/source",
                         **{key: "b" * 64 for key in ("planPackageSha256", "profileSha256",
                            "runtimeSha256", "stagedAssetsSha256", "requestSha256")},
                         "controller": {"repository": "example/control", "commit": "c" * 40,
                                        "workflow": ".github/workflows/native.yml",
                                        "runId": 1, "runAttempt": 1, "job": "native"}}
        self.observations = {"status": "succeeded", "endedAt": "2026-10-04T00:00:01+00:00",
                             "wallSeconds": 1.0, "exitCode": 0, "collectionErrors": [],
                             "resourceMeasurements": {"peakProcessMemoryBytes": None, "cpuSeconds": None}}

    def start(self):
        return start_native_attempt(self.directory, self.identity, started_at="2026-10-04T00:00:00+00:00")

    def finish(self, digest):
        return finish_native_attempt(self.directory, self.observations, expected_started_sha256=digest)

    def test_started_before_terminal_and_loss_remains_unknown(self):
        digest = self.start()
        raw = (self.directory / "started-receipt.json").read_bytes()
        receipt = json.loads(raw)
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(validate_native_receipt(receipt), "started")
        self.assertIsNone(receipt["exitCode"])
        self.assertFalse((self.directory / "terminal-receipt.json").exists())
        self.assertEqual(raw, (self.directory / "runner-receipt.json").read_bytes())
        with self.assertRaises(FileExistsError):
            self.start()

    def test_all_terminal_outcomes_and_immutable_identity(self):
        for index, status in enumerate(("succeeded", "failed", "timed-out", "cancelled")):
            with self.subTest(status=status):
                self.directory = self.directory.parent / f"attempt-{index}"
                digest = self.start()
                original = (self.directory / "started-receipt.json").read_bytes()
                self.observations.update(status=status, exitCode=0 if status == "succeeded" else 1)
                terminal = self.finish(digest)
                raw = (self.directory / "terminal-receipt.json").read_bytes()
                value = json.loads(raw)
                self.assertEqual(value["startedReceiptSha256"], digest)
                self.assertEqual(value["controller"], self.identity["controller"])
                self.assertEqual(terminal, hashlib.sha256(raw).hexdigest())
                self.assertEqual(original, (self.directory / "started-receipt.json").read_bytes())
                with self.assertRaises(PackageError):
                    self.finish(digest)

    def test_cleanup_exit_zero_does_not_mask_terminal_outcome(self):
        for status in ("cancelled", "timed-out", "failed"):
            self.directory = self.directory.parent / status
            digest = self.start()
            self.observations.update(status=status, exitCode=0,
                                     collectionErrors=["output rejected"] if status == "failed" else [])
            self.finish(digest)
            receipt = json.loads((self.directory / "terminal-receipt.json").read_bytes())
            self.assertEqual(receipt["status"], status)


    def test_missing_start_wrong_digest_and_identity_rewrite_rejected(self):
        with self.assertRaises(PackageError):
            self.finish("a" * 64)
        digest = self.start()
        with self.assertRaises(PackageError):
            self.finish("a" * 64)
        self.observations["profileSha256"] = "c" * 64
        with self.assertRaises(PackageError):
            self.finish(digest)

    def test_contradictory_unmeasured_and_hostile_values(self):
        digest = self.start()
        cases = [{"status": "lost"}, {"status": []}, {"endedAt": "2026-10-03T00:00:00Z"},
                 {"wallSeconds": float("nan")}, {"wallSeconds": 10**500}, {"exitCode": True},
                 {"status": "failed"}, {"collectionErrors": ["rejected output"]},
                 {"resourceMeasurements": {"peakProcessMemoryBytes": -1, "cpuSeconds": None}},
                 {"resourceMeasurements": {"peakProcessMemoryBytes": 1.5, "cpuSeconds": None}}]
        for update in cases:
            saved = copy.deepcopy(self.observations)
            self.observations.update(update)
            with self.subTest(update=str(update)[:100]), self.assertRaises((PackageError, ValueError)):
                self.finish(digest)
            self.observations = saved
        self.assertFalse((self.directory / "terminal-receipt.json").exists())

    def test_rejected_terminal_cannot_change_latest_receipt(self):
        digest = self.start()
        before = (self.directory / "runner-receipt.json").read_bytes()
        self.observations["status"] = "started"
        with self.assertRaises(PackageError):
            self.finish(digest)
        self.assertEqual(before, (self.directory / "runner-receipt.json").read_bytes())

    def test_persistence_failure_retains_snapshot(self):
        with patch("experiment_compiler.native_receipts._persist_attempt_receipt", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.start()
        self.assertEqual(json.loads((self.directory / "started-receipt.json").read_bytes())["status"], "started")

    def test_unexpected_store_members_cannot_be_overwritten(self):
        digest = self.start()
        outside = self.directory.parent / "outside"
        outside.write_text("unchanged")
        pending = self.directory / "runner-receipt.json.tmp"
        pending.symlink_to(outside)
        with self.assertRaises(PackageError):
            self.finish(digest)
        self.assertEqual(outside.read_text(), "unchanged")
        pending.unlink()
        latest = self.directory / "runner-receipt.json"
        latest.write_text("tampered")
        with self.assertRaises(PackageError):
            self.finish(digest)


    def test_symlink_and_unqualified_platform_rejected(self):
        root = self.directory.parent
        link = root / "link"
        link.symlink_to(root, target_is_directory=True)
        self.directory = link / "attempt"
        with self.assertRaises(PackageError):
            self.start()
        self.directory = root / "attempt"
        with patch("experiment_compiler.native_receipts.os.name", "nt"), self.assertRaises(PackageError):
            self.start()


if __name__ == "__main__":
    unittest.main()
