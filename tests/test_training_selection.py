import unittest
from unittest.mock import Mock

from agent.core.training_flow import ANCHORS, PREFIX
from tools.run_training_selection import guarded_select, post_verified


class TrainingSelectionTests(unittest.TestCase):
    def test_lost_response_does_not_repeat_selection(self):
        report = {'input_attempts': 0}
        send = Mock(side_effect=TimeoutError('lost'))
        guard = Mock(return_value={'nonce': 'a' * 32, 'frame_sha256': 'b' * 64})
        checkpoint = Mock()
        self.assertFalse(guarded_select(report, True, (461, 236), guard, send, checkpoint))
        self.assertFalse(guarded_select(report, True, (461, 236), guard, send, checkpoint))
        self.assertEqual(send.call_count, 1)
        checkpoint.assert_called_once()
        self.assertEqual(report['input_attempts'], 1)

    def test_disabled_arbitrary_point_and_stale_guard_send_nothing(self):
        send = Mock()
        for execute, point in ((False, (461, 236)), (1, (461, 236)), (True, (640, 510))):
            self.assertFalse(guarded_select({'input_attempts': 0}, execute, point, Mock(), send, Mock()))
        self.assertFalse(guarded_select({'input_attempts': 0}, True, (461, 236),
                         Mock(side_effect=ValueError('stale')), send, Mock()))
        send.assert_not_called()

    def test_select_ack_cannot_substitute_for_pending_postcondition(self):
        for state, attempted in (('confirmed', True), ('ready', False), ('blocked', False)):
            response = {'ok': True, 'attempted': attempted, 'reason': 'test', 'state': state,
                        'attempts': 2, 'max_attempts': 4}
            self.assertFalse(guarded_select({'input_attempts': 0}, True, (461, 236),
                lambda: {'nonce': 'a' * 32, 'frame_sha256': 'b' * 64}, lambda request: response, Mock()))

    def test_postcondition_requires_complete_card_and_zero_materials_without_overlay(self):
        hits = [{'scene': PREFIX + 'Zero.' + name, 'hit': True, 'score': .99, 'roi': roi,
                 'raw_detail': {'filtered': [{'box': roi, 'score': .99}]}}
                for name, roi in ANCHORS['Zero'].items()]
        clean = {'state': 'no_known_popup', 'complete_contracts': [], 'partial_contracts': []}
        self.assertTrue(post_verified(hits, clean))
        self.assertFalse(post_verified(hits[:-1], clean))
        self.assertFalse(post_verified(hits, {**clean, 'partial_contracts': ['unknown']}))
        self.assertFalse(post_verified(hits, {**clean, 'state': 'known_popup'}))
        lowered = [{**hit, 'score': .94} if hit['scene'].endswith('BronzeZero') else hit for hit in hits]
        self.assertFalse(post_verified(lowered, clean))


if __name__ == '__main__':
    unittest.main()
