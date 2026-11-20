"""W.I.N.G. 「自动改游戏设置」 (RunOptions.settings_fix): on the first Hub open オプション, tap OFF on
未読イベント早送り解除 / 警告ポップアップ when they read ON (only at the measured row positions), 保存, open the page again
and read them (verify), keep the values. Synthetic readings for the state machine, rect geometry from the recording, and
native replays of captures-20261011-164549Z (#294 Hub, #299 / #303 / #306 list, #310 Hub) with the toggles redrawn."""
from __future__ import annotations

import unittest
from unittest import mock

from agent.core import produce_config as pc
from agent.core import wing_nav as nav
from agent.core import wing_options as wo
from agent.core import wing_screen as wsc
from tests.test_wing_options import GOOD, OCR, REC, ROOT, overlaps, record, runtime, sent

FIX_INTENTS = (*wo.FIX_INTENTS.values(), wo.SAVE_INTENT, wo.SAVED_CLOSE_INTENT)
FIX_VERIFIED = {i: (nav.TARGETS[i][0], True) for i in (*wo.READ_INTENTS.values(), *FIX_INTENTS)}
READ_ONLY_VERIFIED = {i: (nav.TARGETS[i][0], True) for i in wo.READ_INTENTS.values()}
BEFORE = {**GOOD, "wing_warning_popup": "ON"}          # unread ON (the 10-11 value), popup ON (the 10-25 record)


def page(view, **override):
    tops, values = view
    return {"produce_options_rows": dict(tops), "produce_options": {**dict.fromkeys(wo.ROWS), **values, **override},
            "options_dialog": None, "options_saved": False}


# 「設定を保存しました。」 over the list end (live 2026-10-25 wing10 seg2): rows dimmed (no value), one label readable.
SAVED = {"produce_options_rows": {"wing_warning_popup": 505}, "produce_options": dict.fromkeys(wo.ROWS),
         "options_dialog": None, "options_saved": True}


TOP = ({"idol_touch": 157, "unread_ff_release": 280, "cutin_ff_release": 414, "rest_confirm": 562},
       {"idol_touch": "ON", "unread_ff_release": "ON", "cutin_ff_release": "OFF", "rest_confirm": "ON"})
MID = ({"unread_ff_release": 137, "cutin_ff_release": 271, "rest_confirm": 422, "live_confirm": 557},
       {"unread_ff_release": "ON", "cutin_ff_release": "OFF", "rest_confirm": "ON", "live_confirm": "ON"})
BOTTOM = ({"rest_confirm": 234, "live_confirm": 369, "wing_warning_popup": 504},
          {"rest_confirm": "ON", "live_confirm": "ON", "wing_warning_popup": "ON"})
OFF = {"unread_ff_release": "OFF"}
ALL_OFF = {"unread_ff_release": "OFF", "wing_warning_popup": "OFF"}


def view(base, values):
    """``base`` with the given rows' values replaced where that row is on screen."""
    tops, old = base
    return tops, {**old, **{k: v for k, v in values.items() if k in tops}}


class Walk:
    """Drive the runtime over (screen, reading) steps like SC.Wing.Act (input enabled); collect intents/actions."""

    def __init__(self, test, rt):
        self.test, self.rt, self.trail, self.after = test, rt, [], []

    def __call__(self, screen, reading=None):
        d = self.rt.decide(screen, reading or {})
        self.trail.append(d.intent or d.reason if d.action != "wait" else "wait")
        if d.action == "click":
            self.after.append((d.intent, sent(self.rt, screen, d)))
        return d

    def options(self, *views):
        for v in views:
            for _ in range(2):                       # two agreeing frames per reading
                d = self("Options", page(v))
        return d


