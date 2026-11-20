#!/usr/bin/env python3
"""每日商店（マニー页本日限定）监督运行器：默认只读；每次运行最多一个固定动作，不重试。

消耗动作只有 ``shop-exchange-confirm-<rank>``：发送前先写不可删除的 pending 见证；之后只能
通过只读重新观察（列表该卡交换次数+1 且マニー恰好减少必要マニー）登记确认，永不重发。
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
from agent.core.daily_navigation import load_contract as load_navigation_contract
from agent.core.daily_config import DailyConfig, load_json
from agent.core.foreground_guard import interpret_foreground
from agent.core.shop_flow import (ACTIONS, AlreadyComplete, CONSUMING, OFFER_RANKS, POST_STAGES, PREFIX, REPEATABLE,
                                  ShopLedger, anchor_stage, fixed_pipeline, game_day, list_evidence,
                                  position_of, validate_after, validate_confirm, validate_offer_open, validate_result)
from tools import repeatable_navigation_protocol as repeatable_protocol
from tools.navigation_protocol import (daily_navigation_request, shop_budget_path, validate_daily_session,
                                       validate_navigation_response)
from tools.replay_foreground import load_configuration
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.replay_shop_pipeline import FRAMEWORK, implementation_stamp as replay_stamp
from tools.run_daily_navigation import send_request
from tools.run_supervised_navigation import private_output
from tools.shop_recognition import (DEFAULT_MODEL, DEFAULT_PROFILE, ShopOcr, confirm_evidence_of, identity_of,
                                   load_profile, observe, result_evidence_of, slots_of)

PRIVATE = ROOT / "dev_assets/private"
RUNNER = "SC.DailyShop"
STAGE_CHOICES = ("Home", "Premium", "List", "Confirm", "Result", "After")
ACTION_CHOICES = ("home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel", "shop-result-close",
                  "shop-offer-open", "shop-exchange-confirm")
# Navigation profile used only to EXCLUDE popups over Home (and to recognise an interrupted home-shop tap).
NAVIGATION_PROFILE = PRIVATE / "in_app_browser/20261001-daily-research/nav-native-v27"
# Pending repeatable entries that a read-only re-observation may close (no ledger/identity side conditions).
RESOLVABLE = ("home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel")


def validate_pending_report(prior, action, pid):
    """A repeatable shop navigation was sent once by this host and its post state was not recognised."""
    response = prior.get("response") if type(prior) is dict else None
    require(type(prior) is dict and prior.get("runner") == RUNNER and prior.get("action") == action
            and action in RESOLVABLE and prior.get("input_enabled") is True and prior.get("input_attempts") == 1
            and prior.get("passed") is False and prior.get("reason") == "postcondition_unverified_no_retry"
            and type(prior.get("initial_repeatable_status")) is dict and prior.get("session_pid") == pid
            and type(response) is dict and response.get("ok") is True and response.get("attempted") is True
            and response.get("state") == "pending" and repeatable_protocol.nonce_valid(prior.get("nonce"))
            and repeatable_protocol.digest(prior.get("pre_frame_sha256"))
            and prior.get("request_binding", {}).get("nonce") == prior["nonce"]
            and prior["request_binding"].get("frame_sha256") == prior["pre_frame_sha256"],
            "只接受本宿主已发送一次、后置未识别的可重复商店导航报告")
    return {"nonce": prior["nonce"], "pre_frame_sha256": prior["pre_frame_sha256"], "response": dict(response)}


def guard_signature(observed):
    fore = observed.get("foreground") or {}
    shop = observed.get("recognitions") or []
    nav = observed.get("navigation_recognitions") or []
    return (interrupt.recognition_signature(shop, tuple(r.get("scene") for r in shop if type(r) is dict)),
            interrupt.recognition_signature(nav, interrupt.home_signature_names()),
            (fore.get("state"), fore.get("popup"), tuple(fore.get("complete_contracts") or ()),
             tuple(fore.get("partial_contracts") or ())))


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def resolve_action(action, rank, confirmed):
    """``--offer-rank`` is the ORIGINAL 本日限定 rank. Bought cards disappear from the list, so the card
    to open sits at position rank − (confirmed ranks below it); the exchange confirm stays per rank."""
    if action == "shop-offer-open":
        require(rank in OFFER_RANKS, "该动作需要 --offer-rank 1..3")
        if rank in confirmed:
            return None  # Already bought today per the ledger: nothing to open.
        return f"shop-offer-open-{position_of(rank, confirmed)}"
    if action == "shop-exchange-confirm":
        require(rank in OFFER_RANKS, "该动作需要 --offer-rank 1..3")
        return f"shop-exchange-confirm-{rank}"
    return action


def foreground_allowed(state):
    # The exchange dialog is proved by its own complete anchors; any known overlay blocks.
    return (state.get("state") == "no_known_popup" and state.get("popup") is None
            and state.get("complete_contracts") == [] and state.get("partial_contracts") == [])


def validate_offline_proof(proof, data, digest):
    require(type(proof) is dict and proof.get("passed") is True and proof.get("offline_only") is True
            and proof.get("real_input_enabled") is False and proof.get("real_input_attempts") == 0
            and proof.get("framework_version") == FRAMEWORK and proof.get("configuration_sha256") == digest
            and proof.get("implementation_sha256") == replay_stamp()
            and proof.get("ocr_metrics", {}).get("ocr_wrong_values") == 0, "商店离线证明身份、实现或真实输入字段无效")
    samples = proof.get("samples")
    require(type(samples) is list and len(samples) == len(data["samples"]) == proof.get("sample_count")
            and {s.get("id") for s in samples} == {s["id"] for s in data["samples"]}
            and all(s.get("passed") is True and s.get("real_input_attempts") == 0 for s in samples)
            and all(c.get("passed") is True for c in proof.get("evidence_cases", [])) and proof.get("evidence_cases"),
            "商店离线证明样本或证据规则不完整")
    fixed_pipeline()


def native_ledger_unused(session, action, day):
    """The per-day native one-shot ledger for a consuming action must still be untouched."""
    path = shop_budget_path(action, day).resolve()
    require(Path(session["budget_paths"][action]).resolve() == path, "商店原生预算路径必须为当前游戏日固定路径")
    if path.exists():
        ledger = json.loads(path.read_bytes().decode("utf-8-sig"))
        require(type(ledger) is dict and ledger.get("Navigation") == action and ledger.get("MaxAttempts") == 1
                and ledger.get("Attempts") == 0 and ledger.get("LastNonce") is None, "该交换今日已尝试或账本不确定，禁止再次消耗")
    return str(path)


def validate_prior_open(prior, rank, pid, digest, day):
    """Confirm requires the same host's verified offer-open report for this rank and game day."""
    require(type(prior) is dict and prior.get("runner") == RUNNER and prior.get("passed") is True
            and prior.get("offer_baseline", {}).get("rank") == rank
            and prior.get("action") == f"shop-offer-open-{prior['offer_baseline'].get('position')}"
            and prior.get("session_pid") == pid
            and prior.get("configuration_sha256") == digest and prior.get("game_day") == day
            and prior.get("reason") == "stage_postcondition_verified"
            and prior.get("postcondition", {}).get("stage") == "Confirm", "交换确认缺少本宿主本日已验证的打开弹窗报告")
    baseline = prior.get("offer_baseline")
    require(type(baseline) is dict and baseline.get("rank") == rank, "打开弹窗报告缺少列表基线")
    return dict(baseline)


