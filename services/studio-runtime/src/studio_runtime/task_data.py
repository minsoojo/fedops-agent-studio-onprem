"""Task-local data summaries and Tool AI inference sample adapters."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .environments import uv_run_command

SAMPLE_MARKER = "__FEDOPS_TASK_DATA_SAMPLE__="


def resolve_task_data_selection(data_root: Path, data_path: str | None) -> tuple[Path, str]:
    """Resolve one local-only selection without allowing Task Data root escape."""
    root = data_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("The Task Data directory was not found.")

    raw = str(data_path or "").strip().replace("\\", "/")
    relative = PurePosixPath(raw or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Task Data path must stay inside the Task Data folder.")

    selected = (root / Path(*relative.parts)).resolve()
    try:
        selected.relative_to(root)
    except ValueError as error:
        raise ValueError("Task Data path must stay inside the Task Data folder.") from error
    if not selected.exists():
        raise FileNotFoundError(f"The selected Task Data path was not found: {raw}")
    normalized = "" if selected == root else selected.relative_to(root).as_posix()
    return selected, normalized


def build_task_data_sample(
    project_root: Path,
    local_project_id: str,
    data_root: Path,
    *,
    index: int = 0,
    data_path: str | None = None,
    environment_id: str | None = None,
) -> dict[str, Any]:
    """Ask the Task-owned adapter to turn local training data into Tool JSON."""
    if index < 0:
        raise ValueError("Task Data sample index must be zero or greater.")
    project = project_root.expanduser().resolve()
    data, normalized_data_path = resolve_task_data_selection(data_root, data_path)
    runner = (
        "import json,sys\n"
        "from federated_task.tool_ai import tool\n"
        "adapter=getattr(tool,'build_tool_data_sample',None)\n"
        "if adapter is None:\n"
        " raise RuntimeError('This Federated Task does not provide build_tool_data_sample(data_root, index).')\n"
        "value=adapter(sys.argv[1],int(sys.argv[2]))\n"
        "if not isinstance(value,dict): raise TypeError('Task Data adapter must return a JSON object.')\n"
        "payload=value.get('payload',value)\n"
        "metadata=value.get('metadata',{})\n"
        "if not isinstance(payload,dict): raise TypeError('Task Data adapter payload must be a JSON object.')\n"
        "print('" + SAMPLE_MARKER + "'+json.dumps({'payload':payload,'metadata':metadata},ensure_ascii=False))\n"
    )
    argv, environment, _ = uv_run_command(
        project,
        local_project_id,
        ["python", "-c", runner, str(data), str(index)],
        environment_id,
    )
    environment = {**os.environ, **environment, "PYTHONPATH": str(project)}
    completed = subprocess.run(
        argv,
        cwd=project,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stdout[-3000:].strip()
        raise RuntimeError(f"Task Data sample could not be prepared.\n{detail}")
    encoded = next(
        (
            line[len(SAMPLE_MARKER):]
            for line in completed.stdout.splitlines()
            if line.startswith(SAMPLE_MARKER)
        ),
        None,
    )
    if encoded is None:
        raise RuntimeError("Task Data adapter returned no structured sample.")
    try:
        result = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise RuntimeError("Task Data adapter returned invalid JSON.") from error
    return {
        "localProjectId": local_project_id,
        "index": index,
        "dataPath": normalized_data_path,
        "payload": result["payload"],
        "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
        "source": "task-data-adapter",
    }


__all__ = ["build_task_data_sample", "resolve_task_data_selection"]
