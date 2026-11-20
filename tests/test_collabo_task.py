"""Collabo Fes rules (agent/core/collabo_task.py) and its Pipeline / action-table contract; no Maa, no input."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from agent.core import collabo_task as ct
from agent.core import pipeline_actions as pa

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "collabo.json"
TABLE = ROOT / "assets" / "input" / "pipeline_actions.json"


class Log(list):
    def append(self, record):
        super().append(record)


class Authority:
    def __init__(self, enabled=True, refusal=None):
        self.enabled, self.refusal, self.grants = enabled, refusal, []

    def can_spend(self, action):
        return self.enabled and action == ct.SPEND_ACTION

    def authorize(self, action, image, approver):
        self.grants.append((action, approver))
        return self.refusal


class Reader:
    """One scripted frame: ``page`` plus the reads its handler asks for."""

    def __init__(self, page, **reads):
        self._page, self.reads = page, reads

    def page(self, image):
        return self._page

    def banner(self, image):
        return self.reads.get("banner", True)

    def event(self, image, stages):
        reads = dict(self.reads)
        full = reads.pop("full", False)
        full = {s: (s in full) if isinstance(full, (set, tuple, list)) else bool(full) for s in stages}
        return {"pass": 5, "deadline": "11/27", "percent": "", "recovery": "", **reads, "full": full}

    def difficulty(self, image):
        return {"stage": "VocalStage", "ok": True, "level": 1, "plus": False, **self.reads}

    def unit(self, image):
        return {"shown": self.reads.get("unit", 1), "empty": self.reads.get("empty", 0)}

    def tension(self, image):
        return self.reads.get("button", "decide")

    def battle(self, image):
        return {"turn": 1, "prompt": "", "skills": False, "own": None, **self.reads}

    def report(self, image):
        return {"level": 1, "clear": 10000, "score": 10000, **self.reads}


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 18, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return datetime.fromtimestamp(self.t, timezone.utc)


def play(tmp, authority=None, clock=None, max_wait_s=0, **config):
    ledger = ct.LevelLedger(Path(tmp) / "levels.json")
    return ct.CollaboPlay(ct.CollaboConfig(**config), Log(), authority or Authority(), clock or Clock(), ledger,
                          max_wait_s)


IMAGE = np.zeros((720, 1280, 3), dtype=np.uint8)


def step(p, page, **reads):
    return p.step(Reader(page, **reads), IMAGE).kind


class CollaboPlayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_one_live_with_drag_then_next_level(self):
        auth = Authority()
        p = play(self.tmp.name, auth, count=2)
        self.assertEqual(step(p, "home", banner=False), "banner_next")
        self.assertEqual(step(p, "home"), "banner")
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(p.budget, 2)
        self.assertEqual(step(p, "difficulty", ok=False), "hard")
        self.assertEqual(step(p, "difficulty"), "level_ok")
        self.assertEqual(step(p, "unit", unit=3), "unit_tab_1")
        self.assertEqual(step(p, "unit"), "unit_live")
        self.assertEqual(step(p, "tension"), "tension")
        self.assertEqual(auth.grants, [(ct.SPEND_ACTION, ct.APPROVER)])
        self.assertEqual(p.phase, "live")
        self.assertEqual(step(p, "tension", button="start"), "live_start")
        self.assertEqual(step(p, "battle", skills=True, own=4), "skill")
        self.assertEqual(step(p, "battle", prompt="決めてください", own=4), "drag_second")
        self.assertEqual(p.drag_row, 4)
        self.assertEqual(step(p, "battle", prompt="もう1度タップして決定", own=1), "panel_confirm")
        self.assertEqual(step(p, "report"), "report_ok")
        self.assertEqual(p.phase, "navigate")
        self.assertEqual(p.ledger.get("11/27", "vocal"), (1, None))
        # Auto: 「+」 is white at Lv.1 (Lv.2 open) -> press it; grey at Lv.2 -> OK.
        self.assertEqual(step(p, "event", **{"pass": 4}), "stage_vocal")
        self.assertEqual(step(p, "difficulty", level=1, plus=True), "level_plus")
        self.assertEqual(step(p, "difficulty", level=2, plus=False), "level_ok")
        self.assertEqual(p.level, 2)

    def test_auto_presses_plus_while_it_is_white(self):
        # 2026-11-20 live: the Hard card opened at Lv.11 (last played); 「+」 white -> Lv.12, then grey.
        p = play(self.tmp.name, count=1)
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(step(p, "difficulty", level=11, plus=True), "level_plus")
        self.assertEqual(step(p, "difficulty", level=12, plus=None), "wait")
        self.assertEqual(step(p, "difficulty", level=12, plus=False), "level_ok")
        self.assertEqual(p.level, 12)
        self.assertTrue(any(r.get("event") == "collabo_level_highest" for r in p.log))

    def test_auto_stays_below_a_loss_and_stops_when_plus_does_not_move(self):
        p = play(self.tmp.name, count=1)
        p.ledger.record("11/27", "vocal", 14, False)
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(p.level, 13)
        self.assertEqual(step(p, "difficulty", level=13, plus=True), "level_ok")
        q = play(self.tmp.name + "/stale", count=1)
        self.assertEqual(step(q, "event"), "stage_vocal")
        self.assertEqual(step(q, "difficulty", level=5, plus=True), "level_plus")
        self.assertEqual(step(q, "difficulty", level=5, plus=True), "level_plus")   # one more for a stale frame
        self.assertEqual(step(q, "difficulty", level=5, plus=True), "level_ok")
        self.assertEqual(q.level, 5)

    def test_after_a_loss_the_card_steps_back_one_level(self):
        # Lost Lv.23: the card opens at Lv.23 (last played) -> 「−」 to Lv.22, OK; 「+」 is not pressed.
        p = play(self.tmp.name, count=1)
        p.ledger.record("11/27", "vocal", 22, True)
        p.ledger.record("11/27", "vocal", 23, False)
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(step(p, "difficulty", level=23, plus=True), "level_minus")
        self.assertEqual(step(p, "difficulty", level=22, plus=True), "level_ok")
        self.assertEqual(p.level, 22)

    def test_fixed_level_ignores_plus(self):
        p = play(self.tmp.name, count=1, level="4")
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(step(p, "difficulty", level=9, plus=True), "level_minus")
        self.assertEqual(step(p, "difficulty", level=3, plus=False), "level_plus")
        self.assertEqual(step(p, "difficulty", level=4, plus=True), "level_ok")

    def test_plus_lit_reads_the_button_brightness(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(ct.plus_lit(image))
        x, y, w, h = ct.HARD_PLUS
        image[y:y + h, x:x + w] = 245
        self.assertTrue(ct.plus_lit(image))
        image[y:y + h // 5, x:x + w] = 0
        image[y + h // 5:y + h, x:x + w] = 128
        image[y:y + h // 6, x:x + w] = 245
        self.assertIsNone(ct.plus_lit(image))
        self.assertIsNone(ct.plus_lit(None))

    def test_fading_page_after_a_leaving_tap_waits(self):
        # Live 2026-11-19: the difficulty dialog still showed after OK; deciding OK again hit the unit page.
        p = play(self.tmp.name)
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(step(p, "event"), "wait")
        self.assertEqual(step(p, "difficulty"), "level_ok")
        self.assertEqual(step(p, "difficulty"), "wait")
        self.assertEqual(step(p, "unknown"), "wait")
        self.assertEqual(step(p, "difficulty"), "wait")
        self.assertEqual(step(p, "unit"), "unit_live")
        self.assertEqual(step(p, "unit"), "wait")
        kinds = [step(p, "unit") for _ in range(ct.MAX_WAIT_FRAMES + 1)]
        self.assertEqual(kinds[-1], "none")
        self.assertEqual(p.outcome, "collabo_page_unread")

    def test_live_under_way_on_a_fresh_run_is_played_out(self):
        p = play(self.tmp.name, count=1)
        self.assertEqual(step(p, "battle", skills=True, own=5), "skill")
        self.assertEqual(p.phase, "live")
        self.assertEqual(step(p, "battle", prompt="決めてください", own=5), "drag_second")
        self.assertEqual(step(p, "battle", prompt="もう1度タップして決定", own=1), "panel_confirm")
        self.assertEqual(step(p, "report"), "report_ok")
        self.assertEqual(p.lives, [])
        self.assertEqual(p.ledger.get("11/27", "vocal"), (0, None))
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(p.budget, 1)

    def test_unread_clear_bonus_gets_more_frames_then_leaves_the_level(self):
        # 2026-11-20 live: Lv.11 クリアボーナス unread while SCORE was read -> the level never rose.
        p = play(self.tmp.name, count=1)
        self.assertEqual(step(p, "battle", skills=True, own=5), "skill")
        kinds = [step(p, "report", clear=None) for _ in range(ct.REPORT_CLEAR_FRAMES)]
        self.assertEqual(kinds, ["wait"] * ct.REPORT_CLEAR_FRAMES)
        self.assertEqual(step(p, "report", clear=None), "report_ok")
        # read on a later frame: counted as cleared
        p = play(self.tmp.name, count=1)
        step(p, "battle", skills=True, own=5)
        self.assertEqual(step(p, "report", clear=None), "wait")
        self.assertEqual(step(p, "report", level=11, clear=12500, score=54000), "wait")      # not Lv.11's bonus: unread
        self.assertEqual(step(p, "report", level=11, clear=42500, score=54000), "report_ok")
        self.assertEqual(p.counters["report_clear"], 0)

    def test_report_still_shown_gets_a_few_more_oks(self):
        p = play(self.tmp.name)
        p.last_page = "report"
        p.pending = {"kind": "report_ok", "page": "report"}
        for _ in range(ct.MAX_PENDING_RETRIES):
            kinds = [step(p, "report") for _ in range(ct.PENDING_RETRY_FRAMES + 1)]
            self.assertEqual(kinds, ["wait"] * ct.PENDING_RETRY_FRAMES + ["report_ok"])
        kinds = [step(p, "report") for _ in range(ct.MAX_WAIT_FRAMES + 1)]
        self.assertNotIn("report_ok", kinds)
        self.assertEqual(p.outcome, "collabo_page_unread")
        # Not for other page-leaving taps.
        p = play(self.tmp.name)
        p.last_page = "unit"
        p.pending = {"kind": "unit_live", "page": "unit"}
        self.assertNotIn("unit_live", [step(p, "unit") for _ in range(ct.MAX_WAIT_FRAMES + 1)])

    def test_full_target_plays_the_backup_then_returns(self):
        p = play(self.tmp.name, stage="vocal", backup="center")
        p.ledger.record("11/27", "vocal", 7, True)
        self.assertEqual(step(p, "event", full={"vocal"}), "stage_center")
        self.assertEqual(p.stage, "center")
        self.assertEqual(step(p, "difficulty", stage="CenterStage", level=1), "level_ok")
        self.assertEqual(p.level, 1)
        p.pending = None
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(p.stage, "vocal")

    def test_backup_full_too_waits(self):
        p = play(self.tmp.name, backup="center", max_wait_s=3 * 3600)
        self.assertEqual(step(p, "event", full={"vocal", "center"}), "event_back")
        self.assertEqual(p.wait["reason"], "full")
        # Without a backup (default) a full target waits.
        p = play(self.tmp.name, max_wait_s=3 * 3600)
        self.assertEqual(step(p, "event", full={"vocal"}), "event_back")
        self.assertEqual(p.wait["reason"], "full")

    def test_backup_popup_full_counts_for_the_stage_played(self):
        p = play(self.tmp.name, backup="center", max_wait_s=3 * 3600)
        self.assertEqual(step(p, "event", full={"vocal"}), "stage_center")
        p.pending = None
        self.assertEqual(step(p, "stage_full"), "full_close")
        p.pending = None
        self.assertEqual(step(p, "event", full={"vocal"}), "event_back")
        self.assertEqual(p.wait["reason"], "full")

    def test_backup_config(self):
        self.assertEqual(ct.CollaboConfig.from_params({"stage": "vocal", "backup": "vocal"}).backup, "")
        self.assertEqual(ct.CollaboConfig.from_params({"backup": "center"}).backup, "center")
        with self.assertRaises(Exception):
            ct.CollaboConfig.from_params({"backup": "x"})

    def test_stage_full_popup_goes_back_and_waits(self):
        # Live 2026-11-19: Vocal filled while the unit page was open; ライブへ showed the 最高潮 popup.
        clock = Clock()
        p = play(self.tmp.name, clock=clock, max_wait_s=3 * 3600)
        p.pending, p.budget, p.event, p.level = None, 6, "11/27", 2
        self.assertEqual(step(p, "stage_full"), "full_close")
        self.assertEqual(step(p, "stage_full"), "wait")
        self.assertEqual(step(p, "unit"), "unit_back")
        self.assertEqual(step(p, "difficulty"), "difficulty_back")
        self.assertEqual(step(p, "event"), "event_back")
        self.assertEqual(p.wait["reason"], "full")
        self.assertFalse(p.stage_full)
        self.assertEqual(step(p, "home"), "idle")

    def test_own_panel_already_second_confirms_without_drag(self):
        p = play(self.tmp.name)
        p.phase, p.lives = "live", [{"live": 1}]
        p.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True}
        self.assertEqual(step(p, "battle", prompt="決めてください", own=1), "panel_confirm")

    def test_drag_bounded_per_turn(self):
        p = play(self.tmp.name)
        p.phase, p.lives = "live", [{"live": 1}]
        p.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True}
        kinds = [step(p, "battle", prompt="決めてください", own=3) for _ in range(3)]
        self.assertEqual(kinds, ["drag_second", "drag_second", "panel_confirm"])

    def test_count_done_leaves_to_home(self):
        p = play(self.tmp.name, count=1)
        p.budget, p.lives = 1, [{"live": 1}]
        self.assertEqual(step(p, "event"), "event_back")
        self.assertEqual(p.reason, "count_done")
        self.assertEqual(step(p, "home"), "none")
        self.assertEqual(p.phase, "ended")
        self.assertIsNone(p.outcome)

    def test_no_wait_budget_ends(self):
        p = play(self.tmp.name)
        self.assertEqual(step(p, "event", full=True), "event_back")
        self.assertEqual(p.reason, "wait_time_up:full")
        p = play(self.tmp.name)
        self.assertEqual(step(p, "event", **{"pass": 0}), "event_back")
        self.assertTrue(p.reason.startswith("no_pass"))

    def test_full_stage_waits_on_home_then_enters_again(self):
        clock = Clock()
        p = play(self.tmp.name, clock=clock, max_wait_s=3 * 3600)
        self.assertIsNone(p.status_text())
        self.assertEqual(step(p, "event", full=True), "event_back")
        self.assertEqual(p.phase, "navigate")
        self.assertRegex(p.status_text(), r"^Collabo Fes · Vocal 满员，等重开 · \d\d:\d\d 再看 · 已打 0/\d+ 局$")
        self.assertEqual(step(p, "home"), "idle")
        self.assertEqual(step(p, "unknown"), "idle")
        clock.t += ct.FULL_RECHECK_S + 1
        self.assertEqual(step(p, "home"), "banner")
        self.assertEqual(step(p, "event"), "stage_vocal")
        self.assertEqual(p.waits, 1)
        self.assertIsNone(p.status_text())

    def test_full_rechecks_back_off_exponentially_and_reset_after_a_live(self):
        clock = Clock()
        p = play(self.tmp.name, clock=clock, max_wait_s=3 * 3600)
        waits = []
        for _ in range(5):
            self.assertEqual(step(p, "event", full=True), "event_back")
            waits.append(p.wait["until"] - clock.t)
            self.assertEqual(step(p, "home"), "idle")
            clock.t += waits[-1] + 1
            self.assertEqual(step(p, "home"), "banner")
        self.assertEqual(waits, [60, 120, 240, 300, 300])
        p.lives.append({"live": 1})
        p.full_streak = 0                       # as the spend step does when a live starts
        self.assertEqual(step(p, "event", full=True), "event_back")
        self.assertEqual(p.wait["until"] - clock.t, 60)

    def test_runtime_waits_end_before_the_session_cap(self):
        from agent.core import collabo_custom
        from agent.core import task_common as tc

        for time_left, want in ((600.0, 600.0), (lambda: 1 / 0, 0), (None, 4 * 3600)):
            runtime = collabo_custom.CollaboRuntime(max_wait_s=4 * 3600,
                                                    time_left=(time_left if callable(time_left) or time_left is None
                                                               else (lambda v=time_left: v)))
            runtime.started(tc.TaskRun(ct.CollaboConfig(), runtime.log))
            self.assertEqual(runtime.play.max_wait_s, want)

    def test_runtime_retries_a_refused_navigation_tap(self):
        # 2026-11-20 live: ライブへ refused before input (frame binding) ended the task; now it decides again.
        from agent.core import collabo_custom
        from agent.core import task_common as tc

        logs = tempfile.TemporaryDirectory(dir=tc.PRIVATE)
        self.addCleanup(logs.cleanup)
        runtime = collabo_custom.CollaboRuntime(log_root=logs.name)
        runtime.started(tc.TaskRun(ct.CollaboConfig(), runtime.log))
        runtime.run = tc.TaskRun(ct.CollaboConfig(), runtime.log)
        play = runtime.play
        before = play.snapshot()
        play.pending = {"kind": "unit_live", "page": "unit"}
        for n in range(1, ct.MAX_UNSENT_TAPS):
            runtime._last_tap = ("unit_live", before)
            self.assertIs(runtime.failed(None, {"kind": "unit_live"}), True)
            self.assertIsNone(play.pending)
            self.assertEqual(runtime._unsent_runs, n)
        runtime._last_tap = ("unit_live", before)
        self.assertIs(runtime.failed(None, {"kind": "unit_live"}), False)
        self.assertEqual(play.outcome, "collabo_tap_failed")
        # a live tap never retries
        runtime = collabo_custom.CollaboRuntime(log_root=logs.name)
        runtime.started(tc.TaskRun(ct.CollaboConfig(), runtime.log))
        runtime.run = tc.TaskRun(ct.CollaboConfig(), runtime.log)
        runtime.play.phase = "live"
        runtime._last_tap = ("skill", runtime.play.snapshot())
        self.assertIs(runtime.failed(None, {"kind": "skill"}), False)
        self.assertEqual(runtime.play.outcome, "spend_uncertain")

    def test_set_count_waits_for_pass_recovery(self):
        clock = Clock()
        p = play(self.tmp.name, clock=clock, max_wait_s=3 * 3600, count=3)
        self.assertEqual(step(p, "event", **{"pass": 1}), "stage_vocal")
        self.assertEqual(p.budget, 3)
        p.lives, p.pending = [{"live": 1}], None   # one live played in between
        self.assertEqual(step(p, "event", **{"pass": 0, "recovery": "あと12:30"}), "event_back")
        self.assertEqual(p.wait["until"] - clock.t, 12 * 60 + 30 + ct.PASS_MARGIN_S)
        self.assertIn("Pass 不足，等恢复", p.status_text())
        self.assertIn("已打 1/3 局", p.status_text())
        self.assertEqual(step(p, "home"), "idle")
        clock.t += 13 * 60
        self.assertEqual(step(p, "home"), "banner")
        self.assertEqual(step(p, "event", **{"pass": 1}), "stage_vocal")

    def test_use_all_never_waits_for_pass(self):
        p = play(self.tmp.name, max_wait_s=3 * 3600)
        self.assertEqual(step(p, "event", **{"pass": 2}), "stage_vocal")
        p.lives, p.pending = [{"live": 1}], None   # one live played in between
        self.assertEqual(step(p, "event", **{"pass": 0, "recovery": "あと30:00"}), "event_back")
        self.assertTrue(p.reason.startswith("no_pass"))

    def test_wait_past_the_budget_ends(self):
        clock = Clock()
        p = play(self.tmp.name, clock=clock, max_wait_s=ct.FULL_RECHECK_S + ct.WAIT_END_MARGIN_S + 60)
        self.assertEqual(step(p, "event", full=True), "event_back")
        clock.t += ct.FULL_RECHECK_S + 1
        self.assertEqual(step(p, "home"), "banner")
        self.assertEqual(step(p, "event", full=True), "event_back")
        self.assertEqual(p.reason, "wait_time_up:full")
        self.assertEqual(step(p, "home"), "none")
        self.assertEqual(p.phase, "ended")

    def test_banner_probe_after_unread_pages(self):
        p = play(self.tmp.name)
        for _ in range(ct.MAX_BANNER_TAPS):
            self.assertEqual(step(p, "home", banner=False), "banner_next")
        self.assertEqual(step(p, "home", banner=False), "banner")
        self.assertEqual(step(p, "other_event"), "wrong_event_back")
        self.assertEqual(step(p, "home", banner=False), "banner")
        self.assertEqual(step(p, "event"), "stage_vocal")

    def test_empty_unit_stops_before_spend(self):
        auth = Authority()
        p = play(self.tmp.name, auth)
        step(p, "event")
        self.assertEqual(step(p, "unit", empty=2), "unit_back")
        self.assertEqual(p.reason, "unit_empty")
        self.assertEqual(step(p, "difficulty"), "difficulty_back")
        self.assertEqual(auth.grants, [])

    def test_spend_not_enabled_never_authorizes(self):
        auth = Authority(enabled=False)
        p = play(self.tmp.name, auth)
        step(p, "event")
        self.assertEqual(step(p, "tension"), "none")
        self.assertEqual(auth.grants, [])
        self.assertEqual(p.reason, "spend_not_enabled")

    def test_unexpected_start_page_fails(self):
        p = play(self.tmp.name)
        self.assertEqual(step(p, "tension", button="start"), "none")
        self.assertEqual(p.outcome, "collabo_unexpected_start")

    def test_unreadable_live_becomes_uncertain(self):
        p = play(self.tmp.name)
        p.phase, p.lives = "live", [{"live": 1}]
        p.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True}
        for _ in range(ct.MAX_LIVE_FRAMES + 1):
            step(p, "unknown")
        self.assertEqual(p.outcome, "spend_uncertain")

    def test_loss_locks_below(self):
        p = play(self.tmp.name)
        p.event = "11/27"
        p.phase, p.lives = "live", [{"live": 1, "level": 3}]
        p.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True}
        self.assertEqual(step(p, "report", level=3, clear=None, score=None), "wait")   # still animating in
        self.assertEqual(step(p, "report", level=3, clear=0, score=8000), "report_ok")
        self.assertEqual(p.ledger.get("11/27", "vocal"), (0, 3))

    def test_score_below_the_clear_bonus_is_a_loss(self):
        # 2026-11-21 live Lv.22: 「0pt」 unread, SCORE 1,052 < 136,000 -> loss, Lv.22 locked.
        p = play(self.tmp.name, count=1)
        p.event = "11/27"
        p.phase, p.lives = "live", [{"live": 1, "level": 22}]
        p.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True}
        self.assertEqual(step(p, "report", level=22, clear=None, score=1052), "report_ok")
        self.assertEqual(p.ledger.get("11/27", "vocal"), (0, 22))

    def test_unread_clear_bonus_keeps_the_ledger(self):
        p = play(self.tmp.name)
        p.event = "11/27"
        p.phase, p.lives = "live", [{"live": 1, "level": 3}]
        p.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True}
        kinds = [step(p, "report", level=3, clear=None, score=20000) for _ in range(ct.REPORT_CLEAR_FRAMES + 1)]
        self.assertEqual(kinds[-1], "report_ok")
        self.assertEqual(p.ledger.get("11/27", "vocal"), (0, None))


class TargetLevelTest(unittest.TestCase):
    def test_auto_and_fixed(self):
        auto = ct.CollaboConfig()
        # auto: an upper bound; the Hard card's 「+」 settles the level
        self.assertEqual(ct.target_level(auto, 0, None), ct.LEVEL_MAX)
        self.assertEqual(ct.target_level(auto, 7, 8), 7)
        self.assertIsNone(ct.target_level(auto, 0, 1))
        self.assertEqual(ct.target_level(ct.CollaboConfig(level="12"), 0, 3), 12)

    def test_config_validation(self):
        self.assertEqual(ct.CollaboConfig.from_params({"count": 3, "unit": 10}).count, 3)
        for bad in ({"stage": "x"}, {"count": 7}, {"unit": 0}, {"level": "31"}):
            with self.assertRaises(Exception):
                ct.CollaboConfig.from_params(bad)

    def test_parsers(self):
        self.assertEqual(ct.parse_level("Hard Lv.12"), 12)
        self.assertEqual(ct.parse_points(">10,000pt"), 10000)
        # 2026-11-22 live: a lone 「0pt」 OCRs as 「Opt」
        self.assertEqual((ct.parse_points("Opt"), ct.parse_points(">Opt"), ct.parse_points("opt")), (0, 0, 0))
        self.assertIsNone(ct.parse_points("Hopt"))
        self.assertIsNone(ct.parse_points("1,Opt"))
        self.assertEqual(ct.parse_unit("ユニット10"), 10)
        self.assertEqual(ct.parse_deadline("開催期限11/2713:59まで"), "11/27")
        self.assertEqual(ct.parse_turn("TURN 3"), 3)
        self.assertEqual(ct.parse_recovery("あと56:19"), 56 * 60 + 19)
        self.assertIsNone(ct.parse_recovery(""))

    def test_clear_bonus_checked_against_the_level(self):
        # 2026-11-20 live Lv.11: 「Hard(11)」 put a stray 「2」 in front of 42,500pt -> five wins read as unknown.
        self.assertEqual([ct.clear_bonus(x) for x in (1, 5, 7, 11)], [10000, 17000, 23500, 42500])
        self.assertEqual(ct.parse_points("2》42,500pt"), 42500)
        self.assertEqual(ct.check_clear(11, ct.parse_points("242,500pt")), 42500)
        self.assertEqual(ct.check_clear(11, 0), 0)
        self.assertIsNone(ct.check_clear(11, 12500))
        self.assertEqual(ct.REPORT_CLEAR[0], 392)


class PixelReadTest(unittest.TestCase):
    def test_pass_pips(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for i, (x, y) in enumerate(ct.PASS_PIPS):
            image[y, x] = (230, 255, 92) if i < 4 else (160, 120, 40)
        self.assertEqual(ct.pass_count(image), 4)
        image[674, 1097] = (255, 255, 255)
        self.assertIsNone(ct.pass_count(image))

    def test_own_row(self):
        image = np.full((720, 1280, 3), 200, dtype=np.uint8)
        x, y, w, h = ct.PANEL_SAMPLE[3]
        image[y:y + h, x:x + w] = (200, 166, 215)   # BGR pink under the prompt dimming (2026-11-18 frame)
        self.assertEqual(ct.own_row(image), 3)


class PipelineContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipe = json.loads(PIPELINE.read_text(encoding="utf-8"))
        cls.raw = json.loads(TABLE.read_text(encoding="utf-8"))

    def test_every_decision_has_a_disabled_guarded_node(self):
        actions = {n: a for a in self.raw["actions"] if a["family"] == "collabo-fes" for n in a["nodes"]}
        for kind, name in ct.CLICK_NODES.items():
            node = self.pipe[name]
            self.assertIs(node["enabled"], False, name)
            self.assertEqual(node["recognition"], "And")
            self.assertEqual(node["all_of"][1], ct.want_node(kind))
            self.assertIn(name, actions)
            self.assertIn(f"[JumpBack]{name}", self.pipe["SC.Collabo.Main"]["next"])
        self.assertEqual(sum(a["consuming"] for a in actions.values()), 1)
        self.assertTrue(actions[ct.CLICK_NODES["tension"]]["consuming"])
        self.assertEqual(ct.want_node("tension"), ct.APPROVER)

    def test_drag_node_contract(self):
        name = ct.CLICK_NODES["drag_second"]
        raw = next(a for a in self.raw["actions"] if name in a["nodes"])
        action = pa.Action(raw["id"], raw["family"], raw["consuming"], tuple(raw["rect"]), tuple(raw["nodes"]),
                           drag_rect=tuple(raw["drag_rect"]))
        node = self.pipe[name]
        self.assertEqual(node["action"], "Swipe")
        self.assertTrue(pa.is_drag_node(node, action))
        self.assertFalse(pa.is_drag_node({**node, "duration": 50}, action))
        self.assertFalse(pa.is_drag_node({**node, "end": [1000, 400, 50, 40]}, action))
        self.assertFalse(pa.is_drag_node({**node, "action": "Click"}, action))
        x, y, w, h = raw["rect"]
        for rx, ry, rw, rh in ct.PANEL_ROWS:   # every Swipe start box sits inside the action rect
            self.assertTrue(x <= rx and y <= ry and rx + rw <= x + w and ry + rh <= y + h)


if __name__ == "__main__":
    unittest.main()
