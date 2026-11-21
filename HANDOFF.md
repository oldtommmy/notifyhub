# HANDOFF — notifyhub（通知网关 + 飞书远程续聊）

> **给接手的 agent**：先读第 1 节和第 9 节，再按需跳读。这份文档记录的是
> **为什么这么写**——很多决定的理由不在代码里，只看 diff 会以为某些写法多余，
> 而每一条都对应一次线上踩坑。删掉任何一条「看起来多余」的保护前，请先找到它
> 对应的故障记录。


> **2026-11-21 更新：原生续聊原型**
> `integrations/dsh-native-bridge/` 在桌面 dsh 进程内使用
> `ctx.agents.get(id).followup()`，Python `native_transport.py` 仅负责飞书 I/O。
> 因此同进程续聊不需要另起 headless，也不需要争抢会话写锁或复制历史。
> 已核对本机 dsh 0.2.2-rc.5 源码；原型测试和真机验收分别记录，不能将旧 bridge
> 的测试结论当作原生插件的验收。详见该插件 README。
> 真机已验证：`/sessions` 列出桌面测试会话，`/attach` 后手机发消息，
> 飞书收到回答且桌面原会话出现同一轮消息。故障恢复与审批应答仍未完成真机验收。
> 当前首版只支持已加载的主会话、每会话一条未完成手机请求，沿用原会话权限。
> 新插件独立安装到 `~/.local/share/notifyhub/native-bridge`，使用独立配置
> `~/.config/notifyhub/native-bridge.json`。不启动旧 bridge 或 headless 服务。

---

## 1. 这个项目是什么

notifyhub 有两个层面，**先分清自己在改哪一层**：

| 层面 | 作用 | 状态 |
|---|---|---|
| **A. 通知网关（核心）** | Codex / Claude Code / Command Code / DSH 跑完一轮后，把结果推到飞书或 iMessage | **稳定可用，已发布 0.1.0** |
| **B. 飞书远程续聊（bridge）** | 在手机上给飞书机器人发消息，驱动本机 dsh / codex 干活 | **实验性，已暂停，未合并主干** |

两层共享同一个包（`scripts/notifyhub/`）、同一份配置、同一个 CLI，但 **B 的代码
在 A 里完全不存在**——本地安装的 0.1.0 插件没有任何 bridge 模块（见 §7）。

```
本机 agent 跑完一轮
    └─> hook（Codex/Claude/CommandCode/DSH 各一个）
        └─> notifyhub.py hook <agent>
            └─> 适配器解析 payload
                └─> router 分发到 channels（feishu / feishu_app / imessage）
                    └─> 失败就落盘 outbox，下次成功时顺带重放
```

远程续聊走的是另一条路：

```
飞书 App ──发消息──> 飞书服务器
                        ↑ 出站 WebSocket（本机主动连，不需要公网 IP）
                    本机 Mac ──> dsh / codex ──> 结果卡片回到飞书
```

**核心事实：飞书长连接是出站 WebSocket（`wss://msg-frontier.feishu.cn/ws/v2`），
所以不需要 VPS、内网穿透、公网端口。** 这是 B 方案能成立的前提，改架构时别把这条丢了。

---

## 2. 当前状态

| 项目 | 值 |
|---|---|
| 仓库 | `git@github.com:oldtommmy/notifyhub.git` |
| 本地克隆 | `/Users/cyh/Desktop/dsh_workplace/tools/remote_notice/notifyhub` |
| 主干 | `main` @ `212c7e0`（**未包含任何 bridge 代码**） |
| 工作分支 | `feature/feishu-bridge` @ `fc8a3da`，17 个提交，**已 push** |
| 版本 | `0.9.2`（`scripts/notifyhub/__init__.py`） |
| 测试 | **265 个**，Python 3.9.6 / 3.12.14 双绿 |
| PR | 未创建。要开会话：https://github.com/oldtommmy/notifyhub/pull/new/feature/feishu-bridge |
| 运行状态 | **无进程在跑**。launchd 已卸载，plist 已删 |

功能上 B 已经跑通过真机：手机发消息 → 本机 dsh 执行 → 结果回到飞书。暂停是产品决定，不是技术障碍。

---

## 3. 代码地图

`scripts/notifyhub/` 共 5287 行。

