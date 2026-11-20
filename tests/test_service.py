"""Tests for the launchd service wrapper (no launchctl calls)."""

from __future__ import annotations

import plistlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from notifyhub import service


class BuildPlistTests(unittest.TestCase):
    def make(self, **overrides):
        kwargs = dict(
            python="/usr/bin/python3",
            script="/repo/notifyhub/scripts/notifyhub.py",
            config="/repo/config.json",
            workspace="/repo",
            log_path="/tmp/bridge.out",
        )
        kwargs.update(overrides)
        with patch.dict("os.environ", {}, clear=True):
            return service.build_plist(**kwargs)

    def test_runs_the_bridge_in_the_foreground_under_launchd(self):
        plist = self.make()
        self.assertEqual(plist["Label"], "com.notifyhub.bridge")
        self.assertIn("--config", plist["ProgramArguments"])
        self.assertEqual(
            plist["ProgramArguments"][-3:], ["bridge", "start", "--force"]
        )

    def test_restarts_after_a_crash_but_not_after_a_clean_stop(self):
        # KeepAlive must not fight an operator who ran `service uninstall`.
        self.assertEqual(self.make()["KeepAlive"], {"SuccessfulExit": False})

    def test_keeps_a_working_directory_launchd_can_use(self):
        self.assertEqual(self.make()["WorkingDirectory"], "/repo")

    def test_names_the_credential_variables_without_leaking_values(self):
        plist = self.make()
        env = plist["EnvironmentVariables"]
        self.assertIn("QWEN_API_KEY", env)
        self.assertIn("NOTIFYHUB_FEISHU_APP_SECRET", env)
        self.assertEqual(env["QWEN_API_KEY"], "")

    def test_freezes_values_it_is_given(self):
        plist = self.make(env={"QWEN_API_KEY": "sk-test"})
        self.assertEqual(plist["EnvironmentVariables"]["QWEN_API_KEY"], "sk-test")


class WritePlistTests(unittest.TestCase):
    def test_writes_a_private_readable_agent_file(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "com.notifyhub.bridge.plist"
            plist = {"Label": "com.notifyhub.bridge", "ProgramArguments": ["x"]}
            service.write_plist(plist, target)
            self.assertEqual(plistlib.loads(target.read_bytes()), plist)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_round_trips_through_the_real_plist_parser(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.plist"
            service.write_plist({"Label": "l", "KeepAlive": {"SuccessfulExit": False}}, target)
            loaded = plistlib.loads(target.read_bytes())
            self.assertEqual(loaded["KeepAlive"], {"SuccessfulExit": False})


class PlatformTests(unittest.TestCase):
    def test_reports_a_reason_instead_of_crashing_off_macos(self):
        with patch.object(service, "_is_macos", return_value=False):
            info = service.status()
        self.assertFalse(info["loaded"])
        self.assertIn("macOS", info["reason"])

    def test_install_refuses_off_macos(self):
        with patch.object(service, "_is_macos", return_value=False):
            with self.assertRaises(SystemExit):
                service.install({"Label": "x"})

    def test_uninstall_is_idempotent_when_nothing_is_installed(self):
        with TemporaryDirectory() as tmp, patch.object(
            service, "_is_macos", return_value=True
        ), patch.object(service, "plist_path", return_value=Path(tmp) / "x.plist"), patch.object(
            service, "_run"
        ) as run:
            run.return_value = type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
            result = service.uninstall("x")
        self.assertFalse(result["removed"])
        self.assertFalse(result["unloaded"])


class StatusParsingTests(unittest.TestCase):
    def test_reads_pid_and_last_exit_from_launchctl(self):
        output = '{\n\t"PID" = 28325;\n\t"LastExitStatus" = 0;\n\t"Label" = "x";\n}'
        fake = type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})()
        with patch.object(service, "_is_macos", return_value=True), patch.object(
            service, "_run", return_value=fake
        ):
            info = service.status("x")
        self.assertTrue(info["loaded"])
        self.assertEqual(info["pid"], "28325")
        self.assertEqual(info["last_exit"], "0")

    def test_reports_not_loaded_when_launchctl_does_not_know_it(self):
        fake = type("R", (), {"returncode": 113, "stdout": "", "stderr": "not found"})()
        with patch.object(service, "_is_macos", return_value=True), patch.object(
            service, "_run", return_value=fake
        ):
            info = service.status("x")
        self.assertFalse(info["loaded"])
