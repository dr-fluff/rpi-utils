
import asyncio
import hmac
import ipaddress
import json
import os
import shlex
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
    change_password,
    clear_login_failures,
    configured_password,
    is_loopback_host,
    login_is_limited,
    password_change_required,
    record_login_failure,
    session_token,
    valid_session,
)
from app.commands import COMMANDS, COMMAND_BY_NAME, CommandError, get_global_ip, run_command
from app.custom_commands import (
    MAX_SCRIPT_BYTES,
    CustomCommandStore,
    UserSettingsStore,
)
from app.docker_projects import (
    MAX_COMPOSE_BYTES,
    DockerConfigurationError,
    DockerError,
    DockerProjectManager,
    DockerUnavailableError,
)
from app.programs import ProgramManager
from app.services import ServiceManager
from app.terminal import terminal_socket
from app.telegram.bot import telegram_polling
from app.telegram.settings import TelegramSettingsStore

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "app" / "static"
GITHUB_RELEASES_URL = "https://api.github.com/repos/dr-fluff/rpi-utils/releases?per_page=100"
manager = ProgramManager()
services = ServiceManager()
docker_projects = DockerProjectManager()
user_settings = UserSettingsStore()
custom_commands = CustomCommandStore(user_settings)
telegram_settings = TelegramSettingsStore()


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


class ProgramEditInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=2048)


class ServiceInput(BaseModel):
    unit: str = Field(min_length=1, max_length=256)


class DockerProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    compose_yaml: str = Field(min_length=1, max_length=MAX_COMPOSE_BYTES)


class DockerContainerInput(BaseModel):
    container_id: str = Field(min_length=12, max_length=64, pattern=r"^[a-fA-F0-9]+$")


class CommandInput(BaseModel):
    args: list[str] = Field(default_factory=list, max_length=10)


class CustomCommandInput(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=120)
    command: str = Field(default="", max_length=1000)
    script_name: str | None = Field(default=None, max_length=68)
    arguments: str = Field(default="", max_length=1000)
    telegram_enabled: bool = False
    requires_confirmation: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    icon: str = Field(default="bolt", max_length=32)


class UserSettingsInput(BaseModel):
    scripts_directory: str = Field(min_length=1, max_length=1024)
    default_timeout_seconds: int = Field(ge=1, le=300)


class TelegramSettingsInput(BaseModel):
    bot_token: str = Field(default="", max_length=256)
    allowed_chat_ids: str = Field(default="", max_length=4096)
    clear_bot_token: bool = False


class ScriptInput(BaseModel):
    content: str = Field(max_length=MAX_SCRIPT_BYTES)


class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeInput(BaseModel):
    password: str = Field(min_length=MINIMUM_PASSWORD_LENGTH, max_length=1024)

class TelegramNotificationInput(BaseModel):
    message: str = Field(min_length=1, max_length=4096)

@asynccontextmanager
async def lifespan(_: FastAPI):
    app.state.telegram_task_lock = asyncio.Lock()
    app.state.telegram_notification_queue = asyncio.Queue()
    app.state.telegram_task = asyncio.create_task(
        telegram_polling(
            manager,
            custom_commands,
            app.state.telegram_notification_queue,
        )
    )
    yield
    bot_task = app.state.telegram_task
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

        local_notification_endpoint = (
            request.url.path == "/api/internal/telegram/notify"
            and loopback_client
        )

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
                local_notification_endpoint = (
        request.url.path == "/api/internal/telegram/notify"
            and loopback_client
        )
        public_paths = {"/", "/commands", "/api/auth/status", "/api/auth/login"}
        if (
            password
            and request.url.path not in public_paths
            and not request.url.path.startswith("/static/")
            and not local_notification_endpoint
        ):
            token = request.cookies.get(SESSION_COOKIE)
            if not valid_session(password, token):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
        if (
            password
            and password_change_required()
            and not local_notification_endpoint
            and request.url.path not in {
                "/",
                "/commands",
                "/api/auth/status",
                "/api/auth/login",
                "/api/auth/password",
                "/api/auth/logout",
            }
            and not request.url.path.startswith("/static/")
        ):
            return JSONResponse(
                {"detail": "Change the temporary dashboard password before continuing"},
                status_code=403,
            )
        return await call_next(request)


