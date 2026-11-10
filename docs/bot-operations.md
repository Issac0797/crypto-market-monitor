# Bot运行与恢复手册

2026-11-04已部署：[行情提醒模式](bot-market-notifications-2026-11-04.md)默认将数据故障、
数据恢复和缺口预热保留在健康/日志中，Telegram继续发送行情风险及其升级/恢复。
`--data-notifications`可显式恢复Telegram运维诊断；原配置指纹和风险门保持。
原生WS等待/文本心跳及元数据网络重试已随`bot-b-65740d54885a2039`上线，
详见[隔离验证、回滚与最终切换](bot-market-notifications-vps-2026-11-04.md)。

2026-10-30本地审计补记：首次GitHub Windows/Python3.12 CI暴露稳定币元数据刷新清理与
关闭取消的竞态。开发版确保共享适配器清理结束后再传递取消信号，close仍限时5秒；
风险阈值/刷新周期不变，新增事件同步离线回归。此修复已随2026-11-04原生版部署。

当前发布bot-b-65740d54885a2039，服务cql-bot.service；2026-10-27发布已成为回滚前版。
由systemd独立于本机/Codex/SSH会话运行，开机自启；异常退出15秒后重启，看门狗90秒。
不设置24小时后停止的定时器。以后停服、回滚或生产修改仍按对应用户授权执行。

## 查看与管理

```bash
sudo systemctl status cql-bot --no-pager
sudo systemctl show cql-bot -p MainPID -p NRestarts -p MemoryCurrent -p MemoryMax
sudo journalctl -u cql-bot --since '1 hour ago' --no-pager
sudo tail -n 30 /var/lib/cql-bot/logs/bot.log
sudo cat /var/lib/cql-bot/health.json
sudo systemctl start cql-bot
sudo systemctl stop cql-bot
sudo systemctl restart cql-bot
```

当前User/Group=cqlbot；MemoryHigh144MiB、MemoryMax192MiB、CPUQuota20%、TasksMax24。
本次隔离峰值RSS45,344KiB；最终切换后MemoryCurrent29,106,176字节，
MemoryCurrent为cgroup口径，和RSS不能直接等同。
修改资源或服务配置前需先复核其他服务和当前运行数据。

## 已启用规则

| 规则 | 来源与触发 | 确认 |
| --- | --- | --- |
| BTC/ETH急涨急跌 | OKX现货15分钟绝对涨跌≥5% | 30秒 |
| BTC/ETH现货–永续基差 | OKX盘口中价基差绝对值≥1%，不是标记价 | 60秒 |
| 极端预计资金费 | OKX预计费率按实际周期线性折算8小时，绝对值≥0.1% | 60秒 |
| USDT/USDC美元偏离 | Bitstamp兑USD，USDC可自动切Kraken USD；原规则要求买卖两侧报价均越过±0.5% | 60秒 |
| USDe美元偏离 | 有效美元中价绝对偏离≥0.5% | 60秒 |
| 数据异常/恢复 | 连续无效采集保留健康/日志；默认不推送Telegram，不据缺数解除风险 | 原故障机制 |

15分钟价格规则恢复为绝对值≤2.5%；基差≤0.5%；8小时预计资金费≤0.05%；
稳定币/USDe≤0.25%，分别持续确认后恢复。已移除5分钟±3%规则。
不发送日报、每小时汇总、普通行情或常规启动消息。所有金额按Decimal，通知UTC+8、HTML转义。

## USDe来源与数据可靠性

保留Kraken USDE/USD；备用Bybit现货USDE/USDT × Bitstamp USDT/USD，买价和卖价分别相乘。
USDT并非假定1美元。Bybit源时间为result.ts（系统生成盘口数据时间），不取旧cts、外层time或本机时间。
Kraken只取真实snapshot/update时间；心跳不刷新盘口时间。

首次优先直接美元；当前交叉路径有效则保持，防止短暂主源恢复反复重置确认。
实际换源仍重新确认，不拼接不同路径的60秒证据。每腿源时间/盘口版本持久校验，
倒序或同时间变价拒绝；合法异步更新接受，重复观测不推进时间。

两腿默认请求完成后间隔5秒采集，主规则仍15秒采样。网络耗时计入实际采集间隔。
单腿源年龄≤30秒，两腿差≤10秒；单腿/合成价差≤0.2%；每腿每侧深度≥1000美元。
Kraken、Bybit和Bitstamp各项失败隔离；USDC慢请求不阻断USDT缓存发布。
真正失效仍进入DATA_STALE，保留原active、事件ID/峰值，不当作风险恢复。

