# 官方原生公共 API

Crypto Market Monitor 只使用自行维护的官方公共 REST/WebSocket 适配器，不依赖
CCXT，不使用私有 API、API Key、签名、Broker、订单或交易返佣字段。

## 端点与时间语义

| 来源 | 公共 API | 关键约束 |
| --- | --- | --- |
| Binance | `api.binance.com`、`fapi.binance.com`、官方 spot/futures WS | spot REST 深度无源时间时不能用于有效时序判断；diff 需要连续序列 |
| OKX | `www.okx.com/api/v5`、`ws.okx.com` | `books5` 为完整快照；资金费使用源时间、结算周期和合约面值 |
| Hyperliquid | `api.hyperliquid.xyz/info`、WS `l2Book` | 只允许公共 info 方法；缺市场源时间时拒绝 |
| Bitstamp | `www.bitstamp.net/api/v2` | 只读取已审阅的 USD 交易对；微秒时间转毫秒 |
| Kraken | `api.kraken.com/0/public`、`ws.kraken.com/v2` | WS 校验 CRC32；心跳不刷新盘口源时间 |
| Bybit | `api.bybit.com/v5/market` | 盘口使用 `result.ts`，不用外部响应时间或本机时间补值 |

传输层使用固定官方主机、路径和参数白名单。请求不携带 API Key、签名、broker、
affiliate、referral、订单号或交易头；不跟随 HTTP 重定向，不保留 cookies。

价格和数量以 `Decimal` 解析，源时间与接收时间分开保存。过期、未来、乱序、
非有限、身份不一致、盘口价差过大或深度不足的数据都会被拒绝，不用接收时间重写
交易所源时间。

## 连接与重试

- REST/WS 请求有明确超时和关闭上限。
- 429/418 使用有界 `Retry-After` 退避；网络错误由调用方有限重试。
- WebSocket 断线、序列不连续、校验和失败或取消时丢弃该连接，不拼接旧状态。
- 同一客户端共享请求间隔，避免扩大采样后无限并发。
- 资金费等待新发布时可以显示 `waiting_for_fresh_publication`，但旧费率不参与
  新风险触发。

当前实现不是完整的交易所权重配额调度器。增加标的、缩短轮询间隔或扩展来源前，
需要重新检查每个端点的频率限制、共享 IP 预算和长期资源使用。

## 行为边界

监控阈值、确认/恢复/冷却状态机、事件身份和 SQLite 状态兼容性不以适配器重构为
理由改变。项目只做市场风险监控，不提供真实下单、私有账户读取、钱包签名或
任何交易执行能力。

## 官方依据

- [Binance Spot WebSocket](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)
- [OKX API](https://www.okx.com/docs-v5/en/)
- [Hyperliquid info](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint)
- [Bitstamp API](https://www.bitstamp.net/api/)
- [Kraken book](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book)
- [Kraken checksum](https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2)
- [Bybit orderbook](https://bybit-exchange.github.io/docs/v5/market/orderbook)
