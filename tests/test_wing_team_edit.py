"""W.I.N.G. team edit (RunOptions.team_edit, 2026-10-12): slot planning, team rules, picker tile lookup and the
runtime flow replayed on the supervised recording captures-20261011-164549Z (user edited ユニット17, P 風野灯織)."""
from __future__ import annotations

import unittest
from pathlib import Path

from agent.core import wing_nav as nav
from agent.core import wing_team_edit as wte

ROOT = Path(__file__).resolve().parent.parent
SHIPPED_INTENTS = wte.VERIFIED_INTENTS          # recorded tiles + assets/input/wing_picker_verified.json (live set)
RECORDED_ONLY = frozenset(wte.tile_intent(r, c) for r, c in wte.VERIFIED_TILES)
_patch = None


def setUpModule():
    # Behaviour tests run against the four recorded tiles only, independent of later live verifications.
    global _patch
    import unittest.mock as um
    _patch = um.patch.object(wte, "VERIFIED_INTENTS", RECORDED_ONLY)
    _patch.start()


def tearDownModule():
    _patch.stop()
REC = ROOT / "dev_assets/private/captures-20261011-164549Z"
# The team of the recording's final formation (ユニット確認 #239): P 灯織, S 恋鐘 / 023 / 咲耶 / 摩美々, guest 結華.
TEAM = {"P": "1040020030", "S": ["2040040090", "2040230040", "2040060080", "2040050080"], "guest": "2040070080"}


def acc(card):
    return {"status": "accepted", "card_id": card, "candidates": [[card, 0.95, 0.9]], "blank": False}


def full(**over):
    cards = {"P": acc(TEAM["P"]), **{f"S{i + 1}": acc(c) for i, c in enumerate(TEAM["S"])}, "guest": acc(TEAM["guest"])}
    cards.update(over)
    return cards


