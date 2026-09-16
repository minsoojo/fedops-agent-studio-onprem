"""Authenticated Agent Builder transport over account-local model assets."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
import shutil
import subprocess
from typing import Annotated, Any, Literal
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from studio_runtime.agent_builder import (
    AgentStore,
    agent_data_root,
    list_local_model_sources,
    local_llm_reference,
    local_tool_reference,
    resolve_local_model_source,
    validate_agent_draft,
)
from studio_runtime.agents import (
    AgentRuntimeUnavailable,
    chat_agent_definition,
    read_agent_definition_llm_preparation,
    start_agent_definition_llm_preparation,
    test_agent_definition,
)
from studio_runtime.model_runner import huggingface_model_relative_path
from studio_runtime.workspace import find_project

from ...account import AccountContext, account_context
from ...integrations.fedops_web import (
    download_authenticated_fedops_artifact,
    download_fedops_artifact,
    fedops_request,
)
from ...session import get_local_session, require_fedops_session
from ...streaming import agent_chat_stream
from ..registry.task_mapper import normalize_registry_tasks

router = APIRouter(prefix="/api/v1/agent-builder", tags=["agent-builder"])


class HuggingFaceLlmRequest(BaseModel):
    source: Literal["huggingface"]
    repoId: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=120)
    fileName: str | None = Field(default=None, max_length=240)
    format: Literal["transformers", "gguf"] = "transformers"
    displayName: str = Field(min_length=1, max_length=120)
    license: str = Field(min_length=1, max_length=80)
    localPath: str | None = Field(default=None, max_length=1_024)
    runtimeStatus: Literal["not-prepared", "preparing", "installed", "ready", "error"] = "not-prepared"


class FederatedTaskLlmRequest(BaseModel):
    source: Literal["federated-task"]
    localProjectId: str = Field(min_length=1, max_length=256)
    projectName: str = Field(min_length=1, max_length=160)
    taskId: str | None = Field(default=None, max_length=128)
    taskTitle: str = Field(min_length=1, max_length=160)
    modelName: str = Field(min_length=1, max_length=200)
    releaseId: str | None = Field(default=None, max_length=128)
    modelVersionId: str | None = Field(default=None, max_length=128)
    sourceFingerprint: str = Field(min_length=64, max_length=64)
    modelSha256: str = Field(min_length=64, max_length=64)
    localPath: str = Field(min_length=1, max_length=1_024)
    modelArtifactPath: str | None = Field(default=None, max_length=1_024)
    runtimeStatus: Literal["installed", "ready", "error"] = "installed"


AgentLlmRequest = Annotated[
    HuggingFaceLlmRequest | FederatedTaskLlmRequest,
    Field(discriminator="source"),
]


class AgentToolRequest(BaseModel):
    localProjectId: str = Field(min_length=1, max_length=256)
    projectName: str = Field(min_length=1, max_length=160)
    taskId: str | None = Field(default=None, max_length=128)
    runtimeKey: str | None = Field(default=None, max_length=128)
    registryId: str | None = Field(default=None, max_length=256)
    taskTitle: str = Field(min_length=1, max_length=160)
    releaseId: str | None = Field(default=None, max_length=128)
    modelVersionId: str | None = Field(default=None, max_length=128)
    modelName: str = Field(min_length=1, max_length=200)
    modelVersion: int | None = Field(default=None, ge=1)
    format: str | None = Field(default=None, max_length=80)
    sourceFingerprint: str = Field(min_length=64, max_length=64)
    modelSha256: str = Field(min_length=64, max_length=64)
    localPath: str = Field(min_length=1, max_length=1_024)
    modelArtifactPath: str | None = Field(default=None, max_length=1_024)
    toolManifest: dict[str, Any]


class AgentHarnessRequest(BaseModel):
    # Drafts may prepare and test a Base LLM before the Harness is written.
    # Non-empty Instructions remain a Validation/Build readiness requirement.
    instructions: str = Field(max_length=12_000)
    toolRouting: Literal["automatic", "explicit"] = "automatic"
    contextWindow: int = Field(default=32768, ge=512, le=262_144)
    memory: Literal["none", "session"] = "session"
    safety: Literal["standard", "strict"] = "standard"
    temperature: float = Field(default=0.2, ge=0, le=2)
    maxTokens: int = Field(default=512, ge=1, le=8192)


class CreateAgentDraftRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class UpdateAgentDraftRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    llm: AgentLlmRequest | None
    tools: list[AgentToolRequest] = Field(max_length=16)
    harness: AgentHarnessRequest


class AgentChatMessageRequest(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class AgentPreviewRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    history: list[AgentChatMessageRequest] = Field(default_factory=list, max_length=40)
    toolInput: dict[str, Any] | None = None
    toolId: str | None = Field(default=None, max_length=256)


def require_account(request: Request) -> tuple[AccountContext, dict[str, Any]]:
    try:
        session = require_fedops_session(request)
        return account_context(session), session
    except PermissionError as error:
        raise HTTPException(
            status_code=403 if get_local_session(request) else 401,
            detail=str(error),
        ) from error


def store_for(account: AccountContext) -> AgentStore:
    return AgentStore(agent_data_root(account.workspace_root.parent / ".local-data"))


def model_sources(account: AccountContext) -> list[dict[str, Any]]:
    return list_local_model_sources(account.workspace_root, account.display_workspace_root)


def _source_hub(session: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    runtime_key = str(source.get("runtimeKey") or "").strip()
    if not runtime_key:
        return {}
    value = fedops_request(session, f"model/{urllib.parse.quote(runtime_key, safe='')}/hub")
    return value if isinstance(value, dict) else {}


def _safe_source_hub(session: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    try:
        return _source_hub(session, source)
    except (PermissionError, RuntimeError):
        return {}


def enriched_model_sources(
    account: AccountContext,
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    sources = model_sources(account)
    if not sources:
        return sources
    with ThreadPoolExecutor(max_workers=min(4, len(sources))) as executor:
        hubs = list(executor.map(lambda source: _safe_source_hub(session, source), sources))
    for source, hub in zip(sources, hubs, strict=True):
        if not hub:
            continue
        cached = {
            str(item.get("modelVersionId")): item
            for item in source.get("availableModelVersions", [])
            if item.get("modelVersionId")
        }
        versions: list[dict[str, Any]] = []
        for item in hub.get("models") if isinstance(hub.get("models"), list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            model_version_id = str(item["id"])
            local = cached.get(model_version_id, {})
            versions.append({
                "modelVersionId": model_version_id,
                "version": item.get("version"),
                "role": item.get("role") or "global",
                "label": item.get("label") or f"Global Model v{item.get('version')}",
                "sourceFormat": local.get("sourceFormat") or item.get("format"),
                "format": local.get("format") or item.get("format"),
                "size": item.get("size"),
                "sha256": local.get("sha256") or item.get("checksum"),
                "cached": bool(local.get("cached")),
                "modelArtifactPath": local.get("modelArtifactPath"),
            })
        source["availableModelVersions"] = versions or source.get("availableModelVersions", [])
    return sources


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run_project_python(
    project_root: Path,
    script: str,
    *arguments: Path,
    action: str,
    timeout: int = 600,
) -> None:
    """Run one model preparation check in the Task's selected uv contract."""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(f"uv is required to {action}.")
    result = subprocess.run(
        [
            uv, "run", "--project", str(project_root), "--locked", "python", "-c",
            script,
            *(str(argument) for argument in arguments),
        ],
        cwd=project_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = str(result.stdout or "").strip()[-2_000:]
        raise RuntimeError(f"Failed to {action}.\n{detail}")


def _validate_tool_model_artifact(
    source: dict[str, Any],
    project_root: Path,
    artifact_path: Path,
) -> None:
    """Fail preparation early when this exact model cannot load in the Task."""
    if source.get("capability") != "tool-ai":
        return
    _run_project_python(
        project_root,
        "import sys; from pathlib import Path; "
        "from federated_task.runtime.model_release import load_released_model; "
        "model=load_released_model(Path(sys.argv[1])); "
        "assert model is not None, 'model loader returned no model'",
        artifact_path,
        action="verify this Global Model against the Federated Task architecture",
    )


def _download_model_version(
    account: AccountContext,
    session: dict[str, Any],
    local_project_id: str,
    model_version_id: str,
) -> dict[str, Any]:
    sources = model_sources(account)
    source = next(
        (item for item in sources if item["localProjectId"] == local_project_id),
        None,
    )
    if not source:
        raise KeyError(local_project_id)
    hub = _source_hub(session, source)
    model = next(
        (
            item for item in hub.get("models", [])
            if isinstance(item, dict) and str(item.get("id") or "") == model_version_id
        ),
        None,
    )
    if not model:
        raise ValueError("The selected Global Model version is unavailable.")
    if model.get("role") == "initial" or model.get("source") == "task_release":
        return resolve_local_model_source(
            account.workspace_root,
            {**source, "modelVersionId": model_version_id},
            account.display_workspace_root,
        )
    runtime_key = str(source.get("runtimeKey") or "")
    descriptor = fedops_request(
        session,
        f"model/{urllib.parse.quote(runtime_key, safe='')}/versions/"
        f"{urllib.parse.quote(model_version_id, safe='')}/download",
    )
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("url"), str):
        raise RuntimeError("FedOps returned an invalid Global Model download descriptor.")
    _, project_root = find_project(account.workspace_root, local_project_id)
    safe_id = "".join(value for value in model_version_id if value.isalnum() or value in {"-", "_"})
    version_root = project_root / ".fedops-studio" / "model-versions" / safe_id
    version_root.mkdir(parents=True, exist_ok=True)
    extension = Path(str(model.get("name") or "model.bin")).suffix.lower() or ".bin"
    raw_path = version_root / f"download{extension}"
    raw_path.unlink(missing_ok=True)
    url = str(descriptor["url"])
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"https", "http"}:
        download_fedops_artifact(url, raw_path, int(model.get("size") or 0))
    elif parsed.path.startswith("/fedops/api/"):
        download_authenticated_fedops_artifact(
            session,
            parsed.path.removeprefix("/fedops/api/"),
            raw_path,
            expected_size=int(model.get("size")) if model.get("size") is not None else None,
        )
    else:
        raise RuntimeError("FedOps returned an unsupported Global Model URL.")
    artifact_path = raw_path
    source_format = str(model.get("format") or extension.lstrip("."))
    output_format = source_format
    if extension in {".pth", ".pt"}:
        artifact_path = version_root / "model.safetensors"
        artifact_path.unlink(missing_ok=True)
        _run_project_python(
            project_root,
            "import sys,torch; from safetensors.torch import save_file; "
            "value=torch.load(sys.argv[1],map_location='cpu',weights_only=True); "
            "value=value.get('state_dict',value) if isinstance(value,dict) else value; "
            "assert isinstance(value,dict), 'Global Model must contain a PyTorch state_dict'; "
            "assert all(hasattr(v,'detach') for v in value.values()), "
            "'Global Model state_dict contains a non-tensor value'; "
            "save_file({str(k):v.detach().cpu().contiguous() for k,v in value.items()},sys.argv[2])",
            raw_path,
            artifact_path,
            action="convert this PyTorch Global Model to the Agent Studio safetensors contract",
        )
        output_format = "safetensors"
    if artifact_path.suffix.lower() == ".safetensors":
        _validate_tool_model_artifact(source, project_root, artifact_path)
    if artifact_path != raw_path:
        raw_path.unlink(missing_ok=True)
    manifest = {
        "schemaVersion": 1,
        "modelVersionId": model_version_id,
        "version": model.get("version"),
        "role": model.get("role") or "global",
        "label": model.get("label") or f"Global Model v{model.get('version')}",
        "sourceFormat": source_format,
        "format": output_format,
        "artifact": artifact_path.name,
        "size": artifact_path.stat().st_size,
        "sha256": _sha256(artifact_path),
        "cachedAt": datetime.now(UTC).isoformat(),
    }
    temporary = version_root / ".manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(version_root / "manifest.json")
    return resolve_local_model_source(
        account.workspace_root,
        {**source, "modelVersionId": model_version_id},
        account.display_workspace_root,
    )


