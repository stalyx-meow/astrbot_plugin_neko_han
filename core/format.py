"""把游戏数据渲染成适合在聊天窗口阅读的文本。

所有函数都是纯函数（不访问 AstrBot、不修改状态），方便单测与复用。
"""

from __future__ import annotations

from typing import Any

from .defs import CATEGORY_LABELS
from .items import effect_summary
from .tasks import STATUS_ACTIVE, STATUS_DONE, STATUS_FAILED, STATUS_LABELS
from .util import as_int, format_duration, now_ts

#: 进度条使用的方块
_SOLID = "█"
_EMPTY = "░"


def bar(value: int, maximum: int, width: int = 10) -> str:
    """生成 `████░░░░░░ 80/100` 形式的进度条。"""
    maximum = max(1, maximum)
    value = max(0, min(int(value), maximum))
    filled = int(round(width * value / maximum))
    return f"{_SOLID * filled}{_EMPTY * (width - filled)} {value}/{maximum}"


def progress_bar(fraction: float, width: int = 10) -> str:
    """按比例生成进度条（只有方块，不带数值）。"""
    fraction = max(0.0, min(1.0, float(fraction)))
    filled = int(round(width * fraction))
    return f"{_SOLID * filled}{_EMPTY * (width - filled)}"


#: 任务状态对应的前缀标记（进行中用自己的标记，便于一眼区分）
_STATUS_MARKS: dict[str, str] = {
    STATUS_ACTIVE: "⏳",
    STATUS_DONE: "✅",
    STATUS_FAILED: "❌",
}


def coins(amount: int) -> str:
    """金币文案。"""
    return f"{as_int(amount)} 金币"


def item_line(item: dict[str, Any], *, price: int | None = None, qty: int = 1) -> str:
    """道具的单行描述，例如 `🍚 猫粮 ×3（饱食+30）`。"""
    emoji = item.get("emoji") or "❔"
    name = item.get("name") or item.get("id")
    suffix = f" ×{qty}" if qty != 1 else ""
    price_text = f" · {price} 金币" if price is not None else ""
    effect = effect_summary(item.get("effect") or {})
    return f"{emoji} {name}{suffix}（{effect}{price_text}）"


def catgirl_card(
    snapshot: dict[str, Any],
    limits: dict[str, int],
    *,
    show_id: bool = False,
) -> str:
    """渲染猫娘状态卡片。"""
    name = snapshot.get("name") or "猫娘"
    lines: list[str] = []

    title = f"🐱 {name}"
    if show_id:
        title += f"（{snapshot.get('id')}）"
    lines.append(title)

    if not snapshot.get("alive", True):
        lines.append("　💀 状态：已离世")
        lines.append("　使用「复活草」可以让她重新回到你身边。")
        return "\n".join(lines)

    lines.append(f"　🍚 饱食度 {bar(snapshot.get('satiety', 0), limits['satiety'])}")
    lines.append(f"　💧 水　分 {bar(snapshot.get('hydration', 0), limits['hydration'])}")
    lines.append(f"　❤️ 健康值 {bar(snapshot.get('health', 0), limits['health'])}")
    lines.append(f"　⚡ 精力值 {bar(snapshot.get('energy', 0), limits['energy'])}")

    partner = snapshot.get("partner")
    lines.append(f"　💞 伴侣：{partner}" if partner else "　💞 伴侣：暂无")

    flags = snapshot.get("flags") or []
    if flags:
        lines.append("　⚠️ " + "、".join(flags))

    stats = snapshot.get("stats") or {}
    lines.append(
        f"　📊 完成任务 {as_int(stats.get('tasks_done'), 0)} 次 · "
        f"累计赚取 {as_int(stats.get('coins_earned'), 0)} 金币"
    )
    return "\n".join(lines)


