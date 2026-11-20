"""W.I.N.G. True End 离线机制推荐；不会连接游戏或执行编队。"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.true_end.growth import GrowthConfig
from agent.core.true_end.models import CardData, load_inventory
from agent.core.true_end.optimizer import SearchConfig, find_best_teams, scenario_from_dict
from agent.core.true_end.records import RunRecord, append_run_record


def input_template() -> dict:
    return {"schema_version": 1, "p_card": "请填写已审核P卡ID", "cards": [], "inventory": [],
            "growth": {"starting_stats": {"Vo": 50, "Da": 50, "Vi": 50, "Me": 50},
                       "starting_live_skills": [], "facility_levels": [1, 2, 3, 4],
                       "placement_probability": 1 / 6, "perfect_probability": .1,
                       "event_completion_probability": .75, "first_place_probability": .5},
            "search": asdict(SearchConfig()), "battle_scenarios": [],
            "notes": ["模板的起始属性/概率只是待替换的假设；请用实际开局读数和4个装备技能",
                      "cards从true_end_wiki离线解析JSON合并；P字段/面板前置路径需人工带出处审核",
                      "inventory每项含card_id/level/limit_break，Guest也必须持有",
                      "battle_scenarios为空时只给ESTIMATED成长推荐，不输出战斗概率"]}


def run_input(value: dict, extra_cards: list[dict] | None = None) -> dict:
    if value.get("schema_version") != 1:
        raise ValueError("input.schema_version must be 1")
    definitions = [*value.get("cards", []), *(extra_cards or [])]
    cards = {}
    for raw in definitions:
        card = CardData.from_dict(raw)
        if card.card_id in cards:
            raise ValueError(f"duplicate card definition: {card.card_id}")
        cards[card.card_id] = card
    inventory = load_inventory(value.get("inventory", []))
    growth = GrowthConfig.from_dict(value.get("growth", {}))
    cfg = SearchConfig.from_dict(value.get("search", {}))
    scenarios = tuple(scenario_from_dict(s) for s in value.get("battle_scenarios", ()))
    if len(scenarios) > 8:
        raise ValueError("at most eight explicit battle scenarios")
    result = find_best_teams(cards, inventory, value["p_card"], growth=growth, config=cfg,
                             scenarios=scenarios, baseline=value.get("baseline"))
    canonical = json.dumps({"schema_version": 1, "mode": "W.I.N.G.", "goal": "TrueEnd", "p_card": value["p_card"],
                            "cards": definitions, "inventory": value.get("inventory", []), "growth": asdict(growth),
                            "search": asdict(cfg), "scenarios": [asdict(s) for s in scenarios], "baseline": value.get("baseline")},
                           sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    result["data_snapshot_sha256"] = sha256(canonical).hexdigest()
    result["schema_version"] = 1
    result["guide"] = build_guide(result)
    return result


def build_guide(result: dict) -> list[str]:
    rec = result.get("recommendation")
    if not rec:
        return [result.get("reason", "数据未完成，请补足字段和持有绑定")]
    selection = rec["strategy"]
    policy = selection.get("evaluated_policy", selection["strategy"])
    if not rec["projection"]["route_feasible"]:
        return ["当前32周计划未满足季节粉丝门槛或50万目标；先修改行动表并重新评估。"]
    if policy.endswith("_FORCE"):
        return ["在已列出的属性/手牌/NPC假设下，S3、THE LEGEND、准决赛和决赛可直接争流1TA。",
                "FORCE仍是条件模拟结论；实际等级、技能、队伍或读数变化后须重算。"]
    if policy.endswith("_DUAL"):
        return ["双极按已装备手牌与两个培养属性比较流1/流2；育成周数已在两属性间分配。",
                "S4优先选择条件模拟更可靠的流行，保留50万粉丝路线的可行周数。"]
    if policy.endswith("_AGGRESSIVE"):
        return ["用已测试的AGGRESSIVE规则争流1，并按可见剩余HP捕捉LA；未达到无条件碾压证明。",
                "逐场查看低属性、弱buff和坏手牌情景；保留Wiki基础路线作为缺数据时的回退。"]
    return ["S3/S4优先在主属性有利且参加条件已满足的周争TA；先4万场，再进入需5万粉丝的增刊号。",
            "Middle准决赛：Vo流1可争流1，其余采用流2基线；决赛主Da/Vi在流1时争流1，其余争流2并观察流1LA。",
            "这是Wiki/NPC表支持的保守规则，TREND_AWARE目前共用SAFE；实际模拟策略与证据档位分别列出。"]


def render_report(result: dict) -> str:
    lines = ["# W.I.N.G. True End 离线推荐", "", f"状态：{result['status']}", ""]
    rec = result.get("recommendation")
    if not rec:
        lines += [result.get("reason", "数据不足"), "", "缺失卡/字段：", "", json.dumps(result.get("rejected_cards", {}), ensure_ascii=False, indent=2)]
        return "\n".join(lines) + "\n"
    projection = rec["projection"]
    lines += [f"P：{rec['p_card']}", f"Active：{' / '.join(rec['active'])}", f"Guest：{rec['guest']}", "",
              f"策略档位：{rec['strategy']['strategy']}；实际测试规则：{rec['strategy'].get('evaluated_policy', rec['strategy']['strategy'])}",
              f"ProxyTERating：{rec['ProxyTERating']:.2f}/100（ESTIMATED）；True End整局概率：尚未建立", "",
              "| 属性 | Raw期望 | 有效期望 | 上限 |", "|---|---:|---:|---:|"]
    for attr in ("Vo", "Da", "Vi", "Me"):
        lines.append(f"| {attr} | {projection['raw_stats'][attr]:.1f} | {projection['effective_stats'][attr]:.1f} | {projection['caps'][attr]:.1f} |")
    lines += ["", f"预计SP {projection['sp']:.1f}；目标完整路径 {projection['sp_required']:.1f}；已购买 {projection['skill_plan']['cost']:.1f}。",
              f"S1-S4粉丝：{', '.join(str(round(n)) for n in projection['fans_by_season'])}。", ""]
    battle = rec.get("battle", {})
    selected = rec["strategy"].get("evaluated_policy", rec["strategy"]["strategy"])
    profile = battle.get("policies", {}).get(selected)
    if profile:
        lines += ["条件战斗模拟：同时置信下界只反映抽样误差，不涵盖机制/成长误差。", "",
                  "| 场次 | 最差条件胜率 | 同时置信下界 |", "|---|---:|---:|"]
        for audition in ("s3_odotte", "legend", "semifinal", "final"):
            rows = [r for r in profile["rows"] if r["audition"] == audition]
            worst = min(s["win_probability"] or 0 for r in rows for s in r["scenarios"])
            lower = min(r["worst_lower_bound"] for r in rows)
            lines.append(f"| {audition} | {worst:.1%} | {lower:.1%} |")
    else:
        lines += ["战斗未模拟：需要显式judgment/NPC/反击/手牌情景。"]
    lines += ["", *[f"- {item}" for item in result["guide"]], "", "未采用机制与保守奖励说明：", "",
              *[f"- {w}" for w in projection.get("warnings", ())], "", "数据与投影缺口：", "",
              *[f"- {u}" for u in projection["unknowns"]], "", "成长假设：", "",
              *[f"- {a}" for a in projection["assumptions"]], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--database", type=Path, action="append", default=[], help="合并离线解析卡数据库")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="另写中文Markdown报告")
    parser.add_argument("--template", action="store_true", help="写配置模板，不运行搜索")
    parser.add_argument("--import-run", type=Path, help="导入人工观察RunRecord，仅写私有JSONL")
    args = parser.parse_args()
    try:
        if args.template:
            result = input_template()
        elif args.import_run:
            record = RunRecord.from_dict(json.loads(args.import_run.read_text(encoding="utf-8")))
            append_run_record(record, args.output)
            print("已追加人工观察RunRecord；未生成或训练胜率。")
            return 0
        else:
            if args.input is None:
                parser.error("需要--input，或选择--template/--import-run")
            databases = []
            for path in args.database:
                data = json.loads(path.read_text(encoding="utf-8"))
                databases.extend(data["cards"])
            result = run_input(json.loads(args.input.read_text(encoding="utf-8")), databases)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if args.report and not args.template:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_report(result), encoding="utf-8")
        print("已写配置模板。" if args.template else f"{result['status']}；输入/资源预算0；{args.output}")
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f"数据或配置未通过：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
