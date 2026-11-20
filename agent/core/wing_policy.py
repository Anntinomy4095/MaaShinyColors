"""W.I.N.G. weekly decision engine (Produce P3 foundation): pure, deterministic, no input, no screen reading.

The first strategy is the user's Vo single-stat route:

* Season 1: Vo lesson every week; add one audition when the fans are short of the season gate.
* Season 2: Vo lessons, plus one 10k-fan audition.
* Season 3: one 40k and one 50k audition, Vo lessons otherwise.
* Season 4: 100k audition ×4 (as many as the 500k True End total still needs), otherwise free (Vo lesson).

Other archetypes (Da / Vi single, balanced) follow the wiki "recommended" route
and are marked ``source="wiki_unverified"``. Every decision carries the rule that fired and a plan summary so the run
log explains it.

``*_highest`` strategies (default ``vo_highest``; first supervised True End run, unit7 / Middle, 2026-10-04)
replace the fixed tier list with season fan targets (S1 1k, S2 10k, S3 50k, S4 500k):
take only the highest-fan audition that is unlocked (rank gate) and whose recommended primary stat is met; prefer
weeks where the primary trend rank is 1–2, allow rank 3 when primary >= ``trend3_stat_factor`` × recommended
(observed passes at ~1.7×); S1 may reach its target through work fans instead of an audition; S2/S3 may bank one
extra audition on a favourable week. They also use a 15 % trouble-rate rest threshold.

Order of rules (first match wins):

1. stop: invalid / unrecognised / low-confidence state, unknown stamina, unreachable fan plan, missing buttons;
2. semifinal / final: hand the audition over (``live_mode``: own policy by default; game Auto never, 1st required),
   SP spent first;
3. audition with no spare week (tight deadline);
4. accepted 約束 lesson due this week (trouble rate below ``promise_trouble_cap``);
5. audition due (deadline with slack) or favourable (trend);
6. fatigue guard (bad 体調; trouble rate >= limit, or stamina below threshold when the rate is unread) -> a room with
   a stamina-recovery support if one is shown, else rest;
7. mental floor (if the strategy sets one) -> work; S1 work for fans when the target is reachable that way;
8. lesson on the strategy attribute in the best room (stamina-recovery support > most supports > largest gain);
   when the primary stat is at its cap and SP remains -> ``spend_sp`` first instead.

Auditions and the semifinal/final carry ``pre_actions=("spend_sp",)``: spend SP (``wing_sp.plan_sp``) first.

強化週間 (``params["special_window"]``, set by the navigator from ``wing_score.special_window_active``): the season's
limited audition is a bonus planned by ``wing_special.season_plan`` in both modes (seasons the user left on in
``special_seasons``); no target ever counts on it. A planned one keeps the final week free (ordinary deadlines one
week earlier); in the final week it is entered once the season target is met (``special_audition_bonus`` /
``special_audition``) and otherwise only as the last chance when no ordinary audition can reach the target.

Score mode: strategies with ``decision="score"`` (``vo_score`` /
``da_score`` / ``vi_score``) or ``params["decision_mode"] == "score"`` rank every available action with
``wing_score.score_week`` and take the best. Hard guards stay rules: invalid / low-confidence state, semifinal/final
handoff, unread stamina *and* trouble rate, unreachable fan gate (stop), a tight deadline (force the best audition),
bad 体調 (rest unless tight), and a capped primary lesson (spend SP first). When the scorer cannot rank (no readable
primary-lesson gain), the rule table above runs instead and the plan records ``score_fallback``. The rule table is
kept unchanged as the regression path. W.I.N.G. has no お出かけ: an ``outing`` option invalidates the state.
"""
from __future__ import annotations

import dataclasses
import math

from agent.core import wing_score as wsc
from agent.core import wing_state as ws

GOALS = ("true_end", "fans")
# "policy": the agent plays every live itself (wing_audition); "game_auto": the user's explicit opt-in to the game's
# Auto, still gated per live by wing_audition.should_use_game_auto (never when 1st place is required); "agent": legacy
# name of "policy".
LIVE_MODES = ("policy", "game_auto", "agent")
AUDITION_RULES = ("tiers", "highest_fans")
DECISION_MODES = ("rules", "score")

