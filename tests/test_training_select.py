"""Training list adaptation: sort/filter reading, identity-based location, bounded scroll planning,
identity re-check after the tap and the 「未选择」 default card. No device, no input.

* Pure rules: always.
* Runtime (fake vision / fake locate): needs only the maa Python package.
* Real-frame location: the user's private captures, ui/game-assets and the private template bundle (skipped otherwise).
* Whole-task replays through tools/run_tasks.py --simulate-input: additionally MaaFramework and the OCR model.
"""
import dataclasses
import datetime as dt
import shutil
import unittest
from pathlib import Path
from unittest import mock

from agent.core import pipeline_actions as pa
from agent.core import task_common as tc
from agent.core import training_select as ts
from agent.core import training_task as tt
from tests import training_list_frames as F

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
TABLE = pa.default_table()
LEVEL_DESC = ts.ListOrder("level", "desc")
VOCAL_LIST = "dev_assets/private/train-list-20261012/05.png"   # 2026-10-12 live Vocal順/▼降順 list (private)


def view(status="absent", digest="d0", slot="r1c2", box=(405, 184, 108, 108), offset=184):
    target = ts.TileCheck(slot, box, 1.0, "rainbow", "017", None, "target")
    look = ts.TileCheck(slot, box, 0.9, "gold", "017", None, "lookalike", ("frame:gold",))
    return ts.ListView(status, offset, (target,) if status == "found" else (),
                       (look,) if status == "ambiguous" else (), 21, digest)


class OrderTests(unittest.TestCase):
    def test_known_orders(self):
        self.assertEqual(ts.parse_order("デフォルト", "▼降順", "絞り込み"), (ts.ListOrder("default", "desc"), []))
        self.assertEqual(ts.parse_order("Lv順", "▼降順", "絞り込み"), (LEVEL_DESC, []))
        self.assertEqual(ts.parse_order("LV順", "▲ 昇順", "絞り込み"), (ts.ListOrder("level", "asc"), []))

    def test_join_order_key_from_the_live_list(self):
        # 2026-10-11 live (sample-live-20261011/14): the user's list opened as 加入順/▼降順.
        self.assertEqual(ts.parse_order("加入順", "▼降順", "絞り込み"), (ts.ListOrder("joined", "desc"), []))
        self.assertEqual(ts.parse_order("Lv順", "降順", "絞り込み"), (LEVEL_DESC, []))

    def test_unknown_sort_filter_or_unreadable_never_guess(self):
        order, reasons = ts.parse_order("レア度順", "▼降順", "絞り込み")
        self.assertEqual((order, reasons, ts.order_outcome(reasons)), (None, ["sort_unknown:レア度順"], "list_sort_unknown"))
        order, reasons = ts.parse_order("Lv順", "▼降順", "絞り込みON")
        self.assertEqual((order, ts.order_outcome(reasons)), (None, "list_filter_active"))
        order, reasons = ts.parse_order("Lv順", "降順昇順", "絞り込み")
        self.assertEqual(reasons, ["direction_unknown:降順昇順"])
        order, reasons = ts.parse_order(None, "▼降順", "絞り込み")
        self.assertTrue(ts.only_unreadable(reasons))
        self.assertFalse(ts.only_unreadable(["sort_unreadable", "filter_active:x"]))


