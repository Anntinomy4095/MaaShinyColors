"""W.I.N.G. number plausibility and two-frame agreement (agent/core/wing_numbers.py), offline, zero input."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import wing_custom as wcu
from agent.core import wing_nav as nav
from agent.core import wing_numbers as wn
from agent.core import wing_screen as wsc

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "dev_assets/private/ocr-eval/video-20261028"
WEEK8 = {"season": 4, "weeks_left": 8, "stats": {"Vo": 1090, "Da": 333, "Vi": 208, "Me": 358}, "sp": 17,
         "fans": 107102}


def reading(**changes):
    out = json.loads(json.dumps(WEEK8))
    for key, value in changes.items():
        if key in wn.STATS:
            out["stats"][key] = value
        else:
            out[key] = value
    return out


class Rules(unittest.TestCase):
    def guard(self, first=WEEK8):
        g, state = wn.NumberGuard(), {}
        self.assertEqual(g.check("Schedule", first, state, 1).hold, [])      # the first reading starts the history
        return g, state

    def test_badge_digit_misreads_are_rejected_and_re_read(self):
        # wing5b S4 week 8: SP 17 read as 717; the SP badge glued to メンタル 358 read as 3581 (recording, 2026-10-29)
        g, state = self.guard()
        out = g.check("Schedule", reading(sp=717, Me=3581), state, 2)
        self.assertEqual(out.hold, ["Me", "sp"])
        self.assertIsNone(out.readings["sp"])
        self.assertIsNone(out.readings["stats"])                    # one doubtful stat: the row is unread
        self.assertEqual([(e["field"], e["read"]) for e in out.events], [("Me", 3581), ("sp", 717)])
        self.assertEqual(state["values"]["sp"][0], 17)               # history unchanged
        back = g.check("Schedule", WEEK8, state, 3)                  # the next capture reads right
        self.assertEqual((back.hold, back.readings["sp"], back.readings["stats"]["Me"]), ([], 17, 358))

    def test_ranges_and_drops(self):
        imp = wn.NumberGuard.impossible
        self.assertTrue(imp("fans", 10710, 107102, 1, None))                # a digit lost: fans never go down
        self.assertTrue(imp("fans", 1071021, 107102, 1, None))              # a digit too many
        self.assertFalse(imp("fans", 307348, 207348, 1, None))              # an audition reward (demo 2 #2267)
        self.assertTrue(imp("Vo", 109, 1090, 1, None))
        self.assertFalse(imp("Vo", 1080, 1090, 1, None))                     # a failed lesson
        self.assertTrue(imp("sp", 717, 17, 1, None))
        self.assertFalse(imp("sp", 717, 17, 4, None))                        # four weeks may add that much
        self.assertFalse(imp("sp", 0, 400, 1, None))                         # spending is never impossible
        self.assertTrue(imp("weeks_left", 7, 6, 0, True))                    # the counter only goes down in a season
        self.assertFalse(imp("weeks_left", 8, 1, 1, False))
        self.assertTrue(imp("season", 3, 4, 0, None))
        self.assertTrue(imp("season", 4, 2, 0, None))
        self.assertFalse(imp("season", "wing", 4, 0, None))
        self.assertTrue(imp("Me", 3581, None, 1, None))                      # above any stat in W.I.N.G.

    def test_expected_changes_pass_at_once(self):
        g, state = self.guard()
        probe = {"key": [4, 8], "stats": dict(WEEK8["stats"]), "sp": 17, "fans": 107102,
                 "gain": {"Vo": 46, "sp": 8, "fans": 516}}
        week7 = reading(weeks_left=7, Vo=1136, sp=25, fans=107618)
        out = g.check("Schedule", week7, state, 2, probe)            # exactly last week's preview gains
        self.assertEqual((out.hold, out.events), ([], []))
        self.assertEqual(out.readings, week7)
        spent = g.check("Review", {"sp": 5}, state, 3)               # SP only goes down on the panel
        self.assertEqual((spent.hold, spent.readings["sp"]), ([], 5))
        self.assertEqual(g.check("Hub", {"season": 4, "weeks_left": 6}, state, 4).hold, [])
        self.assertEqual(g.check("Hub", {"season": "wing", "weeks_left": 8}, state, 5).hold, [])

    def test_other_changes_need_two_captures(self):
        g, state = self.guard()
        changed = reading(weeks_left=7, Vo=1150, sp=60)              # e.g. an audition week: gains unknown
        first = g.check("Schedule", changed, state, 2)
        self.assertEqual(first.hold, ["Vo", "sp"])
        self.assertIsNone(first.readings["sp"])
        same_capture = g.check("Schedule", changed, state, 2)        # the same frame again proves nothing
        self.assertEqual(same_capture.hold, ["Vo", "sp"])
        second = g.check("Schedule", changed, state, 3)
        self.assertEqual(second.hold, [])
        self.assertEqual((second.readings["sp"], second.readings["stats"]["Vo"]), (60, 1150))
        self.assertEqual({e["event"] for e in second.events}, {"number_confirmed"})
        self.assertEqual(state["values"]["sp"], [60, wn.week_index(4, 7)])

    def test_disagreeing_frames_leave_the_field_unread(self):
        g, state = self.guard()
        verdicts = [g.check("Schedule", reading(weeks_left=7, sp=40 + i), state, 2 + i) for i in range(wn.MAX_HOLDS + 1)]
        self.assertTrue(all(v.hold == ["sp"] for v in verdicts[:wn.MAX_HOLDS]))
        last = verdicts[-1]
        self.assertEqual(last.hold, [])
        self.assertIsNone(last.readings["sp"])
        self.assertEqual(last.events[-1], {"event": "number_unconfirmed", "screen": "Schedule", "fields": ["sp"]})
        self.assertEqual(state["values"]["sp"][0], 17)

    def test_a_wrong_history_is_replaced_after_repeated_reads(self):
        g, state = self.guard()
        state["values"]["fans"] = [1071020, 0]                       # e.g. a misread confirmed long ago
        results = [g.check("Schedule", WEEK8, state, 10 + i) for i in range(wn.RESET_CAPTURES)]
        self.assertTrue(all(r.readings["fans"] is None for r in results[:-1]))
        self.assertEqual(results[-1].readings["fans"], 107102)
        self.assertIn("number_reset", [e["event"] for e in results[-1].events])
        self.assertEqual(state["values"]["fans"][0], 107102)

    def test_screens_that_do_not_wait(self):
        g, state = self.guard()
        out = g.check("RankPyramid", {"fans": 10710}, state, 2)       # dropped, no re-read on this screen
        self.assertEqual((out.hold, out.readings["fans"]), ([], None))
        out = g.check("RankPyramid", {"fans": 157102}, state, 3)      # a reward: passes, not history yet
        self.assertEqual((out.hold, out.readings["fans"]), ([], 157102))
        self.assertEqual(state["values"]["fans"][0], 107102)

    def test_week_index(self):
        self.assertEqual(wn.week_index(1, 8), 0)
        self.assertEqual(wn.week_index(4, 1), 31)
        self.assertEqual(wn.week_index("wing", 8), 32)
        self.assertIsNone(wn.week_index(None, 8))


class Persistence(unittest.TestCase):
    def test_snapshot_round_trip_and_validation(self):
        memory = nav.RunMemory()
        memory.numbers = {"values": {"sp": [17, 24], "season": [4, 24]}, "week": 24}
        snap = memory.snapshot()
        restored = nav.RunMemory()
        restored.restore(json.loads(json.dumps(snap)))
        self.assertEqual(restored.numbers, memory.numbers)
        for bad in ({"values": {"sp": "17"}}, {"values": {"luck": [1, 0]}}, {"week": "x"}, {"other": 1}):
            with self.assertRaises(ValueError):
                nav.RunMemory().restore({"numbers": bad})
        self.assertIn("numbers", nav.RunMemory.PRODUCE_FACTS)

    def test_lesson_probe_keeps_the_preview_gain(self):
        from tests.test_wing_room_preview import frame, runtime
        rt = runtime()
        rt.decide("Schedule", frame())
        d = rt.decide("Schedule", frame("work_talk", {"preview": {"Da": 8, "Me": 8}, "sp_gain": 80, "fan_gain": 150}))
        self.assertEqual(d.reason, "confirm_room")
        probe = rt.memory.lesson_probe
        self.assertEqual((probe["sp"], probe["fans"], probe["gain"]),
                         (20, 500, {"Da": 8, "Me": 8, "sp": 80, "fans": 150}))
        week7 = {"season": 1, "weeks_left": 7, "stats": {"Vo": 120, "Da": 68, "Vi": 60, "Me": 88}, "sp": 100,
                 "fans": 650}
        g, state = wn.NumberGuard(), {}
        g.check("Schedule", frame(), state, 1)
        self.assertEqual(g.check("Schedule", week7, state, 2, probe).hold, [])   # exactly the preview: no re-read


class SessionWiring(unittest.TestCase):
    def session(self, seq):
        s = wcu.WingSession(capture_seq=lambda: seq[0])
        s.backend_factory = lambda context: wsc.BoxBackend()
        s.round_screen, s.confirmed_thumb = "Schedule", None
        return s

    def decide(self, s, readings, seed):
        s.round_decision = None
        img = np.random.default_rng(seed).integers(40, 200, size=(720, 1280, 3), dtype=np.uint8)
        with mock.patch.object(wsc.Evaluator, "hit", return_value=True), \
                mock.patch.object(wsc, "read_screen", return_value=json.loads(json.dumps(readings))):
            return s.decision(None, img, "Schedule")

    def test_a_rejected_number_requeues_and_the_next_capture_decides(self):
        seq = [1]
        s = self.session(seq)
        seen = []
        s.runtime.decide = lambda screen, readings: seen.append(readings) or nav._wait("x")
        self.decide(s, WEEK8, 1)
        seq[0] = 2
        held = self.decide(s, reading(sp=717), 2)
        self.assertEqual((held.action, held.reason, held.detail["fields"]), ("requeue", "number_recheck", ["sp"]))
        self.assertEqual(len(seen), 1)                               # rt.decide never saw 717
        self.assertIn("number_rejected", [e["event"] for e in s.runtime.log])
        seq[0] = 3
        self.assertEqual(self.decide(s, WEEK8, 3).reason, "x")
        self.assertEqual(seen[-1]["sp"], 17)
        self.assertEqual(s.runtime.memory.numbers["values"]["sp"][0], 17)

    def test_rejected_frames_are_kept_for_the_ocr_evaluation(self):
        seq = [1]
        s = self.session(seq)
        s.runtime.decide = lambda screen, readings: nav._wait("x")
        with tempfile.TemporaryDirectory() as tmp:
            s.unknown_dir = Path(tmp) / "wing_unknown"
            s.authority = type("LiveSpendAuthority", (), {})()       # live runs only (as the decision trace)
            self.decide(s, WEEK8, 1)
            seq[0] = 2
            self.decide(s, reading(sp=717), 2)
            files = sorted((Path(tmp) / "ocr-eval").rglob("*.json"))
            self.assertEqual(len(files), 1)                          # the accepted first frame is not kept
            sample = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(sample["read"]["sp"], 717)
            self.assertEqual(sample["events"][0]["field"], "sp")
            self.assertIsNone(sample["truth"])
            self.assertTrue(files[0].with_suffix(".png").is_file())

    def test_live_animation_waits_are_not_kept(self):
        # 2026-10-29 wing6: the run's 20 samples were all Live animation frames (live:phase_unread)
        s = self.session([1])
        s.runtime.decide = lambda screen, readings: nav._wait("live:phase_unread")
        with tempfile.TemporaryDirectory() as tmp:
            s.unknown_dir = Path(tmp) / "wing_unknown"
            s.authority = type("LiveSpendAuthority", (), {})()
            self.decide(s, WEEK8, 1)
            self.assertEqual(list((Path(tmp) / "ocr-eval").rglob("*.json")) if (Path(tmp) / "ocr-eval").exists()
                             else [], [])


@unittest.skipUnless(EVAL.is_dir(), "private labelled recording frames not present")
class LabelledSequences(unittest.TestCase):
    """The hand-labelled Schedule frames of wing4b/4c (10-28 recordings, in run order): true values never get
    rejected, and the known misreads injected into them always do."""

    def truths(self):
        rows = []
        for path in sorted(EVAL.glob("wing4*.json")):
            truth = json.loads(path.read_text(encoding="utf-8"))["truth"]
            rows.append({"stats": truth["stats"], "sp": truth["sp"], "fans": truth["fans"]})
        return rows

    def test_true_sequence_is_never_rejected(self):
        g, state = wn.NumberGuard(), {}
        holds = 0
        for i, row in enumerate(self.truths()):
            for capture in (2 * i, 2 * i + 1):                       # each frame read on two captures at most
                out = g.check("Schedule", row, state, capture)
                self.assertNotIn("number_rejected", [e["event"] for e in out.events], row)
                if not out.hold:
                    break
                holds += 1
            self.assertEqual(out.readings, row)
        self.assertLessEqual(holds, len(self.truths()))

    def test_injected_misreads_are_rejected(self):
        rows = self.truths()
        for i in range(1, len(rows)):
            prev, row = rows[i - 1], json.loads(json.dumps(rows[i]))
            for field, bad in (("sp", int("7" + str(row["sp"]))), ("fans", row["fans"] // 10 if row["fans"] >= 100 else None),
                               ("Me", int(str(row["stats"]["Me"]) + "1"))):
                if bad is None:
                    continue
                g, state = wn.NumberGuard(), {}
                g.check("Schedule", prev, state, 1)
                broken = json.loads(json.dumps(row))
                if field == "Me":
                    broken["stats"]["Me"] = bad
                else:
                    broken[field] = bad
                out = g.check("Schedule", broken, state, 2)
                self.assertIn(field, out.hold, (i, field, bad))


if __name__ == "__main__":
    unittest.main()
