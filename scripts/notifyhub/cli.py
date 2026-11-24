import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, dsh_install, installer
from .adapters import get_adapter, sources
from .channels import channels as channel_names
from .codex_meta import find_db
from .config import config_path, ensure_config, load_config
from .events import Notification
from .log import get_logger
from .render import render_text
from . import bridge_cli
from .outbox import outbox_for
from .router import dispatch, replay_outbox
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
        return _process_hook(args)
    finally:
        if args.source == "antigravity" and not args.dry_run:
            print("{}")


def _process_hook(args):
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
        origin = payload.get("origin")
        logger.info(
            "event ignored: %s%s",
            payload.get("hook_event_name"),
            f" origin={origin}" if origin else "",
        )
        return 0

    if args.dry_run:
        print(render_text(notification))
        return 0

    if args.source == "antigravity" and config.get("antigravity", {}).get("track_sessions"):
        from .antigravity_bridge import record_stop
        try:
            if record_stop(payload, notification):
                return 0
        except (OSError, ValueError) as error:
            logger.warning("Antigravity continuation ledger unavailable: %s", error)

    try:
        results = dispatch(notification, config, logger, force=args.force)
    except Exception as error:
        logger.exception("failed to dispatch notification: %s", error)
        return 0

    # Piggyback replay: the moment connectivity works again, drain whatever
    # backed up while the proxy was down. Bounded so the hook stays fast.
    if results and any(ok for _, ok, _ in results) and not args.no_flush:
        try:
            delivered, failed, remaining = replay_outbox(config, logger, limit=3)
            if delivered or failed:
                logger.info(
                    "outbox drain: delivered=%d failed=%d remaining=%d",
                    delivered,
                    failed,
                    remaining,
                )
        except Exception as error:
            logger.error("outbox drain failed: %s", error)
    return 0


def cmd_flush(args):
    config = load_config(args.config)
    logger = _logger(config, args.verbose)
    outbox = outbox_for(config, logger)

    if args.status:
        stats = outbox.stats()
        print(f"发件箱: {outbox.path_str}")
        print(f"积压: {stats['count']} 条（可重试 {stats['due']}，已放弃 {stats['dead']}）")
        if stats["count"]:
            print(f"最老一条: {stats['oldest_age_hours']} 小时前")
        for entry in outbox.entries():
            notification = entry.get("notification") or {}
            state = "dead" if entry.get("dead") else "pending"
            print(
                f"  [{state}] {entry.get('id')} attempts={entry.get('attempts', 0)} "
                f"{notification.get('status_text', '')} :: {entry.get('last_error', '')[:120]}"
            )
        return 0

    if args.clear:
        count = outbox.clear()
        print(f"已清空发件箱（{count} 条）")
        return 0

    if args.retry_dead:
        revived = outbox.revive_dead()
        print(f"已恢复 {revived} 条放弃的重试")

    delivered, failed, remaining = replay_outbox(config, logger, limit=args.limit)
    print(f"重放: 成功 {delivered}，失败 {failed}，剩余 {remaining}")
    if failed:
        print("查看原因: python3 notifyhub.py flush --status")
        return 1
    return 0


def cmd_test(args):
    config = load_config(args.config)

    if args.dry_run:
        print(render_text(SAMPLE_NOTIFICATION))
        return 0

    logger = _logger(config, args.verbose)
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

    target = args.target
    settings = config.get(target, {})
    events = args.events.split(",") if args.events else settings.get("events", ["Stop"])
    events = [event.strip() for event in events if event.strip()]
    command = args.command or hook_command(_config_target(args), target)
    path = installer.target_path(target)

    result = installer.install_hooks(path, events, command, target)
    print(f"已写入: {result['path']}")
    print(f"目标: {target}")
    print(f"事件: {', '.join(result['events'])}")
    print(f"命令: {result['command']}")
    if result["backup"]:
        print(f"备份: {result['backup']}")
    print()
    print("下一步:")
    if target == "codex":
        print("1. 打开 Codex，运行 /hooks 并信任新增的 notifyhub hook")
    elif target == "antigravity":
        print("1. 在 Antigravity 的设置 → Customizations → Hooks 确认 NotifyHub Stop hook 已启用")
    else:
        name = "Claude Code" if target == "claude" else "Command Code"
        print(f"1. 重启 {name}，运行 /hooks 确认新增的 notifyhub hook")
    print("2. 检查配置（飞书应用 / 群机器人 / iMessage）: " + str(_config_target(args)))
    self_test = [sys.executable, str(launcher_path()), "--config", str(_config_target(args)), "test"]
    print(f"3. 运行自检: {dsh_install.format_command(self_test)}")
    return 0


