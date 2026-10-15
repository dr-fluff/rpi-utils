import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import ServiceManager


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

    def test_remove_does_not_call_systemctl(self):
        self.manager._save(["ssh.service"])

        self.manager.remove("ssh.service")

        self.assertEqual(self.manager._load(), [])
