import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from notifyhub.gateway import DshConnection
from notifyhub import gateway


class GatewayTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Gateway service runs on Unix')
    def test_stale_feishu_connection_exits_for_service_restart(self):
        transport = MagicMock()
        transport.listener.last_frame_at = 0
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(gateway, 'load_config', return_value={'bridge': {'enabled': True, 'allow_open_ids': ['owner']}}), \
                patch.object(gateway, '_acquire_instance_lock') as acquire, \
                patch.object(gateway, 'socket_path', return_value=Path(temp) / 'gateway.sock'), \
                patch.object(gateway.threading, 'Thread'), \
                patch.object(gateway.time, 'sleep'), \
                patch.object(gateway.time, 'monotonic', return_value=301), \
                patch('notifyhub.native_transport.NativeTransport', return_value=transport):
            self.assertEqual(gateway.run_gateway('unused-config'), 1)
            acquire.return_value.close.assert_called_once()
            transport.codex.close.assert_called_once()

    def test_offline_dsh_returns_feedback_without_invoking_an_agent(self):
        replies = []
        connection = DshConnection()
        connection.transport = SimpleNamespace(enqueue=replies.append)
        connection.emit({'type': 'message', 'message_id': 'phone'})
        self.assertEqual(replies[0]['message_id'], 'phone')
        self.assertEqual(replies[0]['type'], 'reply')
        self.assertIsNone(connection.client)

    @unittest.skipIf(os.name == 'nt', 'The local gateway uses Unix domain sockets on macOS/Linux')
    def test_local_dsh_connection_preserves_request_identity_and_replies(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'gateway.sock'
            connection = DshConnection()
            replies = []
            connection.transport = SimpleNamespace(enqueue=replies.append)
            threading.Thread(target=connection.serve, args=(path,), daemon=True).start()
            for _ in range(100):
                if path.exists():
                    break
                time.sleep(.01)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(path))
                for _ in range(100):
                    if connection.client is not None:
                        break
                    time.sleep(.01)
                frame = {'type': 'message', 'message_id': 'same-request', 'kind': 'status'}
                connection.emit(frame)
                client.settimeout(2)
                self.assertEqual(json.loads(client.recv(4096)), frame)
                client.sendall(b'{"type":"reply","message_id":"same-request","text":"ok"}\n')
                for _ in range(100):
                    if replies:
                        break
                    time.sleep(.01)
                self.assertEqual(replies[0]['message_id'], 'same-request')