DEFAULT_PARAMS = {
    "min_confidence": 0.95,
    "rest_stamina": 50,          # absolute stamina below which a rest is preferred (体力≥50, tunable)
    "trouble_rest_rate": 5.0,    # % trouble rate on the preview -> rest (player note, unverified)
    "audition_slack": 1,         # spare weeks kept for one failed audition retry per season
    "opportunistic_trend_rank": 1,   # take a pending audition early when the primary attribute has this trend rank
    "final_primary_min": 450,    # readiness check before semifinal (wiki: 450–500)
    "final_mental_min": 300,
    # How auditions / W.I.N.G. lives are played (2026-10-05 user: the game's Auto plays randomly and does not
    # reliably take 1st). Default "policy": our own live policy. "game_auto" is an explicit opt-in and is applied per
    # live only when wing_audition.should_use_game_auto allows it; the decision plan records the effective mode.
    "live_mode": "policy",
    # ---- highest_fans audition rule (first TE run, 2026-10-04) ----
    "season_fan_targets": {1: 1_000, 2: 10_000, 3: 50_000, 4: ws.TRUE_END_FANS},
    "audition_trend_max_rank": 2,     # favourable week: primary trend rank <= this
    "trend3_stat_factor": 1.6,        # ...or primary >= factor × recommended (passes observed at ~1.7× on trend 3)
    "max_auditions_per_season": {1: 1, 2: 2, 3: 2, 4: 6},
    "bank_seasons": (2, 3),           # take one extra favourable audition after the target is met (banks fans for S4)
    "work_fan_seasons": (1,),         # seasons whose target may be met by work fans instead of an audition
    # ---- weekly action ----
    "promise_trouble_cap": 20.0,      # promised lesson is kept below this trouble rate (%); 2026-10-08: 30 -> 20
                                      # (= wing_score.TROUBLE_HARD_CAP_MAX)
    "cap_spend_min_sp": 20,           # smallest panel node cost; below it a capped lesson still earns SP
    # 2026-10-29 user: 「接近500差不多就要sp学习了」. Caps are read on the SP panel (所持スキル); before the first SP
    # session of a Produce they are unknown, so an unread cap counts as this base value for the spend-SP-first rule
    # (the session then reads the real caps). The navigator sets None once an SP session ran (no weekly re-opening).
    "provisional_cap": 500,
    # ---- score mode ----
    "decision_mode": None,            # None: the strategy's own mode; "rules" / "score" override it
    "score_params": None,             # overrides for wing_score.ScoreParams (dict)
    "score_team": None,               # wing_score.TeamProfile (support stats / bonds / masteries), optional
    "score_sp_context": None,         # reviewed SP paths and terminal-value evaluator in PEP, optional
    "win_model": None,                # audition P(win) provider (wing_score.WinModel); None = built-in logistic
    # ---- 強化週間 limited auditions (wing_special) ----
    "special_window": False,          # an event window is open (the navigator sets it; pure callers default off)
    "special_seasons": (1, 2, 3, 4),  # Produce setting 「限定试镜」 S1–S4 (default all: 自动)
    "special_min_ratio": None,        # primary / 推奨 per Live mode (None: wing_special.DEFAULT_MIN_RATIO)
    "special_prep_weeks": 2,          # an unmet Me condition is prepared only when it fits in this many weeks
}


@dataclasses.dataclass(frozen=True)
class SeasonPlan:
    auditions: tuple = ()          # planned fan-reward tiers to win this season (multiset)
    shortfall_audition: bool = True    # add one audition when fans + planned rewards miss the season gate
    mental_floor: int | None = None    # work instead of lesson below this メンタル (None: no rule)


@dataclasses.dataclass(frozen=True)
class Strategy:
    name: str
    primary: str | None            # lesson attribute; None -> lowest of Vo/Da/Vi (balanced)
    seasons: dict
    s4_tier: int                   # extra tier added in season 4 when True End fans still fall short
    source: str
    note: str
    audition_rule: str = "tiers"   # "tiers": SeasonPlan.auditions; "highest_fans": season fan targets
    params: dict = dataclasses.field(default_factory=dict)   # strategy defaults, below user params
    decision: str = "rules"        # "rules": rule table below; "score": wing_score ranking (rules as fallback)

    def lesson_attribute(self, state: ws.WingState) -> str | None:
        if self.primary:
            return self.primary
        values = [(state.stats.get(a), i, a) for i, a in enumerate(ws.ATTRIBUTES)]
        if any(v is None for v, _, _ in values):
            return None
        return min(values)[2]


def _single(primary, source, note, s1=(), s2=(10_000,), s3=(50_000, 50_000),
            s4=(100_000, 100_000, 50_000, 50_000, 50_000), floors=(150, 200, 250, 300)):
    seasons = {i + 1: SeasonPlan(tiers, True, floors[i] if floors else None)
               for i, tiers in enumerate((s1, s2, s3, s4))}
    return Strategy(f"{primary.lower()}_single" if primary else "balanced", primary, seasons, 100_000, source, note)


STRATEGIES = {
    "vo_single": _single("Vo", "user_2026-10-03", "用户 unit4 Vo 单极：S1 Vo 课程(粉丝不足加试镜)、S2 +1万、S3 4万+5万、S4 10万×4",
                         s3=(40_000, 50_000), s4=(100_000,) * 4, floors=None),
    "da_single": _single("Da", "wiki_unverified", "wiki 推荐路线：S2 1万、S3 5万×2、S4 10万×2+5万×3；メンタル下限 150/200/250/300"),
    "vi_single": _single("Vi", "wiki_unverified", "同 da_single，主属性 Vi（wiki：THE LEGEND 流行 1 位较常见）"),
    "balanced": _single(None, "wiki_unverified", "课程取三围最低项；试镜路线同 wiki 推荐；双极/均衡易手牌冲突，默认需用户确认"),
}

# 2026-10-08: rest threshold 15 -> 10 % (UI default 10 %, options 5/10/15/20).
# 2026-10-25: the scorer also excludes stamina actions at or above it.
HIGHEST_FANS_PARAMS = {"trouble_rest_rate": 10.0, "rest_stamina": 30}


def _highest(primary: str, source: str, note: str) -> Strategy:
    seasons = {i: SeasonPlan((), False, None) for i in range(1, ws.SEASONS + 1)}
    return Strategy(f"{primary.lower()}_highest", primary, seasons, 100_000, source, note, "highest_fans",
                    dict(HIGHEST_FANS_PARAMS))


