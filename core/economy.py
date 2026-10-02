"""经济系统：金币与仓库（背包）。

设计说明：**金币与道具都记在玩家账号上**，猫娘本身只承载属性、任务与
婚姻关系。这样一位玩家认养多只猫娘时不必在多个钱包之间转账，喂食/
挂单时也只需指定"由哪只猫娘出面"，交互更顺畅。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .errors import GameError
from .util import as_int


class EconomyMixin:
    """金币与仓库操作。依赖宿主提供的 `store` / `cfg` / `registry`。"""

    # ------------------------------------------------------------------
    # 金币
    # ------------------------------------------------------------------
    def add_coins(self, player: dict[str, Any], amount: int) -> int:
        """增加金币（`amount` 可为负，但余额不会低于 0）。返回新余额。"""
        coins = max(0, as_int(player.get("coins"), 0) + int(amount))
        player["coins"] = coins
        return coins

    def spend_coins(self, player: dict[str, Any], amount: int, what: str = "") -> int:
        """扣除金币；余额不足时抛出 `GameError`。返回新余额。"""
        amount = int(amount)
        if amount < 0:
            raise GameError("金额不能为负数")
        coins = as_int(player.get("coins"), 0)
        if coins < amount:
            suffix = f"（{what}）" if what else ""
            raise GameError(
                f"金币不足{suffix}：需要 {amount} 金币，你只有 {coins} 金币。"
            )
        player["coins"] = coins - amount
        return player["coins"]

    def claim_daily(self, player: dict[str, Any], *, today: date | None = None) -> int:
        """领取每日签到金币，返回获得的数量。"""
        today = today or self.today()
        day = today.isoformat()
        if str(player.get("last_allowance_day") or "") == day:
            raise GameError("今天已经签到过啦，明天再来吧～")
        amount = max(0, self.cfg.i("economy.daily_allowance", 80))
        player["last_allowance_day"] = day
        self.add_coins(player, amount)
        return amount

    # ------------------------------------------------------------------
    # 仓库
    # ------------------------------------------------------------------
    def _inventory(self, player: dict[str, Any]) -> dict[str, int]:
        """返回玩家的仓库字典（必要时初始化）。"""
        inv = player.get("inventory")
        if not isinstance(inv, dict):
            inv = {}
            player["inventory"] = inv
        return inv

    def _inv_count(self, player: dict[str, Any], item_id: str) -> int:
        """查询玩家持有某道具的数量。"""
        return max(0, as_int(self._inventory(player).get(str(item_id)), 0))

    def _inv_add(self, player: dict[str, Any], item_id: str, qty: int = 1) -> int:
        """向仓库加入道具，返回加入后的数量。"""
        if qty <= 0:
            return self._inv_count(player, item_id)
        inv = self._inventory(player)
        key = str(item_id)
        inv[key] = max(0, as_int(inv.get(key), 0)) + int(qty)
        return inv[key]

    def _inv_remove(self, player: dict[str, Any], item_id: str, qty: int = 1) -> int:
        """从仓库移除道具；数量不足时抛出 `GameError`。"""
        key = str(item_id)
        have = self._inv_count(player, key)
        if have < qty:
            item = self.registry.by_id(key)
            name = item["name"] if item else key
            raise GameError(f"仓库里的 `{name}` 不足：需要 {qty} 个，只有 {have} 个。")
        inv = self._inventory(player)
        remaining = have - int(qty)
        if remaining > 0:
            inv[key] = remaining
        else:
            inv.pop(key, None)
        return remaining

    def inventory_view(self, player: dict[str, Any]) -> list[dict[str, Any]]:
        """返回可展示的仓库列表：`[{item, qty}, ...]`。"""
        inv = self._inventory(player)
        rows: list[dict[str, Any]] = []
        for item_id, qty in inv.items():
            count = as_int(qty, 0)
            if count <= 0:
                continue
            item = self.registry.by_id(item_id)
            if item is None or not item.get("enabled", True):
                continue
            rows.append({"item": item, "qty": count})
        rows.sort(key=lambda row: (row["item"]["category"], row["item"]["name"]))
        return rows

    def inventory_total(self, player: dict[str, Any]) -> int:
        """仓库中的道具总件数。"""
        return sum(max(0, as_int(v, 0)) for v in self._inventory(player).values())


__all__ = ["EconomyMixin"]
