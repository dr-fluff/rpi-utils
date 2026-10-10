import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketState

from app.auth import (
    SESSION_COOKIE,
    SESSION_DURATION,
    clear_login_limiter,
    login_is_limited,
    record_login_failure,
    session_token,
    valid_session,
)
from app.main import LoginInput, LocalOnlyMiddleware, app, auth_login, auth_status
from app.terminal import terminal_socket


def make_request(
    scheme: str = "https",
    host: str = "pi-console.home.arpa",
    path: str = "/api/status",
    cookie: str | None = None,
    client_host: str = "192.168.1.12",
) -> Request:
    headers = [(b"host", host.encode())]
    if cookie:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": (client_host, 12345),
            "server": (host, 443 if scheme == "https" else 80),
        }
    )


class AuthSessionTests(unittest.TestCase):
    def test_session_signature_and_expiry_are_checked(self):
        password = "a-long-test-password"
        token = session_token(password, issued_at=1000)

        self.assertTrue(valid_session(password, token, now=1000))
        self.assertFalse(valid_session(password, token + "tampered", now=1000))
        self.assertFalse(valid_session("different-password", token, now=1000))
        self.assertFalse(valid_session(password, token, now=1000 + SESSION_DURATION + 1))

    def test_login_failure_limiter_expires_and_can_be_cleared(self):
        clear_login_limiter()
        record_login_failure("client", now=10)
        self.assertFalse(login_is_limited("client", now=10))
        for _ in range(5):
            record_login_failure("client", now=11)
        self.assertTrue(login_is_limited("client", now=12))
        self.assertFalse(login_is_limited("client", now=11 + 5 * 60))
        clear_login_limiter()

    def test_successful_login_sets_httponly_secure_session_cookie(self):
        request = make_request()
        response = Response()
        with patch.dict(os.environ, {"RPI_UTILS_WEB_PASSWORD": "long-enough-test-password"}):
            result = asyncio.run(
                auth_login(
                    LoginInput(password="long-enough-test-password"),
                    request,
                    response,
                )
            )

        self.assertEqual(result, {"authenticated": True})
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=strict", cookie)

    def test_login_rejects_weak_configured_password(self):
        with patch.dict(os.environ, {"RPI_UTILS_WEB_PASSWORD": "short"}):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    auth_login(
                        LoginInput(password="short"),
                        make_request(),
                        Response(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 500)


class AuthMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_status_exposes_terminal_only_for_local_no_password_session(self):
        response = Response()
        with patch.dict(os.environ, {}, clear=True):
            status = await auth_status(
                make_request(
                    scheme="http",
                    host="127.0.0.1",
                    path="/api/auth/status",
                    client_host="127.0.0.1",
                ),
                response,
            )

        self.assertEqual(
            status,
            {"enabled": False, "authenticated": True, "terminal_available": True},
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_remote_dashboard_requires_configured_password(self):
        middleware = LocalOnlyMiddleware(app)
        with patch.dict(os.environ, {}, clear=True):
            response = await middleware.dispatch(
                make_request(host="192.168.1.12"),
                self.next_response,
            )
            spoofed_local_host = await middleware.dispatch(
                make_request(scheme="http", host="localhost"),
                self.next_response,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(spoofed_local_host.status_code, 503)

    async def test_unauthenticated_api_is_rejected_but_login_page_is_available(self):
        middleware = LocalOnlyMiddleware(app)
        with patch.dict(
            os.environ,
            {
                "RPI_UTILS_WEB_PASSWORD": "long-enough-test-password",
                "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
            },
        ):
            api_response = await middleware.dispatch(make_request(), self.next_response)
            page_response = await middleware.dispatch(
                make_request(path="/"),
                self.next_response,
            )

        self.assertEqual(api_response.status_code, 401)
        self.assertEqual(page_response.status_code, 204)

    async def test_local_development_can_use_dashboard_without_password(self):
        middleware = LocalOnlyMiddleware(app)
        with patch.dict(os.environ, {}, clear=True):
            response = await middleware.dispatch(
                make_request(
                    scheme="http",
                    host="127.0.0.1",
                    client_host="127.0.0.1",
                ),
                self.next_response,
            )

        self.assertEqual(response.status_code, 204)

    async def test_authenticated_api_requires_https_for_remote_host(self):
        password = "long-enough-test-password"
        token = session_token(password)
        middleware = LocalOnlyMiddleware(app)
        with patch.dict(
            os.environ,
            {
                "RPI_UTILS_WEB_PASSWORD": password,
                "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
            },
        ):
            secure_response = await middleware.dispatch(
                make_request(cookie=token),
                self.next_response,
            )
            http_response = await middleware.dispatch(
                make_request(scheme="http", cookie=token),
                self.next_response,
            )

        self.assertEqual(secure_response.status_code, 204)
        self.assertEqual(http_response.status_code, 426)

    async def test_unlisted_host_is_rejected(self):
        middleware = LocalOnlyMiddleware(app)
        with patch.dict(
            os.environ,
            {
                "RPI_UTILS_WEB_PASSWORD": "long-enough-test-password",
                "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
            },
        ):
            response = await middleware.dispatch(
                make_request(host="other.home.arpa"),
                self.next_response,
            )

        self.assertEqual(response.status_code, 403)

    def test_login_protects_api_and_logout_clears_the_session(self):
        clear_login_limiter()
        password = "long-enough-test-password"
        with (
            patch.dict(
                os.environ,
                {
                    "RPI_UTILS_WEB_PASSWORD": password,
                    "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
                },
            ),
            TestClient(app, base_url="https://pi-console.home.arpa") as client,
        ):
            self.assertEqual(client.get("/api/status").status_code, 401)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(
                client.post("/api/auth/login", json={"password": "incorrect"}).status_code,
                401,
            )
            login = client.post("/api/auth/login", json={"password": password})
            self.assertEqual(login.status_code, 200)
            self.assertIn("secure", login.headers["set-cookie"].lower())
            self.assertEqual(client.get("/api/status").status_code, 200)
            self.assertEqual(client.post("/api/auth/logout").status_code, 200)
            self.assertEqual(client.get("/api/status").status_code, 401)

    def test_authenticated_terminal_runs_a_pty_shell(self):
        password = "long-enough-test-password"
        socket = FakeTerminalSocket(password)
        with patch.dict(os.environ, {"RPI_UTILS_WEB_PASSWORD": password, "SHELL": "/bin/sh"}):
            asyncio.run(terminal_socket(socket))

        self.assertTrue(any("PTY_READY" in message for message in socket.output))

    def test_local_terminal_works_without_dashboard_password(self):
        socket = FakeTerminalSocket("unused-password")
        socket.cookies.clear()
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}, clear=True):
            asyncio.run(terminal_socket(socket))

        self.assertTrue(any("PTY_READY" in message for message in socket.output))

    def test_terminal_rejects_missing_session_before_starting_a_shell(self):
        socket = FakeTerminalSocket("long-enough-test-password")
        socket.cookies.clear()
        with (
            patch.dict(os.environ, {"RPI_UTILS_WEB_PASSWORD": "long-enough-test-password"}),
            patch("app.terminal.subprocess.Popen") as popen,
        ):
            asyncio.run(terminal_socket(socket))

        popen.assert_not_called()
        self.assertEqual(socket.close_code, 4403)

    @staticmethod
    async def next_response(_request, call_next=None):
        return Response(status_code=204)


class FakeTerminalSocket(WebSocket):
    def __init__(self, password: str) -> None:
        self.output: list[str] = []
        self.received_input = False
        self.close_code = None
        self.close_reason = ""

        async def receive():
            if self.application_state == WebSocketState.CONNECTING:
                return {"type": "websocket.connect"}
            if not self.received_input:
                self.received_input = True
                return {
                    "type": "websocket.receive",
                    "text": json.dumps({"type": "input", "data": "printf 'PTY_READY\\n'; exit\n"}),
                }
            await asyncio.Future()

        async def send(message):
            if message["type"] == "websocket.send":
                self.output.append(message["text"])
            elif message["type"] == "websocket.close":
                self.close_code = message.get("code", 1000)
                self.close_reason = message.get("reason", "")

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("127.0.0.1", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "path": "/api/terminal",
            "raw_path": b"/api/terminal",
            "query_string": b"",
            "headers": [
                (b"origin", b"http://127.0.0.1"),
                (b"host", b"127.0.0.1"),
                (
                    b"cookie",
                    f"{SESSION_COOKIE}={session_token(password)}".encode(),
                ),
            ],
            "subprotocols": [],
        }
        super().__init__(scope, receive, send)


if __name__ == "__main__":
    unittest.main()
