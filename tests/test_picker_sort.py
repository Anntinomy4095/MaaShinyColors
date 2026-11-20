"""Picker sort (agent/core/picker_sort.py, 2026-10-14): attribute planner, header / popup reading, the team-edit
navigator steps (RunOptions.picker_sort) and the training key choice; pixel checks replayed on the supervised
recording captures-20261011-164549Z (#113–#162, the user changing the サポートアイドル編成 sort)."""
from __future__ import annotations

import unittest
import unittest.mock as um
from pathlib import Path

from agent.core import picker_sort as ps
from agent.core import wing_nav as nav
from agent.core import wing_team_edit as wte

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "dev_assets/private/captures-20261011-164549Z"
VO1, VO2, VI1, DA1 = "2040110010", "2040120010", "2040130010", "2040140010"
ATTRS = {VO1: "Vo", VO2: "Vo", VI1: "Vi", DA1: "Da"}
ALL = frozenset(wte.PICKER_INTENTS) | frozenset(wte.SORT_POPUP_INTENTS)


def header(key="デフォルト", direction="desc"):
    return ps.Header(key, direction, True)


class PlannerTest(unittest.TestCase):
    def test_majority_attribute_then_the_remaining_one(self):
        survey = ps.plan_sort(header(), None, [VO1, VO2, VI1], ATTRS)
        self.assertEqual((survey.key, survey.change), ("Vocal順", True))
        self.assertEqual(survey.counts, (("Vo", 2), ("Vi", 1)))
        # Vo card picked under Vocal順: keep (no change saves taps)
        self.assertFalse(ps.plan_sort(header("Vocal順"), VO1, [VO1, VI1], ATTRS).change)
        # only a Vi card remains: switch to Visual順
        plan = ps.plan_sort(header("Vocal順"), VI1, [VI1], ATTRS)
        self.assertEqual((plan.key, plan.change, plan.reason), ("Visual順", True, "attribute_sort"))

    def test_focus_card_wins_over_the_majority(self):
        plan = ps.plan_sort(header(), DA1, [DA1, VO1, VO2], ATTRS)
        self.assertEqual(plan.key, "Dance順")

    def test_no_plan_on_doubt(self):
        self.assertEqual(ps.plan_sort(None, VO1, [VO1], ATTRS).reason, "header_unread")
        self.assertEqual(ps.plan_sort(ps.Header(None, None, None, ("sort_unreadable",)), VO1, [VO1], ATTRS).reason,
                         "header_unread")
        self.assertEqual(ps.plan_sort(header("Lv順", "asc"), VO1, [VO1], ATTRS).reason, "direction_not_desc")
        unknown = ps.plan_sort(header("Lv順"), "2049990010", ["2049990010"], {})
        self.assertEqual((unknown.change, unknown.reason), (False, "focus_attribute_unknown"))

    def test_ranks_count_page_one(self):
        ranks = {"Vocal順": {VO1: 3, VI1: 40}, "Visual順": {VO1: 30, VI1: 2}}
        self.assertEqual(ps.plan_sort(header("Vocal順"), VI1, [VI1, VO1], ATTRS, ranks).key, "Visual順")
        self.assertFalse(ps.plan_sort(header("Vocal順"), VO1, [VI1, VO1], ATTRS, ranks).change)

    def test_fixed_key(self):
        # Auto 自动编队: always 「Lv順」, same header rules as the attribute plan
        plan = ps.fixed_plan(header(), "Lv順")
        self.assertEqual((plan.key, plan.change, plan.reason), ("Lv順", True, "fixed_key"))
        self.assertEqual((ps.fixed_plan(header("Lv順"), "Lv順").change, ps.fixed_plan(header("Lv順"), "Lv順").reason),
                         (False, "current_key_fixed"))
        self.assertEqual(ps.fixed_plan(header("Vocal順", "asc"), "Lv順").reason, "direction_not_desc")
        self.assertEqual(ps.fixed_plan(None, "Lv順").reason, "header_unread")
        with self.assertRaises(ValueError):
            ps.fixed_plan(header(), "Lv")

    def test_main_attribute(self):
        self.assertEqual(ps.main_attribute({"levels": {"1": {"Vo": 48, "Da": 32, "Vi": 50, "Me": 48},
                                                       "60": {"Vo": 150, "Da": 100, "Vi": 100, "Me": 155}}}), "Me")
        self.assertIsNone(ps.main_attribute({"levels": {"60": {"Vo": 140, "Da": 125, "Vi": 140, "Me": 100}}}))
        self.assertIsNone(ps.main_attribute(None))
        self.assertEqual(ps.card_attributes([VO1], library={VO1: {"levels": {"80": {"Vo": 9, "Da": 1, "Vi": 1,
                                                                                     "Me": 1}}}}), {VO1: "Vo"})


