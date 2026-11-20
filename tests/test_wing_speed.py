"""W.I.N.G. classification shortcuts and the Schedule static reuse (2026-10-25 speed batch, tools/wing_speed_audit.py)."""
import unittest
from unittest import mock

import numpy as np

from agent.core import wing_custom as wcu
from agent.core import wing_screen as wsc
from agent.core import wing_speed as ws


def image(seed=0):
    return np.random.default_rng(seed).integers(40, 200, size=(720, 1280, 3), dtype=np.uint8)


def shifted(img, delta):
    return np.clip(img.astype(np.int16) + delta, 0, 255).astype(np.uint8)


class SameViewTests(unittest.TestCase):
    def test_small_global_change_passes_and_a_local_overlay_does_not(self):
        base = image()
        self.assertTrue(ws.same_view(ws.thumb(base), ws.thumb(shifted(base, 3))))
        cut_in = base.copy()
        cut_in[200:360, 400:560] = 255          # one 160x160 overlay: the mean stays small, its blocks do not
        mean, block = ws.view_diff(ws.thumb(base), ws.thumb(cut_in))
        self.assertLess(mean, ws.VIEW_MEAN_DIFF)
        self.assertGreater(block, ws.VIEW_BLOCK_DIFF)
        self.assertFalse(ws.same_view(ws.thumb(base), ws.thumb(cut_in)))

    def test_non_frames_never_match(self):
        self.assertIsNone(ws.thumb(object()))
        self.assertFalse(ws.same_view(None, ws.thumb(image())))
        self.assertFalse(ws.same_view(ws.thumb(image()), np.zeros((10, 10), dtype=np.float32)))


class FrameDigestTests(unittest.TestCase):
    def setUp(self):
        self.seq = 1
        self.session = wcu.WingSession(capture_seq=lambda: self.seq)

    def test_one_capture_is_hashed_once(self):
        img = image(1)
        with mock.patch.object(wcu, "_digest", wraps=wcu._digest) as digest:
            first = self.session.frame_digest(img)
            for _ in range(5):
                self.assertEqual(self.session.frame_digest(img.copy()), first)
        self.assertEqual(digest.call_count, 1)
        self.assertEqual(first, wcu.hashlib.sha256(img.tobytes()).hexdigest())

    def test_a_new_capture_or_a_different_frame_is_hashed_again(self):
        a, b = image(1), image(2)
        first = self.session.frame_digest(a)
        self.assertNotEqual(self.session.frame_digest(b), first)      # same capture number, other pixels
        self.seq = 2
        with mock.patch.object(wcu, "_digest", wraps=wcu._digest) as digest:
            self.assertEqual(self.session.frame_digest(a), first)
        self.assertEqual(digest.call_count, 1)

    def test_without_a_capture_number_every_call_hashes(self):
        session = wcu.WingSession()
        img = image(3)
        with mock.patch.object(wcu, "_digest", wraps=wcu._digest) as digest:
            session.frame_digest(img)
            session.frame_digest(img)
        self.assertEqual(digest.call_count, 2)


class HintTableTests(unittest.TestCase):
    def test_every_hint_screen_and_confuser_has_a_screen_guard(self):
        nodes = wsc.load_nodes()
        self.assertTrue(ws.HINT_CONFUSERS)
        for screen, confusers in ws.HINT_CONFUSERS.items():
            for name in (screen, *confusers):
                self.assertIn(wsc.SCREEN_PREFIX + name, nodes, f"{screen}: {name}")
            self.assertNotIn(screen, confusers)
            if screen != "Loading":
                self.assertIn("Loading", confusers)


