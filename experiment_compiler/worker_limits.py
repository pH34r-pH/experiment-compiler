"""Observe the current CWL controller's effective Linux cgroup v2 limits."""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

from .core import PackageError, json_value


WORKER_PROFILE = "systemd-cgroup-v2-cwl-v1"
_UNIT = re.compile(r"experiment-compiler-worker-[a-z0-9][a-z0-9-]{0,63}\.service\Z")
_LIMIT_FIELDS = {"profile", "unit", "cpuQuotaMicros", "cpuPeriodMicros", "memoryBytes", "tasks"}


class WorkerAdmissionError(PackageError):
    """A controller could not verify an approved worker boundary."""


@dataclass(frozen=True)
class ObservedWorker:
    cpu: Fraction
    memory_bytes: int
    tasks: int

    def check_workload(self, cores: int | float, memory_mib: int | float) -> None:
        if Fraction(str(cores)) > self.cpu or Fraction(str(memory_mib)) * 1024 * 1024 > self.memory_bytes:
            raise WorkerAdmissionError("execution deferred: workload exceeds observed worker CPU or memory ceiling")

    def evidence(self, reported: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile": WORKER_PROFILE,
            "reportedOuterLimits": {key: reported[key] for key in _LIMIT_FIELDS - {"profile", "unit"}},
            "observedOuterLimits": {
                "cpuCores": {"numerator": self.cpu.numerator, "denominator": self.cpu.denominator},
                "memoryBytes": self.memory_bytes,
                "tasks": self.tasks,
            },
            "observationSource": "linux-cgroup-v2-current-process-and-visible-ancestors",
            "outerImage": {"status": "unavailable", "reason": "host-systemd-service"},
        }


def _positive_value(text: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,19}", text):
        raise WorkerAdmissionError("execution deferred: malformed cgroup v2 limit")
    return int(text)


def _finite_limit(text: str) -> int | None:
    return None if text == "max" else _positive_value(text)


def _cpu_limit(text: str) -> Fraction | None:
    parts = text.split()
    if len(parts) != 2:
        raise WorkerAdmissionError("execution deferred: malformed cgroup v2 CPU quota/period")
    quota, period = _finite_limit(parts[0]), _positive_value(parts[1])
    return None if quota is None else Fraction(quota, period)


def _cgroup_for_unit(membership: str, unit: str, cgroup_root: Path) -> Path:
    paths = [record[3:] for record in membership.splitlines() if record.startswith("0::")]
    if len(paths) != 1:
        raise WorkerAdmissionError("execution deferred: no unique cgroup v2 membership")
    path = PurePosixPath(paths[0])
    if not path.is_absolute() or ".." in path.parts or path.name != unit:
        raise WorkerAdmissionError("execution deferred: current process is outside the approved worker service")
    return cgroup_root.joinpath(*path.parts[1:])


def _observed_limit(current: Path, root: Path, name: str, parse):
    values = []
    while current != root:
        try:
            limit = parse((current / name).read_text(encoding="ascii").strip())
        except FileNotFoundError:
            limit = None  # A controller may be enabled only at an ancestor.
        if limit is not None:
            values.append(limit)
        current = current.parent
    if not values:
        raise WorkerAdmissionError(f"execution deferred: no finite observed worker {name} limit")
    return min(values)


def _verify_cgroup_mount(root: Path, mountinfo: str) -> None:
    mounts = []
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        fields = before.split()
        if not separator or len(fields) < 5 or not after.split():
            continue
        mountpoint = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
        if Path(mountpoint) == root:
            mounts.append((fields[3], after.split()[0]))
    if mounts != [("/", "cgroup2")] or not (root / "cgroup.controllers").is_file():
        raise WorkerAdmissionError("execution deferred: a cgroup v2 hierarchy root is required")


def observe_worker(raw_approval: str | None, *, membership_path: Path = Path("/proc/self/cgroup"),
                   cgroup_root: Path = Path("/sys/fs/cgroup"),
                   mountinfo_path: Path = Path("/proc/self/mountinfo")) -> tuple[ObservedWorker, dict[str, Any]]:
    try:
        approval = json_value(raw_approval.encode()) if raw_approval else None
    except PackageError as exc:
        raise WorkerAdmissionError("execution deferred: malformed worker approval") from exc
    if (not isinstance(approval, dict) or set(approval) != _LIMIT_FIELDS or approval["profile"] != WORKER_PROFILE
            or not isinstance(approval["unit"], str) or not _UNIT.fullmatch(approval["unit"])):
        raise WorkerAdmissionError("execution deferred: supported, caller-approved worker profile and service required")
    if any(type(approval[name]) is not int or not 0 < approval[name] < 2**63
           for name in _LIMIT_FIELDS - {"profile", "unit"}):
        raise WorkerAdmissionError("execution deferred: worker approval needs positive CPU, memory, and task limits")
    approved_cpu = Fraction(approval["cpuQuotaMicros"], approval["cpuPeriodMicros"])
    try:
        root = cgroup_root.resolve(strict=True)
        _verify_cgroup_mount(root, mountinfo_path.read_text(encoding="utf-8"))
        membership = membership_path.read_text(encoding="utf-8")
        current = _cgroup_for_unit(membership, approval["unit"], root)
        if current.resolve(strict=True) != current or not current.is_dir():
            raise WorkerAdmissionError("execution deferred: worker cgroup directory is not a direct hierarchy member")
        observed = ObservedWorker(
            _observed_limit(current, root, "cpu.max", _cpu_limit),
            _observed_limit(current, root, "memory.max", _finite_limit),
            _observed_limit(current, root, "pids.max", _finite_limit),
        )
        if membership_path.read_text(encoding="utf-8") != membership:
            raise WorkerAdmissionError("execution deferred: worker cgroup membership changed during observation")
    except (OSError, UnicodeError) as exc:
        raise WorkerAdmissionError("execution deferred: cannot read current worker limits from Linux cgroup v2") from exc
    if observed.cpu > approved_cpu or observed.memory_bytes > approval["memoryBytes"] or observed.tasks > approval["tasks"]:
        raise WorkerAdmissionError("execution deferred: observed worker enforcement is weaker than the approved limits")
    return observed, observed.evidence(approval)
