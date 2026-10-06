"""Executable contracts for the local agent skills and safe helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/linear-regression-v1"
DOCTOR = ROOT / ".agents/skills/experiment-doctor/scripts/preflight.py"
REPAIR = ROOT / ".agents/skills/experiment-repair/scripts/propose_source_path_fix.py"
SKILLS = ROOT / ".agents/skills"


def invoke(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), *args], cwd=ROOT,
                          capture_output=True, text=True, check=False)


class AgentModeTests(unittest.TestCase):
    def test_all_modes_have_unique_agent_skills_metadata_and_scientific_boundaries(self):
        expected = {"experiment-onboarding", "experiment-doctor", "experiment-repair"}
        found = set()
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), path)
            frontmatter = text.split("---\n", 2)[1]
            fields = dict(line.split(":", 1) for line in frontmatter.splitlines() if ":" in line)
            found.add(fields["name"].strip())
            self.assertTrue(fields.get("description", "").strip(), path)
        self.assertEqual(found, expected)
        onboarding = (SKILLS / "experiment-onboarding/SKILL.md").read_text(encoding="utf-8")
        repair = (SKILLS / "experiment-repair/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not complete a missing field by guessing", onboarding)
        self.assertIn("Do not overwrite an existing file", onboarding)
        self.assertIn("exact SHA-256 and byte count match", repair)
        self.assertIn("never modify an existing recipe, package", repair)
        self.assertTrue((SKILLS / "experiment-onboarding/assets/experiment-intake.md").is_file())

    def _copy_example(self, root: Path) -> Path:
        folder = root / "example"
        shutil.copytree(EXAMPLE, folder)
        return folder

    def test_doctor_default_is_read_only_and_reports_closure_runtime_and_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            before = {path.relative_to(example): path.read_bytes()
                      for path in example.rglob("*") if path.is_file()}
            result = invoke(DOCTOR, str(example / "experiment.json"))
            report = json.loads(result.stdout)
            after = {path.relative_to(example): path.read_bytes()
                     for path in example.rglob("*") if path.is_file()}
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["status"], "diagnostics-complete")
        self.assertEqual(report["compilerCheck"]["status"], "not_run")
        self.assertTrue(report["environment"]["supportedPython"])
        self.assertEqual(report["dependencyClosure"]["runtimeNetworkRequired"], False)
        self.assertIn("embedded", report["dependencyClosure"]["classificationCounts"])
        self.assertIn("experiment/environment.json", report["runtime"])
        self.assertIn("evidence/resource-requirements.json", report["resourceRequirements"])
        self.assertEqual(before, after)

    def test_doctor_reports_missing_source_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            (example / "source/data.csv").unlink()
            result = invoke(DOCTOR, str(example / "experiment.json"))
            report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("source/data.csv" in item["message"] for item in report["findings"]))
        self.assertFalse((example / "source/data.csv").exists())

    def test_doctor_reports_invalid_recipe_as_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            recipe = example / "experiment.json"
            recipe.write_text('{"duplicate": 1, "duplicate": 2}\n')
            result = invoke(DOCTOR, str(recipe))
            report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("Duplicate JSON key" in item["message"] for item in report["findings"]))

    def test_doctor_preserves_unknown_and_unavailable_values(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            recipe_path = example / "experiment.json"
            recipe = json.loads(recipe_path.read_text())
            closure_path = example / "dependency-closure.json"
            closure = json.loads(closure_path.read_text())
            closure["classifications"]["unavailable"].append({"item": "unresolved runtime", "path": "external"})
            closure_path.write_text(json.dumps(closure, indent=2) + "\n")
            resources_path = example / "evidence/resource-requirements.json"
            resources = json.loads(resources_path.read_text())
            resources["ram"]["planningRamBytes"] = "unknown"
            resources_path.write_text(json.dumps(resources, indent=2) + "\n")
            for item in recipe["members"]:
                source = example / item["source"]
                if source in {closure_path, resources_path}:
                    raw = source.read_bytes()
                    item["sha256"] = hashlib.sha256(raw).hexdigest()
                    item["size"] = len(raw)
            recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
            result = invoke(DOCTOR, str(recipe_path))
            report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["dependencyClosure"]["unavailable"],
                         [{"item": "unresolved runtime", "path": "external"}])
        self.assertEqual(
            report["resourceRequirements"]["evidence/resource-requirements.json"]["ram"]["planningRamBytes"],
            "unknown",
        )

    def test_doctor_optional_compiler_check_uses_existing_compile_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            before = {path.relative_to(example) for path in example.rglob("*") if path.is_file()}
            result = invoke(DOCTOR, str(example / "experiment.json"), "--compiler-check")
            report = json.loads(result.stdout)
            after = {path.relative_to(example) for path in example.rglob("*") if path.is_file()}
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["status"], "compiler-verified")
        self.assertEqual(report["compilerCheck"]["compile"]["status"], "passed")
        self.assertEqual(report["compilerCheck"]["verify"]["status"], "passed")
        self.assertTrue(report["compilerCheck"]["temporaryPackageRemoved"])
        self.assertEqual(before, after)

    def test_repair_proposes_exact_same_byte_path_change_without_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            recipe_path = example / "experiment.json"
            original_recipe = recipe_path.read_bytes()
            original_source = (example / "source/protocol.md").read_bytes()
            (example / "source/protocol-copy.md").write_bytes(original_source)
            result = invoke(REPAIR, str(recipe_path), "--member", "experiment/protocol.md",
                            "--to", "source/protocol-copy.md")
            report = result.stdout
            self.assertEqual(recipe_path.read_bytes(), original_recipe)
            self.assertFalse((example / "experiment.repaired.json").exists())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Proposed same-byte source mapping repair", report)
        self.assertIn("source/protocol.md", report)
        self.assertIn("source/protocol-copy.md", report)
        changed_lines = [line for line in report.splitlines()
                         if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        self.assertTrue(changed_lines)
        self.assertTrue(all('"source"' in line for line in changed_lines))

    def test_approved_repair_writes_new_recipe_and_preserves_package_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            recipe_path = example / "experiment.json"
            original_recipe = recipe_path.read_bytes()
            source = (example / "source/protocol.md").read_bytes()
            (example / "source/protocol-copy.md").write_bytes(source)
            repaired_path = example / "experiment.repaired.json"
            result = invoke(REPAIR, str(recipe_path), "--member", "experiment/protocol.md",
                            "--to", "source/protocol-copy.md", "--output", repaired_path.name)
            self.assertEqual(recipe_path.read_bytes(), original_recipe)
            package_path = Path(directory) / "repaired.zip"
            compiled = subprocess.run([sys.executable, "-m", "experiment_compiler", "compile",
                                       str(repaired_path), "--output", str(package_path)],
                                      cwd=ROOT, capture_output=True, text=True)
            verified = subprocess.run([sys.executable, "-m", "experiment_compiler", "verify",
                                       str(package_path), "--recipe", str(repaired_path)],
                                      cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Wrote new recipe: experiment.repaired.json", result.stdout)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertTrue(json.loads(compiled.stdout)["matchedExpectedPackage"])
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_repair_refuses_changed_bytes_and_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            example = self._copy_example(Path(directory))
            recipe_path = example / "experiment.json"
            replacement = example / "source/protocol-changed.md"
            replacement.write_text("changed protocol\n")
            proposed = invoke(REPAIR, str(recipe_path), "--member", "experiment/protocol.md",
                              "--to", "source/protocol-changed.md", "--output", "experiment.repaired.json")
            self.assertFalse((example / "experiment.repaired.json").exists())
            copy = example / "source/protocol-copy.md"
            copy.write_bytes((example / "source/protocol.md").read_bytes())
            (example / "experiment.repaired.json").write_text("preserve me")
            existing = invoke(REPAIR, str(recipe_path), "--member", "experiment/protocol.md",
                              "--to", "source/protocol-copy.md", "--output", "experiment.repaired.json")
        self.assertEqual(proposed.returncode, 2)
        self.assertIn("bytes differ", proposed.stderr)
        self.assertEqual(existing.returncode, 2)
        self.assertIn("refusing to overwrite", existing.stderr)


if __name__ == "__main__":
    unittest.main()
