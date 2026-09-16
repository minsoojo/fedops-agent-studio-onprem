import json
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio_runtime.jobs import WorkspaceRunManager
from studio_runtime.legacy_workspace import (
    MIGRATION_LOG,
    discover_legacy_projects,
    import_legacy_projects,
)


class AccountWorkspaceTest(unittest.TestCase):
    def test_direct_stop_signals_only_the_supervisor_process(self):
        process = Mock()
        process.pid = 321

        with patch("studio_runtime.jobs.os.killpg") as kill_process_group:
            WorkspaceRunManager._request_process_stop(
                process,
                signal.SIGINT,
                terminate_process_group=False,
            )

        process.send_signal.assert_called_once_with(signal.SIGINT)
        kill_process_group.assert_not_called()

    def test_default_stop_signals_the_entire_process_group(self):
        process = Mock()
        process.pid = 654

        with patch("studio_runtime.jobs.os.killpg") as kill_process_group:
            WorkspaceRunManager._request_process_stop(process, signal.SIGTERM)

        kill_process_group.assert_called_once_with(654, signal.SIGTERM)
        process.send_signal.assert_not_called()

    def test_run_parses_training_progress_without_polluting_logs(self):
        manager = WorkspaceRunManager()
        event = {
            "schemaVersion": 1,
            "stage": "training",
            "percent": 42.5,
            "message": "Training local model",
            "timestamp": "2026-08-14T00:00:00+00:00",
            "epoch": 1,
            "epochs": 2,
            "batch": 5,
            "totalBatches": 10,
            "step": 0,
            "totalSteps": 20,
            "metrics": {"training_loss": 0.25},
        }
        with tempfile.TemporaryDirectory() as directory:
            started = manager.start_command(
                kind="local-train",
                local_project_id="local:mnist",
                argv=[
                    sys.executable,
                    "-c",
                    (
                        "import json; "
                        f"print('FEDOPS_PROGRESS ' + json.dumps({event!r})); "
                        "print('human-readable output')"
                    ),
                ],
                cwd=Path(directory),
            )
            for _ in range(200):
                current = manager.get(str(started["runId"]))
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)

        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["progress"]["percent"], 42.5)
        self.assertEqual(current["progress"]["metrics"]["training_loss"], 0.25)
        self.assertEqual(current["progress"]["step"], 0)
        self.assertEqual(current["metricSeries"][0]["totalSteps"], 20)
        self.assertEqual(len(current["metricSeries"]), 1)
        self.assertIn("human-readable output", current["output"])
        self.assertNotIn("FEDOPS_PROGRESS", current["output"])

    def test_run_history_is_filtered_by_account_namespace(self):
        manager = WorkspaceRunManager()
        run_a = manager.record_completed(
            kind="create",
            local_project_id="local:same-task",
            command="create a",
            output="a",
            namespace="account-a",
        )
        run_b = manager.record_completed(
            kind="create",
            local_project_id="local:same-task",
            command="create b",
            output="b",
            namespace="account-b",
        )

        self.assertEqual(
            [
                item["runId"]
                for item in manager.list_for("local:same-task", "account-a")
            ],
            [run_a["runId"]],
        )
        self.assertEqual(
            [
                item["runId"]
                for item in manager.list_for("local:same-task", "account-b")
            ],
            [run_b["runId"]],
        )
        with self.assertRaises(KeyError):
            manager.get(str(run_a["runId"]), "account-b")
        with self.assertRaises(KeyError):
            manager.cancel(str(run_a["runId"]), "account-b")

    def test_run_history_survives_a_runtime_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            history_root = Path(directory) / "runtime-history"
            first = WorkspaceRunManager(history_root)
            recorded = first.record_completed(
                kind="local-train",
                local_project_id="local:mnist",
                command="fedops-task local-train",
                output="training complete\n",
                namespace="account-a",
            )

            restored = WorkspaceRunManager(history_root)
            history = restored.list_for("local:mnist", "account-a")

            self.assertEqual([item["runId"] for item in history], [recorded["runId"]])
            self.assertEqual(history[0]["status"], "succeeded")
            self.assertEqual(history[0]["output"], "training complete\n")

    def test_deleted_project_history_does_not_return_when_name_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            history_root = Path(directory) / "runtime-history"
            manager = WorkspaceRunManager(history_root)
            manager.record_completed(
                kind="local-train",
                local_project_id="local:mnist",
                command="local train",
                output="old task run\n",
                namespace="account-a",
            )
            manager.record_completed(
                kind="local-train",
                local_project_id="local:other",
                command="local train",
                output="other task run\n",
                namespace="account-a",
            )

            removed = manager.remove_for("local:mnist", "account-a")
            restored = WorkspaceRunManager(history_root)

            self.assertEqual(removed, 1)
            self.assertEqual(restored.list_for("local:mnist", "account-a"), [])
            self.assertEqual(len(restored.list_for("local:other", "account-a")), 1)

    def test_in_process_operation_exposes_progress_and_result(self):
        manager = WorkspaceRunManager()

        def operation(run):
            manager.update_progress(
                run,
                stage="exporting",
                percent=60,
                message="Uploading Release snapshot",
            )
            return {"releaseId": "release-1", "revision": 1}

        started = manager.start_operation(
            kind="release-candidate",
            local_project_id="local:mnist",
            command="Submit Release Candidate",
            operation=operation,
            initial_progress={
                "schemaVersion": 1,
                "stage": "preparing",
                "percent": 1,
                "message": "Preparing Release Candidate",
                "timestamp": "2026-08-19T00:00:00+00:00",
                "metrics": {},
            },
        )
        for _ in range(100):
            current = manager.get(str(started["runId"]))
            if current["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)

        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["progress"]["percent"], 100)
        self.assertEqual(current["result"]["releaseId"], "release-1")

    def test_incomplete_run_is_restored_as_interrupted_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history_root = Path(directory) / "runtime-history"
            first = WorkspaceRunManager(history_root)
            first.record_completed(
                kind="release-readiness",
                local_project_id="local:mnist",
                command="fedops-task check-readiness",
                output="checking\n",
                namespace="account-a",
            )
            history_file = next(history_root.glob("*.json"))
            payload = json.loads(history_file.read_text(encoding="utf-8"))
            payload["runs"][0]["status"] = "running"
            payload["runs"][0]["endedAt"] = None
            history_file.write_text(json.dumps(payload), encoding="utf-8")

            restored = WorkspaceRunManager(history_root)
            run = restored.list_for("local:mnist", "account-a")[0]

            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["exitCode"], 1)
            self.assertIn(
                "Agent Studio stopped before this action completed", run["output"]
            )

    def test_legacy_projects_move_only_after_explicit_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "fedops-workspace"
            account_workspace = base / "accounts" / "account-a" / "projects"
            legacy = base / "fedops-existing"
            legacy.mkdir(parents=True)
            (legacy / "model.py").write_text("value = 1\n", encoding="utf-8")
            account_workspace.mkdir(parents=True)

            found = discover_legacy_projects(base, account_workspace)
            self.assertEqual([item["name"] for item in found], ["fedops-existing"])
            self.assertTrue(legacy.is_dir())
            self.assertFalse((account_workspace / legacy.name).exists())

            result = import_legacy_projects(
                base,
                account_workspace,
                "account-a",
                [legacy.name],
            )

            self.assertEqual(result["imported"], 1)
            self.assertFalse(legacy.exists())
            self.assertTrue(
                (account_workspace / "fedops-existing" / "model.py").is_file()
            )
            history = json.loads((base / MIGRATION_LOG).read_text(encoding="utf-8"))
            self.assertEqual(history[-1]["accountKey"], "account-a")
            self.assertNotIn("userId", history[-1])

    def test_legacy_import_rejects_an_account_name_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            account_workspace = base / "accounts" / "account-a" / "projects"
            (base / "fedops-conflict").mkdir()
            (account_workspace / "fedops-conflict").mkdir(parents=True)

            found = discover_legacy_projects(base, account_workspace)
            self.assertTrue(found[0]["conflict"])
            with self.assertRaises(FileExistsError):
                import_legacy_projects(
                    base,
                    account_workspace,
                    "account-a",
                    ["fedops-conflict"],
                )


if __name__ == "__main__":
    unittest.main()
