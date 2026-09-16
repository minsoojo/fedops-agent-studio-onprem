"""FedOps Federated Task creation and local Task contract validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

from .environments import uv_run_command
from .folder import task_data_inventory
from .jobs import RUN_MANAGER

TASK_SCHEMA_VERSIONS = {1, 2, 3}
MODULE_NAME = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
RELEASE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_RELEASE_FILE_BYTES = 128 * 1024 * 1024
MAX_RELEASE_BYTES = 512 * 1024 * 1024
BASELINE_METADATA_PATH = Path(".fedops-studio/baseline.json")
FORBIDDEN_RELEASE_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".fedops-studio",
    "dataset",
    "datasets",
}
_CACHE_LOCK = threading.Lock()


def workspace_directory_name(name: str) -> str:
    """Return a discoverable, path-safe directory for a user-facing Task name."""
    normalized = unicodedata.normalize("NFKC", name).strip().lower()
    if not normalized:
        raise ValueError("Task name is required.")
    slug = "".join(
        character if character.isalnum() else "-" for character in normalized
    )
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError("Task name must contain at least one letter or number.")
    return slug[:64].rstrip("-")


def read_task_contract(project_root: Path) -> dict[str, Any] | None:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        configuration = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    contract = configuration.get("tool", {}).get("fedops", {}).get("task")
    return contract if isinstance(contract, dict) else None


def validate_task_contract(project_root: Path) -> tuple[dict[str, Any], list[str]]:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ValueError("pyproject.toml is missing.")
    try:
        configuration = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"pyproject.toml is invalid: {error}") from error

    project = configuration.get("project")
    if not isinstance(project, dict) or not project.get("name"):
        raise ValueError("[project].name is missing from pyproject.toml.")
    contract = configuration.get("tool", {}).get("fedops", {}).get("task")
    if not isinstance(contract, dict):
        raise ValueError(  # noqa: TRY004 - invalid task content is a value error
            "[tool.fedops.task] is missing. This is not a FedOps Federated Task."
        )
    schema_version = contract.get("schema-version")
    if schema_version not in TASK_SCHEMA_VERSIONS:
        raise ValueError("tool.fedops.task.schema-version must be 1, 2, or 3.")
    if contract.get("task-type") != "silo":
        raise ValueError("tool.fedops.task.task-type must be 'silo'.")
    runtime_module = contract.get("runtime-module")
    if not isinstance(runtime_module, str) or not MODULE_NAME.fullmatch(runtime_module):
        raise ValueError(
            "tool.fedops.task.runtime-module must be a valid Python module."
        )

    referenced_files = {
        "Model": contract.get("model-file"),
        "Data preparation": contract.get("data-preparation-file"),
        "Task config": contract.get("config-file"),
    }
    missing = [
        label
        for label, path in referenced_files.items()
        if not path or not (project_root / str(path)).is_file()
    ]
    if missing:
        raise ValueError(
            f"FedOps Task contract files are missing: {', '.join(missing)}"
        )
    return contract, [
        f"✓ FedOps Task: {project['name']}",
        f"✓ Task schema: {schema_version}",
        f"✓ Runtime module: {runtime_module}",
        "✓ Model, data preparation, and config files",
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as content:
        while chunk := content.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _release_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("The Baseline release contains an invalid file path.")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or value != candidate.as_posix():
        raise ValueError(f"The Baseline release path is unsafe: {value}")
    if any(
        part in {"", ".", ".."} or part in FORBIDDEN_RELEASE_PARTS
        for part in candidate.parts
    ):
        raise ValueError(f"The Baseline release path is unsafe: {value}")
    return candidate


def _descriptor(value: object, *, manifest: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("The Baseline download descriptor is invalid.")
    path = _release_path(value.get("path"))
    if manifest and path.as_posix() != "baseline-manifest.json":
        raise ValueError("The Baseline manifest descriptor has an invalid path.")
    size = value.get("size")
    maximum = MAX_MANIFEST_BYTES if manifest else MAX_RELEASE_FILE_BYTES
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or size > maximum
    ):
        raise ValueError(
            f"The Baseline release has an invalid size for {path.as_posix()}."
        )
    checksum = value.get("sha256")
    if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
        raise ValueError(
            f"The Baseline release has an invalid checksum for {path.as_posix()}."
        )
    download_ref = value.get("artifactId") or value.get("url")
    if not isinstance(download_ref, str) or not download_ref:
        raise ValueError(
            f"The Baseline release has no download reference for {path.as_posix()}."
        )
    if not isinstance(value.get("contentType"), str) or not value.get("contentType"):
        raise ValueError(
            f"The Baseline release has no content type for {path.as_posix()}."
        )
    if not isinstance(value.get("role"), str) or not value.get("role"):
        raise ValueError(f"The Baseline release has no role for {path.as_posix()}.")
    if not isinstance(value.get("editable"), bool):
        raise ValueError(
            f"The Baseline release has an invalid edit policy for {path.as_posix()}."
        )
    return {
        **value,
        "path": path.as_posix(),
        "size": size,
        "sha256": checksum,
        "downloadRef": download_ref,
    }


def _normalize_download_session(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("FedOps returned an invalid Baseline download session.")
    release = value.get("release")
    if not isinstance(release, dict):
        raise ValueError("The Baseline download session has no release metadata.")
    name = release.get("name")
    version = release.get("version")
    revision = release.get("revision")
    if not isinstance(name, str) or not RELEASE_SEGMENT.fullmatch(name):
        raise ValueError("The Baseline release name is invalid.")
    if not isinstance(version, str) or not RELEASE_SEGMENT.fullmatch(version):
        raise ValueError("The Baseline release version is invalid.")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("The Baseline release revision is invalid.")
    manifest = _descriptor(value.get("manifest"), manifest=True)
    files_value = value.get("files")
    if not isinstance(files_value, list) or not files_value:
        raise ValueError("The Baseline download session has no files.")
    files = [_descriptor(file) for file in files_value]
    paths = [file["path"] for file in files]
    if len(paths) != len(set(paths)):
        raise ValueError("The Baseline download session contains duplicate paths.")
    if sum(file["size"] for file in files) > MAX_RELEASE_BYTES:
        raise ValueError("The Baseline release is larger than the Studio safety limit.")
    if release.get("fileCount") != len(files):
        raise ValueError(
            "The Baseline release file count does not match its download session."
        )
    if release.get("totalSize") != sum(file["size"] for file in files):
        raise ValueError(
            "The Baseline release size does not match its download session."
        )
    if release.get("manifestSha256") != manifest["sha256"]:
        raise ValueError("The Baseline release manifest checksum is inconsistent.")
    return {
        "release": {**release, "name": name, "version": version, "revision": revision},
        "manifest": manifest,
        "files": files,
    }


def _load_release_manifest(path: Path, session: dict[str, Any]) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "The downloaded Baseline manifest is not valid JSON."
        ) from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, 2}:
        raise ValueError("The downloaded Baseline manifest schema is unsupported.")
    baseline = manifest.get("baseline")
    release = session["release"]
    if release.get("schemaVersion") != manifest.get("schema_version"):
        raise ValueError("The Baseline release schema does not match its manifest.")
    if not isinstance(baseline, dict) or (
        baseline.get("name") != release["name"]
        or baseline.get("release_version") != release["version"]
        or baseline.get("template_revision") != release["revision"]
    ):
        raise ValueError(
            "The downloaded Baseline manifest does not match the selected release."
        )
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list) or not manifest_files:
        raise ValueError("The downloaded Baseline manifest has no files.")
    session_by_path = {file["path"]: file for file in session["files"]}
    seen: set[str] = set()
    for value in manifest_files:
        if not isinstance(value, dict):
            raise ValueError(
                "The downloaded Baseline manifest has an invalid file entry."
            )
        path_value = _release_path(value.get("path")).as_posix()
        if path_value in seen:
            raise ValueError(
                "The downloaded Baseline manifest contains duplicate paths."
            )
        seen.add(path_value)
        descriptor = session_by_path.get(path_value)
        if not descriptor or (
            descriptor["size"] != value.get("size")
            or descriptor["sha256"] != value.get("sha256")
            or descriptor.get("contentType") != value.get("content_type")
            or descriptor.get("role") != value.get("role")
            or descriptor.get("editable") != bool(value.get("editable"))
        ):
            raise ValueError(
                f"The Baseline download session differs from its manifest: {path_value}"
            )
    if seen != set(session_by_path):
        raise ValueError(
            "The Baseline download session file set differs from its manifest."
        )
    return manifest


def _verify_file(path: Path, descriptor: dict[str, Any]) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"The Baseline artifact is missing: {descriptor['path']}")
    if (
        path.stat().st_size != descriptor["size"]
        or _sha256(path) != descriptor["sha256"]
    ):
        raise ValueError(
            f"The Baseline artifact failed verification: {descriptor['path']}"
        )


def _verify_cached_release(cache: Path, session: dict[str, Any]) -> None:
    manifest_path = cache / "baseline-manifest.json"
    _verify_file(manifest_path, session["manifest"])
    _load_release_manifest(manifest_path, session)
    for descriptor in session["files"]:
        _verify_file(
            cache.joinpath(*PurePosixPath(descriptor["path"]).parts), descriptor
        )
    validate_task_contract(cache)


def _prepare_release_cache(
    cache_root: Path,
    session: dict[str, Any],
    download: Callable[[str, Path, int], None],
) -> Path:
    release = session["release"]
    cache = (
        cache_root.expanduser().resolve()
        / release["name"]
        / release["version"]
        / f"r{release['revision']}-{session['manifest']['sha256'][:12]}"
    )
    with _CACHE_LOCK:
        if cache.is_dir():
            try:
                _verify_cached_release(cache, session)
                return cache
            except ValueError:
                shutil.rmtree(cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".baseline-download-", dir=cache.parent))
        try:
            manifest_path = staging / "baseline-manifest.json"
            download(
                session["manifest"]["downloadRef"],
                manifest_path,
                session["manifest"]["size"],
            )
            _verify_file(manifest_path, session["manifest"])
            _load_release_manifest(manifest_path, session)
            for descriptor in session["files"]:
                destination = staging.joinpath(*PurePosixPath(descriptor["path"]).parts)
                download(descriptor["downloadRef"], destination, descriptor["size"])
                _verify_file(destination, descriptor)
            validate_task_contract(staging)
            staging.replace(cache)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return cache


def create_workspace_from_release(
    workspace: Path,
    name: str,
    cache_root: Path,
    download_session: object,
    download: Callable[[str, Path, int], None],
    runtime_namespace: str = "device",
) -> dict[str, object]:
    """Create a Task from a verified FedOps-owned immutable Baseline release."""
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    session = _normalize_download_session(download_session)
    baseline = _prepare_release_cache(cache_root, session, download)

    directory_name = workspace_directory_name(name)
    target = workspace / directory_name
    if target.exists():
        raise FileExistsError(f"Workspace already exists: {directory_name}")

    staging_root = Path(tempfile.mkdtemp(prefix=".studio-create-", dir=workspace))
    staging_target = staging_root / directory_name
    try:
        shutil.copytree(
            baseline,
            staging_target,
            ignore=shutil.ignore_patterns("baseline-manifest.json"),
        )
        metadata_path = staging_target / BASELINE_METADATA_PATH
        metadata_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(baseline / "baseline-manifest.json", metadata_path)
        validate_task_contract(staging_target)
        staging_target.replace(target)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    local_project_id = f"local:{directory_name}"
    release = session["release"]
    release_label = (
        f"{release['name']}@{release['version']} (revision {release['revision']})"
    )
    # A newly created directory is a new Task instance even when an earlier
    # deleted Task used the same local name. Remove orphaned history first.
    RUN_MANAGER.remove_for(local_project_id, runtime_namespace)
    return RUN_MANAGER.record_completed(
        kind="create",
        local_project_id=local_project_id,
        command=f"install FedOps Baseline {release_label}",
        output=(
            f"Verified FedOps Baseline: {release_label}\n"
            "Created a Federated Task without cloning a Git repository.\n"
            f"Workspace: ./{directory_name}\n"
        ),
        result_local_project_id=local_project_id,
        namespace=runtime_namespace,
    )


def validate_project_run(
    project_root: Path,
    local_project_id: str,
    environment_id: str | None = None,
    runtime_namespace: str = "device",
) -> dict[str, object]:
    contract, messages = validate_task_contract(project_root)
    runtime_module = str(contract["runtime-module"])
    schema_version = int(contract["schema-version"])
    action = (
        ["validate"]
        if schema_version == 1
        else ["check-readiness", "--mode", "release"]
    )
    argv, environment, selected = uv_run_command(
        project_root,
        local_project_id,
        ["python", "-m", runtime_module, *action],
        environment_id,
    )
    return RUN_MANAGER.start_command(
        kind="validate",
        local_project_id=local_project_id,
        environment_id=str(selected["environmentId"]),
        argv=argv,
        cwd=project_root,
        environment=environment,
        initial_output="\n".join(messages) + "\nRunning FedOps Task validation...\n",
        namespace=runtime_namespace,
    )


def _metadata_root(project_root: Path) -> Path:
    root = project_root / ".fedops-studio"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def read_task_binding(project_root: Path) -> dict[str, Any] | None:
    path = project_root / ".fedops-studio" / "task-binding.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        value if isinstance(value, dict) and value.get("schemaVersion") == 1 else None
    )


def write_task_binding(project_root: Path, task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("taskId") or "").strip()
    runtime_key = str(task.get("runtimeKey") or task.get("title") or "").strip()
    display_name = str(task.get("displayName") or task.get("title") or "").strip()
    if not task_id or not runtime_key or not display_name:
        raise ValueError("FedOps Web returned an incomplete Task identity.")
    current = read_task_binding(project_root)
    if current and current.get("taskId") != task_id:
        raise FileExistsError(
            "This Workspace is already linked to a different FedOps Web Draft."
        )
    runtime_contract = (
        task.get("runtimeContract")
        if isinstance(task.get("runtimeContract"), dict)
        else {"name": "legacy-v1", "schemaVersion": 1}
    )
    contract = read_task_contract(project_root)
    workspace_schema = contract.get("schema-version") if contract else None
    expected_schema = runtime_contract.get("schemaVersion")
    if workspace_schema is not None and expected_schema in {1, 2, 3}:
        if int(workspace_schema) != int(expected_schema):
            raise ValueError(
                "This Workspace and FedOps Web Task use different Runtime contracts. "
                "Legacy Tasks are not upgraded to the new Federated Task contract automatically."
            )
    permissions = (
        task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
    )
    participation = (
        task.get("participation") if isinstance(task.get("participation"), dict) else {}
    )
    requested_role = str(
        task.get("workspaceRole") or participation.get("role") or ""
    ).strip()
    workspace_role = (
        requested_role
        if requested_role in {"owner", "admin", "participant"}
        else "owner"
        if permissions.get("isOwner") or not task.get("releaseId")
        else "admin"
        if permissions.get("isAdmin") or permissions.get("canManage")
        else "participant"
    )
    binding = {
        "schemaVersion": 1,
        "taskId": task_id,
        "runtimeKey": runtime_key,
        "displayName": display_name,
        "workspaceRole": workspace_role,
        "taskCategory": task.get("taskCategory"),
        "dataModality": task.get("dataModality"),
        "dataType": task.get("dataType"),
        "modelType": task.get("modelType"),
        "primaryModel": task.get("primaryModel")
        if isinstance(task.get("primaryModel"), dict)
        else None,
        "ownerHandle": task.get("ownerHandle"),
        "slug": task.get("slug"),
        "registryStatus": str(task.get("registryStatus") or "draft"),
        "runtimeContract": runtime_contract,
        "linkedAt": current.get("linkedAt")
        if current
        else datetime.now(UTC).isoformat(),
    }
    for key in ("releaseId", "modelVersionId", "bundleSha256"):
        if task.get(key):
            binding[key] = task[key]
    path = _metadata_root(project_root) / "task-binding.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return binding


def _parse_readiness_output(output: str, mode: str) -> dict[str, Any]:
    candidates = [
        index for index in range(len(output)) if output.startswith("{", index)
    ]
    for index in reversed(candidates):
        try:
            report = json.loads(output[index:])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(report, dict)
            and report.get("ok") is True
            and report.get("mode") == mode
        ):
            return report
    raise ValueError(
        f"The {mode} readiness command did not produce a valid JSON report."
    )


def _persist_readiness(
    project_root: Path,
    mode: str,
    output: str,
    data_path: str | None = None,
) -> None:
    report = _parse_readiness_output(output, mode)
    if data_path:
        report["dataPath"] = data_path
        selected_data = Path(data_path).expanduser().resolve()
        if selected_data.is_dir():
            report["dataFingerprint"] = task_data_inventory(selected_data)[
                "fingerprint"
            ]
    path = _metadata_root(project_root) / f"readiness-{mode}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(path, 0o600)


def task_action_run(
    project_root: Path,
    local_project_id: str,
    action: str,
    environment_id: str | None = None,
    runtime_namespace: str = "device",
    data_path: str | None = None,
) -> dict[str, object]:
    contract, messages = validate_task_contract(project_root)
    if int(contract["schema-version"]) < 2:
        raise ValueError(f"{action} requires a Federated Task schema v2 Workspace.")
    runtime_module = str(contract["runtime-module"])
    binding = read_task_binding(project_root)
    environment: dict[str, str]
    arguments: list[str]
    readiness_mode: str | None = None
    if action == "local-train":
        if not data_path:
            raise ValueError(
                "Local Train requires a Workspace-visible local data path."
            )
        arguments = ["local-train", "--data-root", data_path]
    elif action == "release-readiness":
        arguments = ["check-readiness", "--mode", "release"]
        readiness_mode = "release"
    elif action == "participation-readiness":
        if not data_path:
            raise ValueError(
                "Participation Readiness requires a local data binding path."
            )
        arguments = [
            "check-readiness",
            "--mode",
            "participation",
            "--data-root",
            data_path,
            "--samples",
            "8",
            "--max-batches",
            "1",
        ]
        readiness_mode = "participation"
    else:
        raise ValueError(f"Unsupported Federated Task action: {action}")
    argv, environment, selected = uv_run_command(
        project_root,
        local_project_id,
        ["python", "-m", runtime_module, *arguments],
        environment_id,
    )
    if binding:
        environment.update(
            {
                "FEDOPS_TASK_ID": str(binding["taskId"]),
            }
        )

    progress_stop = threading.Event()

    def before_command(run: Any) -> None:
        if action == "release-readiness":
            # A new Check supersedes the previous result immediately. If this
            # run fails, no stale success file may keep submission enabled.
            (project_root / ".fedops-studio" / "readiness-release.json").unlink(
                missing_ok=True
            )
            return
        if action != "participation-readiness":
            return

        def report_progress() -> None:
            stages = (
                ("preparing", 16, "Validating Federated Task files"),
                ("loading-data", 32, "Loading the bounded local data probe"),
                ("training", 58, "Testing one bounded local training batch"),
                ("evaluating", 78, "Checking evaluation and parameter exchange"),
                ("exporting", 92, "Checking Tool AI and participation output"),
            )
            for stage, percent, message in stages:
                if progress_stop.wait(0.8):
                    return
                RUN_MANAGER.update_progress(
                    run,
                    stage=stage,
                    percent=percent,
                    message=message,
                )

        threading.Thread(
            target=report_progress,
            daemon=True,
            name=f"participation-readiness-progress-{run.run_id[:8]}",
        ).start()

    def after_success(run: Any) -> None:
        progress_stop.set()
        if readiness_mode:
            _persist_readiness(project_root, readiness_mode, run.output, data_path)
            RUN_MANAGER.update_progress(
                run,
                stage="completed",
                percent=100,
                message=(
                    "Release Readiness passed"
                    if readiness_mode == "release"
                    else "Participation Readiness passed"
                ),
            )

    return RUN_MANAGER.start_command(
        kind=action,
        local_project_id=local_project_id,
        environment_id=str(selected["environmentId"]),
        argv=argv,
        cwd=project_root,
        environment=environment,
        initial_output="\n".join(messages) + f"\nRunning {action}...\n",
        initial_progress={
            "schemaVersion": 1,
            "stage": "preparing",
            "percent": 5,
            "message": (
                "Checking Registry Release requirements"
                if action == "release-readiness"
                else "Checking local participation requirements"
            ),
            "timestamp": datetime.now(UTC).isoformat(),
            "metrics": {},
        }
        if readiness_mode
        else None,
        before_command=before_command,
        after_success=after_success,
        always=progress_stop.set,
        namespace=runtime_namespace,
    )


def _source_files(project_root: Path) -> list[Path]:
    """Return releasable files without traversing managed runtime directories.

    ``Path.rglob`` still descends into ``.venv`` and only filters the returned
    paths afterwards. On a host-mounted Docker Workspace that turns a simple
    menu load into a multi-second scan of every installed package. Prune those
    directories before traversal while preserving the existing release rules.
    """
    files: list[Path] = []
    for current, directories, names in os.walk(
        project_root, topdown=True, followlinks=False
    ):
        current_root = Path(current)
        allowed_directories: list[str] = []
        for name in directories:
            path = current_root / name
            if name in FORBIDDEN_RELEASE_PARTS:
                continue
            relative = path.relative_to(project_root)
            if path.is_symlink():
                raise ValueError(
                    f"Release source contains a symlink: {relative.as_posix()}"
                )
            allowed_directories.append(name)
        directories[:] = allowed_directories

        for name in names:
            if name in FORBIDDEN_RELEASE_PARTS:
                continue
            path = current_root / name
            relative = path.relative_to(project_root)
            if path.is_symlink():
                raise ValueError(
                    f"Release source contains a symlink: {relative.as_posix()}"
                )
            if not path.is_file():
                continue
            if relative.as_posix() == "model_release/model.safetensors":
                continue
            if path.stat().st_size > MAX_RELEASE_FILE_BYTES:
                raise ValueError(
                    f"Release file exceeds its size limit: {relative.as_posix()}"
                )
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(project_root).as_posix())


def source_fingerprint(project_root: Path) -> str:
    entries = [
        [path.relative_to(project_root).as_posix(), path.stat().st_size, _sha256(path)]
        for path in _source_files(project_root)
    ]
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _release_role(path: str) -> str:
    if path == "README.md":
        return "documentation"
    if path == "requirements.txt":
        return "task_dependencies"
    if path == "federated_task/model.py" or path.endswith("local_training/model.py"):
        return "model_code"
    if path == "federated_task/data_preparation.py" or path.endswith(
        "local_training/data_preparation.py"
    ):
        return "data_preparation"
    if path in {
        "federated_task/training.py",
        "federated_task/local_training/train.py",
        "federated_task/local_training/training.py",
    }:
        return "local_training"
    if path.endswith("config.toml") or path == "federated_task/conf/config.yaml":
        return "task_config"
    if path in {
        "federated_task/manifest.json",
        "federated_task/agent_tool/manifest.json",
        "federated_task/tool_ai/manifest.json",
    }:
        return "tool_manifest"
    if path in {
        "federated_task/tool.py",
        "federated_task/agent_tool/inference.py",
        "federated_task/tool_ai/tool.py",
    }:
        return "tool_inference"
    if path == "model_release/manifest.json":
        return "model_release_manifest"
    return "runtime"


def _release_editable(path: str) -> bool:
    """Preserve the Baseline's Owner/FedOps file boundary in a Task Release."""
    if path in {
        "README.md",
        "requirements.txt",
        "federated_task/model.py",
        "federated_task/data_preparation.py",
        "federated_task/training.py",
        "federated_task/manifest.json",
        "federated_task/tool.py",
        "federated_task/conf/config.yaml",
        "federated_task/agent_tool/manifest.json",
        "federated_task/agent_tool/inference.py",
        "federated_task/tool_ai/manifest.json",
        "federated_task/tool_ai/tool.py",
    }:
        return True
    return path.startswith("federated_task/local_training/") and not path.endswith(
        "/__init__.py"
    )