class SortPlanTests(unittest.TestCase):
    def test_wanted_direction(self):
        levels = {"2040010010": 1, "2040010020": 1, "2040010030": 80, "2040010040": 30}
        self.assertEqual(ts.wanted_direction(LEVEL_DESC, "2040010040", levels)[0], "desc")   # 1 above, 2 below
        self.assertEqual(ts.wanted_direction(LEVEL_DESC, "2040010010", levels)[0], "asc")
        self.assertEqual(ts.wanted_direction(LEVEL_DESC, "2050170010", levels),
                         ("asc", "target_level_unknown_low_level_assumed"))
        self.assertEqual(ts.wanted_direction(ts.ListOrder("default", "desc"), "2040010010", levels)[0], None)

    def test_owned_levels_reads_store_and_export_and_ignores_garbage(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            store, export, bad = Path(d) / "r.json", Path(d) / "o.js", Path(d) / "missing.json"
            store.write_text(json.dumps({"cards": {"2040010010": {"level": 12}, "x": {"level": 3}}}), encoding="utf-8")
            export.write_text("export default " + json.dumps({"details": {"2040010010": {"level": 99},
                                                                          "2050170010": {"level": 5}}}), encoding="utf-8")
            self.assertEqual(ts.owned_levels((store, export, bad)), {"2040010010": 12, "2050170010": 5})

    def test_sort_actions_are_bound_and_the_key_popup_is_enabled(self):
        # direction toggle enabled after the 2026-10-11 live rect check; key popup calibrated on the 2026-10-11 samples
        bound = TABLE.node_actions(include_disabled=True)
        self.assertEqual(bound[ts.SORT_NODE], ts.SORT_ACTION)
        self.assertEqual(bound[ts.SORT_OPEN_NODE], ts.SORT_OPEN_ACTION)
        self.assertEqual(bound[ts.SORT_APPLY_NODE], ts.SORT_APPLY_ACTION)
        self.assertEqual({bound[n] for n in ts.SORT_KEY_NODES.values()}, set(ts.SORT_KEY_ACTIONS))
        self.assertTrue(ts.sort_key_wired(TABLE))
        self.assertEqual(tuple(TABLE.get(ts.SORT_OPEN_ACTION).rect), ts.SORT_OPEN_RECT)
        self.assertEqual(tuple(TABLE.get(ts.SORT_APPLY_ACTION).rect), ts.SORT_APPLY_RECT)
        self.assertEqual({a: tuple(TABLE.get(a).rect) for a in ts.SORT_KEY_ACTIONS}, ts.SORT_KEY_ACTIONS)
        self.assertFalse(any(TABLE.get(a).consuming
                             for a in (ts.SORT_OPEN_ACTION, ts.SORT_APPLY_ACTION, *ts.SORT_KEY_ACTIONS)))

    def test_popup_parsing_binds_lv_to_one_cell(self):
        items = [{"text": "並び替え", "box": [410, 128, 110, 32]}, {"text": "Lv 順", "box": [781, 427, 80, 30]},
                 {"text": "デフォルト", "box": [413, 238, 90, 30]}, {"text": "決定", "box": [610, 525, 60, 34]}]
        popup = ts.parse_sort_popup(items)
        self.assertEqual(ts.sort_key_action(popup), "training-sort-key-r3c3")
        self.assertTrue(ts.apply_ready(popup))
        self.assertIsNone(ts.sort_key_action(ts.parse_sort_popup(items[1:])))          # no title
        self.assertIsNone(ts.sort_key_action(ts.parse_sort_popup(items + [items[1]])))  # duplicated key


class SearchTests(unittest.TestCase):
    def test_found_opens_ambiguous_stops_and_no_scroll_stops(self):
        search = ts.ListSearch(0)
        self.assertEqual(search.observe(LEVEL_DESC, view("found")).kind, "open")
        self.assertEqual(search.observe(LEVEL_DESC, view("ambiguous")).reason, "target_ambiguous")
        decision = ts.ListSearch(0).observe(LEVEL_DESC, view(), can_scroll=True)
        self.assertEqual((decision.kind, decision.reason), ("stop", "target_not_visible"))
        decision = ts.ListSearch(4).observe(LEVEL_DESC, view(), can_scroll=False)
        self.assertIn("scroll_unavailable", decision.notes)

    def test_scrolls_down_to_the_end_then_up_to_the_top_then_stops(self):
        # unknown start offset: down to the end, then up to the start (the older plan)
        search = ts.ListSearch(10)
        steps = [search.observe(LEVEL_DESC, view(digest=d, offset=None), True) for d in ("a", "b", "b", "a", "a")]
        self.assertEqual([(s.kind, s.direction or s.reason) for s in steps],
                         [("scroll", "down"), ("scroll", "down"), ("scroll", "up"), ("scroll", "up"),
                          ("stop", "target_absent")])
        self.assertEqual(ts.STOP_OUTCOMES["target_absent"], "target_not_visible")
        # proven top: one downward pass; mid-list start: up to the proven top first, then down
        search = ts.ListSearch(10)
        steps = [search.observe(LEVEL_DESC, view(digest=d), True) for d in ("a", "b", "b")]
        self.assertEqual([(s.kind, s.direction or s.reason) for s in steps],
                         [("scroll", "down"), ("scroll", "down"), ("stop", "target_absent")])
        search = ts.ListSearch(10)
        steps = [search.observe(LEVEL_DESC, view(digest=d, offset=o), True)
                 for d, o in (("m", 298), ("t", 184), ("u", 184), ("u", 184))]
        self.assertEqual([(s.kind, s.direction) for s in steps],
                         [("scroll", "up"), ("scroll", "down"), ("scroll", "down"), ("stop", None)])
        self.assertEqual(steps[1].notes, ("list_top_reached",))
        self.assertEqual(search.offsets, [298, 184, 184, 184])

    def test_budget_is_bounded(self):
        search = ts.ListSearch(2)
        kinds = [search.observe(LEVEL_DESC, view(digest=str(i)), True).kind for i in range(4)]
        self.assertEqual(kinds, ["scroll", "scroll", "stop", "stop"])
        with self.assertRaises(ValueError):
            ts.ListSearch(ts.MAX_SCROLL_STEPS + 1)

    def test_order_or_content_change_invalidates_the_search(self):
        search = ts.ListSearch(10)
        search.observe(LEVEL_DESC, view(digest="a"), True)
        search.observe(LEVEL_DESC, view(digest="b"), True)
        self.assertEqual(search.steps, 2)
        # The user switches to デフォルト mid-run: everything learnt about the old order is dropped.
        decision = search.observe(ts.ListOrder("default", "desc"), view("found", digest="c"), True)
        self.assertEqual((decision.kind, decision.notes, search.steps, search.invalidations),
                         ("open", ("list_order_changed",), 0, 1))
        # Content changed although the task did not scroll (e.g. a filter toggled and cleared, list reloaded).
        decision = search.observe(ts.ListOrder("default", "desc"), view(digest="z"), True)
        self.assertEqual((decision.notes, search.invalidations), (("list_content_changed",), 2))


class ClickBindingTests(unittest.TestCase):
    def test_only_the_slot_with_a_table_action_is_clickable(self):
        # ANN-47: the 21 full grid slots are enabled, each bound to its own table action (r1c2 keeps the legacy one);
        # 2026-11-22: plus the 35 shifted-grid slots (b: 3 rows, c: 2 rows, 7 columns)
        actions = ts.select_actions(TABLE)
        self.assertEqual(actions[0], "training-select-card")
        self.assertEqual(len(actions), 21 + 35)
        self.assertEqual(ts.click_action((405, 184, 108, 108), TABLE), "training-select-card")
        self.assertEqual(ts.click_action((406, 185, 108, 108), TABLE), "training-select-card")
        self.assertEqual(ts.click_action((819, 338, 108, 108), TABLE), "training-list-slot-r2c5")
        # scrolled half a row: the shifted grid b (+51 px) binds it; between the grids nothing does
        self.assertEqual(ts.click_action((405, 184 + 60, 108, 108), TABLE), "training-list-slot-b-r1c2")
        self.assertIsNone(ts.click_action((405, 184 + 18, 108, 108), TABLE))
        self.assertTrue(ts.scroll_wired(TABLE))      # enabled 2026-10-11 after the user's approval (wheel ∓120)
        self.assertFalse(ts.scroll_wired(BASE))

    def test_config_default_is_no_card_and_scroll_disabled(self):
        config = tt.TrainingConfig.from_params({})
        self.assertEqual((config.selected, config.max_scroll_steps), (False, tt.DEFAULT_SCROLL_STEPS))
        self.assertEqual(tt.DEFAULT_SCROLL_STEPS, 10)
        self.assertEqual(ts.TARGETS[tt.CARD_ID].card_id, tt.CARD_IDS[tt.CARD_ID])
        self.assertEqual(tt.identity_reasons(tt.TrainingConfig(tt.CARD_ID), "【くっつく、くっつかない】芹沢あさひ"), [])
        self.assertEqual(tt.identity_reasons(tt.TrainingConfig(tt.CARD_ID), "【ひだまりの中で】櫻木真乃"),
                         ["identity_mismatch"])
        self.assertEqual(tt.identity_reasons(tt.TrainingConfig(tt.CARD_ID), None), ["card_name_unreadable"])


# ======================================================================== runtime (fake screen)
try:
    from agent.core import training_custom
    HAVE_RUNTIME = True
except Exception:  # pragma: no cover - environment without the maa package
    HAVE_RUNTIME = False


def enabled(table, predicate):
    """Copy of the real table with the reserved actions matching ``predicate`` enabled (offline only)."""
    return dataclasses.replace(table, actions=tuple(dataclasses.replace(a, enabled=True) if predicate(a.id) else a
                                                    for a in table.actions))


# The live table enables the direction toggle since 2026-10-11; tests start from the reserved catalog.
RESERVED = (ts.SORT_ACTION, *ts.SCROLL_ACTIONS.values(),    # enabled live on 2026-10-11; tests choose explicitly
            ts.SORT_OPEN_ACTION, ts.SORT_APPLY_ACTION, *ts.SORT_KEY_ACTIONS)
BASE = dataclasses.replace(TABLE, actions=tuple(dataclasses.replace(a, enabled=False) if a.id in RESERVED else a
                                                 for a in TABLE.actions))
# Same table with the 20 grid-slot actions switched off: a located target then has no action to tap.
NO_SLOTS = dataclasses.replace(BASE, actions=tuple(
    dataclasses.replace(a, enabled=False) if a.id.startswith("training-list-slot-") else a for a in BASE.actions))
SCROLL_TABLE = enabled(BASE, lambda i: i in ts.SCROLL_ACTIONS.values())
GRID_TABLE = enabled(BASE, lambda i: i.startswith("training-list-") and i != ts.SORT_ACTION)  # 20 slots + both wheels
SORT_TABLE = enabled(BASE, lambda i: i == ts.SORT_ACTION)
KEY_TABLE = enabled(BASE, lambda i: i in (ts.SORT_ACTION, ts.SORT_OPEN_ACTION, ts.SORT_APPLY_ACTION)
                    or i in ts.SORT_KEY_ACTIONS)
NO_SORT_TABLE = BASE


def frame(marker):
    import numpy as np

    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[0, 0, 0] = marker
    return image


@unittest.skipUnless(HAVE_RUNTIME, "需要 maa Python 包")
# one fake frame per step: the live settle check (two matching list readings) is covered by SettleTests
@mock.patch.object(tt, "MAX_SETTLE_HOLDS", 0)
class RuntimeTests(unittest.TestCase):
    TEXTS = {1: ("Lv順", "▼降順", "絞り込み"), 2: ("デフォルト", "▼降順", "絞り込み"), 3: (None, None, "絞り込み"),
             4: ("Lv順", "▼降順", "絞り込みON"), 5: ("Lv順", "▲昇順", "絞り込み")}
    NAMES = {10: "【くっつく、くっつかない】芹沢あさひ", 11: "【ひだまりの中で】櫻木真乃", 12: None}

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_tsel_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        test = self

        class Vision:
            def __init__(self, context):
                pass

            def text(self, image, roi, threshold=None):
                marker = int(image[0, 0, 0])
                if list(roi) == tt.CARD_NAME_BOX:
                    return test.NAMES.get(marker)
                sort, direction, flt = test.TEXTS[marker]
                return {tuple(ts.SORT_BOX): sort, tuple(ts.DIRECTION_BOX): direction,
                        tuple(ts.FILTER_BOX): flt}[tuple(roi)]

            def number(self, image, crop, kind, pattern, cutoff=128, unanimous=False):
                return "2"

        self.views = {}
        self.locates = []

        def fake_locate(pil, spec, index, reference):
            marker = pil.getpixel((0, 0))[2]  # BGR -> RGB: the marker channel ends up blue
            self.locates.append(marker)
            return self.views[marker]

        patcher = mock.patch.object(ts, "locate", fake_locate)
        patcher.start()
        self.addCleanup(patcher.stop)
        now = dt.datetime(2026, 10, 7, 3, 0, tzinfo=dt.timezone.utc)
        self.runtime = training_custom.TrainingRuntime(log_root=self.root, now=lambda: now, vision=Vision,
                                                       card_index=object(), reference_loader=lambda spec: "ref",
                                                       table=SCROLL_TABLE)

    def start(self, **config):
        self.runtime.run = tt.TrainingRun(tt.TrainingConfig(tt.CARD_ID, **config), self.runtime.log)
        return self.runtime.run

    def step(self, marker, **p):
        if "node" not in p and p.get("want") == "open":
            p["node"] = ts.SELECT_NODES[p["action"]]
        elif "node" not in p and p.get("want") == "scroll":
            p["node"] = ts.SCROLL_NODES[p["direction"]]
        elif "node" not in p and p.get("want") == "sort":
            p["node"] = ts.SORT_NODE
        return self.runtime.list_step(None, frame(marker), p)

    def test_sort_change_mid_run_invalidates_and_relocates_by_identity(self):
        run = self.start(max_scroll_steps=4)
        self.views = {1: view("absent", "a"), 2: view("found", "b")}
        self.assertFalse(self.step(1, want="open", action="training-select-card"))
        self.assertTrue(self.step(1, want="scroll", direction="down"))      # same frame, evaluated once
        self.assertEqual(self.locates, [1])
        self.assertTrue(self.step(2, want="open", action="training-select-card"))
        self.assertEqual((run.list_search.invalidations, run.list_search.steps, run.finished), (1, 0, False))
        self.assertIn("list_order_changed", run.decisions[-1]["decision"]["notes"])

    # -- 2026-10-10: Maa sets the list direction itself (one toggle, verified on the next frame)
    def test_hidden_low_level_target_toggles_to_ascending_once_then_opens(self):
        self.runtime._table, self.runtime._levels = SORT_TABLE, {}
        run = self.start()
        self.views = {1: view("absent", "a"), 5: view("found", "b")}
        self.assertFalse(self.step(1, want="open", action="training-select-card"))
        self.assertTrue(self.step(1, want="sort"))
        self.assertTrue(self.step(1, want="sort_sent"))      # SC.Training.List.Sorted, after the toggle's click
        self.assertEqual((run.sort_toggles, run.sort_expected), (1, "asc"))
        self.assertTrue(self.step(5, want="open", action="training-select-card"))
        self.assertFalse(run.finished)
        self.assertEqual(run.decisions[-2]["sort_plan"]["want"], "asc")

    def test_attribute_key_not_wired_keeps_the_lv_direction_plan(self):
        # 2026-10-14 (picker_sort.training_key): the stats chose Visual順 but the key cells are not enabled -> the
        # list is Lv順 already, so the direction toggle is still planned (no regression)
        self.runtime._table, self.runtime._levels = SORT_TABLE, {}
        run = self.start()
        run.sort_key_want = "Visual順"
        self.views = {1: view("absent", "a")}
        self.assertTrue(self.step(1, want="sort"))
        self.assertEqual(run.sort_expected, "asc")
        self.assertIn({"order": {"key": "level", "direction": "desc"}, "want_key": "visual", "why": None,
                       "action": "unavailable"}, [d.get("sort_plan") for d in run.decisions])

    def test_toggle_without_effect_stops_and_never_toggles_twice(self):
        self.runtime._table, self.runtime._levels = SORT_TABLE, {}
        run = self.start()
        self.views = {1: view("absent", "a")}
        self.assertTrue(self.step(1, want="sort"))
        self.assertTrue(self.step(1, want="sort_sent"))
        self.assertFalse(self.step(1, want="sort"))
        self.assertEqual(run.outcome, "sort_toggle_unverified")

    def test_known_levels_choose_the_nearer_end_and_never_change_the_sort_key(self):
        self.runtime._table = SORT_TABLE
        self.runtime._levels = {tt.CARD_IDS[tt.CARD_ID]: 80, **{f"20400100{i:02d}": 1 for i in range(30)}}
        run = self.start()
        self.views = {1: view("absent", "a"), 2: view("absent", "c")}
        self.assertFalse(self.step(1, want="sort"))           # Lv80 under ▼降順 is already the near end
        self.assertEqual(run.outcome, "target_not_visible")
        self.runtime._levels = {}
        run = self.start()
        self.assertFalse(self.step(2, want="sort"))           # デフォルト: the key is never changed
        self.assertEqual(run.decisions[0]["sort_plan"], {"order": {"key": "default", "direction": "desc"},
                                                         "want_key": "level", "action": "unavailable"})

    def test_unwired_toggle_reports_the_needed_order(self):
        self.runtime._table, self.runtime._levels = NO_SORT_TABLE, {}
        run = self.start(max_scroll_steps=4)
        self.views = {1: view("absent")}
        self.assertFalse(self.step(1, want="sort"))
        self.assertEqual(run.outcome, "target_not_visible")
        self.assertIn("sort_change_needed:level/asc", run.notes)

    # -- 2026-10-11: sort KEY デフォルト -> Lv順 through the 並び替え popup (reserved actions, OCR-bound cell)
    def popup_context(self, keys=None, apply=True, title=True):
        from types import SimpleNamespace as NS
        cells = keys if keys is not None else {"デフォルト": (373, 220), "Lv順": (741, 409), "特訓回数順": (557, 315)}
        results = [NS(text=t, box=(x + 40, y + 18, 80, 30), score=0.9) for t, (x, y) in cells.items()]
        if title:
            results.append(NS(text="並び替え", box=(410, 128, 110, 32), score=0.9))
        if apply:
            results.append(NS(text="決定", box=(610, 525, 60, 34), score=0.9))
        return NS(run_recognition=lambda node, image: NS(hit=True, all_results=results))

    def popup_frame(self, selected=None):
        image = frame(9)
        if selected:
            x, y, w, h = ts.SORT_KEY_ACTIONS[selected]
            image[y + 5:y + 15, x + 5:x + 60] = (170, 90, 240)       # BGR of pink (240, 90, 170)
        return image

    def popup(self, want, context, image, **p):
        node = {"sort_apply": ts.SORT_APPLY_NODE}.get(want, ts.SORT_KEY_NODES.get(p.get("action")))
        return self.runtime.list_step(context, image, {"want": want, "node": node, **p})

    def test_default_key_opens_the_popup_picks_lv_and_applies_once(self):
        self.runtime._table, self.runtime._levels = KEY_TABLE, {}
        run = self.start()
        self.views = {2: view("absent", "a"), 1: view("absent", "c"), 5: view("found", "b")}
        self.assertTrue(self.step(2, want="sort_open", node=ts.SORT_OPEN_NODE))
        self.assertEqual(run.sort_key_phase, "open_requested")
        ctx = self.popup_context()
        self.assertFalse(self.popup("sort_pick", ctx, self.popup_frame(), action="training-sort-key-r1c1"))
        self.assertTrue(self.popup("sort_pick", ctx, self.popup_frame(), action="training-sort-key-r3c3"))
        self.assertFalse(self.popup("sort_apply", ctx, self.popup_frame()))              # Lv順 not yet selected
        self.assertTrue(self.popup("sort_apply", ctx, self.popup_frame("training-sort-key-r3c3")))
        self.assertFalse(self.popup("sort_pick", ctx, self.popup_frame(), action="training-sort-key-r3c3"))
        self.assertTrue(self.step(1, want="sort"))                 # Lv順/▼降順 now read -> direction toggle
        self.assertTrue(self.step(5, want="open", action="training-select-card"))
        self.assertFalse(run.finished)

    def test_key_not_applied_or_unexpected_popup_stops(self):
        self.runtime._table, self.runtime._levels = KEY_TABLE, {}
        run = self.start()
        self.views = {2: view("absent", "a")}
        self.assertTrue(self.step(2, want="sort_open", node=ts.SORT_OPEN_NODE))
        ctx = self.popup_context()
        self.assertTrue(self.popup("sort_pick", ctx, self.popup_frame(), action="training-sort-key-r3c3"))
        self.assertTrue(self.popup("sort_apply", ctx, self.popup_frame("training-sort-key-r3c3")))
        self.assertFalse(self.step(2, want="sort_open", node=ts.SORT_OPEN_NODE))   # still デフォルト after 決定
        self.assertEqual(run.outcome, "sort_key_unverified")
        run = self.start()
        self.assertTrue(self.runtime.list_step(self.popup_context(), self.popup_frame(), {"want": "sort_popup_unexpected"}))
        self.assertEqual(run.outcome, "sort_popup_unexpected")

    def test_popup_without_a_bound_lv_key_closes_unchanged_and_stops(self):
        self.runtime._table, self.runtime._levels = KEY_TABLE, {}
        run = self.start()
        self.views = {2: view("absent", "a")}
        self.assertTrue(self.step(2, want="sort_open", node=ts.SORT_OPEN_NODE))
        ctx = self.popup_context(keys={"デフォルト": (373, 220), "Lv順": (900, 600)})     # outside every cell
        self.assertFalse(self.popup("sort_pick", ctx, self.popup_frame(), action="training-sort-key-r3c3"))
        self.assertEqual(run.outcome, "sort_key_unavailable")
        self.assertTrue(self.popup("sort_apply", ctx, self.popup_frame()))        # 決定 with nothing tapped
        self.assertEqual(run.sort_key_phase, "closed")

    def test_unwired_key_change_reports_it(self):
        self.runtime._table, self.runtime._levels = BASE, {}
        run = self.start()
        self.views = {2: view("absent")}
        self.assertFalse(self.step(2, want="sort_open", node=ts.SORT_OPEN_NODE))
        self.assertEqual(run.outcome, "target_not_visible")
        self.assertIn("sort_change_needed:level", run.notes)

    def test_without_reviewed_scroll_actions_a_hidden_target_stops(self):
        self.runtime._table = BASE
        run = self.start(max_scroll_steps=4)
        self.views = {1: view("absent")}
        self.assertFalse(self.step(1, want="open", action="training-select-card"))
        self.assertTrue(self.step(1, want="hold"))   # concluded on this frame: no other list node, Leave next round
        self.assertEqual(run.outcome, "target_not_visible")
        self.assertIn("scroll_unavailable", run.notes)

    def test_target_in_a_slot_without_an_action_is_never_clicked_elsewhere(self):
        # A slot whose action is disabled in the table is never clicked through another slot's action.
        self.runtime._table = NO_SLOTS
        run = self.start()
        self.views = {1: view("found", slot="r2c5", box=(819, 338, 108, 108))}
        self.assertFalse(self.step(1, want="open", action="training-select-card"))
        self.assertEqual((run.outcome, run.notes[0]), ("target_slot_unbound", "slot=r2c5"))

    def test_unreadable_order_holds_then_stops_and_filter_stops(self):
        run = self.start()
        holds = []
        for i in range(tt.MAX_UNREADABLE_FRAMES + 1):
            image = frame(3)
            image[1, 1, 1] = i  # a new frame each time
            holds.append(self.runtime.list_step(None, image, {"want": "open", "action": "training-select-card",
                                                              "node": ts.LEGACY_SELECT_NODE}))
            holds.append(self.runtime.list_step(None, image, {"want": "hold"}))
        self.assertEqual(holds[1::2], [True] * (tt.MAX_UNREADABLE_FRAMES + 1))
        self.assertEqual(run.outcome, "list_sort_unknown")
        self.assertEqual(self.locates, [])
        run = self.start()
        self.assertFalse(self.step(4, want="open", action="training-select-card"))
        self.assertEqual(run.outcome, "list_filter_active")

    def test_identity_recheck_on_detail_and_confirm(self):
        run = self.start()
        self.assertFalse(self.runtime.check_identity(None, frame(10)))           # the configured card: go on
        self.assertTrue(self.runtime.check_identity(None, frame(12)))            # unreadable: another frame
        self.assertFalse(run.finished)
        self.assertTrue(self.runtime.check_identity(None, frame(11)))
        self.assertEqual((run.outcome, run.notes), ("identity_mismatch", ["identity_mismatch"]))
        self.assertFalse(self.runtime.plan(None, frame(11), {"step": "plus"}))
        # Confirm dialog: a different card behind the dialog -> rejected, the existing Cancel path, no spend.
        run = self.start()
        run.phase, run.baseline = "submitted", {"inventory": 10, "level": 2, "remaining_exp": 40}
        self.assertFalse(self.runtime.check_confirm(None, frame(11)))
        self.assertIn("identity_mismatch", run.confirm_rejected)
        self.assertIsNone(run.intent)

    def test_no_card_selected_is_the_default_mode_not_a_refusal(self):
        # 2026-10-10: no chosen card = first trainable card of the current sort (tests/test_training_any_card.py)
        self.runtime.run = run = tt.TrainingRun(tt.TrainingConfig(), self.runtime.log)
        self.runtime.started(run)
        self.assertEqual((run.finished, run.config.default_mode, run.config.selected), (False, True, False))


# ======================================================================== real frames: identity location
def location_ready():
    return (F.present() and (ROOT / "ui/game-assets/manifest.json").is_file()
            and (PRIVATE / "resource_native/image" / ts.TARGETS[tt.CARD_ID].template).is_file())


@unittest.skipUnless(location_ready(), "需要私有训练列表帧、ui/game-assets 与私有模板包")
class LocateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agent.core import card_inventory as ci

        F.build()
        cls.index = ci.CardIndex.from_assets(kind="S")
        cls.spec = ts.TARGETS[tt.CARD_ID]
        cls.reference = ts.load_reference(cls.spec)

    def locate(self, name):
        from PIL import Image

        return ts.locate(Image.open(ROOT / name).convert("RGB"), self.spec, self.index, self.reference)

    def test_native_default_list_and_the_live_lv_desc_list(self):
        found = self.locate(F.LIST_DEFAULT)
        self.assertEqual((found.status, found.target.slot), ("found", "r1c2"))
        self.assertEqual(ts.click_action(found.target.box, TABLE), "training-select-card")
        absent = self.locate(F.LIST_LV_DESC)   # the 2026-10-07 live frame: Lv80 cards only, no look-alike
        self.assertEqual((absent.status, absent.lookalikes), ("absent", ()))

    def test_target_anywhere_in_the_grid(self):
        for name, slot, action in ((F.LV_DESC_R1C2, "r1c2", "training-select-card"), (F.LV_DESC_R2C5, "r2c5", "training-list-slot-r2c5"),
                                   (F.LV_DESC_R3C7, "r3c7", "training-list-slot-r3c7"),
                                   (F.LV_ASC_R1C1, "r1c1", "training-list-slot-r1c1")):
            with self.subTest(name=name):
                found = self.locate(name)
                self.assertEqual((found.status, found.target.slot), ("found", slot))
                self.assertEqual(found.target.tier, "rainbow")
                self.assertEqual(ts.click_action(found.target.box, TABLE), action)

    def test_same_art_in_another_rarity_frame_is_rejected(self):
        look = self.locate(F.LOOKALIKE_GOLD)
        self.assertEqual((look.status, look.targets), ("ambiguous", ()))
        self.assertEqual(look.lookalikes[0].slot, "r1c2")
        self.assertIn("frame:gold", look.lookalikes[0].reasons)
        self.assertEqual(ts.ListSearch(4).observe(LEVEL_DESC, look, True).reason, "target_ambiguous")

    def test_reserved_grid_geometry_binds_native_tiles_only_when_enabled(self):
        table = dataclasses.replace(TABLE, actions=tuple(dataclasses.replace(action, enabled=True) for action in TABLE.actions))
        for name, slot in ((F.LV_ASC_R1C1, "r1c1"), (F.LV_DESC_R2C5, "r2c5"), (F.LV_DESC_R3C7, "r3c7")):
            with self.subTest(slot=slot):
                found = self.locate(name)
                self.assertEqual(found.status, "found")
                self.assertEqual(ts.click_action(found.target.box, table), "training-list-slot-" + slot)
                expected = "training-list-slot-" + slot
                self.assertEqual(ts.click_action(found.target.box, TABLE), expected if TABLE.get(expected).enabled else None)

    def test_all_full_grid_slots_bind_the_fresh_target_to_their_own_action(self):
        from PIL import Image
        base = Image.open(ROOT / F.LIST_LV_DESC).convert("RGB")
        tile = Image.open(ROOT / F.LIST_DEFAULT).convert("RGB").crop(F.TILE)
        table = dataclasses.replace(TABLE, actions=tuple(dataclasses.replace(a, enabled=True) for a in TABLE.actions))
        for row, col in ts.SLOTS:
            with self.subTest(slot=(row, col)):
                frame = base.copy()
                frame.paste(tile, F.slot_xy(row - 1, col - 1))
                found = ts.locate(frame, self.spec, self.index, self.reference)
                action = "training-select-card" if (row, col) == (1, 2) else f"training-list-slot-r{row}c{col}"
                self.assertEqual((found.status, found.target.slot), ("found", f"r{row}c{col}"))
                self.assertEqual(ts.click_action(found.target.box, table), action)
                self.assertIsNone(ts.binding_refusal(table, ts.SELECT_NODES[action], action))
                other = "training-list-slot-r1c1" if action != "training-list-slot-r1c1" else "training-select-card"
                self.assertIsNotNone(ts.binding_refusal(table, ts.SELECT_NODES[other], action))

    def shifted(self, name, d=40):
        """The native list scrolled by ``d`` px (2026-10-11 live: the game reopened the list at offset 298)."""
        import numpy as np
        from PIL import Image

        image = np.asarray(Image.open(ROOT / name).convert("RGB")).copy()
        image[172:690 - d] = image[172 + d:690].copy()
        return Image.fromarray(image)

    def test_list_not_at_top_seeks_the_top_first_then_finds_the_target(self):
        from PIL import Image

        mid = self.shifted(F.LIST_DEFAULT)
        view_mid = ts.locate(mid, self.spec, self.index, self.reference)
        self.assertEqual((view_mid.status, view_mid.offset), ("absent", 298))       # あさひ cut at the top edge
        search = ts.ListSearch(6)
        first = search.observe(ts.ListOrder("default", "desc"), view_mid, True)
        self.assertEqual((first.kind, first.direction, first.reason, search.start), ("scroll", "up", "seek_list_top", "mid"))
        top = self.locate(F.LIST_DEFAULT)
        found = search.observe(ts.ListOrder("default", "desc"), top, True)
        self.assertEqual((found.kind, found.slot, found.notes), ("open", "r1c2", ("list_top_reached",)))
        # Default mode (no card chosen): the not-at-top first tile is not a stop any more, only the top decides.
        default = ts.ListSearch(6, top_only=True)
        dmid = ts.locate_default(mid, self.index)
        self.assertEqual(dmid.reasons, ("list_not_at_top:298",))
        self.assertEqual(default.observe(LEVEL_DESC, dmid, True).direction, "up")
        dtop = ts.locate_default(Image.open(ROOT / F.LIST_DEFAULT).convert("RGB"), self.index)
        self.assertEqual(default.observe(LEVEL_DESC, dtop, True).kind, "open")
        # A top that never shows (wheel without effect) stops; it is never taken for the top.
        stuck = ts.ListSearch(6)
        stuck.observe(LEVEL_DESC, view_mid, True)
        self.assertEqual(stuck.observe(LEVEL_DESC, view_mid, True).notes, ("list_top_unverified:298",))

    def test_default_mode_follows_the_game_dimming_on_native_lists(self):
        """2026-10-12: untrainable cards are dimmed in every sort; the first bright tile is the default target."""
        from PIL import Image

        cases = [(F.LIST_DEFAULT, "found", "r1c1", ()),                              # デフォルト▼: r1c1 bright
                 (F.LIST_LV_DESC, "absent", None, tuple(f"dimmed:r{r}c{c}" for r, c in ts.SLOTS))]  # Lv順▼: all max
        vocal = ROOT / VOCAL_LIST
        if vocal.is_file():          # Vocal順▼ (2026-10-12 live stop): r1c1/r1c2 dimmed, r1c3 bright (Vocal 313)
            cases.append((VOCAL_LIST, "found", "r1c3", ("dimmed:r1c1", "dimmed:r1c2")))
        for name, status, slot, skipped in cases:
            with self.subTest(name=name):
                view = ts.locate_default(Image.open(ROOT / name).convert("RGB"), self.index)
                self.assertEqual((view.status, view.offset), (status, 184), view.reasons)
                self.assertEqual(view.reasons[:len(skipped)], skipped)
                if slot:
                    self.assertEqual(view.target.slot, slot)
                    self.assertEqual(ts.ListSearch(4).observe(ts.ListOrder("vocal", "desc"), view, True).kind, "open")

    def test_detail_s_badge_on_the_native_zero_page(self):
        import numpy as np
        from PIL import Image

        self.assertTrue(ts.detail_s_badge(np.asarray(Image.open(ROOT / F.ZERO).convert("RGB"))))
        self.assertFalse(ts.detail_s_badge(np.asarray(Image.open(ROOT / F.LIST_DEFAULT).convert("RGB"))))

    def test_scroll_search_over_real_pages(self):
        search = ts.ListSearch(6)
        page1, page2 = self.locate(F.LIST_LV_DESC), self.locate(F.LV_DESC_R3C7)
        self.assertEqual(search.observe(LEVEL_DESC, page1, True).direction, "down")
        decision = search.observe(LEVEL_DESC, page2, True)
        self.assertEqual((decision.kind, decision.slot), ("open", "r3c7"))
        # From the proven top, the end of the list (no movement after a wheel step) exhausts the search at once.
        search = ts.ListSearch(6)
        kinds = [search.observe(LEVEL_DESC, page1, True) for _ in range(2)]
        self.assertEqual([(d.kind, d.direction or d.reason) for d in kinds],
                         [("scroll", "down"), ("stop", "target_absent")])


# ======================================================================== whole-task replays (MaaFramework)
try:
    from tests import test_daily_b5_tasks as b5
    from tools import run_tasks as launcher
    HAVE_REPLAY = b5.HAVE_MAA and b5.frames_present()
except Exception:  # pragma: no cover
    HAVE_REPLAY = False

SPEND = ["training-start"]
TO_LIST = ["home-idol", "idol-training"]


@unittest.skipUnless(HAVE_REPLAY and location_ready(), "需要 MaaFramework、OCR 模型、私有模板包与训练帧")
class ListReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not b5.private_ready():
            raise unittest.SkipTest("私有回放数据不完整")
        b5.build_synthetic_frames()
        F.build()

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_tsel_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_training(self, frames, spend=(), card=tt.CARD_IDS[tt.CARD_ID]):
        option = {"训练卡": card} if card is not None else {}
        config = {"preset": None, "tasks": [{"name": "Training", "option": option}]}
        if spend:
            config["allow_spend"] = list(spend)
        options = {"training": {"log_root": self.root, "now": lambda: b5.NOW, "sample_root": self.root / "samples"}}
        summary = launcher.run(config, offline_steps=[(ROOT / f, "click") for f in frames], simulate_input=True,
                               policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                               task_options=options)
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "training"]
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(b5.TABLE.get(action).contains(point), (point, action))
        return task, report

    def events(self):
        path = self.root / "training-2026-10-02.jsonl"
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    def list_records(self, report):
        return [d for d in report["decisions"] if d.get("stage") == "List"]

    def test_lv_desc_target_in_slot_2_trains_by_identity(self):
        frames = [b5.HOME, b5.IDOL, F.LV_DESC_R1C2, b5.ZERO, b5.ONE, b5.CONFIRM, b5.RESULT, b5.AFTER, F.LV_DESC_R1C2,
                  b5.IDOL, b5.HOME]
        task, report = self.run_training(frames, SPEND)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "trained"), report)
        self.assertEqual(task["simulated_actions"], b5.TRAINING_ACTIONS)
        self.assertEqual(task["simulated_spends"], [["training-start", "sent"]])
        record = self.list_records(report)[0]
        self.assertEqual((record["order"], record["view"]["targets"][0]["slot"], record["action"]),
                         ({"key": "level", "direction": "desc"}, "r1c2", "training-select-card"))

    def test_task_started_on_the_training_list_continues_there(self):
        # 2026-10-11 user: the game was left on トレーニングアイドル選択; the task recognises its own list and goes on.
        frames = [F.LV_DESC_R1C2, b5.ZERO, b5.ONE, b5.CONFIRM, b5.RESULT, b5.AFTER, F.LV_DESC_R1C2, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames, SPEND)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "trained"), report)
        self.assertEqual(task["simulated_actions"][0], "training-select-card")

    def test_lv_asc_target_in_slot_2_opens_without_spend_permission(self):
        frames = [b5.HOME, b5.IDOL, F.LV_ASC_R1C2, b5.ZERO, F.LV_ASC_R1C2, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames)
        self.assertEqual(report["outcome"], "spend_not_enabled")
        self.assertEqual(task["simulated_actions"], TO_LIST + ["training-select-card", "nav-back", "nav-back", "nav-back"])
        self.assertEqual(self.list_records(report)[0]["order"], {"key": "level", "direction": "asc"})

    def test_hidden_target_under_lv_desc_toggles_to_asc_and_opens(self):
        # 2026-10-11: the toggle is enabled in the live table; the target hidden under ▼降順 is found under ▲昇順
        frames = [b5.HOME, b5.IDOL, F.LIST_LV_DESC, F.LV_ASC_R1C2, b5.ZERO, F.LV_ASC_R1C2, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames)
        self.assertEqual(report["outcome"], "spend_not_enabled", report)
        self.assertEqual(task["simulated_actions"], TO_LIST + ["training-list-direction-toggle", "training-select-card",
                                                               "nav-back", "nav-back", "nav-back"])

    def test_toggle_without_effect_stops_without_a_second_input(self):
        frames = [b5.HOME, b5.IDOL, F.LIST_LV_DESC, F.LIST_LV_DESC, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames, SPEND)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "sort_toggle_unverified"), report)
        self.assertEqual(task["simulated_actions"], TO_LIST + ["training-list-direction-toggle", "nav-back", "nav-back"])

    def test_stops_without_tapping_the_list(self):
        cases = ((F.SORT_UNKNOWN, "list_sort_unknown"),
                 (F.FILTER_ON, "list_filter_active"), (F.LOOKALIKE_GOLD, "target_ambiguous"))
        for name, outcome in cases:
            with self.subTest(frame=name):
                shutil.rmtree(self.root, ignore_errors=True)
                task, report = self.run_training([b5.HOME, b5.IDOL, name, b5.IDOL, b5.HOME], SPEND)
                self.assertEqual((task["status"], report["outcome"]), ("failed", outcome), report)
                self.assertEqual(task["simulated_actions"], TO_LIST + ["nav-back", "nav-back"])
                self.assertEqual(task["simulated_spends"], [])
                self.assertEqual(self.events(), [])

    def test_an_unverified_slot_tap_stops_after_exactly_one_click(self):
        """ANN-47: the target slot has its own action; the tap is one-shot and, when the detail never opens, final."""
        for name, action in ((F.LV_DESC_R2C5, "training-list-slot-r2c5"), (F.LV_ASC_R1C1, "training-list-slot-r1c1")):
            with self.subTest(frame=name):
                shutil.rmtree(self.root, ignore_errors=True)
                task, report = self.run_training([b5.HOME, b5.IDOL, name, b5.IDOL, b5.HOME], SPEND)
                self.assertEqual((task["status"], report["outcome"]), ("failed", "list_input_unverified"), report)
                self.assertEqual(report["notes"], ["tile_tap_unverified"])
                acts = task["simulated_actions"]      # one tap, then at most read-only back navigation
                self.assertEqual(acts[:len(TO_LIST) + 1], TO_LIST + [action])
                self.assertTrue(all(a == "nav-back" for a in acts[len(TO_LIST) + 1:]), acts)
                self.assertEqual((task["simulated_spends"], self.events()), ([], []))

    def test_a_different_card_opened_is_left_without_spending(self):
        """The sort changed between the list frame and the tap: another card's detail opens."""
        frames = [b5.HOME, b5.IDOL, F.LIST_DEFAULT, F.WRONG_ZERO, F.LIST_DEFAULT, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames, SPEND)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "identity_mismatch"), report)
        self.assertEqual(task["simulated_actions"], TO_LIST + ["training-select-card", "nav-back", "nav-back", "nav-back"])
        self.assertEqual((task["simulated_spends"], self.events()), ([], []))
        self.assertIn("SC.Training.Detail.Identity", task["node_trace"])

    def test_default_option_is_default_mode_and_never_taps_an_unverified_first_card(self):
        # Default mode on the real default-order list: r1c1 is not the calibrated card, so it is located (or stopped as
        # unidentified); a located slot is tapped once at most (stops as unverified here); nothing is spent.
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LIST_DEFAULT, b5.IDOL, b5.HOME], SPEND, card=None)
        self.assertEqual(task["status"], "failed", report)
        self.assertIn(report["outcome"], ("target_uncalibrated", "target_ambiguous", "target_slot_unbound",
                                          "list_input_unverified"), report)
        self.assertEqual((report["config"]["card"], report["config"]["card_id"]), ("", ""))
        taps = [a for a in task["simulated_actions"] if a.startswith("training-")]
        self.assertLessEqual(len(taps) - len(TO_LIST), 1, task["simulated_actions"])
        self.assertEqual((task["simulated_spends"], self.events()), ([], []))


