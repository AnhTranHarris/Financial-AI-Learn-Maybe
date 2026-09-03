from __future__ import annotations

"""Small reusable owner for one isolated JSON-lines subprocess."""

from collections import deque
from enum import StrEnum
from queue import Empty, Queue
import subprocess
from threading import Thread
from typing import Callable, Mapping, TextIO


RESOURCE_BLOCK_PATTERNS = (
    "out of memory",
    "cannot allocate memory",
    "failed to allocate",
    "memoryerror",
    "paging file is too small",
)


class ProviderWorkerState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    RESOURCE_BLOCKED = "resource_blocked"
    FAILED = "failed"


def _pump_stdout(stream: TextIO, destination: Queue[str | None]) -> None:
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            destination.put(line.rstrip("\r\n"))
    finally:
        destination.put(None)


def _pump_stderr(stream: TextIO, destination: deque[str]) -> None:
    while True:
        line = stream.readline()
        if not line:
            break
        rendered = line.strip()
        if rendered:
            destination.append(rendered)


class IsolatedJsonLineWorker:
    """Owns exactly one child process; never scans or kills unrelated processes."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        environment: Mapping[str, str],
        startup_timeout_seconds: int,
        request_timeout_seconds: int,
        shutdown_timeout_seconds: int = 15,
        stderr_line_limit: int = 40,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        for value, label, lower, upper in (
            (startup_timeout_seconds, "startup", 5, 600),
            (request_timeout_seconds, "request", 5, 600),
            (shutdown_timeout_seconds, "shutdown", 1, 60),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(
                    f"provider_{label}_timeout_must_be_{lower}_to_{upper}_seconds"
                )
        if not command or any(not part for part in command):
            raise ValueError("provider_worker_command_invalid")
        if type(stderr_line_limit) is not int or not 1 <= stderr_line_limit <= 200:
            raise ValueError("provider_stderr_line_limit_out_of_bounds")
        self.command = command
        self.environment = dict(environment)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._stderr_line_limit = stderr_line_limit
        self._popen_factory = popen_factory
        self._state = ProviderWorkerState.STOPPED
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: Queue[str | None] = Queue()
        self._stderr_lines: deque[str] = deque(maxlen=stderr_line_limit)
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None

    @property
    def state(self) -> ProviderWorkerState:
        process = self._process
        if (
            process is not None
            and self._state in {
                ProviderWorkerState.STARTING,
                ProviderWorkerState.READY,
                ProviderWorkerState.BUSY,
            }
            and process.poll() is not None
        ):
            self._join_readers()
            self._state = self._failure_state()
        return self._state

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def stderr_excerpt(self) -> str:
        rendered = " ".join(" ".join(self._stderr_lines).split())
        return rendered[:1000] if rendered else "no_provider_error_text"

    def start(self) -> tuple[ProviderWorkerState, str | None]:
        if self.state is not ProviderWorkerState.STOPPED:
            raise RuntimeError(
                f"provider_worker_start_requires_stopped:{self.state.value}"
            )
        self._stdout_queue = Queue()
        self._stderr_lines = deque(maxlen=self._stderr_line_limit)
        self._state = ProviderWorkerState.STARTING
        try:
            process = self._popen_factory(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self.environment,
            )
        except OSError as exc:
            self._stderr_lines.append(f"{type(exc).__name__}: {exc}")
            self._state = self._failure_state()
            return self._state, None
        self._process = process
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._terminate()
            self._close_streams()
            self._state = ProviderWorkerState.FAILED
            return self._state, None

        self._stdout_thread = Thread(
            target=_pump_stdout,
            args=(process.stdout, self._stdout_queue),
            daemon=True,
            name="dusty-provider-stdout",
        )
        self._stdout_thread.start()
        self._stderr_thread = Thread(
            target=_pump_stderr,
            args=(process.stderr, self._stderr_lines),
            daemon=True,
            name="dusty-provider-stderr",
        )
        self._stderr_thread.start()

        try:
            line = self._stdout_queue.get(timeout=self.startup_timeout_seconds)
        except Empty:
            self._terminate()
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        if line is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        self._state = ProviderWorkerState.READY
        return self._state, line

    def transact(self, payload: str) -> tuple[ProviderWorkerState, str | None]:
        if self.state is not ProviderWorkerState.READY:
            return self.state, None
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        self._state = ProviderWorkerState.BUSY
        try:
            process.stdin.write(payload)
            process.stdin.write("\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._stderr_lines.append(f"{type(exc).__name__}: {exc}")
            self._terminate()
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        try:
            line = self._stdout_queue.get(timeout=self.request_timeout_seconds)
        except Empty:
            self._terminate()
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        if line is None:
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        if process.poll() is not None:
            self._join_readers()
            self._state = self._failure_state()
            return self._state, None
        self._state = ProviderWorkerState.READY
        return self._state, line

    def stop(self) -> ProviderWorkerState:
        process = self._process
        if process is None:
            self._state = ProviderWorkerState.STOPPED
            return self._state
        if process.poll() is None and self._state is ProviderWorkerState.BUSY:
            self._terminate()
        elif process.poll() is None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=self.shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                self._terminate()
        self._join_readers()
        self._close_streams()
        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._state = ProviderWorkerState.STOPPED
        return self._state

    def restart(self) -> tuple[ProviderWorkerState, str | None]:
        self.stop()
        return self.start()

    def _failure_state(self) -> ProviderWorkerState:
        error = " ".join(self._stderr_lines).lower()
        if any(pattern in error for pattern in RESOURCE_BLOCK_PATTERNS):
            return ProviderWorkerState.RESOURCE_BLOCKED
        return ProviderWorkerState.FAILED

    def _join_readers(self) -> None:
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=1)

    def _close_streams(self) -> None:
        process = self._process
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass

    def _terminate(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