def _baseline_metadata_path(project_root: Path) -> Path:
    """Use hidden Studio metadata for new Tasks and the root file for compatibility."""
    current = project_root / BASELINE_METADATA_PATH
    if current.is_file():
        return current
    previous = project_root / "baseline-manifest.json"
    if previous.is_file():
        return previous
    raise FileNotFoundError("The verified Baseline metadata is missing.")


def _content_type(path: str) -> str:
    return {
        ".json": "application/json",
        ".md": "text/markdown",
        ".py": "text/x-python",
        ".toml": "application/toml",
        ".txt": "text/plain",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
    }.get(Path(path).suffix.lower(), "application/octet-stream")


def release_inputs(project_root: Path) -> dict[str, Any]:
    binding = read_task_binding(project_root)
    if not binding:
        raise ValueError("Link this Workspace to an owned FedOps Web Draft first.")
    readiness_path = project_root / ".fedops-studio" / "readiness-release.json"
    if not readiness_path.is_file():
        raise ValueError(
            "Run and pass Release Readiness before submitting a Candidate."
        )
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(
        _baseline_metadata_path(project_root).read_text(encoding="utf-8")
    )
    baseline_version = str(
        (baseline_manifest.get("baseline") or {}).get("release_version") or ""
    )
    catalog = readiness.get("registryCatalog")
    if baseline_version in {
        "0.8",
        "0.8.0",
        "0.9",
        "0.9.0",
        "0.10",
        "0.10.0",
        "0.11",
        "0.11.0",
    } and (
        not isinstance(catalog, dict)
        or not isinstance(catalog.get("primaryModel"), dict)
    ):
        raise ValueError(
            "This Baseline Release Readiness did not produce Registry catalog metadata. "
            "Run Release Readiness again with the current Baseline."
        )
    fingerprint = source_fingerprint(project_root)
    if (
        readiness.get("sourceFingerprint") != fingerprint
        or readiness.get("ok") is not True
    ):
        raise ValueError(
            "Workspace source changed after Release Readiness. Run it again."
        )
    model_manifest_path = project_root / "model_release" / "manifest.json"
    model_path = project_root / "model_release" / "model.safetensors"
    if not model_manifest_path.is_file() or not model_path.is_file():
        raise ValueError(
            "Local Train must export model_release/model.safetensors first."
        )
    model_manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
    signature = model_manifest.get("parameterSignature", {}).get("fingerprint")
    if (
        model_manifest.get("status") != "ready"
        or model_manifest.get("sha256") != _sha256(model_path)
        or model_manifest.get("size") != model_path.stat().st_size
        or readiness.get("parameterSignatureFingerprint") != signature
    ):
        raise ValueError(
            "Initial Model, model manifest, and readiness identity do not match."
        )
    return {
        "binding": binding,
        "readiness": readiness,
        "sourceFingerprint": fingerprint,
        "modelManifest": model_manifest,
        "modelPath": model_path,
        "catalog": catalog if isinstance(catalog, dict) else {},
    }


