import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path

from experiment_compiler.runner_logs import LogCapture


CHILD = """
import sys
from pathlib import Path

for stream, value, count in zip((sys.stdout, sys.stderr), (b"o", b"e"), sys.argv[2:]):
    stream.buffer.write(value * int(count))
    stream.buffer.flush()
Path(sys.argv[1]).touch()
sys.stdin.buffer.read(1)
"""


@unittest.skipUnless(sys.platform == "linux", "Linux runner pipes")
class LogCaptureTests(unittest.TestCase):
    def test_live_caps_and_exact_logs(self):
        large = 256 * 1024
        for counts, combined in (((large, 3), 16), ((3, large), 16),
                                 ((7, 7), 10), ((2, 3), 16)):
            with self.subTest(counts=counts, combined=combined):
                self._check_capture(counts, combined)

    def _check_capture(self, counts, combined):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as resources:
            root = Path(temporary)
            stdout, stderr, ready = (root / name for name in ("stdout", "stderr", "ready"))
            capture = LogCapture(resources, stdout, stderr, stream_limit=8, combined_limit=combined)
            capture.start()
            try:
                self._inspect_live_capture(capture, (stdout, stderr), ready, counts)
            finally:
                capture.finish()
            self._check_logs(capture, (stdout, stderr), counts)

    def _inspect_live_capture(self, capture, paths, ready, counts):
        command = [sys.executable, "-c", CHILD, str(ready), *map(str, counts)]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=capture.streams["stdout"].writer,
                              stderr=capture.streams["stderr"].writer) as child:
            try:
                capture.close_writers()
                deadline = time.monotonic() + 5
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    self._check_live_file_sizes(capture, paths)
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "child blocked before it could finish writing both pipes")
                self.assertIsNone(child.poll())
                self._check_live_file_sizes(capture, paths)
                child.stdin.close()
                self.assertEqual(child.wait(timeout=5), 0)
            finally:
                if child.poll() is None:
                    child.kill()

    def _check_live_file_sizes(self, capture, paths):
        sizes = [path.stat().st_size for path in paths]
        self.assertLessEqual(max(sizes), capture.stream_limit)
        self.assertLessEqual(sum(sizes), capture.combined_limit)

    def _check_logs(self, capture, paths, counts):
        summary = capture.summary()["streams"]
        sizes = []
        reasons = set()
        for path, count, char in zip(paths, counts, (b"o", b"e")):
            retained = path.read_bytes()
            self.assertEqual(retained, char * len(retained))
            self.assertEqual(summary[path.name]["observedBytes"], count)
            self.assertEqual(summary[path.name]["retainedBytes"], len(retained))
            self.assertTrue(summary[path.name]["reachedEof"])
            reasons.update(summary[path.name]["truncationReasons"])
            sizes.append(len(retained))
        individually_allowed = sum(min(count, capture.stream_limit) for count in counts)
        self.assertEqual(sum(sizes), min(individually_allowed, capture.combined_limit))
        expected = set()
        if max(counts) > capture.stream_limit:
            expected.add("stream-limit")
        if individually_allowed > capture.combined_limit:
            expected.add("combined-limit")
        self.assertEqual(reasons, expected)

    def test_finish_is_bounded_when_a_writer_keeps_a_pipe_open(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as resources:
            root = Path(temporary)
            capture = LogCapture(resources, root / "stdout", root / "stderr")
            writer = resources.enter_context(os.fdopen(os.dup(capture.streams["stdout"].writer.fileno()), "wb"))
            writer.write(b"first")
            writer.flush()
            capture.start()
            started = time.monotonic()
            capture.finish()
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(capture.summary()["streams"]["stdout"], {
                "observedBytes": 5, "retainedBytes": 5, "reachedEof": False, "truncationReasons": [],
            })
            self.assertIn("stdout log capture ended before EOF", capture.errors())
