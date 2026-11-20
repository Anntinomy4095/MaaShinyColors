"""2026-11-22 live: a Collabo LIVE REPORT left from an earlier run stopped Startup (unknown_or_unhandled_state).
SC.Startup.CollaboReport.Close closes it with its own Startup action; a Grade/Match LIVE REPORT is never matched."""
import json
import unittest

from agent.core.pipeline_actions import default_table
from tests.maa_probe import NodeProbe, ROOT, maa_ready

NODE = "SC.Startup.CollaboReport.Close"
COLLABO = "dev_assets/private/run_logs/diagnostics/failures/20261122T091907323500Z.png"
GRADE = "dev_assets/private/grade-live-auto-20261003-frames/report.png"


class StartupCollaboReportTests(unittest.TestCase):
    def test_bound_to_its_own_startup_action_in_the_home_loop(self):
        action = default_table().get("startup-collabo-report-ok")
        self.assertEqual((action.family, action.consuming, action.nodes), ("startup", False, (NODE,)))
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/startup.json").read_text(encoding="utf-8"))
        self.assertIn("[JumpBack]" + NODE, pipeline["SC.Startup.GoHome"]["next"])
        self.assertEqual(pipeline[NODE]["target"], list(action.rect))
        self.assertIs(pipeline[NODE]["enabled"], False)

    @unittest.skipUnless(maa_ready(COLLABO, GRADE), "private frames / Maa not available")
    def test_collabo_report_only(self):
        probe = NodeProbe()
        self.assertTrue(probe.hits(COLLABO, NODE))
        self.assertFalse(probe.hits(GRADE, NODE))


if __name__ == "__main__":
    unittest.main()
