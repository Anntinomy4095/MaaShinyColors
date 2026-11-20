"""Offline joint choice of current-SP panel purchases and one weekly action."""
from __future__ import annotations

from dataclasses import asdict, replace
from typing import Callable

from ..wing_state import WingState
from .models import integer
from .run_state import RunState, fingerprint
from .sp_value import PanelValueState, SPValueContext, best_panel_plan
from .terminal_value import TerminalEvaluator
from .week_actions import WeekActionModel, build_week_successors

MODEL_VERSION = "wing-preweek-action-q-v1"
WeekInputs = Callable[[RunState], tuple[WingState, WeekActionModel]]


def score_preweek(evaluator: TerminalEvaluator, before: RunState, inputs_for: WeekInputs,
                  *, provider_id: str, assumptions: tuple[str, ...], max_states: int = 64) -> dict:
    """Compare legal panel portfolios by their best actual weekly successor Q.

    ``inputs_for`` is a pure offline provider. For EACH paid snapshot it must rebuild
    its observation and transition model: especially previews after stat/cap changes
    and audition probabilities after skill/passive changes. An old frame cannot be
    rebound by just changing its ID. No native input or observation is performed here.

    Panel paths/equipment use the existing bounded SP search and four-slot heuristic.
    Only SP available before the week pays those paths. Future learning remains in the
    terminal evaluator, using each settled branch's remaining/newly earned SP.
    """
    evaluator.profile.bind(before)
    if not before.route_feasible or before.phase != "week":
        raise ValueError("joint learning/action scoring needs an active week")
    if not callable(inputs_for) or not isinstance(provider_id, str) or not provider_id:
        raise ValueError("joint scoring needs a pure input provider and explicit provider ID")
    if not assumptions or any(not isinstance(s, str) or not s for s in assumptions):
        raise ValueError("joint scoring assumptions must be explicit")
    budget = integer(before.sp, "current observed SP")
    panels = {f"{c.card.card_id}:{p.id}": p for c in evaluator.profile.cards for p in c.panels}
    already_paid = set(before.panels.learned_panel_ids)
    evaluated = {}

    def value(candidate: PanelValueState) -> float:
        key = fingerprint(asdict(candidate))
        if key not in evaluated:
            new_ids = tuple(sorted(set(candidate.learned_panel_ids) - already_paid))
            cost = sum(integer(panels[k].sp_cost, "panel SP") for k in new_ids)
            paid = replace(before, panels=candidate, sp=before.sp - cost)
            observation, model = inputs_for(paid)
            if not isinstance(observation, WingState) or not isinstance(model, WeekActionModel):
                raise ValueError("input provider must return WingState and WeekActionModel")
            successors = build_week_successors(evaluator.profile, paid, observation, model)
            scored = successors.score(evaluator, paid) if successors.actions else None
            evaluated[key] = {"state": paid, "new_panel_ids": new_ids, "spent_sp": cost,
                              "observation": observation, "successors": successors, "score": scored}
        report = evaluated[key]["score"]
        # All eligible terminal Q values are nonnegative. Unread/empty action sets
        # cannot beat a usable plan; they may still have usable panel descendants.
        return report["ranked"][0]["expected_value_pep"] if report is not None else -1.0

    context = SPValueContext(evaluator.profile.cards, before.panels, evaluator.profile.primary,
                             value, before.events_completed, max_states)
    plan = best_panel_plan(context, budget)
    selected = evaluated[fingerprint(asdict(plan.state))]
    baseline = evaluated[fingerprint(asdict(before.panels))]["score"]
    scored, paid = selected["score"], selected["state"]
    target = next((o for o in selected["observation"].options if scored and o.id == scored["choice"]), None)
    ranked, unknowns = [], set(plan.unknowns)
    for row in evaluated.values():
        report = row["score"]
        successors = row["successors"]
        unknowns.update(successors.unknowns)
        if report is not None:
            unknowns.update(report["unknowns"])
        ranked.append({"state_id": row["state"].id, "new_panel_ids": list(row["new_panel_ids"]),
            "spent_sp": row["spent_sp"], "sp_remaining": row["state"].sp,
            "week_action": report["choice"] if report else None,
            "expected_value_pep": report["ranked"][0]["expected_value_pep"] if report else None,
            "observation_id": successors.observation_id, "transition_model_id": successors.transition_model_id,
            "excluded": dict(successors.excluded)})
    ranked.sort(key=lambda row: (row["expected_value_pep"] is None,
        -(row["expected_value_pep"] or 0), row["spent_sp"], row["state_id"]))
    if not plan.search_complete:
        unknowns.add("preweek_panel_search_budget_limited")
    return {"label": "ESTIMATED_PREWEEK_ACTION_Q", "status": "READY" if scored else "BLOCKED_WEEK_INPUTS",
        "comparison_scope": "PREWEEK_PANEL_PORTFOLIOS_AND_WEEK_ACTIONS",
        "profile_id": evaluator.profile.id, "state_id": before.id, "terminal_model_id": evaluator.id,
        "search_id": fingerprint({"version": MODEL_VERSION, "terminal": evaluator.id, "state": before.id,
            "provider_id": provider_id, "assumptions": assumptions, "max_states": max_states}),
        "provider_id": provider_id, "choice": {"next": "learn_panels" if plan.purchases else "week_action",
            "purchases": list(plan.purchases), "spent_sp": plan.spent, "sp_remaining": paid.sp,
            "week_action": scored["choice"], "expected_value_pep": plan.value_pep,
            "week_action_kind": target.kind if target else "audition",
            "attribute": target.attribute if target else evaluator.profile.primary,
            "state_after_learning": paid.to_dict()} if scored else None,
        "delta_vs_no_learning_pep": plan.value_pep - baseline["ranked"][0]["expected_value_pep"] if scored and baseline else None,
        "baseline_no_learning": baseline, "selected_week": scored, "ranked_plans": ranked,
        "search": {"evaluated_panel_states": plan.evaluated_states, "complete": plan.search_complete,
                   "max_states": max_states}, "unknowns": sorted(unknowns), "assumptions": [*assumptions,
            "各面板方案先扣当前SP并应用效果，再由显式provider重建预览/概率；用同终局Q比较，未重复叠加SP或即时收益。",
            "新赚SP仅供后继阶段学习；不补回本周已截断收益。未学习方案始终纳入。",
            "只取已评估合法路径与既有四装备启发式；未穷举所有装备/未来随机轨迹，provider概率未校准。"],
        "true_end_probability": None}


def to_weekly_decision(report: dict):
    """Optional proposal adapter; callers re-observe after learning, never auto-chain.

    Does not install a policy hook, authorize input or resolve SP/Live native readers.
    """
    from ..wing_policy import WeeklyDecision

    choice = report.get("choice") if report.get("status") == "READY" else None
    audit = {"mode": "joint_preweek_q", "evaluation": report}
    if choice is None:
        return WeeklyDecision("stop", "joint_week_inputs_missing", plan=audit)
    if choice["purchases"]:
        return WeeklyDecision("spend_sp", "joint_preweek_learn_then_reobserve", plan=audit,
                              pre_actions=("spend_sp",))
    kind = choice["week_action_kind"]
    target = {"audition_id" if kind == "audition" else "option_id": choice["week_action"]}
    return WeeklyDecision(kind, "joint_preweek_best_week_action", **target,
                          attribute=choice["attribute"], plan=audit)
