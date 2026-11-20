"""W.I.N.G. input wiring: Python decides on the click frame, table-bound Pipeline Click nodes click.

Pure session tests (no Maa) for the decision round / act gate / spend grant, plus real-Maa offline replays of native
1280x720 recording frames through the supervised Wing plan (frame binder, action/node allowlist, spend authority) with
an ENABLED COPY of the action table. The shipped table keeps every wing-* action disabled. Zero game input.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from agent.core import pipeline_actions as pa
from agent.core import wing_custom as wcu
from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc

ROOT = Path(__file__).resolve().parent.parent
DEMO2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02"
DEMO3 = ROOT / "dev_assets/private/recordings/wing-demo3-20261006-1643-03"
PLAIN = {"start_confirm": {"layout": "plain", "stamina_before": 300, "stamina_after": 270, "multiplier": 1}}


def enabled_table():
    """The shipped table with every wing-* action enabled (what the live plan's enable step produces)."""
    from tools import wing_nav_build
    text = wing_nav_build.sync_action_table(pa.table_path().read_bytes().decode("utf-8"), enabled=True)
    return pa.parse_table(text.encode("utf-8"))


class FakeImage:
    def __init__(self, tag):
        self.tag = tag

    def tobytes(self):
        return self.tag.encode()

    def __getitem__(self, item):
        return self


class Authority:
    def __init__(self, refusal=None):
        self.calls, self.refusal = [], refusal

    def authorize(self, action, image, approver):
        self.calls.append((action, image.tag, approver))
        return self.refusal


class SessionTests(unittest.TestCase):
    """Decision round, act gate and spend grant of ``WingSession`` with patched screen evaluation and readers."""

    def session(self, readings=None, screen_ok=True, **kw):
        s = wcu.WingSession(**kw)
        s.backend_factory = lambda context: wsc.BoxBackend()
        self.readings = readings or {}
        patches = [mock.patch.object(wsc.Evaluator, "hit", return_value=screen_ok),
                   mock.patch.object(wsc, "read_screen", side_effect=lambda screen, ev, fr: dict(self.readings))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return s

    def begin(self, s, **run):
        s.begin({"run": run} if run else {})

    def test_timed_mode_requires_host_and_does_not_run_legacy_sleep(self):
        s = self.session(input_enabled=True)
        self.begin(s, live_timed_click=True)
        s.runtime.decide = lambda screen, readings: nav._click("live_appeal_timing")
        s.state({"want": "round", "screen": "Live"}, FakeImage("a"))
        with mock.patch.object(s, "_align_appeal") as legacy:
            self.assertIsNone(s.pick(None, FakeImage("a"), "Live", "live_appeal_timing"))
        legacy.assert_not_called()
        self.assertEqual(s.runtime.finished, "appeal_timing_host_unavailable")
        self.assertEqual(s.runtime.acts, 0)

    def test_unread_appeal_frame_is_reread_then_bounded(self):
        """2026-10-14 live a3: one APPEAL frame with a hidden cursor stopped the run although nothing was sent."""
        s = self.session(input_enabled=True)
        self.begin(s, live_timed_click=True)
        s.authority = mock.Mock(stage_appeal_timing=mock.Mock(return_value=("appeal_timing_unread", None)))
        s.runtime.decide = lambda screen, readings: nav._click("live_appeal_timing")
        for i in range(wcu.MAX_TIMING_REREADS):
            s.state({"want": "round", "screen": "Live"}, FakeImage(f"r{i}"))
            self.assertIsNone(s.pick(None, FakeImage(f"r{i}"), "Live", "live_appeal_timing"))
            self.assertIsNone(s.runtime.finished)
            self.assertEqual(s.round_decision[1].action, "requeue")
        s.state({"want": "round", "screen": "Live"}, FakeImage("last"))
        s.pick(None, FakeImage("last"), "Live", "live_appeal_timing")
        self.assertEqual(s.runtime.finished, "appeal_timing_unread")
        self.assertEqual(s.runtime.acts, 0)

    def test_unknown_screen_with_text_is_saved_once(self):
        import tempfile
        import numpy as np
        s = self.session(input_enabled=True)
        self.begin(s)
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        s.unknown_dir = tmp
        image = np.zeros((720, 1280, 3), np.uint8)
        digest = wcu._digest(image)
        s.ocr_cache[digest] = [("記者", (1, 2, 3, 4)), ("取材", (5, 6, 7, 8)), ("OK", (9, 9, 9, 9))]
        s._keep_unknown(image, digest)
        s._keep_unknown(image, digest)
        files = sorted(p.suffix for p in Path(tmp).rglob("*.*"))
        self.assertEqual(files, [".json", ".png"])
        blank = np.ones((720, 1280, 3), np.uint8)
        s.ocr_cache[wcu._digest(blank)] = [("x", (0, 0, 1, 1))]
        s._keep_unknown(blank, wcu._digest(blank))        # a fade/loading frame: too little text
        self.assertEqual(len(list(Path(tmp).rglob("*.png"))), 1)

    def test_launcher_only_removes_pre_delay_in_timed_mode(self):
        """2026-10-15: timed mode is the default (the sleep-aligned path landed Normal live)."""
        from tools.run_tasks import wing_plan
        self.assertEqual(wing_plan({"option": {}}).pipeline_override["SC.Wing.Click.Live.live_appeal_timing"],
                         {"pre_delay": 0})
        ordinary = wing_plan({"option": {"live_timed_click": False}})
        timed = wing_plan({"option": {"live_timed_click": True, "live_timing_event_offset_ms": 120}})
        node = "SC.Wing.Click.Live.live_appeal_timing"
        self.assertNotIn(node, ordinary.pipeline_override)
        self.assertEqual(timed.pipeline_override[node], {"pre_delay": 0})
        self.assertTrue(timed.pipeline_override["SC.Wing.Nav.Start"]["custom_action_param"]["run"]["live_timed_click"])

    def test_agentserver_session_never_sends(self):
        s = self.session()
        self.begin(s)
        self.assertTrue(s.state({"want": "round", "screen": "Hub"}, FakeImage("a")))
        self.assertIsNone(s.pick(None, FakeImage("a"), "Hub", "hub_schedule"))
        self.assertEqual(s.runtime.finished, "act_refused:live_input_disabled")
        self.assertTrue(s.state({"want": "held", "screen": "Hub"}, FakeImage("a")))
        self.assertTrue(s.state({"want": "halted"}, FakeImage("a")))
        self.assertEqual(s.runtime.acts, 0)
        # The AgentServer module builds its session without input authority (it cannot be imported outside an agent).
        source = (ROOT / "agent/custom/action/wing.py").read_text(encoding="utf-8")
        self.assertIn("SESSION = wc.WingSession()", source)
        self.assertNotIn("input_enabled=True", source)

    def test_one_decision_per_round_and_only_the_decided_intent_hits(self):
        s = self.session(input_enabled=True)
        self.begin(s)
        calls = []
        real = s.runtime.decide
        s.runtime.decide = lambda screen, r: calls.append(screen) or real(screen, r)
        s.state({"want": "round", "screen": "Hub"}, FakeImage("a"))
        self.assertIsNone(s.pick(None, FakeImage("a"), "Hub", "hub_rest"))
        hit = s.pick(None, FakeImage("a"), "Hub", "hub_schedule")
        self.assertEqual((hit.intent, len(calls), s.runtime.acts), ("hub_schedule", 1, 1))
        self.assertFalse(s.state({"want": "held", "screen": "Hub"}, FakeImage("a")))
        self.assertTrue(s.runtime.progress("other-digest"))
        # A re-polled next list on a new frame never reuses the round's decision.
        self.assertIsNone(s.pick(None, FakeImage("b"), "Hub", "hub_schedule"))
        self.assertEqual(s.runtime.finished, "decision_frame_changed")

    def test_screen_not_confirmed_requeues_boundedly(self):
        s = self.session(screen_ok=False, input_enabled=True)
        self.begin(s)
        for _ in range(wcu.MAX_REQUEUES):
            s.state({"want": "round", "screen": "Hub"}, FakeImage("a"))
            self.assertIsNone(s.pick(None, FakeImage("a"), "Hub", "hub_schedule"))
            self.assertFalse(s.state({"want": "held", "screen": "Hub"}, FakeImage("a")))
            self.assertTrue(s.state({"want": "requeue"}, FakeImage("a")))
        s.state({"want": "round", "screen": "Hub"}, FakeImage("a"))
        s.pick(None, FakeImage("a"), "Hub", "hub_schedule")
        self.assertFalse(s.state({"want": "requeue"}, FakeImage("a")))
        self.assertEqual(s.runtime.finished, "screen_unstable")

    def test_produce_start_needs_start_mode_spend_flag_grant_and_happens_once(self):
        authority = Authority()
        s = self.session(PLAIN, input_enabled=True, allow_start_spend=True, authority=authority)
        self.begin(s, mode="start", team_confirmed=True)
        s.state({"want": "round", "screen": "StartConfirm"}, FakeImage("a"))
        hit = s.pick(None, FakeImage("a"), "StartConfirm", "start_confirm_ok")
        self.assertEqual(hit.intent, "start_confirm_ok")
        self.assertEqual(authority.calls, [("wing-produce-start", "a", "SC.Wing.Decide")])
        s.state({"want": "round", "screen": "StartConfirm"}, FakeImage("b"))
        self.assertIsNone(s.pick(None, FakeImage("b"), "StartConfirm", "start_confirm_ok"))
        self.assertEqual(s.runtime.finished, "start_already_attempted")
        self.assertEqual(len(authority.calls), 1)

    def test_start_refusals(self):
        for kw, run, readings, reason in (
                ({}, {}, PLAIN, "spend_not_enabled"),                                    # attach (default)
                ({"allow_start_spend": False}, {"mode": "start", "team_confirmed": True}, PLAIN, "spend_not_enabled"),
                ({"allow_start_spend": True}, {"mode": "start", "team_confirmed": True},
                 {"start_confirm": {**PLAIN["start_confirm"], "layout": "items"}}, "start_confirm_layout_not_plain"),
                ({"allow_start_spend": True}, {"mode": "start", "team_confirmed": True},
                 {"start_confirm": {**PLAIN["start_confirm"], "multiplier": 2}}, "start_multiplier_not_1"),
                ({"allow_start_spend": True}, {"mode": "start", "team_confirmed": True},
                 {"start_confirm": {**PLAIN["start_confirm"], "stamina_after": 240}},
                 "start_stamina_unread_or_unexpected")):
            with self.subTest(reason=reason):
                authority = Authority()
                s = self.session(readings, input_enabled=True, authority=authority, **kw)
                self.begin(s, **run)
                s.state({"want": "round", "screen": "StartConfirm"}, FakeImage("a"))
                self.assertIsNone(s.pick(None, FakeImage("a"), "StartConfirm", "start_confirm_ok"))
                self.assertEqual((s.runtime.finished, authority.calls), (reason, []))
        authority = Authority(refusal="analysed_image_is_not_the_latest_frame")
        s = self.session(PLAIN, input_enabled=True, allow_start_spend=True, authority=authority)
        self.begin(s, mode="start", team_confirmed=True)
        s.state({"want": "round", "screen": "StartConfirm"}, FakeImage("a"))
        self.assertIsNone(s.pick(None, FakeImage("a"), "StartConfirm", "start_confirm_ok"))
        self.assertEqual(s.runtime.finished, "spend_grant_refused:analysed_image_is_not_the_latest_frame")
        self.assertTrue(s.runtime.memory.start_attempted)          # never retried, whatever the grant said

    def test_pipeline_parameters_cannot_grant_input(self):
        s = self.session(PLAIN)                                       # AgentServer-like: no authority
        with self.assertRaises(ValueError):
            s.begin({"run": {"input_enabled": True}})
        s.begin({"run": {"mode": "start", "team_confirmed": True}})
        self.assertFalse(s.runtime.options.input_enabled or s.runtime.options.allow_start_spend)

    def test_stop_and_finish_reports(self):
        from agent.core.task_actions import ReportSink
        sink = ReportSink()
        s = self.session(sink=sink, input_enabled=True)
        self.begin(s)
        s.runtime.stop("Schedule", "sp_learn_handover")
        self.assertFalse(s.stop({"screen": "Schedule", "reason": "no_decision"}))
        reports = sink.drain()
        self.assertEqual([r["kind"] for r in reports], ["wing", "fail"])
        self.assertEqual((reports[0]["outcome"], reports[1]["reason"]), ("sp_learn_handover", "wing:sp_learn_handover"))


class RunOptionTests(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            nav.RunOptions(allow_start_spend=True)                    # spend only in start mode
        with self.assertRaises(ValueError):
            nav.RunOptions.from_params({"mode": "auto"})
        with self.assertRaises(ValueError):
            nav.RunOptions.from_params({"allow_start_spend": True})   # never a Pipeline parameter
        with self.assertRaises(ValueError):
            nav.RunOptions(segment_acts=0)
        self.assertFalse(nav.RunOptions.from_params({"mode": "attach"}, allow_start_spend=True).allow_start_spend)

    def test_memory_snapshot_round_trip(self):
        m = nav.RunMemory(wins=[(1, 2000)], promise={"text": "一週間後にダンスレッスン"}, true_end_seen=True)
        data = json.loads(json.dumps(m.snapshot()))
        fresh = nav.RunMemory()
        fresh.restore(data)
        self.assertEqual((fresh.wins, fresh.promise, fresh.true_end_seen), ([(1, 2000)], m.promise, True))
        with self.assertRaises(ValueError):
            nav.RunMemory(strategy="da_score").restore(data)
        with self.assertRaises(ValueError):
            fresh.restore({**data, "pending": "rest"})


class WeekRuleTests(unittest.TestCase):
    def runtime(self, **options):
        rt = nav.WingNavRuntime(options=nav.RunOptions(**options))
        rt.begin()
        rt.memory.auditions_seen = {"key": [4, 6], "pages": [], "trend": None, "done": True}
        return rt

    def week(self, rt, decision):
        rt.overrides["week"] = lambda *a: decision
        return rt.decide("Schedule", {"season": 4, "weeks_left": 6, "stats": {"Vo": 300}, "fans": 1000, "sp": 40,
                                      "rooms": [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo"}],
                                      "selected_room": None})

    def test_sp_request_is_handed_over_once_then_continues_after_the_user(self):
        info = {"kind": "lesson", "option_id": "lesson_vo", "pre_actions": ["spend_sp"]}
        self.assertEqual(self.week(self.runtime(), info).reason, "sp_learn_handover")
        done = self.runtime(sp_done=True)
        self.assertEqual(self.week(done, info).intent, "schedule_room_1")
        self.assertEqual(self.week(done, info).reason, "sp_learn_handover")       # only one request is pre-handled
        self.assertEqual(self.week(self.runtime(sp_mode="skip"), info).intent, "schedule_room_1")
        self.assertEqual(self.week(self.runtime(sp_mode="skip"), {"kind": "spend_sp"}).reason,
                         "sp_still_requested_after_handover")

    def test_final_stage_hands_over_sp_once_per_stage(self):
        rt = self.runtime()
        rt.memory.last_sp = 80
        self.assertEqual(rt.decide("HubSemiFinal").reason, "sp_learn_handover")
        resumed = self.runtime(sp_done=True)
        resumed.memory.last_sp = 80
        self.assertEqual(resumed.decide("HubSemiFinal").intent, "hub_final_live")
        self.assertEqual(resumed.decide("HubFinal").reason, "sp_learn_handover")
        zero = self.runtime()
        zero.memory.last_sp = 0
        self.assertEqual(zero.decide("HubFinal").intent, "hub_final_live")

    def test_segment_boundary_only_at_a_week_start(self):
        rt = self.runtime(segment_acts=2)
        rt.acts = 2
        self.assertEqual(rt.decide("CommuSkip").intent, "commu_skip")            # mid-week: keep going
        self.assertEqual(rt.decide("Hub").reason, "segment_budget_reached")
        rt.memory.pending = "rest"                                               # a planned step is never cut
        self.assertEqual(rt.decide("Hub").intent, "hub_rest")

    def test_act_gate(self):
        rt = nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, max_acts=1))
        rt.begin()
        self.assertEqual(rt.act("Hub", nav._click("hub_schedule")), (True, "sent"))
        self.assertEqual(rt.act("Hub", nav._click("hub_rest")), (False, "act_budget_exhausted"))
        self.assertEqual(rt.act("Hub", nav._click("commu_skip")), (False, "intent_not_bound"))
        unverified = nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, mode="start"))
        unverified.begin()
        with mock.patch.dict(nav.TARGETS, {"unit_page_prev": (nav.TARGETS["unit_page_prev"][0], False)}):
            self.assertEqual(unverified.act("UnitSelect", nav._click("unit_page_prev")), (False, "target_not_verified"))


