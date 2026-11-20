#!/usr/bin/env python3
"""Grade Fes 完整 Maa Pipeline 离线回放：守卫、颜色检查、导航遮挡排除、OCR 与证据规则；绝不连接浏览器或实时输入。"""
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

from agent.core.daily_navigation import load_contract as load_navigation_contract
from agent.core.grade_flow import (ACTIONS, PREFIX, TEXT_BOXES, difficulty_evidence, fixed_pipeline, navigation_blockers,
                                   navigation_clear, node_name, parse_rights, validate_difficulty_tap)
from tools.grade_recognition import (DEFAULT_MODEL, DEFAULT_PROFILE, NAVIGATION_PROFILE, GradeOcr, load_profile, observe,
                                     read_text)
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path

FRAMEWORK = "v5.12.3"
STAGE_ACTIONS = {}
for _action, (_stages, _) in ACTIONS.items():
    for _stage in _stages:
        STAGE_ACTIONS.setdefault(_stage, []).append(_action)
# Offline evidence configuration (not the user's daily_tasks.json, which stays disabled by default).
REPLAY_CONFIG = {
    "version": 1, "premium_currency_allowed": False,
    "budgets": {"coins": 0, "basic_training_tickets": 0, "fes_participation_rights": 1},
    "shop": {"enabled": False, "purchase_scope": "daily_discount_only", "include_third": False, "coin_budget": 0,
             "coin_reserve": 0, "items": []},
    "training": {"enabled": False, "card_id": None, "candidate_card_ids": [], "ticket_type": "basic_training_ticket",
                 "ticket_quantity": 1, "ticket_budget": 0},
    "match_fes": {"enabled": False, "mode": "skip", "count": 20, "participation_budget": 0, "stage_id": None,
                  "stage_selection": "latest"},
    "grade_fes": {"enabled": True, "difficulty_id": "Normal", "count": 1, "participation_budget": 1},
}
EMPTY_DAY = {"attempts": 0, "rights_used": 0, "unresolved": []}


def offline_pipeline(action=None, stage=None):
    """Static replay frames never change, so the live 8 s re-capture window only adds time."""
    pipeline = fixed_pipeline(action, stage)
    for name, node in pipeline.items():
        if name.endswith(".Observe"):
            node["timeout"], node["rate_limit"] = 1000, 0
    return pipeline

def implementation_stamp():
    files = (ROOT / "agent/core/grade_flow.py", ROOT / "tools/grade_recognition.py", Path(__file__).resolve(),
             ROOT / "tests/fixtures/grade_flow/pipeline.json", ROOT / "tools/replay_screenshots.py",
             ROOT / "tools/replay_ocr.py", ROOT / "tools/shop_recognition.py", ROOT / "agent/core/daily_navigation.py")
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def _outcome(function):
    try:
        return {"allowed": True, "result": function()}
    except (ValueError, KeyError, TypeError) as exc:
        return {"allowed": False, "reason": str(exc)}


def evidence_cases(observations, extra):
    cases = []

    def case(case_id, expected, function):
        outcome = _outcome(function)
        cases.append({"id": case_id, "expected_allowed": expected, **outcome, "passed": outcome["allowed"] is expected})

    def config(**grade):
        data = copy.deepcopy(REPLAY_CONFIG)
        data["grade_fes"].update(grade)
        return data

    base = observations["Difficulty-066"]
    ev = lambda o: difficulty_evidence(o["ocr_texts"], o["names"])  # noqa: E731
    for difficulty in ("Easy", "Normal", "Hard"):
        action = "grade-difficulty-" + difficulty.lower()
        case(f"066-{difficulty.lower()}-allowed", True,
             lambda a=action, d=difficulty: validate_difficulty_tap(config(difficulty_id=d), a, ev(base), EMPTY_DAY))
    case("066-action-differs-from-config", False,
         lambda: validate_difficulty_tap(config(), "grade-difficulty-easy", ev(base), EMPTY_DAY))
    case("066-disabled", False, lambda: validate_difficulty_tap(config(enabled=False), "grade-difficulty-normal", ev(base), EMPTY_DAY))
    case("066-free-text-difficulty", False, lambda: validate_difficulty_tap(
        config(difficulty_id="observed-difficulty-1"), "grade-difficulty-normal", ev(base), EMPTY_DAY))
    case("066-lowercase-difficulty", False, lambda: validate_difficulty_tap(
        config(difficulty_id="normal"), "grade-difficulty-normal", ev(base), EMPTY_DAY))
    case("066-budget-zero", False, lambda: validate_difficulty_tap(
        config(participation_budget=0), "grade-difficulty-normal", ev(base), EMPTY_DAY))
    no_global = config()
    no_global["budgets"]["fes_participation_rights"] = 0
    case("066-global-budget-zero", False, lambda: validate_difficulty_tap(no_global, "grade-difficulty-normal", ev(base), EMPTY_DAY))
    case("066-count-exhausted", False, lambda: validate_difficulty_tap(
        config(), "grade-difficulty-normal", ev(base), {"attempts": 1, "rights_used": 1, "unresolved": []}))
    case("066-budget-exhausted", False, lambda: validate_difficulty_tap(
        config(count=2), "grade-difficulty-normal", ev(base), {"attempts": 1, "rights_used": 1, "unresolved": []}))
    case("066-unresolved-pending", False, lambda: validate_difficulty_tap(
        config(), "grade-difficulty-normal", ev(base), {"attempts": 0, "rights_used": 0,
                                                        "unresolved": [{"day": "2026-10-01", "index": 1}]}))
    case("066-rehearsal-action-name", False, lambda: validate_difficulty_tap(
        config(), "rehearsal-difficulty-normal", ev(base), EMPTY_DAY))
    zero = observations["Difficulty-synthetic-rights-0"]
    case("rights-0-refused", False, lambda: validate_difficulty_tap(config(), "grade-difficulty-normal", ev(zero), EMPTY_DAY))
    # The rehearsal dialog is refused by the guard; its OCR'd title is also refused by the rule.
    rehearsal = extra["rehearsal-056"]
    case("rehearsal-056-title-refused", False, lambda: validate_difficulty_tap(
        config(), "grade-difficulty-normal", difficulty_evidence({"rights": RIGHTS_TEXT}, rehearsal), EMPTY_DAY))
    blank = extra["prompt-digit-removed"]
    case("prompt-digit-removed-refused", False, lambda: validate_difficulty_tap(
        config(), "grade-difficulty-normal", difficulty_evidence({"rights": RIGHTS_TEXT}, blank), EMPTY_DAY))
    for sample_id in ("FesMain-053", "ModeSelect-064"):
        case(f"rights-parse-{sample_id}", True, lambda s=sample_id: parse_rights(observations[s]["ocr_texts"]["rights"]))
    return cases


