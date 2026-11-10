# Bot USDC 数据可用性修复（2026-10-25）

状态：2026-10-25用户确认后已正式切换b6d096b5a216db02；生产连续健康检查通过。
正式切换前须用户确认，不发送额外 Telegram 测试消息，不改 Bot A、凭据或行情风险阈值。

## 实际原因

VPS 数据库中反复故障均集中在 stablecoin:USDC/USD，旧记录未区分失败原因。
2026-10-25 通过 VPS 直接调用当前 CCXT 适配器采集 Bitstamp 25 轮、两个美元市场：
USDC/USD 接口成功、价格和深度正常，但实际盘口源时间曾静止，年龄先后达到
32815ms、38253ms，违反 30000ms 门限，随后新盘口恢复；USDT 同期有效。
这是可复现的报价过期，不能归因于 USDC 脱锚、Bot 崩溃，也不能证明所有历史失败原因相同。

Kraken USDC/USD 元数据明确 active，原生 ID USDCUSD；真实 WebSocket 快照及三次更新
源年龄约 118–340ms，随后一次 20 秒等待超时。因此换源不能保证持续发布，仍需质量门。
Bitstamp 返回真实 microtimestamp，由 CCXT 映射毫秒，禁止以本机时间替换。
参考：[Bitstamp API](https://www.bitstamp.net/api/)、
[Kraken book](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book)。

## 最小修复

- reliability.usdc_fallback=true 启用 Kraken USDC/USD 备用。原配置缺省 false，Bot A 不启用。
- 独立有界异步订阅，只缓存一个不可变盘口；与 Bitstamp 采集互不阻塞，失败重连和关闭有界。
- 初始 Bitstamp 优先；当前有效来源粘滞，失效才换源。当前来源从 SQLite context 恢复。
- 两路均按真实源时间 30 秒、原美元深度及恢复价差门校验；只接受已审阅真实 USD 市场。
- 换源不拼接 60 秒风险确认；已有风险身份保留。通知来源随实际选择改变。
- 两路都不合格才记录 USDC DATA_STALE；不凭缺数解除风险。
- health.stablecoin_details 增加实际来源/时间/年龄或具体内部校验原因。
- reliability.data_recovery_ms=60000：已有数据故障须连续有效采集 60 秒才发恢复通知。
  中途失败或观测间隔超过原 max_gap_ms 重置；恢复候选及原故障起点跨重启保存。
  旧配置默认 0 保持旧行为；风险触发、风险解除阈值与确认时间均不变。
- 修复既有迁移器对已经包含 reliability 的旧配置比较错误；仅允许 reliability 变化，
  仍拒绝既有行情规则/USDe源配置改动，保留事件、样本、发送记录。

USDT/USD 和 USDe 的 Bitstamp 转换腿不改来源；新增订阅资源必须在原 192MiB 硬上限下验证。
单所报价切换代表市场观察来源改变，不等于跨所共同确认。

## 文件及验证

修改：bot_policy.py、bot_migrate.py、bot_details.py、risk_watch_cli.py、
adapters/compact_kraken.py、market_data/stablecoin_feed.py、stablecoin_watch.py、
risk_session.py、risk_watch.py、config/bot-phase2-3.toml、scripts/package-bot.py。
新增：tests/test_usdc_reliability.py、本报告；DEVLOG/ROADMAP 更新实际结果。

全项目离线测试：487 passed in 46.36s，无跳过；Ruff check 通过。
命令：.venv/Scripts/python.exe -m pytest -q --basetemp .test-tmp/usdc-full --tb=short。
包含跨源重新确认、粘滞和重启、双源失效保留 active、身份/禁用校验、
恢复抖动/间隙/重启持久性、已含 reliability 的迁移、独立重连/关闭及 Bot A 回归。
测试使用模拟数据/Telegram，不人为改变生产行情。

初次专项 73 通过、2 个新增测试断言失败（陈旧 receipt 先于 source 校验、sqlite Row 比较）；
修正测试为新接收的旧源报价及 tuple 比较，产品质量门未放宽，完整回归通过。

## 当时剩余验收（后续已完成）

候选单独版本/venv、在线只读数据库副本迁移、无 Telegram 连续采样及内存验证；
通过后提供具体版本/备份/回滚并等待正式切换确认。外部源仍可能同时过期或断网。


## VPS 隔离验收最终结果

候选 /opt/cql-bot/releases/bot-b-b6d096b5a216db02；源指纹
b6d096b5a216db022a50e16c95190d38a6a52006732c53355b58f28b6939670c。
包 data/bot-b-b6d096b5a216db02.tar.gz，SHA256
bbe562f5240ae5626e4274a3335098980a6a21c7594476b0c4f1084808c9f0b8。
独立venv/源码逐文件SHA/依赖检查通过。在线只读副本
/var/lib/cql-bot-staging/usdc-b6d096b5a216db02迁移成功，
新配置SHA256 9cee084e6c47e92108667a800d7ea77680da0b8c6e53ae59f7e61c9e83b9cee3。
真实 --once 退出0，所有数据源有效，无Telegram参数/凭据加载/轮询。

第一次验证器固定25秒预热不足，12点包含1个尚未就绪样本，严格断言失败，结果保留为
warmup-timing-result.json；其后11点有效，并实际观察Bitstamp失效后自动选择Kraken。
未将首次验证报告为全通过。只修正验证器为首批全部就绪后开始计数，后续失效仍严格失败；
第二次重启连续运行202.65秒，12个不同健康时间均有效：

- USDC、USDT、USDe均12/12有效；USDC12点沿用持久化Kraken来源。
- USDC最大源年龄26409ms，仍小于30000ms；不伪造源时间或放宽限制。
- 全部基差有效；资金费均有效或正常等待新发布。
- 候选进程最大采样RSS133868KiB，约130.7MiB；原CPU20%、内存192MiB硬上限。
- 候选SIGTERM正常退出0，临时unit已inactive，无候选残留/Telegram polling。
- SQLite完整性ok，旧sent36未增加；2条pending为隔离重启造成BTC/ETH预热通知，未发送。

第二次证据服务器result.json，本地data/reports/bot-usdc-preflight-b6d096b5a216db02.json。
不使用systemd-run汇总中与/proc明显不符的内存峰值。
最终生产核验：current仍29aa4ec0a329ea01、PID13125、active、NRestarts0，
原配置指纹80adc9b2…不变、SQLite完整性ok。未停止/替换生产或修改生产库。

正式批准后可执行：

```bash
bash /opt/cql-bot/releases/bot-b-b6d096b5a216db02/deploy/upgrade-bot.sh --approved-cutover /opt/cql-bot/releases/bot-b-b6d096b5a216db02
```

脚本停服后先创建/var/backups/cql-bot/pre-bot-b-b6d096b5a216db02-<UTC时间>完整备份，
再迁移/切换；健康门禁失败自动恢复旧版本。手动回滚需对本次实际备份路径执行同版本
rollback-bot.sh --approved-rollback <实际备份目录>。当前尚未执行，不存在本候选生产备份。
发布目录/清单保持冻结，本文最终补记在本地；包内报告为打包时预检版本。
真实源仍可能同时失效，数分钟验收不能代替长期缺失率观察。


## 正式上线（取代前文待批准状态）

用户明确确认“切换上线”，2026-10-25 20:48:14 UTC+8开始执行上述正式切换。
返回CUTOVER_HEALTH_VERIFIED、CUTOVER_COMMITTED，无回滚。
最终生产备份：/var/backups/cql-bot/pre-bot-b-b6d096b5a216db02-20261025T124814Z；备份和生产库完整性ok。
三轮健康时间前进、USDT/USDC/USDe有效、USDC自动从Bitstamp转到Kraken，polling=true。
9项风险NORMAL，原价格窗口各62项保留；sent36未增加，offset755061885保留。
唯一主进程PID21078、NRestarts0，enabled/active，原非root/192MiB/CPU20%限制保持。
实测RSS/HWM126276KiB约123.3MiB；未发送额外部署测试或普通启动消息。
证据位于备份目录post-cutover-verification.json及本地data/reports/bot-production-b6d096b5a216db02.json。
旧29aa4ec0a329ea01版本保留；当前回滚命令和日常管理见部署/运维文档。
本次批准仅覆盖此发布，未来生产变更仍按相应授权处理。
