"""Match 推进 (agent.core.match_play): decisions, runtime integration and native page replay."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import match_custom as mc
from agent.core import match_play as mpl
from agent.core import match_task as mt
from agent.core import task_common as tc
from tests.maa_probe import maa_ready

ROOT = Path(__file__).resolve().parents[1]
FRAMES = "dev_assets/private/wing_samples/2026-10-31/"
NOW = dt.datetime(2026, 10, 31, 1, 0, tzinfo=dt.timezone.utc)


def config(**kw):
    return mt.MatchConfig.from_params({"sample_only": False, "play": "advance", **kw})


class FakeLog:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def events(self):
        return list(self.records)

    def path(self):
        return Path("match.jsonl")


class FakeAuthority:
    def __init__(self, allowed=(mpl.SPEND_ACTION, mt.SPEND_ACTION), refusal=None, outcome="sent"):
        self.allowed, self.refusal, self.outcome = set(allowed), refusal, outcome
        self.grants = []

    def can_spend(self, action):
        return action in self.allowed

    def authorize(self, action, image, approver):
        self.grants.append((action, approver))
        return self.refusal

    def last_outcome(self, action):
        return self.outcome


class FakeReader:
    """One scripted page per call of ``page``; the reads return the page's fields."""

    def __init__(self, page, **fields):
        self._page, self.fields = page, fields

    def page(self, image):
        return self._page

    def main(self, image, rights=False):
        return {"top": None, "next": None, "lock": None, "rights": None, **self.fields}

    def stages(self, image):
        return {"area": None, "stage": None, "stars": None, "rights": None, **self.fields}

    def info(self, image):
        return {"stage": None, "stars": None, "rights": None, "bulk_off": True, **self.fields}

    def info_rights(self, image):
        return self.fields.get("rights")

    def unit(self, image):
        return {"shown": None, "empty": 0, "first_tab": "ユニット1", **self.fields}

    def result_word(self, image):
        return self.fields.get("word")


IMAGE = np.zeros((720, 1280, 3), np.uint8)


def main(top, nxt=None, lock=None, rights=None):
    return FakeReader("main", top=top, next=nxt, lock=lock, rights=rights)


def stages(area, stage, stars="uncleared", rights=(20, 20)):
    return FakeReader("stages", area=area, stage=stage, stars=stars, rights=rights)


def info(stage, stars="uncleared", rights=(20, 20), bulk_off=True):
    return FakeReader("info", stage=stage, stars=stars, rights=rights, bulk_off=bulk_off)


def unit(shown, empty=0, first_tab="ユニット1"):
    return FakeReader("unit", shown=shown, empty=empty, first_tab=first_tab)


def page(name, **fields):
    return FakeReader(name, **fields)


