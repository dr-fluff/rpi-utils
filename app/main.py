
import asyncio
import hmac
import ipaddress
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import (
    MINIMUM_PASSWORD_LENGTH,
    SESSION_COOKIE,
    SESSION_DURATION,
    allowed_hosts,
    clear_login_failures,
    configured_password,
    is_loopback_host,
    login_is_limited,
    record_login_failure,
    session_token,
    valid_session,
)
from app.commands import COMMANDS, COMMAND_BY_NAME, CommandError, get_global_ip, run_command
from app.programs import ProgramManager
from app.terminal import terminal_socket
from app.telegram.bot import telegram_polling

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "app" / "static"
GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/dr-fluff/rpi-utils/releases/latest"
manager = ProgramManager()


def is_local_network_host(hostname: str | None) -> bool:
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    if hostname is None:
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


class ProgramInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=1000)
    cwd: str | None = None
    url: str | None = Field(default=None, max_length=2048)


class RunningProgramInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    pid: int = Field(gt=0)
    url: str | None = Field(default=None, max_length=2048)


class CommandInput(BaseModel):
    args: list[str] = Field(default_factory=list, max_length=10)


class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@asynccontextmanager
async def lifespan(_: FastAPI):
    bot_task = asyncio.create_task(telegram_polling(manager))
    yield
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Raspberry Pi Control", lifespan=lifespan)


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        hostname = (request.url.hostname or "").lower()
        if not is_local_network_host(hostname) and hostname not in allowed_hosts():
            return PlainTextResponse("Private network access only", status_code=403)
        client_host = request.client.host if request.client else None
        loopback_client = is_loopback_host(client_host)
        password = configured_password()
        if password is None and not loopback_client:
            return JSONResponse(
                {"detail": "Configure RPI_UTILS_WEB_PASSWORD before remote dashboard access"},
                status_code=503,
            )
        if password and request.url.scheme != "https" and not loopback_client:
            return JSONResponse(
                {"detail": "HTTPS is required for authenticated dashboard access"},
                status_code=426,
            )
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                valid_origin = (
                    parsed_origin.scheme == request.url.scheme
                    and parsed_origin.netloc.lower() == request.headers.get("host", "").lower()
                    and not parsed_origin.path
                    and not parsed_origin.query
                    and not parsed_origin.fragment
                )
            except ValueError:
                valid_origin = False
            if not valid_origin:
                return PlainTextResponse("Private network access only", status_code=403)
        public_paths = {"/", "/api/auth/status", "/api/auth/login"}
        if password and request.url.path not in public_paths and not request.url.path.startswith("/static/"):
            token = request.cookies.get(SESSION_COOKIE)
            if not valid_session(password, token):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return await call_next(request)


