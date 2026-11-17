import hmac
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dsh_install, quota
from .channels.feishu_app import RECEIVE_ID_TYPES
from .codex_meta import codex_home, find_db
from .config import config_path, deep_merge, load_config, save_config
from .events import Notification
from .installer import has_notifyhub_hook, install_codex, uninstall_codex
from .log import get_logger
from .render import build_card, render_text
from .router import dispatch
from .runtime import hook_command

INDEX_PATH = Path(__file__).resolve().parent / "gui_assets" / "index.html"
ALLOWED_EVENTS = ["Stop", "SubagentStop", "Interrupt", "SessionEnd"]
GOAL_CHOICES = {"active", "complete", "blocked", "none"}


def build_sample(goal="active"):
    if goal == "none":
        status_text, color, goal_status = "✅ 本轮完成", "green", ""
    elif goal == "complete":
        status_text, color, goal_status = "🎉 Goal 完成", "green", "complete"
    elif goal == "blocked":
        status_text, color, goal_status = "⛔ Goal 受阻", "red", "blocked"
    else:
        status_text, color, goal_status = "🔁 Goal 进行中", "blue", "active"

    return Notification(
        source="codex",
        event="Stop",
        status_text=status_text,
        color=color,
        title="给 Codex 加完成通知",
        project="codex-notice-plugin",
        cwd=str(Path.home() / "Desktop" / "Codex" / "cmdcode" / "codex-notice-plugin"),
        session_id="01a1d5f7-e17d-7fe2-9560-28cd55e3ebee",
        model="gpt-6.1-sol",
        goal_status=goal_status,
        goal_objective="实现 notifyhub 的通知链路",
        goal_tokens_used=12400 if goal_status else None,
        goal_token_budget=50000 if goal_status else None,
        goal_time_used_seconds=185 if goal_status else None,
        thread_tokens_used=31800,
        quota_line="剩余额度: 21%",
        show_quota=True,
        recent_prompt="帮我把 Codex 的通知接到飞书，最好能看到剩余额度。",
        last_message="已经完成 Stop hook 接入。\n\n**改动**\n- 接入飞书应用机器人\n- 增加剩余额度显示\n\n`notifyhub test` 已通过。",
    )


def status_payload(config_file):
    config = load_config(config_file)
    feishu = config["channels"]["feishu"]
    feishu_app = config["channels"]["feishu_app"]
    imessage = config["channels"]["imessage"]
    events = config["codex"].get("events", ["Stop"])
    state_db = find_db("state")
    goals_db = find_db("goals")

    return {
        "ok": True,
        "config_path": str(config_file),
        "config_exists": Path(config_file).exists(),
        "channels": {
            "feishu": {
                "enabled": bool(feishu.get("enabled")),
                "configured": bool(feishu.get("enabled") and feishu.get("webhook_url")),
                "message_type": feishu.get("message_type", "card"),
                "at_all": bool(feishu.get("at_all")),
            },
            "feishu_app": {
                "enabled": bool(feishu_app.get("enabled")),
                "configured": bool(
                    feishu_app.get("enabled")
                    and feishu_app.get("app_id")
                    and feishu_app.get("app_secret")
                    and feishu_app.get("receive_id")
                ),
                "receive_id_type": feishu_app.get("receive_id_type", "email"),
                "receive_id": feishu_app.get("receive_id", ""),
            },
            "imessage": {
                "enabled": bool(imessage.get("enabled")),
                "configured": bool(imessage.get("enabled") and imessage.get("to")),
            },
        },
        "codex": {
            "events": events,
            "hooks_installed": has_notifyhub_hook(codex_home(), events),
            "codex_home": str(codex_home()),
            "state_db": str(state_db) if state_db else "",
            "goals_db": str(goals_db) if goals_db else "",
        },
        "dsh": dsh_install.status("web"),
        "log_path": config.get("log_path", ""),
    }


