"""強化週間 limited auditions: the final week of each season is surveyed while
an event window is open, the limited page is told apart from a regular page with the same fan reward, and the planned
page is entered only when both reward and limited id match."""
from __future__ import annotations

import unittest
from unittest import mock

from agent.core import produce_config as pc
from agent.core import wing_audition as wa
from agent.core import wing_nav as nav
from agent.core import wing_score as wsco
from agent.core import wing_screen as wsc

BOX = (960, 300, 120, 30)
ROOMS = [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "label_ok": True, "selected": False, "level": 3,
          "supports": 0}]
WEEK = {"season": 3, "weeks_left": 1, "stats": {"Vo": 600, "Da": 200, "Vi": 200, "Me": 320}, "fans": 60_000,
        "sp": 10, "stamina_pct": 80, "rooms": ROOMS, "selected_room": None}
PAGE = {"season": 3, "weeks_left": 1, "reward": 50_000, "difficulty": 15, "required_rank": "C",
        "recommended": {"Vo": 410, "Me": 280}, "trend": {"Vo": 1, "Da": 2, "Vi": 3}}


def runtime() -> nav.WingNavRuntime:
    rt = nav.WingNavRuntime(nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(pc.default_config("true_end"))))
    rt.begin()
    return rt


class ReaderTests(unittest.TestCase):
    def test_limited_names_are_recognised_and_regular_pages_are_not(self):
        self.assertEqual(wsc.special_audition([("おはよう街角の朝ごはん", BOX)]), "machikado")
        self.assertEqual(wsc.special_audition([("エレぇベスト", BOX)]), "ereebest")
        self.assertEqual(wsc.special_audition([("めざせアイドルNo.1", BOX)]), "mezase")
        self.assertEqual(wsc.special_audition([("今夜は帰りたくない！", BOX)]), "kaeritakunai")
        self.assertIsNone(wsc.special_audition([("THE LEGEND", BOX), ("+100000", BOX)]))
        self.assertEqual(wsc.parse_audition([("エレぇベスト", BOX), ("+100000", BOX)])["special"], "ereebest")

    def test_limited_page_layout_is_an_audition_tab_and_named_by_season(self):
        # live 2026-10-28 S3 (wing_samples/2026-10-28/20261028T140150361610Z): 「出場条件」 sits 20 px lower than on a
        # regular page (outside its anchor ROI) and the logo 「めざせアイドルNo.1」 OCRs as noise; 「特殊ルール」 reads
        # cleanly and stands in for 「出場条件」 in the AuditionTab guard.
        nodes = wsc.load_nodes()
        self.assertIn("SC.Wing.Anchor.Audition.ConditionOrSpecial", nodes["SC.Wing.Screen.AuditionTab"]["all_of"])
        texts = [("スケジュール選択", (49, 19, 205, 32)), ("ファン人数あと", (706, 13, 152, 26)),
                 ("オーディション", (418, 111, 212, 41)), ("特別オーディション", (528, 217, 172, 27)),
                 ("曲子せ", (480, 252, 150, 57)), ("出場条件", (853, 225, 103, 33)), ("特殊ルール", (1009, 227, 104, 31)),
                 ("難易度：20", (833, 284, 128, 36)), ("+50000", (1048, 502, 109, 32)),
                 ("振り返り", (928, 643, 110, 38)), ("決定", (1140, 637, 76, 47))]
        nodes = wsc.load_nodes()
        classifier = wsc.ScreenClassifier(nodes, wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})
        self.assertEqual(classifier.classify(wsc.Frame(texts)).screen, "AuditionTab")
        regular = [t for t in texts if t[0] not in ("出場条件", "特殊ルール", "特別オーディション")]
        sliding_locked_title = regular + [("出場条件", (866, 228, 136, 43))]          # demo2 #1890
        self.assertNotEqual(classifier.classify(wsc.Frame(sliding_locked_title)).screen, "AuditionTab")
        with mock.patch.object(wsc, "parse_season", return_value=3):
            out = wsc.read_screen("AuditionTab", wsc.Evaluator(nodes, wsc.BoxBackend()), wsc.Frame(texts))
        self.assertEqual((out["reward"], out["special"]), (50_000, "mezase"))
        with mock.patch.object(wsc, "parse_season", return_value=3):
            plain = [t for t in texts if t[0] not in ("特殊ルール", "特別オーディション")]
            self.assertIsNone(wsc.read_screen("AuditionTab", wsc.Evaluator(nodes, wsc.BoxBackend()),
                                              wsc.Frame(plain))["special"])

    def test_event_window(self):
        self.assertTrue(wsco.special_window_active("2026-10-28"))
        self.assertTrue(wsco.special_window_active("2026-11-10"))
        self.assertFalse(wsco.special_window_active("2026-11-11"))


