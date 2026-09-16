"""Local prototype for the future ``fedops run agent-studio`` command.

This file deliberately stays in the Agent Studio repository until its Docker,
OS, update, Workspace, and GPU branches are verified. It can then be moved into
the FedOps package without coupling that package to this source checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from pathlib import Path

DEFAULT_IMAGE = "gachonccl/fedops-agent-studio:latest"
DEFAULT_CONTAINER = "fedops-agent-studio"
DEFAULT_STUDIO_PORT = 24368
DEFAULT_BIND_ADDRESS = "0.0.0.0"
AGENT_PORT_START = 24400
AGENT_PORT_END = 24499
BRIDGE_PORT = 5602
DEFAULT_WORKSPACE = Path.home() / "fedops-workspace"
UV_CACHE_VOLUME = "fedops-agent-studio-uv"


class RunnerError(RuntimeError):
    """Expected startup failure with a user-facing explanation."""


def _print_banner() -> None:
    assets = Path(__file__).resolve().parent / "assets"
    logo = assets / "fedops_logo_ascii.txt"
    wordmark = assets / "fedops-agent-studio-ascii.txt"
    parts = [
        path.read_text(encoding="utf-8").rstrip()
        for path in (logo, wordmark)
        if path.is_file()
    ]
    print("\n\n".join(parts) or "FedOps Agent Studio", flush=True)


def _status(label: str, detail: str, state: str = "OK") -> None:
    print(f"[{state:<5}] {label:<22} {detail}", flush=True)


def _run(
    command: Sequence[str],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
        check=False,
    )


def _command_text(command: Sequence[str]) -> str:
    return " ".join(str(part) for part in command)


def _docker_cli() -> str:
    docker = shutil.which("docker")
    if not docker:
        raise RunnerError(
            "Docker CLI was not found. Install Docker Desktop or Docker Engine first."
        )
    result = _run([docker, "--version"])
    if result.returncode != 0:
        raise RunnerError((result.stderr or result.stdout or "Docker check failed").strip())
    _status("Docker CLI", (result.stdout or "installed").strip())
    return docker


def _daemon_version(docker: str) -> str | None:
    result = _run([docker, "info", "--format", "{{.ServerVersion}}|{{.OSType}}|{{.Architecture}}"])
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _start_docker_application() -> bool:
    system = platform.system()
    if system == "Darwin":
        return _run(["open", "-a", "Docker"]).returncode == 0
    if system == "Windows":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Docker"
            / "Docker"
            / "Docker Desktop.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Docker"
            / "Docker Desktop.exe",
        ]
        executable = next((path for path in candidates if path.is_file()), None)
        if executable:
            subprocess.Popen([str(executable)], creationflags=subprocess.DETACHED_PROCESS)
            return True
        return False
    if system == "Linux":
        desktop = _run(["systemctl", "--user", "start", "docker-desktop"])
        if desktop.returncode == 0:
            return True
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return _run(["systemctl", "start", "docker"]).returncode == 0
    return False


def _ensure_daemon(docker: str, start_docker: bool, timeout: int) -> str:
    version = _daemon_version(docker)
    if version:
        _status("Docker Engine", version)
        return version
    _status("Docker Engine", "not reachable", "WAIT")
    if not start_docker or not _start_docker_application():
        raise RunnerError(
            "Docker Engine is not running. Start Docker Desktop or Docker Engine and retry."
        )
    _status("Docker Engine", "starting Docker application", "WAIT")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        version = _daemon_version(docker)
        if version:
            _status("Docker Engine", version)
            return version
    raise RunnerError(f"Docker Engine did not become ready within {timeout} seconds.")


def _image_id(docker: str, image: str) -> str | None:
    result = _run([docker, "image", "inspect", "--format", "{{.Id}}", image])
    return (result.stdout or "").strip() if result.returncode == 0 else None


def _prepare_image(docker: str, image: str, pull: bool, dry_run: bool) -> tuple[str | None, str | None]:
    previous = _image_id(docker, image)
    if dry_run:
        _status("Agent Studio image", f"would {'pull' if pull else 'use'} {image}", "PLAN")
        return previous, previous
    if pull:
        _status("Agent Studio image", f"checking latest {image}", "WAIT")
        result = _run([docker, "pull", image], capture=False)
        if result.returncode != 0:
            raise RunnerError(f"Could not pull Agent Studio image: {image}")
    current = _image_id(docker, image)
    if not current:
        raise RunnerError(f"Agent Studio image is unavailable: {image}")
    _status("Agent Studio image", f"ready {image} ({current.removeprefix('sha256:')[:12]})")
    return previous, current


def _nvidia_runtime_available(docker: str) -> bool:
    if platform.system() == "Darwin":
        return False
    result = _run([docker, "info", "--format", "{{json .Runtimes}}"])
    if result.returncode != 0:
        return False
    try:
        runtimes = json.loads((result.stdout or "{}").strip())
    except json.JSONDecodeError:
        return False
    return isinstance(runtimes, dict) and "nvidia" in runtimes


def _use_nvidia(docker: str, requested: str) -> bool:
    available = _nvidia_runtime_available(docker)
    if requested == "nvidia" and not available:
        raise RunnerError(
            "NVIDIA GPU mode was requested, but Docker has no NVIDIA runtime. "
            "Install the NVIDIA Container Toolkit or use --gpu cpu."
        )
    enabled = requested == "nvidia" or (requested == "auto" and available)
    if enabled:
        _status("Container GPU", "NVIDIA runtime exposed with --gpus all")
    else:
        detail = "CPU mode"
        if platform.system() == "Darwin":
            detail += " (Docker Desktop cannot expose the macOS GPU)"
        _status("Container GPU", detail)
    return enabled


def _runtime_directory() -> tuple[Path, Path | None]:
    repository = Path(__file__).resolve().parent.parent
    runtime_source = repository / "services" / "studio-runtime" / "src"
    if runtime_source.is_dir():
        return repository / ".runtime", runtime_source
    return Path.home() / ".fedops-agent-studio" / "runtime", None


def _ensure_bridge_token(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)


def _bridge_health(token_file: Path) -> bool:
    try:
        token = token_file.read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"http://127.0.0.1:{BRIDGE_PORT}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _prepare_host_bridge(workspace: Path, dry_run: bool) -> Path | None:
    runtime_dir, runtime_source = _runtime_directory()
    token_file = runtime_dir / "folder-opener-token"
    if dry_run:
        _status("Host integration", f"would use token {token_file}", "PLAN")
        return token_file
    if runtime_source is None:
        _status("Host integration", "source bridge unavailable; container fallback will be used", "WARN")
        return None
    _ensure_bridge_token(token_file)
    if _bridge_health(token_file):
        _status("Host integration", "folder opener and hardware bridge already running")
        return token_file
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(runtime_source), env.get("PYTHONPATH", "")])
    )
    command = [
        sys.executable,
        "-m",
        "studio_runtime.folder_bridge",
        "--workspace",
        str(workspace),
        "--token-file",
        str(token_file),
        "--host",
        "0.0.0.0",
        "--port",
        str(BRIDGE_PORT),
        "--daemon",
        "--pid-file",
        str(runtime_dir / "folder-opener.pid"),
        "--log-file",
        str(runtime_dir / "folder-opener.log"),
    ]
    result = _run(command, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        _status("Host integration", f"unavailable: {detail}", "WARN")
        return None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _bridge_health(token_file):
            _status("Host integration", "folder opener and hardware bridge running")
            return token_file
        time.sleep(0.25)
    _status("Host integration", "bridge did not become ready; using container fallback", "WARN")
    return None


def build_container_command(
    docker: str,
    *,
    image: str,
    container_name: str,
    workspace: Path,
    studio_port: int,
    token_file: Path | None,
    nvidia: bool,
    bind_address: str = DEFAULT_BIND_ADDRESS,
) -> list[str]:
    command = [
        docker,
        "run",
        "-d",
        "--name",
        container_name,
        "--restart",
        "unless-stopped",
        "-p",
        f"{bind_address}:{studio_port}:24368",
        "-p",
        f"{bind_address}:{AGENT_PORT_START}-{AGENT_PORT_END}:{AGENT_PORT_START}-{AGENT_PORT_END}",
        "-v",
        f"{workspace}:/workspace",
        "--mount",
        f"type=volume,source={UV_CACHE_VOLUME},target=/var/cache/fedops-uv",
        "-e",
        "UV_LINK_MODE=copy",
        "-e",
        "UV_CACHE_DIR=/var/cache/fedops-uv/cache",
        "-e",
        "UV_PYTHON_INSTALL_DIR=/var/cache/fedops-uv/python",
        "-e",
        f"STUDIO_HOST_WORKSPACE_DIR={workspace}",
    ]
    if platform.system() == "Linux":
        command.extend(["--add-host", "host.docker.internal:host-gateway"])
    if nvidia:
        command.extend(["--gpus", "all"])
    if token_file is not None:
        command.extend(
            [
                "-v",
                f"{token_file}:/run/secrets/folder-opener-token:ro",
                "-e",
                f"STUDIO_FOLDER_OPENER_URL=http://host.docker.internal:{BRIDGE_PORT}",
                "-e",
                "STUDIO_FOLDER_OPENER_TOKEN_FILE=/run/secrets/folder-opener-token",
            ]
        )
    command.append(image)
    return command


def _remove_container(docker: str, container_name: str, dry_run: bool) -> None:
    if dry_run:
        _status("Existing container", f"would replace {container_name}", "PLAN")
        return
    _run([docker, "rm", "-f", container_name])


def _stop_container(docker: str, container_name: str, dry_run: bool) -> None:
    inspect = _run(
        [docker, "container", "inspect", "--format", "{{.State.Running}}", container_name]
    )
    if inspect.returncode != 0:
        _status(
            "Agent Studio",
            f"already stopped; container not found ({container_name})",
            "INFO",
        )
        return
    if (inspect.stdout or "").strip().lower() != "true":
        _status("Agent Studio", f"already stopped ({container_name})", "INFO")
        return
    if dry_run:
        _status("Agent Studio", f"would stop {container_name}", "PLAN")
        return
    _status("Agent Studio", f"stopping {container_name}", "WAIT")
    stopped = _run([docker, "stop", "--timeout", "10", container_name])
    if stopped.returncode != 0:
        detail = (stopped.stderr or stopped.stdout or "unknown error").strip()
        raise RunnerError(f"Could not stop Agent Studio: {detail}")
    state = _run(
        [docker, "container", "inspect", "--format", "{{.State.Running}}", container_name]
    )
    if state.returncode == 0 and (state.stdout or "").strip().lower() == "true":
        raise RunnerError("Agent Studio container is still running after the stop request.")
    _status("Agent Studio", f"stopped successfully ({container_name})")


def _stop_host_bridge(workspace: Path, dry_run: bool) -> None:
    runtime_dir, runtime_source = _runtime_directory()
    pid_file = runtime_dir / "folder-opener.pid"
    if not pid_file.is_file():
        _status("Host integration", "already stopped", "INFO")
        return
    if dry_run:
        _status("Host integration", "would stop host bridge", "PLAN")
        return
    if runtime_source is None:
        _status("Host integration", "stop helper unavailable", "WARN")
        return
    token_file = runtime_dir / "folder-opener-token"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(runtime_source), env.get("PYTHONPATH", "")])
    )
    result = _run(
        [
            sys.executable,
            "-m",
            "studio_runtime.folder_bridge",
            "--workspace",
            str(workspace),
            "--token-file",
            str(token_file),
            "--port",
            str(BRIDGE_PORT),
            "--pid-file",
            str(pid_file),
            "--stop",
        ],
        env=env,
    )
    if result.returncode == 0:
        _status("Host integration", "stopped successfully")
    else:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        _status("Host integration", f"could not stop: {detail}", "WARN")


def _remove_previous_image(docker: str, previous: str | None, current: str | None) -> None:
    if not previous or previous == current:
        return
    result = _run([docker, "image", "rm", previous])
    if result.returncode == 0:
        _status("Previous image", f"removed {previous.removeprefix('sha256:')[:12]}")
    else:
        _status("Previous image", "still referenced; kept safely", "WARN")


def _wait_for_studio(port: int, timeout: int = 30) -> None:
    url = f"http://127.0.0.1:{port}/api/v1/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=1.5) as response:
                if response.status == 200:
                    _status("Agent Studio", f"ready at http://localhost:{port}")
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RunnerError(f"Agent Studio did not become ready within {timeout} seconds.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fedops-agent-studio-runner",
        description="Prototype of 'fedops run agent-studio'.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("start", "stop"),
        default="start",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--container-name", default=DEFAULT_CONTAINER)
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    parser.add_argument(
        "--bind-address",
        choices=("0.0.0.0", "127.0.0.1"),
        default=DEFAULT_BIND_ADDRESS,
        help="Docker host bind address; 0.0.0.0 supports localhost and host-network access.",
    )
    parser.add_argument("--gpu", choices=("auto", "cpu", "nvidia"), default="auto")
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-start-docker", action="store_true")
    parser.add_argument("--docker-timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "start":
        _print_banner()
    try:
        workspace = Path(args.workspace).expanduser().resolve()
        if args.action == "stop":
            docker = _docker_cli()
            _ensure_daemon(docker, not args.no_start_docker, args.docker_timeout)
            _stop_container(docker, args.container_name, args.dry_run)
            _stop_host_bridge(workspace, args.dry_run)
            return 0
        system = f"{platform.system()} {platform.release()} ({platform.machine()})"
        _status("Host", system)
        _status("Workspace", str(workspace))
        if not args.dry_run:
            workspace.mkdir(parents=True, exist_ok=True)
        docker = _docker_cli()
        _ensure_daemon(docker, not args.no_start_docker, args.docker_timeout)
        previous, current = _prepare_image(docker, args.image, not args.no_pull, args.dry_run)
        nvidia = _use_nvidia(docker, args.gpu)
        token_file = _prepare_host_bridge(workspace, args.dry_run)
        command = build_container_command(
            docker,
            image=args.image,
            container_name=args.container_name,
            workspace=workspace,
            studio_port=args.port,
            token_file=token_file,
            nvidia=nvidia,
            bind_address=args.bind_address,
        )
        _remove_container(docker, args.container_name, args.dry_run)
        if args.dry_run:
            _status("Container start", _command_text(command), "PLAN")
            return 0
        _remove_previous_image(docker, previous, current)
        started = _run(command)
        if started.returncode != 0:
            detail = (started.stderr or started.stdout or "unknown error").strip()
            raise RunnerError(f"Container start failed: {detail}")
        container_id = (started.stdout or "").strip()
        _status("Container", f"started {container_id[:12]}")
        _wait_for_studio(args.port)
        url = f"http://localhost:{args.port}"
        if not args.no_browser:
            webbrowser.open(url)
            _status("Browser", f"opened {url}")
        _status("Stop command", "fedops stop agent-studio", "INFO")
        return 0
    except RunnerError as error:
        _status("Startup failed", str(error), "ERROR")
        return 1
    except KeyboardInterrupt:
        _status("Startup", "interrupted", "STOP")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
