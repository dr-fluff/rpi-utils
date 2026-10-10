import os
import tempfile
import unittest
from unittest.mock import patch

from app.commands import COMMANDS, CommandError, help_message, run_command
from app.programs import ProgramManager


class CommandRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.environment = patch.dict(
            os.environ,
            {"RPI_UTILS_PROGRAMS_FILE": f"{self.temporary_directory.name}/programs.json"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = ProgramManager()

    async def test_help_lists_every_registered_telegram_command(self):
        message = help_message()

        for command in COMMANDS:
            self.assertIn(f"/{command['name']}", message)

    async def test_program_command_runs_through_shared_dispatcher(self):
        self.manager.add_program("backup", "backup-tool", None)
        with patch.object(
            self.manager,
            "start",
            return_value={"id": "backup", "running": True, "pid": 4321},
        ):
            result = await run_command("start", self.manager, ["backup"])

        self.assertEqual(result["message"], "backup: running")
        self.assertEqual(result["pid"], 4321)

    async def test_program_command_requires_program_id(self):
        with self.assertRaisesRegex(CommandError, "Usage: /start <program-id>"):
            await run_command("start", self.manager)

    async def test_ip_command_returns_both_addresses(self):
        with (
            patch("app.commands.get_global_ip", return_value="203.0.113.5"),
            patch("app.commands.get_local_ip", return_value="192.168.0.10"),
        ):
            result = await run_command("ip", self.manager)

        self.assertEqual(result["global_ip"], "203.0.113.5")
        self.assertEqual(result["local_ip"], "192.168.0.10")
        self.assertIn("Local IP: 192.168.0.10", result["message"])


if __name__ == "__main__":
    unittest.main()
