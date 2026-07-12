import os
import tempfile
import unittest

import config
import task_queue


class TaskQueueTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_path = config.TASK_QUEUE_DB
        self._old_url = config.SUPABASE_URL
        self._old_key = config.SUPABASE_KEY
        config.TASK_QUEUE_DB = os.path.join(self._tmpdir.name, 'test_tasks.db')
        config.SUPABASE_URL = ''
        config.SUPABASE_KEY = ''

    def tearDown(self):
        config.TASK_QUEUE_DB = self._old_path
        config.SUPABASE_URL = self._old_url
        config.SUPABASE_KEY = self._old_key
        self._tmpdir.cleanup()

    def test_create_and_fetch_pending(self):
        task_id = task_queue.create_task(
            title='Fix FB texts',
            brief={'goal': 'write enrich to sheet'},
            project='airbnb-bot',
        )
        pending = task_queue.fetch_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['id'], task_id)
        self.assertEqual(pending[0]['brief']['goal'], 'write enrich to sheet')

    def test_claim_and_complete(self):
        task_id = task_queue.create_task('Test', {'step': 1})
        claimed = task_queue.claim_task(task_id)
        self.assertEqual(claimed['status'], 'in_progress')
        task_queue.complete_task(task_id, result={'ok': True})
        self.assertEqual(task_queue.fetch_pending(), [])


if __name__ == '__main__':
    unittest.main()
