"""Read-only Skip list geometry and overlapping-page coverage, never player-progress or cycle proof.

Native rows move continuously with the wheel and may have blue or red headers.
Keep their actual coordinates; fixed action slots cannot be inferred from these rows.
"""
from __future__ import annotations

import dataclasses
import math

from agent.core import match_task as mt, task_common as tc

LIST_TOP, LIST_BOTTOM = 188, 562
GUARD = "SC.Nav.MatchSkip.Guard"


@dataclasses.dataclass(frozen=True)
class SkipRow:
    stage: tuple[int, int] | None
    count: int | None
    plus_enabled: bool
    header_box: tuple[int, int, int, int]
    plus_box: tuple[int, int, int, int]


@dataclasses.dataclass(frozen=True)
class SkipPage:
    frame_digest: str
    observed_at: float
    rows: tuple[SkipRow, ...]
    cost: tuple[int, int] | None
    thumb: tuple[int, int] | None

    @property
    def readable(self):
        return (bool(self.rows) and self.cost is not None and self.cost[1] <= mt.RIGHTS_MAXIMUM
                and self.thumb is not None
                and all(row.stage is not None and row.count is not None for row in self.rows))

    def at_top(self):
        return self.thumb is not None and self.thumb[0] <= mt.SCROLLBAR_TOP + 6

    def at_bottom(self):
        return self.thumb is not None and self.thumb[1] >= mt.SCROLLBAR_BOTTOM - 6

    def to_dict(self):
        return {**dataclasses.asdict(self), "scope": "skip_list", "input_authorized": False,
                "stage_list_complete": False, "latest_stage": None, "cycle_id": None}


def _header_spans(image, x):
    """Solid interior column of a native blue/red header; exclude the orange reward band."""
    column = image[LIST_TOP:LIST_BOTTOM + 1, x].astype(int)
    b, g, r = column.T
    colored = ((b >= 200) & (r <= 120) & (g >= 100) & (g <= 210)) | (
        (r >= 200) & (g <= 130) & (b >= 40) & (b <= 150))
    spans = []
    for offset, hit in enumerate(colored):
        if hit:
            y = LIST_TOP + offset
            if not spans or spans[-1][1] + 1 != y:
                spans.append([y, y])
            else:
                spans[-1][1] = y
    return [(top, bottom) for top, bottom in spans if 28 <= bottom - top + 1 <= 40]


COST_SLASH = (1120, 1140)        # the slash glyph of 「N / M」 sits at x 1124..1136 in every native sample
COST_WIDE = (1050, 614, 170, 38)  # fallback window inside the light 消費数 panel: room for centered 2-digit N


def read_cost(vision, image, strict_box=None):
    """「参加権消費数 N / M」 -> (N, M) or None. Does not click.

    The full-box det+rec read scores ~0.98-0.99 for "0 / 19|20" but only ~0.94 for "0 / 1" and below 0.90 for
    some counts ("8 / 20", live real4 2026-10-13; same layout and color). Fallback: the slash must sit at its fixed
    columns with digits only on either side; each side must read unanimously (4 preprocessings, best >= 0.95) as
    digits, and the independent full-box det+rec text must spell exactly the same "N/M" at any score.
    The スキップ確認 review dims the same field (text ~46, panel ~121; "20 / 20" scored 0.905, live real5), so glyph
    columns use the window's own mid-contrast instead of a fixed level. ``strict_box``: the screen's strict box.
    """
    cost = tc.ratio(vision.text(image, strict_box or mt.COST_FIELD))
    if cost is not None:
        return cost
    x, y, w, h = COST_WIDE
    gray = image[y:y + h, x:x + w].astype(int).mean(axis=2)
    if gray.max() - gray.min() < 40:
        return None
    dark = gray < (gray.max() + gray.min()) / 2
    columns = [x + i for i in range(w) if dark[:, i].any()]
    left, right = COST_SLASH
    slash = [c for c in columns if left <= c < right]
    if not slash or not any(c < left for c in columns) or not any(c >= right for c in columns):
        return None
    # One solid slash run with blank columns on both sides: no digit merged into or reaching the slash zone.
    if slash[-1] - slash[0] + 1 != len(slash) or slash[0] == left or slash[-1] == right - 1:
        return None
    current = vision.number(image, (x, y, left - x, h), "dark", r"[0-9]{1,2}", unanimous=True)
    maximum = vision.number(image, (right, y, x + w - right, h), "dark", r"[0-9]{1,2}", unanimous=True)
    if current is None or maximum is None:
        return None
    text = f"{int(current)}/{int(maximum)}"
    # The dimmed review's wider crop can drop the slash ("1/1" -> "11").
    # Cross-check with the review's own fixed field when it was supplied. That field may read the verified slash
    # as "1" ("9 / 9" -> "919" at 0.51, live 2026-10-31); the review also binds the cost to the Match main rights.
    full = vision.text(image, strict_box or COST_WIDE, threshold=0.0)
    return tc.ratio(text) if full in (text, f"{int(current)}1{int(maximum)}") else None


ROW_BAND = 20                    # same visual row: header tops of the two columns differ by a few px (pitch ~127)


def _screen_order(rows):
    """Left-to-right within a visual row, rows top to bottom.

    Live 2026-10-13: the right header top was detected 1 px above the left one, so a plain (y, x) sort put A4-S10
    before A3-S9 and the overlap check refused the page.
    """
    bands = []
    for row in sorted(rows, key=lambda row: row.header_box[1]):
        if bands and row.header_box[1] - bands[-1][0].header_box[1] <= ROW_BAND:
            bands[-1].append(row)
        else:
            bands.append([row])
    return [row for band in bands for row in sorted(band, key=lambda row: row.header_box[0])]


