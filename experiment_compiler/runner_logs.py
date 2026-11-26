"""Bounded capture of stdout and stderr on the Linux execution worker."""
from __future__ import annotations

import os
import selectors
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


LOG_STREAM_LIMIT = 4 * 1024 * 1024
LOG_COMBINED_LIMIT = 2 * LOG_STREAM_LIMIT
_CHUNK_BYTES = 64 * 1024
_POLL_SECONDS = 0.05
_FINAL_DRAIN_SECONDS = 0.5


@dataclass
class _CapturedStream:
    output: BinaryIO
    writer: BinaryIO
    observed: int = 0
    retained: int = 0
    eof: bool = False
    reasons: set[str] = field(default_factory=set)
    error: str | None = None

    def retain(self, chunk: bytes, stream_limit: int, remaining: int) -> int:
        size = len(chunk)
        stream_remaining = stream_limit - self.retained
        self.observed += size
        if size > stream_remaining:
            self.reasons.add("stream-limit")
        if min(size, stream_remaining) > remaining:
            self.reasons.add("combined-limit")
        prefix = chunk[:min(stream_remaining, remaining)]
        if not prefix or self.error is not None:
            return 0
        try:
            written = self.output.write(prefix)
            self.retained += written
            if written != len(prefix):
                self.error = "short write to retained log"
            return written
        except OSError as exc:
            self.error = str(exc)[:512]
            return 0

    def summary(self) -> dict:
        return {
            "observedBytes": self.observed,
            "retainedBytes": self.retained,
            "reachedEof": self.eof,
            "truncationReasons": sorted(self.reasons),
        }


class LogCapture:
    def __init__(self, resources: ExitStack, stdout: Path, stderr: Path, *,
                 stream_limit: int = LOG_STREAM_LIMIT, combined_limit: int = LOG_COMBINED_LIMIT):
        self.stream_limit = stream_limit
        self.combined_limit = combined_limit
        self.remaining = combined_limit
        self.selector = resources.enter_context(selectors.DefaultSelector())
        self.streams = {
            "stdout": self._open_stream(resources, stdout),
            "stderr": self._open_stream(resources, stderr),
        }
        self.done = threading.Event()
        self.error: str | None = None
        self.reader = threading.Thread(target=self._drain, name="cwl-log-capture")

    def _open_stream(self, resources: ExitStack, path: Path) -> _CapturedStream:
        output = resources.enter_context(path.open("xb", buffering=0))
        read_fd, write_fd = os.pipe()
        reader = resources.enter_context(os.fdopen(read_fd, "rb", buffering=0))
        writer = resources.enter_context(os.fdopen(write_fd, "wb", buffering=0))
        stream = _CapturedStream(output, writer)
        os.set_blocking(read_fd, False)
        self.selector.register(reader, selectors.EVENT_READ, stream)
        return stream

    def start(self) -> None:
        self.reader.start()

    def close_writers(self) -> None:
        for stream in self.streams.values():
            stream.writer.close()

    def finish(self) -> None:
        self.close_writers()
        self.done.set()
        if self.reader.ident is not None:
            self.reader.join()

    def _drain(self) -> None:
        try:
            self._drain_until_eof()
        except OSError as exc:
            self.error = str(exc)[:512]
            for key in self.selector.get_map().values():
                key.fileobj.close()

    def _drain_until_eof(self) -> None:
        deadline = None
        while self.selector.get_map():
            # A leftover writer must not keep terminal evidence pending.
            if self.done.is_set():
                if deadline is None:
                    deadline = time.monotonic() + _FINAL_DRAIN_SECONDS
                if time.monotonic() >= deadline:
                    break
            for key, _ in self.selector.select(_POLL_SECONDS):
                self._read(key)

    def _read(self, key: selectors.SelectorKey) -> None:
        stream: _CapturedStream = key.data
        try:
            chunk = os.read(key.fd, _CHUNK_BYTES)
        except BlockingIOError:
            return
        except OSError as exc:
            stream.error = str(exc)[:512]
            self.selector.unregister(key.fd)
            key.fileobj.close()
            return
        if not chunk:
            stream.eof = True
            self.selector.unregister(key.fd)
            return
        self.remaining -= stream.retain(chunk, self.stream_limit, self.remaining)

    def summary(self) -> dict:
        return {
            "policy": "truncate-prefix-and-drain",
            "perStreamLimitBytes": self.stream_limit,
            "combinedLimitBytes": self.combined_limit,
            "streams": {name: stream.summary() for name, stream in self.streams.items()},
        }

    def errors(self) -> list[str]:
        errors = [f"CWL log capture failed: {self.error}"] if self.error is not None else []
        for name, stream in self.streams.items():
            if stream.reasons:
                errors.append(f"{name} log truncated by " + ", ".join(sorted(stream.reasons)))
            if stream.error is not None:
                errors.append(f"{name} log capture failed: {stream.error}")
            if not stream.eof:
                errors.append(f"{name} log capture ended before EOF")
        return errors
