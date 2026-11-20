"""W.I.N.G. live seg6–8 (2026-10-09/10) replays: a click is only "sent" when the controller history proves it, an
unsent click is re-decided once from the pre-decision state, a too-slow decision is re-made on a fresh frame, an
unconfirmed 習得する is retried once when nothing was spent, and an unfinished SP learning chain survives a segment
boundary. Pure session/runtime tests, zero game input."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import wing_custom as wcu
from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc
from tests.test_wing_live_wiring import PLAIN, FakeImage
from tests.test_wing_sp_learning import INFO, review, runtime

C08 = "skill_node_c08_r06"


class History:
    """Launcher spend-authority stand-in with the controller click history (``click_mark`` / ``clicks_since``)."""

    def __init__(self):
        self.entries = []

    def click_mark(self):
        return len(self.entries)

    def clicks_since(self, mark):
        clicks = self.entries[mark:]
        if all(e in ("denied", "refused_unattempted") for e in clicks):
            return "not_sent"
        return "sent" if "sent" in clicks else "unknown"

    def authorize(self, action, image, approver):
        return None


def learn_ready(rt):
    """Runtime on 振り返り with the P skill selected and its catalog read (seg8 just before 習得する)."""
    rt.memory.pending = "spend_sp"
    rt.memory.sp_session = {"key": [1, 4], "sp_start": 138, "taps": 7, "learned": [], "skipped": [], "catalog": {
        k: {**v, "attributes": list(v["attributes"]), "state": "learnable", "source": "p_card" if k == C08 else "unknown"}
        for k, v in INFO.items()}, "last_tap": C08, "learning": None, "owned": "read", "stats": None, "caps": None,
        "reentered": False, "unstable": 0}
    return rt


class Clock:
    def __init__(self):
        self.t, self.step = 0.0, 0.0

    def __call__(self):
        self.t += self.step
        return self.t


class SessionOutcomeTests(unittest.TestCase):
    def session(self, readings, history=None, clock=None, **kw):
        s = wcu.WingSession(input_enabled=True, authority=history, clock=clock or Clock(), **kw)
        s.backend_factory = lambda context: wsc.BoxBackend()
        self.readings = readings
        for p in (mock.patch.object(wsc.Evaluator, "hit", return_value=True),
                  mock.patch.object(wsc, "read_screen", side_effect=lambda screen, ev, fr: dict(self.readings))):
            p.start()
            self.addCleanup(p.stop)
        s.begin({})
        rt = learn_ready(runtime())
        rt.options = nav.RunOptions(sp_mode="auto", input_enabled=True, sp_small_view=False)
        s.runtime = rt
        return s

    def round(self, s, screen, tag, intent):
        self.assertTrue(s.state({"want": "round", "screen": screen}, FakeImage(tag)))
        return s.pick(None, FakeImage(tag), screen, intent)

    def test_seg8_frame_stale_learn_click_is_rolled_back_and_redecided_once(self):
        history = History()
        s = self.session(review(138, selected=C08), history)
        hit = self.round(s, "Review", "a", "skill_learn")
        self.assertEqual((hit.intent, s.runtime.acts), ("skill_learn", 1))
        self.assertIsNotNone(s.runtime.memory.sp_session["learning"])
        act = [e for e in s.runtime.log if e.get("event") == "act"][-1]
        self.assertNotIn("sent", act)                              # approval is not a send
        # The frame guard refused the permit (no controller entry); the And failed and Hold.Review polled next.
        self.assertTrue(s.state({"want": "active", "screen": "Review", "wait": True}, FakeImage("a")))
        self.assertIsNone(s.runtime.memory.sp_session["learning"])
        self.assertEqual(s.runtime.acts, 0)
        self.assertIn({"event": "act_result", "screen": "Review", "intent": "skill_learn", "sent": "not_sent"},
                      s.runtime.log)
        # The next round re-decides the same learn from the restored state; this time the tap is sent.
        self.assertEqual(self.round(s, "Review", "b", "skill_learn").intent, "skill_learn")
        history.entries.append("sent")
        self.assertTrue(s.state({"want": "expect", "screens": ["Review"]}, FakeImage("c")) in (True, False))
        self.assertEqual(s.runtime.memory.sp_session["learning"]["sp_before"], 138)
        self.assertEqual(s.runtime.acts, 1)
        self.assertTrue(s.runtime.is_active())

    def test_unsent_twice_in_a_row_stops(self):
        s = self.session(review(138, selected=C08), History())
        self.round(s, "Review", "a", "skill_learn")
        s.state({"want": "requeue"}, FakeImage("a"))
        self.round(s, "Review", "b", "skill_learn")
        self.assertFalse(s.state({"want": "requeue"}, FakeImage("b")))
        self.assertEqual(s.runtime.finished, "click_not_sent_again:skill_learn")

    def test_seg7_controller_denied_click_ends_resumable_with_rolled_back_memory(self):
        history = History()
        s = self.session(review(138), history)
        s.runtime.memory.sp_session.update(owned="todo", last_tap=None, catalog={})
        self.assertEqual(self.round(s, "Review", "a", "skill_owned").intent, "skill_owned")
        self.assertEqual(s.runtime.memory.sp_session["owned"], "opened")
        history.entries.append("denied")                           # host binding changed (manual mouse move)
        self.assertFalse(s.stop({"reason": "clickUnconfirmed"}))
        self.assertEqual(s.runtime.finished, "click_not_sent_resumable:skill_owned")
        self.assertEqual(s.runtime.memory.sp_session["owned"], "todo")

    def test_sent_click_keeps_its_effects_and_unknown_never_rolls_back(self):
        for entries, finished in ((["sent"], "clickUnconfirmed"), (["refused_attempted"], "clickUnconfirmed")):
            history = History()
            s = self.session(review(138, selected=C08), history)
            self.round(s, "Review", "a", "skill_learn")
            history.entries.extend(entries)
            s.stop({"reason": "clickUnconfirmed"})
            self.assertEqual(s.runtime.finished, finished)
            self.assertIsNotNone(s.runtime.memory.sp_session["learning"])
        # No controller history (AgentServer / tests without authority): unknown, nothing restored.
        s = self.session(review(138, selected=C08), None)
        self.round(s, "Review", "a", "skill_learn")
        s.state({"want": "requeue"}, FakeImage("a"))
        self.assertIsNotNone(s.runtime.memory.sp_session["learning"])

    def test_consuming_click_is_never_redecided(self):
        history = History()
        s = self.session(PLAIN, history, allow_start_spend=True)
        s.runtime = nav.WingNavRuntime(options=nav.RunOptions(mode="start", team_confirmed=True, input_enabled=True,
                                                              allow_start_spend=True))
        s.runtime.begin()
        self.assertEqual(self.round(s, "StartConfirm", "a", "start_confirm_ok").intent, "start_confirm_ok")
        self.assertFalse(s.state({"want": "requeue"}, FakeImage("a")))
        self.assertEqual(s.runtime.finished, "click_not_sent:start_confirm_ok")
        self.assertTrue(s.runtime.memory.start_attempted)

    def test_slow_decision_is_remade_on_a_fresh_frame(self):
        clock = Clock()
        s = self.session(review(138, selected=C08), History(), clock=clock)
        clock.step = 4.0                                           # 4 s per clock read -> decision > 3.5 s
        self.assertTrue(s.state({"want": "round", "screen": "Review"}, FakeImage("a")))
        self.assertIsNone(s.pick(None, FakeImage("a"), "Review", "skill_learn"))
        self.assertIsNone(s.runtime.memory.sp_session["learning"])
        self.assertEqual(s.runtime.acts, 0)
        self.assertFalse(s.state({"want": "held", "screen": "Review"}, FakeImage("a")))
        self.assertTrue(s.state({"want": "requeue"}, FakeImage("a")))
        clock.step = 0.0
        self.assertEqual(self.round(s, "Review", "b", "skill_learn").intent, "skill_learn")

    def test_decision_age_counts_from_the_frame_not_the_decision_start(self):
        """2026-10-23 round audit: guards/Picks before the decision already spend part of the 5 s frame permit."""
        clock = Clock()
        s = self.session(review(138, selected=C08), History(), clock=clock)
        self.assertTrue(s.state({"want": "round", "screen": "Review"}, FakeImage("a")))   # frame first seen at 0 s
        clock.t = 3.6                                              # a fast decision, but 3.6 s after the capture
        self.assertIsNone(s.pick(None, FakeImage("a"), "Review", "skill_learn"))
        stale = [e for e in s.runtime.log if e.get("event") == "decision_stale"]
        self.assertEqual((stale[-1]["seconds"], stale[-1]["frame_seconds"]), (0.0, 3.6))
        self.assertEqual(s.runtime.acts, 0)
        self.assertTrue(s.state({"want": "requeue"}, FakeImage("a")))
        self.assertEqual(self.round(s, "Review", "b", "skill_learn").intent, "skill_learn")   # fresh frame: decided

    def test_a_requeue_restarts_the_frame_clock_on_an_unchanged_screen(self):
        """2026-10-25 live (wing4): 振り返り stayed identical, so every capture had the same digest and the age of each
        re-decision kept growing (4.9 s ... 62 s) until decision_too_slow stopped three segments."""
        clock = Clock()
        s = self.session(review(138, selected=C08), History(), clock=clock)
        self.assertTrue(s.state({"want": "round", "screen": "Review"}, FakeImage("a")))
        clock.t = 3.6
        self.assertIsNone(s.pick(None, FakeImage("a"), "Review", "skill_learn"))   # stale: re-decide
        self.assertTrue(s.state({"want": "requeue"}, FakeImage("a")))
        clock.t = 6.0                                              # next round: a new capture of the same view
        self.assertEqual(self.round(s, "Review", "a", "skill_learn").intent, "skill_learn")

    def test_a_resumed_inspect_tap_without_an_info_bar_is_dropped(self):
        """2026-10-25 live (wing6): the saved inspect tap had used up its waits; the resumed segment stopped at once
        (sp_info_unread) on a default-view panel without an info bar."""
        rt = learn_ready(runtime())
        rt.options = nav.RunOptions(sp_mode="auto", input_enabled=True, sp_small_view=False)
        rt.memory.sp_session.update(unstable=nav.SP_UNSTABLE_WAITS + 1, reentered=True, zoom_attempted=True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "memory.json"
        path.write_text(json.dumps(rt.memory.snapshot(), default=str), encoding="utf-8")
        resumed = learn_ready(runtime())
        resumed.options = rt.options
        resumed.memory.sp_session = None
        wcu.WingSession()._restore_memory(resumed, path)
        s = resumed.memory.sp_session
        self.assertEqual((s["last_tap"], s["unstable"], s["resumed_tap"]), (C08, 0, True))
        self.assertFalse(s["reentered"])          # wing7: a resumed segment may re-enter 振り返り once more
        self.assertFalse(s["zoom_attempted"])     # and zooms out again on a default-view panel
        frame = review(138)                                    # no info bar
        waits = [resumed.decide("Review", frame) for _ in range(nav.SP_UNSTABLE_WAITS)]
        self.assertEqual({(d.action, d.reason) for d in waits}, {("wait", "sp_info_settling")})
        after = resumed.decide("Review", frame)
        self.assertNotEqual(after.reason, "sp_info_unread")
        self.assertIn("sp_inspect_dropped", [e.get("event") for e in resumed.log])
        self.assertNotIn("resumed_tap", s)

    def test_an_approved_click_clears_the_reused_view_and_teaches_its_successor(self):
        """2026-10-25 speed batch: after a click the next frames are classified afresh; the screen that followed the
        click becomes the hint for the same click next time."""
        s = self.session(review(138, selected=C08), History())
        s.view_ref = ("Review", object())
        self.assertEqual(self.round(s, "Review", "a", "skill_learn").intent, "skill_learn")
        self.assertIsNone(s.view_ref)
        self.assertEqual(s.last_act, ("Review", "skill_learn"))
        with mock.patch.object(s, "classify", return_value="LearnConfirm") as classify:
            self.assertTrue(s.state({"want": "expect", "screens": ["LearnConfirm"]}, FakeImage("b"), object()))
        self.assertIsNone(classify.call_args.kwargs["hint"])          # nothing learned yet
        self.assertEqual(s.successors[("Review", "skill_learn")], "LearnConfirm")
        with mock.patch.object(s, "classify", return_value="LearnConfirm") as classify:
            s.state({"want": "expect", "screens": ["LearnConfirm"]}, FakeImage("c"), object())
        self.assertEqual(classify.call_args.kwargs["hint"], "LearnConfirm")

    def test_persistently_slow_decisions_stop(self):
        clock = Clock()
        s = self.session(review(138, selected=C08), History(), clock=clock)
        clock.step = 4.0
        for i in range(wcu.MAX_REQUEUES + 1):
            self.assertTrue(s.state({"want": "round", "screen": "Review"}, FakeImage(f"f{i}")))
            self.assertIsNone(s.pick(None, FakeImage(f"f{i}"), "Review", "skill_learn"))
        self.assertEqual(s.runtime.finished, "decision_too_slow")


class LearnRetryTests(unittest.TestCase):
    def test_seg8_learn_tap_without_dialog_is_retried_once_when_nothing_was_spent(self):
        rt = learn_ready(runtime())
        frame = review(138, selected=C08)
        self.assertEqual(rt.decide("Review", frame).intent, "skill_learn")
        reasons = [rt.decide("Review", frame) for _ in range(nav.SP_LEARN_RETRY_WAITS + 1)]
        self.assertEqual([d.reason for d in reasons[:-1]], ["sp_learn_settling"] * nav.SP_LEARN_RETRY_WAITS)
        self.assertEqual((reasons[-1].intent, reasons[-1].reason), ("skill_learn", "sp_learn"))
        self.assertTrue(any(e.get("event") == "sp_learn_retry" for e in rt.log))
        # a second silent learn tap: no further retry
        tail = [rt.decide("Review", frame).reason for _ in range(nav.SP_UNSTABLE_WAITS + 1)]
        self.assertEqual(tail[-1], "sp_learn_unconfirmed")

    def test_no_retry_after_ok_or_when_sp_changed(self):
        rt = learn_ready(runtime())
        frame = review(138, selected=C08)
        rt.decide("Review", frame)
        rt.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 138, "sp_after": 118}})
        reasons = [rt.decide("Review", frame).reason for _ in range(nav.SP_UNSTABLE_WAITS + 1)]
        self.assertEqual(reasons[-1], "sp_learn_unconfirmed")
        self.assertFalse(any(e.get("event") == "sp_learn_retry" for e in rt.log))
        rt = learn_ready(runtime())
        rt.decide("Review", frame)
        self.assertEqual(rt.decide("Review", review(130, selected=C08)).reason, "sp_learn_unconfirmed")


class SegmentContextTests(unittest.TestCase):
    def carried(self, rt):
        fresh = runtime()
        fresh.memory.restore(json.loads(json.dumps(rt.memory.snapshot(), ensure_ascii=False)))
        return fresh

    def forget_planned(self):
        rt = learn_ready(runtime())
        rt.decide("Review", review(138, selected=C08))
        rt.memory.sp_session["learning"].update(
            confirmed=True, forget={"row": 4, "name": "メンタルキュアI", "multiplier": 0.0, "reason": "recovery_first"})
        return rt

    def test_seg6_replace_confirm_after_attach_finishes_the_planned_chain(self):
        fresh = self.carried(self.forget_planned())
        self.assertEqual(fresh.memory.pending, "spend_sp")
        name = INFO[C08]["name"]
        d = fresh.decide("ReplaceConfirm", {"replace": {"forget": "メンタルキュアI", "new": name}})
        self.assertEqual((d.intent, d.reason), ("replace_confirm_ok", "sp_replace_confirmed"))
        other = self.carried(self.forget_planned())
        d = other.decide("ReplaceConfirm", {"replace": {"forget": "ボーカルアピール", "new": name}})
        self.assertEqual(d.intent, "replace_cancel")
        # no SP session in the previous segment: still cancelled (no plan, nothing guessed)
        self.assertEqual(runtime().decide("ReplaceConfirm", {"replace": {"forget": "メンタルキュアI", "new": name}})
                         .intent, "replace_cancel")

    def test_a13_roman_numeral_misread_on_replace_confirm(self):
        """Live a13: forget list 「ボーカルアピールV」, dialog 「ボーカルアピールIV」 -> the 5x skill was cancelled 3x."""
        name = INFO[C08]["name"]
        for rows, expected in ((["黒百合前で待ち合わせ", "ボーカルアピールV", "メンタルキュアI"], "replace_confirm_ok"),
                               (["ボーカルアピールIII", "ボーカルアピールV"], "replace_cancel")):
            rt = self.forget_planned()
            rt.memory.sp_session["learning"].update(
                forget={"row": 2, "name": "ボーカルアピールV", "multiplier": 2.5, "reason": "weakest_appeal"},
                forget_rows=rows)
            d = rt.decide("ReplaceConfirm", {"replace": {"forget": "ボーカルアピールIV", "new": name}})
            self.assertEqual(d.intent, expected, rows)

    def test_learn_confirm_after_attach_checks_the_carried_plan(self):
        rt = learn_ready(runtime())
        rt.decide("Review", review(138, selected=C08))
        fresh = self.carried(rt)
        ok = fresh.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 138, "sp_after": 118}})
        self.assertEqual(ok.intent, "learn_ok")
        fresh = self.carried(rt)
        bad = fresh.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 98, "sp_after": 78}})
        self.assertEqual(bad.intent, "learn_cancel")

    def test_sp_context_validation_and_done_keys(self):
        rt = runtime()
        rt.memory.sp_done_keys = [[1, 4]]
        fresh = self.carried(rt)
        self.assertEqual((fresh.memory.sp_done_keys, fresh.memory.pending), ([[1, 4]], None))
        self.assertNotIn("sp_context", runtime().memory.snapshot())
        base = runtime().memory.snapshot()
        for bad in ({"pending": "rest", "sp_session": None}, {"pending": "spend_sp", "sp_session": None},
                    {"pending": None, "sp_session": None, "sp_done_keys": [[1, "4"]]}, {"extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                nav.RunMemory().restore({**base, "sp_context": bad})


class LauncherOcrOption(unittest.TestCase):
    def test_ocr_option_inside_run_selects_the_frame_backend(self):
        s = wcu.WingSession(input_enabled=True)
        self.assertTrue(s.begin({"run": {"mode": "attach", "sp_mode": "auto", "ocr": "frame"}}))
        self.assertEqual(s.ocr_mode, "frame")
        with self.assertRaises(ValueError):
            wcu.WingSession().begin({"run": {"ocr": "fast"}})

    def test_launcher_passes_ocr_as_a_begin_parameter(self):
        from tools import run_tasks
        params, options = run_tasks.wing_options({"mode": "attach", "sp_mode": "auto", "ocr": "frame"})
        self.assertEqual((params["ocr"], "ocr" in params["run"]), ("frame", False))
        s = wcu.WingSession(input_enabled=True)
        s.begin(params)
        self.assertEqual(s.ocr_mode, "frame")
        with self.assertRaises(ValueError):
            run_tasks.wing_options({"ocr": "fast"})


class EarlierLiveFixes(unittest.TestCase):
    """seg2/seg3 fixes still hold: attach on the audition tab without a plan goes back to the lesson tab."""

    def test_resume_from_audition_tab(self):
        rt = runtime()
        d = rt.decide("AuditionTab", {"reward": 1000})
        self.assertEqual((d.intent, d.reason), ("schedule_lesson_tab", "resume_from_audition_tab"))


if __name__ == "__main__":
    unittest.main()
