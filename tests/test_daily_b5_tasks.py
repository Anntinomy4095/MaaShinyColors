"""B5: Training, GradeFes and MatchSkip rules and offline replay of each whole task through tools/run_tasks.py."""
import copy
import datetime as dt
import hashlib
import json
import shutil
import time
import unittest
from pathlib import Path

from agent.core import grade_task as gt
from agent.core import match_task as mt
from agent.core import match_progress as mp
from agent.core import task_common as tc
from agent.core import training_task as tt
from tools import run_tasks as launcher
from tests.test_task_launcher import HAVE_MAA, RISK_POLICY, TABLE, FakePipelineHost, pipeline_nodes, private_ready

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets/private"
NOW = dt.datetime(2026, 10, 2, 8, 0, tzinfo=dt.timezone.utc)  # JST 17:00, game day 2026-10-02
P = "dev_assets/private/"
HOME = P + "shop-20261002-01-home-frames/frame-001.png"
# Training: the real 2026-10-01 chain (Bronze 22614 -> 22613, Lv2 -> 3) on this machine.
IDOL = P + "nav-v16-idol-back-execute-frames/frame-001.png"
LIST = P + "training-selection-execute-20261001f-frames/frame-001.png"
ZERO = P + "training-selection-execute-20261001f-frames/frame-023.png"
ONE = P + "training-v2-zero-execute-20261001e-frames/frame-024.png"
CONFIRM = P + "training-v3-one-execute-20261001b-frames/frame-024.png"
RESULT = P + "training-v4-confirm-execute-20261001b-frames/frame-024.png"
AFTER = P + "training-v5-result-execute-20261001b-frames/frame-024.png"
EXTRA_MATERIAL = P + "training-confirm-synthetic-20261001/Confirm-extra-material-native.png"
# 2026-10-03 16:31 live 「トレーニング大成功」 (Lv3 -> 4, Bronze 22620 -> 22619 expected); the task ended spend_uncertain and
# left this dialog open (browser_tasks/20261003-163400-704182/frame-001.png).
GREAT = P + "training-native-v6-results/daisuccess-20261003.png"
TODAY_LOG = [  # the real log of that run (game day rebased to the replay day): intent, then uncertain
    {"card": tt.CARD_ID, "event": "intent", "game_day": "2026-10-02", "inventory": 22620, "level": 3, "quantity": 1,
     "remaining_exp": 40, "ticket": "basic_bronze", "time": "2026-10-03T06:31:56.040068+00:00"},
    {"card": tt.CARD_ID, "event": "uncertain", "game_day": "2026-10-02", "inventory": 22620, "level": 3, "quantity": 1,
     "reason": "result_not_observed:sent", "remaining_exp": 40, "ticket": "basic_bronze",
     "time": "2026-10-03T06:32:11.458959+00:00"}]
# GradeFes: the 2026-10-02 live run (rights 20/20; the Normal tap did not consume).
FESMENU = P + "grade-run-02-home-fes-frames/frame-026.png"
FESMAIN = P + "grade-run-03c-entry-frames/frame-008.png"
MODE = P + "grade-run-04b-join-frames/frame-007.png"
DIFF = P + "grade-run-06-diff-readonly-frames/frame-001.png"
UNIT = P + "grade-run-07c-normal-frames/frame-024.png"
FESMAIN_6 = P + "manual-capture-kept-20261002/shop-grade/031.png"  # Fes main with 6/20 rights (manual capture)
# Difficulty dialog with the 2026-10-03 「獲得マニー 2倍」 campaign: a badge over each circle and the circle row 22-23 px
# further left (live 05:44, rights 20/20); a plain one at the largest prompt-bubble pulse phase; and the rehearsal dialog.
DIFF_MONEY2X = P + "manual-capture-kept-20261002/grade-difficulty-money2x-20261003.png"
DIFF_PULSE = P + "grade-run-07c-normal-frames/frame-001.png"
REHEARSAL = P + "manual-capture-kept-20261002/shop-grade/060.png"
# TensionDecided (B5c live 2026-10-02 23:34): 30 s after テンション決定, tension faces revealed, 「ライブ開始」, rights 20/20.
TENSION_RUN = P + "run_logs/samples/grade-after-spend-20261002T133404973940Z/"
TENSION = TENSION_RUN + "frame-070.png"
TENSION_PRESS = TENSION_RUN + "frame-001.png"  # the テンション決定 press animation (neither stage)
# 2026-10-11 live: pink (high-mood) rival 2 face is pale (749 px at S>=120); the guard counts S>=60 (1888 px).
TENSION_PALE = P + "sample-live-20261011/38-grade-auto-stopped.png"
DETAIL_NO_LV50 = P + "sample-live-20261011/28-after-mission-navigation-refusal.png"   # 2026-10-11 甜花 detail
LEGACY_LOG = [  # today's real B5 log: intent before テンション決定 (then thought to consume), then uncertain
    {"difficulty": "Normal", "event": "intent", "game_day": "2026-10-02", "rights_before": [20, 20],
     "time": "2026-10-02T13:34:04.554058+00:00"},
    {"difficulty": "Normal", "event": "uncertain", "game_day": "2026-10-02", "reason": "result_not_observed:sent",
     "rights_before": [20, 20], "time": "2026-10-02T13:36:05.885986+00:00"}]
# Match: native main page (Skip 1/20), first-visit help and the native skip dialog (2026-10-02 sample run, rights 20/20).
MATCH_HELP = P + "nav-v21-match-help-close-execute-frames/frame-001.png"
MATCH = P + "nav-v21-match-help-close-execute-frames/frame-023.png"
SKIP = P + "match-skip-native-20261002-frames/frame-005.png"
SYN = P + "b5-replay/"
RESULT_UNKNOWN, MATCH_DIMMED = SYN + "training-result-unknown-title.png", SYN + "match-main-dimmed.png"
MATCH_FULL = SYN + "match-main-full-rights.png"  # synthetic 20/20 paired with the native skip dialog's 0 / 20
RESULT_STEM_ONLY = SYN + "training-result-stem-only.png"  # 「トレーニング」 kept, 「成功」 erased: not an accepted title
# Synthetic skip dialogs from the native frame: thumb at the bottom; slot 6 (エリア2 ステージ6) counter / 参加権消費数.
SKIP_BOTTOM, SKIP_R3_1 = SYN + "skip-bottom.png", SYN + "skip-r3-1.png"
SKIP_R3_MISMATCH, SKIP_GREY6 = SYN + "skip-r3-mismatch.png", SYN + "skip-bottom-grey6.png"
# 2026-10-03 14:46 live: Fes main (rights 20/20) with today's live already played (browser_tasks/20261003-144644-531204).
FESMAIN_NOTHING = P + "manual-capture-kept-20261002/fes-main-nothing-to-do-20261003.png"
HOME_NO_BADGE = P + "manual-capture-kept-20261002/home-20261003-nobadge.png"
FRAMES = (HOME, IDOL, LIST, ZERO, ONE, CONFIRM, RESULT, AFTER, EXTRA_MATERIAL, FESMENU, FESMAIN, MODE, DIFF, UNIT,
          FESMAIN_6, MATCH_HELP, MATCH, SKIP, TENSION, TENSION_PRESS, DIFF_MONEY2X, DIFF_PULSE, REHEARSAL, FESMAIN_NOTHING,
          HOME_NO_BADGE, GREAT)
TRAINING_CHAIN = [HOME, IDOL, LIST, ZERO, ONE, CONFIRM, RESULT, AFTER, LIST, IDOL, HOME]
TRAINING_ACTIONS = ["home-idol", "idol-training", "training-select-card", "training-bronze-plus", "training-submit",
                    "training-start", "training-result-close", "nav-back", "nav-back", "nav-back"]
GRADE_CHAIN = [HOME, FESMENU, FESMAIN, MODE, DIFF, UNIT]
GRADE_ACTIONS = ["home-fes", "fes-grade-entry", "grade-join-open", "grade-mode-grade", "grade-difficulty-normal",
                 "grade-tension-confirm", "grade-tension-confirm"]  # テンション決定, then ライブ開始 (same table action)


def build_synthetic_frames():
    """Private replay derivatives of native frames; never evidence of the current live state."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    out = ROOT / SYN
    out.mkdir(parents=True, exist_ok=True)
    result = np.array(Image.open(ROOT / RESULT).convert("RGB"))
    result[190:225, 408:668] = 255  # title 「トレーニング成功」 gone (stands in for an unsampled 大成功 title)
    Image.fromarray(result).save(ROOT / RESULT_UNKNOWN)
    stem = np.array(Image.open(ROOT / RESULT).convert("RGB"))
    stem[190:225, 572:668] = 255  # 「成功」 gone, the 「トレーニング」 stem (ResultAny anchor) kept
    Image.fromarray(stem).save(ROOT / RESULT_STEM_ONLY)
    match = np.array(Image.open(ROOT / MATCH).convert("RGB")).astype(float) * 0.6
    Image.fromarray(match.astype(np.uint8)).save(ROOT / MATCH_DIMMED)
    full = Image.open(ROOT / MATCH).convert("RGB")
    draw = ImageDraw.Draw(full)
    x, y, w, h = mt.RIGHTS_FIELD
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=full.getpixel((x, y + 8)))
    draw.text((x + w / 2, y + h / 2), "20/20", font=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 14),
              fill=(255, 255, 255), anchor="mm")
    full.save(ROOT / MATCH_FULL)
    build_skip_frames()


def build_skip_frames():
    """Skip-dialog variants drawn on the native frame-005 (counter glyphs rendered with Arial Bold)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    font = "C:/Windows/Fonts/arialbd.ttf"
    base = np.array(Image.open(ROOT / SKIP).convert("RGB"))
    bottom = base.copy()
    bottom[193:330, 1194:1203] = bottom[400, 1196]
    bottom[430:559, 1194:1203] = 255

    def counted(value, cost):
        image = Image.fromarray(bottom.copy())
        draw = ImageDraw.Draw(image)
        cx, cy = 1073, 518
        draw.rectangle([cx - 25, cy - 11, cx + 25, cy + 11], fill=tuple(int(v) for v in bottom[cy, cx - 30]))
        draw.text((cx, cy), str(value), font=ImageFont.truetype(font, 22), fill=(255, 255, 255), anchor="mm")
        draw.rectangle([1070, 615, 1206, 650], fill=tuple(int(v) for v in bottom[612, 1100]))
        draw.text((1138, 633), f"{cost} / 20", font=ImageFont.truetype(font, 32), fill=(90, 80, 95), anchor="mm")
        return image

    Image.fromarray(bottom).save(ROOT / SKIP_BOTTOM)
    counted(1, 1).save(ROOT / SKIP_R3_1)
    counted(1, 2).save(ROOT / SKIP_R3_MISMATCH)
    grey = bottom.copy()
    patch = grey[505:531, 1135:1167].astype(int)
    pink = (patch[:, :, 0] >= 200) & (patch[:, :, 1] <= 90)
    patch[pink] = (170, 170, 175)
    grey[505:531, 1135:1167] = patch.astype(np.uint8)
    Image.fromarray(grey).save(ROOT / SKIP_GREY6)


