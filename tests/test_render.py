import unittest

from notifyhub.events import Notification
from notifyhub.render import build_card, render_text


class RenderTests(unittest.TestCase):
    def test_goal_and_message(self):
        notification = Notification(
            source="codex",
            event="Stop",
            status_text="🔁 Goal 进行中",
            project="demo",
            title="修复登录",
            model="gpt-6.1-sol",
            goal_status="active",
            goal_objective="把登录超时修好",
            goal_tokens_used=12000,
            goal_token_budget=50000,
            goal_time_used_seconds=185,
            last_message="已经定位到问题。",
        )
        text = render_text(notification)
        self.assertIn("[CODEX] 🔁 Goal 进行中 · demo", text)
        self.assertIn("会话: 修复登录", text)
        self.assertIn("Goal: 把登录超时修好 · 进行中 · 12,000/50,000 tokens · 3m05s", text)
        self.assertIn("已经定位到问题。", text)

    def test_message_can_be_hidden(self):
        notification = Notification(
            source="codex",
            event="Stop",
            status_text="✅ 完成",
            last_message="secret",
            include_last_message=False,
        )
        self.assertNotIn("secret", render_text(notification))

    def test_card_uses_markdown_components(self):
        notification = Notification(
            source="codex",
            event="Stop",
            status_text="✅ 本轮完成",
            project="demo",
            title="修复登录",
            model="gpt-6.1-sol",
            quota_line="剩余额度: 21% · 10 小时后重置",
            show_quota=True,
            recent_prompt="帮我修复登录超时",
            last_message="**已修复**\n\n- 登录超时\n- 增加重试\n\n```python\nprint('ok')\n```",
        )
        card = build_card(notification)

        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["subtitle"]["content"], "修复登录")

        elements = card["body"]["elements"]
        self.assertEqual(elements[0]["tag"], "div")
        fields = elements[0]["fields"]
        contents = [field["text"]["content"] for field in fields]
        self.assertTrue(any("**最近提问**" in content for content in contents))
        self.assertFalse(any("**会话**" in content for content in contents))
        self.assertTrue(any("**模型**" in content for content in contents))
        self.assertTrue(any("**剩余额度**" in content for content in contents))

        model_field = next(field for field in fields if "**模型**" in field["text"]["content"])
        quota_field = next(field for field in fields if "**剩余额度**" in field["text"]["content"])
        self.assertTrue(model_field["is_short"])
        self.assertTrue(quota_field["is_short"])
        self.assertTrue(any(element["tag"] == "hr" for element in elements))

        message = [element["content"] for element in elements if element["tag"] == "markdown"][-1]
        self.assertIn("**已修复**", message)
        self.assertIn("```python", message)

    def test_card_subtitle_falls_back(self):
        notification = Notification(
            source="codex",
            event="Stop",
            status_text="✅ 完成",
            title="Codex",
            project="Codex",
        )
        card = build_card(notification)
        self.assertEqual(card["header"]["subtitle"]["content"], "未命名会话")

    def test_card_without_message_has_no_divider(self):
        notification = Notification(
            source="codex",
            event="Stop",
            status_text="✅ 完成",
            project="demo",
            include_last_message=False,
        )
        card = build_card(notification)
        self.assertFalse(any(element["tag"] == "hr" for element in card["body"]["elements"]))


if __name__ == "__main__":
    unittest.main()
