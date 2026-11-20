"""Pure, closed protocol for the opt-in bounded repeatable non-consuming navigation endpoint."""
from __future__ import annotations

import math
from pathlib import Path
import re
import secrets

from agent.core.browser_bridge import validate_status

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "dev_assets/private"
LEDGER_ROOT = PRIVATE / "daily_navigation_repeatable"
TRANSPORT = "webview2-composition-repeatable-navigation"
SESSION_SUFFIX = ".repeatable-navigation.json"
PROOF_KIND = "SC.RepeatableNavigation.Postcondition"
# A fixed Home tap that landed on the inert backdrop of a whitelisted popup (see agent/core/navigation_interrupt.py).
INTERRUPTED_KIND = "SC.RepeatableNavigation.Interrupted"
PRODUCER = ROOT / "tools/run_daily_navigation.py"
SHOP_PRODUCER = ROOT / "tools/run_daily_shop.py"
GRADE_PRODUCER = ROOT / "tools/run_daily_grade.py"
PER_ACTION_MAX = 3
DAY_MAX = 60
# Closed and constant; mirrors RepeatableNavigationBudget.Actions in the C# host.
ACTIONS = ("reward-ack", "login-board-skip", "announcement-back", "home-mission", "mission-complete-to-mission",
           "mission-back", "mission-help-close", "home-fes", "fes-match-entry", "match-help-close", "match-skip-open",
           "match-skip-cancel", "match-back", "training-back", "pickup-close", "story-skip-open",
           "training-help-close", "game-start", "home-idol", "idol-training", "training-list-back", "idol-back",
           "home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel",
           "shop-offer-open-1", "shop-offer-open-2", "shop-offer-open-3", "shop-result-close",
           "fes-grade-entry", "fes-help-close", "grade-join-open", "grade-mode-grade", "grade-back")
# Non-consuming Grade Fes navigation behind --enable-supervised-grade; proofs come only from the grade runner.
GRADE_ACTIONS = ("fes-grade-entry", "fes-help-close", "grade-join-open", "grade-mode-grade", "grade-back")
# Non-consuming shop navigation behind the separate shop switch; proofs come only from the shop runner.
SHOP_ACTIONS = ("home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel",
                "shop-offer-open-1", "shop-offer-open-2", "shop-offer-open-3", "shop-result-close")
TRAINING_PORTAL_ACTIONS = ("home-idol", "idol-training", "training-list-back", "idol-back")
STATES = ("ready", "pending", "confirmed", "interrupted", "blocked")


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


def allowed_actions(training, shop=False, grade=False):
    return [a for a in ACTIONS if (training or a not in TRAINING_PORTAL_ACTIONS) and (shop or a not in SHOP_ACTIONS)
            and (grade or a not in GRADE_ACTIONS)]


def producer_for(action):
    return SHOP_PRODUCER if action in SHOP_ACTIONS else GRADE_PRODUCER if action in GRADE_ACTIONS else PRODUCER


