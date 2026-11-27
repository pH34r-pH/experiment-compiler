"""Generated sequences over the runner's retained filesystem and fake engine."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if os.name != "posix":
    raise unittest.SkipTest("retained runner recovery requires POSIX")
try:
    from hypothesis import settings, strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
except ImportError as exc:
    raise unittest.SkipTest("install the optional hypothesis tooling for generated runner checks") from exc

from experiment_compiler import runner
from experiment_compiler.core import json_value
from runner_fixture_helpers import RECOVERY_IDENTITY, prepare_recovery_attempt


_CUTS = st.sampled_from(("none", "marker", "journal", "receipt"))
_CUT_DESTINATIONS = {
    "marker": "container-journal.pending",
    "journal": "container-journal.json",
    "receipt": "runner-receipt.json",
}
_CONTAINER = "c" * 64


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _PublicationInterrupted(OSError):
    pass


class RetainedAttemptMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory()
        self.attempt = Path(self.temporary.name) / "run.attempt"
        self.journal = prepare_recovery_attempt(self.attempt, RECOVERY_IDENTITY, before_launch=True)
        self.receipt = self._read("runner-receipt.json")
        self.receipt.update(executionPhase="preflight", launchIntentPersisted=False, executionNeverStarted=False)
        runner._persist_attempt_receipt(self.attempt, self.receipt)
        self.controller_alive = True
        self.has_launch_evidence = False
        self.cid_retained = False
        self.absence_provable = True
        self.absence_verified = False
        self.last_recovery = None
        self.expected_status = "failed"
        self.retained_snapshots = {}

    def teardown(self):
        self.temporary.cleanup()

    def _read(self, name):
        return json_value((self.attempt / name).read_bytes())

    def _publish(self, cut, operation):
        replace = Path.replace

        def interrupted_replace(source, target):
            if target.name == _CUT_DESTINATIONS.get(cut):
                raise _PublicationInterrupted("synthetic interruption at publication")
            return replace(source, target)

        with patch.object(Path, "replace", new=interrupted_replace):
            try:
                operation()
            except _PublicationInterrupted:
                self.controller_alive = False
                assert cut != "none"
            else:
                assert cut == "none"

    @precondition(lambda self: self.controller_alive and self.receipt["executionPhase"] == "preflight")
    @rule(cut=_CUTS)
    def publish_launch_intent(self, cut):
        runner._transition_process(self.receipt, runner._ProcessPhase.LAUNCH_INTENT)
        self._publish(cut, lambda: runner._journal_update(
            self.attempt, self.journal, self.receipt, launchIntentPersisted=True,
        ))
        self.has_launch_evidence = cut in {"none", "receipt"}

    @precondition(lambda self: self.controller_alive and self.receipt["executionPhase"] == "launch-intent")
    @rule(cut=_CUTS)
    def record_running(self, cut):
        runner._transition_process(self.receipt, runner._ProcessPhase.RUNNING)
        self._publish(cut, lambda: runner._journal_update(
            self.attempt, self.journal, self.receipt, state=runner._CleanupState.RUNNING,
        ))

    @precondition(lambda self: self.controller_alive and self.receipt["executionPhase"] == "running")
    @rule(cut=_CUTS)
    def request_cancellation(self, cut):
        first = self.journal["cancellation"]
        self._publish(cut if first is None else "none",
                      lambda: runner._record_first_cancellation(self.attempt, self.journal, self.receipt))
        if first is not None:
            assert self.journal["cancellation"] == first

    @precondition(lambda self: self.controller_alive and self.receipt["executionPhase"] == "running")
    @rule(terminal_cut=st.booleans())
    def observe_timeout(self, terminal_cut):
        self.receipt.update(exitCode=124, timedOut=True)
        runner._finish_attempt(self.receipt, runner._AttemptStatus.TIMED_OUT)
        self._publish("receipt" if terminal_cut else "none",
                      lambda: runner._persist_attempt_receipt(self.attempt, self.receipt))
        self.controller_alive = False
        if not terminal_cut:
            self.expected_status = "timed-out"

    @precondition(lambda self: self.controller_alive)
    @rule()
    def interrupt_controller(self):
        self.controller_alive = False

    @precondition(lambda self: self.has_launch_evidence and not self.cid_retained)
    @rule()
    def retain_container_identity(self):
        path = self.attempt / "engine-cids" / f"{RECOVERY_IDENTITY['attempt_id']}--fixture.cid"
        path.write_text(_CONTAINER + "\n", encoding="ascii")
        self.cid_retained = True
        self.last_recovery = None

    @rule(provable=st.booleans())
    def change_container_observation(self, provable):
        if self.absence_provable != provable:
            self.last_recovery = None
        self.absence_provable = provable

    def _engine_call(self, attempt, journal, arguments):
        if arguments[:2] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=RECOVERY_IDENTITY["image_id"])
        if arguments[0] == "inspect":
            assert arguments[-1] == _CONTAINER
            return SimpleNamespace(returncode=1, stdout="")
        if arguments[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="" if self.absence_provable else "incomplete listing")
        raise AssertionError("The synthetic engine provided no evidence for a write operation")

    @precondition(lambda self: not self.controller_alive)
    @rule()
    def recover_retained_attempt(self):
        with patch.object(runner, "_controller_is_alive", return_value=False), \
                patch.object(runner, "_reconcile_cwl_process_group"), \
                patch.object(runner, "_engine_call", side_effect=self._engine_call), \
                patch.object(runner.subprocess, "Popen", side_effect=AssertionError("recovery launched a process")):
            result = runner.recover_attempt(self.attempt)
        assert result["status"] == self.expected_status
        summary = result["containerCleanup"]
        if not self.cid_retained and self.has_launch_evidence:
            assert summary["state"] == "unknown" and not summary["allAbsent"]
        elif self.cid_retained and not (self.absence_provable or self.absence_verified):
            assert summary["state"] == "cleanup-failed" and not summary["allAbsent"]
        else:
            assert summary["state"] == "clean" and summary["allAbsent"]
            self.absence_verified = self.cid_retained
        if self.last_recovery is not None:
            assert result == self.last_recovery
        self.last_recovery = result if summary["state"] in {"clean", "unknown"} else None
        assert not (self.attempt / "container-journal.pending").exists()

    @invariant()
    def retain_identity_and_snapshot_bytes(self):
        receipt = self._read("runner-receipt.json")
        assert receipt["attemptId"] == RECOVERY_IDENTITY["attempt_id"]
        assert receipt["status"] in {"started", "failed", "timed-out"}
        for path, expected in self.retained_snapshots.items():
            assert _digest(path) == expected
        for path in self.attempt.glob("container-journal-history/*.json"):
            digest = _digest(path)
            assert digest == path.stem
            self.retained_snapshots[path] = digest


TestRetainedAttemptSequences = RetainedAttemptMachine.TestCase
TestRetainedAttemptSequences.settings = settings(
    max_examples=40, stateful_step_count=20, deadline=None, derandomize=True, database=None,
)
