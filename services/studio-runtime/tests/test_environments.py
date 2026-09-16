import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from studio_runtime.environments import (
    REGISTRY_PATH,
    _ensure_environment_pip,
    _published_participant_lock,
    _requirements_refresh_args,
    create_environment,
    delete_environment,
    list_environments,
    read_task_requirements,
    select_environment,
    sync_environment_run,
    uv_run_command,
    validate_task_requirements,
    write_task_requirements,
)
from studio_runtime.jobs import RUN_MANAGER
from studio_runtime.execution import run_python_file_run


class PythonEnvironmentsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.owner_id = f"local:fedops-test-{self.root.name}"
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "fedops-test"\nversion = "0.1.0"\n'
            '[project.optional-dependencies]\nparticipate = ["fedops"]\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_bootstraps_default_and_persists_selection(self):
        initial = list_environments(self.root, self.owner_id)
        self.assertEqual(initial["items"][0]["environmentId"], "env-default")
        self.assertTrue(initial["items"][0]["selected"])
        self.assertEqual(initial["items"][0]["status"], "missing")

        created = create_environment(
            self.root,
            self.owner_id,
            name="Python 3.11 Test",
            python_version="3.11",
        )
        self.assertTrue(created["selected"])
        self.assertTrue((self.root / REGISTRY_PATH).is_file())

        selected = select_environment(self.root, self.owner_id, "env-default")
        self.assertTrue(selected["selected"])
        reloaded = list_environments(self.root, self.owner_id)
        self.assertEqual(
            next(item for item in reloaded["items"] if item["selected"])["environmentId"],
            "env-default",
        )

    def test_prepares_a_venv_local_pip_command_without_network_access(self):
        environment = self.root / ".venv"
        bin_directory = environment / "bin"
        bin_directory.mkdir(parents=True)
        python = bin_directory / "python"
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o755)

        def seed_pip(*_args, **_kwargs):
            (bin_directory / "pip").touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("studio_runtime.environments.subprocess.run", side_effect=seed_pip) as run:
            self.assertTrue(_ensure_environment_pip(environment))

        self.assertEqual(
            run.call_args.args[0],
            [str(python), "-m", "ensurepip", "--upgrade", "--default-pip"],
        )
        self.assertFalse(_ensure_environment_pip(environment))

    def test_deletes_environment_directory_and_selects_a_fallback(self):
        created = create_environment(
            self.root,
            self.owner_id,
            name="Disposable",
            python_version="3.12",
        )
        environment_path = self.root / str(created["path"])
        (environment_path / "bin").mkdir(parents=True)
        (environment_path / "bin" / "python").touch()

        remaining = delete_environment(
            self.root,
            self.owner_id,
            str(created["environmentId"]),
        )

        self.assertFalse(environment_path.exists())
        self.assertEqual(len(remaining["items"]), 1)
        self.assertTrue(remaining["items"][0]["selected"])
        self.assertEqual(remaining["items"][0]["environmentId"], "env-default")

        default_path = self.root / ".venv"
        (default_path / "bin").mkdir(parents=True)
        (default_path / "bin" / "python").touch()
        reset = delete_environment(self.root, self.owner_id, "env-default")
        self.assertFalse(default_path.exists())
        self.assertEqual(reset["items"][0]["environmentId"], "env-default")
        self.assertEqual(reset["items"][0]["status"], "missing")

    def test_requirements_file_is_the_single_owner_dependency_contract(self):
        (self.root / "pyproject.toml").write_text(
            """[build-system]
requires = ["hatchling==1.27.0", "hatch-requirements-txt==0.4.1"]
build-backend = "hatchling.build"
[project]
name = "fedops-test"
version = "0.10.0"
dynamic = ["dependencies"]
[tool.hatch.metadata.hooks.requirements_txt]
files = ["requirements.txt"]
""",
            encoding="utf-8",
        )
        (self.root / "requirements.txt").write_text(
            "# Task packages\nnumpy==1.26.4\n",
            encoding="utf-8",
        )

        initial = read_task_requirements(self.root)
        self.assertEqual(initial["mode"], "requirements")
        self.assertTrue(initial["editable"])
        self.assertTrue(initial["valid"])
        self.assertEqual(initial["dependencies"], ["numpy==1.26.4"])

        updated = write_task_requirements(
            self.root,
            "numpy==1.26.4\npandas==2.3.1\n",
        )
        self.assertEqual(
            updated["dependencies"],
            ["numpy==1.26.4", "pandas==2.3.1"],
        )
        with self.assertRaisesRegex(ValueError, "line 1"):
            validate_task_requirements("numpy>=1.26\n")
        with self.assertRaisesRegex(ValueError, "duplicates"):
            validate_task_requirements("numpy==1.26.4\nNumPy==2.0.0\n")

        (self.root / "requirements.txt").write_text("numpy>=1.26\n", encoding="utf-8")
        invalid = read_task_requirements(self.root)
        self.assertFalse(invalid["valid"])
        self.assertIn("line 1", str(invalid["error"]))

    def test_requirements_sync_refreshes_only_dynamic_project_metadata(self):
        (self.root / "pyproject.toml").write_text(
            """[build-system]
requires = ["hatchling==1.27.0", "hatch-requirements-txt==0.4.1"]
build-backend = "hatchling.build"
[project]
name = "fedops-test"
version = "0.1.0"
dynamic = ["dependencies"]
[tool.hatch.metadata.hooks.requirements_txt]
files = ["requirements.txt"]
""",
            encoding="utf-8",
        )
        (self.root / "requirements.txt").write_text(
            "packaging==25.0\n",
            encoding="utf-8",
        )
        self.assertEqual(
            _requirements_refresh_args(self.root),
            ["--refresh-package", "fedops-test"],
        )

        uv = self.root / "fake-uv"
        uv.write_text(
            """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "uv 9.9.9"
  exit 0
fi
if [ "$1" = "sync" ]; then
  mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
  touch "$UV_PROJECT_ENVIRONMENT/bin/python"
  test -f uv.lock || echo "version = 1" > uv.lock
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        uv.chmod(0o755)

        with (
            patch.dict(os.environ, {"STUDIO_UV_BIN": str(uv)}),
            patch(
                "studio_runtime.environments._verify_task_requirements",
                return_value=["packaging==25.0"],
            ),
        ):
            started = sync_environment_run(self.root, self.owner_id, "env-default")
            for _ in range(200):
                current = RUN_MANAGER.get(str(started["runId"]))
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
        self.assertEqual(current["status"], "succeeded")
        self.assertIn("--refresh-package fedops-test", current["command"])
        self.assertIn("Verified 1 direct Task dependencies", current["output"])

    def test_previous_format_dependencies_remain_read_only(self):
        previous = read_task_requirements(self.root)
        self.assertEqual(previous["mode"], "pyproject")
        self.assertFalse(previous["editable"])
        with self.assertRaisesRegex(ValueError, "previous-format"):
            write_task_requirements(self.root, "numpy==1.26.4\n")

    def test_rejects_deleting_an_environment_used_by_an_active_run(self):
        started = RUN_MANAGER.start_command(
            kind="run-file",
            local_project_id=self.owner_id,
            environment_id="env-default",
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=self.root,
        )
        for _ in range(100):
            if RUN_MANAGER.get(str(started["runId"]))["status"] == "running":
                break
            time.sleep(0.01)
        with self.assertRaisesRegex(RuntimeError, "active action"):
            delete_environment(self.root, self.owner_id, "env-default")
        RUN_MANAGER.cancel(str(started["runId"]))

    def test_uv_sync_makes_environment_ready_and_run_uses_it(self):
        uv = self.root / "fake-uv"
        uv.write_text(
            """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "uv 9.9.9"
  exit 0
fi
if [ "$1" = "sync" ]; then
  mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
  touch "$UV_PROJECT_ENVIRONMENT/bin/python"
  test -f uv.lock || echo "version = 1" > uv.lock
  echo "sync-ok"
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        uv.chmod(0o755)

        with patch.dict(os.environ, {"STUDIO_UV_BIN": str(uv)}):
            started = sync_environment_run(self.root, self.owner_id, "env-default")
            for _ in range(200):
                current = RUN_MANAGER.get(str(started["runId"]))
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            self.assertEqual(current["status"], "succeeded")
            self.assertNotIn("--locked", str(started["command"]))
            self.assertIn("--extra participate", str(started["command"]))
            self.assertIn("sync-ok", current["output"])
            synced = list_environments(self.root, self.owner_id)["items"][0]
            self.assertEqual(synced["status"], "ready")
            self.assertEqual(synced["lockStatus"], "synced")
            self.assertEqual(synced["uvVersion"], "9.9.9")

            argv, environment, item = uv_run_command(
                self.root,
                self.owner_id,
                ["python", "-m", "compileall", "-q", "."],
            )
            self.assertEqual(argv[:4], [str(uv), "run", "--locked", "--no-sync"])
            self.assertEqual(environment["UV_PROJECT_ENVIRONMENT"], str(self.root / ".venv"))
            self.assertEqual(environment["UV_LINK_MODE"], "copy")
            self.assertEqual(item["environmentId"], "env-default")

            script = self.root / "hello.py"
            script.write_text('print("hello")\n', encoding="utf-8")
            file_run = run_python_file_run(
                self.root,
                self.owner_id,
                "hello.py",
                "env-default",
            )
            for _ in range(100):
                completed = RUN_MANAGER.get(str(file_run["runId"]))
                if completed["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            self.assertEqual(completed["status"], "succeeded")
            self.assertIn("python hello.py", completed["command"])
            with self.assertRaises(ValueError):
                run_python_file_run(self.root, self.owner_id, "pyproject.toml", "env-default")
            with self.assertRaises(ValueError):
                run_python_file_run(self.root, self.owner_id, "../outside.py", "env-default")

            (self.root / "pyproject.toml").write_text(
                '[project]\nname = "fedops-test-renamed"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            outdated = list_environments(self.root, self.owner_id)["items"][0]
            self.assertEqual(outdated["status"], "outdated")
            self.assertEqual(outdated["lockStatus"], "outdated")

            registry = json.loads((self.root / REGISTRY_PATH).read_text(encoding="utf-8"))
            self.assertNotIn(str(self.root), json.dumps(registry))

    def test_published_participant_sync_keeps_release_lock_immutable(self):
        (self.root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        metadata = self.root / ".fedops-studio"
        metadata.mkdir()
        (metadata / "task-binding.json").write_text(json.dumps({
            "schemaVersion": 1,
            "taskId": "task-1",
            "runtimeKey": "mnist",
            "displayName": "MNIST",
            "workspaceRole": "participant",
            "registryStatus": "published",
        }))
        self.assertEqual(_published_participant_lock(self.root), ["--locked"])


if __name__ == "__main__":
    unittest.main()
