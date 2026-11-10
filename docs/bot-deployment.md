# Bot 正式部署记录

## 2026-11-04 当前生产：行情提醒模式与原生适配器

已按用户明确要求部署`bot-b-65740d54885a2039`，源码`5995968`。
最终备份始于12:29:18 UTC（20:29:18 UTC+8），新服务12:29:36 UTC启动；
PID104809、active/running、NRestarts0、enabled，配置SHA256仍为下述9cee084e…。
`data_notifications=false`，行情风险通知与原阈值/数据质量门保留。
195.23秒隔离、首次验证器误判回滚、旧版本两条过渡预热通知、最终健康及恢复命令，
详见[完整部署证据](bot-market-notifications-vps-2026-11-04.md)。
最终备份：`/var/backups/cql-bot/pre-bot-b-65740d54885a2039-20261104T122918Z`。
下文2026-10-27及更早版本、PID和恢复路径均为历史记录。

## 2026-10-27 历史发布记录

2026-10-27用户明确回复“上线”后，OKX可靠性修复版已正式切换为bot-b-d990eef9731292f7。
切换备份开始时间2026-10-27 19:19:27 UTC+8，健康门禁和上线三轮检查通过，无回滚。
服务cql-bot.service为enabled/active，非root用户cqlbot，唯一运行器PID35015，NRestarts0。
Restart=always、15秒自动重启、90秒看门狗；MemoryHigh144MiB/Max192MiB、CPU20%、Tasks24。
生产RSS127420KiB（124.43MiB）；cgroup MemoryCurrent106352640字节，两个口径不同。

BTC/ETH现货/永续盘口4/4，基差有效，资金费有效或正常等待发布，USDT/USDC/USDe有效。
Telegram真实轮询command_polling=true；未额外发送测试消息或假冒用户命令。
前后9项风险均NORMAL，历史事件ID和已发送ID全部保留，sent71不增加，offset755061886保留。
15分钟价格窗口各63→62样本为正常滚动，未进入缺口预热；SQLite前后integrity_check=ok。
配置SHA256仍9cee084e6c47e92108667a800d7ea77680da0b8c6e53ae59f7e61c9e83b9cee3。
Bot A及原凭据不变。真实用户命令回包及长期外部源故障率仍需后续观察。

备份：/var/backups/cql-bot/pre-bot-b-d990eef9731292f7-20261027T111927Z，保留旧可运行版本bot-b-b6d096b5a216db02。
服务器证据：备份目录post-cutover-verification.json。
本地证据：data/reports/bot-production-d990eef9731292f7.json。
回滚在需要且获授权时执行（先保存现场，再恢复配套旧DB/配置/unit/current）：

```bash
sudo bash /opt/cql-bot/releases/bot-b-d990eef9731292f7/deploy/rollback-bot.sh --approved-rollback /var/backups/cql-bot/pre-bot-b-d990eef9731292f7-20261027T111927Z
```

## 2026-10-25 历史发布记录

2026-10-25用户明确确认“切换上线”，USDC修复版已正式运行，7×24 systemd常驻。
切换开始：2026-10-25 20:48:14 UTC+8。健康门禁和上线连续三轮检查通过，无回滚。
Bot A、凭据和原行情风险门限未改；未新增交易、日报或额外Telegram测试消息。

## 2026-10-25 当时生产

- VPS：<VPS_HOST>，Ubuntu24.04.5 LTS、Python3.12.3、1CPU、961MiB RAM、无Swap。
- 发布：/opt/cql-bot/releases/bot-b-b6d096b5a216db02；current已原子切换。
- 服务：cql-bot.service，enabled/active，非root User/Group=cqlbot。
- 配置：/etc/cql-bot/config.toml，root0644；SHA256：
  9cee084e6c47e92108667a800d7ea77680da0b8c6e53ae59f7e61c9e83b9cee3。
