"""Fixed-team, source-driven S-card limit-break comparisons; no rarity bonus."""
from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from typing import Mapping

from .growth import GrowthConfig, estimate_team_growth, panel_closure
from .models import STATS, CardData, Ownership, extract_card_features
from .optimizer import STRATEGIES, SearchConfig, TeamCandidate, evaluate_team
from .battle import Scenario

SCORING_VERSION = "true-end-limit-break-v1"


def _hash(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode("utf-8")).hexdigest()


def compare_limit_breaks(cards: Mapping[str, CardData], inventory: Mapping[str, Ownership],
                         p_card: str, active: tuple[str, ...], guest: str, target_card: str, *,
                         strategy: str = "DA", growth: GrowthConfig,
                         config: SearchConfig, scenarios: tuple[Scenario, ...] = (),
                         fixed_level: int | None = None, allow_partial: bool = False) -> dict:
    """Evaluate exactly ten cases (five LB x two level modes), keeping all other cards.

Absent source values are NEEDS_DATA/null, never a zero-score card. FIXED_LEVEL
isolates LB gates; AT_LB_CAP also includes actual level-stat and skill growth.
No interpolation or search is performed, and every case reuses the same seed.
"""
    ids = (p_card, *active, guest)
    if len(active) != 4 or len(set(ids)) != 6 or any(k not in cards or k not in inventory for k in ids):
        raise ValueError("需要固定的已定义六卡队：一P、四上场S、一个Guest，均须提供Ownership。")
    if cards[p_card].kind != "P" or any(cards[k].kind != "S" for k in ids[1:]):
        raise ValueError("固定六卡队P/S类型不一致。")
    if len({cards[k].idol for k in ids}) != 6:
        raise ValueError("固定六卡队不能重复偶像。")
    if target_card not in ids[1:]:
        raise ValueError("比较目标必须是固定队伍中的S卡。")
    if strategy not in STRATEGIES or not isinstance(allow_partial, bool):
        raise ValueError("策略或allow_partial无效。")
    if fixed_level is not None and (isinstance(fixed_level, bool) or not isinstance(fixed_level, int) or fixed_level < 1):
        raise ValueError("fixed_level必须为显式正整数。")
    target = cards[target_card]
    if fixed_level is None:
        level = target.max_levels.get(0)
        fixed_level = level if level in target.levels else None
    setup = {"scoring_version": SCORING_VERSION, "p_card": p_card, "active": list(active), "guest": guest,
             "target_card": target_card, "strategy": strategy, "fixed_level": fixed_level,
             "growth": asdict(growth), "config": asdict(config),
             "scenarios": [asdict(s) for s in scenarios], "allow_partial": allow_partial,
             "cards": [asdict(cards[k]) for k in ids],
             "inventory": [asdict(inventory[k]) for k in ids]}
    common_hash = _hash(setup)
    modes = []
    for mode in ("FIXED_LEVEL", "AT_LB_CAP"):
        cases = []
        prior_plan = None
        for lb in range(5):
            level = fixed_level if mode == "FIXED_LEVEL" else target.max_levels.get(lb)
            case = {"limit_break": lb, "level": level, "status": "NEEDS_DATA",
                    "total_score": None, "model_score": None, "full_strength_score": None,
                    "delta_from_zero": None, "missing": [],
                    "comparison_sha256": common_hash, "seed": config.seed,
                    "trials_per_scenario": config.confirmation_trials,
                    "true_end_probability": None}
            cases.append(case)
            if level is None:
                case["missing"].append("缺少该凸上限等级或可验证的固定等级。")
                continue
            owned = dict(inventory)
            owned[target_card] = replace(inventory[target_card], limit_break=lb, level=level)
            case["case_sha256"] = _hash({"comparison": common_hash,
                "ownership": [asdict(owned[k]) for k in ids]})
            resolved, gaps = {}, []
            try:
                for key in ids:
                    f = extract_card_features(cards[key], owned[key])
                    is_guest = key == guest
                    essential = (cards[key].kind == "S" and (set(f.stats) != set(STATS)
                                                             or (f.owned_live is None and not is_guest))
                                 or cards[key].kind == "P" and cards[key].te_eligible is not True
                                 or cards[key].status == "PARSE_ERROR" or not cards[key].idol.strip()
                                 or cards[key].kind == "S" and owned[key].limit_break not in cards[key].max_levels)
                    if essential:
                        raise ValueError(f"{key}:必要属性/Lv上限/自身技能/TE资格未知：{', '.join(f.unknowns)}")
                    own_gaps = f.guest_unknowns if is_guest else f.unknowns  # Guest never plays its own Live.
                    gaps.extend(f"{key}:{v}" for v in (*own_gaps, *f.unsupported))
                    if cards[key].status == "PARTIAL" and not own_gaps:
                        gaps.append(f"{key}:卡页标记PARTIAL，未确认全字段完整")
                    resolved[key] = f
                locked, unavailable = [], []
                for key in ids:
                    f = resolved[key]
                    if cards[key].panel_topology is not None and not cards[key].panel_topology["complete"]:
                        gaps.append(f"{key}:面板邻接图尚未完整审核")
                    for panel in cards[key].panels:
                        if panel.required_lb > owned[key].limit_break:
                            locked.append(f"{key}:{panel.id}")
                        elif panel_closure(f, panel.id, growth.events_completed) is None:
                            unavailable.append(f"{key}:{panel.id}")
                            if not panel.prerequisites_known or panel.unsupported or panel.kind == "unknown":
                                gaps.append(f"{key}:未纳入面板路径或效果 {panel.id}")
                case.update(locked_panel_ids=locked, unavailable_panel_paths=unavailable,
                            target_support_stats=dict(resolved[target_card].stats),
                            target_owned_skill=(asdict(resolved[target_card].owned_live)
                                                if resolved[target_card].owned_live else None),
                            target_support_effects=[{"id": s.id, "kind": s.kind, "amount": amount}
                                                    for s, amount in resolved[target_card].support_effects])
                projection = estimate_team_growth(resolved[p_card], tuple(resolved[k] for k in active),
                                                   resolved[guest], strategy, growth)
                gaps.extend(projection.unknowns)
                case["gaps"] = sorted(set(gaps))
                case["coverage_gaps"] = case["gaps"]
                if gaps and not allow_partial:
                    case["missing"].extend(sorted(set(gaps)))
                    continue
                candidate = TeamCandidate(resolved[p_card], tuple(resolved[k] for k in active),
                                          resolved[guest], strategy, projection)
                evaluated = evaluate_team(candidate, scenarios, config, trials=config.confirmation_trials)
                def plan_record(projected, result, origin, replayed):
                    return {"origin_limit_break": origin, "replayed": replayed,
                            "model_score": result["rating"],
                            "target_path_cost": projected.sp_required,
                            "purchased_panel_ids": list(projected.skill_plan.unlocked_panel_ids),
                            "plan_sha256": _hash({"comparison": common_hash,
                                "panels": projected.skill_plan.unlocked_panel_ids,
                                "actions": [row["planned_action"] for row in projected.timeline],
                                "starting_skills": [asdict(s) for s in growth.starting_live_skills],
                                "target_path_cost": projected.sp_required})}
                options = [plan_record(projection, evaluated, lb, False)]
                greedy_score = evaluated["rating"]
                selected_origin = lb
                # Re-evaluate a real lower-LB purchase witness at this ownership.
                # Never copy/clamp its old score: current growth, SP and battles rerun.
                if prior_plan is not None and (prior_plan[1] != projection.skill_plan.unlocked_panel_ids
                                               or prior_plan[2] < projection.sp_required):
                    origin, purchased_ids, target_cost = prior_plan
                    try:
                        replayed = estimate_team_growth(resolved[p_card], tuple(resolved[k] for k in active),
                            resolved[guest], strategy, growth, replay_panel_ids=purchased_ids)
                        # Keep the old SP objective: dropping unpaid goals is not an LB benefit.
                        target_cost = max(target_cost, replayed.skill_plan.cost)
                        replayed = replace(replayed, sp_required=target_cost,
                            sp_readiness=1.0 if target_cost <= 0 else min(1.0, replayed.sp / target_cost))
                        alternative = evaluate_team(TeamCandidate(resolved[p_card],
                            tuple(resolved[k] for k in active), resolved[guest], strategy, replayed),
                            scenarios, config, trials=config.confirmation_trials)
                        options.append(plan_record(replayed, alternative, origin, True))
                        if alternative["rating"] > evaluated["rating"]:
                            projection, evaluated, selected_origin = replayed, alternative, origin
                    except ValueError as error:
                        case["failed_plan_replay"] = {"origin_limit_break": origin, "reason": str(error)}
                selected = next(o for o in options if o["origin_limit_break"] == selected_origin)
                prior_plan = (selected_origin, projection.skill_plan.unlocked_panel_ids, projection.sp_required)
                case.update(status="ESTIMATED_WITH_GAPS" if gaps else "ESTIMATED",
                    total_score=evaluated["rating"], model_score=evaluated["rating"],
                    score_scope="BEST_EVALUATED_FEASIBLE_MODEL_PLAN",
                    mechanism_coverage="PARTIAL" if gaps else "SUPPORTED_MODEL",
                    independent_greedy_score=greedy_score, evaluated_plans=options,
                    selected_plan_origin_lb=selected_origin, selected_plan_sha256=selected["plan_sha256"],
                    purchase_weeks={key: next(row["week"] for row in projection.timeline
                        if key in row["unlocked_panel_ids"]) for key in projection.skill_plan.unlocked_panel_ids},
                    growth_readiness=evaluated["readiness"],
                    effective_stats=dict(projection.effective_stats), caps=dict(projection.caps),
                    expected_sp=projection.sp, sp_path_cost=projection.sp_required,
                    purchased_path_cost=projection.skill_plan.cost,
                    learned_skills=[asdict(s) for s in projection.skill_plan.learned_skills],
                    learned_panel_ids=list(projection.skill_plan.unlocked_panel_ids),
                    fans_by_season=list(projection.fans_by_season), route_feasible=projection.route_feasible,
                    strategy=evaluated["strategy"], battle=evaluated["battle"],
                    assumptions=list(projection.assumptions), warnings=list(projection.warnings),
                    guaranteed_reachable=False)
            except (ValueError, KeyError, TypeError) as error:
                case["missing"].append(str(error))
        baseline = cases[0]["total_score"]
        previous = None
        nonmonotonic = []
        for case in cases:
            score = case["total_score"]
            if score is not None and baseline is not None:
                case["delta_from_zero"] = score - baseline
            if score is not None and previous is not None and score < previous[1] - 1e-9:
                nonmonotonic.append({"from_limit_break": previous[0], "to_limit_break": case["limit_break"],
                                     "score_delta": score - previous[1]})
            previous = (case["limit_break"], score) if score is not None else None
        modes.append({"mode": mode, "cases": cases,
                      "comparability": "ALL_CASES_EVALUATED" if all(c["total_score"] is not None for c in cases) else "INCOMPLETE_DATA",
                      "mechanism_coverage": "PARTIAL" if any(c.get("gaps") for c in cases) else "SUPPORTED_MODEL",
                      "nonmonotonic": nonmonotonic,
                      "review_note": "已重放可行低凸方案；剩余非单调须复核自动技能、成长、手牌与模拟噪声；未强制修分。" if nonmonotonic else None,
                      "difference_scope": "同等级仅凸数门槛解锁的面板/支援技能变化。" if mode == "FIXED_LEVEL"
                          else "凸数门槛之外，还包含各凸上限等级的属性/支援技能/自身牌成长。"})
    return {"status": "LIMIT_BREAK_COMPARISON", "scoring_version": SCORING_VERSION,
            "comparison_sha256": common_hash,
            "team": {"p_card": p_card, "active": list(active), "guest": guest},
            "target_card": target_card, "modes": modes, "true_end_probability": None,
            "notes": ["总分复用整队期望成长与条件战斗，不按稀有度或凸数加固定奖励。",
                      "分数为已评估可行方案的最佳模型分，包含低凸方案的重新执行；不是全局最优或完整现代卡强度。",
                      "固定其他五卡、上场顺序、开局四装备、路线与随机种子；不搜索换队。",
                      "缺少必要字段不给零分；期望属性/上限/SP和粉丝路线不等于保证可达或TE成功率。"],
            "input_budget": 0, "resource_budget": 0}
