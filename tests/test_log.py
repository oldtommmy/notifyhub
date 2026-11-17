import contextlib
import io
import logging
import tempfile
import unittest
from pathlib import Path

from notifyhub import cli
from notifyhub.config import save_config
from notifyhub.log import get_logger


class LogTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("notifyhub")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def _config(self, directory):
        path = Path(directory) / "config.json"
        save_config(
            {
                "channels": {
                    "feishu": {"enabled": False},
                    "feishu_app": {"enabled": False},
                    "imessage": {"enabled": False},
                },
                "log_path": directory,
            },
            path,
        )
        return path

    def test_unwritable_log_path_falls_back_to_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stderr(io.StringIO()):
                logger = get_logger(directory)
            self.assertEqual(len(logger.handlers), 1)
            self.assertNotIsInstance(logger.handlers[0], logging.FileHandler)

    def test_writable_log_path_uses_rotating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifyhub.log"
            logger = get_logger(str(path))
            try:
                logger.info("hello-%s", "log")
                for handler in logger.handlers:
                    handler.flush()
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()
            self.assertIn("hello-log", path.read_text(encoding="utf-8"))

    def test_dry_run_does_not_need_a_writable_log(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._config(directory)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["--config", str(config_path), "test", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("测试通知", stdout.getvalue())

    def test_test_command_survives_unwritable_log(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._config(directory)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["--config", str(config_path), "test"])
            self.assertEqual(code, 1)
            self.assertIn("没有启用且匹配的通知渠道", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
