"""Run and reconcile bounded CWL processes and their containers."""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import runner_admission, runner_receipts
from .core import PackageError, bounded_read, safe_path
from .diagnostics import report_failure
from .runner_logs import LOG_STREAM_LIMIT, LogCapture


_LOGGER = logging.getLogger(__package__ + ".runner")
_ENGINE_IMAGE_ID = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})\Z")
_MAX_RECOVERY_AGE_SECONDS = 7 * 24 * 60 * 60


def _materialize(root: Path, payload: dict[str, runner_admission._MemberSource]) -> None:
    for relative, data in payload.items():
        safe_path(relative)
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            with data.open("rb") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)


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
    payload: dict[str, runner_admission._MemberSource]
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


def _bounded_output_root(tmpfs_root: str, limits: dict) -> Path | None:
    value = os.environ.get("EXPERIMENT_RUNNER_OUTPUT_TMPFS_ROOT")
    if not value:
        return None
    output_root = _require_bounded_output_tmpfs(Path(value), limits["outdirMiB"])
    scratch_root = Path(tmpfs_root).resolve(strict=True)
    if (output_root == scratch_root or output_root.is_relative_to(scratch_root) or
            scratch_root.is_relative_to(output_root)):
        raise PackageError("execution deferred: bounded output tmpfs must be separate from CWL scratch storage")
    return output_root


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


def _settle_cwl_group(process: Any, *, terminate: bool) -> tuple[bool, list[str]]:
    if process is None or (not terminate and not _process_group_exists(process.pid)):
        return False, []
    errors = _terminate_process_group(process)
    return _process_group_exists(process.pid), errors


def _cleanup_after_cwl_exit(paths: _CwlExecutionPaths, group_remaining: bool) -> list[str]:
    if group_remaining:
        runner_receipts._journal_update(
            paths.attempt_directory, paths.journal, paths.receipt, state=runner_receipts._CleanupState.UNKNOWN,
        )
        report_failure(_LOGGER, "run", "cleanup", receipt=paths.receipt)
        return ["Container cleanup skipped because the CWL process group remains alive"]
    errors: list[str] = []
    _cleanup_container_journal(paths.attempt_directory, paths.journal, paths.receipt, errors)
    if errors:
        report_failure(_LOGGER, "run", "cleanup", receipt=paths.receipt)
    return errors


def _handle_cwl_interrupt(paths: _CwlExecutionPaths, process: Any, interruption: KeyboardInterrupt) -> None:
    group_remaining, errors = _settle_cwl_group(process, terminate=True)
    for error in errors + _cleanup_after_cwl_exit(paths, group_remaining):
        interruption.add_note(error)
    _remove_bounded_output(paths.output_attempt, interruption)


def _handle_cwl_error(paths: _CwlExecutionPaths, process: Any, exc: Exception) -> None:
    group_remaining, errors = _settle_cwl_group(process, terminate=True)
    container_errors = _cleanup_after_cwl_exit(paths, group_remaining)
    _remove_bounded_output(paths.output_attempt, exc)
    for error in errors + container_errors:
        if hasattr(exc, "add_note"):
            exc.add_note(error)
    if not isinstance(exc, (OSError, subprocess.SubprocessError, PackageError)):
        raise PackageError(f"CWL runner could not complete its controller handoff: {exc}") from exc


def _record_cwl_exit(paths: _CwlExecutionPaths, return_code: int | None, *, unresolved: bool) -> None:
    if unresolved:
        paths.journal["cwlProcessGroupUnresolved"] = True
        runner_receipts._journal_update(
            paths.attempt_directory, paths.journal, paths.receipt, state=runner_receipts._CleanupState.UNKNOWN,
        )
    else:
        paths.journal["cwlProcessExit"] = {"returnCode": return_code}
        runner_receipts._journal_update(paths.attempt_directory, paths.journal, paths.receipt)


def _wait_cwl_process(paths: _CwlExecutionPaths, process: Any, timeout: int) -> tuple[int, bool, list[str]]:
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        unresolved, errors = _settle_cwl_group(process, terminate=True)
        _record_cwl_exit(paths, process.returncode, unresolved=unresolved)
        return process.returncode or 124, True, errors
    unresolved, errors = _settle_cwl_group(process, terminate=False)
    _record_cwl_exit(paths, return_code, unresolved=unresolved)
    return return_code, False, errors


