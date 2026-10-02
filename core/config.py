"""配置读取封装。

`_conf_schema.json` 定义的所有配置项都通过本类读取。它把嵌套的
AstrBotConfig（本质是 dict）包装成带默认值与类型转换的点号路径访问，
这样核心逻辑既不必依赖 AstrBot，也不会因为用户漏填配置而崩溃。
"""

from __future__ import annotations

from typing import Any

from .util import as_bool, as_float, as_int


class GameConfig:
    """点号路径式的只读配置访问器。"""

    def __init__(self, raw: Any = None) -> None:
        self.raw: dict[str, Any] = raw if isinstance(raw, dict) else {}

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    def get(self, path: str, default: Any = None) -> Any:
        """读取 `section.key` 形式的配置，缺失时返回 `default`。"""
        section, _, key = str(path).partition(".")
        value = self.section(section).get(key, default)
        return default if value is None else value

    def i(self, path: str, default: int = 0) -> int:
        """按 int 读取配置。"""
        return as_int(self.get(path, default), default)

    def f(self, path: str, default: float = 0.0) -> float:
        """按 float 读取配置。"""
        return as_float(self.get(path, default), default)

    def b(self, path: str, default: bool = False) -> bool:
        """按 bool 读取配置。"""
        return as_bool(self.get(path, default), default)

    def s(self, path: str, default: str = "") -> str:
        """按 str 读取配置。"""
        value = self.get(path, default)
        return str(value) if value is not None else default

    # -- 常用组合的便捷访问 -------------------------------------------
    @property
    def day_reset_hour(self) -> int:
        """每日刷新时刻，限制在 0-23。"""
        hour = self.i("shop.day_reset_hour", 4)
        return min(23, max(0, hour))

    def limits(self) -> dict[str, int]:
        """四项属性的上限。"""
        return {
            "satiety": max(1, self.i("survival.max_satiety", 100)),
            "hydration": max(1, self.i("survival.max_hydration", 100)),
            "health": max(1, self.i("survival.max_health", 100)),
            "energy": max(1, self.i("survival.max_energy", 100)),
        }


__all__ = ["GameConfig"]
