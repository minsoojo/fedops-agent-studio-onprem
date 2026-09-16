import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from studio_runtime.folder import (
    open_project_data_directory,
    open_workspace_directory,
    prepare_project_data_directory,
)
from studio_runtime.folder_bridge import FolderBridgeServer


class FolderOpenTest(unittest.TestCase):
    def test_prepares_task_data_outside_workspace_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_data = root / "accounts/account-a/.local-data"
            binding = prepare_project_data_directory(
                local_data,
                "mnist-task",
                local_project_id="local:mnist-task",
                display_local_data_root=Path(
                    "/host/fedops-workspace/accounts/account-a/.local-data"
                ),
            )

            expected = local_data / "federated-tasks/mnist-task/dataset"
            self.assertTrue(expected.is_dir())
            self.assertEqual(binding["containerPath"], str(expected.resolve()))
            self.assertEqual(
                binding["hostPath"],
                "/host/fedops-workspace/accounts/account-a/.local-data/"
                "federated-tasks/mnist-task/dataset",
            )
            self.assertFalse(binding["hasEntries"])
            self.assertEqual(binding["fileCount"], 0)
            self.assertEqual(binding["totalBytes"], 0)
            empty_fingerprint = binding["fingerprint"]
            (expected / "sample.csv").write_text("value\n1\n", encoding="utf-8")
            refreshed = prepare_project_data_directory(
                local_data,
                "mnist-task",
                local_project_id="local:mnist-task",
            )
            self.assertTrue(refreshed["hasEntries"])
            self.assertEqual(refreshed["fileCount"], 1)
            self.assertEqual(refreshed["totalBytes"], 8)
            self.assertNotEqual(refreshed["fingerprint"], empty_fingerprint)

    def test_opens_only_a_first_level_workspace_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "fedops-project"
            project.mkdir()
            nested = project / "nested"
            nested.mkdir()

            with patch("studio_runtime.folder.open_directory") as opener:
                result = open_workspace_directory(workspace, project)
                opener.assert_called_once_with(project.resolve())
            self.assertTrue(result["opened"])
            self.assertEqual(result["mode"], "native")

            with self.assertRaisesRegex(ValueError, "first-level"):
                open_workspace_directory(workspace, nested)

    def test_rejects_a_directory_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside"
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, "outside"):
                open_workspace_directory(workspace, outside)

    def test_bridge_receives_the_account_scoped_host_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "account-projects"
            project = workspace / "fedops-project"
            project.mkdir(parents=True)
            token = root / "token"
            token.write_text("folder-token\n", encoding="utf-8")
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.status = 200

            with patch(
                "urllib.request.OpenerDirector.open", return_value=response
            ) as request_open:
                result = open_workspace_directory(
                    workspace,
                    project,
                    bridge_url="http://host:5602",
                    token_file=token,
                    display_workspace=Path(
                        "/host/fedops-workspace/accounts/account-a/projects"
                    ),
                    bridge_relative_workspace=Path("accounts/account-a/projects"),
                )

            request = request_open.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(
                payload["relativePath"],
                "accounts/account-a/projects/fedops-project",
            )
            self.assertEqual(
                result["path"],
                "/host/fedops-workspace/accounts/account-a/projects/fedops-project",
            )

    def test_data_folder_bridge_receives_only_task_local_data_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_data = root / "accounts/account-a/.local-data"
            token = root / "token"
            token.write_text("folder-token\n", encoding="utf-8")
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.status = 200

            with patch(
                "urllib.request.OpenerDirector.open", return_value=response
            ) as request_open:
                result = open_project_data_directory(
                    local_data,
                    "mnist-task",
                    local_project_id="local:mnist-task",
                    bridge_url="http://host:5602",
                    token_file=token,
                    display_local_data_root=Path(
                        "/host/fedops-workspace/accounts/account-a/.local-data"
                    ),
                    bridge_relative_local_data_root=Path(
                        "accounts/account-a/.local-data"
                    ),
                )

            request = request_open.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(
                payload["relativePath"],
                "accounts/account-a/.local-data/federated-tasks/mnist-task/dataset",
            )
            self.assertTrue(result["opened"])
            self.assertEqual(result["mode"], "host-bridge")

    def test_slow_hardware_does_not_block_health_or_folder_opening(self):
        release = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "accounts/account-test/projects"
            project = projects / "example"
            project.mkdir(parents=True)
            token = root / "token"
            token.write_text("test-token")
            with patch(
                "studio_runtime.folder_bridge.collect_hardware_information",
                side_effect=lambda _: release.wait(10),
            ):
                server = FolderBridgeServer(("127.0.0.1", 0), root, "test-token")
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                url = f"http://127.0.0.1:{server.server_port}"
                try:
                    # A global proxy is deliberately broken. Neither health nor
                    # local /open requests should use it.
                    with (
                        patch(
                            "urllib.request.getproxies",
                            return_value={"http": "http://127.0.0.1:1"},
                        ),
                        patch("studio_runtime.folder_bridge.open_directory") as opened,
                    ):
                        result = open_workspace_directory(
                            projects,
                            project,
                            bridge_url=url,
                            token_file=token,
                            bridge_relative_workspace=Path(
                                "accounts/account-test/projects"
                            ),
                        )
                        self.assertTrue(result["opened"])
                        opened.assert_called_once_with(project.resolve())
                    request = urllib.request.Request(
                        url + "/health", headers={"Authorization": "Bearer test-token"}
                    )
                    with urllib.request.build_opener(
                        urllib.request.ProxyHandler({})
                    ).open(request, timeout=1) as response:
                        self.assertEqual(json.load(response)["status"], "ok")
                finally:
                    release.set()
                    server.shutdown()
                    server.server_close()
                    worker.join(2)

    def test_connection_failures_include_specific_reason_and_recovery(self):
        import socket

        cases = [
            (ConnectionRefusedError(), "refused"),
            (TimeoutError(), "timed out"),
            (socket.gaierror(), "resolve"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "example"
            project.mkdir()
            token = root / "token"
            token.write_text("test-token")
            for reason, expected in cases:
                with (
                    self.subTest(reason=expected),
                    patch(
                        "urllib.request.OpenerDirector.open",
                        side_effect=urllib.error.URLError(reason),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, expected) as error:
                        open_workspace_directory(
                            root,
                            project,
                            bridge_url="http://host:5602",
                            token_file=token,
                        )
                    self.assertIn("--repair-host", str(error.exception))

    def test_bridge_rejects_bad_token_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            projects = workspace / "accounts/account-test/projects"
            projects.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (projects / "escape").symlink_to(outside, target_is_directory=True)
            with patch(
                "studio_runtime.folder_bridge.collect_hardware_information",
                return_value={},
            ):
                server = FolderBridgeServer(("127.0.0.1", 0), workspace, "test-token")
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    for token, expected in [("wrong", 401), ("test-token", 400)]:
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{server.server_port}/open",
                            data=json.dumps(
                                {
                                    "relativePath": "accounts/account-test/projects/escape"
                                }
                            ).encode(),
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        with patch(
                            "studio_runtime.folder_bridge.open_directory"
                        ) as opened:
                            with self.assertRaises(urllib.error.HTTPError) as error:
                                urllib.request.build_opener(
                                    urllib.request.ProxyHandler({})
                                ).open(request)
                            self.assertEqual(error.exception.code, expected)
                            opened.assert_not_called()
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(2)


if __name__ == "__main__":
    unittest.main()
