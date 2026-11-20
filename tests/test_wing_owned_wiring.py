"""Saved owned-card snapshot -> Wing segments -> Begin/team rebuild. Synthetic data, no host or game input."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from agent.core import card_inventory as ci, wing_custom, wing_ledger, wing_team_profile
from tools import run_tasks, ui_engine, ui_schema, ui_wing, wing_segments
from test_wing_team_profile import fixture, P, SUPPORTS


def profile():
    return {"schema_version": 1, "tasks": [{"name": "AutoProduce", "enabled": True,
                                           "option": {"ProduceP卡": P}}]}


class OwnedSnapshotTests(unittest.TestCase):
    def test_clean_snapshot_drops_private_fields_invalid_numbers_and_unowned_details(self):
        source = {"p": [P, P, "2010010010", "１040010010"], "s": [SUPPORTS[0], P, 123, {}],
                  "details": {P: {"level": True, "limit_break": 0, "cookie": "private"},
                              SUPPORTS[0]: {"level": 60, "limit_break": 5, "image": "private"},
                              SUPPORTS[1]: {"level": 80, "limit_break": 4}},
                  "account": "private", "allow_spend": ["anything"]}
        self.assertEqual(ci.sanitize_owned_cards(source),
                         {"p": [P], "s": [SUPPORTS[0]], "details": {P: {"limit_break": 0},
                                                                       SUPPORTS[0]: {"level": 60}}})
        self.assertIsNone(ci.sanitize_owned_cards(None))
        self.assertEqual(ci.sanitize_owned_cards({"p": P, "s": {"card": SUPPORTS[0]}}), {"p": [], "s": []})

    def test_generic_ui_profile_does_not_accept_owned_or_prefs(self):
        schema = ui_schema.export_schema()
        for extra in ({"owned_cards": {"p": [P]}}, {"prefs": {"owned_cards": {"p": [P]}}}):
            with self.assertRaisesRegex(ValueError, "档案字段无效"):
                ui_schema._profile_to_run_config(schema, {**profile(), **extra})

    def test_ui_to_begin_and_attach_keep_same_snapshot_and_spend_guards(self):
        _, owned, _, _ = fixture()
        owned["details"][P] = {"limit_break": 2}  # A P card needs only LB; do not invent a support-card Lv.
        owned["details"][SUPPORTS[0]] = {"level": 60, "limit_break": 0}
        config, _ = ui_wing.wing_run_config(ui_schema.export_schema(), profile(), snapshot={},
                                           allow_produce_spend=True, ledger=wing_ledger.SpendLedger(None),
                                           owned_cards=owned)
        option = config["tasks"][0]["option"]
        params, options = run_tasks.wing_options(option, allow_spend=config["allow_spend"])
        self.assertEqual(params["owned_cards"], owned)
        self.assertEqual(params["owned_cards"]["details"][P], {"limit_break": 2})
        self.assertTrue(options.verify_resume)
        self.assertNotIn("owned_cards", params["run"])
        attached = wing_segments.attach_config(config)
        attached_option = attached["tasks"][0]["option"]
        attached_params, attach_options = run_tasks.wing_options(attached_option, allow_spend=attached["allow_spend"])
        self.assertEqual(attached_params["owned_cards"], params["owned_cards"])
        self.assertEqual(attached["allow_spend"], [])
        self.assertFalse(attach_options.allow_start_spend)
        attached_option["owned_cards"]["details"][SUPPORTS[0]]["level"] = 80
        self.assertEqual(option["owned_cards"]["details"][SUPPORTS[0]]["level"], 60)
        owned["details"][SUPPORTS[0]]["level"] = 100
        self.assertEqual(option["owned_cards"]["details"][SUPPORTS[0]]["level"], 60)

    def test_begin_uses_actual_lv_lb_and_team_rebuild_keeps_snapshot(self):
        team, owned, cards, id_map = fixture()
        owned["details"][SUPPORTS[0]] = {"level": 60, "limit_break": 0}
        data = copy.deepcopy(run_tasks.wing_options()[0]["produce_config"])
        data["team"].update(team)
        params, _ = run_tasks.wing_options({"produce_config": data, "owned_cards": owned})
        original_build = wing_team_profile.build_team_profile

        def build(current_team, current_owned, **kw):
            return original_build(current_team, current_owned, cards=cards, id_map=id_map, library={})

        with patch.object(wing_team_profile, "build_team_profile", side_effect=build) as builds:
            session = wing_custom.WingSession()
            self.assertTrue(session.begin(params))
            runtime = session.runtime
            scored = runtime.policy_params["score_team"].supports[SUPPORTS[0]]
            self.assertEqual(scored.stats["Vo"], 120)
            self.assertNotIn("lesson_Vo", scored.masteries)  # Lv60/LB0 cannot unlock the LB2 mastery.
            self.assertEqual(runtime.policy_params["score_team"].supports[SUPPORTS[1]].stats["Vo"], 260)
            # The newly observed unit swaps an active and guest; profile reconstruction must keep the run snapshot.
            slots = {"P": P, **{f"S{i + 1}": card for i, card in enumerate(SUPPORTS[:4])}, "guest": SUPPORTS[4]}
            slots["S1"], slots["guest"] = slots["guest"], slots["S1"]
            runtime._observe_supports("UnitCheck", {"unit_cards": {
                slot: {"card_id": card, "status": "accepted"} for slot, card in slots.items()}})
            self.assertEqual(builds.call_count, 2)
            self.assertIs(builds.call_args.args[1], runtime.owned_cards)
            self.assertEqual(runtime.profile_report["source"], "unit_check")
            self.assertEqual(runtime.policy_params["score_team"].supports[SUPPORTS[0]].stats["Vo"], 120)

    def test_direct_wing_option_sanitizes_and_does_not_authorize_spend(self):
        params, options = run_tasks.wing_options({"owned_cards": {"p": [P], "s": [], "allow_spend": True}})
        self.assertEqual(params["owned_cards"], {"p": [P], "s": []})
        self.assertFalse(options.allow_start_spend)
        for bad in ([], True, "private"):
            with self.assertRaisesRegex(ValueError, "owned_cards"):
                run_tasks.wing_options({"owned_cards": bad})
            with self.assertRaisesRegex(ValueError, "持有卡资料"):
                ui_wing.wing_run_config(ui_schema.export_schema(), profile(), snapshot={},
                                       allow_produce_spend=True, ledger=wing_ledger.SpendLedger(None), owned_cards=bad)

    def test_engine_segments_freeze_owned_and_forward_only_wing_data(self):
        _, owned, _, _ = fixture()
        calls = []
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets" / "private") as tmp:
            folder = Path(tmp)
            saved = folder / "profile.json"
            saved.write_text(json.dumps({**profile(), "owned_cards": owned}), encoding="utf-8")
            args = SimpleNamespace(wing=True, session="unused-synthetic-session", profile=saved,
                                   summary=folder / "summary.json", events=folder / "events.jsonl",
                                   stop_file=folder / "stop.flag", allow_produce_spend=True)

            def run(config, **kw):
                calls.append(copy.deepcopy(config))
                saved.write_text(json.dumps({**profile(), "owned_cards": {"p": [], "s": []}}), encoding="utf-8")
                task = {"name": "Wing", "status": "succeeded" if len(calls) == 2 else "failed",
                        "fail_reasons": [] if len(calls) == 2 else ["wing:segment_acts"],
                        "live_clicks": [{"result": "sent"}], "reports": [{"kind": "wing", "start_attempted": True,
                            "spend_ledger": {"key": "confirmed-test", "uncertain": False, "unresolved_before": []}}]}
                return {"tasks": [task], "failed": int(len(calls) == 1), "succeeded": int(len(calls) == 2)}

            with patch.object(ui_wing, "check_memory"), patch.object(ui_wing, "record_snapshot"), \
                    patch.object(ui_wing, "spend_pending", return_value=[]), \
                    patch.object(ui_engine, "publish_end_summary"), patch.object(ui_engine, "WING_PAUSE_S", 0):
                self.assertEqual(ui_engine.wing_main(args, run=run), 0)
            self.assertEqual(len(calls), 2)
            for config in calls:
                option = config["tasks"][0]["option"]
                self.assertEqual(run_tasks.wing_options(option, allow_spend=config["allow_spend"])[0]["owned_cards"], owned)
                self.assertNotIn("prefs", config)
            self.assertEqual(calls[1]["allow_spend"], [])
            self.assertEqual(calls[1]["tasks"][0]["option"]["mode"], "attach")


if __name__ == "__main__":
    unittest.main()
