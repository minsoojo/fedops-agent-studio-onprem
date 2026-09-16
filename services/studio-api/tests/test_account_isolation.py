import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_api.account import account_context, account_key_for_user, fedops_profile
from studio_api.integrations.fedops_web import login_to_fedops
from studio_api.session import serialize_session


class AccountIsolationTest(unittest.TestCase):
    def test_account_key_is_stable_per_device_and_does_not_expose_user_id(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            first = account_key_for_user("mongo-user-a", workspace)
            repeated = account_key_for_user("mongo-user-a", workspace)
            second = account_key_for_user("mongo-user-b", workspace)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("account-"))
        self.assertNotIn("mongo-user-a", first)

    def test_account_context_uses_separate_project_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "workspace"
            display = Path(directory) / "host-workspace"
            with (
                patch("studio_api.account.WORKSPACE_DIR", str(base)),
                patch("studio_api.account.WORKSPACE_DISPLAY_DIR", str(display)),
            ):
                account_a = account_context(
                    {
                        "user_id": "user-a",
                        "account_key": "account-aaaaaaaaaaaaaaaa",
                        "username": "a@example.com",
                    }
                )
                account_b = account_context(
                    {
                        "user_id": "user-b",
                        "account_key": "account-bbbbbbbbbbbbbbbb",
                        "username": "b@example.com",
                    }
                )
                self.assertNotEqual(account_a.workspace_root, account_b.workspace_root)
                self.assertEqual(
                    account_a.relative_workspace.as_posix(),
                    "accounts/account-aaaaaaaaaaaaaaaa/projects",
                )
                self.assertTrue(account_a.workspace_root.is_dir())
                self.assertTrue(account_b.workspace_root.is_dir())

    def test_missing_device_key_does_not_orphan_existing_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "accounts" / "account-existing" / "projects").mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "Restore.*device-account-key"):
                account_key_for_user("mongo-user-a", workspace)

    def test_session_exposes_only_opaque_account_identity(self):
        session = serialize_session(
            {
                "mode": "fedops",
                "user_id": "mongo-user-a",
                "account_key": "account-aaaaaaaaaaaaaaaa",
                "username": "a@example.com",
                "handle": "account-a",
                "organization": "Example Lab",
                "display_name": "Account A",
            }
        )
        self.assertEqual(session["accountKey"], "account-aaaaaaaaaaaaaaaa")
        self.assertEqual(session["handle"], "account-a")
        self.assertEqual(session["organization"], "Example Lab")
        self.assertNotIn("userId", session)
        self.assertNotIn("mongo-user-a", str(session))

    def test_fedops_profile_requires_a_stable_server_identity(self):
        with self.assertRaisesRegex(RuntimeError, "stable user identity"):
            fedops_profile({"username": "a@example.com"}, "a@example.com")

    def test_fedops_login_keeps_the_server_profile_for_local_identity(self):
        response = _LoginResponse()
        with patch("studio_api.integrations.fedops_web.urllib.request.urlopen", return_value=response):
            authentication = login_to_fedops("a@example.com", "password")

        self.assertEqual(authentication["profile"]["_id"], "mongo-user-a")
        self.assertEqual(authentication["profile"]["handle"], "account-a")
        self.assertEqual(authentication["profile"]["organization"], "Example Lab")
        self.assertEqual(authentication["accessToken"], "signed-token")


class _Headers:
    @staticmethod
    def get_all(name: str) -> list[str]:
        return ["access_token=signed-token; Path=/; HttpOnly"] if name == "Set-Cookie" else []


class _LoginResponse:
    headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def read() -> bytes:
        return b'{"_id":"mongo-user-a","username":"a@example.com","handle":"account-a","organization":"Example Lab"}'


if __name__ == "__main__":
    unittest.main()
