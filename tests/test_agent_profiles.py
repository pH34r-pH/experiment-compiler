"""Repository profile admission preserves identity without granting acceptance."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from offline_fixture_helpers import offline_operations
from repository_profile_helpers import composed_records, retained_trajectory

from experiment_compiler.agent_profiles import SOURCE, projection_fingerprint
from experiment_compiler.archive_policy import PackageReadPolicy, ResourcePolicyError
from experiment_compiler.core import (
    PackageError,
    canonical,
    compile_package,
    sha256,
    verify_bytes,
    verify_package,
)
from experiment_compiler.corpus import CROISSANT_REFERENCE, CROISSANT_SOURCE, verify_corpus
from experiment_compiler.trajectory import BOUND_QUALIFICATION, TRACE_REFERENCE, TRAJECTORY_SOURCE, verify_trajectory

ROOT = Path(__file__).resolve().parents[1]
HAS_TRAJECTORY_EXTRA = importlib.util.find_spec("long_haul") is not None


def trajectory_reference(fingerprint, digest):
    return {"kind": "semantic-trajectory", "source": TRAJECTORY_SOURCE,
            "reference": f"{TRACE_REFERENCE}/{fingerprint}", "digest": digest,
            "qualification": BOUND_QUALIFICATION}


@unittest.skipUnless(importlib.util.find_spec("jsonschema"), "install the repository extra for profile tests")
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
        with offline_operations():
            report, package = self.package()
            verified = verify_package(package, expected_sha256=report["packageSha256"])
            raw = package.read_bytes()
            self.assertEqual(verify_bytes(raw, expected_sha256=report["packageSha256"]), verified)
        self.assertEqual(verified["repositoryAgent"]["scope"], "schema-identity-and-disclosure-only")
        self.assertEqual(verified["repositoryAgent"]["acceptance"], "not-evaluated")
        self.assertEqual(verified["repositoryAgent"]["trainingEligibility"], "not-granted")

    def test_repository_records_are_budgeted_and_opened_once(self):
        report, package = self.package()
        record = "records/repository-evidence.json"
        with zipfile.ZipFile(package) as archive:
            sizes = {info.filename: info.file_size for info in archive.infolist()}
        original_open = zipfile.ZipFile.open
        opened = []

        def record_open(archive, info):
            opened.append(info.filename)
            return original_open(archive, info)

        with patch.object(zipfile.ZipFile, "open", record_open):
            verified = verify_package(package, expected_sha256=report["packageSha256"],
                                      policy=PackageReadPolicy(max_total_bytes=sum(sizes.values())))
        self.assertEqual(verified["repositoryAgent"], report["repositoryAgent"])
        self.assertCountEqual(opened, sizes.keys())
        with patch.object(zipfile.ZipFile, "open") as opener, self.assertRaises(ResourcePolicyError) as refusal:
            verify_package(package, policy=PackageReadPolicy(max_member_bytes=sizes[record] - 1))
        self.assertEqual(refusal.exception.member, record)
        opener.assert_not_called()

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

    def test_upstream_pending_trajectory_and_corpus_references_are_inert(self):
        references = [{"kind": kind, "source": SOURCE, "reference": "urn:fixture:" + kind,
            "digest": "sha256:" + "a" * 64, "qualification": "upstream-pending"}
            for kind in ("semantic-trajectory", "governed-corpus")]
        report, output = self.package(references=references)
        self.assertEqual(report["repositoryAgent"]["references"], references)
        output.unlink()
        references[0]["qualification"] = "training-approved"
        with self.assertRaisesRegex(PackageError, "not qualified"):
            self.package(references=references)

    def corpus_fixture(self):
        snapshot = self.root / "corpus"
        snapshot.mkdir()
        (snapshot / "case.txt").write_bytes(b"public invented corpus")
        metadata = {
            "@type": "sc:Dataset", "conformsTo": "http://mlcommons.org/croissant/1.1",
            "distribution": [{
                "@id": "files/case.txt", "@type": "cr:FileObject", "name": "case.txt",
                "encodingFormat": "text/plain", "contentUrl": "https://example.invalid/case.txt",
                "sha256": sha256((snapshot / "case.txt").read_bytes()),
            }],
        }
        return snapshot, metadata

    def corpus_package(self, snapshot, metadata):
        raw = canonical(metadata)
        metadata_path = self.root / "croissant.json"
        metadata_path.write_bytes(raw)
        reference = {"kind": "governed-corpus", "source": CROISSANT_SOURCE,
                     "reference": CROISSANT_REFERENCE, "digest": "sha256:" + sha256(raw),
                     "qualification": BOUND_QUALIFICATION}
        report, package = self.package(audience="private-evaluator", references=[reference])
        self.assertEqual(report["repositoryAgent"]["references"], [reference])
        self.assertNotIn("trajectoryBinding", report["repositoryAgent"])
        self.assertEqual(report["repositoryAgent"]["acceptance"], "not-evaluated")
        return (package, metadata_path, snapshot), {
            "expected_sha256": report["packageSha256"], "expected_metadata_sha256": sha256(raw),
            "max_bytes": len(raw) + len(b"public invented corpus"),
        }, reference

    def test_corpus_cli_and_api_verify_the_pinned_local_snapshot(self):
        paths, pins, _ = self.corpus_package(*self.corpus_fixture())
        with offline_operations():
            receipt = verify_corpus(*paths, **pins)
        self.assertEqual(receipt, {
            "schemaVersion": 1, "scope": "corpus-file-integrity-only",
            "packageSha256": pins["expected_sha256"], "metadataSha256": pins["expected_metadata_sha256"],
            "croissantSource": CROISSANT_SOURCE, "fileCount": 1,
            "snapshotSizeBytes": len(b"public invented corpus"),
            "corpusAdmission": "not-granted", "trainingEligibility": "not-granted",
        })
        readback = subprocess.run(
            [sys.executable, "-m", "experiment_compiler", "verify-corpus", *map(str, paths),
             "--expected-sha256", pins["expected_sha256"], "--expected-metadata-sha256",
             pins["expected_metadata_sha256"], "--max-corpus-bytes", str(pins["max_bytes"])],
            cwd=ROOT, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(readback.stdout), receipt)

    def test_corpus_reference_requires_private_scope_reviewed_source_and_unique_digest(self):
        paths, _, reference = self.corpus_package(*self.corpus_fixture())
        paths[0].unlink()
        with self.assertRaisesRegex(PackageError, "private-evaluator"):
            self.package(references=[reference])
        with self.assertRaisesRegex(PackageError, "readback identity"):
            self.package(audience="private-evaluator", references=[dict(reference, source=SOURCE)])
        with self.assertRaisesRegex(PackageError, "Duplicate bound corpus"):
            self.package(audience="private-evaluator", references=[reference, reference])

    def test_corpus_readback_checks_pins_and_budget_before_issuing_a_receipt(self):
        paths, pins, _ = self.corpus_package(*self.corpus_fixture())
        with self.assertRaisesRegex(PackageError, "[Pp]ackage.*(digest|SHA-256)"):
            verify_corpus(*paths, **dict(pins, expected_sha256="0" * 64))
        with self.assertRaisesRegex(PackageError, "lacks the bound Croissant"):
            verify_corpus(*paths, **dict(pins, expected_metadata_sha256="0" * 64))
        with self.assertRaisesRegex(PackageError, "read budget"):
            verify_corpus(*paths, **dict(pins, max_bytes=pins["max_bytes"] - 1))
        paths[1].write_bytes(paths[1].read_bytes() + b" ")
        with self.assertRaisesRegex(PackageError, "independently reviewed digest"):
            verify_corpus(*paths, **dict(pins, max_bytes=pins["max_bytes"] + 1))

    def test_corpus_readback_rejects_missing_extra_and_substituted_files(self):
        paths, pins, _ = self.corpus_package(*self.corpus_fixture())
        snapshot = paths[2]
        extra = snapshot / "unlisted.txt"
        extra.write_bytes(b"private unlisted data")
        with self.assertRaisesRegex(PackageError, "exact regular-file inventory"):
            verify_corpus(*paths, **pins)
        extra.unlink()
        path = snapshot / "case.txt"
        path.unlink()
        with self.assertRaisesRegex(PackageError, "exact regular-file inventory"):
            verify_corpus(*paths, **pins)
        path.write_bytes(b"substituted corpus")
        with self.assertRaisesRegex(PackageError, "reviewed digest"):
            verify_corpus(*paths, **pins)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(PackageError, "exact regular-file inventory"):
            verify_corpus(*paths, **pins)

    def test_corpus_projection_refuses_nonportable_and_case_colliding_files(self):
        snapshot, metadata = self.corpus_fixture()
        record = metadata["distribution"][0]
        metadata["distribution"].append(dict(record, name="Case.txt", **{"@id": "files/Case.txt"}))
        paths, pins, _ = self.corpus_package(snapshot, metadata)
        with self.assertRaisesRegex(PackageError, "unique portable"):
            verify_corpus(*paths, **pins)
        paths[0].unlink()
        metadata["distribution"][1].update(name="CON.txt", **{"@id": "files/CON.txt"})
        paths, pins, _ = self.corpus_package(snapshot, metadata)
        with self.assertRaisesRegex(PackageError, "unique portable"):
            verify_corpus(*paths, **pins)

    def test_corpus_readback_rejects_a_link_even_with_matching_bytes(self):
        paths, pins, _ = self.corpus_package(*self.corpus_fixture())
        data = paths[2] / "case.txt"
        moved = self.root / "same-content.txt"
        data.rename(moved)
        try:
            data.symlink_to(moved)
        except (NotImplementedError, PermissionError):
            self.skipTest("symbolic links unavailable")
        with self.assertRaisesRegex(PackageError, "exact regular-file inventory|linked paths"):
            verify_corpus(*paths, **pins)

    def test_bound_trajectory_references_require_private_task_and_pinned_source(self):
        fingerprint = "sha256:" + "a" * 64
        bound = trajectory_reference(fingerprint, fingerprint)
        with self.assertRaisesRegex(PackageError, "private-evaluator"):
            self.package(references=[bound])
        with self.assertRaisesRegex(PackageError, "repository-task"):
            self.package(audience="private-evaluator", references=[bound])
        task = composed_records(self.packet)["repository-task"]
        with self.assertRaisesRegex(PackageError, "readback identity"):
            self.package({"repository-task": task}, audience="private-evaluator",
                         references=[dict(bound, source=SOURCE)])

    def trajectory_package(self, terminal="settled", *, repository_id=None, candidate_record=None,
                           result_candidate=None, reference_digest=None):
        task = composed_records(self.packet)["repository-task"]
        result, trace = retained_trajectory(task, terminal, repository_id=repository_id, candidate=result_candidate)
        result_path, trace_path = self.root / "result.json", self.root / "trace.jsonl"
        result_path.write_bytes(result.model_dump_json().encode())
        trace_path.write_bytes(trace)
        documents = {"repository-task": task}
        if candidate_record:
            documents["repository-candidate"] = candidate_record
        report, archive = self.package(documents, audience="private-evaluator",
                                       references=[trajectory_reference(result.fingerprint, reference_digest or result.trace.digest)])
        self.assertEqual(report["repositoryAgent"]["scope"], "schema-identity-and-disclosure-only")
        return (archive, result_path, trace_path), {
            "expected_sha256": report["packageSha256"], "expected_result_fingerprint": result.fingerprint,
            "max_bytes": len(result_path.read_bytes()) + len(trace),
        }

    @unittest.skipUnless(HAS_TRAJECTORY_EXTRA, "install the pinned trajectory extra")
    def test_verified_trajectory_reports_exact_attempt_without_running_package(self):
        with offline_operations():
            paths, pins = self.trajectory_package()
            receipt = verify_trajectory(*paths, **pins)
        self.assertEqual(receipt["eventCount"], 1)
        self.assertEqual(receipt["terminal"], "settled")
        self.assertEqual(receipt["acceptance"], "not_evaluated")
        self.assertEqual(receipt["trainingEligibility"], "not-granted")
        self.assertEqual(receipt["resultFingerprint"], pins["expected_result_fingerprint"])
        self.assertEqual(receipt["traceDigest"], "sha256:" + sha256(paths[2].read_bytes()))
        self.assertEqual(receipt["packageSha256"], pins["expected_sha256"])

    @unittest.skipUnless(HAS_TRAJECTORY_EXTRA, "install the pinned trajectory extra")
    def test_trajectory_command_retains_a_zero_event_provider_failure(self):
        paths, pins = self.trajectory_package("provider_failure")
        command = [sys.executable, "-m", "experiment_compiler", "verify-trajectory", *(str(path) for path in paths),
                   "--expected-sha256", pins["expected_sha256"], "--expected-result-fingerprint",
                   pins["expected_result_fingerprint"], "--max-trajectory-bytes", str(pins["max_bytes"])]
        completed = subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["terminal"], "provider_failure")
        self.assertEqual(receipt["eventCount"], 0)
        self.assertEqual(receipt["candidateDisposition"], "none")
        self.assertEqual(receipt["verifierSource"], TRAJECTORY_SOURCE)
        with self.assertRaisesRegex(PackageError, "one bound reference"):
            verify_trajectory(*paths, **dict(pins, expected_result_fingerprint="sha256:" + "0" * 64))
        task = composed_records(self.packet)["repository-task"]
        alternate, trace = retained_trajectory(task, "provider_failure", repository_id="other-repository")
        self.assertEqual(trace, paths[2].read_bytes())
        paths[1].write_bytes(alternate.model_dump_json().encode())
        with self.assertRaisesRegex(PackageError, "verifier rejected"):
            verify_trajectory(*paths, **dict(pins, max_bytes=paths[1].stat().st_size))

    @unittest.skipUnless(HAS_TRAJECTORY_EXTRA, "install the pinned trajectory extra")
    def test_trajectory_readback_rejects_changed_bytes_and_independent_pin_mismatches(self):
        paths, pins = self.trajectory_package()
        with self.assertRaisesRegex(PackageError, "Package SHA-256 mismatch"):
            verify_trajectory(*paths, **dict(pins, expected_sha256="0" * 64))
        with self.assertRaisesRegex(PackageError, "resource budget"):
            verify_trajectory(*paths, **dict(pins, max_bytes=pins["max_bytes"] - 1))
        trace = paths[2].read_bytes()
        self.assertIn(b'"sequence":1', trace)
        paths[2].write_bytes(trace.replace(b'"sequence":1', b'"sequence":2'))
        with self.assertRaisesRegex(PackageError, "verifier rejected"):
            verify_trajectory(*paths, **pins)

    @unittest.skipUnless(HAS_TRAJECTORY_EXTRA, "install the pinned trajectory extra")
    def test_valid_trajectory_cannot_attach_to_different_repository_task(self):
        paths, pins = self.trajectory_package(repository_id="different-repository")
        with self.assertRaisesRegex(PackageError, "different repository task"):
            verify_trajectory(*paths, **pins)

    @unittest.skipUnless(HAS_TRAJECTORY_EXTRA, "install the pinned trajectory extra")
    def test_verified_result_must_agree_with_package_trace_digest(self):
        paths, pins = self.trajectory_package(reference_digest="sha256:" + "a" * 64)
        with self.assertRaisesRegex(PackageError, "package trace digest"):
            verify_trajectory(*paths, **pins)

    @unittest.skipUnless(HAS_TRAJECTORY_EXTRA, "install the pinned trajectory extra")
    def test_bound_candidate_matches_canonical_harness_candidate_identity(self):
        from long_haul.work.repository_models import DigestReference

        candidate = self.packet["candidate"]
        digest = projection_fingerprint(candidate)
        result_candidate = DigestReference(reference="artifact:frozen-worker-candidate", digest=digest)
        paths, pins = self.trajectory_package(candidate_record=candidate, result_candidate=result_candidate)
        self.assertEqual(verify_trajectory(*paths, **pins)["candidateDisposition"], "frozen")
        paths[0].unlink()
        different = result_candidate.model_copy(update={"digest": "sha256:" + "0" * 64})
        paths, pins = self.trajectory_package(candidate_record=candidate, result_candidate=different)
        with self.assertRaisesRegex(PackageError, "different frozen candidate"):
            verify_trajectory(*paths, **pins)

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
