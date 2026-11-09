# Bot B：USDe 监控可用性修复

状态：2026-10-24用户明确批准后，修复版已正式上线。隔离连续验证、生产切换及受控重启均通过。
本轮只处理 USDe 报价可用性与确认连续性，不改 Bot A，不改变既有监控阈值，不新增交易或普通行情播报。

## 问题与原因

此前隔离运行观察到 USDe 监控在 fresh 与 DATA_STALE 之间切换。这表示报价证据暂不满足质量要求，
不能据此得出 USDe 已经脱锚。原监控正确拒绝了过期报价；WebSocket 连接在线不代表盘口源时间足够新。

已识别的问题有三项：

1. Kraken USDE/USD 及原备用 USDE/USDT 市场可能长时间没有盘口更新。重新订阅返回的盘口源时间也可能相同，
   无法通过重连或心跳补造更新。上一轮已经补齐 CCXT 4.5.89 漏映射的真实快照时间，但不能解决市场静默。
2. 原交叉报价仅用两腿时间的最小值标识合成报价。一腿尚未更新、另一腿已更新时，合成价格可能变化而该时间不变，
   被误判为同时间矛盾报价，打断持续确认。
3. 每次检查都重新优先选择直接美元路径，导致短暂有效的 Kraken 直接报价与备用交叉报价来回切换，
   反复重置 60 秒确认，即使备用路径一直有效，也可能无法完成确认。

## 最小修改方案

保留 Kraken USDE/USD 直接美元路径。将本轮候选的备用第一腿改为 Bybit 现货 USDE/USDT，
继续复用 Bitstamp USDT/USD，合成真实美元参考价：

```text
美元买价 = Bybit USDE/USDT 买价 × Bitstamp USDT/USD 买价
美元卖价 = Bybit USDE/USDT 卖价 × Bitstamp USDT/USD 卖价
美元中价 = (美元买价 + 美元卖价) / 2
偏离率 = 美元中价 / 1 USD - 1
```

USDT/USD 不是固定为 1。任一腿过期、身份错误、深度不足或两腿时间差过大时，交叉路径无效，
不能继续触发或确认脱锚，也不能据缺数宣告原风险解除。

Bybit 与 Bitstamp 两腿每 5 秒采样，复用原进程中的异步任务和最新盘口缓存。
既有风险主循环仍为 15 秒；USDT/USDC 规则判断与全部原阈值保持不变。
仅发现所需 Bybit USDE/USDT 现货市场，不载入全交易所市场目录，不增加常驻服务。

交叉报价持久记录两腿各自的交易标识、真实源时间和盘口内容版本。重复版本不推进确认；
任一腿的新有效版本可形成新观测，不能因另一腿源时间没变而误判矛盾。
合成展示时间采用最新参与腿的源时间，两腿仍分别通过年龄和同步性校验，
不以本机接收时间替换交易所时间。

初始选择仍优先有效的直接美元报价。一旦使用交叉路径，只要该路径持续有效，就保持该路径，
避免直接市场偶发更新打断确认。当前路径失效时才重新选路；实际路径变化仍重新确认，
不把两种来源的证据无条件拼成连续异常。

## 数据源与时间语义

VPS 只读探测已确认 Bybit USDE/USDT 上市状态为 Trading。当次快照价差约 0.01%，
一档数量约 7 万至 8 万 USDE；这些是探测时的真实观察，不是固定深度保证或当前价格承诺。
源字段 ts 在探测中约每 4 秒推进，cts 可能早数分钟。

