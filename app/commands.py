import asyncio
import ipaddress
import socket
import subprocess
import urllib.request
from typing import Any

from fastapi import HTTPException

from app.programs import ProgramManager

COMMANDS = (
    {
        "name": "help",
        "description": "Show available commands.",
        "usage": "",
        "program_action": False,
        "requires_confirmation": False,
    },
    {
        "name": "ip",
        "description": "Show global and local IP addresses.",
        "usage": "",
        "program_action": False,
        "requires_confirmation": False,
    },
    {
        "name": "status",
        "description": "Show running programs.",
        "usage": "",
        "program_action": False,
        "requires_confirmation": False,
    },
    {
        "name": "start",
        "description": "Start a saved program.",
        "usage": "<program-id>",
        "program_action": True,
        "requires_confirmation": False,
    },
    {
        "name": "stop",
        "description": "Stop a saved program.",
        "usage": "<program-id>",
        "program_action": True,
        "requires_confirmation": False,
    },
    {
        "name": "restart",
        "description": "Restart the Raspberry Pi.",
        "usage": "",
        "program_action": False,
        "requires_confirmation": True,
    },
    {
        "name": "update",
        "description": "Install the latest release and system upgrades.",
        "usage": "",
        "program_action": False,
        "requires_confirmation": True,
    },
)
COMMAND_BY_NAME = {command["name"]: command for command in COMMANDS}


class CommandError(Exception):
    pass


def help_message() -> str:
    return "Available commands:\n" + "\n".join(
        f"/{command['name']}{' ' + command['usage'] if command['usage'] else ''} — "
        f"{command['description']}"
        for command in COMMANDS
    )


def get_local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect(("8.8.8.8", 80))
        address = connection.getsockname()[0]
    if not ipaddress.ip_address(address).is_private:
        raise OSError("The selected network address is not private")
    return address


def get_global_ip() -> str:
    with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
        return response.read(64).decode("ascii").strip()


async def run_command(
    name: str,
    manager: ProgramManager,
    args: list[str] | None = None,
) -> dict[str, Any]:
    command = COMMAND_BY_NAME.get(name)
    if command is None:
        raise CommandError(f"Unknown command: {name}")
    arguments = args or []
    usage = command["usage"]
    if (usage and len(arguments) != 1) or (not usage and arguments):
        invocation = f"/{name}{' ' + usage if usage else ''}"
        raise CommandError(f"Usage: {invocation}")

    if name == "help":
        return {"message": help_message()}
    if name == "ip":
        results = await asyncio.gather(
            asyncio.to_thread(get_global_ip),
            asyncio.to_thread(get_local_ip),
            return_exceptions=True,
        )
        global_ip = str(results[0]) if isinstance(results[0], str) else "Unavailable"
        local_ip = str(results[1]) if isinstance(results[1], str) else "Unavailable"
        return {
            "message": f"Global IP: {global_ip}\nLocal IP: {local_ip}",
            "global_ip": global_ip,
            "local_ip": local_ip,
        }
    if name == "status":
        try:
            programs = await asyncio.to_thread(manager.list_programs)
        except (OSError, RuntimeError) as error:
            raise CommandError(f"Could not read program status: {error}") from error
        lines = [f"RUNNING  {program['name']}" for program in programs if program["running"]]
        return {"message": "\n".join(lines) if lines else "No programs are running."}
    if name in {"start", "stop"}:
        try:
            result = await asyncio.to_thread(getattr(manager, name), arguments[0])
        except (KeyError, RuntimeError, ValueError) as error:
            raise CommandError(str(error)) from error
        running = result.get("running", False)
        return {
            "message": f"{arguments[0]}: {'running' if running else 'stopped'}",
            **result,
        }
    if name == "restart":
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "-n", "/usr/local/sbin/rpi-utils-reboot"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CommandError(f"Could not restart the Raspberry Pi: {error}") from error
        if result.returncode:
            output = (result.stdout + result.stderr).strip()
            raise CommandError(f"Could not restart the Raspberry Pi: {output or 'reboot command failed'}")
        return {"message": "Restarting the Raspberry Pi now."}
    if name == "update":
        try:
            from app.main import update as update_device

            result = await update_device()
        except HTTPException as error:
            raise CommandError(f"Update failed: {error.detail}") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise CommandError(f"Update failed: {error}") from error
        return {"message": result["message"], **result}

    raise CommandError(f"Command is not implemented: {name}")
