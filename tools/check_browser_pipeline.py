#!/usr/bin/env python3
"""Run the actual daily-observe Pipeline against owned offline screenshots."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.tasker import Tasker
    from agent.core.browser_bridge import create_screenshot_controller
    from agent.core.browser_tasks import run_daily_observe

    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    if Library.version() != "v5.12.3":
        raise ValueError("离线Pipeline验证要求MaaFramework v5.12.3")
    results = []
    with tempfile.TemporaryDirectory(prefix="sc-browser-check-") as temporary:
        for state in ("ready", "complete", "unknown", "ambiguous"):
            class FixtureBridge:
                def status(self):
                    return {"ready": True, "browser_demo": True, "input_enabled": False, "width": 1280, "height": 720}
                def screencap(self):
                    return (ROOT / "tests/fixtures/replay" / (state + ".png")).read_bytes()
            bridge = FixtureBridge()
            controller = create_screenshot_controller(bridge, Path(temporary) / state)
            input_attempts = []
            def deny(*args):
                input_attempts.append(args)
                return False
            for name in ("start_app", "stop_app", "click", "swipe", "touch_down", "touch_move", "touch_up", "click_key", "input_text", "key_down", "key_up", "scroll", "relative_move", "shell"):
                setattr(controller, name, deny)
            if not controller.set_screenshot_use_raw_size(True) or not controller.post_connection().wait().succeeded:
                raise RuntimeError("离线帧控制器初始化失败")
            result = run_daily_observe(controller, bridge)
            passed = result["actual_state"] == state and result["passed"] == (state in {"ready", "complete"}) and not input_attempts
            results.append({"fixture": state, "matched_expected": passed, "input_attempts": len(input_attempts), **result})
    print(json.dumps({"passed": all(r["matched_expected"] for r in results), "results": results}, ensure_ascii=False))
    return 0 if all(r["matched_expected"] for r in results) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
