import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.core import PackageError, compile_package, json_value, load_recipe, verify_bytes
from experiment_compiler.catalog import describe_catalog
from experiment_compiler.runner import (_admit_workflow, _collect_execution_tree, _collect_tree,
                                        _require_bounded_tmpfs, run_package)
from experiment_compiler.runner import sha256_bytes

ROOT = Path(__file__).resolve().parents[1]


class RunnerBoundaryTests(unittest.TestCase):
    def test_runner_requires_explicit_execution_opt_in_before_opening_package(self):
        with self.assertRaisesRegex(PackageError, "explicit opt-in"):
            run_package(Path("does-not-exist.zip"), Path("result.zip"), expected_sha256="0" * 64)

    def test_blocked_muon_plan_is_rejected_before_runner_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            package = directory / "muon-plan.zip"
            output = directory / "muon-result.zip"
            built = compile_package(ROOT / "examples/muon-comparison-plan-v1/experiment.json", package)
            with self.assertRaisesRegex(PackageError, "unavailable prerequisites"):
                run_package(package, output, expected_sha256=built["packageSha256"],
                            allow_workflow_execution=True)
            self.assertFalse(output.exists())

    def test_collection_enforces_aggregate_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.txt").write_bytes(b"1234")
            (root / "two.txt").write_bytes(b"5678")
            with self.assertRaisesRegex(PackageError, "exceed the package size limit"):
                _collect_tree(root, {}, 7)

    def test_collection_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("data")
            link = root / "link.txt"
            link.symlink_to(target)
            with self.assertRaisesRegex(PackageError, "symlink"):
                _collect_tree(root, {}, 100)

    def test_tmpfs_admission_rejects_a_temp_directory_outside_the_bound(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as other:
            path = Path(temporary)
            root = Path(other)
            with patch("experiment_compiler.runner.shutil.disk_usage", return_value=type(
                    "DiskUsage", (), {"total": 32 * 1024 * 1024})()):
                with self.assertRaisesRegex(PackageError, "outside the declared tmpfs"):
                    _require_bounded_tmpfs(path, root, 64)

    def test_tmpfs_admission_accepts_backing_capacity_at_or_above_declared_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "attempt"
            path.mkdir()
            for total in (64, 512):
                with patch("experiment_compiler.runner.shutil.disk_usage", return_value=type(
                        "DiskUsage", (), {"total": total * 1024 * 1024})()):
                    _require_bounded_tmpfs(path, root, 64)

    def test_tmpfs_admission_rejects_backing_capacity_below_declared_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "attempt"
            path.mkdir()
            with patch("experiment_compiler.runner.shutil.disk_usage", return_value=type(
                    "DiskUsage", (), {"total": 63 * 1024 * 1024})()):
                with self.assertRaisesRegex(PackageError, "below the declared worker limit"):
                    _require_bounded_tmpfs(path, root, 64)

    def test_workflow_admission_allows_literal_arguments_and_rejects_other_expressions(self):
        import yaml

        workflow = yaml.safe_load((ROOT / "examples/linear-regression-frozen-lifecycle-v1/source/workflow.cwl")
                                  .read_text())
        limits = {"cores": 1, "ramMiB": 64, "tmpdirMiB": 64, "outdirMiB": 64, "wallSeconds": 120}
        _admit_workflow(workflow, limits,
                        "python:3.13.13-slim@sha256:7ba5f5888fbe0014ab9edb2278922995c2201fc3752c46b0be24763eb46fa9f3")
        workflow["arguments"][1]["valueFrom"] = "$(inputs.reproduce.contents)"
        with self.assertRaisesRegex(PackageError, "outside the reviewed path-only subset"):
            _admit_workflow(workflow, limits,
                            "python:3.13.13-slim@sha256:7ba5f5888fbe0014ab9edb2278922995c2201fc3752c46b0be24763eb46fa9f3")

    def test_cwl_document_references_are_rejected_before_subprocess_launch(self):
        reference_documents = (
            """cwlVersion: v1.2
class: Workflow
inputs: {}
outputs: {}
steps:
  nested:
    run: external-tool.cwl
    in: {}
    out: []
""",
            """cwlVersion: v1.2
class: CommandLineTool
$import: external-tool.cwl
""",
            """cwlVersion: v1.2
class: CommandLineTool
inputs:
  config:
    type: string
    doc:
      $include: external-description.txt
""",
            """cwlVersion: v1.2
class: CommandLineTool
"https://w3id.org/cwl/cwl#schemas": ["https://example.invalid/schema.yml"]
""",
        )
        for workflow_text in reference_documents:
            with self.subTest(workflow=workflow_text.splitlines()[1]), \
                    tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture = directory / "fixture"
                shutil.copytree(ROOT / "examples/linear-regression-plan-v1", fixture)
                workflow_path = fixture / "source/workflow.cwl"
                workflow_path.write_text(workflow_text)
                recipe_path = fixture / "experiment.json"
                recipe = json.loads(recipe_path.read_text())
                for member in recipe["members"]:
                    if member["source"] == "source/workflow.cwl":
                        workflow_bytes = workflow_path.read_bytes()
                        member["sha256"] = sha256_bytes(workflow_bytes)
                        member["size"] = len(workflow_bytes)
                recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
                plan = directory / "plan.zip"
                built = compile_package(recipe_path, plan)
                with patch.dict(os.environ, {
                        "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                        "EXPERIMENT_RUNNER_SANDBOX": "test worker",
                        "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                            '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
                        "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
                        "TMPDIR": str(directory),
                }), patch("experiment_compiler.runner.importlib.metadata.version",
                          return_value="3.2.20260720092025"), \
                        patch("experiment_compiler.runner.subprocess.Popen") as popen:
                    with self.assertRaisesRegex(PackageError, "execution deferred:"):
                        run_package(plan, directory / "result.zip",
                                    expected_sha256=built["packageSha256"],
                                    allow_workflow_execution=True)
                    popen.assert_not_called()

    def test_job_order_references_and_overrides_are_rejected_before_subprocess_launch(self):
        base_job = (ROOT / "examples/linear-regression-plan-v1/source/job.yml").read_text()
        job_documents = (
            '"$import": "https://example.invalid/job.yml"\n' + base_job,
            base_job.replace("runPlan:\n", "runPlan:\n  $include: https://example.invalid/run-plan.yml\n"),
            '"cwltool:overrides": []\n' + base_job,
            '"cwl:tool": "external-tool.cwl"\n' + base_job,
            '"https://w3id.org/cwl/cwl#overrides": []\n' + base_job,
            '"https://w3id.org/cwl/cwl#import": "https://example.invalid/job.yml"\n' + base_job,
            base_job.replace("data:\n", 'data:\n  "https://w3id.org/cwl/cwl#include": "https://example.invalid/data.yml"\n'),
        )
        for job_text in job_documents:
            with self.subTest(job=job_text.splitlines()[0]), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture = directory / "fixture"
                shutil.copytree(ROOT / "examples/linear-regression-plan-v1", fixture)
                job_path = fixture / "source/job.yml"
                job_path.write_text(job_text)
                recipe_path = fixture / "experiment.json"
                recipe = json.loads(recipe_path.read_text())
                for member in recipe["members"]:
                    if member["source"] == "source/job.yml":
                        job_bytes = job_path.read_bytes()
                        member["sha256"] = sha256_bytes(job_bytes)
                        member["size"] = len(job_bytes)
                recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
                plan = directory / "plan.zip"
                built = compile_package(recipe_path, plan)
                with patch.dict(os.environ, {
                        "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                        "EXPERIMENT_RUNNER_SANDBOX": "test worker",
                        "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                            '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
                        "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
                        "TMPDIR": str(directory),
                }), patch("experiment_compiler.runner.importlib.metadata.version",
                          return_value="3.2.20260720092025"), \
                        patch("experiment_compiler.runner.subprocess.Popen") as popen:
                    with self.assertRaisesRegex(PackageError, "execution deferred:"):
                        run_package(plan, directory / "result.zip",
                                    expected_sha256=built["packageSha256"],
                                    allow_workflow_execution=True)
                    popen.assert_not_called()

    def test_podman_mode_selects_podman_in_the_pinned_cwltool_runner(self):
        class CompletedProcess:
            pid = 102
            returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        def fake_popen(command, **kwargs):
            self.assertIn("--podman", command)
            self.assertIn("--disable-pull", command)
            self.assertNotIn("--no-container", command)
            outdir = Path(command[command.index("--outdir") + 1])
            provdir = Path(command[command.index("--provenance") + 1])
            outdir.mkdir(parents=True)
            (outdir / "result.txt").write_text("fixture result\n")
            provdir.mkdir(parents=True)
            (provdir / "workflow-run.json").write_text('{"fixture":"provenance"}\n')
            return CompletedProcess()

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = directory / "fixture"
            shutil.copytree(ROOT / "examples/linear-regression-plan-v1", fixture)
            runner_path = fixture / "source/runner.json"
            runner = json.loads(runner_path.read_text())
            runner["executionMode"] = "cwltool-podman"
            runner_path.write_text(json.dumps(runner, indent=2) + "\n")
            recipe_path = fixture / "experiment.json"
            recipe = json.loads(recipe_path.read_text())
            for member in recipe["members"]:
                if member["source"] == "source/runner.json":
                    runner_bytes = runner_path.read_bytes()
                    member["sha256"] = sha256_bytes(runner_bytes)
                    member["size"] = len(runner_bytes)
            recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
            plan = directory / "plan.zip"
            built = compile_package(recipe_path, plan)
            result = directory / "result.zip"
            limits = '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}'
            with patch.dict(os.environ, {
                    "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                    "EXPERIMENT_RUNNER_SANDBOX": "rootless Podman fixture",
                    "EXPERIMENT_RUNNER_RESOURCE_LIMITS": limits,
                    "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
                    "TMPDIR": str(directory),
            }), patch("experiment_compiler.runner._require_bounded_tmpfs"), \
                    patch("experiment_compiler.runner.importlib.metadata.version",
                          return_value="3.2.20260720092025"), \
                    patch("experiment_compiler.runner.subprocess.Popen", side_effect=fake_popen):
                outcome = run_package(plan, result, expected_sha256=built["packageSha256"],
                                      allow_workflow_execution=True)
            self.assertEqual(outcome["executionStatus"], "succeeded")
            verified = verify_bytes(result.read_bytes())
            self.assertEqual(verified["profile"], "compiled-experiment-lifecycle-v1")

    def test_execution_collection_rejection_is_recorded_without_admitting_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("data")
            (root / "rejected-link.txt").symlink_to(target)
            errors = []
            collected = _collect_execution_tree(root, "output", errors)
            self.assertEqual(collected, {})
            self.assertTrue(any("symlink" in error for error in errors))

    def test_successful_attempt_creates_a_distinct_immutable_result_package(self):
        class CompletedProcess:
            pid = 101
            returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        def fake_popen(command, **kwargs):
            self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
            self.assertNotIn("ACTIONS_RUNTIME_TOKEN", kwargs["env"])
            self.assertIn("--disable-pull", command)
            self.assertIn("--strict-memory-limit", command)
            self.assertIn("--strict-cpu-limit", command)
            self.assertNotIn("--no-container", command)
            outdir = Path(command[command.index("--outdir") + 1])
            provdir = Path(command[command.index("--provenance") + 1])
            outdir.mkdir(parents=True)
            (outdir / "result.json").write_text('{"fixture":"result"}\n')
            provdir.mkdir(parents=True)
            (provdir / "workflow-run.json").write_text('{"fixture":"provenance"}\n')
            return CompletedProcess()

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = directory / "plan.zip"
            plan_result = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", plan)
            plan_bytes = plan.read_bytes()
            original_sha = sha256_bytes(plan_bytes)
            result = directory / "result.zip"
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
                run_result = run_package(plan, result, expected_sha256=original_sha,
                                         allow_workflow_execution=True)
            self.assertEqual(plan.read_bytes(), plan_bytes)
            self.assertEqual(run_result["executionStatus"], "succeeded")
            verified = verify_bytes(result.read_bytes())
            self.assertEqual(verified["profile"], "compiled-experiment-lifecycle-v1")
            self.assertNotEqual(verified["packageSha256"], plan_result["packageSha256"])
            result_recipe = result.with_name(result.stem + ".source") / "experiment.json"
            verified_from_recipe = verify_bytes(result.read_bytes(), recipe=load_recipe(result_recipe))
            self.assertEqual(verified_from_recipe["packageSha256"], verified["packageSha256"])
            subprocess.run([sys.executable, str(ROOT / "scripts/validate_current_ro_profiles.py"),
                            str(result_recipe.parent)], check=True, capture_output=True, text=True)
            catalog = describe_catalog(result_recipe.parent)
            self.assertEqual(catalog["experiments"][0]["lifecycle"]["attemptCount"], 1)
            with __import__("zipfile").ZipFile(result) as archive:
                names = archive.namelist()
                receipt_name = next(name for name in names if name.endswith("/runner-receipt.json"))
                receipt = json_value(archive.read(receipt_name))
                crate = json_value(archive.read("ro-crate-metadata.json"))
                archived_plan = archive.read(receipt["planPackageArchive"]["@id"])
            self.assertEqual(receipt["planPackageSha256"], original_sha)
            self.assertEqual(archived_plan, plan_bytes)
            self.assertEqual(receipt["status"], "succeeded")
            self.assertEqual(receipt["workflowRunCrateFiles"], ["workflow-run.json"])
            self.assertTrue(any(node.get("@type") == "CreateAction" for node in crate["@graph"]))


if __name__ == "__main__":
    unittest.main()
