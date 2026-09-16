"""Long-lived, account-scoped Federated Task participation runtime."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import socket
import threading
import time
import uuid
from typing import Any, Callable

from .environments import uv_run_command
from .folder import task_data_inventory
from .federated_task import read_task_binding, source_fingerprint
from .jobs import ManagedRun, WorkspaceRunManager


EVENT_LIMIT = 500
HISTORY_EVENT_LIMIT = 5_000
LOG_LIMIT = 100_000
SNAPSHOT_FINGERPRINT_CACHE_SECONDS = 5.0
_fingerprint_cache_lock = threading.Lock()
_source_fingerprint_cache: dict[str, tuple[float, str]] = {}
_data_inventory_cache: dict[str, tuple[float, dict[str, object]]] = {}
_metric_persist_lock = threading.Lock()
_metric_persist_state: dict[str, tuple[float, str]] = {}
LOCAL_STAGE_RANGES = {
    "preparing": (0.0, 5.0),
    "loading-data": (5.0, 10.0),
    "training": (10.0, 75.0),
    "evaluating": (75.0, 92.0),
    "exporting": (92.0, 99.0),
    "completed": (100.0, 100.0),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cached_source_fingerprint(project_root: Path, *, refresh: bool = False) -> str:
    key = str(project_root.resolve())
    now = time.monotonic()
    with _fingerprint_cache_lock:
        cached = _source_fingerprint_cache.get(key)
        if cached and not refresh and now - cached[0] < SNAPSHOT_FINGERPRINT_CACHE_SECONDS:
            return cached[1]
    value = source_fingerprint(project_root)
    with _fingerprint_cache_lock:
        _source_fingerprint_cache[key] = (now, value)
    return value


def _cached_data_inventory(data_path: Path, *, refresh: bool = False) -> dict[str, object]:
    key = str(data_path.resolve())
    now = time.monotonic()
    with _fingerprint_cache_lock:
        cached = _data_inventory_cache.get(key)
        if cached and not refresh and now - cached[0] < SNAPSHOT_FINGERPRINT_CACHE_SECONDS:
            return dict(cached[1])
    value = task_data_inventory(data_path)
    with _fingerprint_cache_lock:
        _data_inventory_cache[key] = (now, dict(value))
    return value


def _metadata_root(project_root: Path) -> Path:
    root = project_root / ".fedops-studio" / "participation"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Status callbacks and the request thread can persist state at the same
    # time. A shared `state.tmp` lets one writer rename the other writer's
    # temporary file, producing FileNotFoundError. Give every atomic write its
    # own sibling temporary file and always clean it up after replacement.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_events(path: Path, limit: int = EVENT_LIMIT) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schemaVersion") == 1:
            events.append(value)
    return events


def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _stage_progress(stage: str, percent: object) -> float | None:
    """Convert the shared local-training range to progress within this FL stage."""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return None
    limits = LOCAL_STAGE_RANGES.get(stage)
    if limits is None:
        return max(0.0, min(100.0, value))
    start, end = limits
    if end <= start:
        return 100.0
    return max(0.0, min(100.0, ((value - start) / (end - start)) * 100.0))


def _merge_live_run_events(
    events: list[dict[str, Any]],
    run: dict[str, object] | None,
    *,
    task_id: object,
    release_id: object,
    client_instance_id: object,
    limit: int = EVENT_LIMIT,
) -> list[dict[str, Any]]:
    """Expose captured child-process progress through the FL event contract.

    FedOps transport events are written to ``events.jsonl``. Owner training hooks
    emit ``FEDOPS_PROGRESS`` on stdout so they also work in Workspace Local Train.
    The process manager captures those events; this adapter joins both streams for
    the Federated Learning console without coupling Task code to Studio APIs.
    """
    if not run:
        return events[-limit:]
    points = run.get("metricSeries")
    progress = run.get("progress")
    live_points = list(points) if isinstance(points, list) else []
    if isinstance(progress, dict) and not any(
        isinstance(point, dict) and point.get("timestamp") == progress.get("timestamp")
        for point in live_points
    ):
        live_points.append(progress)
    merged = [dict(event) for event in events]
    for point in live_points:
        if not isinstance(point, dict) or not point.get("timestamp") or not point.get("stage"):
            continue
        stage = str(point["stage"])
        live_event: dict[str, Any] = {
            "schemaVersion": 1,
            "timestamp": point["timestamp"],
            "taskId": task_id,
            "releaseId": release_id,
            "clientInstanceId": client_instance_id,
            "stage": stage,
            "progress": point.get("percent"),
            "stageProgress": _stage_progress(stage, point.get("percent")),
            "message": point.get("message") or stage.replace("-", " ").title(),
            "metrics": point.get("metrics") if isinstance(point.get("metrics"), dict) else {},
            "source": "local-training-runtime",
        }
        for name in ("epoch", "epochs", "batch", "totalBatches", "step", "totalSteps"):
            if point.get(name) is not None:
                live_event[name] = point[name]
        merged.append(live_event)
    merged.sort(key=lambda event: str(event.get("timestamp") or ""))

    current_round: int | None = None
    batch_size: int | None = None
    for event in merged:
        try:
            if event.get("round") is not None:
                current_round = int(event["round"])
        except (TypeError, ValueError):
            pass
        training = event.get("training")
        if isinstance(training, dict):
            try:
                batch_size = int(training.get("batchSize"))
            except (TypeError, ValueError):
                pass
        if event.get("source") == "local-training-runtime":
            if current_round is not None:
                event["round"] = current_round
            if batch_size and event.get("batch") is not None:
                try:
                    event["sampleCount"] = int(event["batch"]) * batch_size
                except (TypeError, ValueError):
                    pass
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in merged:
        identity = json.dumps(
            {
                "timestamp": event.get("timestamp"),
                "source": event.get("source"),
                "stage": event.get("stage"),
                "round": event.get("round"),
                "epoch": event.get("epoch"),
                "batch": event.get("batch"),
                "step": event.get("step"),
                "metrics": event.get("metrics"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(event)
    return unique[-limit:]


def _persist_local_metric_events(
    event_path: Path,
    run: dict[str, object] | None,
    *,
    task_id: object,
    release_id: object,
    client_instance_id: object,
    force: bool = False,
) -> None:
    """Keep batch metrics beside transport events for restart-safe history."""
    if not run:
        return
    points = run.get("metricSeries")
    signature = json.dumps(
        {
            "points": len(points) if isinstance(points, list) else 0,
            "last": points[-1] if isinstance(points, list) and points else None,
            "progress": run.get("progress"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    cache_key = str(event_path)
    now = time.monotonic()
    with _metric_persist_lock:
        previous = _metric_persist_state.get(cache_key)
        if not force and previous:
            persisted_at, persisted_signature = previous
            if persisted_signature == signature or now - persisted_at < 0.75:
                return
        _metric_persist_state[cache_key] = (now, signature)
    merged = _merge_live_run_events(
        _read_events(event_path, HISTORY_EVENT_LIMIT),
        run,
        task_id=task_id,
        release_id=release_id,
        client_instance_id=client_instance_id,
        limit=HISTORY_EVENT_LIMIT,
    )
    local = [
        event for event in merged
        if event.get("source") == "local-training-runtime"
    ]
    _write_events(event_path.with_name("local-metrics.jsonl"), local)


def _normalize_model_lifecycle_events(
    events: list[dict[str, Any]],
    *,
    model: dict[str, Any],
    campaign: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Project legacy transport versions onto the v1.3 model lifecycle.

    ``GL_Model_V`` is the version that the aggregation server will publish when
    the whole Campaign completes.  Older Client events copied that target value
    onto every Flower round, which made a first run look like ``v1 -> v1``.
    Preserve the raw target as ``targetGlobalModelVersion`` and derive the model
    that was actually received or produced at each stage.
    """
    try:
        manifest_target = int(model.get("version"))
    except (TypeError, ValueError):
        manifest_target = None
    try:
        total_rounds = int((campaign or {}).get("rounds"))
    except (TypeError, ValueError):
        total_rounds = None

    # FedOps revision fde3137 used the Campaign target Global Model version
    # (GL_Model_V) as the initial round and added one.  For example, a new
    # Campaign producing Global Model v2 emitted connecting/waiting as Round 3.
    # These stages happen once before Flower Round 1, so project them back to
    # Round 1 for v1.3 Campaigns while preserving legacy Tasks without a
    # Campaign contract.
    projected_events: list[dict[str, Any]] = []
    for original in events:
        event = dict(original)
        if campaign and event.get("stage") in {"connecting", "waiting_round"}:
            event["transportReportedRound"] = event.get("round")
            event["round"] = 1
        projected_events.append(event)

    observed_rounds = [
        int(event["round"])
        for event in projected_events
        if isinstance(event.get("round"), int)
        and event.get("stage") not in {"completed", "failed", "connecting", "waiting_round"}
        and (not total_rounds or 1 <= event["round"] <= total_rounds)
    ]
    last_observed_round = max(observed_rounds, default=0)
    normalized: list[dict[str, Any]] = []
    for original in projected_events:
        event = dict(original)
        if campaign and event.get("stage") in {"completed", "failed"}:
            # The legacy Client's local num_rounds can lag behind the Campaign
            # policy. Transport callbacks are authoritative for rounds that
            # actually ran, so do not display an older local config value.
            event["transportReportedRound"] = event.get("round")
            event["round"] = last_observed_round or None
        try:
            round_number = int(event.get("round"))
        except (TypeError, ValueError):
            round_number = None
        try:
            target_version = int(
                event.get("targetGlobalModelVersion")
                or event.get("globalModelVersion")
                or manifest_target
            )
        except (TypeError, ValueError):
            target_version = manifest_target
        if target_version is not None:
            event["targetGlobalModelVersion"] = target_version

        stage = str(event.get("stage") or "")
        if stage == "downloading_global" and round_number is not None:
            if round_number == 1:
                source_version = max((target_version or 1) - 1, 0)
                if source_version == 0:
                    event.update({
                        "modelRole": "initiative",
                        "modelLabel": "Initiative Model",
                        "sourceGlobalModelVersion": None,
                    })
                else:
                    event.update({
                        "modelRole": "global",
                        "modelLabel": f"Global Model v{source_version}",
                        "sourceGlobalModelVersion": source_version,
                    })
            else:
                event.update({
                    "modelRole": "round-aggregate",
                    "modelLabel": f"Round {round_number - 1} Aggregate",
                    "sourceRound": round_number - 1,
                })
        elif stage == "global_model_updated" and round_number is not None:
            campaign_final = bool(total_rounds and round_number >= total_rounds)
            if event.get("aggregationScope") == "campaign-final":
                campaign_final = True
            if campaign_final and target_version is not None:
                event.update({
                    "modelRole": "global",
                    "modelLabel": f"Global Model v{target_version}",
                    "aggregationScope": "campaign-final",
                })
            else:
                event.update({
                    "modelRole": "round-aggregate",
                    "modelLabel": f"Round {round_number} Aggregate",
                    "aggregationScope": "round",
                })
        elif stage == "completed" and target_version is not None:
            event.update({
                "modelRole": "global",
                "modelLabel": f"Global Model v{target_version}",
                "aggregationScope": "campaign-final",
            })
        normalized.append(event)
    return normalized


