import unittest

from notifyhub.channels.feishu import build_payload, sign
from notifyhub.events import Notification


class FeishuTests(unittest.TestCase):
    def test_signature_vector(self):
        self.assertEqual(
            sign("1599360473", "test-secret"),
            "wSds2BzzFIIGf/WrhUO+NI1q/9j+FRJd3JNHKAq0NZY=",
        )

    def test_card_payload(self):
        notification = Notification(source="codex", event="Stop", status_text="✅ 完成", project="demo")
        payload = build_payload(notification, {"secret": "", "message_type": "card"})
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["template"], "blue")
        self.assertIn("✅ 完成", payload["card"]["header"]["title"]["content"])

    def test_text_payload_and_signature(self):
        notification = Notification(source="codex", event="Stop", status_text="✅ 完成", project="demo")
        payload = build_payload(notification, {"secret": "s", "message_type": "text"})
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("sign", payload)
        self.assertIn("timestamp", payload)
        self.assertIn("[CODEX] ✅ 完成", payload["content"]["text"])


if __name__ == "__main__":
    unittest.main()
