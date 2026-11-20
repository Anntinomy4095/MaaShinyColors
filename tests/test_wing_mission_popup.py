"""W.I.N.G. home ミッション達成 popup (live seg15, 2026-10-10): the daily login popup over a home whose button still reads
「プロデュース中」 is not a Produce end -> tap OK once (never ミッションへ) and continue.

Native frame (private): dev_assets/private/wing-live-20261009/startup7.png; OCR boxes below are read off that frame.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from agent.core import wing_nav as nav

ROOT = Path(__file__).resolve().parent.parent
FRAME = ROOT / "dev_assets/private/wing-live-20261009/startup7.png"
OK_BUTTON = (455, 543, 163, 75)         # measured on startup7.png: x455-618 y543-618
GOTO_BUTTON = (662, 543, 164, 75)       # ミッションへ x662-826


def runtime(mode="attach", **options):
    opts = {"mode": mode, "input_enabled": True, "from_home": True, **options}
    return nav.WingNavRuntime(nav.RunMemory(primary="Vo"), options=nav.RunOptions(**opts))


def inside(inner, outer, pad=0):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return x >= ox + pad and y >= oy + pad and x + w <= ox + ow - pad and y + h <= oy + oh - pad


class DecisionTests(unittest.TestCase):
    def test_target_inside_ok_and_clear_of_goto(self):
        rect = nav.TARGETS["mission_popup_ok"][0]
        self.assertTrue(inside(rect, OK_BUTTON, 8))
        self.assertTrue(rect[0] + rect[2] + 30 <= GOTO_BUTTON[0])
        self.assertEqual(nav.screen_intents("MissionPopup"), ("mission_popup_ok",))
        self.assertEqual(nav.action_id("mission_popup_ok"), "wing-mission-popup-ok")
        self.assertIn("Home", nav.RULES["MissionPopup"].expect)

    def test_in_progress_button_taps_ok_once(self):
        rt = runtime(from_home=False)
        d = rt.decide("MissionPopup", {"home_produce_resume_label": True})
        self.assertEqual((d.action, d.intent, d.reason), ("click", "mission_popup_ok", "mission_popup_login_ok"))
        self.assertEqual(rt.decide("MissionPopup", {"home_produce_resume_label": True}).reason,
                         "mission_popup_no_effect")

    def test_attach_from_home_without_end_taps_ok(self):
        self.assertEqual(runtime().decide("MissionPopup", {}).intent, "mission_popup_ok")

    def test_start_mode_popup_before_the_start_taps_ok(self):
        # H2d 2026-11-07: ミッション達成 over the home (「プロデュース」) before a start-mode run started its Produce
        d = runtime("start").decide("MissionPopup", {"home_produce_label": True})
        self.assertEqual((d.action, d.intent), ("click", "mission_popup_ok"))

    def test_finished_produce_still_finishes(self):
        self.assertEqual(runtime().decide("MissionPopup", {"home_produce_label": True}).action, "finish")
        self.assertEqual(runtime(from_home=False).decide("MissionPopup", {}).reason, "produce_finished_home")
        rt = runtime("start")
        rt.memory.start_attempted = True                                # this run's Produce has started
        self.assertEqual(rt.decide("MissionPopup", {"home_produce_label": True}).action, "finish")
        rt = runtime("start")
        rt.memory.produce = {"p_card": "x"}                             # a restored segment of a running Produce
        self.assertEqual(rt.decide("MissionPopup", {}).action, "finish")
        rt = runtime()
        rt.memory.true_end_seen = True
        self.assertEqual(rt.decide("MissionPopup", {}).action, "finish")
        rt = runtime()
        rt.decide("Evaluation", {"texts": [], "dialog": False})        # an end screen was seen this run
        self.assertEqual(rt.decide("MissionPopup", {}).action, "finish")


@unittest.skipUnless(FRAME.is_file(), "private startup frame not present")
class NativeFrameTests(unittest.TestCase):
    TEXTS = [("ミッション達成", (412, 95, 220, 35)), ("以下のミッションを達成しました。", (388, 155, 330, 24)),
             ("ゲームにログインしよう", (398, 258, 200, 24)), ("OK", (510, 565, 55, 35)),
             ("ミッションへ", (668, 565, 150, 35)), ("ガシャ", (430, 672, 70, 26)),
             ("プロデュース中", (1002, 640, 112, 28)), ("300/300", (1010, 672, 85, 20))]

    def read(self, texts):
        import numpy as np
        from PIL import Image
        from agent.core import wing_screen as wsc
        from tools import wing_nav_build as build
        nodes = wsc.load_nodes((wsc.PIPELINE_DIR / "wing_draft.json",))
        nodes.update(build.build())
        classifier = wsc.ScreenClassifier(nodes, wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})
        image = np.asarray(Image.open(FRAME).convert("RGB"))
        frame = wsc.Frame(texts, lambda: image, key="startup7.png")
        screen = classifier.classify(frame).screen
        return screen, wsc.read_screen(screen, classifier.evaluator, frame)

    def test_login_popup_over_produce_in_progress(self):
        screen, r = self.read(self.TEXTS)
        self.assertEqual(screen, "MissionPopup")
        self.assertTrue(r["home_produce_resume_label"])
        d = runtime().decide(screen, r)
        self.assertEqual((d.action, d.intent), ("click", "mission_popup_ok"))
        self.assertTrue(inside(d.target, OK_BUTTON, 8))
        self.assertFalse(inside(d.target, GOTO_BUTTON))

    def test_popup_after_produce_end_finishes(self):
        texts = [t for t in self.TEXTS if t[0] != "プロデュース中"] + [("プロデュース", (1010, 640, 95, 28))]
        screen, r = self.read(texts)
        self.assertEqual(screen, "MissionPopup")
        self.assertFalse(r["home_produce_resume_label"])
        self.assertEqual(runtime().decide(screen, r).action, "finish")


if __name__ == "__main__":
    unittest.main()