### A 层（通知网关，`main` 上就有）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `cli.py` | 455 | 全部子命令入口 |
| `config.py` | 215 | 默认配置 + 深合并 + 路径展开 |
| `router.py` | 169 | 分发到各 channel；`replay_outbox()` |
| `render.py` | 197 | 把 Notification 渲染成各渠道的卡片/文本 |
| `channels/feishu.py` | — | 群机器人 webhook |
| `channels/feishu_app.py` | — | 自建应用 API（**用户实际在用的**，含 `AmbiguousSendError`） |
| `channels/imessage.py` | — | macOS 信息 |
| `outbox.py` | 242 | 失败落盘 + 指数退避 + 重放 |
| `installer.py` | 187 | 安装/卸载各 agent 的 hooks |
| `quota.py` | 229 | 读 Codex 剩余额度 |
| `gui.py` | 370 | 本地 Web 配置界面（:8765） |
| `state.py`, `log.py`, `events.py`, `runtime.py` | 小 | 状态/日志/事件类型 |
| `transcripts.py` | 172 | A 层读 transcript 取「最后一条消息」 |

各 agent 适配器：`codex_meta.py`、`dsh_install.py`、`feishu_discovery.py`，以及 `cli.py` 里注册的 claude/commandcode dsh 适配器。

### B 层（远程续聊，只在 `feature/feishu-bridge`）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `bridge_cli.py` | 910 | **主入口**：`BridgeService` 主循环、`_on_message`、`_attach`、`_handoff`、`cmd_bridge_*` |
| `bridge.py` | 530 | 纯逻辑：`Task`/`TaskResult`/`parse_command`/`is_allowed`/`SessionMap`/`RunGuard`/`Worker`/`run_task` |
| `agents.py` | 299 | `dsh_runner` / `codex_runner` / `session_in_use` / `is_already_owned` / `available_agents` |
| `sessions_index.py` | 250 | 扫 `~/.dsh/sessions` 和 `~/.codex/sessions`，给 `/attach` 用 |
| `transcript.py` | 219 | 读 dsh 压缩事件流 → `Digest`，给 `/handoff` 用 |
| `feishu_listener.py` | 224 | 长连接客户端 + `SeenMessages` 入站去重 |
| `service.py` | 183 | launchd plist 生成与 install/uninstall/status/restart |

---

## 4. 关键设计决定（**理由比代码重要**）

### 4.1 为什么用长连接而不是 HTTP 回调

飞书自建应用支持两种接消息方式：webhook（要公网地址）和长连接（本机主动出站连）。
选了长连接，因为不需要公网 IP、不需要端口映射、外面扫不到任何端口。

### 4.2 为什么一个会话不能「并发续写」

dsh 内核强制**一个 session id 同时只有一个写句柄**：

```js
// @deepseek-ai/dsh-session-persistence-jsonl (app.asar 内)
claimWrite(id) { if (this.writers.has(id)) throw new SessionAlreadyOwnedError(id); this.writers.set(id, null); }
```

跨进程那一层在 `SessionWriteLease.acquire(dir, id)`，对 `session.lock` 做 `tryLockExclusive`。

**但 dsh 只在真正追加时才拿写锁**：打开已有会话用读句柄，写完就放。所以
「App 里开着但这个会话此刻没在写」是**可以**被 headless 续写的——这一点曾经搞错，
见 §5.1。

### 4.3 `session.lock` 文件存在 ≠ 会话被锁

dsh 释放锁时**只关闭文件句柄，从不删除 lock 文件**（注释原文：
"Release never removes the POSIX lock file ... preserving the stable inode"）。

所以判断占用**只能**看 flock 有没有被持有，不能看文件在不在。
`agents.session_in_use()` 现在只做 flock 探测（`_lock_is_held()`，`fcntl.LOCK_EX|LOCK_NB`）。

> **历史警告**：0.6.0 曾引入 `bridge.session_conflict`（`"lock"` / `"idle"`）和
> `bridge.idle_grace_seconds`（默认 90 秒 mtime 判定）。**这两个配置在 0.9.0 已删除**，
> 因为 mtime 判定会误拒「App 里开着但空闲」的会话（桌面会话的周期性 flush
> 一直在刷新 mtime）。如果你在看旧 commit 或旧文档时见到这两个键，它们是废弃的。

