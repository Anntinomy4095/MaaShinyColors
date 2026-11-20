"""「只读取资源」 (ResourceRead, role-play D6/A8, 2026-10-26): one guarded home-counter read with no click of its own."""
import json
import unittest
from pathlib import Path

from agent.core import pipeline_actions as pa
from agent.core import resource_read as rr
from agent.core.task_actions import ReportSink
from tests.test_resource_observer import (ANCHORS, COUNTERS, FES_MAIN, HOME_NOBADGE, HOME_SHOP, FakeVision, _maa_ready,
                                          frame)
from tools import run_tasks as launcher
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline"
NOW = type("Now", (), {"isoformat": lambda self: "2026-10-26T01:02:03+00:00"})()
FES_MISREAD = ANCHORS + COUNTERS[:3] + [("17/20D+0", 0.99, (1138, 671, 95, 22))]


def runtime(vision, **options):
    sink = ReportSink()
    return rr.ResourceReadRuntime(sink, vision=vision, now=lambda: NOW, **options), sink


class ReadRuleTests(unittest.TestCase):
    def test_complete_read_hits_once_and_reports_the_counters(self):
        rt, sink = runtime(FakeVision())
        detail = rt.recognise(None, frame())
        self.assertEqual((detail["ok"], detail["missing"], detail["frames"]), (True, [], 1))
        self.assertTrue(rt.report())
        report, = sink.drain()
        self.assertEqual((report["kind"], report["source"], report["ok"], report["observed_at"]),
                         ("resources", "resource_read", True, "2026-10-26T01:02:03+00:00"))
        self.assertEqual(report["items"]["money"], {"count": 17678700, "confidence_pm": 999})
        self.assertEqual(report["items"]["fes"], {"count": 20, "max": 20, "plus": 0, "confidence_pm": 999})
        self.assertTrue(report["items"]["jewel"]["indicator_only"])                # shown only
        self.assertNotIn("text", json.dumps(report))                                 # no raw OCR text

    def test_other_screens_are_a_miss_without_ocr(self):
        for vision in (FakeVision(home=False), FakeVision(foreign=True)):
            rt, sink = runtime(vision)
            FakeVision.calls = []
            for _ in range(5):
                self.assertIsNone(rt.recognise(None, frame()))
            self.assertFalse([c for c in FakeVision.calls if c[0] == "ocr"])
            self.assertEqual((rt.unreadable, sink.drain()), (0, []))
        rt, _ = runtime(FakeVision())
        self.assertIsNone(rt.recognise(None, frame()[:360]))                         # wrong canvas

    def test_partial_reads_give_up_after_three_frames_and_keep_what_was_read(self):
        rt, sink = runtime(FakeVision(texts=FES_MISREAD))
        self.assertIsNone(rt.recognise(None, frame()))
        self.assertIsNone(rt.recognise(None, frame()))
        detail = rt.recognise(None, frame())
        self.assertEqual((detail["ok"], detail["missing"], detail["frames"]), (False, ["fes"], 3))
        self.assertFalse(rt.report())
        report, = sink.drain()
        self.assertEqual((report["ok"], report["missing"], report["rejected"]), (False, ["fes"], ["fes"]))
        self.assertEqual(report["items"]["money"]["count"], 17678700)              # clean reads are still kept
        self.assertEqual(rt.unreadable, 0)

    def test_a_read_recovers_after_an_unreadable_frame(self):
        vision = FakeVision(texts=FES_MISREAD)
        rt, _ = runtime(vision)
        self.assertIsNone(rt.recognise(None, frame()))
        vision.texts = ANCHORS + COUNTERS
        self.assertEqual(rt.recognise(None, frame())["frames"], 2)

    def test_dialog_inside_a_counter_area_is_never_read_as_a_value(self):
        rt, _ = runtime(FakeVision(texts=ANCHORS + COUNTERS + [("キャンセル", 0.99, (1000, 670, 80, 20))]))
        for _ in range(2):
            self.assertIsNone(rt.recognise(None, frame()))
        detail = rt.recognise(None, frame())
        self.assertEqual((detail["ok"], detail["items"], detail["refused"]), (False, {}, "dialog_overlay"))

    def test_report_without_a_read_fails(self):
        rt, sink = runtime(FakeVision())
        self.assertFalse(rt.report())
        self.assertEqual(sink.drain()[0]["error"], "no_read")

    def test_reading_store_keeps_the_read_like_a_passive_reading(self):
        from agent.core import report_readings

        rt, sink = runtime(FakeVision())
        rt.recognise(None, frame())
        rt.report()
        report, = sink.drain()
        keys = {key for key, _, _ in report_readings.resources(report)}
        self.assertEqual(keys, {"resource.money", "resource.jewel", "resource.stamina", "resource.fes_rights",
                                "gacha.home"})


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = load_jsonc(PIPELINE / "resource_read.json")

    def test_no_node_of_its_own_clicks(self):
        self.assertEqual(sorted(self.nodes), ["SC.ResourceRead.Done", "SC.ResourceRead.Entry", "SC.ResourceRead.NotHome",
                                              "SC.ResourceRead.Read", "SC.ResourceRead.Unreadable"])
        for name, node in self.nodes.items():
            self.assertIn(node.get("action", "DoNothing"), ("DoNothing", "Custom"), name)
        self.assertEqual(self.nodes["SC.ResourceRead.Entry"]["next"],
                         ["SC.ResourceRead.Read", "[JumpBack]SC.Popup.CloseKnown", "[JumpBack]SC.Loading.Wait"])
        self.assertEqual((self.nodes["SC.ResourceRead.Read"]["custom_recognition"],
                          self.nodes["SC.ResourceRead.Read"]["custom_action"]), (rr.RECOGNITION, rr.ACTION))
        table = pa.default_table()
        self.assertFalse([a.id for a in table.actions if any(n.startswith("SC.ResourceRead.") for n in a.nodes)])

    def test_interface_task_and_supervised_scope(self):
        interface = load_jsonc(ROOT / "assets" / "interface.json")
        self.assertIn("tasks/resource_read.json", interface["import"])
        task, = load_jsonc(ROOT / "assets" / "tasks" / "resource_read.json")["task"]
        self.assertEqual((task["name"], task["entry"], task["default_check"]), ("ResourceRead", "SC.ResourceRead.Entry", False))
        self.assertEqual(launcher.SUPERVISED_TASK_FAMILIES["ResourceRead"], frozenset({"popup"}))
        self.assertIsNone(launcher.LIVE_TASK_CLICK_CAPS["ResourceRead"])   # popup family only; no click cap
        config = launcher.sequence_run_config({"tasks": [{"name": "Startup", "enabled": True},
                                                         {"name": "ResourceRead", "enabled": True}]})
        self.assertEqual((config.get("task_max_clicks"), config["allow_spend"]), (None, []))
        scoped = launcher.supervised_sequence_config(config, live=True, offline=False, simulate_input=False)
        self.assertEqual(sorted(scoped["families"]), ["nav", "popup", "startup"])
        # while it runs, only the shared pop-up closes of the run's families stay enabled (startup/nav are off)
        table, clicks = pa.default_table(), launcher.click_node_actions()
        override = launcher.sequence_task_override("ResourceRead", scoped["families"], [])
        in_run = {node for node, action in clicks.items() if table.get(action).family in scoped["families"]
                  and not node.startswith(launcher.SUPERVISED_ONLY_NODE_PREFIXES)}
        enabled = {node for node in in_run if override.get(node) != {"enabled": False}}
        self.assertTrue(enabled)
        self.assertEqual({table.get(clicks[node]).family for node in enabled}, {"popup"})


