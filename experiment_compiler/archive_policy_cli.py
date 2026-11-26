"""Shared CLI options for verifier and runner archive budgets."""
from __future__ import annotations

import argparse

from .archive_policy import PackageReadPolicy


_OPTIONS = {
    "max_member_bytes": "maximum expanded bytes for one archive member",
    "max_total_bytes": "maximum expanded bytes across all archive members",
    "max_members": "maximum archive member count, including the manifest",
    "max_compression_ratio": "maximum expanded-to-compressed ratio per archive member",
}


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def add_archive_policy_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("optional local archive resource policy")
    for name, help_text in _OPTIONS.items():
        group.add_argument("--" + name.replace("_", "-"), type=_positive_integer, help=help_text)


def archive_policy_from_args(args: argparse.Namespace) -> PackageReadPolicy | None:
    limits = {name: getattr(args, name) for name in _OPTIONS}
    return PackageReadPolicy(**limits) if any(value is not None for value in limits.values()) else None
