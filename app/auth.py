import hashlib
import hmac
import ipaddress
import os
import secrets
import time

SESSION_COOKIE = "rpi_utils_session"
SESSION_DURATION = 8 * 60 * 60
MINIMUM_PASSWORD_LENGTH = 12
_login_failures: dict[str, list[float]] = {}
_MAX_LOGIN_FAILURES = 5
_LOGIN_WINDOW = 5 * 60


def configured_password() -> str | None:
    password = os.environ.get("RPI_UTILS_WEB_PASSWORD", "")
    return password or None


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
