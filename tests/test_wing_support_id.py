"""Support identity for W.I.N.G. scoring: mastery-matching supports score higher, unknown supports fall
back to lower bounds and are flagged, room faces are named from the 発動中スキル popup, native replays."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core import wing_nav as nav
from agent.core import wing_score as sc
from agent.core import wing_state as ws
from agent.core import wing_support_id as wsi
from tests.test_wing_te_rules import run_state

ROOT = Path(__file__).resolve().parent.parent
DEMO2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02"
DEMO2_OCR = ROOT / "dev_assets/private/wing-demo2-analysis/ocr.jsonl"
REC = ROOT / "dev_assets/private/captures-20261011-164549Z"
Opt = ws.ScheduleOption


def _rgb(path):
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _ocr(path, seq):
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["seq"] == seq:
            return [(t, tuple(b)) for t, b in row["texts"]]
    raise KeyError(seq)


class MasteryScoringTest(unittest.TestCase):
    """The user's question: same room, a support with vs without the room's mastery must score differently."""

    TEAM = sc.TeamProfile({
        "mastery": sc.SupportProfile("mastery", {"Vo": 250}, 50, {"lesson_Vo": {"stat": 15, "sp": 12}}),
        "plain": sc.SupportProfile("plain", {"Vo": 250}, 50),
        "unit": sc.SupportProfile("unit", {"Vo": 250}, 50, {sc.UNIT_MASTERY_ROOM: {"sp": 10}}),
    })

    def _vo(self, ids, team=TEAM):
        room = Opt("lesson_vo", "lesson", "Vo", "", True, {}, 15, len(ids), level=2, support_ids=tuple(ids))
        r = sc.score_week(run_state(1, 4, 500, 200, options=(room, Opt("rest", "rest", None, "", True)), stamina=90,
                                    trouble_rate=None), "Vo", team=team)
        return next(c for c in r.ranked if c.id == "lesson_vo")

    def test_matching_mastery_scores_higher(self):
        with_mastery, without = self._vo(["mastery"]), self._vo(["plain"])
        self.assertGreater(with_mastery.terms["stat_gain"], without.terms["stat_gain"])
        self.assertGreater(with_mastery.score, without.score)
        self.assertIn("gain_formula_team_profile", with_mastery.notes)
        self.assertNotIn("support_profiles_incomplete", with_mastery.unknown)

    def test_unit_mastery_counts_other_supports(self):
        # ユニットマスタリー counts same-unit members at the action (P included), not every other support.
        team = sc.TeamProfile({
            "plain": sc.SupportProfile("plain", {"Vo": 250}, 50, unit="illumination"),
            "other": sc.SupportProfile("other", {"Vo": 250}, 50, unit="antica"),
            "unit": sc.SupportProfile("unit", {"Vo": 250}, 50, {sc.UNIT_MASTERY_ROOM: {"sp": 10}}, unit="illumination"),
        }, produce_unit="antica")
        alone, mixed, together = (self._vo(ids, team) for ids in (["unit"], ["unit", "other"], ["unit", "plain"]))
        self.assertFalse(any(n.startswith("unit_mastery_sp") for n in alone.notes + mixed.notes))
        self.assertIn("unit_mastery_sp_10", together.notes)

    def test_unknown_support_falls_back_and_is_flagged(self):
        unknown = self._vo(["mastery", "stranger"])
        self.assertIn("support_profiles_incomplete", unknown.unknown)
        self.assertIn("gain_formula_lower_bound", unknown.notes)
        no_ids = Opt("lesson_vo", "lesson", "Vo", "", True, {}, 15, 1, level=2)
        r = sc.score_week(run_state(1, 4, 500, 200, options=(no_ids,), stamina=90, trouble_rate=None), "Vo",
                          team=self.TEAM)
        self.assertIn("support_ids", next(c for c in r.ranked if c.id == "lesson_vo").unknown)


class AvatarBookTest(unittest.TestCase):
    def test_match_needs_margin_and_references(self):
        book = wsi.AvatarBook({"a": [[0.1] * 12], "b": [[0.5] * 12]})
        self.assertEqual(book.match([0.12] * 12, ["a", "b"])[0], "a")
        self.assertIsNone(book.match([0.3] * 12, ["a", "b"])[0])            # equidistant
        self.assertIsNone(book.match([0.2] * 12, ["a", "b", "c"])[0])       # c has no reference, not extremely close
        self.assertEqual(wsi.room_support_ids([[[0.11] * 12], [[0.11] * 12, [0.3] * 12], []], book, ["a", "b"]),
                         [("a",), None, ()])

    def test_label_entries_unique_owner(self):
        cards = {"2000000001": {"support_skills": [{"id": "ユニットマスタリーSP"}, {"id": "ユニットマスタリー体力"}]},
                 "2000000002": {"support_skills": [{"id": "ユニットマスタリーSP"}]}}
        entries = [{"skills": [("ュニットマスタリーSP", 10), ("ユニットマスタリー体カ", 3)], "signature": "x"},
                   {"skills": [("ユニットマスタリーSP", 10)], "signature": "y"},
                   {"skills": [], "none": True, "signature": "z"}]
        self.assertEqual(wsi.label_entries(entries, cards, cards), [("2000000001", "x")])

    def test_team_from_unit_cards(self):
        ok = {s: {"status": "accepted", "card_id": f"20000000{i:02d}"} for i, s in enumerate(nav.wte.SLOTS)}
        self.assertEqual(wsi.team_from_unit_cards(ok)["S"], ["2000000001", "2000000002", "2000000003", "2000000004"])
        self.assertIsNone(wsi.team_from_unit_cards({**ok, "S2": {"status": "rejected", "card_id": None}}))


@unittest.skipUnless((DEMO2 / "frame-000559.png").is_file() and DEMO2_OCR.is_file(), "private second demo frames")
class NativeActiveSkillsReplay(unittest.TestCase):
    """Second demo: Schedule #558 Visual room faces == 発動中スキル #559 Visual section faces (same order)."""

    def test_popup_faces_name_schedule_faces(self):
        schedule = wsi.room_avatar_signatures(_rgb(DEMO2 / "frame-000558.png"))
        self.assertEqual([len(r) for r in schedule], [0, 2, 3, 0, 0, 0])
        popup = wsi.read_active_skills(_ocr(DEMO2_OCR, 559), _rgb(DEMO2 / "frame-000559.png"))
        self.assertEqual([s["room"] for s in popup["sections"]], ["lesson_vi"])
        entries = popup["entries"]
        self.assertEqual([e["room"] for e in entries], ["lesson_vi"] * 3)
        self.assertEqual([e["skills"] for e in entries],
                         [[("ユニットマスタリーSP", 10)], [], [("ユニットマスタリーSP", 10), ("ユニットマスタリー体力", 3)]])
        self.assertEqual([e["none"] for e in entries], [False, True, False])
        book = wsi.AvatarBook({f"c{i}": [e["signature"]] for i, e in enumerate(entries)})
        team = ["c0", "c1", "c2"]
        ids = wsi.room_support_ids(schedule, book, team)
        self.assertEqual(ids[2], ("c0", "c1", "c2"))
        self.assertIsNone(ids[1])          # the Dance room faces are not among the three references


@unittest.skipUnless((REC / "frame-000239.png").is_file(), "private recording 2026-10-11")
class NativeUnitCheckReplay(unittest.TestCase):
    def test_unit_check_reads_team(self):
        from agent.core import wing_unit_check as wuc
        cards = wuc.read_unit_cards(_rgb(REC / "frame-000239.png"))
        if cards is None:
            self.skipTest("card index not available")
        self.assertEqual(cards["P"]["card_id"], "1040020030")       # 風野灯織 P
        self.assertFalse(any(c["blank"] for c in cards.values()))
        loading = wuc.read_unit_cards(_rgb(REC / "frame-000085.png"))
        self.assertTrue(all(c["blank"] for c in loading.values()))   # formation still loading: never a card


if __name__ == "__main__":
    unittest.main()
