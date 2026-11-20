"""In-game オプション → プロデュース設定 (agent/core/wing_options.py; 2026-10-13 check, 2026-10-14 adaptive): record
status (complete + fresh, either value), the gate, Maa's own read pass (open → read → wheel → read → ←), the adaptive
rest / live / warning-popup behaviour, the safety properties (never a toggle / 保存; dialogs and unknown frames stop) and
native replays of the supervised recording captures-20261011-164549Z (#294 Hub, #299 top / #303 middle / #306 bottom of
the list, #310 Hub after ←)."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import wing_nav as nav
from agent.core import wing_options as wo
from agent.core import wing_screen as wsc

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "dev_assets/private/captures-20261011-164549Z"
OCR = ROOT / "dev_assets/private/wing-demo4-analysis/ocr.jsonl"
GOOD = {"idol_touch": "ON", "unread_ff_release": "ON", "cutin_ff_release": "OFF", "rest_confirm": "ON",
        "live_confirm": "ON", "wing_warning_popup": "OFF"}
NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
READ_PATH_VERIFIED = {i: (nav.TARGETS[i][0], True) for i in wo.READ_INTENTS.values()}
# Page regions Maa must never touch: the ON/OFF toggles, 初期設定に戻す and 保存 (#299).
FORBIDDEN = ((865, 100, 330, 500), (890, 630, 170, 70), (1090, 625, 165, 75))


def record(values=None, days_ago=0.0, **extra):
    stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    values = dict(GOOD if values is None else values)
    return {"values": values, "seen_at": {k: stamp for k in values}, **extra}


def runtime(rec=None, **options):
    rt = nav.WingNavRuntime(options=nav.RunOptions(**options))
    rt.settings = wo.SettingsGate(rec)
    rt.begin()
    return rt


def sent(rt, screen, d):
    """Run the Agent gate like SC.Wing.Act (input enabled) and return the After guard's accepted screens."""
    ok, why = rt.act(screen, d)
    assert ok, why
    return rt.expect_screens(nav.RULES[screen].expect)


def overlaps(a, b):
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


class RecordStatus(unittest.TestCase):
    def test_status_requires_complete_fresh_reading_of_either_value(self):
        self.assertTrue(wo.status(record())["ok"])
        flipped = {**GOOD, "rest_confirm": "OFF", "live_confirm": "OFF", "wing_warning_popup": "ON"}
        st = wo.status(record(flipped))
        self.assertTrue(st["ok"])                                   # adaptive: any value is accepted
        self.assertEqual(st["values"], {"rest_confirm": "OFF", "live_confirm": "OFF", "wing_warning_popup": "ON"})
        self.assertEqual(wo.status(None)["missing"], ["rest_confirm", "live_confirm", "wing_warning_popup"])
        self.assertFalse(wo.status(record(stale="x"))["ok"])
        # informational rows never block
        self.assertTrue(wo.status(record({k: GOOD[k] for k in wo.DEPENDENT}))["ok"])

    def test_expiry(self):
        self.assertTrue(wo.status(record(days_ago=6.9))["ok"])
        st = wo.status(record(days_ago=7.1))
        self.assertEqual((st["ok"], st["expired"]), (False, list(wo.DEPENDENT)))
        st = wo.status({"values": GOOD, "seen_at": {"rest_confirm": "garbage"}}, NOW)
        self.assertEqual(st["expired"], list(wo.DEPENDENT))       # unparsable / missing stamps are not fresh
        self.assertTrue(wo.status({"values": GOOD, "seen_at": dict.fromkeys(GOOD, "2026-10-11T16:50:45+00:00")},
                                  NOW)["ok"])

    def test_value_falls_back_to_recording(self):
        self.assertEqual({k: wo.value(None, k) for k in wo.DEPENDENT}, wo.ASSUMED)
        self.assertEqual(wo.value(record({**GOOD, "rest_confirm": "OFF"}), "rest_confirm"), "OFF")

    def test_stale_cleared_only_after_every_dependent_row_reread(self):
        g = wo.SettingsGate(record())
        g.invalidate("rest_confirm", "OFF", "休む后没有确认画面")
        self.assertFalse(g.status()["ok"])
        g.observe({"rest_confirm": "OFF", "live_confirm": "ON"})
        self.assertEqual(g.status()["stale"], "休む后没有确认画面")
        g.observe({"wing_warning_popup": "OFF"})
        self.assertTrue(g.status()["ok"])


