"""任务系统：每日随机任务、消耗精力与时间、领取金币奖励。

每只猫娘每天会得到一批随机任务（数量由配置决定）。任务分为
`轻松 / 普通 / 困难` 三档，档位越高消耗的精力与时间越多，奖励也越丰厚。

任务流程：`生成 -> 开始（扣精力，进入计时）-> 到点领取（发金币）`。

跨天处理：游戏日切换时，**正在进行中的任务会被保留**，未开始的旧任务
则被丢弃，并补足当天的新任务，避免玩家因为跨天而损失已投入的精力。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .defs import TASK_POOL, TIER_WEIGHTS
from .errors import GameError
from .util import as_int, game_day, jitter, now_ts, weighted_pick

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_PENDING: "未开始",
    STATUS_ACTIVE: "进行中",
    STATUS_DONE: "已完成",
    STATUS_FAILED: "已中断",
}


class TaskMixin:
    """任务相关逻辑。依赖宿主提供的 `store` / `cfg` / `rng`。"""

    # ------------------------------------------------------------------
    # 每日任务生成
    # ------------------------------------------------------------------
    def ensure_daily_tasks(
        self,
        catgirl: dict[str, Any],
        *,
        today: date | None = None,
    ) -> dict[str, Any]:
        """确保猫娘拥有当天（`today`）的任务列表，返回任务状态字典。"""
        today = today or self.today()
        day = today.isoformat()
        state = catgirl.get("tasks")
        if not isinstance(state, dict):
            state = {"date": "", "slots": []}
            catgirl["tasks"] = state
        slots = state.get("slots")
        if not isinstance(slots, list):
            slots = []
            state["slots"] = slots

        if str(state.get("date") or "") == day:
            return state

        # 保留进行中的任务，丢弃未开始/已结束的旧任务
        kept = [slot for slot in slots if slot.get("status") == STATUS_ACTIVE]
        used_numbers = {as_int(slot.get("slot"), 0) for slot in kept}

        count = max(1, self.cfg.i("tasks.daily_task_count", 3))
        new_slots: list[dict[str, Any]] = []
        for template in self._pick_task_templates(count):
            number = 1
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            new_slots.append(self._make_slot(template, number))

        state["slots"] = sorted(kept + new_slots, key=lambda s: as_int(s.get("slot"), 0))
        state["date"] = day
        return state

    def _pick_task_templates(self, count: int) -> list[dict[str, Any]]:
        """按档位权重不重复地抽取 `count` 个任务模板。"""
        pool = list(TASK_POOL)
        tiers = [(tier, weight) for tier, weight in TIER_WEIGHTS.items()]
        chosen: list[dict[str, Any]] = []
        guard = 0
        while len(chosen) < count and pool and guard < count * 20 + 50:
            guard += 1
            tier = weighted_pick(tiers, self.rng)
            candidates = [t for t in pool if t.get("tier") == tier]
            if not candidates:
                continue
            template = self.rng.choice(candidates)
            pool.remove(template)
            chosen.append(template)
        # 池子还够但权重抽样失败时兜底
        while len(chosen) < count and pool:
            chosen.append(pool.pop())
        return chosen

    def _make_slot(self, template: dict[str, Any], number: int) -> dict[str, Any]:
        """依据模板生成一个任务槽位（奖励在此刻随机确定）。"""
        multiplier = self.cfg.f("economy.task_reward_multiplier", 1.0)
        variance = self.cfg.f("economy.task_reward_variance", 0.2)
        base = as_int(template.get("reward"), 10) * max(0.0, multiplier)
        reward = max(1, int(round(jitter(base, variance, self.rng))))
        return {
            "slot": number,
            "key": template.get("key"),
            "name": template.get("name"),
            "emoji": template.get("emoji", "📋"),
            "desc": template.get("desc", ""),
            "tier": template.get("tier", "normal"),
            "energy": as_int(template.get("energy"), 10),
            "minutes": max(1, as_int(template.get("minutes"), 5)),
            "reward": reward,
            "status": STATUS_PENDING,
            "started_at": 0,
            "finish_at": 0,
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def task_slots(self, catgirl: dict[str, Any]) -> list[dict[str, Any]]:
        """返回当天的任务槽位列表。"""
        return list(self.ensure_daily_tasks(catgirl).get("slots") or [])

    def find_slot(
        self, catgirl: dict[str, Any], number: int
    ) -> dict[str, Any] | None:
        """按槽位编号查找任务。"""
        for slot in self.task_slots(catgirl):
            if as_int(slot.get("slot"), 0) == int(number):
                return slot
        return None

    def active_slots(self, catgirl: dict[str, Any]) -> list[dict[str, Any]]:
        """返回所有进行中的任务。"""
        return [s for s in self.task_slots(catgirl) if s.get("status") == STATUS_ACTIVE]

    # ------------------------------------------------------------------
    # 开始 / 领取
    # ------------------------------------------------------------------
    def start_task(
        self, catgirl: dict[str, Any], number: int
    ) -> dict[str, Any]:
        """开始一个任务：扣除精力并进入计时。"""
        if not catgirl.get("alive", True):
            raise GameError(f"{catgirl.get('name')} 已经离开了，没办法再接下任务。")
        self.settle_catgirl(catgirl)
        slot = self.find_slot(catgirl, number)
        if slot is None:
            raise GameError(f"找不到编号为 {number} 的任务。")
        if slot.get("status") == STATUS_ACTIVE:
            raise GameError(f"任务「{slot['name']}」已经在进行中了。")
        if slot.get("status") == STATUS_DONE:
            raise GameError(f"任务「{slot['name']}」已经完成了。")
        if slot.get("status") == STATUS_FAILED:
            raise GameError(f"任务「{slot['name']}」已经中断，请等明天的任务刷新。")

        if not self.cfg.b("tasks.allow_parallel_tasks", False):
            running = self.active_slots(catgirl)
            if running:
                raise GameError(
                    f"{catgirl.get('name')} 正在做「{running[0]['name']}」，"
                    f"先等她做完吧。"
                )

        cost = as_int(slot.get("energy"), 0)
        energy = as_int(catgirl.get("energy"), 0)
        if energy < cost:
            raise GameError(
                f"{catgirl.get('name')} 的精力不够了：需要 {cost} 点，"
                f"现在只有 {energy} 点。睡一觉就会恢复哦。"
            )

        catgirl["energy"] = energy - cost
        now = now_ts()
        slot["status"] = STATUS_ACTIVE
        slot["started_at"] = now
        slot["finish_at"] = now + max(1, as_int(slot.get("minutes"), 5)) * 60
        return slot

    def claim_task(
        self,
        player: dict[str, Any],
        catgirl: dict[str, Any],
        number: int | None = None,
    ) -> list[dict[str, Any]]:
        """领取已到时间的任务奖励；`number` 为 None 时领取全部可领任务。"""
        if not catgirl.get("alive", True):
            raise GameError(f"{catgirl.get('name')} 已经离开了，无法领取奖励。")
        self.settle_catgirl(catgirl)

        slots = self.task_slots(catgirl)
        if number is None:
            targets = [
                s
                for s in slots
                if s.get("status") == STATUS_ACTIVE
                and now_ts() >= as_int(s.get("finish_at"), 0)
            ]
            if not targets:
                raise GameError("现在没有可以领取的任务奖励。")
        else:
            slot = self.find_slot(catgirl, number)
            if slot is None:
                raise GameError(f"找不到编号为 {number} 的任务。")
            if slot.get("status") != STATUS_ACTIVE:
                raise GameError(f"任务「{slot['name']}」现在不能领取奖励。")
            remaining = as_int(slot.get("finish_at"), 0) - now_ts()
            if remaining > 0:
                raise GameError(
                    f"任务「{slot['name']}」还没做完，还要等 {int(remaining) + 1} 秒左右。"
                )
            targets = [slot]

        claimed: list[dict[str, Any]] = []
        for slot in targets:
            slot["status"] = STATUS_DONE
            reward = as_int(slot.get("reward"), 0)
            self.add_coins(player, reward)
            stats = catgirl.setdefault("stats", {})
            stats["tasks_done"] = as_int(stats.get("tasks_done"), 0) + 1
            stats["coins_earned"] = as_int(stats.get("coins_earned"), 0) + reward
            claimed.append(slot)
        return claimed


__all__ = [
    "TaskMixin",
    "STATUS_PENDING",
    "STATUS_ACTIVE",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_LABELS",
]