STRATEGIES.update({
    "vo_highest": _highest("Vo", "user_2026-10-04_te_run",
                           "首局 TE（unit7/Middle）：每季粉丝目标 1k/1万/5万/50万，只打已解锁且主属性达推奨的最高粉丝试镜；"
                           "流行 1–2 位优先，3 位需主属性≥1.6×推奨；S1 可靠工作粉丝；トラブル率≥10% 休息（首局实测用 15%）"),
    "da_highest": _highest("Da", "generalised_unverified", "同 vo_highest 规则，主属性 Da（未实测）"),
    "vi_highest": _highest("Vi", "generalised_unverified", "同 vo_highest 规则，主属性 Vi（未实测）"),
})


def _scored(primary: str, note: str) -> Strategy:
    base = _highest(primary, "user_2026-10-05_score_v1", note)
    return dataclasses.replace(base, name=f"{primary.lower()}_score", decision="score")


STRATEGIES.update({
    "vo_score": _scored("Vo", "打分器 v1：每周给全部可选行动打分取最高；硬守卫与 vo_highest 规则表作兜底"),
    "da_score": _scored("Da", "同 vo_score，主属性 Da（未实测）"),
    "vi_score": _scored("Vi", "同 vo_score，主属性 Vi（未实测）"),
})


@dataclasses.dataclass(frozen=True)
class WeeklyDecision:
    kind: str                      # lesson | work | rest | audition | handoff | stop
    rule: str
    option_id: str | None = None
    audition_id: str | None = None
    attribute: str | None = None
    reasons: tuple = ()
    plan: dict = dataclasses.field(default_factory=dict)
    pre_actions: tuple = ()        # e.g. ("spend_sp",): run wing_sp.plan_sp on the panel before this action

    def to_dict(self) -> dict:
        return {"kind": self.kind, "rule": self.rule, "option_id": self.option_id, "audition_id": self.audition_id,
                "attribute": self.attribute, "reasons": list(self.reasons), "plan": dict(self.plan),
                "pre_actions": list(self.pre_actions)}

    def explain(self) -> str:
        target = self.audition_id or self.option_id or "-"
        pre = f" after {'+'.join(self.pre_actions)}" if self.pre_actions else ""
        return f"{self.kind} {target}{pre} [{self.rule}] {'; '.join(self.reasons)} plan={self.plan}"


def _stop(rule, *reasons, plan=None):
    return WeeklyDecision("stop", rule, reasons=tuple(reasons), plan=plan or {})


def pending_auditions(state: ws.WingState, strategy: Strategy, goal: str) -> tuple[list[int | None], list[str]]:
    """Fan tiers still to win this season (None = 'any audition that covers the gate deficit') and notes.

    Season 4 with goal true_end is sized to the 500k total: planned tiers are trimmed when fewer suffice and extra
    ``s4_tier`` entries are added when they do not."""
    plan = strategy.seasons[state.season]
    pending = list(plan.auditions)
    for won in state.season_wins():
        if won in pending:
            pending.remove(won)
        elif pending:                        # a bigger / different win still counts: drop the smallest planned tier
            pending.remove(min(pending))
    notes = []
    gate_deficit = max(0, (state.season_gate or 0) - state.fans)
    if state.season == 4 and goal == "true_end":
        need = max(0, ws.TRUE_END_FANS - state.fans)
        pending.sort(reverse=True)
        kept, total = [], 0
        for tier in pending:
            if total >= need:
                break
            kept.append(tier)
            total += tier
        while total < need:
            kept.append(strategy.s4_tier)
            total += strategy.s4_tier
        if kept != pending:
            notes.append(f"s4_sized_to_true_end_need_{need}")
        pending = kept
        gate_deficit = max(gate_deficit, need)
    if plan.shortfall_audition and gate_deficit > sum(t or 0 for t in pending):
        pending.append(None)
        notes.append(f"shortfall_audition_for_{gate_deficit}")
    return pending, notes


def _pick_audition(state: ws.WingState, tier: int | None, deficit: int):
    """Available audition for a planned tier (exact reward, else the smallest reward above it); for tier None the
    smallest reward covering the deficit. Entry requirement must be known and met."""
    eligible = [a for a in state.auditions if a.available and a.min_fans is not None and a.min_fans <= state.fans]
    if tier is None:
        covering = [a for a in eligible if a.fan_reward >= deficit]
        return min(covering, key=lambda a: (a.fan_reward, a.id)) if covering else None
    exact = [a for a in eligible if a.fan_reward == tier]
    if exact:
        return min(exact, key=lambda a: a.id)
    above = [a for a in eligible if a.fan_reward > tier]
    return min(above, key=lambda a: (a.fan_reward, a.id)) if above else None


def _handoff(state, strategy, goal, params):
    primary = strategy.primary or max(ws.ATTRIBUTES, key=lambda a: state.stats.get(a) or 0)
    checks = {
        "fans_true_end": None if state.fans is None else state.fans >= ws.TRUE_END_FANS,
        "primary_min": (state.stats.get(primary) or 0) >= params["final_primary_min"],
        "mental_min": (state.stats.get("Me") or 0) >= params["final_mental_min"],
        "trend_primary_rank": None if state.trend is None else state.trend[primary],
    }
    if goal != "true_end":
        checks.pop("fans_true_end")
    failed = sorted(k for k, v in checks.items() if v is False)
    return WeeklyDecision("handoff", f"{state.phase}_audition_handoff", attribute=primary,
                          reasons=tuple(f"not_ready:{k}" for k in failed) or ("ready",),
                          plan={"checks": checks, "live_mode": params["live_mode"]}, pre_actions=_spend_sp(state))


