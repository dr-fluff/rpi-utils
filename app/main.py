
import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app.programs import ProgramManager
from app.telegram.bot import telegram_polling

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "app" / "static"
manager = ProgramManager()


class ProgramInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=1000)
    cwd: str | None = None


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
        if request.url.hostname not in {"127.0.0.1", "localhost"}:
            return PlainTextResponse("Local access only", status_code=403)
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                valid_origin = (
                    parsed_origin.scheme == "http"
                    and parsed_origin.hostname in {"127.0.0.1", "localhost"}
                    and parsed_origin.port == 8002
                )
            except ValueError:
                valid_origin = False
            if not valid_origin:
                return PlainTextResponse("Local access only", status_code=403)
        return await call_next(request)


app.add_middleware(LocalOnlyMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return {"programs": manager.list_programs()}


@app.post("/api/programs", status_code=201)
async def add_program(program: ProgramInput) -> dict:
    try:
        return manager.add_program(program.name, program.command, program.cwd)
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
    result = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(ROOT), "pull", "--ff-only"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        raise HTTPException(status_code=409, detail=output or "Git update failed")
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
            raise HTTPException(status_code=500, detail="Code updated, but dependency installation failed")
    if os.environ.get("INVOCATION_ID"):
        asyncio.create_task(restart_service())
        return {"message": output or "Already up to date", "restarting": True}
    return {"message": output or "Already up to date", "restarting": False}


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

    uvicorn.run(app, host="127.0.0.1", port=8002)