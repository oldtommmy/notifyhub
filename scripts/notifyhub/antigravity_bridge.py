"""Relay between the shared Feishu transport and Antigravity's managed sidecar."""
import json
import os
from pathlib import Path
import hashlib
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime

from .bridge import is_allowed
from .state import state_lock
from .adapters.antigravity import session_metadata
from .transcripts import _read_events

SUBMISSION_TIMEOUT = 12
SUBMISSION_STATES = ('submitting', 'submitted', 'unknown')


def ledger_path():
    return Path.home() / ".local/state/notifyhub/antigravity-bridge.json"


def _system_text(row):
    return str(row.get('content') or '').rstrip().removesuffix('</SYSTEM_MESSAGE>').rstrip()


def _injected_text(text):
    header, separator, content = text.partition(' content=')
    line = header.splitlines()[-1] if header else ''
    if separator and line.split(' ', 1)[0] == '[Message]' and (' sender=' not in line or ' sender=system ' in line):
        return content
    return None


def _matches_request(row, request):
    if row.get('type') != 'SYSTEM_MESSAGE':
        return False
    step = row.get('step_index', -1)
    if request.get('input_step') is not None and step != request['input_step']:
        return False
    text = _system_text(row)
    if request.get('input_hash'):
        content = _injected_text(text)
        return (step > request.get('baseline_step', -1) and content is not None
                and hashlib.sha256(content.encode()).hexdigest() == request['input_hash'])
    return bool(request.get('marker') and text and text.splitlines()[-1] == request['marker'])


def retire_previous_requests(data, session):
    """Release a stale wait while preserving the original submission outcome."""
    pending = [r for r in data['requests'].values() if r['session'] == session and r['state'] in SUBMISSION_STATES]
    if not pending:
        return
    path = Path.home() / '.gemini/antigravity/brain' / session / '.system_generated/logs/transcript.jsonl'
    rows = [row for row in _read_events(path) if isinstance(row, dict)]
    latest_desktop = next((row for row in reversed(rows)
                           if row.get('type') == 'USER_INPUT' and row.get('status') == 'DONE'), None)
    if latest_desktop is None:
        return
    desktop_step = latest_desktop.get('step_index', -1)
    try:
        desktop_time = datetime.fromisoformat(str(latest_desktop.get('created_at', '')).replace('Z', '+00:00')).timestamp()
    except ValueError:
        desktop_time = 0
    for request in pending:
        matched = next((row for row in reversed(rows) if _matches_request(row, request)), None)
        if matched is not None:
            if matched.get('step_index', -1) < desktop_step:
                request.update(state='ended_before_desktop_turn', input_step=matched['step_index'])
        elif ('baseline_step' in request and desktop_step > request['baseline_step']
              and desktop_time > request.get('submission_started_at', request['created']) + SUBMISSION_TIMEOUT):
            request.update(state='unconfirmed_after_desktop_turn')


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
    rows = [row for row in _read_events(path) if isinstance(row, dict)] if path else []
    with ledger() as data:
        session = notification.session_id
        data["sessions"][session] = {
            "id": session, "title": session_metadata(session).get("title") or notification.title, "project": notification.project,
            "cwd": notification.cwd, "model": notification.model, "status": "idle",
            "updated": time.time(),
        }
        requests = [r for r in data['requests'].values() if r['session'] == session]
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if row.get('type') == 'USER_INPUT':
                break
            if row.get('type') != 'SYSTEM_MESSAGE':
                continue
            for request in reversed(requests):
                state = request['state']
                if state not in (*SUBMISSION_STATES, 'finished', 'handed_off') or not _matches_request(row, request):
                    continue
                if state in SUBMISSION_STATES:
                    answer = next((str(step['content']) for step in reversed(rows[index + 1:])
                                   if step.get('type') == 'PLANNER_RESPONSE' and step.get('status') == 'DONE'
                                   and step.get('content')), '本轮已结束，桌面没有记录文字回答。')
                    request.update(state='finished', input_step=row.get('step_index'), answer=answer[:12000],
                                   ok=notification.color == 'green')
                    request.pop('prompt', None)
                return True
            # A different external message starts its own turn. Internal system
            # instructions can appear after the phone input within the same turn.
            if _injected_text(_system_text(row)) is not None:
                break
    return False