def _frames_present(*paths):
    return all(Path(p).is_file() for p in paths)


@unittest.skipUnless(_frames_present(DEMO2 / "frame-001224.png", DEMO3 / "frame-000373.png"),
                     "private W.I.N.G. recordings not present")
class OfflineMaaReplayTests(unittest.TestCase):
    """Real Maa OCR + Pipeline on native frames, enabled table copy, Wing plan through ``run_wing_offline``."""

    SHORT_AFTER = {"SC.Wing.Click." + r.screen + "." + i: {"timeout": 2500}
                   for r in nav.SCREENS if r.kind in ("fixed", "decide") for i in nav.screen_intents(r.screen)}

    def run_frames(self, steps, option, allow_spend=()):
        from tools import run_tasks as launcher
        images, advances = launcher.load_frames([(Path(p), a) for p, a in steps])
        table = enabled_table()
        with mock.patch.object(pa, "_DEFAULT", table):
            return launcher.run_wing_offline(images, advances, option, allow_spend=allow_spend,
                                             extra_override=self.SHORT_AFTER, timeout_s=300.0)

    def test_attach_week_start_clicks_through_hub_schedule_and_planned_audition(self):
        # #1262 (the tab while it slides in) reads as ScheduleBusy -> Maa waits there; #1263 is the settled tab.
        # 2026-10-09 live: the week decision takes the audition list from the static table (no survey) and the
        # tab's page already shows the planned 40k audition -> 決定 (SP skipped here; auto/handover tested elsewhere).
        # The recorded frames cannot follow a preview-check room tap (#1258: Vo room with a support, preview unread),
        # so the check is switched off here; tests/test_wing_room_preview.py covers it.
        with mock.patch.object(nav, "PREVIEW_CHECK_MAX", 0):
            out = self.run_frames([(DEMO2 / "frame-001224.png", "click"), (DEMO2 / "frame-001258.png", "click"),
                                   (DEMO2 / "frame-001263.png", "click")],
                                  {"live_mode": "game_auto", "sp_mode": "skip"})
        scenario, session = out["scenario"], out["session"]
        self.assertEqual(scenario.actions[:3], ["wing-hub-schedule", "wing-schedule-audition-tab",
                                                "wing-schedule-decide"], session.runtime.log[-12:])
        self.assertEqual(scenario.spends, [])
        # The last frame never changes: the click's result is not observed -> stop, no retry.
        self.assertEqual(session.runtime.finished, "clickUnconfirmed", session.runtime.log[-6:])
        self.assertLessEqual(len(scenario.actions), 4)
        self.assertEqual(out["result"]["status"], "failed")
        kinds = [r["kind"] for r in out["result"]["reports"]]
        self.assertIn("wing", kinds)

    def test_attach_never_presses_produce_start(self):
        out = self.run_frames([(DEMO3 / "frame-000373.png", "click")], {})
        self.assertEqual((out["scenario"].actions, out["session"].runtime.finished), ([], "spend_not_enabled"))

    def test_start_mode_presses_produce_start_once_with_a_grant(self):
        out = self.run_frames([(DEMO3 / "frame-000373.png", "click"), (DEMO2 / "frame-001224.png", "click")],
                              {"mode": "start", "team_confirmed": True, "segment_acts": 1},
                              allow_spend=("wing-produce-start",))
        scenario, session = out["scenario"], out["session"]
        self.assertEqual(scenario.actions, ["wing-produce-start"], session.runtime.log[-8:])
        self.assertEqual(scenario.spends, [("wing-produce-start", "sent")])
        self.assertEqual(session.runtime.finished, "segment_budget_reached")


if __name__ == "__main__":
    unittest.main()
