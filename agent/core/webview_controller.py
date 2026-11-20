"""MaaFramework CustomController for the WebView2 host (migration batch B2).

Screenshots come from the existing read-only ``BrowserBridge``. ``click`` is the single input gate:
disabled by default; when a reviewed run config enables it, a Maa coordinate must resolve to exactly one
fixed action id from the host's closed vocabulary (``DailyNavigationPolicy``), be bound to the latest
fresh frame and go through the host's existing authenticated endpoints with their existing request
formats. Spending actions additionally need a one-time grant from a Python-side ``SpendGate``.

Pipeline mode (B3.5): when the run config enables a family of the shared action table
(``assets/input/pipeline_actions.json``, see ``agent.core.pipeline_actions``), Pipeline Click nodes target the
action's rect, and the point Maa samples inside it is mapped back to the one action whose (disjoint) rect contains
it. Ordinary actions send only their id; the six Match Plus nodes additionally send the observed button point,
validated against the node's catalog column. The host uses that point for Plus and otherwise chooses its own
point inside the rect shrunk by the table margin and enforces the interval,
per-action, per-run and session caps in memory. This controller adds per-run, per-task and session caps from the
run config. A consuming table action (B4: ``shop-exchange-confirm``) is sent only with a one-shot SpendGate grant that
an SC. custom recognition obtained (``LiveSpendAuthority``) for the very frame the click is bound to; without a
grant it is refused.
Pipeline mode also requires a one-shot permit from the complete native parent recognition on its exact captured
frame. The development Maa adapter starts disabled; all Pipeline clicks are refused until it is explicitly enabled
by a test caller. No CLI/config option enables it. Protocol v2 binds both original host sequences to the request and
spend signature; the host rechecks them through the hover waits and immediately before Down/Wheel.
The legacy fixed-point endpoints (daily/grade/repeatable) remain for the legacy families, with their host ledgers.
This module keeps no ledger of its own. The JSONL audit log is evidence, never authorization.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path

from agent.core import pipeline_actions
from agent.core.frame_permit import FrameCapture, FramePermitStore
from agent.core.browser_bridge import validate_png, validate_status
from tools import grade_protocol
from tools import repeatable_navigation_protocol as repeatable_protocol
from tools.navigation_protocol import (DAILY_ACTION_POINTS, DAILY_SCROLL_ACTION_POINTS, DAILY_SCROLL_DELTAS,
                                       DAILY_SHOP_ACTION_POINTS, DAILY_TRAINING_ACTION_POINTS,
                                       daily_navigation_request, validate_daily_session,
                                       validate_navigation_response)

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets/private"
AUDIT_ROOT = PRIVATE / "run_logs"
POLICY_PATH = ROOT / "project_policy.json"
CANVAS = (1280, 720)
MAX_FRAME_AGE_S = 5.0
UUID = "SC.WebView2.Controller"

# Closed click families; each mirrors the C# DailyNavigationPolicy lists through the Python protocols.
CLICK_FAMILIES = {
    "daily": dict(DAILY_ACTION_POINTS),
    "training": dict(DAILY_TRAINING_ACTION_POINTS),
    "shop": dict(DAILY_SHOP_ACTION_POINTS),
    "grade": dict(grade_protocol.GRADE_ACTION_POINTS),
}
# Scroll is a separate family reachable only through ``scroll(dx, dy)`` with the fixed wheel delta.
SCROLL_FAMILY = "scroll"
LEGACY_FAMILIES = frozenset(CLICK_FAMILIES) | {SCROLL_FAMILY}
# Families of the shared pipeline action table (startup/popup/nav/mission); never mixed with legacy families.
PIPELINE_FAMILIES = frozenset(pipeline_actions.default_table().families)
FAMILIES = LEGACY_FAMILIES | PIPELINE_FAMILIES
# Pipeline-table consuming actions (B4: shop-exchange-confirm) join the legacy spending set; each still needs a
# SpendGate grant issued by an SC. custom recognition for the exact frame.
PIPELINE_SPENDING_ACTIONS = frozenset(pipeline_actions.default_table().consuming_ids)
LEGACY_SPENDING_ACTIONS = frozenset({"training-confirm-one-bronze", "shop-exchange-confirm-1", "shop-exchange-confirm-2",
                                     "shop-exchange-confirm-3", *grade_protocol.CONSUMING})
SPENDING_ACTIONS = LEGACY_SPENDING_ACTIONS | PIPELINE_SPENDING_ACTIONS
MAX_GRANTS_PER_ACTION = 3
ENDPOINT_DAILY, ENDPOINT_GRADE, ENDPOINT_REPEATABLE = "daily", "grade", "repeatable"
SESSION_SUFFIXES = {ENDPOINT_DAILY: ".daily-input.json", ENDPOINT_GRADE: grade_protocol.SESSION_SUFFIX,
                    ENDPOINT_REPEATABLE: repeatable_protocol.SESSION_SUFFIX}
# The opt-in pipeline transport (C# PipelineInputPolicy / PipelineInputBridge).
ENDPOINT_PIPELINE = "pipeline"
PIPELINE_SUFFIX = ".pipeline-input.json"
PIPELINE_TRANSPORT = "webview2-composition-pipeline"
PIPELINE_COMMAND = "pipeline-click"
PIPELINE_RUN_BEGIN_COMMAND = "pipeline-run-begin"  # launcher-only (tools/run_tasks.begin_host_run), never sent by the Agent
PIPELINE_PROTOCOL_VERSION = 2
PIPELINE_FRAME_BINDING_PROTOCOL = "SC.PipelineFrameBinding.v2"
PIPELINE_GRANT_DOMAIN = "SC.PipelineSpendGrant.v2"
APPEAL_TIMING_PROTOCOL = "SC.WingAppealTiming.v1"      # hosts 2026-10-14..10-24: W.I.N.G. bar only
APPEAL_TIMING_PROTOCOL_V2 = "SC.AppealTiming.v2"        # 2026-10-24: + the Grade Fes bar (same geometry and plan)
HOLD_PROTOCOL = "SC.PipelineHold.v1"   # 2026-10-14 user: long-press the Match Skip ＋ (non-consuming counter)
HOLD_PROTOCOL_V2 = "SC.PipelineHold.v2"   # 2026-11-01 user: + Produce 「諦める」 (selectable only by a long press)
HOLD_ACTIONS = {HOLD_PROTOCOL: frozenset(f"match-skip-plus-{i}" for i in range(1, 7))}   # per host hold protocol
HOLD_ACTIONS[HOLD_PROTOCOL_V2] = HOLD_ACTIONS[HOLD_PROTOCOL] | {"wing-giveup-hold"}
HOLD_MIN_MS, HOLD_MAX_MS = 300, 4000
DRAG_PROTOCOL = "SC.PipelineDrag.v1"   # 2026-11-18 user (Collabo Fes): Maa Swipe of a table action with drag_rect
DRAG_MIN_MS, DRAG_MAX_MS = 150, 3000
APPEAL_ACTION = "wing-live-appeal-timing"
APPEAL_NODE = "SC.Wing.Click.Live.live_appeal_timing"
GRADE_APPEAL_ACTION = "live-appeal-timing"
GRADE_APPEAL_NODE = "SC.Grade.Live.Timing"
APPEAL_ACTIONS = {APPEAL_ACTION: APPEAL_NODE, GRADE_APPEAL_ACTION: GRADE_APPEAL_NODE}
# Timed actions each advertised host protocol accepts (an older host must never receive a Grade plan).
APPEAL_TIMING_TARGETS = {APPEAL_TIMING_PROTOCOL: {APPEAL_ACTION: APPEAL_NODE},
                         APPEAL_TIMING_PROTOCOL_V2: dict(APPEAL_ACTIONS)}
PIPELINE_LIMIT_KEYS = frozenset({"min_interval_ms", "interval_jitter_max_ms", "per_action_per_minute", "run_max",
                                 "run_spend_max", "grant_max_ttl_ms", "session_max_ms"})
# Controller-side limits (see tools/run_tasks.py). no click caps by default (None); per-node
# max_hit/timeout bound the flows, as in MaaFramework / MAA. An explicit cap still works (developer CLI).
DEFAULT_RUN_MAX_CLICKS = None
DEFAULT_TASK_MAX_CLICKS = None
DEFAULT_SESSION_MAX_S = 14400.0


def action_family(action):
    if action in DAILY_SCROLL_ACTION_POINTS:
        return SCROLL_FAMILY
    for family, points in CLICK_FAMILIES.items():
        if action in points:
            return family
    raise ValueError("未知固定动作：" + str(action))


def action_point(action):
    family = action_family(action)
    return DAILY_SCROLL_ACTION_POINTS[action] if family == SCROLL_FAMILY else CLICK_FAMILIES[family][action]


def all_actions():
    return frozenset(DAILY_SCROLL_ACTION_POINTS).union(*CLICK_FAMILIES.values())


def endpoint_for(action, repeatable, pipeline=False):
    """The pipeline transport takes every action when present; otherwise the legacy runner choice applies,
    where spending actions never use the repeatable ledger."""
    family = action_family(action)
    if pipeline:
        return ENDPOINT_PIPELINE
    if action not in SPENDING_ACTIONS and repeatable and action in repeatable_protocol.ACTIONS:
        return ENDPOINT_REPEATABLE
    return ENDPOINT_GRADE if family == "grade" else ENDPOINT_DAILY


def game_day(now):
    """JST game day (05:00 boundary); only names the audit file, never gates input."""
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=9 - 5)).strftime("%Y-%m-%d")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{%d}" % length, value) is not None


def validate_pipeline_session(data, capture_pid, table=None):
    """Validate ``<session>.pipeline-input.json`` written by the host's PipelineInputBridge against the action table."""
    table = pipeline_actions.default_table() if table is None else table
    expected = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only", "transport", "actions",
                "consuming_actions", "action_table_sha256", "spend_secret", "audit_root", "limits", "frame_binding_protocol"}
    # 2026-10-11 hosts also name the launcher-only run-window handshake; older hosts (lifetime window) omit it.
    # The Agent only validates it here and never sends it (tools/run_tasks.begin_host_run is the launcher's).
    _require(isinstance(data, dict) and set(data) - {"run_begin_command", "appeal_timing_protocol", "hold_protocol", "drag_protocol"} == expected,
             "Pipeline 会话字段无效")
    _require("appeal_timing_protocol" not in data or data["appeal_timing_protocol"] in APPEAL_TIMING_TARGETS,
             "Live 定时协议无效")
    _require("hold_protocol" not in data or data["hold_protocol"] in HOLD_ACTIONS, "长按协议无效")
    _require("drag_protocol" not in data or data["drag_protocol"] == DRAG_PROTOCOL, "拖动协议无效")
    _require(data.get("run_begin_command", PIPELINE_RUN_BEGIN_COMMAND) == PIPELINE_RUN_BEGIN_COMMAND,
             "Pipeline 运行窗口握手命令无效")
    _require(type(data["version"]) is int and data["version"] == PIPELINE_PROTOCOL_VERSION
             and data["frame_binding_protocol"] == PIPELINE_FRAME_BINDING_PROTOCOL,
             "Pipeline 必须使用原子帧绑定 v2（需重新构建宿主），不得回退旧协议")
    _require(data["host"] == "127.0.0.1"
             and type(data["port"]) is int and 1 <= data["port"] <= 65535, "Pipeline 只允许本地版本1端点")
    _require(type(capture_pid) is int and capture_pid > 0 and type(data["pid"]) is int and data["pid"] == capture_pid,
             "Pipeline 端点与截图会话PID不匹配")
    _require(data["input_enabled"] is True and data["browser_demo_only"] is False and data["transport"] == PIPELINE_TRANSPORT,
             "Pipeline 端点未显式启用或接口不匹配")
    _require(_hex(data["token"], 64) and _hex(data["spend_secret"], 64), "Pipeline 认证或授权密钥字段无效")
    actions, consuming = data["actions"], data["consuming_actions"]
    _require(data["action_table_sha256"] == table.sha256, "宿主动作表与 pipeline_actions.json 不一致（在宿主 设置→开发者「重新载入动作表」；2026-10-10 前的旧宿主需重建一次）")
    _require(type(actions) is list and actions == table.ids, "Pipeline 动作词表必须与动作表精确一致")
    _require(type(consuming) is list and consuming == table.consuming_ids, "Pipeline 消费动作集合必须与动作表精确一致")
    _require(isinstance(data["audit_root"], str) and Path(data["audit_root"]).resolve().is_relative_to(PRIVATE.resolve()),
             "Pipeline 宿主审计必须位于 dev_assets/private")
    limits = data["limits"]
    _require(isinstance(limits, dict) and set(limits) == PIPELINE_LIMIT_KEYS
             and all(type(v) is int and v >= 0 for v in limits.values()), "Pipeline 限速字段无效")
    _require(limits["min_interval_ms"] >= 800 and 0 <= limits["interval_jitter_max_ms"] <= 5000
             and 1 <= limits["grant_max_ttl_ms"] <= 5000 and 1 <= limits["session_max_ms"] <= 43_200_000
             and 0 <= limits["run_max"], "Pipeline 限速低于基线")   # run_max 0: the host has no click cap
    return dict(data)


