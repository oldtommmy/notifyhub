import hashlib
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from notifyhub import updates


class SkillUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills/notifyhub"
        self.cache = self.root / "check.json"
        updates.install_local(self.skill)

    def release(self, tag):
        stream = BytesIO()
        with ZipFile(stream, "w") as package:
            for relative in updates.REQUIRED:
                contents = (self.skill / relative).read_text(encoding="utf-8")
                if relative.endswith("__init__.py"):
                    contents = f'__version__ = "{tag.lstrip("v")}"'
                package.writestr("notifyhub/" + relative, contents)
        return stream.getvalue()

    def test_only_complete_stable_skill_releases_are_offered_and_cached(self):
        assets = [{"name": updates.BUNDLE}, {"name": updates.CHECKSUM}]
        listing = [
            {"tag_name": "v12.0.0", "draft": True, "assets": assets},
            {"tag_name": "v11.0.0", "prerelease": True, "assets": assets},
            {"tag_name": "v10.0.0", "assets": [{"name": "dsh-notifyhub.tgz"}]},
            {"tag_name": "v2.0.0", "assets": assets},
            {"tag_name": "v1.12.0", "assets": assets},
        ]
        with patch.object(updates, "_fetch", return_value=json.dumps(listing).encode()) as fetch:
            result = updates.check(root=self.skill, cache_path=self.cache)
            self.assertEqual(result["latest"], "v2.0.0")
            self.assertTrue(result["can_apply"])
            self.assertTrue(result["available"])
            self.assertEqual(updates.check(root=self.skill, cache_path=self.cache), result)
            fetch.assert_called_once()

    def test_update_checks_digest_backs_up_and_preserves_local_files(self):
        extra = self.skill / "local.txt"
        extra.write_text("keep", encoding="utf-8")
        archive = self.release("v2.0.0")
        checksum = f"{hashlib.sha256(archive).hexdigest()}  {updates.BUNDLE}".encode()
        with patch.object(updates, "_fetch", side_effect=[checksum, archive]):
            result = updates.apply("v2.0.0", self.skill)
        self.assertEqual(result["version"], "2.0.0")
        self.assertEqual(updates._content_version(self.skill), "2.0.0")
        self.assertEqual(updates._content_version(Path(result["backup"])), updates.__version__)
        self.assertEqual(extra.read_text(encoding="utf-8"), "keep")

    def test_wrong_hash_zip_escape_or_checkout_leaves_current_install_intact(self):
        before = (self.skill / "SKILL.md").read_bytes()
        checksum = f"{'0' * 64}  {updates.BUNDLE}".encode()
        with patch.object(updates, "_fetch", side_effect=[checksum, self.release("v2.0.0")]):
            with self.assertRaisesRegex(ValueError, "校验失败"):
                updates.apply("v2.0.0", self.skill)
        archive = BytesIO()
        with ZipFile(archive, "w") as package:
            package.writestr("notifyhub/scripts/../../unexpected.py", "do not unpack")
        bundle = archive.getvalue()
        checksum = f"{hashlib.sha256(bundle).hexdigest()}  {updates.BUNDLE}".encode()
        with patch.object(updates, "_fetch", side_effect=[checksum, bundle]):
            with self.assertRaisesRegex(ValueError, "无效路径"):
                updates.apply("v2.0.0", self.skill)
        (self.skill / ".git").touch()
        with patch.object(updates, "_fetch") as fetch:
            with self.assertRaisesRegex(ValueError, "源码检出"):
                updates.apply("v2.0.0", self.skill)
            fetch.assert_not_called()
        self.assertEqual((self.skill / "SKILL.md").read_bytes(), before)
