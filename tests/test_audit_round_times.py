"""tools/audit_round_times.py: round simulation and summary on synthetic timings (no Maa, no frames)."""
from __future__ import annotations

import unittest

from tools import audit_round_times as art


def r(ms, hit=False, ms_pass=None, hit_pass=None):
    low = {"ms": ms, "hit": hit}
    high = {"ms": ms if ms_pass is None else ms_pass, "hit": hit if hit_pass is None else hit_pass}
    return low, high


class SimulateTests(unittest.TestCase):
    def test_cumulative_cost_reaches_the_hit_click_and_later_clicks_are_shadowed(self):
        parents = {"P": ["A", "Gate", "C1", "B", "C2"]}
        clicks = {"C1", "C2"}
        customs = {"A": False, "Gate": True, "C1": True, "B": False, "C2": False}
        results = {"A": r(100), "Gate": r(5, ms_pass=300, hit_pass=True), "C1": r(2, ms_pass=80, hit_pass=True),
                   "B": r(50, hit=True), "C2": r(40, hit=True)}
        rows = art.simulate(parents, clicks, customs, results)
        c1 = next(x for x in rows if x.get("click") == "C1")
        # the click itself is charged at its open-gate cost; earlier gates at closed (lower) / open (upper) cost
        self.assertAlmostEqual(c1["lower_ms"], 100 + 5 + 80)
        self.assertAlmostEqual(c1["upper_ms"], 100 + 300 + 80)
        c2 = next(x for x in rows if x.get("click") == "C2")
        self.assertEqual(c2["shadowed_by"], "B")          # B hits without any Agent gate: Maa runs it first
        whole = next(x for x in rows if x.get("round"))
        self.assertAlmostEqual(whole["lower_ms"], 100 + 5 + 2 + 50 + 40)

    def test_summary_flags_confirmed_possible_and_unsampled(self):
        raw = {"parents": {"P": ["X", "C1", "C2", "C3"]}, "clicks": ["C1", "C2", "C3"], "frames": ["f"],
               "skipped": [], "rows": [
                   {"parent": "P", "click": "C1", "index": 1, "lower_ms": 3600.0, "upper_ms": 3700.0,
                    "preceding": [("X", 3500.0, 3600.0)], "frame": "f"},
                   {"parent": "P", "click": "C2", "index": 2, "lower_ms": 900.0, "upper_ms": 4000.0,
                    "preceding": [("X", 800.0, 3900.0)], "frame": "f"},
                   {"parent": "P", "round": True, "lower_ms": 3700.0, "upper_ms": 4100.0, "frame": "f"}]}
        report = art.summarize(raw, threshold_s=3.5)
        risk = {i["click"]: i["risk"] for i in report["clicks"]}
        self.assertEqual(risk, {"C1": "confirmed", "C2": "possible"})
        self.assertEqual([u["click"] for u in report["unsampled_clicks"]], ["C3"])
        self.assertEqual(report["rounds"][0]["max_lower_ms"], 3700)
        self.assertIn("C1", art.markdown(report))


class PlanTests(unittest.TestCase):
    def test_custom_names_follow_referenced_nodes(self):
        data = {"N": {"recognition": {"type": "And", "param": {"all_of": ["Guard", "Want"]}}},
                "Guard": {"recognition": {"type": "OCR", "param": {}}},
                "Want": {"recognition": {"type": "Custom", "param": {"custom_recognition": "SC.X.Want"}}}}
        self.assertEqual(art._custom_names("N", data, set()), {"SC.X.Want"})
        self.assertEqual(art._custom_names("Guard", data, set()), set())

    def test_next_names_strip_prefixes_and_skip_anchors(self):
        node = {"next": [{"name": "A", "anchor": False}, {"name": "B", "anchor": True}, "[JumpBack]C"]}
        self.assertEqual(list(art._next_names(node)), ["A", "C"])


if __name__ == "__main__":
    unittest.main()
