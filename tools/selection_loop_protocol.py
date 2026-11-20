"""Pure, closed protocol for the explicitly enabled bounded S-card selection loop."""
from __future__ import annotations

import math
from pathlib import Path
import re
import secrets

from agent.core.browser_bridge import validate_status

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "dev_assets/private"
ACTION = "training-select-asahi"
POINT = (461, 236)
TRANSPORT = "webview2-composition-training-selection-loop"
BUDGET = PRIVATE / "daily_navigation_loop/training-selection.json"
RESOLUTION = BUDGET.with_name("training-selection-resolution.json")
MAX_ATTEMPTS = 4


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def nonce_valid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def private_path(value):
    require(isinstance(value, str), "私有路径必须为字符串")
    path = Path(value).resolve()
    require(path.is_relative_to(PRIVATE.resolve()), "文件必须位于private")
    return path


# The repository moved from this root; legacy reports keep their original absolute paths.
LEGACY_ROOTS = (r"D:\Machine Learning\maa",)


def legacy_private_path(value):
    """Rebase a recorded path from a known former workspace; callers must still verify its SHA."""
    require(isinstance(value, str), "私有路径必须为字符串")
    for old in LEGACY_ROOTS:
        prefix = old + "\\"
        if value[:len(prefix)].lower() == prefix.lower():
            return private_path(str(ROOT / value[len(prefix):]))
    return private_path(value)


def validate_session(data, pid, resolution_sha256):
    fields = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only",
              "transport", "action", "budget_path", "resolution_path", "resolution_sha256", "max_attempts"}
    require(type(data) is dict and set(data) == fields, "选卡循环会话字段无效")
    require(type(pid) is int and pid > 0 and type(data["pid"]) is int and data["pid"] == pid,
            "选卡循环与截图PID不匹配")
    require(type(data["version"]) is int and data["version"] == 1 and data["host"] == "127.0.0.1"
            and type(data["port"]) is int and 1 <= data["port"] <= 65535 and digest(data["token"]), "仅允许认证本地端点")
    require(data["input_enabled"] is True and data["browser_demo_only"] is False
            and data["transport"] == TRANSPORT and data["action"] == ACTION, "选卡循环未独立启用")
    require(private_path(data["budget_path"]) == BUDGET.resolve()
            and private_path(data["resolution_path"]) == RESOLUTION.resolve()
            and digest(resolution_sha256) and data["resolution_sha256"] == resolution_sha256
            and type(data["max_attempts"]) is int and data["max_attempts"] == MAX_ATTEMPTS,
            "选卡循环必须绑定固定账本、已审查证明及总上限")
    return dict(data)


def frame_binding(session, status, frame_sha256):
    validate_session(session, session.get("pid"), session.get("resolution_sha256"))
    validate_status(status)
    require(status.get("composition_game") is True and status["browser_demo"] is False
            and status["ready"] is True and status.get("training_selection_loop") is True
            and status.get("minimized") is False and status.get("window_visible") is True
            and status.get("webview_visible") is True, "选卡循环必须绑定可见就绪宿主")
    require(digest(frame_sha256) and status.get("frame_sha256") == frame_sha256, "选卡循环截图变化")
    age = status.get("frame_age_ms")
    require(type(age) in (float, int) and math.isfinite(age) and 0 <= age <= 5000, "选卡截图已过期")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    require(type(width) is int and type(height) is int and abs(width - 1280) <= 2 and abs(height - 720) <= 2,
            "选卡循环源尺寸不符")
    return {"token": session["token"], "action": ACTION, "frame_sha256": frame_sha256,
            "source_width": width, "source_height": height}


def select_request(session, status, frame_sha256, nonce=None):
    nonce = secrets.token_hex(16) if nonce is None else nonce
    require(nonce_valid(nonce), "选卡循环nonce无效")
    return {**frame_binding(session, status, frame_sha256), "command": "selection-loop-select", "nonce": nonce}


def confirm_request(session, status, frame_sha256, nonce, proof_path, proof_sha256):
    require(nonce_valid(nonce) and digest(proof_sha256), "选卡确认摘要无效")
    proof = private_path(str(proof_path))
    require(proof.is_file(), "选卡确认缺少私有证明")
    return {**frame_binding(session, status, frame_sha256), "command": "selection-loop-confirm", "nonce": nonce,
            "proof_path": str(proof), "proof_sha256": proof_sha256}


def status_request(session):
    validate_session(session, session.get("pid"), session.get("resolution_sha256"))
    return {"token": session["token"], "command": "selection-loop-status"}


def validate_response(data):
    fields = {"ok", "attempted", "reason", "state", "attempts", "max_attempts"}
    require(type(data) is dict and set(data) == fields and type(data["ok"]) is bool
            and type(data["attempted"]) is bool and isinstance(data["reason"], str) and len(data["reason"]) <= 2048,
            "选卡循环响应无效")
    require(data["state"] in ("ready", "pending", "confirmed", "blocked") and type(data["attempts"]) is int
            and 1 <= data["attempts"] <= MAX_ATTEMPTS and type(data["max_attempts"]) is int
            and data["max_attempts"] == MAX_ATTEMPTS, "选卡循环状态或计数无效")
    return dict(data)
