import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

SESSION_COOKIE = "rpi_utils_session"
SESSION_DURATION = 8 * 60 * 60
MINIMUM_PASSWORD_LENGTH = 12
_login_failures: dict[str, list[float]] = {}
_MAX_LOGIN_FAILURES = 5
_LOGIN_WINDOW = 5 * 60


def configured_password() -> str | None:
    password = os.environ.get("RPI_UTILS_WEB_PASSWORD", "")
    return password or None


def password_change_required() -> bool:
    return os.environ.get("RPI_UTILS_PASSWORD_CHANGE_REQUIRED") == "1"


def change_password(password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MINIMUM_PASSWORD_LENGTH} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in password):
        raise ValueError("Password cannot contain control characters")

    env_path = Path.home() / ".config" / "rpi-utils" / "env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.parent.chmod(0o700)
    try:
        existing_lines = env_path.read_text().splitlines()
    except FileNotFoundError:
        existing_lines = []
    except OSError as error:
        raise OSError(f"Could not read dashboard settings: {error}") from error

    settings = {
        "RPI_UTILS_WEB_PASSWORD": password,
        "RPI_UTILS_PASSWORD_CHANGE_REQUIRED": "0",
    }
    setting_pattern = re.compile(
        r"^\s*(RPI_UTILS_WEB_PASSWORD|RPI_UTILS_PASSWORD_CHANGE_REQUIRED)\s*="
    )
    retained_lines = [line for line in existing_lines if not setting_pattern.match(line)]
    for name, value in settings.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        retained_lines.append(f'{name}="{escaped}"')

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=env_path.parent,
            prefix=".env-",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write("\n".join(retained_lines) + "\n")
        temporary_path.chmod(0o600)
        temporary_path.replace(env_path)
        env_path.chmod(0o600)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise OSError(f"Could not save dashboard password: {error}") from error

    os.environ["RPI_UTILS_WEB_PASSWORD"] = password
    os.environ["RPI_UTILS_PASSWORD_CHANGE_REQUIRED"] = "0"


def allowed_hosts() -> set[str]:
    return {
        host.strip().lower()
        for host in os.environ.get("RPI_UTILS_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }


def is_loopback_host(hostname: str | None) -> bool:
    if hostname == "localhost":
        return True
    if hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def session_token(password: str, issued_at: int | None = None) -> str:
    timestamp = int(time.time()) if issued_at is None else issued_at
    nonce = secrets.token_urlsafe(16)
    return f"{timestamp}.{nonce}.{_session_signature(password, timestamp, nonce)}"


def _session_signature(password: str, timestamp: int, nonce: str) -> str:
    signature = hmac.new(
        password.encode(),
        f"{timestamp}.{nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return signature


def valid_session(password: str | None, token: str | None, now: int | None = None) -> bool:
    if not password or not token:
        return False
    try:
        timestamp_text, nonce, signature = token.split(".")
        timestamp = int(timestamp_text)
    except (ValueError, TypeError):
        return False
    current_time = int(time.time()) if now is None else now
    if timestamp > current_time or current_time - timestamp > SESSION_DURATION:
        return False
    expected = _session_signature(password, timestamp, nonce)
    return hmac.compare_digest(signature, expected)


def login_is_limited(client_id: str, now: float | None = None) -> bool:
    current_time = time.monotonic() if now is None else now
    failures = [timestamp for timestamp in _login_failures.get(client_id, []) if current_time - timestamp < _LOGIN_WINDOW]
    _login_failures[client_id] = failures
    return len(failures) >= _MAX_LOGIN_FAILURES


def record_login_failure(client_id: str, now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    _login_failures.setdefault(client_id, []).append(current_time)


def clear_login_failures(client_id: str) -> None:
    _login_failures.pop(client_id, None)


def clear_login_limiter() -> None:
    _login_failures.clear()