class PlayDecisionTests(unittest.TestCase):
    def play(self, authority=None, **kw):
        self.log = FakeLog()
        self.authority = authority or FakeAuthority()
        return mpl.MatchPlay(config(**kw), self.log, self.authority, lambda: NOW)

    def kinds(self, play, readers):
        return [play.step(reader, IMAGE).kind for reader in readers]

    def events(self):
        return [r["event"] for r in self.log.records]

    def test_frontier_area_unit_and_one_live(self):
        play = self.play(unit=2)
        self.assertEqual(self.kinds(play, [
            main(1, 2),                 # エリア2 unlocked below the selected エリア1
            main(2, lock=2),            # エリア3 locked by エリア2: エリア2 is the frontier
            stages(2, 5),
            info(5),                    # unit not checked yet
            unit(1, first_tab="ユニット1"),
            unit(2, empty=5),
            unit(2, empty=0),
            info(5),
            page("loading"), page("touch"), page("live"), page("result", word="win"),
        ]), ["area_next", "area_open", "stage_decide", "unit_open", "unit_tab_2", "omakase", "unit_back",
             "live_start", "wait", "touch", "skip", "next_stage"])
        self.assertEqual(self.authority.grants, [(mpl.SPEND_ACTION, mpl.APPROVER)])
        intent = next(r for r in self.log.records if r["event"] == "play_intent")
        self.assertEqual((intent["area"], intent["stage"], intent["rights_before"], intent["unit"]), (2, 5, [20, 20], 2))
        # 次のステージへ -> ステージ6 info: its rights bind the last live's consumption before the next live.
        self.assertEqual(play.step(info(6, rights=(19, 20)), IMAGE).kind, "live_start")
        self.assertEqual(play.consumed, 1)
        verified = next(r for r in self.log.records if r["event"] == "play_verified")
        self.assertEqual((verified["rights_after"], verified["consumed"]), ([19, 20], True))
        self.assertEqual(play.target, (None, 6))   # 次のステージへ may have crossed into the next area
        self.assertIn("play_omakase", self.events())

    def test_loss_retries_then_the_play_part_ends(self):
        play = self.play(retries=1)
        play.unit_ready = True
        seq = [stages(1, 3), info(3), page("touch"), page("live"), page("result", word="lose")]
        self.assertEqual(self.kinds(play, seq), ["stage_decide", "live_start", "touch", "skip", "area_select"])
        self.assertEqual(play.losses, {3: 1})
        # a loss that did not consume (rights unchanged) is verified as such; one retry is allowed
        self.assertEqual(self.kinds(play, [main(1, lock=1, rights=(20, 20)), stages(1, 3), info(3)]),
                         ["area_open", "stage_decide", "live_start"])
        self.assertEqual(play.consumed, 0)
        self.assertEqual(self.kinds(play, [page("touch"), page("live"), page("result", word="lose")]),
                         ["touch", "skip", "area_select"])
        self.assertEqual(play.phase, "returning")
        self.assertEqual(play.reason, "retries_used")
        self.assertEqual(self.kinds(play, [main(1, lock=1, rights=(20, 20))]), ["none"])
        self.assertEqual(play.phase, "ended")
        self.assertIsNone(play.outcome)
        self.assertEqual(play.summary()["losses"], 2)

    def test_lose_notice_is_closed_counted_and_its_return_verified(self):
        # 2026-10-31 live (A2-S17): 「敗北したため使用したフェス参加権を返却しました」 + 閉じる over the LOSE result.
        play = self.play(retries=1)
        play.unit_ready = True
        seq = [stages(2, 17), info(17, rights=(9, 20)), page("touch"), page("live"), page("lose_notice"),
               page("lose_notice"), page("result")]
        self.assertEqual(self.kinds(play, seq), ["stage_decide", "live_start", "touch", "skip", "lose_close",
                                                 "wait", "area_select"])
        result = next(r for r in self.log.records if r["event"] == "play_result")
        self.assertEqual((result["result"], result["returned"]), ("lose", True))
        self.assertEqual(play.losses, {17: 1})
        # back on Match main the unchanged rights verify the return; the retry plays the same stage again
        self.assertEqual(self.kinds(play, [main(2, lock=2, rights=(9, 20)), stages(2, 17), info(17, rights=(9, 20))]),
                         ["area_open", "stage_decide", "live_start"])
        self.assertEqual((play.consumed, play.lives[0]["consumed"]), (0, False))

    def test_a_result_or_lose_notice_left_from_before_is_left_first(self):
        play = self.play()
        self.assertEqual(self.kinds(play, [page("lose_notice"), page("result"), main(1, 2)]),
                         ["lose_close", "area_select", "area_next"])
        self.assertEqual(play.lives, [])

    def test_retries_zero_ends_after_the_first_loss(self):
        play = self.play(retries=0)
        play.unit_ready = True
        self.kinds(play, [stages(1, 3), info(3), page("result", word="lose")])
        self.assertEqual((play.phase, play.reason), ("returning", "retries_used"))

    def test_rights_left_reserve_and_fixed_count_end_the_play_part(self):
        play = self.play(rights_reserve=5)
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [info(3, rights=(5, 20))]), ["info_back"])
        self.assertEqual((play.phase, play.reason), ("returning", "rights_used"))
        self.assertEqual(self.authority.grants, [])
        play = self.play(count=1, count_mode="fixed")
        play.unit_ready = True
        self.kinds(play, [info(3), page("result", word="win")])
        self.assertEqual(play.reason, "count_reached")   # one live is the whole count: no 次のステージへ

    def test_win_with_the_last_right_returns_instead_of_next_stage(self):
        play = self.play()
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [info(3, rights=(1, 20)), page("result", word="win")]),
                         ["live_start", "area_select"])
        self.assertEqual(play.reason, "rights_used")

    def test_cleared_frontier_and_bulk_consume_are_never_played(self):
        play = self.play()
        self.assertEqual(self.kinds(play, [stages(2, 12, stars="cleared")]), ["stage_back"])
        self.assertEqual(play.reason, "frontier_cleared")
        play = self.play()
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [info(4, bulk_off=False)]), ["info_back"])
        self.assertEqual(play.reason, "bulk_consume_on")
        play = self.play()
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [info(4, stars="cleared")]), ["info_back"])
        self.assertEqual(self.authority.grants, [])

    def test_spend_not_enabled_or_refused_ends_without_a_tap(self):
        play = self.play(authority=FakeAuthority(allowed=()))
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [info(4)]), ["info_back"])
        self.assertEqual(play.reason, "play_spend_not_enabled")
        play = self.play(authority=FakeAuthority(refusal="frame_missing_or_stale"))
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [info(4)]), ["info_back"])
        self.assertEqual(play.reason, "spend_refused:frame_missing_or_stale")
        self.assertIn("play_refused", self.events())
        self.assertEqual(play.lives, [])

    def test_rights_jump_after_a_live_is_uncertain(self):
        play = self.play()
        play.unit_ready = True
        self.kinds(play, [info(4, rights=(10, 20)), page("result", word="win")])
        self.assertEqual(play.step(info(5, rights=(7, 20)), IMAGE).kind, "none")
        self.assertEqual((play.phase, play.outcome), ("ended", "spend_uncertain"))
        self.assertIn("play_uncertain", self.events())

    def test_live_that_never_started_is_not_spent_and_not_pressed_again(self):
        play = self.play()
        play.unit_ready = True
        play.step(info(4, rights=(10, 20)), IMAGE)
        kinds = self.kinds(play, [info(4, rights=(10, 20))] * (mpl.START_LEFT_FRAMES + 1))
        self.assertEqual(set(kinds), {"wait"})
        self.assertEqual((play.phase, play.reason, play.lives), ("returning", "live_not_started", []))
        self.assertIn("play_not_spent", self.events())
        self.assertEqual(play.step(info(4, rights=(10, 20)), IMAGE).kind, "info_back")

    def test_live_result_never_seen_is_uncertain(self):
        play = self.play()
        play.unit_ready = True
        play.step(info(4), IMAGE)
        for _ in range(mpl.MAX_LIVE_FRAMES):
            play.step(page("unknown"), IMAGE)
        self.assertEqual(play.step(page("unknown"), IMAGE).kind, "none")
        self.assertEqual(play.outcome, "spend_uncertain")

    def test_touch_and_skip_wait_for_their_tap_before_repeating(self):
        play = self.play()
        play.unit_ready = True
        play.step(info(4), IMAGE)
        kinds = self.kinds(play, [page("touch")] * (mpl.SETTLE_FRAMES + 2))
        self.assertEqual(kinds, ["touch", *["wait"] * mpl.SETTLE_FRAMES, "touch"])

    def test_unchanged_page_after_a_tap_waits_then_decides_again(self):
        play = self.play()
        kinds = self.kinds(play, [main(1, 2)] * (mpl.SETTLE_FRAMES + 2))
        self.assertEqual(kinds, ["area_next", *["wait"] * mpl.SETTLE_FRAMES, "area_next"])

    def test_play_idles_until_a_match_page_shows(self):
        play = self.play()
        self.assertEqual(self.kinds(play, [page("unknown"), page("loading"), page("unknown")] * 5), ["idle"] * 15)
        self.assertEqual((play.phase, play.waits, play.engaged), ("navigate", 0, False))
        self.assertEqual(self.kinds(play, [main(1, 2)]), ["area_next"])
        self.assertTrue(play.engaged)

    def test_unreadable_pages_end_the_play_part(self):
        play = self.play()
        self.assertEqual(self.kinds(play, [main(1, 2)]), ["area_next"])
        kinds = self.kinds(play, [page("unknown")] * (mpl.MAX_WAIT_FRAMES + 1))
        self.assertEqual(set(kinds), {"wait"})
        self.assertEqual((play.phase, play.reason), ("returning", "page_unknown"))
        kinds = self.kinds(play, [page("unknown")] * (mpl.MAX_WAIT_FRAMES + 1))
        self.assertEqual((play.phase, play.outcome), ("ended", "play_page_unknown"))

    def test_unit_tabs_beyond_the_first_page_end_the_play_part(self):
        play = self.play(unit=4)
        self.assertEqual(self.kinds(play, [unit(12, first_tab="ユニット11")]), ["unit_back"])
        self.assertEqual(play.reason, "unit_tab_unavailable")

    def test_current_unit_only_fills_empty_slots(self):
        play = self.play(unit=0)
        self.assertEqual(self.kinds(play, [unit(7, empty=2), unit(7, empty=0)]), ["omakase", "unit_back"])
        play = self.play(unit=0)
        self.assertEqual(self.kinds(play, [unit(7, empty=3), *[unit(7, empty=3)] * (mpl.SETTLE_FRAMES + 1),
                                           unit(7, empty=3)]),
                         ["omakase", *["wait"] * mpl.SETTLE_FRAMES, "omakase", "wait"])
        play.after = None
        self.assertEqual(play.step(unit(7, empty=3), IMAGE).kind, "unit_back")
        self.assertEqual(play.reason, "unit_still_empty")

    def test_frontier_area_search_is_bounded(self):
        play = self.play()
        for area in range(1, mpl.MAX_AREA_TAPS + 1):
            self.assertEqual(play.step(main(area, area + 1), IMAGE).kind, "area_next")
        self.assertEqual(play.step(main(9, 10), IMAGE).kind, "none")
        self.assertEqual((play.phase, play.reason), ("ended", "frontier_area_not_found"))

    def test_frontier_search_budget_restarts_after_each_live(self):
        # live 10-31: after a LOSE エリア選択へ showed the lower areas again; run-wide counting ended play at エリア4
        play = self.play()
        play.unit_ready = True
        self.assertEqual(self.kinds(play, [main(a, a + 1) for a in range(1, 6)]), ["area_next"] * 5)
        self.assertEqual(self.kinds(play, [main(6, lock=6), stages(6, 19), info(19, rights=(8, 20)),
                                           page("touch"), page("live"), page("lose_notice"), page("result")]),
                         ["area_open", "stage_decide", "live_start", "touch", "skip", "lose_close", "area_select"])
        kinds = self.kinds(play, [main(1, 2, rights=(8, 20))] + [main(a, a + 1) for a in range(2, mpl.MAX_AREA_TAPS + 1)])
        self.assertEqual(kinds, ["area_next"] * mpl.MAX_AREA_TAPS)
        self.assertEqual((play.phase, play.consumed), ("navigate", 0))

    def test_navigation_budget_between_lives(self):
        play = self.play()
        for _ in range(mpl.MAX_NAV_TAPS):
            play.after = None
            play.step(main(1, lock=1), IMAGE)
        play.after = None
        self.assertEqual(play.step(main(1, lock=1), IMAGE).kind, "wait")
        self.assertEqual(play.reason, "navigation_budget")

    def test_parsers(self):
        self.assertEqual(mpl.parse_rights("19/20+0"), (19, 20))
        self.assertIsNone(mpl.parse_rights("19/25"))
        self.assertEqual(mpl.parse_area_header("エリア4Hard"), 4)
        self.assertIsNone(mpl.parse_area_header("I73Normal"))
        self.assertEqual(mpl.parse_lock("エリア2のステージをすベてクリア"), 2)
        self.assertEqual(mpl.parse_stage("ステージ12"), 12)
        self.assertEqual(mpl.want_node("live_start"), "SC.Match.Play.Want.LiveStart")


