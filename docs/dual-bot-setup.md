# Bot B 配置与连接工具

此项目只运行 Bot B；套利 Bot A 留在 crypto-quant-lab，凭据/状态/轮询所有者隔离。
旧文档名保留方便历史引用；本项目包名为 crypto_market_monitor。

## 本机加密配置

```powershell
.\scripts\telegram-bots.ps1 -Bot B -Action Configure
.\scripts\telegram-bots.ps1 -Bot B -Action Status
```

Configure 隐藏输入原 B Token 和数字 Chat ID，将 SecureString 通过 Windows DPAPI
保存到忽略的 data/telegram-bot-b.clixml；仅本机当前用户可解密。
Status 离线检查，不代表网络连通。Reconnect 可复用 B 加密配置或当前进程中的整组 B 变量，
随后验证身份并发送显式连接测试。不会搜索原项目或自动读取 A 配置。
原凭据没有随源码迁移或重新创建；VPS 的凭据仍在原位置。

## 显式联网

```powershell
.\scripts\telegram-bots.ps1 -Bot B -Action Verify
.\scripts\telegram-bots.ps1 -Bot B -Action Test
```

Verify 使用 getMe 验证原 B 身份；Test 通过 B 专用库发送连接测试。
只有 test_delivered=true 才表示本次确认送达。当前迁移没有运行这些命令。
等价离线命令：`python -m crypto_market_monitor.bot_cli --bot B --status`。
环境为 CQL_TELEGRAM_B_TOKEN、CQL_TELEGRAM_B_CHAT_ID、CQL_TELEGRAM_B_ALLOWED_CHAT_IDS，
不回退到 A。Chat ID 必须在白名单内；绑定路由和历史身份不允许转投。

`bot_cli`/脚本 Run 是独立连接诊断发送器；生产 risk_watch_cli 已拥有 B 的发送队列，
不可额外并行运行诊断发送器或第二个 getUpdates poller。
生产监控由 README 的 risk_watch_cli 入口加 --telegram 显式启用。
只读 /status、/risks、/history、/settings 限绑定 Chat ID；不提供控制或交易操作。

[迁移边界](migration.md)、[运维](bot-b-operations.md)。
