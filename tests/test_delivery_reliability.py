import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notifyhub.channels import feishu_app
from notifyhub.events import Notification
from notifyhub.router import dispatch


class DeliveryReliabilityTests(unittest.TestCase):
    def test_concurrent_hooks_keep_all_keys_and_send_each_once(self):
        with tempfile.TemporaryDirectory() as directory:
            script = '''
import logging, sys, time
from pathlib import Path
from unittest.mock import patch
from notifyhub.events import Notification
from notifyhub.router import dispatch
root = Path(sys.argv[1])
key = sys.argv[2]
config = {"state_path": str(root / "state.json"), "channels": {"test": {"enabled": True}}}
def send(*args):
    time.sleep(0.08)
    with (root / "sent").open("a") as f: f.write(key + "\\n")
with patch("notifyhub.router.get_sender", return_value=send):
    dispatch(Notification(source="test", event="Stop", status_text="done", dedupe_key=key), config, logging.getLogger("test"))
'''
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'scripts'))
            processes = [subprocess.Popen([sys.executable, '-c', script, directory, key], env=env)
                         for key in ['a', 'a', 'b', 'c', 'b', 'c']]
            for process in processes:
                self.assertEqual(process.wait(timeout=15), 0)
            self.assertCountEqual((Path(directory) / 'sent').read_text().splitlines(), ['a', 'b', 'c'])
            self.assertCountEqual(json.loads((Path(directory) / 'state.json').read_text())['seen'], ['a', 'b', 'c'])

    def test_ambiguous_submission_is_not_resent_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {'state_path': str(Path(directory) / 'state.json'), 'channels': {'feishu_app': {'enabled': True}}}
            notification = Notification(source='dsh', event='Stop', status_text='done', dedupe_key='dsh:1:2')
            with patch('notifyhub.router.get_sender', return_value=lambda *args: (_ for _ in ()).throw(feishu_app.AmbiguousSendError('timeout'))) as sender:
                self.assertFalse(dispatch(notification, config, logging.getLogger('test'))[0][1])
                self.assertEqual(dispatch(notification, config, logging.getLogger('test')), [])
                self.assertEqual(sender.call_count, 1)

    def test_token_failure_remains_retryable_but_submission_timeout_is_ambiguous(self):
        n = Notification(source='dsh', event='Stop', status_text='done')
        config = {'receive_id': 'ou_test', 'app_id': 'cli_test', 'app_secret': 'test'}
        with patch.object(feishu_app, '_post_json', side_effect=feishu_app.TransportError('token timeout')):
            with self.assertRaises(feishu_app.TransportError):
                feishu_app.send(n, config)
        with patch.object(feishu_app, '_post_json', side_effect=[{'tenant_access_token': 'token'}, feishu_app.TransportError('message timeout')]):
            with self.assertRaises(feishu_app.AmbiguousSendError):
                feishu_app.send(n, config)

    def test_uuid_is_stable_per_event_and_receiver(self):
        n = Notification(source='dsh', event='Stop', status_text='done', dedupe_key='event-a')
        config = {'receive_id': 'ou_one'}
        first = feishu_app.build_message_payload(n, config)['uuid']
        self.assertEqual(first, feishu_app.build_message_payload(n, config)['uuid'])
        self.assertNotEqual(first, feishu_app.build_message_payload(n, {'receive_id': 'ou_two'})['uuid'])
        n.dedupe_key = 'event-b'
        self.assertNotEqual(first, feishu_app.build_message_payload(n, config)['uuid'])