def catgirl_list(
    snapshots: list[dict[str, Any]],
    limits: dict[str, int],
    quota: int | None = None,
) -> str:
    """渲染"我的猫娘"列表。

    `quota` 为可同时养活的猫娘上限；离世的猫娘不占名额，因此上限提示
    只跟存活数量比较。
    """
    alive = sum(1 for snap in snapshots if snap.get("alive", True))
    dead = len(snapshots) - alive

    if not snapshots:
        suffix = f"（上限 {quota} 只）" if quota else ""
        return f"你还没有猫娘哦，发送「/认养 名字」就能领一只回家～{suffix}"

    header = f"🐾 你有 {len(snapshots)} 只猫娘"
    if quota:
        header += f"（存活 {alive}/{quota}"
        if dead:
            header += f"，另有 {dead} 只已离世不占名额"
        header += "）"

    lines = [header + "："]
    for index, snap in enumerate(snapshots, start=1):
        if not snap.get("alive", True):
            lines.append(f"{index}. 💀 {snap.get('name')}（已离世）")
            continue
        flags = snap.get("flags") or []
        flag_text = f" ⚠️{'、'.join(flags)}" if flags else ""
        lines.append(
            f"{index}. 🐱 {snap.get('name')}（{snap.get('id')}）"
            f"　🍚{snap.get('satiety')} 💧{snap.get('hydration')} "
            f"❤️{snap.get('health')} ⚡{snap.get('energy')}{flag_text}"
        )
    if quota and alive >= quota:
        lines.append("⚠️ 存活的猫娘已经到达上限啦。")
    lines.append("发送「/状态 名字」查看详细状态。")
    return "\n".join(lines)


def task_progress_line(slot: dict[str, Any]) -> str:
    """把进行中的任务渲染成一行带进度条与剩余时间的文案。"""
    emoji = slot.get("emoji", "")
    name = slot.get("name")
    started = as_int(slot.get("started_at"), 0)
    finish = as_int(slot.get("finish_at"), 0)
    now = now_ts()
    reward = as_int(slot.get("reward"), 0)

    if finish <= 0:
        return f"　{emoji}「{name}」进行中"
    remaining = finish - now
    if remaining <= 0:
        return f"　{emoji}「{name}」已做完，{reward} 金币马上到账"

    total = max(1, finish - started)
    done = max(0, now - started)
    percent = int(done * 100 / total)
    return (
        f"　{emoji}「{name}」"
        f"{progress_bar(done / total)} {percent}%"
        f"　还剩 {format_duration(remaining)}　完成后 +{reward} 金币"
    )


def task_list(name: str, slots: list[dict[str, Any]], energy: int) -> str:
    """渲染某只猫娘的每日任务列表。

    顶部单独列出"当前正在进行的任务"，让玩家一眼看到进度；
    下面再给出全部任务的明细。
    """
    if not slots:
        return f"{name} 今天没有任务，明天再来看看吧。"

    lines: list[str] = []

    # ① 当前进行中的任务（独立区块，支持并行任务时列出多条）
    active = [slot for slot in slots if slot.get("status") == STATUS_ACTIVE]
    if active:
        lines.append(f"⏳ {name} 当前进行中：")
        lines.extend(task_progress_line(slot) for slot in active)
    else:
        lines.append(f"⏳ {name} 当前没有进行中的任务")

    # ② 全部任务明细
    lines.append("")
    lines.append(f"📋 今天的任务（剩余精力 {energy}）：")
    for slot in slots:
        status = slot.get("status")
        mark = _STATUS_MARKS.get(str(status), "▫️")
        label = STATUS_LABELS.get(str(status), str(status))
        if status == STATUS_ACTIVE:
            remaining = as_int(slot.get("finish_at"), 0) - now_ts()
            label = (
                "已完成，奖励自动发放中"
                if remaining <= 0
                else f"进行中（还剩 {format_duration(remaining)}）"
            )
        elif status == STATUS_DONE:
            label = "已完成，奖励已到账"
        lines.append(
            f"{mark} [{slot.get('slot')}] {slot.get('emoji', '')} {slot.get('name')}"
            f"　消耗精力 {slot.get('energy')}"
            f" · 耗时 {slot.get('minutes')} 分钟"
            f" · 奖励 {slot.get('reward')} 金币"
            f"　[{label}]"
        )
        desc = slot.get("desc")
        if desc:
            lines.append(f"　　{desc}")

    lines.append(
        "发送「/开始 <猫娘> <编号>」开始任务；做完后奖励会**自动发放**，不需要手动领取。"
    )
    return "\n".join(lines)


def task_notice(notices: list[dict[str, Any]]) -> str:
    """渲染"任务已完成、奖励已自动到账"的提示。"""
    if not notices:
        return ""
    total = sum(as_int(notice.get("reward"), 0) for notice in notices)
    lines: list[str] = []
    for notice in notices:
        lines.append(
            f"🎉 {notice.get('catgirl_name')} 完成了"
            f"{notice.get('emoji', '')}「{notice.get('name')}」，"
            f"奖励 {notice.get('reward')} 金币已自动到账"
        )
    if len(notices) > 1:
        lines.append(f"　合计 +{total} 金币")
    return "\n".join(lines)


