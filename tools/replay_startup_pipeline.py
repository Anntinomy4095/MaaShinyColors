#!/usr/bin/env python3
"""Run the fixed guarded navigation Pipeline on private PNGs without real input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.replay_screenshots import read_png, require, template_path
from tools.run_supervised_startup import IMAGE_NAMES, PREFIX, fixed_pipeline, load_guard

SAMPLE_SECONDS = 15.0
TOTAL_SECONDS = 120.0


def replay_pipeline(manifest, sources):
    """Verify real Maa And→Click routing using a simulated input boundary only."""
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    data, truth, configuration_sha256 = load_guard(manifest, sources)
    pipeline = fixed_pipeline(True)
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker

    require(Library.version() == "v5.12.3", "完整导航回放要求MaaFramework v5.12.3")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)

    class FixtureController(CustomController):
        def __init__(self):
            self.image = None
            self.clicks = []
            self.touch_calls = []
            self.other_input_calls = 0
            self.capture_count = 0
            super().__init__()

        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.Offline.SupervisedStartupPipeline"
        def screencap(self):
            self.capture_count += 1
            return self.image.copy()
        def click(self, x, y):
            self.clicks.append([x, y])
            return len(self.clicks) == 1 and (x, y) == (640, 630)
        def record_touch(self, *args):
            self.touch_calls.append(list(args))
            return False
        touch_down = touch_move = touch_up = record_touch
        def deny(self, *args):
            self.other_input_calls += 1
            return False
        start_app = stop_app = swipe = click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args):
            self.other_input_calls += 1
            return None

    controller = FixtureController()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            "离线完整Pipeline控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    samples = []
    started = time.monotonic()
    total_deadline = started + TOTAL_SECONDS
    with tempfile.TemporaryDirectory(prefix="sc-offline-navigation-pipeline-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/navigation.json").write_text(json.dumps(pipeline), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "离线完整导航Pipeline载入失败")
        for scene, image_name in zip(data["scenes"], IMAGE_NAMES):
            image = read_png(template_path(scene, manifest.parent), expected_size=None)
            require(resource.override_image(image_name, image), "离线导航模板载入失败")
        require(tasker.bind(resource, controller) and tasker.inited, "离线完整导航Pipeline绑定失败")
        for sample in data["samples"]:
            require(time.monotonic() < total_deadline, "离线完整导航回放总预算超时")
            require(load_guard(manifest, sources)[2] == configuration_sha256, "离线导航配置发生变化")
            controller.image = read_png(manifest.parent / sample["image"])
            controller.clicks = []
            controller.touch_calls = []
            controller.other_input_calls = 0
            controller.capture_count = 0
            expected_startup = truth[sample["image"]]["expected_startup"]
            sample_started = time.monotonic()
            deadline = min(total_deadline, sample_started + SAMPLE_SECONDS)
            job = tasker.post_task(PREFIX + "Observe")
            while not job.done:
                if time.monotonic() >= deadline:
                    tasker.post_stop().wait()
                    raise RuntimeError("离线完整导航Pipeline超时；已停止且不重试")
                time.sleep(0.01)
            detail = job.get()
            trace = [{"name": node.name, "completed": node.completed,
                      "recognition_hit": node.recognition.hit if node.recognition else None,
                      "action": node.action.action.value if node.action else None,
                      "action_success": node.action.success if node.action else None}
                     for node in detail.nodes] if detail else []
            expected_end = PREFIX + ("Startup.Click" if expected_startup else "Unknown")
            expected_clicks = [[640, 630]] if expected_startup else []
            passed = (job.succeeded and [node["name"] for node in trace] == [PREFIX + "Observe", expected_end]
                      and all(node["completed"] is True and node["action_success"] is True for node in trace)
                      and trace[-1]["action"] == ("Click" if expected_startup else "StopTask")
                      and (not expected_startup or all(node["recognition_hit"] is True for node in trace))
                      and controller.clicks == expected_clicks and not controller.touch_calls
                      and controller.other_input_calls == 0 and controller.capture_count > 0
                      and time.monotonic() <= deadline)
            samples.append({"id": sample["id"], "image": str(manifest.parent / sample["image"]),
                            "expected_startup": expected_startup, "passed": bool(passed),
                            "framework_succeeded": bool(job.succeeded), "node_trace": trace,
                            "simulated_clicks": list(controller.clicks),
                            "simulated_click_attempts": len(controller.clicks),
                            "touch_calls": list(controller.touch_calls),
                            "other_input_calls": controller.other_input_calls,
                            "capture_count": controller.capture_count,
                            "real_input_attempts": 0,
                            "elapsed_seconds": time.monotonic() - sample_started})
            require(load_guard(manifest, sources)[2] == configuration_sha256, "回放期间导航配置发生变化")
    return {"passed": all(sample["passed"] for sample in samples), "offline_only": True,
            "real_input_enabled": False, "real_input_attempts": 0,
            "simulated_click_attempts": sum(sample["simulated_click_attempts"] for sample in samples),
            "sample_count": len(samples), "samples": samples, "framework_version": Library.version(),
            "configuration_sha256": configuration_sha256,
            "sample_budget_seconds": SAMPLE_SECONDS, "total_budget_seconds": TOTAL_SECONDS,
            "budget_enforcement": "每轮与Maa调用前后检查；回调不是硬实时，超时停止且不再开始下一轮",
            "elapsed_seconds": time.monotonic() - started}


def private_report(path):
    path = Path(path).resolve()
    require(path.is_relative_to((ROOT / "dev_assets/private").resolve()) and path.suffix.lower() == ".json"
            and not path.exists(), "报告须为dev_assets/private内尚不存在的JSON")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="完整Maa导航Pipeline离线回放；模拟Click，不连接浏览器或输入端点。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    path = private_report(args.report)
    try:
        report = replay_pipeline(args.manifest, args.sources)
    except Exception as exception:
        report = {"passed": False, "offline_only": True, "real_input_enabled": False,
                  "real_input_attempts": 0, "error": str(exception)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"passed": report["passed"], "real_input_attempts": 0,
                      "sample_count": report.get("sample_count"), "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
