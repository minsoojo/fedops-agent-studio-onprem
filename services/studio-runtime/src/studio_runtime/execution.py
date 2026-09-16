"""Safe, environment-bound execution for files edited in a Workspace."""

from __future__ import annotations

from pathlib import Path

from .environments import uv_run_command
from .jobs import RUN_MANAGER
from .workspace import resolve_project_file


def run_python_file_run(
    project_root: Path,
    local_project_id: str,
    file_path: str,
    environment_id: str | None = None,
    runtime_namespace: str = "device",
) -> dict[str, object]:
    """Run one project-local Python file in the selected synced uv environment."""
    target = resolve_project_file(project_root, file_path)
    if not target.is_file():
        raise FileNotFoundError("The selected Python file was not found.")
    if target.suffix.casefold() != ".py":
        raise ValueError("Only Python (.py) files can be run from the Code editor.")
    relative_path = target.relative_to(project_root.resolve()).as_posix()
    argv, environment, selected = uv_run_command(
        project_root,
        local_project_id,
        ["python", relative_path],
        environment_id,
    )
    return RUN_MANAGER.start_command(
        kind="run-file",
        local_project_id=local_project_id,
        environment_id=str(selected["environmentId"]),
        argv=argv,
        cwd=project_root,
        environment=environment,
        initial_output=f"Running {relative_path}...\n",
        namespace=runtime_namespace,
    )