### 4.4 `/handoff` 和 `/attach` 的分工（最容易被误解的地方）

因为 4.2/4.3 的约束，手机接手电脑上的对话有两条路：

| 命令 | 前提 | 结果 | 代价 |
|---|---|---|---|
| `/attach <id>` | 那个会话**此刻没有写句柄**（App 没在写它） | 真正写回原会话 | 若 App 正在写，会被拒 |
| `/handoff [id]` | 任何时候 | 读历史喂给**新会话** | 手机说的话**不写回**电脑那个会话 |

`/handoff` 的原理：**读 transcript 不需要写权**，所以能绕开限制。它把最近 N 轮
（`bridge.handoff_turns`，默认 6；上限 `bridge.handoff_max_chars`，默认 12000）
渲染成只读 prelude，作为 prompt 的 `context` 一次性注入，然后 `SessionMap.reset()`
开新会话（**必须重置**，否则两段历史混在一起）。

实现细节：`context` 通过 **stdin** 传给 dsh（prompt 变成 `-`），因为历史文本很长
且含 shell 元字符，走 argv 有风险。见 `agents.dsh_runner()`。

**用户明确要的场景是「Mac 聊到一半，出门用手机接着聊同一个话题」。**
`/handoff` 满足使用体验，但**不写回**是真实的妥协——接手的 agent 不要假装它等价于续写。

### 4.5 为什么发送失败不能盲重试

超时、连接被重置、TLS 握手中断——这些意味着**没收到回应**，不是**飞书拒绝了**。
飞书很可能已经发出去了，重试就会在聊天里出现两条一样的卡片。

所以 `_post_json` 把它们抛成 `AmbiguousSendError`（继承 `FeishuAppError`），
`_send()` / `_reply()` 收到就**立即停手**，日志记
`"card send outcome unknown; not retrying to avoid a duplicate"`。
只有明确的 HTTP 错误 / 非零 `code` 才重试。

### 4.6 回执默认关闭

`bridge.reply` 现在是 `{"ack": False, "on_finish": True}`。

- `ack` 默认 **false**：一条普通消息已经会得到结果卡片，再加一句「收到，已交给 dsh 处理」就是同一件事通报两遍。
- **命令始终回执**（`/attach` `/topic` `/new` `/handoff`），因为没有结果卡片能告知命令是否生效。
- 曾经的 `on_start` 是文档写了但**代码从不读**的死开关，已删。

### 4.7 launchd 常驻（为什么需要，以及两个坑）

前台 `bridge start` 活在被启动它的进程组里。Mac 上有个很具体的坑：
**退出桌面 App（Cmd+Q）会连带把 bridge 杀掉**（整个进程组收 SIGTERM），
手机就发消息没人收。`nohup` 只防 SIGHUP，防不住进程组 SIGTERM。

launchd 让它脱离 App 进程树：`RunAtLoad: True`、`KeepAlive: {"SuccessfulExit": False}`
（故意不含 `SuccessfulExit: True`，否则 `service uninstall` 会被 launchd 顶回来）、
`ThrottleInterval: 30`、`ProcessType: "Background"`。

两个坑：
- **launchd 不继承 shell 环境变量**。凭据要么 export 后 install 冻结进 plist（mode 600），要么让服务从本机配置文件自己读。
- **`.venv/bin/python` 是符号链接**。用 `Path.resolve()` 会跟到
  `/opt/homebrew/Cellar/python@3.12/.../python3.12`，**跑出 venv、丢掉 `lark-oapi`**，
  报 `RuntimeError: 缺少 lark-oapi（飞书长连接需要它）`。必须锚定 venv 路径、不跟随符号链接
  （`service._absolute_interpreter()`）。

实测 `kill -9` 后 12 秒自动重拉。

### 4.8 入站去重

飞书会重投事件（网络抖动时）。`SeenMessages`（512 条环形窗口，`deque` + `set` + `Lock`）
挡掉重复的 `message_id`，否则同一条消息会被执行两遍。日志里
`ignoring redelivered message_id=...` 就是它在工作。

### 4.9 单实例

两个 bridge 同时连飞书会**随机瓜分事件**（每条事件只投给一个连接）。
`_acquire_instance_lock()` 用 flock 抢占，第二个实例直接退出。

