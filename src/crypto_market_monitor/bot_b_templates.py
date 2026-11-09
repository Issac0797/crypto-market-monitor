"""Compact Bot B HTML; every external value is escaped before interpolation."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from html import escape

from crypto_market_monitor.notification_events import NotificationEvent

ZONE = timezone(timedelta(hours=8))


def _text(value: object) -> str:
    return escape(str(value)[:180], quote=True) if value is not None else "历史未记录"


def _number(value: object, suffix: str = "", *, places: int = 4) -> str:
    if value is None:
        return "历史未记录"
    return _text(f"{Decimal(str(value)):.{places}f}".rstrip("0").rstrip(".") + suffix)


def _time(value: object) -> str:
    if value is None:
        return "历史未记录"
    dt = datetime.fromtimestamp(int(value) / 1000, UTC).astimezone(ZONE)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC+8")


def _duration(value: object) -> str:
    if value is None:
        return "历史未记录"
    seconds = max(0, int(value) // 1000)
    return f"{seconds // 3600}小时{seconds % 3600 // 60}分{seconds % 60}秒"


def format_bot_b(event: NotificationEvent) -> str:
    if event.details.get("bot_channel") != "B":
        raise ValueError("Bot B formatter refuses another channel")
    d, status = event.details, event.status
    if status == "COMMAND_REPLY":
        return escape(str(d.get("reply", ""))[:1800], quote=True)
    symbol, source = _text(event.symbol), _text(event.source)
    if status in {"DATA_UNAVAILABLE", "DATA_GAP_REWARMING"}:
        return "\n".join(
            [
                f"⚠️ <b>数据异常｜{source}</b>",
                "",
                f"📡 受影响标的：{symbol}",
                f"❌ 故障原因：{_text(d.get('error_type', '数据缺口，窗口重新预热'))}",
                f"⏱ 中断时长：{_duration(d.get('outage_duration_ms', d.get('gap_ms')))}",
                f"🕒 最后有效数据：{_time(d.get('last_valid_ms'))}",
                "",
                "状态：相关监控暂不可用",
            ]
        )
    if status == "DATA_RECOVERED":
        return "\n".join(
            [
                f"✅ <b>数据恢复｜{source}</b>",
                "",
                f"⏱ 故障持续：{_duration(d.get('outage_duration_ms'))}",
                f"🕒 恢复时间：{_time(event.timestamp_ms)}",
                "",
                "状态：监控恢复正常",
            ]
        )
    kind = (
        "预计资金费率"
        if status.startswith("FUNDING_")
        else "现货–永续基差"
        if status.startswith("BASIS_")
        else "稳定币脱锚风险"
        if status.startswith(("STABLECOIN_", "USDE_"))
        else "急涨急跌"
    )
    value = _number(d.get("value_percent", d.get("change_percent")), "%")
    if status.endswith("RECOVERED"):
        peak_label = "已记录最大异常（历史不全）" if d.get("peak_incomplete") else "期间最大异常"
        return "\n".join(
            [
                f"🟢 <b>风险解除｜{kind}｜{symbol}</b>",
                "",
                f"📊 当前指标：{value}",
                f"📉 {peak_label}：{_number(d.get('peak_value_percent'), '%')}",
                f"⏱ 异常持续：{_duration(d.get('episode_duration_ms'))}",
                f"🕒 恢复时间：{_time(event.timestamp_ms)}",
                "",
                "状态：已恢复",
            ]
        )
    if not status.endswith(("ENTERED", "ESCALATED")):
        raise ValueError("Non-risk Bot B broadcast is disabled")
    lines = [
        f"🔴 <b>风险预警｜{kind}｜{symbol}</b>",
        "",
        f"📊 当前指标：{value}",
        f"📍 触发阈值：±{_number(d.get('threshold_percent'), '%')}",
        f"🏦 数据来源：{source}",
        f"⏱ 确认时间：{_duration(d.get('confirmation_ms'))}",
        f"🕒 触发时间：{_time(event.timestamp_ms)}",
    ]
    if status.endswith("ESCALATED"):
        lines.append("🔺 严重程度升级 / 风险方向变化")
    if status.startswith("BASIS_"):
        lines += [
            f"现货中价：{_number(d.get('spot_mid_usdt'))} USDT",
            f"永续盘口中价：{_number(d.get('perp_mid_usdt'))} USDT",
        ]
    elif status.startswith("FUNDING_"):
        lines += [
            f"预计原始费率：{_number(d.get('raw_rate_percent'), '%')}",
            f"实际周期：{_number(d.get('actual_interval_hours'))}小时；指标为8小时等效",
            f"下次结算：{_time(d.get('next_settlement_ms'))}",
            _text(d.get("positive_rate", "")) + "；预计值，非已结算",
        ]
    elif status.startswith(("STABLECOIN_", "USDE_")):
        lines += [
            f"美元参考价：{_number(d.get('mid_usd'), places=6)} USD",
            f"买 / 卖：{_number(d.get('bid_usd'), places=6)} / "
            f"{_number(d.get('ask_usd'), places=6)} USD",
            "报价路径：" + _text(d.get("quote_route", event.source)),
            "单所/交叉报价观察，非全市场定论",
        ]
    else:
        lines += [
            f"窗口：{_text(d.get('window_minutes'))}分钟",
            f"当前 / 起始：{_number(d.get('mid_price_usdt'))} / "
            f"{_number(d.get('baseline_price_usdt'))} USDT",
        ]
    return "\n".join([*lines, "", "状态：异常持续中"])