@mock.patch.dict(nav.TARGETS, FIX_VERIFIED)
class FixFlow(unittest.TestCase):
    def run_fix(self, rec=None):
        rt = runtime(record(BEFORE) if rec is None else rec, input_enabled=True, settings_fix=True)
        return rt, Walk(self, rt)

    def test_fix_then_verify_then_keep(self):
        rt, walk = self.run_fix()
        d = walk("Hub")
        self.assertEqual((d.intent, d.reason), ("hub_options", "produce_settings_fix"))
        walk.options(TOP)                                            # unread ON at the list top -> OFF
        walk.options(view(TOP, OFF), view(MID, OFF), BOTTOM)          # popup ON at the list end -> OFF
        self.assertEqual(rt.settings.record["values"]["unread_ff_release"], "ON")   # not recorded before verify
        walk.options(view(BOTTOM, ALL_OFF))                          # both OFF -> 保存
        self.assertEqual(rt.settings.record["stale"], "settings_fix_saved")
        d = walk("Options", SAVED)                                   # 「設定を保存しました。」 -> 閉じる
        self.assertEqual((d.intent, d.reason), ("options_saved_close", "options_saved_close"))
        walk.options(view(BOTTOM, ALL_OFF))                          # dialog gone, same values -> ←
        d = walk("Hub")
        self.assertEqual((d.intent, d.reason), ("hub_options", "produce_settings_fix_verify"))
        walk.options(view(TOP, ALL_OFF), view(MID, ALL_OFF), view(BOTTOM, ALL_OFF))
        d = walk("Hub")
        self.assertEqual(d.intent, "hub_schedule")                   # the run goes on with the kept values
        clicks = [i for i, _ in walk.after]
        self.assertEqual(clicks, ["hub_options", "options_unread_off", "options_scroll_down", "options_scroll_down",
                                  "options_popup_off", "options_save", "options_saved_close", "options_back",
                                  "hub_options", "options_scroll_down", "options_scroll_down", "options_back",
                                  "hub_schedule"])
        after = dict(walk.after[:8])
        self.assertEqual(after["options_unread_off"], {"Options"})
        self.assertEqual(after["options_popup_off"], {"Options"})
        self.assertEqual(after["options_save"], {"Options"})          # the game stays on the page (live 10-25)
        self.assertEqual(after["options_saved_close"], {"Options"})
        g = rt.settings
        self.assertTrue(g.passed)
        self.assertEqual({k: g.record["values"][k] for k in wo.FIX_VALUES}, wo.FIX_VALUES)
        self.assertIsNone(g.record["stale"])
        self.assertEqual(g.record["source"], "options_fix_verify")
        self.assertEqual(g.record["fixed"]["changed"], ["unread_ff_release", "wing_warning_popup"])
        self.assertEqual(rt.setting("wing_warning_popup"), "OFF")    # the navigator adapts to the kept value
        self.assertTrue(wo.fix_satisfied(g.record))
        self.assertEqual(rt.memory.options_fix["phase"], "verified")

    def test_screens_before_the_first_hub_do_not_skip_the_fix(self):
        # Live 2026-10-25 (wing10): a start run decides on Home / setup screens first; the gate passed there and the
        # fix never ran on the Hub.
        rt, walk = self.run_fix()
        for screen in ("Home", "ProduceSelect"):
            rt.decide(screen, {})
        self.assertFalse(rt.settings.passed)
        d = walk("Hub")
        self.assertEqual((d.intent, d.reason), ("hub_options", "produce_settings_fix"))

    def test_page_already_off_changes_nothing(self):
        rt, walk = self.run_fix(record(GOOD))                        # the record still says unread ON
        walk("Hub")
        walk.options(view(TOP, OFF), view(MID, OFF), view(BOTTOM, ALL_OFF))
        self.assertEqual(walk("Hub").intent, "hub_schedule")
        self.assertFalse(set(FIX_INTENTS) & {i for i, _ in walk.after})
        self.assertEqual(rt.memory.options_fix["phase"], "kept")
        self.assertEqual(rt.settings.record["values"]["unread_ff_release"], "OFF")

    def test_fresh_record_already_off_does_not_open_the_page(self):
        rt, walk = self.run_fix(record({**GOOD, **ALL_OFF}))
        self.assertEqual(walk("Hub").intent, "hub_schedule")
        self.assertEqual(rt.memory.options_fix["phase"], "kept")
        stale = record({**GOOD, **ALL_OFF}, stale="rest_confirm_missing")
        self.assertFalse(wo.fix_satisfied(stale))
        self.assertFalse(wo.fix_satisfied(record({**GOOD, **ALL_OFF}, days_ago=8)))

    def test_switch_off_or_stage_hub_never_fixes(self):
        rt = runtime(record(BEFORE), input_enabled=True)             # settings_fix off: read-only behaviour
        self.assertEqual(Walk(self, rt)("Hub").intent, "hub_schedule")
        rt, walk = self.run_fix()
        d = walk("HubFinal")
        self.assertNotIn(d.intent, ("hub_options",))
        self.assertEqual(rt.memory.options_fix["phase"], "skipped")

    def test_toggle_that_does_not_flip_stops(self):
        rt, walk = self.run_fix()
        walk("Hub")
        walk.options(TOP)
        d = walk.options(TOP)                                        # still ON after the OFF tap
        self.assertEqual((d.action, d.reason), ("stop", "options_toggle_unconfirmed"))
        rt, walk = self.run_fix()
        walk("Hub")
        walk.options(TOP)
        d = walk.options(view(TOP, {**OFF, "idol_touch": "OFF"}))   # another row changed too
        self.assertEqual((d.action, d.reason), ("stop", "options_toggle_unconfirmed"))

    def test_rows_away_from_the_measured_position_are_not_tapped(self):
        rt, walk = self.run_fix()
        walk("Hub")
        tops, values = TOP
        d = walk.options(({**tops, "unread_ff_release": 300}, values))
        self.assertEqual((d.action, d.reason), ("stop", "options_row_moved"))
        rt, walk = self.run_fix(record({**BEFORE, **OFF}))
        walk("Hub")
        walk.options(view(TOP, OFF), view(MID, OFF))
        low = ({**BOTTOM[0], "wing_warning_popup": 480}, BOTTOM[1])
        walk.options(low)                                            # popup above the list end: wheel on
        d = walk.options(low)                                        # the list did not move: the end
        self.assertEqual((d.action, d.reason), ("stop", "options_row_moved"))
        self.assertFalse(set(FIX_INTENTS) & {i for i, _ in walk.after})

    def test_dialog_after_save_stops_and_leaves_the_record_stale(self):
        rt, walk = self.run_fix(record({**BEFORE, **OFF}))
        walk("Hub")
        walk.options(view(TOP, OFF), view(MID, OFF), BOTTOM, view(BOTTOM, ALL_OFF))
        self.assertEqual(walk.after[-1][0], "options_save")
        d = walk("Options", {**page(view(BOTTOM, ALL_OFF)), "options_dialog": "保存しました"})
        self.assertEqual((d.action, d.reason), ("stop", "options_dialog"))
        self.assertEqual(rt.settings.record["stale"], "settings_fix_saved")
        self.assertFalse(wo.status(rt.settings.record)["ok"])        # the next run re-reads the page

    def saved_pass(self):
        rt, walk = self.run_fix(record({**BEFORE, **OFF}))
        walk("Hub")
        walk.options(view(TOP, OFF), view(MID, OFF), BOTTOM, view(BOTTOM, ALL_OFF))
        self.assertEqual(walk.after[-1][0], "options_save")
        return rt, walk

    def test_save_without_the_saved_dialog_stops(self):
        rt, walk = self.saved_pass()
        waits = 0
        while (d := walk("Options", page(view(BOTTOM, ALL_OFF)))).action == "wait":
            waits += 1
            self.assertLessEqual(waits, wo.OPTIONS_SETTLE_WAITS + 1)    # one settle frame + the save waits
        self.assertEqual((d.action, d.reason), ("stop", "options_save_unconfirmed"))
        self.assertNotIn("options_back", [i for i, _ in walk.after])

    def test_saved_dialog_is_closed_once(self):
        rt, walk = self.saved_pass()
        self.assertEqual(walk("Options", SAVED).intent, "options_saved_close")
        for _ in range(wo.OPTIONS_SETTLE_WAITS):                     # still fading out: wait, never a 2nd tap
            self.assertEqual(walk("Options", SAVED).action, "wait")
        d = walk("Options", SAVED)
        self.assertEqual((d.action, d.reason), ("stop", "options_saved_close_unconfirmed"))
        self.assertEqual([i for i, _ in walk.after].count("options_saved_close"), 1)

    def test_values_changed_under_the_saved_dialog_stop(self):
        rt, walk = self.saved_pass()
        walk("Options", SAVED)
        d = walk.options(view(BOTTOM, OFF))                         # popup reads ON after 閉じる
        self.assertEqual((d.action, d.reason), ("stop", "options_value_changed"))

    def test_saved_dialog_outside_a_save_stops(self):
        rt, walk = self.run_fix()
        walk("Hub")
        d = walk("Options", SAVED)                                   # Maa did not tap 保存 in this pass
        self.assertEqual((d.action, d.reason), ("stop", "options_dialog"))
        rt = runtime(record(BEFORE), input_enabled=True)             # a page the user opened
        d = rt.decide("Options", SAVED)
        self.assertEqual((d.action, d.reason), ("stop", "options_dialog"))

    def test_save_that_returns_to_the_hub_is_verified(self):
        rt, walk = self.run_fix(record({**BEFORE, **OFF}))
        walk("Hub")
        walk.options(view(TOP, OFF), view(MID, OFF), BOTTOM, view(BOTTOM, ALL_OFF))
        d = walk("Hub")                                              # the game closed the page itself
        self.assertEqual((d.intent, d.reason), ("hub_options", "produce_settings_fix_verify"))

    def test_verify_that_reads_on_stops(self):
        rt, walk = self.run_fix()
        walk("Hub")
        walk.options(TOP, view(TOP, OFF), view(MID, OFF), BOTTOM, view(BOTTOM, ALL_OFF))
        walk("Options", SAVED)
        walk.options(view(BOTTOM, ALL_OFF))
        walk("Hub")
        d = walk.options(view(TOP, ALL_OFF), view(MID, ALL_OFF), view(BOTTOM, OFF))   # popup back ON
        self.assertEqual((d.action, d.reason), ("stop", "options_fix_not_kept"))

    def test_unexpected_hub_mid_pass_stops(self):
        rt, walk = self.run_fix()
        walk("Hub")
        walk.options(TOP)                                            # OFF tap sent
        d = walk("Hub")
        self.assertEqual((d.action, d.reason), ("stop", "produce_settings_fix_incomplete"))

    def test_value_changing_during_a_read_stops(self):
        rt, walk = self.run_fix()
        walk("Hub")
        walk.options(view(TOP, OFF))
        d = walk.options(view(MID, {"rest_confirm": "OFF", **OFF}))
        self.assertEqual((d.action, d.reason), ("stop", "options_value_changed"))


