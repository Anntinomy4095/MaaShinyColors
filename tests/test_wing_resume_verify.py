"""W.I.N.G. in-progress Produce check (「Produce」 page): with ``verify_resume`` an in-progress Produce is
continued only when its P idol (プロデュース再開 dialog / Hub bubble), P card and the configured S/Guest (read-only
ユニット確認, wing_unit_check) match the settings; a mismatch or anything unverifiable stops (never abandons, never
guesses). Offline: synthetic readings plus the native ユニット確認 frame when the private recording is present."""
from __future__ import annotations

import unittest
from pathlib import Path

from agent.core import wing_nav as nav

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "dev_assets/private/captures-20261011-164549Z"
LIVE = ROOT / "dev_assets/private/wing-live-20261009"
P_DIALOG = "1040020080"     # 【ひとりじゃない夜のこと】風野灯織, the card of the native プロデュース再開 sample
TEAM = {"P": "1040020030", "S": ["2040040090", "2040230040", "2040060080", "2040050080"], "guest": "2040070080"}


def runtime(mode="start", team=TEAM, **options):
    memory = nav.RunMemory(primary="Vo", p_idol="風野灯織", p_title="ひとりじゃない夜のこと",
                           team_slots={k: v for k, v in team.items() if v})
    rt = nav.WingNavRuntime(memory, options=nav.RunOptions(mode=mode, input_enabled=True, from_home=True,
                                                           verify_resume=True, **options))
    rt.begin()
    return rt


def cards(**over):
    ids = {"P": TEAM["P"], **{f"S{i + 1}": c for i, c in enumerate(TEAM["S"])}, "guest": TEAM["guest"], **over}
    return {slot: {"card_id": card, "status": "accepted"} for slot, card in ids.items()}


DIALOG = {"title": "ひとりじゃない夜のこと", "idol": "風野灯織", "scenario": "wing", "difficulty": "middle", "season": 2,
          "weeks_left": 3, "stats": None, "items": [], "ok_label": True, "cancel_label": True, "end_in_target": False,
          "end_label": True, "portrait": {"card_id": P_DIALOG, "status": "accepted"}}
DIALOG_TEAM = {**TEAM, "P": P_DIALOG}