class ConfigTests(unittest.TestCase):
    def test_options_parse_and_defaults(self):
        self.assertEqual((mt.MatchConfig().play, mt.MatchConfig().unit, mt.MatchConfig().retries), ("advance", 0, 3))
        self.assertEqual(config(play="off", unit=10, retries=5).unit, 10)
        for bad in ({"play": "climb"}, {"unit": 11}, {"unit": -1}, {"retries": 4}, {"retries": True}):
            with self.assertRaises(ValueError, msg=bad):
                config(**bad)
        self.assertIn("played", mt.SUCCESS_OUTCOMES)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets" / "private")
        self.addCleanup(self.tmp.cleanup)

    def runtime(self, authority, **cfg):
        runtime = mc.MatchRuntime(authority=authority, log_root=Path(self.tmp.name), now=lambda: NOW,
                                  vision=lambda context: None)
        runtime.run = mt.MatchRun(config(**cfg), runtime.log)
        runtime.run.planner = mt.SkipPlanner(runtime.run.config)
        runtime.started(runtime.run)
        return runtime

    def events(self, runtime):
        return [e["event"] for e in runtime.log.events()]

    def test_play_starts_only_when_live_start_may_spend(self):
        runtime = self.runtime(FakeAuthority())
        self.assertEqual(runtime.run.phase, "play")
        self.assertIn("play_begin", self.events(runtime))
        runtime = self.runtime(FakeAuthority(allowed=(mt.SPEND_ACTION,)))
        self.assertEqual(runtime.run.phase, "active")
        self.assertIn("play_not_enabled", runtime.run.notes)
        runtime = self.runtime(FakeAuthority(), play="off")
        self.assertEqual((runtime.run.phase, runtime.play), ("active", None))

    def check(self, runtime, reader, image, stage):
        with mock.patch.object(mpl, "Reader", lambda vision: reader):
            return runtime.play_check(None, image, {"stage": stage})

    def test_one_decision_per_frame_and_unsent_taps_roll_back(self):
        runtime = self.runtime(FakeAuthority())
        runtime.play.unit_ready = True
        frame1, frame2 = IMAGE.copy(), IMAGE.copy()
        frame2[0, 0] = 1
        self.assertFalse(self.check(runtime, info(4), frame1, "observe"))
        self.assertFalse(self.check(runtime, info(4), frame1, "info_back"))
        self.assertEqual(runtime.play.phase, "live")
        # the LiveStart node never ran on frame1 (its guard missed): the next frame restores the state before it
        self.assertFalse(self.check(runtime, main(1, lock=1), frame2, "observe"))
        self.assertIn("play_not_spent", self.events(runtime))
        self.assertIn("play_tap_not_sent", self.events(runtime))
        self.assertEqual(runtime.play.lives, [])
        frame3 = IMAGE.copy()
        frame3[0, 1] = 1
        self.assertTrue(self.check(runtime, info(4), frame3, "live_start"))
        self.assertIsNone(runtime._play_unsent)
        self.assertEqual(len(runtime.play.lives), 1)

    def test_wait_catches_a_tap_whose_node_did_not_run(self):
        # live 10-31 run e: AreaNext's max_hit (20) was used up; the 21st decision fell through to SC.Nav.BackOne.
        runtime = self.runtime(FakeAuthority())
        frames = []
        for n in range(mpl.MAX_UNSENT_TAPS):
            frame = IMAGE.copy()
            frame[0, n] = 1
            frames.append(frame)
            self.assertFalse(self.check(runtime, main(1, 2), frame, "observe"))
            # AreaNext is skipped (max_hit used up, its Want never evaluated): Wait undoes the decision
            self.assertTrue(self.check(runtime, main(1, 2), frame, "wait"))
            self.assertIsNone(runtime._play_unsent)
            self.assertEqual(runtime.play.nav_taps, 0)
        self.assertEqual(self.events(runtime).count("play_tap_not_sent"), mpl.MAX_UNSENT_TAPS)
        self.assertEqual((runtime.play.phase, runtime.play.reason), ("returning", "tap_not_sent"))

    def test_a_tap_that_runs_resets_the_unsent_count(self):
        runtime = self.runtime(FakeAuthority())
        first, second = IMAGE.copy(), IMAGE.copy()
        second[0, 0] = 1
        self.check(runtime, main(1, 2), first, "observe")
        self.assertTrue(self.check(runtime, main(1, 2), first, "wait"))
        self.assertEqual(runtime._play_unsent_runs, 1)
        self.assertTrue(self.check(runtime, main(1, 2), second, "area_next"))
        self.assertEqual((runtime._play_unsent_runs, runtime.play.nav_taps), (0, 1))

    def test_review_cancel_only_for_a_rejected_or_left_over_review(self):
        # 「加上自动点cancel」: never this run's own pending review or a started skip.
        runtime = self.runtime(FakeAuthority())
        cases = {"play": True, "active": True, "aborting": True, "review": False, "awaiting_result": False}
        for phase, expected in cases.items():
            runtime.run.phase = phase
            self.assertEqual(runtime.extra_state("cancel_review", None, IMAGE, {}), expected, phase)
        runtime.run.phase = "active"
        runtime.run.finish("spend_uncertain", "test")
        self.assertFalse(runtime.extra_state("cancel_review", None, IMAGE, {}))

    def test_ended_play_hands_the_rights_left_to_the_skip(self):
        runtime = self.runtime(FakeAuthority(), count=5, count_mode="fixed")
        runtime.play.consumed, runtime.play.phase = 2, "returning"
        self.check(runtime, main(2, lock=2), IMAGE, "observe")
        self.assertEqual(runtime.run.phase, "active")
        self.assertEqual((runtime.run.config.count, runtime.run.planner.config.count), (3, 3))
        self.assertEqual(runtime.run.summary()["play"]["consumed"], 2)
        runtime = self.runtime(FakeAuthority(), count=2, count_mode="fixed")
        runtime.play.consumed, runtime.play.phase = 2, "returning"
        self.check(runtime, main(2, lock=2), IMAGE, "observe")
        self.assertEqual((runtime.run.outcome, runtime.run.succeeded), ("played", True))

    def test_uncertain_play_halts_the_task(self):
        runtime = self.runtime(FakeAuthority())
        runtime.play.unit_ready = True
        self.check(runtime, info(4, rights=(10, 20)), IMAGE, "live_start")
        frame = IMAGE.copy()
        frame[0, 0] = 1
        self.check(runtime, page("result", word="win"), frame, "next_stage")
        frame = frame.copy()
        frame[0, 1] = 1
        self.check(runtime, info(5, rights=(6, 20)), frame, "observe")
        self.assertEqual(runtime.run.outcome, "spend_uncertain")
        self.assertTrue(runtime.run.halted)

    def test_live_start_error_denied_ends_play_sent_is_uncertain(self):
        runtime = self.runtime(FakeAuthority(outcome="denied"))
        runtime.play.unit_ready = True
        self.check(runtime, info(4), IMAGE, "live_start")
        self.assertTrue(runtime.play_spend_error(None, {}))
        self.assertEqual((runtime.play.phase, runtime.play.reason, runtime.play.lives), ("returning", "spend_refused", []))
        runtime = self.runtime(FakeAuthority(outcome="unknown"))
        runtime.play.unit_ready = True
        self.check(runtime, info(4), IMAGE, "live_start")
        self.assertFalse(runtime.play_spend_error(None, {}))
        self.assertEqual(runtime.run.outcome, "spend_uncertain")
        self.assertTrue(runtime.run.halted)

    def test_abort_during_a_live_is_uncertain(self):
        runtime = self.runtime(FakeAuthority())
        runtime.play.unit_ready = True
        self.check(runtime, info(4), IMAGE, "live_start")
        self.assertFalse(runtime.abort(None, {}))
        self.assertEqual(runtime.run.outcome, "spend_uncertain")

    def test_failed_navigation_tap_ends_the_play_part(self):
        runtime = self.runtime(FakeAuthority())
        self.check(runtime, main(1, 2), IMAGE, "area_next")
        self.assertTrue(runtime.play_failed(None, {}))
        self.assertEqual((runtime.play.phase, runtime.play.reason), ("returning", "tap_failed"))
        self.assertTrue(runtime.play_failed(None, {}))
        self.assertEqual(runtime.run.outcome, "play_return_failed")