# ======================================================================== pure rules
class TaskCommonTests(unittest.TestCase):
    def test_parsers_and_game_day(self):
        self.assertEqual(tc.ratio("20/20"), (20, 20))
        self.assertEqual(tc.ratio("1/20"), (1, 20))
        for bad in ("21/20", "x/20", "20", None, "0/0"):
            self.assertIsNone(tc.ratio(bad))
        self.assertEqual((tc.to_int("x22614"), tc.to_int("22,614"), tc.to_int("2")), (22614, 22614, 2))
        self.assertIsNone(tc.to_int("22.614"))
        self.assertEqual(tc.game_day(dt.datetime(2026, 10, 2, 19, 59, tzinfo=dt.timezone.utc)), "2026-10-02")
        self.assertEqual(tc.game_day(dt.datetime(2026, 10, 2, 20, 0, tzinfo=dt.timezone.utc)), "2026-10-03")
        self.assertEqual(tc.normalize_text("参加ユニット（Normal）"), "参加ユニット(Normal)")

    def test_logs_and_samples_stay_private(self):
        with self.assertRaises(ValueError):
            tc.JsonlLog("training", ROOT / "docs")
        with self.assertRaises(ValueError):
            tc.FrameSaver("x", ROOT / "docs")
        root = PRIVATE / "run_logs" / "_unittest_b5_common"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        log = tc.JsonlLog("grade", root, lambda: NOW)
        log.append({"event": "intent"})
        self.assertEqual(log.path().name, "grade-2026-10-02.jsonl")
        self.assertEqual([e["event"] for e in log.events()], ["intent"])


class TrainingRuleTests(unittest.TestCase):
    BASE = {"inventory": 22614, "level": 2, "remaining_exp": 40}

    def test_config_is_fixed_to_one_bronze_and_the_calibrated_card(self):
        # Default: no card chosen = default mode (first trainable S card of the current list sort), never the developer's card.
        config = tt.TrainingConfig.from_params({})
        self.assertEqual((config.card, config.selected, config.card_id, config.max_scroll_steps),
                         (tt.NO_CARD, False, "", tt.DEFAULT_SCROLL_STEPS))
        self.assertEqual(tt.TrainingConfig(), config)
        config = tt.TrainingConfig.from_params({"card": tt.CARD_ID})
        self.assertEqual((config.card, config.ticket, config.quantity, config.skip_if_done_today, config.card_id),
                         (tt.CARD_ID, "basic_bronze", 1, False, "2050170010"))
        self.assertTrue(config.selected)
        for bad in ({"card": "【別】カード"}, {"card": tt.CARD_ID, "card_id": "2050010010"},
                    {"card": tt.CARD_ID, "max_scroll_steps": 13}, {"card": tt.CARD_ID, "max_scroll_steps": True}, {"ticket": "silver"}, {"quantity": 2}, {"quantity": True},
                    {"skip_if_done_today": "yes"}, {"extra": 1}):
            with self.assertRaises(ValueError):
                tt.TrainingConfig.from_params(bad)

    def test_stage_checks(self):
        self.assertEqual(tt.baseline_reasons(self.BASE), [])
        self.assertEqual(tt.baseline_reasons({**self.BASE, "inventory": 0}), ["insufficient_tickets"])
        self.assertEqual(tt.baseline_reasons({**self.BASE, "level": None}), ["ocr_unreadable"])
        self.assertIn("no_remaining_exp", tt.baseline_reasons({**self.BASE, "remaining_exp": 0}))
        self.assertEqual(tt.one_reasons(self.BASE, {"inventory": 22614, "level": 2}), [])
        self.assertEqual(tt.one_reasons(self.BASE, {"inventory": 22613, "level": 2}), ["inventory_changed"])
        config = tt.TrainingConfig(tt.CARD_ID)
        self.assertEqual(tt.confirm_reasons(config, self.BASE, {"level": 2}), [])
        self.assertEqual(tt.confirm_reasons(config, self.BASE, {"level": 3}), ["level_changed"])
        self.assertEqual(tt.confirm_reasons(config, None, {"level": 2}), ["no_baseline"])
        self.assertEqual(tt.after_reasons(self.BASE, {"inventory": 22613, "level": 3, "remaining_exp": 40}), [])
        self.assertEqual(tt.after_reasons(self.BASE, {"inventory": 22613, "level": 2, "remaining_exp": 20}), [])
        self.assertEqual(tt.after_reasons(self.BASE, {"inventory": 22614, "level": 3, "remaining_exp": 40}),
                         ["inventory_not_minus_1"])
        self.assertEqual(tt.after_reasons(self.BASE, {"inventory": 22612, "level": 3, "remaining_exp": 40}),
                         ["inventory_not_minus_1"])
        self.assertEqual(tt.after_reasons(self.BASE, {"inventory": 22613, "level": 2, "remaining_exp": 40}),
                         ["no_level_or_exp_gain"])

    def test_log_state(self):
        events = [{"event": "intent"}, {"event": "verified"}, {"event": "intent"}]
        self.assertEqual(tt.log_state(events), tt.LogState(verified=1, uncertain=1))
        self.assertEqual(tt.log_state([{"event": "intent"}, {"event": "refused"}]), tt.LogState(0, 0))


class TrainingResultTitleTests(unittest.TestCase):
    def test_result_titles(self):
        self.assertEqual([tt.result_kind(t) for t in ("トレーニング成功", "トレーニング大成功", "トレーニング超成功",
                                                       "トレーニング 大成功!!", "トレーニング", "トレーニング確認",
                                                       "トレーニング失敗", "大成功", None)],
                         ["success", "great_success", "super_success", "great_success", None, None, None, None, None])

    def test_open_intent_is_the_single_unresolved_intent(self):
        self.assertEqual(tt.open_intent(TODAY_LOG), {"card": tt.CARD_ID, "ticket": "basic_bronze", "quantity": 1,
                                                     "inventory": 22620, "level": 3, "remaining_exp": 40})
        self.assertIsNone(tt.open_intent(TODAY_LOG[:1] + [{"event": "verified"}]))
        self.assertIsNone(tt.open_intent(TODAY_LOG + TODAY_LOG))  # two uncertain intents: never resolved
        self.assertIsNone(tt.open_intent([]))
        resolved = TODAY_LOG + [{"event": "resolved_great_success"}, {"event": "verified"}]
        self.assertIsNone(tt.open_intent(resolved))
        self.assertEqual(tt.log_state(resolved), tt.LogState(verified=1, uncertain=0))


class GradeRuleTests(unittest.TestCase):
    def test_config(self):
        config = gt.GradeConfig.from_params({})
        self.assertEqual((config.difficulty, config.runs_per_day, config.rights_reserve, config.bulk,
                          config.samples_runs_per_day), ("Normal", 1, 0, False, 0))
        for bad in ({"difficulty": "normal"}, {"difficulty": "Expert"}, {"runs_per_day": 0}, {"rights_reserve": 20},
                    {"bulk": True}, {"x": 1}, {"samples_runs_per_day": -1}, {"samples_runs_per_day": 21},
                    {"runs_per_day": 21}, {"bulk_count": 6}, {"mode": "x"}):
            with self.assertRaises(ValueError):
                gt.GradeConfig.from_params(bad)

    def test_a_live_with_an_observed_report_counts_as_played(self):
        live = [{"event": "tension_intent"}, {"event": "tension_decided"}, {"event": "intent", "stage": gt.LIVE_STAGE}]
        reported = gt.log_state(live + [{"event": "live_report"}, {"event": "live_unfinished"}])
        self.assertEqual((reported.attempts, reported.verified, reported.reported, reported.played), (1, 0, 1, 1))
        self.assertTrue(reported.open["reported"])  # kept open: a later report/ranking takeover still binds to it
        self.assertEqual(gt.rights_reasons(gt.GradeConfig(), reported, (20, 20)), ["runs_done_today"])
        self.assertEqual(gt.rights_reasons(gt.GradeConfig(runs_per_day=2), reported, (20, 20)), [])
        verified = gt.log_state(live + [{"event": "live_report"}, {"event": "verified"}])
        self.assertEqual((verified.verified, verified.reported, verified.played), (1, 0, 1))  # counted once
        unfinished = gt.log_state(live + [{"event": "live_unfinished"}])
        self.assertEqual(unfinished.played, 0)  # no report seen: still uncertain, not a play

    def test_rights_difficulty_and_unit_checks(self):
        config = gt.GradeConfig()
        empty = gt.LogState()
        self.assertEqual(gt.rights_reasons(config, empty, (20, 20)), [])
        self.assertEqual(gt.rights_reasons(config, empty, (0, 20)), ["rights_below_reserve"])
        self.assertEqual(gt.rights_reasons(gt.GradeConfig(rights_reserve=10), empty, (6, 20)), ["rights_below_reserve"])
        self.assertEqual(gt.rights_reasons(config, gt.LogState(attempts=1, verified=1), (20, 20)), ["runs_done_today"])
        self.assertEqual(gt.rights_reasons(config, gt.LogState(attempts=3, uncertain=3), (20, 20)), ["samples_cap_reached"])
        # A sent-and-uncertain ライブ開始 is counted but not retried and does not block the next sampling attempt.
        spent = {"kind": "tension", "spent": True, "uncertain": True, "resolved": False}
        sampling = gt.GradeConfig(samples_runs_per_day=3)
        self.assertEqual(gt.rights_reasons(sampling, gt.LogState(1, 1, spent), (20, 20)), [])
        self.assertEqual(gt.rights_reasons(sampling, gt.LogState(1, 0, {**spent, "spent": False}), (20, 20)),
                         ["attempt_unresolved"])
        self.assertEqual(gt.rights_reasons(config, empty, None), ["rights_unreadable"])
        ok = gt.difficulty_reasons(config, "Normal", gt.DIFFICULTY_TITLE, gt.ONE_RIGHT, (20, 20), (20, 20))
        self.assertEqual(ok, [])
        self.assertIn("title_not_grade_fes", gt.difficulty_reasons(
            config, "Normal", "フェスリハーサルの難易度を選択してくださし", gt.ONE_RIGHT, (20, 20), None))
        self.assertIn("prompt_not_one_right", gt.difficulty_reasons(
            config, "Normal", gt.DIFFICULTY_TITLE, "フェス参加権を5消費します", (20, 20), None))
        self.assertIn("difficulty_not_configured", gt.difficulty_reasons(
            config, "Hard", gt.DIFFICULTY_TITLE, gt.ONE_RIGHT, (20, 20), None))
        notice = gt.NO_RETURN + gt.ONE_RIGHT
        self.assertEqual(gt.unit_reasons(config, "参加ユニット(Normal)", notice, (20, 20), (20, 20)), [])
        self.assertIn("unit_title_not_configured_difficulty",
                      gt.unit_reasons(config, "参加ユニット(Hard)", notice, (20, 20), (20, 20)))
        self.assertIn("notice_not_one_right", gt.unit_reasons(config, "参加ユニット(Normal)", gt.NO_RETURN, (20, 20), None))
        self.assertIn("rights_changed", gt.unit_reasons(config, "参加ユニット(Normal)", notice, (19, 20), (20, 20)))

    def test_log_state_counts_uncertain_runs(self):
        events = [{"event": "intent"}, {"event": "uncertain"}, {"event": "intent"}, {"event": "verified"}]
        self.assertEqual((gt.log_state(events).attempts, gt.log_state(events).uncertain), (2, 1))
        self.assertEqual(gt.log_state([{"event": "intent"}, {"event": "refused"}]), gt.LogState(0, 0))
        live = {"event": "intent", "stage": gt.LIVE_STAGE, "rights_before": [20, 20]}
        flow = [{"event": "tension_intent", "rights_before": [20, 20]}, {"event": "tension_decided"}, live,
                {"event": "verified"}]
        self.assertEqual(gt.log_state(flow), gt.LogState(1, 0, None, 1))
        self.assertEqual(gt.log_state(flow[:1] + [{"event": "tension_refused"}]), gt.LogState(0, 0))

    def test_open_attempts_and_the_legacy_resolution(self):
        legacy = [{"event": "intent", "difficulty": "Normal", "rights_before": [20, 20]}, {"event": "uncertain"}]
        state = gt.log_state(legacy)
        self.assertEqual((state.attempts, state.uncertain, gt.resume_kind(state)), (1, 1, "legacy_uncertain"))
        resolved = gt.log_state(legacy + [{"event": "resolved", "result": gt.NOT_SPENT_TENSION_DECIDED}])
        self.assertEqual((resolved.attempts, resolved.uncertain, gt.resume_kind(resolved)), (1, 0, "tension"))
        self.assertTrue(resolved.open["decided"])
        self.assertEqual(gt.live_reasons(gt.GradeConfig(), resolved, (20, 20)), [])
        # A different resolution result, or one without a legacy attempt, resolves nothing.
        self.assertEqual(gt.resume_kind(gt.log_state(legacy + [{"event": "resolved", "result": "other"}])),
                         "legacy_uncertain")
        self.assertEqual(gt.log_state([{"event": "resolved", "result": gt.NOT_SPENT_TENSION_DECIDED}]), gt.LogState())
        # ライブ開始 sent (uncertain or pending): counted and never resumed; certainly refused: resumable.
        live = {"event": "intent", "stage": gt.LIVE_STAGE}
        tension = [{"event": "tension_intent", "rights_before": [20, 20], "difficulty": "Normal"},
                   {"event": "tension_decided"}]
        self.assertIsNone(gt.resume_kind(gt.log_state(tension + [live])))
        self.assertIsNone(gt.resume_kind(gt.log_state(tension + [live, {"event": "uncertain"}])))
        self.assertEqual(gt.resume_kind(gt.log_state(tension + [live, {"event": "refused"}])), "tension")
        self.assertEqual(gt.resume_kind(gt.log_state(tension[:1] + [{"event": "tension_uncertain"}])), "tension")
        self.assertIn("no_open_tension_decided_attempt",
                      gt.live_reasons(gt.GradeConfig(), gt.log_state(tension[:1]), (20, 20)))
        self.assertIn("no_open_tension_decided_attempt", gt.live_reasons(gt.GradeConfig(), gt.LogState(), (20, 20)))
        orphan = gt.log_state([live])
        self.assertEqual((orphan.attempts, gt.resume_kind(orphan)), (1, None))
        self.assertIn("rights_below_reserve", gt.live_reasons(gt.GradeConfig(rights_reserve=5),
                                                              gt.log_state(tension), (5, 20)))

    def test_tension_decided_checks(self):
        config = gt.GradeConfig()
        notice = gt.NO_RETURN + gt.ONE_RIGHT
        self.assertEqual(gt.tension_reasons(config, "参加ユニット(Normal)", notice, (20, 20), (20, 20)), [])
        self.assertEqual(gt.tension_reasons(config, "参加ユニット(Normal)", notice, (19, 20), (20, 20)), ["rights_changed"])
        self.assertEqual(gt.tension_reasons(config, "参加ユニット(Normal)", notice, (20, 20), None), ["rights_changed"])
        self.assertIn("unit_title_not_configured_difficulty",
                      gt.tension_reasons(config, "参加ユニット(Hard)", notice, (20, 20), (20, 20)))
        self.assertIn("rights_unreadable", gt.tension_reasons(config, "参加ユニット(Normal)", notice, None, (20, 20)))


