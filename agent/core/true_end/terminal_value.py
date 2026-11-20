"""One explicit PEP objective over remaining growth and paired conditional WING battles.

Not a joint Produce simulation: future season auditions in GrowthConfig are conditional
success assumptions. No unspent-SP bonus and no conversion from ProxyTERating.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from random import Random
from typing import Mapping

from .battle import BattleSupport, ProducedIdol, Scenario, middle_audition, simulate_audition
from .growth import GrowthConfig, GrowthProjection, estimate_team_growth
from .fan_route import FanRoutePolicy
from .models import ATTRIBUTES, STATS, integer, number
from .optimizer import _battle_skill, _passive
from .run_state import RunProfile, RunState, fingerprint
from .sp_value import PanelValueState, SPValueContext

MODEL_VERSION = "wing-terminal-pep-v1"


@dataclass(frozen=True)
class TerminalScenario:
    scenario: Scenario
    semifinal_trend: tuple[str, ...]
    final_trend: tuple[str, ...]
    weight: float = 1

    def __post_init__(self):
        for trend in (self.semifinal_trend, self.final_trend):
            if len(trend) != 3 or set(trend) != set(ATTRIBUTES):
                raise ValueError("terminal trends must explicitly permute Vo/Da/Vi")
        number(self.weight, "scenario weight")


@dataclass(frozen=True)
class TerminalModel:
    stat_weights: Mapping[str, float]  # primary must be 1 PEP per point; other weights explicit
    wing_win_pep: float  # user/model assumption, not a calibrated probability scale
    scenarios: tuple[TerminalScenario, ...]
    seeds: tuple[int, ...]

    def __post_init__(self):
        if set(self.stat_weights) != set(STATS):
            raise ValueError("terminal objective needs all four stat weights")
        for a, weight in self.stat_weights.items():
            number(weight, f"weight.{a}")
        number(self.wing_win_pep, "wing_win_pep")
        if (not 1 <= len(self.scenarios) <= 6 or
                len({s.scenario.name for s in self.scenarios}) != len(self.scenarios) or
                sum(s.weight for s in self.scenarios) <= 0):
            raise ValueError("terminal model needs 1..6 unique weighted scenarios")
        if not 1 <= len(self.seeds) <= 64 or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("terminal model needs 1..64 distinct paired seeds")
        for seed in self.seeds:
            integer(seed, "seed")


@dataclass(frozen=True)
class TerminalEvaluation:
    profile_id: str
    state_id: str
    model_id: str
    value_pep: float
    stat_pep: float
    wing_pep: float
    goal_eligible: bool
    projection: GrowthProjection
    conditional_battles: tuple[Mapping, ...]
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    label: str = "ESTIMATED_TERMINAL_PEP"
    true_end_probability: None = None
    fan_route: Mapping | None = None

    def summary(self) -> dict:
        result = asdict(self)
        if self.fan_route is None:
            result.pop("fan_route")
        return result


class TerminalEvaluator:
    """Same profile/objective for pregame next_week=0 and observed midrun snapshots."""

    def __init__(self, profile: RunProfile, growth: GrowthConfig, model: TerminalModel,
                 *, fan_route: FanRoutePolicy | None = None):
        if model.stat_weights[profile.primary] != 1:
            raise ValueError("primary weight must be 1 PEP per primary point")
        self.profile, self.growth, self.model = profile, growth, model
        self.fan_route = fan_route

    @property
    def id(self) -> str:
        return fingerprint({"version": MODEL_VERSION, "profile": self.profile.id,
                            "growth": asdict(self.growth), "model": asdict(self.model),
                            **({"fan_route": asdict(self.fan_route)} if self.fan_route is not None else {})})

    def evaluate(self, state: RunState, *, learn_panels: bool = True) -> TerminalEvaluation:
        self.profile.bind(state)
        cfg = state.growth_config(self.growth)
        if self.fan_route is not None:
            candidates = self.fan_route.candidates(cfg)
            evaluations = []
            for plan in candidates.plans:
                projected = estimate_team_growth(self.profile.p, self.profile.active, self.profile.guest,
                    self.profile.strategy, self.fan_route.apply(cfg, plan),
                    replay_panel_ids=None if learn_panels else ())
                evaluations.append((plan, self._evaluate_projection(state, projected, learn_panels)))
            viable = [(p, e) for p, e in evaluations if e.projection.route_feasible and e.projection.final_fans >= 500000]
            if viable:
                selected, result = min(viable, key=lambda item: (-item[1].goal_eligible, -item[1].value_pep,
                                                               len(item[0].remaining_audition_weeks), item[0].id))
            else:
                selected, result = next(item for item in evaluations if not item[0].replaced_weeks)
            metadata = {"label": "ESTIMATED_CONDITIONAL_FAN_ROUTE",
                "scope": "DECLARED_FALLBACKS_AT_ORIGINAL_AUDITION_SLOTS",
                "status": ("TE_GOAL_FEASIBLE" if result.goal_eligible else
                           "FAN_ROUTE_FEASIBLE_GOAL_UNVERIFIED_OR_INELIGIBLE" if viable else
                           "TE_FAN_ROUTE_INFEASIBLE" if candidates.search_complete else "NO_FEASIBLE_ROUTE_FOUND_WITHIN_BUDGET"),
                "selected_plan_id": selected.id, "replaced_weeks": selected.replaced_weeks,
                "remaining_audition_weeks": selected.remaining_audition_weeks,
                "evaluated_plans": len(evaluations), "total_possible": candidates.total_possible,
                "search_complete": candidates.search_complete,
                "plans": [{"plan_id": p.id, "replaced_weeks": p.replaced_weeks,
                    "remaining_audition_weeks": p.remaining_audition_weeks, "value_pep": e.value_pep,
                    "final_fans": e.projection.final_fans, "goal_eligible": e.goal_eligible,
                    "conditional_te_fan_route_feasible": e.projection.route_feasible and e.projection.final_fans >= 500000,
                    "wing_final_fan_requirement_met": e.projection.final_fans >= 100000} for p, e in evaluations]}
            unknowns = set(result.unknowns)
            if not candidates.search_complete:
                unknowns.add("fan_route_search_budget_limited")
            return replace(result, fan_route=metadata, unknowns=tuple(sorted(unknowns)),
                assumptions=(*result.assumptions, *self.fan_route.assumptions,
                    "未来保留试镜按声明通过条件重算；只比较原试镜槽的显式替代，未枚举全部操作/未来流行。",
                    "同seed挑选最佳已评估路线是模型样本内排序，非独立胜率验证或整局TE概率。"))
        projection = estimate_team_growth(self.profile.p, self.profile.active, self.profile.guest,
                                          self.profile.strategy, cfg,
                                          replay_panel_ids=None if learn_panels else ())
        return self._evaluate_projection(state, projection, learn_panels)

    def _evaluate_projection(self, state: RunState, projection: GrowthProjection,
                             learn_panels: bool) -> TerminalEvaluation:
        unknowns = set(projection.unknowns)
        if state.next_week < 32:
            unknowns.add("future_memory_level_progression_not_modeled")
        eligible = (projection.route_feasible and projection.final_fans >= 500000 and
                    self.profile.p.card.te_eligible is True)
        if self.profile.p.card.te_eligible is not True:
            unknowns.add("P:TrueEnd_eligibility_unverified_or_false")
        stat_pep = sum(self.model.stat_weights[a] * projection.effective_stats[a] for a in STATS)
        rows, wing_pep = [], 0.0
        if eligible and self.model.wing_win_pep:
            missing = [f"{c.card.card_id}:battle_stats_or_owned_live_unavailable"
                       for c in self.profile.active
                       if c.owned_live is None or not set(ATTRIBUTES) <= set(c.stats)]
            if projection.effective_stats["Me"] < 1:
                missing.append("P:battle_mental_below_one")
            if missing:
                unknowns.update(missing)
            else:
                # Guest remains bound by identity; none of its own battle data is used.
                supports = tuple(BattleSupport(c.card.card_id, {a: int(c.stats[a]) for a in ATTRIBUTES},
                                               _battle_skill(c.owned_live, c.card.card_id, support=True))
                                 for c in self.profile.active)
                supports += (BattleSupport(self.profile.guest.card.card_id, {}, None, True),)
                skills = tuple(_battle_skill(s, "P") for s in projection.skill_plan.learned_skills)
                passives = tuple(_passive(p, i) for i, p in enumerate(projection.skill_plan.passives))
                base = ProducedIdol({a: int(projection.effective_stats[a]) for a in ATTRIBUTES},
                                    int(projection.effective_stats["Me"]), skills,
                                    33 if state.phase == "final" else 32, passives, state.memory_level)
                for setting in self.model.scenarios:
                    if setting.weight == 0:
                        continue
                    runs = []
                    for seed in self.model.seeds:
                        rng = Random(seed)
                        semi = None
                        if state.phase != "final":
                            semi = simulate_audition(base, supports, middle_audition("semifinal", setting.semifinal_trend),
                                                     self.profile.strategy, rng, setting.scenario)
                        final = None
                        if semi is None or (semi.complete and semi.won):
                            final_idol = base
                            if semi is not None:
                                stats = {a: int(min(projection.caps[a], projection.effective_stats[a] + 20)) for a in STATS}
                                final_idol = replace(base, stats={a: stats[a] for a in ATTRIBUTES}, mental=stats["Me"], week=33)
                            final = simulate_audition(final_idol, supports, middle_audition("final", setting.final_trend),
                                                      self.profile.strategy, rng, setting.scenario)
                        last = final if final is not None else semi
                        runs.append({"seed": seed, "won": bool(final and final.complete and final.won),
                                     "complete": last.complete,
                                     "semifinal_rank": semi.rank if semi else None,
                                     "final_rank": final.rank if final else None,
                                     "failure_reasons": list(last.failure_reasons)})
                    wins = sum(r["won"] for r in runs)
                    incomplete = sum(not r["complete"] for r in runs)
                    rows.append({"scenario": setting.scenario.name, "weight": setting.weight,
                                 "semifinal_trend": list(setting.semifinal_trend), "final_trend": list(setting.final_trend),
                                 "phase": "final_only" if state.phase == "final" else "paired_semifinal_final",
                                 "trials": len(runs), "wins": wins, "incomplete": incomplete,
                                 "win_rate": wins / len(runs) if not incomplete else None,
                                 "value_lower_bound": wins / len(runs), "runs": runs,
                                 "assumptions": list(setting.scenario.assumptions)})
                    if incomplete:
                        unknowns.add(f"{setting.scenario.name}:conditional_battle_turn_limit")
                wing_pep = self.model.wing_win_pep * sum(r["weight"] * r["value_lower_bound"] for r in rows) / sum(r["weight"] for r in rows)
        unknowns.update(f"P:{s.id}:{u}" for s in projection.skill_plan.learned_skills for u in s.unsupported)
        assumptions = (*projection.assumptions,
                       "PEP=显式加权本战前属性+条件本战优胜权重；TE资格/粉丝/路线门槛不满足时目标值为0；权重未校准，未计结束奖励。",
                       "同一seed串联准决与决赛；准决失败不跑决赛，胜利+20四维受当前上限限制；不是两场胜率相乘。",
                       "final快照已含准决奖励，不再次添加；未来流行由显式情景提供，不使用未来录制帧。",
                       "未来思い出等级保持当前已知值，未模型化亲爱度增长；情景中的思い出时机是显式假设。",
                       "未花SP不单独计分。" + ("SP候选回调禁止后续自动购买，不重复使用当前预算。" if not learn_panels else "剩余成长按审核路径规划新购买，已购路径不重付。"))
        return TerminalEvaluation(self.profile.id, state.id, self.id,
                                  stat_pep + wing_pep if eligible else 0.0,
                                  stat_pep, wing_pep, eligible, projection, tuple(rows), assumptions,
                                  tuple(sorted(unknowns)))

    def sp_context(self, state: RunState, *, max_states: int = 128) -> SPValueContext:
        """Value a budget available AT this actual snapshot, with future purchases fixed.

        For SP earned by a weekly action use sp_context_after_action; changing only the
        week would lose that action's capped stats/fans/stamina/bond/event outcome.
        """
        self.profile.bind(state)
        def value(candidate: PanelValueState) -> float:
            return self.evaluate(replace(state, panels=candidate), learn_panels=False).value_pep
        return SPValueContext(self.profile.cards, state.panels, self.profile.primary, value,
                              state.events_completed, max_states)

    def sp_context_after_action(self, before: RunState, after: RunState, *, max_states: int = 128) -> SPValueContext:
        """Require the settled successor, including current-week overflow and gate result."""
        self._check_successor(before, after)
        return self.sp_context(after, max_states=max_states)

    def _check_successor(self, before: RunState, after: RunState) -> None:
        self.profile.bind(before)
        self.profile.bind(after)
        if before.phase != "week" or after.next_week != before.next_week + 1:
            raise ValueError("a weekly successor must consume exactly one actual week")
        if (not before.route_feasible and after.route_feasible or
                not set(before.panels.learned_panel_ids) <= set(after.panels.learned_panel_ids) or
                not set(before.events_completed) <= set(after.events_completed)):
            raise ValueError("successor cannot restore an eliminated run or forget purchased/completed IDs")

    def score_successors(self, before: RunState,
                         actions: Mapping[str, tuple[tuple[float, RunState], ...]]) -> dict:
        """One-step Q over explicit settled branches; no invented transition probabilities.

        Callers supply normal/Perfect/failure outcomes including all rewards/losses.
        This value already includes their stats, SP paths, fans and stamina: do not
        add wing_score's immediate terms again. Future weeks remain conditional priors.
        """
        self.profile.bind(before)
        if not before.route_feasible or before.phase != "week" or not 1 <= len(actions) <= 16:
            raise ValueError("successor scoring needs an active week and 1..16 actions")
        for key, branches in actions.items():
            if not isinstance(key, str) or not key or not 1 <= len(branches) <= 8:
                raise ValueError("each action needs a name and 1..8 explicit branches")
            probabilities = [number(p, "branch probability") for p, _ in branches]
            if abs(sum(probabilities) - 1) > 1e-9:
                raise ValueError("action branch probabilities must sum to one")
            for _, after in branches:
                self._check_successor(before, after)
        baseline = self.evaluate(before)
        ranked, unknowns = [], set(baseline.unknowns)
        for key, branches in actions.items():
            evaluations = [(p, self.evaluate(after)) for p, after in branches if p > 0]
            expected = sum(p * value.value_pep for p, value in evaluations)
            unknowns.update(u for _, value in evaluations for u in value.unknowns)
            ranked.append({"action": key, "expected_value_pep": expected,
                           "delta_pep": expected - baseline.value_pep,
                           "branches": [{"probability": p, "evaluation": value.summary()} for p, value in evaluations]})
        ranked.sort(key=lambda row: (-row["expected_value_pep"], row["action"]))
        return {"label": "ESTIMATED_SUCCESSOR_Q", "profile_id": self.profile.id, "model_id": self.id,
                "state_id": before.id, "baseline_value_pep": baseline.value_pep,
                "choice": ranked[0]["action"], "ranked": ranked, "unknowns": sorted(unknowns),
                "true_end_probability": None}
