"""Failed lessons / work: a stat drop the next week is logged as a failure (大失敗 when the week
after shows the 99 % trouble rate), never inferred as a cap, and shown in the live history. Synthetic, zero input."""
from __future__ import annotations

import unittest

from agent.core import wing_nav as nav
from tests.test_wing_room_preview import WEEK, frame, runtime

BEFORE = dict(WEEK["stats"])          # Vo 120, Da 60, Vi 60, Me 80


def probe(attribute="Vo", kind="lesson", trouble=8):
    return {"key": [1, 8], "attribute": attribute, "stat": BEFORE[attribute] if attribute else None, "kind": kind,
            "stats": dict(BEFORE), "trouble": trouble}


def next_week(rt, stats, trouble=40):
    rt.memory.pending = None
    rt.decide("Schedule", frame(weeks_left=7, stats=stats, trouble_rate=trouble))
    return [e for e in rt.log if e.get("event") in ("lesson_failure", "cap_inferred")]


class LessonFailureTests(unittest.TestCase):
    def test_confirmed_room_records_all_stats(self):
        rt = runtime()
        rt.decide("Schedule", frame())
        d = rt.decide("Schedule", frame("work_talk", {"preview": {"Da": 8, "Me": 8}, "sp_gain": 80, "fan_gain": 150}))
        self.assertEqual(d.reason, "confirm_room")
        p = rt.memory.lesson_probe
        self.assertEqual((p["attribute"], p["kind"], p["stats"], p["trouble"]), (None, "work", BEFORE, 0))
        # the probe survives a segment restart
        fresh = nav.RunMemory(strategy=rt.memory.strategy, goal=rt.memory.goal)
        fresh.restore(rt.memory.snapshot())
        self.assertEqual(fresh.lesson_probe, p)

    def test_drop_is_a_failure_not_a_cap(self):
        rt = runtime()
        rt.memory.lesson_probe = probe()
        events = next_week(rt, {**BEFORE, "Vo": 112, "Me": 78})
        self.assertEqual(events, [{"event": "lesson_failure", "kind": "failure", "action": "lesson", "attribute": "Vo",
                                   "drop": {"Vo": -8, "Me": -2}, "trouble": 8, "week": [1, 8]}])
        self.assertNotIn("Vo", rt.memory.stat_caps or {})
        self.assertIsNone(rt.memory.lesson_probe)

    def test_catastrophe_and_work_failure(self):
        rt = runtime()
        rt.memory.lesson_probe = probe(None, "work")
        events = next_week(rt, {**BEFORE, "Da": 40}, trouble=99)
        self.assertEqual((events[0]["kind"], events[0]["action"], events[0]["drop"]), ("catastrophe", "work", {"Da": -20}))

    def test_unchanged_stat_still_infers_the_cap_and_misreads_are_ignored(self):
        rt = runtime()
        rt.memory.lesson_probe = probe()
        self.assertEqual(next_week(rt, dict(BEFORE)), [{"event": "cap_inferred", "attribute": "Vo", "cap": 120}])
        rt = runtime()
        rt.memory.lesson_probe = probe()
        self.assertEqual(next_week(rt, {**BEFORE, "Vo": 150}), [])
        self.assertIsNone(nav._stat_drop(BEFORE, {**BEFORE, "Me": BEFORE["Me"] - 400}))
        self.assertIsNone(nav._stat_drop(BEFORE, {**BEFORE, "Vo": 140}))


if __name__ == "__main__":
    unittest.main()