def pipeline_frame_sequences(status):
    """Strict v2 sequence snapshot. A pending capture is never a frame permit."""
    _require(isinstance(status, dict) and status.get("capture_pending") is False, "Pipeline 截图未完成或状态未知")
    values = status.get("capture_seq"), status.get("frame_binding_seq")
    _require(all(type(value) is int and 0 < value < 2**63 for value in values), "Pipeline 帧序号必须为正 int64")
    return values


def pipeline_click_request(session, status, frame_sha256, action, nonce=None, table=None, *, capture_seq, frame_binding_seq,
                           node_name=None, target=None, appeal_timing=None, hold_ms=None, drag_to=None, drag_ms=None):
    """Bind a fixed action to the original permit, never rebase it onto the latest host status."""
    table = pipeline_actions.default_table() if table is None else table
    validate_pipeline_session(session, session.get("pid"), table)
    validate_status(status)
    _require(status.get("composition_game") is True and status["browser_demo"] is False and status["ready"] is True,
             "Pipeline 必须绑定已就绪的Composition游戏截图")
    _require(status.get("minimized") is False and status.get("window_visible") is True
             and status.get("webview_visible") is True, "Pipeline 窗口必须可见且非最小化")
    _require(status.get("pipeline_input") is True and status.get("supervised_navigation") is None,
             "宿主未独立启用 --enable-pipeline-input")
    _require(action in session["actions"], "未知 Pipeline 动作")
    _require(table.get(action).enabled, "Pipeline 动作未启用")
    if table.version == 2:
        _require(isinstance(node_name, str) and table.node_actions().get(node_name) == action,
                 "Pipeline 动作缺少匹配的识别节点")
    _require(all(type(value) is int and 0 < value < 2**63 for value in (capture_seq, frame_binding_seq))
             and pipeline_frame_sequences(status) == (capture_seq, frame_binding_seq), "Pipeline 原始许可帧序号已变化")
    _require(_hex(frame_sha256, 64) and status.get("frame_sha256") == frame_sha256, "Pipeline 正向守卫截图已变化")
    age = status.get("frame_age_ms")
    _require(type(age) in (int, float) and math.isfinite(age) and 0 <= age <= MAX_FRAME_AGE_S * 1000,
             "Pipeline 正向守卫截图已超时")
    width, height = status.get("source_capture_width"), status.get("source_capture_height")
    _require(type(width) is int and type(height) is int and abs(width - 1280) <= 2 and abs(height - 720) <= 2,
             "Pipeline 截图源尺寸不符合规范画布")
    nonce = secrets.token_hex(16) if nonce is None else nonce
    _require(_hex(nonce, 32), "Pipeline 单次编号无效")
    request = {"token": session["token"], "command": PIPELINE_COMMAND, "protocol_version": PIPELINE_PROTOCOL_VERSION,
            "capture_seq": capture_seq, "frame_binding_seq": frame_binding_seq, "action": action, "frame_sha256": frame_sha256,
            "source_width": width, "source_height": height, "nonce": nonce}
    if table.version == 2:
        request["node_name"] = node_name
    if appeal_timing is not None:
        targets = APPEAL_TIMING_TARGETS.get(session.get("appeal_timing_protocol"))
        _require(targets is not None, "宿主尚不支持 Live 定时点击")
        _require(action in APPEAL_ACTIONS and node_name == APPEAL_ACTIONS[action], "定时只用于 Live 停条")
        _require(targets.get(action) == node_name, "宿主尚不支持该停条的定时点击（需重建宿主）")
        _require(type(appeal_timing) is dict
                 and set(appeal_timing) == {"target_down_ms", "period_ms", "latest_down_ms"}
                 and all(type(v) in (int, float) and math.isfinite(v) for v in appeal_timing.values()),
                 "Live 定时参数无效")
        _require(0 <= appeal_timing["target_down_ms"] <= appeal_timing["latest_down_ms"]
                 and appeal_timing["latest_down_ms"] - appeal_timing["target_down_ms"] <= MAX_FRAME_AGE_S * 1000
                 and 900 <= appeal_timing["period_ms"] <= 1050, "Live 定时范围无效")
        request["appeal_timing"] = dict(appeal_timing)
    if hold_ms is not None:
        _require(session.get("hold_protocol") in HOLD_ACTIONS, "宿主尚不支持长按")
        _require(appeal_timing is None and action in HOLD_ACTIONS[session["hold_protocol"]]
                 and action not in session.get("consuming_actions", ()), "宿主不支持该动作长按")
        _require(type(hold_ms) is int and HOLD_MIN_MS <= hold_ms <= HOLD_MAX_MS, "长按时长无效")
        request["hold_ms"] = hold_ms
    entry = table.get(action)
    if entry.drag_rect is not None or drag_to is not None or drag_ms is not None:
        # SC.PipelineDrag.v1: a drag action is only ever dragged, from the recognised start to a point in drag_rect.
        _require(session.get("drag_protocol") == DRAG_PROTOCOL, "宿主尚不支持拖动（需重建宿主）")
        _require(entry.drag_rect is not None and appeal_timing is None and hold_ms is None
                 and action not in session.get("consuming_actions", ()), "该动作不能拖动")
        _require(all(isinstance(p, (list, tuple)) and len(p) == 2 and all(type(v) is int for v in p)
                     for p in (target, drag_to)), "拖动起点或终点无效")
        _require(entry.contains(target), "拖动起点超出动作框")
        _require(entry.drag_contains(drag_to), "拖动终点超出终点框")
        _require(type(drag_ms) is int and DRAG_MIN_MS <= drag_ms <= DRAG_MAX_MS, "拖动时长无效")
        request.update(target=list(target), drag_to=list(drag_to), drag_ms=drag_ms)
        return request
    if target is not None:
        slot = next((i for i in range(1, 7) if action == f"match-skip-plus-{i}"), None)
        _require(slot is not None and node_name == f"SC.Match.Skip.Plus{slot}", "识别位置只用于Match加号")
        _require(isinstance(target, (list, tuple)) and len(target) == 2
                 and all(type(value) is int for value in target), "Match加号位置无效")
        x, y, w, h = table.get(action).rect
        _require(x <= target[0] < x + w and y <= target[1] < y + h, "Match加号位置超出动作框")
        request["target"] = list(target)
    return request


