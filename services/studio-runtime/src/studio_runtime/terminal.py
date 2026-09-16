"""Interactive project PTY for a FedOps Federated Task uv environment."""

from __future__ import annotations

import asyncio
import os
import pty
import re
import select
import signal
import struct
import termios
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import fcntl


Emit = Callable[[str], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]
StartTask = Callable[..., object]
MAX_RECENT_OUTPUT_CHARS = 250_000


class TerminalRuntime:
    """One project-scoped bash PTY using an optional uv-managed environment."""

    shell_name = "bash"

    def __init__(
        self,
        project_root: Path,
        environment_path: Path | None = None,
        environment_label: str | None = None,
        environment_status: str | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        if not self.project_root.is_dir():
            raise FileNotFoundError("The terminal project directory was not found.")
        self.environment_path = self._validated_environment(environment_path)
        self.environment_label = _safe_environment_label(environment_label)
        self.environment_status = _safe_environment_status(environment_status)
        self.fd: int | None = None
        self.child_pid: int | None = None
        self.recent_output = ""
        self._reader_started = False
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def context_key(self) -> tuple[Path, Path | None, str | None]:
        return self.project_root, self.environment_path, self.environment_label

    async def ensure_started(
        self,
        start_task: StartTask,
        sleep: Sleep,
        emit: Emit,
    ) -> None:
        if self.child_pid is not None:
            return
        async with self._lock:
            if self.child_pid is not None:
                return
            child_pid, fd = pty.fork()
            if child_pid == 0:
                self._start_child_shell()
            self.child_pid = child_pid
            self.fd = fd
            self.resize(rows=40, cols=120)
            if not self._reader_started:
                start_task(self._read_output, sleep, emit)
                self._reader_started = True

    def write(self, value: str) -> None:
        if self.fd is not None and value:
            os.write(self.fd, value.encode())

    def resize(self, *, rows: int, cols: int) -> None:
        if self.fd is None:
            return
        safe_rows = min(300, max(10, rows))
        safe_cols = min(500, max(20, cols))
        winsize = struct.pack("HHHH", safe_rows, safe_cols, 0, 0)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)

    def clear_recent_output(self) -> None:
        self.recent_output = ""

    def close(self) -> None:
        self._closed = True
        child_pid = self.child_pid
        self.child_pid = None
        fd = self.fd
        self.fd = None
        if child_pid is not None:
            try:
                os.killpg(child_pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def shell_environment(
        self,
        base: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the child environment without creating an implicit root venv."""
        environment = dict(base if base is not None else os.environ)
        environment.update(
            {
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "UTF-8",
                "BASH_SILENCE_DEPRECATION_WARNING": "1",
            }
        )
        environment.pop("VIRTUAL_ENV", None)
        environment.pop("VIRTUAL_ENV_PROMPT", None)
        if self.environment_path is None:
            prefix = ""
            if self.environment_label:
                status = _prompt_environment_status(self.environment_status)
                prefix = f"(env:{self.environment_label}:{status}) "
            environment["PS1"] = f"{prefix}fedops-studio:\\W$ "
            return environment

        environment["VIRTUAL_ENV"] = str(self.environment_path)
        environment["UV_PROJECT_ENVIRONMENT"] = str(self.environment_path)
        environment["PIP_REQUIRE_VIRTUALENV"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PATH"] = (
            f"{self.environment_path / 'bin'}:{environment.get('PATH', '')}"
        )
        label = self.environment_label or self.environment_path.name
        environment["VIRTUAL_ENV_PROMPT"] = f"(uv:{label}) "
        environment["PS1"] = f"(uv:{label}) fedops-studio:\\W$ "
        return environment

    def _validated_environment(self, environment_path: Path | None) -> Path | None:
        if environment_path is None:
            return None
        resolved = environment_path.expanduser().resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as error:
            raise ValueError("The terminal environment escapes the project.") from error
        if not (resolved / "bin" / "python").is_file():
            raise FileNotFoundError("The selected uv environment is not ready.")
        return resolved

    def _start_child_shell(self) -> None:
        os.chdir(self.project_root)
        os.execvpe(
            "/bin/bash",
            ["/bin/bash", "--noprofile", "--norc", "-i"],
            self.shell_environment(),
        )

    async def _read_output(self, sleep: Sleep, emit: Emit) -> None:
        while not self._closed:
            await sleep(0.01)
            fd = self.fd
            if fd is None:
                continue
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                continue
            try:
                raw_output = os.read(fd, 4096)
            except OSError:
                self.fd = None
                self.child_pid = None
                continue
            if not raw_output:
                self.fd = None
                self.child_pid = None
                continue
            output = raw_output.decode(errors="ignore")
            if output:
                self.recent_output = (
                    f"{self.recent_output}{output}"[-MAX_RECENT_OUTPUT_CHARS:]
                )
                await emit(output)


def _safe_environment_label(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9A-Za-z가-힣._ -]+", "-", value).strip(" .-")
    return normalized[:48] or "environment"


def _safe_environment_status(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^a-z-]+", "-", value.lower()).strip("-")
    return normalized[:24] or None


def _prompt_environment_status(value: str | None) -> str:
    if value in {"missing", "outdated"}:
        return "sync-required"
    if value in {"syncing", "error"}:
        return value
    return "not-ready"
