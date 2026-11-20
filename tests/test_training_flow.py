import copy
import unittest
from unittest.mock import Mock, patch

from agent.core.training_flow import (ACTION_POINTS, ANCHORS, CARD_ID, PREFIX, STAGES,
    TrainingFlow, fixed_pipeline, stage_matches, validate_resource_evidence)


class TrainingFlowTests(unittest.TestCase):
    def hits(self, stage):
        return [{"scene": PREFIX + stage + "." + anchor, "hit": True, "score": .98,
                 "roi": roi, "raw_detail": {"filtered": [{"box": roi}]}}
                for anchor, roi in ANCHORS[stage].items()]

    def evidence(self, stage):
        item = {"card_id": CARD_ID, "card_type": "support_s", "ticket_type": "basic_bronze",
                "inventory": 22611, "level": 2, "max_level": 80,
                "bronze_quantity": 0 if stage in ("Zero", "After") else 1,
                "silver_quantity": 0, "exp": 50}
        if stage == "Confirm":
            item["frame_sha256"] = "a" * 64
            item["confirmed_items"] = [{"ticket_type": "basic_bronze", "quantity": 1}]
            item["final_cost_proof"] = {"kind": "single_bronze_cost_panel", "frame_sha256": "a" * 64,
                "sole_material": "basic_bronze", "quantity": 1, "complete_panel": True, "guard_verified": True}
        if stage == "After":
            item.update(before_inventory=22612, before_level=1)
        return item

    def test_default_pipeline_disables_every_input_and_has_no_retry_edges(self):
        pipeline = fixed_pipeline()
        for stage, point in ACTION_POINTS.items():
            base = PREFIX + stage + "."
            node = pipeline[base + "Click"]
            self.assertFalse(node["enabled"])
            self.assertEqual(node["target"], list(point) + [1, 1])
            self.assertNotIn(base + "Click", pipeline[base + "Observe"]["next"])
            self.assertNotIn("next", node)
            self.assertEqual(node["on_error"], [PREFIX + "Error"])
        for stage in STAGES:
            self.assertEqual(pipeline[PREFIX + stage + ".Unknown"]["action"], "StopTask")

    def test_disk_pipeline_cannot_enable_click_or_reduce_guard(self):
        for key, value in (("enabled", True), ("all_of", ["SC.Training.One.Submit"]), ("target", [1, 1, 1, 1])):
            pipeline = fixed_pipeline()
            pipeline[PREFIX + "One.Click"][key] = value
            with patch("agent.core.training_flow.json.loads", return_value=pipeline):
                with self.assertRaises(ValueError):
                    fixed_pipeline(True)
        with self.assertRaises(ValueError):
            fixed_pipeline(1)

    def test_each_stage_requires_every_fixed_unique_anchor(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertTrue(stage_matches(stage, self.hits(stage)))
                for index in range(len(ANCHORS[stage])):
                    hits = self.hits(stage)
                    hits[index]["hit"] = False
                    self.assertFalse(stage_matches(stage, hits))
                self.assertFalse(stage_matches(stage, self.hits(stage)[:-1]))
                self.assertFalse(stage_matches(stage, self.hits(stage) + self.hits(stage)[:1]))

    def test_ambiguous_target_bad_confidence_or_moved_crop_refuses(self):
        for mutation in ("double", "nan", "moved", "low"):
            hits = self.hits("Confirm")
            target = hits[-1]
            if mutation == "double": target["raw_detail"]["filtered"] *= 2
            if mutation == "nan": target["score"] = float("nan")
            if mutation == "moved": target["raw_detail"]["filtered"][0]["box"] = [1, 2, 3, 4]
            if mutation == "low": target["score"] = .949
            self.assertFalse(stage_matches("Confirm", hits))

    def test_identity_rois_do_not_include_level(self):
        self.assertEqual(ANCHORS["Zero"]["CardName"], ANCHORS["After"]["CardName"])
        self.assertEqual(ANCHORS["Zero"]["CardName"][1], 96)
        self.assertLess(sum(ANCHORS["Zero"]["CardName"][1::2]), 140)

    def test_preview_contract_does_not_infer_hidden_gold_from_exp(self):
        for stage in ("Zero", "One"):
            self.assertEqual(validate_resource_evidence(stage, self.evidence(stage))["card_id"], CARD_ID)
        evidence = self.evidence("Confirm")
        del evidence["final_cost_proof"]
        evidence.update(gold_quantity=0, silver_quantity=0)
        with self.assertRaises(ValueError):
            validate_resource_evidence("Confirm", evidence)

    def test_confirm_requires_single_bronze_complete_panel_on_same_fresh_frame(self):
        self.assertEqual(validate_resource_evidence("Confirm", self.evidence("Confirm"))["ticket_type"], "basic_bronze")
        for key, value in (("quantity", 2), ("quantity", True), ("sole_material", "silver"),
                           ("complete_panel", False), ("guard_verified", False), ("frame_sha256", "b" * 64)):
            evidence = self.evidence("Confirm")
            evidence["final_cost_proof"][key] = value
            with self.assertRaises(ValueError):
                validate_resource_evidence("Confirm", evidence)
        evidence = self.evidence("Confirm")
        evidence["confirmed_items"].append({"ticket_type": "gold", "quantity": 1})
        with self.assertRaises(ValueError):
            validate_resource_evidence("Confirm", evidence)

    def test_wrong_card_maxlevel_wrong_ticket_or_insufficient_inventory_refuses(self):
        for key, value in (("card_id", "another"), ("ticket_type", "silver"), ("level", 80), ("inventory", 0), ("bronze_quantity", 2)):
            evidence = self.evidence("One")
            evidence[key] = value
            with self.assertRaises(ValueError):
                validate_resource_evidence("One", evidence)

    def test_after_requires_exact_inventory_delta_levelup_and_reset_quantity(self):
        validate_resource_evidence("After", self.evidence("After"))
        for key, value in (("inventory", 22610), ("before_level", 2), ("bronze_quantity", 1)):
            evidence = self.evidence("After")
            evidence[key] = value
            with self.assertRaises(ValueError):
                validate_resource_evidence("After", evidence)

    def test_live_remaining_exp_replaces_unobserved_max_and_allows_exp_only_gain(self):
        evidence=self.evidence('One')
        del evidence['max_level']
        with self.assertRaises(ValueError):validate_resource_evidence('One',evidence)
        evidence['remaining_exp']=75
        validate_resource_evidence('One',evidence)
        for value in (0,True,-1):
            with self.assertRaises(ValueError):validate_resource_evidence('One',{**evidence,'remaining_exp':value})
        after=self.evidence('After')
        del after['max_level']
        after.update(before_level=2,before_remaining_exp=75,remaining_exp=25)
        validate_resource_evidence('After',after)
        with self.assertRaises(ValueError):validate_resource_evidence('After',{**after,'remaining_exp':75})

    def test_lost_send_response_cannot_retry(self):
        flow = TrainingFlow()
        send = Mock(side_effect=TimeoutError("response lost"))
        self.assertFalse(flow.act_once("Zero", ACTION_POINTS["Zero"], Mock(return_value="fresh"), send, execute=True))
        self.assertEqual(flow.attempts, {"Zero": "pending"})
        self.assertTrue(flow.stopped)
        self.assertFalse(flow.act_once("Zero", ACTION_POINTS["Zero"], Mock(), send, execute=True))
        self.assertEqual(send.call_count, 1)

    def test_order_requires_each_positive_postcondition_and_cannot_claim_mission(self):
        flow = TrainingFlow()
        for stage, following in zip(STAGES[:-1], STAGES[1:]):
            self.assertFalse(flow.act_once(following, ACTION_POINTS.get(following), Mock(), Mock(), execute=True))
            self.assertTrue(flow.act_once(stage, ACTION_POINTS[stage], Mock(return_value="fresh"), Mock(return_value={"ok": True, "attempted": True}), execute=True))
            self.assertFalse(flow.act_once(stage, ACTION_POINTS[stage], Mock(), Mock(), execute=True))
            self.assertTrue(flow.verify_next(following, self.hits(following), self.evidence(following)))
        self.assertTrue(flow.resource_result_verified)
        self.assertFalse(flow.daily_mission_verified)

    def test_bad_postcondition_stops_before_any_next_action(self):
        flow = TrainingFlow()
        self.assertTrue(flow.act_once("Zero", ACTION_POINTS["Zero"], Mock(), Mock(return_value={"ok": True, "attempted": True}), execute=True))
        self.assertFalse(flow.verify_next("Confirm", self.hits("Confirm"), self.evidence("Confirm")))
        self.assertTrue(flow.stopped)
        send = Mock()
        self.assertFalse(flow.act_once("One", ACTION_POINTS["One"], Mock(), send, execute=True))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