class Gate(unittest.TestCase):
    def test_no_settings_no_gate(self):
        rt = nav.WingNavRuntime()
        rt.begin()
        self.assertEqual(rt.decide("Hub", {}).intent, "hub_schedule")

    def test_read_path_verified_live(self):
        # 2026-10-14 live (wing-attach1): open -> read -> wheel x2 -> read -> ← back to Hub.
        self.assertTrue(nav.TARGETS["hub_options"][1])
        self.assertTrue(nav.TARGETS["options_scroll_down"][1])
        self.assertTrue(nav.TARGETS["options_back"][1])
        self.assertTrue(wo.read_path_ready(nav.TARGETS))
        disabled = dict(nav.TARGETS, options_back=(nav.TARGETS["options_back"][0], False))
        self.assertFalse(wo.read_path_ready(disabled))

    def test_without_read_path_the_gate_stops_before_the_first_decision(self):
        import unittest.mock as um
        # Without a verified read path (e.g. a target revoked) the gate cannot read the page itself.
        with um.patch.dict(nav.TARGETS, options_back=(nav.TARGETS["options_back"][0], False)):
            d = runtime().decide("Home", {})
            self.assertEqual((d.action, d.reason), ("stop", "produce_settings_unverified"))
            self.assertIn("オプション → プロデュース設定", d.detail["text"])
            self.assertEqual(runtime(record(stale="休む后没有确认画面")).decide("Hub", {}).reason,
                             "produce_settings_stale")
            d = runtime(record(days_ago=8)).decide("Hub", {})
            self.assertEqual(d.reason, "produce_settings_expired")
            self.assertIn("超过 7 天", d.detail["text"])
        # With the live-verified read path a stale record is re-read from the Hub instead.
        self.assertEqual(runtime(record(stale="x")).decide("Hub", {}).intent, "hub_options")

    def test_fresh_record_passes_once_with_any_value(self):
        for values in (GOOD, {**GOOD, "rest_confirm": "OFF", "live_confirm": "OFF", "wing_warning_popup": "ON"}):
            rt = runtime(record(values))
            self.assertEqual(rt.decide("Hub", {}).intent, "hub_schedule")
            self.assertEqual(rt.decide("Hub", {}).intent, "hub_schedule")
            self.assertEqual(sum(e["event"] == "produce_settings_ok" for e in rt.log), 1)

    def test_user_opened_page_updates_record_and_stops(self):
        rt = runtime()
        top = {k: GOOD[k] for k in ("idol_touch", "unread_ff_release", "cutin_ff_release", "rest_confirm")}
        d = rt.decide("Options", {"produce_options": {**dict.fromkeys(wo.ROWS), **top}})
        self.assertEqual(d.reason, "produce_settings_incomplete")
        self.assertTrue(rt.settings.dirty)
        rt2 = runtime(rt.settings.record)            # next run, user scrolled to the bottom
        d = rt2.decide("Options", {"produce_options": {"live_confirm": "OFF", "wing_warning_popup": "ON"}})
        self.assertEqual(d.reason, "produce_settings_checked")
        self.assertTrue(runtime(rt2.settings.record).settings.status()["ok"])
        self.assertEqual(rt2.settings.value("live_confirm"), "OFF")
        self.assertEqual(runtime(rt2.settings.record).decide("Options", {"options_dialog": "変更を保存しますか"}).reason,
                         "options_dialog")

    def test_store_round_trip_and_session(self):
        from agent.core.wing_custom import WingSession
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "produce-settings.json"
            session = WingSession(settings=path)
            session.begin({})
            self.assertEqual(session.runtime.settings.status()["missing"], list(wo.REQUIRED))
            session.runtime.settings.observe(GOOD)
            session._save_settings()
            self.assertTrue(wo.status(json.loads(path.read_text(encoding="utf-8")))["ok"])
            again = WingSession(settings=path)
            again.begin({})
            self.assertTrue(again.runtime.settings.status()["ok"])
            path.write_text("[]", encoding="utf-8")
            broken = WingSession(settings=path)
            broken.begin({})
            self.assertFalse(broken.runtime.settings.status()["ok"])
        self.assertIsNone(WingSession().runtime.settings)


