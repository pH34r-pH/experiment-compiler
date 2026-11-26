"""The optional first coverage slice binds source declarations to actual bytes."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from experiment_compiler.core import PackageError, canonical, compile_package, sha256, verify_bytes
from experiment_compiler.campaign_census import attempt_member
from experiment_compiler.coverage import inspect_coverage
from experiment_compiler.finalization import finalize_package
from experiment_compiler.revision import _read_parent_attempt
from experiment_compiler.lifecycle_components import component_command
import test_revision

ROOT = Path(__file__).resolve().parents[1]


class ProtocolCoverageTests(unittest.TestCase):
    def campaign_fixture(self, directory):
        attempt, attempt_id, coverage = self.fixture(directory)
        separate = directory / "excluded-attempt"
        separate.mkdir()
        excluded, plan_sha256, _, excluded_id = test_revision.RevisionTests()._attempt(
            separate, directory / "fixture/experiment.json", '{"diagnostic":"observed"}\n',
        )
        excluded_bytes = excluded.read_bytes()
        excluded_sha256 = sha256(excluded_bytes)
        (directory / f"{excluded_sha256}.zip").write_bytes(excluded_bytes)
        _, excluded_receipt, _ = _read_parent_attempt(excluded_bytes, excluded_id)
        output = next(entry for entry in excluded_receipt["outputs"] if entry["@id"].endswith("/result.json"))
        coverage["resolutions"].append({
            "id": "calibration", "status": "observed", "attemptId": excluded_id,
            "outputPath": output["@id"], "outputSha256": output["sha256"],
            "finding": "Measured as a diagnostic and kept outside the primary analysis.",
        })
        now = datetime.now(UTC)
        inventory = {
            "campaignId": "synthetic-campaign", "planSha256": plan_sha256, "scope": "complete",
            "cutoff": (now + timedelta(minutes=1)).isoformat(),
            "issued": [
                {"attemptId": identifier, "replicateId": replicate,
                 "issuedAt": (now - timedelta(minutes=minutes)).isoformat(), "retryOf": parent}
                for identifier, replicate, minutes, parent in (
                    ("failed-root", "replicate-a", 4, None),
                    (excluded_id, "replicate-a", 3, "failed-root"),
                    (attempt_id, "replicate-a", 2, excluded_id),
                    ("interrupted-root", "replicate-b", 2, None),
                )
            ],
        }
        outcomes = [
            {"attemptId": "failed-root", "status": "failed", "packageSha256": None,
             "reason": "Source recorded infrastructure failure before a package was available."},
            {"attemptId": excluded_id, "status": "measured-excluded", "packageSha256": excluded_sha256,
             "reason": "Calibration is outside the primary analysis."},
            {"attemptId": attempt_id, "status": "included", "packageSha256": sha256(attempt.read_bytes()),
             "reason": None},
            {"attemptId": "interrupted-root", "status": "interrupted", "packageSha256": None,
             "reason": "Source recorded no terminal package."},
        ]
        coverage.update(
            schema="compiled-experiment-coverage/v2",
            campaign={"inventory": inventory, "inventorySha256": sha256(canonical(inventory)), "outcomes": outcomes},
        )
        return attempt, attempt_id, coverage, excluded_sha256

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

    def test_campaign_checks_and_retains_attempts_without_counting_retries_as_replicates(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage, excluded_digest = self.campaign_fixture(directory)
            report, finalized, _ = self.finalize(directory, attempt, attempt_id, coverage, "campaign-census")
            result = report["coverage"]
            self.assertEqual(result["campaignAttemptInventory"], "complete")
            self.assertEqual(result["campaign"]["campaignDenominator"], 4)
            self.assertEqual(result["campaign"]["recordedReplicates"], 2)
            self.assertEqual(result["campaign"]["recordedRetries"], 2)
            self.assertEqual([item["status"] for item in result["campaign"]["attempts"]],
                             ["failed", "measured-excluded", "included", "interrupted"])
            self.assertEqual(result["obligations"][0]["finding"], "source reports failure")
            self.assertEqual(result["obligations"][-1]["attemptId"], coverage["resolutions"][-1]["attemptId"])
            with zipfile.ZipFile(finalized) as archive:
                self.assertEqual(sha256(archive.read(attempt_member(excluded_digest))), excluded_digest)
            payload = finalized.read_bytes()
            self.assertEqual(inspect_coverage(payload, expected_sha256=sha256(payload)), result)
            readback = subprocess.run([sys.executable, "-m", "experiment_compiler", "inspect-coverage",
                                       str(finalized), "--expected-sha256", sha256(payload)],
                                      cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(readback.stdout), result)
            profile = subprocess.run([sys.executable, str(ROOT / "scripts/validate_current_ro_profiles.py"),
                                      str(finalized.with_name(finalized.stem + ".source"))],
                                     capture_output=True, text=True)
            self.assertEqual(profile.returncode, 0, profile.stdout + profile.stderr)

    def test_partial_inventory_keeps_denominator_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage, _ = self.campaign_fixture(directory)
            campaign = coverage["campaign"]
            campaign["inventory"]["scope"] = "partial"
            del campaign["inventory"]["issued"][0]
            campaign["inventorySha256"] = sha256(canonical(campaign["inventory"]))
            del campaign["outcomes"][0]
            campaign["outcomes"].pop()
            report, _, _ = self.finalize(directory, attempt, attempt_id, coverage, "partial-campaign")
            result = report["coverage"]["campaign"]
            self.assertIsNone(result["campaignDenominator"])
            self.assertEqual(result["recordedIssuedAttempts"], 3)
            self.assertEqual(result["unresolvedRetryParents"], ["failed-root"])
            self.assertEqual(result["attempts"][-1]["status"], "unreported")

    def test_campaign_rejects_invalid_source_and_evidence_relationships(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            attempt, attempt_id, coverage, excluded_digest = self.campaign_fixture(directory)
            cases = (
                ("scope-digest", "wrong plan, scope or digest"),
                ("unaccounted", "unaccounted issued attempts"),
                ("duplicate-outcome", "unissued or duplicate attempt"),
                ("duplicate-issuance", "duplicated or beyond"),
                ("retry", "earlier issued attempt"),
                ("other-plan", "wrong plan, scope or digest"),
                ("misbound-output", "exact campaign-attempt output"),
                ("misbound-package", "ID does not match"),
                ("cutoff", "outside the issued campaign interval"),
            )
            for name, error in cases:
                with self.subTest(name=name):
                    invalid = copy.deepcopy(coverage)
                    campaign = invalid["campaign"]
                    inventory = campaign["inventory"]
                    if name == "unaccounted":
                        campaign["outcomes"].pop()
                    elif name == "duplicate-outcome":
                        campaign["outcomes"].append(campaign["outcomes"][0])
                    elif name == "duplicate-issuance":
                        inventory["issued"].append(inventory["issued"][0])
                    elif name == "retry":
                        inventory["issued"][1]["retryOf"] = "interrupted-root"
                    elif name == "other-plan":
                        inventory["planSha256"] = "a" * 64
                    elif name == "misbound-output":
                        invalid["resolutions"][-1]["outputSha256"] = "a" * 64
                    elif name == "misbound-package":
                        different_digest = sha256(attempt.read_bytes())
                        (directory / f"{different_digest}.zip").write_bytes(attempt.read_bytes())
                        campaign["outcomes"][1]["packageSha256"] = different_digest
                    elif name == "cutoff":
                        inventory["cutoff"] = inventory["issued"][-1]["issuedAt"]
                    campaign["inventorySha256"] = "a" * 64 if name == "scope-digest" else sha256(canonical(inventory))
                    with self.assertRaisesRegex(PackageError, error):
                        self.finalize(directory, attempt, attempt_id, invalid, name)
                    self.assertFalse((directory / f"{name}.zip").exists())
            (directory / f"{excluded_digest}.zip").unlink()
            with self.assertRaisesRegex(PackageError, "Expected regular file"):
                self.finalize(directory, attempt, attempt_id, coverage, "missing-campaign-package")


if __name__ == "__main__":
    unittest.main()
