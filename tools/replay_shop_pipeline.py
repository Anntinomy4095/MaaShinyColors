#!/usr/bin/env python3
"""每日商店完整Maa Pipeline离线回放：守卫、商品身份、数字OCR与证据规则；绝不连接浏览器或实时输入。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.shop_flow import (slot_full, validate_result, ACTIONS, AlreadyComplete, OFFER_RANKS, PREFIX, anchor_stage, confirm_evidence,
                                  fixed_pipeline, list_evidence, validate_after, validate_confirm, validate_offer_open)
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.shop_recognition import (DEFAULT_MODEL, DEFAULT_PROFILE, ShopOcr, confirm_evidence_of, identity_of,
                                   load_profile, observe, result_evidence_of, slots_of)

FRAMEWORK = "v5.12.3"
STAGE_ACTIONS = {stage: [a for a, (s, _) in ACTIONS.items() if s == stage] for stage in ("Home", "Premium", "List", "Confirm", "Result")}
# Offline evidence configuration (not the user's daily_tasks.json, which stays disabled by default).
REPLAY_CONFIG = {
    "version": 1, "premium_currency_allowed": False,
    "budgets": {"coins": 10000, "basic_training_tickets": 0, "fes_participation_rights": 0},
    "shop": {"enabled": True, "purchase_scope": "daily_discount_only", "include_third": True,
             "coin_budget": 10000, "coin_reserve": 0,
             "items": [{"offer_rank": 1, "item_id": "skip_ticket", "item_type": "ordinary_item", "quantity": 1, "max_unit_price": 10},
                       {"offer_rank": 2, "item_id": "gold_training_ticket", "item_type": "ordinary_item", "quantity": 1, "max_unit_price": 10},
                       {"offer_rank": 3, "item_id": "illumination_stars_towel", "item_type": "ordinary_item", "quantity": 1, "max_unit_price": 5000}]},
    "training": {"enabled": False, "card_id": None, "candidate_card_ids": [], "ticket_type": "basic_training_ticket",
                 "ticket_quantity": 1, "ticket_budget": 0},
    "match_fes": {"enabled": False, "mode": "skip", "count": 20, "participation_budget": 0, "stage_id": None, "stage_selection": "latest"},
    "grade_fes": {"enabled": False, "difficulty_id": None, "count": 1, "participation_budget": 0},
}


def implementation_stamp():
    files = (ROOT / "agent/core/shop_flow.py", ROOT / "tools/shop_recognition.py", Path(__file__).resolve(),
             ROOT / "tests/fixtures/shop_flow/pipeline.json", ROOT / "tools/replay_screenshots.py", ROOT / "tools/replay_ocr.py")
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def _outcome(function):
    try:
        return {"allowed": True, "result": function()}
    except AlreadyComplete as exc:
        return {"allowed": False, "complete": True, "reason": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        return {"allowed": False, "complete": False, "reason": str(exc)}


def rank_mode(config):
    """Same budgets, but every rank uses any_daily_discount (identity = OCR'd name)."""
    config = copy.deepcopy(config)
    for item in config["shop"]["items"]:
        item.update(item_id="any_daily_discount", currency="money")
    return config


def evidence_cases(observations, mode="catalog"):
    """Fixed expectations of the pure rules on real OCR from the replayed frames."""
    cases = []

    def case(case_id, expected, function, expect_complete=False):
        outcome = _outcome(function)
        passed = outcome["allowed"] is expected and (not expect_complete or outcome.get("complete") is True)
        cases.append({"id": mode + ":" + case_id, "mode": mode, "expected_allowed": expected, **outcome, "passed": passed})

    convert = rank_mode if mode == "rank" else copy.deepcopy
    config = convert(REPLAY_CONFIG)
    no_third = convert(REPLAY_CONFIG)
    no_third["shop"]["include_third"] = False
    no_third["shop"]["items"] = no_third["shop"]["items"][:2]
    reserve = convert(REPLAY_CONFIG)
    reserve["shop"]["coin_reserve"] = 17_678_510
    base = observations["List-007"]
    list_ev = list_evidence(base["ocr_texts"])
    baselines = {}
    for rank in OFFER_RANKS:
        case(f"list-007-open-rank{rank}", True, lambda r=rank: validate_offer_open(
            config, r, list_ev, slots_of(base), set(), "ready", 0))
        baselines[rank] = _outcome(lambda r=rank: validate_offer_open(config, r, list_ev, slots_of(base), set(), "ready", 0)).get("result")
    case("list-007-rank3-without-include-third", False,
         lambda: validate_offer_open(no_third, 3, list_ev, slots_of(base), set(), "ready", 0))
    case("list-007-rank1-pending-ledger", False, lambda: validate_offer_open(config, 1, list_ev, slots_of(base), set(), "pending", 0))
    case("list-007-rank3-over-day-budget", False, lambda: validate_offer_open(config, 3, list_ev, slots_of(base), set(), "ready", 5001))
    case("list-007-rank1-below-reserve", False, lambda: validate_offer_open(reserve, 1, list_ev, slots_of(base), set(), "ready", 0))
    case("list-007-rank1-wrong-identity", False, lambda: validate_offer_open(
        config, 1, list_ev, {"count": 3, "cards": {1: "gold_training_ticket", 2: "gold_training_ticket",
                                                    3: "illumination_stars_towel"}}, set(), "ready", 0))
    sparkle = observations["List-011"]
    case("list-011-rank2-price-refused", False, lambda: validate_offer_open(
        config, 2, list_evidence(sparkle["ocr_texts"]), slots_of(sparkle), set(), "ready", 0))
    done = observations["List-synthetic-limit1-reached"]
    # The synthetic 1/1 is refused by OCR (0.948 < 0.95): stop, never buy. AlreadyComplete is unit-tested.
    case("list-synthetic-rank1-limit-refused-or-complete", False, lambda: validate_offer_open(
        config, 1, list_evidence(done["ocr_texts"]), slots_of(done), set(), "ready", 0))
    price = observations["List-synthetic-price1-5000"]
    case("list-synthetic-rank1-price-over-max", False, lambda: validate_offer_open(
        config, 1, list_evidence(price["ocr_texts"]), slots_of(price), set(), "ready", 0))
    witnesses = {}
    for frame, rank in (("009", 1), ("013", 2), ("016", 3)):
        dialog = observations[f"Confirm-{frame}"]
        case(f"confirm-{frame}-rank{rank}", True, lambda d=dialog, r=rank: validate_confirm(
            config, r, baselines[r], confirm_evidence_of(d), identity_of(d, "Confirm"), "ready", 0))
        witnesses[rank] = _outcome(lambda d=dialog, r=rank: validate_confirm(
            config, r, baselines[r], confirm_evidence_of(d), identity_of(d, "Confirm"), "ready", 0)).get("result")
    gold = observations["Confirm-013"]
    case("confirm-013-as-rank1-identity", False, lambda: validate_confirm(
        config, 1, baselines[1], confirm_evidence_of(gold), identity_of(gold, "Confirm"), "ready", 0))
    for sample_id, rank in (("Confirm-synthetic-required-5000", 1), ("Confirm-synthetic-money-after", 1),
                            ("Confirm-synthetic-quantity-4", 1), ("Confirm-synthetic-name-gold-on-skip", 1),
                            ("Confirm-synthetic-name-towel-on-gold", 2), ("Confirm-synthetic-name-blank", 1)):
        tampered = observations[sample_id]
        case(sample_id.lower(), False, lambda t=tampered, r=rank: validate_confirm(
            config, r, baselines[r], confirm_evidence_of(t), identity_of(t, "Confirm"), "ready", 0))
    unchanged = observations["After-synthetic-limit-without-spend"]
    rank1 = {"spend": witnesses[1], "offer_baseline": baselines[1]}
    case("after-limit-reached-money-unchanged", False, lambda: validate_after(
        rank1, list_evidence(unchanged["ocr_texts"]), slots_of(unchanged)))
    case("after-unchanged-list-007", False, lambda: validate_after(rank1, list_evidence(base["ocr_texts"]), slots_of(base)))
    # 2026-10-02 live frame: ranks 1 and 2 bought, they disappeared; the towel (rank 3) is at position 1.
    bought = observations["List-after-two-bought"]
    bought_ev = list_evidence(bought["ocr_texts"])
    case("two-bought-empty-ledger-rank1-stop", False, lambda: validate_offer_open(config, 1, bought_ev, slots_of(bought), set(), "ready", 0))
    case("two-bought-empty-ledger-rank3-stop", False, lambda: validate_offer_open(config, 3, bought_ev, slots_of(bought), set(), "ready", 0))
    case("two-bought-ledger-1-only-rank3-stop", False, lambda: validate_offer_open(config, 3, bought_ev, slots_of(bought), {1}, "ready", 10))
    case("two-bought-ledger-1-2-rank3-position1", True, lambda: validate_offer_open(config, 3, bought_ev, slots_of(bought), {1, 2}, "ready", 20))
    case("two-bought-ledger-1-2-rank1-complete", False, lambda: validate_offer_open(config, 1, bought_ev, slots_of(bought), {1, 2}, "ready", 20),
         expect_complete=True)
    def mapped():
        result = validate_offer_open(config, 3, bought_ev, slots_of(bought), {1, 2}, "ready", 20)
        require(result["position"] == 1 and result["price"] == 5000 and result["banner_count"] == 1, "位置映射错误")
        return result
    case("two-bought-rank3-maps-to-position1", True, mapped)
    grey = observations["List-synthetic-grey-button2"]
    case("grey-button-position2-stop", False, lambda: validate_offer_open(config, 2, list_evidence(grey["ocr_texts"]), slots_of(grey), set(), "ready", 0))
    # Post-spend: rank 2 bought after rank 1 was confirmed (list showed gold, towel); now only the towel shifted to position 1.
    before = slots_of(base)
    witness = {"offer_baseline": {"rank": 2, "position": 1, "banner_count": 2, "slots": [
                   {"catalog": c2["catalog"], "name": c2["name"]} for c2 in (before["cards"][2], before["cards"][3])]},
               "spend": {"rank": 2, "item_id": baselines[2]["item_id"] if baselines.get(2) else None, "card_name": before["cards"][2]["name"],
                         "required": 10, "money_before": 17_678_502}}
    case("after-two-bought-shift-verified", True, lambda: validate_after(witness, bought_ev, slots_of(bought)))
    case("after-two-bought-money-mismatch", False, lambda: validate_after(
        dict(witness, spend=dict(witness["spend"], money_before=17_678_512)), bought_ev, slots_of(bought)))
    case("after-not-shifted-007", False, lambda: validate_after(witness, list_ev, slots_of(base)))
    # Live 交換結果 (rank 1, 2026-10-02): the witness values are those of the runner's own pending.
    live = {"spend": {"rank": 1, "item_id": config["shop"]["items"][0]["item_id"], "card_name": "スキップチケット",
                      "dialog_name": "スキップチケット", "required": 10, "money_before": 17_678_492, "item_before": 609}}
    shown = observations["Result-live-confirm1"]
    case("result-live-rank1", True, lambda: validate_result(live, result_evidence_of(shown)))
    case("result-wrong-name", False, lambda: validate_result(
        {"spend": dict(live["spend"], dialog_name="ゴールドトレーニングチケット")}, result_evidence_of(shown)))
    case("result-wrong-money", False, lambda: validate_result(
        {"spend": dict(live["spend"], money_before=17_678_512)}, result_evidence_of(shown)))
    case("result-wrong-item-count", False, lambda: validate_result(
        {"spend": dict(live["spend"], item_before=608)}, result_evidence_of(shown)))
    pasted_money = observations["Result-synthetic-money-610"]
    case("result-synthetic-money-610", False, lambda: validate_result(live, result_evidence_of(pasted_money)))
    return cases


def replay(manifest, sources, model_dir):
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    data, truth, digest = load_profile(manifest, sources)
    base = manifest.parent
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker
    require(Library.version() == FRAMEWORK, "商店回放要求MaaFramework v5.12.3")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    reco, ocr = MaaRecognizer(), ShopOcr(model_dir)

    class FixtureController(CustomController):
        def __init__(self):
            self.image, self.clicks, self.other_calls, self.captures = None, [], 0, 0
            super().__init__()
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.Offline.ShopPipeline"
        def screencap(self):
            self.captures += 1
            return self.image.copy()
        def click(self, x, y):
            self.clicks.append([x, y])
            return True
        def deny(self, *args):
            self.other_calls += 1
            return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args):
            self.other_calls += 1
            return None

    controller = FixtureController()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded, "离线控制器初始化失败")
    results, observations = [], {}
    metrics = {"ocr_fields": 0, "ocr_accepted": 0, "ocr_wrong_values": 0, "ocr_refusals": 0, "ocr_required_refusals": 0}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sc-offline-shop-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/shop.json").write_text(json.dumps(fixed_pipeline()), encoding="utf-8")
        resource, tasker = Resource(), Tasker()
        require(resource.post_bundle(bundle).wait().succeeded, "商店Pipeline载入失败")
        for scene in data["scenes"]:
            if scene["name"].startswith((PREFIX + "Item.", PREFIX + "Exclude.")) or scene["recognition"] != "TemplateMatch":
                continue
            require(resource.override_image(scene["template"], read_png(template_path(scene, base), expected_size=None)),
                    "商店模板载入失败")
        require(tasker.bind(resource, controller) and tasker.inited, "商店Pipeline绑定失败")
        for sample in data["samples"]:
            require(time.monotonic() - started < 600, "商店回放总预算超时")
            item = truth[sample["image"]]
            stage, expected = item["stage"], item["expected_guard"]
            image = read_png(base / sample["image"])
            observed = observe(stage, image, reco, data, base, ocr if item.get("ocr_expected") else None)
            observations[sample["id"]] = observed
            group = anchor_stage(stage)
            runs = []
            for action in STAGE_ACTIONS[group]:
                require(resource.override_pipeline(fixed_pipeline(action)), "商店动作Pipeline配置失败")
                controller.image, controller.clicks, controller.other_calls, controller.captures = image, [], 0, 0
                job = tasker.post_task(PREFIX + group + ".Observe")
                deadline = time.monotonic() + 15
                while not job.done:
                    if time.monotonic() >= deadline:
                        tasker.post_stop().wait()
                        raise RuntimeError("商店Pipeline超时，停止且禁止重试")
                    time.sleep(0.01)
                detail = job.get()
                trace = [{"name": n.name, "completed": n.completed,
                          "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
                position = int(action.rsplit("-", 1)[1]) if action.startswith("shop-offer-open-") else None
                slots = observed.get("daily_slots")
                slot_ok = position is None or type(slots) is int and position <= slots
                # The Pipeline Click needs the common anchors plus (for offer-open) that slot's own anchors.
                clicked = observed["anchors_matched"] and (position is None or slot_full(observed["recognitions"], position))
                point = list(ACTIONS[action][1])
                permit = bool(controller.clicks == [point] and observed["guard_verified"] and slot_ok)
                want_slots = item.get("slots_expected")
                expected_permit = expected and (position is None or type(want_slots) is int and position <= want_slots)
                names = [n["name"] for n in trace]
                if clicked:
                    trace_ok = (job.succeeded and names == [PREFIX + group + ".Observe", PREFIX + "Action." + action]
                                and all(n["completed"] is True and n["action_success"] is True for n in trace))
                elif observed["anchors_matched"]:
                    # Common page proved but this daily slot is absent/disabled: the Click node never matches.
                    trace_ok = PREFIX + "Action." + action not in names
                else:
                    trace_ok = (job.succeeded and names == [PREFIX + group + ".Observe", PREFIX + group + ".Unknown"]
                                and all(n["completed"] is True and n["action_success"] is True for n in trace))
                ok = (trace_ok and controller.clicks == ([point] if clicked else []) and controller.other_calls == 0
                      and controller.captures > 0 and permit is expected_permit)
                runs.append({"action": action, "node_trace": trace, "simulated_clicks": list(controller.clicks),
                             "composite_permit": permit, "passed": bool(ok)})
            passed = observed["guard_verified"] is expected and all(r["passed"] for r in runs)
            ocr_rows = {}
            if item.get("ocr_expected") and observed["guard_verified"]:
                for name, want in item["ocr_expected"].items():
                    got = observed["ocr_texts"].get(name)
                    metrics["ocr_fields"] += 1
                    if got is None:
                        metrics["ocr_refusals"] += 1
                        metrics["ocr_required_refusals"] += bool(item.get("ocr_required"))
                        passed = passed and not item.get("ocr_required")
                    elif got != want:
                        metrics["ocr_wrong_values"] += 1
                        passed = False
                    else:
                        metrics["ocr_accepted"] += 1
                    decision = observed["ocr"][name]["decision"]
                    ocr_rows[name] = {"expected": want, "accepted": got, "score": decision.get("score"),
                                      "reason": decision.get("reason")}
            if item.get("identity_expected") and observed["guard_verified"]:
                passed = passed and observed["identity"] == item["identity_expected"]
            if "slots_expected" in item and observed["guard_verified"]:
                passed = passed and observed.get("daily_slots") == item["slots_expected"]
            if item.get("names_expected") and observed["guard_verified"]:
                passed = passed and all(observed.get("names", {}).get(k) == v for k, v in item["names_expected"].items())
            results.append({"id": sample["id"], "stage": stage, "kind": item["kind"], "expected_guard": expected,
                            "guard_verified": observed["guard_verified"], "anchors_matched": observed["anchors_matched"],
                            "exclusions_clear": observed["exclusions_clear"], "identity": observed.get("identity"),
                            "identity_detail": observed.get("identity_detail"), "names": observed.get("names"), "daily_slots": observed.get("daily_slots"),
                            "name_ocr": observed.get("name_ocr"), "ocr": ocr_rows,
                            "recognitions": observed["recognitions"], "pipeline_runs": runs,
                            "real_input_attempts": 0, "passed": bool(passed)})
    cases = evidence_cases(observations, "catalog") + evidence_cases(observations, "rank")
    require(reco.input_attempts == 0 and ocr.input_attempts == 0, "识别器出现意外输入")
    return {"passed": all(r["passed"] for r in results) and all(c["passed"] for c in cases) and metrics["ocr_wrong_values"] == 0,
            "offline_only": True, "real_input_enabled": False, "real_input_attempts": 0,
            "framework_version": Library.version(), "configuration_sha256": digest,
            "implementation_sha256": implementation_stamp(), "ocr_model": ocr.model,
            "sample_count": len(results), "samples": results, "ocr_metrics": metrics, "evidence_cases": cases,
            "simulated_click_attempts": sum(len(r["simulated_clicks"]) for s in results for r in s["pipeline_runs"]),
            "elapsed_seconds": time.monotonic() - started,
            "limitations": ("模板来源帧参与自身评测；无交换后结果、真实1/1、售罄、マニー不足样本；"
                            "After 正例只有规则单测，没有图像。离线通过不代表实机可执行。")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日商店离线回放，模拟固定点击，无实机输入选项。")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PROFILE / "manifest.json")
    parser.add_argument("--sources", type=Path, default=DEFAULT_PROFILE / "sources.json")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    path = args.report.resolve()
    require(path.is_relative_to((ROOT / "dev_assets/private").resolve()) and path.suffix == ".json" and not path.exists(),
            "商店离线报告必须是private目录下的新JSON")
    try:
        report = replay(args.manifest, args.sources, args.model_dir)
    except Exception as exc:
        report = {"passed": False, "offline_only": True, "real_input_attempts": 0, "error": str(exc)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    summary = {k: report.get(k) for k in ("passed", "sample_count", "ocr_metrics", "real_input_attempts", "error")}
    summary["failed"] = [s["id"] for s in report.get("samples", []) if not s["passed"]] + \
                        [c["id"] for c in report.get("evidence_cases", []) if not c["passed"]]
    print(json.dumps({**summary, "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
