"""B5d: Grade Fes live play — pure policy, screen parsers, runtime stage machine and offline replay of the manual live."""
import dataclasses
import datetime as dt
import json
import shutil
import unittest
from pathlib import Path

from agent.core import fes_live_policy as pol
from agent.core import fes_live_reader as reader
from agent.core import grade_task as gt
from agent.core import task_common as tc
from tools import run_tasks as launcher
from tests.test_task_launcher import HAVE_MAA, TABLE, pipeline_nodes, private_ready

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets/private"
NOW = dt.datetime(2026, 10, 2, 8, 0, tzinfo=dt.timezone.utc)
LIVE_DIR = PRIVATE / "grade-live-manual-20261003-frames"
AUTO_DIR = PRIVATE / "grade-live-auto-20261003-frames"  # B5i: auto run 2026-10-03 09:45 (resume, then TURN 3 stop)
# B5j: LIVE REPORT of the 10:16 takeover run (1位, ★88, 20,277, マニー 405 with the 2倍 badge, ミッション報酬 row) after
# the OK tap that had no effect, and that run's deduplicated samples (frame 023 onwards: LIVE REPORT).
REPORT_FRAME = AUTO_DIR / "report.png"
REPORT_SAMPLES = PRIVATE / "run_logs/samples/grade-after-takeover-20261003T001609006246Z"
MISSION_POPUP = PRIVATE / "nav-v25-reward-ack2-20261002-frames/frame-023.png"
# B5k: auto run 2026-10-03 11:09 (full win, 1位 20,751): after the first LIVE REPORT OK a second LIVE REPORT page (ranking
# list) appeared (browser_tasks/20261003-111148-904428/frame-001.png); that run's samples 035–037 are the first report
# (037 settled), 038–045 the ranking page.
RANKING_FRAME = AUTO_DIR / "report-ranking.png"
RANKING_SAMPLES = PRIVATE / "run_logs/samples/grade-after-live-20261003T010915810093Z"
REPORT_1109 = RANKING_SAMPLES / "frame-037.png"
OK_BUTTON = (1097, 630, 162, 70)  # pink OK button on REPORT_FRAME (x 1097..1258, y 630..699)
SYN = PRIVATE / "synthetic-b5d"
FES_MAIN_19 = SYN / "fes-main-19-of-20.png"
P = "dev_assets/private/"
GRADE_CHAIN = [P + x for x in ("shop-20261002-01-home-frames/frame-001.png", "grade-run-02-home-fes-frames/frame-026.png",
                               "grade-run-03c-entry-frames/frame-008.png", "grade-run-04b-join-frames/frame-007.png",
                               "grade-run-06-diff-readonly-frames/frame-001.png", "grade-run-07c-normal-frames/frame-024.png",
                               "run_logs/samples/grade-after-spend-20261002T133404973940Z/frame-070.png")]
FES_MAIN = P + "grade-run-03c-entry-frames/frame-008.png"
FES_MAIN_6 = P + "manual-capture-kept-20261002/shop-grade/031.png"
# B5h: 「フェス再開」 over the dimmed Fes main (19/20) after a host restart (native frame 2026-10-03 06:22) and the
# synthetic variants with one anchor erased (built by build_resume_negatives).
FES_RESUME = P + "manual-capture-kept-20261002/fes-resume-popup-20261003.png"
RESUME_NEGATIVES = {name: SYN / f"fes-resume-no-{name}.png" for name in ("title", "text", "end", "resume")}
RESUME_ERASE = {"title": (358, 193, 190, 45), "text": (490, 310, 282, 54), "end": (505, 453, 70, 35),
                "resume": (705, 453, 70, 35)}
NAV_ACTIONS = ["home-fes", "fes-grade-entry", "grade-join-open", "grade-mode-grade", "grade-difficulty-normal",
               "grade-tension-confirm", "grade-tension-confirm"]


def judge(attribute, rank, interest=0, alive=True):
    return pol.Judge(attribute, alive, interest if alive else None, rank, {1: 2.0, 2: 1.5, 3: 1.0}[rank])


def state(turn=1, judges=None, cards=None, memory=10):
    judges = judges or (judge("Vo", 3), judge("Da", 2), judge("Vi", 1))
    cards = cards or (pol.Card(1, "Vo"), pol.Card(2, "Vo", link=True), pol.Card(3, "Vo"))
    return pol.TurnState(turn, tuple(judges), tuple(cards), memory)


# ======================================================================== pure policy
class PolicyTests(unittest.TestCase):
    def test_turn_one_of_the_manual_live(self):
        """TURN 1/7 of the manual live (all Vo cards): the top-trend judge, ties broken towards the higher trend."""
        decision = pol.decide(state())
        self.assertEqual((decision.kind, decision.slot, decision.target), ("card", 2, "Vi"))  # LINK breaks the tie
        self.assertEqual(decision.scores["card1"], {"Vo": 2.0, "Da": 1.5, "Vi": 2.0})
        self.assertIn("card 2 to Vi", pol.explain(state(), decision))

    def test_attribute_match_and_trend(self):
        cards = (pol.Card(1, "Da"), pol.Card(2, "Vo"), pol.Card(3, None))
        decision = pol.decide(state(cards=cards))
        self.assertEqual((decision.slot, decision.target), (1, "Da"))  # Da ×2 on 流行2 ×1.5 = 3.0
        cards = (pol.Card(1, "All"), pol.Card(2, "Vi"), pol.Card(3, "Vo"))
        decision = pol.decide(state(cards=cards))
        self.assertEqual((decision.slot, decision.target), (1, "Vi"))  # All = match everywhere; slot 1 < slot 2

    def test_finished_judges_and_remaining_interest(self):
        judges = (judge("Vo", 3, 60), judge("Da", 2, 0), judge("Vi", 1, alive=False))
        decision = pol.decide(state(2, judges, (pol.Card(1, "Vo"), pol.Card(2, "Da"), pol.Card(3, "Vi"))))
        self.assertEqual((decision.slot, decision.target), (2, "Da"))   # Da 2×1.5=3.0 vs Vo 2×1×1.3=2.6
        self.assertEqual(decision.scores["card3"]["Vi"], 0.0)
        judges = (judge("Vo", 3, 90), judge("Da", 2, 0), judge("Vi", 1, alive=False))
        decision = pol.decide(state(2, judges, (pol.Card(1, "Vo"), pol.Card(2, "Da"), pol.Card(3, "Vi"))))
        self.assertEqual((decision.slot, decision.target), (2, "Da"))   # 2×1.45=2.9 < 3.0: still Da
        params = {**pol.PARAMS, "remaining_weight": 1.0}
        decision = pol.decide(state(2, judges, (pol.Card(1, "Vo"), pol.Card(2, "Da"), pol.Card(3, "Vi"))), params)
        self.assertEqual((decision.slot, decision.target), (1, "Vo"))   # tunable: 2×1.9=3.8 (near finish)

    def test_memory_rule(self):
        self.assertEqual(pol.memory_rule(state(memory=80)), (False, "gauge_not_full"))
        self.assertEqual(pol.memory_rule(state(turn=2, memory=100)), (False, "too_early"))
        self.assertEqual(pol.memory_rule(state(turn=7, memory=100))[0], True)
        near = (judge("Vo", 3, 10), judge("Da", 2, 0), judge("Vi", 1, 55))
        self.assertEqual(pol.memory_rule(state(4, near, memory=100)), (True, "top_trend_Vi_at_55"))
        two = (judge("Vo", 3, 60), judge("Da", 2, 55), judge("Vi", 1, 20))
        self.assertEqual(pol.memory_rule(state(4, two, memory=100)), (True, "two_judges_near_finish"))
        far = (judge("Vo", 3, 10), judge("Da", 2, 0), judge("Vi", 1, 20))
        self.assertEqual(pol.memory_rule(state(4, far, memory=100)), (False, "no_judge_near_finish"))
        decision = pol.decide(state(4, near, memory=100))
        self.assertEqual((decision.kind, decision.slot, decision.target), ("memory", None, "Vi"))
        self.assertEqual(pol.decide(state(4, near, memory=None)).kind, "card")

    def test_invalid_states_are_refused(self):
        bad_ranks = (judge("Vo", 1), judge("Da", 1), judge("Vi", 3))
        self.assertIn("trend_ranks_not_a_permutation", pol.decide(state(judges=bad_ranks)).reasons)
        unreadable = (pol.Judge("Vo", True, None, 3, 1.0), judge("Da", 2), judge("Vi", 1))
        self.assertIn("alive_judge_interest_unreadable", pol.validate(state(judges=unreadable)))
        done = (judge("Vo", 3, alive=False), judge("Da", 2, alive=False), judge("Vi", 1, alive=False))
        self.assertEqual(pol.decide(state(judges=done)).kind, "none")
        self.assertIn("turn_out_of_range", pol.validate(state(turn=8)))
        self.assertIn("cards_incomplete", pol.validate(state(cards=(pol.Card(1, "Vo"),))))

    def test_target_from_predictions(self):
        s = state()
        plan = pol.decide(s)
        # Manual live turn 1: every judge predicted 100 % -> the 流行1 judge (Last Appeal 12 stars × 2).
        self.assertEqual(pol.choose_target(s, plan, {"Vo": 100, "Da": 100, "Vi": 100})[0], "Vi")
        # Turn 2: Vi finished, Vo/Da predicted 100 % -> Da (9 × 1.5 > 6 × 1).
        s2 = state(2, (judge("Vo", 3), judge("Da", 2), judge("Vi", 1, alive=False)))
        target, why = pol.choose_target(s2, pol.decide(s2), {"Vo": 100, "Da": 100, "Vi": None})
        self.assertEqual((target, why["rule"]), ("Da", "predicted_finish"))
        target, why = pol.choose_target(s2, pol.decide(s2), {"Vo": 80, "Da": 30, "Vi": None})
        self.assertEqual((target, why["rule"]), ("Vo", "predicted_gain"))   # 80 × 1 > 30 × 1.5
        target, why = pol.choose_target(s, plan, {"Vo": None, "Da": None, "Vi": None})
        self.assertEqual((target, why["rule"]), (plan.target, "planned_target_kept"))
        target, why = pol.choose_target(s, plan, {"Vo": 0, "Da": 0, "Vi": 0})
        self.assertEqual((target, why["rule"]), (plan.target, "no_predicted_gain_planned_target_kept"))