app.add_middleware(LocalOnlyMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/commands")
async def commands_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "commands.html")


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "settings.html")


async def restart_telegram_bot(request: Request) -> bool:
    lock = getattr(request.app.state, "telegram_task_lock", None)
    if lock is None:
        return False

    async with lock:
        previous_task = getattr(request.app.state, "telegram_task", None)

        if previous_task is not None and not previous_task.done():
            previous_task.cancel()
            await asyncio.gather(previous_task, return_exceptions=True)

        request.app.state.telegram_task = asyncio.create_task(
            telegram_polling(
                manager,
                custom_commands,
                request.app.state.telegram_notification_queue,
            )
        )

    return True

@app.post("/api/internal/telegram/notify")
async def internal_telegram_notify(
    payload: TelegramNotificationInput,
    request: Request,
):
    client_host = request.client.host if request.client else None

    if not is_loopback_host(client_host):
        raise HTTPException(
            status_code=403,
            detail="Notifications are only accepted from localhost",
        )

    bot_task = getattr(request.app.state, "telegram_task", None)
    if bot_task is None or bot_task.done():
        raise HTTPException(
            status_code=503,
            detail="Telegram service is not running",
        )

    queue = request.app.state.telegram_notification_queue
    future = asyncio.get_running_loop().create_future()
    await queue.put((payload.message, future))

    try:
        delivered = await asyncio.wait_for(future, timeout=25)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Timed out waiting for Telegram delivery",
        )

    if not delivered:
        raise HTTPException(
            status_code=502,
            detail="Telegram notification delivery failed",
        )

    return {"ok": True}


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
        "must_change_password": (
            password is not None
            and valid_session(password, request.cookies.get(SESSION_COOKIE))
            and password_change_required()
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


@app.post("/api/auth/password")
async def auth_change_password(
    change: PasswordChangeInput,
    request: Request,
    response: Response,
) -> dict:
    current_password = configured_password()
    if current_password is None or not valid_session(
        current_password,
        request.cookies.get(SESSION_COOKIE),
    ):
        raise HTTPException(status_code=401, detail="Sign in before changing the password")
    try:
        change_password(change.password)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        SESSION_COOKIE,
        session_token(change.password),
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
    return {
        "programs": manager.list_programs(),
        "services": await asyncio.to_thread(services.list_services),
    }


@app.get("/api/update-check")
async def update_check() -> dict:
    try:
        return await asyncio.to_thread(check_for_update)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=502, detail=f"Could not check for updates: {error}") from error


@app.get("/api/commands")
async def available_commands() -> dict:
    built_in = [{**command, "is_custom": False} for command in COMMANDS]
    user_defined = [
        {
            **command,
            "command": shlex.join(command["command"]),
            "usage": "",
            "program_action": False,
            "is_custom": True,
        }
        for command in custom_commands.list()
    ]
    return {"commands": built_in + user_defined}


@app.get("/api/settings")
async def get_settings() -> dict:
    try:
        settings = user_settings.get()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Could not read settings: {error}") from error
    return {
        "scripts_directory": settings["scripts_directory"],
        "default_timeout_seconds": settings["default_timeout_seconds"],
    }


@app.put("/api/settings")
async def update_settings(settings: UserSettingsInput) -> dict:
    try:
        updated = user_settings.update(settings.scripts_directory, settings.default_timeout_seconds)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not save settings: {error}") from error
    return {
        "scripts_directory": updated["scripts_directory"],
        "default_timeout_seconds": updated["default_timeout_seconds"],
    }


