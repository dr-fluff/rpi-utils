import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app, custom_commands


class CustomCommandApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.original_path = custom_commands.settings.path
        custom_commands.settings.path = Path(self.temporary_directory.name) / "settings.json"
        self.addCleanup(setattr, custom_commands.settings, "path", self.original_path)
        self.original_legacy_path = custom_commands.settings.legacy_commands_path
        custom_commands.settings.legacy_commands_path = Path(self.temporary_directory.name) / "commands.json"
        self.addCleanup(
            setattr,
            custom_commands.settings,
            "legacy_commands_path",
            self.original_legacy_path,
        )
        self.environment = patch.dict(
            os.environ,
            {
                "RPI_UTILS_WEB_PASSWORD": "test-dashboard-password-long",
                "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app, base_url="https://pi-console.home.arpa")
        response = self.client.post(
            "/api/auth/login",
            json={"password": "test-dashboard-password-long"},
        )
        self.assertEqual(response.status_code, 200)

    def test_custom_command_create_edit_list_and_delete(self):
        created = self.client.post(
            "/api/custom-commands",
            json={
                "name": "restart_router",
                "description": "Restart the router",
                "command": "/bin/echo router-restart",
                "script_name": None,
                "arguments": "",
                "telegram_enabled": True,
                "requires_confirmation": False,
                "timeout_seconds": 30,
                "icon": "wifi",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "restart_router")
        self.assertEqual(created.json()["icon"], "wifi")

        listed = self.client.get("/api/commands")
        router = next(item for item in listed.json()["commands"] if item["name"] == "restart_router")
        self.assertTrue(router["is_custom"])
        self.assertEqual(router["command"], "/bin/echo router-restart")
        self.assertEqual(router["icon"], "wifi")

        updated = self.client.put(
            "/api/custom-commands/restart_router",
            json={
                "name": "router_reboot",
                "description": "Reboot the router",
                "command": "/bin/echo router-reboot",
                "script_name": None,
                "arguments": "",
                "telegram_enabled": False,
                "requires_confirmation": True,
                "timeout_seconds": 45,
                "icon": "server",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "router_reboot")
        self.assertEqual(updated.json()["icon"], "server")

        deleted = self.client.delete("/api/custom-commands/router_reboot")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(custom_commands.list(), [])

    def test_custom_command_cannot_replace_a_builtin(self):
        response = self.client.post(
            "/api/custom-commands",
            json={
                "name": "restart",
                "description": "Replace built-in restart",
                "command": "/bin/true",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already in use", response.json()["detail"])

    def test_custom_command_rejects_unsupported_icon(self):
        response = self.client.post(
            "/api/custom-commands",
            json={
                "name": "unsafe_icon",
                "description": "No-op",
                "command": "/bin/true",
                "icon": "bolt extra-class",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("supported command icon", response.json()["detail"])

    def test_custom_command_accepts_icon_from_full_font_awesome_catalog(self):
        response = self.client.post(
            "/api/custom-commands",
            json={
                "name": "camera_tool",
                "description": "Camera tool",
                "command": "/bin/true",
                "icon": "camera-retro",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["icon"], "camera-retro")

    def test_custom_command_configuration_is_private(self):
        self.client.post(
            "/api/custom-commands",
            json={
                "name": "safe",
                "description": "No-op",
                "command": "/bin/true",
            },
        )

        self.assertEqual(custom_commands.settings.path.stat().st_mode & 0o777, 0o600)

    def test_settings_and_script_api_create_private_script_directory(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        script_directory = Path(response.json()["scripts_directory"])
        self.assertTrue(script_directory.is_dir())

        saved = self.client.put(
            "/api/scripts/restart-router.sh",
            json={"content": "#!/bin/sh\nprintf 'router restarted\\n'\n"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual((script_directory / "restart-router.sh").stat().st_mode & 0o777, 0o700)

        listed = self.client.get("/api/scripts")
        self.assertEqual(listed.json()["scripts"], [{"name": "restart-router.sh"}])
        loaded = self.client.get("/api/scripts/restart-router.sh")
        self.assertIn("router restarted", loaded.json()["content"])

    def test_script_directory_cannot_be_inside_checkout(self):
        response = self.client.put(
            "/api/settings",
            json={
                "scripts_directory": str(Path(__file__).resolve().parents[1] / "scripts"),
                "default_timeout_seconds": 75,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("outside the Git checkout", response.json()["detail"])

    def test_scripts_require_shebang_and_safe_filename(self):
        invalid_content = self.client.put(
            "/api/scripts/not-executable.sh",
            json={"content": "echo not-an-executable-script\n"},
        )
        self.assertEqual(invalid_content.status_code, 400)
        invalid_name = self.client.put(
            "/api/scripts/../outside.sh",
            json={"content": "#!/bin/sh\nexit 0\n"},
        )
        self.assertNotEqual(invalid_name.status_code, 200)

    def test_script_cannot_be_deleted_while_a_command_uses_it(self):
        self.client.get("/api/settings")
        self.client.put(
            "/api/scripts/restart-router.sh",
            json={"content": "#!/bin/sh\nexit 0\n"},
        )
        self.client.post(
            "/api/custom-commands",
            json={
                "name": "restart_router",
                "description": "Restart router",
                "script_name": "restart-router.sh",
                "arguments": "",
                "telegram_enabled": False,
                "requires_confirmation": False,
                "timeout_seconds": 60,
            },
        )
        response = self.client.delete("/api/scripts/restart-router.sh")

        self.assertEqual(response.status_code, 400)
        self.assertIn("is used by /restart_router", response.json()["detail"])

    def test_custom_command_can_run_a_saved_script_with_arguments(self):
        self.client.get("/api/settings")
        self.client.put(
            "/api/scripts/restart-router.sh",
            json={"content": "#!/bin/sh\nprintf 'router %s restarted\\n' \"$1\"\n"},
        )
        created = self.client.post(
            "/api/custom-commands",
            json={
                "name": "restart_router",
                "description": "Restart router",
                "script_name": "restart-router.sh",
                "arguments": "main",
                "telegram_enabled": False,
                "requires_confirmation": False,
                "timeout_seconds": 60,
            },
        )
        self.assertEqual(created.status_code, 201)
        command = next(
            item
            for item in self.client.get("/api/commands").json()["commands"]
            if item["name"] == "restart_router"
        )
        self.assertEqual(command["script_name"], "restart-router.sh")
        self.assertEqual(command["arguments"], "main")

        result = self.client.post("/api/commands/restart_router", json={"args": []})

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["message"], "router main restarted")

    def test_changing_script_directory_does_not_hide_existing_command_configuration(self):
        self.client.get("/api/settings")
        self.client.put(
            "/api/scripts/restart-router.sh",
            json={"content": "#!/bin/sh\nexit 0\n"},
        )
        self.client.post(
            "/api/custom-commands",
            json={
                "name": "restart_router",
                "description": "Restart router",
                "script_name": "restart-router.sh",
                "arguments": "",
                "telegram_enabled": False,
                "requires_confirmation": False,
                "timeout_seconds": 60,
            },
        )
        updated_settings = self.client.put(
            "/api/settings",
            json={
                "scripts_directory": str(Path(self.temporary_directory.name) / "alternate"),
                "default_timeout_seconds": 75,
            },
        )

        self.assertEqual(updated_settings.status_code, 200)
        command_listing = self.client.get("/api/commands")
        self.assertEqual(command_listing.status_code, 200)
        self.assertTrue(
            any(
                command["name"] == "restart_router"
                for command in command_listing.json()["commands"]
            )
        )

    def test_settings_file_migrates_legacy_command_list(self):
        custom_commands.settings.legacy_commands_path.write_text(
            '[{"name":"legacy","description":"Legacy command","command":["/bin/true"],'
            '"telegram_enabled":false,"requires_confirmation":false,"timeout_seconds":10}]'
        )

        response = self.client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        settings_document = json.loads(custom_commands.settings.path.read_text())
        self.assertEqual(settings_document["custom_commands"][0]["name"], "legacy")
        self.assertTrue((Path(response.json()["scripts_directory"])).is_dir())


if __name__ == "__main__":
    unittest.main()
