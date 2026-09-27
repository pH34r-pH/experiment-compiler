import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.core import MAX_FILE, PackageError, compile_package, json_value, load_recipe, sha256, verify_bytes
from experiment_compiler.revision import _read_parent_attempt, revise_package
from experiment_compiler.runner import run_package

ROOT = Path(__file__).resolve().parents[1]


class RevisionTests(unittest.TestCase):
    def _attempt(self, directory: Path) -> tuple[Path, str, bytes, str]:
        plan = directory / "plan.zip"
        built = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", plan)
        plan_bytes = plan.read_bytes()

        class CompletedProcess:
            pid = 101
            returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        def fake_popen(command, **kwargs):
            outdir = Path(command[command.index("--outdir") + 1])
            provdir = Path(command[command.index("--provenance") + 1])
            outdir.mkdir(parents=True)
            (outdir / "result.json").write_text('{"fixture":"result"}\n')
            provdir.mkdir(parents=True)
            (provdir / "workflow-run.json").write_text('{"fixture":"provenance"}\n')
            return CompletedProcess()

        attempt = directory / "attempt.zip"
        with patch.dict(os.environ, {
                "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                "EXPERIMENT_RUNNER_SANDBOX": "test worker",
                "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                    '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
                "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
                "TMPDIR": str(directory),
        }), patch("experiment_compiler.runner._require_bounded_tmpfs"), \
                patch("experiment_compiler.runner.importlib.metadata.version",
                      return_value="3.2.20260720092025"), \
                patch("experiment_compiler.runner.subprocess.Popen", side_effect=fake_popen):
            run_package(plan, attempt, expected_sha256=built["packageSha256"],
                        allow_workflow_execution=True)
        with zipfile.ZipFile(attempt) as archive:
            receipt_name = next(name for name in archive.namelist()
                                if name.endswith("/runner-receipt.json"))
            receipt = json_value(archive.read(receipt_name))
        return attempt, built["packageSha256"], plan_bytes, receipt["attemptId"]

    def _revision_recipe(self, directory: Path) -> Path:
        fixture = directory / "revision-v2"
        shutil.copytree(ROOT / "examples/linear-regression-plan-v1", fixture)
        recipe_path = fixture / "experiment.json"
        recipe = json.loads(recipe_path.read_text())
        recipe["id"] = "linear-regression-plan-v2"
        recipe["title"] = "Prospective linear-regression revision"
        recipe.pop("expectedPackage", None)
        protocol_path = fixture / "source/protocol.md"
        protocol_path.write_text(protocol_path.read_text() + "\n## Revision\nUpdated after attempt 1.\n")
        protocol_bytes = protocol_path.read_bytes()
        for member in recipe["members"]:
            if member["source"] == "source/protocol.md":
                member["sha256"] = sha256(protocol_bytes)
                member["size"] = len(protocol_bytes)
        recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
        return recipe_path

    def test_revision_carries_exact_attempt_and_preserves_prospective_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, attempt_bytes, attempt_id = self._attempt(directory)
            attempt_package_bytes = attempt.read_bytes()
            attempt_sha = sha256(attempt_package_bytes)
            recipe = self._revision_recipe(directory)
            revised = directory / "revision-v2.zip"

            result = revise_package(attempt, recipe, revised,
                                    expected_sha256=attempt_sha, attempt_id=attempt_id)

            self.assertEqual(attempt.read_bytes(), attempt_package_bytes)
            self.assertNotEqual(attempt_bytes, attempt_package_bytes)
            self.assertEqual(result["parentPackageSha256"], attempt_sha)
            self.assertEqual(result["selectedAttemptId"], attempt_id)
            verified = verify_bytes(revised.read_bytes(), recipe=load_recipe(
                revised.with_name("revision-v2.source") / "experiment.json"))
            self.assertEqual(verified["lifecycle"]["attemptCount"], 0)
            self.assertFalse(verified["lifecycle"]["processRunCrate"])
            with zipfile.ZipFile(revised) as archive:
                crate = json_value(archive.read("ro-crate-metadata.json"))
                entities = {node["@id"]: node for node in crate["@graph"]}
                root = entities["./"]
                protocol = entities[root["mainEntity"]["@id"]]
                parent_file = next(node for node in crate["@graph"]
                                   if node.get("sha256") == attempt_sha)
                self.assertEqual(archive.read(parent_file["@id"]), attempt_package_bytes)
                self.assertIn("https://www.w3.org/ns/prov#wasRevisionOf", protocol)
                self.assertEqual(root["https://www.w3.org/ns/prov#wasRevisionOf"]["@id"],
                                 parent_file["@id"])

    def test_revision_rejects_wrong_digest_or_attempt_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            recipe = self._revision_recipe(directory)
            for index, (digest, selected_attempt) in enumerate((
                    ("0" * 64, attempt_id), (sha256(attempt.read_bytes()), "wrong-attempt"))):
                output = directory / f"rejected-{index}.zip"
                with self.subTest(index=index), self.assertRaises(PackageError):
                    revise_package(attempt, recipe, output, expected_sha256=digest,
                                   attempt_id=selected_attempt)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name(output.stem + ".source").exists())

    def test_revision_rejects_oversized_parent_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            parent = directory / "oversized.zip"
            parent.write_bytes(b"x" * (MAX_FILE + 1))
            recipe = self._revision_recipe(directory)
            output = directory / "never-written.zip"
            with self.assertRaisesRegex(PackageError, "single-member revision limit"):
                revise_package(parent, recipe, output, expected_sha256="0" * 64,
                               attempt_id="unreached")
            self.assertFalse(output.exists())

    def test_revision_rejects_prospective_action_masquerading_as_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            with zipfile.ZipFile(attempt) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            crate = json_value(files["ro-crate-metadata.json"])
            selected = next(node for node in crate["@graph"]
                            if node.get("@id") == f"#attempt-{attempt_id}")
            selected["actionStatus"] = "https://schema.org/PotentialActionStatus"
            # Keep the crate's Process Run claim valid with another executed action.
            executed = dict(selected)
            executed.update({"@id": "#unselected-execution",
                             "actionStatus": "https://schema.org/CompletedActionStatus"})
            crate["@graph"].append(executed)
            files["ro-crate-metadata.json"] = json.dumps(crate).encode()
            malformed = directory / "malformed-attempt.zip"
            with zipfile.ZipFile(malformed, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            with self.assertRaisesRegex(PackageError, "not an executed CreateAction"):
                _read_parent_attempt(malformed.read_bytes(), attempt_id)


if __name__ == "__main__":
    unittest.main()
