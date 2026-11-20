"""Frame permits bind one recognition and action attempt to one concrete capture."""
from dataclasses import FrozenInstanceError, replace
import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest

from agent.core.frame_permit import FrameCapture, FramePermitStore


class Image:
    shape = (720, 1280, 3)

    def __init__(self, pixels=b"decoded BGR pixels"):
        self.pixels = pixels

    def tobytes(self):
        return self.pixels


class FramePermitTests(unittest.TestCase):
    NODE, ACTION = "SC.Test.Click", "test-click"

    def setUp(self):
        self.now = 100.0
        self.image = Image()
        self.frame = FrameCapture("ab" * 32, 100.0, self.image.shape,
                                  hashlib.sha256(self.image.tobytes()).hexdigest(), 1, 1, 1)
        self.current = self.frame
        self.table = SimpleNamespace(node_actions=lambda: {self.NODE: self.ACTION, "SC.Other.Click": self.ACTION})
        self.store = FramePermitStore(enabled=True, table=self.table, current_frame=lambda: self.current,
                                      clock=lambda: self.now)

    def issue(self, **changes):
        args = dict(node_name=self.NODE, action=self.ACTION, task_id=1, reco_id=2,
                    image=self.image, frame=self.frame)
        args.update(changes)
        return self.store.issue(**args)

    def arm(self, issued_token, **changes):
        args = dict(token=issued_token, task_id=1, reco_id=2, node_name=self.NODE)
        args.update(changes)
        return self.store.arm(**args)

    def test_defaults_disabled_and_records_are_frozen(self):
        store = FramePermitStore(table=self.table, current_frame=lambda: self.current, clock=lambda: self.now)
        self.assertIsNone(store.issue(self.NODE, self.ACTION, 1, 2, self.image, self.frame))
        self.assertEqual(store.refusal(None, self.ACTION), "pipeline_frame_guard_disabled")
        with self.assertRaises(FrozenInstanceError):
            self.frame.capture_seq = 2

    def test_issue_arm_take_is_one_shot_and_bound_to_exact_record(self):
        token = self.issue()
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertTrue(self.arm(token))
        permit = self.store.take()
        self.assertEqual((permit.node_name, permit.action, permit.task_id, permit.reco_id, permit.frame),
                         (self.NODE, self.ACTION, 1, 2, self.frame))
        self.assertIsNone(self.store.refusal(permit, self.ACTION))
        self.assertEqual(self.store.refusal(permit, "other"), "pipeline_frame_guard_action_mismatch")
        self.assertIsNone(self.store.take())
        self.assertFalse(self.arm(token))

    def test_attempt_before_arm_destroys_candidate(self):
        token = self.issue()
        self.assertIsNone(self.store.take())
        self.assertFalse(self.arm(token))

    def test_wrong_image_shape_pixels_node_action_or_ids_cannot_issue(self):
        small = Image()
        small.shape = (1, 1, 3)
        for changes in ({"image": Image(b"wrong")}, {"image": small}, {"node_name": "SC.Unknown"},
                        {"action": "other"}, {"task_id": True}, {"task_id": 0}, {"reco_id": False},
                        {"reco_id": -1}):
            with self.subTest(changes=changes):
                token = self.issue()
                self.assertIsNone(self.issue(**changes))
                self.assertFalse(self.arm(token))

    def test_wrong_arm_identity_clears_candidate_and_armed(self):
        for changes in ({"token": "wrong"}, {"task_id": 2}, {"task_id": True}, {"reco_id": 3},
                        {"reco_id": True}, {"node_name": "SC.Other.Click"}):
            with self.subTest(changes=changes):
                token = self.issue()
                self.assertTrue(self.arm(token))
                self.assertFalse(self.arm(token, **changes))
                self.assertIsNone(self.store.take())
                self.assertFalse(self.arm(token))

    def test_new_capture_even_with_same_pixels_invalidates_permit(self):
        for field in ("capture_seq", "host_capture_seq", "host_binding_seq"):
            with self.subTest(field=field):
                self.current = self.frame
                token = self.issue()
                self.assertTrue(self.arm(token))
                self.current = replace(self.frame, **{field: 2})
                self.assertEqual(self.store.refusal(self.store.take(), self.ACTION),
                                 "pipeline_frame_guard_frame_mismatch")
                self.assertIsNone(self.issue())
                self.assertFalse(self.arm(token))

    def test_png_digest_and_capture_time_are_part_of_concrete_frame(self):
        for changes in ({"sha256": "cd" * 32}, {"captured_at": 99.0},
                        {"pixel_digest": "cd" * 32}):
            with self.subTest(changes=changes):
                self.current = self.frame
                token = self.issue()
                self.current = replace(self.frame, **changes)
                self.assertFalse(self.arm(token))
                self.assertIsNone(self.store.take())

    def test_unknown_or_invalid_host_sequences_cannot_issue(self):
        for field in ("capture_seq", "host_capture_seq", "host_binding_seq"):
            for value in (None, 0, -1, True, 1.0):
                with self.subTest(field=field, value=value):
                    self.frame = replace(self.frame, **{field: value})
                    self.current = self.frame
                    self.assertIsNone(self.issue())
                    self.frame = replace(self.frame, **{field: 1})

    def test_noncanonical_or_missing_digests_are_rejected(self):
        for changes in ({"shape": (1280, 720, 3)}, {"shape": [720, 1280, 3]},
                        {"pixel_digest": None}, {"sha256": "bad"}):
            with self.subTest(changes=changes):
                frame = replace(self.frame, **changes)
                self.current = frame
                self.assertIsNone(self.issue(frame=frame))

    def test_current_record_cannot_match_through_bool_integer_equality(self):
        for field in ("capture_seq", "host_capture_seq", "host_binding_seq"):
            with self.subTest(field=field):
                self.current = replace(self.frame, **{field: True})
                self.assertIsNone(self.issue())

    def test_failed_frame_reader_never_mints_or_arms(self):
        token = self.issue()

        def failed_reader():
            raise ValueError("capture unavailable")

        self.store.current_frame = failed_reader
        self.assertFalse(self.arm(token))
        self.assertIsNone(self.issue())
        self.assertIsNone(self.store.take())

    def test_capture_and_issue_time_must_be_finite_past_and_fresh(self):
        for value in (94.9, 100.1, float("nan"), float("inf"), True):
            with self.subTest(captured_at=value):
                frame = replace(self.frame, captured_at=value)
                self.current = frame
                self.assertIsNone(self.issue(frame=frame))
        self.current = self.frame
        for value in (float("nan"), float("inf"), True, 99.9, 105.1):
            with self.subTest(now=value):
                self.now = 100.0
                token = self.issue()
                self.now = value
                self.assertFalse(self.arm(token))
                self.assertIsNone(self.store.take())

    def test_time_limit_boundary_and_post_take_expiration(self):
        token = self.issue()
        self.now = 105.0
        self.assertTrue(self.arm(token))
        permit = self.store.take()
        self.assertIsNone(self.store.refusal(permit, self.ACTION))
        self.now = 105.001
        self.assertEqual(self.store.refusal(permit, self.ACTION), "pipeline_frame_guard_stale")

    def test_reset_epoch_invalidates_already_taken_permit(self):
        token = self.issue()
        self.assertTrue(self.arm(token))
        permit = self.store.take()
        self.store.reset_task()
        self.assertEqual(self.store.epoch, 1)
        self.assertEqual(self.store.refusal(permit, self.ACTION), "pipeline_frame_guard_epoch_mismatch")
        self.assertFalse(self.arm(token))

    def test_invalidate_cannot_resurrect_candidate_or_armed_token(self):
        for armed in (False, True):
            token = self.issue()
            if armed:
                self.assertTrue(self.arm(token))
            self.store.invalidate("capture_failed")
            self.assertIsNone(self.store.take())
            self.assertFalse(self.arm(token))

    def test_new_issue_replaces_old_token(self):
        old = self.issue()
        new = self.issue()
        self.assertNotEqual(old, new)
        self.assertFalse(self.arm(old))
        self.assertFalse(self.arm(new))  # wrong-token attempt consumed the candidate too

    def test_concurrent_attempts_take_only_one_permit(self):
        token = self.issue()
        self.assertTrue(self.arm(token))
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.store.take(), range(8)))
        self.assertEqual(sum(permit is not None for permit in results), 1)


if __name__ == "__main__":
    unittest.main()
