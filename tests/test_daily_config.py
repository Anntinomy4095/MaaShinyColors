from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent.core.daily_config import DailyConfig, load_json, plan_daily_tasks


ROOT = Path(__file__).resolve().parents[1]


def config():
    return load_json(ROOT / "daily_tasks.json")


def observation():
    return {
        "source": "manual",
        "inventory": {"coins": 1000, "basic_training_tickets": 5,
                      "match_fes_participation_rights": 25, "grade_fes_participation_rights": 25},
        "shop": {"state": "ready", "offer_scope": "daily_discount", "offers": [
            {"offer_rank": rank, "item_id": f"item-{rank}", "item_type": "ordinary_item",
             "currency": "coins", "unit_price": 10, "original_unit_price": 20,
             "discount_evidence": "人工观察到原价20及现价10", "available_quantity": 2, "purchased": False}
            for rank in (1, 2, 3)]},
        "training": {"state": "ready", "cards": [
            {"card_id": "support-A", "card_type": "support_s", "completed": False,
             "max_level": False, "trainable": True}]},
        "match_fes": {"state": "ready", "resource_pool": "match", "stage_list_complete": False, "available_stages": [
            {"stage_id": "area-1-stage-1", "stage_order": 1, "skip_available": True, "participation_cost_per_run": 1}]},
        "grade_fes": {"state": "ready", "resource_pool": "grade", "available_difficulties": [
            {"difficulty_id": "observed-difficulty-1", "available": True, "participation_cost_per_run": 1}]},
    }


def enabled_config():
    data = config()
    data["budgets"] = {"coins": 100, "basic_training_tickets": 1, "fes_participation_rights": 25}
    data["shop"].update(enabled=True, coin_budget=100, items=[
        {"offer_rank": rank, "item_id": f"item-{rank}", "item_type": "ordinary_item",
         "quantity": 1, "max_unit_price": 10} for rank in (2, 1)])
    data["training"].update(enabled=True, card_id="support-A", ticket_budget=1)
    data["match_fes"].update(enabled=True, stage_selection="explicit", stage_id="area-1-stage-1", participation_budget=20)
    data["grade_fes"].update(enabled=True, difficulty_id="observed-difficulty-1", count=5, participation_budget=5)
    return data


def task(report, name):
    return next(entry for entry in report["tasks"] if entry["task"] == name)


