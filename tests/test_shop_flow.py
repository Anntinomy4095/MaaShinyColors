import copy
import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path

from agent.core import shop_flow as shop
from tools import navigation_protocol as nav
from tools import repeatable_navigation_protocol as rep

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc

CONFIG = {
    "version": 1, "premium_currency_allowed": False,
    "budgets": {"coins": 100, "basic_training_tickets": 0, "fes_participation_rights": 0},
    "shop": {"enabled": True, "purchase_scope": "daily_discount_only", "include_third": False,
             "coin_budget": 50, "coin_reserve": 1000,
             "items": [{"offer_rank": 1, "item_id": "skip_ticket", "item_type": "ordinary_item", "quantity": 1, "max_unit_price": 10},
                       {"offer_rank": 2, "item_id": "gold_training_ticket", "item_type": "ordinary_item", "quantity": 1, "max_unit_price": 10}]},
    "training": {"enabled": False, "card_id": None, "candidate_card_ids": [], "ticket_type": "basic_training_ticket",
                 "ticket_quantity": 1, "ticket_budget": 0},
    "match_fes": {"enabled": False, "mode": "skip", "count": 20, "participation_budget": 0, "stage_id": None, "stage_selection": "latest"},
    "grade_fes": {"enabled": False, "difficulty_id": None, "count": 1, "participation_budget": 0},
}
LIST_TEXTS = {"List.money": "17,678,512", "List.limit1": "0/1", "List.limit2": "0/1", "List.limit3": "0/1",
              "List.price1": "10", "List.price2": "10", "List.price3": "5,000"}
CONFIRM_TEXTS = {"Confirm.quantity": "1", "Confirm.limit_after": "1", "Confirm.limit_max": "1", "Confirm.required": "10",
                 "Confirm.money_before": "17,678,512", "Confirm.money_after": "17,678,502",
                 "Confirm.item_before": "608", "Confirm.item_after": "609(+1)"}


CATALOG_SLOTS = {1: "skip_ticket", 2: "gold_training_ticket", 3: "illumination_stars_towel"}
NAMES = {1: "スキップチケット", 2: "ゴールドトレーニングチケット", 3: "イルミネーションスターズのタオル"}


def slots(count=3, cards=None):
    cards = cards or {p: {"catalog": CATALOG_SLOTS[p], "name": NAMES[p]} for p in range(1, 4)}
    return {"count": count, "cards": {p: cards[p] for p in range(1, count + 1)}}


def open_(config, rank, ev, ident, state="ready", spent=0, confirmed=()):
    """Open rank r on a full 3-banner list whose card at r carries ``ident``."""
    cards = {p: {"catalog": CATALOG_SLOTS[p], "name": NAMES[p]} for p in range(1, 4)}
    cards[rank] = ident
    return shop.validate_offer_open(config, rank, ev, slots(3, cards), set(confirmed), state, spent)


def hit(scene, roi, score=0.99):
    return {"scene": scene, "hit": score >= 0.95, "score": score, "roi": list(roi), "raw_detail": {"filtered": [{"box": list(roi)}]}}


def recognitions(stage, *, missing=None, low=None, extra=()):
    rows = []
    for other in shop.GUARD_STAGES:
        for anchor, roi in shop.ANCHORS[other].items():
            name = shop.scene_name(other, anchor)
            score = 0.99 if other == shop.anchor_stage(stage) else 0.2
            if name == low:
                score = 0.9
            if name != missing:
                rows.append(hit(name, roi, score))
    for name, roi in shop.EXCLUSIONS.items():
        rows.append(hit(shop.exclusion_scene(name), roi, 0.1))
    for name, score in extra:
        rows = [dict(r, score=score, hit=score >= .95) if r["scene"] == name else r for r in rows]
    return rows


