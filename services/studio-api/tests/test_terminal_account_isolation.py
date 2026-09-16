import unittest
from unittest.mock import Mock

from studio_api.features.workspace.realtime import (
    PROJECT_TERMINALS,
    TERMINALS,
    TerminalSession,
    _next_terminal_number,
    _project_items,
    _remove_terminal_session,
    close_project_terminals,
)


class TerminalAccountIsolationTest(unittest.TestCase):
    def setUp(self):
        TERMINALS.clear()
        PROJECT_TERMINALS.clear()

    def tearDown(self):
        for session in TERMINALS.values():
            session.runtime.close()
        TERMINALS.clear()
        PROJECT_TERMINALS.clear()

    def test_same_project_id_has_separate_terminal_namespaces(self):
        runtime_a = Mock(shell_name="bash")
        runtime_b = Mock(shell_name="bash")
        terminal_a = self._terminal("terminal-a", "account-a", runtime_a)
        terminal_b = self._terminal("terminal-b", "account-b", runtime_b)
        TERMINALS.update({terminal_a.terminal_id: terminal_a, terminal_b.terminal_id: terminal_b})
        PROJECT_TERMINALS[("account-a", "local:same-task")] = ["terminal-a"]
        PROJECT_TERMINALS[("account-b", "local:same-task")] = ["terminal-b"]

        self.assertEqual(
            [item["terminalId"] for item in _project_items("account-a", "local:same-task")],
            ["terminal-a"],
        )
        self.assertEqual(
            [item["terminalId"] for item in _project_items("account-b", "local:same-task")],
            ["terminal-b"],
        )

        close_project_terminals("account-a", "local:same-task")
        runtime_a.close.assert_called_once()
        runtime_b.close.assert_not_called()
        self.assertNotIn("terminal-a", TERMINALS)
        self.assertIn("terminal-b", TERMINALS)

    def test_next_terminal_number_reuses_a_closed_trailing_number(self):
        terminal_1 = self._terminal(
            "terminal-1",
            "account-a",
            Mock(shell_name="bash"),
            title="Terminal 1",
        )
        terminal_2 = self._terminal(
            "terminal-2",
            "account-a",
            Mock(shell_name="bash"),
            title="Terminal 2",
        )
        TERMINALS.update(
            {
                terminal_1.terminal_id: terminal_1,
                terminal_2.terminal_id: terminal_2,
            }
        )
        PROJECT_TERMINALS[("account-a", "local:same-task")] = [
            terminal_1.terminal_id,
            terminal_2.terminal_id,
        ]

        _remove_terminal_session(terminal_2)

        self.assertEqual(
            _next_terminal_number("account-a", "local:same-task"),
            2,
        )

    def test_next_terminal_number_does_not_reuse_a_closed_lower_number(self):
        terminal_2 = self._terminal(
            "terminal-2",
            "account-a",
            Mock(shell_name="bash"),
            title="Terminal 2",
        )
        terminal_3 = self._terminal(
            "terminal-3",
            "account-a",
            Mock(shell_name="bash"),
            title="Terminal 3",
        )
        TERMINALS.update(
            {
                terminal_2.terminal_id: terminal_2,
                terminal_3.terminal_id: terminal_3,
            }
        )
        PROJECT_TERMINALS[("account-a", "local:same-task")] = [
            terminal_2.terminal_id,
            terminal_3.terminal_id,
        ]

        self.assertEqual(
            _next_terminal_number("account-a", "local:same-task"),
            4,
        )

    @staticmethod
    def _terminal(
        terminal_id: str,
        account_key: str,
        runtime: Mock,
        *,
        title: str = "Terminal 1",
    ) -> TerminalSession:
        return TerminalSession(
            terminal_id=terminal_id,
            account_key=account_key,
            local_project_id="local:same-task",
            title=title,
            environment_id=None,
            environment_status="missing",
            environment_label=None,
            runtime=runtime,
            created_at="2026-08-04T00:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
