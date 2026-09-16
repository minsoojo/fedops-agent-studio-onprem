"""Durable uv environment metadata and allow-listed environment operations.

The registry is owner-oriented so the same runtime can later serve Federated
Tasks, Agent builds, and Agent serving. This first slice resolves only local
Federated Task owners; the API boundary keeps the ownership model explicit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tomllib
import uuid
from pathlib import Path
from typing import Any

from .jobs import RUN_MANAGER, ManagedRun, utc_now

REGISTRY_VERSION = 1
REGISTRY_PATH = Path(".fedops-studio/environments.json")
REQUIREMENTS_PATH = Path("requirements.txt")
SUPPORTED_OWNER_TYPES = {"federated-task", "agent-build", "agent-serve"}
PYTHON_VERSION = re.compile(r"^3\.(?:10|11|12|13|14)$")
PINNED_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9._+!-]*$"
)
MAX_REQUIREMENTS_BYTES = 128 * 1024
_REGISTRY_LOCK = threading.RLock()


def requirements_mode(project_root: Path) -> str:
    """Return the dependency authoring contract used by this Task version."""
    try:
        configuration = tomllib.loads(
            (project_root / "pyproject.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        return "missing"
    project = configuration.get("project", {})
    hook = (
        configuration.get("tool", {})
        .get("hatch", {})
        .get("metadata", {})
        .get("hooks", {})
        .get("requirements_txt", {})
    )
    dynamic = project.get("dynamic", []) if isinstance(project, dict) else []
    files = hook.get("files", []) if isinstance(hook, dict) else []
    if "dependencies" in dynamic and REQUIREMENTS_PATH.as_posix() in files:
        return "requirements"
    return "pyproject"


def validate_task_requirements(content: str) -> list[str]:
    """Validate the intentionally simple Owner dependency contract."""
    if len(content.encode("utf-8")) > MAX_REQUIREMENTS_BYTES:
        raise ValueError("requirements.txt exceeds the 128 KiB safety limit.")
    dependencies: list[str] = []
    names: set[str] = set()
    for line_number, raw in enumerate(content.splitlines(), start=1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if not PINNED_REQUIREMENT.fullmatch(value):
            raise ValueError(
                f"requirements.txt line {line_number} must use library==version."
            )
        name = re.sub(r"[-_.]+", "-", value.split("==", 1)[0]).casefold()
        if name in names:
            raise ValueError(
                f"requirements.txt line {line_number} duplicates package {name}."
            )
        names.add(name)
        dependencies.append(value)
    if not dependencies:
        raise ValueError("requirements.txt must contain at least one library==version entry.")
    return dependencies


def read_task_requirements(project_root: Path) -> dict[str, object]:
    """Read the canonical Task dependencies without exposing environment internals."""
    mode = requirements_mode(project_root)
    path = project_root / REQUIREMENTS_PATH
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    dependencies: list[str] = []
    error: str | None = None
    if mode == "requirements":
        try:
            dependencies = validate_task_requirements(content)
        except ValueError as cause:
            error = str(cause)
    return {
        "path": REQUIREMENTS_PATH.as_posix(),
        "mode": mode,
        "editable": mode == "requirements",
        "content": content,
        "dependencies": dependencies,
        "valid": error is None,
        "error": error,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None,
    }


def write_task_requirements(project_root: Path, content: str) -> dict[str, object]:
    """Atomically update the sole Owner-editable dependency input."""
    if requirements_mode(project_root) != "requirements":
        raise ValueError(
            "This previous-format Task manages dependencies in pyproject.toml."
        )
    validate_task_requirements(content)
    normalized = content.rstrip() + "\n"
    path = project_root / REQUIREMENTS_PATH
    temporary = path.with_suffix(".tmp")
    temporary.write_text(normalized, encoding="utf-8")
    temporary.replace(path)
    return read_task_requirements(project_root)


def _sync_extras(project_root: Path) -> list[str]:
    """Install the complete Federated Task runtime when the project declares it."""
    try:
        configuration = tomllib.loads(
            (project_root / "pyproject.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        return []
    optional = configuration.get("project", {}).get("optional-dependencies", {})
    if isinstance(optional, dict) and isinstance(optional.get("participate"), list):
        return ["--extra", "participate"]
    return []


def _requirements_refresh_args(project_root: Path) -> list[str]:
    """Refresh dynamic requirements.txt metadata without invalidating the whole uv cache."""
    if requirements_mode(project_root) != "requirements":
        return []
    try:
        configuration = tomllib.loads(
            (project_root / "pyproject.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        return ["--refresh"]
    project = configuration.get("project", {})
    name = project.get("name") if isinstance(project, dict) else None
    if isinstance(name, str) and name.strip():
        return ["--refresh-package", name.strip()]
    return ["--refresh"]


def _verify_task_requirements(
    project_root: Path,
    environment_path: Path,
) -> list[str]:
    """Confirm every direct requirements.txt pin exists at the requested version."""
    requirements = read_task_requirements(project_root)
    if requirements["mode"] != "requirements":
        return []
    dependencies = [str(value) for value in requirements["dependencies"]]
    expected = [value.split("==", 1) for value in dependencies]
    python = environment_path / "bin" / "python"
    if not python.is_file():
        raise FileNotFoundError("The synchronized Python environment is missing its interpreter.")
    verification = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json, sys\n"
                "from importlib.metadata import PackageNotFoundError, version\n"
                "result = {}\n"
                "for name in sys.argv[1:]:\n"
                "    try:\n"
                "        result[name] = version(name)\n"
                "    except PackageNotFoundError:\n"
                "        result[name] = None\n"
                "print(json.dumps(result, sort_keys=True))\n"
            ),
            *[name for name, _ in expected],
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if verification.returncode != 0:
        detail = verification.stderr.strip() or verification.stdout.strip()
        raise RuntimeError(f"Dependency verification failed: {detail or 'unknown error'}")
    try:
        installed = json.loads(verification.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Dependency verification returned invalid output.") from error
    mismatches = [
        f"{name} (expected {wanted}, installed {installed.get(name) or 'missing'})"
        for name, wanted in expected
        if installed.get(name) != wanted
        and not str(installed.get(name) or "").startswith(f"{wanted}+")
    ]
    if mismatches:
        raise RuntimeError(
            "The synchronized environment does not match requirements.txt: "
            + ", ".join(mismatches)
        )
    return [f"{name}=={installed[name]}" for name, _ in expected]


def _ensure_environment_pip(environment_path: Path) -> bool:
    """Provide ``pip`` inside the selected uv environment without using the network.

    ``uv sync`` intentionally does not seed pip. Without a venv-local executable,
    an interactive shell can resolve the container's system pip even though its
    Python and PATH point at the selected environment.
    """
    python = environment_path / "bin" / "python"
    pip = environment_path / "bin" / "pip"
    if pip.is_file():
        return False
    if not python.is_file():
        raise FileNotFoundError("The synchronized Python environment is missing its interpreter.")
    if not os.access(python, os.X_OK):
        return False
    result = subprocess.run(
        [str(python), "-m", "ensurepip", "--upgrade", "--default-pip"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0 or not pip.is_file():
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            "The synchronized environment could not prepare pip: "
            + (detail or "pip executable was not created")
        )
    return True


def _published_participant_lock(project_root: Path) -> list[str]:
    """Keep an installed participant Release byte-for-byte reproducible."""
    path = project_root / ".fedops-studio" / "task-binding.json"
    if not (project_root / "uv.lock").is_file() or not path.is_file() or path.is_symlink():
        return []
    try:
        binding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if (
        isinstance(binding, dict)
        and binding.get("workspaceRole") == "participant"
        and binding.get("registryStatus") == "published"
    ):
        return ["--locked"]
    return []


def uv_process_environment(environment_path: Path) -> dict[str, str]:
    """Return the stable uv process policy for a host-mounted Workspace."""
    environment = os.environ.copy()
    environment["UV_PROJECT_ENVIRONMENT"] = str(environment_path)
    environment["UV_LINK_MODE"] = "copy"
    return environment


def default_python_version(project_root: Path) -> str:
    version_file = project_root / ".python-version"
    if version_file.is_file():
        lines = version_file.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            match = re.match(r"^(3\.(?:10|11|12|13|14))", lines[0])
            if match:
                return match.group(1)
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def list_environments(
    project_root: Path,
    owner_id: str,
    owner_type: str = "federated-task",
) -> dict[str, object]:
    _validate_owner(owner_type, owner_id)
    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, owner_type, owner_id)
        items = [_serialize(project_root, item) for item in registry["items"]]
    return {
        "items": items,
        "ownerType": owner_type,
        "ownerId": owner_id,
        "uv": uv_information(),
        "source": "local-runtime-metadata",
    }


def create_environment(
    project_root: Path,
    owner_id: str,
    *,
    name: str,
    python_version: str,
    owner_type: str = "federated-task",
    select: bool = True,
) -> dict[str, object]:
    _validate_owner(owner_type, owner_id)
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Environment name is required.")
    if len(normalized_name) > 48:
        raise ValueError("Environment name must be 48 characters or fewer.")
    if not PYTHON_VERSION.fullmatch(python_version):
        raise ValueError("Python version must be one of 3.10 through 3.14.")

    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, owner_type, owner_id)
        if any(str(item["name"]).casefold() == normalized_name.casefold() for item in registry["items"]):
            raise ValueError("An environment with this name already exists.")
        environment_id = f"env-{uuid.uuid4().hex[:12]}"
        if select:
            for item in registry["items"]:
                item["selected"] = False
        item = _new_item(
            environment_id=environment_id,
            owner_type=owner_type,
            owner_id=owner_id,
            name=normalized_name,
            python_version=python_version,
            relative_path=f".fedops-studio/environments/{environment_id}",
            selected=select,
        )
        registry["items"].append(item)
        _save(project_root, registry)
        return _serialize(project_root, item)


def select_environment(
    project_root: Path,
    owner_id: str,
    environment_id: str,
    owner_type: str = "federated-task",
) -> dict[str, object]:
    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, owner_type, owner_id)
        selected = _find(registry, environment_id)
        for item in registry["items"]:
            item["selected"] = item["environmentId"] == environment_id
        _save(project_root, registry)
        return _serialize(project_root, selected)


def delete_environment(
    project_root: Path,
    owner_id: str,
    environment_id: str,
    owner_type: str = "federated-task",
    runtime_namespace: str = "device",
) -> dict[str, object]:
    """Delete one uv environment directory and its durable registry item."""
    active = RUN_MANAGER.active_for(owner_id, environment_id, runtime_namespace)
    if active:
        raise RuntimeError(f"The environment is used by an active action ({active['runId']}).")

    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, owner_type, owner_id)
        item = _find(registry, environment_id)
        expected_relative_paths = {
            Path(".venv"),
            Path(".fedops-studio/environments") / environment_id,
        }
        relative_path = Path(str(item["path"]))
        if relative_path not in expected_relative_paths:
            raise ValueError("The Python environment path is not managed by Agent Studio.")
        environment_path = _environment_path(project_root, item)
        if environment_path == project_root.resolve():
            raise ValueError("The project directory cannot be deleted as an environment.")

        if environment_path.exists():
            if environment_path.is_dir():
                shutil.rmtree(environment_path)
            else:
                environment_path.unlink()

        was_selected = bool(item.get("selected"))
        registry["items"] = [
            candidate
            for candidate in registry["items"]
            if candidate.get("environmentId") != environment_id
        ]
        if not registry["items"]:
            registry["items"].append(
                _new_item(
                    environment_id="env-default",
                    owner_type=owner_type,
                    owner_id=owner_id,
                    name="Default",
                    python_version=default_python_version(project_root),
                    relative_path=".venv",
                    selected=True,
                )
            )
        elif was_selected:
            registry["items"][0]["selected"] = True
        _save(project_root, registry)

    return list_environments(project_root, owner_id, owner_type)


def sync_environment_run(
    project_root: Path,
    owner_id: str,
    environment_id: str,
    owner_type: str = "federated-task",
    runtime_namespace: str = "device",
) -> dict[str, object]:
    if not (project_root / "pyproject.toml").is_file():
        raise FileNotFoundError("pyproject.toml is required before syncing an environment.")
    if requirements_mode(project_root) == "requirements":
        requirements = read_task_requirements(project_root)
        if not requirements["valid"]:
            raise ValueError(str(requirements["error"]))
    uv = require_uv()
    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, owner_type, owner_id)
        item = _find(registry, environment_id)
        python_version = str(item["pythonVersion"])
        environment_path = _environment_path(project_root, item)

    process_environment = uv_process_environment(environment_path)
    argv = [
        uv,
        "sync",
        "--python",
        python_version,
        *_published_participant_lock(project_root),
        *_requirements_refresh_args(project_root),
        *_sync_extras(project_root),
    ]

    def mark_syncing(_: ManagedRun) -> None:
        _update_item(project_root, owner_type, owner_id, environment_id, status="syncing", error=None)

    def mark_ready(run: ManagedRun) -> None:
        pip_seeded = _ensure_environment_pip(environment_path)
        verified = _verify_task_requirements(project_root, environment_path)
        lock_hash = _lock_hash(project_root)
        if lock_hash is None:
            raise FileNotFoundError("uv sync completed without creating uv.lock.")
        version = uv_information()["version"]
        _update_item(
            project_root,
            owner_type,
            owner_id,
            environment_id,
            status="ready",
            lockHash=lock_hash,
            projectHash=_project_hash(project_root),
            uvVersion=version,
            lastSyncedAt=utc_now(),
            error=None,
        )
        if verified:
            RUN_MANAGER.append(
                run,
                f"\nVerified {len(verified)} direct Task dependencies:\n"
                + "\n".join(f"  - {dependency}" for dependency in verified)
                + "\n",
            )
        if pip_seeded:
            RUN_MANAGER.append(
                run,
                "\nPrepared the environment-local pip command for interactive terminals.\n",
            )
        RUN_MANAGER.append(run, "\nEnvironment and uv.lock are synchronized.\n")

    def mark_failed(_: ManagedRun, error: Exception) -> None:
        _update_item(
            project_root,
            owner_type,
            owner_id,
            environment_id,
            status="error",
            error=str(error),
        )

    def mark_cancelled(_: ManagedRun) -> None:
        _update_item(
            project_root,
            owner_type,
            owner_id,
            environment_id,
            status="outdated",
            error=None,
        )

    return RUN_MANAGER.start_command(
        kind="environment-sync",
        local_project_id=owner_id,
        environment_id=environment_id,
        argv=argv,
        cwd=project_root,
        environment=process_environment,
        initial_output=f"Synchronizing {item['name']} with Python {python_version}...\n",
        before_command=mark_syncing,
        after_success=mark_ready,
        after_failure=mark_failed,
        after_cancel=mark_cancelled,
        namespace=runtime_namespace,
    )


def selected_environment_ready(project_root: Path, owner_id: str) -> bool:
    try:
        selected = selected_environment(project_root, owner_id)
    except (FileNotFoundError, ValueError):
        return False
    return selected["status"] == "ready"


def selected_environment(
    project_root: Path,
    owner_id: str,
    environment_id: str | None = None,
) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, "federated-task", owner_id)
        if environment_id:
            item = _find(registry, environment_id)
        else:
            item = next((candidate for candidate in registry["items"] if candidate["selected"]), None)
            if item is None:
                raise FileNotFoundError("No Python environment is selected.")
        return _serialize(project_root, item)


def uv_run_command(
    project_root: Path,
    owner_id: str,
    command: list[str],
    environment_id: str | None = None,
) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    item = selected_environment(project_root, owner_id, environment_id)
    if item["status"] != "ready":
        raise FileNotFoundError("Sync the selected Python environment before running this action.")
    process_environment = uv_process_environment(project_root / str(item["path"]))
    argv = [require_uv(), "run", "--locked", "--no-sync", *command]
    return argv, process_environment, item


def uv_information() -> dict[str, object]:
    executable = shutil.which(os.getenv("STUDIO_UV_BIN", "uv"))
    if not executable:
        return {"available": False, "version": None, "executable": None}
    try:
        output = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "version": None, "executable": executable}
    version = output.removeprefix("uv ").strip() or output
    return {"available": True, "version": version, "executable": executable}


def require_uv() -> str:
    executable = shutil.which(os.getenv("STUDIO_UV_BIN", "uv"))
    if not executable:
        raise FileNotFoundError("uv is not installed in the Studio runtime.")
    return executable


def _new_item(
    *,
    environment_id: str,
    owner_type: str,
    owner_id: str,
    name: str,
    python_version: str,
    relative_path: str,
    selected: bool,
) -> dict[str, Any]:
    return {
        "environmentId": environment_id,
        "ownerType": owner_type,
        "ownerId": owner_id,
        "name": name,
        "pythonVersion": python_version,
        "path": relative_path,
        "selected": selected,
        "status": "missing",
        "lockHash": None,
        "projectHash": None,
        "uvVersion": None,
        "createdAt": utc_now(),
        "lastSyncedAt": None,
        "error": None,
    }


def _load_or_create(project_root: Path, owner_type: str, owner_id: str) -> dict[str, Any]:
    _validate_owner(owner_type, owner_id)
    path = project_root / REGISTRY_PATH
    if path.is_file():
        try:
            registry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Python environment metadata is invalid: {error}") from error
        if registry.get("version") != REGISTRY_VERSION or not isinstance(registry.get("items"), list):
            raise ValueError("Python environment metadata has an unsupported format.")
        return registry

    registry = {
        "version": REGISTRY_VERSION,
        "items": [
            _new_item(
                environment_id="env-default",
                owner_type=owner_type,
                owner_id=owner_id,
                name="Default",
                python_version=default_python_version(project_root),
                relative_path=".venv",
                selected=True,
            )
        ],
    }
    _save(project_root, registry)
    return registry


def _save(project_root: Path, registry: dict[str, Any]) -> None:
    path = project_root / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _find(registry: dict[str, Any], environment_id: str) -> dict[str, Any]:
    item = next(
        (candidate for candidate in registry["items"] if candidate.get("environmentId") == environment_id),
        None,
    )
    if item is None:
        raise FileNotFoundError("The Python environment was not found.")
    return item


def _update_item(
    project_root: Path,
    owner_type: str,
    owner_id: str,
    environment_id: str,
    **values: object,
) -> None:
    with _REGISTRY_LOCK:
        registry = _load_or_create(project_root, owner_type, owner_id)
        item = _find(registry, environment_id)
        item.update(values)
        _save(project_root, registry)


def _serialize(project_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(item)
    environment_path = _environment_path(project_root, item)
    current_hash = _lock_hash(project_root)
    stored_hash = item.get("lockHash")
    current_project_hash = _project_hash(project_root)
    stored_project_hash = item.get("projectHash")
    project_synced = bool(stored_project_hash and current_project_hash == stored_project_hash)
    python_ready = (environment_path / "bin" / "python").is_file()
    if item.get("status") == "syncing":
        status = "syncing"
    elif python_ready and stored_hash and current_hash == stored_hash and project_synced:
        status = "ready"
    elif item.get("status") == "error":
        status = "error"
    elif python_ready:
        status = "outdated"
    else:
        status = "missing"
    serialized["status"] = status
    serialized["lockStatus"] = "missing" if current_hash is None else (
        "synced" if stored_hash == current_hash and project_synced else "outdated"
    )
    serialized.pop("projectHash", None)
    return serialized


def _environment_path(project_root: Path, item: dict[str, Any]) -> Path:
    target = (project_root / str(item["path"])).resolve()
    try:
        target.relative_to(project_root.resolve())
    except ValueError as error:
        raise ValueError("Python environment path escapes the project.") from error
    return target


def _lock_hash(project_root: Path) -> str | None:
    lock = project_root / "uv.lock"
    if not lock.is_file():
        return None
    return hashlib.sha256(lock.read_bytes()).hexdigest()


def _project_hash(project_root: Path) -> str | None:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    digest = hashlib.sha256()
    for path in (pyproject, project_root / REQUIREMENTS_PATH):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _validate_owner(owner_type: str, owner_id: str) -> None:
    if owner_type not in SUPPORTED_OWNER_TYPES:
        raise ValueError("Unsupported Python environment owner type.")
    if not owner_id.strip():
        raise ValueError("Python environment owner ID is required.")
