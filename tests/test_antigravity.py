import json
import time
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notifyhub import antigravity_bridge, cli, installer
from notifyhub.adapters.antigravity import parse


class AntigravityTests(unittest.TestCase):
    def test_continuation_tracks_stop_with_completion_notifications_disabled(self):
        args = SimpleNamespace(source="antigravity", config=None, verbose=False, dry_run=False)
        config = {"antigravity": {"enabled": False, "track_sessions": True}}
        payload = {"conversationId": "phone-conversation", "fullyIdle": True, "executionNum": 1,
                   "terminationReason": "NO_TOOL_CALL"}
        with patch.object(cli, "load_config", return_value=config), patch.object(cli, "_read_payload", return_value=payload), \
                patch.object(cli, "_logger"), patch.object(cli, "dispatch") as dispatch, \
                patch.object(antigravity_bridge, "record_stop", return_value=False) as record:
            self.assertEqual(cli._process_hook(args), 0)
        record.assert_called_once()
        self.assertEqual(record.call_args.args[1].session_id, "phone-conversation")
        dispatch.assert_not_called()

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
                data["heartbeat"] = int(time.time())
            bridge.handle({**msg, "kind": "task", "payload": ["dsh", "phone"], "message_id": "phone"})
            answer = "完整回答" + "长" * 12_001 + "末尾"
            with antigravity_bridge.ledger() as data:
                request = data["requests"]["phone"]
                request["state"] = "submitted"
                request["baseline_step"] = 2
            def stop(kind, input_message, intervening=None, boundary_step=3):
                rows = [{"type": kind, "content": input_message, "step_index": boundary_step}]
                if intervening is not None:
                    rows.append({"type": "SYSTEM_MESSAGE", "content": intervening, "step_index": 4})
                rows.append({"type": "PLANNER_RESPONSE", "status": "DONE", "content": answer})
                transcript.write_text("\n".join(map(json.dumps, rows)))
                return antigravity_bridge.record_stop(event, notification)

            self.assertFalse(stop("USER_INPUT", "desktop"))
            self.assertFalse(stop("SYSTEM_MESSAGE", "<SYSTEM_MESSAGE>\n[Message] content=other\n</SYSTEM_MESSAGE>"))
            phone_input = "<SYSTEM_MESSAGE>\n[Message] content=phone\n</SYSTEM_MESSAGE>"
            self.assertFalse(stop("SYSTEM_MESSAGE", phone_input, boundary_step=2))
            self.assertFalse(stop("SYSTEM_MESSAGE", phone_input, "[Message] content=other external input"))
            host_facts = ("<SYSTEM_MESSAGE>\n[Message] sender=system priority=MESSAGE_PRIORITY_LOW "
                          "content=[BROWSER_HOST_FACTS subagent=browser] Host-observed facts.\n</SYSTEM_MESSAGE>")
            self.assertFalse(stop("SYSTEM_MESSAGE", phone_input, host_facts.replace("LOW", "HIGH")))
            self.assertTrue(stop("SYSTEM_MESSAGE", phone_input, host_facts))
            self.assertTrue(stop("SYSTEM_MESSAGE", phone_input, "Internal tool instructions"))
            self.assertTrue(stop("SYSTEM_MESSAGE", phone_input, "Internal tool instructions"))
            result = bridge.take_result({"phone": "chat"})
            self.assertEqual(result["text"], answer)
            self.assertTrue(result["view"]["answer_complete"])
            self.assertIsNone(bridge.take_result({"phone": "chat"}))
            bridge.receipt("phone", "delivered")
            with antigravity_bridge.ledger() as data:
                self.assertEqual(data["requests"]["phone"]["delivery"], "delivered")

    def test_later_desktop_turn_releases_unconfirmed_wait_without_requeue(self):
        with TemporaryDirectory() as temp, patch.object(antigravity_bridge.Path, "home", return_value=Path(temp)):
            transcript = Path(temp) / ".gemini/antigravity/brain/session/.system_generated/logs/transcript.jsonl"
            transcript.parent.mkdir(parents=True)
            request = {"id": "om_uncertain", "session": "session", "state": "unknown",
                       "created": 100, "baseline_step": 10, "input_hash": "original"}
            with antigravity_bridge.ledger() as data:
                data["sessions"]["session"] = {"id": "session", "title": "test"}
                data["bindings"]["chat"] = "session"
                data["requests"][request["id"]] = request
                data["heartbeat"] = time.time()
            responses = []
            bridge = antigravity_bridge.AntigravityBridge({"bridge": {"antigravity_enabled": True}}, responses.append)
            inbound = {"chat_id": "chat", "chat_type": "p2p", "kind": "task",
                       "payload": ["dsh", "new prompt"], "message_id": "om_new"}

            bridge.handle(inbound)
            self.assertIn("待确认", responses[-1]["text"])
            transcript.write_text(json.dumps({"type": "USER_INPUT", "status": "DONE", "step_index": 11,
                                              "created_at": "1970-01-01T00:03:00Z"}))
            bridge.handle(inbound)
            self.assertEqual(responses[-1]["key"], "accepted")
            with antigravity_bridge.ledger() as data:
                self.assertEqual(data["requests"]["om_uncertain"]["state"], "unconfirmed_after_desktop_turn")
                self.assertEqual(data["requests"]["om_uncertain"]["input_hash"], "original")
                self.assertEqual(data["requests"]["om_new"]["state"], "queued")

    def test_result_recovery_checks_bot_and_sender_and_keeps_attempts_at_most_once(self):
        with TemporaryDirectory() as temp, patch.object(antigravity_bridge, "ledger_path", return_value=Path(temp) / "state.json"):
            config = {"bridge": {"antigravity_enabled": True, "allow_open_ids": ["ou_owner"]},
                      "channels": {"feishu_app": {"app_id": "cli_bot"}}}
            request = {"session": "session", "state": "finished", "answer": "phone answer",
                       "ok": True, "created": time.time(), "chat_id": "oc_owner"}
            with antigravity_bridge.ledger() as data:
                data["sessions"]["session"] = {"id": "session", "title": "test"}
                data["requests"] = {
                    "om_legacy": {**request},
                    "om_other_bot": {**request, "app_id": "cli_other", "sender_id": "ou_owner"},
                    "om_removed": {**request, "app_id": "cli_bot", "sender_id": "ou_removed"},
                    "om_owner": {**request, "app_id": "cli_bot", "sender_id": "ou_owner"},
                }
            bridge = antigravity_bridge.AntigravityBridge(config, self.fail)
            reply = bridge.take_result({})
            self.assertEqual(reply["message_id"], "om_owner")
            self.assertEqual(reply["chat_id"], "oc_owner")
            self.assertEqual(reply["text"], "phone answer")
            bridge.receipt("om_owner", "unknown")
            restarted = antigravity_bridge.AntigravityBridge(config, self.fail)
            self.assertIsNone(restarted.take_result({}))
            with antigravity_bridge.ledger() as data:
                self.assertEqual(data["requests"]["om_owner"]["delivery"], "unknown")
                self.assertEqual(data["requests"]["om_legacy"]["state"], "finished")

    def test_installer_preserves_independent_hook_groups(self):
        with TemporaryDirectory() as temp:
            path = installer.target_path("antigravity", home=temp)
            path.parent.mkdir(parents=True)
            path.write_text('{"other":{"Stop":[]}}')
            installer.install_target("antigravity", ["Stop"], "notifyhub hook antigravity", home=temp)
            self.assertTrue(installer.has_target_hooks("antigravity", home=temp))
            installer.uninstall_target("antigravity", home=temp)
            self.assertEqual(json.loads(path.read_text()), {"other": {"Stop": []}})
