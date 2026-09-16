import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio_runtime.federated_learning import (
    ParticipationManager,
    _merge_live_run_events,
    _normalize_model_lifecycle_events,
    _confirmed_completion,
    _read_json,
    _preflight_is_current,
    _write_json,
    participation_preflight,
)
from studio_runtime.federated_task import source_fingerprint


def manifest(fingerprint: str) -> dict:
    return {
        "schemaVersion": 1,
        "task": {
            "taskId": "task-1",
            "runtimeKey": "mnist-runtime",
            "title": "MNIST",
            "runtimeContract": {"name": "federated-task-v3", "schemaVersion": 3},
        },
        "participation": {"role": "owner", "status": "approved"},
        "release": {
            "releaseId": "release-1",
            "sourceFingerprint": fingerprint,
            "bundleSha256": "a" * 64,
        },
        "globalModel": {
            "modelVersionId": "model-1",
            "version": 1,
            "sha256": "b" * 64,
            "parameterSignatureFingerprint": "signature-1",
        },
        "server": {
            "state": "ready",
            "ready": True,
            "managerUrl": "http://manager.test",
            "aggregationServer": "server.test:40026",
        },
        "campaign": {"rounds": 2, "clientsPerRound": 2},
    }


class FederatedLearningRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.project = root / "projects" / "mnist"
        self.project.mkdir(parents=True)
        self.data_root = root / ".local-data"
        self.data = self.data_root / "mnist"
        self.data.mkdir(parents=True)
        (self.project / "pyproject.toml").write_text("[project]\nname='mnist'\nversion='0.1.0'\n")
        metadata = self.project / ".fedops-studio"
        metadata.mkdir()
        (metadata / "task-binding.json").write_text(json.dumps({
            "schemaVersion": 1,
            "taskId": "task-1",
            "runtimeKey": "mnist-runtime",
            "displayName": "MNIST",
            "releaseId": "release-1",
            "modelVersionId": "model-1",
        }))
        fingerprint = source_fingerprint(self.project)
        (metadata / "readiness-participation.json").write_text(json.dumps({
            "ok": True,
            "mode": "participation",
            "sourceFingerprint": fingerprint,
            "parameterSignatureFingerprint": "signature-1",
        }))
        self.manifest = manifest(fingerprint)

    def tearDown(self):
        self.temporary.cleanup()

    def test_preflight_accepts_only_matching_release_and_local_data(self):
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["releaseId"], "release-1")
        self.assertIsNone(report["campaignRunId"])
        self.assertTrue(report["sourceFingerprint"])
        self.assertTrue(report["dataFingerprint"])
        self.assertTrue(all(item["status"] == "passed" for item in report["checks"]))
        self.assertFalse(any(item["id"] == "federation-server" for item in report["checks"]))
        self.assertTrue(report["serverAvailability"]["ready"])

        snapshot = ParticipationManager().snapshot(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )
        self.assertTrue(snapshot["workspace"]["readyToStart"])
        self.assertEqual(snapshot["workspace"]["participationPreflight"]["checkedAt"], report["checkedAt"])

    def test_server_availability_is_separate_from_local_participation_readiness(self):
        self.manifest["server"] = {
            "state": "stopped",
            "ready": False,
            "managerUrl": "http://manager.test",
            "aggregationServer": None,
        }
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )

        self.assertTrue(report["ok"])
        self.assertTrue(_preflight_is_current(self.project, self.manifest, report))
        self.assertFalse(report["serverAvailability"]["ready"])
        snapshot = ParticipationManager().snapshot(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )
        self.assertTrue(snapshot["workspace"]["participationReadyToStart"])
        self.assertFalse(snapshot["workspace"]["serverAvailability"]["ready"])
        self.assertFalse(snapshot["workspace"]["readyToStart"])

        self.manifest["server"].update({
            "state": "ready",
            "ready": True,
            "aggregationServer": "server.test:40026",
        })
        live_snapshot = ParticipationManager().snapshot(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )
        self.assertTrue(live_snapshot["workspace"]["participationReadyToStart"])
        self.assertTrue(live_snapshot["workspace"]["serverAvailability"]["ready"])
        self.assertTrue(live_snapshot["workspace"]["readyToStart"])

    def test_start_rejects_an_offline_server_without_invalidating_readiness(self):
        self.manifest["server"] = {
            "state": "stopped",
            "ready": False,
            "managerUrl": "http://manager.test",
            "aggregationServer": None,
        }
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
        self.assertTrue(report["ok"])

        with self.assertRaisesRegex(ValueError, "Federated Server is not live"):
            ParticipationManager().start(
                account_key="account-0000000000000000",
                project_root=self.project,
                local_project_id="local:mnist",
                manifest=self.manifest,
                data_path=str(self.data),
                allowed_data_root=self.data_root,
            )

    def test_different_tasks_can_run_concurrently_with_unique_local_ports(self):
        second_project = self.project.parent / "calorie"
        second_project.mkdir()
        (second_project / "pyproject.toml").write_text(
            "[project]\nname='calorie'\nversion='0.1.0'\n"
        )
        second_data = self.data_root / "calorie"
        second_data.mkdir()
        second_metadata = second_project / ".fedops-studio"
        second_metadata.mkdir()
        second_manifest = copy.deepcopy(self.manifest)
        second_manifest["task"].update({
            "taskId": "task-2",
            "runtimeKey": "calorie-runtime",
            "title": "Calorie",
        })
        second_manifest["release"].update({"releaseId": "release-2"})
        second_manifest["globalModel"].update({"modelVersionId": "model-2"})
        (second_metadata / "task-binding.json").write_text(json.dumps({
            "schemaVersion": 1,
            "taskId": "task-2",
            "runtimeKey": "calorie-runtime",
            "displayName": "Calorie",
            "releaseId": "release-2",
            "modelVersionId": "model-2",
        }))
        second_fingerprint = source_fingerprint(second_project)
        (second_metadata / "readiness-participation.json").write_text(json.dumps({
            "ok": True,
            "mode": "participation",
            "sourceFingerprint": second_fingerprint,
            "parameterSignatureFingerprint": "signature-1",
        }))

        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
            participation_preflight(
                second_project,
                "local:calorie",
                second_manifest,
                str(second_data),
                self.data_root,
            )

        manager = ParticipationManager()
        runtime_command = [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
        ]
        try:
            with patch(
                "studio_runtime.federated_learning.uv_run_command",
                return_value=(runtime_command, os.environ.copy(), selected),
            ):
                first = manager.start(
                    account_key="account-0000000000000000",
                    project_root=self.project,
                    local_project_id="local:mnist",
                    manifest=self.manifest,
                    data_path=str(self.data),
                    allowed_data_root=self.data_root,
                )
                with self.assertRaisesRegex(RuntimeError, "already has an active Client"):
                    manager.start(
                        account_key="account-0000000000000000",
                        project_root=self.project,
                        local_project_id="local:mnist",
                        manifest=self.manifest,
                        data_path=str(self.data),
                        allowed_data_root=self.data_root,
                    )
                second = manager.start(
                    account_key="account-0000000000000000",
                    project_root=second_project,
                    local_project_id="local:calorie",
                    manifest=second_manifest,
                    data_path=str(second_data),
                    allowed_data_root=self.data_root,
                )

            first_ports = {first["runtime"]["managerPort"], first["runtime"]["clientPort"]}
            second_ports = {second["runtime"]["managerPort"], second["runtime"]["clientPort"]}
            self.assertEqual(len(first_ports), 2)
            self.assertEqual(len(second_ports), 2)
            self.assertTrue(first_ports.isdisjoint(second_ports))
            self.assertIsNotNone(manager._active("account-0000000000000000", "local:mnist"))
            self.assertIsNotNone(manager._active("account-0000000000000000", "local:calorie"))
        finally:
            for project, project_id, selected_manifest in (
                (self.project, "local:mnist", self.manifest),
                (second_project, "local:calorie", second_manifest),
            ):
                if manager._active("account-0000000000000000", project_id):
                    manager.stop(
                        "account-0000000000000000",
                        project,
                        project_id,
                        selected_manifest,
                    )
            deadline = time.monotonic() + 5
            while manager._reserved_ports and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(manager._reserved_ports, set())

    def test_preflight_must_match_the_current_campaign_run(self):
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
        self.assertTrue(_preflight_is_current(self.project, self.manifest, report))
        self.manifest["campaignRun"] = {
            "runId": "run-new",
            "baseGlobalModelVersion": 1,
            "targetGlobalModelVersion": 2,
        }
        self.assertFalse(_preflight_is_current(self.project, self.manifest, report))

    def test_stale_running_state_recovers_as_disconnected(self):
        state_root = self.project / ".fedops-studio" / "participation"
        state_root.mkdir()
        (state_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1,
            "status": "running",
            "clientState": "training",
            "runId": "lost-run",
        }))
        snapshot = ParticipationManager().snapshot(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )
        self.assertEqual(snapshot["runtime"]["status"], "disconnected")
        self.assertEqual(snapshot["runtime"]["clientState"], "disconnected")

    def test_completed_client_acknowledgment_preserves_success(self):
        state_root = self.project / ".fedops-studio" / "participation"
        state_root.mkdir()
        (state_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1,
            "status": "completed",
            "clientState": "completed",
            "runId": "completed-run",
            "endedAt": "2026-08-14T01:00:00+00:00",
        }))
        snapshot = ParticipationManager().stop(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )
        self.assertEqual(snapshot["runtime"]["status"], "completed")
        self.assertEqual(snapshot["runtime"]["clientState"], "completed")

    def test_completed_snapshot_keeps_persisted_batch_metrics(self):
        state_root = self.project / ".fedops-studio" / "participation"
        run_root = state_root / "runs" / "campaign-completed"
        run_root.mkdir(parents=True)
        (state_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1,
            "status": "completed",
            "clientState": "completed",
            "campaignRunId": "campaign-completed",
            "sessionId": "campaign-completed",
            "runId": "completed-process",
        }))
        (run_root / "events.jsonl").write_text(json.dumps({
            "schemaVersion": 1,
            "timestamp": "2026-08-14T01:00:02+00:00",
            "stage": "global_model_updated",
            "round": 1,
        }) + "\n")
        (run_root / "local-metrics.jsonl").write_text("".join(
            json.dumps({
                "schemaVersion": 1,
                "timestamp": f"2026-08-14T01:00:0{batch}+00:00",
                "stage": "training",
                "source": "local-training-runtime",
                "round": 1,
                "epoch": 1,
                "batch": batch,
                "totalBatches": 2,
                "metrics": {"training_loss": 1.0 / batch},
            }) + "\n"
            for batch in (1, 2)
        ))
        self.manifest["campaignRun"] = {
            "runId": "campaign-completed",
            "baseGlobalModelVersion": 0,
            "targetGlobalModelVersion": 1,
            "campaign": {"rounds": 1, "clientsPerRound": 1},
        }

        snapshot = ParticipationManager().snapshot(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )

        metric_events = [
            event for event in snapshot["events"]
            if event.get("source") == "local-training-runtime"
        ]
        self.assertEqual([event["batch"] for event in metric_events], [1, 2])
        self.assertEqual(metric_events[-1]["metrics"]["training_loss"], 0.5)

    def test_new_campaign_run_does_not_reuse_previous_round_events(self):
        state_root = self.project / ".fedops-studio" / "participation"
        old_events = state_root / "runs" / "run-old" / "events.jsonl"
        old_events.parent.mkdir(parents=True)
        old_events.write_text(json.dumps({
            "schemaVersion": 1,
            "timestamp": "2026-08-14T01:00:00+00:00",
            "stage": "completed",
            "round": 5,
        }) + "\n")
        (state_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1,
            "status": "completed",
            "clientState": "completed",
            "campaignRunId": "run-old",
            "runId": "local-old",
        }))
        self.manifest["campaignRun"] = {
            "runId": "run-new",
            "baseGlobalModelVersion": 1,
            "targetGlobalModelVersion": 2,
        }

        snapshot = ParticipationManager().snapshot(
            "account-0000000000000000",
            self.project,
            "local:mnist",
            self.manifest,
        )

        self.assertEqual(snapshot["campaignRun"]["runId"], "run-new")
        self.assertEqual(snapshot["runtime"]["status"], "not_started")
        self.assertEqual(snapshot["runtime"]["campaignRunId"], "run-new")
        self.assertEqual(snapshot["events"], [])
        persisted = _read_json(state_root / "state.json")
        self.assertEqual(persisted["campaignRunId"], "run-new")
        self.assertFalse(snapshot["workspace"]["readyToStart"])

    def test_preflight_reports_local_data_failure_without_starting(self):
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data_root / "missing"),
                self.data_root,
            )
        self.assertFalse(report["ok"])
        local_data = next(item for item in report["checks"] if item["id"] == "local-data")
        self.assertEqual(local_data["status"], "failed")

    def test_preflight_allows_locally_valid_source_that_differs_from_release_archive(self):
        self.manifest["release"]["sourceFingerprint"] = "published-source"
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
        self.assertTrue(report["ok"])

    def test_preflight_reports_parameter_contract_mismatch(self):
        self.manifest["globalModel"]["parameterSignatureFingerprint"] = "different"
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
        contract = next(item for item in report["checks"] if item["id"] == "parameter-contract")
        self.assertEqual(contract["status"], "failed")

    def test_preflight_reports_release_update_without_relabeling_readiness(self):
        binding_path = self.project / ".fedops-studio" / "task-binding.json"
        binding = json.loads(binding_path.read_text())
        binding["releaseId"] = "release-old"
        binding_path.write_text(json.dumps(binding))
        selected = {"environmentId": "env-default", "name": "Default"}
        with patch(
            "studio_runtime.federated_learning.uv_run_command",
            return_value=(["uv", "run"], {}, selected),
        ):
            report = participation_preflight(
                self.project,
                "local:mnist",
                self.manifest,
                str(self.data),
                self.data_root,
            )
        readiness = next(item for item in report["checks"] if item["id"] == "participation-readiness")
        release = next(item for item in report["checks"] if item["id"] == "release-identity")
        self.assertEqual(readiness["status"], "passed")
        self.assertEqual(release["status"], "failed")

    def test_concurrent_state_writes_use_independent_atomic_files(self):
        state_path = self.project / ".fedops-studio" / "participation" / "state.json"
        errors: list[Exception] = []

        def writer(index: int) -> None:
            try:
                _write_json(state_path, {"schemaVersion": 1, "writer": index})
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(_read_json(state_path)["schemaVersion"], 1)
        self.assertEqual(list(state_path.parent.glob("*.tmp")), [])

    def test_live_training_progress_is_joined_with_transport_events(self):
        events = [{
            "schemaVersion": 1,
            "timestamp": "2026-08-14T01:00:00+00:00",
            "stage": "training",
            "round": 2,
            "progress": 0.0,
            "training": {"batchSize": 128, "localEpochs": 1},
        }]
        run = {
            "progress": {
                "schemaVersion": 1,
                "timestamp": "2026-08-14T01:00:01+00:00",
                "stage": "training",
                "percent": 42.5,
                "message": "Training local model",
                "epoch": 1,
                "epochs": 1,
                "batch": 25,
                "totalBatches": 100,
                "step": 25,
                "totalSteps": 100,
                "metrics": {"training_loss": 0.25},
            },
            "metricSeries": [],
        }

        merged = _merge_live_run_events(
            events,
            run,
            task_id="task-1",
            release_id="release-1",
            client_instance_id="client-1",
        )

        live = merged[-1]
        self.assertEqual(live["source"], "local-training-runtime")
        self.assertEqual(live["round"], 2)
        self.assertEqual(live["sampleCount"], 3200)
        self.assertEqual(live["step"], 25)
        self.assertEqual(live["totalSteps"], 100)
        self.assertEqual(live["metrics"]["training_loss"], 0.25)
        self.assertAlmostEqual(live["stageProgress"], 50.0)

    def test_first_campaign_distinguishes_initiative_and_published_global_model(self):
        events = [
            {"schemaVersion": 1, "timestamp": "1", "stage": "downloading_global", "round": 1, "globalModelVersion": 1},
            {"schemaVersion": 1, "timestamp": "2", "stage": "global_model_updated", "round": 1, "globalModelVersion": 1},
            {"schemaVersion": 1, "timestamp": "3", "stage": "downloading_global", "round": 2, "globalModelVersion": 1},
            {"schemaVersion": 1, "timestamp": "4", "stage": "global_model_updated", "round": 2, "globalModelVersion": 1},
        ]
        normalized = _normalize_model_lifecycle_events(
            events,
            model={"version": 1},
            campaign={"rounds": 2},
        )
        self.assertEqual(normalized[0]["modelLabel"], "Initiative Model")
        self.assertEqual(normalized[1]["modelLabel"], "Round 1 Aggregate")
        self.assertEqual(normalized[2]["modelLabel"], "Round 1 Aggregate")
        self.assertEqual(normalized[3]["modelLabel"], "Global Model v1")
        self.assertEqual(normalized[3]["aggregationScope"], "campaign-final")

    def test_history_is_grouped_by_campaign_and_selectable_by_round(self):
        history_root = self.project / ".fedops-studio" / "participation" / "runs"
        first = history_root / "campaign-first"
        second = history_root / "campaign-second"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "session.json").write_text(json.dumps({
            "schemaVersion": 1,
            "sessionId": "campaign-first",
            "campaignRunId": "campaign-first",
            "status": "completed",
            "baseGlobalModelVersion": 0,
            "targetGlobalModelVersion": 1,
            "campaign": {"rounds": 2, "clientsPerRound": 2},
            "startedAt": "2026-08-14T01:00:00+00:00",
            "endedAt": "2026-08-14T01:02:00+00:00",
        }))
        first_events = [
            {"schemaVersion": 1, "timestamp": "2026-08-14T01:00:01+00:00", "stage": "training", "round": 1},
            {"schemaVersion": 1, "timestamp": "2026-08-14T01:01:01+00:00", "stage": "global_model_updated", "round": 1},
            {"schemaVersion": 1, "timestamp": "2026-08-14T01:01:02+00:00", "stage": "training", "round": 2},
            {"schemaVersion": 1, "timestamp": "2026-08-14T01:02:00+00:00", "stage": "global_model_updated", "round": 2},
        ]
        (first / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in first_events)
        )
        (second / "session.json").write_text(json.dumps({
            "schemaVersion": 1,
            "sessionId": "campaign-second",
            "status": "stopped",
            "baseGlobalModelVersion": 1,
            "targetGlobalModelVersion": 2,
            "startedAt": "2026-08-14T02:00:00+00:00",
        }))

        manager = ParticipationManager()
        sessions = manager.history_sessions(self.project, self.manifest)
        detail = manager.history_session(
            self.project,
            self.manifest,
            "campaign-first",
        )

        self.assertEqual([item["sessionId"] for item in sessions], [
            "campaign-second", "campaign-first",
        ])
        first_summary = next(
            item for item in sessions if item["sessionId"] == "campaign-first"
        )
        self.assertEqual(first_summary["rounds"], [1, 2])
        self.assertEqual(first_summary["completedRounds"], 2)
        self.assertEqual(detail["session"]["targetGlobalModelVersion"], 1)
        self.assertEqual(
            {event["round"] for event in detail["events"]},
            {1, 2},
        )

        offline_detail = manager.history_session(
            self.project,
            {},
            "campaign-first",
        )
        self.assertEqual(offline_detail["session"]["rounds"], [1, 2])

    def test_completed_event_uses_the_last_server_observed_round(self):
        normalized = _normalize_model_lifecycle_events(
            [
                {"schemaVersion": 1, "timestamp": "1", "stage": "training", "round": 5},
                {"schemaVersion": 1, "timestamp": "2", "stage": "completed", "round": 2},
            ],
            model={"version": 3},
            campaign={"rounds": 5},
        )

        self.assertEqual(normalized[-1]["round"], 5)
        self.assertEqual(normalized[-1]["modelLabel"], "Global Model v3")

    def test_campaign_initial_transport_version_is_not_used_as_round(self):
        normalized = _normalize_model_lifecycle_events(
            [
                {"schemaVersion": 1, "timestamp": "1", "stage": "connecting", "round": 6},
                {"schemaVersion": 1, "timestamp": "2", "stage": "waiting_round", "round": 6},
            ],
            model={"version": 1},
            campaign={"rounds": 5},
        )

        self.assertEqual([event["round"] for event in normalized], [1, 1])
        self.assertEqual(
            [event["transportReportedRound"] for event in normalized],
            [6, 6],
        )

    def test_failure_model_version_never_becomes_a_round(self):
        events = _normalize_model_lifecycle_events([
            {"stage": "training", "round": 2},
            {"stage": "failed", "round": 40},
        ], model={"version": 40}, campaign={"rounds": 2})
        self.assertEqual(events[-1]["round"], 2)
        self.assertEqual(events[-1]["transportReportedRound"], 40)
        before_training = _normalize_model_lifecycle_events(
            [{"stage": "failed", "round": 40}], model={"version": 40}, campaign={"rounds": 2})
        self.assertIsNone(before_training[0]["round"])

    def test_completion_requires_same_run_release_and_final_local_evaluation(self):
        state = {"campaignRunId": "run-a", "releaseId": "release-1", "taskId": "task-1",
                 "targetGlobalModelVersion": 40, "status": "running"}
        campaign = {"runId": "run-a", "releaseId": "release-1", "status": "completed",
                    "endedAt": "2026-09-08T03:00:00Z", "targetGlobalModelVersion": 40,
                    "campaign": {"rounds": 2}}
        events = [{"stage": "global_model_updated", "round": 2, "taskId": "task-1",
                   "releaseId": "release-1", "targetGlobalModelVersion": 40, "timestamp": "1"},
                  {"stage": "failed", "errorType": "_MultiThreadedRendezvous", "timestamp": "2",
                   "message": "UNAVAILABLE: Stream removed (Socket closed)"}]
        completion = _confirmed_completion(state, campaign, events)
        self.assertEqual(completion["stage"], "completed")
        self.assertEqual(completion["round"], 2)
        self.assertEqual(events[-1]["stage"], "failed")  # Original evidence retained.
        for override in ({"runId": "different"}, {"releaseId": "different"},
                         {"status": "failed"}, {"status": "running"}, {"endedAt": None},
                         {"targetGlobalModelVersion": 41}):
            with self.subTest(override=override):
                self.assertIsNone(_confirmed_completion(state, {**campaign, **override}, events))
        for override in ({"status": "stopped"}, {"stopRequestedAt": "now"}):
            self.assertIsNone(_confirmed_completion({**state, **override}, campaign, events))
        self.assertIsNone(_confirmed_completion(state, campaign, events[1:]))
        for failure in ({"errorType": "ValueError", "message": "bad labels"},
                        {"message": "UNAVAILABLE: network outage"}, {"timestamp": "0"}):
            self.assertIsNone(_confirmed_completion(state, campaign, [events[0], {**events[1], **failure}]))

    def test_snapshot_persists_confirmed_completion_and_preserves_raw_failure(self):
        state_root = self.project / ".fedops-studio" / "participation"
        run_root = state_root / "runs" / "campaign-end"
        run_root.mkdir(parents=True)
        state = {"schemaVersion": 1, "status": "failed", "clientState": "failed",
                 "campaignRunId": "campaign-end", "sessionId": "campaign-end", "taskId": "task-1",
                 "releaseId": "release-1", "targetGlobalModelVersion": 40}
        _write_json(state_root / "state.json", state)
        _write_json(run_root / "session.json", {**state, "campaign": {"rounds": 2}})
        events = [
            {"stage": "global_model_updated", "round": 2, "taskId": "task-1", "releaseId": "release-1",
             "targetGlobalModelVersion": 40, "timestamp": "2026-09-08T03:00:00Z"},
            {"stage": "failed", "round": 40, "errorType": "_MultiThreadedRendezvous",
             "message": "UNAVAILABLE: Stream removed (Socket closed)", "timestamp": "2026-09-08T03:00:01Z"},
        ]
        (run_root / "events.jsonl").write_text("\n".join(json.dumps({"schemaVersion": 1, **event}) for event in events) + "\n")
        self.manifest["campaignRun"] = {"runId": "campaign-end", "releaseId": "release-1",
            "status": "completed", "endedAt": "2026-09-08T03:00:01Z",
            "targetGlobalModelVersion": 40, "campaign": {"rounds": 2}}
        manager = ParticipationManager()
        result = manager.snapshot("account-0000000000000000", self.project, "local:mnist", self.manifest)
        self.assertEqual(result["runtime"]["status"], "completed")
        self.assertEqual(result["latestEvent"]["round"], 2)
        self.assertIn('"failed"', (run_root / "events.jsonl").read_text())
        again = manager.snapshot("account-0000000000000000", self.project, "local:mnist", self.manifest)
        self.assertEqual(len(result["events"]), len(again["events"]))
        history = manager.history_sessions(self.project, self.manifest)[0]
        self.assertEqual(history["status"], "completed")
        self.assertEqual(history["rounds"], [2])

    def test_legacy_initial_round_is_preserved_without_campaign_contract(self):
        normalized = _normalize_model_lifecycle_events(
            [{"schemaVersion": 1, "timestamp": "1", "stage": "waiting_round", "round": 3}],
            model={"version": 3},
            campaign=None,
        )

        self.assertEqual(normalized[0]["round"], 3)
        self.assertNotIn("transportReportedRound", normalized[0])


if __name__ == "__main__":
    unittest.main()
