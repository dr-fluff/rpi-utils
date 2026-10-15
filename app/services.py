import json
import os
import re
import subprocess
from pathlib import Path


_UNIT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,255}$")


def config_path() -> Path:
    configured = Path.home() / ".config" / "rpi-utils" / "services.json"
    return Path(os.environ.get("RPI_UTILS_SERVICES_FILE", configured))


class ServiceManager:
    """Persist and control explicitly registered systemd service units."""

    def __init__(self) -> None:
        self.path = config_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save([])

    def _load(self) -> list[str]:
        try:
            services = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read service configuration: {error}") from error
        if not isinstance(services, list) or not all(isinstance(unit, str) for unit in services):
            raise RuntimeError("Service configuration must be a JSON list of unit names")
        return services

    def _save(self, services: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(services, indent=2) + "\n")
        temporary.replace(self.path)

    @staticmethod
    def normalize_unit(unit: str) -> str:
        value = unit.strip()
        if re.search(r"\.(?:socket|target|timer|path|mount|automount|slice|scope)$", value):
            raise ValueError("Only .service units can be managed")
        if not value.endswith(".service"):
            value = f"{value}.service"
        if not _UNIT_NAME.fullmatch(value):
            raise ValueError("Service name must be a valid systemd unit name")
        if not value.endswith(".service"):
            raise ValueError("Only .service units can be managed")
        return value

    def add(self, unit: str) -> dict:
        normalized = self.normalize_unit(unit)
        services = self._load()
        if normalized in services:
            raise ValueError("Service is already in the list")
        services.append(normalized)
        self._save(services)
        return self.status(normalized)

    def remove(self, unit: str) -> None:
        normalized = self.normalize_unit(unit)
        services = self._load()
        if normalized not in services:
            raise KeyError("Service not found")
        self._save([item for item in services if item != normalized])

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["systemctl", "--no-pager", *args],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError("systemctl is not available on this device") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("systemctl timed out") from error

    def status(self, unit: str) -> dict:
        normalized = self.normalize_unit(unit)
        result = self._run("show", normalized, "--property=LoadState,ActiveState,SubState,Description")
        properties = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key] = value
        active_state = properties.get("ActiveState", "unknown")
        return {
            "unit": normalized,
            "description": properties.get("Description") or normalized,
            "status": active_state,
            "sub_status": properties.get("SubState", "unknown"),
            "loaded": properties.get("LoadState") == "loaded",
            "running": active_state == "active",
            "error": (result.stderr.strip() or None) if result.returncode and not properties else None,
        }

    def list_services(self) -> list[dict]:
        return [self.status(unit) for unit in self._load()]

    def available_running_services(self) -> list[dict]:
        result = self._run(
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--no-pager",
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "Could not list running services"
            raise RuntimeError(detail)
        registered = set(self._load())
        available = []
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=4)
            if len(fields) < 4:
                continue
            unit = fields[0]
            try:
                unit = self.normalize_unit(unit)
            except ValueError:
                continue
            if unit in registered:
                continue
            available.append({
                "unit": unit,
                "description": fields[4] if len(fields) > 4 else unit,
                "status": fields[3],
            })
        return available

    def control(self, unit: str, action: str) -> dict:
        normalized = self.normalize_unit(unit)
        if normalized not in self._load():
            raise KeyError("Service not found")
        if action not in {"start", "stop"}:
            raise ValueError("Unsupported service action")
        result = self._run(action, normalized)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or f"Could not {action} service"
            raise RuntimeError(detail)
        return self.status(normalized)