# ======================================================================== parsers
class ReaderTests(unittest.TestCase):
    def test_text_parsers(self):
        self.assertEqual([reader.parse_turn(t) for t in ("117", "1/7", "217", "717", "7/7", "817", "17", None, "1/8")],
                         [1, 1, 2, 7, 7, None, 1, None, None])
        self.assertEqual([reader.parse_percent(t) for t in ("0%", "100%", "27%", "x%", None)], [0, 100, 27, None, None])
        # B5i: partial interest has one decimal (auto run 2026-10-03: 「69.2%」 over the red segmented bar).
        self.assertEqual([reader.parse_percent(t) for t in ("69.2%", "0.5%", "69.25%", "69,2%", "1000%")],
                         [69.2, 0.5, None, None, None])
        self.assertTrue(reader.is_finished("審查終了") and reader.is_finished("審査終了") and not reader.is_finished("0%"))
        self.assertEqual([reader.parse_multiplier(t) for t in ("★2倍", "★1.5倍", None, "Vo")], [2.0, 1.5, None, None])
        self.assertEqual(reader.ranks_from_multipliers({"Vo": None, "Da": 1.5, "Vi": 2.0}),
                         {"Vo": (3, 1.0), "Da": (2, 1.5), "Vi": (1, 2.0)})
        self.assertIsNone(reader.ranks_from_multipliers({"Vo": None, "Da": None, "Vi": 2.0}))
        self.assertEqual([reader.swap_hint_slot(x) for x in (340, 570, 800, 690, None)], [1, 2, 3, None, None])
        self.assertEqual([reader.parse_int(t) for t in ("21,874pt", "1位", "90", "218", "x", None)],
                         [21874, 1, 90, 218, None, None])
        self.assertEqual(reader.card_label("PLUS+"), (False, True))

    def test_judges_from_texts(self):
        judges, reasons = reader.read_judges({"Vo": "0%", "Da": "0%", "Vi": "審查終了"},
                                             {"Vo": None, "Da": "★1.5倍", "Vi": "★2倍"})
        self.assertEqual(reasons, [])
        self.assertEqual(judges[2], pol.Judge("Vi", False, None, 1, 2.0))
        _, reasons = reader.read_judges({"Vo": "0%", "Da": None, "Vi": "100%"}, {"Vo": None, "Da": None, "Vi": "★2倍"})
        self.assertEqual(reasons, ["trend_multipliers_unreadable", "judge_Da_unreadable", "judge_Vi_unreadable"])

    def test_turn3_judges_with_decimal_interest_and_finished_judge(self):
        """B5i, TURN 3/7 of the auto run: Vo 0 %, Da 69.2 % (流行2), Vi 審査終了 (流行1): Vi excluded from every choice."""
        judges, reasons = reader.read_judges({"Vo": "0%", "Da": "69.2%", "Vi": "審查終了"},
                                             {"Vo": None, "Da": "★1.5倍", "Vi": "★2倍"})
        self.assertEqual(reasons, [])
        self.assertEqual(judges[1], pol.Judge("Da", True, 69.2, 2, 1.5))
        s = state(3, judges, (pol.Card(1, "Vo", plus=True), pol.Card(2, "Vo", link=True), pol.Card(3, "Vo", plus=True)),
                  memory=89)
        decision = pol.decide(s)
        self.assertEqual((decision.kind, decision.slot, decision.target), ("card", 2, "Da"))  # 1×1.5×1.346×1.25 > 2.5
        self.assertTrue(all(v["Vi"] == 0.0 for k, v in decision.scores.items() if k.startswith("card")))
        self.assertEqual(pol.memory_rule(s), (False, "gauge_not_full"))                 # 89 %: never before 100
        self.assertEqual(pol.memory_rule(dataclasses.replace(s, memory_gauge=100)), (True, "top_trend_Da_at_69.2"))
        target, why = pol.choose_target(s, decision, {"Vo": 35.5, "Da": 100, "Vi": None})
        self.assertEqual((target, why["rule"]), ("Da", "predicted_finish"))
        # A dual 「Vo./Da.」 icon matches both judges.
        self.assertEqual(pol.attribute_factor("Vo/Da", judges[1]), pol.PARAMS["attribute_match"])
        self.assertEqual(pol.attribute_factor("Vo/Da", pol.Judge("Vi", True, 0, 1, 2.0)), pol.PARAMS["attribute_mismatch"])

    def test_turn_number_needs_agreeing_readings(self):
        class V:
            def __init__(self, det, recs):
                self.det, self.recs = det, list(recs)

            def text(self, image, roi):
                return self.det

            def rec(self, pixels, pattern):
                return self.recs.pop(0)

        import numpy as np
        image = np.full((720, 1280, 3), 255, np.uint8)
        image[20:40, 660:672] = 40  # some ink in the digit crop
        self.assertEqual(reader.read_turn_number(V(None, ["317", "3/7", None]), image)[0], 3)
        self.assertEqual(reader.read_turn_number(V("317", [None, None, None]), image)[0], 3)
        self.assertIsNone(reader.read_turn_number(V("317", ["117", None, None]), image)[0])   # disagreement
        self.assertIsNone(reader.read_turn_number(V(None, [None, None, None]), image)[0])

    def test_icon_hue(self):
        import numpy as np
        for bgr, expected in (((150, 40, 240), "Vo"), ((230, 120, 30), "Da"), ((30, 160, 250), "Vi"),
                              ((90, 200, 60), None), ((200, 200, 200), None)):
            image = np.zeros((720, 1280, 3), np.uint8)
            image[630:695, 900:975] = bgr
            self.assertEqual(reader.icon_attribute(image, 3)[0], expected, bgr)
        image = np.zeros((720, 1280, 3), np.uint8)
        image[630:695, 900:975] = (150, 40, 240)
        image[630:650, 900:975] = (230, 120, 30)   # a Da. band on a Vo. icon (Dance担当 card, auto run 2026-10-03)
        self.assertEqual(reader.icon_attribute(image, 3)[0], "Vo/Da")


# ======================================================================== runtime with a fake screen
class FakeVision:
    hits, texts = set(), {}

    def __init__(self, context):
        pass

    def hit(self, node, image):
        return node in type(self).hits

    def text(self, image, roi):
        return type(self).texts.get(tuple(roi))

    def number(self, image, crop, kind, pattern):
        return type(self).texts.get("rights")


class FakeContext:
    swap_x = None
    arrow_y = None  # centre y of the post-memory arrow (SC.Grade.Live.Anchor.MemoryArrow)

    def run_recognition(self, node, image):
        if node == "SC.Grade.Live.Anchor.MemoryArrow":
            class Arrow:
                hit = type(self).arrow_y is not None
                box = (133, (type(self).arrow_y or 0) - 41, 84, 82)
            return Arrow()

        class Detail:
            hit = type(self).swap_x is not None
            box = (type(self).swap_x or 0, 700, 100, 16)
        return Detail()


WAIT = {"SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.TrendWord", "SC.Grade.Live.Anchor.SpeedOff"}


