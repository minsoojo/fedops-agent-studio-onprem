"""Small stable bootstrap contract used by the React application shell."""

from pathlib import Path

from fastapi import APIRouter, Request
from studio_runtime.hardware import (
    collect_hardware_information,
    read_host_hardware_information,
)
from studio_runtime.legacy_workspace import discover_legacy_projects
from studio_runtime.workspace import discover_projects

from ... import __version__
from ...account import account_context
from ...config import (
    FEDOPS_BASE_URL,
    FOLDER_OPENER_TOKEN_FILE,
    FOLDER_OPENER_URL,
    WORKSPACE_DIR,
    WORKSPACE_DISPLAY_DIR,
)
from ...session import get_local_session, serialize_session

router = APIRouter(prefix="/api/v1", tags=["system"])


def hardware_information() -> dict[str, object]:
    if FOLDER_OPENER_URL and FOLDER_OPENER_TOKEN_FILE:
        try:
            return read_host_hardware_information(
                FOLDER_OPENER_URL,
                Path(FOLDER_OPENER_TOKEN_FILE),
            )
        except (FileNotFoundError, RuntimeError, ValueError):
            pass
    return collect_hardware_information("runtime")


def capabilities() -> dict[str, bool]:
    return {
        "workspaceFiles": True,
        "createFederatedTask": True,
        "projectInstall": True,
        "pythonEnvironments": True,
        "federatedTaskValidation": True,
        "fedopsLogin": True,
        "accountTaskList": True,
        "publicTaskRegistry": True,
        "taskHub": True,
        "taskActivity": True,
        "participationRequest": True,
        "baselineImport": True,
        "localValidation": True,
        "stableTaskBinding": True,
        "globalModelRegistry": True,
        "federatedParticipation": True,
        "agentBuilder": False,
        "agentRuntime": False,
    }


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "fedops-agent-studio-api",
        "version": __version__,
    }


@router.get("/bootstrap")
async def bootstrap(request: Request) -> dict[str, object]:
    session = get_local_session(request)
    account = (
        account_context(session)
        if session and session.get("mode") == "fedops"
        else None
    )
    projects = discover_projects(account.workspace_root) if account else []
    legacy_projects = (
        discover_legacy_projects(
            Path(WORKSPACE_DIR),
            account.workspace_root,
            display_base=Path(WORKSPACE_DISPLAY_DIR),
        )
        if account
        else []
    )
    return {
        "version": __version__,
        "connections": {
            "fedopsWeb": FEDOPS_BASE_URL,
            "fedopsRegister": f"{FEDOPS_BASE_URL}/register",
        },
        "session": serialize_session(session),
        "workspace": {
            "root": str(account.workspace_root) if account else str(WORKSPACE_DIR),
            "displayRoot": (
                str(account.display_workspace_root)
                if account
                else str(WORKSPACE_DISPLAY_DIR)
            ),
            "projects": projects,
            "legacyProjects": legacy_projects,
            "projectDiscovery": "first-level directories in the signed-in account Workspace",
        },
        "hardware": hardware_information(),
        "capabilities": capabilities(),
    }