### 4.10 outbox（A 层的可靠性机制）

A 层发送失败时把通知落盘到 `~/.local/state/notifyhub/outbox.json`：

- 按 `dedupe_key` 去重
- 指数退避 `backoff_seconds(attempts) = min(60 * 2**(attempts-1), 3600)`，即 1m → 2m → 4m … 封顶 1h
- `PERMANENT_CODES = {99991663, 99991672, 99991661, 230001, 230002, 230006}` 立即标 dead（重试没意义）
- `revive_dead()` 手动复活，`stats()` 看统计
- hook 成功时顺带 `replay_outbox(limit=3)`，CLI 有 `flush --status/--clear/--retry-dead/--limit`

---

## 5. 已修过的坑（按时间，**别重复踩**）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `230001 invalid receive_id` | 结果卡片用 `receive_id_type=chat_id` 配 `ou_` 的 open_id | 发到 `task.chat_id` |
| 2 | 同一条消息被执行两遍 | 入站事件无去重 | `SeenMessages` |
| 3 | `/new` 期间旧会话被复活 | 跑完的写入覆盖了新状态 | `SessionMap.set()` 加 `generation` token |
| 4 | 话题永久忙碌 | 加锁用 `(chat, topic, agent)`，解锁用 `(chat, agent)` | 统一键 |
| 5 | `SessionMap._load` 读不回自己写的文件 | `{"topics":…,"active":…}` 与 `{agent: session}` 两种格式没区分 | `_looks_like_topic_map()` |
| 6 | 凭据错误没被识别 | `MISSING_CREDENTIAL` 只在 stdout 空时检查，但 dsh 会先发 `session` 事件 | 改查 `final.reason.error.code` |
| 7 | 两个实例随机瓜分事件 | 飞书长连接每事件只投一个连接 | `_acquire_instance_lock()` |
| 8 | `/attach <2>` 存下脏数据 | 飞书客户端把参数渲染成 `<2>` 或 `` `2` `` | 先 strip `<>` 与反引号，再用正则校验 |
| 9 | `reply.on_start` 不生效 | 文档写了但代码从不读 | 删除该开关 |
| 10 | 同一条卡片发两遍 | 超时后盲重试，而飞书其实已送达 | `AmbiguousSendError`，不重试 |
| 11 | 「App 里开着但空闲」的会话被拒 | 把 `session.lock` 文件存在当占用；idle 模式 mtime 判定被周期 flush 干扰 | 只查 flock（0.9.0） |
| 12 | 换成别的 cwd 跑同一会话失败 | dsh 会校验会话记录的 cwd | 报错 `session "..." was recorded in "...", not "..."` |

### 5.1 特别说明第 11 条

这是最反复的一条，**改过三次**：

1. 最初严格 flock 探测 → 用户抱怨「App 开着就永远接不上」
2. 改成 `idle` 模式（mtime 90 秒）→ 仍误拒，因为桌面会话周期性 flush 一直刷新 mtime
3. 最终只查 flock（0.9.0）→ 正确。dsh 打开会话是读句柄，只在追加时才算写占用

**如果将来又想加「温和判定」，请注意 mtime 不能作为判据。**

---

## 6. 已知问题 / 未完成

### 6.1 codex 通道在当前机器上跑不了（**环境问题，不是代码问题**）

```
codex exec 报: The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account
```

根因是 `~/.codex/config.toml` 里 `model = "gpt-6.1-sol"`，**脱离 bridge 也能复现**。
接手后要先修这个配置，codex 通道才能测。

### 6.2 headless profile 必须先配 provider（**最容易卡住接手者**）

bridge 用 `dsh --profile headless`，而 headless profile 与桌面 profile **完全隔离**：

- `~/.dsh/profiles/headless/package.json` 的 `dependencies` 是 `{}`，只加载 `@deepseek-ai/dsh-base` + `@deepseek-ai/dsh-headless`
- **桌面 App 的账号登录不会传给 headless profile**

不配 provider 直接跑会报：

```
RunnerError: dsh 缺少模型凭据（MISSING_CREDENTIAL）。headless profile 使用 deepseek-official 路由，需要 API key
```

**恢复方法**：在 `~/.dsh/profiles/headless/cordis.patch.yml` 里声明 provider，
形如（把 `apiKeyEnv` 指向一个有值的环境变量）：

