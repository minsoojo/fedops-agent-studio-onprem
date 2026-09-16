import unittest

from fastapi.responses import JSONResponse

from studio_api.features.auth.router import router
from studio_api.session import attach_session_cookie, clear_session_cookie


class AuthContractTest(unittest.TestCase):
    def test_v1_auth_routes_are_registered(self):
        paths = {route.path for route in router.routes}
        self.assertEqual(
            paths,
            {
                "/api/v1/auth/login",
                "/api/v1/auth/session",
                "/api/v1/auth/logout",
            },
        )

    def test_local_session_cookie_is_http_only_and_same_site(self):
        response = JSONResponse({})
        attach_session_cookie(response, "session-id")
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("Secure", cookie)

        logout_response = JSONResponse({})
        clear_session_cookie(logout_response)
        self.assertIn("SameSite=strict", logout_response.headers["set-cookie"])

if __name__ == "__main__":
    unittest.main()
