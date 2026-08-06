import json
import os
import tempfile
import unittest
from unittest.mock import patch

import cursor_agent


class CursorAgentRunnerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_sessions = cursor_agent.SESSIONS_FILE
        cursor_agent.SESSIONS_FILE = os.path.join(self._tmpdir.name, 'cursor_sessions.json')

    def tearDown(self):
        cursor_agent.SESSIONS_FILE = self._old_sessions
        self._tmpdir.cleanup()

    def test_session_persistence(self):
        runner = cursor_agent.CursorAgentRunner()
        runner.set_agent_id(5041767749, 'agent-abc')
        self.assertEqual(runner.get_agent_id(5041767749), 'agent-abc')
        runner.clear_session(5041767749)
        self.assertEqual(runner.get_agent_id(5041767749), '')

    @patch.object(cursor_agent.config, 'CURSOR_API_KEY', '')
    def test_disabled_without_api_key(self):
        runner = cursor_agent.CursorAgentRunner()
        self.assertFalse(runner.is_enabled())
        result = runner.run(1, 'test task')
        self.assertFalse(result.ok)
        self.assertIn('CURSOR_API_KEY', result.error)


if __name__ == '__main__':
    unittest.main()
