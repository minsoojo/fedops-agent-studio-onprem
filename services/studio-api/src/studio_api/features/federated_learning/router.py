"""Authenticated transport for local Federated Learning participations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from studio_runtime.federated_learning import (
    PARTICIPATION_MANAGER,
    participation_preflight,
)
from studio_runtime.federated_task import (
    read_task_binding,
    source_fingerprint,
    write_task_binding,
)
from studio_runtime.workspace import discover_projects, find_project

from ...account import AccountContext, account_context
from ...session import get_local_session, require_fedops_session
from ...integrations.fedops_web import fedops_request


router = APIRouter(prefix="/api/v1/federated-learning", tags=["federated-learning"])
PARTICIPATION_MANIFEST_TIMEOUT_SECONDS = 4.0
DETAIL_MANIFEST_REFRESH_SECONDS = 20.0
TERMINAL_MANIFEST_REFRESH_SECONDS = 4.0
LIST_MANIFEST_REFRESH_SECONDS = 20.0
_detail_refresh_lock = asyncio.Lock()
_detail_refresh_in_flight: set[str] = set()
_detail_refresh_started_at: dict[str, float] = {}
_participation_event_lock = asyncio.Lock()
_reported_participation_events: set[str] = set()
_list_refresh_lock = asyncio.Lock()
_list_refresh_in_flight: set[str] = set()
_list_refresh_started_at: dict[str, float] = {}


class ParticipationActionRequest(BaseModel):
    dataPath: str | None = None
    environmentId: str | None = None


def participation_event_payload(
    snapshot: dict[str, Any],
    event: str,
) -> tuple[str, dict[str, Any]] | None:
    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    release = snapshot.get("release") if isinstance(snapshot.get("release"), dict) else {}
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    task_id = task.get("taskId")
    run_id = runtime.get("runId")
    if not task_id or not run_id or event not in {"started", "completed"}:
        return None
    return str(task_id), {
        "event": event,
        "runId": str(run_id),
        "releaseId": release.get("releaseId"),
    }


async def report_participation_event(
    session: dict[str, Any],
    snapshot: dict[str, Any],
    event: str,
) -> None:
    selected = participation_event_payload(snapshot, event)
    if not selected:
        return
    task_id, payload = selected
    event_key = f"{task_id}:{payload['runId']}:{event}"
    async with _participation_event_lock:
        if event_key in _reported_participation_events:
            return
    try:
        await run_in_threadpool(lambda: fedops_request(
            session,
            f"tasks/id/{task_id}/participation-events",
            method="POST",
            payload=payload,
        ))
    except (PermissionError, RuntimeError):
        return
    async with _participation_event_lock:
        _reported_participation_events.add(event_key)
        if len(_reported_participation_events) > 1000:
            _reported_participation_events.pop()


async def track_participation_run(
    session: dict[str, Any],
    account: AccountContext,
    project_root: Path,
    local_project_id: str,
    manifest: dict[str, Any],
    initial_snapshot: dict[str, Any],
) -> None:
    await report_participation_event(session, initial_snapshot, "started")
    for iteration in range(60 * 60 * 24):
        try:
            if iteration % 15 == 0:
                try:
                    updated = await manifest_for(session, str(manifest["task"]["taskId"]))
                    if (updated.get("campaignRun") or {}).get("runId") == (manifest.get("campaignRun") or {}).get("runId"):
                        manifest = updated
                except HTTPException:
                    pass  # Offline must never be interpreted as successful completion.
            snapshot = await run_in_threadpool(
                PARTICIPATION_MANAGER.snapshot,
                account.account_key,
                project_root,
                local_project_id,
                manifest,
            )
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            return
        runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
        status = runtime.get("status")
        if status == "failed":
            try:
                updated = await manifest_for(session, str(manifest["task"]["taskId"]))
                if (updated.get("campaignRun") or {}).get("runId") == runtime.get("campaignRunId"):
                    snapshot = await run_in_threadpool(
                        PARTICIPATION_MANAGER.snapshot, account.account_key,
                        project_root, local_project_id, updated,
                    )
                    status = snapshot.get("runtime", {}).get("status")
            except HTTPException:
                pass
        if status == "completed":
            await report_participation_event(session, snapshot, "completed")
            return
        if status in {"failed", "stopped", "disconnected"}:
            return
        await asyncio.sleep(1.0)


def require_account(request: Request) -> tuple[AccountContext, dict[str, Any]]:
    try:
        session = require_fedops_session(request)
        return account_context(session), session
    except PermissionError as error:
        raise HTTPException(
            status_code=403 if get_local_session(request) else 401,
            detail=str(error),
        ) from error


def project_for(account: AccountContext, local_project_id: str) -> tuple[dict[str, Any], Path]:
    try:
        return find_project(account.workspace_root, local_project_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


async def manifest_for(session: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        value = await run_in_threadpool(
            lambda: fedops_request(
                session,
                f"task-releases/tasks/{task_id}/participation-manifest",
            )
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="FedOps returned an invalid participation manifest.")
    return value


def refresh_binding(project_root: Path, manifest: dict[str, Any]) -> None:
    task = manifest.get("task") if isinstance(manifest.get("task"), dict) else {}
    release = manifest.get("release") if isinstance(manifest.get("release"), dict) else {}
    model = manifest.get("globalModel") if isinstance(manifest.get("globalModel"), dict) else {}
    participation = (
        manifest.get("participation")
        if isinstance(manifest.get("participation"), dict)
        else {}
    )
    current = read_task_binding(project_root) or {}
    remote_release_id = release.get("releaseId")
    source_matches_remote = bool(
        release.get("sourceFingerprint")
        and release.get("sourceFingerprint") == source_fingerprint(project_root)
    )
    adopt_remote_release = bool(
        current.get("releaseId") == remote_release_id
        or source_matches_remote
    )
    write_task_binding(project_root, {
        **current,
        **task,
        "workspaceRole": participation.get("role") or current.get("workspaceRole"),
        "registryStatus": "published",
        "releaseId": (
            remote_release_id if adopt_remote_release else current.get("releaseId")
        ),
        "bundleSha256": (
            release.get("bundleSha256")
            if adopt_remote_release
            else current.get("bundleSha256")
        ),
        "modelVersionId": (
            model.get("modelVersionId")
            if adopt_remote_release
            else current.get("modelVersionId")
        ),
    })
    cache = project_root / ".fedops-studio" / "participation" / "manifest.json"
    cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = cache.with_name(f".{cache.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)


def cached_manifest(project_root: Path, binding: dict[str, Any]) -> dict[str, Any] | None:
    """Read the last authorized manifest without waiting for FedOps Web."""
    path = project_root / ".fedops-studio" / "participation" / "manifest.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    task = value.get("task") if isinstance(value.get("task"), dict) else {}
    if str(task.get("taskId") or "") != str(binding.get("taskId") or ""):
        return None
    return value


def _read_local_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


async def refresh_participation_detail(
    session: dict[str, Any],
    project_root: Path,
    task_id: str,
) -> None:
    """Refresh remote state at most once per interval while local polling stays fast."""
    key = str(project_root.resolve())
    now = asyncio.get_running_loop().time()
    async with _detail_refresh_lock:
        last_started = _detail_refresh_started_at.get(key, 0.0)
        if key in _detail_refresh_in_flight or now - last_started < DETAIL_MANIFEST_REFRESH_SECONDS:
            return
        _detail_refresh_in_flight.add(key)
        _detail_refresh_started_at[key] = now
    try:
        manifest = await manifest_for(session, task_id)
        await run_in_threadpool(refresh_binding, project_root, manifest)
    except Exception:
        return
    finally:
        async with _detail_refresh_lock:
            _detail_refresh_in_flight.discard(key)


def selected_data_path(project_root: Path, requested: str | None) -> str:
    if requested:
        return requested
    preflight = project_root / ".fedops-studio" / "participation" / "preflight.json"
    if preflight.is_file() and not preflight.is_symlink():
        try:
            value = json.loads(preflight.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        if isinstance(value, dict) and isinstance(value.get("dataPath"), str):
            return value["dataPath"]
    readiness = project_root / ".fedops-studio" / "readiness-participation.json"
    if readiness.is_file() and not readiness.is_symlink():
        try:
            value = json.loads(readiness.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        if isinstance(value, dict) and isinstance(value.get("dataPath"), str):
            return value["dataPath"]
    raise ValueError("Select a local-only data directory before starting this Client.")


def hide_from_participation_home(error: HTTPException) -> bool:
    """Hide Tasks that are not yet valid authorized participation targets."""
    return error.status_code == 403 or (
        "A Published Task Release is required for participation" in str(error.detail)
    )


def unavailable_participation(
    project: dict[str, Any],
    binding: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "localProjectId": project["localProjectId"],
        "task": {
            "taskId": binding.get("taskId"),
            "runtimeKey": binding.get("runtimeKey"),
            "title": binding.get("displayName") or project["name"],
            "primaryModel": binding.get("primaryModel"),
            "runtimeContract": binding.get("runtimeContract"),
        },
        "workspace": {"ready": False, "binding": binding},
        "runtime": {"status": "needs_attention", "clientState": "not_configured"},
        "unavailableReason": reason,
    }


def local_participation_item(
    account: AccountContext,
    project: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a card from account-local state without waiting for FedOps Web."""
    binding = project.get("taskBinding")
    if (
        not isinstance(binding, dict)
        or not binding.get("taskId")
        or binding.get("registryStatus") != "published"
        or not binding.get("releaseId")
    ):
        return None
    _, project_root = find_project(
        account.workspace_root,
        str(project["localProjectId"]),
    )
    cache = project_root / ".fedops-studio" / "participation" / "manifest.json"
    if cache.is_file() and not cache.is_symlink():
        try:
            manifest = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                return PARTICIPATION_MANAGER.snapshot(
                    account.account_key,
                    project_root,
                    str(project["localProjectId"]),
                    manifest,
                )
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    state_path = project_root / ".fedops-studio" / "participation" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict) or not state:
        state = {"status": "loading", "clientState": "loading"}
    return {
        "localProjectId": project["localProjectId"],
        "task": {
            "taskId": binding.get("taskId"),
            "runtimeKey": binding.get("runtimeKey"),
            "title": binding.get("displayName") or project["name"],
            "primaryModel": binding.get("primaryModel"),
            "runtimeContract": binding.get("runtimeContract"),
        },
        "release": {
            "releaseId": binding.get("releaseId"),
            "bundleSha256": binding.get("bundleSha256"),
        },
        "globalModel": {"modelVersionId": binding.get("modelVersionId")},
        "server": {"state": "Refreshing", "ready": False},
        "workspace": {"ready": False, "binding": binding},
        "runtime": state,
    }


