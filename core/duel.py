"""决斗系统：两只猫娘以金币为赌注，用"猜数字"一决胜负。

规则
----
1. 挑战方向对方猫娘发起决斗，并自定义赌注；发起时**立即托管**挑战方的赌注，
   避免他中途把钱花掉。
2. 对方主人接受后托管自己的赌注，系统随机生成一个目标数字（默认 1-100）。
3. 双方各提交**一次**猜测，谁的猜测离目标更近谁获胜，赢走整个奖池
   （默认 2 × 赌注；可配置抽成）。
4. 距离相同视为平局，双方本金原样退回。

公平性设计（关键）
------------------
猜测**必须保密**，否则后猜的一方只要看一眼对手的数字就能稳赢。因此：

- 决斗开始时目标数字只存在存档里，绝不外泄；
- `/猜` 的回复**不回显玩家猜的数字**，只说"已收到"；
- 只有**双方都猜完**时才一次性揭晓目标、双方猜测与胜负；
- 建议玩家在**私聊**里发送 `/猜`，避免群里被人看到（插件会在提示里说明）。
"""

from __future__ import annotations

from typing import Any

from .errors import GameError
from .util import as_int, new_id, now_ts

STATUS_PENDING = "pending"  # 等待对方接受
STATUS_GUESSING = "guessing"  # 双方已下注，等待猜测
STATUS_FINISHED = "finished"  # 已分出胜负（或平局）
STATUS_DECLINED = "declined"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

_ACTIVE_STATUSES = (STATUS_PENDING, STATUS_GUESSING)

STATUS_LABELS: dict[str, str] = {
    STATUS_PENDING: "等待接受",
    STATUS_GUESSING: "等待猜测",
    STATUS_FINISHED: "已结束",
    STATUS_DECLINED: "已被拒绝",
    STATUS_CANCELLED: "已取消",
    STATUS_EXPIRED: "已超时",
}

#: 只保留最近这么多条已结束/失效的决斗记录，避免存档无限增长
_MAX_HISTORY = 200


