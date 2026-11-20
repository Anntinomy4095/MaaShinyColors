#!/usr/bin/env python3
"""每日グレードフェス监督运行器：默认只读；每次运行最多一个固定动作，不重试。

只有 ``grade-difficulty-<easy|normal|hard>`` 可能消耗 1 个フェス参加権：发送前先写不可删除的 pending 见证与
报告检查点，宿主再消耗该动作的每游戏日单次原生预算。点击难度后的画面没有样本，因此只做约30秒只读观察并记录帧，
报告 ``postcondition_unknown_needs_sampling``，永不再次点击、刷新或关闭任何画面。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import navigation_interrupt as interrupt
from agent.core.browser_bridge import BrowserBridge
from agent.core.daily_config import DailyConfig, load_json
from agent.core.daily_navigation import load_contract as load_navigation_contract
from agent.core.foreground_guard import interpret_foreground
from agent.core.grade_flow import (ACTIONS, CONSUMING, GUARD_STAGES, POST_OBSERVE_SECONDS, POST_STAGES, PREFIX, REPEATABLE,
                                   RIGHTS_STAGES, RUNNER, STAGES, GradeLedger, action_stages, difficulty_evidence,
                                   fixed_pipeline, game_day, navigation_blockers, navigation_clear, parse_rights,
                                   validate_difficulty_tap)
from tools import grade_protocol
from tools import repeatable_navigation_protocol as repeatable_protocol
from tools.grade_recognition import DEFAULT_MODEL, DEFAULT_PROFILE, NAVIGATION_PROFILE, GradeOcr, load_profile, observe
from tools.navigation_protocol import validate_navigation_response
from tools.replay_foreground import load_configuration
from tools.replay_grade_pipeline import FRAMEWORK, implementation_stamp as replay_stamp
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.run_daily_navigation import send_request
from tools.run_supervised_navigation import private_output

PRIVATE = ROOT / "dev_assets/private"
# Read-only window for finding a stable two-frame guard on animated pages.
STABLE_GUARD_WINDOW_S = 8.0
STABLE_GUARD_POLL_S = 0.5


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def foreground_allowed(state):
    # Grade screens are proved by their own anchors; any known foreground overlay blocks.
    return (state.get("state") == "no_known_popup" and state.get("popup") is None
            and state.get("complete_contracts") == [] and state.get("partial_contracts") == [])


def validate_offline_proof(proof, data, digest, nav_digest):
    require(type(proof) is dict and proof.get("passed") is True and proof.get("offline_only") is True
            and proof.get("real_input_enabled") is False and proof.get("real_input_attempts") == 0
            and proof.get("framework_version") == FRAMEWORK and proof.get("configuration_sha256") == digest
            and proof.get("navigation_configuration_sha256") == nav_digest
            and proof.get("implementation_sha256") == replay_stamp()
            and proof.get("ocr_metrics", {}).get("ocr_wrong_values") == 0
            and proof.get("ocr_metrics", {}).get("text_wrong") == 0, "Grade离线证明身份、实现或真实输入字段无效")
    samples = proof.get("samples")
    require(type(samples) is list and len(samples) == len(data["samples"]) == proof.get("sample_count")
            and {s.get("id") for s in samples} == {s["id"] for s in data["samples"]}
            and all(s.get("passed") is True and s.get("real_input_attempts") == 0 for s in samples)
            and proof.get("evidence_cases") and all(c.get("passed") is True for c in proof["evidence_cases"]),
            "Grade离线证明样本或证据规则不完整")
    fixed_pipeline()


def native_ledger_unused(session, action, day):
    """The per-day native one-shot ledger of a difficulty tap must still be untouched."""
    path = grade_protocol.budget_path(action, day).resolve()
    require(Path(session["budget_paths"][action]).resolve() == path, "Grade原生预算路径必须为当前游戏日固定路径（宿主跨过05:00需重启）")
    if path.exists():
        ledger = json.loads(path.read_bytes().decode("utf-8-sig"))
        require(type(ledger) is dict and ledger.get("Navigation") == action and ledger.get("MaxAttempts") == 1
                and ledger.get("Attempts") == 0 and ledger.get("LastNonce") is None, "该难度今日已尝试或账本不确定，禁止再次消耗")
    return str(path)


def validate_pending_report(prior, action, stage, pid):
    """A repeatable Grade navigation was sent once by this host and its post state was not recognised."""
    response = prior.get("response") if type(prior) is dict else None
    require(type(prior) is dict and prior.get("runner") == RUNNER and prior.get("action") == action and action in REPEATABLE
            and prior.get("stage") == stage and prior.get("input_enabled") is True and prior.get("input_attempts") == 1
            and prior.get("passed") is False and prior.get("reason") == "postcondition_unverified_no_retry"
            and type(prior.get("initial_repeatable_status")) is dict and prior.get("session_pid") == pid
            and type(response) is dict and response.get("ok") is True and response.get("attempted") is True
            and response.get("state") == "pending" and repeatable_protocol.nonce_valid(prior.get("nonce"))
            and repeatable_protocol.digest(prior.get("pre_frame_sha256"))
            and prior.get("request_binding", {}).get("nonce") == prior["nonce"]
            and prior["request_binding"].get("frame_sha256") == prior["pre_frame_sha256"],
            "只接受本宿主已发送一次、后置未识别的可重复 Grade 导航报告")
    return {"nonce": prior["nonce"], "pre_frame_sha256": prior["pre_frame_sha256"], "response": dict(response)}


def guard_signature(observed):
    fore = observed.get("foreground") or {}
    own = observed.get("recognitions") or []
    nav = observed.get("navigation_recognitions") or []
    return (interrupt.recognition_signature(own, tuple(r.get("scene") for r in own if type(r) is dict)),
            interrupt.recognition_signature(nav, interrupt.home_signature_names()),
            (fore.get("state"), fore.get("popup"), tuple(fore.get("complete_contracts") or ()),
             tuple(fore.get("partial_contracts") or ())))


def run(args, report, checkpoint):
    _, frames = private_output(args.report)
    config = DailyConfig.from_dict(load_json(args.config))
    report["config_sha256"] = sha256(Path(args.config).read_bytes())
    data, _, digest = load_profile(args.manifest, args.sources)
    nav_data, _, nav_digest = load_navigation_contract(args.navigation_manifest, args.navigation_sources)
    nav_base = args.navigation_manifest.resolve().parent
    proof_path = args.offline_proof.resolve()
    require(proof_path.is_relative_to(PRIVATE.resolve()), "Grade离线证明必须位于private")
    validate_offline_proof(load_json(proof_path), data, digest, nav_digest)
    fore_data, fore_rules, fore_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
    reco, ocr = MaaRecognizer(), GradeOcr(args.model_dir)
    base, fore_base = args.manifest.resolve().parent, args.foreground_manifest.resolve().parent
    day = game_day()
    ledger = GradeLedger()
    action, stage = args.action, args.stage
    report.update(configuration_sha256=digest, navigation_configuration_sha256=nav_digest, foreground_sha256=fore_digest,
                  implementation_sha256=sha256(Path(__file__).read_bytes()), offline_proof_sha256=sha256(proof_path.read_bytes()),
                  ocr_model=ocr.model, framework_version=reco.version, game_day=day, action=action,
                  unresolved_pending=ledger.unresolved(), ledger_today=ledger.summary(day))
    bridge = BrowserBridge.from_file(args.session)
    status = bridge.status()
    require(status["ready"] and status.get("composition_game") is True and status.get("minimized") is False, "Grade截图宿主未就绪或最小化")
    pid = bridge.session["pid"]
    report["session_pid"] = pid
    session = None
    if args.execute and not args.repeatable:
        require(status.get("supervised_grade") is True, "宿主未显式启用 --enable-supervised-grade")
        session = grade_protocol.validate_session(load_json(Path(str(args.session) + grade_protocol.SESSION_SUFFIX)), pid)
        require(action in session["actions"], "宿主会话未开放该 Grade 动作")
        require(grade_protocol.session_day(session) == day, "宿主的 Grade 预算属于其他游戏日；05:00 后需重启宿主")
    if args.repeatable:
        session = repeatable_protocol.validate_session(load_json(Path(str(args.session) + repeatable_protocol.SESSION_SUFFIX)), pid)
        initial = repeatable_protocol.validate_response(send_request(session, repeatable_protocol.status_request(session, action)))
        report["initial_repeatable_status"] = initial
        if args.resolve_pending_report:
            require(initial["state"] == "pending", "宿主账本不是pending，无需或不能解决")
        else:
            require(repeatable_protocol.status_allows_attempt(initial), "可重复导航pending或已达上限；禁止发送")
    frames.mkdir(parents=True)

    def capture():
        raw = bridge.screencap()
        path = frames / f"frame-{len(report['frames']) + 1:03d}.png"
        path.write_bytes(raw)
        report["frames"].append({"image": str(path), "sha256": sha256(raw)})
        return read_png(path), report["frames"][-1]["sha256"]

    def classify(current_stage, image, frame_hash):
        observed = observe(current_stage, image, reco, data, base, ocr)
        fore_hits = [reco.recognize(s, image, fore_base) for s in fore_data["scenes"]]
        state = interpret_foreground(fore_hits, (1280, 720), fore_rules)
        # nav-native-v27 overlay exclusion on every Grade stage (popups such as 「ミッション達成」 do not dim).
        nav = [reco.recognize(s, image, nav_base) for s in nav_data["scenes"]]
        observed.update(frame_sha256=frame_hash, foreground=state, navigation_recognitions=nav,
                        navigation_clear=navigation_clear(current_stage, nav),
                        navigation_blockers=navigation_blockers(current_stage, nav))
        observed["guard_verified"] = observed["guard_verified"] and foreground_allowed(state) and observed["navigation_clear"]
        return observed

    def post_candidates(current, stamp, post_stages):
        observed = [classify(post, current, stamp) for post in post_stages]
        return [o for o in observed if o["guard_verified"]]

    def evaluate(current_stage, observed):
        evidence = {}
        if current_stage in RIGHTS_STAGES:
            try:
                evidence["rights"] = parse_rights(observed.get("ocr_texts", {}).get("rights"))
            except ValueError as exc:
                evidence["rights_refused"] = str(exc)
        if action in CONSUMING:
            require(not ledger.unresolved(), "存在未解决的 Grade pending；先人工核对，禁止再次消耗")
            shown = difficulty_evidence(observed["ocr_texts"], observed["names"])
            evidence.update(difficulty=shown, tap=validate_difficulty_tap(config, action, shown, ledger.summary(day)))
        return evidence

    if args.resolve_pending_report:
        resolve_pending(args, report, session, bridge, capture, post_candidates, digest, fore_digest)
        return
    if stage == "AfterStart":
        # Nothing after a difficulty tap has been sampled: classify only, never input.
        image, frame_hash = capture()
        report["observation"] = {s: classify(s, image, frame_hash)["guard_verified"] for s in GUARD_STAGES}
        report.update(passed=True, reason="readonly_after_start_classified_only")
        return
    # Read-only: an animation phase (button shine/pulse) may fail one frame; look again briefly.
    deadline = time.monotonic() + STABLE_GUARD_WINDOW_S
    while True:
        image, frame_hash = capture()
        initial = classify(stage, image, frame_hash)
        if initial["guard_verified"] or time.monotonic() >= deadline:
            break
        time.sleep(STABLE_GUARD_POLL_S)
    report["observation"] = initial
    require(initial["guard_verified"], "完整阶段、颜色、排除、导航遮挡或前景守卫拒绝")
    report["evidence"] = evaluate(stage, initial)
    if not args.execute:
        report.update(passed=True, reason="readonly_stage_verified")
        return
    require(stage in action_stages(action), "动作不属于当前阶段")
    if action in CONSUMING:
        report["native_budget_path"] = native_ledger_unused(session, action, day)
        report["tap"] = report["evidence"]["tap"]
    initial_texts, initial_names = initial.get("ocr_texts"), initial.get("names")
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def fresh_guard():
        require(load_profile(args.manifest, args.sources)[2] == digest, "Grade档案变化")
        require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == fore_digest, "前景配置变化")
        require(DailyConfig.from_dict(load_json(args.config)).data == config.data, "日常配置变化")

        def guard_frame():
            current, stamp = capture()
            observed = classify(stage, current, stamp)
            report["fresh_guard"] = observed
            if observed["guard_verified"]:
                require(observed.get("ocr_texts") == initial_texts and observed.get("names") == initial_names,
                        "新帧参加权或提示文字与初次观察不同，停止")
            return observed, stamp

        # Two identical full guard passes ≥ STABLE_GUARD_INTERVAL_S apart; bind to the second frame.
        # Animated buttons (e.g. the pulsing 「フェスに参加する」) fail some frames, so read-only
        # captures repeat within a bounded window; no input is ever sent before a stable pair.
        first, started, attempts = None, None, []
        deadline = time.monotonic() + STABLE_GUARD_WINDOW_S
        while True:
            observed, stamp = guard_frame()
            now = time.monotonic()
            current = {"passed": observed["guard_verified"] is True,
                       "signature": guard_signature(observed) if observed["guard_verified"] else None,
                       "frame_sha256": stamp}
            attempts.append({"frame_sha256": stamp, "passed": current["passed"]})
            if not current["passed"]:
                first = None
            elif first is None or current["signature"] != first["signature"]:
                first, started = current, now
            elif interrupt.stable_pair(first, current, now - started):
                break
            require(now < deadline, "限定时间内未取得稳定两帧守卫；不发送")
            time.sleep(STABLE_GUARD_POLL_S)
        report["stable_guard"] = {"first_frame_sha256": first["frame_sha256"], "second_frame_sha256": stamp,
                                  "elapsed_s": now - started, "identical": True, "captures": attempts}
        evidence = evaluate(stage, observed)
        if action in CONSUMING:
            native_ledger_unused(session, action, day)
            require(evidence["tap"] == report["tap"], "难度点击前的证据变化，停止")
        if args.repeatable:
            return repeatable_protocol.navigate_request(session, bridge.status(), stamp, action)
        return grade_protocol.navigate_request(session, bridge.status(), stamp, action)

    def guarded_send(x, y):
        if report["input_attempts"] or (x, y) != ACTIONS[action][1]:
            return False
        try:
            request = fresh_guard()
            report["request_binding"] = {k: request[k] for k in ("action", "frame_sha256", "nonce")}
            report.update(input_attempts=1, reason="input_pending_no_retry", uncertain_consumption=action in CONSUMING)
            checkpoint()  # Writes the consumption witness before transmission.
            if args.repeatable:
                report.update(nonce=request["nonce"], pre_frame_sha256=request["frame_sha256"])
                response = repeatable_protocol.validate_response(send_request(session, request))
                require(not response["ok"] or response["attempted"] and response["state"] == "pending", "可重复导航响应状态不符")
            else:
                response = validate_navigation_response(send_request(session, request))
            report["response"] = response
            return response["ok"] and response["attempted"]
        except Exception as exc:
            report["input_error"] = str(exc)
            return False

    class Controller(CustomController):
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.DailyGrade.OneAction"
        def screencap(self): return capture()[0]
        def click(self, x, y): return guarded_send(x, y)
        def deny(self, *args): return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args): return None

    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded, "Grade控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix="sc-supervised-grade-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/grade.json").write_text(json.dumps(fixed_pipeline(action, stage)), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "Grade Pipeline加载失败")
        for scene in data["scenes"]:
            if scene["recognition"] == "TemplateMatch" and not scene["name"].startswith(PREFIX + "Exclude."):
                require(resource.override_image(scene["template"], read_png(template_path(scene, base), expected_size=None)),
                        "Grade模板加载失败")
        require(tasker.bind(resource, controller) and tasker.inited, "Grade Pipeline绑定失败")
        job = tasker.post_task(PREFIX + stage + ".Observe")
        deadline = time.monotonic() + 30
        while not job.done:
            if time.monotonic() >= deadline:
                tasker.post_stop().wait()
                raise RuntimeError("Grade任务超时，停止且不重试")
            time.sleep(.02)
        detail = job.get()
        report["node_trace"] = [{"name": n.name, "completed": n.completed,
                                 "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
        report.update(reason="postcondition_unverified_no_retry", passed=False)
        require(job.succeeded and report.get("response", {}).get("ok") is True and report["response"].get("attempted") is True,
                "固定输入未确认，禁止重试")
    if action in CONSUMING:
        # The post-tap state is unknown: read-only frames for ~30 s, never another input, reload or close.
        started = time.monotonic()
        checks = report.setdefault("postcondition_checks", [])
        while time.monotonic() - started < POST_OBSERVE_SECONDS:
            current, stamp = capture()
            verified = [s for s in GUARD_STAGES if classify(s, current, stamp)["guard_verified"]]
            checks.append({"frame_sha256": stamp, "elapsed_s": round(time.monotonic() - started, 2), "verified": verified})
            time.sleep(1.0)
        report.update(passed=False, reason="postcondition_unknown_needs_sampling", uncertain_consumption=True,
                      resource_result_verified=False)
        require(reco.input_attempts == 0 and ocr.input_attempts == 0, "识别器出现意外输入")
        return
    post_stages = POST_STAGES[(action, stage)]
    for _ in range(20):
        current, stamp = capture()
        candidates = post_candidates(current, stamp, post_stages)
        report.setdefault("postcondition_checks", []).append({"frame_sha256": stamp, "verified": [c["stage"] for c in candidates]})
        if len(candidates) == 1:
            observed = candidates[0]
            if args.repeatable:
                confirm_repeatable(report, args, session, bridge, report["frames"][-1], observed, digest, fore_digest)
            report.update(passed=True, reason="stage_postcondition_verified",
                          postcondition={"stage": observed["stage"], "frame_sha256": stamp, "ocr_texts": observed.get("ocr_texts")})
            break
        time.sleep(.5)
    require(reco.input_attempts == 0 and ocr.input_attempts == 0, "识别器出现意外输入")


def resolve_pending(args, report, session, bridge, capture, post_candidates, digest, fore_digest):
    """Read-only re-observation of a pending repeatable Grade navigation; never sends input."""
    path = args.resolve_pending_report.resolve()
    blob = path.read_bytes()
    prior = json.loads(blob.decode("utf-8-sig"))
    report.update(validate_pending_report(prior, args.action, args.stage, bridge.session["pid"]),
                  resolved_from={"path": str(path), "sha256": sha256(blob)})
    image, stamp = capture()
    candidates = post_candidates(image, stamp, POST_STAGES[(args.action, args.stage)])
    report["postcondition_checks"] = [{"frame_sha256": stamp, "verified": [c["stage"] for c in candidates]}]
    require(len(candidates) == 1, "当前画面不是唯一后置状态；保持pending")
    confirm_repeatable(report, args, session, bridge, report["frames"][-1], candidates[0], digest, fore_digest)
    report.update(passed=True, reason="repeatable_pending_resolved_by_reobservation",
                  postcondition={"stage": candidates[0]["stage"], "frame_sha256": stamp})


def confirm_repeatable(report, args, session, bridge, entry, observed, digest, fore_digest):
    """Write the private postcondition proof once, then register it; never resends input."""
    response = report.get("response", {})
    require(response.get("ok") is True and response.get("attempted") is True and response.get("state") == "pending",
            "原生手势未确认发送；保持pending")
    proof_path = args.report.resolve().with_name(args.report.stem + "-postcondition.json")
    private_output(proof_path)
    producer_digest = sha256(Path(__file__).read_bytes())
    require(producer_digest == report["implementation_sha256"], "生产者代码变化，拒绝登记")
    proof = repeatable_protocol.postcondition_proof(
        report["action"], report["nonce"], bridge.session["pid"], report["pre_frame_sha256"],
        {"image": str(Path(entry["image"]).resolve()), "sha256": entry["sha256"]}, digest, fore_digest,
        observed["recognitions"], observed["foreground"], producer_digest)
    with proof_path.open("x", encoding="utf-8") as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2)
    proof_sha = sha256(proof_path.read_bytes())
    report["postcondition_proof"] = {"path": str(proof_path), "sha256": proof_sha}
    confirmation = repeatable_protocol.validate_response(send_request(session, repeatable_protocol.confirm_request(
        session, bridge.status(), entry["sha256"], report["action"], report["nonce"], proof_path, proof_sha)))
    report["confirmation_response"] = confirmation
    require(confirmation["ok"] and not confirmation["attempted"] and confirmation["state"] == "confirmed",
            "可重复导航后置登记未确认；保持pending，不重发输入")


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日 Grade Fes；默认只读，--execute 每次仅一个固定动作。")
    parser.add_argument("--config", type=Path, default=ROOT / "daily_tasks.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PROFILE / "manifest.json")
    parser.add_argument("--sources", type=Path, default=DEFAULT_PROFILE / "sources.json")
    parser.add_argument("--offline-proof", type=Path, default=PRIVATE / "grade-native-v1-offline.json")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--navigation-manifest", type=Path, default=NAVIGATION_PROFILE / "daily-navigation-manifest.json",
                        help="弹窗遮挡排除用的导航档案（只读）")
    parser.add_argument("--navigation-sources", type=Path, default=NAVIGATION_PROFILE / "daily-navigation-sources.json")
    parser.add_argument("--resolve-pending-report", type=Path,
                        help="可重复非消费导航后置未识别时：只读重新观察，唯一后置成立才登记；不输入")
    for name in ("foreground-manifest", "foreground-contracts", "session", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES, default="FesMain")
    parser.add_argument("--action", choices=tuple(ACTIONS))
    parser.add_argument("--execute", action="store_true", help="本次仅尝试当前阶段固定动作一次")
    parser.add_argument("--repeatable", action="store_true", help="非消费导航使用独立每日有界账本（宿主需同时启用）")
    args = parser.parse_args(argv)
    if args.stage == "AfterStart" and (args.execute or args.action or args.repeatable or args.resolve_pending_report):
        parser.error("AfterStart 只读分类，不接受动作")
    if args.execute and not args.action:
        parser.error("--execute 需要 --action")
    if args.action and args.stage not in action_stages(args.action):
        parser.error("该动作不属于 --stage 指定的阶段")
    if args.action in CONSUMING and args.repeatable:
        parser.error("难度点击可能消耗参加权，永不使用可重复账本")
    if args.resolve_pending_report and (not args.repeatable or args.execute or args.action not in REPEATABLE
                                        or not args.resolve_pending_report.resolve().is_relative_to(PRIVATE.resolve())):
        parser.error("--resolve-pending-report 须与 --repeatable、--action <非消费动作>、--stage <动作阶段> 同用，不能 --execute，报告须在private")
    if args.repeatable and not args.resolve_pending_report and (not args.execute or args.action not in REPEATABLE):
        parser.error("--repeatable 仅用于执行非消费 Grade 导航")
    private_output(args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"runner": RUNNER, "version": 1, "passed": False, "stage": args.stage, "input_enabled": args.execute,
              "input_attempts": 0, "frames": [], "resource_result_verified": False, "uncertain_consumption": False}
    ledger = GradeLedger()

    def checkpoint():
        action = report.get("action")
        if (action in CONSUMING and report["input_attempts"] == 1 and not report.get("consumption_pending")
                and not report.get("input_error")):
            binding = report["request_binding"]
            require(binding.get("action") == action and re.fullmatch(r"[0-9a-f]{32}", binding.get("nonce", "")) is not None,
                    "pending见证必须绑定本次固定难度请求")
            report["consumption_pending"] = ledger.write_pending(report["game_day"], {
                "version": 1, "runner": RUNNER, "stage": "Difficulty", "action": action, "day": report["game_day"],
                "report": str(args.report.resolve()), "session_pid": report["session_pid"], "request_binding": binding,
                "tap": report["tap"], "native_budget_path": report.get("native_budget_path"), "no_retry": True})
        mode = "w" if report.get("checkpoint_written") else "x"
        with args.report.open(mode, encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        report["checkpoint_written"] = True

    try:
        run(args, report, checkpoint)
    except Exception as exc:
        report.update(passed=False, reason=report.get("reason") if report.get("reason") == "postcondition_unknown_needs_sampling"
                      else "refused_or_uncertain_no_retry", error=str(exc))
    checkpoint()
    print(json.dumps({k: report.get(k) for k in ("passed", "reason", "input_attempts", "uncertain_consumption")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
