import logging
from pathlib import Path
import tempfile
import unittest

from notifyhub.codex_bridge import CodexBridge
from notifyhub.codex_rpc import CodexRpcError, CodexRpcRejected


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.turns = []
        self.error = None
        self.thread = {'id': 'thread-one', 'name': 'Project', 'cwd': '/example/project'}

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/list':
            return {'data': [self.thread]}
        if method == 'thread/read':
            return {'thread': self.thread}
        if method == 'thread/queue/add':
            if self.error:
                raise self.error
            return {'queuedSubmission': {'id': 'queue-one'}}
        if method == 'thread/turns/list':
            return {'data': self.turns}
        raise AssertionError(method)


class CodexBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.json'
        self.rpc = FakeRpc()
        self.outputs = []
        self.config = {'bridge': {'codex_enabled': True}}
        self.bridge = self.make_bridge()
        self.bridge.handle(self.frame('pick', 'attach', 'thread-one'))

    def make_bridge(self):
        return CodexBridge(self.config, self.outputs.append, logging.getLogger(__name__),
                           state_path=self.path, rpc_factory=lambda: self.rpc)

    @staticmethod
    def frame(identity, kind='task', payload=None, chat_type='p2p'):
        return {'chat_id': 'chat', 'message_id': identity, 'chat_type': chat_type, 'kind': kind,
                'payload': ['dsh', 'first message'] if payload is None else payload}

    def queue_calls(self):
        return [params for method, params in self.rpc.calls if method == 'thread/queue/add']

    def test_list_and_route_switch(self):
        frame = self.frame('list', payload=['dsh', 'Codex会话'])
        self.assertTrue(self.bridge.wants(frame))
        self.bridge.handle(frame)
        self.assertEqual(self.outputs[-1]['view']['sessions'][0]['title'], 'Project')
        self.assertTrue(self.bridge.wants(self.frame('phone')))
        self.assertFalse(self.bridge.wants(self.frame('switch', payload=['dsh', 'DSH会话'])))
        self.assertFalse(self.bridge.wants(self.frame('next')))

    def test_queue_once_and_return_only_matching_final_answer(self):
        self.bridge.handle(self.frame('phone'))
        self.bridge.handle(self.frame('phone'))
        self.bridge.handle(self.frame('while-pending'))
        [queued] = self.queue_calls()
        self.assertEqual(queued['threadId'], 'thread-one')
        self.assertEqual(queued['input'], [{'type': 'text', 'text': 'first message'}])
        self.rpc.turns = [{'id': 'desktop', 'status': 'completed', 'items': [
            {'type': 'userMessage', 'clientId': 'desktop-id'},
            {'type': 'agentMessage', 'text': 'wrong reply'}]}]
        self.bridge.poll()
        self.assertFalse(any(row['key'] == 'result' for row in self.outputs))
        self.rpc.turns.append({'id': 'phone-turn', 'status': 'completed', 'items': [
            {'type': 'userMessage', 'clientId': queued['clientUserMessageId']},
            {'type': 'agentMessage', 'phase': 'commentary', 'text': 'working'},
            {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'correct reply'}]})
        self.bridge.poll()
        self.bridge.poll()
        self.assertEqual([row['text'] for row in self.outputs if row['key'] == 'result'], ['correct reply'])

    def test_uncertain_queue_is_never_replayed_after_restart(self):
        self.rpc.error = CodexRpcError('timeout')
        self.bridge.handle(self.frame('phone'))
        self.assertEqual(self.outputs[-1]['key'], 'uncertain')
        restarted = self.make_bridge()
        restarted.handle(self.frame('phone'))
        self.assertEqual(self.outputs[-1]['key'], 'duplicate')
        self.assertEqual(len(self.queue_calls()), 1)

    def test_definite_rejection_does_not_block_new_message(self):
        self.rpc.error = CodexRpcRejected('unsupported')
        self.bridge.handle(self.frame('first'))
        self.rpc.error = None
        self.bridge.handle(self.frame('second'))
        self.assertEqual(len(self.queue_calls()), 2)
        self.assertEqual(self.outputs[-1]['key'], 'accepted')

    def test_group_message_and_disabled_mode_do_not_queue(self):
        self.bridge.handle(self.frame('group', chat_type='group'))
        self.bridge.enabled = False
        self.bridge.handle(self.frame('off'))
        self.assertEqual(self.queue_calls(), [])