def registry_model_source(task: dict[str, Any]) -> dict[str, Any]:
    permissions = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
    membership = task.get("membership") if isinstance(task.get("membership"), dict) else {}
    participation_status = permissions.get("participationStatus") or membership.get("status")
    has_release = bool(
        task.get("registryStatus") == "published"
        and task.get("currentPublishedReleaseId")
    )
    if not has_release:
        access_state = "unavailable"
    elif permissions.get("canOpenWorkspace"):
        access_state = "workspace-required"
    elif participation_status in {"requested", "pending-approval"}:
        access_state = "approval-pending"
    elif permissions.get("canRequestParticipation"):
        access_state = "join-required"
    else:
        access_state = "unavailable"
    primary = task.get("primaryModel") if isinstance(task.get("primaryModel"), dict) else {}
    return {
        "registryId": str(task["registryId"]),
        "taskId": task.get("taskId"),
        "title": str(task["title"]),
        "displayName": str(task["displayName"]),
        "modelName": str(
            primary.get("displayName")
            or primary.get("workingName")
            or task["title"]
        ),
        "capability": str(task["modelCapability"]),
        "ownerHandle": task.get("ownerHandle"),
        "slug": task.get("slug"),
        "summary": str(task.get("summary") or ""),
        "participationPolicy": str(task.get("participationPolicy") or "approval_required"),
        "registryStatus": str(task.get("registryStatus") or "legacy"),
        "membershipRole": membership.get("role"),
        "participationStatus": participation_status,
        "accessState": access_state,
        "canRequestParticipation": bool(permissions.get("canRequestParticipation")),
        "canOpenWorkspace": bool(permissions.get("canOpenWorkspace")),
    }


