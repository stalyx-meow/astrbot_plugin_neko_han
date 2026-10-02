"""静态定义：道具分类、默认道具表、任务模板池、文案标签。

这里是"内容层"，与逻辑层分离，方便后续扩展或由面板覆盖。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 道具
# ---------------------------------------------------------------------------

CATEGORIES: list[str] = ["food", "drink", "medicine", "special", "toy"]

CATEGORY_LABELS: dict[str, str] = {
    "food": "食物",
    "drink": "饮品",
    "medicine": "药品",
    "special": "特殊",
    "toy": "玩具",
}

# 数值型效果字段（会累加到猫娘属性上）
EFFECT_KEYS: list[str] = ["satiety", "hydration", "energy", "health"]

# 布尔型效果字段（触发特殊行为）
EFFECT_FLAGS: list[str] = ["revive"]

EFFECT_LABELS: dict[str, str] = {
    "satiety": "饱食度",
    "hydration": "水分",
    "energy": "精力",
    "health": "健康",
    "revive": "复活",
}


def empty_effect() -> dict[str, object]:
    """返回一个全零的 effect 结构。"""
    effect: dict[str, object] = {key: 0 for key in EFFECT_KEYS}
    for flag in EFFECT_FLAGS:
        effect[flag] = False
    return effect


def make_item(
    item_id: str,
    name: str,
    emoji: str,
    category: str,
    price: int,
    fluctuation: float,
    stock_min: int,
    stock_max: int,
    *,
    description: str = "",
    satiety: int = 0,
    hydration: int = 0,
    energy: int = 0,
    health: int = 0,
    revive: bool = False,
    shop_enabled: bool = True,
    tradable: bool = True,
    enabled: bool = True,
) -> dict[str, object]:
    """构造一条默认道具定义（内部使用，减少样板代码）。"""
    return {
        "id": item_id,
        "name": name,
        "emoji": emoji,
        "category": category,
        "description": description,
        "base_price": price,
        "price_fluctuation": fluctuation,
        "shop_enabled": shop_enabled,
        "stock_min": stock_min,
        "stock_max": stock_max,
        "effect": {
            "satiety": satiety,
            "hydration": hydration,
            "energy": energy,
            "health": health,
            "revive": revive,
        },
        "tradable": tradable,
        "enabled": enabled,
    }


#: 插件首次运行（以及"恢复默认"）时写入的道具表。
DEFAULT_ITEMS: list[dict[str, object]] = [
    # ---------------------------- 食物 ----------------------------
    make_item(
        "cat_food",
        "猫粮",
        "🍚",
        "food",
        20,
        0.20,
        10,
        25,
        description="最普通的猫粮。虽然不豪华，但能填饱肚子。",
        satiety=30,
    ),
    make_item(
        "dried_fish",
        "小鱼干",
        "🐟",
        "food",
        38,
        0.25,
        6,
        18,
        description="香喷喷的小鱼干，猫娘的最爱之一。",
        satiety=45,
    ),
    make_item(
        "salmon",
        "三文鱼刺身",
        "🍣",
        "food",
        68,
        0.35,
        2,
        6,
        description="新鲜的三文鱼刺身，美味又养身。",
        satiety=60,
        health=8,
    ),
    make_item(
        "premium_can",
        "高级罐头",
        "🥫",
        "food",
        85,
        0.30,
        2,
        8,
        description="贵有贵的道理，一口下去幸福感爆棚。",
        satiety=70,
        health=5,
    ),
    # ---------------------------- 饮品 ----------------------------
    make_item(
        "water",
        "清水",
        "💧",
        "drink",
        8,
        0.10,
        20,
        50,
        description="干净的清水。不喝水的猫娘撑不了几天。",
        hydration=35,
    ),
    make_item(
        "milk",
        "牛奶",
        "🥛",
        "drink",
        26,
        0.20,
        8,
        20,
        description="温热的牛奶，补水又顶饿。",
        hydration=50,
        satiety=5,
    ),
    make_item(
        "catnip_tea",
        "猫薄荷茶",
        "🍵",
        "drink",
        48,
        0.30,
        3,
        10,
        description="喝了会原地打滚，但确实很提神。",
        hydration=40,
        energy=10,
    ),
    # ---------------------------- 药品 ----------------------------
    make_item(
        "energy_potion",
        "精力药水",
        "⚡",
        "medicine",
        130,
        0.25,
        2,
        8,
        description="一口下去精力充沛，适合赶任务进度。",
        energy=50,
    ),
    make_item(
        "medkit",
        "医疗包",
        "🩹",
        "medicine",
        160,
        0.25,
        1,
        5,
        description="治疗伤病，恢复健康值。",
        health=50,
    ),
    # ---------------------------- 特殊 ----------------------------
    make_item(
        "revive_grass",
        "复活草",
        "🌿",
        "special",
        900,
        0.50,
        0,
        1,
        description="传说中的草药，能让离世的猫娘重新睁开眼睛。",
        revive=True,
    ),
    # ---------------------------- 玩具 ----------------------------
    make_item(
        "yarn_ball",
        "毛线球",
        "🧶",
        "toy",
        45,
        0.30,
        4,
        12,
        description="没有实际效果，但猫娘玩得很开心。可用来交易。",
    ),
    make_item(
        "teaser_wand",
        "逗猫棒",
        "🪄",
        "toy",
        70,
        0.30,
        3,
        10,
        description="挥一挥就会扑上来的神奇棍子。可用来交易。",
    ),
    make_item(
        "bell",
        "铃铛",
        "🔔",
        "toy",
        95,
        0.35,
        2,
        6,
        description="戴上就再也藏不住行踪了。可用来交易。",
    ),
]


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------

TIER_LABELS: dict[str, str] = {"easy": "轻松", "normal": "普通", "hard": "困难"}

#: 任务模板池。生成每日任务时按 tier 权重抽取。
TASK_POOL: list[dict[str, object]] = [
    # ---------------------------- 轻松 ----------------------------
    {
        "key": "sunbath",
        "name": "晒太阳",
        "emoji": "☀️",
        "tier": "easy",
        "energy": 5,
        "minutes": 5,
        "reward": 12,
        "desc": "在窗台上摊成一张猫饼。",
    },
    {
        "key": "chase_butterfly",
        "name": "追蝴蝶",
        "emoji": "🦋",
        "tier": "easy",
        "energy": 8,
        "minutes": 6,
        "reward": 18,
        "desc": "蝴蝶飞走了，但过程很开心。",
    },
    {
        "key": "grooming",
        "name": "舔毛",
        "emoji": "🧼",
        "tier": "easy",
        "energy": 6,
        "minutes": 5,
        "reward": 15,
        "desc": "把自己打理得干干净净。",
    },
    {
        "key": "nap",
        "name": "打盹",
        "emoji": "😴",
        "tier": "easy",
        "energy": 10,
        "minutes": 10,
        "reward": 24,
        "desc": "睡觉也是正经事。",
    },
    # ---------------------------- 普通 ----------------------------
    {
        "key": "catch_mouse",
        "name": "抓老鼠",
        "emoji": "🐭",
        "tier": "normal",
        "energy": 20,
        "minutes": 10,
        "reward": 50,
        "desc": "仓库里的老鼠最近有点嚣张。",
    },
    {
        "key": "patrol",
        "name": "巡逻领地",
        "emoji": "🗺️",
        "tier": "normal",
        "energy": 18,
        "minutes": 12,
        "reward": 45,
        "desc": "绕着地盘走一圈，宣示主权。",
    },
    {
        "key": "shop_sitting",
        "name": "帮邻居看店",
        "emoji": "🏪",
        "tier": "normal",
        "energy": 25,
        "minutes": 20,
        "reward": 75,
        "desc": "坐在柜台后面，顺便卖个萌。",
    },
    {
        "key": "wash_dishes",
        "name": "洗碗",
        "emoji": "🍽️",
        "tier": "normal",
        "energy": 16,
        "minutes": 12,
        "reward": 38,
        "desc": "碗很多，但报酬实在。",
    },
    {
        "key": "singing",
        "name": "唱歌表演",
        "emoji": "🎤",
        "tier": "normal",
        "energy": 22,
        "minutes": 15,
        "reward": 62,
        "desc": "在街角开一场小型演唱会。",
    },
    # ---------------------------- 困难 ----------------------------
    {
        "key": "fishing",
        "name": "钓鱼",
        "emoji": "🎣",
        "tier": "hard",
        "energy": 30,
        "minutes": 25,
        "reward": 105,
        "desc": "耐心是唯一的诀窍。",
    },
    {
        "key": "herb_gathering",
        "name": "采药",
        "emoji": "🌿",
        "tier": "hard",
        "energy": 35,
        "minutes": 30,
        "reward": 130,
        "desc": "深入后山采集珍贵的草药。",
    },
    {
        "key": "night_watch",
        "name": "夜间守卫",
        "emoji": "🌙",
        "tier": "hard",
        "energy": 40,
        "minutes": 35,
        "reward": 155,
        "desc": "夜里也要保持警惕。",
    },
    {
        "key": "delivery",
        "name": "送快递",
        "emoji": "📦",
        "tier": "hard",
        "energy": 28,
        "minutes": 22,
        "reward": 95,
        "desc": "跑遍整条街，速度快就有奖励。",
    },
    {
        "key": "treasure_hunt",
        "name": "探险寻宝",
        "emoji": "🗝️",
        "tier": "hard",
        "energy": 45,
        "minutes": 40,
        "reward": 190,
        "desc": "听说废墟深处埋着宝物。",
    },
]

#: 抽取每日任务时各 tier 的权重。
TIER_WEIGHTS: dict[str, float] = {"easy": 4.0, "normal": 3.0, "hard": 1.2}

#: 猫娘的名字与称号词库（认养时若未指定名字可随机生成）。
NAME_POOL: list[str] = [
    "小奶油",
    "橘子",
    "雪球",
    "咪咪",
    "布丁",
    "芝麻",
    "汤圆",
    "年糕",
    "麻薯",
    "月牙",
    "团子",
    "可可",
    "抹茶",
    "柚子",
    "桃桃",
    "星星",
    "棉花",
    "泡芙",
]

__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "EFFECT_KEYS",
    "EFFECT_FLAGS",
    "EFFECT_LABELS",
    "empty_effect",
    "make_item",
    "DEFAULT_ITEMS",
    "TIER_LABELS",
    "TASK_POOL",
    "TIER_WEIGHTS",
    "NAME_POOL",
]
