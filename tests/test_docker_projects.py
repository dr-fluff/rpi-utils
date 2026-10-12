import json
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app, docker_projects


class DockerProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.original_directory = docker_projects.directory
        docker_projects.directory = Path(self.temporary_directory.name) / "docker-projects"
        self.addCleanup(setattr, docker_projects, "directory", self.original_directory)
        self.environment = patch.dict(
            os.environ,
            {
                "RPI_UTILS_WEB_PASSWORD": "test-dashboard-password-long",
                "RPI_UTILS_ALLOWED_HOSTS": "pi-console.home.arpa",
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app, base_url="https://pi-console.home.arpa")
        response = self.client.post(
            "/api/auth/login",
            json={"password": "test-dashboard-password-long"},
        )
        self.assertEqual(response.status_code, 200)

    @staticmethod
    def docker_result(stdout="", stderr="", returncode=0):
        return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_create_lists_restarts_and_forgets_compose_project_without_stopping_containers(self):
        compose_yaml = "services:\n  app:\n    image: example/app:latest\n"
        running_container = json.dumps([
            {"Name": "demo-app-1", "Service": "app", "State": "running", "Status": "Up"},
        ])
        with patch(
            "app.docker_projects.subprocess.run",
            side_effect=[
                self.docker_result(),  # Compose config validation
                self.docker_result(),  # Compose up
                self.docker_result(stdout=running_container),  # Status after create
                self.docker_result(stdout=running_container),  # Status listing
                self.docker_result(),  # Compose restart
                self.docker_result(stdout=running_container),  # Status after restart
            ],
        ) as run:
            created = self.client.post(
                "/api/docker/projects",
                json={"name": "demo", "compose_yaml": compose_yaml},
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["containers"][0]["state"], "running")
            compose_file = docker_projects.directory / "demo" / "compose.yaml"
            self.assertEqual(compose_file.read_text(), compose_yaml)
            self.assertEqual(compose_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual((compose_file.parent.stat().st_mode & 0o777), 0o700)

            listed = self.client.get("/api/docker/projects")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["projects"][0]["name"], "demo")

            restarted = self.client.post("/api/docker/projects/demo/restart")
            self.assertEqual(restarted.status_code, 200)
            self.assertEqual(restarted.json()["containers"][0]["service"], "app")

            removed = self.client.delete("/api/docker/projects/demo")
            self.assertEqual(removed.status_code, 200)
            self.assertIn("left untouched", removed.json()["message"])
            self.assertFalse(compose_file.exists())
            self.assertEqual(run.call_count, 6)
            self.assertEqual(run.call_args_list[1].args[0][-2:], ["up", "--detach"])
            self.assertEqual(run.call_args_list[4].args[0][-1], "restart")

    def test_invalid_compose_configuration_is_rejected_without_enrolling_project(self):
        with patch(
            "app.docker_projects.subprocess.run",
            return_value=self.docker_result(stderr="invalid compose file", returncode=1),
        ) as run:
            response = self.client.post(
                "/api/docker/projects",
                json={"name": "demo", "compose_yaml": "not: valid: compose: yaml"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid compose file", response.json()["detail"])
        self.assertFalse((docker_projects.directory / "demo" / "compose.yaml").exists())
        run.assert_called_once()

    def test_project_names_are_validated_before_running_docker(self):
        with patch("app.docker_projects.subprocess.run") as run:
            response = self.client.post(
                "/api/docker/projects",
                json={"name": "../outside", "compose_yaml": "services: {}"},
            )

        self.assertEqual(response.status_code, 400)
        run.assert_not_called()

    def test_removing_unknown_project_returns_not_found(self):
        response = self.client.delete("/api/docker/projects/missing")

        self.assertEqual(response.status_code, 404)

    @staticmethod
    def inspected_container(container_id="a" * 64):
        return json.dumps([{
            "Id": container_id,
            "Name": "/existing-app",
            "Config": {
                "Image": "example/app:stable",
                "Cmd": ["./start", "--mode", "server"],
                "Env": ["PASSWORD=never-return-this"],
            },
            "State": {
                "Status": "running",
                "StartedAt": "2026-10-12T12:00:00Z",
                "Health": {"Status": "healthy"},
            },
            "HostConfig": {
                "RestartPolicy": {"Name": "unless-stopped"},
                "PortBindings": {"8080/tcp": [{"HostPort": "8080"}]},
            },
            "Mounts": [{
                "Source": "/srv/app-data",
                "Destination": "/data",
                "Mode": "rw",
                "RW": True,
            }],
            "NetworkSettings": {"Networks": {"bridge": {}}},
        }])

    def test_running_container_can_be_imported_managed_and_forgotten_without_stopping(self):
        container_id = "a" * 64
        listed_running = json.dumps({
            "ID": container_id,
            "Names": "existing-app",
            "Image": "example/app:stable",
            "Status": "Up 3 hours",
        })
        inspect = self.inspected_container(container_id)
        with patch(
            "app.docker_projects.subprocess.run",
            side_effect=[
                self.docker_result(stdout=f"{listed_running}\n"),  # Available to import
                self.docker_result(stdout=inspect),  # Import details
                self.docker_result(stdout=f"{listed_running}\n"),  # Excludes imported container
                self.docker_result(stdout=inspect),  # Imported status
                self.docker_result(),  # Restart
                self.docker_result(stdout=inspect),  # Status after restart
            ],
        ) as run:
            available = self.client.get("/api/docker/containers/available")
            self.assertEqual(available.status_code, 200)
            self.assertEqual(available.json()["containers"][0]["name"], "existing-app")

            imported = self.client.post(
                "/api/docker/containers/import",
                json={"container_id": container_id},
            )
            self.assertEqual(imported.status_code, 201)
            self.assertEqual(imported.json()["state"], "running")
            self.assertEqual(imported.json()["restart_policy"], "unless-stopped")
            self.assertEqual(imported.json()["mounts"][0]["source"], "/srv/app-data")
            self.assertNotIn("Env", imported.json())

            available = self.client.get("/api/docker/containers/available")
            self.assertEqual(available.json()["containers"], [])
            managed = self.client.get("/api/docker/containers")
            self.assertEqual(managed.json()["containers"][0]["name"], "existing-app")

            restarted = self.client.post(
                f"/api/docker/containers/{container_id}/restart",
            )
            self.assertEqual(restarted.status_code, 200)
            self.assertEqual(run.call_args_list[4].args[0], ["docker", "restart", container_id])

            removed = self.client.delete(f"/api/docker/containers/{container_id}")
            self.assertEqual(removed.status_code, 200)
            self.assertIn("left untouched", removed.json()["message"])
            self.assertEqual(run.call_count, 6)
            self.assertEqual(
                json.loads(docker_projects.imported_containers_file.read_text()),
                [],
            )

    def test_import_rejects_container_that_is_not_running(self):
        container_id = "b" * 64
        stopped = json.loads(self.inspected_container(container_id))
        stopped[0]["State"]["Status"] = "exited"
        with patch(
            "app.docker_projects.subprocess.run",
            return_value=self.docker_result(stdout=json.dumps(stopped)),
        ) as run:
            response = self.client.post(
                "/api/docker/containers/import",
                json={"container_id": container_id},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Only running", response.json()["detail"])
        run.assert_called_once()
        self.assertFalse(docker_projects.imported_containers_file.exists())
