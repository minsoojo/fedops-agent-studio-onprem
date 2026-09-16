import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from studio_runtime.federated_task import (
    _release_editable,
    _release_role,
    build_release_archive,
    create_workspace_from_release,
    read_task_binding,
    reconcile_existing_published_release,
    source_fingerprint,
    validate_task_contract,
    write_task_binding,
)
from studio_runtime.workspace import discover_projects


PYPROJECT = """[project]
name = "fedops-silo-baseline"
version = "0.2.0"
requires-python = ">=3.10,<3.13"

[tool.fedops.task]
schema-version = 1
task-type = "silo"
runtime-module = "fedops_silo_baseline.task_runtime"
model-file = "fedops_silo_baseline/model.py"
data-preparation-file = "fedops_silo_baseline/data_preparation.py"
config-file = "fedops_silo_baseline/conf/config.toml"
"""


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FederatedTaskTest(unittest.TestCase):
    def test_admin_management_binding_is_not_recorded_as_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            binding = write_task_binding(Path(directory), {
                "taskId": "64b000000000000000000010",
                "runtimeKey": "another-users-task",
                "displayName": "Another User's Task",
                "releaseId": "release-1",
                "permissions": {
                    "isOwner": False,
                    "isAdmin": True,
                    "canManage": True,
                },
            })

            self.assertEqual(binding["workspaceRole"], "admin")

    def test_source_fingerprint_prunes_managed_runtime_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.py").write_text("MODEL = 1\n", encoding="utf-8")
            managed = root / ".venv" / "lib" / "python" / "site-packages"
            managed.mkdir(parents=True)
            (managed / "large-runtime-file.bin").write_bytes(b"x" * 1024)
            first = source_fingerprint(root)
            (managed / "large-runtime-file.bin").write_bytes(b"y" * 2048)
            self.assertEqual(source_fingerprint(root), first)

    def test_baseline_0100_paths_preserve_owner_edit_boundaries(self):
        self.assertEqual(
            _release_role("federated_task/local_training/model.py"),
            "model_code",
        )
        self.assertEqual(
            _release_role("federated_task/tool_ai/manifest.json"),
            "tool_manifest",
        )
        self.assertTrue(_release_editable("federated_task/local_training/training.py"))
        self.assertTrue(_release_editable("federated_task/tool_ai/tool.py"))
        self.assertTrue(_release_editable("requirements.txt"))
        self.assertFalse(_release_editable("pyproject.toml"))
        self.assertFalse(
            _release_editable("federated_task/federated_learning/client_main.py")
        )
        self.assertFalse(_release_editable("federated_task/runtime/model_release.py"))

    def make_release(self) -> tuple[dict[str, object], dict[str, bytes]]:
        artifacts = {
            "pyproject.toml": PYPROJECT.encode(),
            "fedops_silo_baseline/model.py": b"MODEL = 'baseline'\n",
            "fedops_silo_baseline/data_preparation.py": b"def prepare(): return None\n",
            "fedops_silo_baseline/conf/config.toml": b"[training]\nrounds = 1\n",
        }
        manifest = {
            "schema_version": 1,
            "baseline": {
                "name": "fedops-silo-baseline",
                "release_version": "0.2.0",
                "template_revision": 1,
            },
            "files": [
                {
                    "path": path,
                    "role": "task_definition",
                    "content_type": "text/plain",
                    "editable": True,
                    "size": len(content),
                    "sha256": digest(content),
                }
                for path, content in artifacts.items()
            ],
        }
        manifest_content = json.dumps(manifest).encode()
        artifacts["baseline-manifest.json"] = manifest_content
        session = {
            "release": {
                "name": "fedops-silo-baseline",
                "version": "0.2.0",
                "revision": 1,
                "schemaVersion": 1,
                "manifestSha256": digest(manifest_content),
                "fileCount": len(manifest["files"]),
                "totalSize": sum(file["size"] for file in manifest["files"]),
            },
            "manifest": {
                "path": "baseline-manifest.json",
                "role": "task_config",
                "editable": False,
                "contentType": "application/json; charset=utf-8",
                "size": len(manifest_content),
                "sha256": digest(manifest_content),
                "url": "https://artifacts.test/baseline-manifest.json",
            },
            "files": [
                {
                    **file,
                    "contentType": file["content_type"],
                    "url": f"https://artifacts.test/{file['path']}",
                }
                for file in manifest["files"]
            ],
        }
        return session, artifacts

    @staticmethod
    def downloader(artifacts: dict[str, bytes], calls: list[str]):
        def download(url: str, destination: Path, expected_size: int) -> None:
            path = url.removeprefix("https://artifacts.test/")
            content = artifacts[path]
            assert len(content) == expected_size
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            calls.append(path)

        return download

    def test_creates_a_task_from_verified_fedops_release_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            cache = root / "cache"
            session, artifacts = self.make_release()
            calls: list[str] = []
            download = self.downloader(artifacts, calls)

            run = create_workspace_from_release(
                workspace,
                "ECG Classifier",
                cache,
                session,
                download,
            )
            second = create_workspace_from_release(
                workspace,
                "ECG Validation",
                cache,
                session,
                download,
            )

            target = workspace / "ecg-classifier"
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["resultLocalProjectId"], "local:ecg-classifier")
            self.assertIn("without cloning a Git repository", run["output"])
            self.assertEqual(second["status"], "succeeded")
            self.assertTrue((target / "pyproject.toml").is_file())
            self.assertFalse((target / "baseline-manifest.json").exists())
            self.assertTrue((target / ".fedops-studio/baseline.json").is_file())
            self.assertTrue(discover_projects(workspace)[0]["hasFedOpsTask"])
            self.assertEqual(len(calls), len(artifacts))

    def test_rejects_an_artifact_that_does_not_match_its_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, artifacts = self.make_release()
            original = artifacts["fedops_silo_baseline/model.py"]
            artifacts["fedops_silo_baseline/model.py"] = b"x" * len(original)
            with self.assertRaisesRegex(ValueError, "failed verification"):
                create_workspace_from_release(
                    root / "workspace",
                    "Unsafe Task",
                    root / "cache",
                    session,
                    self.downloader(artifacts, []),
                )

    def test_rejects_a_manifest_path_outside_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, artifacts = self.make_release()
            session["files"][0]["path"] = "../outside.py"
            with self.assertRaisesRegex(ValueError, "unsafe"):
                create_workspace_from_release(
                    root / "workspace",
                    "Unsafe Path",
                    root / "cache",
                    session,
                    self.downloader(artifacts, []),
                )

    def test_rejects_a_project_without_the_fedops_task_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "other"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "tool.fedops.task"):
                validate_task_contract(root)

    def test_does_not_upgrade_a_legacy_web_task_with_a_v2_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "fedops-v2"