class ReadTest(unittest.TestCase):
    def test_header_vocabulary(self):
        h = ps.parse_header([("Vocal順", [1010, 113, 85, 30])], ["▼降順"], ["絞り込み"])
        self.assertEqual((h.key, h.direction, h.ok), ("Vocal順", "desc", True))
        self.assertEqual(ps.header_from(h.to_dict()), h)
        self.assertIn("filter_active:絞り込みON", ps.parse_header(["Vocal順"], ["降順"], ["絞り込みON"]).reasons)
        self.assertFalse(ps.parse_header([], ["降順"], ["絞り込み"]).ok)

    def popup_items(self):
        # popup text boxes as on #114 / #120 (centres inside the reserved cells)
        items = [("並び替え", [410, 128, 110, 32]), ("決定", [612, 525, 58, 36])]
        for key, (r, c) in ps.KEY_CELLS.items():
            x, y, w, h = ps.cell_rect(r, c)
            items.append((key, [x + 10, y + 5, w - 20, h - 10]))
        return items

    def test_popup_binds_each_key_to_its_cell(self):
        popup = ps.parse_popup(self.popup_items())
        self.assertTrue(ps.popup_known(popup))
        for key, cell in ps.KEY_CELLS.items():
            self.assertEqual(ps.popup_cell(popup, key), cell, key)
        self.assertIsNone(ps.popup_cell(ps.parse_popup(self.popup_items()[1:]), "Vocal順"))     # no title
        moved = [("Vocal順", [760, 330, 100, 30]) if t == "Vocal順" else (t, b) for t, b in self.popup_items()]
        self.assertIsNone(ps.popup_cell(ps.parse_popup(moved), "Vocal順"))                       # wrong cell

    def test_sort_targets(self):
        self.assertTrue(nav.TARGETS[wte.SORT_OPEN_INTENT][1])
        self.assertTrue(nav.TARGETS[wte.SORT_APPLY_INTENT][1])
        verified_keys = {i for i in wte.SORT_KEY_INTENTS if nav.TARGETS[i][1]}
        self.assertTrue({wte.sort_key_intent(1, 2), wte.sort_key_intent(2, 2), wte.sort_key_intent(3, 3)} <= verified_keys)
        for (rect, _), click in ((nav.TARGETS[wte.sort_key_intent(2, 2)], (637, 355)),
                                 (nav.TARGETS[wte.sort_key_intent(3, 3)], (827, 435)),
                                 (nav.TARGETS[wte.sort_key_intent(1, 2)], (639, 253)),     # Lv順 button, #113
                                 (nav.TARGETS[wte.SORT_OPEN_INTENT], (1045, 141)),
                                 (nav.TARGETS[wte.SORT_APPLY_INTENT], (652, 532))):
            x, y, w, h = rect
            self.assertTrue(x <= click[0] <= x + w and y <= click[1] <= y + h, (rect, click))
        self.assertIn(wte.SORT_OPEN_INTENT, nav.screen_intents("IdolPicker"))
        self.assertEqual(set(nav.screen_intents("PickerSort")), set(wte.SORT_POPUP_INTENTS))
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="start", picker_sort=True)
        self.assertFalse(nav.RunOptions(mode="start", team_edit=True).picker_sort)


def picker(key="デフォルト", tiles=(), direction="desc"):
    return {"picker_kind": "support", "picker_offset": 184, "picker_thumb_top": 191, "picker_at_bottom": False,
            "picker_scrollbar": "ok", "picker_tiles": list(tiles),
            "picker_header": ps.Header(key, direction, True).to_dict()}


