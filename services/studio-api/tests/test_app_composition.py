import asyncio
import unittest

from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio_api.config import SOCKET_CORS_ORIGINS, TRUSTED_HOSTS
from studio_api.main import app, browser_origin_is_allowed, fastapi_app, sio


class AppCompositionTest(unittest.TestCase):
    def test_product_routes_and_terminal_events_are_registered(self):
        self.assertIsNotNone(app)
        paths = fastapi_app.openapi()["paths"]
        self.assertIn("/api/v1/bootstrap", paths)
        self.assertIn("/api/v1/workspaces/terminal/session", paths)
        self.assertIn("/api/v1/environments", paths)
        self.assertIn("/api/v1/environments/requirements", paths)
        self.assertIn("/api/v1/environments/{environment_id}/select", paths)
        self.assertIn("/api/v1/environments/{environment_id}/sync", paths)
        self.assertIn("/api/v1/federated-learning/participations", paths)
        self.assertIn("/api/v1/federated-learning/participations/{local_project_id}/preflight", paths)
        self.assertIn("/api/v1/federated-learning/participations/{local_project_id}/start", paths)
        self.assertIn("/api/v1/federated-learning/participations/{local_project_id}/stop", paths)
        self.assertNotIn("/ide", paths)
        events = sio.handlers.get("/", {})
        self.assertIn("terminal_list", events)
        self.assertIn("terminal_create", events)
        self.assertIn("terminal_context", events)
        self.assertIn("terminal_restart", events)
        self.assertIn("terminal_close", events)
        self.assertIn("terminal_input", events)
        self.assertIn("terminal_resize", events)
        self.assertIn("terminal_clear", events)
        self.assertNotIn("run_createfl_command", events)

    def test_browser_and_socket_connections_accept_all_origins(self):
        self.assertEqual(TRUSTED_HOSTS, ["*"])
        self.assertEqual(SOCKET_CORS_ORIGINS, "*")
        self.assertTrue(browser_origin_is_allowed("GET", "https://example.com"))
        self.assertTrue(browser_origin_is_allowed("POST", None))
        self.assertTrue(browser_origin_is_allowed("POST", "http://localhost:24368"))
        self.assertTrue(browser_origin_is_allowed("DELETE", "http://127.0.0.1:24368"))
        self.assertTrue(browser_origin_is_allowed("POST", "http://arbitrary-host.example:24368"))
        self.assertTrue(browser_origin_is_allowed("POST", "https://example.com"))

    def test_port_forwarding_host_and_origin_are_not_rejected(self):
        messages = []

        async def accepted_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        middleware = TrustedHostMiddleware(accepted_app, allowed_hosts=TRUSTED_HOSTS)
        asyncio.run(
            middleware(
                {
                    "type": "http",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/",
                    "raw_path": b"/",
                    "query_string": b"",
                    "headers": [(b"host", b"arbitrary-host.example:24368")],
                    "server": ("127.0.0.1", 24368),
                    "client": ("127.0.0.1", 50000),
                    "root_path": "",
                },
                receive,
                send,
            )
        )

        self.assertEqual(messages[0]["status"], 204)


if __name__ == "__main__":
    unittest.main()
