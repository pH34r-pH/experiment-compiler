"""Repository profile admission preserves identity without granting acceptance."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repository_profile_helpers import composed_records

from experiment_compiler.agent_profiles import SOURCE, projection_fingerprint
from experiment_compiler.core import (
    PackageError,
    canonical,
    compile_package,
    sha256,
    verify_package,
)

ROOT = Path(__file__).resolve().parents[1]


class RepositoryAgentProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packet = json.loads((ROOT / "tests/fixtures/repository-evidence-v1.json").read_bytes())

    def package(self, documents=None, *, audience="public-evidence", references=None, extra=None):
        documents = documents or {"repository-evidence": self.packet}
        members, records = [], []
        for kind, document in documents.items():
            path = "records/" + kind + ".json"
            target = self.root / path
            target.parent.mkdir(exist_ok=True)
            data = canonical(document)
            target.write_bytes(data)
            members.append({"source": path, "path": path, "sha256": sha256(data), "size": len(data)})
            records.append({"kind": kind, "path": path, "fingerprint": projection_fingerprint(document)})
        if extra:
            path, data = extra
            (self.root / path).write_bytes(data)
            members.append({"source": path, "path": path, "sha256": sha256(data), "size": len(data)})
        recipe = {"buildRecipeVersion": 1, "id": "repository-fixture", "title": "Public evidence fixture",
            "profile": "compiled-experiment-v1", "manifest": {
                "schemaVersion": 1, "packageType": "compiled-experiment", "status": "reproducible",
                "profile": "compiled-experiment-v1", "source": SOURCE, "standards": {},
                "evidence": {"repositoryAgent": {"schemaVersion": 1, "source": SOURCE, "audience": audience,
                    "records": records, "references": references or []}}}, "members": members}
        path = self.root / "experiment.json"
        path.write_bytes(canonical(recipe))
        output = self.root / "package.zip"
        return compile_package(path, output), output

    def test_exact_real_upstream_packet_packages_without_execution_or_self_acceptance(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                patch("socket.socket", side_effect=AssertionError("no network")):
            report, package = self.package()
            verified = verify_package(package, expected_sha256=report["packageSha256"])
        self.assertEqual(verified["repositoryAgent"]["scope"], "schema-identity-and-disclosure-only")
        self.assertEqual(verified["repositoryAgent"]["acceptance"], "not-evaluated")
        self.assertEqual(verified["repositoryAgent"]["trainingEligibility"], "not-granted")

    def test_failed_and_invalid_attempts_are_retained_as_typed_evidence(self):
        for outcome, state in (("fail", "FAIL"), ("timeout", "UNKNOWN")):
            with self.subTest(outcome=outcome):
                packet = copy.deepcopy(self.packet)
                packet["checks"][0].update(outcome=outcome, state=state)
                packet["evidence"]["checks"][0].update(state=state, detail="repository check " + outcome)
                packet["evidence"]["verified"].remove("public")
                if state == "UNKNOWN":
                    packet["evidence"]["unresolved"] = ["public"]
                    packet.update(validity="invalid", invalid_reasons=["public:timeout"])
                report, output = self.package({"repository-evidence": packet}, audience="private-evaluator")
                self.assertEqual(report["repositoryAgent"]["acceptance"], "not-evaluated")
                output.unlink()

    def test_legacy_free_text_commands_and_unknown_fields_cannot_leak(self):
        for field, value in (("command", ["secret", "private-stdin"]), ("detail", "private raw log"),
                             ("raw_stdout", "private raw output")):
            with self.subTest(field=field):
                packet = copy.deepcopy(self.packet)
                packet["evidence"]["checks"][0][field] = value
                with self.assertRaises(PackageError):
                    self.package({"repository-evidence": packet})

    def test_stale_candidate_and_unclassified_payload_are_rejected(self):
        self.packet["evidence"]["candidate"]["fingerprint"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(PackageError, "candidate fingerprint"):
            self.package()
        self.packet = json.loads((ROOT / "tests/fixtures/repository-evidence-v1.json").read_bytes())
        with self.assertRaisesRegex(PackageError, "unclassified"):
            self.package(extra=("hidden-test.txt", b"sealed stdin and expected output"))

    def test_private_source_and_worker_outcome_disclosure_fail_closed(self):
        with self.assertRaisesRegex(PackageError, "Worker package"):
            self.package(audience="worker")
        self.packet["candidate"]["source"].update(visibility="private", data_class="personal")
        self.packet["evidence"]["candidate"]["fingerprint"] = projection_fingerprint(self.packet["candidate"])
        with self.assertRaisesRegex(PackageError, "Private repository"):
            self.package()

    def test_trajectory_and_corpus_are_exact_unqualified_references_only(self):
        references = [{"kind": kind, "source": SOURCE, "reference": "urn:fixture:" + kind,
            "digest": "sha256:" + "a" * 64, "qualification": "upstream-pending"}
            for kind in ("semantic-trajectory", "governed-corpus")]
        report, output = self.package(references=references)
        self.assertEqual(report["repositoryAgent"]["references"], references)
        output.unlink()
        references[0]["qualification"] = "training-approved"
        with self.assertRaisesRegex(PackageError, "not qualified"):
            self.package(references=references)

    def test_invalid_trace_fingerprint_and_changed_schema_source_rejected(self):
        self.packet["checks"][0]["stdout_digest"] = "private output disguised as a digest"
        with self.assertRaisesRegex(PackageError, "trace payload"):
            self.package()

    def test_worker_and_private_evaluator_profiles_enforce_disjoint_inventories(self):
        records = composed_records(self.packet)
        worker = {key: value for key, value in records.items()
                  if key not in {"repository-evaluator-sealed", "repository-evidence"}}
        worker = copy.deepcopy(worker)
        worker["repository-task"]["sealed_evaluator"] = None
        public = worker["repository-evaluator-public"]
        public["requirements"] = [item for item in public["requirements"] if item["classification"] != "SEALED"]
        worker["repository-task"]["public_evaluation"]["digest"] = projection_fingerprint(public)
        report, package = self.package(worker, audience="worker")
        self.assertEqual(report["repositoryAgent"]["audience"], "worker")
        package.unlink()
        with self.assertRaises(PackageError):
            self.package({**worker, "repository-evaluator-sealed": records["repository-evaluator-sealed"]}, audience="worker")
        report, package = self.package(records, audience="private-evaluator")
        self.assertEqual(report["repositoryAgent"]["audience"], "private-evaluator")
        package.unlink()
        public_records = {key: value for key, value in records.items() if key != "repository-evaluator-sealed"}
        with self.assertRaisesRegex(PackageError, "Private evaluator commitments"):
            self.package(public_records)

    def test_public_projection_rejects_sealed_checks_and_stale_context(self):
        records = composed_records(self.packet)
        public = {key: value for key, value in records.items() if key not in {"repository-evaluator-sealed", "repository-evidence"}}
        public["repository-context"]["source"]["commit"] = "0" * 40
        with self.assertRaisesRegex(PackageError, "mismatched"):
            self.package(public, audience="worker")
        projection = copy.deepcopy(records["repository-evaluator-public"])
        projection["public_checks"] = [records["repository-evaluator-sealed"]["checks"][1]]
        with self.assertRaisesRegex(PackageError, "sealed check"):
            self.package({"repository-evaluator-public": projection}, audience="worker")
        with patch("experiment_compiler.agent_profiles.SOURCE", {**SOURCE, "commit": "0" * 40}), \
                self.assertRaises(PackageError):
            self.package()
