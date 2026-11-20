"""Supervised ``Wing`` launcher entry (tools/run_tasks.py, 2026-10-09): config validation, allowlist, overrides, gate.

Offline only; no host, no game input. CardScan / other supervised entries are covered by test_supervised_tasks.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.core import pipeline_actions as pa
from agent.core import wing_nav as nav
from tools import run_tasks as launcher

ROOT = Path(__file__).resolve().parent.parent


def config(option=None, cap=200, spend=()):
    return {"preset": None, "tasks": [{"name": "Wing", "option": option or {}}], "max_clicks": cap,
            "task_max_clicks": {"Wing": cap}, "session_max_minutes": 60, "task_timeout_s": 3600,
            "allow_spend": list(spend)}


def scoped(value):
    return launcher.supervised_task_config(value, live=True, offline=False, simulate_input=False)


class WingConfigTests(unittest.TestCase):
    def test_defaults_attach_and_caps(self):
        out = scoped(config())
        self.assertEqual(set(out["families"]), launcher.SUPERVISED_TASK_FAMILIES["Wing"])
        self.assertEqual(scoped(config(cap=launcher.WING_TASK_CAP + 1))["max_clicks"], launcher.WING_TASK_CAP + 1)  # optional
        with self.assertRaises(ValueError):
            scoped({**config(), "preset": "daily"})
        with self.assertRaises(ValueError):
            scoped(config({"unknown": 1}))
        with self.assertRaises(ValueError):
            launcher.supervised_task_config(config(), live=False, offline=True, simulate_input=True)

    def test_spend_only_in_start_mode_with_confirmed_team(self):
        with self.assertRaises(ValueError):
            scoped(config(spend=["wing-produce-start"]))                                   # attach + spend
        # 2026-10-09: start mode verifies the team by avatar fingerprint (team_confirmed only lets an uncertain one on).
        self.assertEqual(scoped(config({"mode": "start"}, spend=["wing-produce-start"]))["allow_spend"],
                         ["wing-produce-start"])
        with self.assertRaises(ValueError):
            scoped(config({"mode": "start", "team_confirmed": True}, spend=["training-start"]))
        out = scoped(config({"mode": "start", "team_confirmed": True}, spend=["wing-produce-start"]))
        self.assertEqual(out["allow_spend"], ["wing-produce-start"])

    def test_plan_parameters(self):
        plan = launcher.supervised_only_plan({"name": "Wing", "option": {"live_mode": "game_auto", "sp_done": True}},
                                             config(cap=120))
        self.assertEqual((plan.name, plan.entry), ("Wing", "SC.Wing.Nav.Start"))
        params = plan.pipeline_override["SC.Wing.Nav.Start"]["custom_action_param"]
        self.assertEqual(params["run"], {"sp_done": True, "max_acts": 120, "segment_acts": 90})
        self.assertEqual(params["produce_config"]["manual"]["live_mode"], "game_auto")
        self.assertNotIn("input_enabled", params["run"])
        enabled = {n for n, v in plan.pipeline_override.items() if v.get("enabled")}
        self.assertIn("SC.Wing.Nav.Loop", enabled)
        self.assertIn("SC.Wing.On.Hub", enabled)
        self.assertFalse(any(n.startswith(("SC.Wing.Click.", "SC.Wing.Screen.", "SC.Wing.Anchor.", "SC.Wing.Pick."))
                             for n in enabled))
        with self.assertRaises(ValueError):
            launcher.wing_options({"memory_file": "C:/elsewhere/memory.json"})

    def test_allowlist_and_click_nodes(self):
        self.assertNotIn("wing-produce-start", launcher.wing_task_actions())
        self.assertIn("wing-produce-start", launcher.wing_task_actions(("wing-produce-start",)))
        # Repository table (2026-10-11 review P1-5): only live-verified wing-* actions are enabled, so only their
        # SC.Wing.Click.* nodes can be enabled, never the start without the spend flag nor an unverified target.
        actions = launcher.wing_task_actions()
        live = launcher.mode_override(launcher.LIVE_INPUT, sorted(launcher.SUPERVISED_TASK_FAMILIES["Wing"]), (),
                                      actions, ("SC.Wing.Click.",))
        self.assertTrue(all(n.startswith("SC.Wing.Click.") for n in live))
        self.assertNotIn("SC.Wing.Click.StartConfirm.start_confirm_ok", live)
        self.assertFalse([n for n in live if not nav.TARGETS[n.rsplit(".", 1)[1]][1]])
        # Enabled copy (the live plan's enable step): only SC.Wing.Click.* of the allowed ids; ordinary runs none.
        from tests.test_wing_live_wiring import enabled_table
        with patch.object(pa, "_DEFAULT", enabled_table()):
            live = launcher.mode_override(launcher.LIVE_INPUT, sorted(launcher.SUPERVISED_TASK_FAMILIES["Wing"]), (),
                                          launcher.wing_task_actions(), ("SC.Wing.Click.",))
            self.assertTrue(live and all(n.startswith("SC.Wing.Click.") for n in live))
            self.assertNotIn("SC.Wing.Click.StartConfirm.start_confirm_ok", live)
            ordinary = launcher.mode_override(launcher.LIVE_INPUT, sorted(pa.default_table().families), ())
            self.assertFalse(any(n.startswith("SC.Wing.") for n in ordinary))


class WingGateTests(unittest.TestCase):
    def gate(self, spend=()):
        from agent.core import webview_controller as wc
        from tests.test_task_launcher import RISK_POLICY
        from tests.test_wing_live_wiring import enabled_table

        audit = Path(tempfile.mkdtemp(dir=launcher.PRIVATE, prefix="wing-gate-"))
        self.addCleanup(__import__("shutil").rmtree, audit, True)
        table = enabled_table()
        spend_gate = wc.SpendGate(spend, max_per_action=1) if spend else wc.SpendGate()
        return wc.WebViewInputGate(object(), wc.RunConfig(True, launcher.SUPERVISED_TASK_FAMILIES["Wing"]), {},
                                   spend_gate, wc.AuditLog(audit), policy=RISK_POLICY, action_table=table)

    def resolve(self, gate, action, node):
        point = gate.table.centre(action)
        with patch.object(gate.frame_permits, "refusal", return_value=None):
            return gate._resolve_pipeline_click(point, SimpleNamespace(node_name=node, action=action))

    def test_consuming_start_in_allowlist_only_when_spend_allowed(self):
        with self.assertRaises(ValueError):
            self.gate().restrict_actions({"wing-produce-start"}, ("SC.Wing.Click.",))
        gate = self.gate(("wing-produce-start",))
        gate.restrict_actions(launcher.wing_task_actions(("wing-produce-start",)), ("SC.Wing.Click.",))
        node = "SC.Wing.Click.StartConfirm.start_confirm_ok"
        self.assertEqual(self.resolve(gate, "wing-produce-start", node)[2], "spend_not_authorized")   # still a grant
        self.assertEqual(self.resolve(gate, "wing-hub-schedule", "SC.Wing.Click.Hub.hub_schedule"),
                         ("wing-hub-schedule", None, None))
        self.assertEqual(self.resolve(gate, "wing-commu-skip", "SC.Wing.Click.CommuSkip.commu_skip")[2], None)


class LauncherCliTests(unittest.TestCase):
    def test_flag_maps_to_the_start_action(self):
        self.assertEqual(launcher.SPEND_FLAGS["allow_produce_spend"], "wing-produce-start")
        self.assertIn("wing-produce-start", launcher.SPEND_ACTIONS_ALLOWED)
        self.assertNotIn("Wing", launcher.SUPERVISED_TASK_SPEND)        # checked by wing_options instead
        source = (ROOT / "tools/run_tasks.py").read_text(encoding="utf-8")
        self.assertIn('"--allow-produce-spend"', source)


if __name__ == "__main__":
    unittest.main()