def shop_list(rows: list[dict[str, Any]], day: str, refresh_hour: int) -> str:
    """渲染官方商城。"""
    if not rows:
        return "今天的商城空空如也，请管理员在面板中配置道具。"
    lines = [f"🏪 官方商城（{day}，每天 {refresh_hour} 点刷新）"]
    for row in rows:
        item = row["item"]
        price = row["price"]
        base = row["base_price"]
        delta = price - base
        if base > 0 and delta:
            pct = delta / base * 100
            trend = f"　📈+{pct:.0f}%" if delta > 0 else f"　📉{pct:.0f}%"
        else:
            trend = ""
        stock = "不限量" if row.get("unlimited") else f"库存 {row.get('stock')}"
        lines.append(
            f"{item.get('emoji', '❔')} {item.get('name')}（{item.get('id')}）"
            f"　{price} 金币{trend}　· {stock}"
        )
        effect = effect_summary(item.get("effect") or {})
        lines.append(f"　　{effect}｜{item.get('description') or '——'}")
    lines.append("发送「neko 购买 <道具名> [数量]」下单。")
    return "\n".join(lines)


def inventory_list(rows: list[dict[str, Any]], owner_name: str) -> str:
    """渲染仓库。"""
    if not rows:
        return (
            f"{owner_name} 的仓库空空如也，去「neko 商城」买点东西吧～"
        )
    lines = [f"🎒 {owner_name} 的仓库（共 {sum(r['qty'] for r in rows)} 件）"]
    for row in rows:
        item = row["item"]
        category = CATEGORY_LABELS.get(str(item.get("category")), "其他")
        lines.append(f"　{item_line(item, qty=row['qty'])}　[{category}]")
    return "\n".join(lines)


def market_list(rows: list[dict[str, Any]], *, title: str = "玩家市场") -> str:
    """渲染玩家市场挂单。"""
    if not rows:
        return "🛒 市场上暂时没有人在卖东西，你可以用「neko 上架」摆个摊。"
    lines = [f"🛒 {title}（共 {len(rows)} 条）"]
    for row in rows:
        listing = row["listing"]
        item = row["item"]
        lines.append(
            f"　[{listing.get('id')}] {item.get('emoji', '❔')} {item.get('name')}"
            f" ×{listing.get('qty')}　单价 {listing.get('unit_price')} 金币"
            f"　卖家：{row.get('seller_name')}"
        )
    lines.append("发送「neko 购买挂单 <编号> [数量]」购买。")
    return "\n".join(lines)


def my_listings(rows: list[dict[str, Any]]) -> str:
    """渲染自己的挂单。"""
    if not rows:
        return "你还没有上架任何商品，发送「neko 上架 <猫娘> <道具> <数量> <单价>」试试。"
    lines = [f"🏷️ 你的在售商品（{len(rows)} 条）"]
    for row in rows:
        listing = row["listing"]
        item = row["item"]
        lines.append(
            f"　[{listing.get('id')}] {item.get('emoji', '❔')} {item.get('name')}"
            f" ×{listing.get('qty')}　单价 {listing.get('unit_price')} 金币"
            f"　（由 {listing.get('catgirl_name')} 看摊）"
        )
    lines.append("发送「neko 下架 <编号>」取回商品。")
    return "\n".join(lines)


def proposal_list(rows: list[dict[str, Any]]) -> str:
    """渲染待处理的求婚请求。"""
    if not rows:
        return "暂时没有人向你的猫娘求婚哦～"
    lines = ["💌 待处理的求婚请求："]
    for proposal in rows:
        lines.append(
            f"　[{proposal.get('id')}] {proposal.get('from_cat_name')}"
            f"（主人：{proposal.get('from_player_name')}）"
            f" → {proposal.get('to_cat_name')}"
        )
    lines.append("发送「neko 同意 <编号>」或「neko 拒绝 <编号>」来回应。")
    return "\n".join(lines)


def error_box(message: str) -> str:
    """统一的错误提示格式。"""
    return f"😿 {message}"


__all__ = [
    "bar",
    "coins",
    "item_line",
    "catgirl_card",
    "catgirl_list",
    "task_list",
    "task_progress_line",
    "progress_bar",
    "task_notice",
    "shop_list",
    "inventory_list",
    "market_list",
    "my_listings",
    "proposal_list",
    "error_box",
]
