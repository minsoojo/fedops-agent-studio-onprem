import tempfile
from pathlib import Path
from urllib.parse import quote
from studio_runtime.validation_data import build_validation_archive

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from studio_runtime.execution import run_python_file_run
from studio_runtime.federated_task import (
    build_release_archive,
    create_workspace_from_release,
    release_inputs,
    task_action_run,
    validate_project_run,
    workspace_directory_name,
    write_task_binding,
)
from studio_runtime.folder import (
    open_project_data_directory,
    open_workspace_directory,
    prepare_project_data_directory,
)
from studio_runtime.jobs import RUN_MANAGER
from studio_runtime.legacy_workspace import (
    discover_legacy_projects,
    import_legacy_projects,
)
from studio_runtime.task_data import build_task_data_sample
from studio_runtime.workspace import (
    build_file_tree,
    create_text_file,
    delete_project,
    delete_text_file,
    discover_projects,
    find_project,
    format_text_file,
    project_for_task,
    read_text_file,
    save_text_file,
)

from ...account import AccountContext, account_context
from ...config import (
    BASELINE_CACHE_DIR,
    FOLDER_OPENER_TOKEN_FILE,
    FOLDER_OPENER_URL,
    WORKSPACE_DIR,
    WORKSPACE_DISPLAY_DIR,
)
from ...integrations.fedops_web import (
    download_authenticated_fedops_artifact,
    fedops_request,
    upload_fedops_binary,
)
from ...session import get_local_session, require_fedops_session
from .realtime import close_project_terminals, ensure_terminal_session
from .schemas import (
    CreateFileRequest,
    CreateWorkspaceRequest,
    DeleteWorkspaceRequest,
    FileActionRequest,
    ImportLegacyWorkspacesRequest,
    LinkTaskRequest,
    SaveFileRequest,
    TaskDataSampleRequest,
    WorkspaceActionRequest,
    ValidationUploadRequest,
)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspace"])


def require_account(request: Request) -> AccountContext:
    try:
        return account_context(require_fedops_session(request))
    except PermissionError as error:
        raise HTTPException(
            status_code=403 if get_local_session(request) else 401, detail=str(error)
        ) from error


