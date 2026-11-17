import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from notifyhub.installer import (
    has_hooks,
    has_notifyhub_hook,
    install_codex,
    install_hooks,
    target_path,
    uninstall_codex,
    uninstall_hooks,
)

COMMAND = "/usr/bin/python3 /repo/notifyhub.py hook codex"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / "hooks.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_install_preserves_existing_hooks(self):
        self.path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [{"hooks": [{"type": "command", "command": "/old/notify.sh"}]}],
                        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/guard.sh"}]}],
                    }
                }
            ),
            encoding="utf-8",
        )

        result = install_codex(self.home, ["Stop"], COMMAND)
        data = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "/guard.sh")
        self.assertTrue(result["backup"].exists())
        self.assertTrue(has_notifyhub_hook(self.home, ["Stop"]))

    def test_install_is_idempotent(self):
        install_codex(self.home, ["Stop"], COMMAND)
        install_codex(self.home, ["Stop"], COMMAND)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        ours = [
            handler
            for group in data["hooks"]["Stop"]
            for handler in group["hooks"]
            if "notifyhub" in handler["command"]
        ]
        self.assertEqual(len(ours), 1)

    def test_uninstall_removes_only_ours(self):
        self.path.write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/old/notify.sh"}]}]}}),
            encoding="utf-8",
        )
        install_codex(self.home, ["Stop"], COMMAND)
        result = uninstall_codex(self.home, ["Stop"])
        data = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(result["removed"], 1)
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "/old/notify.sh")
        self.assertFalse(has_notifyhub_hook(self.home, ["Stop"]))

    def test_install_claude_preserves_other_settings(self):
        path = self.home / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/guard.sh"}]}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        command = "/usr/bin/python3 /repo/notifyhub.py hook claude"

        result = install_hooks(path, ["Stop"], command, "claude")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "deepseek-flash")
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "/guard.sh")
        self.assertTrue(has_hooks(path, ["Stop"], "claude"))
        self.assertFalse(has_hooks(path, ["Stop"], "codex"))
        self.assertTrue(result["backup"].exists())

        removed = uninstall_hooks(path, ["Stop"], "claude")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(removed["removed"], 1)
        self.assertNotIn("Stop", data["hooks"])
        self.assertFalse(has_hooks(path, ["Stop"], "claude"))
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "/guard.sh")

    def test_install_is_scoped_by_source(self):
        path = self.home / "settings.json"
        install_hooks(path, ["Stop"], "/usr/bin/python3 /repo/notifyhub.py hook claude", "claude")
        install_hooks(path, ["Stop"], "/usr/bin/python3 /repo/notifyhub.py hook commandcode", "commandcode")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        self.assertTrue(has_hooks(path, ["Stop"], "claude"))
        self.assertTrue(has_hooks(path, ["Stop"], "commandcode"))

        uninstall_hooks(path, ["Stop"], "claude")
        self.assertFalse(has_hooks(path, ["Stop"], "claude"))
        self.assertTrue(has_hooks(path, ["Stop"], "commandcode"))

    def test_target_path_uses_custom_home(self):
        self.assertEqual(target_path("codex", self.home), self.home / ".codex" / "hooks.json")
        self.assertEqual(target_path("claude", self.home), self.home / ".claude" / "settings.json")
        self.assertEqual(
            target_path("commandcode", self.home),
            self.home / ".commandcode" / "settings.json",
        )

    @unittest.skipUnless(os.name == "posix", "mode bits are POSIX-only")
    def test_install_preserves_private_file_mode(self):
        path = self.home / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"hooks": {}}', encoding="utf-8")
        os.chmod(path, 0o600)

        result = install_hooks(path, ["Stop"], "/repo/notifyhub.py hook claude", "claude")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(result["backup"].stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