class PlanTest(unittest.TestCase):
    def test_done_and_s_as_set(self):
        self.assertEqual(wte.next_slot(full(), wte.expected_slots(TEAM), {})["action"], "done")
        swapped = full(S1=acc(TEAM["S"][1]), S2=acc(TEAM["S"][0]))
        self.assertEqual(wte.next_slot(swapped, wte.expected_slots(TEAM), {})["action"], "done")

    def test_first_wrong_slot_is_edited_once(self):
        cards = full(S3=acc("2040180020"))
        step = wte.next_slot(cards, wte.expected_slots(TEAM), {})
        self.assertEqual((step["action"], step["slot"], step["card"]), ("edit", "S3", TEAM["S"][2]))
        self.assertEqual(wte.next_slot(cards, wte.expected_slots(TEAM), {"S3": TEAM["S"][2]})["reason"],
                         "team_edit_slot_repeat")

    def test_unread_blank_and_idol_elsewhere(self):
        unread = {"status": "rejected", "card_id": None, "candidates": [[TEAM["S"][2][:-1] + "9", 0.6, 0.4]]}
        self.assertEqual(wte.next_slot(full(S3=unread), wte.expected_slots(TEAM), {})["reason"], "team_edit_slot_unread")
        self.assertEqual(wte.next_slot(full(guest={**acc(TEAM["guest"]), "blank": True}), wte.expected_slots(TEAM),
                                       {})["action"], "wait")
        # S3 wants 咲耶 (idol 006) while S4 holds another 咲耶 card: replace S4 first, never S3 first.
        cards = full(S3=acc("2040180020"), S4=acc("2040060210"))
        step = wte.next_slot(cards, wte.expected_slots(TEAM), {})
        self.assertEqual((step["action"], step["slot"]), ("edit", "S4"))
        # every wrong slot clashes (S3/S4 hold each other's idols on other cards): the game would move them -> stop
        cards = full(S3=acc("2040050210"), S4=acc("2040060210"))
        self.assertEqual(wte.next_slot(cards, wte.expected_slots(TEAM), {})["reason"], "team_edit_idol_in_other_slot")
        # a new S card of the old P's idol: the P is replaced first
        cards = full(P=acc("1040040010"), S1=acc("2040180020"))
        self.assertEqual(wte.next_slot(cards, wte.expected_slots(TEAM), {})["slot"], "P")

    def test_team_rules(self):
        self.assertEqual(wte.team_rule_problems(wte.expected_slots(TEAM)), [])
        dup = wte.expected_slots({**TEAM, "guest": "2040020010"})           # guest = P idol 002
        self.assertTrue(any(p.startswith("team_duplicate_idol") for p in wte.team_rule_problems(dup)))
        self.assertIn("team_guest_not_owned", wte.team_rule_problems(wte.expected_slots(TEAM), {"s": ["2040040090"]}))
        self.assertIn("team_s_requires_four", wte.team_rule_problems(wte.expected_slots({**TEAM, "S": TEAM["S"][:3]})))

    def test_find_tile_top_only_unique(self):
        tiles = [{"slot": "r2c7", "status": "accepted", "card_id": TEAM["P"]}]
        self.assertEqual(wte.find_tile(tiles, 184, 191, TEAM["P"])["intent"], "picker_tile_r2c7")
        self.assertTrue(wte.find_tile(tiles, 184, 191, TEAM["P"])["verified"])
        # 2026-10-14: a scrolled grid maps to a tap phase; the phase-b rect of r2c7 is not verified -> never tapped
        scrolled = wte.find_tile(tiles, 258, 201, TEAM["P"])
        self.assertEqual((scrolled["reason"], scrolled["intent"]), ("team_edit_tile_not_enabled", "picker_tile_b_r2c7"))
        self.assertEqual(wte.find_tile(tiles, 258, 201, TEAM["P"], verified={"picker_tile_b_r2c7"})["intent"],
                         "picker_tile_b_r2c7")
        self.assertEqual(wte.find_tile(tiles * 2, 184, 191, TEAM["P"])["reason"], "team_edit_tile_ambiguous")
        self.assertEqual(wte.find_tile(tiles, 184, 191, "1000000000")["reason"], "card_not_visible")
        self.assertEqual(wte.find_tile(tiles, None, 191, TEAM["P"])["reason"], "picker_offset_unread")
        self.assertEqual(wte.find_tile([{"slot": "r3c3", "status": "accepted", "card_id": TEAM["P"]}], 184, 191,
                                       TEAM["P"])["reason"], "team_edit_tile_not_enabled")

    def test_phases_cover_every_offset(self):
        # card_inventory reads the grid offset in [170, 324) in 2 px steps; every fully visible row has a phase whose
        # fixed rect lies inside the tile with the margin.
        for offset in range(170, 324, 2):
            rows, y = 0, offset
            while y + wte.GRID_TILE <= 680:
                rows, y = rows + 1, y + wte.GRID_DY
            for row in range(1, rows + 1):
                phases = wte.phase_for(offset, row)
                self.assertTrue(phases, (offset, row))
                for phase in phases:
                    x, ry, w, h = wte.tile_rect(1, 1, phase)
                    top = offset + wte.GRID_DY * (row - 1)
                    rect_top = ry + wte.GRID_DY * (row - 1)
                    self.assertTrue(top <= rect_top and rect_top + h <= top + wte.GRID_TILE, (offset, row, phase))
        self.assertEqual(wte.tile_rect(1, 1), (283, 200, 76, 76))       # the recorded list-top rects are unchanged
        self.assertEqual(wte.tile_rect(2, 7), (1111, 354, 76, 76))

    def test_targets_and_options(self):
        for slot, intent in wte.SLOT_INTENTS.items():
            self.assertTrue(nav.TARGETS[intent][1], intent)
            self.assertIn(intent, nav.screen_intents("UnitSelect"))
        verified = {i for i in wte.PICKER_INTENTS if nav.TARGETS[i][1]}
        self.assertEqual(verified, set(SHIPPED_INTENTS) & set(wte.PICKER_INTENTS))   # + sort popup intents (PickerSort)
        self.assertTrue({wte.tile_intent(r, c) for r, c in wte.VERIFIED_TILES} <= verified)
        for intent in wte.PICKER_INTENTS:
            self.assertIn(intent, nav.screen_intents("IdolPicker"))
        self.assertEqual(nav.WHEEL_INTENTS, {"picker_scroll_down": -60, "picker_scroll_up": 60,
                                             "options_scroll_down": -60})   # + オプション read pass
        self.assertTrue(nav.TARGETS["unit_page_next"][1])
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="attach", team_edit=True)
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="start", team_edit=True, unit_search=True)
        self.assertFalse(nav.RunOptions().team_edit)