def _confirmed_completion(
    state: dict[str, Any], campaign_run: dict[str, Any], events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """A closed socket alone is NOT success: require the exact run and local evaluation.

    The API supplies the authorized Web manifest; this Runtime does no network I/O.
    A finished Campaign can close gRPC before an older Client receives its final
    disconnect message. Keep the original event/log and record the confirmation.
    """
    if (not state.get("campaignRunId")
        or state.get("campaignRunId") != campaign_run.get("runId")
        or state.get("releaseId") != campaign_run.get("releaseId")
        or state.get("targetGlobalModelVersion") != campaign_run.get("targetGlobalModelVersion")
        or campaign_run.get("status") != "completed"
        or not campaign_run.get("endedAt")
        or state.get("status") in {"stopping", "stopped"}
        or state.get("stopRequestedAt")):
        return None
    rounds = (campaign_run.get("campaign") or {}).get("rounds")
    target = campaign_run.get("targetGlobalModelVersion")
    if not isinstance(rounds, int) or rounds < 1 or not isinstance(target, int):
        return None
    evidence = next((event for event in reversed(events)
        if event.get("stage") == "global_model_updated"
        and event.get("round") == rounds
        and event.get("taskId") == state.get("taskId")
        and event.get("releaseId") == state.get("releaseId")
        and event.get("targetGlobalModelVersion") == target), None)
    if not evidence:
        return None
    for event in events:
        if event.get("stage") == "failed":
            message = str(event.get("message") or "")
            if (event.get("errorType") != "_MultiThreadedRendezvous"
                or "UNAVAILABLE" not in message
                or "Stream removed (Socket closed)" not in message
                or str(event.get("timestamp") or "") < str(evidence.get("timestamp") or "")):
                return None
    return {
        "schemaVersion": 1, "timestamp": _now(), "stage": "completed",
        "taskId": state.get("taskId"), "releaseId": state.get("releaseId"),
        "clientInstanceId": state.get("clientInstanceId"), "round": rounds,
        "progress": 100, "source": "campaign-confirmation",
        "message": "Final local evaluation and Campaign completion confirmed.",
        "targetGlobalModelVersion": target, "globalModelVersion": target,
        "modelRole": "global", "modelLabel": f"Global Model v{target}",
        "aggregationScope": "campaign-final",
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _validate_data_path(data_path: str, allowed_data_root: Path) -> Path:
    selected = Path(data_path).expanduser().resolve()
    allowed = allowed_data_root.expanduser().resolve()
    try:
        selected.relative_to(allowed)
    except ValueError as error:
        raise ValueError(f"Local data must be inside {allowed}.") from error
    if not selected.is_dir():
        raise FileNotFoundError("The selected local data directory does not exist.")
    return selected


def _manifest_parts(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    task = manifest.get("task")
    release = manifest.get("release")
    model = manifest.get("globalModel")
    server = manifest.get("server")
    participation = manifest.get("participation")
    if not all(isinstance(value, dict) for value in (task, release, model, server, participation)):
        raise ValueError("FedOps Web returned an incomplete participation manifest.")
    if manifest.get("schemaVersion") != 1:
        raise ValueError("The participation manifest schema is unsupported.")
    return task, release, model, server, participation


def _server_availability(server: dict[str, Any]) -> dict[str, Any]:
    ready = bool(server.get("ready") and server.get("aggregationServer"))
    return {
        "ready": ready,
        "state": str(server.get("state") or ("ready" if ready else "not_available")),
        "endpoint": str(server.get("aggregationServer") or "") or None,
        "observedAt": server.get("observedAt") or _now(),
    }


def participation_preflight(
    project_root: Path,
    local_project_id: str,
    manifest: dict[str, Any],
    data_path: str,
    allowed_data_root: Path,
    environment_id: str | None = None,
) -> dict[str, Any]:
    task, release, model, server, participation = _manifest_parts(manifest)
    binding = read_task_binding(project_root)
    checks: list[dict[str, Any]] = []

    def check(identifier: str, label: str, passed: bool, detail: str) -> None:
        checks.append({
            "id": identifier,
            "label": label,
            "status": "passed" if passed else "failed",
            "detail": detail,
        })

    binding_matches = bool(binding and str(binding.get("taskId")) == str(task.get("taskId")))
    check("task-binding", "Federated Task linked", binding_matches, "Workspace Task ID matches FedOps Web.")

    runtime_contract = task.get("runtimeContract") if isinstance(task.get("runtimeContract"), dict) else {}
    contract_ready = runtime_contract.get("name") == "federated-task-v3"
    check("runtime-contract", "Participant Runtime compatible", contract_ready, str(runtime_contract.get("name") or "missing"))

    readiness_path = project_root / ".fedops-studio" / "readiness-participation.json"
    readiness = _read_json(readiness_path)
    fingerprint = _cached_source_fingerprint(project_root, refresh=True)
    readiness_current = bool(
        readiness.get("ok") is True
        and readiness.get("mode") == "participation"
        and readiness.get("sourceFingerprint") == fingerprint
    )
    check(
        "participation-readiness",
        "Participation Readiness",
        readiness_current,
        (
            "Local data, training, and parameter export were checked for the current Workspace source."
            if readiness_current
            else "Run Check Participation Readiness for the current Workspace source and local data."
        ),
    )

    release_identity_ready = bool(
        binding
        and binding.get("releaseId") == release.get("releaseId")
        and binding.get("modelVersionId") == model.get("modelVersionId")
    )
    check(
        "release-identity",
        "Published Release installed",
        release_identity_ready,
        (
            f"Workspace {binding.get('releaseId')} · Registry {release.get('releaseId')}"
            if binding
            else "No Published Release identity is installed in this Workspace."
        ),
    )

    readiness_signature = readiness.get("parameterSignatureFingerprint")
    model_signature = model.get("parameterSignatureFingerprint")
    parameter_contract_ready = bool(
        readiness_signature
        and model_signature
        and readiness_signature == model_signature
    )
    check(
        "parameter-contract",
        "Federated parameter contract",
        parameter_contract_ready,
        (
            "Local model parameters match the current Global Model."
            if parameter_contract_ready
            else "Local model parameter names, shapes, or data types do not match the current Global Model."
        ),
    )

    try:
        selected_data = _validate_data_path(data_path, allowed_data_root)
    except (FileNotFoundError, ValueError) as error:
        selected_data = Path(data_path).expanduser()
        check("local-data", "Local dataset accessible", False, str(error))
    else:
        check("local-data", "Local dataset accessible", True, str(selected_data))

    data_inventory = (
        _cached_data_inventory(selected_data, refresh=True)
        if selected_data.is_dir()
        else {"fingerprint": ""}
    )
    data_fingerprint = str(data_inventory.get("fingerprint") or "")
    data_current = bool(
        readiness_current
        and selected_data.is_dir()
        and (
            not readiness.get("dataFingerprint")
            or readiness.get("dataFingerprint")
            == data_fingerprint
        )
    )
    checks = [item for item in checks if item["id"] != "participation-readiness"]
    check(
        "participation-readiness",
        "Participation Readiness",
        data_current,
        (
            "Workspace source and current Task Data match the latest Participation Readiness run."
            if data_current
            else "Task Data or Workspace source changed. Run Check Participation Readiness again."
        ),
    )

    try:
        _, _, environment = uv_run_command(
            project_root,
            local_project_id,
            ["python", "--version"],
            environment_id,
        )
    except FileNotFoundError as error:
        environment = None
        check("python-environment", "Python environment ready", False, str(error))
    else:
        check(
            "python-environment",
            "Python environment ready",
            True,
            str(environment.get("name") or environment.get("environmentId")),
        )

    model_ready = bool(model.get("modelVersionId") and model.get("sha256"))
    check("global-model", "Global Model available", model_ready, f"Version {model.get('version')}")
    participation_ready = participation.get("status") == "approved"
    check("authorization", "Participation authorized", participation_ready, str(participation.get("role") or "participant"))

    report = {
        "ok": all(item["status"] == "passed" for item in checks),
        "checkedAt": _now(),
        "taskId": task.get("taskId"),
        "releaseId": release.get("releaseId"),
        "modelVersionId": model.get("modelVersionId"),
        "campaignRunId": str(
            (manifest.get("campaignRun") or {}).get("runId") or ""
        ) or None,
        "sourceFingerprint": fingerprint,
        "dataFingerprint": data_fingerprint,
        "environmentId": environment.get("environmentId") if environment else environment_id,
        "dataPath": str(selected_data),
        "serverAvailability": _server_availability(server),
        "checks": checks,
    }
    _write_json(_metadata_root(project_root) / "preflight.json", report)
    return report


def _preflight_is_current(
    project_root: Path,
    manifest: dict[str, Any],
    preflight: dict[str, Any],
) -> bool:
    if preflight.get("ok") is not True:
        return False
    task, release, model, _, participation = _manifest_parts(manifest)
    campaign_run = manifest.get("campaignRun") if isinstance(manifest.get("campaignRun"), dict) else {}
    data_path = Path(str(preflight.get("dataPath") or "")).expanduser()
    if not data_path.is_dir():
        return False
    identities_match = (
        str(preflight.get("taskId") or "") == str(task.get("taskId") or "")
        and str(preflight.get("releaseId") or "") == str(release.get("releaseId") or "")
        and str(preflight.get("modelVersionId") or "") == str(model.get("modelVersionId") or "")
        and (str(preflight.get("campaignRunId") or "") or None)
        == (str(campaign_run.get("runId") or "") or None)
    )
    if not identities_match or participation.get("status") != "approved":
        return False
    return bool(
        preflight.get("sourceFingerprint")
        == _cached_source_fingerprint(project_root)
        and preflight.get("dataFingerprint")
        == _cached_data_inventory(data_path).get("fingerprint")
    )


class ParticipationManager:
    """Own a dedicated process manager and durable participation snapshots."""

    def __init__(self) -> None:
        self._runs = WorkspaceRunManager()
        self._lock = threading.Lock()
        self._starting_projects: set[tuple[str, str]] = set()
        self._reserved_ports: set[int] = set()

    def _active(self, account_key: str, local_project_id: str) -> dict[str, object] | None:
        return self._runs.active_for(local_project_id, namespace=account_key)

    def _claim_start(self, account_key: str, local_project_id: str) -> tuple[str, str]:
        key = (account_key, local_project_id)
        active = self._active(account_key, local_project_id)
        with self._lock:
            if key in self._starting_projects or active:
                run_id = active.get("runId") if active else "starting"
                raise RuntimeError(
                    f"This Federated Task already has an active Client ({run_id})."
                )
            self._starting_projects.add(key)
        return key

    def _release_start(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._starting_projects.discard(key)

    def _reserve_port_pair(self) -> tuple[int, int]:
        selected: list[int] = []
        with self._lock:
            while len(selected) < 2:
                port = _free_port()
                if port in self._reserved_ports or port in selected:
                    continue
                selected.append(port)
            self._reserved_ports.update(selected)
        return selected[0], selected[1]

    def _release_ports(self, *ports: int) -> None:
        with self._lock:
            self._reserved_ports.difference_update(ports)

    @staticmethod
    def _campaign_run(manifest: dict[str, Any]) -> dict[str, Any]:
        value = manifest.get("campaignRun")
        return value if isinstance(value, dict) else {}

    @classmethod
    def _event_path(
        cls,
        metadata: Path,
        manifest: dict[str, Any],
        session_id: str | None = None,
    ) -> Path:
        run_id = str(
            session_id or cls._campaign_run(manifest).get("runId") or ""
        ).strip()
        if not run_id:
            return metadata / "events.jsonl"
        safe_run_id = "".join(
            character for character in run_id
            if character.isalnum() or character in {"-", "_"}
        )
        if not safe_run_id:
            return metadata / "events.jsonl"
        return metadata / "runs" / safe_run_id / "events.jsonl"

    @classmethod
    def _session_path(
        cls,
        metadata: Path,
        manifest: dict[str, Any],
        session_id: str | None = None,
    ) -> Path:
        return cls._event_path(metadata, manifest, session_id).with_name("session.json")

    @staticmethod
    def _history_summary(
        session_id: str,
        record: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rounds = sorted({
            int(event["round"])
            for event in events
            if isinstance(event.get("round"), int)
        })
        final = next(
            (
                event for event in reversed(events)
                if event.get("stage") in {"completed", "failed"}
            ),
            events[-1] if events else {},
        )
        status = str(record.get("status") or final.get("stage") or "recorded")
        if status == "running" and final.get("stage") == "completed":
            status = "completed"
        target_version = record.get("targetGlobalModelVersion")
        if target_version is None:
            for event in reversed(events):
                target_version = event.get("targetGlobalModelVersion")
                if target_version is not None:
                    break
        return {
            "sessionId": session_id,
            "campaignRunId": record.get("campaignRunId"),
            "runId": record.get("runId"),
            "taskId": record.get("taskId") or final.get("taskId"),
            "releaseId": record.get("releaseId") or final.get("releaseId"),
            "status": status,
            "baseGlobalModelVersion": record.get("baseGlobalModelVersion"),
            "targetGlobalModelVersion": target_version,
            "campaign": record.get("campaign"),
            "rounds": rounds,
            "completedRounds": len({
                event.get("round")
                for event in events
                if event.get("stage") == "global_model_updated"
                and isinstance(event.get("round"), int)
            }),
            "startedAt": record.get("startedAt") or (events[0].get("timestamp") if events else None),
            "endedAt": record.get("endedAt") or (final.get("timestamp") if final else None),
            "eventCount": len(events),
        }

    def history_sessions(
        self,
        project_root: Path,
        manifest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """List durable Client sessions without depending on remote history."""
        metadata = _metadata_root(project_root)
        sessions: list[dict[str, Any]] = []
        runs_root = metadata / "runs"
        if runs_root.is_dir() and not runs_root.is_symlink():
            for directory in runs_root.iterdir():
                if not directory.is_dir() or directory.is_symlink():
                    continue
                events = [
                    *_read_events(directory / "events.jsonl", HISTORY_EVENT_LIMIT),
                    *_read_events(directory / "local-metrics.jsonl", HISTORY_EVENT_LIMIT),
                ]
                events.sort(key=lambda event: str(event.get("timestamp") or ""))
                record = _read_json(directory / "session.json")
                events = _normalize_model_lifecycle_events(
                    events, model={"version": record.get("targetGlobalModelVersion")},
                    campaign=record.get("campaign"),
                )
                if events or record:
                    sessions.append(self._history_summary(directory.name, record, events))
        legacy_events = _read_events(metadata / "events.jsonl", HISTORY_EVENT_LIMIT)
        if legacy_events:
            sessions.append(self._history_summary("legacy", {}, legacy_events))
        return sorted(
            sessions,
            key=lambda value: str(value.get("startedAt") or value.get("endedAt") or ""),
            reverse=True,
        )

    def history_session(
        self,
        project_root: Path,
        manifest: dict[str, Any],
        session_id: str,
    ) -> dict[str, Any]:
        """Read one authorized local participation session and its round events."""
        metadata = _metadata_root(project_root)
        if session_id == "legacy":
            event_path = metadata / "events.jsonl"
            record: dict[str, Any] = {}
        else:
            safe = "".join(
                character for character in session_id
                if character.isalnum() or character in {"-", "_"}
            )
            if not safe or safe != session_id:
                raise ValueError("The participation history session ID is invalid.")
            event_path = metadata / "runs" / safe / "events.jsonl"
            record = _read_json(event_path.with_name("session.json"))
        events = [
            *_read_events(event_path, HISTORY_EVENT_LIMIT),
            *_read_events(event_path.with_name("local-metrics.jsonl"), HISTORY_EVENT_LIMIT),
        ]
        events.sort(key=lambda event: str(event.get("timestamp") or ""))
        if not events and not record:
            raise FileNotFoundError("The participation history session was not found.")
        try:
            _, _, model, _, _ = _manifest_parts(manifest)
        except ValueError:
            # Durable device-local history must remain readable while FedOps Web
            # is unavailable or before the authorized manifest cache is rebuilt.
            # The recorded session already contains the Campaign and model
            # lifecycle fields needed for a faithful local view.
            model = {}
        campaign = record.get("campaign")
        if not isinstance(campaign, dict):
            campaign = manifest.get("campaign") if isinstance(manifest.get("campaign"), dict) else None
        normalized = _normalize_model_lifecycle_events(
            events,
            model=model,
            campaign=campaign,
        )
        return {
            "session": self._history_summary(session_id, record, normalized),
            "events": normalized,
        }

    def start(
        self,
        *,
        account_key: str,
        project_root: Path,
        local_project_id: str,
        manifest: dict[str, Any],
        data_path: str,
        allowed_data_root: Path,
        environment_id: str | None = None,
    ) -> dict[str, Any]:
        selected_data = _validate_data_path(data_path, allowed_data_root)
        preflight = _read_json(_metadata_root(project_root) / "preflight.json")
        if str(preflight.get("dataPath") or "") != str(selected_data):
            raise ValueError("Check Participation Readiness for the selected Task Data before starting this Client.")
        if not _preflight_is_current(project_root, manifest, preflight):
            raise ValueError("Participation Readiness is missing or outdated for this Campaign. Check it again before starting this Client.")
        task, release, _, server, _ = _manifest_parts(manifest)
        if not _server_availability(server)["ready"]:
            raise ValueError(
                "Participation Readiness passed, but the Federated Server is not live. "
                "Start it in FedOps Web or wait until it becomes available."
            )
        start_key = self._claim_start(account_key, local_project_id)
        try:
            reserved_ports = self._reserve_port_pair()
            release_lock = threading.Lock()
            released = False

            def release_ports_once() -> None:
                nonlocal released
                with release_lock:
                    if released:
                        return
                    released = True
                self._release_ports(*reserved_ports)

            return self._start_claimed(
                account_key=account_key,
                project_root=project_root,
                local_project_id=local_project_id,
                manifest=manifest,
                preflight=preflight,
                reserved_ports=reserved_ports,
                ports_owned=release_ports_once,
            )
        finally:
            self._release_start(start_key)

    def _start_claimed(
        self,
        *,
        account_key: str,
        project_root: Path,
        local_project_id: str,
        manifest: dict[str, Any],
        preflight: dict[str, Any],
        reserved_ports: tuple[int, int],
        ports_owned: Callable[[], None],
    ) -> dict[str, Any]:
        try:
            task, release, _, server, _ = _manifest_parts(manifest)
            metadata = _metadata_root(project_root)
            state_path = metadata / "state.json"
            current = _read_json(state_path)
            client_instance_id = str(current.get("clientInstanceId") or f"client-{uuid.uuid4().hex[:16]}")
            campaign_run = self._campaign_run(manifest)
            campaign_run_id = str(campaign_run.get("runId") or "").strip() or None
            session_id = campaign_run_id or f"local-{uuid.uuid4().hex}"
            manager_port, client_port = reserved_ports
            event_path = self._event_path(metadata, manifest, session_id)
            session_path = self._session_path(metadata, manifest, session_id)
            command = [
                "python", "-m", "federated_task.main", "participate",
                "--task-id", str(task["taskId"]),
                "--runtime-key", str(task["runtimeKey"]),
                "--data-root", str(preflight["dataPath"]),
                "--server-manager-url", str(server["managerUrl"]),
                "--manager-port", str(manager_port),
                "--client-port", str(client_port),
                "--release-id", str(release["releaseId"]),
                "--client-instance-id", client_instance_id,
                "--event-file", str(event_path),
            ]
            argv, environment, selected = uv_run_command(
                project_root,
                local_project_id,
                command,
                preflight.get("environmentId"),
            )
            initial_state = {
                "schemaVersion": 1,
                "status": "starting",
                "clientState": "connecting",
                "taskId": task["taskId"],
                "runtimeKey": task["runtimeKey"],
                "releaseId": release["releaseId"],
                "campaignRunId": campaign_run_id,
                "sessionId": session_id,
                "baseGlobalModelVersion": campaign_run.get("baseGlobalModelVersion"),
                "targetGlobalModelVersion": campaign_run.get("targetGlobalModelVersion"),
                "clientInstanceId": client_instance_id,
                "managerPort": manager_port,
                "clientPort": client_port,
                "environmentId": selected["environmentId"],
                "startedAt": _now(),
                "endedAt": None,
                "runId": None,
            }

            def update_status(status: str, client_state: str) -> None:
                # Releasing supervisor processes must not turn confirmed success
                # into a cancelled/failed participation history entry.
                previous = _read_json(state_path)
                if previous.get("completionConfirmed") and previous.get("sessionId") == session_id:
                    status, client_state = "completed", "completed"
                stored = {**_read_json(state_path), "status": status, "clientState": client_state}
                if status in {"completed", "failed", "stopped"}:
                    stored["endedAt"] = _now()
                _write_json(state_path, stored)
                _write_json(session_path, {
                    **_read_json(session_path),
                    "status": status,
                    "clientState": client_state,
                    "endedAt": stored.get("endedAt"),
                })

            def before(run: ManagedRun) -> None:
                _write_json(state_path, {**initial_state, "runId": run.run_id, "status": "running"})
                _write_json(session_path, {
                    **_read_json(session_path),
                    "runId": run.run_id,
                    "status": "running",
                })

            def finish(run: ManagedRun, status: str, client_state: str) -> None:
                _persist_local_metric_events(
                    event_path,
                    run.serialize(),
                    task_id=task["taskId"],
                    release_id=release["releaseId"],
                    client_instance_id=client_instance_id,
                    force=True,
                )
                update_status(status, client_state)

            _write_json(state_path, initial_state)
            _write_json(session_path, {
                "schemaVersion": 1,
                "sessionId": session_id,
                "campaignRunId": campaign_run_id,
                "taskId": task["taskId"],
                "runtimeKey": task["runtimeKey"],
                "releaseId": release["releaseId"],
                "clientInstanceId": client_instance_id,
                "baseGlobalModelVersion": campaign_run.get("baseGlobalModelVersion"),
                "targetGlobalModelVersion": campaign_run.get("targetGlobalModelVersion"),
                "campaign": campaign_run.get("campaign") or manifest.get("campaign"),
                "status": "starting",
                "clientState": "connecting",
                "startedAt": initial_state["startedAt"],
                "endedAt": None,
                "runId": None,
            })
            run = self._runs.start_command(
                kind="federated-participation",
                local_project_id=local_project_id,
                environment_id=str(selected["environmentId"]),
                argv=argv,
                cwd=project_root,
                environment=environment,
                initial_output="Starting the authorized FedOps Client runtime...\n",
                before_command=before,
                after_success=lambda completed: finish(completed, "completed", "completed"),
                after_failure=lambda failed, _error: finish(failed, "failed", "failed"),
                after_cancel=lambda cancelled: finish(cancelled, "stopped", "stopped"),
                always=ports_owned,
                termination_signal=signal.SIGINT,
                # The participation supervisor owns the Client and its local manager.
                # Signal only that supervisor so its finally block can stop the Client
                # before the manager. Broadcasting SIGINT to the whole process group
                # races the Client's final /trainFin callback against manager shutdown.
                terminate_process_group=False,
                cancel_grace_seconds=12.0,
                namespace=account_key,
            )
        except Exception:
            ports_owned()
            raise
        stored = _read_json(state_path)
        _write_json(state_path, {**stored, "runId": run["runId"]})
        return self.snapshot(account_key, project_root, local_project_id, manifest)

    def stop(
        self,
        account_key: str,
        project_root: Path,
        local_project_id: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        active = self._active(account_key, local_project_id)
        current = self.snapshot(account_key, project_root, local_project_id, manifest)
        if current["runtime"].get("clientState") == "completed":
            if active:
                state_path = _metadata_root(project_root) / "state.json"
                _write_json(state_path, {**_read_json(state_path), "completionConfirmed": True})
                try:
                    self._runs.cancel(str(active["runId"]), account_key)
                except (KeyError, RuntimeError):
                    pass  # Supervisor may already have exited.
            return self.snapshot(account_key, project_root, local_project_id, manifest)
        if not active:
            state_path = _metadata_root(project_root) / "state.json"
            state = _read_json(state_path)
            if state.get("status") not in {"completed", "failed", "disconnected", "starting", "running"}:
                raise RuntimeError("This Federated Learning Client is not running.")
            _write_json(state_path, {
                **state,
                "status": "stopped",
                "clientState": "stopped",
                "endedAt": state.get("endedAt") or _now(),
            })
            return self.snapshot(account_key, project_root, local_project_id, manifest)
        state_path = _metadata_root(project_root) / "state.json"
        state = _read_json(state_path)
        _write_json(state_path, {
            **state,
            "status": "stopping",
            "clientState": "stopping",
            "stopRequestedAt": _now(),
            "stopPolicy": "local-client-only",
        })
        self._runs.cancel(str(active["runId"]), account_key)
        return self.snapshot(account_key, project_root, local_project_id, manifest)

    def snapshot(
        self,
        account_key: str,
        project_root: Path,
        local_project_id: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        task, release, model, server, participation = _manifest_parts(manifest)
        metadata = _metadata_root(project_root)
        state_path = metadata / "state.json"
        state = _read_json(state_path)
        campaign_run = self._campaign_run(manifest)
        campaign_run_id = str(campaign_run.get("runId") or "").strip() or None
        if campaign_run_id and state.get("campaignRunId") != campaign_run_id:
            state = {
                "schemaVersion": 1,
                "status": "not_started",
                "clientState": "ready" if server.get("ready") else "not_configured",
                "campaignRunId": campaign_run_id,
                "baseGlobalModelVersion": campaign_run.get("baseGlobalModelVersion"),
                "targetGlobalModelVersion": campaign_run.get("targetGlobalModelVersion"),
                "clientInstanceId": state.get("clientInstanceId"),
                "startedAt": None,
                "endedAt": None,
                "runId": None,
            }
            _write_json(state_path, state)
        run = None
        run_id = state.get("runId")
        if run_id:
            try:
                run = self._runs.get(str(run_id), account_key)
            except KeyError:
                if state.get("status") in {"starting", "running"}:
                    state.update({
                        "status": "disconnected",
                        "clientState": "disconnected",
                        "endedAt": _now(),
                    })
                    _write_json(state_path, state)
        event_path = self._event_path(
            metadata,
            manifest,
            str(state.get("sessionId") or "") or None,
        )
        if run:
            _persist_local_metric_events(
                event_path,
                run,
                task_id=task.get("taskId"),
                release_id=release.get("releaseId"),
                client_instance_id=state.get("clientInstanceId"),
            )
        recorded_events = [
            *_read_events(event_path),
            *_read_events(event_path.with_name("local-metrics.jsonl"), HISTORY_EVENT_LIMIT),
        ]
        events = _merge_live_run_events(
            recorded_events,
            run,
            task_id=task.get("taskId"),
            release_id=release.get("releaseId"),
            client_instance_id=state.get("clientInstanceId"),
        )
        events = _normalize_model_lifecycle_events(
            events,
            model=model,
            campaign=(
                campaign_run.get("campaign")
                if isinstance(campaign_run.get("campaign"), dict)
                else manifest.get("campaign")
                if isinstance(manifest.get("campaign"), dict)
                else None
            ),
        )
        confirmation = _confirmed_completion(state, campaign_run, events)
        if confirmation and not state.get("completionConfirmed"):
            # Never delete the transport failure: append separate authoritative
            # completion evidence, then stop only this session's supervisor.
            _write_events(event_path, [*_read_events(event_path, HISTORY_EVENT_LIMIT), confirmation])
            state.update({"status": "completed", "clientState": "completed",
                          "completionConfirmed": True, "endedAt": campaign_run["endedAt"]})
            _write_json(state_path, state)
            session_path = event_path.with_name("session.json")
            _write_json(session_path, {**_read_json(session_path), "status": "completed",
                "clientState": "completed", "endedAt": state["endedAt"], "completionConfirmed": True})
            events.append(confirmation)
            if run and run.get("status") in {"queued", "running"}:
                try:
                    self._runs.cancel(str(run["runId"]), account_key)
                except (KeyError, RuntimeError):
                    pass
        latest = events[-1] if events else None
        if (
            run
            and run.get("status") in {"queued", "running"}
            and latest
            and state.get("status") != "stopping"
            and not state.get("completionConfirmed")
        ):
            state["clientState"] = latest.get("stage", state.get("clientState", "connecting"))
            state["status"] = "running"
        binding = read_task_binding(project_root)
        readiness = _read_json(project_root / ".fedops-studio" / "readiness-participation.json")
        fingerprint = _cached_source_fingerprint(project_root)
        readiness_current = bool(
            readiness.get("ok") is True
            and readiness.get("mode") == "participation"
            and readiness.get("sourceFingerprint") == fingerprint
            and isinstance(readiness.get("dataPath"), str)
            and Path(str(readiness.get("dataPath"))).is_dir()
            and (
                not readiness.get("dataFingerprint")
                or readiness.get("dataFingerprint")
                == _cached_data_inventory(Path(str(readiness.get("dataPath")))).get("fingerprint")
            )
        )
        release_identity_ready = bool(
            binding
            and binding.get("releaseId") == release.get("releaseId")
            and binding.get("modelVersionId") == model.get("modelVersionId")
        )
        parameter_contract_ready = bool(
            readiness.get("parameterSignatureFingerprint")
            and readiness.get("parameterSignatureFingerprint")
            == model.get("parameterSignatureFingerprint")
        )
        preflight = _read_json(metadata / "preflight.json")
        participation_ready_to_start = _preflight_is_current(
            project_root,
            manifest,
            preflight,
        )
        server_availability = _server_availability(server)
        ready_to_start = bool(
            participation_ready_to_start and server_availability["ready"]
        )
        preflight_contract = preflight if all(
            key in preflight
            for key in (
                "campaignRunId",
                "sourceFingerprint",
                "dataFingerprint",
            )
        ) else None
        return {
            "localProjectId": local_project_id,
            "task": task,
            "release": release,
            "globalModel": model,
            "server": server,
            "campaign": manifest.get("campaign") if isinstance(manifest.get("campaign"), dict) else None,
            "campaignRun": campaign_run or None,
            "participation": participation,
            "workspace": {
                "ready": bool(
                    binding
                    and str(binding.get("taskId")) == str(task.get("taskId"))
                    and readiness_current
                    and release_identity_ready
                    and parameter_contract_ready
                ),
                "releaseUpdateRequired": bool(binding and not release_identity_ready),
                "binding": binding,
                "participationReadiness": readiness or None,
                "participationPreflight": preflight_contract,
                "participationReadyToStart": participation_ready_to_start,
                "serverAvailability": server_availability,
                "readyToStart": ready_to_start,
            },
            "runtime": state or {
                "schemaVersion": 1,
                "status": "not_started",
                "clientState": "ready" if server.get("ready") else "not_configured",
            },
            "latestEvent": latest,
            "events": events,
            "logs": str(run.get("output") or "")[-LOG_LIMIT:] if run else "",
            "observedAt": _now(),
        }


PARTICIPATION_MANAGER = ParticipationManager()


__all__ = [
    "PARTICIPATION_MANAGER",
    "ParticipationManager",
    "participation_preflight",
]