class DuelMixin:
    """决斗相关逻辑。依赖宿主提供的 `store` / `cfg` / `rng` / `add_coins`。"""

    # ==================================================================
    # 查询
    # ==================================================================
    def duel_by_id(self, duel_id: str) -> dict[str, Any] | None:
        """按编号查找决斗，容忍用户省略 `D` 前缀。"""
        key = str(duel_id or "").strip().upper()
        if not key:
            return None
        if not key.startswith("D"):
            key = f"D{key}"
        for duel in self.store.duels:
            if str(duel.get("id", "")).upper() == key:
                return duel
        return None

    def active_duels(self) -> list[dict[str, Any]]:
        """所有进行中的决斗（待接受 + 待猜测）。"""
        return [d for d in self.store.duels if d.get("status") in _ACTIVE_STATUSES]

    def duel_of_catgirl(self, catgirl_id: str) -> dict[str, Any] | None:
        """某只猫娘当前参与的进行中决斗。"""
        for duel in self.active_duels():
            if catgirl_id in (duel.get("challenger_cat"), duel.get("opponent_cat")):
                return duel
        return None

    def duels_of_player(self, player_key: str) -> list[dict[str, Any]]:
        """该玩家参与的全部进行中决斗。"""
        return [
            duel
            for duel in self.active_duels()
            if player_key in (duel.get("challenger"), duel.get("opponent"))
        ]

    def duels_awaiting_guess(self, player_key: str) -> list[dict[str, Any]]:
        """等待该玩家提交猜测的决斗。"""
        result = []
        for duel in self.store.duels:
            if duel.get("status") != STATUS_GUESSING:
                continue
            key = self._guess_key(duel, player_key)
            if key and duel.get(key) is None:
                result.append(duel)
        return result

    def duels_to_accept(self, player_key: str) -> list[dict[str, Any]]:
        """等待该玩家接受/拒绝的决斗邀请。"""
        return [
            duel
            for duel in self.store.duels
            if duel.get("status") == STATUS_PENDING
            and duel.get("opponent") == player_key
        ]

    @staticmethod
    def _guess_key(duel: dict[str, Any], player_key: str) -> str | None:
        """返回该玩家在这局里存放猜测的字段名。"""
        if duel.get("challenger") == player_key:
            return "challenger_guess"
        if duel.get("opponent") == player_key:
            return "opponent_guess"
        return None

    # ==================================================================
    # 配置
    # ==================================================================
    def _duel_limits(self) -> tuple[int, int]:
        low = max(1, self.cfg.i("duel.min_bet", 10))
        high = max(low, self.cfg.i("duel.max_bet", 10000))
        return low, high

    def _number_range(self) -> tuple[int, int]:
        low = self.cfg.i("duel.number_min", 1)
        high = self.cfg.i("duel.number_max", 100)
        if high < low:
            low, high = high, low
        if low == high:
            high = low + 1
        return low, high

    def duel_range_text(self) -> str:
        """目标数字范围的可读文案。"""
        low, high = self._number_range()
        return f"{low} ~ {high}"

    def _assert_enabled(self) -> None:
        if not self.cfg.b("duel.enabled", True):
            raise GameError("决斗系统当前已被管理员关闭。")

    # ==================================================================
    # 发起 / 回应
    # ==================================================================
    def invite_duel(
        self,
        player: dict[str, Any],
        my_catgirl: dict[str, Any],
        target_query: str,
        bet: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """发起决斗，返回 `(duel, 对方猫娘)`。发起时立即托管挑战方赌注。"""
        self._assert_enabled()
        self.expire_duels()

        bet = as_int(bet, 0)
        low, high = self._duel_limits()
        if bet < low:
            raise GameError(f"赌注至少要 {low} 金币。")
        if bet > high:
            raise GameError(f"赌注最多 {high} 金币。")

        if not my_catgirl.get("alive", True):
            raise GameError(f"{my_catgirl.get('name')} 已经离开了，无法出战……")

        target = self.find_catgirl_globally(target_query)
        if target is None:
            raise GameError(
                f"找不到叫 `{target_query}` 的猫娘。可以用「/猫娘榜」看看大家的猫娘。"
            )
        if target["id"] == my_catgirl["id"]:
            raise GameError("不能和自己决斗哦，换一只猫娘吧。")
        if not target.get("alive", True):
            raise GameError(f"{target.get('name')} 已经离开了，没办法应战。")

        target_owner = str(target.get("owner") or "")
        if target_owner == player["key"]:
            raise GameError("不能让自己的两只猫娘互相决斗（左口袋进右口袋没意义）。")

        if self.duel_of_catgirl(str(my_catgirl["id"])) is not None:
            raise GameError(f"{my_catgirl.get('name')} 已经有一场决斗在进行了。")
        if self.duel_of_catgirl(str(target["id"])) is not None:
            raise GameError(f"{target.get('name')} 已经有一场决斗在进行了，等一等吧。")

        # 同一位玩家对同一只猫娘的重复邀请
        for duel in self.active_duels():
            if duel.get("challenger") == player["key"] and duel.get(
                "opponent_cat"
            ) == target["id"]:
                raise GameError(
                    f"你已经向 {target.get('name')} 下过战书了（编号 {duel.get('id')}），"
                    f"等对方回应吧。"
                )

        # 托管挑战方赌注
        self.spend_coins(player, bet, "决斗赌注（托管）")

        duel = {
            "id": new_id("D", self.store.next_counter("duel")),
            "challenger": player["key"],
            "challenger_name": player.get("name") or player["key"],
            "challenger_cat": my_catgirl["id"],
            "challenger_cat_name": my_catgirl.get("name"),
            "challenger_guess": None,
            "opponent": target_owner,
            "opponent_cat": target["id"],
            "opponent_cat_name": target.get("name"),
            "opponent_guess": None,
            "bet": bet,
            "pot": bet * 2,
            "target": None,
            "status": STATUS_PENDING,
            "winner": None,
            "created": now_ts(),
            "started_at": None,
            "resolved_at": None,
        }
        self.store.duels.append(duel)
        self._trim_duels()
        return duel, target

    def _find_incoming(
        self, player: dict[str, Any], duel_id: str, *, status: str
    ) -> dict[str, Any]:
        """查找一条"发给我、且处于指定状态"的决斗。"""
        duel = self.duel_by_id(duel_id)
        if duel is None:
            raise GameError(f"找不到编号为 `{duel_id}` 的决斗。")
        if duel.get("opponent") != player["key"]:
            raise GameError(f"决斗 `{duel.get('id')}` 不是发给你的。")
        if duel.get("status") != status:
            label = STATUS_LABELS.get(str(duel.get("status")), duel.get("status"))
            raise GameError(f"决斗 `{duel.get('id')}` 当前状态是「{label}」，无法这样操作。")
        return duel

    def decline_duel(self, player: dict[str, Any], duel_id: str) -> dict[str, Any]:
        """拒绝决斗，退回挑战方赌注。"""
        duel = self._find_incoming(player, duel_id, status=STATUS_PENDING)
        self._refund(duel, "challenger")
        duel["status"] = STATUS_DECLINED
        duel["resolved_at"] = now_ts()
        return duel

    def cancel_duel(self, player: dict[str, Any], duel_id: str) -> dict[str, Any]:
        """挑战方撤回尚未被接受的战书，退回自己的赌注。"""
        duel = self.duel_by_id(duel_id)
        if duel is None:
            raise GameError(f"找不到编号为 `{duel_id}` 的决斗。")
        if duel.get("challenger") != player["key"]:
            raise GameError("只能撤回自己发起的决斗。")
        if duel.get("status") != STATUS_PENDING:
            label = STATUS_LABELS.get(str(duel.get("status")), duel.get("status"))
            raise GameError(f"决斗已经在「{label}」阶段，无法撤回了。")
        self._refund(duel, "challenger")
        duel["status"] = STATUS_CANCELLED
        duel["resolved_at"] = now_ts()
        return duel

    def accept_duel(self, player: dict[str, Any], duel_id: str) -> dict[str, Any]:
        """接受决斗：托管应战方赌注，生成目标数字，进入猜测阶段。"""
        self._assert_enabled()
        duel = self._find_incoming(player, duel_id, status=STATUS_PENDING)

        my_cat = self.catgirl_by_id(str(duel.get("opponent_cat")))
        their_cat = self.catgirl_by_id(str(duel.get("challenger_cat")))
        if my_cat is None or their_cat is None:
            self._refund(duel, "challenger")
            duel["status"] = STATUS_CANCELLED
            duel["resolved_at"] = now_ts()
            raise GameError("决斗涉及的猫娘已经不存在了，已退回赌注。")
        if not my_cat.get("alive", True) or not their_cat.get("alive", True):
            self._refund(duel, "challenger")
            duel["status"] = STATUS_CANCELLED
            duel["resolved_at"] = now_ts()
            raise GameError("其中一方已经离开了，决斗取消，赌注已退回。")

        bet = as_int(duel.get("bet"), 0)
        self.spend_coins(player, bet, "决斗赌注（托管）")

        low, high = self._number_range()
        duel["target"] = self.rng.randint(low, high)
        duel["status"] = STATUS_GUESSING
        duel["started_at"] = now_ts()
        return duel

    # ==================================================================
    # 猜测与结算
    # ==================================================================
    def submit_guess(
        self, player: dict[str, Any], value: int, duel_id: str = ""
    ) -> dict[str, Any]:
        """提交猜测；双方都猜完后自动结算。返回更新后的决斗记录。"""
        value = as_int(value, 0)
        low, high = self._number_range()
        if value < low or value > high:
            raise GameError(f"请猜 {low} ~ {high} 之间的整数。")

        duel = self._pick_guess_duel(player, duel_id)
        key = self._guess_key(duel, player["key"])
        if key is None:  # pragma: no cover - _pick_guess_duel 已保证
            raise GameError("你没有参与这场决斗。")
        if duel.get(key) is not None:
            raise GameError(f"你已经猜过了（本局编号 {duel.get('id')}）。")

        duel[key] = value
        duel[f"{key}_at"] = now_ts()

        # 双方都猜完 -> 结算
        if duel.get("challenger_guess") is not None and duel.get(
            "opponent_guess"
        ) is not None:
            self._resolve_duel(duel)
        return duel

    def _pick_guess_duel(
        self, player: dict[str, Any], duel_id: str
    ) -> dict[str, Any]:
        """定位玩家要提交猜测的那场决斗。"""
        waiting = self.duels_awaiting_guess(player["key"])
        if not waiting:
            raise GameError(
                "你现在没有需要猜测的决斗。用「/决斗列表」看看进行中的决斗吧。"
            )
        if duel_id:
            target = self.duel_by_id(duel_id)
            if target is None or target not in waiting:
                raise GameError(f"决斗 `{duel_id}` 不需要你猜测。")
            return target
        if len(waiting) == 1:
            return waiting[0]
        ids = "、".join(str(d.get("id")) for d in waiting)
        raise GameError(
            f"你同时有好几场决斗在等你猜（{ids}），"
            f"请用「/猜 <编号> <数字>」指明是哪一场。"
        )

    def _resolve_duel(self, duel: dict[str, Any]) -> dict[str, Any]:
        """比较双方猜测，发放奖池。"""
        target = as_int(duel.get("target"), 0)
        c_guess = as_int(duel.get("challenger_guess"), 0)
        o_guess = as_int(duel.get("opponent_guess"), 0)
        c_dist = abs(c_guess - target)
        o_dist = abs(o_guess - target)

        pot = as_int(duel.get("pot"), as_int(duel.get("bet"), 0) * 2)
        rake_percent = max(0.0, min(100.0, self.cfg.f("duel.rake_percent", 0.0)))
        rake = int(pot * rake_percent / 100.0)
        prize = pot - rake

        if c_dist == o_dist:
            # 平局：各自退回本金
            winner = None
            self._refund(duel, "challenger")
            self._refund(duel, "opponent")
        elif c_dist < o_dist:
            winner = duel.get("challenger")
            self._payout(duel, "challenger", prize)
        else:
            winner = duel.get("opponent")
            self._payout(duel, "opponent", prize)

        duel["status"] = STATUS_FINISHED
        duel["winner"] = winner
        duel["resolved_at"] = now_ts()
        duel["rake"] = rake
        duel["prize"] = prize
        duel["challenger_dist"] = c_dist
        duel["opponent_dist"] = o_dist

        # 统计战绩
        if winner:
            self._bump_duel_stats(str(duel.get("challenger_cat")), winner == duel.get("challenger"))
            self._bump_duel_stats(str(duel.get("opponent_cat")), winner == duel.get("opponent"))
        else:
            self._bump_duel_stats(str(duel.get("challenger_cat")), None)
            self._bump_duel_stats(str(duel.get("opponent_cat")), None)
        self._trim_duels()
        return duel

    def _bump_duel_stats(self, catgirl_id: str, won: bool | None) -> None:
        """累加猫娘的决斗战绩。`won=None` 表示平局。"""
        catgirl = self.catgirl_by_id(catgirl_id)
        if catgirl is None:
            return
        stats = catgirl.setdefault("stats", {})
        stats["duels"] = as_int(stats.get("duels"), 0) + 1
        if won:
            stats["duels_won"] = as_int(stats.get("duels_won"), 0) + 1

    def _payout(self, duel: dict[str, Any], side: str, amount: int) -> None:
        """把奖池发给获胜方主人。"""
        player = self.store.players.get(str(duel.get(side) or ""))
        if player is not None:
            self.add_coins(player, amount)

    def _refund(self, duel: dict[str, Any], side: str) -> None:
        """把本金退还给某一方主人。"""
        player = self.store.players.get(str(duel.get(side) or ""))
        if player is not None:
            self.add_coins(player, as_int(duel.get("bet"), 0))

    # ==================================================================
    # 超时清理
    # ==================================================================
    def expire_duels(self) -> list[dict[str, Any]]:
        """清理超时的决斗并退回赌注，返回需要通知玩家的事件列表。"""
        invite_minutes = max(1, self.cfg.i("duel.invite_expire_minutes", 60))
        guess_hours = max(1, self.cfg.i("duel.guess_timeout_hours", 24))
        now = now_ts()
        events: list[dict[str, Any]] = []

        for duel in self.store.duels:
            status = duel.get("status")
            if status == STATUS_PENDING:
                deadline = float(duel.get("created") or 0) + invite_minutes * 60
                if now < deadline:
                    continue
                self._refund(duel, "challenger")
                duel["status"] = STATUS_EXPIRED
                duel["resolved_at"] = now
                events.append(
                    {
                        "type": "duel_expired",
                        "owner": duel.get("challenger"),
                        "duel_id": duel.get("id"),
                        "message": (
                            f"⌛ 你向「{duel.get('opponent_cat_name')}」发起的决斗"
                            f"（{duel.get('id')}）超过 {invite_minutes} 分钟没人回应，"
                            f"已自动取消，{duel.get('bet')} 金币赌注已退回。"
                        ),
                    }
                )
            elif status == STATUS_GUESSING:
                # 猜测阶段超时：只要有一方没猜，就整局作废、双方退款
                deadline = float(duel.get("started_at") or 0) + guess_hours * 3600
                if now < deadline:
                    continue
                self._refund(duel, "challenger")
                self._refund(duel, "opponent")
                duel["status"] = STATUS_EXPIRED
                duel["resolved_at"] = now
                for side in ("challenger", "opponent"):
                    events.append(
                        {
                            "type": "duel_expired",
                            "owner": duel.get(side),
                            "duel_id": duel.get("id"),
                            "message": (
                                f"⌛ 决斗（{duel.get('id')}）超过 {guess_hours} 小时没有双方都猜完，"
                                f"已作废，你的 {duel.get('bet')} 金币赌注已退回。"
                            ),
                        }
                    )
        if events:
            self._trim_duels()
        return events

    def _trim_duels(self) -> None:
        """只保留最近的若干条已结束记录。"""
        active = [d for d in self.store.duels if d.get("status") in _ACTIVE_STATUSES]
        finished = [d for d in self.store.duels if d.get("status") not in _ACTIVE_STATUSES]
        if len(finished) <= _MAX_HISTORY:
            return
        finished.sort(key=lambda d: float(d.get("resolved_at") or d.get("created") or 0))
        keep = finished[len(finished) - _MAX_HISTORY :]
        self.store.state["duels"] = active + keep

    # ==================================================================
    # 展示
    # ==================================================================
    def duel_snapshot(self, duel: dict[str, Any], viewer: str = "") -> dict[str, Any]:
        """把决斗整理成便于渲染的字典。

        `viewer` 传入查看者的玩家键：**只有双方都猜完后**才会带上目标数字
        与双方猜测，避免通过列表泄露答案。
        """
        status = str(duel.get("status"))
        revealed = status == STATUS_FINISHED
        snapshot: dict[str, Any] = {
            "id": duel.get("id"),
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "challenger": duel.get("challenger"),
            "opponent": duel.get("opponent"),
            "challenger_name": duel.get("challenger_name"),
            "challenger_cat_name": duel.get("challenger_cat_name"),
            "opponent_cat_name": duel.get("opponent_cat_name"),
            "bet": as_int(duel.get("bet"), 0),
            "pot": as_int(duel.get("pot"), as_int(duel.get("bet"), 0) * 2),
            "is_challenger": duel.get("challenger") == viewer,
            "is_opponent": duel.get("opponent") == viewer,
            "my_guess_done": (
                duel.get("challenger_guess") is not None
                if duel.get("challenger") == viewer
                else duel.get("opponent_guess") is not None
                if duel.get("opponent") == viewer
                else False
            ),
        }
        if revealed:
            snapshot.update(
                {
                    "target": duel.get("target"),
                    "challenger_guess": duel.get("challenger_guess"),
                    "opponent_guess": duel.get("opponent_guess"),
                    "challenger_dist": duel.get("challenger_dist"),
                    "opponent_dist": duel.get("opponent_dist"),
                    "winner": duel.get("winner"),
                    "prize": as_int(duel.get("prize"), 0),
                    "rake": as_int(duel.get("rake"), 0),
                    "draw": duel.get("winner") is None,
                }
            )
        else:
            # 未揭晓时只暴露"是否已猜"，不暴露数值
            snapshot["challenger_guessed"] = duel.get("challenger_guess") is not None
            snapshot["opponent_guessed"] = duel.get("opponent_guess") is not None
        return snapshot


__all__ = [
    "DuelMixin",
    "STATUS_PENDING",
    "STATUS_GUESSING",
    "STATUS_FINISHED",
    "STATUS_DECLINED",
    "STATUS_CANCELLED",
    "STATUS_EXPIRED",
    "STATUS_LABELS",
]