def frame_diagnostics(status):
    """Allowlisted optional host telemetry; never copy session tokens or arbitrary status text."""
    status = status if isinstance(status, dict) else {}
    result = {}
    for key in ("capture_seq", "frame_binding_seq"):
        value = status.get(key)
        result[key] = value if type(value) is int and 0 <= value < 2**63 else None
    result["capture_pending"] = status.get("capture_pending") if type(status.get("capture_pending")) is bool else None
    sha = status.get("frame_sha256")
    result["frame_sha256"] = sha if _hex(sha, 64) else None
    age = status.get("frame_age_ms")
    result["frame_age_ms"] = age if type(age) in (int, float) and math.isfinite(age) and age >= 0 else None
    for key in ("capture_started_ms", "capture_completed_ms", "capture_estimated_ms"):
        value = status.get(key)
        result[key] = value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    sources = {"capture_bridge", "local_demo_guard"}
    events = sources | {"navigation_starting", "manual_mouse_move", "manual_mouse_down", "manual_mouse_up",
                        "navigation_gesture", "selection_gesture", "pipeline_gesture", "repeatable_gesture",
                        "daily_gesture", "capture_starting", "capture_failed", "desktop_locked", "desktop_unlocked"}
    source, event = status.get("capture_source"), status.get("frame_binding_event")
    result["capture_source"] = source if isinstance(source, str) and source in sources else "unknown"
    result["binding_event"] = event if isinstance(event, str) and event in events else "unknown"
    return result


def sign_spend_grant(secret_hex, action, frame_sha256, nonce, expires_at, capture_seq, frame_binding_seq):
    """HMAC-SHA256 over the same canonical message as C# ``PipelineInputPolicy.GrantMessage``."""
    _require(_hex(secret_hex, 64) and isinstance(action, str) and _hex(frame_sha256, 64) and _hex(nonce, 32)
             and all(type(value) is int and 0 < value < 2**63 for value in (expires_at, capture_seq, frame_binding_seq)),
             "消费授权签名参数无效")
    message = "\n".join((PIPELINE_GRANT_DOMAIN, action, frame_sha256, nonce, str(expires_at), str(capture_seq), str(frame_binding_seq)))
    return hmac.new(bytes.fromhex(secret_hex), message.encode("utf-8"), hashlib.sha256).hexdigest()


