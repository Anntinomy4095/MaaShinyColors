"""K3: supervised read-only scan of the ノウハウブック一覧 (pure decision core; no Maa import, no input).

``knowhow_scan_actions`` binds this core to the ``SC.KnowhowScan.*`` Pipeline
(every node ``enabled: false``; the input steps are ``input_action_missing:*`` placeholders until the action table
has reviewed entries and a supervised run passed). Mirrors ``card_list_scan``: call ``observe(image)`` once per *new*
capture; it returns one Decision. After an input decision the caller performs exactly that input and captures again.

Entry (outside the Produce flow, as reached on 2026-10-08): home → アイドル → アイドル一覧 → left tab ノウハウブック →
ノウハウブック一覧. The Produce-preparation picker (ノウハウブック選択) is not used:
its layout is not calibrated and a picker frame stops the scan.

Flow::

    enter       record the original sort (デフォルト, ノウハウ評価値順 or 獲得順, ▼降順 only; anything else stops before
                any input) and 所持数; not the target sort -> open_sort
    sort        open_sort -> (並び替え popup) pick_sort(評価値順) -> (key shown selected) apply_sort -> list in 評価値順;
                every step once, any other frame stops
    seek_top    grid scrollbar thumb not at the top -> scroll_grid(up) (bounded; no movement stops)
    grid        the grid does not snap (a wheel notch moves 159-160 px, rows are 157.5 px apart):
                after every grid wheel hold until two consecutive captures are identical (knowhow_grid.settled, a
                pixel check before any OCR; an unchanged capture with the same thumb = content stopped; wheels paced
                to the host limit, GRID_PACE); locate the rows on every frame
                (knowhow_grid.locate_rows, not ok -> stop) and read the readable rows (評価値, Lv5↑ N/M pill, 保護 lock;
                マーク only on complete rows). Bind the screen to absolute rows: the located offset fixes the content
                position modulo one row; the whole-row placement must agree tile by tile with every row already read
                (>= 1 shared row) and be unique (a reliable pixel shift may break a tie; mismatch -> grid_overlap_lost,
                several -> grid_overlap_ambiguous, a step the thumb contradicts -> grid_thumb_mismatch); values stay
                non-increasing; then open each unread tile of a complete row, then scroll_grid(down) until the thumb is
                at the bottom or the content stops; complete only when the tile count equals 所持数
    detail      open_tile through the tile's tap slot (knowhow_grid.slot_for: a fixed rect inside the located cover)
                -> the tile gets the pink ✓ (and only it), the grid did not move, the panel title is read,
                ノウハウ数 equals the tile's M and 保護 equals the tile lock, the panel list starts at the top; then
                scroll_detail until merged rows == ノウハウ数 with the last row unclipped (knowhow_books.merge_detail_pages;
                a step without overlap, no movement or the thumb at the end with rows missing stops)
    keep        top-up only, after the newest books were read (ScanOptions.keep_lock / keep_sell, from the W.I.N.G.
                end settings): plan each new book with knowhow_keep.plan_new_books against the library as the finish
                will write it; lock (保護) the planned books one at a time on the detail panel: the book is shown (its
                tile alone has the ✓, title / stats / ノウハウ数 match the read, 保護 not set) -> press_protect once ->
                the panel reads 保護解除 and the tile lock appears (bounded holds), else stop with the lock uncertain.
                Planned sales are only reported (SELL_EXECUTOR off until the 売却 confirm dialog is sampled).
    restore     back to the original sort (same three steps), then leave (back button)

Stops (no retry, no further input): unknown / picker frame, any popup that was not just requested, 保護 or 売却
multi-select mode, a destructive marker, unreadable mode guards, sort/direction/所持数 changes, overlap lost or
ambiguous, ✓ binding lost, detail mismatch, frame / hold budgets. Budget exhaustion of inputs / scrolls / books is a
*soft* end: the scan winds down through the sort restore and leaves (inputs are reserved for it). A hard stop leaves the
sort as it is and reports ``sort_restore_needed``. Results are always flagged: ``grid_complete`` (whole 評価値 grid
proven), ``details_complete`` (every grid tile has a complete book), ``partial``.

Resume: only runs of the same ``scan_id`` may reuse a previous ``result()``. The host keeps the id across automatic
segments and, since 2026-11-24, across a stop followed by a user start while no game input happened in between; it
starts a new id for a fresh scan or changed game activity. Legacy/different scans are read afresh: tile signatures
alone cannot identify books. Within one scan the owned count and grid prefix must still match before reuse.

Calibration status: grid/detail scrollbar geometry and the sort popup selection are measured on the 2026-10-08 native
frames; the wheel steps were measured by the 2026-10-12 calibration run (grid 159-160 px, not snapping; detail 159-160
px). The drifting grid is read with knowhow_grid and replayed offline on those frames; detail steps must keep >= 1 row
of overlap.
"""
from __future__ import annotations

import collections
import copy
import dataclasses
import re
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from agent.core import knowhow_books as kb
from agent.core import knowhow_grid as kg
from agent.core import knowhow_keep as kk
from agent.core import knowhow_vocab as kv

# ---- geometry (native 1280x720, 2026-10-08 live-capture-20261008-knowhow) ---------------------------------------
GRID_BAR_X = 1237                 # grid scrollbar: dark track y 229..655 under a white thumb y 187..228 at the top
GRID_TRACK = (187, 655)
DETAIL_BAR_X = 438                # detail scrollbar: thumb y 278..310 at the top, 510..542 at the end (21 rows)
DETAIL_TRACK = (276, 545)
BAR_BRIGHT_MIN, BAR_DARK_MAX, BAR_EDGE = 225, 180, 4
SORT_BUTTON = (963, 104, 166, 42)                    # 「デフォルト」/「ノウハウ評価値順」 button above the grid
SORT_POPUP_KEYS = {                                  # 並び替え popup, 3x3 buttons (knowhow_sort_options.png)
    "デフォルト": (373, 220, 165, 66), "ノウハウ数順": (557, 220, 165, 66), kb.SORT_EVAL: (741, 220, 165, 66),
    "Vocal上昇値順": (373, 315, 165, 66), "Dance上昇値順": (557, 315, 165, 66), "Visual上昇値順": (741, 315, 165, 66),
    "メンタル上昇値順": (373, 409, 165, 66), "獲得順": (557, 409, 165, 66), "Lv順": (741, 409, 165, 66),
}
SORT_APPLY = (558, 505, 164, 76)                     # 決定 (sort only; pink 0.48)
SORT_SELECTED_PINK_MIN = 0.03                        # selected key: pink border/text 0.076, others 0.000
# An original sort the scan can put back. 獲得順 (a list kept by time is switched to 評価値順 for a
# library read and back afterwards) uses the newest-books scan's key; its first live press is checked like the others.
RESTORABLE_SORTS = ("デフォルト", kb.SORT_EVAL, kb.SORT_DATE)
PICK_NODES = {"デフォルト": "SC.KnowhowScan.Sort.PickDefault", kb.SORT_EVAL: "SC.KnowhowScan.Sort.PickEval",
              kb.SORT_DATE: "SC.KnowhowScan.Sort.PickDate"}   # sort keys with a pick action
# Stops raised only after the frame passed the 一覧 checks (no popup / destructive marker / mode / 所持数 / direction /
# sort change): the scan backs out home instead of halting on the list (once; a second problem halts as before).
RECOVERABLE_STOPS = frozenset({
    "hold_budget_exhausted", "grid_not_settled", "grid_locate_failed", "grid_overlap_lost", "grid_overlap_ambiguous", "grid_thumb_mismatch",
    "grid_changed_during_detail", "detail_binding_lost", "detail_mismatch", "detail_not_at_top", "detail_stalled",
    "detail_overlap_lost", "detail_rows_missing", "detail_scroll_budget_exhausted"})
RECOVERABLE_PHASES = ("seek_top", "grid", "detail")
COLS, ROWS = kb.LIST_GRID["cols"], kb.LIST_GRID["rows"]
# drifting grid (2026-10-12 live: a wheel notch moves 159-160 px, rows are 157.5 px apart)
GRID_VIEW_PX = kg.VIEW_BOTTOM - kg.VIEW_TOP     # 481: a larger step cannot share a row with the previous screen
GRID_STEP_BACK_PX = 3                           # a downward wheel never moves the content back (locator jitter only)
GRID_SHIFT_TOL = 6                              # px: placement step vs. the measured pixel shift
GRID_SHIFT_MARGIN = 1.0                         # vertical_shift margin to break a tie between agreeing placements
GRID_THUMB_TOL = kg.PITCH_Y                     # px: chosen step vs. the thumb estimate (live error <= 48 px)
GRID_STILL_TOL = 1.5                            # px: the grid must not move while a book is opened / re-read
GRID_TOP_TOL = kg.INLIER_PX                     # px: with the thumb at the top the rows sit on the aligned lattice
SLOT_PREFIX = "knowhow-slot-"
# Newest-books scan (ScanOptions.newest = N > 0; 增量): 獲得順 ▼降順 puts the latest books first, so the
# first N tiles of the top screen are opened (oldest of them first: a partial run stays contiguous with the books read
# before) and read in full, the sort is restored and the run leaves. Books a Produce end put into
# ``KnowhowInventory.pending`` (partial: no ノウハウ数 / 評価値, rows cut) become complete records. No grid wheel: N stays
# within the two top rows. 評価値 is not on the tiles in this sort (book records keep it unknown).
NEWEST_MAX = 10
# Top-up (ScanOptions.topup, 「produce 结束后读取的书…当做过而不匹配数量选择做增量补充」): N is decided on
# the first 一覧 frame from 所持数 and the inventory (``topup_plan``): baseline = 所持数 of the last complete library
# scan, plus the books top-ups read since (``KnowhowInventory.recent``). "only" (after a Produce) reads N or leaves;
# "prefer" (the full-scan button) reads N when a baseline count differs by 1..NEWEST_MAX, else scans the whole library.
TOPUP_MODES = ("off", "only", "prefer")
TOPUP_ANCHOR = 3                  # tile-key fields compared at index N: (date, Lv5 N, ノウハウ数 M); the lock may change
# Keep step (lock / sell the new books after a W.I.N.G. run, both opt-in). The detail panel's 保護
# button toggles one book (保護 <-> 保護解除, knowhow_books.DETAIL_PROTECT_ROI); its first live press is the sample: any
# popup or a state that does not settle stops the run with that book's lock uncertain. Selling needs the 売却 confirm
# dialog, never sampled: planned sales are reported only.
SELL_EXECUTOR = False

DEFAULTS = {"details": True, "max_books": 600, "max_inputs": 590, "max_frames": 8000, "max_holds": 3,
            "max_grid_scrolls": 300, "max_detail_scrolls": 30, "max_seconds": 0, "newest": 0, "topup": "off"}
LIMITS = {"max_books": 600, "max_inputs": 6000, "max_frames": 8000, "max_holds": 10, "max_grid_scrolls": 300,
          "max_detail_scrolls": 30, "max_seconds": 3600, "newest": NEWEST_MAX}
# max_seconds (0 = off; tools/run_tasks sets it from the task timeout): a segment winds down like an exhausted input budget
# once this long has passed since its first capture, and opens no new book in the last BOOK_RESERVE_S. 2026-10-23 live
# seg7 hit the 2700 s task timeout at 522/590 inputs (task_ended_before_report, stopped on the 一覧).
BOOK_RESERVE_S = 90
INPUT_KINDS = ("open_sort", "pick_sort", "apply_sort", "scroll_grid", "open_tile", "scroll_detail", "leave",
               "open_filter", "scroll_filter", "cancel_filter",      # filter: knowhow_filter
               "jump_grid", "tap_track",                             # calibration probe only (knowhow_calibrate)
               "press_protect")                                      # keep step: lock the shown book
