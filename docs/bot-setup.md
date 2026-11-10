# Telegram 配置与连接

Crypto Market Monitor 默认不启用 Telegram。启用后，机器人从官方 Telegram Bot API
发送风险通知，并只响应绑定 Chat ID 的只读查询。Token 和 Chat ID 不得写入 Git、
公开配置或日志。

## 1. 创建机器人

1. 在 Telegram 打开 [@BotFather](https://t.me/BotFather)。
2. 发送 `/newbot`，设置显示名称和以 `bot` 结尾的用户名。
3. 保存 BotFather 返回的 Token，但不要发送到聊天或粘贴到公开页面。
4. 打开机器人发送 `/start`。群组使用时，把机器人加入群组并发送
   `/start@机器人用户名`。

## 2. 获取 Chat ID

项目自带本地只读工具。先在当前终端设置 Token：

Windows PowerShell：

```powershell
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN = "<你的Bot Token>"
.\.venv\Scripts\python.exe scripts/telegram-chat-id.py
```

Linux/macOS：

```bash
export CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN="<你的Bot Token>"
python scripts/telegram-chat-id.py
```

工具调用 Telegram `getUpdates`，只输出 `chat_id`、聊天类型和标题，不输出 Token。
私聊 ID 为正数，群组 ID 通常为负数。如果结果为空，先在 Telegram 给机器人发送
一条新消息，再运行工具。若监控服务已经启动并持有 `getUpdates`，先安全停止该服务，
避免多个轮询所有者。

## 3. 配置环境变量

需要三个变量：

| 变量 | 说明 |
| --- | --- |
| `CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN` | BotFather 返回的 Token |
| `CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID` | 默认接收通知的数字 Chat ID |
| `CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS` | 允许查询的 Chat ID，逗号分隔 |

Windows PowerShell：

```powershell
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN = "<你的Bot Token>"
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID = "<目标数字Chat ID>"
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS = "<允许的数字Chat ID列表>"
```

Linux/macOS：

```bash
export CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN="<你的Bot Token>"
export CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID="<目标数字Chat ID>"
export CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS="<允许的数字Chat ID列表>"
```

环境变量只对当前进程及其子进程有效。Windows 可使用下面的 DPAPI 配置方式；
Linux systemd 使用 `EnvironmentFile=`，文件应为 `root:root 0600`。

## 4. Windows 本机加密配置

```powershell
.\scripts\telegram-bot.ps1 -Action Configure
.\scripts\telegram-bot.ps1 -Action Status
```

`Configure` 隐藏输入 Token 和数字 Chat ID，将 SecureString 通过 Windows DPAPI
保存到 Git 忽略的 `data/telegram-bot.clixml`；只有当前 Windows 用户可解密。
`Status` 只做离线配置检查，不代表 Telegram 网络可用。

## 5. 验证与测试

显式联网验证 Bot 身份与发送链路：

```powershell
.\scripts\telegram-bot.ps1 -Action Verify
.\scripts\telegram-bot.ps1 -Action Test
```

跨平台等价命令：

```bash
python -m crypto_market_monitor.bot_cli --status
python -m crypto_market_monitor.bot_cli --verify --telegram
python -m crypto_market_monitor.bot_cli --test-message --telegram
```

`Verify` 使用 `getMe`；`Test` 发送一条显式连接测试。只有
`verified: true` 或 `test_delivered: true` 才表示该步骤通过。

## 6. 启用监控

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state" --telegram
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir $state --telegram
```

`bot_cli` 的连接测试、`telegram-bot.ps1 -Action Run` 和常驻监控都可能持有
`getUpdates`。同一 Token 只能有一个轮询所有者；不要并行启动它们。

## 7. 只读查询

绑定 Chat ID 可发送：

| 命令 | 内容 |
| --- | --- |
| `/status` | 健康状态、快照年龄、行情源状态 |
| `/risks` | 当前 active、待确认或 `DATA_STALE` 风险 |
| `/history` | 最近 5 条风险或状态事件 |
| `/settings` | 当前阈值、重试和冷却参数 |

命令不提供启停、改配置、交易或下单能力。绑定群聊时，群成员可发起查询；
当前没有单独的用户级入站白名单。

## 安全边界

- Token 等同于机器人密码；不要打印、截图或提交。
- Chat ID 也属于私有路由信息，不应写入公开配置。
- 只使用本文件中的项目环境变量；旧部署若仍使用历史变量名，代码保留兼容读取，
  但新部署不应再使用它们。
- 服务端确认发送成功与本机 SQLite 记录 `sent` 之间发生崩溃时，可能重复一条消息；
  Telegram API 没有业务幂等键，不能承诺端到端恰好一次。

部署目录、服务管理和备份见 [部署说明](bot-deployment.md) 与
[运行恢复](bot-operations.md)。
