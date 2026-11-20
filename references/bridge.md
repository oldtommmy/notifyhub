# 飞书远程续聊（bridge）

在手机上用飞书给机器人发一条消息，本机就会续聊一个已有的 dsh / codex 会话，结果再发回飞书。

## 为什么不需要 VPS 或内网穿透

bridge 用的是飞书**长连接（WebSocket）**，由本机主动连到飞书服务器：

```
飞书 App ──发消息──> 飞书服务器
                        ↑ 出站 WebSocket（本机主动连）
                    本机 Mac ──> 本地 agent
```

因为是**出站**连接，所以：

- 不需要公网 IP、不需要端口映射、不需要 ngrok / frp / cloudflared
- 在 NAT 后面、在家用宽带、在 Tailscale 后面都能用
- 比开一个公网 HTTP 回调端口**更安全**：外面扫不到任何端口

Tailscale 只是让你在手机上访问别的东西更方便，**bridge 本身不依赖它**。

## 前置条件

1. 飞书自建应用已开启机器人能力，且能发消息（和通知功能同一套 `feishu_app` 配置）
2. 在飞书开放平台给该应用添加：
   - 事件订阅：`im.message.receive_v1`
   - 权限：读取用户发给机器人的单聊消息（例如 `im:message.p2p_msg:readonly`）
3. **重新发布版本**并等租户管理员审批。事件订阅不发布不生效。
4. 安装 SDK：

```bash
python3 -m pip install 'lark-oapi>=1.8'
```

5. 本机有可用的 `dsh` 或 `codex`。

## 配置

```json
{
  "bridge": {
    "enabled": true,
    "allow_open_ids": ["ou_你的open_id"],
    "default_agent": "dsh",
    "workspace": "~/projects",
    "approval_policy": "never",
    "permission_mode": "workspace-write",
    "timeout_seconds": 1800
  }
}
```

| 字段 | 说明 |
|---|---|
| `allow_open_ids` | **必填**。谁能指挥本机 agent。留空 = 拒绝所有人，不是允许所有人 |
| `default_agent` | 裸消息用哪个 agent，`dsh` 或 `codex` |
| `workspace` | agent 的工作目录 |
| `approval_policy` | 无人值守必须是 `never`，否则会卡在确认框 |
| `timeout_seconds` | 单次运行上限，超时视为失败 |
| `reject_when_busy` | 同一会话运行中时拒绝新消息，默认 `true` |

查自己的 open_id：

```bash
python3 scripts/notifyhub.py bridge owner
```

## 使用

```bash
python3 scripts/notifyhub.py bridge check    # 启动前自检
python3 scripts/notifyhub.py bridge start    # 前台运行，Ctrl-C 停止
python3 scripts/notifyhub.py bridge status   # 查看当前会话
```

手机上给机器人发消息：

| 消息 | 行为 |
|---|---|
| `帮我跑一下测试` | 用默认 agent 续聊**当前话题** |
| `@codex 检查这个 PR` | 这一条强制用 codex |
| `@dsh 继续` | 这一条强制用 dsh |
| `/topic 前端` | 切到话题「前端」，不存在就新建 |
| `/topics` | 列出所有话题和它们各自的会话 |
| `/del 前端` | 删除话题（`main` 不可删） |
| `/new` | 清空**当前话题**的上下文 |
| `/status` | 当前话题、会话 id、排队情况 |
| `/help` | 帮助 |
| `/attach` | 列出电脑上已有的 dsh / codex 会话 |
| `/attach 3` | 把第 3 个会话接到当前话题 |

## 话题（区分多个对话）

一个飞书私聊可以同时挂多个互不相干的对话，这叫**话题**：

```
/topic 前端       → 新建并切到「前端」
这个按钮样式改一下   → 走「前端」的会话
/topic 后端       → 新建并切到「后端」
接口报 500 了      → 走「后端」的会话
/topic main       → 切回默认话题
```

规则：

- **按「话题 + agent」分别存会话**。同一话题下 dsh 和 codex 各有自己的上下文，
  切换 agent 不会串。
- 话题存于 `~/.local/state/notifyhub/bridge_sessions.json`，重启 bridge 后仍在。
- `main` 是默认话题，不能删除；`/new` 只清空当前话题，不影响其他话题。
- 话题名会做清理：去首尾空格、合并连续空格、去掉开头的 `#`、最长 32 字符。
- 花括号里看到的就是话题名，例如回执「收到（话题 前端）…」、卡片标题
  「飞书远程续聊 · 前端」。

**并发**：同一「话题 + agent」一次只跑一个任务；不同话题之间互不阻塞，
可以并行。

