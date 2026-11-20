"""Match Live skip (B5 / B5b): pure rules, the skip-dialog planner and per-run state. No Maa import, no input.

Facts (native samples): the Match main page shows a スキップ button with the shared フェス参加権 bar ("1/20 +0").
The スキップ dialog (native 2026-10-02 sample run, ``dev_assets/private/match-skip-native-20261002-frames``) shows
title, 参加権回復 / 強化アイテム一覧 / 絞り込み, a scrollable two-column list of stage slots 「エリアN ステージM」 with
獲得報酬 icons and a −/count/+ counter, the buttons キャンセル / クリア / スキップする and 「参加権消費数 N / M」, where
M is the rights currently available (legacy sample: rights 13/20 -> "0 / 13").

``sample_only`` (default True) opens the dialog, saves frames read-only and presses キャンセル. With
``sample_only=False`` (B5b, not yet run live) the planner:

1. binds trusted player progress to independently observed current-cycle evidence, then scans the whole list from
   the top to the bottom; its stage identities must match the skippable subset of complete player progress;
2. picks the target: ``latest`` = the highest trusted stage_order, independently of screen order or numbering;
   if that row is not skippable the task STOPS - it never falls back to an older stage;
3. requires ``count <= rights M - reserve`` (insufficient rights stop the task; nothing is capped silently);
4. scrolls back until the target row is visible, presses its + exactly ``count`` times, verifying after every press
   that the row counter and 参加権消費数 both equal the presses so far;
5. スキップする opens a non-consuming review; only its verified スキップ開始 writes the intent and asks for a grant.

Any mismatch: クリア (when anything was added) then キャンセル, and the task fails. The result screen after
スキップする has no native sample: the task only observes read-only and ends ``spend_uncertain`` unless a known page
shows the rights reduced by ``count``. Recovery tickets are never used (jewels never).
"""
from __future__ import annotations

import dataclasses
import re

from agent.core import fes_allocation as fa
from agent.core import task_common as tc
from agent.core import match_stage_target as mst
from agent.core.match_progress import MatchProgress

RIGHTS_FIELD = [1150, 553, 46, 20]   # light "1/20" on the Match main スキップ button bar (nav-v21 frame-023)
RIGHTS_PATTERN = r"[0-9]{1,2}/[0-9]{1,2}"
RIGHTS_MAXIMUM = 20              # verified capacity of the shared フェス参加権 pool
SAMPLE_FRAMES = 5
MAX_UNREADABLE_FRAMES = 3
MAX_SCROLLS = 15
SPEND_ACTION = "match-skip-confirm"
APPROVER = "SC.Match.Check.Confirm"
WATCH_FRAME_LIMIT = 30
COUNT_MODES = ("all", "fixed")
PLAY_MODES = ("advance", "off")     # match_play.MODES (no import: match_play reads this config)
PLAY_UNIT_TABS = 10
PLAY_RETRIES = (0, 1, 2, 3, 5)

