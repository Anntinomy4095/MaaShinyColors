"""Note/label -> screen rules of tools/halo_samples.py; no private data needed."""
import unittest

from tools import halo_samples as hs


class ScreenOfTests(unittest.TestCase):
    def test_label_wins_over_the_note(self):
        self.assertEqual(hs.screen_of({"label": "HaloHub", "note": "H1: S2W1 select Visual"}), "HaloHub")

    def test_notes_name_the_screen_the_click_was_made_on(self):
        cases = {
            "H1: S2W1 hub プロデュース": "HaloHub",
            "H1: S2W5 select Visual (5 supports)": "HaloLesson",
            "H1: S2W6 audition tab": "HaloLesson",              # tapped on the lesson page
            "H1: back to lesson tab (save Me/閃 for 施設効率相談)": "HaloPractice",
            "H1: audition carousel > (3)": "HaloAudition",
            "H1: S3W7 施設効率相談 100% 決定 (target entry)": "HaloPractice",
            "H1: 成長確認 習得する": "HaloGrowthConfirm",
            "H1: final 成長 Vocal上限 + r1": "HaloGrowth",
            "H1: 勧誘確認 櫻木真乃 決定": "HaloRecruitConfirm",
            "H1: AUDITION-REPORT 次へ": "HaloReport",
            "H1: 挑戦確認 First Live Visual 挑戦する": "HaloLiveConfirm",
            "H1: First Live select Visual (78%, Vi pt scarce)": "HaloLiveSelect",
            "H1: 誕生確認 キャンセル (sample スキル変更 first)": "FesBirthConfirm",
            "H1: Halo tutorial page → (2)": "HaloTutorial",
        }
        for note, screen in cases.items():
            self.assertEqual(hs.screen_of({"note": note}), screen, note)

    def test_unprefixed_or_unknown_notes_are_left_out(self):
        self.assertIsNone(hs.screen_of({"note": "Match sampling: open skip"}))
        self.assertIsNone(hs.screen_of({"note": "H1: something new"}))


if __name__ == "__main__":
    unittest.main()
