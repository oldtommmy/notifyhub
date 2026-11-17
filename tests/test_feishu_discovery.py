import unittest
from unittest import mock

from notifyhub import feishu_discovery


APP_INFO = {
    "data": {
        "app": {
            "app_name": "测试应用",
            "creator_id": "ou_owner",
            "owner": {
                "owner_id": "ou_owner",
                "name": "陈远航",
                "email": "me@example.com",
            },
        }
    }
}

CHATS = {"data": {"items": [{"chat_id": "oc_1", "name": "研发群"}]}}


class DiscoveryTests(unittest.TestCase):
    def test_owner_email_and_chat_candidates(self):
        calls = []

        def fake_get(url, token, timeout):
            calls.append(url)
            if "/application/" in url:
                return APP_INFO
            return CHATS

        with mock.patch.object(feishu_discovery, "get_tenant_token", return_value="token"), mock.patch.object(
            feishu_discovery, "_get_json", side_effect=fake_get
        ):
            result = feishu_discovery.discover("cli_1", "secret")

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_name"], "测试应用")
        self.assertEqual([item["type"] for item in result["candidates"]], ["open_id", "email", "chat_id"])
        self.assertEqual(result["candidates"][0]["label"], "陈远航")
        self.assertEqual(result["candidates"][0]["detail"], "应用所有者")
        self.assertEqual(result["candidates"][2]["label"], "研发群")
        self.assertEqual(result["warnings"], [])

    def test_owner_id_is_deduplicated(self):
        app_info = {
            "data": {
                "app": {
                    "app_name": "测试应用",
                    "creator_id": "ou_owner",
                    "owner": {"owner_id": "ou_owner", "name": "陈远航"},
                }
            }
        }

        with mock.patch.object(feishu_discovery, "get_tenant_token", return_value="token"), mock.patch.object(
            feishu_discovery,
            "_get_json",
            side_effect=lambda url, token, timeout: app_info if "/application/" in url else {"data": {"items": []}},
        ):
            result = feishu_discovery.discover("cli_1", "secret")

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["type"], "open_id")

    def test_partial_permission_failure_keeps_owner(self):
        with mock.patch.object(feishu_discovery, "get_tenant_token", return_value="token"), mock.patch.object(
            feishu_discovery,
            "_get_json",
            side_effect=lambda url, token, timeout: APP_INFO
            if "/application/" in url
            else (_ for _ in ()).throw(feishu_discovery.FeishuAppError("denied")),
        ):
            result = feishu_discovery.discover("cli_1", "secret")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["candidates"]), 2)
        self.assertTrue(result["warnings"])
        self.assertIn("读取群列表失败", result["warnings"][0])

    def test_all_failures_return_warning(self):
        with mock.patch.object(feishu_discovery, "get_tenant_token", return_value="token"), mock.patch.object(
            feishu_discovery,
            "_get_json",
            side_effect=feishu_discovery.FeishuAppError("denied"),
        ):
            result = feishu_discovery.discover("cli_1", "secret")

        self.assertFalse(result["ok"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["warnings"]), 2)


if __name__ == "__main__":
    unittest.main()
