"""Self-scrolling read-only scan of the home アイドル一覧 (P / S tabs) for the resources page (pure; no Maa import).

The user asked that Maa itself scrolls the S and P card lists so the resources tab can read every owned card
. This module is the decision core; ``card_scan_actions`` binds it to the ``SC.CardScan.*`` Pipeline
(all nodes ``enabled: false`` until the action-table entries exist and a supervised run passed).

Flow per tab (``tabs`` option, default support then produce)::

    tab not selected      -> tap_tab (once; a second failure stops)
    list not at the top   -> scroll up until the scrollbar thumb is at the top or the content stops moving
    scan                  -> read every tile on the frame, scroll down one step, measure the content shift
                             against the previous frame (>= one tile row of overlap required), repeat until the
                             scrollbar thumb is at the bottom or the content no longer moves
    next tab / leave      -> ``leave`` (the Pipeline taps back to the idol portal, then back home)

Every decision is bound to one frame and bounded: ``max_scrolls_per_tab``, ``max_frames``, ``max_holds`` (frames that
need a re-capture, e.g. tiles still loading). The scan never changes the sort or the filter: an active filter
(「絞り込みON」) stops the scan (cards would be hidden), any readable sort key/direction is accepted but must stay the
same for the whole tab (a change mid-scan stops). ``required_order`` can demand a specific order; a mismatch stops
instead of re-sorting (the 並び替え dialog has no action-table entries).

Geometry is measured on native 1280x720 frames of the home アイドル一覧 (third demo recording -02/-03 and
``owned-levels-20261007/idol-list-lv-001.png``):

- title OCR 「アイドル一覧」 at [44..48, 11..18, 158..164, 37..47] (OCR often reads 「アイドルー覧」);
- left tabs: selected label pink (RGB 230-255/40-140/110-200): プロデュースアイドル 967 px in (28,184,150,62),
  サポートアイドル 874 px in (28,270,150,62), every other tab 0 px; a modal dialog dims all to 0;
- 絞り込み [790..793,111,98..100,29..30] (「絞り込みON」 [772,111,144,32] when active), sort [997..1020,111..114,..]
  (デフォルト / Lv順 / 加入順), direction [1153..1157,110,76..80,33..34] (▼降順 / ▲昇順);
- grid: 7 columns x 138 px from x 267, rows 154 px, tiles 108 px, viewport y 170..680 (``card_inventory``);
- scrollbar: dark track (RGB ~119/110/122) at x 1239..1243, y 169..697; the thumb is a white gap ~43 px long
  (top position y 191 = list top, bottom edge y 675 = list end on -02 #48); while the list loads the whole track
  is white (-02 #19/#54).

The scroll step is not known yet (the recordings hold no wheel events and their frames are seconds apart, so
consecutive frames never overlap): the content shift is measured on every step. A down step outside
[154, 356] px (one row .. viewport - one row; only the step that reaches the list end may be shorter) stops the tab as
``overlap_lost`` (partial result, ``complete=False``). Each step's dy / overlap / thumb / at_bottom is kept in the tab
summary (``shifts``) for the supervised calibration run.
"""
from __future__ import annotations

import collections
import dataclasses
import hashlib
import re
import time
import unicodedata
from typing import Any, Callable, Iterable, Mapping

CANVAS = (1280, 720)
TITLE_ROI = (40, 8, 220, 52)
TITLE_PATTERN = re.compile(r"アイドル.?覧")
TAB_TEXT_ROI = {
    "fes": (28, 100, 150, 62), "produce": (28, 184, 150, 62), "support": (28, 270, 150, 62),
    "produce_room": (28, 356, 150, 62), "support_room": (28, 442, 150, 62), "knowhow": (28, 528, 150, 62),
}
SCAN_TABS = ("support", "produce")
TAB_SELECTED_MIN = 500
TAB_UNSELECTED_MAX = 40
PINK_LOWER = (230, 40, 110)
PINK_UPPER = (255, 140, 200)
FILTER_BOX = (760, 104, 176, 52)
SORT_BOX = (960, 104, 180, 52)
DIRECTION_BOX = (1144, 104, 104, 52)
SORT_KEYS = ("デフォルト", "Lv順", "特訓回数順", "加入順", "リリース順", "楽曲適性", "パフォーマンス適性", "環境適性")
DIRECTIONS = {"降順": "desc", "昇順": "asc"}
ARROWS = "▼▲▽△↓↑"
FILTER_OFF = "絞り込み"

