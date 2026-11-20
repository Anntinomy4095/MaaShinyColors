"""H2b readers for say "Halo" (agent/core/halo_screen.py): parsers over OCR boxes measured on the H1 frames
 and the room advice reader on six private lesson frames checked by eye."""
import unittest
from pathlib import Path

from agent.core import halo_screen as hsc

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "dev_assets/private/wing_samples/2026-11-01"


class ParserTests(unittest.TestCase):
    def test_advice_bar_by_slot(self):
        boxes = [("中", (629, 40, 21, 14)), ("x1", (635, 51, 24, 19)), ("x2", (670, 55, 23, 14)),
                 ("x2", (702, 53, 23, 17)), ("x1", (738, 53, 26, 19)), ("3", (771, 40, 20, 13)),
                 ("x3", (773, 50, 26, 22)), ("x1", (807, 53, 25, 17)), ("x0", (843, 54, 23, 16)),
                 ("x2", (881, 54, 21, 16)), ("x0C", (913, 43, 31, 34))]
        self.assertEqual(hsc.parse_advice(boxes), {"vo": 1, "da": 2, "vi": 2, "me": 1, "hirameki": 3, "rest": 1,
                                                   "work": 0, "limit": 2, "best": 0})
        self.assertIsNone(hsc.parse_advice(boxes[:-1]))                       # a slot unread
        self.assertIsNone(hsc.parse_advice(boxes + [("x4", (915, 51, 25, 23))]))   # a slot twice

    def test_affection(self):
        self.assertEqual(hsc.parse_affection([("Lv.1 MEXTあと22", (1120, 42, 144, 28))]), {"level": 1, "next": 22})
        self.assertEqual(hsc.parse_affection([("Lv.5", (1121, 46, 41, 21)), ("NEXTあと40", (1161, 43, 100, 27))]),
                         {"level": 5, "next": 40})
        values = [hsc.affection_value({"level": lv, "next": n})
                  for lv, n in ((0, 1), (1, 1), (2, 1), (3, 6), (4, 20), (5, 40))]
        self.assertEqual(values, [0, 24, 49, 69, 80, 110])                   # H1: 110 on 社長からの評価
        self.assertIsNone(hsc.affection_value({"level": 6, "next": 3}))
        self.assertIsNone(hsc.affection_value({"level": 2, "next": 40}))     # below Lv2's floor: misread

    def test_hub_button_and_cards(self):
        self.assertEqual(hsc.parse_hub_button([("プロデュース", (26, 608, 266, 80))]), "produce")
        self.assertEqual(hsc.parse_hub_button([("ファイナルライブ", (5, 623, 317, 78)), ("PRO", (82, 624, 26, 12))]),
                         "final_live")
        self.assertEqual(hsc.parse_percent([("成功率", (776, 299, 82, 23)), ("100%", (761, 318, 113, 48))]), 100)
        self.assertIsNone(hsc.parse_percent([("58%", (778, 301, 92, 45)), ("72%", (800, 320, 40, 20))]))
        self.assertEqual(hsc.parse_fans([("+65000", (1096, 506, 109, 34))]), 65000)
        self.assertEqual(hsc.parse_card_note([("必要なアドバイスが不足しています", (669, 452, 200, 19))]), "short")
        self.assertEqual(hsc.parse_card_note([("ベストアドバイスで代用可能", (680, 447, 148, 17))]), "best")
        self.assertIsNone(hsc.parse_card_note([]))

    def test_live_rates_and_completion(self):
        boxes = [("成功率", (997, 153, 85, 24)), ("98%", (1005, 174, 87, 44)), ("97%", (1005, 349, 87, 44)),
                 ("62%", (1005, 525, 88, 44))]
        self.assertEqual(hsc.parse_live_rates(boxes), [98, 97, 62])
        self.assertEqual(hsc.parse_live_rates(boxes[:2]), [98, None, None])
        self.assertEqual(hsc.parse_completion([("チーム完成度：", (445, 92, 160, 30)), ("0%", (650, 90, 60, 40))]), 0)


@unittest.skipUnless(FRAMES.is_dir(), "private H1 frames not present")
class RoomAdviceTests(unittest.TestCase):
    # Checked by eye on the lesson band crops; ringed promise portraits included.
    TRUTH = {
        "20261101T102308061767Z-r13c17": [[], ["vo"], ["rest", "da", "rest", "work", "work"], [], ["work", "vi", "da"],
                                          ["vo", "vi"]],
        "20261101T100138372159Z-r13c17": [["work"], ["me"], ["rest", "me", "hirameki"], ["limit", "limit"],
                                          ["hirameki"], ["vi", "best"]],
        "20261101T105843112798Z-r13c29": [["vi"], ["vi", "work", "rest"], ["vo", "vi"], ["hirameki"], ["vo"],
                                          ["rest", "me", "best", "limit"]],
        "20261101T101256589173Z-r13c21": [["rest"], ["work", "hirameki"], ["vi", "work"], ["vi", "me", "best", "vi"],
                                          ["limit"], ["limit", "da", "hirameki"]],
        "20261101T103022274375Z-r13c21": [["da", "da"], ["limit", "limit", "hirameki", "vi"], ["me"],
                                          ["limit", "hirameki", "vi"], ["vi"], ["hirameki"]],
    }

    def test_rooms(self):
        from tools.wing_demo_replay import load_rgb
        for stem, want in self.TRUTH.items():
            self.assertEqual(hsc.read_room_advice(load_rgb(FRAMES / f"{stem}-before.png")), want, stem)

    def test_no_image(self):
        self.assertIsNone(hsc.read_room_advice(None))


if __name__ == "__main__":
    unittest.main()