RIGHTS_TEXT = "6/20"


def replay(manifest, sources, model_dir, nav_manifest, nav_sources):
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    data, truth, digest = load_profile(manifest, sources)
    nav_data, _, nav_digest = load_navigation_contract(nav_manifest, nav_sources)
    nav_base = Path(nav_manifest).resolve().parent
    base = manifest.parent
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker
    require(Library.version() == FRAMEWORK, "Grade回放要求MaaFramework v5.12.3")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    reco, ocr = MaaRecognizer(), GradeOcr(model_dir)

    class FixtureController(CustomController):
        def __init__(self):
            self.image, self.clicks, self.other_calls, self.captures = None, [], 0, 0
            super().__init__()
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.Offline.GradePipeline"
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
    results, observations, extra = [], {}, {}
    metrics = {"ocr_fields": 0, "ocr_accepted": 0, "ocr_wrong_values": 0, "ocr_refusals": 0,
               "text_boxes": 0, "text_accepted": 0, "text_wrong": 0}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sc-offline-grade-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/grade.json").write_text(json.dumps(offline_pipeline()), encoding="utf-8")
        resource, tasker = Resource(), Tasker()
        require(resource.post_bundle(bundle).wait().succeeded, "Grade Pipeline载入失败")
        for scene in data["scenes"]:
            if scene["recognition"] == "TemplateMatch" and not scene["name"].startswith(PREFIX + "Exclude."):
                require(resource.override_image(scene["template"], read_png(template_path(scene, base), expected_size=None)),
                        "Grade模板载入失败")
        require(tasker.bind(resource, controller) and tasker.inited, "Grade Pipeline绑定失败")
        for sample in data["samples"]:
            require(time.monotonic() - started < 900, "Grade回放总预算超时")
            item = truth[sample["image"]]
            stage, expected = item["stage"], item["expected_guard"]
            image = read_png(base / sample["image"])
            observed = observe(stage, image, reco, data, base, ocr)
            nav_hits = [reco.recognize(s, image, nav_base) for s in nav_data["scenes"]]
            nav_ok = navigation_clear(stage, nav_hits)
            guard = observed["guard_verified"] and nav_ok
            observations[sample["id"]] = observed
            runs = []
            for action in STAGE_ACTIONS.get(stage, []):
                require(resource.override_pipeline(offline_pipeline(action, stage)), "Grade动作Pipeline配置失败")
                controller.image, controller.clicks, controller.other_calls, controller.captures = image, [], 0, 0
                job = tasker.post_task(PREFIX + stage + ".Observe")
                deadline = time.monotonic() + 15
                while not job.done:
                    if time.monotonic() >= deadline:
                        tasker.post_stop().wait()
                        raise RuntimeError("Grade Pipeline超时，停止且禁止重试")
                    time.sleep(0.01)
                detail = job.get()
                trace = [{"name": n.name, "completed": n.completed,
                          "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
                names = [n["name"] for n in trace]
                point = list(ACTIONS[action][1])
                clicked = observed["anchors_matched"]
                if clicked:
                    trace_ok = (job.succeeded and names == [PREFIX + stage + ".Observe", node_name(action, stage)]
                                and all(n["completed"] is True and n["action_success"] is True for n in trace))
                else:
                    # A refused frame waits out Observe and stops via Error, never reaching a Click.
                    trace_ok = (names[:1] == [PREFIX + stage + ".Observe"] and names[-1:] == [PREFIX + "Error"]
                                and not any(n.startswith(PREFIX + "Action.") for n in names)
                                and controller.clicks == [])
                # The runner's composite permit = Pipeline Click + exclusions + navigation overlay exclusion.
                permit = bool(controller.clicks == [point] and guard)
                ok = (trace_ok and controller.clicks == ([point] if clicked else []) and controller.other_calls == 0
                      and controller.captures > 0 and permit is expected)
                runs.append({"action": action, "node_trace": trace, "simulated_clicks": list(controller.clicks),
                             "composite_permit": permit, "passed": bool(ok)})
            passed = guard is expected and all(r["passed"] for r in runs)
            ocr_rows, name_rows = {}, {}
            if item.get("ocr_expected") and guard:
                for name, want in item["ocr_expected"].items():
                    got = observed["ocr_texts"].get(name)
                    metrics["ocr_fields"] += 1
                    if got is None:
                        metrics["ocr_refusals"] += 1
                        passed = False
                    elif got != want:
                        metrics["ocr_wrong_values"] += 1
                        passed = False
                    else:
                        metrics["ocr_accepted"] += 1
                    decision = observed["ocr"][name]["decision"]
                    ocr_rows[name] = {"expected": want, "accepted": got, "score": decision.get("score")}
            if item.get("names_expected") and guard:
                for key, want in item["names_expected"].items():
                    got = observed["names"].get(key)
                    metrics["text_boxes"] += 1
                    metrics["text_accepted"] += got == want
                    metrics["text_wrong"] += got is not None and got != want
                    passed = passed and got == want
                    name_rows[key] = {"expected": want, "accepted": got,
                                      "scores": [r["score"] for r in observed["name_ocr"][key]["results"]]}
            if sample["id"] == "Difficulty-056":
                extra["rehearsal-056"] = {k: read_text(ocr, roi, image)[0] for k, roi in TEXT_BOXES.items()}
            if sample["id"] == "Difficulty-synthetic-prompt-digit-removed":
                extra["prompt-digit-removed"] = {k: read_text(ocr, roi, image)[0] for k, roi in TEXT_BOXES.items()}
            results.append({"id": sample["id"], "stage": stage, "kind": item["kind"], "expected_guard": expected,
                            "guard_verified": guard, "maa_guard_verified": observed["guard_verified"],
                            "anchors_matched": observed["anchors_matched"], "exclusions_clear": observed["exclusions_clear"],
                            "navigation_clear": nav_ok, "navigation_blockers": navigation_blockers(stage, nav_hits),
                            "ocr": ocr_rows, "names": name_rows, "recognitions": observed["recognitions"],
                            "pipeline_runs": runs, "real_input_attempts": 0, "passed": bool(passed)})
    cases = evidence_cases(observations, extra)
    require(reco.input_attempts == 0 and ocr.input_attempts == 0, "识别器出现意外输入")
    return {"passed": all(r["passed"] for r in results) and all(c["passed"] for c in cases) and metrics["ocr_wrong_values"] == 0
            and metrics["text_wrong"] == 0,
            "offline_only": True, "real_input_enabled": False, "real_input_attempts": 0,
            "framework_version": Library.version(), "configuration_sha256": digest, "navigation_configuration_sha256": nav_digest,
            "implementation_sha256": implementation_stamp(), "ocr_model": ocr.model,
            "sample_count": len(results), "samples": results, "ocr_metrics": metrics, "evidence_cases": cases,
            "simulated_click_attempts": sum(len(r["simulated_clicks"]) for s in results for r in s["pipeline_runs"]),
            "elapsed_seconds": time.monotonic() - started,
            "limitations": ("模板来源帧参与自身评测；难度页只有1张稳定正例（066）；无点击难度后的画面、勾选「まとめて消費」、"
                            "参加权不足与グレード判定弹窗样本。离线通过不代表实机可执行。")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade Fes 离线回放，模拟固定点击，无实机输入选项。")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PROFILE / "manifest.json")
    parser.add_argument("--sources", type=Path, default=DEFAULT_PROFILE / "sources.json")
    parser.add_argument("--navigation-manifest", type=Path, default=NAVIGATION_PROFILE / "daily-navigation-manifest.json")
    parser.add_argument("--navigation-sources", type=Path, default=NAVIGATION_PROFILE / "daily-navigation-sources.json")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    path = args.report.resolve()
    require(path.is_relative_to((ROOT / "dev_assets/private").resolve()) and path.suffix == ".json" and not path.exists(),
            "Grade离线报告必须是private目录下的新JSON")
    try:
        report = replay(args.manifest, args.sources, args.model_dir, args.navigation_manifest, args.navigation_sources)
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
