"""Explicit migration of projects from the former device-wide Workspace."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .federated_task import read_task_contract
from .workspace import discover_projects

MIGRATION_LOG = Path(".fedops-studio/workspace-migrations.json")
_MIGRATION_LOCK = threading.RLock()


def discover_legacy_projects(
    workspace_base: Path,
    account_workspace: Path,
    *,
    display_base: Path | None = None,
) -> list[dict[str, object]]:
    base = workspace_base.expanduser().resolve()
    account_root = account_workspace.expanduser().resolve()
    shown_base = (display_base or base).expanduser()
    if not base.is_dir():
        return []

    items: list[dict[str, object]] = []
    for candidate in sorted(base.iterdir(), key=lambda path: path.name.casefold()):
        if (
            candidate.name in {"accounts", ".fedops-studio"}
            or candidate.name.startswith(".")
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        items.append(
            {
                "name": candidate.name,
                "path": str(shown_base / candidate.name),
                "hasPyproject": (candidate / "pyproject.toml").is_file(),
                "hasFedOpsTask": read_task_contract(candidate) is not None,
                "conflict": (account_root / candidate.name).exists(),
            }
        )
    return items


def import_legacy_projects(
    workspace_base: Path,
    account_workspace: Path,
    account_key: str,
    project_names: list[str],
) -> dict[str, object]:
    base = workspace_base.expanduser().resolve()
    account_root = account_workspace.expanduser().resolve()
    normalized = [name.strip() for name in project_names]
    if not normalized or any(not name for name in normalized):
        raise ValueError("Select at least one legacy Workspace project.")
    if len(set(normalized)) != len(normalized):
        raise ValueError("A legacy Workspace project can only be selected once.")

    with _MIGRATION_LOCK:
        available = {
            str(item["name"]): item
            for item in discover_legacy_projects(base, account_root)
        }
        unknown = [name for name in normalized if name not in available]
        if unknown:
            raise FileNotFoundError(
                f"Legacy Workspace project was not found: {', '.join(unknown)}"
            )
        conflicts = [name for name in normalized if (account_root / name).exists()]
        if conflicts:
            raise FileExistsError(
                f"Account Workspace already contains: {', '.join(conflicts)}"
            )

        account_root.mkdir(parents=True, exist_ok=True)
        moved: list[tuple[Path, Path]] = []
        try:
            for name in normalized:
                source = base / name
                target = account_root / name
                if source.is_symlink() or source.resolve().parent != base:
                    raise ValueError(f"Legacy project path is not safe: {name}")
                source.replace(target)
                moved.append((source, target))
            _record_migration(base, account_key, normalized)
        except Exception:
            for source, target in reversed(moved):
                if target.exists() and not source.exists():
                    target.replace(source)
            raise

    imported = [
        project
        for project in discover_projects(account_root)
        if project["name"] in normalized
    ]
    return {"imported": len(imported), "projects": imported}


def _record_migration(workspace_base: Path, account_key: str, names: list[str]) -> None:
    path = workspace_base / MIGRATION_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Workspace migration history is invalid: {error}") from error
        if not isinstance(stored, list):
            raise ValueError("Workspace migration history has an unsupported format.")
        history = stored
    history.append(
        {
            "accountKey": account_key,
            "projects": names,
            "migratedAt": datetime.now(UTC).isoformat(),
        }
    )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