class PipelineTest(unittest.TestCase):
    def test_fixture_is_fixed_and_disabled(self):
        pipeline = shop.fixed_pipeline()
        clicks = {k: v for k, v in pipeline.items() if v.get("action") == "Click"}
        self.assertEqual(set(clicks), {shop.PREFIX + "Action." + a for a in shop.ACTIONS})
        self.assertTrue(all(v["enabled"] is False for v in clicks.values()))
        for action, (stage, point) in shop.ACTIONS.items():
            self.assertEqual(clicks[shop.PREFIX + "Action." + action]["target"], [*point, 1, 1])
            self.assertEqual(clicks[shop.PREFIX + "Action." + action]["all_of"], shop.action_anchor_names(action))
        self.assertEqual(clicks[shop.PREFIX + "Action.shop-offer-open-2"]["all_of"][-4:], shop.slot_names(2))
        self.assertEqual(pipeline[shop.button_scene(2)]["recognition"], "ColorMatch")

    def test_one_action_enabled(self):
        pipeline = shop.fixed_pipeline("shop-exchange-confirm-2")
        enabled = [k for k, v in pipeline.items() if v.get("enabled") is True]
        self.assertEqual(enabled, [shop.PREFIX + "Action.shop-exchange-confirm-2"])
        self.assertEqual(pipeline[shop.PREFIX + "Confirm.Observe"]["next"][0], shop.PREFIX + "Action.shop-exchange-confirm-2")
        with self.assertRaises(ValueError):
            shop.fixed_pipeline("shop-exchange-plus")

    def test_points_match_host_and_protocol(self):
        source = (ROOT / "browser_host/DailyNavigationPolicy.cs").read_text(encoding="utf-8")
        for action, (_, (x, y)) in shop.ACTIONS.items():
            self.assertEqual(nav.DAILY_SHOP_ACTION_POINTS[action], (x, y))
            self.assertRegex(source, re.escape(f'"{action}"') + r"[^\n]*=> new Point\(" + f"{x}, {y}" + r"\)")
        self.assertEqual(set(shop.CONSUMING), {a for a in shop.ACTIONS if a.startswith("shop-exchange-confirm-")})
        self.assertEqual(set(shop.REPEATABLE), set(rep.SHOP_ACTIONS))
        self.assertFalse(set(shop.CONSUMING) & set(rep.ACTIONS))
        self.assertTrue(all(f"shop-offer-open-{p}" in rep.ACTIONS for p in (1, 2, 3)) and "shop-result-close" in rep.ACTIONS)


class GuardTest(unittest.TestCase):
    def test_complete_stage(self):
        for stage in ("Home", "Premium", "List", "Confirm", "After"):
            rows = recognitions(stage)
            self.assertTrue(shop.stage_matches(stage, rows), stage)
            self.assertTrue(shop.exclusions_clear(stage, rows), stage)
        self.assertFalse(shop.stage_matches("Result", recognitions("List")))

    def test_missing_or_low_anchor(self):
        for stage in shop.GUARD_STAGES:
            for anchor in shop.guard_anchors(stage):
                name = shop.scene_name(stage, anchor)
                self.assertFalse(shop.stage_matches(stage, recognitions(stage, missing=name)))
                self.assertFalse(shop.stage_matches(stage, recognitions(stage, low=name)))

    def test_shifted_box_rejected(self):
        rows = recognitions("List")
        rows[0]["raw_detail"]["filtered"][0]["box"] = [0, 0, 1, 1]
        self.assertFalse(shop.stage_matches(rows[0]["scene"].split(".")[2], rows))

    def test_exclusions(self):
        loading = shop.exclusion_scene("Loading")
        self.assertFalse(shop.exclusions_clear("List", recognitions("List", extra=[(loading, 0.85)])))
        # Confirm title over a List refuses List; List under the dialog is allowed for Confirm.
        title = shop.scene_name("Confirm", "Title")
        self.assertFalse(shop.exclusions_clear("List", recognitions("List", extra=[(title, 0.81)])))
        under = recognitions("Confirm", extra=[(shop.scene_name("List", "MoneySelected"), 0.99)])
        self.assertTrue(shop.exclusions_clear("Confirm", under))
        self.assertFalse(shop.exclusions_clear("Premium", recognitions("Premium", extra=[(shop.scene_name("List", "MoneySelected"), 0.9)])))

    def test_identity_unique(self):
        roi = shop.list_name_roi(2)
        results = {item: hit("x", roi, 0.3) for item in shop.ITEMS}
        self.assertIsNone(shop.identify_item(results, roi))
        results["gold_training_ticket"] = hit("x", roi, 0.999)
        self.assertEqual(shop.identify_item(results, roi), "gold_training_ticket")
        results["skip_ticket"] = hit("x", roi, 0.96)
        self.assertIsNone(shop.identify_item(results, roi))
        with self.assertRaises(ValueError):
            shop.identify_item({"skip_ticket": hit("x", roi)}, roi)


