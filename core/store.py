"""JSON 持久化层。

设计要点：
- **原子写入**：先写同目录下的临时文件再 `os.replace`，避免进程被杀导致存档损坏。
- **单锁串行**：所有写操作在同一个 `asyncio.Lock` 下进行，避免并发命令互相覆盖。
- **容错读取**：存档缺失或损坏时回退到默认结构并保留坏档备份，插件不会因此启动失败。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("astrbot")

#: 存档结构版本，便于未来做数据迁移。
STATE_VERSION = 1


def default_state() -> dict[str, Any]:
    """返回一份全新的空存档。"""
    return {
        "version": STATE_VERSION,
        "players": {},  # player_key -> player dict
        "catgirls": {},  # catgirl_id -> catgirl dict
        "marriages": [],  # list[marriage dict]
        "proposals": [],  # list[proposal dict]
        "duels": [],  # list[duel dict]
        "listings": [],  # list[market listing dict]
        "official_shop": {"date": "", "entries": []},
        "counters": {
            "catgirl": 0,
            "listing": 0,
            "proposal": 0,
            "marriage": 0,
            "duel": 0,
        },
        "meta": {"last_tick_day": ""},
    }


def _atomic_write_json(path: Path, payload: Any) -> None:
    """把 `payload` 以 JSON 形式原子地写入 `path`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


class Store:
    """负责 `state.json` 与 `items.json` 的读写。"""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "state.json"
        self.items_path = self.data_dir / "items.json"

        self.state: dict[str, Any] = default_state()
        self.items_doc: dict[str, Any] = {"items": []}

        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 载入
    # ------------------------------------------------------------------
    def load(self) -> None:
        """从磁盘载入存档（同步，供插件初始化时调用）。"""
        self.state = self._read_json(self.state_path, default_state())
        self.items_doc = self._read_json(self.items_path, {"items": []})
        self._repair_state()

    def _read_json(self, path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return fallback
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("顶层结构不是对象")
            return data
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # 坏档不阻塞启动：备份后使用默认值。
            logger.error("Neko_Han: 读取 %s 失败(%s)，将备份并使用默认数据", path, exc)
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".corrupt"))
            except OSError:
                pass
            return fallback

    def _repair_state(self) -> None:
        """补齐缺失字段，保证后续代码可以放心地直接索引。"""
        base = default_state()
        for key, value in base.items():
            if key not in self.state or not isinstance(self.state[key], type(value)):
                # 允许 dict/list 的宽松类型检查失败时回退
                if isinstance(value, dict) and isinstance(self.state.get(key), dict):
                    continue
                self.state[key] = value
        for key, value in base["counters"].items():
            self.state["counters"].setdefault(key, value)
        for key, value in base["meta"].items():
            self.state["meta"].setdefault(key, value)
        self.state["version"] = STATE_VERSION
        if not isinstance(self.items_doc.get("items"), list):
            self.items_doc["items"] = []

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def save_state_now(self) -> None:
        """立即写盘（同步）。调用方需自行保证不并发。"""
        _atomic_write_json(self.state_path, self.state)

    def save_items_now(self) -> None:
        """立即写盘道具表（同步）。"""
        _atomic_write_json(self.items_path, self.items_doc)

    async def save(self) -> None:
        """在锁内把状态与道具表写盘。"""
        async with self.lock:
            self.save_state_now()
            self.save_items_now()

    # ------------------------------------------------------------------
    # 便捷访问
    # ------------------------------------------------------------------
    @property
    def players(self) -> dict[str, Any]:
        return self.state["players"]

    @property
    def catgirls(self) -> dict[str, Any]:
        return self.state["catgirls"]

    @property
    def marriages(self) -> list[dict[str, Any]]:
        return self.state["marriages"]

    @property
    def proposals(self) -> list[dict[str, Any]]:
        return self.state["proposals"]

    @property
    def duels(self) -> list[dict[str, Any]]:
        return self.state["duels"]

    @property
    def listings(self) -> list[dict[str, Any]]:
        return self.state["listings"]

    def next_counter(self, name: str) -> int:
        """自增并返回指定计数器的值。"""
        counters = self.state["counters"]
        counters[name] = int(counters.get(name, 0)) + 1
        return counters[name]


__all__ = ["Store", "default_state", "STATE_VERSION"]
