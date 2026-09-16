"""Owner-oriented Python environment endpoints backed by studio-runtime."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from studio_runtime.environments import (
    create_environment,
    delete_environment,
    list_environments,
    read_task_requirements,
    select_environment,
    sync_environment_run,
    write_task_requirements,
)
from studio_runtime.workspace import find_project

from ...account import AccountContext, account_context
from ...session import get_local_session, require_fedops_session
from .schemas import (
    CreateEnvironmentRequest,
    EnvironmentOwnerRequest,
    EnvironmentOwnerType,
    UpdateRequirementsRequest,
)

router = APIRouter(prefix="/api/v1/environments", tags=["environments"])


def require_account(request: Request) -> AccountContext:
    try:
        return account_context(require_fedops_session(request))
    except PermissionError as error:
        raise HTTPException(
            status_code=403 if get_local_session(request) else 401,
            detail=str(error),
        ) from error


def owner_project(account: AccountContext, owner_type: str, owner_id: str) -> Path:
    if owner_type != "federated-task":
        raise HTTPException(
            status_code=409,
            detail=f"{owner_type} environments are reserved for a future feature.",
        )
    try:
        _, project_root = find_project(account.workspace_root, owner_id)
        return project_root
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("")
async def read_environments(
    request: Request,
    owner_type: Annotated[EnvironmentOwnerType, Query()],
    owner_id: Annotated[str, Query(min_length=1)],
) -> dict[str, object]:
    account = require_account(request)
    try:
        return list_environments(
            owner_project(account, owner_type, owner_id),
            owner_id,
            owner_type,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("", status_code=201)
async def add_environment(
    payload: CreateEnvironmentRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    try:
        return create_environment(
            owner_project(account, payload.ownerType, payload.ownerId),
            payload.ownerId,
            owner_type=payload.ownerType,
            name=payload.name,
            python_version=payload.pythonVersion,
            select=payload.select,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/requirements")
async def read_requirements(
    request: Request,
    owner_type: Annotated[EnvironmentOwnerType, Query()],
    owner_id: Annotated[str, Query(min_length=1)],
) -> dict[str, object]:
    account = require_account(request)
    try:
        return read_task_requirements(owner_project(account, owner_type, owner_id))
    except (OSError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/requirements")
async def update_requirements(
    payload: UpdateRequirementsRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    try:
        return write_task_requirements(
            owner_project(account, payload.ownerType, payload.ownerId),
            payload.content,
        )
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{environment_id}/select")
async def choose_environment(
    environment_id: str,
    payload: EnvironmentOwnerRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    try:
        return select_environment(
            owner_project(account, payload.ownerType, payload.ownerId),
            payload.ownerId,
            environment_id,
            payload.ownerType,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{environment_id}/sync", status_code=202)
async def sync_environment(
    environment_id: str,
    payload: EnvironmentOwnerRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    try:
        return sync_environment_run(
            owner_project(account, payload.ownerType, payload.ownerId),
            payload.ownerId,
            environment_id,
            payload.ownerType,
            account.account_key,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/{environment_id}")
async def remove_environment(
    environment_id: str,
    payload: EnvironmentOwnerRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    try:
        return delete_environment(
            owner_project(account, payload.ownerType, payload.ownerId),
            payload.ownerId,
            environment_id,
            payload.ownerType,
            account.account_key,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
