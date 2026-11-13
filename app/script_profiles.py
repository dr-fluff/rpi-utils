import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.custom_commands import UserSettingsStore

PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_PROFILE_CONFIG_BYTES = 65536
MAX_PRIVATE_KEY_BYTES = 65536
MAX_PASSWORD_BYTES = 4096
SENSITIVE_CONFIG_KEY = re.compile(
    r"password|passphrase|private.?key|secret|token|credential|(?:^|[_-])(?:ssh[_-]|api[_-])?key(?:[_-]|$)",
    re.IGNORECASE,
)


class ScriptProfileStore:
    def __init__(self, settings: UserSettingsStore) -> None:
        self.settings = settings

    def list(self) -> list[dict[str, Any]]:
        profiles = self.settings.get()["script_profiles"]
        return [
            {
                "name": name,
                "config": config,
                "private_key_configured": self._credential_exists(self.private_key_path(name)),
                "password_configured": self._credential_exists(self.password_path(name)),
            }
            for name, config in sorted(profiles.items())
        ]

    def save_credentials(
        self,
        name: str,
        private_key: str = "",
        password: str = "",
        clear_private_key: bool = False,
        clear_password: bool = False,
    ) -> dict[str, bool]:
        self._validate_name(name)
        if name not in self.settings.get()["script_profiles"]:
            raise KeyError("Script profile not found")
        if private_key:
            self._write_private_key(name, private_key)
        elif clear_private_key:
            self._remove_file(self.private_key_path(name))
        if password:
            self._write_secret(self.password_path(name), password.encode("utf-8"), MAX_PASSWORD_BYTES)
        elif clear_password:
            self._remove_file(self.password_path(name))
        return {
            "private_key_configured": self._credential_exists(self.private_key_path(name)),
            "password_configured": self._credential_exists(self.password_path(name)),
        }

    @classmethod
    def validate_profiles(cls, profiles: dict[str, dict[str, Any]]) -> None:
        if not isinstance(profiles, dict):
            raise ValueError("Script profiles must be a JSON object")
        for name, config in profiles.items():
            cls._validate_name(name)
            cls._validate_config(config)

    def delete_credentials(self, name: str) -> None:
        self._validate_name(name)
        profile_directory = self._secret_root() / name
        if profile_directory.is_symlink():
            profile_directory.unlink()
        elif profile_directory.exists():
            shutil.rmtree(profile_directory)

    def remove_config(self, name: str) -> None:
        self._remove_file(self.profile_config_path(name))

    def execution_environment(self, name: str | None) -> dict[str, str]:
        if name is None:
            return {"RPI_UTILS_SCRIPT_CONFIG_FILE": str(self.settings.script_config_path())}
        self._validate_name(name)
        profiles = self.settings.get()["script_profiles"]
        if name not in profiles:
            raise KeyError("Script profile not found")
        config_path = self.profile_config_path(name)
        self._atomic_write(config_path, json.dumps(profiles[name], indent=2, allow_nan=False).encode() + b"\n")
        environment = {
            "RPI_UTILS_SCRIPT_PROFILE": name,
            "RPI_UTILS_SCRIPT_CONFIG_FILE": str(config_path),
        }
        private_key = self.private_key_path(name)
        password = self.password_path(name)
        if self._credential_exists(private_key):
            environment["RPI_UTILS_SCRIPT_SSH_KEY_FILE"] = str(private_key)
        if self._credential_exists(password):
            environment["RPI_UTILS_SCRIPT_PASSWORD_FILE"] = str(password)
        return environment

    @staticmethod
    def _validate_name(name: str) -> None:
        if not PROFILE_NAME_PATTERN.fullmatch(name):
            raise ValueError("Profile name must start with a lowercase letter and contain only lowercase letters, numbers, underscores, or hyphens")

    @classmethod
    def _validate_config(cls, config: dict[str, Any]) -> None:
        def validate_keys(value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if not isinstance(key, str) or SENSITIVE_CONFIG_KEY.search(key):
                        raise ValueError("Keep passwords, private keys, tokens, and other secrets in the profile credential fields")
                    validate_keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    validate_keys(nested)

        if not isinstance(config, dict):
            raise ValueError("Profile configuration must be a JSON object")
        try:
            encoded = json.dumps(config, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("Profile configuration must contain valid JSON values") from error
        if len(encoded) > MAX_PROFILE_CONFIG_BYTES:
            raise ValueError(f"Profile configuration must be {MAX_PROFILE_CONFIG_BYTES} bytes or smaller")
        validate_keys(config)

    def _write_private_key(self, name: str, private_key: str) -> None:
        encoded = private_key.encode("utf-8")
        first_line = private_key.strip().splitlines()[0] if private_key.strip() else ""
        if len(encoded) > MAX_PRIVATE_KEY_BYTES:
            raise ValueError(f"Private keys must be {MAX_PRIVATE_KEY_BYTES} bytes or smaller")
        if not first_line.startswith("-----BEGIN ") or "PRIVATE KEY-----" not in first_line:
            raise ValueError("Enter an SSH private key, not a .pub public key")
        self._write_secret(self.private_key_path(name), encoded, MAX_PRIVATE_KEY_BYTES)

    @staticmethod
    def _write_secret(path: Path, content: bytes, max_bytes: int) -> None:
        if len(content) > max_bytes:
            raise ValueError(f"Credential must be {max_bytes} bytes or smaller")
        if path.parent.is_symlink():
            raise ValueError("Credential directory must not be a symbolic link")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        ScriptProfileStore._atomic_write(path, content)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.chmod(0o600)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _remove_file(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()

    def _config_root(self) -> Path:
        root = self.settings.path.parent / "script-profiles"
        if root.is_symlink():
            raise ValueError("Script profile directory must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        return root

    def _secret_root(self) -> Path:
        root = self.settings.path.parent / "script-secrets"
        if root.is_symlink():
            raise ValueError("Credential root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        return root

    def profile_config_path(self, name: str) -> Path:
        self._validate_name(name)
        return self._config_root() / f"{name}.json"

    def private_key_path(self, name: str) -> Path:
        self._validate_name(name)
        return self._secret_root() / name / "ssh_private_key"

    def password_path(self, name: str) -> Path:
        self._validate_name(name)
        return self._secret_root() / name / "password"

    @staticmethod
    def _credential_exists(path: Path) -> bool:
        return not path.is_symlink() and path.is_file()
