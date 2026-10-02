"""道具注册表：定义、校验、增删改查与默认值恢复。

道具定义存放在 `data/plugin_data/neko_han/items.json`，由 WebUI 面板
（`pages/panel`）可视化编辑；玩家运行时通过本模块读取。
"""

from __future__ import annotations

import re
from typing import Any

from .defs import (
    CATEGORIES,
    DEFAULT_ITEMS,
    EFFECT_FLAGS,
    EFFECT_KEYS,
    empty_effect,
)
from .errors import GameError
from .store import Store
from .util import as_bool, as_float, as_int, clamp

#: 合法的道具 ID 形式
_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")


def effect_summary(effect: dict[str, Any]) -> str:
    """把 effect 结构转成 `饱食+30 · 水分+35` 这样的短文案。"""
    parts: list[str] = []
    labels = {
        "satiety": "饱食",
        "hydration": "水分",
        "energy": "精力",
        "health": "健康",
    }
    for key in EFFECT_KEYS:
        value = as_int(effect.get(key), 0)
        if value:
            sign = "+" if value > 0 else ""
            parts.append(f"{labels[key]}{sign}{value}")
    if as_bool(effect.get("revive"), False):
        parts.append("复活")
    return " · ".join(parts) if parts else "无效果"


def normalize_item(payload: dict[str, Any], *, fallback_id: str = "") -> dict[str, Any]:
    """把任意（可能来自 WebUI 的）输入清洗成规范的道具定义。

    抛出 `GameError` 表示输入非法，消息可直接展示给管理员。
    """
    if not isinstance(payload, dict):
        raise GameError("道具数据格式不正确")

    raw_id = str(payload.get("id") or fallback_id or "").strip().lower()
    raw_id = raw_id.replace("-", "_").replace(" ", "_")
    if not raw_id:
        raise GameError("道具 ID 不能为空")
    if not _ID_RE.match(raw_id):
        raise GameError("道具 ID 只能包含小写字母、数字和下划线，长度 2-32")

    name = str(payload.get("name") or "").strip()
    if not name:
        raise GameError("道具名称不能为空")
    if len(name) > 20:
        raise GameError("道具名称不能超过 20 个字符")

    category = str(payload.get("category") or "food").strip().lower()
    if category not in CATEGORIES:
        raise GameError(f"道具分类必须是 {'/'.join(CATEGORIES)} 之一")

    base_price = as_int(payload.get("base_price"), 0)
    if base_price < 0:
        raise GameError("基础价格不能为负数")

    fluctuation = clamp(as_float(payload.get("price_fluctuation"), 0.0), 0.0, 1.0)

    stock_min = as_int(payload.get("stock_min"), 1)
    stock_max = as_int(payload.get("stock_max"), 5)
    stock_min = max(0, stock_min)
    stock_max = max(0, stock_max)
    if stock_min > stock_max:
        raise GameError("最小库存不能大于最大库存")

    effect_raw = payload.get("effect")
    if not isinstance(effect_raw, dict):
        effect_raw = {}
    effect = empty_effect()
    for key in EFFECT_KEYS:
        effect[key] = as_int(effect_raw.get(key), 0)
    for flag in EFFECT_FLAGS:
        effect[flag] = as_bool(effect_raw.get(flag), False)

    description = str(payload.get("description") or "").strip()
    if len(description) > 120:
        description = description[:120]

    emoji = str(payload.get("emoji") or "❔").strip() or "❔"
    if len(emoji) > 4:
        emoji = emoji[:4]

    return {
        "id": raw_id,
        "name": name,
        "emoji": emoji,
        "category": category,
        "description": description,
        "base_price": base_price,
        "price_fluctuation": fluctuation,
        "shop_enabled": as_bool(payload.get("shop_enabled"), True),
        "stock_min": stock_min,
        "stock_max": stock_max,
        "effect": effect,
        "tradable": as_bool(payload.get("tradable"), True),
        "enabled": as_bool(payload.get("enabled"), True),
    }