SCROLLBAR_X = (1239, 1244)
TRACK_Y = (150, 700)
TRACK_ENDS = ((160, 180), (688, 702))
TRACK_DARK_MAX = 170          # mean V of the track; the thumb and the panel are ~255
THUMB_TOP_AT_TOP = 194        # measured 191 at the list top
THUMB_BOTTOM_AT_END = 672     # measured 675 at the list end
VIEWPORT = (170, 680)
STRIP_X = (267, 1203, 3)
ROW_PITCH = 154
MIN_OVERLAP = 154             # one full tile row must be visible before and after a step
VIEWPORT_HEIGHT = VIEWPORT[1] - VIEWPORT[0]
# Accepted displacement of one down step in the scan phase (calibration 9a): at least one
# row (a smaller notch is not guessed at; only the last step that reaches the end of the list may be shorter) and at
# most viewport - one row, so that a full row stays visible in both frames. ``content_shift`` never matches beyond it.
STEP_MIN_PX = ROW_PITCH
STEP_MAX_PX = VIEWPORT_HEIGHT - MIN_OVERLAP
SHIFT_MATCH_MAX = 12.0        # mean abs grey difference of the overlap (same frame ~0-1; live 2026-10-08 fading thumbnails up to ~8.5; unrelated ~22-35)
SHIFT_STILL_MAX = 2           # |dy| <= this is "did not move"
DEFAULT_MAX_SCROLLS = 30
DEFAULT_MAX_FRAMES = 150
DEFAULT_MAX_HOLDS = 4
# 2026-10-24: the wheel nodes wait 1 s instead of 5 s (that only respected the old host limit of 12 inputs per action and
# minute; it is 30 now). The list coasts after a wheel: a capture is read only once the content has not moved since the
# previous capture (best shift |dy| <= SHIFT_STILL_MAX within +-SETTLE_SEARCH px; fading thumbnails still match), with
# at most SETTLE_FRAMES_MAX captures per wheel (``settle`` decisions: no input, a short re-capture). The session also
# keeps the wheel under the host limit: at most PACE[0] wheels in PACE[1] s.
SETTLE_SEARCH = 12
SETTLE_FRAMES_MAX = 6
PACE = (28, 61.0)


def list_still(prev_strip, cur_strip) -> bool:
    """True when the viewport content did not move between two consecutive captures."""
    import numpy as np

    height = prev_strip.shape[0]
    best = (float("inf"), None)
    for dy in range(-SETTLE_SEARCH, SETTLE_SEARCH + 1):
        if dy >= 0:
            diff = float(np.abs(prev_strip[dy:] - cur_strip[:height - dy]).mean())
        else:
            diff = float(np.abs(prev_strip[:height + dy] - cur_strip[-dy:]).mean())
        if diff < best[0]:
            best = (diff, dy)
    return best[0] <= SHIFT_MATCH_MAX and abs(best[1]) <= SHIFT_STILL_MAX


def card_thumbnails_loading(image_rgb) -> bool:
    """Recognise the grey avatar placeholders, independently of card identity (native 2026-10-09 first page)."""
    import numpy as np

    rgb = _asarray(image_rgb)
    placeholders = 0
    for column in range(7):
        x = 267 + column * 138
        patch = rgb[201:261, x + 20:x + 88].astype(float)
        luminance = patch.mean(axis=2)
        grey = float((np.ptp(patch, axis=2) < 4).mean())
        avatar_bands = float(((abs(luminance - 195) < 10) | (abs(luminance - 240) < 10)).mean())
        if grey >= 0.98 and avatar_bands >= 0.85 and 10 < float(luminance.std()) < 30:
            placeholders += 1
    return placeholders >= 2