class GradeWatchTests(unittest.TestCase):
    """Runtime decisions with a fake screen (no Maa): only Fes main with rights - 1 is success."""

    class Vision:
        screen, rights_text = None, None

        def __init__(self, context):
            pass

        def hit(self, node, image):
            return node == type(self).screen

        def number(self, image, crop, kind, pattern):
            return type(self).rights_text

    def runtime(self):
        from agent.core import grade_custom
        root = PRIVATE / "run_logs" / "_unittest_b5_grade_watch"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        self.time = [0.0]
        runtime = grade_custom.GradeRuntime(log_root=root, now=lambda: NOW, vision=self.Vision,
                                            sample_root=root / "samples", clock=lambda: self.time[0])
        runtime.run = gt.GradeRun(gt.GradeConfig(), runtime.log)
        runtime.run.phase, runtime.run.intent = "awaiting_result", {"difficulty": "Normal", "rights_before": [20, 20],
                                                                     "stage": gt.LIVE_STAGE}
        runtime.run.saver = tc.FrameSaver("x", root / "samples", lambda: NOW, 0)
        self.Vision.screen, self.Vision.rights_text = None, None
        return runtime

    def test_static_unrecognised_screen_stops_after_20_s_and_frames_are_deduplicated(self):
        import numpy as np
        runtime = self.runtime()
        runtime.run.saver = tc.FrameSaver("dedupe", runtime.sample_root, lambda: NOW, 10)
        still, moving = np.zeros((720, 1280, 3), np.uint8), np.full((720, 1280, 3), 90, np.uint8)
        for t, image in ((0, still), (2, still), (4, moving), (6, still), (24, still), (25.9, still)):
            self.time[0] = t
            self.assertFalse(runtime.watch(None, image, {}), t)
        self.assertEqual(len(runtime.run.saver.saved), 3)  # 0 s, 4 s (changed), 6 s (changed back); 2/24 s duplicates
        self.time[0] = 26.0  # unchanged since 6 s
        self.assertTrue(runtime.watch(None, still, {}))
        self.assertEqual((runtime.run.outcome, runtime.log.events()[0]["reason"]),
                         ("spend_uncertain", "unrecognised_screen_static_20s"))

    def test_fes_main_with_rights_minus_one_is_played(self):
        import numpy as np
        runtime = self.runtime()
        self.Vision.screen, self.Vision.rights_text = "SC.Nav.FesMain.Guard", "20/20"
        self.assertFalse(runtime.watch(None, np.zeros((720, 1280, 3), np.uint8), {}))
        self.Vision.rights_text = "19/20"
        self.assertTrue(runtime.watch(None, np.zeros((720, 1280, 3), np.uint8), {}))
        self.assertEqual(runtime.run.outcome, "played")
        self.assertEqual([e["event"] for e in runtime.log.events()], ["verified"])

    def test_home_without_rights_is_uncertain(self):
        import numpy as np
        runtime = self.runtime()
        self.Vision.screen = "SC.Common.Home.Guard"
        self.assertTrue(runtime.watch(None, np.zeros((720, 1280, 3), np.uint8), {}))
        self.assertEqual(runtime.run.outcome, "spend_uncertain")
        self.assertTrue(runtime.run.halted)
        self.assertEqual(runtime.log.events()[0]["reason"], "home_observed_rights_not_verified")


class MatchRuleTests(unittest.TestCase):
    def test_config_validates_recovery_preferences_and_stages(self):
        config = mt.MatchConfig.from_params({})
        self.assertEqual((config.stage, config.count, config.rights_reserve, config.use_recovery_tickets,
                          config.sample_only), ("latest", 20, 0, False, True))
        self.assertEqual(mt.MatchConfig.from_params({"stage": "A1-S1"}).stage, "A1-S1")
        recovery = mt.MatchConfig.from_params({"use_recovery_tickets": True, "recovery_cap": 2})
        self.assertEqual((recovery.use_recovery_tickets, recovery.recovery_cap), (True, 2))
        for bad in ({"use_recovery_tickets": 1}, {"recovery_cap": 0}, {"stage": "oldest"}, {"count": 0}, {"count": 21},
                    {"sample_only": 1}, {"jewels": True}):
            with self.assertRaises(ValueError):
                mt.MatchConfig.from_params(bad)
        self.assertEqual(mt.plan_notes(config, (1, 20)),
                         ["rights 1/20", "count_mode_all", "rights_insufficient_for_count_20(usable_1)"])
        self.assertEqual(mt.plan_notes(config, (20, 20)), ["rights 20/20", "count_mode_all"])
        self.assertEqual(mt.MatchConfig.from_params({"stage": "A2-S6"}).explicit(), (2, 6))
        self.assertEqual(mt.parse_header("エリア2ステージ5"), (2, 5))
        self.assertIsNone(mt.parse_header("エリア2"))


def view(stages, counts=None, cost=(0, 20), thumb=(195, 326), enabled=None):
    slots = []
    for i in range(6):
        if i < len(stages):
            slots.append(mt.Slot(i + 1, True, stages[i], (counts or {}).get(i + 1, 0), (enabled or {}).get(i + 1, True)))
        else:
            slots.append(mt.Slot(i + 1, False))
    return mt.DialogView(tuple(slots), cost, thumb)


TOP = [(1, 1), (1, 2), (1, 3), (2, 4), (2, 5), (2, 6)]
NEXT = [(2, 5), (2, 6), (3, 7), (3, 8), (3, 9)]
BOTTOM_THUMB = (430, 558)
FULL_THUMB = (193, 558)


def match_progress_fixture(stages=TOP):
    """Synthetic trusted progress/cycle for offline replay only; never evidence of a live game cycle."""
    unique = list(dict.fromkeys(stages))
    return mp.MatchProgress("offline-fixture-20261002", "ready", True,
                            tuple(mp.StageProgress(f"A{a}-S{s}", order, True)
                                  for order, (a, s) in enumerate(unique, 1)))


class SkipPlannerTests(unittest.TestCase):
    def planner(self, stages=TOP + NEXT, **params):
        return mt.SkipPlanner(mt.MatchConfig.from_params({"sample_only": False, "count": 2, **params}),
                              match_progress_fixture(stages))

    def test_scans_to_the_bottom_then_presses_the_latest_stage_count_times(self):
        planner = self.planner()
        self.assertEqual(planner.step(view(TOP)).kind, "scroll_down")
        self.assertEqual(planner.step(view(NEXT, thumb=BOTTOM_THUMB)), mt.Decision("plus", 5, notes=("press_1_of_2",)))
        self.assertEqual(planner.target, (3, 9))
        self.assertEqual(planner.step(view(NEXT, {5: 1}, (1, 20), BOTTOM_THUMB)).kind, "plus")
        self.assertEqual(planner.step(view(NEXT, {5: 2}, (2, 20), BOTTOM_THUMB)).kind, "confirm")

    def test_never_falls_back_when_the_latest_stage_is_not_skippable(self):
        planner = self.planner()
        planner.step(view(TOP))
        decision = planner.step(view(NEXT, thumb=BOTTOM_THUMB, enabled={5: False}))
        self.assertEqual((decision.kind, decision.outcome), ("abort", "latest_not_skippable"))

    def test_explicit_stage_scrolls_back_up_and_missing_stage_stops(self):
        planner = self.planner(stage="A1-S2")
        planner.step(view(TOP))
        self.assertEqual(planner.step(view(NEXT, thumb=BOTTOM_THUMB)).kind, "scroll_up")
        self.assertEqual(planner.step(view(TOP)).slot, 2)
        missing = self.planner(stage="A9-S99")
        missing.step(view(TOP))
        self.assertEqual(missing.step(view(NEXT, thumb=BOTTOM_THUMB)).outcome, "stage_unavailable")

    def test_incomplete_evidence_insufficient_rights_and_dead_scroll_stop(self):
        gap = self.planner()
        gap.step(view(TOP))
        self.assertEqual(gap.step(view([(4, 10), (4, 11)], thumb=BOTTOM_THUMB)).outcome, "progress_changed")
        poor = self.planner(stages=TOP, count=20, rights_reserve=1)
        self.assertEqual(poor.step(view(TOP, thumb=FULL_THUMB)).outcome, "rights_insufficient")
        stuck = self.planner()
        stuck.step(view(TOP))
        for _ in range(mt.MAX_UNREADABLE_FRAMES):
            self.assertEqual(stuck.step(view(TOP)).kind, "wait")
        self.assertEqual(stuck.step(view(TOP)).outcome, "scroll_no_effect")
        dirty = self.planner()
        self.assertEqual(dirty.step(view(TOP, {1: 1}, (1, 20))).outcome, "counts_not_zero")

    def test_counter_mismatch_aborts(self):
        planner = self.planner(stages=TOP)
        planner.step(view(TOP, thumb=FULL_THUMB))
        decision = planner.step(view(TOP, {6: 1}, (2, 20), BOTTOM_THUMB))
        self.assertEqual((decision.kind, decision.outcome), ("abort", "counter_mismatch"))