async def refresh_participation_cache(
    account: AccountContext,
    session: dict[str, Any],
    projects: list[dict[str, Any]],
) -> None:
    await asyncio.gather(*(
        participation_item(account, session, project)
        for project in projects
    ), return_exceptions=True)


async def refresh_participation_cache_throttled(
    account: AccountContext,
    session: dict[str, Any],
    projects: list[dict[str, Any]],
) -> None:
    """Keep list polling local while refreshing FedOps Web on a slower cadence."""
    key = account.account_key
    now = asyncio.get_running_loop().time()
    async with _list_refresh_lock:
        last_started = _list_refresh_started_at.get(key, 0.0)
        if (
            key in _list_refresh_in_flight
            or now - last_started < LIST_MANIFEST_REFRESH_SECONDS
        ):
            return
        _list_refresh_in_flight.add(key)
        _list_refresh_started_at[key] = now
    try:
        await refresh_participation_cache(account, session, projects)
    finally:
        async with _list_refresh_lock:
            _list_refresh_in_flight.discard(key)


async def participation_item(
    account: AccountContext,
    session: dict[str, Any],
    project: dict[str, Any],
) -> dict[str, Any] | None:
    binding = project.get("taskBinding")
    if not isinstance(binding, dict) or not binding.get("taskId"):
        return None
    try:
        manifest = await asyncio.wait_for(
            manifest_for(session, str(binding["taskId"])),
            timeout=PARTICIPATION_MANIFEST_TIMEOUT_SECONDS,
        )
        _, project_root = find_project(account.workspace_root, str(project["localProjectId"]))
        await run_in_threadpool(refresh_binding, project_root, manifest)
        return await run_in_threadpool(
            PARTICIPATION_MANAGER.snapshot,
            account.account_key,
            project_root,
            str(project["localProjectId"]),
            manifest,
        )
    except TimeoutError:
        return unavailable_participation(
            project,
            binding,
            "FedOps Web took too long to return the authorized Client state. Refresh to try again.",
        )
    except HTTPException as error:
        if error.status_code not in {403, 409, 502}:
            raise
        if hide_from_participation_home(error):
            return None
        return unavailable_participation(project, binding, str(error.detail))