# ---- frame readers --------------------------------------------------------------------------------------------
def _asarray(image_rgb):
    import numpy as np

    image = np.asarray(image_rgb)
    if image.ndim != 3 or image.shape[2] < 3 or (image.shape[1], image.shape[0]) != CANVAS:
        raise ValueError(f"画面必须是 1280×720 RGB，实际 {getattr(image, 'shape', None)}")
    return image[:, :, :3]


def pink_count(image_rgb, roi) -> int:
    import numpy as np

    x, y, w, h = roi
    region = _asarray(image_rgb)[y:y + h, x:x + w].astype(int)
    return int(((region >= PINK_LOWER) & (region <= PINK_UPPER)).all(axis=2).sum())


def selected_tab(image_rgb) -> str | None:
    """The one left tab whose label is pink; None when none or several are (dialog, loading, other page)."""
    counts = {name: pink_count(image_rgb, roi) for name, roi in TAB_TEXT_ROI.items()}
    selected = [n for n, c in counts.items() if c >= TAB_SELECTED_MIN]
    if len(selected) != 1 or any(c > TAB_UNSELECTED_MAX for n, c in counts.items() if n != selected[0]):
        return None
    return selected[0]


@dataclasses.dataclass(frozen=True)
class Scrollbar:
    status: str                    # ok | loading | absent
    thumb_top: int | None = None
    thumb_bottom: int | None = None

    @property
    def at_top(self):
        return self.status == "ok" and self.thumb_top <= THUMB_TOP_AT_TOP

    @property
    def at_bottom(self):
        return self.status == "ok" and self.thumb_bottom >= THUMB_BOTTOM_AT_END

    def to_dict(self):
        return dataclasses.asdict(self) | {"at_top": self.at_top, "at_bottom": self.at_bottom}


def read_scrollbar(image_rgb) -> Scrollbar:
    import numpy as np

    image = _asarray(image_rgb)
    x0, x1 = SCROLLBAR_X
    y0, y1 = TRACK_Y
    column = image[y0:y1, x0:x1].astype(float).mean(axis=(1, 2))
    dark = np.nonzero(column < TRACK_DARK_MAX)[0]
    if len(dark) == 0:
        return Scrollbar("absent")
    top, bottom = int(dark.min()), int(dark.max())
    # the list's track spans y 169..697 (measured); anything else is another page or another list
    if not (TRACK_ENDS[0][0] <= top + y0 <= TRACK_ENDS[0][1] and TRACK_ENDS[1][0] <= bottom + y0 <= TRACK_ENDS[1][1]):
        return Scrollbar("absent")
    gaps = np.nonzero(column[top:bottom + 1] >= TRACK_DARK_MAX)[0]
    if len(gaps) == 0:
        return Scrollbar("absent")
    runs, start, prev = [], gaps[0], gaps[0]
    for g in gaps[1:]:
        if g != prev + 1:
            runs.append((start, prev))
            start = g
        prev = g
    runs.append((start, prev))
    s, e = max(runs, key=lambda r: r[1] - r[0])
    thumb_top, thumb_bottom = int(s + top + y0), int(e + top + y0)
    if thumb_bottom - thumb_top > 300:          # the whole track is white: the list is (re)loading
        return Scrollbar("loading", thumb_top, thumb_bottom)
    return Scrollbar("ok", thumb_top, thumb_bottom)


def content_strip(image_rgb):
    """Grey viewport strip used for the shift measurement (columns subsampled)."""
    import numpy as np

    image = _asarray(image_rgb).astype(np.float32)
    grey = image @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    x0, x1, step = STRIP_X
    return grey[VIEWPORT[0]:VIEWPORT[1], x0:x1:step]


