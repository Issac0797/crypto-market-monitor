# 开发记录

## 2026-11-09：独立迁入 Bot B

用户要求将 Bot B 全部市场监控代码迁到已有空项目 crypto market monitor，
并从原 GitHub 当前版本移除；确认保留原项目两处并行 Polymarket 改动继续。
本项目无原有文件、提交或远端。源提交和逐文件 SHA256 见
docs/migration-manifest.json。

迁入独立包 crypto_market_monitor、专项测试、三份公开配置、历史模块文档、
源码打包、配置/状态迁移、凭据传输工具及 systemd/安装/升级/回滚模板。
公共行情模型/适配器和通知/操作基础组件自包含复制，研究仓库保留所需公共实现；
不建立跨目录导入或依赖，不搬入预测/套利研究。
systemd notify 抽成独立 stdlib 模块，移除对原 foundation 服务的导入。
生产目录、环境变量、消息身份和 SQLite 格式保持，公开配置字节不变。
安装器改为默认 stage；本轮没有 SSH、网络采集、Telegram 发送、凭据迁移或生产切换。

实际验证（本项目独立 .venv/Scripts/python.exe / Python 3.13）：

- 首轮专项因未先创建 .test-tmp 父目录，19 passed / 63 setup errors；
  创建目录后重跑，未跳过失败项目。首次 Ruff 发现改包名后的长行/导入顺序，
  已格式化修正。README 补建临时目录步骤。
- `-m pytest -q tests/test_risk_watch.py tests/test_derivative_watch.py
  tests/test_bot_b_market_only.py tests/test_bot_b_reliability.py
  tests/test_bot_connection.py tests/test_migration_boundary.py
  --basetemp .test-tmp/migration-focused-final --tb=short`：
  84 passed in 21.97s。
- `-m pytest -q --basetemp .test-tmp/migration-full --tb=short`：
  240 passed in 54.90s，无 skip。
- `-m ruff check .`、`-m ruff format --check .`、`-m pip check`通过；
  `scripts/check-repository.py`：104 文件、0 findings。
- risk_watch_cli --help 退出0；bot_cli --bot B --status 离线退出0，
  configured=false、network_started=false；未复制本地凭据，配置未就绪不是连通成功。
- 三份公开 TOML SHA256 与迁入前完全相同。64 份迁入 Python 源码/测试
  在还原包名及 systemd notify 导入后 AST 相同；另有明确调整的打包、
  独立连接工具/日志/systemd helper 与 Windows 启动器。
  来源清单共94项，53项 B 专属文件迁出，其他为共用复制或拆分。
- 暂存核验发现 Git 会将原 TOML 的 CRLF 转为 LF，导致配置指纹变化。
  对三份冻结配置禁用 text 归一化，保留原有 CRLF/末尾空行；
  属性仅声明这些既有换行合法，其他文件仍正常检查空白。
  直接读取索引 blob，三份 SHA256 均匹配迁入前原文。
  新增指纹回归后专项3 passed in1.45s，最终
  `-m pytest -q --basetemp .test-tmp/migration-final-full --tb=short`：
  241 passed in27.71s，无skip。新增测试一处format已修正；
  Git最初将CRLF报告为行尾空白，声明冻结配置原换行后重新检查。
- 源码包测试核对实际 tar 成员、全部摘要、三配置及独立包名，
  不包含 crypto_quant_lab、套利/预测源码、凭据或 data。
- `-m pip wheel --wheel-dir .test-tmp/wheels .` 构建普通 wheel，
  SHA256 d0c1bd3ff12a7832e16ca0a1ee774ba252015302dcbe94a350a5bc47b8cea30d。
  新建 .test-tmp/wheel-env，`pip install --no-index --find-links
  .test-tmp/wheels crypto-market-monitor==0.1.0`及 pip check 通过；
  在源目录外用 -I 导入全部包模块，确认安装路径属于新 venv，
  crypto_quant_lab 不可发现；risk_watch_cli --help 退出0。
- Windows Parser 核验本项目两份 PowerShell 工具，语法通过。
  本机无可用 bash，未冒称已执行 Linux 部署脚本或 systemd。

原研究项目实际全量606项通过，独立保留 A 与 Polymarket；其并行修改由原任务提交，
未纳入本项目或复制为原有已部署源码。本轮没有公共行情 smoke、Telegram 发送、
SSH/VPS 操作或新 GitHub 远端，因此没有新项目的远端 CI 或生产验收。
本地初始提交在全部检查通过后完成；提交号在交付说明。
下一项是用户单独审阅并批准后的隔离发布/生产迁移，不自动启动第二个 B 实例。
