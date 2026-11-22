"""An unanswered send must not be retried, or the chat gets two copies."""

from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from notifyhub.channels import feishu_app


class ErrorClassificationTests(unittest.TestCase):
    def post(self, side_effect):
        with patch("urllib.request.urlopen", side_effect=side_effect):
            with self.assertRaises(feishu_app.FeishuAppError) as caught:
                feishu_app._submit_json("https://example/x", {"a": 1}, {}, 5)
        return caught.exception

    def test_a_timeout_is_ambiguous_because_the_message_may_have_landed(self):
        error = self.post(TimeoutError("timed out"))
        self.assertIsInstance(error, feishu_app.AmbiguousSendError)

    def test_a_dropped_connection_is_ambiguous(self):
        error = self.post(urllib.error.URLError("UNEXPECTED_EOF_WHILE_READING"))
        self.assertIsInstance(error, feishu_app.AmbiguousSendError)

    def test_a_rejection_is_definite_so_retrying_is_safe(self):
        http_error = urllib.error.HTTPError(
            "https://example/x", 400, "bad", {}, MagicMock(read=lambda: b'{"code":1}')
        )
        error = self.post(http_error)
        self.assertNotIsInstance(error, feishu_app.AmbiguousSendError)

    def test_a_business_error_code_is_definite(self):
        response = MagicMock()
        response.read.return_value = b'{"code": 230001, "msg": "invalid receive_id"}'
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: False
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(feishu_app.FeishuAppError) as caught:
                feishu_app._post_json("https://example/x", {}, {}, 5)
        self.assertNotIsInstance(caught.exception, feishu_app.AmbiguousSendError)
