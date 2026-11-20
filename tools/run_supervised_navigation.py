#!/usr/bin/env python3
"""One guarded, non-consuming announcement return; default is observation only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.browser_bridge import BrowserBridge
from agent.core.daily_config import load_json
from tools.navigation_protocol import navigation_request, validate_navigation_response, validate_navigation_session
from tools.replay_foreground import load_configuration
from agent.core.foreground_guard import interpret_foreground
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path, validate_manifest

PREFIX = "SC.SupervisedNav."
ANCHORS = ("Title", "TopicsSelected", "ReturnTarget")
ROIS = ([45, 18, 125, 45], [15, 87, 176, 86], [17, 620, 84, 83])
IMAGE_NAMES = ("title.png", "topics.png", "return.png")
FIXTURE = ROOT / "tests/fixtures/supervised_navigation/pipeline.json"
PRIVATE_ROOT = ROOT / "dev_assets/private"


def private_output(report_path):
    path = Path(report_path).resolve()
    require(path.is_relative_to(PRIVATE_ROOT.resolve()) and path.suffix == ".json" and not path.exists(), "报告须为private内的新JSON文件")
    frames = path.parent / (path.stem + "-frames")
    require(frames.resolve().is_relative_to(PRIVATE_ROOT.resolve()) and not frames.exists(), "截图须保存在private且不得覆盖")
    return path, frames


def click_once(report, execute, point, fresh_guard, send):
    if execute is not True or point != (57, 662) or report["input_attempts"]:
        return False
    try:
        binding = fresh_guard()
        # A lost response cannot grant a second call, even in this process.
        report["input_attempts"] += 1
        report["navigation_response"] = validate_navigation_response(send(binding))
        return report["navigation_response"]["ok"]
    except Exception as exc:
        report["input_error"] = str(exc)
        return False


def announcement_matches(recognitions):
    """A shared return button alone must never make a page actionable."""
    names = ["SC.Research.Announcement." + name for name in ANCHORS]
    if type(recognitions) is not list or len(recognitions) != 3:
        return False
    by_name = {item.get("scene"): item for item in recognitions}
    return len(by_name) == 3 and set(by_name) == set(names) and all(
        by_name[name].get("hit") is True and type(by_name[name].get("score")) in (int, float)
        and 0.95 <= by_name[name]["score"] <= 1 for name in names)


def home_matches(recognitions):
    names = {"SC.Research.HomePostcondition.Mission", "SC.Research.HomePostcondition.FesText"}
    return len(recognitions) == 2 and {r["scene"] for r in recognitions} == names and all(
        r["hit"] is True and type(r.get("score")) in (int, float) and 0.95 <= r["score"] <= 1 for r in recognitions)


def fixed_pipeline(execute=False):
    require(type(execute) is bool, "执行开关必须为布尔值")
    data = load_json(FIXTURE)
    anchors = [PREFIX + "Announcement." + name for name in ANCHORS]
    require(set(data) == {PREFIX + name for name in (
        "Observe", "Announcement.Title", "Announcement.TopicsSelected", "Announcement.ReturnTarget",
        "Announcement.ReadOnly", "Announcement.Click", "Unknown", "Error")}, "导航节点白名单变化")
    for anchor, roi, image in zip(anchors, ROIS, IMAGE_NAMES):
        require(data[anchor] == {"recognition": "TemplateMatch", "template": image, "roi": roi,
            "threshold": 0.95, "method": 5, "action": "DoNothing", "pre_delay": 0, "post_delay": 0}, "导航锚点变化")
    require(data[PREFIX + "Announcement.ReadOnly"] == {"recognition": "And", "all_of": anchors,
        "action": "DoNothing", "pre_delay": 0, "post_delay": 0}, "必须同时满足三个锚点")
    require(data[PREFIX + "Announcement.Click"] == {"enabled": False, "recognition": "And", "all_of": anchors,
        "action": "Click", "target": [57, 662, 1, 1], "on_error": [PREFIX + "Error"],
        "pre_delay": 0, "post_delay": 0}, "固定导航输入必须默认关闭")
    require(data[PREFIX + "Observe"] == {"recognition": "DirectHit", "action": "DoNothing",
        "next": [PREFIX + "Announcement.ReadOnly", PREFIX + "Unknown"], "on_error": [PREFIX + "Error"],
        "timeout": 1000, "rate_limit": 0, "pre_delay": 0, "post_delay": 0}, "导航入口变化")
    require(data[PREFIX + "Unknown"] == {"recognition": "Or", "any_of": [PREFIX + "Announcement.ReadOnly"],
        "inverse": True, "action": "StopTask", "pre_delay": 0, "post_delay": 0}, "未知状态必须停止")
    require(data[PREFIX + "Error"] == {"recognition": "DirectHit", "action": "StopTask", "pre_delay": 0,
        "post_delay": 0}, "异常必须停止")
    if execute:
        data[PREFIX + "Announcement.Click"]["enabled"] = True
        data[PREFIX + "Observe"]["next"][0] = PREFIX + "Announcement.Click"
    return data


def load_guard(manifest, sources):
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    data = validate_manifest(load_json(manifest), manifest.parent)
    origin = load_json(sources)
    require([s["name"] for s in data["scenes"]] == ["SC.Research.Announcement." + name for name in ANCHORS], "公告守卫必须三锚点")
    for scene, roi in zip(data["scenes"], ROIS):
        require(scene["recognition"] == "TemplateMatch" and scene["roi"] == roi and scene["threshold"] == 0.95, "禁止改守卫阈值或ROI")
    templates = {t["template"]: t["template_sha256"] for t in origin["templates"]}
    require(len(templates) == 3 and set(templates) == {s["template"] for s in data["scenes"]}, "模板来源缺失")
    for scene in data["scenes"]:
        require(hashlib.sha256(template_path(scene, manifest.parent).read_bytes()).hexdigest() == templates[scene["template"]], "模板来源哈希不符")
    samples = {s["derived"]: s for s in origin["samples"]}
    require(set(samples) == {s["image"] for s in data["samples"]}, "样本来源不完整")
    for sample in data["samples"]:
        source = samples[sample["image"]]
        require(type(source["expected_announcement"]) is bool, "人工真值须为布尔值")
        require(hashlib.sha256((manifest.parent / sample["image"]).read_bytes()).hexdigest() == source["derived_sha256"], "离线样本变化")
    require(any(s["expected_announcement"] and s["channel"] == "native_browser_host_png" for s in samples.values())
            and any(not s["expected_announcement"] for s in samples.values()), "须包含原生跨通道正例和负例")
    digest = hashlib.sha256(manifest.read_bytes() + sources.read_bytes()).hexdigest()
    return data, samples, digest


def send_navigation(session, status, frame_sha256):
    request = navigation_request(session, status, frame_sha256)
    with socket.create_connection((session["host"], session["port"]), timeout=5) as connection:
        connection.settimeout(10)
        connection.sendall((json.dumps(request) + "\n").encode())
        with connection.makefile("rb") as stream:
            line = stream.readline(4097)
        require(len(line) <= 4096 and line.endswith(b"\n"), "导航响应截断或过长")
        return validate_navigation_response(json.loads(line))


def run(args, report):
    _, evidence = private_output(args.report)
    data, truth, digest = load_guard(args.manifest, args.sources)
    pipeline = fixed_pipeline(args.execute)
    recognizer = MaaRecognizer()
    base = args.manifest.resolve().parent
    offline = []
    for sample in data["samples"]:
        image = read_png(base / sample["image"])
        hits = [recognizer.recognize(scene, image, base) for scene in data["scenes"]]
        offline.append({"id": sample["id"], "matches": announcement_matches(hits), "expected": truth[sample["image"]]["expected_announcement"], "recognitions": hits})
    report["offline"] = offline
    require(all(r["matches"] == r["expected"] for r in offline) and recognizer.input_attempts == 0, "离线三锚点回放未通过")
    report["configuration_sha256"] = digest
    if args.offline_only:
        report.update(passed=True, reason="offline_guard_verified", framework_version=recognizer.version)
        return
    foreground = None
    home = None
    if args.execute:
        fore_data, fore_rules, fore_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
        foreground = (fore_data, fore_rules, fore_digest, args.foreground_manifest.resolve().parent)
        home = validate_manifest(load_json(args.home_manifest), args.home_manifest.resolve().parent)
        require({s["name"] for s in home["scenes"]} == {"SC.Research.HomePostcondition.Mission", "SC.Research.HomePostcondition.FesText"}
                and all(s["recognition"] == "TemplateMatch" and s["threshold"] == 0.95 for s in home["scenes"]), "主页后置守卫无效")
    bridge = BrowserBridge.from_file(args.session)
    initial_status = bridge.status()
    require(initial_status["ready"] and initial_status.get("minimized") is False
            and initial_status.get("window_visible") is True, "宿主未就绪或最小化，拒绝启动Pipeline")
    input_session = None
    if args.execute:
        input_session = validate_navigation_session(load_json(Path(str(args.session) + ".navigation-input.json")), bridge.session["pid"])
    evidence.mkdir(parents=True)
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    import numpy as np
    Tasker.set_stdout_level(LoggingLevelEnum.Off)

    class Controller(CustomController):
        # Maa otherwise routes Click through touch_down/up, bypassing this guard.
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.SupervisedNav.SingleAnnouncementReturn"
        def screencap(self):
            if report.get("capture_error"):
                return np.empty((0, 0, 3), dtype=np.uint8)
            try:
                raw = bridge.screencap()
                path = evidence / f"frame-{len(report['frames']) + 1:03d}.png"
                path.write_bytes(raw)
                report["frames"].append({"image": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
                return read_png(path)
            except Exception as exc:
                report["capture_error"] = str(exc)
                return np.empty((0, 0, 3), dtype=np.uint8)
        def click(self, x, y):
            def fresh_guard():
                require(load_guard(args.manifest, args.sources)[2] == digest, "输入前配置变化")
                image = self.screencap()
                require(image is not None and image.shape == (720, 1280, 3), "输入前截图失败")
                hits = [recognizer.recognize(scene, image, base) for scene in data["scenes"]]
                report["fresh_guard"] = hits
                require(announcement_matches(hits), "输入前公告三锚点不完整")
                require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == foreground[2], "输入前前景配置变化")
                fore_hits = [recognizer.recognize(s, image, foreground[3]) for s in foreground[0]["scenes"]]
                report["fresh_foreground"] = interpret_foreground(fore_hits, (1280, 720), foreground[1])
                require(report["fresh_foreground"]["state"] == "no_known_popup", "已知前景或部分前景阻止导航")
                stamp = report["frames"][-1]["sha256"]
                status = bridge.status()
                navigation_request(input_session, status, stamp)
                return status, stamp
            return click_once(report, args.execute, (x, y), fresh_guard,
                              lambda binding: send_navigation(input_session, *binding))
        def deny(self, *args): return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args): return None

    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded, "Maa游戏截图控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix="sc-supervised-nav-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/navigation.json").write_text(json.dumps(pipeline), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "Maa导航Pipeline载入失败")
        for scene, name in zip(data["scenes"], IMAGE_NAMES):
            require(resource.override_image(name, read_png(template_path(scene, base), expected_size=None)), "Maa模板载入失败")
        require(tasker.bind(resource, controller) and tasker.inited, "Maa游戏Pipeline初始化失败")
        job = tasker.post_task(PREFIX + "Observe")
        deadline = time.monotonic() + 20
        while not job.done:
            if time.monotonic() >= deadline:
                tasker.post_stop().wait()
                raise RuntimeError("Maa导航超时，禁止重试")
            time.sleep(0.02)
        detail = job.get()
        report["node_trace"] = [{"name": n.name, "completed": n.completed,
            "hit": n.recognition.hit if n.recognition else None,
            "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
        report["framework_succeeded"] = job.succeeded
        expected = PREFIX + ("Announcement.Click" if args.execute else "Announcement.ReadOnly")
        names = [n["name"] for n in report["node_trace"]]
        positive = names == [PREFIX + "Observe", expected] and job.succeeded and all(n["completed"] and n["hit"] and n["action_success"] for n in report["node_trace"])
        report.update(passed=bool(positive and not args.execute), reason="readonly_positive_guard" if positive else "guard_or_action_refused", framework_version=recognizer.version)
        if args.execute:
            report.update(passed=False, reason="postcondition_requires_independent_review" if positive else "guard_or_action_refused_no_retry")
            report["postcondition_checks"] = []
            if positive and report.get("navigation_response", {}).get("attempted") is True:
                for _ in range(3):
                    image = controller.screencap()
                    if image is None or image.shape != (720, 1280, 3):
                        break
                    announcement = [recognizer.recognize(s, image, base) for s in data["scenes"]]
                    home_hits = [recognizer.recognize(s, image, args.home_manifest.resolve().parent) for s in home["scenes"]]
                    fore_hits = [recognizer.recognize(s, image, foreground[3]) for s in foreground[0]["scenes"]]
                    state = interpret_foreground(fore_hits, (1280, 720), foreground[1])
                    verified = home_matches(home_hits) and not any(r["hit"] for r in announcement) and state["state"] == "no_known_popup"
                    report["postcondition_checks"].append({"home": home_hits, "announcement": announcement, "foreground": state, "verified": verified})
                    if verified:
                        report.update(passed=True, reason="navigation_postcondition_verified")
                        break
                    if state.get("popup") != "SC.Foreground.Loading":
                        break
                    time.sleep(0.3)


def main(argv=None):
    parser = argparse.ArgumentParser(description="公告三锚点监督导航实验；默认只读。真实日常/消费尚未实现。")
    parser.add_argument("--session", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--home-manifest", type=Path)
    parser.add_argument("--foreground-manifest", type=Path)
    parser.add_argument("--foreground-contracts", type=Path)
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute and args.offline_only or not args.offline_only and args.session is None:
        parser.error("离线模式禁止执行；实机模式需要截图会话")
    if args.execute and any(v is None for v in (args.home_manifest, args.foreground_manifest, args.foreground_contracts)):
        parser.error("执行前需要主页后置及前景拒绝守卫")
    private_output(args.report)
    report = {"passed": False, "input_enabled": args.execute, "input_attempts": 0,
        "navigation": "announcement-back", "resource_consumption": False, "frames": [], "node_trace": []}
    try:
        run(args, report)
    except Exception as exc:
        report.update(passed=False, reason="refused_or_uncertain_no_retry", error=str(exc))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"passed": report["passed"], "reason": report["reason"], "input_attempts": report["input_attempts"], "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