class ItemRegistry:
    """对 `store.items_doc["items"]` 的读写封装。"""

    def __init__(self, store: Store) -> None:
        self.store = store
        #: 内存缓存：item_id -> 规范化后的定义
        self._cache: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.reload()

    # ------------------------------------------------------------------
    # 载入与缓存
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """从 store 重新构建缓存（载入存档或面板修改后调用）。"""
        items: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        raw_items = self.store.items_doc.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        for raw in raw_items:
            try:
                item = normalize_item(raw)
            except GameError:
                continue
            if item["id"] in items:
                continue
            items[item["id"]] = item
            order.append(item["id"])
        self._cache = items
        self._order = order

    def ensure_seeded(self) -> bool:
        """若道具表为空则写入默认道具。返回是否发生了写入。"""
        if self._cache:
            return False
        self.reset()
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def all(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        """按定义顺序返回所有道具。"""
        result = [self._cache[item_id] for item_id in self._order]
        if not include_disabled:
            result = [item for item in result if item.get("enabled", True)]
        return result

    def shop_items(self) -> list[dict[str, Any]]:
        """可作为官方商城商品的候选道具。"""
        return [
            item
            for item in self.all()
            if item.get("shop_enabled", True) and item.get("base_price", 0) >= 0
        ]

    def by_id(self, item_id: str) -> dict[str, Any] | None:
        return self._cache.get(str(item_id or "").strip().lower())

    def resolve(self, query: str, *, include_disabled: bool = False) -> dict[str, Any] | None:
        """按 ID 或名称模糊匹配一个道具。

        匹配顺序：精确 ID → 精确名称 → 名称包含 → ID 包含。
        """
        text = str(query or "").strip()
        if not text:
            return None
        pool = self.all(include_disabled=include_disabled)
        low = text.lower()
        for item in pool:
            if item["id"] == low:
                return item
        for item in pool:
            if item["name"] == text:
                return item
        for item in pool:
            if low and low in item["name"].lower():
                return item
        for item in pool:
            if low and low in item["id"]:
                return item
        return None

    def names(self) -> list[str]:
        return [item["name"] for item in self.all()]

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def _flush(self) -> None:
        self.store.items_doc["items"] = [self._cache[i] for i in self._order]
        self.store.save_items_now()

    def save(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """新增或更新一个道具。

        返回 `(item, created)`。当 `payload` 未提供 id 时会依据名称生成。
        """
        fallback_id = ""
        if not str(payload.get("id") or "").strip():
            fallback_id = self._generate_id(str(payload.get("name") or "item"))
        item = normalize_item(payload, fallback_id=fallback_id)

        created = item["id"] not in self._cache
        self._cache[item["id"]] = item
        if created:
            self._order.append(item["id"])
        self._flush()
        return item, created

    def delete(self, item_id: str) -> None:
        """删除一个道具定义。"""
        key = str(item_id or "").strip().lower()
        if key not in self._cache:
            raise GameError(f"找不到道具 `{item_id}`")
        del self._cache[key]
        self._order = [i for i in self._order if i != key]
        self._flush()

    def reset(self) -> list[dict[str, Any]]:
        """用内置默认道具表覆盖当前定义。"""
        items: list[dict[str, Any]] = []
        for raw in DEFAULT_ITEMS:
            items.append(normalize_item(raw))
        self.store.items_doc["items"] = items
        self.store.save_items_now()
        self.reload()
        return self.all(include_disabled=True)

    def _generate_id(self, name: str) -> str:
        """由名称生成一个不冲突的 ID。"""
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if not slug:
            slug = "item"
        if len(slug) < 2:
            slug = f"{slug}_x"
        slug = slug[:28]
        if slug not in self._cache and _ID_RE.match(slug):
            return slug
        index = 2
        while f"{slug}_{index}" in self._cache:
            index += 1
        return f"{slug}_{index}"


__all__ = ["ItemRegistry", "normalize_item", "effect_summary"]