app.add_middleware(LocalOnlyMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/auth/status")
async def auth_status(request: Request, response: Response) -> dict:
    password = configured_password()
    response.headers["Cache-Control"] = "no-store"
    client_host = request.client.host if request.client else None
    local_access = is_loopback_host(client_host)
    return {
        "enabled": password is not None,
        "authenticated": password is None or valid_session(
            password,
            request.cookies.get(SESSION_COOKIE),
        ),
        "terminal_available": password is not None or local_access,
    }


@app.post("/api/auth/login")
async def auth_login(login: LoginInput, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    password = configured_password()
    if password is None:
        raise HTTPException(status_code=404, detail="Dashboard login is not configured")
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=500,
            detail=f"RPI_UTILS_WEB_PASSWORD must be at least {MINIMUM_PASSWORD_LENGTH} characters",
        )
    client_id = request.client.host if request.client else "unknown"
    if login_is_limited(client_id):
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again in five minutes.")
    if not hmac.compare_digest(login.password.encode(), password.encode()):
        record_login_failure(client_id)
        raise HTTPException(status_code=401, detail="Incorrect password")
    clear_login_failures(client_id)
    response.set_cookie(
        SESSION_COOKIE,
        session_token(password),
        max_age=SESSION_DURATION,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return {"authenticated": True}


@app.post("/api/auth/logout")
async def auth_logout(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"authenticated": False}


@app.websocket("/api/terminal")
async def terminal(websocket: WebSocket) -> None:
    hostname = (websocket.url.hostname or "").lower()
    if not is_local_network_host(hostname) and hostname not in allowed_hosts():
        await websocket.close(code=4403)
        return
    await terminal_socket(websocket)


@app.get("/api/status")
async def status() -> dict:
    return {"programs": manager.list_programs()}


@app.get("/api/update-check")
async def update_check() -> dict:
    try:
        return await asyncio.to_thread(check_for_update)
    except HTTPException as error:
        if error.status_code == 404:
            return {"update_available": False, "latest_version": None}
        raise
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=502, detail=f"Could not check for updates: {error}") from error


@app.get("/api/commands")
async def available_commands() -> dict:
    return {"commands": COMMANDS}


@app.post("/api/commands/{command_name}")
async def execute_command(command_name: str, command_input: CommandInput) -> dict:
    if command_name not in COMMAND_BY_NAME:
        raise HTTPException(status_code=404, detail=f"Unknown command: {command_name}")
    try:
        return await run_command(command_name, manager, command_input.args)
    except CommandError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/processes")
async def running_processes() -> dict:
    try:
        return {"processes": manager.list_running_processes()}
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not inspect running processes: {error}") from error


@app.post("/api/programs", status_code=201)
async def add_program(program: ProgramInput) -> dict:
    try:
        return manager.add_program(program.name, program.command, program.cwd, program.url)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/programs/running", status_code=201)
async def add_running_program(program: RunningProgramInput) -> dict:
    try:
        return manager.add_running_process(program.name, program.pid, program.url)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete("/api/programs/{program_id}")
async def remove_program(program_id: str) -> dict:
    try:
        manager.remove_program(program_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"message": "Program removed from the list. Any running process was left running."}


@app.post("/api/programs/{program_id}/start")
async def start_program(program_id: str) -> dict:
    try:
        return await run_command("start", manager, [program_id])
    except CommandError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/programs/{program_id}/stop")
async def stop_program(program_id: str) -> dict:
    try:
        return await run_command("stop", manager, [program_id])
    except CommandError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/ip")
async def global_ip() -> dict:
    try:
        return {"ip": await asyncio.to_thread(get_global_ip)}
    except Exception as error:
        raise HTTPException(status_code=502, detail="Could not determine global IP") from error


def get_latest_release_tag() -> str:
    request = URLRequest(
        GITHUB_LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "rpi-utils"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            release = json.loads(response.read())
    except HTTPError as error:
        if error.code == 404:
            raise HTTPException(status_code=404, detail="No published GitHub release is available") from error
        raise HTTPException(
            status_code=502,
            detail=f"GitHub release lookup failed with HTTP {error.code}",
        ) from error
    except URLError as error:
        raise HTTPException(status_code=502, detail=f"Could not reach GitHub: {error.reason}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=502, detail="GitHub returned invalid release data") from error

    if not isinstance(release, dict):
        raise HTTPException(status_code=502, detail="GitHub returned invalid release data")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise HTTPException(status_code=502, detail="GitHub release does not contain a tag")
    return tag


def check_for_update() -> dict:
    if not (ROOT / ".git").exists():
        raise HTTPException(status_code=400, detail="Update checks require a Git checkout")

    tag = get_latest_release_tag()
    ref = f"refs/tags/{tag}"
    valid_ref = subprocess.run(
        ["git", "-C", str(ROOT), "check-ref-format", ref],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if valid_ref.returncode:
        raise HTTPException(status_code=502, detail="GitHub returned an invalid release tag")

    current = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if current.returncode:
        detail = (current.stdout + current.stderr).strip()
        raise HTTPException(status_code=500, detail=detail or "Could not determine installed version")

    remote = subprocess.run(
        ["git", "-C", str(ROOT), "ls-remote", "--tags", "origin", ref, f"{ref}^{{}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if remote.returncode:
        detail = (remote.stdout + remote.stderr).strip()
        raise HTTPException(status_code=502, detail=detail or "Could not check the latest release tag")

    refs = {}
    for line in remote.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2:
            refs[fields[1]] = fields[0]
    release_commit = refs.get(f"{ref}^{{}}", refs.get(ref))
    if release_commit is None:
        raise HTTPException(status_code=502, detail=f"Release tag {tag} was not found on origin")

    current_commit = current.stdout.strip()
    return {
        "update_available": current_commit != release_commit,
        "latest_version": tag,
        "current_version": current_commit[:12],
    }


@app.post("/api/update")
async def update() -> dict:
    if not (ROOT / ".git").exists():
        raise HTTPException(status_code=400, detail="Updates require a Git checkout")
    changes = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if changes.returncode or changes.stdout.strip():
        raise HTTPException(status_code=409, detail="Working tree must be clean before updating")
    tag = await asyncio.to_thread(get_latest_release_tag)
    ref = f"refs/tags/{tag}"
    valid_ref = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(ROOT), "check-ref-format", ref],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if valid_ref.returncode:
        raise HTTPException(status_code=502, detail="GitHub returned an invalid release tag")
    fetch = await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-C",
            str(ROOT),
            "fetch",
            "--force",
            "--no-tags",
            "origin",
            f"{ref}:{ref}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if fetch.returncode:
        output = (fetch.stdout + fetch.stderr).strip()
        raise HTTPException(status_code=502, detail=output or f"Could not fetch release {tag}")
    checkout = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(ROOT), "checkout", "--detach", ref],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if checkout.returncode:
        output = (checkout.stdout + checkout.stderr).strip()
        raise HTTPException(status_code=500, detail=output or f"Could not install release {tag}")
    try:
        system_upgrade = await asyncio.to_thread(
            subprocess.run,
            ["sudo", "-n", "/usr/local/sbin/rpi-utils-system-upgrade"],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(
            status_code=500,
            detail=f"Git updated, but the system package upgrade could not complete: {error}",
        ) from error
    if system_upgrade.returncode:
        upgrade_output = (system_upgrade.stdout + system_upgrade.stderr).strip()
        raise HTTPException(
            status_code=500,
            detail="Git updated, but the system package upgrade failed"
            + (f": {upgrade_output[-4000:]}" if upgrade_output else ""),
        )
    pip = ROOT / ".venv" / "bin" / "pip"
    if pip.exists():
        install = await asyncio.to_thread(
            subprocess.run,
            [str(pip), "install", "--editable", str(ROOT)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if install.returncode:
            raise HTTPException(
                status_code=500,
                detail=f"Release {tag} checked out, but dependency installation failed",
            )
    message = f"Installed GitHub release {tag}. System package upgrade completed."
    if os.environ.get("INVOCATION_ID"):
        asyncio.create_task(restart_service())
        return {"message": message, "restarting": True}
    return {"message": message, "restarting": False}


async def restart_service() -> None:
    await asyncio.sleep(1)
    subprocess.Popen(
        ["systemctl", "restart", "--no-block", "rpi-utils.service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)