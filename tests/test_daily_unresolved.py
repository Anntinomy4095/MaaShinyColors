"""Daily task retries keep unresolved spends visible (noted, the screen decides; never a block);
reports retain post-skip rights."""
import unittest
from unittest.mock import Mock

from agent.core import match_custom as mc, match_task as mt
from agent.core import report_readings as rr
from agent.core import training_custom as tr, training_task as tt


class UnresolvedSpendTests(unittest.TestCase):
    def training(self, events):
        runtime = tr.TrainingRuntime(authority=Mock())
        runtime.log = Mock()
        runtime.log.events.return_value = events
        runtime.run = tt.TrainingRun(tt.TrainingConfig(), runtime.log)
        runtime.started(runtime.run)
        return runtime

    def test_training_incomplete_prior_intent_is_noted_and_the_run_goes_on(self):
        runtime = self.training([{"event": "intent"}])
        self.assertIsNone(runtime.run.outcome)
        self.assertEqual((runtime.run.phase, runtime.run.notes), ("active", ["uncertain_spend_earlier_today"]))
        self.assertFalse(runtime.finish_action(None, {}))   # nothing submitted in this run: no grant
        runtime.authority.authorize.assert_not_called()

    def test_training_prior_intent_without_result_dialog_starts_afresh(self):
        runtime = self.training([{"event": "intent", "inventory": 100, "level": 2, "remaining_exp": 40}])
        self.assertEqual(runtime.run.phase, "resolving")
        self.assertFalse(runtime.extra_state("resolve_elsewhere", None, None, {}))   # not a match: the flow goes on
        self.assertIsNone(runtime.run.outcome)
        self.assertEqual((runtime.run.phase, runtime.run.intent, runtime.run.baseline), ("active", None, None))
        self.assertIn("uncertain_spend_earlier_today", runtime.run.notes)
        self.assertFalse(runtime.finish_action(None, {}))
        runtime.log.append.assert_not_called()
        runtime.authority.authorize.assert_not_called()

    def test_training_unknown_prior_result_is_not_a_success(self):
        runtime = self.training([{"event": "intent", "inventory": 100, "level": 2, "remaining_exp": 40}])
        runtime.result_title = Mock(return_value=("unknown", None))
        for _ in range(tt.MAX_UNREADABLE_FRAMES + 1):
            self.assertFalse(runtime.check_result(None, None))
        self.assertEqual(runtime.run.outcome, "spend_uncertain")
        self.assertFalse(runtime.finish_action(None, {}))
        runtime.log.append.assert_not_called()

    def test_match_prior_intent_is_noted_and_the_run_goes_on(self):
        runtime = mc.MatchRuntime(authority=Mock())
        runtime.log = Mock()
        runtime.log.events.return_value = [{"event": "intent", "time": "prior-run"}]
        runtime.run = mt.MatchRun(mt.MatchConfig(stage="A1-S1", sample_only=False), runtime.log)
        runtime.started(runtime.run)
        self.assertIsNone(runtime.run.outcome)
        self.assertIn("unresolved_skip_batches_1", runtime.run.notes)
        self.assertFalse(runtime.finish_action(None, {}))
        runtime.authority.authorize.assert_not_called()

    def test_verified_match_report_uses_the_post_skip_rights(self):
        vision = Mock()
        vision.hit.return_value = True
        vision.number.return_value = "18/20"
        runtime = mc.MatchRuntime(vision=lambda context: vision, clock=lambda: 0)
        runtime.log = Mock()
        runtime.run = mt.MatchRun(mt.MatchConfig(count=2, count_mode="fixed", sample_only=False), runtime.log)
        run = runtime.run
        run.phase, run.rights = "awaiting_result", (20, 20)
        run.intent = {"rights_before": [2, 20], "time": "current-run"}
        run.note({"stage": "MatchMain", "rights": "20/20"})
        run.saver = Mock(saved=[])
        self.assertTrue(runtime.watch(None, None, {}))
        self.assertEqual((run.outcome, run.rights), ("skipped", (18, 20)))
        self.assertEqual(rr.match(run.summary()), [
            ("resource.fes_rights", {"count": 18, "max": 20}, 1000)])

    def test_match_denied_before_send_does_not_block_the_next_run(self):
        runtime = mc.MatchRuntime(authority=Mock())
        runtime.log = Mock()
        runtime.log.events.return_value = [
            {"event": "intent", "time": "prior-run"},
            {"event": "refused", "time": "prior-run", "reason": "click_denied_before_send"}]
        runtime.run = mt.MatchRun(mt.MatchConfig(stage="A1-S1", sample_only=False), runtime.log)
        runtime.started(runtime.run)
        self.assertIsNone(runtime.run.outcome)
        runtime.authority.authorize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
