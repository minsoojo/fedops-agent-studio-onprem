import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from studio_runtime.hardware import (
    _memory_string_bytes,
    collect_hardware_information,
    read_host_hardware_information,
)


class HardwareInformationTest(unittest.TestCase):
    def test_collects_one_normalized_cross_platform_shape(self):
        with (
            patch("studio_runtime.hardware.platform.system", return_value="Darwin"),
            patch("studio_runtime.hardware.platform.release", return_value="25.5.0"),
            patch("studio_runtime.hardware.platform.machine", return_value="arm64"),
            patch("studio_runtime.hardware._cpu_model", return_value="Apple M2"),
            patch("studio_runtime.hardware._logical_cpu_count", return_value=8),
            patch("studio_runtime.hardware._physical_memory_bytes", return_value=24 * 1024**3),
            patch(
                "studio_runtime.hardware._gpu_devices",
                return_value=[
                    {
                        "name": "Apple M2",
                        "memoryBytes": None,
                        "computeUnits": 10,
                    }
                ],
            ),
        ):
            payload = collect_hardware_information("host")

        self.assertEqual(payload["source"], "host")
        self.assertEqual(payload["platform"]["system"], "macOS")
        self.assertEqual(payload["cpu"], {"model": "Apple M2", "logicalCores": 8})
        self.assertEqual(payload["memory"]["totalBytes"], 24 * 1024**3)
        self.assertTrue(payload["gpu"]["detected"])
        self.assertEqual(payload["gpu"]["devices"][0]["computeUnits"], 10)

    def test_reads_token_protected_host_bridge_hardware(self):
        payload = {
            "source": "host",
            "platform": {"system": "Linux", "release": "6", "architecture": "x86_64"},
            "cpu": {"model": "Test CPU", "logicalCores": 4},
            "memory": {"totalBytes": 8 * 1024**3},
            "gpu": {"detected": False, "devices": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("host-token\n", encoding="utf-8")
            response = MagicMock()
            response.__enter__.return_value.status = 200
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()

            with patch("urllib.request.OpenerDirector.open", return_value=response) as opener:
                result = read_host_hardware_information("http://host:5602", token)

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "http://host:5602/hardware")
        self.assertEqual(request.headers["Authorization"], "Bearer host-token")
        self.assertEqual(result, payload)

    def test_parses_human_readable_gpu_memory(self):
        self.assertEqual(_memory_string_bytes("24 GB"), 24 * 1024**3)
        self.assertEqual(_memory_string_bytes("8192 MB"), 8192 * 1024**2)
        self.assertIsNone(_memory_string_bytes("shared"))


if __name__ == "__main__":
    unittest.main()
