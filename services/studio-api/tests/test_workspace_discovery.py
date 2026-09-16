import tempfile
import unittest
from pathlib import Path

from studio_runtime.workspace import discover_projects, project_for_task


class WorkspaceDiscoveryTest(unittest.TestCase):
    def test_discovers_first_level_local_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fedops-mnist").mkdir()
            (root / "fedops-llm").mkdir()
            (root / "other-project").mkdir()
            (root / "fedops-mnist" / "pyproject.toml").write_text("", encoding="utf-8")

            projects = discover_projects(root)

            self.assertEqual(
                [project["name"] for project in projects],
                ["fedops-llm", "fedops-mnist", "other-project"],
            )
            self.assertTrue(projects[1]["hasPyproject"])
            self.assertFalse(projects[0]["hasPyproject"])
            self.assertFalse(projects[1]["hasFedOpsTask"])
            self.assertFalse(projects[1]["projectEnvironmentReady"])
            self.assertTrue(
                (root / "fedops-mnist" / ".fedops-studio" / "environments.json").is_file()
            )

            (root / "fedops-mnist" / ".venv" / "bin").mkdir(parents=True)
            (root / "fedops-mnist" / ".venv" / "bin" / "python").touch()
            projects = discover_projects(root)
            self.assertFalse(projects[1]["projectEnvironmentReady"])

    def test_missing_workspace_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                discover_projects(Path(directory) / "missing"),
                [],
            )

    def test_selects_existing_workspace_by_stable_task_id(self):
        projects = [
            {
                "localProjectId": "local:mnist",
                "taskBinding": {"taskId": "task-mnist"},
            },
            {
                "localProjectId": "local:ecg",
                "taskBinding": {"taskId": "task-ecg"},
            },
        ]

        self.assertEqual(
            project_for_task(projects, "task-ecg")["localProjectId"],
            "local:ecg",
        )
        self.assertIsNone(project_for_task(projects, "task-missing"))


if __name__ == "__main__":
    unittest.main()
