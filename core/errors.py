"""游戏内可预期的错误类型。"""

from __future__ import annotations


class GameError(Exception):
    """可以直接展示给玩家的业务错误。

    这类错误属于"用户操作不当"而非程序缺陷，`main.py` 会把它的文案
    原样回复给玩家，因此消息应当是友好、可读的中文。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


__all__ = ["GameError"]