class StaticContractTests(unittest.TestCase):
    def setUp(self):
        self.nodes = json.loads((ROOT / "assets/resource/base/pipeline/match.json").read_text(encoding="utf-8"))
        self.table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))

    def test_every_decision_has_a_guarded_disabled_click_node(self):
        actions = {n: a for a in self.table["actions"] for n in a["nodes"]}
        main = self.nodes["SC.Match.Main"]["next"]
        for kind, name in mpl.CLICK_NODES.items():
            node = self.nodes[name]
            self.assertFalse(node["enabled"], name)
            self.assertEqual(node["all_of"][-1], mpl.want_node(kind))
            self.assertGreaterEqual(len(node["all_of"]), 2, name)
            self.assertEqual(node["target"], actions[name]["rect"], name)
            self.assertEqual(node["next"], ["SC.Match.Play.Sent"])
            self.assertEqual(self.nodes[mpl.want_node(kind)]["custom_recognition_param"], {"stage": kind})
            self.assertIn("[JumpBack]" + name, main)
        self.assertEqual(self.nodes["SC.Match.Play.LiveStart"]["on_error"], ["SC.Match.Play.SpendError"])
        self.assertTrue(all(self.nodes[n]["on_error"] == ["SC.Match.Play.Failed"]
                            for k, n in mpl.CLICK_NODES.items() if k != "live_start"))
        consuming = [a["id"] for a in self.table["actions"] if a["consuming"] and a["id"].startswith("match-play")]
        self.assertEqual(consuming, [mpl.SPEND_ACTION])
        self.assertEqual(actions["SC.Match.Play.LiveStart"]["id"], mpl.SPEND_ACTION)
        # observed before any play click, waited on after them; play nodes come before the Skip nodes
        self.assertLess(main.index("SC.Match.Play.Observe"), main.index("[JumpBack]SC.Match.Play.AreaNext"))
        self.assertLess(main.index("[JumpBack]SC.Match.Play.AreaSelect"), main.index("[JumpBack]SC.Match.Play.Wait"))
        self.assertLess(main.index("[JumpBack]SC.Match.Play.Wait"), main.index("[JumpBack]SC.Match.Skip.Result.Close"))
        self.assertIn("SC.Match.Config.Play", self.nodes["SC.Match.Entry"]["custom_action_param"]["config_nodes"])
        self.assertEqual(mc.CONFIG_NODES, tuple(self.nodes["SC.Match.Entry"]["custom_action_param"]["config_nodes"]))

    def test_launcher_scopes_both_match_spends(self):
        from tools import run_tasks
        from agent.core import webview_controller as wc

        plan = run_tasks.plan_tasks(run_tasks.load_interface(), {"preset": None, "tasks": [{"name": "MatchSkip"}]})[0]
        self.assertEqual(run_tasks.task_spend_actions(plan), {mt.SPEND_ACTION, mpl.SPEND_ACTION})
        skip_only = run_tasks.plan_tasks(run_tasks.load_interface(), {"preset": None, "tasks": [
            {"name": "MatchSkip", "option": {"Match推进": "只跳过"}}]})[0]
        self.assertEqual(run_tasks.task_spend_actions(skip_only), {mt.SPEND_ACTION})
        self.assertIn(mpl.SPEND_ACTION, run_tasks.SPEND_ACTIONS_ALLOWED)
        limits = run_tasks.spend_grant_limits({"preset": None, "tasks": [{"name": "MatchSkip"}],
                                               "allow_spend": [mt.SPEND_ACTION, mpl.SPEND_ACTION]})
        self.assertEqual(limits, {mpl.SPEND_ACTION: run_tasks.MATCH_PLAY_GRANT_LIMIT})
        self.assertEqual(run_tasks.MATCH_PLAY_GRANT_LIMIT, mpl.MAX_LIVES)
        gate = wc.SpendGate([mt.SPEND_ACTION, mpl.SPEND_ACTION], max_per_action=3, action_limits=limits)
        self.assertEqual(gate.action_limits[mpl.SPEND_ACTION], 40)
        override = run_tasks.sequence_task_override("MatchSkip", {"match-fes"}, [mt.SPEND_ACTION, mpl.SPEND_ACTION], plan)
        self.assertNotIn("SC.Match.Play.LiveStart", override)
        override = run_tasks.sequence_task_override("MatchSkip", {"match-fes"}, [mt.SPEND_ACTION], plan)
        self.assertEqual(override["SC.Match.Play.LiveStart"], {"enabled": False})


