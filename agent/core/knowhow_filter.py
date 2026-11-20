"""絞り込み (filter) popup of the ノウハウブック一覧: geometry, readers and the read-only popup calibration session.

The popup is a long scroll list (育成プロデュースシナリオ → 特化プロデュース
→ アイドル → ノウハウ (type/tier checkboxes) → ノウハウレベル → 詳細検索) with a live 「該当するノウハウブック N」 count.
The host only accepts wheel deltas of ±60/±120/±240 and the grid wheel was measured not to snap (159-160 px per step),
so a checkbox below the first screen can only be tapped at a *reproducible* scroll state: N steps from the top, or
N steps back from the clamped bottom. ``FilterCalibration`` measures those states (read-only):

    enter       一覧 in normal mode, no popup, 絞り込み not active (``filter_active is False``) -> open_filter
    top         popup open at the top (thumb at top), count = 所持数 (no condition set) -> scroll_filter(down)
    down        each step: same popup, measured shift; until the thumb reaches the bottom or the content stops
    up          ``up_wheels`` steps back up (states "bottom - k")
    cancel      キャンセル (never リセット / 決定 / a checkbox) -> 一覧 again with 絞り込み off and the same 所持数 -> leave

Every frame records the popup tokens (text + box), section header positions, thumb and shift, so checkbox rects for each
reproducible state can be derived offline from the saved frames. Any other popup (e.g. the 絞り込みノウハウ選択
sub-list), a destructive marker, a count other than 所持数 at the top, or an unexpected screen stops without input.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

from agent.core import knowhow_books as kb
from agent.core import knowhow_scan as ks

# ---- geometry (native 1280x720, 2026-10-12 knowhow-sample-20261012 #001/#117/#122/#131) ---------------------------
FILTER_BUTTON, POPUP_CONTENT, POPUP_WHEEL = ks.FILTER_BUTTON, ks.FILTER_POPUP_CONTENT, ks.FILTER_POPUP_WHEEL
POPUP_BAR_X, POPUP_TRACK = 1119, (104, 570)   # popup scrollbar: thumb 106..169 at the top (#117/#131)
CANCEL_BUTTON, RESET_BUTTON, APPLY_BUTTON = ks.FILTER_CANCEL, ks.FILTER_RESET, ks.FILTER_APPLY
SECTION_HEADERS = ("育成プロデュースシナリオ", "特化プロデュース", "アイドル", "ノウハウ", "ノウハウレベル", "詳細検索")
SHIFT_RANGE = 320                         # |dy| of one popup wheel step is searched up to this (grid step 160 px)
SHIFT_MIN_OVERLAP = 120
SETTLE_MAD = 0.5                          # popup content of two captures this close = not moving
ZERO_SHIFT_RECHECKS = 2                   # a 0 px step is re-captured this often before "content stopped"

DEFAULTS = {"max_down_wheels": 30, "up_wheels": 3, "max_frames": 200, "max_holds": 6}
LIMITS = {"max_down_wheels": 60, "up_wheels": 10, "max_frames": 400, "max_holds": 10}
NAV_INPUTS, EXIT_INPUTS = 3, 4            # home -> アイドル -> 一覧 -> tab ; open + cancel + back x3 (bounded below)


@dataclasses.dataclass(frozen=True)
class FilterCalibrationOptions:
    max_down_wheels: int = DEFAULTS["max_down_wheels"]
    up_wheels: int = DEFAULTS["up_wheels"]
    max_frames: int = DEFAULTS["max_frames"]
    max_holds: int = DEFAULTS["max_holds"]

    @classmethod
    def from_params(cls, data: Mapping[str, Any] | None) -> "FilterCalibrationOptions":
        data = dict(data or {})
        unknown = set(data) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"筛选弹窗校准选项未知: {sorted(unknown)}")
        options = cls(**data)
        for name, high in LIMITS.items():
            value = getattr(options, name)
            if type(value) is not int or not 0 <= value <= high:
                raise ValueError(f"{name} 须为 0..{high} 的整数")
        return options

    def max_inputs(self) -> int:
        return NAV_INPUTS + 1 + self.max_down_wheels + self.up_wheels + 1 + 3


# ---- readers -----------------------------------------------------------------------------------------------------
def popup_thumb(image) -> dict | None:
    return ks.read_thumb(image, POPUP_BAR_X, POPUP_TRACK)


def popup_tokens(texts) -> list[dict]:
    """OCR tokens inside the scrolling content (for offline checkbox geometry per scroll state)."""
    return [{"text": t, "box": list(b), "score": round(s, 3)} for t, b, s in kb._tokens(texts)
            if kb._inside(b, POPUP_CONTENT)]


def popup_sections(texts) -> dict:
    """Section header -> y (top of its OCR box) for the headers visible in the content area."""
    out = {}
    for t, b, _ in kb._tokens(texts):
        if kb._inside(b, POPUP_CONTENT) and t in SECTION_HEADERS and t not in out:
            out[t] = b[1]
    return out


def popup_shift(before, after, direction: str) -> dict:
    from agent.core.knowhow_calibrate import vertical_shift   # lazy: knowhow_calibrate imports knowhow_scan

    rng = (0, SHIFT_RANGE) if direction == "down" else (-SHIFT_RANGE, 0)
    return vertical_shift(before, after, POPUP_CONTENT, min_overlap=SHIFT_MIN_OVERLAP, dy_range=rng)


def _mad(a, b) -> float:
    import numpy as np

    x, y, w, h = POPUP_CONTENT
    return float(np.abs(np.asarray(a[y:y + h, x:x + w], dtype=np.float32)
                        - np.asarray(b[y:y + h, x:x + w], dtype=np.float32)).mean())


# ---- session -----------------------------------------------------------------------------------------------------
class FilterCalibration:
    """Read-only measurement of the 絞り込み popup scroll states (see the module docstring)."""

    def __init__(self, options: FilterCalibrationOptions, read_texts: ks.TextReader):
        self.options = options
        self.read_texts = read_texts
        self.phase = "enter"
        self.final: ks.Decision | None = None
        self.pending: ks.Decision | None = None
        self.frames = self.inputs = self.holds = 0
        self.owned = self.capacity = None
        self.sort = None
        self.states: list[dict] = []          # one per settled popup frame: {frame, state, thumb, dy, sections, ...}
        self.down = self.up = 0
        self.bottom: dict | None = None
        self.prev_image = None                # settled capture the last wheel step started from
        self.last_seen = None                 # previous capture (settle check)
        self.awaiting: str | None = None      # direction of the wheel step not yet measured
        self.zero_checks = 0
        self.after_cancel: dict | None = None
        self.soft_reason: str | None = None
        self.log: list[dict] = []
        self.books: dict = {}                 # runtime.stop() probes these attributes of every session
        self.grid: dict = {}
        self.grid_steps = 0

    # -- decisions (same contract as ScanSession)
    def _decide(self, decision: ks.Decision, **note) -> ks.Decision:
        self.log.append({"frame": self.frames, "phase": self.phase, **decision.to_dict(), **note})
        if decision.kind in ("stop", "leave"):
            self.final = decision
        if decision.is_input:
            self.inputs += 1
            self.pending = decision
            self.holds = 0
        return decision

    def _stop(self, reason, *notes) -> ks.Decision:
        return self._decide(ks.Decision("stop", reason, notes=tuple(str(n) for n in notes)))

    def _hold(self, reason) -> ks.Decision:
        self.holds += 1
        if self.holds > self.options.max_holds:
            return self._stop("hold_budget_exhausted", reason)
        return self._decide(ks.Decision("hold", reason))

    def observe(self, image) -> ks.Decision:
        if self.final is not None:
            return self.final
        self.frames += 1
        if self.frames > self.options.max_frames:
            return self._stop("frame_budget_exhausted")
        pending, self.pending = self.pending, None
        texts = self.read_texts(image)
        screen = kb.classify_knowhow_screen(texts, image)
        if screen["refused"]:
            return self._stop("destructive_marker", *screen["refused"])
        if self.phase == "enter":
            return self._on_enter(screen)
        if self.phase == "left":
            return self._on_after_cancel(screen)
        return self._on_popup(screen, texts, image, pending)

    def _on_enter(self, screen) -> ks.Decision:
        problem = ks.ScanSession._list_problem(screen)
        if problem is not None:
            return self._stop(problem)
        if screen["owned"] is None or screen["filter_active"] is None:
            return self._hold("meta_unreadable")
        if screen["filter_active"]:
            return self._stop("filter_already_active")          # never touch a filter the user set
        self.owned, self.capacity, self.sort = screen["owned"], screen["capacity"], screen["sort"]
        self.phase = "top"
        return self._decide(ks.Decision("open_filter", "calibrate_filter_popup"))

    def _on_popup(self, screen, texts, image, pending) -> ks.Decision:
        if screen["popup"] != "filter":
            if screen["popup"] is None and pending is not None and pending.kind == "open_filter" and not self.states:
                return self._hold("filter_popup_not_open_yet")
            return self._stop("filter_popup_missing", f"popup={screen['popup']}",
                              f"after={pending.kind if pending else None}")
        head = kb.parse_filter_popup(texts)
        thumb = popup_thumb(image)
        if thumb is None or head["count"] is None:
            return self._hold("filter_popup_unreadable")
        shift = None
        if self.awaiting is not None:
            # 2026-10-12 grid calibration: captures mid-animation measured 2 / 216 / 317 px. Measure only a settled
            # frame (identical to the previous capture), and re-capture a 0 px step before calling it "stopped".
            seen, self.last_seen = self.last_seen, image
            if seen is None or _mad(seen, image) > SETTLE_MAD:
                return self._hold("filter_popup_settling")
            shift = popup_shift(self.prev_image, image, self.awaiting)
            if not shift["moved"] and self.zero_checks < ZERO_SHIFT_RECHECKS:
                self.zero_checks += 1
                return self._hold("filter_popup_step_not_started")
            self.awaiting, self.zero_checks = None, 0
        else:
            self.last_seen = image
        if self.phase == "top":
            if not thumb["at_top"]:
                return self._stop("filter_popup_not_at_top", thumb["top"])
            if head["count"] != self.owned:
                return self._stop("filter_condition_preset", f"count={head['count']} owned={self.owned}")
            state = "top"
        elif self.phase == "down":
            state = f"top+{self.down}"
        else:
            state = f"bottom-{self.up}"
        if head["count"] != self.owned:
            return self._stop("filter_count_changed", head["count"])   # only wheels were sent: nothing may be ticked
        self.states.append({"frame": self.frames, "state": state, "thumb": thumb, "count": head["count"],
                            "shift": shift, "sections": popup_sections(texts), "tokens": popup_tokens(texts)})
        self.prev_image = image
        if self.phase == "top":
            self.phase = "down"
        if self.phase == "down":
            stopped = shift is not None and not shift["moved"]
            if thumb["at_bottom"] or stopped:
                self.bottom = {"frame": self.frames, "down_wheels": self.down, "by": "thumb" if thumb["at_bottom"]
                               else "content_stopped"}
                self.states[-1]["state"] = "bottom"
                self.phase = "up"
            elif self.down >= self.options.max_down_wheels:
                self.soft_reason = "down_wheel_budget"
                self.phase = "up"
            else:
                self.down += 1
                self.awaiting, self.last_seen = "down", None
                return self._decide(ks.Decision("scroll_filter", "measure_down", direction="down"))
        if self.phase == "up" and self.up < self.options.up_wheels and self.bottom is not None:
            self.up += 1
            self.awaiting, self.last_seen = "up", None
            return self._decide(ks.Decision("scroll_filter", "measure_up", direction="up"))
        self.phase = "left"
        return self._decide(ks.Decision("cancel_filter", "close_without_change"))

    def _on_after_cancel(self, screen) -> ks.Decision:
        if screen["popup"] == "filter":
            return self._hold("filter_popup_still_open")
        problem = ks.ScanSession._list_problem(screen)
        if problem is not None:
            return self._stop(problem, "after=cancel_filter")
        if screen["owned"] is None or screen["filter_active"] is None:
            return self._hold("meta_unreadable")
        self.after_cancel = {"frame": self.frames, "filter_active": screen["filter_active"], "owned": screen["owned"],
                             "sort": screen["sort"]}
        if screen["filter_active"] or screen["owned"] != self.owned or screen["sort"] != self.sort:
            return self._stop("filter_cancel_changed_list", screen["filter_active"], screen["owned"], screen["sort"])
        return self._decide(ks.Decision("leave", self.soft_reason or "filter_calibrated"))

    def result(self) -> dict:
        stopped = self.final is not None and self.final.kind == "stop"
        downs = [s["shift"]["dy"] for s in self.states if s["shift"] and s["state"].startswith(("top+", "bottom"))
                 and s["shift"]["dy"] > 0]
        ups = [s["shift"]["dy"] for s in self.states if s["shift"] and s["state"].startswith("bottom-")]
        return {"version": 1, "kind": "knowhow_filter_calibration",
                "partial": stopped or self.soft_reason is not None or self.bottom is None,
                "outcome": None if self.final is None else self.final.reason, "stopped": stopped,
                "soft_reason": self.soft_reason, "frames": self.frames, "inputs": self.inputs,
                "owned": self.owned, "sort": self.sort, "bottom": self.bottom,
                "down_step_px": downs, "up_step_px": ups, "states": self.states,
                "after_cancel": self.after_cancel, "log": list(self.log)}


__all__ = ["FilterCalibrationOptions", "FilterCalibration", "popup_thumb", "popup_sections", "popup_tokens",
           "popup_shift"]