if __name__ == "__main__":
    unittest.main()


LIVE = "dev_assets/private/sample-live-20261011/"
SORT_LIVE = {"join": LIVE + "14-training-list-settled.png", "options": LIVE + "15-training-sort-options.png",
             "join_selected": LIVE + "16-training-sort-join-selected.png",
             "lv_selected": LIVE + "17-training-sort-lv-selected.png",
             "lv_desc": LIVE + "19-training-lv-list.png", "lv_asc": LIVE + "20-training-lv-ascending.png"}


def _live_ready():
    try:
        from tests.maa_probe import maa_ready
    except Exception:
        return False
    return maa_ready(*SORT_LIVE.values())


@unittest.skipUnless(HAVE_RUNTIME and _live_ready(), "需要 Maa OCR 与 2026-10-11 训练排序原生帧")
class SettleTests(unittest.TestCase):
    """2026-10-12 live: the list was read while still loading/scrolling. A reading must repeat on a later frame first."""
    TEXTS, NAMES = RuntimeTests.TEXTS, RuntimeTests.NAMES
    setUp, start = RuntimeTests.setUp, RuntimeTests.start

    def step(self, image, **p):
        p.setdefault("node", ts.SELECT_NODES.get(p.get("action")) if p.get("want") == "open" else None)
        return self.runtime.list_step(None, image, p)

    @staticmethod
    def capture(marker, n):
        image = frame(marker)
        image[5, 5, 1] = n          # another capture of the same screen (live: animation pixels differ)
        return image

    def test_a_reading_must_repeat_on_a_later_frame_before_a_tap(self):
        self.start()
        self.views = {1: view("found", "b")}
        first = self.capture(1, 1)
        self.assertFalse(self.step(first, want="open", action="training-select-card"))
        self.assertTrue(self.step(first, want="hold"))                    # same capture: still holding
        self.assertTrue(self.step(self.capture(1, 2), want="open", action="training-select-card"))

    def test_a_changing_reading_keeps_holding_then_proceeds_after_the_cap(self):
        run = self.start()
        for n in range(tt.MAX_SETTLE_HOLDS):
            self.views = {1: view("found", f"moving{n}", offset=184 + n)}
            self.assertFalse(self.step(self.capture(1, n), want="open", action="training-select-card"))
        self.views = {1: view("found", "settled")}
        self.assertTrue(self.step(self.capture(1, 99), want="open", action="training-select-card"))
        self.assertFalse(run.finished)