def content_shift(prev_strip, cur_strip, direction: str) -> tuple[int | None, float]:
    """(dy, score): content moved up by ``dy`` px (down-scroll > 0, up-scroll < 0) with at least ``MIN_OVERLAP`` px of
    overlap, or (None, best score) when no shift in the scroll direction matches."""
    import numpy as np

    height = prev_strip.shape[0]
    limit = height - MIN_OVERLAP
    sign = 1 if direction == "down" else -1
    best = (float("inf"), None)
    for step in range(0, limit + 1):
        dy = sign * step
        if dy >= 0:
            diff = float(np.abs(prev_strip[dy:] - cur_strip[:height - dy]).mean())
        else:
            diff = float(np.abs(prev_strip[:height + dy] - cur_strip[-dy:]).mean())
        if diff < best[0]:
            best = (diff, dy)
    score, dy = best
    if score > SHIFT_MATCH_MAX:
        return None, round(score, 2)
    return dy, round(score, 2)


def _clean(text):
    if not isinstance(text, str):
        return None
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).strip(ARROWS)
    return re.sub(r"(?i)^lv", "Lv", text) or None


@dataclasses.dataclass(frozen=True)
class ListOrder:
    key: str            # one of SORT_KEYS (native text)
    direction: str      # desc | asc

    def to_dict(self):
        return dataclasses.asdict(self)


def parse_order(sort_text, direction_text, filter_text) -> tuple[ListOrder | None, list[str]]:
    """Any readable known sort; the filter must be the plain 「絞り込み」 label (「絞り込みON」 -> filter_active)."""
    reasons = []
    sort, direction, flt = _clean(sort_text), _clean(direction_text), _clean(filter_text)
    if flt is None:
        reasons.append("filter_unreadable")
    elif flt != FILTER_OFF:
        reasons.append("filter_active:" + flt)
    if sort is None:
        reasons.append("sort_unreadable")
    elif sort not in SORT_KEYS:
        reasons.append("sort_unknown:" + sort)
    if direction is None:
        reasons.append("direction_unreadable")
    elif direction not in DIRECTIONS:
        reasons.append("direction_unknown:" + direction)
    if reasons:
        return None, reasons
    return ListOrder(sort, DIRECTIONS[direction]), []


# ---- options / decisions ---------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class ScanOptions:
    tabs: tuple = SCAN_TABS
    max_scrolls_per_tab: int = DEFAULT_MAX_SCROLLS
    max_frames: int = DEFAULT_MAX_FRAMES
    max_holds: int = DEFAULT_MAX_HOLDS
    required_order: tuple | None = None      # e.g. ("Lv順", "desc"); None accepts any stable order

    @classmethod
    def from_params(cls, data: Mapping[str, Any] | None) -> "ScanOptions":
        data = dict(data or {})
        unknown = set(data) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"卡牌扫描选项未知: {sorted(unknown)}")
        if "tabs" in data:
            data["tabs"] = tuple(data["tabs"])
        if data.get("required_order") is not None:
            data["required_order"] = tuple(data["required_order"])
        options = cls(**data)
        if not options.tabs or len(set(options.tabs)) != len(options.tabs) or not set(options.tabs) <= set(SCAN_TABS):
            raise ValueError("tabs 须为 support/produce 的非空不重复列表")
        for name, high in (("max_scrolls_per_tab", 80), ("max_frames", 400), ("max_holds", 10)):
            value = getattr(options, name)
            if type(value) is not int or not 0 <= value <= high:
                raise ValueError(f"{name} 须为 0..{high} 的整数")
        if options.required_order is not None and (len(options.required_order) != 2
                                                   or options.required_order[0] not in SORT_KEYS
                                                   or options.required_order[1] not in DIRECTIONS.values()):
            raise ValueError("required_order 须为 (排序键, desc|asc)")
        return options


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                     # tap_tab | scroll | settle | hold | leave | stop
    reason: str
    tab: str | None = None
    direction: str | None = None
    notes: tuple = ()

    def to_dict(self):
        return dataclasses.asdict(self) | {"notes": list(self.notes)}


