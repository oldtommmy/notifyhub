import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_CACHE_TTL = 600
DEFAULT_TIMEOUT = 6.0

CACHE_ENV = "NOTIFYHUB_QUOTA_CACHE"


class QuotaError(RuntimeError):
    pass


def cache_path() -> Path:
    return Path(os.environ.get(CACHE_ENV, "~/.local/state/notifyhub/quota.json")).expanduser()


def _send(process, message):
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def _reader(stream, output):
    for line in stream:
        try:
            output.put(json.loads(line))
        except ValueError:
            continue
    output.put(None)


def _read_until(output, wanted_id, deadline):
    while time.time() < deadline:
        try:
            message = output.get(timeout=max(0.05, deadline - time.time()))
        except queue.Empty:
            return None
        if message is None:
            return None
        if message.get("id") == wanted_id:
            return message
    return None


def parse_result(result):
    rate = result.get("rateLimits") or {}
    primary = rate.get("primary") or {}
    credits = rate.get("credits") or {}
    reset = result.get("rateLimitResetCredits") or {}

    used = primary.get("usedPercent")
    remaining = None
    if used is not None:
        remaining = max(0, min(100, int(round(100 - float(used)))))

    balance = credits.get("balance")
    if isinstance(balance, str):
        try:
            balance = float(balance)
        except ValueError:
            balance = None

    return {
        "plan_type": rate.get("planType"),
        "primary_used_percent": used,
        "primary_remaining_percent": remaining,
        "primary_window_minutes": primary.get("windowDurationMins"),
        "primary_resets_at": primary.get("resetsAt"),
        "credits_has": bool(credits.get("hasCredits")),
        "credits_unlimited": bool(credits.get("unlimited")),
        "credits_balance": balance,
        "reset_credits": int(reset.get("availableCount") or 0),
        "spend_control_reached": bool(rate.get("spendControlReached")),
        "rate_limit_reached_type": rate.get("rateLimitReachedType"),
        "fetched_at": int(time.time()),
        "stale": False,
    }


def fetch(timeout=DEFAULT_TIMEOUT):
    binary = shutil.which("codex")
    if not binary:
        raise QuotaError("codex not found")

    process = subprocess.Popen(
        [binary, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    try:
        deadline = time.time() + timeout
        output = queue.Queue()
        threading.Thread(target=_reader, args=(process.stdout, output), daemon=True).start()

        _send(
            process,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {"name": "notifyhub", "title": "notifyhub", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        initialized = _read_until(output, 1, deadline)
        if initialized is None:
            raise QuotaError("app-server initialize timed out")
        if initialized.get("error"):
            raise QuotaError(f"app-server initialize failed: {initialized['error']}")

        _send(process, {"method": "initialized", "params": {}})
        _send(process, {"method": "account/rateLimits/read", "id": 2, "params": {}})
        response = _read_until(output, 2, deadline)
        if response is None:
            raise QuotaError("rate limits request timed out")
        if response.get("error"):
            raise QuotaError(f"rate limits request failed: {response['error']}")

        return parse_result(response.get("result") or {})
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


def _read_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        return


def cached(path=None):
    return _read_cache(path or cache_path())


def get(max_age=DEFAULT_CACHE_TTL, timeout=DEFAULT_TIMEOUT, refresh=False, path=None):
    target = path or cache_path()
    cached = _read_cache(target)
    now = int(time.time())

    if cached and not refresh and now - int(cached.get("fetched_at", 0)) <= max_age:
        return cached

    try:
        fresh = fetch(timeout)
        _write_cache(target, fresh)
        return fresh
    except Exception:
        if cached:
            stale = dict(cached)
            stale["stale"] = True
            return stale
        return None


def age_seconds(quota):
    if not quota:
        return None
    return max(0, int(time.time()) - int(quota.get("fetched_at", 0)))


def _reset_text(resets_at):
    if not resets_at:
        return ""
    delta = int(resets_at) - int(time.time())
    if delta <= 0:
        return "即将重置"
    if delta < 3600:
        return f"{max(1, delta // 60)} 分钟后重置"
    if delta < 86400:
        return f"{delta // 3600} 小时后重置"
    return f"{delta // 86400} 天后重置"


def format_line(quota):
    if not quota:
        return ""
    if quota.get("spend_control_reached") or quota.get("rate_limit_reached_type"):
        return "剩余额度: 已受限"

    parts = []
    remaining = quota.get("primary_remaining_percent")
    if remaining is not None:
        parts.append(f"{remaining}%")
    reset = _reset_text(quota.get("primary_resets_at"))
    if reset:
        parts.append(reset)
    if quota.get("reset_credits"):
        parts.append(f"重置券 {quota['reset_credits']}")

    if not parts:
        if quota.get("credits_unlimited"):
            parts.append("Credits 无限")
        elif quota.get("credits_balance") is not None:
            parts.append(f"Credits {quota['credits_balance']:,.0f}")

    return "剩余额度: " + " · ".join(parts) if parts else ""
