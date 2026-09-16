import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from studio_api.features.registry.router import (
    can_open_published_release,
    join_public_task,
    leave_public_task,
    local_artifact_descriptor,
    OpenPublishedReleaseRequest,
    open_published_release,
    router,
)
from studio_api.integrations.fedops_web import (
    download_fedops_artifact,
    fedops_api_url,
    quote_segment,
)


class RegistryContractTest(unittest.TestCase):
    def test_registry_routes_cover_actual_fedops_resources(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/v1/registry/tasks", paths)
        self.assertIn("/api/v1/registry/tasks/public/{handle}/{slug}/activity", paths)
        self.assertIn("/api/v1/registry/tasks/public/{handle}/{slug}/hub", paths)
        self.assertIn("/api/v1/registry/tasks/public/{handle}/{slug}/join", paths)
        self.assertIn("/api/v1/registry/tasks/public/{handle}/{slug}/participation", paths)
        self.assertIn(
            "/api/v1/registry/tasks/public/{handle}/{slug}/files/{file_id}/preview",
            paths,
        )
        self.assertIn(
            "/api/v1/registry/tasks/runtime/{runtime_key}/models/{version_id}/download",
            paths,
        )
        self.assertIn("/api/v1/registry/tasks/{task_id}/published-release", paths)

    def test_fedops_url_adapter_encodes_query_and_path_segments(self):
        with patch(
            "studio_api.integrations.fedops_web.FEDOPS_BASE_URL",
            "https://example.test/fedops",
        ):
            url = fedops_api_url(
                "tasks/public",
                {"q": "ECG model", "page": 2, "tag": None},
            )

        self.assertEqual(
            url,
            "https://example.test/fedops/api/tasks/public?q=ECG+model&page=2",
        )
        self.assertEqual(quote_segment("owner/name"), "owner%2Fname")

    def test_artifact_adapter_streams_only_a_verified_size_to_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "artifact.bin"
            with patch(
                "studio_api.integrations.fedops_web.urllib.request.urlopen",
                return_value=_ArtifactResponse(b"fedops"),
            ):
                download_fedops_artifact(
                    "https://artifacts.example.test/signed",
                    destination,
                    6,
                )
            self.assertEqual(destination.read_bytes(), b"fedops")

    def test_artifact_adapter_rejects_plain_http_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "unsupported artifact URL"):
                download_fedops_artifact(
                    "http://artifacts.example.test/signed",
                    Path(directory) / "artifact.bin",
                    1,
                )

    def test_download_descriptor_stays_behind_the_authenticated_studio_api(self):
        request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "server": ("localhost", 24368),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
            "path": "/api/v1/registry/tasks/public/owner/task/files/file/download",
            "raw_path": b"/api/v1/registry/tasks/public/owner/task/files/file/download",
            "query_string": b"",
            "headers": [],
        })
        result = local_artifact_descriptor(
            request,
            {"url": "/fedops/api/task-releases/tasks/id/published/files/file/artifact"},
        )
        self.assertEqual(
            result["url"],
            "http://localhost:24368/api/v1/registry/tasks/public/owner/task/files/file/download?artifact=true",
        )

    def test_workspace_release_requires_owner_or_approved_participant(self):
        self.assertFalse(can_open_published_release({"task": {"permissions": {}}}))
        self.assertTrue(can_open_published_release({
            "task": {"permissions": {"isParticipant": True}},
        }))
        self.assertTrue(can_open_published_release({
            "task": {"permissions": {"isOwner": True}},
        }))


class RegistryParticipationContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_open_published_release_reuses_workspace_with_same_task_id(self):
        request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "server": ("localhost", 24368),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
            "path": "/api/v1/registry/tasks/task-mnist/published-release",
            "raw_path": b"/api/v1/registry/tasks/task-mnist/published-release",
            "query_string": b"",
            "headers": [],
        })
        release = {
            "releaseId": "release-1",
            "task": {
                "taskId": "task-mnist",
                "displayName": "MNIST Classifier",
                "permissions": {"isParticipant": True, "canOpenWorkspace": True},
            },
        }
        existing = {
            "localProjectId": "local:mnist",
            "name": "mnist",
            "path": "./mnist",
            "taskBinding": {"taskId": "task-mnist"},
        }
        with (
            patch("studio_api.features.registry.router.session_for", return_value={"username": "member"}),
            patch(
                "studio_api.features.registry.router.account_context",
                return_value=SimpleNamespace(
                    workspace_root=Path("/workspace/account"),
                    account_key="account-test",
                ),
            ),
            patch("studio_api.features.registry.router.fedops_request", return_value=release),
            patch("studio_api.features.registry.router.find_project_for_task", return_value=existing),
            patch(
                "studio_api.features.registry.router.find_project",
                return_value=(existing, Path("/workspace/account/mnist")),
            ),
            patch(
                "studio_api.features.registry.router.reconcile_existing_published_release",
                return_value={
                    "binding": existing["taskBinding"],
                    "updated": False,
                    "releaseUpdateAvailable": False,
                },
            ),
            patch("studio_api.features.registry.router.download_authenticated_fedops_artifact") as download,
            patch("studio_api.features.registry.router.install_published_release") as install,
        ):
            result = await open_published_release(
                "task-mnist",
                OpenPublishedReleaseRequest(),
                request,
            )

        self.assertEqual(result["resultLocalProjectId"], "local:mnist")
        self.assertIn("Opened existing Workspace", result["output"])
        download.assert_not_called()
        install.assert_not_called()

    async def test_join_uses_stable_task_id_instead_of_registry_title(self):
        request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "server": ("localhost", 24368),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
            "path": "/api/v1/registry/tasks/public/owner/mnist/join",
            "raw_path": b"/api/v1/registry/tasks/public/owner/mnist/join",
            "query_string": b"",
            "headers": [],
        })
        with patch(
            "studio_api.features.registry.router.session_for",
            return_value={"username": "participant@example.com"},
        ), patch(
            "studio_api.features.registry.router.fedops_request",
            side_effect=[
                {
                    "taskId": "507f1f77bcf86cd799439011",
                    "id": "owner/mnist",
                    "title": "MNIST Federated Task",
                    "primaryModel": {"displayName": "MNIST CNN"},
                    "owner": {"handle": "owner"},
                    "slug": "mnist",
                },
                {
                    "_id": "participation-1",
                    "taskId": "507f1f77bcf86cd799439011",
                    "status": "requested",
                },
            ],
        ) as fedops_request_mock:
            result = await join_public_task("owner", "mnist", request)

        self.assertEqual(result["status"], "pending-approval")
        self.assertEqual(
            fedops_request_mock.call_args_list[1].args[1],
            "tasks/id/507f1f77bcf86cd799439011/participants",
        )

    async def test_leave_uses_stable_task_id_and_delete(self):
        request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "DELETE",
            "scheme": "http",
            "server": ("localhost", 24368),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
            "path": "/api/v1/registry/tasks/public/owner/mnist/participation",
            "raw_path": b"/api/v1/registry/tasks/public/owner/mnist/participation",
            "query_string": b"",
            "headers": [],
        })
        with patch(
            "studio_api.features.registry.router.session_for",
            return_value={"username": "participant@example.com"},
        ), patch(
            "studio_api.features.registry.router.fedops_request",
            side_effect=[
                {
                    "taskId": "507f1f77bcf86cd799439011",
                    "id": "owner/mnist",
                    "title": "MNIST Federated Task",
                    "owner": {"handle": "owner"},
                    "slug": "mnist",
                },
                {
                    "participationId": "participation-1",
                    "taskId": "507f1f77bcf86cd799439011",
                    "status": "left",
                },
            ],
        ) as fedops_request_mock:
            result = await leave_public_task("owner", "mnist", request)

        self.assertEqual(result["status"], "left")
        self.assertEqual(
            fedops_request_mock.call_args_list[1].args[1],
            "tasks/id/507f1f77bcf86cd799439011/participants/me",
        )
        self.assertEqual(
            fedops_request_mock.call_args_list[1].kwargs["method"],
            "DELETE",
        )


class _ArtifactResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.read_complete = False

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def geturl() -> str:
        return "https://artifacts.example.test/signed"

    def read(self, _: int) -> bytes:
        if self.read_complete:
            return b""
        self.read_complete = True
        return self.content


if __name__ == "__main__":
    unittest.main()
