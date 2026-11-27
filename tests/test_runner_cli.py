"""Native controller cancellation fixtures execute no packaged CWL or containers."""
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.core import PackageError, canonical, compile_package
from experiment_compiler.runner_process import _defer_operator_signals
from experiment_compiler.runner_cli import _operator_cancellation, main

from fixture_requirements import REQUIRES_RUNNER

ROOT = Path(__file__).resolve().parents[1]
ARGS = ["run", "plan.zip", "--output", "result.zip", "--expected-sha256", "0" * 64,
        "--allow-workflow-execution"]
WORKER = textwrap.dedent("""\
    import os, signal, sys, time
    from pathlib import Path
    directory = Path(sys.argv[1])
    def terminate(number, frame):
        (directory / "grace").write_text(str(number))
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    print("native cancellation diagnostic", flush=True)
    print("x" * 4096, end="", flush=True)
    (directory / "ready").write_text(str(os.getpid()))
    while True:
        time.sleep(1)
    """)
CONTROLLER = textwrap.dedent("""\
    import json, os, sys
    from functools import partial
    from pathlib import Path
    from unittest.mock import patch
    sys.path.insert(0, str(Path.cwd() / "tests"))
    from runner_fixture_helpers import prepare_container_journal, synthetic_controller_environment
    from experiment_compiler import runner_admission, runner_cli, runner_process
    directory = Path(sys.argv[1])
    worker = sys.argv[2]
    killpg = os.killpg
    runner_config = runner_admission._runner_config
    def configure(payload):
        config = runner_config(payload)
        if sys.argv[4] == "timeout":
            config["maxWallSeconds"] = 1
        return config
    def record_signal(pid, number):
        with (directory / "signals").open("a") as stream:
            stream.write(str(number) + "\\n")
        killpg(pid, number)
    def native_command(temporary, *args):
        (directory / "scratch").write_text(str(temporary))
        return [sys.executable, "-u", "-c", worker, str(directory)]
    with synthetic_controller_environment(directory), \\
            patch.object(runner_process, "_require_bounded_tmpfs"), \\
            patch.object(runner_admission, "_runner_config", side_effect=configure), \\
            patch.object(runner_process, "_new_container_journal", side_effect=prepare_container_journal), \\
            patch.object(runner_process, "LogCapture", partial(runner_process.LogCapture, stream_limit=31)), \\
            patch.object(runner_process, "_run_command", side_effect=native_command), \\
            patch.object(runner_process.os, "killpg", side_effect=record_signal):
        raise SystemExit(runner_cli.main(json.loads(sys.argv[3])))
    """)
RETENTION_CONTROLLER = textwrap.dedent("""\
    import json, os, sys, time
    from pathlib import Path
    from unittest.mock import patch
    sys.path.insert(0, str(Path.cwd() / "tests"))
    from runner_fixture_helpers import prepare_container_journal, synthetic_controller_environment
    from experiment_compiler import runner, runner_cli, runner_process
    directory = Path(sys.argv[1])
    mode = sys.argv[2]
    replace = Path.replace
    def execute(request, *args):
        temporary = request.temporary
        (directory / "scratch").write_text(str(temporary))
        (temporary / "stdout.log").write_bytes(b"retained native diagnostic")
        (temporary / "stderr.log").write_bytes(b"retained native stderr")
        if mode == "failure":
            raise runner.PackageError("ordinary execution failure")
        if mode == "interruption":
            interruption = KeyboardInterrupt("earlier runner interruption")
            interruption.add_note("original runner diagnostic")
            raise interruption
        return {"returnCode": 0, "timedOut": False, "stdout": b"retained native diagnostic",
                "stderr": b"retained native stderr", "outputs": {"result.txt": b"fixture"},
                "provenance": {}, "collectionErrors": []}
    def pause_receipt(path, target):
        if path.name.startswith("runner-receipt.json.tmp."):
            value = json.loads(path.read_bytes())
            selected = (value["status"] == "failed" or (directory / "result.zip").exists())
            if selected:
                (directory / "ready").write_text(value["status"])
                deadline = time.monotonic() + 10
                while not (directory / "release").exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("native receipt fixture release timed out")
                    time.sleep(0.01)
        return replace(path, target)
    with synthetic_controller_environment(directory), \\
            patch.object(runner_process, "_execute_cwl", side_effect=execute), \\
            patch.object(runner_process, "_new_container_journal", side_effect=prepare_container_journal), \\
            patch.object(Path, "replace", new=pause_receipt):
        raise SystemExit(runner_cli.main(json.loads(sys.argv[3])))
    """)


