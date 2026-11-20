from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from agent.core.confirmation import ConfirmationEvidence, ConfirmationLedger


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 100.0

    def __call__(self) -> float:
        return self.seconds


def evidence() -> ConfirmationEvidence:
    return ConfirmationEvidence(
        image_sha256="a" * 64,
        configuration_sha256="b" * 64,
        scene="SC.Produce.Menu",
        candidate_id="visual_lesson",
        box=(80, 90, 120, 45),
        score=0.99,
    )


class ConfirmationEvidenceTests(unittest.TestCase):
    def test_valid_evidence_is_immutable_and_json_serializable(self) -> None:
        value = evidence()
        self.assertIsNone(value.validate())
        self.assertEqual(json.loads(json.dumps(value.to_dict()))["box"], [80, 90, 120, 45])
        with self.assertRaises(FrozenInstanceError):
            value.score = 1.0
        for score in (0.95, 1.0, 1):
            replace(value, score=score).validate()
        replace(value, box=(0, 0, 1280, 720)).validate()

    def test_hash_identity_and_scene_prefix_are_required(self) -> None:
        invalid = {
            "image_sha256": (None, "a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n"),
            "configuration_sha256": (42, "", "B" * 64),
            "scene": (None, "Produce.Menu", "SC.", "SC.Menu ", "SC.Menu\n"),
            "candidate_id": (None, 1, "", "  \t"),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, f"^invalid_{field}$"):
                    replace(evidence(), **{field: value}).validate()

    def test_wrong_canvas_and_invalid_rectangles_are_rejected(self) -> None:
        for value in ((1920, 1080), (720, 1280), [1280, 720], (1280.0, 720), (True, 720), (1280,), None):
            with self.subTest(dimensions=value), self.assertRaisesRegex(ValueError, "^invalid_dimensions$"):
                replace(evidence(), dimensions=value).validate()
        for value in ((-1, 0, 10, 10), (0, -1, 10, 10), (0, 0, 0, 10), (0, 0, 10, -1), (1270, 0, 11, 10), (0, 710, 10, 11), (True, 0, 10, 10), (0.0, 0, 10, 10), [0, 0, 10, 10], (0, 0, 10), None):
            with self.subTest(box=value), self.assertRaisesRegex(ValueError, "^invalid_box$"):
                replace(evidence(), box=value).validate()

    def test_nonfinite_or_weak_confidence_and_coerced_booleans_are_rejected(self) -> None:
        for value in (True, False, "0.99", None, float("nan"), float("inf"), -float("inf"), 0.949999, 1.000001):
            with self.subTest(score=value), self.assertRaisesRegex(ValueError, "^invalid_score$"):
                replace(evidence(), score=value).validate()
        for field in ("recognized", "available"):
            for value in (False, 1, "true", None):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, f"^invalid_{field}$"):
                    replace(evidence(), **{field: value}).validate()


class ConfirmationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.ledger = ConfirmationLedger(clock=self.clock, session_id="offline-rehearsal")
        self.value = evidence()

    def issue(self) -> str:
        return self.ledger.issue(self.value)["proposal_id"]

    def test_preview_and_receipt_only_confirm_the_rehearsal(self) -> None:
        preview = self.ledger.issue(self.value)
        self.assertEqual(preview["session_id"], "offline-rehearsal")
        self.assertEqual(preview["remaining_seconds"], 30)
        self.assertEqual(preview["ttl_seconds"], 30)
        self.clock.seconds += 10
        receipt = self.ledger.confirm(preview["proposal_id"], self.value, approved=True)
        self.assertIs(receipt["confirmed"], True)
        self.assertEqual(receipt["evidence"], self.value.to_dict())
        for result in (preview, receipt):
            self.assertIs(result["input_authorized"], False)
            self.assertIs(result["input_enabled"], False)
            self.assertIs(result["rehearsal_only"], True)
            json.dumps(result, allow_nan=False)
            self.assertNotIn("expires_at", result)
        with self.assertRaisesRegex(ValueError, "^proposal_consumed$"):
            self.ledger.confirm(preview["proposal_id"], self.value, approved=True)

    def test_expiration_boundary_rejects_and_cannot_be_retried(self) -> None:
        proposal_id = self.issue()
        self.clock.seconds += 30
        with self.assertRaisesRegex(ValueError, "^proposal_expired$"):
            self.ledger.confirm(proposal_id, self.value, approved=True)
        self.clock.seconds -= 1
        with self.assertRaisesRegex(ValueError, "^proposal_expired$"):
            self.ledger.confirm(proposal_id, self.value, approved=True)

    def test_confirmation_just_before_deadline_succeeds(self) -> None:
        proposal_id = self.issue()
        self.clock.seconds += 29.999
        self.assertTrue(self.ledger.confirm(proposal_id, self.value, approved=True)["confirmed"])

    def test_each_changed_binding_is_terminal(self) -> None:
        changes = {
            "image_sha256": "c" * 64,
            "configuration_sha256": "d" * 64,
            "scene": "SC.Produce.Rest",
            "candidate_id": "rest",
            "box": (81, 90, 120, 45),
            "score": 0.98,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                proposal_id = self.issue()
                with self.assertRaisesRegex(ValueError, "^evidence_mismatch$"):
                    self.ledger.confirm(proposal_id, replace(self.value, **{field: value}), approved=True)
                with self.assertRaisesRegex(ValueError, "^proposal_rejected$"):
                    self.ledger.confirm(proposal_id, self.value, approved=True)

    def test_current_invalid_evidence_also_rejects_permanently(self) -> None:
        for changed in (replace(self.value, recognized=False), replace(self.value, available=False), replace(self.value, dimensions=(640, 360)), None, self.value.to_dict()):
            with self.subTest(current_evidence=changed):
                proposal_id = self.issue()
                with self.assertRaises(ValueError):
                    self.ledger.confirm(proposal_id, changed, approved=True)
                with self.assertRaisesRegex(ValueError, "^proposal_rejected$"):
                    self.ledger.confirm(proposal_id, self.value, approved=True)

    def test_false_approval_cancels_and_other_types_reject(self) -> None:
        proposal_id = self.issue()
        with self.assertRaisesRegex(ValueError, "^approval_required$"):
            self.ledger.confirm(proposal_id, self.value, approved=False)
        with self.assertRaisesRegex(ValueError, "^proposal_cancelled$"):
            self.ledger.confirm(proposal_id, self.value, approved=True)
        for approved in (1, "true", None):
            proposal_id = self.issue()
            with self.subTest(approved=approved), self.assertRaisesRegex(ValueError, "^invalid_approval$"):
                self.ledger.confirm(proposal_id, self.value, approved=approved)
            with self.assertRaisesRegex(ValueError, "^proposal_rejected$"):
                self.ledger.confirm(proposal_id, self.value, approved=True)

    def test_new_proposal_supersedes_previous_pending_proposal(self) -> None:
        first = self.issue()
        second = self.issue()
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(ValueError, "^proposal_superseded$"):
            self.ledger.confirm(first, self.value, approved=True)
        self.assertTrue(self.ledger.confirm(second, self.value, approved=True)["confirmed"])

    def test_cancel_and_stop_leave_no_reusable_proposal(self) -> None:
        proposal_id = self.issue()
        result = self.ledger.cancel(proposal_id)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["input_authorized"])
        with self.assertRaisesRegex(ValueError, "^proposal_cancelled$"):
            self.ledger.cancel(proposal_id)
        next_id = self.issue()
        self.ledger.cancel_all()
        self.ledger.cancel_all()
        with self.assertRaisesRegex(ValueError, "^proposal_cancelled$"):
            self.ledger.confirm(next_id, self.value, approved=True)

    def test_other_session_or_new_ledger_cannot_consume_a_proposal(self) -> None:
        proposal_id = self.issue()
        for other in (ConfirmationLedger(clock=self.clock), ConfirmationLedger(clock=self.clock, session_id="offline-rehearsal")):
            with self.assertRaisesRegex(ValueError, "^unknown_proposal$"):
                other.confirm(proposal_id, self.value, approved=True)
        self.assertTrue(self.ledger.confirm(proposal_id, self.value, approved=True)["confirmed"])

    def test_invalid_ids_do_not_cancel_the_legitimate_pending_proposal(self) -> None:
        proposal_id = self.issue()
        for value in (None, 1, "", "unknown"):
            with self.subTest(proposal_id=value), self.assertRaises(ValueError):
                self.ledger.confirm(value, self.value, approved=True)
            with self.assertRaises(ValueError):
                self.ledger.cancel(value)
        self.assertTrue(self.ledger.confirm(proposal_id, self.value, approved=True)["confirmed"])

    def test_modifying_preview_does_not_modify_bound_evidence(self) -> None:
        preview = self.ledger.issue(self.value)
        preview["evidence"]["box"][0] = 200
        preview["evidence"]["score"] = 0.1
        receipt = self.ledger.confirm(preview["proposal_id"], self.value, approved=True)
        self.assertEqual(receipt["evidence"]["box"][0], 80)
        self.assertEqual(receipt["evidence"]["score"], 0.99)

    def test_simultaneous_confirmation_returns_only_one_receipt(self) -> None:
        proposal_id = self.issue()

        def confirm_once() -> object:
            try:
                return self.ledger.confirm(proposal_id, self.value, approved=True)
            except ValueError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: confirm_once(), range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("proposal_consumed", results)

    def test_invalid_issue_does_not_supersede_a_valid_pending_proposal(self) -> None:
        proposal_id = self.issue()
        with self.assertRaisesRegex(ValueError, "^invalid_score$"):
            self.ledger.issue(replace(self.value, score=0.1))
        self.assertTrue(self.ledger.confirm(proposal_id, self.value, approved=True)["confirmed"])

    def test_constructor_rejects_invalid_lifetime_and_session(self) -> None:
        for ttl in (True, None, "30", 0, -1, 120.001, float("nan"), float("inf")):
            with self.subTest(ttl=ttl), self.assertRaisesRegex(ValueError, "^invalid_ttl_seconds$"):
                ConfirmationLedger(ttl_seconds=ttl)
        for session_id in (True, 1, "", "  "):
            with self.subTest(session_id=session_id), self.assertRaisesRegex(ValueError, "^invalid_session_id$"):
                ConfirmationLedger(session_id=session_id)
        with self.assertRaisesRegex(ValueError, "^invalid_clock$"):
            ConfirmationLedger(clock=100)

    def test_clock_failure_rejects_existing_ticket(self) -> None:
        for value in (float("nan"), float("inf"), True, "100", 99.0, 10 ** 1000):
            self.clock.seconds = 100.0
            proposal_id = self.issue()
            self.clock.seconds = value
            with self.subTest(clock=value), self.assertRaisesRegex(ValueError, "^invalid_clock$"):
                self.ledger.confirm(proposal_id, self.value, approved=True)
            self.clock.seconds = 100.0
            with self.assertRaisesRegex(ValueError, "^proposal_rejected$"):
                self.ledger.confirm(proposal_id, self.value, approved=True)

    def test_clock_exception_also_rejects_existing_ticket(self) -> None:
        failing = False

        def clock() -> float:
            if failing:
                raise RuntimeError("unavailable")
            return 100.0

        ledger = ConfirmationLedger(clock=clock)
        proposal_id = ledger.issue(self.value)["proposal_id"]
        failing = True
        with self.assertRaisesRegex(ValueError, "^invalid_clock$"):
            ledger.confirm(proposal_id, self.value, approved=True)
        failing = False
        with self.assertRaisesRegex(ValueError, "^proposal_rejected$"):
            ledger.confirm(proposal_id, self.value, approved=True)


if __name__ == "__main__":
    unittest.main()
