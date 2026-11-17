import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, dsh_install
from .adapters import get_adapter, sources
from .channels import channels as channel_names
from .codex_meta import codex_home, find_db
from .config import config_path, ensure_config, load_config
from .events import Notification
from .installer import has_notifyhub_hook, install_codex, uninstall_codex
from .log import get_logger
from .render import render_text
from .router import dispatch
from .runtime import hook_command, launcher_path

SAMPLE_NOTIFICATION = Notification(
    source="codex",
    event="Stop",
    status_text="✅ 测试通知",
    color="green",
    title="notifyhub 自检",
    project="notifyhub",
    cwd=os.getcwd(),
    session_id="notifyhub-test",
    model="gpt-6.1-sol",
    last_message="如果你看到这条消息，说明通知通道配置成功。",
)


def _logger(config, verbose=False):
    return get_logger(config.get("log_path", ""), verbose)


def _config_target(args):
    return Path(args.config).expanduser() if args.config else config_path()


def _read_payload(path=None):
    if path:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def cmd_hook(args):
    try:
        config = load_config(args.config)
        logger = _logger(config, args.verbose)
    except Exception as error:
        print(f"notifyhub: {error}", file=sys.stderr)
        return 0

    try:
        payload = _read_payload()
    except (OSError, ValueError) as error:
        logger.error("invalid hook payload: %s", error)
        return 0

    try:
        adapter = get_adapter(args.source)
        notification = adapter.parse(payload, config)
    except Exception as error:
        logger.exception("failed to parse hook event: %s", error)
        return 0

    if notification is None:
        logger.info("event ignored: %s", payload.get("hook_event_name"))
        return 0

    if args.dry_run:
        print(render_text(notification))
        return 0

    try:
        dispatch(notification, config, logger, force=args.force)
    except Exception as error:
        logger.exception("failed to dispatch notification: %s", error)
    return 0


def cmd_test(args):
    config = load_config(args.config)
    logger = _logger(config, args.verbose)

    if args.dry_run:
        print(render_text(SAMPLE_NOTIFICATION))
        return 0

    results = dispatch(SAMPLE_NOTIFICATION, config, logger, force=True, only_channel=args.channel)
    for name, ok, detail in results:
        print(f"{name}: {'ok' if ok else 'failed'}{' - ' + detail if detail else ''}")

    if not results:
        print("没有启用且匹配的通知渠道，请检查配置。")
        return 1

    return 0 if any(ok for _, ok, _ in results) else 1


def cmd_init(args):
    path, created = ensure_config(args.config, force=args.force)
    print(f"{'已创建' if created else '已存在'}: {path}")
    if created or args.force:
        print("请编辑该文件，至少填写 channels.feishu.webhook_url。")
    return 0


def cmd_install_hooks(args):
    config = load_config(args.config)
    ensure_config(args.config)

    events = args.events.split(",") if args.events else config["codex"].get("events", ["Stop"])
    events = [event.strip() for event in events if event.strip()]
    command = args.command or hook_command(_config_target(args))

    result = install_codex(codex_home(), events, command)
    print(f"已写入: {result['path']}")
    print(f"事件: {', '.join(result['events'])}")
    print(f"命令: {result['command']}")
    if result["backup"]:
        print(f"备份: {result['backup']}")
    print()
    print("下一步:")
    print("1. 打开 Codex，运行 /hooks 并信任新增的 notifyhub hook")
    print("2. 检查配置（飞书应用 / 群机器人 / iMessage）: " + str(_config_target(args)))
    self_test = [sys.executable, str(launcher_path()), "--config", str(_config_target(args)), "test"]
    print(f"3. 运行自检: {dsh_install.format_command(self_test)}")
    return 0


def cmd_uninstall_hooks(args):
    result = uninstall_codex(codex_home(), args.events.split(",") if args.events else None)
    print(f"已处理: {result['path']}")
    print(f"移除 handler: {result['removed']}")
    if result["backup"]:
        print(f"备份: {result['backup']}")
    return 0


