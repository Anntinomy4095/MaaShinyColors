#!/usr/bin/env python3
"""监督固定单步Maa Scroll；默认只读，不消费资源，不自动重试。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.browser_bridge import BrowserBridge
from agent.core.daily_config import load_json
from agent.core.daily_navigation import load_contract
from agent.core.daily_scroll import ACTION_POINTS, DELTAS, FIXTURE, GUARD_ACTIONS, PREFIX, allowed, content_snapshot, fixed_pipeline, fixed_target_binding, observe_change
from agent.core.foreground_guard import interpret_foreground
from tools import navigation_protocol
from tools.replay_foreground import load_configuration
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.run_daily_navigation import send_request
from tools.run_supervised_navigation import private_output


def implementation_stamp():
    paths = (Path(__file__), ROOT / "agent/core/daily_scroll.py", ROOT / "agent/core/daily_navigation.py",
             ROOT / "agent/core/foreground_guard.py", ROOT / "tools/navigation_protocol.py",
             ROOT / "tools/replay_screenshots.py", FIXTURE)
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def validate_offline_proof(proof, action, digest, foreground_digest, sample_ids, stamp, expected_by_id=None):
    require(proof.get("passed") is True and proof.get("offline_only") is True
            and proof.get("input_enabled") is False and type(proof.get("input_attempts")) is int
            and proof["input_attempts"] == 0 and proof.get("action") == action
            and proof.get("configuration_sha256") == digest and proof.get("foreground_sha256") == foreground_digest
            and proof.get("implementation_sha256") == stamp, "滚动离线证明与本次动作、模板或实现不符")
    samples = proof.get("offline")
    require(type(samples) is list and len(samples) == len(sample_ids)
            and {s.get("id") for s in samples} == set(sample_ids)
            and all(s.get("passed") is True and s.get("unexpected_inputs") == 0 for s in samples),
            "滚动离线证明样本、负例或意外输入记录不完整")
    for sample in samples:
        require(sample.get("expected") is sample.get("allowed")
                and type(sample.get("expected")) is bool
                and sample.get("scrolls") == ([list(DELTAS[action])] if sample["expected"] else []),
                "滚动离线证明固定方向或正负样本不符")
        if expected_by_id is not None:
            require(sample["expected"] is expected_by_id[sample["id"]], "滚动缓存证明与样本来源真值不符")
    return samples


def guarded_send(report, execute, action, delta, guard, send, checkpoint=lambda: None):
    if execute is not True or action not in GUARD_ACTIONS or delta != DELTAS[action] or report["input_attempts"]:
        return False
    try:
        request = guard()
        report.update(input_attempts=1, reason="scroll_pending_no_retry")
        checkpoint()
        response = navigation_protocol.validate_navigation_response(send(request))
        report["response"] = response
        return response["ok"] and response["attempted"]
    except Exception as exc:
        report["input_error"] = str(exc)
        return False


def verify_scroll_protocol(action):
    require(getattr(navigation_protocol, "DAILY_SCROLL_ACTION_POINTS", None) == ACTION_POINTS
            and getattr(navigation_protocol, "DAILY_SCROLL_DELTAS", None) == DELTAS,
            "Python协议尚未整合或固定滚动位置/方向变化，拒绝实时输入")
    require(action in navigation_protocol.DAILY_SCROLL_BUDGET_PATHS, "固定滚动预算缺失")


def validate_foreground_proof(samples, data, rules, base):
    require(type(samples) is list and len(samples) == len(data["samples"]), "前景离线证明样本不完整")
    by_id = {sample.get("id"): sample for sample in samples}
    require(len(by_id) == len(samples) and set(by_id) == {s["id"] for s in data["samples"]}, "前景离线证明样本重复")
    for source in data["samples"]:
        sample = by_id[source["id"]]
        expected = rules["expected"][source["id"]]
        require(sample.get("passed") is True and sample.get("expected") == expected
                and type(sample.get("actual")) is dict and all(sample["actual"].get(k) == v for k, v in expected.items())
                and sample.get("image_sha256") == hashlib.sha256((base / source["image"]).read_bytes()).hexdigest(),
                "前景离线样本图像或预期变化")
    return samples


def run(args, report, checkpoint):
    _, frames = private_output(args.report)
    data, truth, digest = load_contract(args.manifest, args.sources)
    foreground_data, rules, foreground_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
    base, foreground_base = args.manifest.resolve().parent, args.foreground_manifest.resolve().parent
    reco = MaaRecognizer()
    def classify(image):
        hits = [reco.recognize(scene, image, base) for scene in data["scenes"]]
        foreground_hits = [reco.recognize(scene, image, foreground_base) for scene in foreground_data["scenes"]]
        return hits, interpret_foreground(foreground_hits, (1280, 720), rules)
    pipeline = fixed_pipeline(args.action, args.execute)
    stamp = implementation_stamp()
    report.update(configuration_sha256=digest, foreground_sha256=foreground_digest,
                  implementation_sha256=stamp, framework_version=reco.version, offline=[])
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    import numpy as np
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    bridge, session = None, None
    class Controller(CustomController):
        offline = True
        image = None
        permitted = False
        scrolls = []
        unexpected_inputs = 0
        pre_image = None
        target_bound = False
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.DailyScroll.FixedSingleStep"
        def screencap(self):
            if self.offline: return self.image
            if report.get("capture_error"): return np.empty((0, 0, 3), dtype=np.uint8)
            try:
                raw = bridge.screencap()
                path = frames / f"frame-{len(report['frames']) + 1:03d}.png"
                path.write_bytes(raw)
                report["frames"].append({"image": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
                return read_png(path)
            except Exception as exc:
                report["capture_error"] = str(exc)
                return np.empty((0, 0, 3), dtype=np.uint8)
        def scroll(self, dx, dy):
            if not self.target_bound:
                self.unexpected_inputs += 1
                return False
            self.target_bound = False
            if self.offline:
                if not self.permitted or (dx, dy) != DELTAS[args.action] or self.scrolls: return False
                self.scrolls.append([dx, dy])
                return True
            def guard():
                require(load_contract(args.manifest, args.sources)[2] == digest, "滚动守卫配置变化")
                require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == foreground_digest, "滚动前景配置变化")
                require(implementation_stamp() == stamp, "滚动实现变化")
                image = self.screencap()
                require(image.shape == (720, 1280, 3), "新滚动截图失败")
                hits, foreground = classify(image)
                report["fresh_guard"] = {"recognitions": hits, "foreground": foreground}
                require(allowed(args.action, hits, foreground), "新帧全锚点或前景守卫拒绝")
                status = bridge.status()
                require(status.get("supervised_scroll") is True, "宿主未独立启用只读滚动实验")
                verify_scroll_protocol(args.action)
                self.pre_image = image.copy()
                report["pre_frame"] = dict(report["frames"][-1])
                report["pre_content"] = content_snapshot(args.action, image)
                return navigation_protocol.daily_navigation_request(session, status, report["pre_frame"]["sha256"], args.action)
            return guarded_send(report, args.execute, args.action, (dx, dy), guard,
                                lambda request: send_request(session, request), checkpoint)
        def deny(self, *args):
            self.unexpected_inputs += 1
            return False
        def touch_move(self, contact, x, y, pressure):
            # Maa Scroll first calls zero-pressure touch_move for its target.
            # Validate and record only; never forward this callback to the host.
            self.target_bound = fixed_target_binding(args.action, contact, x, y, pressure)
            if not self.target_bound:
                self.unexpected_inputs += 1
            return self.target_bound
        start_app = stop_app = click = swipe = touch_down = touch_up = deny
        click_key = input_text = key_down = key_up = relative_move = deny
        def shell(self, *args):
            self.unexpected_inputs += 1
            return None
    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded, "滚动控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix="sc-daily-scroll-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/scroll.json").write_text(json.dumps(fixed_pipeline(args.action, True)), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "滚动Pipeline加载失败")
        for scene in data["scenes"]:
            require(resource.override_image(scene["name"] + ".png", read_png(template_path(scene, base), expected_size=None)), "滚动模板加载失败")
        require(tasker.bind(resource, controller) and tasker.inited, "滚动Pipeline绑定失败")
        head = PREFIX + args.action + "."
        def task():
            job = tasker.post_task(head + "Observe")
            deadline = time.monotonic() + 20
            while not job.done:
                if time.monotonic() >= deadline:
                    tasker.post_stop().wait()
                    raise RuntimeError("滚动任务超时，停止且不重试")
                time.sleep(.02)
            detail = job.get()
            trace = [{"name": node.name, "completed": node.completed,
                      "action": node.action.action.value if node.action else None,
                      "action_success": node.action.success if node.action else None} for node in detail.nodes] if detail else []
            return job.succeeded, trace
        samples = data["samples"]
        expected_by_id = {sample["id"]: GUARD_ACTIONS[args.action] in truth[sample["image"]]["actions"] for sample in samples}
        require(any(expected_by_id.values()) and not all(expected_by_id.values()), "滚动必须具有本动作正负回放样本")
        if args.offline_proof:
            require(args.offline_proof.resolve().is_relative_to((ROOT / "dev_assets/private").resolve()), "滚动离线证明须private")
            proof = load_json(args.offline_proof)
            report["offline"] = validate_offline_proof(proof, args.action, digest,
                foreground_digest, [sample["id"] for sample in samples], stamp, expected_by_id)
            report["foreground_offline"] = validate_foreground_proof(proof.get("foreground_offline"),
                foreground_data, rules, foreground_base)
            report["offline_reused"] = True
            samples = []
        else:
            report["foreground_offline"] = []
            for sample in foreground_data["samples"]:
                path = foreground_base / sample["image"]
                image = read_png(path)
                hits = [reco.recognize(scene, image, foreground_base) for scene in foreground_data["scenes"]]
                actual = interpret_foreground(hits, (1280, 720), rules)
                expected = rules["expected"][sample["id"]]
                report["foreground_offline"].append({"id": sample["id"], "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "expected": expected, "actual": actual, "passed": all(actual[k] == v for k, v in expected.items())})
            validate_foreground_proof(report["foreground_offline"], foreground_data, rules, foreground_base)
        for sample in samples:
            controller.image = read_png(base / sample["image"])
            controller.scrolls, controller.unexpected_inputs, controller.target_bound = [], 0, False
            hits, foreground = classify(controller.image)
            controller.permitted = allowed(args.action, hits, foreground)
            expected = GUARD_ACTIONS[args.action] in truth[sample["image"]]["actions"]
            succeeded, trace = task()
            desired = [list(DELTAS[args.action])] if expected else []
            passed = (controller.permitted is expected and controller.scrolls == desired
                      and controller.unexpected_inputs == 0 and (succeeded or not expected)
                      and (not expected or ([n["name"] for n in trace] == [head + "Observe", head + "Scroll"]
                           and all(n["completed"] is True and n["action_success"] is True for n in trace))))
            report["offline"].append({"id": sample["id"], "expected": expected, "allowed": controller.permitted,
                "scrolls": list(controller.scrolls), "unexpected_inputs": controller.unexpected_inputs,
                "trace": trace, "task_succeeded": succeeded, "passed": bool(passed)})
        require(report["offline"] and all(sample["passed"] for sample in report["offline"])
                and reco.input_attempts == 0, "滚动完整Pipeline或前景正负回放失败")
        if args.offline_only:
            report.update(passed=True, reason="offline_scroll_pipeline_verified")
            return
        # Load hash-bound templates into the recognizer before taking a live frame.
        classify(read_png(base / data["samples"][-1]["image"]))
        bridge = BrowserBridge.from_file(args.session)
        status = bridge.status()
        require(status["ready"] and status.get("minimized") is False, "滚动宿主未就绪或最小化")
        if args.execute:
            verify_scroll_protocol(args.action)
            require(status.get("supervised_scroll") is True, "宿主未独立启用滚动")
            session = navigation_protocol.validate_daily_session(load_json(Path(str(args.session) + ".daily-input.json")), bridge.session["pid"])
        frames.mkdir(parents=True)
        controller.offline = False
        require(resource.override_pipeline(pipeline), "实时滚动Pipeline配置失败")
        if not args.execute:
            image = controller.screencap()
            require(image.shape == (720, 1280, 3), "只读截图失败")
            hits, foreground = classify(image)
            report.update(observation={"recognitions": hits, "foreground": foreground}, pre_frame=report["frames"][-1],
                          pre_content=content_snapshot(args.action, image), passed=allowed(args.action, hits, foreground),
                          reason="readonly_scroll_guard")
            return
        succeeded, trace = task()
        report.update(node_trace=trace, passed=False, reason="scroll_postcondition_unknown_no_retry")
        require(succeeded and report.get("response", {}).get("ok") is True
                and report.get("response", {}).get("attempted") is True and controller.unexpected_inputs == 0,
                "滚动尝试未确认，停止且不重试")
        for _ in range(4):
            time.sleep(.3)
            image = controller.screencap()
            require(image.shape == (720, 1280, 3), "滚动后截图失败")
            hits, foreground = classify(image)
            result = observe_change(args.action, controller.pre_image, image, hits, foreground)
            result["frame"] = dict(report["frames"][-1])
            report.setdefault("postcondition_checks", []).append(result)
            report.update(post_frame=result["frame"], content_changed=result["content_changed"])
            if not result["screen_guard_verified"]:
                report["reason"] = "scroll_screen_unknown_no_retry"
                break
            if result["content_changed"]:
                report.update(passed=True, reason="scroll_content_changed_only")
                break
        if not report["passed"] and report["reason"] == "scroll_postcondition_unknown_no_retry":
            report["reason"] = "scroll_content_unchanged_no_retry"


def main(argv=None):
    parser = argparse.ArgumentParser(description="固定单步滚动监督实验；默认只读，内容变化不证明列表边界或最新关卡。")
    for name in ("manifest", "sources", "foreground-manifest", "foreground-contracts", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--action", choices=GUARD_ACTIONS, required=True)
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--offline-proof", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.offline_only and (args.execute or args.offline_proof) or not args.offline_only and args.session is None:
        parser.error("离线禁止输入或复用证明；实时只读及执行均须截图会话")
    private_output(args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "input_attempts": 0, "input_enabled": args.execute,
              "offline_only": args.offline_only, "action": args.action, "resource_consumption": False,
              "semantic_progress_verified": False, "latest_stage_verified": False, "frames": []}
    def checkpoint():
        with args.report.open("w" if report.get("checkpoint_written") else "x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        report["checkpoint_written"] = True
    try:
        run(args, report, checkpoint)
    except Exception as exc:
        report.update(passed=False, reason="scroll_refused_or_uncertain_no_retry", error=str(exc))
    checkpoint()
    print(json.dumps({k: report[k] for k in ("passed", "reason", "input_attempts")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
