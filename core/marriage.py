"""结婚系统：求婚、同意/拒绝、结婚关系与离婚。

求婚以"猫娘 -> 猫娘"为单位：A 玩家的猫娘向 B 玩家的猫娘求婚，
由 B 玩家本人同意后才成立。这样既符合"猫娘之间结婚"的设定，
又保证只有主人能替自己的猫娘做决定。
"""

from __future__ import annotations

from typing import Any

from .errors import GameError
from .util import as_int, new_id, now_ts

PROPOSAL_PENDING = "pending"
PROPOSAL_ACCEPTED = "accepted"
PROPOSAL_REJECTED = "rejected"
PROPOSAL_EXPIRED = "expired"

#: 保留的历史记录上限，避免存档无限增长
_MAX_HISTORY = 200


class MarriageMixin:
    """婚姻相关逻辑。依赖宿主提供的 `store` / `cfg` / `catgirl_by_id`。"""

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def marriage_of(self, catgirl_id: str) -> dict[str, Any] | None:
        """返回包含该猫娘的婚姻记录。"""
        for marriage in self.store.marriages:
            if catgirl_id in (marriage.get("a"), marriage.get("b")):
                return marriage
        return None

    def partner_of(self, catgirl_id: str) -> dict[str, Any] | None:
        """返回该猫娘的伴侣猫娘记录。"""
        marriage = self.marriage_of(catgirl_id)
        if not marriage:
            return None
        partner_id = marriage.get("b") if marriage.get("a") == catgirl_id else marriage.get("a")
        return self.catgirl_by_id(str(partner_id))

    def is_married(self, catgirl_id: str) -> bool:
        """该猫娘是否已婚（关闭多配偶时用于校验）。"""
        if self.cfg.b("marriage.allow_polygamy", False):
            return False
        return self.marriage_of(catgirl_id) is not None

    # ------------------------------------------------------------------
    # 求婚
    # ------------------------------------------------------------------
    def propose(
        self,
        player: dict[str, Any],
        my_catgirl: dict[str, Any],
        target_query: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """发起求婚，返回 `(proposal, target_catgirl)`。"""
        self.expire_proposals()

        if not my_catgirl.get("alive", True):
            raise GameError(f"{my_catgirl.get('name')} 已经离开了，无法求婚……")

        target = self.find_catgirl_globally(target_query)
        if target is None:
            raise GameError(
                f"找不到叫 `{target_query}` 的猫娘。可以用「neko 猫娘榜」看看大家的猫娘。"
            )
        if target["id"] == my_catgirl["id"]:
            raise GameError("不能向自己求婚哦，换一只猫娘吧。")
        if not target.get("alive", True):
            raise GameError(f"{target.get('name')} 已经离开了，没办法接受求婚。")

        target_owner = str(target.get("owner") or "")
        if target_owner == player["key"] and not self.cfg.b(
            "marriage.self_marriage_allowed", False
        ):
            raise GameError(
                "不能让自己的猫娘互相结婚（管理员可在插件配置中开启该选项）。"
            )

        if self.is_married(my_catgirl["id"]):
            raise GameError(
                f"{my_catgirl.get('name')} 已经结婚了，"
                f"想再婚的话请先用「neko 离婚 {my_catgirl.get('name')}」。"
            )
        if self.is_married(target["id"]):
            raise GameError(f"{target.get('name')} 已经有伴侣了，来晚一步……")

        for proposal in self.store.proposals:
            if proposal.get("status") != PROPOSAL_PENDING:
                continue
            if proposal.get("from_cat") == my_catgirl["id"] and proposal.get(
                "to_cat"
            ) == target["id"]:
                raise GameError(
                    f"你已经向 {target.get('name')} 求过婚了，"
                    f"等待对方主人同意吧（编号 {proposal.get('id')}）。"
                )

        proposal = {
            "id": new_id("P", self.store.next_counter("proposal")),
            "from_player": player["key"],
            "from_player_name": player.get("name") or player["key"],
            "from_cat": my_catgirl["id"],
            "from_cat_name": my_catgirl.get("name"),
            "to_player": target_owner,
            "to_cat": target["id"],
            "to_cat_name": target.get("name"),
            "created": now_ts(),
            "status": PROPOSAL_PENDING,
        }
        self.store.proposals.append(proposal)
        self._trim_proposals()
        return proposal, target

    def expire_proposals(self) -> int:
        """把超时的求婚标记为过期，返回本次过期的数量。"""
        hours = max(1, self.cfg.i("marriage.proposal_expire_hours", 24))
        deadline = now_ts() - hours * 3600
        count = 0
        for proposal in self.store.proposals:
            if proposal.get("status") != PROPOSAL_PENDING:
                continue
            if float(proposal.get("created") or 0) < deadline:
                proposal["status"] = PROPOSAL_EXPIRED
                count += 1
        return count

    def _trim_proposals(self) -> None:
        """只保留最近的若干条求婚记录。"""
        if len(self.store.proposals) <= _MAX_HISTORY:
            return
        self.store.proposals.sort(key=lambda p: float(p.get("created") or 0))
        del self.store.proposals[: len(self.store.proposals) - _MAX_HISTORY]

    # ------------------------------------------------------------------
    # 同意 / 拒绝
    # ------------------------------------------------------------------
    def received_proposals(self, player: dict[str, Any]) -> list[dict[str, Any]]:
        """返回等待该玩家处理的求婚请求。"""
        self.expire_proposals()
        return [
            p
            for p in self.store.proposals
            if p.get("status") == PROPOSAL_PENDING
            and p.get("to_player") == player["key"]
        ]

    def sent_proposals(self, player: dict[str, Any]) -> list[dict[str, Any]]:
        """返回该玩家发出且仍在等待的求婚请求。"""
        self.expire_proposals()
        return [
            p
            for p in self.store.proposals
            if p.get("status") == PROPOSAL_PENDING
            and p.get("from_player") == player["key"]
        ]

    def _find_proposal(
        self, player: dict[str, Any], proposal_id: str
    ) -> dict[str, Any]:
        """按编号查找"发给我"的待处理求婚。"""
        key = str(proposal_id or "").strip().upper()
        if key and not key.startswith("P"):
            key = f"P{key}"
        for proposal in self.store.proposals:
            if str(proposal.get("id", "")).upper() != key:
                continue
            if proposal.get("to_player") != player["key"]:
                raise GameError(f"求婚 `{key}` 不是发给你的，无法处理。")
            if proposal.get("status") != PROPOSAL_PENDING:
                raise GameError(f"求婚 `{key}` 已经处理过了（状态：{proposal.get('status')}）。")
            return proposal
        raise GameError(f"找不到编号为 `{proposal_id}` 的待处理求婚。")

    def accept_proposal(
        self, player: dict[str, Any], proposal_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """同意求婚，返回 `(proposal, marriage)`。"""
        proposal = self._find_proposal(player, proposal_id)
        mine = self.catgirl_by_id(str(proposal.get("to_cat")))
        theirs = self.catgirl_by_id(str(proposal.get("from_cat")))
        if mine is None or theirs is None:
            raise GameError("求婚涉及的猫娘已经不存在了。")
        if not mine.get("alive", True) or not theirs.get("alive", True):
            raise GameError("其中一方已经离开了，这段姻缘只能作罢。")
        if self.is_married(mine["id"]) or self.is_married(theirs["id"]):
            raise GameError("其中一方已经有伴侣了，婚姻无法成立。")

        marriage = {
            "id": new_id("M", self.store.next_counter("marriage")),
            "a": theirs["id"],
            "b": mine["id"],
            "since": now_ts(),
            "owners": [proposal.get("from_player"), proposal.get("to_player")],
        }
        self.store.marriages.append(marriage)
        proposal["status"] = PROPOSAL_ACCEPTED
        proposal["resolved_at"] = now_ts()
        return proposal, marriage

    def reject_proposal(
        self, player: dict[str, Any], proposal_id: str
    ) -> dict[str, Any]:
        """拒绝求婚，返回被拒绝的请求。"""
        proposal = self._find_proposal(player, proposal_id)
        proposal["status"] = PROPOSAL_REJECTED
        proposal["resolved_at"] = now_ts()
        return proposal

    # ------------------------------------------------------------------
    # 离婚
    # ------------------------------------------------------------------
    def divorce(
        self, player: dict[str, Any], catgirl: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """让指定猫娘离婚，返回 `(marriage, 被移除的伴侣猫娘)`。"""
        marriage = self.marriage_of(str(catgirl["id"]))
        if marriage is None:
            raise GameError(f"{catgirl.get('name')} 还没有结婚呢。")
        if player["key"] not in (marriage.get("owners") or []):
            raise GameError("只有当事人的主人才能办理离婚。")

        cost = max(0, self.cfg.i("marriage.divorce_cost", 500))
        if cost:
            self.spend_coins(player, cost, "离婚手续费")

        partner = self.partner_of(str(catgirl["id"]))
        # 就地替换列表内容（store.marriages 是只读属性，不能整体赋值）
        self.store.state["marriages"] = [
            m for m in self.store.marriages if m.get("id") != marriage.get("id")
        ]
        return marriage, partner  # type: ignore[return-value]

    def marriage_count(self) -> int:
        """当前婚姻总数（用于面板统计）。"""
        return len(self.store.marriages)


__all__ = ["MarriageMixin"]
