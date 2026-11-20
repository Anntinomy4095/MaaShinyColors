"""固定列表滚动合同；只观察内容变化，不推断关卡进度或消耗资源。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from agent.core.daily_config import load_json
from agent.core.daily_navigation import GROUPS, ROIS, actionable
from tools.replay_screenshots import require

PREFIX = "SC.DailyScroll."
GUARD_ACTIONS = {
    "mission-scroll-down": "mission-back", "mission-scroll-up": "mission-back",
    "match-area-scroll-down": "match-skip-open", "match-area-scroll-up": "match-skip-open",
    "match-skip-scroll-down": "match-skip-cancel", "match-skip-scroll-up": "match-skip-cancel",
}
GROUP_NAMES = {"mission-back": "Mission", "match-skip-open": "MatchMain", "match-skip-cancel": "MatchSkip"}
ACTION_POINTS = {action: (1000, 430) if action.startswith("mission-") else
                 (260, 460) if action.startswith("match-area-") else (1030, 410) for action in GUARD_ACTIONS}
DELTAS = {action: (0, -240 if action.endswith("-down") else 240) for action in GUARD_ACTIONS}
BODY_ROIS = {"Mission": [260, 225, 950, 345], "MatchMain": [20, 210, 387, 390],
             "MatchSkip": [65, 189, 1120, 370]}
FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/daily_scroll/pipeline.json"


def allowed(action, hits, foreground):
    if action not in GUARD_ACTIONS or not actionable(GUARD_ACTIONS[action], hits, foreground):
        return False
    names = GROUPS[GROUP_NAMES[GUARD_ACTIONS[action]]]
    selected = [hit for hit in hits if hit.get("scene") in names]
    for hit in selected:
        roi = ROIS[hit["scene"]]
        box = hit["raw_detail"]["filtered"][0].get("box")
        if (hit.get("roi") != roi or type(box) is not list or len(box) != 4
                or not all(type(value) is int for value in box) or box[2] <= 0 or box[3] <= 0
                or box[0] < roi[0] or box[1] < roi[1]
                or box[0] + box[2] > roi[0] + roi[2] or box[1] + box[3] > roi[1] + roi[3]):
            return False
    return True


def expected_pipeline():
    nodes = {}
    base = {"pre_delay": 0, "post_delay": 0}
    for name, roi in ROIS.items():
        nodes[PREFIX + "Anchor." + name] = dict(base, recognition="TemplateMatch", template=name + ".png",
            roi=roi, threshold=.95, method=5, action="DoNothing")
    for action, guard_action in GUARD_ACTIONS.items():
        head = PREFIX + action + "."
        anchors = [PREFIX + "Anchor." + name for name in GROUPS[GROUP_NAMES[guard_action]]]
        nodes[head + "Observe"] = dict(base, recognition="DirectHit", action="DoNothing",
            next=[head + "Guard", head + "Unknown"], on_error=[head + "Error"], timeout=1000, rate_limit=0)
        nodes[head + "Guard"] = dict(base, recognition="And", all_of=anchors, action="DoNothing")
        nodes[head + "Scroll"] = dict(base, enabled=False, recognition="And", all_of=anchors,
            action="Scroll", target=[*ACTION_POINTS[action], 1, 1], dx=DELTAS[action][0], dy=DELTAS[action][1],
            on_error=[head + "Error"])
        nodes[head + "Unknown"] = dict(base, recognition="Or", any_of=[head + "Guard"], inverse=True, action="StopTask")
        nodes[head + "Error"] = dict(base, recognition="DirectHit", action="StopTask")
    return nodes


def fixed_pipeline(action, execute=False):
    require(action in GUARD_ACTIONS and type(execute) is bool, "只允许六个固定滚动动作及布尔执行开关")
    nodes = load_json(FIXTURE)
    require(nodes == expected_pipeline(), "滚动Pipeline默认关闭、守卫、方向或固定区域合同变化")
    if execute:
        head = PREFIX + action + "."
        nodes[head + "Scroll"]["enabled"] = True
        nodes[head + "Observe"]["next"][0] = head + "Scroll"
    return nodes


def fixed_target_binding(action, contact, x, y, pressure):
    """Maa's zero-pressure move only binds a target; it must not send live input."""
    return (action in ACTION_POINTS and all(type(value) is int for value in (contact, x, y, pressure))
            and contact == 0 and pressure == 0 and (x, y) == ACTION_POINTS[action])


def content_snapshot(action, image):
    require(action in GUARD_ACTIONS and image.shape == (720, 1280, 3), "滚动内容截图或动作无效")
    roi = BODY_ROIS[GROUP_NAMES[GUARD_ACTIONS[action]]]
    x, y, width, height = roi
    return {"roi": roi, "sha256": hashlib.sha256(image[y:y + height, x:x + width].tobytes()).hexdigest()}


def observe_change(action, before, after, hits, foreground):
    pre, post = content_snapshot(action, before), content_snapshot(action, after)
    return {"screen_guard_verified": allowed(action, hits, foreground), "pre_content": pre, "post_content": post,
            "content_changed": pre["sha256"] != post["sha256"], "semantic_progress_verified": False,
            "latest_stage_verified": False, "list_boundary_verified": False}
