import json
import tempfile
import unittest
from pathlib import Path

from studio_api.features.workspace.router import router
from studio_runtime.workspace import (
    build_file_tree,
    create_text_file,
    delete_project,
    delete_text_file,
    find_project,
    format_text_file,
    project_file_editable,
    read_text_file,
    resolve_project_file,
    save_text_file,
)


class WorkspaceFilesTest(unittest.TestCase):
    def test_reads_and_saves_only_inside_a_discovered_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_root = workspace / "fedops-mnist"
            project_root.mkdir()
            source = project_root / "client_app.py"
            source.write_text("value = 1\n", encoding="utf-8")

            project, resolved_root = find_project(workspace, "local:fedops-mnist")
            opened = read_text_file(resolved_root, "client_app.py")
            saved = save_text_file(resolved_root, "client_app.py", "value = 2\n")

            self.assertEqual(project["name"], "fedops-mnist")
            self.assertEqual(opened["language"], "python")
            self.assertEqual(saved["content"], "value = 2\n")
            self.assertEqual(source.read_text(encoding="utf-8"), "value = 2\n")
            with self.assertRaises(ValueError):
                resolve_project_file(resolved_root, "../outside.txt")

    def test_tree_omits_environments_and_dependency_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-project"
            project_root.mkdir()
            (project_root / "model.py").write_text("", encoding="utf-8")
            (project_root / ".venv").mkdir()
            (project_root / "node_modules").mkdir()

            tree = build_file_tree(project_root)

            self.assertEqual([child["name"] for child in tree["children"]], ["model.py"])

    def test_new_dependency_contract_protects_generated_python_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-project"
            project_root.mkdir()
            (project_root / "pyproject.toml").write_text(
                """[project]
name = "task"
version = "0.10.0"
dynamic = ["dependencies"]
[tool.hatch.metadata.hooks.requirements_txt]
files = ["requirements.txt"]
""",
                encoding="utf-8",
            )
            (project_root / "requirements.txt").write_text("numpy==1.26.4\n", encoding="utf-8")
            (project_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")

            self.assertTrue(read_text_file(project_root, "pyproject.toml")["readOnly"])
            self.assertTrue(read_text_file(project_root, "uv.lock")["readOnly"])
            self.assertFalse(read_text_file(project_root, "requirements.txt")["readOnly"])
            with self.assertRaisesRegex(ValueError, "managed by Agent Studio"):
                save_text_file(project_root, "pyproject.toml", "changed")
            with self.assertRaisesRegex(ValueError, "managed by Agent Studio"):
                delete_text_file(project_root, "uv.lock")

    def test_baseline_manifest_is_the_authoritative_owner_edit_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-project"
            (project_root / ".fedops-studio").mkdir(parents=True)
            paths = {
                "README.md": True,
                "requirements.txt": True,
                "pyproject.toml": False,
                "uv.lock": False,
                "federated_task/local_training/model.py": True,
                "federated_task/runtime/model_release.py": False,
            }
            for relative in paths:
                target = project_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("value = 1\n", encoding="utf-8")
            (project_root / ".fedops-studio/baseline.json").write_text(
                json.dumps({
                    "schema_version": 2,
                    "files": [
                        {"path": path, "editable": editable}
                        for path, editable in paths.items()
                    ],
                }),
                encoding="utf-8",
            )

            self.assertTrue(project_file_editable(project_root, "README.md"))
            self.assertTrue(project_file_editable(
                project_root, "federated_task/local_training/helper.py"
            ))
            self.assertFalse(project_file_editable(
                project_root, "federated_task/runtime/model_release.py"
            ))
            self.assertFalse(project_file_editable(
                project_root, "federated_task/runtime/helper.py"
            ))
            self.assertFalse(read_text_file(
                project_root, "federated_task/local_training/model.py"
            )["readOnly"])
            self.assertTrue(read_text_file(
                project_root, "federated_task/runtime/model_release.py"
            )["readOnly"])

            save_text_file(
                project_root,
                "federated_task/local_training/model.py",
                "value = 2\n",
            )
            create_text_file(
                project_root,
                "federated_task/local_training/helper.py",
                "value = 3\n",
            )
            with self.assertRaisesRegex(ValueError, "read-only"):
                save_text_file(
                    project_root,
                    "federated_task/runtime/model_release.py",
                    "changed\n",
                )
            with self.assertRaisesRegex(ValueError, "read-only"):
                create_text_file(
                    project_root,
                    "federated_task/runtime/helper.py",
                )
            with self.assertRaisesRegex(ValueError, "read-only"):
                delete_text_file(project_root, "pyproject.toml")

            tree = build_file_tree(project_root)
            federated_task = next(
                child for child in tree["children"] if child["name"] == "federated_task"
            )
            directories = {child["name"]: child for child in federated_task["children"]}
            self.assertFalse(directories["local_training"]["readOnly"])
            self.assertTrue(directories["runtime"]["readOnly"])
            self.assertEqual(directories["local_training"]["accessKind"], "editable")
            self.assertEqual(directories["runtime"]["accessKind"], "managed")

            model_release = project_root / "model_release"
            model_release.mkdir()
            (model_release / "model.safetensors").write_bytes(b"model")
            generated = build_file_tree(project_root)
            release_node = next(
                child for child in generated["children"] if child["name"] == "model_release"
            )
            self.assertEqual(release_node["accessKind"], "generated")
            self.assertEqual(release_node["children"][0]["accessKind"], "generated")

    def test_deletes_only_a_confirmed_first_level_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_root = workspace / "fedops-delete-me"
            project_root.mkdir()
            (project_root / "model.py").touch()

            with self.assertRaisesRegex(ValueError, "confirmation name"):
                delete_project(workspace, "local:fedops-delete-me", "wrong-name")
            self.assertTrue(project_root.is_dir())

            result = delete_project(
                workspace,
                "local:fedops-delete-me",
                "fedops-delete-me",
            )
            self.assertTrue(result["deleted"])
            self.assertFalse(project_root.exists())

    def test_creates_and_deletes_editor_files_without_touching_managed_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-project"
            source_dir = project_root / "src"
            source_dir.mkdir(parents=True)

            created = create_text_file(project_root, "src/model.py", "value=1\n")
            self.assertEqual(created["path"], "src/model.py")
            self.assertEqual((source_dir / "model.py").read_text(encoding="utf-8"), "value=1\n")
            with self.assertRaises(FileExistsError):
                create_text_file(project_root, "src/model.py")
            with self.assertRaises(ValueError):
                create_text_file(project_root, ".venv/secret.py")
            with self.assertRaises(ValueError):
                create_text_file(project_root, "src/../outside.py")

            deleted = delete_text_file(project_root, "src/model.py")
            self.assertEqual(deleted, {"deleted": True, "path": "src/model.py"})
            self.assertFalse((source_dir / "model.py").exists())

    def test_formats_json_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-project"
            project_root.mkdir()
            target = project_root / "config.json"
            target.write_text('{"alpha":1,"items":[2,3]}', encoding="utf-8")

            formatted = format_text_file(project_root, "config.json")

            self.assertEqual(
                formatted["content"],
                '{\n  "alpha": 1,\n  "items": [\n    2,\n    3\n  ]\n}\n',
            )
            self.assertEqual(target.read_text(encoding="utf-8"), formatted["content"])

    def test_versioned_workspace_file_routes_are_registered(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/v1/workspaces/files/tree", paths)
        self.assertIn("/api/v1/workspaces/files/content", paths)
        self.assertIn("/api/v1/workspaces/files/format", paths)
        self.assertIn("/api/v1/workspaces/terminal/session", paths)
        self.assertIn("/api/v1/workspaces/{local_project_id}", paths)
        self.assertIn("/api/v1/workspaces/{local_project_id}/open-folder", paths)
        self.assertIn("/api/v1/workspaces/legacy", paths)
        self.assertIn("/api/v1/workspaces/legacy/import", paths)


if __name__ == "__main__":
    unittest.main()