def run(args, report, checkpoint):
    _, frames = private_output(args.report)
    config = DailyConfig.from_dict(load_json(args.config))
    report["config_sha256"] = sha256(Path(args.config).read_bytes())
    data, _, digest = load_profile(args.manifest, args.sources)
    proof_path = args.offline_proof.resolve()
    require(proof_path.is_relative_to(PRIVATE.resolve()), "商店离线证明必须位于private")
    validate_offline_proof(load_json(proof_path), data, digest)
    fore_data, fore_rules, fore_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
    reco, ocr = MaaRecognizer(), ShopOcr(args.model_dir)
    base, fore_base = args.manifest.resolve().parent, args.foreground_manifest.resolve().parent
    day = game_day()
    ledger = ShopLedger()
    # Every Home observation (stage or post stage) also runs the navigation overlay exclusion.
    nav_data, _, nav_digest = load_navigation_contract(args.navigation_manifest, args.navigation_sources)
    navigation = (nav_data, args.navigation_manifest.resolve().parent)
    report["navigation_configuration_sha256"] = nav_digest
    report.update(configuration_sha256=digest, foreground_sha256=fore_digest, implementation_sha256=sha256(Path(__file__).read_bytes()),
                  offline_proof_sha256=sha256(proof_path.read_bytes()), ocr_model=ocr.model, framework_version=reco.version,
                  game_day=day, unresolved_pending=ledger.unresolved())
    confirmed = ledger.confirmed_ranks(day)
    report["confirmed_ranks_today"] = sorted(confirmed)
    action = resolve_action(args.action, args.offer_rank, confirmed) if args.action else None
    if args.action == "shop-offer-open" and action is None:
        report.update(passed=True, reason="offer_already_complete_skip", detail="今日账本已确认该位次购买")
        return
    report["action"] = action
    rank = args.offer_rank
    bridge = BrowserBridge.from_file(args.session)
    status = bridge.status()
    require(status["ready"] and status.get("composition_game") is True and status.get("minimized") is False, "商店截图宿主未就绪或最小化")
    pid = bridge.session["pid"]
    report["session_pid"] = pid
    session = None
    if args.execute and not args.repeatable:
        require(status.get("supervised_shop") is True, "宿主未显式启用 --enable-supervised-shop")
        session = validate_daily_session(load_json(Path(str(args.session) + ".daily-input.json")), pid)
        require(action in session["actions"], "宿主会话未开放该商店动作")
    if args.repeatable:
        session = repeatable_protocol.validate_session(load_json(Path(str(args.session) + repeatable_protocol.SESSION_SUFFIX)), pid)
        initial = repeatable_protocol.validate_response(send_request(session, repeatable_protocol.status_request(session, action)))
        report["initial_repeatable_status"] = initial
        if args.resolve_pending_report:
            require(initial["state"] == "pending", "宿主账本不是pending，无需或不能解决")
        else:
            require(repeatable_protocol.status_allows_attempt(initial), "可重复导航pending或已达上限；禁止发送")
    baseline = None
    if action in CONSUMING:
        require(args.prior_report and args.prior_report.resolve().is_relative_to(PRIVATE.resolve()), "交换确认必须提供private打开弹窗报告")
        baseline = validate_prior_open(load_json(args.prior_report), rank, pid, digest, day)
        report.update(prior_report={"path": str(args.prior_report.resolve()), "sha256": sha256(args.prior_report.read_bytes())},
                      offer_baseline=baseline)
    frames.mkdir(parents=True)

    def capture():
        raw = bridge.screencap()
        path = frames / f"frame-{len(report['frames']) + 1:03d}.png"
        path.write_bytes(raw)
        report["frames"].append({"image": str(path), "sha256": sha256(raw)})
        return read_png(path), report["frames"][-1]["sha256"]

    def navigation_hits(image):
        nav_data, nav_base = navigation
        return [reco.recognize(s, image, nav_base) for s in nav_data["scenes"]]

    def classify(stage, image, frame_hash):
        observed = observe(stage, image, reco, data, base, ocr)
        fore_hits = [reco.recognize(s, image, fore_base) for s in fore_data["scenes"]]
        state = interpret_foreground(fore_hits, (1280, 720), fore_rules)
        observed.update(frame_sha256=frame_hash, foreground=state,
                        guard_verified=observed["guard_verified"] and foreground_allowed(state))
        if stage == "Home":
            # The shop's Home anchors still match under an undimmed popup (live 2026-10-02 「ミッション達成」);
            # the navigation profile's overlay exclusion must also pass. Only refuses more.
            nav = navigation_hits(image)
            observed.update(navigation_recognitions=nav, navigation_home_clear=interrupt.home_overlays_clear(nav))
            observed["guard_verified"] = observed["guard_verified"] and observed["navigation_home_clear"]
        return observed

    def interruption_of(image, frame_hash, pre_sha, pre_pid, expected_post_complete):
        nav = navigation_hits(image)
        fore_hits = [reco.recognize(s, image, fore_base) for s in fore_data["scenes"]]
        state = interpret_foreground(fore_hits, (1280, 720), fore_rules)
        current = load_json(args.session)
        decision = interrupt.decide_interrupted(action, ACTIONS[action][1], nav, state, pre_sha, frame_hash, pre_pid,
                                                current.get("pid") if type(current) is dict else None, expected_post_complete)
        return decision, nav, state

    def post_candidates(current, stamp):
        observed = [classify(post, current, stamp) for post in POST_STAGES[action]]
        return [o for o in observed if o["guard_verified"]], any(o["anchors_matched"] or o["guard_verified"] for o in observed)

    def evaluate(stage, observed):
        """Stage evidence and rules; raises on any refusal. Returns the binding evidence."""
        group = anchor_stage(stage)
        if group == "List":
            evidence = list_evidence(observed["ocr_texts"])
            if stage == "After":
                found = witness_for(rank)
                result = validate_after(found["witness"], evidence, slots_of(observed))
                return {"list": evidence, "after": result, "pending": found["pending"], "ledger_state": found["state"]}
            if action and action.startswith("shop-offer-open-"):
                spent = ledger.spent(day)
                require(not ledger.unresolved(), "存在未解决的交换pending；先只读确认结果")
                offer = validate_offer_open(config, rank, evidence, slots_of(observed), ledger.confirmed_ranks(day),
                                            ledger.state(day, rank), spent)
                require(action == f"shop-offer-open-{offer['position']}", "位置映射与动作不一致")
                return {"list": evidence, "offer": offer}
            return {"list": evidence}
        if group == "Confirm":
            evidence = confirm_evidence_of(observed)
            if action in CONSUMING:
                require(not ledger.unresolved(), "存在未解决的交换pending；禁止再次消耗")
                return {"confirm": evidence, "spend": validate_confirm(config, rank, baseline, evidence,
                        identity_of(observed, "Confirm"), ledger.state(day, rank), ledger.spent(day))}
            return {"confirm": evidence, "identity": identity_of(observed, "Confirm")}
        if group == "Result":
            found = witness_for(rank)
            shown = result_evidence_of(observed)
            return {"result": shown, "verified": validate_result(found["witness"], shown), "pending": found["pending"],
                    "ledger_state": found["state"]}
        return {}

    def witness_for(rank_value):
        """The rank's pending witness: unresolved (any game day) or confirmed today."""
        require(rank_value in OFFER_RANKS, "需要 --offer-rank")
        rows = [r for r in ledger.unresolved() if r["rank"] == rank_value]
        if rows:
            require(len(rows) == 1, "该位次有多个未解决pending")
            path, state, pending_day = Path(rows[0]["path"]), "pending", rows[0]["day"]
        else:
            require(ledger.state(day, rank_value) == "confirmed", "该位次今日没有pending或确认记录")
            path, state, pending_day = ledger.pending_path(day, rank_value), "confirmed", day
        return {"witness": json.loads(path.read_bytes().decode("utf-8")), "state": state,
                "pending": {"day": pending_day, "path": str(path), "sha256": sha256(path.read_bytes())}}

    def resolve_from_report(found_pending):
        """Bind the explicit --resolve-after-spend-report to this exact pending witness."""
        require(args.resolve_after_spend_report is not None, "解决pending需要 --resolve-after-spend-report")
        path = args.resolve_after_spend_report.resolve()
        require(path.is_relative_to(PRIVATE.resolve()), "消费报告必须private")
        prior = load_json(path)
        witness = json.loads(Path(found_pending["path"]).read_bytes().decode("utf-8"))
        require(prior.get("runner") == RUNNER and prior.get("action") == f"shop-exchange-confirm-{rank}"
                and prior.get("input_attempts") == 1 and prior.get("uncertain_consumption") is True
                and type(prior.get("consumption_pending")) is dict
                and prior["consumption_pending"].get("sha256") == found_pending["sha256"]
                and Path(witness.get("report", "")).resolve() == path
                and prior.get("request_binding") == witness.get("request_binding"),
                "消费报告与pending见证不一致")
        return {"path": str(path), "sha256": sha256(path.read_bytes())}

    if args.resolve_pending_report:
        resolve_pending(args, report, session, bridge, capture, post_candidates, interruption_of, digest, fore_digest)
        return
    stage = args.stage
    image, frame_hash = capture()
    initial = classify(stage, image, frame_hash)
    report["observation"] = initial
    require(initial["guard_verified"], "完整阶段、排除或前景守卫拒绝")
    try:
        report["evidence"] = evaluate(stage, initial)
    except AlreadyComplete as exc:
        report.update(passed=True, reason="offer_already_complete_skip", detail=str(exc))
        return
    if stage == "After":
        pending = report["evidence"]["pending"]
        if report["evidence"]["ledger_state"] == "pending":
            resolution = {"rank": rank, "kind": "list_shift", "pending_sha256": pending["sha256"], "report": str(args.report.resolve()),
                          "frame_sha256": frame_hash, "after": report["evidence"]["after"], "session_pid": pid}
            report["confirmed_ledger"] = ledger.write_confirmed(pending["day"], rank, resolution)
            report.update(passed=True, reason="exchange_result_verified_by_readonly_reobservation",
                          resource_result_verified=True, uncertain_consumption=False)
        else:
            if not ledger.after_path(pending["day"], rank).exists():
                report["after_ledger"] = ledger.write_after(pending["day"], rank, {
                    "rank": rank, "frame_sha256": frame_hash, "after": report["evidence"]["after"], "report": str(args.report.resolve())})
            report.update(passed=True, reason="list_shift_verified_for_confirmed_exchange", resource_result_verified=True)
        return
    if stage == "Result" and not args.execute:
        pending = report["evidence"]["pending"]
        if report["evidence"]["ledger_state"] == "pending":
            source = resolve_from_report(pending)
            report["confirmed_ledger"] = ledger.write_confirmed(pending["day"], rank, {
                "rank": rank, "kind": "result_dialog", "pending_sha256": pending["sha256"], "report": str(args.report.resolve()),
                "spend_report": source, "frame_sha256": frame_hash, "result": report["evidence"]["verified"], "session_pid": pid})
            report.update(passed=True, reason="exchange_result_dialog_verified_readonly", resource_result_verified=True,
                          uncertain_consumption=False)
        else:
            report.update(passed=True, reason="result_dialog_matches_confirmed_exchange", resource_result_verified=True)
        return
    if action == "shop-result-close":
        require(report["evidence"]["ledger_state"] == "confirmed", "交换结果未经只读确认，禁止关闭；先 --stage Result 解决pending")
    if not args.execute:
        report.update(passed=True, reason="readonly_stage_verified")
        return
    require(action is not None and ACTIONS[action][0] == anchor_stage(stage), "动作不属于当前阶段")
    if action in CONSUMING:
        report["native_budget_path"] = native_ledger_unused(session, action, day)
        report["spend"] = report["evidence"]["spend"]
    if action.startswith("shop-offer-open-"):
        report["offer_baseline"] = report["evidence"]["offer"]
    initial_texts = initial.get("ocr_texts")
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def fresh_guard():
        require(load_profile(args.manifest, args.sources)[2] == digest, "商店档案变化")
        require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == fore_digest, "前景配置变化")
        require(DailyConfig.from_dict(load_json(args.config)).data == config.data, "日常配置变化")
        def guard_frame():
            current, stamp = capture()
            observed = classify(stage, current, stamp)
            report["fresh_guard"] = observed
            require(observed["guard_verified"], "新帧完整阶段或前景守卫拒绝")
            require(observed.get("ocr_texts") == initial_texts and observed.get("identity") == initial.get("identity")
                    and observed.get("names") == initial.get("names"),
                    "新帧数值或商品身份与初次观察不同，停止")
            return observed, stamp

        observed, stamp = guard_frame()
        if stage == "Home":
            # Two identical full guard passes ≥ STABLE_GUARD_INTERVAL_S apart; bind to the second frame.
            first = {"passed": True, "signature": guard_signature(observed), "frame_sha256": stamp}
            started = time.monotonic()
            time.sleep(interrupt.STABLE_GUARD_INTERVAL_S)
            observed, stamp = guard_frame()
            elapsed = time.monotonic() - started
            second = {"passed": True, "signature": guard_signature(observed), "frame_sha256": stamp}
            report["stable_guard"] = {"first_frame_sha256": first["frame_sha256"], "second_frame_sha256": stamp,
                                      "elapsed_s": elapsed, "identical": first["signature"] == second["signature"]}
            require(interrupt.stable_pair(first, second, elapsed), "主页两帧守卫不稳定；不发送")
        evidence = evaluate(stage, observed)
        if action in CONSUMING:
            native_ledger_unused(session, action, day)
            require(evidence["spend"] == report["spend"], "交换预览变化，停止")
        if args.repeatable:
            return repeatable_protocol.navigate_request(session, bridge.status(), stamp, action)
        return daily_navigation_request(session, bridge.status(), stamp, action)

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
        def request_uuid(self): return "SC.DailyShop.OneAction"
        def screencap(self): return capture()[0]
        def click(self, x, y): return guarded_send(x, y)
        def deny(self, *args): return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args): return None

    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded, "商店控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix="sc-supervised-shop-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/shop.json").write_text(json.dumps(fixed_pipeline(action)), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "商店Pipeline加载失败")
        for scene in data["scenes"]:
            if scene["name"].startswith((PREFIX + "Item.", PREFIX + "Exclude.")) or scene["recognition"] != "TemplateMatch":
                continue
            require(resource.override_image(scene["template"], read_png(template_path(scene, base), expected_size=None)), "商店模板加载失败")
        require(tasker.bind(resource, controller) and tasker.inited, "商店Pipeline绑定失败")
        job = tasker.post_task(PREFIX + anchor_stage(stage) + ".Observe")
        deadline = time.monotonic() + 30
        while not job.done:
            if time.monotonic() >= deadline:
                tasker.post_stop().wait()
                raise RuntimeError("商店任务超时，停止且不重试")
            time.sleep(.02)
        detail = job.get()
        report["node_trace"] = [{"name": n.name, "completed": n.completed,
                                 "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
        report.update(reason="postcondition_unverified_no_retry", passed=False)
        require(job.succeeded and report.get("response", {}).get("ok") is True and report["response"].get("attempted") is True,
                "固定输入未确认，禁止重试")
    # Bounded read-only observation; never resend, reload or close an unknown result.
    interruptible = args.repeatable and action in interrupt.INTERRUPTIBLE_ACTIONS
    for _ in range(20):
        current, stamp = capture()
        candidates, reached = post_candidates(current, stamp)
        report.setdefault("postcondition_checks", []).append({"frame_sha256": stamp, "verified": [c["stage"] for c in candidates]})
        if interruptible and not candidates:
            decision, nav, state = interruption_of(current, stamp, report.get("pre_frame_sha256"), pid, reached)
            report["postcondition_checks"][-1]["interruption"] = decision
            last_interruption = (nav, state)
        if len(candidates) == 1:
            observed = candidates[0]
            if action in CONSUMING or action == "shop-result-close":
                pending = ledger.pending_path(day, rank)
                witness = json.loads(pending.read_bytes().decode("utf-8"))
                try:
                    if observed["stage"] == "Result":
                        proof, kind = validate_result(witness, result_evidence_of(observed)), "result_dialog"
                    else:
                        proof, kind = validate_after(witness, list_evidence(observed["ocr_texts"]), slots_of(observed)), "list_shift"
                except ValueError as exc:
                    # Keep observing only; an unproved result stays pending and uncertain.
                    report["postcondition_checks"][-1]["proof_refused"] = str(exc)
                    time.sleep(.5)
                    continue
                if action in CONSUMING:
                    report["confirmed_ledger"] = ledger.write_confirmed(day, rank, {
                        "rank": rank, "kind": kind, "pending_sha256": sha256(pending.read_bytes()), "report": str(args.report.resolve()),
                        "frame_sha256": stamp, kind: proof, "session_pid": pid})
                    report.update(resource_result_verified=True, uncertain_consumption=False)
                elif not ledger.after_path(day, rank).exists():
                    report["after_ledger"] = ledger.write_after(day, rank, {
                        "rank": rank, "frame_sha256": stamp, "after": proof, "report": str(args.report.resolve())})
            if action.startswith("shop-offer-open-"):
                opened, shown = report["offer_baseline"], identity_of(observed, "Confirm")
                same = (shown["name"] is not None and shown["name"] == opened["card_name"]) if opened["identity_mode"] == "rank"                     else shown["catalog"] == opened["item_id"]
                require(same, "弹窗商品与打开的卡片不符")
            if args.repeatable:
                confirm_repeatable(report, args, session, bridge, report["frames"][-1], observed, digest, fore_digest)
            report.update(passed=True, reason="stage_postcondition_verified",
                          postcondition={"stage": observed["stage"], "frame_sha256": stamp, "identity": observed.get("identity"), "names": observed.get("names"),
                                         "ocr_texts": observed.get("ocr_texts")})
            break
        time.sleep(.5)
    else:
        checks = report["postcondition_checks"][-interrupt.INTERRUPT_STABLE_FRAMES:]
        decisions = [c.get("interruption") for c in checks]
        if (interruptible and len(decisions) == interrupt.INTERRUPT_STABLE_FRAMES
                and all(type(d) is dict and d.get("interrupted") is True for d in decisions)
                and all(d["interruption"] == decisions[-1]["interruption"] for d in decisions)):
            nav, state = last_interruption
            confirm_repeatable(report, args, session, bridge, report["frames"][-1], {"recognitions": nav, "foreground": state},
                               digest, fore_digest, decisions[-1]["interruption"])
            report.update(passed=False, interrupted=True, reason="repeatable_interrupted_by_known_popup_no_retry")
    require(reco.input_attempts == 0 and ocr.input_attempts == 0, "识别器出现意外输入")


def resolve_pending(args, report, session, bridge, capture, post_candidates, interruption_of, digest, fore_digest):
    """Read-only re-observation of a pending repeatable shop navigation; never sends input."""
    path = args.resolve_pending_report.resolve()
    blob = path.read_bytes()
    prior = json.loads(blob.decode("utf-8-sig"))
    report.update(validate_pending_report(prior, report["action"], bridge.session["pid"]),
                  resolved_from={"path": str(path), "sha256": sha256(blob)})
    image, stamp = capture()
    candidates, reached = post_candidates(image, stamp)
    report["postcondition_checks"] = [{"frame_sha256": stamp, "verified": [c["stage"] for c in candidates]}]
    if len(candidates) == 1:
        confirm_repeatable(report, args, session, bridge, report["frames"][-1], candidates[0], digest, fore_digest)
        report.update(passed=True, reason="repeatable_pending_resolved_by_reobservation",
                      postcondition={"stage": candidates[0]["stage"], "frame_sha256": stamp})
        return
    require(not candidates and report["action"] in interrupt.INTERRUPTIBLE_ACTIONS, "当前画面不是唯一后置状态；保持pending")
    decisions = []
    for index in range(2):
        if index:
            time.sleep(interrupt.STABLE_GUARD_INTERVAL_S)
            image, stamp = capture()
            candidates, reached = post_candidates(image, stamp)
            require(not candidates, "第二帧出现后置状态；保持pending，请重新观察")
        decision, nav, state = interruption_of(image, stamp, report["pre_frame_sha256"], prior.get("session_pid"), reached)
        decisions.append(decision)
        report.setdefault("interruption_checks", []).append({"frame_sha256": stamp, **decision})
    require(all(d["interrupted"] for d in decisions) and decisions[0]["interruption"] == decisions[1]["interruption"],
            "既不是后置状态，也不是稳定的已知弹窗中断；保持pending")
    confirm_repeatable(report, args, session, bridge, report["frames"][-1], {"recognitions": nav, "foreground": state},
                       digest, fore_digest, decisions[-1]["interruption"])
    report.update(passed=False, interrupted=True, reason="repeatable_pending_recorded_as_interrupted_no_retry")


def confirm_repeatable(report, args, session, bridge, entry, observed, digest, fore_digest, interruption=None):
    """Write the private postcondition (or interruption) proof once, then register it; never resends input."""
    response = report.get("response", {})
    require(response.get("ok") is True and response.get("attempted") is True and response.get("state") == "pending",
            "原生手势未确认发送；保持pending")
    proof_path = args.report.resolve().with_name(args.report.stem + "-postcondition.json")
    private_output(proof_path)
    producer_digest = sha256(Path(__file__).read_bytes())
    require(producer_digest == report["implementation_sha256"], "生产者代码变化，拒绝登记")
    fields = (report["action"], report["nonce"], bridge.session["pid"], report["pre_frame_sha256"],
              {"image": str(Path(entry["image"]).resolve()), "sha256": entry["sha256"]}, digest, fore_digest,
              observed["recognitions"], observed["foreground"], producer_digest)
    proof = (repeatable_protocol.postcondition_proof(*fields) if interruption is None
             else repeatable_protocol.interrupted_proof(*fields, interruption))
    with proof_path.open("x", encoding="utf-8") as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2)
    proof_sha = sha256(proof_path.read_bytes())
    report["postcondition_proof"] = {"path": str(proof_path), "sha256": proof_sha}
    confirmation = repeatable_protocol.validate_response(send_request(session, repeatable_protocol.confirm_request(
        session, bridge.status(), entry["sha256"], report["action"], report["nonce"], proof_path, proof_sha)))
    report["confirmation_response"] = confirmation
    expected = "confirmed" if interruption is None else "interrupted"
    require(confirmation["ok"] and not confirmation["attempted"] and confirmation["state"] == expected,
            "可重复导航后置登记未确认；保持pending，不重发输入")


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日商店本日限定交换；默认只读，--execute 每次仅一个固定动作。")
    parser.add_argument("--config", type=Path, default=ROOT / "daily_tasks.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PROFILE / "manifest.json")
    parser.add_argument("--sources", type=Path, default=DEFAULT_PROFILE / "sources.json")
    parser.add_argument("--offline-proof", type=Path, default=PRIVATE / "shop-native-v1-offline.json")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--navigation-manifest", type=Path, default=NAVIGATION_PROFILE / "daily-navigation-manifest.json",
                        help="Home 阶段弹窗排除与中断识别用的导航档案（只读）")
    parser.add_argument("--navigation-sources", type=Path, default=NAVIGATION_PROFILE / "daily-navigation-sources.json")
    parser.add_argument("--resolve-pending-report", type=Path,
                        help="可重复非消费导航后置未识别时：只读重新观察；后置成立则确认，稳定的已知弹窗中断则登记 interrupted；不输入")
    for name in ("foreground-manifest", "foreground-contracts", "session", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--stage", choices=STAGE_CHOICES, default="List")
    parser.add_argument("--action", choices=ACTION_CHOICES)
    parser.add_argument("--offer-rank", type=int, choices=OFFER_RANKS)
    parser.add_argument("--prior-report", type=Path, help="交换确认：本宿主本日已验证的 shop-offer-open 报告")
    parser.add_argument("--resolve-after-spend-report", type=Path,
                        help="Result 只读：用该交换确认报告绑定 pending，以 交換結果 弹窗证明后登记确认；不输入")
    parser.add_argument("--execute", action="store_true", help="本次仅尝试当前阶段固定动作一次")
    parser.add_argument("--repeatable", action="store_true", help="非消费导航使用独立每日有界账本（宿主需同时启用）")
    args = parser.parse_args(argv)
    if args.execute and (args.stage == "After" or args.stage == "Result" and args.action != "shop-result-close"):
        parser.error("After 仅供只读；Result 只允许 shop-result-close（结果须先只读确认）")
    if args.stage in ("Result", "After") and args.offer_rank is None:
        parser.error("Result/After 需要 --offer-rank")
    if args.resolve_after_spend_report and (args.stage != "Result" or args.execute):
        parser.error("--resolve-after-spend-report 仅用于只读 Result")
    if args.execute and not args.action:
        parser.error("--execute 需要 --action")
    if args.stage == "After" and args.offer_rank is None:
        parser.error("After 需要 --offer-rank")
    if args.resolve_pending_report and (not args.repeatable or args.execute or args.action not in RESOLVABLE
                                        or args.stage != ACTIONS[args.action][0]
                                        or not args.resolve_pending_report.resolve().is_relative_to(PRIVATE.resolve())):
        parser.error("--resolve-pending-report 须与 --repeatable、--stage <动作阶段>、非消费导航动作同用，不能 --execute，报告须在private")
    if args.repeatable and not args.resolve_pending_report and (not args.execute or args.action not in (
            "home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel", "shop-result-close", "shop-offer-open")):
        parser.error("--repeatable 仅用于执行非消费商店导航")
    if args.action in ("shop-offer-open", "shop-exchange-confirm") and args.offer_rank is None:
        parser.error("该动作需要 --offer-rank")
    private_output(args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"runner": RUNNER, "version": 1, "passed": False, "stage": args.stage, "input_enabled": args.execute,
              "input_attempts": 0, "frames": [], "offer_rank": args.offer_rank, "resource_result_verified": False,
              "uncertain_consumption": False}
    ledger = ShopLedger()

    def checkpoint():
        action = report.get("action")
        if (action in CONSUMING and report["input_attempts"] == 1 and not report.get("consumption_pending")
                and not report.get("input_error")):
            binding = report["request_binding"]
            require(binding.get("action") == action and re.fullmatch(r"[0-9a-f]{32}", binding.get("nonce", "")) is not None,
                    "pending见证必须绑定本次固定交换请求")
            report["consumption_pending"] = ledger.write_pending(report["game_day"], report["offer_rank"], {
                "version": 1, "runner": RUNNER, "stage": "Confirm", "action": action, "day": report["game_day"],
                "report": str(args.report.resolve()), "session_pid": report["session_pid"], "request_binding": binding,
                "spend": report["spend"], "offer_baseline": report.get("offer_baseline"), "no_retry": True})
        mode = "w" if report.get("checkpoint_written") else "x"
        with args.report.open(mode, encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        report["checkpoint_written"] = True

    try:
        run(args, report, checkpoint)
    except Exception as exc:
        report.update(passed=False, reason="refused_or_uncertain_no_retry", error=str(exc))
    checkpoint()
    print(json.dumps({k: report.get(k) for k in ("passed", "reason", "input_attempts", "uncertain_consumption")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
