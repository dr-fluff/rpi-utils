import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.programs import ProgramManager
from app.telegram.bot import allowed_chat_ids


class ProgramManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = os.path.join(self.temporary_directory.name, "programs.json")
        self.environment = patch.dict(os.environ, {"RPI_UTILS_PROGRAMS_FILE": self.config_path})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = ProgramManager()

    def test_add_persists_argument_list(self):
        program = self.manager.add_program("Demo worker", 'python -c "print(1); print(2)"', None)

        self.assertEqual(program["id"], "demo-worker")
        self.assertEqual(program["command"], ["python", "-c", "print(1); print(2)"])
        with open(self.config_path) as configuration:
            self.assertEqual(json.load(configuration)[0]["id"], "demo-worker")

    def test_start_and_stop_process(self):
        self.manager.add_program("Worker", f'{sys.executable} -c "import time; time.sleep(30)"', None)

        started = self.manager.start("worker")
        self.assertTrue(started["running"])
        self.assertTrue(self.manager.list_programs()[0]["running"])
        stopped = self.manager.stop("worker")
        self.assertFalse(stopped["running"])
        self.assertFalse(self.manager.list_programs()[0]["running"])

    def test_duplicate_names_are_rejected(self):
        self.manager.add_program("Worker", "python -V", None)

        with self.assertRaisesRegex(ValueError, "already exists"):
            self.manager.add_program("Worker", "python -V", None)

    def test_telegram_allowlist_accepts_group_chat_ids(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_CHAT_IDS": "12345,-10012345"}):
            self.assertEqual(allowed_chat_ids(), {12345, -10012345})


if __name__ == "__main__":
    unittest.main()