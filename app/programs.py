import os
import json
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path


def config_path() -> Path:
    configured = Path.home() / ".config" / "rpi-utils" / "programs.json"
    return Path(os.environ.get("RPI_UTILS_PROGRAMS_FILE", configured))


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _mac_process_info(pid: int) -> dict | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    fields = result.stdout.strip().split(maxsplit=5)
    if len(fields) != 6:
        return None
    try:
        command = shlex.split(fields[5])
    except ValueError:
        return None
    if not command:
        return None
    return {"pid": pid, "command": command, "cwd": None, "start_time": " ".join(fields[:5])}


def _process_info(pid: int, proc_root: Path = Path("/proc")) -> dict | None:
    if _is_macos():
        return _mac_process_info(pid)
    process_path = proc_root / str(pid)
    try:
        command = [part.decode(errors="replace") for part in (process_path / "cmdline").read_bytes().split(b"\0") if part]
        stat = (process_path / "stat").read_text()
        stat_fields = stat[stat.rfind(")") + 1 :].split()
        start_time = int(stat_fields[19])
        cwd = str((process_path / "cwd").resolve())
    except (IndexError, OSError, ValueError):
        return None
    if not command:
        return None
    return {"pid": pid, "command": command, "cwd": cwd, "start_time": start_time}


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
            if process is not None and process.poll() is None:
                pid = process.pid
                running = True
            else:
                attached_pid = program.get("attached_pid")
                attached = _process_info(attached_pid) if isinstance(attached_pid, int) else None
                running = (
                    attached is not None
                    and attached["start_time"] == program.get("attached_start_time")
                )
                pid = attached_pid if running else None
            result.append(
                {
                    **program,
                    "running": running,
                    "pid": pid,
                }
            )
        return result

    def list_running_processes(self) -> list[dict]:
        programs = self._load()
        registered_pids = {
            program["attached_pid"]
            for program in programs
            if isinstance(program.get("attached_pid"), int)
        }
        registered_pids.update(
            process.pid
            for process in self.processes.values()
            if process.poll() is None
        )
        processes = []
        if _is_macos():
            try:
                result = subprocess.run(
                    ["ps", "-axo", "pid=", "-o", "lstart=", "-o", "command="],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(f"Could not list running processes: {error}") from error
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Could not list running processes")
            process_rows = []
            for line in result.stdout.splitlines():
                fields = line.strip().split(maxsplit=6)
                if len(fields) != 7 or not fields[0].isdigit():
                    continue
                try:
                    command = shlex.split(fields[6])
                except ValueError:
                    continue
                if command:
                    process_rows.append({
                        "pid": int(fields[0]),
                        "command": command,
                        "cwd": None,
                        "start_time": " ".join(fields[1:6]),
                    })
        else:
            process_rows = []
            for process_path in Path("/proc").iterdir():
                if process_path.name.isdigit():
                    process = _process_info(int(process_path.name))
                    if process is not None:
                        process_rows.append(process)
        for process in process_rows:
            pid = process["pid"]
            if pid == os.getpid() or pid in registered_pids:
                continue
            processes.append(process)
        return sorted(processes, key=lambda process: process["pid"])

    def add_program(self, name: str, command: str, cwd: str | None) -> dict:
        return self._save_program(name, shlex.split(command), cwd)

    def add_running_process(self, name: str, pid: int) -> dict:
        if pid <= 0 or pid == os.getpid():
            raise ValueError("Invalid process ID")
        process = _process_info(pid)
        if process is None:
            raise ValueError("Process is no longer running or cannot be inspected")
        program = self._save_program(name, process["command"], process["cwd"])
        programs = self._load()
        saved_program = next(item for item in programs if item["id"] == program["id"])
        saved_program["attached_pid"] = process["pid"]
        saved_program["attached_start_time"] = process["start_time"]
        self._save(programs)
        return {**saved_program, "running": True, "pid": process["pid"]}

    def _save_program(self, name: str, command: str | list[str], cwd: str | None) -> dict:
        try:
            args = command.copy() if isinstance(command, list) else shlex.split(command)
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
        programs = self._load()
        program = next((item for item in programs if item["id"] == program_id), None)
        if program is None:
            raise KeyError("Program not found")
        process = self.processes.get(program_id)
        if process is not None and process.poll() is None:
            raise RuntimeError("Program is already running")
        attached_pid = program.get("attached_pid")
        attached = _process_info(attached_pid) if isinstance(attached_pid, int) else None
        if attached is not None and attached["start_time"] == program.get("attached_start_time"):
            raise RuntimeError("Program is already running")
        program.pop("attached_pid", None)
        program.pop("attached_start_time", None)
        self._save(programs)
        try:
            process = subprocess.Popen(program["command"], cwd=program["cwd"] or None)
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Could not start program: {error}") from error
        self.processes[program_id] = process
        return {"id": program_id, "running": True, "pid": process.pid}

    def stop(self, program_id: str) -> dict:
        programs = self._load()
        program = next((item for item in programs if item["id"] == program_id), None)
        if program is None:
            raise KeyError("Program not found")
        process = self.processes.get(program_id)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            return {"id": program_id, "running": False}
        attached_pid = program.get("attached_pid")
        if not isinstance(attached_pid, int):
            program.pop("attached_pid", None)
            program.pop("attached_start_time", None)
            self._save(programs)
            return {"id": program_id, "running": False}
        attached = _process_info(attached_pid)
        if attached is None or attached["start_time"] != program.get("attached_start_time"):
            program.pop("attached_pid", None)
            program.pop("attached_start_time", None)
            self._save(programs)
            return {"id": program_id, "running": False}
        try:
            os.kill(attached_pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = _process_info(attached_pid)
                if current is None or current["start_time"] != program["attached_start_time"]:
                    break
                time.sleep(0.1)
            else:
                current = _process_info(attached_pid)
                if current is not None and current["start_time"] == program["attached_start_time"]:
                    os.kill(attached_pid, signal.SIGKILL)
        except OSError as error:
            raise RuntimeError(f"Could not stop process: {error}") from error
        program.pop("attached_pid", None)
        program.pop("attached_start_time", None)
        self._save(programs)
        return {"id": program_id, "running": False}