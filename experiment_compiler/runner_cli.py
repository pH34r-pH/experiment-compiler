"""Command-line wrapper for the explicit experiment-runner handoff."""
from __future__ import annotations

import argparse
import signal
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from .core import PackageError, canonical, write_once
from .runner import run_package


@contextmanager
def _operator_cancellation():
    """Keep the first CLI cancellation while bounded cleanup records evidence."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {number: signal.getsignal(number) for number in signals}
    interruption = None

    def cancel(number, _frame):
        nonlocal interruption
        if interruption is None:
            interruption = KeyboardInterrupt(f"Operator cancellation requested by {signal.Signals(number).name}")
            interruption.signal_number = number
            raise interruption
        # Repeated/mixed signals must not interrupt TERM/KILL or receipt retention.

    try:
        for number in signals:
            signal.signal(number, cancel)
        yield
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt) and (interruption is None or interruption.__context__ is exc):
            # A runner interruption already in flight remains the original cause.
            interruption = exc
        elif interruption is not None and exc is not interruption:
            interruption.add_note(f"Runner raised after cancellation: {type(exc).__name__}: {str(exc)[:1024]}")
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        # Cleanup may catch the handler's interruption. Replay it after the
        # runner exits, without rewriting evidence that it already published.
        if interruption is not None:
            raise interruption


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment-runner")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a digest-pinned lifecycle package through its declared CWL workflow")
    run.add_argument("package", type=Path)
    run.add_argument("--output", type=Path, required=True,
                     help="new immutable result ZIP; writes a sibling .source/ folder for verify/catalog/promotion")
    run.add_argument("--expected-sha256", required=True, help="exact reviewed plan package digest")
    run.add_argument("--allow-workflow-execution", action="store_true",
                     help="confirm that the packaged CWL workflow and its tool code may execute")
    args = parser.parse_args(argv)
    try:
        with _operator_cancellation():
            result = run_package(args.package, args.output, expected_sha256=args.expected_sha256,
                                 allow_workflow_execution=args.allow_workflow_execution)
        sys.stdout.buffer.write(canonical(result))
        return 0 if result["executionStatus"] == "succeeded" else 2
    except KeyboardInterrupt as exc:
        print(f"experiment-runner: {exc or 'Operator cancellation requested'}", file=sys.stderr)
        return 128 + getattr(exc, "signal_number", signal.SIGINT)
    except (PackageError, OSError) as exc:
        print(f"experiment-runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
