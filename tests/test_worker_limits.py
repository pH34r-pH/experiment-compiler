"""Linux worker admission fixtures use only synthetic cgroup counters."""
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from experiment_compiler.worker_limits import WORKER_PROFILE, WorkerAdmissionError, observe_worker


class WorkerLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        self.root = directory / "cgroup fixture"
        self.parent = self.root / "system.slice"
        self.unit = "experiment-compiler-worker-fixture.service"
        self.leaf = self.parent / self.unit
        self.leaf.mkdir(parents=True)
        (self.root / "cgroup.controllers").write_text("cpu memory pids\n", encoding="ascii")
        self.membership = directory / "membership"
        self.membership.write_text(f"0::/system.slice/{self.unit}\n", encoding="ascii")
        self.mountinfo = directory / "mountinfo"
        root_display = str(self.root).replace(" ", "\\040")
        self.mountinfo.write_text(f"29 1 0:23 / {root_display} rw - cgroup2 cgroup rw\n", encoding="utf-8")
        self.approval = {"profile": WORKER_PROFILE, "unit": self.unit,
                         "cpuQuotaMicros": 100000, "cpuPeriodMicros": 100000,
                         "memoryBytes": 512 * 1024 * 1024, "tasks": 128}
        self.write_limits(self.leaf, "100000 100000", str(self.approval["memoryBytes"]), "128")

    @staticmethod
    def write_limits(directory, cpu, memory, pids):
        for name, value in (("cpu.max", cpu), ("memory.max", memory), ("pids.max", pids)):
            (directory / name).write_text(value + "\n", encoding="ascii")

    def observe(self, approval):
        return observe_worker(json.dumps(approval) if approval is not None else None,
                              membership_path=self.membership, cgroup_root=self.root, mountinfo_path=self.mountinfo)

    def test_finite_parent_limits_are_applied_to_the_current_service(self):
        self.write_limits(self.parent, "50000 100000", str(256 * 1024 * 1024), "64")
        worker, evidence = self.observe(self.approval)
        self.assertEqual((worker.cpu, worker.memory_bytes, worker.tasks), (Fraction(1, 2), 256 * 1024 * 1024, 64))
        self.assertEqual(evidence, {
            "profile": WORKER_PROFILE,
            "reportedOuterLimits": {
                "cpuQuotaMicros": 100000,
                "cpuPeriodMicros": 100000,
                "memoryBytes": 512 * 1024 * 1024,
                "tasks": 128,
            },
            "observedOuterLimits": {
                "cpuCores": {"numerator": 1, "denominator": 2},
                "memoryBytes": 256 * 1024 * 1024,
                "tasks": 64,
            },
            "observationSource": "linux-cgroup-v2-current-process-and-visible-ancestors",
            "outerImage": {"status": "unavailable", "reason": "host-systemd-service"},
        })
        worker.check_workload(0.5, 256)
        for cores, memory in ((0.75, 256), (0.5, 256.125)):
            with self.subTest(cores=cores, memory=memory), self.assertRaisesRegex(WorkerAdmissionError, "workload exceeds"):
                worker.check_workload(cores, memory)

    def test_profile_or_environment_claim_alone_does_not_admit_a_worker(self):
        for approval in (None, {**self.approval, "profile": "unknown"}):
            with self.subTest(approval=approval), self.assertRaisesRegex(WorkerAdmissionError, "worker profile"):
                self.observe(approval)
        self.membership.write_text("0::/\n", encoding="ascii")
        with self.assertRaisesRegex(WorkerAdmissionError, "outside the approved worker service"):
            self.observe(self.approval)

    def test_missing_malformed_unlimited_or_weaker_observations_fail_closed(self):
        for cpu, memory, pids, diagnostic in (
            ("100000 0", "536870912", "128", "malformed"),
            ("max 100000", "536870912", "128", "no finite"),
            ("100000 100000", "max", "128", "no finite"),
            ("100000 100000", "536870912", "max", "no finite"),
            ("100001 100000", "536870912", "128", "weaker"),
            ("100000 100000", "536870913", "128", "weaker"),
            ("100000 100000", "536870912", "129", "weaker"),
        ):
            self.write_limits(self.leaf, cpu, memory, pids)
            with self.subTest(cpu=cpu, memory=memory, pids=pids), self.assertRaisesRegex(WorkerAdmissionError, diagnostic):
                self.observe(self.approval)
        (self.leaf / "pids.max").unlink()
        with self.assertRaisesRegex(WorkerAdmissionError, "no finite observed worker pids.max"):
            self.observe(self.approval)
