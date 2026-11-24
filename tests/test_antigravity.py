import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notifyhub import antigravity_bridge, installer
from notifyhub.adapters.antigravity import parse


class AntigravityTests(unittest.TestCase):
    def test_stop_uses_only_current_turn_and_recorded_completion(self):
        with TemporaryDirectory() as temp:
            transcript = Path(temp) / "transcript.jsonl"
            rows = [{"type": kind, "status": "DONE", "content": text, "step_index": index}
                    for index, (kind, text) in enumerate([
                        ("USER_INPUT", "first"), ("PLANNER_RESPONSE", "old"),
                        ("USER_INPUT", "second"), ("PLANNER_RESPONSE", "new"),
                    ])]
            transcript.write_text("\n".join(map(json.dumps, rows)))
            event = dict(conversationId="session", executionNum=0, fullyIdle=True,
                         terminationReason="NO_TOOL_CALL", lastUserInput="second", transcriptPath=str(transcript))
            self.assertEqual(parse(event, {}).last_message, "new")
            self.assertEqual(parse(event, {}).color, "green")
            self.assertFalse(parse({**event, "fullyIdle": False}, {}))
            self.assertEqual(parse({**event, "lastUserInput": "unwritten third"}, {}).last_message, "")

    def test_phone_answer_requires_matching_sidecar_input_and_is_returned_once(self):
        with TemporaryDirectory() as temp, patch.object(antigravity_bridge, "ledger_path", return_value=Path(temp) / "state.json"):
            transcript = Path(temp) / "transcript.jsonl"
            event = dict(conversationId="session", executionNum=0, fullyIdle=True,
                         terminationReason="NO_TOOL_CALL", transcriptPath=str(transcript))
            notification = parse(event, {})
            antigravity_bridge.record_stop(event, notification)
            responses = []
            bridge = antigravity_bridge.AntigravityBridge({"bridge": {"antigravity_enabled": True}}, responses.append)
            msg = {"chat_id": "chat", "chat_type": "p2p"}
            bridge.handle({**msg, "kind": "attach", "payload": "session", "message_id": "bind"})
            with antigravity_bridge.ledger() as data:
                import time
                data["heartbeat"] = int(time.time())
            bridge.handle({**msg, "kind": "task", "payload": ["dsh", "phone"], "message_id": "phone"})
            with antigravity_bridge.ledger() as data:
                request = data["requests"]["phone"]
                request["state"] = "submitted"
                request["baseline_step"] = 2
            def stop(kind, input_message):
                rows = [{"type": kind, "content": input_message, "step_index": 3},
                        {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "answer"}]
                transcript.write_text("\n".join(map(json.dumps, rows)))
                return antigravity_bridge.record_stop(event, notification)

            self.assertFalse(stop("USER_INPUT", "desktop"))
            self.assertFalse(stop("SYSTEM_MESSAGE", "<SYSTEM_MESSAGE>\n[Message] content=other\n</SYSTEM_MESSAGE>"))
            self.assertTrue(stop("SYSTEM_MESSAGE", "<SYSTEM_MESSAGE>\n[Message] content=phone\n</SYSTEM_MESSAGE>"))
            bridge.poll({"phone"})
            bridge.poll({"phone"})
            self.assertEqual([r["text"] for r in responses if r["key"] == "result"], ["answer"])
            bridge.receipt("phone", "delivered")
            with antigravity_bridge.ledger() as data:
                self.assertEqual(data["requests"]["phone"]["delivery"], "delivered")

    def test_installer_preserves_independent_hook_groups(self):
        with TemporaryDirectory() as temp:
            path = installer.target_path("antigravity", home=temp)
            path.parent.mkdir(parents=True)
            path.write_text('{"other":{"Stop":[]}}')
            installer.install_target("antigravity", ["Stop"], "notifyhub hook antigravity", home=temp)
            self.assertTrue(installer.has_target_hooks("antigravity", home=temp))
            installer.uninstall_target("antigravity", home=temp)
            self.assertEqual(json.loads(path.read_text()), {"other": {"Stop": []}})
