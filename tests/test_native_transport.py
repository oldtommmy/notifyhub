import logging
import unittest
from unittest.mock import patch

from notifyhub.native_transport import NativeTransport
from notifyhub.channels import feishu_app


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.frames = []
        self.transport = NativeTransport({'channels': {'feishu_app': {}}, 'bridge': {}}, logging.getLogger('test'), self.frames.append)

    def test_unknown_destination_is_rejected(self):
        self.transport.enqueue({'type': 'reply', 'message_id': 'unseen'})
        self.assertTrue(self.transport.outbound.empty())

    def test_inbound_roundtrip_keeps_message_identity(self):
        self.transport.inbound(kind='agent', payload=['dsh', '继续'], message_id='om_1', chat_id='oc_1', sender_id='ou_1', chat_type='p2p')
        self.assertEqual(self.frames[-1]['message_id'], 'om_1')
        self.assertEqual(self.frames[-1]['kind'], 'task')
        self.transport.enqueue({'type': 'reply', 'message_id': 'om_1', 'text': 'done', 'key': 'result'})
        self.assertEqual(self.transport.outbound.get_nowait()['message_id'], 'om_1')

    def test_reply_uuid_is_stable_for_result_but_differs_for_ack(self):
        with patch.object(feishu_app, '_token_for', return_value=('test', 'https://example.invalid', 5)), patch.object(feishu_app, '_post_json', return_value={}) as post:
            feishu_app.reply_text('om_1', 'done', {}, dedupe_key='result')
            first = post.call_args.args[1]['uuid']
            feishu_app.reply_text('om_1', 'done', {}, dedupe_key='result')
            self.assertEqual(first, post.call_args.args[1]['uuid'])
            feishu_app.reply_text('om_1', 'received', {}, dedupe_key='accepted')
            self.assertNotEqual(first, post.call_args.args[1]['uuid'])
