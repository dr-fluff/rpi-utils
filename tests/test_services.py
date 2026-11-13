import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app, service_units, services
from app.services import ServiceManager, ServiceUnitManager


class ServiceManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        path = os.path.join(self.temporary_directory.name, "services.json")
        self.environment = patch.dict(os.environ, {"RPI_UTILS_SERVICES_FILE": path})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = ServiceManager()

    def test_add_normalizes_service_suffix_and_reports_status(self):
        result = SimpleNamespace(
            returncode=0,
            stdout="LoadState=loaded\nActiveState=active\nSubState=running\nDescription=SSH server\n",
            stderr="",
        )
        with patch.object(self.manager, "_run", return_value=result):
            service = self.manager.add("ssh")

        self.assertEqual(service["unit"], "ssh.service")
        self.assertTrue(service["running"])
        self.assertEqual(self.manager._load(), ["ssh.service"])

    def test_rejects_shell_like_or_non_service_unit_names(self):
        for unit in ("ssh.service; reboot", "../ssh.service", "ssh.socket"):
            with self.assertRaises(ValueError):
                self.manager.add(unit)

    def test_control_runs_registered_service_and_returns_fresh_status(self):
        self.manager._save(["ssh.service"])
        started = SimpleNamespace(returncode=0, stdout="", stderr="")
        status = SimpleNamespace(
            returncode=0,
            stdout="LoadState=loaded\nActiveState=active\nSubState=running\nDescription=SSH server\n",
            stderr="",
        )
        with patch.object(self.manager, "_run", side_effect=[started, status]) as run:
            service = self.manager.control("ssh.service", "start")

        self.assertTrue(service["running"])
        self.assertEqual(run.call_args_list[0].args, ("start", "ssh.service"))

    def test_status_output_runs_systemctl_for_registered_service(self):
        self.manager._save(["ssh.service"])
        result = SimpleNamespace(
            returncode=3,
            stdout="○ ssh.service - OpenBSD Secure Shell server\n   Active: inactive (dead)",
            stderr="",
        )

        with patch.object(self.manager, "_run", return_value=result) as run:
            output = self.manager.status_output("ssh")

        self.assertIn("ssh.service", output)
        run.assert_called_once_with("status", "--full", "ssh.service")

    def test_status_output_rejects_unregistered_units(self):
        with patch.object(self.manager, "_run") as run:
            with self.assertRaises(KeyError):
                self.manager.status_output("ssh")

        run.assert_not_called()

    def test_remove_does_not_call_systemctl(self):
        self.manager._save(["ssh.service"])

        self.manager.remove("ssh.service")

        self.assertEqual(self.manager._load(), [])


class ServiceUnitManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.scripts_directory = Path(self.temporary_directory.name) / "scripts"
        self.service_directory = self.scripts_directory / "service"
        self.service_directory.mkdir(parents=True)
        self.manager = ServiceUnitManager(
            lambda: self.scripts_directory,
            helper_path="/test/rpi-utils-service-manager",
        )

    def test_list_combines_repo_files_with_installed_units(self):
        (self.service_directory / "power-monitor.service").write_text("[Service]\n")
        (self.service_directory / "ignored.txt").write_text("not a unit\n")
        result = SimpleNamespace(returncode=0, stdout='["old-unit.service"]', stderr="")

        with (
            patch("app.services.sys.platform", "linux"),
            patch("app.services.subprocess.run", return_value=result) as run,
        ):
            services = self.manager.list_available()

        self.assertEqual(
            services,
            [
                {"unit": "old-unit.service", "installed": True, "source_available": False},
                {"unit": "power-monitor.service", "installed": False, "source_available": True},
            ],
        )
        self.assertEqual(
            run.call_args.args[0],
            ["sudo", "-n", "/test/rpi-utils-service-manager", "list"],
        )

    def test_install_dispatches_only_a_valid_repo_service_file(self):
        (self.service_directory / "power-monitor.service").write_text("[Service]\n")
        result = SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch("app.services.sys.platform", "linux"),
            patch("app.services.subprocess.run", return_value=result) as run,
        ):
            unit = self.manager.install("power-monitor.service")

        self.assertEqual(unit, "power-monitor.service")
        self.assertEqual(
            run.call_args.args[0],
            [
                "sudo",
                "-n",
                "/test/rpi-utils-service-manager",
                "install",
                str(self.service_directory),
                "power-monitor.service",
            ],
        )

    def test_install_rejects_missing_file_and_non_service_names(self):
        with self.assertRaises(KeyError):
            self.manager.install("missing.service")
        with self.assertRaises(ValueError):
            self.manager.install("../power-monitor.service")

    def test_service_management_reports_linux_requirement_without_calling_sudo(self):
        with (
            patch("app.services.sys.platform", "darwin"),
            patch("app.services.subprocess.run") as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "only available on Linux"):
                self.manager.list_available()

        run.assert_not_called()