# Skip dialog geometry (1280x720, native frame-005). Slots: 1/2 = row 1 left/right, 3/4 = row 2, 5/6 = row 3.
SLOTS = (1, 2, 3, 4, 5, 6)
_ROW_HEADER_Y, _ROW_COUNT_Y, _ROW_PLUS_Y = (192, 319, 447), (250, 375, 503), (257, 382, 510)
SLOT_HEADER = {s: [70 + 566 * ((s - 1) % 2), _ROW_HEADER_Y[(s - 1) // 2], 300, 28] for s in SLOTS}
SLOT_COUNT = {s: [462 + 566 * ((s - 1) % 2), _ROW_COUNT_Y[(s - 1) // 2], 90, 28] for s in SLOTS}
SLOT_PLUS = {s: [575 + 565 * ((s - 1) % 2), _ROW_PLUS_Y[(s - 1) // 2], 20, 16] for s in SLOTS}  # pink "+" glyph
SLOT_BAR = {s: (x + 4, y + 6) for s, (x, y, _, _) in SLOT_HEADER.items()}                      # blue header bar pixel
COST_FIELD = [1068, 614, 140, 36]                                                             # dark "0 / 20"
REVIEW_GUARD = "SC.Match.Review.Guard"
REVIEW_HEADER = [393, 191, 310, 28]
REVIEW_COUNT = [735, 254, 110, 38]
REVIEW_COST = [1060, 610, 148, 46]
SCROLLBAR_X, SCROLLBAR_TOP, SCROLLBAR_BOTTOM = 1198, 193, 561
HEADER_PATTERN = re.compile(r"エリア([0-9]{1,2})ステージ([0-9]{1,3})")

# "played": the play part used the rights it was allowed (match_play) and nothing was left to skip.
SUCCESS_OUTCOMES = frozenset({"sampled", "skipped", "nothing_to_do", "played"})
LATEST_FAILURE_OUTCOMES = frozenset({"latest_progress_unreadable", "latest_frame_order_invalid",
    "latest_area_overlap_missing", "latest_area_order_unreadable", "latest_area_boundary_changed",
    "latest_area_start_not_observed", "latest_area_end_not_observed", "latest_navigation_failed"})
FAILURE_OUTCOMES = frozenset({"config_invalid", "recovery_policy_disabled", "recovery_cap_exceeds_policy",
                              "recovery_not_implemented", "skip_not_enabled", "rights_unreadable", "sample_dialog_unrecognised",
                              "skip_open_no_effect", "dialog_unreadable", "counts_not_zero", "scroll_no_effect",
                              "list_end_not_reached", "stage_evidence_incomplete", "latest_not_skippable",
                              "stage_reward_unverified", "attribute_stage_unavailable", "latest_attribute_not_skippable",
                              "stage_unavailable", "stage_not_skippable", "rights_insufficient", "counter_mismatch",
                              "rights_changed_or_insufficient",
                              "progress_evidence_missing", "progress_unreadable", "progress_reset", "progress_cycle_changed",
                              "progress_frame_mismatch", "progress_stale", "progress_changed",
                              "spend_uncertain", "spend_refused", "log_write_failed", "timeout",
                              "play_page_unknown", "play_return_failed"}) | LATEST_FAILURE_OUTCOMES


@dataclasses.dataclass(frozen=True)
class MatchConfig:
    """``count_mode``: ``all`` (default 「全部用完」) skips ``min(count, rights - reserve)`` read on the
    Match page at run time (``count`` is then the batch cap 20); ``fixed`` skips exactly ``count`` or stops.
    Recovery tickets have their own switch and per-run cap; the policy refuses them (``fes_allocation``).
    ``play``: ``advance`` plays the frontier stage before the Skip (``match_play``) with ``unit`` (0 = the
    current unit, 1–10) and ``retries`` more lives after a LOSE of the same stage; ``off`` only skips."""
    # "latest_vo/da/vi" (2026-10-09 UI; the PI/Pipeline default is latest_vo), "latest" (legacy dataclass default kept
    # for direct construction) or "A<area>-S<stage>".
    stage: str = "latest"
    count: int = 20
    rights_reserve: int = 0
    use_recovery_tickets: bool = False
    sample_only: bool = True
    count_mode: str = "all"        # "all" | "fixed"
    recovery_cap: int = 1
    play: str = "advance"          # "advance" | "off"
    unit: int = 0
    retries: int = 3

    @classmethod
    def from_params(cls, params):
        tc.require(isinstance(params, dict), "Match 配置必须为对象")
        allowed = {"stage", "count", "rights_reserve", "use_recovery_tickets", "sample_only", "count_mode",
                   "recovery_cap", "play", "unit", "retries"}
        tc.require(set(params) <= allowed, "Match 配置含未知字段: " + ",".join(sorted(set(params) - allowed)))
        stage = params.get("stage", "latest")
        tc.require(stage == "latest" or stage in mst.TARGETS
                   or (isinstance(stage, str) and re.fullmatch(r"A[1-9][0-9]?-S[1-9][0-9]{0,2}", stage)),
                   "stage 只能为 latest_vo/latest_da/latest_vi、latest 或 A<区域>-S<关卡>")
        count = params.get("count", 20)
        tc.require(type(count) is int and 1 <= count <= 20, "count 必须为 1–20 的整数")
        mode = params.get("count_mode", "all")
        tc.require(mode in COUNT_MODES, "count_mode 只能为 all/fixed")
        reserve = params.get("rights_reserve", 0)
        tc.require(type(reserve) is int and 0 <= reserve <= 19, "rights_reserve 必须为 0–19 的整数")
        recovery = params.get("use_recovery_tickets", False)
        tc.require(type(recovery) is bool, "use_recovery_tickets 必须为布尔值")
        cap = params.get("recovery_cap", 1)
        tc.require(cap in fa.RECOVERY_CAPS and type(cap) is int, "recovery_cap 只能为 1/2/3")
        sample = params.get("sample_only", True)
        tc.require(type(sample) is bool, "sample_only 必须为布尔值")
        play = params.get("play", "advance")
        tc.require(play in PLAY_MODES, "play 只能为 advance/off")
        unit = params.get("unit", 0)
        tc.require(type(unit) is int and 0 <= unit <= PLAY_UNIT_TABS, "unit 只能为 0（当前）或 1–10")
        retries = params.get("retries", 3)
        tc.require(type(retries) is int and retries in PLAY_RETRIES, "retries 只能为 0/1/2/3/5")
        return cls(stage, count, reserve, recovery, sample, mode, cap, play, unit, retries)

    def resolved(self, rights):
        """The run-time count for the Match page reading ``rights`` (current, max): ``fixed`` keeps ``count``;
        ``all`` becomes ``min(count, current - reserve)`` with mode ``fixed`` (0 when nothing is left)."""
        if self.count_mode == "fixed":
            return self
        n = fa.match_all_count(rights[0], self.rights_reserve, self.count)
        return dataclasses.replace(self, count=n, count_mode="fixed") if n else None

    def to_dict(self):
        return dataclasses.asdict(self)

    def explicit(self):
        """(area, stage) of an explicit stage, None for latest / latest_<attr>."""
        if self.stage == "latest" or self.stage in mst.TARGETS:
            return None
        area, stage = re.fullmatch(r"A([0-9]+)-S([0-9]+)", self.stage).groups()
        return int(area), int(stage)


def plan_notes(config, rights):
    """Informational notes (a real skip needs count + reserve rights)."""
    notes = [f"rights {rights[0]}/{rights[1]}", f"count_mode_{config.count_mode}"]
    usable = max(0, rights[0] - config.rights_reserve)
    if usable < config.count:
        notes.append(f"rights_insufficient_for_count_{config.count}(usable_{usable})")
    return notes


def parse_header(text):
    """'エリア2ステージ5' (whitespace already removed) -> (2, 5); None otherwise."""
    match = HEADER_PATTERN.fullmatch(text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


@dataclasses.dataclass(frozen=True)
class Slot:
    slot: int
    present: bool                 # a stage row occupies this slot (blue header bar)
    stage: tuple = None           # (area, stage) or None when unreadable
    count: int = None
    plus_enabled: bool = False


@dataclasses.dataclass(frozen=True)
class DialogView:
    slots: tuple
    cost: tuple = None            # (selected, available)
    thumb: tuple = None           # (top, bottom) of the scrollbar thumb, None when no thumb

    @property
    def readable(self):
        return self.cost is not None and all(s.stage is not None and s.count is not None for s in self.slots if s.present)

    def stages(self):
        return tuple(s.stage for s in self.slots if s.present)

    def at_top(self):
        return self.thumb is not None and self.thumb[0] <= SCROLLBAR_TOP + 6

    def at_bottom(self):
        return self.thumb is not None and self.thumb[1] >= SCROLLBAR_BOTTOM - 6


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                     # wait | scroll_down | scroll_up | plus | confirm | abort
    slot: int = None
    outcome: str = None
    notes: tuple = ()

    def to_dict(self):
        return {k: v for k, v in dataclasses.asdict(self).items() if v not in (None, ())}


class SkipPlanner:
    """Stateful planner of one dialog session (per task run). ``step(view)`` is called once per fresh frame."""

    def __init__(self, config, progress=None):
        self.config = config
        tc.require(progress is None or isinstance(progress, MatchProgress), "Match 进度合同类型无效")
        self.progress = progress
        self.seen = {}            # (area, stage) -> plus enabled (last observation)
        self.scan_started = False
        self.scan_done = False
        self.target = None
        self.pressed = 0
        self.scrolls = 0
        self.last = None          # last decision kind that changed the screen
        self.last_thumb = None
        self.waits = 0

    def _wait_or(self, outcome, *notes):
        self.waits += 1
        if self.waits > MAX_UNREADABLE_FRAMES:
            return Decision("abort", outcome=outcome, notes=notes)
        return Decision("wait", notes=notes)

    def step(self, view):
        if self.progress is None:
            return Decision("abort", outcome="progress_evidence_missing")
        if failure := self.progress.failure():
            return Decision("abort", outcome=failure)
        if not view.readable:
            return self._wait_or("dialog_unreadable", "slots_or_cost_unreadable")
        if view.thumb is None:
            return self._wait_or("dialog_unreadable", "scrollbar_unreadable")
        if len(set(view.stages())) != len(view.stages()):
            return Decision("abort", outcome="stage_evidence_incomplete", notes=("duplicate_stage_rows",))
        expected = {stage.stage for stage in self.progress.stages}
        if any(stage not in expected for stage in view.stages()):
            return Decision("abort", outcome="progress_changed", notes=("unbound_stage_observed",))
        selected, available = view.cost
        moved = self.last in ("scroll_down", "scroll_up")
        if moved and view.thumb == self.last_thumb:
            return self._wait_or("scroll_no_effect", "thumb_unchanged_after_scroll")
        if not self.scan_done:
            if selected != 0:
                return Decision("abort", outcome="counts_not_zero", notes=(f"selected_{selected}",))
            if not self.scan_started:
                if not view.at_top():
                    return Decision("abort", outcome="stage_evidence_incomplete", notes=("list_start_not_observed",))
                self.scan_started = True
            for slot in view.slots:
                if slot.present:
                    self.seen[slot.stage] = slot.plus_enabled
            if view.at_bottom():
                self.scan_done = True
            else:
                return self._scroll("scroll_down", view)
        if self.target is None:
            chosen = self._choose(available)
            if isinstance(chosen, Decision):
                return chosen
            self.target = chosen
        slot = next((s for s in view.slots if s.present and s.stage == self.target), None)
        if slot is None:
            if self.pressed:
                return Decision("abort", outcome="counter_mismatch", notes=("target_left_the_screen",))
            return self._scroll("scroll_up", view)
        if not slot.plus_enabled and self.pressed < self.config.count:
            return Decision("abort", outcome="stage_not_skippable", notes=("plus_disabled",))
        if slot.count != self.pressed or selected != self.pressed:
            if self.last == "plus" and slot.count == self.pressed - 1 and selected == self.pressed - 1:
                return self._wait_or("counter_mismatch", "press_not_reflected_yet")
            return Decision("abort", outcome="counter_mismatch",
                            notes=(f"pressed_{self.pressed}", f"row_{slot.count}", f"selected_{selected}"))
        self.waits = 0  # reset only after the last press is positively reflected, never on an unchanged count
        if available - self.config.rights_reserve < self.config.count:
            return Decision("abort", outcome="rights_insufficient", notes=(f"available_{available}",))
        if self.pressed < self.config.count:
            self.pressed += 1
            self.last = "plus"
            return Decision("plus", slot=slot.slot, notes=(f"press_{self.pressed}_of_{self.config.count}",))
        self.last = None
        return Decision("confirm", slot=slot.slot)

    def _scroll(self, kind, view):
        self.waits = 0
        self.scrolls += 1
        if self.scrolls > MAX_SCROLLS:
            return Decision("abort", outcome="list_end_not_reached" if kind == "scroll_down" else "stage_unavailable",
                            notes=(f"scrolls_{self.scrolls - 1}",))
        if kind == "scroll_up" and view.at_top():
            return Decision("abort", outcome="stage_unavailable", notes=("target_not_found_at_top",))
        self.last, self.last_thumb = kind, view.thumb
        return Decision(kind)

    def _choose(self, available):
        stages = {stage.stage: stage for stage in self.progress.stages}
        skippable = {stage for stage, record in stages.items() if record.skip_available}
        if set(self.seen) != skippable:
            return Decision("abort", outcome="stage_evidence_incomplete", notes=("list_does_not_match_verified_progress",))
        if available - self.config.rights_reserve < self.config.count:
            return Decision("abort", outcome="rights_insufficient",
                            notes=(f"available_{available}", f"reserve_{self.config.rights_reserve}"))
        explicit = self.config.explicit()
        if self.config.stage in mst.TARGETS:  # 2026-10-09: latest stage of one reward attribute; never an older one
            target, outcome = mst.resolve(self.config.stage, self.progress.stages, self.seen)
            if outcome:
                return Decision("abort", outcome=outcome, notes=(self.config.stage,))
            return target
        if explicit is None:
            latest = max(stages.values(), key=lambda stage: stage.stage_order).stage
            if not stages[latest].skip_available or not self.seen.get(latest, False):
                return Decision("abort", outcome="latest_not_skippable", notes=(f"A{latest[0]}-S{latest[1]}",))
            return latest
        if explicit not in self.seen:
            return Decision("abort", outcome="stage_unavailable", notes=(self.config.stage,))
        if not stages[explicit].skip_available or not self.seen[explicit]:
            return Decision("abort", outcome="stage_not_skippable", notes=(self.config.stage,))
        return explicit


class MatchRun(tc.TaskRun):
    """Phases: [play ->] active -> skip_opened -> planning -> review -> awaiting_result -> finished."""
    SUCCESS = SUCCESS_OUTCOMES
    HALT = tc.TaskRun.HALT | {"sample_dialog_unrecognised"}

    def __init__(self, config, log, progress=None):
        super().__init__(config, log)
        self.rights = None
        self.saver = None
        self.samples = 0
        self.dialog_hits = 0
        self.progress = progress
        self.progress_proof = None
        self.planner = SkipPlanner(config, progress)
        self.abort_outcome = None
        self.cleared = False
        self.last_save = None
        self.prepared = None
        self.receipt_seen = False
        self.settle = None            # ListSettle after a Skip wheel (match_custom)
        self.play = None              # match_play.MatchPlay.summary() once the play part ended

    def summary(self):
        report = super().summary()
        if self.play is not None:
            report["play"] = dict(self.play)
        evidence = getattr(self.planner, "evidence", None)
        if evidence is not None:
            observed = evidence()
            report["progress"] = {
                "latest_open_stage": observed.get("latest_stage"),
                "latest_skippable_stage": observed.get("latest_skippable_stage"),
                "selected_stage": observed.get("target"),
                "skip_list_complete": observed.get("skip_list_complete", False),
            }
        return report
