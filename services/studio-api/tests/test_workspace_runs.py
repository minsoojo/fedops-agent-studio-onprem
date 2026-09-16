import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request
from studio_api.features.workspace.router import (
    RUN_MANAGER as WORKSPACE_RUN_MANAGER,
)
from studio_api.features.workspace.router import (
    create_workspace,
    get_release_submission_readiness,
    remove_workspace,
    router,
    start_workspace_action,
    submit_release_candidate,
)
from studio_api.features.workspace.schemas import (
    CreateWorkspaceRequest,
    DeleteWorkspaceRequest,
    WorkspaceActionRequest,
)
from studio_api.session import AUTH_SESSIONS
from studio_runtime.federated_task import (
    validate_task_contract,
    workspace_directory_name,
)
from studio_runtime.jobs import WorkspaceRunManager


class WorkspaceRunsTest(unittest.TestCase):
    def tearDown(self):
        AUTH_SESSIONS.clear()

    def test_normalizes_a_discoverable_workspace_directory(self):
        self.assertEqual(workspace_directory_name("My First Task"), "my-first-task")
        self.assertEqual(workspace_directory_name("FedOps ECG"), "fedops-ecg")
        self.assertEqual(workspace_directory_name("한글 태스크"), "한글-태스크")
        with self.assertRaises(ValueError):
            workspace_directory_name("---")

    def test_managed_command_captures_real_status_and_output(self):
        manager = WorkspaceRunManager()
        with tempfile.TemporaryDirectory() as directory:
            started = manager.start_command(
                kind="validate",
                local_project_id="local:fedops-test",
                argv=[sys.executable, "-c", "print('validation-ok')"],
                cwd=Path(directory),
            )
            for _ in range(100):
                current = manager.get(str(started["runId"]))
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)

        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["exitCode"], 0)
        self.assertIn("validation-ok", current["output"])

    def test_managed_command_can_be_cancelled(self):
        manager = WorkspaceRunManager()
        with tempfile.TemporaryDirectory() as directory:
            started = manager.start_command(
                kind="run-file",
                local_project_id="local:fedops-cancel",
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=Path(directory),
            )
            for _ in range(100):
                current = manager.get(str(started["runId"]))
                if current["status"] == "running":
                    break
                time.sleep(0.01)
            manager.cancel(str(started["runId"]))
            for _ in range(100):
                current = manager.get(str(started["runId"]))
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)

        self.assertEqual(current["status"], "cancelled")
        self.assertIn("Action stopped", current["output"])

    def test_validates_fedops_task_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "fedops-test"
