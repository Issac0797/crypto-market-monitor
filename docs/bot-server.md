# Bot服务器7×24小时行情监控

2026-10-23用户明确授权部署并选择仅BTC/ETH急涨急跌。服务器<VPS_HOST>，
Ubuntu 24.04 / Python 3.12.3，约961MiB内存；服务为cql-bot.service。
用户随后明确扩展现货/永续基差及极端资金费，要求24小时运行。当前发布为
bot-b-a7a686eef3c2a730，沿用独立Bot 凭据和192MiB内存硬上限。
默认monitor_cli仍为离线foundation；本入口不导入策略或Broker，不使用交易所凭据。

## 最新规则变更：稳定币与移除5分钟窗口

用户明确移除5分钟±3%规则；当前价格规则只有BTC/ETH现货15分钟±5%，确认30秒。
新增Bitstamp USDT/USD、USDC/USD法币美元盘口，CCXT统一公开接口；不是USDC/USDT相对比价。
仅缓存2项元数据和各20档最新盘口，同一进程独立异步任务约15秒采样；失败/重连不阻断BTC/ETH。
Bitstamp元数据接口无逐标的过滤，临时目录响应选出2项后丢弃，每6小时重新发现；不持有全量市场。

买卖一都低于0.995美元或都高于1.005美元，连续新源时间确认60秒后通知；
双倍阈值（1%）严重升级；买卖一都回到0.9975–1.0025美元并确认60秒后恢复。
价差跨过1美元且超恢复带不能证明正常或脱锚，拒绝判断；每侧返回20档至少1000美元深度。
沿用30秒新鲜度、45秒源缺口重确认、15分钟再进入冷却和5分钟事件有效期；无历史窗口预热，
但必须完成60秒独立样本确认。旧/重复源时间不推进，同源时间值变化拒绝。
健康快照stablecoin_feed展示两项fresh/unavailable；连续3次不可用及恢复单独通知。
所有消息注明Bitstamp单所美元偏离，尚无多源确认，不宣称全市场已脱锚。
官方字段契约：[Bitstamp订单簿API](https://www.bitstamp.net/api/#tag/Order-book/operation/GetOrderBook)，
CCXT 4.5.89将原始microtimestamp解析为毫秒，不以接收时间替代。

`migrate-bot-config.py --stablecoin-upgrade`仅允许本次明确变更：去掉300000ms窗口、
加入固定稳定币参数，其余配置必须逐字段相同；停服锁与旧指纹验证后事务更新审计，
删除5分钟状态并使尚未发送的5分钟告警过期，保留15分钟及基差/资金费历史。
以下首版5分钟规则段落为历史证据。

## 规则与数据

配置：config/bot.toml，服务器为/etc/cql-bot/config.toml。
OKX CCXT公开接口先按instId分别读取BTC-USDT、ETH-USDT元数据，只将这两项放入
CCXT缓存；后续复用既有CcxtPublicAdapter和Scanner，不缓存交易所全量市场。
每15秒采样两项现货盘口，中价=(买一+卖一)/2，变动=当前中价/窗口基准中价-1。
不是成交价、可实现收益、资金费预测或策略入场信号。

- 5分钟变动绝对值≥3%，15分钟≥5%，连续确认30秒后告警。
- 达对应阈值2倍，连续确认后严重升级；降到一半阈值以内并确认30秒后恢复。
- 相同窗口恢复后的再次进入有15分钟冷却；已发生事件的升级/反向变化不受该冷却阻挡。
- 基准取窗口边界之前最近的真实样本，最多容忍45秒边界偏差，不使用窗口之后样本。
- 源时间最多30秒；源时间缺失、未来、陈旧、错标的或非法盘口均拒绝。
- 相邻有效源时间间隔超过45秒则清空该标的旧窗口/状态、重新预热，不推断缺口中的行情。
- 断网连续3次采样失败产生数据不可用通知，恢复后单独通知；连续失败不重复刷屏。
- 启动首次无历史时，5/15分钟窗口分别预热；重启仅在历史仍连续时复用持久样本。

以上是冻结的初始工程参数，未历史校准。改变配置指纹时运行器拒绝沿用旧状态库，
需先停止服务、备份旧库并审阅状态迁移方案，不能边运行边静默调参。
不同窗口或标的可以分别通知；首版仅价格规则，当前新增基差/资金费见下文。

## 资源与状态

采用单Python进程和systemd，不运行Docker、Dashboard、独立发送进程或10Hz发件箱轮询。
采样、规则判断和每轮最多一次发送尝试在同一进程中完成；通知失败仍持久化重试。
每个标的最多256条紧凑样本（当前15秒频率通常64条），只保存约最长窗口加45秒，
不保存每次完整盘口。已终结通知最多1000条；未发送事件5分钟过期，防止补发旧行情。
状态与通知入队同一SQLite事务，崩溃不出现“更新状态却遗漏通知”的提交间隙。
Telegram已确认而本机未记sent即崩溃时仍可能重复，无法保证端到端恰好一次。

首版systemd设置MemoryHigh=144M、MemoryMax=192M、CPUQuota=20%、Nice=10、TasksMax=24，
WatchdogSec=90、Restart=on-failure。日志单文件512000字节、2份轮转；SQLite页数上限
8192（默认4KiB页约32MiB）。到达磁盘/数据库上限会失败退出，不伪报健康。
不修改系统其他服务或实盘程序；没有为此安装常驻额外守护进程。

本轮预检单次RSS峰值130148KiB（约127MiB）；systemd运行后20秒采样：
内存97.36MiB、当时峰值98.43MiB，平均占单核CPU约0.056%。服务器当时可用内存约574MiB。
这些是本次短时实测，不是长期上限证明；硬上限由systemd单独执行。

目录：

- /opt/cql-bot/releases/bot-b-9ec77bf8f735a767：保留的首版只读源码及独立venv。
- /opt/cql-bot/current：当前发布指针；依赖精确版本记录于发布目录installed-requirements.txt。
- /etc/cql-bot/telegram.env：只含Bot 凭据与目标白名单，root:root 0600；不放A凭据。
- /var/lib/cql-bot/data/bot-b.sqlite3：样本、规则状态、健康状态和Bot 发件箱。
- /var/lib/cql-bot/health.json、logs/bot.log、service.lock：健康快照、日志、内核单实例锁。

用户先明确批准凭据传输后，scripts/deploy-bot-credential.ps1通过SSH stdin传入本机
DPAPI解密后的Bot 配置，不输出Token、不生成本机明文文件。配置目录0755保证服务可读规则，
凭据文件0600只供systemd加载。不要用cat或systemctl show Environment打印凭据。

## 管理、升级与恢复

通过deploy账户登录，需要管理命令时sudo；本轮使用已配置的root密钥安装systemd。

```bash
sudo systemctl status cql-bot --no-pager
sudo systemctl show cql-bot -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec -p NRestarts
sudo cat /var/lib/cql-bot/health.json
sudo tail -n 20 /var/lib/cql-bot/logs/bot.log
sudo systemctl stop cql-bot
sudo systemctl start cql-bot
```

health.json的status=sampling仅表示最近两项采集正常；样本first_ms/last_ms展示实际窗口覆盖，
不表示尚未预热的规则已就绪。跨过旧健康时间30秒应视为异常，不能只看systemd active。
看门狗验证进程进展，行情失败则靠数据事件/健康快照；服务器完全断网/关机不能自行发送失联消息。

scripts/package-bot.py只打包源码和指定配置，排除data、日志、凭据、缓存和本机venv；
deploy/install-bot.sh在新发布目录校验每个文件摘要，创建独立venv并固定CCXT 4.5.89，
只安装，不自行启用服务。首次构建的其余精确依赖记录于installed-requirements.txt；
目前不是已验证的跨服务器离线wheelhouse发布流程。
切换版本前停止Bot，保留/备份状态库和旧发布，再更新current指针并启动。
回滚指向保留的旧目录，配置/状态必须兼容；不要覆盖正在运行的发布目录。
禁止同时运行旧foundation的B发送器、另一个Bot 运行器或另一台服务器上的同一规则实例。

本轮验证了主动重启后样本从6条延续到7条，通知sent记录保持，未发生自动重启。
首版没有自动备份，当前版本见下文；异地备份和长时间故障演练仍待后续。现有operations备份工具需要停止持有
service.lock的进程后再备份；不可直接复制活跃SQLite文件冒充一致性备份。
服务器重新迁移时先停止并禁用旧实例，再传状态/单独授权的凭据；不迁移或启用实盘Broker。

## 基差、资金费与7×24扩展（当前版本）

首版规则和资源数据在上文保留作历史证据；本节描述当前运行的新增能力。
紧凑缓存现在只有BTC/ETH的现货与USDT线性永续共4个标的，6小时刷新一次元数据。
盘口继续REST每15秒采集；资金费用同一CCXT Pro客户端的2个异步任务订阅，仅保留最新发布，
不会新增常驻进程。获取失败会清理失败订阅缓存并有界等待后重试，不静默回退REST。

基差 = 永续中价/现货中价−1。仅比较同一OKX、同一基础币、USDT报价与结算的现货和线性永续，
两腿各自新鲜度≤30秒且源时间差≤10秒。20档已返回深度中，现货/永续每一侧名义深度
至少1000USDT；永续数量按contract_size换算，不能把合约张数当基础币。
基差绝对值≥1%连续确认60秒预警，≥2%严重升级，≤0.5%连续确认后恢复。
这是未扣手续费/滑点的中价偏离观察，不是可实现套利利润。缺腿、错时或薄盘口会取消待确认，
不伪造“异常恢复”；价格规则仍可独立继续。

8小时等效资金费 = 原始预估费率 × 28800000 / 实际周期毫秒。
绝对值≥0.1%预警，≥0.2%严重，≤0.05%恢复，确认间隔60秒；独立新发布才能推进确认。
不同发布间隔超过180秒或实际周期变化则重置待确认过程，已触发异常不会因缺数被假定恢复。
正费率表示多头支付空头，负费率相反；消息包含原始费率、实际周期、下次结算和8小时等效值，
明确标为预估，不能当作已结算或保证收益。新增两类仍使用原15分钟冷却、确认及5分钟有效期。

资金费发布节奏不同于盘口：源年龄≤30秒才参与判断，30–180秒的正常发布间隙显示
waiting_for_fresh_publication，不重写源时间、不推进确认。超过上限、非法数据或订阅失败
则取消待确认并进入对应数据健康检查。health.json可能为sampling_partial，必须结合
basis_feed、funding_feed查看是正常等待还是unavailable；不会将预估资金费当作闭合bar信号。

systemd当前Restart=always、StartLimitIntervalSec=0，每15秒延迟重试，避免连续失败后永久封锁。
保留开机自启、90秒看门狗、日志轮转、单实例与资源限制；手动systemctl stop仍可正常停服。
“24小时运行”指无定时休眠的服务器常驻与自动恢复配置，不能保证交易所/网络或硬件永不故障。
当前未宣称已经完成连续24小时验收，也没有增加Codex自动任务或任何实盘功能。

自动在线备份位于/var/lib/cql-bot/backups/latest.sqlite3与previous.sqlite3，首次启动及此后
每日到期时用SQLite backup API生成，完整性通过后原子替换，只保留两份，不复制Token。
备份在同一服务器，不是异地容灾；恢复必须先停止服务并验证备份/配置兼容。
本次切换前另留/var/backups/cql-bot/pre-ecdd9ff45d03c997及旧发布。
scripts/migrate-bot-config.py只有在停止状态、旧配置指纹匹配且价格参数完全未变时，
才允许仅添加basis/funding/stablecoin规则并记录迁移审计；价格历史和原告警状态保留。
本次移除5分钟规则须显式--stablecoin-upgrade，严格迁移限制见最新规则变更。
stage安装不切换current或服务配置；新依赖沿用旧发布的精确版本约束。

配置仍为初始工程阈值，未做历史校准。稳定币新增范围见本文最新变更；
其他币种、跨所套利和Polymarket策略不在当前监控范围。

本次稳定币升级实测：全回归329 passed，Ruff通过；隔离--once所有盘口/资金费有效，
6.02秒、峰值RSS132908KiB。正式服务active/running，自启与Restart=always保留，
当前内存约100.54MiB、峰值约100.79MiB，NRestarts=0；两个稳定币feed均fresh。
切换前快照/var/backups/cql-bot/pre-a7a686eef3c2a730；SQLite完整性ok，
5分钟状态为0，原价格样本保留，启动通知sent。以上均为短时验证，不等于24小时验收。