# ---------------------------------------------------------------- native frames
NATIVE = {"main_a1": "20261031T004316432944Z-capture.png", "main_a2": "20261031T004355849918Z-capture.png",
          "stages": "20261031T004419709373Z-capture.png", "info5": "20261031T004449438326Z-capture.png",
          "touch": "20261031T004545125446Z-capture.png", "live": "20261031T004620546433Z-capture.png",
          "win": "20261031T004650092859Z-capture.png", "info6": "20261031T004732925069Z-capture.png",
          "unit2": "20261031T004758891871Z-capture.png", "unit3_empty": "20261031T004820809793Z-capture.png",
          "unit3_filled": "20261031T004848595619Z-capture.png", "lose_notice": "20261031T035530840961Z-capture.png"}


class NativeVision:
    """``task_custom.Vision`` on a private PNG: Maa node probes for ``hit`` and the Maa OCR model for ``text``."""

    def __init__(self, frame):
        from tests.maa_probe import NodeProbe

        self.frame, self.probe, self._hits = frame, NodeProbe(), {}

    def hit(self, node, image):
        if node not in self._hits:
            self._hits[node] = self.probe.hits(self.frame, node)
        return self._hits[node]

    def text(self, image, roi, threshold=0.95):
        from tests.maa_probe import ocr_items

        rows = ocr_items(self.frame, tuple(roi))
        x, y, w, h = roi
        if not rows or not all(r["text"].strip() and r["score"] >= threshold and r["box"][0] >= x and r["box"][1] >= y
                               and r["box"][0] + r["box"][2] <= x + w and r["box"][1] + r["box"][3] <= y + h for r in rows):
            return None
        lines = []
        for row in sorted(rows, key=lambda r: r["box"][1] + r["box"][3] / 2):
            centre = row["box"][1] + row["box"][3] / 2
            if lines and abs(lines[-1][0] - centre) < 10:
                lines[-1][1].append(row)
            else:
                lines.append([centre, [row]])
        return tc.normalize_text("".join(r["text"] for _, line in lines for r in sorted(line, key=lambda r: r["box"][0])))


