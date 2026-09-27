"""Exact unit conversions for CWL v1.2 resource fields."""
from __future__ import annotations

import math

from .core import PackageError

MIB = 1024 * 1024


def bytes_to_mib_minimum(value: int) -> int:
    """Convert a positive byte budget to CWL MiB, rounding up conservatively."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PackageError("A byte resource requirement must be a positive integer; unknown is not zero")
    return (value + MIB - 1) // MIB


def seconds_to_cwl_limit(value: int | float) -> int:
    """Convert a positive time budget to CWL ToolTimeLimit seconds, rounding up."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PackageError("A CWL time limit must be numeric; unknown is not zero")
    if not math.isfinite(value) or value <= 0:
        raise PackageError("A CWL time limit must be finite and positive")
    return math.ceil(value)
