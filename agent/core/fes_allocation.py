"""Shared フェス参加権 allocation between Match skip, Grade Fes and Grade rehearsal.

Pure rules, no Maa import, no input. Match skip, グレードフェス and フェスリハーサル draw from ONE pool of フェス参加権
(natural cap 20, +1 per 30 min). Running Match and Grade on the same day is allowed; the
user decides how the pool is split:

* Match ``count_mode="all"`` (default 「全部用完」): Grade and rehearsal take their explicit counts first, Match skips
  everything that is left (``min(20, current - Match reserve)``, read on the Match page at run time). For this to
  leave Grade its share the Fes tasks must run Grade before Match (the daily preset does); an all-in Match ordered
  before an enabled Grade is refused at preflight (``match_all_before_grade``).
* Match ``count_mode="fixed"`` (custom count N): every task uses its explicit count; the total must fit the rights.

Each task still re-reads the rights on its own Fes page right before spending and stops (``rights_insufficient`` /
``skipped_rights``) when its share is not there: a profile-time plan is a check, never a spend authority.

Unknown current rights (no fresh reading) are never guessed: the plan is ``rights_unknown`` and only checked against
the capacity 20; the tasks take the count from their Fes page reading. The ``+c`` part of a home reading ``a/b+c`` is
not counted (whether the skip dialog's M includes it is unverified).

Recovery tickets (参加権回復) are a separate resource with their own per-task switch and per-run cap (default off).
``project_policy.json`` ``resource_usage.fes_tickets`` is the machine-enforced baseline: while it is disabled any
enabled recovery switch is refused (``recovery_policy_disabled``). Even with a future policy the live use path does
not exist yet (``recovery_not_implemented``); this module only plans.

Rehearsal (フェスリハーサル): supervised Normal sample 2026-10-11 confirms that selecting the difficulty consumes
one right and directly starts a ten-turn live, without the formal Grade unit/tension page. Its single LIVE REPORT OK
returns to Fes main with rights 20 -> 19 and unchanged Grade points. Plan one right per rehearsal.

まとめて消費: Grade may plan 1-5 rights per live (``bulk_count``); the demand is ``lives × bulk_count``.
Only 1 (unticked) is sampled, so any larger value is refused at preflight (``bulk_not_sampled``) as well as at run time.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "project_policy.json"
RIGHTS_CAPACITY = 20           # verified natural cap of the shared pool
MATCH_MAX_COUNT = 20           # skip dialog count per batch (Match config 1-20)
RECOVERY_CAPS = (1, 2, 3)      # per-run cap choices of the recovery switch
REHEARSAL_RIGHTS_PER_RUN = 1   # 「フェス参加権を 1 消費します」 on the rehearsal difficulty page (bulk never used)
GRADE_RIGHTS_PER_RUN = 1
FES_TASKS = ("MatchSkip", "GradeFes")

MESSAGES = {
    "rehearsal_not_sampled": "彩排画面还没有样本，暂不运行。",
    "mixed_mode_not_supported": "正式 Fes 与彩排分开运行，每次选一种。",
    "bulk_not_sampled": "每局只能消耗 1 个参加权：把「每局消耗参加权」设为 1。",
    "recovery_policy_disabled": "回复券未开放：关闭「参加权不足时用回复券」",
    "recovery_cap_exceeds_policy": "回复券上限超出允许范围",
    "recovery_not_implemented": "暂不支持回复券：关闭「参加权不足时用回复券」",
    "match_all_before_grade": "Match「全部」会用掉 Grade 的参加权：把 Grade 排在 Match 前面，或给 Match 指定次数。",
    "over_allocated": "计划消耗超过当前参加权：减少 Match 次数或 Grade 局数。",
    "over_allocated_capacity": "计划消耗超过上限 20 个参加权：减少 Match 次数或 Grade 局数。",
    "rights_unknown": "当前参加权尚未读取：各任务在 Fes 页面读到后再核对，不足时停止。",
    "match_all_nothing_left": "Grade/彩排之后没有剩余参加权，Match 不跳过。",
    "plus_not_counted": "首页读数的 +c 部分不计入（跳过弹窗是否包含未核实）。",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclasses.dataclass(frozen=True)
class TicketPolicy:
    """``project_policy.json`` ``resource_usage.fes_tickets``; missing or malformed means disabled."""
    enabled: bool = False
    max_per_run: int = 0

    @classmethod
    def from_document(cls, document):
        tickets = ((document or {}).get("resource_usage") or {}).get("fes_tickets") or {}
        enabled = tickets.get("enabled") is True
        cap = tickets.get("max_per_run", 0)
        return cls(enabled, cap if type(cap) is int and cap >= 0 else 0)


def load_policy(path=POLICY_PATH):
    try:
        return TicketPolicy.from_document(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return TicketPolicy()


def recovery_refusal(use_recovery, cap, policy):
    """Reason code why an enabled recovery switch cannot run (None when the switch is off)."""
    if not use_recovery:
        return None
    if not policy.enabled:
        return "recovery_policy_disabled"
    if cap > policy.max_per_run:
        return "recovery_cap_exceeds_policy"
    return "recovery_not_implemented"


@dataclasses.dataclass(frozen=True)
class Rights:
    """A rights reading ``current/maximum`` (+ ``plus``); only a reading of the verified capacity 20 is usable."""
    current: int
    maximum: int = RIGHTS_CAPACITY
    plus: int = 0

    @classmethod
    def coerce(cls, value):
        if value is None or isinstance(value, Rights):
            return value
        if isinstance(value, (tuple, list)) and len(value) in (2, 3):
            return cls(*value)
        if isinstance(value, dict):
            return cls(value.get("count", value.get("current")), value.get("max", RIGHTS_CAPACITY), value.get("plus") or 0)
        raise ValueError("参加权读数无效")

    def usable(self):
        ok = (type(self.current) is int and type(self.maximum) is int and self.maximum == RIGHTS_CAPACITY
              and 0 <= self.current <= 999)
        return self.current if ok else None


@dataclasses.dataclass(frozen=True)
class Allocation:
    """What the enabled Fes tasks want, in run order. Built from the merged Match/Grade task configs."""
    order: tuple = ()                  # enabled Fes task names in run order
    match_mode: str = "all"            # "all" | "fixed"
    match_count: int = MATCH_MAX_COUNT # fixed count, or the per-batch cap in "all" mode
    match_reserve: int = 0
    match_recovery: bool = False
    match_recovery_cap: int = 1
    grade_runs: int = 0                # real グレードフェス lives to run in this batch
    rehearsal_runs: int = 0
    grade_reserve: int = 0
    grade_recovery: bool = False
    grade_recovery_cap: int = 1
    grade_bulk: int = 1                # まとめて消費: rights per live (1 = unticked)

    @classmethod
    def from_configs(cls, match=None, grade=None, order=FES_TASKS, grade_progress=None):
        """``match`` / ``grade``: ``MatchConfig`` / ``GradeConfig`` of ENABLED tasks (None = not run); ``order``:
        the run order of the task names. A sample-only Match never consumes and is left out. Pass the selected
        mode's ``DailyProgress`` to reserve only the executable remaining lives, rather than the daily total."""
        if match is not None and getattr(match, "sample_only", False):
            match = None
        names = [n for n in order if (n == "MatchSkip" and match is not None) or (n == "GradeFes" and grade is not None)]
        fields = {"order": tuple(names)}
        if match is not None:
            fields.update(match_mode=match.count_mode, match_count=match.count, match_reserve=match.rights_reserve,
                          match_recovery=match.use_recovery_tickets, match_recovery_cap=match.recovery_cap)
        if grade is not None:
            fields.update(grade_runs=getattr(grade, "grade_runs", grade.runs_per_day), rehearsal_runs=grade.rehearsal_runs,
                          grade_bulk=getattr(grade, "bulk_count", 1),
                          grade_reserve=grade.rights_reserve, grade_recovery=grade.use_recovery_tickets,
                          grade_recovery_cap=grade.recovery_cap)
            if grade_progress is not None:
                progress = grade_progress.to_dict() if hasattr(grade_progress, "to_dict") else grade_progress
                _require(isinstance(progress, dict) and progress.get("mode") == grade.mode,
                         "Grade 每日进度与所选模式不一致")
                count = progress.get("planned_runs")
                _require(type(count) is int and 0 <= count <= grade.runs_per_day, "Grade 本轮剩余局数无效")
                # Manual takeover starts at most one live; automatic play runs the remaining daily queue.
                count = count if grade.live_play else min(1, count)
                fields.update(grade_runs=count if grade.mode == "grade" else 0,
                              rehearsal_runs=count if grade.mode == "rehearsal" else 0)
        return cls(**fields)

    @property
    def match(self):
        return "MatchSkip" in self.order

    @property
    def grade(self):
        return "GradeFes" in self.order

    def grade_demand(self):
        if not self.grade:
            return 0
        return (self.grade_runs * GRADE_RIGHTS_PER_RUN + self.rehearsal_runs * REHEARSAL_RIGHTS_PER_RUN) * self.grade_bulk


