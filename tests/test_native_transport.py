import json
import logging
from queue import Empty
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from notifyhub.bridge import parse_command
from notifyhub.native_cards import build_native_card
from notifyhub.native_transport import NativeTransport
from notifyhub.channels import feishu_app


class NativeTransportTests(unittest.TestCase):
    def test_plain_ack_with_explicit_null_view_keeps_reply_worker_alive(self):
        transport = NativeTransport({'bridge': {}}, logging.getLogger('test'), lambda frame: None)
        transport.receipt({'type': 'reply', 'message_id': 'phone', 'key': 'accepted', 'view': None}, 'delivered')

    def setUp(self):
        self.frames = []
        self.transport = NativeTransport({'channels': {'feishu_app': {}}, 'bridge': {}}, logging.getLogger('test'), self.frames.append)

    def test_unknown_destination_is_rejected(self):
        self.transport.enqueue({'type': 'reply', 'message_id': 'unseen'})
        self.assertTrue(self.transport.outbound.empty())

    def test_recovered_antigravity_result_can_sign_card_after_gateway_restart(self):
        transport = self.transport
        transport.buttons_enabled = True
        response = {'type': 'reply', 'message_id': 'om_phone', 'request_id': 'om_phone', 'chat_id': 'oc_original',
                    'key': 'result', 'view': {'type': 'result', 'engine': 'Antigravity', 'ok': True,
                                              'text': 'done', 'session': {'id': 'session', 'title': 'test'}}}
        with patch.object(transport.outbound, 'get', side_effect=[Empty, StopIteration]), \
                patch.object(transport.antigravity, 'take_result', return_value=response), \
                patch.object(transport.antigravity, 'receipt') as receipt, \
                patch.object(feishu_app, 'reply_card') as reply:
            with self.assertRaises(StopIteration):
                transport._send_loop()

        self.assertFalse(transport.targets)
        self.assertEqual(reply.call_args.args[0], 'om_phone')
        actions = next(element['actions'] for element in reply.call_args.args[1]['elements'] if element['tag'] == 'action')
        self.assertTrue(transport.actions.verify(actions[0]['value'], 'oc_original'))
        receipt.assert_called_once_with('om_phone', 'delivered')

    def test_inbound_roundtrip_keeps_message_identity(self):
        self.transport.inbound(kind='agent', payload=['dsh', '继续'], message_id='om_1', chat_id='oc_1', sender_id='ou_1', chat_type='p2p')
        self.assertEqual(self.frames[-1]['message_id'], 'om_1')
        self.assertEqual(self.frames[-1]['kind'], 'task')
        self.transport.enqueue({'type': 'reply', 'message_id': 'om_1', 'text': 'done', 'key': 'result'})
        self.assertEqual(self.transport.outbound.get_nowait()['message_id'], 'om_1')

    def test_help_menu_and_buttons_route_without_submitting_a_prompt(self):
        transport = self.transport
        transport.config['bridge'].update(enabled=True, allow_open_ids=['owner'])
        transport.buttons_enabled = True
        transport.codex.enabled = True
        chat = 'oc_1'
        for alias in ('帮助', '命令', '菜单', 'HELP', 'command', '/help', '/COMMAND'):
            with self.subTest(alias=alias):
                self.assertEqual(parse_command(alias, transport.config), ('help', None))
        kind, payload = parse_command('帮助', transport.config)
        transport.inbound(kind=kind, payload=payload, message_id='om_1', chat_id=chat, sender_id='owner', chat_type='p2p')
        self.assertFalse(self.frames)
        view = transport.outbound.get_nowait()['view']
        self.assertEqual(view, {'type': 'help', 'dsh': True, 'codex': True, 'antigravity': False})
        card = build_native_card(view, action_value=lambda k, p, e: transport.actions.sign(chat, k, p, e))
        buttons = {button['value']['engine']: button
                   for element in card['elements'] for button in element.get('actions', [])}

        def click(button):
            return transport.handle_card_action(NS(event=NS(
                operator=NS(open_id='owner'), context=NS(open_chat_id=chat, open_message_id='menu'),
                action=NS(value=button['value']),
            )))

        with patch.object(transport.codex, 'enqueue') as route_codex:
            self.assertEqual(click(buttons['codex']).toast.type, 'info')
            self.assertEqual(route_codex.call_args.args[0]['kind'], 'attach_list')
            self.assertEqual(click(buttons['current']).toast.type, 'info')
            self.assertEqual(route_codex.call_args.args[0]['kind'], 'status')
            self.assertEqual(click(buttons['dsh']).toast.type, 'info')
            self.assertEqual(self.frames[-1]['kind'], 'attach_list')
            self.assertEqual(transport.codex.routes[chat], 'dsh')

    def test_help_without_card_callbacks_shows_only_available_commands(self):
        card = build_native_card({'type': 'help', 'dsh': False, 'codex': True, 'antigravity': False})
        text = json.dumps(card, ensure_ascii=False)
        self.assertFalse(any(element['tag'] == 'action' for element in card['elements']))
        self.assertIn('Codex会话', text)
        self.assertNotIn('DSH会话', text)
        self.assertNotIn('Antigravity会话', text)
        self.assertIn('/attach 2', text)

    def test_help_sends_once_when_optional_logo_upload_fails(self):
        transport = self.transport
        transport.targets['om_help'] = 'oc_1'
        response = {'type': 'reply', 'message_id': 'om_help', 'key': 'help',
                    'view': {'type': 'help', 'codex': True}}
        with patch.object(transport.outbound, 'get', side_effect=[response, StopIteration]), \
                patch.object(feishu_app, 'help_logo_key', side_effect=feishu_app.FeishuAppError('scope')), \
                patch.object(feishu_app, 'reply_card') as reply:
            with self.assertRaises(StopIteration):
                transport._send_loop()

        reply.assert_called_once()
        self.assertEqual(reply.call_args.args[1]['header']['title']['content'], 'NotifyHub · 续聊菜单')
        self.assertEqual(reply.call_args.kwargs['dedupe_key'], 'help')
        self.assertEqual(self.frames[-1]['status'], 'delivered')

    def test_reply_uuid_is_stable_for_result_but_differs_for_ack(self):
        with patch.object(feishu_app, '_token_for', return_value=('test', 'https://example.invalid', 5)), patch.object(feishu_app, '_post_json', return_value={}) as post:
            feishu_app.reply_text('om_1', 'done', {}, dedupe_key='result')
            first = post.call_args.args[1]['uuid']
            feishu_app.reply_text('om_1', 'done', {}, dedupe_key='result')
            self.assertEqual(first, post.call_args.args[1]['uuid'])
            feishu_app.reply_text('om_1', 'received', {}, dedupe_key='accepted')
            self.assertNotEqual(first, post.call_args.args[1]['uuid'])
