"""Pure validation for the separate Grade Fes endpoint (``<session>.grade-input.json``).

The host opens it only with ``--enable-supervised-grade``; it uses the daily request format but its own
transport, closed action list and per-game-day one-shot native budgets under
``dev_assets/private/daily_grade_native/<game-day>/<action>.json``. Mirrors
``DailyNavigationPolicy.GradeActions`` and ``SupervisedDailyBridge.Grade`` in the C# host.
"""
from __future__ import annotations

import math
import re
import secrets
from pathlib import Path

from agent.core.browser_bridge import validate_status

ROOT = Path(__file__).resolve().parent.parent
TRANSPORT = "webview2-composition-grade"
SESSION_SUFFIX = ".grade-input.json"
MAX_FRAME_AGE_MS = 5000
GRADE_ACTION_POINTS = {"fes-grade-entry": (1188, 541), "fes-help-close": (640, 600), "grade-join-open": (1183, 620),
                       "grade-mode-grade": (1167, 453), "grade-back": (57, 662), "grade-difficulty-easy": (332, 360),
                       "grade-difficulty-normal": (638, 360), "grade-difficulty-hard": (942, 360)}
CONSUMING = ("grade-difficulty-easy", "grade-difficulty-normal", "grade-difficulty-hard")
BUDGET_ROOT = ROOT / "dev_assets/private/daily_grade_native"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def budget_path(action, game_day):
    require(action in GRADE_ACTION_POINTS and isinstance(game_day, str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", game_day) is not None, "Grade 原生预算动作或游戏日无效")
    return BUDGET_ROOT / game_day / (action + ".json")


def validate_session(data, capture_pid):
    expected = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only", "transport", "actions",
                "budget_paths"}
    require(isinstance(data, dict) and set(data) == expected, "Grade 会话字段无效")
    require(type(data["version"]) is int and data["version"] == 1 and data["host"] == "127.0.0.1"
            and type(data["port"]) is int and 1 <= data["port"] <= 65535, "Grade 只允许本地版本1端点")
    require(type(capture_pid) is int and capture_pid > 0 and type(data["pid"]) is int and data["pid"] == capture_pid,
            "Grade 端点与截图会话PID不匹配")
    require(data["input_enabled"] is True and data["browser_demo_only"] is False and data["transport"] == TRANSPORT,
            "Grade 端点未显式启用或接口不匹配")
    require(isinstance(data["token"], str) and re.fullmatch(r"[0-9a-fA-F]{64}", data["token"]) is not None, "Grade 认证字段无效")
    require(data["actions"] == list(GRADE_ACTION_POINTS), "Grade 动作白名单必须精确匹配")
    paths = data["budget_paths"]
    require(isinstance(paths, dict) and set(paths) == set(GRADE_ACTION_POINTS)
            and all(isinstance(p, str) for p in paths.values()), "Grade 预算字段必须精确匹配")
    days = {Path(p).resolve().parent.name for p in paths.values()}
    require(len(days) == 1, "Grade 原生预算必须属于同一游戏日")
    day = days.pop()
    for action, path in paths.items():
        require(Path(path).resolve() == budget_path(action, day).resolve(), "Grade 必须使用每游戏日固定单次预算")
    return dict(data)


def session_day(session):
    return Path(next(iter(session["budget_paths"].values()))).resolve().parent.name


def navigate_request(session, status, frame_sha256, action, nonce=None):
    """Bind one fixed Grade action after the caller's fresh positive Maa guard."""
    validate_session(session, session.get("pid"))
    validate_status(status)
    require(status.get("composition_game") is True and status["browser_demo"] is False and status["ready"] is True,
            "Grade 必须绑定已就绪的Composition游戏截图")
    require(status.get("minimized") is False and status.get("window_visible") is True and status.get("webview_visible") is True,
            "Grade 窗口必须可见且非最小化")
    require(status.get("supervised_grade") is True and status.get("supervised_daily") is True
            and status.get("supervised_navigation") is None, "宿主未独立启用 --enable-supervised-grade")
    require(action in GRADE_ACTION_POINTS and action in session["actions"], "未知 Grade 动作")
    require(isinstance(frame_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", frame_sha256) is not None
            and status.get("frame_sha256") == frame_sha256, "Grade 正向守卫截图已变化")
    age = status.get("frame_age_ms")
    require(type(age) in (int, float) and math.isfinite(age) and 0 <= age <= MAX_FRAME_AGE_MS, "Grade 正向守卫截图已超时")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    require(type(width) is int and type(height) is int and abs(width - 1280) <= 2 and abs(height - 720) <= 2,
            "Grade 截图源尺寸不符合规范画布")
    nonce = secrets.token_hex(16) if nonce is None else nonce
    require(isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{32}", nonce) is not None, "Grade 单次编号无效")
    return {"token": session["token"], "command": "daily-navigate", "action": action, "frame_sha256": frame_sha256,
            "source_width": width, "source_height": height, "nonce": nonce}