- 凭据：/etc/cql-bot/telegram.env，原Token/Chat ID，root0600。
- 状态：/var/lib/cql-bot，数据库data/bot-b.sqlite3，cqlbot0600。
- 自动恢复：Restart=always、RestartSec=15、WatchdogSec=90，开机自启。
- 资源：MemoryHigh144MiB、MemoryMax192MiB、CPUQuota20%、TasksMax24。
- 本次实测PID21078，NRestarts0，VmRSS/HWM126276KiB约123.3MiB，Threads4。
  cgroup MemoryCurrent105078784字节，与RSS口径不同。

SSH沿用已有Windows OpenSSH代理/密钥及已授权root连接；服务始终以cqlbot运行。
没有请求或输出私钥、口令、Token，没有更改SSH设置。

## 发布与切换

复核候选逐文件SHA和隔离验收后执行：

```bash
bash /opt/cql-bot/releases/bot-b-b6d096b5a216db02/deploy/upgrade-bot.sh --approved-cutover /opt/cql-bot/releases/bot-b-b6d096b5a216db02
```

返回CUTOVER_HEALTH_VERIFIED、CUTOVER_COMMITTED。停旧B后最终备份，再迁移、安装配置和unit、
原子切换current并启动。生产配置80adc9b2…迁至9cee084e…；没有用隔离数据库覆盖生产。
源码指纹b6d096b5a216db022a50e16c95190d38a6a52006732c53355b58f28b6939670c。
本地包data/bot-b-b6d096b5a216db02.tar.gz，SHA256：
bbe562f5240ae5626e4274a3335098980a6a21c7594476b0c4f1084808c9f0b8。
发布目录保持冻结；包内报告是打包时版本，本文记录后续实际操作。

## 备份与回滚

本次实际备份：
/var/backups/cql-bot/pre-bot-b-b6d096b5a216db02-20261025T124814Z，root0700。
其中bot-b.sqlite3、config.toml、service.unit、old-release.txt、manifest.json保存配套旧状态。
备份及上线数据库integrity_check=ok。原凭据文件未被脚本修改。
旧可运行版本/opt/cql-bot/releases/bot-b-29aa4ec0a329ea01保留。

需要且获授权回滚时：

```bash
sudo bash /opt/cql-bot/releases/bot-b-b6d096b5a216db02/deploy/rollback-bot.sh --approved-rollback /var/backups/cql-bot/pre-bot-b-b6d096b5a216db02-20261025T124814Z
```

脚本先停新服务并保存现场，再恢复匹配的旧DB/配置/unit/current。不要把旧程序配新数据库，
不要使用隔离测试副本覆盖生产。更早2026-10-24发布的备份也保留在原目录。

## 验收证据

- 离线487项测试通过，Ruff通过；隔离真实--once成功，受限重启后12点连续有效。
- 上线连续三轮健康时间推进，USDT/USDC/USDe有效，USDC实际从Bitstamp转用Kraken。
- 基差有效，资金费有效或正常等待新发布；9项风险NORMAL，前后active一致。
- BTC/ETH原15分钟窗口各62样本保留；SQLite/offset755061885保留。
- getUpdates真实轮询正常，command_polling=true；仅一个 Bot 运行器PID21078。
- sent36未增加，无重复、额外测试消息或普通启动播报。
- 权限、自动恢复、资源限制和开机自启复核通过。

服务器证据：本次备份目录post-cutover-verification.json。
本地证据：data/reports/bot-production-b6d096b5a216db02.json。
本轮没有修改源码或重复生产重启；候选重启验收已完成。真实用户命令可用/status等查询，
没有假冒用户发送命令。历史2026-10-24验收见DEVLOG及bot-usde-fix.md。

日常管理见bot-operations.md；增量原因/测试见bot-usdc-fix.md。
外部源仍可能共同失效，后续继续观察长期缺失率和内存；整机失联需外部监测补充。
