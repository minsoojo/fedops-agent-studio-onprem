"""Token-protected host bridge for local OS operations needed by Docker Studio."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .folder import open_directory
from .hardware import collect_hardware_information


class FolderBridgeHandler(BaseHTTPRequestHandler):
    server: FolderBridgeServer

    def do_GET(self) -> None:
        if self.path not in {"/health", "/hardware"}:
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path == "/hardware":
            if self.server.hardware is None:
                self._json(503, {"error": "hardware information is still loading"})
                return
            self._json(200, self.server.hardware)
            return
        self._json(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/open":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            relative = Path(str(payload.get("relativePath", "")))
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise ValueError("invalid relative path")
            target = (self.server.workspace / relative).resolve()
            target.relative_to(self.server.workspace)
            workspace_project = (
                len(relative.parts) == 4
                and relative.parts[0] == "accounts"
                and relative.parts[1].startswith("account-")
                and relative.parts[2] == "projects"
            )
            task_data = (
                len(relative.parts) == 6
                and relative.parts[0] == "accounts"
                and relative.parts[1].startswith("account-")
                and relative.parts[2] == ".local-data"
                and relative.parts[3] == "federated-tasks"
                and not relative.parts[4].startswith(".")
                and relative.parts[5] in {"dataset", "validation"}
            )
            if not workspace_project and not task_data:
                raise ValueError(
                    "only account-scoped Workspace projects and Task data directories can be opened"
                )
            if not target.is_dir():
                raise FileNotFoundError("Workspace project was not found")
            open_directory(target)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, {"opened": True})

    def _authorized(self) -> bool:
        provided = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return bool(provided) and hmac.compare_digest(provided, self.server.token)

    def _json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[folder-opener] {self.address_string()} {format % args}", flush=True)


class FolderBridgeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], workspace: Path, token: str):
        super().__init__(address, FolderBridgeHandler)
        self.workspace = workspace.resolve()
        self.token = token
        self.hardware = None
        threading.Thread(target=self._load_hardware, daemon=True).start()

    def _load_hardware(self) -> None:
        try:
            self.hardware = collect_hardware_information("host")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            print("[folder-opener] hardware information is unavailable", flush=True)


def ensure_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
        path.chmod(0o600)
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Folder opener token is empty.")
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FedOps Agent Studio host folder opener")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5602)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")
    ensure_token(args.token_file.expanduser())
    if args.stop:
        if args.pid_file is None or not args.pid_file.is_file():
            print("folder opener is not running")
            return 0
        pid = int(args.pid_file.read_text(encoding="utf-8").strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        args.pid_file.unlink(missing_ok=True)
        print(f"folder opener stopped (pid={pid})")
        return 0
    if args.daemon:
        if args.pid_file is None or args.log_file is None:
            raise ValueError("--daemon requires --pid-file and --log-file")
        if args.pid_file.is_file() and sys.platform != "win32":
            pid = int(args.pid_file.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                args.pid_file.unlink(missing_ok=True)
            else:
                print(f"folder opener is already running (pid={pid})")
                return 0
        command = [
            sys.executable,
            "-m",
            "studio_runtime.folder_bridge",
            "--workspace",
            str(workspace),
            "--token-file",
            str(args.token_file.expanduser()),
            "--host",
            args.host,
            "--port",
            str(args.port),
        ]
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        with args.log_file.open("ab") as log:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=sys.platform != "win32",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                    if sys.platform == "win32" else 0
                ),
            )
        args.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
        time.sleep(0.2)
        if process.poll() is not None:
            raise RuntimeError(f"Folder opener failed to start; inspect {args.log_file}")
        print(f"folder opener started (pid={process.pid})")
        return 0

    token = ensure_token(args.token_file.expanduser())
    server = FolderBridgeServer((args.host, args.port), workspace, token)
    print(
        f"[folder-opener] serving {workspace} on {args.host}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
