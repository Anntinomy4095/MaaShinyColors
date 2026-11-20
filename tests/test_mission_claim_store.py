"""Durable offline intent/recovery tests; no game, Maa or input authorization."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent.core.frame_permit import FrameCapture
from agent.core import mission_claim as mc
from agent.core import mission_claim_store as store


CYCLE = "offline-durable-day-A"
MISSION = "offline-durable-login"


def frame(seq=1):
    return FrameCapture(f"{seq:064x}", 99.0 + seq, (720, 1280, 3), f"{seq + 100:064x}", seq, seq, seq * 2)


def view(seq=1, identity=MISSION, claimed=False):
    target = mc.ClaimTarget(identity, "single_daily", (1000, 250, 120, 45), True, True)
    return mc.ClaimObservation(CYCLE, frame(seq), "daily_missions", True, True,
                               (mc.ClaimRow(identity, (1, 1), "claimed" if claimed else "claimable"),),
                               () if claimed else (target,))


def begin(tracker, identity=MISSION, seq=1):
    return tracker.begin(view(seq, identity), identity, frame=frame(seq), now=99.0 + seq)


def complete(tracker, attempt):
    proof = mc.ClaimReceipt(CYCLE, attempt.mission_id, frame(2), "daily_mission_reward", True, True, True)
    observed = tracker.accept_receipt(attempt, proof, frame=frame(2), now=101.0)
    if observed.state != "receipt_observed":
        raise AssertionError(observed)
    return tracker.finish(attempt, view(3, attempt.mission_id, True), frame=frame(3), now=102.0)


def competing_process(path, barrier, results, identity):
    tracker = store.DurableClaimTracker(path, CYCLE, enabled=True, max_attempts=3)
    barrier.wait(timeout=60)     # spawned interpreters start slowly while the full suite runs in parallel
    decision = begin(tracker, identity)
    results.put((decision.state, decision.input_authorized))


def crashing_process(path, after_commit):
    tracker = store.DurableClaimTracker(path, CYCLE, enabled=True)

    def crash(connection):
        if after_commit:
            connection.commit()
        os._exit(7)

    store._commit = crash
    begin(tracker)


class MissionClaimStoreTests(unittest.TestCase):
    def setUp(self):
        store.PRIVATE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=store.PRIVATE)
        self.root = Path(self.temp.name)
        self.path = self.root / "claim.sqlite3"
        store.initialize_claim_journal(self.path, CYCLE)
        self.addCleanup(self.temp.cleanup)

    def tracker(self, path=None, **options):
        return store.DurableClaimTracker(path or self.path, CYCLE, enabled=True, **options)

    def events(self, path=None):
        with closing(sqlite3.connect((path or self.path).as_uri() + "?mode=ro", uri=True)) as connection:
            return connection.execute("SELECT seq, mission_id, outcome FROM claim_events ORDER BY seq").fetchall()

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(statement, parameters)

    def assert_stop(self, tracker):
        decision = begin(tracker, seq=4)
        self.assertEqual(decision.state, "stopped", decision)
        self.assertIsNone(decision.target)
        self.assertIsNone(decision.attempt)
        self.assertFalse(decision.input_authorized)
        return decision

    def test_disabled_methods_leave_initialized_journal_unchanged(self):
        tracker = store.DurableClaimTracker(self.path, CYCLE)
        before = self.path.read_bytes()
        decisions = [begin(tracker), tracker.refuse(), tracker.interrupt(),
                     tracker.accept_receipt(None, None, frame=None, now=0),
                     tracker.finish(None, None, frame=None, now=0)]
        self.assertTrue(all(d.state == "disabled" and not d.input_authorized for d in decisions))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(tracker.history, ())
        self.assertEqual(tracker.attempts, 0)

    def test_existing_or_incompletely_initialized_files_are_never_overwritten(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            store.initialize_claim_journal(self.path, CYCLE)
        self.assertEqual(self.path.read_bytes(), before)
        empty = self.root / "empty.sqlite3"
        empty.touch()
        with self.assertRaises(FileExistsError):
            store.initialize_claim_journal(empty, CYCLE)
        self.assert_stop(self.tracker(empty))
        self.assertEqual(empty.read_bytes(), b"")

    def test_missing_journal_is_unavailable_never_created_as_empty_history(self):
        path = self.root / "missing.sqlite3"
        tracker = self.tracker(path)
        self.assertEqual(self.assert_stop(tracker).reason, "claim_journal_missing")
        self.assertIsNone(tracker.history)
        self.assertIsNone(tracker.attempts)
        self.assertFalse(path.exists())

    def test_initialization_failure_is_retained_without_automatic_recreation(self):
        path = self.root / "init-failed.sqlite3"
        with patch.object(store, "_commit", side_effect=OSError("initial commit failed")):
            with self.assertRaises(store.ClaimJournalError):
                store.initialize_claim_journal(path, CYCLE)
        self.assertTrue(path.exists())
        self.assert_stop(self.tracker(path))
        with self.assertRaises(FileExistsError):
            store.initialize_claim_journal(path, CYCLE)

    def test_deleted_initialized_journal_does_not_get_recreated(self):
        tracker = self.tracker()
        self.path.unlink()
        self.assert_stop(tracker)
        self.assertIsNone(tracker.history)
        self.assert_stop(self.tracker())
        self.assertFalse(self.path.exists())

    def test_cycle_and_cap_are_bound_without_filtering_or_resetting_history(self):
        begin(self.tracker())
        for cycle, cap in (("offline-durable-day-B", 1), (CYCLE, 2)):
            tracker = store.DurableClaimTracker(self.path, cycle, enabled=True, max_attempts=cap)
            self.assertEqual(self.assert_stop(tracker).reason, "claim_journal_binding_mismatch")
            self.assertIsNone(tracker.history)
        self.assertEqual(self.events(), [(1, MISSION, "pending")])

    def test_invalid_options_and_nonprivate_paths_create_no_journal(self):
        absent = self.root / "invalid.sqlite3"
        for options in ({"max_attempts": True}, {"max_attempts": 0}, {"enabled": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                store.DurableClaimTracker(absent, CYCLE, **options)
        outside = store.PRIVATE.parent / "claim-test-not-created.sqlite3"
        with self.assertRaises(ValueError):
            store.initialize_claim_journal(outside, CYCLE)
        with self.assertRaises(ValueError):
            store.initialize_claim_journal(self.root / "wrong.json", CYCLE)
        self.assertFalse(absent.exists())
        self.assertFalse(outside.exists())

    def test_pending_is_independently_visible_before_prepared_can_escape(self):
        tracker = self.tracker()
        entered, release = threading.Event(), threading.Event()
        original_commit = store._commit

        def hold_ack(connection):
            original_commit(connection)
            entered.set()
            if not release.wait(3):
                raise OSError("controlled commit acknowledgement release missing")

        with patch.object(store, "_commit", hold_ack), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(begin, tracker)
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(future.done())
                self.assertEqual(self.events(), [(1, MISSION, "pending")])
            finally:
                release.set()
            decision = future.result(timeout=5)
        self.assertEqual(decision.state, "prepared")
        self.assertFalse(decision.input_authorized)
        self.assertEqual(tracker.history, (mc.ClaimRecord(CYCLE, MISSION, "pending"),))

    def test_close_keeps_pending_and_recovered_tracker_stops_with_fresh_frame(self):
        tracker = self.tracker()
        self.assertEqual(begin(tracker).state, "prepared")
        tracker.close()
        self.assertEqual(self.assert_stop(tracker).reason, "claim_journal_closed")
        recovered = self.tracker()
        self.assertEqual(self.assert_stop(recovered).reason, "claim_previous_attempt_unresolved")
        self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, "pending"),))

    def test_recovered_pending_observer_does_not_poison_original_owner(self):
        owner = self.tracker()
        attempt = begin(owner).attempt
        observer = self.tracker()
        self.assert_stop(observer)
        self.assertEqual(complete(owner, attempt).state, "claimed")
        self.assertEqual(self.events(), [(1, MISSION, "pending"), (2, MISSION, "claimed")])

    def test_receipt_alone_is_durable_pending_and_never_success_after_restart(self):
        tracker = self.tracker()
        attempt = begin(tracker).attempt
        proof = mc.ClaimReceipt(CYCLE, MISSION, frame(2), "daily_mission_reward", True, True, True)
        self.assertEqual(tracker.accept_receipt(attempt, proof, frame=frame(2), now=101).state, "receipt_observed")
        self.assertEqual(self.events(), [(1, MISSION, "pending")])
        self.assert_stop(self.tracker())

    def test_unverified_receipt_commits_uncertainty_and_recovery_cannot_retry(self):
        tracker = self.tracker()
        attempt = begin(tracker).attempt
        proof = mc.ClaimReceipt(CYCLE, MISSION, frame(2), "login_reward", True, True, True)
        decision = tracker.accept_receipt(attempt, proof, frame=frame(2), now=101)
        self.assertEqual(decision.reason, "claim_receipt_unverified")
        recovered = self.tracker()
        self.assert_stop(recovered)
        self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, "uncertain"),))

    def test_refused_and_uncertain_are_retained_and_block_restored_retry(self):
        for operation, outcome in (("refuse", "refused"), ("interrupt", "uncertain")):
            with self.subTest(outcome=outcome):
                path = self.root / f"{outcome}.sqlite3"
                store.initialize_claim_journal(path, CYCLE)
                tracker = self.tracker(path)
                begin(tracker)
                self.assertEqual(getattr(tracker, operation)().state, "stopped")
                self.assert_stop(tracker)
                recovered = self.tracker(path)
                self.assert_stop(recovered)
                self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, outcome),))
                self.assertEqual(self.events(path), [(1, MISSION, "pending"), (2, MISSION, outcome)])

    def test_positive_terminal_commits_before_success_and_budget_survives_restart(self):
        tracker = self.tracker()
        attempt = begin(tracker).attempt
        result = complete(tracker, attempt)
        self.assertEqual(result.state, "claimed")
        self.assertFalse(result.input_authorized)
        recovered = self.tracker()
        self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, "claimed"),))
        self.assertEqual(recovered.attempts, 1)
        self.assertEqual(begin(recovered, "offline-other", 4).reason, "claim_attempt_budget_exhausted")

    def test_duplicate_mission_remains_used_after_restart_with_larger_bound_cap(self):
        path = self.root / "two.sqlite3"
        store.initialize_claim_journal(path, CYCLE, max_attempts=2)
        tracker = self.tracker(path, max_attempts=2)
        complete(tracker, begin(tracker).attempt)
        recovered = self.tracker(path, max_attempts=2)
        self.assertEqual(begin(recovered, seq=4).reason, "claim_attempt_already_used")
        self.assertEqual(self.events(path), [(1, MISSION, "pending"), (2, MISSION, "claimed")])

    def test_ambiguity_without_intent_persists_stop_across_restart(self):
        tracker = self.tracker()
        ambiguous = replace(view(), targets=view().targets * 2)
        decision = tracker.begin(ambiguous, MISSION, frame=frame(), now=100)
        self.assertEqual(decision.reason, "claim_target_ambiguous")
        recovered = self.tracker()
        self.assertEqual(self.assert_stop(recovered).reason, "claim_target_ambiguous")
        self.assertEqual(recovered.history, ())
        self.assertEqual(self.events(), [])

    def test_same_instance_concurrent_polling_reserves_once_and_owner_can_finish(self):
        tracker = self.tracker()
        with ThreadPoolExecutor(max_workers=4) as pool:
            decisions = list(pool.map(lambda _: begin(tracker), range(12)))
        prepared = [d for d in decisions if d.state == "prepared"]
        self.assertEqual(len(prepared), 1)
        self.assertTrue(all(not d.input_authorized for d in decisions))
        self.assertEqual(complete(tracker, prepared[0].attempt).state, "claimed")

    def test_independent_instances_with_stale_snapshots_do_not_overwrite_owner(self):
        owner, stale = self.tracker(), self.tracker()
        attempt = begin(owner).attempt
        self.assertEqual(self.assert_stop(stale).reason, "claim_journal_changed")
        self.assertEqual(complete(owner, attempt).state, "claimed")
        self.assert_stop(stale)
        self.assertEqual(self.events(), [(1, MISSION, "pending"), (2, MISSION, "claimed")])

    def test_independent_instances_compete_for_same_and_distinct_missions(self):
        for distinct in (False, True):
            with self.subTest(distinct=distinct):
                path = self.root / f"compete-{distinct}.sqlite3"
                store.initialize_claim_journal(path, CYCLE, max_attempts=3)
                trackers = [self.tracker(path, max_attempts=3) for _ in range(2)]
                barrier = threading.Barrier(2)

                def compete(index):
                    barrier.wait(timeout=3)
                    return begin(trackers[index], "offline-other" if distinct and index else MISSION)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    decisions = list(pool.map(compete, range(2)))
                prepared_count = sum(d.state == "prepared" for d in decisions)
                self.assertLessEqual(prepared_count, 1)
                self.assertEqual(len(self.events(path)), prepared_count)
                self.assertTrue(all(not d.input_authorized for d in decisions))

    def test_cross_process_competition_reserves_at_most_one_distinct_mission(self):
        path = self.root / "process.sqlite3"
        store.initialize_claim_journal(path, CYCLE, max_attempts=3)
        context = multiprocessing.get_context("spawn")
        barrier, results = context.Barrier(2), context.Queue()
        processes = [context.Process(target=competing_process, args=(str(path), barrier, results, identity))
                     for identity in (MISSION, "offline-other")]
        try:
            for process in processes:
                process.start()
            decisions = [results.get(timeout=90) for _ in processes]
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            prepared_count = sum(state == "prepared" for state, _ in decisions)
            self.assertLessEqual(prepared_count, 1)
            self.assertTrue(all(not authorized for _, authorized in decisions))
            self.assertEqual(len(self.events(path)), prepared_count)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_locked_database_stops_instance_without_wait_or_retry(self):
        tracker = self.tracker()
        connection = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.assert_stop(tracker)
        finally:
            connection.close()
        self.assert_stop(tracker)
        self.assertEqual(self.events(), [])

    def test_commit_failure_before_acknowledgment_leaks_no_candidate_and_never_retries_instance(self):
        tracker = self.tracker()
        with patch.object(store, "_commit", side_effect=sqlite3.OperationalError("disk full")):
            self.assert_stop(tracker)
        self.assertIsNone(tracker.history)
        self.assert_stop(tracker)
        self.assertEqual(self.events(), [])
        # No prepared candidate escaped and the uncommitted intent rolled back.
        self.assertEqual(self.tracker().history, ())

    def test_commit_succeeds_then_acknowledgment_fails_pending_survives_restart(self):
        tracker = self.tracker()
        original_commit = store._commit

        def lost_ack(connection):
            original_commit(connection)
            raise OSError("commit acknowledgement lost")

        with patch.object(store, "_commit", lost_ack):
            self.assert_stop(tracker)
        self.assertIsNone(tracker.history)
        self.assert_stop(tracker)
        recovered = self.tracker()
        self.assert_stop(recovered)
        self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, "pending"),))

    def test_result_commit_failure_does_not_report_claimed_or_clear_durable_pending(self):
        tracker = self.tracker()
        attempt = begin(tracker).attempt
        proof = mc.ClaimReceipt(CYCLE, MISSION, frame(2), "daily_mission_reward", True, True, True)
        tracker.accept_receipt(attempt, proof, frame=frame(2), now=101)
        with patch.object(store, "_commit", side_effect=OSError("result write failed")):
            decision = tracker.finish(attempt, view(3, claimed=True), frame=frame(3), now=102)
        self.assertEqual(decision.state, "stopped")
        self.assertFalse(decision.input_authorized)
        self.assertIsNone(tracker.history)
        self.assertEqual(self.events(), [(1, MISSION, "pending")])
        self.assert_stop(self.tracker())

    def test_committed_claimed_with_lost_ack_keeps_budget_and_deduplication(self):
        tracker = self.tracker()
        attempt = begin(tracker).attempt
        proof = mc.ClaimReceipt(CYCLE, MISSION, frame(2), "daily_mission_reward", True, True, True)
        tracker.accept_receipt(attempt, proof, frame=frame(2), now=101)
        original_commit = store._commit

        def lost_ack(connection):
            original_commit(connection)
            raise OSError("result acknowledgement lost")

        with patch.object(store, "_commit", lost_ack):
            result = tracker.finish(attempt, view(3, claimed=True), frame=frame(3), now=102)
        self.assertEqual(result.state, "stopped")
        self.assert_stop(tracker)
        recovered = self.tracker()
        self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, "claimed"),))
        self.assertEqual(begin(recovered, seq=4).reason, "claim_attempt_already_used")

    def test_each_mutation_uses_zero_wait_and_extra_synchronization(self):
        tracker = self.tracker()
        observed = []
        original_checkpoint = store._checkpoint

        def inspect_transaction(connection, *args):
            observed.append((connection.execute("PRAGMA busy_timeout").fetchone()[0],
                             connection.execute("PRAGMA synchronous").fetchone()[0],
                             connection.execute("PRAGMA journal_mode").fetchone()[0]))
            self.assertTrue(connection.in_transaction)
            return original_checkpoint(connection, *args)

        with patch.object(store, "_checkpoint", inspect_transaction):
            complete(tracker, begin(tracker).attempt)
        self.assertEqual(observed, [(0, 3, "delete")] * 3)

    def test_append_failure_rolls_back_and_latches_stop(self):
        tracker = self.tracker()
        original_checkpoint = store._checkpoint

        def failed_checkpoint(*args):
            original_checkpoint(*args)
            raise sqlite3.OperationalError("controlled event/header write failure")

        with patch.object(store, "_checkpoint", failed_checkpoint):
            self.assert_stop(tracker)
        self.assert_stop(tracker)
        self.assertEqual(self.events(), [])

    def test_open_failure_is_not_retried_after_io_recovers(self):
        tracker = self.tracker()
        with patch.object(store.sqlite3, "connect", side_effect=OSError("open failed")):
            self.assert_stop(tracker)
        self.assert_stop(tracker)
        self.assertEqual(self.events(), [])

    def test_corrupt_and_truncated_journals_are_preserved_and_rejected(self):
        begin(self.tracker())
        valid = self.path.read_bytes()
        for content in (b"not a sqlite database", valid[:120], b""):
            with self.subTest(size=len(content)):
                self.path.write_bytes(content)
                tracker = self.tracker()
                self.assert_stop(tracker)
                self.assertIsNone(tracker.history)
                self.assertEqual(self.path.read_bytes(), content)

    def test_unknown_schema_version_extra_tables_and_invalid_header_are_rejected(self):
        for mutation in ("UPDATE claim_meta SET version=2", "CREATE TABLE sqliteXextra (secret TEXT)",
                         "UPDATE claim_meta SET event_count=1", "UPDATE claim_meta SET max_attempts=0",
                         "UPDATE claim_meta SET halt_reason='untrusted reason'", "DELETE FROM claim_meta"):
            with self.subTest(mutation=mutation):
                self.sql(mutation)
                self.assert_stop(self.tracker())
                # Start each separate corruption case from the known original bytes.
                self.path.write_bytes(self.clean_database)

    @property
    def clean_database(self):
        path = self.root / "clean-template.sqlite3"
        if not path.exists():
            store.initialize_claim_journal(path, CYCLE)
        return path.read_bytes()

    def test_event_gaps_duplicate_intents_and_illegal_terminal_transitions_are_rejected(self):
        cases = [
            ((1, MISSION, "claimed"),),
            ((2, MISSION, "pending"),),
            ((1, MISSION, "pending"), (2, MISSION, "pending")),
            ((1, MISSION, "pending"), (2, "offline-other", "claimed")),
            ((1, MISSION, "pending"), (2, MISSION, "uncertain"), (3, MISSION, "pending")),
            ((1, MISSION, "pending"), (2, MISSION, "refused"), (3, "offline-other", "pending")),
            ((1, MISSION, "pending"), (2, MISSION, "bogus")),
        ]
        clean = self.path.read_bytes()
        for events in cases:
            with self.subTest(events=events):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.executemany("INSERT INTO claim_events VALUES (?, ?, ?)", events)
                    connection.execute("UPDATE claim_meta SET event_count=?", (len(events),))
                self.assert_stop(self.tracker())
                self.path.write_bytes(clean)

    def test_deleted_event_or_conflicting_history_stops_existing_snapshot(self):
        tracker = self.tracker()
        begin(tracker)
        self.sql("DELETE FROM claim_events")
        self.assert_stop(tracker)
        self.assertIsNone(tracker.history)
        self.assert_stop(self.tracker())

    def test_process_death_before_and_after_commit_preserves_correct_recovery(self):
        context = multiprocessing.get_context("spawn")
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                path = self.root / f"crash-{after_commit}.sqlite3"
                store.initialize_claim_journal(path, CYCLE)
                process = context.Process(target=crashing_process, args=(str(path), after_commit))
                try:
                    process.start()
                    process.join(timeout=90)
                    self.assertEqual(process.exitcode, 7)
                    recovered = self.tracker(path)
                    if after_commit:
                        self.assert_stop(recovered)
                        self.assertEqual(recovered.history, (mc.ClaimRecord(CYCLE, MISSION, "pending"),))
                    else:
                        # No commit or prepared return happened, so no input was possible.
                        self.assertEqual(recovered.history, ())
                        self.assertEqual(begin(recovered).state, "prepared")
                finally:
                    if process.is_alive():
                        process.terminate()
                    if process.pid is not None:
                        process.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