UNVERIFIED = {**READ_ONLY_VERIFIED, **{i: (nav.TARGETS[i][0], False) for i in FIX_INTENTS}}


class Unverified(unittest.TestCase):
    @mock.patch.dict(nav.TARGETS, UNVERIFIED)
    def test_unverified_fix_targets_are_never_attempted(self):
        self.assertFalse(wo.fix_path_ready(nav.TARGETS))
        rt = runtime(record(BEFORE), input_enabled=True, settings_fix=True)
        d = rt.decide("Hub", {})
        self.assertEqual(d.intent, "hub_schedule")
        self.assertEqual(rt.memory.options_fix["phase"], "unavailable")
        self.assertIn("produce_settings_fix_unavailable", [e.get("event") for e in rt.log])

    @mock.patch.dict(nav.TARGETS, UNVERIFIED)
    def test_act_gate_refuses_the_unverified_targets(self):
        rt = runtime(record(BEFORE), input_enabled=True, settings_fix=True)
        for intent in FIX_INTENTS:
            ok, why = rt.act("Options", nav._click(intent, "test"))
            self.assertEqual((ok, why), (False, "target_not_verified"), intent)

    def test_fix_targets_verified_live(self):
        # 2026-10-25 supervised run (wing10 seg2): both OFF taps read back OFF, 保存 answered 「設定を保存しました。」.
        self.assertTrue(wo.fix_path_ready(nav.TARGETS))