def _spend_sp(state: ws.WingState) -> tuple:
    """Spend all SP before an audition / W.I.N.G. (user rule). Unknown SP still asks the executor to open the panel."""
    return () if state.sp == 0 else ("spend_sp",)


def _rank_key(option: ws.ScheduleOption, attribute: str | None):
    gain = option.preview.get(attribute, 0) if attribute else sum(option.preview.values())
    return (option.stamina_recovery, option.supports or 0, gain)


def _best_room(options, attribute: str | None):
    """(option, None) for the best room, or (None, reason). Ties on every key are ambiguous -> no choice."""
    if not options:
        return None, "absent"
    ranked = sorted(options, key=lambda o: (_rank_key(o, attribute), o.id), reverse=True)
    if len(ranked) > 1 and _rank_key(ranked[0], attribute) == _rank_key(ranked[1], attribute):
        return None, "ambiguous"
    return ranked[0], None


def _special_plan(state: ws.WingState, goal: str, params: dict, targets, primary: str | None) -> dict | None:
    from agent.core import wing_special as wsp
    return wsp.season_plan(state, goal, window=bool(params.get("special_window")), targets=targets,
                           seasons=tuple(params.get("special_seasons") or ()), primary=primary,
                           min_ratio=params.get("special_min_ratio"),
                           prep_weeks=int(params.get("special_prep_weeks") or 0), slack=params["audition_slack"])


def _fan_target_audition(state: ws.WingState, strategy: Strategy, goal: str, params: dict, primary: str):
    """highest_fans rule -> (decision or None, tight, plan, stop decision or None)."""
    from agent.core import wing_special as wsp
    season, fans, weeks_left = state.season, state.fans, state.weeks_left
    target = params["season_fan_targets"][season]
    if season == 4 and goal == "true_end":
        target = max(target, ws.TRUE_END_FANS)
    target = max(target, state.season_gate or 0)
    special = _special_plan(state, goal, params, params["season_fan_targets"], primary)
    planned = special if special and special.get("planned") else None
    if planned and planned.get("reserve") and weeks_left > 1:
        weeks_left -= 1                     # the final week stays free for the limited audition
    limited = next((a for a in state.auditions if wsp.is_special(a.id)), None)
    deficit = max(0, target - fans)
    stat = state.stats.get(primary)
    rank = state.trend[primary] if state.trend is not None else None
    notes = []

    def unlocked(a: ws.Audition) -> bool:
        return (a.available and state.rank_at_least(a.required_rank) is True
                and (a.min_fans is None or a.min_fans <= fans))

    def met(a: ws.Audition) -> bool:
        need = a.recommended.get(primary)
        return need is not None and stat >= need

    open_ = [a for a in state.auditions if unlocked(a) and not wsp.is_special(a.id)]
    if any(a.available and state.rank_at_least(a.required_rank) is None for a in state.auditions):
        notes.append("rank_unread_gate_skipped")
    candidates = [a for a in open_ if met(a)]
    best = max(candidates, key=lambda a: (a.fan_reward, a.id)) if candidates else None
    if open_ and not candidates:
        notes.append("recommended_primary_not_met")
        needed_now = math.ceil(deficit / max(a.fan_reward for a in open_)) if deficit else 0
        if deficit and needed_now >= state.weeks_left and params.get("te_last_chance", True):
            # Last chance (abandon only once TE is unreachable; live 2026-10-10 S4 3 weeks left,
            # 280k short): the strongest Live play may still win, so take the highest-reward open audition rather
            # than stopping while the route is still reachable.
            best = max(open_, key=lambda a: (a.fan_reward, a.id))
            notes.append("last_chance_below_recommendation")

    def favourable(a: ws.Audition) -> str | None:
        if rank is None:
            return None
        if rank <= params["audition_trend_max_rank"]:
            return f"trend_{primary}_rank_{rank}"
        need = a.recommended.get(primary) or 0
        if need and stat >= params["trend3_stat_factor"] * need:
            return f"trend_{primary}_rank_{rank}_stat_{stat}_ge_{params['trend3_stat_factor']}x_{need}"
        return None

    estimate = best.fan_reward if best else max((a.fan_reward for a in state.auditions
                                                 if a.available and not wsp.is_special(a.id)), default=0)
    needed = 0 if deficit == 0 else (math.ceil(deficit / estimate) if estimate else 1)
    plan = {"season": season, "week": state.week, "weeks_left": state.weeks_left, "fans": fans, "target": target,
            "deficit": deficit, "needed_auditions": needed, "best": best.id if best else None,
            "season_wins": len(state.season_wins()), "notes": notes, "strategy": strategy.name,
            "live_mode": params["live_mode"]}
    if special is not None:
        plan["special"] = wsp.summary(special)
        if weeks_left != state.weeks_left:
            plan["ordinary_weeks"] = weeks_left

    def take_limited(rule: str, *why):
        # the plan already checked the page (entry condition, win estimate and margin)
        if not (planned and planned.get("final_week") and limited is not None and unlocked(limited)):
            return None
        return WeeklyDecision("audition", rule, audition_id=limited.id, attribute=primary,
                              reasons=(f"reward_{limited.fan_reward}", f"limited_{planned['id']}") + why,
                              plan=plan, pre_actions=_spend_sp(state))

    # S1: work fans may cover the target (user: S1 Vo lessons, or work in the last weeks)
    if deficit and season in params["work_fan_seasons"]:
        works = [o for o in state.options if o.kind == "work" and o.available and o.fan_gain]
        if works:
            gain = max(o.fan_gain for o in works)
            work_weeks = math.ceil(deficit / gain)
            plan["work_weeks_needed"] = work_weeks
            if work_weeks <= weeks_left:
                plan["fan_route"] = "work"
                return None, False, plan, None

    if deficit:
        plan["fan_route"] = "audition"
        if needed > state.weeks_left:            # a reserved final week still counts here; tight/due use it less
            last = take_limited("special_audition_last_chance", f"needed_{needed}_weeks_left_{state.weeks_left}") \
                if limited is not None and limited.fan_reward >= deficit else None
            if last is not None:
                return last, True, plan, None
            return None, True, plan, _stop("fan_plan_unreachable",
                                           f"needed_{needed}_weeks_left_{state.weeks_left}", plan=plan)
        tight = weeks_left <= needed
        due = weeks_left <= needed + params["audition_slack"]
        if best is None:
            if tight:
                last = take_limited("special_audition_last_chance", *notes) \
                    if limited is not None and limited.fan_reward >= deficit else None
                if last is not None:
                    return last, True, plan, None
                return None, True, plan, _stop("no_audition_meets_recommendation", *notes, plan=plan)
            return None, False, plan, None
        why = favourable(best)
        if not (why or due):
            notes.append(f"waiting_for_trend_{best.id}")
            return None, False, plan, None
        reasons = (f"reward_{best.fan_reward}", why or "deadline")
        if not why:
            reasons += ("trend_unfavourable",)
        if best.recommended.get("Me") is not None and state.stats["Me"] < best.recommended["Me"]:
            reasons += ("mental_below_recommended",)       # passed anyway in the TE run; reported only
        rule = "audition_due" if (due and not why) else "audition_favourable"
        return (WeeklyDecision("audition", rule, audition_id=best.id, attribute=primary, reasons=reasons, plan=plan,
                               pre_actions=_spend_sp(state)), tight, plan, None)

    # target met: the planned limited audition of the final week, else bank one more favourable audition in S2/S3
    special_pick = take_limited("special_audition", "target_met") if planned and planned.get("bonus") else None
    if special_pick is not None:
        plan["fan_route"] = "special"
        return special_pick, True, plan, None         # final week: no later week to defer it to
    if (season in params["bank_seasons"] and best is not None
            and len(state.season_wins()) < params["max_auditions_per_season"].get(season, 0)):
        why = favourable(best)
        if why:
            plan["fan_route"] = "bank"
            return (WeeklyDecision("audition", "audition_bank_fans", audition_id=best.id, attribute=primary,
                                   reasons=(f"reward_{best.fan_reward}", why, "target_met_bank_for_s4"), plan=plan,
                                   pre_actions=_spend_sp(state)), False, plan, None)
    plan["fan_route"] = "done"
    return None, False, plan, None


