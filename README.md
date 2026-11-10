# Crypto Market Monitor

[简体中文](#简体中文) | [English](#english)

## 简体中文

Crypto Market Monitor 是一个独立、确定性的公开加密市场风险监控机器人。
它通过官方公共 REST/WebSocket 行情监控 BTC/ETH 价格异动、现货与永续基差、
预计资金费，以及 USDT、USDC、USDe 的美元偏离，并可通过 Telegram 推送风险事件。

机器人只读取公开市场数据，不接交易所私有 API、不保存交易密钥、不下单，
也不使用 LLM 参与运行时的风险判断。项目以 [MIT License](LICENSE) 开源。

### 监控事件

| 事件 | 监控对象 | 规则摘要 |
| --- | --- | --- |
| 短时急涨急跌 | BTC/USDT、ETH/USDT 现货盘口中价 | 5 分钟绝对变动达到 1% 或 15 分钟达到 5% 后，持续确认 30 秒；分别回到 0.5%、2.5% 以内按恢复规则处理 |
| 现货/永续基差 | BTC、ETH 现货与 USDT 永续盘口中价 | 绝对基差达到 1% 后，持续确认 60 秒 |
| 预计资金费 | BTC、ETH USDT 永续合约 | 当前预计资金费折算为 8 小时线性值达到 0.1% 后，持续确认 60 秒 |
| USDT/USDC 美元偏离 | 经过校验的 USDT/USD、USDC/USD 盘口 | 最优买卖报价整体越过 ±0.5% 后，持续确认 60 秒 |
| USDe 美元偏离 | Kraken USDE/USD，或 Bybit USDE/USDT × Bitstamp USDT/USD | 美元中价偏离 1 USD 达到 0.5% 后，持续确认 60 秒；回到 0.25% 以内按恢复规则处理 |
| 数据质量异常 | 全部行情源 | 拒绝过期、未来、格式错误、非有限、源时间不一致以及价差或深度不合格的数据；缺数进入 `DATA_STALE`，不据此宣告风险解除 |

价格事件区分触发、升级和恢复。预计资金费是当前预测值，不是已实现收益；
现货/永续基差是盘口中价观察，不代表可以实现的套利收益。

### 功能

- 官方公共 REST/WS 行情，来源时间与接收时间分离。
- 确定性 Python 规则、`Decimal` 金额计算和 UTC 毫秒时间；运行时不调用 LLM。
- 新鲜度、跨腿时间差、盘口价差和美元深度校验；异常或不一致数据会被拒绝。
- SQLite 持久保存事件、状态、峰值和发送记录，支持确认、升级、恢复、去重、
  冷却和重启恢复。
- 有界重试、同类消息合并、持久通知队列和单实例运行。
- 可选 Telegram 通知和只读 `/status`、`/risks`、`/history`、`/settings` 查询。
- `--once` 有界公开行情检查，无需启动常驻进程。

### 下载

需要 Python 3.12+ 和 Git。

```bash
git clone https://github.com/Issac0797/crypto-market-monitor.git
cd crypto-market-monitor
```

也可以打开 GitHub 仓库页面，选择 `Code` → `Download ZIP`，解压后进入目录。
后续命令都在该目录执行。

### 安装

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --help
```

Linux/macOS：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m crypto_market_monitor.risk_watch_cli --help
```

如需运行测试，可安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

### 首次检查

首次使用应创建新的空状态目录。以下命令会访问真实公开行情，网络失败不能算通过。

Windows PowerShell：

```powershell
$state = Join-Path $HOME "crypto-market-monitor-state"
New-Item -ItemType Directory -Path $state -Force
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --once --config config/bot-phase4.toml --state-dir $state
```

Linux/macOS：

```bash
mkdir -p "$HOME/crypto-market-monitor-state"
python -m crypto_market_monitor.risk_watch_cli --once --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state"
```

状态目录保存 SQLite 风险和健康快照。`--once` 会完成一次有界检查，但不发送 Telegram。
不同配置指纹不能直接共用一个旧状态目录。

### 常驻运行

去掉 `--once` 即可持续轮询：

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir $state
```

Linux/macOS：

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state"
```

生产环境应使用 systemd 等进程管理器保持单实例。Telegram 启用后，同一 Token
只能有一个 `getUpdates` 轮询所有者，不要同时运行第二个服务、调试轮询器或连接工具。

### Telegram 连接指南

Telegram 是可选功能，默认关闭。

1. 在 Telegram 中打开 [@BotFather](https://t.me/BotFather)，发送 `/newbot`，
   设置显示名称和以 `bot` 结尾的用户名。
2. 复制 BotFather 返回的 Token。Token 等同于机器人密码，只保存在本机受限权限的
   环境文件或进程环境中，不要发到聊天、Issue、日志或 Git。
3. 打开新建的机器人并发送 `/start`；如果使用群组，把机器人加入群组后在群里发送
   `/start@你的机器人用户名`。
4. 在项目目录运行下面的只读本地工具，读取当前更新中的 Chat ID：

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

脚本只输出 `chat_id`、聊天类型和标题，不输出 Token。私聊 ID 为正数，群组 ID
通常为负数。若列表为空，先在 Telegram 给机器人发送新消息再重试。

5. 设置三个环境变量。`ALLOWED_CHAT_IDS` 可用逗号填写多个数字 Chat ID：

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

6. 先做身份检查和显式连接测试：

Windows PowerShell：

```powershell
.\scripts\telegram-bot.ps1 -Action Verify
.\scripts\telegram-bot.ps1 -Action Test
```

也可以直接运行：

```bash
python -m crypto_market_monitor.bot_cli --verify --telegram
python -m crypto_market_monitor.bot_cli --test-message --telegram
```

只有返回 `verified: true` 或 `test_delivered: true` 才表示对应步骤通过。
`Status`/`--status` 是离线配置检查，不代表网络可用。

Windows 用户也可以运行 `.\scripts\telegram-bot.ps1 -Action Configure`，
将 Token 和 Chat ID 以当前 Windows 用户的 DPAPI 加密形式保存到被 Git 忽略的
`data/telegram-bot.clixml`，随后使用 `Verify`、`Test`。

7. 启用 Telegram 并启动监控：

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state" --telegram
```

Windows PowerShell 使用：

```powershell
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir $state --telegram
```

### Telegram 查询命令

当配置启用 `[reliability] commands = true`、显式传入 `--telegram` 且监控进程常驻时，
绑定的 Chat ID 可使用以下只读命令：

| 命令 | 返回内容 |
| --- | --- |
| `/status` | 总体健康状态、健康快照年龄、现货/基差、稳定币、USDe 和资金费源状态 |
| `/risks` | 当前 active、待确认或 `DATA_STALE` 的风险，最多 15 项 |
| `/history` | 最近 5 条非命令回复的风险或状态事件 |
| `/settings` | 当前启用阈值、发送重试上限和冷却时间 |

机器人只响应绑定 Chat ID 发来的新消息。命令只读取健康快照、SQLite 状态和内存配置，
不支持启停服务、修改配置、触发交易或下单。未知命令会返回支持的命令列表。
绑定群聊时，群成员可发起查询；当前没有单独的用户级入站白名单。

### Linux systemd 部署

下面是直接部署示例。不要把 Token 写入 unit 文件或公开配置。

```bash
sudo useradd --system --user-group --home-dir /var/lib/crypto-market-monitor --shell /usr/sbin/nologin cmm
sudo install -d -m 755 -o root -g root /opt/crypto-market-monitor /etc/crypto-market-monitor
sudo install -d -m 700 -o cmm -g cmm /var/lib/crypto-market-monitor
sudo python3 -m venv /opt/crypto-market-monitor/venv
sudo /opt/crypto-market-monitor/venv/bin/python -m pip install .
sudo install -m 0644 config/bot-phase4.toml /etc/crypto-market-monitor/config.toml
sudo install -m 0600 -o root -g root /dev/null /etc/crypto-market-monitor/telegram.env
```

用编辑器把三个环境变量写入 `/etc/crypto-market-monitor/telegram.env`，每行一个：

```text
CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN=<你的Bot Token>
CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID=<目标数字Chat ID>
CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS=<允许的数字Chat ID列表>
```

创建 `/etc/systemd/system/crypto-market-monitor.service`：

```ini
[Unit]
Description=Crypto Market Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
User=cmm
Group=cmm
WorkingDirectory=/var/lib/crypto-market-monitor
EnvironmentFile=/etc/crypto-market-monitor/telegram.env
ExecStart=/opt/crypto-market-monitor/venv/bin/python -m crypto_market_monitor.risk_watch_cli --config /etc/crypto-market-monitor/config.toml --state-dir /var/lib/crypto-market-monitor --telegram
Restart=always
RestartSec=15
WatchdogSec=90
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

启用并检查：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-market-monitor
sudo systemctl status crypto-market-monitor --no-pager
sudo journalctl -u crypto-market-monitor -f
```

更完整的部署与故障处理见 [部署说明](docs/bot-deployment.md)、
[运行与恢复](docs/bot-operations.md) 和 [Telegram 配置](docs/bot-setup.md)。

### 安全与状态兼容

- 不提交 `.env`、Token、Chat ID、SQLite、日志、SSH 密钥或本机虚拟环境。
- 新安装使用新的状态目录；已有状态目录不要直接切换到不同配置指纹。
- 为兼容已有部署，部分内部 SQLite 表名、事件前缀、数据库文件名和 Telegram
  路由标识保留历史值。它们不是公开机器人名称，也不应随意修改。
- 项目不提供交易执行、私有交易所 API 或远程配置修改。

## English

Crypto Market Monitor is a standalone, deterministic public-market risk-monitoring
Bot. It uses official public REST/WebSocket market data to monitor rapid BTC/ETH
moves, spot-perpetual basis, estimated funding, and USD deviations in USDT, USDC,
and USDe. Optional Telegram alerts report entry, escalation, recovery, and data
quality states.

The Bot reads public data only. It does not use private exchange APIs, hold trading
keys, place orders, or run an LLM in the runtime decision path. It is released under
the [MIT License](LICENSE).

### Monitored Events

| Event | Monitored market | Rule summary |
| --- | --- | --- |
| Rapid price move | BTC/USDT and ETH/USDT spot mid-price | A 1% absolute move over 5 minutes or 5% over 15 minutes must persist for 30 seconds; recovery uses 0.5% and 2.5% respectively |
| Spot/perpetual basis | BTC and ETH spot and USDT perpetual mid-prices | A 1% absolute basis must persist for 60 seconds |
| Estimated funding | BTC and ETH USDT perpetuals | The current estimate, expressed as an 8-hour linear rate, must reach 0.1% and persist for 60 seconds |
| USDT/USDC USD deviation | Validated USDT/USD and USDC/USD books | The complete best bid/ask must move beyond ±0.5% and persist for 60 seconds |
| USDe USD deviation | Kraken USDE/USD, or Bybit USDE/USDT × Bitstamp USDT/USD | The USD mid-price must deviate by 0.5% and persist for 60 seconds; recovery uses 0.25% |
| Data-quality failure | All feeds | Rejects stale, future, malformed, non-finite, time-inconsistent, spread-invalid, or depth-invalid data; missing data enters `DATA_STALE` and never implies recovery |

Price events distinguish entry, escalation, and recovery. Estimated funding is a
forecast, not realized income. Spot/perpetual basis is an order-book observation,
not realized arbitrage profit.

### Features

- Official public REST/WS feeds with separate source and receipt timestamps.
- Deterministic Python rules, `Decimal` money handling, UTC milliseconds, and no runtime LLM.
- Freshness, cross-leg timing, spread, and USD-depth validation.
- SQLite persistence for events, phases, peaks, and delivery records.
- Confirmation, escalation, recovery, deduplication, cooldown, restart recovery,
  bounded retries, batching, and single-instance operation.
- Optional Telegram alerts and read-only `/status`, `/risks`, `/history`, and `/settings`.
- Bounded `--once` public-market check without starting a long-running process.

### Download and Install

Python 3.12+ and Git are required.

```bash
git clone https://github.com/Issac0797/crypto-market-monitor.git
cd crypto-market-monitor
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m crypto_market_monitor.risk_watch_cli --help
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --help
```

You can also use `Code` → `Download ZIP` on GitHub and run the same commands from
the extracted directory. Install `-e ".[dev]"` instead if you want the test tools.

### First Run

Use a new empty state directory. This command accesses real public market data;
network failure is a failed check.

```bash
mkdir -p "$HOME/crypto-market-monitor-state"
python -m crypto_market_monitor.risk_watch_cli --once --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state"
```

The state directory stores SQLite risk state and health snapshots. `--once` does not
send Telegram notifications. Do not point a configuration with a different fingerprint
at an existing state directory.

### Continuous Operation

Remove `--once` to run continuously:

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state"
```

Use a process supervisor in production and keep exactly one polling instance. When
Telegram is enabled, only one process may own `getUpdates` for a Token.

### Telegram Connection Guide

Telegram is disabled by default.

1. Open [@BotFather](https://t.me/BotFather), send `/newbot`, and choose a display
   name and a username ending in `bot`.
2. Copy the Bot Token. Treat it like a password and keep it out of Git, chat, Issues,
   logs, and public configuration.
3. Open the new Bot and send `/start`. For a group, add the Bot and send
   `/start@your_bot_username` in the group.
4. Read the Chat ID locally without printing the Token:

```bash
export CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN="<your Bot Token>"
python scripts/telegram-chat-id.py
```

On Windows PowerShell:

```powershell
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN = "<your Bot Token>"
.\.venv\Scripts\python.exe scripts/telegram-chat-id.py
```

The helper prints only Chat IDs, chat types, and titles. Private chat IDs are positive;
group IDs are usually negative.

5. Configure the three runtime variables:

```bash
export CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN="<your Bot Token>"
export CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID="<target numeric Chat ID>"
export CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS="<comma-separated allowed IDs>"
```

On Windows PowerShell:

```powershell
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN = "<your Bot Token>"
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID = "<target numeric Chat ID>"
$env:CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS = "<comma-separated allowed IDs>"
```

6. Verify identity and send one explicit connection test:

```bash
python -m crypto_market_monitor.bot_cli --verify --telegram
python -m crypto_market_monitor.bot_cli --test-message --telegram
```

Only `verified: true` or `test_delivered: true` means the corresponding step passed.
`--status` is an offline configuration check, not proof of network connectivity.

On Windows, `.\scripts\telegram-bot.ps1 -Action Configure` stores the Token and
Chat ID with current-user DPAPI encryption under the Git-ignored `data/` directory.

7. Start the monitor with Telegram enabled:

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-phase4.toml --state-dir "$HOME/crypto-market-monitor-state" --telegram
```

### Telegram Query Commands

When `[reliability] commands = true`, `--telegram` is explicitly supplied, and the
monitor is running continuously, the bound Chat ID can use these read-only commands:

| Command | Response |
| --- | --- |
| `/status` | Overall health, snapshot age, spot/basis, stablecoin, USDe, and funding statuses |
| `/risks` | Active, pending, or `DATA_STALE` risks, limited to 15 |
| `/history` | The five most recent non-command risk or status events |
| `/settings` | Enabled thresholds, retry limit, and cooldown |

The Bot replies only to new messages from the bound Chat ID. Commands read the health
snapshot, SQLite state, and in-memory configuration. They cannot start or stop services,
change configuration, trade, or place orders. An unknown command returns the supported
command list. If the bound destination is a group, group members can issue queries;
there is currently no separate inbound user allowlist.

### Linux systemd

The Chinese section above contains a complete minimal systemd unit and environment-file
setup. Use project-owned names and keep `/etc/.../telegram.env` readable only by root.
The service must run with exactly one `getUpdates` owner. See
[deployment](docs/bot-deployment.md), [operations](docs/bot-operations.md), and
[Telegram setup](docs/bot-setup.md).

### Security and State Compatibility

- Never commit `.env`, Tokens, Chat IDs, SQLite files, logs, SSH keys, or virtual environments.
- New installations should use a new state directory; never point a different
  configuration fingerprint at existing state.
- Some internal SQLite table names, event prefixes, database filenames, and Telegram
  route identifiers retain historical values for upgrade compatibility. They are not
  public Bot names and must not be changed casually.
- The project has no trading execution, private exchange API, or remote configuration control.