def validate_config(config):
    errors = []
    if not isinstance(config, dict):
        return ["配置必须是一个对象"]

    channels = config.get("channels", {})
    feishu = channels.get("feishu", {})
    url = feishu.get("webhook_url", "")
    if url and not (str(url).startswith(("http://", "https://")) or str(url).startswith("${")):
        errors.append("飞书 webhook 必须以 http:// 或 https:// 开头")
    if feishu.get("message_type") not in ("card", "text"):
        errors.append("飞书消息格式必须是 card 或 text")

    feishu_app = channels.get("feishu_app", {})
    if feishu_app.get("message_type") not in ("card", "text"):
        errors.append("飞书应用消息格式必须是 card 或 text")
    if feishu_app.get("receive_id_type") not in RECEIVE_ID_TYPES:
        errors.append("飞书应用接收人类型不支持")

    codex = config.get("codex", {})
    events = codex.get("events", [])
    if not isinstance(events, list) or any(event not in ALLOWED_EVENTS for event in events):
        errors.append("包含不支持的 Codex 事件")
    elif not events:
        errors.append("至少选择一个 Codex 事件")
    max_chars = codex.get("max_message_chars", 600)
    if not isinstance(max_chars, int) or not 0 <= max_chars <= 10000:
        errors.append("消息截断长度必须在 0 到 10000 之间")

    quiet = config.get("filters", {}).get("quiet_hours", {})
    if quiet.get("enabled"):
        for key, label in (("start", "免打扰开始时间"), ("end", "免打扰结束时间")):
            value = str(quiet.get(key, ""))
            try:
                hour, minute = (int(part) for part in value.split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
            except ValueError:
                errors.append(f"{label}格式应为 HH:MM")

    return errors


def save_config_payload(config_file, incoming):
    if not isinstance(incoming, dict):
        raise ValueError("配置必须是一个对象")
    merged = deep_merge(load_config(config_file, expand=False), incoming)
    errors = validate_config(merged)
    if errors:
        raise ValueError("；".join(errors))
    save_config(merged, config_file)
    return load_config(config_file, expand=False)


def quota_payload(refresh=False):
    data = quota.get(max_age=600, timeout=8, refresh=refresh)
    if not data:
        return {"ok": False, "error": "无法读取 Codex 额度"}
    return {
        "ok": True,
        "line": quota.format_line(data),
        "age_seconds": quota.age_seconds(data),
        "stale": bool(data.get("stale")),
    }


def test_payload(config_file):
    config = load_config(config_file)
    logger = get_logger(config.get("log_path", ""))
    results = dispatch(build_sample("complete"), config, logger, force=True)
    if not results:
        return {"ok": False, "error": "没有启用且配置完整的通知渠道"}
    return {"ok": any(ok for _, ok, _ in results), "results": results}


def preview_payload(config_file, goal="active"):
    config = load_config(config_file)
    sample = build_sample(goal if goal in GOAL_CHOICES else "active")
    sample.show_quota = bool(config["codex"].get("include_remaining_quota", True))
    if not config["codex"].get("include_recent_prompt", True):
        sample.recent_prompt = ""
    if sample.show_quota:
        line = quota.format_line(quota.cached())
        if line:
            sample.quota_line = line
    feishu = config["channels"]["feishu"]
    feishu_app = config["channels"]["feishu_app"]
    at_all = feishu.get("at_all") if feishu.get("enabled") else feishu_app.get("at_all", False)
    return {
        "ok": True,
        "text": render_text(sample),
        "card": build_card(sample, at_all=at_all),
    }


class GuiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "notifyhub-gui/0.1"

    def log_message(self, fmt, *args):
        return

    def _authorized(self):
        token = self.headers.get("X-Notifyhub-Token", "")
        if not hmac.compare_digest(token, self.server.token):
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc != self.headers.get("Host", ""):
                return False
        return True

    def _send(self, body, status=200, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"), status)

    def _error(self, message, status=400):
        self._json({"ok": False, "error": str(message)}, status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            token = parse_qs(parsed.query).get("token", [""])[0]
            if not hmac.compare_digest(token, self.server.token):
                self._send("invalid token".encode("utf-8"), 403, "text/plain; charset=utf-8")
                return
            html = self.server.index_html.replace("__TOKEN__", token)
            self._send(html.encode("utf-8"), 200, "text/html; charset=utf-8")
            return

        if not self._authorized():
            self._error("unauthorized", 403)
            return

        try:
            if parsed.path == "/api/status":
                self._json(status_payload(self.server.config_file))
            elif parsed.path == "/api/config":
                self._json({"ok": True, "config": load_config(self.server.config_file, expand=False)})
            elif parsed.path == "/api/preview":
                goal = parse_qs(parsed.query).get("goal", ["active"])[0]
                self._json(preview_payload(self.server.config_file, goal))
            elif parsed.path == "/api/quota":
                refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] in ("1", "true", "yes")
                self._json(quota_payload(refresh))
            else:
                self._error("not found", 404)
        except Exception as error:
            self._error(error, 500)

    def do_POST(self):
        if not self._authorized():
            self._error("unauthorized", 403)
            return

        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/config":
                incoming = body.get("config", body)
                saved = save_config_payload(self.server.config_file, incoming)
                self._json({"ok": True, "config": saved})
            elif parsed.path == "/api/test":
                self._json(test_payload(self.server.config_file))
            elif parsed.path == "/api/hooks/install":
                config = load_config(self.server.config_file)
                events = config["codex"].get("events", ["Stop"])
                result = install_codex(codex_home(), events, hook_command(self.server.config_file))
                self._json(
                    {
                        "ok": True,
                        "path": str(result["path"]),
                        "events": result["events"],
                        "backup": str(result["backup"]) if result["backup"] else "",
                        "command": result["command"],
                    }
                )
            elif parsed.path == "/api/hooks/uninstall":
                result = uninstall_codex(codex_home())
                self._json({"ok": True, "removed": result["removed"], "path": str(result["path"])})
            elif parsed.path == "/api/hooks/install-dsh":
                result = dsh_install.install("web")
                self._json(result, 200 if result.get("ok") else 400)
            else:
                self._error("not found", 404)
        except ValueError as error:
            self._error(error, 400)
        except Exception as error:
            self._error(error, 500)


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config_file, token, index_html):
        super().__init__(address, GuiHandler)
        self.config_file = config_file
        self.token = token
        self.index_html = index_html


def run_gui(config_file, host="127.0.0.1", port=8765, open_browser=True):
    config_file = Path(config_file).expanduser()
    index_html = INDEX_PATH.read_text(encoding="utf-8")
    token = secrets.token_urlsafe(32)

    try:
        server = GuiServer((host, port), config_file, token, index_html)
    except OSError:
        server = GuiServer((host, 0), config_file, token, index_html)

    actual_port = server.server_address[1]
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{browser_host}:{actual_port}/?token={token}"

    print(f"notifyhub GUI: {url}")
    print("Ctrl+C 退出")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
