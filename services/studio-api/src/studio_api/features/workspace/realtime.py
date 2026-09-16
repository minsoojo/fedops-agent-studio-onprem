"""Socket.IO adapter for authenticated, project-scoped interactive PTYs."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from socketio import AsyncServer
from studio_runtime.environments import list_environments
from studio_runtime.terminal import TerminalRuntime
from studio_runtime.workspace import find_project

from ...account import AccountContext, account_context
from ...session import bind_socket_session, get_socket_session, unbind_socket_session

MAX_TERMINALS_PER_PROJECT = 8


@dataclass
class TerminalSession:
    terminal_id: str
    account_key: str
    local_project_id: str
    title: str
    environment_id: str | None
    environment_status: str
    environment_label: str | None
    runtime: TerminalRuntime
    created_at: str

    def serialize(self) -> dict[str, Any]:
        return {
            "terminalId": self.terminal_id,
            "localProjectId": self.local_project_id,
            "title": self.title,
            "shellName": self.runtime.shell_name,
            "environmentId": self.environment_id,
            "environmentStatus": self.environment_status,
            "environmentLabel": self.environment_label,
            "createdAt": self.created_at,
        }


TERMINALS: dict[str, TerminalSession] = {}
PROJECT_TERMINALS: dict[tuple[str, str], list[str]] = {}
SOCKET_CONTEXTS: dict[str, str] = {}
_socket_server: AsyncServer | None = None


def register_terminal_events(socket_server: AsyncServer) -> None:
    global _socket_server
    _socket_server = socket_server

    @socket_server.event
    async def connect(sid: str, environ: dict[str, Any], auth: Any) -> bool:
        del auth
        return bind_socket_session(sid, environ)

    @socket_server.event
    async def disconnect(sid: str) -> None:
        SOCKET_CONTEXTS.pop(sid, None)
        unbind_socket_session(sid)

    @socket_server.on("terminal_list")
    async def terminal_list(sid: str, data: dict[str, Any]) -> dict[str, Any]:
        account = _socket_account(sid)
        if not account:
            return _error("An authenticated session is required.")
        try:
            local_project_id = _project_id(data)
            find_project(account.workspace_root, local_project_id)
            return {
                "status": "ready",
                "items": _project_items(account.account_key, local_project_id),
            }
        except (FileNotFoundError, ValueError) as error:
            return _error(str(error))

    @socket_server.on("terminal_create")
    async def terminal_create(sid: str, data: dict[str, Any]) -> dict[str, Any]:
        account = _socket_account(sid)
        if not account:
            return _error("An authenticated session is required.")
        try:
            local_project_id = _project_id(data)
            existing = PROJECT_TERMINALS.get(
                _project_key(account.account_key, local_project_id),
                [],
            )
            if len(existing) >= MAX_TERMINALS_PER_PROJECT:
                raise ValueError(
                    f"A project can have at most {MAX_TERMINALS_PER_PROJECT} terminals."
                )
            environment_id = str(data.get("environmentId", "")).strip() or None
            session = _create_terminal_session(account, local_project_id, environment_id)
            try:
                await _start_terminal(socket_server, session)
            except Exception as error:
                _remove_terminal_session(session)
                raise RuntimeError(str(error)) from error
            return {"status": "ready", "item": session.serialize()}
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            return _error(str(error))

    @socket_server.on("terminal_context")
    async def terminal_context(sid: str, data: dict[str, Any]) -> dict[str, Any]:
        account = _socket_account(sid)
        if not account:
            return _error("An authenticated session is required.")
        try:
            local_project_id = _project_id(data)
            terminal_id = str(data.get("terminalId", "")).strip()
            if not terminal_id:
                raise ValueError("A terminal ID is required.")
            session = TERMINALS.get(terminal_id)
            if (
                not session
                or session.account_key != account.account_key
                or session.local_project_id != local_project_id
            ):
                raise FileNotFoundError("The selected project terminal was not found.")

            previous_id = SOCKET_CONTEXTS.get(sid)
            if previous_id and previous_id != terminal_id:
                await socket_server.leave_room(sid, _terminal_room(previous_id))
            SOCKET_CONTEXTS[sid] = terminal_id
            await socket_server.enter_room(sid, _terminal_room(terminal_id))
            if session.runtime.recent_output:
                await socket_server.emit(
                    "terminal_output",
                    {
                        "terminalId": terminal_id,
                        "output": session.runtime.recent_output,
                        "replay": True,
                    },
                    to=sid,
                )
            return {"status": "ready", "item": session.serialize()}
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            return _error(str(error))

    @socket_server.on("terminal_restart")
    async def terminal_restart(sid: str, data: dict[str, Any]) -> dict[str, Any]:
        account = _socket_account(sid)
        if not account:
            return _error("An authenticated session is required.")
        try:
            local_project_id = _project_id(data)
            terminal_id = str(data.get("terminalId", "")).strip()
            session = TERMINALS.get(terminal_id)
            if (
                not session
                or session.account_key != account.account_key
                or session.local_project_id != local_project_id
            ):
                raise FileNotFoundError("The selected project terminal was not found.")
            environment_id = str(data.get("environmentId", "")).strip() or None
            replacement = await _restart_terminal_session(
                socket_server,
                account,
                session,
                environment_id,
            )
            return {
                "status": "ready",
                "item": replacement.serialize(),
                "items": _project_items(account.account_key, local_project_id),
            }
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            return _error(str(error))

    @socket_server.on("terminal_close")
    async def terminal_close(sid: str, data: dict[str, Any]) -> dict[str, Any]:
        account = _socket_account(sid)
        if not account:
            return _error("An authenticated session is required.")
        try:
            local_project_id = _project_id(data)
            terminal_id = str(data.get("terminalId", "")).strip()
            session = TERMINALS.get(terminal_id)
            if (
                not session
                or session.account_key != account.account_key
                or session.local_project_id != local_project_id
            ):
                raise FileNotFoundError("The selected project terminal was not found.")
            session.runtime.close()
            _remove_terminal_session(session)
            for connected_sid, active_id in list(SOCKET_CONTEXTS.items()):
                if active_id == terminal_id:
                    await socket_server.leave_room(connected_sid, _terminal_room(terminal_id))
                    SOCKET_CONTEXTS.pop(connected_sid, None)
            return {
                "status": "ready",
                "items": _project_items(account.account_key, local_project_id),
            }
        except (FileNotFoundError, ValueError) as error:
            return _error(str(error))

    @socket_server.on("terminal_input")
    async def terminal_input(sid: str, data: dict[str, Any]) -> None:
        runtime = _runtime_for_socket(sid)
        if runtime:
            runtime.write(str(data.get("input", ""))[-100_000:])

    @socket_server.on("terminal_resize")
    async def terminal_resize(sid: str, data: dict[str, Any]) -> None:
        runtime = _runtime_for_socket(sid)
        if runtime:
            try:
                runtime.resize(
                    rows=int(data.get("rows", 40)),
                    cols=int(data.get("cols", 120)),
                )
            except (TypeError, ValueError):
                return

    @socket_server.on("terminal_clear")
    async def terminal_clear(sid: str) -> None:
        runtime = _runtime_for_socket(sid)
        if runtime:
            runtime.clear_recent_output()


async def ensure_terminal_session() -> None:
    """Verify that the transport is initialized; terminals start via create."""
    if _socket_server is None:
        raise RuntimeError("The terminal transport is not initialized.")


def close_project_terminals(account_key: str, local_project_id: str) -> None:
    """Close all project PTYs before deleting a local Federated Task."""
    project_key = _project_key(account_key, local_project_id)
    terminal_ids = PROJECT_TERMINALS.pop(project_key, [])
    for terminal_id in terminal_ids:
        session = TERMINALS.pop(terminal_id, None)
        if session:
            session.runtime.close()
    for sid, terminal_id in list(SOCKET_CONTEXTS.items()):
        if terminal_id in terminal_ids:
            SOCKET_CONTEXTS.pop(sid, None)


def _create_terminal_session(
    account: AccountContext,
    local_project_id: str,
    environment_id: str | None,
    *,
    title: str | None = None,
) -> TerminalSession:
    _, project_root = find_project(account.workspace_root, local_project_id)
    environments = list_environments(project_root, local_project_id)
    selected = next(
        (
            item
            for item in environments["items"]
            if item["environmentId"] == environment_id
        ),
        None,
    ) if environment_id else next(
        (item for item in environments["items"] if item["selected"]),
        None,
    )
    if environment_id and selected is None:
        raise FileNotFoundError("The selected Python environment was not found.")

    environment_path: Path | None = None
    environment_label: str | None = None
    environment_status = "missing"
    selected_environment_id: str | None = None
    if selected:
        environment_status = str(selected["status"])
        selected_environment_id = str(selected["environmentId"])
        environment_label = str(selected["name"])
        if environment_status == "ready":
            environment_path = project_root / str(selected["path"])

    project_key = _project_key(account.account_key, local_project_id)
    if title is None:
        title = f"Terminal {_next_terminal_number(account.account_key, local_project_id)}"
    terminal_id = f"terminal-{uuid.uuid4().hex[:12]}"
    session = TerminalSession(
        terminal_id=terminal_id,
        account_key=account.account_key,
        local_project_id=local_project_id,
        title=title,
        environment_id=selected_environment_id,
        environment_status=environment_status,
        environment_label=environment_label,
        runtime=TerminalRuntime(
            project_root,
            environment_path,
            environment_label,
            environment_status,
        ),
        created_at=datetime.now(UTC).isoformat(),
    )
    TERMINALS[terminal_id] = session
    PROJECT_TERMINALS.setdefault(project_key, []).append(terminal_id)
    return session


async def _start_terminal(
    socket_server: AsyncServer,
    session: TerminalSession,
) -> None:
    room = _terminal_room(session.terminal_id)

    async def emit_output(output: str) -> None:
        await socket_server.emit(
            "terminal_output",
            {
                "terminalId": session.terminal_id,
                "output": output,
                "replay": False,
            },
            room=room,
        )

    await session.runtime.ensure_started(
        socket_server.start_background_task,
        socket_server.sleep,
        emit_output,
    )


async def _restart_terminal_session(
    socket_server: AsyncServer,
    account: AccountContext,
    session: TerminalSession,
    environment_id: str | None,
) -> TerminalSession:
    project_key = _project_key(account.account_key, session.local_project_id)
    terminal_ids = PROJECT_TERMINALS.get(project_key, [])
    old_index = terminal_ids.index(session.terminal_id)
    replacement = _create_terminal_session(
        account,
        session.local_project_id,
        environment_id,
        title=session.title,
    )
    try:
        await _start_terminal(socket_server, replacement)
    except Exception as error:
        _remove_terminal_session(replacement)
        raise RuntimeError(str(error)) from error

    affected_sockets = [
        connected_sid
        for connected_sid, active_id in SOCKET_CONTEXTS.items()
        if active_id == session.terminal_id
    ]
    for connected_sid in affected_sockets:
        await socket_server.leave_room(
            connected_sid,
            _terminal_room(session.terminal_id),
        )
        SOCKET_CONTEXTS.pop(connected_sid, None)

    session.runtime.close()
    _remove_terminal_session(session)
    replacement_ids = PROJECT_TERMINALS[project_key]
    replacement_ids.remove(replacement.terminal_id)
    replacement_ids.insert(min(old_index, len(replacement_ids)), replacement.terminal_id)
    return replacement


def _runtime_for_socket(sid: str) -> TerminalRuntime | None:
    account = _socket_account(sid)
    if not account:
        return None
    terminal_id = SOCKET_CONTEXTS.get(sid)
    session = TERMINALS.get(terminal_id) if terminal_id else None
    return session.runtime if session and session.account_key == account.account_key else None


def _project_items(account_key: str, local_project_id: str) -> list[dict[str, Any]]:
    return [
        TERMINALS[terminal_id].serialize()
        for terminal_id in PROJECT_TERMINALS.get(
            _project_key(account_key, local_project_id),
            [],
        )
        if terminal_id in TERMINALS
        and TERMINALS[terminal_id].account_key == account_key
    ]


def _next_terminal_number(account_key: str, local_project_id: str) -> int:
    """Return one greater than the highest active terminal number."""
    used_numbers: set[int] = set()
    for terminal_id in PROJECT_TERMINALS.get(
        _project_key(account_key, local_project_id),
        [],
    ):
        session = TERMINALS.get(terminal_id)
        if not session or session.account_key != account_key:
            continue
        prefix = "Terminal "
        suffix = session.title.removeprefix(prefix)
        if session.title.startswith(prefix) and suffix.isdigit():
            number = int(suffix)
            if number > 0:
                used_numbers.add(number)

    return max(used_numbers, default=0) + 1


def _remove_terminal_session(session: TerminalSession) -> None:
    TERMINALS.pop(session.terminal_id, None)
    project_key = _project_key(session.account_key, session.local_project_id)
    ids = PROJECT_TERMINALS.get(project_key, [])
    remaining = [
        item for item in ids if item != session.terminal_id
    ]
    if remaining:
        PROJECT_TERMINALS[project_key] = remaining
    else:
        PROJECT_TERMINALS.pop(project_key, None)


def _socket_account(sid: str) -> AccountContext | None:
    session = get_socket_session(sid)
    if not session:
        return None
    try:
        return account_context(session)
    except PermissionError:
        return None


def _project_key(account_key: str, local_project_id: str) -> tuple[str, str]:
    return account_key, local_project_id


def _project_id(data: dict[str, Any]) -> str:
    local_project_id = str(data.get("localProjectId", "")).strip()
    if not local_project_id:
        raise ValueError("A local project ID is required for the terminal.")
    return local_project_id


def _terminal_room(terminal_id: str) -> str:
    return f"terminal:{terminal_id}"


def _error(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}
