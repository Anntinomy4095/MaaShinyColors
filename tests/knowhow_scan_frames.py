"""Synthetic ノウハウブック一覧 frames and a fake game for tests/test_knowhow_scan.py (no Maa, no input).

Frames are 1280x720 RGB arrays carrying their OCR tokens (``Frame.texts``) so the K1 parsers of knowhow_books and the
pixel readers of knowhow_scan run unchanged. Geometry follows the calibrated constants (LIST_GRID, ROIs); the pixel
features are the minimum the calibrated thresholds need (pill grey, white card edge, yellow lock, pink ✓, scrollbars).
The grid can drift like the live one (``grid_step_px``: a wheel notch that is not a whole row, 2026-10-12 159-160 px vs a
157.5 px pitch): tiles are drawn at any pixel offset, clipped to the visible grid y 171..651, with the live pill
(knowhow_grid.PILL_RGB, 26 px at tile y+113) and a cover pattern derived from the tile signature (so identical tiles
look identical, as in the game). ``animate`` shows one intermediate position after each grid wheel (settle checks).
Taps are resolved by rectangle: a slot/tile rect that is not inside one visible tile cover selects nothing (``misses``).
"""
from __future__ import annotations

import dataclasses
import zlib

import numpy as np

from agent.core import knowhow_books as kb
from agent.core import knowhow_grid as kg
from agent.core import knowhow_scan as ks

WHITE, PINK, YELLOW, DARK = (246, 246, 246), (250, 70, 160), (235, 195, 40), (110, 100, 120)


class Frame(np.ndarray):
    texts: list = []

    def __array_finalize__(self, obj):
        self.texts = getattr(obj, "texts", [])


def frame(image, texts) -> Frame:
    out = np.ascontiguousarray(image).view(Frame)
    out.texts = list(texts)
    return out


def read_texts(image):
    return image.texts


@dataclasses.dataclass
class Book:
    value: int
    rows: list                     # [(name, level)]
    protected: bool = False
    marked: bool = False
    title: str = "【示例】偶像A"
    stats: tuple = (10, 20, 30, 40)
    acquired: str = "2026/01/01"   # 獲得順 overlay (YYYY/MM/DD); same day: the later list index is newer

    @property
    def lv5(self):
        return sum(1 for _, lv in self.rows if lv >= 5)


def library(n, *, seed=0, rows=5):
    rng = np.random.default_rng(seed)
    books = []
    for i in range(n):
        names = [f"条目{i}x{j}" for j in range(rows)]
        books.append(Book(value=1000 - 5 * i, rows=[(nm, int(rng.integers(1, 6))) for nm in names],
                          protected=bool(i % 7 == 3), marked=bool(i % 11 == 5), title=f"【示例{i}】偶像{i % 9}",
                          stats=(10 + i % 50, 20, 30, 40)))
    return books


def _fill(img, roi, color, clip=None):
    x, y, w, h = roi
    y0, y1 = max(0, y), y + h
    if clip is not None:
        y0, y1 = max(y0, clip[0]), min(y1, clip[1])
    if y1 > y0:
        img[y0:y1, max(0, x):x + w] = color


VIEW = (kg.VIEW_TOP, kg.VIEW_BOTTOM)


def _cover(img, x, y, book):
    """Signature-derived 40x40 block pattern on the cover (x+35..75, y+32..72), clear of every pixel ROI."""
    rng = np.random.default_rng(zlib.crc32(repr((book.value, book.lv5, len(book.rows), book.protected)).encode()))
    blocks = rng.integers(40, 220, (5, 5, 3), dtype=np.uint8)
    for by in range(5):
        for bx in range(5):
            _fill(img, (x + 35 + 8 * bx, y + 32 + 8 * by, 8, 8), tuple(int(v) for v in blocks[by, bx]), VIEW)