> 旧版本的会话文件（没有话题层）会被自动当作 `main` 话题读取，不会丢。

## 续聊电脑上已经开着的对话

bridge 默认只认它自己发起的会话。如果你在 Mac 上已经开了一个对话，
想用手机接着聊，先把它「接」过来：

```
/attach          → 列出本机最近的 10 个会话
1. [dsh] NotifyHub 问题调研与方案
    12分钟前 · remote_notice
2. [codex] 修一下这个 bug
    3分钟前 · mapf_workspace
...
/attach 2        → 把第 2 个接到当前话题
然后直接发消息，就接着那个对话说
```

- 也可以直接给 id：`/attach session-06dba783-...` 或 `/attach 01a0624a-...`
- 只影响**当前话题**，其他话题不受影响
- 会话按 `~/.dsh/sessions` 和 `~/.codex/sessions` 扫描，用 `DSH_HOME` /
  `CODEX_HOME` 可覆盖
- codex 标题取自第一条真实的用户消息，环境注入内容会被跳过

**限制**：如果那个会话正被桌面 App 占着（dsh 的 `session.lock`），续聊会被拒绝，
需要先在 App 里结束它。

## 安全模型

这个功能本质上是**一个远程命令执行入口**，请认真对待：

- **白名单默认拒绝**。`allow_open_ids` 为空时，任何人都用不了，包括你。
- **收到非授权消息会回复「未授权」并记录 open_id**，方便你确认自己填对了没有。
- `approval_policy: never` 意味着 agent 不会停下来问你。请配合 `permission_mode: workspace-write` 限制它只能在 workspace 里写文件。
- 建议 `workspace` 指向一个专门目录，不要指向 `~` 或包含密钥的仓库。
- 通知内容会经过飞书服务器；同理，发给 agent 的指令也一样。

## 已知限制

### dsh 会话锁

dsh 的会话文件在 `~/.dsh/sessions/<cwd>/<session-id>/`，用一个 `session.lock` 做排他。如果**桌面 App 正开着同一个会话**，headless 续聊会冲突。

bridge 会在续聊前检查该锁，被占用时直接回你一句「该会话正在被桌面 App 占用」，而不是把历史写坏。遇到这种情况：

- 在 App 里结束那个会话，或者
- 发 `/new` 开一个新会话

### 首次运行与模型凭据

`dsh` 第一次跑 `--profile headless` 会自动创建 profile，默认走 `deepseek-official`
路由，需要 `DEEPSEEK_API_KEY`。

**桌面 App 的登录态不会传给 headless profile。** 桌面版通常用账号登录或别的 provider，
而 headless 读的是 `apiKeyEnv` 指定的**进程环境变量**。所以会出现「App 里好好的，
命令行报 MISSING_CREDENTIAL」。

两种解法：

**A. 复用桌面已配置的 provider**（推荐，不用新申请 key）

把 desktop profile 的 provider 段复制到 headless：

```bash
cp ~/.dsh/profiles/desktop/cordis.patch.yml ~/.dsh/profiles/headless/cordis.patch.yml
```

**B. 直接给 headless 配一个 API key**

```bash
export DEEPSEEK_API_KEY=...
```

`dsh --profile headless "回复：OK"` 能返回结果，bridge 就一定能用。

> 注意：key 存在 `~/.dsh/.credentials.yaml` 里**不等于**已注入环境。如果 patch 里写了
> `apiKeyEnv: QWEN_API_KEY`，就必须真的 `export QWEN_API_KEY`。`bridge check` 的
> 「模型凭据」一项会区分这两种情况，`start_bridge.sh` 会自动从凭据文件读取并注入。

### 并发

同一「话题 + agent」一次只跑一个任务。不同话题、不同 agent 可以同时跑，互不阻塞。

## 排障

| 现象 | 原因 |
|---|---|
| 发消息没反应 | 应用没发布、没加 `im.message.receive_v1`、或 `lark-oapi` 没装 |
| 一直回「未授权」 | `allow_open_ids` 里没有你的 open_id |
| `bridge check` 报 lark-oapi 缺失 | `pip install 'lark-oapi>=1.8'` |
| 回「该会话正在被桌面 App 占用」 | 见上面的会话锁说明 |
| 回 `MISSING_CREDENTIAL` / `no credential for provider route` | headless profile 没拿到 key，见上面「模型凭据」 |

日志在 `~/.local/state/notifyhub/notifyhub.log`，会话映射在 `~/.local/state/notifyhub/bridge_sessions.json`（mode 600）。