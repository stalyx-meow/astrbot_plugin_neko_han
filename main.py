"""Neko_Han —— QQ 机器人猫娘养成插件（AstrBot Star 插件）。

功能总览
--------
- 认养猫娘并命名
- 生存：每天消耗饱食度与水分，长期不喂会生病甚至离世
- 经济：金币作为通用货币
- 任务：消耗精力与时间完成随机任务赚取金币
- 结婚：猫娘之间可以求婚、结婚、离婚
- 商城：官方每日商城（价格波动）+ 玩家自由交易市场
- 面板：WebUI 页面可视化自定义道具与商城

本文件只做"胶水"：把聊天事件翻译成对 `core.NekoWorld` 的调用，
再把结果渲染成消息。全部游戏逻辑位于 `core/`，可脱离 AstrBot 单测。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Callable

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .core import GameError, NekoWorld
from .core import format as fmt
from .core.defs import CATEGORIES, CATEGORY_LABELS, EFFECT_FLAGS, EFFECT_KEYS
from .core.items import effect_summary
from .core.util import as_int

#: 插件数据目录名（与 metadata.yaml 的 name 保持一致）
PLUGIN_NAME = "neko_han"

HELP_TEXT = """🐱 Neko_Han 猫娘养成 · 指令一览

（下面的 `/` 是 AstrBot 的唤醒前缀，如果你改过 `wake_prefix` 请替换成你自己的。
群里必须带前缀或 @机器人；私聊默认可以省略前缀。）

【认养与查看】
  /认养 [名字]        认养一只猫娘（不给名字会随机取）
  /我的               查看自己的猫娘列表
  /状态 [猫娘]        查看详细状态卡片
  /改名 <猫娘> <新名>  给猫娘改名
  /送养 <猫娘>        与猫娘告别（不可恢复）
  /猫娘榜             看看大家都有哪些猫娘

【生存】
  /喂食 <猫娘> <道具>  喂食物（恢复饱食度）
  /喝水 <猫娘> <道具>  喂饮品（恢复水分）
  /使用 <猫娘> <道具>  使用药品/复活草等
  /仓库               查看仓库
  /钱包               查看金币
  /签到               每日签到领金币

【任务】
  /任务 <猫娘>         查看今天的任务
  /开始 <猫娘> <编号>  开始任务（消耗精力，奖励完成后自动到账）

【决斗】
  /决斗 <我的猫娘> <对方猫娘> <赌注>   下战书（赌注立即托管）
  /决斗列表            看待应战的战书、待猜的决斗与进行中的决斗
  /接受决斗 <编号> / /拒绝决斗 <编号>
  /猜 <数字>           提交猜测；有多场时用「/猜 <编号> <数字>」
  /取消决斗 <编号>     撤回自己发出的战书
  （双方各猜一次，谁离目标数字更近谁赢走奖池；建议私聊提交猜测）

【商城与交易】
  /商城               官方商城（每天刷新，价格浮动）
  /购买 <道具> [数量]  从官方商城购买
  /市场 [道具]         浏览玩家市场
  /上架 <猫娘> <道具> <数量> <单价>
  /挂单               查看自己的在售商品
  /买入 <编号> [数量]  购买玩家的挂单
  /下架 <编号>         下架自己的商品

【结婚】
  /求婚 <我的猫娘> <对方猫娘>
  /求婚列表            查看待处理的求婚
  /同意 <编号> / /拒绝 <编号>
  /伴侣 <猫娘>         查看猫娘的伴侣
  /离婚 <猫娘>         离婚（需要金币）

【管理】
  /刷新商城           立即刷新官方商城（仅管理员）
  /发放 <玩家> <金币>  给玩家发放/扣除金币（仅管理员）