class Geometry(unittest.TestCase):
    """Rects against the recording's buttons (#299 list top, #306 list end): inside the row's OFF half / 保存, clear of
    the ON halves, 初期設定に戻す and the neighbouring rows."""
    OFF_HALF_X = (1030, 1195)
    ON_HALF = (865, 100, 164, 500)
    RESET = (890, 625, 170, 75)                                      # 初期設定に戻す
    SAVE = (1090, 625, 162, 75)
    TOGGLE_Y = {"options_unread_off": (275, 340), "options_popup_off": (487, 550)}

    def test_toggle_rects(self):
        for intent, (y0, y1) in self.TOGGLE_Y.items():
            x, y, w, h = nav.TARGETS[intent][0]
            self.assertTrue(self.OFF_HALF_X[0] + 20 <= x and x + w <= self.OFF_HALF_X[1] - 20, intent)
            self.assertTrue(y0 + 10 <= y and y + h <= y1 - 10, intent)
            self.assertFalse(overlaps((x, y, w, h), self.ON_HALF), intent)
        key = {v: k for k, v in wo.FIX_INTENTS.items()}
        for intent in self.TOGGLE_Y:                                 # the rect follows the row's measured label top
            y = nav.TARGETS[intent][0][1]
            self.assertTrue(0 <= y - wo.FIX_ROW_TOP[key[intent]] + wo.FIX_ROW_TOLERANCE <= 40, intent)

    def test_save_rect(self):
        x, y, w, h = nav.TARGETS["options_save"][0]
        sx, sy, sw, sh = self.SAVE
        self.assertTrue(sx + 12 <= x and x + w <= sx + sw - 12 and sy + 12 <= y and y + h <= sy + sh - 12)
        self.assertGreaterEqual(x - (self.RESET[0] + self.RESET[2]), 40)
        self.assertTrue(x <= 1133 + 37 < x + w and y <= 640 + 20 < y + h)   # the 保存 label centre

    def test_saved_close_rect(self):
        x, y, w, h = nav.TARGETS["options_saved_close"][0]
        bx, by, bw, bh = (558, 403, 164, 75)                         # 閉じる, live frame 20261025T035257995279Z
        self.assertTrue(bx + 20 <= x and x + w <= bx + bw - 20 and by + 12 <= y and y + h <= by + bh - 12)

    def test_only_the_two_rows_and_save(self):
        self.assertEqual(set(wo.FIX_VALUES), {"unread_ff_release", "wing_warning_popup"})
        self.assertEqual(set(wo.FIX_VALUES.values()), {"OFF"})
        self.assertNotIn("idol_touch", wo.FIX_VALUES)                # stays until a tap fast lane exists


