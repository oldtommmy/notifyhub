import json
import logging
from pathlib import Path
from queue import Empty
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from notifyhub.adapters import dsh
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
        transport.config['bridge']['allow_open_ids'] = ['owner']
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
        action = next(item['value'] for item in actions if item['text']['content'] == '显示完整回答')
        opened = transport.handle_card_action(NS(event=NS(
            operator=NS(open_id='owner'), context=NS(open_chat_id='oc_original', open_message_id='result-card'),
            action=NS(value=action),
        )))
        self.assertEqual(opened.card.data['elements'][0]['content'], 'done')
        receipt.assert_called_once_with('om_phone', 'delivered')

    def test_inbound_roundtrip_keeps_message_identity(self):
        self.transport.inbound(kind='agent', payload=['dsh', '继续'], message_id='om_1', chat_id='oc_1', sender_id='ou_1', chat_type='p2p')
        self.assertEqual(self.frames[-1]['message_id'], 'om_1')
        self.assertEqual(self.frames[-1]['kind'], 'task')
        self.transport.enqueue({'type': 'reply', 'message_id': 'om_1', 'text': 'done', 'key': 'result'})
        self.assertEqual(self.transport.outbound.get_nowait()['message_id'], 'om_1')

    def test_continuation_rejects_only_a_prompt_above_the_default_limit(self):
        transport = self.transport
        chat = {'chat_id': 'oc_1', 'sender_id': 'ou_1', 'chat_type': 'p2p'}
        transport.inbound(kind='agent', payload=['dsh', '问' * 5000], message_id='om_fit', **chat)
        transport.inbound(kind='agent', payload=['dsh', '问' * 5001], message_id='om_long', **chat)
        self.assertEqual([frame['message_id'] for frame in self.frames], ['om_fit'])
        self.assertEqual(len(self.frames[0]['payload'][1]), 5000)
        refusal = transport.outbound.get_nowait()
        self.assertEqual(refusal['message_id'], 'om_long')
        self.assertIn('5000 字符上限', refusal['text'])

    def test_full_answer_pages_update_the_same_card_without_submitting_a_turn(self):
        from lark_oapi.core.json import JSON

        transport = self.transport
        transport.config['bridge']['allow_open_ids'] = ['owner']
        transport.targets['om_long_answer'] = 'oc_original'
        answer = '开始\n' + ('一段🙂 "字符" 和 \\ 路径\n' * 4000) + '完整结尾'
        response = {'type': 'reply', 'message_id': 'om_long_answer', 'key': 'result',
                    'view': {'type': 'result', 'engine': 'Codex', 'ok': True, 'text': answer}}
        sender = patch.object(feishu_app, 'reply_card')
        send = sender.start()
        self.addCleanup(sender.stop)
        with patch.object(transport.outbound, 'get', side_effect=[response, StopIteration]):
            with self.assertRaises(StopIteration):
                transport._send_loop()
        preview = send.call_args.args[1]
        self.assertIn(answer[:3000], preview['elements'][2]['text']['content'])
        self.assertNotIn('完整结尾', json.dumps(preview, ensure_ascii=False))

        def buttons(card):
            return {item['text']['content']: item['value']
                    for element in card['elements'] for item in element.get('actions', [])}

        def click(action, chat='oc_original'):
            return transport.handle_card_action(NS(event=NS(
                operator=NS(open_id='owner'), context=NS(open_chat_id=chat, open_message_id='result-card'),
                action=NS(value=action),
            )))

        open_answer = buttons(preview)['显示完整回答']
        self.assertEqual(click(open_answer, 'oc_other').toast.type, 'warning')
        opened = click(open_answer)
        pages = []
        for _ in range(20):
            self.assertLessEqual(len(JSON.marshal(opened).encode('utf-8')), 30_000)
            self.assertEqual(opened.card.type, 'raw')
            card = opened.card.data
            pages.append(card['elements'][0]['content'])
            controls = buttons(card)
            if '下一段' not in controls:
                break
            opened = click(controls['下一段'])
        self.assertGreater(len(pages), 1)
        self.assertEqual(''.join(pages), answer)
        previous = click(controls['上一段'])
        self.assertEqual(previous.card.data['elements'][0]['content'], pages[-2])
        collapsed = click(buttons(previous.card.data)['收起回答'])
        self.assertIn('显示完整回答', buttons(collapsed.card.data))
        self.assertNotIn('完整结尾', json.dumps(collapsed.card.data, ensure_ascii=False))
        self.assertTrue(transport.outbound.empty())
        self.assertEqual([frame['type'] for frame in self.frames], ['delivery'])
        send.assert_called_once()

    def test_completion_card_opens_only_its_own_full_answer(self):
        from lark_oapi.core.json import JSON

        app = {'app_id': 'cli_notification', 'app_secret': 'test',
               'receive_id_type': 'open_id', 'receive_id': 'ou_owner'}
        config = {'channels': {'feishu_app': app}, 'bridge': {'card_actions': True}}
        answer = '开始\n' + ('一段🙂 "正文" 和 \\ 路径\n' * 2500) + '完整结尾'
        payload = {'session_id': 'original', 'last_assistant_message': answer}
        notification = dsh.parse(payload, {})

        def buttons(card):
            return {element['text']['content']: element['behaviors'][0]['value']
                    for element in card['body']['elements'] if element['tag'] == 'button'}

        with TemporaryDirectory() as directory, patch.object(Path, 'home', return_value=Path(directory)):
            transport = NativeTransport(config, logging.getLogger('test'), self.frames.append)
            outgoing = feishu_app.build_message_payload(notification, app)
            self.assertIsNone(transport.notification_answers.prepare(notification, outgoing))
            transport.notification_answers.heartbeat()
            hidden = dsh.parse(payload, {'dsh': {'include_last_message': False}})
            self.assertEqual(hidden.full_answer, '')
            self.assertIsNone(transport.notification_answers.prepare(hidden, feishu_app.build_message_payload(hidden, app)))
            response = {'data': {'chat_id': 'oc_original', 'message_id': 'om_notice'}}
            with patch.object(feishu_app, 'get_tenant_token', return_value='test-token'), \
                    patch.object(feishu_app, '_post_json', return_value=response) as send:
                self.assertEqual(feishu_app.send(notification, app), response)
            preview = json.loads(send.call_args.args[1]['content'])
            self.assertNotIn('完整结尾', json.dumps(preview, ensure_ascii=False))
            action = buttons(preview)['显示完整回答']
            restarted = NativeTransport(config, logging.getLogger('test'), self.frames.append)

            def click(value, chat='oc_original', message_id='om_notice'):
                return restarted.handle_card_action(NS(event=NS(
                    operator=NS(open_id='ou_owner'), context=NS(open_chat_id=chat, open_message_id=message_id),
                    action=NS(value=value),
                )))

            self.assertEqual(click(action, chat='oc_other').toast.type, 'warning')
            self.assertEqual(click(action, message_id='om_other').toast.type, 'warning')
            all_text = []
            for _ in range(20):
                opened = click(action)
                self.assertLessEqual(len(JSON.marshal(opened).encode('utf-8')), 30_000)
                self.assertEqual(opened.card.data['schema'], '2.0')
                all_text.append(opened.card.data['body']['elements'][0]['content'])
                controls = buttons(opened.card.data)
                if '下一段' not in controls:
                    break
                action = controls['下一段']
            self.assertGreater(len(all_text), 1)
            self.assertEqual(''.join(all_text), answer)
            self.assertEqual(click(controls['收起回答']).card.data, preview)
            self.assertFalse(self.frames)

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
        buttons = {(button['value']['engine'], button['value']['kind']): button
                   for element in card['elements'] for button in element.get('actions', [])}

        def click(button):
            return transport.handle_card_action(NS(event=NS(
                operator=NS(open_id='owner'), context=NS(open_chat_id=chat, open_message_id='menu'),
                action=NS(value=button['value']),
            )))

        with patch.object(transport.codex, 'enqueue') as route_codex:
            self.assertEqual(click(buttons['codex', 'attach_list']).toast.type, 'info')
            self.assertEqual(route_codex.call_args.args[0]['kind'], 'attach_list')
            for query in ('status', 'latest_answer', 'pending_input'):
                self.assertEqual(click(buttons['current', query]).toast.type, 'info')
                self.assertEqual(route_codex.call_args.args[0]['kind'], query)
            self.assertEqual(click(buttons['dsh', 'attach_list']).toast.type, 'info')
            self.assertEqual(self.frames[-1]['kind'], 'attach_list')
            self.assertEqual(transport.routes[chat], 'dsh')

    def test_session_commands_use_the_saved_engine_and_literal_reply(self):
        config = {'channels': {'feishu_app': {'app_id': 'cli_test'}},
                  'bridge': {'codex_enabled': True, 'antigravity_enabled': True}}
        chat = {'chat_id': 'oc_test', 'sender_id': 'owner', 'chat_type': 'p2p'}

        def send(transport, message_id, text):
            transport.inbound(kind='agent', payload=['dsh', text], message_id=message_id, **chat)

        with TemporaryDirectory() as directory, patch.object(Path, 'home', return_value=Path(directory)):
            transport = NativeTransport(config, logging.getLogger('test'), self.frames.append)
            with patch.object(transport.antigravity, 'enqueue') as select:
                send(transport, 'switch', 'Antigravity会话')
                self.assertEqual(select.call_args.args[0]['kind'], 'attach_list')
            restarted = NativeTransport(config, logging.getLogger('test'), self.frames.append)
            with patch.object(restarted.codex, 'enqueue') as codex, patch.object(restarted.antigravity, 'enqueue') as agy:
                restarted.inbound(kind='status', payload=None, engine='codex', message_id='old-card-query', **chat)
                self.assertEqual(codex.call_args.args[0]['kind'], 'status')
                send(restarted, 'latest', '最新回答')
                self.assertEqual(agy.call_args.args[0]['kind'], 'latest_answer')
                send(restarted, 'reply', '回复 状态')
                self.assertEqual(agy.call_args.args[0]['kind'], 'reply_text')
                self.assertEqual(agy.call_args.args[0]['payload'], ['dsh', '状态'])
                send(restarted, 'pending', '/pending')
                self.assertEqual(agy.call_args.args[0]['kind'], 'pending_input')
            self.assertFalse(self.frames)

    def test_literal_reply_reaches_selected_dsh_with_codex_as_default(self):
        transport = NativeTransport({'bridge': {'codex_enabled': True, 'default_agent': 'codex'}},
                                    logging.getLogger('test'), self.frames.append)
        chat = {'chat_id': 'oc_test', 'sender_id': 'owner', 'chat_type': 'p2p'}
        transport.inbound(kind='agent', payload=['codex', 'DSH会话'], message_id='switch', **chat)
        transport.inbound(kind='agent', payload=['codex', '回复 1'], message_id='reply', **chat)
        self.assertEqual(self.frames[-1]['kind'], 'reply_text')
        self.assertEqual(self.frames[-1]['payload'], ['dsh', '1'])

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
