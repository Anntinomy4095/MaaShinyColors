"""Latest open Match target from this run's continuous native navigation.

The area list and stage lock boundary establish the open target; the separate
complete Skip survey establishes current skip eligibility. No calendar, persisted
progress, or highest Skip-list number is used as an open-stage proof.
"""
from __future__ import annotations

import dataclasses
import re

from agent.core import match_scan as ms, match_task as mt, task_common as tc
from agent.core.match_observed import ObservedSkipPlanner

AREA_GUARD = "SC.Nav.MatchMain.Guard"
STAGE_GUARD = "SC.Match.Latest.Stage.Guard"
AREA_UP = (184, 86, 50, 60)
AREA_DOWN = (184, 636, 50, 60)
AREA_OPEN = (36, 270, 340, 47)
BACK = (20, 627, 75, 71)
MAX_NAVIGATION = 18


@dataclasses.dataclass(frozen=True)
class AreaPage:
    frame_digest: str
    observed_at: float
    areas: tuple[int, ...]
    terminal_locked: int | None
    rights: tuple[int, int] | None


@dataclasses.dataclass(frozen=True)
class StageBoundary:
    frame_digest: str
    observed_at: float
    area: int
    latest: int
    locked_after: bool
    rights: tuple[int, int]


def _lock(image, x, y):
    """Native padlock: white shackle above a pale-yellow rectangular body.

    Both parts must occur on the dark disabled overlay, not on reward icons.
    """
    patch = image[y:y + 60, x:x + 54].astype(int)
    if patch.shape != (60, 54, 3):
        return False
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    white = (b[:24] > 210) & (g[:24] > 210) & (r[:24] > 210)
    yellow = (r[24:48] > 190) & (g[24:48] > 180) & (b[24:48] < 215)
    dark = (b < 170) & (g < 170) & (r < 170)
    return int(white.sum()) >= 55 and int(yellow.sum()) >= 110 and int(dark.sum()) >= 450


def read_area_page(vision, image, stamp):
    if getattr(image, "shape", None) != (720, 1280, 3) or not vision.hit(AREA_GUARD, image):
        return None
    areas = []
    terminal = None
    for row in range(3):
        y = 218 + 136 * row
        header = vision.text(image, (28, y, 188, 42))
        match = re.fullmatch(r"エリア([1-7])(Normal|Hard)", header or "")
        if match:
            areas.append(int(match[1]))
            continue
        if row == 1 and _lock(image, 53, y + 28):
            requirement = vision.text(image, (108, y + 25, 280, 39))
            lock = re.fullmatch(r"エリア([1-7])のステージをす[べベ]てクリア", requirement or "")
            # Native terminal EX card is followed by empty map, not another card.
            column = image[488:603, 30].astype(int)
            if lock and not ((column[:, 2] > 200) & (column[:, 1] < 140)).any():
                terminal = int(lock[1])
                break
        # A cut/animated row is not proof; complete preceding rows remain readable.
        break
    rights = tc.ratio(vision.number(image, mt.RIGHTS_FIELD, "light", mt.RIGHTS_PATTERN))
    return AreaPage(tc.image_digest(image), stamp, tuple(areas), terminal, rights) if areas else None