@dataclasses.dataclass
class TabState:
    name: str
    phase: str = "enter"          # enter | seek_top | scan | done
    order: ListOrder | None = None
    tapped: bool = False
    scrolls: int = 0
    frames: int = 0
    offset: int = 0               # content px scrolled since the scan phase started
    complete: bool = False
    end_reason: str | None = None
    slots: dict = dataclasses.field(default_factory=dict)   # (abs_row, col) -> best record status
    shifts: list = dataclasses.field(default_factory=list)

    def summary(self):
        accepted = sum(1 for s in self.slots.values() if s == "accepted")
        return {"tab": self.name, "phase": self.phase, "complete": self.complete, "end_reason": self.end_reason,
                "order": None if self.order is None else self.order.to_dict(), "frames": self.frames,
                "scrolls": self.scrolls, "content_px": self.offset, "slots": len(self.slots),
                "slots_accepted": accepted, "slots_unresolved": len(self.slots) - accepted,
                "shifts": list(self.shifts)}


TileReader = Callable[[Any, str], list]     # (pil_frame, frame_name) -> owned_levels.read_frame records
TextReader = Callable[[Any, tuple], str | None]   # (rgb array, roi) -> text or None


class ScanSession:
    """One scan run. Call ``observe(frame)`` once per *new* capture; it returns the next Decision. After a ``scroll``
    or ``tap_tab`` decision the caller performs exactly that input (or nothing on ``hold``) and captures again."""

    def __init__(self, options: ScanOptions, read_tiles: TileReader, read_text: TextReader):
        self.options = options
        self.read_tiles = read_tiles
        self.read_text = read_text
        self.tabs = [TabState(name) for name in options.tabs]
        self.index = 0
        self.records: list = []
        self.frames = 0
        self.holds = 0
        self.pending: tuple | None = None       # ("scroll", direction) | ("tap_tab", tab) sent before this capture
        self.prev_strip = None
        self.final: Decision | None = None
        self.log: list = []
        self.settle_prev = None                 # content strip of the last capture after a wheel (list_still)
        self.settle_frames = 0
        self.settled = True                     # the capture after the last wheel was proven still
        self.settle = {"still": 0, "holds": 0, "pace_holds": 0}
        self.wheels: collections.deque = collections.deque()   # clock() of each wheel (PACE)
        self.clock: Callable[[], float] = time.monotonic

    # -- helpers
    @property
    def tab(self) -> TabState | None:
        return self.tabs[self.index] if self.index < len(self.tabs) else None

    def _decide(self, decision: Decision, **note):
        self.log.append({"frame": self.frames, "tab": None if self.tab is None else self.tab.name,
                         **decision.to_dict(), **note})
        if decision.kind in ("stop", "leave"):
            self.final = decision
        if decision.kind in ("scroll", "tap_tab"):
            self.pending = (decision.kind, decision.direction or decision.tab)
        if decision.kind == "scroll":
            self.settled, self.settle_prev, self.settle_frames = False, None, 0
            self.wheels.append(self.clock())
        return decision

    def _settling(self, rgb):
        """After a wheel, before any OCR: a settle decision until the list is proven still, then the host pace."""
        if not self.settled:
            strip = content_strip(rgb)
            prev, self.settle_prev = self.settle_prev, strip
            self.settle_frames += 1
            if prev is None or prev.shape != strip.shape or not list_still(prev, strip):
                if self.settle_frames > SETTLE_FRAMES_MAX:
                    return self._decide(Decision("stop", "scroll_not_settled"))
                self.settle["holds"] += 1
                return self._decide(Decision("settle", "list_moving"))
            self.settled, self.settle_prev = True, None
            self.settle["still"] += 1
        count, window = PACE
        now, sent = self.clock(), self.wheels
        while sent and now - sent[0] >= window:
            sent.popleft()
        if len(sent) >= count:
            self.settle["pace_holds"] += 1
            return self._decide(Decision("settle", "host_pace", notes=(f"wait={window - (now - sent[0]):.1f}s",)))
        return None

    def _hold(self, reason):
        self.holds += 1
        if self.holds > self.options.max_holds:
            return self._decide(Decision("stop", "hold_budget_exhausted", notes=(reason,)))
        return self._decide(Decision("hold", reason))

    def _next_tab(self, end_reason):
        tab = self.tab
        tab.phase, tab.end_reason = "done", end_reason
        tab.complete = end_reason in ("scrollbar_at_end", "content_stopped")
        self.index += 1
        self.prev_strip = None
        if self.tab is None:
            return self._decide(Decision("leave", "all_tabs_scanned"))
        return None

    # -- main entry
    def observe(self, frame) -> Decision:
        """``frame``: 1280x720 PIL image of the list (RGB)."""
        import numpy as np

        if self.final is not None:
            return self.final
        self.frames += 1
        if self.frames > self.options.max_frames:
            return self._decide(Decision("stop", "frame_budget_exhausted"))
        rgb = np.asarray(frame.convert("RGB"))
        pending = self.pending     # retain the last movement while a loading frame asks for re-observation
        waiting = self._settling(rgb)
        if waiting is not None:
            return waiting
        title = self.read_text(rgb, TITLE_ROI)
        if not title or not TITLE_PATTERN.search(title):
            return self._hold("title_not_read")
        current_tab = selected_tab(rgb)
        bar = read_scrollbar(rgb)
        if current_tab is None or bar.status == "loading":
            return self._hold("list_loading" if bar.status == "loading" else "tab_unreadable")
        tab = self.tab
        if current_tab != tab.name:
            if tab.tapped or pending is not None and pending[0] == "scroll":
                return self._decide(Decision("stop", "tab_switch_failed", notes=(current_tab,)))
            tab.tapped = True
            self.prev_strip = None
            return self._decide(Decision("tap_tab", "tab_not_selected", tab=tab.name))
        if card_thumbnails_loading(rgb):
            return self._hold("card_thumbnails_loading")
        order, reasons = parse_order(self.read_text(rgb, SORT_BOX), self.read_text(rgb, DIRECTION_BOX),
                                     self.read_text(rgb, FILTER_BOX))
        if any(r.startswith("filter_active") for r in reasons):
            return self._decide(Decision("stop", "list_filter_active", notes=tuple(reasons)))
        if order is None:
            if all(r.endswith("_unreadable") for r in reasons):
                return self._hold("order_unreadable")
            return self._decide(Decision("stop", "list_sort_unknown", notes=tuple(reasons)))
        if tab.order is None:
            tab.order = order
            required = self.options.required_order
            if required is not None and (order.key, order.direction) != tuple(required):
                return self._decide(Decision("stop", "list_order_mismatch",
                                             notes=(f"{order.key}/{order.direction}", f"want {required[0]}/{required[1]}")))
        elif order != tab.order:
            return self._decide(Decision("stop", "list_order_changed", notes=(f"{tab.order.key}->{order.key}",)))
        if bar.status == "absent":
            return self._hold("scrollbar_unreadable")
        self.pending = None
        self.holds = 0
        tab.frames += 1
        strip = content_strip(rgb)
        moved = None
        if pending is not None and pending[0] == "scroll" and self.prev_strip is not None:
            dy, score = content_shift(self.prev_strip, strip, pending[1])
            tab.shifts.append({"frame": self.frames, "direction": pending[1], "phase": tab.phase, "dy": dy,
                               "overlap_px": None if dy is None else VIEWPORT_HEIGHT - abs(dy), "score": score,
                               "thumb_top": bar.thumb_top, "thumb_bottom": bar.thumb_bottom,
                               "at_top": bar.at_top, "at_bottom": bar.at_bottom})
            if dy is None and tab.phase == "scan":
                # A step without one full row of overlap could have skipped cards: the tab is not complete.
                self.prev_strip = strip
                tab.end_reason = "overlap_lost"
                return self._decide(Decision("stop", "overlap_lost", notes=(f"score={score}",)))
            if (tab.phase == "scan" and pending[1] == "down" and SHIFT_STILL_MAX < dy < STEP_MIN_PX
                    and not bar.at_bottom):
                # Shorter than one row without reaching the end: the notch is not what was calibrated; do not guess.
                self.prev_strip = strip
                tab.end_reason = "overlap_lost"
                return self._decide(Decision("stop", "overlap_lost", notes=(f"step_below_min:{dy}<{STEP_MIN_PX}",
                                                                            f"score={score}")))
            moved = dy
        self.prev_strip = strip
        if tab.phase == "enter":
            tab.phase = "seek_top" if not bar.at_top else "scan"
        if tab.phase == "seek_top":
            if bar.at_top or (moved is not None and abs(moved) <= SHIFT_STILL_MAX):
                tab.phase, tab.offset = "scan", 0
            else:
                return self._scroll(tab, "up")
        # scan phase: account the step, read the tiles, decide the next step
        if moved is not None and pending[1] == "down":
            if abs(moved) <= SHIFT_STILL_MAX:
                decision = self._next_tab("content_stopped")
                return decision or self._continue_next_tab()
            tab.offset += moved
        self._read(frame, tab)
        if bar.at_bottom:
            decision = self._next_tab("scrollbar_at_end")
            return decision or self._continue_next_tab()
        return self._scroll(tab, "down")

    def _continue_next_tab(self):
        # The new tab is not selected on this frame (the scanned tab still is): tap it now.
        tab = self.tab
        tab.tapped = True
        return self._decide(Decision("tap_tab", "next_tab", tab=tab.name))

    def _scroll(self, tab, direction):
        if tab.scrolls >= self.options.max_scrolls_per_tab:
            tab.end_reason = "scroll_budget_exhausted"
            return self._decide(Decision("stop", "scroll_budget_exhausted", notes=(tab.name,)))
        tab.scrolls += 1
        return self._decide(Decision("scroll", "scan" if direction == "down" else "seek_top", direction=direction))

    def _read(self, frame, tab):
        name = f"{tab.name}#{tab.frames}"
        for record in self.read_tiles(frame, name):
            record = dict(record, tab=tab.name)
            col = int(record["slot"].split("c")[1]) - 1
            abs_row = round((tab.offset + record["box"][1] - VIEWPORT[0]) / ROW_PITCH)
            key = (abs_row, col)
            record["abs_slot"] = list(key)
            if tab.slots.get(key) != "accepted":
                tab.slots[key] = record["status"]
            self.records.append(record)

    # -- result
    def result(self) -> dict:
        from agent.core import owned_levels as ol

        merged = ol.merge_readings(self.records)
        complete = all(t.complete for t in self.tabs)
        return {"version": 1, "complete": complete,
                "outcome": None if self.final is None else self.final.reason,
                "tabs": [t.summary() for t in self.tabs], "frames": self.frames, "settle": dict(self.settle),
                "cards": merged["cards"], "conflicts": merged["conflicts"], "rejected_tiles": merged["rejected"],
                "details": ol.details_for_import(merged), "log": list(self.log)}


