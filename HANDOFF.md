# HANDOFF — 飞书远程续聊（bridge）交接说明

> 状态：**已暂停开发（2026-11-20）**。功能可用，代码在 `feature/feishu-bridge` 分支，
> 尚未合并到 `main`。接手前请先读「已知问题」一节。

这份文档是给下一个接手的人/agent 看的。它记录**为什么**这么设计——很多决定的理由
不在代码里，只看 diff 会以为某些写法多余，其实每一条都对应一次线上踩坑。

---

## 1. 一句话说清这个功能

在手机上给飞书机器人发一条消息，本机（Mac）上的 dsh / codex 就接着干活，结果再发回飞书。

```
飞书 App ──发消息──> 飞书服务器
                        ↑ 出站 WebSocket（本机主动连，不需要公网 IP）
                    本机 Mac ──> dsh / codex ──> 结果卡片回到飞书
```

**核心事实：用的是飞书长连接（出站 WebSocket），所以不需要 VPS、内网穿透、公网端口。**
这是整个方案能成立的前提，改架构时别把这条丢了。

---

## 2. 当前状态

| 项目 | 值 |
|---|---|
| 分支 | `feature/feishu-bridge`（`main` 仍停在 `212c7e0`） |
| 版本 | 0.9.0 |
| 测试 | 265 个，Python 3.9.6 / 3.12.14 双绿 |
| 运行方式 | 曾用 launchd 常驻，**现已卸载**，当前无进程在跑 |
| 依赖 | `pip install 'lark-oapi>=1.8'`（长连接必需） |

功能上**已经能跑通**，包括真机验证：手机发消息 → 本机 dsh 执行 → 结果回飞书。

---

## 3. 代码地图

```
scripts/notifyhub/
├── bridge.py          Task / TaskResult / parse_command / is_allowed / SessionMap / RunGuard / Worker
├── bridge_cli.py      BridgeService（主循环）、_handoff、_attach、_on_message、cmd_bridge_*
├── feishu_listener.py 长连接客户端 + SeenMessages（入站去重）
├── agents.py          dsh_runner / codex_runner / session_in_use / available_agents
├── transcript.py      dsh 会话历史读取（zstd → 事件流 → Digest）
├── sessions_index.py  扫描 ~/.dsh/sessions 和 ~/.codex/sessions，给 /attach 用
├── service.py         launchd 常驻（plist 生成、install/uninstall/status）
├── outbox.py          发送失败落盘 + 指数退避重放
└── channels/feishu_app.py  飞书 API 封装（含 AmbiguousSendError）
```

配置在 `config.py` 的 `bridge` 段，文档在 `references/bridge.md`。

---

## 4. 关键设计决定（**理由比代码重要**）

### 4.1 为什么用长连接而不是 HTTP 回调

飞书自建应用支持两种接消息的方式：webhook（要公网地址）和长连接（本机主动出站连）。
选了长连接，因为：不需要公网 IP、不需要端口映射、在外面扫不到任何端口。

### 4.2 为什么一个会话不能「并发续聊」

dsh 内核强制**一个 session id 同时只有一个写句柄**：

```js
claimWrite(id) { if (this.writers.has(id)) throw new SessionAlreadyOwnedError(id); }
```

（在 `@deepseek-ai/dsh-session-persistence-jsonl` 里，`app.asar` 内。）
桌面 App 会一直搂着**它加载过的所有会话**（包括后台标签页），直到会话被关闭或 App 退出。
所以「电脑上开着这个会话，同时手机续写它」在内核层面是不可能的，不是配置问题。

### 4.3 `session.lock` 文件存在 ≠ 会话被锁

dsh 释放锁时**只关闭文件句柄，从不删除 lock 文件**（为了保持 inode 稳定）。
所以看到空 lock 文件不要以为被占用，唯一依据是 `lsof` 里有没有进程持有它。