def project_for(
    account: AccountContext,
    local_project_id: str,
) -> tuple[dict[str, object], Path]:
    try:
        return find_project(account.workspace_root, local_project_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def prepare_data_binding(
    account: AccountContext,
    local_project_id: str,
    project_root: Path,
) -> dict[str, object]:
    return prepare_project_data_directory(
        account.workspace_root.parent / ".local-data",
        project_root.name,
        local_project_id=local_project_id,
        display_local_data_root=account.display_workspace_root.parent / ".local-data",
    )


def _release_submission_error(project_root: Path) -> str | None:
    try:
        release_inputs(project_root)
    except (KeyError, TypeError):
        return "Release Readiness data is incomplete. Run Release Readiness again."
    except (OSError, ValueError) as error:
        return str(error)
    return None


def web_task_workspace_name(
    task: dict[str, object],
    projects: list[dict[str, object]],
) -> str:
    """Derive a local folder from Web identity without redefining Task identity."""
    preferred = str(
        task.get("slug")
        or task.get("displayName")
        or task.get("runtimeKey")
        or task.get("taskId")
        or "federated-task"
    )
    directory_name = workspace_directory_name(preferred)
    used_names = {str(project.get("name") or "") for project in projects}
    if directory_name not in used_names:
        return directory_name
    identity_suffix = str(task.get("taskId") or "")[-6:] or "draft"
    prefix = directory_name[: 63 - len(identity_suffix)].rstrip("-")
    return f"{prefix}-{identity_suffix}"


@router.get("")
async def list_workspaces(request: Request) -> dict[str, object]:
    account = require_account(request)
    return {
        "items": discover_projects(account.workspace_root),
        "source": "local-runtime",
    }


@router.post("", status_code=202)
async def create_workspace(
    payload: CreateWorkspaceRequest, request: Request
) -> dict[str, object]:
    account = require_account(request)
    local_name = str(payload.name or "").strip()
    if not payload.sourceTaskId:
        try:
            workspace_directory_name(local_name)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    session = require_fedops_session(request)
    task_context = None
    workspace_name = local_name
    try:
        if payload.sourceTaskId:
            task_context = await run_in_threadpool(
                lambda: fedops_request(
                    session,
                    f"task-releases/tasks/{payload.sourceTaskId}/context",
                )
            )
            runtime_contract = (
                task_context.get("runtimeContract")
                if isinstance(task_context, dict)
                and isinstance(task_context.get("runtimeContract"), dict)
                else {"name": "legacy-v1"}
            )
            if runtime_contract.get("name") == "legacy-v1":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This is a Legacy FedOps Task. Keep its original Web/Launcher "
                        "workflow; Agent Studio will not replace it with the newest Baseline."
                    ),
                )
            projects = await run_in_threadpool(
                discover_projects, account.workspace_root
            )
            task_id = str(task_context.get("taskId") or payload.sourceTaskId)
            existing = project_for_task(projects, task_id)
            if existing:
                local_project_id = str(existing["localProjectId"])
                return RUN_MANAGER.record_completed(
                    kind="create",
                    local_project_id=local_project_id,
                    command=f"open FedOps Web Draft {task_id}",
                    output=(
                        f"Opened existing Workspace for {task_context.get('displayName') or task_id}.\n"
                        f"Workspace: {existing.get('path') or existing.get('name')}\n"
                    ),
                    result_local_project_id=local_project_id,
                    namespace=account.account_key,
                )
            workspace_name = web_task_workspace_name(task_context, projects)
        baseline_template = (
            task_context.get("baselineTemplate")
            if isinstance(task_context, dict)
            and isinstance(task_context.get("baselineTemplate"), dict)
            else {}
        )
        release = await run_in_threadpool(
            lambda: fedops_request(
                session,
                "baselines/default",
                query={
                    "distribution": "bundled",
                    "version": baseline_template.get("version"),
                },
            )
        )
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    try:
        result = await run_in_threadpool(
            create_workspace_from_release,
            account.workspace_root,
            workspace_name,
            Path(BASELINE_CACHE_DIR),
            release,
            lambda artifact_id, destination, expected_size: (
                download_authenticated_fedops_artifact(
                    session,
                    f"baselines/default/artifacts/{artifact_id}",
                    destination,
                    expected_size=expected_size,
                )
            ),
            account.account_key,
        )
        if task_context:
            _, project_root = find_project(
                account.workspace_root,
                str(result["resultLocalProjectId"]),
            )
            await run_in_threadpool(write_task_binding, project_root, task_context)
        return result
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.put("/{local_project_id}/binding")
async def link_workspace_task(
    local_project_id: str,
    payload: LinkTaskRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    session = require_fedops_session(request)
    try:
        context = await run_in_threadpool(
            lambda: fedops_request(
                session,
                f"task-releases/tasks/{payload.taskId}/context",
            )
        )
        return await run_in_threadpool(write_task_binding, project_root, context)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/{local_project_id}/release-submission-readiness")
def get_release_submission_readiness(
    local_project_id: str,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    reason = _release_submission_error(project_root)
    return {"ready": reason is None, "reason": reason}


@router.post("/{local_project_id}/release-candidate", status_code=202)
def submit_release_candidate(
    local_project_id: str,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    readiness_error = _release_submission_error(project_root)
    if readiness_error:
        raise HTTPException(status_code=409, detail=readiness_error)
    session = require_fedops_session(request)

    def operation(run):
        RUN_MANAGER.update_progress(
            run,
            stage="preparing",
            percent=5,
            message="Validating release inputs",
        )
        inputs = release_inputs(project_root)
        task_id = str(inputs["binding"]["taskId"])
        model_manifest = inputs["modelManifest"]
        model_name = str(
            model_manifest.get("displayName")
            or (inputs.get("catalog", {}).get("primaryModel") or {}).get("displayName")
            or "Primary Model"
        )
        signature = str(model_manifest["parameterSignature"]["fingerprint"])
        RUN_MANAGER.update_progress(
            run,
            stage="exporting",
            percent=15,
            message="Uploading Initiative Model",
        )
        model = upload_fedops_binary(
            session,
            f"task-releases/tasks/{task_id}/models",
            inputs["modelPath"],
            content_type="application/octet-stream",
            headers={
                "X-FedOps-SHA256": str(model_manifest["sha256"]),
                "X-FedOps-Parameter-Signature": signature,
                "X-FedOps-Framework": str(model_manifest.get("framework") or "pytorch"),
                "X-FedOps-Model-Format": str(
                    model_manifest.get("format") or "safetensors"
                ),
                "X-FedOps-Model-Origin": str(
                    model_manifest.get("origin") or "centrally-trained"
                ),
                "X-FedOps-Model-Name": model_name,
            },
            progress_callback=lambda completed, total: RUN_MANAGER.update_progress(
                run,
                stage="exporting",
                percent=15 + (35 * completed / max(total, 1)),
                message="Uploading Initiative Model",
            ),
        )
        model_version_id = str(model.get("modelVersionId") or "")
        if not model_version_id:
            raise RuntimeError("FedOps Web did not return a Model Version identity.")
        with tempfile.TemporaryDirectory(prefix="fedops-release-") as directory:
            archive_path = Path(directory) / "release.fedops.zip"
            RUN_MANAGER.update_progress(
                run,
                stage="exporting",
                percent=55,
                message="Building immutable Release snapshot",
            )
            release = build_release_archive(
                project_root, model_version_id, archive_path
            )
            RUN_MANAGER.update_progress(
                run,
                stage="exporting",
                percent=65,
                message="Uploading Release snapshot",
            )
            return upload_fedops_binary(
                session,
                f"task-releases/tasks/{task_id}/releases",
                archive_path,
                content_type="application/octet-stream",
                headers={"X-FedOps-SHA256": str(release["sha256"])},
                progress_callback=lambda completed, total: RUN_MANAGER.update_progress(
                    run,
                    stage="exporting",
                    percent=65 + (30 * completed / max(total, 1)),
                    message="Uploading Release snapshot",
                ),
            )

    try:
        return RUN_MANAGER.start_operation(
            kind="release-candidate",
            local_project_id=local_project_id,
            command="Submit Release Candidate",
            operation=operation,
            namespace=account.account_key,
            initial_output="Preparing Initial Model and immutable Release snapshot...\n",
            initial_progress={
                "schemaVersion": 1,
                "stage": "preparing",
                "percent": 1,
                "message": "Preparing Release Candidate",
                "timestamp": "",
                "metrics": {},
            },
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/legacy")
async def list_legacy_workspaces(request: Request) -> dict[str, object]:
    account = require_account(request)
    return {
        "items": discover_legacy_projects(
            Path(WORKSPACE_DIR),
            account.workspace_root,
            display_base=Path(WORKSPACE_DISPLAY_DIR),
        ),
        "source": "device-legacy-workspace",
    }


@router.post("/legacy/import")
async def import_legacy_workspaces_for_account(
    payload: ImportLegacyWorkspacesRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    try:
        return import_legacy_projects(
            Path(WORKSPACE_DIR),
            account.workspace_root,
            account.account_key,
            payload.projectNames,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/{local_project_id}")
async def remove_workspace(
    local_project_id: str,
    payload: DeleteWorkspaceRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    active = RUN_MANAGER.active_for(
        local_project_id,
        namespace=account.account_key,
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Stop the active Workspace action before deleting this Task ({active['runId']}).",
        )
    close_project_terminals(account.account_key, local_project_id)
    try:
        result = delete_project(account.workspace_root, local_project_id, payload.name)
        RUN_MANAGER.remove_for(local_project_id, account.account_key)
        return result
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{local_project_id}/open-folder")
async def open_workspace_folder(
    local_project_id: str,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    try:
        return open_workspace_directory(
            account.workspace_root,
            project_root,
            bridge_url=FOLDER_OPENER_URL,
            token_file=(
                Path(FOLDER_OPENER_TOKEN_FILE) if FOLDER_OPENER_TOKEN_FILE else None
            ),
            display_workspace=account.display_workspace_root,
            bridge_relative_workspace=account.relative_workspace,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def validation_owner(request: Request, local_project_id: str):
    account = require_account(request)
    project, root = project_for(account, local_project_id)
    binding = project.get('taskBinding') or {}
    if binding.get('workspaceRole') not in ('owner', 'admin') or not binding.get('runtimeKey'):
        raise HTTPException(status_code=403, detail='Only an owned, linked Task can upload server validation data.')
    return account, root, quote(str(binding['runtimeKey']), safe='')


@router.get("/{local_project_id}/validation-data")
def get_validation_data(local_project_id: str, request: Request):
    account, root, task_key = validation_owner(request, local_project_id)
    local = prepare_project_data_directory(account.workspace_root.parent / '.local-data', root.name,
        local_project_id=local_project_id, purpose='validation',
        display_local_data_root=account.display_workspace_root.parent / '.local-data')
    try:
        remote = fedops_request(require_fedops_session(request), f'server-control/validation-data/{task_key}')
        return {'local': local, 'items': remote.get('items', [])}
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except RuntimeError:
        return {'local': local, 'items': [], 'serverError': 'Server data unavailable. Prepare the Task server or update its Server Manager.'}


@router.post("/{local_project_id}/open-validation-folder")
def open_validation_folder(local_project_id: str, request: Request):
    account, root, _ = validation_owner(request, local_project_id)
    try:
        return open_project_data_directory(account.workspace_root.parent / '.local-data', root.name,
            local_project_id=local_project_id, purpose='validation',
            bridge_url=FOLDER_OPENER_URL,
            token_file=Path(FOLDER_OPENER_TOKEN_FILE) if FOLDER_OPENER_TOKEN_FILE else None,
            display_local_data_root=account.display_workspace_root.parent / '.local-data',
            bridge_relative_local_data_root=account.relative_workspace.parent / '.local-data')
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/{local_project_id}/validation-data")
def upload_validation_data(local_project_id: str, request: Request, payload: ValidationUploadRequest):
    account, root, task_key = validation_owner(request, local_project_id)
    session = require_fedops_session(request)
    try:
        # Recheck authoritative Owner permission before reading or sending local files.
        fedops_request(session, f'server-control/validation-data/{task_key}')
        binding = prepare_project_data_directory(account.workspace_root.parent / '.local-data', root.name,
            local_project_id=local_project_id, purpose='validation')
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'validation.zip'
            metadata = build_validation_archive(Path(str(binding['containerPath'])), payload.relativePath, archive)
            return upload_fedops_binary(session, f'server-control/validation-data/{task_key}', archive,
                content_type='application/zip', timeout=360,
                headers={'X-FedOps-SHA256': metadata['sha256'], 'X-FedOps-Server-Data-Consent': 'true'})
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.put("/{local_project_id}/data-binding")
async def prepare_workspace_data_binding(
    local_project_id: str,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    try:
        return prepare_data_binding(account, local_project_id, project_root)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{local_project_id}/open-data-folder")
async def open_workspace_data_folder(
    local_project_id: str,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    try:
        return open_project_data_directory(
            account.workspace_root.parent / ".local-data",
            project_root.name,
            local_project_id=local_project_id,
            bridge_url=FOLDER_OPENER_URL,
            token_file=(
                Path(FOLDER_OPENER_TOKEN_FILE) if FOLDER_OPENER_TOKEN_FILE else None
            ),
            display_local_data_root=account.display_workspace_root.parent
            / ".local-data",
            bridge_relative_local_data_root=account.relative_workspace.parent
            / ".local-data",
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{local_project_id}/task-data/sample")
async def workspace_task_data_sample(
    local_project_id: str,
    request: Request,
    payload: TaskDataSampleRequest | None = None,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    binding = prepare_data_binding(account, local_project_id, project_root)
    try:
        return await run_in_threadpool(
            build_task_data_sample,
            project_root,
            local_project_id,
            Path(str(binding["containerPath"])),
            index=payload.index if payload else 0,
            data_path=payload.dataPath if payload else "",
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{local_project_id}/actions", status_code=202)
async def start_workspace_action(
    payload: WorkspaceActionRequest,
    request: Request,
    local_project_id: str,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    try:
        if payload.action == "validate":
            return validate_project_run(
                project_root,
                local_project_id,
                payload.environmentId,
                account.account_key,
            )
        if payload.action in {
            "local-train",
            "release-readiness",
            "participation-readiness",
        }:
            data_path = payload.dataPath
            if payload.action in {"local-train", "participation-readiness"}:
                if not data_path:
                    data_path = str(
                        prepare_data_binding(
                            account,
                            local_project_id,
                            project_root,
                        )["containerPath"]
                    )
                local_data_root = (
                    account.workspace_root.parent / ".local-data"
                ).resolve()
                selected_data = Path(data_path).expanduser().resolve()
                try:
                    selected_data.relative_to(local_data_root)
                except ValueError as error:
                    raise ValueError(
                        f"Local data must be inside {local_data_root}."
                    ) from error
                if not selected_data.is_dir():
                    raise ValueError(
                        "The selected local data directory does not exist."
                    )
                data_path = str(selected_data)
            return task_action_run(
                project_root,
                local_project_id,
                payload.action,
                payload.environmentId,
                account.account_key,
                data_path,
            )
        if payload.action == "run-file":
            if not payload.filePath:
                raise ValueError("A Python file path is required for run-file.")
            return run_python_file_run(
                project_root,
                local_project_id,
                payload.filePath,
                payload.environmentId,
                account.account_key,
            )
        raise HTTPException(
            status_code=422, detail=f"Unsupported Workspace action: {payload.action}"
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/runs")
async def list_workspace_runs(
    request: Request,
    local_project_id: str | None = Query(default=None),
) -> dict[str, object]:
    account = require_account(request)
    return {
        "items": RUN_MANAGER.list_for(local_project_id, account.account_key),
        "source": "local-runtime-session",
    }


@router.get("/runs/{run_id}")
async def get_workspace_run(request: Request, run_id: str) -> dict[str, object]:
    account = require_account(request)
    try:
        return RUN_MANAGER.get(run_id, account.account_key)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="Workspace run was not found."
        ) from error


@router.post("/runs/{run_id}/cancel")
async def cancel_workspace_run(request: Request, run_id: str) -> dict[str, object]:
    account = require_account(request)
    try:
        return RUN_MANAGER.cancel(run_id, account.account_key)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="Workspace run was not found."
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/files/tree")
async def read_file_tree(
    request: Request,
    local_project_id: str = Query(min_length=1),
) -> dict[str, object]:
    account = require_account(request)
    project, project_root = project_for(account, local_project_id)
    return {"project": project, "tree": build_file_tree(project_root)}


@router.get("/files/content")
async def read_file_content(
    request: Request,
    local_project_id: str = Query(min_length=1),
    file_path: str = Query(min_length=1),
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, local_project_id)
    try:
        return read_text_file(project_root, file_path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OverflowError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.put("/files/content")
async def save_file_content(
    payload: SaveFileRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, payload.localProjectId)
    try:
        return save_text_file(project_root, payload.filePath, payload.content)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OverflowError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/files/content", status_code=201)
async def create_file_content(
    payload: CreateFileRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, payload.localProjectId)
    try:
        return create_text_file(project_root, payload.filePath, payload.content)
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OverflowError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.delete("/files/content")
async def delete_file_content(
    payload: FileActionRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, payload.localProjectId)
    try:
        return delete_text_file(project_root, payload.filePath)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/files/format")
async def format_file_content(
    payload: FileActionRequest,
    request: Request,
) -> dict[str, object]:
    account = require_account(request)
    _, project_root = project_for(account, payload.localProjectId)
    try:
        return format_text_file(project_root, payload.filePath)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OverflowError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/terminal/session")
async def prepare_terminal(request: Request) -> dict[str, str]:
    require_account(request)
    await ensure_terminal_session()
    return {"status": "ready"}
