"""Clean source reconstruction and native signature-verifier boundary tests."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.attestation import verify_attestation
from experiment_compiler.clean_rebuild import clean_rebuild
from experiment_compiler.core import PackageError, compile_package

ROOT = Path(__file__).resolve().parents[1]


class CleanRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "candidate.zip"
        self.digest = compile_package(ROOT / "examples/issue-164/experiment.json", self.package)["packageSha256"]
        self.commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()

    def test_exact_committed_source_rebuilds_without_package_execution(self):
        report = clean_rebuild(ROOT, self.commit, "examples/issue-164/experiment.json", self.package, self.digest)
        self.assertEqual(report["builderSourceCommit"], self.commit)
        self.assertEqual(report["packageSha256"], self.digest)
        self.assertEqual(report["scientificReproduction"], "not-run")
        self.assertFalse(report["publicationAuthorized"])
        with self.assertRaisesRegex(PackageError, "differs"):
            clean_rebuild(ROOT, self.commit, "examples/linear-regression-v1/experiment.json", self.package, self.digest)

    def test_wrong_source_or_candidate_identity_fails_before_promotion(self):
        for commit, digest in (("main", self.digest), (self.commit, "0" * 64)):
            with self.assertRaises(PackageError):
                clean_rebuild(ROOT, commit, "examples/issue-164/experiment.json", self.package, digest)

    def test_attestation_json_cannot_substitute_for_native_verification(self):
        output = self.root / "verification.json"
        arguments = {"repository": "pH34r-pH/experiment-compiler", "source_commit": self.commit,
            "signer_workflow": "pH34r-pH/experiment-compiler/.github/workflows/lifecycle.yml", "output": output}
        verifier = self.root / "gh"
        verifier.write_text("synthetic verifier path; subprocess is rejected")
        with patch("experiment_compiler.attestation.GH_EXECUTABLE", verifier), \
                patch("experiment_compiler.attestation.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["gh"])) as run, \
                self.assertRaisesRegex(PackageError, "verification failed"):
            verify_attestation(self.package, self.digest, **arguments)
        failure = json.loads(output.read_bytes())
        self.assertEqual(failure["status"], "failed")
        self.assertFalse(failure["publicationAuthorized"])
        command = run.call_args.args[0]
        for flag in ("--source-digest", "--source-ref", "--signer-workflow", "--signer-digest", "--deny-self-hosted-runners"):
            self.assertIn(flag, command)
