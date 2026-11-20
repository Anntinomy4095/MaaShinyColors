"""Page-guard near-miss diagnosis (2026-10-22 shop プレミアム layout failure) and the launcher failure snapshot."""
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import guard_diagnosis as gd
from agent.core.task_actions import ReportSink
from tests.maa_probe import NodeProbe, maa_ready
from tests.test_task_launcher import FakeJob, FakeTasker
from tools import run_tasks as launcher

PRIVATE = ROOT / "dev_assets" / "private"
SALE = "dev_assets/private/shop-20261022-sale/premium.png"
SYN = PRIVATE / "synthetic-guard-diagnosis"


class GuardSetTests(unittest.TestCase):
    def test_page_guards_are_named_and_without_custom_steps(self):
        nodes = gd.pipeline_nodes()
        guards = gd.page_guards(nodes)
        self.assertEqual(guards["SC.Shop.Premium.Guard"],
                         ["SC.Shop.Anchor.Premium.Title", "SC.Shop.Anchor.Premium.PremiumSelected",
                          "SC.Shop.Anchor.Premium.MoneyIdle", "SC.Shop.Anchor.Premium.LegalNotice"])
        self.assertIn("SC.Nav.ShopPremium.Guard", guards)
        self.assertTrue(all(name.endswith(".Guard") and len(parts) >= gd.MIN_ANCHORS for name, parts in guards.items()))
        for parts in guards.values():  # a Custom step needs its runtime: never part of a one-frame diagnosis
            self.assertTrue(all(nodes[p].get("recognition") != "Custom" for p in parts))

    def test_summary_lines(self):
        report = {"near_miss": [{"guard": "SC.Shop.Premium.Guard", "anchor": "SC.Shop.Anchor.Premium.PremiumSelected",
                                 "score": 0.91, "threshold": 0.95},
                                {"guard": "G", "anchor": "A", "score": None, "threshold": None}]}
        self.assertEqual(gd.summary_lines(report),
                         ["near_miss:SC.Shop.Premium.Guard/SC.Shop.Anchor.Premium.PremiumSelected@0.91", "near_miss:G/A"])

    def test_layout_hint_only_on_an_unrecognised_page_with_a_close_template_anchor(self):
        near = {"guard": "SC.Shop.Premium.Guard", "anchor": "SC.Shop.Anchor.Premium.PremiumSelected", "score": 0.91,
                "threshold": 0.95}
        self.assertEqual(gd.layout_hint({"hits": [], "near_miss": [near]}),
                         {"page": "Shop.Premium", "anchor": near["anchor"], "score": 0.91})
        self.assertIsNone(gd.layout_hint({"hits": ["SC.Common.Home.Guard"], "near_miss": [near]}))  # a known page
        self.assertIsNone(gd.layout_hint({"hits": [], "near_miss": [{**near, "score": 0.4}]}))
        self.assertIsNone(gd.layout_hint({"hits": [], "near_miss": [{**near, "score": None}]}))  # OCR/colour miss
        self.assertIsNone(gd.layout_hint(None))


class FailureSnapshotHookTests(unittest.TestCase):
    """The launcher runs one read-only snapshot job after a failed task and attaches its near misses."""

    class SnapshotTasker(FakeTasker):
        def __init__(self, outcomes, sink):
            super().__init__(outcomes)
            self.sink = sink

        def post_task(self, entry, override):
            if entry == "SC.Diagnostics.FailureSnapshot":
                self.posted.append((entry, override))
                self.sink.add("failure_snapshot", {"ok": True, "hits": [], "screenshot": None, "near_miss": [
                    {"guard": "SC.Shop.Premium.Guard", "anchor": "SC.Shop.Anchor.Premium.PremiumSelected",
                     "score": 0.91, "threshold": 0.95}]})
                return FakeJob(True)
            return super().post_task(entry, override)

    def plans(self, *entries):
        return [launcher.TaskPlan(entry.split(".")[1], entry, {}) for entry in entries]

    def test_only_failed_tasks_get_a_snapshot_and_only_when_enabled(self):
        sink = ReportSink()
        tasker = self.SnapshotTasker({"SC.A.Entry": "ok", "SC.B.Entry": "fail", "SC.C.Entry": "ok"}, sink)
        results = launcher.execute_plan(self.plans("SC.A.Entry", "SC.B.Entry", "SC.C.Entry"), tasker, sink=sink,
                                        failure_snapshot=True)
        self.assertEqual([e for e, _ in tasker.posted],
                         ["SC.A.Entry", "SC.B.Entry", "SC.Diagnostics.FailureSnapshot", "SC.C.Entry"])
        self.assertEqual(tasker.posted[2][1]["SC.Diagnostics.FailureSnapshot"]["custom_action_param"], {"task": "B"})
        failed = results[1]
        self.assertEqual(failed["failure_snapshot"]["near_miss"][0]["guard"], "SC.Shop.Premium.Guard")
        self.assertIn("near_miss:SC.Shop.Premium.Guard/SC.Shop.Anchor.Premium.PremiumSelected@0.91",
                      failed["fail_reasons"])
        self.assertEqual(failed["layout_hint"]["page"], "Shop.Premium")
        self.assertNotIn("failure_snapshot", results[0])
        self.assertEqual(results[2]["reports"], [])  # the snapshot report never leaks into the next task
        # Default (offline replays, legacy callers): no extra job, frame order unchanged.
        tasker = self.SnapshotTasker({"SC.B.Entry": "fail"}, sink)
        launcher.execute_plan(self.plans("SC.B.Entry"), tasker, sink=sink)
        self.assertEqual([e for e, _ in tasker.posted], ["SC.B.Entry"])

    def test_snapshot_node_is_read_only(self):
        nodes = gd.pipeline_nodes()
        node = nodes["SC.Diagnostics.FailureSnapshot"]
        self.assertEqual((node["recognition"], node["action"], node["custom_action"]),
                         ("DirectHit", "Custom", "SC.Diagnostics.FailureSnapshot"))
        self.assertNotIn("next", node)