def read_stage_boundary(vision, image, stamp, expected_area):
    if getattr(image, "shape", None) != (720, 1280, 3) or not vision.hit(STAGE_GUARD, image):
        return None
    area = vision.text(image, (32, 92, 110, 38))
    if area != f"エリア{expected_area}":
        return None
    rights = tc.ratio(vision.number(image, (1120, 665, 115, 24), "light", mt.RIGHTS_PATTERN))
    if rights is None or rights[1] != mt.RIGHTS_MAXIMUM:
        return None
    # The measured final card is fully visible in the native stage viewport.
    # Require a complete enabled row immediately before the positive lock.
    headers = []
    column = image[85:591, 760].astype(int)
    b, g, r = column.T
    colored = (r > 210) & (g < 130) & (b < 160)
    spans = []
    for offset, present in enumerate(colored):
        if present:
            y = 85 + offset
            if spans and spans[-1][1] == y - 1:
                spans[-1][1] = y
            else:
                spans.append([y, y])
    for top, bottom in spans:
        if bottom - top < 23:
            continue
        stage = vision.text(image, (590, top, 153, min(38, bottom - top + 1)))
        match = re.fullmatch(r"ステージ([1-9][0-9]?)", stage or "")
        if match:
            headers.append((int(match[1]), top, bottom))
    if not headers or any(a[0] >= b[0] for a, b in zip(headers, headers[1:])):
        return None
    stage, top, bottom = headers[-1]
    if _lock(image, 867, bottom + 186):
        # Lock occupies the next card and reaches the list end. No hidden higher row.
        return StageBoundary(tc.image_digest(image), stamp, expected_area, stage, True, rights)
    return None


class LatestNavigation:
    """One task-local navigation observation, invalidated on an uncertain action."""
    def __init__(self):
        self.phase = "areas"
        self.areas = []
        self.frames = []
        self.target = None
        self.rights = None
        self.pending = None
        self.waits = self.actions = 0
        self.last_page = None
        self.reason = None
        self.boundary = None

    def fail(self, reason):
        self.reason = reason
        return "failed"

    def _wait(self):
        self.waits += 1
        return self.fail("latest_progress_unreadable") if self.waits > mt.MAX_UNREADABLE_FRAMES else "hold"

    def area_step(self, page):
        if page is None or page.rights is None or page.rights[1] != mt.RIGHTS_MAXIMUM:
            return self._wait()
        if self.rights is not None and self.rights != page.rights:
            return self.fail("rights_changed_or_insufficient")
        if self.pending in ("area_down", "area_up") and page.areas == self.last_page.areas:
            return self._wait()
        if not self.areas:
            self.rights = page.rights
            if page.areas[0] != 1:
                if self.last_page is not None and page.observed_at <= self.last_page.observed_at:
                    return self.fail("latest_frame_order_invalid")
                self.frames.append(page.frame_digest)
                self.last_page, self.waits = page, 0
                self.actions += 1
                if self.actions > MAX_NAVIGATION:
                    return self.fail("latest_area_start_not_observed")
                self.pending = "area_up"
                return "area_up"
        else:
            overlap = [area for area in page.areas if area in self.areas]
            if not overlap or page.areas[:len(overlap)] != tuple(overlap) or self.areas[-len(overlap):] != overlap:
                return self.fail("latest_area_overlap_missing")
            if page.observed_at <= self.last_page.observed_at:
                return self.fail("latest_frame_order_invalid")
        for area in page.areas:
            if area not in self.areas:
                self.areas.append(area)
        if self.areas != list(range(1, max(self.areas) + 1)):
            return self.fail("latest_area_order_unreadable")
        self.frames.append(page.frame_digest)
        self.last_page, self.waits = page, 0
        if page.terminal_locked is not None:
            if page.areas != (page.terminal_locked,) or self.areas[-1] != page.terminal_locked:
                return self.fail("latest_area_boundary_changed")
            self.phase, self.pending = "stages", "area_open"
            return "area_open"
        self.actions += 1
        if self.actions > MAX_NAVIGATION:
            return self.fail("latest_area_end_not_observed")
        self.pending = "area_down"
        return "area_down"

    def stage_step(self, boundary):
        if boundary is None:
            return self._wait()
        if boundary.rights != self.rights or boundary.observed_at <= self.last_page.observed_at:
            return self.fail("rights_changed_or_insufficient")
        self.boundary = boundary
        self.target = (boundary.area, boundary.latest)
        self.frames.append(boundary.frame_digest)
        self.phase, self.pending = "return", "back"
        return "back"

    def returned(self, page):
        if page is None:
            return self._wait()
        # Back shows either the unchanged terminal page or (live 2026-10-13) the area list reset to its top.
        # Any other window, a new area or a different lock means the observed boundary is no longer bound.
        unchanged = (page.areas, page.terminal_locked) == (self.last_page.areas, self.last_page.terminal_locked)
        reset_to_top = page.terminal_locked is None and page.areas == tuple(self.areas[:len(page.areas)])
        if page.rights != self.rights or not (unchanged or reset_to_top):
            return self.fail("latest_area_boundary_changed")
        if page.observed_at <= self.boundary.observed_at:
            return self.fail("latest_frame_order_invalid")
        self.frames.append(page.frame_digest)
        self.phase, self.pending = "ready", None
        return "ready"

    def evidence(self):
        return {"scope": "current_navigation_latest_boundary", "latest_stage":
                f"A{self.target[0]}-S{self.target[1]}" if self.target else None,
                "areas": self.areas, "frames": self.frames, "cycle_id": None,
                "ranking_deadline_is_cycle": False, "input_authorized": False}