def _finish_cwl_logs(paths: _CwlExecutionPaths, capture: LogCapture,
                     original: BaseException | None = None) -> None:
    if paths.receipt.get("logCapture") is None:
        with _defer_operator_signals(original):
            capture.finish()
            paths.receipt["logCapture"] = capture.summary()
            paths.receipt.setdefault("collectionErrors", []).extend(capture.errors())


@contextmanager
def _capture_cwl_logs(paths: _CwlExecutionPaths):
    with ExitStack() as resources:
        capture = LogCapture(resources, paths.stdout_path, paths.stderr_path)
        try:
            with _defer_operator_signals():
                capture.start()
            yield capture
        except BaseException as exc:
            _finish_cwl_logs(paths, capture, exc)
            raise
        else:
            _finish_cwl_logs(paths, capture)


def _run_cwl_process(paths: _CwlExecutionPaths, timeout: int, capture: LogCapture) -> tuple[int, bool, list[str]]:
    process = None
    try:
        with _defer_operator_signals():
            process = subprocess.Popen(
                paths.command, cwd=paths.work, env=paths.environment,
                stdout=capture.streams["stdout"].writer, stderr=capture.streams["stderr"].writer,
                start_new_session=True,
            )
            capture.close_writers()
            paths.journal["cwlProcessIdentity"] = _cwl_process_identity(process.pid)
            runner_receipts._transition_process(paths.receipt, runner_receipts._ProcessPhase.RUNNING)
            runner_receipts._journal_update(paths.attempt_directory, paths.journal, paths.receipt,
                            state=runner_receipts._CleanupState.RUNNING, processStartedAt=datetime.now(timezone.utc).isoformat())
        outcome = _wait_cwl_process(paths, process, timeout)
        paths.receipt.update(exitCode=outcome[0], timedOut=outcome[1])
        _finish_cwl_logs(paths, capture)
        return outcome
    except KeyboardInterrupt as interruption:
        with _defer_operator_signals(interruption):
            if process is not None:
                runner_receipts._record_first_cancellation(paths.attempt_directory, paths.journal, paths.receipt)
            _handle_cwl_interrupt(paths, process, interruption)
        raise
    except Exception as exc:
        _handle_cwl_error(paths, process, exc)
        raise


def _remove_bounded_output(output_attempt: Path | None, error_target: BaseException) -> None:
    if output_attempt is None or not output_attempt.exists():
        return
    try:
        shutil.rmtree(output_attempt)
    except OSError as exc:
        note = f"bounded output tmpfs cleanup failed: {type(exc).__name__}: {str(exc)[:512]}"
        if hasattr(error_target, "add_note"):
            error_target.add_note(note)


def _prepare_attempt_context(request: _CwlExecutionRequest) -> tuple[Path, dict, dict]:
    temporary, runner = request.temporary, request.runner
    context = request.context
    if context is not None:
        attempt_directory, journal, receipt = context
        if not (attempt_directory / "engine-cids").exists():
            (attempt_directory / "engine-cids").mkdir(mode=0o700)
        return attempt_directory, journal, receipt
    attempt_directory, journal, receipt = None, None, None
    if attempt_directory is None:
        attempt_directory = temporary / "attempt-evidence"
        attempt_directory.mkdir(mode=0o700)
    cid_directory = attempt_directory / "engine-cids"
    if not cid_directory.exists():
        cid_directory.mkdir(mode=0o700)
    if journal is None:
        journal = {
            "schema": runner_receipts._CONTAINER_JOURNAL_SCHEMA, "attemptId": "test-attempt",
            "engine": runner_admission._container_engine(runner),
            "enginePath": "/usr/bin/podman" if runner["executionMode"] == "cwltool-podman" else "/usr/bin/docker",
            "workerImageRef": runner.get("workerImageBase"), "workerImageId": None,
            "reportedWorkerImageId": "unspecified", "state": runner_receipts._CleanupState.PREPARED,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "cidfilePrefix": "test-attempt-", "containers": [], "cancellation": None,
        }
    return attempt_directory, journal, {"executionPhase": runner_receipts._ProcessPhase.LAUNCH_INTENT}


