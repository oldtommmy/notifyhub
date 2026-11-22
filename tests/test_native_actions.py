import logging
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from notifyhub.native_actions import CardActions
from notifyhub.native_cards import build_native_card
from notifyhub.native_transport import NativeTransport


class CardActionTests(unittest.TestCase):
    def test_capability_is_chat_bound_expires_and_rejects_tampering(self):
        signer = CardActions()
        value = signer.sign('chat', 'attach', 'session-original')
        self.assertTrue(signer.verify(value, 'chat'))
        self.assertFalse(signer.verify(value, 'other-chat'))
        self.assertFalse(signer.verify({**value, 'payload': 'another-session'}, 'chat'))
        self.assertFalse(CardActions().verify(value, 'chat'))
        with patch('notifyhub.native_actions.time.time', return_value=value['expires'] + 1):
            self.assertFalse(signer.verify(value, 'chat'))

    def test_callback_checks_operator_and_deduplicates_before_forwarding(self):
        frames = []
        transport = NativeTransport({'bridge': {'enabled': True, 'card_actions': True, 'allow_open_ids': ['owner']}}, logging.getLogger('test'), frames.append)
        value = transport.actions.sign('chat', 'attach', 'session-original')
        data = NS(event=NS(operator=NS(open_id='stranger'), context=NS(open_chat_id='chat', open_message_id='parent-card'), action=NS(value=value)))
        self.assertEqual(transport.handle_card_action(data).toast.type, 'error')
        self.assertFalse(frames)
        data.event.operator.open_id = 'owner'
        self.assertEqual(transport.handle_card_action(data).toast.type, 'info')
        self.assertEqual(frames[0]['kind'], 'attach')
        self.assertEqual(frames[0]['payload'], 'session-original')
        request_id = frames[0]['message_id']
        self.assertEqual(transport.card_replies[request_id], 'parent-card')
        transport.handle_card_action(data)
        self.assertEqual(len(frames), 1)

    def test_buttons_only_appear_when_enabled_and_target_exact_session(self):
        signer = CardActions()
        card = build_native_card({'type': 'sessions', 'sessions': [{'id': 'exact-session', 'title': '测试'}]}, action_value=lambda kind, payload: signer.sign('chat', kind, payload))
        buttons = [button for element in card['elements'] if element['tag'] == 'action' for button in element['actions']]
        self.assertEqual([b['text']['content'] for b in buttons], ['继续此会话', '刷新列表', '当前会话', '使用帮助'])
        self.assertEqual(buttons[0]['value']['payload'], 'exact-session')
        self.assertTrue(all(signer.verify(b['value'], 'chat') for b in buttons))
