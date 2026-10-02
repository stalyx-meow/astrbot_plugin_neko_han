"""Neko_Han 核心逻辑单元测试。

不依赖 AstrBot，直接用 `python3 -m unittest discover tests` 运行。
"""

from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import GameError, NekoWorld  # noqa: E402
from core.defs import DEFAULT_ITEMS, TASK_POOL  # noqa: E402
from core.items import normalize_item  # noqa: E402
from core.store import Store  # noqa: E402
from core.util import game_day, now_ts  # noqa: E402
from core.world import validate_name  # noqa: E402


def make_world(config: dict | None = None, seed: int = 1234) -> NekoWorld:
    """在临时目录里建一个独立世界。"""
    tmp = tempfile.mkdtemp(prefix="neko_han_test_")
    world = NekoWorld(tmp, config or {}, rng=random.Random(seed))
    world.load()
    world._test_tmp = tmp  # type: ignore[attr-defined]
    return world


class WorldTestCase(unittest.TestCase):
    """提供常用夹具与清理逻辑。"""

    config: dict = {}

    def setUp(self) -> None:
        self.world = make_world(self.config)
        self.addCleanup(shutil.rmtree, self.world._test_tmp, True)  # type: ignore[attr-defined]
        self.today = self.world.today()

    def make_player(self, uid: str = "1001", name: str = "阿猫"):
        player, _ = self.world.ensure_player("aiocqhttp", uid, name, f"umo:{uid}")
        return player

    def make_catgirl(self, player, name: str = "小奶油"):
        return self.world.adopt(player, name)


# ======================================================================
# 存储
# ======================================================================
class TestStore(WorldTestCase):
    def test_roundtrip_persistence(self):
        """存档写盘后重新载入应完全一致。"""
        player = self.make_player()
        cat = self.make_catgirl(player, "布丁")
        self.world.save_now()

        again = NekoWorld(self.world._test_tmp, {})  # type: ignore[attr-defined]
        again.load()
        self.assertIn(player["key"], again.store.players)
        self.assertEqual(again.store.players[player["key"]]["coins"], player["coins"])
        self.assertEqual(again.store.catgirls[cat["id"]]["name"], "布丁")

    def test_items_seeded_on_first_load(self):
        """首次载入应写入默认道具表。"""
        self.assertEqual(len(self.world.registry.all()), len(DEFAULT_ITEMS))
        self.assertTrue((Path(self.world._test_tmp) / "items.json").exists())  # type: ignore[attr-defined]

    def test_corrupt_state_recovers(self):
        """存档损坏时不应崩溃，而是备份并使用默认数据。"""
        path = Path(self.world._test_tmp) / "state.json"  # type: ignore[attr-defined]
        path.write_text("{ this is not json", encoding="utf-8")
        store = Store(self.world._test_tmp)  # type: ignore[attr-defined]
        store.load()  # 不应抛异常
        self.assertEqual(store.state["players"], {})
        self.assertTrue(path.with_suffix(".json.corrupt").exists())

    def test_missing_fields_repaired(self):
        """存档缺字段时应自动补齐。"""
        path = Path(self.world._test_tmp) / "state.json"  # type: ignore[attr-defined]
        path.write_text(json.dumps({"players": {"a:b": {"coins": 5}}}), encoding="utf-8")
        store = Store(self.world._test_tmp)  # type: ignore[attr-defined]
        store.load()
        self.assertIn("catgirls", store.state)
        self.assertIn("catgirl", store.state["counters"])
        self.assertEqual(store.state["players"]["a:b"]["coins"], 5)


