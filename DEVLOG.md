# 开发记录

## 2026-11-09：README 双语使用与部署说明

用户要求 README 将机器人公开名称统一为 Bot，介绍监控事件与功能，
并为其他使用者补充下载、安装、使用和部署说明。未修改运行代码、阈值、
配置字节、配置指纹、事件 ID 或 SQLite 状态。

README 现分为简体中文和 English 两部分，包含：

- Bot 的公开数据边界、确定性规则和可选通知概述。
- BTC/ETH 快速涨跌、现货/永续基差、预计资金费、USDT/USDC、USDe
  及数据质量异常事件表。
- 事件状态、持久化、通知队列、只读命令和单次检查等功能说明。
- Telegram 查询命令表：`/status`、`/risks`、`/history`、`/settings` 的用途、
  绑定 Chat ID 授权、只读边界和单轮询实例要求。
- `git clone`/GitHub ZIP、虚拟环境安装、首次 `--once`、常驻运行、
  可选 Telegram 和 Linux systemd stage/activate 部署入口。

公开名称在 README 中统一为 Bot；为保持兼容性，现有配置文件、脚本路径、
环境变量和部署目录中的历史标识未重命名。使用说明明确真实 Token/Chat ID
只保存于受限环境文件，不进入 README、Git、日志或公开配置。
部署说明标明安装器模板配置与 `config/bot-b-phase2-3.toml` 的差异，
并要求已有状态目录先走配置指纹迁移流程。

实际验证：

- README 名称检查：`Bot B`/`BotB`/`bot B`/`botB` 均未出现；
  所列本地许可证和文档链接存在。
- `-m pytest -q --basetemp .test-tmp/readme-final-full --tb=short`：
  241 passed in 66.05s，无 skip。
- 补充 Telegram 查询命令后再次运行
  `-m pytest -q --basetemp .test-tmp/readme-commands-full --tb=short`：
  241 passed in 40.93s；Ruff、格式检查和仓库守卫均通过。
- `-m ruff check .`、`-m ruff format --check .`通过，90 files formatted。
- `scripts/check-repository.py`：105 files、0 findings；`git diff --check`通过。

本轮没有执行公共行情网络检查、Telegram 发送、SSH/VPS 操作或服务启停；
README 中的 systemd 命令是部署说明，不表示已在任何主机执行。
下一验收是将 README 流程在独立主机按 stage 结果审阅，再决定是否激活。

## 2026-11-09：GitHub 开源发布

用户明确授权新建与项目同名的 GitHub 开源仓库，并要求不泄露服务器密钥。
目标为 `Issac0797/crypto-market-monitor`，名称沿用 Python 发行名。
开始时工作区干净，仅有初始提交 `52ed50f`，没有远端。
GitHub 连接器与本机 Git 认证账户一致；目标仓库查询返回 404。
连接器未提供建库接口，后续通过本机已有 Git 认证调用 GitHub API；
认证只在进程内使用，不写文件、不输出凭据。

对全部现有历史的 1 个提交、104 份文件版本执行仓库检查，0 findings；
另查凭据赋值候选，13 处均为检测规则、变量/路径表达式和测试样例。
扫描只输出路径、规则和行号，不输出匹配内容。
新增 MIT LICENSE、包元数据中的许可证与仓库地址，并将许可证加入源码包；
已有发布包测试新增许可证内容校验。更新公开仓库说明与授权边界。
三份冻结配置、运行规则、状态格式和生产部署均未改变。

实际验证（`.venv/Scripts/python.exe`）：

- 修改前 `-m pytest -q tests/test_repository_guard.py
  tests/test_migration_boundary.py --basetemp .test-tmp/github-publish-focused
  --tb=short`：14 passed in 2.79s。
- 修改后同一专项使用 `--basetemp .test-tmp/github-license-focused`：
  14 passed in 2.39s。
- `-m pytest -q --basetemp .test-tmp/github-publish-full --tb=short`：
  241 passed in 31.23s，无 skip。
- `-m ruff check .`：通过；`-m ruff format --check .`：
  90 files already formatted。
- `scripts/check-repository.py`：105 files、0 findings；`git diff --check` 通过。
- `.venv/`、`.test-tmp/`、`data/`、`.env`、私钥文件、凭据 JSON 和
  DPAPI 配置样例路径均由 Git 忽略。

实际索引 `scripts/check-repository.py --staged`：105 files、0 findings；
`git diff --cached --check` 通过，提交 `3c9d7aa`。
GitHub API 创建公开仓库 `Issac0797/crypto-market-monitor`，
`git remote add origin https://github.com/Issac0797/crypto-market-monitor.git`
及 `git -c credential.interactive=never push -u origin master` 成功。
使用未认证 GitHub API 复核 visibility=public、private=false、
default_branch=master、许可证 SPDX=MIT，远端 master 与本地
`3c9d7aacedc2acb4d0202159b4029335469becc7` 一致；
`git ls-remote origin refs/heads/master` 交叉核验一致，工作区干净。
随后仅补记本发布结果与路线完成状态，并单独提交同步。

规则扫描存在覆盖范围限制，
不将其表述为对任意形式秘密的绝对保证。本次不采集行情、不发送 Telegram，
不执行 SSH/VPS 操作，不迁移凭据或切换生产。
下一项验收仍为另行审批后的 VPS 隔离验证与生产切换；此次未运行远端 CI。

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
