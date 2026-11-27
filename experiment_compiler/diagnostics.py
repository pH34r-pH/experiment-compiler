"""Bounded stdlib diagnostics for controller and verification failures."""
from __future__ import annotations

import logging
import re
import sys
from contextlib import contextmanager


def _approved(value, pattern):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else "unavailable"


def report_failure(logger, operation, phase, *, receipt=None, digest=None, error=None):
    receipt = receipt or {}
    fields = (
        operation, phase,
        _approved(receipt.get("attemptId"), r"[0-9a-f]{12}"),
        _approved(digest or receipt.get("planPackageSha256"), r"[0-9a-f]{64}"),
        _approved(type(error).__name__ if error is not None else None, r"[A-Za-z_][A-Za-z0-9_]{0,63}"),
    )
    try:
        logger.warning("operation=%s phase=%s attempt_id=%s package_sha256=%s exception=%s", *fields)
    except Exception:
        # A caller-installed logging sink must not replace the controller's failure.
        pass


@contextmanager
def failure_diagnostics(logger, operation, phase, **identity):
    try:
        yield
    except Exception as error:
        report_failure(logger, operation, phase, error=error, **identity)
        raise


@contextmanager
def cli_diagnostics(verbose: bool):
    if not verbose:
        yield
        return
    logger = logging.getLogger("experiment_compiler")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("experiment-compiler: %(message)s"))
    original_level, original_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        logger.propagate = original_propagate
        handler.close()