class ClassifyShortcutTests(unittest.TestCase):
    def session(self):
        s = wcu.WingSession()
        s.backend_factory = lambda context: wsc.BoxBackend()
        s.load_nodes()
        self.full = mock.patch.object(wsc.ScreenClassifier, "classify",
                                      side_effect=lambda frame, **kw: wsc.Classification(self.answer, None, ()))
        self.calls = self.full.start()
        self.addCleanup(self.full.stop)
        return s

    def test_same_view_reuses_the_classification_until_the_view_changes(self):
        s, self.answer = self.session(), "Hub"
        base = image()
        self.assertEqual(s.classify(None, base), "Hub")
        self.answer = "Schedule"                                 # the full classifier would now say otherwise
        self.assertEqual(s.classify(None, shifted(base, 2)), "Hub")
        self.assertEqual(s.classify(None, shifted(base, -2)), "Hub")
        self.assertEqual(self.calls.call_count, 1)
        cut_in = base.copy()
        cut_in[200:360, 400:560] = 255
        self.assertEqual(s.classify(None, cut_in), "Schedule")   # an overlay: classified afresh
        self.assertEqual(dict(s.classify_counts), {"full": 2, "same_view": 2})

    def test_reference_does_not_creep_with_reused_frames(self):
        s, self.answer = self.session(), "Hub"
        base = image()
        s.classify(None, base)
        for k in range(1, 6):                                    # each step is close to the last, not to the first
            s.classify(None, shifted(base, 3 * k))
        self.assertGreaterEqual(self.calls.call_count, 2)

    def test_unknown_frames_are_never_reused(self):
        s, self.answer = self.session(), None
        base = image()
        self.assertIsNone(s.classify(None, base))
        s.classify(None, shifted(base, 1))
        self.assertEqual(self.calls.call_count, 2)

    def test_hint_answers_only_with_its_guard_and_no_confuser(self):
        s, self.answer = self.session(), "Loading"
        guards = {wsc.SCREEN_PREFIX + "Hub": True, wsc.SCREEN_PREFIX + "Loading": False}
        with mock.patch.dict(ws.HINT_CONFUSERS, {"Hub": ("Loading",)}, clear=True), \
                mock.patch.object(wsc.Evaluator, "hit", side_effect=lambda ref, frame: guards[ref]):
            self.assertEqual(s.classify(None, image(1), hint="Hub"), "Hub")
            self.assertEqual(self.calls.call_count, 0)
            guards[wsc.SCREEN_PREFIX + "Loading"] = True          # a confuser hits: full classification
            self.assertEqual(s.classify(None, image(2), hint="Hub"), "Loading")
            self.assertEqual(s.classify(None, image(3), hint="Schedule"), "Loading")   # not audited: never a hint
        self.assertEqual(dict(s.classify_counts), {"hint": 1, "hint_miss": 1, "full": 1})

    def test_a_failing_hint_check_falls_back_to_the_full_classification(self):
        s, self.answer = self.session(), "Hub"
        with mock.patch.dict(ws.HINT_CONFUSERS, {"Hub": ()}, clear=True), \
                mock.patch.object(wsc.Evaluator, "hit", side_effect=RuntimeError("ocr")), \
                self.assertLogs(wcu.LOGGER, "ERROR"):
            self.assertEqual(s.classify(None, image(), hint="Hub"), "Hub")
        self.assertEqual(self.calls.call_count, 1)


