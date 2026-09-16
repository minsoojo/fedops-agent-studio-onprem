"""Project discovery and safe file access inside the local Workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .environments import requirements_mode, selected_environment_ready
from .federated_task import read_task_binding, read_task_contract


MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
IGNORED_NAMES = {
    ".fedops-studio",
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
}
PROTECTED_PATH_NAMES = IGNORED_NAMES | {".runtime"}
BASELINE_POLICY_PATHS = (
    Path(".fedops-studio/baseline.json"),
    Path("baseline-manifest.json"),
)
OWNER_EDITABLE_DIRECTORIES = (
    "federated_task/conf/",
    "federated_task/local_training/",
    "federated_task/tool_ai/",
)


def discover_projects(workspace: Path) -> list[dict[str, Any]]:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        return []

    projects: list[dict[str, Any]] = []
    for child in workspace.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        relative_path = child.relative_to(workspace).as_posix()
        projects.append(
            {
                "localProjectId": f"local:{relative_path}",
                "name": child.name,
                "path": f"./{relative_path}",
                "origin": "local",
                "hasPyproject": (child / "pyproject.toml").is_file(),
                "hasFedOpsTask": read_task_contract(child) is not None,
                "taskBinding": read_task_binding(child),
                "projectEnvironmentReady": (
                    selected_environment_ready(child, f"local:{relative_path}")
                ),
            }
        )

    return sorted(projects, key=lambda item: item["name"].casefold())


def project_for_task(
    projects: list[dict[str, Any]],
    task_id: str,
) -> dict[str, Any] | None:
    """Select a project by stable FedOps taskId from an already discovered list."""
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return None
    return next(
        (
            project
            for project in projects
            if isinstance(project.get("taskBinding"), dict)
            and str(project["taskBinding"].get("taskId") or "") == normalized_task_id
        ),
        None,
    )


def find_project_for_task(workspace: Path, task_id: str) -> dict[str, Any] | None:
    """Return the one account-local Workspace bound to a stable FedOps taskId."""
    return project_for_task(discover_projects(workspace), task_id)


def find_project(workspace: Path, local_project_id: str) -> tuple[dict[str, Any], Path]:
    project = next(
        (
            item
            for item in discover_projects(workspace)
            if item["localProjectId"] == local_project_id
        ),
        None,
    )
    if not project:
        raise FileNotFoundError("The local Workspace project was not found.")
    workspace_root = workspace.expanduser().resolve()
    project_root = (workspace_root / str(project["path"])).resolve()
    project_root.relative_to(workspace_root)
    return project, project_root


def delete_project(
    workspace: Path,
    local_project_id: str,
    expected_name: str,
) -> dict[str, object]:
    """Permanently remove one discovered first-level local project."""
    project, project_root = find_project(workspace, local_project_id)
    if str(project["name"]) != expected_name:
        raise ValueError("The confirmation name does not match the local project.")
    workspace_root = workspace.expanduser().resolve()
    workspace_entry = workspace_root / str(project["path"])
    if workspace_entry.parent != workspace_root or project_root.parent != workspace_root:
        raise ValueError("Only a first-level local Workspace project can be deleted.")
    if workspace_entry.is_symlink():
        raise ValueError("Linked Workspace directories cannot be deleted from Agent Studio.")
    shutil.rmtree(workspace_entry)
    return {
        "deleted": True,
        "localProjectId": local_project_id,
        "name": project["name"],
    }


def resolve_project_file(project_root: Path, file_path: str) -> Path:
    project_root = project_root.resolve()
    normalized = str(file_path or "").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        raise ValueError("A file path is required.")
    target = (project_root / normalized).resolve()
    try:
        target.relative_to(project_root)
    except ValueError as error:
        raise ValueError("Cannot access files outside the selected project.") from error
    return target


def mutable_project_file(project_root: Path, file_path: str) -> Path:
    """Resolve an editor-managed file while protecting runtime directories."""
    normalized = str(file_path or "").strip().removeprefix("./")
    relative = Path(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("A project-relative file path is required.")
    if any(part.startswith(".") or part in PROTECTED_PATH_NAMES for part in relative.parts):
        raise ValueError("Hidden and runtime-managed paths cannot be modified in the editor.")
    if not project_file_editable(project_root, relative.as_posix()):
        raise ValueError(
            "This file is managed by Agent Studio/FedOps and is read-only. "
            "Edit only files marked Owner editable."
        )
    cursor = project_root.resolve()
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Linked paths cannot be modified in the editor.")
    return resolve_project_file(project_root, relative.as_posix())


def _baseline_edit_policy(project_root: Path) -> tuple[bool, dict[str, bool]]:
    """Return the immutable Baseline authoring policy, failing closed if malformed."""
    for relative in BASELINE_POLICY_PATHS:
        policy_path = project_root / relative
        if not policy_path.is_file():
            continue
        try:
            manifest = json.loads(policy_path.read_text(encoding="utf-8"))
            entries = manifest.get("files")
            if not isinstance(entries, list):
                return True, {}
            policy: dict[str, bool] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    return True, {}
                path = entry.get("path")
                if not isinstance(path, str) or not path or path in policy:
                    return True, {}
                policy[path] = entry.get("editable") is True
            return True, policy
        except (OSError, json.JSONDecodeError):
            return True, {}
    return False, {}


def _baseline_file_roles(project_root: Path) -> dict[str, str]:
    """Read presentation roles without weakening the manifest edit policy."""
    for relative in BASELINE_POLICY_PATHS:
        policy_path = project_root / relative
        if not policy_path.is_file() or policy_path.is_symlink():
            continue
        try:
            manifest = json.loads(policy_path.read_text(encoding="utf-8"))
            entries = manifest.get("files")
            if not isinstance(entries, list):
                return {}
            return {
                str(entry["path"]): str(entry["role"])
                for entry in entries
                if isinstance(entry, dict)
                and isinstance(entry.get("path"), str)
                and isinstance(entry.get("role"), str)
            }
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def project_file_access_kind(project_root: Path, relative: str) -> str:
    """Classify authoring, generated output, and FedOps-managed files."""
    normalized = str(relative or "").strip().removeprefix("./")
    if normalized == "local_model" or normalized.startswith("local_model/"):
        return "generated"
    if normalized == "model_release" or normalized.startswith("model_release/"):
        return "generated"
    if f"{normalized.rstrip('/')}/" in OWNER_EDITABLE_DIRECTORIES:
        return "editable"
    return "editable" if project_file_editable(project_root, normalized) else "managed"


def project_file_editable(project_root: Path, file_path: str) -> bool:
    """Resolve the one authoring rule used by the API, Explorer, and editor."""
    relative = str(file_path or "").strip().removeprefix("./")
    has_policy, policy = _baseline_edit_policy(project_root)
    if has_policy:
        if relative in policy:
            return policy[relative]
        # Task authors may add helper/config files only inside explicit Owner areas.
        return any(relative.startswith(prefix) for prefix in OWNER_EDITABLE_DIRECTORIES)
    if relative == "uv.lock":
        return False
    if relative == "pyproject.toml" and requirements_mode(project_root) == "requirements":
        return False
    return True


def _project_directory_editable(
    project_root: Path,
    relative: str,
    policy_state: tuple[bool, dict[str, bool]],
) -> bool:
    has_policy, policy = policy_state
    if not has_policy or relative == ".":
        return True
    prefix = f"{relative.rstrip('/')}/"
    editable_paths = [path for path, editable in policy.items() if editable]
    editable_paths.extend(OWNER_EDITABLE_DIRECTORIES)
    return any(
        candidate.startswith(prefix) or prefix.startswith(candidate.rstrip("/") + "/")
        for candidate in editable_paths
    )


def build_file_tree(
    project_root: Path,
    current: Path | None = None,
    _policy_state: tuple[bool, dict[str, bool]] | None = None,
    _roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    node_path = current or project_root
    policy_state = _policy_state or _baseline_edit_policy(project_root)
    roles = _roles if _roles is not None else _baseline_file_roles(project_root)
    relative = node_path.relative_to(project_root).as_posix()
    node: dict[str, Any] = {
        "name": project_root.name if node_path == project_root else node_path.name,
        "path": "." if relative == "." else relative,
        "type": "directory" if node_path.is_dir() else "file",
    }
    if node_path.is_dir():
        node["readOnly"] = not _project_directory_editable(
            project_root,
            "." if relative == "." else relative,
            policy_state,
        )
        children = []
        for child in node_path.iterdir():
            if child.name.startswith(".") or child.name in IGNORED_NAMES or child.is_symlink():
                continue
            children.append(build_file_tree(project_root, child, policy_state, roles))
        node["children"] = sorted(
            children,
            key=lambda item: (item["type"] != "directory", item["name"].casefold()),
        )
    else:
        node["readOnly"] = not project_file_editable(project_root, relative)
    node["accessKind"] = (
        "editable" if relative == "." else project_file_access_kind(project_root, relative)
    )
    node["role"] = roles.get(relative)
    return node


def language_for(path: Path) -> str:
    return {
        ".py": "python",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".json": "json",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".css": "css",
        ".html": "html",
        ".xml": "xml",
        ".md": "markdown",
        ".sh": "shell",
        ".txt": "text",
    }.get(path.suffix.casefold(), "text")


def serialize_file(project_root: Path, path: Path, content: str) -> dict[str, Any]:
    from datetime import UTC, datetime

    project_root = project_root.resolve()
    path = path.resolve()
    stat = path.stat()
    relative = path.relative_to(project_root).as_posix()
    read_only = not project_file_editable(project_root, relative)
    return {
        "path": relative,
        "name": path.name,
        "content": content,
        "language": language_for(path),
        "size": stat.st_size,
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "readOnly": read_only,
    }


def read_text_file(project_root: Path, file_path: str) -> dict[str, Any]:
    path = resolve_project_file(project_root, file_path)
    if not path.is_file():
        raise FileNotFoundError("The selected file was not found.")
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise OverflowError("Files larger than 2 MiB cannot be opened in the editor.")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("The selected file is not UTF-8 text.") from error
    return serialize_file(project_root, path, content)


def save_text_file(project_root: Path, file_path: str, content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TEXT_FILE_BYTES:
        raise OverflowError("Files larger than 2 MiB cannot be saved in the editor.")
    path = mutable_project_file(project_root, file_path)
    if not path.is_file():
        raise FileNotFoundError("The selected file was not found.")
    path.write_bytes(encoded)
    return serialize_file(project_root, path, content)


def create_text_file(
    project_root: Path,
    file_path: str,
    content: str = "",
) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TEXT_FILE_BYTES:
        raise OverflowError("Files larger than 2 MiB cannot be created in the editor.")
    path = mutable_project_file(project_root, file_path)
    if path.exists():
        raise FileExistsError("A file or directory already exists at this path.")
    if not path.parent.is_dir():
        raise FileNotFoundError("Create the parent directory before creating this file.")
    try:
        with path.open("xb") as file:
            file.write(encoded)
    except FileExistsError as error:
        raise FileExistsError("A file or directory already exists at this path.") from error
    return serialize_file(project_root, path, content)


def delete_text_file(project_root: Path, file_path: str) -> dict[str, object]:
    path = mutable_project_file(project_root, file_path)
    if not path.exists():
        raise FileNotFoundError("The selected file was not found.")
    if not path.is_file():
        raise ValueError("Only files can be deleted from the Code editor.")
    relative_path = path.relative_to(project_root.resolve()).as_posix()
    path.unlink()
    return {"deleted": True, "path": relative_path}


def format_text_file(project_root: Path, file_path: str) -> dict[str, Any]:
    path = mutable_project_file(project_root, file_path)
    if not path.is_file():
        raise FileNotFoundError("The selected file was not found.")
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise OverflowError("Files larger than 2 MiB cannot be formatted in the editor.")

    suffix = path.suffix.casefold()
    if suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"JSON formatting failed: {error}") from error
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path.write_text(content, encoding="utf-8")
        return serialize_file(project_root, path, content)
    if suffix != ".py":
        raise ValueError("Format Document currently supports Python and JSON files.")

    executable = shutil.which("ruff")
    if not executable:
        raise FileNotFoundError("The Studio Python formatter (ruff) is not installed.")
    completed = subprocess.run(
        [executable, "format", "--", str(path)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise ValueError(f"Python formatting failed: {message or 'ruff returned an error'}")
    return read_text_file(project_root, file_path)