@dataclasses.dataclass
class Game:
    """State machine of the 一覧 page, driven by scan decisions (wheel steps are whole rows by default)."""
    books: list
    sort: str = "デフォルト"
    direction: str = "▼降順"
    popup: str | None = None
    popup_selected: str | None = None
    scroll_px: float = 0.0         # content scrolled from the top (px)
    selected: int | None = None
    detail_offset: int = 0
    grid_step: int = 1             # rows per grid wheel notch (when grid_step_px is None)
    grid_step_px: float | None = None   # px per grid wheel notch (live 159-160: the grid drifts off the lattice)
    animate: bool = False          # one intermediate frame after each grid wheel
    detail_step: int = 1           # detail rows per wheel notch
    mode: str = "normal"
    owned: int | None = None
    capacity: int = 600
    inputs: list = dataclasses.field(default_factory=list)
    taps: list = dataclasses.field(default_factory=list)      # (action id, grid position or None)
    misses: int = 0
    protect_effect: str = "lock"   # press_protect: lock the shown book | none (nothing changes) | popup (a dialog)
    presses: list = dataclasses.field(default_factory=list)    # book index shown at each 保護 press
    _anim: list = dataclasses.field(default_factory=list)

    @property
    def order(self):
        if self.sort == kb.SORT_EVAL:
            return sorted(range(len(self.books)), key=lambda i: -self.books[i].value)
        if self.sort == kb.SORT_DATE:
            return sorted(range(len(self.books)), key=lambda i: (self.books[i].acquired, i), reverse=True)
        return list(range(len(self.books)))[::-1]          # デフォルト: some other order

    @property
    def max_top(self):
        rows = -(-len(self.books) // ks.COLS)
        return max(0, rows - ks.ROWS)

    @property
    def max_scroll(self):
        return self.max_top * kg.PITCH_Y

    @property
    def top_row(self):
        return self.scroll_px / kg.PITCH_Y

    @top_row.setter
    def top_row(self, value):
        self.scroll_px = value * kg.PITCH_Y

    def tile_top(self, pos, scroll=None) -> float:
        return kg.GRID_Y + (pos // ks.COLS) * kg.PITCH_Y - (self.scroll_px if scroll is None else scroll)

    def tile_at(self, rect):
        """Grid position whose visible cover fully contains ``rect``, else None."""
        x, y, w, h = rect
        if y < kg.VIEW_TOP or y + h > kg.VIEW_BOTTOM:
            return None
        for pos in range(len(self.books)):
            tx, ty = kg.column_x(pos % ks.COLS), round(self.tile_top(pos))
            if tx <= x and x + w <= tx + kg.TILE and ty <= y and y + h <= ty + kg.TILE:
                return pos
        return None

    def apply(self, d: ks.Decision):
        self.inputs.append(d.kind)
        if d.kind == "open_sort":
            self.popup, self.popup_selected = "sort", self.sort
        elif d.kind == "pick_sort":
            self.popup_selected = d.key
        elif d.kind == "apply_sort":
            self.sort, self.popup, self.scroll_px, self.selected = self.popup_selected, None, 0.0, None
        elif d.kind == "scroll_grid":
            step = self.grid_step * kg.PITCH_Y if self.grid_step_px is None else self.grid_step_px
            old = self.scroll_px
            self.scroll_px = min(self.max_scroll, max(0.0, old + (step if d.direction == "down" else -step)))
            if self.animate and self.scroll_px != old:
                self._anim = [(old + self.scroll_px) / 2]
        elif d.kind == "open_tile":
            rect = kg.slot_rect(d.key) if d.key is not None else next(
                a["rect"] for a in ks.ACTIONS if a["id"] == f"knowhow-tile-r{d.row}c{d.col}")
            pos = self.tile_at(rect)
            self.taps.append((d.key or f"knowhow-tile-r{d.row}c{d.col}", pos))
            if pos is None or pos >= len(self.order):
                self.misses += 1                     # nothing under the tap: the selection stays as it was
                return
            self.selected = self.order[pos]
            self.detail_offset = 0
        elif d.kind == "scroll_detail":
            book = self.books[self.selected]
            self.detail_offset = min(max(0, len(book.rows) - 3), self.detail_offset + self.detail_step)
        elif d.kind == "press_protect":            # the detail panel's 保護 toggles the shown book
            self.presses.append(self.selected)
            if self.protect_effect == "popup":
                self.popup = "confirm"
            elif self.protect_effect == "lock" and self.selected is not None:
                book = self.books[self.selected]
                book.protected = not book.protected

    def render(self) -> Frame:
        img = np.full((720, 1280, 3), WHITE, dtype=np.uint8)
        texts = [("ノウハウブック一覧", (50, 20, 220, 30), 0.99)]
        for roi in kb.TOGGLE_INNER:
            _fill(img, roi, kb.TOGGLE_OFF_RGB)
        if self.mode == "protect":
            _fill(img, (536, 114, 30, 26), PINK)
        texts += [(self.sort, (975, 110, 140, 24), 0.99), (self.direction, (1150, 110, 70, 24), 0.99)]
        owned = len(self.books) if self.owned is None else self.owned
        texts.append((f"{owned}/{self.capacity}", (1150, 660, 90, 24), 0.99))
        order = self.order
        scroll = self._anim.pop(0) if self._anim else self.scroll_px

        def visible(box):
            return kg.VIEW_TOP <= box[1] and box[1] + box[3] <= kg.VIEW_BOTTOM

        for pos in range(len(order)):
            book = self.books[order[pos]]
            x, y = kg.column_x(pos % ks.COLS), round(self.tile_top(pos, scroll))
            if y + 140 <= kg.VIEW_TOP or y >= kg.VIEW_BOTTOM:
                continue
            _cover(img, x, y, book)
            _fill(img, (x - 6, y + kg.PILL_TOP, 120, kg.PILL_HEIGHT), kg.PILL_RGB, VIEW)
            for sx in range(x + 30, x + 100, 3):                              # pill text strokes
                _fill(img, (sx, y + kg.PILL_TOP + 6, 1, 15), (255, 255, 255), VIEW)
            if book.protected:
                _fill(img, (x + 3, y + 87, 16, 16), YELLOW, VIEW)
            if book.marked:
                _fill(img, (x + 6, y + 6, 20, 20), (40, 40, 40), VIEW)
            if order[pos] == self.selected:
                _fill(img, (x + 84, y + 2, 22, 22), PINK, VIEW)
                for rx, ry, rw, rh in kb.LIST_SELECTED_FRAME_ROIS:             # the shown book's pink frame
                    _fill(img, (x + rx, y + ry, rw, rh), PINK, VIEW)
            overlay = ((book.acquired, (x + 30, y + 86, 80, 22), 0.99) if self.sort == kb.SORT_DATE
                       else (str(book.value), (x + 60, y + 86, 44, 22), 0.99))
            for token in (overlay, (f"Lv.5↑x{book.lv5}/{len(book.rows)}", (x - 2, y + 114, 112, 22), 0.99)):
                if visible(token[1]):
                    texts.append(token)
        _fill(img, (ks.GRID_BAR_X - 3, ks.GRID_TRACK[0], 7, ks.GRID_TRACK[1] - ks.GRID_TRACK[0] + 1), DARK)
        span = ks.GRID_TRACK[1] - ks.GRID_TRACK[0] - 42
        top = ks.GRID_TRACK[0] + (0 if not self.max_scroll else round(span * scroll / self.max_scroll))
        _fill(img, (ks.GRID_BAR_X - 3, top, 7, 42), (252, 252, 252))
        if self.selected is None:
            texts.append((kb.DETAIL_EMPTY, (40, 330, 400, 30), 0.99))
        else:
            book = self.books[self.selected]
            texts.append((book.title, (150, 98, 300, 24), 0.99))
            for key, value in zip(kb.STATS, book.stats):
                bx, by, _, _ = kb.DETAIL_STAT_ROIS[key]
                texts.append((str(value), (bx + 20, by + 8, 40, 22), 0.99))
            texts.append((f"{len(book.rows)}/{len(book.rows)}", (378, 576, 70, 24), 0.99))
            texts.append(("保護解除" if book.protected else "保護", (150, 646, 100, 28), 0.99))
            for k, (name, level) in enumerate(book.rows[self.detail_offset:self.detail_offset + 3]):
                ly = 272 + 103 * k
                texts += [(name, (64, ly, 150, 22), 0.95), (f"Lv.{level}", (368, ly, 40, 22), 0.99),
                          (f"効果{name}", (32, ly + 30, 160, 18), 0.9)]
            if len(book.rows) > 3:
                _fill(img, (ks.DETAIL_BAR_X - 3, ks.DETAIL_TRACK[0], 7, ks.DETAIL_TRACK[1] - ks.DETAIL_TRACK[0] + 1), DARK)
                dspan = ks.DETAIL_TRACK[1] - ks.DETAIL_TRACK[0] - 33 - 2
                dmax = len(book.rows) - 3
                dtop = ks.DETAIL_TRACK[0] + 2 + round(dspan * self.detail_offset / dmax)
                _fill(img, (ks.DETAIL_BAR_X - 3, dtop, 7, 33), (252, 252, 252))
        if self.popup == "sort":
            img[:] = (img * 0.6).astype(np.uint8)
            texts = [("並び替え", (410, 130, 110, 30), 0.99)]
            for key, roi in ks.SORT_POPUP_KEYS.items():
                _fill(img, roi, (236, 236, 246))
                if key == self.popup_selected:
                    _fill(img, (roi[0] + 8, roi[1] + 8, roi[2] - 16, 8), PINK)
                texts.append((key, (roi[0] + 10, roi[1] + 20, roi[2] - 20, 26), 0.99))
            _fill(img, ks.SORT_APPLY, PINK)
            texts.append(("決定", (610, 530, 60, 30), 0.99))
        elif self.popup == "confirm":              # an unsampled dialog over the list (dimmed)
            img[:] = (img * 0.6).astype(np.uint8)
            texts = [("確認", (600, 200, 80, 30), 0.99), ("このノウハウブックを保護しますか？", (420, 330, 440, 30), 0.99),
                     ("キャンセル", (440, 520, 140, 34), 0.99), ("OK", (720, 520, 60, 34), 0.99)]
        return frame(img, texts)


def run(session: ks.ScanSession, game: Game, limit=2000, capture_s=2.5):
    """Closed loop: observe → apply input decisions to the fake game. Returns the decisions. The session clock
    advances ``capture_s`` per capture (detail wheel pacing)."""
    out = []
    now = [0.0]
    session.clock = lambda: now[0]
    for _ in range(limit):
        now[0] += capture_s
        d = session.observe(game.render())
        out.append(d)
        if d.kind in ("stop", "leave"):
            if d.kind == "leave":
                game.inputs.append("leave")
            return out
        if d.is_input:
            game.apply(d)
    raise AssertionError("scan did not end")