class SettleTests(unittest.TestCase):
    """The click waits only the rest of SETTLE_S since its screen was first classified (no fixed pre_delay)."""

    def session(self):
        s = wcu.WingSession()
        s.backend_factory = lambda context: wsc.BoxBackend()
        s.load_nodes()
        self.now = 100.0
        s.clock = lambda: self.now
        self.slept = []
        s.sleep = self.slept.append
        patcher = mock.patch.object(wsc.ScreenClassifier, "classify",
                                    side_effect=lambda frame, **kw: wsc.Classification(self.answer, None, ()))
        patcher.start()
        self.addCleanup(patcher.stop)
        return s

    def approve(self, s, screen, intent):
        since = s.screen_since
        s.settle_since = since[1] if since is not None and since[0] == screen else None
        s.screen_since, s.last_act = None, (screen, intent)

    def test_first_classification_starts_the_clock_and_a_new_screen_restarts_it(self):
        s, self.answer = self.session(), "Choice"
        s.classify(None, image(1))
        self.now += 0.3
        s.classify(None, image(2))
        self.assertEqual(s.screen_since, ("Choice", 100.0))
        self.answer = "Commu"
        s.classify(None, image(3))
        self.assertEqual(s.screen_since, ("Commu", 100.3))
        self.answer = None
        s.classify(None, image(4))
        self.assertIsNone(s.screen_since)

    def test_a_quick_decision_waits_the_rest_and_a_slow_one_does_not_wait(self):
        s, self.answer = self.session(), "Choice"
        s.classify(None, image(1))
        self.now += 0.2
        self.approve(s, "Choice", "choice_left")
        s._settle()
        self.assertAlmostEqual(self.slept[-1], wcu.SETTLE_S - 0.2)
        s.classify(None, image(2))
        self.now += 2.5
        self.approve(s, "Choice", "choice_left")
        s._settle()
        self.assertEqual(len(self.slept), 1)

    def test_an_untracked_screen_waits_the_whole_time_and_the_timed_appeal_never_waits(self):
        s = self.session()
        self.approve(s, "Review", "page_return")
        s._settle()
        self.assertEqual(self.slept, [wcu.SETTLE_S])
        self.approve(s, "Live", "live_appeal_timing")
        s._settle()
        self.assertEqual(self.slept, [wcu.SETTLE_S])

    def test_every_click_node_ends_with_the_settle_guard_and_has_no_pre_delay(self):
        nodes = wsc.load_nodes()
        clicks = {k: v for k, v in nodes.items() if k.startswith("SC.Wing.Click.")}
        self.assertTrue(clicks)
        for name, node in clicks.items():
            self.assertEqual(node["all_of"][-1], "SC.Wing.State.Settled", name)
            self.assertEqual(node.get("pre_delay"), 0, name)


class SpendLedgerReadTests(unittest.TestCase):
    def test_a_resolved_spend_entry_is_not_read_again(self):
        s = wcu.WingSession()
        s.spend_key = "k"
        screen = next(iter(wcu.nav.IN_PRODUCE_SCREENS))
        with mock.patch.object(s.ledger, "get", return_value={"key": "k", "state": "confirmed"}) as get:
            for _ in range(5):
                s.observe(screen)
        self.assertEqual(get.call_count, 1)


class ScheduleStaticTests(unittest.TestCase):
    STATIC = {"header": {"season": 2, "weeks_left": 4, "fans_left": 12000},
              "status": {"stats": {"Vo": 400, "Da": 200, "Vi": 180, "Me": 150}, "sp": 155, "fans": 9000},
              "promise": False}

    def test_read_screen_uses_the_static_part_and_reads_the_rest(self):
        evaluator = wsc.Evaluator(wsc.load_nodes(), wsc.BoxBackend())
        frame = wsc.Frame([], lambda: np.zeros((720, 1280, 3), dtype=np.uint8))
        with mock.patch.object(wsc, "read_status", side_effect=AssertionError("status read again")), \
                mock.patch.object(wsc, "parse_season", side_effect=AssertionError("header read again")):
            out = wsc.read_screen("Schedule", evaluator, frame, static=self.STATIC)
        self.assertEqual((out["season"], out["weeks_left"], out["sp"], out["stats"]["Vo"], out["promise"]),
                         (2, 4, 155, 400, False))
        self.assertIn("rooms", out)                               # rooms / selection are read on this frame
        self.assertTrue(out[wsc.SCHEDULE_STATIC_KEY]["reused"])
        fresh = wsc.read_screen("Schedule", evaluator, frame)
        self.assertFalse(fresh[wsc.SCHEDULE_STATIC_KEY]["reused"])
        self.assertNotIn(wsc.SCHEDULE_STATIC_KEY, wsc.read_screen("Hub", evaluator, frame))

    def test_static_part_is_reused_only_right_after_one_room_tap(self):
        s = wcu.WingSession()
        s.schedule_static = (3, self.STATIC)
        s.runtime.acts = 4
        s.last_act = ("Schedule", "schedule_room_2")
        self.assertIs(s._schedule_static("Schedule"), self.STATIC)
        self.assertIsNone(s._schedule_static("Hub"))
        s.last_act = ("Schedule", "schedule_decide")
        self.assertIsNone(s._schedule_static("Schedule"))
        s.last_act, s.runtime.acts = ("Schedule", "schedule_room_2"), 5   # another act since
        self.assertIsNone(s._schedule_static("Schedule"))
        s.runtime.acts, s.last_act = 4, ("Hub", "schedule_room_2")
        self.assertIsNone(s._schedule_static("Schedule"))


