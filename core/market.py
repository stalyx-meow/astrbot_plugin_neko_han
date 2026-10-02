"""商城系统：官方每日商城 + 玩家之间的自由交易市场。

官方商城
    每天在配置的刷新时刻重新生成，商品从"允许上架"的道具中随机抽取，
    价格围绕基础价在 `±price_fluctuation` 区间内波动，并按道具自身的
    库存区间给出当日库存（可关闭库存限制）。

玩家市场
    玩家把自己仓库里的道具挂单出售，其他玩家可以按单价购买。
    挂单时道具进入托管（从仓库扣除），取消或流拍时原样退回，
    成交后按配置收取手续费，用于回收金币、抑制通胀。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .errors import GameError
from .util import as_int, as_float, clamp, new_id, now_ts

#: 单次交易的数量上限，避免误输入
MAX_TRADE_QTY = 99


class MarketMixin:
    """商城与交易逻辑。依赖宿主提供的 `store` / `cfg` / `registry` / `rng`。"""

    # ==================================================================
    # 官方商城
    # ==================================================================
    def ensure_official_shop(self, *, today: date | None = None) -> dict[str, Any]:
        """确保官方商城是"今天"的，返回商城文档。"""
        today = today or self.today()
        day = today.isoformat()
        shop = self.store.state.get("official_shop")
        if not isinstance(shop, dict):
            shop = {"date": "", "entries": []}
            self.store.state["official_shop"] = shop
        if str(shop.get("date") or "") != day or not isinstance(
            shop.get("entries"), list
        ):
            self._generate_official_shop(shop, day)
        return shop

    def _generate_official_shop(self, shop: dict[str, Any], day: str) -> None:
        """重新生成当日官方商城。"""
        candidates = self.registry.shop_items()
        size = max(1, self.cfg.i("shop.shop_size", 8))
        scale = max(0.0, self.cfg.f("shop.price_fluctuation_scale", 1.0))
        restock = self.cfg.b("shop.restock_enabled", True)

        if len(candidates) <= size:
            chosen = list(candidates)
        else:
            chosen = self.rng.sample(candidates, size)

        entries: list[dict[str, Any]] = []
        for item in chosen:
            base = max(0, as_int(item.get("base_price"), 0))
            fluctuation = clamp(as_float(item.get("price_fluctuation"), 0.0), 0.0, 1.0)
            if base > 0 and fluctuation > 0:
                factor = 1.0 + self.rng.uniform(-fluctuation, fluctuation) * scale
                price = max(1, int(round(base * factor)))
            else:
                price = base
            if restock:
                low = max(0, as_int(item.get("stock_min"), 1))
                high = max(low, as_int(item.get("stock_max"), low))
                stock = self.rng.randint(low, high) if high > low else low
            else:
                stock = -1  # 不限量
            entries.append(
                {
                    "item_id": item["id"],
                    "price": price,
                    "base_price": base,
                    "stock": stock,
                    "stock_initial": stock,
                }
            )
        entries.sort(key=lambda e: as_int(e.get("price"), 0))
        shop["date"] = day
        shop["entries"] = entries

    def refresh_official_shop(self) -> dict[str, Any]:
        """立刻重新生成官方商城（面板按钮 / 管理员指令使用）。"""
        shop = self.store.state.setdefault("official_shop", {"date": "", "entries": []})
        self._generate_official_shop(shop, self.today().isoformat())
        return shop

    def shop_view(self) -> list[dict[str, Any]]:
        """返回官方商城的展示数据（已关联道具定义）。"""
        shop = self.ensure_official_shop()
        rows: list[dict[str, Any]] = []
        for entry in shop.get("entries") or []:
            item = self.registry.by_id(str(entry.get("item_id")))
            if item is None or not item.get("enabled", True):
                continue
            stock = as_int(entry.get("stock"), -1)
            rows.append(
                {
                    "item": item,
                    "price": as_int(entry.get("price"), 0),
                    "base_price": as_int(entry.get("base_price"), 0),
                    "stock": stock,
                    "stock_initial": as_int(entry.get("stock_initial"), stock),
                    "unlimited": stock < 0,
                }
            )
        return rows

    def shop_entry(self, item_query: str) -> dict[str, Any] | None:
        """在当日官方商城中查找某个道具的条目。"""
        item = self.registry.resolve(item_query)
        if item is None:
            return None
        shop = self.ensure_official_shop()
        for entry in shop.get("entries") or []:
            if entry.get("item_id") == item["id"]:
                return entry
        return None

    def buy_official(
        self, player: dict[str, Any], item_query: str, qty: int = 1
    ) -> dict[str, Any]:
        """从官方商城购买道具，返回交易摘要。"""
        item = self.registry.resolve(item_query)
        if item is None:
            raise GameError(f"商城里没有叫 `{item_query}` 的道具。")

        qty = as_int(qty, 1)
        if qty < 1:
            raise GameError("购买数量至少为 1。")
        if qty > MAX_TRADE_QTY:
            raise GameError(f"一次最多购买 {MAX_TRADE_QTY} 个。")

        entry = self.shop_entry(str(item["id"]))
        if entry is None:
            raise GameError(
                f"`{item['name']}` 今天没有上架，看看别的吧（每天 {self.cfg.day_reset_hour} 点刷新）。"
            )

        stock = as_int(entry.get("stock"), -1)
        if stock >= 0 and stock < qty:
            raise GameError(
                f"`{item['name']}` 今天只剩 {stock} 个了，买不了 {qty} 个。"
            )

        price = as_int(entry.get("price"), 0)
        total = price * qty
        self.spend_coins(player, total, f"购买 {item['name']}×{qty}")
        if stock >= 0:
            entry["stock"] = stock - qty
        self._inv_add(player, str(item["id"]), qty)
        return {
            "item": item,
            "qty": qty,
            "unit_price": price,
            "total": total,
            "remaining_stock": as_int(entry.get("stock"), -1),
        }

    # ==================================================================
    # 玩家市场
    # ==================================================================
    def market_listings(
        self,
        *,
        exclude_player: str | None = None,
        item_query: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """返回在售商品的展示数据（已关联道具与卖家信息）。"""
        item_filter = None
        if item_query:
            resolved = self.registry.resolve(item_query)
            item_filter = resolved["id"] if resolved else str(item_query).lower()

        rows: list[dict[str, Any]] = []
        for listing in self.store.listings:
            if exclude_player and listing.get("seller") == exclude_player:
                continue
            if item_filter and listing.get("item_id") != item_filter:
                continue
            item = self.registry.by_id(str(listing.get("item_id")))
            if item is None or not item.get("enabled", True):
                continue
            seller_key = str(listing.get("seller") or "")
            seller = self.store.players.get(seller_key) or {}
            rows.append(
                {
                    "listing": listing,
                    "item": item,
                    "seller_key": seller_key,
                    "seller_name": seller.get("name") or seller_key,
                    "seller_catgirl": listing.get("catgirl_name")
                    or listing.get("catgirl"),
                }
            )
        rows.sort(key=lambda row: as_int(row["listing"].get("unit_price"), 0))
        return rows[: max(1, limit)]

    def my_listings(self, player: dict[str, Any]) -> list[dict[str, Any]]:
        """返回该玩家自己的挂单。"""
        rows: list[dict[str, Any]] = []
        for listing in self.store.listings:
            if listing.get("seller") != player["key"]:
                continue
            item = self.registry.by_id(str(listing.get("item_id")))
            if item is None:
                continue
            rows.append({"listing": listing, "item": item})
        rows.sort(key=lambda row: float(row["listing"].get("created") or 0))
        return rows

    def sell_listing(
        self,
        player: dict[str, Any],
        catgirl: dict[str, Any],
        item_query: str,
        qty: int,
        unit_price: int,
    ) -> dict[str, Any]:
        """把仓库中的道具挂到市场出售（道具进入托管）。"""
        if not catgirl.get("alive", True):
            raise GameError(f"{catgirl.get('name')} 已经离开了，没办法替你去摆摊。")

        item = self.registry.resolve(item_query)
        if item is None:
            raise GameError(f"找不到道具 `{item_query}`。")
        if not item.get("enabled", True):
            raise GameError(f"`{item['name']}` 已被禁用，无法上架。")
        if not item.get("tradable", True):
            raise GameError(f"`{item['name']}` 是绑定物品，不能交易。")

        qty = as_int(qty, 0)
        unit_price = as_int(unit_price, 0)
        if qty < 1:
            raise GameError("上架数量至少为 1。")
        if qty > MAX_TRADE_QTY:
            raise GameError(f"一次最多上架 {MAX_TRADE_QTY} 个。")
        if unit_price < 1:
            raise GameError("单价至少为 1 金币。")

        limit = max(1, self.cfg.i("economy.max_listings_per_player", 10))
        if len(self.my_listings(player)) >= limit:
            raise GameError(f"你同时最多只能挂 {limit} 个商品，先下架一些吧。")

        self._inv_remove(player, str(item["id"]), qty)

        listing = {
            "id": new_id("L", self.store.next_counter("listing")),
            "seller": player["key"],
            "catgirl": catgirl["id"],
            "catgirl_name": catgirl.get("name"),
            "item_id": item["id"],
            "qty": qty,
            "unit_price": unit_price,
            "created": now_ts(),
        }
        self.store.listings.append(listing)
        return {"listing": listing, "item": item}

    def buy_listing(
        self, player: dict[str, Any], listing_id: str, qty: int = 1
    ) -> dict[str, Any]:
        """购买市场上的挂单，返回交易摘要。"""
        listing = self._find_listing(listing_id)
        if listing.get("seller") == player["key"]:
            raise GameError("这是你自己的挂单，不用买啦。")

        item = self.registry.by_id(str(listing.get("item_id")))
        if item is None:
            raise GameError("该商品对应的道具已被删除，请联系管理员。")

        available = max(0, as_int(listing.get("qty"), 0))
        qty = as_int(qty, 1)
        if qty < 1:
            raise GameError("购买数量至少为 1。")
        if qty > available:
            raise GameError(f"该挂单只剩 {available} 个了。")

        unit_price = max(0, as_int(listing.get("unit_price"), 0))
        total = unit_price * qty
        fee_percent = clamp(self.cfg.f("economy.market_fee_percent", 5.0), 0.0, 100.0)
        fee = int(total * fee_percent / 100.0)
        income = total - fee

        self.spend_coins(player, total, f"购买 {item['name']}×{qty}")
        self._inv_add(player, str(item["id"]), qty)

        remaining = available - qty
        seller_key = str(listing.get("seller") or "")
        seller = self.store.players.get(seller_key)
        if seller is not None:
            self.add_coins(seller, income)
        if remaining > 0:
            listing["qty"] = remaining
        else:
            self.store.state["listings"] = [
                item_row
                for item_row in self.store.listings
                if item_row.get("id") != listing.get("id")
            ]

        return {
            "item": item,
            "qty": qty,
            "unit_price": unit_price,
            "total": total,
            "fee": fee,
            "seller_income": income,
            "seller_name": (seller or {}).get("name") or seller_key,
            "remaining": remaining,
        }

    def cancel_listing(
        self, player: dict[str, Any], listing_id: str
    ) -> dict[str, Any]:
        """下架自己的挂单并把道具退回仓库。"""
        listing = self._find_listing(listing_id)
        if listing.get("seller") != player["key"]:
            raise GameError("只能下架自己的商品。")
        qty = max(0, as_int(listing.get("qty"), 0))
        self._inv_add(player, str(listing.get("item_id")), qty)
        self.store.state["listings"] = [
            row for row in self.store.listings if row.get("id") != listing.get("id")
        ]
        item = self.registry.by_id(str(listing.get("item_id")))
        return {"listing": listing, "item": item, "qty": qty}

    def _find_listing(self, listing_id: str) -> dict[str, Any]:
        """按编号查找挂单，容忍用户省略前缀。"""
        key = str(listing_id or "").strip().upper()
        if key and not key.startswith("L"):
            key = f"L{key}"
        for listing in self.store.listings:
            if str(listing.get("id", "")).upper() == key:
                return listing
        raise GameError(f"找不到编号为 `{listing_id}` 的在售商品。")

    def _withdraw_catgirl_listings(self, catgirl_id: str) -> int:
        """把某只猫娘的挂单全部下架并退回卖家仓库，返回下架数量。"""
        removed = [
            row for row in self.store.listings if row.get("catgirl") == catgirl_id
        ]
        if not removed:
            return 0
        for row in removed:
            seller = self.store.players.get(str(row.get("seller") or ""))
            if seller is not None:
                self._inv_add(seller, str(row.get("item_id")), max(0, as_int(row.get("qty"), 0)))
        removed_ids = {row.get("id") for row in removed}
        self.store.state["listings"] = [
            row for row in self.store.listings if row.get("id") not in removed_ids
        ]
        return len(removed)

    def clear_player_listings(self, player: dict[str, Any]) -> int:
        """下架某玩家的全部挂单并退回仓库（面板/管理员使用）。"""
        removed = [row for row in self.store.listings if row.get("seller") == player["key"]]
        for row in removed:
            self._inv_add(player, str(row.get("item_id")), max(0, as_int(row.get("qty"), 0)))
        removed_ids = {row.get("id") for row in removed}
        self.store.state["listings"] = [
            row for row in self.store.listings if row.get("id") not in removed_ids
        ]
        return len(removed)


__all__ = ["MarketMixin", "MAX_TRADE_QTY"]
