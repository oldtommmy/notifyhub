"""Relay between the shared Feishu transport and Antigravity's managed sidecar."""
import json
import os
from pathlib import Path
import hashlib
import subprocess
import time
from contextlib import contextmanager

from .state import state_lock
from .adapters.antigravity import session_metadata
from .transcripts import _read_events


def ledger_path():
    return Path.home() / ".local/state/notifyhub/antigravity-bridge.json"


@contextmanager
def ledger():
    path = ledger_path()
    with state_lock(path):
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        data = json.loads(old) if old else {"sessions": {}, "bindings": {}, "requests": {}}
        yield data
        new = json.dumps(data, ensure_ascii=False)
        if new != old:
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(new)
            os.replace(tmp, path)


def record_stop(payload, notification):
    """Match the exact sidecar input, even when lastUserInput is a desktop prompt."""
    path = payload.get("transcriptPath")
    rows = _read_events(path) if path else []
    boundary = next((i for i in range(len(rows) - 1, -1, -1)
                     if isinstance(rows[i], dict) and (
                         rows[i].get("type") == "USER_INPUT" or
                         rows[i].get("type") == "SYSTEM_MESSAGE"
                     )), None)
    with ledger() as data:
        session = notification.session_id
        data["sessions"][session] = {
            "id": session, "title": session_metadata(session).get("title") or notification.title, "project": notification.project,
            "cwd": notification.cwd, "model": notification.model, "status": "idle",
            "updated": time.time(),
        }
        if boundary is None:
            return False
        if rows[boundary].get("type") != "SYSTEM_MESSAGE":
            return False
        text = str(rows[boundary].get("content") or "").rstrip().removesuffix("</SYSTEM_MESSAGE>").rstrip()
        marker = text.splitlines()[-1] if text else ""
        for request in data["requests"].values():
            if request["session"] != session:
                continue
            if request.get("input_hash"):
                if int(rows[boundary].get("step_index", -1)) <= request.get("baseline_step", -1):
                    continue
                prefix, separator, content = text.partition(" content=")
                if not separator or hashlib.sha256(content.encode()).hexdigest() != request["input_hash"]:
                    continue
            elif marker != request.get("marker"):
                continue
            if request["state"] in ("submitting", "submitted", "unknown"):
                answers = [str(row["content"]) for row in rows[boundary + 1:] if isinstance(row, dict)
                           and row.get("type") == "PLANNER_RESPONSE" and row.get("status") == "DONE" and row.get("content")]
                request.update(state="finished", answer=(answers[-1][:12000] if answers else "本轮已结束，桌面没有记录文字回答。"),
                               ok=notification.color == "green")
                request.pop("prompt", None)
            return True
    return False


def run_sidecar(config_file):
    from .config import load_config
    while True:
        cfg = load_config(config_file).get("bridge", {})
        if not cfg.get("enabled") or not cfg.get("antigravity_enabled"):
            time.sleep(2)
            continue
        with ledger() as data:
            data["heartbeat"] = int(time.time())
            queued = next((r for r in data["requests"].values() if r["state"] == "queued"), None)
            if queued:
                # Claim durably before submitting. A crash or timeout never replays a prompt.
                queued["state"] = "submitting"
                prompt = queued.pop("prompt")
                transcript = Path.home() / ".gemini/antigravity/brain" / queued["session"] / ".system_generated/logs/transcript.jsonl"
                rows = _read_events(transcript)
                queued["baseline_step"] = max((row.get("step_index", -1) for row in rows if isinstance(row, dict)), default=-1)
        if queued:
            try:
                result = subprocess.run(["agentapi", "send-message", "--title=飞书消息", queued["session"], prompt],
                                        text=True, capture_output=True, timeout=12)
                values = json.loads(result.stdout)
                status = "submitted" if result.returncode == 0 and values.get("response") else "unknown"
            except (OSError, ValueError, subprocess.TimeoutExpired):
                status = "unknown"
            with ledger() as data:
                persisted = data["requests"][queued["id"]]
                if persisted["state"] == "submitting":
                    persisted["state"] = status
        time.sleep(2)