def dialog_ev(texts):
    evidence = shop.confirm_evidence(texts)
    evidence["currency_prompt"] = "マニーを消費して以下のアイテムと交換しますか?"
    return evidence


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.list = shop.list_evidence(LIST_TEXTS)
        self.config = copy.deepcopy(CONFIG)

    def test_parse(self):
        self.assertEqual(self.list["money"], 17678512)
        self.assertEqual(self.list["offers"][3], {"limit_current": 0, "limit_max": 1, "price": 5000})
        for bad in ("17,67,512", "1,0000", "", "10.", "T0"):
            with self.assertRaises(ValueError):
                shop.parse_count(bad)
        with self.assertRaises(ValueError):
            shop.parse_limit("2/1")
        self.assertEqual(shop.list_evidence(dict(LIST_TEXTS, **{"List.price2": None}))["offers"][2], {"limit_current": 0, "limit_max": 1})

    def test_offer_open(self):
        result = open_(self.config, 1, self.list, "skip_ticket", "ready", 0)
        self.assertEqual(result["price"], 10)
        for rank, identity, state, spent in ((1, "gold_training_ticket", "ready", 0), (1, "skip_ticket", "pending", 0),
                                             (1, "skip_ticket", "confirmed", 0), (1, "skip_ticket", "ready", 41),
                                             (3, "illumination_stars_towel", "ready", 0), (1, None, "ready", 0)):
            with self.assertRaises(ValueError):
                open_(self.config, rank, self.list, identity, state, spent)

    def test_rank3_requires_include_third(self):
        self.config["shop"]["include_third"] = True
        self.config["shop"]["items"].append({"offer_rank": 3, "item_id": "illumination_stars_towel", "item_type": "ordinary_item",
                                             "quantity": 1, "max_unit_price": 5000})
        with self.assertRaisesRegex(ValueError, "预算"):
            open_(self.config, 3, self.list, "illumination_stars_towel", "ready", 0)
        self.config["shop"]["coin_budget"] = self.config["budgets"]["coins"] = 6000
        self.assertEqual(open_(self.config, 3, self.list, "illumination_stars_towel", "ready", 0)["price"], 5000)

    def test_quantity_must_be_one_and_disabled_shop(self):
        self.config["shop"]["items"][0]["quantity"] = 2
        with self.assertRaises(ValueError):
            open_(self.config, 1, self.list, "skip_ticket", "ready", 0)
        disabled = copy.deepcopy(CONFIG)
        disabled["shop"]["enabled"] = False
        with self.assertRaises(ValueError):
            open_(disabled, 1, self.list, "skip_ticket", "ready", 0)

    def test_reserve_and_price_cap(self):
        self.config["shop"]["coin_reserve"] = 17678503
        with self.assertRaisesRegex(ValueError, "保留"):
            open_(self.config, 1, self.list, "skip_ticket", "ready", 0)
        self.config["shop"]["coin_reserve"] = 0
        self.config["shop"]["items"][0]["max_unit_price"] = 9
        with self.assertRaisesRegex(ValueError, "单价"):
            open_(self.config, 1, self.list, "skip_ticket", "ready", 0)

    def test_already_complete(self):
        done = shop.list_evidence(dict(LIST_TEXTS, **{"List.limit1": "1/1"}))
        with self.assertRaises(shop.AlreadyComplete):
            open_(self.config, 1, done, "skip_ticket", "ready", 0)

    def test_confirm_and_after(self):
        baseline = open_(self.config, 1, self.list, "skip_ticket", "ready", 0)
        spend = shop.validate_confirm(self.config, 1, baseline, dialog_ev(CONFIRM_TEXTS), "skip_ticket", "ready", 0)
        self.assertEqual((spend["required"], spend["money_after"], spend["item_after"]), (10, 17678502, 609))
        for change in ({"Confirm.quantity": "2"}, {"Confirm.required": "5,000"}, {"Confirm.money_after": "17,678,512"},
                       {"Confirm.money_before": "17,678,513"}, {"Confirm.limit_after": "0"}, {"Confirm.limit_max": "2"},
                       {"Confirm.item_before": "607"}):
            with self.assertRaises(ValueError, msg=change):
                shop.validate_confirm(self.config, 1, baseline, dialog_ev(dict(CONFIRM_TEXTS, **change)), "skip_ticket", "ready", 0)
        with self.assertRaises(ValueError):
            shop.confirm_evidence(dict(CONFIRM_TEXTS, **{"Confirm.item_after": "609"}))
        with self.assertRaises(ValueError):
            shop.validate_confirm(self.config, 1, baseline, dialog_ev(CONFIRM_TEXTS), "gold_training_ticket", "ready", 0)
        with self.assertRaises(ValueError):
            shop.validate_confirm(self.config, 2, baseline, dialog_ev(CONFIRM_TEXTS), "skip_ticket", "ready", 0)
        witness = {"spend": spend, "offer_baseline": baseline}
        # Bought card disappears; gold and towel shift to positions 1 and 2.
        shifted = slots(2, {1: {"catalog": "gold_training_ticket", "name": NAMES[2]}, 2: {"catalog": "illumination_stars_towel", "name": NAMES[3]}})
        after = shop.list_evidence(dict(LIST_TEXTS, **{"List.money": "17,678,502"}))
        self.assertEqual(shop.validate_after(witness, after, shifted)["banner_count"], 2)
        with self.assertRaises(ValueError):  # money unchanged
            shop.validate_after(witness, self.list, shifted)
        with self.assertRaises(ValueError):  # not removed (3 banners, the old 1/1 idea)
            shop.validate_after(witness, after, slots(3))
        with self.assertRaises(ValueError):  # wrong card removed
            shop.validate_after(witness, after, slots(2, {1: {"catalog": "skip_ticket", "name": NAMES[1]}, 2: {"catalog": "illumination_stars_towel", "name": NAMES[3]}}))
        with self.assertRaises(ValueError):  # two cards vanished
            shop.validate_after(witness, after, slots(1, {1: {"catalog": "illumination_stars_towel", "name": NAMES[3]}}))


