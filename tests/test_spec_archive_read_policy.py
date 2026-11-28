"""Independent cases for the archive read policy specification (ARP-001..ARP-013).

Expected values come from spec/features/archive-read-policy/index.md, never from
the implementation's output.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import math
import unittest
import zipfile
from unittest.mock import patch

from experiment_compiler.archive_policy import (
    ArchiveMemberReader,
    PackageError,
    PackageReadPolicy,
    ResourcePolicyError,
)
from experiment_compiler.archive_policy_cli import (
    ARCHIVE_POLICY_OPTIONS,
    add_archive_policy_options,
    archive_policy_from_args,
)

BUDGETS = ("max_member_bytes", "max_total_bytes", "max_members", "max_compression_ratio")


def archive(members: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as writer:
        for name, data in members.items():
            writer.writestr(name, data)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


class ArchiveReadPolicyCases(unittest.TestCase):
    def test_ARP_001_unset_budgets_impose_no_limit(self) -> None:
        payload = b"\0" * 2_000_000
        for policy in (None, PackageReadPolicy()):
            with self.subTest(policy=policy), archive({"big.bin": payload}) as source:
                reader = ArchiveMemberReader(source, policy)
                digest, size, _ = reader.read(source.getinfo("big.bin"))
                self.assertEqual(size, len(payload))
                self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_ARP_002_budgets_must_be_positive_integers(self) -> None:
        for budget in BUDGETS:
            for value in (0, -1, True, 1.5, "3"):
                with self.subTest(budget=budget, value=value), self.assertRaisesRegex(ValueError, budget):
                    PackageReadPolicy(**{budget: value})
            self.assertEqual(getattr(PackageReadPolicy(**{budget: 7}), budget), 7)

    def test_ARP_003_declared_inventory_is_checked_inclusively_before_opening(self) -> None:
        with archive({"a.txt": b"a" * 40, "b.txt": b"b" * 60}) as source:
            infos = source.infolist()
            exact = {
                "max_member_bytes": max(info.file_size for info in infos),
                "max_total_bytes": sum(info.file_size for info in infos),
                "max_members": len(infos),
                "max_compression_ratio": max(math.ceil(info.file_size / info.compress_size) for info in infos),
            }
            for budget, value in exact.items():
                with self.subTest(budget=budget):
                    ArchiveMemberReader(source, PackageReadPolicy(**{budget: value}))
                    with patch.object(zipfile.ZipFile, "open") as opener, \
                            self.assertRaises(ResourcePolicyError) as refusal:
                        ArchiveMemberReader(source, PackageReadPolicy(**{budget: value - 1}))
                    self.assertEqual(refusal.exception.budget, budget)
                    self.assertEqual(refusal.exception.limit, value - 1)
                    opener.assert_not_called()

    def test_ARP_004_observed_running_total_is_checked_before_keeping_bytes(self) -> None:
        with archive({"m.bin": b"x" * 10}, compression=zipfile.ZIP_STORED) as source:
            reader = ArchiveMemberReader(source, PackageReadPolicy(max_total_bytes=15))
            info = source.getinfo("m.bin")
            reader.read(info)
            self.assertEqual(reader.total_bytes, 10)
            with self.assertRaises(ResourcePolicyError) as refusal:
                reader.read(info, capture=True)
            self.assertEqual(refusal.exception.budget, "max_total_bytes")
            self.assertEqual(refusal.exception.limit, 15)
            self.assertEqual(refusal.exception.member, "m.bin")

    def test_ARP_005_compression_ratio_budget(self) -> None:
        with archive({"zeros.bin": b"\0" * 50_000}) as source:
            info = source.getinfo("zeros.bin")
            ratio = math.ceil(info.file_size / info.compress_size)
            self.assertGreater(ratio, 2)
            ArchiveMemberReader(source, PackageReadPolicy(max_compression_ratio=ratio)).read(info)
            with self.assertRaises(ResourcePolicyError) as refusal:
                ArchiveMemberReader(source, PackageReadPolicy(max_compression_ratio=ratio - 1))
            self.assertEqual(refusal.exception.budget, "max_compression_ratio")
            self.assertEqual(refusal.exception.member, "zeros.bin")

    def test_ARP_006_refusal_shape_and_message(self) -> None:
        with archive({"one.txt": b"12345", "two.txt": b"6"}) as source:
            with self.assertRaises(ResourcePolicyError) as member_refusal:
                ArchiveMemberReader(source, PackageReadPolicy(max_member_bytes=4))
            with self.assertRaises(ResourcePolicyError) as count_refusal:
                ArchiveMemberReader(source, PackageReadPolicy(max_members=1))
            with self.assertRaises(ResourcePolicyError) as total_refusal:
                ArchiveMemberReader(source, PackageReadPolicy(max_total_bytes=5))
        member, count, total = member_refusal.exception, count_refusal.exception, total_refusal.exception
        self.assertEqual((total.budget, total.limit, total.member), ("max_total_bytes", 5, "two.txt"))
        self.assertIn("'two.txt'", str(total))
        for error in (member, count):
            self.assertIsInstance(error, PackageError)
            self.assertIn("Local resource policy refused package", str(error))
        self.assertEqual((member.budget, member.limit, member.member), ("max_member_bytes", 4, "one.txt"))
        self.assertIn("'one.txt'", str(member))
        self.assertIn("max_member_bytes exceeds 4", str(member))
        self.assertEqual((count.budget, count.limit, count.member), ("max_members", 1, None))
        self.assertNotIn("member '", str(count))
        self.assertIn("max_members exceeds 1", str(count))

    def test_ARP_007_digest_size_capture_and_size_mismatch(self) -> None:
        payload = b"specified content\n" * 3
        with archive({"data.txt": payload}, compression=zipfile.ZIP_STORED) as source:
            info = source.getinfo("data.txt")
            reader = ArchiveMemberReader(source, None)
            digest, size, data = reader.read(info)
            self.assertEqual((digest, size, bytes(data)), (hashlib.sha256(payload).hexdigest(), len(payload), b""))
            self.assertEqual(bytes(reader.read(info, capture=True)[2]), payload)
            overstated = copy.copy(info)
            overstated.file_size = len(payload) + 1
            with self.assertRaisesRegex(PackageError, "data.txt") as mismatch:
                reader.read(overstated)
            self.assertNotIsInstance(mismatch.exception, ResourcePolicyError)

    def test_ARP_008_command_line_options(self) -> None:
        parser = argparse.ArgumentParser(prog="reader", exit_on_error=False)
        add_archive_policy_options(parser)
        self.assertIsNone(archive_policy_from_args(parser.parse_args([])))
        policy = archive_policy_from_args(parser.parse_args(["--max-members", "3", "--max-total-bytes", "100"]))
        self.assertEqual(policy, PackageReadPolicy(max_members=3, max_total_bytes=100))
        self.assertEqual(ARCHIVE_POLICY_OPTIONS,
                         {"max-member-bytes", "max-total-bytes", "max-members", "max-compression-ratio"})
        for option in ("--max-member-bytes", "--max-total-bytes", "--max-members", "--max-compression-ratio"):
            for value in ("0", "-1", "1.5", "many"):
                with self.subTest(option=option, value=value):
                    stderr = io.StringIO()
                    strict = argparse.ArgumentParser(prog="reader")
                    add_archive_policy_options(strict)
                    with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                        strict.parse_args([option, value])
                    self.assertIn("positive integer", stderr.getvalue())

    def test_ARP_009_refusal_reports_the_observed_value(self) -> None:
        with archive({"one.txt": b"12345", "two.txt": b"6"}) as source:
            with self.assertRaises(ResourcePolicyError) as member_refusal:
                ArchiveMemberReader(source, PackageReadPolicy(max_member_bytes=4))
            with self.assertRaises(ResourcePolicyError) as count_refusal:
                ArchiveMemberReader(source, PackageReadPolicy(max_members=1))
        self.assertEqual(member_refusal.exception.observed, 5)
        self.assertIn("observed 5", str(member_refusal.exception))
        self.assertEqual(count_refusal.exception.observed, 2)
        self.assertIn("observed 2", str(count_refusal.exception))

    def test_ARP_010_compression_ratio_observed_value_and_zero_compressed_size(self) -> None:
        def info(expanded: int, compressed: int) -> zipfile.ZipInfo:
            declared = zipfile.ZipInfo("member.bin")
            declared.file_size, declared.compress_size = expanded, compressed
            return declared

        with self.assertRaises(ResourcePolicyError) as refusal:
            PackageReadPolicy(max_compression_ratio=3).check_bytes(info(100, 7), 100, 100)
        self.assertEqual(refusal.exception.observed, 15)
        with self.assertRaises(ResourcePolicyError) as zero:
            PackageReadPolicy(max_compression_ratio=1).check_bytes(info(5, 0), 5, 5)
        self.assertEqual((zero.exception.budget, zero.exception.observed), ("max_compression_ratio", 5))
        PackageReadPolicy(max_compression_ratio=1).check_bytes(info(0, 0), 0, 0)

    def test_ARP_011_total_bytes_counts_the_refused_read(self) -> None:
        with archive({"m.bin": b"x" * 10}, compression=zipfile.ZIP_STORED) as source:
            reader = ArchiveMemberReader(source, PackageReadPolicy(max_total_bytes=15))
            reader.read(source.getinfo("m.bin"))
            with self.assertRaises(ResourcePolicyError):
                reader.read(source.getinfo("m.bin"))
            self.assertGreater(reader.total_bytes, 15)

    def test_ARP_012_first_exceeded_budget_in_order_is_named(self) -> None:
        with archive({"big.bin": b"\0" * 1000, "other.txt": b"a"}) as source:
            with self.assertRaises(ResourcePolicyError) as count:
                ArchiveMemberReader(source, PackageReadPolicy(max_members=1, max_member_bytes=1))
            self.assertEqual(count.exception.budget, "max_members")
        with archive({"big.bin": b"\0" * 1000}) as source:
            for budgets, expected in (
                ({"max_member_bytes": 10, "max_total_bytes": 10, "max_compression_ratio": 1}, "max_member_bytes"),
                ({"max_total_bytes": 10, "max_compression_ratio": 1}, "max_total_bytes"),
                ({"max_compression_ratio": 1}, "max_compression_ratio"),
            ):
                with self.subTest(expected=expected), self.assertRaises(ResourcePolicyError) as refusal:
                    ArchiveMemberReader(source, PackageReadPolicy(**budgets))
                self.assertEqual(refusal.exception.budget, expected)

    def test_ARP_013_command_line_integer_forms(self) -> None:
        parser = argparse.ArgumentParser(prog="reader")
        add_archive_policy_options(parser)
        for text, value in (("+3", 3), (" 3 ", 3), ("1_000", 1000)):
            with self.subTest(text=text):
                self.assertEqual(parser.parse_args(["--max-members", text]).max_members, value)


if __name__ == "__main__":
    unittest.main()
