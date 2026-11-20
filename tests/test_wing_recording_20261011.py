"""Native replays of the supervised recording captures-20261011-164549Z: screen classification of the
popups found there, and the read-only team / support-identity steps (``RunOptions.read_team``)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "dev_assets/private/captures-20261011-164549Z"
OCR = ROOT / "dev_assets/private/wing-demo4-analysis/ocr.jsonl"


def _frames():
    texts = {}
    for line in OCR.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        texts[row["seq"]] = [(t, tuple(b)) for t, b in row["texts"]]
    return texts


class ReadTeamRules(unittest.TestCase):
    def test_hub_opens_unit_check_once_and_rules_exist(self):
        rt = nav.WingNavRuntime(options=nav.RunOptions(read_team=True))
        self.assertEqual(rt._decide_week("Hub", {}).intent, "hub_unit_check")
        self.assertEqual(rt._decide_week("Hub", {}).intent, "hub_schedule")
        self.assertEqual(nav.RULES["BookmarkDialog"].intent, "bookmark_cancel")
        # 2026-10-14: a decision (RunOptions.picker_sort); a popup the run did not open still stops
        self.assertEqual(nav.RULES["PickerSort"].kind, "decide")
        self.assertEqual(nav.WingNavRuntime(options=nav.RunOptions()).decide("PickerSort", {}).reason,
                         "picker_sort_not_automated")
        self.assertIn("schedule_support_skills", nav.screen_intents("Schedule"))
        off = nav.WingNavRuntime(options=nav.RunOptions())
        self.assertEqual(off._decide_week("Hub", {}).intent, "hub_schedule")

    def test_support_probe_bounded(self):
        rt = nav.WingNavRuntime(options=nav.RunOptions(read_team=True))
        rt.memory.team_slots = {"P": "1040020030", "S": ["2040040090", "2040230040", "2040060080", "2040050080"],
                                "guest": "2040070080"}
        r = {"selected_room": "lesson_vo", "rooms": [{"id": "lesson_vo", "supports": 2}]}
        self.assertEqual(rt._support_probe(r, [1, 8]).intent, "schedule_support_skills")
        self.assertIsNone(rt._support_probe(r, [1, 8]))                    # once per (week, room)
        named = {"selected_room": "lesson_vo", "rooms": [{"id": "lesson_vo", "supports": 1, "support_ids": ["x"]}]}
        self.assertIsNone(rt._support_probe(named, [1, 7]))


@unittest.skipUnless(REC.is_dir() and OCR.is_file(), "private recording 2026-10-11 and its OCR")
class RecordingReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.wing_demo_replay import load_rgb
        cls.texts = _frames()
        cls.classifier = wsc.ScreenClassifier(customs={"SC.Wing.State": lambda p, f: True})
        cls.load = staticmethod(lambda n: load_rgb(REC / f"frame-{n:06d}.png"))

    def classify(self, n):
        return self.classifier.classify(wsc.Frame(self.texts[n], lambda: self.load(n), key=str(n))).screen

    def test_popups(self):
        for n in (350, 353, 355):
            self.assertEqual(self.classify(n), "BookmarkDialog", n)       # was RewardDetail before 2026-10-12
        for n in (113, 114, 161):
            self.assertEqual(self.classify(n), "PickerSort", n)
        self.assertEqual(self.classify(357), "RewardDetail")               # after キャンセル (551,635)
        # #78: ユニット編成 mid-slide (only 「次へ」 read) used to fall to NoticeNext -> rank_next on the setup 次へ.
        self.assertEqual(self.classify(78), "SetupTransition")
        self.assertEqual(nav.RULES["SetupTransition"].kind, "wait")
        for n, screen in ((82, "UnitSelect"), (91, "IdolPicker"), (110, "IdolPicker"), (239, "UnitCheck"),
                          (235, "Hub"), (243, "BonusList"), (299, "Options"), (314, "AbandonDialog")):
            self.assertEqual(self.classify(n), screen, n)

    def test_unit_check_observes_team(self):
        from agent.core import wing_unit_check as wuc
        cards = wuc.read_unit_cards(self.load(239))
        if cards is None:
            self.skipTest("card index not available")
        rt = nav.WingNavRuntime(options=nav.RunOptions(read_team=True))
        d = rt.decide("UnitCheck", {"unit_cards": cards})
        self.assertEqual(d.intent, "page_return")
        observed = next(e for e in rt.log if e.get("event") == "team_observed")["team"]
        # S2 is a rejected reading on this frame -> the team is not adopted on its own (never guessed) ...
        self.assertIsNone(observed)
        self.assertIsNone(rt.memory.team_observed)
        # ... but the configured team is confirmed by the targeted per-slot check.
        team = {"P": "1040020030", "S": ["2040040090", "2040230040", "2040060080", "2040050080"], "guest": "2040070080"}
        rt2 = nav.WingNavRuntime(options=nav.RunOptions(read_team=True))
        rt2.memory.team_slots = dict(team)
        rt2.decide("UnitCheck", {"unit_cards": cards})
        self.assertEqual(rt2.memory.team_observed, team)
        self.assertEqual(rt2._team_supports(), [*team["S"], team["guest"]])
        wrong = nav.WingNavRuntime(options=nav.RunOptions(read_team=True))
        wrong.memory.team_slots = {**team, "guest": "2040170060"}
        wrong.decide("UnitCheck", {"unit_cards": cards})
        self.assertIsNone(wrong.memory.team_observed)


if __name__ == "__main__":
    unittest.main()
