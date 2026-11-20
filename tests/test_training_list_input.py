"""Training list grid/scroll wiring (schedule ① step 2-3, TRAINING_INPUT_BINDINGS_20261007): the 20 grid-slot and two
wheel Click nodes of training.json, their positive guards, error paths, re-capture after each input and the
「训练列表滚动」 setting. The reserved catalog actions stay ``enabled: false``; the replays enable a copy of the table
offline only. No device, no input.

* Static Pipeline/catalog binding: always.
* Runtime (fake screen): needs the maa Python package.
* Whole-task replays through tools/run_tasks.py --simulate-input: MaaFramework, the OCR model, the private template
  bundle and the private training frames (skipped otherwise).
"""
import copy
import datetime as dt
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from agent.core import pipeline_actions as pa
from agent.core import training_select as ts
from agent.core import training_task as tt
from tests import training_list_frames as F
from tests.test_training_select import GRID_TABLE, NO_SLOTS, TABLE, enabled, frame, location_ready, view

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "training.json"
TASK = ROOT / "assets" / "tasks" / "training.json"
LEVEL_DESC = ts.ListOrder("level", "desc")
DEFAULT_DESC = ts.ListOrder("default", "desc")
# 2026-10-07 native list after the supervised run: デフォルト/▼降順, あさひ Lv5 at r1c2 (capture, host seq 123).
LIST_DEFAULT_1007 = "dev_assets/private/fes-match-reader-20261007/training_list_descending.png"
SLOT_NODES = {a: n for a, n in ts.SELECT_NODES.items() if a != "training-select-card"}


def nodes():
    return json.loads(PIPELINE.read_text(encoding="utf-8"))


