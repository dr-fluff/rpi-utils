import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.programs import ProgramManager, _mac_process_info, _process_info
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

    def test_program_saves_optional_http_service_url(self):
        program = self.manager.add_program(
            "AdGuard",
            "/usr/bin/adguard",
            None,
            " http://192.168.0.10:3000 ",
        )

        self.assertEqual(program["url"], "http://192.168.0.10:3000")
        self.assertEqual(self.manager.list_programs()[0]["url"], "http://192.168.0.10:3000")

    def test_program_rejects_non_http_service_url(self):
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            self.manager.add_program("Unsafe", "unsafe", None, "javascript:alert(1)")

        self.assertEqual(self.manager.list_programs(), [])

    def test_import_running_process_persists_attachment_and_does_not_duplicate(self):
        process = {
            "pid": 4321,
            "command": ["/usr/bin/example-service", "--foreground"],
            "cwd": self.temporary_directory.name,
            "start_time": 987654,
        }
        with (
            patch("app.programs._process_info", return_value=process) as process_info,
            patch("app.programs._is_macos", return_value=False),
            patch("app.programs.Path.iterdir", return_value=[]),
        ):
            program = self.manager.add_running_process("Example service", 4321, "http://192.168.0.10:3000")
            self.assertTrue(program["running"])
            self.assertEqual(program["pid"], 4321)
            self.assertEqual(self.manager.list_running_processes(), [])

        with open(self.config_path) as configuration:
            saved = json.load(configuration)[0]
        self.assertEqual(saved["command"], process["command"])
        self.assertEqual(saved["attached_pid"], 4321)
        self.assertEqual(saved["attached_start_time"], 987654)
        self.assertEqual(saved["url"], "http://192.168.0.10:3000")
        process_info.assert_called_once_with(4321)

        restored_manager = ProgramManager()
        with patch("app.programs._process_info", return_value=process):
            restored_program = restored_manager.list_programs()[0]
        self.assertTrue(restored_program["running"])
        self.assertEqual(restored_program["pid"], 4321)

    def test_list_running_processes_uses_ps_on_macos(self):
        ps_output = (
            " 4321 Sat Oct 10 14:33:45 2026 /usr/bin/wg-quick up wg0\n"
            f" {os.getpid()} Sat Oct 10 14:33:45 2026 test runner\n"
        )
        with (
            patch("app.programs._is_macos", return_value=True),
            patch(
                "app.programs.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=ps_output, stderr=""),
            ) as run,
        ):
            processes = self.manager.list_running_processes()

        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["pid"], 4321)
        self.assertEqual(processes[0]["command"], ["/usr/bin/wg-quick", "up", "wg0"])
        self.assertIsNone(processes[0]["cwd"])
        self.assertEqual(run.call_args.args[0], ["ps", "-axo", "pid=", "-o", "lstart=", "-o", "command="])

    def test_mac_process_info_parses_start_time_and_command(self):
        with (
            patch("app.programs._is_macos", return_value=True),
            patch(
                "app.programs.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="Sat Oct 10 14:33:45 2026 /usr/bin/wg-quick up wg0\n",
                    stderr="",
                ),
            ),
        ):
            process = _mac_process_info(4321)

        self.assertEqual(
            process,
            {
                "pid": 4321,
                "command": ["/usr/bin/wg-quick", "up", "wg0"],
                "cwd": None,
                "start_time": "Sat Oct 10 14:33:45 2026",
            },
        )

    def test_linux_process_is_listed_when_working_directory_is_not_accessible(self):
        process_root = Path(self.temporary_directory.name) / "proc"
        process_path = process_root / "4321"
        process_path.mkdir(parents=True)
        (process_path / "cmdline").write_bytes(b"/usr/bin/AdGuardHome\0-s\0run\0")
        stat_fields = ["S"] + ["0"] * 18 + ["987654"]
        (process_path / "stat").write_text(f"4321 (AdGuardHome) {' '.join(stat_fields)}")
        (process_path / "cwd").symlink_to(self.temporary_directory.name)

        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == process_path / "cwd":
                raise PermissionError("permission denied")
            return original_resolve(path, *args, **kwargs)

        with (
            patch("app.programs._is_macos", return_value=False),
            patch("app.programs.Path.resolve", autospec=True, side_effect=resolve),
        ):
            process = _process_info(4321, process_root)

        self.assertEqual(
            process,
            {
                "pid": 4321,
                "command": ["/usr/bin/AdGuardHome", "-s", "run"],
                "cwd": None,
                "start_time": 987654,
            },
        )

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