[Bybit 官方订单簿接口文档](https://bybit-exchange.github.io/docs/v5/market/orderbook)
区分系统生成盘口数据时间 ts 和撮合引擎时间 cts。当前 CCXT 4.5.89 的统一 timestamp 映射自 result.ts；
本轮复用这一已有映射，不用较旧的 cts 冒充 REST 快照生成时间，也不使用外层响应时间或本机时间补值。

[Kraken 官方订单簿文档](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book)
分别提供快照和更新的真实 timestamp。此前补丁仅恢复同一原始消息中被 CCXT 遗漏的快照字段；
缺少字段仍拒绝。两个来源均不因连接成功或收到心跳而刷新行情时间。

## 配置与质量门

候选配置：config/bot-b-phase2-3.toml。旧 config/bot-b.toml 与第一阶段历史配置不因此变更。

| 项目 | 本轮候选值 |
| --- | --- |
| 直接美元来源 | Kraken USDE/USD |
| 备用交叉来源 | Bybit USDE/USDT × Bitstamp USDT/USD |
| 交叉腿采样 | quote_poll_seconds = 5 |
| 偏离触发 | 绝对偏离 ≥ 0.5% |
| 触发/恢复持续确认 | 60 秒 |
| 独立恢复阈值 | 绝对偏离 ≤ 0.25% |
| 单腿最大源年龄 | 30 秒 |
| 交叉腿最大源时差 | 10 秒 |
| 单腿及合成最大价差 | 0.2% |
| 每腿每侧最小美元深度 | 1,000 USD |
| 最大观测缺口 | 45 秒 |

保持严格质量门，不通过放宽源年龄、两腿时差或深度限制来制造绿色健康状态。
DATA_STALE 独立于价格风险状态：已有活跃事件保留身份和峰值，重新获得有效报价后按恢复规则确认。

## 最终自动化验收

- 新来源/第一阶段专项：34 passed in48.93s；复合时间与相关规则专项：101 passed in51.06s。
- 关闭失败隔离专项：8 passed in23.41s；Bitstamp旧fixture修正后相关22 passed in1.63s。
- **最终全项目：398 passed in35.89s，无跳过**。
- Ruff check通过；format check：153 files already formatted。
- 最终命令：`.venv/Scripts/python.exe -m pytest -q --basetemp .test-tmp/usde-fix-accepted-full --tb=short`。

模拟测试覆盖单腿异步更新、同合成时间有效更新、逐腿倒序/同时间变价拒绝、SQLite事务回滚、
重复数据/缺腿不推进确认、重启、主备路径保持和真正失效后恢复确认。
也覆盖Kraken失败不阻断Bybit、源close异常后重建、USDC阻塞/超时不阻断USDT，以及Bot A与旧规则回归。
旧测试空instruments却生成两盘口的fixture已修正，原断言保留；未跳过关键测试、未放宽质量门。
所有异常场景用模拟数据和模拟Telegram，未人为改变生产行情。

## VPS实际验收

候选：`/opt/cql-bot-b/releases/bot-b-29aa4ec0a329ea01`。
源码SHA256：29aa4ec0a329ea01dd367813a5d4c062c0581cca8b29aff3a39e241d5af4b3cb。
本地包：data/bot-b-29aa4ec0a329ea01.tar.gz；包SHA256：
5f6e66be5db394b90637fa5b7d57a6567baee7b52e7bd3b916894a6f58c821d0。
包内报告为打包时版本，本段是打包后最终记录；不修改已冻结发布目录。

SSH最初因本机加密私钥未解锁失败；已启动已有手动ssh-agent，用户在本机解锁后恢复连接。
deploy可登录，但sudo需要口令；使用既有已授权root连接完成独立stage，监控始终以cqlbotb运行。
没有收取私钥/口令/Token，没有修改服务器SSH设置、密钥或生产配置。

SHA与逐文件校验、独立venv安装和pip check、两份bash脚本语法、systemd unit验证全部通过。
只读在线SQLite快照、隔离迁移通过：
`/var/lib/cql-bot-b-staging/preflight-29aa4ec0a329ea01`，数据库0600、目录0700。
新配置指纹：80adc9b20f081b62de1294e07ba758015c99624c6bd714885062b6ba15e9461d。

| 验收项 | 实际结果 |
| --- | --- |
| 第一次--once | 退出0，6.21秒，全部源fresh，failures=[] |
| 第一次进程峰值RSS | 133740KiB，约130.6MiB |
| 受限连续运行 | 338.14秒含预热，20个15秒主循环采样全部有效 |
| USDe路径 | 20/20均为Bybit USDE/USDT × Bitstamp USDT/USD |
| 独立推进 | 20个不同health时间，20个不同USDe源时间 |
| USDe状态 | 20/20 fresh且NORMAL，无采样期DATA_STALE |
| 最大两腿源时间差 | 6103ms，小于10000ms门限 |
| 采样点最大腿年龄 | 9794ms，小于30000ms门限 |
| 连续运行最大采样RSS | 124988KiB，约122.1MiB |
| 资源限制 | 同一cqlbotb测试unit，CPUQuota20%、MemoryMax192MiB；临时验证器共同计入unit |
| 原有源 | 全部采样的基差、USDT/USDC均fresh；预计资金费fresh或正常等待新发布 |
| 停止 | SIGTERM正常退出0，无残留候选进程 |
| 再次--once重启 | 退出0，22.40秒，峰值133720KiB；全部源fresh |
| 重启持久性 | 两腿版本保留并前进；SQLite完整性ok，USDe风险事件0条，旧sent仍6条 |

连续采样结果已保存于服务器隔离目录的usde-live-result.json，
本机证据为data/reports/usde-fix-vps-29aa4ec0a329ea01.json。
辅助验证器data/usde-fix-live-check.py只针对隔离目录，不是生产入口。
systemd-run退出摘要曾显示404KiB峰值，与/proc实测不符，因此不将该摘要当内存证据；
表中使用/usr/bin/time和/proc采样，资源限制由启动属性设置。

初始预热前有一次USDe缺数记录，取得第一批有效数据后恢复；20个验收样本均在预热完成后。
测试副本在有意停启间隔超过45秒后，BTC/ETH旧价格窗口按规则清空，需重新预热15分钟；
这不是风险恢复，原active身份仍被保留。生产实际价格窗口未操作，不能把隔离副本当生产库替换。
隔离发件箱出现的价格数据缺口通知保持未发送/到期，之前6条sent未重发。
本轮所有启动均无--telegram，未加载Telegram环境，未polling，未再发送部署测试消息。

## 实际文件

新增：
- src/crypto_quant_lab/adapters/compact_bybit.py
- tests/test_usde_sources.py
- tests/test_usde_composite_time.py
- tests/test_stablecoin_feed_isolation.py
- docs/bot-b-usde-fix.md

修改：
- src/crypto_quant_lab/market_data/usde_config.py、usde_feed.py、usde_watch.py
- src/crypto_quant_lab/market_data/derivative_watch.py、stablecoin_feed.py、risk_session.py、venues.py
- src/crypto_quant_lab/bot_b_lifecycle.py、risk_watch_cli.py
- config/bot-b-phase2-3.toml、scripts/package-bot-b.py
- tests/test_bot_b_phase1.py、test_stablecoin_watch.py
- README.md、DEVLOG.md、ROADMAP.md及Bot B Phase2/3、部署、运维文档

## 生产状态与下一步

最后只读核验生产仍为bot-b-a7a686eef3c2a730、PID4629、active、NRestarts=0。
原配置SHA256仍d2e95e8bd0a1115f43132bbfbaa644fb8c793b05cefb1bce229418f3ead683db。
未停服、迁移生产库或切换current。Bot A未被启停、策略/凭据/默认行为未改变。
正式切换需用户确认；切换时重新停服做最终备份，不能使用本轮测试副本覆盖生产。
具体切换与回滚见bot-b-deployment.md。

五分钟通过说明本次间歇失效和确认重置问题在验收中未复现，不能保证外部交易所永不故障。
任一腿真的断网、过期、价差过大或深度不足时，仍会暂停判断并报告数据质量问题。
生产真实命令响应、受控重启及更长期数据质量/内存观察，留在批准切换之后。


## 正式上线结果（2026-10-24，取代前文待确认状态）

用户明确要求“直接切换”，已执行最终备份、生产迁移、原子切换和健康门禁，无需再次确认同一次操作。
生产current现为bot-b-29aa4ec0a329ea01，systemd enabled/active，cqlbotb非root，7×24运行。
受控重启后连续三轮USDe有效、Telegram polling=true，9个风险状态NORMAL，SQLite完整、sent仍6。
原BTC/ETH15分钟窗口保留，单实例；进程RSS/HWM约124.5MiB，MemoryMax192MiB，CPU20%。
最终备份/var/backups/cql-bot-b/pre-bot-b-29aa4ec0a329ea01-20261024T080112Z。
生产验收证据及具体回滚命令见bot-b-deployment.md、bot-b-phase2-3.md。
本轮没有重复发部署测试消息或人为触发风险；以后生产修改仍需对应授权。