def decide(state: ws.WingState, strategy: Strategy | str = "vo_single", goal: str = "true_end",
           params: dict | None = None) -> WeeklyDecision:
    """Choose this week's action. Never returns an action whose button is not uniquely available in ``state``.

    Audition / handoff decisions carry the effective ``live_mode`` (see ``_live_plan``)."""
    decision = _decide(state, strategy, goal, params)
    if decision.kind not in ("audition", "handoff"):
        return decision
    return _live_plan(state, decision)


def _live_plan(state: ws.WingState, decision: WeeklyDecision) -> WeeklyDecision:
    """Resolve the requested live mode through wing_audition.should_use_game_auto (user opt-in + safe margin)."""
    from agent.core import wing_audition as wa
    requested = decision.plan.get("live_mode", "policy")
    need_first = decision.kind == "handoff"           # semifinal / final: only 1st passes
    estimate = None
    audition = next((a for a in state.auditions if a.id == decision.audition_id), None)
    if audition is not None and decision.attribute in ws.ATTRIBUTES:
        estimate = wa.audition_win_estimate(wa.context_from_wing(state, audition, decision.attribute,
                                                                 need_first=need_first))
    use_auto, reason = wa.should_use_game_auto(need_first=need_first, estimate=estimate,
                                               user_opt_in=requested == "game_auto")
    plan = {**decision.plan, "live_mode": "game_auto" if use_auto else "policy", "live_mode_requested": requested,
            "live_mode_reason": reason}
    from agent.core import wing_special as wsp
    special = wsp.special_of(decision.audition_id)
    if special is not None:
        plan["live_special"] = {"id": special.id, "live": special.live, "rule": special.rule}
        if special.live == "game_auto" and not use_auto:
            plan.update(live_mode="game_auto", live_mode_reason="special_rule_unplayable")
    if estimate is not None:
        plan["audition_estimate"] = {k: estimate.get(k) for k in ("p_first", "p_pass", "plan", "source")}
    return dataclasses.replace(decision, plan=plan)