@app.get("/api/telegram-settings")
async def get_telegram_settings(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return telegram_settings.get()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.put("/api/telegram-settings")
async def update_telegram_settings(
    settings: TelegramSettingsInput,
    request: Request,
    response: Response,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        updated = telegram_settings.update(
            settings.bot_token,
            settings.allowed_chat_ids,
            settings.clear_bot_token,
        )
        updated["restarted"] = await restart_telegram_bot(request)
        return updated
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/scripts")
async def list_scripts() -> dict:
    try:
        return {
            "scripts_directory": str(user_settings.scripts_directory()),
            "scripts": user_settings.list_scripts(),
        }
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Could not list scripts: {error}") from error


@app.get("/api/scripts/{script_name}")
async def get_script(script_name: str) -> dict:
    try:
        return {"name": script_name, "content": user_settings.read_script(script_name)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=500, detail=f"Could not read script: {error}") from error


@app.put("/api/scripts/{script_name}")
async def save_script(script_name: str, script: ScriptInput) -> dict:
    try:
        return user_settings.write_script(script_name, script.content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not save script: {error}") from error


@app.delete("/api/scripts/{script_name}")
async def delete_script(script_name: str) -> dict:
    try:
        user_settings.delete_script(script_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=500, detail=f"Could not delete script: {error}") from error
    return {"message": f"Deleted script {script_name}."}


@app.post("/api/commands/{command_name}")
async def execute_command(command_name: str, command_input: CommandInput) -> dict:
    if command_name not in COMMAND_BY_NAME and custom_commands.get(command_name) is None:
        raise HTTPException(status_code=404, detail=f"Unknown command: {command_name}")
    try:
        return await run_command(command_name, manager, command_input.args, custom_commands)
    except CommandError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/custom-commands", status_code=201)
async def add_custom_command(command: CustomCommandInput) -> dict:
    try:
        result = custom_commands.add(
            command.name,
            command.description,
            command.command,
            command.script_name,
            command.arguments,
            command.telegram_enabled,
            command.requires_confirmation,
            command.timeout_seconds,
            set(COMMAND_BY_NAME),
            command.icon,
        )
        resolved = custom_commands.get(result["name"])
        if resolved is None:
            raise RuntimeError("Saved command could not be loaded")
        return {**resolved, "command": shlex.join(resolved["command"])}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.put("/api/custom-commands/{command_name}")
async def edit_custom_command(command_name: str, command: CustomCommandInput) -> dict:
    try:
        result = custom_commands.update(
            command_name,
            command.name,
            command.description,
            command.command,
            command.script_name,
            command.arguments,
            command.telegram_enabled,
            command.requires_confirmation,
            command.timeout_seconds,
            set(COMMAND_BY_NAME),
            command.icon,
        )
        resolved = custom_commands.get(result["name"])
        if resolved is None:
            raise RuntimeError("Updated command could not be loaded")
        return {**resolved, "command": shlex.join(resolved["command"])}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.delete("/api/custom-commands/{command_name}")
async def delete_custom_command(command_name: str) -> dict:
    try:
        custom_commands.remove(command_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"message": f"Removed /{command_name}."}


@app.get("/api/processes")
async def running_processes() -> dict:
    try:
        return {"processes": manager.list_running_processes()}
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not inspect running processes: {error}") from error


@app.get("/api/docker/projects")
async def list_docker_projects() -> dict:
    try:
        projects = await asyncio.to_thread(docker_projects.list_projects)
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not read Docker projects: {error}") from error
    return {"projects": projects}


@app.get("/api/docker/containers/available")
async def available_docker_containers() -> dict:
    try:
        containers = await asyncio.to_thread(docker_projects.available_containers)
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not list running Docker containers: {error}") from error
    return {"containers": containers}


@app.get("/api/docker/containers")
async def list_imported_docker_containers() -> dict:
    try:
        containers = await asyncio.to_thread(docker_projects.list_imported_containers)
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not read imported Docker containers: {error}") from error
    return {"containers": containers}


@app.post("/api/docker/containers/import", status_code=201)
async def import_docker_container(container: DockerContainerInput) -> dict:
    try:
        imported = await asyncio.to_thread(
            docker_projects.import_container,
            container.container_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not import Docker container: {error}") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not save imported Docker container: {error}") from error
    return imported


@app.post("/api/docker/containers/{container_id}/restart")
async def restart_imported_docker_container(container_id: str) -> dict:
    try:
        restarted = await asyncio.to_thread(
            docker_projects.restart_imported_container,
            container_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not restart Docker container: {error}") from error
    return restarted


@app.delete("/api/docker/containers/{container_id}")
async def remove_imported_docker_container(container_id: str) -> dict:
    try:
        await asyncio.to_thread(
            docker_projects.remove_imported_container,
            container_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=500, detail=f"Could not remove imported Docker container: {error}") from error
    return {"message": "Removed container from Pi Console. The container and its data were left untouched."}


@app.post("/api/docker/projects", status_code=201)
async def add_docker_project(project: DockerProjectInput) -> dict:
    try:
        created = await asyncio.to_thread(
            docker_projects.add_project,
            project.name,
            project.compose_yaml,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except DockerConfigurationError as error:
        raise HTTPException(status_code=400, detail=f"Invalid Docker Compose configuration: {error}") from error
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not start Docker project: {error}") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not save Docker project: {error}") from error
    return created


@app.post("/api/docker/projects/{project_name}/restart")
async def restart_docker_project(project_name: str) -> dict:
    try:
        restarted = await asyncio.to_thread(docker_projects.restart_project, project_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except DockerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DockerError as error:
        raise HTTPException(status_code=502, detail=f"Could not restart Docker project: {error}") from error
    return restarted


@app.delete("/api/docker/projects/{project_name}")
async def remove_docker_project(project_name: str) -> dict:
    try:
        await asyncio.to_thread(docker_projects.remove_project, project_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not remove Docker project entry: {error}") from error
    return {"message": f"Removed {project_name} from Pi Console. Its containers and data were left untouched."}


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


@app.put("/api/programs/{program_id}")
async def update_program(program_id: str, program: ProgramEditInput) -> dict:
    try:
        return manager.update_program(program_id, program.name, program.url)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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


@app.post("/api/services", status_code=201)
async def add_service(service: ServiceInput) -> dict:
    try:
        return await asyncio.to_thread(services.add, service.unit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/services/available")
async def available_running_services() -> dict:
    try:
        available = await asyncio.to_thread(services.available_running_services)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"services": available}


@app.delete("/api/services/{unit}")
async def remove_service(unit: str) -> dict:
    try:
        await asyncio.to_thread(services.remove, unit)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"message": "Service removed from the list. It was left unchanged."}


@app.post("/api/services/{unit}/{action}")
async def control_service(unit: str, action: str) -> dict:
    try:
        return await asyncio.to_thread(services.control, unit, action)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/ip")
async def global_ip() -> dict:
    try:
        return {"ip": await asyncio.to_thread(get_global_ip)}
    except Exception as error:
        raise HTTPException(status_code=502, detail="Could not determine global IP") from error


def get_latest_release_tag() -> str:
    request = URLRequest(
        GITHUB_RELEASES_URL,
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

    if not isinstance(release, list):
        raise HTTPException(status_code=502, detail="GitHub returned invalid release data")
    published_releases = [
        item for item in release
        if isinstance(item, dict) and not item.get("draft", False)
    ]
    if not published_releases:
        raise HTTPException(status_code=404, detail="No published GitHub release is available")
    published_releases.sort(
        key=lambda item: item.get("published_at") or item.get("created_at") or "",
        reverse=True,
    )
    tag = published_releases[0].get("tag_name")
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
