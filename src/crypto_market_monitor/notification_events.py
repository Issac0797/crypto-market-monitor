"""Transport-independent immutable notification contract (UTC ms, Decimal strings)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum, StrEnum
from types import MappingProxyType


class EventType(StrEnum):
    ARBITRAGE = "ARBITRAGE"
    MARKET = "MARKET"
    RISK = "RISK"
    SYSTEM = "SYSTEM"
    REPORT = "REPORT"


class Priority(IntEnum):
    CRITICAL = 0
    WARNING = 1
    NORMAL = 2


def _normalize(value):
    if isinstance(value, Mapping):
        if any(
            any(word in str(key).lower() for word in ("token", "secret", "password", "api_key"))
            for key in value
        ):
            raise ValueError("Credentials are forbidden in notification details")
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite event value")
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ValueError("Event values must use JSON primitives and Decimal; floats are forbidden")


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class NotificationEvent:
    event_id: str
    event_type: EventType
    source: str
    strategy: str
    timestamp_ms: int
    symbol: str
    status: str
    details: Mapping[str, object]
    priority: Priority = Priority.NORMAL
    mode: str = "PAPER"

    def __post_init__(self) -> None:
        if not isinstance(self.details, Mapping):
            raise ValueError("Event details must be a mapping")
        if not all(
            isinstance(v, str) and v.strip()
            for v in (self.event_id, self.source, self.strategy, self.symbol, self.status)
        ):
            raise ValueError("Event identity fields are required")
        if type(self.timestamp_ms) is not int or self.timestamp_ms < 0:
            raise ValueError("UTC epoch milliseconds required")
        if self.mode not in {"PAPER", "MONITOR"}:
            raise ValueError("Live execution notifications are not enabled in this phase")
        object.__setattr__(self, "event_type", EventType(self.event_type))
        object.__setattr__(self, "priority", Priority(self.priority))
        object.__setattr__(self, "details", _freeze(_normalize(self.details)))
        if len(self.to_json().encode()) > 32000:
            raise ValueError("Event exceeds 32 KB limit")

    def to_json(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "event_type": self.event_type.value,
                "source": self.source,
                "strategy": self.strategy,
                "timestamp_ms": self.timestamp_ms,
                "symbol": self.symbol,
                "status": self.status,
                "details": _normalize(self.details),
                "priority": int(self.priority),
                "mode": self.mode,
            },
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, body: str) -> "NotificationEvent":
        return cls(**json.loads(body))


def format_message(event: NotificationEvent) -> str:
    """Plain text: no Telegram HTML/Markdown interpretation of external values."""
    headings = {
        EventType.ARBITRAGE: "套利机会扫描" if event.mode == "MONITOR" else "套利模拟交易",
        EventType.MARKET: "行情监控",
        EventType.RISK: "风险告警",
        EventType.SYSTEM: "系统通知",
        EventType.REPORT: "收益汇总",
    }
    lines = [
        f"[{event.mode}] [{event.priority.name}] {headings[event.event_type]}",
        f"{event.status} | {event.symbol}",
        f"策略: {event.strategy} | 来源: {event.source}",
        f"UTC epoch ms: {event.timestamp_ms} | ID: {event.event_id}",
    ]
    if event.details.get("bot_channel") == "B":
        lines.insert(0, "[Bot]")
    lines.extend(f"{key}: {value}" for key, value in event.details.items() if key != "bot_channel")
    text = "\n".join(lines)
    encoded = text.encode("utf-16-le")
    if len(encoded) > 7800:
        text = (
            encoded[:7600].decode("utf-16-le", errors="ignore")
            + "\n[详情截断；完整事件见本地发件箱]"
        )
    return text