class DailyConfigTests(unittest.TestCase):
    def test_default_tasks_disabled_and_budgets_zero(self):
        data = config()
        report = plan_daily_tasks(data)
        self.assertTrue(all(entry["status"] == "skip" for entry in report["tasks"]))
        self.assertEqual(report["reservations"], {"coins": 0, "basic_training_tickets": 0, "fes_participation_rights": 0})
        self.assertFalse(report["input_authorized"])
        self.assertFalse(report["input_enabled"])
        self.assertFalse(data["premium_currency_allowed"])

    def test_plan_has_exact_costs_and_never_authorizes_input(self):
        data, obs = enabled_config(), observation()
        before = copy.deepcopy((data, obs))
        report = plan_daily_tasks(DailyConfig.from_dict(data), obs)
        self.assertEqual((data, obs), before)
        self.assertTrue(all(entry["status"] == "planned" for entry in report["tasks"]))
        self.assertEqual(report["reservations"], {"coins": 20, "basic_training_tickets": 1, "fes_participation_rights": 25})
        self.assertEqual(task(report, "match_fes")["details"]["fes_participation_rights"], 20)
        self.assertEqual(task(report, "training")["details"]["ticket_quantity"], 1)
        for entry in report["tasks"]:
            self.assertFalse(entry["input_authorized"])
            self.assertFalse(entry["input_enabled"])

    def test_unknown_fields_modules_types_and_premium_rejected(self):
        cases = [((), "unknown_task", {}), ((), "premium_currency_allowed", True),
                 (("shop",), "enabled", "yes"), (("training",), "ticket_type", "premium_ticket"),
                 (("match_fes",), "mode", "climb"), (("grade_fes",), "difficulty_id", "auto")]
        for path, key, value in cases:
            data = config()
            target = data
            for part in path:
                target = target[part]
            target[key] = value
            with self.subTest(path=path, key=key), self.assertRaises(ValueError):
                DailyConfig.from_dict(data)
        data = enabled_config()
        data["shop"]["items"][0]["item_type"] = "unknown_item_type"
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)

    def test_boolean_negative_float_nan_and_unknown_version_rejected(self):
        for value in (True, -1, 1.0, float("nan"), float("inf"), "20"):
            for section, key in (("budgets", "coins"), ("match_fes", "count"), ("training", "ticket_budget")):
                data = config()
                data[section][key] = value
                with self.subTest(value=value, section=section), self.assertRaises(ValueError):
                    DailyConfig.from_dict(data)
        for value in (True, 2, 1.0):
            data = config()
            data["version"] = value
            with self.assertRaises(ValueError):
                DailyConfig.from_dict(data)

    def test_public_model_is_revalidated(self):
        model = DailyConfig(config())
        model.data["premium_currency_allowed"] = True
        with self.assertRaises(ValueError):
            plan_daily_tasks(model)

    def test_manual_observation_contract_rejected(self):
        for change in ({"source": "recognition"}, {"unknown_task": {}}, {"input_authorized": True}):
            obs = {**observation(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                plan_daily_tasks(config(), obs)
        obs = observation()
        obs["inventory"]["match_fes_participation_rights"] = True
        with self.assertRaises(ValueError):
            plan_daily_tasks(config(), obs)

    def test_missing_observation_stops_and_does_not_reserve(self):
        report = plan_daily_tasks(enabled_config())
        self.assertTrue(all(entry["status"] == "stop" and entry["reason"] for entry in report["tasks"]))
        self.assertEqual(sum(report["reservations"].values()), 0)

    def test_partial_inventory_never_invents_unknown_resource_counts(self):
        obs = observation()
        obs["inventory"] = {"basic_training_tickets": 5}
        report = plan_daily_tasks(enabled_config(), obs)
        self.assertEqual(task(report, "training")["status"], "planned")
        for name in ("shop", "match_fes", "grade_fes"):
            self.assertEqual(task(report, name)["status"], "stop")
        self.assertEqual(report["reservations"], {"coins": 0, "basic_training_tickets": 1, "fes_participation_rights": 0})

    def test_complete_unknown_and_reset_do_not_spend(self):
        for state, expected in (("complete", "skip"), ("unknown", "stop"), ("reset", "stop")):
            obs = observation()
            for name in ("shop", "training", "match_fes", "grade_fes"):
                obs[name]["state"] = state
            report = plan_daily_tasks(enabled_config(), obs)
            self.assertTrue(all(entry["status"] == expected for entry in report["tasks"]))
            self.assertEqual(sum(report["reservations"].values()), 0)

    def test_shop_order_and_explicit_third(self):
        data = enabled_config()
        report = plan_daily_tasks(data, observation())
        self.assertEqual([entry["offer_rank"] for entry in task(report, "shop")["details"]["purchases"]], [1, 2])
        third = {"offer_rank": 3, "item_id": "item-3", "item_type": "ordinary_item", "quantity": 1, "max_unit_price": 10}
        data["shop"]["items"].append(third)
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)
        data["shop"]["include_third"] = True
        self.assertEqual(task(plan_daily_tasks(data, observation()), "shop")["details"]["coins"], 30)

    def test_shop_missing_binding_duplicate_and_coordinate_rejected(self):
        for mutation in ("missing", "duplicate", "coordinates"):
            data = enabled_config()
            if mutation == "missing":
                data["shop"]["items"].pop()
            elif mutation == "duplicate":
                data["shop"]["items"].append(copy.deepcopy(data["shop"]["items"][0]))
            else:
                data["shop"]["items"][0]["position"] = [100, 200]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                DailyConfig.from_dict(data)

    def test_shop_identity_discount_currency_price_quantity_and_budgets(self):
        for key, value in (("item_id", "other"), ("item_type", "fes_entry_ticket"), ("currency", "premium"),
                           ("currency", "unknown"), ("unit_price", 11), ("original_unit_price", 10),
                           ("available_quantity", 0), ("offer_rank", 4)):
            obs = observation()
            obs["shop"]["offers"][0][key] = value
            entry = task(plan_daily_tasks(enabled_config(), obs), "shop")
            with self.subTest(key=key):
                self.assertEqual(entry["status"], "stop")
        for section, key, value in (("shop", "coin_budget", 19), ("budgets", "coins", 19)):
            data = enabled_config()
            data[section][key] = value
            self.assertEqual(task(plan_daily_tasks(data, observation()), "shop")["status"], "stop")
        obs = observation()
        obs["inventory"]["coins"] = 19
        self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "shop")["status"], "stop")

    def test_observed_invalid_prices_empty_evidence_and_duplicate_rejected(self):
        for key, value in (("unit_price", True), ("unit_price", -1), ("unit_price", float("nan")),
                           ("original_unit_price", 0), ("discount_evidence", ""), ("available_quantity", 1.2)):
            obs = observation()
            obs["shop"]["offers"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                plan_daily_tasks(config(), obs)
        obs = observation()
        obs["shop"]["offers"].append(copy.deepcopy(obs["shop"]["offers"][0]))
        with self.assertRaises(ValueError):
            plan_daily_tasks(config(), obs)

    def test_shop_preserves_balance_after_purchase(self):
        data = enabled_config()
        data["shop"]["coin_reserve"] = 981
        report = plan_daily_tasks(data, observation())
        self.assertEqual(task(report, "shop")["status"], "stop")
        self.assertEqual(report["reservations"]["coins"], 0)
        data["shop"]["coin_reserve"] = 980
        self.assertEqual(task(plan_daily_tasks(data, observation()), "shop")["status"], "planned")
        data["shop"]["coin_reserve"] = True
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)

    def test_shop_daily_discounts_cannot_fall_back_to_regular_catalog(self):
        for scope in ("regular", "unknown"):
            obs = observation()
            obs["shop"]["offer_scope"] = scope
            report = plan_daily_tasks(enabled_config(), obs)
            self.assertEqual(task(report, "shop")["status"], "stop")
            self.assertEqual(report["reservations"]["coins"], 0)
        data = enabled_config()
        data["shop"]["purchase_scope"] = "any_discount"
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)
        obs = observation()
        obs["shop"]["offer_scope"] = "other_shop"
        with self.assertRaises(ValueError):
            plan_daily_tasks(enabled_config(), obs)

    def test_shop_skips_only_explicitly_purchased_items(self):
        obs = observation()
        obs["shop"]["offers"][0].update(purchased=True, available_quantity=0)
        report = plan_daily_tasks(enabled_config(), obs)
        self.assertEqual(task(report, "shop")["status"], "planned")
        self.assertEqual(report["reservations"]["coins"], 10)
        obs["shop"]["offers"][1].update(purchased=True, available_quantity=0)
        report = plan_daily_tasks(enabled_config(), obs)
        self.assertEqual(task(report, "shop")["status"], "skip")
        self.assertEqual(report["reservations"]["coins"], 0)
        obs["shop"]["offers"][0]["available_quantity"] = 1
        with self.assertRaises(ValueError):
            plan_daily_tasks(enabled_config(), obs)

    def test_training_exact_one_ticket_and_explicit_card_required(self):
        for key, value in (("ticket_quantity", 0), ("ticket_quantity", 2), ("ticket_quantity", True),
                           ("card_id", None), ("card_id", "")):
            data = enabled_config()
            data["training"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                DailyConfig.from_dict(data)
        data = enabled_config()
        data["training"]["candidate_card_ids"] = ["support-A"]
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)

    def test_training_completed_maxed_untrainable_and_missing(self):
        for key, value, expected in (("completed", True, "skip"), ("max_level", True, "stop"),
                                     ("trainable", False, "stop"), ("card_id", "other", "stop")):
            obs = observation()
            obs["training"]["cards"][0][key] = value
            report = plan_daily_tasks(enabled_config(), obs)
            with self.subTest(key=key):
                self.assertEqual(task(report, "training")["status"], expected)
                self.assertEqual(report["reservations"]["basic_training_tickets"], 0)

    def test_training_candidate_set_is_explicit_and_ordered(self):
        data, obs = enabled_config(), observation()
        data["training"].update(card_id=None, candidate_card_ids=["support-B", "support-A"])
        obs["training"]["cards"].append({"card_id": "support-B", "card_type": "support_s", "completed": False, "max_level": False, "trainable": True})
        self.assertEqual(task(plan_daily_tasks(data, obs), "training")["details"]["card_id"], "support-B")
        obs["training"]["cards"].pop()
        self.assertEqual(task(plan_daily_tasks(data, obs), "training")["status"], "stop")

    def test_training_budget_and_inventory_caps(self):
        for section, key in (("training", "ticket_budget"), ("budgets", "basic_training_tickets")):
            data = enabled_config()
            data[section][key] = 0
            self.assertEqual(task(plan_daily_tasks(data, observation()), "training")["status"], "stop")
        obs = observation()
        obs["inventory"]["basic_training_tickets"] = 0
        self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "training")["status"], "stop")

    def test_match_stage_missing_or_skip_unavailable_requires_takeover(self):
        for key, value in (("stage_id", "other-stage"), ("skip_available", False)):
            obs = observation()
            obs["match_fes"]["available_stages"][0][key] = value
            report = plan_daily_tasks(enabled_config(), obs)
            with self.subTest(key=key):
                self.assertEqual(task(report, "match_fes")["status"], "stop")
                self.assertEqual(report["reservations"]["fes_participation_rights"], 5)
                self.assertEqual(report["inventory_reservations"]["match_fes_participation_rights"], 0)
        obs = observation()
        obs["match_fes"]["available_stages"] = []
        self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "match_fes")["status"], "stop")

    def test_match_stage_binding_is_required_and_never_automatic(self):
        for stage_id in (None, "", "auto", "any", "random"):
            data = enabled_config()
            data["match_fes"]["stage_id"] = stage_id
            with self.subTest(stage_id=stage_id), self.assertRaises(ValueError):
                DailyConfig.from_dict(data)
        obs = observation()
        obs["match_fes"]["available_stages"].append({
            "stage_id": "another-available-stage", "stage_order": 2, "skip_available": True, "participation_cost_per_run": 1})
        obs["match_fes"]["available_stages"][0]["skip_available"] = False
        self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "match_fes")["status"], "stop")

    def test_match_reset_unknown_stop_without_participation_reservation(self):
        for state in ("reset", "unknown"):
            obs = observation()
            obs["match_fes"]["state"] = state
            report = plan_daily_tasks(enabled_config(), obs)
            with self.subTest(state=state):
                self.assertEqual(task(report, "match_fes")["status"], "stop")
                self.assertEqual(report["inventory_reservations"]["match_fes_participation_rights"], 0)

    def test_match_uses_selected_stage_observed_participation_cost(self):
        data, obs = enabled_config(), observation()
        data["match_fes"].update(count=3, participation_budget=6)
        obs["match_fes"]["available_stages"][0]["participation_cost_per_run"] = 2
        report = plan_daily_tasks(data, obs)
        details = task(report, "match_fes")["details"]
        self.assertEqual(details["fes_participation_rights"], 6)
        self.assertEqual(details["stage_id"], "area-1-stage-1")
        self.assertEqual(details["participation_cost_per_run"], 2)

    def test_grade_explicit_identity_and_no_fallback(self):
        for identity, available in (("other", True), ("observed-difficulty-1", False)):
            obs = observation()
            obs["grade_fes"]["available_difficulties"][0].update(difficulty_id=identity, available=available)
            self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "grade_fes")["status"], "stop")
        data = enabled_config()
        data["grade_fes"]["difficulty_id"] = None
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)

    def test_fes_shared_budget_is_not_reused(self):
        data = enabled_config()
        data["budgets"]["fes_participation_rights"] = 20
        report = plan_daily_tasks(data, observation())
        self.assertEqual(task(report, "match_fes")["status"], "planned")
        self.assertEqual(task(report, "grade_fes")["status"], "stop")
        self.assertEqual(report["reservations"]["fes_participation_rights"], 20)
        self.assertIn("全局", task(report, "grade_fes")["reason"])

    def test_fes_independent_budgets_inventory_and_observed_cost(self):
        for name, amount in (("match_fes", 19), ("grade_fes", 4)):
            data = enabled_config()
            data[name]["participation_budget"] = amount
            self.assertEqual(task(plan_daily_tasks(data, observation()), name)["status"], "stop")
            obs = observation()
            obs["inventory"][f"{name}_participation_rights"] = amount
            self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), name)["status"], "stop")
        obs = observation()
        obs["grade_fes"]["available_difficulties"][0]["participation_cost_per_run"] = 2
        self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "grade_fes")["status"], "stop")
        obs = observation()
        obs["inventory"]["match_fes_participation_rights"] = 20
        obs["inventory"]["grade_fes_participation_rights"] = 5
        self.assertEqual(plan_daily_tasks(enabled_config(), obs)["reservations"]["fes_participation_rights"], 25)

    def test_match_default_latest_twenty_requires_complete_stage_evidence(self):
        data, obs = config(), observation()
        self.assertEqual(data["match_fes"]["stage_selection"], "latest")
        self.assertIsNone(data["match_fes"]["stage_id"])
        self.assertEqual(data["match_fes"]["count"], 20)
        data["match_fes"].update(enabled=True, participation_budget=20)
        data["budgets"]["fes_participation_rights"] = 20
        self.assertEqual(task(plan_daily_tasks(data, obs), "match_fes")["status"], "stop")
        obs["match_fes"]["stage_list_complete"] = True
        report = plan_daily_tasks(data, obs)
        self.assertEqual(task(report, "match_fes")["status"], "planned")
        self.assertEqual(task(report, "match_fes")["details"]["count"], 20)
        self.assertEqual(report["reservations"]["fes_participation_rights"], 20)
        data["match_fes"]["participation_budget"] = 19
        self.assertEqual(task(plan_daily_tasks(data, obs), "match_fes")["status"], "stop")
        data["match_fes"]["participation_budget"] = 20
        data["budgets"]["fes_participation_rights"] = 19
        self.assertEqual(task(plan_daily_tasks(data, obs), "match_fes")["status"], "stop")

    def test_match_latest_uses_global_order_and_never_falls_back(self):
        data, obs = enabled_config(), observation()
        data["match_fes"].update(stage_selection="latest", stage_id=None)
        obs["match_fes"]["stage_list_complete"] = True
        newest = {"stage_id": "area-2-stage-3", "stage_order": 9,
                  "skip_available": True, "participation_cost_per_run": 1}
        old = obs["match_fes"]["available_stages"][0]
        for stages in ([old, newest], [newest, old]):
            obs["match_fes"]["available_stages"] = stages
            report = plan_daily_tasks(data, obs)
            self.assertEqual(task(report, "match_fes")["details"]["stage_id"], newest["stage_id"])
        newest["skip_available"] = False
        self.assertEqual(task(plan_daily_tasks(data, obs), "match_fes")["status"], "stop")
        obs["match_fes"]["available_stages"] = []
        self.assertEqual(task(plan_daily_tasks(data, obs), "match_fes")["status"], "stop")

    def test_match_stage_selection_and_global_order_contract_are_strict(self):
        data = enabled_config()
        data["match_fes"]["stage_selection"] = "latest"
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)
        data["match_fes"].update(stage_selection="guess", stage_id=None)
        with self.assertRaises(ValueError):
            DailyConfig.from_dict(data)
        obs = observation()
        obs["match_fes"]["available_stages"].append({
            "stage_id": "different-stage", "stage_order": 1,
            "skip_available": True, "participation_cost_per_run": 1})
        with self.assertRaises(ValueError):
            plan_daily_tasks(config(), obs)
        for key, value in (("stage_order", True), ("stage_order", 0), ("stage_order", 1.5)):
            obs = observation()
            obs["match_fes"]["available_stages"][0][key] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan_daily_tasks(config(), obs)
        obs = observation()
        obs["match_fes"]["stage_list_complete"] = "yes"
        with self.assertRaises(ValueError):
            plan_daily_tasks(config(), obs)

    def test_fes_shared_pool_inventory_is_not_reused_across_modes(self):
        data, obs = enabled_config(), observation()
        data["match_fes"].update(count=9, participation_budget=9)
        data["grade_fes"].update(count=5, participation_budget=5)
        obs["inventory"] = {"fes_participation_rights": 13}
        for name in ("match_fes", "grade_fes"):
            obs[name]["resource_pool"] = "shared"
        report = plan_daily_tasks(data, obs)
        self.assertEqual(task(report, "match_fes")["status"], "planned")
        self.assertEqual(task(report, "grade_fes")["status"], "stop")
        self.assertIn("观测库存", task(report, "grade_fes")["reason"])
        self.assertEqual(report["inventory_reservations"], {"fes_participation_rights": 9})
        self.assertEqual(report["reservations"]["fes_participation_rights"], 9)
        obs["inventory"]["fes_participation_rights"] = 14
        report = plan_daily_tasks(data, obs)
        self.assertEqual(task(report, "grade_fes")["status"], "planned")
        self.assertEqual(report["inventory_reservations"]["fes_participation_rights"], 14)

    def test_fes_independent_pools_do_not_deduct_each_other(self):
        data, obs = enabled_config(), observation()
        obs["inventory"] = {"match_fes_participation_rights": 20, "grade_fes_participation_rights": 5}
        report = plan_daily_tasks(data, obs)
        self.assertEqual(task(report, "match_fes")["status"], "planned")
        self.assertEqual(task(report, "grade_fes")["status"], "planned")
        self.assertEqual(report["inventory_reservations"], obs["inventory"])
        self.assertEqual(report["reservations"]["fes_participation_rights"], 25)

    def test_fes_pool_evidence_missing_unknown_or_cross_mode_stops(self):
        for name, wrong_pool in (("match_fes", "grade"), ("grade_fes", "match")):
            for pool in (None, "unknown", wrong_pool):
                obs = observation()
                if pool is None:
                    del obs[name]["resource_pool"]
                else:
                    obs[name]["resource_pool"] = pool
                report = plan_daily_tasks(enabled_config(), obs)
                with self.subTest(name=name, pool=pool):
                    self.assertEqual(task(report, name)["status"], "stop")
                    self.assertEqual(report["inventory_reservations"][f"{name}_participation_rights"], 0)
        obs = observation()
        obs["match_fes"]["resource_pool"] = "shared"
        # Independent inventory cannot stand in for a missing shared pool count.
        self.assertEqual(task(plan_daily_tasks(enabled_config(), obs), "match_fes")["status"], "stop")

    def test_fes_resource_contract_rejects_legacy_tickets_and_recovery(self):
        for section, key in (("budgets", "fes_tickets"), ("match_fes", "ticket_budget"), ("grade_fes", "ticket_budget")):
            data = config()
            data[section][key] = 1
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                DailyConfig.from_dict(data)
        for key in ("fes_tickets", "fes_recovery_tickets"):
            obs = observation()
            obs["inventory"][key] = 10
            with self.subTest(key=key), self.assertRaises(ValueError):
                plan_daily_tasks(config(), obs)
        obs = observation()
        obs["match_fes"]["available_stages"][0]["ticket_cost_per_run"] = 1
        with self.assertRaises(ValueError):
            plan_daily_tasks(config(), obs)

    def test_match_stage_duplicates_and_invalid_costs_rejected(self):
        obs = observation()
        obs["match_fes"]["available_stages"].append(copy.deepcopy(obs["match_fes"]["available_stages"][0]))
        with self.assertRaises(ValueError):
            plan_daily_tasks(config(), obs)
        for cost in (True, 0, -1, 1.5, float("nan")):
            obs = observation()
            obs["match_fes"]["available_stages"][0]["participation_cost_per_run"] = cost
            with self.subTest(cost=cost), self.assertRaises(ValueError):
                plan_daily_tasks(config(), obs)

    def test_strict_json_duplicate_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            for content in ('{"version":1,"version":1}', '{"coins":NaN}', '{"coins":Infinity}'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_json(path)

    def test_cli_creates_report_and_refuses_overwrite_or_input(self):
        with tempfile.TemporaryDirectory() as folder:
            input_path, output = Path(folder) / "config.json", Path(folder) / "report.json"
            input_path.write_text(json.dumps(config()), encoding="utf-8")
            command = [sys.executable, str(ROOT / "tools/plan_daily_tasks.py"), "--config", str(input_path), "--output", str(output)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            report = load_json(output)
            self.assertTrue(all(entry["status"] == "skip" for entry in report["tasks"]))
            saved = output.read_bytes()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(output.read_bytes(), saved)
            before = input_path.read_bytes()
            command[-1] = str(input_path)
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(input_path.read_bytes(), before)

    def test_cli_invalid_input_leaves_no_report(self):
        with tempfile.TemporaryDirectory() as folder:
            input_path, output = Path(folder) / "config.json", Path(folder) / "report.json"
            input_path.write_text('{"version": true}', encoding="utf-8")
            run = subprocess.run([sys.executable, str(ROOT / "tools/plan_daily_tasks.py"), "--config", str(input_path), "--output", str(output)], capture_output=True)
            self.assertEqual(run.returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
