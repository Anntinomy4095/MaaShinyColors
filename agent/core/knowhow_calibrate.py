"""K3 calibration run of the ノウハウブック一覧 (pure decision core; no Maa import, no input).

The wheel step of the grid and of the detail list was never recorded (2026-10-08 only
has scrollbar drags). ``knowhow_scan`` needs whole-row grid steps and overlapping detail steps, so the first supervised
run only measures. It uses the same ``SC.KnowhowScan.*`` Pipeline nodes and decision kinds as the scan
(``scroll_grid`` / ``open_tile`` / ``scroll_detail`` / ``leave`` / ``hold`` / ``stop``) and never changes the sort:
the measurements are pixel shifts, independent of the sort key. Flow::

    enter       一覧 in normal mode without popup (same refusals as the scan); record sort, direction, 所持数, thumbs
    books       open up to ``books`` present tiles of the first screen, one at a time; each must get the pink ✓ (and
                only it) and show a readable detail title; record whether its list starts at the top; then wheel the
                detail list one notch at a time (≤ ``detail_wheels``) until its thumb reaches the end or the content
                stops; every step records the pixel shift, the thumbs and the OCR row overlap with the previous page
    grid_down   wheel the grid one notch at a time: ``grid_wheels`` measured steps; with ``grid_seek_bottom`` keep going
                (≤ ``max_grid_wheels`` in total) until the content stops moving (= the grid bottom, with frames)
    probe       (``grid_jumps`` / ``track_taps``, default off; 2026-10-25 jump probe) ``grid_jumps`` −240 notches, then a
                tap on the scrollbar track above the thumb and one below it (each only when the thumb is clear of the
                fixed rect, else recorded as skipped), then ``grid_jumps`` +240 notches; every step waits for two
                identical captures and records the thumb before/after and the located row offset (frames are saved,
                so the exact content shift is measured offline against the scanned grid)
    grid_down   (skipped when ``grid_wheels`` is 0 without ``grid_seek_bottom``)
    grid_up     ``grid_up_wheels`` notches back up (the up step for the scan's seek-top)
    leave       back key (Pipeline then leaves アイドル一覧 and アイドル page)

Stops (no retry, no further input): the scan's 一覧 refusals (popup, 保護/売却 mode, destructive marker, unknown /
picker screen), sort / direction / 所持数 changes, ✓ binding lost, detail title changing during a book, frame / hold
budgets. Every frame is saved by the runtime; each step names its before/after frame numbers.
"""
from __future__ import annotations

import dataclasses
import statistics
from typing import Any, Mapping

from agent.core import knowhow_books as kb
from agent.core import knowhow_grid as kg
from agent.core import knowhow_scan as ks

GRID_ROI = (526, 172, 690, 476)      # grid content between the header rule and the 所持数 bar, left of the scrollbar
DETAIL_ROI = (24, 262, 400, 306)     # detail rows (kb.DETAIL_LIST_ROI without the scrollbar at x 438)
ROW_PITCH = kb.LIST_GRID["pitch_y"]  # 157.5 px
WHOLE_ROW_TOLERANCE = 4              # px
MOVE_MIN_PX = 2

DEFAULTS = {"books": 2, "detail_wheels": 10, "grid_wheels": 4, "grid_seek_bottom": True, "max_grid_wheels": 120,
            "grid_up_wheels": 2, "max_frames": 400, "max_holds": 3, "grid_jumps": 0, "track_taps": False}
LIMITS = {"books": 5, "detail_wheels": 30, "grid_wheels": 30, "max_grid_wheels": 180, "grid_up_wheels": 10,
          "max_frames": 1000, "max_holds": 10, "grid_jumps": 5}
NAV_INPUTS = 3                       # home-idol, idol-portal-list, idol-list-tab-knowhow
EXIT_INPUTS = 3                      # knowhow back, idol-list back, portal back


