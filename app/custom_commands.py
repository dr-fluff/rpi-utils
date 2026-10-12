import json
import os
import re
import shlex
import subprocess
from pathlib import Path

COMMAND_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SCRIPT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.(?:sh|bash|py)$")
MAX_COMMAND_TIMEOUT = 300
MAX_SCRIPT_BYTES = 65536
DEFAULT_COMMAND_ICON = "bolt"
ICON_CATALOG_PATH = Path(__file__).parent / "static" / "vendor" / "fa-solid-icons.json"
COMMAND_ICONS = {icon[0] for icon in json.loads(ICON_CATALOG_PATH.read_text())}


def settings_path() -> Path:
    default = Path.home() / ".config" / "rpi-utils" / "settings.json"
    path = Path(os.environ.get("RPI_UTILS_SETTINGS_FILE", default)).expanduser().resolve()
    checkout = Path(__file__).resolve().parent.parent
    try:
        path.relative_to(checkout)
    except ValueError:
        return path
    raise ValueError("Settings file must be outside the Git checkout")


class UserSettingsStore:
    def __init__(self) -> None:
        self.path = settings_path()
        self.legacy_commands_path = Path(
            os.environ.get("RPI_UTILS_COMMANDS_FILE", self.path.with_name("commands.json"))
        ).expanduser()

    def _default(self) -> dict:
        return {
            "scripts_directory": str(self.path.parent / "scripts"),
            "default_timeout_seconds": 60,
            "custom_commands": [],
            "other_settings": {},
        }

    def _load(self) -> dict:
        if not self.path.exists():
            settings = self._default()
            if self.legacy_commands_path.exists():
                try:
                    legacy_commands = json.loads(self.legacy_commands_path.read_text())
                except (OSError, json.JSONDecodeError) as error:
                    raise RuntimeError(f"Could not read legacy custom commands: {error}") from error
                if not isinstance(legacy_commands, list):
                    raise RuntimeError("Legacy custom command configuration must be a JSON list")
                settings["custom_commands"] = legacy_commands
            return settings
        try:
            settings = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read settings: {error}") from error
        if not isinstance(settings, dict):
            raise RuntimeError("Settings file must contain a JSON object")
        defaults = self._default()
        defaults.update(settings)
        if not isinstance(defaults["custom_commands"], list):
            raise RuntimeError("The custom_commands setting must be a JSON list")
        return defaults

    def _save(self, settings: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def get(self) -> dict:
        settings = self._load()
        scripts_directory = self._validated_scripts_directory(settings["scripts_directory"])
        scripts_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            persisted = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read settings: {error}") from error
        required_settings = {
            "scripts_directory",
            "default_timeout_seconds",
            "custom_commands",
            "other_settings",
        }
        if (
            not required_settings.issubset(persisted)
            or settings["scripts_directory"] != str(scripts_directory)
        ):
            settings["scripts_directory"] = str(scripts_directory)
            self._save(settings)
        return {**settings, "scripts_directory": str(scripts_directory)}

    def update(self, scripts_directory: str, default_timeout_seconds: int) -> dict:
        if not 1 <= default_timeout_seconds <= MAX_COMMAND_TIMEOUT:
            raise ValueError(f"Default timeout must be between 1 and {MAX_COMMAND_TIMEOUT} seconds")
        settings = self._load()
        resolved_directory = self._validated_scripts_directory(scripts_directory)
        resolved_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings["scripts_directory"] = str(resolved_directory)
        settings["default_timeout_seconds"] = default_timeout_seconds
        self._save(settings)
        return self.get()

    def scripts_directory(self) -> Path:
        return Path(self.get()["scripts_directory"])

    def list_scripts(self) -> list[dict]:
        scripts_directory = self.scripts_directory()
        scripts = []
        for path in sorted(scripts_directory.iterdir()):
            if path.is_file() and not path.is_symlink() and SCRIPT_NAME_PATTERN.fullmatch(path.name):
                scripts.append({"name": path.name})
        return scripts

    def read_script(self, name: str) -> str:
        path = self._script_path(name)
        try:
            content = path.read_text()
        except FileNotFoundError as error:
            raise KeyError("Script not found") from error
        except OSError as error:
            raise RuntimeError(f"Could not read script: {error}") from error
        if len(content.encode()) > MAX_SCRIPT_BYTES:
            raise ValueError(f"Scripts must be {MAX_SCRIPT_BYTES} bytes or smaller")
        return content

    def write_script(self, name: str, content: str) -> dict:
        path = self._script_path(name)
        if len(content.encode()) > MAX_SCRIPT_BYTES:
            raise ValueError(f"Scripts must be {MAX_SCRIPT_BYTES} bytes or smaller")
        if not content.startswith("#!"):
            raise ValueError("Scripts must start with a shebang (#!) so they can be run directly")
        if path.is_symlink():
            raise ValueError("Script path must not be a symbolic link")
        self.scripts_directory()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content)
        temporary.chmod(0o700)
        temporary.replace(path)
        return {"name": name}

    def delete_script(self, name: str) -> None:
        path = self._script_path(name)
        references = [
            command["name"]
            for command in self._load()["custom_commands"]
            if command.get("script_name") == name
        ]
        if references:
            names = ", ".join(f"/{command_name}" for command_name in references)
            raise ValueError(f"Script is used by {names}; edit or remove those commands first")
        try:
            path.unlink()
        except FileNotFoundError as error:
            raise KeyError("Script not found") from error
        except OSError as error:
            raise RuntimeError(f"Could not delete script: {error}") from error

    @classmethod
    def _validated_scripts_directory(cls, directory: str) -> Path:
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise ValueError("Scripts directory must be an absolute path")
        resolved = path.resolve()
        checkout = Path(__file__).resolve().parent.parent
        try:
            resolved.relative_to(checkout)
        except ValueError:
            return resolved
        raise ValueError("Scripts directory must be outside the Git checkout")

    def _script_path(self, name: str) -> Path:
        if not SCRIPT_NAME_PATTERN.fullmatch(name):
            raise ValueError("Script name must be a .sh, .bash, or .py filename")
        path = self.scripts_directory() / name
        if path.is_symlink():
            raise ValueError("Script path must not be a symbolic link")
        return path


