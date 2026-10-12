import os
import re
import tempfile
from pathlib import Path


TELEGRAM_BOT_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
TELEGRAM_ALLOWED_CHAT_IDS_KEY = "TELEGRAM_ALLOWED_CHAT_IDS"
TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
TELEGRAM_CHAT_ID_PATTERN = re.compile(r"^-?\d+$")


def telegram_env_path() -> Path:
    default = Path.home() / ".config" / "rpi-utils" / "env"
    return Path(os.environ.get("RPI_UTILS_ENV_FILE", default)).expanduser()


class TelegramSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or telegram_env_path()

    def get(self) -> dict:
        settings = self._read()
        token = settings.get(TELEGRAM_BOT_TOKEN_KEY, os.environ.get(TELEGRAM_BOT_TOKEN_KEY, ""))
        chat_ids = settings.get(
            TELEGRAM_ALLOWED_CHAT_IDS_KEY,
            os.environ.get(TELEGRAM_ALLOWED_CHAT_IDS_KEY, ""),
        )
        normalized_ids = self._normalize_chat_ids(chat_ids)
        return {
            "bot_token_configured": bool(token),
            "allowed_chat_ids": normalized_ids,
            "enabled": bool(token and normalized_ids),
        }

    def update(
        self,
        bot_token: str,
        allowed_chat_ids: str,
        clear_bot_token: bool,
    ) -> dict:
        token = bot_token.strip()
        ids = self._normalize_chat_ids(allowed_chat_ids)
        if clear_bot_token and token:
            raise ValueError("Enter a new bot token or choose to remove the saved token, not both")
        if token and not TELEGRAM_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Bot token must use the format provided by Telegram")

        settings = self._read()
        if clear_bot_token:
            settings.pop(TELEGRAM_BOT_TOKEN_KEY, None)
        elif token:
            settings[TELEGRAM_BOT_TOKEN_KEY] = token
        elif existing_token := settings.get(
            TELEGRAM_BOT_TOKEN_KEY,
            os.environ.get(TELEGRAM_BOT_TOKEN_KEY, ""),
        ):
            settings[TELEGRAM_BOT_TOKEN_KEY] = existing_token

        if ids:
            settings[TELEGRAM_ALLOWED_CHAT_IDS_KEY] = ids
        else:
            settings.pop(TELEGRAM_ALLOWED_CHAT_IDS_KEY, None)

        self._write(settings)
        if TELEGRAM_BOT_TOKEN_KEY in settings:
            os.environ[TELEGRAM_BOT_TOKEN_KEY] = settings[TELEGRAM_BOT_TOKEN_KEY]
        else:
            os.environ.pop(TELEGRAM_BOT_TOKEN_KEY, None)
        if ids:
            os.environ[TELEGRAM_ALLOWED_CHAT_IDS_KEY] = ids
        else:
            os.environ.pop(TELEGRAM_ALLOWED_CHAT_IDS_KEY, None)
        return self.get()

    @staticmethod
    def _normalize_chat_ids(value: str) -> str:
        raw_ids = [item.strip() for item in value.replace("\n", ",").split(",")]
        ids: list[str] = []
        for item in raw_ids:
            if not item:
                continue
            if not TELEGRAM_CHAT_ID_PATTERN.fullmatch(item):
                raise ValueError("Chat IDs must be integers separated by commas or new lines")
            if item not in ids:
                ids.append(item)
        return ",".join(ids)

    def _read(self) -> dict[str, str]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise RuntimeError(f"Could not read Telegram settings: {error}") from error

        values: dict[str, str] = {}
        keys = {TELEGRAM_BOT_TOKEN_KEY, TELEGRAM_ALLOWED_CHAT_IDS_KEY}
        pattern = re.compile(r"^\s*(TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_CHAT_IDS)\s*=(.*)$")
        for line in lines:
            match = pattern.match(line)
            if match and match.group(1) in keys:
                values[match.group(1)] = self._decode_value(match.group(2))
        return values

    @staticmethod
    def _decode_value(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return value

    def _write(self, settings: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        try:
            existing_lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            existing_lines = []
        except OSError as error:
            raise RuntimeError(f"Could not read Telegram settings: {error}") from error

        setting_pattern = re.compile(
            r"^\s*(TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_CHAT_IDS)\s*="
        )
        retained_lines = [line for line in existing_lines if not setting_pattern.match(line)]
        for name in (TELEGRAM_BOT_TOKEN_KEY, TELEGRAM_ALLOWED_CHAT_IDS_KEY):
            if name in settings:
                escaped = settings[name].replace("\\", "\\\\").replace('"', '\\"')
                retained_lines.append(f'{name}="{escaped}"')

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".telegram-env-",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write("\n".join(retained_lines) + "\n")
            temporary_path.chmod(0o600)
            temporary_path.replace(self.path)
            self.path.chmod(0o600)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise RuntimeError(f"Could not save Telegram settings: {error}") from error