`bridge.session_conflict` 两种模式：
- `"lock"` — 严格，有持有者就算占用
- `"idle"`（默认）— 温和，有持有者**且** transcript 最近 `idle_grace_seconds`（90 秒）内被写过才算

### 4.4 `/handoff` 和 `/attach` 的分工（这是最容易被误解的地方）

因为 4.2 的限制，手机接手电脑上的对话有两条路：

| 命令 | 前提 | 结果 | 代价 |
|---|---|---|---|
| `/attach <id>` | 那个会话**没有写句柄**（App 已退出/关闭该会话） | 真正写回原会话 | 电脑上必须放掉它 |
| `/handoff [id]` | 任何时候 | 读历史喂给**新会话** | 手机说的话**不写回**电脑那个会话 |

`/handoff` 的原理：读 transcript **不需要写权**，所以可以绕开限制。
它把最近 N 轮（`handoff_turns`，默认 6）渲染成一段只读 prelude，作为 prompt 的
`context` 一次性注入，然后 `SessionMap.reset()` 开新会话（必须重置，否则两段历史混在一起）。

**注意**：用户明确要的是「Mac 聊到一半，出门用手机接着聊同一个话题」这个场景。
`/handoff` 满足使用体验，但**不写回**是个真实的妥协，交接时不要假装它等价。

### 4.5 为什么发送失败不能盲重试

超时、连接被重置、TLS 握手中断——这些意味着**没收到回应**，不是**飞书拒绝了**。
飞书很可能已经发出去了，重试就会在聊天里出现两条一样的卡片。

所以 `_post_json` 把它们抛成 `AmbiguousSendError`，`_send()` / `_reply()` 收到就
**立即停手**（宁可少一条，不发两条）。只有明确的 HTTP 错误 / 非零 `code` 才重试。

### 4.6 回执默认关闭

`reply.ack` 默认 `false`。原因：一条普通消息已经会得到结果卡片，再加一句
「收到，已交给 dsh 处理」就是同一件事通知两遍。命令（`/attach`、`/topic`、`/new`）
**始终回执**，因为没有结果卡片能告诉用户命令生效没有。

### 4.7 launchd 常驻（为什么要这个）

前台 `bridge start` 活在被启动它的进程组里。Mac 上这有个很具体的坑：
**退出桌面 App（Cmd+Q）会连带把 bridge 杀掉**，手机就发消息没人收。

launchd 让它脱离 App 进程树：登录自启、崩溃重拉（`KeepAlive: {SuccessfulExit: false}`，
故意不含 `SuccessfulExit: true`，否则 `uninstall` 会被 launchd 顶回来）。

两个坑：
- **launchd 不继承 shell 环境变量**。凭据要么 export 后 install 冻结进 plist（mode 600），要么让服务从本机配置文件自己读。
- **`.venv/bin/python` 是符号链接**。用 `Path.resolve()` 会跟出 venv、丢掉 `lark-oapi`。必须锚定 venv 路径不跟随（`_absolute_interpreter()`）。

### 4.8 入站去重

飞书会重投事件（网络抖动时）。`SeenMessages`（512 条的环形窗口）挡掉重复的
`message_id`，否则同一条消息会被执行两遍。日志里 `ignoring redelivered message_id=...`
就是它在工作。

---

## 5. 已修过的坑（按时间，别重复踩）

1. `230001 invalid receive_id` — 结果卡片用了 `chat_id` 类型配 `ou_` 的 id，要发到 `task.chat_id`
2. 入站事件重投 — 加 `SeenMessages`
3. `/new` 期间旧会话被复活 — `SessionMap.set()` 加 `generation` token
4. 运行锁获取用 `(chat, topic, agent)` 释放用 `(chat, agent)` → 话题永久忙碌
5. `SessionMap._load` 读不回自己的 `{"topics":…,"active":…}` 格式
6. `MISSING_CREDENTIAL` 只在 stdout 空时检查，但 dsh 会先发 `session` 事件 — 改查 `final.reason.error.code`
7. 两个 bridge 实例随机瓜分飞书事件 — `_acquire_instance_lock()` flock，第二个直接退出
8. 飞书把 `/attach 2` 渲染成 `<2>` 或 `` `2` `` — 先 strip 这些字符再做正则校验
9. `reply.on_start` 是文档里写了但代码从不读的死开关 — 已删

