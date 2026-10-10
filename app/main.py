
import asyncio
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app.programs import ProgramManager
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
        if not is_local_network_host(request.url.hostname):
            return PlainTextResponse("Private network access only", status_code=403)
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                valid_origin = (
                    parsed_origin.scheme == "http"
                    and is_local_network_host(parsed_origin.hostname)
                    and parsed_origin.port == 8002
                )
            except ValueError:
                valid_origin = False
            if not valid_origin:
                return PlainTextResponse("Private network access only", status_code=403)
        return await call_next(request)


app.add_middleware(LocalOnlyMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return {"programs": manager.list_programs()}


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


@app.post("/api/programs/{program_id}/start")
async def start_program(program_id: str) -> dict:
    try:
        return manager.start(program_id)
    except (KeyError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/programs/{program_id}/stop")
async def stop_program(program_id: str) -> dict:
    try:
        return manager.stop(program_id)
    except (KeyError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/ip")
async def global_ip() -> dict:
    import urllib.request

    def fetch_ip() -> str:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
            return response.read(64).decode("ascii").strip()

    try:
        return {"ip": await asyncio.to_thread(fetch_ip)}
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