# ======================================================================== static binding
class PipelineBindingTests(unittest.TestCase):
    def test_every_list_click_node_is_bound_and_guarded(self):
        data, bound = nodes(), TABLE.node_actions(include_disabled=True)
        self.assertEqual(len(SLOT_NODES), 20 + 35)   # 2026-11-22: +21 grid b (+51 px), +14 grid c (+103 px)
        clicks = {**{n: a for a, n in ts.SELECT_NODES.items()},
                  **{n: ts.SCROLL_ACTIONS[d] for d, n in ts.SCROLL_NODES.items()}}
        for node, action in clicks.items():
            with self.subTest(node=node):
                spec = data[node]
                self.assertEqual(bound[node], action)
                self.assertIs(spec["enabled"], False)
                self.assertEqual(spec["action"], "Click")
                self.assertEqual(spec["target"], list(TABLE.get(action).rect))
                guard, active, plan = spec["all_of"]
                self.assertEqual((guard, active), ("SC.Training.List.Guard", "SC.Training.State.Active"))
                param = data[plan]["custom_recognition_param"]
                self.assertEqual(data[plan]["custom_recognition"], "SC.Training.List")
                self.assertEqual(param["node"], node)   # the recognition names the click node it answers for
                if action in ts.SCROLL_ACTIONS.values():
                    self.assertEqual((param["want"], ts.SCROLL_ACTIONS[param["direction"]]), ("scroll", action))
                    self.assertEqual((spec["next"], spec["on_error"]),
                                     (["SC.Training.List.Scrolled"], ["SC.Training.List.ScrollFailed"]))
                    self.assertLessEqual(spec["max_hit"], tt.MAX_SCROLL_STEPS)
                else:
                    self.assertEqual((param["want"], param["action"]), ("open", action))
                    self.assertEqual((spec["next"], spec["on_error"]),
                                     (["SC.Training.List.Tapped"], ["SC.Training.List.TapFailed"]))
                self.assertIsNone(ts.binding_refusal(TABLE, node, action))
        # The wheel pair was enabled on 2026-10-11 (user approval, ∓120); the 20 slot actions were enabled by ANN-47
        # after the 21 full grid slots passed real-card replay, each bound to its own table action.
        self.assertTrue(all(TABLE.get(a).enabled for a in SLOT_NODES))
        self.assertEqual(len(ts.select_actions(TABLE)), 21 + 35)
        self.assertTrue(ts.scroll_wired(TABLE))

    def test_main_loop_order_and_error_paths(self):
        data = nodes()
        main = data["SC.Training.Main"]["next"]
        start = main.index("[JumpBack]SC.Training.List.Select")
        expected = (["[JumpBack]" + ts.LEGACY_SELECT_NODE] + ["[JumpBack]" + n for n in SLOT_NODES.values()]
                    + ["[JumpBack]SC.Training.List.SortOpen", "[JumpBack]SC.Training.List.SortToggle",
                       "[JumpBack]SC.Training.List.ScrollDown", "[JumpBack]SC.Training.List.ScrollUp",
                       "[JumpBack]SC.Training.List.Hold"])
        self.assertEqual(main[start:start + len(expected)], expected)
        # The detail/confirm stages stay before the list; nothing new runs ahead of the stop/result nodes.
        self.assertLess(main.index("[JumpBack]SC.Training.Detail.Plus"), start)
        self.assertEqual(data["SC.Training.List.Scrolled"]["all_of"], ["SC.Training.List.Guard"])
        for name, reason in (("SC.Training.List.TapFailed", "tile_tap_unverified"),
                             ("SC.Training.List.ScrollFailed", "scroll_unverified")):
            self.assertEqual((data[name]["custom_action"], data[name]["custom_action_param"]),
                             ("SC.Training.Abort", {"list_error": reason}))
        self.assertEqual(data[ts.LEGACY_SELECT_NODE]["on_error"], ["SC.Training.List.TapFailed"])

    def test_scroll_setting_is_bounded_and_defaults_small(self):
        data, task = nodes(), json.loads(TASK.read_text(encoding="utf-8"))
        # 2026-10-09 (user): no 「列表最多滚动」 setting; the fixed bounded budget stays on SC.Training.Config.List.
        self.assertNotIn("训练列表滚动", task["option"])
        self.assertNotIn("训练列表滚动", task["task"][0]["option"])
        steps = [tt.DEFAULT_SCROLL_STEPS]
        self.assertEqual(tt.DEFAULT_SCROLL_STEPS, 10)   # Search the whole list for the card
        self.assertIn("SC.Training.Config.List", data["SC.Training.Entry"]["custom_action_param"]["config_nodes"])
        self.assertEqual(data["SC.Training.Config.List"]["attach"], {"max_scroll_steps": tt.DEFAULT_SCROLL_STEPS})
        self.assertEqual(tt.TrainingConfig.from_params({}).max_scroll_steps, tt.DEFAULT_SCROLL_STEPS)
        from tools import run_tasks
        self.assertIsNone(run_tasks.LIVE_TASK_CLICK_CAPS["Training"])   # no click cap
        for bad in (-1, tt.MAX_SCROLL_STEPS + 1, "3", True):
            with self.assertRaises(ValueError):
                tt.TrainingConfig.from_params({"max_scroll_steps": bad})

    def test_mis_bound_node_is_refused(self):
        self.assertIsNone(ts.binding_refusal(TABLE, "SC.Training.List.Select.R2C5", "training-list-slot-r2c5"))
        self.assertTrue(ts.binding_refusal(TABLE, "SC.Training.List.Select.R2C4", "training-list-slot-r2c5")
                        .startswith("node_binding_mismatch"))
        self.assertTrue(ts.binding_refusal(TABLE, "SC.Training.List.ScrollUp", "training-list-scroll-down")
                        .startswith("node_binding_mismatch"))
        self.assertEqual(ts.binding_refusal(TABLE, None, "training-list-slot-r2c5"), "node_binding_missing")
        self.assertTrue(ts.binding_refusal(TABLE, "SC.Training.List.Select.R9C9", "training-list-slot-r9c9")
                        .startswith("node_binding_mismatch"))


# ======================================================================== runtime (fake screen)
try:
    from agent.core import training_custom
    HAVE_RUNTIME = True
except Exception:  # pragma: no cover - environment without the maa package
    HAVE_RUNTIME = False


