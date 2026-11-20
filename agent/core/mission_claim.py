"""Offline single-daily-mission claim contract; never Maa, input or runtime authorization.

Evidence must eventually come from verified native readers, not MissionStatus progress, UI
preferences or a generic reward popup. No such execution adapter is connected yet. A caller
must durably save the pending history before sending input and reload it after interruption.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from threading import RLock

from agent.core.frame_permit import CANONICAL_SHAPE, MAX_AGE_S, FrameCapture


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identity(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def _positive_int(value):
    return type(value) is int and 0 < value < 2**63


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class ClaimRow:
    mission_id: str
    progress: tuple[int, int] | None
    reward_state: str  # positive claimable/claimed markers; completed progress alone is unknown

    def __post_init__(self):
        _require(_identity(self.mission_id), "领取任务需要受信的唯一身份，不能使用行号")
        _require(self.reward_state in ("claimable", "claimed", "incomplete", "unknown"), "领取状态无效")
        if self.progress is not None:
            _require(type(self.progress) is tuple and len(self.progress) == 2
                     and all(type(v) is int and v >= 0 for v in self.progress), "任务进度必须为非负整数对")


@dataclass(frozen=True)
class ClaimTarget:
    mission_id: str
    scope: str
    rect: tuple[int, int, int, int]
    enabled: bool
    no_cost_verified: bool

    def __post_init__(self):
        _require(_identity(self.mission_id) and _identity(self.scope), "领取目标身份/范围无效")
        _require(type(self.rect) is tuple and len(self.rect) == 4
                 and all(type(v) is int for v in self.rect), "领取目标需要完整整数框")
        _require(type(self.enabled) is bool and type(self.no_cost_verified) is bool, "目标证明必须为布尔值")


@dataclass(frozen=True)
class ClaimObservation:
    cycle_id: str
    frame: FrameCapture
    page: str
    guard_verified: bool
    overlays_clear: bool
    rows: tuple[ClaimRow, ...]
    targets: tuple[ClaimTarget, ...]

    def __post_init__(self):
        _require(_identity(self.cycle_id) and _identity(self.page), "每日观测需要明确周期与页面")
        _require(type(self.guard_verified) is bool and type(self.overlays_clear) is bool, "页面证明必须为布尔值")
        _require(type(self.rows) is tuple and all(isinstance(r, ClaimRow) for r in self.rows), "任务行必须为不可变记录")
        _require(type(self.targets) is tuple and all(isinstance(t, ClaimTarget) for t in self.targets), "目标必须为不可变记录")


@dataclass(frozen=True)
class ClaimReceipt:
    cycle_id: str
    mission_id: str
    frame: FrameCapture
    kind: str
    guard_verified: bool
    scope_verified: bool
    unobstructed: bool

    def __post_init__(self):
        _require(all(_identity(v) for v in (self.cycle_id, self.mission_id, self.kind)), "领取结果归属无效")
        _require(all(type(v) is bool for v in (self.guard_verified, self.scope_verified, self.unobstructed)),
                 "领取结果证明必须为布尔值")


@dataclass(frozen=True)
class ClaimRecord:
    cycle_id: str
    mission_id: str
    outcome: str

    def __post_init__(self):
        _require(_identity(self.cycle_id) and _identity(self.mission_id), "领取记录归属无效")
        _require(self.outcome in ("pending", "claimed", "uncertain", "refused"), "领取记录结果无效")

    def to_dict(self):
        return {"cycle_id": self.cycle_id, "mission_id": self.mission_id, "outcome": self.outcome}

    @classmethod
    def from_dict(cls, value):
        _require(isinstance(value, dict) and set(value) == {"cycle_id", "mission_id", "outcome"}, "领取记录字段无效")
        return cls(**value)


@dataclass(frozen=True)
class ClaimAttempt:
    index: int
    cycle_id: str
    mission_id: str
    target: ClaimTarget
    frame: FrameCapture


@dataclass(frozen=True)
class ClaimDecision:
    state: str
    reason: str
    target: ClaimTarget | None = None
    attempt: ClaimAttempt | None = None

    @property
    def input_authorized(self):
        return False


def _frame_failure(frame, current_frame, now):
    if not isinstance(frame, FrameCapture):
        return "claim_frame_missing"
    if not isinstance(current_frame, FrameCapture):
        return "claim_frame_changed"
    for candidate in (frame, current_frame):
        if (type(candidate.shape) is not tuple or candidate.shape != CANONICAL_SHAPE
                or any(type(v) is not int for v in candidate.shape)
                or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
                           for v in (candidate.sha256, candidate.pixel_digest))
                or not all(_positive_int(v) for v in
                           (candidate.capture_seq, candidate.host_capture_seq, candidate.host_binding_seq))):
            return "claim_frame_invalid"
    if not _finite(now) or not _finite(frame.captured_at) or not 0 <= now - frame.captured_at <= MAX_AGE_S:
        return "claim_frame_stale"
    if not _finite(current_frame.captured_at) or current_frame != frame:
        return "claim_frame_changed"
    return None


def _observation_failure(observation, cycle_id, frame, now):
    if not isinstance(observation, ClaimObservation):
        return "claim_observation_missing"
    if observation.cycle_id != cycle_id:
        return "claim_cycle_changed"
    if observation.page != "daily_missions" or observation.guard_verified is not True:
        return "claim_daily_page_unverified"
    if observation.overlays_clear is not True:
        return "claim_overlay_present"
    if failure := _frame_failure(observation.frame, frame, now):
        return failure
    if len({r.mission_id for r in observation.rows}) != len(observation.rows):
        return "claim_identity_ambiguous"
    return None


def _complete(row):
    return row.progress is not None and row.progress[1] > 0 and row.progress[0] == row.progress[1]


def _rect_valid(rect):
    x, y, width, height = rect
    return x >= 0 and y >= 0 and width > 0 and height > 0 and x + width <= 1280 and y + height <= 720


def _overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def inspect_claim(observation, mission_id, cycle_id, *, frame, now):
    """Read-only classification of one explicit identity; never chooses a row by screen order."""
    if not _identity(mission_id) or not _identity(cycle_id):
        return ClaimDecision("stopped", "claim_identity_missing")
    if failure := _observation_failure(observation, cycle_id, frame, now):
        return ClaimDecision("stopped", failure)
    row = next((r for r in observation.rows if r.mission_id == mission_id), None)
    if row is None:
        return ClaimDecision("stopped", "claim_mission_missing")
    targets = [t for t in observation.targets if t.mission_id == mission_id]
    if row.reward_state == "claimed" and _complete(row):
        if any(t.enabled for t in targets):
            return ClaimDecision("stopped", "claim_terminal_conflict")
        return ClaimDecision("already_claimed", "claim_positive_terminal")
    if row.reward_state == "incomplete" and row.progress and 0 <= row.progress[0] < row.progress[1]:
        return ClaimDecision("stopped", "claim_mission_incomplete")
    if row.reward_state != "claimable" or not _complete(row):
        return ClaimDecision("stopped", "claim_reward_unverified")
    if len(targets) != 1:
        return ClaimDecision("stopped", "claim_target_ambiguous" if targets else "claim_target_missing")
    target = targets[0]
    if target.scope != "single_daily":
        return ClaimDecision("stopped", "claim_batch_scope_unverified")
    if target.enabled is not True or target.no_cost_verified is not True:
        return ClaimDecision("stopped", "claim_target_unverified")
    if not _rect_valid(target.rect):
        return ClaimDecision("stopped", "claim_target_geometry_invalid")
    for other in observation.targets:
        if other is not target and other.enabled:
            if not _rect_valid(other.rect) or _overlaps(target.rect, other.rect):
                return ClaimDecision("stopped", "claim_target_ambiguous")
    return ClaimDecision("candidate", "claim_single_daily_candidate", target)


def _newer(frame, previous):
    return (frame.capture_seq > previous.capture_seq and frame.host_capture_seq > previous.host_capture_seq
            and frame.host_binding_seq > previous.host_binding_seq and frame.captured_at >= previous.captured_at)


class ClaimTracker:
    """One attempt per identity/cycle; history restores pending/refused/unknown as a stop.

    This only reserves offline intent. There is no execution adapter or persistence here. Persist
    ``history`` before any future host request; a logging failure must refuse the attempt.
    """
    def __init__(self, cycle_id, *, enabled=False, max_attempts=1, history=()):
        _require(_identity(cycle_id), "领取需要受信的每日周期")
        _require(type(enabled) is bool and _positive_int(max_attempts), "领取开关/尝试上限无效")
        _require(type(history) is tuple and all(isinstance(r, ClaimRecord) for r in history), "领取历史必须为不可变记录")
        _require(all(r.cycle_id == cycle_id for r in history), "不能静默丢弃其他周期的未决记录")
        _require(len({r.mission_id for r in history}) == len(history), "领取历史含重复任务")
        self.cycle_id, self.enabled, self.max_attempts = cycle_id, enabled, max_attempts
        self._records = list(history)
        self._pending = self._receipt = None
        self._halt_reason = "claim_previous_attempt_unresolved" if any(r.outcome != "claimed" for r in history) else None
        self._lock = RLock()

    @property
    def history(self):
        with self._lock:
            return tuple(self._records)

    @property
    def attempts(self):
        return len(self.history)

    def begin(self, observation, mission_id, *, frame, now):
        with self._lock:
            if self.enabled is not True:
                return ClaimDecision("disabled", "claim_contract_disabled")
            if self._halt_reason:
                return ClaimDecision("stopped", self._halt_reason)
            if self._pending is not None:
                return ClaimDecision("stopped", "claim_attempt_pending")
            decision = inspect_claim(observation, mission_id, self.cycle_id, frame=frame, now=now)
            if decision.state != "candidate":
                if decision.state == "stopped":
                    self._halt_reason = decision.reason
                return decision
            if any(r.mission_id == mission_id for r in self._records):
                return ClaimDecision("stopped", "claim_attempt_already_used")
            if self.attempts >= self.max_attempts:
                return ClaimDecision("stopped", "claim_attempt_budget_exhausted")
            attempt = ClaimAttempt(self.attempts + 1, self.cycle_id, mission_id, decision.target, observation.frame)
            self._records.append(ClaimRecord(self.cycle_id, mission_id, "pending"))
            self._pending = attempt
            return ClaimDecision("prepared", "claim_offline_intent_reserved", decision.target, attempt)

    def _stop(self, reason, outcome="uncertain"):
        if self._pending is not None:
            self._records[-1] = ClaimRecord(self.cycle_id, self._pending.mission_id, outcome)
        self._pending = self._receipt = None
        self._halt_reason = reason
        return ClaimDecision("stopped", reason)

    def refuse(self):
        """A proven no-send still consumes the reserved attempt and stops this contract."""
        with self._lock:
            return self._stop("claim_refused", "refused")

    def interrupt(self):
        with self._lock:
            return self._stop("claim_result_uncertain")

    def accept_receipt(self, attempt, receipt, *, frame, now):
        with self._lock:
            if self._halt_reason:
                return ClaimDecision("stopped", self._halt_reason)
            if self._pending is None or attempt is not self._pending or self._receipt is not None:
                return self._stop("claim_attempt_mismatch")
            if (not isinstance(receipt, ClaimReceipt) or receipt.cycle_id != self.cycle_id
                    or receipt.mission_id != attempt.mission_id or receipt.kind != "daily_mission_reward"
                    or not all(v is True for v in (receipt.guard_verified, receipt.scope_verified, receipt.unobstructed))):
                return self._stop("claim_receipt_unverified")
            if failure := _frame_failure(receipt.frame, frame, now):
                return self._stop(failure)
            if not _newer(receipt.frame, attempt.frame):
                return self._stop("claim_receipt_not_after_attempt")
            self._receipt = receipt
            return ClaimDecision("receipt_observed", "claim_terminal_still_required")

    def finish(self, attempt, observation, *, frame, now):
        with self._lock:
            if self._halt_reason:
                return ClaimDecision("stopped", self._halt_reason)
            if self._pending is None or attempt is not self._pending:
                return self._stop("claim_attempt_mismatch")
            if self._receipt is None:
                return self._stop("claim_receipt_missing")
            if failure := _observation_failure(observation, self.cycle_id, frame, now):
                return self._stop(failure)
            if not _newer(observation.frame, self._receipt.frame):
                return self._stop("claim_terminal_not_after_receipt")
            terminal = inspect_claim(observation, attempt.mission_id, self.cycle_id, frame=frame, now=now)
            if terminal.state != "already_claimed":
                return self._stop("claim_terminal_unverified")
            self._records[-1] = ClaimRecord(self.cycle_id, attempt.mission_id, "claimed")
            self._pending = self._receipt = None
            return ClaimDecision("claimed", "claim_receipt_and_terminal_verified")
