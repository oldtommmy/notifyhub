# notifyhub

Codex 完成通知 · 飞书私聊 · Agent Skill

![notifyhub GUI](assets/gui-preview.png)

`notifyhub` 在 Codex 每一轮或每个 goal round 结束时，通过飞书应用机器人给你发一条私聊卡片。它同时是一个 Agent Skill：`SKILL.md` 会把「检查环境 → 验证飞书凭据 → 找 open_id → 写配置 → 测试 → 安装 hook → 排障」整条流程交给 agent。

## 功能

- Codex `Stop` hook，goal 模式每轮通知
- 飞书 Card 2.0 卡片：状态标题、会话名、最近提问、模型、剩余额度、Goal
- 最后一条 assistant 消息按 markdown 渲染（粗体、列表、行内代码、代码块、链接）
- 剩余额度来自 Codex `account/rateLimits/read`，带 10 分钟缓存
- 本地 Web GUI：凭证、四个通知开关、额度、测试发送、卡片预览
- 安装 / 合并 `~/.codex/hooks.json`，不修改已有的 `notify` 配置

## 安装

把仓库克隆到 skill 目录，目录名必须是 `notifyhub`：

```bash
git clone https://github.com/oldtommmy/notifyhub.git ~/.commandcode/skills/notifyhub
```

或者在项目内使用：

```bash
git clone https://github.com/oldtommmy/notifyhub.git .commandcode/skills/notifyhub
```

安装后，在 Command Code 里用 `/notifyhub` 调用。

## 快速开始

前置条件：

- macOS，Codex CLI 或桌面 App 已登录
- Python 3.10+
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
```

然后打开 Codex，运行 `/hooks` 信任 `notifyhub` 的 Stop hook。桌面 App 用 Settings → Hooks。

GUI：

```bash
python3 "$SKILL_ROOT/scripts/notifyhub.py" gui
```

## 验证

```bash
sh scripts/selftest.sh
python3 scripts/notifyhub.py doctor
```

## UI

界面视觉参考 [LeiBox](https://leibox.pantoria.cn) 的玻璃卡片与 Glass Tab Menu 交互；预览状态切换是一个紧凑的玻璃分段控件。没有直接捆绑 LeiBox 的 WebGL/TS 组件，页面保持零依赖。

完整 agent 操作手册见 [SKILL.md](SKILL.md)；飞书应用配置、完整配置项、排障分别在：

- [references/feishu-setup.md](references/feishu-setup.md)
- [references/config.md](references/config.md)
- [references/troubleshooting.md](references/troubleshooting.md)

## 目录结构

```text
SKILL.md
scripts/
  notifyhub.py          # CLI 入口
  notifyhub/            # Python 包（adapter、飞书通道、GUI、额度）
  setup_config.py       # 安全写配置
  feishu_probe.py       # 凭据验证 / owner open_id / 测试发送
  selftest.sh           # 测试
references/
assets/gui-preview.png
tests/
```

## 安全

Skill 包含可执行的 Python 代码，并且会写 `~/.codex/hooks.json`、读取 `~/.codex/history.jsonl` 和 Codex 本地数据库。安装或分享前请先 review 代码。

- App Secret 只保存在本机配置（mode 600）或环境变量中
- 通知内容会发到飞书，注意敏感项目
- 不要提交 `~/.config/notifyhub/config.json`