@unittest.skipUnless(HAVE_RUNTIME, "需要 maa Python 包")
# one fake frame per step: the live settle check (two matching list readings) is covered by SettleTests
@mock.patch.object(tt, "MAX_SETTLE_HOLDS", 0)
class GridRuntimeTests(unittest.TestCase):
    TEXTS = {1: ("Lv順", "▼降順", "絞り込み"), 2: ("デフォルト", "▼降順", "絞り込み"), 5: ("Lv順", "▼降順", "絞り込み"),
             6: ("Lv順", "▼降順", "絞り込み")}

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_tlin_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        test = self

        class Vision:
            def __init__(self, context):
                pass

            def text(self, image, roi, threshold=None):
                sort, direction, flt = test.TEXTS[int(image[0, 0, 0])]
                return {tuple(ts.SORT_BOX): sort, tuple(ts.DIRECTION_BOX): direction,
                        tuple(ts.FILTER_BOX): flt}[tuple(roi)]

        self.views, self.locates = {}, []

        def fake_locate(pil, spec, index, reference):
            marker = pil.getpixel((0, 0))[2]
            self.locates.append(marker)
            return self.views[marker]

        patcher = mock.patch.object(ts, "locate", fake_locate)
        patcher.start()
        self.addCleanup(patcher.stop)
        now = dt.datetime(2026, 10, 8, 3, 0, tzinfo=dt.timezone.utc)
        self.runtime = training_custom.TrainingRuntime(log_root=self.root, now=lambda: now, vision=Vision,
                                                       card_index=object(), reference_loader=lambda spec: "ref",
                                                       table=GRID_TABLE)

    def start(self, **config):
        self.runtime.run = tt.TrainingRun(tt.TrainingConfig(tt.CARD_ID, **config), self.runtime.log)
        return self.runtime.run

    def open(self, marker, action, node=None):
        return self.runtime.list_step(None, frame(marker), {"want": "open", "action": action,
                                                            "node": node or ts.SELECT_NODES[action]})

    def scroll(self, marker, direction, node=None):
        return self.runtime.list_step(None, frame(marker), {"want": "scroll", "direction": direction,
                                                            "node": node or ts.SCROLL_NODES[direction]})

    def main_round(self, marker):
        """The Main loop's list nodes in Pipeline order on one frame: the first that answers True, or None."""
        for action in ts.SELECT_NODES:
            if self.open(marker, action):
                return action
        for direction in ("down", "up"):
            if self.scroll(marker, direction):
                return ts.SCROLL_ACTIONS[direction]
        return None

    def test_each_slot_opens_only_through_its_own_node(self):
        for slot, box in (("r1c1", (267, 184, 108, 108)), ("r2c5", (819, 338, 108, 108)),
                          ("r3c7", (1095, 492, 108, 108)), ("r1c2", (405, 184, 108, 108))):
            with self.subTest(slot=slot):
                self.locates.clear()
                run = self.start()
                self.views = {1: view("found", slot=slot, box=box)}
                expected = "training-select-card" if slot == "r1c2" else "training-list-slot-" + slot
                self.assertEqual(self.main_round(1), expected)
                self.assertEqual(self.locates, [1])          # one identity pass for the whole round
                self.assertIsNone(run.list_frame)             # consumed: the next capture is a new observation
                self.assertFalse(run.finished)
                self.assertEqual(run.decisions[-1]["action"], expected)

    def test_disabled_catalog_never_opens_a_new_slot(self):
        self.runtime._table = NO_SLOTS
        run = self.start()
        self.views = {1: view("found", slot="r2c5", box=(819, 338, 108, 108))}
        self.assertIsNone(self.main_round(1))
        self.assertEqual((run.outcome, run.notes[0]), ("target_slot_unbound", "slot=r2c5"))

    def test_mis_bound_node_stops_before_reading_the_list(self):
        run = self.start()
        self.views = {1: view("found", slot="r2c5", box=(819, 338, 108, 108))}
        # A copied parameter block: node R2C4 claiming the r2c5 action would tap r2c4's rect.
        self.assertFalse(self.open(1, "training-list-slot-r2c5", node="SC.Training.List.Select.R2C4"))
        self.assertEqual(run.outcome, "list_binding_invalid")
        self.assertEqual(self.locates, [])
        run = self.start()
        self.assertFalse(self.scroll(1, "down", node="SC.Training.List.ScrollUp"))
        self.assertEqual(run.outcome, "list_binding_invalid")
        run = self.start()
        self.assertFalse(self.runtime.list_step(None, frame(1), {"want": "open", "action": "training-list-slot-r2c5"}))
        self.assertEqual((run.outcome, run.notes), ("list_binding_invalid", ["node_binding_missing"]))

    def test_identical_capture_after_a_wheel_step_is_the_list_end(self):
        """Bottom of the list: the wheel moves nothing, the re-captured frame is pixel-identical."""
        run = self.start(max_scroll_steps=3)
        self.views = {1: view("absent", "a")}
        self.assertEqual(self.main_round(1), "training-list-scroll-down")
        self.assertIsNone(self.main_round(1))     # from the proven top (offset 184) the end exhausts the search
        self.assertEqual((run.outcome, self.locates), ("target_not_visible", [1, 1]))
        self.assertIn("target_absent", run.notes)
        self.assertIn("list_exhausted", run.notes)
        self.assertEqual(run.list_search.steps, 1)

    def test_budget_exhaustion_stops(self):
        run = self.start(max_scroll_steps=2)
        self.views = {1: view("absent", "a"), 5: view("absent", "b"), 6: view("absent", "c")}
        self.assertEqual([self.main_round(m) for m in (1, 5, 6)],
                         ["training-list-scroll-down", "training-list-scroll-down", None])
        self.assertEqual(run.outcome, "target_not_visible")
        self.assertIn("target_not_found_within_budget", run.notes)
        self.assertIn("steps=2", run.notes)
        run = self.start(max_scroll_steps=0)
        self.assertIsNone(self.main_round(1))
        self.assertEqual((run.outcome, "scroll_unavailable" in run.notes), ("target_not_visible", False))

    def test_sort_change_after_a_wheel_step_relocates_by_identity(self):
        run = self.start(max_scroll_steps=3)
        self.views = {1: view("absent", "a"), 2: view("found", "b", slot="r3c7", box=(1095, 492, 108, 108))}
        self.assertEqual(self.main_round(1), "training-list-scroll-down")
        self.assertEqual(self.main_round(2), "training-list-slot-r3c7")
        self.assertIn("list_order_changed", run.decisions[-1]["decision"]["notes"])
        self.assertEqual((run.list_search.invalidations, run.list_search.steps), (1, 0))

    def test_unobserved_input_result_has_its_own_outcome(self):
        for reason in ("tile_tap_unverified", "scroll_unverified"):
            run = self.start()
            self.assertFalse(self.runtime.abort(None, {"list_error": reason}))
            self.assertEqual((run.outcome, run.notes), ("list_input_unverified", [reason]))
        run = self.start()
        self.runtime.abort(None, {})
        self.assertEqual(run.outcome, "timeout")
        run = self.start()
        run.finish("target_ambiguous")
        self.runtime.abort(None, {"list_error": "scroll_unverified"})
        self.assertEqual(run.outcome, "target_ambiguous")   # an earlier conclusion is never overwritten


