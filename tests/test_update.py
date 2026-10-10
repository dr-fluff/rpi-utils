import asyncio
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

from fastapi import HTTPException

from app.main import get_latest_release_tag, update


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        (self.root / ".git").mkdir()
        pip = self.root / ".venv" / "bin" / "pip"
        pip.parent.mkdir(parents=True)
        pip.touch()

    def test_update_installs_latest_release_system_upgrade_and_dependencies(self):
        results = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with (
            patch("app.main.ROOT", self.root),
            patch("app.main.get_latest_release_tag", return_value="v1.0.0"),
            patch("app.main.subprocess.run", side_effect=results) as run,
            patch.dict(os.environ, {"INVOCATION_ID": ""}),
        ):
            result = asyncio.run(update())

        self.assertEqual(
            run.call_args_list,
            [
                call(
                    ["git", "-C", str(self.root), "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                ),
                call(
                    ["git", "-C", str(self.root), "check-ref-format", "refs/tags/v1.0.0"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                ),
                call(
                    [
                        "git",
                        "-C",
                        str(self.root),
                        "fetch",
                        "--force",
                        "--no-tags",
                        "origin",
                        "refs/tags/v1.0.0:refs/tags/v1.0.0",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                ),
                call(
                    ["git", "-C", str(self.root), "checkout", "--detach", "refs/tags/v1.0.0"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                ),
                call(
                    ["sudo", "-n", "/usr/local/sbin/rpi-utils-system-upgrade"],
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    check=False,
                ),
                call(
                    [str(self.root / ".venv" / "bin" / "pip"), "install", "--editable", str(self.root)],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                ),
            ],
        )
        self.assertEqual(result["message"], "Installed GitHub release v1.0.0. System package upgrade completed.")
        self.assertFalse(result["restarting"])

    def test_update_reports_system_upgrade_failure(self):
        results = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr="Permission denied"),
        ]
        with (
            patch("app.main.ROOT", self.root),
            patch("app.main.get_latest_release_tag", return_value="v1.0.0"),
            patch("app.main.subprocess.run", side_effect=results),
            patch.dict(os.environ, {"INVOCATION_ID": ""}),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(update())

        self.assertIn("system package upgrade failed: Permission denied", raised.exception.detail)

    def test_update_does_not_fetch_when_no_release_exists(self):
        with (
            patch("app.main.ROOT", self.root),
            patch(
                "app.main.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as run,
            patch(
                "app.main.get_latest_release_tag",
                side_effect=HTTPException(status_code=404, detail="No published GitHub release is available"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(update())

        self.assertEqual(raised.exception.status_code, 404)
        run.assert_called_once()

    def test_get_latest_release_tag_reads_published_tag(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"tag_name":"v1.0.0"}'
        with patch("app.main.urlopen", return_value=response) as open_url:
            tag = get_latest_release_tag()

        self.assertEqual(tag, "v1.0.0")
        self.assertEqual(
            open_url.call_args.args[0].full_url,
            "https://api.github.com/repos/dr-fluff/rpi-utils/releases/latest",
        )

    def test_get_latest_release_tag_reports_when_no_release_exists(self):
        error = HTTPError("https://api.github.com", 404, "Not Found", Message(), None)
        with patch("app.main.urlopen", side_effect=error):
            with self.assertRaises(HTTPException) as raised:
                get_latest_release_tag()

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "No published GitHub release is available")


if __name__ == "__main__":
    unittest.main()