@unittest.skipUnless(_maa_ready(HOME_NOBADGE, HOME_SHOP, FES_MAIN), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与原生首页帧")
class OfflineReplayTests(unittest.TestCase):
    """The real Pipeline + recognition on native frames (offline replay, input simulated and recorded)."""

    def run_offline(self, tasks, steps):
        return launcher.run({"tasks": tasks}, offline_steps=[(ROOT / image, advance) for image, advance in steps],
                            simulate_input=True, policy={"live_input_enabled": False},
                            summary_path=False, save_diagnostics=False)

    def test_reads_the_native_home_without_input(self):
        summary = self.run_offline(["ResourceRead"], [(HOME_NOBADGE, "click")])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_clicks"]), ("succeeded", []), task)
        self.assertEqual(task["node_trace"][-1], "SC.ResourceRead.Done")
        report, = [r for r in task["reports"] if r["kind"] == "resources"]
        self.assertEqual((report["source"], report["ok"]), ("resource_read", True))
        self.assertEqual({k: v["count"] for k, v in report["items"].items()},
                         {"money": 17678700, "jewel": 48157, "stamina": 300, "fes": 20})
        reading, = [row for row in summary["readings"] if row["kind"] == "resources"]
        self.assertEqual(reading["readings"]["items"]["fes"]["count"], 20)

    def test_misread_counter_fails_and_keeps_the_clean_ones(self):
        summary = self.run_offline(["ResourceRead"], [(HOME_SHOP, "click")])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_clicks"]), ("failed", []))
        self.assertEqual(task["fail_reasons"], ["resource_read_unreadable"])
        report, = [r for r in task["reports"] if r["kind"] == "resources"]
        self.assertEqual((report["ok"], report["missing"], report["frames"]), (False, ["fes"], 3))
        self.assertEqual(report["items"]["money"]["count"], 17678492)

    def test_not_home_fails_without_input(self):
        summary = self.run_offline(["ResourceRead"], [(FES_MAIN, "click")])
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_clicks"]), ("failed", []))
        self.assertEqual(task["fail_reasons"], ["resource_read_not_home"])
        self.assertFalse([r for r in task["reports"] if r["kind"] == "resources"])


if __name__ == "__main__":
    unittest.main()