class AntigravityBridge:
    def __init__(self, config, reply):
        self.enabled = bool((config.get("bridge") or {}).get("antigravity_enabled"))
        self.reply = reply
        self.choices = {}

    def handle(self, msg):
        def reply(text, key="reply", view=None):
            self.reply({"type": "reply", "message_id": msg["message_id"], "key": key, "text": text, "view": view})

        if not self.enabled or msg.get("chat_type") != "p2p":
            return reply("Antigravity 续聊需要在配置中启用，并与机器人私聊。")
        chat, kind, arg = msg["chat_id"], msg["kind"], msg.get("payload")
        if kind == "task":
            text = str(arg[1]).strip()
            if text in ("会话", "切换会话", "会话列表"):
                kind, arg = "attach_list", ""
            elif text in ("上一页", "下一页"):
                page = self.choices.get(chat, (0, [], 0))[0]
                kind, arg = "attach_list", str(max(0, page + (1 if text == "下一页" else -1)))
            elif text == "状态":
                kind = "status"
            elif text.isdigit() and chat in self.choices:
                kind, arg = "attach", text

        def card(view, **fields):
            return {"type": view, "engine": "Antigravity", **fields}

        with ledger() as data:
            bound = data["bindings"].get(chat)
            sessions = data["sessions"]
            if kind == "attach_list":
                for session_id, info in sessions.items():
                    info.update(session_metadata(session_id))
                rows = sorted(sessions.values(), key=lambda row: row["updated"], reverse=True)
                pages = max(1, (len(rows) + 9) // 10)
                page = min(pages - 1, int(arg or 0))
                visible = rows[page * 10:(page + 1) * 10]
                self.choices[chat] = (page, [r["id"] for r in visible], time.monotonic() + 300)
                return reply("选择 Antigravity 会话。", view=card("sessions", page=page, pages=pages,
                             total=len(rows), sessions=[{**row, "current": row["id"] == bound} for row in visible]))
            if kind == "attach":
                target = str(arg or "")
                if target.isdigit():
                    _, ids, expiry = self.choices.get(chat, (0, [], 0))
                    index = int(target) - 1
                    target = ids[index] if 0 <= index < len(ids) and expiry > time.monotonic() else ""
                if target not in sessions:
                    return reply("会话未找到。请发「Antigravity会话」刷新列表。")
                data["bindings"][chat] = target
                self.choices.pop(chat, None)
                return reply("已绑定 Antigravity 会话。", view=card("bound", session=sessions[target]))
            if bound not in sessions:
                return reply("请先发「Antigravity会话」并选择会话。")
            if kind == "status":
                return reply("当前 Antigravity 会话。", view=card("status", session=sessions[bound]))
            if kind != "task":
                return reply("发「帮助」查看使用方式。")
            if time.time() - data.get("heartbeat", 0) > 15:
                return reply("Antigravity 续聊未连接，请确认桌面 App 与 NotifyHub Sidecar 正在运行。")
            identity = msg["message_id"]
            if identity in data["requests"]:
                return reply("这条消息已登记。", "duplicate")
            if any(r["session"] == bound and r["state"] in ("queued", "submitting", "submitted", "unknown")
                   for r in data["requests"].values()):
                return reply("该会话有一条待确认的请求，请在桌面查看状态。")
            data["requests"][identity] = {"id": identity, "session": bound, "state": "queued",
                                          "created": time.time(), "prompt": text,
                                          "input_hash": hashlib.sha256(text.encode()).hexdigest(), "chat_id": chat}
        reply("已加入发送队列，请保持 Antigravity 桌面 App 运行。", "accepted")

    def receipt(self, identity, status):
        with ledger() as data:
            request = data["requests"].get(identity)
            if request:
                request["delivery"] = status

    def poll(self, known):
        if not self.enabled:
            return
        with ledger() as data:
            for identity, req in data["requests"].items():
                if identity not in known or req["state"] != "finished":
                    continue
                req["state"] = "handed_off"
                self.reply({"type": "reply", "message_id": identity, "request_id": identity, "key": "result", "text": req["answer"],
                            "view": {"type": "result", "engine": "Antigravity", "ok": req["ok"],
                                     "session": {**data["sessions"][req["session"]],
                                                 "status": "completed" if req["ok"] else "failed"},
                                     "text": req["answer"], "duration": round(time.time() - req["created"])}})