def cmd_uninstall_hooks(args):
    target = args.target
    path = installer.target_path(target)
    result = installer.uninstall_hooks(
        path,
        args.events.split(",") if args.events else None,
        target,
    )
    print(f"已处理: {result['path']}")
    print(f"目标: {target}")
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
    installed = installer.has_target_hooks("codex", events)
    checks.append(("Codex hooks", installed, str(installer.target_path("codex"))))

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

    for label, target in (("Claude Code hooks", "claude"), ("Command Code hooks", "commandcode")):
        hook_path = installer.target_path(target)
        if not hook_path.exists():
            continue
        installed = installer.has_target_hooks(target)
        checks.append((label, installed, "已安装" if installed else "未安装"))

    outbox = outbox_for(config, _logger(config))
    stats = outbox.stats()
    if not outbox.enabled:
        checks.append(("发件箱", True, "已关闭（失败通知不会重试）"))
    elif stats["count"] == 0:
        checks.append(("发件箱", True, "空"))
    else:
        detail = f"积压 {stats['count']} 条（可重试 {stats['due']}，放弃 {stats['dead']}）"
        checks.append(("发件箱", False, detail + "，运行 notifyhub flush"))

    for label, ok, detail in checks:
        symbol = "✓" if ok else ("○" if detail in ("已关闭", "未安装") else "✗")
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

    dsh = dsh_install.dsh_binary(args.profile)
    if args.print_only or dsh is None:
        command = dsh_install.build_command(args.profile)
        print(dsh_install.format_command(command))
        if dsh is None:
            print("未找到可用的 dsh CLI。先检查桌面自带 CLI 或 NOTIFYHUB_DSH_BIN；确实未安装时执行 npm install -g @deepseek-ai/dsh，再用 dsh --version 验证。")
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
        return 0 if args.print_only else 1

    result = dsh_install.install(args.profile)
    if not result["ok"]:
        print(f"安装失败：{result.get('stderr') or result.get('error', '')}")
        return 1

    print("已安装 DSH 插件。" + ("重启 DeepSeek Harness 桌面 App 生效。" if args.profile == "desktop" else "重启 dsh web 生效。"))
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
    hook.add_argument("--no-flush", action="store_true", help="跳过重放发件箱")
    hook.set_defaults(func=cmd_hook)

    test = subparsers.add_parser("test", help="发送一条测试通知")
    test.add_argument("--channel", choices=channel_names())
    test.add_argument("--dry-run", action="store_true")
    test.set_defaults(func=cmd_test)

    init = subparsers.add_parser("init", help="创建默认配置")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    flush = subparsers.add_parser("flush", help="重放发件箱里积压的通知")
    flush.add_argument("--status", action="store_true", help="只查看积压，不重放")
    flush.add_argument("--clear", action="store_true", help="清空发件箱")
    flush.add_argument("--retry-dead", action="store_true", help="把放弃的条目重新入队")
    flush.add_argument("--limit", type=int, help="本次最多重放几条")
    flush.set_defaults(func=cmd_flush)

    install = subparsers.add_parser("install-hooks", help="安装 agent hooks")
    install.add_argument("--target", choices=list(installer.TARGETS), default="codex")
    install.add_argument("--events", help="逗号分隔的事件列表，例如 Stop,Interrupt")
    install.add_argument("--command", help="覆盖写入的 hook 命令")
    install.set_defaults(func=cmd_install_hooks)

    uninstall = subparsers.add_parser("uninstall-hooks", help="移除 agent hooks")
    uninstall.add_argument("--target", choices=list(installer.TARGETS), default="codex")
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

    agy = subparsers.add_parser("install-antigravity", help="安装 Antigravity 通知或飞书续聊")
    agy.add_argument("--continuation", action="store_true", help="配置 Antigravity 托管 Sidecar 和独立网关")
    from .antigravity_install import install
    agy.set_defaults(func=lambda args: install(_config_target(args), args.continuation))
    sidecar = subparsers.add_parser("antigravity-sidecar", help="启动 Antigravity 托管的续聊进程")
    from .antigravity_bridge import run_sidecar
    sidecar.set_defaults(func=lambda args: run_sidecar(_config_target(args)))

    gateway_install = subparsers.add_parser("install-gateway", help="安装独立 NotifyHub 常驻网关")
    from .gateway_install import install_gateway
    gateway_install.set_defaults(func=lambda args: install_gateway(_config_target(args)))

    gateway = subparsers.add_parser("gateway", help="独立飞书收发与多 agent 会话服务")
    from .gateway import run_gateway
    gateway.set_defaults(func=lambda args: run_gateway(_config_target(args)))

    bridge = subparsers.add_parser("bridge", help="飞书远程续聊桥接")
    bridge_sub = bridge.add_subparsers(dest="bridge_command")
    bridge_start = bridge_sub.add_parser("start", help="启动长连接桥接（前台运行）")
    bridge_start.add_argument("--force", action="store_true", help="忽略 bridge.enabled=false")
    bridge_start.set_defaults(func=bridge_cli.cmd_bridge_start)
    from .native_transport import cmd_native_transport
    native = bridge_sub.add_parser("native-transport", help="dsh 原生续聊插件的飞书 I/O 子进程")
    native.set_defaults(func=cmd_native_transport)
    bridge_status = bridge_sub.add_parser("status", help="查看当前远程会话")
    bridge_status.set_defaults(func=bridge_cli.cmd_bridge_status)
    bridge_check = bridge_sub.add_parser("check", help="启动前自检")
    bridge_check.set_defaults(func=bridge_cli.cmd_bridge_check)
    bridge_owner = bridge_sub.add_parser("owner", help="打印你的 open_id 供 allow_open_ids 使用")
    bridge_owner.set_defaults(func=bridge_cli.cmd_bridge_owner)
    bridge_logs = bridge_sub.add_parser("logs", help="看最近的桥接日志")
    bridge_logs.add_argument("-n", "--lines", type=int, default=30, help="显示多少行（默认 30）")
    bridge_logs.add_argument("-f", "--follow", action="store_true", help="持续跟踪")
    bridge_logs.set_defaults(func=bridge_cli.cmd_bridge_logs)
    bridge_service = bridge_sub.add_parser("service", help="常驻运行（launchd），关掉 App 也不停")
    bridge_service.add_argument(
        "service_command",
        nargs="?",
        default="install",
        choices=["install", "uninstall", "status", "restart"],
        help="默认 install",
    )
    bridge_service.add_argument("--label", default="com.notifyhub.bridge")
    bridge_service.add_argument("--plist", default=None, help="plist 输出路径（默认 ~/Library/LaunchAgents）")
    bridge_service.add_argument("--root", default=None, help="仓库根目录（默认自动推断）")
    bridge_service.add_argument("--script", default=None, help="入口脚本路径")
    bridge_service.add_argument("--python", default=None, help="解释器（默认 .venv/bin/python）")
    bridge_service.add_argument("--venv", default=None, help="虚拟环境目录")
    bridge_service.add_argument(
        "--env",
        action="append",
        metavar="--env KEY=VALUE",
        help="写进 plist 的环境变量；凭据只想写在文件里的话不要用这个",
    )
    bridge_service.set_defaults(func=bridge_cli.cmd_bridge_service)


    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args) or 0
