"""Built Agent lifecycle, Tool AI smoke execution, and serving credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import time
import uuid
import re
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .agent_builder import (
    AGENT_ID,
    STORE_LOCK,
    AgentStore,
    resolve_local_model_source,
)
from .model_runner import (
    ModelRunnerUnavailable,
    local_huggingface_chat,
    local_huggingface_status,
    prepare_local_huggingface_model,
    read_local_huggingface_preparation,
    start_local_huggingface_preparation,
)
from .folder import prepare_project_data_directory, task_data_inventory
from .task_data import build_task_data_sample, resolve_task_data_selection
from .workspace import find_project

MAX_REQUESTS = 200
AGENT_SERVING_PORT_MIN = int(os.getenv("STUDIO_AGENT_PORT_MIN", "24400"))
AGENT_SERVING_PORT_MAX = int(os.getenv("STUDIO_AGENT_PORT_MAX", "24499"))
TOOL_RESULT_MARKER = "__FEDOPS_TOOL_RESULT__="
LLM_RESULT_MARKER = "__FEDOPS_LLM_RESULT__="


class AgentRuntimeUnavailable(RuntimeError):
    """Raised when a Built Agent cannot truthfully perform full inference."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _serving_public(agent_id: str, state: object) -> dict[str, Any]:
    value = state if isinstance(state, dict) else {}
    raw_port = value.get("port")
    port = int(raw_port) if isinstance(raw_port, int) else None
    endpoint_base = f"/serve/v1/agents/{agent_id}"
    studio_port = int(os.getenv("STUDIO_PORT", "24368"))
    return {
        "agentId": agent_id,
        "enabled": bool(value.get("enabled")),
        "endpointBase": endpoint_base,
        "endpointUrl": (
            f"http://localhost:{port}" if port is not None
            else f"http://localhost:{studio_port}{endpoint_base}"
        ),
        "port": port,
        "tokenCreatedAt": value.get("tokenCreatedAt"),
        "requestCount": int(value.get("requestCount") or 0),
        "dataSourceCount": len(value.get("dataSources") or []) if isinstance(value.get("dataSources"), list) else 0,
        "directToolIds": [
            str(tool_id)
            for tool_id in (value.get("directToolIds") or [])
            if isinstance(tool_id, str)
        ] if isinstance(value.get("directToolIds"), list) else [],
    }


def read_serving(store: AgentStore, agent_id: str) -> dict[str, Any]:
    store.latest_build(agent_id)
    return _serving_public(agent_id, store.load()["serving"].get(agent_id))


def _validate_serving_port(port: int) -> int:
    if not AGENT_SERVING_PORT_MIN <= port <= AGENT_SERVING_PORT_MAX:
        raise ValueError(
            f"Agent serving port must be between {AGENT_SERVING_PORT_MIN} "
            f"and {AGENT_SERVING_PORT_MAX}."
        )
    return port


def _ensure_serving_port_available(
    workspace_root: Path,
    port: int,
    store: AgentStore,
    agent_id: str,
) -> None:
    try:
        existing_store, existing_agent_id = find_served_agent_store_by_port(
            workspace_root,
            port,
        )
    except KeyError:
        return
    if existing_store.path != store.path or existing_agent_id != agent_id:
        raise RuntimeError(f"Port {port} is already assigned to another Agent.")