# ======================================================================== whole-task replays (MaaFramework)
try:
    from tests import test_daily_b5_tasks as b5
    from tools import run_tasks as launcher
    HAVE_REPLAY = b5.HAVE_MAA and b5.frames_present()
except Exception:  # pragma: no cover
    HAVE_REPLAY = False

TO_LIST = ["home-idol", "idol-training"]
BACK3 = ["nav-back", "nav-back", "nav-back"]
DOWN, UP = "training-list-scroll-down", "training-list-scroll-up"


@unittest.skipUnless(HAVE_REPLAY and location_ready() and (ROOT / LIST_DEFAULT_1007).is_file(),
                     "需要 MaaFramework、OCR 模型、私有模板包与训练帧")
class GridReplayTests(unittest.TestCase):
    """The reserved grid/wheel actions enabled in a copy of the table (offline only): what the reviewed batch will
    run. Every simulated tap lands inside its own catalog rect through the node-bound replay controller."""

    @classmethod
    def setUpClass(cls):
        if not b5.private_ready():
            raise unittest.SkipTest("私有回放数据不完整")
        b5.build_synthetic_frames()
        F.build()

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_tlin_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        patcher = mock.patch.object(pa, "default_table", return_value=GRID_TABLE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_training(self, frames, steps=None, override=None, spend=()):
        option = {"训练卡": tt.CARD_IDS[tt.CARD_ID]}
        if steps is not None:  # 2026-10-09: no option any more; tests set the fixed carrier node directly
            override = {"SC.Training.Config.List": {"attach": {"max_scroll_steps": steps}}, **(override or {})}
        config = {"preset": None, "tasks": [{"name": "Training", "option": option}]}
        if spend:
            config["allow_spend"] = list(spend)
        options = {"training": {"log_root": self.root, "now": lambda: b5.NOW, "sample_root": self.root / "samples"}}
        plan_tasks = launcher.plan_tasks

        def planned(interface, cfg):
            plans = plan_tasks(interface, cfg)
            if override:
                plans = [launcher.TaskPlan(p.name, p.entry, launcher.merge_override(p.pipeline_override, override))
                         for p in plans]
            return plans

        with mock.patch.object(launcher, "plan_tasks", planned):
            summary = launcher.run(config, offline_steps=[(ROOT / f, "click") for f in frames], simulate_input=True,
                                   policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                                   task_options=options)
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "training"]
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(GRID_TABLE.get(action).contains(point), (point, action))
        self.assertEqual(task["simulated_spends"], [["training-start", "sent"]] if spend else [])
        return task, report

    def lists(self, report):
        return [d for d in report["decisions"] if d.get("stage") == "List" and "decision" in d]

    def test_target_in_any_grid_slot_opens_through_its_slot_node(self):
        for name, slot in ((F.LV_DESC_R2C5, "r2c5"), (F.LV_DESC_R3C7, "r3c7"), (F.LV_ASC_R1C1, "r1c1")):
            with self.subTest(slot=slot):
                shutil.rmtree(self.root, ignore_errors=True)
                task, report = self.run_training([b5.HOME, b5.IDOL, name, b5.ZERO, name, b5.IDOL, b5.HOME])
                self.assertEqual(report["outcome"], "spend_not_enabled", report)
                self.assertEqual(task["simulated_actions"], TO_LIST + ["training-list-slot-" + slot] + BACK3)
                self.assertIn("SC.Training.List.Select.R%sC%s" % (slot[1], slot[3]), task["node_trace"])
                self.assertEqual(self.lists(report)[0]["action"], "training-list-slot-" + slot)

    def test_one_wheel_step_then_the_target_slot_trains(self):
        frames = [b5.HOME, b5.IDOL, F.LIST_LV_DESC, F.LV_DESC_R3C7, b5.ZERO, b5.ONE, b5.CONFIRM, b5.RESULT, b5.AFTER,
                  F.LV_DESC_R3C7, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames, spend=["training-start"])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "trained"), report)
        self.assertEqual(task["simulated_actions"][:4], TO_LIST + [DOWN, "training-list-slot-r3c7"])
        self.assertIn("SC.Training.List.Scrolled", task["node_trace"])
        decisions = [d["decision"]["kind"] for d in self.lists(report)]
        self.assertEqual(decisions, ["scroll", "open"])

    def test_sort_change_after_a_wheel_step_relocates(self):
        """Lv順 page without the target, wheel, then the user switched to デフォルト: relocated at r1c2 by identity."""
        frames = [b5.HOME, b5.IDOL, F.LIST_LV_DESC, LIST_DEFAULT_1007, b5.ZERO, LIST_DEFAULT_1007, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames)
        self.assertEqual(report["outcome"], "spend_not_enabled", report)
        self.assertEqual(task["simulated_actions"], TO_LIST + [DOWN, "training-select-card"] + BACK3)
        last = self.lists(report)[-1]
        self.assertEqual((last["order"], last["decision"]["notes"]), ({"key": "default", "direction": "desc"},
                                                                      ["list_order_changed"]))

    def test_similar_card_after_a_wheel_step_stops(self):
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LIST_LV_DESC, F.LOOKALIKE_GOLD, b5.IDOL, b5.HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "target_ambiguous"), report)
        self.assertEqual(task["simulated_actions"], TO_LIST + [DOWN, "nav-back", "nav-back"])

    def test_list_end_turns_once_then_stops(self):
        # 2026-10-11: the list is at its proven top (offset 184), so its end exhausts the search (no upward pass)
        frames = [b5.HOME, b5.IDOL, F.LIST_LV_DESC, F.LIST_LV_DESC, b5.IDOL, b5.HOME]
        task, report = self.run_training(frames, steps=3)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "target_not_visible"), report)
        self.assertEqual(task["simulated_actions"], TO_LIST + [DOWN, "nav-back", "nav-back"])
        self.assertIn("list_exhausted", report["notes"])

    def test_budget_setting_bounds_the_wheel(self):
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LIST_LV_DESC, F.LIST_LV_DESC, b5.IDOL, b5.HOME], steps=1)
        self.assertEqual(report["outcome"], "target_not_visible", report)
        self.assertEqual(task["simulated_actions"], TO_LIST + [DOWN, "nav-back", "nav-back"])
        self.assertIn("list_exhausted", report["notes"])     # the stall is seen before the budget
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LIST_LV_DESC, b5.IDOL, b5.HOME], steps=0)
        self.assertEqual(task["simulated_actions"], TO_LIST + ["nav-back", "nav-back"])
        self.assertEqual(report["outcome"], "target_not_visible")

    def test_wrong_page_after_an_input_stops_without_retry(self):
        # Wheel step, but the re-capture is the アイドル page (not the list): no second input, explicit outcome.
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LIST_LV_DESC, b5.IDOL, b5.HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "list_input_unverified"), report)
        self.assertEqual(task["simulated_actions"], TO_LIST + [DOWN])
        self.assertIn("scroll_unverified", report["notes"])
        self.assertIn("SC.Training.List.ScrollFailed", task["node_trace"])
        # Slot tap, but no detail page follows.
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LV_DESC_R2C5, b5.IDOL, b5.HOME])
        self.assertEqual((report["outcome"], task["simulated_actions"]),
                         ("list_input_unverified", TO_LIST + ["training-list-slot-r2c5"]))
        self.assertIn("tile_tap_unverified", report["notes"])

    def test_list_nodes_never_fire_on_another_page(self):
        # The detail page of the configured card comes first: no list node may answer on it (0 list inputs).
        task, report = self.run_training([b5.HOME, b5.IDOL, b5.ZERO, b5.IDOL, b5.HOME])
        self.assertTrue(all(not a.startswith("training-list-") and a != "training-select-card"
                            for a in task["simulated_actions"]), task["simulated_actions"])
        self.assertFalse(any(d.get("stage") == "List" for d in report["decisions"]))

    def test_mis_bound_pipeline_node_is_refused(self):
        """A copied parameter block: node R2C4 answers for the r2c5 target (it would tap r2c4's rect)."""
        override = {"SC.Training.List.Locate.R2C4": {"custom_recognition_param": {
            "want": "open", "action": "training-list-slot-r2c5", "node": "SC.Training.List.Select.R2C4"}}}
        task, report = self.run_training([b5.HOME, b5.IDOL, F.LV_DESC_R2C5, b5.IDOL, b5.HOME], override=override)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "list_binding_invalid"), report)
        self.assertEqual(task["simulated_actions"], TO_LIST + ["nav-back", "nav-back"])

    def test_the_shipped_table_enables_the_slot_nodes(self):
        with mock.patch.object(pa, "default_table", return_value=TABLE):
            override = launcher.mode_override(launcher.OFFLINE_SIMULATED)
            live = launcher.mode_override(launcher.LIVE_INPUT, families={"training-bronze", "nav", "popup"})
        for name in SLOT_NODES.values():      # ANN-47: enabled with their table action, like the wheel nodes
            self.assertTrue(override[name]["enabled"])
            self.assertTrue(live[name]["enabled"])
        # enabled live 2026-10-11 (wheel/direction), and the 並び替え popup after its native samples (2026-10-11 晚)
        for name in list(ts.SCROLL_NODES.values()) + [ts.SORT_NODE, ts.SORT_OPEN_NODE, ts.SORT_APPLY_NODE,
                                                      *ts.SORT_KEY_NODES.values()]:
            self.assertIn(name, live)
        self.assertIn(ts.LEGACY_SELECT_NODE, live)


if __name__ == "__main__":
    unittest.main()
