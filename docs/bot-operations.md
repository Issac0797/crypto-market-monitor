# 运行与恢复

本文面向已经完成本机验证和 Linux 部署的用户。Crypto Market Monitor 是一个只读
风险监控 Bot，不提供交易执行或远程配置控制。

## 日常检查

```bash
sudo systemctl status crypto-market-monitor --no-pager
sudo systemctl show crypto-market-monitor -p MainPID -p NRestarts -p MemoryCurrent -p MemoryMax
sudo journalctl -u crypto-market-monitor --since '1 hour ago' --no-pager
sudo cat /var/lib/crypto-market-monitor/health.json
```

`health.json.updated_ms` 不前进、日志持续报数据失败、或 systemd 不断重启时，应停止
排查。`systemctl active` 只表示进程存在，不代表每个公开行情源仍然新鲜。

## 已启用规则

| 规则 | 来源与触发 | 确认 |
| --- | --- | --- |
| BTC/ETH 急涨急跌 | 现货 5 分钟绝对涨跌 ≥1% 或 15 分钟 ≥5% | 30 秒 |
| BTC/ETH 现货–永续基差 | 盘口中价基差绝对值 ≥1% | 60 秒 |
| 极端预计资金费 | 按实际周期折算 8 小时绝对值 ≥0.1% | 60 秒 |
| USDT/USDC 美元偏离 | 买卖两侧报价均越过 ±0.5% | 60 秒 |
| USDe 美元偏离 | 有效美元中价绝对偏离 ≥0.5% | 60 秒 |
| 数据异常/恢复 | 无效采集写健康/日志，默认不推送运维诊断 | 原故障机制 |

价格恢复值为 0.5%/2.5%，基差恢复值 0.5%，资金费恢复值 0.05%，稳定币/USDe 恢复值
0.25%。5 分钟规则只报告异常波动，不发送普通行情播报。所有金额使用 `Decimal`，
源时间和接收时间分别保存，不改写交易所时间。

## 数据质量

- 拒绝过期、未来、格式错误、非有限、错标的和源时间倒序的数据。
- 跨腿必须满足源年龄、时差、盘口价差和美元深度门限。
- 缺口超过配置上限时重新预热，不补造中间价格。
- 数据缺失进入 `DATA_STALE`，不据此判定风险恢复。
- 资金费 `waiting_for_fresh_publication` 可表示正常等待下一次发布，不能拿旧费率
  当作新值触发。

## Telegram 与单轮询实例

- 默认只发送风险触发、升级、恢复和用户查询回复。
- `--data-notifications` 可额外发送数据故障/恢复诊断。
- 绑定 Chat ID 可使用 `/status`、`/risks`、`/history`、`/settings`，均为只读。
- 同一 Token 只允许一个 `getUpdates` 所有者；不要并行启动诊断轮询器。
- Telegram 确认成功与本地 `sent` 之间崩溃仍可能产生一次重复，不能承诺端到端
  恰好一次。
- 群聊绑定允许群成员发起查询；当前没有单独的用户级入站白名单。

## 状态、备份与恢复

SQLite 保存风险生命周期、样本、峰值、发送记录和健康相关状态。默认在线备份只复制
数据库，不复制 Token、环境文件或日志。常见目录：

```text
/var/lib/crypto-market-monitor/data/
/var/lib/crypto-market-monitor/backups/
/var/lib/crypto-market-monitor/logs/
/etc/crypto-market-monitor/config.toml
/etc/crypto-market-monitor/telegram.env
```

已有数据库的文件名和部分表名是持久化兼容标识，不应仅因命名清理而修改。

恢复流程：

1. 停止服务，确认没有进程持有 `service.lock` 或 `getUpdates`。
2. 保存当前故障数据库、日志、配置和 unit。
3. 校验备份 `PRAGMA integrity_check` 和配置指纹。
4. 在隔离目录恢复并启动一次，确认健康快照和数据源状态。
5. 经人工批准后切换到正式目录并启动。

不得直接用隔离副本覆盖生产状态，也不得绕过配置指纹校验。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| `status=sampling_partial` | 查看各数据源详情；资金费正常等待发布不一定代表故障 |
| `DATA_STALE` | 检查交易所公共接口、网络、源时间和盘口质量；不要手工改成正常 |
| Telegram 409/冲突 | 停止其他同 Token 的轮询实例，只保留一个所有者 |
| 健康快照不更新 | 检查主进程、日志、磁盘和 SQLite 完整性 |
| 配置指纹不匹配 | 停止服务，审阅配置和状态迁移；不要清空数据库绕过 |
| 资源超限 | 检查内存/CPU/任务限制和长期连接；不要静默放宽安全门 |

## 边界

整机断网、关机或宿主机故障时，机器人无法自行发送远端告警。同机备份不能抵御整机
丢失。交易所公共 API 也可能共同失效，`7×24` 常驻不等于外部数据永久可用。

部署、升级和回滚步骤见 [部署说明](bot-deployment.md)。