class RoiOcrBackendTests(unittest.TestCase):
    """Batch 2: a whole-frame read node takes the frame's cached whole-frame OCR."""

    class Inner(wsc.BoxBackend):
        def __init__(self):
            self.calls = []

        def ocr(self, node, frame):
            self.calls.append(list(node.get("roi", [])))
            return [("Vo", (10, 10, 20, 20)), ("APPEAL", (600, 480, 80, 20))]

    def test_full_node_reuses_or_fills_the_frame_cache_and_roi_nodes_run_their_own_ocr(self):
        inner, cache = self.Inner(), {}
        backend = wcu.RoiOcrBackend(inner, cache, "d1")
        full = {"roi": [0, 0, 1280, 720], "threshold": 0.3, "replace": [["Vo", "Vocal"]]}
        self.assertEqual(backend.ocr(full, None)[0][0], "Vocal")
        self.assertEqual(inner.calls, [wcu.FrameOcrBackend.FULL["roi"]])
        self.assertIn("d1", cache)
        backend.ocr(full, None)
        self.assertEqual(len(inner.calls), 1)                         # second read: from the cache
        backend.ocr({"roi": [0, 0, 640, 360]}, None)
        backend.ocr({"roi": [0, 0, 1280, 720], "threshold": 0.6}, None)   # other threshold: its own OCR
        self.assertEqual(len(inner.calls), 3)
        cache["d2"] = [("Ready", (600, 480, 80, 20))]                 # filled by the guard / classifier
        self.assertEqual(wcu.RoiOcrBackend(inner, cache, "d2").ocr({"roi": [0, 0, 1280, 720]}, None),
                         [("Ready", (600, 480, 80, 20))])
        self.assertEqual(len(inner.calls), 3)

    def test_an_alias_answers_only_while_the_frame_has_no_ocr_of_its_own(self):
        inner, cache = self.Inner(), {"c1": [("SP:20", (100, 200, 73, 20))]}
        full = {"roi": [0, 0, 1280, 720]}
        self.assertEqual(wcu.RoiOcrBackend(inner, cache, "c2", "c1").ocr(full, None), [("SP:20", (100, 200, 73, 20))])
        self.assertNotIn("c2", cache)
        cache["c2"] = [("SP:0", (100, 200, 73, 20))]                 # the guard read the decision frame itself
        self.assertEqual(wcu.RoiOcrBackend(inner, cache, "c2", "c1").ocr(full, None)[0][0], "SP:0")
        self.assertEqual(inner.calls, [])

    def test_review_alias_needs_the_same_view_a_review_tap_and_a_whole_frame_classification(self):
        s = wcu.WingSession()
        c1 = image(1)
        s.ocr_cache["c1"] = []
        s.full_ref = ("Review", ws.thumb(c1), "c1")
        s.last_act = ("Review", "skill_info_close")
        self.assertEqual(s._full_alias("Review", shifted(c1, 2)), "c1")
        self.assertIsNone(s._full_alias("Live", c1))
        moved = c1.copy()
        moved[560:700, 580:1080] = 255                                # an info panel came in
        self.assertIsNone(s._full_alias("Review", moved))
        s.last_act = ("LearnConfirm", "learn_ok")                     # labels change with little pixel change
        self.assertIsNone(s._full_alias("Review", c1))
        s.last_act = ("Review", "page_return")
        del s.ocr_cache["c1"]
        self.assertIsNone(s._full_alias("Review", c1))

    def test_the_live_full_reader_matches_the_frame_ocr_node(self):
        node = wsc.load_nodes()["SC.Wing.Read.Live.Full"]
        self.assertEqual(list(node["roi"]), wcu.FrameOcrBackend.FULL["roi"])
        self.assertEqual(float(node.get("threshold", 0.3)), wcu.FrameOcrBackend.FULL["threshold"])