@dataclasses.dataclass(frozen=True)
class CalibrationOptions:
    books: int = DEFAULTS["books"]
    detail_wheels: int = DEFAULTS["detail_wheels"]          # per book
    grid_wheels: int = DEFAULTS["grid_wheels"]              # measured steps from the top
    grid_seek_bottom: bool = DEFAULTS["grid_seek_bottom"]
    max_grid_wheels: int = DEFAULTS["max_grid_wheels"]      # all grid down notches incl. grid_wheels
    grid_up_wheels: int = DEFAULTS["grid_up_wheels"]
    max_frames: int = DEFAULTS["max_frames"]
    max_holds: int = DEFAULTS["max_holds"]
    grid_jumps: int = DEFAULTS["grid_jumps"]                # −240 notches down, then as many +240 up (probe)
    track_taps: bool = DEFAULTS["track_taps"]               # one tap above and one below the thumb (probe)

    @classmethod
    def from_params(cls, data: Mapping[str, Any] | None) -> "CalibrationOptions":
        data = dict(data or {})
        unknown = set(data) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"校准选项未知: {sorted(unknown)}")
        options = cls(**data)
        if type(options.grid_seek_bottom) is not bool or type(options.track_taps) is not bool:
            raise ValueError("grid_seek_bottom / track_taps 须为布尔值")
        for name, high in LIMITS.items():
            value = getattr(options, name)
            if type(value) is not int or not 0 <= value <= high:
                raise ValueError(f"{name} 须为 0..{high} 的整数")
        if options.max_grid_wheels < options.grid_wheels:
            raise ValueError("max_grid_wheels 不得小于 grid_wheels")
        return options

    def max_inputs(self) -> int:
        """Upper bound of game inputs of one calibration run (the launcher's click cap)."""
        grid = self.max_grid_wheels if self.grid_seek_bottom else self.grid_wheels
        probe = 2 * self.grid_jumps + (2 if self.track_taps else 0)
        return NAV_INPUTS + self.books * (1 + self.detail_wheels) + probe + grid + self.grid_up_wheels + EXIT_INPUTS


# ---- pixel shift ---------------------------------------------------------------------------------------------------
def _gray(image, roi):
    import numpy as np

    x, y, w, h = roi
    return np.asarray(image[y:y + h, x:x + w], dtype=np.float32).mean(axis=2)


def _cost(before, after, d, min_rows):
    h = before.shape[0]
    if d >= 0:
        a, b = after[:h - d], before[d:]
    else:
        a, b = after[-d:], before[:h + d]
    if a.shape[0] < min_rows:
        return None
    return float(abs(a - b).mean())


def vertical_shift(before, after, roi, *, min_overlap=40, column_step=4, dy_range=None) -> dict:
    """Content shift ``dy`` (px; > 0 = content moved up, i.e. a scroll down) between two RGB frames inside ``roi``:
    after[y] == before[y + dy]. Exhaustive mean-absolute-difference search over every row offset (columns sampled every
    ``column_step`` px), so a periodic grid still resolves to the actual shift; ties go to the smaller shift.
    ``cost`` = residual grey difference at ``dy`` (≈0 for a clean shift), ``static`` = difference without shift,
    ``margin`` = gap to the best candidate ≥ 6 px away (small margin = ambiguous). ``dy_range`` = (low, high) limits
    the search (2026-10-12 live: an unconstrained search once returned −255 px on a downward detail scroll, where a
    51 px sliver happened to match)."""
    b = _gray(before, roi)[:, ::column_step]
    a = _gray(after, roi)[:, ::column_step]
    h = b.shape[0]
    low, high = -(h - min_overlap), h - min_overlap
    if dy_range is not None:
        low, high = max(low, dy_range[0]), min(high, dy_range[1])
    costs = {}
    for d in sorted(set(range(low, high + 1)) | {0}):
        c = _cost(b, a, d, min_overlap)
        if c is not None:
            costs[d] = c
    candidates = {d: c for d, c in costs.items() if low <= d <= high}
    dy = min(candidates, key=lambda d: (round(candidates[d], 3), abs(d)))
    others = [c for d, c in candidates.items() if abs(d - dy) >= 6]
    return {"dy": int(dy), "cost": round(costs[dy], 3), "static": round(costs[0], 3),
            "margin": round(min(others) - costs[dy], 3) if others else None,
            "moved": abs(dy) >= MOVE_MIN_PX and costs[0] - costs[dy] > 1.0}


