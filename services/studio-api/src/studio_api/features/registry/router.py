from collections.abc import Callable
import tempfile
from pathlib import Path
from typing import Any
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from studio_runtime.federated_task import (
    install_published_release,
    reconcile_existing_published_release,
)
from studio_runtime.jobs import RUN_MANAGER
from studio_runtime.workspace import find_project, find_project_for_task

from ...integrations.fedops_web import (
    download_authenticated_fedops_artifact,
    fedops_request,
    quote_segment,
)
from ...account import account_context
from ...session import get_local_session, require_fedops_session
from .task_mapper import (
    normalize_account_tasks,
    normalize_participation,
    normalize_registry_task,
    normalize_registry_tasks,
)

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


class OpenPublishedReleaseRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)


def session_for(request: Request) -> dict[str, Any]:
    try:
        return require_fedops_session(request)
    except PermissionError as error:
        raise HTTPException(
            status_code=403 if get_local_session(request) else 401,
            detail=str(error),
        ) from error


async def remote(action: Callable[[], Any]) -> Any:
    try:
        return await run_in_threadpool(action)
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


async def artifact_download_response(
    session: dict[str, Any],
    descriptor: Any,
    *,
    fallback_name: str,
) -> FileResponse | RedirectResponse:
    """Deliver Web-owned artifacts without exposing the Studio's FedOps session."""
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("url"), str):
        raise HTTPException(status_code=502, detail="FedOps returned an invalid download descriptor.")
    url = descriptor["url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return RedirectResponse(url, status_code=307)
    api_prefix = "/fedops/api/"
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(api_prefix):
        raise HTTPException(status_code=502, detail="FedOps returned an unsupported artifact URL.")

    temporary = tempfile.NamedTemporaryFile(prefix="fedops-download-", delete=False)
    destination = Path(temporary.name)
    temporary.close()
    destination.unlink(missing_ok=True)
    try:
        await run_in_threadpool(
            lambda: download_authenticated_fedops_artifact(
                session,
                parsed.path.removeprefix(api_prefix),
                destination,
            )
        )
    except PermissionError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=403, detail=str(error)) from error
    except RuntimeError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(error)) from error
    return FileResponse(
        destination,
        filename=Path(fallback_name).name or "fedops-artifact",
        media_type="application/octet-stream",
        background=BackgroundTask(destination.unlink, missing_ok=True),
    )


def local_artifact_descriptor(request: Request, descriptor: Any) -> Any:
    if not isinstance(descriptor, dict):
        return descriptor
    return {
        **descriptor,
        "url": str(request.url.include_query_params(artifact="true")),
    }


def can_open_published_release(release: Any) -> bool:
    if not isinstance(release, dict) or not isinstance(release.get("task"), dict):
        return False
    permissions = release["task"].get("permissions")
    if not isinstance(permissions, dict):
        return False
    return bool(
        permissions.get("canOpenWorkspace")
        or permissions.get("isOwner")
        or permissions.get("isParticipant")
        or permissions.get("canManage")
    )


@router.get("/account-tasks")
async def list_account_tasks(request: Request) -> dict[str, object]:
    session = session_for(request)
    username = str(session.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="The session has no username.")
    payload = await remote(
        lambda: fedops_request(
            session,
            "tasks",
            query={"username": username, "scope": "owned"},
        )
    )
    return {
        "items": normalize_account_tasks(payload),
        "source": "fedops-web-account-tasks",
    }


@router.get("/tasks")
async def list_tasks(
    request: Request,
    view: str = Query(default="public", pattern="^(public|all|owned|joined)$"),
    q: str | None = None,
    model_type: str | None = None,
    tag: str | None = None,
    page: int = Query(default=1, ge=1),
) -> dict[str, object]:
    session = session_for(request)
    if view == "public":
        payload = await remote(lambda: fedops_request(
            session,
            "tasks/public",
            query={"q": q, "modelType": model_type, "tag": tag, "page": page},
        ))
    else:
        payload = await remote(lambda: fedops_request(
            session,
            "tasks",
            query={"scope": view, "page": page},
        ))
    return {
        "items": normalize_registry_tasks(payload),
        "view": view,
        "page": page,
        "source": "fedops-web",
    }


@router.get("/tasks/public/{handle}/{slug}")
async def read_public_task(handle: str, slug: str, request: Request) -> dict[str, Any]:
    session = session_for(request)
    payload = await remote(lambda: fedops_request(
        session,
        f"tasks/public/{quote_segment(handle)}/{quote_segment(slug)}",
    ))
    task = normalize_registry_task(payload)
    if not task:
        raise HTTPException(status_code=502, detail="FedOps returned an invalid Task response.")
    return task


