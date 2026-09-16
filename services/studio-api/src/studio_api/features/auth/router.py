"""FedOps authentication translated into a local Studio session."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ...account import create_account_identity
from ...integrations.fedops_web import login_to_fedops
from ...session import (
    attach_session_cookie,
    clear_session_cookie,
    create_local_session,
    delete_local_session,
    get_local_session,
    get_session_id_from_request,
    serialize_session,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(request: Request) -> JSONResponse:
    data = await json_body(request)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username:
        return json_error("Enter a username.")
    if not password:
        return json_error("Enter a password.")

    try:
        fedops_auth = await run_in_threadpool(login_to_fedops, username, password)
        identity = create_account_identity(fedops_auth.get("profile"), username)
        delete_local_session(get_session_id_from_request(request))
        session_id, studio_session = create_local_session(
            "fedops",
            username=str(identity["username"]),
            fedops_auth=fedops_auth,
            identity=identity,
        )
        response = JSONResponse(
            {"status": "success", "session": serialize_session(studio_session)}
        )
        attach_session_cookie(response, session_id)
        return response
    except PermissionError as error:
        return json_error(str(error), status_code=401)
    except Exception as error:
        return json_error(str(error), status_code=502)


@router.get("/session")
async def session(request: Request) -> JSONResponse:
    return JSONResponse(serialize_session(get_local_session(request)))


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    delete_local_session(get_session_id_from_request(request))
    response = JSONResponse({"status": "success"})
    clear_session_cookie(response)
    return response


async def json_body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)