def read_skip_page(vision, image, observed_at):
    """Require the full native dialog guard before reading moving rows. Does not click."""
    if getattr(image, "shape", None) != (720, 1280, 3) or not vision.hit(GUARD, image):
        return None
    tc.require(type(observed_at) in (float, int) and math.isfinite(observed_at), "Match 采样时间无效")
    rows = []
    for column in (0, 1):
        for top, bottom in _header_spans(image, 400 + 566 * column):
            # Count/plus must be wholly visible. A cut-off edge row is read on an adjacent page.
            count_box = (462 + 566 * column, bottom + 24, 90, 28)
            if count_box[1] + count_box[3] > LIST_BOTTOM:
                continue
            header = (70 + 566 * column, top + 3, 300, 28)
            plus = (575 + 565 * column, bottom + 33, 20, 16)
            stage = mt.parse_header(vision.number(image, header, "light", mt.HEADER_PATTERN.pattern))
            count = tc.to_int(vision.number(image, count_box, "light", r"[0-9]{1,2}", unanimous=True))
            x, y, w, h = plus
            patch = image[y:y + h, x:x + w].astype(int)
            pink = (patch[:, :, 2] >= 200) & (patch[:, :, 1] <= 90) & (patch[:, :, 0] >= 100)
            rows.append(SkipRow(stage, count, int(pink.sum()) >= 25, header, plus))
    rows = _screen_order(rows)
    column = image[mt.SCROLLBAR_TOP:mt.SCROLLBAR_BOTTOM + 1, mt.SCROLLBAR_X].astype(int)
    white = [i for i, pixel in enumerate(column) if min(pixel) >= 245]
    thumb = (mt.SCROLLBAR_TOP + white[0], mt.SCROLLBAR_TOP + white[-1]) if white else None
    return SkipPage(tc.image_digest(image), observed_at, tuple(rows),
                    read_cost(vision, image), thumb)


def covered_view(page, covered):
    """The page without its leading unreadable rows when they come before a readable row the scan already holds.

    Pages are read top-down with overlap, so the covered stages are a contiguous prefix of the list and anything shown
    above a covered row is covered too. Live 2026-10-28: after a half-notch wheel the top-left header (A2-S5, read on
    the page before) came back empty on every settled frame. Only the coverage scan may use this view: the ＋ targets
    are row indices of the full page, so pressing still needs every row readable.
    """
    if page is None or page.readable or not covered or page.cost is None or page.thumb is None:
        return page
    rows = page.rows
    first = next((i for i, row in enumerate(rows) if row.stage is not None and row.count is not None), None)
    if not first or rows[first].stage not in covered:
        return page
    if any(row.stage is None or row.count is None for row in rows[first:]):
        return page
    return dataclasses.replace(page, rows=rows[first:])


class SkipListScan:
    """Collect complete *skippable-list* coverage from ordered overlapping frames.

    It is not MatchProgress: unavailable or open-but-not-skippable stages are absent
    from this list, and no frame here supplies an independent reset-cycle identity.
    """
    def __init__(self):
        self.pages = []
        self.stages = []
        self.complete = False
        self.reason = None

    def add(self, page):
        if self.reason or self.complete:
            return False
        if isinstance(page, SkipPage) and self.pages:
            page = covered_view(page, self.stages)
        if not isinstance(page, SkipPage) or not page.readable:
            self.reason = "dialog_unreadable"
            return False
        if page.cost[0] != 0 or any(row.count != 0 for row in page.rows):
            self.reason = "counts_not_zero"
            return False
        stages = [row.stage for row in page.rows]
        if len(stages) != len(set(stages)):
            self.reason = "duplicate_stage_rows"
            return False
        if not self.pages:
            if not page.at_top():
                self.reason = "list_start_not_observed"
                return False
        else:
            previous = self.pages[-1]
            if page.observed_at <= previous.observed_at:
                self.reason = "frame_order_invalid"
                return False
            if page.cost[1] != previous.cost[1]:
                self.reason = "rights_changed"
                return False
            if page.thumb[0] < previous.thumb[0] or page.thumb[1] < previous.thumb[1]:
                self.reason = "scroll_direction_changed"
                return False
            overlap = [stage for stage in stages if stage in self.stages]
            if not overlap or stages[:len(overlap)] != overlap or self.stages[-len(overlap):] != overlap:
                self.reason = "page_overlap_missing_or_changed"
                return False
            old = {row.stage: row.plus_enabled for old_page in self.pages for row in old_page.rows}
            if any(row.stage in old and old[row.stage] != row.plus_enabled for row in page.rows):
                self.reason = "stage_state_changed"
                return False
        for stage in stages:
            if stage not in self.stages:
                self.stages.append(stage)
        self.pages.append(page)
        self.complete = page.at_bottom()
        return True

    def to_dict(self):
        return {"scope": "skip_list", "skip_list_complete": self.complete and self.reason is None,
                "stage_list_complete": False, "latest_stage": None, "cycle_id": None,
                "input_authorized": False, "reason": self.reason,
                "stages": [f"A{area}-S{stage}" for area, stage in self.stages],
                "pages": [page.to_dict() for page in self.pages]}
