from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio_api.features.agent_builder.router import (
    AgentHarnessRequest,
    _validate_tool_model_artifact,
    registry_model_source,
)
from studio_api.features.agent_builder.router import router as builder_router
from studio_api.features.agents.router import router as agents_router
from studio_api.features.agents.router import serving_safe_chat_result


class AgentsContractTest(unittest.TestCase):
    def test_agent_draft_allows_empty_instructions_before_validation(self):
        harness = AgentHarnessRequest(instructions="")

        self.assertEqual(harness.instructions, "")

    def test_agent_builder_routes_cover_draft_validation_and_build(self):
        paths = {route.path for route in builder_router.routes}
        root = "/api/v1/agent-builder/drafts"
        self.assertIn(root, paths)
        self.assertIn("/api/v1/agent-builder/model-sources", paths)
        self.assertIn(
            "/api/v1/agent-builder/model-sources/{local_project_id}/versions/{model_version_id}/prepare",
            paths,
        )
        self.assertIn("/api/v1/agent-builder/registry-model-sources", paths)
        self.assertIn(f"{root}/{{agent_id}}", paths)
        self.assertIn(f"{root}/{{agent_id}}/validate", paths)
        self.assertIn(f"{root}/{{agent_id}}/test", paths)
        self.assertIn(f"{root}/{{agent_id}}/chat", paths)
        self.assertIn(f"{root}/{{agent_id}}/chat/stream", paths)
        self.assertIn(f"{root}/{{agent_id}}/llm/prepare", paths)
        self.assertIn(f"{root}/{{agent_id}}/build", paths)
        preparation_routes = [route for route in builder_router.routes if route.path == f"{root}/{{agent_id}}/llm/prepare"]
        self.assertEqual({next(iter(route.methods)) for route in preparation_routes}, {"GET", "POST"})

    def test_registry_model_source_exposes_participation_without_local_identity(self):
        task = {
            "registryId": "owner/mnist",
            "taskId": "task-mnist",
            "title": "MNIST CNN",
            "displayName": "MNIST Federated Task",
            "primaryModel": {"displayName": "MNIST CNN"},
            "modelCapability": "tool-ai",
            "ownerHandle": "owner",
            "slug": "mnist",
            "summary": "Digit classifier",
            "participationPolicy": "approval_required",
            "registryStatus": "published",
            "currentPublishedReleaseId": "release-1",
            "permissions": {
                "canRequestParticipation": True,
                "canOpenWorkspace": False,
            },
        }
        source = registry_model_source(task)

        self.assertEqual(source["accessState"], "join-required")
        self.assertEqual(source["capability"], "tool-ai")
        self.assertNotIn("localProjectId", source)

        approved = registry_model_source({
            **task,
            "membership": {"role": "participant", "status": "approved"},
            "permissions": {
                "canRequestParticipation": False,
                "canOpenWorkspace": True,
                "participationStatus": "approved",
            },
        })
        self.assertEqual(approved["accessState"], "workspace-required")
        self.assertEqual(approved["membershipRole"], "participant")

        pending = registry_model_source({
            **task,
            "permissions": {
                "canRequestParticipation": False,
                "canOpenWorkspace": False,
                "participationStatus": "requested",
            },
        })
        self.assertEqual(pending["accessState"], "approval-pending")

    def test_prepared_tool_global_model_is_verified_with_task_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            artifact = project / "model.safetensors"
            artifact.write_bytes(b"model")
            with patch(
                "studio_api.features.agent_builder.router._run_project_python"
            ) as run:
                _validate_tool_model_artifact(
                    {"capability": "tool-ai"},
                    project,
                    artifact,
                )

            run.assert_called_once()
            self.assertIn("load_released_model", run.call_args.args[1])
            self.assertEqual(run.call_args.args[2], artifact)

    def test_federated_task_llm_does_not_use_tool_model_loader(self):
        with patch(
            "studio_api.features.agent_builder.router._run_project_python"
        ) as run:
            _validate_tool_model_artifact(
                {"capability": "base-llm"},
                Path("/workspace/llm"),
                Path("/workspace/llm/model.safetensors"),
            )

        run.assert_not_called()

    def test_agents_routes_cover_local_test_and_serving_api(self):
        paths = {route.path for route in agents_router.routes}
        self.assertIn("/api/v1/agents", paths)
        self.assertIn("/api/v1/agents/{agent_id}/test", paths)
        self.assertIn("/api/v1/agents/{agent_id}/chat", paths)
        self.assertIn("/api/v1/agents/{agent_id}/chat/stream", paths)
        self.assertIn("/api/v1/agents/{agent_id}/llm/prepare", paths)
        self.assertIn("/api/v1/agents/{agent_id}/serving", paths)
        self.assertIn("/api/v1/agents/{agent_id}/serving/tools/{tool_id}", paths)
        self.assertIn("/api/v1/agents/{agent_id}/serving/data-sources", paths)
        self.assertIn("/api/v1/agents/{agent_id}/serving/data-sources/{source_id}/test", paths)
        self.assertIn("/serve/v1/agents/{agent_id}/health", paths)
        self.assertIn("/serve/v1/agents/{agent_id}/info", paths)
        self.assertIn("/serve/v1/agents/{agent_id}/chat", paths)
        self.assertIn("/serve/v1/agents/{agent_id}/tools/{tool_id}/invoke", paths)
        self.assertIn("/serve/v1/agents/{agent_id}/tools/{tool_id}/predict", paths)
        self.assertIn("/tools/{tool_id}/invoke", paths)
        self.assertIn("/tools/{tool_id}/predict", paths)
        self.assertIn("/health", paths)
        self.assertIn("/info", paths)
        self.assertIn("/chat", paths)
        preparation_routes = [route for route in agents_router.routes if route.path == "/api/v1/agents/{agent_id}/llm/prepare"]
        self.assertEqual({next(iter(route.methods)) for route in preparation_routes}, {"GET", "POST"})
        serving_routes = [route for route in agents_router.routes if route.path == "/api/v1/agents/{agent_id}/serving"]
        self.assertEqual({next(iter(route.methods)) for route in serving_routes}, {"GET", "POST", "PUT", "DELETE"})

    def test_serving_response_does_not_expose_local_sample_or_process_output(self):
        result = serving_safe_chat_result({
            "toolResult": {
                "input": {"private": "sample"},
                "result": {"label": 7},
                "environmentOutput": "/workspace/private/path",
            },
            "toolResults": [{
                "input": {"private": "sample"},
                "result": {"label": 7},
            }],
        }, {
            "type": "data-source",
            "sourceId": "data-source-1",
            "sampleIndex": 0,
            "metadata": {"privateLabel": 7},
        })

        self.assertNotIn("input", result["toolResult"])
        self.assertNotIn("environmentOutput", result["toolResult"])
        self.assertEqual(result["toolResult"]["result"], {"label": 7})
        self.assertEqual(result["toolResult"]["inputSource"]["sourceId"], "data-source-1")
        self.assertNotIn("metadata", result["toolResult"]["inputSource"])


if __name__ == "__main__":
    unittest.main()