def wait_texts(turn=1, vi="0%"):
    texts = {tuple(reader.TURN_BOX): f"{turn}/7", tuple(reader.MEMORY_BOX): "10%"}
    for attribute, value in (("Vo", "0%"), ("Da", "0%"), ("Vi", vi)):
        texts[tuple(reader.JUDGE_TEXT[attribute])] = value
    texts[tuple(reader.MULTIPLIER_TEXT["Da"])] = "★1.5倍"
    texts[tuple(reader.MULTIPLIER_TEXT["Vi"])] = "★2倍"
    texts[(reader.CARD_X[1], reader.CARD_LABEL_H[0], 110, reader.CARD_LABEL_H[1])] = "LINK"
    return texts


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from agent.core import grade_custom
        self.root = PRIVATE / "run_logs" / "_unittest_b5d_runtime"
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.time = [0.0]
        rt = grade_custom.GradeRuntime(log_root=self.root, now=lambda: NOW, vision=FakeVision,
                                       sample_root=self.root / "samples", clock=lambda: self.time[0])
        rt.run = gt.GradeRun(gt.GradeConfig(live_play=True), rt.log)
        rt.run.phase, rt.run.intent = "awaiting_result", {"difficulty": "Normal", "rights_before": [20, 20],
                                                          "stage": gt.LIVE_STAGE}
        rt.run.saver = tc.FrameSaver("x", self.root / "samples", lambda: NOW, 0)
        rt._start_watch(rt.run, "live", 0)
        self.rt, self.ctx = rt, FakeContext()
        FakeContext.swap_x = FakeContext.arrow_y = None
        FakeVision.hits, FakeVision.texts = set(WAIT), wait_texts()
        self.image = np.zeros((720, 1280, 3), np.uint8)
        self.image[630:695, 440:515] = (150, 40, 240)
        self.image[630:695, 670:745] = (150, 40, 240)
        self.image[630:695, 900:975] = (150, 40, 240)

    def assert_resource_rights(self, current):
        from agent.core import reading_store, report_readings
        from agent.core.task_actions import ReportSink
        store = reading_store.ReadingStore(self.root / "readings.json", now=lambda: NOW, flush_interval_s=0.0)
        store.record("resource.fes_rights", {"count": 20, "max": 20}, source="old_home",
                     observed_at=(NOW - dt.timedelta(minutes=9)).isoformat())
        self.rt.sink = ReportSink()
        self.rt.sink.add_listener(report_readings.ReportTap(store))
        self.rt.report()
        reading = store.latest("resource.fes_rights")
        self.assertEqual((reading["value"], reading["source"]),
                         ({"count": current, "max": 20}, "task_report:grade"))
        self.assertEqual(reading["observed_at"], NOW.isoformat())

    def test_resumed_rehearsal_verified_rights_refresh_existing_resource_record(self):
        self.round()  # resumed live was observed before its completed Fes-main return
        run = self.rt.run
        run.config = dataclasses.replace(run.config, mode="rehearsal", rehearsal_runs=1)
        run.intent = {"resumed": True, "rights_observed": [19, 20], "difficulty": "Normal"}
        run.note({"rights_before": [20, 20]})
        run.live.seen = True
        run.live.ok_pressed = True
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "19/20"}
        self.assertEqual(self.round(), "finished")
        self.assertEqual(run.outcome, "played")
        self.assert_resource_rights(19)

    def round(self, *steps, dt_=1.0):
        """One recognition round like Maa's ``next`` list: the watch step first (counts the round), then the given
        live steps in order until the first hit (later steps are not evaluated: reported as False)."""
        self.time[0] += dt_
        self.image[0, 0] = (self.image[0, 0] + 1) % 255  # a new screenshot
        if self.rt.watch(self.ctx, self.image, {}):
            return "finished"
        out = []
        for s in steps:
            out.append(False if any(out) else self.rt.live(self.ctx, self.image, s))
        return out

    def rounds_until_finished(self, *steps, limit=15):
        for count in range(1, limit + 1):
            result = self.round(*steps)
            if result == "finished":
                return count
            self.assertFalse(any(result), (count, result))  # never another tap meanwhile
        self.fail("not finished")

    CARDS = ({"step": "speed"}, {"step": "memory"}, {"step": "card", "slot": 1}, {"step": "card", "slot": 2},
             {"step": "card", "slot": 3})

    def test_one_turn_speed_then_card_then_judge_then_timing(self):
        self.assertEqual(self.round(*self.CARDS), [False] * 5)        # first reading: not yet stable
        self.assertEqual(self.round(*self.CARDS), [True, False, False, False, False])  # 倍速 once
        FakeVision.hits = FakeVision.hits - {"SC.Grade.Live.Anchor.SpeedOff"}           # 倍速 took effect
        self.assertEqual(self.round(*self.CARDS), [False, False, False, True, False])  # card 2 (LINK) to Vi
        self.assertEqual(self.rt.run.live.speed_result, "changed")
        FakeContext.swap_x = 570
        for attribute, value in (("Vo", "100%"), ("Da", "100%"), ("Vi", "100%")):
            FakeVision.texts[tuple(reader.JUDGE_TEXT[attribute])] = value
        judges = [{"step": "judge", "judge": a} for a in ("Vo", "Da", "Vi")]
        self.assertEqual(self.round(*judges), [False] * 3)            # predictions must repeat once
        self.assertEqual(self.round(*judges), [False, False, True])
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.AppealLabel"}
        self.assertEqual(self.round({"step": "timing"}), [True])
        self.assertEqual(self.round({"step": "timing"}), [False])  # one tap per turn
        events = [e["event"] for e in self.rt.log.events()]
        self.assertEqual(events, ["live_turn", "live_target"])
        self.assertEqual(self.rt.log.events()[0]["decision"]["slot"], 2)
        # Animation, then TURN 2/7 with Vi finished: a new decision on a stable reading.
        FakeVision.hits, FakeContext.swap_x = {"SC.Grade.Live.Screen.Guard"}, None
        self.assertEqual(self.round(*self.CARDS), [False] * 5)
        FakeVision.hits, FakeVision.texts = set(WAIT), wait_texts(2, "審查終了")
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS), [False, False, False, True, False])  # card 2 to Vo
        self.assertEqual(self.rt.run.live.turn, 2)

    MEMORY_WAIT = {"SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.MemoryArrow"}
    GO = tuple({"step": "memory_go", "judge": a} for a in ("Vo", "Da", "Vi"))

    def memory_judged(self, target="Vi"):
        """TURN 6/6: memory appeal and its judge already tapped (as in the 2026-10-22 live)."""
        live = self.rt._live()
        live.seen, live.turn, live.judge_target = True, 6, target
        live.plan = pol.Decision("memory", None, target, {"memory": "last_turn"}, ("memory:last_turn",))
        live.turns.append({"turn": 6, "kind": "memory", "slot": None, "plan": target, "target": target})
        self.rt._commit(live, "judge_tapped")
        return live

    def test_memory_appeal_waits_for_one_tap_on_the_arrowed_judge(self):
        """2026-10-22: after the memory film the turn screen (no 流行, arrow at the judge) waits for one tap anywhere."""
        live = self.memory_judged()
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}  # Ready / Good-Bad film: nothing to tap
        self.assertEqual(self.round(*self.GO, *self.CARDS), [False] * 8)
        FakeVision.hits, FakeContext.arrow_y = set(self.MEMORY_WAIT), 440
        self.assertEqual(self.round(*self.GO, *self.CARDS), [False] * 8)        # first frame of the waiting screen
        self.assertEqual(self.round(*self.GO, *self.CARDS), [False, False, True] + [False] * 5)  # Vi (arrowed)
        self.assertEqual(live.stage, "memory_go_tapped")
        self.assertEqual(self.round(*self.GO), [False] * 3)                     # once; a re-tap only after 3 s
        self.assertEqual(self.round(*self.GO, dt_=2.0), [False] * 3)            # unchanged >= 3 s: first frame
        self.assertEqual(self.round(*self.GO), [False, False, True])            # second frame: one re-tap
        self.assertIn((6, "memory_go"), live.retaps)
        self.assertEqual(self.round(*self.GO, dt_=4.0), [False] * 3)            # never a third tap
        events = [e["event"] for e in self.rt.log.events()]
        self.assertEqual(events, ["live_memory_go", "live_retap"])
        self.assertEqual(self.round(*self.GO, dt_=5.0), "finished")             # still unchanged: stop
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "memory_go_tapped_no_effect"))

    def test_memory_go_then_end_screens_and_wrong_arrow_stops(self):
        live = self.memory_judged()
        FakeVision.hits, FakeContext.arrow_y = set(self.MEMORY_WAIT), 440
        self.round(*self.GO, dt_=2.0)
        self.assertEqual(self.round(*self.GO), [False, False, True])
        FakeVision.hits, FakeContext.arrow_y = {"SC.Grade.Live.Screen.Guard"}, None  # Appeal Start / end animation
        for _ in range(12):
            self.assertEqual(self.round(*self.GO, *self.CARDS), [False] * 8)
        self.assertIsNone(self.rt.run.outcome)
        self.assertEqual(live.stage, "memory_go_tapped")
        # The arrow at another judge than the chosen one: never tapped, the live stops.
        self.setUp()
        self.memory_judged("Vi")
        FakeVision.hits, FakeContext.arrow_y = set(self.MEMORY_WAIT), 100
        self.assertEqual(self.round(*self.GO, dt_=3.0), [False, False, False])
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "memory_arrow_differs"))

    FILM = ({"step": "film"},)

    def film_image(self, right2="good"):
        """The memory film: cyan frame edges, a Good tile under the frame, ``right2`` at the right edge (BGR)."""
        import numpy as np
        image = np.zeros((720, 1280, 3), np.uint8)
        colour = {"good": (230, 140, 250), "bad": (220, 190, 170)}
        for name, x in reader.FILM_TILES.items():
            image[280:410, x:x + 220] = colour[right2 if name == "right2" else "good"]
        for (x, y, w, h), _ in reader.FILM_EDGES.values():
            image[y + 2:y + h - 2, x + 2:x + w - 2] = (240, 230, 60)
        return image

    def test_film_reader_on_synthetic_frames(self):
        self.assertEqual(reader.read_film(self.film_image("bad")),
                         {"left": "good", "center": "good", "right1": "good", "right2": "bad"})
        dim = self.film_image() // 4                                  # 「Ready!!」 over the darkened strip
        dim[:, :, :][self.film_image().sum(axis=2) == 530] = (240, 230, 60)
        self.assertIsNone(reader.read_film(dim))
        no_edge = self.film_image()
        no_edge[444:462, 515:765] = (230, 140, 250)                   # bottom edge missing
        self.assertIsNone(reader.read_film(no_edge))
        self.assertIsNone(reader.read_film(self.image))

    def test_memory_film_waits_for_a_good_tile_at_the_right_edge(self):
        live = self.memory_judged()
        self.image = self.film_image("bad")
        self.assertEqual(self.round(*self.FILM, *self.GO), [False] * 4)     # film frame 1: Bad arriving
        self.image = self.film_image("good")
        self.assertEqual(self.round(*self.FILM, *self.GO, dt_=0.5), [True, False, False, False])
        self.assertEqual(live.stage, "film_tapped")
        events = self.rt.log.events()
        self.assertEqual([(e["event"], e["rule"], e["film_rounds"]) for e in events],
                         [("live_memory_film", "lookahead_good", 2)])
        for _ in range(3):                                                  # never a second film tap
            self.assertEqual(self.round(*self.FILM, *self.GO, dt_=0.3), [False] * 4)
        self.assertEqual(len(live.film_after), 3)
        # Then the arrow screen: the usual one tap on the chosen judge.
        self.image[:] = 0
        FakeVision.hits, FakeContext.arrow_y = set(self.MEMORY_WAIT), 440
        self.round(*self.GO, dt_=2.0)
        self.assertEqual(self.round(*self.GO), [False, False, True])
        self.assertEqual(live.stage, "memory_go_tapped")
        self.assertEqual(live.summary()["film"]["seen"][0]["right2"], "bad")

    def test_memory_film_taps_at_the_deadline(self):
        live = self.memory_judged()
        self.image = self.film_image("bad")
        self.assertEqual(self.round(*self.FILM, dt_=0.4), [False])
        self.assertEqual(self.round(*self.FILM, dt_=0.4), [False])
        self.assertEqual(self.round(*self.FILM, dt_=0.4), [True])            # third film frame: tap anyway
        self.assertEqual(self.rt.log.events()[-1]["rule"], "deadline")
        self.setUp()
        live = self.memory_judged()
        self.image = self.film_image("bad")
        self.round(*self.FILM, dt_=0.4)
        self.assertEqual(self.round(*self.FILM, dt_=1.1), [True])            # >= 1 s after the first film frame
        # No memory plan (a card turn) or not yet judged: never tapped.
        self.setUp()
        self.image = self.film_image("good")
        self.assertEqual(self.round(*self.FILM), [False])

    def test_dropped_memory_tap_is_repeated_once(self):
        """2026-10-24 live: TURN 5/6 unchanged after the memory tap (no predictions): one re-tap, then stop."""
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        self.rt.live_decide = lambda s: pol.Decision("memory", None, "Vi", {}, ("test",))
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS)[1], True)
        tapped_at = self.time[0]
        taps = self.rounds_with_taps(*self.CARDS)
        self.assertEqual([i for _, i in taps], [1])                     # the memory appeal, once more
        self.assertGreaterEqual(taps[0][0] - tapped_at, gt.LIVE_RETAP_SECONDS + 1)
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events], ["live_turn", "live_retap", "live_unfinished"])
        self.assertEqual((events[1]["tap"], events[1]["target"]), ("memory", "Vi"))
        self.assertEqual(events[-1]["reason"], "card_tapped_no_effect")
        # Predictions on the judges (the memory took effect): never re-tapped.
        self.setUp()
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        self.rt.live_decide = lambda s: pol.Decision("memory", None, "Vi", {}, ("test",))
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS)[1], True)
        FakeVision.texts[tuple(reader.JUDGE_TEXT["Vi"])] = "100%"
        self.assertEqual(self.rounds_with_taps(*self.CARDS), [])

    def rounds_with_taps(self, *steps, limit=30):
        """Rounds until the run finishes; [(time, step index)] of every tap meanwhile."""
        taps = []
        for _ in range(limit):
            result = self.round(*steps)
            if result == "finished":
                return taps
            taps += [(self.time[0], i) for i, hit in enumerate(result) if hit]
        self.fail("not finished")

    def test_tap_without_effect_is_repeated_once_then_stops(self):
        """B5h: a dropped card tap (screen unchanged, same TURN and cards on two fresh frames) is re-tapped once."""
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS)[3], True)
        tapped_at = self.time[0]
        taps = self.rounds_with_taps(*self.CARDS)
        self.assertEqual(len(taps), 1)                                  # exactly one re-tap, of the same card
        retap_at, index = taps[0]
        self.assertEqual(index, 3)
        self.assertGreaterEqual(retap_at - tapped_at, gt.LIVE_RETAP_SECONDS + 1)  # after the delay + a second frame
        self.assertGreater(self.time[0] - retap_at, gt.LIVE_STUCK_SECONDS)       # stuck timer restarted at the re-tap
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "card_tapped_no_effect"))
        self.assertTrue(self.rt.run.halted)
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events], ["live_turn", "live_retap", "live_unfinished"])
        self.assertEqual((events[1]["tap"], events[1]["slot"], events[1]["turn"]), ("card", 2, 1))

    def test_card_retap_needs_the_same_state(self):
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS)[3], True)
        FakeVision.texts[tuple(reader.JUDGE_TEXT["Vo"])] = "10%"     # the state changed: never re-tapped
        self.assertEqual(self.rounds_with_taps(*self.CARDS), [])
        self.assertEqual(self.rt.run.notes[-1], "card_tapped_no_effect")

    def test_speed_tap_without_effect_is_repeated_once_then_the_card(self):
        taps = []
        for _ in range(12):
            result = self.round(*self.CARDS)
            taps += [(self.time[0], i) for i, hit in enumerate(result) if hit]
        speed = [t for t, i in taps if i == 0]
        self.assertEqual(len(speed), 2)                                 # 倍速 + one re-tap, never more
        self.assertGreaterEqual(speed[1] - speed[0], gt.LIVE_RETAP_SECONDS)
        self.assertEqual([i for _, i in taps], [0, 0, 3])               # then card 2, once
        self.assertGreaterEqual(taps[2][0] - speed[1], gt.LIVE_RETAP_SECONDS)
        self.assertEqual(self.rt.run.live.speed_result, "off_still_shown")
        self.assertEqual([e["tap"] for e in self.rt.log.events() if e["event"] == "live_retap"], ["speed"])

    def test_judge_tap_without_effect_is_repeated_once(self):
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS)[3], True)
        FakeContext.swap_x = 570
        for attribute in ("Vo", "Da", "Vi"):
            FakeVision.texts[tuple(reader.JUDGE_TEXT[attribute])] = "100%"
        judges = [{"step": "judge", "judge": a} for a in ("Vo", "Da", "Vi")]
        self.round(*judges)
        self.assertEqual(self.round(*judges), [False, False, True])
        taps = []
        for _ in range(6):
            result = self.round(*judges)
            taps += [i for i, hit in enumerate(result) if hit]
        self.assertEqual(taps, [2])                                     # Vi once more, on the unchanged target stage
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.AppealLabel"}
        self.assertEqual(self.round({"step": "timing"}), [True])
        self.assertEqual([(e["tap"], e.get("target")) for e in self.rt.log.events() if e["event"] == "live_retap"],
                         [("judge", "Vi")])

    def test_wrong_selected_card_skipped_turn_and_unreadable_state_stop(self):
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        self.round(*self.CARDS)
        self.round(*self.CARDS)
        FakeContext.swap_x = 800  # card 3 selected although card 2 was planned
        self.round({"step": "judge", "judge": "Vi"})
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "selected_card_differs"))

    def test_turn_skip_and_turn_cap(self):
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_speed="keep")
        FakeVision.texts = wait_texts(3)
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS)[3], True)          # the first turn seen may be any turn
        self.rt.run.live.stage = "timing_tapped"
        FakeVision.texts = wait_texts(5)
        self.round(*self.CARDS)
        self.assertEqual(self.rt.run.notes[-1], "turn_skipped")

    def test_unreadable_state_after_many_frames(self):
        FakeVision.texts = {}
        for _ in range(gt.LIVE_MAX_UNREADABLE + 1):
            self.round(*self.CARDS)
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "turn_state_unreadable"))

    REPORT = (("rank", "1位"), ("stars", "88"), ("score", "20,277pt"), ("money", "405"), ("held", "17679105"))

    def show_report(self, held="17679105", badge=True):
        texts = {tuple(reader.REPORT_BOXES[k]): v for k, v in self.REPORT}
        texts[tuple(reader.REPORT_BOXES["held"])] = held
        FakeVision.hits = {"SC.Grade.Live.Report.Guard", "SC.Grade.Live.Anchor.ReportMission"} | (
            {"SC.Grade.Live.Anchor.ReportMoney2x"} if badge else set())
        FakeVision.texts = texts

    def test_report_ok_after_stable_complete_readings_and_fes_main_verifies(self):
        """B5j: OK only when every reading incl. 所持マニー is unchanged for 3 s (the 10:16 OK came ~1 s too early)."""
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_report(held="17678700")                                # 所持マニー still counting up
        self.assertEqual(self.round({"step": "report"}), [False])
        self.show_report(held="17679105", badge=False)                   # badge sparkle: missed on this frame
        self.assertEqual(self.round({"step": "report"}), [False])        # changed: the 3 s restart
        self.show_report()
        self.assertEqual(self.round({"step": "report"}, dt_=1.0), [False])
        self.assertEqual(self.round({"step": "report"}, dt_=1.0), [False])
        self.assertEqual(self.round({"step": "report"}, dt_=1.0), [True])  # 3 s unchanged
        self.assertEqual(self.round({"step": "report"}), [False])        # OK once (re-tap only after 3 s)
        self.assertEqual(self.round({"step": "after_report"}), [True])
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "19/20"}
        self.assertEqual(self.round(), "finished")
        self.assertEqual(self.rt.run.outcome, "played")
        self.assert_resource_rights(19)
        report, verified = self.rt.log.events()[-2:]
        self.assertEqual((report["event"], report["held"], report["money_2x_badge"], report["mission_reward_row"],
                          report["settled"]), ("live_report", 17679105, True, True, "stable"))
        self.assertEqual((verified["event"], verified["live"]["report"]["score"]), ("verified", 20277))

    def test_report_ok_without_effect_is_repeated_once_then_stops(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_report()
        self.round({"step": "report"})
        taps = self.rounds_with_taps({"step": "report"}, limit=20)
        self.assertEqual(len(taps), 2)                                   # OK, then exactly one re-tap
        self.assertGreaterEqual(taps[1][0] - taps[0][0], gt.LIVE_RETAP_SECONDS + 1)
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events], ["live_report", "live_retap", "live_unfinished"])
        self.assertEqual((events[1]["tap"], events[-1]["reason"]), ("report", "report_tapped_no_effect"))

    def test_after_report_unknown_screen_is_never_clicked_and_stops(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_report()
        for _ in range(4):
            self.round({"step": "report"})
        self.assertEqual(self.rt.run.live.stage, "report_tapped")
        FakeVision.hits, FakeVision.texts = set(), {}                    # e.g. a promotion screen (not sampled)
        for _ in range(int(gt.LIVE_AFTER_REPORT_SECONDS) + 5):
            self.image[0, 1] = (self.image[0, 1] + 37) % 255             # animated: the 20 s static rule never fires
            self.image[::8, ::8] = (self.image[::8, ::8].astype(int) + 9) % 255
            result = self.round({"step": "report"}, {"step": "after_report"})
            if result == "finished":
                break
            self.assertEqual(result[0], False)                         # never OK again
        # 2026-10-12: the report of the spent right was read and closed: played (counted), not retried; halted.
        self.assertEqual(self.rt.run.outcome, "played_reported")
        self.assertIn("fes_main_not_reached_after_report", self.rt.run.notes)
        self.assertTrue(self.rt.run.succeeded and self.rt.run.halted)
        self.assertEqual(self.rt.log.events()[-1]["reason"], "fes_main_not_reached_after_report")

    # ---- B5k: second LIVE REPORT page (ranking list) after the report OK
    def show_ranking(self, pt="20,277"):
        FakeVision.hits = {"SC.Grade.Live.Ranking.Guard"}
        FakeVision.texts = {tuple(reader._row_box(reader.RANKING_BADGE, 1)): "2位",
                            tuple(reader._row_box(reader.RANKING_BADGE, 2)): "3位", tuple(reader.RANKING_PT): pt}
        self.image[160:210, 1212:1216] = (199, 100, 248)  # pink frame edge of row 0 (BGR): the own row

    def press_report_ok(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_report()
        for _ in range(4):
            self.round({"step": "report"})
        self.assertEqual(self.rt.run.live.stage, "report_tapped")

    def test_ranking_page_after_report_ok_is_recorded_and_closed_once(self):
        self.press_report_ok()
        self.show_ranking()
        self.assertEqual(self.round({"step": "report"}), [False])        # first seen
        self.assertEqual(self.round({"step": "report"}), [False])        # 1 s: still settling
        self.assertEqual(self.round({"step": "report"}), [True])         # 2 s, same own row on two frames: OK
        self.assertEqual(self.round({"step": "report"}), [False])        # once (re-tap only after 3 s)
        self.assertEqual(self.round({"step": "after_report"}), [True])
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "19/20"}
        self.assertEqual(self.round(), "finished")
        self.assertEqual(self.rt.run.outcome, "played")
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events], ["live_report", "live_ranking", "verified"])
        ranking = events[1]
        self.assertEqual({k: ranking[k] for k in ("rank", "pt", "row", "pt_matches_score", "ranking_takeover")},
                         {"rank": 1, "pt": 20277, "row": 0, "pt_matches_score": True, "ranking_takeover": False})
        self.assertEqual(events[-1]["live"]["ranking"], {"rank": 1, "pt": 20277, "row": 0})

    def test_ranking_rank_needs_unscrolled_rows_and_one_own_row(self):
        self.show_ranking()
        vision = FakeVision(None)
        self.assertEqual(reader.read_ranking(vision, self.image)[0], {"rank": 1, "pt": 20277, "row": 0})
        FakeVision.texts[tuple(reader._row_box(reader.RANKING_BADGE, 1))] = "5位"   # scrolled list: rank unknown
        self.assertEqual(reader.read_ranking(vision, self.image)[0]["rank"], None)
        self.image[315:365, 1212:1216] = (199, 100, 248)                           # two pink rows: no own row
        self.assertEqual(reader.read_ranking(vision, self.image)[0], {"rank": None, "pt": None, "row": None})

    def test_ranking_ok_without_effect_is_repeated_once_then_stops(self):
        self.press_report_ok()
        self.show_ranking()
        taps = self.rounds_with_taps({"step": "report"}, limit=30)
        self.assertEqual(len(taps), 2)                                   # OK, then exactly one re-tap
        self.assertGreaterEqual(taps[1][0] - taps[0][0], gt.LIVE_RETAP_SECONDS)
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events], ["live_report", "live_ranking", "live_retap", "live_unfinished"])
        self.assertEqual((events[2]["tap"], events[-1]["reason"]), ("ranking", "ranking_tapped_no_effect"))

    def test_ranking_page_without_report_ok_stops(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_ranking()
        self.assertEqual(self.round({"step": "report"}), "finished")
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "ranking_without_report_ok"))

    def test_ranking_takeover_presses_only_the_ranking_ok(self):
        run = self.rt.run = gt.GradeRun(gt.GradeConfig(live_play=True), self.rt.log)
        run.phase = "active"
        self.show_ranking()
        self.assertTrue(self.rt.check(self.ctx, self.image, {"stage": "ranking_takeover"}))
        self.assertEqual((run.intent["stage"], run.live.stage, run.live.ok_pressed),
                         ("LiveRanking", "report_tapped", False))
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "19/20"}
        self.round()
        self.assertFalse(run.finished)                                  # ranking OK not pressed yet: never verified
        self.show_ranking()
        results = [self.round({"step": "report"}) for _ in range(3)]
        self.assertEqual(results[-1], [True])
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "19/20"}
        self.assertEqual(self.round(), "finished")
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events], ["ranking_takeover", "live_ranking", "verified"])
        self.assertEqual((events[1]["ranking_takeover"], events[1]["pt_matches_score"]), (True, None))
        state = gt.log_state(events)
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))

    def test_time_cap_counts_active_play_only(self):
        """B5j: waiting before the first live screen does not count; the cap is LIVE_WATCH_SECONDS of play."""
        FakeVision.hits = set()
        for _ in range(20):                                              # 600 s before any live screen (e.g. loading)
            self.image[::8, ::8] = (self.image[::8, ::8].astype(int) + 9) % 255
            self.assertNotEqual(self.round(dt_=30.0), "finished")
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}                 # the live animation (no decision needed)
        self.round()
        started = self.time[0]
        while self.time[0] - started < gt.LIVE_WATCH_SECONDS - 5:
            self.assertNotEqual(self.round(dt_=30.0), "finished")
        result = None
        for _ in range(3):
            result = self.round(dt_=5.0)
        self.assertEqual((result, self.rt.run.notes[-1]), ("finished", "live_time_cap"))
        self.assertGreater(self.time[0] - self.rt.run.live.started, gt.LIVE_WATCH_SECONDS + 500)  # waiting excluded

    def test_fes_main_without_minus_one_and_unknown_static_screen_stop(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "20/20"}
        self.assertGreater(self.rounds_until_finished(), gt.LIVE_STUCK_SECONDS)
        self.assertEqual(self.rt.run.notes[-1], "fes_main_rights_not_decremented")
        self.assertFalse(any("rights_after" in decision for decision in self.rt.run.decisions))

    def test_spend_error_after_the_live_started_is_live_unfinished(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.rt.spend_error(None, {})
        self.assertEqual(self.rt.run.outcome, "live_unfinished")
        self.assertIsNone(gt.resume_kind(gt.log_state([{"event": "tension_intent", "rights_before": [20, 20]},
                                                       {"event": "tension_decided"},
                                                       {"event": "intent", "stage": gt.LIVE_STAGE},
                                                       {"event": "live_unfinished"}])))

    def start_takeover(self, live_play=True, prior=True, phase="active"):
        run = self.rt.run = gt.GradeRun(gt.GradeConfig(live_play=live_play), self.rt.log)
        run.phase = phase
        if prior:  # the stopped run of 2026-10-03 09:45: its right was spent, the live left at TURN 3
            for event in ({"event": "tension_intent", "rights_before": [20, 20], "difficulty": "Normal"},
                          {"event": "tension_decided"}, {"event": "intent", "stage": gt.LIVE_STAGE},
                          {"event": "live_unfinished"}):
                self.rt._log(event)
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.TrendWord"}  # 2倍速 already ON
        FakeVision.texts = wait_texts(3, "審查終了")
        FakeVision.texts[tuple(reader.JUDGE_TEXT["Da"])] = "69.2%"
        return run, self.rt.check(self.ctx, self.image, {"stage": "takeover"})

    def test_live_takeover_at_turn_3_plays_on_without_navigation(self):
        """B5i: GradeFes starts on a waiting live turn (no フェス再開): take it over, never press ライブ開始."""
        run, taken = self.start_takeover()
        self.assertTrue(taken)
        self.assertEqual((run.phase, run.intent["takeover"], run.intent["stage"]), ("awaiting_result", True, "LiveTurn"))
        self.round(*self.CARDS)
        self.assertEqual(self.round(*self.CARDS), [False, False, False, True, False])   # card 2 to Da at TURN 3
        self.assertEqual((run.live.turn, run.live.plan.target, run.live.speed_result), (3, "Da", "already_on"))
        FakeVision.hits = {"SC.Nav.FesMain.Guard"}
        FakeVision.texts = {"rights": "18/20"}
        self.round()
        self.assertFalse(run.finished)                                  # no LIVE REPORT OK yet: never verified
        run.live.stage, run.live.ok_pressed = "report_tapped", True
        self.assertEqual(self.round(), "finished")
        self.assertEqual(run.outcome, "played")
        events = self.rt.log.events()
        self.assertEqual([e["event"] for e in events][4:], ["live_takeover", "live_turn", "verified"])
        self.assertEqual((events[4]["attempt_known"], events[-1]["rights_after"]), (True, [18, 20]))
        self.assert_resource_rights(18)
        self.assertIn("69.2%", events[5]["explain"])
        state = gt.log_state(events)
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))

    def test_live_takeover_refused_without_live_play_or_after_navigation(self):
        run, taken = self.start_takeover(live_play=False)
        self.assertFalse(taken)
        self.assertEqual((run.outcome, run.notes[-1], run.halted), ("live_takeover_refused", "live_play_disabled", True))
        run, taken = self.start_takeover(prior=False, phase="joined")
        self.assertFalse(taken)
        self.assertEqual((run.outcome, run.notes[-1]), ("live_takeover_refused", "grade_flow_already_started:joined"))
        self.assertNotIn("live_takeover", [e["event"] for e in self.rt.log.events()])

    def test_read_only_without_live_play(self):
        self.rt.run.config = gt.GradeConfig()
        self.assertEqual(self.round(*self.CARDS), "finished")
        self.assertEqual(self.rt.run.outcome, "manual_takeover")
        self.assertTrue(self.rt.run.halted)
        self.assertIsNone(self.rt.run.live)


