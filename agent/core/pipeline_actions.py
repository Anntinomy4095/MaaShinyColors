"""The single source of truth for the ``pipeline-click`` vocabulary: ``assets/input/pipeline_actions.json``.

The C# host embeds the same file (``SC.PipelineActions.json``), validates it with the same rules and writes the
SHA-256 of the embedded bytes into ``<session>.pipeline-input.json``; the Python controller refuses a session whose
table hash differs from the file it loaded, so the two sides cannot drift silently.

Each action is one on-screen button: ``id``, ``family``, ``consuming`` and ``rect`` = [x, y, w, h] on the canonical
1280x720 canvas. Version 1 requires disjoint rects; version 2 permits overlap only with unique node bindings.
The controller resolves Maa's sampled point using the completed recognition permit's node and action, never a
coordinate alone. Disabled version-2 entries reserve future nodes without enabling input. The host chooses a point inside the
rect shrunk by ``margin_percent`` on each side (centre when the inner rect is smaller than ``min_inner_px``).
The table lives outside ``assets/resource`` because the schema check treats every JSON there as a Pipeline file.

B5b: an action may carry ``wheel`` (one of ±60/±120/±240, never consuming). Its Pipeline node is still a Click
whose target is the rect; the controller maps the point to the action id exactly as for a click, and the host sends a
pointer move plus that fixed wheel delta inside the rect instead of a button gesture (no coordinates, no button).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Development checkout first, then the installed layout (tools/install.py copies assets/input to <install>/input).
CANDIDATES = (ROOT / "assets" / "input" / "pipeline_actions.json", ROOT / "input" / "pipeline_actions.json")
CANVAS = (1280, 720)
ID_PATTERN = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
TOP_KEYS = {"version", "canvas", "margin_percent", "min_inner_px", "families", "actions"}
ACTION_KEYS = {"id", "family", "consuming", "rect", "source", "nodes"}
WHEEL_DELTAS = (-240, -120, -60, 60, 120, 240)
NODE_PATTERN = re.compile(r"\ASC\.[A-Za-z0-9_.]+\Z")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def is_drag_node(node, action):
    """SC.PipelineDrag.v1: a Swipe node of a drag_rect action starts at its recognised box (``begin: true``, from an
    ``And`` whose box_index picks the custom recognition) and ends at a fixed rect inside drag_rect."""
    if not isinstance(node, dict) or node.get("action") != "Swipe" or getattr(action, "drag_rect", None) is None:
        return False
    end = node.get("end")
    if not (isinstance(end, list) and len(end) == 4 and all(type(v) is int for v in end)):
        return False
    x, y, w, h = end
    dx, dy, dw, dh = action.drag_rect
    duration = node.get("duration", 200)
    return (node.get("begin") is True and node.get("recognition") == "And" and isinstance(node.get("box_index"), int)
            and w >= 1 and h >= 1 and dx <= x and dy <= y and x + w <= dx + dw and y + h <= dy + dh
            and type(duration) is int and 150 <= duration <= 3000
            and node.get("begin_offset") in (None, [0, 0, 0, 0]) and node.get("end_offset") in (None, [0, 0, 0, 0]))


def is_match_row_target(name, node, action_id):
    """Only the six Match + nodes may click their observed row instead of a fixed rectangle."""
    for slot in range(1, 7):
        if name == f"SC.Match.Skip.Plus{slot}":
            return (action_id == f"match-skip-plus-{slot}" and node.get("target") is True
                    and node.get("recognition") == "And" and node.get("box_index") == 1
                    and node.get("all_of") == ["SC.Nav.MatchSkip.Guard", f"SC.Match.Plan.Plus{slot}"])
    return False


@dataclasses.dataclass(frozen=True)
class Action:
    id: str
    family: str
    consuming: bool
    rect: tuple  # (x, y, w, h)
    nodes: tuple
    wheel: int = None  # fixed wheel delta of a scroll action (None: click)
    enabled: bool = True
    drag_rect: tuple = None  # SC.PipelineDrag.v1: drag-only action; the drag must end inside this rect

    def drag_contains(self, point):
        if self.drag_rect is None:
            return False
        x, y, w, h = self.drag_rect
        return x <= point[0] < x + w and y <= point[1] < y + h

    def contains(self, point):
        x, y, w, h = self.rect
        return x <= point[0] < x + w and y <= point[1] < y + h


def _disjoint(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay


@dataclasses.dataclass(frozen=True)
class ActionTable:
    actions: tuple
    margin_percent: int
    min_inner_px: int
    families: tuple
    sha256: str
    version: int = 1

    @property
    def ids(self):
        return [a.id for a in self.actions]

    @property
    def consuming_ids(self):
        return [a.id for a in self.actions if a.consuming]

    def get(self, action_id):
        for action in self.actions:
            if action.id == action_id:
                return action
        raise ValueError("未知 Pipeline 动作：" + str(action_id))

    def at(self, point, families=None, *, node=None):
        """Enabled candidates; overlapping coordinates require an exact recognition-node binding."""
        return [a for a in self.actions if a.enabled and (families is None or a.family in families)
                and (node is None or node in a.nodes) and a.contains(point)]

    def inner_rect(self, action_id):
        """Rect shrunk by ``ceil(margin_percent% * size)`` per side; ``None`` when too small (use the centre)."""
        x, y, w, h = self.get(action_id).rect
        mx = (w * self.margin_percent + 99) // 100
        my = (h * self.margin_percent + 99) // 100
        iw, ih = w - 2 * mx, h - 2 * my
        if iw < self.min_inner_px or ih < self.min_inner_px:
            return None
        return (x + mx, y + my, iw, ih)

    def centre(self, action_id):
        x, y, w, h = self.get(action_id).rect
        return (x + w // 2, y + h // 2)

    def sample_point(self, action_id, randrange):
        """Same rule as C# ``PipelineActionTable.Sample``: uniform integer point in the inner rect."""
        inner = self.inner_rect(action_id)
        if inner is None:
            return self.centre(action_id)
        x, y, w, h = inner
        return (x + randrange(w), y + randrange(h))

    def node_actions(self, *, include_disabled=False):
        """Pipeline node name -> action id for every declared node."""
        return {node: a.id for a in self.actions if a.enabled or include_disabled for node in a.nodes}


