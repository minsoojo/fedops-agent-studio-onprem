import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "fedops_agent_studio_runner.py"
SPEC = importlib.util.spec_from_file_location("fedops_agent_studio_runner", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class AgentStudioRunnerTest(unittest.TestCase):
    def test_start_checks_the_latest_image_by_default(self):
        args = runner.build_parser().parse_args([])

        self.assertFalse(args.no_pull)

    def test_prepare_image_pulls_before_using_the_new_digest(self):
        image = "gachonccl/fedops-agent-studio:latest"
        pull_result = subprocess.CompletedProcess(["docker", "pull"], 0, "", "")

        with (
            patch.object(runner, "_image_id", side_effect=["sha256:old", "sha256:new"]),
            patch.object(runner, "_run", return_value=pull_result) as run,
        ):
            previous, current = runner._prepare_image(
                "docker", image, pull=True, dry_run=False
            )

        self.assertEqual(previous, "sha256:old")
        self.assertEqual(current, "sha256:new")
        run.assert_called_once_with(["docker", "pull", image], capture=False)

    def test_stop_mode_is_available(self):
        args = runner.build_parser().parse_args(["stop"])

        self.assertEqual(args.action, "stop")

    def test_cpu_command_mounts_workspace_and_all_local_ports(self):
        command = runner.build_container_command(
            "docker",
            image="gachonccl/fedops-agent-studio:latest",
            container_name="fedops-agent-studio",
            workspace=Path("/tmp/fedops-workspace"),
            studio_port=24368,
            token_file=Path("/tmp/fedops-token"),
            nvidia=False,
        )

        self.assertIn("0.0.0.0:24368:24368", command)
        self.assertIn("0.0.0.0:24400-24499:24400-24499", command)
        self.assertIn("/tmp/fedops-workspace:/workspace", command)
        self.assertIn(
            "type=volume,source=fedops-agent-studio-uv,target=/var/cache/fedops-uv",
            command,
        )
        self.assertIn("UV_CACHE_DIR=/var/cache/fedops-uv/cache", command)
        self.assertIn("UV_PYTHON_INSTALL_DIR=/var/cache/fedops-uv/python", command)
        self.assertNotIn("--gpus", command)

    def test_local_only_bind_address_remains_available(self):
        command = runner.build_container_command(
            "docker",
            image="image",
            container_name="studio",
            workspace=Path("/tmp/workspace"),
            studio_port=24368,
            token_file=None,
            nvidia=False,
            bind_address="127.0.0.1",
        )

        self.assertIn("127.0.0.1:24368:24368", command)
        self.assertIn("127.0.0.1:24400-24499:24400-24499", command)

    def test_nvidia_command_exposes_all_gpus(self):
        command = runner.build_container_command(
            "docker",
            image="gachonccl/fedops-agent-studio:latest",
            container_name="fedops-agent-studio",
            workspace=Path("/tmp/fedops-workspace"),
            studio_port=24368,
            token_file=None,
            nvidia=True,
        )

        self.assertEqual(command[command.index("--gpus") + 1], "all")

    def test_ascii_banner_is_packaged_with_the_prototype(self):
        assets = SCRIPT.parent / "assets"
        logo = assets / "fedops_logo_ascii.txt"
        wordmark = assets / "fedops-agent-studio-ascii.txt"

        self.assertTrue(logo.read_text(encoding="utf-8").strip())
        self.assertIn("agent studio", wordmark.read_text(encoding="utf-8").casefold())


if __name__ == "__main__":
    unittest.main()
