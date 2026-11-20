"""Synthetic evidence only: no Maa/host import, screenshot, input or runtime authorization."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest

from agent.core.frame_permit import FrameCapture
from agent.core import mission_claim as mc
from agent.core.mission_status import classify


CYCLE = "offline-day-A"
MISSION = "offline-login-mission"


def capture(seq=1, *, at=None, sha=None):
    return FrameCapture(sha or f"{seq:064x}", 99.0 + seq if at is None else at, (720, 1280, 3),
                        f"{seq + 100:064x}", seq, seq, seq * 2)


def row(identity=MISSION, *, state="claimable", progress=(1, 1)):
    return mc.ClaimRow(identity, progress, state)


def target(identity=MISSION, **changes):
    return replace(mc.ClaimTarget(identity, "single_daily", (1000, 250, 120, 45), True, True), **changes)


def observation(*, frame=None, rows=None, targets=None, **changes):
    return replace(mc.ClaimObservation(CYCLE, frame or capture(), "daily_missions", True, True,
                                       (row(),) if rows is None else rows,
                                       (target(),) if targets is None else targets), **changes)


def receipt(*, frame=None, **changes):
    return replace(mc.ClaimReceipt(CYCLE, MISSION, frame or capture(2), "daily_mission_reward", True, True, True), **changes)


def terminal(*, frame=None, identity=MISSION, **changes):
    return observation(frame=frame or capture(3), rows=(row(identity, state="claimed"),), targets=(), **changes)


class MissionClaimEvidenceTests(unittest.TestCase):
    def inspect(self, view=None, *, frame=None, now=100.0, mission_id=MISSION):
        view = view or observation()
        return mc.inspect_claim(view, mission_id, CYCLE, frame=frame or view.frame, now=now)

    def test_default_contract_cannot_reserve_a_valid_candidate(self):
        tracker = mc.ClaimTracker(CYCLE)
        decision = tracker.begin(observation(), MISSION, frame=capture(), now=100.0)
        self.assertEqual(decision.state, "disabled")
        self.assertFalse(decision.input_authorized)
        self.assertEqual((tracker.attempts, tracker.history), (0, ()))
        candidate = self.inspect()
        self.assertEqual(candidate.state, "candidate")
        self.assertFalse(candidate.input_authorized)

    def test_progress_complete_or_missing_button_does_not_prove_reward_state(self):
        self.assertEqual(classify(1, 1, None), "completed")
        with self.assertRaises(ValueError):
            row(state="completed")
        for progress in ((1, 1), None, (9, 1), (0, 0)):
            with self.subTest(progress=progress):
                view = observation(rows=(row(state="unknown", progress=progress),), targets=())
                self.assertEqual(self.inspect(view).state, "stopped")

    def test_positive_claimed_marker_is_read_only_and_enabled_button_conflicts(self):
        done = terminal(frame=capture())
        self.assertEqual(self.inspect(done).state, "already_claimed")
        conflict = replace(done, targets=(target(),))
        self.assertEqual(self.inspect(conflict).reason, "claim_terminal_conflict")
        done = replace(done, targets=(target(enabled=False),))
        self.assertEqual(self.inspect(done).state, "already_claimed")

    def test_only_complete_claimable_progress_is_a_candidate(self):
        for progress in (None, (0, 1), (2, 1), (0, 0)):
            with self.subTest(progress=progress):
                self.assertEqual(self.inspect(observation(rows=(row(progress=progress),))).state, "stopped")
        incomplete = observation(rows=(row(state="incomplete", progress=(0, 1)),), targets=())
        self.assertEqual(self.inspect(incomplete).reason, "claim_mission_incomplete")

    def test_page_cycle_overlay_and_missing_identity_stop_before_intent(self):
        cases = [(observation(cycle_id="offline-day-B"), "claim_cycle_changed"),
                 (observation(page="weekly_missions"), "claim_daily_page_unverified"),
                 (observation(guard_verified=False), "claim_daily_page_unverified"),
                 (observation(overlays_clear=False), "claim_overlay_present"),
                 (observation(rows=()), "claim_mission_missing")]
        for view, reason in cases:
            tracker = mc.ClaimTracker(CYCLE, enabled=True)
            with self.subTest(reason=reason):
                self.assertEqual(tracker.begin(view, MISSION, frame=view.frame, now=100).reason, reason)
                self.assertEqual(tracker.attempts, 0)
        self.assertEqual(self.inspect(mission_id="").reason, "claim_identity_missing")
        self.assertEqual(mc.inspect_claim(None, MISSION, CYCLE, frame=capture(), now=100).reason,
                         "claim_observation_missing")

    def test_duplicate_rows_buttons_and_overlapping_other_identity_are_ambiguous(self):
        for view in (observation(rows=(row(), row())), observation(targets=(target(), target())),
                     observation(targets=(target(), target("offline-other-mission")))):
            with self.subTest(view=view):
                self.assertEqual(self.inspect(view).state, "stopped")
        reordered = observation(rows=(row("offline-other-mission", state="unknown", progress=None), row()),
                                targets=(target("offline-other-mission", rect=(1000, 350, 120, 45)), target()))
        self.assertEqual(self.inspect(reordered).target.mission_id, MISSION)

    def test_disabled_unknown_cost_missing_or_bulk_targets_do_not_reserve(self):
        for candidate in (target(enabled=False), target(no_cost_verified=False), target(scope="all_daily"),
                          target(scope="weekly")):
            with self.subTest(target=candidate):
                self.assertEqual(self.inspect(observation(targets=(candidate,))).state, "stopped")
        self.assertEqual(self.inspect(observation(targets=())).reason, "claim_target_missing")

    def test_target_geometry_must_fit_the_entire_canonical_canvas(self):
        for rect in ((-1, 250, 120, 45), (1000, 250, 0, 45), (1200, 250, 120, 45),
                     (1000, 700, 120, 45), (1000, -1, 120, 45)):
            with self.subTest(rect=rect):
                self.assertEqual(self.inspect(observation(targets=(target(rect=rect),))).reason,
                                 "claim_target_geometry_invalid")
        for rect in ([1000, 250, 120, 45], (True, 250, 120, 45), (1000.0, 250, 120, 45)):
            with self.subTest(rect=rect), self.assertRaises(ValueError):
                target(rect=rect)

    def test_stale_future_nonfinite_or_overflow_clock_cannot_make_candidate(self):
        self.assertEqual(self.inspect(now=105).state, "candidate")
        for now in (105.001, 99, float("inf"), float("nan"), True, 10**500, "100"):
            with self.subTest(now=repr(now)):
                self.assertEqual(self.inspect(now=now).reason, "claim_frame_stale")

    def test_invalid_sequences_digests_shape_and_equal_boolean_current_values_stop(self):
        for key in ("capture_seq", "host_capture_seq", "host_binding_seq"):
            for value in (True, 0, -1, 1.0, 2**63, None):
                invalid = replace(capture(), **{key: value})
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.inspect(observation(frame=invalid)).reason, "claim_frame_invalid")
        for key, value in (("sha256", "a" * 63), ("pixel_digest", None), ("shape", (394, 700, 3)),
                           ("captured_at", float("nan"))):
            self.assertEqual(self.inspect(observation(frame=replace(capture(), **{key: value}))).state, "stopped")
        for changed in (replace(capture(), host_capture_seq=True), replace(capture(), capture_seq=1.0)):
            self.assertEqual(changed, capture())  # Python equality alone is insufficient.
            self.assertEqual(self.inspect(frame=changed).reason, "claim_frame_invalid")

    def test_same_png_recapture_does_not_rebase_original_candidate(self):
        original = capture()
        later = replace(capture(2), sha256=original.sha256, pixel_digest=original.pixel_digest)
        tracker = mc.ClaimTracker(CYCLE, enabled=True)
        decision = tracker.begin(observation(frame=original), MISSION, frame=later, now=101)
        self.assertEqual(decision.reason, "claim_frame_changed")
        self.assertEqual(tracker.attempts, 0)

    def test_mutable_evidence_boolean_impostors_and_legacy_states_are_rejected(self):
        for update in ({"rows": [row()]}, {"targets": [target()]}, {"guard_verified": 1}, {"overlays_clear": None}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                observation(**update)
        with self.assertRaises(ValueError):
            row(progress=(True, 1))
        with self.assertRaises(ValueError):
            receipt(scope_verified=1)


class MissionClaimAttemptTests(unittest.TestCase):
    def prepare(self, *, max_attempts=2):
        tracker = mc.ClaimTracker(CYCLE, enabled=True, max_attempts=max_attempts)
        decision = tracker.begin(observation(), MISSION, frame=capture(), now=100)
        self.assertEqual(decision.state, "prepared")
        self.assertFalse(decision.input_authorized)
        self.assertEqual(tracker.history[0].outcome, "pending")
        return tracker, decision.attempt

    def accept(self, tracker, attempt):
        result = tracker.accept_receipt(attempt, receipt(), frame=capture(2), now=101)
        self.assertEqual(result.state, "receipt_observed")
        self.assertFalse(result.input_authorized)

    def assert_halted(self, tracker, expected_outcome="uncertain"):
        self.assertEqual(tracker.attempts, 1)
        self.assertEqual(tracker.history[0].outcome, expected_outcome)
        later = observation(frame=capture(5))
        self.assertEqual(tracker.begin(later, MISSION, frame=later.frame, now=104).state, "stopped")
        self.assertEqual(tracker.attempts, 1)

    def test_one_attempt_is_reserved_before_any_external_sender_and_poll_does_not_repeat(self):
        tracker, attempt = self.prepare()
        self.assertEqual(attempt.frame, capture())
        self.assertEqual(tracker.begin(observation(), MISSION, frame=capture(), now=100).reason, "claim_attempt_pending")
        self.assertEqual(tracker.attempts, 1)

    def test_refused_send_keeps_used_budget_and_stops_later_fresh_observation(self):
        tracker, _ = self.prepare()
        self.assertEqual(tracker.refuse().reason, "claim_refused")
        self.assert_halted(tracker, "refused")

    def test_timeout_error_or_cancellation_keeps_uncertain_record(self):
        for after_receipt in (False, True):
            tracker, attempt = self.prepare()
            if after_receipt:
                self.accept(tracker, attempt)
            with self.subTest(after_receipt=after_receipt):
                self.assertEqual(tracker.interrupt().reason, "claim_result_uncertain")
                self.assert_halted(tracker)
                self.assertEqual(tracker.finish(attempt, terminal(), frame=capture(3), now=102).state, "stopped")

    def test_generic_reward_login_reward_and_mission_completed_are_not_receipts(self):
        for kind in ("generic_reward", "login_reward", "mission_completed"):
            tracker, attempt = self.prepare()
            result = tracker.accept_receipt(attempt, receipt(kind=kind), frame=capture(2), now=101)
            with self.subTest(kind=kind):
                self.assertEqual(result.reason, "claim_receipt_unverified")
                self.assert_halted(tracker)

    def test_receipt_must_bind_original_mission_cycle_and_positive_guards(self):
        cases = [{"mission_id": "offline-other-mission"}, {"cycle_id": "offline-day-B"},
                 {"guard_verified": False}, {"scope_verified": False}, {"unobstructed": False}]
        for update in cases:
            tracker, attempt = self.prepare()
            with self.subTest(update=update):
                self.assertEqual(tracker.accept_receipt(attempt, receipt(**update), frame=capture(2), now=101).state, "stopped")
                self.assert_halted(tracker)

    def test_receipt_must_be_after_attempt_on_its_own_current_capture(self):
        frames = [capture(), replace(capture(2), capture_seq=1), replace(capture(2), host_capture_seq=1),
                  replace(capture(2), host_binding_seq=2)]
        for result_frame in frames:
            tracker, attempt = self.prepare()
            with self.subTest(frame=result_frame):
                result = tracker.accept_receipt(attempt, receipt(frame=result_frame), frame=result_frame, now=101)
                self.assertEqual(result.reason, "claim_receipt_not_after_attempt")
                self.assert_halted(tracker)
        tracker, attempt = self.prepare()
        self.assertEqual(tracker.accept_receipt(attempt, receipt(), frame=capture(3), now=102).reason, "claim_frame_changed")
        self.assert_halted(tracker)

    def test_stale_or_missing_receipt_stops_without_claimed_history(self):
        for proof, now in ((receipt(), 107), (None, 101)):
            tracker, attempt = self.prepare()
            self.assertEqual(tracker.accept_receipt(attempt, proof, frame=capture(2), now=now).state, "stopped")
            self.assert_halted(tracker)
        tracker, attempt = self.prepare()
        self.assertEqual(tracker.finish(attempt, terminal(), frame=capture(3), now=102).reason, "claim_receipt_missing")
        self.assert_halted(tracker)

    def test_receipt_alone_is_pending_not_claimed(self):
        tracker, attempt = self.prepare()
        self.accept(tracker, attempt)
        self.assertEqual(tracker.history[0].outcome, "pending")
        recovered = mc.ClaimTracker(CYCLE, enabled=True, history=tracker.history)
        self.assertEqual(recovered.begin(observation(), MISSION, frame=capture(), now=100).reason,
                         "claim_previous_attempt_unresolved")

    def test_completed_progress_clipped_row_or_enabled_button_cannot_finish(self):
        cases = [
            observation(frame=capture(3), rows=(row(state="unknown"),), targets=()),
            observation(frame=capture(3), rows=(), targets=()),
            terminal(identity="offline-other-mission"),
            replace(terminal(), targets=(target(),)),
            observation(frame=capture(3), rows=(row(state="claimed", progress=(9, 1)),), targets=())]
        for post in cases:
            tracker, attempt = self.prepare()
            self.accept(tracker, attempt)
            with self.subTest(post=post):
                self.assertEqual(tracker.finish(attempt, post, frame=post.frame, now=102).reason, "claim_terminal_unverified")
                self.assert_halted(tracker)

    def test_terminal_cycle_page_overlay_and_duplicate_identity_must_still_be_verified(self):
        for post in (terminal(cycle_id="offline-day-B"), terminal(page="weekly_missions"),
                     terminal(overlays_clear=False), replace(terminal(), rows=(row(state="claimed"), row(state="claimed")))):
            tracker, attempt = self.prepare()
            self.accept(tracker, attempt)
            with self.subTest(post=post):
                self.assertEqual(tracker.finish(attempt, post, frame=post.frame, now=102).state, "stopped")
                self.assert_halted(tracker)

    def test_terminal_must_follow_receipt_and_must_not_rebase_after_capture(self):
        tracker, attempt = self.prepare()
        self.accept(tracker, attempt)
        post = terminal(frame=capture(2))
        self.assertEqual(tracker.finish(attempt, post, frame=post.frame, now=101).reason, "claim_terminal_not_after_receipt")
        self.assert_halted(tracker)
        tracker, attempt = self.prepare()
        self.accept(tracker, attempt)
        self.assertEqual(tracker.finish(attempt, terminal(), frame=capture(4), now=103).reason, "claim_frame_changed")
        self.assert_halted(tracker)

    def test_copied_attempt_cannot_claim_original_pending_identity(self):
        tracker, attempt = self.prepare()
        result = tracker.accept_receipt(replace(attempt), receipt(), frame=capture(2), now=101)
        self.assertEqual(result.reason, "claim_attempt_mismatch")
        self.assert_halted(tracker)

    def test_result_and_reordered_positive_terminal_finish_only_one_selected_mission(self):
        tracker, attempt = self.prepare()
        self.accept(tracker, attempt)
        post = replace(terminal(), rows=(row("offline-other-mission", progress=(0, 1), state="incomplete"),
                                        row(state="claimed")))
        result = tracker.finish(attempt, post, frame=post.frame, now=102)
        self.assertEqual(result.state, "claimed")
        self.assertFalse(result.input_authorized)
        self.assertEqual(tracker.history, (mc.ClaimRecord(CYCLE, MISSION, "claimed"),))
        # A stale-looking claimable marker cannot start a second attempt for the same identity.
        again = observation(frame=capture(4))
        self.assertEqual(tracker.begin(again, MISSION, frame=again.frame, now=103).reason, "claim_attempt_already_used")
        self.assertEqual(tracker.attempts, 1)

    def test_completed_attempt_budget_is_not_refunded_for_another_identity(self):
        tracker, attempt = self.prepare(max_attempts=1)
        self.accept(tracker, attempt)
        tracker.finish(attempt, terminal(), frame=capture(3), now=102)
        next_view = observation(frame=capture(4), rows=(row("offline-other-mission"),),
                                targets=(target("offline-other-mission"),))
        self.assertEqual(tracker.begin(next_view, "offline-other-mission", frame=next_view.frame, now=103).reason,
                         "claim_attempt_budget_exhausted")
        self.assertEqual(tracker.attempts, 1)

    def test_ambiguity_before_intent_stops_task_even_after_new_clean_capture(self):
        tracker = mc.ClaimTracker(CYCLE, enabled=True)
        ambiguous = observation(targets=(target(), target()))
        self.assertEqual(tracker.begin(ambiguous, MISSION, frame=capture(), now=100).reason,
                         "claim_target_ambiguous")
        clean = observation(frame=capture(4))
        self.assertEqual(tracker.begin(clean, MISSION, frame=clean.frame, now=103).reason,
                         "claim_target_ambiguous")
        self.assertEqual(tracker.attempts, 0)

    def test_explicit_budget_allows_distinct_identity_after_proven_first_claim(self):
        tracker, first_attempt = self.prepare()
        self.accept(tracker, first_attempt)
        tracker.finish(first_attempt, terminal(), frame=capture(3), now=102)
        second_id = "offline-other-mission"
        second_view = observation(frame=capture(4), rows=(row(second_id),), targets=(target(second_id),))
        second_attempt = tracker.begin(second_view, second_id, frame=second_view.frame, now=103).attempt
        self.assertIsNotNone(second_attempt)
        second_receipt = receipt(frame=capture(5), mission_id=second_id)
        self.assertEqual(tracker.accept_receipt(second_attempt, second_receipt, frame=second_receipt.frame, now=104).state,
                         "receipt_observed")
        second_terminal = terminal(frame=capture(6), identity=second_id)
        self.assertEqual(tracker.finish(second_attempt, second_terminal, frame=second_terminal.frame, now=105).state,
                         "claimed")
        self.assertEqual(tracker.history, (mc.ClaimRecord(CYCLE, MISSION, "claimed"), mc.ClaimRecord(CYCLE, second_id, "claimed")))

    def test_restored_unresolved_or_refused_history_never_retries_with_fresh_frame(self):
        for outcome in ("pending", "uncertain", "refused"):
            record = mc.ClaimRecord.from_dict(mc.ClaimRecord(CYCLE, MISSION, outcome).to_dict())
            tracker = mc.ClaimTracker(CYCLE, enabled=True, max_attempts=3, history=(record,))
            view = observation(frame=capture(4))
            with self.subTest(outcome=outcome):
                self.assertEqual(tracker.begin(view, MISSION, frame=view.frame, now=103).state, "stopped")
                self.assertEqual(tracker.history, (record,))

    def test_history_cannot_drop_cycle_duplicates_unknown_fields_or_invalid_options(self):
        record = mc.ClaimRecord(CYCLE, MISSION, "claimed")
        for options in ({"history": (record, record)}, {"history": (replace(record, cycle_id="offline-day-B"),)},
                        {"history": [record]}, {"max_attempts": True}, {"max_attempts": 0}, {"enabled": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                mc.ClaimTracker(CYCLE, **options)
        for record_data in ({**record.to_dict(), "input_authorized": True}, {"cycle_id": CYCLE},
                            {**record.to_dict(), "outcome": "success"}):
            with self.subTest(record=record_data), self.assertRaises(ValueError):
                mc.ClaimRecord.from_dict(record_data)

    def test_concurrent_polling_reserves_exactly_one_attempt(self):
        tracker = mc.ClaimTracker(CYCLE, enabled=True, max_attempts=4)
        with ThreadPoolExecutor(max_workers=4) as pool:
            decisions = list(pool.map(lambda _: tracker.begin(observation(), MISSION, frame=capture(), now=100), range(16)))
        self.assertEqual(sum(d.state == "prepared" for d in decisions), 1)
        self.assertEqual(tracker.history, (mc.ClaimRecord(CYCLE, MISSION, "pending"),))


if __name__ == "__main__":
    unittest.main()
