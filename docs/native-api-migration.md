# 官方原生 API 迁移审计（2026-10-27）

用户明确要求取消 CCXT，本地实现已迁移到自行维护的官方公共 REST/WebSocket 连接。
本地虚拟环境已卸载 ccxt 4.5.89，项目依赖改为 aiohttp（通用 HTTP/WS 传输库）。
未操作 VPS、生产服务、生产数据库或 Telegram；生产仍使用此前发布版本。

## 审计结论

旧代码依赖 CCXT 的范围包括：六家公共行情/市场目录、Binance 研究与历史下载、
Bot 资金费/稳定币订阅，以及部署脚本中的固定 ccxt 安装。
本项目当前没有真实交易下单实现，Paper 为独立本地模拟账本。
未发现本项目向交易所发送下单请求或交易返佣标识的路径，因此不能据本项目代码
认定存在“实际交易返佣被扣取”，也不能据此对 CCXT 的所有版本和功能作结论。

删除 adapters/ccxt_public.py 和 Kraken 对第三方解析器的时间戳补丁，改用
native_public.py、native_client.py、native_markets.py、native_http.py、native_streams.py。
compact_* 工厂与 BinancePublicData/History 改为原生客户端，现有调用方继续消费不可变模型。
部署脚本不再安装 CCXT，候选打包包含本报告；旧发布/本地忽略数据中的历史脚本未覆写。
运行源码、测试和部署脚本不再导入 CCXT，无备用 CCXT 路径。

## 端点及字段契约

| 来源 | 公共 API | 时间/数量语义 |
|---|---|---|
| Binance | api.binance.com /api/v3；fapi.binance.com /fapi/v1；官方 spot/futures WS | spot REST 深度无源时间，不能作有效时序盘口；spot WS diff 以 REST 种子和 U/u 连续桥接，使用事件 E；永续 partial depth20 是完整顶层快照 |
| OKX | www.okx.com /api/v5/public/instruments、market/books、public/funding-rate；ws.okx.com public | books5 为完整快照；资金费 ts 为源时间，fundingTime 为下一结算，nextFundingTime-fundingTime 为真实周期；ctVal 是基础币/张且 settleCcy 必须匹配 quote |
| Hyperliquid | api.hyperliquid.xyz /info、WS l2Book | POST 仅允许公共 meta/spotMeta/metaAndAssetCtxs/l2Book；盘口原生 time；当前资金费缺发布时间，仍拒绝，不推算源时间 |
| Bitstamp | www.bitstamp.net /api/v2/trading-pairs-info/、order_book/usdtusd/、order_book/usdcusd/ | microtimestamp 微秒整除1000；仅两个已审阅美元交易对，保留20档 |
| Kraken | api.kraken.com /0/public/AssetPairs、Depth；ws.kraken.com/v2 book | REST 逐档最后修改时间不能当完整快照时间；WS 使用真实 timestamp，订阅10档，逐更新 CRC32 校验，失败丢弃本地状态并重新订阅 |
| Bybit | api.bybit.com /v5/market/instruments-info、orderbook | 明确 spot/USDEUSDT，使用 result.ts；不使用响应 time、本机时间或更旧的撮合 cts 替换 |

Polymarket 的独立 Gamma/CLOB 和预测市场研究接口保持原有边界。
原生方法名中的少数历史研究兼容名称只映射到固定官方 URL，不调用第三方 SDK。

传输层使用固定官方主机、路径和参数白名单；拒绝私有路径、未审阅参数、密钥及归因字段。
请求不传 API Key、签名、broker/affiliate/referral/tag、自定义订单号或交易头。
不跟随 HTTP 重定向，不保留 cookies。WS 仅有明确公共订阅载荷，无登录或交易操作。
同一客户端共享请求间隔；429/418 触发有界 Retry-After 退避，网络错误由原有调用方有限重试。
这不是全交易所权重配额调度器；扩大采样规模前需补全逐端点权重与共享IP预算。

源/接收时间分离，Decimal 解析价格和数量；JSON 浮点以 Decimal 解码以保留 Kraken CRC 精度。
Binance diff 缺序列、已知快照深度不足，Kraken 校验和失败、缺失时间或乱序均失败关闭。
单标的订阅复用 socket，失败/取消清理该连接；关闭等待有界，调用方保留既有重连调度。
Binance 元数据按显式标的筛选，避免全量 spot 目录超过有界响应限制。

## 行为边界

监控阈值、配置文件、确认/恢复/冷却状态机、A/B 分离、研究预设、费用假设和 Paper 规则未改。
normalized provenance 从 ccxt:* 改为 native:*，历史归档保留原来源。
默认 foundation 仍仅离线检查，空 detector 注册表；无真实下单或交易权限。
新版本生产切换需要先隔离预检和连续性/资源验收，再按项目既有规则取得明确切换授权。

## 验证

原有适配器/Bot 定向：117 passed；新增原生线协议首轮：27 passed。
无 CCXT 环境首轮全回归544项；最终全回归548 passed in 53.11s。
追加官方CRC参考向量/再次发现市场/资金费字符串时间/合约数量单位测试，最终定向88项通过。
`python -m ruff check .`、`python -m pip check` 通过；importlib 检查 ccxt_present=False。

有界实测命令：

```powershell
.venv/Scripts/python.exe scripts/native-api-smoke.py --once --output data/reports/native-api-smoke.json
.venv/Scripts/python.exe scripts/native-api-smoke.py --once --venues binance --output data/reports/native-binance-once.json
.venv/Scripts/python.exe scripts/native-api-smoke.py --once --venues okx --output data/reports/native-okx-once.json
```

实测成功：Binance BTC spot/perp WS 盘口及预计资金费（独立单次3/3），
OKX BTC spot/perp REST盘口及 WS 资金费（独立单次3/3），Hyperliquid BTC永续盘口、
Bybit USDE/USDT、Kraken USDC/USD，以及 Bitstamp 两个美元盘口（诊断轮次2/2）。
此前六家同时采集轮次有网络超时，整体退出1；Kraken USDE/USD快照源时间陈旧，明确拒绝。
初轮还定位/修复了 Binance 未筛选目录过大和 OKX 字符串时间与整数比较问题。
未把失败轮次、旧快照或最终未测端点算通过；部分来源单次可用不等于六源同时/长期通过。
上述 JSON 位于 Git 忽略目录，保留失败及修复后证据。所有采集均有界结束。

仍需验证：各来源更多标的/历史分页的真实契约、长时间行情完整性、重连资源和 VPS 原有
192MiB/CPU限制；费用、精度、最小量仍是已有待办。本次没有据单次行情宣称策略收益。

## 官方依据

- [Binance Spot WebSocket](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)
- [OKX API](https://www.okx.com/docs-v5/en/)
- [Hyperliquid info](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint)
- [Bitstamp API](https://www.bitstamp.net/api/)
- [Kraken book](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book)
- [Kraken checksum](https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2)
- [Bybit orderbook](https://bybit-exchange.github.io/docs/v5/market/orderbook)
