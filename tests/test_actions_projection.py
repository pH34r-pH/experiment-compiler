"""Offline generic native handoff fixtures; no vessel or workload qualification."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from experiment_compiler.actions_projection import project_actions
from experiment_compiler.core import PackageError, canonical, compile_package

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = ("foregroundExclusion", "processTreeCleanup", "resourceEnforcement",
                "networkIsolation", "candidateCredentialIsolation", "assetVerification",
                "startedTerminalRetention", "lossRecovery")


class ActionsProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        shutil.copytree(ROOT / "examples/linear-regression-plan-v1", self.source)
        self.profile = {
            "schemaVersion": 1, "id": "fixture-cpu", "executionMode": "fleet-native-v1",
            "platform": {"os": "linux", "arch": "x86_64"},
            "workflow": "example/fleet/.github/workflows/native.yml@" + "a" * 40,
            "limits": {"cores": 1, "ramMiB": 64, "tmpdirMiB": 64, "outdirMiB": 64, "wallSeconds": 120},
            "capabilities": {key: "unknown" for key in CAPABILITIES},
            "qualificationEvidenceSha256": None, "stagedAssets": [],
        }
        self.workflow = yaml.safe_load((self.source / "source/workflow.cwl").read_text())
        del self.workflow["requirements"]["DockerRequirement"]
        self.runner = {"runner": "fleet", "executionMode": "fleet-native-v1",
                       "workerProfileId": "fixture-cpu", "maxWallSeconds": 120}

    def build(self):
        (self.source / "source/workflow.cwl").write_text(json.dumps(self.workflow))
        (self.source / "source/runner.json").write_text(json.dumps(self.runner))
        recipe_path = self.source / "experiment.json"
        recipe = json.loads(recipe_path.read_text())
        for member in recipe["members"]:
            data = (self.source / member["source"]).read_bytes()
            member.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        recipe_path.write_text(json.dumps(recipe))
        package = self.root / "plan.zip"
        receipt = compile_package(recipe_path, package)
        profile_path = self.root / "profile.json"
        data = canonical(self.profile)
        profile_path.write_bytes(data)
        return package, profile_path, receipt["packageSha256"], hashlib.sha256(data).hexdigest()

    def project(self):
        package, profile, package_sha, profile_sha = self.build()
        return project_actions(package, profile, expected_sha256=package_sha,
                               expected_profile_sha256=profile_sha)

    def test_deterministic_projection_is_not_dispatch_or_attempt(self):
        args = self.build()
        with patch("subprocess.Popen", side_effect=AssertionError("no subprocess")), \
                patch("socket.socket", side_effect=AssertionError("no network")):
            results = [project_actions(args[0], args[1], expected_sha256=args[2],
                                       expected_profile_sha256=args[3]) for _ in range(2)]
        self.assertEqual(results[0], results[1])
        self.assertFalse(results[0]["dispatchAuthorized"])
        self.assertEqual(results[0]["jobs"]["native"]["strategy"]["max-parallel"], 1)
        self.assertTrue(any("foregroundExclusion" in item for item in results[0]["blockers"]))
        self.assertFalse(list(self.root.glob("*.attempt")))

    def test_profile_claims_never_authenticate_themselves(self):
        self.profile["capabilities"] = {key: "qualified" for key in CAPABILITIES}
        self.profile["qualificationEvidenceSha256"] = "b" * 64
        result = self.project()
        self.assertFalse(result["dispatchAuthorized"])
        self.assertEqual(len(result["blockers"]), 2)

    def test_digest_mismatches_rejected(self):
        package, profile, package_sha, profile_sha = self.build()
        for expected, selected in (("0" * 64, profile_sha), (package_sha, "0" * 64)):
            with self.subTest(package=expected), self.assertRaises(PackageError):
                project_actions(package, profile, expected_sha256=expected, expected_profile_sha256=selected)

    def test_docker_requirement_cannot_be_silently_removed(self):
        self.workflow["requirements"]["DockerRequirement"] = {"dockerPull": "example@sha256:" + "a" * 64}
        with self.assertRaisesRegex(PackageError, "cannot remove"):
            self.project()

    def test_native_profile_must_be_explicit_in_source(self):
        self.runner["executionMode"] = "cwltool-docker"
        with self.assertRaisesRegex(PackageError, "explicitly select"):
            self.project()

    def test_resources_and_wall_limit_cannot_exceed_profile(self):
        self.profile["limits"]["ramMiB"] = 16
        with self.assertRaisesRegex(PackageError, "ramMin/ramMax"):
            self.project()

    def test_wall_budget_covers_cwl_limit(self):
        self.runner["maxWallSeconds"] = 119
        with self.assertRaisesRegex(PackageError, "CWL time limit"):
            self.project()

    def test_profile_rejects_actions_authority_and_unknown_fields(self):
        for workflow in ("example/fleet/.github/workflows/native.yml@main", "${{ secrets.TOKEN }}",
                         "example/fleet/.github/workflows/../native.yml@" + "a" * 40):
            self.profile["workflow"] = workflow
            with self.subTest(workflow=workflow), self.assertRaisesRegex(PackageError, "pinned"):
                self.project()

    def test_assets_bind_metadata_without_reading_controller_paths(self):
        asset = {"path": "experiment/models/fixture.gguf", "sha256": "d" * 64, "size": 200_000_000}
        self.profile["stagedAssets"] = [asset]
        closure_path = self.source / "dependency-closure.json"
        closure = json.loads(closure_path.read_text())
        closure["classifications"]["external"] = [asset]
        closure_path.write_text(json.dumps(closure))
        self.workflow["inputs"]["model"] = {"type": "File"}
        job_path = self.source / "source/job.yml"
        job = yaml.safe_load(job_path.read_text())
        job["model"] = {"class": "File", "path": "models/fixture.gguf"}
        job_path.write_text(json.dumps(job))
        result = self.project()
        self.assertEqual(result["stagedAssets"], [asset])
        self.assertFalse((self.root / asset["path"]).exists())

    def test_unmatched_and_unsafe_assets_rejected(self):
        self.profile["stagedAssets"] = [{"path": "../model", "sha256": "d" * 64, "size": 1}]
        with self.assertRaisesRegex(PackageError, "Unsafe"):
            self.project()

    def test_unresolved_dependencies_rejected(self):
        closure_path = self.source / "dependency-closure.json"
        closure = json.loads(closure_path.read_text())
        closure["classifications"]["unavailable"] = ["missing"]
        closure_path.write_text(json.dumps(closure))
        with self.assertRaisesRegex(PackageError, "unresolved"):
            self.project()

    def test_unsupported_workflow_and_expression_rejected(self):
        self.workflow["class"] = "Workflow"
        with self.assertRaisesRegex(PackageError, "single top-level"):
            self.project()

    def test_network_permission_remains_required_false(self):
        self.workflow["requirements"]["NetworkAccess"]["networkAccess"] = True
        with self.assertRaisesRegex(PackageError, "disable network"):
            self.project()

    def test_staged_paths_reject_case_and_directory_collisions(self):
        inventories = [("models/A.gguf", "models/a.gguf"), ("experiment",),
                       ("EXPERIMENT/workflow.cwl",), ("experiment-package-manifest.json",)]
        for paths in inventories:
            (self.root / "plan.zip").unlink(missing_ok=True)
            assets = [{"path": path, "sha256": "d" * 64, "size": 1} for path in paths]
            self.profile["stagedAssets"] = assets
            closure_path = self.source / "dependency-closure.json"
            closure = json.loads(closure_path.read_text())
            closure["classifications"]["external"] = assets
            closure_path.write_text(json.dumps(closure))
            with self.subTest(paths=paths), self.assertRaisesRegex(PackageError, "collisi|colliding"):
                self.project()

    def test_native_documents_keep_parser_size_bound(self):
        self.workflow["label"] = "x" * (1024 * 1024)
        with self.assertRaisesRegex(PackageError, "exceeds 1 MiB"):
            self.project()
