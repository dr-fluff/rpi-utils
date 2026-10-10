import asyncio
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
from codecs import getincrementaldecoder
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.auth import (
    SESSION_COOKIE,
    SESSION_DURATION,
    configured_password,
    is_loopback_host,
    valid_session,
)

logger = logging.getLogger(__name__)


def _valid_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "").lower()
    if not origin or not host:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    secure_connection = websocket.url.scheme in {"wss", "https"}
    if not secure_connection and not (
        websocket.url.scheme == "ws"
        and websocket.client is not None
        and is_loopback_host(websocket.client.host)
    ):
        return False
    return (
        parsed.scheme == ("https" if secure_connection else "http")
        and parsed.netloc.lower() == host
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


async def terminal_socket(websocket: WebSocket) -> None:
    password = configured_password()
    token = websocket.cookies.get(SESSION_COOKIE)
    loopback_client = (
        websocket.client is not None
        and is_loopback_host(websocket.client.host)
    )
    if (
        (password is None and not loopback_client)
        or (password is not None and not valid_session(password, token))
        or not (
            websocket.url.scheme in {"wss", "https"}
            or (
                websocket.url.scheme == "ws"
                and loopback_client
            )
        )
        or not _valid_origin(websocket)
    ):
        await websocket.close(code=4403)
        return

    await websocket.accept()
    try:
        master_fd, slave_fd = pty.openpty()
    except OSError as error:
        logger.exception("Could not allocate terminal PTY")
        await websocket.close(code=1011, reason=f"Could not allocate terminal: {error}")
        return
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        process = subprocess.Popen(
            [shell, "-i"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=Path.home(),
            env={**os.environ, "TERM": "xterm-256color"},
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        os.close(master_fd)
        os.close(slave_fd)
        logger.exception("Could not start terminal shell")
        await websocket.close(code=1011, reason=f"Could not start shell: {error}")
        return
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()
    decoder = getincrementaldecoder("utf-8")("replace")

    async def read_output() -> None:
        while True:
            ready = loop.create_future()

            def notify_readable(future=ready) -> None:
                if not future.done():
                    future.set_result(None)

            loop.add_reader(master_fd, notify_readable)
            try:
                await ready
                output = os.read(master_fd, 4096)
            except BlockingIOError:
                continue
            except OSError:
                return
            finally:
                loop.remove_reader(master_fd)
            if not output:
                remainder = decoder.decode(b"", final=True)
                if remainder:
                    async with send_lock:
                        await websocket.send_text(remainder)
                return
            async with send_lock:
                await websocket.send_text(decoder.decode(output))

    async def read_input() -> None:
        while True:
            message = await websocket.receive_text()
            if len(message) > 65536:
                await websocket.close(code=1009)
                return
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "input" and isinstance(payload.get("data"), str):
                pending_input = memoryview(payload["data"].encode())
                while pending_input:
                    written = os.write(master_fd, pending_input)
                    pending_input = pending_input[written:]
            elif payload.get("type") == "resize":
                columns = payload.get("cols")
                rows = payload.get("rows")
                if (
                    type(columns) is int
                    and type(rows) is int
                    and 1 <= columns <= 500
                    and 1 <= rows <= 300
                ):
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    async def expire_session() -> None:
        await asyncio.sleep(SESSION_DURATION)
        await websocket.close(code=4401, reason="Dashboard session expired")

    output_task = asyncio.create_task(read_output())
    input_task = asyncio.create_task(read_input())
    expiry_task = asyncio.create_task(expire_session())
    try:
        done, pending = await asyncio.wait(
            {output_task, input_task, expiry_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
        if (
            output_task in done
            and output_task.exception() is None
            and websocket.application_state == WebSocketState.CONNECTED
        ):
            await websocket.close(code=1000)
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        loop.remove_reader(master_fd)
        try:
            os.killpg(process.pid, signal.SIGTERM)
            await asyncio.to_thread(process.wait, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            await asyncio.to_thread(process.wait)
        os.close(master_fd)
