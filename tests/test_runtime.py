import os
import shlex
import subprocess
import unittest
from pathlib import Path

from notifyhub import dsh_install
from notifyhub.runtime import format_command, hook_command, launcher_path


WINDOWS_PARTS = [
    r"C:\Program Files\Python\python.exe",
    r"C:\a b\notifyhub.py",
    "--config",
    r"C:\x\config.json",
    "hook",
    "codex",
]


class RuntimeTests(unittest.TestCase):
    def test_posix_quoting_uses_shlex(self):
        parts = ["/Applications/Python 3/python3", "/tmp/a b/notifyhub.py"]
        expected = " ".join(shlex.quote(part) for part in parts)
        self.assertEqual(format_command(parts, windows=False), expected)
        self.assertIn("'", expected)

    def test_windows_quoting_uses_list2cmdline(self):
        expected = subprocess.list2cmdline(WINDOWS_PARTS)
        self.assertEqual(format_command(WINDOWS_PARTS, windows=True), expected)
        self.assertNotIn("'", expected)
        self.assertIn('"C:\\Program Files\\Python\\python.exe"', expected)

    def test_hook_command_contains_launcher_and_event(self):
        command = hook_command(Path("/tmp/config.json"))
        self.assertIn("hook codex", command)
        self.assertIn(launcher_path().name, command)
        self.assertIn("/tmp/config.json", command)

    @unittest.skipUnless(os.name == "nt", "windows-only assertion")
    def test_hook_command_windows_has_no_posix_quotes(self):
        command = hook_command(Path(r"C:\work\config.json"))
        self.assertNotIn("'", command)

    def test_dsh_install_delegates_to_runtime(self):
        parts = ["python3", "/tmp/notifyhub.py", "hook", "dsh"]
        self.assertEqual(
            dsh_install.format_command(parts, windows=False),
            format_command(parts, windows=False),
        )
        self.assertEqual(
            dsh_install.format_command(parts, windows=True),
            format_command(parts, windows=True),
        )


if __name__ == "__main__":
    unittest.main()
