"""One-shot Pipeline recognition-frame permits; no Maa, input, or transport dependency."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import secrets
from threading import RLock


MAX_AGE_S = 5.0
CANONICAL_SHAPE = (720, 1280, 3)


@dataclass(frozen=True)
class FrameCapture:
    sha256: str
    captured_at: float
    shape: tuple
    pixel_digest: str | None
    capture_seq: int
    host_capture_seq: int | None = None
    host_binding_seq: int | None = None
    host_capture_started_ms: float | None = None
    host_capture_completed_ms: float | None = None


@dataclass(frozen=True)
class FramePermit:
    token: str
    node_name: str
    action: str
    task_id: int
    reco_id: int
    frame: FrameCapture
    issued_at: float
    epoch: int


def _positive_int(value):
    return type(value) is int and value > 0


def _finite_time(value):
    return type(value) in (int, float) and math.isfinite(value)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class FramePermitStore:
    def __init__(self, *, enabled=False, table, current_frame, clock):
        self.enabled = enabled
        self.table = table
        self.current_frame = current_frame
        self.clock = clock
        self.epoch = 0
        self._candidate = None
        self._armed = None
        self._lock = RLock()

    def reset_task(self):
        with self._lock:
            self.invalidate()
            self.epoch += 1

    def invalidate(self, reason=None):
        del reason
        with self._lock:
            self._candidate = self._armed = None

    def _frame_matches(self, frame, now):
        if not isinstance(frame, FrameCapture):
            return False
        if (type(frame.shape) is not tuple or frame.shape != CANONICAL_SHAPE
                or any(type(value) is not int for value in frame.shape)
                or not _digest(frame.sha256) or not _digest(frame.pixel_digest)
                or not all(_positive_int(value) for value in
                           (frame.capture_seq, frame.host_capture_seq, frame.host_binding_seq))
                or not _finite_time(now) or not _finite_time(frame.captured_at)
                or not 0 <= now - frame.captured_at <= MAX_AGE_S):
            return False
        try:
            current = self.current_frame()
        except Exception:
            return False
        return (isinstance(current, FrameCapture) and current == frame
                and all(_positive_int(value) for value in
                        (current.capture_seq, current.host_capture_seq, current.host_binding_seq))
                and _finite_time(current.captured_at))

    def issue(self, node_name, action, task_id, reco_id, image, frame):
        with self._lock:
            self.invalidate()
            if self.enabled is not True or not _positive_int(task_id) or not _positive_int(reco_id):
                return None
            try:
                if (not isinstance(node_name, str) or not isinstance(action, str)
                        or self.table.node_actions().get(node_name) != action
                        or not isinstance(frame, FrameCapture)
                        or tuple(image.shape) != CANONICAL_SHAPE
                        or hashlib.sha256(image.tobytes()).hexdigest() != frame.pixel_digest):
                    return None
                now = self.clock()
                if not self._frame_matches(frame, now):
                    return None
            except Exception:
                return None
            token = secrets.token_hex(32)
            self._candidate = FramePermit(token, node_name, action, task_id, reco_id, frame, now, self.epoch)
            return token

    def arm(self, token, task_id, reco_id, node_name):
        with self._lock:
            permit = self._candidate
            self._candidate = self._armed = None
            if (permit is None or not isinstance(token, str) or token != permit.token
                    or not _positive_int(task_id) or task_id != permit.task_id
                    or not _positive_int(reco_id) or reco_id != permit.reco_id
                    or node_name != permit.node_name or self.refusal(permit, permit.action) is not None):
                return False
            self._candidate = self._armed = permit
            return True

    def take(self):
        with self._lock:
            permit = self._armed
            self._candidate = self._armed = None
            return permit

    def refusal(self, permit, action):
        with self._lock:
            prefix = "pipeline_frame_guard_"
            if self.enabled is not True:
                return prefix + "disabled"
            if not isinstance(permit, FramePermit):
                return prefix + "missing"
            if permit.epoch != self.epoch:
                return prefix + "epoch_mismatch"
            if action != permit.action or self.table.node_actions().get(permit.node_name) != action:
                return prefix + "action_mismatch"
            try:
                now = self.clock()
            except Exception:
                return prefix + "stale"
            if (not _finite_time(now) or not _finite_time(permit.issued_at)
                    or not 0 <= now - permit.issued_at <= MAX_AGE_S):
                return prefix + "stale"
            if not self._frame_matches(permit.frame, now):
                return prefix + "frame_mismatch"
            return None