def build_release_archive(
    project_root: Path,
    model_version_id: str,
    output_path: Path,
) -> dict[str, Any]:
    inputs = release_inputs(project_root)
    files = []
    for source in _source_files(project_root):
        relative = source.relative_to(project_root).as_posix()
        files.append(
            {
                "path": relative,
                "role": _release_role(relative),
                "contentType": _content_type(relative),
                "editable": _release_editable(relative),
                "size": source.stat().st_size,
                "sha256": _sha256(source),
            }
        )
    baseline_manifest = json.loads(
        _baseline_metadata_path(project_root).read_text(encoding="utf-8")
    )
    baseline = baseline_manifest.get("baseline") or {}
    manifest = {
        "schemaVersion": 1,
        "taskId": inputs["binding"]["taskId"],
        "baseline": {
            "name": baseline.get("name"),
            "version": baseline.get("release_version"),
            "revision": baseline.get("template_revision"),
        },
        "sourceFingerprint": inputs["sourceFingerprint"],
        "modelVersionId": model_version_id,
        "modelRelease": {
            "sha256": inputs["modelManifest"]["sha256"],
            "format": inputs["modelManifest"].get("format"),
            "origin": inputs["modelManifest"].get("origin"),
        },
        "readiness": inputs["readiness"],
        "catalog": inputs["catalog"],
        "files": files,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w") as archive:
        for source in _source_files(project_root):
            relative = source.relative_to(project_root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
        info = zipfile.ZipInfo("release-manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(
            info,
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
        )
    if output_path.stat().st_size > MAX_RELEASE_BYTES:
        output_path.unlink(missing_ok=True)
        raise ValueError("Task Release archive exceeds the Studio size limit.")
    return {
        "path": output_path,
        "size": output_path.stat().st_size,
        "sha256": _sha256(output_path),
        "manifest": manifest,
        **inputs,
    }


def _published_release_binding(
    release: dict[str, Any],
    fallback_name: str,
) -> dict[str, Any]:
    task = release.get("task") if isinstance(release.get("task"), dict) else {}
    model = release.get("model") if isinstance(release.get("model"), dict) else {}
    permissions = (
        task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
    )
    return {
        "taskId": task.get("taskId"),
        "runtimeKey": task.get("runtimeKey") or task.get("title"),
        "displayName": task.get("displayName") or task.get("title") or fallback_name,
        "title": task.get("title") or fallback_name,
        "ownerHandle": task.get("ownerHandle"),
        "slug": task.get("slug"),
        "taskCategory": task.get("taskCategory"),
        "dataModality": task.get("dataModality"),
        "dataType": task.get("dataType"),
        "modelType": task.get("modelType"),
        "primaryModel": task.get("primaryModel"),
        "registryStatus": "published",
        "runtimeContract": task.get("runtimeContract"),
        "workspaceRole": (
            "owner"
            if permissions.get("isOwner")
            else "admin"
            if permissions.get("isAdmin") or permissions.get("canManage")
            else "participant"
        ),
        "releaseId": release.get("releaseId"),
        "modelVersionId": model.get("modelVersionId"),
        "bundleSha256": release.get("bundleSha256"),
    }


def reconcile_existing_published_release(
    project_root: Path,
    release: dict[str, Any],
) -> dict[str, Any]:
    """Refresh identity metadata without overwriting local Task source or model files."""
    current = read_task_binding(project_root)
    if not current:
        raise ValueError("The existing Workspace has no Federated Task binding.")
    incoming = _published_release_binding(release, project_root.name)
    if str(current.get("taskId") or "") != str(incoming.get("taskId") or ""):
        raise ValueError(
            "The existing Workspace belongs to a different Federated Task."
        )

    current_release_id = str(current.get("releaseId") or "")
    incoming_release_id = str(incoming.get("releaseId") or "")
    current_bundle = str(current.get("bundleSha256") or "")
    incoming_bundle = str(incoming.get("bundleSha256") or "")
    update_available = bool(
        current_release_id
        and incoming_release_id
        and current_release_id != incoming_release_id
    ) or bool(current_bundle and incoming_bundle and current_bundle != incoming_bundle)
    if update_available:
        return {
            "binding": current,
            "updated": False,
            "releaseUpdateAvailable": True,
        }

    binding = write_task_binding(project_root, {**current, **incoming})
    return {
        "binding": binding,
        "updated": binding != current,
        "releaseUpdateAvailable": False,
    }


def install_published_release(
    workspace: Path,
    name: str,
    archive_path: Path,
    release: dict[str, Any],
    model_path: Path,
    runtime_namespace: str = "device",
) -> dict[str, object]:
    """Install one exact Published Release and matching model into a local Workspace."""
    directory_name = workspace_directory_name(name)
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / directory_name
    if target.exists():
        raise FileExistsError(f"Workspace already exists: {directory_name}")
    if _sha256(archive_path) != release.get("bundleSha256"):
        raise ValueError(
            "Published Task Release archive checksum does not match metadata."
        )
    model = release.get("model") if isinstance(release.get("model"), dict) else {}
    if _sha256(model_path) != model.get("sha256"):
        raise ValueError("Published model checksum does not match Release metadata.")
    staging_root = Path(tempfile.mkdtemp(prefix=".published-release-", dir=workspace))
    staging_target = staging_root / directory_name
    staging_target.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Published Task Release contains duplicate paths.")
            for info in infos:
                relative = _release_path(info.filename)
                if info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError(
                        f"Published Task Release contains a symlink: {info.filename}"
                    )
                if info.file_size > MAX_RELEASE_FILE_BYTES:
                    raise ValueError(
                        f"Published Task Release file exceeds its limit: {info.filename}"
                    )
                destination = staging_target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        manifest_path = staging_target / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared = {
            str(item.get("path")): item
            for item in manifest.get("files", [])
            if isinstance(item, dict)
        }
        actual = {
            path.relative_to(staging_target).as_posix(): path
            for path in staging_target.rglob("*")
            if path.is_file() and path.name != "release-manifest.json"
        }
        if set(actual) != set(declared):
            raise ValueError(
                "Published Task Release file set does not match its manifest."
            )
        for relative, path in actual.items():
            descriptor = declared[relative]
            if descriptor.get("size") != path.stat().st_size or descriptor.get(
                "sha256"
            ) != _sha256(path):
                raise ValueError(
                    f"Published Task Release file failed verification: {relative}"
                )
        manifest_path.unlink()
        model_manifest_path = staging_target / "model_release" / "manifest.json"
        model_manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
        if (
            model_manifest.get("status") != "ready"
            or model_manifest.get("sha256") != model.get("sha256")
            or model_manifest.get("size") != model_path.stat().st_size
        ):
            raise ValueError(
                "Published model does not match its source model manifest."
            )
        model_destination = staging_target / "model_release" / "model.safetensors"
        model_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(model_path, model_destination)
        binding = _published_release_binding(release, name)
        if not binding.get("taskId"):
            binding["taskId"] = manifest.get("taskId")
        write_task_binding(staging_target, binding)
        validate_task_contract(staging_target)
        staging_target.replace(target)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    local_project_id = f"local:{directory_name}"
    RUN_MANAGER.remove_for(local_project_id, runtime_namespace)
    return RUN_MANAGER.record_completed(
        kind="create",
        local_project_id=local_project_id,
        command=f"install Published Task Release {release.get('releaseId')}",
        output=(
            f"Verified Published Task Release: {release.get('releaseId')}\n"
            f"Verified model version: {model.get('modelVersionId')}\n"
            f"Workspace: ./{directory_name}\n"
        ),
        result_local_project_id=local_project_id,
        namespace=runtime_namespace,
    )
