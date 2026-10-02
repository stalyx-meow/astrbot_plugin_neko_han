"""`NekoWorld`：把存储、道具表与各子系统组合成一个可测试的游戏世界。

本模块**不导入 AstrBot**。`main.py` 只负责把聊天事件转换成对
`NekoWorld` 的方法调用，再把返回值渲染成消息。

约定：
- 玩家以 `"{platform}:{user_id}"` 作为唯一键（`player["key"]`）。
- 金币与仓库记在玩家账号上，猫娘承载属性、任务与婚姻关系。
"""

from __future__ import annotations

import random
import re
from datetime import date
from pathlib import Path
from typing import Any

from .config import GameConfig
from .defs import NAME_POOL
from .duel import DuelMixin
from .economy import EconomyMixin
from .errors import GameError
from .items import ItemRegistry
from .market import MarketMixin
from .marriage import MarriageMixin
from .store import Store
from .survival import SurvivalMixin
from .tasks import TaskMixin
from .util import as_int, game_day, new_id, now_ts

#: 猫娘名字允许的最大长度
MAX_NAME_LENGTH = 12

#: 名字中不允许出现的字符（控制字符、空白、以及用于指令分隔的符号）
_INVALID_NAME_RE = re.compile(r"[\s\u200b-\u200f\u2028\u2029\x00-\x1f]")


def validate_name(name: str) -> str:
    """校验并规整猫娘名字，非法时抛出 `GameError`。"""
    cleaned = str(name or "").strip().strip("\u200b")
    if not cleaned:
        raise GameError("名字不能为空哦，起个好听的名字吧～")
    if _INVALID_NAME_RE.search(cleaned):
        raise GameError("名字里不能包含空格或特殊符号。")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise GameError(f"名字太长了，最多 {MAX_NAME_LENGTH} 个字。")
    return cleaned