def runtime(team=TEAM):
    rt = nav.WingNavRuntime(options=nav.RunOptions(mode="start", team_edit=True))
    rt.memory.target_unit = 17
    rt.memory.team_slots = dict(team)
    return rt


def unit(cards):
    return {"unit_index": 17, "unit_total": 20, "unit_page": 1, "unit_cards": cards}


class RuntimeFlowTest(unittest.TestCase):
    def test_edit_pick_verify_then_next(self):
        rt = runtime()
        d = rt.decide("UnitSelect", unit(full(S4=acc("2040080140"))))
        self.assertEqual((d.action, d.intent), ("click", "unit_slot_s4"))
        # picker not opened yet (same page re-read): wait, never a second slot tap
        self.assertEqual(rt.decide("UnitSelect", unit(full(S4=acc("2040080140")))).action, "wait")
        pick = rt.decide("IdolPicker", {"picker_kind": "support", "picker_offset": 184, "picker_thumb_top": 191,
                                        "picker_tiles": [{"slot": "r2c6", "status": "accepted", "card_id": TEAM["S"][3]}]})
        self.assertEqual((pick.action, pick.intent), ("click", "picker_tile_r2c6"))
        again = rt.decide("IdolPicker", {"picker_kind": "support", "picker_offset": 184, "picker_thumb_top": 191,
                                         "picker_tiles": [{"slot": "r2c6", "status": "accepted", "card_id": TEAM["S"][3]}]})
        self.assertEqual(again.reason, "team_edit_pick_unconfirmed")

    def test_pick_result_must_match(self):
        rt = runtime()
        rt.decide("UnitSelect", unit(full(S4=acc("2040080140"))))
        rt.decide("IdolPicker", {"picker_kind": "support", "picker_offset": 184, "picker_thumb_top": 191,
                                 "picker_tiles": [{"slot": "r2c6", "status": "accepted", "card_id": TEAM["S"][3]}]})
        d = rt.decide("UnitSelect", unit(full(S4=acc("2040080140"))))
        self.assertEqual((d.action, d.reason), ("stop", "team_edit_slot_unverified"))
        rt2 = runtime()
        rt2.decide("UnitSelect", unit(full(S4=acc("2040080140"))))
        rt2.decide("IdolPicker", {"picker_kind": "support", "picker_offset": 184, "picker_thumb_top": 191,
                                  "picker_tiles": [{"slot": "r2c6", "status": "accepted", "card_id": TEAM["S"][3]}]})
        done = rt2.decide("UnitSelect", unit(full()))
        self.assertEqual((done.action, done.intent), ("click", "unit_next"))
        self.assertTrue(any(e.get("event") == "team_edit_done" for e in rt2.log))

    def test_edit_only_returns_to_produce_select(self):
        # Auto 自动编队: verified team -> ← once (never 次へ) -> team_edit_done on プロデュース選択
        def prep():
            rt = nav.WingNavRuntime(options=nav.RunOptions(mode="start", team_edit=True, edit_only=True))
            rt.memory.target_unit = 17
            rt.memory.team_slots = dict(TEAM)
            return rt
        rt = prep()
        d = rt.decide("UnitSelect", unit(full()))
        self.assertEqual((d.action, d.intent), ("click", "unit_back"))
        self.assertEqual(rt.decide("UnitSelect", unit(full())).reason, "unit_back_pending")
        d = rt.decide("ProduceSelect", {"difficulty": "beginner"})
        self.assertEqual((d.action, d.reason), ("stop", "team_edit_done"))
        rt = prep()
        rt.decide("UnitSelect", unit(full()))
        reasons = [rt.decide("UnitSelect", unit(full())).reason for _ in range(rt.UNIT_BACK_WAITS + 1)]
        self.assertEqual(reasons, ["unit_back_pending"] * rt.UNIT_BACK_WAITS + ["unit_back_no_effect"])
        self.assertIn("ProduceSelect", nav.RULES["UnitSelect"].expect)
        self.assertTrue(nav.TARGETS["unit_back"][1])
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="start", edit_only=True)                     # only with team_edit
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="start", team_edit=True, edit_only=True, allow_start_spend=True)

    def test_pale_pick_passes_only_the_edit_check(self):
        # Live 2026-10-25 unit20 S4 after picking 2040080010 (霧・霧・奇・譚): best match, but coarse below 0.78.
        from agent.core import wing_unit_check as wuc
        pale = {"status": "rejected", "card_id": None, "score": 0.7632, "blank": False,
                "candidates": [["2040080010", 0.7632, 0.65], ["1930080010", 0.7296, 0.5754],
                               ["1040130050", 0.7291, 0.5677]]}
        self.assertEqual(wuc._slot_verdict(pale, "2040080010"), "uncertain")
        self.assertEqual(wuc.edit_slot_verdict(pale, "2040080010"), "match")
        self.assertEqual(wuc.edit_slot_verdict(pale, "1930080010"), "uncertain")            # not the best match
        close = {**pale, "candidates": [["2040080010", 0.7632, 0.62], ["1040130050", 0.7291, 0.57]]}
        self.assertEqual(wuc.edit_slot_verdict(close, "2040080010"), "uncertain")           # fine gap 0.05
        low = {**pale, "candidates": [["2040080010", 0.73, 0.65], ["1040130050", 0.70, 0.50]]}
        self.assertEqual(wuc.edit_slot_verdict(low, "2040080010"), "uncertain")             # coarse < 0.74
        self.assertEqual(wuc.edit_slot_verdict(acc("2040180020"), "2040080010"), "mismatch")
        team = {**TEAM, "S": TEAM["S"][:3] + ["2040080010"]}
        cards = full(S4=pale)
        self.assertEqual(wuc.compare(cards, team)["verdict"], "uncertain")                  # resume checks: strict
        self.assertEqual(wuc.compare(cards, team, editing=True)["verdict"], "match")
        # The run that picked it verifies the slot and continues to 次へ.
        rt = runtime(team)
        self.assertEqual(rt.decide("UnitSelect", unit(full(S4=acc("2040180020")))).intent, "unit_slot_s4")
        rt.decide("IdolPicker", {"picker_kind": "support", "picker_offset": 184, "picker_thumb_top": 191,
                                 "picker_tiles": [{"slot": "r2c6", "status": "accepted", "card_id": "2040080010"}]})
        d = rt.decide("UnitSelect", unit(cards))
        self.assertEqual((d.action, d.intent), ("click", "unit_next"), d.to_dict())
        # A later run finds the slot already holding it: no edit, same verdict.
        d = runtime(team).decide("UnitSelect", unit(cards))
        self.assertEqual((d.action, d.intent), ("click", "unit_next"), d.to_dict())

    def test_wrong_picker_and_rules(self):
        rt = runtime()
        rt.decide("UnitSelect", unit(full(P=acc("1040060060"))))
        self.assertEqual(rt.decide("IdolPicker", {"picker_kind": "support"}).reason, "team_edit_picker_kind")
        bad = runtime({**TEAM, "guest": "2040020010"})
        self.assertEqual(bad.decide("UnitSelect", unit(full())).reason, "team_rules_violated")
        off = nav.WingNavRuntime(options=nav.RunOptions(mode="start"))
        self.assertEqual(off.decide("IdolPicker", {}).reason, "team_edit_not_automated")


