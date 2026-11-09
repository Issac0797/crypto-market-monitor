# Bot B 独立迁移（2026-11-09）

来源：私有 `Issac0797/crypto-quant-lab`，具体提交与逐文件哈希见
[迁入清单](migration-manifest.json)。目标为本地 `crypto market monitor` 项目，
包名 `crypto_market_monitor`，发行名 `crypto-market-monitor`，拆分时没有远端。
同日用户另行授权发布为公开仓库 `Issac0797/crypto-market-monitor`，
采用 MIT 许可证；此授权不涉及运行凭据或生产部署。

所有 B 专属规则、价格/基差/资金费/稳定币数据任务、可靠性状态机、
模板/持久发送队列、Chat ID 只读命令、预检/配置迁移/备份、测试、
公开配置和部署脚本归本项目。共享代码以自包含复制方式保留，
两个项目不通过父目录、原 venv 或源仓库导入彼此。

配置 TOML 不变，因此配置指纹及风险阈值不变；不修改已存消息 ID、
Telegram route/profile、CQL_TELEGRAM_B_*、SQLite 表名或部署目录。
仅 Python 导入、模块执行路径和项目打包元数据切换为独立命名。
`systemd_notify.notify` 与原 notify 行为相同，不再导入 foundation 运行器。
`.gitattributes` 对三份 `config/bot-b*.toml` 禁止换行归一化，确保 Git 提交、
克隆及发布包保持原字节，不因 Windows/Linux 换行差异改变已存配置指纹。

清单中的 source_sha256 是迁出时原文；destination_sha256 在完成迁入后更新，
表示此次迁移完成的工作副本。后续正常开发不要求文件永远匹配该历史清单。
kind=moved 表示原项目移除；shared-copy 表示两边各自维护公共组件。
历史部署文档保留原命令和包名，属于过往证据；当前命令以 README、
实际 scripts/deploy 文件为准。新增本项目文件不在来源清单中。

本轮不复制运行数据库、日志、凭据或 SSH 配置，不启动第二个轮询实例。
原 GitHub 只通过普通提交移除当前树中的 B 专属文件，旧提交仍可追溯；
没有删除仓库、强推或重写历史。旧 VPS 发布自包含，不受源码仓库拆分影响。

后续部署需单独审阅隔离结果并批准：使用新项目打包，安装到新 release，
验证新 venv 的 crypto_market_monitor 导入、配置/DB 兼容、健康/资源和唯一 poller，
然后按已有备份/回滚流程切换。不能直接改旧发布的模块路径。