# 絞り込み popup (2026-10-12 native knowhow-sample-20261012 #001/#117/#122/#131; knowhow_filter uses these)
FILTER_BUTTON = (826, 110, 86, 26)         # 一覧 「絞り込み」 text (818,107,100,29) inside the button x 787-952 y 104-146
FILTER_POPUP_CONTENT = (165, 104, 935, 466)  # scrolling part of the popup (between its header and button row)
FILTER_POPUP_WHEEL = (1060, 300, 30, 40)   # right margin of the content: no checkbox / label / button (wheel only)
FILTER_CANCEL = (404, 606, 115, 36)        # キャンセル text (394,607,135,33); button x 380-545 y 588-663
FILTER_RESET = (586, 606, 110, 36)         # リセット (580,606,120,38) — no action yet
FILTER_APPLY = (770, 604, 96, 40)          # 決定 (780,600,76,45) — no action yet
FILTER_WHEEL = 60
# Jump probe (calibration mode only, 2026-10-25): the largest host wheel step and taps on the grid scrollbar track.
# Track: dark x 1232-1239 under the thumb x 1227-1245 (knowhow_rating_grid.png); each tap only when the thumb is clear.
GRID_JUMP_WHEEL = 240
TRACK_UPPER = (1232, 192, 8, 14)           # above the thumb once it left the top (thumb y 187..228 at the top)
TRACK_LOWER = (1232, 610, 8, 20)           # near the track end (y 655); 8 px wide: the table's 20 % margin keeps 4 px
TRACK_CLEAR_PX = 4


# ---- options / decisions -----------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class ScanOptions:
    details: bool = DEFAULTS["details"]               # False = 評価値 grid only (location hints, no book contents)
    max_books: int = DEFAULTS["max_books"]            # books opened in this run (resumed books do not count)
    max_inputs: int = DEFAULTS["max_inputs"]
    max_frames: int = DEFAULTS["max_frames"]
    max_holds: int = DEFAULTS["max_holds"]            # consecutive re-captures of a not-yet-readable frame
    max_grid_scrolls: int = DEFAULTS["max_grid_scrolls"]
    max_detail_scrolls: int = DEFAULTS["max_detail_scrolls"]  # per book
    max_seconds: int = DEFAULTS["max_seconds"]        # segment time budget (0 = none); see BOOK_RESERVE_S
    recover_home: bool = True                        # RECOVERABLE_STOPS back out home instead of halting on the list
    newest: int = DEFAULTS["newest"]                 # > 0: read only the newest N books (獲得順); see NEWEST_MAX
    topup: str = DEFAULTS["topup"]                   # off | only | prefer: N from 所持数 vs the inventory (TOPUP_MODES)
    keep_lock: bool = False                          # keep step: lock the books knowhow_keep plans to lock
    keep_sell: bool = False                          # keep step: plan sales (reported only, SELL_EXECUTOR)
    keep_strategy: str = "useless_only"              # knowhow_keep.STRATEGIES
    keep_plan: bool = False                          # keep step without lock/sell: report the plan only (W.I.N.G. 连跑)

    @property
    def keep(self) -> bool:
        return self.keep_lock or self.keep_sell or self.keep_plan

    @classmethod
    def from_params(cls, data: Mapping[str, Any] | None) -> "ScanOptions":
        data = dict(data or {})
        unknown = set(data) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"书库扫描选项未知: {sorted(unknown)}")
        options = cls(**data)
        if any(type(getattr(options, k)) is not bool for k in ("details", "recover_home", "keep_lock", "keep_sell", "keep_plan")):
            raise ValueError("details / recover_home / keep_lock / keep_sell / keep_plan 须为布尔值")
        if options.topup not in TOPUP_MODES:
            raise ValueError(f"topup 须为 {'、'.join(TOPUP_MODES)}")
        if options.keep_strategy not in kk.STRATEGIES:
            raise ValueError(f"keep_strategy 须为 {'、'.join(kk.STRATEGIES)}")
        if options.keep and options.topup != "only":
            raise ValueError("锁书/卖书只在 Produce 后读新书时进行（topup 须为 only）")
        if (options.newest or options.topup != "off") and not options.details:
            raise ValueError("newest / topup 需要 details")
        if options.newest and options.topup != "off":
            raise ValueError("topup 自行决定读几本，不能同时给 newest")
        for name, high in LIMITS.items():
            value = getattr(options, name)
            if type(value) is not int or not 0 <= value <= high:
                raise ValueError(f"{name} 须为 0..{high} 的整数")
        return options


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                     # INPUT_KINDS | hold | stop
    reason: str
    key: str | None = None        # pick_sort: sort key; open_tile: tap slot action id (knowhow-slot-y{k}c{c}), None = fixed
    row: int | None = None        # open_tile: visible row order / column (fixed knowhow-tile-r{row}c{col} when key is None)
    col: int | None = None
    direction: str | None = None  # scroll_grid / scroll_detail
    notes: tuple = ()

    @property
    def is_input(self) -> bool:
        return self.kind in INPUT_KINDS

    def to_dict(self):
        return dataclasses.asdict(self) | {"notes": list(self.notes)}


def pipeline_node(decision: Decision) -> str | None:
    """The SC.KnowhowScan.* node that performs this decision (None for hold/stop)."""
    if decision.kind == "open_sort":
        return "SC.KnowhowScan.List.OpenSort"
    if decision.kind == "pick_sort":
        return PICK_NODES.get(decision.key)
    if decision.kind == "apply_sort":
        return "SC.KnowhowScan.Sort.Apply"
    if decision.kind == "scroll_grid":
        return "SC.KnowhowScan.Grid.ScrollDown" if decision.direction == "down" else "SC.KnowhowScan.Grid.ScrollUp"
    if decision.kind == "open_tile":
        if decision.key is not None:
            return slot_node(decision.key)
        return f"SC.KnowhowScan.Grid.OpenTile.R{decision.row}C{decision.col}"
    if decision.kind == "scroll_detail":
        return "SC.KnowhowScan.Detail.ScrollDown"
    if decision.kind == "leave":
        return "SC.KnowhowScan.List.Back"
    if decision.kind == "open_filter":
        return "SC.KnowhowScan.Filter.Open"
    if decision.kind == "scroll_filter":
        return "SC.KnowhowScan.Filter.ScrollDown" if decision.direction == "down" else "SC.KnowhowScan.Filter.ScrollUp"
    if decision.kind == "cancel_filter":
        return "SC.KnowhowScan.Filter.Cancel"
    if decision.kind == "jump_grid":
        return "SC.KnowhowScan.Grid.JumpDown" if decision.direction == "down" else "SC.KnowhowScan.Grid.JumpUp"
    if decision.kind == "tap_track":
        return {"upper": "SC.KnowhowScan.Grid.TrackUpper", "lower": "SC.KnowhowScan.Grid.TrackLower"}.get(decision.key)
    if decision.kind == "press_protect":
        return "SC.KnowhowScan.Keep.Protect"
    return None


def slot_node(action_id: str) -> str | None:
    """SC.KnowhowScan.Grid.OpenSlot.Y{k}C{c} for a knowhow_grid tap slot id, else None."""
    import re

    m = re.fullmatch(SLOT_PREFIX + r"y(\d)c(\d)", action_id or "")
    return None if m is None or kg.slot_rect(action_id) is None else f"SC.KnowhowScan.Grid.OpenSlot.Y{m[1]}C{m[2]}"


# ---- pixel readers -----------------------------------------------------------------------------------------------
def read_thumb(image, x: int, track: tuple[int, int]) -> dict | None:
    """Scrollbar thumb {top, bottom, at_top, at_bottom} on a dark track; None when no track is visible."""
    import numpy as np

    if image is None:
        return None
    y0, y1 = track
    col = np.asarray(image[y0:y1 + 1, x - 1:x + 2], dtype=np.float32).mean(axis=(1, 2))
    bright = col > BAR_BRIGHT_MIN
    if (col < BAR_DARK_MAX).mean() < 0.5:
        return None
    best, start = (0, None, None), None
    for i, b in enumerate(list(bright) + [False]):
        if b and start is None:
            start = i
        elif not b and start is not None:
            if i - start > best[0]:
                best = (i - start, start, i - 1)
            start = None
    if best[0] < 8:
        return None
    top, bottom = y0 + best[1], y0 + best[2]
    return {"top": top, "bottom": bottom, "at_top": top <= y0 + BAR_EDGE, "at_bottom": bottom >= y1 - BAR_EDGE}


LEFT_PANEL_W = 490                # detail panel x < 470 (its ‹ tab ends ~490); the grid and its header start at x 500
GRID_REGION = (500, 90, 770, 610)  # x, y, w, h: header buttons, tiles, scrollbar, 所持数
GRID_SAME_MAX = 0.002             # fraction of grid pixels changed by > 24 grey levels: detail scroll <= 0.0003,
                                  # tile open / grid scroll >= 0.0064 (2026-10-23 seg18 frames)


def _grid_region(image):
    x, y, w, h = GRID_REGION
    return np.asarray(image[y:y + h, x:x + w], dtype=np.int16).copy()


# Detail wheel settle (2026-10-24, replaces the fixed 2 s wait): a capture is read only once it provably shows the final
# position: the rows moved by one full notch (wheel -60 = 159-160 px live; the list end stops short), or the rows
# region is identical to the previous capture. Pacing keeps the detail wheel under the host limit (30 per action and
# minute): at most DETAIL_PACE[0] wheels in DETAIL_PACE[1] s.
DETAIL_NOTCH_PX = (158, 161)
DETAIL_ROWS_ROI = (24, 262, 400, 306)     # knowhow_calibrate.DETAIL_ROI
DETAIL_SETTLE_HOLDS = 6                   # re-captures while the rows still move; then stop
DETAIL_PACE = (28, 61.0)
# Grid wheel settle (2026-10-24, replaces the fixed 2 s wait and the full OCR of the moving first capture): checked on
# pixels before any OCR, a capture is read only once it equals the previous capture (knowhow_grid.settled). No
# one-notch shortcut as for the detail: the grid shift reads 159-165 px for 159-160 px steps (2026-10-23 live, 253
# wheels), too coarse for GRID_STILL_TOL. Paced like the detail wheel, per direction (separate host actions).
GRID_SETTLE_HOLDS = 6
GRID_PACE = DETAIL_PACE
SETTLE_REASONS = ("grid_settling", "grid_stall_check", "detail_settling")   # short re-capture (SC.KnowhowScan.List.Settle)


def grid_unchanged(ref, image) -> bool:
    """True when the grid region of ``image`` matches ``ref`` (``_grid_region`` of an earlier capture)."""
    cur = _grid_region(image)
    return cur.shape == ref.shape and float((np.abs(cur - ref).max(axis=-1) > 24).mean()) <= GRID_SAME_MAX


def sort_popup_selection(image) -> list[str]:
    """Keys drawn selected (pink) in the 並び替え popup."""
    return [key for key, roi in SORT_POPUP_KEYS.items()
            if (kb._pink(image, (roi[0] + 6, roi[1] + 6, roi[2] - 12, roi[3] - 12)) or 0) >= SORT_SELECTED_PINK_MIN]


def tile_signature(tile: Mapping[str, Any], field: str = "value") -> tuple | None:
    """Overlap/binding key of one tile: (評価値, Lv5 N, ノウハウ数 M, lock); ``field`` "overlay" puts the 獲得順 date first
    instead. マーク is excluded (the selected tile's pink frame touches the badge corner). None = empty slot; raises
    ValueError for a present tile that is not fully read."""
    if tile.get("present") is False:
        return None
    sig = (tile.get(field), tile.get("lv5"), tile.get("knowhow_count"), tile.get("protected"))
    if tile.get("present") is not True or any(v is None for v in sig):
        raise ValueError(f"tile r{tile.get('row')}c{tile.get('col')} unreadable")
    return sig


def _rows(tiles, field: str = "value") -> list[tuple]:
    sigs = [tile_signature(t, field) for t in tiles]
    return [tuple(sigs[r * COLS:(r + 1) * COLS]) for r in range(ROWS)]


