"""Grade 参加ユニット numeric misses must retry on the same page without navigating or granting input."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent.core import grade_task as gt
from agent.core.grade_custom import GradeRuntime

ROOT = Path(__file__).resolve().parents[1]


class GradeUnitRetryTests(unittest.TestCase):
    def runtime(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private")
        self.addCleanup(directory.cleanup)
        runtime = GradeRuntime(log_root=directory.name)
        runtime.run = gt.GradeRun(gt.GradeConfig(), runtime.log)
        runtime.run.phase = "difficulty_tapped"
        runtime.run.rights = (18, 20)
        runtime.authority = Mock(can_spend=Mock(return_value=True), authorize=Mock(return_value=None))
        runtime.vision = lambda context: Mock(text=Mock(side_effect=["参加ユニット(Normal)",
                                                                    gt.NO_RETURN + gt.ONE_RIGHT, "21801"]))
        return runtime

    def test_transient_numeric_miss_waits_then_grants_only_after_fresh_rights(self):
        runtime = self.runtime()
        runtime.rights = Mock(side_effect=[(None, None), ("18/20", (18, 20))])
        self.assertFalse(runtime.check_unit(None, object()))
        self.assertEqual(runtime.run.phase, "difficulty_tapped")
        self.assertTrue(runtime.extra_state("unit_wait", None, None, {}))
        self.assertFalse(runtime.extra_state("unit_cancel", None, None, {}))
        runtime.authority.authorize.assert_not_called()
        self.assertEqual(runtime.log.events(), [])
        self.assertTrue(runtime.check_unit(None, object()))
        self.assertEqual(runtime.run.phase, "tension_tapped")
        self.assertFalse(runtime.extra_state("unit_wait", None, None, {}))
        runtime.authority.authorize.assert_called_once()

    def test_persistent_miss_uses_existing_bound_then_cancels_without_grant(self):
        runtime = self.runtime()
        runtime.rights = Mock(return_value=(None, None))
        for _ in range(gt.MAX_UNREADABLE_FRAMES):
            self.assertFalse(runtime.check_unit(None, object()))
            self.assertTrue(runtime.extra_state("unit_wait", None, None, {}))
        self.assertFalse(runtime.check_unit(None, object()))
        self.assertFalse(runtime.extra_state("unit_wait", None, None, {}))
        self.assertTrue(runtime.extra_state("unit_cancel", None, None, {}))
        self.assertEqual(runtime.run.outcome, "unit_mismatch")
        self.assertIn("rights_unreadable", runtime.run.notes)
        runtime.authority.authorize.assert_not_called()

    def test_pipeline_wait_precedes_back_and_has_no_input(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/grade.json").read_text(encoding="utf-8"))
        successors = nodes["SC.Grade.Main"]["next"]
        self.assertLess(successors.index("[JumpBack]SC.Grade.Unit.Confirm"),
                        successors.index("[JumpBack]SC.Grade.Unit.Wait"))
        self.assertLess(successors.index("[JumpBack]SC.Grade.Unit.Wait"),
                        successors.index("[JumpBack]SC.Grade.Unit.Back"))
        wait = nodes["SC.Grade.Unit.Wait"]
        self.assertNotIn("action", wait)
        self.assertEqual(wait["all_of"], ["SC.Grade.Unit.Guard", "SC.Grade.State.UnitWait"])

    def test_live_start_grant_refusal_is_not_reported_as_wrong_team(self):
        runtime = self.runtime()
        runtime.run.phase = "tension_decided"
        runtime.run.confirm_rejected = ["spend_grant_refused:该消费动作本次运行授权已用尽"]
        self.assertTrue(runtime.extra_state("tension_hold", None, None, {}))
        self.assertEqual(runtime.run.outcome, "spend_refused")


if __name__ == "__main__":
    unittest.main()