def load_policy(path=POLICY_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclasses.dataclass(frozen=True)
class RunConfig:
    """Explicit per-run configuration. The default instance is read-only (screencap only)."""
    input_enabled: bool = False
    families: frozenset = frozenset()
    repeatable: bool = False

    def __post_init__(self):
        _require(type(self.input_enabled) is bool and type(self.repeatable) is bool, "运行配置开关必须为布尔值")
        _require(isinstance(self.families, frozenset) and self.families <= FAMILIES, "运行配置动作族无效")
        _require(self.input_enabled or (not self.families and not self.repeatable), "输入关闭时不得声明动作族")
        _require(not (self.families & PIPELINE_FAMILIES and self.families & LEGACY_FAMILIES), "Pipeline 动作族不能与旧动作族混用")
        _require(not (self.pipeline and self.repeatable), "Pipeline 模式不使用可重复账本")

    @property
    def pipeline(self):
        return bool(self.families & PIPELINE_FAMILIES)

    @classmethod
    def from_dict(cls, data):
        _require(type(data) is dict and set(data) <= {"input_enabled", "families", "repeatable"}, "运行配置字段无效")
        families = data.get("families", [])
        _require(type(families) is list and all(isinstance(f, str) for f in families)
                 and len(set(families)) == len(families), "运行配置动作族必须为不重复列表")
        return cls(data.get("input_enabled", False), frozenset(families), data.get("repeatable", False))


@dataclasses.dataclass(frozen=True)
class SpendGrant:
    token: str
    action: str
    frame_sha256: str
    issued_at: float
    approver: str
    capture_seq: int | None = None
    frame_binding_seq: int | None = None


class SpendGate:
    """Default-deny, one-time spend approvals issued by the Agent's custom recognition.

    ``allowed`` names the spending actions this run may approve at all; ``max_per_action`` caps the
    grants per action over the gate's lifetime (default 1, at most 3: one per daily shop rank).
    Grade batches may supply an explicit per-action limit for their selected number of lives. A grant is
    bound to one frame SHA (and original host sequences in Pipeline mode), expires after ``ttl_s`` and is consumed by the first click that resolves to its
    point (legacy) or action (pipeline), whatever the outcome.
    """

    def __init__(self, allowed=(), max_per_action=1, ttl_s=MAX_FRAME_AGE_S, clock=time.monotonic, wall_clock=time.time,
                 *, action_limits=None):
        allowed = frozenset(allowed)
        _require(allowed <= SPENDING_ACTIONS, "SpendGate 只能放行已知消费动作")
        _require(type(max_per_action) is int and 0 <= max_per_action <= MAX_GRANTS_PER_ACTION, "每动作授权上限只能为0–3")
        limits = {} if action_limits is None else dict(action_limits)
        batch_caps = {"grade-tension-confirm": 40, "grade-rehearsal-easy": 20,
                      "grade-rehearsal-normal": 20, "grade-rehearsal-hard": 20, "match-play-live-start": 40,
                      "collabo-tension-decide": 6}   # 2026-11-18: one Pass per live, Pass cap 6
        _require(all(action in allowed and action in batch_caps and type(cap) is int
                     and 0 <= cap <= batch_caps[action] for action, cap in limits.items()), "Grade逐动作授权数量无效")
        _require(0 < ttl_s <= MAX_FRAME_AGE_S, "授权有效期须在5秒内")
        self.allowed, self.max_per_action, self.ttl_s, self.clock = allowed, max_per_action, ttl_s, clock
        self.wall_clock = wall_clock
        self.action_limits = limits
        self._pending = None
        self._issued = {}

    def authorize(self, action, frame_sha256, approver, *, capture_seq=None, frame_binding_seq=None):
        _require(action in self.allowed, "SpendGate 未放行该消费动作")
        _require(repeatable_protocol.digest(frame_sha256), "授权必须绑定帧SHA")
        _require(isinstance(approver, str) and approver.startswith("SC."), "授权来源必须是 SC. 自定义识别")
        sequences = capture_seq, frame_binding_seq
        _require((action not in PIPELINE_SPENDING_ACTIONS and sequences == (None, None))
                 or all(type(value) is int and 0 < value < 2**63 for value in sequences),
                 "Pipeline 消费授权必须绑定原始帧序号")
        _require(self._pending is None, "已有未使用的消费授权")
        _require(self._issued.get(action, 0) < self.action_limits.get(action, self.max_per_action),
                 "该消费动作本次运行授权已用尽")
        self._issued[action] = self._issued.get(action, 0) + 1
        self._pending = SpendGrant(secrets.token_hex(16), action, frame_sha256, self.clock(), approver, *sequences)
        return self._pending.token

    def revoke(self):
        self._pending = None

    def take(self, point):
        """Consume the pending grant if its action sits at ``point``; otherwise leave it untouched."""
        grant = self._pending
        if grant is None or grant.action in PIPELINE_SPENDING_ACTIONS or action_point(grant.action) != point:
            return None
        self._pending = None
        return grant if 0 <= self.clock() - grant.issued_at <= self.ttl_s else dataclasses.replace(grant, token="")

    def take_action(self, action):
        """Pipeline mode: consume the pending grant if it is for ``action``; otherwise leave it untouched."""
        grant = self._pending
        if grant is None or grant.action != action:
            return None
        self._pending = None
        return grant if 0 <= self.clock() - grant.issued_at <= self.ttl_s else dataclasses.replace(grant, token="")

    @property
    def pending_action(self):
        return self._pending.action if self._pending is not None else None

    def pipeline_grant(self, grant, session, request):
        """Sign a taken grant for one ``pipeline-click`` request; it expires with the grant's remaining TTL."""
        _require(bool(grant.token) and grant.action == request["action"] and grant.frame_sha256 == request["frame_sha256"],
                 "消费授权与请求不匹配")
        _require((grant.capture_seq, grant.frame_binding_seq) == (request["capture_seq"], request["frame_binding_seq"]),
                 "消费授权原始帧序号与请求不匹配")
        remaining_ms = int((self.ttl_s - (self.clock() - grant.issued_at)) * 1000)
        _require(0 < remaining_ms <= MAX_FRAME_AGE_S * 1000, "消费授权已过期")
        expires_at = int(self.wall_clock() * 1000) + remaining_ms
        return {"action": grant.action, "frame_sha256": grant.frame_sha256, "nonce": request["nonce"],
                "capture_seq": request["capture_seq"], "frame_binding_seq": request["frame_binding_seq"],
                "expires_at": expires_at,
                "hmac": sign_spend_grant(session["spend_secret"], grant.action, grant.frame_sha256, request["nonce"],
                                         expires_at, request["capture_seq"], request["frame_binding_seq"])}


class AuditLog:
    """Append-only JSONL under dev_assets/private/run_logs/<JST game day>.jsonl. Log, not lock."""

    def __init__(self, root=AUDIT_ROOT, now=lambda: _dt.datetime.now(_dt.timezone.utc)):
        root = Path(root).resolve()
        _require(root.is_relative_to(PRIVATE.resolve()), "审计日志必须位于 dev_assets/private")
        self.root, self.now = root, now

    def append(self, record):
        now = self.now()
        path = self.root / (game_day(now) + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"time": now.isoformat(), **record}, ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        return path


@dataclasses.dataclass(frozen=True)
class Endpoint:
    name: str
    session: dict


def send_line(session, request):
    """One request/one line on the host's authenticated local endpoint; no retry."""
    _require(session.get("host") == "127.0.0.1", "只允许本地端点")
    with socket.create_connection((session["host"], session["port"]), timeout=5) as connection:
        connection.settimeout(10)
        connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with connection.makefile("rb") as stream:
            line = stream.readline(4097)
    _require(len(line) <= 4096 and line.endswith(b"\n"), "输入响应过长或中断，不重试")
    return json.loads(line)


def load_endpoints(session_path, capture_pid, config):
    """Read and validate the host's endpoint session files (read-only, no network).

    A run config with pipeline families uses only ``<session>.pipeline-input.json`` (absent -> no endpoint, every
    click is refused); legacy families read only the legacy daily/grade/repeatable session files.
    """
    names = set()
    if config.input_enabled and config.pipeline:
        path = Path(str(session_path) + PIPELINE_SUFFIX)
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {ENDPOINT_PIPELINE: Endpoint(ENDPOINT_PIPELINE, validate_pipeline_session(data, capture_pid))}
    if config.input_enabled:
        if config.families & {"daily", "training", "shop", SCROLL_FAMILY}:
            names.add(ENDPOINT_DAILY)
        if "grade" in config.families:
            names.add(ENDPOINT_GRADE)
        if config.repeatable:
            names.add(ENDPOINT_REPEATABLE)
    validators = {ENDPOINT_DAILY: validate_daily_session, ENDPOINT_GRADE: grade_protocol.validate_session,
                  ENDPOINT_REPEATABLE: repeatable_protocol.validate_session}
    endpoints = {}
    for name in sorted(names):
        path = Path(str(session_path) + SESSION_SUFFIXES[name])
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            endpoints[name] = Endpoint(name, validators[name](data, capture_pid))
    return endpoints


def _default_decode(blob):
    from tools.replay_screenshots import read_png

    class _Blob:
        def read_bytes(self):
            return blob

        def open(self, mode="rb"):
            return io.BytesIO(blob)

    return read_png(_Blob())


def _empty():
    import numpy as np
    return np.empty((0, 0, 3), dtype=np.uint8)


@dataclasses.dataclass(frozen=True)
class ClickLimits:
    """Controller-side limits on clicks that reach the host (intent written): optional per-run / per-task click caps
    (None = no cap) and the session length."""
    run_max: int = DEFAULT_RUN_MAX_CLICKS
    task_max: int = DEFAULT_TASK_MAX_CLICKS
    session_max_s: float = DEFAULT_SESSION_MAX_S

    def __post_init__(self):
        # 2026-10-27: a whole W.I.N.G. run fits one host window (PipelineLimits RunMax 1500, SessionMaxMs 12 h since
        # 2026-10-28).
        _require(self.run_max is None or type(self.run_max) is int and self.run_max >= 0, "每次运行点击上限须为非负整数或不限")
        _require(self.task_max is None or type(self.task_max) is int and self.task_max >= 0, "每任务点击上限须为非负整数或不限")
        _require(type(self.session_max_s) in (int, float) and 0 < self.session_max_s <= 43200, "会话时长上限须在 (0, 43200] 秒")


class WebViewInputGate:
    """Pure controller logic; ``create_webview_controller`` wraps it in a Maa CustomController."""

    def __init__(self, bridge, config=RunConfig(), endpoints=None, spend_gate=None, audit=None, send=send_line,
                 policy=None, clock=time.monotonic, decode=_default_decode, action_table=None, limits=None,
                 frame_guard_enabled=False, run_control=None):
        _require(isinstance(config, RunConfig), "运行配置类型无效")
        policy = load_policy() if policy is None else policy
        if config.input_enabled:
            _require(type(policy) is dict and policy.get("live_input_enabled") is True,
                     "project_policy.json 未允许实时输入；控制器保持只读")
        self.bridge, self.config, self.send, self.clock, self.decode = bridge, config, send, clock, decode
        self.endpoints = dict(endpoints or {})
        self.spend_gate = SpendGate() if spend_gate is None else spend_gate
        self.audit = AuditLog() if audit is None else audit
        self.capture_error = None
        self._frame = None  # immutable FrameCapture, including this capture's own sequence
        self._image_digest = None  # sha256 of the decoded BGR pixels of that frame (what recognitions analyse)
        self.appeal_timing_required = False
        self._appeal_timing = None
        self._hold = None
        self.capture_seq = 0
        self.diagnostic_id = uuid.uuid4().hex
        self._input_lock = threading.RLock()
        self.controller_worker_thread = None
        self.frame_binding = None
        self._scope = None
        self.action_allowlist = None    # frozenset of action ids, or None (families only); see restrict_actions
        self.node_prefixes = None       # tuple of Pipeline node-name prefixes the permit's node must start with
        self.table = pipeline_actions.default_table() if action_table is None else action_table
        _require(type(frame_guard_enabled) is bool, "开发帧许可开关须为布尔值")
        self.frame_guard_enabled = frame_guard_enabled
        self.frame_permits = FramePermitStore(enabled=frame_guard_enabled, table=self.table,
                                             current_frame=lambda: self._frame, clock=clock)
        self.limits = ClickLimits() if limits is None else limits
        _require(isinstance(self.limits, ClickLimits), "点击上限类型无效")
        self.started = clock()
        self.run_clicks = 0
        self.history = []  # one entry per click/scroll attempt (for the launcher summary); not authorization
        self.task_name, self.task_cap, self.task_clicks = None, None, 0
        self.run_control = run_control

    def run_stopped(self):
        return self.run_control is not None and self.run_control.poll()

    def stop_input(self):
        """Nonblocking cleanup; a latched RunControl also forbids callbacks still holding this lock."""
        if not self._input_lock.acquire(blocking=False):
            return False
        try:
            self.frame_permits.invalidate("run_stopped")
            self.spend_gate.revoke()  # pending only; issued budgets and history must remain
            self._frame = self._image_digest = None
            self._appeal_timing = None
            return True
        finally:
            self._input_lock.release()

    def input_idle(self):
        if not self._input_lock.acquire(blocking=False):
            return False
        self._input_lock.release()
        return True

    # ---- caps ------------------------------------------------------------------------------------------
    def begin_task(self, name, cap=None):
        """Start counting clicks for one launcher task; ``cap`` defaults to ``ClickLimits.task_max`` (None = no cap)."""
        cap = self.limits.task_max if cap is None else cap
        _require(cap is None or type(cap) is int and cap >= 0 and (self.limits.task_max is None or cap <= self.limits.task_max),
                 "任务点击上限只能收紧")
        self.task_name, self.task_cap, self.task_clicks = name, cap, 0
        self._scope = None
        self.appeal_timing_required = False
        self._appeal_timing = None
        self.frame_permits.reset_task()
        if self.frame_binding is not None:
            self.frame_binding.reset()

    def restrict_actions(self, actions, node_prefixes):
        """Per-run allowlist by action id AND by recognition node (e.g. the supervised CardScan entry): a pipeline click
        is refused unless its resolved action id is listed and its frame permit's node starts with one of
        ``node_prefixes``. Only before any input, only tightens, never cleared for this run.

        A consuming action may be listed only when this run's SpendGate already allows it (W.I.N.G. ``--allow-produce-
        spend``, 2026-10-09); listing never authorizes it: each click still needs its own frame-bound grant."""
        actions, node_prefixes = frozenset(actions), tuple(node_prefixes)
        _require(self.config.pipeline and self.run_clicks == 0 and self.action_allowlist is None,
                 "动作白名单只能在 Pipeline 输入前设置一次")
        _require(actions and all(isinstance(a, str) for a in actions), "动作白名单不能为空")
        _require(all(self.table.get(a).family in self.config.families
                     and (not self.table.get(a).consuming or a in self.spend_gate.allowed)
                     for a in actions), "动作白名单只能包含本轮动作族内的非消费动作（或本轮已放行的消费动作）")
        _require(node_prefixes and all(isinstance(p, str) and p.startswith("SC.") and p.endswith(".")
                                       for p in node_prefixes), "节点前缀无效")
        self.action_allowlist, self.node_prefixes = actions, node_prefixes

    def configure_frame_guard(self, enabled):
        """Development integration only; this switch never enables input or spending."""
        _require(type(enabled) is bool and self.run_clicks == 0, "帧许可只能在输入前配置")
        self.frame_guard_enabled = self.frame_permits.enabled = enabled
        self.frame_permits.reset_task()

    def _cap_refusal(self):
        if self.clock() - self.started > self.limits.session_max_s:
            return "session_time_cap_reached"
        if self.limits.run_max is not None and self.run_clicks >= self.limits.run_max:
            return "run_click_cap_reached"
        if self.task_cap is not None and self.task_clicks >= self.task_cap:
            return "task_click_cap_reached"
        return None

    # ---- scope -----------------------------------------------------------------------------------------
    def enabled_actions(self):
        if self.config.pipeline:
            return [a.id for a in self.table.actions if a.enabled and a.family in self.config.families and not a.consuming
                    and (self.action_allowlist is None or a.id in self.action_allowlist)]
        actions = []
        for family in sorted(self.config.families):
            points = DAILY_SCROLL_ACTION_POINTS if family == SCROLL_FAMILY else CLICK_FAMILIES[family]
            actions += [a for a in points if a not in SPENDING_ACTIONS]
        return actions

    def set_scope(self, actions=None):
        """Narrow the non-spending candidates (e.g. the Agent's current page). ``None`` = all enabled."""
        if actions is None:
            self._scope = None
            return
        actions = frozenset(actions)
        _require(actions <= set(self.enabled_actions()), "作用域只能包含已启用的非消费动作")
        self._scope = actions

    def _candidates(self, point, family_filter):
        scope = self._scope if self._scope is not None else set(self.enabled_actions())
        return sorted(a for a in scope if family_filter(a) and (point is None or action_point(a) == point))

    @property
    def latest_frame_sha256(self):
        return self._frame.sha256 if self._frame else None

    # ---- screencap -------------------------------------------------------------------------------------
    def screencap(self, source="direct_call"):
        with self._input_lock:
            return self._screencap_locked(source)

    def _screencap_locked(self, source):
        self.capture_seq += 1
        self.capture_fail_streak = getattr(self, "capture_fail_streak", 0)
        record = {"kind": "capture", "capture_seq": self.capture_seq,
                  "capture_source": source if source in ("direct_call", "maa_custom_controller") else "unknown",
                  "process_id": os.getpid(), "controller_id": self.diagnostic_id,
                  "thread_id": threading.get_ident(), "task": self.task_name}
        self._frame = None
        self._image_digest = None
        self._appeal_timing = None
        self.frame_permits.invalidate("capture")
        cookie = self.frame_binding.capture_started(source) if self.frame_binding is not None else None
        try:
            status = validate_status(self.bridge.status())
            record["host_before_capture"] = frame_diagnostics(status)
            _require(status["ready"] is True, "浏览器截图尚未就绪")
            _require(status.get("minimized") is not True and status.get("window_visible") is not False
                     and status.get("webview_visible") is not False, "宿主窗口最小化或不可见，拒绝截图")
            blob = validate_png(self.bridge.screencap())
            image = self.decode(blob)
            _require(tuple(image.shape) == (CANVAS[1], CANVAS[0], 3), "截图必须为1280x720 BGR")
            sha = hashlib.sha256(blob).hexdigest()
            self._image_digest = hashlib.sha256(image.tobytes()).hexdigest() if hasattr(image, "tobytes") else None
            after = None
            if self.frame_guard_enabled:
                after = frame_diagnostics(validate_status(self.bridge.status()))
                before = record["host_before_capture"]
                _require(type(before["capture_seq"]) is int and type(before["frame_binding_seq"]) is int
                         and after["capture_seq"] == before["capture_seq"] + 1
                         and after["frame_binding_seq"] == before["frame_binding_seq"] + 2
                         and after["capture_pending"] is False and after["frame_sha256"] == sha,
                         "严格帧许可缺少独立宿主序号或捕获期间绑定已变化")
                record["host_after_capture"] = after
            self._frame = FrameCapture(sha, self.clock(), tuple(image.shape), self._image_digest, self.capture_seq,
                                       after["capture_seq"] if after else None,
                                       after["frame_binding_seq"] if after else None,
                                       after["capture_started_ms"] if after else None,
                                       after["capture_completed_ms"] if after else None)
            if self.frame_binding is not None:
                self.frame_binding.capture_finished(cookie, self._frame)
            self.capture_error = None
            self.capture_fail_streak = 0
            self._log({**record, "result": "captured", "frame_sha256": self._frame.sha256})
            return image
        except Exception as exc:  # Maa treats an empty image as a failed capture.
            self.capture_error = str(exc)
            self.capture_fail_streak += 1   # 2026-10-10: a host capture outage ended Match/Training without a report
            self._frame = None
            self._image_digest = None
            if self.frame_binding is not None:
                self.frame_binding.capture_finished(cookie, None)
            self._log({**record, "result": "capture_failed"})
            return _empty()

    # ---- input -----------------------------------------------------------------------------------------
    def _log(self, record):
        try:
            self.audit.append(record)
            return self.run_control.input_event(record) if self.run_control is not None else True
        except Exception:
            return False

    def deny(self, kind, *args, reason="input_kind_not_supported"):
        self._log({"kind": kind, "args": [a if isinstance(a, (int, float)) else str(a) for a in args],
                   "result": "denied", "reason": reason, "frame_sha256": self.latest_frame_sha256})
        return False

    def click(self, x, y):
        return self._attempt("click", (x, y), self._resolve_click)

    def swipe(self, x1, y1, x2, y2, duration):
        """Maa ``Swipe``: only a pipeline action with ``drag_rect`` whose rect holds the start and drag_rect the end."""
        def resolve(point, permit=None):
            if not self.config.pipeline:
                return None, None, "swipe_not_in_vocabulary"
            if not (DRAG_MIN_MS <= duration <= DRAG_MAX_MS):
                return None, None, "swipe_duration_out_of_bounds"
            action, grant, refusal = self._resolve_pipeline_click((x1, y1), permit)
            if refusal:
                return action, grant, refusal
            entry = self.table.get(action)
            if entry.drag_rect is None:
                return action, None, "swipe_action_not_draggable"
            if not entry.drag_contains((x2, y2)):
                return action, None, "swipe_end_out_of_bounds"
            return action, None, None
        return self._attempt("swipe", (x1, y1, x2, y2, duration), resolve)

    def scroll(self, dx, dy):
        def resolve(point, permit=None):
            if self.config.pipeline:
                return None, None, "scroll_not_in_pipeline_vocabulary"
            if dx != 0 or dy not in (-240, 240):
                return None, None, "scroll_delta_not_fixed"
            hits = self._candidates(None, lambda a: action_family(a) == SCROLL_FAMILY and DAILY_SCROLL_DELTAS[a] == dy)
            return (hits[0], None, None) if len(hits) == 1 else (None, None, "scroll_ambiguous_or_unknown")
        return self._attempt("scroll", (dx, dy), resolve)

    def _resolve_pipeline_click(self, point, permit):
        """Resolve the exact completed recognition before taking any resource grant."""
        refusal = self.frame_permits.refusal(permit, getattr(permit, "action", None))
        if refusal:
            return None, None, refusal
        hits = self.table.at(point, self.config.families, node=permit.node_name)
        if self._scope is not None:
            hits = [a for a in hits if a.id in self._scope]
        if not hits:
            return None, None, "unknown_point"
        if len(hits) > 1:  # impossible with unique node bindings; refuse anyway
            return None, None, "ambiguous_point:" + ",".join(a.id for a in hits)
        if self.action_allowlist is not None:
            if hits[0].id not in self.action_allowlist:
                return hits[0].id, None, "action_not_in_task_allowlist"
            node = getattr(permit, "node_name", None)
            if not (isinstance(node, str) and node.startswith(self.node_prefixes)):
                return hits[0].id, None, "node_not_in_task_allowlist"
        if hits[0].consuming:
            grant = self.spend_gate.take_action(hits[0].id)
            if grant is None:
                return hits[0].id, None, "spend_not_authorized"
            if not grant.token:
                return hits[0].id, grant, "spend_grant_expired"
            return hits[0].id, grant, None
        return hits[0].id, None, None

    def _resolve_click(self, point, permit=None):
        if self.config.pipeline:
            return self._resolve_pipeline_click(point, permit)
        if any(action_point(a) == point for a in LEGACY_SPENDING_ACTIONS):
            grant = self.spend_gate.take(point)
            if grant is None:
                return None, None, "spend_not_authorized"
            if not grant.token:
                return None, grant, "spend_grant_expired"
            if action_family(grant.action) not in self.config.families:
                return None, grant, "spend_family_not_enabled"
            return grant.action, grant, None
        hits = self._candidates(point, lambda a: action_family(a) != SCROLL_FAMILY)
        if not hits:
            return None, None, "unknown_point"
        if len(hits) > 1:
            return None, None, "ambiguous_point:" + ",".join(hits)
        return hits[0], None, None

    def _attempt(self, kind, args, resolve):
        with self._input_lock:
            return self._attempt_locked(kind, args, resolve)

    def _attempt_locked(self, kind, args, resolve):
        timing, self._appeal_timing = self._appeal_timing, None
        hold, self._hold = getattr(self, "_hold", None), None
        permit = self.frame_permits.take() if self.config.pipeline else None
        attempt = uuid.uuid4().hex
        record = {"attempt": attempt, "kind": kind, "args": list(args), "frame_sha256": self.latest_frame_sha256,
                  "capture_seq": self.capture_seq, "thread_id": threading.get_ident(),
                  "process_id": os.getpid(), "controller_id": self.diagnostic_id}
        if permit is not None:
            record["recognition_guard"] = {"node": permit.node_name, "task_id": permit.task_id,
                                           "reco_id": permit.reco_id, "capture_seq": permit.frame.capture_seq}

        def finish(result, reason, **extra):
            if self.config.pipeline and result != "sent" and self.frame_binding is not None:
                response = extra.get("response")
                if result == "denied" or (result == "refused" and isinstance(response, dict)
                                          and (response.get("attempted") is False
                                               or response.get("reason") in PRE_DOWN_REFUSALS)):
                    self.frame_binding.discard_click("controller_click_not_sent")  # fresh frame may re-recognise
                else:
                    self.frame_binding.reject_click("controller_click_" + result)
            self._log({**record, **extra, "result": result, "reason": reason})
            entry = {**{k: record.get(k) for k in ("task", "kind", "args", "action", "endpoint")},
                     "result": result, "reason": reason}
            response = extra.get("response")
            if isinstance(response, dict) and "attempted" in response:
                entry["attempted"] = bool(response["attempted"])
            if record.get("spend"):
                entry["spend"] = True
            self.history.append(entry)
            return result == "sent"

        if self.run_stopped():
            self.stop_input()
            return finish("denied", "run_stopped")
        if not self.config.input_enabled:
            return finish("denied", "input_disabled")
        if not all(type(v) is int for v in args):
            return finish("denied", "coordinates_not_int")
        point = args if kind == "click" else None
        if kind == "click" and not (0 <= args[0] < CANVAS[0] and 0 <= args[1] < CANVAS[1]):
            return finish("denied", "outside_canvas")
        if kind == "swipe" and not all(0 <= args[i] < CANVAS[i % 2] for i in range(4)):
            return finish("denied", "outside_canvas")
        action, grant, refusal = resolve(tuple(args) if point else None, permit)
        if grant is not None:
            record.update(spend=True, spend_token=grant.token[:8], spend_action=grant.action, approver=grant.approver)
        if refusal:
            if action is not None:
                record["action"] = action
            return finish("denied", refusal)
        record.update(action=action, spend=action in SPENDING_ACTIONS)
        appeal_timing = None
        if action in APPEAL_ACTIONS and self.appeal_timing_required:
            if (timing is None or permit is None or timing[0] != self._frame or timing[2] != action
                    or permit.frame != timing[0] or permit.node_name != APPEAL_ACTIONS[action]):
                return finish("denied", "appeal_timing_frame_missing")
            appeal_timing = timing[1]["request"]
            record["appeal_timing"] = timing[1]
        if self.task_name is not None:
            record["task"] = self.task_name
        cap = self._cap_refusal()
        if cap:
            return finish("denied", cap)
        frame = self._frame
        if frame is None or not 0 <= self.clock() - frame.captured_at <= MAX_FRAME_AGE_S:
            return finish("denied", "frame_missing_or_stale")
        if frame.shape != (CANVAS[1], CANVAS[0], 3):
            return finish("denied", "frame_size_changed")
        if grant is not None and grant.frame_sha256 != frame.sha256:
            return finish("denied", "spend_grant_frame_mismatch")
        if self.config.pipeline and (refusal := self.frame_permits.refusal(permit, action)):
            return finish("denied", refusal)
        if self.config.pipeline and self.frame_binding is not None and not self.frame_binding.allows_click(permit):
            return finish("denied", "pipeline_frame_guard_task_ended")
        endpoint_name = ENDPOINT_PIPELINE if self.config.pipeline else endpoint_for(action, self.config.repeatable, False)
        record["endpoint"] = endpoint_name
        endpoint = self.endpoints.get(endpoint_name)
        if endpoint is None:
            return finish("denied", "endpoint_unavailable")
        try:
            status = self.bridge.status()
            record["host_at_click"] = frame_diagnostics(status)
            record["frame_binding_match"] = status.get("frame_sha256") == frame.sha256
            if endpoint_name == ENDPOINT_PIPELINE:
                diagnostics = record["host_at_click"]
                _require(diagnostics["capture_seq"] == frame.host_capture_seq
                         and diagnostics["frame_binding_seq"] == frame.host_binding_seq,
                         "Pipeline 宿主帧序号已变化或未知")
                request = pipeline_click_request(endpoint.session, status, frame.sha256, action, table=self.table,
                                                 capture_seq=permit.frame.host_capture_seq,
                                                 frame_binding_seq=permit.frame.host_binding_seq,
                                                 node_name=permit.node_name,
                                                 target=(list(args[:2]) if kind == "swipe" else
                                                         point if action in {f"match-skip-plus-{i}" for i in range(1, 7)} else None),
                                                 appeal_timing=appeal_timing,
                                                 hold_ms=hold[1] if hold is not None and hold[0] == action else None,
                                                 drag_to=list(args[2:4]) if kind == "swipe" else None,
                                                 drag_ms=args[4] if kind == "swipe" else None)
                if "drag_to" in request:
                    record.update(drag_to=request["drag_to"], drag_ms=request["drag_ms"])
                if "hold_ms" in request:
                    record["hold_ms"] = request["hold_ms"]
                if grant is not None:
                    request["spend_grant"] = self.spend_gate.pipeline_grant(grant, endpoint.session, request)
                validate = validate_navigation_response
            elif endpoint_name == ENDPOINT_DAILY:
                request = daily_navigation_request(endpoint.session, status, frame.sha256, action)
                validate = validate_navigation_response
            elif endpoint_name == ENDPOINT_GRADE:
                request = grade_protocol.navigate_request(endpoint.session, status, frame.sha256, action)
                validate = validate_navigation_response
            else:
                request = repeatable_protocol.navigate_request(endpoint.session, status, frame.sha256, action)
                validate = repeatable_protocol.validate_response
        except Exception as exc:
            return finish("denied", "frame_or_host_binding_failed: " + str(exc))
        record["nonce"] = request["nonce"]
        if "spend_grant" in request:
            record["spend_expires_at"] = request["spend_grant"]["expires_at"]
        if self.run_stopped():
            self.stop_input()
            return finish("denied", "run_stopped")
        if not self._log({**record, "result": "intent", "reason": "about_to_send"}):
            if self.run_stopped():
                self.stop_input()
            if self.frame_binding is not None:
                self.frame_binding.reject_click("audit_unavailable")
            return False  # No audit, no input.
        # Status and audit callbacks can outlive the proof or receive a stop.
        # Recheck the consumed permit immediately before input becomes possible.
        if self.run_stopped():
            self.stop_input()
            return finish("denied", "run_stopped")
        if self.config.pipeline and (refusal := self.frame_permits.refusal(permit, action)):
            return finish("denied", refusal)
        if self.config.pipeline and self.frame_binding is not None and not self.frame_binding.allows_click(permit):
            return finish("denied", "pipeline_frame_guard_task_ended")
        if self.run_stopped():
            self.stop_input()
            return finish("denied", "run_stopped")
        self.run_clicks += 1  # counted once the request may reach the host, whatever the outcome
        self.task_clicks += 1
        self._frame = None  # The host also drops its frame binding; a new screencap is required.
        self._image_digest = None
        try:
            response = validate(self.send(endpoint.session, request))
        except Exception as exc:
            return finish("unknown", "send_failed_no_retry: " + str(exc))
        if not response["ok"] and response["reason"] == HOST_EMERGENCY_STOP and self.run_control is not None:
            # 紧急停止 pressed in the host panel: the whole run ends, no task or segment continues.
            self.run_control.request_stop(HOST_EMERGENCY_STOP)
        return finish("sent" if response["ok"] else "refused", response["reason"], response=response)


class LiveSpendAuthority:
    """Spend authority of the Shop task in ``--live``: binds a grant to the frame the recognition just analysed.

    ``authorize`` refuses unless the analysed pixels are exactly the controller's latest fresh frame (same decoded
    digest), then asks the gate's SpendGate for a one-shot grant on that frame's PNG SHA. ``last_outcome`` tells a
    certain refusal (``denied``, or a host refusal before any gesture) from an attempt whose effect is unknown.
    """

    def __init__(self, gate):
        self.gate = gate

    def can_spend(self, action):
        return (not self.gate.run_stopped() and action in PIPELINE_SPENDING_ACTIONS and action in self.gate.spend_gate.allowed
                and self.gate.config.input_enabled and self.gate.config.pipeline and self.gate.frame_guard_enabled)

    def configure_appeal_timing(self, enabled):
        with self.gate._input_lock:
            _require(type(enabled) is bool, "Live 定时开关无效")
            self.gate.appeal_timing_required = enabled
            self.gate._appeal_timing = None

    def stage_hold(self, action, hold_ms):
        """Next click of ``action`` (a Match Skip ＋, wing-giveup-hold on a v2 host) is a long press of ``hold_ms``; None
        when the host supports it."""
        gate = self.gate
        with gate._input_lock:
            gate._hold = None
            endpoint = gate.endpoints.get(ENDPOINT_PIPELINE)
            allowed = HOLD_ACTIONS.get(endpoint.session.get("hold_protocol")) if endpoint is not None else None
            if allowed is None or not gate.config.pipeline:
                return "hold_host_unavailable"
            if action not in allowed or not HOLD_MIN_MS <= hold_ms <= HOLD_MAX_MS:
                return "hold_not_allowed"
            gate._hold = (action, int(hold_ms))
            return None

    def appeal_timing_supported(self, action=APPEAL_ACTION):
        """True when this run's host accepts a timed ``action`` (a v1 host takes only the W.I.N.G. bar)."""
        gate = self.gate
        endpoint = gate.endpoints.get(ENDPOINT_PIPELINE)
        return (gate.config.pipeline and gate.frame_guard_enabled and endpoint is not None
                and action in APPEAL_TIMING_TARGETS.get(endpoint.session.get("appeal_timing_protocol"), {}))

    def capture_interval(self, image):
        """Host capture (started_ms, completed_ms) of the latest frame when ``image`` is exactly its pixels, else None."""
        gate = self.gate
        with gate._input_lock:
            frame = gate._frame
            if (frame is None or frame.host_capture_started_ms is None or frame.host_capture_completed_ms is None
                    or hashlib.sha256(image.tobytes()).hexdigest() != frame.pixel_digest):
                return None
            return frame.host_capture_started_ms, frame.host_capture_completed_ms

    def stage_appeal_timing(self, image, bar, *, period_s, event_offset_ms, action=APPEAL_ACTION):
        """Bind one plan to the recognition's original pixels and host capture interval."""
        from agent.core.wing_live_timing import host_timing_plan
        gate = self.gate
        with gate._input_lock:
            gate._appeal_timing = None
            if not gate.appeal_timing_required or not self.appeal_timing_supported(action):
                return "appeal_timing_host_unavailable", None
            frame = gate._frame
            if (gate.run_stopped() or frame is None
                    or not 0 <= gate.clock() - frame.captured_at <= MAX_FRAME_AGE_S):
                return "frame_missing_or_stale", None
            if hashlib.sha256(image.tobytes()).hexdigest() != frame.pixel_digest:
                return "analysed_image_is_not_the_latest_frame", None
            plan = host_timing_plan(bar, capture_started_ms=frame.host_capture_started_ms,
                                    capture_completed_ms=frame.host_capture_completed_ms,
                                    period_s=period_s, event_offset_ms=event_offset_ms)
            if plan is None:
                return "appeal_timing_unread", None
            gate._appeal_timing = (frame, plan, action)
            return None, plan

    def authorize(self, action, image, approver):
        gate = self.gate
        if not self.can_spend(action):
            return "spend_action_not_allowed"
        frame = gate._frame
        if frame is None or gate._image_digest is None or not 0 <= gate.clock() - frame.captured_at <= MAX_FRAME_AGE_S:
            return "frame_missing_or_stale"
        if hashlib.sha256(image.tobytes()).hexdigest() != gate._image_digest:
            return "analysed_image_is_not_the_latest_frame"
        try:
            gate.spend_gate.revoke()
            gate.spend_gate.authorize(action, frame.sha256, approver,
                                      capture_seq=frame.host_capture_seq, frame_binding_seq=frame.host_binding_seq)
        except ValueError as exc:
            return str(exc)
        if gate.run_stopped():
            gate.stop_input()
            return "run_stopped"
        return None

    def last_outcome(self, action):
        """``denied`` (certainly not sent), ``sent``, ``refused`` (host refused after a possible attempt) or ``unknown``."""
        for entry in reversed(self.gate.history):
            if entry.get("action") == action or entry.get("spend"):
                if _not_sent(entry):
                    return "denied"
                return entry["result"]
        return "denied"  # no click for this action ever reached the controller: nothing was sent

    def click_mark(self):
        """Opaque mark of the controller's click history (taken by a task right before a tap)."""
        return len(self.gate.history)

    def clicks_since(self, mark):
        """``not_sent`` when every click after ``mark`` was denied by the controller or refused by the host before any
        gesture (or no click call happened at all), else ``sent`` / ``unknown`` (B6e: a refused tap is not uncertain)."""
        clicks = [e for e in self.gate.history[mark:] if e.get("kind") == "click"]
        if all(_not_sent(e) for e in clicks):
            return "not_sent"
        return "sent" if any(e["result"] == "sent" for e in clicks) else "unknown"


# Host refusals that come from its guard before the first Down (PipelineInput.BeforeInput / PipelineGesture): only
# hover moves were sent, never a tap. The host still reports ``attempted`` once a hover went out (2026-10-15 live:
# Review ← refused with frame_changed_before_input_no_retry was read as an uncertain click and stopped the run).
# Host reply after 紧急停止 in the panel (browser_host PipelineClick): ends the whole run.
HOST_EMERGENCY_STOP = "emergency_stop"
PRE_DOWN_REFUSALS = frozenset({"frame_changed_before_input_no_retry", "cancelled_before_input",
                               "spend_grant_expired_before_input", "appeal_timing_no_future_target",
                               "appeal_timing_deadline_missed_no_input"})


def _not_sent(entry) -> bool:
    return entry["result"] == "denied" or (entry["result"] == "refused" and (
        entry.get("attempted") is False or entry.get("reason") in PRE_DOWN_REFUSALS))


def create_webview_controller(gate):
    """Wrap ``WebViewInputGate`` in a Maa CustomController (lazy import keeps unit tests pure)."""
    from maa.controller import CustomController

    class WebViewController(CustomController):
        def __init__(self):
            self.gate = gate
            super().__init__()

        def connect(self):
            try:
                gate.controller_worker_thread = threading.get_ident()
                return validate_status(gate.bridge.status())["ready"] is True
            except Exception as exc:
                gate.capture_error = str(exc)
                return False

        def request_uuid(self):
            return UUID

        def get_features(self):
            return 0  # Maa must call click(), never touch down/up pairs.

        def screencap(self):
            return gate.screencap(source="maa_custom_controller")

        def click(self, x, y):
            return gate.click(x, y)

        def scroll(self, dx, dy):
            return gate.scroll(dx, dy)

        def start_app(self, intent): return gate.deny("start_app", intent)
        def stop_app(self, intent): return gate.deny("stop_app", intent)
        def swipe(self, x1, y1, x2, y2, duration):
            return gate.swipe(x1, y1, x2, y2, duration)
        def touch_down(self, contact, x, y, pressure): return gate.deny("touch_down", contact, x, y, pressure)
        def touch_move(self, contact, x, y, pressure): return gate.deny("touch_move", contact, x, y, pressure)
        def touch_up(self, contact): return gate.deny("touch_up", contact)
        def click_key(self, keycode): return gate.deny("click_key", keycode)
        def input_text(self, text): return gate.deny("input_text", "<redacted>")
        def key_down(self, keycode): return gate.deny("key_down", keycode)
        def key_up(self, keycode): return gate.deny("key_up", keycode)
        def relative_move(self, dx, dy): return gate.deny("relative_move", dx, dy)
        def shell(self, cmd, timeout): return None

    return WebViewController()
