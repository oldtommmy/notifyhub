import hmac
import json
import os
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import antigravity_install, dsh_install, gateway_install, installer, native_install, quota, service, updates
from .channels import channels as channel_names
from .channels.feishu_app import RECEIVE_ID_TYPES
from .codex_meta import codex_home, find_db
from .config import config_path, deep_merge, load_config, save_config
from .events import Notification
from .feishu_discovery import discover as discover_receivers
from .log import get_logger
from .outbox import outbox_for
from .router import dispatch
from .runtime import BRAND_LOGOS, hook_command, launcher_path
from .state import state_lock

INDEX_PATH = Path(__file__).resolve().parent / "gui_assets" / "index.html"
STATIC = {
    "/assets/style.css": (INDEX_PATH.with_name("style.css"), "text/css; charset=utf-8"),
    "/assets/app.js": (INDEX_PATH.with_name("app.js"), "text/javascript; charset=utf-8"),
    "/assets/maple-mono-regular.woff2": (INDEX_PATH.parent / "fonts/maple-mono-regular.woff2", "font/woff2"),
    "/assets/maple-mono-semibold.woff2": (INDEX_PATH.parent / "fonts/maple-mono-semibold.woff2", "font/woff2"),
    **{"/assets/" + name: (launcher_path().parents[1] / "assets" / name, "image/png")
       for name in BRAND_LOGOS},
}
ALLOWED_EVENTS = ["Stop", "SubagentStop", "Interrupt", "SessionEnd"]


def build_sample():
    return Notification(
        source="codex",
        event="Stop",
        status_text="🎉 Goal 完成",
        color="green",
        title="给 Codex 加完成通知",
        project="codex-notice-plugin",
        cwd=str(Path.home() / "Desktop" / "Codex" / "cmdcode" / "codex-notice-plugin"),
        session_id="01a1d5f7-e17d-7fe2-9560-28cd55e3ebee",
        model="gpt-6.1-sol",
        goal_status="complete",
        goal_objective="实现 notifyhub 的通知链路",
        goal_tokens_used=12400,
        goal_token_budget=50000,
        goal_time_used_seconds=185,
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
    app_credentials_ready = all(feishu_app.get(key) for key in ("app_id", "app_secret", "receive_id"))
    outbox = outbox_for(config)
    dsh = dsh_install.status()
    native = native_install.status(config_file)
    agents = {name: {"installed": installer.has_target_hooks(name)}
              for name in installer.TARGETS}
    agents["dsh"] = {"installed": dsh["profile_installed"], "profiles": dsh["profiles"],
                     "cli_found": dsh["dsh_found"], "desktop_app": dsh["desktop_app"]}
    sidecar = installer.target_path("antigravity").parent / "sidecars/notifyhub/sidecar.json"
    gateway = service.status(gateway_install.LABEL)

    return {
        "ok": True,
        "config_path": str(config_file),
        "config_exists": Path(config_file).exists(),
        "agents": agents,
        "channels": {
            "feishu": {
                "enabled": bool(feishu.get("enabled")),
                "configured": bool(feishu.get("enabled") and feishu.get("webhook_url")),
                "message_type": feishu.get("message_type", "card"),
                "at_all": bool(feishu.get("at_all")),
            },
            "feishu_app": {
                "enabled": bool(feishu_app.get("enabled")),
                "configured": bool(feishu_app.get("enabled") and app_credentials_ready),
                "credentials_ready": app_credentials_ready,
                "receive_id_type": feishu_app.get("receive_id_type", "open_id"),
                "receive_id": feishu_app.get("receive_id", ""),
            },
            "imessage": {
                "enabled": bool(imessage.get("enabled")),
                "configured": bool(imessage.get("enabled") and imessage.get("to")),
            },
        },
        "codex": {
            "events": events,
            "hooks_installed": agents["codex"]["installed"],
            "codex_home": str(codex_home()),
            "state_db": str(state_db) if state_db else "",
            "goals_db": str(goals_db) if goals_db else "",
        },
        "dsh": {**dsh, "native": native},
        "continuation": {
            **native, "legacy": native["configured"] and not native["independent_gateway"],
            "gateway_configured": gateway_install.gateway_path(config_file).exists(),
            "gateway_running": bool(gateway["pid"]), "service_supported": service._is_macos(),
            "gateway_supported": os.name != "nt",
            "antigravity_prepared": sidecar.is_file(),
        },
        "log_path": config.get("log_path", ""),
        "state_path": config.get("state_path", ""),
        "outbox": {"enabled": outbox.enabled, **outbox.stats()},
    }


def install_dsh_payload(config_file, body):
    mode = body.get("mode", "notify")
    if mode == "native":
        return native_install.install(config_file, body)
    if mode != "notify":
        return {"ok": False, "error": "未知的 DSH 安装模式"}
    return dsh_install.install(str(body.get("profile") or "web"))


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
    for agent in (*installer.TARGETS, "dsh"):
        settings = config.get(agent, {})
        for key, label, default in (("max_message_chars", "回答", 600), ("max_prompt_chars", "提问", 160)):
            limit = settings.get(key, default)
            if isinstance(limit, str) and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*}", limit):
                continue
            if isinstance(limit, str) and re.fullmatch(r"[0-9]{1,5}", limit):
                limit = int(limit)
            if type(limit) is not int or not 0 <= limit <= 10000:
                errors.append(f"{agent} 的{label}长度必须在 0 到 10000 之间")

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
    target = Path(config_file).expanduser()
    with state_lock(target):
        current = load_config(target, expand=False)
        merged = deep_merge(current, incoming)
        errors = validate_config(merged)
        if errors:
            raise ValueError("；".join(errors))
        if merged != current or not target.exists():
            installer._backup(target)
            save_config(merged, target)
        return merged


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