@router.get("/tasks/public/{handle}/{slug}/activity")
async def read_public_activity(handle: str, slug: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"tasks/public/{quote_segment(handle)}/{quote_segment(slug)}/activity",
    ))


@router.get("/tasks/public/{handle}/{slug}/hub")
async def read_public_hub(handle: str, slug: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"model/public/{quote_segment(handle)}/{quote_segment(slug)}",
    ))


@router.get("/tasks/runtime/{runtime_key}/activity")
async def read_authorized_activity(runtime_key: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"tasks/{quote_segment(runtime_key)}/activity",
    ))


@router.get("/tasks/runtime/{runtime_key}/hub")
async def read_authorized_hub(runtime_key: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"model/{quote_segment(runtime_key)}/hub",
    ))


@router.post("/tasks/public/{handle}/{slug}/join")
async def join_public_task(handle: str, slug: str, request: Request) -> Any:
    session = session_for(request)
    public_task = await remote(lambda: fedops_request(
        session,
        f"tasks/public/{quote_segment(handle)}/{quote_segment(slug)}",
    ))
    task = normalize_registry_task(public_task)
    if not task:
        raise HTTPException(status_code=502, detail="FedOps returned an invalid Task response.")
    task_id = task.get("taskId")
    if not task_id:
        raise HTTPException(status_code=409, detail="FedOps did not expose a stable taskId.")
    payload = await remote(lambda: fedops_request(
        session,
        f"tasks/id/{quote_segment(str(task_id))}/participants",
        method="POST",
        payload={},
    ))
    participation = normalize_participation(payload, task)
    if not participation:
        raise HTTPException(status_code=502, detail="FedOps returned an invalid participation response.")
    return participation


@router.delete("/tasks/public/{handle}/{slug}/participation")
async def leave_public_task(handle: str, slug: str, request: Request) -> Any:
    session = session_for(request)
    public_task = await remote(lambda: fedops_request(
        session,
        f"tasks/public/{quote_segment(handle)}/{quote_segment(slug)}",
    ))
    task = normalize_registry_task(public_task)
    if not task:
        raise HTTPException(status_code=502, detail="FedOps returned an invalid Task response.")
    task_id = task.get("taskId")
    if not task_id:
        raise HTTPException(status_code=409, detail="FedOps did not expose a stable taskId.")
    payload = await remote(lambda: fedops_request(
        session,
        f"tasks/id/{quote_segment(str(task_id))}/participants/me",
        method="DELETE",
    ))
    participation = normalize_participation(payload, task)
    if not participation:
        raise HTTPException(status_code=502, detail="FedOps returned an invalid participation response.")
    return participation


@router.get("/tasks/public/{handle}/{slug}/files/{file_id}/preview")
async def preview_public_file(handle: str, slug: str, file_id: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"model/public/{quote_segment(handle)}/{quote_segment(slug)}/files/{quote_segment(file_id)}/preview",
    ))


@router.get("/tasks/public/{handle}/{slug}/files/{file_id}/download")
async def download_public_file(
    handle: str,
    slug: str,
    file_id: str,
    request: Request,
    artifact: bool = Query(default=False),
) -> Any:
    session = session_for(request)
    descriptor = await remote(lambda: fedops_request(
        session,
        f"model/public/{quote_segment(handle)}/{quote_segment(slug)}/files/{quote_segment(file_id)}/download",
    ))
    if artifact:
        return await artifact_download_response(session, descriptor, fallback_name=file_id)
    return local_artifact_descriptor(request, descriptor)


@router.get("/tasks/runtime/{runtime_key}/files/{file_id}/preview")
async def preview_authorized_file(runtime_key: str, file_id: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"model/{quote_segment(runtime_key)}/files/{quote_segment(file_id)}/preview",
    ))


@router.get("/tasks/runtime/{runtime_key}/files/{file_id}/download")
async def download_authorized_file(
    runtime_key: str,
    file_id: str,
    request: Request,
    artifact: bool = Query(default=False),
) -> Any:
    session = session_for(request)
    descriptor = await remote(lambda: fedops_request(
        session,
        f"model/{quote_segment(runtime_key)}/files/{quote_segment(file_id)}/download",
    ))
    if artifact:
        return await artifact_download_response(session, descriptor, fallback_name=file_id)
    return local_artifact_descriptor(request, descriptor)