def _decide(state: ws.WingState, strategy: Strategy | str, goal: str, params: dict | None) -> WeeklyDecision:
    if isinstance(strategy, str):
        if strategy not in STRATEGIES:
            return _stop("unknown_strategy", strategy)
        strategy = STRATEGIES[strategy]
    params = {**DEFAULT_PARAMS, **strategy.params, **(params or {})}
    if goal not in GOALS:
        return _stop("unsupported_goal", goal)
    if params["live_mode"] not in LIVE_MODES:
        return _stop("unsupported_live_mode", str(params["live_mode"]))
    if strategy.audition_rule not in AUDITION_RULES:
        return _stop("unsupported_audition_rule", strategy.audition_rule)
    invalid = state.validate()
    if invalid:
        return _stop("state_invalid", *invalid)
    if state.confidence < params["min_confidence"]:
        return _stop("low_confidence", f"confidence_{state.confidence}")
    if state.phase in ("semifinal", "final"):
        return _handoff(state, strategy, goal, params)
    if state.phase != "week":
        return _stop("not_a_week_phase", state.phase)
    mode = params["decision_mode"] or strategy.decision
    if mode not in DECISION_MODES:
        return _stop("unsupported_decision_mode", str(mode))
    if mode == "score":
        scored = _decide_by_score(state, strategy, goal, params)
        if scored is not None:
            return scored
        ruled = _decide_rules(state, strategy, goal, params)
        return dataclasses.replace(ruled, plan={**ruled.plan, "score_fallback": params["_score_fallback"]})
    return _decide_rules(state, strategy, goal, params)


def _decide_rules(state: ws.WingState, strategy: Strategy, goal: str, params: dict) -> WeeklyDecision:
    """The rule table (rules 6–8 of the module docstring); regression path and score-mode fallback."""
    if state.stamina is None:
        return _stop("stamina_unread")

    attribute = strategy.lesson_attribute(state)
    primary = strategy.primary          # balanced: no primary -> no trend-based early audition
    fatigue = []
    if state.condition in ws.BAD_CONDITIONS:
        fatigue.append(f"condition_{state.condition}")
    if state.trouble_rate is not None:
        if state.trouble_rate >= params["trouble_rest_rate"]:
            fatigue.append(f"trouble_rate_{state.trouble_rate}")
    elif state.stamina < params["rest_stamina"]:
        fatigue.append(f"stamina_{state.stamina}_below_{params['rest_stamina']}")

    audition, tight, pending = None, False, None
    if strategy.audition_rule == "highest_fans":
        audition, tight, plan, stop = _fan_target_audition(state, strategy, goal, params, primary)
        if stop is not None:
            return stop
        if audition is not None and fatigue and not tight:
            plan["notes"].append("audition_deferred_for_fatigue")
            audition = None
        elif audition is not None and fatigue:
            audition = dataclasses.replace(audition, reasons=audition.reasons + ("deadline_overrides_fatigue",)
                                           + tuple(fatigue))
    else:
        audition, tight, pending, plan, stop = _tier_audition(state, strategy, goal, params, primary, fatigue)
        if stop is not None:
            return stop

    if audition is not None and tight:
        return audition

    promised = state.promise_due()
    if promised is not None:
        rate = state.trouble_rate
        if rate is None or rate < params["promise_trouble_cap"]:
            room, why = _best_room([o for o in state.options if o.kind == "lesson" and o.available
                                    and o.attribute == promised], promised)
            if room is not None:
                return WeeklyDecision("lesson", "promise_lesson", option_id=room.id, attribute=promised,
                                      reasons=(f"promise_{promised}",), plan=plan,
                                      pre_actions=_capped_pre(state, promised, room, params))
            plan.setdefault("notes", []).append(f"promise_room_{why}")
        else:
            plan.setdefault("notes", []).append(f"promise_skipped_trouble_{rate}")

    if audition is not None:
        return audition

    if fatigue:
        recovery = [o for o in state.options if o.available and o.stamina_recovery and o.kind in ("lesson", "work")
                    and not any(f.startswith("condition_") for f in fatigue)]
        if recovery:
            room, _ = _best_room([o for o in recovery if o.attribute == attribute] or recovery, attribute)
            if room is not None and not _at_cap(state, room.attribute, room, params):
                return WeeklyDecision(room.kind, "stamina_recovery_room", option_id=room.id, attribute=room.attribute,
                                      reasons=tuple(fatigue) + ("support_recovers_stamina",), plan=plan)
        rest = state.option("rest")
        if rest is None:
            return _stop("rest_needed_but_unavailable", *fatigue, plan=plan)
        return WeeklyDecision("rest", "fatigue_guard", option_id=rest.id, reasons=tuple(fatigue), plan=plan)

    floor = strategy.seasons[state.season].mental_floor
    if floor is not None and state.stats["Me"] < floor:
        work = [o for o in state.options if o.kind == "work" and o.available]
        work.sort(key=lambda o: (-(o.preview.get("Me") or 0), o.id))
        if work:
            return WeeklyDecision("work", "mental_floor", option_id=work[0].id, attribute=work[0].attribute,
                                  reasons=(f"mental_{state.stats['Me']}_below_{floor}",), plan=plan)

    if plan.get("fan_route") == "work" and state.weeks_left <= plan["work_weeks_needed"] + params["audition_slack"]:
        works = [o for o in state.options if o.kind == "work" and o.available and o.fan_gain]
        best = max(works, key=lambda o: (o.fan_gain, _rank_key(o, attribute), o.id))
        return WeeklyDecision("work", "work_for_season_fans", option_id=best.id, attribute=best.attribute,
                              reasons=(f"fan_gain_{best.fan_gain}", f"deficit_{plan['deficit']}"), plan=plan)

    if attribute is None:
        return _stop("lesson_attribute_unknown", plan=plan)
    room, why = _best_room([o for o in state.options if o.kind == "lesson" and o.available
                            and o.attribute == attribute], attribute)
    if room is None:
        return _stop("lesson_unavailable", attribute, why, plan=plan)
    if _at_cap(state, attribute, room, params):
        if state.sp is None:
            return _stop("sp_unread_at_cap", attribute, plan=plan)
        if state.sp >= params["cap_spend_min_sp"]:
            return WeeklyDecision("spend_sp", "primary_at_cap_spend_sp_first", attribute=attribute,
                                  reasons=_cap_reasons(state, (attribute,), None, params) + (f"sp_{state.sp}",),
                                  plan=plan, pre_actions=("spend_sp",))
        reasons = (f"{strategy.name}:{attribute}", "capped_lesson_earns_sp")
    else:
        reasons = (f"{strategy.name}:{attribute}",)
    no_more = not pending if strategy.audition_rule == "tiers" else plan.get("fan_route") == "done"
    rule = "free_time_lesson" if state.season == 4 and no_more else "strategy_lesson"
    if room.supports or room.stamina_recovery:
        reasons += (f"room_supports_{room.supports or 0}",) + (("room_stamina_recovery",) if room.stamina_recovery
                                                                else ())
    return WeeklyDecision("lesson", rule, option_id=room.id, attribute=attribute, reasons=reasons, plan=plan)


