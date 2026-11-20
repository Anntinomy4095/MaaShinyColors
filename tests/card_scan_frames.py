"""Replay inputs for the self-scrolling アイドル一覧 scan (tests only; private frames, nothing committed).

The third demo recording holds native list frames, but they are seconds apart (no wheel events were recorded),
so consecutive frames never overlap. ``VirtualList`` therefore stacks *native* tile rows cut from those frames
into one tall list and renders 1280x720 frames of it at any scroll position: the header, tabs, sort bar and the
scrollbar track come from a native frame of the same tab, the thumb is moved proportionally (top y 191 at the
start, bottom edge y 675 at the end, as measured). These are replay inputs, never evidence of the live list.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REC = "dev_assets/private/recordings/wing-demo3-20261006-1643-0"
S_TOP = REC + "2/frame-000057.png"          # support tab, list top (thumb 191..234), grid offset 184
P_MID = REC + "2/frame-000008.png"          # produce tab, mid list
P_END = REC + "2/frame-000048.png"          # produce tab, list end (thumb bottom 675; last row 3 cards)
S_FILTER_ON = REC + "3/frame-000183.png"    # support tab, 絞り込みON
P_ASC = REC + "3/frame-000040.png"          # produce tab, 加入順 ▲昇順, list top
S_LOADING = REC + "2/frame-000054.png"      # support tab, list still loading (white track)
S_PLACEHOLDER = REC + "2/frame-000075.png"  # support tab, grey placeholder tiles
SORT_DIALOG = REC + "3/frame-000026.png"    # 並び替え dialog over the list
PORTAL = "dev_assets/private/nav-v16-idol-back-execute-frames/frame-001.png"
PORTAL_FROM_LIST = REC + "3/frame-000187.png"   # portal reached by the back key of the list (-03 #183 -> #187)
HOME = "dev_assets/private/shop-20261002-01-home-frames/frame-001.png"
LV_DESC = "dev_assets/private/owned-levels-20261007/idol-list-lv-001.png"
# (frame, first full row y, number of full rows) in list order.
S_ROWS = ((S_TOP, 184, 3), (REC + "2/frame-000067.png", 184, 3), (REC + "2/frame-000069.png", 208, 3),
          (REC + "2/frame-000076.png", 268, 2), (REC + "2/frame-000081.png", 290, 2))
P_ROWS = ((P_MID, 314, 2), (REC + "2/frame-000051.png", 204, 3), (REC + "2/frame-000035.png", 266, 2),
          (P_END, 222, 3))
NATIVE = (S_TOP, P_MID, P_END, S_FILTER_ON, P_ASC, S_LOADING, S_PLACEHOLDER, SORT_DIALOG, PORTAL, PORTAL_FROM_LIST,
          HOME, LV_DESC) + tuple(f for f, _, _ in S_ROWS + P_ROWS)

X0, X1 = 257, 1231            # list panel interior (tiles 267..1203)
VIEW_TOP, VIEW_BOTTOM = 170, 680
PITCH = 154
LEAD = 14                     # first row starts 14 px below the viewport top at the list top (S_TOP offset 184)
ROW_CUT = 10                  # cut each row 10 px above its tile top
BAR = (1234, 1250)
THUMB = (191, 234)


def present():
    return all((ROOT / f).is_file() for f in NATIVE)


def load(path):
    import numpy as np
    from PIL import Image

    with Image.open(ROOT / path) as image:
        return np.asarray(image.convert("RGB")).copy()


class VirtualList:
    def __init__(self, base, rows):
        import numpy as np

        self.base = load(base)
        strips = []
        for frame, first, count in rows:
            image = load(frame)
            for k in range(count):
                top = first + k * PITCH - ROW_CUT
                strips.append(image[top:top + PITCH, X0:X1])
        lead = self.base[VIEW_TOP:VIEW_TOP + LEAD - ROW_CUT, X0:X1]
        tail = np.repeat(self.base[VIEW_BOTTOM - 4:VIEW_BOTTOM, X0:X1], 12, axis=0)[:40]
        self.tall = np.concatenate([lead] + strips + [tail], axis=0)
        self.rows = len(strips)
        self.max_scroll = self.tall.shape[0] - (VIEW_BOTTOM - VIEW_TOP)
        from agent.core import card_list_scan as cs

        bar = cs.read_scrollbar(self.base)
        self.native_thumb = (bar.thumb_top - 2, bar.thumb_bottom + 3)       # thumb with its outline
        height = self.native_thumb[1] - self.native_thumb[0]
        free = 400 if bar.thumb_top < 340 else 200      # a plain track segment away from the native thumb
        self.track = self.base[free:free + height, BAR[0]:BAR[1]].copy()
        self.thumb = self.base[self.native_thumb[0]:self.native_thumb[1], BAR[0]:BAR[1]].copy()

    def render(self, scroll):
        from PIL import Image

        scroll = max(0, min(int(scroll), self.max_scroll))
        image = self.base.copy()
        image[VIEW_TOP:VIEW_BOTTOM, X0:X1] = self.tall[scroll:scroll + VIEW_BOTTOM - VIEW_TOP]
        # move the thumb: paint the native thumb position with track pixels, then draw the thumb at its new place
        image[self.native_thumb[0]:self.native_thumb[1], BAR[0]:BAR[1]] = self.track
        height = self.thumb.shape[0]
        first, last = THUMB[0] - 2, 675 + 3 - height          # thumb top 191 at the start, bottom 675 at the end
        top = first + round((last - first) * scroll / self.max_scroll) if self.max_scroll else first
        image[top:top + height, BAR[0]:BAR[1]] = self.thumb
        return Image.fromarray(image)


class ScriptedList:
    """Recorded frames in order: every scroll or tab tap moves to the next recorded frame (open loop)."""

    def __init__(self, frames):
        from PIL import Image

        self.frames = [Image.fromarray(load(f)) for f in frames]
        self.index = 0
        self.inputs = []

    def frame(self):
        return self.frames[min(self.index, len(self.frames) - 1)]

    def apply(self, decision):
        if decision.kind in ("scroll", "tap_tab"):
            self.inputs.append((decision.kind, decision.direction or decision.tab))
            self.index += 1


class FakeList:
    """Closed-loop list: applies the driver's decisions (scroll step ``step`` px, tab taps) and renders frames."""

    def __init__(self, lists, start_tab, step=2 * PITCH, start_scroll=0, tap_works=True):
        self.lists, self.tab, self.step, self.tap_works = lists, start_tab, step, tap_works
        self.scroll = {name: 0 for name in lists}
        self.scroll[start_tab] = start_scroll
        self.inputs = []

    def frame(self):
        return self.lists[self.tab].render(self.scroll[self.tab])

    def apply(self, decision):
        if decision.kind == "scroll":
            delta = self.step if decision.direction == "down" else -self.step
            lst = self.lists[self.tab]
            self.scroll[self.tab] = max(0, min(self.scroll[self.tab] + delta, lst.max_scroll))
            self.inputs.append(("scroll", decision.direction))
        elif decision.kind == "tap_tab":
            self.inputs.append(("tap_tab", decision.tab))
            if self.tap_works:
                self.tab = decision.tab
                self.scroll[self.tab] = 0