[tool.fedops.task]
schema-version = 2
task-type = "silo"
runtime-module = "federated_task.main"
model-file = "federated_task/model.py"
data-preparation-file = "federated_task/data_preparation.py"
training-file = "federated_task/training.py"
config-file = "federated_task/conf/config.yaml"
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Legacy Tasks are not upgraded"):
                write_task_binding(root, {
                    "taskId": "64b000000000000000000001",
                    "runtimeKey": "legacy-task",
                    "title": "Legacy Task",
                    "runtimeContract": {
                        "name": "legacy-v1",
                        "schemaVersion": 1,
                    },
                })

    def test_reconciles_published_identity_without_overwriting_project_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "fedops-v2"
[tool.fedops.task]
schema-version = 2
task-type = "silo"
runtime-module = "federated_task.main"
model-file = "federated_task/model.py"
data-preparation-file = "federated_task/data_preparation.py"
training-file = "federated_task/training.py"
config-file = "federated_task/conf/config.yaml"
""",
                encoding="utf-8",
            )
            source = root / "federated_task/model.py"
            source.parent.mkdir(parents=True)
            source.write_text("MODEL = 'owner-source'\n", encoding="utf-8")
            write_task_binding(root, {
                "taskId": "task-mnist",
                "runtimeKey": "mnist",
                "displayName": "MNIST Draft",
                "runtimeContract": {
                    "name": "federated-task-v2",
                    "schemaVersion": 2,
                },
            })
            release = {
                "releaseId": "release-1",
                "bundleSha256": "a" * 64,
                "model": {"modelVersionId": "model-1"},
                "task": {
                    "taskId": "task-mnist",
                    "runtimeKey": "mnist",
                    "displayName": "MNIST Classifier",
                    "runtimeContract": {
                        "name": "federated-task-v2",
                        "schemaVersion": 2,
                    },
                    "permissions": {"isOwner": True},
                },
            }

            result = reconcile_existing_published_release(root, release)

            self.assertFalse(result["releaseUpdateAvailable"])
            self.assertEqual(read_task_binding(root)["registryStatus"], "published")
            self.assertEqual(read_task_binding(root)["releaseId"], "release-1")
            self.assertEqual(source.read_text(encoding="utf-8"), "MODEL = 'owner-source'\n")

            newer = {
                **release,
                "releaseId": "release-2",
                "bundleSha256": "b" * 64,
            }
            update = reconcile_existing_published_release(root, newer)
            self.assertTrue(update["releaseUpdateAvailable"])
            self.assertEqual(read_task_binding(root)["releaseId"], "release-1")

    def test_schema_v2_binding_and_release_archive_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "federated_task/conf").mkdir(parents=True)
            (root / "model_release").mkdir()
            (root / "pyproject.toml").write_text(
                """
