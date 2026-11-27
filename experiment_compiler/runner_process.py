"""CWL process-group lifecycle, identity checks, and operator signal deferral."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import PackageError
from .runner_admission import _MemberSource


@dataclass
class _CwlExecutionPaths:
    attempt_directory: Path
    journal: dict
    receipt: dict
    output_attempt: Path | None
    attempt_out: Path
    provenance: Path
    stdout_path: Path
    stderr_path: Path
    command: list[str]
    work: Path
    environment: dict[str, str]

@dataclass
class _CwlExecutionRequest:
    temporary: Path
    payload: dict[str, _MemberSource]
    runner: dict
    limits: dict
    tmpfs_root: str
    context: tuple[Path, dict, dict] | None

@dataclass
class _CwlCommandRequest:
    temporary: Path
    work: Path
    attempt_out: Path
    provenance: Path
    runner: dict
    cidfile_dir: Path
    cidfile_prefix: str
    tmp_outdir_prefix: Path | None = None

def _run_command(request: _CwlCommandRequest) -> list[str]:
    temporary, work = request.temporary, request.work
    attempt_out, provenance, runner = request.attempt_out, request.provenance, request.runner
    cidfile_dir, cidfile_prefix = request.cidfile_dir, request.cidfile_prefix
    tmp_outdir_prefix = request.tmp_outdir_prefix
    runtime_args = ["--podman"] if runner["executionMode"] == "cwltool-podman" else []
    command = [
        sys.executable, "-m", "cwltool", *runtime_args, "--disable-pull",
        "--strict-memory-limit", "--strict-cpu-limit",
        "--disable-host-provenance", "--disable-user-provenance",
        "--leave-container", "--cidfile-dir", str(cidfile_dir),
        "--cidfile-prefix", cidfile_prefix,
        "--basedir", str(work / "experiment"),
        "--tmpdir-prefix", str(temporary / "cwl-tmp-"),
        "--tmp-outdir-prefix", str(tmp_outdir_prefix or temporary / "cwl-out-"),
        "--outdir", str(attempt_out), "--provenance", str(provenance),
        str(work / "experiment/workflow.cwl"), str(work / "experiment/job.yml"),
    ]
    return command

def _cwl_process_environment(temporary: Path) -> dict[str, str]:
    runner_home = temporary / "runner-home"
    runner_home.mkdir()
    python_lib = str(Path(sys.executable).resolve().parent.parent / "lib")
    inherited = os.environ.get("LD_LIBRARY_PATH")
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": str(runner_home), "TMPDIR": str(temporary),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": f"{python_lib}:{inherited}" if inherited else python_lib,
    }
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment

def _terminate_process_group(process: Any) -> list[str]:
    """Attempt bounded orchestrator cleanup without losing cancellation evidence."""
    errors: list[str] = []
    for signal_number in (15, 9):
        try:
            os.killpg(process.pid, signal_number)
        except ProcessLookupError:
            pass
        except (OSError, KeyboardInterrupt) as exc:
            errors.append(f"Process group signal {signal_number} failed: {type(exc).__name__}: {str(exc)[:1024]}")
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            errors.append(f"Process cleanup wait after signal {signal_number} failed: {type(exc).__name__}: {str(exc)[:1024]}")
    # A failed wait is evidence of incomplete cleanup, not proof of termination.
    if _process_group_exists(process.pid):
        errors.append("CWL orchestrator process group remains after bounded TERM/KILL")
    return errors

def _cwl_process_identity(pid: int) -> dict:
    """Capture the Linux process and session identity before recording running state."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        return {
            "pid": pid, "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "processStartTicks": fields[19], "processGroupId": int(fields[2]),
            "sessionId": int(fields[3]),
        }
    except (OSError, IndexError, ValueError) as exc:
        raise PackageError("Cannot durably identify the launched cwltool process group") from exc

def _process_group_exists(group_id: int) -> bool:
    proc = Path("/proc")
    if not proc.is_dir():
        return True
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        fields = stat[stat.rfind(")") + 2:].split()
        if len(fields) > 2 and fields[0] != "Z" and fields[2].isdigit() and int(fields[2]) == group_id:
            return True
    return False

def _kernel_boot_uuid() -> uuid.UUID:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        parsed = uuid.UUID(value)
    except (OSError, ValueError, AttributeError) as exc:
        raise PackageError("Current Linux kernel boot ID is invalid or unavailable") from exc
    if str(parsed) != value.lower():
        raise PackageError("Current Linux kernel boot ID is not canonical")
    return parsed