def native(name):
    from tests.maa_probe import rgb

    frame = FRAMES + NATIVE[name]
    return mpl.Reader(NativeVision(frame)), np.ascontiguousarray(rgb(frame)[:, :, ::-1])


@unittest.skipUnless(maa_ready(*(FRAMES + f for f in NATIVE.values())),
                     "需要 MaaFramework、私有模板包、OCR 模型与 wing_samples/2026-10-31 原生帧")
class NativeReplayTests(unittest.TestCase):
    def play(self, **kw):
        self.authority = FakeAuthority()
        return mpl.MatchPlay(config(**kw), FakeLog(), self.authority, lambda: NOW)

    def test_pages_and_reads(self):
        expected = {"main_a1": "main", "main_a2": "main", "stages": "stages", "info5": "info", "info6": "info",
                    "touch": "touch", "live": "live", "win": "result", "unit2": "unit", "unit3_empty": "unit",
                    "unit3_filled": "unit", "lose_notice": "lose_notice"}
        for name, page_name in expected.items():
            reader, image = native(name)
            self.assertEqual(reader.page(image), page_name, name)
        reader, image = native("main_a1")
        self.assertEqual(reader.main(image, rights=True), {"top": 1, "next": 2, "lock": None, "rights": (20, 20)})
        reader, image = native("main_a2")
        self.assertEqual(reader.main(image), {"top": 2, "next": None, "lock": 2, "rights": None})
        reader, image = native("stages")
        self.assertEqual(reader.stages(image), {"area": 2, "stage": 5, "stars": "uncleared", "rights": (20, 20)})
        reader, image = native("info5")
        self.assertEqual(reader.info(image), {"stage": 5, "stars": "uncleared", "rights": (20, 20), "bulk_off": True})
        reader, image = native("info6")
        self.assertEqual(reader.info(image), {"stage": 6, "stars": "uncleared", "rights": (19, 20), "bulk_off": True})
        reader, image = native("unit2")
        self.assertEqual(reader.unit(image), {"shown": 2, "empty": 0, "first_tab": "ユニット1"})
        reader, image = native("unit3_empty")
        self.assertEqual(reader.unit(image)["empty"], 5)
        reader, image = native("unit3_filled")
        self.assertEqual(reader.unit(image), {"shown": 3, "empty": 0, "first_tab": "ユニット1"})
        reader, image = native("win")
        self.assertEqual(reader.result_word(image), "win")

    def test_native_sequence_decides_the_sampled_taps(self):
        play = self.play(unit=3)
        decide = lambda name: play.step(*native(name)).kind  # noqa: E731
        self.assertEqual([decide(n) for n in ("main_a1", "main_a2", "stages", "info5", "unit2", "unit3_empty",
                                              "unit3_filled", "info5", "touch", "live", "win")],
                         ["area_next", "area_open", "stage_decide", "unit_open", "unit_tab_3", "omakase", "unit_back",
                          "live_start", "touch", "skip", "next_stage"])
        self.assertEqual(decide("info6"), "live_start")
        self.assertEqual((play.consumed, play.lives[0]["rights_after"]), (1, [19, 20]))
        self.assertEqual(len(self.authority.grants), 2)
        self.assertEqual([decide(n) for n in ("touch", "live", "lose_notice")], ["touch", "skip", "lose_close"])
        self.assertEqual(play.losses, {6: 1})


if __name__ == "__main__":
    unittest.main()