@mock.patch.dict(nav.TARGETS, READ_PATH_VERIFIED)
class ReadPath(unittest.TestCase):
    """The read pass with its targets marked verified (as after the supervised live sample)."""

    def page(self, tops, values):
        return {"produce_options_rows": tops, "produce_options": {**dict.fromkeys(wo.ROWS), **values},
                "options_dialog": None}

    TOP = ({"idol_touch": 157, "unread_ff_release": 280, "cutin_ff_release": 414, "rest_confirm": 562},
           {k: GOOD[k] for k in ("idol_touch", "unread_ff_release", "cutin_ff_release", "rest_confirm")})
    MID = ({"unread_ff_release": 137, "cutin_ff_release": 271, "rest_confirm": 422, "live_confirm": 557},
           {"unread_ff_release": "ON", "cutin_ff_release": "OFF", "rest_confirm": "ON", "live_confirm": "OFF"})
    BOTTOM = ({"rest_confirm": 234, "live_confirm": 369, "wing_warning_popup": 504},
              {"rest_confirm": "ON", "live_confirm": "OFF", "wing_warning_popup": "ON"})

    def test_sequence_open_read_scroll_read_back(self):
        rt = runtime(input_enabled=True)
        self.assertNotEqual(rt.decide("Home", {}).reason, "produce_settings_unverified")   # deferred to the Hub
        d = rt.decide("Hub", {})
        self.assertEqual((d.intent, d.reason), ("hub_options", "produce_settings_read"))
        self.assertEqual(sent(rt, "Hub", d), {"Options", "Loading"})
        self.assertEqual(rt.decide("Loading", {}).action, "wait")
        trail = []
        for tops, values in (self.TOP, self.TOP, self.MID, self.MID, self.BOTTOM, self.BOTTOM):
            d = rt.decide("Options", self.page(tops, values))
            trail.append(d.intent or d.action)
            if d.action == "click":
                after = sent(rt, "Options", d)
                self.assertEqual(after, {"Options"} if d.intent == "options_scroll_down" else {"Hub", "HubBanner",
                                                                                             "Loading"})
        self.assertEqual(trail, ["wait", "options_scroll_down", "wait", "options_scroll_down", "wait", "options_back"])
        self.assertEqual(rt.settings.status()["values"],
                         {"rest_confirm": "ON", "live_confirm": "OFF", "wing_warning_popup": "ON"})
        self.assertEqual(rt.settings.record["source"], "options_read_path")
        self.assertTrue(rt.settings.dirty)
        d = rt.decide("Hub", {})
        self.assertEqual(d.intent, "hub_schedule")                  # gate passed with the values just read
        self.assertTrue(rt.settings.passed)

    def test_opened_once_per_run(self):
        rt = runtime()
        self.assertEqual(rt.decide("Hub", {}).intent, "hub_options")
        self.assertEqual(rt.decide("Hub", {}).reason, "produce_settings_unverified")   # back without a reading
        rt = runtime()
        self.assertEqual(rt.decide("HubFinal", {}).reason, "produce_settings_unverified")  # only the Hub opens it

    def test_never_toggles_or_saves(self):
        # The settings fix (RunOptions.settings_fix) adds its own intents; a read pass never returns them.
        self.assertEqual(set(nav.screen_intents("Options")),
                         {"options_scroll_down", "options_back", *wo.FIX_INTENTS.values(), wo.SAVE_INTENT,
                          wo.SAVED_CLOSE_INTENT})
        for intent in ("options_scroll_down", "options_back"):
            for region in FORBIDDEN:
                self.assertFalse(overlaps(nav.TARGETS[intent][0], region), (intent, region))
        self.assertIn("options_scroll_down", nav.WHEEL_INTENTS)        # wheel, no button press
        # every decision of a read pass is one of the read-only intents, whatever the readings
        rt = runtime()
        rt.decide("Hub", {})
        for tops, values in (self.TOP, self.TOP) + ((self.MID[0], {"live_confirm": "ON"}),) * 2:
            d = rt.decide("Options", self.page(tops, values))
            self.assertIn(d.intent, (None, "options_scroll_down", "options_back"))

    def test_dialog_stops(self):
        rt = runtime()
        rt.decide("Hub", {})
        d = rt.decide("Options", {**self.page(*self.TOP), "options_dialog": "変更を保存しますか？"})
        self.assertEqual((d.action, d.reason), ("stop", "options_dialog"))
        self.assertIsNotNone(wo.dialog_text([("変更を保存しますか？", (500, 300, 200, 30))]))
        self.assertIsNone(wo.dialog_text([("休む選択時に確認画面を表示", (465, 562, 296, 26))]))

    def test_list_end_without_rows_stops(self):
        rt = runtime()
        rt.decide("Hub", {})
        tops, values = self.TOP
        self.assertEqual(rt.decide("Options", self.page(tops, values)).action, "wait")
        self.assertEqual(rt.decide("Options", self.page(tops, values)).intent, "options_scroll_down")
        self.assertEqual(rt.decide("Options", self.page(tops, values)).action, "wait")
        d = rt.decide("Options", self.page(tops, values))           # the wheel did not move the list
        self.assertEqual((d.action, d.reason), ("stop", "options_rows_unread"))

    def test_unsettled_or_wrong_tab_stops(self):
        rt = runtime()
        rt.decide("Hub", {})
        for _ in range(wo.OPTIONS_SETTLE_WAITS):
            self.assertEqual(rt.decide("Options", self.page({}, {})).action, "wait")
        self.assertEqual(rt.decide("Options", self.page({}, {})).reason, "options_rows_unread")

    def test_scroll_budget(self):
        rt = runtime()
        rt.decide("Hub", {})
        for i in range(wo.OPTIONS_MAX_SCROLLS):
            tops = {"idol_touch": 157 - i}
            self.assertEqual(rt.decide("Options", self.page(tops, {})).action, "wait")
            self.assertEqual(rt.decide("Options", self.page(tops, {})).intent, "options_scroll_down")
        tops = {"idol_touch": 100}
        rt.decide("Options", self.page(tops, {}))
        self.assertEqual(rt.decide("Options", self.page(tops, {})).reason, "options_scroll_budget")

    def test_options_after_back_stops(self):
        rt = runtime()
        rt.decide("Hub", {})
        for tops, values in (self.BOTTOM, self.BOTTOM):
            d = rt.decide("Options", self.page(tops, values))
        self.assertEqual(d.intent, "options_back")
        self.assertEqual(rt.decide("Options", self.page(*self.BOTTOM)).reason, "options_back_unconfirmed")

    def test_pipeline_click_nodes(self):
        nodes = wsc.load_nodes()
        self.assertEqual(nodes["SC.Wing.Read.Options.List"]["roi"], list(wo.LIST_ROI))
        self.assertEqual(nodes["SC.Wing.Read.Options.Dialog"]["roi"], list(wo.DIALOG_ROI))
        clicks = sorted(n for n in nodes if n.startswith("SC.Wing.Click.Options."))
        self.assertEqual(clicks, sorted("SC.Wing.Click.Options." + i for i in nav.screen_intents("Options")))
        self.assertIn("SC.Wing.Click.Hub.hub_options", nodes)
        table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
        by_id = {a["id"]: a for a in table["actions"]}
        self.assertTrue(by_id["wing-options-scroll-down"]["enabled"])
        self.assertTrue(by_id["wing-options-back"]["enabled"])
        self.assertEqual(by_id["wing-options-scroll-down"]["wheel"], wo.OPTIONS_SCROLL_WHEEL)
        self.assertFalse(any(a["consuming"] for k, a in by_id.items() if "options" in k))
        for intent in (*wo.FIX_INTENTS.values(), wo.SAVE_INTENT):     # verified in the supervised run 2026-10-25
            self.assertTrue(by_id[nav.action_id(intent)]["enabled"], intent)
            self.assertTrue(nav.TARGETS[intent][1], intent)


