import json
import tempfile
import unittest
from pathlib import Path

from notifyhub.installer import has_notifyhub_hook, install_codex, uninstall_codex

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


if __name__ == "__main__":
    unittest.main()