class RunnerSignalContextTests(unittest.TestCase):
    def test_verbose_runner_rejection_is_safe_and_carries_available_identity(self):
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (tempfile.TemporaryDirectory() as temporary, redirect_stderr(stderr), redirect_stdout(stdout),
              patch("experiment_compiler.runner_admission._open_run_package",
                    side_effect=OSError("fixture-secret at /private/fixture-plan")) as opened):
            output = Path(temporary) / "result.zip"
            args = ["run", "plan.zip", "--output", str(output), "--expected-sha256", "0" * 64,
                    "--allow-workflow-execution", "--verbose"]
            self.assertEqual(main(args), 1)
            opened.assert_called_once()
        rendered = stderr.getvalue()
        self.assertIn("phase=admission attempt_id=unavailable", rendered)
        self.assertIn("package_sha256=" + "0" * 64, rendered)
        self.assertIn("exception=OSError", rendered)
        self.assertNotIn("fixture-secret", rendered)
        self.assertNotIn("/private/fixture-plan", rendered)
        self.assertEqual(stdout.getvalue(), "")

    def test_verbose_success_keeps_stdout_as_standalone_json(self):
        stderr = io.StringIO()
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw)
        receipt = {"executionStatus": "succeeded", "id": "fixture"}
        with (redirect_stderr(stderr), redirect_stdout(stdout),
              patch("experiment_compiler.runner_cli.run_package", return_value=receipt)):
            self.assertEqual(main([*ARGS, "--verbose"]), 0)
        self.assertEqual(raw.getvalue(), canonical(receipt))
        self.assertEqual(stderr.getvalue(), "")

    def test_context_keeps_first_signal_and_restores_custom_handlers(self):
        originals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        def custom(number, frame):
            pass
        try:
            for number in originals:
                signal.signal(number, custom)
            with self.assertRaisesRegex(KeyboardInterrupt, "SIGTERM") as replayed, _operator_cancellation():
                cancel = signal.getsignal(signal.SIGTERM)
                with self.assertRaisesRegex(KeyboardInterrupt, "SIGTERM") as caught:
                    cancel(signal.SIGTERM, None)
                for number in (signal.SIGINT, signal.SIGTERM) * 20:
                    signal.getsignal(number)(number, None)
                self.assertEqual(caught.exception.signal_number, signal.SIGTERM)
            self.assertIs(replayed.exception, caught.exception)
            for number in originals:
                self.assertIs(signal.getsignal(number), custom)
        finally:
            for number, handler in originals.items():
                signal.signal(number, handler)

    def test_cli_restores_handlers_after_runner_failure(self):
        originals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with patch("experiment_compiler.runner_cli.run_package", side_effect=PackageError("fixture")), \
                patch("sys.stderr"):
            self.assertEqual(main(ARGS), 1)
        self.assertEqual({number: signal.getsignal(number) for number in originals}, originals)

    def test_cli_restores_handlers_after_cancellation(self):
        originals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        def cancel(*args, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        with patch("experiment_compiler.runner_cli.run_package", side_effect=cancel), patch("sys.stderr"):
            self.assertEqual(main(ARGS), 143)
        self.assertEqual({number: signal.getsignal(number) for number in originals}, originals)

    def test_pending_cancellation_survives_runner_return_and_error(self):
        for error in (None, PackageError("later runner failure"), KeyboardInterrupt("later interruption")):
            with self.subTest(error=error):
                def runner(*args, runner_error=error, **kwargs):
                    try:
                        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    except KeyboardInterrupt:
                        pass  # Same boundary as the bounded cleanup helper.
                    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
                    if runner_error is not None:
                        raise runner_error
                    return {"executionStatus": "failed"}
                with patch("experiment_compiler.runner_cli.run_package", side_effect=runner), \
                        patch("sys.stderr") as stderr:
                    self.assertEqual(main(ARGS), 143)
                self.assertIn("SIGTERM", str(stderr.write.call_args_list))

    def test_pending_signal_does_not_replace_existing_runner_interruption(self):
        original = KeyboardInterrupt("earlier runner interruption")
        with self.assertRaises(KeyboardInterrupt) as caught, _operator_cancellation():
            try:
                raise original
            except KeyboardInterrupt:
                try:
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                except KeyboardInterrupt:
                    pass
                raise
        self.assertIs(caught.exception, original)

    def test_context_does_not_install_handlers_off_main_thread(self):
        entered = []
        def enter():
            with _operator_cancellation():
                entered.append(True)
        with patch("experiment_compiler.runner_cli.signal.signal") as install:
            thread = threading.Thread(target=enter)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            install.assert_not_called()
        self.assertEqual(entered, [True])

    def test_import_has_no_signal_side_effects(self):
        script = """import signal
before = [signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)]
import experiment_compiler.runner_cli
assert before == [signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)]
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


@unittest.skipUnless(hasattr(signal, "pthread_sigmask"), "POSIX signal mask required")
class ReceiptSignalDeferralTests(unittest.TestCase):
    def test_first_signal_survives_deferral_restore_and_replay(self):
        for phase in ("install", "write", "restore", "replay"):
            with self.subTest(phase=phase):
                previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
                original_install = signal.signal
                with self.assertRaisesRegex(KeyboardInterrupt, "SIGTERM"), _operator_cancellation():
                    handler = signal.getsignal(signal.SIGTERM)
                    def install(number, restored, *, set_handler=original_install,
                                tested_phase=phase, test_handler=handler):
                        result = set_handler(number, restored)
                        if tested_phase == "install" and number == signal.SIGTERM and restored is not test_handler:
                            signal.raise_signal(signal.SIGTERM)
                        if tested_phase == "restore" and number == signal.SIGTERM and restored is test_handler:
                            signal.raise_signal(signal.SIGINT)
                        return result
                    def replay(number, frame, *, test_handler=handler):
                        try:
                            test_handler(number, frame)
                        finally:
                            signal.raise_signal(signal.SIGINT)
                    if phase == "replay":
                        original_install(signal.SIGTERM, replay)
                    with patch("experiment_compiler.runner_process.signal.signal", side_effect=install):
                        with _defer_operator_signals():
                            if phase != "install":
                                signal.raise_signal(signal.SIGTERM)
                            if phase in ("install", "write"):
                                signal.raise_signal(signal.SIGINT)
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, []), previous_mask)

    def test_first_signal_during_restore_runs_after_handlers_are_restored(self):
        original_install = signal.signal
        with self.assertRaisesRegex(KeyboardInterrupt, "SIGTERM"), _operator_cancellation():
            handler = signal.getsignal(signal.SIGTERM)
            def install(number, restored):
                result = original_install(number, restored)
                if number == signal.SIGTERM and restored is handler:
                    signal.raise_signal(signal.SIGTERM)
                return result
            with patch("experiment_compiler.runner_process.signal.signal", side_effect=install):
                with _defer_operator_signals():
                    pass

    def test_evidence_io_failure_remains_bounded_cancellation_diagnostic(self):
        with self.assertRaisesRegex(KeyboardInterrupt, "SIGTERM") as caught, _operator_cancellation():
            with _defer_operator_signals():
                signal.raise_signal(signal.SIGTERM)
                raise OSError("fixture write failure " + "x" * 10000)
        self.assertTrue(any("Attempt evidence write failed: OSError" in note for note in caught.exception.__notes__))
        self.assertLess(sum(map(len, caught.exception.__notes__)), 1200)

    def test_deferral_is_noop_off_main_thread_without_posix_mask_or_handled_signals(self):
        entered = []
        def enter():
            with _defer_operator_signals():
                entered.append(True)
        with patch("experiment_compiler.runner_process.signal.signal") as install:
            thread = threading.Thread(target=enter)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            with patch("experiment_compiler.runner_process.signal.getsignal", return_value=signal.SIG_DFL), \
                    patch("experiment_compiler.runner_process.signal.pthread_sigmask") as mask:
                enter()
                mask.assert_not_called()
            with patch("experiment_compiler.runner_process.hasattr", return_value=False):
                enter()
            install.assert_not_called()
        self.assertEqual(entered, [True, True, True])


@unittest.skipUnless(os.name == "posix", "runner process-group cancellation requires POSIX")
@REQUIRES_RUNNER
class NativeRunnerCancellationTests(unittest.TestCase):
    def test_native_attempt_retains_sigterm_and_mixed_signal_cancellation(self):
        for first, repeats in ((signal.SIGTERM, ()),
                               (signal.SIGTERM, (signal.SIGINT, signal.SIGTERM)),
                               (signal.SIGINT, (signal.SIGTERM, signal.SIGINT))):
            with self.subTest(first=first, repeats=repeats), tempfile.TemporaryDirectory() as temporary:
                self._run_cancelled_attempt(Path(temporary), first, repeats)

    def test_first_sigterm_during_timeout_cleanup_remains_cancellation(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._run_cancelled_attempt(Path(temporary), signal.SIGTERM, (), timeout_cleanup=True)

    def test_first_signal_during_receipt_write_preserves_failure_or_publication(self):
        for mode in ("failure", "publication", "interruption"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                self._run_retention_signal(Path(temporary), mode)

    def _run_retention_signal(self, directory, mode):
        built = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json", directory / "plan.zip")
        argv = ["run", str(directory / "plan.zip"), "--output", str(directory / "result.zip"),
                "--expected-sha256", built["packageSha256"], "--allow-workflow-execution"]
        with subprocess.Popen([sys.executable, "-c", RETENTION_CONTROLLER, str(directory), mode, json.dumps(argv)],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as controller:
            try:
                self._wait_for(directory / "ready", controller)
                receipt_path = directory / "result.attempt/runner-receipt.json"
                self.assertEqual(json.loads(receipt_path.read_bytes())["status"], "started")
                controller.send_signal(signal.SIGTERM)
                time.sleep(0.03)
                controller.send_signal(signal.SIGINT)
                time.sleep(0.03)
                (directory / "release").touch()
                stdout, stderr = controller.communicate(timeout=10)
                expected = "earlier runner interruption" if mode == "interruption" else "SIGTERM"
                self.assertEqual(controller.returncode, 130 if mode == "interruption" else 143, stderr.decode())
                self.assertEqual(stdout, b"")
                self.assertIn(expected, stderr.decode())
                self._check_retention_signal(directory, mode, receipt_path)
            finally:
                if controller.poll() is None:
                    controller.kill()
                    controller.communicate(timeout=5)

    def _check_retention_signal(self, directory, mode, receipt_path):
        receipt = json.loads(receipt_path.read_bytes())
        published = mode == "publication"
        self.assertEqual(receipt["status"], "succeeded" if published else "failed")
        self.assertIsNotNone(receipt["endedAt"])
        self.assertIsNotNone(receipt["wallSeconds"])
        self.assertEqual((directory / "result.attempt/stdout.log").read_bytes(), b"retained native diagnostic")
        self.assertEqual((directory / "result.attempt/stderr.log").read_bytes(), b"retained native stderr")
        self.assertFalse(Path((directory / "scratch").read_text()).exists())
        self.assertEqual((directory / "result.zip").exists(), published)
        self.assertEqual((directory / "result.source").exists(), published)
        if published:
            with zipfile.ZipFile(directory / "result.zip") as archive:
                member = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
                archived_receipt = json.loads(archive.read(member))
                self.assertEqual(archived_receipt["containerJournal"]["availability"], "private-store")
                self.assertEqual(archived_receipt["containerJournal"]["sha256"],
                                 receipt["containerJournal"]["sha256"])
        else:
            original = "ordinary execution failure" if mode == "failure" else "earlier runner interruption"
            self.assertTrue(any(original in error for error in receipt["collectionErrors"]))
            if mode == "interruption":
                self.assertIn("original runner diagnostic", receipt["collectionErrors"])

    def _wait_for(self, path, process):
        deadline = time.monotonic() + 10
        while not path.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail("controller exited before native fixture readiness "
                          f"(exit={process.returncode}, stdout={stdout!r}, stderr={stderr!r})")
            if time.monotonic() >= deadline:
                self.fail(f"native fixture did not create {path.name}")
            time.sleep(0.01)

    def _run_cancelled_attempt(self, directory, first, repeats, timeout_cleanup=False):
        built = compile_package(ROOT / "examples/linear-regression-plan-v1/experiment.json",
                                directory / "plan.zip")
        argv = ["run", str(directory / "plan.zip"), "--output", str(directory / "result.zip"),
                "--expected-sha256", built["packageSha256"], "--allow-workflow-execution"]
        worker_pid = None
        mode = "timeout" if timeout_cleanup else "cancel"
        with subprocess.Popen([sys.executable, "-c", CONTROLLER, str(directory), WORKER, json.dumps(argv), mode],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as controller:
            try:
                self._wait_for(directory / "ready", controller)
                worker_pid = int((directory / "ready").read_text())
                if timeout_cleanup:
                    self._wait_for(directory / "grace", controller)
                    controller.send_signal(first)
                else:
                    controller.send_signal(first)
                    self._wait_for(directory / "grace", controller)
                for number in repeats:
                    controller.send_signal(number)
                    time.sleep(0.02)
                stdout, stderr = controller.communicate(timeout=15)
                self.assertEqual(controller.returncode, 128 + first, stderr.decode())
                self.assertEqual(stdout, b"")
                self.assertIn(signal.Signals(first).name, stderr.decode())
                self._check_attempt(directory, built["packageSha256"], first, worker_pid, timeout_cleanup)
            finally:
                if controller.poll() is None:
                    controller.kill()
                    controller.communicate(timeout=5)
                if worker_pid is not None:
                    try:
                        os.killpg(worker_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def _check_attempt(self, directory, digest, first, worker_pid, timeout_cleanup):
        receipt = json.loads((directory / "result.attempt/runner-receipt.json").read_text())
        self.assertEqual(receipt["status"], "timed-out" if timeout_cleanup else "failed")
        self.assertEqual(receipt["planPackageSha256"], digest)
        self.assertIsNotNone(receipt["endedAt"])
        self.assertIsNotNone(receipt["wallSeconds"])
        errors = receipt["collectionErrors"]
        self.assertTrue(any("Operator cancellation requested by " + signal.Signals(first).name in e for e in errors))
        diagnostic = "KeyboardInterrupt" if timeout_cleanup else "TimeoutExpired"
        self.assertTrue(any("Process cleanup wait" in e and diagnostic in e for e in errors))
        self.assertLess(sum(map(len, errors)), 8192)
        self.assertEqual(receipt["exitCode"], -9 if timeout_cleanup else None)
        self.assertEqual(receipt["timedOut"], timeout_cleanup)
        self.assertEqual((directory / "signals").read_text().splitlines(), ["15", "9"])
        self.assertEqual((directory / "result.attempt/stdout.log").read_bytes(), b"native cancellation diagnostic\n")
        self.assertEqual((directory / "result.attempt/stderr.log").read_bytes(), b"")
        self.assertIn("stdout log truncated by stream-limit", errors)
        self.assertEqual(receipt["logCapture"]["streams"]["stdout"], {
            "observedBytes": 4096 + 31, "retainedBytes": 31, "reachedEof": True,
            "truncationReasons": ["stream-limit"],
        })
        self.assertEqual((directory / "result.zip").exists(), timeout_cleanup)
        self.assertEqual((directory / "result.source").exists(), timeout_cleanup)
        if timeout_cleanup:
            with zipfile.ZipFile(directory / "result.zip") as archive:
                member = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
                archived_receipt = json.loads(archive.read(member))
                self.assertEqual(archived_receipt["containerJournal"]["availability"], "private-store")
                self.assertEqual(archived_receipt["containerJournal"]["sha256"],
                                 receipt["containerJournal"]["sha256"])
        self.assertFalse(Path((directory / "scratch").read_text()).exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(worker_pid, 0)