class TaskFilesTests(unittest.TestCase):
    @staticmethod
    def _parts(nodes, name) -> set:
        """``name`` and every node its And/Or recognition refers to (recursively)."""
        seen, todo = set(), [name]
        while todo:
            item = todo.pop()
            if item not in seen:
                seen.add(item)
                node = nodes.get(item, {})
                todo += node.get("all_of", []) + node.get("any_of", [])
        return seen

    def test_options_override_the_config_nodes(self):
        interface = launcher.load_interface()
        plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "GradeFes", "option": {"Grade难度": "Hard",
                                                                                          "Grade参加权保留": "5"}}]})
        self.assertEqual(plan.pipeline_override["SC.Grade.Config.Difficulty"]["attach"], {"difficulty": "Hard"})
        self.assertEqual(plan.pipeline_override["SC.Grade.Config.Reserve"]["attach"], {"rights_reserve": 5})
        plan, = launcher.plan_tasks(interface, {"preset": None, "tasks": ["MatchSkip"]})
        # 2026-10-11: 只采样 defaults to No everywhere (the real skip still needs --allow-match-spend and a grant)
        self.assertEqual(plan.pipeline_override["SC.Match.Config.Sample"]["attach"], {"sample_only": False})  # task default
        self.assertEqual(plan.pipeline_override["SC.Match.Config.Stage"]["attach"], {"stage": "latest"})
        plan, = launcher.plan_tasks(interface, {"tasks": ["MatchSkip"]})  # the daily preset's option applies: real skip
        self.assertEqual(plan.pipeline_override["SC.Match.Config.Sample"]["attach"], {"sample_only": False})
        self.assertEqual(plan.pipeline_override["SC.Match.Config.Recovery"]["attach"], {"use_recovery_tickets": False})
        # 2026-10-07: recovery tickets have their own switch (default No) and per-run cap; the policy refuses them
        # at run time (tests/test_fes_allocation.py). The daily preset's Match count is 「全部用完」.
        self.assertEqual(interface.options["Match回复券"]["default_case"], "No")
        self.assertEqual(plan.pipeline_override["SC.Match.Config.RecoveryCap"]["attach"], {"recovery_cap": 1})
        self.assertEqual(plan.pipeline_override["SC.Match.Config.Count"]["attach"], {"count": 20, "count_mode": "all"})
        # Default (PI = Pipeline = Python): no card id = the first trainable S card of the current list sort.
        plan, = launcher.plan_tasks(interface, {"tasks": ["Training"]})
        self.assertEqual(plan.pipeline_override["SC.Training.Config.Card"]["attach"], {"card_id": ""})
        # 2026-10-09: ticket / quantity have their own (single-case) options and carrier nodes.
        self.assertEqual(plan.pipeline_override["SC.Training.Config.Ticket"]["attach"], {"ticket": "basic_bronze"})
        self.assertEqual(plan.pipeline_override["SC.Training.Config.Quantity"]["attach"], {"quantity": 1})
        self.assertEqual(pipeline_nodes()["SC.Training.Config.Card"]["attach"], {"card": "", "card_id": ""})
        self.assertEqual(tt.TrainingConfig.from_params(plan.pipeline_override["SC.Training.Config.Card"]["attach"]),
                         tt.TrainingConfig())
        plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "Training", "option": {"训练卡": "2040010010"}}]})
        self.assertEqual(plan.pipeline_override["SC.Training.Config.Card"]["attach"], {"card_id": "2040010010"})
        self.assertEqual(tt.TrainingConfig.from_params(plan.pipeline_override["SC.Training.Config.Card"]["attach"]).card_id,
                         "2040010010")
        for bad in ("【くっつく、くっつかない】芹沢あさひ", "未选择", "1050170010", "2040010"):   # only ids; legacy names migrate in the UI
            with self.assertRaises(Exception, msg=bad):
                launcher.plan_tasks(interface, {"tasks": [{"name": "Training", "option": {"训练卡": bad}}]})

    def test_a_popup_after_the_home_check_is_closed_before_finishing(self):
        # 2026-10-30 daily: home was clean when Shop.Finish matched, then 「ミッション達成」 opened and FinishClean
        # timed out into Abort. Every task finish now closes whitelisted popups / waits out loading like HomeReached.
        nodes = pipeline_nodes()
        for task in ("Shop", "Training", "Business", "Grade", "Match"):
            self.assertEqual(nodes[f"SC.{task}.Finish"]["next"],
                             [f"SC.{task}.FinishClean", "[JumpBack]SC.Popup.CloseKnown", "[JumpBack]SC.Loading.Wait"])
        self.assertEqual(nodes["SC.Common.HomeReached"]["next"][1:],
                         ["[JumpBack]SC.Popup.CloseKnown", "[JumpBack]SC.Loading.Wait"])

    def test_pipeline_shape_and_spend_points(self):
        nodes = pipeline_nodes()
        for task in ("Training", "Grade", "Match"):
            self.assertEqual(nodes[f"SC.{task}.HomeClean"]["any_of"], nodes["SC.Common.HomeClean"]["any_of"])
            self.assertEqual(nodes[f"SC.{task}.FinishClean"]["any_of"], nodes["SC.Common.HomeClean"]["any_of"])
            self.assertEqual(nodes[f"SC.{task}.Main"]["next"][-1], "[JumpBack]SC.Nav.BackOne")
            self.assertNotIn(f"SC.{task}.Entry", json.loads((ROOT / "assets/resource/base/pipeline/placeholders.json")
                                                            .read_text(encoding="utf-8")))
        start = nodes["SC.Training.Confirm.Start"]
        self.assertEqual((start["enabled"], start["target"], start["max_hit"], start["on_error"], start["all_of"]),
                         (False, list(TABLE.get("training-start").rect), 1, ["SC.Training.SpendError"],
                          ["SC.Training.Confirm.Guard", "SC.Training.Check.Confirm"]))
        self.assertEqual(nodes["SC.Training.Confirm.Sent"]["any_of"], ["SC.Training.ResultAny.Guard"])
        # B6c: any accepted result title (成功/大成功/超成功 by OCR); the one 閉じる node is the Training node, also
        # reached from the shared popup whitelist so a left-over result dialog never blocks the later tasks.
        close = nodes["SC.Training.Result.Close"]
        self.assertEqual((close["all_of"], close["target"]), (["SC.Training.ResultAny.Guard", "SC.Training.Check.Result"],
                                                             list(TABLE.get("training-result-close").rect)))
        self.assertFalse(TABLE.get("training-result-close").consuming)
        known = nodes["SC.Popup.CloseKnown"]
        self.assertIn("SC.Training.ResultAny.Guard", known["any_of"])
        self.assertEqual(known["next"][-2:], ["SC.Training.Result.Close", "SC.Popup.NotHandled"])
        main = nodes["SC.Training.Main"]["next"]
        self.assertEqual(main[:4], ["SC.Training.Halt", "[JumpBack]SC.Training.Result.Close",
                                    "[JumpBack]SC.Training.Result.Wait", "[JumpBack]SC.Training.Resolve.Elsewhere"])
        # 2026-10-11: the guarded 並び替え popup nodes (Unexpected, Pick.R?C?, Apply) precede the shared known-popup close.
        popup = main[4:main.index("[JumpBack]SC.Resource.Observe.TrainingList")]
        self.assertEqual(popup, ["[JumpBack]SC.Training.SortPopup.Unexpected",
                                 *[f"[JumpBack]SC.Training.SortPopup.Pick.R{r}C{c}" for r in range(1, 4) for c in range(1, 4)],
                                 "[JumpBack]SC.Training.SortPopup.Apply"])
        # 2026-10-24: the non-consuming detail/list/idol-page nodes (each page guard needs an undimmed back button) run
        # before the shared popup close (~0.5 s per round); the ticket-consuming 開始 stays after it.
        close = main.index("[JumpBack]SC.Popup.CloseKnown")
        for name in ("SC.Training.Detail.Identity", "SC.Training.Detail.Submit", "SC.Training.Detail.Plus.Top",
                     "SC.Training.Detail.Plus", "SC.Training.List.Select", "SC.Training.List.Hold",
                     "SC.Training.Idol.Open"):
            self.assertLess(main.index(f"[JumpBack]{name}"), close, name)
            self.assertIn("SC.Common.Color.BackUndimmed", self._parts(nodes, nodes[name]["all_of"][0]), name)
        self.assertGreater(main.index("[JumpBack]SC.Training.Confirm.Start"), close)
        self.assertGreater(main.index("[JumpBack]SC.Training.Leave"), main.index("SC.Training.Finish"))
        self.assertNotIn("action", nodes["SC.Training.Result.Wait"])
        self.assertNotIn("action", nodes["SC.Training.Resolve.Elsewhere"])
        tension = nodes["SC.Grade.Unit.Confirm"]
        self.assertEqual((tension["enabled"], tension["target"], tension["max_hit"], tension["on_error"], tension["next"]),
                         (False, list(TABLE.get("grade-tension-confirm").rect), 2, ["SC.Grade.SpendError"],
                          ["SC.Grade.Watch.Tension", "SC.Grade.Live.Loop"]))
        # One table action, two presses: テンション決定 on 参加ユニット, ライブ開始 on TensionDecided (each guarded + granted).
        self.assertEqual(tension["any_of"], ["SC.Grade.Unit.Ready", "SC.Grade.Tension.Ready"])
        self.assertEqual(nodes["SC.Grade.Unit.Ready"]["all_of"], ["SC.Grade.Unit.Guard", "SC.Grade.Check.Unit"])
        self.assertEqual(nodes["SC.Grade.Tension.Ready"]["all_of"], ["SC.Grade.Tension.Guard", "SC.Grade.Check.LiveStart"])
        self.assertLessEqual(tension["timeout"], 300000)
        for name in ("SC.Grade.Watch.Concluded", "SC.Grade.Watch.Tension", "SC.Grade.Tension.Observe"):
            self.assertNotIn("action", nodes[name])  # read-only
            if name == "SC.Grade.Watch.Concluded":
                # Verified completion re-enters planning for the remaining daily lives.
                self.assertEqual(nodes[name]["next"], ["SC.Grade.Main"])
            else:
                self.assertNotIn("next", nodes[name])
        self.assertEqual(nodes["SC.Grade.Tension.Hold"]["custom_action"], "SC.Grade.Abort")  # never a click (or back)
        main = nodes["SC.Grade.Main"]["next"]
        self.assertIn("SC.Grade.Done", main)  # concluded after the spend: end without navigating
        self.assertLess(main.index("SC.Grade.Done"), main.index("[JumpBack]SC.Grade.Leave"))
        order = [main.index(n) for n in ("[JumpBack]SC.Grade.Tension.Observe", "[JumpBack]SC.Grade.Unit.Confirm",
                                         "SC.Grade.Tension.Hold", "[JumpBack]SC.Grade.Unit.Back", "[JumpBack]SC.Grade.Leave")]
        self.assertEqual(order, sorted(order))
        # SC.Grade.Plan.Join may conclude the run (e.g. runs_done_today) while it is evaluated, so Leave must come
        # after the Plan-gated nodes in the same round; otherwise the round falls through to Nav.BackOne and fails.
        leave = main.index("[JumpBack]SC.Grade.Leave")
        self.assertGreater(leave, main.index("[JumpBack]SC.Grade.Main.Join"))
        self.assertGreater(leave, main.index("[JumpBack]SC.Grade.Menu.Grade"))
        self.assertLess(leave, main.index("[JumpBack]SC.Nav.BackOne"))
        self.assertTrue(all(nodes[n]["recognition"] in ("TemplateMatch", "ColorMatch") or n.startswith("SC.Common.")
                            for n in nodes["SC.Grade.Tension.Guard"]["all_of"]))
        self.assertEqual(nodes["SC.Grade.Done"]["action"], "Custom")
        for d in ("easy", "normal", "hard"):
            self.assertFalse(TABLE.get(f"grade-difficulty-{d}").consuming)
        self.assertEqual({n for n, v in nodes.items() if isinstance(v, dict) and v.get("action") == "Click"
                          and TABLE.get(TABLE.node_actions(include_disabled=True)[n]).consuming},
                         {"SC.Shop.Confirm.Exchange", "SC.Training.Confirm.Start", "SC.Grade.Unit.Confirm",
                          "SC.Match.Skip.Start", "SC.Match.Play.LiveStart",   # Match 推进 ライブ開始
                          "SC.Grade.Rehearsal.Easy", "SC.Grade.Rehearsal.Normal", "SC.Grade.Rehearsal.Hard",
                          # W.I.N.G. プロデュース開始 (stamina 30; supervised Wing start mode only, 2026-10-09)
                          "SC.Wing.Click.StartConfirm.start_confirm_ok",
                          # 獲得報酬 再抽選 OK (stamina 30, 2026-10-10): opt-in, see the gate checks below
                          "SC.Wing.Click.RewardRerollConfirm.reward_reroll_ok",
                          # Official Auto Produce: プロデュース開始 (stamina 30 x runs) has its own flag since 2026-10-29;
                          # スキップ (cost unverified) has none, so no run can hold a grant for it
                          "SC.WeeklyProduce.Dialog.Start", "SC.WeeklyProduce.Dialog.Skip",
                          # ループ start with recovery soda (2026-10-31, user 「允许」; own opt-in and cap)
                          "SC.WeeklyProduce.Recovery.Start",
                          # テンション決定 (2026-11-18, one コラボフェスパス; own opt-in, ≤6 grants)
                          "SC.Collabo.Do.Tension"})
        from tools import run_tasks as rt
        self.assertEqual(rt.SPEND_FLAGS["allow_weekly_auto_spend"], "weekly-auto-start")
        self.assertIn("weekly-auto-start", rt.SPEND_ACTIONS_ALLOWED)
        self.assertNotIn("weekly-auto-skip", rt.SPEND_ACTIONS_ALLOWED)
        self.assertNotIn("weekly-auto-skip", rt.SPEND_FLAGS.values())
        # 再抽選 never fires unless the user enables it: the Produce option defaults to No, the navigator's end params
        # default to reroll=False, RunOptions.allow_reward_reroll defaults to False (and needs live input), and the
        # launcher offers no spend flag for its consuming action, so no run can hold a grant for it.
        from agent.core import produce_config as pc, wing_nav
        from tools import run_tasks
        self.assertIs(wing_nav.DEFAULT_END_PARAMS["reroll"], False)
        self.assertIs(wing_nav.RunOptions().allow_reward_reroll, False)
        self.assertIs(wing_nav.RunOptions.from_params({}, input_enabled=False, allow_reward_reroll=True).allow_reward_reroll,
                      False)
        self.assertIs(pc.default_config("true_end")["manual"]["reward_reroll"], False)
        self.assertNotIn("wing-reward-reroll-confirm", run_tasks.SPEND_FLAGS.values())
        self.assertNotIn("wing-reward-reroll-confirm", run_tasks.SPEND_ACTIONS_ALLOWED)
        interface = run_tasks.load_interface()
        self.assertEqual(interface.options["Produce再抽选"]["default_case"], "No")
        cancel = nodes["SC.Match.Skip.Cancel"]
        self.assertEqual(cancel["all_of"][0], "SC.Nav.MatchSkip.Guard")
        self.assertTrue(all(nodes[n]["threshold"] == 0.95 for n in nodes["SC.Nav.MatchSkip.Guard"]["all_of"]))
        self.assertIn("SC.Nav.MatchSkip.Cancel", nodes["SC.Nav.BackOne"]["next"])
        confirm = nodes["SC.Match.Skip.Start"]
        self.assertEqual((confirm["enabled"], confirm["target"], confirm["max_hit"], confirm["on_error"], confirm["next"]),
                         (False, list(TABLE.get("match-skip-confirm").rect), 1, ["SC.Match.SpendError"],
                          ["SC.Match.Skip.Result.Close", "SC.Match.Watch.Concluded"]))
        self.assertNotIn("action", nodes["SC.Match.Watch.Concluded"])
        for s in range(1, 7):
            self.assertEqual(nodes[f"SC.Match.Skip.Plus{s}"]["post_delay"], 1000)  # host: 30 per action and minute; ≤20 presses
        self.assertEqual({a.id: a.wheel for a in TABLE.actions if a.enabled and a.wheel
                          and a.family in {"match-fes", "training-bronze", "card-scan"}},
                         {"match-skip-scroll-down": -60, "match-skip-scroll-up": 60,
                          "idol-list-scroll-down": -60, "idol-list-scroll-up": 60,  # CardScan (live-calibrated 2026-10-08)
                          # training list ∓120
                          "training-list-scroll-down": -120, "training-list-scroll-up": 120})

    def test_difficulty_guard_and_click_rects_avoid_the_campaign_badge(self):
        """Dynamic-element rule (B5g): no circle-label anchors; each click rect inside its circle in both layouts.

        Measured on the native frames: circles r≈140 around y 360, centres x 332/638/942 and 22-23 px
        further left with the 「獲得マニー 2倍」 campaign, whose badges end at y ≤ 320.
        """
        nodes = pipeline_nodes()
        guard = nodes["SC.Grade.Difficulty.Guard"]["all_of"]
        for name in ("Easy", "Normal", "Hard"):
            self.assertNotIn(f"SC.Grade.Anchor.Difficulty.{name}", guard)
        for name in ("ScoreEasy", "ScoreNormal", "ScoreHard", "Prompt"):
            self.assertIsInstance(nodes[f"SC.Grade.Anchor.Difficulty.{name}"]["template"], list)  # one per layout/phase
        badge_bottom = 320
        for name, centres in (("easy", (332, 309)), ("normal", (638, 615)), ("hard", (942, 920))):
            x, y, w, h = TABLE.get(f"grade-difficulty-{name}").rect
            self.assertGreater(y, badge_bottom + 10, name)
            for cx in centres:
                for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                    self.assertLess(((px - cx) ** 2 + (py - 360) ** 2) ** 0.5, 125, (name, cx))

    def test_daily_preset_checks_the_b5_tasks(self):
        preset = {i["name"]: i["enabled"] for i in launcher.load_interface().presets["daily"]["task"]}
        # 2026-10-07: Match and Grade may share the rights on one day. MatchSkip (real skip, latest, 「全部用完」) is
        # checked; GradeFes unchecked but ordered before MatchSkip so an all-in Match leaves Grade its share.
        self.assertEqual((preset["Training"], preset["MatchSkip"], preset["GradeFes"], preset["Shop"]), (True, True, False, True))
        self.assertLess(list(preset).index("GradeFes"), list(preset).index("MatchSkip"))
        match, = [i for i in launcher.load_interface().presets["daily"]["task"] if i["name"] == "MatchSkip"]
        self.assertEqual(match["option"], {"Match只采样": "No", "Match关卡": "最新（全部关卡）", "Match次数": "全部用完"})
        plan = {p.name: p for p in launcher.plan_tasks(launcher.load_interface(), {"preset": "daily"})}
        self.assertNotIn("GradeFes", plan)
        self.assertEqual(plan["MatchSkip"].pipeline_override["SC.Match.Config.Sample"]["attach"], {"sample_only": False})


