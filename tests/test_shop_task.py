"""B4: Shop task rules (agent/core/shop_task.py) and offline replay of the full Shop task through tools/run_tasks.py."""
import copy
import datetime as dt
import hashlib
import json
import shutil
import unittest
from pathlib import Path

from agent.core import shop_task as st
from tools import run_tasks as launcher
from tests.test_task_launcher import HAVE_MAA, RISK_POLICY, TABLE, FakePipelineHost, pipeline_nodes

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets/private"
NOW = dt.datetime(2026, 10, 2, 8, 0, tzinfo=dt.timezone.utc)  # JST 17:00, game day 2026-10-02
P = "dev_assets/private/"
HOME = P + "shop-20261002-01-home-frames/frame-001.png"
PREMIUM = P + "shop-20261002-02-money-frames/frame-001.png"
LIST = P + "shop-20261002-03-open1-frames/frame-001.png"
CONFIRM = P + "shop-20261002-04-confirm1-frames/frame-001.png"
RESULT = P + "shop-20261002-04-confirm1-frames/frame-024.png"
MISSION_POPUP = P + "shop-20261002-10-home-frames/frame-001.png"
AFTER_TWO = P + "manual-capture-kept-20261002/shop-after-two-bought-20261002.png"
# Game day 2026-10-01 (manual capture 03:10 JST on 10-02): list スキップ / ゴールド 98%OFF / タオル and the gold 交換確認.
LIST_1001 = P + "manual-capture-kept-20261002/shop-grade/007.png"
CONFIRM_GOLD_1001 = P + "manual-capture-kept-20261002/shop-grade/013.png"
SYN = P + "shop-b4-replay/"
AFTER_RANK1, GREY = SYN + "list-after-rank1.png", SYN + "list-grey-button1.png"
WRONG_CURRENCY, WRONG_NAME = SYN + "confirm-wrong-currency.png", SYN + "confirm-name-mismatch.png"
SPEND = ["shop-exchange-confirm"]
TODAY = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("シルバートレーニングチケット", 283)]
YESTERDAY = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("イルミネーションスターズのタオル", 5000)]


def view(cards, money=17678492, layout=True, enabled=None, limits=None):
    return st.ListView(layout, tuple(st.Card(i + 1, n, p, (limits or {}).get(i + 1, "0/1"),
                                              (enabled or {}).get(i + 1, True)) for i, (n, p) in enumerate(cards)), money)


def state(snapshot=TODAY, verified=(), uncertain=(), refused=(), spent=0):
    return st.LogState(tuple(snapshot) if snapshot is not None else None, frozenset(verified), frozenset(refused),
                       frozenset(uncertain), spent)


DEFAULT = st.ShopConfig.from_params({})
CONFIG = DEFAULT  # default since 2026-10-07: only スキップ 10 + ゴールド 10 (every third offer is opt-in)
SILVER3, GOLD3 = "商店其他·シルバートレーニングチケット@283", "商店其他·ゴールドトレーニングチケット@283"
TOWEL = "商店其他·イルミネーションスターズのタオル@5000"
ONLY_SKIP = {"商店金训练券": "No", SILVER3: "No", GOLD3: "No"}
DAY1003 = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("始まりのメッセージカード", 283)]
DAY09 = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("ゴールドトレーニングチケット", 283)]
CONFIRM_FIELDS = {"quantity": "1", "limit_after": "1", "limit_max": "1", "required": "10", "money_before": "17,678,492",
                  "money_after": "17,678,482", "item_before": "609", "item_after": "610(+1)", "name": "スキップチケット",
                  "prompt": "マニーを消費して以下のアイテムと交換しますか?"}
PLAN = {"rank": 1, "position": 1, "name": "スキップチケット", "price": 10, "money": 17678492, "visible": []}


class ShopConfigTests(unittest.TestCase):
    def test_visible_reserve_reaches_purchase_and_confirmation(self):
        interface = launcher.load_interface()
        for case, reserve in (("不保留", 0), ("1万", 10000), ("10万", 100000), ("100万", 1000000)):
            with self.subTest(case=case):
                plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "Shop", "option": {"商店余额保留": case}}]})
                config = launcher.task_config(plan, "shop.json", st.ShopConfig)
                self.assertEqual(config.coin_reserve, reserve)
                self.assertFalse(hasattr(config, "coin_budget"))      # 2026-10-28: no spend budget
                # At the reserve boundary the selected 10-money offer is allowed, below it no purchase is planned.
                allowed = st.decide(config, view(TODAY, money=reserve + 10), state())
                self.assertIsNotNone(allowed.plan)
                stopped = st.decide(config, view(TODAY, money=reserve + 9), state())
                self.assertIsNone(stopped.plan)
                self.assertIn("rank1:coin_reserve", stopped.notes)
                fields = dict(CONFIRM_FIELDS, money_before=str(reserve + 10), money_after=str(reserve))
                purchase = dict(PLAN, money=reserve + 10)
                self.assertEqual(st.check_confirm(config, purchase, fields), [])

        plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "Shop", "option": {"商店余额保留": "不保留"}}]})
        config = launcher.task_config(plan, "shop.json", st.ShopConfig)
        self.assertIsNotNone(st.decide(config, view(TODAY, money=5000), state()).plan)
        self.assertIsNone(st.decide(DEFAULT, view(TODAY, money=5000), state()).plan)
        default_plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "Shop"}]})
        self.assertEqual(launcher.task_config(default_plan, "shop.json", st.ShopConfig), DEFAULT)

    def test_defaults_fixed_catalog_prices_and_strict_keys(self):
        self.assertEqual(DEFAULT, st.ShopConfig())
        self.assertEqual(DEFAULT.entries, {("スキップチケット", 10), ("ゴールドトレーニングチケット", 10)})
        towel = st.ShopConfig.from_params({"buy_towel_illumination": True, "buy_skip_ticket": False})
        self.assertIn(("イルミネーションスターズのタオル", 5000), towel.entries)
        self.assertNotIn(("スキップチケット", 10), towel.entries)
        for bad in ({"ranks": [1]}, {"include_third": True}, {"slot1_item": "skip_ticket"}, {"must_skip_ticket": True},
                    {"skip_ticket_max_price": 10}, {"other_enabled": True}, {"other_max_price": 1000},
                    {"other_silver_training_ticket": True}, {"buy_silver_training_ticket": True},
                    {"currency": "jewel"}, {"purchase_scope": "all"}, {"coin_reserve": -1},
                    {"buy_skip_ticket": "yes"}, {"buy_jewel": True}, {"unknown": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                st.ShopConfig.from_params(bad)

    def test_catalog_matches_the_researched_rotation(self):
        ids = list(st.SHOP_ITEMS)
        self.assertEqual(len(ids), 27)  # 2 daily offers + 25 distinct (name, price) third offers
        keys = [(st.item_name(i), st.catalog_price(i)) for i in ids]
        self.assertEqual(len(set(keys)), len(keys))
        days = sorted(d for row in st.SHOP_ITEMS.values() for d in row[5])
        self.assertEqual(days, list(range(1, 32)))  # every day of month has exactly one third offer
        self.assertEqual(st.MAIN_ITEMS, ("skip_ticket", "gold_training_ticket"))
        for name, price in (("ゴールドトレーニングチケット", 10), ("ゴールドトレーニングチケット", 283),
                            ("シルバートレーニングチケット", 283), ("始まりのメッセージカード", 283),
                            ("イルミネーションスターズのタオル", 5000), ("秘密のメモ帳", 50)):
            self.assertIn((name, price), keys)
        # full-width brackets as the game may print them normalise to the catalog's half-width form
        self.assertIn((st.normalize_name("ヒーリングタルト（2個入り）"), 1500), keys)

    def test_item_catalog_and_config_nodes(self):
        nodes = pipeline_nodes()
        entry = nodes["SC.Shop.Entry"]["custom_action_param"]["config_nodes"]
        self.assertEqual(tuple(entry), st.config_nodes())
        merged = {}
        for node in st.config_nodes():
            merged.update(nodes[node]["attach"])  # Pipeline defaults == Python defaults
        self.assertEqual(st.ShopConfig.from_params(merged), DEFAULT)
        self.assertFalse([n for n in nodes if n.startswith("SC.Shop.Config.") and n not in st.config_nodes()])

    def test_task_options_override_separate_config_nodes(self):
        interface = launcher.load_interface()
        options = interface.tasks["Shop"]["option"]
        # The purchase cap stays fixed; the reserve now overrides the existing Budget node.
        self.assertEqual(len(options), len(st.config_nodes()))
        self.assertNotIn("商店预算", interface.options)
        self.assertEqual(options[:2], ["商店跳过券", "商店金训练券"])
        self.assertEqual(options[2], "商店余额保留")
        self.assertEqual(set(options[3:]), {f"商店其他·{row[0]}@{row[2]}" for i, row in st.SHOP_ITEMS.items()
                                            if i not in st.MAIN_ITEMS})
        budget = pipeline_nodes()["SC.Shop.Config.Budget"]["attach"]
        self.assertEqual(budget["coin_reserve"], 100000)
        self.assertNotIn("coin_budget", budget)
        for gone in ("商店必购跳过券", "商店跳过券单价上限", "商店金训练券单价上限", "商店其他商品", "商店其他商品单价上限"):
            self.assertNotIn(gone, interface.options)
        plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "Shop", "option": {
            "商店跳过券": "No", GOLD3: "Yes", TOWEL: "Yes"}}]})
        override = plan.pipeline_override
        self.assertEqual(override["SC.Shop.Config.SkipTicket"]["attach"], {"buy_skip_ticket": False})
        self.assertEqual(override["SC.Shop.Config.Other.TowelIllumination"]["attach"], {"buy_towel_illumination": True})
        self.assertEqual(set(override), set(st.config_nodes()))  # includes the visible reserve carrier
        merged = dict(budget)
        for node in st.config_nodes()[:-1]:
            merged.update(override[node]["attach"])
        config = st.ShopConfig.from_params(merged)
        self.assertEqual(config.entries, {("ゴールドトレーニングチケット", 10), ("ゴールドトレーニングチケット", 283),
                                          ("イルミネーションスターズのタオル", 5000)})
        default, = launcher.plan_tasks(interface, {"tasks": ["Shop"]})
        merged = dict(budget)
        for node in st.config_nodes()[:-1]:
            merged.update(default.pipeline_override[node]["attach"])
        self.assertEqual(st.ShopConfig.from_params(merged), DEFAULT)  # PI defaults == Python defaults
        self.assertTrue(interface.tasks["Shop"].get("default_check", False))  # verified live; checked by default
