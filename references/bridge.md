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
| `帮我跑一下测试` | 用默认 agent 续聊当前会话 |
| `@codex 检查这个 PR` | 切到 codex |
| `@dsh 继续` | 切到 dsh |
| `/new` | 丢弃上下文，下次发消息开新会话 |
| `/status` | 查看当前会话 id 和排队情况 |
| `/help` | 帮助 |

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

### 首次运行

`dsh` 第一次跑 `--profile headless` 会自动创建 profile。隔离的 `DSH_HOME` 里没有 API key 会报 `MISSING_CREDENTIAL`——bridge 会把这条错误原文回给你，按提示在 DSH 的 Models 页面配置即可。

### 并发

同一 `chat_id` + 同一 agent 一次只跑一个任务。不同 agent（dsh 和 codex）可以同时跑。

## 排障

| 现象 | 原因 |
|---|---|
| 发消息没反应 | 应用没发布、没加 `im.message.receive_v1`、或 `lark-oapi` 没装 |
| 一直回「未授权」 | `allow_open_ids` 里没有你的 open_id |
| `bridge check` 报 lark-oapi 缺失 | `pip install 'lark-oapi>=1.8'` |
| 回「该会话正在被桌面 App 占用」 | 见上面的会话锁说明 |
| 回 `MISSING_CREDENTIAL` | DSH 还没配模型 |

日志在 `~/.local/state/notifyhub/notifyhub.log`，会话映射在 `~/.local/state/notifyhub/bridge_sessions.json`（mode 600）。