def _screen_rows(tiles, offset: float, field: str = "value") -> list[dict]:
    """``knowhow_grid.parse_tiles_at(readable=True)`` tiles grouped by row: [{m, y, complete, sigs, tiles}], where the
    row top is GRID_Y + offset + m·PITCH (m = lattice row relative to the aligned grid). Raises ValueError for an
    unreadable present tile (see ``tile_signature``)."""
    out: dict[int, dict] = {}
    for t in tiles:
        m = round((t["y_exact"] - kg.GRID_Y - offset) / kg.PITCH_Y)
        row = out.setdefault(m, {"m": m, "y": t["y"], "complete": t["complete"], "tiles": []})
        row["tiles"].append(t)
    for row in out.values():
        row["tiles"].sort(key=lambda t: t["col"])
        if [t["col"] for t in row["tiles"]] != list(range(COLS)):
            raise ValueError(f"row y{row['y']} columns")
        row["sigs"] = tuple(tile_signature(t, field) for t in row["tiles"])
    return [out[m] for m in sorted(out)]


def dates_descending(tiles) -> bool | None:
    """獲得順 ▼降順: the read acquisition dates of present tiles never increase in reading order (None: fewer than 2)."""
    dates = [t["overlay"] for t in sorted(tiles, key=lambda t: (t.get("y_exact", t.get("y", 0)), t.get("col", 0)))
             if t.get("present") and isinstance(t.get("overlay"), str)]
    return None if len(dates) < 2 else all(a >= b for a, b in zip(dates, dates[1:]))


def library_basis(inv: kb.KnowhowInventory) -> dict:
    """What a top-up needs from the inventory: the baseline 所持数 (last complete library scan, else None), the books
    top-ups read since, the tile key of the newest of them (anchor), the pending Produce-end books, and whether a
    misaligned top-up asked for a full scan."""
    grid = inv.grid or {}
    anchor = inv.recent[-1].get("signature") if inv.recent else None
    return {"baseline": grid.get("owned_total") if grid.get("complete") else None, "recent": len(inv.recent),
            "anchor": list(anchor) if anchor else None, "pending": len(inv.pending),
            "broken": (inv.sync or {}).get("state") == "needs_full_scan"}


def topup_plan(basis: Mapping[str, Any], owned_now: int | None) -> tuple[int, str]:
    """(N, reason) for a top-up: N > 0 only with reason ``topup`` (baseline count) or ``pending`` (no baseline: the
    Produce-end books). N = 0 reasons: library_up_to_date, nothing_pending, owned_unread, and the ones that need a
    complete library scan (FULL_SCAN_REASONS)."""
    if basis.get("broken"):
        return 0, "sync_broken"
    if basis.get("baseline") is None:
        n = min(int(basis.get("pending") or 0), NEWEST_MAX)
        return (n, "pending") if n else (0, "nothing_pending")
    if owned_now is None:
        return 0, "owned_unread"
    new = owned_now - basis["baseline"] - int(basis.get("recent") or 0)
    if new == 0:
        return 0, "library_up_to_date"
    if new < 0:
        return 0, "owned_below_inventory"
    if new > NEWEST_MAX:
        return 0, "topup_too_many"
    return new, "topup"


FULL_SCAN_REASONS = ("owned_below_inventory", "topup_too_many", "sync_broken", "topup_misaligned")


def library_sync(inv: kb.KnowhowInventory, owned_now: int | None = None) -> dict:
    """所持数 vs the inventory for the UI and the sell executor. ``owned_now`` defaults to the last 所持数 a scan read
    (``owned_seen``, with ``seen_at``). state: up_to_date | new_books | too_many | fewer_owned | needs_full_scan |
    no_baseline | unknown."""
    basis = library_basis(inv)
    seen = inv.owned_seen or {}
    owned = seen.get("owned") if owned_now is None else owned_now
    known = None if basis["baseline"] is None else basis["baseline"] + basis["recent"]
    new = None if known is None or owned is None else owned - known
    if basis["broken"]:
        state = "needs_full_scan"
    elif known is None:
        state = "no_baseline"
    elif owned is None:
        state = "unknown"
    elif new == 0:
        state = "up_to_date"
    elif new < 0:
        state = "fewer_owned"
    else:
        state = "too_many" if new > NEWEST_MAX else "new_books"
    return {"state": state, "owned_now": owned, "seen_at": seen.get("at") if owned_now is None else None,
            "known": known, "new": new, "baseline": basis["baseline"], "recent": basis["recent"],
            "pending": basis["pending"]}


_MISSING = object()

# ---- session -----------------------------------------------------------------------------------------------------
TextReader = Callable[[Any], Sequence]       # rgb ndarray -> full-frame OCR tokens [(text, (x, y, w, h), score)]


def validate_scan_id(value) -> str | None:
    """A host-generated id groups automatic segments; it is not a physical book identity."""
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
        raise ValueError("scan_id 须为1–64位字母、数字、下划线或连字符")
    return value