class HubResumeTests(unittest.TestCase):
    def test_match_opens_unit_check_then_continues(self):
        rt = runtime()
        d = rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertEqual((d.action, d.intent, d.reason), ("click", "hub_unit_check", "verify_resume_team"))
        self.assertEqual(rt.memory.produce["source"], "attach")
        d = rt.decide("UnitCheck", {"unit_cards": cards(S1=TEAM["S"][2], S3=TEAM["S"][0])})   # S order is free
        self.assertEqual(d.intent, "page_return")
        self.assertIs(rt.memory.produce["team_verified"], True)
        self.assertEqual(rt.memory.team_observed["P"], TEAM["P"])
        d = rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertNotEqual(d.intent, "hub_unit_check")
        self.assertNotEqual(d.action, "stop")
        # the verified flag is part of the Produce identity, so it survives a segment boundary
        self.assertIs(rt.memory.snapshot()["produce"]["team_verified"], True)

    def test_team_mismatch_stops(self):
        for over in ({"guest": "2040170060"}, {"P": "1040020010"}, {"S2": "2010010010"}):
            with self.subTest(over=over):
                rt = runtime()
                rt.decide("Hub", {"hub_speaker": "灯織"})
                d = rt.decide("UnitCheck", {"unit_cards": cards(**over)})
                self.assertEqual((d.action, d.reason), ("stop", "resume_team_mismatch"))
                self.assertEqual(d.detail["resume_team"]["verdict"], "mismatch")
                self.assertIsNot(rt.memory.produce.get("team_verified"), True)

    def test_unverifiable_team_stops(self):
        rt = runtime()
        rt.decide("Hub", {"hub_speaker": "灯織"})
        d = rt.decide("UnitCheck", {"unit_cards": None})
        self.assertEqual((d.action, d.reason), ("stop", "resume_team_unverified"))
        rt = runtime()
        rt.decide("Hub", {"hub_speaker": "灯織"})
        rejected = cards()
        rejected["guest"] = {"card_id": None, "status": "verify_failed", "candidates": []}
        self.assertEqual(rt.decide("UnitCheck", {"unit_cards": rejected}).reason, "resume_team_unverified")
        # back on the Hub without a verdict (the page never showed): stop, never open it again
        rt = runtime()
        rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).reason, "resume_team_unverified")
        # 準決勝/決勝 Hub: the ユニット確認 target is not sampled there -> cannot verify -> stop
        rt = runtime()
        d = rt.decide("HubSemiFinal", {"hub_speaker": "灯織"})
        self.assertEqual((d.action, d.reason), ("stop", "resume_team_unverified"))
        self.assertEqual(d.detail["resume_team"]["why"], "unit_check_not_on_this_hub")

    def test_p_only_settings_check_the_p_card(self):
        rt = runtime(team={"P": TEAM["P"]})
        rt.decide("Hub", {"hub_speaker": "灯織"})
        d = rt.decide("UnitCheck", {"unit_cards": cards()})
        self.assertEqual(d.intent, "page_return")
        check = [e for e in rt.log if e.get("event") == "resume_team_check"][-1]
        self.assertEqual(check["checked"], ["P"])
        rt = runtime(team={"P": TEAM["P"]})
        rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertEqual(rt.decide("UnitCheck", {"unit_cards": cards(P="1040020010")}).reason, "resume_team_mismatch")

    def test_idol_mismatch_or_unread_stops_in_every_mode(self):
        for mode in ("start", "attach"):
            with self.subTest(mode=mode):
                self.assertEqual(runtime(mode).decide("Hub", {"hub_speaker": "夏葉"}).reason, "resume_config_mismatch")
                self.assertEqual(runtime(mode).decide("Hub", {}).reason, "resume_identity_unverified")
        # without verify_resume (CLI default) an attach run still continues on an unread bubble
        rt = nav.WingNavRuntime(nav.RunMemory(p_idol="風野灯織", team_slots=dict(TEAM)), options=nav.RunOptions())
        rt.begin()
        self.assertNotEqual(rt.decide("Hub", {}).reason, "resume_identity_unverified")

    def test_own_start_and_verified_segments_are_not_rechecked(self):
        rt = runtime()
        rt.memory.start_attempted = True
        self.assertNotEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")
        rt = runtime("attach")
        rt.memory.segment_resumed = True
        rt.memory.produce = {"p_card": TEAM["P"], "source": "start", "difficulty": "middle", "run_id": "r",
                             "verified_context": rt.resume_context()}
        self.assertNotEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")
        rt = runtime("attach")
        rt.memory.segment_resumed = True
        rt.memory.produce = {"p_card": TEAM["P"], "source": "attach", "difficulty": "unverified", "run_id": "a",
                             "team_verified": True, "verified_context": rt.resume_context()}
        self.assertNotEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")
        # an attach segment whose team was never confirmed is checked now
        rt = runtime("attach")
        rt.memory.segment_resumed = True
        rt.memory.produce = {"p_card": TEAM["P"], "source": "attach", "difficulty": "unverified", "run_id": "a"}
        self.assertEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")

    def test_legacy_confirmations_and_changed_supports_are_rechecked(self):
        for source in ("start", "attach"):
            with self.subTest(source=source):
                rt = runtime("attach")
                rt.memory.segment_resumed = True
                rt.memory.produce = {"p_card": TEAM["P"], "source": source, "difficulty": "middle",
                                     "run_id": "old", "team_verified": True}
                self.assertEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")
        rt = runtime("attach")
        rt.adopt_identity("start")
        rt.memory.segment_resumed = True
        rt.memory.team_slots = {**TEAM, "guest": "2040170060"}
        self.assertEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")
        self.assertEqual(rt.decide("UnitCheck", {"unit_cards": cards()}).reason, "resume_team_mismatch")

    def test_option_is_a_bool_param(self):
        self.assertIn("verify_resume", nav.RunOptions.PARAM_KEYS)
        self.assertTrue(nav.RunOptions.from_params({"mode": "attach", "verify_resume": True}).verify_resume)
        with self.assertRaises(ValueError):
            nav.RunOptions(verify_resume="yes")


