"""Native controller cancellation fixtures execute no packaged CWL or containers."""
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
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.core import PackageError, compile_package
from experiment_compiler.runner_cli import _operator_cancellation, main

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
    (directory / "ready").write_text(str(os.getpid()))
    while True:
        time.sleep(1)
    """)
CONTROLLER = textwrap.dedent("""\
    import json, os, sys
    from pathlib import Path
    from unittest.mock import patch
    from experiment_compiler import runner, runner_cli
    directory = Path(sys.argv[1])
    worker = sys.argv[2]
    killpg = os.killpg
    runner_config = runner._runner_config
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
    with patch.dict(os.environ, {
            "EXPERIMENT_RUNNER_SANDBOX": "native test fixture",
            "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
            "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
            "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
    }), patch.object(runner.importlib.metadata, "version", return_value="3.2.20260720092025"), \\
            patch.object(runner, "_require_bounded_tmpfs"), \\
            patch.object(runner, "_runner_config", side_effect=configure), \\
            patch.object(runner, "_run_command", side_effect=native_command), \\
            patch.object(runner.os, "killpg", side_effect=record_signal):
        raise SystemExit(runner_cli.main(json.loads(sys.argv[3])))
    """)


class RunnerSignalContextTests(unittest.TestCase):
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
                def runner(*args, **kwargs):
                    try:
                        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    except KeyboardInterrupt:
                        pass  # Same boundary as the bounded cleanup helper.
                    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
                    if error is not None:
                        raise error
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


@unittest.skipUnless(os.name == "posix", "runner process-group cancellation requires POSIX")
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

    def _wait_for(self, path, process):
        deadline = time.monotonic() + 10
        while not path.exists():
            self.assertIsNone(process.poll(), "controller exited before native fixture readiness")
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
        self.assertEqual((directory / "result.zip").exists(), timeout_cleanup)
        self.assertEqual((directory / "result.source").exists(), timeout_cleanup)
        if timeout_cleanup:
            with zipfile.ZipFile(directory / "result.zip") as archive:
                member = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
                self.assertEqual(json.loads(archive.read(member)), receipt)
        self.assertFalse(Path((directory / "scratch").read_text()).exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(worker_pid, 0)
