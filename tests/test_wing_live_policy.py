"""W.I.N.G. Live focus policy (2026-10-09b): trend-#1 target, card inspection/switching, secured 1st place, tooltip and
card-picture readers, and the recorded-demo replay (tools/wing_play_replay.py). Offline, zero input."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core import wing_live_policy as wlp

ROOT = Path(__file__).resolve().parent.parent
DEMO2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02"
OCR2 = ROOT / "dev_assets/private/wing-demo2-analysis/ocr.jsonl"
DEMO3 = ROOT / "dev_assets/private/recordings/wing-demo3-20261006-1643-03"
LIVE = ROOT / "dev_assets/private/wing-live-20261009"
LIVE_OCR = LIVE / "live-frames-ocr.json"        # whole-frame Maa OCR of live-now.png (tools/wing_sample_ocr.py)


def judges(trend=(3, 1, 2), left=()):
    return [{"attribute": a, "trend_rank": t, "left": a in left, "present": a not in left}
            for a, t in zip(("Vo", "Da", "Vi"), trend)]


class TargetTests(unittest.TestCase):
    def test_trend1_focus_and_fallbacks(self):
        self.assertEqual(wlp.target_judge(judges(), primary="Vo", estimate=None),
                         {"attribute": "Da", "reason": "focus_trend1"})
        self.assertEqual(wlp.target_judge(judges(left=("Da",)), primary="Vo", estimate=None)["attribute"], "Vi")
        own = wlp.target_judge(judges(), primary="Vo", estimate={"plan": "own_attribute", "p_first": 0.4})
        self.assertEqual(own, {"attribute": "Vo", "reason": "estimate_prefers_own_attribute"})
        self.assertEqual(wlp.target_judge(judges(), primary="Vo", estimate={"plan": "trend1_focus"})["attribute"],
                         "Da")
        self.assertEqual(wlp.target_judge(judges((None, 1, 2)), primary="Vo", estimate=None)["reason"],
                         "trend_unread")
        self.assertEqual(wlp.target_judge(judges(left=("Vo", "Da", "Vi")), primary="Vo", estimate=None)["reason"],
                         "no_judge_present")

    def test_card_choice(self):
        hand = [{"slot": 1, "attribute": "Vo", "multiplier": 3.5, "appeal": 1315, "buff": 80},
                {"slot": 2, "attribute": "Vo"}, {"slot": 3, "attribute": "Vo"}]
        self.assertEqual(wlp.choose_card(hand, "Da", None)["slot"], 2)                 # inspect an unread card
        first = wlp.choose_card(hand, "Da", None, selected=1, inspected=[1])
        self.assertEqual((first["action"], first["slot"]), ("select", 2))
        capped = wlp.choose_card(hand, "Da", None, selected=1, inspected=[1], switches=wlp.MAX_SWITCHES)
        self.assertEqual(capped["action"], "play")                                     # bounded inspection
        known = [dict(hand[0]), {"slot": 2, "attribute": "Vo", "multiplier": 5.0, "appeal": 1315, "buff": 80},
                 {"slot": 3, "attribute": "Vo", "multiplier": 2.5, "appeal": 1315, "buff": 80}]
        self.assertEqual(wlp.choose_card(known, "Da", None), {"action": "select", "slot": 2,
                                                              "reason": "best_known_card"})
        self.assertEqual(wlp.choose_card(known, "Da", None, selected=1)["reason"], "switch_to_best_card")
        self.assertEqual(wlp.choose_card(known, "Da", None, selected=2)["action"], "play")
        # the Excellent bonus: a Da card on the Da judge beats a stronger Vo card when the stats are close
        mixed = [{"slot": 1, "attribute": "Vo", "multiplier": 3.0}, {"slot": 2, "attribute": "Da", "multiplier": 2.0}]
        self.assertEqual(wlp.choose_card(mixed, "Da", {"Vo": 500, "Da": 450})["slot"], 2)
        self.assertEqual(wlp.choose_card(mixed, "Da", {"Vo": 1300, "Da": 300})["slot"], 1)

    def test_secured_first(self):
        state = {"our_stars": 120, "rival_stars": [20, 15], "mental": 400, "mental_max": 500,
                 "judges": [{"trend_rank": 3, "left": True}, {"trend_rank": 1, "left": True},
                            {"trend_rank": 2, "left": False}]}
        self.assertTrue(wlp.secured_first(state)["secured"])                           # 100 > 15+6+9
        self.assertEqual(wlp.first_place_estimate(state), 1.0)
        self.assertFalse(wlp.secured_first({**state, "our_stars": 40})["secured"])
        self.assertEqual(wlp.secured_first({**state, "mental": 100})["reason"], "mental_low")
        self.assertEqual(wlp.secured_first({**state, "our_stars": None})["reason"], "stars_unread")
        self.assertEqual(wlp.first_place_estimate({**state, "our_stars": 20}), 0.5)


class ExcellentCardTests(unittest.TestCase):
    """「Excellent」 appeal card (2026-10-10 live-now.png): attribute "All" = Excellent on any judge."""

    def test_all_card_matches_every_target(self):
        self.assertTrue(wlp.matches("All", "Vi") and wlp.matches("Vi", "Vi"))
        self.assertFalse(wlp.matches("Vo", "Vi") or wlp.matches(None, "Vi"))
        stats = {"Vo": 245, "Da": 128, "Vi": 108}
        self.assertEqual(wlp.card_value({"attribute": "All", "multiplier": 3.0}, "Vi", stats), 3.0 * 108 * 2.0)
        # unread hand Vo / All / Vo against trend #1 Vi: the All card is inspected first (it gets Excellent)
        hand = [{"slot": 1, "attribute": "Vo"}, {"slot": 2, "attribute": "All"}, {"slot": 3, "attribute": "Vo"}]
        self.assertEqual(wlp.choose_card(hand, "Vi", stats), {"action": "select", "slot": 2,
                                                              "reason": "inspect_unknown_card"})

    def test_excellent_tooltip_is_all(self):
        texts = [("心に住む人", (492, 493, 120, 23)), ("Excellent3倍アピール", (494, 516, 250, 22)),
                 ("Vo.", (580, 424, 42, 22)), ("1315+80%", (636, 426, 110, 18))]
        self.assertEqual(wlp.parse_tooltip(texts)["attribute"], "All")
        self.assertEqual(wlp.parse_tooltip(texts)["multiplier"], 3.0)

    def test_hand_reader_and_ranking_labels(self):
        from agent.core import wing_screen as wsc
        down = [("Vo.", (452, 643, 32, 22)), ("Excellen", (680, 665, 48, 13)), ("Vo.", (906, 637, 39, 34))]
        hand = wsc.live_hand(down)
        self.assertEqual([(c["attribute"], c["raised"]) for c in hand], [("Vo", False), ("All", False),
                                                                         ("Vo", False)])
        # rivals' judgement labels beside the ranking panel are not our appeal animation
        self.assertEqual(wsc.live_phase(down + [("Perfect!", (878, 189, 128, 42))], hand), "select_card")
        self.assertIsNone(wsc.live_phase(down + [("Perfect!", (420, 300, 200, 60))], hand))
        import numpy as np
        image = np.full((720, 1280, 3), (250, 200, 230), dtype=np.uint8)
        image[664:674, 680:700] = (90, 120, 240)      # blue → orange gradient word in slot 2, lowered
        image[664:674, 720:740] = (250, 170, 60)
        pixel = wsc.live_hand(down[::2], image)
        self.assertEqual([(c["attribute"], c["raised"]) for c in pixel], [("Vo", False), ("All", False),
                                                                          ("Vo", False)])
        raised = np.roll(image, -wsc.LIVE_EXCELLENT_LIFT, axis=0)
        self.assertEqual(wsc.live_hand(down[::2], raised)[1], {"slot": 2, "attribute": "All", "raised": True})
        self.assertIsNone(wsc.live_hand(down[::2], np.full_like(image, 200))[1]["attribute"])


class StarterCardTests(unittest.TestCase):
    """Green-sparkle starter card (メンタル 5% 回復): read as "Other" so the phase is known; never played."""
    FRAME = Path(__file__).resolve().parents[1] / "dev_assets/private/live-20261015n/samples/live-green-card-turn1.png"

    def test_synthetic_icon_raised_and_lowered(self):
        import numpy as np
        from agent.core import wing_screen as wsc
        down = [("Vo.", (452, 643, 32, 22)), ("Vo.", (680, 643, 32, 22))]
        image = np.full((720, 1280, 3), (250, 200, 230), dtype=np.uint8)
        image[640:682, 905:960] = (60, 210, 120)       # green icon of slot 3, lowered (centroid ~661)
        hand = wsc.live_hand(down, image)
        self.assertEqual(hand[2], {"slot": 3, "attribute": "Other", "raised": False})
        self.assertEqual(wsc.live_phase(down, hand), "select_card")
        raised = np.roll(image, -30, axis=0)
        self.assertTrue(wsc.live_hand(down, raised)[2]["raised"])

    def test_policy_never_plays_it(self):
        hand = [{"slot": 1, "attribute": "Vo"}, {"slot": 2, "attribute": "Vo"}]
        self.assertNotIn(3, [wlp.choose_card(hand, "Vo", {"Vo": 300})["slot"]])

    @unittest.skipUnless(FRAME.is_file(), "private live frame absent")
    def test_live_frame(self):
        import numpy as np
        from PIL import Image
        from agent.core import wing_screen as wsc
        image = np.asarray(Image.open(self.FRAME).convert("RGB"))
        self.assertIsNone(wsc._special_card_pixels(image, 310))
        self.assertFalse(wsc._special_card_pixels(image, 770))


class ReaderTests(unittest.TestCase):
    def test_tooltip(self):
        texts = [("にゃんにゃんにゃ～ん♡+", (492, 493, 218, 23)), ("Vocal5倍アピール[興味無視]/Vocal60", (494, 516, 295, 22)),
                 ("％UP[3ターン]/自身のメンタルを10%", (494, 532, 291, 26)), ("Vo.", (580, 424, 42, 22)),
                 ("1315+80%", (636, 426, 110, 18))]
        self.assertEqual(wlp.parse_tooltip(texts), {"name": "にゃんにゃんにゃ~ん♡+", "multiplier": 5.0,  # NFKC (～→~)
                                                    "attribute": "Vo", "appeal": 1315, "buff": 80})
        cut = [("DIVEIN!+", (492, 541, 93, 24)), ("Voca|5倍アピール/4ターンの間回邇時", (493, 566, 288, 24)),
               ("Vo.", (576, 476, 41, 20)), ("1315+80%", (638, 475, 108, 22))]
        self.assertEqual(wlp.parse_tooltip(cut)["multiplier"], 5.0)
        self.assertIsNone(wlp.parse_tooltip([]))

    def test_unread_multiplier_is_reread_from_the_tooltip_box(self):
        # live 2026-10-28 (wing_samples/2026-10-28/20261028T132431739764Z): whole-frame OCR merged the two lines
        from unittest import mock
        from agent.core import wing_screen as wsc
        whole = [("かわいいひと", (492, 492, 110, 22)), ("Y12-5 ビ-ル[奥味無视1/奥味0", (482, 504, 311, 63)),
                 ("Vo.", (582, 426, 36, 20)), ("604", (638, 426, 40, 20))]
        box = [("かわいいひと", (493, 494, 109, 20)), ("Vocal2.5倍アピール[興味無視]/興味0.", (494, 518, 288, 20)),
               ("7倍[1夕-ソ]", (496, 535, 104, 19)), ("Vo.", (586, 427, 30, 18)), ("604", (637, 424, 42, 21))]

        class Backend(wsc.BoxBackend):
            rois = []

            def ocr(self, node, frame):
                self.rois.append(tuple(node.get("roi", ())))
                return box if tuple(node.get("roi", ())) == wsc.LIVE_TOOLTIP_ROI else whole

        def live(phase, tip):
            return lambda texts, image: {"live_phase": phase, "tooltip": tip}
        evaluator = wsc.Evaluator(wsc.load_nodes(), Backend())
        frame = wsc.Frame(whole)
        with mock.patch.object(wsc, "read_live", live("select_judge", wlp.parse_tooltip(whole))):
            out = wsc.read_screen("Live", evaluator, frame)
        self.assertEqual((out["tooltip"]["multiplier"], out["tooltip"]["attribute"]), (2.5, "Vo"))
        Backend.rois.clear()
        with mock.patch.object(wsc, "read_live", live("select_judge", {"multiplier": 3.0})):
            self.assertEqual(wsc.read_screen("Live", evaluator, frame)["tooltip"], {"multiplier": 3.0})
        with mock.patch.object(wsc, "read_live", live("select_card", None)):
            wsc.read_screen("Live", evaluator, frame)
        self.assertNotIn(wsc.LIVE_TOOLTIP_ROI, Backend.rois)

    def test_card_cache(self):
        cards = {}
        wlp.remember(cards, [10] * 72, {"multiplier": 3.5, "appeal": 1315})
        wlp.remember(cards, [12] * 72, {"multiplier": 3.5, "buff": 80})      # same picture: merged
        wlp.remember(cards, [200] * 72, {"multiplier": 5.0})
        wlp.remember(cards, None, {"multiplier": 2.0})
        self.assertEqual(len(cards), 2)
        self.assertEqual(wlp.lookup(cards, [11] * 72)["multiplier"], 3.5)
        self.assertNotIn("buff", wlp.lookup(cards, [11] * 72))
        self.assertNotIn("appeal", wlp.lookup(cards, [11] * 72))
        self.assertIsNone(wlp.lookup(cards, [100] * 72))

    def test_a_picture_with_two_names_is_inspected_again(self):
        # 2026-10-29 wing6: the P idol's portrait carried GAP+, かわいいひと and HoPPoPらびっつ in one Live
        cards = {}
        wlp.remember(cards, [40] * 72, {"name": "GAP+", "multiplier": 3.5})
        self.assertEqual(wlp.lookup(cards, [41] * 72)["name"], "GAP+")
        wlp.remember(cards, [41] * 72, {"name": "かわいいひと", "multiplier": 2.5})
        self.assertIsNone(wlp.lookup(cards, [40] * 72))
        wlp.remember(cards, [90] * 72, {"name": "ボーカルアピールV+++++", "multiplier": 3.5}, shared=True)
        self.assertIsNone(wlp.lookup(cards, [90] * 72))
        self.assertEqual(wlp.shared_portraits([[40] * 72, [42] * 72, [200] * 72]), {0, 1})
        self.assertEqual(wlp.shared_portraits([[40] * 72, None, [200] * 72]), set())

    def test_switch_keeps_the_turns_reading_of_a_shared_picture(self):
        from types import SimpleNamespace
        p = [40] * 72
        live = {"turn": 1, "judges": [{"attribute": a, "trend_rank": t, "left": False}
                                      for a, t in (("Vo", 1), ("Da", 2), ("Vi", 3))],
                "options": [{"id": f"card{i}", "attribute": "Vo"} for i in (1, 2, 3)]}
        memory = SimpleNamespace(live_step=None, live_cards={})
        first = wlp.plan_turn({"live_phase": "select_card", "live_state": live, "hand_sigs": [p, p, [200] * 72]},
                              memory, primary="Vo", estimate=None, stats=None)
        self.assertEqual((first["action"], first["slot"]), ("card", 1))
        memory.live_step = {"turn": 1, "step": "card", "slot": 1, "sigs": first["sigs"], "switches": 0,
                            "inspected": [1]}
        read = {"live_phase": "select_judge", "live_state": live, "selected_card": 1, "hand_sigs": [None, p, [200] * 72],
                "tooltip": {"name": "GAP+", "multiplier": 4.0, "attribute": "Vo"}}
        switch = wlp.plan_turn(read, memory, primary="Vo", estimate=None, stats=None)
        self.assertEqual((switch["action"], switch["slot"], switch["reads"]["1"]["multiplier"]), ("card", 2, 4.0))
        self.assertTrue(memory.live_cards["card1"]["shared"])           # two cards of the hand show this picture
        memory.live_step = {**memory.live_step, "slot": 2, "switches": 1, "inspected": [1, 2],
                            "reads": switch["reads"]}
        back = wlp.plan_turn({**read, "selected_card": 2, "hand_sigs": [p, None, [200] * 72],
                              "tooltip": {"name": "かわいいひと", "multiplier": 2.5, "attribute": "Vo"}},
                             memory, primary="Vo", estimate=None, stats=None)
        self.assertEqual((back["action"], back["slot"]), ("card", 3))   # card 3 unread: inspect it next
        memory.live_step = {**memory.live_step, "slot": 3, "switches": 2, "inspected": [1, 2, 3],
                            "reads": back["reads"]}
        last = wlp.plan_turn({**read, "selected_card": 3, "hand_sigs": [p, p, None],
                              "tooltip": {"name": "ボーカルアピールV+++++", "multiplier": 3.5, "attribute": "Vo"}},
                             memory, primary="Vo", estimate=None, stats=None)
        self.assertEqual((last["action"], last["slot"], last["reason"]), ("card", 1, "switch_to_best_card"))

    def test_cached_dynamic_values_do_not_override_current_stats_or_same_turn_tooltip(self):
        from types import SimpleNamespace
        sigs = [[10] * 72, [120] * 72, [230] * 72]
        # These legacy entries were read in different auditions/turns.
        cache = {str(i): {"sig": sigs[i], "attribute": "Vo", "multiplier": mult, "appeal": power, "buff": buff}
                 for i, (mult, power, buff) in enumerate(((5, 300, 0), (2.5, 1000, 80), (1, 300, 0)))}
        memory = SimpleNamespace(live_cards=cache, live_step=None)
        live = {"turn": 1, "judges": judges((1, 2, 3)),
                "options": [{"id": f"card{i}", "attribute": "Vo"} for i in (1, 2, 3)]}
        read = {"live_phase": "select_card", "live_state": live, "hand_sigs": sigs}
        plan = wlp.plan_turn(read, memory, primary="Vo", estimate=None, stats={"Vo": 1000})
        self.assertEqual((plan["action"], plan["slot"]), ("card", 1))
        memory.live_step = {"sigs": sigs, "inspected": [2], "switches": 0}
        selected = {**read, "live_phase": "select_judge", "selected_card": 2,
                    "tooltip": {"attribute": "Vo", "multiplier": 2.5, "appeal": 1000, "buff": 80}}
        plan = wlp.plan_turn(selected, memory, primary="Vo", estimate=None, stats={"Vo": 300})
        self.assertEqual((plan["action"], plan["slot"]), ("card", 1))  # fresh base/buff also apply to the 5x card
        memory.live_step = {"sigs": sigs, "inspected": [1, 2], "switches": 1}
        expired = {**selected, "selected_card": 1,
                   "tooltip": {"attribute": "Vo", "multiplier": 5, "appeal": 1000, "buff": 0}}
        self.assertEqual(wlp.plan_turn(expired, memory, primary="Vo", estimate=None, stats={"Vo": 1000})["action"], "judge")


def _ocr(seq):
    for line in OCR2.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["seq"] == seq:
            return [(t, tuple(b)) for t, b in row["texts"]]
    raise KeyError(seq)


@unittest.skipUnless((DEMO2 / "frame-001946.png").is_file() and OCR2.is_file() and DEMO3.is_dir(),
                     "private W.I.N.G. recordings not present")
class NativeTests(unittest.TestCase):
    def test_live_readers_on_own_play_frames(self):
        from agent.core import wing_screen as wsc
        from tools import wing_demo_replay as replay
        card = replay.load_rgb(DEMO2 / "frame-001946.png")
        judge = replay.load_rgb(DEMO2 / "frame-001952.png")
        a, b = wsc.read_live(_ocr(1946), card), wsc.read_live(_ocr(1952), judge)
        self.assertEqual((a["live_phase"], b["live_phase"], b["selected_card"]), ("select_card", "select_judge", 1))
        # the lowered cards keep their picture while card 1 is raised
        self.assertTrue(wlp.same_card(a["hand_sigs"][1], b["hand_sigs"][1]))
        self.assertTrue(wlp.same_card(a["hand_sigs"][2], b["hand_sigs"][2]))
        self.assertFalse(wlp.same_card(a["hand_sigs"][0], a["hand_sigs"][1]))
        self.assertEqual(b["tooltip"]["multiplier"], 3.5)
        self.assertEqual(wlp.target_judge([{**j, "present": not j["left"]} for j in a["live_state"]["judges"]],
                                          primary="Vo", estimate=None)["attribute"], "Da")   # the user hit Da

    def test_excellent_card_on_old_demo_frames(self):
        from agent.core import wing_screen as wsc
        from tools import wing_demo_replay as replay
        for seq, slot in ((794, 2), (997, 1)):
            hand = wsc.live_hand(_ocr(seq), replay.load_rgb(DEMO2 / f"frame-{seq:06d}.png"))
            self.assertEqual(hand[slot - 1], {"slot": slot, "attribute": "All", "raised": False}, seq)

    def test_recorded_play_replay(self):
        from tools import wing_play_replay
        summary = wing_play_replay.analyse("boxes")["summary"]
        ok, total = (int(v) for v in summary["sp_truth"].split("/"))
        self.assertEqual(ok, total)
        self.assertNotIn("mismatch", summary["sp_slot_taps"])
        self.assertGreaterEqual(summary["sp_entry"].get("canonical", 0), 6)
        self.assertEqual(summary["live_judge_pct"], 100.0)
        self.assertGreaterEqual(summary["live_card_commit_pct"], 80.0)
        self.assertEqual(summary["live_phase"].get("wrong", 0), 0)


@unittest.skipUnless((LIVE / "live-now.png").is_file() and LIVE_OCR.is_file(), "private live-now frame not present")
class LiveNowFrameTests(unittest.TestCase):
    """seg19: TURN 1, hand Vo / Excellent / Vo, trend Vi★2倍 > Vo★1.5倍 > Da. Before the fix the
    Excellent card was unread (no OCR label), so live_phase stayed None for 81 polls -> live_phase_unread."""

    @classmethod
    def setUpClass(cls):
        from agent.core import wing_screen as wsc
        from tools import wing_demo_replay as replay
        frame = json.loads(LIVE_OCR.read_text(encoding="utf-8"))["frames"]["live-now.png"]
        cls.texts = [(t, tuple(b)) for t, _, b in frame]
        cls.image = replay.load_rgb(LIVE / "live-now.png")
        cls.r = wsc.read_live(cls.texts, cls.image)

    def test_reads_card_select_phase(self):
        r = self.r
        self.assertEqual((r["auto"], r["speed"], r["turn"], r["memory_gauge"]), ("OFF", 3, 1, 20))
        self.assertEqual((r["live_phase"], r["selected_card"]), ("select_card", None))
        self.assertEqual(r["live_state"]["options"], [{"id": "card1", "attribute": "Vo"},
                                                      {"id": "card2", "attribute": "All"},
                                                      {"id": "card3", "attribute": "Vo"}])
        self.assertEqual(r["trend"], {"Vo": 2, "Da": 3, "Vi": 1})
        self.assertTrue(all(r["hand_sigs"]))
        self.assertEqual((r["live_state"]["our_stars"], r["live_state"]["rival_stars"]), (0, [0, 0]))

    def test_navigator_inspects_the_excellent_card_inside_its_button(self):
        from agent.core import produce_config as pc
        from agent.core import wing_nav as nav
        config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        rt = nav.WingNavRuntime(nav.RunMemory.from_produce_config(config))
        rt.begin()
        decision = rt.decide("Live", self.r)
        self.assertEqual((decision.action, decision.intent, decision.reason),
                         ("click", "live_card_2", "live_inspect_unknown_card"))
        self.assertEqual(rt.memory.live_step["target"], "Vi")
        (x, y, w, h), verified = nav.TARGETS["live_card_2"]
        self.assertTrue(verified)
        self.assertTrue(x <= 667 and 750 <= x + w and y <= 615 and 705 <= y + h)     # the Excellent icon circle
        (jx, jy, jw, jh), verified = nav.TARGETS["live_judge_3"]                    # Vi box x15–140, y430–575
        self.assertTrue(verified and jx <= 15 + 23 and 140 - 23 <= jx + jw and jy <= 430 + 29 and 575 - 29 <= jy + jh)


if __name__ == "__main__":
    unittest.main()


class LinkTooltipTest(unittest.TestCase):
    def test_link_card_tooltip_box_moves_left(self):
        # 2026-10-14 live: a LINK card shows its skill box at x 310–636 and 「リンクアピール」 at x 642–968.
        from agent.core import wing_live_policy as wlp
        texts = [("LINK+", [304, 557, 90, 39]), ("黒百合前で待ち合わせ", [325, 492, 184, 22]),
                 ("Vocal3倍アピ-ル/Vocal&Visual25%", [328, 517, 298, 23]), ("UP[3タ-ン]", [328, 533, 105, 22]),
                 ("Vo.", [416, 426, 38, 20]), ("323", [472, 424, 40, 22]), ("リンクアピール", [658, 489, 130, 23]),
                 ("Vocal&Visual40%UP[5夕-ン]", [661, 516, 249, 18])]
        tip = wlp.parse_tooltip(texts)
        self.assertEqual((tip["name"], tip["multiplier"], tip["attribute"]), ("黒百合前で待ち合わせ", 3.0, "Vo"))