class ReconcileAndDecideTests(unittest.TestCase):
    def test_first_observation_maps_ranks_to_positions(self):
        decision = st.decide(CONFIG, view(TODAY), state(snapshot=None))
        self.assertEqual((decision.kind, decision.rank, decision.position), ("open", 1, 1))
        self.assertEqual(decision.plan["name"], "スキップチケット")

    def test_bought_cards_disappear_and_ranks_shift_left(self):
        decision = st.decide(CONFIG, view(TODAY[1:]), state(verified={1}, spent=10))
        self.assertEqual((decision.kind, decision.rank, decision.position), ("open", 2, 1))
        include = st.ShopConfig.from_params({"buy_gold_training_ticket": False,   # third offer シルバー 283 (opt-in)
                                             "buy_silver_training_ticket_283": True})
        decision = st.decide(include, view(TODAY[1:]), state(verified={1}))
        self.assertEqual((decision.rank, decision.position), (3, 2))
        done = st.decide(CONFIG, view(YESTERDAY[2:]), state(YESTERDAY, verified={1, 2}))
        self.assertEqual((done.kind, done.outcome), ("done", "nothing_to_do"))

    def test_manual_purchase_is_inconsistent_not_a_stop(self):
        for visible, log in ((YESTERDAY[2:], state(snapshot=None)), (TODAY[1:], state()),
                             (TODAY[:1] + TODAY[2:], state(verified={1}))):
            decision = st.decide(CONFIG, view(visible), log)
            self.assertEqual((decision.kind, decision.outcome), ("inconsistent", "skipped_inconsistent"), visible)
        self.assertIn("skipped_inconsistent", st.SUCCESS_OUTCOMES)

    def test_uncertain_spend_is_resolved_by_observation(self):
        # An earlier uncertain spend only warns; the screen decides (still offered = not bought).
        still_there = st.decide(CONFIG, view(TODAY), state(uncertain={1}, spent=10))
        self.assertEqual((still_there.kind, still_there.rank, still_there.position), ("open", 1, 1))
        self.assertIn("rank1:uncertain_still_offered", still_there.notes)
        gone = st.decide(CONFIG, view(TODAY[1:]), state(uncertain={1}, spent=10))
        self.assertEqual((gone.rank, gone.position), (2, 1))
        dup = [("A", 10), ("A", 10), ("B", 5)]
        ambiguous = st.reconcile(view(dup[1:]), state(dup, uncertain={1, 2}))
        self.assertEqual(ambiguous[0], None)  # rank 1 or 2 bought? cannot tell -> inconsistent

    def test_rules_skip_or_fail_without_buying(self):
        repriced = [("スキップチケット", 16), ("ゴールドトレーニングチケット", 11), ("シルバートレーニングチケット", 500)]
        decision = st.decide(CONFIG, view(repriced), state(repriced))
        self.assertEqual((decision.outcome, decision.notes), ("skipped_rules", (
            "rank1:price_16_not_selected", "rank2:price_11_not_selected", "rank3:not_selected",
            "selected_absent:skip_ticket", "selected_absent:gold_training_ticket")))
        legacy = st.ShopConfig.from_params({"coin_budget": 15})          # an old override: ignored, no budget
        decision = st.decide(legacy, view(TODAY[1:]), state(verified={1}, spent=10))
        self.assertEqual((decision.kind, decision.rank), ("open", 2))
        poor = st.decide(CONFIG, view(TODAY, money=100005), state())
        self.assertEqual(poor.outcome, "skipped_rules")
        grey = st.decide(CONFIG, view(TODAY, enabled={1: False}), state())
        self.assertEqual((grey.kind, grey.outcome), ("fail", "card_not_purchasable"))
        limit = st.decide(CONFIG, view(TODAY, limits={1: "1/1"}), state())
        self.assertEqual(limit.outcome, "card_not_purchasable")
        self.assertEqual(st.decide(CONFIG, view(TODAY), state(), can_spend=False).outcome, "spend_not_enabled")
        unreadable = st.decide(CONFIG, view([(None, 10)] + TODAY[1:]), state())
        self.assertEqual(unreadable.kind, "wait")
        self.assertEqual(st.decide(CONFIG, view(TODAY, layout=False), state()).kind, "wait")

    def test_selected_items_are_found_at_any_rank(self):
        for rank in (1, 2, 3):
            others = [c for c in DAY1003 if c[0] != "ゴールドトレーニングチケット"]
            cards = others[:rank - 1] + [DAY1003[1]] + others[rank - 1:]
            gold_only = st.ShopConfig.from_params({"buy_skip_ticket": False})
            with self.subTest(rank=rank):
                decision = st.decide(gold_only, view(cards), state(cards))
                self.assertEqual((decision.kind, decision.rank, decision.position, decision.plan["name"]),
                                 ("open", rank, rank, "ゴールドトレーニングチケット"))
        # 2026-10-03 order: skip, gold, then the unselected message card
        first = st.decide(DEFAULT, view(DAY1003), state(DAY1003))
        self.assertEqual((first.rank, first.plan["name"]), (1, "スキップチケット"))
        second = st.decide(DEFAULT, view(DAY1003[1:]), state(DAY1003, verified={1}, spent=10))
        self.assertEqual((second.rank, second.position), (2, 1))
        done = st.decide(DEFAULT, view(DAY1003[2:]), state(DAY1003, verified={1, 2}, spent=20))
        self.assertEqual((done.kind, done.outcome, done.notes),
                         ("done", "nothing_to_do", ("rank1:already_bought", "rank2:already_bought", "rank3:not_selected")))

    def test_gold_is_keyed_by_name_and_price(self):
        # Days 9/14/25: ゴールド is offer 2 at 10 AND offer 3 at 283 -- two catalog entries with separate switches.
        # 2026-10-07: the 283 entry is opt-in (default: only the first two offers).
        with283 = st.ShopConfig.from_params({"buy_gold_training_ticket_283": True})
        both = st.decide(with283, view(DAY09[2:]), state(DAY09, verified={1, 2}, spent=20))
        self.assertEqual((both.kind, both.rank, both.plan["price"]), ("open", 3, 283))
        skipped = st.decide(DEFAULT, view(DAY09[2:]), state(DAY09, verified={1, 2}, spent=20))
        self.assertEqual((skipped.outcome, skipped.notes[-1]), ("skipped_rules", "rank3:price_283_not_selected"))
        only283 = st.ShopConfig.from_params({"buy_skip_ticket": False, "buy_gold_training_ticket": False,
                                             "buy_gold_training_ticket_283": True})
        decision = st.decide(only283, view(DAY09), state(DAY09))
        self.assertEqual((decision.rank, decision.position), (3, 3))
        self.assertIn("rank2:price_10_not_selected", decision.notes)

    def test_absent_or_unselected_items_are_a_normal_skip(self):
        no_gold = [("スキップチケット", 10), ("シルバートレーニングチケット", 283), ("始まりのメッセージカード", 283)]
        only_main = st.ShopConfig.from_params({"buy_silver_training_ticket_283": False})
        decision = st.decide(only_main, view(no_gold[1:]), state(no_gold, verified={1}, spent=10))
        self.assertEqual((decision.kind, decision.outcome), ("done", "nothing_to_do"))
        self.assertIn("selected_absent:gold_training_ticket", decision.notes)
        self.assertFalse([n for n in decision.notes if "selected_absent:silver" in n])  # third offers rotate
        off = st.ShopConfig.from_params({st.option_key(i): False for i in st.SHOP_ITEMS})
        self.assertEqual(off.entries, frozenset())
        none = st.decide(off, view(TODAY), state())
        self.assertEqual((none.outcome, none.notes), ("nothing_to_do", ("rank1:not_selected", "rank2:not_selected",
                                                                       "rank3:not_selected")))
        unknown = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("新しいアイテム", 1)]
        new_item = st.decide(DEFAULT, view(unknown[2:]), state(unknown, verified={1, 2}, spent=20))
        self.assertEqual((new_item.outcome, new_item.notes[-1]), ("nothing_to_do", "rank3:not_selected"))
        # an unwanted card never fails the run even when its button is grey or its limit is not 0/1
        only_skip = st.ShopConfig.from_params({"buy_gold_training_ticket": False, "buy_gold_training_ticket_283": True})
        grey = st.decide(only_skip, view(TODAY[1:], enabled={1: False, 2: False}, limits={1: "1/1"}), state(verified={1}))
        self.assertEqual((grey.outcome, grey.notes[1:]), ("skipped_rules", ("rank2:price_10_not_selected", "rank3:not_selected")))

    def test_third_offer_selection_and_exact_names(self):
        after_main = state(TODAY, verified={1, 2}, spent=20)
        silver = st.ShopConfig.from_params({"buy_silver_training_ticket_283": True})  # opt-in since 2026-10-07
        hit = st.decide(silver, view(TODAY[2:]), after_main)
        self.assertEqual((hit.kind, hit.rank, hit.position), ("open", 3, 1))
        self.assertEqual(st.decide(DEFAULT, view(TODAY[2:]), after_main).notes[-1], "rank3:not_selected")
        towel = st.ShopConfig.from_params({"buy_towel_illumination": True})
        decision = st.decide(towel, view(YESTERDAY[2:]), state(YESTERDAY, verified={1, 2}, spent=20))
        self.assertEqual((decision.kind, decision.rank, decision.position), ("open", 3, 1))
        # OCR text differing by one character is not the item (exact match after NFKC / whitespace removal only)
        self.assertFalse(DEFAULT.allows("ゴールドトレーニングチケツト", 10))
        self.assertTrue(DEFAULT.allows("ゴールドトレーニング\nチケット", 10))
        self.assertFalse(DEFAULT.allows(None, 10))
        self.assertFalse(DEFAULT.allows("ゴールドトレーニングチケット", None))
        tart = st.ShopConfig.from_params({"buy_healing_tart_1500": True})
        self.assertTrue(tart.allows("ヒーリングタルト（2個入り）", 1500))  # full-width brackets (NFKC)
        self.assertFalse(tart.allows("ヒーリングタルト(2個入り)", 5000))

    def test_confirm_dialog_rechecks_the_item_rule(self):
        plan = dict(PLAN, rank=3)
        self.assertEqual(st.check_confirm(CONFIG, plan, CONFIRM_FIELDS), [])  # by item, not by rank
        no_skip = st.ShopConfig.from_params({"buy_skip_ticket": False})
        self.assertIn("item_not_allowed", st.check_confirm(no_skip, PLAN, CONFIRM_FIELDS))
        repriced = dict(PLAN, price=16)
        reasons = st.check_confirm(CONFIG, repriced, dict(CONFIRM_FIELDS, required="16", money_after="17,678,476"))
        self.assertIn("required_money_mismatch", reasons)
        self.assertIn("item_not_allowed", reasons)
    def test_confirm_dialog_checks(self):
        self.assertEqual(st.check_confirm(CONFIG, PLAN, CONFIRM_FIELDS), [])
        cases = {"name": ("ゴールドトレーニングチケット", "name_mismatch"),
                 "prompt": ("ジュエルを消費して以下のアイテムと交換しますか?", "currency_prompt_not_money"),
                 "quantity": ("4", "quantity_not_1"), "limit_after": ("2", "limit_preview_not_1_of_1"),
                 "required": ("5,000", "required_money_mismatch"),
                 "money_before": ("17,678,512", "money_before_mismatch"),
                 "money_after": ("17,678,492", "money_after_mismatch"), "item_after": ("611(+1)", "item_count_not_plus_1")}
        for key, (value, reason) in cases.items():
            with self.subTest(key=key):
                self.assertIn(reason, st.check_confirm(CONFIG, PLAN, dict(CONFIRM_FIELDS, **{key: value})))
        self.assertIn("name_mismatch", st.check_confirm(CONFIG, PLAN, dict(CONFIRM_FIELDS, name=None)))
        reserve = st.ShopConfig.from_params({"coin_reserve": 17678483})
        self.assertEqual(st.check_confirm(reserve, PLAN, CONFIRM_FIELDS), ["coin_reserve"])
        self.assertEqual(st.check_confirm(CONFIG, None, CONFIRM_FIELDS), ["no_open_plan"])

    def test_result_dialog_checks(self):
        intent = {"rank": 1, "name": "スキップチケット", "price": 10, "money_before": 17678492, "item_before": 609}
        good = {"sentence": "スキップチケットと交換しました", "money": "17678482", "items": "610"}
        self.assertEqual(st.check_result(intent, good), [])
        self.assertEqual(st.check_result(intent, dict(good, money="17678472")), ["result_money_mismatch"])
        self.assertEqual(st.check_result(intent, dict(good, items="609")), ["result_items_mismatch"])
        self.assertEqual(st.check_result(intent, dict(good, sentence="ゴールドトレーニングチケットと交換しました")),
                         ["result_sentence_mismatch"])
        self.assertEqual(st.check_result(None, good), ["no_pending_intent"])


