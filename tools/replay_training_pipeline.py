#!/usr/bin/env python3
"""训练阶段完整Maa Pipeline离线模拟；绝不连接浏览器或实时输入。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.training_flow import ACTION_POINTS, ANCHORS, PREFIX, fixed_pipeline, load_guard, stage_matches
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path


def replay_pipeline(manifest, sources):
    """Evaluate conjunctions and Click routing against simulated input only."""
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    data, truth, digest = load_guard(manifest, sources)
    pipeline = fixed_pipeline(True)
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker
    require(Library.version() == "v5.12.3", "训练回放要求MaaFramework v5.12.3")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    recognizer = MaaRecognizer()

    class FixtureController(CustomController):
        def __init__(self):
            self.image = None
            self.clicks = []
            self.other_calls = 0
            self.stage = None
            self.captures = 0
            super().__init__()
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.Offline.TrainingPipeline"
        def screencap(self):
            self.captures += 1
            return self.image.copy()
        def click(self, x, y):
            self.clicks.append([x, y])
            return len(self.clicks) == 1 and (x, y) == ACTION_POINTS.get(self.stage)
        def deny(self, *args):
            self.other_calls += 1
            return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args):
            self.other_calls += 1
            return None

    controller = FixtureController()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            "离线训练控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    results = []
    started = time.monotonic()
    total_deadline = started + 240
    with tempfile.TemporaryDirectory(prefix="sc-offline-training-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/training.json").write_text(json.dumps(pipeline), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "完整训练Pipeline载入失败")
        for scene in data["scenes"]:
            require(resource.override_image(scene["template"], read_png(template_path(scene, manifest.parent), expected_size=None)), "训练模板载入失败")
        require(tasker.bind(resource, controller) and tasker.inited, "完整训练Pipeline绑定失败")
        for sample in data["samples"]:
            require(time.monotonic() < total_deadline, "训练回放总预算超时")
            require(load_guard(manifest, sources)[2] == digest, "训练回放配置变化")
            item = truth[sample["image"]]
            stage = item["stage"]
            controller.image = read_png(manifest.parent / sample["image"])
            controller.stage, controller.clicks, controller.other_calls, controller.captures = stage, [], 0, 0
            scenes = [s for s in data["scenes"] if s["name"].startswith(PREFIX + stage + ".")]
            hits = [recognizer.recognize(s, controller.image, manifest.parent) for s in scenes]
            proof = stage_matches(stage, hits)
            deadline = min(total_deadline, time.monotonic() + 15)
            job = tasker.post_task(PREFIX + stage + ".Observe")
            while not job.done:
                if time.monotonic() >= deadline:
                    tasker.post_stop().wait()
                    raise RuntimeError("训练Pipeline超时，停止且禁止重试")
                time.sleep(0.01)
            detail = job.get()
            trace = [{"name": n.name, "completed": n.completed,
                      "recognition_hit": n.recognition.hit if n.recognition else None,
                      "action": n.action.action.value if n.action else None,
                      "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
            expected = item["expected_guard"]
            end = "Click" if expected and stage in ACTION_POINTS else "Guard" if expected else "Unknown"
            clicks = [list(ACTION_POINTS[stage])] if expected and stage in ACTION_POINTS else []
            passed = (proof == expected and job.succeeded and
                      [n["name"] for n in trace] == [PREFIX + stage + ".Observe", PREFIX + stage + "." + end]
                      and all(n["completed"] is True and n["action_success"] is True for n in trace)
                      and controller.clicks == clicks and controller.other_calls == 0
                      and controller.captures > 0 and recognizer.input_attempts == 0)
            results.append({"id": sample["id"], "stage": stage, "expected_guard": expected,
                            "guard_proof": proof, "passed": bool(passed), "node_trace": trace,
                            "recognitions": hits, "simulated_clicks": list(controller.clicks),
                            "other_input_calls": controller.other_calls, "real_input_attempts": 0})
        require(load_guard(manifest, sources)[2] == digest, "回放结束时训练配置变化")
    return {"passed": all(r["passed"] for r in results), "offline_only": True,
            "real_input_enabled": False, "real_input_attempts": 0, "configuration_sha256": digest,
            "sample_count": len(results), "samples": results, "framework_version": Library.version(),
            "simulated_click_attempts": sum(len(r["simulated_clicks"]) for r in results),
            "elapsed_seconds": time.monotonic() - started,
            "limitations": "原始样本参与其自身ROI模板评测，缺少独立当前Lv2→3与普通成功样本；离线通过不代表实机可执行或日常任务完成。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="训练多阶段离线回放，模拟固定点击，无实机输入选项。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    path = args.report.resolve()
    require(path.is_relative_to((ROOT / "dev_assets/private").resolve()) and path.suffix == ".json" and not path.exists(), "训练报告必须private目录新JSON")
    try:
        report = replay_pipeline(args.manifest, args.sources)
    except Exception as exc:
        report = {"passed": False, "offline_only": True, "real_input_attempts": 0, "error": str(exc)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"passed": report["passed"], "sample_count": report.get("sample_count"), "real_input_attempts": 0, "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