def enable_serving(
    store: AgentStore,
    agent_id: str,
    port: int,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    store.latest_build(agent_id)
    selected_port = _validate_serving_port(port)
    if workspace_root is not None:
        _ensure_serving_port_available(workspace_root, selected_port, store, agent_id)
    with STORE_LOCK:
        document = store.load()
        current = document["serving"].get(agent_id)
        if isinstance(current, dict) and current.get("enabled"):
            raise RuntimeError("This Agent Serving API is already enabled. Rotate its token if needed.")
        token = f"fas_{secrets.token_urlsafe(32)}"
        state = {
            "enabled": True,
            "tokenHash": _token_hash(token),
            "tokenCreatedAt": _now(),
            "port": selected_port,
            "requestCount": int(current.get("requestCount") or 0) if isinstance(current, dict) else 0,
            "dataSources": list(current.get("dataSources") or []) if isinstance(current, dict) else [],
            "directToolIds": list(current.get("directToolIds") or []) if isinstance(current, dict) else [],
        }
        document["serving"][agent_id] = state
        store.save(document)
        return {"serving": _serving_public(agent_id, state), "token": token}


def update_serving_port(
    store: AgentStore,
    agent_id: str,
    port: int,
    workspace_root: Path,
) -> dict[str, Any]:
    """Atomically move an enabled Agent listener while preserving its token."""
    store.latest_build(agent_id)
    selected_port = _validate_serving_port(port)
    with STORE_LOCK:
        _ensure_serving_port_available(workspace_root, selected_port, store, agent_id)
        document = store.load()
        state = document["serving"].get(agent_id)
        if not isinstance(state, dict) or not state.get("enabled"):
            raise RuntimeError("Enable this Agent Serving API before changing its port.")
        state = {**state, "port": selected_port}
        document["serving"][agent_id] = state
        store.save(document)
        return _serving_public(agent_id, state)


def rotate_serving_token(store: AgentStore, agent_id: str) -> dict[str, Any]:
    store.latest_build(agent_id)
    with STORE_LOCK:
        document = store.load()
        state = document["serving"].get(agent_id)
        if not isinstance(state, dict) or not state.get("enabled"):
            raise RuntimeError("Enable this Agent Serving API before rotating its token.")
        token = f"fas_{secrets.token_urlsafe(32)}"
        state = {**state, "tokenHash": _token_hash(token), "tokenCreatedAt": _now()}
        document["serving"][agent_id] = state
        store.save(document)
        return {"serving": _serving_public(agent_id, state), "token": token}


def disable_serving(store: AgentStore, agent_id: str) -> dict[str, Any]:
    store.latest_build(agent_id)
    with STORE_LOCK:
        document = store.load()
        current = document["serving"].get(agent_id)
        state = {
            "enabled": False,
            "tokenHash": None,
            "tokenCreatedAt": None,
            "port": current.get("port") if isinstance(current, dict) else None,
            "requestCount": int(current.get("requestCount") or 0) if isinstance(current, dict) else 0,
            "dataSources": list(current.get("dataSources") or []) if isinstance(current, dict) else [],
            "directToolIds": list(current.get("directToolIds") or []) if isinstance(current, dict) else [],
        }
        document["serving"][agent_id] = state
        store.save(document)
        return _serving_public(agent_id, state)


def authorize_serving(store: AgentStore, agent_id: str, token: str) -> dict[str, Any]:
    if not AGENT_ID.fullmatch(agent_id):
        raise PermissionError("The Agent serving identity is invalid.")
    state = store.load()["serving"].get(agent_id)
    if not isinstance(state, dict) or not state.get("enabled"):
        raise PermissionError("This Agent Serving API is disabled.")
    expected = str(state.get("tokenHash") or "")
    actual = _token_hash(token) if token else ""
    if not expected or not hmac.compare_digest(expected, actual):
        raise PermissionError("The Agent bearer token is invalid.")
    return state


def set_direct_tool_serving(
    store: AgentStore,
    agent_id: str,
    tool_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Opt one Tool AI into the advanced direct invocation surface."""
    build = store.latest_build(agent_id)
    _tool_definition(build, tool_id)
    with STORE_LOCK:
        document = store.load()
        state = document["serving"].get(agent_id)
        if not isinstance(state, dict):
            state = {
                "enabled": False,
                "requestCount": 0,
                "dataSources": [],
                "directToolIds": [],
            }
        current = {
            str(value)
            for value in (state.get("directToolIds") or [])
            if isinstance(value, str)
        }
        if enabled:
            current.add(tool_id)
        else:
            current.discard(tool_id)
        state["directToolIds"] = sorted(current)
        document["serving"][agent_id] = state
        store.save(document)
        return _serving_public(agent_id, state)


def require_direct_tool_serving(store: AgentStore, agent_id: str, tool_id: str) -> None:
    """Reject direct Tool calls unless the owner explicitly exposed that Tool."""
    state = store.load()["serving"].get(agent_id)
    enabled = state.get("directToolIds") if isinstance(state, dict) else []
    if tool_id not in enabled:
        raise AgentRuntimeUnavailable(
            "Direct Tool API is disabled for this Tool AI. Enable it in Serving API > Advanced."
        )


def find_served_agent_store(workspace_root: Path, agent_id: str) -> AgentStore:
    """Locate a served Agent without exposing or accepting an account identity."""
    if not AGENT_ID.fullmatch(agent_id):
        raise KeyError(agent_id)
    accounts = workspace_root.expanduser().resolve() / "accounts"
    if not accounts.is_dir():
        raise KeyError(agent_id)
    matches: list[AgentStore] = []
    for account in accounts.iterdir():
        if not account.is_dir() or not account.name.startswith("account-"):
            continue
        candidate = AgentStore(account / ".local-data" / "agents")
        state = candidate.load()["serving"].get(agent_id)
        if isinstance(state, dict) and state.get("enabled"):
            matches.append(candidate)
    if len(matches) != 1:
        raise KeyError(agent_id)
    return matches[0]


def find_served_agent_store_by_port(
    workspace_root: Path,
    port: int,
) -> tuple[AgentStore, str]:
    """Locate the one enabled Agent assigned to a dedicated local port."""
    selected_port = _validate_serving_port(port)
    accounts = workspace_root.expanduser().resolve() / "accounts"
    if not accounts.is_dir():
        raise KeyError(selected_port)
    matches: list[tuple[AgentStore, str]] = []
    for account in accounts.iterdir():
        if not account.is_dir() or not account.name.startswith("account-"):
            continue
        candidate = AgentStore(account / ".local-data" / "agents")
        for agent_id, state in candidate.load()["serving"].items():
            if (
                isinstance(state, dict)
                and state.get("enabled")
                and state.get("port") == selected_port
            ):
                matches.append((candidate, str(agent_id)))
    if len(matches) != 1:
        raise KeyError(selected_port)
    return matches[0]


def _project_model_path(project_root: Path, reference: dict[str, Any] | None = None) -> Path:
    selected = str((reference or {}).get("modelArtifactPath") or "").strip()
    if selected:
        path = (project_root / selected).resolve()
        try:
            path.relative_to((project_root / ".fedops-studio" / "model-versions").resolve())
        except ValueError as error:
            raise AgentRuntimeUnavailable("The selected Global Model path is invalid.") from error
        if not path.is_file() or path.is_symlink():
            raise AgentRuntimeUnavailable("The selected Global Model is not cached on this device.")
        return path
    manifest_path = project_root / "model_release" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentRuntimeUnavailable("The local model release manifest is invalid.") from error
    artifact = str(manifest.get("artifact") or "model.safetensors")
    path = (project_root / "model_release" / artifact).resolve()
    try:
        path.relative_to((project_root / "model_release").resolve())
    except ValueError as error:
        raise AgentRuntimeUnavailable("The local model artifact path is invalid.") from error
    if not path.is_file():
        raise AgentRuntimeUnavailable("The local model artifact is missing.")
    return path


def _verified_project(store: AgentStore, reference: dict[str, Any]) -> tuple[Path, Path]:
    try:
        resolve_local_model_source(store.workspace_root, reference)
        _, project_root = find_project(store.workspace_root, str(reference["localProjectId"]))
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        raise AgentRuntimeUnavailable(str(error)) from error
    return project_root, _project_model_path(project_root, reference)


def _llm_runtime_status(store: AgentStore, llm: dict[str, Any]) -> dict[str, Any]:
    if llm.get("source") == "huggingface":
        return local_huggingface_status(
            store.models_root,
            str(llm.get("repoId") or ""),
            str(llm.get("revision") or ""),
            str(llm.get("fileName") or "") or None,
        )
    if llm.get("source") == "federated-task":
        try:
            _, model = _verified_project(store, llm)
        except AgentRuntimeUnavailable as error:
            return {
                "status": "error",
                "provider": "federated-task-workspace",
                "model": str(llm.get("modelName") or "Federated Task LLM"),
                "localPath": str(llm.get("localPath") or ""),
                "detail": str(error),
            }
        return {
            "status": "installed",
            "provider": "federated-task-workspace",
            "model": str(llm.get("modelName") or model.name),
            "localPath": str(llm.get("localPath") or model),
            "detail": "The LLM Federated Task code and model are available in the local Workspace.",
        }
    return {
        "status": "error",
        "provider": "unselected",
        "model": "",
        "localPath": "",
        "detail": "This Agent build has no supported Base LLM.",
    }


def agent_health(store: AgentStore, agent_id: str) -> dict[str, Any]:
    build = store.latest_build(agent_id)
    llm = build.get("llm") if isinstance(build.get("llm"), dict) else {}
    runtime = _llm_runtime_status(store, llm)
    runtime_status = str(runtime["status"])
    return {
        "status": "ready" if runtime_status in {"installed", "ready"} else "degraded",
        "agentId": agent_id,
        "buildRevision": build["buildRevision"],
        "llmRuntimeStatus": runtime_status,
        "toolCount": len(build.get("tools") or []),
        "detail": (
            "Agent inference runtime is ready."
            if runtime_status == "ready"
            else str(runtime["detail"])
        ),
        "provider": runtime["provider"],
        "model": runtime["model"],
    }


def agent_info(store: AgentStore, agent_id: str) -> dict[str, Any]:
    build = store.latest_build(agent_id)
    llm = build.get("llm") if isinstance(build.get("llm"), dict) else {}
    runtime = _llm_runtime_status(store, llm)
    return {
        "schemaVersion": 1,
        "agentId": agent_id,
        "name": build["name"],
        "description": build["description"],
        "buildRevision": build["buildRevision"],
        "builtAt": build["builtAt"],
        "llm": {**build["llm"], "runtimeStatus": runtime["status"], "runtime": runtime},
        "tools": [
            {
                key: tool.get(key)
                for key in (
                    "localProjectId", "projectName", "taskId", "registryId", "taskTitle",
                    "releaseId", "modelVersionId", "modelName", "modelVersion", "format",
                    "sourceFingerprint", "modelSha256", "localPath", "modelArtifactPath",
                    "toolManifest",
                )
            }
            for tool in build.get("tools", [])
        ],
        "harness": build["harness"],
        "servingDataSources": [
            {
                key: source.get(key)
                for key in (
                    "sourceId", "toolId", "name", "dataPath", "sampleIndex",
                    "selectionMode", "enabledForServing", "updatedAt",
                )
            }
            for source in list_serving_data_sources(store, agent_id)
            if source.get("enabledForServing")
        ],
    }


def _tool_definition(definition: dict[str, Any], tool_id: str) -> dict[str, Any]:
    tools = definition.get("tools") if isinstance(definition.get("tools"), list) else []
    tool = next(
        (candidate for candidate in tools if candidate.get("localProjectId") == tool_id),
        None,
    )
    if not isinstance(tool, dict):
        raise AgentRuntimeUnavailable("The selected Tool AI is not part of this Agent.")
    return tool


def _tool_data_binding(store: AgentStore, tool: dict[str, Any]) -> tuple[Path, Path, dict[str, object]]:
    try:
        _, project = find_project(store.workspace_root, str(tool["localProjectId"]))
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        raise AgentRuntimeUnavailable("The Tool AI Workspace is unavailable.") from error
    binding = prepare_project_data_directory(
        store.account_root / ".local-data",
        project.name,
        local_project_id=str(tool["localProjectId"]),
    )
    return project, Path(str(binding["containerPath"])), binding


def _data_source_id(agent_id: str, tool_id: str) -> str:
    value = hashlib.sha256(f"{agent_id}\0{tool_id}".encode("utf-8")).hexdigest()[:16]
    return f"data-source-{value}"


def list_serving_data_sources(store: AgentStore, agent_id: str) -> list[dict[str, Any]]:
    build = store.latest_build(agent_id)
    state = store.load()["serving"].get(agent_id)
    sources = state.get("dataSources") if isinstance(state, dict) else []
    result: list[dict[str, Any]] = []
    for raw in sources if isinstance(sources, list) else []:
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        try:
            tool = _tool_definition(build, str(source.get("toolId") or ""))
            _, data_root, binding = _tool_data_binding(store, tool)
            selected, _ = resolve_task_data_selection(data_root, str(source.get("dataPath") or ""))
            available = selected.exists()
            inventory = task_data_inventory(data_root)
        except (AgentRuntimeUnavailable, FileNotFoundError, ValueError):
            available = False
            binding = {"fileCount": 0, "hasEntries": False}
            inventory = binding
        result.append({
            **source,
            "available": available,
            "fileCount": int(inventory.get("fileCount") or 0),
            "hasEntries": bool(inventory.get("hasEntries")),
        })
    return result


def upsert_serving_data_source(
    store: AgentStore,
    agent_id: str,
    *,
    tool_id: str,
    name: str,
    data_path: str,
    sample_index: int,
    selection_mode: str,
    enabled_for_serving: bool,
) -> dict[str, Any]:
    if sample_index < 0:
        raise ValueError("Sample index must be zero or greater.")
    if selection_mode not in {"fixed", "request"}:
        raise ValueError("Data Source selection mode must be fixed or request.")
    build = store.latest_build(agent_id)
    tool = _tool_definition(build, tool_id)
    _, data_root, _ = _tool_data_binding(store, tool)
    _, normalized_path = resolve_task_data_selection(data_root, data_path)
    source_id = _data_source_id(agent_id, tool_id)
    source = {
        "sourceId": source_id,
        "toolId": tool_id,
        "name": name.strip() or str(tool.get("modelName") or "Tool AI Data"),
        "dataPath": normalized_path,
        "sampleIndex": sample_index,
        "selectionMode": selection_mode,
        "enabledForServing": bool(enabled_for_serving),
        "updatedAt": _now(),
    }
    with STORE_LOCK:
        document = store.load()
        state = document["serving"].get(agent_id)
        if not isinstance(state, dict):
            state = {"enabled": False, "requestCount": 0, "dataSources": []}
        current = state.get("dataSources") if isinstance(state.get("dataSources"), list) else []
        state["dataSources"] = [
            *(item for item in current if isinstance(item, dict) and item.get("sourceId") != source_id),
            source,
        ]
        document["serving"][agent_id] = state
        store.save(document)
    return next(item for item in list_serving_data_sources(store, agent_id) if item["sourceId"] == source_id)


def delete_serving_data_source(store: AgentStore, agent_id: str, source_id: str) -> dict[str, Any]:
    store.latest_build(agent_id)
    with STORE_LOCK:
        document = store.load()
        state = document["serving"].get(agent_id)
        if not isinstance(state, dict):
            raise KeyError(source_id)
        sources = state.get("dataSources") if isinstance(state.get("dataSources"), list) else []
        remaining = [item for item in sources if not isinstance(item, dict) or item.get("sourceId") != source_id]
        if len(remaining) == len(sources):
            raise KeyError(source_id)
        state["dataSources"] = remaining
        store.save(document)
    return {"deleted": True, "sourceId": source_id}


def _manifest_input_schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    manifest = tool.get("toolManifest") if isinstance(tool.get("toolManifest"), dict) else {}
    input_contract = manifest.get("input") if isinstance(manifest.get("input"), dict) else {}
    schema = input_contract.get("jsonSchema")
    return schema if isinstance(schema, dict) else None


def _manifest_output_schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    manifest = tool.get("toolManifest") if isinstance(tool.get("toolManifest"), dict) else {}
    output_contract = manifest.get("output") if isinstance(manifest.get("output"), dict) else {}
    schema = output_contract.get("jsonSchema")
    return schema if isinstance(schema, dict) else None


def _validate_json_schema(value: Any, schema: dict[str, Any], path: str = "input") -> None:
    """Validate the JSON Schema subset used by FedOps Tool manifests."""
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected, str) and expected in matches and not matches[expected]:
        raise AgentRuntimeUnavailable(f"{path} must be a JSON {expected}.")
    if isinstance(value, dict):
        minimum_properties = schema.get("minProperties")
        if isinstance(minimum_properties, int) and len(value) < minimum_properties:
            raise AgentRuntimeUnavailable(f"{path} must contain at least {minimum_properties} field(s).")
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        missing = [str(key) for key in required if key not in value]
        if missing:
            raise AgentRuntimeUnavailable(f"{path} is missing required field(s): {', '.join(missing)}.")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if schema.get("additionalProperties") is False:
            unknown = [str(key) for key in value if key not in properties]
            if unknown:
                raise AgentRuntimeUnavailable(f"{path} has unsupported field(s): {', '.join(unknown)}.")
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                _validate_json_schema(value[key], child, f"{path}.{key}")
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            for key, item in value.items():
                if key not in properties:
                    _validate_json_schema(item, additional, f"{path}.{key}")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise AgentRuntimeUnavailable(f"{path} must contain at least {minimum} item(s).")
        if isinstance(maximum, int) and len(value) > maximum:
            raise AgentRuntimeUnavailable(f"{path} must contain no more than {maximum} item(s).")
        child = schema.get("items")
        if isinstance(child, dict):
            for index, item in enumerate(value):
                _validate_json_schema(item, child, f"{path}[{index}]")


def _validate_tool_input(tool: dict[str, Any], payload: dict[str, Any]) -> None:
    schema = _manifest_input_schema(tool)
    if schema:
        _validate_json_schema(payload, schema)


def resolve_agent_tool_input(
    store: AgentStore,
    definition: dict[str, Any],
    input_source: dict[str, Any] | None,
    *,
    legacy_input: dict[str, Any] | None = None,
    legacy_tool_id: str | None = None,
    require_serving_allowed: bool = False,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    if input_source is None:
        return legacy_input, legacy_tool_id, ({"type": "inline"} if legacy_input is not None else None)
    kind = str(input_source.get("type") or "")
    if kind == "inline":
        payload = input_source.get("payload")
        if not isinstance(payload, dict):
            raise AgentRuntimeUnavailable("Inline Tool input must contain a JSON object payload.")
        tool_id = str(input_source.get("toolId") or legacy_tool_id or "") or None
        return payload, tool_id, {"type": "inline", "toolId": tool_id}
    if kind != "data-source":
        raise AgentRuntimeUnavailable("Tool input type must be inline or data-source.")
    source_id = str(input_source.get("sourceId") or "")
    source = next(
        (item for item in list_serving_data_sources(store, str(definition.get("agentId") or "")) if item.get("sourceId") == source_id),
        None,
    )
    if not isinstance(source, dict):
        raise AgentRuntimeUnavailable("The selected Serving Data Source was not found.")
    if require_serving_allowed and not source.get("enabledForServing"):
        raise AgentRuntimeUnavailable("This local Data Source is blocked for Serving API requests.")
    tool_id = str(source["toolId"])
    tool = _tool_definition(definition, tool_id)
    project, data_root, _ = _tool_data_binding(store, tool)
    requested_index = input_source.get("sampleIndex")
    if requested_index is not None and source.get("selectionMode") != "request":
        raise AgentRuntimeUnavailable("This Data Source uses a fixed sample index.")
    sample_index = int(requested_index if requested_index is not None else source.get("sampleIndex") or 0)
    sample = build_task_data_sample(
        project,
        tool_id,
        data_root,
        index=sample_index,
        data_path=str(source.get("dataPath") or ""),
    )
    return sample["payload"], tool_id, {
        "type": "data-source",
        "sourceId": source_id,
        "toolId": tool_id,
        "sampleIndex": sample_index,
        "dataPath": source.get("dataPath") or "",
    }


def run_tool_smoke_definition(
    store: AgentStore,
    definition: dict[str, Any],
    tool_input: dict[str, Any] | None,
    tool_id: str | None = None,
) -> dict[str, Any]:
    """Run one identified Tool AI from a verified Draft or immutable Build."""
    tools = definition.get("tools") if isinstance(definition.get("tools"), list) else []
    if not tools:
        raise AgentRuntimeUnavailable("This Agent build has no Tool AI model.")
    tool = next(
        (candidate for candidate in tools if candidate.get("localProjectId") == tool_id),
        None,
    ) if tool_id else tools[0]
    if not isinstance(tool, dict):
        raise AgentRuntimeUnavailable("The selected Tool AI is not part of this Agent.")
    source, model = _verified_project(store, tool)
    if not (source / "pyproject.toml").is_file() or not model.is_file():
        raise AgentRuntimeUnavailable("The local Workspace Tool AI is incomplete.")
    uv = shutil.which("uv")
    if not uv:
        raise AgentRuntimeUnavailable("uv is required to prepare the Tool AI runtime.")
    if tool_input is not None:
        _validate_tool_input(tool, tool_input)
    payload = json.dumps(tool_input, ensure_ascii=False) if tool_input is not None else "null"
    runner = (
        "import json,sys\n"
        "from federated_task.tool_ai.tool import build_tool_smoke_payload,predict\n"
        "payload=json.loads(sys.argv[1])\n"
        "if payload is None: payload=build_tool_smoke_payload()\n"
        "result=predict(payload,sys.argv[2])\n"
        "print('" + TOOL_RESULT_MARKER + "'+json.dumps({'input':payload,'result':result},ensure_ascii=False))\n"
    )
    environment = os.environ.copy()
    environment.update({"UV_LINK_MODE": "copy", "PYTHONPATH": str(source)})
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [uv, "run", "--project", str(source), "--locked", "python", "-c", runner, payload, str(model)],
            cwd=source,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise AgentRuntimeUnavailable("Tool AI environment preparation exceeded 10 minutes.") from error
    if completed.returncode != 0:
        tail = completed.stdout[-3000:].strip()
        raise AgentRuntimeUnavailable(f"Tool AI smoke test failed.\n{tail}")
    marker = next(
        (line[len(TOOL_RESULT_MARKER):] for line in completed.stdout.splitlines() if line.startswith(TOOL_RESULT_MARKER)),
        None,
    )
    if marker is None:
        raise AgentRuntimeUnavailable("Tool AI smoke test returned no structured result.")
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as error:
        raise AgentRuntimeUnavailable("Tool AI smoke test returned invalid JSON.") from error
    output = result.get("result")
    output_schema = _manifest_output_schema(tool)
    if output_schema:
        _validate_json_schema(output, output_schema, "output")
    return {
        "tool": {
            "localProjectId": tool["localProjectId"],
            "taskId": tool.get("taskId"),
            "releaseId": tool.get("releaseId"),
            "modelVersionId": tool.get("modelVersionId"),
        },
        "input": result.get("input"),
        "result": output,
        "durationMs": round((time.monotonic() - started) * 1000),
        "environmentOutput": completed.stdout[:4000],
    }


def run_tool_smoke(store: AgentStore, agent_id: str, tool_input: dict[str, Any] | None, tool_id: str | None = None) -> dict[str, Any]:
    """Run the selected Tool AI from the latest immutable Build."""
    return run_tool_smoke_definition(store, store.latest_build(agent_id), tool_input, tool_id)


def predict_agent_tool(
    store: AgentStore,
    agent_id: str,
    tool_id: str,
    input_source: dict[str, Any],
    *,
    require_serving_allowed: bool = False,
    require_direct_enabled: bool = False,
) -> dict[str, Any]:
    definition = store.latest_build(agent_id)
    if require_direct_enabled:
        require_direct_tool_serving(store, agent_id, tool_id)
    payload, resolved_tool_id, source = resolve_agent_tool_input(
        store,
        definition,
        input_source,
        legacy_tool_id=tool_id,
        require_serving_allowed=require_serving_allowed,
    )
    if resolved_tool_id and resolved_tool_id != tool_id:
        raise AgentRuntimeUnavailable("The Data Source belongs to a different Tool AI.")
    result = run_tool_smoke_definition(store, definition, payload, tool_id)
    return {
        "tool": result["tool"],
        "result": result["result"],
        "durationMs": result["durationMs"],
        "inputSource": source or {"type": "inline", "toolId": tool_id},
    }


def _tool_search_text(tool: dict[str, Any]) -> str:
    manifest = tool.get("toolManifest") if isinstance(tool.get("toolManifest"), dict) else {}
    features = manifest.get("features") if isinstance(manifest.get("features"), list) else []
    return " ".join(
        str(value or "")
        for value in (
            tool.get("localProjectId"), tool.get("taskTitle"), tool.get("modelName"),
            manifest.get("description"), *features,
        )
    ).casefold()


def _route_tool(
    definition: dict[str, Any],
    message: str,
    tool_id: str | None,
) -> tuple[dict[str, Any] | None, str]:
    tools = definition.get("tools") if isinstance(definition.get("tools"), list) else []
    if not tools:
        return None, "no-tools"
    if tool_id:
        selected = next((tool for tool in tools if tool.get("localProjectId") == tool_id), None)
        if not isinstance(selected, dict):
            raise AgentRuntimeUnavailable("The selected Tool AI is not part of this Agent.")
        return selected, "task-data-selection"
    if len(tools) == 1:
        return tools[0], "single-tool"
    tokens = {
        token for token in re.findall(r"[\w-]+", message.casefold())
        if len(token) >= 3
    }
    ranked = sorted(
        ((sum(1 for token in tokens if token in _tool_search_text(tool)), tool) for tool in tools),
        key=lambda item: item[0],
        reverse=True,
    )
    if ranked and ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][1], "automatic-manifest-match"
    return None, "automatic-no-confident-match"


def test_agent_definition(
    store: AgentStore,
    definition: dict[str, Any],
    message: str,
    tool_input: dict[str, Any] | None,
    tool_id: str | None = None,
) -> dict[str, Any]:
    """Check a verified Draft or Build and optionally run its Tool AI."""
    agent_id = str(definition.get("agentId") or "")
    llm = definition.get("llm") if isinstance(definition.get("llm"), dict) else {}
    runtime = _llm_runtime_status(store, llm)
    trace: list[dict[str, Any]] = [{
        "stage": "definition-integrity",
        "status": "passed",
        "detail": "The Agent definition and referenced local model identities are valid.",
    }]
    tool_results: list[dict[str, Any]] = []
    if definition.get("tools"):
        selected_tools = (
            [tool_id]
            if tool_id
            else [str(tool.get("localProjectId")) for tool in definition.get("tools", [])]
        )
        for selected_tool_id in selected_tools:
            tool_results.append(run_tool_smoke_definition(
                store,
                definition,
                tool_input if tool_id else None,
                selected_tool_id,
            ))
        tool_result = tool_results[0] if tool_results else None
        trace.append({
            "stage": "tool-ai-smoke",
            "status": "passed",
            "detail": f"{len(tool_results)} local Tool AI inference contract(s) completed.",
            "durationMs": sum(int(result["durationMs"]) for result in tool_results),
        })
    else:
        tool_result = None
    runtime_ready = str(runtime.get("status")) in {"installed", "ready"}
    trace.append({
        "stage": "base-llm",
        "status": "passed" if runtime_ready else "pending",
        "detail": str(runtime.get("detail") or ""),
    })
    return {
        "requestId": f"request-{uuid.uuid4().hex[:16]}",
        "agentId": agent_id,
        "status": "succeeded",
        "response": (
            "Agent definition integrity passed. "
            + ("Tool AI smoke inference completed. " if tool_result else "No Tool AI is configured. ")
            + ("The Base LLM is locally available." if runtime_ready
               else "Prepare the selected Base LLM before Agent chat.")
        ),
        "toolResult": tool_result,
        "toolResults": tool_results,
        "trace": trace,
        "createdAt": _now(),
    }


def test_agent(
    store: AgentStore,
    agent_id: str,
    message: str,
    tool_input: dict[str, Any] | None,
    tool_id: str | None = None,
) -> dict[str, Any]:
    """Run local integrity checks and an optional Tool AI smoke inference."""
    request_id = f"request-{uuid.uuid4().hex[:16]}"
    started = time.monotonic()
    try:
        build = store.latest_build(agent_id)
        result = test_agent_definition(store, build, message, tool_input, tool_id)
        record_request(store, agent_id, "POST", "/test", 200, started, request_id, "smoke-test")
        return result
    except AgentRuntimeUnavailable:
        record_request(store, agent_id, "POST", "/test", 409, started, request_id, "smoke-test")
        raise


def _federated_task_llm_chat(
    store: AgentStore,
    llm: dict[str, Any],
    messages: list[dict[str, str]],
    harness: dict[str, Any],
) -> dict[str, Any]:
    project, model = _verified_project(store, llm)
    uv = shutil.which("uv")
    if not uv:
        raise AgentRuntimeUnavailable("uv is required to execute a Federated Task LLM.")
    runner = (
        "import json,sys\n"
        "from federated_task.llm.runtime import chat\n"
        "result=chat(json.loads(sys.argv[1]),sys.argv[2],json.loads(sys.argv[3]))\n"
        "if isinstance(result,str): result={'content':result}\n"
        "print('" + LLM_RESULT_MARKER + "'+json.dumps(result,ensure_ascii=False))\n"
    )
    environment = os.environ.copy()
    environment.update({"UV_LINK_MODE": "copy", "PYTHONPATH": str(project)})
    completed = subprocess.run(
        [
            uv, "run", "--project", str(project), "--locked", "python", "-c", runner,
            json.dumps(messages, ensure_ascii=False), str(model), json.dumps(harness, ensure_ascii=False),
        ],
        cwd=project,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60 * 60,
        check=False,
    )
    if completed.returncode != 0:
        raise AgentRuntimeUnavailable(
            "Federated Task LLM inference failed.\n" + completed.stdout[-4000:].strip()
        )
    encoded = next(
        (line[len(LLM_RESULT_MARKER):] for line in completed.stdout.splitlines() if line.startswith(LLM_RESULT_MARKER)),
        None,
    )
    if not encoded:
        raise AgentRuntimeUnavailable("The Federated Task LLM returned no structured response.")
    result = json.loads(encoded)
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise AgentRuntimeUnavailable("The Federated Task LLM returned no text response.")
    return {"content": content, "usage": result.get("usage") or {}, "model": llm.get("modelName")}


def _normalized_history(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in (history or [])[-40:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if content:
            normalized.append({"role": str(item["role"]), "content": content[:12000]})
    return normalized


def chat_agent_definition(
    store: AgentStore,
    definition: dict[str, Any],
    message: str,
    tool_input: dict[str, Any] | None,
    tool_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run optional Tool AI and Base LLM for a verified Draft or Build."""
    agent_id = str(definition.get("agentId") or "")
    llm = definition.get("llm") if isinstance(definition.get("llm"), dict) else {}
    runtime = _llm_runtime_status(store, llm)
    if runtime["status"] not in {"installed", "ready"}:
        raise AgentRuntimeUnavailable(str(runtime["detail"]))
    trace: list[dict[str, Any]] = []
    tool_result = None
    tool_results: list[dict[str, Any]] = []
    if tool_input is not None:
        selected_tool, route_reason = _route_tool(definition, message, tool_id)
        if selected_tool is None:
            raise AgentRuntimeUnavailable(
                "Automatic Tool routing could not identify one Tool AI. Select the Task Data source or name the Tool in the request."
            )
        selected_tool_id = str(selected_tool.get("localProjectId") or "")
        tool_result = run_tool_smoke_definition(store, definition, tool_input, selected_tool_id)
        tool_results = [tool_result]
        trace.append({
            "stage": "tool-routing",
            "status": "passed",
            "toolId": selected_tool_id,
            "reason": route_reason,
        })
        trace.append({
            "stage": "tool-ai",
            "status": "passed",
            "durationMs": tool_result["durationMs"],
            "taskId": tool_result["tool"]["taskId"],
            "modelVersionId": tool_result["tool"]["modelVersionId"],
        })
    harness = definition.get("harness") if isinstance(definition.get("harness"), dict) else {}
    instructions = str(harness.get("instructions") or "").strip()
    if tool_result is not None:
        compact_tool_result = json.dumps(tool_result["result"], ensure_ascii=False)[:6000]
        instructions = (
            f"{instructions}\n\nThe selected FedOps Tool AI returned this verified JSON result: "
            f"{compact_tool_result}\nUse this result accurately and do not invent additional values."
        )
    try:
        messages = [
            {"role": "system", "content": instructions},
            *_normalized_history(history),
            {"role": "user", "content": message.strip()},
        ]
        if llm.get("source") == "huggingface":
            llm_result = local_huggingface_chat(
                store.models_root,
                str(llm.get("repoId") or ""),
                str(llm.get("revision") or ""),
                messages,
                file_name=str(llm.get("fileName") or "") or None,
                context_window=int(harness.get("contextWindow") or 8192),
                max_tokens=int(harness.get("maxTokens") or 512),
                temperature=float(harness.get("temperature") or 0.2),
                on_delta=on_delta,
            )
        elif llm.get("source") == "federated-task":
            llm_result = _federated_task_llm_chat(store, llm, messages, harness)
            if on_delta is not None:
                on_delta(str(llm_result["content"]))
        else:
            raise AgentRuntimeUnavailable("This Agent has no supported Base LLM.")
    except ModelRunnerUnavailable as error:
        raise AgentRuntimeUnavailable(str(error)) from error
    trace.append({
        "stage": "base-llm",
        "status": "passed",
        "model": llm_result["model"],
        "usage": llm_result["usage"],
    })
    return {
        "requestId": f"request-{uuid.uuid4().hex[:16]}",
        "agentId": agent_id,
        "status": "succeeded",
        "response": llm_result["content"],
        "toolResult": tool_result,
        "toolResults": tool_results,
        "trace": trace,
        "createdAt": _now(),
    }


def chat_agent(
    store: AgentStore,
    agent_id: str,
    message: str,
    tool_input: dict[str, Any] | None,
    tool_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Chat with the latest immutable Build."""
    return chat_agent_definition(
        store,
        store.latest_build(agent_id),
        message,
        tool_input,
        tool_id,
        history,
        on_delta,
    )


def prepare_agent_definition_llm(
    store: AgentStore,
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Prepare the Base LLM selected by a verified Draft or Build."""
    llm = definition.get("llm") if isinstance(definition.get("llm"), dict) else {}
    try:
        if llm.get("source") == "huggingface":
            return prepare_local_huggingface_model(
                store.models_root,
                str(llm.get("repoId") or ""),
                str(llm.get("revision") or ""),
                str(llm.get("fileName") or "") or None,
            )
        if llm.get("source") == "federated-task":
            return _llm_runtime_status(store, llm)
        raise AgentRuntimeUnavailable("This Agent has no supported Base LLM.")
    except ModelRunnerUnavailable as error:
        raise AgentRuntimeUnavailable(str(error)) from error


def prepare_agent_llm(store: AgentStore, agent_id: str) -> dict[str, Any]:
    return prepare_agent_definition_llm(store, store.latest_build(agent_id))


def _federated_task_preparation(
    store: AgentStore,
    definition: dict[str, Any],
) -> dict[str, Any]:
    llm = definition.get("llm") if isinstance(definition.get("llm"), dict) else {}
    runtime = _llm_runtime_status(store, llm)
    ready = runtime.get("status") in {"installed", "ready"}
    now = _now()
    identity = hashlib.sha256(
        f"{store.root}\0{llm.get('localProjectId')}\0{llm.get('modelSha256')}".encode()
    ).hexdigest()[:20]
    return {
        "preparationId": f"model-{identity}",
        "status": "succeeded" if ready else "failed",
        "stage": "ready" if ready else "failed",
        "percent": 100.0 if ready else 0.0,
        "downloadedBytes": 0,
        "totalBytes": None,
        "detail": str(runtime.get("detail") or ""),
        "provider": str(runtime.get("provider") or "federated-task-workspace"),
        "model": str(runtime.get("model") or llm.get("modelName") or ""),
        "localPath": str(runtime.get("localPath") or llm.get("localPath") or ""),
        "startedAt": now,
        "updatedAt": now,
    }


def start_agent_definition_llm_preparation(
    store: AgentStore,
    definition: dict[str, Any],
) -> dict[str, Any]:
    llm = definition.get("llm") if isinstance(definition.get("llm"), dict) else {}
    if llm.get("source") == "huggingface":
        return start_local_huggingface_preparation(
            store.models_root,
            str(llm.get("repoId") or ""),
            str(llm.get("revision") or ""),
            str(llm.get("fileName") or "") or None,
        )
    if llm.get("source") == "federated-task":
        return _federated_task_preparation(store, definition)
    raise AgentRuntimeUnavailable("This Agent has no supported Base LLM.")


def read_agent_definition_llm_preparation(
    store: AgentStore,
    definition: dict[str, Any],
) -> dict[str, Any]:
    llm = definition.get("llm") if isinstance(definition.get("llm"), dict) else {}
    if llm.get("source") == "huggingface":
        return read_local_huggingface_preparation(
            store.models_root,
            str(llm.get("repoId") or ""),
            str(llm.get("revision") or ""),
            str(llm.get("fileName") or "") or None,
        )
    if llm.get("source") == "federated-task":
        return _federated_task_preparation(store, definition)
    raise AgentRuntimeUnavailable("This Agent has no supported Base LLM.")


def start_agent_llm_preparation(store: AgentStore, agent_id: str) -> dict[str, Any]:
    return start_agent_definition_llm_preparation(store, store.latest_build(agent_id))


def read_agent_llm_preparation(store: AgentStore, agent_id: str) -> dict[str, Any]:
    return read_agent_definition_llm_preparation(store, store.latest_build(agent_id))


def record_request(
    store: AgentStore,
    agent_id: str,
    method: str,
    path: str,
    status: int,
    started: float,
    request_id: str,
    source: str,
) -> None:
    with STORE_LOCK:
        document = store.load()
        records = document["requests"].setdefault(agent_id, [])
        records.append({
            "requestId": request_id,
            "createdAt": _now(),
            "method": method,
            "path": path,
            "status": status,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "source": source,
        })
        document["requests"][agent_id] = records[-MAX_REQUESTS:]
        state = document["serving"].get(agent_id)
        if isinstance(state, dict):
            state["requestCount"] = int(state.get("requestCount") or 0) + 1
        store.save(document)


def list_requests(store: AgentStore, agent_id: str) -> list[dict[str, Any]]:
    store.latest_build(agent_id)
    records = store.load()["requests"].get(agent_id)
    return list(reversed(records)) if isinstance(records, list) else []


__all__ = [
    "AgentRuntimeUnavailable",
    "AGENT_SERVING_PORT_MAX",
    "AGENT_SERVING_PORT_MIN",
    "agent_health",
    "agent_info",
    "authorize_serving",
    "chat_agent",
    "chat_agent_definition",
    "delete_serving_data_source",
    "disable_serving",
    "enable_serving",
    "find_served_agent_store",
    "find_served_agent_store_by_port",
    "list_requests",
    "list_serving_data_sources",
    "predict_agent_tool",
    "require_direct_tool_serving",
    "prepare_agent_definition_llm",
    "prepare_agent_llm",
    "read_agent_definition_llm_preparation",
    "read_agent_llm_preparation",
    "read_serving",
    "record_request",
    "rotate_serving_token",
    "set_direct_tool_serving",
    "resolve_agent_tool_input",
    "start_agent_definition_llm_preparation",
    "start_agent_llm_preparation",
    "test_agent",
    "test_agent_definition",
    "upsert_serving_data_source",
    "update_serving_port",
]