class LatestSkipPlanner(ObservedSkipPlanner):
    """Reuse precise explicit-row preparation after an independent open-stage proof."""
    def __init__(self, config, navigation):
        self.navigation = navigation
        target = navigation.target
        super().__init__(dataclasses.replace(config, stage=f"A{target[0]}-S{target[1]}"))
        self.retarget = None

    def step(self, page):
        decision = super().step(page)
        if (decision.kind == "abort" and decision.outcome == "stage_unavailable"
                and "not_in_current_skip_list" in decision.notes and self.scan_done and self.scan.stages
                and not self.pressed and self.retarget is None):
            # 2026-10-14 user: 「最新」 = the latest stage the complete, fresh Skip list offers. The open-stage
            # boundary (A7-S20) can be ahead of the clearable/skippable list (A5-S15) until the player clears it.
            self.retarget = max(self.scan.stages)
            self.target = self.retarget
            self.config = dataclasses.replace(self.config, stage=f"A{self.target[0]}-S{self.target[1]}")
            decision = super().step(page)
        if decision.kind == "abort" and decision.outcome in ("stage_unavailable", "stage_not_skippable"):
            return mt.Decision("abort", outcome="latest_not_skippable", notes=("no_older_stage_fallback",))
        return decision

    def evidence(self):
        return {**super().evidence(), **self.navigation.evidence(),
                "latest_open_stage": self.navigation.evidence()["latest_stage"],
                "selected_stage": f"A{self.target[0]}-S{self.target[1]}",
                "selected_from": "latest_skippable" if self.retarget else "latest_open",
                "skip_list_complete": self.scan_done,
                "skip_frames": [page.frame_digest for page in self.scan.pages]}


class SkipListLatestPlanner(ObservedSkipPlanner):
    """「最新」 straight from the Skip dialog (2026-10-14 user: no area-list scroll first). The target is the last stage of
    the complete, fresh skip list; nothing is pressed before the whole list was read."""
    def __init__(self, config):
        self.config = config
        self.scan = ms.SkipListScan()
        self.target = None
        self.pressed = self.scrolls = self.waits = 0
        self.last = self.last_thumb = None
        self.current_page = None

    def step(self, page):
        decision = super().step(page)
        if (self.target is None and decision.kind == "abort" and decision.outcome == "stage_unavailable"
                and self.scan_done and self.scan.stages and not self.pressed):
            self.target = max(self.scan.stages)
            self.config = dataclasses.replace(self.config, stage=f"A{self.target[0]}-S{self.target[1]}")
            decision = super().step(page)
        if decision.kind == "abort" and decision.outcome in ("stage_unavailable", "stage_not_skippable"):
            return mt.Decision("abort", outcome="latest_not_skippable", notes=decision.notes)
        return decision

    def evidence(self):
        return {**super().evidence(), "scope": "current_skip_list_latest",
                "selected_stage": f"A{self.target[0]}-S{self.target[1]}" if self.target else None,
                "selected_from": "latest_skippable"}