DAY27 = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("ダンス基礎知識本", None)]


class UnreadUnselectedPriceTests(unittest.TestCase):
    """2026-10-27 daily (ui-20261027-223446): the third offer 「ダンス基礎知識本 500」 (not selected) had no price in four
    reads, so the whole list was list_unreadable and the first two offers were not bought."""

    def test_the_selected_offers_are_bought_past_an_unread_unselected_price(self):
        first = st.decide(CONFIG, view(DAY27), state(snapshot=None))
        self.assertEqual((first.kind, first.rank, first.position), ("open", 1, 1))
        second = st.decide(CONFIG, view([DAY27[1], ("ダンス基礎知識本", 500)]), state(DAY27, verified={1}, spent=10))
        self.assertEqual((second.kind, second.rank, second.position), ("open", 2, 1))   # price read later: still rank 3
        done = st.decide(CONFIG, view(DAY27[2:]), state(DAY27, verified={1, 2}, spent=20))
        self.assertEqual((done.kind, done.outcome, done.notes),
                         ("done", "nothing_to_do", ("rank1:already_bought", "rank2:already_bought", "rank3:not_selected")))
        self.assertEqual(st.decide(CONFIG, view(TODAY, limits={3: None}), state()).rank, 1)

    def test_an_offer_maa_may_buy_still_needs_name_price_and_limit(self):
        books = st.ShopConfig.from_params({"buy_dance_book_500": True})
        wait = st.decide(books, view(DAY27[2:]), state(DAY27, verified={1, 2}))
        self.assertEqual((wait.kind, wait.outcome, wait.notes[-1]), ("wait", "list_unreadable", "rank3:price_or_limit_unread"))
        no_limit = st.decide(CONFIG, view(TODAY, limits={1: None}), state())
        self.assertEqual((no_limit.kind, no_limit.notes), ("wait", ("rank1:price_or_limit_unread",)))
        no_name = st.decide(CONFIG, view(TODAY[:2] + [(None, 283)]), state())
        self.assertEqual((no_name.kind, no_name.notes), ("wait", ("layout_or_ocr_unreadable",)))
        grey = st.decide(CONFIG, view([(TODAY[0][0], None)] + TODAY[1:], enabled={1: False}), state())
        self.assertEqual((grey.kind, grey.outcome), ("fail", "card_not_purchasable"))

    def test_unread_price_matches_by_name_only(self):
        self.assertTrue(st.same_cards((("A", None), ("B", 5)), (("A", 7), ("B", 5))))
        self.assertTrue(st.same_cards((("A", 7),), (("A", None),)))
        self.assertFalse(st.same_cards((("A", None),), (("B", 7),)))
        self.assertFalse(st.same_cards((("A", 5),), (("A", 7),)))
        self.assertFalse(st.same_cards((("A", None),), ()))

    def test_snapshot_stores_the_unselected_unread_price_as_null(self):
        import tempfile
        import numpy as np
        from agent.core import shop_custom

        class FakeVision:
            def __init__(self, context):
                pass

            def read_list(self, image):
                return st.ListView(True, tuple(st.Card(i + 1, n, p, "0/1", True) for i, (n, p) in enumerate(cards)),
                                   17678492)

        root = PRIVATE / "run_logs" / "_unittest_shop_unread_price"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        for cards, logged in ((DAY27, True), ([DAY27[0], ("ゴールドトレーニングチケット", None), DAY27[2]], False)):
            with self.subTest(logged=logged):
                shutil.rmtree(root, ignore_errors=True)
                runtime = shop_custom.ShopRuntime(log_root=root, now=lambda: NOW, vision=FakeVision)
                runtime.begin(CONFIG)
                decision = runtime.decision(None, np.zeros((4, 4, 3), np.uint8))
                events = runtime.log.events()
                if logged:      # selected offers read: the day is logged, the null price kept
                    self.assertEqual([(c["name"], c["price"]) for c in events[0]["cards"]], DAY27)
                    # no spend authority in this test: the decision reaches rank 1 and refuses to spend
                    self.assertEqual((decision.outcome, decision.notes), ("spend_not_enabled", ("rank1:spend_not_enabled",)))
                else:           # a selected offer's price is unread: no snapshot from this frame
                    self.assertEqual(events, [])


