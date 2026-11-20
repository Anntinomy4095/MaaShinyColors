import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.core.training_flow import ACTION_POINTS, ANCHORS, CARD_ID, PREFIX
from tools.run_daily_training import (LEVEL_VARIANTS, NUMERIC_CROPS, current_resources_match, foreground_allowed, guarded_send,
                                     historical_numeric_replay, numeric_pixels, observed_integer,
                                     validate_offline_proof, validate_post_spend_resume, validate_preparation_resume, validate_prior,
                                     verify_post_spend_observation,
                                     verify_baseline_image, verify_resume_resources, write_consumption_pending)


def recognition(text="22612", score=.99, box=None):
    return {"hit": True, "results": [{"text": text, "score": score,
            "box": box or [731, 359, 60, 16]}], "raw_detail": {}}


def complete(stage):
    return [{"scene": PREFIX + stage + "." + name, "hit": True, "score": .99,
             "roi": roi, "raw_detail": {"filtered": [{"box": roi}]}}
            for name, roi in ANCHORS[stage].items()]


class DailyTrainingRunnerTests(unittest.TestCase):
    def test_failed_consumption_response_is_one_attempt_and_keeps_uncertainty(self):
        report = {"input_attempts": 0}
        sent, snapshots = [], []
        def send(request):
            sent.append(request)
            raise TimeoutError("lost response")
        call = lambda: guarded_send(report, True, "Confirm", ACTION_POINTS["Confirm"],
                                    lambda: {"fixed": True}, send, lambda: snapshots.append(dict(report)))
        self.assertFalse(call())
        self.assertFalse(call())
        self.assertEqual(len(sent), 1)
        self.assertEqual(report["input_attempts"], 1)
        self.assertTrue(report["uncertain_consumption"])
        self.assertEqual(snapshots[0]["reason"], "input_pending_no_retry")

    def test_guard_refusal_readonly_and_wrong_point_never_send(self):
        for execute, point, error in ((False, ACTION_POINTS["Zero"], False),
                                      (True, (1, 2), False),
                                      (True, ACTION_POINTS["Zero"], True)):
            report = {"input_attempts": 0}
            def guard():
                if error:
                    raise ValueError("unknown stage")
                return {}
            self.assertFalse(guarded_send(report, execute, "Zero", point, guard,
                                         lambda request: self.fail("unexpected input")))
            self.assertEqual(report["input_attempts"], 0)

    def test_checkpoint_failure_does_not_transmit_and_cannot_retry(self):
        report = {"input_attempts": 0}
        def checkpoint():
            raise OSError("disk unavailable")
        self.assertFalse(guarded_send(report, True, "Confirm", ACTION_POINTS["Confirm"], lambda: {},
                                     lambda request: self.fail("unexpected input"), checkpoint))
        self.assertEqual(report["input_attempts"], 1)
        self.assertTrue(report["uncertain_consumption"])

    def test_integer_requires_actual_unique_high_confidence_fixed_roi_ocr(self):
        self.assertEqual(observed_integer("inventory", recognition("x22612"))[0], 22612)
        bad = [recognition(score=.94), recognition("?"), recognition(box=[0, 0, 10, 10]),
               recognition(box=[731, 359, 90, 16]), {},
               {**recognition(), "results": recognition()["results"] * 2}]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                observed_integer("inventory", item)

    def test_foreground_partial_conflict_and_known_overlay_block(self):
        clear = {"state": "no_known_popup", "popup": None,
                 "complete_contracts": [], "partial_contracts": []}
        self.assertTrue(foreground_allowed(clear))
        for update in ({"state": "partial"}, {"state": "conflict"},
                       {"popup": "SC.Foreground.Loading"}, {"partial_contracts": ["SC.Unknown"]}):
            self.assertFalse(foreground_allowed({**clear, **update}))
        self.assertFalse(foreground_allowed({}))

    def test_blank_numbers_and_wrong_canvas_never_reach_numeric_ocr(self):
        import numpy as np
        for name in ("inventory", "level", "remaining_exp"):
            for color in (0, 255):
                pixels, reason = numeric_pixels(name, np.full((720, 1280, 3), color, dtype=np.uint8),
                                                "otsu" if name == "level" else None)
                self.assertIsNone(pixels)
                self.assertEqual(reason["reason"], "blank_or_low_contrast")
        with self.assertRaises(ValueError):
            numeric_pixels("inventory", np.zeros((480, 640, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            numeric_pixels("paid_currency", np.zeros((720, 1280, 3), dtype=np.uint8))

    def test_level_variants_are_fixed_and_other_fields_keep_single_threshold(self):
        import numpy as np
        image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        for name, (x, y, width, height) in NUMERIC_CROPS.items():
            image[y:y + height, x:x + width] = np.linspace(0, 255, width, dtype=np.uint8)[None, :, None]
        # Skewed levels so Otsu and the fixed 128 cutoff differ.
        x, y, width, height = NUMERIC_CROPS["level"]
        image[y:y + height, x:x + width] = (np.linspace(0, 1, width) ** 3 * 255).astype(np.uint8)[None, :, None]
        digests = set()
        for variant in LEVEL_VARIANTS:
            pixels, processing = numeric_pixels("level", image, variant)
            self.assertEqual(processing["variant"], variant)
            digests.add(processing["processed_sha256"])
        self.assertEqual(len(digests), len(LEVEL_VARIANTS))
        for name in ("inventory", "remaining_exp"):
            self.assertEqual(numeric_pixels(name, image)[1]["variant"], "threshold")
            with self.assertRaises(ValueError):
                numeric_pixels(name, image, "otsu")
        for variant in (None, "invented"):
            with self.assertRaises(ValueError):
                numeric_pixels("level", image, variant)

    def test_consumption_binding_rejects_inventory_level_and_unobserved_type_changes(self):
        baseline = {"inventory": 22612, "level": 1}
        self.assertTrue(current_resources_match(dict(baseline), baseline))
        for evidence in ({"inventory": 22611, "level": 1}, {"inventory": 11360, "level": 1},
                         {"inventory": 22612, "level": 2}, {"inventory": "22612", "level": 1},
                         {"inventory": 22612, "level": True}, {}):
            self.assertFalse(current_resources_match(evidence, baseline))

    def test_offline_proof_checks_full_guard_trace_and_fixed_action(self):
        data = {"samples": [{"id": "zero", "image": "zero.png"}]}
        truth = {"zero.png": {"stage": "Zero", "expected_guard": True}}
        sample = {"id": "zero", "stage": "Zero", "expected_guard": True,
                  "guard_proof": True, "passed": True, "recognitions": complete("Zero"),
                  "simulated_clicks": [list(ACTION_POINTS["Zero"])], "real_input_attempts": 0,
                  "other_input_calls": 0, "node_trace": [{"name": PREFIX + "Zero." + end,
                  "completed": True, "action_success": True} for end in ("Observe", "Click")]}
        proof = {"passed": True, "offline_only": True, "real_input_enabled": False,
                 "real_input_attempts": 0, "framework_version": "v5.12.3",
                 "configuration_sha256": "digest", "sample_count": 1, "samples": [sample]}
        self.assertEqual(validate_offline_proof(proof, data, truth, "digest"), [sample])
        for mutation in ("missing_anchor", "wrong_point", "duplicate_sample", "real_input"):
            bad = copy.deepcopy(proof)
            if mutation == "missing_anchor": bad["samples"][0]["recognitions"].pop()
            elif mutation == "wrong_point": bad["samples"][0]["simulated_clicks"] = [[1, 2]]
            elif mutation == "duplicate_sample": bad["samples"].append(sample)
            else: bad["real_input_attempts"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_offline_proof(bad, data, truth, "digest")

    def test_prior_report_cannot_resume_unknown_wrong_session_or_unobserved_baseline(self):
        baseline = {"card_id": CARD_ID, "inventory": 5, "level": 2, "remaining_exp": 90}
        prior = {"runner": "SC.DailyTraining", "passed": True, "input_enabled": True,
                 "session_pid": 123, "configuration_sha256": "d", "foreground_sha256": "f",
                 "implementation_sha256": "s", "ocr_model": {}, "stage": "Zero",
                 "postcondition": {"stage": "One"}, "reason": "stage_postcondition_verified",
                 "baseline": baseline}
        self.assertEqual(validate_prior(prior, "One", 123, "d", "f", "s", {}), baseline)
        for update in ({"passed": False}, {"session_pid": 456}, {"input_enabled": False},
                       {"baseline": {**baseline, "level": "2"}}, {"reason": "uncertain"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_prior({**prior, **update}, "One", 123, "d", "f", "s", {})


class PreparationResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.private = Path(self.temporary.name)
        self.private_patch = patch("tools.run_daily_training.PRIVATE", self.private)
        self.private_patch.start()
        self.addCleanup(self.private_patch.stop)
        (self.private / "daily_navigation").mkdir()
        self.model = {"files": {"rec.onnx": "fixed-model"}}
        self.session = {"pid": 123, "budget_paths": {action: str(self.private / "daily_navigation" / (action + ".json"))
                        for action in ("training-bronze-plus", "training-open-confirm", "training-confirm-one-bronze")}}
        self.base_path = self.private / "baseline.png"
        self.base_path.write_bytes(b"hash-bound baseline source")
        self.base_sha = hashlib.sha256(self.base_path.read_bytes()).hexdigest()
        self.baseline = {"card_id": CARD_ID, "card_type": "support_s", "ticket_type": "basic_bronze",
                         "inventory": 22614, "level": 2, "remaining_exp": 40, "frame_sha256": self.base_sha}

    def ledger(self, action, frame, **changes):
        data = {"Version": 1, "Navigation": action, "MaxAttempts": 1, "Attempts": 1,
                "LastPid": 123, "LastFrameSha256": frame, "LastNonce": "c" * 32}
        data.update(changes)
        Path(self.session["budget_paths"][action]).write_text(json.dumps(data), encoding="utf-8")
        return data

    def report(self, stage):
        previous = "Zero" if stage == "One" else "One"
        action = "training-bronze-plus" if previous == "Zero" else "training-open-confirm"
        source = self.private / (previous + "-fresh.png")
        source.write_bytes((previous + " original fresh source").encode())
        frame = hashlib.sha256(source.read_bytes()).hexdigest()
        evidence = {**self.baseline, "frame_sha256": frame, "bronze_quantity": 0 if previous == "Zero" else 1,
                    "silver_quantity": 0}
        if previous == "One":
            evidence.update(exp=50, linked_remaining_exp_frame_sha256=self.base_sha)
        ocr = {"inventory": {"recognition": recognition("22614", box=[739, 357, 59, 21])},
               "level": {"recognition": recognition("2", box=[267, 138, 24, 25])},
               "remaining_exp": {"recognition": recognition("40", box=[479, 149, 38, 25])}}
        foreground = {"state": "no_known_popup", "popup": None, "complete_contracts": [], "partial_contracts": []}
        guard = {"stage": previous, "guard_verified": True, "recognitions": complete(previous), "foreground": foreground,
                 "evidence": evidence, "ocr": ocr, "frame_sha256": frame}
        self.ledger(action, frame)
        return {"runner": "SC.DailyTraining", "stage": previous, "passed": False,
                "reason": "postcondition_unverified_no_retry", "input_enabled": True, "input_attempts": 1,
                "session_pid": 123, "uncertain_consumption": False, "resource_result_verified": False, "ocr_model": self.model,
                "response": {"ok": True, "attempted": True, "reason": "native_attempted"},
                "node_trace": [{"name": PREFIX + previous + "." + suffix, "completed": True, "action_success": True}
                               for suffix in ("Observe", "Click")],
                "baseline": dict(self.baseline), "baseline_frame": {"image": str(self.base_path), "sha256": self.base_sha},
                "frames": [{"image": str(source), "sha256": frame}], "fresh_guard": guard,
                "request_binding": {"action": action, "frame_sha256": frame, "nonce": "c" * 32},
                "configuration_sha256": "d" * 64, "foreground_sha256": "e" * 64, "implementation_sha256": "f" * 64}

    def test_explicit_nonconsuming_resume_accepts_old_calibration_and_links_real_one_inventory(self):
        for stage in ("One", "Confirm"):
            report = self.report(stage)
            result = validate_preparation_resume(report, stage, 123, self.session, self.model)
            self.assertEqual(result["baseline"], self.baseline)
            self.assertEqual(result["previous_configuration_sha256"], "d" * 64)
            self.assertEqual(result["previous_implementation_sha256"], "f" * 64)
            if stage == "Confirm":
                self.assertEqual(result["inventory_binding"], {"inventory": 22614,
                                 "frame_sha256": report["fresh_guard"]["frame_sha256"]})

    def test_unknown_response_uncertain_or_spurious_report_never_resumes(self):
        report = self.report("One")
        changes = ({"response": {"ok": False, "attempted": True, "reason": "unknown"}},
                   {"response": {"ok": False, "attempted": False, "reason": "refused"}},
                   {"response": None}, {"uncertain_consumption": True}, {"input_attempts": 0},
                   {"input_attempts": True}, {"input_attempts": 2}, {"passed": True},
                   {"resource_result_verified": True},
                   {"session_pid": 456}, {"stage": "Confirm"}, {"reason": "input_pending_no_retry"})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_preparation_resume({**report, **change}, "One", 123, self.session, self.model)

    def test_any_consumption_attempt_or_result_stage_refuses_recovery(self):
        report = self.report("One")
        for stage in ("Zero", "Result", "After"):
            with self.assertRaises(ValueError):
                validate_preparation_resume(report, stage, 123, self.session, self.model)
        self.ledger("training-confirm-one-bronze", "a" * 64)
        with self.assertRaisesRegex(ValueError, "Confirm"):
            validate_preparation_resume(report, "One", 123, self.session, self.model)

    def test_sent_confirm_blocks_resume_even_when_native_request_never_arrives(self):
        preparation = self.report("Confirm")
        report = {"stage": "Confirm", "input_attempts": 0, "session_pid": 123,
                  "fresh_guard": {"frame_sha256": "a" * 64}}
        request = {"action": "training-confirm-one-bronze", "frame_sha256": "a" * 64, "nonce": "b" * 32}
        def send(_):
            raise TimeoutError("never reached native endpoint")
        self.assertFalse(guarded_send(report, True, "Confirm", ACTION_POINTS["Confirm"], lambda: request, send,
                                     lambda: write_consumption_pending(self.private / "confirm-report.json", report)))
        self.assertFalse(Path(self.session["budget_paths"]["training-confirm-one-bronze"]).exists())
        with self.assertRaisesRegex(ValueError, "pending"):
            validate_preparation_resume(preparation, "Confirm", 123, self.session, self.model)

    def test_ledger_mismatch_frame_pid_count_nonce_or_changed_path_refuses(self):
        report = self.report("One")
        for change in ({"Attempts": 0}, {"LastPid": 456}, {"LastFrameSha256": "a" * 64},
                       {"LastNonce": "b" * 32}, {"MaxAttempts": 2}):
            self.ledger("training-bronze-plus", report["fresh_guard"]["frame_sha256"], **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_preparation_resume(report, "One", 123, self.session, self.model)
        session = copy.deepcopy(self.session)
        session["budget_paths"]["training-bronze-plus"] = str(self.private / "new-budget.json")
        with self.assertRaises(ValueError):
            validate_preparation_resume(report, "One", 123, session, self.model)

    def test_old_guard_ocr_frame_and_resource_tampering_refuses(self):
        original = self.report("Confirm")
        cases = []
        altered = copy.deepcopy(original)
        altered["fresh_guard"]["recognitions"].pop()
        cases.append(altered)
        altered = copy.deepcopy(original)
        altered["fresh_guard"]["ocr"]["inventory"]["recognition"] = recognition("11360", box=[739, 357, 59, 21])
        cases.append(altered)
        altered = copy.deepcopy(original)
        altered["fresh_guard"]["evidence"]["bronze_quantity"] = 2
        cases.append(altered)
        for report in cases:
            with self.assertRaises(ValueError):
                validate_preparation_resume(report, "Confirm", 123, self.session, self.model)
        self.base_path.write_bytes(b"changed baseline capture")
        with self.assertRaises(ValueError):
            validate_preparation_resume(original, "Confirm", 123, self.session, self.model)

    def test_new_current_guard_resource_change_blocks_before_next_input(self):
        original = self.report("Confirm")
        observation = copy.deepcopy(original["fresh_guard"])
        observation.update(stage="One", recognitions=complete("One"))
        verify_resume_resources("One", observation, self.baseline)
        for changes in ({"inventory": 22613}, {"level": 3}, {"bronze_quantity": 2}, {"frame_sha256": "a" * 64}):
            changed = copy.deepcopy(observation)
            changed["evidence"].update(changes)
            with self.assertRaises(ValueError):
                verify_resume_resources("One", changed, self.baseline)

    def test_baseline_source_is_reprocessed_instead_of_trusting_reported_numbers(self):
        entry = {"image": str(self.base_path), "sha256": self.base_sha}
        with patch("tools.run_daily_training.read_png", return_value=object()), \
             patch("tools.run_daily_training.observe_resources", return_value=({"inventory": 22613, "level": 2, "remaining_exp": 40}, {})), \
             self.assertRaises(ValueError):
            verify_baseline_image(self.baseline, entry, object())

    def test_numeric_replay_always_uses_hash_checked_historical_dataset(self):
        with patch("tools.run_daily_training.load_guard", return_value=({"fixed": True}, {"historical": True}, "history-digest")) as load, \
             patch("tools.run_daily_training.replay_numeric_fields", return_value=[{"passed": True}]) as fields, \
             patch("tools.run_daily_training.replay_numeric_negatives", return_value=[{"passed": True}]) as negatives:
            digest, _, _ = historical_numeric_replay(object())
            self.assertEqual(digest, "history-digest")
            self.assertEqual(load.call_count, 2)
            self.assertEqual(fields.call_args.args[:2], ({"fixed": True}, {"historical": True}))
            self.assertEqual(fields.call_args.args[2], negatives.call_args.args[0])


class PostSpendResumeTests(PreparationResumeTests):
    """Reuses the preparation fixtures; a sent Confirm is never resent or cleared."""
    def confirm_report(self):
        self.session["budget_paths"]["training-result-close"] = str(self.private / "daily_navigation/training-result-close.json")
        frame = "a" * 64
        evidence = {**self.baseline, "frame_sha256": frame, "confirmed_items": [{"ticket_type": "basic_bronze", "quantity": 1}],
                    "final_cost_proof": {"kind": "single_bronze_cost_panel", "frame_sha256": frame, "sole_material": "basic_bronze",
                                         "quantity": 1, "complete_panel": True, "guard_verified": True}}
        report = {"runner": "SC.DailyTraining", "stage": "Confirm", "passed": False,
                  "reason": "postcondition_unverified_no_retry", "input_enabled": True, "input_attempts": 1,
                  "session_pid": 123, "uncertain_consumption": True, "resource_result_verified": False,
                  "ocr_model": self.model, "response": {"ok": True, "attempted": True, "reason": "native_attempted"},
                  "node_trace": [{"name": PREFIX + "Confirm." + s, "completed": True, "action_success": True} for s in ("Observe", "Click")],
                  "baseline": dict(self.baseline), "fresh_guard": {"stage": "Confirm", "guard_verified": True,
                  "recognitions": complete("Confirm"), "evidence": evidence, "frame_sha256": frame},
                  "request_binding": {"action": "training-confirm-one-bronze", "frame_sha256": frame, "nonce": "c" * 32}}
        path = self.private / "confirm-report.json"
        report["consumption_pending"] = write_consumption_pending(path, report)
        path.write_text(json.dumps(report), encoding="utf-8")
        self.ledger("training-confirm-one-bronze", frame)
        return path, report

    def test_result_close_follows_sent_confirm_only_with_matching_witness_and_ledger(self):
        path, report = self.confirm_report()
        result = validate_post_spend_resume(path, report, "Result", 123, self.session, self.model)
        self.assertEqual(result["baseline"], self.baseline)
        self.assertEqual(result["previous_stage"], "Confirm")
        for stage in ("Zero", "One", "Confirm"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                validate_post_spend_resume(path, report, stage, 123, self.session, self.model)
        for change in ({"uncertain_consumption": False}, {"input_attempts": 2}, {"session_pid": 456},
                       {"response": {"ok": False, "attempted": True, "reason": "x"}}, {"consumption_pending": None},
                       {"stage": "One"}, {"passed": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_post_spend_resume(path, {**report, **change}, "Result", 123, self.session, self.model)

    def test_missing_pending_or_changed_confirm_ledger_refuses(self):
        path, report = self.confirm_report()
        self.ledger("training-confirm-one-bronze", "a" * 64, LastNonce="d" * 32)
        with self.assertRaises(ValueError):
            validate_post_spend_resume(path, report, "Result", 123, self.session, self.model)
        self.ledger("training-confirm-one-bronze", "a" * 64)
        (self.private / "daily_navigation/training-confirm-one-bronze.runner-pending.json").unlink()
        with self.assertRaisesRegex(ValueError, "pending"):
            validate_post_spend_resume(path, report, "Result", 123, self.session, self.model)

    def test_after_requires_result_close_chain_and_net_one_ticket(self):
        path, confirm = self.confirm_report()
        spend = validate_post_spend_resume(path, confirm, "Result", 123, self.session, self.model)
        guard = {"stage": "Result", "frame_sha256": "e" * 64}
        self.ledger("training-result-close", "e" * 64)
        result_report = {**confirm, "stage": "Result", "uncertain_consumption": True, "fresh_guard": guard,
                         "node_trace": [{"name": PREFIX + "Result." + s, "completed": True, "action_success": True} for s in ("Observe", "Click")],
                         "post_spend_resume": {**spend, "current_stage": "Result", "consumption_retried": False}}
        result_path = self.private / "result-report.json"
        result_path.write_text(json.dumps(result_report), encoding="utf-8")
        chained = validate_post_spend_resume(result_path, result_report, "After", 123, self.session, self.model)
        self.assertEqual(chained["previous_stage"], "Result")
        with self.assertRaises(ValueError):
            validate_post_spend_resume(path, confirm, "After", 123, self.session, self.model)
        observation = {"stage": "After", "guard_verified": True, "recognitions": complete("After"),
                       "foreground": {"state": "no_known_popup", "popup": None, "complete_contracts": [], "partial_contracts": []},
                       "evidence": {**self.baseline, "inventory": 22613, "level": 3, "remaining_exp": 40,
                                    "bronze_quantity": 0, "silver_quantity": 0}}
        verify_post_spend_observation("After", copy.deepcopy(observation), self.baseline)
        for change in ({"inventory": 22614}, {"inventory": 22612}, {"level": 2}):
            bad = copy.deepcopy(observation)
            bad["evidence"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_post_spend_observation("After", bad, self.baseline)

if __name__ == "__main__":
    unittest.main()
