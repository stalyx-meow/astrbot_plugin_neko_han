"""针对 AstrBot 胶水层（main.py）的集成测试。

这些测试**真实导入已安装的 AstrBot**，用于验证：
1. main.py 能被 AstrBot 的插件加载方式（`data.plugins.<name>.main`）成功导入；
2. 所有指令都注册成功，且命令名（含别名）与参数签名符合预期；
3. 真实使用 AstrBot 的 CommandFilter 解析消息，确认参数个数/类型转换正确；
4. 每个指令处理函数都能跑通并返回非空回复；
5. 面板 Web API 的响应结构与前端契约一致。

不依赖任何运行中的服务，可直接 `python3 -m unittest`。
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

# AstrBot 的 logger 会打印大量 INFO 日志，测试里静音以免淹没断言输出。
# 必须在导入 astrbot 之前生效。
logging.disable(logging.CRITICAL)

# 导入 astrbot 时会自动创建它的数据根目录（data/、data/t2i_templates 等）。
# 把 ASTRBOT_ROOT 指向临时目录，避免在插件目录里留下垃圾文件。
_ASTRBOT_TEST_ROOT = tempfile.mkdtemp(prefix="neko_han_astrbot_root_")
os.environ["ASTRBOT_ROOT"] = _ASTRBOT_TEST_ROOT
atexit.register(shutil.rmtree, _ASTRBOT_TEST_ROOT, True)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402


def schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    """按 `_conf_schema.json` 生成一份默认配置字典。

    等价于 AstrBot 在 `data/config/<plugin>_config.json` 中生成的实体，
    但不读写文件，测试之间互不干扰。
    """

    def build(node: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, spec in node.items():
            if not isinstance(spec, dict):
                continue
            if "default" in spec:
                result[key] = spec["default"]
            elif spec.get("type") == "object" and isinstance(spec.get("items"), dict):
                result[key] = build(spec["items"])
            else:
                result[key] = None
        return result

    return build(schema)


def load_plugin_module():
    """按 AstrBot 的方式把插件导入为 `data.plugins.neko_han.main`。

    这样 `from .core import ...` 这样的相对导入才能生效。
    """
    package_name = "data.plugins.neko_han"
    if package_name in sys.modules:
        return sys.modules[f"{package_name}.main"]

    # 构造 data / data.plugins / data.plugins.neko_han 命名空间包
    import types

    for name, path in (
        ("data", PLUGIN_ROOT.parent),
        ("data.plugins", PLUGIN_ROOT.parent),
        (package_name, PLUGIN_ROOT),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        module.__package__ = name
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location(
        f"{package_name}.main",
        PLUGIN_ROOT / "main.py",
        submodule_search_locations=None,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{package_name}.main"] = module
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------
# 假的事件与上下文
# ----------------------------------------------------------------------
class FakeEvent:
    """最小可用的 AstrMessageEvent 替身，只实现插件实际用到的接口。"""

    def __init__(
        self,
        message: str = "",
        uid: str = "1001",
        name: str = "阿猫",
        platform: str = "aiocqhttp",
        admin: bool = False,
        umo: str = "",
    ) -> None:
        self.message_str = message
        self._uid = uid
        self._name = name
        self._platform = platform
        self.role = "admin" if admin else "member"
        self.unified_msg_origin = umo or f"{platform}:GroupMessage:888"
        self.sent: list[str] = []
        self.stopped = False

    def get_platform_name(self) -> str:
        return self._platform

    def get_sender_id(self) -> str:
        return self._uid

    def get_sender_name(self) -> str:
        return self._name

    def get_group_id(self) -> str:
        return "888"

    def is_admin(self) -> bool:
        return self.role == "admin"

    def stop_event(self) -> None:
        self.stopped = True

    # -- 结果构造 ------------------------------------------------------
    def plain_result(self, text: str) -> dict[str, str]:
        return {"type": "plain", "text": text}

    def image_result(self, url: str) -> dict[str, str]:
        return {"type": "image", "url": url}

    async def send(self, result: Any) -> None:
        self.sent.append(_result_text(result))


def _result_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("text") or result.get("url") or "")
    return str(result)


class FakeContext:
    """记录 register_web_api 调用，并吞掉主动消息发送。"""

    def __init__(self) -> None:
        self.web_apis: list[tuple[str, Any, list[str], str]] = []
        self.sent_messages: list[tuple[str, str]] = []

    def register_web_api(self, route, view_handler, methods, desc) -> None:
        self.web_apis.append((route, view_handler, methods, desc))

    async def send_message(self, session: str, chain: Any) -> bool:
        self.sent_messages.append((session, str(chain)))
        return True


class FakeRequest:
    """替代 `astrbot.api.web.request`，用于直接调用 Web API 处理函数。"""

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {}

    async def json(self, default: Any = None) -> Any:
        return self._payload if self._payload is not None else default


def run(coro: Any) -> Any:
    """同步执行一个协程。"""
    return asyncio.run(coro)


def collect(handler, *args, **kwargs) -> list[str]:
    """执行指令处理函数（异步生成器），收集所有回复文本。"""

    async def drain() -> list[str]:
        out: list[str] = []
        async for result in handler(*args, **kwargs):
            out.append(_result_text(result))
        return out

    return asyncio.run(drain())


class PluginIntegrationBase(unittest.TestCase):
    """载入插件一次，为每个测试准备全新的数据目录。"""

    module: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_plugin_module()

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="neko_han_it_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.context = FakeContext()
        self.config = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text("utf-8"))
        self.plugin = self.module.NekoHanPlugin(
            self.context, schema_defaults(self.config)
        )
        # 指向临时数据目录，避免污染真实插件数据
        self.plugin.data_dir = self.tmp
        run(self.plugin.initialize())
        self.addCleanup(lambda: run(self.plugin.terminate()))

    # -- 辅助 ----------------------------------------------------------
    def event(self, **kwargs) -> FakeEvent:
        return FakeEvent(**kwargs)

    @classmethod
    def flat_commands(cls) -> dict[str, CommandFilter]:
        """收集所有已注册的指令过滤器，展开其全部指令名（含别名）。"""
        found: dict[str, CommandFilter] = {}
        for handler in star_handlers_registry._handlers:  # noqa: SLF001
            for event_filter in getattr(handler, "event_filters", []) or []:
                if isinstance(event_filter, CommandFilter):
                    for name in event_filter.get_complete_command_names():
                        found[name] = event_filter
        return found

    def call(self, method_name: str, event: FakeEvent, *args) -> list[str]:
        handler = getattr(self.plugin, method_name)
        return collect(handler, event, *args)


# ======================================================================
# 插件装载与注册
# ======================================================================
class TestPluginRegistration(PluginIntegrationBase):
    def test_module_imports_with_relative_core(self):
        """main.py 必须能按 AstrBot 的方式导入，且相对导入生效。"""
        self.assertTrue(hasattr(self.module, "NekoHanPlugin"))
        self.assertTrue(hasattr(self.module, "NekoWorld"))
        self.assertTrue(hasattr(self.module, "GameError"))

    def test_permissions_route_registered_with_plugin_prefix(self):
        """面板 API 路由必须以插件名为前缀，且方法限定正确。"""
        routes = {route: methods for route, _h, methods, _d in self.context.web_apis}
        self.assertEqual(len(routes), 8)
        for route in routes:
            self.assertTrue(
                route.startswith("/neko_han/"), f"路由缺少插件名前缀: {route}"
            )
        self.assertEqual(routes["/neko_han/items"], ["GET"])
        self.assertEqual(routes["/neko_han/shop"], ["GET"])
        self.assertEqual(routes["/neko_han/overview"], ["GET"])
        for route in (
            "/neko_han/items/save",
            "/neko_han/items/delete",
            "/neko_han/items/reset",
            "/neko_han/shop/refresh",
            "/neko_han/players/grant",
        ):
            self.assertEqual(routes[route], ["POST"], route)

    def test_world_initialised_and_items_seeded(self):
        self.assertIsNotNone(self.plugin.world)
        self.assertTrue(self.plugin.world.registry.all())
        self.assertTrue((self.tmp / "state.json").exists())
        self.assertTrue((self.tmp / "items.json").exists())


# ======================================================================
# 指令注册与参数解析（使用真实的 CommandFilter）
# ======================================================================
class TestCommandRegistration(PluginIntegrationBase):

    def test_all_expected_commands_registered(self):
        registered = self.flat_commands()
        expected = [
            "帮助",
            "认养",
            "我的",
            "状态",
            "改名",
            "送养",
            "猫娘榜",
            "喂食",
            "喝水",
            "使用",
            "仓库",
            "钱包",
            "签到",
            "任务",
            "开始",
            "商城",
            "购买",
            "市场",
            "上架",
            "挂单",
            "买入",
            "下架",
            "求婚",
            "求婚列表",
            "同意",
            "拒绝",
            "伴侣",
            "离婚",
            "决斗",
            "决斗列表",
            "接受决斗",
            "拒绝决斗",
            "取消决斗",
            "猜",
            "刷新商城",
            "发放",
        ]
        missing = [name for name in expected if name not in registered]
        self.assertEqual(missing, [], f"未注册的指令: {missing}")

    def test_aliases_registered(self):
        registered = self.flat_commands()
        for alias in [
            "菜单",
            "adopt",
            "feed",
            "shop",
            "buy",
            "sell",
            "propose",
            "accept",
            "reject",
            "daily",
            "wallet",
            "rank",
        ]:
            self.assertIn(alias, registered, f"别名未注册: {alias}")

    def test_old_neko_prefixed_forms_removed(self):
        """旧的「neko 认养」写法必须已经不存在（否则正则兜底提示会与之冲突）。"""
        registered = self.flat_commands()
        leftovers = [name for name in registered if name.startswith(("neko ", "猫娘 ", "nh ", "nekohan "))]
        self.assertEqual(leftovers, [], f"仍存在旧写法: {leftovers}")

    def test_claim_command_removed(self):
        """任务奖励已改为自动发放，/领取 不应再注册。"""
        registered = self.flat_commands()
        for name in ("领取", "claim", "领奖"):
            self.assertNotIn(name, registered, f"{name} 应已移除")
        # 但「领取金币」是签到的别名，必须保留
        self.assertIn("领取金币", registered)

    def test_does_not_shadow_builtin_commands(self):
        """不能占用 AstrBot 内置指令名，否则会把内置功能顶掉。

        内置指令清单取自 AstrBot 4.26.6 的 `astrbot/builtin_stars/` 源码。
        """
        builtin = {
            "help",
            "sid",
            "name",
            "reset",
            "stop",
            "dashboard_update",
            "new",
            "provider",
            "set",
            "stats",
            "unset",
        }
        registered = set(self.flat_commands())
        collisions = sorted(builtin & registered)
        self.assertEqual(collisions, [], f"与内置指令冲突: {collisions}")

    def test_parameter_parsing_with_real_filter(self):
        """用真实的 CommandFilter 验证参数个数与类型转换。"""
        registered = self.flat_commands()

        cases = [
            # (命令, 消息参数, 期望的参数字典)
            ("认养", [], {"name": ""}),
            ("认养", ["小奶油"], {"name": "小奶油"}),
            ("喂食", ["小奶油", "猫粮"], {"cat": "小奶油", "item": "猫粮"}),
            ("喂食", ["猫粮"], {"cat": "猫粮", "item": ""}),
            ("状态", [], {"cat": ""}),
            ("开始", ["小奶油", "2"], {"cat": "小奶油", "slot": 2}),
            ("购买", ["猫粮"], {"item": "猫粮", "qty": 1}),
            ("购买", ["猫粮", "3"], {"item": "猫粮", "qty": 3}),
            (
                "上架",
                ["小奶油", "毛线球", "2", "50"],
                {"cat": "小奶油", "item": "毛线球", "qty": 2, "price": 50},
            ),
            ("买入", ["L1"], {"listing_id": "L1", "qty": 1}),
            ("下架", ["L1"], {"listing_id": "L1"}),
            ("同意", ["P1"], {"proposal_id": "P1"}),
            ("改名", ["旧名", "新名"], {"cat": "旧名", "new_name": "新名"}),
            ("发放", ["阿猫", "500"], {"target": "阿猫", "amount": 500}),
        ]
        for command, params, expected in cases:
            with self.subTest(command=command, params=params):
                cmd_filter = registered[command]
                parsed = cmd_filter.validate_and_convert_params(
                    list(params), cmd_filter.handler_params
                )
                self.assertEqual(parsed, expected)

    def test_required_parameters_enforced(self):
        """缺少必填参数时应报错（本插件几乎所有参数都有默认值）。"""
        registered = self.flat_commands()
        cmd_filter = registered["上架"]
        # 上架的参数都有默认值，因此空参数不会抛错
        parsed = cmd_filter.validate_and_convert_params([], cmd_filter.handler_params)
        self.assertEqual(parsed, {"cat": "", "item": "", "qty": 0, "price": 0})

    def test_invalid_int_rejected(self):
        registered = self.flat_commands()
        cmd_filter = registered["开始"]
        with self.assertRaises(ValueError):
            cmd_filter.validate_and_convert_params(
                ["小奶油", "abc"], cmd_filter.handler_params
            )


# ======================================================================
# 指令端到端行为
# ======================================================================
class TestCommandBehaviour(PluginIntegrationBase):
    def test_help(self):
        out = self.call("neko_help", self.event())
        self.assertEqual(len(out), 1)
        self.assertIn("Neko_Han", out[0])
        self.assertIn("/认养", out[0])

    # ---------------------------- 格式兜底提示 ----------------------------
    def test_hint_for_old_prefix(self):
        """旧的 neko/猫娘 前缀写法要提示已扁平化。"""
        hint_for = self.module.hint_for_text
        cases = [
            "neko 帮助",
            "neko帮助",
            "neko 认养 小奶油",
            "neko认养小奶油",
            "/neko 商城",
            "nekohan 钱包",
            "nh 签到",
            "猫娘 认养",
            "猫娘商城",
            "猫娘认养小奶油",
        ]
        for text in cases:
            with self.subTest(text=text):
                hint = hint_for(text)
                self.assertIsNotNone(hint, f"应给出提示: {text}")
                self.assertIn("不用再加 neko", hint)

    def test_hint_for_glued_arguments(self):
        """指令与参数粘在一起时，要提示加空格。"""
        hint_for = self.module.hint_for_text
        cases = [
            "认养小奶油",
            "/认养小奶油",
            "喂食小奶油猫粮",
            "上架小奶油毛线球250",
            "购买猫粮3",
            "状态小奶油",
            "求婚小奶油橘子",
        ]
        for text in cases:
            with self.subTest(text=text):
                hint = hint_for(text)
                self.assertIsNotNone(hint, f"应给出提示: {text}")
                self.assertIn("空格", hint)

    def test_hint_is_none_for_valid_commands(self):
        """合法指令必须放行，不能被兜底提示抢答。"""
        hint_for = self.module.hint_for_text
        valid = [
            "认养 小奶油",
            "/认养 小奶油",
            "/喂食 小奶油 猫粮",
            "猫娘榜",
            "/猫娘榜",
            "我的猫娘",
            "我的挂单",
            "求婚列表",
            "任务列表",
            "领取金币",
            "帮助",
            "/帮助",
            "上架 小奶油 毛线球 2 50",
            "购买 猫粮 3",
            "状态",
            "/图鉴",
            "",
        ]
        for text in valid:
            with self.subTest(text=text):
                self.assertIsNone(hint_for(text), f"不应提示: {text}")

    def test_hint_ignores_unrelated_text(self):
        """与指令无关的正常聊天不能被误伤。"""
        hint_for = self.module.hint_for_text
        for text in [
            "nekos",
            "今天聊点猫娘相关的话题",
            "这只猫好可爱",
            "Neko 是什么意思",
            "neko assets",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(hint_for(text), f"不应提示: {text}")

    def test_usage_hint_handler_replies_only_when_needed(self):
        """兜底 handler 只在需要时回复。"""
        out = self.call("neko_usage_hint", self.event(message="neko 认养 小奶油"))
        self.assertEqual(len(out), 1)
        self.assertIn("/认养", out[0])

        out = self.call("neko_usage_hint", self.event(message="认养小奶油"))
        self.assertEqual(len(out), 1)
        self.assertIn("空格", out[0])

        # 合法指令：不应有任何输出
        out = self.call("neko_usage_hint", self.event(message="认养 小奶油"))
        self.assertEqual(out, [])

    def test_command_names_constant_matches_registry(self):
        """`_COMMAND_NAMES` 必须与实际注册的指令完全一致，避免漏掉别名。"""
        registered = set(self.flat_commands())
        declared = set(self.module._COMMAND_NAMES)
        self.assertEqual(
            declared - registered, set(), "声明了但未注册的指令名"
        )
        self.assertEqual(
            registered - declared, set(), "注册了但未声明的指令名"
        )

    def test_adopt_with_name(self):
        out = self.call("neko_adopt", self.event(), "小奶油")
        self.assertEqual(len(out), 1)
        self.assertIn("小奶油", out[0])
        cats = self.plugin.world.catgirls_of(
            self.plugin.world.get_player("aiocqhttp:1001")
        )
        self.assertEqual(len(cats), 1)

    def test_adopt_duplicate_name_reports_error(self):
        self.call("neko_adopt", self.event(), "小奶油")
        out = self.call("neko_adopt", self.event(), "小奶油")
        self.assertIn("😿", out[0])

    def test_list_shows_quota(self):
        """「/我的」要显示名额，让玩家知道自己还有几个位置。"""
        out = self.call("neko_list", self.event())
        self.assertIn("上限 3", out[0])

        self.call("neko_adopt", self.event(), "一猫")
        out = self.call("neko_list", self.event())
        self.assertIn("1 只猫娘", out[0])
        self.assertIn("存活 1/3", out[0])

    def test_wallet_shows_quota(self):
        """「/钱包」要显示 猫娘 N/上限。"""
        out = self.call("neko_wallet", self.event())
        self.assertIn("猫娘：0/3", out[0])
        self.call("neko_adopt", self.event(), "一猫")
        out = self.call("neko_wallet", self.event())
        self.assertIn("猫娘：1/3", out[0])

    def test_adopt_reports_progress(self):
        out = self.call("neko_adopt", self.event(), "一猫")
        self.assertIn("猫娘：1/3", out[0])
        out = self.call("neko_adopt", self.event(), "二猫")
        self.assertIn("猫娘：2/3", out[0])

    def test_quota_enforced_and_reported(self):
        """达到上限后必须拒绝，并在提示里给出当前数量与上限。"""
        for i in range(3):
            self.call("neko_adopt", self.event(), f"猫{i}")

        out = self.call("neko_adopt", self.event(), "第四只")
        self.assertIn("😿", out[0])
        self.assertIn("3/3", out[0])

        # 无名认养的分支同样受限
        out = self.call("neko_adopt", self.event(), "")
        self.assertIn("3/3", out[0])

        player = self.plugin.world.get_player("aiocqhttp:1001")
        self.assertEqual(len(player["catgirls"]), 3)

    def test_quota_is_configurable(self):
        """上限必须跟随配置变化。"""
        self.plugin.config["economy"]["max_catgirls_per_player"] = 5
        for i in range(5):
            self.call("neko_adopt", self.event(), f"猫{i}")
        out = self.call("neko_wallet", self.event())
        self.assertIn("猫娘：5/5", out[0])
        out = self.call("neko_adopt", self.event(), "第六只")
        self.assertIn("5/5", out[0])

    def test_dead_catgirl_frees_a_slot(self):
        """离世的猫娘不占名额：玩家可以直接补养新的。"""
        for i in range(3):
            self.call("neko_adopt", self.event(), f"猫{i}")

        # 满员时无法再养
        out = self.call("neko_adopt", self.event(), "第四只")
        self.assertIn("3/3", out[0])

        # 让其中一只离世
        player = self.plugin.world.get_player("aiocqhttp:1001")
        dead = self.plugin.world.resolve_catgirl(player, "猫0")
        self.plugin.world._kill_catgirl(dead, "测试")

        # 名额释放：可以补养
        out = self.call("neko_adopt", self.event(), "替补")
        self.assertIn("恭喜", out[0])
        self.assertIn("3/3", out[0])  # 存活仍是 3

        slots = self.plugin.world.catgirl_slots(player)
        self.assertEqual(slots["alive"], 3)
        self.assertEqual(slots["dead"], 1)
        self.assertEqual(slots["total"], 4)
        self.assertEqual(slots["remaining"], 0)

    def test_dead_catgirl_not_counted_in_wallet_and_list(self):
        """钱包与列表要能看出"离世的不占名额"。"""
        self.call("neko_adopt", self.event(), "活猫")
        self.call("neko_adopt", self.event(), "死猫")
        player = self.plugin.world.get_player("aiocqhttp:1001")
        dead = self.plugin.world.resolve_catgirl(player, "死猫")
        self.plugin.world._kill_catgirl(dead, "测试")

        out = self.call("neko_wallet", self.event())
        self.assertIn("猫娘：1/3 只存活", out[0])
        self.assertIn("另有 1 只已离世", out[0])

        out = self.call("neko_list", self.event())
        self.assertIn("存活 1/3", out[0])
        self.assertIn("不占名额", out[0])
        self.assertIn("💀 死猫（已离世）", out[0])

    def test_adopt_message_mentions_dead_not_occupying(self):
        """有离世猫娘时，认养成功文案要说明她们不占名额。"""
        self.call("neko_adopt", self.event(), "活猫")
        self.call("neko_adopt", self.event(), "死猫")
        player = self.plugin.world.get_player("aiocqhttp:1001")
        dead = self.plugin.world.resolve_catgirl(player, "死猫")
        self.plugin.world._kill_catgirl(dead, "测试")

        out = self.call("neko_adopt", self.event(), "新猫")
        self.assertIn("恭喜", out[0])
        self.assertIn("另有 1 只已离世，不占名额", out[0])

    def test_list_warns_when_quota_reached(self):
        for i in range(3):
            self.call("neko_adopt", self.event(), f"猫{i}")
        out = self.call("neko_list", self.event())
        self.assertIn("已经到达上限", out[0])

    def test_status_and_list(self):
        self.call("neko_adopt", self.event(), "小奶油")
        out = self.call("neko_status", self.event(), "小奶油")
        self.assertIn("饱食度", out[0])
        self.assertIn("精力值", out[0])
        out = self.call("neko_list", self.event())
        self.assertIn("小奶油", out[0])

    def test_wallet_and_daily(self):
        out = self.call("neko_wallet", self.event())
        self.assertIn("300", out[0])
        out = self.call("neko_daily", self.event())
        self.assertIn("80", out[0])
        out = self.call("neko_daily", self.event())
        self.assertIn("😿", out[0], "同一天不应重复签到")

    def test_feed_flow(self):
        self.call("neko_adopt", self.event(), "小奶油")
        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        cat["satiety"] = 10

        # 没有道具时应该报错
        out = self.call("neko_feed", self.event(), "小奶油", "猫粮")
        self.assertIn("😿", out[0])

        self.plugin.world._inv_add(player, "cat_food", 1)
        out = self.call("neko_feed", self.event(), "小奶油", "猫粮")
        self.assertIn("饱食度 +30", out[0])
        self.assertEqual(cat["satiety"], 40)

    def test_feed_without_catgirl_name_infers(self):
        """只给一个参数且是道具名时，自动选中唯一猫娘。"""
        self.call("neko_adopt", self.event(), "小奶油")
        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        cat["hydration"] = 0
        self.plugin.world._inv_add(player, "water", 1)
        out = self.call("neko_drink", self.event(), "清水", "")
        self.assertIn("水分", out[0])
        self.assertEqual(cat["hydration"], 35)

    def test_feed_giving_only_catgirl_asks_for_item(self):
        self.call("neko_adopt", self.event(), "小奶油")
        out = self.call("neko_feed", self.event(), "小奶油", "")
        self.assertIn("要喂", out[0])

    def test_drink_rejects_food(self):
        self.call("neko_adopt", self.event(), "小奶油")
        player = self.plugin.world.get_player("aiocqhttp:1001")
        self.plugin.world._inv_add(player, "cat_food", 1)
        out = self.call("neko_drink", self.event(), "小奶油", "猫粮")
        self.assertIn("😿", out[0])
        # 分类校验应发生在消耗之前
        self.assertEqual(self.plugin.world._inv_count(player, "cat_food"), 1)

    def test_shop_and_buy(self):
        out = self.call("neko_shop", self.event())
        self.assertIn("官方商城", out[0])
        row = self.plugin.world.shop_view()[0]
        out = self.call("neko_buy", self.event(), row["item"]["name"], 1)
        self.assertIn("购买成功", out[0])
        player = self.plugin.world.get_player("aiocqhttp:1001")
        self.assertEqual(self.plugin.world._inv_count(player, row["item"]["id"]), 1)

    def test_buy_without_item(self):
        out = self.call("neko_buy", self.event())
        self.assertIn("😿", out[0])

    def test_bag(self):
        player = self.plugin.world.get_player("aiocqhttp:1001") or None
        self.call("neko_wallet", self.event())  # 触发玩家创建
        player = self.plugin.world.get_player("aiocqhttp:1001")
        self.plugin.world._inv_add(player, "cat_food", 2)
        out = self.call("neko_bag", self.event())
        self.assertIn("猫粮", out[0])

    def test_task_flow_with_auto_reward(self):
        """任务流程：开始 → 到时间自动发奖（不需要 /领取）。"""
        self.call("neko_adopt", self.event(), "小奶油")
        out = self.call("neko_tasks", self.event(), "小奶油")
        self.assertIn("任务", out[0])
        self.assertIn("自动发放", out[0])

        out = self.call("neko_start", self.event(), "小奶油", 1)
        self.assertIn("开始", out[0])
        self.assertIn("自动到账", out[0])
        self.assertNotIn("/领取", out[0])

        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        slot = self.plugin.world.find_slot(cat, 1)
        before = player["coins"]

        # 还没到时间：不发奖
        self.plugin.world.settle_catgirl(cat)
        self.assertEqual(player["coins"], before)

        # 时间到：自动发奖
        slot["finish_at"] = time.time() - 1
        self.plugin.world.settle_catgirl(cat)
        self.assertEqual(player["coins"], before + slot["reward"])
        self.assertEqual(slot["status"], "done")

    def test_task_reward_reported_on_next_command(self):
        """自动到账的奖励要在下一次相关指令里告诉玩家。"""
        self.call("neko_adopt", self.event(), "小奶油")
        self.call("neko_start", self.event(), "小奶油", 1)
        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        slot = self.plugin.world.find_slot(cat, 1)
        slot["finish_at"] = time.time() - 1

        out = self.call("neko_tasks", self.event(), "小奶油")
        self.assertIn("奖励", out[0])
        self.assertIn("自动到账", out[0])
        self.assertIn(slot["name"], out[0])

        # 通知只出现一次
        out = self.call("neko_tasks", self.event(), "小奶油")
        self.assertNotIn("自动到账", out[0])

    def test_task_reward_notice_in_wallet_and_list(self):
        """/我的 与 /状态 也应能带出到账提示。"""
        self.call("neko_adopt", self.event(), "小奶油")
        self.call("neko_start", self.event(), "小奶油", 1)
        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        self.plugin.world.find_slot(cat, 1)["finish_at"] = time.time() - 1

        out = self.call("neko_list", self.event())
        self.assertIn("自动到账", out[0])

        self.call("neko_start", self.event(), "小奶油", 2)
        cat2 = self.plugin.world.catgirls_of(player)[0]
        self.plugin.world.find_slot(cat2, 2)["finish_at"] = time.time() - 1
        out = self.call("neko_status", self.event(), "小奶油")
        self.assertIn("自动到账", out[0])

    def test_start_reports_already_finished_reward(self):
        """开始新任务时，顺手把上一个已完成任务的奖励提示出来。"""
        self.call("neko_adopt", self.event(), "小奶油")
        self.call("neko_start", self.event(), "小奶油", 1)
        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        self.plugin.world.find_slot(cat, 1)["finish_at"] = time.time() - 1

        out = self.call("neko_start", self.event(), "小奶油", 2)
        self.assertIn("自动到账", out[0])

    def test_start_without_slot_number(self):
        self.call("neko_adopt", self.event(), "小奶油")
        out = self.call("neko_start", self.event(), "小奶油", 0)
        self.assertIn("😿", out[0])

    def test_market_sell_buy_cancel_flow(self):
        # 卖家
        seller_ev = self.event(uid="2001", name="卖家")
        self.call("neko_adopt", seller_ev, "看摊猫")
        seller = self.plugin.world.get_player("aiocqhttp:2001")
        self.plugin.world._inv_add(seller, "yarn_ball", 3)
        out = self.call("neko_sell", seller_ev, "看摊猫", "毛线球", 2, 60)
        self.assertIn("上架成功", out[0])

        out = self.call("neko_mine", seller_ev)
        self.assertIn("L1", out[0])

        out = self.call("neko_market", self.event(uid="2002", name="买家"))
        self.assertIn("毛线球", out[0])

        buyer_ev = self.event(uid="2002", name="买家")
        out = self.call("neko_buy_listing", buyer_ev, "L1", 1)
        self.assertIn("成交", out[0])
        buyer = self.plugin.world.get_player("aiocqhttp:2002")
        self.assertEqual(self.plugin.world._inv_count(buyer, "yarn_ball"), 1)

        out = self.call("neko_cancel", seller_ev, "L1")
        self.assertIn("已下架", out[0])
        self.assertEqual(self.plugin.world._inv_count(seller, "yarn_ball"), 2)

    def test_sell_missing_args(self):
        self.call("neko_adopt", self.event(), "看摊猫")
        out = self.call("neko_sell", self.event(), "看摊猫", "毛线球", 0, 0)
        self.assertIn("😿", out[0])

    def test_marriage_flow(self):
        a_ev = self.event(uid="3001", name="甲")
        b_ev = self.event(uid="3002", name="乙")
        self.call("neko_adopt", a_ev, "阿花")
        self.call("neko_adopt", b_ev, "阿草")

        out = self.call("neko_propose", a_ev, "阿花", "阿草")
        self.assertIn("求婚已送出", out[0])
        self.assertIn("P1", out[0])

        out = self.call("neko_proposals", b_ev)
        self.assertIn("阿花", out[0])

        out = self.call("neko_accept", b_ev, "P1")
        self.assertIn("结为伴侣", out[0])

        out = self.call("neko_couple", a_ev, "阿花")
        self.assertIn("阿草", out[0])

        # 离婚需要手续费，这里先给足金币
        self.plugin.world.get_player("aiocqhttp:3001")["coins"] = 1000
        out = self.call("neko_divorce", a_ev, "阿花")
        self.assertIn("婚姻结束了", out[0])
        self.assertEqual(
            self.plugin.world.get_player("aiocqhttp:3001")["coins"], 500
        )

    def test_propose_to_self_rejected(self):
        ev = self.event(uid="3003", name="丙")
        self.call("neko_adopt", ev, "阿花")
        out = self.call("neko_propose", ev, "阿花", "阿花")
        self.assertIn("😿", out[0])

    def test_rank_lists_catgirls(self):
        self.call("neko_adopt", self.event(), "小奶油")
        out = self.call("neko_rank", self.event())
        self.assertIn("小奶油", out[0])

    def test_admin_commands_require_admin(self):
        out = self.call("neko_refresh_shop", self.event(admin=False))
        self.assertIn("只有管理员", out[0])
        out = self.call("neko_grant", self.event(admin=False), "阿猫", 100)
        self.assertIn("只有管理员", out[0])

    def test_grant_changes_coins(self):
        self.call("neko_wallet", self.event())  # 先让玩家存在于存档中
        admin_ev = self.event(admin=True)
        out = self.call("neko_grant", admin_ev, "1001", 500)
        self.assertIn("发放", out[0])
        player = self.plugin.world.get_player("aiocqhttp:1001")
        self.assertEqual(player["coins"], 800)

    def test_grant_unknown_player(self):
        out = self.call("neko_grant", self.event(admin=True), "查无此人", 100)
        self.assertIn("😿", out[0])

    def test_refresh_shop_as_admin(self):
        out = self.call("neko_refresh_shop", self.event(admin=True))
        self.assertIn("商城已刷新", out[0])

    def test_rename_and_release(self):
        self.call("neko_adopt", self.event(), "旧名")
        out = self.call("neko_rename", self.event(), "旧名", "新名")
        self.assertIn("新名", out[0])
        out = self.call("neko_release", self.event(), "新名")
        self.assertIn("已经离开了", out[0])
        player = self.plugin.world.get_player("aiocqhttp:1001")
        self.assertEqual(player["catgirls"], [])

    def test_unknown_catgirl_reports_error(self):
        out = self.call("neko_status", self.event(), "不存在的猫")
        self.assertIn("😿", out[0])

    def test_no_catgirl_yet(self):
        out = self.call("neko_status", self.event(), "")
        self.assertIn("还没有猫娘", out[0])

    def test_use_item_flow(self):
        self.call("neko_adopt", self.event(), "小奶油")
        player = self.plugin.world.get_player("aiocqhttp:1001")
        cat = self.plugin.world.catgirls_of(player)[0]
        cat["health"] = 10
        self.plugin.world._inv_add(player, "medkit", 1)
        out = self.call("neko_use", self.event(), "小奶油", "医疗包")
        self.assertIn("健康值 +50", out[0])
        self.assertEqual(cat["health"], 60)

    def test_platform_isolation(self):
        """同一 QQ 号在不同平台应是两个独立账号。"""
        self.call("neko_adopt", self.event(platform="aiocqhttp"), "企鹅猫")
        out = self.call("neko_list", self.event(platform="telegram", uid="1001"))
        self.assertIn("还没有猫娘", out[0])


# ======================================================================
# 决斗系统（指令层）
# ======================================================================
class TestDuelCommands(PluginIntegrationBase):
    def _pair(self, coins: int = 1000):
        """造两位玩家 + 各一只猫娘。"""
        a_ev = self.event(uid="9001", name="甲", umo="aiocqhttp:GroupMessage:111")
        b_ev = self.event(uid="9002", name="乙", umo="aiocqhttp:GroupMessage:222")
        self.call("neko_adopt", a_ev, "阿花")
        self.call("neko_adopt", b_ev, "阿草")
        a = self.plugin.world.get_player("aiocqhttp:9001")
        b = self.plugin.world.get_player("aiocqhttp:9002")
        a["coins"] = b["coins"] = coins
        return a_ev, b_ev, a, b

    def _start(self, bet: int = 100):
        a_ev, b_ev, a, b = self._pair()
        self.call("neko_duel", a_ev, "阿花", "阿草", bet)
        self.call("neko_accept_duel", b_ev, "D1")
        return a_ev, b_ev, a, b

    def test_full_duel_flow(self):
        a_ev, b_ev, a, b = self._pair()

        out = self.call("neko_duel", a_ev, "阿花", "阿草", 100)
        self.assertIn("战书已送出", out[0])
        self.assertIn("D1", out[0])
        self.assertEqual(a["coins"], 900, "赌注应立即托管")

        out = self.call("neko_duel_list", b_ev)
        self.assertIn("等待你回应的战书", out[0])
        self.assertIn("阿花", out[0])

        out = self.call("neko_accept_duel", b_ev, "D1")
        self.assertIn("决斗开始", out[0])
        self.assertIn("建议私聊", out[0])
        self.assertEqual(b["coins"], 900)

        # 双方提交猜测
        self.plugin.world.store.duels[0]["target"] = 50
        out = self.call("neko_guess", a_ev, "45")
        self.assertIn("已收到", out[0])
        out = self.call("neko_guess", b_ev, "95")
        self.assertIn("揭晓", out[0])
        self.assertIn("目标数字", out[0])
        self.assertEqual(self.plugin.world.store.duels[0]["winner"], a["key"])
        self.assertEqual(a["coins"], 1100)

    def test_guess_reply_never_leaks_the_number(self):
        """关键安全性：提交猜测的回复不能回显数字，否则对手稳赢。"""
        a_ev, b_ev, a, b = self._start()
        self.plugin.world.store.duels[0]["target"] = 50

        out = self.call("neko_guess", a_ev, "77")
        self.assertEqual(len(out), 1)
        self.assertNotIn("77", out[0], "回复里出现了猜测数字")
        self.assertIn("不会公开显示", out[0])

        # 列表也不能泄露：未猜方不应看到对手的数字或目标
        out = self.call("neko_duel_list", b_ev)
        self.assertNotIn("77", out[0])
        self.assertNotIn("50", out[0])
        self.assertIn("阿花 已猜", out[0])

    def test_duel_list_hides_target_until_reveal(self):
        a_ev, b_ev, a, b = self._start()
        self.plugin.world.store.duels[0]["target"] = 88
        listing = self.call("neko_duel_list", a_ev)
        self.assertNotIn("88", listing[0])
        self.assertIn("双方都还没猜", listing[0])

    def test_two_guesses_reveal_and_pay(self):
        a_ev, b_ev, a, b = self._start(200)
        duel = self.plugin.world.store.duels[0]
        duel["target"] = 30
        self.call("neko_guess", a_ev, "10")   # 距离 20
        out = self.call("neko_guess", b_ev, "31")  # 距离 1
        self.assertIn("揭晓", out[0])
        self.assertIn("阿草", out[0])
        self.assertEqual(b["coins"], 800 + 400)

    def test_decline_refunds(self):
        a_ev, b_ev, a, b = self._pair()
        self.call("neko_duel", a_ev, "阿花", "阿草", 150)
        self.assertEqual(a["coins"], 850)
        out = self.call("neko_reject_duel", b_ev, "D1")
        self.assertIn("已拒绝", out[0])
        self.assertEqual(a["coins"], 1000)

    def test_cancel_refunds(self):
        a_ev, b_ev, a, b = self._pair()
        self.call("neko_duel", a_ev, "阿花", "阿草", 150)
        out = self.call("neko_cancel_duel", a_ev, "D1")
        self.assertIn("已撤回", out[0])
        self.assertEqual(a["coins"], 1000)

    def test_duel_usage_errors(self):
        a_ev, b_ev, a, b = self._pair()
        out = self.call("neko_duel", a_ev)
        self.assertIn("😿", out[0])
        out = self.call("neko_duel", a_ev, "阿花", "阿草", 0)
        self.assertIn("😿", out[0])
        out = self.call("neko_duel", a_ev, "阿花", "阿草", 999999)
        self.assertIn("😿", out[0])
        out = self.call("neko_accept_duel", b_ev)
        self.assertIn("😿", out[0])
        out = self.call("neko_reject_duel", b_ev, "")
        self.assertIn("😿", out[0])
        out = self.call("neko_cancel_duel", a_ev, "")
        self.assertIn("😿", out[0])

    def test_guess_argument_forms(self):
        """/猜 42 与 /猜 D1 42 两种写法都要支持。"""
        a_ev, b_ev, a, b = self._start()
        self.plugin.world.store.duels[0]["target"] = 50
        # 一把直接给数字
        out = self.call("neko_guess", a_ev, "42", 0)
        self.assertIn("已收到", out[0])
        # 带编号 + 数字
        out = self.call("neko_guess", b_ev, "D1", 50)
        self.assertIn("揭晓", out[0])

    def test_guess_without_duel(self):
        a_ev, b_ev, a, b = self._pair()
        out = self.call("neko_guess", a_ev, "42", 0)
        self.assertIn("😿", out[0])
        out = self.call("neko_guess", a_ev, "abc", 0)
        self.assertIn("😿", out[0])

    def test_guess_out_of_range_error(self):
        a_ev, b_ev, a, b = self._start()
        out = self.call("neko_guess", a_ev, "999", 0)
        self.assertIn("😿", out[0])
        self.assertIn("1 ~ 100", out[0])

    def test_duel_list_empty(self):
        out = self.call("neko_duel_list", self.event(uid="9100", name="路人"))
        self.assertIn("没有进行中的决斗", out[0])
        self.assertIn("/决斗", out[0])

    def test_duel_notify_opponent(self):
        """下战书应主动通知对方主人（若知道其会话）。"""
        a_ev, b_ev, a, b = self._pair()
        # 先让乙发一条消息，记录其会话
        self.call("neko_wallet", b_ev)
        self.call("neko_duel", a_ev, "阿花", "阿草", 100)
        pushed = [msg for _umo, msg in self.context.sent_messages]
        self.assertTrue(
            any("战书" in m for m in pushed),
            f"未发送战书通知: {pushed}",
        )

    def test_help_mentions_duel(self):
        out = self.call("neko_help", self.event())
        self.assertIn("/决斗", out[0])
        self.assertIn("/猜", out[0])


# ======================================================================
# 面板 Web API
# ======================================================================
class TestPanelApi(PluginIntegrationBase):
    def setUp(self) -> None:
        super().setUp()
        # 用假的 request 替换 main 模块里的 request 代理
        self._real_request = self.module.request

    def tearDown(self) -> None:
        self.module.request = self._real_request

    def body(self, payload: dict | None = None) -> dict:
        self.module.request = FakeRequest(payload)
        return payload or {}

    def test_items_endpoint_shape(self):
        self.module.request = FakeRequest()
        response = run(self.plugin.api_items())
        data = json.loads(response.body)
        self.assertIn("items", data)
        self.assertIn("categories", data)
        self.assertIn("effect_keys", data)
        self.assertTrue(data["items"])
        item = data["items"][0]
        for key in (
            "id",
            "name",
            "emoji",
            "category",
            "base_price",
            "price_fluctuation",
            "shop_enabled",
            "stock_min",
            "stock_max",
            "effect",
            "tradable",
            "enabled",
        ):
            self.assertIn(key, item)
        for key in ("satiety", "hydration", "energy", "health", "revive"):
            self.assertIn(key, item["effect"])

    def test_item_save_create_and_update(self):
        self.body({"id": "test_item", "name": "测试道具", "base_price": 10})
        data = json.loads(run(self.plugin.api_item_save()).body)
        self.assertTrue(data["saved"])
        self.assertTrue(data["created"])
        self.assertEqual(data["item"]["id"], "test_item")

        self.body({"id": "test_item", "name": "改名道具", "base_price": 20})
        data = json.loads(run(self.plugin.api_item_save()).body)
        self.assertFalse(data["created"])
        self.assertEqual(data["item"]["name"], "改名道具")

    def test_item_save_validation_error(self):
        self.body({"id": "bad", "name": ""})
        response = run(self.plugin.api_item_save())
        data = json.loads(response.body)
        self.assertEqual(data["status"], "error")
        self.assertIn("名称", data["message"])

    def test_item_delete(self):
        self.body({"id": "cat_food"})
        data = json.loads(run(self.plugin.api_item_delete()).body)
        self.assertTrue(data["deleted"])
        self.assertIsNone(self.plugin.world.registry.by_id("cat_food"))

        self.body({"id": "cat_food"})
        data = json.loads(run(self.plugin.api_item_delete()).body)
        self.assertEqual(data["status"], "error")

    def test_item_delete_requires_id(self):
        self.body({})
        data = json.loads(run(self.plugin.api_item_delete()).body)
        self.assertEqual(data["status"], "error")

    def test_items_reset(self):
        self.body({"id": "temp", "name": "临时"})
        run(self.plugin.api_item_save())
        data = json.loads(run(self.plugin.api_items_reset()).body)
        self.assertTrue(data["reset"])
        self.assertIsNone(self.plugin.world.registry.by_id("temp"))
        self.assertIsNotNone(self.plugin.world.registry.by_id("cat_food"))

    def test_shop_endpoint_shape(self):
        self.module.request = FakeRequest()
        data = json.loads(run(self.plugin.api_shop()).body)
        self.assertIn("date", data)
        self.assertIn("refresh_hour", data)
        self.assertTrue(data["entries"])
        entry = data["entries"][0]
        for key in (
            "item_id",
            "name",
            "emoji",
            "category",
            "price",
            "base_price",
            "stock",
            "stock_initial",
        ):
            self.assertIn(key, entry)

    def test_shop_refresh(self):
        self.body({})
        data = json.loads(run(self.plugin.api_shop_refresh()).body)
        self.assertTrue(data["entries"])

    def test_overview_shape(self):
        self.module.request = FakeRequest()
        data = json.loads(run(self.plugin.api_overview()).body)
        for key in (
            "players",
            "catgirls",
            "alive",
            "dead",
            "marriages",
            "listings",
            "total_coins",
            "shop_date",
            "top_players",
        ):
            self.assertIn(key, data)

    def test_players_grant(self):
        # 先让玩家存在
        self.call("neko_wallet", self.event())
        self.body({"player_id": "aiocqhttp:1001", "amount": 250})
        data = json.loads(run(self.plugin.api_players_grant()).body)
        self.assertEqual(data["coins"], 550)

    def test_players_grant_negative(self):
        self.call("neko_wallet", self.event())
        self.body({"player_id": "aiocqhttp:1001", "amount": -100})
        data = json.loads(run(self.plugin.api_players_grant()).body)
        self.assertEqual(data["coins"], 200)

    def test_players_grant_unknown(self):
        self.body({"player_id": "nobody", "amount": 10})
        data = json.loads(run(self.plugin.api_players_grant()).body)
        self.assertEqual(data["status"], "error")

    def test_players_grant_bad_amount(self):
        self.body({"player_id": "x", "amount": "abc"})
        data = json.loads(run(self.plugin.api_players_grant()).body)
        self.assertEqual(data["status"], "error")

    def test_endpoints_before_initialize_return_error(self):
        """插件尚未初始化时，面板 API 应返回错误而不是崩溃。"""
        self.plugin.world = None
        self.module.request = FakeRequest()
        for handler in (
            self.plugin.api_items,
            self.plugin.api_shop,
            self.plugin.api_overview,
        ):
            data = json.loads(run(handler()).body)
            self.assertEqual(data["status"], "error")


# ======================================================================
# 配置 Schema
# ======================================================================
class TestConfigSchema(unittest.TestCase):
    """`_conf_schema.json` 必须能被真实 AstrBot 接受（否则插件根本加载不了）。"""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self.tmp = Path(tempfile.mkdtemp(prefix="neko_han_cfg_"))
        os.chdir(self.tmp)

        def restore() -> None:
            os.chdir(self._cwd)
            shutil.rmtree(self.tmp, True)

        self.addCleanup(restore)

    def test_schema_accepted_by_astrbot(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text("utf-8"))
        # 若 schema 非法（例如使用了 4.26.6 不支持的 "dict" 类型），这里会抛异常
        config = AstrBotConfig(schema=schema)
        for section in (
            "economy",
            "survival",
            "tasks",
            "shop",
            "marriage",
            "display",
        ):
            self.assertIn(section, config)
        self.assertEqual(config["survival"]["max_satiety"], 100)
        self.assertEqual(config["display"]["card_mode"], "text")
        self.assertTrue(config["survival"]["death_enabled"])

    def test_schema_types_are_supported(self):
        """4.26.6 只接受这些类型；`dict` 会导致插件加载失败。"""
        allowed = {
            "int",
            "float",
            "bool",
            "string",
            "text",
            "list",
            "file",
            "object",
            "template_list",
        }
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text("utf-8"))

        def walk(node: dict[str, Any], path: str = "") -> None:
            for key, spec in node.items():
                if not isinstance(spec, dict):
                    continue
                where = f"{path}.{key}" if path else key
                if "type" in spec:
                    self.assertIn(
                        spec["type"], allowed, f"{where} 使用了不支持的类型"
                    )
                if isinstance(spec.get("items"), dict):
                    walk(spec["items"], where)

        walk(schema)


# ======================================================================
# 插件元数据（AstrBot 加载器的硬性要求）
# ======================================================================
class TestPluginMetadata(unittest.TestCase):
    """metadata.yaml 必须满足 AstrBot 的加载条件，否则插件根本不会被载入。"""

    @classmethod
    def setUpClass(cls) -> None:
        import yaml

        cls.metadata = yaml.safe_load(
            (PLUGIN_ROOT / "metadata.yaml").read_text("utf-8")
        )

    def test_required_keys_present(self):
        for key in ("name", "desc", "version", "author"):
            self.assertIn(key, self.metadata, f"metadata.yaml 缺少 {key}")
            self.assertTrue(str(self.metadata[key]).strip(), f"{key} 不能为空")

    def test_name_matches_plugin_directory(self):
        """AstrBot 要求插件目录名与 metadata 的 name 一致。"""
        self.assertEqual(self.metadata["name"], PLUGIN_ROOT.name)

    def test_name_is_importable_module_name(self):
        """name 必须是合法 Python 标识符（加载器用它做 importlib 路径）。"""
        import keyword

        name = self.metadata["name"]
        self.assertTrue(name.isidentifier(), f"{name} 不是合法标识符")
        self.assertFalse(keyword.iskeyword(name))
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_platform_metadata_split(self):
        """display 与 desc 必须分开维护：display_name 是展示名，desc 是简介。"""
        self.assertIn("display_name", self.metadata)
        self.assertNotEqual(
            self.metadata["display_name"], self.metadata["desc"]
        )

    def test_astrbot_version_specifier_is_satisfied(self):
        """声明的 AstrBot 版本范围必须能被当前安装版本满足。"""
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        from astrbot.core.config import VERSION

        spec_text = str(self.metadata.get("astrbot_version") or "")
        self.assertTrue(spec_text, "应声明 astrbot_version")
        specifier = SpecifierSet(spec_text)
        self.assertTrue(
            specifier.contains(Version(VERSION), prereleases=True),
            f"当前 AstrBot {VERSION} 不满足 {spec_text}",
        )

    def test_support_platforms_are_known(self):
        from astrbot.core.star.filter.platform_adapter_type import ADAPTER_NAME_2_TYPE

        for platform in self.metadata.get("support_platforms") or []:
            with self.subTest(platform=platform):
                self.assertIn(platform, ADAPTER_NAME_2_TYPE)

    def test_i18n_files_are_valid(self):
        """i18n 文件必须是 JSON 对象，且包含面板标题键。"""
        i18n_dir = PLUGIN_ROOT / ".astrbot-plugin" / "i18n"
        self.assertTrue(i18n_dir.is_dir(), "缺少 .astrbot-plugin/i18n 目录")
        locales = sorted(p for p in i18n_dir.iterdir() if p.suffix == ".json")
        self.assertTrue(locales, "至少需要一个 i18n 文件")
        for path in locales:
            with self.subTest(locale=path.stem):
                data = json.loads(path.read_text("utf-8"))
                self.assertIsInstance(data, dict)
                self.assertIn("metadata", data)
                self.assertIn("pages", data)
                self.assertIn("title", data["pages"]["panel"])

    def test_readme_exists(self):
        self.assertTrue((PLUGIN_ROOT / "README.md").exists(), "缺少 README.md")

    def test_passes_real_astrbot_loader_validation(self):
        """直接调用 AstrBot 的加载器校验函数，确认插件目录能被识别。"""
        from astrbot.core.star.star_manager import PluginManager

        resolved = PluginManager._get_plugin_dir_name_from_metadata(str(PLUGIN_ROOT))
        self.assertEqual(resolved, "neko_han")
        # 不应抛出异常
        PluginManager._validate_importable_name(resolved)

    def test_astrbot_version_range_check_via_loader(self):
        from astrbot.core.star.star_manager import PluginManager

        ok, message = PluginManager._validate_astrbot_version_specifier(
            str(self.metadata.get("astrbot_version") or "")
        )
        self.assertTrue(ok, message)


# ======================================================================
# 插件面板静态资源
# ======================================================================
class TestPanelPageFiles(unittest.TestCase):
    def test_page_entry_exists(self):
        """AstrBot 只会扫描 pages/<name>/index.html。"""
        entry = PLUGIN_ROOT / "pages" / "panel" / "index.html"
        self.assertTrue(entry.exists(), "缺少 pages/panel/index.html")
        html = entry.read_text("utf-8")
        self.assertIn("./app.js", html)
        self.assertIn("./style.css", html)

    def test_no_external_resources(self):
        """面板运行在无同源权限的沙箱 iframe 中，不能依赖外部 CDN。"""
        for name in ("index.html", "app.js", "style.css"):
            text = (PLUGIN_ROOT / "pages" / "panel" / name).read_text("utf-8")
            for bad in ("http://", "https://", "//cdn", "unpkg", "jsdelivr"):
                self.assertNotIn(
                    bad, text, f"{name} 中不应出现外部资源引用: {bad}"
                )

    def test_only_post_mutations_in_panel(self):
        """桥接 SDK 只有 apiGet/apiPost，不允许 apiPut/apiDelete。"""
        js = (PLUGIN_ROOT / "pages" / "panel" / "app.js").read_text("utf-8")
        self.assertNotIn("apiPut", js)
        self.assertNotIn("apiDelete", js)

    def test_panel_calls_match_backend_endpoints(self):
        """面板调用的 endpoint 必须与后端注册的路由一一对应。"""
        js = (PLUGIN_ROOT / "pages" / "panel" / "app.js").read_text("utf-8")
        expected = [
            "items",
            "items/save",
            "items/delete",
            "items/reset",
            "shop",
            "shop/refresh",
            "overview",
            "players/grant",
        ]
        for endpoint in expected:
            with self.subTest(endpoint=endpoint):
                called = (
                    f"'{endpoint}'" in js or f'"{endpoint}"' in js
                )
                self.assertTrue(
                    called, f"面板未调用后端 endpoint: {endpoint}"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