class LiveFilesTests(unittest.TestCase):
    def test_report_ok_rect_extremes_and_outside_points(self):
        """Maa may select any target pixel; even the extremes must stay safely inside OK."""
        action = TABLE.get("live-report-ok")
        x, y, w, h = action.rect
        bx, by, bw, bh = OK_BUTTON
        for point in ((x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)):
            with self.subTest(point=point):
                # W.I.N.G. rects and the node-bound business bottom-right buttons (2026-10-11, on the button body)
                # overlap by design; within the Grade families the point is unique.
                self.assertEqual([a.id for a in TABLE.at(point, families=("grade-fes", "grade-live", "fes-menu", "nav", "popup"))],
                                 ["live-report-ok"])
                self.assertEqual([a.id for a in TABLE.at(point, node="SC.Grade.Live.Report.Ok")], ["live-report-ok"])
                self.assertTrue(bx + 40 <= point[0] < bx + bw - 40 and
                                by + 6 <= point[1] < by + bh - 6)
        # An OK-button pixel outside the action rect is still not an authorized report target.
        for point in ((x - 1, y), (x + w, y), (x, y - 1), (x, y + h)):
            with self.subTest(outside=point):
                self.assertFalse(action.contains(point))
                self.assertNotIn("live-report-ok", [a.id for a in TABLE.at(point)])

    def test_config_and_options(self):
        config = gt.GradeConfig.from_params({"live_play": True, "live_speed": "keep"})
        self.assertEqual((config.live_play, config.live_speed), (True, "keep"))
        self.assertFalse(gt.GradeConfig.from_params({}).live_play)  # new live input starts disabled
        for bad in ({"live_play": 1}, {"live_speed": "off"}, {"live_rerolls": 1}):
            with self.assertRaises(ValueError):
                gt.GradeConfig.from_params(bad)
        interface = launcher.load_interface()
        plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "GradeFes", "option": {"Grade自动演出": "开启",
                                                                                          "Grade倍速": "保持"}}]})
        self.assertEqual(plan.pipeline_override["SC.Grade.Config.Live"]["attach"], {"live_play": True})
        self.assertEqual(plan.pipeline_override["SC.Grade.Config.Speed"]["attach"], {"live_speed": "keep"})
        # 2026-10-11: the PI default plays the live (an observe-only run after a spent right ends uncertain); the
        # Python and Pipeline fallbacks stay off for runs without the launcher's option overrides.
        plan, = launcher.plan_tasks(interface, {"tasks": ["GradeFes"]})
        self.assertEqual(plan.pipeline_override["SC.Grade.Config.Live"]["attach"], {"live_play": True})
        plan, = launcher.plan_tasks(interface, {"tasks": [{"name": "GradeFes", "option": {"Grade自动演出": "关闭"}}]})
        self.assertEqual(plan.pipeline_override["SC.Grade.Config.Live"]["attach"], {"live_play": False})

    def test_live_actions_and_nodes(self):
        nodes = pipeline_nodes()
        live = [a for a in TABLE.actions if a.family == "grade-live"]
        self.assertEqual([a.id for a in live], ["live-card-1", "live-card-2", "live-card-3", "live-memory-appeal",
                                                "live-memory-film", "live-judge-vo", "live-judge-da", "live-judge-vi",
                                                "live-appeal-timing", "live-speed-toggle", "live-report-ok"]
                         # 2026-10-12: NEW RECORD overlay close, staged until the live run releases the table
                         + (["live-record-dismiss"] if "live-record-dismiss" in TABLE.ids else []))
        self.assertTrue(all(not a.consuming and a.wheel is None for a in live))
        # 再抽選 consumes rights / items: no Grade/daily action at all (W.I.N.G.'s 獲得報酬 再抽選 is its own opt-in family)
        self.assertNotIn("reroll", " ".join(i for i in TABLE.ids if not i.startswith("wing-")))
        # B5j: LIVE REPORT OK has its own action (the reused training-submit rect touched the button's left edge).
        self.assertEqual(TABLE.get("live-report-ok").nodes, ("SC.Grade.Live.Report.Ok",))
        self.assertNotIn("SC.Grade.Live.Report.Ok", TABLE.get("training-submit").nodes)
        x, y, w, h = TABLE.get("live-report-ok").rect
        # Well inside the pink OK button (x 1097..1258, y 630..699 on the 10:16 report): >= 40 px from its left edge.
        self.assertTrue(1137 <= x and x + w <= 1218 and 636 <= y and y + h <= 694)
        self.assertEqual(nodes["SC.Grade.Live.Report.Ok"]["target"], list(TABLE.get("live-report-ok").rect))
        self.assertEqual(nodes["SC.Training.Detail.Submit"]["target"], list(TABLE.get("training-submit").rect))
        self.assertIn("SC.Grade.Live.Step.AfterReport", nodes["SC.Grade.Live.Reported"]["any_of"])
        for anchor in ("ReportTitle", "ReportOk", "ReportScore"):  # manual frame 380 + auto run 10:16
            self.assertEqual(len(nodes[f"SC.Grade.Live.Anchor.{anchor}"]["template"]), 2)
        for anchor in ("ReportMoney2x", "ReportMission"):  # record only, never part of a guard
            self.assertFalse(any(f"SC.Grade.Live.Anchor.{anchor}" in node.get("all_of", []) + node.get("any_of", [])
                                 for node in nodes.values() if isinstance(node, dict)))
        self.assertGreaterEqual(launcher.DEFAULT_TASK_TIMEOUT_S, gt.LIVE_WATCH_SECONDS + 300)
        self.assertEqual((gt.LIVE_WATCH_SECONDS, gt.LIVE_AFTER_REPORT_SECONDS), (1800, 60))
        self.assertEqual(nodes["SC.Grade.Unit.Confirm"]["next"], ["SC.Grade.Watch.Tension", "SC.Grade.Live.Loop"])
        loop = nodes["SC.Grade.Live.Loop"]
        self.assertEqual(loop["next"][0], "SC.Grade.Watch.Concluded")
        self.assertEqual(loop["on_error"], ["SC.Grade.SpendError"])
        self.assertLessEqual(loop["timeout"], 120000)
        for name, node in nodes.items():
            if name.startswith("SC.Grade.Live.") and node.get("action") == "Click":
                self.assertEqual(node["enabled"], False)
                guard, step = node["all_of"]
                self.assertTrue(guard.endswith(".Guard") and step.startswith("SC.Grade.Live.Step."), name)
                self.assertGreaterEqual(node["post_delay"], 800)
        self.assertEqual(nodes["SC.Grade.Live.Speed"]["max_hit"], 2)  # one re-tap
        self.assertEqual(nodes["SC.Grade.Live.Report.Ok"]["max_hit"], 4)  # B5j/B5k: report + ranking OK, one re-tap each
        # B5k: the ranking page's OK is the same node / action (same button place): no new action, no new click node.
        self.assertEqual(nodes["SC.Grade.Live.Report.Ok"]["all_of"],
                         ["SC.Grade.Live.ReportOk.Guard", "SC.Grade.Live.Step.Report"])
        self.assertEqual(nodes["SC.Grade.Live.ReportOk.Guard"]["any_of"],
                         ["SC.Grade.Live.Report.AnyGuard", "SC.Grade.Live.Ranking.Guard"])
        self.assertEqual(nodes["SC.Grade.Live.Ranking.Guard"]["all_of"],
                         ["SC.Grade.Live.Anchor.ReportTitle", "SC.Grade.Live.Anchor.ReportOk",
                          "SC.Grade.Live.Anchor.RankingScoreDetail", "SC.Grade.Live.Anchor.RankingUnitDetail"])
        self.assertIn("SC.Grade.Live.Ranking.Guard", nodes["SC.Grade.Live.Reported"]["any_of"])
        # No click caps: per-node max_hit (scaled per planned life) bounds the flow.
        self.assertIsNone(launcher.LIVE_TASK_CLICK_CAPS["GradeFes"])
        self.assertIsNone(launcher.task_click_cap({"tasks": ["GradeFes"]}, "GradeFes"))
        # 2026-10-22: the post-memory continue tap reuses the judge rects (same place the game points its arrow at).
        for judge in ("Vo", "Da", "Vi"):
            node = nodes[f"SC.Grade.Live.MemoryGo{judge}"]
            self.assertEqual(node["target"], nodes[f"SC.Grade.Live.Judge{judge}"]["target"])
            self.assertEqual(node["all_of"], ["SC.Grade.Live.MemoryGo.Guard", f"SC.Grade.Live.Step.MemoryGo{judge}"])
            self.assertIn(f"SC.Grade.Live.MemoryGo{judge}", TABLE.get(f"live-judge-{judge.lower()}").nodes)
            self.assertIn(f"[JumpBack]SC.Grade.Live.MemoryGo{judge}", loop["next"])
        self.assertEqual(nodes["SC.Grade.Live.MemoryGo.Guard"]["all_of"],
                         ["SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.MemoryArrow"])
        for name, cap in (("Card1", 14), ("JudgeVi", 16), ("Timing", 10), ("Memory", 2), ("MemoryGoVi", 4),
                          ("MemoryFilm", 2)):
            self.assertEqual(nodes[f"SC.Grade.Live.{name}"]["max_hit"], cap, name)  # re-tap: cards/judges/memory only
        # 2026-10-24: the film tap sits inside the cyan frame its guard checks (four colour edges), first in the loop.
        film = nodes["SC.Grade.Live.MemoryFilm"]
        self.assertEqual(film["target"], list(TABLE.get("live-memory-film").rect))
        self.assertEqual(TABLE.get("live-memory-film").nodes, ("SC.Grade.Live.MemoryFilm",))
        self.assertEqual(loop["next"][1], "[JumpBack]SC.Grade.Live.MemoryFilm")
        # The judge tap's check node must accept the film, else it waits past the ~2.5 s strip (2026-10-22: miss).
        self.assertIn("SC.Grade.Live.Film.Guard", nodes["SC.Grade.Live.Targeted"]["any_of"])
        x, y, w, h = film["target"]
        self.assertTrue(508 + 12 < x and x + w < 771 - 12 and 262 + 10 < y and y + h < 457 - 10)
        for edge in ("Top", "Bottom", "Left", "Right"):
            anchor = nodes[f"SC.Grade.Live.Anchor.Film{edge}"]
            self.assertEqual((anchor["recognition"], anchor["method"]), ("ColorMatch", 4))
            self.assertEqual(anchor["roi"], list(reader.FILM_EDGES[edge.lower()][0]))
            self.assertEqual(anchor["count"], reader.FILM_EDGES[edge.lower()][1])
            self.assertIn(f"SC.Grade.Live.Anchor.Film{edge}", nodes["SC.Grade.Live.Film.Guard"]["all_of"])

    def test_fes_resume_nodes_and_action(self):
        """B5h: only 「再開」 is an action; other tasks stop on フェス再開 without a click."""
        nodes = pipeline_nodes()
        action = TABLE.get("grade-fes-resume")
        self.assertEqual((action.family, action.consuming, action.nodes), ("grade-fes", False, ("SC.Grade.Resume",)))
        x, y, w, h = action.rect
        self.assertTrue(662 <= x and x + w <= 816 and 436 <= y and y + h <= 508)  # inside the pink 再開 button
        self.assertTrue(x > 623)                                                   # nowhere near 終了 (x ≤ 623)
        resume = nodes["SC.Grade.Resume"]
        self.assertEqual((resume["enabled"], resume["target"], resume["max_hit"], resume["next"], resume["all_of"]),
                         (False, list(action.rect), 1, ["SC.Grade.Live.Loop"],
                          ["SC.Popup.FesResume.Guard", "SC.Grade.Check.Resume"]))
        main = nodes["SC.Grade.Main"]["next"]
        self.assertEqual(main[:7], ["[JumpBack]SC.Grade.Resume", "[JumpBack]SC.Grade.Resume.Wait", "SC.Grade.Resume.Hold",
                                    "[JumpBack]SC.Grade.Takeover", "[JumpBack]SC.Grade.ReportTakeover",
                                    "[JumpBack]SC.Grade.RankingTakeover", "[JumpBack]SC.Popup.CloseKnown"])
        ranking = nodes["SC.Grade.RankingTakeover"]  # B5k: no click, straight into the live loop (ranking OK there)
        self.assertNotIn("action", ranking)
        self.assertEqual((ranking["all_of"], ranking["next"], ranking["max_hit"]),
                         (["SC.Grade.Live.Ranking.Guard", "SC.Grade.Check.RankingTakeover"], ["SC.Grade.Live.Loop"], 1))
        report = nodes["SC.Grade.ReportTakeover"]  # B5j: no click, straight into the live loop (OK there)
        self.assertNotIn("action", report)
        self.assertEqual((report["all_of"], report["next"], report["max_hit"]),
                         (["SC.Grade.Live.Report.AnyGuard", "SC.Grade.Check.ReportTakeover"], ["SC.Grade.Live.Loop"], 1))
        takeover = nodes["SC.Grade.Takeover"]  # B5i: no click, straight into the live loop
        self.assertNotIn("action", takeover)
        self.assertEqual((takeover["all_of"], takeover["next"], takeover["max_hit"]),
                         (["SC.Grade.Live.Screen.Guard", "SC.Grade.Check.Takeover"], ["SC.Grade.Live.Loop"], 1))
        self.assertNotIn("action", nodes["SC.Grade.Resume.Wait"])
        self.assertEqual(nodes["SC.Grade.Resume.Hold"]["custom_action"], "SC.Grade.Abort")
        self.assertIn("SC.Popup.FesResume.Guard", nodes["SC.Grade.Menu.Tapped"]["any_of"])
        close = nodes["SC.Popup.CloseKnown"]
        self.assertIn("SC.Popup.FesResume.Guard", close["any_of"])
        self.assertLess(close["next"].index("SC.Popup.FesResume.Stop"), close["next"].index("SC.Popup.NotHandled"))
        stop = nodes["SC.Popup.FesResume.Stop"]
        self.assertEqual((stop["action"], stop["custom_action"]), ("Custom", "SC.FailTask"))
        clicks = {n for n, v in nodes.items() if isinstance(v, dict) and v.get("action") == "Click"
                  and "SC.Popup.FesResume.Guard" in v.get("all_of", [])}
        self.assertEqual(clicks, {"SC.Grade.Resume"})
        self.assertTrue(all(nodes[n]["threshold"] == 0.95 for n in nodes["SC.Popup.FesResume.Guard"]["all_of"]))

    def test_resume_log_state(self):
        spent = [{"event": "tension_intent", "rights_before": [20, 20]}, {"event": "tension_decided"},
                 {"event": "intent", "stage": gt.LIVE_STAGE}, {"event": "live_unfinished"}]
        state = gt.log_state(spent + [{"event": "resume", "rights_observed": [19, 20]}])
        self.assertEqual((state.attempts, state.open["resumed"], gt.resume_kind(state)), (1, True, None))
        state = gt.log_state(spent + [{"event": "resume"}, {"event": "verified"}])
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))
        state = gt.log_state([{"event": "resume"}])  # a live this log does not know: counted, never resumed again
        self.assertEqual((state.attempts, state.uncertain, gt.resume_kind(state)), (1, 1, None))