class SpendLogTests(unittest.TestCase):
    def test_new_attempt_does_not_inherit_a_previous_not_sent_result(self):
        before = [{"event": "intent", "rank": 1, "price": 10}, {"event": "refused", "rank": 1},
                  {"event": "intent", "rank": 1, "price": 10}]
        for tail in ([], [{"event": "uncertain", "rank": 1}]):
            with self.subTest(tail=tail):
                result = st.log_state(before + tail)
                self.assertEqual(result.refused, frozenset())
                self.assertEqual(result.uncertain, frozenset({1}))
                self.assertEqual(result.spent, 10)
        verified = st.log_state(before + [{"event": "verified", "rank": 1}])
        self.assertEqual(verified.verified, frozenset({1}))
        self.assertEqual(verified.uncertain, frozenset())
        self.assertEqual(verified.spent, 10)

    def setUp(self):
        self.root = PRIVATE / "run_logs" / "_unittest_shop_log"
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_log_state_and_game_day(self):
        log = st.SpendLog(self.root, lambda: NOW)
        self.assertEqual(log.path().name, "shop-2026-10-02.jsonl")
        self.assertEqual(st.game_day(dt.datetime(2026, 10, 2, 19, 59, tzinfo=dt.timezone.utc)), "2026-10-02")
        self.assertEqual(st.game_day(dt.datetime(2026, 10, 2, 20, 0, tzinfo=dt.timezone.utc)), "2026-10-03")
        log.append({"event": "snapshot", "cards": [{"rank": i + 1, "name": n, "price": p} for i, (n, p) in enumerate(TODAY)]})
        for rank, price, terminal in ((1, 10, "verified"), (2, 10, None), (3, 283, "refused")):
            log.append({"event": "intent", "rank": rank, "name": "x", "price": price, "money_before": 1, "item_before": 1})
            if terminal:
                log.append({"event": terminal, "rank": rank})
        result = st.log_state(log.events())
        self.assertEqual((result.snapshot, result.verified, result.uncertain, result.refused, result.spent),
                         (tuple(TODAY), {1}, {2}, {3}, 20))
        with self.assertRaises(ValueError):
            st.SpendLog(ROOT / "docs")

    def test_pipeline_shape(self):
        nodes = pipeline_nodes()
        self.assertEqual(nodes["SC.Shop.HomeClean"]["any_of"], nodes["SC.Common.HomeClean"]["any_of"])
        self.assertEqual(nodes["SC.Shop.FinishClean"]["any_of"], nodes["SC.Common.HomeClean"]["any_of"])
        exchange = nodes["SC.Shop.Confirm.Exchange"]
        self.assertEqual((exchange["enabled"], exchange["target"], exchange["on_error"]),
                         (False, list(TABLE.get("shop-exchange-confirm").rect), ["SC.Shop.SpendError"]))
        self.assertEqual(exchange["all_of"], ["SC.Shop.Confirm.Guard", "SC.Shop.Check.Confirm"])
        self.assertEqual(nodes["SC.Shop.Confirm.Sent"]["any_of"], ["SC.Shop.Result.Guard"])
        main = nodes["SC.Shop.Main"]["next"]
        self.assertLess(main.index("[JumpBack]SC.Shop.Result.Close"), main.index("[JumpBack]SC.Shop.Confirm.Exchange"))
        self.assertLess(main.index("[JumpBack]SC.Shop.Confirm.Exchange"), main.index("[JumpBack]SC.Shop.Confirm.Cancel"))
        self.assertEqual(main[-1], "[JumpBack]SC.Nav.BackOne")
        self.assertNotIn("SC.Shop.Entry", json.loads((ROOT / "assets/resource/base/pipeline/placeholders.json")
                                                     .read_text(encoding="utf-8")))
        for name in ("SC.Shop.Premium.MoneyTab", "SC.Shop.OpenShop"):
            self.assertEqual(nodes[name]["max_hit"], 2)