class TimingShortcutTests(unittest.TestCase):
    """Batch 2: the APPEAL frame right after the judge tap decides from the turn's readings."""
    from agent.core import wing_live_timing as wlt
    BAR = wlt.TimingBar((("Normal", 300, 500), ("Good", 501, 760), ("Perfect", 761, 800)), 420.0)
    JUDGE = {"live_phase": "select_judge", "turn": 3, "tooltip": {"name": "x"}, "selected_card": 2}

    def session(self):
        s = wcu.WingSession()
        s.runtime.acts = 7
        s.live_readings = (6, dict(self.JUDGE))
        s.last_act = ("Live", "live_judge_2")
        s.runtime.memory.live_step = {"turn": 3, "step": "judge", "slot": 2}
        return s

    def shortcut(self, s, bar=BAR, screen="Live"):
        with mock.patch.object(self.wlt, "read_timing_bar", return_value=bar):
            return s._timing_shortcut(screen, wcu.frame_of(image(1)))

    def test_judge_tap_then_a_readable_bar_gives_the_timing_phase(self):
        out = self.shortcut(self.session())
        self.assertEqual((out["live_phase"], out["tooltip"], out["turn"], out["selected_card"]),
                         ("timing", None, 3, 2))
        self.assertTrue(out["timing_shortcut"])

    def test_anything_else_reads_the_frame(self):
        self.assertIsNone(self.shortcut(self.session(), bar=None))
        self.assertIsNone(self.shortcut(self.session(), bar=self.wlt.TimingBar(self.BAR.zones, None)))
        self.assertIsNone(self.shortcut(self.session(), screen="Review"))
        s = self.session()
        s.last_act = ("Live", "live_card_2")
        self.assertIsNone(self.shortcut(s))
        s = self.session()
        s.runtime.acts = 8                                          # another act since the judge readings
        self.assertIsNone(self.shortcut(s))
        s = self.session()
        s.runtime.memory.live_step = {"turn": 3, "step": "timing"}
        self.assertIsNone(self.shortcut(s))
        s = self.session()
        s.live_readings = None
        self.assertIsNone(self.shortcut(s))

    def test_decision_skips_the_read_and_keeps_the_judge_readings(self):
        s = self.session()
        s.backend_factory = lambda context: wsc.BoxBackend()
        s.round_screen, s.confirmed_thumb = "Live", None
        seen = []
        s.runtime.decide = lambda screen, readings: seen.append(dict(readings)) or wcu.nav._wait("x")
        with mock.patch.object(wsc.Evaluator, "hit", return_value=True), \
                mock.patch.object(wsc, "read_screen", side_effect=AssertionError("frame read")), \
                mock.patch.object(self.wlt, "read_timing_bar", return_value=self.BAR):
            s.decision(None, image(2), "Live")
        self.assertEqual(seen[0]["live_phase"], "timing")
        self.assertEqual(s.live_readings, (6, self.JUDGE))           # a shortcut never replaces them
        s.round_decision, s.last_act = None, ("Live", "live_card_1")
        with mock.patch.object(wsc.Evaluator, "hit", return_value=True), \
                mock.patch.object(wsc, "read_screen", return_value={"live_phase": "select_judge", "turn": 4}):
            s.decision(None, image(3), "Live")
        self.assertEqual(s.live_readings, (7, {"live_phase": "select_judge", "turn": 4}))


if __name__ == "__main__":
    unittest.main()
