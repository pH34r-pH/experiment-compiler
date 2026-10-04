"""Offline injected-authority fixtures; no native process or physical qualification."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

import test_actions_projection as fixtures
from experiment_compiler.core import PackageError
from experiment_compiler.native_controller import run_native_controller


class FixtureAuthority:
    def __init__(self):
        self.events = []
        self.retained = []
        self.failure = None
        self.outcome = "succeeded"
        self.bad_ack = False
        self.bad_binding = False
        self.bad_evidence = False
        self.on_admit = None
        self.executed_requests = []

    @contextmanager
    def lease(self, request):
        self.events.append("admit")
        if self.failure == "admit":
            raise RuntimeError("fixture unavailable qualification")
        specification = json.loads(request.specification)
        self.request_sha256 = "f" * 64 if self.bad_binding else request.sha256
        self.qualification_evidence_sha256 = ("f" * 64 if self.bad_evidence
                                               else specification["qualificationEvidenceSha256"])
        if self.on_admit is not None:
            self.on_admit(request)
        try:
            yield self
        finally:
            self.events.append("release")

    def retain(self, receipt, *, expected_sha256):
        value = json.loads(receipt)
        self.events.append("retain-" + value["status"])
        if self.failure == "retain-" + value["status"]:
            raise OSError("fixture transport lost")
        self.retained.append(receipt)
        return "f" * 64 if self.bad_ack else hashlib.sha256(receipt).hexdigest()

    def execute(self, request):
        self.events.append("execute")
        self.executed_requests.append(request)
        if self.failure == "execute":
            raise OSError("fixture worker outcome unknown")
        return {"status": self.outcome, "endedAt": datetime.now(timezone.utc).isoformat(),
                "wallSeconds": 0.01, "exitCode": 0 if self.outcome == "succeeded" else 1,
                "collectionErrors": [], "resourceMeasurements": {"peakProcessMemoryBytes": None, "cpuSeconds": None}}


class NativeControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ActionsProjectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.profile["capabilities"] = {key: "qualified" for key in fixtures.CAPABILITIES}
        self.fixture.profile["qualificationEvidenceSha256"] = "b" * 64
        self.authority = FixtureAuthority()
        self.attempt = self.fixture.root / "fixture.attempt"
        self.controller = {"repository": "example/fleet", "workflow": ".github/workflows/native.yml",
                           "commit": "a" * 40, "runId": 1, "runAttempt": 1, "job": "native"}

    def run_fixture(self):
        package, profile, package_sha, profile_sha = self.fixture.build()
        return run_native_controller(package, profile, self.attempt,
                                     selection={"packageSha256": package_sha, "profileSha256": profile_sha,
                                                "runtimeSha256": "c" * 64, "attemptId": "d" * 12,
                                                "controller": self.controller}, authority=self.authority)

    def test_admit_retain_started_execute_retain_terminal_release_order(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                patch("socket.socket", side_effect=AssertionError("no network")):
            result = self.run_fixture()
        self.assertEqual(self.authority.events, ["admit", "retain-started", "execute", "retain-succeeded", "release"])
        self.assertEqual(result["status"], "succeeded")
        started, terminal = map(json.loads, self.authority.retained)
        self.assertEqual(terminal["startedReceiptSha256"], result["startedReceiptSha256"])
        self.assertEqual(started["requestSha256"], result["requestSha256"])
        self.assertEqual(started["controller"], self.controller)
        self.assertEqual(started["sourceRepository"], "pH34r-pH/experiment-compiler")
        self.assertIn("not established", result["packageOutcome"])

    def test_attempt_directory_reuse_cannot_execute_twice(self):
        self.run_fixture()
        with self.assertRaises(FileExistsError):
            self.run_fixture()
        self.assertEqual(self.authority.events.count("execute"), 1)
        self.assertEqual(len(self.authority.retained), 2)

    def test_request_bytes_survive_replacement_of_original_files(self):
        def replace_sources(request):
            (self.fixture.root / "plan.zip").write_bytes(b"replaced package")
            (self.fixture.root / "profile.json").write_bytes(b"replaced profile")
        self.authority.on_admit = replace_sources
        result = self.run_fixture()
        request = self.authority.executed_requests[0]
        specification = json.loads(request.specification)
        self.assertEqual(hashlib.sha256(request.package).hexdigest(), specification["identity"]["planPackageSha256"])
        self.assertEqual(hashlib.sha256(request.profile).hexdigest(), specification["identity"]["profileSha256"])
        self.assertEqual(request.sha256, result["requestSha256"])
        self.assertEqual((self.fixture.root / "plan.zip").read_bytes(), b"replaced package")


    def test_profile_assertion_cannot_replace_authority_admission(self):
        self.authority.failure = "admit"
        with self.assertRaises(RuntimeError):
            self.run_fixture()
        self.assertFalse(self.attempt.exists())
        self.assertNotIn("execute", self.authority.events)

    def test_unknown_capability_or_wrong_controller_blocks_before_authority(self):
        self.fixture.profile["capabilities"]["networkIsolation"] = "unknown"
        with self.assertRaises(PackageError):
            self.run_fixture()
        self.assertEqual(self.authority.events, [])
        self.fixture.profile["capabilities"]["networkIsolation"] = "qualified"
        self.controller["commit"] = "f" * 40
        with self.assertRaises(PackageError):
            self.run_fixture()
        self.assertEqual(self.authority.events, [])

    def test_lease_must_bind_exact_request(self):
        self.authority.bad_binding = True
        with self.assertRaises(PackageError):
            self.run_fixture()
        self.assertFalse(self.attempt.exists())
        self.assertNotIn("execute", self.authority.events)

    def test_lease_must_bind_exact_qualification_evidence(self):
        self.authority.bad_evidence = True
        with self.assertRaises(PackageError):
            self.run_fixture()
        self.assertFalse(self.attempt.exists())
        self.assertNotIn("execute", self.authority.events)


    def test_started_retention_ack_is_required_before_execution(self):
        self.authority.bad_ack = True
        with self.assertRaises(PackageError):
            self.run_fixture()
        self.assertTrue((self.attempt / "started-receipt.json").exists())
        self.assertFalse((self.attempt / "terminal-receipt.json").exists())
        self.assertNotIn("execute", self.authority.events)

    def test_worker_loss_preserves_started_without_fabricated_terminal(self):
        self.authority.failure = "execute"
        with self.assertRaises(OSError):
            self.run_fixture()
        self.assertTrue((self.attempt / "started-receipt.json").exists())
        self.assertFalse((self.attempt / "terminal-receipt.json").exists())
        self.assertEqual(self.authority.events[-1], "release")

    def test_malformed_observation_preserves_started(self):
        self.authority.outcome = "unknown"
        with self.assertRaises(PackageError):
            self.run_fixture()
        self.assertFalse((self.attempt / "terminal-receipt.json").exists())
        self.assertEqual(len(self.authority.retained), 1)

    def test_terminal_transport_loss_preserves_observed_terminal(self):
        self.authority.failure = "retain-succeeded"
        with self.assertRaises(OSError):
            self.run_fixture()
        self.assertEqual(json.loads((self.attempt / "terminal-receipt.json").read_bytes())["status"], "succeeded")
        self.assertEqual(len(self.authority.retained), 1)

    def test_failure_timeout_and_cancel_remain_distinct(self):
        for status in ("failed", "timed-out", "cancelled"):
            self.authority.outcome = status
            self.attempt = self.attempt.parent / status
            self.assertEqual(self.run_fixture()["status"], status)


if __name__ == "__main__":
    unittest.main()