def _prepare_cwl_paths(request: _CwlExecutionRequest) -> _CwlExecutionPaths:
    temporary, payload = request.temporary, request.payload
    runner, limits, tmpfs_root = request.runner, request.limits, request.tmpfs_root
    attempt_directory, journal, receipt = _prepare_attempt_context(request)
    _require_bounded_tmpfs(temporary, Path(tmpfs_root), limits["tmpdirMiB"])
    output_root = _bounded_output_root(tmpfs_root, limits)
    work = temporary / "package"
    work.mkdir()
    _materialize(work, payload)
    output_attempt = output_root / journal["attemptId"] if output_root else None
    if output_attempt is not None:
        if output_attempt.exists() or output_attempt.is_symlink():
            raise PackageError("bounded output attempt path already exists; refusing reuse")
        output_attempt.mkdir(mode=0o700)
    attempt_out = output_attempt / "outputs" if output_attempt else temporary / "outputs"
    prefix = output_attempt / "cwl-out-" if output_attempt else None
    provenance = temporary / "cwlprov"
    command = _run_command(_CwlCommandRequest(
        temporary, work, attempt_out, provenance, runner,
        attempt_directory / "engine-cids", journal["cidfilePrefix"], prefix,
    ))
    return _CwlExecutionPaths(
        attempt_directory, journal, receipt, output_attempt, attempt_out, provenance,
        temporary / "stdout.log", temporary / "stderr.log", command, work,
        _cwl_process_environment(temporary),
    )


def _execute_cwl(request: _CwlExecutionRequest) -> dict:
    limits = request.limits
    paths = _prepare_cwl_paths(request)
    with _capture_cwl_logs(paths) as capture:
        return_code, timed_out, cleanup_errors = _run_cwl_process(paths, request.runner["maxWallSeconds"], capture)
    attempt_directory, journal, receipt = paths.attempt_directory, paths.journal, paths.receipt
    output_attempt, attempt_out, provenance = paths.output_attempt, paths.attempt_out, paths.provenance
    stdout_path, stderr_path = paths.stdout_path, paths.stderr_path
    collection_errors = [*cleanup_errors, *receipt["collectionErrors"]]
    if journal.get("cwlProcessGroupUnresolved") is True:
        runner_receipts._journal_update(
            attempt_directory, journal, receipt, state=runner_receipts._CleanupState.UNKNOWN,
        )
        collection_errors.append("Container cleanup skipped because the CWL process group remains alive")
    else:
        _cleanup_container_journal(attempt_directory, journal, receipt, collection_errors)
    receipt["containerCleanup"] = runner_receipts._container_cleanup_summary(journal)
    runner_receipts._journal_receipt_reference(attempt_directory, journal, receipt)
    # Execution collection is bounded by the reviewed worker/CWL resource
    # envelope, not by a Compiled Experiment archive-size policy.
    output_budget = limits.get("outdirMiB", limits["tmpdirMiB"]) * 1024 * 1024
    provenance_budget = limits["tmpdirMiB"] * 1024 * 1024
    output_files = _collect_execution_tree(
        attempt_out, "output", collection_errors, output_budget
    )
    provenance_files = _collect_execution_tree(
        provenance, "provenance", collection_errors, provenance_budget
    )
    if output_attempt is not None and output_attempt.exists():
        try:
            shutil.rmtree(output_attempt)
        except OSError as exc:
            collection_errors.append(f"bounded output tmpfs cleanup failed: {type(exc).__name__}: {str(exc)[:512]}")
    if not output_files and return_code == 0:
        collection_errors.append("CWL runner succeeded without producing any admitted result files")
    return {
        "returnCode": return_code,
        "timedOut": timed_out,
        "stdout": _read_execution_log(stdout_path, "stdout", collection_errors),
        "stderr": _read_execution_log(stderr_path, "stderr", collection_errors),
        "outputs": output_files,
        "provenance": provenance_files,
        "collectionErrors": collection_errors,
        "logCapture": receipt["logCapture"],
        "containerJournal": receipt.get("containerJournal"),
        "containerCleanup": runner_receipts._container_cleanup_summary(journal),
        "scratchStorage": {"enforcement": "dedicated-mounted-tmpfs", "capMiB": limits["tmpdirMiB"]},
        "outputStorage": ({"enforcement": "separate-mounted-tmpfs", "capMiB": limits["outdirMiB"]}
                          if output_attempt is not None else {"enforcement": "worker-tmpfs-only", "hardCap": False}),
    }