# ======================================================================== offline replay
def frames_present():
    return all((ROOT / f).is_file() for f in FRAMES)


@unittest.skipUnless(HAVE_MAA and frames_present(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 B5 实机帧")
class B5ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not private_ready():
            raise unittest.SkipTest("私有回放数据不完整")
        build_synthetic_frames()

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_b5_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def options(self):
        options = {k: {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples"}
                   for k in ("training", "grade", "match")}
        progress = match_progress_fixture()
        options["match"].update(progress=progress, progress_reader=lambda context, image: mp.ProgressObservation(
            progress.cycle_id, "ready", tc.image_digest(image), time.monotonic()))
        return options

    def run_task(self, tasks, frames, spend=(), simulate=True, interface=None, match_options=None):
        # task defaults, not the daily preset's options; the training card must be chosen explicitly
        tasks = [{"name": t, "option": {"训练卡": tt.CARD_IDS[tt.CARD_ID]}} if t == "Training" else t for t in tasks]
        config = {"preset": None, "tasks": tasks}
        if spend:
            config["allow_spend"] = list(spend)
        options = self.options()
        if match_options is not None:
            options["match"].update(match_options)
        return launcher.run(config, offline_steps=[(ROOT / f, "click") for f in frames], simulate_input=simulate,
                            policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                            interface=interface, task_options=options)

    def report(self, task, kind):
        report, = [r for r in task["reports"] if r["kind"] == kind]
        return report

    def events(self, kind):
        path = self.root / f"{kind}-2026-10-02.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def assert_points_inside(self, task):
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))

    # ---- Training
    def test_training_trains_once_and_verifies_inventory_and_level(self):
        summary = self.run_task(["Training"], TRAINING_CHAIN, spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], TRAINING_ACTIONS)
        self.assert_points_inside(task)
        self.assertEqual(task["simulated_spends"], [["training-start", "sent"]])
        report = self.report(task, "training")
        self.assertEqual(report["outcome"], "trained")
        self.assertEqual(report["notes"], ["inventory 22614->22613", "level 2->3"])
        self.assertEqual([e["event"] for e in self.events("training")], ["intent", "result", "verified"])
        self.assertEqual(task["node_trace"][-1], "SC.Training.FinishClean")
        # Same game day again: the run heads for training again instead of stopping.
        again = self.run_task(["Training"], [HOME], spend=["training-start"])
        task, = again["tasks"]
        self.assertEqual(task["simulated_actions"][:1], TRAINING_ACTIONS[:1])
        notes = [n for r in task["reports"] if r["kind"] == "training" for n in r.get("notes", [])]
        self.assertNotIn("trained_earlier_today", notes)

    def test_training_without_spend_permission_reads_and_leaves(self):
        summary = self.run_task(["Training"], [HOME, IDOL, LIST, ZERO, LIST, IDOL, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"], ["home-idol", "idol-training", "training-select-card",
                                                     "nav-back", "nav-back", "nav-back"])
        self.assertEqual(self.report(task, "training")["outcome"], "spend_not_enabled")
        self.assertEqual(self.events("training"), [])

    def test_training_extra_material_dialog_is_cancelled_without_spending(self):
        summary = self.run_task(["Training"], [ZERO, ONE, EXTRA_MATERIAL, ONE, LIST, IDOL, HOME],
                                spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"][:3], ["training-bronze-plus", "training-submit", "training-confirm-cancel"])
        self.assertEqual(task["simulated_spends"], [])
        report = self.report(task, "training")
        self.assertEqual(report["outcome"], "confirm_mismatch")
        self.assertIn("material_not_sole_bronze_x1", report["notes"])
        self.assertEqual(self.events("training"), [])

    def test_training_unknown_result_stops_the_run_and_a_later_run_trains_from_the_screen(self):
        summary = self.run_task(["Training"], [ZERO, ONE, CONFIRM, RESULT_UNKNOWN], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_spends"], [["training-start", "sent"]])
        self.assertEqual(task["simulated_actions"][-1], "training-start")  # nothing clicked after the spend
        self.assertEqual(self.report(task, "training")["outcome"], "spend_uncertain")
        self.assertEqual([e["event"] for e in self.events("training")], ["intent", "uncertain"])
        # The earlier uncertain spend only warns; a later run heads to the training page again.
        again = self.run_task(["Training"], [HOME], spend=["training-start"])
        task, = again["tasks"]
        self.assertEqual(task["simulated_actions"][:1], TRAINING_ACTIONS[:1])
        notes = [n for r in task["reports"] if r["kind"] == "training" for n in r.get("notes", [])]
        self.assertIn("uncertain_spend_earlier_today", notes)

    def test_training_great_success_is_verified(self):
        summary = self.run_task(["Training"], [ZERO, ONE, CONFIRM, GREAT, AFTER, LIST, IDOL, HOME], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], TRAINING_ACTIONS[3:])
        self.assert_points_inside(task)
        self.assertEqual(task["simulated_spends"], [["training-start", "sent"]])
        self.assertEqual(self.report(task, "training")["outcome"], "trained")
        events = self.events("training")
        self.assertEqual([e["event"] for e in events], ["intent", "result", "verified"])
        self.assertEqual((events[1]["title"], events[1]["kind"]), ("トレーニング大成功", "great_success"))

    def seed_training_log(self, records):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "training-2026-10-02.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")

    def test_training_resolves_the_uncertain_spend_from_the_open_great_success_dialog(self):
        # Today's real records, rebased onto the replay chain's numbers (AFTER shows Bronze 22613, Lv3).
        self.seed_training_log([dict(r, inventory=22614, level=2) for r in TODAY_LOG])
        summary = self.run_task(["Training"], [GREAT, AFTER, LIST, IDOL, HOME], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["training-result-close", "nav-back", "nav-back", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        report = self.report(task, "training")
        self.assertEqual(report["outcome"], "trained")
        self.assertEqual(report["notes"], ["resolved_great_success", "inventory 22614->22613", "level 2->3"])
        self.assertEqual([e["event"] for e in self.events("training")],
                         ["intent", "uncertain", "resolved_great_success", "verified"])
        again = self.run_task(["Training"], [HOME], spend=["training-start"])
        task, = again["tasks"]
        self.assertEqual(task["simulated_actions"][:1], TRAINING_ACTIONS[:1])
        notes = [n for r in task["reports"] if r["kind"] == "training" for n in r.get("notes", [])]
        self.assertNotIn("trained_earlier_today", notes)

    def test_training_open_intent_without_the_dialog_trains_from_the_screen(self):
        self.seed_training_log(TODAY_LOG)
        summary = self.run_task(["Training"], [HOME], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:1], TRAINING_ACTIONS[:1])
        notes = [n for r in task["reports"] if r["kind"] == "training" for n in r.get("notes", [])]
        self.assertIn("uncertain_spend_earlier_today", notes)
        self.assertEqual([e["event"] for e in self.events("training")][:2], ["intent", "uncertain"])

    def test_training_result_with_an_unaccepted_title_is_uncertain(self):
        summary = self.run_task(["Training"], [ZERO, ONE, CONFIRM, RESULT_STEM_ONLY], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"][-1], "training-start")  # 閉じる never pressed
        report = self.report(task, "training")
        self.assertEqual(report["outcome"], "spend_uncertain")
        self.assertIn("unknown_result_title", report["notes"])
        self.assertEqual([e["event"] for e in self.events("training")], ["intent", "uncertain"])
        # The open dialog with that title is neither resolved nor closed by any task.
        again = self.run_task(["Training", "MissionStatus"], [RESULT_STEM_ONLY], spend=["training-start"])
        self.assertEqual([t["simulated_actions"] for t in again["tasks"]], [[], []])
        self.assertEqual([e["event"] for e in self.events("training")], ["intent", "uncertain"])

    def test_training_started_on_an_unaccepted_result_never_closes_it(self):
        summary = self.run_task(["Training"], [RESULT_STEM_ONLY], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"], [])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(self.report(task, "training")["outcome"], "unexpected_result_dialog")
        self.assertEqual([e["event"] for e in self.events("training")], ["unexpected_result"])

    def test_training_started_on_a_known_result_closes_without_spending(self):
        summary = self.run_task(["Training"], [GREAT, AFTER, LIST, IDOL, HOME], spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")  # no matching intent, so do not claim a verified training
        self.assertEqual(task["simulated_actions"], ["training-result-close", "nav-back", "nav-back", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(self.report(task, "training")["outcome"], "unexpected_result_dialog")
        self.assertEqual([e["event"] for e in self.events("training")], ["unexpected_result"])

    def test_later_tasks_close_a_left_over_training_result(self):
        """Today's live failure: Training left the 大成功 dialog open, so MatchSkip/MissionStatus were blocked."""
        summary = self.run_task(["MissionStatus"], [GREAT, AFTER, LIST, IDOL, HOME, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:4], ["training-result-close", "nav-back", "nav-back", "nav-back"])
        self.assert_points_inside(task)
        self.assertEqual([(e["event"], e["kind"]) for e in self.events("training")],
                         [("result_closed_elsewhere", "great_success")])

    def test_mission_status_leaves_a_training_detail_without_the_lv50_row(self):
        """2026-10-11 live: MissionStatus started on 大崎甜花's detail (no Lv.50 ticket row, Bronze label at y 139) and
        stopped unknown_or_unhandled_state with 0 inputs; the navigation variant now backs out of any training detail."""
        if not (ROOT / DETAIL_NO_LV50).is_file():
            self.skipTest("缺少 2026-10-11 甜花训练详情原生帧 28")
        summary = self.run_task(["MissionStatus"], [DETAIL_NO_LV50, LIST, IDOL, HOME, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:3], ["nav-back", "nav-back", "nav-back"], task)
        self.assert_points_inside(task)

    def test_training_unchanged_inventory_after_result_is_unverified(self):
        summary = self.run_task(["Training"], [ZERO, ONE, CONFIRM, RESULT, ZERO, LIST, IDOL, HOME],
                                spend=["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        report = self.report(task, "training")
        self.assertEqual(report["outcome"], "result_unverified")
        self.assertIn("inventory_not_minus_1", report["notes"])
        self.assertEqual([e["event"] for e in self.events("training")], ["intent", "result", "uncertain"])

    # ---- GradeFes
    def seed_legacy_log(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "grade-2026-10-02.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in LEGACY_LOG), encoding="utf-8")

    def test_grade_tension_then_live_start_and_never_clicks_after_the_spend(self):
        summary = self.run_task(["GradeFes"], GRADE_CHAIN + [TENSION, HOME], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS)
        self.assert_points_inside(task)
        self.assertEqual(task["simulated_spends"], [["grade-tension-confirm", "sent"]] * 2)
        report = self.report(task, "grade")
        # Home shows no rights: the spend is not verified (only Fes main with rights - 1 is success).
        self.assertEqual((task["status"], report["outcome"]), ("failed", "spend_uncertain"))
        events = self.events("grade")
        self.assertEqual([e["event"] for e in events], ["tension_intent", "tension_decided", "intent", "uncertain"])
        self.assertEqual((events[2]["stage"], events[2]["rights_before"]), (gt.LIVE_STAGE, [20, 20]))
        self.assertEqual(events[3]["reason"], "home_observed_rights_not_verified")
        unit = [d for d in report["decisions"] if d.get("stage") == "Unit"][0]
        self.assertEqual((unit["title"], unit["rights"]), ("参加ユニット(Normal)", "20/20"))
        live = [d for d in report["decisions"] if d.get("stage") == gt.LIVE_STAGE][0]
        self.assertEqual((live["title"], live["rights"], live["reasons"]), ("参加ユニット(Normal)", "20/20", []))
        # The uncertain live is never retried; with the sampling cap at 1 nothing else starts today. Since 2026-10-20 a
        # spent open attempt is first looked for on screen (a still running live is taken over): only non-consuming
        # navigation to Fes main, which refuses (samples_cap_reached) and goes back.
        again = self.run_task([{"name": "GradeFes", "option": {"Grade采样每日上限": "1"}}],
                              [HOME, FESMENU, FESMAIN_NOTHING, HOME_NO_BADGE], spend=["grade-tension-confirm"])
        task, = again["tasks"]
        self.assertEqual(task["simulated_spends"], [])
        report = self.report(task, "grade")
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "nothing_to_do"), task)
        self.assertIn("samples_cap_reached", " ".join(map(str, report["notes"] + report["decisions"])))

    def test_grade_on_fes_main_after_a_reported_live_leaves_with_nothing_to_do(self):
        """2026-10-03 14:46 live: GradeFes began on Fes main (rights 20/20) after the 11:09 live had shown its LIVE REPORT
        (logged live_report, then live_unfinished on the unknown ranking page). That live counts as today's play: Fes main
        must not join; one nav-back reaches verified Home and the task succeeds with nothing_to_do (the live run itself
        failed only because the host refused the click: session_time_cap_reached)."""
        self.root.mkdir(parents=True, exist_ok=True)
        flow = [{"event": "tension_intent", "difficulty": "Normal", "rights_before": [20, 20]},
                {"event": "tension_decided"}, {"event": "intent", "stage": gt.LIVE_STAGE, "rights_before": [20, 20]},
                {"event": "live_turn"}, {"event": "live_report", "rank": 1},
                {"event": "live_unfinished", "reason": "fes_main_not_reached_after_report"}]
        (self.root / "grade-2026-10-02.jsonl").write_text(
            "".join(json.dumps({**e, "game_day": "2026-10-02"}) + "\n" for e in flow), encoding="utf-8")
        state = gt.log_state(self.events("grade"))
        self.assertEqual((state.attempts, state.verified, state.reported, state.played), (1, 0, 1, 1))
        for spend in ((), ["grade-tension-confirm"]):
            summary = self.run_task(["GradeFes"], [FESMAIN_NOTHING, HOME_NO_BADGE], spend=spend)
            task, = summary["tasks"]
            self.assertEqual((task["status"], task["simulated_actions"]), ("succeeded", ["nav-back"]), task)
            self.assert_points_inside(task)
            report = self.report(task, "grade")
            self.assertEqual(report["outcome"], "nothing_to_do")
            self.assertIn("runs_done_today", report["notes"])
            self.assertEqual(task["node_trace"][-1], "SC.Grade.FinishClean")
            self.assertEqual(task["simulated_spends"], [])

    def test_grade_difficulty_with_the_money_campaign_badge(self):
        """2026-10-03 live failure: the 「獲得マニー 2倍」 badges and shifted circles must not block the Normal tap."""
        chain = [HOME, FESMENU, FESMAIN, MODE, DIFF_MONEY2X, UNIT, TENSION, HOME]
        summary = self.run_task(["GradeFes"], chain, spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS)
        self.assert_points_inside(task)
        report = self.report(task, "grade")
        difficulty, = [d for d in report["decisions"] if d.get("stage") == "Difficulty"]
        self.assertEqual((difficulty["title"], difficulty["prompt"], difficulty["rights"], difficulty["reasons"]),
                         (gt.DIFFICULTY_TITLE, gt.ONE_RIGHT, "20/20", []))

    def test_grade_unknown_follow_up_is_uncertain_with_saved_frames(self):
        summary = self.run_task(["GradeFes"], GRADE_CHAIN + [TENSION, TENSION], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS)
        self.assertEqual(self.report(task, "grade")["outcome"], "spend_uncertain")
        self.assertEqual([e["event"] for e in self.events("grade")], ["tension_intent", "tension_decided", "intent",
                                                                      "uncertain"])
        self.assertTrue(list((self.root / "samples").glob("grade-after-live-*/frame-*.png")))
        again = self.run_task(["GradeFes"], [TENSION, HOME], spend=["grade-tension-confirm"])
        task, = again["tasks"]  # ライブ開始 was sent: never resumed, never pressed again
        self.assertEqual(task["simulated_actions"], [])
        # 2026-10-20: a spent open attempt is looked for on screen first (live takeover); a TensionDecided screen is not
        # that live and is refused without any click (the sent ライブ開始 counts toward the attempt limit).
        report = self.report(task, "grade")
        self.assertEqual(report["outcome"], "unexpected_tension_decided")
        self.assertEqual(task["simulated_spends"], [])

    def test_grade_tension_confirm_without_effect_is_uncertain_and_never_repeated(self):
        summary = self.run_task(["GradeFes"], GRADE_CHAIN + [UNIT], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS[:-1])
        self.assertEqual((task["status"], self.report(task, "grade")["outcome"]), ("failed", "tension_uncertain"))
        self.assertEqual([e["event"] for e in self.events("grade")], ["tension_intent", "tension_uncertain"])
        self.assertTrue(list((self.root / "samples").glob("grade-after-tension-*/frame-*.png")))
        again = self.run_task(["GradeFes"], [UNIT, DIFF, MODE, HOME], spend=["grade-tension-confirm"])
        task, = again["tasks"]  # an open attempt: 参加ユニット is left (non-consuming back), テンション決定 never again
        self.assertNotIn("grade-tension-confirm", task["simulated_actions"])
        self.assertEqual(task["simulated_spends"], [])

    def test_grade_resumes_the_legacy_uncertain_attempt_from_tension_decided(self):
        self.seed_legacy_log()
        summary = self.run_task(["GradeFes"], [TENSION, HOME], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["grade-tension-confirm"])  # ライブ開始 only, never テンション決定
        self.assertEqual(task["simulated_spends"], [["grade-tension-confirm", "sent"]])
        report = self.report(task, "grade")
        self.assertIn(gt.NOT_SPENT_TENSION_DECIDED, report["notes"])
        events = self.events("grade")
        self.assertEqual([e["event"] for e in events], ["intent", "uncertain", "resolved", "tension_decided", "intent",
                                                        "uncertain"])
        self.assertEqual((events[2]["result"], events[2]["rights_observed"]), (gt.NOT_SPENT_TENSION_DECIDED, [20, 20]))
        self.assertEqual(events[4]["stage"], gt.LIVE_STAGE)
        self.assertEqual(report["outcome"], "spend_uncertain")  # Home after ライブ開始: rights not verified

    def test_grade_resume_without_spend_permission_only_resolves(self):
        self.seed_legacy_log()
        summary = self.run_task(["GradeFes"], [TENSION])
        task, = summary["tasks"]
        self.assertEqual((task["simulated_actions"], task["refused_clicks"]), ([], []))
        self.assertEqual((task["status"], self.report(task, "grade")["outcome"]), ("failed", "spend_not_enabled"))
        self.assertEqual([e["event"] for e in self.events("grade")], ["intent", "uncertain", "resolved", "tension_decided"])
        # A later run with permission resumes the (now resolved) attempt once; no second resolution.
        summary = self.run_task(["GradeFes"], [TENSION, HOME], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["grade-tension-confirm"])
        self.assertEqual([e["event"] for e in self.events("grade")][4:], ["tension_decided", "intent", "uncertain"])

    def test_grade_legacy_attempt_off_screen_is_left_and_the_run_starts_from_home(self):
        # An earlier unresolved attempt only warns; Home shows it was left, this run starts afresh.
        self.seed_legacy_log()
        summary = self.run_task(["GradeFes"], [HOME], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:1], ["home-fes"])
        self.assertIn("open_attempt_left:legacy_uncertain", self.report(task, "grade")["notes"])
        self.assertEqual([e["event"] for e in self.events("grade")][:3], ["intent", "uncertain", "observed_home"])
        self.assertIsNone(gt.log_state(self.events("grade")).open)

    def test_grade_tension_decided_without_an_open_attempt_never_clicks(self):
        summary = self.run_task(["GradeFes"], [TENSION], spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual((task["simulated_actions"], task["simulated_spends"]), ([], []))
        self.assertEqual((task["status"], self.report(task, "grade")["outcome"]), ("failed", "unexpected_tension_decided"))
        self.assertEqual(self.events("grade"), [])

    def test_grade_wrong_difficulty_on_unit_page_goes_back_without_spending(self):
        interface = copy.deepcopy(launcher.load_interface())
        summary = self.run_task([{"name": "GradeFes", "option": {"Grade难度": "Hard"}}],
                                [HOME, FESMENU, FESMAIN, MODE, DIFF, UNIT, DIFF, MODE, HOME],
                                spend=["grade-tension-confirm"], interface=interface)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"], ["home-fes", "fes-grade-entry", "grade-join-open", "grade-mode-grade",
                                                     "grade-difficulty-hard", "nav-back", "nav-back", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        report = self.report(task, "grade")
        self.assertEqual(report["outcome"], "unit_mismatch")
        self.assertIn("unit_title_not_configured_difficulty", report["notes"])
        self.assertEqual(self.events("grade"), [])

    def test_grade_rights_reserve_and_missing_permission_never_join(self):
        summary = self.run_task([{"name": "GradeFes", "option": {"Grade参加权保留": "10"}}], [FESMAIN_6, HOME],
                                spend=["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("succeeded", ["nav-back"]))
        self.assertEqual(self.report(task, "grade")["outcome"], "skipped_rights")
        summary = self.run_task(["GradeFes"], [HOME, FESMENU, FESMAIN, HOME])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("failed", ["home-fes", "fes-grade-entry", "nav-back"]))
        self.assertEqual(self.report(task, "grade")["outcome"], "spend_not_enabled")

    # ---- MatchSkip
    def test_match_samples_the_skip_dialog_and_cancels(self):
        # The legacy fixed-frame sampler (scan_sample=False); the default full survey is in tests/test_match_scan.py.
        # 2026-10-11: 只采样 defaults to No; the sampler is chosen explicitly
        summary = self.run_task([{"name": "MatchSkip", "option": {"Match只采样": "Yes"}}], [HOME, FESMENU, MATCH_HELP, MATCH, SKIP, MATCH, HOME],
                                match_options={"scan_sample": False})
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["home-fes", "fes-match-entry", "popup-help-close", "match-skip-open",
                                                     "match-skip-cancel", "nav-back"])
        self.assert_points_inside(task)
        report = self.report(task, "match")
        self.assertEqual(report["outcome"], "sampled")
        self.assertIn("rights 1/20", report["notes"])
        self.assertEqual([e["event"] for e in self.events("match")], ["rights_observed", "sampled"])
        self.assertEqual(len(self.events("match")[1]["frames"]), mt.SAMPLE_FRAMES)

    def test_task_started_on_another_task_page_navigates_home_first(self):
        # 2026-10-11 user: the game was left on トレーニングアイドル選択 before a daily run. Every daily Main ends with
        # the guarded SC.Nav.BackOne (known pages only, stop guards first), so MatchSkip backs out to home itself.
        summary = self.run_task([{"name": "MatchSkip", "option": {"Match只采样": "Yes"}}], [LIST, IDOL, HOME, FESMENU, MATCH_HELP, MATCH, SKIP, MATCH, HOME],
                                match_options={"scan_sample": False})
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"][:3], ["nav-back", "nav-back", "home-fes"])
        self.assert_points_inside(task)

    def test_match_unrecognised_dialog_stops_without_clicking(self):
        summary = self.run_task([{"name": "MatchSkip", "option": {"Match只采样": "Yes"}}, "Diagnostics"], [MATCH, MATCH_DIMMED])
        match, diagnostics = summary["tasks"]
        self.assertEqual(match["status"], "failed")
        self.assertEqual(match["simulated_actions"], ["match-skip-open"])
        self.assertEqual(self.report(match, "match")["outcome"], "skip_open_no_effect")
        self.assertEqual(diagnostics["status"], "succeeded")  # the queue continues

    def skip_option(self, count="1", **extra):
        # 2026-10-09: the replays exercise the overall-latest rule; the UI default (最新Vo道具关卡) stops on the
        # unverified reward table before any plus (tests/test_ui_task_settings.py).
        return {"name": "MatchSkip", "option": {"Match只采样": "No", "Match次数": count, "Match关卡": "最新（全部关卡）", **extra}}

    def test_match_skip_needs_permission_and_enough_rights(self):
        summary = self.run_task([self.skip_option()], [MATCH, HOME])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("failed", ["nav-back"]))
        self.assertEqual(self.report(task, "match")["outcome"], "skip_not_enabled")
        summary = self.run_task([self.skip_option("20")], [MATCH, HOME], spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_actions"]), ("failed", ["nav-back"]))
        self.assertEqual(self.report(task, "match")["outcome"], "rights_insufficient")  # Match main shows 1/20

    def test_match_skips_the_latest_stage_once_and_never_clicks_after_the_spend(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, SKIP_BOTTOM, SKIP_R3_1, SYN + "match-confirm-a2s6.png", MATCH],
                                spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-scroll-down", "match-skip-plus-6",
                                                     "match-skip-review", "match-skip-confirm"])
        self.assert_points_inside(task)
        self.assertEqual(task["simulated_spends"], [["match-skip-confirm", "sent"]])
        # The result screen is unknown (Match main still shows 1/20, not 20 - 1): uncertain, this run stops.
        self.assertEqual((task["status"], self.report(task, "match")["outcome"]), ("failed", "spend_uncertain"))
        events = self.events("match")
        self.assertEqual([e["event"] for e in events], ["rights_observed", "prepared", "intent", "uncertain"])
        self.assertEqual(events[1]["stage"], events[2]["stage"])  # the two-step flow: prepared at the first dialog, intent at 「スキップ開始」
        self.assertEqual((events[2]["stage"], events[2]["count"], events[2]["rights_before"]), ("A2-S6", 1, [1, 20]))
        self.assertEqual(events[2]["progress"]["cycle_id"], "offline-fixture-20261002")
        self.assertRegex(events[2]["progress_frame_digest"], r"^[0-9a-f]{64}$")
        # A later run only notes it and plans from the rights it reads on screen.
        again = self.run_task([self.skip_option()], [HOME], spend=["match-skip-confirm"])
        task, = again["tasks"]
        self.assertEqual(task["simulated_actions"][:1], ["home-fes"])
        self.assertIn("unresolved_skip_batches_1", self.report(task, "match")["notes"])

    def test_match_final_review_for_another_stage_never_starts_the_skip(self):
        # Native 2026-10-11 final 「スキップ確認」 (エリア5 ステージ15, 1/19) against the prepared A2-S6 plan: Review is
        # pressed (non-consuming) but 「スキップ開始」 is never clicked and nothing is spent.
        # The rejected review is closed with its キャンセル, then クリア + キャンセル close the list.
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, SKIP_BOTTOM, SKIP_R3_1,
                                                       P + "sample-live-20261011/68-match-final-confirm.png", SKIP_R3_1,
                                                       SKIP_BOTTOM, MATCH],
                                spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:7], ["match-skip-open", "match-skip-scroll-down", "match-skip-plus-6",
                                                         "match-skip-review", "match-skip-review-cancel",
                                                         "match-skip-clear", "match-skip-cancel"])
        self.assertNotIn("match-skip-confirm", task["simulated_actions"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(self.report(task, "match")["outcome"], "counter_mismatch")
        self.assertNotIn("intent", [e["event"] for e in self.events("match")])

    def test_match_missing_trusted_progress_never_opens_or_spends(self):
        # 2026-10-14 user: 「最新」 = the last stage of the complete, fresh Skip list, read in the (non-consuming) Skip
        # dialog itself (SkipListLatestPlanner) instead of area navigation. Without a readable list it never spends.
        summary = self.run_task([self.skip_option()], [MATCH_FULL, HOME], spend=["match-skip-confirm"],
                                match_options={"progress": None, "progress_reader": None})
        task, = summary["tasks"]
        self.assertTrue(set(task["simulated_actions"]) <= {"match-skip-open", "match-skip-cancel", "nav-back"},
                        task["simulated_actions"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertNotIn(self.report(task, "match")["outcome"], ("skipped", "success", None),
                         self.report(task, "match"))

    def test_match_unreadable_live_cycle_cannot_use_a_manual_progress_profile(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, HOME], spend=["match-skip-confirm"],
                                match_options={"progress_reader": None})
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(self.report(task, "match")["outcome"], "progress_unreadable")

    def test_match_cycle_change_in_dialog_cancels_before_preparation(self):
        import numpy as np
        from PIL import Image
        main_digest = tc.image_digest(np.array(Image.open(ROOT / MATCH_FULL).convert("RGB"))[:, :, ::-1].copy())

        def changed_cycle(context, image):
            # The production reader is deliberately absent; this only simulates a reset boundary offline.
            cycle = "offline-fixture-20261002" if tc.image_digest(image) == main_digest else "new-offline-cycle"
            return mp.ProgressObservation(cycle, "ready", tc.image_digest(image), time.monotonic())

        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, MATCH, HOME], spend=["match-skip-confirm"],
                                match_options={"progress_reader": changed_cycle})
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(self.report(task, "match")["outcome"], "progress_cycle_changed")

    def test_match_counter_mismatch_clears_and_cancels(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, SKIP_BOTTOM, SKIP_R3_MISMATCH, SKIP_BOTTOM, MATCH, HOME],
                                spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-scroll-down", "match-skip-plus-6",
                                                     "match-skip-clear", "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual((task["status"], self.report(task, "match")["outcome"]), ("failed", "counter_mismatch"))

    def test_match_unchanged_count_never_repeats_plus_or_spends(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, SKIP_BOTTOM, SKIP_BOTTOM, MATCH, HOME],
                                spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-scroll-down", "match-skip-plus-6",
                                                     "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual((task["status"], self.report(task, "match")["outcome"]), ("failed", "counter_mismatch"))

    def test_match_unchanged_scroll_never_repeats_wheel_or_spends(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, SKIP, MATCH, HOME], spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-scroll-down", "match-skip-cancel",
                                                     "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual((task["status"], self.report(task, "match")["outcome"]), ("failed", "scroll_no_effect"))

    def test_match_latest_not_skippable_stops_without_falling_back(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP, SKIP_GREY6, MATCH, HOME], spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-scroll-down", "match-skip-cancel",
                                                     "nav-back"])
        self.assertEqual(self.report(task, "match")["outcome"], "latest_not_skippable")
        self.assertEqual([e["event"] for e in self.events("match")], ["rights_observed"])

    def test_match_changed_rights_cancel_before_any_preparation_or_spend(self):
        summary = self.run_task([self.skip_option()], [MATCH, SKIP, MATCH, HOME], spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual((task["status"], self.report(task, "match")["outcome"]),
                         ("failed", "rights_changed_or_insufficient"))

    def test_match_initial_nonzero_selection_clears_and_cancels_without_reopening_fes(self):
        summary = self.run_task([self.skip_option()], [MATCH_FULL, SKIP_R3_1, SKIP_BOTTOM, MATCH, HOME],
                                spend=["match-skip-confirm"])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-clear", "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual((task["status"], self.report(task, "match")["outcome"]), ("failed", "counts_not_zero"))

    def test_go_home_closes_a_left_over_skip_dialog(self):
        summary = self.run_task(["MissionStatus"], [SKIP, MATCH, HOME, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"][:2], ["match-skip-cancel", "nav-back"])

    def test_guards_match_their_own_screens_only(self):
        """Each B5 guard on the native frames of every stage: exactly the expected hits."""
        if not (ROOT / TENSION_PALE).is_file():
            self.skipTest("缺少 2026-10-11 Grade 心情原生帧 38")
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink

        guards = {"SC.Training.Detail.Guard": {ZERO, AFTER}, "SC.Training.One.Guard": {ONE},
                  "SC.Training.Confirm.Guard": {CONFIRM}, "SC.Training.ConfirmShell.Guard": {CONFIRM, EXTRA_MATERIAL},
                  "SC.Training.Result.Guard": {RESULT}, "SC.Training.ResultAny.Guard": {RESULT, GREAT, RESULT_STEM_ONLY},
                  "SC.Fes.Menu.Guard": {FESMENU},
                  "SC.Grade.Mode.Guard": {MODE}, "SC.Grade.Difficulty.Guard": {DIFF, DIFF_MONEY2X, DIFF_PULSE},
                  "SC.Grade.Unit.Guard": {UNIT},
                  "SC.Grade.Tension.Guard": {TENSION, TENSION_PALE} | {TENSION_RUN + f"frame-{i:03d}.png" for i in (2, 17, 33, 52)},
                  "SC.Nav.MatchSkip.Guard": {SKIP, SKIP_BOTTOM, SKIP_R3_1}}
        frames = [HOME, IDOL, LIST, ZERO, ONE, CONFIRM, EXTRA_MATERIAL, RESULT, AFTER, FESMENU, FESMAIN, MODE, DIFF, UNIT,
                  DIFF_MONEY2X, DIFF_PULSE, REHEARSAL,
                  FESMAIN_6, MATCH_HELP, MATCH, SKIP, SKIP_BOTTOM, SKIP_R3_1, RESULT_UNKNOWN, MATCH_DIMMED, TENSION, GREAT,
                  RESULT_STEM_ONLY,
                  TENSION_PRESS, TENSION_PALE] + [TENSION_RUN + f"frame-{i:03d}.png" for i in (2, 17, 33, 52)] + [
                  P + "in_app_browser/20261001-daily-research/daily-v6-MatchSkip-positive.png",
                  P + "shop-20261002-04-confirm1-frames/frame-001.png", P + "shop-20261002-04-confirm1-frames/frame-024.png"]
        from tools.replay_screenshots import read_png
        images = [read_png(ROOT / f) for f in frames]
        found = {}

        class Probe(CustomAction):
            def run(self, context, argv):
                for frame, image in zip(frames, images):
                    for guard in guards:
                        detail = context.run_recognition(guard, image)
                        if detail is not None and detail.hit:
                            found.setdefault(guard, set()).add(frame)
                return True

        scenario = launcher.ReplayScenario(images[:1], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        resource = tasker._sc_keepalive[0]
        self.assertTrue(resource.register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        self.assertEqual({g: found.get(g, set()) for g in guards}, guards)

    def test_read_only_mode_never_clicks(self):
        summary = self.run_task(["Training", "GradeFes", "MatchSkip"], [HOME], simulate=False)
        self.assertEqual([t["simulated_clicks"] for t in summary["tasks"]], [[], [], []])
        self.assertEqual(summary["input_mode"], launcher.READ_ONLY)


@unittest.skipUnless(HAVE_MAA and frames_present(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 B5 实机帧")
class B5LivePathTests(unittest.TestCase):
    """--live code path (WebView2 gate + LiveSpendAuthority + HMAC grant) against a fake host; no socket."""

    @classmethod
    def setUpClass(cls):
        if not private_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def live(self, tasks, frames, spend):
        from agent.core import webview_controller as wc

        root = PRIVATE / "run_logs" / "_unittest_b5_live"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        host = FakePipelineHost([(f, "click") for f in frames])
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                   "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                   "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
                   "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        # The 5 s frame/grant TTLs are wall-clock: on a busy machine a slow OCR round outlives them and the replay retries
        # (frame_stale_before_permit) or loses its grant, which desyncs the fixed frame chain. The fake host's frames are
        # static, so freshness is replayed on a 10x slower clock; the TTL logic itself is covered by test_pipeline_frame_guard.
        slow_clock = lambda: time.monotonic() / 10
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)},
                                   wc.SpendGate(spend, max_per_action=3, clock=slow_clock), wc.AuditLog(root), host.send,
                                   policy=RISK_POLICY, clock=slow_clock,
                                   limits=wc.ClickLimits(run_max=launcher.DEFAULT_MAX_CLICKS,
                                                         task_max=launcher.DEFAULT_TASK_MAX_CLICKS))  # no caps
        options = {k: {"log_root": root, "now": lambda: NOW, "sample_root": root / "samples"}
                   for k in ("training", "grade", "match")}
        options["match"]["scan_sample"] = False  # the gate readiness replays use the fixed-frame sampler
        tasks = [{"name": t, "option": {"训练卡": tt.CARD_IDS[tt.CARD_ID]}} if t == "Training" else t for t in tasks]  # chosen card
        summary = launcher.run({"tasks": tasks, "allow_spend": spend}, live=True, gate=gate, policy=RISK_POLICY,
                               summary_path=False, save_diagnostics=False, task_options=options, frame_guard_enabled=True)
        return summary, host

    def assert_single_grant(self, host, action, frame):
        self.assert_grants(host, action, [frame])

    def assert_grants(self, host, action, frames):
        from agent.core import webview_controller as wc
        spends = [r for r in host.requests if "spend_grant" in r]
        self.assertEqual(len(spends), len(frames))
        for spend, frame in zip(spends, frames):
            grant = spend["spend_grant"]
            self.assertEqual((spend["action"], grant["action"], grant["frame_sha256"]),
                             (action, action, spend["frame_sha256"]))
            self.assertEqual(spend["frame_sha256"], hashlib.sha256((ROOT / frame).read_bytes()).hexdigest())
            self.assertEqual(grant["hmac"], wc.sign_spend_grant("07" * 32, action, grant["frame_sha256"], grant["nonce"],
                                                                grant["expires_at"], grant["capture_seq"], grant["frame_binding_seq"]))

    def report_outcome(self, task):
        report, = [r for r in task["reports"] if r["kind"] == "grade"]
        return report["outcome"]

    def test_training_spend_reaches_the_host_only_with_a_frame_bound_grant(self):
        summary, host = self.live(["Training"], TRAINING_CHAIN, ["training-start"])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual([r["action"] for r in host.requests], TRAINING_ACTIONS)
        self.assert_single_grant(host, "training-start", CONFIRM)

    def test_grade_spend_reaches_the_host_only_with_a_frame_bound_grant(self):
        summary, host = self.live(["GradeFes"], GRADE_CHAIN + [TENSION, HOME], ["grade-tension-confirm"])
        task, = summary["tasks"]
        self.assertEqual(self.report_outcome(task), "spend_uncertain")  # Home after ライブ開始: rights not verified
        self.assertEqual([r["action"] for r in host.requests], GRADE_ACTIONS)
        # テンション決定 bound to the 参加ユニット frame, ライブ開始 to the TensionDecided frame.
        self.assert_grants(host, "grade-tension-confirm", [UNIT, TENSION])
        self.assertEqual(summary["live"]["task_caps"], {"GradeFes": None})   # no click caps

    # 2026-10-07 click-path readiness: the first supervised live step of each task runs WITHOUT allow_spend.
    def test_training_without_spend_permission_through_the_frame_guarded_gate(self):
        summary, host = self.live(["Training"], [HOME, IDOL, LIST, ZERO, LIST, IDOL, HOME], [])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual([r["action"] for r in host.requests], ["home-idol", "idol-training", "training-select-card",
                                                               "nav-back", "nav-back", "nav-back"])
        self.assertFalse([r for r in host.requests if "spend_grant" in r])
        report, = [r for r in task["reports"] if r["kind"] == "training"]
        self.assertEqual(report["outcome"], "spend_not_enabled")

    def test_grade_without_spend_permission_through_the_frame_guarded_gate(self):
        summary, host = self.live(["GradeFes"], [HOME, FESMENU, FESMAIN, HOME], [])
        task, = summary["tasks"]
        self.assertEqual([r["action"] for r in host.requests], ["home-fes", "fes-grade-entry", "nav-back"])
        self.assertFalse([r for r in host.requests if "spend_grant" in r])
        self.assertEqual(self.report_outcome(task), "spend_not_enabled")

    def test_match_sample_mode_through_the_frame_guarded_gate(self):
        """Readiness check only: sample mode cancels the skip dialog, no grant. The option
        is explicit because a bare task name inherits the daily preset (Match只采样=No, i.e. an actual skip)."""
        summary, host = self.live([{"name": "MatchSkip", "option": {"Match只采样": "Yes"}}], [HOME, FESMENU, MATCH_HELP, MATCH, SKIP, MATCH, HOME], [])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual([r["action"] for r in host.requests], ["home-fes", "fes-match-entry", "popup-help-close",
                                                               "match-skip-open", "match-skip-cancel", "nav-back"])
        self.assertFalse([r for r in host.requests if "spend_grant" in r])


if __name__ == "__main__":
    unittest.main()
