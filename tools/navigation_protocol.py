"""Pure validation for the separate, bounded Composition navigation endpoint."""
from __future__ import annotations

import math
import re
import secrets
from pathlib import Path

from agent.core.browser_bridge import validate_status

ROOT = Path(__file__).resolve().parent.parent
NAVIGATION_POINTS = {"announcement-back": (57, 662), "mission-entry": (62, 429), "touch-to-start": (640, 630)}
TRANSPORT = "webview2-composition-navigation"
MAX_FRAME_AGE_MS = 5000
BUDGET_PATH = ROOT / "dev_assets/private/composition_navigation/one-shot.json"
STARTUP_BUDGET_PATH = BUDGET_PATH.with_name("start-one-shot.json")
DAILY_TRANSPORT = "webview2-composition-daily"
DAILY_ACTION_POINTS = {"reward-ack": (640, 520), "login-board-skip": (1221, 56),
                       "announcement-back": (57, 662), "home-mission": (62, 429),
                       "mission-complete-to-mission": (744, 580),
                       "mission-back": (57, 662), "mission-help-close": (640, 603),
                       "home-fes": (1190, 650), "fes-match-entry": (1045, 543),
                       "match-help-close": (640, 603), "match-skip-open": (1190, 548),
                       "match-skip-cancel": (462, 620), "match-back": (57, 662),
                       "training-back": (57, 662), "pickup-close": (1111, 68),
                       "story-skip-open": (1221, 56), "training-help-close": (640, 603),
                       # Same fixed point as the separate one-shot touch-to-start mode; own budget/guards.
                       "game-start": (640, 630)}
DAILY_TRAINING_ACTION_POINTS = {'home-idol':(565,665),'idol-training':(516,299),'training-select-asahi':(461,236),
    'training-bronze-plus':(1085,347),'training-open-confirm':(1173,663),'training-confirm-one-bronze':(740,510),'training-result-close':(640,486),
    'training-list-back':(57,662),'idol-back':(57,662)}
ALL_DAILY_ACTION_POINTS = DAILY_ACTION_POINTS | DAILY_TRAINING_ACTION_POINTS
DAILY_SCROLL_ACTION_POINTS = {"mission-scroll-down": (1000, 430), "mission-scroll-up": (1000, 430),
                              "match-area-scroll-down": (260, 460), "match-area-scroll-up": (260, 460),
                              "match-skip-scroll-down": (1030, 410), "match-skip-scroll-up": (1030, 410)}
DAILY_SCROLL_DELTAS = {action: -240 if action.endswith("-down") else 240 for action in DAILY_SCROLL_ACTION_POINTS}
DAILY_SCROLL_MAX_ATTEMPTS = 12
DAILY_SCROLL_BUDGET_PATHS = {action: ROOT / "dev_assets/private/readonly_scroll" / (action + ".json")
                           for action in DAILY_SCROLL_ACTION_POINTS}
# Click callers keep their closed vocabulary; scroll callers opt into this wider transport scope.
ALL_DAILY_TRANSPORT_ACTION_POINTS = ALL_DAILY_ACTION_POINTS | DAILY_SCROLL_ACTION_POINTS
DAILY_BUDGET_PATHS = {action: BUDGET_PATH if action == "announcement-back" else
                      ROOT / "dev_assets/private/daily_navigation" / (action + ".json")
                      for action in DAILY_ACTION_POINTS}
# Daily shop (separate host switch). Mirrors DailyNavigationPolicy.ShopActions; the three exchange
# confirms are the only consuming actions. Native one-shot budgets live per JST game day (05:00).
DAILY_SHOP_ACTION_POINTS = {"home-shop": (62, 282), "shop-money-tab": (495, 110), "shop-back": (57, 662),
                            "shop-offer-open-1": (166, 607), "shop-offer-open-2": (436, 607), "shop-offer-open-3": (706, 607),
                            "shop-exchange-cancel": (540, 626), "shop-exchange-confirm-1": (740, 626),
                            "shop-exchange-confirm-2": (740, 626), "shop-exchange-confirm-3": (740, 626),
                            "shop-result-close": (640, 497)}
DAILY_SHOP_BUDGET_ROOT = ROOT / "dev_assets/private/daily_shop_native"