# ======================================================================== offline replay of the manual live
def live_frames():
    return sorted(LIVE_DIR.glob("*.png"))


def frame(index):
    return live_frames()[index - 1]


def build_fes_main_19():
    """Fes main with 19/20 (the native 20/20 frame, rights glyphs redrawn) — no post-live Fes main was sampled."""
    from PIL import Image, ImageDraw, ImageFont
    SYN.mkdir(parents=True, exist_ok=True)
    image = Image.open(ROOT / FES_MAIN).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle([1140, 664, 1196, 682], fill=(245, 92, 22))
    draw.text((1167, 673), "19/20", font=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 15), fill=(255, 250, 245),
              anchor="mm")
    image.save(FES_MAIN_19)


def build_resume_negatives():
    """The native フェス再開 frame with one anchor area filled by its surrounding colour (one per anchor)."""
    import numpy as np
    from PIL import Image
    SYN.mkdir(parents=True, exist_ok=True)
    base = np.array(Image.open(ROOT / FES_RESUME).convert("RGB"))
    for name, (x, y, w, h) in RESUME_ERASE.items():
        image = base.copy()
        image[y:y + h, x:x + w] = base[y + 1, x + 1]  # the panel / button colour at the corner of the anchor
        Image.fromarray(image).save(RESUME_NEGATIVES[name])


