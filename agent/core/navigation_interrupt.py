"""Interrupted repeatable navigation and stable pre-click guards: pure decisions, no input.

An *interrupted* outcome records that one fixed, non-consuming tap whose page was Home landed on
the inert backdrop of a known popup that covered Home (live 2026-10-02: 「ミッション達成」 over Home,
tap at (62,282) outside the panel, nothing changed). It counts toward the host's caps and unblocks
the game day, but it is never a success and never a reason to resend.

Panel rectangles (x, y, w, h, border included) were measured on native 1280x720 frames:
- MissionCompleted: nav-v25-reward-ack2-20261002-frames/frame-023.png (same geometry in
  shop-20261002-10-home-frames/frame-024.png).
- Pickup: nav-v26-mission-back-20261002-frames/frame-023.png; the 「もう表示しない」 checkbox below
  the panel is interactive and is treated as part of the popup.
- Reward: nav-v24-game-start-rep-20261002-frames/frame-023.png.
"""
from __future__ import annotations

import math

from agent.core.daily_navigation import (GROUPS, PRESENCE_SCORE, UNDERLYING_GROUPS, any_group, complete_group,
                                         foreground_allowed)

INTERRUPTED_KIND = "SC.RepeatableNavigation.Interrupted"
# Closed whitelist; mirrors RepeatableNavigationBudget.InterruptPanels in the C# host.
INTERRUPT_POPUPS = ("MissionCompleted", "Pickup", "Reward")
PANEL_RECTS = {
    "MissionCompleted": ((346, 69, 588, 575),),
    "Pickup": ((129, 31, 1022, 611), (536, 646, 196, 56)),
    "Reward": ((347, 128, 586, 458),),
}
# The tap must be at least this far outside every panel rectangle (measurement tolerance).
PANEL_MARGIN = 8
# Only fixed taps on Home may be recorded as interrupted; mirrors the C# InterruptPreGroup table.
INTERRUPTIBLE_ACTIONS = {"home-mission": "Home", "home-fes": "Home", "home-idol": "Home", "home-shop": "Home"}
# Consecutive post frames that must all show the same interruption before the live runner records it.
INTERRUPT_STABLE_FRAMES = 3

# Stable pre-click guard for taps on Home: two full guard passes at least this many seconds apart
# whose Home and exclusion recognition is identical; the request binds to the second frame.
STABLE_GUARD_INTERVAL_S = 1.0


def outside_panels(point, popup, margin=PANEL_MARGIN):
    if popup not in PANEL_RECTS or type(point) not in (tuple, list) or len(point) != 2:
        return False
    x, y = point
    return all(x < rx - margin or x >= rx + rw + margin or y < ry - margin or y >= ry + rh + margin
               for rx, ry, rw, rh in PANEL_RECTS[popup])


def decide_interrupted(action, point, hits, foreground, pre_frame_sha256, post_frame_sha256, pre_pid, post_pid,
                       expected_post_complete):
    """Return the closed interruption evidence when every condition holds, otherwise a refusal.

    ``hits`` are navigation-profile recognitions of the post frame; ``expected_post_complete`` is the
    caller's own (stricter) check that the action's expected post page is NOT complete.
    """
    checks = {"action_interruptible": action in INTERRUPTIBLE_ACTIONS,
              "same_pid": type(pre_pid) is int and pre_pid > 0 and pre_pid == post_pid,
              "post_differs": (isinstance(pre_frame_sha256, str) and isinstance(post_frame_sha256, str)
                               and len(pre_frame_sha256) == 64 and len(post_frame_sha256) == 64
                               and pre_frame_sha256 != post_frame_sha256),
              "expected_post_not_complete": expected_post_complete is False}
    popups = [g for g in INTERRUPT_POPUPS if complete_group(g, hits)]
    checks["single_whitelisted_popup_complete"] = len(popups) == 1
    popup = popups[0] if len(popups) == 1 else None
    pre_group = INTERRUPTIBLE_ACTIONS.get(action)
    underlying = UNDERLYING_GROUPS.get(popup, ()) if popup else ()
    checks["underlying_is_pre_group"] = pre_group is not None and pre_group in underlying
    checks["pre_group_present_beneath"] = pre_group is not None and any_group(pre_group, hits)
    # Nothing but the popup and its declared underlying pages may be visible.
    checks["no_foreign_group"] = popup is not None and not any(
        any_group(g, hits) for g in GROUPS if g != popup and g not in underlying)
    checks["foreground_allowed"] = popup is not None and type(foreground) is dict and foreground_allowed(popup, foreground)
    checks["click_outside_panel"] = popup is not None and outside_panels(point, popup)
    passed = all(checks.values())
    result = {"interrupted": passed, "checks": checks, "popup": popup}
    if passed:
        result["interruption"] = {"popup": popup, "pre_group": pre_group, "underlying_group": pre_group,
                                  "click_point": [int(point[0]), int(point[1])],
                                  "panel_rects": [list(r) for r in PANEL_RECTS[popup]], "panel_margin": PANEL_MARGIN,
                                  "popup_complete": True, "underlying_present": True, "post_differs": True,
                                  "expected_post_complete": False}
    return result


def _score_level(item):
    score = item.get("score") if type(item) is dict else None
    if type(score) not in (int, float) or not math.isfinite(score):
        return None
    return (score >= PRESENCE_SCORE, score >= .95)


def recognition_signature(hits, names):
    """Matched/unmatched state of the named scenes.

    Each frame of the pair independently passes the full guard (including the 0.80 overlay
    exclusion), so score bands of unmatched anchors only add idle-animation jitter (observed live
    on the Fes menu): the pair must agree on which anchors matched.
    """
    by_name = {}
    for item in hits if type(hits) is list else []:
        if type(item) is dict and item.get("scene") in names:
            by_name.setdefault(item["scene"], []).append(item.get("hit") is True)
    return tuple((name, tuple(by_name.get(name, ()))) for name in sorted(names))


def home_signature_names():
    """Home anchors plus every exclusion anchor of the navigation Home guard."""
    return tuple(n for group in GROUPS for n in GROUPS[group])


def stable_pair(first, second, elapsed_s, interval_s=STABLE_GUARD_INTERVAL_S):
    """Two guard frames are stable when both passed, are ≥ interval apart and share one signature."""
    return (first.get("passed") is True and second.get("passed") is True and type(elapsed_s) in (int, float)
            and math.isfinite(elapsed_s) and elapsed_s >= interval_s and first.get("signature") is not None
            and first.get("signature") == second.get("signature"))


def home_overlays_clear(hits):
    """Navigation-profile exclusion for a tap on Home made by another runner (e.g. the shop's Home stage).

    Live 2026-10-02: 「ミッション達成」 opened over an undimmed Home, so the shop's own Home anchors still
    matched; every non-Home navigation group must be absent at the presence score. Only refuses more.
    """
    return type(hits) is list and bool(hits) and not any(any_group(g, hits) for g in GROUPS if g != "Home")