def shop_budget_path(action, game_day):
    if action not in DAILY_SHOP_ACTION_POINTS or not isinstance(game_day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", game_day):
        raise ValueError("商店原生预算动作或游戏日无效")
    return DAILY_SHOP_BUDGET_ROOT / game_day / (action + ".json")


def _daily_scopes():
    """Exact action lists the host may advertise: base + training? + scroll? + shop? (in that order)."""
    scopes = []
    for training in (False, True):
        for scroll in (False, True):
            for shop in (False, True):
                actions = list(DAILY_ACTION_POINTS)
                actions += list(DAILY_TRAINING_ACTION_POINTS) if training else []
                actions += list(DAILY_SCROLL_ACTION_POINTS) if scroll else []
                actions += list(DAILY_SHOP_ACTION_POINTS) if shop else []
                scopes.append(actions)
    return scopes


def validate_navigation_session(data, capture_pid, navigation="announcement-back"):
    expected = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only", "transport", "navigation", "budget_path"}
    if not isinstance(data, dict) or set(data) != expected or navigation not in NAVIGATION_POINTS:
        raise ValueError("监督导航会话字段或固定动作无效")
    if type(data["version"]) is not int or data["version"] != 1 or data["host"] != "127.0.0.1":
        raise ValueError("监督导航仅允许本地版本1端点")
    if type(data["port"]) is not int or not 1 <= data["port"] <= 65535:
        raise ValueError("监督导航端口无效")
    if type(capture_pid) is not int or capture_pid <= 0 or type(data["pid"]) is not int or data["pid"] != capture_pid:
        raise ValueError("监督导航与截图会话PID不匹配")
    if data["input_enabled"] is not True or data["browser_demo_only"] is not False or data["transport"] != TRANSPORT or data["navigation"] != navigation:
        raise ValueError("监督导航未显式启用或固定动作不匹配")
    if not isinstance(data["token"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", data["token"]):
        raise ValueError("监督导航认证字段无效")
    if not isinstance(data["budget_path"], str) or Path(data["budget_path"]).resolve() != (STARTUP_BUDGET_PATH if navigation == "touch-to-start" else BUDGET_PATH).resolve():
        raise ValueError("监督导航必须使用本批共享的持久单次预算")
    return dict(data)


def navigation_request(session, status, frame_sha256):
    """Build a fixed request only after a caller's Maa positive screen guard.

    This is transport validation, not screen recognition. The caller must execute
    its independent positive/negative screenshot replay and guarded Maa Pipeline.
    """
    validate_status(status)
    if status.get("composition_game") is not True or status["browser_demo"] is not False or not status["ready"]:
        raise ValueError("监督导航必须绑定已就绪的Composition游戏截图")
    if status.get("minimized") is not False or status.get("window_visible") is not True or status.get("webview_visible") is not True:
        raise ValueError("监督导航窗口必须可见且非最小化")
    navigation = session.get("navigation")
    if navigation not in NAVIGATION_POINTS or status.get("supervised_navigation") != navigation:
        raise ValueError("宿主未启用本批唯一固定导航")
    if not isinstance(frame_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", frame_sha256) or status.get("frame_sha256") != frame_sha256:
        raise ValueError("监督导航正向守卫截图已变化")
    age = status.get("frame_age_ms")
    if type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= MAX_FRAME_AGE_MS:
        raise ValueError("监督导航正向守卫截图已超时")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    if type(width) is not int or type(height) is not int or abs(width - 1280) > 2 or abs(height - 720) > 2:
        raise ValueError("监督导航截图源尺寸不符合规范画布")
    return {"token": session["token"], "command": "navigate", "navigation": navigation,
            "frame_sha256": frame_sha256, "source_width": width, "source_height": height}


def validate_navigation_response(data):
    if not isinstance(data, dict) or set(data) != {"ok", "attempted", "reason"}:
        raise ValueError("监督导航响应字段无效")
    if type(data["ok"]) is not bool or type(data["attempted"]) is not bool or not isinstance(data["reason"], str) or len(data["reason"]) > 2048:
        raise ValueError("监督导航响应类型无效")
    if data["ok"] and not data["attempted"]:
        raise ValueError("监督导航成功响应必须包含真实尝试")
    return dict(data)


def validate_daily_session(data, capture_pid):
    expected = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only",
                "transport", "actions", "budget_paths"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("日常导航会话字段无效")
    if type(data["version"]) is not int or data["version"] != 1 or data["host"] != "127.0.0.1":
        raise ValueError("日常导航只允许本地版本1端点")
    if type(data["port"]) is not int or not 1 <= data["port"] <= 65535:
        raise ValueError("日常导航端口无效")
    if type(capture_pid) is not int or capture_pid <= 0 or type(data["pid"]) is not int or data["pid"] != capture_pid:
        raise ValueError("日常导航与截图会话PID不匹配")
    if data["input_enabled"] is not True or data["browser_demo_only"] is not False or data["transport"] != DAILY_TRANSPORT:
        raise ValueError("日常导航未显式启用或接口不匹配")
    if not isinstance(data["token"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", data["token"]):
        raise ValueError("日常导航认证字段无效")
    if data["actions"] not in _daily_scopes():
        raise ValueError("日常导航动作白名单必须精确匹配")
    paths = data["budget_paths"]
    if not isinstance(paths, dict) or set(paths) != set(data['actions']):
        raise ValueError("日常导航预算字段必须精确匹配")
    shop_days = set()
    for action, path in paths.items():
        if action in DAILY_SHOP_ACTION_POINTS:
            if not isinstance(path, str):
                raise ValueError("商店原生预算路径无效")
            resolved = Path(path).resolve()
            shop_days.add(resolved.parent.name)
            expected_path = shop_budget_path(action, resolved.parent.name)
        else:
            expected_path = DAILY_SCROLL_BUDGET_PATHS.get(action, DAILY_BUDGET_PATHS.get(
                action, ROOT / 'dev_assets/private/daily_navigation' / f'{action}.json'))
        if not isinstance(path, str) or Path(path).resolve() != expected_path.resolve():
            raise ValueError("日常导航必须沿用固定持久单次预算，公告预算不得重置")
    if len(shop_days) > 1:
        raise ValueError("商店原生预算必须属于同一游戏日")
    return dict(data)


def daily_navigation_request(session, status, frame_sha256, action, nonce=None):
    """Bind one fixed navigation after the caller's fresh positive Maa guard."""
    validate_daily_session(session, session.get("pid"))
    validate_status(status)
    if status.get("composition_game") is not True or status["browser_demo"] is not False or not status["ready"]:
        raise ValueError("日常导航必须绑定已就绪的Composition游戏截图")
    if status.get("minimized") is not False or status.get("window_visible") is not True or status.get("webview_visible") is not True:
        raise ValueError("日常导航窗口必须可见且非最小化")
    if action in DAILY_SHOP_ACTION_POINTS and status.get('supervised_shop') is not True:
        raise ValueError('每日商店实验未独立启用')
    if action not in (ALL_DAILY_TRANSPORT_ACTION_POINTS | DAILY_SHOP_ACTION_POINTS) or action not in session['actions'] or status.get("supervised_daily") is not True or status.get("supervised_navigation") is not None:
        raise ValueError("宿主未显式启用独立日常固定导航")
    if action in DAILY_TRAINING_ACTION_POINTS and status.get('supervised_training') is not True:
        raise ValueError('训练单铜券实验未独立启用')
    if action in DAILY_SCROLL_ACTION_POINTS and status.get('supervised_scroll') is not True:
        raise ValueError('只读滚动未独立启用')
    if not isinstance(frame_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", frame_sha256) or status.get("frame_sha256") != frame_sha256:
        raise ValueError("日常导航正向守卫截图已变化")
    age = status.get("frame_age_ms")
    if type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= MAX_FRAME_AGE_MS:
        raise ValueError("日常导航正向守卫截图已超时")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    if type(width) is not int or type(height) is not int or abs(width - 1280) > 2 or abs(height - 720) > 2:
        raise ValueError("日常导航截图源尺寸不符合规范画布")
    nonce = secrets.token_hex(16) if nonce is None else nonce
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("日常导航单次编号无效")
    return {"token": session["token"], "command": "daily-navigate", "action": action,
            "frame_sha256": frame_sha256, "source_width": width, "source_height": height, "nonce": nonce}
