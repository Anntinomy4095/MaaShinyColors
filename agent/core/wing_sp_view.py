"""Recorded small skill-panel view; fixed slots, no inferred drag coordinates."""
from __future__ import annotations

from agent.core import wing_skill_panel as panel

SMALL_X0, SMALL_Y0 = 639., 348.75
SMALL_DX, SMALL_DY, SMALL_LIFT = 58.5, 33.75, 25.
ZOOM_OUT = "skill_zoom_out"
ZOOM_RECT = (1199, 434, 55, 54)


def label_point(slot):
    return SMALL_X0 + SMALL_DX * slot[0], SMALL_Y0 + SMALL_DY * slot[1]


def node_rect(slot):
    x, y = label_point(slot)
    return round(x - 16), round(y - SMALL_LIFT - 11), 32, 22


# Empty background in the small view: a tap there closes the info bar (live 2026-10-14, supervised: the frame after
# the tap at (1100,140) was byte-identical to the small view before any node was selected).
INFO_CLOSE = "skill_info_close"
INFO_CLOSE_RECT = (1085, 125, 30, 30)


def _overlaps(rect, other):
    x, y, w, h = rect
    a, b, c, d = other
    return not (x + w <= a or a + c <= x or y + h <= b or b + d <= y)


def _clear(slot):
    """On screen and not under the fixed overlays (status box, title, zoom buttons). The info bar is NOT an obstacle:
    nodes under it are tapped after closing the bar (live 2026-10-14: the lower petals' outer gold skills sit there)."""
    x, y, w, h = node_rect(slot)
    if not (0 <= x and 40 <= y and x + w <= 1280 and y + h <= 720):
        return False
    return not any(_overlaps((x, y, w, h), r) for r in ((0, 84, 328, 208), (0, 0, 420, 70), (1188, 222, 78, 76),
                                                         (1188, 422, 78, 76)))


def under_info_bar(slot) -> bool:
    return _overlaps(node_rect(slot), panel.INFO_BAR)


SMALL_SLOTS = tuple((i, k) for i in range(-6, 7) for k in range(-10, 13)
                    if (i + k) % 2 == 1 and 2 <= panel.hex_distance((i, k)) <= 5
                    and panel.petal_of((i, k)) and _clear((i, k)))


def action_intent(slot):
    return "skill_node_small_" + panel.slot_name(tuple(slot))


SMALL_INTENTS = {action_intent(slot): slot for slot in SMALL_SLOTS}


def read_panel(nodes):
    """Default view or #625's stable centred small view; shifted/transition frames stay unreadable."""
    result = panel.read_panel(nodes)
    if result["canonical"]:
        return {**result, "view": "canonical"}
    nodes = [n for n in nodes or () if n.get("label")]
    widths = sorted(n["label"][2] for n in nodes)
    if len(nodes) < panel.CANONICAL_MIN_LABELS or not 34 <= widths[len(widths) // 2] <= 43:
        return result
    matched, dx, dy = {}, [], []
    for n in nodes:
        x, y, w, h = n["label"]
        cx, cy = x + w / 2, y + h / 2
        slot = (round((cx - SMALL_X0) / SMALL_DX), round((cy - SMALL_Y0) / SMALL_DY))
        px, py = label_point(slot)
        if (sum(slot) % 2 == 1 and abs(cx - px) <= 6 and abs(cy - py) <= 6):
            # Small OCR can truncate SP:20 to SP:2 (#625 right inner node).
            # Keep its geometry, but never turn that text into a spendable cost.
            matched[slot] = {**n, "cost": None} if 0 < (n.get("cost") or 0) < 10 else n
            dx.append(cx - px)
            dy.append(cy - py)
    result = {**result, "view": "small", "width": widths[len(widths) // 2], "on_lattice": len(matched)}
    if len(matched) < panel.CANONICAL_MIN_SHARE * len(nodes):
        return {**result, "reason": "small_view_transition"}
    offset = (sorted(dx)[len(dx) // 2], sorted(dy)[len(dy) // 2])
    result["offset"] = tuple(round(v, 1) for v in offset)
    if any(abs(v) > 4 for v in offset):
        return {**result, "reason": "small_view_shifted"}
    ring = [matched.get(s) for s in panel.INNER_RING]
    if sum(n is not None and n.get("cost") in panel.INNER_RING_COSTS for n in ring) < 3 or any(
            n is not None and n.get("cost") is not None and n.get("cost") not in panel.INNER_RING_COSTS for n in ring):
        return {**result, "reason": "inner_ring_mismatch"}
    return {**result, "canonical": True, "reason": "", "slots": {
        panel.slot_intent(s): {"slot": list(s), "cost": n.get("cost"), "state": n.get("state"),
                              "label": list(n["label"]), "petal": panel.petal_of(s),
                              "action_intent": action_intent(s)}
        for s, n in matched.items() if s in SMALL_SLOTS}}
