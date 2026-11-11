"""Clean source reconstruction and native signature-verifier boundary tests."""
import json
import os
import shlex
import shutil
import subprocess
import sys
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
        tree = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"], text=True).strip()
        for commit, digest in (("main", self.digest), (self.commit, "0" * 64), (tree, self.digest)):
            with self.assertRaises(PackageError):
                clean_rebuild(ROOT, commit, "examples/issue-164/experiment.json", self.package, digest)

    def test_path_git_shim_and_caller_git_config_cannot_replace_the_source_archive(self):
        git = shutil.which("git", path=os.defpath)
        repository = self.root / "source-clone"
        subprocess.run([git, "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(repository)], check=True)
        marker = self.root / "shim-executed"
        shim_directory = self.root / "shim"
        shim_directory.mkdir()
        shim = shim_directory / "git"
        shim.write_text("#!/bin/sh\nprintf attacked > " + shlex.quote(str(marker)) + "\nexit 1\n")
        shim.chmod(0o755)
        subprocess.run([git, "-C", str(repository), "config", "tar.tar.command", str(shim)], check=True)
        # Even malformed local config must remain outside the source-reconstruction boundary.
        with (repository / ".git/config").open("a") as stream:
            stream.write("\n[unterminated\n")
        poison = {"PATH": str(shim_directory), "GIT_CONFIG_GLOBAL": str(self.root / "not-a-config"),
                  "GIT_DIR": str(self.root / "not-a-git-directory"), "GIT_CONFIG_PARAMETERS": "not valid config"}
        with patch.dict(os.environ, poison):
            receipt = clean_rebuild(repository, self.commit, "examples/issue-164/experiment.json", self.package, self.digest)
        self.assertFalse(marker.exists())
        self.assertEqual(receipt["packageSha256"], self.digest)

    def test_missing_and_export_ignored_compiler_cannot_fall_back_to_installed_code(self):
        git = shutil.which("git", path=os.defpath)
        for mode in ("missing", "export-ignore"):
            with self.subTest(mode=mode):
                source = self.root / mode
                source.mkdir()
                subprocess.run([git, "init", "--quiet", str(source)], check=True)
                (source / "README.md").write_text("Selected source fixture\n")
                if mode == "export-ignore":
                    package = source / "experiment_compiler"
                    package.mkdir()
                    (package / "__init__.py").write_text("")
                    (package / "core.py").write_text("raise RuntimeError('must not execute')\n")
                    (source / ".gitattributes").write_text("experiment_compiler export-ignore\n")
                subprocess.run([git, "-C", str(source), "add", "."], check=True)
                subprocess.run([git, "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                "commit", "--quiet", "-m", "Fixture"], check=True)
                commit = subprocess.check_output([git, "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
                with patch("experiment_compiler.clean_rebuild.subprocess.run", wraps=subprocess.run) as run, \
                        self.assertRaisesRegex(PackageError, "omits the compiler module"):
                    clean_rebuild(source, commit, "unused.json", self.package, self.digest)
                self.assertFalse(any(call.args[0][0] == sys.executable for call in run.call_args_list))

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

    def test_poisoned_gh_host_home_and_config_cannot_redefine_verification_context(self):
        verifier = self.root / "gh"
        verifier.write_text("synthetic verifier path; process is inspected and rejected")
        poisoned = self.root / "poisoned-config"
        poisoned.mkdir()
        (poisoned / "hosts.yml").write_text("evil.invalid: {git_protocol: ssh, oauth_token: fixture-token}\n")
        environment = {"GH_HOST": "evil.invalid", "GH_CONFIG_DIR": str(poisoned), "HOME": str(poisoned),
            "GH_TOKEN": "fixture-job-token", "GH_ENTERPRISE_TOKEN": "fixture-enterprise-token", "GH_DEBUG": "api"}

        def reject(command, **kwargs):
            actual = kwargs["env"]
            self.assertEqual(actual["GH_HOST"], "github.com")
            self.assertEqual(command[command.index("--hostname") + 1], "github.com")
            self.assertEqual(actual["GH_TOKEN"], "fixture-job-token")
            self.assertNotIn("GH_ENTERPRISE_TOKEN", actual)
            self.assertNotIn("GH_DEBUG", actual)
            self.assertNotEqual(actual["HOME"], str(poisoned))
            self.assertEqual(list(Path(actual["GH_CONFIG_DIR"]).iterdir()), [])
            raise subprocess.CalledProcessError(1, command)

        with patch.dict(os.environ, environment), patch("experiment_compiler.attestation.GH_EXECUTABLE", verifier), \
                patch("experiment_compiler.attestation.subprocess.run", side_effect=reject), \
                self.assertRaises(PackageError):
            verify_attestation(self.package, self.digest, repository="pH34r-pH/experiment-compiler",
                source_commit=self.commit, signer_workflow="pH34r-pH/experiment-compiler/.github/workflows/lifecycle.yml",
                output=self.root / "rejected.json")