```yaml
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: qwen
    model: deepseek-v4.1-flash-1017
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      qwen:
        apiKeyEnv: QWEN_API_KEY
        api: openai-responses
        baseURL: https://maas.qianwenaiapi.com/compatible-mode/v1
        models:
          - id: deepseek-v4.1-flash-1017
            name: deepseek-v4.1-flash-1017
```

然后 `export QWEN_API_KEY=...` 再启动 bridge（launchd 不继承 shell 环境，见 §4.7）。
`bridge check` 会检查这一项，`_headless_api_key_env()` 从 patch 里读 `apiKeyEnv`。

> 本项目收尾时**这个 patch 被删了**（用户要求把本机重置为纯 notice 模式），
> 所以现在起 bridge 前要重新创建它。

### 6.3 「重复消息」的完整结论（**别再重查**）

用户报过「总会重复发送」。查下来是三种**不同**的东西：

- **入站重投**：已被 `SeenMessages` 挡住，日志可验证（只有 `ignoring redelivered`）
- **一条操作收到多条消息**：那其实是 attach 回执 + 「收到」回执 + 结果卡片，**内容不同**，不是重复
- **真重复**：调度结果未知时盲重试 → 已由 `AmbiguousSendError` 修掉（§4.5）

修复后如果还出现**完全相同的两条卡片**，按这个顺序查：
1. 日志有没有 `card send attempt` 出现多次
2. 有没有 `outcome unknown`
3. 都没有 → 说明是另一条发送线。注意 `~/.local/state/notifyhub/dsh-plugin.log`
   是 **DSH 插件 hook** 的输出通道，和 bridge 是两条独立发送线

### 6.4 尚未做的

- `~/.config/notifyhub/config.json` 的正式配置合并（开发时用的是工作区里临时的 `.bridge_config.json`，已删）
- launchd plist 里的凭据是明文（mode 600），可考虑改 Keychain
- PR 未创建、`main` 未合并
- A 层的 `channels.feishu`（群机器人）在用户机器上报 `缺少 webhook_url`——这是**预期的**，因为用户用 `feishu_app`。若嫌 `doctor` 每次报错，可把该 channel 的 `enabled` 设为 false

---

## 7. 本机当前安装状态（重要）

用户在此次工作结束时要求「把本地插件重置为只涉及 notice」，所以：

| 位置 | 状态 |
|---|---|
| `~/.codex/skills/notifyhub/` | **0.1.0，纯 notice，无任何 bridge 模块**（`dsh-notifyhub` 插件，`link:` 依赖） |
| `~/.dsh/profiles/headless/cordis.patch.yml` | **已删除**（见 §6.2，用 bridge 前要重建） |
| `~/.codex/hooks.json` | 只有 `Stop` 一个 hook → `notifyhub.py hook codex`，**未被改动**（mtime Nov 18 20:54） |
| `~/.local/state/notifyhub/outbox.json` | 已清空（12 条 bridge 测试残留已移除） |
| launchd | 未注册，plist 已删 |

**A 层 hook 与 B 层 bridge 是两条完全独立的通道**，代码、配置、触发方式都不同。
改 bridge 不会影响通知 hook。

---

## 8. 怎么跑

### 依赖

```bash
python3 -m pip install 'lark-oapi>=1.8'   # bridge 必需
# transcript.py 读 .zstd：优先 zstandard 模块，没有就调 zstd 命令
```

本机 `.venv/`（Python 3.12.14）已装 `lark-oapi 1.8.0` + `httpx 0.28.1`。
注意 `.venv/bin/python` 是符号链接，见 §4.7。

### 测试

```bash
cd notifyhub
PYTHONPATH=scripts python3.12 -m unittest discover -s tests   # 265 个
./scripts/selftest.sh                                          # 冒烟
```

### 配置

`~/.config/notifyhub/config.json`，`bridge` 段的默认值：

```json
{
  "enabled": false,
  "allow_open_ids": [],
  "default_agent": "dsh",
  "workspace": "~",
  "approval_policy": "never",
  "permission_mode": "workspace-write",
  "timeout_seconds": 1800,
  "max_message_chars": 3000,
  "reject_when_busy": true,
  "sessions_path": "~/.local/state/notifyhub/bridge_sessions.json",
  "handoff_turns": 6,
  "handoff_max_chars": 12000,
  "reply": {"ack": false, "on_finish": true},
  "prefixes": {"dsh": "", "codex": ""}
}
```