def frames_present():
    return all((ROOT / p).is_file() for p in (HOME, PREMIUM, LIST, CONFIRM, RESULT, MISSION_POPUP, AFTER_TWO,
                                              LIST_1001, CONFIRM_GOLD_1001))


def replay_ready():
    from tests.test_task_launcher import private_ready
    if not (HAVE_MAA and frames_present() and private_ready()):
        return False
    if not (ROOT / AFTER_RANK1).is_file():
        from tools import build_shop_replay_frames
        build_shop_replay_frames.build()
    return True


@unittest.skipUnless(HAVE_MAA and frames_present(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 2026-10-02 商店实机帧")
class ShopReplayTests(unittest.TestCase):
    """The whole Shop task through tools/run_tasks.py --simulate-input on the real 2026-10-02 frames."""

    @classmethod
    def setUpClass(cls):
        if not replay_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def setUp(self):
        self.log_root = PRIVATE / "run_logs" / ("_unittest_shop_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.log_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.log_root, True)

    def interface(self, **attach):
        """Override the reserve carrier's default case for a replay-specific budget or reserve."""
        interface = copy.deepcopy(launcher.load_interface())
        values = {k: attach[k] for k in ("coin_reserve",) if k in attach}
        if values:
            option = interface.options["商店余额保留"]
            case = next(c for c in option["cases"] if c["name"] == option["default_case"])
            budget = {**pipeline_nodes()["SC.Shop.Config.Budget"]["attach"], **values}
            case["pipeline_override"]["SC.Shop.Config.Budget"] = {"attach": budget}
        return interface

    def run_shop(self, frames, option=None, spend=True, simulate=True, interface=None, extra_tasks=()):
        option = option or {}
        config = {"tasks": [{"name": "Shop", "option": option}, *extra_tasks]}
        if spend:
            config["allow_spend"] = SPEND
        return launcher.run(config, offline_steps=[(ROOT / f, "click") for f in frames], simulate_input=simulate,
                            policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                            interface=interface, shop_options={"log_root": self.log_root, "now": lambda: NOW})

    def report(self, task):
        report, = [r for r in task["reports"] if r["kind"] == "shop"]
        return report

    def events(self):
        path = self.log_root / "shop-2026-10-02.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def seed(self, cards, verified=()):
        log = st.SpendLog(self.log_root, lambda: NOW)
        log.append({"event": "snapshot", "cards": [{"rank": i + 1, "name": n, "price": p} for i, (n, p) in enumerate(cards)]})
        for rank in verified:
            log.append({"event": "intent", "rank": rank, "name": cards[rank - 1][0], "price": cards[rank - 1][1],
                        "money_before": 0, "item_before": 0})
            log.append({"event": "verified", "rank": rank})

    def test_buys_rank_one_end_to_end_and_returns_home(self):
        summary = self.run_shop([HOME, PREMIUM, LIST, CONFIRM, RESULT, AFTER_RANK1, MISSION_POPUP, HOME],
                                option=ONLY_SKIP)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["home-shop", "shop-money-tab", "shop-offer-open-1",
                                                     "shop-exchange-confirm", "shop-result-close", "nav-back",
                                                     "popup-mission-completed-ok"])
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual(task["simulated_spends"], [["shop-exchange-confirm", "sent"]])
        report = self.report(task)
        self.assertEqual((report["outcome"], report["bought_ranks"]), ("bought", [1]))
        self.assertEqual([e["event"] for e in self.events()], ["snapshot", "intent", "verified"])
        snapshot, intent, verified = self.events()
        self.assertEqual([(c["name"], c["price"]) for c in snapshot["cards"]], TODAY)
        self.assertEqual((intent["money_before"], intent["item_before"]), (17678492, 609))
        self.assertEqual((verified["money_after"], verified["items_after"]), (17678482, 610))
        self.assertEqual(task["node_trace"][-1], "SC.Shop.FinishClean")
        self.assertEqual(summary["real_input_attempts"], 0)

    def test_default_ranks_buy_one_then_skip_by_reserve(self):
        # 17,678,492 on the list: rank 1 (10) leaves 17,678,482 >= the reserve, rank 2 would go below it.
        summary = self.run_shop([HOME, PREMIUM, LIST, CONFIRM, RESULT, AFTER_RANK1, MISSION_POPUP, HOME],
                                interface=self.interface(coin_reserve=17678473))
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        report = self.report(task)
        self.assertEqual((report["outcome"], report["bought_ranks"]), ("bought", [1]))
        self.assertIn("rank2:coin_reserve", report["notes"])

    def test_selected_name_at_another_price_buys_nothing(self):
        # Real 10-02 list: only the third-offer ゴールド 283 is selected; the ゴールド 10 offer is skipped by price.
        summary = self.run_shop([LIST, HOME], option={"商店跳过券": "No", "商店金训练券": "No", GOLD3: "Yes"})
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"], task["simulated_spends"]),
                         ("succeeded", ["nav-back"], []))
        report = self.report(task)
        self.assertEqual((report["outcome"], report["notes"]),
                         ("skipped_rules", ["rank1:not_selected", "rank2:price_10_not_selected", "rank3:not_selected"]))
        self.assertEqual([e["event"] for e in self.events()], ["snapshot"])

    def test_wrong_currency_and_name_mismatch_cancel_without_spending(self):
        for frame, reason in ((WRONG_CURRENCY, "currency_prompt_not_money"), (WRONG_NAME, "name_mismatch")):
            with self.subTest(frame=frame):
                shutil.rmtree(self.log_root, ignore_errors=True)
                summary = self.run_shop([LIST, frame, LIST, HOME])
                task, = summary["tasks"]
                self.assertEqual(task["status"], "failed")
                self.assertEqual(task["simulated_actions"], ["shop-offer-open-1", "shop-exchange-cancel", "nav-back"])
                self.assertEqual(task["simulated_spends"], [])
                report = self.report(task)
                self.assertEqual(report["outcome"], "confirm_mismatch")
                self.assertIn(reason, report["notes"])
                self.assertEqual([e["event"] for e in self.events()], ["snapshot"])

    def test_grey_button_and_disabled_spending_never_open_the_dialog(self):
        # The synthetic grey card carries the monthly 5,000 button: its (name, price) is not a catalog entry, so it is
        # skipped by price before the button state matters (a grey card at its catalog price fails
        # card_not_purchasable -- see test_rules_skip_or_fail_without_buying).
        summary = self.run_shop([GREY, HOME], option=ONLY_SKIP)
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("succeeded", ["nav-back"]))
        report = self.report(task)
        self.assertEqual((report["outcome"], report["notes"][0]), ("skipped_rules", "rank1:price_5000_not_selected"))
        shutil.rmtree(self.log_root, ignore_errors=True)  # the grey frame's snapshot would not match the real list
        summary = self.run_shop([LIST, HOME], spend=False)
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("failed", ["nav-back"]))
        self.assertEqual(self.report(task)["outcome"], "spend_not_enabled")

    def test_unobserved_result_stops_the_run_and_a_later_run_reads_the_list(self):
        summary = self.run_shop([LIST, CONFIRM, LIST])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_spends"], [["shop-exchange-confirm", "sent"]])
        self.assertEqual(self.report(task)["outcome"], "spend_uncertain")
        self.assertEqual([e["event"] for e in self.events()], ["snapshot", "intent", "uncertain"])
        # the later run sees rank 1 still offered at 0/1 (not bought) and buys it through the usual checks
        again = self.run_shop([LIST, CONFIRM, RESULT, AFTER_RANK1, MISSION_POPUP, HOME], option=ONLY_SKIP)
        task, = again["tasks"]
        self.assertEqual((task["status"], task["simulated_spends"]), ("succeeded", [["shop-exchange-confirm", "sent"]]))
        report = self.report(task)
        self.assertEqual((report["outcome"], report["bought_ranks"]), ("bought", [1]))
        # bought now: a third run sees it gone and buys nothing
        third = self.run_shop([AFTER_RANK1, HOME], option=ONLY_SKIP)
        task, = third["tasks"]
        self.assertEqual((task["status"], task["simulated_spends"]), ("succeeded", []))
        self.assertEqual(self.report(task)["outcome"], "nothing_to_do")

    def test_after_two_bought_uses_the_log_and_a_manual_purchase_only_skips(self):
        self.seed(YESTERDAY, verified=(1, 2))
        summary = self.run_shop([AFTER_TWO, HOME])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("succeeded", ["nav-back"]))
        self.assertEqual(self.report(task)["outcome"], "nothing_to_do")
        shutil.rmtree(self.log_root, ignore_errors=True)  # no log: the user bought ranks 1, 2 by hand
        summary = self.run_shop([AFTER_TWO, HOME, HOME], extra_tasks=["Diagnostics"])
        shop, diagnostics = summary["tasks"]
        self.assertEqual((shop["status"], shop["simulated_actions"]), ("succeeded", ["nav-back"]))
        self.assertEqual(self.report(shop)["outcome"], "skipped_inconsistent")
        self.assertEqual([e["event"] for e in self.events()], ["inconsistent"])
        self.assertEqual(diagnostics["status"], "succeeded")

    def test_default_buys_skip_and_gold_on_the_2026_10_01_and_10_02_lists(self):
        # Real OCR on both days' lists: rank 1 スキップチケット and rank 2 ゴールドトレーニングチケット are the default
        # must-buys; rank 3 (タオル on 10-01, シルバー on 10-02) is not selected while 其他商品 is off.
        for frame, third in ((LIST_1001, "イルミネーションスターズのタオル"), (LIST, "シルバートレーニングチケット")):
            with self.subTest(frame=frame):
                shutil.rmtree(self.log_root, ignore_errors=True)
                summary = self.run_shop([frame, HOME], spend=False)
                task, = summary["tasks"]
                self.assertEqual((task["status"], task["simulated_actions"], task["simulated_spends"]),
                                 ("failed", ["nav-back"], []))
                report = self.report(task)
                self.assertEqual((report["outcome"], report["notes"]), ("spend_not_enabled", ["rank1:spend_not_enabled"]))
                snapshot, = self.events()
                self.assertEqual([c["name"] for c in snapshot["cards"]],
                                 ["スキップチケット", "ゴールドトレーニングチケット", third])
                # once the skip ticket is verified, the gold ticket (now at screen position 1) is next
                cards = [(c["name"], c["price"]) for c in snapshot["cards"]]
                log = [snapshot, {"event": "intent", "rank": 1, "price": 10}, {"event": "verified", "rank": 1}]
                decision = st.decide(DEFAULT, view(cards[1:]), st.log_state(log))
                self.assertEqual((decision.rank, decision.position, decision.plan["name"]),
                                 (2, 1, "ゴールドトレーニングチケット"))

    def test_gold_ticket_confirm_dialog_of_2026_10_01_passes_every_check(self):
        # Only the gold ticket enabled: rank 2 opens at position 2 and the real 10-01 交換確認 (4,788 -> 4,789(+1))
        # passes every field check; no 交換結果 frame exists for it, so the run ends spend_uncertain (never retried).
        summary = self.run_shop([LIST_1001, CONFIRM_GOLD_1001, LIST_1001], option={"商店跳过券": "No"})
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:2], ["shop-offer-open-2", "shop-exchange-confirm"])
        self.assertEqual(task["simulated_spends"], [["shop-exchange-confirm", "sent"]])
        self.assertEqual(self.report(task)["outcome"], "spend_uncertain")
        snapshot, intent, uncertain = self.events()
        self.assertEqual((intent["rank"], intent["name"], intent["money_before"], intent["item_before"]),
                         (2, "ゴールドトレーニングチケット", 17678512, 4788))
        self.assertEqual(uncertain["event"], "uncertain")

    def test_nothing_selected_buys_nothing_on_real_frames(self):
        summary = self.run_shop([LIST, HOME], option={"商店跳过券": "No", "商店金训练券": "No", SILVER3: "No", GOLD3: "No"})
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"], task["simulated_spends"]),
                         ("succeeded", ["nav-back"], []))
        report = self.report(task)
        self.assertEqual((report["outcome"], report["notes"]),
                         ("nothing_to_do", ["rank1:not_selected", "rank2:not_selected", "rank3:not_selected"]))
        self.assertEqual([e["event"] for e in self.events()], ["snapshot"])

    def test_third_offer_picker_reads_the_two_line_towel_name(self):
        self.seed(YESTERDAY, verified=(1, 2))
        summary = self.run_shop([AFTER_TWO, HOME], option={TOWEL: "Yes"}, spend=False)
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("failed", ["nav-back"]))
        report = self.report(task)
        self.assertEqual(report["outcome"], "spend_not_enabled")
        self.assertIn("rank3:spend_not_enabled", report["notes"])
        shutil.rmtree(self.log_root, ignore_errors=True)
        self.seed(YESTERDAY, verified=(1, 2))
        summary = self.run_shop([AFTER_TWO, HOME], spend=False)  # default: towel not selected
        task, = summary["tasks"]
        self.assertEqual((task["status"], self.report(task)["outcome"]), ("succeeded", "nothing_to_do"))
        self.assertIn("rank3:not_selected", self.report(task)["notes"])
    def test_failed_shop_does_not_stop_the_queue_and_read_only_never_clicks(self):
        summary = self.run_shop([LIST, HOME, HOME], spend=False, extra_tasks=["Diagnostics"])
        self.assertEqual([t["status"] for t in summary["tasks"]], ["failed", "succeeded"])
        summary = self.run_shop([LIST], simulate=False)
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_clicks"], task["refused_clicks"]), ("failed", [], []))
        self.assertEqual(summary["input_mode"], launcher.READ_ONLY)


