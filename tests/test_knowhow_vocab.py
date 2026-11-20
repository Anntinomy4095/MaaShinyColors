from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.core import knowhow_vocab as kv


def _table(*rows):
    return kv.learn_table([{"rows": [{"name": n, "effect": e} for n, e in rows]}])


TABLE = _table(
    ("Visual適正◯", "フェスユニット編成時、Visualに設定した際の基礎ポジションボーナスを+10%"),
    ("Visual適正◎", "フェスユニット編成時、Visualに設定した際の基礎ポジションボーナスを+15%"),
    ("オールラウンダー◯", "フェスユニット編成時、基礎ポジションボーナスを+5%"),
    ("オールラウンダー◎", "フェスユニット編成時、基礎ポジションボーナスを+10%"),
    ("アピールUP(思い出高&低)(G.R.A.D.)", "アピール値最大5%UP[思い出ゲージが多いほど効果UP]/アピール値最大10%UP[…]"),
    ("アピールUP(思い出高)(G.R.A.D.)", "アピール値最大5%UP[思い出ゲージが多いほど効果UP]"),
)


class SnapTests(unittest.TestCase):
    """2026-10-23 scan (281 books, 5,409 rows): 4,957 exact, 432 snapped, 20 left for review."""

    def setUp(self):
        self.voc = kv.Vocabulary(table=TABLE)

    def snap(self, name, effect=None):
        return self.voc.snap(name, effect)

    def test_exact_and_ocr_slips_of_a_song_snap_to_the_type_from_the_effect(self):
        self.assertEqual(self.snap("HOPPINJAM(Vo)", "Vocal+20").status, "exact")
        for reading in ("今がキラキラ!おもいッきりアリイドルI(VO)", "今がキラキラ!おもいッきりアイドルイ(Vo",
                        "今がキラキラ!おもいッきりアドル!", "令がキラキラ!おもいッきりアJイドルI(Vo)"):
            with self.subTest(reading=reading):
                s = self.snap(reading, "Vocal+20")
                self.assertEqual((s.status, s.name), ("snapped", "今がキラキラ!おもいッきりアイドル!(Vo)"))
        self.assertEqual(self.snap("今がキラキラ!おもいッきりアイドル1(Da", "Dance+20").name,
                         "今がキラキラ!おもいッきりアイドル!(Da)")

    def test_type_needs_evidence_and_must_agree(self):
        self.assertEqual(self.snap("今がキラキラ!おもいッきりアドル!", None).reason, "type_unknown")
        self.assertEqual(self.snap("HOPPINJAM(Vo)", "Dance+20").status, "ambiguous")

    def test_glyph_folds(self):
        self.assertEqual(self.snap("歌姬楽宴(Vo)", "Vocal+20").name, "歌姫楽宴(Vo)")
        self.assertEqual(self.snap("Leader迪正◎").name, "Leader適正◎")
        self.assertEqual(self.snap("エキスパート目標遑成(Da)", "Dance+40").name, "エキスパート目標達成(Da)")

    def test_ring_comes_from_the_effect_and_never_from_string_distance(self):
        s = self.snap("Visual適正", "フェスューソト編成時、Visuに設定した際の基礎ポジションポーナスを+10%")
        self.assertEqual((s.status, s.name), ("snapped", "Visual適正◯"))
        self.assertEqual(self.snap("オールラウンダー", "J.エスユニット編成時、基礎ポジションポーナスを+10%").name, "オールラウンダー◎")
        self.assertEqual(self.snap("Visual適正", None).status, "ambiguous")          # no effect: no guess
        self.assertEqual(self.snap("Visual適正◯", "…+15%").status, "ambiguous")      # tail and effect disagree

    def test_close_cores_are_split_by_the_effect_numbers(self):
        effect = "アピール値最大5%UP[思い出ゲージが多いほど効果UP]/アピール値最大10%UP[思い出グニジが少ないほど効果UP]"
        s = self.snap("アピ一ルUP(思W出高&低)(GA.D.", effect)
        self.assertEqual(s.name, "アピールUP(思い出高&低)(G.R.A.D.)")
        self.assertEqual(self.snap("アピールUP(思い出高&低)(G.RAD", None).status, "ambiguous")

    def test_garbage_stays_unknown_and_unchanged(self):
        s = self.snap("ア比-WUP(果U出高&.低)(G出高&低)(G", "アピール値最大5%UP")
        self.assertEqual((s.status, s.name), ("unknown", s.reading))
        self.assertFalse(s.trusted)

    def test_tiers_follow_the_reading_unless_the_effect_contradicts(self):
        table = _table(("VocalUP", "Vocal+10"), ("VocalUP+", "Vocal+15"))
        voc = kv.Vocabulary(table=table)
        self.assertEqual(voc.snap("VocalUP+", "Vocal+15").name, "VocalUP+")
        self.assertEqual(voc.snap("VocalUP", "Vocal+15").status, "ambiguous")   # a dropped + is caught by the effect

    def test_effect_key_ignores_word_noise_and_read_overs(self):
        self.assertEqual(kv.effect_key("Vocal+20cal+20"), ("+20",))
        self.assertEqual(kv.effect_key("10111R1フエス…Leaderに設定した際の…+10%"), ("+10%",))
        self.assertEqual(kv.effect_key("アピール値最大5%UP[…]/アピール値最大10%UP[…]"), ("5%", "10%"))
        self.assertIsNone(kv.effect_key("テキスト"))

    def test_user_confirmed_names_join_the_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confirmed.json"
            path.write_text(json.dumps({"names": ["新しいノウハウ"]}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(kv.Vocabulary.load(path).snap("新しいノウハウ").status, "exact")
            self.assertEqual(kv.Vocabulary.load(Path(tmp) / "missing.json").snap("新しいノウハウ").status, "unknown")


class MergeTests(unittest.TestCase):
    def pages(self, clipped_name, variants=()):
        from agent.core import knowhow_books as kb   # noqa: F401  (pages use its row shape)
        row = {"name": clipped_name, "level": 5, "effect": "Vocal+20", "clipped": True, "y": 300}
        if variants:
            row["variants"] = list(variants)
        return [{"title": "【x】大崎甘奈", "idol": "大崎甘奈", "stats": {}, "knowhow_count": 2, "complete": True,
                 "rows": [{"name": "VocalUP", "level": 1, "effect": "Vocal+10", "clipped": False, "y": 200}, row]}]

    def test_a_never_whole_row_is_accepted_only_when_its_readings_snap_to_one_name(self):
        from agent.core import knowhow_books as kb
        voc = kv.Vocabulary(table=TABLE)
        merged = kb.merge_detail_pages(self.pages("今る!おもいッきりア", ["今がキラキラ!おもいッきりアリイドルイ(Vo"]),
                                       vocabulary=voc)
        self.assertEqual((merged["complete"], merged["unknowns"]), (True, []))
        row = merged["rows"][1]
        self.assertEqual((row["name"], row["reading"], row["clipped"]), ("今がキラキラ!おもいッきりアイドル!(Vo)", "今る!おもいッきりア", False))
        self.assertIn("vocab", row["repairs"])
        garbled = kb.merge_detail_pages(self.pages("ア比-WUP(果U出高&.低)(G"), vocabulary=voc)
        self.assertIn("rows.clipped", garbled["unknowns"])
        self.assertIn("rows.clipped", kb.merge_detail_pages(self.pages("今がキラキラ!おもいッきりアイドル!(Vo)"))["unknowns"])


if __name__ == "__main__":
    unittest.main()