def import_payloads(result: Mapping[str, Any], generated_at: str) -> dict:
    """Reviewed-import payloads (never the profile): ``owned_levels`` (owned-levels.js shape) and ``inventory``
    (inventory-scan.js shape). Both carry ``complete`` so the UI can say whether the whole list was covered."""
    cards = result["cards"]
    note = ("Self-scrolling アイドル一覧 scan (SC.CardScan); suggestions for review only. complete=false means a tab "
            "stopped before the end of its list.")
    owned = {"version": 1, "generated_by": "agent/core/card_list_scan.py", "generated_at": generated_at, "note": note,
             "complete": result["complete"], "tabs": result["tabs"], "frames": [],
             "cards": cards, "conflicts": result["conflicts"], "rejected_tiles": result["rejected_tiles"],
             "rejected_reasons": {}, "details": result["details"]}
    inventory = {"version": 1, "generated_by": "agent/core/card_list_scan.py", "generated_at": generated_at,
                 "layout": "idol_list", "complete": result["complete"], "frames": [],
                 "cards": [{"id": cid, "score": None, "verify": card.get("confidence"), "confidence": card.get("confidence"),
                            "frames": card.get("frames", 1)}
                           for cid, card in sorted(cards.items())],
                 "rejected_tiles": result["rejected_tiles"]}
    return {"owned_levels": owned, "inventory": inventory}


def frame_digest(image_rgb) -> str:
    import numpy as np

    return hashlib.sha256(np.ascontiguousarray(_asarray(image_rgb)).tobytes()).hexdigest()
