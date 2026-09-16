"""Open an allowed Workspace directory in the host operating system."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


TASK_DATA_DIRECTORY = "federated-tasks"


def _open_via_host_bridge(bridge_url: str, token_file: Path | None, relative: Path) -> None:
    recovery = "On the computer running Docker, run: fedops run agent-studio --repair-host."
    if token_file is None or not token_file.is_file():
        raise FileNotFoundError(f"The host folder opener token is unavailable. {recovery}")
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"The host folder opener token is empty. {recovery}")
    request = urllib.request.Request(
        f"{bridge_url.rstrip('/')}/open",
        data=json.dumps({"relativePath": relative.as_posix()}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    # The bridge is on this machine. System/HTTP proxies must not receive the
    # request or its authentication header (including macOS system proxies).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=4.0) as response:
            if response.status != 200:
                raise RuntimeError(f"Host folder opener returned HTTP {response.status}.")
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            raise RuntimeError(f"Host folder opener authentication failed. {recovery}") from error
        detail = ""
        if error.code == 400:
            try:
                payload = json.loads(error.read(4096))
                if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                    detail = " " + payload["error"][:500]
            except (ValueError, OSError):
                pass
        raise RuntimeError(
            f"Host folder opener rejected the request (HTTP {error.code}).{detail}"
        ) from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, socket.gaierror):
            detail = "Docker could not resolve the host folder opener address."
        elif isinstance(reason, TimeoutError):
            detail = "The connection to the host folder opener timed out. Check the host firewall/VPN."
        elif isinstance(reason, ConnectionRefusedError):
            detail = "The host folder opener refused the connection; its process may have stopped."
        else:
            detail = "The container could not connect to the host folder opener."
        raise RuntimeError(f"{detail} {recovery}") from error


def task_data_inventory(directory: Path) -> dict[str, object]:
    """Return a cheap, deterministic snapshot of one Task-local data folder."""
    files = sorted(
        (path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        stat = path.stat()
        relative = path.relative_to(directory).as_posix()
        total_bytes += stat.st_size
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))
    return {
        "hasEntries": bool(files),
        "fileCount": len(files),
        "totalBytes": total_bytes,
        "fingerprint": digest.hexdigest(),
    }


def open_directory(directory: Path) -> None:
    target = directory.expanduser().resolve()
    if not target.is_dir():
        raise FileNotFoundError("The local project directory was not found.")
    if sys.platform == "darwin":
        command = ["open", str(target)]
    elif sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return
    else:
        executable = shutil.which("xdg-open")
        if not executable:
            raise FileNotFoundError("xdg-open is not installed on this Linux host.")
        command = [executable, str(target)]
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def open_workspace_directory(
    workspace: Path,
    project_root: Path,
    *,
    bridge_url: str | None = None,
    token_file: Path | None = None,
    display_workspace: Path | None = None,
    bridge_relative_workspace: Path | None = None,
) -> dict[str, object]:
    workspace_root = workspace.expanduser().resolve()
    project = project_root.expanduser().resolve()
    try:
        relative_path = project.relative_to(workspace_root)
    except ValueError as error:
        raise ValueError("The project directory is outside the local Workspace.") from error
    if project.parent != workspace_root or not project.is_dir():
        raise ValueError("Only a first-level local Workspace project can be opened.")

    if bridge_url:
        bridge_workspace = bridge_relative_workspace or Path()
        if bridge_workspace.is_absolute() or ".." in bridge_workspace.parts:
            raise ValueError("The host Workspace bridge path is invalid.")
        bridge_project = bridge_workspace / relative_path
        _open_via_host_bridge(bridge_url, token_file, bridge_project)
        mode = "host-bridge"
    else:
        open_directory(project)
        mode = "native"

    display_root = (display_workspace or workspace_root).expanduser()
    return {
        "opened": True,
        "path": str(display_root / relative_path),
        "mode": mode,
    }


def prepare_project_data_directory(
    local_data_root: Path,
    project_name: str,
    *,
    local_project_id: str,
    display_local_data_root: Path | None = None,
    purpose: str = "dataset",
) -> dict[str, object]:
    """Create one account- and Task-scoped dataset directory outside Task source."""
    normalized_name = str(project_name or "").strip()
    if (
        not normalized_name
        or normalized_name in {".", ".."}
        or Path(normalized_name).name != normalized_name
        or Path(normalized_name).is_absolute()
    ):
        raise ValueError("The local project name cannot be used for a data directory.")

    if purpose not in {"dataset", "validation"}:
        raise ValueError("Invalid data folder purpose.")
    root = local_data_root.expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    requested = root / TASK_DATA_DIRECTORY / normalized_name / purpose
    if purpose == 'validation' and any(p.is_symlink() for p in (requested, requested.parent, requested.parent.parent)):
        raise ValueError('Validation folder must be separate and must not use symlinks.')
    data_directory = requested.resolve()
    try:
        data_directory.relative_to(root)
    except ValueError as error:
        raise ValueError("The Task data directory is outside the account local-data root.") from error
    data_directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    display_root = (display_local_data_root or root).expanduser()
    display_path = display_root / TASK_DATA_DIRECTORY / normalized_name / purpose
    return {
        "localProjectId": local_project_id,
        "containerPath": str(data_directory),
        "hostPath": str(display_path),
        **task_data_inventory(data_directory),
        "source": "task-local-data",
    }


def open_project_data_directory(
    local_data_root: Path,
    project_name: str,
    *,
    local_project_id: str,
    bridge_url: str | None = None,
    token_file: Path | None = None,
    display_local_data_root: Path | None = None,
    bridge_relative_local_data_root: Path | None = None,
    purpose: str = "dataset",
) -> dict[str, object]:
    """Open the prepared dataset directory without exposing arbitrary host paths."""
    binding = prepare_project_data_directory(
        local_data_root,
        project_name,
        local_project_id=local_project_id,
        display_local_data_root=display_local_data_root,
        purpose=purpose,
    )
    data_directory = Path(str(binding["containerPath"]))

    if bridge_url:
        bridge_root = bridge_relative_local_data_root or Path()
        if bridge_root.is_absolute() or ".." in bridge_root.parts:
            raise ValueError("The host local-data bridge path is invalid.")
        relative_data = data_directory.relative_to(local_data_root.expanduser().resolve())
        _open_via_host_bridge(bridge_url, token_file, bridge_root / relative_data)
        mode = "host-bridge"
    else:
        open_directory(data_directory)
        mode = "native"

    return {**binding, "opened": True, "mode": mode}
