# notifyhub

Codex / DeepSeek Harness / Claude Code / Command Code 完成通知 · 飞书私聊 · Agent Skill

![notifyhub GUI](assets/gui-preview.png)

`notifyhub` 在 Codex 每一轮或每个 goal round 结束、DeepSeek Harness (`dsh`) session 结束、Claude Code / Command Code 一轮结束时，通过飞书应用机器人给你发一条私聊卡片。它同时是一个 Agent Skill：`SKILL.md` 会把「检查环境 → 验证飞书凭据 → 找 open_id → 写配置 → 测试 → 安装 hook / dsh 插件 → 排障」整条流程交给 agent。

## 功能

- Codex `Stop` hook，goal 模式每轮通知
- DeepSeek Harness `dsh` bundle 插件，监听 `session/event` 的 `turn/end`
- Claude Code / Command Code `Stop` hook，从 transcript 读取最终 `text`、最近提问和模型
- 飞书 Card 2.0 卡片：状态标题、会话名、最近提问、模型、剩余额度、Goal
- 最后一条 assistant 消息按 markdown 渲染（粗体、列表、行内代码、代码块、链接）
- 剩余额度来自 Codex `account/rateLimits/read`，带 10 分钟缓存
- 接收人自动发现：App ID/Secret 自动读取应用所有者 open_id，类型选项说明发给谁
- 本地 Web GUI：凭证、四个通知开关、额度、测试发送、DSH 安装、卡片预览
- 安装 / 合并 `~/.codex/hooks.json`，不修改已有的 `notify` 配置
- 发件箱：发送失败（例如梯子挂掉）不会丢消息，恢复后自动补发
- 飞书远程续聊：手机上发消息，本机续聊 dsh / codex 会话并回结果（长连接，无需公网 IP）

## 让本地 Agent 安装（推荐）

把仓库克隆到 Command Code 的 skill 目录，目录名必须是 `notifyhub`：

```bash
git clone https://github.com/oldtommmy/notifyhub.git ~/.commandcode/skills/notifyhub
```

然后在任意 Command Code 会话里直接发：

```text
读取 ~/.commandcode/skills/notifyhub/SKILL.md，按里面的流程帮我安装 notifyhub：
1. 检查 Python / Codex / dsh
2. 配置飞书应用私聊；需要 App ID / Secret 时再问我，不要打印 Secret
3. 发一张测试卡片
4. 安装 Codex Stop hook；如果我使用 DeepSeek Harness 桌面版，安装 desktop profile 插件；如果使用 dsh web，安装 web profile
5. 最后运行 doctor，并告诉我还需要在 Codex / DSH 里做什么
```

装好后也可以直接用 `/notifyhub` 调用。其他能读 `SKILL.md` 的 agent：把仓库放到它的 skills 目录，或直接把 `SKILL.md` 路径交给它，让它按流程执行。

> 安装前建议先 review 脚本：它会写 `~/.codex/hooks.json`、读取 Codex 本地数据库和 `history.jsonl`，并 spawn 本地 notifyhub；App Secret 只应通过环境变量或 GUI 提供。

## 手动配置

不想让 agent 代劳的话，克隆后按下面的「快速开始」手动执行：

```bash
git clone https://github.com/oldtommmy/notifyhub.git ~/.commandcode/skills/notifyhub
```

项目内使用：

```bash
git clone https://github.com/oldtommmy/notifyhub.git .commandcode/skills/notifyhub
```

安装后，在 Command Code 里用 `/notifyhub` 调用。

## 快速开始

前置条件：

- macOS，Codex CLI 或桌面 App 已登录
- Python 3.10+
- 可选：DeepSeek Harness (`dsh`)，用 `npx @deepseek-ai/dsh web` 或全局 `dsh`
- 飞书自建应用，开启机器人能力，添加 `im:message:send_as_bot`，并发布到租户

```bash
export SKILL_ROOT="$HOME/.commandcode/skills/notifyhub"
export NOTIFYHUB_FEISHU_APP_SECRET='...'

# 1. 验证凭据并找 owner open_id（不会发消息）
python3 "$SKILL_ROOT/scripts/feishu_probe.py" \
  --app-id cli_xxxxxxxx \
  --owner

# 2. 写配置（自动备份，chmod 600）
python3 "$SKILL_ROOT/scripts/setup_config.py" \
  --app-id cli_xxxxxxxx \
  --app-secret "$NOTIFYHUB_FEISHU_APP_SECRET" \
  --receive-id ou_xxxxxxxx \
  --receive-id-type open_id

# 3. 发测试卡片
python3 "$SKILL_ROOT/scripts/notifyhub.py" test --channel feishu_app

# 4. 安装 Codex hook
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks

# 5. 可选：安装 Claude Code / Command Code hook
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target claude
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-hooks --target commandcode

# 6. 可选：安装 DeepSeek Harness 插件
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile desktop
python3 "$SKILL_ROOT/scripts/notifyhub.py" install-dsh --profile web
```

然后打开 Codex，运行 `/hooks` 信任 `notifyhub` 的 Stop hook。桌面 App 用 Settings → Hooks。Claude Code / Command Code 安装后重启对应宿主，在 `/hooks` 里确认。