# ======================================================================
# 道具定义
# ======================================================================
class TestItems(WorldTestCase):
    def test_effect_normalized(self):
        item = normalize_item({"id": "x1", "name": "测试", "effect": {"satiety": 5}})
        self.assertEqual(item["effect"]["satiety"], 5)
        self.assertEqual(item["effect"]["hydration"], 0)
        self.assertFalse(item["effect"]["revive"])

    def test_id_is_slugified(self):
        """为了面板输入方便，ID 会被规整为小写下划线形式。"""
        item = normalize_item({"id": "A B", "name": "x"})
        self.assertEqual(item["id"], "a_b")
        item = normalize_item({"id": "Some-Item", "name": "x"})
        self.assertEqual(item["id"], "some_item")

    def test_invalid_inputs_rejected(self):
        cases = [
            {"id": "x", "name": "太短"},  # ID 长度不足
            {"id": "中文ID", "name": "x"},  # 非 ASCII
            {"id": "ok_id", "name": ""},  # 缺名字
            {"id": "ok_id", "name": "x", "category": "nope"},  # 非法分类
            {"id": "ok_id", "name": "x", "base_price": -5},  # 负价格
            {"id": "ok_id", "name": "x", "stock_min": 9, "stock_max": 2},  # 库存倒挂
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(GameError):
                    normalize_item(payload)

    def test_price_fluctuation_clamped(self):
        item = normalize_item(
            {"id": "ok_id", "name": "x", "price_fluctuation": 5.0}
        )
        self.assertEqual(item["price_fluctuation"], 1.0)

    def test_save_update_delete_reset(self):
        registry = self.world.registry
        item, created = registry.save(
            {"id": "cookie", "name": "小饼干", "base_price": 12, "effect": {"satiety": 10}}
        )
        self.assertTrue(created)
        self.assertEqual(registry.resolve("小饼干")["id"], "cookie")

        item, created = registry.save({"id": "cookie", "name": "小饼干", "base_price": 30})
        self.assertFalse(created)
        self.assertEqual(registry.by_id("cookie")["base_price"], 30)

        registry.delete("cookie")
        self.assertIsNone(registry.by_id("cookie"))
        with self.assertRaises(GameError):
            registry.delete("cookie")

        registry.reset()
        self.assertEqual(len(registry.all()), len(DEFAULT_ITEMS))

    def test_generated_id_when_missing(self):
        item, created = self.world.registry.save({"name": "没有ID的道具", "base_price": 1})
        self.assertTrue(created)
        self.assertTrue(item["id"])

    def test_disabled_items_hidden(self):
        self.world.registry.save(
            {"id": "hidden", "name": "隐藏道具", "enabled": False, "shop_enabled": True}
        )
        self.assertIsNone(self.world.registry.resolve("隐藏道具"))
        self.assertIsNotNone(
            self.world.registry.resolve("隐藏道具", include_disabled=True)
        )
        self.assertNotIn(
            "hidden", [i["id"] for i in self.world.registry.shop_items()]
        )


# ======================================================================
# 认养与命名
# ======================================================================
class TestAdoption(WorldTestCase):
    def test_adopt_and_name(self):
        player = self.make_player()
        cat = self.make_catgirl(player, "小奶油")
        self.assertEqual(cat["name"], "小奶油")
        self.assertEqual(cat["owner"], player["key"])
        self.assertIn(cat["id"], player["catgirls"])
        self.assertTrue(cat["alive"])
        self.assertEqual(cat["satiety"], 100)

    def test_random_name_when_omitted(self):
        player = self.make_player()
        cat = self.world.adopt(player, "")
        self.assertTrue(cat["name"])

    def test_duplicate_and_invalid_names(self):
        player = self.make_player()
        self.make_catgirl(player, "团子")
        with self.assertRaises(GameError):
            self.world.adopt(player, "团子")
        for bad in ["", "   ", "有 空格", "a" * 20]:
            with self.subTest(bad=bad):
                with self.assertRaises(GameError):
                    validate_name(bad)

    def test_adoption_limit(self):
        world = make_world({"economy": {"max_catgirls_per_player": 2}})
        player, _ = world.ensure_player("aiocqhttp", "9", "限额", "")
        world.adopt(player, "一")
        world.adopt(player, "二")
        with self.assertRaises(GameError):
            world.adopt(player, "三")

    def test_dead_catgirl_frees_slot(self):
        """离世的猫娘不占名额，玩家可以直接补养。"""
        world = make_world({"economy": {"max_catgirls_per_player": 2}})
        player, _ = world.ensure_player("aiocqhttp", "77", "补养", "")
        first = world.adopt(player, "一猫")
        world.adopt(player, "二猫")
        with self.assertRaises(GameError):
            world.adopt(player, "三猫")

        world._kill_catgirl(first, "测试")
        # 名额释放
        third = world.adopt(player, "三猫")
        self.assertEqual(third["name"], "三猫")

        slots = world.catgirl_slots(player)
        self.assertEqual(slots["alive"], 2)
        self.assertEqual(slots["dead"], 1)
        self.assertEqual(slots["total"], 3)
        self.assertEqual(slots["remaining"], 0)

    def test_catgirl_slots_counts(self):
        world = make_world({"economy": {"max_catgirls_per_player": 3}})
        player, _ = world.ensure_player("aiocqhttp", "78", "计数", "")
        self.assertEqual(
            world.catgirl_slots(player),
            {"total": 0, "alive": 0, "dead": 0, "quota": 3, "remaining": 3},
        )
        a = world.adopt(player, "A")
        world.adopt(player, "B")
        self.assertEqual(world.catgirl_slots(player)["remaining"], 1)
        world._kill_catgirl(a, "测试")
        self.assertEqual(
            world.catgirl_slots(player),
            {"total": 2, "alive": 1, "dead": 1, "quota": 3, "remaining": 2},
        )

    def test_quota_reflects_config(self):
        world = make_world({"economy": {"max_catgirls_per_player": 7}})
        self.assertEqual(world.catgirl_quota(), 7)
        # 非法/缺失配置回退为默认 3
        self.assertEqual(make_world({}).catgirl_quota(), 3)
        self.assertEqual(
            make_world({"economy": {"max_catgirls_per_player": 0}}).catgirl_quota(), 1
        )

    def test_resolve_by_id_name_and_index(self):
        player = self.make_player()
        first = self.make_catgirl(player, "一花")
        second = self.make_catgirl(player, "二花")
        self.assertEqual(self.world.resolve_catgirl(player, "一花")["id"], first["id"])
        self.assertEqual(self.world.resolve_catgirl(player, second["id"])["id"], second["id"])
        self.assertEqual(self.world.resolve_catgirl(player, "1")["id"], first["id"])
        self.assertEqual(self.world.resolve_catgirl(player, "2")["id"], second["id"])
        with self.assertRaises(GameError):
            self.world.resolve_catgirl(player, "不存在")

    def test_resolve_single_catgirl_without_arg(self):
        player = self.make_player()
        cat = self.make_catgirl(player, "独生女")
        self.assertEqual(self.world.resolve_catgirl(player, "")["id"], cat["id"])

    def test_rename(self):
        player = self.make_player()
        cat = self.make_catgirl(player, "旧名")
        self.make_catgirl(player, "占用")
        self.world.rename(player, cat, "新名")
        self.assertEqual(cat["name"], "新名")
        with self.assertRaises(GameError):
            self.world.rename(player, cat, "占用")


# ======================================================================
# 生存
# ======================================================================
class TestSurvival(WorldTestCase):
    def _rewind(self, cat, days: int) -> None:
        """把猫娘的上次结算日往前推，模拟经过 days 天。"""
        cat["last_tick"] = (self.today - timedelta(days=days)).isoformat()

    def _set_birth(self, cat, days: int) -> None:
        cat["birth_day"] = (self.today - timedelta(days=days)).isoformat()

    def test_daily_decay_applies_per_day(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self._set_birth(cat, 10)
        self._rewind(cat, 2)
        self.world.settle_catgirl(cat, today=self.today)
        # 默认每天 -35 饱食 / -45 水分
        self.assertEqual(cat["satiety"], 100 - 70)
        self.assertEqual(cat["hydration"], 100 - 90)
        self.assertEqual(cat["last_tick"], self.today.isoformat())

    def test_settle_is_idempotent(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self._set_birth(cat, 10)
        self._rewind(cat, 3)
        self.world.settle_catgirl(cat, today=self.today)
        snapshot = (cat["satiety"], cat["hydration"])
        self.world.settle_catgirl(cat, today=self.today)
        self.assertEqual((cat["satiety"], cat["hydration"]), snapshot)

    def test_starvation_damages_health(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self._set_birth(cat, 10)
        cat["satiety"] = 0
        cat["hydration"] = 0
        self._rewind(cat, 1)
        self.world.settle_catgirl(cat, today=self.today)
        self.assertEqual(cat["health"], 100 - 25)

    def test_health_recovers_when_well_fed(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self._set_birth(cat, 10)
        cat["health"] = 50
        self._rewind(cat, 1)
        self.world.settle_catgirl(cat, today=self.today)
        self.assertEqual(cat["health"], 60)

    def test_energy_regenerates_to_cap(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self._set_birth(cat, 10)
        cat["energy"] = 0
        self._rewind(cat, 1)
        self.world.settle_catgirl(cat, today=self.today)
        self.assertEqual(cat["energy"], 100)

    def test_death_when_health_depleted(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self._set_birth(cat, 10)  # 超过保护期
        cat["satiety"] = 0
        cat["hydration"] = 0
        cat["health"] = 20
        self._rewind(cat, 1)
        event = self.world.settle_catgirl(cat, today=self.today)
        self.assertIsNotNone(event)
        self.assertFalse(cat["alive"])
        self.assertEqual(cat["health"], 0)
        self.assertEqual(event["owner"], player["key"])

    def test_grace_period_prevents_death(self):
        world = make_world({"survival": {"grace_days": 3}})
        player, _ = world.ensure_player("aiocqhttp", "5", "新人", "")
        cat = world.adopt(player, "宝宝")
        today = world.today()
        cat["satiety"] = 0
        cat["hydration"] = 0
        cat["health"] = 10
        cat["last_tick"] = (today - timedelta(days=1)).isoformat()
        world.settle_catgirl(cat, today=today)
        self.assertTrue(cat["alive"])
        self.assertEqual(cat["health"], 1)

    def test_death_disabled(self):
        world = make_world({"survival": {"death_enabled": False}})
        player, _ = world.ensure_player("aiocqhttp", "6", "和平", "")
        cat = world.adopt(player, "不死")
        today = world.today()
        cat["birth_day"] = (today - timedelta(days=30)).isoformat()
        cat["satiety"] = 0
        cat["hydration"] = 0
        cat["health"] = 5
        cat["last_tick"] = (today - timedelta(days=1)).isoformat()
        world.settle_catgirl(cat, today=today)
        self.assertTrue(cat["alive"])
        self.assertEqual(cat["health"], 1)

    def test_offline_cap(self):
        world = make_world({"survival": {"offline_decay_cap_days": 2}})
        player, _ = world.ensure_player("aiocqhttp", "7", "停机", "")
        cat = world.adopt(player, "长眠")
        today = world.today()
        cat["birth_day"] = (today - timedelta(days=99)).isoformat()
        cat["last_tick"] = (today - timedelta(days=100)).isoformat()
        world.settle_catgirl(cat, today=today)
        # 只结算 2 天：100 - 2*35 = 30
        self.assertEqual(cat["satiety"], 30)

    def test_feed_and_drink(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        cat["satiety"] = 10
        cat["hydration"] = 10
        self.world._inv_add(player, "cat_food", 2)
        self.world._inv_add(player, "water", 1)

        result = self.world.feed_catgirl(player, cat, "猫粮")
        self.assertEqual(cat["satiety"], 40)
        self.assertEqual(result["applied"]["satiety"], 30)
        self.assertEqual(self.world._inv_count(player, "cat_food"), 1)

        self.world.feed_catgirl(player, cat, "清水")
        self.assertEqual(cat["hydration"], 45)
        self.assertEqual(self.world._inv_count(player, "water"), 0)

    def test_effects_capped_at_max(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._inv_add(player, "premium_can", 1)
        result = self.world.feed_catgirl(player, cat, "高级罐头")
        # 已经满值，实际生效为 0
        self.assertEqual(cat["satiety"], 100)
        self.assertNotIn("satiety", result["applied"])

    def test_feed_rejects_non_food(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._inv_add(player, "medkit", 1)
        with self.assertRaises(GameError):
            self.world.feed_catgirl(player, cat, "医疗包")
        # 校验失败不应消耗道具
        self.assertEqual(self.world._inv_count(player, "medkit"), 1)

    def test_use_without_item(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        with self.assertRaises(GameError):
            self.world.use_item(player, cat, "猫粮")

    def test_revive(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._kill_catgirl(cat, "测试")
        self.assertFalse(cat["alive"])
        self.world._inv_add(player, "revive_grass", 1)
        result = self.world.use_item(player, cat, "复活草")
        self.assertTrue(result["revived"])
        self.assertTrue(cat["alive"])
        self.assertEqual(cat["health"], 50)

    def test_non_revive_item_on_dead_catgirl(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._kill_catgirl(cat, "测试")
        self.world._inv_add(player, "cat_food", 1)
        with self.assertRaises(GameError):
            self.world.use_item(player, cat, "猫粮")

    def test_death_withdraws_listings(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._inv_add(player, "yarn_ball", 2)
        self.world.sell_listing(player, cat, "毛线球", 2, 50)
        self.assertEqual(len(self.world.store.listings), 1)
        self.world._kill_catgirl(cat, "测试")
        self.assertEqual(len(self.world.store.listings), 0)
        # 道具退回仓库
        self.assertEqual(self.world._inv_count(player, "yarn_ball"), 2)


# ======================================================================
# 经济
# ======================================================================
class TestEconomy(WorldTestCase):
    def test_starting_coins_once(self):
        player = self.make_player()
        self.assertEqual(player["coins"], 300)
        player["coins"] = 999
        again, created = self.world.ensure_player("aiocqhttp", "1001", "改名了", "")
        self.assertFalse(created)
        self.assertEqual(again["coins"], 999)
        self.assertEqual(again["name"], "改名了")

    def test_spend_insufficient(self):
        player = self.make_player()
        with self.assertRaises(GameError):
            self.world.spend_coins(player, 10_000)

    def test_daily_allowance_once_per_day(self):
        player = self.make_player()
        gained = self.world.claim_daily(player, today=self.today)
        self.assertEqual(gained, 80)
        self.assertEqual(player["coins"], 380)
        with self.assertRaises(GameError):
            self.world.claim_daily(player, today=self.today)
        # 第二天可以再领
        self.world.claim_daily(player, today=self.today + timedelta(days=1))

    def test_inventory_helpers(self):
        player = self.make_player()
        self.world._inv_add(player, "cat_food", 3)
        self.assertEqual(self.world._inv_count(player, "cat_food"), 3)
        self.world._inv_remove(player, "cat_food", 2)
        self.assertEqual(self.world._inv_count(player, "cat_food"), 1)
        self.world._inv_remove(player, "cat_food", 1)
        self.assertNotIn("cat_food", player["inventory"])
        with self.assertRaises(GameError):
            self.world._inv_remove(player, "cat_food", 1)

    def test_inventory_view_hides_disabled(self):
        player = self.make_player()
        self.world._inv_add(player, "cat_food", 1)
        self.world.registry.save({"id": "cat_food", "name": "猫粮", "enabled": False})
        self.assertEqual(self.world.inventory_view(player), [])


# ======================================================================
# 任务
# ======================================================================
class TestTasks(WorldTestCase):
    def test_daily_generation(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slots = self.world.task_slots(cat)
        self.assertEqual(len(slots), 3)  # 默认每天 3 个
        keys = [s["key"] for s in slots]
        self.assertEqual(len(set(keys)), len(keys), "任务不应重复")
        for slot in slots:
            self.assertIn(slot["status"], {"pending"})
            self.assertGreater(slot["reward"], 0)
            self.assertGreaterEqual(slot["energy"], 0)

    def test_generation_stable_within_day(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        first = [s["key"] for s in self.world.task_slots(cat)]
        second = [s["key"] for s in self.world.task_slots(cat)]
        self.assertEqual(first, second)

    def test_start_consumes_energy(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slot = self.world.task_slots(cat)[0]
        self.world.start_task(cat, slot["slot"])
        self.assertEqual(cat["energy"], 100 - slot["energy"])
        self.assertEqual(slot["status"], "active")
        self.assertGreater(slot["finish_at"], now_ts())

    def test_start_requires_energy(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slot = self.world.task_slots(cat)[0]
        cat["energy"] = 0
        with self.assertRaises(GameError):
            self.world.start_task(cat, slot["slot"])

    def test_only_one_active_without_parallel(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slots = self.world.task_slots(cat)
        self.world.start_task(cat, slots[0]["slot"])
        with self.assertRaises(GameError):
            self.world.start_task(cat, slots[1]["slot"])

    def test_parallel_allowed_by_config(self):
        world = make_world({"tasks": {"allow_parallel_tasks": True}})
        player, _ = world.ensure_player("aiocqhttp", "11", "并行", "")
        cat = world.adopt(player, "多多")
        slots = world.task_slots(cat)
        world.start_task(cat, slots[0]["slot"])
        world.start_task(cat, slots[1]["slot"])
        self.assertEqual(len(world.active_slots(cat)), 2)

    def test_claim_before_finish_rejected(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slot = self.world.task_slots(cat)[0]
        self.world.start_task(cat, slot["slot"])
        with self.assertRaises(GameError):
            self.world.claim_task(player, cat, slot["slot"])

    def test_claim_pays_reward(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slot = self.world.task_slots(cat)[0]
        self.world.start_task(cat, slot["slot"])
        slot["finish_at"] = now_ts() - 1  # 模拟已完成
        before = player["coins"]
        claimed = self.world.claim_task(player, cat, slot["slot"])
        self.assertEqual(len(claimed), 1)
        self.assertEqual(player["coins"], before + slot["reward"])
        self.assertEqual(slot["status"], "done")
        self.assertEqual(cat["stats"]["tasks_done"], 1)

    def test_claim_all(self):
        world = make_world({"tasks": {"allow_parallel_tasks": True}})
        player, _ = world.ensure_player("aiocqhttp", "12", "全部", "")
        cat = world.adopt(player, "勤劳")
        slots = world.task_slots(cat)
        expected = 0
        for slot in slots:
            world.start_task(cat, slot["slot"])
            slot["finish_at"] = now_ts() - 1
            expected += slot["reward"]
        before = player["coins"]
        claimed = world.claim_task(player, cat, None)
        self.assertEqual(len(claimed), len(slots))
        self.assertEqual(player["coins"], before + expected)

    def test_claim_twice_rejected(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slot = self.world.task_slots(cat)[0]
        self.world.start_task(cat, slot["slot"])
        slot["finish_at"] = now_ts() - 1
        self.world.claim_task(player, cat, slot["slot"])
        with self.assertRaises(GameError):
            self.world.claim_task(player, cat, slot["slot"])

    def test_active_task_carried_across_days(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        slot = self.world.task_slots(cat)[0]
        self.world.start_task(cat, slot["slot"])
        carried_key = slot["key"]

        # 模拟进入第二天
        tomorrow = self.today + timedelta(days=1)
        slots = self.world.task_slots(cat)
        self.world.ensure_daily_tasks(cat, today=tomorrow)
        slots_after = self.world.task_slots(cat)
        active = [s for s in slots_after if s["status"] == "active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["key"], carried_key)
        # 新任务补足到配置数量
        pending = [s for s in slots_after if s["status"] == "pending"]
        self.assertEqual(len(pending), 3)

    def test_task_pool_rewards_are_scaled(self):
        world = make_world({"economy": {"task_reward_multiplier": 3.0}})
        player, _ = world.ensure_player("aiocqhttp", "13", "高倍", "")
        cat = world.adopt(player, "暴富")
        slots = world.task_slots(cat)
        by_key = {t["key"]: t["reward"] for t in TASK_POOL}
        for slot in slots:
            base = by_key[slot["key"]] * 3.0
            self.assertGreaterEqual(slot["reward"], base * 0.79)
            self.assertLessEqual(slot["reward"], base * 1.21)


# ======================================================================
# 结婚
# ======================================================================
class TestMarriage(WorldTestCase):
    def _pair(self):
        a = self.make_player("2001", "甲")
        b = self.make_player("2002", "乙")
        return a, self.make_catgirl(a, "阿花"), b, self.make_catgirl(b, "阿草")

    def test_propose_and_accept(self):
        a, cat_a, b, cat_b = self._pair()
        proposal, target = self.world.propose(a, cat_a, "阿草")
        self.assertEqual(target["id"], cat_b["id"])
        self.assertEqual(len(self.world.received_proposals(b)), 1)
        self.assertEqual(len(self.world.received_proposals(a)), 0)

        _, marriage = self.world.accept_proposal(b, proposal["id"])
        self.assertIsNotNone(self.world.marriage_of(cat_a["id"]))
        self.assertEqual(self.world.partner_of(cat_a["id"])["id"], cat_b["id"])
        self.assertEqual(self.world.partner_of(cat_b["id"])["id"], cat_a["id"])
        self.assertEqual(marriage["id"], "M1")

    def test_reject(self):
        a, cat_a, b, cat_b = self._pair()
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        self.world.reject_proposal(b, proposal["id"])
        self.assertIsNone(self.world.marriage_of(cat_a["id"]))
        self.assertEqual(self.world.received_proposals(b), [])

    def test_only_target_owner_can_accept(self):
        a, cat_a, b, cat_b = self._pair()
        c = self.make_player("2003", "丙")
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        with self.assertRaises(GameError):
            self.world.accept_proposal(c, proposal["id"])
        with self.assertRaises(GameError):
            self.world.accept_proposal(a, proposal["id"])  # 发起者自己也不行

    def test_cannot_propose_to_self(self):
        a, cat_a, b, cat_b = self._pair()
        with self.assertRaises(GameError):
            self.world.propose(a, cat_a, "阿花")

    def test_duplicate_proposal_rejected(self):
        a, cat_a, b, cat_b = self._pair()
        self.world.propose(a, cat_a, "阿草")
        with self.assertRaises(GameError):
            self.world.propose(a, cat_a, "阿草")

    def test_no_polygamy_by_default(self):
        a, cat_a, b, cat_b = self._pair()
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        self.world.accept_proposal(b, proposal["id"])
        c = self.make_player("2004", "丁")
        cat_c = self.make_catgirl(c, "阿树")
        with self.assertRaises(GameError):
            self.world.propose(c, cat_c, "阿花")

    def test_self_marriage_option(self):
        world = make_world({"marriage": {"self_marriage_allowed": True}})
        player, _ = world.ensure_player("aiocqhttp", "21", "自恋", "")
        one = world.adopt(player, "甲猫")
        two = world.adopt(player, "乙猫")
        proposal, _ = world.propose(player, one, "乙猫")
        world.accept_proposal(player, proposal["id"])
        self.assertEqual(world.partner_of(one["id"])["id"], two["id"])

    def test_proposal_expires(self):
        a, cat_a, b, cat_b = self._pair()
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        proposal["created"] = now_ts() - 25 * 3600  # 超过默认 24 小时
        self.assertEqual(self.world.expire_proposals(), 1)
        self.assertEqual(self.world.received_proposals(b), [])
        with self.assertRaises(GameError):
            self.world.accept_proposal(b, proposal["id"])

    def test_divorce_costs_coins(self):
        a, cat_a, b, cat_b = self._pair()
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        self.world.accept_proposal(b, proposal["id"])
        a["coins"] = 1000
        marriage, partner = self.world.divorce(a, cat_a)
        self.assertEqual(a["coins"], 500)
        self.assertIsNone(self.world.marriage_of(cat_a["id"]))
        self.assertEqual(partner["id"], cat_b["id"])

    def test_divorce_requires_coins(self):
        a, cat_a, b, cat_b = self._pair()
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        self.world.accept_proposal(b, proposal["id"])
        a["coins"] = 10
        with self.assertRaises(GameError):
            self.world.divorce(a, cat_a)
        self.assertIsNotNone(self.world.marriage_of(cat_a["id"]))

    def test_divorce_without_marriage(self):
        a, cat_a, b, cat_b = self._pair()
        with self.assertRaises(GameError):
            self.world.divorce(a, cat_a)

    def test_dead_catgirl_cannot_marry(self):
        a, cat_a, b, cat_b = self._pair()
        self.world._kill_catgirl(cat_b, "测试")
        with self.assertRaises(GameError):
            self.world.propose(a, cat_a, "阿草")


# ======================================================================
# 商城与市场
# ======================================================================
class TestMarket(WorldTestCase):
    def test_official_shop_generates_and_is_stable(self):
        shop = self.world.ensure_official_shop()
        self.assertTrue(shop["date"])
        self.assertTrue(shop["entries"])
        first = json.dumps(shop["entries"], sort_keys=True)
        again = self.world.ensure_official_shop()
        self.assertEqual(json.dumps(again["entries"], sort_keys=True), first)

    def test_shop_size_respected(self):
        world = make_world({"shop": {"shop_size": 3}})
        shop = world.ensure_official_shop()
        self.assertEqual(len(shop["entries"]), 3)

    def test_prices_within_fluctuation(self):
        """价格必须落在 base ± fluctuation 区间内。"""
        for seed in range(30):
            world = make_world(seed=seed)
            for row in world.shop_view():
                base = row["base_price"]
                item = row["item"]
                if base <= 0:
                    continue
                spread = item["price_fluctuation"]
                low = base * (1 - spread)
                high = base * (1 + spread)
                with self.subTest(seed=seed, item=item["id"], price=row["price"]):
                    self.assertGreaterEqual(row["price"], int(low) - 1)
                    self.assertLessEqual(row["price"], int(high) + 1)

    def test_shop_refresh_changes_rotation(self):
        world = make_world(seed=7)
        before = json.dumps(world.ensure_official_shop()["entries"], sort_keys=True)
        changed = False
        for _ in range(10):
            world.refresh_official_shop()
            after = json.dumps(world.ensure_official_shop()["entries"], sort_keys=True)
            if after != before:
                changed = True
                break
        self.assertTrue(changed, "多次刷新后商城内容应发生变化")

    def test_buy_from_shop(self):
        player = self.make_player()
        row = self.world.shop_view()[0]
        item = row["item"]
        before = player["coins"]
        result = self.world.buy_official(player, item["id"], 2)
        self.assertEqual(result["qty"], 2)
        self.assertEqual(player["coins"], before - result["total"])
        self.assertEqual(self.world._inv_count(player, item["id"]), 2)

    def test_buy_insufficient_coins(self):
        player = self.make_player()
        player["coins"] = 0
        row = self.world.shop_view()[0]
        with self.assertRaises(GameError):
            self.world.buy_official(player, row["item"]["id"], 1)
        self.assertEqual(self.world._inv_count(player, row["item"]["id"]), 0)

    def test_buy_respects_stock(self):
        player = self.make_player()
        player["coins"] = 100000
        shop = self.world.ensure_official_shop()
        entry = next(e for e in shop["entries"] if e["stock"] >= 0)
        entry["stock"] = 1
        with self.assertRaises(GameError):
            self.world.buy_official(player, entry["item_id"], 5)
        self.world.buy_official(player, entry["item_id"], 1)
        with self.assertRaises(GameError):
            self.world.buy_official(player, entry["item_id"], 1)

    def test_buy_item_not_in_shop(self):
        player = self.make_player()
        shop_ids = {e["item_id"] for e in self.world.ensure_official_shop()["entries"]}
        missing = next(
            i["id"] for i in self.world.registry.all() if i["id"] not in shop_ids
        )
        with self.assertRaises(GameError):
            self.world.buy_official(player, missing, 1)

    def test_listing_escrow_and_cancel(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._inv_add(player, "yarn_ball", 3)
        result = self.world.sell_listing(player, cat, "毛线球", 2, 60)
        listing = result["listing"]
        # 道具进入托管
        self.assertEqual(self.world._inv_count(player, "yarn_ball"), 1)
        self.assertEqual(listing["id"], "L1")

        self.world.cancel_listing(player, listing["id"])
        self.assertEqual(self.world._inv_count(player, "yarn_ball"), 3)
        self.assertEqual(self.world.my_listings(player), [])

    def test_listing_requires_stock(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        with self.assertRaises(GameError):
            self.world.sell_listing(player, cat, "毛线球", 1, 60)

    def test_listing_rejects_non_tradable(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world.registry.save(
            {"id": "bound", "name": "绑定物", "tradable": False, "base_price": 10}
        )
        self.world._inv_add(player, "bound", 1)
        with self.assertRaises(GameError):
            self.world.sell_listing(player, cat, "绑定物", 1, 10)

    def test_listing_limit(self):
        world = make_world({"economy": {"max_listings_per_player": 1}})
        player, _ = world.ensure_player("aiocqhttp", "31", "限量", "")
        cat = world.adopt(player, "摆摊")
        world._inv_add(player, "yarn_ball", 4)
        world.sell_listing(player, cat, "毛线球", 1, 10)
        with self.assertRaises(GameError):
            world.sell_listing(player, cat, "毛线球", 1, 10)

    def test_market_buy_transfers_coins_and_fee(self):
        seller = self.make_player("3001", "卖家")
        buyer = self.make_player("3002", "买家")
        cat = self.make_catgirl(seller, "小摊")
        self.world._inv_add(seller, "yarn_ball", 3)
        result = self.world.sell_listing(seller, cat, "毛线球", 3, 100)
        listing = result["listing"]

        seller["coins"] = 0
        buyer["coins"] = 1000
        trade = self.world.buy_listing(buyer, listing["id"], 2)

        self.assertEqual(trade["total"], 200)
        self.assertEqual(trade["fee"], 10)  # 默认 5%
        self.assertEqual(trade["seller_income"], 190)
        self.assertEqual(buyer["coins"], 800)
        self.assertEqual(seller["coins"], 190)
        self.assertEqual(self.world._inv_count(buyer, "yarn_ball"), 2)
        self.assertEqual(listing["qty"], 1)

    def test_market_buy_whole_listing_removes_it(self):
        seller = self.make_player("3003", "卖家2")
        buyer = self.make_player("3004", "买家2")
        cat = self.make_catgirl(seller, "小摊2")
        self.world._inv_add(seller, "yarn_ball", 2)
        listing = self.world.sell_listing(seller, cat, "毛线球", 2, 10)["listing"]
        buyer["coins"] = 1000
        self.world.buy_listing(buyer, listing["id"], 2)
        self.assertEqual(self.world.store.listings, [])

    def test_cannot_buy_own_listing(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._inv_add(player, "yarn_ball", 1)
        listing = self.world.sell_listing(player, cat, "毛线球", 1, 10)["listing"]
        with self.assertRaises(GameError):
            self.world.buy_listing(player, listing["id"], 1)

    def test_cannot_buy_more_than_available(self):
        seller = self.make_player("3005", "卖家3")
        buyer = self.make_player("3006", "买家3")
        cat = self.make_catgirl(seller, "小摊3")
        self.world._inv_add(seller, "yarn_ball", 1)
        listing = self.world.sell_listing(seller, cat, "毛线球", 1, 10)["listing"]
        buyer["coins"] = 1000
        with self.assertRaises(GameError):
            self.world.buy_listing(buyer, listing["id"], 5)

    def test_listing_id_without_prefix(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._inv_add(player, "yarn_ball", 1)
        self.world.sell_listing(player, cat, "毛线球", 1, 10)
        self.world.cancel_listing(player, "1")  # 省略 L 前缀

    def test_market_hides_own_and_disabled(self):
        seller = self.make_player("3007", "卖家4")
        cat = self.make_catgirl(seller, "小摊4")
        self.world._inv_add(seller, "yarn_ball", 1)
        self.world.sell_listing(seller, cat, "毛线球", 1, 10)
        self.assertEqual(len(self.world.market_listings()), 1)
        self.assertEqual(
            len(self.world.market_listings(exclude_player=seller["key"])), 0
        )


# ======================================================================
# 世界与结算
# ======================================================================
class TestWorld(WorldTestCase):
    def test_player_key_includes_platform(self):
        p1, _ = self.world.ensure_player("aiocqhttp", "123", "甲", "")
        p2, _ = self.world.ensure_player("telegram", "123", "乙", "")
        self.assertNotEqual(p1["key"], p2["key"])
        self.assertEqual(len(self.world.store.players), 2)

    def test_tick_settles_and_refreshes(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        cat["birth_day"] = (self.today - timedelta(days=5)).isoformat()
        cat["last_tick"] = (self.today - timedelta(days=1)).isoformat()
        self.world.store.state["official_shop"] = {"date": "1970-01-01", "entries": []}
        result = self.world.tick()
        self.assertTrue(result["shop_refreshed"])
        self.assertEqual(self.world.ensure_official_shop()["date"], self.today.isoformat())

    def test_tick_reports_deaths(self):
        world = make_world({"survival": {"grace_days": 0}})
        player, _ = world.ensure_player("aiocqhttp", "41", "悲伤", "")
        cat = world.adopt(player, "走了")
        today = world.today()
        cat["birth_day"] = (today - timedelta(days=10)).isoformat()
        cat["last_tick"] = (today - timedelta(days=1)).isoformat()
        cat["satiety"] = 0
        cat["hydration"] = 0
        cat["health"] = 1
        result = world.tick()
        self.assertEqual(len(result["deaths"]), 1)
        self.assertEqual(result["deaths"][0]["catgirl_name"], "走了")

    def test_overview(self):
        player = self.make_player()
        self.make_catgirl(player, "统计")
        data = self.world.overview()
        self.assertEqual(data["players"], 1)
        self.assertEqual(data["catgirls"], 1)
        self.assertEqual(data["alive"], 1)
        self.assertEqual(data["dead"], 0)
        self.assertEqual(len(data["top_players"]), 1)

    def test_find_player_variants(self):
        player = self.make_player("1001", "阿猫")
        self.assertEqual(self.world.find_player(player["key"])["key"], player["key"])
        self.assertEqual(self.world.find_player("1001")["key"], player["key"])
        self.assertEqual(self.world.find_player("阿猫")["key"], player["key"])
        self.assertEqual(self.world.find_player("阿")["key"], player["key"])
        self.assertIsNone(self.world.find_player("查无此人"))

    def test_snapshot_shape(self):
        player = self.make_player()
        cat = self.make_catgirl(player, "快照")
        snap = self.world.catgirl_snapshot(cat)
        for key in ("id", "name", "alive", "satiety", "hydration", "health", "energy", "tasks"):
            self.assertIn(key, snap)
        self.assertEqual(len(snap["tasks"]), 3)

    def test_release_removes_catgirl_and_marriage(self):
        a = self.make_player("5001", "甲")
        b = self.make_player("5002", "乙")
        cat_a = self.make_catgirl(a, "阿花")
        cat_b = self.make_catgirl(b, "阿草")
        proposal, _ = self.world.propose(a, cat_a, "阿草")
        self.world.accept_proposal(b, proposal["id"])
        self.world.release_catgirl(a, cat_a)
        self.assertIsNone(self.world.catgirl_by_id(cat_a["id"]))
        self.assertEqual(a["catgirls"], [])
        self.assertEqual(self.world.store.marriages, [])

    def test_dead_catgirl_has_no_new_tasks(self):
        player = self.make_player()
        cat = self.make_catgirl(player)
        self.world._kill_catgirl(cat, "测试")
        snap = self.world.catgirl_snapshot(cat)
        self.assertEqual(snap["tasks"], [])


# ======================================================================
# 时间工具
# ======================================================================
class TestTimeUtils(unittest.TestCase):
    def test_game_day_boundary(self):
        from datetime import datetime

        self.assertEqual(game_day(datetime(2025, 7, 16, 3, 59), 4), date(2025, 7, 15))
        self.assertEqual(game_day(datetime(2025, 7, 16, 4, 0), 4), date(2025, 7, 16))
        self.assertEqual(game_day(datetime(2025, 7, 16, 23, 59), 4), date(2025, 7, 16))
        self.assertEqual(game_day(datetime(2025, 7, 16, 0, 30), 0), date(2025, 7, 16))


if __name__ == "__main__":
    unittest.main(verbosity=2)