def parse_table(raw):
    """Validate the raw JSON bytes with the same rules as the C# host's ``PipelineActionTable.Parse``."""
    _require(isinstance(raw, (bytes, bytearray)), "动作表必须为字节")
    data = json.loads(bytes(raw).decode("utf-8"))
    _require(isinstance(data, dict) and set(data) == TOP_KEYS, "动作表顶层字段无效")
    version = data["version"]
    _require(type(version) is int and version in (1, 2), "动作表版本必须为1或2")
    _require(data["canvas"] == list(CANVAS), "动作表画布必须为1280x720")
    margin, min_inner = data["margin_percent"], data["min_inner_px"]
    _require(type(margin) is int and 20 <= margin <= 45, "边距须为20–45%")
    _require(type(min_inner) is int and 2 <= min_inner <= 64, "最小内框须为2–64像素")
    families = data["families"]
    _require(type(families) is list and families and len(set(families)) == len(families)
             and all(isinstance(f, str) and ID_PATTERN.match(f) for f in families), "动作族列表无效")
    items = data["actions"]
    _require(type(items) is list and items, "动作表为空")
    actions, ids = [], set()
    for item in items:
        extras = {"wheel", "enabled", "drag_rect"} if version == 2 else {"wheel"}
        _require(isinstance(item, dict) and ACTION_KEYS <= set(item) <= ACTION_KEYS | extras, "动作字段无效")
        action_id, family, consuming, rect = item["id"], item["family"], item["consuming"], item["rect"]
        _require(isinstance(action_id, str) and ID_PATTERN.match(action_id) and action_id not in ids, "动作 id 无效或重复")
        _require(family in families and type(consuming) is bool, "动作族或消费标记无效：" + action_id)
        _require(type(rect) is list and len(rect) == 4 and all(type(v) is int for v in rect), "动作框无效：" + action_id)
        x, y, w, h = rect
        _require(w >= 1 and h >= 1 and x >= 0 and y >= 0 and x + w <= CANVAS[0] and y + h <= CANVAS[1],
                 "动作框越出画布：" + action_id)
        _require(isinstance(item["source"], str) and item["source"], "动作来源说明缺失：" + action_id)
        nodes = item["nodes"]
        _require(type(nodes) is list and all(isinstance(n, str) and NODE_PATTERN.match(n) for n in nodes),
                 "动作节点列表无效：" + action_id)
        _require(version == 1 or bool(nodes), "版本2动作必须绑定节点：" + action_id)
        enabled = item.get("enabled", True)
        _require(type(enabled) is bool, "动作启用标记无效：" + action_id)
        wheel = item.get("wheel")
        _require(wheel is None or (type(wheel) is int and wheel in WHEEL_DELTAS and not consuming),
                 "滚轮动作的增量只能为 ±60/±120/±240 且不得消费：" + action_id)
        drag = item.get("drag_rect")
        if drag is not None:
            _require(type(drag) is list and len(drag) == 4 and all(type(v) is int for v in drag) and wheel is None
                     and not consuming, "拖动终点框无效、带滚轮或标为消费：" + action_id)
            dx, dy, dw, dh = drag
            _require(dw >= 1 and dh >= 1 and dx >= 0 and dy >= 0 and dx + dw <= CANVAS[0] and dy + dh <= CANVAS[1],
                     "拖动终点框越出画布：" + action_id)
        ids.add(action_id)
        actions.append(Action(action_id, family, consuming, tuple(rect), tuple(nodes), wheel, enabled,
                              tuple(drag) if drag is not None else None))
    if version == 1:
        for index, first in enumerate(actions):
            for second in actions[index + 1:]:
                _require(_disjoint(first.rect, second.rect), f"动作框重叠：{first.id} / {second.id}")
    all_nodes = [n for a in actions for n in a.nodes]
    _require(len(all_nodes) == len(set(all_nodes)), "同一 Pipeline 节点绑定了多个动作")
    return ActionTable(tuple(actions), margin, min_inner, tuple(families), hashlib.sha256(bytes(raw)).hexdigest(), version)


def table_path():
    for path in CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("缺少 pipeline_actions.json")


_DEFAULT = None


def default_table():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = parse_table(table_path().read_bytes())
    return _DEFAULT