class Plumbing(unittest.TestCase):
    def test_option_reaches_the_run(self):
        self.assertTrue(pc.launch_options({"settings_fix": True})["settings_fix"])
        self.assertFalse(pc.launch_options({})["settings_fix"])
        with self.assertRaises(ValueError):
            pc.launch_options({"settings_fix": "Yes"})
        self.assertTrue(nav.RunOptions.from_params({"settings_fix": True}).settings_fix)
        self.assertFalse(nav.RunOptions().settings_fix)
        from tools import run_tasks
        params, options = run_tasks.wing_options({"settings_fix": True})
        self.assertEqual((params["run"]["settings_fix"], options.settings_fix), (True, True))
        self.assertFalse(run_tasks.wing_options({})[1].settings_fix)

    def test_ui_switch_defaults_off(self):
        import json
        from pathlib import Path
        tasks = json.loads((Path(__file__).resolve().parent.parent / "assets/tasks/auto_produce.json")
                           .read_text(encoding="utf-8"))
        opt = tasks["option"]["Produce改游戏设置"]
        self.assertEqual((opt["type"], opt["default_case"]), ("switch", "No"))
        self.assertIn("Produce改游戏设置", tasks["task"][0]["option"])
        node = {c["name"]: c["pipeline_override"]["SC.Produce.Config.SettingsFix"]["attach"] for c in opt["cases"]}
        self.assertEqual(node, {"Yes": {"settings_fix": True}, "No": {"settings_fix": False}})