S_NEW = ["2040180020", "2040080140", "2040120010", "2040130010"]       # the unit's current (wrong) support cards
ALL = set(wte.PICKER_INTENTS)                                           # as if every picker target were live-verified


def tile(slot, card):
    return {"slot": slot, "status": "accepted", "card_id": card}


def frame(tiles, offset=184, thumb=191, bottom=False):
    return {"picker_kind": "support", "picker_offset": offset, "picker_thumb_top": thumb, "picker_at_bottom": bottom,
            "picker_scrollbar": "ok", "picker_tiles": tiles}


def settle(visit, reading, card, verified=ALL):
    """After a wheel step the scan waits for two equal frames; feed the frame twice."""
    first = wte.picker_step(visit, reading, card, verified)
    if first["action"] != "wait":
        return first
    return wte.picker_step(visit, reading, card, verified)


class PickerScanTest(unittest.TestCase):
    """2026-10-14 read-only scroll scan of the picker (wing_team_edit.picker_step)."""

    def test_list_top_tap_without_scrolling(self):
        visit = wte.new_visit("S1")
        step = wte.picker_step(visit, frame([tile("r1c2", TEAM["S"][0])]), TEAM["S"][0])
        self.assertEqual((step["action"], step["intent"]), ("tap", "picker_tile_r1c2"))

    def test_scroll_not_enabled_stops_before_any_tap(self):
        # shipped state: the wheel targets are unverified -> a card below the first page stops (the user's message)
        visit = wte.new_visit("S1")
        step = wte.picker_step(visit, frame([tile("r1c1", "2040990010")]), TEAM["S"][0])
        self.assertEqual((step["action"], step["reason"]), ("stop", "team_edit_scroll_not_enabled"))

    def test_scroll_down_then_tap_on_a_scrolled_phase(self):
        visit = wte.new_visit("S1")
        top = frame([tile("r1c1", "2040990010"), tile("r3c1", "2040990020")])
        self.assertEqual(wte.picker_step(visit, top, TEAM["S"][0], ALL)["intent"], "picker_scroll_down")
        # content moved ~160 px: the old row 3 is now row 1-ish; the wanted card shows up at offset 238 (phase b)
        moved = frame([tile("r1c1", "2040990020"), tile("r2c3", TEAM["S"][0])], offset=238, thumb=230)
        step = settle(visit, moved, TEAM["S"][0])
        self.assertEqual((step["action"], step["intent"]), ("tap", "picker_tile_b_r2c3"))
        self.assertEqual(visit["pos"], 1)

    def test_overlap_lost_and_end_of_list(self):
        visit = wte.new_visit("S1")
        wte.picker_step(visit, frame([tile("r1c1", "2040990010")]), TEAM["S"][0], ALL)
        jumped = frame([tile("r1c1", "2040990030")], offset=200, thumb=260)
        self.assertEqual(settle(visit, jumped, TEAM["S"][0])["reason"], "team_edit_scroll_overlap_lost")
        visit = wte.new_visit("S1")
        end = frame([tile("r1c1", "2040990010")], bottom=True)
        step = wte.picker_step(visit, end, TEAM["S"][0], ALL)
        self.assertEqual((step["reason"], step["cards"]), ("team_edit_card_not_in_list", {TEAM["S"][0]: "card_not_in_list"}))

    def test_scroll_budget_and_unsettled(self):
        visit = wte.new_visit("S1")
        visit["scrolls"] = wte.MAX_PICKER_SCROLLS
        self.assertEqual(wte.picker_step(visit, frame([tile("r1c1", "2040990010")]), TEAM["S"][0], ALL)["reason"],
                         "team_edit_scroll_budget")
        visit = wte.new_visit("S1")
        wte.picker_step(visit, frame([tile("r1c1", "2040990010")]), TEAM["S"][0], ALL)
        for i in range(wte.SETTLE_WAITS):
            self.assertEqual(wte.picker_step(visit, frame([tile("r1c1", "2040990010")], offset=180 + 2 * i), TEAM["S"][0],
                                             ALL)["action"], "wait")
        self.assertEqual(wte.picker_step(visit, frame([tile("r1c1", "2040990010")], offset=250), TEAM["S"][0], ALL)["reason"],
                         "team_edit_scroll_unsettled")

    def test_opened_scrolled_rewinds_to_top_first(self):
        visit = wte.new_visit("S1")
        step = wte.picker_step(visit, frame([tile("r1c1", TEAM["S"][0])], offset=246, thumb=300), TEAM["S"][0], ALL)
        self.assertEqual(step["intent"], "picker_scroll_up")

    def test_survey_needs_every_support_card_before_the_first_tap(self):
        survey = {"S1": TEAM["S"][0], "S2": TEAM["S"][1]}
        visit = wte.new_visit("S1", survey)
        first = frame([tile("r1c2", TEAM["S"][0]), tile("r1c1", "2040990010")])
        # S1's card is on the first page but S2's is not: no tap yet, scroll to look for it
        self.assertEqual(wte.picker_step(visit, first, TEAM["S"][0], ALL)["intent"], "picker_scroll_down")
        end = frame([tile("r1c1", "2040990010"), tile("r2c1", "2040990020")], offset=246, thumb=600, bottom=True)
        step = settle(visit, end, TEAM["S"][0])
        self.assertEqual((step["action"], step["reason"], step["stage"]), ("stop", "team_edit_card_not_in_list", "survey"))
        # both found (second after one step) -> scroll back up and tap S1's card
        visit = wte.new_visit("S1", survey)
        wte.picker_step(visit, first, TEAM["S"][0], ALL)
        lower = frame([tile("r1c1", "2040990010"), tile("r2c4", TEAM["S"][1])], offset=238, thumb=260)
        self.assertEqual(settle(visit, lower, TEAM["S"][0])["intent"], "picker_scroll_up")
        self.assertTrue(visit["surveyed"])
        back = frame([tile("r1c2", TEAM["S"][0]), tile("r1c1", "2040990010")])
        self.assertEqual(settle(visit, back, TEAM["S"][0])["intent"], "picker_tile_r1c2")

    def test_survey_with_shipped_targets_stops_on_unverified_tiles(self):
        # first page holds both cards, one on an unverified tile: stop before any swap (not enabled)
        visit = wte.new_visit("S1", {"S1": TEAM["S"][0], "S2": TEAM["S"][1]})
        step = wte.picker_step(visit, frame([tile("r1c2", TEAM["S"][0]), tile("r3c5", TEAM["S"][1]), tile("r1c1", "x")]),
                               TEAM["S"][0])
        self.assertEqual((step["action"], step["reason"]), ("stop", "team_edit_scroll_not_enabled"))

    def test_verified_file(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.json"
            path.write_text(json.dumps({"version": 1, "verified": {"picker_tile_b_r1c1": "live #12->#13",
                                                                    "picker_scroll_down": "", "unit_next": "x"}}),
                            encoding="utf-8")
            got = wte.load_verified(path)
            self.assertIn("picker_tile_b_r1c1", got)
            self.assertNotIn("picker_scroll_down", got)      # no evidence -> not verified
            self.assertNotIn("unit_next", got)               # never anything outside the picker
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(set(wte.load_verified(path)),
                             {wte.tile_intent(r, c) for r, c in wte.VERIFIED_TILES} | set(wte.SORT_RECORDED))
            from tools import wing_picker_enable as tool
            path.write_text(json.dumps({"version": 1, "verified": {}}), encoding="utf-8")
            tool.write({"picker_scroll_down": "live #1->#2"}, path)
            self.assertEqual(wte.load_verified(path)["picker_scroll_down"], "live #1->#2")
            with self.assertRaises(ValueError):
                tool.write({"picker_tile_r1c1": "x"}, path)  # recorded tiles are built in
            with self.assertRaises(ValueError):
                tool.write({"picker_tile_r1c3": " "}, path)  # evidence required

    def test_action_table_ships_new_picker_targets_disabled(self):
        import json
        table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
        rows = {a["id"]: a for a in table["actions"] if a["id"].startswith("wing-picker-") and a["id"] != "wing-picker-confirm-ok"}
        self.assertEqual(len(rows), len(wte.PICKER_INTENTS) + len(wte.SORT_POPUP_INTENTS))   # + 並び替え
        enabled = {i for i, a in rows.items() if a["enabled"]}
        self.assertEqual(enabled, {nav.action_id(i) for i in SHIPPED_INTENTS})
        self.assertEqual(rows["wing-picker-scroll-down"]["wheel"], -60)
        self.assertEqual(rows["wing-picker-scroll-up"]["wheel"], 60)


class SameIdolConfirmTest(unittest.TestCase):
    """2026-10-14 live (unit20 S3): the unit held the wanted idol in an unrecognised slot; the game asked
    「ユニット内の同一のアイドルが外れてしまいます」. OK once for the picked slot, then fill the emptied slot."""

    def setUp(self):
        self.rt = runtime()
        # S4 really holds idol 004 (wanted in S1) but reads as another idol: only the game knows -> its dialog
        hidden = {"status": "rejected", "card_id": None, "candidates": [["2040090010", 0.9, 0.5]], "blank": False}
        self.cards = full(S1=acc("2040990010"), S4=hidden)

    def test_ok_once_then_fill_the_emptied_slot(self):
        rt = self.rt
        self.assertEqual(rt.decide("UnitSelect", unit(self.cards)).intent, "unit_slot_s1")
        self.assertEqual(rt.decide("IdolPicker", frame([tile("r1c1", TEAM["S"][0]), tile("r1c2", TEAM["S"][3])])).intent, "picker_tile_r1c1")
        d = rt.decide("IdolPickerConfirm", {})
        self.assertEqual((d.action, d.intent), ("click", "picker_confirm_ok"))
        emptied = full(S4={"status": "rejected", "card_id": None, "candidates": [], "blank": True})
        for _ in range(nav.WingNavRuntime.TEAM_EDIT_VACATED_READS):
            self.assertEqual(rt.decide("UnitSelect", unit(emptied)).action, "wait")
        d = rt.decide("UnitSelect", unit(emptied))
        self.assertEqual((d.action, d.intent), ("click", "unit_slot_s4"))
        self.assertEqual(rt.memory.team_edit["done"], {"S1": TEAM["S"][0]})

    def test_confirm_stops_outside_its_slot_or_twice(self):
        self.assertEqual(nav.WingNavRuntime().decide("IdolPickerConfirm", {}).reason, "team_edit_not_automated")
        rt = self.rt
        rt.decide("UnitSelect", unit(self.cards))
        self.assertEqual(rt.decide("IdolPickerConfirm", {}).reason, "team_edit_confirm_unexpected")   # no tile yet
        rt.decide("IdolPicker", frame([tile("r1c1", TEAM["S"][0]), tile("r1c2", TEAM["S"][3])]))
        self.assertEqual(rt.decide("IdolPickerConfirm", {}).intent, "picker_confirm_ok")
        self.assertEqual(rt.decide("IdolPickerConfirm", {}).reason, "team_edit_confirm_repeat")

    def test_strong_candidate_of_the_wanted_idol_is_replaced_first(self):
        seen = {"status": "rejected", "card_id": None, "candidates": [["2040040010", 0.9, 0.5]], "blank": False}
        step = wte.next_slot(full(S1=acc("2040990010"), S4=seen), wte.expected_slots(TEAM), {})
        self.assertEqual((step["action"], step["slot"]), ("edit", "S4"))   # S1's idol leaves S4 first: no dialog

    def test_a_slot_empty_from_the_start_is_filled_after_the_loading_bound(self):
        rt = runtime()
        empty = full(S4={"status": "rejected", "card_id": None, "candidates": [], "blank": True})
        for _ in range(nav.WingNavRuntime.TEAM_EDIT_BLANK_WAITS):
            self.assertEqual(rt.decide("UnitSelect", unit(empty)).action, "wait")
        self.assertEqual(rt.decide("UnitSelect", unit(empty)).intent, "unit_slot_s4")

    def test_an_edited_slot_emptied_stops(self):
        done = {"S1": TEAM["S"][0]}
        cards = full(S1={"status": "rejected", "card_id": None, "candidates": [], "blank": True})
        step = wte.next_slot(cards, wte.expected_slots(TEAM), done, fill_blank=True)
        self.assertEqual((step["action"], step["reason"]), ("stop", "team_edit_done_slot_vacated"))


class SupportFirstRuntimeTest(unittest.TestCase):
    def test_support_slots_before_p_and_survey_on_first_support_visit(self):
        rt = runtime()
        cards = full(P=acc("1040060060"), S1=acc(S_NEW[0]), S2=acc(S_NEW[1]))
        d = rt.decide("UnitSelect", unit(cards))
        self.assertEqual(d.intent, "unit_slot_s1")                       # support first, P last
        self.assertEqual(rt.memory.team_edit["todo"], {"S1": TEAM["S"][0], "S2": TEAM["S"][1], "P": TEAM["P"]})
        # S2's card is not on the first page and the wheel is not enabled: stop before any swap
        d = rt.decide("IdolPicker", frame([tile("r1c2", TEAM["S"][0]), tile("r1c1", "2040990010")]))
        self.assertEqual((d.action, d.reason), ("stop", "team_edit_scroll_not_enabled"))
        self.assertEqual(d.detail["edited"], {})

    def test_p_missing_after_support_swaps_is_partial(self):
        rt = runtime()
        cards = full(P=acc("1040060060"), S4=acc(S_NEW[1]))
        self.assertEqual(rt.decide("UnitSelect", unit(cards)).intent, "unit_slot_s4")
        self.assertEqual(rt.decide("IdolPicker", frame([tile("r2c6", TEAM["S"][3])])).intent, "picker_tile_r2c6")
        d = rt.decide("UnitSelect", unit(full(P=acc("1040060060"))))
        self.assertEqual(d.intent, "unit_slot_p")
        produce = {**frame([tile("r1c1", "1040990010")]), "picker_kind": "produce"}
        d = rt.decide("IdolPicker", produce)
        self.assertEqual(d.reason, "team_edit_scroll_not_enabled_partial")
        self.assertEqual(d.detail["edited"], {"S4": TEAM["S"][3]})

    def test_scroll_click_is_a_bound_intent(self):
        rt = runtime()
        rt.decide("UnitSelect", unit(full(S4=acc(S_NEW[1]))))
        import unittest.mock as um
        with um.patch.object(wte, "VERIFIED_INTENTS", frozenset(ALL)):
            d = rt.decide("IdolPicker", frame([tile("r1c1", "2040990010")]))
        self.assertEqual((d.action, d.intent), ("click", "picker_scroll_down"))
        # 2026-10-14 live: the list is still moving after the wheel -> wait (the rule's fallback), never a stop
        with um.patch.object(wte, "VERIFIED_INTENTS", frozenset(ALL)):
            d = rt.decide("IdolPicker", frame([tile("r1c1", "2040990020")]))
        self.assertEqual((d.action, d.reason), ("wait", "picker_scrolling"))
        self.assertIn("picker_scroll_down", nav.screen_intents("IdolPicker"))
        self.assertGreater(nav.REPEAT_LIMITS["picker_scroll_down"], wte.MAX_PICKER_SCROLLS)


@unittest.skipUnless((REC / "frame-000093.png").is_file(), "private recording 2026-10-11")
class NativeReplay(unittest.TestCase):
    """The recording's own picks are what the editor chooses on the same frames."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        from PIL import Image
        from agent.core import wing_screen as wsc
        from agent.core import wing_unit_check as wuc
        if wuc.card_index() is None:
            raise unittest.SkipTest("card index not available")
        cls.frame = lambda _, n: np.asarray(Image.open(REC / f"frame-{n:06d}.png").convert("RGB"))
        cls.picker = lambda self, n: wsc.read_picker(self.frame(n))
        cls.cards = lambda self, n: wuc.read_unit_cards(self.frame(n))

    def test_picks_match_user_taps(self):
        for frame, card, intent in ((93, TEAM["P"], "picker_tile_r2c7"),       # user (1179,399) -> #98 P
                                    (163, TEAM["S"][0], "picker_tile_r1c2"),   # user (441,243)
                                    (218, TEAM["S"][3], "picker_tile_r2c6")):  # user (1036,398) -> #220 S4
            r = self.picker(frame)
            self.assertEqual(wte.find_tile(r["picker_tiles"], r["picker_offset"], r["picker_thumb_top"], card)
                             .get("intent"), intent, frame)

    def test_final_formation_needs_no_edit(self):
        rt = runtime()
        d = rt.decide("UnitSelect", unit(self.cards(220)))
        self.assertEqual((d.action, d.intent), ("click", "unit_next"), d.detail)

    def test_mid_edit_formation_opens_s3(self):
        rt = runtime()
        d = rt.decide("UnitSelect", unit(self.cards(176)))       # S3 2040180020 / S4 / guest still the old cards
        self.assertEqual((d.action, d.intent), ("click", "unit_slot_s3"), d.to_dict())

    def test_loading_formation_waits(self):
        rt = runtime()
        self.assertEqual(rt.decide("UnitSelect", unit(self.cards(85))).action, "wait")


LIVE_UNIT20 = ROOT / "dev_assets/private/wing-live-20261025/unit20-s4-pale.png"


@unittest.skipUnless(LIVE_UNIT20.is_file(), "private live frame 2026-10-25")
class LivePaleSlot(unittest.TestCase):
    def test_unit20_after_the_s4_pick(self):
        import numpy as np
        from PIL import Image
        from agent.core import wing_unit_check as wuc
        if wuc.card_index() is None:
            raise unittest.SkipTest("card index not available")
        cards = wuc.read_unit_cards(np.asarray(Image.open(LIVE_UNIT20).convert("RGB")))
        team = {"P": "1030010010", "S": ["2040050080", "2040250050", "2040040090", "2040080010"], "guest": "2050070010"}
        self.assertEqual(cards["S4"]["status"], "rejected")
        self.assertEqual(wuc.compare(cards, team)["slots"]["S4"], "uncertain")
        self.assertEqual(wuc.compare(cards, team, editing=True)["verdict"], "match")
        # Any other S card wanted in S4 stays unmatched.
        other = {**team, "S": team["S"][:3] + ["2040180020"]}
        self.assertNotEqual(wuc.compare(cards, other, editing=True)["verdict"], "match")


if __name__ == "__main__":
    unittest.main()
