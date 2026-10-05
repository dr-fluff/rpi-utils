import json
import re
import shlex
import subprocess
from pathlib import Path


def config_path() -> Path:
    configured = Path.home() / ".config" / "rpi-utils" / "programs.json"
    import os

    return Path(os.environ.get("RPI_UTILS_PROGRAMS_FILE", configured))


class ProgramManager:
    def __init__(self) -> None:
        self.path = config_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save([])
        self.processes: dict[str, subprocess.Popen] = {}

    def _load(self) -> list[dict]:
        try:
            programs = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read program configuration: {error}") from error
        if not isinstance(programs, list):
            raise RuntimeError("Program configuration must be a JSON list")
        return programs

    def _save(self, programs: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(programs, indent=2) + "\n")
        temporary.replace(self.path)

    def list_programs(self) -> list[dict]:
        result = []
        for program in self._load():
            process = self.processes.get(program["id"])
            running = process is not None and process.poll() is None
            result.append(
                {
                    **program,
                    "running": running,
                    "pid": process.pid if process is not None and running else None,
                }
            )
        return result

    def add_program(self, name: str, command: str, cwd: str | None) -> dict:
        try:
            args = shlex.split(command)
        except ValueError as error:
            raise ValueError(f"Invalid command: {error}") from error
        if not args:
            raise ValueError("Command cannot be empty")
        programs = self._load()
        program_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        if not program_id:
            raise ValueError("Name must contain letters or numbers")
        if any(item["id"] == program_id for item in programs):
            raise ValueError("A program with this name already exists")
        working_dir = str(Path(cwd).expanduser().resolve()) if cwd else None
        if working_dir and not Path(working_dir).is_dir():
            raise ValueError("Working directory does not exist")
        program = {"id": program_id, "name": name.strip(), "command": args, "cwd": working_dir}
        programs.append(program)
        self._save(programs)
        return {**program, "running": False, "pid": None}

    def start(self, program_id: str) -> dict:
        program = next((item for item in self._load() if item["id"] == program_id), None)
        if program is None:
            raise KeyError("Program not found")
        process = self.processes.get(program_id)
        if process is not None and process.poll() is None:
            raise RuntimeError("Program is already running")
        try:
            process = subprocess.Popen(program["command"], cwd=program["cwd"] or None)
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Could not start program: {error}") from error
        self.processes[program_id] = process
        return {"id": program_id, "running": True, "pid": process.pid}

    def stop(self, program_id: str) -> dict:
        if not any(item["id"] == program_id for item in self._load()):
            raise KeyError("Program not found")
        process = self.processes.get(program_id)
        if process is None or process.poll() is not None:
            return {"id": program_id, "running": False}
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        return {"id": program_id, "running": False}