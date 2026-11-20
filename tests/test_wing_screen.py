"""W.I.N.G. classifier/readers (agent.core.wing_screen) and the offline demo replay; no Maa, no input."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agent.core import wing_screen as wsc

ROOT = Path(__file__).resolve().parent.parent


def frame(*texts, image=None):
    return wsc.Frame([(t, tuple(b)) for t, b in texts], (lambda: image) if image is not None else None)


class Emulation(unittest.TestCase):
    def test_box_in_roi(self):
        self.assertTrue(wsc.box_in_roi((10, 10, 20, 20), (0, 0, 40, 40)))
        self.assertTrue(wsc.box_in_roi((5, 513, 1251, 107), (380, 505, 520, 120)))  # centre inside
        self.assertFalse(wsc.box_in_roi((100, 100, 20, 20), (0, 0, 40, 40)))
        self.assertFalse(wsc.box_in_roi((0, 0, 0, 5), (0, 0, 40, 40)))

    def test_ocr_and_color(self):
        import numpy as np
        backend = wsc.BoxBackend()
        node = {"recognition": "OCR", "roi": [0, 0, 100, 50], "expected": ["^休む$"], "replace": [["芯", "む"]]}
        self.assertTrue(backend.ocr_hit(node, frame(("休芯", (10, 10, 30, 20)))))
        self.assertFalse(backend.ocr_hit(node, frame(("休芯", (200, 10, 30, 20)))))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[0:10, 0:30] = 250
        color = {"recognition": "ColorMatch", "roi": [0, 0, 40, 40], "lower": [235] * 3, "upper": [255] * 3,
                 "count": 300}
        self.assertTrue(backend.color_hit(color, frame(image=image)))
        self.assertIsNone(backend.color_hit(color, frame()))

    def test_dispatch_priority(self):
        classifier = wsc.ScreenClassifier(customs={"SC.Wing.State": lambda p, f: True})
        commu = [("ON", (1088, 656, 50, 30))]
        self.assertEqual(classifier.classify(frame(*commu)).screen, "Commu")
        self.assertEqual(classifier.classify(frame(*commu, ("SKIP", (1186, 54, 64, 30)))).screen, "CommuSkip")
        self.assertEqual(classifier.classify(frame(("OFF", (1088, 656, 50, 30)))).screen, "CommuFastForwardOff")
        judge = ("審査員", (272, 534, 96, 35))
        self.assertEqual(classifier.classify(frame(judge)).screen, "ResultJudge")
        self.assertEqual(classifier.classify(frame(judge, *commu)).screen, "Commu")    # TV judge commu has 早送り
        loading = ("Now Loading", (830, 650, 310, 60))
        units = [("ユニット編成", (49, 22, 152, 28)), ("Produce Ido", (20, 206, 148, 33)),
                 ("Support Idol", (548, 206, 122, 30)), ("おまかせ編成", (884, 158, 146, 32)), ("スキルパネル", (905, 640, 130, 30)), ("次へ", (1140, 639, 72, 42))]
        self.assertEqual(classifier.classify(frame(*units)).screen, "UnitSelect")
        self.assertEqual(classifier.classify(frame(*units, loading)).screen, "Loading")
        self.assertIsNone(classifier.classify(frame()).screen)


class Readers(unittest.TestCase):
    def test_status_row_split_and_merged(self):
        split = [("C/ 368", (441, 391, 85, 23)), ("E/125", (560, 387, 88, 28)), ("E/118", (682, 390, 86, 24)),
                 ("D/ 213", (804, 391, 84, 23)), ("SP", (897, 394, 23, 16)), ("213", (938, 390, 47, 25)),
                 ("1,513", (1109, 390, 64, 24))]
        out = wsc.parse_status_row(split)
        self.assertEqual(out, {"stats": {"Vo": 368, "Da": 125, "Vi": 118, "Me": 213}, "sp": 213, "fans": 1513})

    def test_sp_band_retry(self):
        # 2026-10-25 live (wing11/12): the SP sub-ROI read the lone 「5」 as 「M」; the 30 px band reads it.
        self.assertEqual(wsc.sp_band((916, 375, 81, 45)), (922, 385, 71, 30))
        row = {(380, 375, 900, 45): [("/898 Da.D/ 216vi.E/144D / 265", (459, 390, 433, 24))],
               (1055, 375, 225, 45): [("57,458", (1096, 390, 73, 24))]}

        def status(sub, band):
            class Backend:
                def ocr(self, node, frame):
                    roi = tuple(node["roi"])
                    return row.get(roi) or (sub if roi == (916, 375, 81, 45) else band)
            ev = mock.Mock(backend=Backend(), nodes={"N": {"roi": [380, 375, 900, 45]}})
            ev.texts.side_effect = lambda name, frame: row[(380, 375, 900, 45)]
            with mock.patch.object(wsc, "status_regions", return_value=((916, 375, 81, 45), (1055, 375, 225, 45))):
                return wsc.read_status(ev, mock.Mock(image=None), "N")

        out = status([("M", (916, 375, 81, 45))], [("5", (965, 395, 16, 19))])
        self.assertEqual((out["sp"], out["fans"]), (5, 57458))
        self.assertEqual(out["stats"], {"Vo": 898, "Da": 216, "Vi": 144, "Me": 265})
        self.assertEqual(status([("213", (938, 390, 47, 25))], [("9", (0, 0, 1, 1))])["sp"], 213)  # first read wins
        self.assertIsNone(status([("M", (0, 0, 1, 1))], [("2", (0, 0, 1, 1)), ("4", (9, 0, 1, 1))])["sp"])  # split
        self.assertIsNone(status([], [("32185", (0, 0, 1, 1))])["sp"])               # not an SP counter

    def test_status_regions(self):
        import numpy as np
        image = np.full((720, 1280, 3), 240, dtype=np.uint8)
        image[380:410, 900:922] = (245, 130, 40)   # SP badge
        image[380:410, 993:1015] = (60, 200, 60)   # fan icon
        sp, fans = wsc.status_regions(image, (380, 375, 900, 45))
        self.assertEqual((sp[0], sp[0] + sp[2]), (915, 997))   # widened 2026-10-14: badge edge - 6 .. icon + 4
        self.assertEqual(fans[0], 1054)
        self.assertIsNone(wsc.status_regions(np.full((720, 1280, 3), 240, dtype=np.uint8), (380, 375, 900, 45)))

    def test_header(self):
        self.assertEqual(wsc.parse_season([("シースン2", (0, 0, 1, 1))]), 2)
        self.assertEqual(wsc.parse_season([("W.I.N.G.", (0, 0, 1, 1))]), "wing")
        self.assertEqual(wsc.parse_weeks_left([("6", (0, 0, 1, 1))]), 6)
        self.assertIsNone(wsc.parse_weeks_left([("6", (0, 0, 1, 1)), ("7", (9, 0, 1, 1))]))
        self.assertEqual(wsc.parse_fans_left([("数あと", (0, 0, 1, 1)), ("6,772人", (5, 0, 1, 1))]), 6772)
        self.assertEqual(wsc.parse_fans_left([("Congratulation!", (0, 0, 1, 1))]), "clear")

    def test_audition_and_live(self):
        panel = [("流行", (765, 120, 67, 36)), ("Vi.", (881, 119, 53, 38)), ("VO.", (994, 119, 55, 37)),
                 ("Da.", (1104, 119, 61, 38)), ("アイドルランクF以上", (870, 239, 202, 27)),
                 ("難易度:", (837, 291, 80, 24)), ("8", (922, 291, 24, 25)), ("+10000", (1048, 502, 109, 32))]
        out = wsc.parse_audition(panel)
        self.assertEqual((out["reward"], out["difficulty"], out["required_rank"]), (10000, 8, "F"))
        self.assertEqual(out["trend"], {"Vi": 1, "Vo": 2, "Da": 3})
        self.assertEqual(wsc.parse_audition([("難易度：13", (840, 290, 120, 24))])["difficulty"], 13)
        self.assertEqual(wsc.parse_live([("OFF", (1017, 39, 59, 31)), ("2倍速", (1110, 41, 62, 30))]),
                         {"auto": "OFF", "speed": 2})

    def test_unit_owned_choice(self):
        tabs = [(f"ユニット{i}", (26 + 112 * (i - 1), 88, 92, 28)) for i in range(1, 11)]
        self.assertEqual(wsc.parse_unit([("7/20", (64, 158, 62, 31))], tabs),
                         {"unit_index": 7, "unit_total": 20, "unit_page": 0})
        tabs2 = [(f"ユニット{i}", (26, 88, 92, 28)) for i in range(11, 21)]
        self.assertEqual(wsc.parse_unit([], tabs2)["unit_page"], 1)
        owned = [("961 / 1059", (565, 96, 131, 28)), ("221/ 545", (808, 96, 113, 28)),
                 ("201/595", (581, 132, 116, 28)), ("423 / 575", (806, 132, 115, 28))]
        self.assertEqual(wsc.parse_owned_stats(owned)["Me"], (423, 575))
        self.assertEqual(wsc.parse_choice({"Left": [("夏葉なら", (0, 0, 1, 1)), ("大丈夫", (0, 30, 1, 1))]}),
                         {"Left": "夏葉なら大丈夫"})
        # 2026-10-14 live: a two-option event; the window's 「8」 behind the gap read as Center 「B」 -> not an option
        self.assertEqual(wsc.parse_choice({"Left": [("いいよ", (0, 0, 1, 1))], "Center": [("B", (0, 0, 1, 1))],
                                           "Right": [("ごめん", (0, 0, 1, 1))]}),
                         {"Left": "いいよ", "Center": None, "Right": "ごめん"})


class RecommendedReader(unittest.TestCase):
    """推奨能力値 row (2026-10-09: the live S1 run read {} on every page). Native Maa OCR tokens of demo 2."""

    S1_503 = [("推奨能力値", (429, 407, 112, 28)), ("Vo.", (576, 415, 19, 13)), ("F", (604, 404, 20, 21)),
              ("eeee", (606, 429, 87, 8)), ("70", (654, 404, 38, 26)), ("Da.", (704, 411, 31, 19)),
              ("eee", (737, 429, 92, 8)), ("_", (766, 415, 28, 8)), ("-eee", (874, 429, 90, 8)),
              ("1_", (902, 417, 27, 5)), ("F", (1009, 404, 21, 22)), ("maeee", (1018, 429, 80, 8)),
              ("70", (1061, 405, 36, 24))]
    SLIDING_502 = [("推奨能力值", (524, 409, 109, 25)), ("Vo", (665, 411, 25, 18)), ("F", (697, 404, 21, 21)),
                   ("70", (749, 405, 35, 24)), ("Da.", (800, 412, 26, 17)), ("11", (861, 417, 26, 5)),
                   ("11", (995, 417, 27, 5)), ("F", (1104, 405, 17, 18))]
    MERGED_2352 = [("推奨能力値", (433, 406, 113, 30)), ("Vo.", (578, 411, 23, 18)), ("C 350", (604, 401, 96, 32)),
                   ("Da.", (711, 411, 27, 18)), ("-", (771, 415, 28, 8)), ("Vi.", (846, 407, 28, 24)),
                   ("元儿", (981, 407, 27, 24)), ("D", (1014, 408, 22, 20)), ("280", (1047, 404, 56, 27))]

    def test_s1_page_without_vi_label(self):
        self.assertEqual(wsc.parse_recommended(self.S1_503), {"Vo": 70, "Me": 70})

    def test_merged_rank_and_value(self):
        self.assertEqual(wsc.parse_recommended(self.MERGED_2352), {"Vo": 350, "Me": 280})

    def test_sliding_panel_and_blob_unread(self):
        self.assertEqual(wsc.parse_recommended(self.SLIDING_502), {})             # 95 px shift: still sliding
        self.assertEqual(wsc.parse_recommended([("推能力恒A500DA500", (400, 390, 760, 50))]), {})
        self.assertEqual(wsc.parse_recommended([("70", (654, 404, 38, 26))]), {})  # no anchor
        conflict = [("推奨能力値", (429, 407, 112, 28)), ("Da.", (604, 411, 31, 19))]   # anchors disagree
        self.assertEqual(wsc.parse_recommended(conflict + [("70", (654, 404, 38, 26))]), {})

    def test_full_width_row(self):
        row = [("推奨能力値", (429, 407, 110, 28)), ("Vo.", (574, 413, 17, 17)), ("B", (604, 404, 22, 24)),
               ("450", (640, 403, 52, 27)), ("Da.", (703, 411, 30, 20)), ("Vi.", (844, 409, 28, 18)),
               ("B", (1008, 405, 20, 22)), ("450", (1044, 403, 52, 27))]
        self.assertEqual(wsc.parse_recommended(row), {"Vo": 450, "Me": 450})


MAA_CACHE2 = ROOT / "dev_assets/private/wing-demo2-analysis/maa_roi_cache.json"


@unittest.skipUnless(MAA_CACHE2.is_file(), "private second W.I.N.G. demo Maa OCR cache not present")
class RecommendedNativeFrames(unittest.TestCase):
    """Exact values on settled native audition tabs of demo 2 (Maa OCR, ROI 400,390,760,50)."""

    EXPECTED = {"frame-000503.png": {"Vo": 70, "Me": 70}, "frame-000504.png": {"Vo": 70, "Me": 70},
                "frame-000666.png": {"Vo": 100, "Me": 70}, "frame-000776.png": {"Vo": 100, "Me": 70},
                "frame-000944.png": {"Vo": 120, "Me": 150}, "frame-001263.png": {"Vo": 390, "Me": 250},
                "frame-001562.png": {"Vo": 410, "Me": 280}, "frame-001600.png": {"Vo": 350, "Me": 280},
                "frame-001772.png": {"Vo": 380, "Me": 280}, "frame-001933.png": {"Vo": 500, "Me": 450},
                "frame-002359.png": {"Vo": 450, "Me": 450}, "frame-000502.png": {}, "frame-001863.png": {}}

    def test_exact(self):
        import json
        cache = json.loads(MAA_CACHE2.read_text(encoding="utf-8"))
        for name, want in self.EXPECTED.items():
            texts = [(t, tuple(b)) for t, _, b in cache[name]["400,390,760,50"]]
            self.assertEqual(wsc.parse_recommended(texts), want, name)


DEMO = ROOT / "dev_assets/private/recordings/wing-demo-20261005-0058-01/index.jsonl"
OCR = ROOT / "dev_assets/private/wing-demo-analysis/ocr.jsonl"


@unittest.skipUnless(DEMO.is_file() and OCR.is_file(), "private W.I.N.G. demo recording not present")
class DemoReplay(unittest.TestCase):
    """Walk all 476 demo samples through classifier, readers and runtime (boxes backend, zero input)."""

    @classmethod
    def setUpClass(cls):
        from tools import wing_demo_replay
        cls.report = wing_demo_replay.replay("boxes")

    def test_no_unguarded_click_and_no_input(self):
        summary = self.report["summary"]
        self.assertEqual(summary["unguarded_click_intents"], 0, self.report["unguarded"])
        self.assertEqual(summary["clicks_sent"], 0)
        self.assertEqual(summary["input_attempts"], 0)
        self.assertGreater(summary["click_intents"], 50)
        self.assertEqual(set(self.report["act_gate"]) - {"live_input_disabled", "target_not_verified",
                                                         "repeat_without_progress"}, set())

    def test_coverage(self):
        summary = self.report["summary"]
        self.assertEqual(summary["samples"], 476)
        self.assertGreaterEqual(summary["actionable_accepted"] / summary["actionable_frames"], 0.95)

    def test_readers(self):
        reads = self.report["reads"]
        for field in ("stats", "sp", "selected_room", "season", "reward", "difficulty", "auto", "unit_index"):
            good, total = map(int, reads[field]["exact"].split("/"))
            self.assertGreaterEqual(good / total, 0.8, field)


class SecondDemoReaders(unittest.TestCase):
    """Readers added from the second demo: Live phase/hand/judges/rivals, rooms, skill info bar."""

    def test_live_hand_and_phase(self):
        down = [("Vo.", (445, 636, 45, 36)), ("Vo.", (676, 637, 41, 34)), ("Vo.", (906, 639, 42, 32))]
        hand = wsc.live_hand(down)
        self.assertEqual([c["attribute"] for c in hand], ["Vo", "Vo", "Vo"])
        self.assertEqual(wsc.live_phase(down, hand), "select_card")
        raised = [("Vo.", (448, 607, 44, 29))] + down[1:]
        self.assertEqual(wsc.live_phase(raised, wsc.live_hand(raised)), "select_judge")
        timing = raised + [("APPEAL", (392, 479, 148, 43))]
        self.assertEqual(wsc.live_phase(timing, wsc.live_hand(timing)), "timing")
        cut = down + [("Appeal", (420, 300, 200, 60))]
        self.assertIsNone(wsc.live_phase(cut, wsc.live_hand(cut)))            # animation: never act
        self.assertIsNone(wsc.live_phase(down[:2], wsc.live_hand(down[:2])))  # a card unread: never act

    def test_live_rivals_never_store_our_name(self):
        texts = [("★08", (1004, 96, 81, 29)), ("4271474", (1061, 132, 93, 24)), ("★00", (1002, 174, 78, 33)),
                 ("アイドルE", (1084, 180, 90, 23)), ("470/470", (1060, 213, 94, 23))]
        out = wsc.live_rivals(texts)
        self.assertEqual((out["our_stars"], out["mental"], out["mental_max"], out["rival_stars"]), (8, 427, 474, [0]))
        self.assertNotIn("name", out)

    def test_live_judges_from_pixels(self):
        import numpy as np
        image = np.full((720, 1280, 3), 40, dtype=np.uint8)
        image[95:240, 15:140] = (240, 60, 120)        # Vo box
        image[430:575, 15:140] = (250, 150, 30)       # Vi box (Da left the stage)
        image[223:236, 24:131] = (20, 20, 20)         # Vo bar empty ...
        image[223:236, 24:77] = (240, 60, 120)        # ... half filled with our appeal
        texts = [("★2倍", (9, 283, 56, 25)), ("★1.5倍", (0, 448, 69, 28)), ("+Excellent!!", (53, 142, 241, 49))]
        judges = wsc.live_judges(image, texts)
        self.assertEqual([j["present"] for j in judges], [True, False, True])
        self.assertEqual([j["trend_rank"] for j in judges], [3, 1, 2])
        self.assertAlmostEqual(judges[0]["our_dealt"], 0.5, places=1)
        self.assertTrue(judges[0]["excellent"])

    def test_room_details(self):
        import numpy as np
        image = np.full((720, 1280, 3), (255, 220, 242), dtype=np.uint8)
        image[575:605, 300:330] = (90, 60, 50)        # one portrait under the Vo tab
        image[575:605, 340:370] = (90, 60, 50)        # a second one
        image[421:446, 447:473] = (16, 192, 21)       # 約束 clover on the Da tab
        texts = [("Lv.2", (338, 450, 64, 37)), ("Excellent!!", (345, 470, 90, 24)), ("Lv.", (506, 427, 34, 26)),
                 ("1", (540, 423, 21, 28))]
        rooms = wsc.room_details(texts, image)
        self.assertEqual([r["level"] for r in rooms[:2]], [2, 1])
        self.assertEqual([r["supports"] for r in rooms[:2]], [2, 0])
        self.assertEqual([r["promise"] for r in rooms], [False, True, False, False, False, False])
        self.assertEqual([r["excellent"] for r in rooms[:2]], [True, False])

    def test_skill_info_bar(self):
        base = [("必要", (1082, 561, 47, 26)), ("40", (1204, 555, 44, 33))]
        self.assertEqual(wsc.parse_skill_info(base + [("SP不足", (1100, 631, 124, 38))]),
                         {"cost": 40, "state": "sp_short"})
        self.assertEqual(wsc.parse_skill_info(base + [("未解放", (1097, 626, 128, 42))])["state"], "locked")
        self.assertEqual(wsc.parse_skill_info(base + [("習得する", (1098, 628, 127, 39))])["state"], "learnable")
        self.assertIsNone(wsc.parse_skill_info([("習得する", (1098, 628, 127, 39))]))
        # third demo #434: the node just learned shows 習得済み (must not read as learnable)
        self.assertEqual(wsc.parse_skill_info([("必要", (1082, 561, 47, 26)), ("0", (1215, 555, 30, 33)),
                                               ("習得済み", (1098, 627, 127, 39))]), {"cost": 0, "state": "learned"})

    def test_unit_counter_with_icon_letter(self):
        out = wsc.parse_unit([("G17/20", (32, 158, 94, 29))], [("ユニット17", (697, 90, 103, 23))])
        self.assertEqual(out, {"unit_index": 17, "unit_total": 20, "unit_page": 1})
        self.assertIsNone(wsc.parse_unit([("117/201", (32, 158, 94, 29))], [])["unit_index"])

    def test_end_readers(self):
        clear = [("プロデュースクリア報酬", (224, 52, 298, 28)), ("信頼度", (290, 146, 68, 31)), ("Lv.9", (625, 146, 71, 35)),
                 ("コミュニケーションレベル", (756, 135, 161, 18)), ("3/35", (961, 145, 80, 46))]
        self.assertEqual(wsc.read_end("ClearReward", clear)["trust_level"], 9)
        self.assertEqual(wsc.read_end("ClearReward", clear)["communication_level"], "3/35")
        birth = [("151/525", (278, 133, 111, 24)), ("119/ 520", (498, 131, 116, 30)), ("108/520", (278, 169, 111, 24)),
                 ("111 / 500", (502, 170, 112, 24)), ("408", (526, 92, 52, 31)),
                 ("ボーカルアピール", (92, 288, 177, 27)), ("Vocal1倍アピール", (52, 337, 158, 23)),
                 ("ダンスアピール", (93, 401, 156, 26))]
        out = wsc.read_end("FesBirth", birth)
        self.assertEqual(out["stats"], {"Vo": 151, "Da": 119, "Vi": 108, "Me": 111})
        self.assertEqual(out["fans"], 408)
        self.assertEqual(out["live_skills"], ["ボーカルアピール", "ダンスアピール"])
        book = [("スタミナ", (1149, 14, 95, 28)), ("270/300", (1158, 50, 80, 22)), ("ノウハウ", (400, 231, 60, 25)),
                ("VisualUP", (262, 272, 90, 25)), ("Lv.1", (565, 275, 40, 22)), ("Visual+10", (228, 301, 90, 20))]
        out = wsc.read_end("RewardDetail", book)
        self.assertEqual((out["reward_tab"], out["stamina"], out["knowhow_books"]),
                         ("knowhow", "270/300", [{"name": "VisualUP", "level": 1}]))

    def test_reward_ex_list(self):
        import numpy as np
        image = np.full((720, 1280, 3), 250, dtype=np.uint8)
        image[109:113, 307:349] = (128, 70, 40)          # bronze top border of the first icon (third demo #688)
        image[128:136, 392:400] = (255, 186, 213)        # P badge
        texts = [("月岡恋鐘", (465, 115, 96, 27)), ("Vocal初期値＋10", (378, 160, 163, 22)),
                 ("黛冬優子", (462, 430, 100, 33)), ("サポ-", (633, 476, 59, 25))]
        out = wsc.read_end("RewardDetail", texts, image=image)
        self.assertEqual(out["reward_tab"], "ex")
        self.assertEqual(out["ex_skills"], [{"name": "Vocal初期値+10", "idol": "月岡恋鐘", "tier": "bronze",
                                             "target": "produce"}])   # the cut line 「サポ-」 is dropped

    def test_skill_nodes(self):
        import numpy as np
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[:, :] = (119, 119, 119)                    # dark grey: not reachable
        image[300:400, 600:800] = (247, 247, 247)        # light hexagon: open
        image[500:600, 200:400] = (127, 111, 53)         # dark gold: event node
        texts = [("SP:30", (673, 400, 73, 29)), ("SP:40", (264, 600, 73, 29)), ("SP: 0", (900, 400, 67, 29)),
                 ("SP:50", (1000, 400, 73, 29)), ("SP:50", (474, 22, 75, 28))]   # last one: hexagon off canvas
        nodes = wsc.skill_nodes(texts, image)
        self.assertEqual([n["state"] for n in nodes], ["open", "event", "learned", "unreachable"])
        self.assertEqual(wsc.skill_node_counts(nodes)["learned"], 1)


DEMO2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02/index.jsonl"
OCR2 = ROOT / "dev_assets/private/wing-demo2-analysis/ocr.jsonl"


@unittest.skipUnless(DEMO2.is_file() and OCR2.is_file(), "private second W.I.N.G. demo recording not present")
class SecondDemoReplay(unittest.TestCase):
    """All 1358 samples of the second demo (items, 大富豪, manual Live) through classifier, readers and runtime."""

    @classmethod
    def setUpClass(cls):
        from tools import wing_demo_replay
        cls.report = wing_demo_replay.replay("boxes", labels=wing_demo_replay.LABELS_DEMO2)

    def test_no_unguarded_click_and_no_input(self):
        summary = self.report["summary"]
        self.assertEqual((summary["unguarded_click_intents"], summary["clicks_sent"], summary["input_attempts"],
                          summary["crashes"]), (0, 0, 0, 0), self.report["unguarded"])
        self.assertEqual(set(self.report["act_gate"]) - {"live_input_disabled", "target_not_verified",
                                                         "repeat_without_progress"}, set())

    def test_coverage_and_new_screens(self):
        summary = self.report["summary"]
        self.assertEqual(summary["samples"], 1358)
        self.assertGreaterEqual(summary["actionable_accepted"] / summary["actionable_frames"], 0.93)
        for label in ("ItemPopup", "ItemUseConfirm", "ItemUsed", "CheerHelp", "Options", "ActiveSkills"):
            good, total = map(int, self.report["per_label"][label].split("/"))
            self.assertEqual(good, total, label)
        good, total = map(int, self.report["per_label"]["Minigame"].split("/"))
        self.assertGreaterEqual(good / total, 0.9)
        self.assertEqual(self.report["stops"].get("item_used_unexpectedly"), 2)   # never acted on a used item

    def test_readers(self):
        reads = self.report["reads"]
        for field in ("live_phase", "selected_card", "memory_gauge", "judges_present", "room_supports",
                      "room_excellent", "room_promise", "skill_info", "stats", "sp"):
            good, total = map(int, reads[field]["exact"].split("/"))
            self.assertGreaterEqual(good / total, 0.9, field)

    def test_click_evidence_for_verified_targets(self):
        targets = self.report["click_targets"]["targets"]
        for intent in ("rank_next", "schedule_lesson_tab", "item_popup_close", "live_card_1", "live_card_2",
                       "live_card_3", "live_judge_2", "live_judge_3", "live_appeal_timing", "cheer_close",
                       "active_skills_close"):
            self.assertTrue(targets[intent]["verified"], intent)
            self.assertGreaterEqual(targets[intent]["changed"], 1, intent)
        self.assertFalse(targets["item_use_cancel"]["verified"])


DEMO3 = ROOT / "dev_assets/private/recordings/wing-demo3-20261006-1643-03/index.jsonl"
OCR3 = ROOT / "dev_assets/private/wing-demo3-analysis/ocr-03.jsonl"


@unittest.skipUnless(DEMO3.is_file() and OCR3.is_file(), "private third W.I.N.G. demo recording not present")
class ThirdDemoReplay(unittest.TestCase):
    """All samples of the three third-demo segments (idol list, unit edits, 諦める, the full end flow)."""

    @classmethod
    def setUpClass(cls):
        from tools import wing_demo_replay
        cls.report = wing_demo_replay.replay("boxes", labels=wing_demo_replay.LABELS_DEMO3)

    def test_no_unguarded_click_and_no_input(self):
        summary = self.report["summary"]
        self.assertEqual((summary["unguarded_click_intents"], summary["clicks_sent"], summary["input_attempts"],
                          summary["crashes"]), (0, 0, 0, 0), self.report["unguarded"])
        self.assertEqual(sorted(self.report["segments"]), ["01", "02", "03"])

    def test_coverage_and_end_flow(self):
        summary = self.report["summary"]
        self.assertGreaterEqual(summary["actionable_accepted"] / summary["actionable_frames"], 0.95)
        for label in ("AbandonDialog", "UnitEditDialog", "FesBirthConfirm", "TotalEval", "ClearReward", "Reward",
                      "RewardDetail", "RewardSellMode", "EndDestructiveDialog", "FesDetail", "FesDetailPopup",
                      "IdolList", "StartConfirm"):
            good, total = map(int, self.report["per_label"][label].split("/"))
            self.assertEqual(good, total, label)
        stops = self.report["stops"]
        self.assertGreaterEqual(stops.get("abandon_dialog_not_automated", 0), 1)    # 諦める never answered
        self.assertGreaterEqual(stops.get("reward_sell_mode", 0), 1)                # 売却する never pressed
        self.assertGreaterEqual(stops.get("produce_end_destructive_dialog", 0), 1)

    def test_readers(self):
        reads = self.report["reads"]
        for field in ("skill_info", "trust_level", "communication_level", "stats", "sp_lost", "unit_page",
                      "knowhow_books", "reward_tab"):
            good, total = map(int, reads[field]["exact"].split("/"))
            self.assertEqual(good, total, field)

    def test_click_evidence(self):
        targets = self.report["click_targets"]["targets"]
        for intent in ("total_eval_tap", "fes_detail_ok", "eval_next", "clear_reward_ok", "fes_birth",
                       "fes_birth_ok"):
            self.assertTrue(targets[intent]["verified"], intent)
            self.assertGreaterEqual(targets[intent]["changed"], 1, intent)
        # ‹ was never tapped in this demo; enabled 2026-10-13 with the user's approval from a live frame.
        self.assertTrue(targets["unit_page_prev"]["verified"])


if __name__ == "__main__":
    unittest.main()
