import asyncio
import unittest
from unittest.mock import patch

from studio_api.features.system.router import capabilities, hardware_information, health


class SystemContractTest(unittest.TestCase):
    def test_health_has_stable_service_identity(self):
        payload = asyncio.run(health())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "fedops-agent-studio-api")
        self.assertTrue(payload["version"])

    def test_capabilities_distinguish_fedops_data_from_local_execution(self):
        payload = capabilities()
        self.assertTrue(payload["workspaceFiles"])
        self.assertTrue(payload["projectInstall"])
        self.assertTrue(payload["pythonEnvironments"])
        self.assertTrue(payload["accountTaskList"])
        self.assertTrue(payload["stableTaskBinding"])
        self.assertTrue(payload["publicTaskRegistry"])
        self.assertTrue(payload["globalModelRegistry"])
        self.assertTrue(payload["taskHub"])
        self.assertTrue(payload["taskActivity"])
        self.assertTrue(payload["participationRequest"])
        self.assertTrue(payload["baselineImport"])
        self.assertTrue(payload["federatedParticipation"])
        self.assertFalse(payload["agentBuilder"])
        self.assertFalse(payload["agentRuntime"])

    def test_hardware_prefers_host_bridge_and_falls_back_to_runtime(self):
        host = {"source": "host"}
        runtime = {"source": "runtime"}
        with (
            patch("studio_api.features.system.router.FOLDER_OPENER_URL", "http://host:5602"),
            patch("studio_api.features.system.router.FOLDER_OPENER_TOKEN_FILE", "/token"),
            patch(
                "studio_api.features.system.router.read_host_hardware_information",
                return_value=host,
            ) as bridge,
        ):
            self.assertEqual(hardware_information(), host)
            bridge.assert_called_once()

        with (
            patch("studio_api.features.system.router.FOLDER_OPENER_URL", "http://host:5602"),
            patch("studio_api.features.system.router.FOLDER_OPENER_TOKEN_FILE", "/token"),
            patch(
                "studio_api.features.system.router.read_host_hardware_information",
                side_effect=RuntimeError("offline"),
            ),
            patch(
                "studio_api.features.system.router.collect_hardware_information",
                return_value=runtime,
            ) as fallback,
        ):
            self.assertEqual(hardware_information(), runtime)
            fallback.assert_called_once_with("runtime")


if __name__ == "__main__":
    unittest.main()