DETAIL_DY_RANGE = (0, 400)            # detail is only scrolled down (wheel 159-160 px, 10-08 drags 201-202 px)
DETAIL_MIN_OVERLAP = 100


def detail_shift(before, after) -> dict:
    return vertical_shift(before, after, DETAIL_ROI, min_overlap=DETAIL_MIN_OVERLAP, dy_range=DETAIL_DY_RANGE)

def row_overlap(prev_rows, rows) -> int:
    """Rows shared between the end of the previous detail page and the start of this one (name + level)."""
    key = lambda r: (kb.normalize_name(r.get("name")), r.get("level"))  # noqa: E731
    p, n = [key(r) for r in prev_rows], [key(r) for r in rows]
    for k in range(min(len(p), len(n)), 0, -1):
        if p[-k:] == n[:k]:
            return k
    return 0


# ---- session -------------------------------------------------------------------------------------------------------
class CalibrationSession:
    """Same driving contract as ``knowhow_scan.ScanSession``: ``observe(rgb)`` once per new capture → Decision."""

    def __init__(self, options: CalibrationOptions, read_texts: ks.TextReader):
        self.options = options
        self.read_texts = read_texts
        self.phase = "enter"
        self.final: ks.Decision | None = None
        self.pending: ks.Decision | None = None
        self.frames = self.inputs = self.holds = 0
        self.sort = self.direction = None
        self.owned = self.capacity = None
        self.prev = None                     # (frame number, rgb image, texts) of the last evaluated capture
        self.enter: dict = {}
        self.books: list[dict] = []
        self.book: dict | None = None
        self.opened: set = set()
        self.grid_steps: list[dict] = []
        self.grid_up_steps: list[dict] = []
        self.grid_bottom: dict | None = None
        self.probe_plan: list[str] = []
        self.probe_pending: str | None = None
        self.probe_ref = None                # last post-input capture of a probe step (settle check)
        self.probe_steps: list[dict] = []
        self.soft_reason: str | None = None
        self.log: list[dict] = []

    # -- decisions (same bookkeeping as the scan session)
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
        decision = self._evaluate(image, texts, pending)
        if decision.kind != "hold":
            self.prev = (self.frames, image, texts)
        return decision

    def _evaluate(self, image, texts, pending) -> ks.Decision:
        screen = kb.classify_knowhow_screen(texts, image)
        problem = ks.ScanSession._list_problem(screen)
        if problem is not None:
            return self._stop(problem, f"after={pending.kind if pending else None}")
        if screen["owned"] is None or screen["sort"] is None or screen["direction"] is None:
            return self._hold("meta_unreadable")
        if self.phase == "enter":
            self.sort, self.direction = screen["sort"], screen["direction"]
            self.owned, self.capacity = screen["owned"], screen["capacity"]
            self.enter = {"frame": self.frames, "sort": self.sort, "direction": self.direction, "owned": self.owned,
                          "capacity": self.capacity, "grid_thumb": ks.read_thumb(image, ks.GRID_BAR_X, ks.GRID_TRACK),
                          "detail_empty": screen.get("detail_empty")}
            self.phase = "books"
            return self._next_book(image, texts)
        if (screen["sort"], screen["direction"]) != (self.sort, self.direction):
            return self._stop("sort_changed", screen["sort"], screen["direction"])
        if (screen["owned"], screen["capacity"]) != (self.owned, self.capacity):
            return self._stop("owned_changed", f"{screen['owned']}/{screen['capacity']}")
        if self.phase == "detail":
            return self._on_detail(image, texts, pending)
        if self.phase == "probe":
            return self._on_probe(image, texts)
        if self.phase == "grid_down":
            return self._on_grid(image, pending, up=False)
        if self.phase == "grid_up":
            return self._on_grid(image, pending, up=True)
        return self._stop("phase_unexpected", self.phase)

    # -- books
    def _next_book(self, image, texts) -> ks.Decision:
        if len(self.books) < self.options.books:
            tiles = kb.parse_list_tiles(texts, image)["tiles"]
            for t in tiles:
                if t.get("present") and (t["row"], t["col"]) not in self.opened:
                    self.opened.add((t["row"], t["col"]))
                    self.book = {"row": t["row"], "col": t["col"], "tile": {k: t.get(k) for k in (
                        "value", "lv5", "knowhow_count", "protected", "marked")}, "open_before_frame": self.frames,
                        "steps": [], "pages": []}
                    self.phase = "detail"
                    return self._decide(ks.Decision("open_tile", "calibrate_detail", row=t["row"], col=t["col"]))
        o = self.options
        if o.grid_jumps or o.track_taps:
            self.probe_plan = ["down"] * o.grid_jumps + (["upper", "lower"] if o.track_taps else []) + ["up"] * o.grid_jumps
            self.phase = "probe"
            return self._probe_next(image)
        return self._start_grid_down()

    def _start_grid_down(self) -> ks.Decision:
        if self.options.grid_wheels == 0 and not self.options.grid_seek_bottom:
            return self._after_grid_down(None)
        self.phase = "grid_down"
        return self._grid_wheel(up=False)

    # -- jump probe
    def _probe_next(self, image) -> ks.Decision:
        thumb = ks.read_thumb(image, ks.GRID_BAR_X, ks.GRID_TRACK)
        while self.probe_plan:
            step = self.probe_plan.pop(0)
            if step in ("down", "up"):
                self.probe_pending, self.probe_ref = step, None
                return self._decide(ks.Decision("jump_grid", "probe_jump", direction=step))
            _, y, _, h = ks.TRACK_UPPER if step == "upper" else ks.TRACK_LOWER
            clear = thumb is not None and (thumb["top"] > y + h + ks.TRACK_CLEAR_PX if step == "upper"
                                           else thumb["bottom"] < y - ks.TRACK_CLEAR_PX)
            if not clear:
                self.probe_steps.append({"step": step, "skipped": "thumb_not_clear", "frame": self.frames,
                                         "thumb": thumb})
                continue
            self.probe_pending, self.probe_ref = step, None
            return self._decide(ks.Decision("tap_track", "probe_track", key=step))
        return self._start_grid_down()

    def _on_probe(self, image, texts) -> ks.Decision:
        ref, self.probe_ref = self.probe_ref, image
        if ref is None or not kg.settled(ref, image):
            return self._hold("grid_settling")       # SC.KnowhowScan.List.Settle: short re-capture
        prev_frame, prev_image, _ = self.prev        # the capture the probe input was decided on
        before = ks.read_thumb(prev_image, ks.GRID_BAR_X, ks.GRID_TRACK)
        after = ks.read_thumb(image, ks.GRID_BAR_X, ks.GRID_TRACK)
        thumb_dy = after["top"] - before["top"] if before and after else None
        located = kg.locate_rows(texts, image)
        self.probe_steps.append({
            "step": self.probe_pending, "before": prev_frame, "after": self.frames, "thumb_before": before,
            "thumb_after": after, "thumb_dy": thumb_dy, "content_px_est": self._content_px(thumb_dy, before),
            "shift": vertical_shift(prev_image, image, GRID_ROI),   # meaningful only below the view height
            "offset_px": located.get("offset_px") if located.get("ok") else None})
        self.probe_pending, self.probe_ref = None, None
        return self._probe_next(image)

    def _content_px(self, thumb_dy, thumb) -> float | None:
        """Coarse content shift implied by the thumb movement (track travel ~ the scrollable rows; ±1 px of thumb is
        about ±35 px of content with 500 books)."""
        if thumb_dy is None or thumb is None or not self.owned:
            return None
        rows = -(-self.owned // ks.COLS)
        scrollable = max(0.0, rows * kg.PITCH_Y - ks.GRID_VIEW_PX)
        travel = (ks.GRID_TRACK[1] - ks.GRID_TRACK[0]) - (thumb["bottom"] - thumb["top"])
        return round(thumb_dy * scrollable / travel, 1) if travel > 0 else None

    def _on_detail(self, image, texts, pending) -> ks.Decision:
        b = self.book
        tiles = kb.parse_list_tiles(texts, image)["tiles"]
        checked = [(t["row"], t["col"]) for t in tiles if t.get("checked")]
        if checked != [(b["row"], b["col"])]:
            if not checked and not b["pages"]:
                return self._hold("detail_not_selected_yet")
            return self._stop("detail_binding_lost", *checked)
        page = kb.parse_detail_panel(kb.refine_detail_tokens(texts, image, self.read_texts), image)
        if page["empty"] or not page["title"]:
            return self._hold("detail_not_loaded")
        thumb = ks.read_thumb(image, ks.DETAIL_BAR_X, ks.DETAIL_TRACK)
        rows = [{"name": r.get("name"), "level": r.get("level"), "clipped": bool(r.get("clipped"))} for r in page["rows"]]
        if not b["pages"]:
            b.update(open_frame=self.frames, title=page["title"], knowhow_count=page["knowhow_count"],
                     thumb_on_open=thumb, starts_at_top=None if thumb is None else thumb["at_top"],
                     count_matches_tile=None if b["tile"]["knowhow_count"] is None
                     else page["knowhow_count"] == b["tile"]["knowhow_count"])
        else:
            if kb.normalize_name(page["title"]) != kb.normalize_name(b["title"]):
                return self._stop("detail_binding_lost", "title")
            prev_frame, prev_image, _ = self.prev
            shift = detail_shift(prev_image, image)
            b["steps"].append({"before": prev_frame, "after": self.frames, **shift,
                               "thumb_before": b["pages"][-1]["thumb"], "thumb_after": thumb,
                               "row_overlap": row_overlap(b["pages"][-1]["rows"], rows),
                               "rows_before": len(b["pages"][-1]["rows"]), "rows_after": len(rows)})
        b["pages"].append({"frame": self.frames, "thumb": thumb, "rows": rows, "raw": page,
                           "dy": b["steps"][-1]["dy"] if len(b["pages"]) else None})
        last = b["steps"][-1] if b["steps"] else None
        end = None
        if thumb is None:
            end = "no_scrollbar"
        elif last is not None and not last["moved"]:
            end = "content_stopped"
        elif thumb["at_bottom"] and last is not None:
            end = "scrollbar_at_end"
        elif len(b["steps"]) >= self.options.detail_wheels:
            end = "wheel_budget"
        if end is None:
            return self._decide(ks.Decision("scroll_detail", "calibrate_detail_step", direction="down",
                                            notes=(f"book={len(self.books)}",)))
        merged = kb.merge_detail_pages([p["raw"] for p in b["pages"]], shifts=[p["dy"] for p in b["pages"]])
        b["end"] = end
        b["rows_merged"] = len(merged.get("rows") or ())
        b["merge_complete"] = bool(merged.get("complete"))
        b["merge_unknowns"] = list(merged.get("unknowns") or ())
        self.books.append(b)
        self.book = None
        self.phase = "books"
        return self._next_book(image, texts)

    # -- grid
    def _grid_wheel(self, *, up: bool) -> ks.Decision:
        return self._decide(ks.Decision("scroll_grid", "calibrate_grid_step", direction="up" if up else "down"))

    def _on_grid(self, image, pending, *, up: bool) -> ks.Decision:
        prev_frame, prev_image, _ = self.prev
        steps = self.grid_up_steps if up else self.grid_steps
        thumb_before = ks.read_thumb(prev_image, ks.GRID_BAR_X, ks.GRID_TRACK)
        thumb = ks.read_thumb(image, ks.GRID_BAR_X, ks.GRID_TRACK)
        shift = vertical_shift(prev_image, image, GRID_ROI)
        steps.append({"before": prev_frame, "after": self.frames, **shift,
                      "rows": round(shift["dy"] / ROW_PITCH, 3), "thumb_before": thumb_before, "thumb_after": thumb})
        if up:
            if len(steps) < self.options.grid_up_wheels and shift["moved"]:
                return self._grid_wheel(up=True)
            return self._leave()
        if not shift["moved"]:
            self.grid_bottom = {"reached": True, "frame": self.frames, "wheels": len(steps), "thumb": thumb}
            return self._after_grid_down(image)
        limit = self.options.max_grid_wheels if self.options.grid_seek_bottom else self.options.grid_wheels
        if len(steps) >= limit:
            if self.options.grid_seek_bottom:
                self.soft_reason = self.soft_reason or "grid_wheel_budget_before_bottom"
            self.grid_bottom = {"reached": False, "frame": self.frames, "wheels": len(steps), "thumb": thumb}
            return self._after_grid_down(image)
        return self._grid_wheel(up=False)

    def _after_grid_down(self, image) -> ks.Decision:
        if self.options.grid_up_wheels:
            self.phase = "grid_up"
            return self._grid_wheel(up=True)
        return self._leave()

    def _leave(self) -> ks.Decision:
        self.phase = "leave"
        return self._decide(ks.Decision("leave", self.soft_reason or "calibrated"))

    # -- result
    def result(self) -> dict:
        def whole(px):
            return abs(px - round(px / ROW_PITCH) * ROW_PITCH) <= WHOLE_ROW_TOLERANCE

        moved = [s for s in self.grid_steps if s["moved"]]
        # the last moving step before the bottom may be short; it is reported apart from the regular step
        regular = moved[:-1] if self.grid_bottom and self.grid_bottom["reached"] and len(moved) > 1 else moved
        px = [s["dy"] for s in regular]
        probe = {"steps": self.probe_steps, "jumps": self.options.grid_jumps, "track_taps": self.options.track_taps,
                 "jump_thumb_dy": [s["thumb_dy"] for s in self.probe_steps if s["step"] in ("down", "up")],
                 "track": {s["step"]: s.get("skipped") or s.get("thumb_dy") for s in self.probe_steps
                           if s["step"] in ("upper", "lower")}}
        grid = {"steps": self.grid_steps, "up_steps": self.grid_up_steps, "bottom": self.grid_bottom,
                "step_px": {"median": statistics.median(px), "min": min(px), "max": max(px)} if px else None,
                "step_rows": round(statistics.median(px) / ROW_PITCH, 3) if px else None,
                "whole_rows": bool(px) and all(whole(v) for v in px),
                "consistent": bool(px) and max(px) - min(px) <= WHOLE_ROW_TOLERANCE,
                "last_step_px": moved[-1]["dy"] if moved else None,
                "up_step_px": [s["dy"] for s in self.grid_up_steps]}
        detail_px = [s["dy"] for b in self.books for s in b["steps"] if s["moved"]]
        overlaps = [s["row_overlap"] for b in self.books for s in b["steps"] if s["moved"]]
        books = [{k: v for k, v in b.items() if k != "pages"} | {"pages": [{"frame": p["frame"], "thumb": p["thumb"],
                 "rows": len(p["rows"])} for p in b["pages"]]} for b in self.books]
        detail = {"books": books,
                  "step_px": {"median": statistics.median(detail_px), "min": min(detail_px), "max": max(detail_px)}
                  if detail_px else None,
                  "min_row_overlap": min(overlaps) if overlaps else None,
                  "starts_at_top": [b.get("starts_at_top") for b in self.books],
                  "all_reached_end": bool(self.books) and all(b.get("end") in ("scrollbar_at_end", "content_stopped",
                                                                                "no_scrollbar") for b in self.books)}
        stopped = self.final is not None and self.final.kind == "stop"
        ready = (not stopped and grid["whole_rows"] and grid["consistent"]
                 and (detail["min_row_overlap"] or 0) >= 1 and all(v is not False for v in detail["starts_at_top"]))
        return {"version": 1, "kind": "knowhow_calibration", "partial": stopped or self.soft_reason is not None,
                "outcome": None if self.final is None else self.final.reason, "stopped": stopped,
                "soft_reason": self.soft_reason, "frames": self.frames, "inputs": self.inputs, "enter": self.enter,
                "grid": grid, "detail": detail, "probe": probe,
                "scan_ready": ready,
                "notes": ["scan_ready 只说明本次测得的网格步长是整行且一致、详情步之间有行重叠、详情从顶部开始；"
                          "须由监督者核对帧后再启用扫描模式。"],
                "log": list(self.log)}


__all__ = ["CalibrationOptions", "CalibrationSession", "vertical_shift", "detail_shift", "row_overlap", "GRID_ROI", "DETAIL_ROI"]
