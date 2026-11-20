"""One-shot task authority, passive readings and the actual launcher path (no live host)."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch

from agent.core.run_control import RunControl
from tools import run_tasks as launcher


def config(name="MissionStatus"):
    return {"preset": None, "tasks": [name], "max_clicks": 4, "task_max_clicks": {name: 4}}


def scoped(value):
    return launcher.supervised_task_config(value, live=True, offline=False, simulate_input=False)


class SupervisedTaskConfigTests(unittest.TestCase):
    def test_mission_claim_uses_one_shot_non_spending_scope_without_a_click_cap(self):
        # 2026-10-27 live: a cap of 10 refused the final back tap; since 2026-10-28 no click caps at all
        value = {"preset": None, "tasks": ["MissionsClaim"]}
        self.assertEqual(set(scoped(value)["families"]), {"mission-claim", "mission", "nav", "popup"})
        self.assertNotIn("MissionsClaim", launcher.SUPERVISED_TASK_SPEND)
        self.assertNotIn("max_clicks", scoped(value))

    def test_each_task_gets_only_its_own_families_without_mutating_config(self):
        for name, families in launcher.SUPERVISED_TASK_FAMILIES.items():
            if name == "KnowhowScan":       # own option-derived cap and table switch: tests/test_knowhow_calibrate.py
                continue
            with self.subTest(name=name):
                raw = config(name)
                before = copy.deepcopy(raw)
                actual = scoped(raw)
                self.assertEqual(raw, before)
                self.assertEqual(set(actual["families"]), families)

    def test_only_live_non_simulated_mode_is_allowed(self):
        for flags in ({"live": False}, {"offline": True}, {"simulate_input": True}):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                launcher.supervised_task_config(config(), **({"live": True, "offline": False,
                                                               "simulate_input": False} | flags))

    def test_omitted_or_daily_preset_is_rejected_before_inheriting_options(self):
        for value in ({k: v for k, v in config("MatchSkip").items() if k != "preset"},
                      {**config("MatchSkip"), "preset": "daily"}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "preset"):
                scoped(value)

    def test_exactly_one_supported_enabled_task_is_required(self):
        for tasks in ([], ["Shop", "Training"], ["Startup"], ["AutoProduce"],
                      [{"name": "Shop", "enabled": False}], [{"name": "Shop", "enabled": "true"}]):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                scoped({**config(), "tasks": tasks})

    def test_click_caps_are_optional_and_only_for_this_task(self):
        # No click caps; an explicit one (developer CLI) must still be a non-negative int of this task.
        for key in ("max_clicks", "task_max_clicks"):
            scoped({k: v for k, v in config().items() if k != key})
        values = [{**config(), "max_clicks": value} for value in (-1, True, "4")]
        values += [{**config(), "task_max_clicks": value} for value in
                   (-1, {"Shop": 4}, {"MissionStatus": 4, "Shop": 1}, {"MissionStatus": -1})]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                scoped(value)
        self.assertEqual(scoped({**config(), "task_max_clicks": 4})["task_max_clicks"], 4)

    def test_cross_task_empty_or_duplicate_families_are_rejected(self):
        for families in ([], ["startup"], ["mission", "shop-money"], ["nav", "nav"]):
            with self.subTest(families=families), self.assertRaises(ValueError):
                scoped({**config(), "families": families})

    def test_only_the_selected_tasks_consuming_action_can_be_authorized(self):
        for name in launcher.SUPERVISED_TASK_FAMILIES:
            own = launcher.SUPERVISED_TASK_SPEND.get(name)
            if own:
                self.assertEqual(scoped({**config(name), "allow_spend": [own]})["allow_spend"], [own])
            other = next(action for action in launcher.SPEND_ACTIONS_ALLOWED if action != own)
            with self.subTest(name=name), self.assertRaises(ValueError):
                scoped({**config(name), "allow_spend": [other]})

    def test_supervision_cannot_be_persisted_in_run_config(self):
        with self.assertRaises(ValueError):
            scoped({**config(), "supervised_task": True})

    def test_supervised_task_keeps_the_cut_in_skip_and_original_task_options(self):
        override = launcher.supervised_startup_override()
        self.assertNotIn("SC.Popup.NewGacha.Close", override)          # SKIP the cut-in
        self.assertNotIn("SC.Popup.CloseKnown", override)
        interface = launcher.load_interface()
        value = scoped({**config("MatchSkip"), "tasks": [{"name": "MatchSkip", "option": {"Match只采样": "Yes"}}]})
        plan, = launcher.plan_tasks(interface, value)
        self.assertEqual(plan.name, "MatchSkip")

    def test_cli_passes_one_shot_flag_without_adding_it_to_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config()), encoding="utf-8")
            fake = {"order": [], "input_mode": "live_input", "tasks": [], "summary_path": None,
                    "fes_allocation": {"status": "idle"},
                    "run_control": {"stop_requested": False, "events_complete": True}, "failed": 0}
            with patch.object(launcher, "run", return_value=fake) as run, patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(launcher.main(["--config", str(path), "--session", "unused.json",
                                               "--live", "--supervised-task"]), 0)
            self.assertTrue(run.call_args.kwargs["supervised_task"])
            self.assertNotIn("supervised_task", run.call_args.args[0])


class SupervisedTaskLauncherTests(unittest.TestCase):
    def test_rejected_request_never_connects_a_controller(self):
        for flags in ({"supervised_startup": True}, {}):
            with self.subTest(flags=flags), patch.object(launcher, "build_tasker") as build:
                with self.assertRaises(ValueError):
                    launcher.run({"tasks": ["Shop"]}, live=True, supervised_task=True, **flags)
                build.assert_not_called()

    def gate(self, directory, families, cap=4):
        from agent.core import webview_controller as wc
        from tests import test_shop_task as s
        from tests.test_task_launcher import RISK_POLICY, TABLE, FakePipelineHost
        frames = (s.HOME, s.PREMIUM, s.LIST, s.HOME)
        if not all((launcher.ROOT / frame).is_file() for frame in frames):
            self.skipTest("requires private native Shop frames")
        host = FakePipelineHost([(frame, "click") for frame in frames])
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL,
            "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
            "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
            "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
            "spend_secret": "07" * 32, "audit_root": str(launcher.PRIVATE / "run_logs"),
            "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                       "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(families)),
                                  {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                  wc.AuditLog(directory), host.send, policy=RISK_POLICY,
                                  limits=wc.ClickLimits(run_max=cap, task_max=cap))
        return gate, host

    def test_one_shot_shop_sends_only_verified_non_consuming_actions_and_keeps_readings(self):
        from tests import test_shop_task as s
        from tests.test_task_launcher import RISK_POLICY, HAVE_MAA
        if not HAVE_MAA:
            self.skipTest("requires installed MaaFramework")
        with tempfile.TemporaryDirectory(dir=launcher.PRIVATE, prefix="supervised-task-") as directory:
            gate, host = self.gate(directory, launcher.SUPERVISED_TASK_FAMILIES["Shop"])
            value = {**config("Shop"), "tasks": [{"name": "Shop", "option": s.ONLY_SKIP}]}
            summary = launcher.run(value, live=True, supervised_task=True, gate=gate, policy=RISK_POLICY,
                                   summary_path=False, save_diagnostics=False,
                                   shop_options={"log_root": Path(directory), "now": lambda: s.NOW})
            self.assertTrue(summary["live"]["frame_guard_enabled"])
            self.assertEqual(summary["live"]["supervised_task"], "Shop")
            self.assertEqual([row["action"] for row in host.requests], ["home-shop", "shop-money-tab", "nav-back"])
            self.assertTrue(all("spend_grant" not in row for row in host.requests))
            self.assertGreaterEqual(summary["resource_observer"]["reads"], 1)
            self.assertTrue(any(row["kind"] == "resources" for row in summary["readings"]))
            self.assertEqual(summary["tasks"][0]["status"], "failed")  # spend_not_enabled is the intended stop

    def test_injected_gate_cannot_widen_the_task_families_or_click_budget(self):
        from tests.test_task_launcher import RISK_POLICY, HAVE_MAA
        if not HAVE_MAA:
            self.skipTest("requires installed MaaFramework")
        own = launcher.SUPERVISED_TASK_FAMILIES["Shop"]
        for families, cap in ((own | {"mission"}, 4), (own, 5)):
            with tempfile.TemporaryDirectory(dir=launcher.PRIVATE, prefix="supervised-gate-") as directory:
                gate, host = self.gate(directory, families, cap)
                with self.assertRaisesRegex(ValueError, "监督控制器"), patch.object(launcher, "build_tasker") as build:
                    launcher.run(config("Shop"), live=True, supervised_task=True, gate=gate, policy=RISK_POLICY,
                                 summary_path=False)
                build.assert_not_called()
                self.assertEqual(host.requests, [])


def sequence(*names, spend=()):
    return launcher.sequence_run_config({"tasks": [{"name": n, "enabled": True} for n in names],
                                         "allow_spend": list(spend)})


def seq_scoped(value):
    return launcher.supervised_sequence_config(value, live=True, offline=False, simulate_input=False)


class SupervisedSequenceTests(unittest.TestCase):
    """ANN-17: the GUI daily run = several supervised tasks in order, each with its own scope and spend action."""

    def test_ui_config_has_no_click_caps_and_only_own_spend(self):
        value = launcher.sequence_run_config({"tasks": [{"name": "Shop", "enabled": True},
                                                        {"name": "GradeFes", "enabled": False},
                                                        {"name": "Training", "enabled": True}],
                                              "allow_spend": ["shop-exchange-confirm", "grade-tension-confirm"]})
        self.assertEqual([t["name"] for t in value["tasks"]], ["Shop", "Training"])
        self.assertEqual(value["allow_spend"], ["shop-exchange-confirm"])     # Grade is not ticked
        self.assertNotIn("task_max_clicks", value)
        self.assertNotIn("max_clicks", value)
        scoped = seq_scoped(value)
        self.assertEqual(set(scoped["families"]), launcher.SUPERVISED_TASK_FAMILIES["Shop"]
                         | launcher.SUPERVISED_TASK_FAMILIES["Training"])

    def test_unsupported_or_wing_tasks_are_refused(self):
        for names in (["Shop", "Wing"], ["CardScan"], ["AutoProduce"], ["Diagnostics"], []):
            with self.subTest(names=names), self.assertRaises(ValueError):
                seq_scoped(sequence(*names))

    def test_budgets_spend_and_mode_are_bounded(self):
        good = sequence("Shop", "Training", spend=["shop-exchange-confirm", "training-start"])
        seq_scoped(good)
        seq_scoped({**good, "task_max_clicks": {"Shop": 21}, "max_clicks": 51})   # explicit caps stay optional
        bad = [{**good, "task_max_clicks": {"Match": 20}},
               {**good, "max_clicks": -1},
               {**good, "allow_spend": ["match-skip-confirm"]},
               {**good, "preset": "daily"},
               {k: v for k, v in good.items() if k != "preset"},
               {**good, "families": ["shop-money", "nav", "popup"]},
               {**good, "tasks": [{"name": "Shop"}, {"name": "Shop"}]}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                seq_scoped(value)
        for flags in ({"live": False}, {"offline": True}, {"simulate_input": True}):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                launcher.supervised_sequence_config(good, **({"live": True, "offline": False,
                                                               "simulate_input": False} | flags))

    def test_each_task_only_keeps_its_own_click_nodes_and_spend(self):
        from agent.core.pipeline_actions import default_table

        table = default_table()
        value = seq_scoped(sequence("Shop", "Training", spend=["shop-exchange-confirm", "training-start"]))
        families = frozenset(value["families"])
        nodes = launcher.click_node_actions()
        shop = launcher.sequence_task_override("Shop", families, value["allow_spend"])
        training = launcher.sequence_task_override("Training", families, value["allow_spend"])
        for node, action in nodes.items():
            if node.startswith(launcher.SUPERVISED_ONLY_NODE_PREFIXES):
                continue        # never enabled outside their own supervised entry
            family = table.get(action).family
            if family == "training-bronze":
                self.assertEqual(shop.get(node), {"enabled": False}, node)
                self.assertNotIn(node, training)
            if family == "shop-money":
                self.assertEqual(training.get(node), {"enabled": False}, node)
                self.assertNotIn(node, shop)
        self.assertNotIn("SC.Popup.NewGacha.Close", shop)  # normal daily keeps the guarded non-consuming preview SKIP
        self.assertNotIn("SC.Startup.Entry", shop)
        # a ticked task whose spend option is off keeps its consuming node disabled
        off = launcher.sequence_task_override("Training", families, ["shop-exchange-confirm"])
        spend_nodes = [n for n, a in nodes.items() if a == "training-start"]
        self.assertTrue(spend_nodes and all(off[n] == {"enabled": False} for n in spend_nodes))

    def test_spend_gate_holds_only_the_running_tasks_action(self):
        from agent.core import webview_controller as wc
        from tests.test_task_launcher import RISK_POLICY

        value = sequence("Shop", "Training", spend=["shop-exchange-confirm", "training-start"])
        seen = []

        class Gate:
            config = wc.RunConfig(True, frozenset(seq_scoped(value)["families"]))
            limits = wc.ClickLimits()
            spend_gate = wc.SpendGate(["shop-exchange-confirm", "training-start"], max_per_action=3)
            history, started, run_control, frame_binding, _frame, run_clicks = [], 0.0, None, None, None, 0
            clock = staticmethod(lambda: 1.0)
            frame_permits = mock.Mock()

            def configure_frame_guard(self, enabled):
                seen.append(("frame_guard", enabled))

            def begin_task(self, name, cap):
                seen.append(("task", name, cap))

            def stop_input(self):
                return True

            def input_idle(self):
                return True

        gate = Gate()

        def fake_execute(plans, tasker, **kw):
            for plan in plans:
                kw["before_task"](plan)
                seen.append(("spend", plan.name, sorted(gate.spend_gate.allowed)))
            return [{"name": p.name, "status": "succeeded", "fail_reasons": [], "reports": []} for p in plans]

        with patch.object(launcher, "build_tasker", return_value=mock.Mock()), \
                patch.object(launcher, "execute_plan", side_effect=fake_execute), \
                patch("agent.core.webview_controller.create_webview_controller"), \
                patch("agent.core.webview_controller.LiveSpendAuthority"):
            summary = launcher.run(value, live=True, supervised_sequence=True, gate=gate, policy=RISK_POLICY,
                                   summary_path=False, save_diagnostics=False)
        self.assertIn(("frame_guard", True), seen)
        self.assertIn(("task", "Shop", None), seen)    # no click cap
        self.assertIn(("spend", "Shop", ["shop-exchange-confirm"]), seen)
        self.assertIn(("spend", "Training", ["training-start"]), seen)
        self.assertEqual(summary["live"]["supervised_sequence"], ["Shop", "Training"])

    def test_cli_passes_the_sequence_flag_without_adding_it_to_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(sequence("Shop")), encoding="utf-8")
            fake = {"order": [], "input_mode": "live_input", "tasks": [], "summary_path": None,
                    "fes_allocation": {"status": "idle"},
                    "run_control": {"stop_requested": False, "events_complete": True}, "failed": 0}
            with patch.object(launcher, "run", return_value=fake) as run, patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(launcher.main(["--config", str(path), "--session", "unused.json",
                                               "--live", "--supervised-sequence"]), 0)
            self.assertTrue(run.call_args.kwargs["supervised_sequence"])
            self.assertNotIn("supervised_sequence", run.call_args.args[0])


CARD_SCAN_9A = {"preset": None, "tasks": [{"name": "CardScan", "option": {"tabs": ["support"], "max_scrolls_per_tab": 1}}],
                "max_clicks": 6, "task_max_clicks": {"CardScan": 6}, "allow_spend": []}
CARD_SCAN_9B = {"preset": None, "tasks": [{"name": "CardScan", "option": {"tabs": ["support", "produce"],
                                                                         "max_scrolls_per_tab": 30}}],
                "max_clicks": 66, "task_max_clicks": {"CardScan": 66}, "allow_spend": []}


class CardScanSupervisedConfigTests(unittest.TestCase):
    """CardScan exists only as a one-shot supervised task, admitted by exact action id."""

    def test_scan_options_are_validated_without_a_click_cap(self):
        self.assertIsNone(launcher.LIVE_TASK_CLICK_CAPS["CardScan"])
        for value in (CARD_SCAN_9A, CARD_SCAN_9B):
            actual = scoped(value)
            self.assertEqual(set(actual["families"]), {"training-bronze", "card-scan", "nav", "popup"})
            self.assertEqual(actual["allow_spend"], [])
        for over in ({**CARD_SCAN_9A, "allow_spend": ["training-start"]},
                     {**CARD_SCAN_9A, "tasks": [{"name": "CardScan", "option": {"max_scrolls_per_tab": 81}}]},
                     {**CARD_SCAN_9A, "tasks": [{"name": "CardScan", "option": {"tabs": ["fes"]}}]},
                     {**CARD_SCAN_9A, "tasks": [{"name": "CardScan", "option": {"bogus": 1}}]},
                     {**CARD_SCAN_9A, "tasks": [{"name": "CardScan", "option": "support"}]},
                     {**CARD_SCAN_9A, "families": ["training-bronze", "card-scan", "nav", "shop-money"]}):
            with self.subTest(value=over), self.assertRaises(ValueError):
                scoped(over)

    def test_full_library_options_do_not_change_the_defaults(self):
        full = copy.deepcopy(CARD_SCAN_9B)
        full["tasks"][0]["option"].update(max_scrolls_per_tab=80, max_frames=400, max_holds=10)
        scoped(full)
        self.assertEqual(launcher.card_scan_options().max_scrolls_per_tab, 30)
        with self.assertRaises(ValueError):
            scoped({**full, "task_max_clicks": {"Training": 166}})

    def test_plan_writes_only_validated_options_into_the_config_node(self):
        plan = launcher.supervised_only_plan(launcher.supervised_only_item(scoped(CARD_SCAN_9A)))
        self.assertEqual((plan.name, plan.entry), ("CardScan", "SC.CardScan.Entry"))
        self.assertEqual(plan.pipeline_override, {"SC.CardScan.Config": {"attach": {
            "tabs": ["support"], "max_scrolls_per_tab": 1, "max_frames": 150, "max_holds": 4, "required_order": None}}})
        with self.assertRaisesRegex(ValueError, "未知任务"):          # never plannable outside the supervised entry
            launcher.plan_tasks(launcher.load_interface(), CARD_SCAN_9A)

    def test_override_enables_exactly_the_allowlisted_card_scan_and_popup_nodes(self):
        from agent.core.pipeline_actions import default_table

        table = default_table()
        actions = launcher.SUPERVISED_TASK_ACTIONS["CardScan"]
        self.assertEqual(actions, {"home-idol", "idol-portal-list", "idol-list-tab-support", "idol-list-tab-produce",
                                   "idol-list-scroll-down", "idol-list-scroll-up", "nav-back", "popup-reward-ok",
                                   "popup-event-fan-reward-ok", "popup-event-fan-receipt-ok", "popup-fes-period-reward-ok",
                                   "popup-mission-completed-ok", "popup-help-close", "popup-pickup-close",
                                   "popup-story-skip", "popup-new-gacha-skip"})
        self.assertFalse(any(table.get(a).consuming for a in actions))
        override = launcher.mode_override(launcher.LIVE_INPUT, scoped(CARD_SCAN_9A)["families"], (), actions,
                                          launcher.SUPERVISED_TASK_NODE_PREFIXES["CardScan"])
        bound = launcher.click_node_actions()
        self.assertTrue(override)
        self.assertTrue(all(name.startswith(("SC.CardScan.", "SC.Popup.")) and bound[name] in actions
                            for name in override))
        self.assertNotIn("SC.Training.OpenIdol", override)             # home-idol only through the scan's own node
        self.assertEqual(override["SC.Popup.NewGacha.Close"], {"enabled": True})   # cut-in SKIP
        self.assertEqual({bound[n] for n in override if n.startswith("SC.CardScan.")},
                         {a for a in actions if not a.startswith("popup-")})
        with self.assertRaises(ValueError):
            launcher.mode_override(launcher.LIVE_INPUT, ["nav"], (), actions, None)


class CardScanGateAllowlistTests(unittest.TestCase):
    """The WebView2 gate refuses every other action id (training-bronze included) and every foreign node."""

    def make_gate(self, families):
        from agent.core import webview_controller as wc
        from tests.test_task_launcher import RISK_POLICY

        audit = Path(tempfile.mkdtemp(dir=launcher.PRIVATE, prefix="cardscan-gate-"))
        self.addCleanup(__import__("shutil").rmtree, audit, True)
        return wc.WebViewInputGate(object(), wc.RunConfig(True, frozenset(families)), {}, wc.SpendGate(),
                                   wc.AuditLog(audit), policy=RISK_POLICY)

    def gate(self):
        gate = self.make_gate(launcher.SUPERVISED_TASK_FAMILIES["CardScan"])
        gate.restrict_actions(launcher.SUPERVISED_TASK_ACTIONS["CardScan"], launcher.SUPERVISED_TASK_NODE_PREFIXES["CardScan"])
        return gate

    def resolve(self, gate, action, node):
        from types import SimpleNamespace

        point = gate.table.centre(action)
        with patch.object(gate.frame_permits, "refusal", return_value=None):
            return gate._resolve_pipeline_click(point, SimpleNamespace(node_name=node, action=action))

    def test_training_actions_and_foreign_nodes_are_refused(self):
        gate = self.gate()
        table = gate.table
        training = [a.id for a in table.actions if a.family == "training-bronze" and a.id != "home-idol" and a.enabled]
        self.assertIn("training-start", training)
        for action in training:
            with self.subTest(action=action):
                self.assertEqual(self.resolve(gate, action, table.get(action).nodes[0])[2], "action_not_in_task_allowlist")
        self.assertEqual(self.resolve(gate, "home-idol", "SC.Training.OpenIdol")[2], "node_not_in_task_allowlist")
        for action, node in (("home-idol", "SC.CardScan.Home.OpenIdol"), ("idol-portal-list", "SC.CardScan.Portal.OpenList"),
                             ("idol-list-tab-support", "SC.CardScan.List.TapSupport"),
                             ("idol-list-scroll-down", "SC.CardScan.List.ScrollDown"),
                             ("nav-back", "SC.CardScan.List.Back"), ("nav-back", "SC.Popup.Announcement.Close")):
            with self.subTest(action=action, node=node):
                self.assertEqual(self.resolve(gate, action, node), (action, None, None))
        self.assertNotIn("training-select-card", gate.enabled_actions())
        self.assertIn("idol-list-scroll-down", gate.enabled_actions())

    def test_allowlist_is_set_once_before_input_and_never_admits_spending(self):
        gate = self.gate()
        with self.assertRaises(ValueError):
            gate.restrict_actions({"nav-back"}, ("SC.CardScan.",))          # cannot be replaced or widened later
        fresh = self.make_gate(launcher.SUPERVISED_TASK_FAMILIES["Training"])
        for actions, prefixes in (({"training-start"}, ("SC.Training.",)), ({"idol-list-scroll-down"}, ("SC.CardScan.",)),
                                  (set(), ("SC.CardScan.",)), ({"nav-back"}, ("Training",))):
            with self.subTest(actions=actions, prefixes=prefixes), self.assertRaises(ValueError):
                fresh.restrict_actions(actions, prefixes)

    def test_replay_scenario_applies_the_same_allowlist(self):
        import numpy as np

        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        scenario = launcher.ReplayScenario([image, image], ["click", "click"], allow_input=True,
                                           families=launcher.SUPERVISED_TASK_FAMILIES["CardScan"],
                                           actions=launcher.SUPERVISED_TASK_ACTIONS["CardScan"])
        x, y = scenario.table.centre("training-select-card")
        self.assertFalse(scenario.click(x, y))
        self.assertEqual(scenario.actions, [])


def _scrolled_support_frame(source, dy, out):
    """The native サポート top frame with its grid moved up by ``dy`` (rows wrap) and the scrollbar thumb lowered."""
    import numpy as np
    from PIL import Image

    from agent.core import card_list_scan as cs

    with Image.open(source) as raw:
        image = np.asarray(raw.convert("RGB")).copy()
    top, bottom = cs.VIEWPORT
    x0, x1 = 250, 1220
    image[top:bottom, x0:x1] = np.roll(image[top:bottom, x0:x1].copy(), -dy, axis=0)
    bar = cs.read_scrollbar(image)
    dark = image[400, 1239:1244].copy()
    white = image[bar.thumb_top + 5, 1239:1244].copy()
    image[169:698, 1239:1244] = dark
    image[bar.thumb_top + 40:bar.thumb_bottom + 41, 1239:1244] = white
    Image.fromarray(image).save(out)
    return out


class CardScanLivePathTests(unittest.TestCase):
    """9a through the real --live --supervised-task path (WebView2 gate + fake host), native 2026-10-08 frames."""

    def test_calibration_run_sends_six_allowlisted_actions_and_records_the_step(self):
        from agent.core import webview_controller as wc
        from tests import test_card_scan_native_20261007 as N
        from tests.test_task_launcher import HAVE_MAA, RISK_POLICY, TABLE, FakePipelineHost
        if not (HAVE_MAA and N._present_1008()):
            self.skipTest("requires MaaFramework and the private 2026-10-08 native アイドル一覧 frames")
        with tempfile.TemporaryDirectory(dir=launcher.PRIVATE, prefix="cardscan-9a-") as directory:
            directory = Path(directory)
            scrolled = _scrolled_support_frame(launcher.ROOT / N.LIST_SUPPORT, 2 * 154, directory / "scrolled.png")
            frames = [N.HOMES[0], N.PORTAL_AFTER, N.LIST_FES, N.LIST_SUPPORT, scrolled, N.PORTAL_AFTER, N.HOMES[1]]
            host = FakePipelineHost([(frame, "click") for frame in frames])
            session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL,
                       "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                       "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                       "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids,
                       "action_table_sha256": TABLE.sha256, "spend_secret": "07" * 32,
                       "audit_root": str(launcher.PRIVATE / "run_logs"),
                       "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                                  "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000,
                                  "session_max_ms": 3_600_000}}
            gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(launcher.SUPERVISED_TASK_FAMILIES["CardScan"])),
                                       {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                       wc.AuditLog(directory / "audit"), host.send, policy=RISK_POLICY,
                                       limits=wc.ClickLimits(run_max=6, task_max=60))
            summary = launcher.run(copy.deepcopy(CARD_SCAN_9A), live=True, supervised_task=True, gate=gate,
                                   policy=RISK_POLICY, summary_path=False, save_diagnostics=False,
                                   task_options={"card_scan": {"run_dir": directory / "card-scan", "write_import": False}})
            self.assertEqual([row["action"] for row in host.requests],
                             ["home-idol", "idol-portal-list", "idol-list-tab-support", "idol-list-scroll-down",
                              "nav-back", "nav-back"])
            self.assertTrue(all(row["node_name"].startswith("SC.CardScan.") for row in host.requests))
            self.assertEqual(summary["live"]["supervised_task"], "CardScan")
            self.assertEqual(summary["live"]["actions"], sorted(launcher.SUPERVISED_TASK_ACTIONS["CardScan"]))
            self.assertEqual(summary["live"]["task_caps"], {"CardScan": 6})
            task, = summary["tasks"]
            scan_reports = [r for r in task["reports"] if r["kind"] == "card_scan"]
            report = scan_reports[0]
            self.assertEqual(report["outcome"], "partial:scroll_budget_exhausted")
            self.assertEqual(scan_reports[-1]["outcome"], "stopped:abort")   # the stop is always reported
            self.assertEqual(scan_reports[-1]["session_outcome"], "scroll_budget_exhausted")
            self.assertIn("card_scan_stopped:abort:scroll_budget_exhausted", task["fail_reasons"])
            self.assertEqual(task["status"], "failed")          # partial scans never count as a completed scan
            shift, = report["tabs"][0]["shifts"]
            self.assertEqual((shift["dy"], shift["overlap_px"], shift["at_bottom"]), (308, 202, False))
            self.assertEqual(report["written"], [])
            frames_dir = Path(report["frames_dir"])
            self.assertEqual(sorted(p.name for p in frames_dir.glob("frame-*.png")),        # + the settle capture
                             ["frame-001.png", "frame-002.png", "frame-003.png", "frame-004.png"])
            self.assertTrue(Path(report["record"]).name.endswith("-partial.json"))


class ResourceEventTests(unittest.TestCase):
    def test_resource_counts_survive_with_no_raw_text_or_authority(self):
        secret = "DO_NOT_EXPORT_THIS"
        report = {"kind": "resources", "layout": "home_counters", "observed_at": secret,
                  "rejected": [secret], "allow_spend": [secret], "items": {
                      "money": {"count": 1234, "confidence_pm": 990, "notes": secret},
                      "stamina": {"count": 100, "max": 100, "plus": 2, "indicator_only": True}}}
        result = RunControl.readings(report)
        self.assertEqual(result["readings"]["items"]["money"], {"count": 1234, "confidence_pm": 990})
        self.assertEqual(result["readings"]["items"]["stamina"]["max"], 100)
        self.assertNotIn(secret, json.dumps(result))
        control = RunControl()
        with patch.object(control, "emit", return_value=True) as emit:
            control.report(report)
        self.assertEqual(emit.call_args.kwargs["readings"], result["readings"])

    def test_invalid_keys_counts_and_extra_values_do_not_become_readings(self):
        result = RunControl.readings({"kind": "resources", "layout": "bad layout", "items": {
            "bad-key": {"count": 1}, "Upper": {"count": 1}, "x": {"count": True},
            "y": {"count": -1}, "z": {"count": 10**9 + 1}, "no_count": {"max": 1},
            "money": {"count": 0, "confidence_pm": 1001, "max": -1, "plus": True, "indicator_only": 1}}})
        self.assertEqual(result["readings"], {"items": {"money": {"count": 0}}})

    def test_resources_are_bounded_to_32_items_and_ignore_unstructured_items(self):
        report = {"kind": "resources", "items": {f"v{i}": {"count": i} for i in range(50)}}
        self.assertEqual(len(RunControl.readings(report)["readings"]["items"]), 32)
        self.assertEqual(RunControl.readings({"kind": "resources", "items": []})["readings"]["items"], {})


if __name__ == "__main__":
    unittest.main()
