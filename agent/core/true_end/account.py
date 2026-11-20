"""Offline account binding. UI ownership is evidence; a card catalogue is not.

P cards have no support level. Their Ownership.level=1 is a structural sentinel,
never an observed level or a strength estimate. No filesystem or device access.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any, Mapping

from agent.core.card_id_map import CardIdMap, WIKI_ID
from .growth import GrowthConfig, panel_closure
from .models import ATTRIBUTES, STATS, CardData, LiveSkill, Ownership, extract_card_features, validate_team
from .optimizer import SearchConfig, scenario_from_dict


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode("utf-8")).hexdigest()


def build_account_input(profile: Mapping[str, Any], settings: Mapping[str, Any],
                        id_map: CardIdMap, cards: Mapping[str, CardData]) -> dict:
    """Return a machine-readable audit plus ``input`` for true_end_recommend.run_input.

settings: p_card (UI ID), primary_attribute, growth (explicit opening state),
optional baseline {active:[four UI IDs], guest:UI ID}, search/battle_scenarios.
Four live skills are explicit user-observed mechanisms, not inferred from art,
rarity or a terminal battle example. Known card skill IDs must match the database.
"""
    missing, excluded, paths = [], [], []
    result = {"status": "NEEDS_ACCOUNT_DATA", "missing": missing,
              "excluded_cards": excluded, "need_panel_paths": paths,
              "input": None, "true_end_probability": None,
              "input_budget": 0, "resource_budget": 0,
              "notes": ["P卡Ownership.level=1仅为结构哨兵，P卡没有S卡等级。",
                        "开局四技能按用户显式填写的当前装备机制绑定；未从卡图、稀有度或终局属性推断。"]}

    def issue(field, code, message):
        missing.append({"field": field, "code": code, "message": message})

    if not isinstance(profile, Mapping) or not isinstance(settings, Mapping):
        issue("profile/settings", "invalid_type", "账号档案与设置必须是对象。")
        return result
    prefs = profile.get("prefs")
    owned = prefs.get("owned_cards") if isinstance(prefs, Mapping) else None
    if not isinstance(owned, Mapping):
        issue("prefs.owned_cards", "required", "请在UI填写实际持有P/S卡，卡库不代表持有。")
        owned = {}
    lists = {}
    for key in ("p", "s"):
        values = owned.get(key)
        if not isinstance(values, list) or any(not isinstance(v, str) or not WIKI_ID.fullmatch(v)
                                               for v in values):
            issue(f"prefs.owned_cards.{key}", "invalid_ids", "必须提供实际持有卡的10位ID数组。")
            values = []
        elif len(set(values)) != len(values):
            issue(f"prefs.owned_cards.{key}", "duplicate_card", "持有卡ID不得重复。")
        lists[key] = values
    details = owned.get("details", {})
    if not isinstance(details, Mapping):
        issue("prefs.owned_cards.details", "invalid_type", "实际Lv/凸数必须是对象。")
        details = {}
    inventory, features, ui_targets = {}, {}, {}
    for kind, ids in lists.items():
        for ui_id in ids:
            field = f"prefs.owned_cards.details.{ui_id}"
            reasons = []
            target = id_map.true_end_id(ui_id)
            card = cards.get(target)
            if target is None:
                reasons.append("缺少逐字精确卡ID映射：" + id_map.unmapped_reason(ui_id))
            elif not isinstance(card, CardData):
                reasons.append("精确映射的卡尚未解析。")
            elif card.kind != kind.upper() or (ui_id[0] == "1") != (kind == "p"):
                reasons.append("持有列表、UI ID与卡库P/S种类不一致。")
            elif card.card_id != target or target not in (card.name, f"【{card.name}】{card.idol}"):
                # CardIdMap targets are wikiwiki titles; synthetic tests use this same contract.
                reasons.append("映射目标、卡名与偶像不一致。")
            entry = details.get(ui_id)
            if not isinstance(entry, Mapping):
                reasons.append("缺少实际凸数" + ("与S卡Lv。" if kind == "s" else "。"))
            else:
                lb = entry.get("limit_break")
                lv = entry.get("level") if kind == "s" else 1
                if isinstance(lb, bool) or not isinstance(lb, int) or not 0 <= lb <= 4:
                    reasons.append("实际凸数必须是0..4整数。")
                if isinstance(lv, bool) or not isinstance(lv, int) or not 1 <= lv <= 100:
                    reasons.append("S卡实际Lv必须是1..100整数。")
                if not reasons:
                    try:
                        ownership = Ownership(target, lb, lv)
                        f = extract_card_features(card, ownership)
                        if not f.ready or f.unsupported:
                            reasons.extend(f.unknowns + f.unsupported or (card.status,))
                        else:
                            inventory[target], features[target], ui_targets[ui_id] = ownership, f, target
                    except ValueError as error:
                        reasons.append(str(error))
            if reasons:
                excluded.append({"id": ui_id, "card_id": target, "field": field,
                                 "code": "unusable_card", "missing": list(reasons)})
    p_ui = settings.get("p_card")
    p_id = ui_targets.get(p_ui) if isinstance(p_ui, str) else None
    if p_ui not in lists["p"] or p_id not in features:
        issue("p_card", "required_usable_owned_p", "请指定实际持有、已解析且TE资格已核实的P卡ID与实际凸数。")
    primary = settings.get("primary_attribute")
    if primary not in ATTRIBUTES:
        issue("primary_attribute", "required", "请显式指定主属性Vo/Da/Vi。")
    p = features.get(p_id)
    support_idols = {f.card.idol for f in features.values()
                     if f.card.kind == "S" and (p is None or f.card.idol != p.card.idol)}
    if len(support_idols) < 5:
        issue("prefs.owned_cards.s", "five_distinct_idols", "需要除P偶像外至少五名不同偶像的可用S卡（四上场加Guest）。")
    growth_value = settings.get("growth")
    growth = None
    if not isinstance(growth_value, Mapping):
        issue("growth", "required", "请填写实际开局属性、SP、上限和四个装备技能。")
    else:
        required = ("starting_stats", "starting_sp", "starting_caps", "starting_live_skills")
        for key in required:
            if key not in growth_value:
                issue("growth." + key, "required", "必须填写实际开局值，不能使用终局数值或隐式默认。")
        if all(key in growth_value for key in required):
            try:
                if not isinstance(growth_value["starting_stats"], Mapping) or not isinstance(growth_value["starting_caps"], Mapping):
                    raise ValueError("开局属性与上限必须是Vo/Da/Vi/Me对象")
                if not isinstance(growth_value["starting_live_skills"], list):
                    raise ValueError("四个当前装备技能必须是数组")
                skills_raw = growth_value["starting_live_skills"]
                if any(not isinstance(s, Mapping) or not isinstance(s.get("id"), str)
                       or not s["id"] or not isinstance(s.get("components"), Mapping) for s in skills_raw):
                    raise ValueError("每个实际装备技能需要非空id与显式components对象")
                if len({s["id"] for s in skills_raw}) != 4:
                    raise ValueError("四个实际装备技能id不得重复")
                if "events_completed" in growth_value and (not isinstance(growth_value["events_completed"], list)
                    or any(not isinstance(e, str) or not e for e in growth_value["events_completed"])):
                    raise ValueError("已完成事件必须是非空字符串数组")
                growth = GrowthConfig.from_dict(growth_value)
                if any(growth.starting_stats[a] > growth.starting_caps[a] for a in STATS):
                    raise ValueError("实际开局属性不得超过实际开局上限")
                for skill in growth.starting_live_skills:
                    if skill.unsupported:
                        raise ValueError(f"装备技能{skill.id}含未实现机制")
                if settings.get("starting_skill_origin") == "initial":
                    attribute_skills = [s for s in growth.starting_live_skills if s.components]
                    healing = [s for s in growth.starting_live_skills if not s.components]
                    if (len(attribute_skills) != 3 or {tuple(s.components.items()) for s in attribute_skills}
                        != {((a, 1.0),) for a in ATTRIBUTES} or len(healing) != 1
                        or healing[0].heal_fraction != .05
                        or any(s.buff_amount or s.heal_fraction for s in attribute_skills)
                        or healing[0].buff_amount):
                        raise ValueError("显式初始装备组须为三属性各1倍与5%纯回血")
                else:
                    records = settings.get("learned_panels", [])
                    if not isinstance(records, list) or any(not isinstance(r, Mapping) for r in records):
                        raise ValueError("已学习记录必须是source_card/panel_id对象数组")
                    learned = {(r.get("source_card"), r.get("panel_id")) for r in records}
                    for raw_skill, skill in zip(growth_value["starting_live_skills"], growth.starting_live_skills):
                        source, panel_id = raw_skill.get("source_card"), raw_skill.get("panel_id")
                        source_feature = features.get(ui_targets.get(source))
                        if source_feature is None or (source, panel_id) not in learned:
                            raise ValueError(f"装备技能{skill.id}需要实际持有来源卡与显式已学习面板记录")
                        panel = next((v for v in source_feature.panels if v.id == panel_id), None)
                        if panel is None or panel.kind != "live" or LiveSkill.from_dict(panel.effects) != skill:
                            raise ValueError(f"装备技能{skill.id}与来源卡已解锁面板不一致")
                        if panel_closure(source_feature, panel_id, growth.events_completed) is None:
                            paths.append({"card_id": source_feature.card.card_id, "panel_id": panel_id,
                                          "message": "当前装备来源路径SP前置或事件证据未完整。"})
                            raise ValueError(f"装备技能{skill.id}需要完整面板路径")
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                growth = None
                issue("growth", "invalid_opening_state", "实际开局状态未通过校验：" + str(error))
    baseline = None
    if settings.get("baseline") is not None:
        try:
            raw = settings["baseline"]
            if not isinstance(raw, Mapping) or not isinstance(raw.get("active"), list) or len(raw["active"]) != 4:
                raise ValueError("基准队必须含四上场ID与一个Guest ID")
            active = tuple(features[ui_targets[k]] for k in raw["active"])
            guest = features[ui_targets[raw["guest"]]]
            validate_team(p, active, guest)
            baseline = {"active": [f.card.card_id for f in active], "guest": guest.card.card_id}
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            issue("baseline", "invalid_team", "基准六卡队未通过校验：" + str(error))
    search, scenarios = None, None
    try:
        search = dict(settings.get("search", {}))
        search.setdefault("strategies", [primary.upper()] if primary in ATTRIBUTES else [])
        SearchConfig.from_dict(search)
        scenarios = settings.get("battle_scenarios", [])
        if not isinstance(scenarios, list) or len(scenarios) > 8:
            raise ValueError("最多八个显式战斗情景")
        for scenario in scenarios:
            scenario_from_dict(scenario)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        issue("search/battle_scenarios", "invalid_settings", "搜索或情景设置未通过校验：" + str(error))
    if growth:
        for f in features.values():
            for panel in f.panels:
                if panel_closure(f, panel.id, growth.events_completed) is None:
                    paths.append({"card_id": f.card.card_id, "panel_id": panel.id,
                                  "message": "需要完整SP前置、事件条件与已实现机制；此路径不可计入。"})
    if not missing:
        payload = {"schema_version": 1, "p_card": p_id,
                   "cards": [asdict(cards[k]) for k in sorted(inventory)],
                   "inventory": [asdict(inventory[k]) for k in sorted(inventory)],
                   "growth": dict(growth_value), "search": search, "battle_scenarios": scenarios}
        if baseline:
            payload["baseline"] = baseline
        result.update(status="READY_ACCOUNT_INPUT", input=payload)
        result["binding_sha256"] = _digest({"input": payload,
            "starting_skill_origin": settings.get("starting_skill_origin"),
            "learned_panels": settings.get("learned_panels", []),
            "ui_mapping": ui_targets})
    return result


def build_reachability_report(result: Mapping[str, Any]) -> dict:
    """Describe optimizer projection without claiming guaranteed growth or TE odds."""
    rec = result.get("recommendation")
    if not isinstance(rec, Mapping) or not isinstance(rec.get("projection"), Mapping):
        return {"status": "NOT_PROJECTED", "guaranteed": False, "true_end_probability": None}
    p = rec["projection"]
    plan = p.get("skill_plan", {})
    return {"status": "EXPECTED_GROWTH_ONLY", "guaranteed": False,
            "true_end_probability": None, "effective_stats": p.get("effective_stats"),
            "caps": p.get("caps"), "expected_sp": p.get("sp"),
            "sp_path_cost": p.get("sp_required"), "purchased_path_cost": plan.get("cost"),
            "unlocked_panel_ids": plan.get("unlocked_panel_ids"),
            "fans_by_season": p.get("fans_by_season"), "route_feasible": p.get("route_feasible"),
            "unknowns": p.get("unknowns", []), "assumptions": p.get("assumptions", []),
            "note": "属性/上限/SP与粉丝路线为32周期望投影；尚未证明保证可达或整局TE成功率。"}