---

## 6. 已知问题 / 未完成

### 6.1 codex 用不了（环境问题，非代码问题）

```
codex exec 报: The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account
```

根因是用户的 `~/.codex/config.toml` 里 `model = "gpt-6.1-sol"`，脱离 bridge 也能复现。
**接手后要先修这个配置，codex 通道才能测。**

### 6.2 「重复消息」的完整结论

用户报过「总会重复发送」。查下来是两种**不同**的东西：

- **入站重投**：已被 `SeenMessages` 挡住，日志可验证（只有 `ignoring redelivered`）
- **一条操作收到多条消息**：那其实是 attach 回执 + 收到回执 + 结果卡片，**内容不同**，不是重复

真实修掉的是「调度结果未知时盲重试」（见 4.5）。
修复后如果还出现完全相同的两条卡片，需要查日志有没有 `card send attempt` 或
`outcome unknown`；都没有的话说明是另一条发送通道（`~/.local/state/notifyhub/dsh-plugin.log`
是 DSH 插件 hook，和 bridge 是两条独立发送线）。

### 6.3 尚未做的

- `~/.config/notifyhub/config.json` 的正式配置合并（开发时用的是工作区里的 `.bridge_config.json`）
- launchd plist 里的凭据是明文（mode 600），考虑改 Keychain
- 会话锁的「乐观尝试」——用户实测过一个被 App 加载但空闲的会话能正常续写，
  说明 mtime 预检可能过严，可以直接试写、失败再报错
- `bridge` 之外的 GUI（`gui.py`，:8765）未在本次工作中涉及

---

## 7. 怎么跑起来

```bash
# 依赖
python3 -m pip install 'lark-oapi>=1.8'

# 配置（工作区里有现成的 .bridge_config.json）
python3 scripts/notifyhub.py bridge owner      # 拿自己的 open_id
# 写进 bridge.allow_open_ids
python3 scripts/notifyhub.py bridge check      # 自检
python3 scripts/notifyhub.py bridge start      # 前台试跑
```

确认能用后装成常驻：

```bash
export QWEN_API_KEY=... NOTIFYHUB_FEISHU_APP_SECRET=...
python3 scripts/notifyhub.py bridge service install
python3 scripts/notifyhub.py bridge service status
python3 scripts/notifyhub.py bridge logs -n 50
```

**安全**：`allow_open_ids` **默认拒绝所有人**，留空谁也用不了。这是一个远程命令执行入口，
改这块之前先读 `references/bridge.md` 的安全模型一节。

---

## 8. 命令速查

| 手机上发 | 作用 |
|---|---|
| 直接发消息 | 续聊当前话题 |
| `@dsh` / `@codex ...` | 这一条切 agent |
| `/topic 名字` | 开/切一个隔离话题 |
| `/topics`、`/del 名字` | 列出 / 删除话题 |
| `/new` | 清空当前话题的会话 |
| `/attach` / `/attach <n>` | 列机器上的会话 / 接到某个会话 |
| `/handoff` / `/handoff <n>` | 把某个会话的历史接到新会话（不写回） |
| `/status`、`/help` | 状态 / 帮助 |

---

## 9. 给接手者的话

这个分支上 16 个提交都是**边测边修**出来的，每个对应一次真实故障。
如果要做大改（比如换成别的消息通道），建议先看 `references/bridge.md` 里的
「dsh 会话锁」和「常驻运行」两节——那里面的约束是内核和 macOS 给的，不是我们的选择。