def live_steps(after, prefix=None, drop_card=False):
    """The manual live as a replay: each user action is a click point (the next frame shows its effect).

    ``prefix``: the frames before the live (default: the whole Grade chain up to ライブ開始); ``drop_card``: the first
    card tap of TURN 1 is dropped by the canvas (the same waiting frame again; only the re-tap selects the card)."""
    steps = [(ROOT / f, "click") for f in (GRADE_CHAIN if prefix is None else prefix)]
    # TURN 1/7: 倍速, then its one re-tap — the user never turned it on, so 「倍速OFF」 stays on every recorded frame
    # (no ON sample): the replay shows the same frame again and the card follows LIVE_RETAP_SECONDS after the re-tap.
    steps += [(frame(221), 1), (frame(222), "click"), (frame(222), "click")]
    if drop_card:
        steps += [(frame(223), "click")]                                  # card tap without effect
    steps += [(frame(223), "click"),                                      # card
              (frame(225), 1), (frame(226), "click"),                     # card 3 selected: judge Vi
              (frame(229), "click"),                                      # APPEAL bar
              (frame(252), 1), (frame(260), 1), (frame(270), 1), (frame(280), 1),
              (frame(296), 1), (frame(297), "click"),                     # TURN 2/7
              (frame(300), 1), (frame(301), "click"), (frame(303), "click"),
              (frame(306), 1), (frame(320), 1), (frame(340), 1), (frame(360), 1), (frame(365), 1),
              (frame(374), 1), (frame(376), 1), (frame(380), 1), (frame(381), "click"),  # LIVE REPORT -> OK
              (after, "click")]
    return steps


USER_CARDS = {1: 3, 2: 3}  # the manual live: card 3 in both turns


def user_choice(state):
    decision = pol.decide(state)
    return pol.Decision("card", USER_CARDS[state.turn], decision.target, decision.scores,
                        ("replay_user_choice",) + decision.reasons)


@unittest.skipUnless(HAVE_MAA and len(live_frames()) >= 396 and all((ROOT / f).is_file() for f in GRADE_CHAIN),
                     "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 2026-10-03 手动 Live 原生帧")
class LiveReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not private_ready():
            raise unittest.SkipTest("私有回放数据不完整")
        build_fes_main_19()
        build_resume_negatives()

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_b5d_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_live(self, after, decide=user_choice, live="开启", steps=None, spend=True, task="GradeFes"):
        options = {k: {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples"}
                   for k in ("training", "grade", "match")}
        if decide is not None:
            options["grade"]["live_decide"] = decide
        config = {"tasks": [{"name": task, "option": {"Grade自动演出": live}} if task == "GradeFes" else task]}
        if spend:
            config["allow_spend"] = ["grade-tension-confirm"]
        summary = launcher.run(config, offline_steps=steps or live_steps(after), simulate_input=True,
                               policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                               task_options=options)
        task, = summary["tasks"]
        reports = [r for r in task["reports"] if r["kind"] == "grade"]
        return task, (reports[0] if reports else None)

    def events(self):
        path = self.root / "grade-2026-10-02.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_manual_live_is_played_to_fes_main_with_rights_minus_one(self):
        task, report = self.run_live(FES_MAIN_19)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        self.assertEqual(task["simulated_actions"], NAV_ACTIONS + [
            "live-speed-toggle", "live-speed-toggle", "live-card-3", "live-judge-vi", "live-appeal-timing",
            "live-card-3", "live-judge-da", "live-appeal-timing", "live-report-ok"])
        self.assertEqual(task["simulated_spends"], [["grade-tension-confirm", "sent"]] * 2)  # ライブ開始 once
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point))
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["tension_intent", "tension_decided", "intent", "live_retap",
                                                        "live_turn", "live_target", "live_turn", "live_target",
                                                        "live_report", "verified"])
        self.assertEqual(events[3]["tap"], "speed")  # 「倍速OFF」 unchanged after the tap: one re-tap
        # Targets came from the on-screen predictions and match the user's choices (Vi, then Da).
        self.assertEqual([(e["target"], e["rule"]) for e in events if e["event"] == "live_target"],
                         [("Vi", "predicted_finish"), ("Da", "predicted_finish")])
        self.assertEqual(events[5]["predicted"], {"Vo": 100, "Da": 100, "Vi": 100})
        self.assertEqual({k: events[8][k] for k in ("rank", "stars", "score", "money", "held")},
                         {"rank": 1, "stars": 90, "score": 21874, "money": 218, "held": 17678700})
        self.assertEqual((events[8]["settled"], events[8]["money_2x_badge"]), ("stable", False))
        self.assertEqual(events[-1]["rights_after"], [19, 20])
        turn1 = events[4]
        self.assertIn("TURN 1/7", turn1["explain"])
        self.assertEqual([c["appeal"] for c in turn1["cards"]], ["Vo", "Vo", "Vo"])

    def test_real_policy_choice_differs_from_the_recorded_frames_and_stops(self):
        """The policy picks card 2 (LINK); the recorded next frame shows card 3 selected: stop, never retry."""
        task, report = self.run_live(FES_MAIN_19, decide=None)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_unfinished"))
        self.assertEqual(task["simulated_actions"], NAV_ACTIONS + ["live-speed-toggle", "live-speed-toggle", "live-card-2"])
        events = self.events()
        self.assertEqual(events[-1]["reason"], "selected_card_differs")
        self.assertEqual(events[4]["decision"]["slot"], 2)
        self.assertEqual(task["simulated_spends"], [["grade-tension-confirm", "sent"]] * 2)

    def test_fes_main_without_rights_minus_one_is_live_unfinished(self):
        task, report = self.run_live(ROOT / FES_MAIN_6)
        self.assertEqual(report["outcome"], "live_unfinished")
        self.assertEqual(self.events()[-1]["reason"], "fes_main_rights_not_decremented")
        self.assertEqual(task["simulated_actions"][-1], "live-report-ok")

    def test_live_play_off_never_touches_the_live(self):
        task, report = self.run_live(FES_MAIN_19, decide=None, live="关闭")
        self.assertEqual(task["simulated_actions"], NAV_ACTIONS)
        self.assertEqual(report["outcome"], "manual_takeover")  # live confirmed: hand over immediately without playing

    # ---- B5h: フェス再開 and dropped taps
    RESUME_PREFIX = [GRADE_CHAIN[0], GRADE_CHAIN[1], FES_RESUME]  # Home -> Fes popup -> グレード&リハ -> フェス再開

    def test_fes_resume_presses_resume_once_and_plays_the_live(self):
        """After a host restart: 「再開」 only (non-consuming, no spend permission needed), then the live loop."""
        task, report = self.run_live(FES_MAIN_19, steps=live_steps(FES_MAIN_19, prefix=self.RESUME_PREFIX), spend=False)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        self.assertEqual(task["simulated_actions"], ["home-fes", "fes-grade-entry", "grade-fes-resume",
                                                     "live-speed-toggle", "live-speed-toggle", "live-card-3", "live-judge-vi",
                                                     "live-appeal-timing", "live-card-3", "live-judge-da",
                                                     "live-appeal-timing", "live-report-ok"])
        self.assertEqual(task["simulated_spends"], [])          # never テンション決定 / ライブ開始
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point))
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["resume", "live_retap", "live_turn", "live_target",
                                                        "live_turn", "live_target", "live_report", "verified"])
        self.assertEqual((events[0]["stage"], events[0]["rights_observed"], events[0]["resumed"]),
                         (gt.RESUME_STAGE, [19, 20], True))
        self.assertEqual((events[-1]["resumed"], events[-1]["rights_after"]), (True, [19, 20]))
        state = gt.log_state(events)
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))

    def test_fes_resume_without_live_play_never_clicks(self):
        task, report = self.run_live(FES_MAIN_19, live="关闭", steps=[(ROOT / f, "click") for f in self.RESUME_PREFIX])
        self.assertEqual(task["simulated_actions"], ["home-fes", "fes-grade-entry"])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "fes_resume_pending"))
        self.assertIn("live_play_disabled", report["notes"])
        self.assertFalse((self.root / "grade-2026-10-02.jsonl").exists())  # nothing logged, nothing pressed

    def test_fes_resume_tap_without_effect_stops_after_15_s(self):
        steps = [(ROOT / f, "click") for f in self.RESUME_PREFIX] + [(ROOT / FES_RESUME, "click")]
        task, report = self.run_live(FES_MAIN_19, steps=steps)
        self.assertEqual(task["simulated_actions"], ["home-fes", "fes-grade-entry", "grade-fes-resume"])  # once
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_unfinished"))
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["resume", "live_unfinished"])
        self.assertEqual(events[-1]["reason"], "resumed_live_not_observed_15s")

    def test_other_tasks_never_close_fes_resume(self):
        """GoHome (MissionStatus) on フェス再開: no click at all (終了 would forfeit the spent right)."""
        task, _ = self.run_live(FES_MAIN_19, steps=[(ROOT / FES_RESUME, "click")], task="MissionStatus", spend=False)
        self.assertEqual((task["status"], task["simulated_actions"], task["refused_clicks"]), ("failed", [], []))
        self.assertIn("fes_resume_pending_never_closed", json.dumps(task, ensure_ascii=False))

    def test_dropped_card_tap_is_repeated_once(self):
        task, report = self.run_live(FES_MAIN_19, steps=live_steps(FES_MAIN_19, drop_card=True))
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        self.assertEqual(task["simulated_actions"], NAV_ACTIONS + [
            "live-speed-toggle", "live-speed-toggle", "live-card-3", "live-card-3", "live-judge-vi", "live-appeal-timing",
            "live-card-3", "live-judge-da", "live-appeal-timing", "live-report-ok"])
        retaps = [e for e in self.events() if e["event"] == "live_retap"]
        self.assertEqual([(e["turn"], e["tap"], e.get("slot")) for e in retaps], [(1, "speed", None), (1, "card", 3)])

    # ---- B5i: take over a live already on screen (auto run 2026-10-03 09:45 stopped at TURN 3/7)
    TAKEOVER_FRAMES = [AUTO_DIR / f"auto_frame-0{n}.png" for n in (27, 28, 29, 30)] + [AUTO_DIR / "turn3.png"]

    @unittest.skipUnless(all(f.is_file() for f in TAKEOVER_FRAMES), "需要 2026-10-03 自动演出原生帧")
    def test_live_takeover_at_turn_3_with_the_real_policy(self):
        """No popup, no navigation: the live loop takes over; the dropped card (frames end) is re-tapped once."""
        steps = [(f, 1) for f in self.TAKEOVER_FRAMES[:-1]] + [(self.TAKEOVER_FRAMES[-1], "click")]
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual(task["simulated_actions"], ["live-card-2", "live-card-2"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_unfinished"))
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["live_takeover", "live_turn", "live_retap", "live_unfinished"])
        turn = events[1]
        self.assertEqual((turn["turn"], turn["decision"]["slot"], turn["decision"]["target"]), (3, 2, "Da"))
        self.assertIn("Da(流行2 ×1.5 69.2%)", turn["explain"])
        self.assertIn("Vi(流行1 ×2 審査終了)", turn["explain"])
        self.assertEqual(events[-1]["reason"], "card_tapped_no_effect")
        self.assertEqual(events[-1]["live"]["speed"], "already_on")  # 「2倍速」 shown: never touched

    # ---- 2026-10-22: 思い出アピール on the official TURN 6/6 (Maa's own deduplicated frames of the second live)
    MEMORY_DIR = PRIVATE / "grade-live-memory-20261022-frames"

    def memory_frame(self, n):
        return self.MEMORY_DIR / f"frame-{n:03d}.png"

    @unittest.skipUnless((PRIVATE / "grade-live-memory-20261022-frames/frame-098.png").is_file(),
                         "需要 2026-10-22 回忆アピール原生帧")
    def test_memory_appeal_turn_continues_with_one_tap_and_reaches_fes_main(self):
        """Takeover at TURN 6/6 (memory 100%): the real policy plays the memory appeal to Vi, the post-film screen (arrow
        at Vi, no 流行) gets its one tap on the Vi judge, then NEW RECORD / LIVE REPORT / ranking and Fes main 19/20."""
        f = self.memory_frame
        steps = ([(f(47), "click"), (f(48), "click"), (f(49), 1), (f(50), 1), (f(51), 1), (f(52), "click")]
                 + [(f(n), 1) for n in (84, 85, 86, 87, 88, 89, 90, 91, 92)]
                 + [(f(93), "click"), (f(95), "click"), (f(96), "click"), (f(97), 1), (f(98), "click")])
        task, report = self.run_live(f(98), decide=None, steps=steps, spend=False)
        self.assertEqual(task["simulated_actions"], ["live-memory-appeal", "live-judge-vi", "live-judge-vi",
                                                     "live-record-dismiss", "live-report-ok", "live-report-ok"])
        self.assertEqual(task["simulated_spends"], [])
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["live_takeover", "live_turn", "live_target", "live_memory_go",
                                                        "live_record_overlay", "live_report", "live_ranking",
                                                        "verified"])
        turn = events[1]
        self.assertEqual((turn["turn"], turn["decision"]["kind"], turn["memory_gauge"]), (6, "memory", 100))
        self.assertEqual((events[2]["target"], events[3]["target"]), ("Vi", "Vi"))
        self.assertEqual(events[-1]["rights_after"], [19, 20])

    @unittest.skipUnless((PRIVATE / "grade-live-film-20261022/frame-040.png").is_file()
                         and (PRIVATE / "grade-live-memory-20261022-frames/frame-098.png").is_file(),
                         "需要 2026-10-22 回忆胶片帧")
    def test_memory_film_is_stopped_once_then_the_turn_continues(self):
        """2026-10-24: the same TURN 6/6 with the scrolling film (video frames). The judge tap's check node also accepts
        the film (frame 12), then Bad at the right edge (13: no tap), then Good there (16) -> one film tap; the arrow
        screen then gets its one tap as before."""
        f, film = self.memory_frame, lambda n: PRIVATE / f"grade-live-film-20261022/frame-{n:03d}.png"
        steps = ([(f(47), "click"), (f(48), "click"), (film(12), 1), (film(13), 1), (film(16), "click"),
                  (film(19), 1),
                  (f(50), 1), (f(51), 1), (f(52), "click")]
                 + [(f(n), 1) for n in (84, 85, 86, 87, 88, 89, 90, 91, 92)]
                 + [(f(93), "click"), (f(95), "click"), (f(96), "click"), (f(97), 1), (f(98), "click")])
        task, report = self.run_live(f(98), decide=None, steps=steps, spend=False)
        self.assertEqual(task["simulated_actions"], ["live-memory-appeal", "live-judge-vi", "live-memory-film",
                                                     "live-judge-vi", "live-record-dismiss", "live-report-ok",
                                                     "live-report-ok"])
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["live_takeover", "live_turn", "live_target", "live_memory_film",
                                                        "live_memory_go", "live_record_overlay", "live_report",
                                                        "live_ranking", "verified"])
        self.assertEqual((events[3]["rule"], events[3]["film_rounds"], events[3]["tiles"]["right2"]),
                         ("lookahead_good", 2, "good"))

    @unittest.skipUnless((AUTO_DIR / "turn3.png").is_file(), "需要 2026-10-03 自动演出原生帧")
    def test_live_takeover_without_live_play_never_clicks(self):
        task, report = self.run_live(FES_MAIN_19, decide=None, live="关闭", spend=False,
                                     steps=[(AUTO_DIR / "turn3.png", "click")])
        self.assertEqual((task["simulated_actions"], task["refused_clicks"]), ([], []))
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_takeover_refused"))

    # ---- B5j: LIVE REPORT OK (own action, settle, one re-tap) and taking over at the report
    @unittest.skipUnless(REPORT_FRAME.is_file(), "需要 2026-10-03 10:16 LIVE REPORT 原生帧")
    def test_report_takeover_presses_ok_once_and_verifies(self):
        steps = [(REPORT_FRAME, "click"), (ROOT / FES_MAIN_19, "click")]
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        self.assertEqual((task["simulated_actions"], task["simulated_spends"]), (["live-report-ok"], []))
        (x, y), (bx, by, bw, bh) = task["simulated_clicks"][0], OK_BUTTON
        self.assertTrue(TABLE.get("live-report-ok").contains((x, y)))
        # Match the static button-safety contract: Maa can select any pixel in the action rect,
        # including y=640..644 and 685..689, which the former 15-pixel inset incorrectly rejected.
        self.assertTrue(bx + 40 <= x < bx + bw - 40 and by + 6 <= y < by + bh - 6)
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["report_takeover", "live_report", "verified"])
        self.assertEqual((events[0]["stage"], events[0]["takeover"]), (gt.REPORT_TAKEOVER_STAGE, True))
        self.assertEqual({k: events[1][k] for k in ("rank", "stars", "score", "money", "held", "money_2x_badge",
                                                    "mission_reward_row", "settled", "report_takeover")},
                         {"rank": 1, "stars": 88, "score": 20277, "money": 405, "held": 17679105,
                          "money_2x_badge": True, "mission_reward_row": True, "settled": "stable",
                          "report_takeover": True})
        self.assertEqual(events[-1]["rights_after"], [19, 20])
        state = gt.log_state(events)
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))

    @unittest.skipUnless(REPORT_FRAME.is_file(), "需要 2026-10-03 10:16 LIVE REPORT 原生帧")
    def test_report_ok_without_effect_is_repeated_once_then_stops(self):
        """The 10:16 failure: OK left LIVE REPORT unchanged. One re-tap, then stop; never a third press."""
        steps = [(REPORT_FRAME, "click")] * 3
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual(task["simulated_actions"], ["live-report-ok", "live-report-ok"])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played_reported"))  # report proven
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["report_takeover", "live_report", "live_retap", "live_unfinished"])
        self.assertEqual((events[2]["tap"], events[-1]["reason"]), ("report", "report_tapped_no_effect"))

    @unittest.skipUnless(REPORT_FRAME.is_file() and MISSION_POPUP.is_file(), "需要 LIVE REPORT 与ミッション達成原生帧")
    def test_mission_popup_after_ok_is_closed_by_the_shared_whitelist(self):
        steps = [(REPORT_FRAME, "click"), (MISSION_POPUP, "click"), (ROOT / FES_MAIN_19, "click")]
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual(task["simulated_actions"], ["live-report-ok", "popup-mission-completed-ok"])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)

    @unittest.skipUnless(REPORT_FRAME.is_file(), "需要 2026-10-03 10:16 LIVE REPORT 原生帧")
    def test_unknown_screen_after_ok_is_never_clicked(self):
        steps = [(REPORT_FRAME, "click"), (ROOT / GRADE_CHAIN[5], "click")]  # stands in for an unsampled result page
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual((task["simulated_actions"], task["refused_clicks"]), (["live-report-ok"], []))
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played_reported"))  # report proven
        self.assertEqual(self.events()[-1]["reason"], "unrecognised_screen_static_20s")

    @unittest.skipUnless(REPORT_FRAME.is_file(), "需要 2026-10-03 10:16 LIVE REPORT 原生帧")
    def test_report_takeover_without_live_play_never_clicks(self):
        task, report = self.run_live(FES_MAIN_19, decide=None, live="关闭", spend=False, steps=[(REPORT_FRAME, "click")])
        self.assertEqual((task["simulated_actions"], task["refused_clicks"]), ([], []))
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_takeover_refused"))
        self.assertFalse((self.root / "grade-2026-10-02.jsonl").exists())

    @unittest.skipUnless(REPORT_FRAME.is_file(), "需要 2026-10-03 10:16 LIVE REPORT 原生帧")
    def test_report_guard_on_the_auto_run_report(self):
        """Guard: both reports (manual 380 without the campaign, auto 10:16 with the 2倍 badge and mission row)."""
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from agent.core import task_custom
        from tools.replay_screenshots import read_png
        samples = [REPORT_SAMPLES / f"frame-{i:03d}.png" for i in range(21, 364, 3)]
        samples = [f for f in samples if f.is_file()]
        files = [REPORT_FRAME, frame(380)] + samples
        nodes = ("SC.Grade.Live.Report.Guard", "SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.ReportMoney2x",
                 "SC.Grade.Live.Anchor.ReportMission")
        found, readings = {n: set() for n in nodes}, {}

        class Probe(CustomAction):
            def run(self, context, argv):
                vision = task_custom.Vision(context)
                for path in files:
                    image = read_png(path)
                    for node in nodes:
                        detail = context.run_recognition(node, image)
                        if detail is not None and detail.hit:
                            found[node].add(path)
                    readings[path] = reader.read_report(vision, image)[0]
                return True

        scenario = launcher.ReplayScenario([read_png(files[0])], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        report = found["SC.Grade.Live.Report.Guard"]
        self.assertTrue({REPORT_FRAME, frame(380)} <= report)
        self.assertEqual(found["SC.Grade.Live.Screen.Guard"] & report, set())
        self.assertIn(REPORT_FRAME, found["SC.Grade.Live.Anchor.ReportMoney2x"])
        self.assertNotIn(frame(380), found["SC.Grade.Live.Anchor.ReportMoney2x"])  # no campaign on 2026-10-03 00:26
        self.assertEqual(readings[REPORT_FRAME], {"rank": 1, "stars": 88, "score": 20277, "money": 405, "held": 17679105})
        self.assertEqual(readings[frame(380)], {"rank": 1, "stars": 90, "score": 21874, "money": 218, "held": 17678700})
        if samples:  # 021/022: the last TURN 4 frames; 024 onwards: the settled report
            later = {f for f in samples if int(f.stem[-3:]) >= 24}
            self.assertEqual(later - report, set())
            self.assertEqual({f for f in samples if int(f.stem[-3:]) < 23} & report, set())
            self.assertTrue(all(readings[f]["score"] == 20277 and readings[f]["held"] == 17679105 for f in later))

    # ---- B5k: the second LIVE REPORT page (ranking list)
    @unittest.skipUnless(RANKING_FRAME.is_file() and REPORT_1109.is_file(), "需要 2026-10-03 11:09 LIVE REPORT 两页原生帧")
    def test_report_then_ranking_ok_then_fes_main(self):
        """The 11:09 stop: report OK, then the ranking page: its own row is recorded and the same OK pressed once."""
        steps = [(REPORT_1109, "click"), (RANKING_FRAME, "click"), (ROOT / FES_MAIN_19, "click")]
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        self.assertEqual((task["simulated_actions"], task["simulated_spends"]), (["live-report-ok"] * 2, []))
        for x, y in task["simulated_clicks"]:
            bx, by, bw, bh = OK_BUTTON
            self.assertTrue(TABLE.get("live-report-ok").contains((x, y)))
            self.assertTrue(bx + 40 <= x < bx + bw - 40 and by + 6 <= y < by + bh - 6)  # safely inside OK on both pages
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["report_takeover", "live_report", "live_ranking", "verified"])
        self.assertEqual({k: events[1][k] for k in ("rank", "stars", "score", "money", "held")},
                         {"rank": 1, "stars": 90, "score": 20751, "money": 207, "held": 17679312})
        self.assertEqual({k: events[2][k] for k in ("rank", "pt", "row", "pt_matches_score", "ranking_takeover")},
                         {"rank": 1, "pt": 20751, "row": 0, "pt_matches_score": True, "ranking_takeover": False})
        self.assertEqual(events[-1]["live"]["ranking"], {"rank": 1, "pt": 20751, "row": 0})

    @unittest.skipUnless(RANKING_FRAME.is_file(), "需要 2026-10-03 11:09 排名页原生帧")
    def test_ranking_takeover_presses_ok_once_and_verifies(self):
        steps = [(RANKING_FRAME, "click"), (ROOT / FES_MAIN_19, "click")]
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        self.assertEqual((task["simulated_actions"], task["simulated_spends"]), (["live-report-ok"], []))
        self.assertTrue(TABLE.get("live-report-ok").contains(task["simulated_clicks"][0]))
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["ranking_takeover", "live_ranking", "verified"])
        self.assertEqual((events[0]["stage"], events[0]["takeover"]), (gt.RANKING_TAKEOVER_STAGE, True))
        self.assertEqual({k: events[1][k] for k in ("rank", "pt", "row", "ranking_takeover")},
                         {"rank": 1, "pt": 20751, "row": 0, "ranking_takeover": True})
        self.assertEqual(events[-1]["rights_after"], [19, 20])
        state = gt.log_state(events)
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))

    @unittest.skipUnless(RANKING_FRAME.is_file(), "需要 2026-10-03 11:09 排名页原生帧")
    def test_ranking_ok_without_effect_is_repeated_once_then_stops(self):
        steps = [(RANKING_FRAME, "click")] * 3
        task, report = self.run_live(FES_MAIN_19, decide=None, steps=steps, spend=False)
        self.assertEqual(task["simulated_actions"], ["live-report-ok", "live-report-ok"])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_unfinished"))
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["ranking_takeover", "live_ranking", "live_retap", "live_unfinished"])
        self.assertEqual((events[2]["tap"], events[-1]["reason"]), ("ranking", "ranking_tapped_no_effect"))

    @unittest.skipUnless(RANKING_FRAME.is_file(), "需要 2026-10-03 11:09 排名页原生帧")
    def test_ranking_takeover_without_live_play_never_clicks(self):
        task, report = self.run_live(FES_MAIN_19, decide=None, live="关闭", spend=False, steps=[(RANKING_FRAME, "click")])
        self.assertEqual((task["simulated_actions"], task["refused_clicks"]), ([], []))
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_takeover_refused"))
        self.assertFalse((self.root / "grade-2026-10-02.jsonl").exists())

    @unittest.skipUnless(RANKING_FRAME.is_file() and REPORT_FRAME.is_file(), "需要 2026-10-03 LIVE REPORT 两页原生帧")
    def test_ranking_guard_and_own_row(self):
        """Ranking guard: the ranking frame and the 11:09 samples 038–045 only; never a first report page or a live turn."""
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from agent.core import task_custom
        from tools.replay_screenshots import read_png
        ranking = [RANKING_FRAME] + [f for f in (RANKING_SAMPLES / f"frame-{i:03d}.png" for i in range(38, 46)) if f.is_file()]
        reports = [REPORT_FRAME, frame(380)] + [f for f in (RANKING_SAMPLES / f"frame-{i:03d}.png" for i in range(34, 38))
                                                if f.is_file()]
        others = [AUTO_DIR / "turn3.png"] + live_frames()[:372:25]  # manual 375–396: the first report page
        files = ranking + reports + others
        nodes = ("SC.Grade.Live.Ranking.Guard", "SC.Grade.Live.Report.Guard", "SC.Grade.Live.ReportOk.Guard")
        found, readings = {n: set() for n in nodes}, {}

        class Probe(CustomAction):
            def run(self, context, argv):
                vision = task_custom.Vision(context)
                for path in files:
                    image = read_png(path)
                    for node in nodes:
                        detail = context.run_recognition(node, image)
                        if detail is not None and detail.hit:
                            found[node].add(path)
                    if path in ranking:
                        readings[path] = reader.read_ranking(vision, image)[0]
                return True

        scenario = launcher.ReplayScenario([read_png(files[0])], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        self.assertEqual(found["SC.Grade.Live.Ranking.Guard"], set(ranking))
        self.assertEqual(found["SC.Grade.Live.Report.Guard"] & set(ranking), set())
        self.assertTrue({REPORT_FRAME, frame(380)} <= found["SC.Grade.Live.ReportOk.Guard"])
        self.assertEqual(found["SC.Grade.Live.ReportOk.Guard"] & set(others), set())
        self.assertTrue(all(readings[f] == {"rank": 1, "pt": 20751, "row": 0} for f in ranking), readings)

    def test_fes_resume_guard_hits_only_the_dialog(self):
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from tools.replay_screenshots import read_png
        files = ([ROOT / FES_RESUME] + list(RESUME_NEGATIVES.values()) + [ROOT / f for f in GRADE_CHAIN]
                 + [ROOT / FES_MAIN_6] + live_frames()[::5])
        guards = ("SC.Popup.FesResume.Guard", "SC.Nav.FesMain.Guard", "SC.Nav.BackOne")
        found = {g: set() for g in guards}

        class Probe(CustomAction):
            def run(self, context, argv):
                for path in files:
                    image = read_png(path)
                    for guard in guards:
                        detail = context.run_recognition(guard, image)
                        if detail is not None and detail.hit:
                            found[guard].add(path)
                return True

        scenario = launcher.ReplayScenario([read_png(files[0])], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        self.assertEqual(found["SC.Popup.FesResume.Guard"], {ROOT / FES_RESUME})
        # The dimmed Fes main under the dialog is not Fes main (no back key, no 「フェスに参加する」).
        self.assertNotIn(ROOT / FES_RESUME, found["SC.Nav.FesMain.Guard"] | found["SC.Nav.BackOne"])

    def test_live_guards_on_every_manual_frame(self):
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from tools.replay_screenshots import read_png
        guards = ("SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Wait.Guard", "SC.Grade.Live.Timing.Guard",
                  "SC.Grade.Live.Report.Guard", "SC.Grade.Live.Anchor.SwapHint")
        files = live_frames()
        found = {g: set() for g in guards}

        class Probe(CustomAction):
            def run(self, context, argv):
                for index, path in enumerate(files, 1):
                    image = read_png(path)
                    for guard in guards:
                        detail = context.run_recognition(guard, image)
                        if detail is not None and detail.hit:
                            found[guard].add(index)
                return True

        images = [read_png(files[0])]
        scenario = launcher.ReplayScenario(images, ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        wait, timing, report = (found["SC.Grade.Live.Wait.Guard"], found["SC.Grade.Live.Timing.Guard"],
                                found["SC.Grade.Live.Report.Guard"])
        self.assertTrue(set(range(1, 224)) <= wait and {296, 297, 299, 300, 301} <= wait)
        self.assertTrue({229, 240, 250, 303, 304} <= timing and not wait & timing and not report & (wait | timing))
        self.assertEqual(report, set(range(375, 397)))  # 373–374: fade-in
        self.assertEqual(found["SC.Grade.Live.Anchor.SwapHint"] & set(range(1, 224)), set())  # no card selected
        self.assertTrue({225, 226, 300, 301} <= found["SC.Grade.Live.Anchor.SwapHint"])

    FILM_DIR = PRIVATE / "grade-live-film-20261022"

    @unittest.skipUnless((PRIVATE / "grade-live-film-20261022/frame-040.png").is_file()
                         and (PRIVATE / "grade-live-memory-20261022-frames/frame-098.png").is_file(),
                         "需要 2026-10-22 回忆胶片帧")
    def test_film_guard_on_the_memory_film_only(self):
        """2026-10-24: SC.Grade.Live.Film.Guard (four cyan edges) and read_film agree on the 2026-10-22 film (video frames
        012-035 scrolling, 011 Ready, 036+ miss) and never fire on the memory live's other frames."""
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from tools.replay_screenshots import read_png
        files = sorted(self.FILM_DIR.glob("frame-*.png")) + sorted(LiveReplayTests.MEMORY_DIR.glob("frame-*.png"))
        found, film, after = set(), set(), set()

        class Probe(CustomAction):
            def run(self, context, argv):
                for path in files:
                    image = read_png(path)
                    detail = context.run_recognition("SC.Grade.Live.Film.Guard", image)
                    if detail is not None and detail.hit:
                        found.add(path)
                    detail = context.run_recognition("SC.Grade.Live.Filmed", image)
                    if detail is not None and detail.hit:
                        after.add(path)
                    if reader.read_film(image) is not None:
                        film.add(path)
                return True

        scenario = launcher.ReplayScenario([read_png(files[0])], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        scrolling = {self.FILM_DIR / f"frame-{n:03d}.png" for n in range(12, 36)}
        self.assertEqual(film, scrolling | {LiveReplayTests.MEMORY_DIR / "frame-049.png"})
        self.assertTrue(film <= found, sorted(p.name for p in film - found))
        # The frame alone also stands over 「Ready!!」 and the strip's end (no lit tile: read_film refuses the tap there).
        unlit = {self.FILM_DIR / f"frame-{n:03d}.png" for n in (11, 36, 37)}
        self.assertFalse(found - film - unlit, sorted(p.name for p in found - film))
        self.assertTrue(scrolling <= after)  # the film tap's next node also hits while the strip stands


if __name__ == "__main__":
    unittest.main()