class AdaptiveBehaviour(unittest.TestCase):
    def rest_runtime(self, rest_confirm):
        rt = runtime(record({**GOOD, "rest_confirm": rest_confirm}), input_enabled=True)
        rt.memory.pending, rt.memory.rest_key = "rest", [1, 5]
        return rt

    def test_rest_confirm_on(self):
        rt = self.rest_runtime("ON")
        d = rt.decide("Hub", {"season": 1, "weeks_left": 5})
        self.assertEqual((d.intent, d.reason), ("hub_rest", "pending_rest"))
        self.assertIn("RestConfirm", sent(rt, "Hub", d))
        self.assertNotIn("HubBanner", rt.expect_screens(nav.RULES["Hub"].expect))
        self.assertEqual(rt.decide("RestConfirm", {}).intent, "dialog_ok")
        self.assertIsNone(rt.memory.pending)

    def test_rest_confirm_off_marks_done_on_progress_and_never_rests_twice(self):
        rt = self.rest_runtime("OFF")
        d = rt.decide("Hub", {"season": 1, "weeks_left": 5})
        self.assertEqual((d.intent, d.reason), ("hub_rest", "pending_rest_no_confirm"))
        after = sent(rt, "Hub", d)
        self.assertTrue({"HubBanner", "ScheduleBusy", "WeekCard", "RestConfirm"} <= after)
        self.assertEqual(rt.decide("HubBanner", {}).action, "wait")
        self.assertIsNone(rt.memory.pending)                        # rest animation / banner proved it
        self.assertEqual(rt.decide("Hub", {"season": 1, "weeks_left": 4}).intent, "hub_schedule")
        # week counter as the proof
        rt = self.rest_runtime("OFF")
        rt.decide("Hub", {"season": 1, "weeks_left": 5})
        self.assertEqual(rt.decide("Hub", {"season": 1, "weeks_left": 4}).intent, "hub_schedule")
        self.assertTrue(any(e["event"] == "rest_done" for e in rt.log))
        # no progress: stop, never a second 休む
        rt = self.rest_runtime("OFF")
        rt.decide("Hub", {"season": 1, "weeks_left": 5})
        d = rt.decide("Hub", {"season": 1, "weeks_left": 5})
        self.assertEqual((d.action, d.reason), ("stop", "rest_unconfirmed"))
        # an unread week cannot prove the rest -> no confirm-less tap at all
        rt = self.rest_runtime("OFF")
        self.assertEqual(rt.decide("Hub", {}).reason, "rest_week_unread")

    def test_record_contradicted_by_the_game(self):
        rt = self.rest_runtime("ON")                       # recorded ON, the game rested without 確認画面
        rt.decide("Hub", {"season": 1, "weeks_left": 5})
        self.assertEqual(rt.decide("Hub", {"season": 1, "weeks_left": 4}).intent, "hub_schedule")
        self.assertEqual((rt.settings.record["values"]["rest_confirm"], rt.settings.status()["ok"]), ("OFF", False))
        rt = self.rest_runtime("OFF")                      # recorded OFF, the 確認画面 showed anyway
        rt.decide("Hub", {"season": 1, "weeks_left": 5})
        self.assertEqual(rt.decide("RestConfirm", {}).intent, "dialog_ok")
        self.assertEqual(rt.settings.record["values"]["rest_confirm"], "ON")
        self.assertIsNone(rt.memory.rest_sent)
        self.assertTrue(rt.settings.dirty)

    def test_live_confirm(self):
        for value, live in (("ON", False), ("OFF", True)):
            rt = runtime(record({**GOOD, "live_confirm": value}), input_enabled=True)
            rt.memory.final_sp_checked, rt.memory.last_sp = "HubFinal", 0
            d = rt.decide("HubFinal", {})
            self.assertEqual(d.intent, "hub_final_live")
            after = sent(rt, "HubFinal", d)
            self.assertIn("LiveConfirm", after)                     # still accepted if it shows
            self.assertEqual("Live" in after, live, value)
        self.assertEqual(nav.WingNavRuntime().decide("LiveConfirm", {}).intent, "dialog_ok")

    def test_warning_popup(self):
        on = runtime(record({**GOOD, "wing_warning_popup": "ON"}))
        self.assertEqual(on.explain_stop("Hub", "lost"), "wing_warning_popup_unsampled")
        self.assertEqual(on.explain_stop("Schedule", "uncertain"), "wing_warning_popup_unsampled")
        self.assertEqual(on.explain_stop("Commu", "lost"), "lost")           # outside its context
        self.assertEqual(on.explain_stop("Hub", "act_refused:x"), "act_refused:x")
        self.assertEqual(runtime(record()).explain_stop("Hub", "lost"), "lost")
        # 2026-10-15 live: an unsent click (nothing reached the game) is not the popup
        on.log.append({"event": "stop", "screen": None, "reason": "click_not_sent_resumable:skill_info_close"})
        self.assertEqual(on.explain_stop("Review", "clickUnconfirmed"), "clickUnconfirmed")
        self.assertIn("此弹窗还没采样", wo.WARNING_POPUP_TEXT)
        # Sampled live 2026-10-14: the fan-shortfall variant -> 「オーディションへ」 only (the 振り返り variant stays unknown).
        rule = nav.RULES[wo.WARNING_POPUP_SCREEN]
        self.assertEqual((rule.kind, rule.intent), ("fixed", "warning_to_audition"))
        self.assertTrue(nav.TARGETS["warning_to_audition"][1])
        nodes = wsc.load_nodes()
        self.assertEqual(nodes["SC.Wing.Anchor.WingWarning.Title"]["expected"], ["ちょっと待って"])

    def test_session_stop_names_the_popup(self):
        from agent.core.wing_custom import WingSession
        session = WingSession()
        session.begin({})
        session.runtime.settings = wo.SettingsGate(record({**GOOD, "wing_warning_popup": "ON"}))
        session.round_screen = "Hub"
        session.stop({"reason": "uncertain"})
        self.assertEqual(session.runtime.finished, "wing_warning_popup_unsampled")

    def test_report_carries_the_stop_text_for_the_ui(self):
        # UX review #12: the wing report names the Chinese detail.text next to the outcome code; the host
        # (UiTaskResult.WingReason) shows "code：text". A new run starts without the old text.
        from agent.core.wing_custom import WingSession

        class Sink:
            def __init__(self):
                self.rows = []

            def add(self, kind, payload):
                self.rows.append((kind, payload))
        sink = Sink()
        session = WingSession(sink=sink)
        session.begin({})
        session.runtime.stop_text = "缺少：体力回復の演出"
        session.runtime.stop("Hub", "produce_settings_unverified")
        session.report()
        wing = [p for k, p in sink.rows if k == "wing"][0]
        self.assertEqual((wing["outcome"], wing["stop_text"]), ("produce_settings_unverified", "缺少：体力回復の演出"))
        session.runtime.begin()
        self.assertIsNone(session.runtime.stop_text)