@unittest.skipUnless(maa_ready(SALE), "需要 MaaFramework、私有模板包与 2026-10-22 商店原生帧")
class NativeDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from PIL import Image
        SYN.mkdir(parents=True, exist_ok=True)
        frame = np.array(Image.open(ROOT / SALE).convert("RGB"))
        frame[96:132, 20:148] = frame[150, 30]  # the selected-tab anchor area filled with the page background
        Image.fromarray(frame).save(SYN / "premium-tab-erased.png")

    def diagnose(self, frame):
        from maa.custom_recognition import CustomRecognition
        out = {}
        nodes = gd.pipeline_nodes()
        guards = gd.page_guards(nodes)

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                out.update(gd.evaluate(context, argv.image, nodes, guards))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="")  # hit: evaluate once

        probe = NodeProbe()
        probe.resource().register_custom_recognition("SC.Test.GuardDiagnosis", Probe())
        probe.run(frame, {"SC.Test.Start": {"recognition": "DirectHit", "next": ["SC.Test.Diagnose"]},
                          "SC.Test.Diagnose": {"recognition": "Custom", "custom_recognition": "SC.Test.GuardDiagnosis"}})
        return out

    def test_sale_premium_page_hits_its_guards(self):
        report = self.diagnose(SALE)
        self.assertTrue(report["complete"])
        self.assertIn("SC.Shop.Premium.Guard", report["hits"])
        self.assertIn("SC.Nav.ShopPremium.Guard", report["hits"])
        self.assertNotIn("SC.Common.Home.Guard", report["hits"])

    def test_launcher_snapshot_job_reports_the_near_miss_without_input(self):
        """The real registered action on a replay controller: screenshot, diagnosis, report; no click, no frame saved."""
        sink = ReportSink()
        images, advances = launcher.load_frames([(SYN / "premium-tab-erased.png", 1)])
        scenario = launcher.ReplayScenario(images, advances, allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), sink, save_diagnostics=False)
        report = launcher.run_failure_snapshot(tasker, sink, "Shop")
        self.assertTrue(report["ok"], report)
        self.assertEqual((report["task"], report["screenshot"]), ("Shop", None))
        self.assertIn("near_miss:SC.Shop.Premium.Guard/SC.Shop.Anchor.Premium.PremiumSelected",
                      " ".join(launcher.guard_summary_lines(report)))
        self.assertEqual((scenario.clicks, scenario.other_calls), ([], 0))

    def test_one_failing_anchor_is_named_as_a_near_miss(self):
        report = self.diagnose(str((SYN / "premium-tab-erased.png").relative_to(ROOT)))
        self.assertNotIn("SC.Shop.Premium.Guard", report["hits"])
        near = {(n["guard"], n["anchor"]) for n in report["near_miss"]}
        self.assertIn(("SC.Shop.Premium.Guard", "SC.Shop.Anchor.Premium.PremiumSelected"), near)
        item = next(n for n in report["near_miss"] if n["guard"] == "SC.Shop.Premium.Guard")
        self.assertLess(item["score"], item["threshold"])


if __name__ == "__main__":
    unittest.main()