def _collect_execution_tree(root: Path, label: str, errors: list[str],
                            max_bytes: int | None = None) -> dict[str, bytes]:
    collected: dict[str, bytes] = {}
    if not root.exists():
        return collected
    try:
        _collect_tree(root, collected, max_bytes)
    except (PackageError, OSError) as exc:
        collected.clear()
        errors.append(f"{label} collection rejected: {exc}")
    return collected


def _read_execution_log(path: Path, label: str, errors: list[str]) -> bytes:
    try:
        return bounded_read(path, LOG_STREAM_LIMIT)
    except (PackageError, OSError) as exc:
        errors.append(f"{label} rejected: {exc}")
        return b""


def _normalize_engine_image_id(value: str) -> str | None:
    """Canonicalize a complete engine image ID; never accept a short ID."""
    if not isinstance(value, str):
        return None
    match = _ENGINE_IMAGE_ID.fullmatch(value.strip())
    if not match:
        return None
    return f"sha256:{match.group(1).lower()}"


def _engine_image_reference_matches(observed: str, pinned: str) -> bool:
    """Allow Podman to omit a tag while preserving the pinned repository and digest."""
    if not isinstance(observed, str) or not isinstance(pinned, str):
        return False
    digest_pattern = re.compile(r"^(.+)@(sha256:[0-9a-f]{64})$")
    observed_match = digest_pattern.fullmatch(observed)
    pinned_match = digest_pattern.fullmatch(pinned)
    if observed_match is None or pinned_match is None:
        return False
    if observed == pinned:
        return True
    pinned_name, pinned_digest = pinned_match.groups()
    observed_name, observed_digest = observed_match.groups()
    if observed_digest != pinned_digest:
        return False
    # A registry port is before the final slash; only a colon in the final
    # path component can be the optional image tag.
    last_slash = pinned_name.rfind("/")
    tag_separator = pinned_name.rfind(":")
    if tag_separator <= last_slash:
        return False
    tag = pinned_name[tag_separator + 1:]
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        return False
    return observed_name == pinned_name[:tag_separator]


def _new_container_journal(attempt_directory: Path, attempt_id: str, runner: dict) -> dict:
    cid_directory = attempt_directory / "engine-cids"
    cid_directory.mkdir(mode=0o700)
    cid_directory.chmod(0o700)
    if cid_directory.is_symlink() or not cid_directory.is_dir():
        raise PackageError("container cidfile directory is not a regular controller-owned directory")
    if hasattr(os, "getuid") and cid_directory.stat().st_uid != os.getuid():
        raise PackageError("container cidfile directory is not owned by the current runner")
    engine = runner_admission._container_engine(runner)
    engine_path = shutil.which(engine, path="/usr/bin:/bin")
    if not engine_path or not Path(engine_path).is_absolute():
        raise PackageError(f"Configured container engine {engine!r} is not available on the trusted PATH")
    journal = {
        "schema": runner_receipts._CONTAINER_JOURNAL_SCHEMA,
        "attemptId": attempt_id,
        "engine": engine,
        "enginePath": str(Path(engine_path).resolve(strict=True)),
        "workerImageRef": runner.get("workerImageBase"),
        "reportedWorkerImageId": os.environ.get("EXPERIMENT_RUNNER_WORKER_IMAGE", "unknown"),
        "workerImageId": None,
        "controller": _controller_identity(),
        "state": runner_receipts._CleanupState.PREPARED,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cidfilePrefix": f"{attempt_id}-",
        "containers": [],
        "cancellation": None,
    }
    image = _engine_call(attempt_directory, journal,
                         ["image", "inspect", "--format", "{{.Id}}", str(journal["workerImageRef"])])
    image_id = _normalize_engine_image_id(image.stdout)
    if image.returncode != 0 or image_id is None:
        raise PackageError("Pinned workflow image could not be verified by the configured engine")
    journal["workerImageId"] = image_id
    return journal