def _caps(state: ws.WingState, params: dict | None = None) -> dict:
    """Read caps, plus ``provisional_cap`` for each stat whose cap is unread (none when the navigator turned it off)."""
    caps = dict(state.stat_caps or {})
    base = (params or {}).get("provisional_cap")
    if isinstance(base, int):
        for a in ("Vo", "Da", "Vi", "Me"):
            caps.setdefault(a, base)
    return caps


def _cap_reasons(state, attributes, option, params) -> tuple:
    read = state.stat_caps or {}
    caps = _caps(state, params)
    return tuple(f"{a}_{state.stats[a]}" + (f"+{option.preview.get(a, 0)}" if option is not None else "")
                 + f"_cap_{caps[a]}" + ("" if a in read else "_provisional") for a in attributes)


def _at_cap(state: ws.WingState, attribute: str | None, room: ws.ScheduleOption, params: dict | None = None) -> bool:
    """Primary stat would hit its cap this week: stat >= cap - preview gain (unread cap -> ``provisional_cap``)."""
    caps = _caps(state, params)
    if attribute is None or attribute not in caps or not isinstance(state.stats.get(attribute), int):
        return False
    return state.stats[attribute] >= caps[attribute] - room.preview.get(attribute, 0)


def _capped_attributes(state: ws.WingState, option: ws.ScheduleOption, params: dict | None = None) -> tuple:
    """Stats this option's preview would push to or past their caps (unread cap -> ``provisional_cap``)."""
    caps = _caps(state, params)
    return tuple(a for a in ("Vo", "Da", "Vi", "Me") if isinstance(caps.get(a), int) and isinstance(state.stats.get(a), int)
                 and option.preview.get(a, 0) > 0
                 and state.stats[a] + option.preview[a] >= caps[a])


def _capped_pre(state, attribute, room, params=None) -> tuple:
    return ("spend_sp",) if _at_cap(state, attribute, room, params) and state.sp != 0 else ()


def _tier_audition(state, strategy, goal, params, primary, fatigue):
    """Legacy tier-list rule -> (decision or None, tight, pending, plan, stop or None)."""
    pending, notes = pending_auditions(state, strategy, goal)
    weeks_left = state.weeks_left
    deficit = max(0, (state.season_gate or 0) - state.fans)
    if state.season == 4 and goal == "true_end":
        deficit = max(deficit, ws.TRUE_END_FANS - state.fans)
    plan = {"season": state.season, "week": state.week, "weeks_left": weeks_left, "fans": state.fans,
            "deficit": deficit, "pending": pending, "notes": notes, "strategy": strategy.name,
            "live_mode": params["live_mode"]}
    if len(pending) > weeks_left:
        return None, True, pending, plan, _stop("fan_plan_unreachable", f"pending_{len(pending)}_weeks_left_{weeks_left}",
                                                plan=plan)
    tight = bool(pending) and len(pending) >= weeks_left
    due = bool(pending) and weeks_left <= len(pending) + params["audition_slack"]
    trend_ok = (state.trend is not None and primary is not None
                and state.trend[primary] <= params["opportunistic_trend_rank"])
    if pending and (due or trend_ok) and (not fatigue or tight):
        tier = pending[0]
        audition = _pick_audition(state, tier, deficit)
        if audition is not None:
            why = "deadline" if due else f"trend_{primary}_rank_{state.trend[primary]}"
            reasons = (f"tier_{tier or 'shortfall'}", f"reward_{audition.fan_reward}", why)
            if fatigue:
                reasons += ("deadline_overrides_fatigue",) + tuple(fatigue)
            if due and not trend_ok and state.trend is not None:
                reasons += ("trend_unfavourable",)
            return (WeeklyDecision("audition", "audition_due" if due else "audition_opportunistic",
                                   audition_id=audition.id, attribute=primary, reasons=reasons, plan=plan,
                                   pre_actions=_spend_sp(state)), tight, pending, plan, None)
        if tight:
            return None, True, pending, plan, _stop("planned_audition_unavailable", f"tier_{tier}", plan=plan)
        plan["notes"] = notes + [f"tier_{tier}_not_offered_this_week"]
    return None, tight, pending, plan, None


