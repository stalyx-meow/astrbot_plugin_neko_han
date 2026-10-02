"""通用工具函数：时间、数值、随机数与格式化。"""

from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# 时间
# ---------------------------------------------------------------------------


def now_ts() -> float:
    """当前 Unix 时间戳（秒）。"""
    return time.time()


def game_day(when: datetime | float | None = None, reset_hour: int = 4) -> date:
    """把现实时间映射为"游戏日"。

    AstrBot 的养成类玩法通常在凌晨刷新，而不是 00:00。若 `reset_hour=4`，
    那么 2025-07-16 03:30 仍属于游戏日 2025-07-15，04:00 之后才进入
    2025-07-16。这样"每日"的边界落在玩家活动最少的时段。
    """
    if when is None:
        moment = datetime.now()
    elif isinstance(when, (int, float)):
        moment = datetime.fromtimestamp(when)
    else:
        moment = when
    if moment.hour < reset_hour:
        moment = moment - timedelta(days=1)
    return moment.date()


def parse_day(text: str) -> date | None:
    """解析 `YYYY-MM-DD` 字符串，失败返回 None。"""
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def days_between(earlier: date, later: date) -> int:
    """两个日期之间相差的整数天，负数归零。"""
    return max(0, (later - earlier).days)


def format_duration(seconds: float) -> str:
    """把秒数格式化成 `1小时23分` / `45秒` 这样的可读文案。"""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{sec}秒" if sec else f"{minutes}分钟"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    days, hours = divmod(hours, 24)
    return f"{days}天{hours}小时" if hours else f"{days}天"


# ---------------------------------------------------------------------------
# 数值
# ---------------------------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    """把数值限制在 [low, high] 区间内。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def as_int(value: object, default: int = 0) -> int:
    """尽力把任意值转换为 int，失败时返回默认值。"""
    try:
        if isinstance(value, bool):
            return int(value)
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def as_float(value: object, default: float = 0.0) -> float:
    """尽力把任意值转换为 float，失败时返回默认值。"""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def as_bool(value: object, default: bool = False) -> bool:
    """宽容地把配置/JSON 中的值解析为布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "yes", "1", "on", "是"}:
            return True
        if low in {"false", "no", "0", "off", "否"}:
            return False
    return default


# ---------------------------------------------------------------------------
# 随机
# ---------------------------------------------------------------------------


def jitter(base: float, variance: float, rng: random.Random | None = None) -> float:
    """在 `base` 基础上做 ±variance 比例的随机浮动。

    variance=0.2 表示结果落在 [0.8*base, 1.2*base]。
    """
    if variance <= 0:
        return base
    source = rng or random
    factor = 1.0 + source.uniform(-variance, variance)
    return base * factor


def weighted_pick(
    pairs: list[tuple[object, float]], rng: random.Random | None = None
) -> object:
    """按权重从 `[(value, weight), ...]` 中挑一个值。"""
    source = rng or random
    total = sum(weight for _, weight in pairs if weight > 0)
    if total <= 0:
        return source.choice([value for value, _ in pairs])
    roll = source.uniform(0, total)
    upto = 0.0
    for value, weight in pairs:
        if weight <= 0:
            continue
        upto += weight
        if roll <= upto:
            return value
    return pairs[-1][0]


def new_id(prefix: str, counter: int) -> str:
    """生成形如 `c12`、`L3` 的短 ID（便于玩家手动输入）。"""
    return f"{prefix}{counter}"


__all__ = [
    "now_ts",
    "game_day",
    "parse_day",
    "days_between",
    "format_duration",
    "clamp",
    "as_int",
    "as_float",
    "as_bool",
    "jitter",
    "weighted_pick",
    "new_id",
]
