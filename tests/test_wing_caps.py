"""Stat caps (live 2026-10-10): caps-first SP order near the cap, cap inference from a stalled lesson, memory round trip."""
from __future__ import annotations

import unittest

from agent.core import wing_nav as nav
from agent.core import wing_sp as sp


def _nodes():
    return [sp.SPNode("skill", "live_skill", 40, 3.0, ("Vo",), source="s_main"),
            sp.SPNode("cap", "cap_up", 30, 50.0, ("Vo",))]


class CapsFirstTests(unittest.TestCase):
    def test_skill_first_below_cap(self):
        plan = sp.plan_sp(_nodes(), sp=40, primary="Vo", stat=300, cap=600)
        self.assertEqual(plan.steps[0].node_id, "skill")

    def test_cap_first_at_cap(self):
        plan = sp.plan_sp(_nodes(), sp=40, primary="Vo", stat=536, cap=536)
        self.assertEqual(plan.steps[0].node_id, "cap")

    def test_switch_off(self):
        plan = sp.plan_sp(_nodes(), sp=40, primary="Vo", stat=536, cap=536, params={"caps_first_near_cap": False})
        self.assertEqual(plan.steps[0].node_id, "skill")


class MemoryTests(unittest.TestCase):
    def test_round_trip(self):
        m = nav.RunMemory(strategy="vo_score", goal="true_end")
        m.stat_caps, m.lesson_probe = {"Vo": 536}, {"key": [2, 4], "attribute": "Vo", "stat": 536}
        n = nav.RunMemory(strategy="vo_score", goal="true_end")
        n.restore(m.snapshot())
        self.assertEqual((n.stat_caps, n.lesson_probe["stat"]), ({"Vo": 536}, 536))

    def test_bad_caps_refused(self):
        n = nav.RunMemory(strategy="vo_score", goal="true_end")
        with self.assertRaises(ValueError):
            n.restore({"strategy": "vo_score", "goal": "true_end", "stat_caps": {"Vo": "x"}})

    def test_week_state_uses_memory_caps(self):
        m = nav.RunMemory(strategy="vo_score", goal="true_end")
        m.stat_caps = {"Vo": 536}
        r = {"season": 2, "weeks_left": 3, "stats": {"Vo": 536, "Da": 200, "Vi": 190, "Me": 210}, "fans": 16000,
             "rooms": []}
        state, problems = nav.build_week_state(r, m)
        self.assertIsNotNone(state, problems)
        self.assertEqual(state.stat_caps, {"Vo": 536})


class ProvisionalCapTests(unittest.TestCase):
    """2026-10-29 user 「接近500差不多就要sp学习了」: wing4b reached the Vo cap with SP 530 because S1 took no audition, so
    no SP session had read the caps. Unread caps count as 500 for the spend-SP-first rule until an SP session ran."""

    def setUp(self):
        from tests.test_wing_te_rules import run_state
        from tests.test_wing_score import rooms
        self.state = lambda **kw: run_state(2, 3, 3_000, kw.pop("vo", 470), sp=kw.pop("sp", 120),
                                            options=rooms(vo=(40, 3)), stamina=90, **kw)

    def test_unread_cap_counts_as_500(self):
        from agent.core import wing_policy as wp
        d = wp.decide(self.state(), "vo_score")
        self.assertEqual((d.kind, d.rule), ("spend_sp", "primary_at_cap_spend_sp_first"))
        self.assertIn("Vo_470_cap_500_provisional", d.reasons)

    def test_below_the_band_or_read_cap_or_no_sp_keeps_the_lesson(self):
        from agent.core import wing_policy as wp
        self.assertNotEqual(wp.decide(self.state(vo=440), "vo_score").kind, "spend_sp")       # 440 + 40 < 500
        self.assertNotEqual(wp.decide(self.state(stat_caps={"Vo": 600}), "vo_score").kind, "spend_sp")
        self.assertNotEqual(wp.decide(self.state(sp=10), "vo_score").kind, "spend_sp")
        self.assertNotEqual(wp.decide(self.state(), "vo_score", params={"provisional_cap": None}).kind, "spend_sp")

    def test_rules_mode_uses_it_too(self):
        from agent.core import wing_policy as wp
        d = wp.decide(self.state(auditions=()), "vo_score", params={"decision_mode": "rules"})
        self.assertEqual((d.kind, d.rule), ("spend_sp", "primary_at_cap_spend_sp_first"))

    def test_navigator_turns_it_off_after_an_sp_session(self):
        from unittest import mock
        week = mock.Mock(return_value={"kind": None, "rule": "x"})
        rt = nav.WingNavRuntime(hooks={"week": week})
        rt.begin()
        rooms = [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "label_ok": True, "selected": True}]
        readings = {"season": 2, "weeks_left": 3, "stats": {"Vo": 470, "Da": 216, "Vi": 144, "Me": 265},
                    "fans": 3000, "sp": 120, "stamina_pct": 90, "rooms": rooms, "selected_room": "lesson_vo"}
        rt.memory.auditions_seen = {"key": [2, 3], "pages": [], "trend": None, "done": True}
        rt.decide("Schedule", readings)
        self.assertNotIn("provisional_cap", week.call_args.args[3])
        rt.memory.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None}
        rt.decide("Schedule", readings)
        self.assertIsNone(week.call_args.args[3]["provisional_cap"])


if __name__ == "__main__":
    unittest.main()