@unittest.skipUnless(HAVE_MAA and frames_present(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 2026-10-02 商店实机帧")
class ShopLivePathTests(unittest.TestCase):
    """The --live code path (WebView2 gate + LiveSpendAuthority + HMAC grant) against a fake host, no socket."""

    @classmethod
    def setUpClass(cls):
        if not replay_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def test_shop_spend_reaches_the_host_only_with_a_frame_bound_grant(self):
        from agent.core import webview_controller as wc

        root = PRIVATE / "run_logs" / "_unittest_shop_live"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        steps = [(f, "click") for f in (HOME, PREMIUM, LIST, CONFIRM, RESULT, AFTER_RANK1, MISSION_POPUP, HOME)]
        host = FakePipelineHost(steps)
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                   "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                   "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
                   "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)},
                                   wc.SpendGate(SPEND, max_per_action=3), wc.AuditLog(root), host.send, policy=RISK_POLICY)
        summary = launcher.run({"tasks": [{"name": "Shop", "option": ONLY_SKIP}], "allow_spend": SPEND},
                               live=True, gate=gate, policy=RISK_POLICY, summary_path=False, save_diagnostics=False,
                               shop_options={"log_root": root, "now": lambda: NOW}, frame_guard_enabled=True)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        actions = [r["action"] for r in host.requests]
        self.assertEqual(actions, ["home-shop", "shop-money-tab", "shop-offer-open-1", "shop-exchange-confirm",
                                   "shop-result-close", "nav-back", "popup-mission-completed-ok"])
        spend, = [r for r in host.requests if "spend_grant" in r]
        grant = spend["spend_grant"]
        self.assertEqual((spend["action"], grant["action"], grant["frame_sha256"], grant["nonce"]),
                         ("shop-exchange-confirm", "shop-exchange-confirm", spend["frame_sha256"], spend["nonce"]))
        self.assertEqual(spend["frame_sha256"], hashlib.sha256((ROOT / CONFIRM).read_bytes()).hexdigest())
        self.assertEqual(grant["hmac"], wc.sign_spend_grant("07" * 32, "shop-exchange-confirm", grant["frame_sha256"],
                                                            grant["nonce"], grant["expires_at"], grant["capture_seq"], grant["frame_binding_seq"]))
        self.assertTrue(all("spend_grant" not in r for r in host.requests if r["action"] != "shop-exchange-confirm"))
        self.assertEqual(summary["allow_spend"], SPEND)

    def test_shop_without_spend_permission_reads_the_list_and_leaves(self):
        """2026-10-07 click-path readiness: the first supervised Shop step runs without allow_spend."""
        from agent.core import webview_controller as wc

        root = PRIVATE / "run_logs" / "_unittest_shop_live_nospend"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        host = FakePipelineHost([(f, "click") for f in (HOME, PREMIUM, LIST, HOME)])
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                   "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                   "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
                   "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(), wc.AuditLog(root),
                                   host.send, policy=RISK_POLICY)
        summary = launcher.run({"tasks": [{"name": "Shop", "option": ONLY_SKIP}]}, live=True, gate=gate,
                               policy=RISK_POLICY, summary_path=False, save_diagnostics=False,
                               shop_options={"log_root": root, "now": lambda: NOW}, frame_guard_enabled=True)
        task, = summary["tasks"]
        self.assertEqual([r["action"] for r in host.requests], ["home-shop", "shop-money-tab", "nav-back"])
        self.assertFalse([r for r in host.requests if "spend_grant" in r])
        report, = [r for r in task["reports"] if r["kind"] == "shop"]
        self.assertEqual(report["outcome"], "spend_not_enabled")
        self.assertEqual(summary["allow_spend"], [])


