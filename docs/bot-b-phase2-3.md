# Bot B Phase 2 + 3开发、测试与正式部署验收

状态更新（2026-10-27）：用户明确批准后，当前生产为bot-b-d990eef9731292f7。
OKX可靠性修复通过518项全回归、隔离12轮及上线3轮健康/历史保留/单实例/轮询检查。
服务7×24运行，详见bot-b-okx-fix.md及bot-b-deployment.md；以下保留此前阶段验收记录。

状态：2026-10-25用户明确批准后，当时生产为bot-b-b6d096b5a216db02，7×24 systemd运行。
最新USDC修复已完成487项全回归、隔离12点及生产三轮健康/单实例/轮询验收。
新增Kraken USDC/USD备用和60秒数据恢复确认；正式备份/回滚见bot-b-deployment.md。
以下主体保留2026-10-24初次可靠性发布记录，USDC增量详见bot-b-usdc-fix.md。
部署与受控重启通过，不再是待确认候选。Bot A、原凭据与全部原风险门限保持，未增加交易。

## 实现结果

- 持久状态机NORMAL→PENDING→ACTIVE→RECOVERING→RESOLVED；独立DATA_STALE保留风险身份。
- 事件/峰值/持续时间/发送记录同事务，重启恢复；数据缺口、计价路径变化重新确认，缺数不解除风险。
- 独立恢复阈值和确认；风险升级突破普通冷却；恢复后新事件不受旧事件冷却压制。
- B专用通知队列：最多5次重试、429退避、永久错误failed、45分钟普通冷却、有界3秒/3项合并。
- 严重通知优先，普通持续行情不推送；统一4种HTML模板、动态转义、UTC+8。
- 原Chat ID只读/status、/risks、/history、/settings；offset与回复原子提交，单poller锁，409停止争抢。
- 停服身份/旧指纹校验迁移；在线一致性副本演练；最终备份、原子切换、健康门禁和回滚。
- 独立非root用户/venv，SQLite/journald，192MiB内存上限、20%CPU、自动重启/看门狗/开机自启。

USDe保留Kraken直接美元，备用改Bybit USDE/USDT × Bitstamp USDT/USD。
双腿每5秒采集、逐腿版本校验及有效路由保持，解决静默源/单腿时间误判/主备抖动。
Bitstamp两项独立采集，一个慢请求不阻断另一个；源close异常不终止健康同伴或重连。
原主循环15秒；新鲜度/时差/流动性/阈值不放宽。专门说明见bot-b-usde-fix.md。

## 文件清单

新增核心：bot_b_policy.py、bot_b_lifecycle.py、bot_b_queue.py、bot_b_commands.py、
bot_b_migrate.py、bot_b_preflight.py、adapters/kraken_book_time.py、adapters/compact_bybit.py。

修改核心：market_data/risk_watch_config.py、risk_watch.py、derivative_watch.py、risk_session.py、
usde_config.py、usde_feed.py、usde_watch.py、stablecoin_feed.py、venues.py、adapters/compact_kraken.py、
risk_watch_cli.py、bot_b_templates.py、telegram_transport.py。
共享Telegram改动仅可选错误分类，原A默认格式/路由/重试行为不变。

新增候选配置config/bot-b-phase2-3.toml；原config/bot-b.toml保持。
新增deploy/upgrade-bot-b.sh、rollback-bot-b.sh；修改systemd unit及scripts/package-bot-b.py。
新增tests/test_bot_b_reliability.py、test_usde_sources.py、test_usde_composite_time.py、
test_stablecoin_feed_isolation.py；修改phase1/稳定币测试fixture及Dashboard冷启动等待。
文档：本报告、bot-b-deployment.md、bot-b-operations.md、bot-b-usde-fix.md和README/ROADMAP/DEVLOG。
正式切换这一轮没有修改生产源码或规则，只部署已测试的冻结发布并更新实际操作记录。

## 自动化测试