def run_sidecar(config_file):
    from .config import load_config
    while True:
        # The app owns this process and its injected connection credentials.
        # An orphan from an old app instance must never claim new requests.
        if os.getppid() == 1:
            return 0
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
                queued['submission_started_at'] = time.time()
                prompt = queued.pop("prompt")
                transcript = Path.home() / ".gemini/antigravity/brain" / queued["session"] / ".system_generated/logs/transcript.jsonl"
                rows = _read_events(transcript)
                queued["baseline_step"] = max((row.get("step_index", -1) for row in rows if isinstance(row, dict)), default=-1)
        if queued:
            try:
                result = subprocess.run(["agentapi", "send-message", "--title=飞书消息", queued["session"], prompt],
                                        text=True, capture_output=True, timeout=SUBMISSION_TIMEOUT)
                values = json.loads(result.stdout)
                status = "submitted" if result.returncode == 0 and values.get("response") else "unknown"
                error_type = 'agentapi_error' if values.get('error') else ''
            except (OSError, ValueError, subprocess.TimeoutExpired):
                status = "unknown"
                error_type = 'submission_timeout_or_transport_error'
            with ledger() as data:
                persisted = data["requests"][queued["id"]]
                if persisted["state"] == "submitting":
                    persisted["state"] = status
                    persisted['submission_error'] = error_type
                    persisted['submitted_by_pid'] = os.getpid()
        time.sleep(2)


class AntigravityBridge:
    def __init__(self, config, reply):
        self.enabled = bool((config.get("bridge") or {}).get("antigravity_enabled"))
        self.config = config
        self.app_id = config.get('channels', {}).get('feishu_app', {}).get('app_id', '')
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
            retire_previous_requests(data, bound)
            if any(r["session"] == bound and r["state"] in ("queued", *SUBMISSION_STATES)
                   for r in data["requests"].values()):
                return reply("该会话有一条待确认的请求，请在桌面查看状态。")
            data["requests"][identity] = {"id": identity, "session": bound, "state": "queued",
                                          "created": time.time(), "prompt": text,
                                          "input_hash": hashlib.sha256(text.encode()).hexdigest(), "chat_id": chat,
                                          "sender_id": msg.get("sender_id"), "app_id": self.app_id}
        reply("已加入发送队列，请保持 Antigravity 桌面 App 运行。", "accepted")

    def receipt(self, identity, status):
        with ledger() as data:
            request = data["requests"].get(identity)
            if request:
                request["delivery"] = status

    def take_result(self, known):
        if not self.enabled:
            return
        with ledger() as data:
            for identity, req in data["requests"].items():
                if req["state"] != "finished":
                    continue
                chat = known.get(identity)
                if req.get('app_id'):
                    # Recover a never-attempted result after a restart only
                    # for the same bot and a still-authorized private sender.
                    if req['app_id'] != self.app_id or not is_allowed(req.get('sender_id'), self.config):
                        continue
                    if chat is None and identity.startswith('om_'):
                        chat = req.get('chat_id')
                if not chat or (req.get('chat_id') and req['chat_id'] != chat):
                    continue
                # Reserve durably before leaving this lock. A crash during a
                # network attempt leaves an uncertain receipt, not a retry.
                req.update(state="handed_off", delivery="attempting")
                return {"type": "reply", "message_id": identity, "request_id": identity,
                        "chat_id": chat, "key": "result", "text": req["answer"],
                        "view": {"type": "result", "engine": "Antigravity", "ok": req["ok"],
                                 "session": {**data["sessions"][req["session"]],
                                             "status": "completed" if req["ok"] else "failed"},
                                 "text": req["answer"], "duration": round(time.time() - req["created"])}}
