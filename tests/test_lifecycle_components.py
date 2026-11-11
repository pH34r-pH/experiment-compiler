"""Reusable components exercise the existing CLIs and retain their safety boundaries."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_revision
import yaml

from experiment_compiler.core import PackageError, compile_package
from experiment_compiler.lifecycle_components import component_command

ROOT = Path(__file__).resolve().parents[1]


class LifecycleComponentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_component(self, stage, parameters, *, check=True, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / "scripts/lifecycle_component.py"), stage],
            env={**os.environ, "COMPONENT_PARAMETERS": json.dumps(parameters)},
            capture_output=True, check=check, timeout=30, cwd=cwd)

    def test_compile_verify_preserve_historical_bytes_and_treat_shell_text_as_data(self):
        recipe = str(ROOT / "examples/issue-164/experiment.json")
        output = self.root / "literal-$(touch INJECTION).zip"
        report = json.loads(self.run_component("compile", {"recipe": recipe, "output": str(output)}).stdout)
        self.assertEqual(report["packageSha256"], "45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341")
        checked = self.run_component("verify", {"package": str(output), "expected-sha256": report["packageSha256"]})
        self.assertEqual(json.loads(checked.stdout)["scope"], "package-integrity-only")
        self.assertFalse((ROOT / "INJECTION").exists())

    def test_caller_repository_cannot_shadow_the_pinned_compiler_module(self):
        shadow = self.root / "experiment_compiler"
        shadow.mkdir()
        (shadow / "__init__.py").write_text("raise RuntimeError('caller shadow executed')")
        report = self.run_component("compile", {"recipe": str(ROOT / "examples/issue-164/experiment.json"),
            "output": str(self.root / "exact.zip")}, cwd=self.root)
        self.assertEqual(json.loads(report.stdout)["packageSizeBytes"], 15296)

    def test_execution_default_cannot_opt_in_or_invent_flags(self):
        parameters = {"package": "plan.zip", "output": "attempt.zip", "expected-sha256": "a" * 64}
        self.assertNotIn("--allow-workflow-execution", component_command("execute", parameters))
        for extra in ({"allow-workflow-execution": "true"}, {"untrusted-command": "echo injected"}):
            with self.assertRaises(PackageError):
                component_command("execute", {**parameters, **extra})
        plan = self.root / "plan.zip"
        report = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", plan)
        rejected = self.run_component("execute", {"package": str(plan), "output": str(self.root / "attempt.zip"),
            "expected-sha256": report["packageSha256"]}, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(list(self.root.glob("*.attempt")))

    def test_finalization_component_binds_one_exact_retained_attempt(self):
        fixture = test_revision.RevisionTests()
        attempt, _, _, attempt_id = fixture._attempt(self.root)
        from experiment_compiler.core import sha256_file
        output = self.root / "final.zip"
        parameters = {"parent-package": str(attempt), "expected-sha256": sha256_file(attempt),
            "attempt-id": attempt_id, "id": "component-final", "title": "Synthetic component fixture",
            "decision": str(ROOT / "examples/pipeline-finalization-fixture/decision.md"),
            "decision-summary": "Synthetic fixture; no scientific claim.",
            "review": str(ROOT / "examples/pipeline-finalization-fixture/review.md"),
            "review-summary": "Exact synthetic closure only.", "reviewer-name": "Fixture reviewer", "output": str(output)}
        result = json.loads(self.run_component("finalize", parameters).stdout)
        self.assertEqual(result["selectedAttemptId"], attempt_id)
        self.assertEqual(result["parentPackageSha256"], parameters["expected-sha256"])
        self.assertTrue(attempt.is_file())

    def test_composite_inputs_are_environment_data_and_publication_gate_is_unchanged(self):
        for stage in ("compile", "verify", "execute", "finalize", "publication"):
            action = yaml.safe_load((ROOT / ".github/actions" / stage / "action.yml").read_text())
            step = action["runs"]["steps"][0]
            self.assertEqual(step["env"]["COMPONENT_PARAMETERS"], "${{ inputs.parameters }}")
            self.assertNotIn("${{", step["run"])
        workflow = yaml.safe_load((ROOT / ".github/workflows/lifecycle.yml").read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        pages = workflow["jobs"]["pages"]
        self.assertEqual(pages["if"], "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'")
        self.assertEqual(pages["environment"]["name"], "github-pages")
        self.assertEqual(pages["permissions"]["attestations"], "write")