DSH Desktop 使用 `desktop` profile，`dsh web` 使用 `web` profile，需要分别安装；桌面版安装后重启 App，web 安装后重启 `dsh web`。桌面版官方推荐从 App 的 **设置 → 插件** 安装，CLI 写入 desktop profile 的方式实测可用，但 App 更新可能重置。也可以直接用 npm 包（发布后）：`dsh plugin --profile desktop add dsh-notifyhub` / `dsh plugin --profile web add dsh-notifyhub`，包内已带 Python launcher。

GUI：

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

接收人类型和接收人不需要手填：填 App ID / App Secret 后点「自动获取」，默认读取应用所有者 `open_id`（你自己），再回退到所有者邮箱或机器人所在群。类型菜单会说明每个选项发给谁。

## 验证

```bash
sh scripts/selftest.sh
python3 scripts/notifyhub.py doctor
```

## 发送失败与发件箱

网络抖动（代理挂掉、DNS 失败、握手超时）导致的所有渠道都失败时，通知会写入
`~/.local/state/notifyhub/outbox.json` 而不是被丢弃。之后任意一次 hook 成功发送时会
自动补发最多 3 条；也可以手动重放：

```bash
python3 scripts/notifyhub.py flush --status   # 查看积压
python3 scripts/notifyhub.py flush            # 重放
python3 scripts/notifyhub.py flush --retry-dead   # 修好权限后重试已放弃的条目
python3 scripts/notifyhub.py flush --clear    # 清空
```

重试策略区分两类错误：

- **可重试**：网络错误、超时、5xx/429 → 指数退避（60s 起，最长 1 小时，默认 8 次）
- **不可重试**：凭据错误 `99991663`、缺权限 `99991672`、非法 `receive_id` `230001`
  等 → 立即标记放弃，避免无谓重试把真正原因淹掉

`doctor` 会显示积压条数。配置项见 [references/config.md](references/config.md) 的
「发件箱」一节。

## 飞书远程续聊（可选）

在手机上给飞书机器人发一条消息，本机就续聊一个已有的 `dsh` / `codex` 会话，结果回飞书。

用的是飞书**长连接**（本机主动出站连飞书服务器），所以**不需要公网 IP、不需要内网穿透、
不需要 VPS 跳板机**，在 Tailscale 后面、家用宽带 NAT 后面都能工作。

```bash
python3 -m pip install 'lark-oapi>=1.8'
python3 scripts/notifyhub.py bridge owner    # 拿到你的 open_id
# 写进 bridge.allow_open_ids，然后：
python3 scripts/notifyhub.py bridge check
python3 scripts/notifyhub.py bridge start     # 前台试跑，Ctrl-C 停
```

**确认能用之后，装成常驻服务**（推荐）：

```bash
export QWEN_API_KEY=... NOTIFYHUB_FEISHU_APP_SECRET=...   # 可选，见下方说明
python3 scripts/notifyhub.py bridge service install
python3 scripts/notifyhub.py bridge service status
```

前台 `bridge start` 会**跟着启动它的进程一起退出**——在 Mac 上就是退出桌面 App 时
bridge 也被带走，于是「手机发消息没人收」。装成 launchd 服务后它就脱离 App 的进程树，
开机自启、崩溃自动重拉，退 App 也不再受影响。日常用：

```bash
notifyhub bridge service status      # 是否在跑
notifyhub bridge service restart     # 改完配置重启
notifyhub bridge service uninstall   # 停掉并移除
notifyhub bridge logs -n 50          # 看日志
```

手机上：直接发消息 = 续聊当前话题；`@codex ...` 这一条切 agent；
**`/topic 名字` = 在一个私聊里开多个互不相干的对话**；`/topics` 看列表；
`/new` 清空当前话题；`/status`、`/help`。

> 这是一个远程命令执行入口。`allow_open_ids` **默认拒绝所有人**，留空谁也用不了。
> 完整安全说明和已知限制见 [references/bridge.md](references/bridge.md)。

## UI

界面视觉参考 [LeiBox](https://leibox.pantoria.cn) 的玻璃卡片与 Glass Tab Menu 交互；预览状态切换是一个紧凑的玻璃分段控件。没有直接捆绑 LeiBox 的 WebGL/TS 组件，页面保持零依赖。

完整 agent 操作手册见 [SKILL.md](SKILL.md)；飞书应用配置、完整配置项、排障分别在：

- [references/feishu-setup.md](references/feishu-setup.md)
- [references/config.md](references/config.md)
- [references/troubleshooting.md](references/troubleshooting.md)
- [references/dsh.md](references/dsh.md)
- [references/bridge.md](references/bridge.md)

变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 目录结构

```text
SKILL.md
scripts/
  notifyhub.py          # CLI 入口
  notifyhub/            # Python 包（adapter、飞书通道、GUI、额度、发件箱）
  setup_config.py       # 安全写配置
  feishu_probe.py       # 凭据验证 / owner open_id / 测试发送
  selftest.sh           # 测试
integrations/dsh/       # DeepSeek Harness bundle 插件
references/
assets/gui-preview.png
tests/
```

## 安全

Skill 包含可执行的 Python 代码和 dsh bundle 插件，会写 `~/.codex/hooks.json`、读取 `~/.codex/history.jsonl` 和 Codex 本地数据库；dsh 插件会 spawn 本地 notifyhub launcher。安装或分享前请先 review 代码。

- App Secret 只保存在本机配置（mode 600）或环境变量中
- 通知内容会发到飞书，注意敏感项目
- 不要提交 `~/.config/notifyhub/config.json`

## License

MIT © 2026 oldtommmy
