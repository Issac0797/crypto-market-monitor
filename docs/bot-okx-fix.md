# Bot OKX 数据采集与通知修复（2026-10-27）

状态：代码及离线测试通过，待隔离VPS验收和正式切换确认。
生产仍为b6d096b5a216db02。Bot A、凭据、阈值、新鲜度、基差配对时差不改变。

## 审计依据

现有VPS的Bot当前正常运行，OKX盘口/资金费有效、Telegram polling正常。
历史记录发现BTC/ETH现货和基差间歇缺数，以及同一波抖动反复产生DATA_GAP_REWARMING；
另有两个永续资金费同步故障。旧通知原因笼统，无法确认每次是超时、断网还是过期。
运行日志有12条资金费订阅警告，部分轮次valid=0/4至3/4；不能宣称每次故障都是程序原因。

代码中确认的问题：

1. 原Scanner按交易所加锁，四项OKX盘口串行请求，单项最多10秒，后项会排队；
   统一完成后才检查时效，多个慢调用可让前面成功的报价也过期，放大局部失败。
2. 每一次价格源间隙超过45秒都无条件发预热通知；同一窗口尚未完成预热又发生间隙时反复发送。
3. 资金费等待下一次发布的分支直接返回，不推进数据恢复确认；正常发布间隙也可能重置恢复计时。
4. 资金费WS失败立即清空缓存，没有启用同一合约/同一预计费率的已实现公共REST读取备用。

## 改动

- 新增BotOkxBooks，仅由Bot入口使用，最多四项并发，仍共享CCXT限流器。
  每项5秒超时、最多两次，中间0.25秒；全部完成后再次检查30秒时效。
  不修改通用Scanner、默认扫描器或Bot A。身份不符/陈旧数据即使重试也不能进入风险判断。
- 增加SQLite bot_b_price_warmup：同一标的未完成15分钟预热时只发一次缺口预热通知。
  后续缺口仍清空窗口并重置确认；完成整个最大窗口后才重新允许下一次预热通知。
  原DATA_UNAVAILABLE/RECOVERED仍报告真实断线及恢复；不以去重替代故障检测。
- 等待新资金费发布可推进数据源恢复确认，但不会重复调用风险状态机或改写源时间/费率。
  超过原180秒发布间隙仍失效，实际风险触发仍要求30秒内的新报价。
- Bot显式启用同一OKX公共REST资金费备用；WS失败后最多一次5秒REST读取。
  使用原adapter解析info.ts、fundingTime、nextFundingTime；只接受真实身份/周期/30秒内数据。
  不换成其他交易所资金费，不把已结算费率冒充预计值，不调用私有API。
- 故障通知增加采集失败/配对校验原因，资金费来源正确标记okx-perpetual。

文件：新增market_data/bot_okx_books.py、tests/test_bot_okx_reliability.py及本文；
修改risk_watch_cli.py、market_data/funding_stream.py、risk_session.py、risk_watch.py和打包文档清单。
公开config/bot-phase2-3.toml保持9cee084e…；原配置指纹、规则阈值均不变。
新表由现有初始化器添加，已有样本/事件/发送状态保留，不删除生产数据。

## 验证

- 专项67 passed in14.73s。
- 全回归518 passed in65.07s，无跳过。
- Ruff check通过；测试命令：
  .venv/Scripts/python.exe -m pytest -q --basetemp .test-tmp/okx-full --tb=short。
- 覆盖慢ETH不阻塞BTC、每项最多两次、坏数据拒绝/重试、取消全部在途任务、
  REST正常/过期/身份错误、等待发布推进数据恢复但不推进风险、预热去重跨重启及重新就绪后再触发。
- VPS直接读取原OKX REST：BTC/ETH预计资金费源年龄2345/2524ms，实际结算周期均8小时。
  此为当次公共数据证据，不是持续更新保证；没有通过人为改变真实行情制造异常。

## 隔离部署预检

冻结候选bot-b-d990eef9731292f7，包SHA256：
c215a8824c1b89159262ac1d1a3baa2c1117574c8c4074030d9efeefe2c8f11c。
独立版本位于/opt/cql-bot/releases/bot-b-d990eef9731292f7，
隔离状态/var/lib/cql-bot-staging/okx-d990eef9731292f7。
安装器stage验证源指纹、创建独立venv；pip check无依赖冲突。
使用SQLite只读连接backup得到一致副本；未覆盖生产库。
副本--once完成：四项OKX盘口有效、基差/资金费/USDT/USDC/USDe均有效，sent仍71。

第一次连续12轮预检脚本误将正常waiting_for_fresh_publication视作失败（5轮）；
其余7轮全部fresh，采集failures全空，进程正常退出0、SQLite integrity=ok，
峰值VmRSS133924KiB。此轮脚本总体判定false，不能标记验收通过。
修正的是隔离检查脚本的健康判据：接受既有180秒内正常等待、但不接受unavailable；
报价与风险计算的30秒时效门保持不变。保留原报告，重新采集完整健康信息验证。
连续预检不加载Telegram环境、不polling、不发送消息；预热导致的2条pending只留在副本。

SSH采用Windows OpenSSH与本机已解锁代理，forwardagent=no；不读取/展示私钥内容，
不上传私钥，不输出Token。服务器Telegram环境文件root:root0600，数据库cqlbot0600。
后续：完成连续预检后提交正式切换确认；旧生产服务继续运行。


第二次完整预检通过：12个连续健康文件（15秒周期，首尾165秒）均为盘口4/4，
基差/USDT/USDC/USDe有效；资金费fresh或合法等待新发布，无采集failures。
VmRSS峰值134032KiB（130.89MiB），相同CPU20%/MemoryHigh144MiB/Max192MiB/Tasks24限制，
进程SIGTERM后退出0、SQLite integrity=ok、预热表存在，跨重启仍保留sent71。
副本2条预热消息未发送，观察期间由pending变为expired；无Telegram环境/无polling。短时通过不等于长期网络不会再抖动。
原始结果data/reports/bot-okx-d990-live.json；首轮失败判据报告亦保留。

预检结束确认：仅PID21078一个risk_watch_cli；生产MainPID未变、NRestarts0、active，
current仍为b6d096b5a216db02，命令polling仍开启。未改生产配置/凭据/数据库或Bot A。
待用户确认后才可用既有upgrade-bot.sh执行备份/原子切换及失败回滚；
回滚目标为/opt/cql-bot/releases/bot-b-b6d096b5a216db02。

## 正式上线（用户已确认）

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
