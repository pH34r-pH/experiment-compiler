import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.core import PackageError, canonical, compile_package, json_value, load_recipe, verify_bytes
from experiment_compiler.catalog import describe_catalog
from experiment_compiler.runner import (_CwlCommandRequest, _CwlExecutionRequest, _admit_workflow, _collect_execution_tree, _collect_tree, _execute_cwl,
                                        _cleanup_container_journal, _require_bounded_output_tmpfs,
                                        _require_bounded_tmpfs, _run_command, _terminate_process_group,
                                        _new_container_journal, _container_present, _validate_recovery_worker,
                                        _engine_image_reference_matches,
                                        _admit_container_requirement, _persist_attempt_receipt,
                                        _write_container_journal, _journal_receipt_reference,
                                        _augment_result_crate,
                                        recover_attempt, run_package)
from experiment_compiler.runner import sha256_bytes
from runner_fixture_helpers import prepare_container_journal, prepare_recovery_attempt

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
WORKER_OUTPUT_BUDGET = 64 * MIB


class RunnerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.process_identity_patch = patch(
            "experiment_compiler.runner._cwl_process_identity",
            return_value={"pid": 98765, "bootId": "fixture", "processStartTicks": "1",
                          "processGroupId": 98765, "sessionId": 98765},
        )
        self.process_identity_patch.start()
        self.addCleanup(self.process_identity_patch.stop)
        self.process_group_patch = patch("experiment_compiler.runner._process_group_exists", return_value=False)
        self.process_group_patch.start()
        self.addCleanup(self.process_group_patch.stop)
        self.engine_journal_patch = patch(
            "experiment_compiler.runner._new_container_journal",
            side_effect=self._fixture_container_journal,
        )
        self.engine_journal_patch.start()
        self.addCleanup(self.engine_journal_patch.stop)

    def _fixture_container_journal(self, attempt_directory, attempt_id, runner):
        return prepare_container_journal(attempt_directory, attempt_id, runner)

    def test_engine_image_ids_accept_only_complete_sha256_and_canonicalize_podman_output(self):
        from experiment_compiler.runner import _normalize_engine_image_id
        image_id = "a" * 64
        self.assertEqual(_normalize_engine_image_id(image_id), "sha256:" + image_id)
        self.assertEqual(_normalize_engine_image_id("sha256:" + image_id.upper()), "sha256:" + image_id)
        for invalid in ("a" * 63, "a" * 65, "sha256:" + "a" * 63,
                        "sha512:" + "a" * 64, "sha256:" + "g" * 64, "shortid"):
            with self.subTest(invalid=invalid):
                self.assertIsNone(_normalize_engine_image_id(invalid))

    def test_engine_config_reference_allows_only_same_digest_repo_with_tag_elision(self):
        pinned = "docker.io/pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime@sha256:" + "d" * 64
        elided = "docker.io/pytorch/pytorch@sha256:" + "d" * 64
        self.assertTrue(_engine_image_reference_matches(pinned, pinned))
        self.assertTrue(_engine_image_reference_matches(elided, pinned))
        rejected = (
            "docker.io/other/pytorch@sha256:" + "d" * 64,
            "docker.io/pytorch/pytorch@sha256:" + "e" * 64,
            "docker.io/pytorch/pytorch:latest@sha256:" + "d" * 64,
            "docker.io/pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime",
            "docker.io/pytorch/pytorch@sha512:" + "d" * 64,
            "docker.io/pytorch/pytorch@sha256:short",
        )
        for observed in rejected:
            with self.subTest(observed=observed):
                self.assertFalse(_engine_image_reference_matches(observed, pinned))
        mutable = "docker.io/pytorch/pytorch:latest"
        self.assertFalse(_engine_image_reference_matches(mutable, mutable))

    def test_container_journal_normalizes_podman_bare_image_id_and_rejects_invalid_ids(self):
        from types import SimpleNamespace
        image_ref = "registry.example/tool@sha256:" + "d" * 64
        runner = {"executionMode": "cwltool-podman", "workerImageBase": image_ref}
        image_id = "a" * 64
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "attempt"
            attempt.mkdir(mode=0o700)
            with patch("experiment_compiler.runner.shutil.which", return_value=sys.executable), \
                    patch("experiment_compiler.runner._engine_call",
                          return_value=SimpleNamespace(returncode=0, stdout=image_id + "\n")):
                journal = _new_container_journal(attempt, "0123456789ab", runner)
            self.assertEqual(journal["workerImageRef"], image_ref)
            self.assertEqual(journal["workerImageId"], "sha256:" + image_id)
        for invalid in ("a" * 63, "sha512:" + image_id):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as value:
                attempt = Path(value) / "attempt"
                attempt.mkdir(mode=0o700)
                with patch("experiment_compiler.runner.shutil.which", return_value=sys.executable), \
                        patch("experiment_compiler.runner._engine_call",
                              return_value=SimpleNamespace(returncode=0, stdout=invalid)):
                    with self.assertRaisesRegex(PackageError, "Pinned workflow image"):
                        _new_container_journal(attempt, "0123456789ab", runner)

    def test_container_inspect_and_recovery_compare_canonical_image_id(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        image_ref = "registry.example/tool@sha256:" + "d" * 64
        image_id = "sha256:" + "a" * 64
        cid = "b" * 64
        journal = {"engine": "podman", "enginePath": sys.executable,
                   "workerImageRef": image_ref, "workerImageId": image_id}
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value)
            bare_inspect = f"{cid} running {'a' * 64} {image_ref}\n"
            with patch("experiment_compiler.runner._engine_call",
                       return_value=SimpleNamespace(returncode=0, stdout=bare_inspect)):
                self.assertEqual(_container_present(attempt, journal, cid), (True, "running"))
            tag_elided_inspect = f"{cid} running {'a' * 64} registry.example/tool@sha256:{'d' * 64}\n"
            with patch("experiment_compiler.runner._engine_call",
                       return_value=SimpleNamespace(returncode=0, stdout=tag_elided_inspect)):
                self.assertEqual(_container_present(attempt, journal, cid), (True, "running"))
            wrong_inspect = f"{cid} running {'c' * 64} {image_ref}\n"
            with patch("experiment_compiler.runner._engine_call",
                       return_value=SimpleNamespace(returncode=0, stdout=wrong_inspect)):
                with self.assertRaisesRegex(PackageError, "differs from the pinned"):
                    _container_present(attempt, journal, cid)

            journal.update({"schema": "cwltool-container-journal/v1", "attemptId": "0123456789ab",
                            "reportedWorkerImageId": "sha256:" + "e" * 64, "state": "running",
                            "createdAt": datetime.now(timezone.utc).isoformat(), "containers": []})
            receipt = {"status": "started", "planPackageSha256": "f" * 64,
                       "runner": {"name": "cwltool", "workerImageBase": image_ref},
                       "workerContext": {"reportedImageId": journal["reportedWorkerImageId"]},
                       "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime"}
            with patch("experiment_compiler.runner._validate_recovery_age_and_controller"), \
                    patch("experiment_compiler.runner._validate_recovery_container_inventory"), \
                    patch("experiment_compiler.runner._engine_call",
                          return_value=SimpleNamespace(returncode=0, stdout="a" * 64 + "\n")):
                _validate_recovery_worker(attempt, receipt, journal)
            with patch("experiment_compiler.runner._validate_recovery_age_and_controller"), \
                    patch("experiment_compiler.runner._validate_recovery_container_inventory"), \
                    patch("experiment_compiler.runner._engine_call",
                          return_value=SimpleNamespace(returncode=0, stdout="c" * 64 + "\n")):
                with self.assertRaisesRegex(PackageError, "exact retained workflow image ID"):
                    _validate_recovery_worker(attempt, receipt, journal)

    def test_wrong_digest_reference_is_still_rejected(self):
        wrong = "registry.example/tool@sha256:" + "b" * 64
        expected = "registry.example/tool@sha256:" + "a" * 64
        with self.assertRaisesRegex(PackageError, "digest-pinned Docker image"):
            _admit_container_requirement({"DockerRequirement": {"dockerPull": wrong}}, expected, False)

    @unittest.skipUnless(os.name == "posix", "private receipt mode is a POSIX worker contract")
    def test_attempt_receipt_is_private_even_under_permissive_umask(self):
        import stat
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "attempt"
            attempt.mkdir(mode=0o700)
            original = os.umask(0)
            try:
                _persist_attempt_receipt(attempt, {"status": "started"})
            finally:
                os.umask(original)
            receipt = attempt / "runner-receipt.json"
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            self.assertEqual(json.loads(receipt.read_text()), {"status": "started"})

    @unittest.skipUnless(os.name == "posix", "private receipt ownership is a POSIX worker contract")
    def test_attempt_receipt_refuses_preexisting_pending_symlink_without_overwriting_target(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            attempt = root / "attempt"
            attempt.mkdir(mode=0o700)
            outside = root / "outside.json"
            outside.write_text("foreign", encoding="utf-8")
            (attempt / "runner-receipt.json.tmp").symlink_to(outside)
            with self.assertRaisesRegex(PackageError, "not a regular file"):
                _persist_attempt_receipt(attempt, {"status": "failed"})
            self.assertEqual(outside.read_text(encoding="utf-8"), "foreign")

    @unittest.skipUnless(os.name == "posix", "private receipt ownership is a POSIX worker contract")
    def test_attempt_receipt_refuses_permissive_existing_target(self):
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "attempt"
            attempt.mkdir(mode=0o700)
            receipt = attempt / "runner-receipt.json"
            receipt.write_text('{"status":"old"}', encoding="utf-8")
            receipt.chmod(0o644)
            with self.assertRaisesRegex(PackageError, "mode 0600"):
                _persist_attempt_receipt(attempt, {"status": "failed"})
            self.assertEqual(receipt.read_text(encoding="utf-8"), '{"status":"old"}')

    @unittest.skipUnless(os.name == "posix", "attempt recovery and private files are POSIX worker contracts")
    def test_receipt_fsync_before_rename_is_recoverable_with_exact_journal_transaction(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        attempt_id = "0123456789ab"
        image_ref = "registry.example/tool@sha256:" + "d" * 64
        image_id = "sha256:" + "a" * 64
        reported_id = "sha256:" + "e" * 64
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "attempt"
            attempt.mkdir(mode=0o700)
            (attempt / "engine-cids").mkdir(mode=0o700)
            (attempt / "runner-home").mkdir(mode=0o700)
            journal = {
                "schema": "cwltool-container-journal/v1", "attemptId": attempt_id,
                "engine": "podman", "enginePath": sys.executable,
                "workerImageRef": image_ref, "workerImageId": image_id,
                "reportedWorkerImageId": reported_id,
                "controller": {"pid": -1, "bootId": None, "processStartTicks": None},
                "state": "prepared", "launchIntentPersisted": False,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "cidfilePrefix": f"{attempt_id}-", "containers": [], "cancellation": None,
            }
            journal_digest = _write_container_journal(attempt, journal)
            receipt = {
                "schemaVersion": 1, "attemptId": attempt_id, "status": "started",
                "planPackageSha256": "f" * 64,
                "runner": {"name": "cwltool", "version": "3.2.20260720092025", "workerImageBase": image_ref},
                "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
                "workerContext": {"reportedImageId": reported_id}, "collectionErrors": [],
                "containerJournal": {"path": "container-journal.json", "sha256": journal_digest},
            }
            _persist_attempt_receipt(attempt, receipt)
            original_replace = Path.replace

            def fail_receipt_rename(source, target):
                if source.name.startswith("runner-receipt.json.tmp."):
                    raise OSError("fixture crash after receipt fsync, before atomic rename")
                return original_replace(source, target)

            journal.update(state="running", launchIntentPersisted=True)
            with patch.object(Path, "replace", new=fail_receipt_rename):
                with self.assertRaisesRegex(OSError, "after receipt fsync"):
                    _journal_receipt_reference(attempt, journal, receipt)
            abandoned = list(attempt.glob("runner-receipt.json.tmp.*"))
            self.assertEqual(len(abandoned), 1)
            self.assertEqual((attempt / "runner-receipt.json").stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(
                json.loads((attempt / "runner-receipt.json").read_text())["containerJournal"]["sha256"],
                sha256_bytes((attempt / "container-journal.json").read_bytes()),
            )

            with patch("experiment_compiler.runner._validate_recovery_age_and_controller"), \
                    patch("experiment_compiler.runner._reconcile_cwl_process_group"), \
                    patch("experiment_compiler.runner._engine_call",
                          return_value=SimpleNamespace(returncode=0, stdout="a" * 64 + "\n")):
                recovered = recover_attempt(attempt)
            self.assertEqual(recovered["containerCleanup"]["state"], "unknown")
            self.assertEqual(len(list(attempt.glob("runner-receipt.json.tmp.*"))), 1)
            latest_journal = (attempt / "container-journal.json").read_bytes()
            latest_receipt = json.loads((attempt / "runner-receipt.json").read_text())
            self.assertEqual(latest_receipt["containerJournal"]["sha256"], sha256_bytes(latest_journal))
            self.assertEqual((attempt / "runner-receipt.json").stat().st_mode & 0o777, 0o600)

            outside = Path(value) / "outside.json"
            outside.write_text("preserved", encoding="utf-8")
            (attempt / "runner-receipt.json.tmp.foreign").symlink_to(outside)
            with self.assertRaisesRegex(PackageError, "not a regular file"):
                recover_attempt(attempt)
            self.assertEqual(outside.read_text(encoding="utf-8"), "preserved")

    @unittest.skipUnless(os.name == "posix", "attempt ownership and recovery are POSIX worker contracts")
    def test_recovery_accepts_only_typed_preflight_not_started_receipt(self):
        from experiment_compiler.runner import _persist_attempt_receipt
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "result.attempt"
            attempt.mkdir(mode=0o700)
            (attempt / "engine-cids").mkdir(mode=0o700)
            receipt = {
                "attemptId": "0123456789ab", "status": "failed",
                "planPackageSha256": "a" * 64,
                "runner": {"name": "cwltool"},
                "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
                "executionPhase": "not-started", "executionNeverStarted": True,
                "launchIntentPersisted": False,
                "containerCleanup": {"state": "not-started", "engine": "podman",
                                     "containerCount": 0, "absentCount": 0, "allAbsent": False},
            }
            _persist_attempt_receipt(attempt, receipt)
            recovered = recover_attempt(attempt)
            self.assertEqual(recovered["containerJournal"], {
                "availability": "not-applicable-not-started", "storePath": None, "sha256": None,
            })
            self.assertTrue(recovered["executionNeverStarted"])
            receipt["executionNeverStarted"] = False
            _persist_attempt_receipt(attempt, receipt)
            with self.assertRaisesRegex(PackageError, "typed preflight"):
                recover_attempt(attempt)

    @unittest.skipUnless(os.name == "posix", "attempt ownership and recovery are POSIX worker contracts")
    def test_recovery_rejects_missing_journal_without_typed_no_start_receipt(self):
        from experiment_compiler.runner import _persist_attempt_receipt
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "result.attempt"
            attempt.mkdir(mode=0o700)
            (attempt / "engine-cids").mkdir(mode=0o700)
            _persist_attempt_receipt(attempt, {"attemptId": "0123456789ab", "status": "started"})
            with self.assertRaisesRegex(PackageError, "typed preflight"):
                recover_attempt(attempt)

    @unittest.skipUnless(os.name == "posix", "attempt ownership and recovery are POSIX worker contracts")
    def test_launch_intent_recovery_without_cid_stays_unknown(self):
        from types import SimpleNamespace
        attempt_id = "0123456789ab"
        image_id = "sha256:" + "b" * 64
        image_ref = "python:3.13.13-slim@sha256:" + "d" * 64
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "result.attempt"
            prepare_recovery_attempt(
                attempt, attempt_id=attempt_id, image_ref=image_ref, image_id=image_id,
                reported_image_id="sha256:" + "e" * 64,
                controller={"pid": -1, "bootId": None, "processStartTicks": None},
            )
            with patch("experiment_compiler.runner._controller_is_alive"), \
                    patch("experiment_compiler.runner._reconcile_cwl_process_group"), \
                    patch("experiment_compiler.runner._engine_call",
                          return_value=SimpleNamespace(returncode=0, stdout=image_id)):
                recovered = recover_attempt(attempt)
            self.assertEqual(recovered["containerCleanup"]["state"], "unknown")
            self.assertFalse(recovered["containerCleanup"]["allAbsent"])

    @unittest.skipUnless(os.name == "posix", "attempt ownership and recovery are POSIX worker contracts")
    def test_recovery_after_kernel_reboot_never_signals_reused_pid_and_cleans_exact_cid(self):
        from types import SimpleNamespace

        container_id = "c" * 64
        image_id = "sha256:" + "b" * 64
        image_ref = "python:3.13.13-slim@sha256:" + "d" * 64

        current_boot = uuid.uuid4()
        old_boot = uuid.uuid4()
        while current_boot == old_boot:
            old_boot = uuid.uuid4()
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "result.attempt"
            prepare_recovery_attempt(
                attempt, attempt_id="0123456789ab", image_ref=image_ref,
                image_id=image_id, reported_image_id="sha256:" + "e" * 64,
                controller={"pid": 1234, "bootId": str(old_boot), "processStartTicks": "10"},
                cwl_boot_id=str(old_boot), container_id=container_id,
            )
            container_inspect_count = 0

            def engine_call(_attempt, _journal, arguments):
                nonlocal container_inspect_count
                if arguments[:2] == ["image", "inspect"]:
                    return SimpleNamespace(returncode=0, stdout=image_id)
                if arguments[0] == "inspect":
                    container_inspect_count += 1
                    if container_inspect_count == 1:
                        return SimpleNamespace(
                            returncode=0,
                            stdout=f"{container_id} exited {image_id} {image_ref}",
                        )
                    return SimpleNamespace(returncode=1, stdout="")
                if arguments[0] == "ps":
                    return SimpleNamespace(returncode=0, stdout="")
                return SimpleNamespace(returncode=0, stdout="")

            with patch("experiment_compiler.runner._kernel_boot_uuid", return_value=current_boot), \
                    patch("experiment_compiler.runner._controller_is_alive"), \
                    patch("experiment_compiler.runner._current_cwl_process_fields",
                          side_effect=AssertionError("must not inspect a potentially reused PID")), \
                    patch("experiment_compiler.runner._signal_retained_cwl_group",
                          side_effect=AssertionError("must not signal a potentially reused process group")), \
                    patch("experiment_compiler.runner._engine_call", side_effect=engine_call) as engine:
                recovered = recover_attempt(attempt)

            self.assertEqual(recovered["containerCleanup"]["state"], "clean")
            self.assertTrue(recovered["containerCleanup"]["allAbsent"])
            self.assertIn(["rm", container_id], [call.args[2] for call in engine.call_args_list])
            self.assertTrue(any(call.args[2][-1] == container_id for call in engine.call_args_list
                                if call.args[2][0] == "inspect"))

    def test_same_boot_cwl_pid_reuse_still_refuses_to_signal(self):
        from experiment_compiler.runner import _reconcile_cwl_process_group

        boot_id = uuid.uuid4()
        journal = {"cwlProcessIdentity": {
            "pid": 2345, "bootId": str(boot_id), "processStartTicks": "20",
            "processGroupId": 2345, "sessionId": 2345,
        }}
        fields = ["S", "1", "2345", "2345"] + ["0"] * 15 + ["21"]
        with patch("experiment_compiler.runner._kernel_boot_uuid", return_value=boot_id), \
                patch("experiment_compiler.runner._current_cwl_process_fields", return_value=fields), \
                patch("experiment_compiler.runner._signal_retained_cwl_group") as signal_group, \
                self.assertRaisesRegex(PackageError, "PID has been reused"):
            _reconcile_cwl_process_group(journal)
        signal_group.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "attempt ownership and recovery are POSIX worker contracts")
    def test_recovery_invalid_process_boot_identity_stays_unknown_and_does_not_cleanup(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "result.attempt"
            prepare_recovery_attempt(
                attempt, attempt_id="0123456789ab",
                image_ref="python:3.13.13-slim@sha256:" + "d" * 64,
                image_id="sha256:" + "b" * 64, reported_image_id="sha256:" + "e" * 64,
                controller={"pid": 1234, "bootId": str(uuid.uuid4()), "processStartTicks": "10"},
                cwl_boot_id="not-a-uuid", container_id="c" * 64,
            )
            with patch("experiment_compiler.runner._controller_is_alive"), \
                    patch("experiment_compiler.runner._engine_call",
                          return_value=SimpleNamespace(returncode=0, stdout="sha256:" + "b" * 64)) as engine:
                recovered = recover_attempt(attempt)
            self.assertEqual(recovered["containerCleanup"]["state"], "unknown")
            self.assertFalse(recovered["containerCleanup"]["allAbsent"])
            self.assertEqual(engine.call_args.args[2][:2], ["image", "inspect"])

    def test_cwltool_command_keeps_engine_ids_for_controller_reconciliation(self):
        temporary = Path("/scratch/run")
        command = _run_command(_CwlCommandRequest(
            temporary, temporary / "package", temporary / "outputs", temporary / "provenance",
            {"executionMode": "cwltool-podman"}, Path("/attempt/engine-cids"),
            "0123456789ab-", Path("/output/cwl-out-"),
        ))
        self.assertIn("--leave-container", command)
        self.assertEqual(Path(command[command.index("--cidfile-dir") + 1]), Path("/attempt/engine-cids"))
        self.assertEqual(command[command.index("--cidfile-prefix") + 1], "0123456789ab-")
        self.assertEqual(Path(command[command.index("--tmp-outdir-prefix") + 1]), Path("/output/cwl-out-"))

    def test_container_cleanup_is_bounded_and_records_proven_absence(self):
        from types import SimpleNamespace
        container_id = "a" * 64
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value)
            (attempt / "engine-cids").mkdir()
            (attempt / "engine-cids/0123456789ab-job.cid").write_text(container_id + "\n")
            journal = {
                "schema": "cwltool-container-journal/v1", "attemptId": "0123456789ab",
                "engine": "podman", "enginePath": "/usr/bin/podman", "workerImageRef": "python@sha256:x",
                "workerImageId": "sha256:" + "b" * 64, "state": "running",
                "cidfilePrefix": "0123456789ab-", "containers": [],
            }
            receipt = {}
            states = iter(((True, "running"), (True, "exited"), (False, None)))
            with patch("experiment_compiler.runner._container_present", side_effect=lambda *_: next(states)), \
                    patch("experiment_compiler.runner._engine_call", return_value=SimpleNamespace(returncode=0)) as engine:
                errors = []
                _cleanup_container_journal(attempt, journal, receipt, errors)
            self.assertEqual(errors, [])
            self.assertEqual(journal["state"], "clean")
            self.assertEqual(journal["containers"][0]["cleanup"]["absentAfterCleanup"], True)
            self.assertEqual([entry.args[2] for entry in engine.call_args_list], [
                ["stop", "--time", "5", container_id], ["rm", container_id],
            ])

    @unittest.skipUnless(os.name == "posix", "attempt ownership and recovery are POSIX worker contracts")
    def test_hard_loss_recovery_discovers_unjournaled_cidfile_and_binds_receipt(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from experiment_compiler.runner import _persist_attempt_receipt
        attempt_id = "0123456789ab"
        container_id = "c" * 64
        image_id = "sha256:" + "b" * 64
        image_ref = "python:3.13.13-slim@sha256:" + "d" * 64
        with tempfile.TemporaryDirectory() as value:
            attempt = Path(value) / "result.attempt"
            attempt.mkdir(mode=0o700)
            cid_directory = attempt / "engine-cids"
            cid_directory.mkdir(mode=0o700)
            (attempt / "runner-home").mkdir(mode=0o700)
            (cid_directory / f"{attempt_id}-timestamp.cid").write_text(container_id + "\n")
            journal = {
                "schema": "cwltool-container-journal/v1", "attemptId": attempt_id,
                "engine": "podman", "enginePath": "/usr/bin/podman",
                "workerImageRef": image_ref, "workerImageId": image_id,
                "reportedWorkerImageId": "sha256:" + "e" * 64,
                "controller": {"pid": -1, "bootId": None, "processStartTicks": None},
                "state": "running", "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "cidfilePrefix": f"{attempt_id}-", "containers": [], "cancellation": None,
            }
            journal_bytes = canonical(journal)
            (attempt / "container-journal.json").write_bytes(journal_bytes)
            receipt = {
                "schemaVersion": 1, "attemptId": attempt_id, "status": "started",
                "planPackageSha256": "f" * 64,
                "runner": {"name": "cwltool", "version": "3.2.20260720092025", "workerImageBase": image_ref},
                "executionMode": "cwltool orchestrator on worker; CWL CommandLineTool uses Podman runtime",
                "workerContext": {"reportedImageId": journal["reportedWorkerImageId"]},
                "collectionErrors": [],
                "containerJournal": {"path": "container-journal.json", "sha256": "0" * 64},
            }
            _persist_attempt_receipt(attempt, receipt)
            (attempt / "container-journal.pending").write_bytes(canonical({
                "schema": "cwltool-container-journal-transaction/v1", "attemptId": attempt_id,
                "fromSha256": "0" * 64, "toSha256": sha256_bytes(journal_bytes),
            }))
            states = iter(((True, "exited"), (False, None)))
            def engine_call(_attempt, _journal, arguments):
                if arguments[:2] == ["image", "inspect"]:
                    return SimpleNamespace(returncode=0, stdout=image_id)
                return SimpleNamespace(returncode=0, stdout="")
            with patch("experiment_compiler.runner._controller_is_alive"), \
                    patch("experiment_compiler.runner._reconcile_cwl_process_group"), \
                    patch("experiment_compiler.runner._container_present", side_effect=lambda *_: next(states)), \
                    patch("experiment_compiler.runner._engine_call", side_effect=engine_call) as engine:
                recovered = recover_attempt(attempt)
            self.assertEqual(recovered["status"], "failed")
            self.assertEqual(recovered["containerCleanup"]["state"], "clean")
            self.assertEqual(recovered["containerCleanup"]["containerCount"], 1)
            self.assertTrue(recovered["containerCleanup"]["allAbsent"])
            self.assertEqual(engine.call_args_list[-1].args[2], ["rm", container_id])
            latest_journal = json.loads((attempt / "container-journal.json").read_text())
            self.assertTrue(latest_journal["containers"][0]["cleanup"]["absentAfterCleanup"])
            latest_receipt = json.loads((attempt / "runner-receipt.json").read_text())
            self.assertEqual(latest_receipt["containerJournal"]["sha256"],
                             sha256_bytes((attempt / "container-journal.json").read_bytes()))

    @unittest.skipUnless(os.name == "posix", "tmpfs mount contract is Linux-specific")
    def test_output_cap_requires_exact_dedicated_tmpfs_mount(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            size = 64 * MIB
            mountinfo = f"20 1 0:1 / {root} rw,relatime - tmpfs tmpfs rw,size={size}"
            with patch.object(Path, "read_text", return_value=mountinfo), \
                    patch("experiment_compiler.runner.shutil.disk_usage",
                          return_value=SimpleNamespace(total=size)):
                self.assertEqual(_require_bounded_output_tmpfs(root, 64), root.resolve())
            with patch.object(Path, "read_text", return_value=mountinfo.replace("tmpfs tmpfs", "ext4 /dev/fake")), \
                    patch("experiment_compiler.runner.shutil.disk_usage",
                          return_value=SimpleNamespace(total=size)), \
                    self.assertRaisesRegex(PackageError, "dedicated tmpfs"):
                _require_bounded_output_tmpfs(root, 64)

    def test_result_package_staging_uses_output_storage_not_bounded_tmpfs(self):
        class CompletedProcess:
            pid = 100
            returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        with tempfile.TemporaryDirectory() as workspace_value, tempfile.TemporaryDirectory() as tmpfs_value:
            workspace = Path(workspace_value)
            tmpfs = Path(tmpfs_value)
            plan = workspace / "plan.zip"
            built = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", plan)
            output = workspace / "result.zip"

            def fake_popen(command, **kwargs):
                outdir = Path(command[command.index("--outdir") + 1])
                provenance = Path(command[command.index("--provenance") + 1])
                outdir.mkdir(parents=True)
                (outdir / "result.json").write_text('{"fixture":"result"}\n')
                provenance.mkdir(parents=True)
                (provenance / "workflow-run.json").write_text('{"fixture":"provenance"}\n')
                return CompletedProcess()

            original_compile = compile_package
            staged_package_paths = []

            def record_package_path(recipe_path, package_path):
                path = Path(package_path)
                staged_package_paths.append(path)
                self.assertEqual(path.parent.parent, workspace)
                self.assertNotEqual(path.parent, tmpfs)
                return original_compile(recipe_path, path)

            with (
                    patch.dict(os.environ, {
                    "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                    "EXPERIMENT_RUNNER_SANDBOX": "test worker",
                    "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                        '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
                    "EXPERIMENT_RUNNER_TMPFS_ROOT": str(tmpfs),
                    "TMPDIR": str(tmpfs),
                    }),
                    patch("experiment_compiler.runner.tempfile.tempdir", str(tmpfs)),
                    patch("experiment_compiler.runner._require_bounded_tmpfs"),
                    patch("experiment_compiler.runner.importlib.metadata.version",
                          return_value="3.2.20260720092025"),
                    patch("experiment_compiler.runner.subprocess.Popen", side_effect=fake_popen),
                    patch("experiment_compiler.runner.compile_package", side_effect=record_package_path),
            ):
                run_package(plan, output, expected_sha256=built["packageSha256"],
                            allow_workflow_execution=True)

            self.assertEqual(len(staged_package_paths), 1)
            self.assertTrue(output.is_file())
            self.assertFalse(list(tmpfs.glob("compiled-experiment-run-*")))

    def test_attempt_result_upgrades_file_only_workflow_in_completed_and_failed_crates(self):
        from datetime import datetime, timezone
        from experiment_compiler.runner import _read_package

        for return_code, expected_status in ((0, "https://schema.org/CompletedActionStatus"),
                                             (9, "https://schema.org/FailedActionStatus")):
            with self.subTest(return_code=return_code), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                plan_path = directory / "plan.zip"
                plan_build = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", plan_path)
                manifest, payload = _read_package(plan_path.read_bytes(), plan_build["packageSha256"])
                crate = json_value(payload["ro-crate-metadata.json"])
                workflow = next(node for node in crate["@graph"]
                                if node.get("@id") == "experiment/workflow.cwl")
                initial_types = "File" if return_code == 0 else ["File", "CreativeWork"]
                workflow["@type"] = initial_types
                workflow_digest = sha256_bytes(payload["experiment/workflow.cwl"])
                workflow.update({"sha256": workflow_digest,
                                 "contentSize": len(payload["experiment/workflow.cwl"])})
                original_properties = {key: value for key, value in workflow.items() if key != "@type"}
                payload["ro-crate-metadata.json"] = canonical(crate)
                augmented = dict(payload)
                attempt_member = "evidence/attempts/fixture/output.json"
                augmented[attempt_member] = b'{"fixture":"retained"}\n'
                created = datetime(2026, 10, 22, tzinfo=timezone.utc)
                execution = {"returnCode": return_code, "timedOut": False, "collectionErrors": []}
                experiment_id, title = _augment_result_crate(
                    augmented, plan_build["packageSha256"], "fixture", [attempt_member],
                    created, created, execution,
                )
                result_crate = json_value(augmented["ro-crate-metadata.json"])
                result_workflow = next(node for node in result_crate["@graph"]
                                       if node.get("@id") == "experiment/workflow.cwl")
                expected_types = ([initial_types] if isinstance(initial_types, str) else list(initial_types))
                self.assertEqual(result_workflow["@type"], expected_types + ["SoftwareSourceCode"])
                for key, value in original_properties.items():
                    self.assertEqual(result_workflow[key], value)
                action = next(node for node in result_crate["@graph"]
                              if node.get("@id") == "#attempt-fixture")
                self.assertEqual(action["actionStatus"], expected_status)

                source = directory / "source"
                members = []
                for name, content in sorted(augmented.items()):
                    member_path = source / name
                    member_path.parent.mkdir(parents=True, exist_ok=True)
                    member_path.write_bytes(content)
                    members.append({"source": name, "path": name,
                                    "sha256": sha256_bytes(content), "size": len(content)})
                recipe = {
                    "buildRecipeVersion": 1,
                    "id": f"{experiment_id}-attempt-fixture-{return_code}",
                    "title": f"{title} — attempt fixture {return_code}",
                    "profile": "compiled-experiment-lifecycle-v1",
                    "manifest": {"schemaVersion": 2, "packageType": "compiled-experiment",
                                 "profile": "compiled-experiment-lifecycle-v1",
                                 "source": manifest["source"], "standards": manifest["standards"],
                                 "evidence": {"planPackageSha256": plan_build["packageSha256"],
                                              "attemptId": "fixture"}},
                    "members": members,
                }
                recipe_path = source / "experiment.json"
                recipe_path.write_bytes(canonical(recipe))
                compile_package(recipe_path, directory / "result.zip")

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

    def test_collector_enforces_explicit_worker_output_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (root / "too-large").open("wb") as stream:
                stream.truncate(65)
            errors = []
            self.assertEqual(_collect_execution_tree(root, "output", errors, 64), {})
            self.assertTrue(any("declared worker resource budget" in error for error in errors))

    def test_attempt_receipts_survive_execution_and_packaging_failures(self):
        scenarios = ("success", "nonzero", "timeout", "oversized-log", "symlink",
                     "oversized-output", "aggregate", "oversized-provenance", "missing", "compile",
                     "copy", "existing-output", "existing-source", "launch", "interrupted", "cancelled", "cancelled-cleanup-timeout", "cancelled-cleanup-interrupted", "terminal-persist")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                self._check_attempt_scenario(scenario, Path(temporary))

    def test_timeout_with_live_orphan_skips_container_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch("experiment_compiler.runner._process_group_exists", side_effect=[True, True]), \
                    patch("experiment_compiler.runner._cleanup_container_journal") as cleanup:
                self._check_attempt_scenario("timeout-orphan", directory)
            cleanup.assert_not_called()
            journal = json.loads((directory / "result.attempt/container-journal.json").read_text())
            self.assertEqual(journal["state"], "unknown")
            self.assertTrue(journal["cwlProcessGroupUnresolved"])

    def _mock_attempt_launch(self, scenario, directory, scratch):
        class Process:
            pid = 98765
            returncode = 9 if scenario == "nonzero" else 0
            calls = 0

            def wait(self, timeout=None):
                self.calls += 1
                if scenario.startswith("cancelled") and self.calls == 1:
                    raise KeyboardInterrupt()
                if scenario == "cancelled-cleanup-timeout" and self.calls > 1:
                    raise subprocess.TimeoutExpired("fixture cleanup", timeout)
                if scenario == "cancelled-cleanup-interrupted" and self.calls == 2:
                    raise KeyboardInterrupt()
                if scenario in {"timeout", "timeout-orphan"} and self.calls == 1:
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
        out = Path(command[command.index("--outdir") + 1])
        out.mkdir()
        if scenario != "missing":
            (out / "result.txt").write_bytes(b"fixture")
        if scenario == "symlink":
            (out / "link").symlink_to(out / "result.txt")
        sizes = {
            "oversized-output": [WORKER_OUTPUT_BUDGET + 1],
            "aggregate": [32 * MIB, 32 * MIB, 1],
        }
        for number, size in enumerate(sizes.get(scenario, [])):
            with (out / f"large-{number}").open("wb") as stream:
                stream.truncate(size)
        if scenario == "oversized-provenance":
            provenance = Path(command[command.index("--provenance") + 1])
            provenance.mkdir()
            with (provenance / "too-much").open("wb") as stream:
                stream.truncate(65 * MIB)
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
                    path.name.startswith("runner-receipt.json.tmp.") and not terminal_failure):
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
                patch("experiment_compiler.runner.os.killpg") as kill_group, \
                patch.object(Path, "replace", new=replace_receipt), \
                patch("experiment_compiler.runner.shutil.copytree", side_effect=copy_source), \
                patch("experiment_compiler.runner.compile_package", side_effect=PackageError("fixture packaging failure") if scenario == "compile" else compile_package):
            errors = {"compile": PackageError, "interrupted": KeyboardInterrupt, "cancelled": KeyboardInterrupt, "cancelled-cleanup-timeout": KeyboardInterrupt,
                      "cancelled-cleanup-interrupted": KeyboardInterrupt,
                      "copy": OSError, "existing-output": OSError, "existing-source": OSError,
                      "launch": OSError, "terminal-persist": OSError}
            if scenario in errors:
                with self.assertRaises(errors[scenario]):
                    run_package(plan, output, expected_sha256=built["packageSha256"], allow_workflow_execution=True)
            else:
                run_package(plan, output, expected_sha256=built["packageSha256"], allow_workflow_execution=True)
        if scenario in {"timeout", "timeout-orphan"} or scenario.startswith("cancelled"):
            self.assertEqual([call.args for call in kill_group.call_args_list], [(98765, 15), (98765, 9)])
        receipt = json.loads((directory / "result.attempt/runner-receipt.json").read_text())
        self.assertEqual(receipt["planPackageSha256"], built["packageSha256"])
        self.assertTrue(receipt["attemptId"])
        self.assertTrue(all(not path.exists() for path in scratch))
        self._check_attempt_outcome(scenario, directory, receipt, terminal_failure)

    def _check_attempt_outcome(self, scenario, directory, receipt, terminal_failure):
        statuses = {"terminal-persist": "started",
                    "success": "succeeded", "timeout": "timed-out", "timeout-orphan": "timed-out"}
        self.assertEqual(receipt["status"], statuses.get(scenario, "failed"))
        self._check_cancellation_evidence(scenario, directory, receipt)
        rejected = {"symlink", "oversized-output", "aggregate", "missing"}
        errors = rejected | {"compile", "copy", "existing-output", "existing-source",
                             "launch", "oversized-log", "oversized-provenance"}
        if scenario in errors:
            self.assertTrue(receipt["collectionErrors"])
        if scenario in rejected:
            self.assertEqual(receipt["outputs"], [])
        if scenario == "oversized-provenance":
            self.assertEqual(receipt["workflowRunCrateFiles"], [])
        if scenario in ("compile", "copy"):
            message = "fixture packaging failure" if scenario == "compile" else "fixture source copy failure"
            self.assertIn(message, receipt["collectionErrors"][-1])
            self.assertFalse((directory / "result.zip").exists())
            self.assertFalse((directory / "result.source").exists())
        if scenario == "compile":
            self.assertEqual((directory / "result.attempt/stdout.log").read_bytes(), b"bounded diagnostic")
        self._check_attempt_publication(scenario, directory, receipt, terminal_failure)

    def _check_cancellation_evidence(self, scenario, directory, receipt):
        if scenario == "interrupted" or scenario.startswith("cancelled"):
            self.assertTrue(any("Operator cancellation requested" in error
                                for error in receipt["collectionErrors"]))
            self.assertIsNotNone(receipt["endedAt"])
            self.assertFalse((directory / "result.zip").exists())
        if scenario.startswith("cancelled"):
            self.assertEqual((directory / "result.attempt/stdout.log").read_bytes(), b"bounded diagnostic")
        if scenario in ("cancelled-cleanup-timeout", "cancelled-cleanup-interrupted"):
            diagnostic = "TimeoutExpired" if scenario.endswith("timeout") else "KeyboardInterrupt"
            self.assertTrue(any("Process cleanup wait" in error and diagnostic in error
                                for error in receipt["collectionErrors"]))
            self.assertFalse(any("could not start" in error for error in receipt["collectionErrors"]))

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
                for key, value in receipt.items():
                    if key not in {"containerJournal", "containerCleanup", "executionPhase",
                                   "executionNeverStarted", "launchIntentPersisted"}:
                        self.assertEqual(published.get(key), value)
                self.assertEqual(published["containerJournal"]["availability"], "private-store")
                self.assertEqual(published["containerJournal"]["sha256"], receipt["containerJournal"]["sha256"])
                self.assertTrue(published["containerCleanup"]["allAbsent"])

    def test_process_cleanup_escalates_and_bounds_each_wait(self):
        from unittest.mock import Mock
        process = Mock(pid=8123)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), -9]
        with patch("experiment_compiler.runner.os.killpg") as kill_group:
            errors = _terminate_process_group(process)
        self.assertTrue(any("TimeoutExpired" in error for error in errors))
        self.assertEqual([call.args for call in kill_group.call_args_list], [(8123, 15), (8123, 9)])
        self.assertEqual([call.kwargs for call in process.wait.call_args_list], [{"timeout": 5}, {"timeout": 5}])

    def test_process_cleanup_records_signal_errors_without_masking_them(self):
        from unittest.mock import Mock
        process = Mock(pid=8124)
        process.wait.return_value = -9
        with patch("experiment_compiler.runner.os.killpg", side_effect=OSError("permission denied")), \
                patch("experiment_compiler.runner._process_group_exists", return_value=False):
            errors = _terminate_process_group(process)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("signal " in error and "OSError" in error for error in errors))
        self.assertEqual(process.wait.call_count, 2)

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
            with self.assertRaisesRegex(PackageError, "declared worker resource budget"):
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

    def assert_engine_client_environment(self, environment, command):
        python_lib = str(Path(sys.executable).resolve().parent.parent / "lib")
        self.assertIn(python_lib, environment["LD_LIBRARY_PATH"].split(":"))
        self.assertEqual(environment["XDG_DATA_HOME"], "/tmp/podman-data")
        self.assertEqual(environment["XDG_RUNTIME_DIR"], "/tmp/podman-runtime")
        self.assertEqual(environment["XDG_CONFIG_HOME"], "/tmp/podman-config")
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(Path(environment["HOME"]).name, "runner-home")
        self.assertNotEqual(environment["HOME"], "/private/host-home")
        self.assertEqual(Path(environment["HOME"]).parent, Path(environment["TMPDIR"]))
        for excluded in ("GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "AWS_SECRET_ACCESS_KEY",
                         "CONTAINERS_CONF", "CONTAINER_HOST", "DOCKER_HOST", "PODMAN_CONNECTIONS_CONF"):
            self.assertNotIn(excluded, environment)
        self.assertNotIn("--preserve-entire-environment", command)
        self.assertFalse(any(arg.startswith("--preserve-environment") for arg in command))

    def test_podman_mode_selects_podman_in_the_pinned_cwltool_runner(self):
        class CompletedProcess:
            pid = 102
            returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        def fake_popen(command, **kwargs):
            self.assertIn("--podman", command)
            self.assert_engine_client_environment(kwargs["env"], command)
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
                    "XDG_RUNTIME_DIR": "/tmp/podman-runtime",
                    "XDG_CONFIG_HOME": "/tmp/podman-config",
                    "HOME": "/private/host-home",
                    "PATH": "/private/host-bin",
                    "GITHUB_TOKEN": "SECRET-CANARY",
                    "ACTIONS_RUNTIME_TOKEN": "SECRET-CANARY",
                    "AWS_SECRET_ACCESS_KEY": "SECRET-CANARY",
                    "CONTAINERS_CONF": "/private/host-config",
                    "CONTAINER_HOST": "ssh://private/host",
                    "DOCKER_HOST": "tcp://private/host",
                    "PODMAN_CONNECTIONS_CONF": "/private/host-connections",
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

    def test_missing_or_empty_xdg_context_is_not_synthesized(self):
        names = ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
        for environment in ({}, dict.fromkeys(names, "")):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                with patch.dict(os.environ, environment, clear=True), \
                        patch("experiment_compiler.runner._require_bounded_tmpfs"), \
                        patch("experiment_compiler.runner.subprocess.Popen") as popen:
                    popen.return_value.wait.return_value = 0
                    _execute_cwl(_CwlExecutionRequest(
                        directory, {}, {"executionMode": "cwltool-podman", "maxWallSeconds": 1},
                        {"tmpdirMiB": 64}, temporary, None,
                    ))
                client_environment = popen.call_args.kwargs["env"]
                for name in names:
                    self.assertNotIn(name, client_environment)
                self.assertEqual(client_environment["HOME"], str(directory / "runner-home"))
                self.assertEqual(client_environment["PATH"], "/usr/bin:/bin")
                self.assertEqual(client_environment["TMPDIR"], temporary)

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

    def _assert_private_container_reference(self, receipt, result):
        reference = receipt["containerJournal"]
        self.assertEqual(reference["availability"], "private-store")
        self.assertRegex(reference["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(reference["storePath"], f"container-journal-history/{reference['sha256']}.json")
        private_snapshot = result.with_name(result.stem + ".attempt") / reference["storePath"]
        self.assertEqual(sha256_bytes(private_snapshot.read_bytes()), reference["sha256"])

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
            self._assert_private_container_reference(receipt, result)
            self.assertTrue(receipt["containerCleanup"]["allAbsent"])
            self.assertNotIn("containerIds", receipt["containerCleanup"])
            self.assertFalse(any(name.endswith("/container-journal.json") for name in names))
            self.assertTrue(any(node.get("@type") == "CreateAction" for node in crate["@graph"]))


if __name__ == "__main__":
    unittest.main()
