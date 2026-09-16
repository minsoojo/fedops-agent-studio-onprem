"""Map existing FedOps Web Task responses without conflating identifiers."""

from __future__ import annotations

from typing import Any


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def model_capability(item: dict[str, Any]) -> str:
    """Map a Federated Task model to its Agent Builder composition role."""
    model_type = str(item.get("modelType") or "").strip().casefold()
    if model_type:
        return "base-llm" if model_type == "llm" else "tool-ai"
    primary = item.get("primaryModel") if isinstance(item.get("primaryModel"), dict) else {}
    values = {
        str(item.get("dataType") or "").strip().casefold(),
        str(item.get("dataModality") or "").strip().casefold(),
        str(primary.get("task") or "").strip().casefold(),
    }
    llm_values = {
        "llm",
        "large-language-model",
        "text-generation",
        "text_generation",
        "causal-language-modeling",
        "causal_lm",
        "chat",
    }
    return "base-llm" if values & llm_values else "tool-ai"


def normalize_registry_task(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    permissions = item.get("permissions") if isinstance(item.get("permissions"), dict) else {}
    runtime_contract = (
        item.get("runtimeContract")
        if isinstance(item.get("runtimeContract"), dict)
        else {"name": "legacy-v1", "schemaVersion": 1}
    )
    membership = item.get("membership") if isinstance(item.get("membership"), dict) else None
    owner_handle = item.get("ownerHandle") or owner.get("handle")
    slug = item.get("slug")
    primary_model = item.get("primaryModel") if isinstance(item.get("primaryModel"), dict) else None
    display_name = item.get("displayName") or item.get("title") or item.get("name")
    title = (
        primary_model.get("displayName")
        if primary_model and primary_model.get("displayName")
        else display_name
    )
    task_id = item.get("taskId") or item.get("_id")
    registry_id = item.get("id")
    if not registry_id and owner_handle and slug:
        registry_id = f"{owner_handle}/{slug}"
    if not registry_id:
        registry_id = task_id or title
    if not registry_id or not title:
        return None
    if not membership:
        if permissions.get("isOwner"):
            membership = {"role": "owner", "status": "owner"}
        elif permissions.get("participationStatus"):
            membership = {
                "role": "participant",
                "status": permissions.get("participationStatus"),
            }
        elif permissions.get("isParticipant"):
            membership = {"role": "participant", "status": "approved"}
        elif permissions.get("isAdmin") or (
            permissions.get("canManage") and not permissions.get("isOwner")
        ):
            membership = {"role": "admin", "status": "admin"}
    can_open_workspace = bool(
        permissions.get("canOpenWorkspace")
        or permissions.get("isOwner")
        or permissions.get("isParticipant")
        or permissions.get("canManage")
    )
    return {
        "registryId": str(registry_id),
        "taskId": str(task_id) if task_id is not None else None,
        "title": str(title),
        "displayName": str(display_name),
        "taskCategory": str(item.get("taskCategory")) if item.get("taskCategory") else None,
        "dataModality": str(item.get("dataModality")) if item.get("dataModality") else None,
        "primaryModel": primary_model,
        "runtimeKey": str(item.get("runtimeKey")) if item.get("runtimeKey") else None,
        "ownerHandle": str(owner_handle) if owner_handle else None,
        "slug": str(slug) if slug else None,
        "summary": str(item.get("summary") or ""),
        "description": str(item.get("description") or ""),
        "cardMarkdown": str(item.get("cardMarkdown") or ""),
        "tags": _tags(item.get("tags")),
        "visibility": str(item.get("visibility") or "private"),
        "participationPolicy": str(item.get("participationPolicy") or "approval_required"),
        "status": str(item.get("status") or "not_start"),
        "dataType": str(item.get("dataType")) if item.get("dataType") else None,
        "modelType": str(item.get("modelType")) if item.get("modelType") else None,
        "modelCapability": model_capability(item),
        "strategy": str(item.get("strategy")) if item.get("strategy") else None,
        "numRounds": str(item.get("numRounds")) if item.get("numRounds") else None,
        "publishedAt": item.get("publishedAt") or item.get("createdAt"),
        "registryStatus": str(item.get("registryStatus") or "legacy"),
        "runtimeContract": runtime_contract,
        "currentPublishedReleaseId": (
            str(item.get("currentPublishedReleaseId"))
            if item.get("currentPublishedReleaseId") else None
        ),
        "updatedAt": item.get("updatedAt"),
        "membership": membership,
        "permissions": {
            "canView": bool(permissions.get("canView")),
            "canManage": bool(permissions.get("canManage")),
            "canDownloadModels": bool(permissions.get("canDownloadModels")),
            "canRequestParticipation": bool(permissions.get("canRequestParticipation")),
            "canOpenWorkspace": can_open_workspace,
            "isOwner": bool(permissions.get("isOwner")),
            "isAdmin": bool(
                permissions.get("isAdmin")
                or permissions.get("canManage") and not permissions.get("isOwner")
            ),
            "isParticipant": bool(permissions.get("isParticipant")),
            "participationStatus": permissions.get("participationStatus"),
            "canLeaveParticipation": bool(permissions.get("canLeaveParticipation")),
            "leaveRequiresCompletedRun": bool(permissions.get("leaveRequiresCompletedRun")),
            "completedParticipationCount": int(
                permissions.get("completedParticipationCount") or 0
            ),
            "lastParticipatedAt": permissions.get("lastParticipatedAt"),
        },
    }


def normalize_registry_tasks(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else []
    normalized = [normalize_registry_task(item) for item in items]
    return [item for item in normalized if item is not None]


def normalize_participation(payload: Any, task: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    participation_id = payload.get("_id") or payload.get("participationId")
    task_id = payload.get("taskId") or task.get("taskId")
    status = {
        "requested": "pending-approval",
        "revoked": "left",
    }.get(str(payload.get("status") or ""), payload.get("status"))
    if not participation_id or not task_id or not status:
        return None
    return {
        "participationId": str(participation_id),
        "taskId": str(task_id),
        "status": str(status),
        "runtimeKey": task.get("runtimeKey"),
        "localProjectId": None,
    }


def normalize_account_tasks(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = next(
            (
                candidate
                for key in ("tasks", "items", "data", "results")
                if isinstance((candidate := payload.get(key)), list)
            ),
            [],
        )
    else:
        items = []

    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        task_id = item.get("_id") or item.get("taskId") or item.get("task_id") or item.get("id")
        if task_id is None:
            continue
        display_name = item.get("displayName") or item.get("title") or item.get("name") or item.get("taskName") or f"Task {index}"
        runtime_key = item.get("runtimeKey") or item.get("runtime_key")
        runtime_contract = (
            item.get("runtimeContract")
            if isinstance(item.get("runtimeContract"), dict)
            else {"name": "legacy-v1", "schemaVersion": 1}
        )
        tasks.append(
            {
                "taskId": str(task_id),
                "displayName": str(display_name),
                "runtimeKey": str(runtime_key) if runtime_key is not None else None,
                "ownerHandle": str(item["ownerHandle"]) if item.get("ownerHandle") else None,
                "slug": str(item["slug"]) if item.get("slug") else None,
                "registryStatus": str(item.get("registryStatus") or "legacy"),
                "runtimeContract": runtime_contract,
                "yamlConfig": str(item["yamlConfig"]) if item.get("yamlConfig") is not None else None,
                "primaryModel": item.get("primaryModel") if isinstance(item.get("primaryModel"), dict) else None,
            }
        )
    return tasks
