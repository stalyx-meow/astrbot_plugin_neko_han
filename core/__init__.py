"""Neko_Han 的核心游戏逻辑。

本包刻意**不依赖 AstrBot**，只使用 Python 标准库，因此可以脱离 AstrBot
运行时进行单元测试（见 `tests/`）。AstrBot 相关的胶水代码全部放在
插件根目录的 `main.py` 中。
"""

from .errors import GameError
from .world import NekoWorld

__all__ = ["GameError", "NekoWorld"]