# one fake frame per step: the live settle check (two matching list readings) is covered by SettleTests
@unittest.skipUnless(HAVE_RUNTIME and _live_ready(), "需要 Maa OCR 与 2026-10-11 训练排序原生帧")
@mock.patch.object(tt, "MAX_SETTLE_HOLDS", 0)
class NativeSortPopupTests(unittest.TestCase):
    """加入順 -> Lv順 on the native 並び替え popup (2026-10-11 supervised samples), list header before/after."""

    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import ocr_items, rgb
        cls.items = {k: ocr_items(v) for k, v in SORT_LIVE.items()}
        cls.rgb = {k: rgb(v) for k, v in SORT_LIVE.items()}

    def order(self, key):
        def text(box):
            hits = [i["text"] for i in self.items[key] if ts._within(i["box"], box)]
            return hits[0] if len(hits) == 1 else None
        return ts.parse_order(text(ts.SORT_BOX), text(ts.DIRECTION_BOX), text(ts.FILTER_BOX))

    def test_list_headers_read_join_then_level_both_directions(self):
        self.assertEqual(self.order("join"), (ts.ListOrder("joined", "desc"), []))
        self.assertEqual(self.order("lv_desc"), (LEVEL_DESC, []))
        self.assertEqual(self.order("lv_asc"), (ts.ListOrder("level", "asc"), []))
        sort_button = [i["box"] for i in self.items["join"] if i["text"] == "加入順"][0]
        self.assertTrue(ts._within(sort_button, ts.SORT_OPEN_RECT))

    def test_popup_binds_lv_to_r1c2_and_shows_the_selection(self):
        for key in ("options", "join_selected", "lv_selected"):
            popup = ts.parse_sort_popup(self.items[key])
            self.assertTrue(popup.title, key)
            self.assertEqual(ts.sort_key_action(popup), "training-sort-key-r1c2", key)
            self.assertTrue(ts.apply_ready(popup), key)
            self.assertEqual(len(popup.keys), 9, popup.to_dict())
        join, lv = self.rgb["join_selected"], self.rgb["lv_selected"]
        self.assertGreater(ts.cell_selected(join, "training-sort-key-r3c2"), ts.SORT_SELECTED_PINK_MIN)
        self.assertLess(ts.cell_selected(join, "training-sort-key-r1c2"), ts.SORT_SELECTED_PINK_MIN)
        self.assertGreater(ts.cell_selected(lv, "training-sort-key-r1c2"), ts.SORT_SELECTED_PINK_MIN)
        others = [a for a in ts.SORT_KEY_ACTIONS if a != "training-sort-key-r1c2"]
        self.assertTrue(all(ts.cell_selected(lv, a) < ts.SORT_SELECTED_PINK_MIN for a in others))

    def test_runtime_picks_lv_on_the_native_popup_and_applies_once(self):
        from types import SimpleNamespace as NS
        harness = RuntimeTests("test_sort_change_mid_run_invalidates_and_relocates_by_identity")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.TEXTS = {**RuntimeTests.TEXTS, 6: ("加入順", "▼降順", "絞り込み")}
        harness.runtime._table, harness.runtime._levels = TABLE, {}
        run = harness.start()
        harness.views = {6: view("absent", "a"), 1: view("absent", "c")}
        self.assertTrue(harness.step(6, want="sort_open", node=ts.SORT_OPEN_NODE))

        def ctx(key):
            results = [NS(text=i["text"], box=tuple(i["box"]), score=i["score"]) for i in self.items[key]]
            return NS(run_recognition=lambda node, image: NS(hit=True, all_results=results))

        def popup(want, key, **p):
            node = {"sort_apply": ts.SORT_APPLY_NODE}.get(want, ts.SORT_KEY_NODES.get(p.get("action")))
            bgr = self.rgb[key][:, :, ::-1].copy()
            return harness.runtime.list_step(ctx(key), bgr, {"want": want, "node": node, **p})

        self.assertFalse(popup("sort_pick", "join_selected", action="training-sort-key-r3c2"))
        self.assertTrue(popup("sort_pick", "join_selected", action="training-sort-key-r1c2"))
        self.assertFalse(popup("sort_apply", "join_selected"))       # 加入順 still drawn selected
        self.assertTrue(popup("sort_apply", "lv_selected"))
        self.assertEqual(run.sort_key_phase, "applied")
        self.assertTrue(harness.step(1, want="sort"))                 # Lv順/▼降順 read -> direction toggle once
        self.assertFalse(run.finished)
