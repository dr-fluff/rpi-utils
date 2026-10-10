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

    def test_import_running_process_persists_attachment_and_does_not_duplicate(self):
        process = {
            "pid": 4321,
            "command": ["/usr/bin/example-service", "--foreground"],
            "cwd": self.temporary_directory.name,
            "start_time": 987654,
        }
        with (
            patch("app.programs._process_info", return_value=process) as process_info,
            patch("app.programs.Path.iterdir", return_value=[]),
        ):
            program = self.manager.add_running_process("Example service", 4321)
            self.assertTrue(program["running"])
            self.assertEqual(program["pid"], 4321)
            self.assertEqual(self.manager.list_running_processes(), [])

        with open(self.config_path) as configuration:
            saved = json.load(configuration)[0]
        self.assertEqual(saved["command"], process["command"])
        self.assertEqual(saved["attached_pid"], 4321)
        self.assertEqual(saved["attached_start_time"], 987654)
        process_info.assert_called_once_with(4321)

        restored_manager = ProgramManager()
        with patch("app.programs._process_info", return_value=process):
            restored_program = restored_manager.list_programs()[0]
        self.assertTrue(restored_program["running"])
        self.assertEqual(restored_program["pid"], 4321)

    def test_import_running_process_rejects_a_process_that_exited(self):
        with patch("app.programs._process_info", return_value=None):
            with self.assertRaisesRegex(ValueError, "no longer running"):
                self.manager.add_running_process("Gone", 4321)

        self.assertEqual(self.manager.list_programs(), [])

    def test_stop_does_not_signal_a_reused_process_id(self):
        process = {
            "pid": 4321,
            "command": ["/usr/bin/example-service"],
            "cwd": self.temporary_directory.name,
            "start_time": 987654,
        }
        with patch("app.programs._process_info", return_value=process):
            program = self.manager.add_running_process("Example service", 4321)
        self.assertTrue(program["running"])

        replacement_process = {**process, "start_time": 987655}
        with (
            patch("app.programs._process_info", return_value=replacement_process),
            patch("app.programs.os.kill") as kill,
        ):
            result = self.manager.stop("example-service")

        self.assertFalse(result["running"])
        kill.assert_not_called()
        self.assertFalse(self.manager._load()[0].get("attached_pid"))

    def test_telegram_allowlist_accepts_group_chat_ids(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_CHAT_IDS": "12345,-10012345"}):
            self.assertEqual(allowed_chat_ids(), {12345, -10012345})


if __name__ == "__main__":
    unittest.main()