class ScanSession:
    """One scan run; see the module docstring for the flow and the stop rules."""

    def __init__(self, options: ScanOptions, read_texts: TextReader, *, resume: Mapping[str, Any] | None = None,
                 scan_id: str | None = None, basis: Mapping[str, Any] | None = None,
                 library: kb.KnowhowInventory | None = None, wanted: Sequence = ()):
        self.options = options
        self.library = library                    # keep step: the inventory the finish writes into (not modified)
        self.wanted = list(wanted)                # keep step: the UI wanted list (knowhow_books.load_wanted_from_profile)
        self.keep: dict | None = None             # keep step state / report (result()["keep"])
        self._set_newest(options.newest)
        if options.topup == "only":
            self.target_sort = kb.SORT_DATE
        self.basis = dict(basis or {})            # library_basis(inventory) for options.topup
        self.topup: dict | None = None            # the top-up plan once 所持数 was read (result()["topup"])
        self.scan_id = validate_scan_id(scan_id)
        self.read_texts = read_texts
        self.phase = "enter"
        self.final: Decision | None = None
        self.pending: Decision | None = None      # input sent before this capture
        self.frames = self.inputs = self.holds = 0
        self.grid_scrolls = 0
        self.books_opened = 0
        self.orig_sort: str | None = None
        self.current_sort: str | None = None
        self.sort_target: str | None = None
        self.sort_purpose: str | None = None      # "scan" | "restore"
        self.owned: int | None = None
        self.capacity: int | None = None
        # drifting grid (knowhow_grid): screen row tops are GRID_Y + offset + m·PITCH; absolute row = m + screen_n,
        # i.e. the content is scrolled by screen_n·PITCH − offset px from the top
        self.screen_n = 0
        self.screen_offset: float | None = None
        self.screen: dict[int, tuple] | None = None   # absolute row -> 5 signatures of every readable row on screen
        self.screen_tiles: dict[int, dict] = {}       # absolute index -> tile of a complete (tappable) row
        self.grid: dict[int, tuple | None] = {}   # absolute index -> signature (None = empty slot)
        self.marks: dict[int, bool] = {}
        self.grid_steps = 0
        self.grid_end: str | None = None
        self.step_log: list[dict] = []
        self.cur_image = self.cur_bar = None      # this capture (for the scroll bookkeeping)
        self.last_image = None                    # previous capture (settle check)
        self.scroll_from: dict | None = None      # {image, bar, direction}: grid wheel sent, not yet settled
        self.since_scroll = 0                     # captures since that wheel (pixel checks only, no OCR)
        self.scrolled: dict | None = None         # settled after a grid wheel: {pre, stalled, direction}
        self.books: dict[int, dict] = {}          # absolute index -> book record dict (+ grid_index, signature)
        self.detail: dict | None = None           # {index, row, col, slot, tile, pages, scrolls, thumb}
        self.soft_reason: str | None = None
        self.recovered_from: dict | None = None   # recoverable stop turned into a wind-down (RECOVERABLE_STOPS)
        self.log: list[dict] = []
        self.resume = self._load_resume(resume, self.scan_id)
        self.resume_valid_upto: int | None = None if self.resume is None else len(self.resume["tiles"])
        self.resumed: list[int] = []
        self.grid_ref = None          # (grid region pixels, grid tokens) of the last full-frame read (``_read``)
        self.partial_reads = 0
        self.detail_settle: dict | None = None    # {pre, since}: detail wheel sent, frame not yet proven settled
        self.settle = {"notch": 0, "still": 0, "holds": 0, "pace_holds": 0, "grid_holds": 0, "grid_pace_holds": 0}
        self.detail_wheels: collections.deque = collections.deque()   # clock() of each detail wheel (pacing)
        self.grid_wheels = {"down": collections.deque(), "up": collections.deque()}   # per host action
        self.clock: Callable[[], float] = time.monotonic
        self.started_at: float | None = None      # clock() of the first capture (max_seconds)
        self._shift_cache: tuple | None = None    # (image id, detail_shift) measured by the settle check
        # Known row names; the effect table learns from the resumed books' and this run's exact rows (knowhow_vocab).
        self.vocabulary = kv.Vocabulary.load(table=kv.learn_table((self.resume or {}).get("books", {}).values()))

    def _set_newest(self, n: int) -> None:
        self.newest = n
        self.target_sort = kb.SORT_DATE if n else kb.SORT_EVAL
        self.sig_field = "overlay" if n else "value"

    # -- resume
    @staticmethod
    def _load_resume(resume, current_scan_id):
        if (not current_scan_id or not isinstance(resume, Mapping) or resume.get("scan_id") != current_scan_id
                or resume.get("partial") is not True
                or not isinstance((resume.get("grid") or {}).get("tiles"), list)):
            return None
        tiles = [tuple(t) if isinstance(t, (list, tuple)) else None for t in resume["grid"]["tiles"]]
        books = {b["grid_index"]: dict(b) for b in resume.get("books") or ()
                 if isinstance(b, Mapping) and isinstance(b.get("grid_index"), int)}
        orig = resume.get("orig_sort") if resume.get("orig_sort") in RESTORABLE_SORTS else None
        return {"tiles": tiles, "books": books, "orig_sort": orig, "owned_total": resume.get("owned_total")}

    def _resumed_book(self, index: int, sig) -> dict | None:
        if self.resume is None or self.resume_valid_upto is None or index >= self.resume_valid_upto:
            return None
        if self.resume["owned_total"] != self.owned:
            self.resume_valid_upto = 0
            return None
        saved = self.resume["tiles"]
        if saved[index] != sig or any(saved[i] != self.grid.get(i) for i in range(index)):
            self.resume_valid_upto = index          # everything from here on may have shifted
            return None
        book = self.resume["books"].get(index)
        return book if book and kb.KnowhowBookRecord.from_dict(book).complete else None

    def _carried(self) -> tuple[list, dict]:
        """Saved grid tiles / books past the end of this run's grid, kept in the new partial while every tile this run
        saw matches the resume record (2026-10-23 live seg4: a run that stopped at index 20 wrote a 20-book partial
        over the 59-book one, so the next "latest" resume would have re-read 39 books)."""
        if self.resume is None or self.resume_valid_upto is None or self.resume["owned_total"] != self.owned:
            return [], {}
        saved, seen = self.resume["tiles"], sorted(self.grid)
        upto = min(self.resume_valid_upto, len(saved))
        if seen != list(range(len(seen))) or len(seen) >= upto or any(saved[i] != self.grid[i] for i in seen):
            return [], {}
        books = {i: b for i, b in self.resume["books"].items() if len(seen) <= i < upto}
        return list(saved[len(seen):upto]), books

    # -- decisions
    def _decide(self, decision: Decision, **note) -> Decision:
        self.log.append({"frame": self.frames, "phase": self.phase, **decision.to_dict(), **note})
        if decision.kind in ("stop", "leave"):
            self.final = decision
        if decision.is_input:
            self.inputs += 1
            self.pending = decision
            self.holds = 0
        if decision.kind == "scroll_detail":       # read again only once the rows provably stopped
            self.detail_settle = {"pre": self.cur_image, "since": 0}
            self.detail_wheels.append(self.clock())
        if decision.kind == "scroll_grid":         # read again only once the grid has settled after this wheel
            self.scroll_from = {"image": self.cur_image, "bar": self.cur_bar, "direction": decision.direction}
            self.since_scroll, self.scrolled = 0, None
            self.grid_wheels[decision.direction].append(self.clock())
        return decision

    def _stop(self, reason, *notes) -> Decision:
        if (self.options.recover_home and reason in RECOVERABLE_STOPS and self.phase in RECOVERABLE_PHASES
                and self.recovered_from is None):
            # A reading/binding problem on the plain 一覧 (screen, mode, popup and 所持数 checks already passed): the
            # unverified book is dropped (never stored), then the ordinary guarded wind-down restores the sort and
            # backs out to home. Read-only navigation; the run still ends partial with this reason.
            self.recovered_from = {"reason": reason, "notes": [str(n) for n in notes], "frame": self.frames}
            self.log.append({"frame": self.frames, "phase": self.phase, "kind": "recover", "reason": reason,
                             "notes": [str(n) for n in notes]})
            self.detail = None
            return self._wind_down(f"recovered:{reason}")
        return self._decide(Decision("stop", reason, notes=tuple(str(n) for n in notes)))

    def _hold(self, reason) -> Decision:
        self.holds += 1
        if self.holds > self.options.max_holds:
            return self._stop("hold_budget_exhausted", reason)
        return self._decide(Decision("hold", reason))

    def _reserve(self) -> int:
        """Inputs kept for the wind-down: leave, plus open/pick/apply when the original sort must be restored."""
        return 1 + (3 if self.orig_sort not in (None, self.target_sort) else 0)

    def _input(self, decision: Decision) -> Decision:
        """Budget-checked scan input. pick/apply/leave and the restore steps use the reserve and are never refused."""
        if (decision.kind in ("open_sort", "scroll_grid", "open_tile", "scroll_detail", "press_protect")
                and self.sort_purpose != "restore"):
            need = 3 if decision.kind == "open_sort" else 1
            if self.inputs + need + self._reserve() > self.options.max_inputs:
                return self._wind_down("input_budget_exhausted")
            if self._out_of_time(decision.kind):
                # Same soft end as the input budget (the next segment continues); the log says it was the time.
                self.log.append({"frame": self.frames, "phase": self.phase, "kind": "time_budget",
                                 "seconds": round(self.clock() - self.started_at, 1)})
                return self._wind_down("input_budget_exhausted")
        return self._decide(decision)

    def _out_of_time(self, kind) -> bool:
        if not self.options.max_seconds or self.started_at is None:
            return False
        left = self.options.max_seconds - (self.clock() - self.started_at)
        return left <= (BOOK_RESERVE_S if kind == "open_tile" else 0)

    def _wind_down(self, reason) -> Decision:
        """Soft end: keep a partly read book as an incomplete record, restore the sort, then leave."""
        self.soft_reason = self.soft_reason or reason
        if self.detail and self.detail["pages"]:
            merged = kb.merge_detail_pages(self.detail["pages"])
            self._store_book(merged)
        self.detail = None
        continuing = (self.scan_id and not self.newest
                      and self.soft_reason in ("book_budget_exhausted", "input_budget_exhausted"))
        # Automatic segments keep the evaluation sort. Restore once at the actual end, rather than re-sort tied books.
        if not continuing and self.current_sort != self.orig_sort and self.orig_sort in RESTORABLE_SORTS:
            self.sort_purpose, self.sort_target, self.phase = "restore", self.orig_sort, "sort_open"
            return self._decide(Decision("open_sort", "restore_sort", key=self.orig_sort))
        self.phase = "leave"
        return self._decide(Decision("leave", self.soft_reason))

    # -- frame checks
    @staticmethod
    def _list_problem(screen) -> str | None:
        """None for a normal-mode 一覧 without popup; else the stop reason."""
        if screen["refused"]:
            return "destructive_marker"
        if screen["popup"]:                       # popups dim the title: name the popup before "unknown"
            return f"popup_{screen['popup']}"
        if screen["screen"] == "picker":
            return "picker_uncalibrated"
        if screen["screen"] != "list":
            return "unknown_screen"
        if screen["mode"] in ("protect", "sale"):
            return f"mode_{screen['mode']}"
        if screen["mode"] != "normal":
            return "mode_unknown"
        return None

    # -- main entry
    def observe(self, image) -> Decision:
        """``image``: 1280x720 RGB ndarray of a fresh capture (one call per capture)."""
        if self.final is not None:
            return self.final
        self.frames += 1
        if self.started_at is None:
            self.started_at = self.clock()
        if self.frames > self.options.max_frames:
            return self._stop("frame_budget_exhausted")
        pending, self.pending = self.pending, None
        self.cur_image, self.cur_bar = image, None
        try:
            waiting = self._detail_settling(image)
            if waiting is None:
                waiting = self._grid_settling(image)
            if waiting is not None:
                self.pending = pending
                return waiting
            return self._evaluate(image, self._read(image), pending)
        finally:
            self.last_image = image

    def _detail_shift(self, before, image) -> dict:
        from agent.core.knowhow_calibrate import detail_shift   # lazy: knowhow_calibrate imports this module

        cached = self._shift_cache
        if cached is not None and cached[0] is before and cached[1] is image:
            return cached[2]
        measured = detail_shift(before, image)
        self._shift_cache = (before, image, measured)
        return measured

    def _detail_settling(self, image) -> Decision | None:
        """After a detail wheel, before any OCR: hold (no input) until the capture is proven settled."""
        s = self.detail_settle
        if s is None:
            return None
        s["since"] += 1
        measured = self._detail_shift(s["pre"], image)
        if measured["moved"] and DETAIL_NOTCH_PX[0] <= measured["dy"] <= DETAIL_NOTCH_PX[1]:
            proven = "notch"
        elif s["since"] > 1 and kg.settled(self.last_image, image, roi=DETAIL_ROWS_ROI):
            proven = "still"
        elif s["since"] > DETAIL_SETTLE_HOLDS:
            self.detail_settle = None
            return self._stop("detail_not_settled", f"dy={measured['dy']}")
        else:
            self.settle["holds"] += 1
            return self._decide(Decision("hold", "detail_settling", notes=(f"dy={measured['dy']}",)))
        self.settle[proven] += 1
        self.detail_settle = None
        return None

    def _grid_settling(self, image) -> Decision | None:
        """After a grid wheel, before any OCR: hold (no input) until two consecutive captures show the same grid, then
        record whether the content moved at all (``stalled``: identical to the pre-wheel capture, same thumb)."""
        pre = self.scroll_from
        if pre is None:
            return None
        self.since_scroll += 1
        bar = read_thumb(image, GRID_BAR_X, GRID_TRACK)
        stalled = kg.settled(pre["image"], image) and (bar or {}).get("top") == (pre["bar"] or {}).get("top")
        if self.since_scroll > 1 and kg.settled(self.last_image, image):
            self.scroll_from = None
            self.scrolled = {"pre": pre["image"], "pre_bar": pre["bar"], "stalled": stalled,
                             "direction": pre["direction"]}
            return None
        if self.since_scroll > GRID_SETTLE_HOLDS:
            self.scroll_from = None
            return self._stop("grid_not_settled", f"captures={self.since_scroll}")
        self.settle["grid_holds"] += 1
        return self._decide(Decision("hold", "grid_stall_check" if stalled and self.since_scroll == 1
                                     else "grid_settling"))

    def _pace(self, sent: collections.deque, spec) -> float:
        """Seconds to wait before the next wheel of one host action (0 = send now); ``sent`` holds clock() stamps."""
        count, window = spec
        now = self.clock()
        while sent and now - sent[0] >= window:
            sent.popleft()
        return window - (now - sent[0]) if len(sent) >= count else 0.0

    def _detail_pace(self) -> float:
        """Seconds to wait before the next detail wheel (0 = send now)."""
        return self._pace(self.detail_wheels, DETAIL_PACE)

    def _grid_pace_hold(self, direction) -> Decision | None:
        """A no-input hold while a grid wheel in ``direction`` would exceed the host limit (GRID_PACE), else None.
        The capture after the hold re-reads the same screen (still position) and decides again."""
        wait = self._pace(self.grid_wheels[direction], GRID_PACE)
        if wait <= 0:
            return None
        self.settle["grid_pace_holds"] += 1
        return self._decide(Decision("hold", "grid_pace", notes=(f"wait={wait:.1f}s",)))

    def _read(self, image) -> list:
        """OCR tokens of this capture. During a book's detail scroll the grid does not move: when the grid region
        matches the last full read (``grid_unchanged``), only the left panel is read and that read's grid tokens are
        reused (2026-10-23: full frame ~1.6 s, left panel ~0.5 s of a 4.55 s detail step). Otherwise the whole frame
        is read and becomes the new reference."""
        ref = self.grid_ref
        if self.phase in ("detail", "keep") and ref is not None and grid_unchanged(ref[0], image):
            self.partial_reads += 1
            return self._left(image) + ref[1]
        texts = kb.refine_list_pills(self.read_texts(image), image, self.read_texts)
        texts = kb.refine_list_values(texts, image, self.read_texts)
        grid = [t for t in kb._tokens(texts) if kb._center(t[1])[0] >= LEFT_PANEL_W]
        self.grid_ref = (_grid_region(image), grid)
        if self.phase in ("detail", "keep"):
            # A book's first page: read the panel exactly like its later pages (2026-10-24 book 414: the full-frame
            # read missed a row that the left-panel read finds).
            return self._left(image) + grid
        return texts

    def _left(self, image) -> list:
        return [t for t in kb._tokens(self.read_texts(image[:, :LEFT_PANEL_W])) if kb._center(t[1])[0] < LEFT_PANEL_W]

    def _evaluate(self, image, texts, pending) -> Decision:
        screen = kb.classify_knowhow_screen(texts, image)
        if self.phase in ("sort_open", "sort_pick"):
            return self._on_sort_popup(screen, image, pending)
        problem = self._list_problem(screen)
        if problem is not None:
            return self._stop(problem, f"after={pending.kind if pending else None}")
        if screen["owned"] is None or screen["direction"] is None or screen["sort"] is None:
            return self._hold("meta_unreadable")
        if self.owned is not None and (screen["owned"], screen["capacity"]) != (self.owned, self.capacity):
            return self._stop("owned_changed", f"{screen['owned']}/{screen['capacity']}")
        if screen["direction"] != "desc":
            return self._stop("direction_not_desc")       # the scan never toggles the direction
        self.owned, self.capacity = screen["owned"], screen["capacity"]
        if self.phase == "enter":
            return self._on_enter(screen, image, texts)
        if self.phase == "sort_apply":
            return self._on_sort_applied(screen, image, texts)
        if screen["sort"] != self.target_sort:
            return self._stop("sort_changed", screen["sort"])
        bar = self.cur_bar = read_thumb(image, GRID_BAR_X, GRID_TRACK)
        if self.phase == "seek_top":
            return self._on_seek_top(bar, image, texts)
        located = kg.locate_rows(texts, image)
        if not located["ok"]:
            return self._stop("grid_locate_failed", located["confidence"], *located["reasons"])
        tiles = kg.parse_tiles_at(texts, image, located, readable=True)
        if self.newest:
            tiles = {**tiles, "order_ok": dates_descending(tiles["tiles"])}
        extra = [] if self.newest else kb.reread_missing_values(tiles["tiles"], image, self.read_texts)
        if extra:
            texts = list(kb._tokens(texts)) + extra
            tiles = kg.parse_tiles_at(texts, image, located, readable=True)
        if tiles["order_ok"] is False and not self.newest:
            fixed = kb.reread_out_of_order(texts, tiles["tiles"], image, self.read_texts,
                                           descending=tiles.get("direction") != "asc")
            if fixed is not None:
                texts = fixed
                tiles = kg.parse_tiles_at(texts, image, located, readable=True)
        try:
            rows = _screen_rows(tiles["tiles"], located["offset_px"], self.sig_field)
        except ValueError as exc:
            return self._hold(f"tiles_unreadable:{exc}")
        if not rows:
            return self._stop("grid_rows_missing", located["offset_px"])
        if tiles["order_ok"] is False:
            return self._stop("grid_order_broken", "screen")
        if self.phase == "detail":
            return self._on_detail(rows, located["offset_px"], texts, image)
        if self.phase == "keep":
            return self._on_keep(rows, located["offset_px"], texts, image)
        return self._on_grid(rows, located["offset_px"], bar, image)

    # -- sort
    def _on_enter(self, screen, image, texts) -> Decision:
        self.current_sort = screen["sort"]
        self.orig_sort = (self.resume or {}).get("orig_sort") or screen["sort"]
        if self.options.topup != "off" and self.topup is None:
            n, reason = topup_plan(self.basis, screen["owned"])
            self.topup = {"mode": self.options.topup, "n": n, "reason": reason, "owned_now": screen["owned"],
                          **{k: self.basis.get(k) for k in ("baseline", "recent", "pending")}}
            self.log.append({"frame": self.frames, "phase": self.phase, "kind": "topup", **self.topup})
            if n and (self.options.topup == "only" or reason == "topup"):
                self._set_newest(n)
            elif self.options.topup == "only":
                return self._wind_down(reason)               # nothing to read: leave in the current sort
            else:
                self.topup["n"] = 0                          # prefer: no baseline top-up, the requested full scan
                self._set_newest(0)
        if self.orig_sort not in RESTORABLE_SORTS and self.orig_sort != self.target_sort:
            return self._stop("orig_sort_not_restorable", self.orig_sort)
        self.sort_purpose, self.sort_target = "scan", self.target_sort
        if screen["sort"] == self.target_sort:
            self.phase = "seek_top"
            return self._evaluate(image, texts, None)      # same capture, next phase: no input
        self.phase = "sort_open"
        reason = "switch_to_date" if self.newest else "switch_to_eval"
        decision = self._input(Decision("open_sort", reason, key=self.target_sort))
        return decision

    def _on_sort_popup(self, screen, image, pending) -> Decision:
        if screen["refused"]:
            return self._stop("destructive_marker")
        if screen["popup"] != "sort":
            return self._stop("sort_popup_missing", f"popup={screen['popup']}", f"after={pending.kind if pending else None}")
        selected = sort_popup_selection(image)
        if len(selected) != 1:
            return self._stop("sort_selection_unreadable", *selected)
        if selected[0] == self.sort_target:
            self.phase = "sort_apply"
            return self._input(Decision("apply_sort", "apply", key=self.sort_target))
        if self.phase == "sort_pick":
            return self._stop("sort_pick_failed", selected[0])
        if self.sort_target not in PICK_NODES:
            return self._stop("sort_target_not_bound", self.sort_target)
        self.phase = "sort_pick"
        return self._input(Decision("pick_sort", "pick", key=self.sort_target))

    def _on_sort_applied(self, screen, image, texts) -> Decision:
        if screen["sort"] != self.sort_target:
            return self._stop("sort_apply_failed", screen["sort"])
        self.current_sort = screen["sort"]
        if self.sort_purpose == "restore":
            self.phase = "leave"
            return self._decide(Decision("leave", self.soft_reason or "done"))
        self.phase = "seek_top"
        return self._evaluate(image, texts, None)

    # -- grid
    def _on_seek_top(self, bar, image, texts) -> Decision:
        scrolled, self.scrolled = self.scrolled, None
        if bar is None:
            return self._hold("grid_scrollbar_unreadable")
        if bar["at_top"]:
            self.phase, self.screen = "grid", None
            return self._evaluate(image, texts, None)       # same settled capture, read as the first screen
        if scrolled is not None and scrolled["stalled"]:
            return self._stop("grid_stalled_seek_top")
        if self.grid_scrolls >= self.options.max_grid_scrolls:
            return self._wind_down("grid_scroll_budget_exhausted")
        paced = self._grid_pace_hold("up")
        if paced is not None:
            return paced
        self.grid_scrolls += 1
        return self._input(Decision("scroll_grid", "seek_top", direction="up"))

    def _still_n(self, offset) -> int | None:
        """screen_n for a capture that must show the grid where it was (no wheel since): the same scroll position
        within GRID_STILL_TOL px (an offset may wrap by one pitch), else None."""
        delta = offset - self.screen_offset
        j = round(delta / kg.PITCH_Y)
        return None if abs(delta - j * kg.PITCH_Y) > GRID_STILL_TOL else self.screen_n + j

    def _row_map(self, rows, n: int) -> dict[int, tuple]:
        """Absolute row -> signatures for screen rows placed with ``screen_n`` = n."""
        return {row["m"] + n: row["sigs"] for row in rows}

    def _overlap(self, rows, n: int) -> int | None:
        """Rows of this screen already in the grid when placed at n: count, or None when any of them differs."""
        shared = 0
        for k, sigs in self._row_map(rows, n).items():
            known = [self.grid.get(k * COLS + c, _MISSING) for c in range(COLS)]
            if any(v is _MISSING for v in known):
                continue
            if tuple(known) != sigs:
                return None
            shared += 1
        return shared

    def _thumb_step(self, pre_bar, bar) -> float | None:
        """Content px implied by the grid thumb movement (track travel ~ (rows - 3) x pitch from 所持数). Coarse: one
        thumb px is ~37 px of content at 516 books (2026-10-12 live: estimate within 48 px of the located step)."""
        if not pre_bar or not bar or not self.owned:
            return None
        travel = (GRID_TRACK[1] - GRID_TRACK[0]) - (bar["bottom"] - bar["top"] + 1)
        content = max(0, -(-self.owned // COLS) - ROWS) * kg.PITCH_Y
        if travel <= 0 or content <= 0:
            return None
        return (bar["top"] - pre_bar["top"]) * content / travel

    def _place(self, rows, offset, image, scrolled, bar) -> tuple[int | None, Decision | None, dict]:
        """screen_n of a settled screen after a downward grid wheel. Candidates are the whole-row placements whose step
        (px, from the located offsets) is plausible; a candidate must agree tile by tile with every row already read
        (>= 1 shared row). Several agreeing candidates are separated only by a reliable pixel shift
        (knowhow_calibrate.vertical_shift, margin >= GRID_SHIFT_MARGIN), else the scan stops (ambiguous). The pixel
        shift never overrides the tiles: on the live grid (same-idol covers repeat) it reported 2 / 317 px for real
        159-160 px steps. The thumb gives an independent coarse check of the chosen step."""
        from agent.core import knowhow_calibrate as kc    # lazy: knowhow_calibrate imports this module

        prev = self.screen_n * kg.PITCH_Y - self.screen_offset
        cands = []
        for n in range(self.screen_n - 1, self.screen_n + 5):
            d = n * kg.PITCH_Y - offset - prev
            if -GRID_STEP_BACK_PX <= d <= GRID_VIEW_PX:
                cands.append((n, d))
        sig_ok = [(n, d) for n, d in cands if (self._overlap(rows, n) or 0) >= 1]
        shift = kc.vertical_shift(scrolled["pre"], image, kc.GRID_ROI, dy_range=(0, GRID_VIEW_PX - 40))
        px = None
        if shift["moved"] and shift["margin"] is not None and shift["margin"] >= GRID_SHIFT_MARGIN:
            near = [(n, d) for n, d in cands if abs(d - shift["dy"]) <= GRID_SHIFT_TOL]
            px = near[0] if len(near) == 1 else None
        thumb = self._thumb_step(scrolled.get("pre_bar"), bar)
        note = {"step": self.grid_steps, "frame": self.frames, "offset": offset, "dy_px": shift["dy"],
                "margin": shift["margin"], "pixel_n": None if px is None else px[0],
                "thumb_px": None if thumb is None else round(thumb, 1), "candidates": [n for n, _ in sig_ok]}
        if not sig_ok:
            return None, self._stop("grid_overlap_lost", f"step={self.grid_steps}", f"dy={shift['dy']}"), note
        if len(sig_ok) == 1:
            chosen, source = sig_ok[0], "tiles"
        elif px in sig_ok:
            chosen, source = px, "tiles+pixels"
        else:
            return None, self._stop("grid_overlap_ambiguous", *[n for n, _ in sig_ok]), note
        note.update(n=chosen[0], d_px=round(chosen[1], 1), source=source)
        if thumb is not None and abs(chosen[1] - thumb) > GRID_THUMB_TOL:
            return None, self._stop("grid_thumb_mismatch", f"step={self.grid_steps}", f"d={chosen[1]:.0f}",
                                    f"thumb={thumb:.0f}"), note
        return chosen[0], None, note

    def _integrate(self, rows, offset, bar, image) -> Decision | None:
        """Bind the settled screen to absolute rows (offset + whole-row placement proven by the tiles). Stop or None."""
        scrolled, self.scrolled = self.scrolled, None
        if self.screen is None:
            if abs(offset) > GRID_TOP_TOL:                 # thumb at the top: rows sit on the aligned lattice
                return self._stop("grid_top_misaligned", offset)
            n = 0
        elif scrolled is not None:
            self.grid_steps += 1
            if scrolled["stalled"]:
                n = self.screen_n
                self.grid_end = "scrollbar_at_end" if bar and bar["at_bottom"] else "content_stopped"
                self.step_log.append({"step": self.grid_steps, "frame": self.frames, "offset": offset, "n": n,
                                      "d_px": 0.0, "source": "stalled"})
            else:
                n, stopped, note = self._place(rows, offset, image, scrolled, bar)
                self.step_log.append(note)
                if stopped is not None:
                    return stopped
        else:
            n = self._still_n(offset)
            if n is None:
                return self._stop("grid_changed", f"offset {self.screen_offset}->{offset}")
        if self._overlap(rows, n) is None:
            return self._stop("grid_changed", f"n={n}")
        self.screen_n, self.screen_offset = n, offset
        self.screen = self._row_map(rows, n)
        self.screen_tiles = {}
        for row in rows:
            k = row["m"] + n
            for c, (sig, tile) in enumerate(zip(row["sigs"], row["tiles"])):
                index = k * COLS + c
                if sig is not None and any(self.grid.get(i, 0) is None for i in range(index)):
                    return self._stop("grid_layout_unexpected", f"index={index}")
                self.grid[index] = sig
                if sig is not None and tile.get("checked") is False and tile.get("marked") is not None:
                    self.marks[index] = tile["marked"]
                if row["complete"]:
                    self.screen_tiles[index] = tile
        values = [self.grid[i][0] for i in sorted(self.grid) if self.grid[i] is not None]
        if any(a < b for a, b in zip(values, values[1:])):
            return self._stop("grid_order_broken", "across_screens")
        if bar is not None and bar["at_bottom"] and not self.grid_end:
            self.grid_end = "scrollbar_at_end"
        return None

    def _on_grid(self, rows, offset, bar, image) -> Decision:
        stopped = self._integrate(rows, offset, bar, image)
        return stopped if stopped is not None else self._next_step()

    def _next_tile(self):
        """(tile, index) of the next tile of this screen (complete rows) whose book still has to be read, else None."""
        for index in sorted(self.screen_tiles):
            sig = self.grid.get(index)
            if sig is None or index in self.books:
                continue
            saved = self._resumed_book(index, sig)
            if saved is not None:
                self.books[index] = saved
                self.resumed.append(index)
                continue
            return self.screen_tiles[index], index
        return None

    def _next_step(self) -> Decision:
        """Open the next unread tile of the current screen, else scroll the grid, else wind down."""
        self.phase = "grid"
        if self.newest:
            return self._next_newest()
        nxt = self._next_tile() if self.options.details else None
        if nxt is not None and self.books_opened >= self.options.max_books:
            self.soft_reason = self.soft_reason or "book_budget_exhausted"   # the grid scan continues without details
            nxt = None
        if nxt is not None:
            tile, index = nxt
            if not tile.get("slot"):
                return self._stop("tile_slot_missing", f"index={index}", f"y={tile.get('y')}")
            decision = self._input(Decision("open_tile", "read_book", key=tile["slot"], row=tile["grid_row"],
                                            col=tile["col"], notes=(f"index={index}", f"y={tile['y']}")))
            if decision.kind == "open_tile":
                self.books_opened += 1
                self.phase = "detail"
                self.detail = {"index": index, "row": tile["grid_row"], "col": tile["col"], "slot": tile["slot"],
                               "tile": tile, "pages": [], "scrolls": 0, "thumb": None}
            return decision
        if self.grid_end:
            return self._wind_down(self.soft_reason or "grid_done")
        if self.grid_scrolls >= self.options.max_grid_scrolls:
            return self._wind_down("grid_scroll_budget_exhausted")
        paced = self._grid_pace_hold("down")
        if paced is not None:
            return paced
        decision = self._input(Decision("scroll_grid", "next_screen", direction="down"))
        if decision.kind == "scroll_grid":
            self.grid_scrolls += 1
        return decision

    def _next_newest(self) -> Decision:
        """Newest-books scan: open grid indices N-1..0 (the oldest new book first; complete rows of the top screen
        only), then wind down. An empty slot means the library holds fewer books; an index off the screen ends the run
        early. A baseline top-up first checks that tile N (the first book it does not read) is the newest book read
        before (``basis["anchor"]``): otherwise the count no longer tells which books are new."""
        if self.topup and self.topup["reason"] == "topup" and "anchor_ok" not in self.topup:
            anchor, sig = self.basis.get("anchor"), self.grid.get(self.newest)
            self.topup["anchor_ok"] = (None if anchor is None or sig is None
                                       else list(sig[:TOPUP_ANCHOR]) == list(anchor[:TOPUP_ANCHOR]))
            if self.topup["anchor_ok"] is False:
                self.log.append({"frame": self.frames, "phase": self.phase, "kind": "topup_anchor",
                                 "tile": list(sig), "anchor": list(anchor)})
                return self._wind_down("topup_misaligned")
        for index in reversed(range(self.newest)):
            if index in self.books:
                continue
            if index in self.grid and self.grid[index] is None:
                continue
            tile = self.screen_tiles.get(index)
            if tile is None:
                return self._wind_down("newest_beyond_screen")
            if not tile.get("slot"):
                return self._stop("tile_slot_missing", f"index={index}", f"y={tile.get('y')}")
            decision = self._input(Decision("open_tile", "read_new_book", key=tile["slot"], row=tile["grid_row"],
                                            col=tile["col"], notes=(f"index={index}", f"y={tile['y']}")))
            if decision.kind == "open_tile":
                self.books_opened += 1
                self.phase = "detail"
                self.detail = {"index": index, "row": tile["grid_row"], "col": tile["col"], "slot": tile["slot"],
                               "tile": tile, "pages": [], "scrolls": 0, "thumb": None}
            return decision
        return self._start_keep()

    # -- keep (lock / sell the newest books)
    def _start_keep(self) -> Decision:
        """Every newest book is read: plan keep / lock / sell and lock the planned books, else wind down."""
        self.soft_reason = self.soft_reason or "newest_done"
        if not self.options.keep or self.keep is not None:
            return self._wind_down("newest_done")
        self.keep = self._keep_plan()
        if not self.keep["queue"]:
            self.keep["outcome"] = self.keep["outcome"] or "done"
            return self._wind_down("newest_done")
        self.phase = "keep"
        return self._decide(Decision("hold", "keep_start"))     # the first keep check reads a fresh capture

    def _keep_plan(self) -> dict:
        """knowhow_keep.plan_new_books for the books read now, in acquisition order (oldest first), against a copy of
        the library with this run applied as the finish will apply it (so its ``sync`` decides whether to sell)."""
        o = self.options
        settings = kk.KeepSettings(sell=o.keep_sell, strategy=o.keep_strategy, lock=o.keep_lock)
        order = [i for i in reversed(range(self.newest)) if i in self.books]
        keep = {"settings": dataclasses.asdict(settings), "sell_executor": SELL_EXECUTOR, "verdicts": [],
                "counts_ok": None, "synced": None, "queue": [], "locked": [], "would_sell": [], "target": None,
                "step": None, "uncertain": None, "presses": 0, "outcome": None}
        if self.library is None:
            keep["outcome"] = "no_library"
            return keep
        library = copy.deepcopy(self.library)
        apply_to_inventory(self.result(), library, scanned_at="keep-plan")
        records = [kb.KnowhowBookRecord.from_dict(self.books[i]) for i in order]
        plan = kk.plan_new_books(records, library, self.wanted, settings, owned_now=self.owned, in_inventory=True,
                                 sync=library.sync)
        verdicts = [{**v, "grid_index": i} for v, i in zip(plan["verdicts"], order)]
        keep.update(verdicts=verdicts, counts_ok=plan["counts_ok"], synced=plan["synced"],
                    queue=[v["grid_index"] for v in verdicts if v["action"] == "lock"],
                    would_sell=[v["grid_index"] for v in verdicts if v["action"] == "sell"])
        self.log.append({"frame": self.frames, "phase": self.phase, "kind": "keep_plan", "lock": list(keep["queue"]),
                         "sell": list(keep["would_sell"]), "counts_ok": plan["counts_ok"], "synced": plan["synced"]})
        return keep

    def _keep_fail(self, reason, *notes) -> Decision:
        """Before a press the guarded wind-down restores the sort and leaves (the remaining books stay unlocked); with
        a press not yet confirmed the run stops where it is: that book's lock is uncertain and never pressed again."""
        self.keep["outcome"] = reason
        if self.keep["uncertain"] is not None:
            return self._stop(reason, *notes)
        self.log.append({"frame": self.frames, "phase": self.phase, "kind": "keep_problem", "reason": reason,
                         "notes": [str(n) for n in notes]})
        return self._wind_down("newest_done")

    def _keep_hold(self, reason) -> Decision:
        if self.holds >= self.options.max_holds:
            pressed = self.keep["step"] == "pressed"
            return self._keep_fail("keep_protect_unconfirmed" if pressed else "keep_detail_unreadable",
                                   f"index={self.keep['target']}", reason)
        return self._hold(reason)

    def _keep_input(self, decision: Decision, step: str) -> Decision:
        out = self._input(decision)
        if out.kind == decision.kind:
            self.keep["step"] = step
        else:                                       # input / time budget: wound down, the rest stays unlocked
            self.keep["outcome"] = "budget"
        return out

    def _keep_row_ok(self, r: int, sigs) -> bool:
        """The grid row as it was read, except that the book whose 保護 was just pressed may show its lock."""
        k = self.keep
        for c, (now, was) in enumerate(zip(sigs, self.screen[r])):
            if now == was:
                continue
            if (k["step"] == "pressed" and r * COLS + c == k["target"] and now is not None and was is not None
                    and now[:3] == was[:3] and now[3] is True and was[3] is False):
                continue
            return False
        return True

    def _on_keep(self, rows, offset, texts, image) -> Decision:
        k = self.keep
        n = self._still_n(offset)
        now = {} if n is None else self._row_map(rows, n)
        shared = set(now) & set(self.screen)
        if (n is None or (k["target"] is not None and k["target"] // COLS not in shared)
                or any(not self._keep_row_ok(r, now[r]) for r in shared)):
            return self._keep_fail("keep_grid_changed", f"index={k['target']}")
        checked = [(row["m"] + n) * COLS + c for row in rows if row["complete"]
                   for c, t in enumerate(row["tiles"]) if t.get("checked")]
        if k["target"] is None:
            if not k["queue"]:
                k["outcome"] = "done"
                return self._wind_down("newest_done")
            if len(checked) == 1 and checked[0] in k["queue"]:       # the book on the panel first: no tap
                k["queue"].remove(checked[0])
                k["queue"].insert(0, checked[0])
            k["target"], k["step"] = k["queue"].pop(0), "open"
            k.pop("reopened", None)
        target = k["target"]
        tile = self.screen_tiles.get(target)
        if k["step"] == "open":
            if checked != [target]:
                if tile is None or not tile.get("slot"):
                    return self._keep_fail("keep_tile_missing", f"index={target}")
                return self._keep_input(Decision("open_tile", "keep_open", key=tile["slot"], row=tile["grid_row"],
                                                 col=tile["col"], notes=(f"index={target}",)), "opened")
            k["step"] = "verify"
        if k["step"] == "opened":
            if not checked and not k.get("reopened") and any(kb.DETAIL_EMPTY in t for t, _, _ in kb._tokens(texts)):
                k["reopened"] = True                 # the tap never ran (see _on_detail): the same tap once more
                return self._keep_input(Decision("open_tile", "keep_reopen", key=tile["slot"], row=tile["grid_row"],
                                                 col=tile["col"], notes=(f"index={target}", "reopen")), "opened")
            k["step"] = "verify"
        if checked != [target]:
            return self._keep_fail("keep_binding_lost", f"index={target}", *checked)
        page = self._detail_page(texts, image)
        book = self.books[target]
        if page["empty"] or not page["title"]:
            return self._keep_hold("keep_detail_not_loaded")
        if not kb.same_book_title(book.get("title"), page["title"]) or any(
                (book.get("stats") or {}).get(s) not in (None, v) for s, v in page["stats"].items()):
            return self._keep_fail("keep_binding_lost", f"index={target}", page["title"])
        if page["knowhow_count"] is not None and page["knowhow_count"] != self.grid[target][2]:
            return self._keep_fail("keep_detail_mismatch", f"count {page['knowhow_count']} != {self.grid[target][2]}")
        lock = now[target // COLS][target % COLS][3]
        if k["step"] == "verify":
            if page["knowhow_count"] is None or page["protected"] is None:
                return self._keep_hold("keep_detail_unreadable")
            if page["protected"] or lock:
                return self._keep_fail("keep_state_changed", f"index={target}")   # locked since it was read
            out = self._keep_input(Decision("press_protect", "lock_book", notes=(f"index={target}",)), "pressed")
            if out.kind == "press_protect":
                k["presses"] += 1
                k["uncertain"] = target
            return out
        if page["protected"] is True and lock:        # pressed: 保護解除 on the panel and the lock on the tile
            r = target // COLS
            self.screen[r] = now[r]
            sig = self.grid[target] = now[r][target % COLS]
            self.screen_tiles[target] = {**tile, "protected": True} if tile else tile
            self.books[target] = {**book, "protected": True, "signature": list(sig)}
            k["locked"].append(target)
            k["target"] = k["step"] = k["uncertain"] = None
            self.holds = 0
            self.log.append({"frame": self.frames, "phase": self.phase, "kind": "locked", "index": target})
            return self._on_keep(rows, offset, texts, image)         # next book on this capture
        return self._keep_hold("keep_protect_pending")

    # -- detail
    def _on_detail(self, rows, offset, texts, image) -> Decision:
        d = self.detail
        n = self._still_n(offset)
        now = {} if n is None else self._row_map(rows, n)
        shared = set(now) & set(self.screen)
        if n is None or d["index"] // COLS not in shared or any(now[k] != self.screen[k] for k in shared):
            return self._stop("grid_changed_during_detail", f"index={d['index']}")
        checked = [(row["m"] + n) * COLS + c for row in rows if row["complete"]
                   for c, t in enumerate(row["tiles"]) if t.get("checked")]
        if not checked and not d["pages"] and not d.get("reopened") and any(kb.DETAIL_EMPTY in t for t, _, _ in kb._tokens(texts)):
            # 2026-10-12 live (book 7): the open tap never ran (stale-frame refusal after a slow first observation) and the
            # next capture differed only by background animation. Nothing is selected and the panel is still empty, so the
            # same tap is issued once more; a tile that is checked, or any other mismatch, still stops below.
            d["reopened"] = True
            return self._input(Decision("open_tile", "reopen_book", key=d["slot"], row=d["row"], col=d["col"],
                                        notes=(f"index={d['index']}", "reopen")))
        if checked != [d["index"]]:
            return self._stop("detail_binding_lost", *checked)
        page = self._detail_page(texts, image)
        tile = d["tile"]
        if page["empty"] or not page["title"]:
            return self._hold("detail_not_loaded")
        if page["knowhow_count"] is None:
            return self._hold("detail_count_unreadable")   # unread, not different: re-capture (max_holds bounds it)
        if page["knowhow_count"] != tile["knowhow_count"]:
            return self._stop("detail_mismatch", f"count {page['knowhow_count']} != tile {tile['knowhow_count']}")
        if page["protected"] is not None and page["protected"] != tile["protected"]:
            return self._stop("detail_mismatch", "protected")
        thumb = read_thumb(image, DETAIL_BAR_X, DETAIL_TRACK)
        if not d["pages"]:
            if thumb is not None and not thumb["at_top"]:
                return self._stop("detail_not_at_top", thumb["top"])
        else:
            first = d["pages"][0]
            if not kb.same_book_title(first["title"], page["title"]):
                return self._stop("detail_binding_lost", "title", first["title"], page["title"])
            if any(first["stats"].get(k) != v for k, v in page["stats"].items() if k in first["stats"]):
                return self._stop("detail_binding_lost", "stats", first["stats"], page["stats"])
            key = lambda p: [(r["name"], r["level"]) for r in p["rows"]]  # noqa: E731
            if key(page) == key(d["pages"][-1]) and (thumb or {}).get("top") == (d["thumb"] or {}).get("top"):
                return self._stop("detail_stalled", f"index={d['index']}")
        shift = None
        if d["pages"] and d.get("image") is not None:
            measured = self._detail_shift(d["image"], image)
            shift = measured["dy"] if measured["moved"] else None
        before = d["thumb"], d.get("image")
        d["pages"].append(page)
        d.setdefault("shifts", []).append(shift)
        d["thumb"], d["image"] = thumb, image
        # Pair rows by the measured shift when every step was measured; otherwise the name overlap rule applies.
        shifts = d["shifts"] if all(v is not None for v in d["shifts"][1:]) else None
        merged = kb.merge_detail_pages(d["pages"], shifts=shifts, vocabulary=self.vocabulary)
        if "merge.overlap_lost" in merged["unknowns"]:
            return self._stop("detail_overlap_lost", f"index={d['index']}")
        if "merge.book_conflict" in merged["unknowns"]:
            return self._stop("detail_binding_lost", "book_conflict")
        if merged["complete"]:
            self._store_book(merged)
            self.detail = None
            return self._next_step()
        if thumb is not None and thumb["at_bottom"]:
            return self._stop("detail_rows_missing", f"{len(merged['rows'])}/{merged['knowhow_count']}")
        if d["scrolls"] >= self.options.max_detail_scrolls:
            return self._stop("detail_scroll_budget_exhausted", f"index={d['index']}")
        wait = self._detail_pace()
        if wait > 0:
            # Host limit: this page is read again on a later capture (no input now).
            d["pages"].pop()
            d["shifts"].pop()
            d["thumb"], d["image"] = before
            self.settle["pace_holds"] += 1
            return self._decide(Decision("hold", "detail_pace", notes=(f"wait={wait:.1f}s",)))
        decision = self._input(Decision("scroll_detail", "next_rows", direction="down", notes=(f"index={d['index']}",)))
        if decision.kind == "scroll_detail":
            d["scrolls"] += 1
        return decision

    def _detail_page(self, texts, image) -> dict:
        """The left panel of this capture (knowhow_books.parse_detail_panel), with the count / stats read again from
        their own crops when the full read missed them."""
        refined = kb.refine_detail_tokens(texts, image, self.read_texts)
        page = kb.parse_detail_panel(refined, image)
        extra = kb.reread_detail_count(image, self.read_texts) if page["knowhow_count"] is None else []
        missing = [k for k in kb.STATS if k not in page["stats"]]
        if missing and page["title"]:
            extra += kb.reread_detail_stats(image, self.read_texts, missing)
        if extra:
            page = kb.parse_detail_panel(list(refined) + extra, image)
        return page

    def _store_book(self, merged) -> None:
        d = self.detail
        tile = d["tile"]
        book = kb.book_from_detail(merged, eval_value=tile.get("value"), marked=self.marks.get(d["index"]),
                                   frames=[f"knowhow-scan#{d['index']}"])
        unknowns = list(book.unknowns)
        if merged.get("complete") and sum(1 for r in book.rows if (r.level or 0) >= 5) != tile["lv5"]:
            unknowns.append("rows.lv5_mismatch")         # makes the record incomplete (p-) instead of trusting it
        book = dataclasses.replace(book, protected=tile["protected"], unknowns=tuple(dict.fromkeys(unknowns)))
        self.books[d["index"]] = {**book.to_dict(), "grid_index": d["index"], "signature": list(self.grid[d["index"]])}
        for row in merged.get("rows") or ():
            self.vocabulary.table.learn(row.get("name", ""), row.get("effect"))

    # -- result
    def result(self) -> dict:
        present = [i for i in sorted(self.grid) if self.grid[i] is not None]
        contiguous = present == list(range(len(present)))
        grid_complete = bool(self.grid_end and contiguous and self.owned is not None and len(present) == self.owned)
        read = [i for i in present if i in self.books and kb.KnowhowBookRecord.from_dict(self.books[i]).complete]
        details_complete = grid_complete and len(read) == len(present)
        carried_tiles, carried_books = ([], {}) if grid_complete else self._carried()
        books = {**carried_books, **self.books}
        tiles = [list(self.grid[i]) if self.grid[i] is not None else None for i in sorted(self.grid)]
        tiles += [list(t) if t is not None else None for t in carried_tiles]
        complete_carried = sum(1 for b in carried_books.values() if kb.KnowhowBookRecord.from_dict(b).complete)
        basis = (f"{self.target_sort}▼降順，{self.grid_steps} 次滚动均逐帧定位行偏移并以已读格对位衔接，读到 {len(present)} 格"
                 f"{'' if self.owned is None else f'，所持数 {self.owned}'}；结束：{self.grid_end or '未到末尾'}")
        return {
            "version": 1, "scan_id": self.scan_id, "mode": "newest" if self.newest or self.options.topup == "only" else "full",
            "newest": self.newest, "topup": None if self.topup is None else dict(self.topup),
            "partial": not details_complete, "grid_complete": grid_complete,
            "details_complete": details_complete,
            "outcome": None if self.final is None else self.final.reason,
            "stopped": self.final is not None and self.final.kind == "stop",
            "soft_reason": self.soft_reason, "recovered_from": self.recovered_from,
            "orig_sort": self.orig_sort, "current_sort": self.current_sort,
            "sort_restore_needed": self.orig_sort is not None and self.current_sort != self.orig_sort,
            "owned_total": self.owned, "capacity": self.capacity, "frames": self.frames, "inputs": self.inputs,
            "books_opened": self.books_opened, "books_complete": len(read) + complete_carried,
            "partial_reads": self.partial_reads,
            "settle": dict(self.settle),
            "resumed": list(self.resumed), "carried": len(carried_books),
            "keep": copy.deepcopy(self.keep),
            "grid": {"complete": grid_complete, "basis": basis,
                     "values": [self.grid[i][0] for i in present] + [t[0] for t in carried_tiles if t is not None],
                     "count": len(present) + len(carried_tiles), "steps": self.grid_steps, "end": self.grid_end,
                     "sort": self.target_sort, "step_log": list(self.step_log),
                     "direction": "desc",
                     "tiles": tiles,
                     "marks": {str(i): v for i, v in sorted(self.marks.items())}},
            "books": [books[i] for i in sorted(books)],
            "log": list(self.log),
        }


def apply_to_inventory(result: Mapping[str, Any], inv: kb.KnowhowInventory, *, scanned_at: str) -> dict:
    """Write a scan result into the private inventory: books per grid index (identical content at several indices →
    physical count), 所持数, and the grid coverage (``inv.grid``; ``complete`` only when proven). Returns a summary."""
    groups: dict[str, list[kb.KnowhowBookRecord]] = {}
    for raw in result.get("books") or ():
        book = kb.KnowhowBookRecord.from_dict({**raw, "first_seen": scanned_at, "last_seen": scanned_at})
        groups.setdefault(book.fingerprint, []).append(book)
    # A proven-complete grid whose every tile has its book in this result is the whole library, even when one book misses
    # a field (2026-10-24: book 419 without stats.me left the result partial and kept 9 stale records beside the 498).
    owned = result.get("owned_total")
    newest = result.get("mode") == "newest"
    topup = result.get("topup") or {}
    covered = result.get("grid_complete") is True and owned is not None and len(result.get("books") or ()) == owned
    full = not newest and (covered or (result.get("details_complete") is True and not result.get("partial")))
    if owned is not None:
        inv.owned_seen = {"owned": owned, "capacity": result.get("capacity"), "at": scanned_at}
    if newest and topup.get("reason") == "topup":
        return _apply_topup(result, inv, scanned_at=scanned_at)
    previous = inv.books
    if full:
        inv.books = {}  # a complete library snapshot replaces current holdings, not historical observations
        inv.recent, inv.sync = [], None   # the new baseline: 所持数 of this scan (library_basis)
    for fp, books in groups.items():
        old = previous.get(fp)
        merged = books[0] if old is None else kb._merge(old, books[0])
        count = len(books) if fp.startswith("b-") else 1
        if not full and old is not None:
            count = max(old.count, count)  # partial detail reads cannot establish that existing copies disappeared
        inv.books[fp] = dataclasses.replace(merged, count=count)
    if result.get("owned_total") is not None:
        inv.owned_total = result["owned_total"]
    if not newest:          # the newest-books scan sees only the top tiles in 獲得順: the 評価値 coverage stays as it was
        grid = result.get("grid") or {}
        inv.grid = kb.clean_grid({**grid, "owned_total": result.get("owned_total"), "scanned_at": scanned_at})
        inv.scanned_at = scanned_at
    reconciled = inv.reconcile_pending(candidates=list(groups) if newest else None)
    _note_sync(result, inv, owned, scanned_at)
    return {"books": sum(len(v) for v in groups.values()), "fingerprints": len(groups),
            "grid_complete": bool(inv.grid and inv.grid["complete"]), "reconciled_pending": len(reconciled),
            "partial": bool(result.get("partial")), "sync": inv.sync}


def _apply_topup(result: Mapping[str, Any], inv: kb.KnowhowInventory, *, scanned_at: str) -> dict:
    """Baseline top-up: every book read at grid index N-1..0 is a new copy (count + 1 on a known fingerprint). Only the
    contiguous run of complete books from index N-1 is kept and appended to ``inv.recent``; a book after a gap is left
    for the next top-up (it would otherwise be counted twice)."""
    n = int((result.get("topup") or {}).get("n") or 0)
    by_index = {raw.get("grid_index"): raw for raw in result.get("books") or ()}
    added: list[str] = []
    for index in reversed(range(n)):
        raw = by_index.get(index)
        book = None if raw is None else kb.KnowhowBookRecord.from_dict(
            {**raw, "first_seen": scanned_at, "last_seen": scanned_at})
        if book is None or not book.complete:
            break
        fp, old = book.fingerprint, inv.books.get(book.fingerprint)
        inv.books[fp] = (dataclasses.replace(book, count=1) if old is None
                         else dataclasses.replace(kb._merge(old, book), count=old.count + 1))
        sig = list(raw.get("signature") or [])[:TOPUP_ANCHOR]
        inv.recent.append({"fp": fp, "signature": sig if len(sig) == TOPUP_ANCHOR else None,
                           "date": sig[0] if sig and isinstance(sig[0], str) else None, "at": scanned_at})
        added.append(fp)
    inv.recent = kb.clean_recent(inv.recent)
    if result.get("owned_total") is not None:
        inv.owned_total = result["owned_total"]
    reconciled = inv.reconcile_pending(candidates=added)
    _note_sync(result, inv, result.get("owned_total"), scanned_at)
    return {"books": len(added), "fingerprints": len(set(added)),
            "grid_complete": bool(inv.grid and inv.grid["complete"]), "reconciled_pending": len(reconciled),
            "partial": len(added) < n, "topup_added": len(added), "sync": inv.sync}


def _note_sync(result: Mapping[str, Any], inv: kb.KnowhowInventory, owned, scanned_at: str) -> None:
    """After a top-up decision: 所持数 vs the inventory as the UI / sell executor read it; a misaligned top-up keeps
    ``needs_full_scan`` until a complete library scan sets a new baseline."""
    topup = result.get("topup")
    if not topup:
        return
    sync = library_sync(inv, owned)
    if result.get("soft_reason") == "topup_misaligned":
        sync["state"] = "needs_full_scan"
    inv.sync = kb.clean_sync({**sync, "reason": result.get("soft_reason") or topup.get("reason"), "at": scanned_at})


# ---- action-table entries (family knowhow-scan; tools/knowhow_scan_build.py writes them, staged under
# dev_assets/private/pending-20261012-knowhow/ until the table is switched) ----------
# Every rect comes from a native 2026-10-08 frame (live-capture-20261008-knowhow). The supervised 2026-10-08 capture
# (action-journal.json, Computer Use, not Pipeline) pressed the same buttons: idol-list-knowhow, knowhow-sort-open /
# -rating-select / -rating-apply / -restore-default-select / -restore-default-confirm, knowhow-detail-rating1020 (tile
# r0c0) and knowhow-back-to-idol-list. The tile is a persistent left detail panel: there is no close action.
# Wheel steps are UNVERIFIED: only scrollbar drags were recorded; the calibration mode measures them.
GRID_WHEEL = 60                       # smallest table delta; the idol list moved 154-356 px per -60
DETAIL_WHEEL = 60
UNVERIFIED_WHEEL = "wheel step UNVERIFIED: measured by the KnowhowScan calibration mode first"


def _tile_rect(row: int, col: int) -> list[int]:
    x, y, _, _ = kb.list_tile_box(row, col)
    return [x + 28, y + 30, 52, 40]       # cover centre: clear of マーク (top-left), ✓ (top-right) and lock (bottom-left)


ACTIONS: tuple[dict, ...] = (
    {"id": "idol-list-tab-knowhow", "rect": [34, 532, 136, 48], "nodes": ["SC.KnowhowScan.IdolList.OpenKnowhow"],
     "source": "native idol_list_support_top.png: left tab ノウハウブック x 19-184 y 519-594; pressed in the supervised "
               "2026-10-08 capture (action-journal idol-list-knowhow -> knowhow_default_grid.png)"},
    {"id": "knowhow-sort-open", "rect": [975, 108, 142, 34], "nodes": ["SC.KnowhowScan.List.OpenSort"],
     "source": "native knowhow_default_grid.png / knowhow_rating_grid.png: sort button x 963-1129 y 104-146 "
               "(2026-10-08 knowhow-sort-open -> knowhow_sort_options.png)"},
    {"id": "knowhow-sort-key-default", "rect": [390, 232, 130, 42], "nodes": ["SC.KnowhowScan.Sort.PickDefault"],
     "source": "native knowhow_sort_options.png: デフォルト x 373-538 y 220-286 (2026-10-08 restore-default-select)"},
    {"id": "knowhow-sort-key-eval", "rect": [758, 232, 130, 42], "nodes": ["SC.KnowhowScan.Sort.PickEval"],
     "source": "native knowhow_sort_options.png: ノウハウ評価値順 x 741-906 y 220-286 (2026-10-08 rating-select)"},
    {"id": "knowhow-sort-key-date", "rect": [574, 421, 130, 42], "nodes": ["SC.KnowhowScan.Sort.PickDate"],
     "source": "native knowhow_sort_options.png: 獲得順 x 557-722 y 409-475 (OCR 「獲得順」 [596,423,85,34]); the "
               "newest-books scan (ScanOptions.newest); not pressed live yet"},
    {"id": "knowhow-sort-apply", "rect": [578, 520, 124, 46], "nodes": ["SC.KnowhowScan.Sort.Apply"],
     "source": "native knowhow_sort_options.png: 決定 x 558-722 y 505-581, only under the 並び替え popup guard "
               "(2026-10-08 rating-apply -> knowhow_rating_grid.png, restore-default-confirm -> knowhow_restored_default.png)"},
    {"id": "knowhow-grid-scroll-down", "rect": [690, 322, 90, 12], "wheel": -GRID_WHEEL,
     "nodes": ["SC.KnowhowScan.Grid.ScrollDown"],
     "source": "native knowhow_rating_grid.png: gap between tile rows 1/2 (no button, no tile); " + UNVERIFIED_WHEEL},
    {"id": "knowhow-grid-scroll-up", "rect": [850, 322, 90, 12], "wheel": GRID_WHEEL, "nodes": ["SC.KnowhowScan.Grid.ScrollUp"],
     "source": "native knowhow_rating_grid.png: gap between tile rows 1/2 (no button, no tile); " + UNVERIFIED_WHEEL},
    {"id": "knowhow-detail-scroll-down", "rect": [60, 340, 200, 30], "wheel": -DETAIL_WHEEL,
     "nodes": ["SC.KnowhowScan.Detail.ScrollDown"],
     "source": "native knowhow_1020_detail_top.png: detail name/effect column, clear of the per-row buttons x 360-420 "
               "and of 保護/売却 below y 625 (wheel only, never a click); " + UNVERIFIED_WHEEL},
) + tuple(
    {"id": f"knowhow-tile-r{r}c{c}", "rect": _tile_rect(r, c), "nodes": [f"SC.KnowhowScan.Grid.OpenTile.R{r}C{c}"],
     "source": "native LIST_GRID tile cover centre (knowhow_books.LIST_GRID calibrated on 18 normal 一覧 frames, "
               "2026-10-08); selecting a tile only fills the left detail panel"
               + (" (r0c0 pressed in the 2026-10-08 capture: knowhow-detail-rating1020 -> knowhow_1020_detail_top.png)"
                  if (r, c) == (0, 0) else "")}
    for r in range(ROWS) for c in range(COLS)) + tuple(
    # drifting-grid tap slots (knowhow_grid.SLOT_ACTIONS, 6 y-slots x 5 columns): the scan opens a located tile with
    # knowhow_grid.slot_for(tile box); the fixed knowhow-tile-r*c* above stay for the calibration mode (top screen).
    {"id": a["id"], "rect": list(a["rect"]), "nodes": [slot_node(a["id"])], "source": a["source"]}
    for a in kg.SLOT_ACTIONS) + (
    {"id": "knowhow-filter-open", "rect": list(FILTER_BUTTON), "nodes": ["SC.KnowhowScan.Filter.Open"],
     "source": "native knowhow-sample-20261012 (user-operated, Maa read-only captures) #001: 「絞り込み」 button x 787-952 y 104-146 -> #117 popup (user tap)"},
    {"id": "knowhow-filter-scroll-down", "rect": list(FILTER_POPUP_WHEEL), "wheel": -FILTER_WHEEL,
     "nodes": ["SC.KnowhowScan.Filter.ScrollDown"],
     "source": "native knowhow-sample-20261012 (user-operated, Maa read-only captures) #117/#122/#131: popup content right margin (wheel only); " + UNVERIFIED_WHEEL},
    {"id": "knowhow-filter-scroll-up", "rect": list(FILTER_POPUP_WHEEL), "wheel": FILTER_WHEEL,
     "nodes": ["SC.KnowhowScan.Filter.ScrollUp"],
     "source": "native knowhow-sample-20261012 (user-operated, Maa read-only captures) #117/#122/#131: popup content right margin (wheel only); " + UNVERIFIED_WHEEL},
    {"id": "knowhow-filter-cancel", "rect": list(FILTER_CANCEL), "nodes": ["SC.KnowhowScan.Filter.Cancel"],
     "source": "native knowhow-sample-20261012 (user-operated, Maa read-only captures) #117/#122/#131: キャンセル x 380-545 y 588-663 (closes the popup without applying)"},
    # Jump probe (knowhow_calibrate grid_jumps / track_taps, default off): what a -240 notch and a track tap do.
    {"id": "knowhow-grid-jump-down", "rect": [690, 322, 90, 12], "wheel": -GRID_JUMP_WHEEL,
     "nodes": ["SC.KnowhowScan.Grid.JumpDown"],
     "source": "same gap as knowhow-grid-scroll-down (no button, no tile); -240 = four -60 notches; calibration probe "
               "only; " + UNVERIFIED_WHEEL},
    {"id": "knowhow-grid-jump-up", "rect": [850, 322, 90, 12], "wheel": GRID_JUMP_WHEEL,
     "nodes": ["SC.KnowhowScan.Grid.JumpUp"],
     "source": "same gap as knowhow-grid-scroll-up (no button, no tile); +240; calibration probe only; " + UNVERIFIED_WHEEL},
    {"id": "knowhow-grid-track-upper", "rect": list(TRACK_UPPER), "nodes": ["SC.KnowhowScan.Grid.TrackUpper"],
     "source": "native knowhow_rating_grid.png: grid scrollbar dark track x 1232-1239 (thumb x 1227-1245, y 187-228 "
               "at the top; nothing else at x >= 1220); tapped only while the thumb top is below y 210 (probe only)"},
    {"id": "knowhow-grid-track-lower", "rect": list(TRACK_LOWER), "nodes": ["SC.KnowhowScan.Grid.TrackLower"],
     "source": "native knowhow_rating_grid.png: grid scrollbar dark track x 1232-1239, track end y 655; tapped only "
               "while the thumb bottom is above y 606 (probe only)"},
)
# Keep step (ScanOptions.keep_lock): admitted only for a run with locking on (tools/run_tasks.knowhow_task_actions).
KEEP_ACTIONS: tuple[dict, ...] = (
    {"id": "knowhow-detail-protect", "rect": [146, 648, 112, 30], "nodes": ["SC.KnowhowScan.Keep.Protect"],
     "source": "native knowhow_1020_detail_top.png: detail 保護 button x 121-282 y 642-685 (knowhow_1010_protected_top.png: "
               "保護解除 in the same place; 売却 x 302-463 to its right); pressed only on the bound, unprotected book of the "
               "keep step; not pressed live yet (its first press is the sample)"},
)
ACTIONS = ACTIONS + KEEP_ACTIONS
# Existing table actions that list the KnowhowScan navigation nodes as well (same buttons as CardScan).
NODE_ADDITIONS = {
    "home-idol": ["SC.KnowhowScan.Home.OpenIdol"],
    "idol-portal-list": ["SC.KnowhowScan.Portal.OpenList"],
    "nav-back": ["SC.KnowhowScan.List.Back", "SC.KnowhowScan.IdolList.Back", "SC.KnowhowScan.Portal.Back"],
}
FAMILY = "knowhow-scan"
# Back-compat names of the 2026-10-08 proposal.
PROPOSED_ACTIONS, PROPOSED_NODE_ADDITIONS, PROPOSED_FAMILY = ACTIONS, NODE_ADDITIONS, FAMILY

__all__ = ["ScanOptions", "ScanSession", "Decision", "pipeline_node", "slot_node", "read_thumb", "sort_popup_selection",
           "tile_signature", "apply_to_inventory", "ACTIONS", "KEEP_ACTIONS", "NODE_ADDITIONS", "FAMILY", "PROPOSED_ACTIONS",
           "PROPOSED_NODE_ADDITIONS", "PROPOSED_FAMILY"]