def validate_session(data, pid):
    fields = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only", "transport",
              "ledger_root", "game_day", "per_action_max", "day_max", "actions"}
    require(type(data) is dict and set(data) == fields, "可重复导航会话字段无效")
    require(type(pid) is int and pid > 0 and type(data["pid"]) is int and data["pid"] == pid,
            "可重复导航与截图PID不匹配")
    require(type(data["version"]) is int and data["version"] == 1 and data["host"] == "127.0.0.1"
            and type(data["port"]) is int and 1 <= data["port"] <= 65535 and digest(data["token"]), "仅允许认证本地端点")
    require(data["input_enabled"] is True and data["browser_demo_only"] is False and data["transport"] == TRANSPORT,
            "可重复导航未独立启用")
    require(private_path(data["ledger_root"]) == LEDGER_ROOT.resolve(), "可重复导航必须绑定固定账本目录")
    require(isinstance(data["game_day"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["game_day"]) is not None,
            "游戏日格式无效")
    require(type(data["per_action_max"]) is int and data["per_action_max"] == PER_ACTION_MAX
            and type(data["day_max"]) is int and data["day_max"] == DAY_MAX, "可重复导航上限必须固定")
    require(data["actions"] in [allowed_actions(t, s, g) for t in (False, True) for s in (False, True)
                                for g in (False, True)], "可重复导航动作白名单必须精确匹配")
    return dict(data)


def _action(session, action):
    require(action in ACTIONS and action in session["actions"], "该动作不在可重复导航白名单")
    return action


def frame_binding(session, status, frame_sha256, action):
    validate_session(session, session.get("pid"))
    _action(session, action)
    validate_status(status)
    require(status.get("composition_game") is True and status["browser_demo"] is False and status["ready"] is True
            and status.get("supervised_daily") is True and status.get("repeatable_navigation") is True
            and status.get("supervised_navigation") is None and status.get("minimized") is False
            and status.get("window_visible") is True and status.get("webview_visible") is True,
            "可重复导航必须绑定可见就绪的监督日常宿主")
    if action in TRAINING_PORTAL_ACTIONS:
        require(status.get("supervised_training") is True, "训练入口导航未独立启用")
    if action in GRADE_ACTIONS:
        require(status.get("supervised_grade") is True, "Grade 导航未独立启用")
    if action in SHOP_ACTIONS:
        require(status.get("supervised_shop") is True, "商店导航未独立启用")
    require(digest(frame_sha256) and status.get("frame_sha256") == frame_sha256, "可重复导航截图变化")
    age = status.get("frame_age_ms")
    require(type(age) in (float, int) and math.isfinite(age) and 0 <= age <= 5000, "可重复导航截图已过期")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    require(type(width) is int and type(height) is int and abs(width - 1280) <= 2 and abs(height - 720) <= 2,
            "可重复导航源尺寸不符")
    return {"token": session["token"], "action": action, "frame_sha256": frame_sha256,
            "source_width": width, "source_height": height}


def status_request(session, action):
    validate_session(session, session.get("pid"))
    return {"token": session["token"], "command": "repeatable-status", "action": _action(session, action)}


def navigate_request(session, status, frame_sha256, action, nonce=None):
    nonce = secrets.token_hex(16) if nonce is None else nonce
    require(nonce_valid(nonce), "可重复导航nonce无效")
    return {**frame_binding(session, status, frame_sha256, action), "command": "repeatable-navigate", "nonce": nonce}


def confirm_request(session, status, frame_sha256, action, nonce, proof_path, proof_sha256):
    require(nonce_valid(nonce) and digest(proof_sha256), "可重复导航确认摘要无效")
    proof = private_path(str(proof_path))
    require(proof.is_file(), "可重复导航确认缺少私有证明")
    return {**frame_binding(session, status, frame_sha256, action), "command": "repeatable-confirm", "nonce": nonce,
            "proof_path": str(proof), "proof_sha256": proof_sha256}


def validate_response(data):
    fields = {"ok", "attempted", "reason", "state", "action_count", "day_count"}
    require(type(data) is dict and set(data) == fields and type(data["ok"]) is bool
            and type(data["attempted"]) is bool and isinstance(data["reason"], str) and len(data["reason"]) <= 2048,
            "可重复导航响应无效")
    require(data["state"] in STATES and type(data["action_count"]) is int and 0 <= data["action_count"] <= PER_ACTION_MAX
            and type(data["day_count"]) is int and 0 <= data["day_count"] <= DAY_MAX
            and data["action_count"] <= data["day_count"], "可重复导航状态或计数无效")
    return dict(data)


def status_allows_attempt(response):
    """A read-only status only permits a new attempt when nothing is pending and caps remain."""
    response = validate_response(response)
    return (response["ok"] is True and response["attempted"] is False and response["state"] in ("ready", "confirmed", "interrupted")
            and response["action_count"] < PER_ACTION_MAX and response["day_count"] < DAY_MAX)


def postcondition_proof(action, nonce, session_pid, pre_frame_sha256, post_frame, configuration_sha256,
                        foreground_sha256, post_recognitions, foreground, producer_sha256):
    require(action in ACTIONS and nonce_valid(nonce) and type(session_pid) is int and session_pid > 0
            and digest(pre_frame_sha256) and digest(configuration_sha256) and digest(foreground_sha256)
            and digest(producer_sha256), "可重复导航证明字段无效")
    require(type(post_frame) is dict and set(post_frame) == {"image", "sha256"} and digest(post_frame["sha256"])
            and post_frame["sha256"] != pre_frame_sha256, "后置帧必须为新的私有帧")
    private_path(post_frame["image"])
    require(type(post_recognitions) is list and type(foreground) is dict, "后置识别结果无效")
    return {"version": 1, "kind": PROOF_KIND, "action": action, "passed": True, "resource_consumption": False,
            "input_attempts": 1, "nonce": nonce, "session_pid": session_pid, "pre_frame_sha256": pre_frame_sha256,
            "post_frame": dict(post_frame), "configuration_sha256": configuration_sha256,
            "foreground_sha256": foreground_sha256, "post_recognitions": post_recognitions, "foreground": foreground,
            "producer_path": str(producer_for(action).resolve()), "producer_sha256": producer_sha256}


INTERRUPTION_FIELDS = {"popup", "pre_group", "underlying_group", "click_point", "panel_rects", "panel_margin",
                       "popup_complete", "underlying_present", "post_differs", "expected_post_complete"}


def interrupted_proof(action, nonce, session_pid, pre_frame_sha256, post_frame, configuration_sha256,
                      foreground_sha256, post_recognitions, foreground, producer_sha256, interruption):
    """Same bindings as the postcondition proof plus the closed interruption evidence; never a success."""
    from agent.core.navigation_interrupt import INTERRUPTIBLE_ACTIONS, PANEL_MARGIN, PANEL_RECTS, outside_panels
    proof = postcondition_proof(action, nonce, session_pid, pre_frame_sha256, post_frame, configuration_sha256,
                                foreground_sha256, post_recognitions, foreground, producer_sha256)
    require(action in INTERRUPTIBLE_ACTIONS and type(interruption) is dict and set(interruption) == INTERRUPTION_FIELDS,
            "中断证明字段无效")
    popup = interruption["popup"]
    require(popup in PANEL_RECTS and interruption["panel_rects"] == [list(r) for r in PANEL_RECTS[popup]]
            and interruption["panel_margin"] == PANEL_MARGIN
            and interruption["pre_group"] == interruption["underlying_group"] == INTERRUPTIBLE_ACTIONS[action]
            and outside_panels(tuple(interruption["click_point"]), popup)
            and all(interruption[k] is True for k in ("popup_complete", "underlying_present", "post_differs"))
            and interruption["expected_post_complete"] is False, "中断证明条件不成立")
    return {**proof, "kind": INTERRUPTED_KIND, "passed": False, "interruption": dict(interruption)}