class ServiceUnitApiTests(unittest.TestCase):
    def setUp(self):
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

    def test_list_repository_service_units(self):
        with patch.object(
            service_units,
            "list_available",
            return_value=[{"unit": "power-monitor.service", "installed": False, "source_available": True}],
        ):
            response = self.client.get("/api/service-units")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["services"][0]["unit"], "power-monitor.service")

    def test_install_repository_service_unit_and_register_it(self):
        service_status = {"unit": "power-monitor.service", "running": True}
        with (
            patch.object(service_units, "install", return_value="power-monitor.service"),
            patch.object(services, "is_registered", return_value=False),
            patch.object(services, "add") as add,
            patch.object(services, "status", return_value=service_status),
        ):
            response = self.client.post("/api/service-units/power-monitor.service")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), service_status)
        add.assert_called_once_with("power-monitor.service")

    def test_uninstall_repository_service_unit_and_forget_it(self):
        with (
            patch.object(service_units, "remove", return_value="power-monitor.service"),
            patch.object(services, "is_registered", return_value=True),
            patch.object(services, "remove") as remove,
        ):
            response = self.client.delete("/api/service-units/power-monitor.service")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["message"],
            "power-monitor.service stopped, disabled, and uninstalled.",
        )
        remove.assert_called_once_with("power-monitor.service")

    def test_service_status_output_endpoint(self):
        with patch.object(services, "status_output", return_value="● ssh.service - SSH server") as status_output:
            response = self.client.get("/api/services/ssh.service/status-output")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "unit": "ssh.service",
            "output": "● ssh.service - SSH server",
        })
        status_output.assert_called_once_with("ssh.service")


class PrivilegedServiceHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.service_directory = self.root / "scripts" / "service"
        self.service_directory.mkdir(parents=True)
        self.systemd_directory = self.root / "systemd"
        self.systemd_directory.mkdir()
        self.state_directory = self.root / "state"
        helper_path = Path(__file__).resolve().parents[1] / "scripts" / "rpi-utils-service-manager"
        self.helper = runpy.run_path(str(helper_path))
        helper_globals = self.helper["install"].__globals__
        helper_globals["SYSTEMD_DIRECTORY"] = self.systemd_directory
        helper_globals["STATE_DIRECTORY"] = self.state_directory
        helper_globals["STATE_FILE"] = self.state_directory / "managed-services.json"
        self.systemctl = unittest.mock.Mock()
        helper_globals["systemctl"] = self.systemctl
        self.account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))

    def test_installs_enables_and_uninstalls_only_its_unit(self):
        unit = "power-monitor.service"
        content = "[Unit]\nDescription=Power monitor\n"
        (self.service_directory / unit).write_text(content)

        self.helper["install"](self.account, str(self.service_directory), unit)

        installed_file = self.systemd_directory / unit
        self.assertEqual(installed_file.read_text(), content)
        self.assertEqual(self.systemctl.call_args_list[0].args, ("daemon-reload",))
        self.assertEqual(self.systemctl.call_args_list[1].args, ("enable", "--now", unit))

        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            self.helper["remove"](unit)

        self.assertFalse(installed_file.exists())
        self.assertEqual(json.loads((self.state_directory / "managed-services.json").read_text()), {})
        self.assertEqual(self.systemctl.call_args_list[-2].args, ("disable", "--now", unit))
        self.assertEqual(self.systemctl.call_args_list[-1].args, ("daemon-reload",))

    def test_refuses_to_remove_unit_modified_outside_pi_console(self):
        unit = "power-monitor.service"
        (self.service_directory / unit).write_text("[Service]\n")
        self.helper["install"](self.account, str(self.service_directory), unit)
        installed_file = self.systemd_directory / unit
        installed_file.write_text("[Service]\nExecStart=/bin/false\n")

        with self.assertRaisesRegex(RuntimeError, "modified outside Pi Console"):
            self.helper["remove"](unit)

        self.assertTrue(installed_file.exists())

    def test_adopts_exact_existing_repo_unit_but_rejects_conflicting_unit(self):
        unit = "power-monitor.service"
        content = "[Service]\nExecStart=/usr/bin/python3\n"
        (self.service_directory / unit).write_text(content)
        installed_file = self.systemd_directory / unit
        installed_file.write_text(content)

        self.helper["install"](self.account, str(self.service_directory), unit)

        self.assertEqual(installed_file.read_text(), content)
        self.assertIn(unit, json.loads((self.state_directory / "managed-services.json").read_text()))

        conflicting_unit = "router-watchdog.service"
        (self.service_directory / conflicting_unit).write_text("[Service]\nExecStart=/usr/bin/safe\n")
        conflicting_file = self.systemd_directory / conflicting_unit
        conflicting_file.write_text("[Service]\nExecStart=/usr/bin/other\n")
        with self.assertRaisesRegex(RuntimeError, "different system service"):
            self.helper["install"](self.account, str(self.service_directory), conflicting_unit)
        self.assertEqual(conflicting_file.read_text(), "[Service]\nExecStart=/usr/bin/other\n")