def test_payload(config_file, channel=None):
    if channel is not None and channel not in channel_names():
        raise ValueError("未知的通知渠道。")
    config = load_config(config_file)
    logger = get_logger(config.get("log_path", ""))
    results = dispatch(build_sample(), config, logger, force=True, only_channel=channel)
    if not results:
        return {"ok": False, "error": "没有启用且配置完整的通知渠道"}
    if not any(ok for _, ok, _ in results):
        return {"ok": False, "error": "测试通知发送失败，请在「运行与更新」检查日志和发件箱。", "results": results}
    return {"ok": True, "results": results}


def hook_payload(config_file, target, uninstall=False):
    if target not in installer.TARGETS:
        raise ValueError("未知的 Agent 接入。")
    if uninstall:
        result = installer.uninstall_target(target)
        return {"ok": True, "path": str(result["path"]), "removed": result["removed"]}
    events = load_config(config_file).get(target, {}).get("events", ["Stop"])
    result = installer.install_target(target, events, hook_command(config_file, target))
    return {"ok": True, "path": str(result["path"]), "events": result["events"],
            "backup": str(result["backup"] or ""), "command": result["command"]}


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
        if parsed.path in STATIC:
            path, media_type = STATIC[parsed.path]
            try:
                self._send(path.read_bytes(), 200, media_type)
            except FileNotFoundError:
                self._error("not found", 404)
            return

        if not self._authorized():
            self._error("unauthorized", 403)
            return

        try:
            if parsed.path == "/api/status":
                self._json(status_payload(self.server.config_file))
            elif parsed.path == "/api/config":
                self._json({"ok": True, "config": load_config(self.server.config_file, expand=False)})
            elif parsed.path == "/api/quota":
                refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] in ("1", "true", "yes")
                self._json(quota_payload(refresh))
            elif parsed.path == "/api/update":
                refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] == "1"
                self._json(updates.check(refresh=refresh))
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
                self._json(test_payload(self.server.config_file, body.get("channel")))
            elif parsed.path == "/api/hooks/install":
                self._json(hook_payload(self.server.config_file, body.get("target", "codex")))
            elif parsed.path == "/api/hooks/uninstall":
                self._json(hook_payload(self.server.config_file, body.get("target", "codex"), uninstall=True))
            elif parsed.path == "/api/hooks/install-dsh":
                profile = str(body.get("profile") or "web")
                result = install_dsh_payload(self.server.config_file, body)
                result["profile"] = profile
                self._json(result, 200 if result.get("ok") else 400)
            elif parsed.path == "/api/continuation":
                path = gateway_install.save_options(self.server.config_file, body)
                self._json({"ok": True, "path": str(path)})
            elif parsed.path == "/api/continuation/gateway":
                if body.get("stop") is True:
                    gateway_install.stop_gateway()
                else:
                    gateway_install.install_gateway(gateway_install.gateway_path(self.server.config_file))
                self._json({"ok": True})
            elif parsed.path == "/api/continuation/dsh":
                result = native_install.install_gateway_adapter(self.server.config_file)
                self._json(result, 200 if result.get("ok") else 400)
            elif parsed.path == "/api/continuation/antigravity":
                target = gateway_install.gateway_path(self.server.config_file)
                if not target.exists() or not load_config(target)["bridge"].get("antigravity_enabled"):
                    raise ValueError("请先在续聊设置中选择 Antigravity 并保存。")
                antigravity_install.configure_sidecar(self.server.config_file, target)
                self._json({"ok": True})
            elif parsed.path == "/api/update":
                self._json(updates.apply(str(body.get("version") or "")))
            elif parsed.path == "/api/feishu/receivers":
                config = load_config(self.server.config_file)
                saved = config["channels"]["feishu_app"]
                app_id = str(body.get("app_id") or saved.get("app_id") or "").strip()
                if app_id.startswith("${"):
                    app_id = str(saved.get("app_id") or "")
                app_secret = str(body.get("app_secret") or "").strip()
                if not app_secret or app_secret.startswith("${"):
                    app_secret = str(saved.get("app_secret") or "")
                base_url = str(body.get("base_url") or saved.get("base_url") or "https://open.feishu.cn")
                if not app_id or not app_secret:
                    self._error("App ID / App Secret 不能为空", 400)
                    return
                try:
                    result = discover_receivers(app_id, app_secret, base_url)
                except Exception as error:
                    self._json({"ok": False, "error": str(error), "candidates": [], "warnings": []}, 400)
                    return
                self._json(result)
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