def cmd_doctor(args):
    config = load_config(args.config)
    checks = []

    path = _config_target(args)
    checks.append(("配置文件", path.exists(), str(path)))

    feishu = config["channels"]["feishu"]
    feishu_ok = bool(feishu.get("enabled") and feishu.get("webhook_url"))
    feishu_detail = "已配置" if feishu_ok else ("已关闭" if not feishu.get("enabled") else "缺少 webhook_url")
    checks.append(("飞书群机器人", feishu_ok, feishu_detail))

    feishu_app = config["channels"]["feishu_app"]
    feishu_app_ok = bool(
        feishu_app.get("enabled")
        and feishu_app.get("app_id")
        and feishu_app.get("app_secret")
        and feishu_app.get("receive_id")
    )
    feishu_app_detail = "已配置" if feishu_app_ok else ("已关闭" if not feishu_app.get("enabled") else "缺少 app_id / app_secret / receive_id")
    checks.append(("飞书应用", feishu_app_ok, feishu_app_detail))

    imessage = config["channels"]["imessage"]
    imessage_ok = bool(imessage.get("enabled") and imessage.get("to"))
    imessage_detail = "已配置" if imessage_ok else ("已关闭" if not imessage.get("enabled") else "缺少 to")
    checks.append(("iMessage", imessage_ok, imessage_detail))

    events = config["codex"].get("events", ["Stop"])
    installed = has_notifyhub_hook(codex_home(), events)
    checks.append(("Codex hooks", installed, str(codex_home() / "hooks.json")))

    state_db = find_db("state")
    checks.append(("Codex state DB", state_db is not None, str(state_db or "未找到")))

    goals_db = find_db("goals")
    checks.append(("Codex goals DB", goals_db is not None, str(goals_db or "未找到")))

    dsh_state = dsh_install.status()
    installed = ", ".join(dsh_state.get("installed_profiles") or [])
    dsh_ok = bool(dsh_state["dsh_found"] and dsh_state["plugin_available"])
    if dsh_ok and installed:
        dsh_detail = f"dsh + plugin ({installed})"
    elif dsh_ok:
        dsh_detail = "dsh 已安装，插件未装"
    else:
        dsh_detail = "未找到 dsh" if not dsh_state["dsh_found"] else "plugin 缺失"
    checks.append(("DeepSeek Harness", dsh_ok, dsh_detail))

    for label, ok, detail in checks:
        symbol = "✓" if ok else ("○" if detail == "已关闭" else "✗")
        print(f"{symbol} {label}: {detail}")

    critical = [checks[0], checks[4]]
    if not (feishu_ok or feishu_app_ok or imessage_ok):
        critical.append(("通知渠道", False, "飞书、飞书应用和 iMessage 都没有配置"))
    return 0 if all(ok for _, ok, _ in critical) else 1


def cmd_preview(args):
    config = load_config(args.config)
    payload = _read_payload(args.file)
    adapter = get_adapter(args.source)
    notification = adapter.parse(payload, config)
    if notification is None:
        print("事件被忽略（未启用或不在 codex.events 中）。", file=sys.stderr)
        return 1
    print(render_text(notification))
    return 0


def cmd_gui(args):
    from .gui import run_gui

    path, created = ensure_config(args.config)
    print(f"{'已创建' if created else '配置文件'}: {path}")
    run_gui(path, host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def cmd_install_dsh(args):
    plugin = dsh_install.find_plugin()
    if plugin is None:
        print("找不到 integrations/dsh，请确认 notifyhub 包完整。")
        return 1

    dsh = dsh_install.dsh_binary()
    if args.print_only or dsh is None:
        command = dsh_install.build_command(args.profile)
        print(dsh_install.format_command(command))
        if dsh is None:
            print("未找到 dsh。先安装 DeepSeek Harness：npx @deepseek-ai/dsh web")
            print("也可以用 npx 安装插件：")
            link_path = plugin.as_posix() if os.name == "nt" else str(plugin)
            npx_command = [
                "npx",
                "@deepseek-ai/dsh",
                "plugin",
                "--profile",
                args.profile,
                "add",
                f"link:{link_path}",
            ]
            print(dsh_install.format_command(npx_command))
        return 0

    result = dsh_install.install(args.profile)
    if not result["ok"]:
        print(f"安装失败：{result.get('stderr') or result.get('error', '')}")
        return 1

    print("已安装 DSH 插件。重启 dsh web 生效。")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="notifyhub", description="Codex 完成通知网关")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command")

    hook = subparsers.add_parser("hook", help="从 stdin 读取 agent hook payload")
    hook.add_argument("source", choices=sources())
    hook.add_argument("--dry-run", action="store_true")
    hook.add_argument("--force", action="store_true")
    hook.set_defaults(func=cmd_hook)

    test = subparsers.add_parser("test", help="发送一条测试通知")
    test.add_argument("--channel", choices=channel_names())
    test.add_argument("--dry-run", action="store_true")
    test.set_defaults(func=cmd_test)

    init = subparsers.add_parser("init", help="创建默认配置")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    install = subparsers.add_parser("install-hooks", help="安装 Codex hooks")
    install.add_argument("--target", choices=["codex"], default="codex")
    install.add_argument("--events", help="逗号分隔的事件列表，例如 Stop,Interrupt")
    install.add_argument("--command", help="覆盖写入的 hook 命令")
    install.set_defaults(func=cmd_install_hooks)

    uninstall = subparsers.add_parser("uninstall-hooks", help="移除 Codex hooks")
    uninstall.add_argument("--target", choices=["codex"], default="codex")
    uninstall.add_argument("--events", help="逗号分隔的事件列表")
    uninstall.set_defaults(func=cmd_uninstall_hooks)

    doctor = subparsers.add_parser("doctor", help="检查配置和本地依赖")
    doctor.set_defaults(func=cmd_doctor)

    preview = subparsers.add_parser("preview", help="预览 hook payload 渲染结果")
    preview.add_argument("source", choices=sources())
    preview.add_argument("--file", help="payload JSON 文件，默认从 stdin 读取")
    preview.set_defaults(func=cmd_preview)

    gui = subparsers.add_parser("gui", help="打开本地 Web 配置界面")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--no-open", action="store_true")
    gui.set_defaults(func=cmd_gui)

    dsh = subparsers.add_parser("install-dsh", help="安装 DeepSeek Harness 插件")
    dsh.add_argument("--profile", default="web")
    dsh.add_argument("--print", action="store_true", dest="print_only")
    dsh.set_defaults(func=cmd_install_dsh)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args) or 0