@unittest.skipUnless(REC.is_dir() and OCR.is_file(), "private recording 2026-10-11 and its OCR")
@mock.patch.dict(nav.TARGETS, FIX_VERIFIED)
class RecordingReplay(unittest.TestCase):
    """The recording's frames with a toggle redrawn (ON/OFF halves swapped in that row) stand in for the frames after
    a tap; every reading goes through the native classifier and the Options readers."""

    @classmethod
    def setUpClass(cls):
        import json
        from tools.wing_demo_replay import load_rgb
        cls.texts = {}
        for line in OCR.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cls.texts[row["seq"]] = [(t, tuple(b)) for t, b in row["texts"]]
        cls.rgb = {n: load_rgb(REC / f"frame-{n:06d}.png") for n in (294, 299, 303, 306, 310)}
        cls.classifier = wsc.ScreenClassifier(customs={"SC.Wing.State": lambda p, f: True})

    def frame(self, n, flip=()):
        import numpy as np
        image = np.array(self.rgb[n])
        tops = wo.row_tops(self.texts[n])
        for key in flip:
            y0, y1 = max(0, tops[key] - 20), tops[key] + 64
            on, off = image[y0:y1, 865:1029].copy(), image[y0:y1, 1031:1195].copy()
            image[y0:y1, 865:1029], image[y0:y1, 1031:1195] = off, on
        return image

    def read(self, n, flip=()):
        image = self.frame(n, flip)
        frame = wsc.Frame(self.texts[n], lambda: image, key=f"{n}:{','.join(flip)}")
        screen = self.classifier.classify(frame).screen
        return screen, wsc.read_screen(screen, self.classifier.evaluator, frame), image

    def pink_in(self, image, rect):
        x, y, w, h = rect
        return wo._pink_share(image, (x, x + w), y, y + h)

    def test_fix_and_verify_on_the_recorded_pages(self):
        rt = runtime(record(GOOD), input_enabled=True, settings_fix=True)
        walk = Walk(self, rt)
        screen, r, _ = self.read(294)
        self.assertEqual(walk(screen, r).intent, "hub_options")
        rect = nav.TARGETS["options_unread_off"][0]
        plan = [(299, ()), (299, ()), (299, ("unread_ff_release",)), (299, ("unread_ff_release",)),
                (303, ("unread_ff_release",)), (303, ("unread_ff_release",)), (306, ()), (306, ()), ("saved", ()),
                (306, ()), (306, ())]
        for n, flip in plan:
            if n == "saved":                                         # the recording has no 保存; see LiveSavedDialog
                walk("Options", SAVED)
                continue
            screen, r, image = self.read(n, flip)
            self.assertEqual(screen, "Options", (n, flip))
            d = walk(screen, r)
            if d.intent == "options_unread_off":
                # The tap frame: the rect lies on the unselected OFF half of the unread row ...
                self.assertLess(self.pink_in(image, rect), 0.05)
                # ... which is the selected (pink) half once the row reads OFF.
                self.assertGreater(self.pink_in(self.frame(299, ("unread_ff_release",)), rect), 0.5)
        self.assertEqual([i for i, _ in walk.after], ["hub_options", "options_unread_off", "options_scroll_down",
                                                      "options_scroll_down", "options_save", "options_saved_close",
                                                      "options_back"])
        screen, r, _ = self.read(310)
        self.assertEqual(walk(screen, r).intent, "hub_options")          # verify pass
        for n, flip in [(299, ("unread_ff_release",))] * 2 + [(303, ("unread_ff_release",))] * 2 + [(306, ())] * 2:
            screen, r, _ = self.read(n, flip)
            walk(screen, r)
        self.assertEqual(walk.after[-1][0], "options_back")
        screen, r, _ = self.read(310)
        d = walk(screen, r)
        self.assertNotEqual(d.intent, "hub_options")
        self.assertTrue(rt.settings.passed)
        self.assertEqual({k: rt.settings.record["values"][k] for k in wo.FIX_VALUES}, wo.FIX_VALUES)

    def test_popup_toggle_rect_on_the_recorded_list_end(self):
        rect = nav.TARGETS["options_popup_off"][0]
        screen, r, image = self.read(306, ("wing_warning_popup",))
        self.assertEqual((screen, r["produce_options"]["wing_warning_popup"]), ("Options", "ON"))
        self.assertEqual(r["produce_options_rows"]["wing_warning_popup"], wo.FIX_ROW_TOP["wing_warning_popup"])
        self.assertLess(self.pink_in(image, rect), 0.05)              # on the OFF half, not selected
        self.assertGreater(self.pink_in(self.rgb[306], rect), 0.5)    # the recorded OFF half (selected)
        state = wo.new_read("fix")
        state["seen"], state["values"] = ["unread_ff_release"], {"unread_ff_release": "OFF"}
        reading = {"tops": r["produce_options_rows"], "values": r["produce_options"], "dialog": r["options_dialog"]}
        wo.read_step(state, reading)
        step = wo.read_step(state, reading)
        self.assertEqual((step["action"], step["key"]), ("toggle", "wing_warning_popup"))

    def test_unread_row_top_matches_the_measured_position(self):
        self.assertEqual(wo.row_tops(self.texts[299])["unread_ff_release"], wo.FIX_ROW_TOP["unread_ff_release"])
        self.assertEqual(wo.row_tops(self.texts[306])["wing_warning_popup"], wo.FIX_ROW_TOP["wing_warning_popup"])
        save = next(b for t, b in self.texts[299] if t == "保存")
        x, y, w, h = nav.TARGETS["options_save"][0]
        cx, cy = save[0] + save[2] / 2, save[1] + save[3] / 2
        self.assertTrue(x <= cx < x + w and y <= cy < y + h)


