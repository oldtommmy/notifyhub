import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notifyhub.channels import feishu_app
from notifyhub.config import DEFAULT_CONFIG, deep_merge
from notifyhub.events import Notification
from notifyhub.gui import validate_config


class FeishuAppTests(unittest.TestCase):
    def notification(self):
        return Notification(
            source="codex",
            event="Stop",
            status_text="✅ 完成",
            project="demo",
            last_message="done",
        )

    def test_card_content(self):
        msg_type, content = feishu_app.build_content(self.notification(), {"message_type": "card"})
        self.assertEqual(msg_type, "interactive")
        self.assertIn("header", content)

    def test_text_content(self):
        msg_type, content = feishu_app.build_content(self.notification(), {"message_type": "text"})
        self.assertEqual(msg_type, "text")
        self.assertIn("[CODEX] ✅ 完成", content["text"])

    def test_message_payload(self):
        payload = feishu_app.build_message_payload(
            self.notification(),
            {"message_type": "card", "receive_id_type": "email", "receive_id": "me@example.com"},
        )
        self.assertEqual(payload["receive_id"], "me@example.com")
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertIsInstance(payload["content"], str)
        self.assertIn("header", json.loads(payload["content"]))

    def test_unsupported_receive_type(self):
        with self.assertRaises(feishu_app.FeishuAppError):
            feishu_app.build_message_payload(
                self.notification(),
                {"message_type": "card", "receive_id_type": "phone", "receive_id": "x"},
            )

    def test_send_uses_token_and_message_endpoint(self):
        config = {"app_id": "cli_x", "app_secret": "s", "receive_id_type": "email", "receive_id": "me@example.com"}
        with patch(
            "notifyhub.channels.feishu_app._post_json",
            side_effect=[{"tenant_access_token": "t-123"}, {"code": 0, "data": {"message_id": "om_1"}}],
        ) as post:
            result = feishu_app.send(self.notification(), config)

        self.assertEqual(result["data"]["message_id"], "om_1")
        first_url = post.call_args_list[0].args[0]
        second_url = post.call_args_list[1].args[0]
        self.assertIn("tenant_access_token", first_url)
        self.assertIn("receive_id_type=email", second_url)
        self.assertEqual(post.call_args_list[1].args[2]["Authorization"], "Bearer t-123")

    def test_send_requires_receive_id(self):
        with self.assertRaises(feishu_app.FeishuAppError):
            feishu_app.send(self.notification(), {"receive_id": ""})

    def test_upload_card_logo_uses_bot_image_endpoint(self):
        with patch.object(feishu_app, 'get_tenant_token', return_value='token'), \
                patch.object(feishu_app, '_request_json', return_value={'data': {'image_key': 'img_v2_logo'}}) as send:
            self.assertEqual(feishu_app._upload_card_image(b'image', {'base_url': 'https://example.test/'}),
                             'img_v2_logo')
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'https://example.test/open-apis/im/v1/images')
        self.assertEqual(request.get_header('Authorization'), 'Bearer token')
        self.assertIn('multipart/form-data; boundary=', request.get_header('Content-type'))
        self.assertIn(b'name="image_type"\r\n\r\nmessage', request.data)
        self.assertIn(b'Content-Type: image/png\r\n\r\nimage', request.data)

    def test_help_logo_is_cached_for_the_uploading_app(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(feishu_app, '_upload_card_image', side_effect=['img_v2_first', 'img_v2_second']) as upload:
            path = Path(directory) / 'help-logo.json'
            config = {'app_id': 'cli_first', 'app_secret': 'private-fixture'}
            self.assertEqual(feishu_app.help_logo_key(config, path), 'img_v2_first')
            self.assertEqual(feishu_app.help_logo_key(config, path), 'img_v2_first')
            upload.assert_called_once()
            self.assertEqual(feishu_app.help_logo_key({**config, 'app_id': 'cli_other'}, path), 'img_v2_second')
            self.assertNotIn(config['app_secret'], path.read_text(encoding='utf-8'))

    def test_validate_config_accepts_app_channel(self):
        config = deep_merge(
            DEFAULT_CONFIG,
            {
                "channels": {
                    "feishu_app": {
                        "enabled": True,
                        "app_id": "cli_x",
                        "app_secret": "s",
                        "receive_id_type": "open_id",
                        "receive_id": "ou_x",
                    }
                }
            },
        )
        self.assertEqual(validate_config(config), [])

    def test_validate_config_rejects_bad_receive_type(self):
        config = deep_merge(
            DEFAULT_CONFIG,
            {"channels": {"feishu_app": {"receive_id_type": "phone"}}},
        )
        self.assertTrue(validate_config(config))


if __name__ == "__main__":
    unittest.main()