def popup(selected="r1c1"):
    items = ReadTest.popup_items(None)
    return {"sort_popup_items": [[t, b] for t, b in items],
            "sort_selected": {f"r{r}c{c}": (0.12 if f"r{r}c{c}" == selected else 0.0) for r, c in ps.KEY_CELLS.values()}}


def runtime(sort=True, edit_only=False):
    TEAM = {"P": "1040020030", "S": [VO1, VO2, VI1, DA1], "guest": "2040150010"}
    rt = nav.WingNavRuntime(options=nav.RunOptions(mode="start", team_edit=True, picker_sort=sort, edit_only=edit_only))
    rt.memory.target_unit = 17
    rt.memory.team_slots = TEAM
    rt.attributes = dict(ATTRS)
    return rt


def acc(card):
    return {"status": "accepted", "card_id": card, "candidates": [[card, 0.95, 0.9]], "blank": False}


def unit(cards):
    return {"unit_index": 17, "unit_total": 20, "unit_page": 1, "unit_cards": cards}


class NavigatorTest(unittest.TestCase):
    """RunOptions.picker_sort: open -> key -> 決定 -> verified header -> scan (one change per visit)."""

    def setUp(self):
        self.patch = um.patch.object(wte, "VERIFIED_INTENTS", ALL)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def start(self, rt):
        cards = {"P": acc("1040020030"), "S1": acc("2040990010"), "S2": acc(VO2), "S3": acc(VI1), "S4": acc(DA1),
                 "guest": acc("2040150010")}
        d = rt.decide("UnitSelect", unit(cards))
        self.assertEqual(d.intent, "unit_slot_s1")

    def test_full_sort_then_tap(self):
        rt = runtime()
        self.start(rt)
        d = rt.decide("IdolPicker", picker("デフォルト", [{"slot": "r3c1", "status": "accepted", "card_id": "2040880010"}]))
        self.assertEqual((d.action, d.intent), ("click", wte.SORT_OPEN_INTENT))
        d = rt.decide("PickerSort", popup("r1c1"))
        self.assertEqual((d.action, d.intent), ("click", wte.sort_key_intent(2, 1)))       # Vocal順
        self.assertEqual(rt.decide("PickerSort", popup("r1c1")).reason, "picker_sort_key_pending")
        d = rt.decide("PickerSort", popup("r2c1"))
        self.assertEqual((d.action, d.intent), ("click", wte.SORT_APPLY_INTENT))
        self.assertEqual(rt.decide("PickerSort", popup("r2c1")).action, "wait")           # never 決定 twice
        # back on the list: must read Vocal順 before the scan restarts on the new order
        self.assertEqual(rt.decide("IdolPicker", picker("デフォルト")).reason, "picker_sort_applying")
        d = rt.decide("IdolPicker", picker("Vocal順", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual((d.action, d.intent), ("click", "picker_tile_r1c2"))
        events = [e["event"] for e in rt.log if e["event"].startswith("picker_sort")]
        self.assertEqual(events, ["picker_sort_plan", "picker_sort_tap", "picker_sort_tap", "picker_sort_applied"])
        self.assertEqual(rt.memory.team_edit["sort"]["original"], {"key": "デフォルト", "direction": "desc"})

    def test_edit_only_sorts_by_level(self):
        # Auto 自动编队: Lv順 (r1c2) instead of the Vocal順 the attribute plan would pick
        rt = runtime(edit_only=True)
        self.start(rt)
        d = rt.decide("IdolPicker", picker("デフォルト", [{"slot": "r3c1", "status": "accepted", "card_id": "2040880010"}]))
        self.assertEqual((d.action, d.intent), ("click", wte.SORT_OPEN_INTENT))
        d = rt.decide("PickerSort", popup("r1c1"))
        self.assertEqual((d.action, d.intent), ("click", wte.sort_key_intent(1, 2)))
        plan = next(e for e in rt.log if e["event"] == "picker_sort_plan")["plan"]
        self.assertEqual((plan["key"], plan["reason"]), ("Lv順", "fixed_key"))
        rt = runtime(edit_only=True)
        self.start(rt)
        d = rt.decide("IdolPicker", picker("Lv順", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual(d.intent, "picker_tile_r1c2")                                    # already Lv順: no change

    def test_current_key_kept_and_off_by_default(self):
        rt = runtime()
        self.start(rt)
        d = rt.decide("IdolPicker", picker("Vocal順", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual(d.intent, "picker_tile_r1c2")
        rt = runtime(sort=False)
        self.start(rt)
        d = rt.decide("IdolPicker", picker("デフォルト", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual(d.intent, "picker_tile_r1c2")
        self.assertEqual(rt.decide("PickerSort", popup()).reason, "picker_sort_not_automated")

    def test_unverified_key_falls_back_to_level_then_to_the_scan(self):
        # H2d: Vocal順 (r2c1) not verified -> the verified Lv順 (r1c2)
        rt = runtime()
        self.start(rt)
        with um.patch.object(wte, "VERIFIED_INTENTS", ALL - {wte.sort_key_intent(2, 1)}):
            d = rt.decide("IdolPicker", picker("デフォルト", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual((d.action, d.intent), ("click", wte.SORT_OPEN_INTENT))
        plan = next(e for e in rt.log if e["event"] == "picker_sort_plan")["plan"]
        self.assertEqual((plan["key"], plan["fallback_from"]), ("Lv順", "Vocal順"))
        # already Lv順: keep it
        rt = runtime()
        self.start(rt)
        with um.patch.object(wte, "VERIFIED_INTENTS", ALL - {wte.sort_key_intent(2, 1)}):
            d = rt.decide("IdolPicker", picker("Lv順", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual(d.intent, "picker_tile_r1c2")
        # neither verified -> the scan on the current order
        rt = runtime()
        self.start(rt)
        with um.patch.object(wte, "VERIFIED_INTENTS", ALL - {wte.sort_key_intent(2, 1), wte.sort_key_intent(1, 2)}):
            d = rt.decide("IdolPicker", picker("デフォルト", [{"slot": "r1c2", "status": "accepted", "card_id": VO1}]))
        self.assertEqual(d.intent, "picker_tile_r1c2")
        plan = next(e for e in rt.log if e["event"] == "picker_sort_plan")["plan"]
        self.assertTrue(plan["skipped"].startswith("sort_target_not_enabled"))

    def test_unknown_popup_and_unverified_header_stop(self):
        rt = runtime()
        self.start(rt)
        rt.decide("IdolPicker", picker("デフォルト"))
        bad = popup()
        bad["sort_popup_items"] = [i for i in bad["sort_popup_items"] if i[0] != "Vocal順"]
        self.assertEqual(rt.decide("PickerSort", bad).reason, "picker_sort_popup_unknown")
        rt = runtime()
        self.start(rt)
        rt.decide("IdolPicker", picker("デフォルト"))
        rt.decide("PickerSort", popup("r2c1"))                                            # already selected: 決定
        for _ in range(wte.SORT_WAITS):
            self.assertEqual(rt.decide("IdolPicker", picker("Dance順")).action, "wait")
        self.assertEqual(rt.decide("IdolPicker", picker("Dance順")).reason, "picker_sort_unverified")

    def test_expected_screens_per_sort_intent(self):
        rt = runtime()
        rt._last_intent = ("IdolPicker", wte.SORT_OPEN_INTENT)
        self.assertEqual(rt.expect_screens(("UnitSelect",)), {"PickerSort", "Loading"})
        rt._last_intent = ("PickerSort", wte.SORT_APPLY_INTENT)
        self.assertEqual(rt.expect_screens(()), {"IdolPicker", "Loading", "PickerSort"})


class TrainingKeyTest(unittest.TestCase):
    TARGET = "2049990010"

    def lib(self, n):
        row = lambda vo, vi: {"Vo": vo, "Da": 10, "Vi": vi, "Me": 10}  # noqa: E731
        cards = {f"20400{i:03d}10": {"rarity": "S_SSR", "max_levels": {"0": 60},
                                     "levels": {str(lv): row(100 + i, 20) for lv in (40, 60, 80)}} for i in range(n)}
        cards[self.TARGET] = {"rarity": "S_SSR", "max_levels": {"0": 60},
                              "levels": {str(lv): row(10, 500) for lv in (40, 60, 80)}}
        return cards

    def test_lv_by_default_and_attribute_when_clearly_better(self):
        lib = self.lib(50)
        self.assertEqual(ps.training_key(self.TARGET, {}, lib)[0], "Lv順")
        sparse = {c: 60 for c in list(lib)[:9] + [self.TARGET]}
        self.assertEqual(ps.training_key(self.TARGET, sparse, lib), ("Lv順", "owned_levels_sparse:9"))
        # half the cards Lv40, half Lv80, the target Lv60: 25 cards before it either way under Lv順;
        # under Visual順 it is first -> Visual順
        levels = {c: (40 if i % 2 else 80) for i, c in enumerate(lib) if c != self.TARGET}
        levels[self.TARGET] = 60
        key, why = ps.training_key(self.TARGET, levels, lib)
        self.assertEqual(key, "Visual順", why)
        # a low-level target: Lv順 (▲昇順 side) is as good -> Lv順
        levels[self.TARGET] = 40
        self.assertEqual(ps.training_key(self.TARGET, levels, lib)[0], "Lv順")


@unittest.skipUnless((REC / "frame-000121.png").is_file(), "private recording 2026-10-11")
class RecordingReplay(unittest.TestCase):
    """Pink selection on the recorded 並び替え frames (the clean key pairs) and no popup state on list frames."""

    def share(self, n):
        import numpy as np
        from PIL import Image
        rgb = np.asarray(Image.open(REC / f"frame-{n:06d}.png").convert("RGB"))
        return {cell: ps.selected_share(rgb, cell) for cell in ps.KEY_CELLS.values()}

    def selected(self, n):
        return [cell for cell, v in self.share(n).items() if v >= ps.SELECTED_MIN]

    def test_clean_pairs(self):
        self.assertEqual(self.selected(113), [(1, 1)])      # opened on デフォルト
        self.assertEqual(self.selected(120), [(2, 1)])      # reopened on the saved Vocal順
        self.assertEqual(self.selected(121), [(2, 2)])      # (637,355) -> Dance順 selected
        self.assertEqual(self.selected(143), [(3, 2)])
        self.assertEqual(self.selected(145), [(3, 3)])      # (827,435) -> リリース順 selected
        self.assertEqual(self.selected(161), [(2, 1)])

    def ocr(self, n):
        import json
        path = ROOT / "dev_assets/private/wing-demo4-analysis/ocr.jsonl"
        if not path.is_file():
            self.skipTest("recording OCR")
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["seq"] == n:
                return [(t, tuple(b)) for t, b in row["texts"]]
        self.skipTest(f"no OCR for #{n}")

    def inside(self, texts, roi):
        from agent.core import training_select as ts
        return [(t, b) for t, b in texts if ts._within(b, roi)]

    def header(self, n):
        from agent.core import training_select as ts
        texts = self.ocr(n)
        return ps.parse_header(self.inside(texts, ts.SORT_BOX), self.inside(texts, ts.DIRECTION_BOX),
                               self.inside(texts, ts.FILTER_BOX))

    def test_headers_of_the_recorded_lists(self):
        for n, key in ((118, "Vocal順"), (123, "Dance順"), (162, "Vocal順")):
            h = self.header(n)
            self.assertEqual((h.key, h.direction, h.filter_off), (key, "desc", True), n)

    def test_recorded_popup_drives_the_key_then_apply(self):
        # #120: popup opened on the saved Vocal順; Maa wants Dance順 -> tap r2c2; #121 shows it selected -> 決定
        from agent.core import training_select as ts
        sort = dict(wte.new_sort_state(), phase="open_sent", want="Dance順", cell=(2, 2))
        for n, intent in ((120, wte.sort_key_intent(2, 2)), (121, wte.SORT_APPLY_INTENT)):
            popup = ps.parse_popup(self.inside(self.ocr(n), ts.SORT_POPUP_ROI))
            self.assertTrue(ps.popup_known(popup), n)
            shares = self.share(n)
            step = wte.sort_popup_step(sort, popup, lambda cell: shares[tuple(cell)])
            self.assertEqual((step["action"], step["intent"]), ("tap", intent), n)

    def test_sort_wait_bound(self):
        self.assertGreaterEqual(wte.SORT_WAITS, 2)


if __name__ == "__main__":
    unittest.main()
