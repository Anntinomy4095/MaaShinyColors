"""W.I.N.G. プロデュース再開: home 「プロデュース中」 -> resume dialog -> config check -> 再開 / stop.

Native frames (private): dev_assets/private/wing-live-20261009/{restart3,after-restart,sample-produce-resume-dialog}.png
with their whole-frame Maa OCR boxes (resume-frames-ocr.json, same format as the demo OCR caches).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core import wing_nav as nav
from agent.core import wing_resume as wr

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "dev_assets/private/wing-live-20261009"
OCR = LIVE / "resume-frames-ocr.json"
DIALOG, HOME, HOME2 = "sample-produce-resume-dialog.png", "restart3.png", "after-restart.png"


def runtime(mode="attach", p_idol="風野灯織", p_title="ひとりじゃない夜のこと", **options):
    opts = {"mode": mode, "input_enabled": True, "from_home": True, **options}
    memory = nav.RunMemory(primary="Vo", p_idol=p_idol, p_title=p_title)
    return nav.WingNavRuntime(memory, options=nav.RunOptions(**opts))


DIALOG_READ = {"title": "ひとりじゃない夜のこと", "idol": "風野灯織", "scenario": "wing", "difficulty": "middle",
               "season": 1, "weeks_left": 4, "stats": {"Vo": 245, "Da": 128, "Vi": 108, "Me": 176, "SP": 138,
                                                       "fans": 385},
               "items": [], "ok_label": True, "cancel_label": True, "end_in_target": False, "end_label": True}


class TargetTests(unittest.TestCase):
    def test_targets_bound_and_end_never_a_target(self):
        for intent in ("home_produce_resume", "resume_ok", "resume_cancel"):
            self.assertEqual(nav.TARGETS[intent][0], wr.RECTS[intent])
            self.assertTrue(nav.TARGETS[intent][1])
        self.assertIn("home_produce_resume", nav.screen_intents("Home"))
        self.assertEqual(nav.screen_intents("ResumeDialog"), ("resume_ok", "resume_cancel"))
        self.assertFalse(any("end" in i or "finish" in i for i in nav.screen_intents("ResumeDialog")))
        ex, ey, ew, eh = wr.END_RECT
        for intent in ("resume_ok", "resume_cancel"):
            x, y, w, h = wr.RECTS[intent]
            self.assertTrue(x + w + 12 <= ex or ex + ew + 12 <= x, intent)   # >= 12 px clear of 終了
        self.assertEqual(nav.action_id("resume_ok"), "wing-resume-ok")
        self.assertEqual(nav.action_id("home_produce_resume"), "wing-home-produce-resume")

    def test_catalog_entries(self):
        from tools import wing_nav_build as build
        catalog = {a["id"]: a for a in build.action_catalog()}
        for aid in ("wing-home-produce-resume", "wing-resume-ok", "wing-resume-cancel"):
            self.assertIn(aid, catalog)
            self.assertFalse(catalog[aid]["consuming"])
            self.assertFalse(catalog[aid]["enabled"])
        self.assertEqual(catalog["wing-resume-ok"]["nodes"], ["SC.Wing.Click.ResumeDialog.resume_ok"])
        self.assertEqual(catalog["wing-home-produce-resume"]["nodes"], ["SC.Wing.Click.Home.home_produce_resume"])


class DecisionTests(unittest.TestCase):
    def test_home_resume_only_with_from_home_and_once(self):
        rt = runtime()
        d = rt.decide("Home", {"home_produce_resume_label": True})
        self.assertEqual((d.intent, d.reason), ("home_produce_resume", "home_to_produce_resume"))
        self.assertEqual(rt.decide("Home", {"home_produce_resume_label": True}).reason, "home_resume_no_effect")
        off = runtime(from_home=False)
        d = off.decide("Home", {"home_produce_resume_label": True})
        self.assertEqual((d.action, d.reason), ("stop", "outside_produce"))
        # attach + from_home without a suspended Produce: nothing to resume
        self.assertEqual(runtime().decide("Home", {"home_produce_label": True}).reason, "no_produce_in_progress")
        # start + from_home with a suspended Produce resumes (and compares) instead of starting a second one
        self.assertEqual(runtime("start").decide("Home", {"home_produce_resume_label": True,
                                                          "home_produce_label": False}).intent,
                         "home_produce_resume")

    def test_match_taps_resume_once_and_hub_gate_is_satisfied(self):
        rt = runtime()
        d = rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG_READ)})
        self.assertEqual((d.intent, d.detail["verdict"]), ("resume_ok", "match"))
        self.assertEqual(rt.memory.resume_info["season"], 1)
        self.assertEqual(rt.memory.last_stats, {"Vo": 245, "Da": 128, "Vi": 108, "Me": 176})
        self.assertEqual(rt.memory.resume_info["goal_check"], "not_shown")
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG_READ)}).reason, "resume_no_effect")
        self.assertIsNone(rt._resume_gate("Hub", {"hub_speaker": "灯織"}))

    def test_mismatch_stops_with_choices_unless_user_continues(self):
        for field, value in (("idol", "白瀬咲耶"), ("difficulty", "master"), ("scenario", "other:G.R.A.D.編"),
                             ("title", "まったく別のカード名です")):
            with self.subTest(field=field):
                rt = runtime()
                d = rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG_READ, field: value}})
                self.assertEqual((d.action, d.reason), ("stop", "resume_config_mismatch"))
                self.assertEqual(len(d.detail["resume"]["choices"]), 2)
        rt = runtime(resume_mismatch_ok=True)
        d = rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG_READ, "idol": "白瀬咲耶"}})
        self.assertEqual(d.intent, "resume_ok")
        self.assertEqual(rt.memory.resume_info["user_choice"], "continue")

    def test_unverified_and_unreadable(self):
        # attach without a configured P card: continue with the warning; start mode stops
        rt = runtime(p_idol=None, p_title=None)
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG_READ)}).intent, "resume_ok")
        self.assertEqual(rt.memory.resume_info["verdict"], "unverified")
        rt = runtime("start", p_idol=None, p_title=None)
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG_READ)}).reason,
                         "resume_identity_unverified")
        for broken in ({"ok_label": False}, {"end_label": False}, {"end_in_target": True}, {"idol": None},
                       {"scenario": None}):
            with self.subTest(broken=broken):
                d = runtime().decide("ResumeDialog", {"resume_dialog": {**DIALOG_READ, **broken}})
                self.assertEqual((d.action, d.reason), ("stop", "resume_dialog_unreadable"))

    def test_act_gate_accepts_verified_targets(self):
        rt = runtime()
        rt.begin()
        d = rt.decide("Home", {"home_produce_resume_label": True})
        self.assertEqual(rt.act("Home", d), (True, "sent"))
        d = rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG_READ)})
        self.assertEqual(rt.act("ResumeDialog", d), (True, "sent"))

    def test_options(self):
        nav.RunOptions(mode="attach", from_home=True)
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="attach", unit_search=True)


@unittest.skipUnless(OCR.is_file() and (LIVE / DIALOG).is_file(), "private resume frames not present")
class NativeFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agent.core import wing_screen as wsc
        from tools import wing_nav_build as build
        nodes = wsc.load_nodes((wsc.PIPELINE_DIR / "wing_draft.json",))
        nodes.update(build.build())
        cls.classifier = wsc.ScreenClassifier(nodes, wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})
        cls.ocr = json.loads(OCR.read_text(encoding="utf-8"))["frames"]

    def read(self, name):
        import numpy as np
        from PIL import Image
        from agent.core import wing_screen as wsc
        image = np.asarray(Image.open(LIVE / name).convert("RGB"))
        texts = [(t, tuple(b)) for t, s, b in self.ocr[name] if s >= 0.3]
        frame = wsc.Frame(texts, lambda: image, key=name)
        screen = self.classifier.classify(frame).screen
        return screen, wsc.read_screen(screen, self.classifier.evaluator, frame)

    def test_home_with_suspended_produce(self):
        for name in (HOME, HOME2):
            with self.subTest(name=name):
                screen, r = self.read(name)
                self.assertEqual(screen, "Home")
                self.assertTrue(r["home_produce_resume_label"])
                self.assertFalse(r["home_produce_label"])
                self.assertEqual(runtime().decide(screen, r).intent, "home_produce_resume")

    def test_home_label_with_a_hyphen_for_the_long_vowel(self):
        # live 2026-10-29: the home bar read 「プロデュ-ス中」 and the run stopped on home_label_unread
        from agent.core import wing_screen as wsc
        bar = [("プロデュ-ス中", (998, 641, 119, 28)), ("279/300", (1002, 672, 90, 20))]
        r = wsc.read_batch_c("Home", lambda name: bar if name == "SC.Wing.Read.Home.Bar" else [], wsc.Frame(bar))
        self.assertEqual((r["home_produce_resume_label"], r["home_produce_label"]), (True, False))
        plain = [("プロデュ-ス", (998, 641, 100, 28))]
        r = wsc.read_batch_c("Home", lambda name: plain if name == "SC.Wing.Read.Home.Bar" else [], wsc.Frame(plain))
        self.assertEqual((r["home_produce_resume_label"], r["home_produce_label"]), (False, True))

    def test_resume_dialog_reading_and_decision(self):
        screen, r = self.read(DIALOG)
        self.assertEqual(screen, "ResumeDialog")
        d = r["resume_dialog"]
        self.assertEqual({k: d[k] for k in DIALOG_READ}, DIALOG_READ)
        decision = runtime().decide(screen, r)
        self.assertEqual((decision.intent, decision.detail["verdict"]), ("resume_ok", "match"))
        other = runtime(p_idol="白瀬咲耶", p_title="ふらりと咲いた")
        self.assertEqual(other.decide(screen, r).reason, "resume_config_mismatch")
