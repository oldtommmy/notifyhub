import json
import unittest
from notifyhub.native_cards import build_native_card


class NativeCardTests(unittest.TestCase):
    def test_list_has_titles_project_model_current_marker_and_numeric_selection(self):
        card = build_native_card({'type': 'sessions', 'sessions': [{'title': '真实会话名', 'project': 'my_project', 'model': 'test-model', 'status': 'idle', 'current': True}]})
        text = json.dumps(card, ensure_ascii=False)
        for value in ['真实会话名', 'test-model', '当前会话', '回复序号', '空闲']:
            self.assertIn(value, text)
        self.assertNotIn('button', text)  # no dead buttons without callback subscription

    def test_results_preserve_markdown_and_limit_body(self):
        card = build_native_card({'type': 'result', 'ok': True, 'session': {'title': '修复通知'}, 'text': '**完成**\n' + 'x' * 100, 'turn': 4, 'duration': 8}, max_chars=30)
        text = json.dumps(card, ensure_ascii=False)
        self.assertIn('**完成**', text)
        self.assertIn('内容截断', text)
        self.assertIn('第 4 轮', text)
        self.assertEqual(card['header']['template'], 'green')

    def test_empty_list_explains_next_step(self):
        self.assertIn('请先在桌面', json.dumps(build_native_card({'type': 'sessions'}), ensure_ascii=False))
