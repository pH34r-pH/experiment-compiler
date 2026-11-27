"""Reusable components exercise the existing CLIs and retain their safety boundaries."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_protocol_coverage
import test_revision
from fixture_requirements import REQUIRES_RUNNER

from experiment_compiler.core import PackageError, compile_package
from experiment_compiler.lifecycle_components import component_command

ROOT = Path(__file__).resolve().parents[1]


class LifecycleComponentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_component(self, stage, parameters, *, check=True, cwd=None):
        if os.name != "posix":
            self.skipTest("GitHub lifecycle command handoff uses POSIX exec")
        return subprocess.run([sys.executable, str(ROOT / "scripts/lifecycle_component.py"), stage],
            env={**os.environ, "COMPONENT_PARAMETERS": json.dumps(parameters)},
            capture_output=True, check=check, timeout=30, cwd=cwd)

    def test_compile_verify_preserve_historical_bytes_and_treat_shell_text_as_data(self):
        recipe = str(ROOT / "examples/issue-164/experiment.json")
        output = self.root / "literal-$(touch INJECTION).zip"
        report = json.loads(self.run_component("compile", {"recipe": recipe, "output": str(output)}).stdout)
        self.assertEqual(report["packageSha256"], "45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341")
        parameters = {"package": str(output), "expected-sha256": report["packageSha256"]}
        checked = self.run_component("verify", {**parameters, "max-members": "9"})
        self.assertEqual(json.loads(checked.stdout)["scope"], "package-integrity-only")
        refused = self.run_component("verify", {**parameters, "max-members": "8"}, check=False)
        self.assertIn(b"Local resource policy refused package", refused.stderr)
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

    @REQUIRES_RUNNER
    def test_composite_inputs_are_environment_data_and_publication_gate_is_unchanged(self):
        import yaml

        for stage in ("compile", "verify", "execute", "finalize", "publication"):
            action = yaml.safe_load((ROOT / ".github/actions" / stage / "action.yml").read_text())
            step = action["runs"]["steps"][0]
            self.assertEqual(step["env"]["COMPONENT_PARAMETERS"], "${{ inputs.parameters }}")
            self.assertNotIn("${{", step["run"])
        workflow = yaml.safe_load((ROOT / ".github/workflows/lifecycle.yml").read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        pages = workflow["jobs"]["pages"]
        self.assertEqual(pages["if"], "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' "
                         "&& inputs.operation == 'pages'")
        self.assertEqual(pages["environment"]["name"], "github-pages")
        self.assertEqual(pages["permissions"]["attestations"], "write")

    @REQUIRES_RUNNER
    def test_alpha_signing_uses_only_an_owner_dispatch_and_a_same_attempt_build(self):
        import yaml

        workflow = yaml.load((ROOT / ".github/workflows/lifecycle.yml").read_text(), Loader=yaml.BaseLoader)
        operation = workflow["on"]["workflow_dispatch"]["inputs"]["operation"]
        self.assertEqual(operation["default"], "pages")
        self.assertEqual(operation["options"], ["pages", "alpha-provenance"])
        build = workflow["jobs"]["build"]
        self.assertNotIn("permissions", build)
        signing = workflow["jobs"]["alpha-provenance"]
        for guard in ("github.event_name == 'workflow_dispatch'", "inputs.operation == 'alpha-provenance'",
                      "github.repository == 'pH34r-pH/experiment-compiler'", "github.ref == 'refs/heads/main'",
                      "github.actor == github.repository_owner", "github.triggering_actor == github.repository_owner"):
            self.assertIn(guard, signing["if"])
        self.assertEqual(signing["needs"], "build")
        self.assertEqual(signing["permissions"], {"contents": "read", "id-token": "write", "attestations": "write"})
        producer = next(step for step in build["steps"] if step.get("name") == "Retain exact alpha inputs for the signing job")
        reader = next(step for step in signing["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(reader["with"]["name"], "alpha-release-inputs-${{ github.sha }}-${{ github.run_attempt }}")
        self.assertEqual(reader["with"]["name"], producer["with"]["name"])
        self.assertEqual(reader["with"]["digest-mismatch"], "error")
        self.assertEqual(set(reader["with"]), {"name", "path", "digest-mismatch"})
        self.assertEqual(producer["if"], "inputs.operation == 'alpha-provenance' && matrix.python == '3.12'")
        verifier = next(step["run"] for step in signing["steps"]
                        if step.get("name") == "Verify every signature against the exact producer")
        self.assertIn("/usr/bin/gh attestation verify", verifier)

    def test_finalization_component_retains_amended_coverage(self):
        from experiment_compiler.core import sha256_file
        from experiment_compiler.coverage import inspect_coverage

        fixture = test_protocol_coverage.ProtocolCoverageTests()
        attempt, attempt_id, coverage = fixture.amendment_fixture(self.root)
        source = self.root / "coverage.json"
        source.write_text(json.dumps(coverage), encoding="utf-8")
        output = self.root / "result-with-amendments.zip"
        parameters = {
            "parent-package": str(attempt), "expected-sha256": sha256_file(attempt),
            "attempt-id": attempt_id, "id": "component-amendments", "title": "Synthetic amendments",
            "decision": str(ROOT / "examples/pipeline-finalization-fixture/decision.md"),
            "decision-summary": "Synthetic negative decision.",
            "review": str(ROOT / "examples/pipeline-finalization-fixture/review.md"),
            "review-summary": "Synthetic review.", "reviewer-name": "Fixture reviewer",
            "output": str(output), "coverage-record": str(source),
        }
        report = json.loads(self.run_component("finalize", parameters).stdout)
        self.assertEqual(report["coverageRecordSha256"], sha256_file(source))
        self.assertEqual(report["coverage"], inspect_coverage(output.read_bytes(), expected_sha256=sha256_file(output)))
