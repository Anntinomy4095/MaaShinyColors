"""Explicit Match targets from a fresh, complete Skip dialog survey.

The dialog is the game's current eligibility list. This proof is deliberately
limited to the explicitly requested row; it cannot establish the latest open stage
or a reset calendar. No persisted progress or invented cycle identifier is used.
"""
import dataclasses

from agent.core import match_scan as ms, match_task as mt


HOLD_MS = 2500        # long press of ＋ (2026-10-14 user); the counter stops at the available rights
HOLD_MIN_COUNT = 3


class ObservedSkipPlanner:
    hold_used = False
    def __init__(self, config):
        if config.explicit() is None:
            raise ValueError("实时跳过列表只能核对用户指定关卡，不能推断最新开放关卡")
        self.config = config
        self.scan = ms.SkipListScan()
        self.target = config.explicit()
        self.pressed = self.scrolls = self.waits = 0
        self.last = self.last_thumb = None
        self.current_page = None

    @property
    def scan_done(self):
        return self.scan.complete and self.scan.reason is None

    def _wait(self, reason):
        self.waits += 1
        return (mt.Decision("abort", outcome=reason) if self.waits > mt.MAX_UNREADABLE_FRAMES
                else mt.Decision("wait"))

    def _scroll(self, direction, page):
        self.scrolls += 1
        if self.scrolls > mt.MAX_SCROLLS:
            return mt.Decision("abort", outcome="list_end_not_reached")
        if direction == "scroll_up" and page.at_top():
            return mt.Decision("abort", outcome="stage_unavailable")
        self.last, self.last_thumb, self.waits = direction, page.thumb, 0
        return mt.Decision(direction)

    def _implied_zero_counts(self, page):
        """2026-11-21 live: rows 1–2 of the Skip dialog read every stage but no counter (A5-S15 x20; the run cancelled).
        The selected total counts every row: when the target row's counter equals it, the unread counters of the other
        rows are 0. Stages, cost and thumb must still be read; anything else is left unread."""
        if page.readable or page.cost is None or page.thumb is None or any(r.stage is None for r in page.rows):
            return page
        if page.cost[0] != 0:   # nothing selected: every counter is 0 (the scan needs no other proof)
            target = [r for r in page.rows if r.stage == self.target]
            if len(target) != 1 or target[0].count is None or target[0].count != page.cost[0]:
                return page
        rows = tuple(dataclasses.replace(r, count=0) if r.count is None else r for r in page.rows)
        return dataclasses.replace(page, rows=rows)

    def step(self, page):
        page = self._implied_zero_counts(page)
        self.current_page = page
        # While scanning, leading rows already read on the page before may be unreadable (ms.covered_view).
        scan_page = page if self.scan_done else ms.covered_view(page, self.scan.stages)
        if scan_page is None or not scan_page.readable:
            return self._wait("dialog_unreadable")
        if self.last in ("scroll_up", "scroll_down") and self.last_thumb == page.thumb:
            return self._wait("scroll_no_effect")
        if not self.scan_done:
            if not self.scan.add(scan_page):
                return mt.Decision("abort", outcome="stage_evidence_incomplete", notes=(self.scan.reason,))
            if not self.scan_done:
                return self._scroll("scroll_down", page)
            if self.target not in self.scan.stages:
                return mt.Decision("abort", outcome="stage_unavailable",
                                   notes=(self.config.stage, "not_in_current_skip_list"))
        if not page.readable:   # ＋ targets are row indices of the full page: every row must be read
            return self._wait("dialog_unreadable")
        if any(row.stage not in self.scan.stages for row in page.rows):
            return mt.Decision("abort", outcome="progress_changed")
        rows = [(index, row) for index, row in enumerate(page.rows, 1) if row.stage == self.target]
        if len(rows) != 1:
            if self.pressed:
                return mt.Decision("abort", outcome="counter_mismatch", notes=("target_left_the_screen",))
            return self._scroll("scroll_up", page)
        slot, row = rows[0]
        selected, available = page.cost
        if available != self.scan.pages[0].cost[1]:
            return mt.Decision("abort", outcome="rights_changed_or_insufficient")
        if self.last == "hold":
            # One long press of ＋ (only when count == all available rights, so it cannot overshoot): take the counter
            # it reached, then tap the rest. A counter beyond the planned count or other rows changed -> stop.
            self.last = None
            if (row.count == selected and 0 <= row.count <= self.config.count
                    and all(item.count == 0 for item in page.rows if item.stage != self.target)):
                self.pressed = row.count
            else:
                return mt.Decision("abort", outcome="counter_mismatch", notes=("after_hold",))
        # 2026-11-21 live: one ＋ tap counted twice (18 -> 20 of 20 planned). A counter that lands exactly on the planned
        # count, one past the taps sent, with no other row changed, is the plan reached; anything else still stops.
        if (self.last == "plus" and row.count == selected == self.config.count == self.pressed + 1
                and all(item.count == 0 for item in page.rows if item.stage != self.target)):
            self.pressed = row.count
        # After the hold the counter is taken first: at all rights the game disables ＋ (live 2026-10-31, 8/8).
        if not row.plus_enabled and self.pressed < self.config.count:
            return mt.Decision("abort", outcome="stage_not_skippable")
        if row.count != self.pressed or selected != self.pressed or any(
                item.count != 0 for item in page.rows if item.stage != self.target):
            if self.last == "plus" and row.count == selected == self.pressed - 1:
                return self._wait("counter_mismatch")
            return mt.Decision("abort", outcome="counter_mismatch")
        if available - self.config.rights_reserve < self.config.count:
            return mt.Decision("abort", outcome="rights_insufficient")
        self.waits = 0
        if (self.pressed == 0 and not self.hold_used and self.config.count >= HOLD_MIN_COUNT
                and self.config.count == available):
            self.hold_used = True
            self.last = "hold"
            return mt.Decision("plus", slot=slot)
        if self.pressed < self.config.count:
            self.pressed += 1
            self.last = "plus"
            return mt.Decision("plus", slot=slot)
        self.last = None
        return mt.Decision("confirm", slot=slot)

    def hold_unavailable(self):
        """The host cannot long-press: this ＋ becomes an ordinary single tap."""
        if self.last == "hold":
            self.last, self.pressed = "plus", self.pressed + 1

    def evidence(self):
        return {"scope": "explicit_current_skip_list", "target": self.config.stage,
                "skip_list_complete": self.scan_done,
                "latest_skippable_stage": (f"A{max(self.scan.stages)[0]}-S{max(self.scan.stages)[1]}"
                                           if self.scan_done and self.scan.stages else None),
                "stages": [f"A{area}-S{stage}" for area, stage in self.scan.stages],
                "frames": [page.frame_digest for page in self.scan.pages],
                "latest_stage": None, "cycle_id": None}