[project]
name = "fedops-v2"
[tool.fedops.task]
schema-version = 2
task-type = "silo"
runtime-module = "federated_task.main"
model-file = "federated_task/model.py"
data-preparation-file = "federated_task/data_preparation.py"
training-file = "federated_task/training.py"
config-file = "federated_task/conf/config.yaml"
""",
                encoding="utf-8",
            )
            for relative in (
                "federated_task/main.py",
                "federated_task/conf/config.yaml",
                "federated_task/model.py",
                "federated_task/data_preparation.py",
                "federated_task/training.py",
                "README.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {relative}\n", encoding="utf-8")
            model = root / "model_release/model.safetensors"
            model.write_bytes(b"model-bytes")
            model_sha = digest(model.read_bytes())
            signature = "a" * 64
            (root / "model_release/manifest.json").write_text(json.dumps({
                "status": "ready",
                "size": model.stat().st_size,
                "sha256": model_sha,
                "format": "safetensors",
                "origin": "centrally-trained",
                "parameterSignature": {"fingerprint": signature},
            }), encoding="utf-8")
            (root / "baseline-manifest.json").write_text(json.dumps({
                "schema_version": 2,
                "baseline": {
                    "name": "federated-task-baseline",
                    "release_version": "0.4.0",
                    "template_revision": 1,
                },
            }), encoding="utf-8")
            binding = write_task_binding(root, {
                "taskId": "64b000000000000000000001",
                "runtimeKey": "v2-task",
                "title": "internal-runtime-key",
                "displayName": "V2 Task",
                "taskCategory": "classification",
                "dataModality": "timeseries",
                "primaryModel": {"workingName": "ECG Classifier"},
                "registryStatus": "draft",
                "runtimeContract": {
                    "name": "federated-task-v2",
                    "schemaVersion": 2,
                },
            })
            self.assertEqual(binding["workspaceRole"], "owner")
            fingerprint = source_fingerprint(root)
            readiness_root = root / ".fedops-studio"
            (readiness_root / "readiness-release.json").write_text(json.dumps({
                "ok": True,
                "mode": "release",
                "checkerVersion": "1.0.0",
                "sourceFingerprint": fingerprint,
                "parameterSignatureFingerprint": signature,
            }), encoding="utf-8")

            first = build_release_archive(root, "64b000000000000000000002", root / ".fedops-studio/first.zip")
            second = build_release_archive(root, "64b000000000000000000002", root / ".fedops-studio/second.zip")

            self.assertEqual(binding["taskId"], read_task_binding(root)["taskId"])
            self.assertEqual(binding["displayName"], "V2 Task")
            self.assertEqual(binding["primaryModel"]["workingName"], "ECG Classifier")
            self.assertEqual(binding["runtimeContract"]["name"], "federated-task-v2")
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(first["manifest"]["sourceFingerprint"], fingerprint)
            paths = {item["path"] for item in first["manifest"]["files"]}
            roles = {
                item["path"]: item["role"]
                for item in first["manifest"]["files"]
            }
            editable = {
                item["path"]: item["editable"]
                for item in first["manifest"]["files"]
            }
            self.assertNotIn("model_release/model.safetensors", paths)
            self.assertFalse(any(path.startswith(".fedops-studio") for path in paths))
            self.assertEqual(roles["federated_task/model.py"], "model_code")
            self.assertEqual(roles["federated_task/training.py"], "local_training")
            self.assertEqual(roles["federated_task/conf/config.yaml"], "task_config")
            self.assertTrue(editable["federated_task/model.py"])
            self.assertTrue(editable["federated_task/training.py"])
            self.assertFalse(editable["model_release/manifest.json"])


if __name__ == "__main__":
    unittest.main()
