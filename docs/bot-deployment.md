# Linux 部署说明

本文说明如何把 Crypto Market Monitor 作为单个 systemd 服务部署到 Linux。
部署前应先在本机完成 `--once` 公开行情检查和 Telegram 显式测试。项目不提供交易
执行，也不需要交易所 API Key。

## 前置条件

- Linux 与 systemd
- Python 3.12+
- `python3-venv`
- 可访问公开交易所 REST/WS；启用 Telegram 时还需访问 `api.telegram.org`
- 新的空状态目录

## 目录与用户

示例使用以下名称：

```text
/opt/crypto-market-monitor/venv
/etc/crypto-market-monitor/config.toml
/etc/crypto-market-monitor/telegram.env
/var/lib/crypto-market-monitor
```

```bash
sudo useradd --system --user-group --home-dir /var/lib/crypto-market-monitor --shell /usr/sbin/nologin cmm
sudo install -d -m 755 -o root -g root /opt/crypto-market-monitor /etc/crypto-market-monitor
sudo install -d -m 700 -o cmm -g cmm /var/lib/crypto-market-monitor
sudo python3 -m venv /opt/crypto-market-monitor/venv
```

在仓库目录安装当前源码：

```bash
sudo /opt/crypto-market-monitor/venv/bin/python -m pip install --upgrade pip
sudo /opt/crypto-market-monitor/venv/bin/python -m pip install .
sudo /opt/crypto-market-monitor/venv/bin/python -m pip check
```

## 公开配置

安装已审阅的配置并保持可读：

```bash
sudo install -m 0644 config/bot-phase4.toml /etc/crypto-market-monitor/config.toml
```

配置指纹由文件实际字节计算。不要用编辑器改变换行后继续使用旧状态库；阈值、
确认、恢复和数据质量参数应先审阅，不应为了启动而放宽。

## Telegram 凭据文件

先按 [Telegram 配置](bot-setup.md) 创建 Bot、获取 Chat ID，并把以下变量写入：

```text
CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN=<Bot Token>
CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID=<数字 Chat ID>
CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS=<逗号分隔的数字 Chat ID>
```

收紧权限：

```bash
sudo chown root:root /etc/crypto-market-monitor/telegram.env
sudo chmod 600 /etc/crypto-market-monitor/telegram.env
```

不要使用 `systemctl show Environment`、`cat` 或日志命令打印 Token。若目标机器由多人
管理，应把凭据文件权限进一步限制到实际运维账户，并禁止 shell 历史保存 Token。

## systemd unit

创建 `/etc/systemd/system/crypto-market-monitor.service`：

```ini
[Unit]
Description=Crypto Market Monitor
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=main
User=cmm
Group=cmm
WorkingDirectory=/var/lib/crypto-market-monitor
StateDirectory=crypto-market-monitor
StateDirectoryMode=0700
RuntimeDirectory=crypto-market-monitor
RuntimeDirectoryMode=0700
EnvironmentFile=/etc/crypto-market-monitor/telegram.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/crypto-market-monitor/venv/bin/python -m crypto_market_monitor.risk_watch_cli --config /etc/crypto-market-monitor/config.toml --state-dir /var/lib/crypto-market-monitor --telegram
Restart=always
RestartSec=15
WatchdogSec=90
TimeoutStartSec=90
TimeoutStopSec=20
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictSUIDSGID=true
LimitCORE=0
MemoryAccounting=true
MemoryHigh=144M
MemoryMax=192M
CPUQuota=20%
Nice=10
TasksMax=24
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

验证并启动：

```bash
sudo systemd-analyze verify /etc/systemd/system/crypto-market-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-market-monitor
sudo systemctl status crypto-market-monitor --no-pager
```

## 首次上线检查

1. 服务必须为 `active/running`，且只有一个主进程。
2. `health.json` 的 `updated_ms` 必须持续推进。
3. 现货/基差、稳定币、USDe 和资金费源应与配置一致；资金费短时显示
   `waiting_for_fresh_publication` 可能是正常等待新发布。
4. `command_polling=true` 表示 Telegram 只读命令轮询正常；未启用命令时可能为
   `disabled`。
5. 日志和 SQLite 不得包含 Token 或完整 Chat ID。
6. 同一 Token 只允许一个 `getUpdates` 所有者。

短时通过不等于长期稳定。外部交易所可能同时失效，整机断网时机器人的远端通知也会中断。

## 升级

升级前：

1. 停止服务，确保没有正在进行的采样或发送。
2. 备份 SQLite、`config.toml`、systemd unit 和当前发布版本。
3. 核对新配置指纹、阈值和 SQLite 兼容性。
4. 在隔离状态目录完成 `--once` 和连续健康检查。
5. 经人工审阅后再原子切换发布并启动。

不得用隔离测试副本覆盖生产状态，也不得让旧程序继续读取新格式数据库。

## 回滚

保留与新版本匹配的旧发布、旧配置、旧数据库和 unit。回滚时先保存故障现场，
再停止新服务、恢复配套文件并启动旧服务。不要只替换 Python 包而保留不兼容状态。

运行命令、日志、备份和恢复边界见 [运行与恢复](bot-operations.md)。