class NekoWorld(
    SurvivalMixin,
    TaskMixin,
    EconomyMixin,
    MarriageMixin,
    MarketMixin,
    DuelMixin,
):
    """猫娘养成的全部游戏逻辑。"""

    def __init__(
        self,
        data_dir: Path | str,
        config: Any = None,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = GameConfig(config)
        self.store = Store(data_dir)
        self.rng = rng if rng is not None else random.Random()
        self.registry = ItemRegistry(self.store)

    # ==================================================================
    # 生命周期
    # ==================================================================
    def load(self) -> "NekoWorld":
        """从磁盘载入存档与道具表，必要时写入默认道具。"""
        self.store.load()
        self.registry = ItemRegistry(self.store)
        self.registry.ensure_seeded()
        return self

    def save_now(self) -> None:
        """同步写盘（测试与关键节点使用）。"""
        self.store.save_state_now()
        self.store.save_items_now()

    async def save(self) -> None:
        """异步写盘（命令处理结束后调用）。"""
        await self.store.save()

    def today(self) -> date:
        """当前游戏日。"""
        return game_day(reset_hour=self.cfg.day_reset_hour)

    # ==================================================================
    # 玩家
    # ==================================================================
    @staticmethod
    def player_key(platform: str, user_id: str) -> str:
        """构造玩家唯一键。"""
        return f"{platform or 'unknown'}:{user_id}"

    def get_player(self, key: str) -> dict[str, Any] | None:
        """按键获取玩家。"""
        player = self.store.players.get(str(key))
        return player if isinstance(player, dict) else None

    def ensure_player(
        self,
        platform: str,
        user_id: str,
        name: str = "",
        umo: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """获取玩家档案，不存在则创建并发放初始金币。

        返回 `(player, created)`。每次调用都会更新昵称与会话标识，
        以便主动推送通知时能找到正确的会话。
        """
        key = self.player_key(platform, user_id)
        player = self.get_player(key)
        created = player is None
        if player is None:
            player = {
                "key": key,
                "platform": platform or "unknown",
                "user_id": str(user_id),
                "name": name or str(user_id),
                "coins": max(0, self.cfg.i("economy.starting_coins", 300)),
                "inventory": {},
                "catgirls": [],
                "umo": umo or "",
                "last_allowance_day": "",
                "created_at": now_ts(),
            }
            self.store.players[key] = player
        else:
            if name:
                player["name"] = name
            if umo:
                player["umo"] = umo
            if not isinstance(player.get("inventory"), dict):
                player["inventory"] = {}
            if not isinstance(player.get("catgirls"), list):
                player["catgirls"] = [
                    cid
                    for cid, cat in self.store.catgirls.items()
                    if cat.get("owner") == key
                ]
        return player, created

    def find_player(self, query: str) -> dict[str, Any] | None:
        """按玩家键、昵称或用户 ID 查找玩家（面板与管理员使用）。"""
        text = str(query or "").strip()
        if not text:
            return None
        player = self.get_player(text)
        if player is not None:
            return player
        low = text.lower()
        for candidate in self.store.players.values():
            if str(candidate.get("user_id")) == text:
                return candidate
        for candidate in self.store.players.values():
            if low and low == str(candidate.get("name", "")).lower():
                return candidate
        for candidate in self.store.players.values():
            if low and low in str(candidate.get("name", "")).lower():
                return candidate
        return None

    # ==================================================================
    # 猫娘
    # ==================================================================
    def catgirl_by_id(self, catgirl_id: str) -> dict[str, Any] | None:
        """按 ID 获取猫娘。"""
        cat = self.store.catgirls.get(str(catgirl_id or ""))
        return cat if isinstance(cat, dict) else None

    def catgirls_of(
        self, player: dict[str, Any], *, settle: bool = True
    ) -> list[dict[str, Any]]:
        """返回该玩家名下的所有猫娘（顺序稳定）。"""
        result: list[dict[str, Any]] = []
        for catgirl_id in player.get("catgirls") or []:
            cat = self.catgirl_by_id(str(catgirl_id))
            if cat is None:
                continue
            if settle:
                self.settle_catgirl(cat)
            result.append(cat)
        return result

    def alive_catgirls_of(self, player: dict[str, Any]) -> list[dict[str, Any]]:
        """返回该玩家名下仍活着的猫娘。"""
        return [cat for cat in self.catgirls_of(player) if cat.get("alive", True)]

    def catgirl_quota(self) -> int:
        """每名玩家可同时养活的猫娘数量上限。"""
        return max(1, self.cfg.i("economy.max_catgirls_per_player", 3))

    def catgirl_slots(self, player: dict[str, Any]) -> dict[str, int]:
        """返回该玩家的名额使用情况：存活数、离世数、上限、剩余名额。

        离世的猫娘不占名额，因此 `remaining` 只跟存活数有关。
        """
        cats = self.catgirls_of(player)
        alive = sum(1 for cat in cats if cat.get("alive", True))
        quota = self.catgirl_quota()
        return {
            "total": len(cats),
            "alive": alive,
            "dead": len(cats) - alive,
            "quota": quota,
            "remaining": max(0, quota - alive),
        }

    def all_catgirls(self, *, settle: bool = False) -> list[dict[str, Any]]:
        """返回全部猫娘（面板统计使用）。"""
        result = list(self.store.catgirls.values())
        if settle:
            for cat in result:
                self.settle_catgirl(cat)
        return result

    def resolve_catgirl(
        self, player: dict[str, Any], query: str
    ) -> dict[str, Any]:
        """在玩家自己的猫娘中解析名字 / ID / 序号。

        依次尝试：完整 ID → 精确名字 → 序号（从 1 开始）→ 名字包含。
        """
        text = str(query or "").strip()
        mine = self.catgirls_of(player)
        if not mine:
            raise GameError(
                "你还没有猫娘哦，快用「neko 认养 名字」领一只回家吧～"
            )
        if not text:
            if len(mine) == 1:
                return mine[0]
            names = "、".join(str(cat.get("name")) for cat in mine)
            raise GameError(f"你有好几只猫娘，请指定一只：{names}")

        for cat in mine:
            if str(cat.get("id")) == text:
                return cat
        for cat in mine:
            if str(cat.get("name")) == text:
                return cat
        if text.isdigit():
            index = int(text) - 1
            if 0 <= index < len(mine):
                return mine[index]
        low = text.lower()
        for cat in mine:
            if low and low in str(cat.get("name", "")).lower():
                return cat
        for cat in mine:
            if low and low in str(cat.get("id", "")).lower():
                return cat
        raise GameError(f"你名下没有叫 `{text}` 的猫娘。")

    def find_catgirl_globally(self, query: str) -> dict[str, Any] | None:
        """在全部猫娘中按 ID 或名字查找（求婚、排行榜使用）。"""
        text = str(query or "").strip()
        if not text:
            return None
        cat = self.catgirl_by_id(text)
        if cat is not None:
            return cat
        low = text.lower()
        for candidate in self.store.catgirls.values():
            if str(candidate.get("name")) == text:
                return candidate
        for candidate in self.store.catgirls.values():
            if low and low in str(candidate.get("name", "")).lower():
                return candidate
        return None

    def adopt(
        self, player: dict[str, Any], name: str = ""
    ) -> dict[str, Any]:
        """认养一只新猫娘并命名。"""
        limit = max(1, self.cfg.i("economy.max_catgirls_per_player", 3))
        # 只统计"存活"的猫娘：已离世的不占名额，玩家可以直接补养新的，
        # 不必先删除那只去世的（也就保留了将来用复活草救她的可能）。
        current = len(self.alive_catgirls_of(player))
        if current >= limit:
            raise GameError(
                f"最多只能同时养 {limit} 只猫娘哦（当前存活 {current}/{limit}）。"
                f"想再养新的，先用「/送养」与其中一只告别吧。"
            )

        if name:
            final_name = validate_name(name)
        else:
            final_name = self._random_name(player)
        self._assert_name_free(player, final_name)

        limits = self.cfg.limits()
        today = self.today()
        catgirl_id = new_id("c", self.store.next_counter("catgirl"))
        catgirl = {
            "id": catgirl_id,
            "name": final_name,
            "owner": player["key"],
            "created_at": now_ts(),
            "birth_day": today.isoformat(),
            "last_tick": today.isoformat(),
            "satiety": limits["satiety"],
            "hydration": limits["hydration"],
            "health": limits["health"],
            "energy": limits["energy"],
            "alive": True,
            "died_at": None,
            "death_reason": "",
            "tasks": {"date": "", "slots": []},
            "stats": {"tasks_done": 0, "coins_earned": 0, "items_used": 0},
        }
        self.store.catgirls[catgirl_id] = catgirl
        player.setdefault("catgirls", []).append(catgirl_id)
        return catgirl

    def _random_name(self, player: dict[str, Any]) -> str:
        """随机生成一个未被本玩家占用的名字。"""
        pool = [n for n in NAME_POOL if not self._name_taken(player, n)]
        if pool:
            return self.rng.choice(pool)
        index = 1
        while self._name_taken(player, f"猫娘{index}"):
            index += 1
        return f"猫娘{index}"

    def _name_taken(self, player: dict[str, Any], name: str) -> bool:
        return any(
            str(cat.get("name")) == name for cat in self.catgirls_of(player, settle=False)
        )

    def _assert_name_free(self, player: dict[str, Any], name: str) -> None:
        if self._name_taken(player, name):
            raise GameError(f"你已经有一只叫 `{name}` 的猫娘了，换个名字吧。")

    def rename(
        self, player: dict[str, Any], catgirl: dict[str, Any], new_name: str
    ) -> dict[str, Any]:
        """给猫娘改名。"""
        final_name = validate_name(new_name)
        self._assert_name_free(player, final_name)
        catgirl["name"] = final_name
        return catgirl

    def release_catgirl(
        self, player: dict[str, Any], catgirl: dict[str, Any]
    ) -> dict[str, Any]:
        """把猫娘送养（从玩家名下移除，并下架其挂单）。"""
        self._withdraw_catgirl_listings(str(catgirl["id"]))
        marriage = self.marriage_of(str(catgirl["id"]))
        if marriage is not None:
            self.store.state["marriages"] = [
                m for m in self.store.marriages if m.get("id") != marriage.get("id")
            ]
        self.store.catgirls.pop(str(catgirl["id"]), None)
        owned = player.get("catgirls") or []
        player["catgirls"] = [cid for cid in owned if str(cid) != str(catgirl["id"])]
        return catgirl

    # ==================================================================
    # 每日结算
    # ==================================================================
    #: 每只猫娘最多保留多少条"待通知的任务完成记录"，防止无人领取时无限增长
    _TASK_NOTICE_LIMIT = 10

    def settle_catgirl(
        self, catgirl: dict[str, Any], *, today: date | None = None
    ) -> dict[str, Any] | None:
        """结算一只猫娘。

        先**自动结算已完成的任务**（发放奖励），再执行生存衰减 —— 顺序很重要：
        如果任务是在猫娘饿死之前完成的，她依然应该拿到那份报酬。
        发放结果会记入待通知队列，由指令回复或后台任务告诉玩家。
        """
        if catgirl.get("alive", True):
            completed = self.settle_tasks(catgirl)
            if completed:
                notices = catgirl.get("task_notices")
                if not isinstance(notices, list):
                    notices = []
                    catgirl["task_notices"] = notices
                notices.extend(completed)
                # 只保留最近的若干条
                if len(notices) > self._TASK_NOTICE_LIMIT:
                    del notices[: len(notices) - self._TASK_NOTICE_LIMIT]
        return super().settle_catgirl(catgirl, today=today)

    def drain_task_notices(self, catgirl: dict[str, Any]) -> list[dict[str, Any]]:
        """取出并清空某只猫娘的"任务已完成"通知队列。"""
        notices = catgirl.get("task_notices")
        if not isinstance(notices, list) or not notices:
            return []
        catgirl["task_notices"] = []
        return list(notices)

    def tick(self) -> dict[str, Any]:
        """执行一次全局结算：任务自动发奖、生存衰减、商城刷新、过期求婚清理。

        返回本轮的变动（死亡事件、任务完成事件等），供 `main.py` 做主动通知。
        """
        today = self.today()
        deaths = self.settle_all(today=today)

        # 收集本轮的"任务自动完成"记录（settle_all 已顺带结算了任务奖励）
        task_events: list[dict[str, Any]] = []
        for catgirl in self.store.catgirls.values():
            for notice in self.drain_task_notices(catgirl):
                event = dict(notice)
                event["owner"] = catgirl.get("owner", "")
                task_events.append(event)

        shop = self.store.state.get("official_shop")
        shop_refreshed = not isinstance(shop, dict) or str(
            shop.get("date") or ""
        ) != today.isoformat()
        if shop_refreshed:
            self.ensure_official_shop(today=today)

        expired = self.expire_proposals()
        duel_events = self.expire_duels()
        self.store.state["meta"]["last_tick_day"] = today.isoformat()

        return {
            "date": today.isoformat(),
            "deaths": deaths,
            "task_done": task_events,
            "duels": duel_events,
            "shop_refreshed": shop_refreshed,
            "proposals_expired": expired,
        }

    # ==================================================================
    # 统计（供 WebUI 面板使用）
    # ==================================================================
    def overview(self) -> dict[str, Any]:
        """汇总运行状态，供插件面板展示。"""
        catgirls = self.all_catgirls()
        alive = sum(1 for cat in catgirls if cat.get("alive", True))
        players = list(self.store.players.values())
        total_coins = sum(max(0, as_int(p.get("coins"), 0)) for p in players)
        shop = self.ensure_official_shop()

        ranked = sorted(
            players, key=lambda p: as_int(p.get("coins"), 0), reverse=True
        )[:10]
        top_players = [
            {
                "name": p.get("name") or p.get("key"),
                "key": p.get("key"),
                "coins": as_int(p.get("coins"), 0),
                "catgirls": len(p.get("catgirls") or []),
            }
            for p in ranked
        ]

        return {
            "players": len(players),
            "catgirls": len(catgirls),
            "alive": alive,
            "dead": len(catgirls) - alive,
            "marriages": len(self.store.marriages),
            "listings": len(self.store.listings),
            "active_duels": len(self.active_duels()),
            "pending_proposals": sum(
                1
                for p in self.store.proposals
                if p.get("status") == "pending"
            ),
            "total_coins": total_coins,
            "shop_date": str(shop.get("date") or ""),
            "shop_size": len(shop.get("entries") or []),
            "top_players": top_players,
        }

    def catgirl_snapshot(self, catgirl: dict[str, Any]) -> dict[str, Any]:
        """把猫娘整理成便于渲染的字典（含伴侣与状态标签）。"""
        self.settle_catgirl(catgirl)
        partner = self.partner_of(str(catgirl["id"]))
        owner = self.get_player(str(catgirl.get("owner") or "")) or {}
        alive = bool(catgirl.get("alive", True))
        return {
            "id": catgirl.get("id"),
            "name": catgirl.get("name"),
            "owner": catgirl.get("owner"),
            "owner_name": owner.get("name") or catgirl.get("owner"),
            "alive": alive,
            "satiety": as_int(catgirl.get("satiety"), 0),
            "hydration": as_int(catgirl.get("hydration"), 0),
            "health": as_int(catgirl.get("health"), 0),
            "energy": as_int(catgirl.get("energy"), 0),
            "flags": self.status_flags(catgirl),
            "partner": partner.get("name") if partner else None,
            "duel": (
                self.duel_of_catgirl(str(catgirl["id"])) or {}
            ).get("id"),
            "stats": catgirl.get("stats") or {},
            # 已离世的猫娘不再生成每日任务
            "tasks": self.task_slots(catgirl) if alive else [],
        }


__all__ = ["NekoWorld", "validate_name", "MAX_NAME_LENGTH"]
