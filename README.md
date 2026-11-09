# Crypto Market Monitor

Bot B 的独立市场风险监控项目，2026-11-09 从 `crypto-quant-lab` 拆出。
使用 Python 3.12+、本地官方公共 REST/WS 适配器和确定性规则；
不依赖原项目，不包含套利研究、Polymarket、交易执行或 LLM 决策。

项目仓库：<https://github.com/Issac0797/crypto-market-monitor>。
以 [MIT License](LICENSE) 开源；GitHub 名称与 Python 发行名一致。
仓库仅包含源码、测试、公开配置与文档，服务器密钥、Telegram 凭据、
本地加密配置、运行数据库和日志不进入 Git。

监控 BTC/ETH 15 分钟急涨急跌、现货/永续基差、预计资金费、
USDT/USDC 与 USDe 美元偏离。阈值、确认/恢复规则、配置字节、
Telegram B 环境变量、消息身份和 SQLite 表结构沿用原实现。
预计资金费是预测假设，不是已实现收益。

## 安装与检查

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -c "from pathlib import Path; Path('.test-tmp').mkdir(exist_ok=True)"
.\.venv\Scripts\python.exe -m pytest -q --basetemp .test-tmp/local --tb=short
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe scripts/check-repository.py
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --help
```

离线配置验证：

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig; RiskWatchConfig.load(Path('config/bot-b-phase2-3.toml')); print('configuration valid')"
```

有界公共网络检查（显式启动采集，不发送 Telegram）：

```powershell
.\.venv\Scripts\python.exe -m crypto_market_monitor.risk_watch_cli --once --config config/bot-b-phase2-3.toml --state-dir C:\path\to\new-isolated-state
```

Linux 使用 `.venv/bin/python` 和绝对状态路径。
该命令仍需真实网络，失败不能记作通过。
常驻使用同一入口去掉 `--once`；只有显式 `--telegram` 才启用通知，
配置使用 `CQL_TELEGRAM_B_TOKEN`、`CQL_TELEGRAM_B_CHAT_ID`、
`CQL_TELEGRAM_B_ALLOWED_CHAT_IDS`，B 不回退到 A 的凭据。
只读命令仅限绑定 Chat ID，禁止同 Token 多个轮询所有者。
本机加密配置及离线状态可使用
`.\scripts\telegram-bots.ps1 -Bot B -Action Configure` 和 `-Action Status`；
完整说明见 [B 配置](docs/dual-bot-setup.md)。

## 打包与生产边界

```powershell
.\.venv\Scripts\python.exe scripts/package-bot-b.py --config config/bot-b-phase2-3.toml
```

只打包本项目源码、公开配置与部署材料，输出到忽略的 `data/`，
不包含凭据、运行库或原项目源码。
安装器默认 `stage`，不修改生产指针、配置或服务；
生产安装须显式传入 `activate`，且仍需用户审阅结果后明确批准。

最初拆分仅迁移本地代码并移除原 GitHub 仓库当前树中的 B 专属内容；
没有创建新远端、改写 Git 历史、复制凭据或切换 VPS。
2026-11-09 用户另行授权将本项目发布为独立 GitHub 公开仓库；
该发布仅涉及源码，不授权生产切换。
保留现有 `/opt/cql-bot-b`、`/etc/cql-bot-b`、
`/var/lib/cql-bot-b` 和 `cql-bot-b.service` 标识，便于后续有审批的迁移。
现有 VPS 仍使用旧发布中的 `crypto_quant_lab` 包；
新部署模板使用 `crypto_market_monitor`，不能只修改旧服务的模块名。

[迁移边界与文件来源](docs/migration.md)、
[来源文件清单](docs/migration-manifest.json)、
[运行与恢复](docs/bot-b-operations.md)。
迁入的旧部署文档为历史证据，不能当成本项目已上线或已通过长期验收。