检查health.updated_ms应持续推进；basis_feed、stablecoin_feed、usde_feed通常fresh。
funding_feed的waiting_for_fresh_publication是正常等待，不能把旧资金费当新值触发预警。
此时总status可能为sampling_partial；应结合各源状态和时间判断，而非只看这个汇总词。
价格断档>45秒须重新预热15分钟；没有补造中间价格，已有风险身份保留。

## 通知、命令与单实例

SQLite保存NORMAL/PENDING/ACTIVE/RECOVERING/RESOLVED，DATA_STALE独立。
同事件首次触发一次，持续不刷屏；普通通知冷却45分钟，升级可突破；恢复后新风险用新事件ID。
普通同源同类最多等3秒合并3项，严重消息不等待合并。发送有限重试最多5次，429遵守retry_after，
永久错误或耗尽标failed；不能盲目把failed改回pending补发过期风险。

授权Chat ID可使用/status、/risks、/history、/settings；只读，不提供远程改配置/启停/交易。
同Token仅一个getUpdates所有者；不要另开polling调试器或另一台同Token服务。
service.lock和/run/cql-bot/poll-<bot_id>.lock为内核锁；文件存在不代表锁仍被占用，
不要靠删除锁文件或kill所有Python解除实例冲突。409冲突会停止继续争抢polling并暴露健康异常。

## 文件、日志与备份

- /etc/cql-bot/config.toml：公开规则与配置指纹，root0644。
- /etc/cql-bot/telegram.env：原凭据，root0600；不打印、不上传到仓库。
- /var/lib/cql-bot：cqlbot0700；data/bot-b.sqlite3为0600。
- bot_b_risks/history/gaps、derivative_source_versions和notification_outbox/meta为Bot 独立状态。
- 历史上限5000、终结发件箱1000；SQLite8192页上限（通常约32MiB）。
- 日志同时journald和512000字节轮转文件加2份备份；unit日志每小时30条限流。
- 日常SQLite在线备份保留latest/previous；同机备份不能覆盖整机/磁盘丢失。

配置支持文件参数，但当前运行库绑定指纹。不要直接编辑后强行重启或清空数据库绕过校验；
需审阅对应迁移，保留事件/发送记录与回滚。更换来源也需适配器身份审阅。

本次最终备份：/var/backups/cql-bot/pre-bot-b-65740d54885a2039-20261104T122918Z。
回滚命令（获授权时执行）：

```bash
sudo bash /opt/cql-bot/releases/bot-b-65740d54885a2039/deploy/rollback-bot.sh --approved-rollback /var/backups/cql-bot/pre-bot-b-65740d54885a2039-20261104T122918Z
```

先保存失败新状态/日志，再恢复配套旧DB/配置/unit/current。备份或完整性失败时保留现场排查。
具体部署和备份清单见bot-deployment.md。

## 已知边界

整机断网/关机时机器人无法自行发失联消息；异地监测和异地备份仍是后续事项。
Telegram确认成功到SQLite标sent间崩溃仍可能重复，API无业务幂等键，不能承诺绝对exactly-once。
数据真的过期/不足时暂停对应判断，7×24常驻不等于外部API永不故障。
本轮已验真实轮询、单实例、生产重启和9项状态；真实用户命令回包及长期24小时数据质量继续观察。


## 2026-10-25 USDC 修复版（已上线）

2026-10-25当时生产为b6d096b5a216db02，启用
`[reliability] usdc_fallback = true` 和 `data_recovery_ms = 60000`。
前者只增加Kraken USDC/USD备用，不替换USDT或USDe转换腿；后者只确认数据恢复通知，
不改变任何行情风险阈值。两路都失效仍暂停USDC判断。缺省false/0兼容旧配置。
health.stablecoin_details可查看实际选中的来源、真实源年龄或校验失败原因。
已有配置含reliability时的再次迁移已修复，必须通过备份/显式停服迁移执行，不能手动改指纹。
具体根因、验证和正式切换记录见[USDC修复](bot-usdc-fix.md)。

## 2026-10-27 OKX可靠性修复（已上线）

当前版本bot-b-d990eef9731292f7。Bot 四项OKX盘口并发、每项5秒最多两次，
共享CCXT限流器；同一OKX公共REST作为预计资金费备用。没有跨交易所替换资金费口径。
bot_b_price_warmup持久化预热通知去重，完整15分钟窗口就绪后重新允许下一次缺口通知。
正常等待发布可推进数据恢复确认，但旧费率不参与新风险触发，原时效/阈值不变。
上线三轮有效、仅一个 Bot 运行器、polling正常、sent71未增加、历史/offset保留。
通过不代表外部API永不故障；真实缺数继续暂停对应风险判断并按原机制通知。
见bot-okx-fix.md及bot-deployment.md；此前USDC章节为历史增量记录。
