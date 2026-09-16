import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio_runtime.agent_builder import (
    AgentStore,
    DEFAULT_HARNESS,
    QWEN_TEST_LLM,
    list_local_model_sources,
    local_tool_reference,
    resolve_local_model_source,
    validate_agent_draft,
)
from studio_runtime.agents import (
    AgentRuntimeUnavailable,
    _validate_json_schema,
    _route_tool,
    authorize_serving,
    disable_serving,
    enable_serving,
    list_serving_data_sources,
    resolve_agent_tool_input,
    require_direct_tool_serving,
    rotate_serving_token,
    set_direct_tool_serving,
    update_serving_port,
    upsert_serving_data_source,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AgentBuilderRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.account = Path(self.temporary.name) / "account-a"
        self.root = self.account / ".local-data" / "agents"
        self.store = AgentStore(self.root)
        self.draft = self.store.create_draft("MNIST Assistant", "MNIST Tool AI test")
        self.tool = {
            "taskId": "task-mnist",
            "runtimeKey": "mnist-runtime",
            "registryId": "owner/mnist",
            "taskTitle": "MNIST",
            "ownerRole": "owner",
            "releaseId": "release-mnist-1",
            "modelVersionId": "model-mnist-1",
            "modelName": "MNIST CNN",
            "modelVersion": 1,
            "format": "safetensors",
            "sha256": "0" * 64,
        }
        self.store.update_draft(self.draft["agentId"], {
            "name": self.draft["name"],
            "description": self.draft["description"],
            "llm": dict(QWEN_TEST_LLM),
            "tools": [],
            "harness": dict(DEFAULT_HARNESS),
        })

    def tearDown(self):
        self.temporary.cleanup()

    def test_validation_and_build_commit_exact_artifact_identity(self):
        project = self.account / "projects" / "mnist"
        (project / "federated_task" / "tool_ai").mkdir(parents=True)
        (project / "model_release").mkdir()
        (project / ".fedops-studio").mkdir()
        (project / "pyproject.toml").write_text(
            "[project]\nname='mnist'\nversion='1.0.0'\n"
            "[tool.fedops.task]\nschema-version=3\ntask-type='silo'\n"
            "runtime-module='federated_task.main'\nmodel-file='federated_task/tool_ai/tool.py'\n"
            "data-preparation-file='federated_task/tool_ai/tool.py'\n"
            "config-file='federated_task/tool_ai/manifest.json'\n",
            encoding="utf-8",
        )
        (project / "federated_task" / "tool_ai" / "manifest.json").write_text(json.dumps({
            "description": "MNIST digit prediction",
            "features": ["image"],
            "output": {"description": "digit", "labels": ["0", "1"]},
        }), encoding="utf-8")
        (project / "federated_task" / "tool_ai" / "tool.py").write_text(
            "def predict(payload, model_path=None):\n    return {'label': 0}\n\n"
            "def build_tool_smoke_payload():\n    return {'image': [[0]]}\n",
            encoding="utf-8",
        )
        model = project / "model_release" / "model.safetensors"
        model.write_bytes(b"mnist-model")
        (project / "model_release" / "manifest.json").write_text(json.dumps({
            "status": "ready", "artifact": "model.safetensors", "format": "safetensors",
            "sha256": sha256(model), "version": 1,
        }), encoding="utf-8")
        (project / ".fedops-studio" / "task-binding.json").write_text(json.dumps({
            "schemaVersion": 1, "taskId": "task-mnist", "displayName": "MNIST",
            "runtimeKey": "mnist-runtime", "dataModality": "image",
            "primaryModel": {"displayName": "MNIST CNN"},
        }), encoding="utf-8")
        source = list_local_model_sources(self.account / "projects")[0]
        canonical = local_tool_reference(source)
        self.store.update_draft(self.draft["agentId"], {
            "name": self.draft["name"], "description": self.draft["description"],
            "llm": dict(QWEN_TEST_LLM), "tools": [canonical], "harness": dict(DEFAULT_HARNESS),
        })
        report = validate_agent_draft(self.store, self.draft["agentId"], [canonical])
        self.assertTrue(report["ok"])
        built = self.store.commit_build(self.draft["agentId"], [canonical], dict(QWEN_TEST_LLM))
        self.assertEqual(built["buildRevision"], 1)
        self.assertEqual(built["tools"][0]["localProjectId"], "local:mnist")
        self.assertEqual(built["tools"][0]["modelSha256"], sha256(model))
        self.assertTrue((self.root / "builds" / self.draft["agentId"] / "r1" / "agent.json").is_file())

    def test_cached_global_model_version_can_be_selected_without_replacing_initiative_model(self):
        project = self.account / "projects" / "mnist-versioned"
        (project / "federated_task" / "tool_ai").mkdir(parents=True)
        (project / "model_release").mkdir()
        metadata = project / ".fedops-studio"
        metadata.mkdir()
        (project / "pyproject.toml").write_text(
            "[project]\nname='mnist-versioned'\nversion='1.0.0'\n"
            "[tool.fedops.task]\nschema-version=3\ntask-type='silo'\n"
            "runtime-module='federated_task.main'\nmodel-file='federated_task/tool_ai/tool.py'\n"
            "data-preparation-file='federated_task/tool_ai/tool.py'\n"
            "config-file='federated_task/tool_ai/manifest.json'\n",
            encoding="utf-8",
        )
        (project / "federated_task" / "tool_ai" / "manifest.json").write_text(
            json.dumps({
                "description": "MNIST digit prediction",
                "features": ["image"],
                "output": {"description": "digit", "labels": ["0", "1"]},
            }),
            encoding="utf-8",
        )
        (project / "federated_task" / "tool_ai" / "tool.py").write_text(
            "def predict(payload, model_path=None):\n    return {'label': 0}\n\n"
            "def build_tool_smoke_payload():\n    return {'image': [[0]]}\n",
            encoding="utf-8",
        )
        initiative = project / "model_release" / "model.safetensors"
        initiative.write_bytes(b"initiative")
        (project / "model_release" / "manifest.json").write_text(json.dumps({
            "status": "ready",
            "artifact": "model.safetensors",
            "format": "safetensors",
            "sha256": sha256(initiative),
            "version": 1,
        }), encoding="utf-8")
        (metadata / "task-binding.json").write_text(json.dumps({
            "schemaVersion": 1,
            "taskId": "task-versioned",
            "runtimeKey": "mnist-versioned",
            "displayName": "MNIST Versioned",
            "modelVersionId": "initiative-id",
        }), encoding="utf-8")
        cached_root = metadata / "model-versions" / "global-v2-id"
        cached_root.mkdir(parents=True)
        cached = cached_root / "model.safetensors"
        cached.write_bytes(b"global-v2")
        (cached_root / "manifest.json").write_text(json.dumps({
            "modelVersionId": "global-v2-id",
            "version": 2,
            "role": "global",
            "label": "Global Model v2",
            "format": "safetensors",
            "artifact": cached.name,
            "sha256": sha256(cached),
        }), encoding="utf-8")

        selected = resolve_local_model_source(
            self.account / "projects",
            {"localProjectId": "local:mnist-versioned", "modelVersionId": "global-v2-id"},
        )

        self.assertEqual(selected["modelVersion"], 2)
        self.assertEqual(selected["modelSha256"], sha256(cached))
        self.assertEqual(
            selected["modelArtifactPath"],
            ".fedops-studio/model-versions/global-v2-id/model.safetensors",
        )
        self.assertEqual(initiative.read_bytes(), b"initiative")

    def test_explicit_ai_model_type_is_not_inferred_as_an_llm(self):
        project = self.account / "projects" / "text-classifier"
        (project / ".fedops-studio").mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            "[project]\nname='text-classifier'\nversion='1.0.0'\n"
            "[tool.fedops.task]\nschema-version=3\ntask-type='silo'\n"
            "runtime-module='federated_task.main'\nmodel-file='model.py'\n"
            "data-preparation-file='data.py'\nconfig-file='config.yaml'\n",
            encoding="utf-8",
        )
        (project / ".fedops-studio" / "task-binding.json").write_text(json.dumps({
            "schemaVersion": 1,
            "taskId": "task-text",
            "displayName": "Text Classifier",
            "runtimeKey": "text-classifier",
            "modelType": "AI",
            "dataType": "LLM",
        }), encoding="utf-8")

        source = list_local_model_sources(self.account / "projects")[0]
        self.assertEqual(source["capability"], "tool-ai")

    def test_serving_token_is_returned_once_and_rotation_revokes_it(self):
        document = self.store.load()
        document["builds"][self.draft["agentId"]] = [{
            **self.store.read_draft(self.draft["agentId"]),
            "status": "built",
            "buildRevision": 1,
            "builtAt": "2026-08-14T00:00:00+00:00",
            "sourceFingerprint": "a" * 64,
        }]
        self.store.save(document)
        first = enable_serving(self.store, self.draft["agentId"], 24400)
        self.assertEqual(first["serving"]["port"], 24400)
        self.assertEqual(first["serving"]["endpointUrl"], "http://localhost:24400")
        authorize_serving(self.store, self.draft["agentId"], first["token"])
        moved = update_serving_port(
            self.store,
            self.draft["agentId"],
            24401,
            Path(self.temporary.name),
        )
        self.assertEqual(moved["port"], 24401)
        authorize_serving(self.store, self.draft["agentId"], first["token"])
        second = rotate_serving_token(self.store, self.draft["agentId"])
        with self.assertRaises(PermissionError):
            authorize_serving(self.store, self.draft["agentId"], first["token"])
        authorize_serving(self.store, self.draft["agentId"], second["token"])
        stopped = disable_serving(self.store, self.draft["agentId"])
        self.assertFalse(stopped["enabled"])

    def test_serving_port_cannot_be_shared_by_two_agents(self):
        device = Path(self.temporary.name) / "device"

        def built_store(account_name: str) -> tuple[AgentStore, str]:
            store = AgentStore(
                device / "accounts" / account_name / ".local-data" / "agents"
            )
            draft = store.create_draft(f"Agent {account_name}")
            document = store.load()
            document["builds"][draft["agentId"]] = [{
                **draft,
                "status": "built",
                "buildRevision": 1,
                "builtAt": "2026-08-14T00:00:00+00:00",
                "sourceFingerprint": "a" * 64,
            }]
            store.save(document)
            return store, draft["agentId"]

        first_store, first_agent = built_store("account-a")
        second_store, second_agent = built_store("account-b")
        enable_serving(first_store, first_agent, 24408, device)

        with self.assertRaises(RuntimeError):
            enable_serving(second_store, second_agent, 24408, device)

    def test_multiple_tools_are_routed_by_manifest_or_explicit_data_source(self):
        tools = [
            {
                "localProjectId": "local:mnist",
                "modelName": "MNIST Classifier",
                "toolManifest": {"description": "handwritten digit image classification", "features": ["image"]},
            },
            {
                "localProjectId": "local:calorie",
                "modelName": "Calorie Predictor",
                "toolManifest": {"description": "exercise calorie prediction", "features": ["heart_rate"]},
            },
        ]
        selected, reason = _route_tool({"tools": tools}, "Predict calories from heart_rate", None)
        self.assertEqual(selected["localProjectId"], "local:calorie")
        self.assertEqual(reason, "automatic-manifest-match")

        selected, reason = _route_tool({"tools": tools}, "Run this local record", "local:mnist")
        self.assertEqual(selected["localProjectId"], "local:mnist")
        self.assertEqual(reason, "task-data-selection")

        selected, reason = _route_tool({"tools": tools}, "Analyze this", None)
        self.assertIsNone(selected)
        self.assertEqual(reason, "automatic-no-confident-match")

    def test_serving_data_source_is_tool_scoped_and_requires_explicit_allow(self):
        project = self.account / "projects" / "mnist"
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text("[project]\nname='mnist'\nversion='1.0.0'\n")
        tool = {
            "localProjectId": "local:mnist",
            "modelName": "MNIST CNN",
            "toolManifest": {"input": {"jsonSchema": {"type": "object"}}},
        }
        document = self.store.load()
        document["builds"][self.draft["agentId"]] = [{
            **self.store.read_draft(self.draft["agentId"]),
            "status": "built",
            "buildRevision": 1,
            "builtAt": "2026-08-19T00:00:00+00:00",
            "sourceFingerprint": "a" * 64,
            "tools": [tool],
        }]
        self.store.save(document)

        source = upsert_serving_data_source(
            self.store,
            self.draft["agentId"],
            tool_id="local:mnist",
            name="MNIST local samples",
            data_path="",
            sample_index=0,
            selection_mode="request",
            enabled_for_serving=False,
        )
        self.assertFalse(source["enabledForServing"])
        self.assertEqual(list_serving_data_sources(self.store, self.draft["agentId"])[0]["toolId"], "local:mnist")
        with self.assertRaises(AgentRuntimeUnavailable):
            resolve_agent_tool_input(
                self.store,
                self.store.latest_build(self.draft["agentId"]),
                {"type": "data-source", "sourceId": source["sourceId"]},
                require_serving_allowed=True,
            )

        source = upsert_serving_data_source(
            self.store,
            self.draft["agentId"],
            tool_id="local:mnist",
            name="MNIST local samples",
            data_path="",
            sample_index=0,
            selection_mode="request",
            enabled_for_serving=True,
        )
        with patch("studio_runtime.agents.build_task_data_sample", return_value={
            "payload": {"image": [[0]]},
            "metadata": {"record": 2},
        }):
            payload, tool_id, input_source = resolve_agent_tool_input(
                self.store,
                self.store.latest_build(self.draft["agentId"]),
                {"type": "data-source", "sourceId": source["sourceId"], "sampleIndex": 2},
                require_serving_allowed=True,
            )
        self.assertEqual(payload, {"image": [[0]]})
        self.assertEqual(tool_id, "local:mnist")
        self.assertEqual(input_source["sampleIndex"], 2)

    def test_direct_tool_serving_requires_explicit_per_tool_opt_in(self):
        tool = {
            "localProjectId": "local:mnist",
            "modelName": "MNIST CNN",
            "toolManifest": {"input": {"jsonSchema": {"type": "object"}}},
        }
        document = self.store.load()
        document["builds"][self.draft["agentId"]] = [{
            **self.store.read_draft(self.draft["agentId"]),
            "status": "built",
            "buildRevision": 1,
            "builtAt": "2026-08-31T00:00:00+00:00",
            "sourceFingerprint": "a" * 64,
            "tools": [tool],
        }]
        self.store.save(document)

        with self.assertRaises(AgentRuntimeUnavailable):
            require_direct_tool_serving(
                self.store,
                self.draft["agentId"],
                "local:mnist",
            )

        enabled = set_direct_tool_serving(
            self.store,
            self.draft["agentId"],
            "local:mnist",
            True,
        )
        self.assertEqual(enabled["directToolIds"], ["local:mnist"])
        require_direct_tool_serving(
            self.store,
            self.draft["agentId"],
            "local:mnist",
        )

        disabled = set_direct_tool_serving(
            self.store,
            self.draft["agentId"],
            "local:mnist",
            False,
        )
        self.assertEqual(disabled["directToolIds"], [])

    def test_tool_manifest_json_schema_rejects_invalid_payload(self):
        schema = {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "number"}},
            "additionalProperties": False,
        }
        _validate_json_schema({"value": 1.5}, schema)
        with self.assertRaises(AgentRuntimeUnavailable):
            _validate_json_schema({"value": "invalid"}, schema)
        with self.assertRaises(AgentRuntimeUnavailable):
            _validate_json_schema({"value": 1, "secret": 2}, schema)

if __name__ == "__main__":
    unittest.main()