def _controller_identity() -> dict:
    identity = {"pid": os.getpid(), "bootId": None, "processStartTicks": None}
    try:
        identity["bootId"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{os.getpid()}/stat").read_text()
        identity["processStartTicks"] = stat[stat.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        pass
    return identity


def _controller_is_alive(identity: Any) -> bool:
    if not isinstance(identity, dict) or type(identity.get("pid")) is not int or identity["pid"] <= 0:
        raise PackageError("Attempt journal has no trusted controller process identity")
    current = _controller_identity()
    if (not isinstance(identity.get("bootId"), str) or
            not re.fullmatch(r"[0-9a-fA-F-]{36}", identity["bootId"]) or
            not isinstance(identity.get("processStartTicks"), str) or
            not identity["processStartTicks"].isdigit() or
            not isinstance(current.get("bootId"), str) or
            not isinstance(current.get("processStartTicks"), str)):
        raise PackageError("Attempt journal lacks a verifiable Linux controller identity")
    if identity.get("bootId") != current.get("bootId"):
        return False
    pid = identity["pid"]
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PackageError(f"Cannot verify prior controller process state: {exc}") from exc
    try:
        ticks = stat[stat.rfind(")") + 2:].split()[19]
    except IndexError as exc:
        raise PackageError("Prior controller process identity is malformed") from exc
    if ticks != identity.get("processStartTicks"):
        return False
    raise PackageError("The original attempt controller is still alive; refusing concurrent cleanup")


def _discover_container_ids(attempt_directory: Path, journal: dict,
                            receipt: dict, errors: list[str]) -> None:
    cid_directory = attempt_directory / "engine-cids"
    if cid_directory.is_symlink() or not cid_directory.is_dir():
        errors.append("Container cidfile directory is missing or unsafe")
        runner_receipts._journal_update(
            attempt_directory, journal, receipt, state=runner_receipts._CleanupState.CLEANUP_FAILED,
        )
        return
    files = sorted(cid_directory.glob(f"{journal['cidfilePrefix']}*.cid"))
    if len(files) > runner_receipts._MAX_ATTEMPT_CONTAINERS:
        errors.append("Container journal exceeds its bounded CID count")
        runner_receipts._journal_update(
            attempt_directory, journal, receipt, state=runner_receipts._CleanupState.CLEANUP_FAILED,
        )
        return
    known = {entry.get("id") for entry in journal["containers"] if isinstance(entry, dict)}
    for path in files:
        try:
            if path.is_symlink() or not path.is_file():
                raise PackageError("CID file is not a regular file")
            if path.stat().st_size > 128:
                raise PackageError("CID file exceeds its limit")
            container_id = path.read_text(encoding="ascii").strip()
            if not runner_receipts._CONTAINER_ID.fullmatch(container_id):
                raise PackageError("CID file does not contain a full lowercase container ID")
        except (OSError, UnicodeError, PackageError) as exc:
            errors.append(f"Container CID evidence rejected ({path.name}): {exc}")
            continue
        if container_id in known:
            continue
        entry = {
            "engine": journal["engine"],
            "id": container_id,
            "imageId": journal["workerImageId"],
            "imageRef": journal["workerImageRef"],
            "cidfile": f"engine-cids/{path.name}",
            "discoveredAt": datetime.now(timezone.utc).isoformat(),
            "inspectState": "unknown",
            "cleanup": {"status": runner_receipts._ContainerCleanupPhase.PENDING, "absentAfterCleanup": False},
        }
        journal["containers"].append(entry)
        known.add(container_id)
        # Record every newly discovered engine identity before contacting it.
        runner_receipts._journal_update(attempt_directory, journal, receipt)


def _container_engine_environment(attempt_directory: Path) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(attempt_directory / "runner-home"),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1"}
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "LD_LIBRARY_PATH"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def _engine_call(attempt_directory: Path, journal: dict, arguments: list[str]) -> subprocess.CompletedProcess:
    executable = journal.get("enginePath")
    expected = shutil.which(journal.get("engine", ""), path="/usr/bin:/bin")
    if (not isinstance(executable, str) or not Path(executable).is_absolute() or
            not expected or Path(executable).resolve(strict=True) != Path(expected).resolve(strict=True)):
        raise PackageError("Container cleanup engine path differs from the trusted engine binary")
    try:
        return subprocess.run([executable, *arguments], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=8, check=False,
                              env=_container_engine_environment(attempt_directory))
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackageError(f"Container engine request failed: {type(exc).__name__}: {str(exc)[:512]}") from exc


def _container_present(attempt_directory: Path, journal: dict, container_id: str) -> tuple[bool, str | None]:
    inspected = _engine_call(attempt_directory, journal,
                             ["inspect", "--format", "{{.Id}} {{.State.Status}} {{.Image}} {{.Config.Image}}", container_id])
    if inspected.returncode == 0:
        fields = inspected.stdout.strip().split()
        if len(fields) != 4 or fields[0].lower() != container_id:
            raise PackageError("Container inspect returned a different or malformed ID")
        inspected_image_id = _normalize_engine_image_id(fields[2])
        if (inspected_image_id != journal["workerImageId"]
                or not _engine_image_reference_matches(fields[3], journal["workerImageRef"])):
            raise PackageError("Journaled container image differs from the pinned and receipt-bound worker image")
        return True, fields[1].lower()
    listing = _engine_call(attempt_directory, journal, ["ps", "--all", "--no-trunc", "--format", "{{.ID}}"])
    ids = {line.strip().lower() for line in listing.stdout.splitlines() if line.strip()}
    if listing.returncode != 0 or any(not runner_receipts._CONTAINER_ID.fullmatch(item) for item in ids):
        raise PackageError("Container engine could not prove whether the exact journaled ID remains")
    if container_id in ids:
        raise PackageError("Container engine lists the ID but inspect cannot establish its state")
    return False, None


def _cleanup_container_journal(attempt_directory: Path, journal: dict, receipt: dict,
                               errors: list[str]) -> None:
    _discover_container_ids(attempt_directory, journal, receipt, errors)
    for entry in journal["containers"]:
        container_id = entry["id"]
        cleanup = entry["cleanup"]
        if cleanup.get("absentAfterCleanup") is True:
            continue
        try:
            present, state = _container_present(attempt_directory, journal, container_id)
            entry["inspectState"] = state or "absent"
            if present and state == "running":
                runner_receipts._transition_container_cleanup(cleanup, runner_receipts._ContainerCleanupPhase.STOPPING,
                                              requestedAt=datetime.now(timezone.utc).isoformat())
                runner_receipts._journal_update(attempt_directory, journal, receipt)
                stopped = _engine_call(attempt_directory, journal,
                                       ["stop", "--time", "5", container_id])
                present, state = _container_present(attempt_directory, journal, container_id)
                if present and state == "running":
                    raise PackageError(f"Container stop did not stop exact ID (exit {stopped.returncode})")
            if present:
                if state not in {"created", "exited", "stopped"}:
                    raise PackageError(f"Container state {state!r} is busy or unknown; refusing removal")
                runner_receipts._transition_container_cleanup(cleanup, runner_receipts._ContainerCleanupPhase.REMOVING,
                                              removeRequestedAt=datetime.now(timezone.utc).isoformat())
                runner_receipts._journal_update(attempt_directory, journal, receipt)
                removed = _engine_call(attempt_directory, journal, ["rm", container_id])
                present, state = _container_present(attempt_directory, journal, container_id)
                if present:
                    raise PackageError(f"Container removal did not remove exact ID (exit {removed.returncode}, state {state})")
            runner_receipts._transition_container_cleanup(cleanup, runner_receipts._ContainerCleanupPhase.ABSENT,
                                          completedAt=datetime.now(timezone.utc).isoformat())
            entry["inspectState"] = "absent"
            runner_receipts._journal_update(attempt_directory, journal, receipt)
        except PackageError as exc:
            runner_receipts._transition_container_cleanup(cleanup, runner_receipts._ContainerCleanupPhase.FAILED,
                                          error=f"{type(exc).__name__}: {str(exc)[:512]}")
            errors.append(f"Container cleanup failed for exact ID {container_id}: {exc}")
            runner_receipts._journal_update(attempt_directory, journal, receipt)
    state = runner_receipts._CleanupState.CLEANUP_FAILED if errors else runner_receipts._CleanupState.CLEAN
    if journal.get("state") != state:
        runner_receipts._journal_update(attempt_directory, journal, receipt, state=state)


def _validate_recovery_age_and_controller(journal: dict) -> None:
    try:
        created = datetime.fromisoformat(journal["createdAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("Recovery journal has no valid creation time") from exc
    if created.tzinfo is None:
        raise PackageError("Recovery journal creation time needs an explicit timezone")
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if age < -300 or age > _MAX_RECOVERY_AGE_SECONDS:
        raise PackageError("Recovery journal is stale or has a future timestamp")
    _controller_is_alive(journal.get("controller"))


def _validate_recovery_worker(attempt_directory: Path, receipt: dict, journal: dict) -> None:
    """Require the retained plan identity, worker image, and container inventory to match."""
    runner_receipts._validate_recovery_receipt_worker(receipt, journal)
    runner_receipts._validate_recovery_process_evidence(receipt)
    runner_receipts._validate_recovery_engine(receipt, journal)
    _validate_recovery_age_and_controller(journal)
    runner_receipts._validate_recovery_container_inventory(attempt_directory, journal)
    image = _engine_call(attempt_directory, journal,
                         ["image", "inspect", "--format", "{{.Id}}", journal["workerImageRef"]])
    if (image.returncode != 0 or
            _normalize_engine_image_id(image.stdout) != journal["workerImageId"]):
        raise PackageError("Recovery engine no longer resolves the exact retained workflow image ID")


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


def _collect_tree(root: Path, collected: dict[str, bytes], max_bytes: int | None) -> None:
    total = sum(len(value) for value in collected.values())
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PackageError("CWL output contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        safe_path(relative)
        remaining = None if max_bytes is None else max_bytes - total
        if remaining is not None and (remaining < 0 or path.stat().st_size > remaining):
            raise PackageError("CWL output exceeds the declared worker resource budget")
        content = bounded_read(path, remaining)
        total += len(content)
        if max_bytes is not None and total > max_bytes:
            raise PackageError("CWL output exceeds the declared worker resource budget")
        collected[relative] = content


def _require_bounded_tmpfs(temporary: Path, root: Path, limit_mib: int) -> None:
    resolved_root = root.resolve(strict=True)
    resolved_temporary = temporary.resolve(strict=True)
    if not resolved_temporary.is_relative_to(resolved_root):
        raise PackageError("execution deferred: runner temporary directory is outside the declared tmpfs")
    _require_dedicated_tmpfs(root, limit_mib, "scratch")
    if resolved_temporary.stat().st_dev != resolved_root.stat().st_dev:
        raise PackageError("execution deferred: runner temporary directory must use the admitted scratch filesystem")


def _require_bounded_output_tmpfs(root: Path, limit_mib: int) -> Path:
    return _require_dedicated_tmpfs(root, limit_mib, "output")


def _require_dedicated_tmpfs(root: Path, limit_mib: int, role: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise PackageError(f"execution deferred: {role} root must be an existing regular directory")
    resolved = root.resolve(strict=True)
    current = root.absolute()
    while current != current.parent:
        if current.is_symlink():
            raise PackageError(f"execution deferred: {role} root path contains a symlink")
        current = current.parent
    try:
        entries = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PackageError(f"execution deferred: cannot verify the {role} tmpfs mount") from exc
    mount_types = []
    for line in entries:
        try:
            before, after = line.split(" - ", 1)
            fields = before.split()
            mountpoint = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
            if Path(mountpoint) == resolved:
                mount_types.append(after.split()[0])
        except (IndexError, ValueError):
            continue
    if mount_types != ["tmpfs"]:
        raise PackageError(f"execution deferred: {role} root must be an unambiguous dedicated tmpfs mountpoint")
    capacity = shutil.disk_usage(resolved).total
    cap_bytes = int(limit_mib * 1024 * 1024)
    if capacity != cap_bytes:
        raise PackageError(f"execution deferred: {role} tmpfs size must exactly match the declared worker {role} cap")
    return resolved