class CustomCommandStore:
    def __init__(self, settings: UserSettingsStore | None = None) -> None:
        self.settings = settings or UserSettingsStore()

    def list(self) -> list[dict]:
        return [self._with_runtime_command(command) for command in self.settings.get()["custom_commands"]]

    def get(self, name: str) -> dict | None:
        return next((command for command in self.list() if command["name"] == name), None)

    def _with_runtime_command(self, command: dict) -> dict:
        icon = command.get("icon", DEFAULT_COMMAND_ICON)
        if icon not in COMMAND_ICONS:
            icon = DEFAULT_COMMAND_ICON
        command = {**command, "icon": icon}
        if not command.get("script_name"):
            return command
        try:
            arguments = shlex.split(command.get("arguments", ""))
        except ValueError as error:
            raise RuntimeError(f"Invalid saved arguments for /{command['name']}: {error}") from error
        script_path = self.settings._script_path(command["script_name"])
        return {**command, "command": [str(script_path), *arguments]}

    def add(
        self,
        name: str,
        description: str,
        command: str,
        script_name: str | None,
        arguments: str,
        telegram_enabled: bool,
        requires_confirmation: bool,
        timeout_seconds: int,
        reserved_names: set[str],
        icon: str = DEFAULT_COMMAND_ICON,
    ) -> dict:
        settings = self.settings.get()
        commands = settings["custom_commands"]
        record = self._validated_record(
            name,
            description,
            command,
            script_name,
            arguments,
            telegram_enabled,
            requires_confirmation,
            timeout_seconds,
            reserved_names | {item["name"] for item in commands},
            icon,
        )
        commands.append(record)
        self.settings._save(settings)
        return record

    def update(
        self,
        name: str,
        new_name: str,
        description: str,
        command: str,
        script_name: str | None,
        arguments: str,
        telegram_enabled: bool,
        requires_confirmation: bool,
        timeout_seconds: int,
        reserved_names: set[str],
        icon: str = DEFAULT_COMMAND_ICON,
    ) -> dict:
        settings = self.settings.get()
        commands = settings["custom_commands"]
        existing = next((item for item in commands if item["name"] == name), None)
        if existing is None:
            raise KeyError("Custom command not found")
        other_names = {item["name"] for item in commands if item is not existing}
        record = self._validated_record(
            new_name,
            description,
            command,
            script_name,
            arguments,
            telegram_enabled,
            requires_confirmation,
            timeout_seconds,
            reserved_names | other_names,
            icon,
        )
        existing.update(record)
        self.settings._save(settings)
        return record

    def remove(self, name: str) -> None:
        settings = self.settings.get()
        commands = settings["custom_commands"]
        remaining = [command for command in commands if command["name"] != name]
        if len(remaining) == len(commands):
            raise KeyError("Custom command not found")
        settings["custom_commands"] = remaining
        self.settings._save(settings)

    def _validated_script(self, script_name: str) -> Path:
        path = self.settings._script_path(script_name)
        if not path.is_file():
            raise ValueError(f"Script {script_name} does not exist in the scripts directory")
        if not path.stat().st_mode & 0o100:
            raise ValueError(f"Script {script_name} is not executable; run chmod +x on it")
        return path

    def _validated_record(
        self,
        name: str,
        description: str,
        command: str,
        script_name: str | None,
        arguments: str,
        telegram_enabled: bool,
        requires_confirmation: bool,
        timeout_seconds: int,
        reserved_names: set[str],
        icon: str = DEFAULT_COMMAND_ICON,
    ) -> dict:
        normalized_name = name.strip().lower()
        if not COMMAND_NAME_PATTERN.fullmatch(normalized_name):
            raise ValueError(
                "Command name must start with a letter and contain only lowercase letters, "
                "numbers, or underscores (max 32 characters)"
            )
        if normalized_name in reserved_names:
            raise ValueError(f"The command /{normalized_name} is already in use")
        normalized_description = description.strip()
        if not normalized_description:
            raise ValueError("Description cannot be empty")
        if len(normalized_description) > 120:
            raise ValueError("Description must be 120 characters or fewer")
        if icon not in COMMAND_ICONS:
            raise ValueError("Choose a supported command icon")
        if script_name:
            script_path = self._validated_script(script_name)
            try:
                command_args = [str(script_path), *shlex.split(arguments)]
            except ValueError as error:
                raise ValueError(f"Invalid script arguments: {error}") from error
        else:
            try:
                command_args = shlex.split(command)
            except ValueError as error:
                raise ValueError(f"Invalid command: {error}") from error
            arguments = ""
        if not command_args:
            raise ValueError("Command to run cannot be empty")
        if len(command) > 1000 or len(arguments) > 1000:
            raise ValueError("Command and arguments must each be 1000 characters or fewer")
        if not 1 <= timeout_seconds <= MAX_COMMAND_TIMEOUT:
            raise ValueError(f"Timeout must be between 1 and {MAX_COMMAND_TIMEOUT} seconds")
        return {
            "name": normalized_name,
            "description": normalized_description,
            "command": command_args if not script_name else [],
            "script_name": script_name or None,
            "arguments": arguments,
            "telegram_enabled": telegram_enabled,
            "requires_confirmation": requires_confirmation,
            "timeout_seconds": timeout_seconds,
            "icon": icon,
        }


def run_custom_command(command: dict) -> dict:
    try:
        result = subprocess.run(
            command["command"],
            capture_output=True,
            text=True,
            timeout=command["timeout_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Command timed out after {command['timeout_seconds']} seconds"
        ) from error
    except OSError as error:
        raise RuntimeError(f"Could not run command: {error}") from error

    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        detail = output[-4000:] if output else "command failed without output"
        raise RuntimeError(f"Command exited with status {result.returncode}: {detail}")
    message = output[-4000:] if output else f"/{command['name']} completed successfully."
    return {"message": message}
