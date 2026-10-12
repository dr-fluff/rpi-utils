import json
import os
import re
import subprocess
from pathlib import Path

PROJECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
MAX_COMPOSE_BYTES = 65536


class DockerError(RuntimeError):
    pass


class DockerUnavailableError(DockerError):
    pass


class DockerConfigurationError(DockerError):
    pass


def projects_directory() -> Path:
    default = Path.home() / ".config" / "rpi-utils" / "docker-projects"
    path = Path(os.environ.get("RPI_UTILS_DOCKER_PROJECTS_DIR", default)).expanduser().resolve()
    checkout = Path(__file__).resolve().parent.parent
    try:
        path.relative_to(checkout)
    except ValueError:
        return path
    raise ValueError("Docker project files must be outside the Git checkout")


class DockerProjectManager:
    def __init__(self) -> None:
        self.directory = projects_directory()

    @property
    def imported_containers_file(self) -> Path:
        return self.directory / "imported-containers.json"

    def _run_docker(self, *arguments: str) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                ["docker", *arguments],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except FileNotFoundError as error:
            raise DockerUnavailableError("Docker CLI is not installed or is not available to the Pi Console service account.") from error
        except subprocess.TimeoutExpired as error:
            raise DockerError(f"Docker timed out while running {arguments[0]}.") from error
        if result.returncode:
            message = (result.stderr or result.stdout).strip()
            raise DockerError(message or f"Docker {arguments[0]} failed.")
        return result

    def _compose_file(self, name: str) -> Path:
        if not PROJECT_NAME_PATTERN.fullmatch(name):
            raise ValueError("Project name must start with a lowercase letter and contain only lowercase letters, numbers, hyphens, or underscores.")
        return self.directory / name / "compose.yaml"

    def _run(self, name: str, compose_file: Path, *arguments: str) -> subprocess.CompletedProcess:
        command = [
            "docker", "compose", "--project-name", name,
            "--file", str(compose_file), *arguments,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=compose_file.parent,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except FileNotFoundError as error:
            raise DockerUnavailableError("Docker CLI is not installed or is not available to the Pi Console service account.") from error
        except subprocess.TimeoutExpired as error:
            raise DockerError(f"Docker Compose timed out while running {arguments[0]}.") from error
        if result.returncode:
            message = (result.stderr or result.stdout).strip()
            raise DockerError(message or f"Docker Compose {arguments[0]} failed.")
        return result

    def _containers(self, name: str, compose_file: Path) -> list[dict]:
        result = self._run(name, compose_file, "ps", "--format", "json")
        output = result.stdout.strip()
        if not output:
            return []
        try:
            decoded = json.loads(output)
            containers = decoded if isinstance(decoded, list) else [decoded]
        except json.JSONDecodeError:
            try:
                containers = [json.loads(line) for line in output.splitlines() if line.strip()]
            except json.JSONDecodeError as error:
                raise DockerError("Docker Compose returned invalid container status data.") from error
        return [
            {
                "name": item.get("Name", ""),
                "service": item.get("Service", ""),
                "state": item.get("State", ""),
                "status": item.get("Status", ""),
            }
            for item in containers
            if isinstance(item, dict)
        ]

    def list_projects(self) -> list[dict]:
        if not self.directory.exists():
            return []
        projects = []
        for project_directory in sorted(self.directory.iterdir()):
            if not PROJECT_NAME_PATTERN.fullmatch(project_directory.name):
                continue
            compose_file = project_directory / "compose.yaml"
            if not compose_file.is_file():
                continue
            try:
                compose_yaml = compose_file.read_text()
            except OSError as error:
                raise DockerError(f"Could not read {compose_file.name} for {project_directory.name}: {error}") from error
            projects.append({
                "name": project_directory.name,
                "compose_yaml": compose_yaml,
                "containers": self._containers(project_directory.name, compose_file),
            })
        return projects

    def available_containers(self) -> list[dict]:
        result = self._run_docker("ps", "--no-trunc", "--format", "{{json .}}")
        imported_ids = set(self._load_imported_container_ids())
        available = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise DockerError("Docker returned invalid running-container data.") from error
            container_id = item.get("ID")
            if not isinstance(container_id, str) or container_id in imported_ids:
                continue
            available.append({
                "id": container_id,
                "name": item.get("Names", ""),
                "image": item.get("Image", ""),
                "status": item.get("Status", ""),
            })
        return available

    def _load_imported_container_ids(self) -> list[str]:
        path = self.imported_containers_file
        if not path.exists():
            return []
        try:
            container_ids = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise DockerError(f"Could not read imported Docker containers: {error}") from error
        if (
            not isinstance(container_ids, list)
            or any(not isinstance(container_id, str) for container_id in container_ids)
        ):
            raise DockerError("Imported Docker container configuration must be a list of container IDs.")
        return container_ids

    def _save_imported_container_ids(self, container_ids: list[str]) -> None:
        path = self.imported_containers_file
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(container_ids, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)

    @staticmethod
    def _container_details(container: dict) -> dict:
        config = container.get("Config") or {}
        host_config = container.get("HostConfig") or {}
        state = container.get("State") or {}
        network_settings = container.get("NetworkSettings") or {}
        ports = host_config.get("PortBindings") or {}
        networks = network_settings.get("Networks") or {}
        return {
            "id": container.get("Id", ""),
            "name": str(container.get("Name", "")).lstrip("/"),
            "image": config.get("Image", ""),
            "command": config.get("Cmd") or [],
            "state": state.get("Status", "unknown"),
            "status": (state.get("Health") or {}).get("Status", "") or state.get("Error", ""),
            "started_at": state.get("StartedAt", ""),
            "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name", "no"),
            "ports": ports,
            "mounts": [
                {
                    "source": mount.get("Source", ""),
                    "destination": mount.get("Destination", ""),
                    "mode": mount.get("Mode", ""),
                    "rw": mount.get("RW", False),
                }
                for mount in container.get("Mounts", [])
            ],
            "networks": list(networks),
        }

    def _inspect_container(self, container_id: str) -> dict | None:
        try:
            result = self._run_docker("inspect", container_id)
        except DockerError as error:
            if "no such object" in str(error).lower():
                return None
            raise
        try:
            inspected = json.loads(result.stdout)
            container = inspected[0]
        except (json.JSONDecodeError, IndexError, TypeError) as error:
            raise DockerError("Docker returned invalid container details.") from error
        return self._container_details(container)

    def list_imported_containers(self) -> list[dict]:
        containers = []
        for container_id in self._load_imported_container_ids():
            details = self._inspect_container(container_id)
            if details is None:
                containers.append({
                    "id": container_id,
                    "name": container_id[:12],
                    "image": "",
                    "state": "missing",
                    "status": "Container no longer exists",
                    "restart_policy": "",
                    "ports": {},
                    "mounts": [],
                    "networks": [],
                    "command": [],
                })
            else:
                containers.append(details)
        return containers

    def import_container(self, container_id: str) -> dict:
        imported_ids = self._load_imported_container_ids()
        if container_id in imported_ids:
            raise ValueError("This container is already imported")
        details = self._inspect_container(container_id)
        if details is None:
            raise KeyError("Running Docker container not found")
        if details["state"] != "running":
            raise ValueError("Only running Docker containers can be imported")
        imported_ids.append(details["id"])
        self._save_imported_container_ids(imported_ids)
        return details

    def restart_imported_container(self, container_id: str) -> dict:
        if container_id not in self._load_imported_container_ids():
            raise KeyError("Imported Docker container not found")
        self._run_docker("restart", container_id)
        details = self._inspect_container(container_id)
        if details is None:
            raise DockerError("Docker container disappeared after restart.")
        return details

    def remove_imported_container(self, container_id: str) -> None:
        imported_ids = self._load_imported_container_ids()
        if container_id not in imported_ids:
            raise KeyError("Imported Docker container not found")
        imported_ids.remove(container_id)
        self._save_imported_container_ids(imported_ids)

    def add_project(self, name: str, compose_yaml: str) -> dict:
        compose_file = self._compose_file(name)
        if not compose_yaml.strip():
            raise ValueError("Compose YAML cannot be empty")
        if len(compose_yaml.encode("utf-8")) > MAX_COMPOSE_BYTES:
            raise ValueError(f"Compose YAML must be {MAX_COMPOSE_BYTES} bytes or smaller")
        if compose_file.exists():
            raise ValueError(f"A project named {name} is already enrolled")

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        compose_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        compose_file.parent.chmod(0o700)
        try:
            compose_file.write_text(compose_yaml)
            compose_file.chmod(0o600)
            self._run(name, compose_file, "config", "--quiet")
        except DockerError as error:
            compose_file.unlink(missing_ok=True)
            if isinstance(error, DockerUnavailableError):
                raise
            raise DockerConfigurationError(str(error)) from error
        except OSError:
            compose_file.unlink(missing_ok=True)
            raise

        self._run(name, compose_file, "up", "--detach")
        return {
            "name": name,
            "compose_yaml": compose_yaml,
            "containers": self._containers(name, compose_file),
        }

    def restart_project(self, name: str) -> dict:
        compose_file = self._compose_file(name)
        if not compose_file.is_file():
            raise KeyError(f"Docker project not found: {name}")
        self._run(name, compose_file, "restart")
        return {
            "name": name,
            "compose_yaml": compose_file.read_text(),
            "containers": self._containers(name, compose_file),
        }

    def remove_project(self, name: str) -> None:
        compose_file = self._compose_file(name)
        if not compose_file.is_file():
            raise KeyError(f"Docker project not found: {name}")
        compose_file.unlink()
        try:
            compose_file.parent.rmdir()
        except OSError:
            pass
