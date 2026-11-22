"""Headless agent runners for the Feishu bridge.

Each runner turns (session, prompt) into a child process and parses its output
into a common RunResult. Nothing here talks to Feishu, so it is testable on its
own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTPUT_CHARS = 4_000_000


class RunnerError(RuntimeError):
    pass


@dataclass
class RunResult:
    ok: bool
    text: str = ""
    session_id: str = ""
    error: str = ""
    error_code: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    events: list = field(default_factory=list)


def _binary(name, env_var=None):
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    found = shutil.which(name)
    if found:
        return found
    # The dsh desktop app bundles its CLI outside PATH on macOS.
    if name == "dsh":
        bundled = Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")
        if bundled.exists():
            return str(bundled)
    return ""


def _run(command, prompt, cwd, timeout, env=None):
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    try:
        return subprocess.run(
            command,
            cwd=cwd or None,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired as error:
        raise RunnerError(f"超时（>{timeout:.0f}s）") from error
    except FileNotFoundError as error:
        raise RunnerError(f"找不到可执行文件: {command[0]}") from error
    except OSError as error:
        raise RunnerError(f"启动失败: {error}") from error


# --------------------------------------------------------------------- dsh


def dsh_runner(
    session_id, prompt, cwd, timeout, permission_mode=None, approval_policy=None, context=None
):
    """Run one dsh headless turn, resuming session_id when given."""
    binary = _binary("dsh", "NOTIFYHUB_DSH_BIN")
    if not binary:
        raise RunnerError("找不到 dsh，请先安装 DeepSeek Harness")

    task = prompt
    if context:
        # Handoff digests are long and carry shell metacharacters, so they go
        # through stdin and the prompt becomes "-": less argv, no quoting risk.
        task = "-"
    command = [binary, "--profile", "headless", "--json"]
    if session_id:
        command += ["--session-id", session_id]
    if permission_mode:
        command += ["--permission-mode", permission_mode]
    if approval_policy:
        command += ["--approval-policy", approval_policy]
    command.append(task)

    started = time.time()
    completed = _run(command, context, cwd, timeout)
    elapsed = time.time() - started

    result = RunResult(ok=False, exit_code=completed.returncode, duration_seconds=elapsed)
    events = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        if event.get("type") == "session" and event.get("sessionId"):
            result.session_id = str(event["sessionId"])
        elif event.get("type") == "final":
            result.session_id = str(event.get("sessionId") or result.session_id)
            result.text = str(event.get("text") or "")
            reason = event.get("reason") or {}
            kind = str(reason.get("kind") or "")
            if kind == "error":
                error = reason.get("error") or {}
                result.error = str(error.get("message") or "运行失败")
                result.error_code = str(error.get("code") or "")
            elif kind in ("aborted", "interrupted", "max-tokens"):
                result.ok = False
                result.error = f"运行结束: {kind}"
            else:
                result.ok = True

    result.events = events[-20:]
    if not events:
        detail = (completed.stderr or "").strip() or "没有输出"
        result.error = detail[:500]
        return result
    if result.error and ("MISSING_CREDENTIAL" in result.error or result.error_code == "MISSING_CREDENTIAL"):
        # dsh emits a session event before it discovers missing credentials,
        # so this cannot live in the "no events" branch above.
        raise RunnerError(
            "dsh 缺少模型凭据（MISSING_CREDENTIAL）。headless profile 使用 "
            "deepseek-official 路由，需要 API key：\n"
            "  export DEEPSEEK_API_KEY=... 后重启 bridge，\n"
            "  或在该 profile 里配置其他已登录的 provider。\n"
            "（桌面 App 的账号登录不会自动传给 headless profile）"
        )
    if result.error and not result.text:
        result.ok = False
    return result


# ------------------------------------------------------------------- codex


def codex_runner(
    session_id,
    prompt,
    cwd,
    timeout,
    sandbox=None,
    approval=None,
    model=None,
    context=None,
):
    binary = _binary("codex", "NOTIFYHUB_CODEX_BIN")
    if not binary:
        raise RunnerError("找不到 codex CLI")

    command = [binary, "exec"]
    if session_id:
        command += ["resume", session_id]
    command += ["--json", "--skip-git-repo-check"]
    if sandbox:
        command += ["--sandbox", sandbox]
    if approval:
        command += ["-c", f'approval_policy="{approval}"']
    if model:
        command += ["-m", model]
    command.append(f"{context}\n\n{prompt}" if context else prompt)

    started = time.time()
    completed = _run(command, context, cwd, timeout)
    elapsed = time.time() - started

    result = RunResult(ok=False, exit_code=completed.returncode, duration_seconds=elapsed)
    texts = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        msg = event.get("msg") or event
        if not isinstance(msg, dict):
            continue
        msg_type = str(msg.get("type") or "")
        if msg_type == "session_configured" and msg.get("session_id"):
            result.session_id = str(msg["session_id"])
        elif msg_type == "agent_message":
            texts.append(str(msg.get("message") or ""))
        elif msg_type == "error":
            result.error = str(msg.get("message") or "运行失败")

    result.text = "\n\n".join(t for t in texts if t).strip()
    result.ok = completed.returncode == 0 and bool(result.text)
    if not result.ok and not result.error:
        result.error = (completed.stderr or "codex 没有返回内容").strip()[:500]
    return result


# -------------------------------------------------------------------- 通用


def get_runner(name):
    runners = {"dsh": dsh_runner, "codex": codex_runner}
    try:
        return runners[name]
    except KeyError:
        raise RunnerError(f"不支持的 agent: {name}（可选: {', '.join(sorted(runners))}）") from None


def available_agents():
    return sorted(name for name in ("dsh", "codex") if _binary(name))


# -------------------------------------------------------------- 会话锁探测


def _lock_is_held(lock_path):
    """True when some process holds an exclusive flock on this lock file."""
    try:
        import fcntl
    except ImportError:  # Windows: flock is unavailable, assume free.
        return False
    try:
        with open(lock_path, "a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
    except OSError:
        return False


def session_in_use(agent, session_id, dsh_home=None):
    """True when another process currently holds this session's write lock.

    dsh keeps <DSH_HOME>/sessions/<cwd-hash>/<session-id>/session.lock and takes
    a non-blocking flock on it while a write handle is open. The lock file is
    *never deleted* on release (dsh keeps the inode stable for later lockers),
    so the file's presence proves nothing — only a held flock does.

    The flock is also not held for the whole life of a desktop session: dsh
    opens existing sessions with a read handle and only takes the write claim
    when it actually appends. So an idle session the app has "opened" is
    normally writable from the bridge, and this probe answers False for it.
    """
    if agent != "dsh" or not session_id:
        return False

    home = Path(dsh_home or os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    sessions = home / "sessions"
    if not sessions.is_dir():
        return False

    needle = session_id.replace("session-", "")
    try:
        candidates = list(sessions.glob(f"*/{session_id}/session.lock")) + list(
            sessions.glob(f"*/session-{needle}/session.lock")
        )
    except OSError:
        return False
    if not candidates:
        return False

    return _lock_is_held(candidates[0])


# dsh's SessionAlreadyOwnedError text (rendered into the headless "final" event
# or stderr when a write open finds the session bound to another writer).
_OWNED_MARKERS = (
    "already owned by an active write handle",
    "SessionAlreadyOwnedError",
)


def is_already_owned(result_or_text):
    """True when a run failed only because another process owns the session."""
    if isinstance(result_or_text, RunResult):
        text = f"{result_or_text.error}\n{result_or_text.text}"
    else:
        text = str(result_or_text or "")
    return any(marker in text for marker in _OWNED_MARKERS)