@unittest.skipUnless(REC.is_dir() and OCR.is_file(), "private recording 2026-10-11 and its OCR")
class RecordingReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.wing_demo_replay import load_rgb
        cls.texts = {}
        for line in OCR.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cls.texts[row["seq"]] = [(t, tuple(b)) for t, b in row["texts"]]
        cls.load = staticmethod(lambda n: load_rgb(REC / f"frame-{n:06d}.png"))
        cls.classifier = wsc.ScreenClassifier(customs={"SC.Wing.State": lambda p, f: True})

    def read(self, n, image=None):
        frame = wsc.Frame(self.texts[n], (lambda: image) if image is not None else (lambda: self.load(n)), key=str(n))
        screen = self.classifier.classify(frame).screen
        return screen, wsc.read_screen(screen, self.classifier.evaluator, frame)

    def test_recorded_settings(self):
        expected = {299: {"idol_touch": "ON", "unread_ff_release": "ON", "cutin_ff_release": "OFF",
                          "rest_confirm": "ON"},
                    303: {"unread_ff_release": "ON", "cutin_ff_release": "OFF", "rest_confirm": "ON",
                          "live_confirm": "ON"},
                    306: {"rest_confirm": "ON", "live_confirm": "ON", "wing_warning_popup": "OFF"}}
        rt = runtime()
        for n, want in expected.items():
            screen, r = self.read(n)
            self.assertEqual(screen, "Options", n)
            self.assertEqual({k: v for k, v in r["produce_options"].items() if v is not None}, want, n)
            self.assertEqual(set(r["produce_options_rows"]), set(want), n)
            self.assertIsNone(r["options_dialog"], n)
            d = rt.decide(screen, r)
        self.assertEqual(d.reason, "produce_settings_checked")
        self.assertEqual(rt.settings.record["values"], GOOD)

    @mock.patch.dict(nav.TARGETS, READ_PATH_VERIFIED)
    def test_read_path_replay(self):
        """#294 Hub → オプション → #299 (twice: settle) → wheel → #303 ×2 → wheel → #306 ×2 → ← → #310 Hub."""
        rt = runtime(input_enabled=True)
        screen, r = self.read(294)
        self.assertEqual(screen, "Hub")
        d = rt.decide(screen, r)
        self.assertEqual(d.intent, "hub_options")
        self.assertEqual(sent(rt, screen, d), {"Options", "Loading"})
        for n in (296, 298):
            self.assertEqual(self.read(n)[0], "Loading", n)
        trail = []
        for n in (299, 299, 303, 303, 306, 306):
            screen, r = self.read(n)
            self.assertEqual(screen, "Options", n)
            d = rt.decide(screen, r)
            trail.append((n, d.intent or d.action))
            if d.action == "click":
                sent(rt, screen, d)
        self.assertEqual(trail, [(299, "wait"), (299, "options_scroll_down"), (303, "wait"),
                                 (303, "options_scroll_down"), (306, "wait"), (306, "options_back")])
        self.assertEqual({k: rt.settings.record["values"][k] for k in GOOD}, GOOD)
        screen, r = self.read(310)
        self.assertIn(screen, rt.expect_screens(nav.RULES["Options"].expect))
        d = rt.decide(screen, r)
        self.assertEqual(d.action, "click")
        self.assertNotIn(d.intent, ("hub_options",))
        self.assertTrue(rt.settings.passed)

    def test_flipped_warning_toggle_is_read_and_accepted(self):
        import numpy as np
        image = np.array(self.load(306))
        on, off = image[:, 865:1029].copy(), image[:, 1031:1195].copy()
        image[:, 865:1029], image[:, 1031:1195] = off, on          # draw ON as the selected half on every row
        screen, r = self.read(306, image)
        self.assertEqual(r["produce_options"]["wing_warning_popup"], "ON")
        rt = runtime(record())
        d = rt.decide(screen, r)
        self.assertEqual(d.reason, "produce_settings_checked")
        self.assertEqual(rt.settings.value("wing_warning_popup"), "ON")

    def test_other_pages_read_nothing(self):
        from agent.core.wing_screen import box_in_roi
        for n in (294, 239):                        # Hub / ユニット確認
            texts = [tb for tb in self.texts[n] if box_in_roi(tb[1], wo.LIST_ROI)]
            self.assertEqual(set(wo.read_toggles(texts, self.load(n)).values()), {None}, n)

    def test_hub_options_rect_on_the_icon(self):
        (x, y, w, h), verified = nav.TARGETS["hub_options"]
        self.assertTrue(verified)
        self.assertTrue(x <= 1204 < x + w and y <= 231 < y + h)          # the user's tap before #296
        label = next(b for t, b in self.texts[294] if t in ("オブション", "オプション"))   # label under the icon
        self.assertLess(y + h, label[1] + label[3] + 5)
        bx, by, bw, bh = nav.TARGETS["options_back"][0]
        self.assertTrue(bx <= 64 < bx + bw and by <= 666 < by + bh)        # the user's ← before #310

    def test_seed_tool(self):
        from tools import wing_options_record as tool
        self.assertEqual(tool.read_frames(REC, OCR, {299, 303, 306}), GOOD)


if __name__ == "__main__":
    unittest.main()
