"""Native screenshot replay resolves overlapping clicks by completed recognition, without a host."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from agent.core.pipeline_actions import parse_table
from agent.core.run_control import RunControl
from tools.run_tasks import ReplayScenario, ReplaySpendAuthority, create_replay_controller

try:
    from maa.context import ContextEventSink
    from maa.custom_recognition import CustomRecognition
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker
    HAVE_MAA = Library.version() == "v5.12.3"
except (ImportError, OSError, RuntimeError):
    HAVE_MAA = False

ROOT = Path(__file__).resolve().parents[1]
RECT = [100, 100, 40, 40]
FIRST, SECOND = "SC.Replay.First", "SC.Replay.Second"
PIXELS = np.full((720, 1280, 3), [15, 10, 5], dtype=np.uint8)


class ReplayFamilyScopeTests(unittest.TestCase):
    def test_unselected_overlapping_page_cannot_change_the_selected_page_input(self):
        actions = [
            {"id": "selected", "family": "one", "consuming": False, "rect": RECT,
             "source": "synthetic", "nodes": [FIRST]},
            {"id": "unselected", "family": "two", "consuming": False, "rect": RECT,
             "source": "synthetic", "nodes": [SECOND]},
        ]
        table = parse_table(json.dumps({"version": 2, "canvas": [1280, 720], "margin_percent": 20,
            "min_inner_px": 4, "families": ["one", "two"], "actions": actions}).encode())
        scenario = ReplayScenario([PIXELS], ["click"], True, table=table, families={"one"})
        self.assertFalse(scenario.node_binding_required)
        self.assertTrue(scenario.click(120, 120))
        self.assertEqual(scenario.actions, ["selected"])
        scoped_both = ReplayScenario([PIXELS], ["click"], True, table=table, families={"one", "two"})
        self.assertTrue(scoped_both.node_binding_required)
        self.assertFalse(scoped_both.click(120, 120))


@unittest.skipUnless(HAVE_MAA, "MaaFramework v5.12.3 required")
class ReplayNodeBindingTests(unittest.TestCase):
    def build(self, *, match=True, consuming=False, authorize=False, disabled=False, repeat=1,
              wrong_target=False, wheel=False, control=None, hook=None):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        actions = [
            {"id": "first", "family": "popup", "consuming": False, "rect": RECT,
             "source": "synthetic replay", "nodes": [FIRST]},
            {"id": "second", "family": "popup", "consuming": consuming, "rect": RECT,
             "source": "synthetic replay", "nodes": [SECOND], "enabled": not disabled},
        ]
        if wheel:
            actions[1]["wheel"] = -240
        table = parse_table(json.dumps({"version": 2, "canvas": [1280, 720], "margin_percent": 20,
            "min_inner_px": 4, "families": ["popup"], "actions": actions}).encode())
        scenario = ReplayScenario([PIXELS, PIXELS.copy()], ["click", "click"], True, table=table,
                                  allow_spend=("second",) if authorize else (), run_control=control)
        nodes = {
            "SC.Replay.Entry": {"next": [FIRST, SECOND, "SC.Replay.End"], "pre_delay": 0, "post_delay": 0},
            "SC.Replay.No": {"recognition": "ColorMatch", "roi": [0, 0, 10, 10], "count": 100,
                             "method": 4, "lower": [250, 250, 250], "upper": [255, 255, 255]},
            "SC.Replay.Yes": {"recognition": "ColorMatch", "roi": [0, 0, 10, 10], "count": 100,
                              "method": 4, "lower": [5, 10, 15], "upper": [5, 10, 15]},
            "SC.Replay.End": {"pre_delay": 0, "post_delay": 0},
        }
        for name in (FIRST, SECOND):
            nodes[name] = {"recognition": "And", "all_of": ["SC.Replay.Yes" if name == SECOND and match
                           else "SC.Replay.No"], "action": "Click", "target": RECT,
                           "next": ["SC.Replay.End"], "on_error": ["SC.Replay.End"],
                           "timeout": 150, "rate_limit": 10, "pre_delay": 0, "post_delay": 0}
        nodes[SECOND]["repeat"] = repeat
        if wrong_target:
            nodes[SECOND]["target"] = [200, 200, 40, 40]
        if authorize:
            nodes[SECOND]["all_of"].append({"recognition": "Custom", "custom_recognition": "SC.Replay.Grant"})
        (directory / "pipeline").mkdir()
        (directory / "pipeline/probe.json").write_text(json.dumps(nodes), encoding="utf-8")
        resource = Resource()
        self.assertTrue(resource.post_bundle(directory).wait().succeeded)
        authority = ReplaySpendAuthority(scenario)

        class Grant(CustomRecognition):
            def analyze(self, context, argv):
                if authority.authorize("second", argv.image, "SC.Replay.Grant") is None:
                    return self.AnalyzeResult((0, 0, 10, 10), {})
                return None

        grant = Grant()
        self.assertTrue(resource.register_custom_recognition("SC.Replay.Grant", grant))
        from agent.core.pipeline_frame_guard import install
        binder = install(resource, scenario)
        controller = create_replay_controller(scenario)
        self.assertTrue(controller.set_screenshot_use_raw_size(True))
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        binder.attach(tasker)

        class Hook(ContextEventSink):
            def on_raw_notification(self, context, message, details):
                if message == "Node.Action.Starting" and details.get("name") == SECOND and hook is not None:
                    hook(scenario, context)

        sink = Hook()
        tasker.add_context_sink(sink)
        return SimpleNamespace(scenario=scenario, tasker=tasker, binder=binder, controller=controller,
                               resource=resource, grant=grant, sink=sink)

    def run_replay(self, **options):
        harness = self.build(**options)
        harness.tasker.post_task("SC.Replay.Entry").wait()
        self.assertEqual(harness.scenario.other_calls, 0)
        self.assertIsNone(harness.scenario.frame_permits.take())
        return harness

    def test_overlapping_rect_uses_successful_parent_not_first_table_entry(self):
        harness = self.run_replay()
        self.assertTrue(harness.scenario.node_binding_required)
        self.assertEqual(harness.scenario.actions, ["second"])
        self.assertEqual(harness.scenario.index, 1)
        self.assertFalse(harness.scenario.click(120, 120))  # ended task cannot reuse its node

    def test_unambiguous_families_keep_existing_replay(self):
        # popup overlaps since the Match season 閉じる (a0e5a1e); nav + present still have disjoint rects.
        scenario = ReplayScenario([PIXELS], ["click"], True, families={"nav", "present"})
        self.assertFalse(scenario.node_binding_required)

    def test_enabled_training_grid_overlaps_and_requires_node_binding(self):
        # The training list/sort grid is enabled in the table (since 74d176a) and its rects overlap, so replay must bind by node.
        scenario = ReplayScenario([PIXELS], ["click"], True, families={"training-bronze", "nav", "popup"})
        self.assertTrue(scenario.node_binding_required)

    def test_failed_parent_never_clicks(self):
        harness = self.run_replay(match=False)
        self.assertEqual(harness.scenario.actions, [])
        self.assertEqual(harness.scenario.index, 0)

    def test_disabled_catalog_entry_does_not_select_overlapping_enabled_action(self):
        harness = self.run_replay(disabled=True)
        self.assertEqual(harness.scenario.actions, [])

    def test_scroll_action_resolves_through_its_click_node(self):
        harness = self.run_replay(wheel=True)
        self.assertEqual(harness.scenario.actions, ["second"])

    def test_extra_identical_capture_clears_completed_recognition(self):
        harness = self.run_replay(hook=lambda scenario, context: scenario.screencap())
        self.assertEqual(harness.scenario.actions, [])

    def test_scenario_change_clears_completed_recognition(self):
        harness = self.run_replay(hook=lambda scenario, context:
            scenario.reset_frames([PIXELS.copy()], ["click"], True))
        self.assertEqual(harness.scenario.actions, [])

    def test_stop_before_click_clears_binding(self):
        control = RunControl()
        harness = self.run_replay(control=control,
                                  hook=lambda scenario, context: control.request_stop())
        self.assertEqual(harness.scenario.actions, [])

    def test_repeat_cannot_reuse_single_click_permission(self):
        harness = self.run_replay(repeat=2)
        self.assertEqual(harness.scenario.actions, ["second"])
        self.assertEqual(len(harness.scenario.refused), 1)

    def test_wrong_target_is_not_resolved_to_another_action(self):
        harness = self.run_replay(wrong_target=True)
        self.assertEqual(harness.scenario.actions, [])

    def test_consuming_action_still_needs_its_separate_grant(self):
        harness = self.run_replay(consuming=True)
        self.assertEqual(harness.scenario.actions, [])
        self.assertEqual(harness.scenario.spends, [("second", "denied")])
        harness = self.run_replay(consuming=True, authorize=True)
        self.assertEqual(harness.scenario.actions, ["second"])
        self.assertEqual(harness.scenario.spends, [("second", "sent")])


if __name__ == "__main__":
    unittest.main()
