import tempfile
import unittest
from pathlib import Path

from studio_runtime.terminal import TerminalRuntime


class TerminalRuntimeTest(unittest.TestCase):
    def test_uses_a_project_uv_environment_without_creating_a_root_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-task"
            environment_path = project_root / ".venv"
            (environment_path / "bin").mkdir(parents=True)
            (environment_path / "bin" / "python").touch()

            runtime = TerminalRuntime(project_root, environment_path, "Default", "ready")
            environment = runtime.shell_environment(
                {"PATH": "/usr/bin", "VIRTUAL_ENV": "/obsolete/root/.venv"}
            )

            self.assertEqual(environment["VIRTUAL_ENV"], str(environment_path.resolve()))
            self.assertEqual(
                environment["UV_PROJECT_ENVIRONMENT"],
                str(environment_path.resolve()),
            )
            self.assertTrue(environment["PATH"].startswith(f"{environment_path.resolve()}/bin:"))
            self.assertEqual(environment["PIP_REQUIRE_VIRTUALENV"], "1")
            self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
            self.assertEqual(
                environment["PS1"],
                "(uv:Default) fedops-studio:\\W$ ",
            )

    def test_plain_shell_does_not_create_or_inherit_a_virtual_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-task"
            project_root.mkdir()

            runtime = TerminalRuntime(project_root)
            environment = runtime.shell_environment(
                {"PATH": "/usr/bin", "VIRTUAL_ENV": "/obsolete/root/.venv"}
            )

            self.assertEqual(runtime.shell_name, "bash")
            self.assertNotIn("VIRTUAL_ENV", environment)
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", environment)
            self.assertEqual(environment["PATH"], "/usr/bin")
            self.assertEqual(environment["PS1"], "fedops-studio:\\W$ ")
            self.assertEqual(environment["BASH_SILENCE_DEPRECATION_WARNING"], "1")
            self.assertFalse((project_root / ".venv").exists())

    def test_missing_environment_has_a_semantic_non_container_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "fedops-task"
            project_root.mkdir()

            runtime = TerminalRuntime(
                project_root,
                environment_label="ccltestenv",
                environment_status="missing",
            )
            environment = runtime.shell_environment({"PATH": "/usr/local/bin:/usr/bin"})

            self.assertEqual(
                environment["PS1"],
                "(env:ccltestenv:sync-required) fedops-studio:\\W$ ",
            )
            self.assertNotIn("VIRTUAL_ENV", environment)
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", environment)
            self.assertNotIn("\\u", environment["PS1"])
            self.assertNotIn("\\h", environment["PS1"])
            self.assertNotIn("\\w", environment["PS1"])

    def test_rejects_an_environment_outside_the_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "fedops-task"
            outside = root / "outside"
            project_root.mkdir()
            (outside / "bin").mkdir(parents=True)
            (outside / "bin" / "python").touch()

            with self.assertRaisesRegex(ValueError, "escapes the project"):
                TerminalRuntime(project_root, outside, "Outside")


if __name__ == "__main__":
    unittest.main()
