import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from studio_runtime.task_data import (
    SAMPLE_MARKER,
    build_task_data_sample,
    resolve_task_data_selection,
)


class TaskDataRuntimeTest(unittest.TestCase):
    def test_task_adapter_runs_in_selected_project_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "task"
            data = Path(temporary) / "dataset"
            project.mkdir()
            data.mkdir()
            encoded = json.dumps({"payload": {"image": [[0]]}, "metadata": {"label": 7}})
            with (
                patch(
                    "studio_runtime.task_data.uv_run_command",
                    return_value=(["uv", "run", "python"], {"UV_PROJECT_ENVIRONMENT": ".venv"}, project / ".venv"),
                ) as command,
                patch(
                    "studio_runtime.task_data.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=f"{SAMPLE_MARKER}{encoded}\n"),
                ) as execute,
            ):
                result = build_task_data_sample(
                    project,
                    "local:mnist",
                    data,
                    index=3,
                    environment_id="environment-default",
                )

            self.assertEqual(result["payload"], {"image": [[0]]})
            self.assertEqual(result["metadata"], {"label": 7})
            self.assertEqual(result["dataPath"], "")
            command.assert_called_once()
            self.assertEqual(command.call_args.args[1], "local:mnist")
            self.assertEqual(command.call_args.args[3], "environment-default")
            self.assertEqual(command.call_args.args[2][-2:], [str(data.resolve()), "3"])
            self.assertEqual(execute.call_args.kwargs["env"]["PYTHONPATH"], str(project.resolve()))

    def test_zero_is_forwarded_as_the_first_sample_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "task"
            data = Path(temporary) / "dataset"
            project.mkdir()
            data.mkdir()
            encoded = json.dumps({"payload": {"value": "first"}, "metadata": {"index": 0}})
            with (
                patch(
                    "studio_runtime.task_data.uv_run_command",
                    return_value=(["uv", "run", "python"], {}, project / ".venv"),
                ) as command,
                patch(
                    "studio_runtime.task_data.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=f"{SAMPLE_MARKER}{encoded}\n"),
                ),
            ):
                result = build_task_data_sample(project, "local:task", data, index=0)

            self.assertEqual(result["index"], 0)
            self.assertEqual(result["metadata"], {"index": 0})
            self.assertEqual(command.call_args.args[2][-1], "0")

    def test_relative_file_or_directory_can_be_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            selected = root / "client-2" / "records.csv"
            selected.parent.mkdir(parents=True)
            selected.write_text("value\n1\n", encoding="utf-8")

            resolved, relative = resolve_task_data_selection(root, "client-2/records.csv")

            self.assertEqual(resolved, selected.resolve())
            self.assertEqual(relative, "client-2/records.csv")

    def test_task_data_selection_cannot_escape_the_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "inside the Task Data folder"):
                resolve_task_data_selection(root, "../private.csv")
            with self.assertRaisesRegex(ValueError, "inside the Task Data folder"):
                resolve_task_data_selection(root, "/tmp/private.csv")

    def test_negative_sample_index_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "zero or greater"):
            build_task_data_sample(Path("."), "local:task", Path("."), index=-1)


if __name__ == "__main__":
    unittest.main()
