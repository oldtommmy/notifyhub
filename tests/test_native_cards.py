import json
import unittest
from notifyhub.native_cards import build_native_card


class NativeCardTests(unittest.TestCase):
    def test_help_groups_commands_and_displays_the_full_logo_on_the_right(self):
        card = build_native_card(
            {'type': 'help', 'codex': True, 'dsh': True, 'antigravity': True},
            action_value=lambda kind, payload, engine: {'kind': kind, 'engine': engine},
            logo_key='img_v2_logo',
        )
        self.assertNotIn('header', card)
        banner = card['elements'][0]
        self.assertEqual(banner['tag'], 'column_set')
        left, right = banner['columns']
        self.assertEqual(card['config']['style']['color'][left['background_style']], {
            'light_mode': 'rgba(224,233,255,0.5)',
            'dark_mode': 'rgba(23,49,102,0.5)',
        })
        self.assertIn('NotifyHub · 续聊菜单', left['elements'][0]['text']['content'])
        self.assertGreater(left['weight'], right['weight'])
        image = right['elements'][0]
        self.assertEqual((image['tag'], image['img_key'], image['mode']),
                         ('img', 'img_v2_logo', 'fit_horizontal'))
        actions = [element['actions'] for element in card['elements'] if element['tag'] == 'action']
        self.assertEqual([item['value']['engine'] for item in actions[0]], ['codex', 'dsh', 'antigravity'])
        self.assertEqual(actions[1][0]['value'], {'kind': 'status', 'engine': 'current'})
        fields = next(element['fields'] for element in card['elements'] if 'fields' in element)
        self.assertEqual(len(fields), 4)
        self.assertTrue(all(field['is_short'] for field in fields))
        text = json.dumps(card, ensure_ascii=False)
        self.assertNotIn('`', text)
        for command in ('Codex会话', 'DSH会话', 'Antigravity会话', '/sessions', '/attach 2',
                        '上一页', '下一页', '/status', '帮助', '命令', '菜单', 'help', 'command'):
            self.assertIn(command, text)

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
