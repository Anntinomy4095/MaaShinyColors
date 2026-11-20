"""Read-only Maa Pipeline lifecycle and explicit business-result interpretation."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from agent.core.browser_bridge import validate_status

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_DIR = ROOT / "assets/resource/browser_demo"
ENTRY = "SC.Browser.Daily.Observe"
PREFIX = "SC.Browser.Daily."
MAX_STEPS = 2
MAX_SECONDS = 15


def require_demo(status):
    validate_status(status)
    if not status["ready"] or not status["browser_demo"]:
        raise ValueError("每日观察仅允许已就绪的内置离线浏览器演示页面")
    return status


def validate_pipeline(data):
    """Reject executable extensions and changes to the bounded state graph."""
    suffixes = {"Observe", "Ready", "Complete", "Ambiguous", "Unknown", "Error"}
    if not isinstance(data, dict) or set(data) != {PREFIX + name for name in suffixes}:
        raise ValueError("观察Pipeline节点白名单无效")
    allowed = {"recognition", "action", "next", "on_error", "timeout", "rate_limit", "pre_delay", "post_delay", "lower", "upper", "roi", "count", "method", "all_of", "any_of", "inverse"}
    for name, node in data.items():
        if not isinstance(node, dict) or not set(node) <= allowed:
            raise ValueError("观察Pipeline包含未允许字段")
        suffix = name.removeprefix(PREFIX)
        reco = {"Observe": "DirectHit", "Ready": "ColorMatch", "Complete": "ColorMatch", "Ambiguous": "And", "Unknown": "Or", "Error": "DirectHit"}[suffix]
        action = "DoNothing" if suffix in {"Observe", "Ready", "Complete"} else "StopTask"
        if node.get("recognition") != reco or node.get("action") != action:
            raise ValueError("观察Pipeline只允许内置只读识别和DoNothing/StopTask")
        if node.get("pre_delay") != 0 or node.get("post_delay") != 0:
            raise ValueError("观察Pipeline延迟必须为零")
        if suffix != "Observe" and any(key in node for key in ("next", "on_error", "timeout", "rate_limit")):
            raise ValueError("终止节点不允许后续步骤")
        if suffix in {"Ready", "Complete"}:
            roi = node.get("roi")
            if not isinstance(roi, list) or len(roi) != 4 or any(type(v) is not int for v in roi):
                raise ValueError("ROI无效")
            x, y, w, h = roi
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1280 or y + h > 720:
                raise ValueError("ROI超出画布")
            for key in ("lower", "upper"):
                if not isinstance(node.get(key), list) or len(node[key]) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in node[key]):
                    raise ValueError("颜色范围无效")
            if any(a > b for a, b in zip(node["lower"], node["upper"])) or type(node.get("count")) is not int or not 0 < node["count"] <= w * h or node.get("method") != 4:
                raise ValueError("ColorMatch参数无效")
    states = [PREFIX + "Ready", PREFIX + "Complete"]
    entry = data[ENTRY]
    if entry.get("next") != [PREFIX + "Ambiguous", *states, PREFIX + "Unknown"] or entry.get("on_error") != [PREFIX + "Error"] or type(entry.get("timeout")) is not int or not 0 < entry["timeout"] <= 1000 or entry.get("rate_limit") != 0:
        raise ValueError("观察Pipeline必须为有界单次状态分支")
    if data[PREFIX + "Ambiguous"].get("all_of") != states or data[PREFIX + "Unknown"].get("any_of") != states or data[PREFIX + "Unknown"].get("inverse") is not True:
        raise ValueError("观察Pipeline必须显式处理歧义和未知状态")
    for name, node in data.items():
        suffix = name.removeprefix(PREFIX)
        special = {"all_of": "Ambiguous", "any_of": "Unknown", "inverse": "Unknown"}
        if any(key in node and suffix != owner for key, owner in special.items()):
            raise ValueError("识别组合字段超出白名单")
        if suffix not in {"Ready", "Complete"} and any(key in node for key in ("lower", "upper", "roi", "count", "method")):
            raise ValueError("识别参数超出白名单")
    return data


def load_pipeline(resource_dir=RESOURCE_DIR):
    path = Path(resource_dir).resolve()
    if path != RESOURCE_DIR.resolve():
        raise ValueError("观察资源目录不在白名单中")
    files = [p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()]
    if files != ["pipeline/daily_observe.json"]:
        raise ValueError("观察资源仅允许daily_observe.json")
    pipeline_path = (path / files[0]).resolve()
    if not pipeline_path.is_relative_to(path):
        raise ValueError("观察Pipeline路径超出资源目录")
    return validate_pipeline(json.loads(pipeline_path.read_text(encoding="utf-8")))


def interpret_trace(trace, framework_succeeded, failure_reason=None):
    result = {"entry": ENTRY, "node_trace": trace, "actual_state": None, "passed": False, "reason": failure_reason or "pipeline_failed", "summary": "观察失败，已安全停止"}
    if failure_reason:
        return result
    names = [node.get("name") for node in trace]
    if len(names) != MAX_STEPS or names[0] != ENTRY:
        result["reason"] = "invalid_node_trace"
        return result
    suffix = names[-1].removeprefix(PREFIX) if isinstance(names[-1], str) else ""
    result["actual_state"] = {"Ready": "ready", "Complete": "complete", "Unknown": "unknown", "Ambiguous": "ambiguous", "Error": "error"}.get(suffix)
    if suffix in {"Unknown", "Ambiguous", "Error"}:
        result.update(reason={"Unknown": "unknown_state", "Ambiguous": "ambiguous_state", "Error": "pipeline_error"}[suffix], summary="未知、歧义或异常状态，已拒绝继续")
    elif suffix in {"Ready", "Complete"} and framework_succeeded and all(node.get("completed") is True and node.get("recognition_hit") is True and node.get("action_success") is True and node.get("action") == "DoNothing" for node in trace):
        result.update(passed=True, reason="positive_guard" if suffix == "Ready" else "already_complete", summary="可领取，请手动点击" if suffix == "Ready" else "已领取，安全退出")
    return result


def run_daily_observe(controller, bridge, frames=1):
    """Execute ordinary state flow only through post_task, with no input API."""
    from maa.resource import Resource
    from maa.tasker import Tasker

    require_demo(bridge.status())
    data = load_pipeline()
    if type(frames) is not int or not 1 <= frames <= 20:
        raise ValueError("观察次数须在1到20之间")
    resource, tasker = Resource(), Tasker()
    observations = []
    deadline = time.monotonic() + MAX_SECONDS
    # Load an immutable validated snapshot; Maa cannot discover other resource files.
    with tempfile.TemporaryDirectory(prefix="sc-browser-pipeline-") as temporary:
        path = Path(temporary) / "daily_observe.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        if not resource.post_pipeline(path).wait().succeeded or not tasker.bind(resource, controller) or not tasker.inited:
            raise RuntimeError("只读Pipeline初始化失败")
        for index in range(frames):
            require_demo(bridge.status())
            failure = None
            if time.monotonic() >= deadline:
                failure = "time_limit"
                observations.append(interpret_trace([], False, failure))
                break
            job = tasker.post_task(entry=ENTRY)
            while not job.done:
                if time.monotonic() >= deadline:
                    failure = "time_limit"
                    tasker.post_stop().wait()
                    break
                time.sleep(0.01)
            detail = job.get()
            trace = []
            if detail is not None:
                for node in detail.nodes:
                    trace.append({"node_id": node.node_id, "name": node.name, "completed": node.completed, "recognition_hit": node.recognition.hit if node.recognition else None, "recognition": node.recognition.raw_detail if node.recognition else None, "action": node.action.action.value if node.action else None, "action_success": node.action.success if node.action else None})
            try:
                require_demo(bridge.status())
            except (ValueError, RuntimeError, OSError):
                failure = "browser_state_changed"
            failure = failure or ("capture_failed" if getattr(controller, "capture_error", None) else None)
            result = interpret_trace(trace, job.succeeded, failure)
            result.update(index=index + 1, framework_succeeded=job.succeeded, task_id=job.job_id)
            observations.append(result)
            if not result["passed"] or result["actual_state"] == "complete":
                break
    result = dict(observations[-1])
    result.update(observations=observations, frames=[{"index": i + 1, "image": str(p), "dimensions": [1280, 720]} for i, p in enumerate(controller.frame_paths)], max_steps=MAX_STEPS * frames, max_seconds=MAX_SECONDS)
    return result
