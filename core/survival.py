"""生存系统：饱食度 / 水分 / 健康值 / 精力值的每日结算，以及道具使用与死亡。

结算采用"惰性 + 定时"双轨：

- 每次读取某只猫娘时调用 :meth:`SurvivalMixin.settle_catgirl`，按游戏日差
  补算衰减。这样即使机器人停机数天，重新上线后状态依然正确。
- 后台定时任务对所有猫娘调用同一方法，负责当天到点后的统一结算。

两种路径都以猫娘自身的 `last_tick` 为基准，因此重复调用是幂等的。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .defs import EFFECT_KEYS
from .errors import GameError
from .util import clamp, days_between, game_day, now_ts, parse_day

#: 死亡原因文案
DEATH_STARVATION = "饥饿与缺水"
DEATH_SICKNESS = "身体过于虚弱"


class SurvivalMixin:
    """生存相关逻辑。依赖宿主提供的 `store` / `cfg` / `registry`。"""

    # ------------------------------------------------------------------
    # 每日结算
    # ------------------------------------------------------------------
    def settle_catgirl(
        self,
        catgirl: dict[str, Any],
        *,
        today: date | None = None,
    ) -> dict[str, Any] | None:
        """把猫娘的状态结算到 `today`。

        返回死亡事件字典（若本次结算导致死亡），否则返回 None。
        """
        today = today or game_day(reset_hour=self.cfg.day_reset_hour)
        last = parse_day(str(catgirl.get("last_tick") or "")) or today
        catgirl["last_tick"] = today.isoformat()

        days = days_between(last, today)
        if days <= 0:
            return None
        if not catgirl.get("alive", True):
            return None

        cap = max(1, self.cfg.i("survival.offline_decay_cap_days", 30))
        days = min(days, cap)

        limits = self.cfg.limits()
        satiety_decay = max(0, self.cfg.i("survival.satiety_decay_per_day", 35))
        hydration_decay = max(0, self.cfg.i("survival.hydration_decay_per_day", 45))
        starve_damage = max(0, self.cfg.i("survival.starvation_damage_per_day", 25))
        regen = max(0, self.cfg.i("survival.health_regen_per_day", 10))
        regen_threshold = max(0, self.cfg.i("survival.health_regen_threshold", 50))
        energy_regen = max(0, self.cfg.i("survival.energy_regen_per_day", 100))
        death_enabled = self.cfg.b("survival.death_enabled", True)
        grace_days = max(0, self.cfg.i("survival.grace_days", 2))

        birth = parse_day(str(catgirl.get("birth_day") or "")) or today
        age_days = days_between(birth, today)

        for _ in range(days):
            catgirl["satiety"] = max(
                0, int(catgirl.get("satiety", limits["satiety"])) - satiety_decay
            )
            catgirl["hydration"] = max(
                0, int(catgirl.get("hydration", limits["hydration"])) - hydration_decay
            )

            starving = catgirl["satiety"] <= 0 or catgirl["hydration"] <= 0
            health = int(catgirl.get("health", limits["health"]))
            if starving:
                health -= starve_damage
            elif (
                catgirl["satiety"] >= regen_threshold
                and catgirl["hydration"] >= regen_threshold
            ):
                health += regen
            catgirl["health"] = int(clamp(health, 0, limits["health"]))

            catgirl["energy"] = int(
                clamp(
                    int(catgirl.get("energy", limits["energy"])) + energy_regen,
                    0,
                    limits["energy"],
                )
            )

            if catgirl["health"] <= 0:
                # 保护期：认养后的前 grace_days 天内不会死亡
                if death_enabled and age_days >= grace_days:
                    reason = DEATH_STARVATION if starving else DEATH_SICKNESS
                    self._kill_catgirl(catgirl, reason)
                    return {
                        "type": "death",
                        "catgirl_id": catgirl["id"],
                        "catgirl_name": catgirl.get("name", "猫娘"),
                        "owner": catgirl.get("owner", ""),
                        "reason": reason,
                    }
                # 保护期内或关闭了死亡机制：保底 1 点健康值
                catgirl["health"] = 1
        return None

    def settle_all(self, *, today: date | None = None) -> list[dict[str, Any]]:
        """结算所有猫娘，返回本次产生的死亡事件列表。"""
        today = today or game_day(reset_hour=self.cfg.day_reset_hour)
        events: list[dict[str, Any]] = []
        for catgirl in list(self.store.catgirls.values()):
            event = self.settle_catgirl(catgirl, today=today)
            if event:
                events.append(event)
        return events

    def _kill_catgirl(self, catgirl: dict[str, Any], reason: str) -> None:
        """标记猫娘死亡并清理其未完成的任务与挂牌。"""
        catgirl["alive"] = False
        catgirl["health"] = 0
        catgirl["died_at"] = now_ts()
        catgirl["death_reason"] = reason
        catgirl["energy"] = 0
        tasks = catgirl.get("tasks")
        if isinstance(tasks, dict) and isinstance(tasks.get("slots"), list):
            for slot in tasks["slots"]:
                if slot.get("status") == "active":
                    slot["status"] = "failed"
        # 猫娘去世后，她在市场上的挂牌一并下架，避免卖出无法履约的商品
        self._withdraw_catgirl_listings(catgirl["id"])

    # ------------------------------------------------------------------
    # 道具使用
    # ------------------------------------------------------------------
    def use_item(
        self,
        player: dict[str, Any],
        catgirl: dict[str, Any],
        item_query: str,
    ) -> dict[str, Any]:
        """对猫娘使用一件道具，返回效果摘要。

        抛出 `GameError` 表示无法使用（没有道具、猫娘已离世且道具不能复活等）。
        """
        self.settle_catgirl(catgirl)
        item = self.registry.resolve(item_query)
        if item is None:
            raise GameError(f"找不到道具 `{item_query}`，可以用「neko 商城」看看有哪些。")
        if not item.get("enabled", True):
            raise GameError(f"`{item['name']}` 已被禁用，暂时无法使用。")

        item_id = str(item["id"])
        if self._inv_count(player, item_id) <= 0:
            raise GameError(
                f"你的仓库里没有 `{item['name']}`，可以用「neko 买 {item['name']}」购买。"
            )

        effect = item.get("effect") or {}
        revive = bool(effect.get("revive"))

        if not catgirl.get("alive", True):
            if not revive:
                raise GameError(
                    f"{catgirl.get('name')} 已经离开你了……"
                    f"只有「复活草」这类道具才能让她回来。"
                )
        elif revive:
            raise GameError(f"{catgirl.get('name')} 现在活蹦乱跳的，不需要复活草。")

        limits = self.cfg.limits()
        applied: dict[str, int] = {}
        for key in EFFECT_KEYS:
            amount = int(effect.get(key, 0) or 0)
            if not amount:
                continue
            before = int(catgirl.get(key, 0) or 0)
            after = int(clamp(before + amount, 0, limits[key]))
            catgirl[key] = after
            if after != before:
                applied[key] = after - before

        revived = False
        if revive and not catgirl.get("alive", True):
            catgirl["alive"] = True
            catgirl["health"] = max(1, limits["health"] // 2)
            catgirl["died_at"] = None
            catgirl["death_reason"] = ""
            applied["health"] = catgirl["health"]
            revived = True

        self._inv_remove(player, item_id, 1)
        stats = catgirl.setdefault("stats", {})
        stats["items_used"] = int(stats.get("items_used", 0)) + 1

        return {"item": item, "applied": applied, "revived": revived}

    def feed_catgirl(
        self,
        player: dict[str, Any],
        catgirl: dict[str, Any],
        item_query: str,
        *,
        drink: bool = False,
    ) -> dict[str, Any]:
        """喂食/喂水。

        `drink=True` 时只接受饮品（`drink` 分类），否则接受食物与饮品。
        分类校验发生在**消耗道具之前**，避免"先扣物品再报错"的不一致状态。
        """
        item = self.registry.resolve(item_query)
        if item is None:
            raise GameError(f"找不到道具 `{item_query}`，可以用「neko 商城」看看有哪些。")

        category = item.get("category")
        name = item.get("name")
        if drink:
            if category != "drink":
                raise GameError(
                    f"`{name}` 不是饮品。如果想喂食物，请用"
                    f"「neko 喂食 {catgirl.get('name')} {name}」。"
                )
        elif category not in ("food", "drink"):
            raise GameError(
                f"`{name}` 不是食物或饮品，如果想用它请发送"
                f"「neko 使用 {catgirl.get('name')} {name}」。"
            )
        return self.use_item(player, catgirl, str(item["id"]))

    # ------------------------------------------------------------------
    # 状态描述
    # ------------------------------------------------------------------
    def status_flags(self, catgirl: dict[str, Any]) -> list[str]:
        """返回猫娘当前状态的提示标签（用于群内提醒）。"""
        flags: list[str] = []
        if not catgirl.get("alive", True):
            return ["已离世"]
        limits = self.cfg.limits()
        satiety = int(catgirl.get("satiety", 0))
        hydration = int(catgirl.get("hydration", 0))
        health = int(catgirl.get("health", 0))
        if satiety <= 0:
            flags.append("快饿死了")
        elif satiety < limits["satiety"] * 0.25:
            flags.append("很饿")
        if hydration <= 0:
            flags.append("严重缺水")
        elif hydration < limits["hydration"] * 0.25:
            flags.append("口渴")
        if health < limits["health"] * 0.3:
            flags.append("虚弱")
        if int(catgirl.get("energy", 0)) <= 0:
            flags.append("精疲力尽")
        return flags


__all__ = ["SurvivalMixin", "DEATH_STARVATION", "DEATH_SICKNESS"]