举个例子：/认养 小奶油
提示：子指令与参数之间用空格分隔，例如「/喂食 小奶油 猫粮」。
"""

#: 全部合法指令名（主名 + 别名）。用于判断"指令与参数是否粘在一起"。
#: 测试会校验它与实际注册的指令完全一致，因此不会漂移。
_COMMAND_NAMES = frozenset(
    {
        "帮助", "菜单", "猫娘帮助",
        "认养", "adopt", "领养",
        "我的", "list", "列表", "我的猫娘",
        "状态", "status", "查看",
        "改名", "rename",
        "送养", "release", "放生",
        "猫娘榜", "rank", "图鉴",
        "喂食", "feed", "喂",
        "喝水", "drink", "喂水",
        "使用", "use",
        "仓库", "bag", "背包", "道具",
        "钱包", "wallet", "金币", "余额",
        "签到", "daily", "每日", "领取金币",
        "任务", "task", "任务列表",
        "开始", "start", "接任务",
        "商城", "shop", "商店", "官方商城",
        "购买", "buy", "买",
        "市场", "market", "交易市场",
        "上架", "sell", "摆摊",
        "挂单", "mine", "我的挂单", "在售",
        "买入", "buylisting", "买挂单",
        "下架", "cancel", "取消挂单",
        "求婚", "propose", "求亲",
        "求婚列表", "proposals", "求婚请求",
        "同意", "accept",
        "拒绝", "reject",
        "伴侣", "couple", "结婚",
        "离婚", "divorce",
        "决斗", "duel", "下战书",
        "接受决斗", "acceptduel", "应战",
        "拒绝决斗", "rejectduel", "拒战",
        "取消决斗", "cancelduel", "收战书",
        "猜", "guess",
        "决斗列表", "duels", "战书",
        "刷新商城", "refreshshop",
        "发放", "grant",
    }
)

#: 扁平化之前的旧前缀
_OLD_PREFIXES = ("nekohan", "neko", "nh", "猫娘")

_OLD_STYLE_HINT = (
    "💡 指令前面不用再加 neko 啦～ 现在直接写子指令就行：\n"
    "　/帮助\n"
    "　/认养 小奶油\n"
    "　/喂食 小奶油 猫粮\n"
    "发送 /帮助 可以看到全部指令。"
)

_GLUE_HINT = (
    "💡 指令和参数之间要加一个空格哦～\n"
    "　/认养 小奶油\n"
    "　/喂食 小奶油 猫粮\n"
    "　/上架 小奶油 毛线球 2 50\n"
    "发送 /帮助 可以看到全部指令。"
)

#: 粗筛正则：可能需要注意写法的消息（真正判断交给 `hint_for_text`）。
_HINT_CANDIDATE_RE = (
    r"^[/!！]?\s*(?:"
    + "|".join(
        re.escape(name)
        for name in sorted(set(_OLD_PREFIXES) | _COMMAND_NAMES, key=len, reverse=True)
    )
    + r")"
)


def hint_for_text(text: str) -> str | None:
    """针对写错格式的消息返回提示文案；返回 None 表示不需要提示。

    处理两类常见笔误：

    1. 仍在使用旧前缀，例如「neko 认养」「猫娘商城」；
    2. 指令与参数粘在一起，例如「认养小奶油」（少了空格）。

    合法指令一律返回 None，交给正常处理器，避免抢答。
    """
    cleaned = str(text or "").strip().lstrip("/!！").strip()
    if not cleaned:
        return None
    parts = cleaned.split()
    if not parts:
        return None
    head = parts[0]

    # 1) 合法指令：直接放行
    if head in _COMMAND_NAMES:
        return None

    # 2) 旧前缀写法：只有后面确实跟着一个指令名时才提示，
    #    避免对「neko assets」这类普通聊天误报。
    for prefix in _OLD_PREFIXES:
        if not head.startswith(prefix):
            continue
        rest = head[len(prefix):]
        if not rest:
            # 形如「neko 认养」：看下一个词是不是指令名
            if len(parts) > 1 and parts[1] in _COMMAND_NAMES:
                return _OLD_STYLE_HINT
            continue
        # 排除 "nekos" 这类普通英文单词
        if rest[0].isascii() and rest[0].isalpha():
            continue
        # 形如「neko认养」「猫娘商城」「neko认养小奶油」
        if any(rest.startswith(name) for name in _COMMAND_NAMES):
            return _OLD_STYLE_HINT

    # 3) 指令名与参数粘连
    for name in _COMMAND_NAMES:
        if len(head) > len(name) and head.startswith(name):
            return _GLUE_HINT

    return None



class NekoHanPlugin(Star):
    """Neko_Han 猫娘养成插件：认养、喂养、任务、结婚与商城。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.world: NekoWorld | None = None
        self._tick_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self.data_dir = self._resolve_data_dir()

        # 面板后端 API（路由需以插件名为前缀）
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/items",
            self.api_items,
            ["GET"],
            "获取道具定义列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/items/save",
            self.api_item_save,
            ["POST"],
            "新增或更新道具",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/items/delete",
            self.api_item_delete,
            ["POST"],
            "删除道具",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/items/reset",
            self.api_items_reset,
            ["POST"],
            "恢复默认道具表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/shop",
            self.api_shop,
            ["GET"],
            "获取官方商城",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/shop/refresh",
            self.api_shop_refresh,
            ["POST"],
            "刷新官方商城",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/overview",
            self.api_overview,
            ["GET"],
            "获取运行概览",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/players/grant",
            self.api_players_grant,
            ["POST"],
            "给玩家发放金币",
        )

    # ==================================================================
    # 生命周期
    # ==================================================================
    def _resolve_data_dir(self) -> Path:
        """确定插件数据目录（`data/plugin_data/neko_han`）。"""
        try:
            return StarTools.get_data_dir(PLUGIN_NAME)
        except Exception as exc:  # pragma: no cover - 取决于运行环境
            logger.warning("Neko_Han: 无法通过 StarTools 获取数据目录(%s)，改用默认路径", exc)
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            path = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
            path.mkdir(parents=True, exist_ok=True)
            return path

    async def initialize(self) -> None:
        """插件启用时：载入存档并启动每日结算循环。"""
        self.world = NekoWorld(self.data_dir, self.config)
        self.world.load()
        self.world.save_now()
        logger.info(
            "Neko_Han: 已载入 %d 位玩家、%d 只猫娘、%d 种道具",
            len(self.world.store.players),
            len(self.world.store.catgirls),
            len(self.world.registry.all(include_disabled=True)),
        )
        self._stop_event = asyncio.Event()
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def terminate(self) -> None:
        """插件卸载/重载时：停止后台任务并落盘。"""
        self._stop_event.set()
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._tick_task = None
        if self.world is not None:
            await self.world.save()

    async def _tick_loop(self) -> None:
        """后台循环：按配置间隔执行每日结算并推送通知。"""
        while not self._stop_event.is_set():
            interval = max(1, self._cfg_int("display.tick_interval_minutes", 10)) * 60
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return  # 收到停止信号
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                return

            if self.world is None:
                continue
            try:
                result = self.world.tick()
                await self.world.save()
                await self._notify_tick(result)
            except Exception as exc:  # noqa: BLE001 - 后台任务绝不能崩
                logger.exception("Neko_Han: 定时结算失败: %s", exc)

    async def _notify_tick(self, result: dict[str, Any]) -> None:
        """把定时结算产生的死亡与任务完成事件主动通知给主人。"""
        if self.world is None:
            return
        await self._notify_deaths(result.get("deaths") or [])
        await self._notify_task_done(result.get("task_done") or [])
        await self._notify_duels(result.get("duels") or [])

    async def _notify_deaths(self, deaths: list[dict[str, Any]]) -> None:
        """通知猫娘离世。"""
        if not deaths or not self._cfg_bool("display.notify_on_death", True):
            return
        for event in deaths:
            umo = self._player_umo(event.get("owner"))
            if not umo:
                continue
            text = (
                f"😿 你的猫娘「{event.get('catgirl_name')}」"
                f"因为{event.get('reason')}离开了……\n"
                f"如果还想见到她，可以使用「复活草」。"
            )
            await self._push(umo, text, "死亡通知")

    async def _notify_task_done(self, events: list[dict[str, Any]]) -> None:
        """通知"任务已完成、奖励已自动到账"（按主人合并成一条，避免刷屏）。"""
        if not events or not self._cfg_bool("display.notify_on_task_done", True):
            return
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            key = str(event.get("owner") or "")
            if key:
                grouped.setdefault(key, []).append(event)
        for owner, items in grouped.items():
            umo = self._player_umo(owner)
            if not umo:
                continue
            total = sum(as_int(item.get("reward"), 0) for item in items)
            lines = [
                f"🎉 {item.get('catgirl_name')} 完成了"
                f"{item.get('emoji', '')}「{item.get('name')}」，奖励已自动到账"
                for item in items
            ]
            lines.append(f"　本次共 +{total} 金币")
            await self._push(umo, "\n".join(lines), "任务完成通知")

    async def _notify_duels(self, events: list[dict[str, Any]]) -> None:
        """通知决斗超时/作废（赌注已退回）。"""
        if not events:
            return
        seen: set[tuple[str, str]] = set()
        for event in events:
            owner = str(event.get("owner") or "")
            message = str(event.get("message") or "")
            key = (owner, str(event.get("duel_id") or ""))
            if not owner or not message or key in seen:
                continue
            seen.add(key)
            umo = self._player_umo(owner)
            if umo:
                await self._push(umo, message, "决斗超时通知")

    def _player_umo(self, owner: Any) -> str:
        """取玩家最后一次发言的会话标识（用于主动推送）。"""
        player = self.world.get_player(str(owner or "")) if self.world else None
        return str((player or {}).get("umo") or "")

    async def _push(self, umo: str, text: str, kind: str) -> None:
        """发送一条主动消息，失败只记日志，不影响后台循环。"""
        try:
            await self.context.send_message(umo, MessageChain().message(text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neko_Han: %s 发送失败: %s", kind, exc)

    # ==================================================================
    # 通用辅助
    # ==================================================================
    def _cfg_str(self, key: str, default: str) -> str:
        if isinstance(self.config, dict):
            section, _, name = key.partition(".")
            raw = self.config.get(section)
            if isinstance(raw, dict) and raw.get(name) is not None:
                return str(raw[name])
        return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        if isinstance(self.config, dict):
            section, _, name = key.partition(".")
            raw = self.config.get(section)
            if isinstance(raw, dict) and name in raw:
                value = raw[name]
                if isinstance(value, bool):
                    return value
                if isinstance(value, (int, float)):
                    return bool(value)
                if isinstance(value, str):
                    return value.strip().lower() in {"true", "1", "yes", "on"}
        return default

    def _cfg_int(self, key: str, default: int) -> int:
        if isinstance(self.config, dict):
            section, _, name = key.partition(".")
            raw = self.config.get(section)
            if isinstance(raw, dict) and raw.get(name) is not None:
                try:
                    return int(raw[name])
                except (TypeError, ValueError):
                    return default
        return default

    def _world(self) -> NekoWorld:
        """获取已初始化的游戏世界，未就绪时抛出 `GameError`。"""
        if self.world is None:
            raise GameError("插件还在启动中，请稍后再试～")
        return self.world

    def _player(self, event: AstrMessageEvent) -> dict[str, Any]:
        """获取（或创建）当前发言者的玩家档案。"""
        world = self._world()
        platform = event.get_platform_name() or "unknown"
        player, _created = world.ensure_player(
            platform,
            str(event.get_sender_id() or "unknown"),
            event.get_sender_name() or "",
            event.unified_msg_origin or "",
        )
        return player

    def _limits(self) -> dict[str, int]:
        return self._world().cfg.limits()

    def _show_id(self) -> bool:
        return self._cfg_bool("display.show_catgirl_id", False)

    def _catgirl_quota(self) -> int:
        """每名玩家可同时养活的猫娘数量（离世的不占名额）。"""
        return self._world().catgirl_quota()

    def _run(self, action: Callable[[], Any]) -> tuple[bool, Any]:
        """执行一个同步游戏操作，把异常转成可回复的文案。

        返回 `(ok, payload)`：成功时 `payload` 是 `action()` 的返回值，
        失败时 `payload` 是可以直接发给玩家的错误文案。
        """
        try:
            return True, action()
        except GameError as exc:
            return False, fmt.error_box(exc.message)
        except Exception as exc:  # noqa: BLE001 - 单个命令不应该拖垮插件
            logger.exception("Neko_Han: 指令执行失败: %s", exc)
            return False, fmt.error_box("操作出了点问题，请联系管理员查看日志。")

    async def _reply(self, event: AstrMessageEvent, text: str):
        """渲染回复；配置为图片模式时优先转图，失败自动回退文本。"""
        if self._cfg_str("display.card_mode", "text") == "image":
            try:
                url = await self.text_to_image(text)
                if url:
                    return event.image_result(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Neko_Han: 文转图失败，回退为文本: %s", exc)
        return event.plain_result(text)

    async def _persist(self) -> None:
        """命令结束后落盘。"""
        if self.world is not None:
            await self.world.save()

    def _task_notices(self, catgirls: Any) -> str:
        """取出这些猫娘的"任务奖励已自动到账"通知并清空队列。"""
        world = self._world()
        collected: list[dict[str, Any]] = []
        for catgirl in catgirls:
            collected.extend(world.drain_task_notices(catgirl))
        return fmt.task_notice(collected)

    def _cat_and_item(
        self, player: dict[str, Any], cat_query: str, item_query: str
    ) -> tuple[dict[str, Any], str]:
        """解析"猫娘 + 道具"参数对，允许省略猫娘名。

        - `/喂食 小奶油 猫粮` → (小奶油, 猫粮)
        - `/喂食 猫粮` → 若只有一只猫娘则自动选中她
        """
        world = self._world()
        if item_query:
            cat = self._run(lambda: world.resolve_catgirl(player, cat_query))
            if not cat[0]:
                raise GameError(str(cat[1]).replace("😿 ", ""))
            return cat[1], item_query

        if not cat_query:
            raise GameError(
                "请告诉我要对哪只猫娘做什么，例如「/喂食 小奶油 猫粮」。"
            )

        resolved = self._run(lambda: world.resolve_catgirl(player, cat_query))
        if resolved[0]:
            catgirl = resolved[1]
            raise GameError(
                f"要喂 {catgirl.get('name')} 什么呢？"
                f"例如「/喂食 {catgirl.get('name')} 猫粮」。"
            )

        alive = [c for c in world.catgirls_of(player) if c.get("alive", True)]
        if len(alive) == 1:
            return alive[0], cat_query
        if not alive:
            raise GameError("你没有活着的猫娘……先认养一只吧。")
        names = "、".join(str(c.get("name")) for c in alive)
        raise GameError(f"你有好几只猫娘（{names}），请指明是哪一只。")

    # ==================================================================
    # 指令组
    # ==================================================================
    # ---------------------------- 帮助 ----------------------------
    @filter.command("帮助", alias={"菜单", "猫娘帮助"})
    async def neko_help(self, event: AstrMessageEvent):
        """查看 Neko_Han 的全部指令与玩法说明"""
        yield await self._reply(event, HELP_TEXT)

    @filter.regex(_HINT_CANDIDATE_RE)
    async def neko_usage_hint(self, event: AstrMessageEvent):
        """对写错指令格式的用户给出提示（兜底，避免"发了没反应"）

        处理两种情况：仍带着旧的 neko/猫娘 前缀，或指令与参数粘在一起。
        合法指令一律放行（`hint_for_text` 返回 None），不抢答。
        正则过滤器不受唤醒前缀限制，因此没 @机器人 时也能提示到。
        """
        hint = hint_for_text(event.message_str or "")
        if hint:
            yield event.plain_result(hint)

    # ---------------------------- 认养 ----------------------------
    @filter.command("认养", alias={"adopt", "领养"})
    async def neko_adopt(self, event: AstrMessageEvent, name: str = ""):
        """认养一只猫娘并给她起名字，例如「/认养 小奶油」"""
        try:
            player = self._player(event)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        # 未提供名字时进入会话模式，引导玩家输入
        if not name:
            slots = self._world().catgirl_slots(player)
            if slots["remaining"] <= 0:
                yield event.plain_result(
                    fmt.error_box(
                        f"你已经有 {slots['alive']} 只活着的猫娘啦"
                        f"（存活 {slots['alive']}/{slots['quota']}）。"
                        f"想再养新的，先用「/送养」与其中一只告别吧。"
                    )
                )
                return

            yield event.plain_result(
                "🐱 想给你的新猫娘起什么名字呢？（直接发送名字即可，60 秒内有效）"
            )

            @session_waiter(timeout=60)
            async def name_waiter(controller: SessionController, inner: AstrMessageEvent):
                chosen = (inner.message_str or "").strip()
                if chosen in {"取消", "cancel", "退出"}:
                    await inner.send(inner.plain_result("好的，那就不认养了～"))
                    controller.stop()
                    return
                ok, result = self._run(lambda: self._world().adopt(player, chosen))
                if not ok:
                    await inner.send(inner.plain_result(str(result)))
                    controller.keep(timeout=60, reset_timeout=True)
                    return
                self.world.save_now()  # type: ignore[union-attr]
                await inner.send(
                    inner.plain_result(self._adopt_text(result, player))
                )
                controller.stop()

            try:
                await name_waiter(event)
            except TimeoutError:
                yield event.plain_result("⏰ 等太久啦，下次再认养吧～")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Neko_Han: 认养会话失败: %s", exc)
                yield event.plain_result(fmt.error_box("认养过程出错了，请重试。"))
            finally:
                event.stop_event()
            return

        ok, result = self._run(lambda: self._world().adopt(player, name))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        yield await self._reply(event, self._adopt_text(result, player))

    def _adopt_text(self, catgirl: dict[str, Any], player: dict[str, Any]) -> str:
        """认养成功的文案。"""
        slots = self._world().catgirl_slots(player)
        dead_note = (
            f"（另有 {slots['dead']} 只已离世，不占名额）" if slots["dead"] else ""
        )
        return (
            f"🎉 恭喜！一只叫「{catgirl['name']}」的猫娘来到了你身边～\n"
            f"　编号：{catgirl['id']}　金币：{player.get('coins')}\n"
            f"　猫娘：{slots['alive']}/{slots['quota']} 只存活{dead_note}\n"
            f"她每天都会饿、会渴，记得用「/喂食 {catgirl['name']} 猫粮」"
            f"和「/喝水 {catgirl['name']} 清水」照顾她。\n"
            f"想赚金币的话，试试「/任务 {catgirl['name']}」。"
        )

    # ---------------------------- 查看 ----------------------------
    @filter.command("我的", alias={"list", "列表", "我的猫娘"})
    async def neko_list(self, event: AstrMessageEvent):
        """查看自己名下所有猫娘的状态概览"""
        try:
            player = self._player(event)
            world = self._world()
            cats = world.catgirls_of(player)
            snaps = [world.catgirl_snapshot(cat) for cat in cats]
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        text = fmt.catgirl_list(snaps, self._limits(), quota=self._catgirl_quota())
        notice = self._task_notices(cats)
        if notice:
            text = notice + "\n\n" + text
        yield await self._reply(event, text)

    @filter.command("状态", alias={"status", "查看"})
    async def neko_status(self, event: AstrMessageEvent, cat: str = ""):
        """查看某只猫娘的详细状态，例如「/状态 小奶油」"""
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
            snap = world.catgirl_snapshot(catgirl)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        text = fmt.catgirl_card(snap, self._limits(), show_id=self._show_id())
        notice = self._task_notices([catgirl])
        if notice:
            text = notice + "\n\n" + text
        yield await self._reply(event, text)

    @filter.command("改名", alias={"rename"})
    async def neko_rename(self, event: AstrMessageEvent, cat: str = "", new_name: str = ""):
        """给猫娘改名，例如「/改名 小奶油 大福」"""
        if not new_name:
            yield event.plain_result(
                fmt.error_box("请这样使用：「/改名 <猫娘> <新名字>」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
            old_name = catgirl.get("name")
            world.rename(player, catgirl, new_name)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        await self._persist()
        yield event.plain_result(f"✅ 改名成功：「{old_name}」→「{catgirl['name']}」")

    @filter.command("送养", alias={"release", "放生"})
    async def neko_release(self, event: AstrMessageEvent, cat: str = ""):
        """与猫娘告别（会永久删除该猫娘，请谨慎使用）"""
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
            name = catgirl.get("name")
            world.release_catgirl(player, catgirl)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        await self._persist()
        yield event.plain_result(
            f"👋 「{name}」已经离开了。希望她能在别处遇到好人家。"
        )

    @filter.command("猫娘榜", alias={"rank", "图鉴"})
    async def neko_rank(self, event: AstrMessageEvent):
        """看看这个世界上都有哪些猫娘"""
        try:
            world = self._world()
            world.settle_all()
            cats = sorted(
                world.all_catgirls(),
                key=lambda c: (not c.get("alive", True), str(c.get("name"))),
            )
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        if not cats:
            yield event.plain_result("世界上还没有猫娘，快用「/认养」成为第一个吧！")
            return

        lines = [f"🌍 共 {len(cats)} 只猫娘："]
        for cat in cats[:40]:
            owner = world.get_player(str(cat.get("owner") or "")) or {}
            status = "🐱" if cat.get("alive", True) else "💀"
            lines.append(
                f"　{status} {cat.get('name')}（{cat.get('id')}）"
                f"　主人：{owner.get('name') or cat.get('owner')}"
            )
        if len(cats) > 40:
            lines.append(f"　…… 还有 {len(cats) - 40} 只")
        lines.append("想求婚的话：/求婚 <我的猫娘> <对方猫娘>")
        yield await self._reply(event, "\n".join(lines))

    # ---------------------------- 生存 ----------------------------
    @filter.command("喂食", alias={"feed", "喂"})
    async def neko_feed(self, event: AstrMessageEvent, cat: str = "", item: str = ""):
        """给猫娘喂食物，例如「/喂食 小奶油 猫粮」"""
        yield await self._do_use(event, cat, item, mode="feed")

    @filter.command("喝水", alias={"drink", "喂水"})
    async def neko_drink(self, event: AstrMessageEvent, cat: str = "", item: str = ""):
        """给猫娘喝水或饮料，例如「/喝水 小奶油 清水」"""
        yield await self._do_use(event, cat, item, mode="drink")

    @filter.command("使用", alias={"use"})
    async def neko_use(self, event: AstrMessageEvent, cat: str = "", item: str = ""):
        """对猫娘使用道具（药品、复活草等），例如「/使用 小奶油 医疗包」"""
        yield await self._do_use(event, cat, item, mode="use")

    async def _do_use(
        self, event: AstrMessageEvent, cat: str, item: str, *, mode: str
    ):
        """喂食 / 喝水 / 使用道具的公共实现。

        这是一个普通协程（不是异步生成器），返回一个可直接 yield 的消息结果，
        因此调用方写成 `yield await self._do_use(...)`。
        """
        try:
            player = self._player(event)
            world = self._world()
            catgirl, item_query = self._cat_and_item(player, cat, item)

            def action():
                if mode == "feed":
                    return world.feed_catgirl(player, catgirl, item_query)
                if mode == "drink":
                    return world.feed_catgirl(player, catgirl, item_query, drink=True)
                return world.use_item(player, catgirl, item_query)

            ok, result = self._run(action)
        except GameError as exc:
            return event.plain_result(fmt.error_box(exc.message))

        if not ok:
            return event.plain_result(str(result))

        await self._persist()
        applied = result.get("applied") or {}
        labels = {
            "satiety": "饱食度",
            "hydration": "水分",
            "energy": "精力",
            "health": "健康值",
        }
        detail = "、".join(
            f"{labels.get(k, k)} {'+' if v > 0 else ''}{v}"
            for k, v in applied.items()
            if v
        )
        verb = {"feed": "吃掉了", "drink": "喝掉了", "use": "使用了"}[mode]
        text = (
            f"✨ {catgirl.get('name')} {verb} {result['item'].get('emoji', '')}"
            f"{result['item']['name']}"
        )
        text += f"，{detail}。" if detail else "，不过好像没什么变化……"
        if result.get("revived"):
            text += f"\n💫 {catgirl.get('name')} 重新睁开了眼睛！"
        return await self._reply(event, text)

    @filter.command("仓库", alias={"bag", "背包", "道具"})
    async def neko_bag(self, event: AstrMessageEvent):
        """查看自己的道具仓库"""
        try:
            player = self._player(event)
            rows = self._world().inventory_view(player)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        yield await self._reply(
            event, fmt.inventory_list(rows, player.get("name") or "你")
        )

    @filter.command("钱包", alias={"wallet", "金币", "余额"})
    async def neko_wallet(self, event: AstrMessageEvent):
        """查看自己的金币数量"""
        try:
            player = self._player(event)
            world = self._world()
            total = world.inventory_total(player)
            slots = world.catgirl_slots(player)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        yield event.plain_result(
            f"💰 {player.get('name') or '你'} 的钱包\n"
            f"　金币：{player.get('coins')}\n"
            f"　仓库：{total} 件道具\n"
            f"　猫娘：{slots['alive']}/{slots['quota']} 只存活"
            + (f"（另有 {slots['dead']} 只已离世）" if slots["dead"] else "")
        )

    @filter.command("签到", alias={"daily", "每日", "领取金币"})
    async def neko_daily(self, event: AstrMessageEvent):
        """每日签到领取金币"""
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        ok, result = self._run(lambda: world.claim_daily(player))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        yield event.plain_result(
            f"📅 签到成功！获得 {result} 金币，当前余额 {player.get('coins')} 金币。"
        )

    # ---------------------------- 任务 ----------------------------
    @filter.command("任务", alias={"task", "任务列表"})
    async def neko_tasks(self, event: AstrMessageEvent, cat: str = ""):
        """查看猫娘今天的任务列表，例如「/任务 小奶油」"""
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
            slots = world.task_slots(catgirl)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        await self._persist()
        text = fmt.task_list(
            str(catgirl.get("name")), slots, as_int(catgirl.get("energy"), 0)
        )
        notice = self._task_notices([catgirl])
        if notice:
            text = notice + "\n\n" + text
        yield await self._reply(event, text)

    @filter.command("开始", alias={"start", "接任务"})
    async def neko_start(self, event: AstrMessageEvent, cat: str = "", slot: int = 0):
        """开始一个任务，例如「/开始 小奶油 1」"""
        if slot <= 0:
            yield event.plain_result(
                fmt.error_box("请指定任务编号，例如「/开始 小奶油 1」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        # 先把之前已完成的任务结算掉，让玩家看到到账
        notice = fmt.task_notice(world.drain_task_notices(catgirl))

        ok, result = self._run(lambda: world.start_task(catgirl, slot))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        text = (
            f"🏃 {catgirl.get('name')} 开始「{result['name']}」！\n"
            f"　消耗精力 {result['energy']}　预计耗时 {result['minutes']} 分钟\n"
            f"　{result['reward']} 金币会在任务完成后**自动到账**，不需要手动领取。"
        )
        if notice:
            text = notice + "\n\n" + text
        yield await self._reply(event, text)

    # ---------------------------- 商城 ----------------------------
    @filter.command("商城", alias={"shop", "商店", "官方商城"})
    async def neko_shop(self, event: AstrMessageEvent):
        """查看官方商城（每天刷新，价格有浮动）"""
        try:
            world = self._world()
            rows = world.shop_view()
            shop = world.ensure_official_shop()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        await self._persist()
        yield await self._reply(
            event,
            fmt.shop_list(
                rows, str(shop.get("date") or ""), world.cfg.day_reset_hour
            ),
        )

    @filter.command("购买", alias={"buy", "买"})
    async def neko_buy(self, event: AstrMessageEvent, item: str = "", qty: int = 1):
        """从官方商城购买道具，例如「/购买 猫粮 2」"""
        if not item:
            yield event.plain_result(
                fmt.error_box("请指定要买的道具，例如「/购买 猫粮 2」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.buy_official(player, item, qty))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        stock_text = (
            "不限量"
            if result["remaining_stock"] < 0
            else f"剩余库存 {result['remaining_stock']}"
        )
        yield event.plain_result(
            f"🛍️ 购买成功！{result['item'].get('emoji', '')}"
            f"{result['item']['name']} ×{result['qty']}\n"
            f"　花费 {result['total']} 金币（单价 {result['unit_price']}），"
            f"余额 {player.get('coins')} 金币\n"
            f"　该商品今日{stock_text}"
        )

    # ---------------------------- 玩家市场 ----------------------------
    @filter.command("市场", alias={"market", "交易市场"})
    async def neko_market(self, event: AstrMessageEvent, item: str = ""):
        """浏览玩家市场在售的道具"""
        try:
            player = self._player(event)
            rows = self._world().market_listings(
                exclude_player=player["key"], item_query=item or None
            )
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        title = "玩家市场" + (f"（筛选：{item}）" if item else "")
        yield await self._reply(event, fmt.market_list(rows, title=title))

    @filter.command("上架", alias={"sell", "摆摊"})
    async def neko_sell(
        self,
        event: AstrMessageEvent,
        cat: str = "",
        item: str = "",
        qty: int = 0,
        price: int = 0,
    ):
        """把道具挂到市场出售，例如「/上架 小奶油 毛线球 2 50」"""
        if not item or qty <= 0 or price <= 0:
            yield event.plain_result(
                fmt.error_box(
                    "用法：「/上架 <猫娘> <道具> <数量> <单价>」，"
                    "例如「/上架 小奶油 毛线球 2 50」。"
                )
            )
            return
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(
            lambda: world.sell_listing(player, catgirl, item, qty, price)
        )
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        listing = result["listing"]
        yield event.plain_result(
            f"🏷️ 上架成功！{result['item'].get('emoji', '')}"
            f"{result['item']['name']} ×{listing['qty']}，"
            f"单价 {listing['unit_price']} 金币（编号 {listing['id']}）。\n"
            f"　由 {catgirl.get('name')} 负责看摊。"
        )

    @filter.command("挂单", alias={"mine", "我的挂单", "在售"})
    async def neko_mine(self, event: AstrMessageEvent):
        """查看自己正在出售的商品"""
        try:
            player = self._player(event)
            rows = self._world().my_listings(player)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        yield await self._reply(event, fmt.my_listings(rows))

    @filter.command("买入", alias={"buylisting", "买挂单"})
    async def neko_buy_listing(
        self, event: AstrMessageEvent, listing_id: str = "", qty: int = 1
    ):
        """购买玩家市场的挂单，例如「/买入 L1 2」"""
        if not listing_id:
            yield event.plain_result(
                fmt.error_box("请指定挂单编号，例如「/买入 L1」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.buy_listing(player, listing_id, qty))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        fee_text = f"（含手续费 {result['fee']} 金币）" if result["fee"] else ""
        yield event.plain_result(
            f"🤝 成交！{result['item'].get('emoji', '')}{result['item']['name']}"
            f" ×{result['qty']}\n"
            f"　支付 {result['total']} 金币{fee_text}，余额 {player.get('coins')} 金币\n"
            f"　卖家 {result['seller_name']} 收入 {result['seller_income']} 金币"
        )

    @filter.command("下架", alias={"cancel", "取消挂单"})
    async def neko_cancel(self, event: AstrMessageEvent, listing_id: str = ""):
        """下架自己的商品并取回道具，例如「/下架 L1」"""
        if not listing_id:
            yield event.plain_result(
                fmt.error_box("请指定要下架的编号，例如「/下架 L1」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.cancel_listing(player, listing_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        item = result.get("item") or {}
        yield event.plain_result(
            f"📦 已下架 {item.get('emoji', '')}{item.get('name', '商品')}"
            f" ×{result['qty']}，道具已退回仓库。"
        )

    # ---------------------------- 决斗 ----------------------------
    @filter.command("决斗", alias={"duel", "下战书"})
    async def neko_duel(
        self,
        event: AstrMessageEvent,
        my_cat: str = "",
        target: str = "",
        bet: int = 0,
    ):
        """向别人的猫娘下战书，例如「/决斗 小奶油 橘子 100」"""
        if not target or bet <= 0:
            low, high = self._world()._duel_limits()
            yield event.plain_result(
                fmt.error_box(
                    "用法：「/决斗 <我的猫娘> <对方猫娘> <赌注>」，"
                    f"例如「/决斗 小奶油 橘子 100」。\n"
                    f"　赌注范围 {low} ~ {high} 金币，"
                    f"目标数字范围 {self._world().duel_range_text()}。"
                )
            )
            return
        try:
            player = self._player(event)
            world = self._world()
            mine = world.resolve_catgirl(player, my_cat)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.invite_duel(player, mine, target, bet))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        duel, target_cat = result

        # 主动通知对方主人
        if self._cfg_bool("display.notify_on_duel", True):
            umo = self._player_umo(target_cat.get("owner"))
            if umo and umo != (event.unified_msg_origin or ""):
                await self._push(
                    umo,
                    f"⚔️ {duel.get('challenger_name')} 的猫娘"
                    f"「{mine.get('name')}」向你的猫娘「{target_cat.get('name')}」"
                    f"下了战书，赌注 {bet} 金币！\n"
                    f"用「/接受决斗 {duel['id']}」应战，"
                    f"「/拒绝决斗 {duel['id']}」拒绝。",
                    "决斗邀请通知",
                )

        yield await self._reply(
            event,
            f"⚔️ 战书已送出！「{mine.get('name')}」 → "
            f"「{target_cat.get('name')}」（编号 {duel['id']}）\n"
            f"　赌注 {bet} 金币已托管，目标数字范围 "
            f"{world.duel_range_text()}\n"
            f"　等对方主人用「/接受决斗 {duel['id']}」应战。",
        )

    @filter.command("接受决斗", alias={"acceptduel", "应战"})
    async def neko_accept_duel(self, event: AstrMessageEvent, duel_id: str = ""):
        """接受决斗，例如「/接受决斗 D1」"""
        if not duel_id:
            yield event.plain_result(fmt.error_box("请指定决斗编号，例如「/接受决斗 D1」。"))
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.accept_duel(player, duel_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        duel = result
        yield await self._reply(
            event,
            f"⚔️ 决斗开始！{duel.get('challenger_cat_name')} vs "
            f"{duel.get('opponent_cat_name')}　奖池 {duel.get('pot')} 金币\n"
            f"　目标数字范围 {world.duel_range_text()}，双方各猜一次，"
            f"猜得更接近的人赢。\n"
            f"　👉 请两位主人发送「/猜 <数字>」提交猜测。\n"
            f"　⚠️ **建议私聊机器人提交**，在群里发会被对手看到。",
        )

    @filter.command("拒绝决斗", alias={"rejectduel", "拒战"})
    async def neko_reject_duel(self, event: AstrMessageEvent, duel_id: str = ""):
        """拒绝决斗并退回对方赌注，例如「/拒绝决斗 D1」"""
        if not duel_id:
            yield event.plain_result(fmt.error_box("请指定决斗编号，例如「/拒绝决斗 D1」。"))
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.decline_duel(player, duel_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        yield event.plain_result(
            f"🛡️ 已拒绝「{result.get('challenger_cat_name')}」的战书，"
            f"{result.get('bet')} 金币赌注已退还对方。"
        )

    @filter.command("取消决斗", alias={"cancelduel", "收战书"})
    async def neko_cancel_duel(self, event: AstrMessageEvent, duel_id: str = ""):
        """撤回自己发出、还没被接受的战书，例如「/取消决斗 D1」"""
        if not duel_id:
            yield event.plain_result(fmt.error_box("请指定决斗编号，例如「/取消决斗 D1」。"))
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.cancel_duel(player, duel_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        yield event.plain_result(
            f"📥 已撤回战书（{result.get('id')}），"
            f"{result.get('bet')} 金币赌注已退回你的钱包。"
        )

    @filter.command("猜", alias={"guess"})
    async def neko_guess(self, event: AstrMessageEvent, first: str = "", second: int = 0):
        """提交决斗猜测，例如「/猜 42」或「/猜 D1 42」"""
        if second:
            duel_id, value = first, second
        elif first.isdigit():
            duel_id, value = "", int(first)
        else:
            yield event.plain_result(
                fmt.error_box(
                    "用法：「/猜 <数字>」，同时有多场时用「/猜 <编号> <数字>」，"
                    "例如「/猜 42」或「/猜 D1 42」。"
                )
            )
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.submit_guess(player, value, duel_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        duel = result

        if duel.get("status") == "finished":
            # 双方都猜完：揭晓
            snapshot = world.duel_snapshot(duel, player["key"])
            yield await self._reply(event, fmt.duel_result(snapshot))
            return

        # 只有一方猜完：绝不回显数字，避免泄露给对手
        yield event.plain_result(
            f"✅ 已收到你对决斗 {duel.get('id')} 的猜测（不会公开显示）。\n"
            f"　等对手也猜完后一起揭晓。"
        )

    @filter.command("决斗列表", alias={"duels", "战书"})
    async def neko_duel_list(self, event: AstrMessageEvent):
        """查看待应战的战书、待猜测的决斗与进行中的决斗"""
        try:
            player = self._player(event)
            world = self._world()
            world.expire_duels()
            incoming = world.duels_to_accept(player["key"])
            awaiting = world.duels_awaiting_guess(player["key"])
            playing = [
                world.duel_snapshot(duel, player["key"])
                for duel in world.duels_of_player(player["key"])
                if duel.get("status") == "guessing"
            ]
            outgoing = [
                duel
                for duel in world.duels_of_player(player["key"])
                if duel.get("status") == "pending"
                and duel.get("challenger") == player["key"]
            ]
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        await self._persist()
        yield await self._reply(
            event,
            fmt.duel_list(
                incoming, outgoing, awaiting, playing, world.duel_range_text()
            ),
        )

    # ---------------------------- 结婚 ----------------------------
    @filter.command("求婚", alias={"propose", "求亲"})
    async def neko_propose(
        self, event: AstrMessageEvent, my_cat: str = "", target: str = ""
    ):
        """向别人的猫娘求婚，例如「/求婚 小奶油 橘子」"""
        if not target:
            yield event.plain_result(
                fmt.error_box(
                    "用法：「/求婚 <我的猫娘> <对方猫娘>」，"
                    "可以用「/猫娘榜」查看大家的猫娘。"
                )
            )
            return
        try:
            player = self._player(event)
            world = self._world()
            mine = world.resolve_catgirl(player, my_cat)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.propose(player, mine, target))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        proposal, target_cat = result

        # 主动通知对方主人
        if self._cfg_bool("display.notify_on_proposal", True):
            target_player = world.get_player(str(target_cat.get("owner") or ""))
            umo = str((target_player or {}).get("umo") or "")
            if umo and umo != (event.unified_msg_origin or ""):
                try:
                    await self.context.send_message(
                        umo,
                        MessageChain().message(
                            f"💌 {player.get('name')} 的猫娘「{mine.get('name')}」"
                            f"向你的猫娘「{target_cat.get('name')}」求婚了！\n"
                            f"用「/求婚列表」查看，"
                            f"「/同意 {proposal['id']}」答应她。"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Neko_Han: 求婚通知发送失败: %s", exc)

        yield await self._reply(
            event,
            f"💌 求婚已送出！「{mine.get('name')}」 → "
            f"「{target_cat.get('name')}」（编号 {proposal['id']}）\n"
            f"　等对方主人用「/同意 {proposal['id']}」回应吧。",
        )

    @filter.command("求婚列表", alias={"proposals", "求婚请求"})
    async def neko_proposals(self, event: AstrMessageEvent):
        """查看等待自己处理的求婚请求"""
        try:
            player = self._player(event)
            rows = self._world().received_proposals(player)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        await self._persist()
        yield await self._reply(event, fmt.proposal_list(rows))

    @filter.command("同意", alias={"accept"})
    async def neko_accept(self, event: AstrMessageEvent, proposal_id: str = ""):
        """同意求婚，例如「/同意 P1」"""
        if not proposal_id:
            yield event.plain_result(
                fmt.error_box("请指定求婚编号，例如「/同意 P1」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.accept_proposal(player, proposal_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        proposal, marriage = result
        yield await self._reply(
            event,
            f"💞 恭喜！「{proposal.get('from_cat_name')}」与"
            f"「{proposal.get('to_cat_name')}」结为伴侣啦～\n"
            f"　婚姻编号：{marriage['id']}　祝她们幸福！",
        )

    @filter.command("拒绝", alias={"reject"})
    async def neko_reject(self, event: AstrMessageEvent, proposal_id: str = ""):
        """拒绝求婚，例如「/拒绝 P1」"""
        if not proposal_id:
            yield event.plain_result(
                fmt.error_box("请指定求婚编号，例如「/拒绝 P1」。")
            )
            return
        try:
            player = self._player(event)
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.reject_proposal(player, proposal_id))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        yield event.plain_result(
            f"💔 已拒绝「{result.get('from_cat_name')}」的求婚。"
        )

    @filter.command("伴侣", alias={"couple", "结婚"})
    async def neko_couple(self, event: AstrMessageEvent, cat: str = ""):
        """查看猫娘的伴侣"""
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
            partner = world.partner_of(str(catgirl["id"]))
            marriage = world.marriage_of(str(catgirl["id"]))
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        if partner is None or marriage is None:
            yield event.plain_result(
                f"「{catgirl.get('name')}」目前还是单身哦～\n"
                f"试试「/求婚 {catgirl.get('name')} <对方猫娘>」。"
            )
            return
        yield await self._reply(
            event,
            f"💞 「{catgirl.get('name')}」 ❤️ 「{partner.get('name')}」\n"
            f"　婚姻编号：{marriage.get('id')}",
        )

    @filter.command("离婚", alias={"divorce"})
    async def neko_divorce(self, event: AstrMessageEvent, cat: str = ""):
        """办理离婚（需要支付手续费）"""
        try:
            player = self._player(event)
            world = self._world()
            catgirl = world.resolve_catgirl(player, cat)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return

        ok, result = self._run(lambda: world.divorce(player, catgirl))
        if not ok:
            yield event.plain_result(str(result))
            return
        await self._persist()
        marriage, partner = result
        partner_name = partner.get("name") if partner else "对方"
        yield event.plain_result(
            f"💔 「{catgirl.get('name')}」与「{partner_name}」的婚姻结束了"
            f"（{marriage.get('id')}）。\n"
            f"　当前余额 {player.get('coins')} 金币。"
        )

    # ---------------------------- 管理 ----------------------------
    @filter.command("刷新商城", alias={"refreshshop"})
    async def neko_refresh_shop(self, event: AstrMessageEvent):
        """立即刷新官方商城（仅管理员）"""
        if not event.is_admin():
            yield event.plain_result(fmt.error_box("只有管理员才能刷新商城。"))
            return
        try:
            world = self._world()
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        shop = world.refresh_official_shop()
        await self._persist()
        yield event.plain_result(
            f"🔄 商城已刷新（{shop.get('date')}），"
            f"共 {len(shop.get('entries') or [])} 件商品。"
        )

    @filter.command("发放", alias={"grant"})
    async def neko_grant(
        self, event: AstrMessageEvent, target: str = "", amount: int = 0
    ):
        """给玩家发放或扣除金币（仅管理员），例如「/发放 阿猫 500」"""
        if not event.is_admin():
            yield event.plain_result(fmt.error_box("只有管理员才能发放金币。"))
            return
        if not target:
            yield event.plain_result(
                fmt.error_box("用法：「/发放 <玩家昵称或ID> <金额>」。")
            )
            return
        try:
            world = self._world()
            player = world.find_player(target)
        except GameError as exc:
            yield event.plain_result(fmt.error_box(exc.message))
            return
        if player is None:
            yield event.plain_result(
                fmt.error_box(f"找不到玩家 `{target}`，对方需要先和机器人说过话。")
            )
            return
        if amount >= 0:
            world.add_coins(player, amount)
        else:
            ok, result = self._run(
                lambda: world.spend_coins(player, -amount, "管理员扣除")
            )
            if not ok:
                yield event.plain_result(str(result))
                return
        await self._persist()
        yield event.plain_result(
            f"✅ 已为 {player.get('name') or player.get('key')} "
            f"{'发放' if amount >= 0 else '扣除'} {abs(amount)} 金币，"
            f"当前余额 {player.get('coins')} 金币。"
        )

    # ==================================================================
    # 面板 Web API
    # ==================================================================
    def _api_world(self) -> tuple[NekoWorld | None, Any]:
        """Web API 里安全地取 world，未就绪时返回错误响应。"""
        if self.world is None:
            return None, error_response("插件尚未初始化完成，请稍后重试。", status_code=503)
        return self.world, None

    async def api_items(self):
        """GET /neko_han/items —— 返回全部道具定义。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        return json_response(
            {
                "items": world.registry.all(include_disabled=True),
                "categories": CATEGORIES,
                "category_labels": CATEGORY_LABELS,
                "effect_keys": EFFECT_KEYS,
                "effect_flags": EFFECT_FLAGS,
            }
        )

    async def api_item_save(self):
        """POST /neko_han/items/save —— 新增或更新一个道具。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体格式不正确。")
        try:
            item, created = world.registry.save(payload)
        except GameError as exc:
            return error_response(exc.message)
        await world.save()
        return json_response({"saved": True, "created": created, "item": item})

    async def api_item_delete(self):
        """POST /neko_han/items/delete —— 删除一个道具。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        payload = await request.json(default={})
        item_id = str((payload or {}).get("id") or "").strip()
        if not item_id:
            return error_response("缺少道具 ID。")
        try:
            world.registry.delete(item_id)
        except GameError as exc:
            return error_response(exc.message)
        await world.save()
        return json_response({"deleted": True, "id": item_id})

    async def api_items_reset(self):
        """POST /neko_han/items/reset —— 恢复内置默认道具表。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        items = world.registry.reset()
        await world.save()
        return json_response({"reset": True, "items": items})

    def _shop_payload(self, world: NekoWorld) -> dict[str, Any]:
        """构造官方商城的响应体（GET / POST 刷新共用同一结构）。"""
        shop = world.ensure_official_shop()
        entries = []
        for row in world.shop_view():
            item = row["item"]
            entries.append(
                {
                    "item_id": item["id"],
                    "name": item["name"],
                    "emoji": item.get("emoji", ""),
                    "category": item.get("category"),
                    "price": row["price"],
                    "base_price": row["base_price"],
                    "stock": row["stock"],
                    "stock_initial": row["stock_initial"],
                    "unlimited": row["unlimited"],
                }
            )
        return {
            "date": str(shop.get("date") or ""),
            "refresh_hour": world.cfg.day_reset_hour,
            "entries": entries,
        }

    async def api_shop(self):
        """GET /neko_han/shop —— 返回当日官方商城。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        return json_response(self._shop_payload(world))

    async def api_shop_refresh(self):
        """POST /neko_han/shop/refresh —— 立即重新生成官方商城。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        world.refresh_official_shop()
        await world.save()
        # 与 GET /shop 保持完全一致的结构，前端无需为刷新单独做兼容
        return json_response(self._shop_payload(world))

    async def api_overview(self):
        """GET /neko_han/overview —— 返回运行概览统计。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        return json_response(world.overview())

    async def api_players_grant(self):
        """POST /neko_han/players/grant —— 给玩家发放/扣除金币。"""
        world, err = self._api_world()
        if err:
            return err
        assert world is not None
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体格式不正确。")
        query = str(payload.get("player_id") or "").strip()
        if not query:
            return error_response("请填写玩家 ID 或昵称。")
        try:
            amount = int(payload.get("amount"))
        except (TypeError, ValueError):
            return error_response("金额必须是整数。")

        player = world.find_player(query)
        if player is None:
            return error_response(f"找不到玩家 `{query}`。")
        if amount >= 0:
            world.add_coins(player, amount)
        else:
            try:
                world.spend_coins(player, -amount, "面板扣除")
            except GameError as exc:
                return error_response(exc.message)
        await world.save()
        return json_response(
            {"player_id": player.get("key"), "coins": player.get("coins")}
        )
