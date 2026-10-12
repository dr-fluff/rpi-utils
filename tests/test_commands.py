import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.commands import COMMANDS, CommandError, help_message, run_command
from app.custom_commands import CustomCommandStore
from app.programs import ProgramManager


class CommandRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.environment = patch.dict(
            os.environ,
            {
                "RPI_UTILS_PROGRAMS_FILE": f"{self.temporary_directory.name}/programs.json",
                "RPI_UTILS_SETTINGS_FILE": f"{self.temporary_directory.name}/settings.json",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = ProgramManager()
        self.custom_commands = CustomCommandStore()

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

    async def test_custom_command_runs_as_direct_argv_and_returns_output(self):
        self.custom_commands.add(
            "say_hi",
            "Print a greeting",
            f"{sys.executable} -c 'print(\"hello from script\")'",
            None,
            "",
            False,
            False,
            10,
            set(),
        )

        result = await run_command("say_hi", self.manager, custom_commands=self.custom_commands)

        self.assertEqual(result["message"], "hello from script")

    async def test_custom_command_is_available_to_telegram_only_when_enabled(self):
        self.custom_commands.add(
            "dashboard_only",
            "Dashboard-only command",
            "true",
            None,
            "",
            False,
            False,
            10,
            set(),
        )
        with self.assertRaisesRegex(CommandError, "not enabled for Telegram"):
            await run_command(
                "dashboard_only",
                self.manager,
                custom_commands=self.custom_commands,
                source="telegram",
            )

    async def test_confirmation_required_custom_command_cannot_run_from_telegram(self):
        self.custom_commands.add(
            "confirm_me",
            "Needs confirmation",
            "true",
            None,
            "",
            True,
            True,
            10,
            set(),
        )

        with self.assertRaisesRegex(CommandError, "requires confirmation"):
            await run_command(
                "confirm_me",
                self.manager,
                custom_commands=self.custom_commands,
                source="telegram",
            )

    async def test_help_lists_only_telegram_enabled_custom_commands(self):
        self.custom_commands.add("home_only", "Dashboard only", "true", None, "", False, False, 10, set())
        self.custom_commands.add("router", "Restart router", "true", None, "", True, False, 10, set())

        message = help_message(self.custom_commands)

        self.assertIn("/router", message)
        self.assertNotIn("/home_only", message)


if __name__ == "__main__":
    unittest.main()
