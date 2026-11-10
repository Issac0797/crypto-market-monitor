# Bot第一阶段：USDe与HTML通知（本地候选，未部署）

本轮仅修改本地源码、候选配置、测试和文档；没有连接生产服务器，没有修改服务器运行配置，
没有读取/更换凭据，没有向真实Telegram发送消息，没有增加命令、日报或交易功能。

## 配置与现有规则

原config/bot.toml保持逐字节不变，SHA256：
d2e95e8bd0a1115f43132bbfbaa644fb8c793b05cefb1bce229418f3ead683db。
新config/bot-phase1.toml复制原规则再增加[usde]；新旧配置自动化逐字段比较。
USDe可选；旧配置没有[usde]时不创建该数据订阅。不要用新配置直接打开既有生产状态目录：
现有配置指纹门仍会拒绝不一致，生产迁移和发布需下阶段准备并经用户确认。

现有BTC/ETH 15分钟±5%/30秒、基差±1%/60秒、预计资金费8小时±0.1%/60秒，
以及USDT/USDC原有买卖报价都越线±0.5%/60秒规则和冷却均保持不变。
基差仍是盘口中价比，不是永续标记价；通知明确显示“永续盘口中价”，不误标。

## USDe来源与公式

首选Kraken USDE/USD直接美元盘口，通过CCXT Pro watch_order_book接入，
复用已有CcxtPublicAdapter标准化。审计时公开元数据确认直接美元市场在线；
本阶段不联网验收，不声称已验证真实订阅的可用性或更新频率。

选择依据：直接法币USD计价；官方WebSocket v2订单簿包含源时间；不把USDT假定为美元。
CCXT 4.5.89的Kraken REST盘口统一timestamp为空；本实现不使用接收时间替代。
该版本Pro的初始snapshot可能无统一时间，必须等待带有效源时间的更新才参与判断。
官方契约：https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book

备选路径：Kraken USDE/USDT × Bitstamp USDT/USD。只有该实际市场元数据存在且两腿
都有效才可使用；未通过发现的可选USDE/USDT市场不会阻断直接美元市场。
本阶段未联网确认USDE/USDT是否实际可用，备选路径不能视为已有可用的生产容灾源。

合成bid = 第一腿bid × 第二腿bid；合成ask = 第一腿ask × 第二腿ask；
中价=(bid+ask)/2；偏离=中价/reference_usd−1。任何腿无效都不产生脱锚结论。

默认参数均在候选配置中：锚定1 USD、触发±0.5%、恢复±0.25%、确认60秒；
源年龄≤30秒、两腿源时差≤10秒、单腿及合成价差≤0.2%、每腿每侧美元深度≥1000。
价差=(ask−bid)/mid。交叉第一腿深度保守按USDT/USD bid换算；第二腿直接美元深度。
独立源时间推进确认，重复/倒序不推进，同时间不同值拒绝；缺口>45秒或报价路径切换
重置待确认，缺数保留已激活异常而不伪造恢复。恢复和升级复用原确定性状态机。
订阅超时60秒、重试5秒、元数据6小时刷新；采样节奏沿用现有15秒主循环。
来源身份配置仅允许已经审阅的上述交易所/币对，不接受任意URL或私有接口。

无效报价在health.json的usde_feed中保存原因，并沿用连续3次失败/恢复通知；
不伪造正常价格、不静默把USDE/USDT当USDE/USD。底层错误不打印Token或请求URL。
缓存只有最多2个USDe最新不可变盘口，在原进程内异步运行；没有新常驻进程或全量历史缓存。

## 通知与持久化

bot_templates.py负责四种HTML模板和转义。数字字段只按Decimal渲染，所有动态文本
转义；UTC毫秒通过固定UTC+8展示，不依赖服务器时区。不同类型只显示对应指标。
美元报价保留最多6位小数，百分比最多4位，避免把买卖价都舍入成相同值。

NotificationService新增可选formatter，默认仍是原format_message；TelegramAPI新增显式
send_html，原send和默认_request仍不携带parse_mode。只有Bot运行器选择HTML；
Bot A策略、路由、事件格式和凭据不变，HTML格式化器拒绝A事件。

新增bot_b_episodes、bot_b_feed_details两个小表，复用 Bot 原 SQLite：
异常起点（首次确认触发时间）、期间观测峰值、持续时长及故障起点/最近有效采集检查时刻。
证据更新、原规则状态和事件入队在同一事务内，回滚不会只提交其中一部分。
起点不包含触发前的待确认时间；旧库缺失历史不补造，显示历史未记录/已记录峰值历史不全。
故障时长从首次检查失败计至采集恢复，不推断两次检查间精确断线时刻。

不发送常规启动通知；只发送风险触发/升级/恢复和数据故障/恢复。
四种示例由离线测试中的真实状态机事件渲染，明确为模拟，不是实际行情或送达记录：
[data/reports/bot-phase1-template-examples.md](../data/reports/bot-phase1-template-examples.md)。

## 实际文件清单

修改源码：
- src/crypto_quant_lab/market_data/risk_watch_config.py
- src/crypto_quant_lab/market_data/risk_session.py
- src/crypto_quant_lab/market_data/risk_watch.py
- src/crypto_quant_lab/market_data/derivative_watch.py
- src/crypto_quant_lab/notifications.py
- src/crypto_quant_lab/telegram_transport.py
- src/crypto_quant_lab/risk_watch_cli.py

新增源码：
- src/crypto_quant_lab/adapters/compact_kraken.py
- src/crypto_quant_lab/market_data/usde_config.py
- src/crypto_quant_lab/market_data/usde_feed.py
- src/crypto_quant_lab/market_data/usde_watch.py
- src/crypto_quant_lab/bot_details.py
- src/crypto_quant_lab/bot_templates.py

新增配置/测试/文档：config/bot-phase1.toml、tests/test_bot_phase1.py、本文件。
更新文档：AGENTS.md、README.md、ROADMAP.md、DEVLOG.md。
本地模拟示例在data/reports/内，不提交本地数据。

## 下一阶段

- 真实USDe WebSocket与备选市场契约验收、报价更新节奏、流动性和长期内存测量。
- 审阅配置/数据库迁移、旧消息兼容、发布包和回滚方案后，再经用户确认部署；本轮不执行。
- 有限次数重试、同类事件合并、按类型冷却和恢复后新事件策略；本阶段沿用现有行为。
- 完整事件历史、UNKNOWN状态统一化和价格窗口跨数据缺口的状态保留。
- 经后续确认添加只读/status、/risks、/history、/settings和入站授权；当前仍无命令。
- 手机实际显示验收；本阶段只验证生成HTML、转义、长度及假传输，不声称真实送达。

Telegram确认与本地记录间崩溃仍可能重复；已有最多1000条终结发件箱保留和5分钟过期
机制不变。新增证据表不等于完整长期事件档案，未宣称实现此前审计中的全部后续事项。

## 自动化验收结果

- 新增USDe/模板验收：27 passed in 9.21s。
- 现有规则/通知/Bot定向回归：126 passed in 41.32s。
- 最终全项目：356 passed in 129.76s，无跳过。
- Ruff check及format check均通过。

全部离线假客户端；未执行真实Telegram测试或生产部署。实际命令和边界见DEVLOG。
