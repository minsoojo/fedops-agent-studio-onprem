import unittest

from studio_api.server import (
    DEFAULT_STUDIO_PORT,
    agent_serving_ports,
    listening_ports,
    server_options,
)


class ServerOptionsTest(unittest.TestCase):
    def test_local_defaults_use_the_agent_port(self):
        self.assertEqual(
            server_options({}),
            {"host": "127.0.0.1", "port": DEFAULT_STUDIO_PORT},
        )

    def test_container_can_override_the_bind_host(self):
        self.assertEqual(
            server_options(
                {
                    "STUDIO_HOST": "0.0.0.0",
                    "STUDIO_PORT": "24368",
                }
            ),
            {
                "host": "0.0.0.0",
                "port": 24368,
            },
        )

    def test_agent_serving_ports_are_dedicated_and_configurable(self):
        ports = agent_serving_ports({"STUDIO_AGENT_PORT_MIN": "24410", "STUDIO_AGENT_PORT_MAX": "24412"})
        self.assertEqual(list(ports), [24410, 24411, 24412])
        self.assertEqual(
            listening_ports({
                "STUDIO_PORT": "24368",
                "STUDIO_AGENT_PORT_MIN": "24410",
                "STUDIO_AGENT_PORT_MAX": "24412",
            }),
            [24368, 24410, 24411, 24412],
        )


if __name__ == "__main__":
    unittest.main()