@router.get("/participations")
async def list_participations(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    account, session = require_account(request)
    projects = discover_projects(account.workspace_root)
    items = [
        item
        for project in projects
        if (item := local_participation_item(account, project)) is not None
    ]
    background_tasks.add_task(
        refresh_participation_cache_throttled,
        account,
        session,
        projects,
    )
    return {"items": items, "source": "account-local-cache-refreshing"}


async def context(
    request: Request,
    local_project_id: str,
) -> tuple[AccountContext, Path, dict[str, Any]]:
    account, session = require_account(request)
    _, project_root = project_for(account, local_project_id)
    binding = read_task_binding(project_root)
    if not binding or not binding.get("taskId"):
        raise HTTPException(status_code=409, detail="Link this Workspace to FedOps Web first.")
    manifest = await manifest_for(session, str(binding["taskId"]))
    try:
        await run_in_threadpool(refresh_binding, project_root, manifest)
    except (FileExistsError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return account, project_root, manifest


@router.get("/participations/{local_project_id}")
async def read_participation(
    local_project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    account, session = require_account(request)
    _, project_root = project_for(account, local_project_id)
    binding = read_task_binding(project_root)
    if not binding or not binding.get("taskId"):
        raise HTTPException(status_code=409, detail="Link this Workspace to FedOps Web first.")
    manifest = cached_manifest(project_root, binding)
    if manifest is None:
        manifest = await manifest_for(session, str(binding["taskId"]))
        await run_in_threadpool(refresh_binding, project_root, manifest)
    else:
        state = _read_local_json(project_root / ".fedops-studio" / "participation" / "state.json")
        runtime_terminal = state.get("status") in {"completed", "failed", "stopped", "disconnected"}
        cache_path = project_root / ".fedops-studio" / "participation" / "manifest.json"
        cache_age = time.time() - cache_path.stat().st_mtime if cache_path.is_file() else float("inf")
        if runtime_terminal and cache_age >= TERMINAL_MANIFEST_REFRESH_SECONDS:
            manifest = await manifest_for(session, str(binding["taskId"]))
            await run_in_threadpool(refresh_binding, project_root, manifest)
        else:
            background_tasks.add_task(
                refresh_participation_detail,
                session,
                project_root,
                str(binding["taskId"]),
            )
    snapshot = await run_in_threadpool(
        PARTICIPATION_MANAGER.snapshot,
        account.account_key,
        project_root,
        local_project_id,
        manifest,
    )
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    if runtime.get("status") == "completed":
        background_tasks.add_task(
            report_participation_event,
            session,
            snapshot,
            "completed",
        )
    return snapshot


@router.get("/participations/{local_project_id}/history")
async def list_participation_history(
    local_project_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return device-local Campaign history for this authorized Workspace."""
    account, _ = require_account(request)
    _, project_root = project_for(account, local_project_id)
    binding = read_task_binding(project_root)
    if not binding or not binding.get("taskId"):
        raise HTTPException(status_code=409, detail="Link this Workspace to FedOps Web first.")
    items = await run_in_threadpool(
        PARTICIPATION_MANAGER.history_sessions,
        project_root,
        cached_manifest(project_root, binding) or {},
    )
    return {"items": items, "source": "account-local-participation-history"}


@router.get("/participations/{local_project_id}/history/{session_id}")
async def read_participation_history(
    local_project_id: str,
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the round events for one device-local Campaign participation."""
    account, _ = require_account(request)
    _, project_root = project_for(account, local_project_id)
    binding = read_task_binding(project_root)
    if not binding or not binding.get("taskId"):
        raise HTTPException(status_code=409, detail="Link this Workspace to FedOps Web first.")
    manifest = cached_manifest(project_root, binding) or {}
    try:
        return await run_in_threadpool(
            PARTICIPATION_MANAGER.history_session,
            project_root,
            manifest,
            session_id,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/participations/{local_project_id}/preflight")
async def run_preflight(
    local_project_id: str,
    payload: ParticipationActionRequest,
    request: Request,
) -> dict[str, Any]:
    account, project_root, manifest = await context(request, local_project_id)
    try:
        data_path = selected_data_path(project_root, payload.dataPath)
        return await run_in_threadpool(
            participation_preflight,
            project_root,
            local_project_id,
            manifest,
            data_path,
            account.workspace_root.parent / ".local-data",
            payload.environmentId,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/participations/{local_project_id}/start", status_code=202)
async def start_participation(
    local_project_id: str,
    payload: ParticipationActionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    account, session = require_account(request)
    _, project_root = project_for(account, local_project_id)
    binding = read_task_binding(project_root)
    if not binding or not binding.get("taskId"):
        raise HTTPException(status_code=409, detail="Link this Workspace to FedOps Web first.")
    manifest = await manifest_for(session, str(binding["taskId"]))
    try:
        await run_in_threadpool(refresh_binding, project_root, manifest)
        snapshot = await run_in_threadpool(
            PARTICIPATION_MANAGER.start,
            account_key=account.account_key,
            project_root=project_root,
            local_project_id=local_project_id,
            manifest=manifest,
            data_path=selected_data_path(project_root, payload.dataPath),
            allowed_data_root=account.workspace_root.parent / ".local-data",
            environment_id=payload.environmentId,
        )
        background_tasks.add_task(
            track_participation_run,
            session,
            account,
            project_root,
            local_project_id,
            manifest,
            snapshot,
        )
        return snapshot
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/participations/{local_project_id}/stop", status_code=202)
async def stop_participation(local_project_id: str, request: Request) -> dict[str, Any]:
    account, project_root, manifest = await context(request, local_project_id)
    try:
        return await run_in_threadpool(
            PARTICIPATION_MANAGER.stop,
            account.account_key,
            project_root,
            local_project_id,
            manifest,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