def verified_components(
    account: AccountContext,
    draft: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    tools: list[dict[str, Any]] = []
    for reference in draft.get("tools") if isinstance(draft.get("tools"), list) else []:
        source = resolve_local_model_source(
            account.workspace_root,
            reference,
            account.display_workspace_root,
        )
        tools.append(local_tool_reference(source))

    llm_value = draft.get("llm") if isinstance(draft.get("llm"), dict) else None
    if not llm_value:
        return None, tools
    if llm_value.get("source") == "federated-task":
        source = resolve_local_model_source(
            account.workspace_root,
            llm_value,
            account.display_workspace_root,
        )
        return local_llm_reference(source), tools
    if llm_value.get("source") != "huggingface":
        raise ValueError("The selected Base LLM source is unsupported.")
    repo_id = str(llm_value.get("repoId") or "").strip()
    revision = str(llm_value.get("revision") or "").strip()
    file_name = str(llm_value.get("fileName") or "").strip() or None
    model_format = str(llm_value.get("format") or ("gguf" if file_name else "transformers"))
    if model_format == "gguf" and not (file_name and file_name.lower().endswith(".gguf")):
        raise ValueError("Select one .gguf model file for a GGUF Base LLM.")
    relative = huggingface_model_relative_path(repo_id, revision, file_name)
    display_path = account.display_workspace_root.parent / "models" / relative
    return {
        "source": "huggingface",
        "repoId": repo_id,
        "revision": revision,
        "fileName": file_name,
        "format": model_format,
        "displayName": str(llm_value.get("displayName") or repo_id),
        "license": str(llm_value.get("license") or "unknown"),
        "localPath": str(display_path),
        "runtimeStatus": str(llm_value.get("runtimeStatus") or "not-prepared"),
    }, tools


async def resolve_or_conflict(
    account: AccountContext,
    draft: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        return await run_in_threadpool(verified_components, account, draft)
    except (OSError, TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


async def verified_draft_definition(
    account: AccountContext,
    agent_id: str,
) -> tuple[AgentStore, dict[str, Any]]:
    store = store_for(account)
    try:
        draft = await run_in_threadpool(store.read_draft, agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Agent draft not found.") from error
    llm, tools = await resolve_or_conflict(account, draft)
    return store, {**draft, "llm": llm, "tools": tools}


def display_runtime_path(account: AccountContext, result: dict[str, Any]) -> dict[str, Any]:
    runtime_path = result.get("localPath")
    if not runtime_path:
        return result
    try:
        relative = Path(str(runtime_path)).resolve().relative_to(account.workspace_root.parent)
    except ValueError:
        return result
    return {**result, "localPath": str(account.display_workspace_root.parent / relative)}


@router.get("/model-sources")
async def list_model_sources(request: Request) -> dict[str, Any]:
    account, session = require_account(request)
    return {
        "items": await run_in_threadpool(enriched_model_sources, account, session),
        "source": "account-local-workspace-and-registry-versions",
    }


@router.post("/model-sources/{local_project_id}/versions/{model_version_id}/prepare")
async def prepare_model_version(
    local_project_id: str,
    model_version_id: str,
    request: Request,
) -> dict[str, Any]:
    account, session = require_account(request)
    try:
        await run_in_threadpool(
            _download_model_version,
            account,
            session,
            local_project_id,
            model_version_id,
        )
        sources = await run_in_threadpool(enriched_model_sources, account, session)
        return next(
            source for source in sources
            if source["localProjectId"] == local_project_id
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Workspace project not found.") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/registry-model-sources")
async def list_registry_model_sources(request: Request) -> dict[str, Any]:
    _, session = require_account(request)
    try:
        public_payload = await run_in_threadpool(
            lambda: fedops_request(session, "tasks/public", query={"page": 1})
        )
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "items": [
            registry_model_source(task)
            for task in normalize_registry_tasks(public_payload)
        ],
        "source": "fedops-web-registry",
    }


@router.get("/drafts")
async def list_drafts(request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    return {
        "items": await run_in_threadpool(store_for(account).list_drafts),
        "source": "account-local-agent-store",
    }


@router.post("/drafts", status_code=201)
async def create_draft(payload: CreateAgentDraftRequest, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    try:
        return await run_in_threadpool(
            store_for(account).create_draft,
            payload.name,
            payload.description,
        )
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/drafts/{agent_id}")
async def read_draft(agent_id: str, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    try:
        return await run_in_threadpool(store_for(account).read_draft, agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Agent draft not found.") from error


@router.put("/drafts/{agent_id}")
async def update_draft(
    agent_id: str,
    payload: UpdateAgentDraftRequest,
    request: Request,
) -> dict[str, Any]:
    account, _ = require_account(request)
    try:
        return await run_in_threadpool(
            store_for(account).update_draft,
            agent_id,
            payload.model_dump(),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent draft not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/drafts/{agent_id}")
async def delete_draft(agent_id: str, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    try:
        await run_in_threadpool(store_for(account).delete_draft, agent_id)
        return {"deleted": True, "agentId": agent_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent draft not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/drafts/{agent_id}/validate")
async def validate_draft(agent_id: str, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    store = store_for(account)
    try:
        draft = await run_in_threadpool(store.read_draft, agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Agent draft not found.") from error
    llm, tools = await resolve_or_conflict(account, draft)
    return await run_in_threadpool(
        validate_agent_draft,
        store,
        agent_id,
        tools,
        llm,
    )


@router.post("/drafts/{agent_id}/test")
async def test_draft(
    agent_id: str,
    payload: AgentPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    account, _ = require_account(request)
    store, definition = await verified_draft_definition(account, agent_id)
    try:
        return await run_in_threadpool(
            test_agent_definition,
            store,
            definition,
            payload.message,
            payload.toolInput,
            payload.toolId,
        )
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/drafts/{agent_id}/chat")
async def chat_with_draft(
    agent_id: str,
    payload: AgentPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    account, _ = require_account(request)
    store, definition = await verified_draft_definition(account, agent_id)
    try:
        return await run_in_threadpool(
            chat_agent_definition,
            store,
            definition,
            payload.message,
            payload.toolInput,
            payload.toolId,
            [item.model_dump() for item in payload.history],
        )
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/drafts/{agent_id}/chat/stream")
async def stream_chat_with_draft(
    agent_id: str,
    payload: AgentPreviewRequest,
    request: Request,
):
    account, _ = require_account(request)
    store, definition = await verified_draft_definition(account, agent_id)
    history = [item.model_dump() for item in payload.history]
    return agent_chat_stream(
        lambda on_delta: chat_agent_definition(
            store,
            definition,
            payload.message,
            payload.toolInput,
            payload.toolId,
            history,
            on_delta,
        )
    )


@router.post("/drafts/{agent_id}/llm/prepare", status_code=202)
async def prepare_draft_llm(agent_id: str, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    store, definition = await verified_draft_definition(account, agent_id)
    try:
        result = await run_in_threadpool(start_agent_definition_llm_preparation, store, definition)
        return display_runtime_path(account, result)
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/drafts/{agent_id}/llm/prepare")
async def read_draft_llm_preparation(agent_id: str, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    store, definition = await verified_draft_definition(account, agent_id)
    try:
        result = await run_in_threadpool(read_agent_definition_llm_preparation, store, definition)
        return display_runtime_path(account, result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Base LLM preparation has not started.") from error
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/drafts/{agent_id}/build", status_code=201)
async def build_agent(agent_id: str, request: Request) -> dict[str, Any]:
    account, _ = require_account(request)
    store = store_for(account)
    try:
        draft = await run_in_threadpool(store.read_draft, agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Agent draft not found.") from error
    llm, tools = await resolve_or_conflict(account, draft)
    report = await run_in_threadpool(
        validate_agent_draft,
        store,
        agent_id,
        tools,
        llm,
    )
    if not report["ok"]:
        raise HTTPException(status_code=409, detail="Agent validation has failed checks.")
    try:
        return await run_in_threadpool(store.commit_build, agent_id, tools, llm)
    except (FileExistsError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


__all__ = ["router"]