@router.get("/tasks/public/{handle}/{slug}/models/{version_id}/download")
async def download_public_model(
    handle: str,
    slug: str,
    version_id: str,
    request: Request,
    artifact: bool = Query(default=False),
) -> Any:
    session = session_for(request)
    descriptor = await remote(lambda: fedops_request(
        session,
        f"model/public/{quote_segment(handle)}/{quote_segment(slug)}/versions/{quote_segment(version_id)}/download",
    ))
    if artifact:
        return await artifact_download_response(
            session, descriptor, fallback_name=f"model-{version_id}.bin"
        )
    return local_artifact_descriptor(request, descriptor)


@router.get("/tasks/runtime/{runtime_key}/models/{version_id}/download")
async def download_authorized_model(
    runtime_key: str,
    version_id: str,
    request: Request,
    artifact: bool = Query(default=False),
) -> Any:
    session = session_for(request)
    descriptor = await remote(lambda: fedops_request(
        session,
        f"model/{quote_segment(runtime_key)}/versions/{quote_segment(version_id)}/download",
    ))
    if artifact:
        return await artifact_download_response(
            session, descriptor, fallback_name=f"model-{version_id}.bin"
        )
    return local_artifact_descriptor(request, descriptor)


@router.get("/tasks/{task_id}/published-release")
async def read_published_release(task_id: str, request: Request) -> Any:
    session = session_for(request)
    return await remote(lambda: fedops_request(
        session,
        f"task-releases/tasks/{quote_segment(task_id)}/published",
    ))


@router.post("/tasks/{task_id}/published-release", status_code=201)
async def open_published_release(
    task_id: str,
    payload: OpenPublishedReleaseRequest,
    request: Request,
) -> Any:
    session = session_for(request)
    account = account_context(session)
    try:
        release = await run_in_threadpool(
            lambda: fedops_request(
                session,
                f"task-releases/tasks/{quote_segment(task_id)}/published",
            )
        )
        if not can_open_published_release(release):
            raise PermissionError(
                "Owner access or approved Federated Learning participation is required "
                "to open this Task in Workspace."
            )
        task = release.get("task") if isinstance(release, dict) else None
        if not isinstance(task, dict) or str(task.get("taskId") or "") != task_id:
            raise RuntimeError("Published Release Task identity does not match the requested taskId.")

        existing = await run_in_threadpool(
            find_project_for_task,
            account.workspace_root,
            task_id,
        )
        if existing:
            local_project_id = str(existing["localProjectId"])
            _, project_root = await run_in_threadpool(
                find_project,
                account.workspace_root,
                local_project_id,
            )
            reconciliation = await run_in_threadpool(
                reconcile_existing_published_release,
                project_root,
                release,
            )
            update_message = (
                "A newer Published Release is available; local source and model files were not overwritten.\n"
                if reconciliation["releaseUpdateAvailable"]
                else "Published Release identity is current.\n"
            )
            return RUN_MANAGER.record_completed(
                kind="create",
                local_project_id=local_project_id,
                command=f"open Published Task {task_id}",
                output=(
                    f"Opened existing Workspace for {task.get('displayName') or task.get('title') or task_id}.\n"
                    f"Workspace: {existing.get('path') or existing.get('name')}\n"
                    f"{update_message}"
                ),
                result_local_project_id=local_project_id,
                namespace=account.account_key,
            )

        model = release.get("model") if isinstance(release, dict) else None
        if not isinstance(model, dict) or not model.get("modelVersionId"):
            raise RuntimeError("Published Release has no downloadable model identity.")
        workspace_name = str(
            payload.name
            or task.get("slug")
            or task.get("displayName")
            or task.get("title")
            or task_id
        )
        with tempfile.TemporaryDirectory(prefix="fedops-published-") as directory:
            root = Path(directory)
            archive = root / "release.fedops.zip"
            model_file = root / "model.safetensors"
            await run_in_threadpool(
                lambda: download_authenticated_fedops_artifact(
                    session,
                    f"task-releases/tasks/{quote_segment(task_id)}/published/archive",
                    archive,
                    expected_size=int(release["bundleSize"]),
                    expected_sha256=str(release["bundleSha256"]),
                )
            )
            await run_in_threadpool(
                lambda: download_authenticated_fedops_artifact(
                    session,
                    "task-releases/tasks/"
                    f"{quote_segment(task_id)}/models/"
                    f"{quote_segment(str(model['modelVersionId']))}/artifact",
                    model_file,
                    expected_size=int(model["size"]),
                    expected_sha256=str(model["sha256"]),
                )
            )
            return await run_in_threadpool(
                install_published_release,
                account.workspace_root,
                workspace_name,
                archive,
                release,
                model_file,
                account.account_key,
            )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (KeyError, RuntimeError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
