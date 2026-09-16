"""Durable Workspace job history and allow-listed subprocess execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MAX_OUTPUT_CHARS = 200_000
MAX_METRIC_POINTS = 1_000
MAX_RUN_HISTORY = 50
HISTORY_WRITE_INTERVAL_SECONDS = 0.75
PROGRESS_EVENT_PREFIX = "FEDOPS_PROGRESS "
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROGRESS_STAGES = {
    "preparing",
    "loading-data",
    "training",
    "evaluating",
    "exporting",
    "completed",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ManagedRun:
    run_id: str
    kind: str
    status: str
    local_project_id: str | None
    environment_id: str | None
    command: str
    namespace: str = field(default="device", repr=False)
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    output: str = ""
    result_local_project_id: str | None = None
    result: dict[str, object] | None = None
    progress: dict[str, object] | None = None
    metric_series: list[dict[str, object]] = field(default_factory=list)
    cancel_requested: bool = field(default=False, repr=False)
    termination_signal: int = field(default=signal.SIGTERM, repr=False)
    terminate_process_group: bool = field(default=True, repr=False)
    cancel_grace_seconds: float = field(default=3.0, repr=False)
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def serialize(self) -> dict[str, object]:
        return {
            "runId": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "localProjectId": self.local_project_id,
            "environmentId": self.environment_id,
            "taskId": None,
            "command": self.command,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "exitCode": self.exit_code,
            "output": self.output,
            "resultLocalProjectId": self.result_local_project_id,
            "result": self.result,
            "progress": self.progress,
            "metricSeries": list(self.metric_series),
        }


class WorkspaceRunManager:
    def __init__(self, history_root: Path | None = None) -> None:
        self._runs: dict[str, ManagedRun] = {}
        self._lock = threading.Lock()
        self._history_root = history_root
        self._last_persisted: dict[str, float] = {}
        if history_root is not None:
            self._load_history()

    def _history_path(self, namespace: str) -> Path:
        assert self._history_root is not None
        digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:24]
        return self._history_root / f"{digest}.json"

    def _load_history(self) -> None:
        assert self._history_root is not None
        self._history_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        interrupted_at = utc_now()
        for path in sorted(self._history_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                namespace = str(payload["namespace"])
                raw_runs = payload["runs"]
                if payload.get("schemaVersion") != 1 or not isinstance(raw_runs, list):
                    continue
                restored: list[ManagedRun] = []
                for raw in raw_runs[-MAX_RUN_HISTORY:]:
                    run = self._restore_run(raw, namespace)
                    if run.status in {"queued", "running"}:
                        run.status = "failed"
                        run.exit_code = 1
                        run.ended_at = interrupted_at
                        run.output = (
                            f"{run.output}\nAgent Studio stopped before this action completed. "
                            "The history was restored, but the process must be started again.\n"
                        )[-MAX_OUTPUT_CHARS:]
                    restored.append(run)
                for run in restored:
                    self._runs[run.run_id] = run
                if any(
                    str(raw.get("status")) in {"queued", "running"}
                    for raw in raw_runs
                    if isinstance(raw, dict)
                ):
                    self._persist_namespace_locked(namespace, force=True)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
                continue

    @staticmethod
    def _restore_run(raw: object, namespace: str) -> ManagedRun:
        if not isinstance(raw, dict):
            raise TypeError("invalid run history item")
        run_id = str(raw.get("runId") or "")
        status = str(raw.get("status") or "")
        if not run_id or status not in {
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise ValueError("invalid run history identity")
        metric_series = raw.get("metricSeries") or []
        progress = raw.get("progress")
        if not isinstance(metric_series, list) or (
            progress is not None and not isinstance(progress, dict)
        ):
            raise ValueError("invalid run history metrics")
        return ManagedRun(
            run_id=run_id,
            kind=str(raw.get("kind") or "workspace-action"),
            status=status,
            local_project_id=str(raw["localProjectId"])
            if raw.get("localProjectId") is not None
            else None,
            environment_id=str(raw["environmentId"])
            if raw.get("environmentId") is not None
            else None,
            command=str(raw.get("command") or ""),
            namespace=namespace,
            created_at=str(raw.get("createdAt") or utc_now()),
            started_at=str(raw["startedAt"])
            if raw.get("startedAt") is not None
            else None,
            ended_at=str(raw["endedAt"]) if raw.get("endedAt") is not None else None,
            exit_code=int(raw["exitCode"]) if raw.get("exitCode") is not None else None,
            output=str(raw.get("output") or "")[-MAX_OUTPUT_CHARS:],
            result_local_project_id=(
                str(raw["resultLocalProjectId"])
                if raw.get("resultLocalProjectId") is not None
                else None
            ),
            result=dict(raw["result"]) if isinstance(raw.get("result"), dict) else None,
            progress=progress,
            metric_series=metric_series[-MAX_METRIC_POINTS:],
        )

    def _persist_namespace_locked(self, namespace: str, *, force: bool = False) -> None:
        if self._history_root is None:
            return
        now = time.monotonic()
        if (
            not force
            and now - self._last_persisted.get(namespace, 0.0)
            < HISTORY_WRITE_INTERVAL_SECONDS
        ):
            return
        self._history_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        runs = [
            run.serialize() for run in self._runs.values() if run.namespace == namespace
        ]
        payload = {
            "schemaVersion": 1,
            "namespace": namespace,
            "runs": runs[-MAX_RUN_HISTORY:],
        }
        path = self._history_path(namespace)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        self._last_persisted[namespace] = now

    def start_command(
        self,
        *,
        kind: str,
        local_project_id: str | None,
        environment_id: str | None = None,
        argv: list[str],
        cwd: Path,
        namespace: str = "device",
        environment: dict[str, str] | None = None,
        initial_output: str = "",
        initial_progress: dict[str, object] | None = None,
        before_command: Callable[[ManagedRun], None] | None = None,
        after_success: Callable[[ManagedRun], None] | None = None,
        after_failure: Callable[[ManagedRun, Exception], None] | None = None,
        after_cancel: Callable[[ManagedRun], None] | None = None,
        always: Callable[[], None] | None = None,
        termination_signal: int = signal.SIGTERM,
        terminate_process_group: bool = True,
        cancel_grace_seconds: float = 3.0,
    ) -> dict[str, object]:
        self._ensure_no_active_run(local_project_id, namespace)
        run = ManagedRun(
            run_id=str(uuid.uuid4()),
            kind=kind,
            status="queued",
            local_project_id=local_project_id,
            environment_id=environment_id,
            command=shlex.join(argv),
            namespace=namespace,
            output=initial_output,
            termination_signal=termination_signal,
            terminate_process_group=terminate_process_group,
            cancel_grace_seconds=cancel_grace_seconds,
            progress=self._validated_progress_event(initial_progress)
            if initial_progress is not None
            else None,
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._persist_namespace_locked(namespace, force=True)
        threading.Thread(
            target=self._execute,
            args=(
                run,
                argv,
                cwd,
                environment,
                before_command,
                after_success,
                after_failure,
                after_cancel,
                always,
            ),
            daemon=True,
            name=f"workspace-run-{run.run_id[:8]}",
        ).start()
        return run.serialize()

    def start_operation(
        self,
        *,
        kind: str,
        local_project_id: str | None,
        command: str,
        operation: Callable[[ManagedRun], dict[str, object] | None],
        namespace: str = "device",
        initial_output: str = "",
        initial_progress: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Run an in-process I/O operation with the same durable job contract."""
        self._ensure_no_active_run(local_project_id, namespace)
        run = ManagedRun(
            run_id=str(uuid.uuid4()),
            kind=kind,
            status="queued",
            local_project_id=local_project_id,
            environment_id=None,
            command=command,
            namespace=namespace,
            output=initial_output,
            progress=self._validated_progress_event(initial_progress)
            if initial_progress is not None
            else None,
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._persist_namespace_locked(namespace, force=True)
        threading.Thread(
            target=self._execute_operation,
            args=(run, operation),
            daemon=True,
            name=f"workspace-operation-{run.run_id[:8]}",
        ).start()
        return run.serialize()

    def record_completed(
        self,
        *,
        kind: str,
        local_project_id: str | None,
        command: str,
        output: str,
        result_local_project_id: str | None = None,
        namespace: str = "device",
    ) -> dict[str, object]:
        """Record a short, synchronous filesystem operation in run history."""
        timestamp = utc_now()
        run = ManagedRun(
            run_id=str(uuid.uuid4()),
            kind=kind,
            status="succeeded",
            local_project_id=local_project_id,
            environment_id=None,
            command=command,
            created_at=timestamp,
            started_at=timestamp,
            ended_at=timestamp,
            exit_code=0,
            output=output,
            result_local_project_id=result_local_project_id,
            namespace=namespace,
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._persist_namespace_locked(namespace, force=True)
        return run.serialize()

    def get(self, run_id: str, namespace: str | None = None) -> dict[str, object]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or (namespace is not None and run.namespace != namespace):
                raise KeyError(run_id)
            return run.serialize()

    def list_for(
        self,
        local_project_id: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, object]]:
        with self._lock:
            runs = list(self._runs.values())
            if namespace is not None:
                runs = [run for run in runs if run.namespace == namespace]
            if local_project_id:
                runs = [run for run in runs if run.local_project_id == local_project_id]
            return [run.serialize() for run in reversed(runs[-50:])]

    def active_for(
        self,
        local_project_id: str,
        environment_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            active = next(
                (
                    run
                    for run in self._runs.values()
                    if run.local_project_id == local_project_id
                    and (namespace is None or run.namespace == namespace)
                    and run.status in {"queued", "running"}
                    and (environment_id is None or run.environment_id == environment_id)
                ),
                None,
            )
            return active.serialize() if active else None

    def cancel(self, run_id: str, namespace: str | None = None) -> dict[str, object]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or (namespace is not None and run.namespace != namespace):
                raise KeyError(run_id)
            if run.status not in {"queued", "running"}:
                raise RuntimeError("Only a queued or running action can be stopped.")
            run.cancel_requested = True
            process = run.process
            self._persist_namespace_locked(run.namespace, force=True)
        self.append(run, "\nStop requested...\n")
        if process is not None:
            self._request_process_stop(
                process,
                run.termination_signal,
                terminate_process_group=run.terminate_process_group,
            )
            threading.Thread(
                target=self._force_kill_after_timeout,
                args=(process, run.cancel_grace_seconds),
                daemon=True,
                name=f"workspace-stop-{run.run_id[:8]}",
            ).start()
        return self.get(run_id, namespace)

    def remove_for(self, local_project_id: str, namespace: str) -> int:
        """Remove one deleted project's durable history without touching other Tasks."""
        with self._lock:
            active = [
                run
                for run in self._runs.values()
                if run.local_project_id == local_project_id
                and run.namespace == namespace
                and run.status in {"queued", "running"}
            ]
            if active:
                raise RuntimeError(
                    f"A Workspace action is still running ({active[0].run_id})."
                )
            run_ids = [
                run_id
                for run_id, run in self._runs.items()
                if run.local_project_id == local_project_id
                and run.namespace == namespace
            ]
            for run_id in run_ids:
                del self._runs[run_id]
            self._persist_namespace_locked(namespace, force=True)
            return len(run_ids)

    def update_progress(
        self,
        run: ManagedRun,
        *,
        stage: str,
        percent: float,
        message: str,
    ) -> None:
        event = self._validated_progress_event(
            {
                "schemaVersion": 1,
                "stage": stage,
                "percent": percent,
                "message": message,
                "timestamp": utc_now(),
                "metrics": {},
            }
        )
        with self._lock:
            run.progress = event
            self._persist_namespace_locked(run.namespace, force=True)

    def _ensure_no_active_run(
        self, local_project_id: str | None, namespace: str
    ) -> None:
        if local_project_id is None:
            return
        with self._lock:
            active = next(
                (
                    run
                    for run in self._runs.values()
                    if run.local_project_id == local_project_id
                    and run.namespace == namespace
                    and run.status in {"queued", "running"}
                ),
                None,
            )
        if active:
            raise RuntimeError(
                f"A Workspace action is already running ({active.run_id})."
            )

    def append(self, run: ManagedRun, chunk: str) -> None:
        clean = ANSI_ESCAPE.sub("", chunk).replace("\r\n", "\n")
        with self._lock:
            run.output = f"{run.output}{clean}"[-MAX_OUTPUT_CHARS:]
            self._persist_namespace_locked(run.namespace)

    def ingest_output_line(self, run: ManagedRun, line: str) -> None:
        """Store a bounded structured training event or preserve normal output."""
        clean = ANSI_ESCAPE.sub("", line).replace("\r\n", "\n")
        if not clean.startswith(PROGRESS_EVENT_PREFIX):
            self.append(run, clean)
            return
        try:
            raw = json.loads(clean[len(PROGRESS_EVENT_PREFIX) :].strip())
            event = self._validated_progress_event(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            self.append(run, clean)
            return
        point = {
            "timestamp": event["timestamp"],
            "stage": event["stage"],
            "percent": event["percent"],
            "message": event["message"],
            "epoch": event["epoch"],
            "epochs": event["epochs"],
            "batch": event["batch"],
            "totalBatches": event["totalBatches"],
            "step": event["step"],
            "totalSteps": event["totalSteps"],
            "metrics": event["metrics"],
        }
        with self._lock:
            run.progress = event
            if event["metrics"]:
                run.metric_series = [*run.metric_series, point][-MAX_METRIC_POINTS:]
            self._persist_namespace_locked(run.namespace)

    @staticmethod
    def _validated_progress_event(raw: object) -> dict[str, object]:
        if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
            raise ValueError("invalid progress schema")
        stage = str(raw.get("stage") or "")
        if stage not in PROGRESS_STAGES:
            raise ValueError("invalid progress stage")
        percent = float(raw.get("percent"))
        if not math.isfinite(percent) or percent < 0 or percent > 100:
            raise ValueError("invalid progress percent")
        metrics = raw.get("metrics") or {}
        if not isinstance(metrics, dict):
            raise TypeError("invalid progress metrics")
        normalized_metrics: dict[str, float] = {}
        for name, value in metrics.items():
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("invalid progress metric")
            normalized_metrics[str(name)] = number
        normalized: dict[str, object] = {
            "schemaVersion": 1,
            "stage": stage,
            "percent": percent,
            "message": str(raw.get("message") or stage.replace("-", " ").title()),
            "timestamp": str(raw.get("timestamp") or utc_now()),
            "epoch": None,
            "epochs": None,
            "batch": None,
            "totalBatches": None,
            "step": None,
            "totalSteps": None,
            "metrics": normalized_metrics,
        }
        for name in ("epoch", "epochs", "batch", "totalBatches", "totalSteps"):
            value = raw.get(name)
            if value is None:
                continue
            integer = int(value)
            if integer < 1:
                raise ValueError(f"invalid progress {name}")
            normalized[name] = integer
        step = raw.get("step")
        if step is not None:
            integer = int(step)
            if integer < 0:
                raise ValueError("invalid progress step")
            normalized["step"] = integer
        return normalized

    def _execute(
        self,
        run: ManagedRun,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str] | None,
        before_command: Callable[[ManagedRun], None] | None,
        after_success: Callable[[ManagedRun], None] | None,
        after_failure: Callable[[ManagedRun, Exception], None] | None,
        after_cancel: Callable[[ManagedRun], None] | None,
        always: Callable[[], None] | None,
    ) -> None:
        try:
            with self._lock:
                run.status = "running"
                run.started_at = utc_now()
                self._persist_namespace_locked(run.namespace, force=True)
            if before_command:
                before_command(run)
            with self._lock:
                cancelled_before_start = run.cancel_requested
            if cancelled_before_start:
                if after_cancel:
                    after_cancel(run)
                with self._lock:
                    run.status = "cancelled"
                return
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            with self._lock:
                run.process = process
                cancel_after_start = run.cancel_requested
            if cancel_after_start:
                self._request_process_stop(
                    process,
                    run.termination_signal,
                    terminate_process_group=run.terminate_process_group,
                )
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    self.ingest_output_line(run, line)
            exit_code = process.wait()
            with self._lock:
                run.exit_code = exit_code
                run.process = None
                was_cancelled = run.cancel_requested
            if was_cancelled:
                if after_cancel:
                    after_cancel(run)
                self.append(run, "Action stopped.\n")
                with self._lock:
                    run.status = "cancelled"
                return
            if exit_code != 0:
                raise RuntimeError(f"Command exited with code {exit_code}.")
            if after_success:
                after_success(run)
            with self._lock:
                run.status = "succeeded"
        except Exception as error:  # noqa: BLE001 - managed callbacks may raise any error
            self.append(run, f"\nError: {error}\n")
            if after_failure:
                after_failure(run, error)
            with self._lock:
                run.process = None
                run.status = "failed"
                if run.exit_code is None:
                    run.exit_code = 1
        finally:
            if always:
                always()
            with self._lock:
                run.process = None
                run.ended_at = utc_now()
                self._persist_namespace_locked(run.namespace, force=True)

    def _execute_operation(
        self,
        run: ManagedRun,
        operation: Callable[[ManagedRun], dict[str, object] | None],
    ) -> None:
        try:
            with self._lock:
                run.status = "running"
                run.started_at = utc_now()
                self._persist_namespace_locked(run.namespace, force=True)
            result = operation(run)
            with self._lock:
                run.result = result
                run.exit_code = 0
                run.status = "succeeded"
                if run.progress is not None:
                    run.progress = {
                        **run.progress,
                        "stage": "completed",
                        "percent": 100.0,
                        "message": "Release Candidate submitted",
                        "timestamp": utc_now(),
                    }
        except Exception as error:  # noqa: BLE001 - integration operations report through the run
            self.append(run, f"\nError: {error}\n")
            with self._lock:
                run.exit_code = 1
                run.status = "failed"
        finally:
            with self._lock:
                run.ended_at = utc_now()
                self._persist_namespace_locked(run.namespace, force=True)

    @staticmethod
    def _request_process_stop(
        process: subprocess.Popen[str],
        termination_signal: int = signal.SIGTERM,
        *,
        terminate_process_group: bool = True,
    ) -> None:
        try:
            if terminate_process_group:
                os.killpg(process.pid, termination_signal)
            else:
                process.send_signal(termination_signal)
        except ProcessLookupError:
            return

    @staticmethod
    def _force_kill_after_timeout(
        process: subprocess.Popen[str],
        timeout_seconds: float = 3.0,
    ) -> None:
        try:
            process.wait(timeout=timeout_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


_HISTORY_ROOT_VALUE = os.getenv("STUDIO_RUN_HISTORY_DIR", "").strip()
RUN_MANAGER = WorkspaceRunManager(
    Path(_HISTORY_ROOT_VALUE).expanduser() if _HISTORY_ROOT_VALUE else None
)