def _validated_cwl_process_identity(identity: Any) -> tuple[int, int, bool]:
    if not isinstance(identity, dict):
        raise PackageError("Launch-intent recovery has no durable cwltool process identity")
    pid, pgid = identity.get("pid"), identity.get("processGroupId")
    session = identity.get("sessionId")
    saved_boot_id = identity.get("bootId")
    try:
        saved_boot = uuid.UUID(saved_boot_id) if isinstance(saved_boot_id, str) else None
    except (ValueError, AttributeError) as exc:
        raise PackageError("Retained cwltool process identity has an invalid boot ID") from exc
    current_boot = _kernel_boot_uuid()
    if (saved_boot is None or str(saved_boot) != saved_boot_id.lower() or
            type(pid) is not int or pid <= 0 or pgid != pid or session != pid or
            not isinstance(identity.get("processStartTicks"), str) or
            not identity["processStartTicks"].isdigit()):
        raise PackageError("Retained cwltool process identity is malformed")
    return pid, pgid, saved_boot != current_boot

def _current_cwl_process_fields(pid: int) -> list[str] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PackageError(f"Cannot verify retained cwltool process identity: {exc}") from exc
    return stat[stat.rfind(")") + 2:].split()

def _signal_retained_cwl_group(pgid: int) -> None:
    for number in (15, 9):
        try:
            os.killpg(pgid, number)
        except ProcessLookupError:
            return
        except OSError as exc:
            raise PackageError(f"Cannot stop exact retained cwltool process group: {exc}") from exc
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _process_group_exists(pgid):
            time.sleep(0.05)
        if not _process_group_exists(pgid):
            return
    raise PackageError("Exact retained cwltool process group remains after bounded TERM/KILL")

def _reconcile_cwl_process_group(journal: dict) -> None:
    identity = journal.get("cwlProcessIdentity")
    pid, pgid, previous_boot = _validated_cwl_process_identity(identity)
    if previous_boot:
        # A process group cannot survive a kernel reboot. Numeric PIDs may have
        # been reused since then, so do not inspect or signal the old PID/PGID.
        return
    fields = _current_cwl_process_fields(pid)
    if fields is None:
        if _process_group_exists(pgid):
            raise PackageError("cwltool leader exited but its process group remains; refusing ambiguous cleanup")
        return
    try:
        matches = (fields[19] == identity["processStartTicks"] and int(fields[2]) == pgid and
                   int(fields[3]) == identity["sessionId"])
    except (IndexError, ValueError) as exc:
        raise PackageError("Retained cwltool process stat is malformed") from exc
    if not matches:
        raise PackageError("Retained cwltool PID has been reused or changed session; refusing to signal")
    _signal_retained_cwl_group(pgid)

def _replay_operator_signal(handler, pending, original, failure):
    number, frame = pending
    try:
        handler(number, frame)
    except KeyboardInterrupt as interruption:
        if not isinstance(original, KeyboardInterrupt):
            if failure is not None:
                interruption.add_note(f"Attempt evidence write failed: {type(failure).__name__}: {str(failure)[:1024]}")
            raise
        original.add_note(f"Deferred signal during evidence retention: {str(interruption)[:1024]}")
    # Raise outside the deferred handler's except block so an earlier
    # interruption stays its exception context/cause and retains its notes.
    if isinstance(original, KeyboardInterrupt):
        if failure is not None:
            original.add_note(f"Attempt evidence write failed: {type(failure).__name__}: {str(failure)[:1024]}")
        raise original

@contextmanager
def _defer_operator_signals(original: BaseException | None = None):
    """Deliver the first handled signal after controller evidence writes finish."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "pthread_sigmask"):
        yield
        return
    handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)
                if callable(signal.getsignal(number))}
    if not handlers:
        yield
        return
    pending = None
    failure = None

    def defer(number, frame):
        nonlocal pending
        if pending is None:
            pending = (number, frame)

    try:
        installation_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
        try:
            for number in handlers:
                signal.signal(number, defer)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, installation_mask)
        yield
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        raise
    finally:
        # The evidence phase records delivery order. Block only the short
        # restore/replay transition so a later signal cannot replace its first.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
        try:
            for number, handler in handlers.items():
                signal.signal(number, handler)
            if pending is not None:
                _replay_operator_signal(handlers[pending[0]], pending, original, failure)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
