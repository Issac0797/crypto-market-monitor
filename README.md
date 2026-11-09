# Crypto Market Monitor

[简体中文](#简体中文) | [English](#english)

## 简体中文

Crypto Market Monitor 是一个独立、确定性的公开市场风险监控项目。
Bot 使用官方公共 REST/WS 行情，监控 BTC/ETH 现货快速波动、现货与永续基差、
预计资金费，以及 USDT、USDC、USDe 的美元偏离，并通过可选 Telegram 通知报告风险事件。

Bot 只读取公开市场数据，不接入私有交易所 API、不持有交易密钥、不下单，
也不使用 LLM 参与运行时判断。项目以 [MIT License](LICENSE) 开源。

### 监控事件

| 事件 | 监控对象 | 规则摘要 |
| --- | --- | --- |
| 短时急涨急跌 | BTC/USDT、ETH/USDT 现货盘口中价 | 15 分钟窗口内绝对变动达到 5% 后，持续确认 30 秒；回到 2.5% 以内按恢复规则处理 |
| 现货/永续基差 | BTC、ETH 现货与 USDT 永续盘口中价 | 绝对基差达到 1% 后，持续确认 60 秒 |
| 预计资金费 | BTC、ETH USDT 永续合约 | 当前预计资金费折算为 8 小时线性值达到 0.1% 后，持续确认 60 秒 |
| USDT/USDC 美元偏离 | 经过校验的 USDT/USD、USDC/USD 盘口 | 最优买卖报价整体越过 ±0.5% 后，持续确认 60 秒 |
| USDe 美元偏离 | Kraken USDE/USD，或 Bybit USDE/USDT × Bitstamp USDT/USD | 美元中价偏离 1 USD 达到 0.5% 后，持续确认 60 秒；回到 0.25% 以内按恢复规则处理 |
| 数据质量异常 | 全部行情源 | 拒绝过期、未来、格式错误、非有限、源时间不一致以及价差或深度不合格的数据；缺数进入 `DATA_STALE`，不据此宣告风险解除 |

价格事件还会区分触发、升级和恢复。预计资金费是当前预测值，不是已实现收益；
现货/永续基差是盘口中价观察，不代表可以实现的套利收益。

### Bot 功能

- 使用官方公共 REST/WS 适配器读取行情，保持来源时间和接收时间分离。
- 使用确定性 Python 规则判断事件，以 `Decimal` 处理金额，以 UTC 毫秒记录行情与接收时间。
- 校验报价身份、新鲜度、跨腿时间差、盘口价差和美元深度，拒绝不一致或不可信的数据。
- 使用 SQLite 持久保存事件、状态、峰值和发送记录；确认、升级、恢复、去重、冷却和重启恢复可追踪。
- 支持触发、升级、恢复和数据故障通知；通知队列具备有界重试、合并和持久化。
- Telegram 通知必须显式启用，并提供只读 `/status`、`/risks`、`/history`、`/settings` 查询。
- 支持有界单次行情检查，便于在不启动常驻轮询的情况下验证公开数据源。

### 下载与安装

需要 Python 3.12+ 和 Git。可通过以下命令获取代码，也可在 GitHub 页面选择
`Code` → `Download ZIP`：

```bash
git clone https://github.com/Issac0797/crypto-market-monitor.git
cd crypto-market-monitor
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --help
```

Linux/macOS：

```bash
. .venv/bin/activate
python -m pip install -e .
python -m crypto_market_monitor.risk_watch_cli --help
```

### 首次运行

先用新的空状态目录执行一次有界公开行情检查。该命令需要真实网络，失败不能视为通过。

Windows PowerShell：

```powershell
$state = Join-Path $HOME "crypto-market-monitor-state"
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --once --config config/bot-b-phase2-3.toml --state-dir $state
```

Linux/macOS：

```bash
python -m crypto_market_monitor.risk_watch_cli --once --config config/bot-b-phase2-3.toml --state-dir "$HOME/crypto-market-monitor-state"
```

状态目录保存 SQLite 事件状态和健康快照。首次使用应选择新目录；
不要将修改过配置指纹的新配置直接指向已有状态目录。`--once` 不发送 Telegram。

### 常驻运行

去掉 `--once` 后，同一入口会持续轮询公开行情：

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-b-phase2-3.toml --state-dir "$HOME/crypto-market-monitor-state"
```

生产环境应使用进程管理器保持单实例运行。启用 Telegram 时，同一 Token
只能有一个 `getUpdates` 轮询所有者，不能同时启动第二个实例或独立轮询脚本。

### Telegram 通知（可选）

默认不启用 Telegram。现有环境变量名保留兼容标识：

```powershell
$env:CQL_TELEGRAM_B_TOKEN = "YOUR_TELEGRAM_TOKEN"
$env:CQL_TELEGRAM_B_CHAT_ID = "YOUR_CHAT_ID"
$env:CQL_TELEGRAM_B_ALLOWED_CHAT_IDS = "YOUR_CHAT_ID"
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --config config/bot-b-phase2-3.toml --state-dir $state --telegram
```

真实 Token 和 Chat ID 只应保存在本机受限权限的环境文件或进程环境中，
不得写入 README、Git、日志或公开配置。详细配置见
[Telegram 配置](docs/dual-bot-setup.md)。

### Telegram 查询命令

当 `[reliability] commands = true`、显式传入 `--telegram` 且 Bot 常驻运行时，
绑定 Chat ID 可使用以下只读命令：

| 命令 | 返回内容 |
| --- | --- |
| `/status` | 总体健康状态、健康快照年龄、现货/基差、稳定币、USDe 和各资金费源状态 |
| `/risks` | 当前 active、待确认或 `DATA_STALE` 的风险，最多显示 15 项 |
| `/history` | 最近 5 条非命令回复的风险或状态事件 |
| `/settings` | 当前启用的价格、基差、资金费、稳定币和 USDe 阈值，以及发送重试上限和冷却时间 |

Bot 只响应绑定 Chat ID 发来的新消息；其他 Chat ID 不会获得查询结果。
命令仅读取 `health.json`、SQLite 状态和当前内存中的配置，不支持启停服务、修改配置、触发通知或交易。
未知命令会返回支持的命令列表。启用 Telegram 时必须保持单轮询实例，避免同一 Token
出现多个 `getUpdates` 所有者。

### Linux systemd 部署

先构建只包含源码、公开配置和部署材料的发布包：

```bash
python scripts/package-bot-b.py --config config/bot-b-phase2-3.toml
```

命令输出 `release_id`、归档路径和 SHA256。将归档传到目标 Linux 主机后，
可使用安装器校验并暂存发布：

```bash
sudo bash deploy/install-bot-b.sh <release-id> /path/to/<release-archive> stage
```

`stage` 会校验包内摘要、创建独立虚拟环境，不切换当前版本、不修改配置、不启动服务。
在确认目标主机和暂存结果后，才可使用 `activate`；它仍不会自动启动服务。
随附的 systemd 模板会以 `--telegram` 启动，并读取 `/etc/cql-bot-b/telegram.env`；
首次启用前应创建 `root:root 0600` 环境文件并填入前述变量。安装器模板使用
`config/bot-b.toml`；如果需要本 README 所述的 USDe 和可靠性规则，应在首次启动前
将 `config/bot-b-phase2-3.toml` 安装为 `/etc/cql-bot-b/config.toml`。
已有状态目录不能直接改用不同配置指纹，需先按迁移流程处理。

配置和凭据就绪后，可在目标主机启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cql-bot-b
sudo systemctl status cql-bot-b --no-pager
```

完整的主机目录、Telegram 环境文件、systemd 启停、升级、备份和回滚步骤见
[部署说明](docs/bot-b-deployment.md) 与 [运行与恢复](docs/bot-b-operations.md)。

## English

Crypto Market Monitor is an independent, deterministic public-market risk monitor.
The Bot uses official public REST/WS market data to monitor rapid BTC/ETH spot moves,
spot-perpetual basis, estimated funding, and USD deviations in USDT, USDC, and USDe.
Optional Telegram alerts report risk events and recovery.

The Bot only reads public market data. It does not use private exchange APIs,
hold trading keys, place orders, or include an LLM in the runtime decision path.
The project is released under the [MIT License](LICENSE).

### Monitored Events

| Event | Monitored market | Rule summary |
| --- | --- | --- |
| Rapid price move | BTC/USDT and ETH/USDT spot mid-price | A 5% absolute move over 15 minutes must persist for 30 seconds; return below 2.5% follows the recovery rules |
| Spot/perpetual basis | BTC and ETH spot and USDT perpetual mid-prices | A 1% absolute basis must persist for 60 seconds |
| Estimated funding | BTC and ETH USDT perpetuals | The current funding estimate, expressed as an 8-hour linear rate, must reach 0.1% and persist for 60 seconds |
| USDT/USDC USD deviation | Validated USDT/USD and USDC/USD order books | The complete best bid/ask must move beyond ±0.5% and persist for 60 seconds |
| USDe USD deviation | Kraken USDE/USD, or Bybit USDE/USDT × Bitstamp USDT/USD | The USD mid-price must deviate from 1 USD by 0.5% and persist for 60 seconds; return within 0.25% follows the recovery rules |
| Data-quality failure | All market feeds | Rejects stale, future, malformed, non-finite, time-inconsistent, spread-invalid, or depth-invalid data; missing data enters `DATA_STALE` and never implies risk recovery |

Price events distinguish entry, escalation, and recovery. Estimated funding is a forecast,
not realized income. Spot/perpetual basis is an order-book observation, not realized arbitrage profit.

### Bot Capabilities

- Reads markets through official public REST/WS adapters and keeps source and receipt timestamps separate.
- Applies deterministic Python rules, uses `Decimal` for money, and records market and receipt times as UTC milliseconds.
- Validates market identity, freshness, cross-leg timing, order-book spread, and USD depth; rejects inconsistent or untrusted quotes.
- Persists events, phases, peaks, and delivery records in SQLite, with traceable confirmation, escalation, recovery, deduplication, cooldown, and restart behavior.
- Sends trigger, escalation, recovery, and data-failure notifications through a bounded persistent queue with retries and batching.
- Enables Telegram only by explicit opt-in and supports read-only `/status`, `/risks`, `/history`, and `/settings` queries.
- Supports a bounded single-run market check without starting a continuously polling process.

### Download and Install

Python 3.12+ and Git are required. Clone the repository below, or use
`Code` → `Download ZIP` on GitHub:

```bash
git clone https://github.com/Issac0797/crypto-market-monitor.git
cd crypto-market-monitor
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --help
```

Linux/macOS:

```bash
. .venv/bin/activate
python -m pip install -e .
python -m crypto_market_monitor.risk_watch_cli --help
```

### First Run

Start with a new, empty state directory and run one bounded public-market check.
The command requires real network access, and a failed run is not a successful check.

Windows PowerShell:

```powershell
$state = Join-Path $HOME "crypto-market-monitor-state"
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --once --config config/bot-b-phase2-3.toml --state-dir $state
```

Linux/macOS:

```bash
python -m crypto_market_monitor.risk_watch_cli --once --config config/bot-b-phase2-3.toml --state-dir "$HOME/crypto-market-monitor-state"
```

The state directory stores SQLite event state and health snapshots. A new installation
should use a new directory; do not point a configuration with a new fingerprint at an
existing state directory. `--once` does not send Telegram notifications.

### Continuous Operation

Remove `--once` to keep polling public markets:

```bash
python -m crypto_market_monitor.risk_watch_cli --config config/bot-b-phase2-3.toml --state-dir "$HOME/crypto-market-monitor-state"
```

Use a process supervisor for production and keep exactly one polling instance.
When Telegram is enabled, only one process may own `getUpdates` for a given Token.
Do not start a second service or diagnostic poller at the same time.

### Telegram Notifications (Optional)

Telegram is disabled by default. The existing environment-variable names are kept
for compatibility:

```powershell
$env:CQL_TELEGRAM_B_TOKEN = "YOUR_TELEGRAM_TOKEN"
$env:CQL_TELEGRAM_B_CHAT_ID = "YOUR_CHAT_ID"
$env:CQL_TELEGRAM_B_ALLOWED_CHAT_IDS = "YOUR_CHAT_ID"
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --config config/bot-b-phase2-3.toml --state-dir $state --telegram
```

Keep real Tokens and Chat IDs only in a restricted local environment file or process
environment. Never write them to the README, Git, logs, or public configuration.
See [Telegram setup](docs/dual-bot-setup.md) for details.

### Telegram Query Commands

When `[reliability] commands = true`, `--telegram` is explicitly supplied, and the Bot
runs continuously, the bound Chat ID can use these read-only commands:

| Command | Response |
| --- | --- |
| `/status` | Overall health, health-snapshot age, spot/basis, stablecoin, USDe, and funding-feed statuses |
| `/risks` | Active, pending, or `DATA_STALE` risks, limited to 15 entries |
| `/history` | The five most recent non-command risk or status events |
| `/settings` | Enabled price, basis, funding, stablecoin, and USDe thresholds, plus retry limit and cooldown |

The Bot responds only to new messages from the bound Chat ID; other Chat IDs receive no results.
The commands read `health.json`, SQLite state, and the active in-memory configuration only.
They cannot start or stop the service,
change configuration, trigger notifications, or trade. An unknown command returns the supported
command list. Keep exactly one polling instance when Telegram is enabled; never create multiple
`getUpdates` owners for the same Token.

### Linux systemd Deployment

First build a source-only release archive containing source code, public configuration,
and deployment materials:

```bash
python scripts/package-bot-b.py --config config/bot-b-phase2-3.toml
```

The command prints the `release_id`, archive path, and SHA256. After transferring the
archive to the target Linux host, verify and stage it with:

```bash
sudo bash deploy/install-bot-b.sh <release-id> /path/to/<release-archive> stage
```

`stage` verifies the archive checksums and creates an isolated virtual environment
without switching the current release, changing configuration, or starting the service.
Use `activate` only after reviewing the target host and staged result; it still does not
start the service. The supplied systemd template starts with `--telegram` and reads
`/etc/cql-bot-b/telegram.env`; create that file as `root:root 0600` and fill in the
variables above before enabling it. The installer template uses `config/bot-b.toml`;
to enable the USDe and reliability rules described here, install
`config/bot-b-phase2-3.toml` as `/etc/cql-bot-b/config.toml` before the first start.
Do not point an existing state directory at a different configuration fingerprint;
follow the migration procedure first.

After configuration and credentials are ready, start the service on the target host:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cql-bot-b
sudo systemctl status cql-bot-b --no-pager
```

See [deployment](docs/bot-b-deployment.md) and
[operations and recovery](docs/bot-b-operations.md) for host paths, the Telegram
environment file, systemd commands, upgrades, backups, and rollback.
