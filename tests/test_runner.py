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
    def test_nonfinite_resource_limits_are_rejected(self):
        import copy
        import yaml
        original = yaml.safe_load((ROOT / "examples/linear-regression-frozen-lifecycle-v1/source/workflow.cwl").read_text())
        limits = {"cores": 1, "ramMiB": 64, "tmpdirMiB": 64, "outdirMiB": 64, "wallSeconds": 120}
        image = original["requirements"]["DockerRequirement"]["dockerPull"]
        _admit_workflow(original, limits, image)
        for value in (float("nan"), float("inf"), float("-inf"), 10 ** 1000):
            for key in limits:
                with self.subTest(worker=key, value=value):
                    invalid = dict(limits, **{key: value})
                    with self.assertRaises(PackageError):
                        _admit_workflow(original, invalid, image)
            for key in original["requirements"]["ResourceRequirement"]:
                with self.subTest(cwl=key, value=value):
                    invalid = copy.deepcopy(original)
                    invalid["requirements"]["ResourceRequirement"][key] = value
                    with self.assertRaises(PackageError):
                        _admit_workflow(invalid, limits, image)
        for value in (True, 1.0, float("nan"), float("inf")):
            invalid = copy.deepcopy(original)
            invalid["requirements"]["ToolTimeLimit"]["timelimit"] = value
            with self.subTest(timelimit=value), self.assertRaises(PackageError):
                _admit_workflow(invalid, limits, image)

    def test_collector_rejects_oversized_individual_member(self):
        from experiment_compiler.core import MAX_FILE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (root / "too-large").open("wb") as stream:
                stream.truncate(MAX_FILE + 1)
            errors = []
            self.assertEqual(_collect_execution_tree(root, "output", errors), {})
            self.assertTrue(errors)

    def test_attempt_receipts_survive_execution_and_packaging_failures(self):
        scenarios = ("success", "nonzero", "timeout", "oversized-log", "symlink",
                     "oversized-output", "aggregate", "shared-budget", "missing", "compile",
                     "copy", "existing-output", "existing-source", "launch", "interrupted", "terminal-persist")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                self._check_attempt_scenario(scenario, Path(temporary))

    def _mock_attempt_launch(self, scenario, directory, scratch):
        class Process:
            pid = 98765
            returncode = 9 if scenario == "nonzero" else 0
            calls = 0

            def wait(self, timeout=None):
                self.calls += 1
                if scenario == "timeout" and self.calls == 1:
                    raise subprocess.TimeoutExpired("fixture", timeout)
                return self.returncode

        def launch(command, **kwargs):
            receipt = json.loads((directory / "result.attempt/runner-receipt.json").read_text())
            self.assertEqual(receipt["status"], "started")
            self.assertIsNone(receipt["endedAt"])
            if scenario == "launch":
                raise OSError("fixture launch failure")
            if scenario == "interrupted":
                raise KeyboardInterrupt()
            scratch.append(Path(kwargs["cwd"]).parent)
            kwargs["stdout"].write(b"bounded diagnostic")
            if scenario == "oversized-log":
                kwargs["stdout"].truncate(4 * 1024 * 1024 + 1)
            self._write_attempt_outputs(scenario, directory, command)
            return Process()

        return launch

    def _write_attempt_outputs(self, scenario, directory, command):
        from experiment_compiler.core import MAX_FILE
        out = Path(command[command.index("--outdir") + 1])
        out.mkdir()
        if scenario != "missing":
            (out / "result.txt").write_bytes(b"fixture")
        if scenario == "symlink":
            (out / "link").symlink_to(out / "result.txt")
        sizes = {"oversized-output": [MAX_FILE + 1], "aggregate": [MAX_FILE] * 3,
                 "shared-budget": [MAX_FILE] * 2}
        for number, size in enumerate(sizes.get(scenario, [])):
            with (out / f"large-{number}").open("wb") as stream:
                stream.truncate(size)
        if scenario == "shared-budget":
            provenance = Path(command[command.index("--provenance") + 1])
            provenance.mkdir()
            with (provenance / "too-much").open("wb") as stream:
                stream.truncate(MAX_FILE)
        if scenario == "existing-output":
            (directory / "result.zip").write_bytes(b"not owned by this attempt")
        if scenario == "existing-source":
            (directory / "result.source").mkdir()
            (directory / "result.source/existing").write_bytes(b"not owned by this attempt")

    def _mock_attempt_publication(self, scenario, output, terminal_failure):
        original_replace = Path.replace
        original_copytree = shutil.copytree

        def replace_receipt(path, target):
            if (scenario == "terminal-persist" and output.exists() and
                    path.name == "runner-receipt.json.tmp" and not terminal_failure):
                terminal_failure.append(True)
                raise OSError("fixture terminal persistence failure")
            return original_replace(path, target)

        def copy_source(source, destination, *args, **kwargs):
            if scenario == "copy":
                (destination / "partial").write_bytes(b"partial copied source")
                raise OSError("fixture source copy failure")
            return original_copytree(source, destination, *args, **kwargs)

        return replace_receipt, copy_source

    def _check_attempt_scenario(self, scenario, directory):
        plan = directory / "plan.zip"
        built = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", plan)
        output = directory / "result.zip"
        scratch, terminal_failure = [], []
        launch = self._mock_attempt_launch(scenario, directory, scratch)
        replace_receipt, copy_source = self._mock_attempt_publication(scenario, output, terminal_failure)
        with patch.dict(os.environ, {
                "EXPERIMENT_RUNNER_SANDBOX": "fixture",
                "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                    '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
                "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
        }), patch("experiment_compiler.runner.importlib.metadata.version", return_value="3.2.20260720092025"), \
                patch("experiment_compiler.runner._require_bounded_tmpfs"), \
                patch("experiment_compiler.runner.subprocess.Popen", side_effect=launch), \
                patch("experiment_compiler.runner.os.killpg"), \
                patch.object(Path, "replace", new=replace_receipt), \
                patch("experiment_compiler.runner.shutil.copytree", side_effect=copy_source), \
                patch("experiment_compiler.runner.compile_package", side_effect=PackageError("fixture packaging failure") if scenario == "compile" else compile_package):
            errors = {"compile": PackageError, "interrupted": KeyboardInterrupt,
                      "copy": OSError, "existing-output": OSError, "existing-source": OSError,
                      "launch": OSError, "terminal-persist": OSError}
            if scenario in errors:
                with self.assertRaises(errors[scenario]):
                    run_package(plan, output, expected_sha256=built["packageSha256"], allow_workflow_execution=True)
            else:
                run_package(plan, output, expected_sha256=built["packageSha256"], allow_workflow_execution=True)
        receipt = json.loads((directory / "result.attempt/runner-receipt.json").read_text())
        self.assertEqual(receipt["planPackageSha256"], built["packageSha256"])
        self.assertTrue(receipt["attemptId"])
        self.assertTrue(all(not path.exists() for path in scratch))
        self._check_attempt_outcome(scenario, directory, receipt, terminal_failure)

    def _check_attempt_outcome(self, scenario, directory, receipt, terminal_failure):
        statuses = {"interrupted": "started", "terminal-persist": "started",
                    "success": "succeeded", "timeout": "timed-out"}
        self.assertEqual(receipt["status"], statuses.get(scenario, "failed"))
        rejected = {"symlink", "oversized-output", "aggregate", "missing"}
        errors = rejected | {"compile", "copy", "existing-output", "existing-source",
                             "launch", "oversized-log", "shared-budget"}
        if scenario in errors:
            self.assertTrue(receipt["collectionErrors"])
        if scenario in rejected:
            self.assertEqual(receipt["outputs"], [])
        if scenario == "shared-budget":
            self.assertEqual(receipt["workflowRunCrateFiles"], [])
        if scenario in ("compile", "copy"):
            message = "fixture packaging failure" if scenario == "compile" else "fixture source copy failure"
            self.assertIn(message, receipt["collectionErrors"][-1])
            self.assertFalse((directory / "result.zip").exists())
            self.assertFalse((directory / "result.source").exists())
        if scenario == "compile":
            self.assertEqual((directory / "result.attempt/stdout.log").read_bytes(), b"bounded diagnostic")
        self._check_attempt_publication(scenario, directory, receipt, terminal_failure)

    def _check_attempt_publication(self, scenario, directory, receipt, terminal_failure):
        output = directory / "result.zip"
        if scenario == "existing-output":
            self.assertEqual(output.read_bytes(), b"not owned by this attempt")
            self.assertFalse((directory / "result.source").exists())
        if scenario == "existing-source":
            self.assertEqual((directory / "result.source/existing").read_bytes(), b"not owned by this attempt")
            self.assertFalse(output.exists())
        if scenario in ("success", "terminal-persist"):
            import zipfile
            verify_bytes(output.read_bytes())
            with zipfile.ZipFile(output) as archive:
                member = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
                published = json.loads(archive.read(member))
            self.assertEqual(published["status"], "succeeded")
            self.assertEqual(published["attemptId"], receipt["attemptId"])
            if scenario == "terminal-persist":
                self.assertEqual(terminal_failure, [True])
                self.assertEqual(receipt["collectionErrors"], [])
            else:
                self.assertEqual(receipt, published)

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
            python_lib = str(Path(sys.executable).resolve().parent.parent / "lib")
            self.assertIn(python_lib, kwargs["env"]["LD_LIBRARY_PATH"].split(":"))
            self.assertEqual(kwargs["env"]["XDG_DATA_HOME"], "/tmp/podman-data")
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
                    "XDG_DATA_HOME": "/tmp/podman-data",
            }), patch("experiment_compiler.runner._require_bounded_tmpfs"), \
                    patch("experiment_compiler.runner.importlib.metadata.version",
                          return_value="3.2.20260720092025"), \
                    patch("experiment_compiler.runner.subprocess.Popen", side_effect=fake_popen):
                outcome = run_package(plan, result, expected_sha256=built["packageSha256"],
                                      allow_workflow_execution=True)
            self.assertEqual(outcome["executionStatus"], "succeeded")
            retained_recipe = load_recipe(
                result.with_name(result.stem + ".source") / "experiment.json"
            )
            verify_bytes(result.read_bytes(), recipe=retained_recipe)
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