@dataclasses.dataclass(frozen=True)
class Plan:
    status: str                        # "ok" | "rights_unknown" | "refused" | "idle"
    available: int = None              # usable current rights (None: unknown)
    match: int = None                  # planned skips (None: decided at run time from the Match page reading)
    grade: int = 0
    rehearsal: int = 0
    total: int = None                  # planned consumption (None when Match "all" is still open)
    recovery: dict = dataclasses.field(default_factory=dict)  # task -> tickets that WOULD be needed (never used now)
    errors: tuple = ()
    warnings: tuple = ()

    @property
    def refused(self):
        return bool(self.errors)

    def messages(self):
        return [MESSAGES.get(code, code) for code in (*self.errors, *self.warnings)]

    def to_dict(self):
        data = dataclasses.asdict(self)
        data["messages"] = self.messages()
        return data


def plan(allocation, observed=None, policy=None):
    """The day's split of the shared rights. ``observed``: a fresh rights reading (``Rights``/tuple/dict) or None."""
    policy = load_policy() if policy is None else policy
    if not allocation.order:
        return Plan("idle")
    errors, warnings = [], []
    if allocation.grade and allocation.grade_runs > 0 and allocation.rehearsal_runs > 0:
        errors.append("mixed_mode_not_supported")
    if allocation.grade and allocation.grade_bulk > 1:
        errors.append("bulk_not_sampled")
    for enabled, use, cap in ((allocation.match, allocation.match_recovery, allocation.match_recovery_cap),
                              (allocation.grade, allocation.grade_recovery, allocation.grade_recovery_cap)):
        reason = recovery_refusal(enabled and use, cap, policy)
        if reason and reason not in errors:
            errors.append(reason)
    match_all = allocation.match and allocation.match_mode == "all"
    grade_part = allocation.grade_demand()
    if match_all and grade_part and allocation.order.index("MatchSkip") < allocation.order.index("GradeFes"):
        errors.append("match_all_before_grade")

    rights = Rights.coerce(observed)
    available = rights.usable() if rights is not None else None
    if rights is not None and available is not None and rights.plus:
        warnings.append("plus_not_counted")
    bound = available if available is not None else RIGHTS_CAPACITY
    reserves = [r for on, r in ((allocation.match, allocation.match_reserve), (allocation.grade, allocation.grade_reserve))
                if on]
    reserve = max(reserves) if reserves else 0
    fixed_match = allocation.match_count if allocation.match and not match_all else 0
    need = grade_part + fixed_match
    match = fixed_match if allocation.match and not match_all else None
    if match_all and available is not None:
        match = max(0, min(allocation.match_count, available - grade_part - allocation.match_reserve))
        if match == 0:
            warnings.append("match_all_nothing_left")
    if not allocation.match:
        match = 0
    shortfall = need + (reserve if need else 0) - bound
    recovery = {}
    if shortfall > 0:
        caps = [(n, c) for n, on, c in (("MatchSkip", allocation.match and allocation.match_recovery,
                                         allocation.match_recovery_cap),
                                        ("GradeFes", allocation.grade and allocation.grade_recovery,
                                         allocation.grade_recovery_cap)) if on]
        if caps and sum(c for _, c in caps) >= shortfall:
            left = shortfall
            for name, cap in reversed(caps):    # the later task meets the shortfall first
                take = min(cap, left)
                if take:
                    recovery[name] = take
                    left -= take
        else:
            errors.append("over_allocated" if available is not None else "over_allocated_capacity")
    total = None if match is None else need + (match if match_all else 0)
    if errors:
        status = "refused"
    elif available is None:
        status = "rights_unknown"
        warnings.insert(0, "rights_unknown")
    else:
        status = "ok"
    return Plan(status, available, match, allocation.grade_runs if allocation.grade else 0,
                allocation.rehearsal_runs if allocation.grade else 0, total, recovery, tuple(errors), tuple(warnings))


def match_all_count(current, reserve, cap=MATCH_MAX_COUNT):
    """Run-time Match 「全部用完」 count from the Match page reading: ``min(cap, current - reserve)`` (0: nothing)."""
    _require(type(current) is int and type(reserve) is int and type(cap) is int, "参加权读数无效")
    return max(0, min(cap, current - reserve))