class LedgerTest(unittest.TestCase):
    def test_game_day_matches_host(self):
        self.assertEqual(shop.game_day(dt.datetime(2026, 10, 1, 19, 59, 59, tzinfo=UTC)), "2026-10-01")
        self.assertEqual(shop.game_day(dt.datetime(2026, 10, 1, 20, 0, 0, tzinfo=UTC)), "2026-10-02")
        self.assertEqual(shop.game_day(dt.datetime(2026, 10, 2, 4, 59, tzinfo=dt.timezone(dt.timedelta(hours=9)))), "2026-10-01")
        with self.assertRaises(ValueError):
            shop.game_day(dt.datetime(2026, 10, 2))
        source = (ROOT / "browser_host/RepeatableNavigationBudget.cs").read_text(encoding="utf-8")
        self.assertIn("AddHours(9 - 5)", source)

    def test_pending_blocks_and_never_resets(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as temporary:
            ledger = shop.ShopLedger(Path(temporary) / "daily_shop")
            day = "2026-10-02"
            self.assertEqual(ledger.state(day, 1), "ready")
            witness = {"version": 1, "runner": "SC.DailyShop", "no_retry": True, "spend": {"rank": 1, "required": 10}}
            proof = ledger.write_pending(day, 1, witness)
            self.assertEqual(ledger.state(day, 1), "pending")
            self.assertEqual(ledger.spent(day), 10)
            self.assertEqual([r["rank"] for r in ledger.unresolved()], [1])
            with self.assertRaises(FileExistsError):
                ledger.write_pending(day, 1, witness)
            with self.assertRaises(ValueError):
                ledger.write_confirmed(day, 1, {"pending_sha256": "0" * 64})
            ledger.write_confirmed(day, 1, {"pending_sha256": proof["sha256"]})
            self.assertEqual(ledger.state(day, 1), "confirmed")
            self.assertEqual(ledger.unresolved(), [])
            self.assertEqual(ledger.spent(day), 10)
            with self.assertRaises(ValueError):
                ledger.write_pending(day, 2, dict(witness, spend={"rank": 1, "required": 10}))
        with self.assertRaises(ValueError):
            shop.ShopLedger(ROOT / "tools")


class ProtocolTest(unittest.TestCase):
    def session(self, shop_enabled, day="2026-10-02"):
        actions = list(nav.DAILY_ACTION_POINTS) + (list(nav.DAILY_SHOP_ACTION_POINTS) if shop_enabled else [])
        paths = {a: str(nav.shop_budget_path(a, day) if a in nav.DAILY_SHOP_ACTION_POINTS else nav.DAILY_BUDGET_PATHS[a]) for a in actions}
        return {"version": 1, "host": "127.0.0.1", "port": 1234, "token": "a" * 64, "pid": 7, "input_enabled": True,
                "browser_demo_only": False, "transport": nav.DAILY_TRANSPORT, "actions": actions, "budget_paths": paths}

    def test_daily_session_scopes(self):
        nav.validate_daily_session(self.session(False), 7)
        nav.validate_daily_session(self.session(True), 7)
        bad = self.session(True)
        bad["budget_paths"]["home-shop"] = str(nav.shop_budget_path("home-shop", "2026-10-03"))
        with self.assertRaises(ValueError):
            nav.validate_daily_session(bad, 7)
        bad = self.session(True)
        bad["budget_paths"]["shop-exchange-confirm-1"] = str(ROOT / "dev_assets/private/daily_navigation/shop-exchange-confirm-1.json")
        with self.assertRaises(ValueError):
            nav.validate_daily_session(bad, 7)
        bad = self.session(True)
        bad["actions"] = bad["actions"][:-1]
        with self.assertRaises(ValueError):
            nav.validate_daily_session(bad, 7)

    def test_shop_request_requires_switch(self):
        status = {"ready": True, "browser_demo": False, "input_enabled": False, "composition_game": True, "minimized": False,
                  "window_visible": True, "webview_visible": True, "supervised_daily": True, "supervised_navigation": None,
                  "frame_sha256": "b" * 64, "frame_age_ms": 10, "width": 1280, "height": 720, "source_capture_width": 1280, "source_capture_height": 720}
        session = self.session(True)
        with self.assertRaises(ValueError):
            nav.daily_navigation_request(session, status, "b" * 64, "home-shop")
        request = nav.daily_navigation_request(session, dict(status, supervised_shop=True), "b" * 64, "home-shop")
        self.assertEqual(request["action"], "home-shop")

    def test_repeatable_shop(self):
        self.assertEqual(rep.allowed_actions(True), [a for a in rep.ACTIONS if a not in rep.SHOP_ACTIONS + rep.GRADE_ACTIONS])
        self.assertEqual(rep.allowed_actions(True, True, True), list(rep.ACTIONS))
        self.assertEqual(rep.producer_for("home-shop"), rep.SHOP_PRODUCER)
        self.assertEqual(rep.producer_for("home-mission"), rep.PRODUCER)


class RunnerArgumentTest(unittest.TestCase):
    def run_main(self, *extra):
        from tools import run_daily_shop
        base = ["--foreground-manifest", "x.json", "--foreground-contracts", "y.json", "--session", "s.json",
                "--report", str(ROOT / "dev_assets/private/shop-test-never-written.json")]
        import contextlib, io
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_daily_shop.main(base + list(extra))

    def test_refusals(self):
        self.run_main("--stage", "After", "--execute", "--action", "shop-back")
        self.run_main("--stage", "Result", "--execute", "--action", "shop-back")
        self.run_main("--stage", "Result", "--action", "shop-result-close", "--execute")  # missing --offer-rank
        self.run_main("--stage", "List", "--resolve-after-spend-report", "x.json")
        self.run_main("--stage", "List", "--execute")
        self.run_main("--stage", "List", "--action", "shop-offer-open")
        self.run_main("--stage", "Confirm", "--execute", "--repeatable", "--action", "shop-exchange-confirm", "--offer-rank", "1")
        self.run_main("--stage", "After")
        self.assertFalse((ROOT / "dev_assets/private/shop-test-never-written.json").exists())

    def test_default_config_disabled(self):
        data = json.loads((ROOT / "daily_tasks.json").read_text(encoding="utf-8"))
        self.assertFalse(data["shop"]["enabled"])
        self.assertFalse(data["shop"]["include_third"])
        with self.assertRaises(ValueError):
            shop.offer_config(data, 1)


if __name__ == "__main__":
    unittest.main()


class RankModeTest(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)
        for item in self.config["shop"]["items"]:
            item.update(item_id=shop.ANY_ITEM, currency="money")
        self.list = shop.list_evidence(LIST_TEXTS)
        self.card = {"catalog": None, "name": "スキップ チケット\n"}
        self.dialog = shop.confirm_evidence(CONFIRM_TEXTS)
        self.dialog["currency_prompt"] = "マニー\nを消費して以下のアイテムと交換しますか？"

    def test_daily_config_accepts_only_bounded_any(self):
        from agent.core.daily_config import DailyConfig, plan_daily_tasks
        DailyConfig.from_dict(self.config)  # two ANY entries do not collide as identities
        for change in ({"quantity": 2}, {"currency": "jewel"}, {"item_type": "basic_training_ticket"}, {"max_unit_price": 0}):
            bad = copy.deepcopy(self.config)
            bad["shop"]["items"][0].update(change)
            with self.assertRaises(ValueError, msg=change):
                DailyConfig.from_dict(bad)
        missing = copy.deepcopy(self.config)
        del missing["shop"]["items"][0]["currency"]
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(missing)
        offers = [{"offer_rank": r, "item_id": f"today-{r}", "item_type": "ordinary_item", "currency": "coins", "unit_price": 10,
                   "original_unit_price": 500, "discount_evidence": "98%OFF", "available_quantity": 1, "purchased": False} for r in (1, 2)]
        plan = plan_daily_tasks(self.config, {"source": "manual", "inventory": {"coins": 5000},
                                              "shop": {"state": "ready", "offer_scope": "daily_discount", "offers": offers}})
        self.assertEqual(plan["tasks"][0]["status"], "planned")

    def test_names_must_be_readable_and_equal(self):
        baseline = open_(self.config, 1, self.list, self.card, "ready", 0)
        self.assertEqual((baseline["identity_mode"], baseline["card_name"]), ("rank", "スキップチケット"))
        with self.assertRaises(ValueError):
            open_(self.config, 1, self.list, {"catalog": "skip_ticket", "name": None}, "ready", 0)
        spend = shop.validate_confirm(self.config, 1, baseline, self.dialog, {"catalog": None, "name": "スキップチケット"}, "ready", 0)
        self.assertEqual((spend["card_name"], spend["dialog_name"]), ("スキップチケット", "スキップチケット"))
        for name in ("ゴールドトレーニングチケット", None, ""):
            with self.assertRaises(ValueError):
                shop.validate_confirm(self.config, 1, baseline, self.dialog, {"catalog": None, "name": name}, "ready", 0)
        for prompt in (None, "ジュエルを消費して以下のアイテムと交換しますか?", "を消費して"):
            with self.assertRaises(ValueError):
                shop.validate_confirm(self.config, 1, baseline, dict(self.dialog, currency_prompt=prompt),
                                      {"catalog": None, "name": "スキップチケット"}, "ready", 0)
        witness = {"spend": spend, "offer_baseline": baseline}
        after = shop.list_evidence(dict(LIST_TEXTS, **{"List.money": "17,678,502"}))
        shifted = slots(2, {1: {"catalog": None, "name": NAMES[2]}, 2: {"catalog": None, "name": NAMES[3]}})
        self.assertEqual(shop.validate_after(witness, after, shifted)["card_name"], "スキップチケット")
        with self.assertRaises(ValueError):  # unreadable remaining name
            shop.validate_after(witness, after, slots(2, {1: {"catalog": None, "name": None}, 2: {"catalog": None, "name": NAMES[3]}}))

    def test_normalize(self):
        self.assertEqual(shop.normalize_name("イルミネーションスターズ\nのタオル "), "イルミネーションスターズのタオル")
        self.assertEqual(shop.normalize_name("交換しますか？"), "交換しますか?")



def slot_rows(count, *, grey=(), covered=(), banner_without_slot=()):
    rows = [hit(shop.scene_name("List", a), roi) for a, roi in shop.guard_anchors("List").items()]
    for p in shop.OFFER_RANKS:
        daily = p <= count and p not in covered
        for anchor in shop.SLOT_ANCHORS:
            roi = shop.ANCHORS["List"][f"{anchor}{p}"]
            score = 0.99 if daily or (anchor == "Banner" and p in banner_without_slot) else (0.96 if anchor != "Banner" else 0.1)
            rows.append(hit(shop.scene_name("List", f"{anchor}{p}"), roi, score))
        enabled = daily and p not in grey
        rows.append({"scene": shop.button_scene(p), "hit": enabled, "roi": shop.button_roi(p),
                     "matched_pixels": 480 if enabled else 0})
        monthly = p > count and p not in covered
        rows.append(hit(shop.monthly_scene(p), shop.monthly_roi(p), 0.99 if monthly else 0.2))
    return rows


class SlotTest(unittest.TestCase):
    def test_layouts(self):
        self.assertEqual(shop.daily_slots(slot_rows(3)), 3)
        self.assertEqual(shop.daily_slots(slot_rows(1)), 1)
        self.assertEqual(shop.daily_slots(slot_rows(0)), 0)
        self.assertIsNone(shop.daily_slots(slot_rows(3, grey=(2,))))           # grey (disabled) button
        self.assertIsNone(shop.daily_slots(slot_rows(1, covered=(3,))))        # covered card: no header at all
        self.assertIsNone(shop.daily_slots(slot_rows(1, banner_without_slot=(2,))))
        self.assertIsNone(shop.daily_slots(slot_rows(3)[:-1]))

    def test_position_mapping(self):
        self.assertEqual([shop.position_of(r, set()) for r in (1, 2, 3)], [1, 2, 3])
        self.assertEqual(shop.position_of(3, {1, 2}), 1)
        self.assertEqual(shop.position_of(3, {1}), 2)
        self.assertEqual(shop.position_of(2, {1}), 1)
        self.assertEqual(shop.position_of(1, {2}), 1)
        with self.assertRaises(ValueError):
            shop.position_of(2, {2})

    def test_ledger_drives_rank(self):
        config = copy.deepcopy(CONFIG)
        config["shop"].update(include_third=True, coin_budget=6000)
        config["budgets"]["coins"] = 6000
        config["shop"]["items"].append({"offer_rank": 3, "item_id": "illumination_stars_towel", "item_type": "ordinary_item",
                                        "quantity": 1, "max_unit_price": 5000})
        towel_first = shop.list_evidence(dict(LIST_TEXTS, **{"List.price1": "5,000", "List.limit2": "1/10", "List.price2": "1,000"}))
        one = slots(1, {1: {"catalog": "illumination_stars_towel", "name": NAMES[3]}})
        result = shop.validate_offer_open(config, 3, towel_first, one, {1, 2}, "ready", 20)
        self.assertEqual((result["position"], result["price"], result["banner_count"]), (1, 5000, 1))
        for confirmed in (set(), {1}, {2}):  # banner count disagrees with the ledger: unknown purchase
            with self.assertRaises(ValueError):
                shop.validate_offer_open(config, 3, towel_first, one, confirmed, "ready", 20)
        with self.assertRaises(shop.AlreadyComplete):
            shop.validate_offer_open(config, 1, towel_first, one, {1, 2}, "ready", 20)
        with self.assertRaises(ValueError):  # the card at the mapped position is not the configured item
            shop.validate_offer_open(config, 3, towel_first, slots(1, {1: {"catalog": "skip_ticket", "name": NAMES[1]}}), {1, 2}, "ready", 20)

    def test_runner_resolves_position(self):
        from tools.run_daily_shop import resolve_action
        self.assertEqual(resolve_action("shop-offer-open", 3, {1, 2}), "shop-offer-open-1")
        self.assertEqual(resolve_action("shop-offer-open", 2, {1}), "shop-offer-open-1")
        self.assertIsNone(resolve_action("shop-offer-open", 1, {1}))
        self.assertEqual(resolve_action("shop-exchange-confirm", 3, {1, 2}), "shop-exchange-confirm-3")


class ResultTest(unittest.TestCase):
    SPEND = {"rank": 1, "item_id": "any_daily_discount", "card_name": "スキップチケット", "dialog_name": "スキップチケット",
             "required": 10, "money_before": 17678492, "item_before": 609}

    def evidence(self, money="17678482", items="610", sentence="スキップチケット と交換しました"):
        return shop.result_evidence({"Result.money": money, "Result.items": items}, sentence)

    def test_live_values(self):
        result = shop.validate_result({"spend": self.SPEND}, self.evidence())
        self.assertEqual((result["name"], result["money"], result["items"]), ("スキップチケット", 17678482, 610))
        self.assertEqual(shop.result_evidence({"Result.money": "1", "Result.items": "4,789"}, "Xと交換しました")["items"], 4789)

    def test_refusals(self):
        for kwargs in ({"money": "17678492"}, {"items": "609"}, {"sentence": "ゴールドトレーニングチケットと交換しました"}):
            with self.assertRaises(ValueError, msg=kwargs):
                shop.validate_result({"spend": self.SPEND}, self.evidence(**kwargs))
        for kwargs in ({"money": "17,678,482"}, {"money": None}, {"sentence": "スキップチケット"}, {"sentence": "と交換しました"}, {"sentence": None}):
            with self.assertRaises(ValueError, msg=kwargs):
                self.evidence(**kwargs)

    def test_result_stage_contract(self):
        self.assertIn("Result", shop.GUARD_STAGES)
        self.assertEqual(shop.ACTIONS["shop-result-close"], ("Result", (640, 497)))
        self.assertEqual(shop.POST_STAGES["shop-exchange-confirm-1"], ("Result", "After"))
        self.assertNotIn("shop-result-close", shop.CONSUMING)
        rows = recognitions("Result")
        self.assertTrue(shop.stage_matches("Result", rows) and shop.exclusions_clear("Result", rows))

    def test_after_record_requires_confirmed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as temporary:
            ledger = shop.ShopLedger(Path(temporary) / "daily_shop")
            with self.assertRaises(ValueError):
                ledger.write_after("2026-10-02", 1, {"rank": 1})
            proof = ledger.write_pending("2026-10-02", 1, {"version": 1, "runner": "SC.DailyShop", "no_retry": True,
                                                           "spend": {"rank": 1, "required": 10}})
            ledger.write_confirmed("2026-10-02", 1, {"pending_sha256": proof["sha256"], "kind": "result_dialog"})
            ledger.write_after("2026-10-02", 1, {"rank": 1})
            with self.assertRaises(FileExistsError):
                ledger.write_after("2026-10-02", 1, {"rank": 1})
