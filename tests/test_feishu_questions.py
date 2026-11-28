import json
import logging
import os
from pathlib import Path
import re
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from notifyhub.feishu_questions import FeishuQuestions
from notifyhub.native_transport import NativeTransport


@unittest.skipIf(os.name == 'nt', 'The independent gateway uses Unix sockets')
class FeishuQuestionsTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix='nh-test-', dir='/tmp')
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'questions.sock'
        config = {'bridge': {'allow_open_ids': ['owner', 'other'], 'codex_enabled': True,
                             'codex_ledger_path': str(Path(tmp.name) / 'codex.json'), 'antigravity_enabled': True}}
        self.relay = FeishuQuestions(self.path, config)
        self.transport = NativeTransport(config, logging.getLogger('test'), lambda frame: None, self.relay)
        self.transport.targets['phone'] = 'chat'
        server = self.relay.open()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def request(self, engine='codex'):
        frame = {'message_id': 'phone', 'chat_id': 'chat', 'sender_id': 'owner',
                 'chat_type': 'p2p', 'ask_in_feishu': True}
        prompt = self.relay.prepare(frame, engine, '大论文实验推进', '选择运行方式')
        ticket = re.search(r'回复码 `([\w-]+)`', prompt)[1]
        return {'ticket': ticket, 'engine': engine, 'question': '选哪一种？',
                'options': ['小规模试验', '完整运行', '暂缓']}

    def client(self, request):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        client.settimeout(3)
        client.connect(str(self.path))
        client.sendall(json.dumps(request).encode() + b'\n')
        return client

    def send_inbound(self, text, message_id):
        message = NS(message_id=message_id, chat_id='chat', message_type='text', chat_type='p2p',
                     content=json.dumps({'text': text}))
        event = NS(event=NS(message=message, sender=NS(sender_id=NS(open_id='owner'))))
        self.transport.listener.handle_event(event)

    def test_phone_letter_resolves_waiting_request_without_new_turn(self):
        for engine in ('codex', 'antigravity'):
            with self.subTest(engine=engine), patch.object(self.transport.codex, 'enqueue') as codex, \
                    patch.object(self.transport.antigravity, 'enqueue') as antigravity:
                self.transport.routes['chat'] = engine
                with self.client(self.request(engine)) as client:
                    question = self.transport.outbound.get(timeout=2)
                    self.assertEqual(question['message_id'], 'phone')
                    self.assertIn('A. 小规模试验', question['text'])
                    self.assertNotIn('ticket', question['text'])
                    self.assertIsNone(self.relay.respond('chat', 'other', choice='B'))
                    self.send_inbound('待回复', f'query-{engine}')
                    self.assertEqual(self.transport.outbound.get(timeout=2)['text'], question['text'])
                    self.send_inbound('D', f'invalid-{engine}')
                    self.assertIn('A / B / C', self.transport.outbound.get(timeout=2)['text'])
                    self.send_inbound('Ｂ', f'choice-{engine}')
                    self.assertEqual(json.loads(client.makefile().readline()),
                                     {'status': 'answered', 'choice': 'B', 'answer': '完整运行'})
                    self.assertIn('已将 B', self.transport.outbound.get(timeout=2)['text'])
                    codex.assert_not_called()
                    antigravity.assert_not_called()

    def test_wrong_engine_and_switching_session(self):
        request = self.request()
        with self.client({**request, 'engine': 'antigravity'}) as client:
            self.assertEqual(json.loads(client.makefile().readline())['status'], 'invalid_ticket')
        with self.client(request) as client:
            self.transport.outbound.get(timeout=2)
            with patch.object(self.transport.antigravity, 'enqueue'):
                self.send_inbound('Antigravity会话', 'switch')
            self.assertEqual(json.loads(client.makefile().readline())['status'], 'session_changed')

    def test_lost_delivery_releases_session_for_text_fallback(self):
        with self.client(self.request()) as client:
            question = self.transport.outbound.get(timeout=2)
            self.transport.receipt(question, 'unknown')
            self.assertEqual(json.loads(client.makefile().readline())['status'], 'unavailable')
            self.assertIsNone(self.relay.respond('chat', 'owner'))

    def test_expired_question_releases_turn(self):
        with patch('notifyhub.feishu_questions.WAIT_SECONDS', 0.05), self.client(self.request()) as client:
            self.transport.outbound.get(timeout=2)
            self.assertEqual(json.loads(client.makefile().readline())['status'], 'expired')
            self.assertIsNone(self.relay.respond('chat', 'owner'))

    def test_ask_command_requests_opt_in_on_selected_agent(self):
        self.transport.routes['chat'] = 'codex'
        with patch.object(self.transport.codex, 'enqueue') as enqueue:
            self.send_inbound('文字问答 选择运行方式', 'enable')
        frame = enqueue.call_args.args[0]
        self.assertTrue(frame['ask_in_feishu'])
        self.assertEqual(frame['payload'][1], '选择运行方式')