SAVED_PNG = ROOT / "dev_assets/private/wing_samples/2026-10-25/20261025T035257995279Z-capture.png"


@unittest.skipUnless(SAVED_PNG.is_file(), "private live frame 2026-10-25 (「設定を保存しました。」)")
@mock.patch.dict(nav.TARGETS, FIX_VERIFIED)
class LiveSavedDialog(unittest.TestCase):
    """The live frame after Maa's 保存 (wing10 seg2): whole-frame project OCR, native classifier and Options readers."""

    @classmethod
    def setUpClass(cls):
        from tools.explore_step import FrameOcr
        from tools.wing_demo_replay import load_rgb
        cls.texts = [(d["text"], tuple(d["box"])) for d in FrameOcr().texts(SAVED_PNG)]
        image = load_rgb(SAVED_PNG)
        frame = wsc.Frame(cls.texts, lambda: image, key="saved")
        classifier = wsc.ScreenClassifier(customs={"SC.Wing.State": lambda p, f: True})
        cls.screen = classifier.classify(frame).screen
        cls.reading = wsc.read_screen(cls.screen, classifier.evaluator, frame)

    def test_reads_as_the_saved_dialog_on_the_options_page(self):
        self.assertEqual(self.screen, "Options")
        self.assertTrue(self.reading["options_saved"])
        self.assertIsNone(self.reading["options_dialog"])            # not a question Maa would have to answer

    def test_close_target_on_the_button_label(self):
        box = next(b for t, b in self.texts if wo._clean(t) == "閉じる")
        x, y, w, h = nav.TARGETS["options_saved_close"][0]
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        self.assertTrue(x <= cx < x + w and y <= cy < y + h)

    def test_saving_pass_closes_it(self):
        state = wo.new_read("fix")
        state.update(phase="saving", tapped=["unread_ff_release", "wing_warning_popup"], values=dict(ALL_OFF))
        step = wo.read_step(state, {"tops": self.reading["produce_options_rows"],
                                    "values": self.reading["produce_options"],
                                    "dialog": self.reading["options_dialog"], "saved": self.reading["options_saved"]})
        self.assertEqual(step["action"], "close")


if __name__ == "__main__":
    unittest.main()