class FlowTests(unittest.TestCase):
    def test_final_week_is_surveyed_only_while_an_event_runs(self):
        rt = runtime()
        with mock.patch.object(wsco, "special_window_active", return_value=True):
            survey = rt.decide("Schedule", WEEK)
        self.assertEqual(survey.reason, "survey_auditions")
        self.assertTrue(rt.memory.auditions_seen["special_week"])
        rt = runtime()
        with mock.patch.object(wsco, "special_window_active", return_value=False):
            plan = rt.decide("Schedule", WEEK)
        self.assertNotEqual(plan.reason, "survey_auditions")
        self.assertEqual(rt.memory.auditions_seen["source"], "table")
        rt = runtime()
        with mock.patch.object(wsco, "special_window_active", return_value=True):
            plan = rt.decide("Schedule", {**WEEK, "weeks_left": 4})      # not a final week: table as before
        self.assertEqual(rt.memory.auditions_seen["source"], "table")

    def test_survey_keeps_both_pages_of_one_reward_and_skips_an_unread_page(self):
        rt = runtime()
        with mock.patch.object(wsco, "special_window_active", return_value=True):
            rt.decide("Schedule", WEEK)
        self.assertEqual(rt.decide("AuditionTab", PAGE).intent, "audition_next")
        unread = rt.decide("AuditionTab", {**PAGE, "reward": None})
        self.assertEqual((unread.intent, unread.reason), ("audition_next", "survey_page_unread"))
        limited = {**PAGE, "special": "mezase", "difficulty": None, "recommended": {"Vo": 500, "Me": 350}}
        self.assertEqual(rt.decide("AuditionTab", limited).intent, "audition_next")
        done = rt.decide("AuditionTab", PAGE)                         # back at the first page
        self.assertEqual((done.intent, done.reason), ("schedule_lesson_tab", "survey_done"))
        pages = rt.memory.auditions_seen["pages"]
        self.assertEqual([(p["reward"], p["special"]) for p in pages], [(50_000, None), (50_000, "mezase")])
        state, problems = nav.build_week_state(WEEK, rt.memory)
        self.assertEqual(problems, [])
        self.assertEqual([a.id for a in state.auditions], ["s3_50k", "s3_50k_mezase"])
        rt.memory.auditions_seen["pages"][1]["recommended"] = {}
        state, _ = nav.build_week_state(WEEK, rt.memory)
        self.assertEqual([a.available for a in state.auditions], [True, False])

    def test_planned_limited_page_is_entered_and_a_same_reward_regular_page_is_turned(self):
        rt = runtime()
        m = rt.memory
        m.pending, m.pending_audition, m.pending_special, m.audition_pages = "audition", 50_000, "mezase", 0
        m.auditions_seen = {"key": [3, 1], "pages": [], "done": True, "special_week": True}
        self.assertEqual(rt.decide("AuditionTab", PAGE).intent, "audition_next")
        self.assertEqual(rt.decide("AuditionTab", {**PAGE, "reward": None}).intent, "audition_next")
        enter = rt.decide("AuditionTab", {**PAGE, "special": "mezase"})
        self.assertEqual(enter.intent, "schedule_decide")
        self.assertEqual(m.entered_audition["special"], "mezase")
        self.assertIsNone(m.pending_special)

    def test_choose_audition_needs_the_limited_id_and_reads_unread_pages_as_other(self):
        import types
        m = types.SimpleNamespace(pending="audition", pending_audition=100_000, pending_special="ereebest",
                                  audition_pages=0, auditions_seen={"special_week": True})
        self.assertEqual(wa.choose_audition({"reward": 100_000}, m)["action"], "audition_next")
        self.assertEqual(wa.choose_audition({"reward": None}, m)["action"], "audition_next")
        self.assertEqual(wa.choose_audition({"reward": 100_000, "special": "ereebest"}, m)["action"],
                         "schedule_decide")
        m.auditions_seen = {}
        m.pending_special = None
        self.assertEqual(wa.choose_audition({"reward": None}, m)["reason"], "audition_reward_unread")

    def test_the_page_arrow_may_turn_every_audition_page(self):
        # live 2026-10-28: the 4th audition_next of S1's final-week survey was refused as a repeat
        rt = nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True))
        rt.begin()
        click = nav._click("audition_next", "survey_page")
        for _ in range(nav.AUDITION_PAGES):
            self.assertEqual(rt.act("AuditionTab", click), (True, "sent"))
        self.assertEqual(rt.act("AuditionTab", click), (False, "repeat_without_progress"))

    def test_chosen_audition_id_names_the_limited_audition(self):
        self.assertEqual(nav._special_of({"audition_id": "s2_100k_ereebest"}), "ereebest")
        self.assertIsNone(nav._special_of({"audition_id": "s2_15k"}))


if __name__ == "__main__":
    unittest.main()
