from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core import wing_state as ws

FIXTURE = Path(__file__).parent / "fixtures" / "produce" / "wing_s2_week.json"


def make_state(**overrides) -> ws.WingState:
    """Synthetic, fully read week state (S2 week 3, Vo single)."""
    base = dict(
        phase="week", season=2, week=3,
        stats={"Vo": 220, "Da": 80, "Vi": 75, "Me": 160}, fans=3_000, stamina=80, max_stamina=100,
        condition="普通", sp=120, memory_level=1, trend={"Vo": 2, "Da": 1, "Vi": 3}, trouble_rate=None,
        options=(ws.ScheduleOption("lesson_vo", "lesson", "Vo", "Vocal", True, {"Vo": 30}, 20),
                 ws.ScheduleOption("lesson_da", "lesson", "Da", "Dance", True, {"Da": 30}, 20),
                 ws.ScheduleOption("lesson_vi", "lesson", "Vi", "Visual", True, {"Vi": 30}, 20),
                 ws.ScheduleOption("work_radio", "work", None, "ラジオ", True, {"Me": 20}, 15),
                 ws.ScheduleOption("rest", "rest", None, "休息", True, {}, 0)),
        auditions=(ws.Audition("aud_10k", "夕方ワイド", 10_000, 1_000, True),),
        wins=(ws.AuditionWin(1, 1_000),), confidence=0.99, recognized=True, evidence=("synthetic",),
    )
    base.update(overrides)
    return ws.WingState(**base)


class WingStateTests(unittest.TestCase):
    def test_round_trip_json(self) -> None:
        state = make_state()
        again = ws.WingState.from_json(state.to_json())
        self.assertEqual(again.to_dict(), state.to_dict())
        self.assertEqual(again.weeks_left, 6)
        self.assertEqual(again.season_gate, 10_000)
        self.assertEqual(again.season_wins(1), [1_000])

    def test_fixture_loads_and_validates(self) -> None:
        state = ws.WingState.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertEqual(state.validate(), [])
        self.assertEqual(state.option("lesson", "Vo").id, "lesson_vo")

    def test_unknown_is_null_not_zero(self) -> None:
        data = make_state().to_dict()
        data["fans"] = None
        data["stats"]["Me"] = None
        state = ws.WingState.from_dict(data)
        self.assertIn("fans_unread", state.validate())
        self.assertIn("stats_unread", state.validate())

    def test_strict_rejections(self) -> None:
        good = make_state().to_dict()
        cases = [
            ("extra", lambda d: d.update(extra=1)),
            ("missing confidence", lambda d: d.pop("confidence")),
            ("bool fans", lambda d: d.update(fans=True)),
            ("season 5", lambda d: d.update(season=5)),
            ("week 9", lambda d: d.update(week=9)),
            ("partial trend", lambda d: d.update(trend={"Vo": 1, "Da": None, "Vi": 3})),
            ("bad condition", lambda d: d.update(condition="元気")),
            ("bad stat key", lambda d: d["stats"].update(Mx=1)),
            ("version", lambda d: d.update(version=2)),
            ("nan", None),
        ]
        for name, mutate in cases:
            with self.subTest(name):
                data = json.loads(json.dumps(good))
                if mutate is None:
                    with self.assertRaises(ValueError):
                        ws.WingState.from_json(json.dumps(data).replace('"confidence": 0.99', '"confidence": NaN'))
                    continue
                mutate(data)
                with self.assertRaises(ValueError):
                    ws.WingState.from_dict(data)

    def test_duplicate_json_key_rejected(self) -> None:
        text = make_state().to_json()
        with self.assertRaises(ValueError):
            ws.WingState.from_json(text[:-1] + ', "fans": 1}')

    def test_validate_flags(self) -> None:
        self.assertIn("not_recognized", make_state(recognized=False).validate())
        self.assertIn("trend_not_a_permutation", make_state(trend={"Vo": 1, "Da": 1, "Vi": 3}).validate())
        dup = make_state(auditions=(ws.Audition("rest", "x", 1000, 0, True),))
        self.assertIn("duplicate_option_id", dup.validate())
        self.assertIn("stamina_above_max", make_state(stamina=120).validate())

    def test_ambiguous_option_is_none(self) -> None:
        state = make_state(options=(ws.ScheduleOption("a", "rest"), ws.ScheduleOption("b", "rest")))
        self.assertIsNone(state.option("rest"))


if __name__ == "__main__":
    unittest.main()