# 8.5th anniversary layout (supervised live run 2026-10-07 00:57/00:59 UTC, host PID 25092): the プレミアム page has no
# 新人P向け/ALL subtabs, so the old NewbieSubtab anchor is gone; the マニー list keeps the 2026-10-02 layout.
ANNIV_PREMIUM = P + "shop-20261007-anniversary/premium.png"
ANNIV_LIST = P + "shop-20261007-anniversary/list.png"
TWO_LINE_LIST = P + "shop-20261024-twoline/list.png"
SALE_PREMIUM = P + "shop-20261022-sale/premium.png"  # ジュエルSALE layout, no NEW badge over プレミアム
ANNIV_TODAY = [("スキップチケット", 10), ("ゴールドトレーニングチケット", 10), ("放課後クライマックスガールズのタオル", 5000)]


def anniversary_present():
    return all((ROOT / p).is_file() for p in (ANNIV_PREMIUM, ANNIV_LIST))


@unittest.skipUnless(HAVE_MAA and frames_present() and anniversary_present(),
                     "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 2026-10-07 周年商店实机帧")
class ShopAnniversaryLayoutTests(unittest.TestCase):
    """Batch 4: the anniversary プレミアム layout is a regular production path (no supervised-only guard override).

    The guard is Title + PremiumSelected + MoneyIdle + the 「特定商取引法に基づく表示」 button, which both layouts share;
    spend rules (first two offers by default, third opt-in, budget/reserve caps, one-shot grants) are unchanged."""

    @classmethod
    def setUpClass(cls):
        if not replay_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def setUp(self):
        self.log_root = PRIVATE / "run_logs" / ("_unittest_shop_anniv_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.log_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.log_root, True)

    def run_shop(self, frames, option=None, spend=False):
        config = {"tasks": [{"name": "Shop", "option": option or {}}]}
        if spend:
            config["allow_spend"] = SPEND
        return launcher.run(config, offline_steps=[(ROOT / f, "click") for f in frames], simulate_input=True,
                            policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                            shop_options={"log_root": self.log_root, "now": lambda: NOW})

    def events(self):
        path = self.log_root / "shop-2026-10-02.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def test_pipeline_guard_no_longer_requires_the_newbie_subtab(self):
        nodes = pipeline_nodes()
        guard = nodes["SC.Shop.Premium.Guard"]["all_of"]
        self.assertEqual(guard, ["SC.Shop.Anchor.Premium.Title", "SC.Shop.Anchor.Premium.PremiumSelected",
                                 "SC.Shop.Anchor.Premium.MoneyIdle", "SC.Shop.Anchor.Premium.LegalNotice"])
        self.assertEqual(nodes["SC.Shop.Anchor.Premium.LegalNotice"]["template"], "native/shop_premium/legal_notice.png")

    @unittest.skipUnless((ROOT / SALE_PREMIUM).is_file(), "需要 2026-10-22 ジュエルSALE 商店原生帧")
    def test_jewel_sale_premium_page_without_new_badge_opens_the_money_list(self):
        """2026-10-22 live: the old PremiumSelected template carried the edge of a NEW badge above the tab; without the
        badge the guard missed and the daily run timed out on the shop."""
        summary = self.run_shop([HOME, SALE_PREMIUM, ANNIV_LIST, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["home-shop", "shop-money-tab", "nav-back"], task)
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual(task["simulated_spends"], [])

    def test_anniversary_premium_page_opens_the_money_list_without_any_override(self):
        summary = self.run_shop([HOME, ANNIV_PREMIUM, ANNIV_LIST, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["home-shop", "shop-money-tab", "nav-back"], task)
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual(task["simulated_spends"], [])
        report, = [r for r in task["reports"] if r["kind"] == "shop"]
        self.assertEqual((task["status"], report["outcome"]), ("failed", "spend_not_enabled"))
        self.assertEqual(report["notes"], ["rank1:spend_not_enabled"])
        snapshot, = self.events()
        self.assertEqual([(c["name"], c["price"]) for c in snapshot["cards"]], ANNIV_TODAY)
        self.assertEqual(summary["real_input_attempts"], 0)

    def test_anniversary_third_offer_stays_opt_in_and_budget_still_applies(self):
        # Default: the towel (third offer) is never a candidate. Opted in, 5,000 fits the 10,000 budget, so it becomes
        # the candidate -- and without allow_spend the run still ends spend_not_enabled before any confirm dialog.
        summary = self.run_shop([ANNIV_LIST, HOME], option={"商店跳过券": "No", "商店金训练券": "No"})
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "shop"]
        self.assertEqual((task["status"], task["simulated_actions"], report["outcome"]),
                         ("succeeded", ["nav-back"], "nothing_to_do"))
        self.assertEqual(report["notes"], ["rank1:not_selected", "rank2:not_selected", "rank3:not_selected"])
        shutil.rmtree(self.log_root, ignore_errors=True)
        summary = self.run_shop([ANNIV_LIST, HOME], option={"商店跳过券": "No", "商店金训练券": "No",
                                                            "商店其他·放課後クライマックスガールズのタオル@5000": "Yes"})
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "shop"]
        self.assertEqual((task["simulated_actions"], task["simulated_spends"]), (["nav-back"], []))
        self.assertEqual(report["outcome"], "spend_not_enabled")
        self.assertIn("rank3:spend_not_enabled", report["notes"])

    @unittest.skipUnless((ROOT / TWO_LINE_LIST).is_file(), "需要 2026-10-24 两行商品名商店帧")
    def test_two_line_third_offer_name_is_read(self):
        """2026-10-24 daily: the third offer's name has two lines (first line from y 270, above the old box at 274):
        the old box read None and the whole list was list_unreadable (the first two were not bought). The sample is a
        recording frame (h264): only the name OCR is checked, the list guard templates need native frames."""
        from maa.custom_action import CustomAction
        from agent.core import shop_custom
        from agent.core.task_actions import ReportSink
        from tools.replay_screenshots import read_png
        image, names = read_png(ROOT / TWO_LINE_LIST), {}

        class Probe(CustomAction):
            def run(self, context, argv):
                vision = shop_custom.Vision(context)
                for position in st.RANKS:
                    names[position] = vision.text(image, st.card_roi(st.LIST_NAME_ROI, position))
                names["old"] = vision.text(image, st.card_roi([41, 274, 250, 50], 3))
                return True

        scenario = launcher.ReplayScenario([image], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        self.assertEqual(names, {1: "スキップチケット", 2: "ゴールドトレーニングチケット",
                                 3: "ヒーリングフルーツタルト(2個入り)", "old": None})

    def test_list_and_home_are_never_taken_for_the_premium_page(self):
        # The list (same title / tab bar) and Home must not satisfy the new guard: from the list the task reads offers,
        # it never presses the マニー tab again.
        summary = self.run_shop([ANNIV_LIST, HOME])
        task, = summary["tasks"]
        self.assertNotIn("shop-money-tab", task["simulated_actions"])
        self.assertNotIn("SC.Shop.Premium.MoneyTab", task["node_trace"])


class ShopDefaultsTests(unittest.TestCase):
    """By default buy only the first two offers; PI = Pipeline = Python."""

    def test_default_selection_is_the_first_two_offers(self):
        self.assertEqual(st.DEFAULT_SELECTED, frozenset({"skip_ticket", "gold_training_ticket"}))
        self.assertEqual(st.ShopConfig().selected, st.DEFAULT_SELECTED)
        self.assertEqual(st.ShopConfig.from_params({}).selected, st.DEFAULT_SELECTED)
        self.assertEqual(st.ShopConfig().entries, frozenset({("スキップチケット", 10), ("ゴールドトレーニングチケット", 10)}))

    def test_pi_pipeline_and_python_defaults_agree(self):
        import json
        from tools.check_project import load_jsonc
        tasks = json.loads((ROOT / "assets" / "tasks" / "shop.json").read_text(encoding="utf-8"))
        pipeline = load_jsonc(ROOT / "assets" / "resource" / "base" / "pipeline" / "shop.json")
        options = tasks["option"]
        for item in st.SHOP_ITEMS:
            key = st.option_key(item)
            want = item in st.DEFAULT_SELECTED
            option = next(name for name, o in options.items()
                          if any(key in (c["pipeline_override"].get(st.config_node(item)) or {}).get("attach", {})
                                 for c in o.get("cases", [])))
            with self.subTest(item=item):
                self.assertEqual(options[option]["default_case"], "Yes" if want else "No")
                self.assertIs(pipeline[st.config_node(item)]["attach"][key], want)

if __name__ == "__main__":
    unittest.main()