**安全**：`allow_open_ids` **默认拒绝所有人**，留空谁也用不了。
这是一个**远程命令执行入口**，改这块之前先读 `references/bridge.md` 的安全模型一节。

### 启动

```bash
python3 scripts/notifyhub.py bridge owner      # 拿自己的 open_id → 写进 allow_open_ids
python3 scripts/notifyhub.py bridge check      # 自检（会查凭据、lark-oapi、allow 列表、workspace）
python3 scripts/notifyhub.py bridge start      # 前台试跑
```

确认能用后装常驻：

```bash
export QWEN_API_KEY=... NOTIFYHUB_FEISHU_APP_SECRET=...
python3 scripts/notifyhub.py bridge service install
python3 scripts/notifyhub.py bridge service status
python3 scripts/notifyhub.py bridge logs -n 50
python3 scripts/notifyhub.py bridge service uninstall   # 明确退出
```

---

## 9. 命令速查（手机上发 + 本机 CLI）

### CLI（本机）

`notifyhub {hook|test|init|flush|install-hooks|uninstall-hooks|doctor|preview|gui|install-dsh|bridge}`

`notifyhub bridge {start|status|check|owner|logs|service}`

`--verbose` 是**全局参数**，必须写在子命令**之前**。
没有 `send` 子命令，发测试消息用 `notifyhub test`。

### 手机上发（B 层）

| 消息 | 作用 |
|---|---|
| 直接发消息 | 续聊当前话题 |
| `@dsh <内容>` / `@codex <内容>` | 这一条切 agent |
| `/topic <名字>` / `/use <名字>` | 开/切一个隔离话题 |
| `/topics`（或 `/topic` `/ls`） | 列出话题 |
| `/del <名字>` | 删除话题 |
| `/new`（或 `/reset`） | 清空当前话题的上下文 |
| `/attach`（或 `/sessions` `/list`） | 列机器上的会话 |
| `/attach <n>` / `/attach <session-id>` | 接到某个会话（真续写） |
| `/handoff` / `/takeover` / `/接续` | 把最近写的 dsh 会话历史接到新会话（**不写回**） |
| `/handoff <n>` / `/handoff <id>` | 指定要接哪个 |
| `/status` | 查看当前话题与运行状态 |
| `/help`（或 `help` `？` `?`） | 帮助 |

`parse_command()` 在 `bridge.py:98`，改命令一定同步改 `HELP_TEXT`（`bridge.py:61`）。

---

## 10. 文档在哪

| 文件 | 内容 |
|---|---|
| `README.md` | 项目总览、快速开始 |
| `references/bridge.md` | **bridge 详解**：安全模型、会话锁、常驻运行、排障表 |
| `references/config.md` | 全部配置项 |
| `references/dsh.md` | DSH 集成 |
| `references/feishu-setup.md` | 飞书应用创建步骤 |
| `references/troubleshooting.md` | 常见故障 |
| `CHANGELOG.md` | 逐版本变更（0.1.0 → 0.9.1，含每条的**理由**） |
| `HANDOFF.md` | 本文档 |

**`CHANGELOG.md` 是除本文档外最有价值的历史记录**——每一条都写了 Why，比 commit title 详细。

---

## 11. 给接手者的话

1. **先确认你要改哪一层**（§1）。A 层稳定，B 层实验性。
2. **动手前跑一次 `bridge check`**，尤其看「模型凭据」和 `lark-oapi` 两项（§6.2）。
3. **B 层的 16 个提交都是边测边修出来的**，每个对应一次真实故障。§5 的表要通读一遍。
4. **如果要做大改**（比如换成别的消息通道），先看 `references/bridge.md` 里
   「dsh 会话锁」和「常驻运行」两节——那里的约束是**内核和 macOS 给的**，不是我们的选择。
5. **最重要的一条**：dsh 一个 session id 只能有一个写句柄（§4.2）。任何「让手机直接续写
   电脑正在用的会话」的想法都会撞上这堵墙，`/handoff` 就是为了绕开它而存在的。