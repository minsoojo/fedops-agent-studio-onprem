"""Built Agent management and token-protected local serving transport."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from studio_runtime.agent_builder import AgentStore, agent_data_root
from studio_runtime.agents import (
    AgentRuntimeUnavailable,
    agent_health,
    agent_info,
    authorize_serving,
    chat_agent,
    delete_serving_data_source,
    disable_serving,
    enable_serving,
    find_served_agent_store,
    find_served_agent_store_by_port,
    list_requests,
    list_serving_data_sources,
    predict_agent_tool,
    read_agent_llm_preparation,
    read_serving,
    record_request,
    resolve_agent_tool_input,
    rotate_serving_token,
    set_direct_tool_serving,
    start_agent_llm_preparation,
    test_agent,
    upsert_serving_data_source,
    update_serving_port,
)

from ...account import AccountContext, account_context
from ...config import WORKSPACE_DIR
from ...session import get_local_session, require_fedops_session
from ...streaming import agent_chat_stream

router = APIRouter(tags=["agents"])


class AgentChatMessageRequest(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    history: list[AgentChatMessageRequest] = Field(default_factory=list, max_length=40)
    toolInput: dict[str, Any] | None = None
    toolId: str | None = Field(default=None, max_length=256)
    input: dict[str, Any] | None = None


class AgentToolPredictionRequest(BaseModel):
    input: dict[str, Any]


class AgentServingDataSourceRequest(BaseModel):
    toolId: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=120)
    dataPath: str = Field(default="", max_length=1_000)
    sampleIndex: int = Field(default=0, ge=0)
    selectionMode: Literal["fixed", "request"] = "fixed"
    enabledForServing: bool = False


class AgentDataSourceTestRequest(BaseModel):
    sampleIndex: int | None = Field(default=None, ge=0)


class AgentDirectToolServingRequest(BaseModel):
    enabled: bool = False


class AgentServingRequest(BaseModel):
    port: int = Field(ge=24400, le=24499)


def require_account(request: Request) -> AccountContext:
    try:
        return account_context(require_fedops_session(request))
    except PermissionError as error:
        raise HTTPException(
            status_code=403 if get_local_session(request) else 401,
            detail=str(error),
        ) from error


def account_store(request: Request) -> AgentStore:
    account = require_account(request)
    return AgentStore(agent_data_root(account.workspace_root.parent / ".local-data"))


def serving_safe_chat_result(
    result: dict[str, Any],
    input_source: dict[str, Any] | None,
) -> dict[str, Any]:
    """Remove local samples and process output from public Serving responses."""
    public_source = None if input_source is None else {
        key: input_source.get(key)
        for key in ("type", "sourceId", "toolId", "sampleIndex", "dataPath")
        if input_source.get(key) is not None
    }

    def safe_tool_result(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        safe = {
            key: item
            for key, item in value.items()
            if key not in {"input", "environmentOutput"}
        }
        if public_source is not None:
            safe["inputSource"] = public_source
        return safe

    safe_result = dict(result)
    safe_result["toolResult"] = safe_tool_result(result.get("toolResult"))
    safe_result["toolResults"] = [
        safe_tool_result(item)
        for item in result.get("toolResults", [])
    ]
    return safe_result


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="An Agent bearer token is required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="An Agent bearer token is required.")
    return token


def with_runtime_status(store: AgentStore, agent: dict[str, Any]) -> dict[str, Any]:
    health = agent_health(store, str(agent["agentId"]))
    return {
        **agent,
        "llm": {**agent["llm"], "runtimeStatus": health["llmRuntimeStatus"]},
    }


async def served_store(agent_id: str, authorization: str | None) -> AgentStore:
    try:
        store = await run_in_threadpool(
            find_served_agent_store,
            Path(WORKSPACE_DIR),
            agent_id,
        )
        await run_in_threadpool(authorize_serving, store, agent_id, bearer_token(authorization))
        return store
    except HTTPException:
        raise
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


def request_port(request: Request) -> int:
    server = request.scope.get("server")
    if not isinstance(server, tuple) or len(server) != 2:
        raise HTTPException(status_code=400, detail="The local serving port is unavailable.")
    return int(server[1])


async def dedicated_served_store(
    request: Request,
    authorization: str | None,
) -> tuple[AgentStore, str]:
    try:
        store, agent_id = await run_in_threadpool(
            find_served_agent_store_by_port,
            Path(WORKSPACE_DIR),
            request_port(request),
        )
        await run_in_threadpool(authorize_serving, store, agent_id, bearer_token(authorization))
        return store, agent_id
    except HTTPException:
        raise
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


@router.get("/api/v1/agents")
async def list_built_agents(request: Request) -> dict[str, Any]:
    store = account_store(request)
    items = await run_in_threadpool(store.list_latest_builds)
    return {
        "items": await run_in_threadpool(lambda: [with_runtime_status(store, item) for item in items]),
        "source": "account-local-agent-builds",
    }


@router.get("/api/v1/agents/{agent_id}")
async def read_built_agent(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        store = account_store(request)
        agent = await run_in_threadpool(store.latest_build, agent_id)
        return await run_in_threadpool(with_runtime_status, store, agent)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error


@router.delete("/api/v1/agents/{agent_id}")
async def delete_built_agent(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        await run_in_threadpool(account_store(request).delete_agent, agent_id)
        return {"deleted": True, "agentId": agent_id}
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/v1/agents/{agent_id}/test")
async def run_agent_test(
    agent_id: str,
    payload: AgentChatRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            test_agent,
            account_store(request),
            agent_id,
            payload.message,
            payload.toolInput,
            payload.toolId,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/v1/agents/{agent_id}/chat")
async def run_agent_chat(
    agent_id: str,
    payload: AgentChatRequest,
    request: Request,
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store = account_store(request)
    try:
        definition = await run_in_threadpool(store.latest_build, agent_id)
        tool_input, tool_id, _ = await run_in_threadpool(
            resolve_agent_tool_input,
            store,
            definition,
            payload.input,
            legacy_input=payload.toolInput,
            legacy_tool_id=payload.toolId,
        )
        result = await run_in_threadpool(
            chat_agent,
            store,
            agent_id,
            payload.message,
            tool_input,
            tool_id,
            [item.model_dump() for item in payload.history],
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except AgentRuntimeUnavailable as error:
        await run_in_threadpool(
            record_request, store, agent_id, "POST", "/api/v1/agents/chat",
            409, started, request_id, "agent-playground",
        )
        raise HTTPException(status_code=409, detail=str(error)) from error
    await run_in_threadpool(
        record_request, store, agent_id, "POST", "/api/v1/agents/chat",
        200, started, request_id, "agent-playground",
    )
    return result


@router.post("/api/v1/agents/{agent_id}/chat/stream")
async def stream_agent_chat(
    agent_id: str,
    payload: AgentChatRequest,
    request: Request,
):
    store = account_store(request)
    try:
        await run_in_threadpool(store.latest_build, agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    history = [item.model_dump() for item in payload.history]
    definition = await run_in_threadpool(store.latest_build, agent_id)
    try:
        tool_input, tool_id, _ = await run_in_threadpool(
            resolve_agent_tool_input,
            store,
            definition,
            payload.input,
            legacy_input=payload.toolInput,
            legacy_tool_id=payload.toolId,
        )
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return agent_chat_stream(
        lambda on_delta: chat_agent(
            store,
            agent_id,
            payload.message,
            tool_input,
            tool_id,
            history,
            on_delta,
        )
    )


@router.post("/api/v1/agents/{agent_id}/llm/prepare", status_code=202)
async def prepare_llm(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        account = require_account(request)
        store = AgentStore(agent_data_root(account.workspace_root.parent / ".local-data"))
        result = await run_in_threadpool(start_agent_llm_preparation, store, agent_id)
        runtime_path = result.get("localPath")
        if runtime_path:
            try:
                relative = Path(str(runtime_path)).resolve().relative_to(account.workspace_root.parent)
                result = {
                    **result,
                    "localPath": str(account.display_workspace_root.parent / relative),
                }
            except ValueError:
                pass
        return result
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/v1/agents/{agent_id}/llm/prepare")
async def read_llm_preparation(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        account = require_account(request)
        store = AgentStore(agent_data_root(account.workspace_root.parent / ".local-data"))
        result = await run_in_threadpool(read_agent_llm_preparation, store, agent_id)
        runtime_path = result.get("localPath")
        if runtime_path:
            try:
                relative = Path(str(runtime_path)).resolve().relative_to(account.workspace_root.parent)
                result = {**result, "localPath": str(account.display_workspace_root.parent / relative)}
            except ValueError:
                pass
        return result
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Base LLM preparation has not started.") from error
    except AgentRuntimeUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/v1/agents/{agent_id}/serving")
async def get_serving(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        return await run_in_threadpool(read_serving, account_store(request), agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error


@router.post("/api/v1/agents/{agent_id}/serving", status_code=201)
async def start_serving(
    agent_id: str,
    payload: AgentServingRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            enable_serving,
            account_store(request),
            agent_id,
            payload.port,
            Path(WORKSPACE_DIR),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/api/v1/agents/{agent_id}/serving")
async def stop_serving(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        return await run_in_threadpool(disable_serving, account_store(request), agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error


@router.put("/api/v1/agents/{agent_id}/serving")
async def change_serving_port(
    agent_id: str,
    payload: AgentServingRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            update_serving_port,
            account_store(request),
            agent_id,
            payload.port,
            Path(WORKSPACE_DIR),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/v1/agents/{agent_id}/serving/token", status_code=201)
async def rotate_token(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        return await run_in_threadpool(rotate_serving_token, account_store(request), agent_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/v1/agents/{agent_id}/serving/data-sources")
async def read_serving_data_sources(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        items = await run_in_threadpool(list_serving_data_sources, account_store(request), agent_id)
        return {"items": items, "source": "account-local-serving-data-sources"}
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error


@router.put("/api/v1/agents/{agent_id}/serving/tools/{tool_id}")
async def configure_direct_tool_serving(
    agent_id: str,
    tool_id: str,
    payload: AgentDirectToolServingRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            set_direct_tool_serving,
            account_store(request),
            agent_id,
            tool_id,
            payload.enabled,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error
    except (AgentRuntimeUnavailable, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/api/v1/agents/{agent_id}/serving/data-sources")
async def save_serving_data_source(
    agent_id: str,
    payload: AgentServingDataSourceRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            upsert_serving_data_source,
            account_store(request),
            agent_id,
            tool_id=payload.toolId,
            name=payload.name,
            data_path=payload.dataPath,
            sample_index=payload.sampleIndex,
            selection_mode=payload.selectionMode,
            enabled_for_serving=payload.enabledForServing,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Built Agent or Tool AI not found.") from error
    except (AgentRuntimeUnavailable, FileNotFoundError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/api/v1/agents/{agent_id}/serving/data-sources/{source_id}")
async def remove_serving_data_source(
    agent_id: str,
    source_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            delete_serving_data_source,
            account_store(request),
            agent_id,
            source_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Serving Data Source not found.") from error


@router.post("/api/v1/agents/{agent_id}/serving/data-sources/{source_id}/test")
async def test_serving_data_source(
    agent_id: str,
    source_id: str,
    request: Request,
    payload: AgentDataSourceTestRequest | None = None,
) -> dict[str, Any]:
    store = account_store(request)
    try:
        sources = await run_in_threadpool(list_serving_data_sources, store, agent_id)
        source = next(item for item in sources if item.get("sourceId") == source_id)
        input_source: dict[str, Any] = {"type": "data-source", "sourceId": source_id}
        if payload and payload.sampleIndex is not None:
            input_source["sampleIndex"] = payload.sampleIndex
        return await run_in_threadpool(
            predict_agent_tool,
            store,
            agent_id,
            str(source["toolId"]),
            input_source,
        )
    except (KeyError, StopIteration) as error:
        raise HTTPException(status_code=404, detail="Serving Data Source not found.") from error
    except (AgentRuntimeUnavailable, FileNotFoundError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/v1/agents/{agent_id}/requests")
async def read_requests(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        return {
            "items": await run_in_threadpool(list_requests, account_store(request), agent_id),
            "source": "bounded-local-agent-request-log",
        }
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Built Agent not found.") from error


@router.get("/serve/v1/agents/{agent_id}/health")
async def serving_health(
    agent_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store = await served_store(agent_id, authorization)
    result = await run_in_threadpool(agent_health, store, agent_id)
    await run_in_threadpool(
        record_request, store, agent_id, "GET", f"/serve/v1/agents/{agent_id}/health",
        200, started, request_id, "serving-api",
    )
    return result


@router.get("/serve/v1/agents/{agent_id}/info")
async def serving_info(
    agent_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store = await served_store(agent_id, authorization)
    result = await run_in_threadpool(agent_info, store, agent_id)
    await run_in_threadpool(
        record_request, store, agent_id, "GET", f"/serve/v1/agents/{agent_id}/info",
        200, started, request_id, "serving-api",
    )
    return result


@router.post("/serve/v1/agents/{agent_id}/chat")
async def serving_chat(
    agent_id: str,
    payload: AgentChatRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store = await served_store(agent_id, authorization)
    try:
        definition = await run_in_threadpool(store.latest_build, agent_id)
        tool_input, tool_id, input_source = await run_in_threadpool(
            resolve_agent_tool_input,
            store,
            definition,
            payload.input,
            legacy_input=payload.toolInput,
            legacy_tool_id=payload.toolId,
            require_serving_allowed=True,
        )
        result = await run_in_threadpool(
            chat_agent,
            store,
            agent_id,
            payload.message,
            tool_input,
            tool_id,
            [item.model_dump() for item in payload.history],
        )
        result = serving_safe_chat_result(result, input_source)
    except AgentRuntimeUnavailable as error:
        await run_in_threadpool(
            record_request, store, agent_id, "POST", f"/serve/v1/agents/{agent_id}/chat",
            409, started, request_id, "serving-api",
        )
        raise HTTPException(status_code=409, detail=str(error)) from error
    await run_in_threadpool(
        record_request, store, agent_id, "POST", f"/serve/v1/agents/{agent_id}/chat",
        200, started, request_id, "serving-api",
    )
    return result


@router.post("/serve/v1/agents/{agent_id}/tools/{tool_id}/invoke")
@router.post("/serve/v1/agents/{agent_id}/tools/{tool_id}/predict", deprecated=True)
async def serving_tool_invoke(
    agent_id: str,
    tool_id: str,
    payload: AgentToolPredictionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store = await served_store(agent_id, authorization)
    path = f"/serve/v1/agents/{agent_id}/tools/{tool_id}/invoke"
    try:
        result = await run_in_threadpool(
            predict_agent_tool,
            store,
            agent_id,
            tool_id,
            payload.input,
            require_serving_allowed=True,
            require_direct_enabled=True,
        )
    except (AgentRuntimeUnavailable, FileNotFoundError, RuntimeError, ValueError) as error:
        await run_in_threadpool(record_request, store, agent_id, "POST", path, 409, started, request_id, "serving-api")
        raise HTTPException(status_code=409, detail=str(error)) from error
    await run_in_threadpool(record_request, store, agent_id, "POST", path, 200, started, request_id, "serving-api")
    return result


@router.get("/health")
async def dedicated_serving_health(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store, agent_id = await dedicated_served_store(request, authorization)
    result = await run_in_threadpool(agent_health, store, agent_id)
    await run_in_threadpool(
        record_request, store, agent_id, "GET", "/health",
        200, started, request_id, "dedicated-serving-api",
    )
    return result


@router.get("/info")
async def dedicated_serving_info(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store, agent_id = await dedicated_served_store(request, authorization)
    result = await run_in_threadpool(agent_info, store, agent_id)
    await run_in_threadpool(
        record_request, store, agent_id, "GET", "/info",
        200, started, request_id, "dedicated-serving-api",
    )
    return result


@router.post("/chat")
async def dedicated_serving_chat(
    payload: AgentChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store, agent_id = await dedicated_served_store(request, authorization)
    try:
        definition = await run_in_threadpool(store.latest_build, agent_id)
        tool_input, tool_id, input_source = await run_in_threadpool(
            resolve_agent_tool_input,
            store,
            definition,
            payload.input,
            legacy_input=payload.toolInput,
            legacy_tool_id=payload.toolId,
            require_serving_allowed=True,
        )
        result = await run_in_threadpool(
            chat_agent,
            store,
            agent_id,
            payload.message,
            tool_input,
            tool_id,
            [item.model_dump() for item in payload.history],
        )
        result = serving_safe_chat_result(result, input_source)
    except AgentRuntimeUnavailable as error:
        await run_in_threadpool(
            record_request, store, agent_id, "POST", "/chat",
            409, started, request_id, "dedicated-serving-api",
        )
        raise HTTPException(status_code=409, detail=str(error)) from error
    await run_in_threadpool(
        record_request, store, agent_id, "POST", "/chat",
        200, started, request_id, "dedicated-serving-api",
    )
    return result


@router.post("/tools/{tool_id}/invoke")
@router.post("/tools/{tool_id}/predict", deprecated=True)
async def dedicated_serving_tool_invoke(
    tool_id: str,
    payload: AgentToolPredictionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    store, agent_id = await dedicated_served_store(request, authorization)
    path = f"/tools/{tool_id}/invoke"
    try:
        result = await run_in_threadpool(
            predict_agent_tool,
            store,
            agent_id,
            tool_id,
            payload.input,
            require_serving_allowed=True,
            require_direct_enabled=True,
        )
    except (AgentRuntimeUnavailable, FileNotFoundError, RuntimeError, ValueError) as error:
        await run_in_threadpool(record_request, store, agent_id, "POST", path, 409, started, request_id, "dedicated-serving-api")
        raise HTTPException(status_code=409, detail=str(error)) from error
    await run_in_threadpool(record_request, store, agent_id, "POST", path, 200, started, request_id, "dedicated-serving-api")
    return result


__all__ = ["router"]
