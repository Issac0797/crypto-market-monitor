"""Explicit reliability settings for Bot only; legacy/A defaults are unchanged."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BotPolicy:
    max_attempts: int = 5
    merge_ms: int = 3000
    max_batch: int = 3
    cooldown_ms: int = 2700000
    history_limit: int = 5000
    commands: bool = True
    usdc_fallback: bool = False
    data_recovery_ms: int = 0


def load_policy(raw: dict | None) -> BotPolicy | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - set(BotPolicy.__dataclass_fields__):
        raise ValueError("Unknown Bot reliability option")
    p = BotPolicy(**raw)
    for value, low, high in (
        (p.max_attempts, 1, 10),
        (p.merge_ms, 0, 10000),
        (p.max_batch, 1, 5),
        (p.cooldown_ms, 1800000, 3600000),
        (p.history_limit, 100, 10000),
        (p.data_recovery_ms, 0, 180000),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Bot reliability bounds invalid")
    if type(p.commands) is not bool or type(p.usdc_fallback) is not bool:
        raise ValueError("Bot command opt-in must be boolean")
    return p
