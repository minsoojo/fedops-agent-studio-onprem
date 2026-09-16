"""Process-local Studio session store and browser/socket authentication helpers."""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import APP_SESSION_COOKIE

AUTH_SESSIONS: dict[str, dict[str, Any]] = {}
SOCKET_SESSIONS: dict[str, str] = {}


def serialize_session(session: dict[str, Any] | None) -> dict[str, Any]:
    if not session:
        return {
            "authenticated": False,
            "mode": "anonymous",
            "username": None,
            "accountKey": None,
            "handle": None,
            "organization": None,
            "displayName": None,
            "isGuest": False,
            "isFedOps": False,
        }

    mode = session.get("mode", "anonymous")
    return {
        "authenticated": True,
        "mode": mode,
        "username": session.get("username"),
        "accountKey": session.get("account_key"),
        "handle": session.get("handle"),
        "organization": session.get("organization"),
        "displayName": session.get("display_name"),
        "isGuest": mode == "guest",
        "isFedOps": mode == "fedops",
    }


def get_session_id_from_request(request: Request) -> str | None:
    return request.cookies.get(APP_SESSION_COOKIE)


def get_local_session(request: Request) -> dict[str, Any] | None:
    session_id = get_session_id_from_request(request)
    return AUTH_SESSIONS.get(session_id) if session_id else None


def create_local_session(
    mode: str,
    username: str | None = None,
    fedops_auth: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    session_id = secrets.token_hex(32)
    session = {
        "mode": mode,
        "username": username,
        "fedops_auth": fedops_auth,
        "user_id": (identity or {}).get("userId"),
        "account_key": (identity or {}).get("accountKey"),
        "handle": (identity or {}).get("handle"),
        "organization": (identity or {}).get("organization"),
        "display_name": (identity or {}).get("displayName"),
    }
    AUTH_SESSIONS[session_id] = session
    return session_id, session


def delete_local_session(session_id: str | None) -> None:
    if not session_id:
        return
    AUTH_SESSIONS.pop(session_id, None)
    stale_socket_ids = [
        sid
        for sid, stored_session_id in SOCKET_SESSIONS.items()
        if stored_session_id == session_id
    ]
    for sid in stale_socket_ids:
        SOCKET_SESSIONS.pop(sid, None)


def attach_session_cookie(
    response: JSONResponse,
    session_id: str,
) -> None:
    response.set_cookie(
        APP_SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )


def clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(
        APP_SESSION_COOKIE,
        path="/",
        secure=False,
        httponly=True,
        samesite="strict",
    )


def require_local_session(request: Request) -> dict[str, Any]:
    session = get_local_session(request)
    if not session:
        raise PermissionError("Login is required.")
    return session


def require_fedops_session(request: Request) -> dict[str, Any]:
    session = require_local_session(request)
    if session.get("mode") != "fedops":
        raise PermissionError("FedOps login is required.")
    return session


def get_session_id_from_socket_environ(environ: dict[str, Any]) -> str | None:
    raw_cookie = environ.get("HTTP_COOKIE")
    if not raw_cookie:
        scope = environ.get("asgi.scope") or {}
        for key, value in scope.get("headers") or []:
            if key == b"cookie":
                raw_cookie = value.decode("latin-1")
                break
    if not raw_cookie:
        return None

    cookies = SimpleCookie()
    cookies.load(raw_cookie)
    morsel = cookies.get(APP_SESSION_COOKIE)
    return morsel.value if morsel else None


def bind_socket_session(sid: str, environ: dict[str, Any]) -> bool:
    session_id = get_session_id_from_socket_environ(environ)
    session = AUTH_SESSIONS.get(session_id) if session_id else None
    if not session or session.get("mode") != "fedops" or not session.get("account_key"):
        return False
    SOCKET_SESSIONS[sid] = session_id
    return True


def unbind_socket_session(sid: str) -> None:
    SOCKET_SESSIONS.pop(sid, None)


def get_socket_session(sid: str) -> dict[str, Any] | None:
    session_id = SOCKET_SESSIONS.get(sid)
    return AUTH_SESSIONS.get(session_id) if session_id else None