最终：398 passed in35.89s，无跳过；Ruff check和153文件format check通过。

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp .test-tmp/usde-fix-accepted-full --tb=short
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
```

覆盖触发/恢复/升级/去重、多事件合并、重启/迁移/事务回滚、429/永久错误/重试耗尽、
行情失效与恢复、授权/offset/409、USDe异步双腿与选路、客户端清理和Bitstamp隔离、Bot A回归。
所有异常场景模拟，不向真实Telegram制造风暴，不改变真实市场报价。

开发中修复过Dashboard测试30秒冷启动超时（单独4项功能通过后只将两处等待改60秒），
及旧稳定币fixture空市场列表伪造两盘口的问题；保留断言，没有跳过关键测试。
首次VPS USDe预检失败定位为CCXT漏映射真实Kraken快照时间；补同消息时间后再测。
后续静默与交叉问题修复及实绩见USDe报告，完整实际命令与失败记录见DEVLOG。

## VPS隔离与正式验收

Ubuntu24.04.5/Python3.12.3、1CPU/961MiB、无Swap；服务器非新机器，沿用原B环境。
新venv依赖固定沿用旧精确版本，CCXT4.5.89；包和逐文件SHA、pip/bash/unit校验通过。
正式包SHA256：5f6e66be5db394b90637fa5b7d57a6567baee7b52e7bd3b916894a6f58c821d0。

隔离副本迁移、两次--once、338.14秒含预热连续检查通过；20/20 USDe有效，20次源时间推进，
最大两腿skew6103ms；运行期采样RSS122.1MiB，单次启动峰值130.6MiB。

用户“直接切换”授权后：最终停服备份/生产迁移成功，健康门禁全部通过，current切至新版本。
受控重启后连续3轮全部行情源与真实Telegram轮询正常；原15分钟窗口仍62–63点，9个风险均NORMAL。
SQLite完整性ok、offset保留、sent仍6，无重复发送；一个B运行器，验收PID13125、NRestarts0。
真实进程RSS/HWM124.5MiB，cgroup内存约101MiB；192MiB上限/144MiB软限制、CPU20%有效。

Telegram：此前唯一一条“部署测试”真实发送已API确认；本轮未额外发测试或常规启动通知。
getMe认证和getUpdates真实轮询正常。命令解析/权限/回复入队由离线测试覆盖；
没有假冒用户发送指令，用户端实际四个命令的收发可在授权聊天查询验证。
生产当时没有活跃风险，真实触发/升级/恢复场景未制造；这些由模拟测试验证。

## 路径、运行与回滚

发布/opt/cql-bot-b/releases/bot-b-29aa4ec0a329ea01；服务cql-bot-b.service；
状态/var/lib/cql-bot-b；配置/etc/cql-bot-b/config.toml；原凭据/etc/cql-bot-b/telegram.env仍root0600。
新配置SHA256：80adc9b20f081b62de1294e07ba758015c99624c6bd714885062b6ba15e9461d。

最终备份/var/backups/cql-bot-b/pre-bot-b-29aa4ec0a329ea01-20261024T080112Z。
备份DB完整性通过，旧版本a7a686eef3c2a730保留。启停/日志/回滚命令见两份配套运维文档。
服务器备份目录post-cutover-verification.json及本机data/reports/bot-b-production-29aa4ec0a329ea01.json
保存上线后实际证据。历史隔离副本不能冒充或替换最终生产备份。

## 已知边界与下一阶段

7×24表示服务已常驻和自动恢复，不是已观察满24小时。后续继续观察数据失效率、内存及用户命令回包。
整机断网/关机不能自行发失联告警，异地监测/备份为后续；外部交易所失效仍严格DATA_STALE。
Telegram服务端确认与本地sent落库之间的崩溃仍可能造成一次重复，无法承诺API端exactly-once。
没有实盘、私有交易所调用或自动交易。以后生产修改继续按明确授权执行。
