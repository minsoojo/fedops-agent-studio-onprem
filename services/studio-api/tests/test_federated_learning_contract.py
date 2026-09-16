import unittest

from fastapi import HTTPException

from studio_api.features.federated_learning.router import (
    DETAIL_MANIFEST_REFRESH_SECONDS,
    PARTICIPATION_MANIFEST_TIMEOUT_SECONDS,
    TERMINAL_MANIFEST_REFRESH_SECONDS,
    cached_manifest,
    hide_from_participation_home,
    local_participation_item,
    participation_event_payload,
    refresh_binding,
    router,
    unavailable_participation,
)


class FederatedLearningContractTest(unittest.TestCase):
    def test_routes_expose_real_local_client_lifecycle(self):
        paths = {route.path for route in router.routes}
        root = "/api/v1/federated-learning/participations"
        self.assertIn(root, paths)
        self.assertIn(f"{root}/{{local_project_id}}", paths)
        self.assertIn(f"{root}/{{local_project_id}}/history", paths)
        self.assertIn(f"{root}/{{local_project_id}}/history/{{session_id}}", paths)
        self.assertIn(f"{root}/{{local_project_id}}/preflight", paths)
        self.assertIn(f"{root}/{{local_project_id}}/start", paths)
        self.assertIn(f"{root}/{{local_project_id}}/stop", paths)

    def test_home_hides_unapproved_and_unpublished_projects(self):
        self.assertTrue(hide_from_participation_home(HTTPException(403, "Approved participation is required.")))
        self.assertTrue(hide_from_participation_home(HTTPException(
            502,
            "A Published Task Release is required for participation.",
        )))
        self.assertFalse(hide_from_participation_home(HTTPException(502, "FedOps Web is unavailable.")))

    def test_home_has_a_bounded_remote_manifest_wait(self):
        self.assertLessEqual(PARTICIPATION_MANIFEST_TIMEOUT_SECONDS, 5.0)
        self.assertGreaterEqual(DETAIL_MANIFEST_REFRESH_SECONDS, 15.0)
        self.assertLess(TERMINAL_MANIFEST_REFRESH_SECONDS, DETAIL_MANIFEST_REFRESH_SECONDS)

    def test_remote_timeout_keeps_local_task_visible_with_actionable_state(self):
        item = unavailable_participation(
            {"localProjectId": "local:mnist", "name": "MNIST"},
            {"taskId": "task-1", "runtimeKey": "mnist", "displayName": "MNIST Task"},
            "FedOps Web took too long.",
        )
        self.assertEqual(item["localProjectId"], "local:mnist")
        self.assertEqual(item["runtime"]["status"], "needs_attention")
        self.assertIn("too long", item["unavailableReason"])

    def test_published_local_binding_can_render_without_remote_manifest(self):
        import json
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_root = workspace / "mnist"
            metadata = project_root / ".fedops-studio"
            metadata.mkdir(parents=True)
            binding = {
                "taskId": "task-1",
                "runtimeKey": "mnist",
                "displayName": "MNIST",
                "registryStatus": "published",
                "releaseId": "release-1",
                "modelVersionId": "model-1",
            }
            (metadata / "task-binding.json").write_text(json.dumps(binding))
            account = SimpleNamespace(workspace_root=workspace, account_key="account-test")
            item = local_participation_item(account, {
                "localProjectId": "local:mnist",
                "name": "MNIST",
                "taskBinding": binding,
            })

        self.assertEqual(item["task"]["taskId"], "task-1")
        self.assertEqual(item["runtime"]["status"], "loading")

    def test_detail_poll_reuses_latest_task_manifest_and_checks_release_later(self):
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".fedops-studio" / "participation" / "manifest.json"
            cache.parent.mkdir(parents=True)
            manifest = {
                "schemaVersion": 1,
                "task": {"taskId": "task-1"},
                "release": {"releaseId": "release-1"},
            }
            cache.write_text(json.dumps(manifest))
            loaded = cached_manifest(root, {
                "taskId": "task-1",
                "releaseId": "release-1",
            })
            newer = cached_manifest(root, {
                "taskId": "task-1",
                "releaseId": "release-2",
            })

        self.assertEqual(loaded, manifest)
        self.assertEqual(newer, manifest)

    def test_refresh_does_not_claim_remote_release_was_installed(self):
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".fedops-studio"
            metadata.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='local'\nversion='1'\n")
            (metadata / "task-binding.json").write_text(json.dumps({
                "schemaVersion": 1,
                "taskId": "task-1",
                "runtimeKey": "mnist",
                "displayName": "MNIST",
                "workspaceRole": "participant",
                "registryStatus": "published",
                "releaseId": "release-old",
                "modelVersionId": "model-old",
            }))
            manifest = {
                "task": {"taskId": "task-1", "runtimeKey": "mnist", "title": "MNIST"},
                "participation": {"role": "participant"},
                "release": {
                    "releaseId": "release-new",
                    "bundleSha256": "bundle-new",
                    "sourceFingerprint": "different-source",
                },
                "globalModel": {"modelVersionId": "model-new"},
            }
            refresh_binding(root, manifest)
            binding = json.loads((metadata / "task-binding.json").read_text())
            cached = json.loads((metadata / "participation" / "manifest.json").read_text())

        self.assertEqual(binding["releaseId"], "release-old")
        self.assertEqual(binding["modelVersionId"], "model-old")
        self.assertEqual(binding["workspaceRole"], "participant")
        self.assertEqual(cached["release"]["releaseId"], "release-new")

    def test_completed_client_event_uses_only_task_release_and_run_identity(self):
        selected = participation_event_payload({
            "task": {"taskId": "task-1"},
            "release": {"releaseId": "release-1"},
            "runtime": {"runId": "run-1", "clientInstanceId": "private-device"},
        }, "completed")

        self.assertEqual(selected, (
            "task-1",
            {"event": "completed", "runId": "run-1", "releaseId": "release-1"},
        ))
        self.assertNotIn("clientInstanceId", selected[1])


if __name__ == "__main__":
    unittest.main()
