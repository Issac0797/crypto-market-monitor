# Bot B行情提醒模式VPS部署记录（2026-11-04）

用户明确要求将修复代码部署到VPS运行，随后要求提交部署记录。本次已完成隔离验证、
生产切换和只读复核；以下时间以UTC为主，UTC+8另行标注。

## 发布与配置

- 源码提交：`5995968`，`fix: prioritize Bot B market alerts over transport diagnostics`。
- 当前发布：`/opt/cql-bot-b/releases/bot-b-65740d54885a2039`，`current`已指向此目录。
- 发布包SHA256：`6491511615ce78dc4e4773898f2a0044429c554e474b6b592052c5589cdd96ae`。
- 上一生产版本：`bot-b-d990eef9731292f7`；新运行环境使用本地官方REST/WS适配器，无CCXT。
- 配置SHA256仍为`9cee084e6c47e92108667a800d7ea77680da0b8c6e53ae59f7e61c9e83b9cee3`。
- `data_notifications=false`：数据不可用、恢复和缺口预热留在健康状态/日志，
  行情风险触发、升级、恢复和授权只读命令回复继续发送。原阈值、确认时间与数据质量门不变。

生产部署前24小时数据库记录39条`DATA_UNAVAILABLE`、39条`DATA_RECOVERED`、
4条`DATA_GAP_REWARMING`，支持“数据诊断反复刷屏”的审计结论。
这些记录不能逐次证明旧CCXT版本的底层故障原因；原生WS缺陷及修复另见
[源码审计与行为说明](bot-b-market-notifications-2026-11-04.md)。

## 隔离与生产验证

隔离运行195.23秒，13份健康快照，末6份均健康；峰值RSS45,344KiB，
进程退出0，SQLite完整性`ok`。隔离期间生产已发送数199→199，没有隔离Telegram发送。
候选运行环境无CCXT；独立`--once`成功，OKX四个盘口以及基差、资金费、
USDT/USDC/USDe检查有效。本地原先网络失败的smoke记录仍保留，不冒称其成功。

最终切换备份于2026-11-04 12:29:18 UTC（20:29:18 UTC+8）开始，
服务于12:29:36 UTC启动；安装器返回`CUTOVER_HEALTH_VERIFIED`和`CUTOVER_COMMITTED`。
随后三份样本均在采集时通过新鲜度门，独立验证`verified=true`。
最终切换前后SQLite完整性均`ok`，发送数201→201。

12:34:55 UTC只读复核：当前发布正确，Bot B PID104809、active/running、NRestarts0，
开机自启；健康快照距读取约14.4秒，四个OKX标的有效，采集失败列表为空，
基差、USDT/USDC及USDe新鲜，`command_polling=true`，`data_notifications=false`。
切换后记录的cgroup MemoryCurrent为29,106,176字节，不能与RSS直接比较。
资金费新鲜或正常等待新发布时，汇总`sampling_partial`不代表整个监控失效。

篮子PID76408/NRestarts1、包含PID93301/NRestarts0在最终切换前后保持，
本次没有切换这两个服务、改变Bot A行为、增加Telegram测试消息或启用交易。

## 首次误判、回滚与过渡消息

首次候选于12:24:20 UTC启动。额外部署验证器用较晚的当前时间重新验证早先采集的
快照，导致新鲜度误判并触发自动回滚；12:27:27 UTC旧服务恢复。
这是验证器错误，不能记作市场数据已经证实失效。
验证器随后改为在每份快照采集时记录检查结果，完成回归后再次切换成功。

回滚后的旧版本在12:27:31.258和12:27:31.260 UTC分别发送BTC、ETH的
`DATA_GAP_REWARMING`，缺口约210秒，发送总数199→201。
因此整个部署过程确实有两条额外数据诊断，不能声称全程零消息。
最终切换开始时已是201；截至12:34:55 UTC复核仍为201，
发件箱仅有201条sent，无pending/failed。本次未伪造行情或发送部署测试通知。

## 20:27消息专项复核

用户随后报告“8点27分报错”，与上述2026-11-04 20:27:31 UTC+8两条记录对应。
原因链为：额外验证器错误重查历史快照→误触发回滚→旧服务恢复后检测到约210秒缺口
→按旧规则发送缺口预热通知。并非最终新版本20:29:36启动后继续发出的数据错误。
验证器修正与通知静默代码已在上述发布切换中生效，本轮不重复重启已修复进程。

2026-11-04 20:43:11 UTC+8只读复核：PID104809、active/running、NRestarts0；
健康年龄约5.93秒，四标的有效，failures为空，基差/USDT/USDC/USDe均fresh，
command_polling=true、data_notifications=false。BTC/ETH各63份滚动价格样本，
两项15分钟价格风险均NORMAL，窗口已完成预热。
发件箱仍201条sent，无pending/failed，自20:27两条过渡消息后没有新增发送。
生产src文件逐项匹配发布清单，无源码哈希不一致。
本轮独立导出已部署源码复验：专项83 passed in14.56s、全量724 passed in105.19s，
Ruff check通过，format275文件通过。此结果仅适用于已部署5995968源码，
不覆盖工作区中并行开发的Polymarket候选。

## 备份与恢复

最终备份：
`/var/backups/cql-bot-b/pre-bot-b-65740d54885a2039-20261104T122918Z`。
服务器验证：该目录下`post-cutover-verification.json`。
本地隔离与有界报告：
`data/reports/bot-b-market-stage-65740d54885a2039.json`、
`data/reports/bot-b-market-once-65740d54885a2039.json`。
原始状态、报告、包及日志留在忽略目录，不提交仓库。

需要且获授权回滚时，先保存现场，再恢复配套旧DB、配置、unit和current：

```bash
sudo bash /opt/cql-bot-b/releases/bot-b-65740d54885a2039/deploy/rollback-bot-b.sh --approved-rollback /var/backups/cql-bot-b/pre-bot-b-65740d54885a2039-20261104T122918Z
```

不得仅切旧程序后继续使用不匹配的数据库；凭据沿用原受保护环境文件。

## 检查与剩余验收

部署源码最终全量回归724 passed in118.01s，Ruff check及format通过（275文件）。
源码提交`5995968`的GitHub Actions `41826397737`，
Ubuntu/Windows × Python3.12/3.13四组均成功。
部署记录文档是后续独立提交，其检查与实际CI结果另行报告，不借用源码CI结论。

195秒隔离与上线短窗不等于24小时/7天稳定性验收。真实行情触发后的用户端送达、
授权命令完整回包、长期原生重连/内存及外部源故障率仍待观察。
没有触发风险时不会定期播报普通报价，也不会制造风险消息；本次只读复核未主动调用命令或发送消息。
