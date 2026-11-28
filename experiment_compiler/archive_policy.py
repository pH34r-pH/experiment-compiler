"""Caller-owned resource limits for reading a Compiled Experiment."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from zipfile import ZipFile, ZipInfo


class PackageError(ValueError):
    """Input is malformed, unsafe, unsupported or differs from its pinned bytes."""


class ResourcePolicyError(PackageError):
    """A caller's operational budget refused the archive."""

    def __init__(self, budget: str, limit: int, *, member: str | None = None, observed: int | None = None) -> None:
        self.budget = budget
        self.limit = limit
        self.member = member
        self.observed = observed
        target = f" for member {member!r}" if member is not None else ""
        value = f" (observed {observed})" if observed is not None else ""
        super().__init__(f"Local resource policy refused package{target}: {budget} exceeds {limit}{value}")


@dataclass(frozen=True)
class PackageReadPolicy:
    """Optional positive limits on expanded archive data, including the manifest."""

    max_member_bytes: int | None = None
    max_total_bytes: int | None = None
    max_members: int | None = None
    max_compression_ratio: int | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{field.name} must be a positive integer")

    def check_inventory(self, infos: list[ZipInfo]) -> None:
        self._check("max_members", len(infos))
        total = 0
        for info in infos:
            total += info.file_size
            self.check_bytes(info, info.file_size, total)

    def check_bytes(self, info: ZipInfo, member_bytes: int, total_bytes: int) -> None:
        self._check("max_member_bytes", member_bytes, info.filename)
        self._check("max_total_bytes", total_bytes, info.filename)
        ratio = self.max_compression_ratio
        if ratio is not None and member_bytes > info.compress_size * ratio:
            observed = -(-member_bytes // info.compress_size) if info.compress_size else member_bytes
            raise ResourcePolicyError("max_compression_ratio", ratio, member=info.filename, observed=observed)

    def _check(self, budget: str, value: int, member: str | None = None) -> None:
        limit = getattr(self, budget)
        if limit is not None and value > limit:
            raise ResourcePolicyError(budget, limit, member=member, observed=value)


class ArchiveMemberReader:
    """Hash members in one pass, retaining raw bytes only for semantic reads."""

    def __init__(self, archive: ZipFile, policy: PackageReadPolicy | None) -> None:
        self.archive = archive
        self.policy = policy
        self.total_bytes = 0
        if policy is not None:
            policy.check_inventory(archive.infolist())

    def read(self, info: ZipInfo, *, capture: bool = False) -> tuple[str, int, bytearray]:
        digest = hashlib.sha256()
        size = 0
        data = bytearray()
        with self.archive.open(info) as stream:
            while chunk := stream.read(min(1024 * 1024, info.file_size - size + 1)):
                size += len(chunk)
                self.total_bytes += len(chunk)
                if self.policy is not None:
                    self.policy.check_bytes(info, size, self.total_bytes)
                if size > info.file_size:
                    raise PackageError(f"Member size mismatch: {info.filename}")
                digest.update(chunk)
                if capture:
                    data.extend(chunk)
        if size != info.file_size:
            raise PackageError(f"Member size mismatch: {info.filename}")
        return digest.hexdigest(), size, data
