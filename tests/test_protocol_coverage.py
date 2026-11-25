"""The optional first coverage slice binds source declarations to actual bytes."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from experiment_compiler.core import PackageError, compile_package, sha256, verify_bytes
from experiment_compiler.coverage import inspect_coverage
from experiment_compiler.finalization import finalize_package
from experiment_compiler.revision import _read_parent_attempt
from experiment_compiler.lifecycle_components import component_command
import test_revision

ROOT = Path(__file__).resolve().parents[1]


class ProtocolCoverageTests(unittest.TestCase):
    def fixture(self, directory, *, wrong_protocol=False, duplicate_obligation=False):
        source = directory / "fixture"
        shutil.copytree(ROOT / "examples/linear-regression-plan-v1", source)
        recipe_path = source / "experiment.json"
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        protocol_name = "README.md" if wrong_protocol else "protocol.md"
        protocol = (source / "source" / protocol_name).read_bytes()
        obligations = {"schema": "compiled-experiment-obligations/v1",
                       "protocol": {"path": f"experiment/{protocol_name}", "sha256": sha256(protocol)},
                       "obligations": [{"id": f"primary-{i}", "role": "primary", "applicability": "always"}
                                       for i in range(4)] +
                                      [{"id": "calibration", "role": "diagnostic", "applicability": "always"}]}
        if duplicate_obligation:
            obligations["obligations"].append(copy.deepcopy(obligations["obligations"][0]))
        raw = (json.dumps(obligations) + "\n").encode()
        (source / "source/obligations.json").write_bytes(raw)
        member = {"path": "experiment/obligations.json", "source": "source/obligations.json",
                  "sha256": sha256(raw), "size": len(raw)}
        recipe["members"].append(member)
        crate_path = source / "ro-crate-metadata.json"
        crate = json.loads(crate_path.read_text(encoding="utf-8"))
        root = next(node for node in crate["@graph"] if node.get("@id") == "./")
        root["hasPart"].append({"@id": member["path"]})
        crate["@graph"].append({"@id": member["path"], "@type": "File", "name": "Frozen obligations"})
        crate_bytes = (json.dumps(crate, indent=2) + "\n").encode()
        crate_path.write_bytes(crate_bytes)
        for existing in recipe["members"]:
            if existing["path"] == "ro-crate-metadata.json":
                existing.update(sha256=sha256(crate_bytes), size=len(crate_bytes))
        recipe.pop("expectedPackage", None)
        recipe_path.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
        attempt, plan_sha, _, attempt_id = test_revision.RevisionTests()._attempt(
            directory, recipe_path, '{"primary_results":["failed","failed","failed","failed"]}\n')
        _, receipt, _ = _read_parent_attempt(attempt.read_bytes(), attempt_id)
        result = next(out for out in receipt["outputs"] if out["@id"].endswith("/result.json"))
        coverage = {"schema": "compiled-experiment-coverage/v1", "planSha256": plan_sha,
                    "selectedAttemptId": attempt_id, "frozen": {"path": member["path"], "sha256": member["sha256"]},
                    "resolutions": [{"id": f"primary-{i}", "status": "observed", "attemptId": attempt_id,
                                     "outputPath": result["@id"], "outputSha256": result["sha256"],
                                     "finding": "source reports failure"} for i in range(4)]}
        return attempt, attempt_id, coverage

    def finalize(self, directory, attempt, attempt_id, coverage, name):
        decision = directory / "decision.md"
        review = directory / "review.md"
        decision.write_text("Source decision: negative.\n", encoding="utf-8")
        review.write_text("Synthetic test review.\n", encoding="utf-8")
        coverage_path = directory / f"{name}.json"
        coverage_path.write_text(json.dumps(coverage) + "\n", encoding="utf-8")
        output = directory / f"{name}.zip"
        result = finalize_package(attempt, output, expected_sha256=sha256(attempt.read_bytes()),
            attempt_id=attempt_id, experiment_id=name, title="Synthetic coverage fixture",
            decision_path=decision, decision_summary="Four source-reported failures.",
            review_path=review, review_summary="Synthetic test only.", reviewer_name="Fixture",
            coverage_path=coverage_path)
        return result, output, coverage_path

    def test_missing_diagnostic_and_explained_omission_remain_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage = self.fixture(directory)
            before = attempt.read_bytes()
            result, output, source = self.finalize(directory, attempt, attempt_id, coverage, "missing-diagnostic")
            self.assertEqual(result["coverage"]["campaignAttemptInventory"], "unassessed")
            self.assertFalse(result["coverage"]["sourceAccountedFor"])
            self.assertFalse(result["coverage"]["sourceReportedObservedAsPlanned"])
            self.assertEqual(result["coverage"]["obligations"][-1]["status"], "unreported")
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("evidence/protocol-coverage.json"), source.read_bytes())
            self.assertEqual(attempt.read_bytes(), before)
            self.assertEqual(verify_bytes(output.read_bytes())["scope"], "package-integrity-only")
            profile = subprocess.run([sys.executable, str(ROOT / "scripts/validate_current_ro_profiles.py"),
                                      str(output.with_name(output.stem + ".source"))],
                                     capture_output=True, text=True)
            self.assertEqual(profile.returncode, 0, profile.stdout + profile.stderr)
            self.assertEqual(inspect_coverage(output.read_bytes(), expected_sha256=sha256(output.read_bytes())),
                             result["coverage"])
            report = subprocess.run([sys.executable, "-m", "experiment_compiler", "inspect-coverage", str(output),
                                     "--expected-sha256", sha256(output.read_bytes())],
                                    cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(report.stdout), result["coverage"])
            self.assertEqual(inspect_coverage(before, expected_sha256=sha256(before))["status"], "unassessed")
            parameters = {"parent-package": str(attempt), "expected-sha256": sha256(before),
                          "attempt-id": attempt_id, "id": "component-coverage", "title": "Synthetic",
                          "decision": str(directory / "decision.md"), "decision-summary": "Negative fixture",
                          "review": str(directory / "review.md"), "review-summary": "Synthetic",
                          "reviewer-name": "Fixture", "coverage-record": str(source),
                          "output": str(directory / "component-coverage.zip")}
            component = subprocess.run(component_command("finalize", parameters), cwd=ROOT,
                                       capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(component.stdout)["coverage"], result["coverage"])
            coverage["resolutions"].append({"id": "calibration", "status": "omitted", "reason": "not collected"})
            omitted, _, _ = self.finalize(directory, attempt, attempt_id, coverage, "omitted-diagnostic")
            self.assertTrue(omitted["coverage"]["sourceAccountedFor"])
            self.assertFalse(omitted["coverage"]["sourceReportedObservedAsPlanned"])
            self.assertEqual(omitted["coverage"]["obligations"][-1]["status"], "omitted")
            self.assertEqual(omitted["coverage"]["obligations"][-1]["reason"], "not collected")

    def test_wrong_identity_duplicate_and_unretained_output_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage = self.fixture(directory)
            variants = []
            wrong_plan = copy.deepcopy(coverage); wrong_plan["planSha256"] = "0" * 64; variants.append(wrong_plan)
            wrong_attempt = copy.deepcopy(coverage); wrong_attempt["resolutions"][0]["attemptId"] = "other"; variants.append(wrong_attempt)
            wrong_output = copy.deepcopy(coverage); wrong_output["resolutions"][0]["outputSha256"] = "0" * 64; variants.append(wrong_output)
            duplicate = copy.deepcopy(coverage); duplicate["resolutions"].append(duplicate["resolutions"][0]); variants.append(duplicate)
            wrong_frozen = copy.deepcopy(coverage); wrong_frozen["frozen"]["sha256"] = "0" * 64; variants.append(wrong_frozen)
            unknown = copy.deepcopy(coverage); unknown["resolutions"][0]["id"] = "not-frozen"; variants.append(unknown)
            no_reason = copy.deepcopy(coverage); no_reason["resolutions"].append({"id": "calibration", "status": "omitted", "reason": ""}); variants.append(no_reason)
            for i, value in enumerate(variants):
                with self.subTest(i=i), self.assertRaises(PackageError):
                    self.finalize(directory, attempt, attempt_id, value, f"rejected-{i}")
                self.assertFalse((directory / f"rejected-{i}.zip").exists())

    def test_other_packaged_file_cannot_masquerade_as_frozen_protocol(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage = self.fixture(directory, wrong_protocol=True)
            with self.assertRaisesRegex(PackageError, "different plan protocol"):
                self.finalize(directory, attempt, attempt_id, coverage, "wrong-protocol")
            self.assertFalse((directory / "wrong-protocol.zip").exists())

    def test_duplicate_frozen_obligation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage = self.fixture(directory, duplicate_obligation=True)
            with self.assertRaisesRegex(PackageError, "duplicated"):
                self.finalize(directory, attempt, attempt_id, coverage, "duplicate-obligation")
            self.assertFalse((directory / "duplicate-obligation.zip").exists())

    def test_component_accepts_optional_coverage_without_execution(self):
        # Exercise argument construction without a shell or running an experiment.
        params = {"parent-package": "attempt.zip", "expected-sha256": "a" * 64, "attempt-id": "one",
                  "id": "final", "title": "Synthetic", "decision": "decision.md", "decision-summary": "No claim",
                  "review": "review.md", "review-summary": "Synthetic", "reviewer-name": "Reviewer",
                  "coverage-record": "coverage.json", "output": "final.zip"}
        self.assertIn("--coverage-record=coverage.json", component_command("finalize", params))

    def test_historical_poc_is_unassessed_without_changing_its_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "historical.zip"
            built = compile_package(ROOT / "examples/issue-164/experiment.json", path)
            self.assertEqual(built["packageSha256"], "45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341")
            self.assertEqual(inspect_coverage(path.read_bytes(), expected_sha256=built["packageSha256"]),
                             {"status": "unassessed", "campaignAttemptInventory": "unassessed"})


if __name__ == "__main__":
    unittest.main()