class DialogResumeTests(unittest.TestCase):
    def test_dialog_match_then_team_check(self):
        rt = runtime(team=DIALOG_TEAM)
        d = rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG)})
        self.assertEqual((d.intent, d.detail["verdict"]), ("resume_ok", "match"))
        self.assertIs(rt.memory.resume_info["checks"]["portrait"], True)
        self.assertEqual(rt.decide("Hub", {"hub_speaker": "灯織"}).intent, "hub_unit_check")

    def test_halo_dialog_match_then_team_check_on_the_halo_hub(self):
        # H2d 2026-11-07: a resumed say "Halo" Produce opens the Halo Hub's ユニット確認 and compares the team
        rt = runtime(team=DIALOG_TEAM, halo_route="dig")
        d = rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "scenario": "halo", "difficulty": None}})
        self.assertEqual(d.intent, "resume_ok")
        d = rt.decide("HaloHub", {"season": 2, "hub_button": "produce"})
        self.assertEqual((d.action, d.intent, d.reason), ("click", "halo_unit_check", "verify_resume_team"))
        self.assertIn("UnitCheck", rt.expect_screens(nav.RULES["HaloHub"].expect))
        d = rt.decide("UnitCheck", {"unit_cards": cards(P=P_DIALOG)})
        self.assertEqual(d.intent, "page_return")
        self.assertIn("HaloHub", nav.RULES["UnitCheck"].expect)
        self.assertNotEqual(rt.decide("HaloHub", {"season": 2, "hub_button": "produce"}).intent, "halo_unit_check")
        rt = runtime(team=DIALOG_TEAM, halo_route="dig")
        rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "scenario": "halo", "difficulty": None}})
        rt.decide("HaloHub", {"season": 2, "hub_button": "produce"})
        self.assertEqual(rt.decide("UnitCheck", {"unit_cards": cards(P=P_DIALOG, guest="2040170060")}).reason,
                         "resume_team_mismatch")
        rt = runtime(team=DIALOG_TEAM, halo_route="dig")
        rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "scenario": "halo", "difficulty": None}})
        rt.decide("HaloHub", {"season": 2, "hub_button": "produce"})
        self.assertEqual(rt.decide("HaloHub", {"season": 2, "hub_button": "produce"}).reason,
                         "resume_team_unverified")                          # the page did not read as ユニット確認

    def test_dialog_portrait_must_be_the_p_card(self):
        # same idol, other P card (title text could be misread): the portrait decides
        rt = runtime(team={**TEAM, "P": "1040020030"})
        d = rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG)})
        self.assertEqual((d.action, d.reason), ("stop", "resume_config_mismatch"))
        self.assertIs(d.detail["resume"]["checks"]["portrait"], False)
        # unread / low-confidence portrait: cannot verify -> stop
        for portrait in (None, {"card_id": None, "status": "rejected", "reason": "low_score", "candidates": []},
                         {"card_id": None, "status": "rejected", "candidates": [[P_DIALOG, 0.70, 0.40]]},
                         {"card_id": P_DIALOG, "status": "accepted", "blank": True}):
            with self.subTest(portrait=portrait):
                rt = runtime(team=DIALOG_TEAM)
                d = rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "portrait": portrait}})
                self.assertEqual((d.action, d.reason), ("stop", "resume_identity_unverified"))
        # a rejected tile whose top candidate passes the strict targeted check is the configured card
        rt = runtime(team=DIALOG_TEAM)
        near = {"card_id": None, "status": "rejected", "candidates": [[P_DIALOG, 0.95, 0.85], ["1040020030", 0.80, 0.60]]}
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "portrait": near}}).intent, "resume_ok")
        # without verify_resume (CLI) the portrait is not required
        rt = nav.WingNavRuntime(nav.RunMemory(p_idol="風野灯織", p_title="ひとりじゃない夜のこと"),
                                options=nav.RunOptions(mode="attach", input_enabled=True, from_home=True))
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "portrait": None}}).intent, "resume_ok")

    def test_dialog_difficulty_follows_the_settings(self):
        rt = runtime(team=DIALOG_TEAM)
        rt.memory.difficulty = "master"
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG)}).reason, "resume_config_mismatch")
        rt = runtime(team=DIALOG_TEAM)
        rt.memory.difficulty = "master"
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": {**DIALOG, "difficulty": "master"}}).intent,
                         "resume_ok")

    def test_dialog_unverified_stops_in_attach_too(self):
        rt = runtime("attach", team=DIALOG_TEAM)
        rt.memory.p_idol = rt.memory.p_title = None
        self.assertEqual(rt.decide("ResumeDialog", {"resume_dialog": dict(DIALOG)}).reason, "resume_identity_unverified")


@unittest.skipUnless((REC / "frame-000239.png").is_file(), "private recording 2026-10-11")
class NativeUnitCheckTests(unittest.TestCase):
    """Native ユニット確認 (#239, 風野灯織 P): the configured team passes; another guest stops."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        from PIL import Image
        from agent.core import wing_unit_check as wuc
        cls.cards = wuc.read_unit_cards(np.asarray(Image.open(REC / "frame-000239.png").convert("RGB")))
        if cls.cards is None:
            raise unittest.SkipTest("card index not available")

    def test_native_frame(self):
        rt = runtime()
        rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertEqual(rt.decide("UnitCheck", {"unit_cards": self.cards}).intent, "page_return")
        self.assertIs(rt.memory.produce["team_verified"], True)
        rt = runtime(team={**TEAM, "guest": "2040170060"})
        rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertIn(rt.decide("UnitCheck", {"unit_cards": self.cards}).reason,
                      ("resume_team_mismatch", "resume_team_unverified"))


@unittest.skipUnless((LIVE / "sample-produce-resume-dialog.png").is_file(), "private resume sample 2026-10-09")
class NativeResumePortraitTests(unittest.TestCase):
    """Native プロデュース再開 dialog: the P portrait reads as 1040020080; another P card of the same idol is a mismatch."""

    def test_native_portrait(self):
        import numpy as np
        from PIL import Image
        from agent.core import wing_unit_check as wuc
        image = np.asarray(Image.open(LIVE / "sample-produce-resume-dialog.png").convert("RGB"))
        read = wuc.read_unit_cards(image, layout="resume_dialog")
        if read is None:
            self.skipTest("card index not available")
        self.assertEqual((read["P"]["status"], read["P"]["card_id"]), ("accepted", P_DIALOG))
        self.assertEqual(wuc.slot_verdict(read["P"], P_DIALOG), "match")
        self.assertEqual(wuc.slot_verdict(read["P"], "1040020030"), "mismatch")
        # a frame without the dialog (home after restart) never yields an accepted portrait at that box
        home = np.asarray(Image.open(LIVE / "restart3.png").convert("RGB"))
        self.assertNotEqual(wuc.slot_verdict(wuc.read_unit_cards(home, layout="resume_dialog")["P"], P_DIALOG), "match")


if __name__ == "__main__":
    unittest.main()
