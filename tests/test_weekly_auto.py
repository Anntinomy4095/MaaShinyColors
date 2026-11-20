"""Weekly official Auto Produce (WeeklyProduce): rules, runtime decisions, static
Pipeline/table contracts and the private-frame offline replay. Offline; no game, no host, no input."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import pipeline_actions as pa
from agent.core import weekly_auto as wa
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets/resource/base/pipeline/weekly_produce.json"
TASK = ROOT / "assets/tasks/weekly_produce.json"
PRIVATE = ROOT / "dev_assets/private"
IMAGE = np.zeros((720, 1280, 3), dtype=np.uint8)


def config(**kw):
    return wa.WeeklyAutoConfig.from_params(kw)


class ConfigAndRuleTests(unittest.TestCase):
    def test_config_defaults_and_limits(self):
        c = config()
        self.assertEqual((c.runs, c.use_skip, c.expect_scenario, c.expect_unit), (1, False, "", ""))
        self.assertEqual((c.unit, c.books, c.policy), (1, "history", ""))
        self.assertEqual((c.stamina, c.spend_action), (30, "weekly-auto-start"))
        self.assertEqual(config(runs="3", use_skip=True).spend_action, "weekly-auto-skip")
        for bad in ({"runs": 0}, {"runs": wa.MAX_RUNS + 1}, {"runs": True}, {"use_skip": "yes"},
                    {"expect_scenario": "HALO"}, {"expect_unit": "x" * 21}, {"unknown": 1}, {"setup": "edit"},
                    {"setup": "maa"}, {"unit": 0}, {"unit": 21}, {"books": "preset3"}, {"policy": "vocal"},
                    {"expect_idol": "結華"}, {"expect_scenario": wa.TARGET_SCENARIO}):
            with self.subTest(bad), self.assertRaises(ValueError):
                wa.WeeklyAutoConfig.from_params(bad)
        self.assertEqual(config(expect_unit=" 結 華 ").expect_unit, "結華")
        c = config(setup="maa", expect_scenario="GRAD", unit="14", books="preset2", policy="Dance")
        self.assertEqual((c.setup, c.expect_scenario, c.unit, c.books, c.policy), ("maa", "GRAD", 14, "preset2", "Dance"))

    def test_loop_config(self):
        # 2026-10-30 user 「允许」: ループ = 25 runs (750) with recovery sodas, row count 1, never スキップ
        c = config(loop=True, runs=3, use_skip=True)
        self.assertEqual((c.runs, c.use_skip, c.soda_cap, c.stamina, c.spend_action),
                         (1, False, 450, 750, "weekly-auto-soda-start"))
        self.assertEqual(config(loop=True, soda_cap="750").soda_cap, 750)
        self.assertEqual(config(soda_cap=750).soda_cap, 0)   # without ループ no soda
        for bad in ({"loop": "yes"}, {"loop": True, "soda_cap": 500}):
            with self.subTest(bad), self.assertRaises(ValueError):
                wa.WeeklyAutoConfig.from_params(bad)

    def test_soda_plan(self):
        owned = (162, 469, 666)
        self.assertEqual(wa.plan_sodas(450, owned, 450), (1, 2, 3))     # exact, fewest sodas
        self.assertEqual(wa.plan_sodas(453, owned, 600), (0, 1, 4))     # 460: smallest overshoot
        self.assertEqual(wa.plan_sodas(453, owned, 450), None)          # over the cap
        self.assertEqual(wa.plan_sodas(450, (15, 0, 0), 750), (15, 0, 0))
        self.assertEqual(wa.plan_sodas(450, (0, 0, 4), 750), None)      # not enough owned
        self.assertEqual(wa.plan_sodas(450, (None, 1, 9), 750), None)   # unread
        self.assertEqual(wa.plan_sodas(0, (None, None, None), 450), (0, 0, 0))

    def test_policy_soda_cap(self):
        root = Path(tempfile.mkdtemp(dir=PRIVATE))
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "policy.json"
        for setting, cap in (({"enabled": True, "max_per_run": 750}, 750), ({"enabled": False, "max_per_run": 0}, 0),
                             ({"enabled": True, "max_per_run": "750"}, 0), (None, 0)):
            path.write_text(json.dumps({"resource_usage": {"stamina_items": setting}}), encoding="utf-8")
            self.assertEqual(wa.stamina_item_cap(path), cap, setting)
        self.assertEqual(wa.stamina_item_cap(root / "missing.json"), 0)
        self.assertEqual(wa.stamina_item_cap(), 750)   # the repository baseline (ADR-013)

    def test_parsers(self):
        self.assertEqual(wa.parse_ratio("300/300"), (300, 300))
        self.assertEqual(wa.parse_ratio("5/25"), (5, 25))
        self.assertIsNone(wa.parse_ratio("5 25"))
        self.assertEqual(wa.parse_int("150"), 150)
        self.assertIsNone(wa.parse_int("-"))
        self.assertEqual(wa.parse_skip_left("残り5回/日"), 5)
        self.assertIsNone(wa.parse_skip_left("残り回/日"))
        self.assertEqual([wa.loop_state(n) for n in (0, 50, 503, 1545)], ["off", "off", None, "on"])

    def test_count_step_never_passes_the_target(self):
        self.assertEqual(wa.count_step(1, 3), "plus")
        self.assertEqual(wa.count_step(5, 3), "minus")
        self.assertIsNone(wa.count_step(3, 3))

    def test_schedule_and_dialog_rules(self):
        ok = {"row": "configured", "items": 0, "books": 3, "loop": "off", "scenario": "STEP", "unit_name": "結華",
              "count": 1}
        self.assertEqual(wa.schedule_reasons(config(), ok), [])
        self.assertEqual(wa.schedule_reasons(config(), {**ok, "row": "empty"}), ["row1_empty"])
        self.assertEqual(wa.schedule_reasons(config(), {**ok, "loop": "on"}), ["loop_on"])
        self.assertEqual(wa.schedule_reasons(config(), {**ok, "items": 1, "books": 2}),
                         ["row1_items_set", "row1_books_2"])
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="STEP"), {**ok, "scenario": "SWING"}),
                         ["scenario_mismatch"])
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="STEP"), {**ok, "scenario": "SSTEPC"}), [])
        self.assertEqual(wa.schedule_reasons(config(expect_unit="灯織"), ok), ["unit_mismatch"])
        # Rows of the other scenarios carry no books (G.R.A.D. row, live 2026-10-30).
        grad = {**ok, "layout": "train", "books": None, "scenario": "GRAD"}
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="GRAD"), grad), [])
        self.assertEqual(wa.schedule_reasons(config(), {**grad, "items": 1}), ["row1_items_set"])
        # ファン感謝祭/LP banners are unsampled: a set row is not trusted, a row Maa just set is.
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="LP"), ok), ["scenario_banner_unsampled"])
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="GRAD"), ok), ["scenario_mismatch"])
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="LP"), ok, True, "結華"), [])
        self.assertEqual(wa.schedule_reasons(config(expect_scenario="STEP"), ok, True, "G.R.A.D."), ["unit_mismatch"])
        self.assertEqual(wa.decide_totals(config(runs=5), (5, 25), 150), [])
        self.assertEqual(wa.decide_totals(config(runs=5), (7, 25), 210), ["other_rows_scheduled_7", "required_stamina_210"])
        self.assertEqual(wa.dialog_reasons(config(runs=5), 300, 150), [])
        self.assertEqual(wa.dialog_reasons(config(runs=2), 300, 150), ["stamina_mismatch_300_150"])
        self.assertEqual(wa.dialog_reasons(config(runs=5), 120, 0), ["stamina_short_120", "stamina_mismatch_120_0"])
        self.assertEqual(wa.dialog_reasons(config(runs=5), None, 150), ["dialog_stamina_unread"])
        self.assertEqual(wa.dialog_reasons(config(runs=5, use_skip=True), 300, 150, 4), ["skip_left_4"])
        self.assertEqual(wa.dialog_reasons(config(runs=5, use_skip=True), 300, 150, 5), [])


class FakeReader:
    def __init__(self, page, **values):
        self._page, self.values = page, values

    def page(self, image):
        return self._page

    def home(self, image):
        return self.values["home"]

    def running_left(self, image):
        return self.values.get("left")

    def schedule(self, image):
        return self.values["schedule"]

    def dialog(self, image, use_skip):
        return self.values["dialog"]

    def caption(self, image):
        return self.values["caption"]

    def unit(self, image):
        return self.values["unit"]

    def unit_tab_page(self, image):
        return self.values.get("tab_page", 0)

    def unit_name(self, image):
        return self.values.get("unit_name", "結華")

    def presets(self, image):
        return self.values["presets"]

    def policy_choice(self, image):
        return self.values["choice"]

    def p_card(self, image):
        return self.values.get("p_card")

    def books(self, image):
        return self.values["books"]

    def history(self, image):
        return self.values["history"]

    def items(self, image):
        return self.values["items"]

    def recovery(self, image):
        return self.values["recovery"]

    def book_stats(self, image):
        return self.values["book_stats"]

    def picker(self, image):
        return self.values["picker"]

    def picker_detail(self, image):
        return self.values["detail"]

    def hit(self, node, image):
        return self.values.get("hits", {}).get(node, True)


PLAN = ({"title": "【オイサラバエル】樋口円香", "vo": 60, "da": 20, "vi": 20, "me": 30, "eval": 1020},
        {"title": "【ポシェットの中には】福丸小糸", "vo": 110, "da": 30, "vi": 30, "me": 40, "eval": 1010},
        {"title": "【ポシェットの中には】福丸小糸", "vo": 100, "da": 30, "vi": 40, "me": 40, "eval": 1000})


def picker_view(evals, selected=(), grey=(), sort="ノウハウ評価値順", order="▼降順"):
    """The picker's first screen: 評価値 per tile in reading order (padded with 900), ✓ and grey by (r, c)."""
    evals = list(evals) + [900] * (wa.PICKER_COLS * wa.PICKER_ROWS - len(evals))
    tiles = [{"r": i // wa.PICKER_COLS, "c": i % wa.PICKER_COLS, "eval": value,
              "grey": (i // wa.PICKER_COLS, i % wa.PICKER_COLS) in grey,
              "selected": (i // wa.PICKER_COLS, i % wa.PICKER_COLS) in selected} for i, value in enumerate(evals)]
    return {"sort": sort, "order": order, "tiles": tiles}


def detail(book, **stats):
    return {"title": book["title"], "stats": {**{k: book[k] for k in wa.STATS}, **stats}}


class FakeContext:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def get_node_data(self, name):
        return {"enabled": self.enabled}


class FakeAuthority:
    def __init__(self, allowed=("weekly-auto-start",), refusal=None):
        self.allowed, self.refusal, self.granted = allowed, refusal, []

    def can_spend(self, action):
        return action in self.allowed

    def authorize(self, action, image, approver):
        self.granted.append((action, approver))
        return self.refusal

    def last_outcome(self, action):
        return "sent"


def recovery(after, before=300, counts=(0, 0, 0), owned=(162, 469, 666)):
    return {"before": before, "after": after, "required": (after, 750) if after is not None else None,
            "owned": list(owned), "counts": list(counts)}


def schedule_state(count, **kw):
    return {"row": "configured", "items": 0, "books": 3, "loop": "off", "scenario": "STEP", "unit_name": "結華",
            "policy": "Vocal", "count": count, "total_runs": (count, 25), "total_stamina": 30 * count, **kw}


class RuntimeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=PRIVATE))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.authority = FakeAuthority()
        self.rt = wa.WeeklyAutoRuntime(authority=self.authority, log_root=self.root, vision=lambda context: None)
        self.rt._context = FakeContext()

    def begin(self, **kw):
        self.rt.run = wa.WeeklyAutoRun(config(**kw), self.rt.log)

    def decide(self, reader):
        return self.rt._decide(reader, IMAGE)

    def test_home_checks_running_and_stamina_before_opening(self):
        self.begin(runs=5)
        reader = FakeReader("home", home={"running": True, "ready": False, "stamina": (150, 300)})
        self.assertEqual(self.decide(reader).kind, "home_running")      # collect first (one tap)
        self.rt.run.collect, self.rt.run.phase = "collected", "start"
        self.assertEqual(self.decide(reader).kind, "none")
        self.assertEqual(self.rt.run.reason, "auto_produce_running")
        self.begin(runs=5)
        self.decide(FakeReader("home", home={"running": False, "ready": True, "stamina": (120, 300)}))
        self.assertEqual(self.rt.run.reason, "stamina_short_120")
        self.begin(runs=4)
        decision = self.decide(FakeReader("home", home={"running": False, "ready": True, "stamina": (120, 300)}))
        self.assertEqual((decision.kind, self.rt.run.phase), ("home_open", "to_select"))

    def test_a_finished_auto_is_collected_then_the_next_one_starts(self):
        self.begin(runs=1)
        running = FakeReader("home", home={"running": True, "ready": False, "stamina": (283, 300)})
        self.assertEqual(self.decide(running).kind, "home_running")
        self.assertEqual((self.rt.run.phase, self.rt.run.collect), ("collect", "tapped"))
        self.assertEqual(self.decide(FakeReader("end")).kind, "end_ok")
        self.assertEqual(self.decide(FakeReader("loading")).kind, "wait")
        self.assertEqual(self.decide(FakeReader("result")).kind, "result_ok")
        self.assertEqual(self.decide(FakeReader("result")).kind, "wait")         # never a second OK
        self.assertEqual(self.decide(FakeReader("unknown")).kind, "go_home")     # ミッション達成 / イベント累計報酬: GoHome
        self.assertEqual(self.decide(FakeReader("unknown")).kind, "wait")
        ready = FakeReader("home", home={"running": False, "ready": True, "stamina": (283, 300)})
        self.assertEqual(self.decide(ready).kind, "home_open")
        self.assertEqual((self.rt.run.collect, self.rt.run.phase), ("collected", "to_select"))
        self.assertTrue(self.rt.run.summary()["collected"])
        self.assertEqual(self.decide(running).kind, "none")                     # never a second collect tap
        self.assertEqual(self.rt.run.reason, "auto_produce_running")

    def test_a_slow_click_decision_is_made_again_on_the_next_frame(self):
        ticks = iter([0.0, 5.0, 5.0, 10.0, 10.5])
        self.rt.clock = lambda: next(ticks)
        self.begin(runs=4)
        reader = FakeReader("home", home={"running": False, "ready": True, "stamina": (300, 300)})
        with mock.patch.object(wa, "Reader", lambda vision: reader):
            self.assertEqual(self.rt.decide(FakeContext(), IMAGE).reason, "decision_too_slow")   # guard would refuse
            self.assertEqual(self.rt.run.phase, "start")
            self.assertEqual(self.rt.decide(FakeContext(), IMAGE).kind, "home_open")          # not cached
        self.assertEqual(self.rt.run.phase, "to_select")

    def test_collect_hands_popups_to_go_home_a_bounded_number_of_times(self):
        self.begin()
        self.rt.run.phase, self.rt.run.collect = "collect", "result_ok"
        kinds = [self.decide(FakeReader("unknown")).kind for _ in range(7)]
        self.assertEqual(kinds, ["wait", "go_home", "wait", "go_home", "wait", "none", "none"])
        self.assertEqual(self.rt.run.reason, "collect_home_not_reached")

    def test_a_running_auto_is_left_alone_with_its_time_left(self):
        self.begin()
        running = FakeReader("home", home={"running": True, "ready": False, "stamina": (270, 300)})
        self.assertEqual(self.decide(running).kind, "home_running")
        self.assertEqual(self.decide(FakeReader("running", left="56:44")).kind, "running_cancel")
        self.assertEqual(self.decide(FakeReader("running", left="56:44")).kind, "wait")    # never 終了 / スキップ
        self.assertEqual(self.decide(running).kind, "none")
        self.assertEqual(self.rt.run.reason, "auto_produce_running")
        self.assertEqual(self.rt.run.summary()["running_left"], "56:44")
        self.assertFalse(self.rt.run.summary()["collected"])

    def test_collect_stops_when_the_tap_opens_nothing_or_hangs(self):
        self.begin()
        running = FakeReader("home", home={"running": True, "ready": False, "stamina": (283, 300)})
        self.decide(running)
        for _ in range(wa.MAX_WAIT_FRAMES + 1):
            decision = self.decide(running)
        self.assertEqual((decision.kind, self.rt.run.reason), ("none", "collect_not_opened"))
        self.begin()
        self.decide(running)
        self.decide(FakeReader("end"))
        for _ in range(wa.MAX_COLLECT_FRAMES + 1):
            self.decide(FakeReader("loading"))
        self.assertEqual(self.rt.run.reason, "collect_end_ok_timeout")
        self.assertFalse(self.rt.run.summary()["collected"])

    def test_disabled_click_node_stops_without_input(self):
        self.rt._context = FakeContext(enabled=False)
        self.begin()
        self.assertEqual(self.decide(FakeReader("select")).kind, "none")
        self.assertEqual((self.rt.run.outcome, self.rt.run.reason), ("stopped", "input_disabled:open_auto"))

    def test_count_taps_are_verified_one_by_one(self):
        self.begin(runs=3)
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1))).kind, "plus")
        self.assertEqual(self.rt.run.pending, (1, 2))
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(2))).kind, "plus")
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(3))).kind, "decide")
        self.assertEqual((self.rt.run.taps, self.rt.run.phase), (2, "to_dialog"))

    def test_unchanged_count_waits_then_stops_and_a_jump_stops_at_once(self):
        self.begin(runs=3)
        self.decide(FakeReader("schedule", schedule=schedule_state(1)))
        kinds = [self.decide(FakeReader("schedule", schedule=schedule_state(1))).kind
                 for _ in range(wa.MAX_PENDING_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.MAX_PENDING_FRAMES + ["none"])
        self.assertEqual((self.rt.run.reason, self.rt.run.taps), ("count_not_changed", 1))
        self.begin(runs=3)
        self.decide(FakeReader("schedule", schedule=schedule_state(5)))
        self.decide(FakeReader("schedule", schedule=schedule_state(2)))
        self.assertEqual(self.rt.run.reason, "count_unexpected_5_2")

    def test_schedule_stops_on_loop_rows_and_totals(self):
        for state, reason in (({"row": "empty"}, "row1_empty"),
                              (schedule_state(1, total_runs=(3, 25)), "other_rows_scheduled_3"),
                              (schedule_state(1, items=1), "row1_items_set")):
            with self.subTest(reason):
                self.begin()
                self.decide(FakeReader("schedule", schedule=state))
                self.assertEqual(self.rt.run.reason, reason)

    def test_loop_on_is_turned_off_once(self):
        # 2026-10-30: 「ループする」 is never used (ON needs 750 stamina -> recovery items); one tap turns it off
        self.begin()
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1, loop="on"))).kind, "loop_off")
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1))).kind, "decide")
        self.begin()
        self.decide(FakeReader("schedule", schedule=schedule_state(1, loop="on")))
        kinds = [self.decide(FakeReader("schedule", schedule=schedule_state(1, loop="on"))).kind
                 for _ in range(wa.MAX_PENDING_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.MAX_PENDING_FRAMES + ["none"])
        self.assertEqual(self.rt.run.reason, "loop_not_off")
        self.begin()                                     # with another problem on the row: stop, no tap
        self.decide(FakeReader("schedule", schedule=schedule_state(1, loop="on", items=1)))
        self.assertEqual(self.rt.run.reason, "row1_items_set,loop_on")

    def test_loop_turns_loop_on_and_checks_25_runs(self):
        self.begin(loop=True)
        self.assertEqual(self.decide(FakeReader("home", home={"running": False, "ready": True,
                                                              "stamina": (300, 300)})).kind, "home_open")
        self.begin(loop=True)
        self.decide(FakeReader("home", home={"running": False, "ready": True, "stamina": (299, 300)}))
        self.assertEqual(self.rt.run.reason, "stamina_short_299")   # 299 + ソーダ上限 450 < 750
        self.begin(loop=True, soda_cap=600)
        self.assertEqual(self.decide(FakeReader("home", home={"running": False, "ready": True,
                                                              "stamina": (150, 300)})).kind, "home_open")
        self.begin(loop=True)
        loop_state = schedule_state(1, loop="on", total_runs=(25, 25), total_stamina=750)
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1))).kind, "loop_on")
        self.assertEqual(self.decide(FakeReader("schedule", schedule=loop_state)).kind, "decide")
        self.begin(loop=True)
        self.decide(FakeReader("schedule", schedule={**loop_state, "total_runs": (5, 25)}))
        self.assertEqual(self.rt.run.reason, "other_rows_scheduled_5")

    def test_recovery_sodas_are_tapped_one_by_one_then_start(self):
        self.authority.allowed = ("weekly-auto-soda-start",)
        self.begin(loop=True)
        self.rt.run.phase = "to_dialog"
        steps = [(300, (0, 0, 0), "soda_30"), (330, (1, 0, 0), "soda_60"), (390, (1, 1, 0), "soda_60"),
                 (450, (1, 2, 0), "soda_100"), (550, (1, 2, 1), "soda_100"), (650, (1, 2, 2), "soda_100"),
                 (750, (1, 2, 3), "recovery_start")]
        for after, counts, kind in steps:
            with self.subTest(after):
                decision = self.decide(FakeReader("recovery", recovery=recovery(after, counts=counts)))
                self.assertEqual(decision.kind, kind)
        self.assertEqual((self.rt.run.phase, self.rt.run.soda_taps), ("awaiting_result", 6))
        self.assertEqual(self.authority.granted, [("weekly-auto-soda-start", wa.APPROVER_RECOVERY)])
        self.assertEqual(self.rt.run.intent["sodas"], {30: 1, 60: 2, 100: 3})

    def test_recovery_cancels_unless_every_check_passes(self):
        def walk(reader_values, **kw):
            self.begin(**{"loop": True, **kw})
            self.rt.run.phase = "to_dialog"
            decision = None
            for values in reader_values:
                decision = self.decide(FakeReader("recovery", recovery=values))
            return decision

        self.authority.allowed = ()
        frames = [recovery(after, counts=c) for after, c in ((300, (0, 0, 0)), (330, (1, 0, 0)), (390, (1, 1, 0)),
                                                             (450, (1, 2, 0)), (550, (1, 2, 1)), (650, (1, 2, 2)),
                                                             (750, (1, 2, 3)))]
        decision = walk(frames)           # 「开始 Auto」 off: sodas set and read, then キャンセル (nothing consumed)
        self.assertEqual((decision.kind, self.rt.run.stop_after_cancel), ("recovery_cancel", "spend_not_enabled"))
        self.assertEqual(self.authority.granted, [])
        self.decide(FakeReader("schedule", schedule=schedule_state(1)))
        self.assertEqual((self.rt.run.outcome, self.rt.run.reason), ("stopped", "spend_not_enabled"))
        self.authority.allowed = ("weekly-auto-soda-start",)
        for values, kw, reason in (
                ([recovery(300)] + [recovery(300)] * (wa.MAX_PENDING_FRAMES + 1), {}, "soda_30_not_changed"),
                ([recovery(300), recovery(360, counts=(None, 1, None))], {}, "soda_unexpected_0_60"),
                ([recovery(200, before=200)], {}, "soda_cap_short_200"),
                ([recovery(330)], {}, "soda_preset_30"),
                ([recovery(300, owned=(None, 469, 666))], {}, "soda_owned_unread"),
                ([recovery(300, owned=(0, 0, 4))], {}, "soda_short"),
                ([recovery(300), recovery(330, counts=(0, 1, 0))], {}, "soda_count_mismatch"),
                ([{**recovery(300), "required": (300, 600)}], {}, "required_stamina_300_600"),
                ([recovery(300), recovery(330, before=301)], {}, "stamina_changed_300_301"),
                ([recovery(300)], {"loop": False}, "stamina_short_300")):
            with self.subTest(reason):
                decision = walk(values, **kw)
                self.assertEqual((decision.kind, self.rt.run.stop_after_cancel), ("recovery_cancel", reason))
        decision = walk([recovery(None)] * (wa.MAX_PENDING_FRAMES + 1))
        self.assertEqual((decision.kind, self.rt.run.stop_after_cancel), ("recovery_cancel", "recovery_unread"))
        self.begin(loop=True)              # ループ on a plain dialog (750+ stamina on hand): unsampled, cancel
        self.rt.run.phase = "to_dialog"
        decision = self.decide(FakeReader("dialog", dialog={"before": 800, "after": 50, "skip_left": 5}))
        self.assertEqual((decision.kind, self.rt.run.stop_after_cancel), ("cancel", "loop_plain_dialog"))

    def test_soda_cap_over_the_policy_stops_at_begin(self):
        class Context:
            def get_node_data(self, name):
                return {"attach": {"loop": True, "soda_cap": 600}}
        original = wa.stamina_item_cap
        self.addCleanup(setattr, wa, "stamina_item_cap", original)
        wa.stamina_item_cap = lambda path=None: 450
        self.assertFalse(self.rt.begin(Context(), {"config_nodes": ["SC.WeeklyProduce.Config.Loop"]}))
        self.assertEqual(self.rt.run.reason, "soda_cap_over_policy")
        wa.stamina_item_cap = lambda path=None: 750
        self.assertTrue(self.rt.begin(Context(), {"config_nodes": ["SC.WeeklyProduce.Config.Loop"]}))

    def test_dialog_grants_only_after_the_stamina_check(self):
        self.begin(runs=5)
        self.rt.run.phase = "to_dialog"
        decision = self.decide(FakeReader("dialog", dialog={"before": 300, "after": 150, "skip_left": 5}))
        self.assertEqual((decision.kind, self.rt.run.phase), ("start", "awaiting_result"))
        self.assertEqual(self.authority.granted, [("weekly-auto-start", wa.APPROVER)])
        self.begin(runs=2)
        self.rt.run.phase = "to_dialog"
        decision = self.decide(FakeReader("dialog", dialog={"before": 300, "after": 150, "skip_left": 5}))
        self.assertEqual((decision.kind, self.rt.run.stop_after_cancel), ("cancel", "stamina_mismatch_300_150"))
        self.assertEqual(len(self.authority.granted), 1)
        self.decide(FakeReader("schedule", schedule=schedule_state(2)))
        self.assertEqual((self.rt.run.outcome, self.rt.run.reason), ("stopped", "stamina_mismatch_300_150"))

    def test_dialog_without_spend_permission_cancels(self):
        self.rt.authority = FakeAuthority(allowed=())
        self.begin(runs=5)
        self.rt.run.phase = "to_dialog"
        decision = self.decide(FakeReader("dialog", dialog={"before": 300, "after": 150, "skip_left": None}))
        self.assertEqual((decision.kind, decision.reason), ("cancel", "spend_not_enabled"))

    def test_skip_needs_its_option_and_enough_daily_skips(self):
        self.rt.authority = FakeAuthority(allowed=("weekly-auto-skip",))
        self.begin(runs=5, use_skip=True)
        self.rt.run.phase = "to_dialog"
        decision = self.decide(FakeReader("dialog", dialog={"before": 300, "after": 150, "skip_left": 4}))
        self.assertEqual((decision.kind, decision.reason), ("cancel", "skip_left_4"))
        self.begin(runs=5, use_skip=True)
        self.rt.run.phase = "to_dialog"
        decision = self.decide(FakeReader("dialog", dialog={"before": 300, "after": 150, "skip_left": 5}))
        self.assertEqual(decision.kind, "skip")

    def test_result_is_the_running_home_or_uncertain(self):
        self.begin(runs=5)
        self.rt.run.phase = "awaiting_result"
        self.assertEqual(self.decide(FakeReader("loading")).kind, "wait")
        self.decide(FakeReader("home", home={"running": True, "ready": False, "stamina": (150, 300)}))
        self.assertTrue(self.rt.run.succeeded)
        self.begin(runs=5)
        self.rt.run.phase = "awaiting_result"
        for _ in range(wa.MAX_RESULT_FRAMES + 1):
            self.decide(FakeReader("unknown"))
        self.assertEqual((self.rt.run.outcome, self.rt.run.reason), ("spend_uncertain", "running_not_observed"))
        self.assertTrue(self.rt.run.halted)

    def test_wizard_and_unknown_screens_stop(self):
        self.begin()
        self.decide(FakeReader("w_select"))
        self.assertEqual(self.rt.run.reason, "row1_setup_off")
        self.begin(setup="maa", expect_scenario="STEP")
        self.rt.run.phase = "setup_unit"
        self.decide(FakeReader("w_books"))
        self.assertEqual(self.rt.run.reason, "w_books_after_setup_unit")
        self.begin(setup="maa", expect_scenario="STEP")
        self.rt.run.phase = "setup_books"
        self.decide(FakeReader("w_tutorial"))
        self.assertEqual(self.rt.run.reason, "auto_tutorial_shown")
        self.begin()
        for _ in range(wa.MAX_WAIT_FRAMES + 1):
            self.decide(FakeReader("unknown"))
        self.assertEqual(self.rt.run.reason, "unknown_screen")

    def test_empty_row_stops_unless_maa_sets_it(self):
        self.begin()
        self.decide(FakeReader("schedule", schedule={"row": "empty"}))
        self.assertEqual(self.rt.run.reason, "row1_empty")
        self.begin(setup="maa", expect_scenario="STEP")
        decision = self.decide(FakeReader("schedule", schedule={"row": "empty"}))
        self.assertEqual((decision.kind, self.rt.run.phase), ("row_set", "setup_select"))

    def test_step_setup_walks_the_wizard_once(self):
        self.begin(setup="maa", expect_scenario="STEP", unit=4)
        empty = {"row": "empty"}
        steps = [
            (FakeReader("schedule", schedule=empty), "row_set"),
            (FakeReader("schedule", schedule=empty), "wait"),          # the tap has not left the schedule yet
            (FakeReader("unknown"), "wait"),
            (FakeReader("w_select", caption="W.I.N.G.編"), "tab_special"),
            (FakeReader("w_select", caption="W.I.N.G.編"), "wait"),   # the tab change is not shown yet
            (FakeReader("w_select", caption="S.T.E.P.編"), "scenario_next"),
            (FakeReader("w_select", caption="S.T.E.P.編"), "wait"),
            (FakeReader("w_unit", unit=1), "unit_tab_4"),
            (FakeReader("w_unit", unit=4), "unit_next"),
            (FakeReader("w_books", books={"filled": 0, "prompt": True}), "book_history"),
            (FakeReader("w_history", history={"date": "2026/10/22 22:05", "call": True}), "history_call"),
            (FakeReader("w_history", history={"date": "2026/10/22 22:05", "call": True}), "wait"),
            (FakeReader("w_books", books={"filled": 1, "prompt": False}), "wait"),   # slots still filling
            (FakeReader("w_books", books={"filled": 3, "prompt": False}), "book_next"),
            (FakeReader("w_item", items=2), "item_decide"),
            (FakeReader("schedule", schedule=schedule_state(1)), "decide"),
        ]
        for index, (reader, kind) in enumerate(steps):
            with self.subTest(index):
                self.assertEqual(self.decide(reader).kind, kind, self.rt.run.reason)
        self.assertTrue(self.rt.run.setup_done)
        self.assertEqual(self.rt.run.setup_taps, 2)

    def test_refused_history_call_falls_back_to_omakase(self):
        # 2026-11-21 live: every 設定履歴 set held 霧子's book, 霧子 was in the unit -> 「呼び出し」 greyed, page stayed.
        self.begin(setup="maa", expect_scenario="STEP", unit=4)
        self.rt.run.phase = "setup_books"
        history = FakeReader("w_history", history={"date": "2026/11/15 20:25", "call": True})
        self.assertEqual(self.decide(FakeReader("w_books", books={"filled": 0, "prompt": True})).kind, "book_history")
        self.assertEqual(self.decide(history).kind, "history_call")
        kinds = [self.decide(history).kind for _ in range(wa.HISTORY_CALL_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.HISTORY_CALL_FRAMES + ["history_back"])
        self.assertEqual(self.decide(FakeReader("w_books", books={"filled": 3, "prompt": False})).kind, "book_omakase")
        self.assertEqual(self.decide(FakeReader("w_books", books={"filled": 3, "prompt": False})).kind, "book_next")

    def test_short_slots_after_history_fall_back_to_omakase(self):
        self.begin(setup="maa", expect_scenario="STEP", unit=4)
        self.rt.run.phase = "setup_books"
        self.rt.run.history_called = True
        short = FakeReader("w_books", books={"filled": 2, "prompt": True})
        kinds = [self.decide(short).kind for _ in range(wa.BOOK_FILL_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.BOOK_FILL_FRAMES + ["book_omakase"])
        self.assertEqual(self.decide(FakeReader("w_books", books={"filled": 3, "prompt": False})).kind, "book_next")

    def test_wing_setup_skips_the_books_and_a_set_row_is_never_edited(self):
        self.begin(setup="maa", expect_scenario="WING")
        self.rt.run.phase = "setup_select"
        self.assertEqual(self.decide(FakeReader("w_select", caption="W.I.N.G.編")).kind, "scenario_next")
        self.assertEqual(self.decide(FakeReader("w_unit", unit=1)).kind, "unit_next")
        self.assertEqual(self.rt.run.phase, "setup_item")
        self.decide(FakeReader("w_books", books={"filled": 0, "prompt": True}))
        self.assertEqual(self.rt.run.reason, "w_books_after_setup_item")
        self.begin(setup="maa", expect_scenario="STEP")
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1))).kind, "decide")

    def test_setup_stops_on_unexpected_readings(self):
        cases = (
            ("setup_select", FakeReader("w_select", caption="say \"Halo\"編"), "scenario_unknown:say \"Halo\"編"),
            ("setup_select", FakeReader("w_select", caption="LandingPoint編"), "scenario_above:LandingPoint編"),
            ("setup_item", FakeReader("w_item", items=1), None),
        )
        for phase, reader, reason in cases:
            with self.subTest(reason):
                self.begin(setup="maa", expect_scenario="GRAD" if reason and "above" in reason else "STEP")
                self.rt.run.phase = phase
                decision = self.decide(reader)
                if reason is None:
                    self.assertEqual(decision.kind, "wait")
                    for _ in range(wa.MAX_WAIT_FRAMES):
                        self.decide(reader)
                    self.assertEqual(self.rt.run.reason, "items_set_1")
                else:
                    self.assertEqual(self.rt.run.reason, reason)
        self.begin(setup="maa", expect_scenario="STEP")
        self.rt.run.phase = "setup_select"
        self.decide(FakeReader("w_select", caption="W.I.N.G.編"))
        kinds = [self.decide(FakeReader("w_select", caption="W.I.N.G.編")).kind for _ in range(wa.MAX_PENDING_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.MAX_PENDING_FRAMES + ["none"])
        self.assertEqual(self.rt.run.reason, "tab_special_not_changed")
        self.begin(setup="maa", expect_scenario="STEP")
        self.decide(FakeReader("schedule", schedule={"row": "empty"}))
        for _ in range(wa.MAX_LEAVE_FRAMES + 1):
            self.decide(FakeReader("schedule", schedule={"row": "empty"}))
        self.assertEqual(self.rt.run.reason, "row_set_page_not_left")

    def test_train_scenario_steps_down_the_list(self):
        self.begin(setup="maa", expect_scenario="GRAD")
        self.rt.run.phase = "setup_select"
        steps = [
            (FakeReader("w_select", caption="S.T.E.P.編"), "tab_train"),
            (FakeReader("w_select", caption="W.I.N.G.編"), "scenario_down"),
            (FakeReader("w_select", caption="W.I.N.G.編"), "wait"),
            (FakeReader("w_select", caption="ファン感謝祭編"), "scenario_down"),
            (FakeReader("w_select", caption="G.R.A.D.編"), "scenario_next"),
        ]
        for index, (reader, kind) in enumerate(steps):
            with self.subTest(index):
                self.assertEqual(self.decide(reader).kind, kind, self.rt.run.reason)
        self.assertEqual((self.rt.run.phase, self.rt.run.setup_taps), ("setup_unit", 3))

    def test_unit_on_the_second_page(self):
        self.begin(setup="maa", expect_scenario="WING", unit=14)
        self.rt.run.phase = "setup_unit"
        steps = [
            (FakeReader("w_unit", unit=1, tab_page=0), "unit_page_next"),
            (FakeReader("w_unit", unit=1, tab_page=0), "wait"),
            (FakeReader("w_unit", unit=11, tab_page=1), "unit_tab_4"),
            (FakeReader("w_unit", unit=14, tab_page=1, unit_name="ノクチル"), "unit_next"),
        ]
        for index, (reader, kind) in enumerate(steps):
            with self.subTest(index):
                self.assertEqual(self.decide(reader).kind, kind, self.rt.run.reason)
        self.assertEqual(self.rt.run.unit_name, "ノクチル")
        self.begin(setup="maa", expect_scenario="WING", unit=3)
        self.rt.run.phase = "setup_unit"
        self.assertEqual(self.decide(FakeReader("w_unit", unit=12, tab_page=1)).kind, "unit_page_prev")
        self.begin(setup="maa", expect_scenario="WING", unit=3)
        self.rt.run.phase = "setup_unit"
        self.assertEqual(self.decide(FakeReader("w_unit", unit=3, unit_name=None)).kind, "wait")

    def test_books_from_a_preset(self):
        self.begin(setup="maa", expect_scenario="STEP", books="preset2")
        self.rt.run.phase = "setup_books"
        presets = [{"label": "プリセット1", "name": "A", "set": True}, {"label": "プリセット2", "name": "B", "set": True}]
        steps = [
            (FakeReader("w_books", books={"filled": 3, "prompt": False}), "book_preset"),   # slots on arrival: replaced
            (FakeReader("w_preset", presets=[presets[0], {**presets[1], "set": False}]), "wait"),
            (FakeReader("w_preset", presets=presets), "preset_set_2"),
            (FakeReader("w_preset", presets=presets), "wait"),
            (FakeReader("w_books", books={"filled": 3, "prompt": False}), "book_next"),
        ]
        for index, (reader, kind) in enumerate(steps):
            with self.subTest(index):
                self.assertEqual(self.decide(reader).kind, kind, self.rt.run.reason)
        self.assertTrue(self.rt.run.preset_called)

    def begin_picker(self):
        self.begin(setup="maa", expect_scenario="STEP", books="target")
        self.rt.run.book_plan, self.rt.run.phase = PLAN, "setup_picker"

    def test_target_books_are_found_by_eval_and_checked_one_by_one(self):
        self.begin_picker()
        evals = [1020, 1010, 1010, 1000]   # (0, 1) is another book with 1010
        other = {"title": "【ふわふわ】福丸小糸", "stats": {"vo": 90, "da": 40, "vi": 40, "me": 40}}
        steps = [
            (picker_view(evals), None, "picker_tile_0_0"),
            (picker_view(evals, {(0, 0)}), detail(PLAN[0]), "picker_tile_0_1"),
            *[(picker_view(evals, {(0, 0), (0, 1)}), other, "wait")] * wa.MAX_PENDING_FRAMES,
            (picker_view(evals, {(0, 0), (0, 1)}), other, "picker_tile_0_1"),   # not the book: ✓ off again
            (picker_view(evals, {(0, 0)}), None, "picker_tile_0_2"),
            (picker_view(evals, {(0, 0), (0, 2)}), {**detail(PLAN[1]), "title": "【ポシエットの中には】福丸小糸"}, "picker_tile_0_3"),
            (picker_view(evals, {(0, 0), (0, 2), (0, 3)}), detail(PLAN[2]), "picker_decide"),
        ]
        for index, (view, shown, kind) in enumerate(steps):
            with self.subTest(index):
                self.assertEqual(self.decide(FakeReader("w_picker", picker=view, detail=shown)).kind, kind, self.rt.run.reason)
        self.assertEqual(self.rt.run.picker_done, {0: (0, 0), 1: (0, 2), 2: (0, 3)})
        self.assertTrue(self.rt.run.picker_decided)
        self.assertEqual(self.rt.run.phase, "setup_books")
        # back on the book page the three slots must show the plan's stats
        filled = {"filled": 3, "prompt": False}
        shown = [{k: b[k] for k in wa.STATS} for b in reversed(PLAN)]
        self.assertEqual(self.decide(FakeReader("w_books", books=filled, book_stats=shown)).kind, "book_next")
        self.begin_picker()
        self.rt.run.phase, self.rt.run.picker_decided = "setup_books", True
        wrong = [{**shown[0], "vo": 1}, *shown[1:]]
        self.assertEqual(self.decide(FakeReader("w_books", books=filled, book_stats=wrong)).kind, "none")
        self.assertEqual(self.rt.run.reason, "target_books_mismatch")

    def test_target_books_stop_instead_of_guessing(self):
        cases = [
            (picker_view([1020, 1010, 1000], sort="入手順"), "picker_sort:入手順/▼降順"),
            (picker_view([1020, 1010, 1000], order="▲昇順"), "picker_sort:ノウハウ評価値順/▲昇順"),
            (picker_view([1020, 1010, 1000], grey={(0, 0)}), "target_book_blocked_1"),
            (picker_view([1010, 1000]), "target_book_not_visible_1"),
            (picker_view([1020, 1010, 1000], selected={(0, 4)}), "picker_selection_unexpected"),
        ]
        for view, reason in cases:
            with self.subTest(reason):
                self.begin_picker()
                self.assertEqual(self.decide(FakeReader("w_picker", picker=view)).kind, "none")
                self.assertEqual(self.rt.run.reason, reason)
        self.begin_picker()
        unread = picker_view([1020, 1010, 1000])
        unread["tiles"][3]["eval"] = None
        self.assertEqual(self.decide(FakeReader("w_picker", picker=unread)).kind, "wait")
        self.begin_picker()
        self.rt.run.picker_taps = wa.MAX_PICKER_TAPS
        self.assertEqual(self.decide(FakeReader("w_picker", picker=picker_view([1020, 1010, 1000]))).kind, "none")
        self.assertEqual(self.rt.run.reason, "picker_tap_budget")
        # slots already filled on arrival would start the picker with their ✓
        self.begin(setup="maa", expect_scenario="STEP", books="target")
        self.rt.run.book_plan, self.rt.run.phase = PLAN, "setup_books"
        self.assertEqual(self.decide(FakeReader("w_books", books={"filled": 2, "prompt": False})).kind, "none")
        self.assertEqual(self.rt.run.reason, "books_preset_2")

    def test_a_decided_tap_whose_node_never_ran_is_undone(self):
        # Live 2026-10-31 (external-auto-20261031b): 次へ decided on a frame where UnitNext's guards failed; the run then
        # waited for the unit page to leave and stopped with unit_next_page_not_left although no tap was sent.
        self.begin(setup="maa", expect_scenario="STEP", books="history", unit=20)
        self.rt.run.phase = "setup_unit"
        unit_page = FakeReader("w_unit", unit=20, tab_page=1, unit_name="ユニット20", p_card="1040020010")
        frames = iter(np.full((720, 1280, 3), i, dtype=np.uint8) for i in range(10))
        with mock.patch.object(wa, "Reader", lambda vision: unit_page):
            first = self.rt.decide(FakeContext(), next(frames))
            self.assertEqual(first.kind, "unit_next")
            self.assertEqual((self.rt.run.phase, self.rt.run.leaving), ("setup_books", ("w_unit", "unit_next")))
            # no check("unit_next"): the node did not run; the next frame decides 次へ again instead of waiting
            self.assertEqual(self.rt.decide(FakeContext(), next(frames)).kind, "unit_next")
            image = next(frames)
            self.assertEqual(self.rt.decide(FakeContext(), image).kind, "unit_next")
            self.assertTrue(self.rt.check(FakeContext(), image, {"stage": "unit_next"}))   # this time it ran
            self.assertEqual(self.rt.decide(FakeContext(), next(frames)).kind, "wait")    # now the page must leave
        self.assertEqual(self.rt.run.leaving, ("w_unit", "unit_next"))
        self.assertGreater(self.rt.run.leave_frames, 0)

    def test_book_plan_from_the_ui_profile(self):
        raw = [{**b, "title": b["title"].replace("】", "】 ")} for b in PLAN]
        self.assertEqual(wa.clean_book_plan(raw), PLAN)
        for bad in (raw[:2], [*raw[:2], {**raw[2], "eval": 2001}], [*raw[:2], {**raw[2], "vo": True}],
                    [*raw[:2], {**raw[2], "title": ""}], [*raw[:2], "x"], None):
            self.assertIsNone(wa.clean_book_plan(bad), bad)
        path = self.root / "profile.json"
        self.assertIsNone(wa.load_book_plan(path))
        path.write_text(json.dumps({"schema_version": 1, "prefs": {"auto_step_books": raw}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(wa.load_book_plan(path), PLAN)
        self.assertTrue(wa.book_matches(PLAN[1], "【ポシエットの中には】 福丸小糸", detail(PLAN[1])["stats"]))
        self.assertFalse(wa.book_matches(PLAN[1], PLAN[1]["title"], detail(PLAN[1], me=41)["stats"]))
        self.assertFalse(wa.book_matches(PLAN[1], "【オイサラバエル】樋口円香", detail(PLAN[1])["stats"]))
        self.assertFalse(wa.book_matches(PLAN[1], None, detail(PLAN[1])["stats"]))

    def test_target_books_need_a_saved_plan(self):
        context = FakeContext()
        context.get_node_data = lambda name: {"attach": {"setup": "maa", "expect_scenario": "STEP", "books": "target"}}
        rt = wa.WeeklyAutoRuntime(authority=self.authority, log_root=self.root, vision=lambda context: None)
        with mock.patch.object(wa, "load_book_plan", return_value=None):
            self.assertFalse(rt.begin(context, {"config_nodes": ["x"]}))
        self.assertEqual(rt.run.reason, "target_books_not_set")
        rt = wa.WeeklyAutoRuntime(authority=self.authority, log_root=self.root, vision=lambda context: None, book_plan=PLAN)
        self.assertTrue(rt.begin(context, {"config_nodes": ["x"]}))
        self.assertEqual(rt.run.book_plan, PLAN)

    def test_policy_is_set_then_verified_on_the_row(self):
        self.begin(policy="Dance")
        steps = [
            (FakeReader("schedule", schedule=schedule_state(1)), "policy_open"),
            (FakeReader("policy", choice="Vocal"), "policy_dance"),
            (FakeReader("policy", choice="Vocal"), "wait"),
            (FakeReader("policy", choice="Dance"), "policy_set"),
            (FakeReader("policy", choice="Dance"), "wait"),
            (FakeReader("schedule", schedule=schedule_state(1, policy="Dance")), "decide"),
        ]
        for index, (reader, kind) in enumerate(steps):
            with self.subTest(index):
                self.assertEqual(self.decide(reader).kind, kind, self.rt.run.reason)
        self.begin(policy="Vocal")
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1))).kind, "decide")

    def test_auto_policy_follows_the_p_card(self):
        from unittest import mock
        with mock.patch.object(wa, "policy_for_card", lambda card: {"1040020030": "Dance"}.get(card)):
            self.begin(setup="maa", expect_scenario="WING", policy="auto")
            self.rt.run.phase = "setup_unit"
            self.assertEqual(self.decide(FakeReader("w_unit", unit=1, p_card=None)).kind, "wait")   # still loading
            self.assertEqual(self.decide(FakeReader("w_unit", unit=1, p_card="1040020030")).kind, "unit_next")
            self.assertEqual((self.rt.run.p_card, self.rt.run.policy_target), ("1040020030", "Dance"))
            # never read: the row's 育成方針 is kept
            self.begin(setup="maa", expect_scenario="WING", policy="auto")
            self.rt.run.phase = "setup_unit"
            kinds = [self.decide(FakeReader("w_unit", unit=1, p_card=None)).kind for _ in range(wa.MAX_WAIT_FRAMES + 1)]
            self.assertEqual(kinds, ["wait"] * wa.MAX_WAIT_FRAMES + ["unit_next"])
            self.assertEqual(self.rt.run.policy_target, "")
            self.rt.run.phase, self.rt.run.setup_done = "to_schedule", True
            self.rt.run.leaving = None
            self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1, scenario="WING"))).kind,
                             "decide")

    def test_auto_team_checks_the_prep_p_card(self):
        # 自动编队: the Auto unit page must show the P card the UI engine's prep edited in
        self.rt.expect_p_card = "1040020010"
        self.begin(setup="maa", expect_scenario="WING", team="auto")
        self.rt.run.phase = "setup_unit"
        self.assertEqual(self.decide(FakeReader("w_unit", unit=1, p_card="1040020010")).kind, "unit_next")
        self.begin(setup="maa", expect_scenario="WING", team="auto")
        self.rt.run.phase = "setup_unit"
        self.assertEqual(self.decide(FakeReader("w_unit", unit=1, p_card="1040010010")).kind, "none")
        self.assertEqual(self.rt.run.reason, "auto_team_p_card_mismatch:1040010010")
        self.begin(setup="maa", expect_scenario="WING", team="auto")
        self.rt.run.phase = "setup_unit"
        kinds = [self.decide(FakeReader("w_unit", unit=1, p_card=None)).kind for _ in range(wa.MAX_WAIT_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.MAX_WAIT_FRAMES + ["none"])
        self.assertEqual(self.rt.run.reason, "auto_team_p_card_unread")

    def test_auto_team_needs_the_prep(self):
        class Nodes:
            def get_node_data(self, name):
                return {"attach": {"team": "auto"}} if name == "SC.WeeklyProduce.Config.Team" else {}
        param = {"config_nodes": ["SC.WeeklyProduce.Config.Team"]}
        self.assertFalse(self.rt.begin(Nodes(), param))
        self.assertEqual(self.rt.run.reason, "auto_team_not_prepared")
        self.rt.expect_p_card = "1040020010"
        self.assertTrue(self.rt.begin(Nodes(), param))
        with self.assertRaises(ValueError):
            wa.WeeklyAutoRuntime(expect_p_card="P-card")

    def test_tap_waits_until_its_target_is_readable(self):
        # Live 2026-10-30: the first 育成方針選択 frame (choices still animating) missed the Dance anchor.
        self.begin(policy="Dance")
        self.rt.run.phase = "policy"
        hidden = {wa.TARGET_ANCHORS["policy_dance"]: False}
        decision = self.decide(FakeReader("policy", choice="Vocal", hits=hidden))
        self.assertEqual((decision.kind, decision.reason), ("wait", "policy_dance_target_unread"))
        self.assertEqual((self.rt.run.setup_taps, self.rt.run.setup_pending), (0, None))
        self.assertEqual(self.decide(FakeReader("policy", choice="Vocal")).kind, "policy_dance")
        self.begin(policy="Dance")
        self.rt.run.phase = "policy"
        kinds = [self.decide(FakeReader("policy", choice="Vocal", hits=hidden)).kind for _ in range(wa.MAX_WAIT_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.MAX_WAIT_FRAMES + ["policy_cancel"])   # never left open
        self.assertEqual(self.rt.run.stop_after_cancel, "policy_dance_target_unread")
        self.begin(policy="Dance")
        self.rt.run.phase = "policy"
        self.rt.run.stop_after_cancel = "policy_unread"   # a キャンセル decided while its button was not readable
        self.assertEqual(self.decide(FakeReader("policy", choice="Dance")).kind, "policy_cancel")

    def test_policy_mismatch_cancels_and_stops(self):
        self.begin(policy="Visual")
        self.rt.run.phase = "policy"
        self.decide(FakeReader("policy", choice="Vocal"))
        kinds = [self.decide(FakeReader("policy", choice="Vocal")).kind for _ in range(wa.MAX_PENDING_FRAMES + 1)]
        self.assertEqual(kinds, ["wait"] * wa.MAX_PENDING_FRAMES + ["policy_cancel"])
        self.decide(FakeReader("schedule", schedule=schedule_state(1)))
        self.assertEqual(self.rt.run.reason, "policy_visual_not_changed")
        self.begin(policy="Dance")
        self.rt.run.phase, self.rt.run.policy_done = "policy_set", True
        self.decide(FakeReader("schedule", schedule=schedule_state(1)))
        self.assertEqual(self.rt.run.reason, "policy_not_applied_Vocal")
        self.begin(policy="Dance")
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1, policy=None))).kind, "wait")
        self.begin()
        self.decide(FakeReader("policy", choice="Vocal"))
        self.assertEqual(self.rt.run.reason, "policy_dialog_after_start")

    def test_set_row_must_show_the_unit_read_in_the_wizard(self):
        self.begin(setup="maa", expect_scenario="GRAD")
        self.rt.run.setup_done, self.rt.run.unit_name, self.rt.run.phase = True, "結華", "to_schedule"
        self.assertEqual(self.decide(FakeReader("schedule", schedule=schedule_state(1, scenario=None))).kind, "decide")
        self.begin(setup="maa", expect_scenario="GRAD")
        self.rt.run.setup_done, self.rt.run.unit_name, self.rt.run.phase = True, "G.R.A.D.", "to_schedule"
        self.decide(FakeReader("schedule", schedule=schedule_state(1)))
        self.assertEqual(self.rt.run.reason, "unit_mismatch")


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = load_jsonc(PIPELINE)
        cls.table = pa.default_table()

    def test_click_nodes_are_disabled_and_bound_to_disabled_actions(self):
        bound = self.table.node_actions(include_disabled=True)
        clicks = {n for n, node in self.nodes.items() if node.get("action") == "Click"}
        self.assertEqual(clicks, set(wa.CLICK_NODES.values()))
        for name in clicks:
            node = self.nodes[name]
            action = self.table.get(bound[name])
            with self.subTest(name):
                self.assertIs(node["enabled"], False)
                # 2026-10-29: every action but スキップ is enabled; the launcher enables the nodes.
                self.assertEqual(action.enabled, action.id != "weekly-auto-skip")
                self.assertEqual(action.family, "weekly-auto")
                self.assertEqual(node["target"], list(action.rect))
                self.assertTrue(node["on_error"])
                self.assertEqual(node["recognition"], "And")
                kind = next(k for k, v in wa.CLICK_NODES.items() if v == name)
                self.assertEqual(node["all_of"][-1], wa.want_node(kind))
                self.assertEqual(self.nodes[wa.want_node(kind)]["custom_recognition_param"], {"stage": kind})
                self.assertTrue(all(n in self.nodes or n.startswith(("SC.Common.", "SC.Nav.", "SC.Loading."))
                                    for n in node["all_of"]), name)
        for kind, anchor in wa.TARGET_ANCHORS.items():   # the runtime reads the node's own target anchor
            self.assertIn(anchor, self.nodes[wa.CLICK_NODES[kind]]["all_of"], kind)
        self.assertEqual(wa.want_node("home_open"), "SC.WeeklyProduce.Want.HomeOpen")
        self.assertEqual(wa.want_node("unit_tab_10"), "SC.WeeklyProduce.Want.UnitTab10")
        main = self.nodes["SC.WeeklyProduce.Main"]["next"]
        self.assertLessEqual(set(wa.CLICK_NODES.values()), set(main))
        consuming = {name for name in clicks if self.table.get(bound[name]).consuming}
        self.assertEqual(consuming, {wa.CLICK_NODES[k] for k in ("start", "skip", "recovery_start")})
        for name in consuming:   # an unobserved result after a press is uncertain, never an ordinary abort
            self.assertEqual(self.nodes[name]["on_error"], ["SC.WeeklyProduce.SpendError"])
            self.assertEqual(self.nodes[name]["max_hit"], 1)
        # Live 2026-10-30: a soda node with max_hit 1 skipped the second 「+」 of the same soda (soda_60_not_changed).
        for kind in ("plus", "minus"):
            self.assertGreaterEqual(self.nodes[wa.CLICK_NODES[kind]]["max_hit"], wa.MAX_TAPS)
        for size in wa.SODA_SIZES:
            self.assertGreaterEqual(self.nodes[wa.CLICK_NODES[f"soda_{size}"]]["max_hit"], wa.MAX_SODA_TAPS)
            self.assertGreaterEqual(wa.MAX_SODA_TAPS, -(-wa.LOOP_STAMINA // size))
        # A picker tile showing another book of the same 評価値 is tapped a second time to take its ✓ off.
        tiles = [wa.CLICK_NODES[f"picker_tile_{r}_{c}"] for r in range(wa.PICKER_ROWS) for c in range(wa.PICKER_COLS)]
        self.assertEqual(len(set(tiles)), 15)
        self.assertTrue(all(self.nodes[name]["max_hit"] == 2 for name in tiles))
        # Live 2026-10-25: the Produce page loaded for over 14 s under a 「Now Loading」 no guard recognises.
        for kind in ("home_open", "open_auto"):
            self.assertGreaterEqual(self.nodes[wa.CLICK_NODES[kind]]["timeout"], 30000)

    def test_task_options_map_to_config_nodes(self):
        doc = load_jsonc(TASK)
        task, = doc["task"]
        self.assertEqual(task["entry"], "SC.WeeklyProduce.Entry")
        self.assertFalse(task["default_check"])
        self.assertIn("消耗：体力 30×次数", task["description"])
        options = doc["option"]
        self.assertEqual([c["name"] for c in options["Auto次数"]["cases"]], [str(n) for n in range(1, wa.MAX_RUNS + 1)])
        self.assertEqual(options["Auto次数"]["default_case"], "1")
        self.assertEqual(options["Auto使用スキップ"]["default_case"], "No")
        self.assertEqual(options["Autoループ"]["default_case"], "No")
        self.assertEqual([c["name"] for c in options["Autoソーダ上限"]["cases"]], [str(n) for n in wa.SODA_CAPS])
        config_nodes = set(self.nodes["SC.WeeklyProduce.Entry"]["custom_action_param"]["config_nodes"])
        attach = {}
        for name in config_nodes:
            attach.update(self.nodes[name]["attach"])
        # 2026-10-30: no 「由 Maa 设定」 switch; the panel always sets an empty オート1 (W.I.N.G., 育成方針 自动 by default)
        # 编队 默认 自动编队: the UI engine's team-edit prep runs first
        self.assertEqual(wa.WeeklyAutoConfig.from_params(attach),
                         wa.WeeklyAutoConfig(expect_scenario="WING", setup="maa", policy="auto", team="auto"))
        self.assertEqual(options["Auto编队来源"]["default_case"], "自动编队")
        self.assertNotIn("Auto设定オート1", options)
        self.assertNotIn("不核对", [c["name"] for c in options["Auto核对剧本"]["cases"]])
        for option in options.values():
            overrides = [c["pipeline_override"] for c in option.get("cases", [])] + (
                [option["pipeline_override"]] if "pipeline_override" in option else [])
            for override in overrides:
                self.assertLessEqual(set(override), config_nodes)

    def test_launcher_registers_the_runtime_with_the_start_flag_only(self):
        from tools import run_tasks
        # 2026-10-29: プロデュース開始 has its own flag; スキップ (cost unverified) has none.
        self.assertIn("weekly-auto-start", run_tasks.SPEND_ACTIONS_ALLOWED)
        self.assertNotIn("weekly-auto-skip", run_tasks.SPEND_ACTIONS_ALLOWED)
        self.assertEqual(run_tasks.SPEND_FLAGS["allow_weekly_auto_spend"], "weekly-auto-start")
        self.assertEqual(run_tasks.SUPERVISED_TASK_SPEND["WeeklyProduce"], "weekly-auto-start")
        # ループ: the recovery dialog's プロデュース開始 is its own action, picked by the task option
        self.assertIn("weekly-auto-soda-start", run_tasks.SPEND_ACTIONS_ALLOWED)
        self.assertEqual(run_tasks.item_spend_action({"name": "WeeklyProduce", "option": {"Autoループ": "Yes"}}),
                         "weekly-auto-soda-start")
        self.assertEqual(run_tasks.item_spend_action({"name": "WeeklyProduce", "option": {"Autoループ": "No"}}),
                         "weekly-auto-start")
        loop = {"preset": None, "tasks": [{"name": "WeeklyProduce", "option": {"Autoループ": "Yes"}}]}
        self.assertEqual(run_tasks.spend_action_for_flag("allow_weekly_auto_spend", loop), "weekly-auto-soda-start")
        options = run_tasks.offline_replay_options({}, None, PRIVATE / "offline-replay" / "x")
        self.assertEqual(options["weekly_auto"]["log_root"], PRIVATE / "offline-replay" / "x" / "run_logs")


def _replay_ready():
    try:
        from tests.test_task_launcher import private_ready
        from tools import replay_weekly_produce as harness
    except Exception:
        return False
    return private_ready() and not harness.missing_frames()


@unittest.skipUnless(_replay_ready(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 captures-20261022-110140Z 原生帧")
class OfflineReplayTests(unittest.TestCase):
    def test_private_frame_replay(self):
        from tools import replay_weekly_produce as harness

        report = harness.replay()
        failed = [c for c in report["cases"] if not c["passed"]]
        self.assertFalse(failed, json.dumps(failed, ensure_ascii=False, default=str)[:4000])
        self.assertEqual(report["case_count"], len(harness.CASES))


if __name__ == "__main__":
    unittest.main()
