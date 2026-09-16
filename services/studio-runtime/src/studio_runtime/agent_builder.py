"""Account-local Agent drafts, verified Tool AI artifacts, and build snapshots."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .federated_task import read_task_binding, source_fingerprint
from .workspace import discover_projects, find_project

AGENT_ID = re.compile(r"^agent-[a-f0-9]{16}$")
STORE_LOCK = threading.RLock()
QWEN_TEST_LLM = {
    "source": "huggingface",
    "repoId": "bartowski/Qwen_Qwen3.5-4B-GGUF",
    "revision": "main",
    "fileName": "Qwen_Qwen3.5-4B-Q4_K_M.gguf",
    "format": "gguf",
    "displayName": "Qwen3.5-4B Q4_K_M",
    "license": "apache-2.0",
    "runtimeStatus": "not-prepared",
}
DEFAULT_HARNESS = {
    "instructions": (
        "Use the selected FedOps Tool AI when the request matches its declared "
        "features. Explain uncertainty and never invent a Tool result."
    ),
    "toolRouting": "automatic",
    "contextWindow": 32768,
    "memory": "session",
    "safety": "standard",
    "temperature": 0.2,
    "maxTokens": 512,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as content:
        while chunk := content.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{secrets.token_hex(4)}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _empty_store() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "drafts": {},
        "builds": {},
        "serving": {},
        "requests": {},
    }


def agent_data_root(local_data_root: Path) -> Path:
    root = local_data_root.expanduser().resolve() / "agents"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


class AgentStore:
    """Small atomic repository shared by Agent Builder and local serving."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "store.json"

    @property
    def account_root(self) -> Path:
        """Account directory on the host-mounted Workspace volume."""
        if self.root.name == "agents" and self.root.parent.name == ".local-data":
            return self.root.parent.parent
        return self.root.parent

    @property
    def workspace_root(self) -> Path:
        return self.account_root / "projects"

    @property
    def models_root(self) -> Path:
        root = self.account_root / "models"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root

    def load(self) -> dict[str, Any]:
        with STORE_LOCK:
            if not self.path.is_file() or self.path.is_symlink():
                return _empty_store()
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("The local Agent store is invalid.") from error
            if not isinstance(value, dict) or value.get("schemaVersion") != 1:
                raise RuntimeError("The local Agent store schema is unsupported.")
            empty = _empty_store()
            for key in ("drafts", "builds", "serving", "requests"):
                if not isinstance(value.get(key), dict):
                    value[key] = empty[key]
            return value

    def save(self, value: dict[str, Any]) -> None:
        with STORE_LOCK:
            _write_json(self.path, value)

    def create_draft(self, name: str, description: str = "") -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Agent name is required.")
        with STORE_LOCK:
            store = self.load()
            duplicate = next(
                (
                    draft for draft in store["drafts"].values()
                    if str(draft.get("name", "")).casefold() == clean_name.casefold()
                ),
                None,
            )
            if duplicate:
                raise FileExistsError("An Agent draft with this name already exists.")
            agent_id = f"agent-{uuid.uuid4().hex[:16]}"
            timestamp = _now()
            draft = {
                "schemaVersion": 1,
                "agentId": agent_id,
                "name": clean_name,
                "description": description.strip(),
                "status": "draft",
                "llm": None,
                "tools": [],
                "harness": dict(DEFAULT_HARNESS),
                "validation": None,
                "buildRevision": 0,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            store["drafts"][agent_id] = draft
            self.save(store)
            return draft

    def list_drafts(self) -> list[dict[str, Any]]:
        drafts = list(self.load()["drafts"].values())
        return sorted(drafts, key=lambda item: str(item.get("updatedAt", "")), reverse=True)

    def read_draft(self, agent_id: str) -> dict[str, Any]:
        _require_agent_id(agent_id)
        draft = self.load()["drafts"].get(agent_id)
        if not isinstance(draft, dict):
            raise KeyError(agent_id)
        return draft

    def update_draft(self, agent_id: str, value: dict[str, Any]) -> dict[str, Any]:
        _require_agent_id(agent_id)
        with STORE_LOCK:
            store = self.load()
            current = store["drafts"].get(agent_id)
            if not isinstance(current, dict):
                raise KeyError(agent_id)
            updated = {
                **current,
                "name": str(value["name"]).strip(),
                "description": str(value.get("description") or "").strip(),
                "llm": dict(value["llm"]) if isinstance(value.get("llm"), dict) else None,
                "tools": [dict(tool) for tool in value["tools"]],
                "harness": dict(value["harness"]),
                "status": "modified" if current.get("buildRevision", 0) else "draft",
                "validation": None,
                "updatedAt": _now(),
            }
            if not updated["name"]:
                raise ValueError("Agent name is required.")
            store["drafts"][agent_id] = updated
            self.save(store)
            return updated

    def save_validation(
        self,
        agent_id: str,
        verified_tools: list[dict[str, Any]],
        report: dict[str, Any],
        verified_llm: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with STORE_LOCK:
            store = self.load()
            draft = store["drafts"].get(agent_id)
            if not isinstance(draft, dict):
                raise KeyError(agent_id)
            draft = {
                **draft,
                "tools": verified_tools,
                "llm": verified_llm if verified_llm is not None else draft.get("llm"),
                "validation": report,
                "status": "ready" if report["ok"] else "validation-required",
                "updatedAt": _now(),
            }
            store["drafts"][agent_id] = draft
            self.save(store)
            return draft

    def commit_build(
        self,
        agent_id: str,
        prepared_tools: list[dict[str, Any]],
        prepared_llm: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with STORE_LOCK:
            store = self.load()
            draft = store["drafts"].get(agent_id)
            if not isinstance(draft, dict):
                raise KeyError(agent_id)
            current_fingerprint = draft_fingerprint(draft)
            validation = draft.get("validation")
            if not isinstance(validation, dict) or not validation.get("ok"):
                raise ValueError("Validate this Agent before building it.")
            if validation.get("sourceFingerprint") != current_fingerprint:
                raise ValueError("The Agent changed after validation. Validate it again.")
            revision = int(draft.get("buildRevision") or 0) + 1
            built_at = _now()
            build = {
                "schemaVersion": 1,
                "agentId": agent_id,
                "name": draft["name"],
                "description": draft["description"],
                "status": "built",
                "buildRevision": revision,
                "builtAt": built_at,
                "sourceFingerprint": current_fingerprint,
                "llm": dict(prepared_llm or draft["llm"]),
                "tools": prepared_tools,
                "harness": dict(draft["harness"]),
                "validation": validation,
            }
            build_root = self.root / "builds" / agent_id / f"r{revision}"
            if build_root.exists():
                raise FileExistsError("This immutable Agent build already exists.")
            build_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".r{revision}-", dir=build_root.parent))
            try:
                _write_json(staging / "agent.json", build)
                _write_json(staging / "checksums.json", {
                    "schemaVersion": 1,
                    "agentSha256": _sha256(staging / "agent.json"),
                    "sourceFingerprint": current_fingerprint,
                    "workspaceTools": [
                        {
                            "localProjectId": tool["localProjectId"],
                            "sourceFingerprint": tool["sourceFingerprint"],
                            "modelSha256": tool["modelSha256"],
                        }
                        for tool in prepared_tools
                    ],
                })
                staging.replace(build_root)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            builds = store["builds"].setdefault(agent_id, [])
            builds.append(build)
            draft = {
                **draft,
                "status": "built",
                "buildRevision": revision,
                "updatedAt": built_at,
            }
            store["drafts"][agent_id] = draft
            self.save(store)
            return with_serving(build, store["serving"].get(agent_id))

    def latest_build(self, agent_id: str) -> dict[str, Any]:
        _require_agent_id(agent_id)
        store = self.load()
        builds = store["builds"].get(agent_id)
        if not isinstance(builds, list) or not builds:
            raise KeyError(agent_id)
        return with_serving(builds[-1], store["serving"].get(agent_id))

    def list_latest_builds(self) -> list[dict[str, Any]]:
        store = self.load()
        result = []
        for agent_id, builds in store["builds"].items():
            if isinstance(builds, list) and builds:
                result.append(with_serving(builds[-1], store["serving"].get(agent_id)))
        return sorted(result, key=lambda item: str(item.get("builtAt", "")), reverse=True)

    def delete_draft(self, agent_id: str) -> None:
        _require_agent_id(agent_id)
        with STORE_LOCK:
            store = self.load()
            if agent_id not in store["drafts"]:
                raise KeyError(agent_id)
            if store["builds"].get(agent_id):
                raise RuntimeError("Delete the Built Agent before deleting its draft.")
            del store["drafts"][agent_id]
            self.save(store)

    def delete_agent(self, agent_id: str) -> None:
        _require_agent_id(agent_id)
        with STORE_LOCK:
            store = self.load()
            if not store["builds"].get(agent_id):
                raise KeyError(agent_id)
            serving = store["serving"].get(agent_id)
            if isinstance(serving, dict) and serving.get("enabled"):
                raise RuntimeError("Disable this Agent's Serving API before deleting it.")
            store["builds"].pop(agent_id, None)
            store["serving"].pop(agent_id, None)
            store["requests"].pop(agent_id, None)
            draft = store["drafts"].get(agent_id)
            if isinstance(draft, dict):
                store["drafts"][agent_id] = {
                    **draft,
                    "status": "modified",
                    "buildRevision": 0,
                    "validation": None,
                    "updatedAt": _now(),
                }
            self.save(store)
            shutil.rmtree(self.root / "builds" / agent_id, ignore_errors=True)


def _require_agent_id(agent_id: str) -> None:
    if not AGENT_ID.fullmatch(agent_id):
        raise ValueError("The local agentId is invalid.")


def with_serving(build: dict[str, Any], serving: object) -> dict[str, Any]:
    state = serving if isinstance(serving, dict) else {}
    raw_port = state.get("port")
    port = int(raw_port) if isinstance(raw_port, int) else None
    endpoint_base = f"/serve/v1/agents/{build['agentId']}"
    studio_port = int(os.getenv("STUDIO_PORT", "24368"))
    return {
        **build,
        "serving": {
            "agentId": build["agentId"],
            "enabled": bool(state.get("enabled")),
            "endpointBase": endpoint_base,
            "endpointUrl": (
                f"http://localhost:{port}" if port is not None
                else f"http://localhost:{studio_port}{endpoint_base}"
            ),
            "port": port,
            "tokenCreatedAt": state.get("tokenCreatedAt"),
            "requestCount": int(state.get("requestCount") or 0),
            "dataSourceCount": len(state.get("dataSources") or []) if isinstance(state.get("dataSources"), list) else 0,
            "directToolIds": [
                str(tool_id)
                for tool_id in (state.get("directToolIds") or [])
                if isinstance(tool_id, str)
            ] if isinstance(state.get("directToolIds"), list) else [],
        },
    }


def draft_fingerprint(draft: dict[str, Any]) -> str:
    return _fingerprint({
        "name": draft.get("name"),
        "description": draft.get("description"),
        "llm": draft.get("llm"),
        "tools": draft.get("tools"),
        "harness": draft.get("harness"),
    })


def _model_capability(binding: dict[str, Any]) -> str:
    model_type = str(binding.get("modelType") or "").strip().casefold()
    if model_type:
        return "base-llm" if model_type == "llm" else "tool-ai"
    primary = binding.get("primaryModel") if isinstance(binding.get("primaryModel"), dict) else {}
    values = {
        str(binding.get("dataType") or "").strip().casefold(),
        str(binding.get("dataModality") or "").strip().casefold(),
        str(primary.get("task") or "").strip().casefold(),
    }
    llm_values = {
        "llm", "large-language-model", "text-generation",
        "text_generation", "causal-language-modeling", "causal_lm", "chat",
    }
    return "base-llm" if values & llm_values else "tool-ai"


def _model_release(project_root: Path) -> tuple[dict[str, Any], Path]:
    manifest_path = project_root / "model_release" / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("model_release/manifest.json is missing.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The local model release manifest is invalid.") from error
    if not isinstance(manifest, dict):
        raise TypeError("The local model release manifest must be an object.")
    artifact = str(manifest.get("artifact") or "model.safetensors")
    artifact_path = Path(artifact)
    if artifact_path.is_absolute() or len(artifact_path.parts) != 1 or artifact_path.name != artifact:
        raise ValueError("The local model artifact name is unsafe.")
    model_path = project_root / "model_release" / artifact
    if not model_path.is_file() or model_path.is_symlink():
        raise ValueError(f"model_release/{artifact} is missing.")
    checksum = _sha256(model_path)
    declared = str(manifest.get("sha256") or "")
    if declared and declared != checksum:
        raise ValueError("The local model checksum does not match its manifest.")
    if manifest.get("status") not in {"ready", "published"}:
        raise ValueError("Local Train or a Published Task download must prepare the model release first.")
    return {**manifest, "sha256": checksum}, model_path


def _cached_model_versions(project_root: Path) -> list[dict[str, Any]]:
    root = project_root / ".fedops-studio" / "model-versions"
    values: list[dict[str, Any]] = []
    if not root.is_dir() or root.is_symlink():
        return values
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact = str(manifest.get("artifact") or "")
            artifact_path = manifest_path.parent / artifact
            if (
                not isinstance(manifest, dict)
                or not manifest.get("modelVersionId")
                or not artifact
                or not artifact_path.is_file()
                or artifact_path.is_symlink()
            ):
                continue
            checksum = _sha256(artifact_path)
            if manifest.get("sha256") and manifest["sha256"] != checksum:
                continue
            values.append({
                **manifest,
                "sha256": checksum,
                "modelArtifactPath": str(artifact_path.relative_to(project_root)),
                "cached": True,
            })
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(values, key=lambda item: int(item.get("version") or 0))


def _validate_llm_contract(project_root: Path) -> dict[str, Any]:
    manifest_path = project_root / "federated_task" / "llm" / "manifest.json"
    runtime_path = project_root / "federated_task" / "llm" / "runtime.py"
    if not manifest_path.is_file() or not runtime_path.is_file():
        raise ValueError(
            "An LLM Federated Task must provide federated_task/llm/manifest.json and runtime.py."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tree = ast.parse(runtime_path.read_text(encoding="utf-8"), filename=str(runtime_path))
    except (OSError, json.JSONDecodeError, SyntaxError) as error:
        raise ValueError("The local LLM runtime contract is invalid.") from error
    functions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "chat" not in functions:
        raise ValueError("The LLM Federated Task runtime must provide chat(messages, model_path, harness).")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("description"), str):
        raise TypeError("The LLM runtime manifest must contain a description.")
    return manifest


def list_local_model_sources(
    workspace_root: Path,
    display_workspace_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Describe account-local Federated Task projects without copying their code."""
    sources: list[dict[str, Any]] = []
    for project in discover_projects(workspace_root):
        if not project.get("hasFedOpsTask"):
            continue
        _, project_root = find_project(workspace_root, str(project["localProjectId"]))
        binding = read_task_binding(project_root) or {}
        capability = _model_capability(binding)
        primary = binding.get("primaryModel") if isinstance(binding.get("primaryModel"), dict) else {}
        project_name = str(project["name"])
        display_root = display_workspace_root or workspace_root
        display_path = str(display_root.expanduser() / project_name)
        source: dict[str, Any] = {
            "localProjectId": str(project["localProjectId"]),
            "projectName": project_name,
            "displayPath": display_path,
            "capability": capability,
            "ready": False,
            "reason": None,
            "taskId": str(binding.get("taskId")) if binding.get("taskId") else None,
            "runtimeKey": str(binding.get("runtimeKey")) if binding.get("runtimeKey") else None,
            "registryId": (
                f"{binding.get('ownerHandle')}/{binding.get('slug')}"
                if binding.get("ownerHandle") and binding.get("slug") else None
            ),
            "taskTitle": str(binding.get("displayName") or project_name),
            "modelName": str(
                primary.get("displayName") or primary.get("workingName") or project_name
            ),
            "releaseId": str(binding.get("releaseId")) if binding.get("releaseId") else None,
            "modelVersionId": (
                str(binding.get("modelVersionId")) if binding.get("modelVersionId") else None
            ),
            "modelVersion": None,
            "format": None,
            "sourceFingerprint": None,
            "modelSha256": None,
            "localPath": display_path,
            "toolManifest": None,
            "availableModelVersions": [],
            "modelArtifactPath": None,
        }
        try:
            model_manifest, _ = _model_release(project_root)
            contract = (
                _validate_llm_contract(project_root)
                if capability == "base-llm"
                else _validate_tool_contract(project_root)
            )
            source.update({
                "ready": True,
                "sourceFingerprint": source_fingerprint(project_root),
                "modelSha256": model_manifest["sha256"],
                "format": model_manifest.get("format"),
                "modelVersion": (
                    int(model_manifest["version"])
                    if isinstance(model_manifest.get("version"), int) else None
                ),
                "toolManifest": contract if capability == "tool-ai" else None,
                "availableModelVersions": [
                    {
                        "modelVersionId": source["modelVersionId"],
                        "version": model_manifest.get("version"),
                        "role": model_manifest.get("role") or "initial",
                        "label": "Initiative Model",
                        "format": model_manifest.get("format"),
                        "size": model_manifest.get("size"),
                        "sha256": model_manifest["sha256"],
                        "cached": True,
                        "modelArtifactPath": None,
                    },
                    *_cached_model_versions(project_root),
                ],
            })
        except (OSError, TypeError, ValueError) as error:
            source["reason"] = str(error)
        sources.append(source)
    return sorted(sources, key=lambda item: (item["capability"], item["projectName"].casefold()))


def resolve_local_model_source(
    workspace_root: Path,
    reference: dict[str, Any],
    display_workspace_root: Path | None = None,
) -> dict[str, Any]:
    local_project_id = str(reference.get("localProjectId") or "")
    source = next(
        (
            item for item in list_local_model_sources(workspace_root, display_workspace_root)
            if item["localProjectId"] == local_project_id
        ),
        None,
    )
    if not source:
        raise ValueError("The selected local Federated Task project no longer exists.")
    if not source["ready"]:
        raise ValueError(f"{source['projectName']} is not Agent-ready: {source['reason']}")
    selected_model_version_id = reference.get("modelVersionId")
    if selected_model_version_id and selected_model_version_id != source.get("modelVersionId"):
        selected = next(
            (
                item for item in source.get("availableModelVersions", [])
                if item.get("modelVersionId") == selected_model_version_id
            ),
            None,
        )
        if not selected or not selected.get("cached"):
            raise ValueError(
                f"Model version {selected_model_version_id} is not prepared on this device."
            )
        source.update({
            "modelVersionId": selected.get("modelVersionId"),
            "modelVersion": selected.get("version"),
            "format": selected.get("format"),
            "modelSha256": selected.get("sha256"),
            "modelArtifactPath": selected.get("modelArtifactPath"),
        })
    for field in ("capability", "sourceFingerprint", "modelSha256"):
        expected = reference.get(field)
        if expected is not None and expected != source.get(field):
            raise ValueError(
                f"{source['projectName']} changed after selection. Select it again and rebuild the Agent."
            )
    return source


def local_tool_reference(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("capability") != "tool-ai" or not source.get("ready"):
        raise ValueError("The selected Workspace project is not a ready Tool AI.")
    return {
        key: source.get(key)
        for key in (
            "localProjectId", "projectName", "taskId", "runtimeKey", "registryId",
            "taskTitle", "releaseId", "modelVersionId", "modelName", "modelVersion",
            "format", "sourceFingerprint", "modelSha256", "localPath", "toolManifest",
            "modelArtifactPath",
        )
    }


def local_llm_reference(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("capability") != "base-llm" or not source.get("ready"):
        raise ValueError("The selected Workspace project is not a ready Federated Task LLM.")
    return {
        "source": "federated-task",
        **{
            key: source.get(key)
            for key in (
                "localProjectId", "projectName", "taskId", "taskTitle", "modelName",
                "releaseId", "modelVersionId", "sourceFingerprint", "modelSha256", "localPath",
                "modelArtifactPath",
            )
        },
        "runtimeStatus": "installed",
    }


def validate_agent_draft(
    store: AgentStore,
    agent_id: str,
    verified_tools: list[dict[str, Any]],
    verified_llm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = store.read_draft(agent_id)
    checks: list[dict[str, str]] = []

    def check(identifier: str, label: str, passed: bool, detail: str) -> None:
        checks.append({
            "id": identifier,
            "label": label,
            "status": "passed" if passed else "failed",
            "detail": detail,
        })

    check("identity", "Agent identity", bool(str(draft.get("name") or "").strip()), agent_id)
    llm = verified_llm or (draft.get("llm") if isinstance(draft.get("llm"), dict) else {})
    source = str(llm.get("source") or "")
    if source == "huggingface":
        llm_supported = bool(
            str(llm.get("repoId") or "").strip()
            and str(llm.get("revision") or "").strip()
            and (
                llm.get("format") != "gguf"
                or str(llm.get("fileName") or "").lower().endswith(".gguf")
            )
        )
        selected_file = f" · {llm.get('fileName')}" if llm.get("fileName") else ""
        llm_detail = f"Hugging Face · {llm.get('repoId')}@{llm.get('revision')}{selected_file}"
    elif source == "federated-task":
        llm_supported = bool(
            llm.get("localProjectId") and llm.get("sourceFingerprint") and llm.get("modelSha256")
        )
        llm_detail = f"Federated Task · {llm.get('taskTitle') or llm.get('projectName')}"
    else:
        llm_supported = False
        llm_detail = "Select one Hugging Face or Federated Task LLM."
    check(
        "base-llm",
        "Base LLM identity",
        llm_supported,
        llm_detail,
    )
    requested_tools = draft.get("tools") if isinstance(draft.get("tools"), list) else []
    check(
        "tool-selection",
        "Tool AI selection",
        True,
        f"{len(requested_tools)} local Federated Task Tool AI project(s); Tool AI is optional.",
    )
    requested_ids = {
        (tool.get("localProjectId"), tool.get("sourceFingerprint"), tool.get("modelSha256"))
        for tool in requested_tools if isinstance(tool, dict)
    }
    verified_ids = {
        (tool.get("localProjectId"), tool.get("sourceFingerprint"), tool.get("modelSha256"))
        for tool in verified_tools
    }
    check(
        "workspace-tools",
        "Local Workspace Tool AI",
        requested_ids == verified_ids,
        "Every selected Tool AI points to its current local source and model checksum.",
    )
    harness = draft.get("harness") if isinstance(draft.get("harness"), dict) else {}
    instructions = str(harness.get("instructions") or "").strip()
    check(
        "agent-harness",
        "Agent Harness",
        bool(instructions) and len(instructions) <= 12_000,
        "Instructions, routing, context, memory, and safety are configured.",
    )
    report = {
        "ok": all(item["status"] == "passed" for item in checks),
        "checkedAt": _now(),
        "sourceFingerprint": draft_fingerprint({**draft, "llm": llm or None, "tools": verified_tools}),
        "checks": checks,
    }
    store.save_validation(agent_id, verified_tools, report, llm or None)
    return report


def _validate_tool_contract(source_root: Path) -> dict[str, Any]:
    manifest_path = source_root / "federated_task" / "tool_ai" / "manifest.json"
    tool_path = source_root / "federated_task" / "tool_ai" / "tool.py"
    if not manifest_path.is_file() or not tool_path.is_file():
        raise ValueError("The Published Release has no Tool AI manifest and adapter.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The Tool AI manifest is invalid.") from error
    if not isinstance(manifest, dict):
        raise TypeError("The Tool AI manifest must be a JSON object.")
    features = manifest.get("features")
    output = manifest.get("output")
    if not isinstance(manifest.get("description"), str) or not isinstance(features, list) or not features:
        raise ValueError("The Tool AI manifest must describe at least one input feature.")
    if not isinstance(output, dict) or not isinstance(output.get("description"), str):
        raise TypeError("The Tool AI manifest output contract is incomplete.")
    try:
        tree = ast.parse(tool_path.read_text(encoding="utf-8"), filename=str(tool_path))
    except (OSError, SyntaxError) as error:
        raise ValueError("The Tool AI adapter is not valid Python.") from error
    functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    required = {"predict", "build_tool_smoke_payload"}
    if not required.issubset(functions):
        raise ValueError("The Tool AI adapter must provide predict and build_tool_smoke_payload.")
    return manifest


__all__ = [
    "DEFAULT_HARNESS",
    "QWEN_TEST_LLM",
    "AgentStore",
    "agent_data_root",
    "draft_fingerprint",
    "list_local_model_sources",
    "local_llm_reference",
    "local_tool_reference",
    "resolve_local_model_source",
    "validate_agent_draft",
]
