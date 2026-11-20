"""固定六卡队的0–4凸离线整队评分对照，不连接游戏。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.true_end.growth import GrowthConfig
from agent.core.true_end.limit_break import compare_limit_breaks
from agent.core.true_end.models import CardData, load_inventory
from agent.core.true_end.optimizer import SearchConfig, scenario_from_dict


def run(value: dict[str, Any], target_card: str, *, extra_cards: list[dict] | None = None,
        fixed_level: int | None = None, allow_partial: bool = False) -> dict:
    """Existing true_end_recommend schema, plus required baseline; no team search."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("需要schema_version=1的True End推荐器输入对象。")
    baseline = value.get("baseline")
    if (not isinstance(baseline, dict) or not isinstance(baseline.get("active"), list)
        or len(baseline["active"]) != 4 or any(not isinstance(k, str) or not k for k in baseline["active"])
        or not isinstance(baseline.get("guest"), str) or not baseline["guest"]):
        raise ValueError("必须显式提供baseline.active四张S卡ID及baseline.guest；不搜索或推断队伍。")
    p_card = value.get("p_card")
    if not isinstance(p_card, str) or not p_card:
        raise ValueError("必须显式指定p_card。")
    definitions = value.get("cards", [])
    if not isinstance(definitions, list):
        raise ValueError("cards必须是已解析卡定义数组。")
    cards = {}
    for raw in [*definitions, *(extra_cards or [])]:
        card = CardData.from_dict(raw)
        if card.card_id in cards:
            raise ValueError("重复卡定义：" + card.card_id)
        cards[card.card_id] = card
    inventory_value = value.get("inventory")
    if not isinstance(inventory_value, list):
        raise ValueError("必须提供inventory数组及每张卡显式level/limit_break。")
    inventory = load_inventory(inventory_value)
    growth_value = value.get("growth")
    if not isinstance(growth_value, dict):
        raise ValueError("必须显式提供growth工程假设。")
    growth = GrowthConfig.from_dict(growth_value)
    config = SearchConfig.from_dict(value.get("search", {}))
    if len(config.strategies) != 1:
        raise ValueError("固定队对照需在search.strategies明确指定唯一策略，例如[DA]。")
    scenario_values = value.get("battle_scenarios", [])
    if not isinstance(scenario_values, list) or len(scenario_values) > 8:
        raise ValueError("battle_scenarios最多八个显式情景。")
    scenarios = tuple(scenario_from_dict(s) for s in scenario_values)
    result = compare_limit_breaks(cards, inventory, p_card, tuple(baseline["active"]), baseline["guest"],
        target_card, strategy=config.strategies[0], growth=growth, config=config,
        scenarios=scenarios, fixed_level=fixed_level, allow_partial=allow_partial)
    result["schema_version"] = 1
    result["source_scope"] = "输入JSON中的固定队伍与工程假设；不推断任何真实账号持有。"
    return result


def _display(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(result: dict) -> str:
    team = result["team"]
    lines = ["# 同卡0–4凸固定队评分对照", "",
        f"目标S卡：{result['target_card']}", f"P：{team['p_card']}",
        f"上场顺序：{' / '.join(team['active'])}；Guest：{team['guest']}", "",
        "这是输入文件中的工程假设对照，不是账号结果。总分是整队Proxy估计值；True End概率始终为null。",
        "ESTIMATED_WITH_GAPS仅评估已支持机制；NEEDS_DATA的分数保留null。",
        "ALL_CASES_EVALUATED只表示五个凸数都有数值，不代表完整机理、保证可达或完成整局模拟。", "",
        f"共同配置/队伍/四装备摘要：`{result['comparison_sha256']}`", ""]
    for mode in result["modes"]:
        label = "固定等级：隔离凸数解锁" if mode["mode"] == "FIXED_LEVEL" else "各凸上限等级：包含等级成长"
        lines.extend([f"## {label}", "", mode["difference_scope"], "",
            f"可比较性：{mode['comparability']}（仅数据可用覆盖）", "",
            "| 凸数 | 等级 | 状态 | 整队模型分 | 相对0凸差值 | 预计SP | 计划路径成本 | 已购路径成本 |",
            "|---|---:|---|---:|---:|---:|---:|---:|"])
        for case in mode["cases"]:
            lines.append("| " + " | ".join(_display(case.get(k)) for k in
                ("limit_break", "level", "status", "total_score", "delta_from_zero", "expected_sp",
                 "sp_path_cost", "purchased_path_cost")) + " |")
        lines.append("")
        for case in mode["cases"]:
            lines.extend([f"### {case['limit_break']}凸 / Lv {_display(case.get('level'))}", "",
                          f"状态：{case['status']}；种子：{case['seed']}；每情景trial：{case['trials_per_scenario']}。", ""])
            if case["status"] == "ESTIMATED_WITH_GAPS":
                lines.extend(["分数只覆盖下列缺口之外的已支持机制，不能据此排名完整卡性能。", ""])
            for label, key in (("缺失数据", "missing"), ("机制缺口", "gaps"),
                               ("未解锁面板", "locked_panel_ids"), ("前置/机制不可用路径", "unavailable_panel_paths")):
                items = case.get(key, [])
                if items:
                    lines.extend([label + "：", "", *["- " + _display(v) for v in items], ""])
            if case["total_score"] is not None:
                lines.extend(["| 属性 | 有效期望 | 上限 |", "|---|---:|---:|"])
                for a in ("Vo", "Da", "Vi", "Me"):
                    lines.append(f"| {a} | {_display(case['effective_stats'].get(a))} | {_display(case['caps'].get(a))} |")
                skills = " / ".join(s["id"] for s in case.get("learned_skills", []))
                lines.extend(["", f"已装备技能：{skills}",
                    f"所选方案源自{case['selected_plan_origin_lb']}凸；购买与SP已在当前凸数重新验证。完整强度分：null。",
                    "已学习面板：" + (" / ".join(case.get("learned_panel_ids", [])) or "无"),
                    "各季粉丝：" + " / ".join(_display(v) for v in case.get("fans_by_season", [])),
                    f"粉丝路线期望可行：{case.get('route_feasible')}；保证可达：False。", ""])
        if mode.get("nonmonotonic"):
            lines.extend([mode["review_note"], "", json.dumps(mode["nonmonotonic"], ensure_ascii=False), ""])
    lines.extend(["期望成长与条件战斗不是True End成功率；保留原始非单调结果，不按凸数加固定奖励。", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--cards", type=Path, action="append", default=[], help="附加已解析cards数据库，可重复")
    parser.add_argument("--target-card", required=True)
    parser.add_argument("--fixed-level", type=int)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.input.read_text(encoding="utf-8"))
        extra = []
        for path in args.cards:
            database = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(database, dict) or not isinstance(database.get("cards"), list):
                raise ValueError(f"{path.name}缺少cards数组")
            extra.extend(database["cards"])
        result = run(value, args.target_card, extra_cards=extra,
                     fixed_level=args.fixed_level, allow_partial=args.allow_partial)
        args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if args.markdown_output:
            args.markdown_output.write_text(render_report(result), encoding="utf-8")
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        print("逐凸比较数据或配置未通过：" + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
