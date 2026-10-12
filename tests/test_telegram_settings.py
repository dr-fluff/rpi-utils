import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app, telegram_settings


class TelegramSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.original_path = telegram_settings.path
        telegram_settings.path = Path(self.temporary_directory.name) / "env"
        self.addCleanup(setattr, telegram_settings, "path", self.original_path)
        self.environment = patch.dict(
            os.environ,
            {
                "RPI_UTILS_WEB_PASSWORD": "test-dashboard-password-long",
                "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_ALLOWED_CHAT_IDS": "",
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

    def test_telegram_settings_mask_token_save_private_and_preserve_other_env(self):
        telegram_settings.path.write_text("OTHER_SETTING=preserve\n")
        with patch("app.main.restart_telegram_bot", new_callable=AsyncMock) as restart_bot:
            restart_bot.return_value = True
            updated = self.client.put(
                "/api/telegram-settings",
                json={
                    "bot_token": "123456:Valid_Telegram-token",
                    "allowed_chat_ids": "12345\n-10012345,12345",
                },
            )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.json(),
            {
                "bot_token_configured": True,
                "allowed_chat_ids": "12345,-10012345",
                "enabled": True,
                "restarted": True,
            },
        )
        self.assertNotIn("123456:Valid_Telegram-token", updated.text)
        self.assertEqual(telegram_settings.path.stat().st_mode & 0o777, 0o600)
        saved_env = telegram_settings.path.read_text()
        self.assertIn("OTHER_SETTING=preserve", saved_env)
        self.assertIn('TELEGRAM_BOT_TOKEN="123456:Valid_Telegram-token"', saved_env)
        self.assertIn('TELEGRAM_ALLOWED_CHAT_IDS="12345,-10012345"', saved_env)
        restart_bot.assert_awaited_once()

    def test_telegram_settings_keep_token_when_field_is_blank_and_allow_clear(self):
        telegram_settings.path.write_text(
            'TELEGRAM_BOT_TOKEN="123456:Existing_token"\n'
            'TELEGRAM_ALLOWED_CHAT_IDS="12345"\n'
        )
        with patch("app.main.restart_telegram_bot", new_callable=AsyncMock) as restart_bot:
            restart_bot.return_value = True
            changed_ids = self.client.put(
                "/api/telegram-settings",
                json={"bot_token": "", "allowed_chat_ids": "-10012345"},
            )
            saved_after_id_update = telegram_settings.path.read_text()
            removed_token = self.client.put(
                "/api/telegram-settings",
                json={
                    "bot_token": "",
                    "allowed_chat_ids": "-10012345",
                    "clear_bot_token": True,
                },
            )

        self.assertEqual(changed_ids.status_code, 200)
        self.assertTrue(changed_ids.json()["bot_token_configured"])
        self.assertIn("123456:Existing_token", saved_after_id_update)
        self.assertEqual(removed_token.status_code, 200)
        self.assertFalse(removed_token.json()["bot_token_configured"])
        self.assertFalse(removed_token.json()["enabled"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN=", telegram_settings.path.read_text())
        self.assertNotIn("TELEGRAM_BOT_TOKEN", os.environ)
        self.assertEqual(restart_bot.await_count, 2)

    def test_telegram_settings_reject_invalid_token_or_chat_ids(self):
        initial_contents = "OTHER_SETTING=preserve\n"
        telegram_settings.path.write_text(initial_contents)
        with patch("app.main.restart_telegram_bot", new_callable=AsyncMock) as restart_bot:
            invalid_token = self.client.put(
                "/api/telegram-settings",
                json={"bot_token": "not-a-token", "allowed_chat_ids": "12345"},
            )
            invalid_ids = self.client.put(
                "/api/telegram-settings",
                json={"bot_token": "", "allowed_chat_ids": "12345,not-an-id"},
            )

        self.assertEqual(invalid_token.status_code, 400)
        self.assertEqual(invalid_ids.status_code, 400)
        self.assertEqual(telegram_settings.path.read_text(), initial_contents)
        restart_bot.assert_not_awaited()
