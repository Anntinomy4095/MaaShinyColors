#!/usr/bin/env python3
"""Run explicit read-only browser observations or an offline Produce sample."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.browser_bridge import BrowserBridge, create_screenshot_controller


def wait_for_bridge(session_path, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            bridge = BrowserBridge.from_file(session_path)
            status = bridge.status()
            if not status["ready"]:
                raise RuntimeError("浏览器截图尚未就绪")
            return bridge, status
        except (OSError, RuntimeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def execute(args, evidence_dir):
    if args.task == "produce-sample":
        from agent.core.produce_input import decide_from_params
        decision = decide_from_params({"sample": True, "low_stamina_threshold": 50})
        return {"passed": True, "summary": "培育样例决策完成（样例数据）：" + decision.chosen.candidate.label, "sample_only": True, "decision": decision.to_dict()}
    bridge, initial_status = wait_for_bridge(args.session, args.wait_seconds)
    if args.task == "daily-observe" and not initial_status["browser_demo"]:
        raise ValueError("每日观察仅允许内置离线浏览器演示页面")
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.tasker import Tasker
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    if Library.version() != "v5.12.3":
        raise ValueError("浏览器观察要求MaaFramework v5.12.3")
    if args.task == "daily-observe":
        from agent.core.browser_tasks import require_demo, run_daily_observe
        class DemoBridge:
            def status(self):
                return require_demo(bridge.status())
            def screencap(self):
                self.status()
                return bridge.screencap()
        capture_bridge = DemoBridge()
    else:
        capture_bridge = bridge
    controller = create_screenshot_controller(capture_bridge, evidence_dir)
    if not controller.set_screenshot_use_raw_size(True) or not controller.post_connection().wait().succeeded:
        raise RuntimeError("只读Maa截图控制器初始化失败")
    if args.task == "daily-observe":
        result = run_daily_observe(controller, capture_bridge, args.frames)
        result.update(framework_version=Library.version(), initial_status=initial_status)
        return result
    frames = []
    summary, passed = "浏览器只读截图完成", True
    for index in range(args.frames):
        status = bridge.status()
        if not status["ready"] or (args.task == "daily-observe" and not status["browser_demo"]):
            raise RuntimeError("浏览器状态变化，拒绝继续观察")
        job = controller.post_screencap().wait()
        if not job.succeeded:
            raise RuntimeError(controller.capture_error or "Maa只读截图失败")
        image = job.get()
        if tuple(image.shape[:2]) != (720, 1280):
            raise ValueError("Maa截图尺寸异常")
        frame = {"index": index + 1, "image": str(controller.frame_paths[-1]), "dimensions": [1280, 720], "status": status}
        frames.append(frame)
        if not passed or summary == "已领取，安全退出":
            break
    return {"passed": passed, "summary": summary, "framework_version": Library.version(), "initial_status": initial_status, "frames": frames}


def main(argv=None):
    parser = argparse.ArgumentParser(description="浏览器只读截图、每日观察或培育样例；不会发送输入。")
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--task", choices=("capture", "daily-observe", "produce-sample"), required=True)
    parser.add_argument("--frames", type=int, choices=range(1, 21), default=1)
    parser.add_argument("--wait-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not 0 <= args.wait_seconds <= 20:
        parser.error("等待时间必须在0到20秒之间")
    evidence_dir = ROOT / "dev_assets/private/browser_tasks" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    report_path = args.report or evidence_dir / "report.json"
    report = {"task": args.task, "input_enabled": False, "readonly": True}
    if args.task == "daily-observe":
        from agent.core.browser_tasks import ENTRY
        report.update(entry=ENTRY, node_trace=[], actual_state=None, reason="not_started")
    try:
        report.update(execute(args, evidence_dir))
        code = 0 if report["passed"] else 1
    except Exception as exc:
        report.update({"passed": False, "summary": "浏览器任务已拒绝或失败", "error": str(exc), "reason": "task_exception"})
        print("浏览器任务失败：" + str(exc), file=sys.stderr)
        code = 2
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print("保存浏览器任务报告失败：" + str(exc), file=sys.stderr)
        code = 2
        report["passed"] = False
    print(json.dumps({"summary": report["summary"], "passed": report["passed"], "report": str(report_path)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