[tool.fedops.task]
schema-version = 1
task-type = "silo"
runtime-module = "fedops_test.task_runtime"
model-file = "model.py"
data-preparation-file = "data.py"
config-file = "config.toml"
""",
                encoding="utf-8",
            )
            for name in ("model.py", "data.py", "config.toml"):
                (root / name).touch()
            _, messages = validate_task_contract(root)

        self.assertIn("✓ FedOps Task: fedops-test", messages)

    def test_workspace_action_routes_are_registered(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/v1/workspaces/{local_project_id}/actions", paths)
        self.assertIn("/api/v1/workspaces/runs", paths)
        self.assertIn("/api/v1/workspaces/runs/{run_id}", paths)
        self.assertIn("/api/v1/workspaces/runs/{run_id}/cancel", paths)
        self.assertIn("/api/v1/workspaces/{local_project_id}/binding", paths)
        self.assertIn("/api/v1/workspaces/{local_project_id}/release-candidate", paths)
        self.assertIn(
            "/api/v1/workspaces/{local_project_id}/release-submission-readiness",
            paths,
        )
        self.assertIn("/api/v1/workspaces/{local_project_id}/data-binding", paths)
        self.assertIn("/api/v1/workspaces/{local_project_id}/open-data-folder", paths)

    def test_release_candidate_starts_a_durable_background_run(self):
        account = SimpleNamespace(
            account_key="account-a",
            workspace_root=Path("/workspace/account-a/projects"),
        )
        request = Request({"type": "http", "method": "POST", "path": "/"})
        started = {
            "runId": "release-run",
            "kind": "release-candidate",
            "status": "queued",
        }
        with (
            patch(
                "studio_api.features.workspace.router.require_account",
                return_value=account,
            ),
            patch(
                "studio_api.features.workspace.router.project_for",
                return_value=({}, Path("/workspace/account-a/projects/mnist")),
            ),
            patch(
                "studio_api.features.workspace.router.require_fedops_session",
                return_value={"fedops_auth": {"accessToken": "test"}},
            ),
            patch(
                "studio_api.features.workspace.router.release_inputs",
                return_value={},
            ),
            patch.object(
                WORKSPACE_RUN_MANAGER, "start_operation", return_value=started
            ) as start_operation,
        ):
            result = submit_release_candidate("local:mnist", request)

        self.assertEqual(result, started)
        self.assertEqual(start_operation.call_args.kwargs["kind"], "release-candidate")
        self.assertEqual(start_operation.call_args.kwargs["namespace"], "account-a")

    def test_release_candidate_is_rejected_before_start_when_readiness_is_invalid(self):
        account = SimpleNamespace(
            account_key="account-a",
            workspace_root=Path("/workspace/account-a/projects"),
        )
        request = Request({"type": "http", "method": "POST", "path": "/"})
        with (
            patch(
                "studio_api.features.workspace.router.require_account",
                return_value=account,
            ),
            patch(
                "studio_api.features.workspace.router.project_for",
                return_value=({}, Path("/workspace/account-a/projects/mnist")),
            ),
            patch(
                "studio_api.features.workspace.router.release_inputs",
                side_effect=ValueError(
                    "Run and pass Release Readiness before submitting a Candidate."
                ),
            ),
            patch.object(WORKSPACE_RUN_MANAGER, "start_operation") as start_operation,
        ):
            with self.assertRaises(Exception) as raised:
                submit_release_candidate("local:mnist", request)

        self.assertEqual(raised.exception.status_code, 409)
        start_operation.assert_not_called()

    def test_release_submission_readiness_reports_current_workspace_state(self):
        account = SimpleNamespace(
            account_key="account-a",
            workspace_root=Path("/workspace/account-a/projects"),
        )
        request = Request({"type": "http", "method": "GET", "path": "/"})
        with (
            patch(
                "studio_api.features.workspace.router.require_account",
                return_value=account,
            ),
            patch(
                "studio_api.features.workspace.router.project_for",
                return_value=({}, Path("/workspace/account-a/projects/mnist")),
            ),
            patch(
                "studio_api.features.workspace.router.release_inputs",
                side_effect=ValueError(
                    "Workspace source changed after Release Readiness. Run it again."
                ),
            ),
        ):
            result = get_release_submission_readiness("local:mnist", request)

        self.assertFalse(result["ready"])
        self.assertIn("source changed", str(result["reason"]))

    def test_workspace_delete_removes_only_that_tasks_runtime_history(self):
        account = SimpleNamespace(
            account_key="account-a",
            workspace_root=Path("/workspace/account-a/projects"),
        )
        request = Request({"type": "http", "method": "DELETE", "path": "/"})
        result_payload = {
            "deleted": True,
            "localProjectId": "local:mnist",
            "name": "mnist",
        }
        with (
            patch(
                "studio_api.features.workspace.router.require_account",
                return_value=account,
            ),
            patch.object(WORKSPACE_RUN_MANAGER, "active_for", return_value=None),
            patch(
                "studio_api.features.workspace.router.delete_project",
                return_value=result_payload,
            ),
            patch.object(
                WORKSPACE_RUN_MANAGER, "remove_for", return_value=3
            ) as remove_for,
            patch("studio_api.features.workspace.router.close_project_terminals"),
        ):
            result = asyncio.run(
                remove_workspace(
                    "local:mnist",
                    DeleteWorkspaceRequest(name="mnist"),
                    request,
                )
            )

        self.assertEqual(result, result_payload)
        remove_for.assert_called_once_with("local:mnist", "account-a")

    def test_local_train_uses_the_task_scoped_data_binding_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "accounts/account-a/projects"
            project_root = workspace / "mnist-task"
            project_root.mkdir(parents=True)
            account = SimpleNamespace(
                account_key="account-a",
                workspace_root=workspace,
                display_workspace_root=Path(
                    "/host/fedops-workspace/accounts/account-a/projects"
                ),
                relative_workspace=Path("accounts/account-a/projects"),
            )
            request = Request({"type": "http", "method": "POST", "path": "/"})
            completed = {"runId": "run-local-train", "status": "queued"}
            with (
                patch(
                    "studio_api.features.workspace.router.require_account",
                    return_value=account,
                ),
                patch(
                    "studio_api.features.workspace.router.project_for",
                    return_value=({}, project_root),
                ),
                patch(
                    "studio_api.features.workspace.router.task_action_run",
                    return_value=completed,
                ) as run,
            ):
                result = asyncio.run(
                    start_workspace_action(
                        WorkspaceActionRequest(action="local-train"),
                        request,
                        "local:mnist-task",
                    )
                )

            expected = (
                root / "accounts/account-a/.local-data/federated-tasks/"
                "mnist-task/dataset"
            ).resolve()
            self.assertEqual(result, completed)
            self.assertTrue(expected.is_dir())
            self.assertEqual(run.call_args.args[-1], str(expected))

    def test_workspace_creation_uses_the_authenticated_web_baseline_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            AUTH_SESSIONS["session"] = {
                "mode": "fedops",
                "user_id": "mongo-user-a",
                "account_key": "account-aaaaaaaaaaaaaaaa",
                "username": "a@example.com",
                "fedops_auth": {"accessToken": "token"},
            }
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/workspaces",
                    "headers": [(b"cookie", b"fedops_studio_session=session")],
                }
            )
            completed = {
                "runId": "run-1",
                "kind": "create",
                "status": "succeeded",
                "resultLocalProjectId": "local:fedops-web-task",
            }
            release = {"release": {"name": "fedops-silo-baseline"}}
            with (
                patch("studio_api.account.WORKSPACE_DIR", str(root / "workspace")),
                patch(
                    "studio_api.account.WORKSPACE_DISPLAY_DIR", str(root / "workspace")
                ),
                patch(
                    "studio_api.features.workspace.router.BASELINE_CACHE_DIR",
                    str(root / "cache"),
                ),
                patch(
                    "studio_api.features.workspace.router.fedops_request",
                    return_value=release,
                ) as web_request,
                patch(
                    "studio_api.features.workspace.router.create_workspace_from_release",
                    return_value=completed,
                ) as installer,
            ):
                result = asyncio.run(
                    create_workspace(CreateWorkspaceRequest(name="Web Task"), request)
                )

            self.assertEqual(result, completed)
            web_request.assert_called_once()
            self.assertEqual(web_request.call_args.args[1], "baselines/default")
            self.assertEqual(
                web_request.call_args.kwargs["query"],
                {"distribution": "bundled", "version": None},
            )
            self.assertEqual(installer.call_args.args[2], root / "cache")
            self.assertIs(installer.call_args.args[3], release)

    def test_web_draft_creation_uses_web_identity_without_asking_for_a_second_name(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            AUTH_SESSIONS["session"] = {
                "mode": "fedops",
                "user_id": "mongo-user-a",
                "account_key": "account-aaaaaaaaaaaaaaaa",
                "username": "a@example.com",
                "fedops_auth": {"accessToken": "token"},
            }
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/workspaces",
                    "headers": [(b"cookie", b"fedops_studio_session=session")],
                }
            )
            task = {
                "taskId": "507f1f77bcf86cd799439011",
                "displayName": "MNIST Digit Classifier",
                "runtimeKey": "ccl-mnist-digits-439011",
                "ownerHandle": "ccl",
                "slug": "mnist-digits",
                "registryStatus": "draft",
                "runtimeContract": {
                    "name": "federated-task-v3",
                    "schemaVersion": 3,
                },
                "baselineTemplate": {"version": "0.8.0"},
            }
            release = {"release": {"name": "federated-task-baseline"}}
            completed = {
                "runId": "run-web-draft",
                "kind": "create",
                "status": "succeeded",
                "resultLocalProjectId": "local:mnist-digits",
            }
            project_root = root / "workspace" / "mnist-digits"
            with (
                patch("studio_api.account.WORKSPACE_DIR", str(root / "workspace")),
                patch(
                    "studio_api.account.WORKSPACE_DISPLAY_DIR", str(root / "workspace")
                ),
                patch(
                    "studio_api.features.workspace.router.BASELINE_CACHE_DIR",
                    str(root / "cache"),
                ),
                patch(
                    "studio_api.features.workspace.router.discover_projects",
                    return_value=[],
                ),
                patch(
                    "studio_api.features.workspace.router.fedops_request",
                    side_effect=[task, release],
                ) as web_request,
                patch(
                    "studio_api.features.workspace.router.create_workspace_from_release",
                    return_value=completed,
                ) as installer,
                patch(
                    "studio_api.features.workspace.router.find_project",
                    return_value=({}, project_root),
                ),
                patch(
                    "studio_api.features.workspace.router.write_task_binding"
                ) as bind,
            ):
                result = asyncio.run(
                    create_workspace(
                        CreateWorkspaceRequest(sourceTaskId=task["taskId"]),
                        request,
                    )
                )

            self.assertEqual(result, completed)
            self.assertEqual(web_request.call_count, 2)
            self.assertEqual(installer.call_args.args[1], "mnist-digits")
            bind.assert_called_once_with(project_root, task)

    def test_web_draft_creation_reopens_an_existing_bound_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_id = "507f1f77bcf86cd799439011"
            AUTH_SESSIONS["session"] = {
                "mode": "fedops",
                "user_id": "mongo-user-a",
                "account_key": "account-aaaaaaaaaaaaaaaa",
                "username": "a@example.com",
                "fedops_auth": {"accessToken": "token"},
            }
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/workspaces",
                    "headers": [(b"cookie", b"fedops_studio_session=session")],
                }
            )
            task = {
                "taskId": task_id,
                "displayName": "MNIST Digit Classifier",
                "runtimeContract": {"name": "federated-task-v3", "schemaVersion": 3},
            }
            existing = {
                "localProjectId": "local:mnist-digits",
                "name": "mnist-digits",
                "path": "./mnist-digits",
                "taskBinding": {"taskId": task_id},
            }
            with (
                patch("studio_api.account.WORKSPACE_DIR", str(root / "workspace")),
                patch(
                    "studio_api.account.WORKSPACE_DISPLAY_DIR", str(root / "workspace")
                ),
                patch(
                    "studio_api.features.workspace.router.fedops_request",
                    return_value=task,
                ),
                patch(
                    "studio_api.features.workspace.router.discover_projects",
                    return_value=[existing],
                ),
                patch(
                    "studio_api.features.workspace.router.create_workspace_from_release"
                ) as installer,
            ):
                result = asyncio.run(
                    create_workspace(
                        CreateWorkspaceRequest(sourceTaskId=task_id), request
                    )
                )

            self.assertEqual(result["resultLocalProjectId"], "local:mnist-digits")
            self.assertIn("Opened existing Workspace", result["output"])
            installer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
