# 开发记录

## 2026-11-10：单 Bot 项目整理与使用指南

目标是把仓库整理为一个独立的 Crypto Market Monitor Bot，删除旧项目来源、历史双机器人
说明和已失效的内部部署报告，并让新用户能够完成下载、安装、公开行情检查和 Telegram 连接。

### 实际修改

- README 重写为中英文独立项目说明，包含监控事件、功能、下载、安装、首次检查、
  常驻运行、Telegram 创建与 Chat ID 获取、身份验证、连接测试、只读命令和 systemd 示例。
- 新增项目级 Telegram 环境变量：
  `CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN`、
  `CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID`、
  `CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS`。
  旧部署变量仅作为代码内兼容回退，不再出现在新用户指南中。
- 新增本地 `scripts/telegram-chat-id.py`，只从 `getUpdates` 提取数字 Chat ID、
  聊天类型和标题，不输出 Token。
- 移除 Telegram 双机器人配置比较和旧 profile 回退；运行代码只接受单一 Bot 路径。
- 保留持久化兼容所需的内部 profile、SQLite 表名、事件前缀和数据库文件名，避免改变
  已有状态、事件 ID 和配置指纹。
- 删除旧项目迁移说明、来源清单和迁移测试，改为项目自身安装/发布边界测试及冻结配置
  SHA256 校验。
- 新部署目录、运行用户、systemd 服务名和仓库文档统一使用项目名称；删除已完成使命的
  旧内部部署报告。
- Telegram 配置、部署和运维文档重写，不再包含其他机器人或旧项目说明。

### 验证

- 聚焦测试：26 passed in 5.94s。
- 全量测试：249 passed in 48.09s。
- Ruff check：通过。
- Ruff format check：84 files already formatted。
- `scripts/check-repository.py`：99 files，0 findings。
- `git diff --check`：通过。
- 两份 PowerShell 工具语法检查：通过。
- README 与 docs 本地 Markdown 链接检查：缺失 0。

本轮没有访问公开行情、没有发送 Telegram 消息、没有读取或输出凭据，也没有执行 SSH、
生产停服、数据库迁移或服务切换。测试使用模拟 Telegram 和本地临时数据库。

### 限制与下一步

- `bot-phase4.toml` 相对已有 `bot-phase2-3.toml` 的状态迁移仍需单独审阅、隔离验证和
  明确批准，本次没有执行。
- 当前真实 Telegram 送达、长期行情完整性和 24 小时/7 天稳定性仍需在获得授权后进行；
  本地连接测试通过不能代替生产验收。
- 下一步是审阅 `bot-phase4` 迁移与影子运行结果，再决定是否进入生产切换审批。