def fan_projection(strategy: Strategy | str = "vo_single") -> dict:
    """Planned fan rewards per season (assumes every planned audition is won once) — for docs/tests, not decisions."""
    strategy = STRATEGIES[strategy] if isinstance(strategy, str) else strategy
    total, out = 0, {}
    for season in range(1, ws.SEASONS + 1):
        total += sum(strategy.seasons[season].auditions)
        out[season] = total
    out["true_end_gap"] = max(0, ws.TRUE_END_FANS - total)
    out["extra_s4_auditions"] = math.ceil(out["true_end_gap"] / strategy.s4_tier)
    return out


def _decide_by_score(state: ws.WingState, strategy: Strategy, goal: str, params: dict) -> WeeklyDecision | None:
    """Score-mode weekly decision, or None to fall through to the rule table (scorer could not rank)."""
    primary = strategy.primary
    if primary is None:
        return _stop("score_mode_needs_primary", strategy.name)
    if state.stamina is None and state.trouble_rate is None:
        return _stop("stamina_unread")
    try:
        targets = dict(wsc.ScoreParams.from_overrides(params["score_params"]).season_fan_targets)
        special = _special_plan(state, goal, params, targets, primary)
        result = wsc.score_week(state, primary, goal=goal, params=params["score_params"],
                                team=params["score_team"], win_model=params["win_model"],
                                sp_context=params["score_sp_context"], preview_bounds=True, special=special)
    except ValueError as exc:
        return _stop("score_params_invalid", str(exc))
    if result.rule in ("no_candidate", "no_reference_gain"):
        if state.stamina is None:            # the rule table needs stamina; nothing safe to fall back to
            return _stop(f"score_{result.rule}", str(result.context.get("reason", "")))
        params["_score_fallback"] = result.rule
        return None
    ctx = dict(result.context)
    plan = {"mode": "score", "strategy": strategy.name, "live_mode": params["live_mode"], "context": ctx,
            "scores": [c.to_dict() for c in result.ranked],
            "excluded": [c.to_dict() for c in result.excluded], "score_rule": result.rule}
    if ctx.get("hard_plan_unreachable"):
        return _stop("fan_plan_unreachable", f"hard_need_{ctx.get('hard_need')}_weeks_left_{state.weeks_left}",
                     plan=plan)
    by_id = {c.id: c for c in result.ranked}
    best_aud = ctx.get("best_audition")
    if ctx.get("tight") and best_aud in by_id:
        return WeeklyDecision("audition", "score_tight_deadline_audition", audition_id=best_aud, attribute=primary,
                              reasons=("deadline", f"hard_need_{ctx.get('hard_need')}"), plan=plan,
                              pre_actions=_spend_sp(state))
    if state.condition in ws.BAD_CONDITIONS:
        rest = state.option("rest")
        if rest is None:
            return _stop("rest_needed_but_unavailable", f"condition_{state.condition}", plan=plan)
        return WeeklyDecision("rest", "score_condition_guard", option_id=rest.id,
                              reasons=(f"condition_{state.condition}",), plan=plan)
    bonus = ctx.get("special_bonus")
    if bonus in by_id:
        # 強化週間: the season target is met and the planned limited audition passed its checks (user setting 自动)
        return WeeklyDecision("audition", "special_audition_bonus", audition_id=bonus, attribute=primary,
                              reasons=("target_met", f"limited_{(ctx.get('special') or {}).get('id')}"), plan=plan,
                              pre_actions=_spend_sp(state))
    # user rule: primary at its cap with SP left -> raise the cap on the panel first, then re-score
    best_primary = max((o for o in state.options if o.kind == "lesson" and o.available and o.attribute == primary
                        and primary in o.preview), key=lambda o: (o.preview[primary], o.id), default=None)
    if best_primary is not None and _at_cap(state, primary, best_primary, params) and state.sp is not None             and state.sp >= params["cap_spend_min_sp"]:
        return WeeklyDecision("spend_sp", "primary_at_cap_spend_sp_first", attribute=primary,
                              reasons=_cap_reasons(state, (primary,), None, params) + (f"sp_{state.sp}",),
                              plan=plan, pre_actions=("spend_sp",))
    choice = result.choice
    top = sorted(((k, v) for k, v in choice.terms.items() if abs(v) >= 0.05), key=lambda kv: (-abs(kv[1]), kv[0]))
    reasons = (f"score_{choice.score:.1f}",) + tuple(f"{k}{v:+.1f}" for k, v in top[:4])         + tuple(f"unknown:{u}" for u in choice.unknown)
    rule = f"score_{result.rule}"
    if choice.kind == "audition":
        return WeeklyDecision("audition", rule, audition_id=choice.id, attribute=primary, reasons=reasons, plan=plan,
                              pre_actions=_spend_sp(state))
    option = next(o for o in state.options if o.id == choice.id)
    # 2026-10-14 user: whenever the chosen lesson/work would push any stat past its cap, raise the cap on the panel
    # first (the gain above the cap is lost), not only for the primary stat's own lesson.
    capped = _capped_attributes(state, option, params) if choice.kind in ("lesson", "work") else ()
    if capped:
        if state.sp is None:
            return _stop("sp_unread_at_cap", *capped, plan=plan)
        if state.sp >= params["cap_spend_min_sp"]:
            return WeeklyDecision("spend_sp", "primary_at_cap_spend_sp_first", attribute=capped[0],
                                  reasons=_cap_reasons(state, capped, option, params) + (f"sp_{state.sp}",),
                                  plan=plan, pre_actions=("spend_sp",))
        reasons += ("capped_lesson_earns_sp",)
    return WeeklyDecision(choice.kind, rule, option_id=choice.id, attribute=choice.attribute, reasons=reasons,
                          plan=plan)
