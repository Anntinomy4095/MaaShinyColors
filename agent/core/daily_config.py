"""严格的日常计划合同；仅处理人工 JSON，不连接设备、不授权输入。"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


TASK_NAMES = ("shop", "training", "match_fes", "grade_fes")
ITEM_TYPES = ("basic_training_ticket", "fes_entry_ticket", "ordinary_item")
STATES = ("ready", "complete", "reset", "unknown")
# Rank mode for the daily shop: buy offer rank r whatever today's 本日限定 item is. Identity is then
# the OCR'd name, which must match between card and dialog at run time (agent/core/shop_flow.py).
ANY_DAILY_DISCOUNT = "any_daily_discount"


def _object(value: Any, keys: set[str], name: str, *, required: bool = True) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是对象")
    unknown = set(value) - keys
    missing = keys - set(value) if required else set()
    if unknown or missing:
        raise ValueError(f"{name} 字段错误：未知={sorted(unknown)}，缺少={sorted(missing)}")
    return dict(value)


def _int(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} 必须是{'正' if positive else '非负'}整数")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} 必须是布尔值")
    return value


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} 必须是非空且无首尾空格的字符串")
    return value


def _enum(value: Any, choices: tuple[str, ...], name: str) -> str:
    if value not in choices or not isinstance(value, str):
        raise ValueError(f"{name} 必须为 {choices} 之一")
    return value


def _array(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} 必须是数组")
    return value


def _unique(values: list, name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} 不得重复")


def load_json(path: str | Path) -> dict:
    """Reject duplicate fields and nonstandard numeric constants before parsing."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"JSON 字段重复：{key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"JSON 数值非法：{value}")

    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=pairs, parse_constant=constant)


@dataclass(frozen=True)
class DailyConfig:
    data: Mapping[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DailyConfig":
        data = _object(value, {"version", "premium_currency_allowed", "budgets", *TASK_NAMES}, "config")
        if _int(data["version"], "version") != 1:
            raise ValueError("仅支持日常配置 version=1")
        if _bool(data["premium_currency_allowed"], "premium_currency_allowed"):
            raise ValueError("付费货币始终禁止")
        budgets = _object(data["budgets"], {"coins", "basic_training_tickets", "fes_participation_rights"}, "budgets")
        for key, val in budgets.items():
            _int(val, f"budgets.{key}")

        shop = _object(data["shop"], {"enabled", "purchase_scope", "include_third", "coin_budget", "coin_reserve", "items"}, "shop")
        _bool(shop["enabled"], "shop.enabled")
        _enum(shop["purchase_scope"], ("daily_discount_only",), "shop.purchase_scope")
        _bool(shop["include_third"], "shop.include_third")
        _int(shop["coin_budget"], "shop.coin_budget")
        _int(shop["coin_reserve"], "shop.coin_reserve")
        items = _array(shop["items"], "shop.items")
        ranks, identities = [], []
        for raw in items:
            fields = {"offer_rank", "item_id", "item_type", "quantity", "max_unit_price"}
            if isinstance(raw, Mapping) and "currency" in raw:
                fields = fields | {"currency"}
            item = _object(raw, fields, "shop.item")
            rank = _int(item["offer_rank"], "offer_rank", positive=True)
            if rank not in (1, 2, 3) or (rank == 3 and not shop["include_third"]):
                raise ValueError("仅可配置前两项及显式开启的第三项")
            ranks.append(rank)
            identity = _str(item["item_id"], "item_id")
            _enum(item["item_type"], ITEM_TYPES, "item_type")
            _int(item["quantity"], "quantity", positive=True)
            _int(item["max_unit_price"], "max_unit_price", positive=True)
            if "currency" in item and item["currency"] != "money":
                raise ValueError("商店商品仅允许普通货币 currency=money（マニー）")
            if identity == ANY_DAILY_DISCOUNT:
                if (shop["purchase_scope"] != "daily_discount_only" or item["quantity"] != 1
                        or item.get("currency") != "money" or item["item_type"] != "ordinary_item"):
                    raise ValueError("any_daily_discount 仅限本日限定、数量1、正单价上限、currency=money、ordinary_item")
            else:
                identities.append(identity)
        _unique(ranks, "shop.offer_rank")
        _unique(identities, "shop.item_id")
        if shop["enabled"] and not {1, 2}.issubset(ranks):
            raise ValueError("启用商店需绑定前两项的明确商品身份")

        training = _object(data["training"], {"enabled", "card_id", "candidate_card_ids", "ticket_type", "ticket_quantity", "ticket_budget"}, "training")
        _bool(training["enabled"], "training.enabled")
        card_id = training["card_id"]
        if card_id is not None:
            _str(card_id, "training.card_id")
        candidates = _array(training["candidate_card_ids"], "candidate_card_ids")
        for card in candidates:
            _str(card, "candidate_card_id")
        _unique(candidates, "candidate_card_ids")
        if card_id is not None and candidates:
            raise ValueError("card_id 和候选集合只能选择一种")
        if training["enabled"] and card_id is None and not candidates:
            raise ValueError("启用训练需指定卡或明确候选集合")
        if training["ticket_type"] != "basic_training_ticket":
            raise ValueError("仅支持基础 training ticket")
        if _int(training["ticket_quantity"], "ticket_quantity") != 1:
            raise ValueError("训练恰好投入一张基础 training ticket")
        _int(training["ticket_budget"], "training.ticket_budget")

        match = _object(data["match_fes"], {"enabled", "mode", "stage_selection", "stage_id", "count", "participation_budget"}, "match_fes")
        _bool(match["enabled"], "match_fes.enabled")
        if match["mode"] != "skip":
            raise ValueError("Match Live 仅规划 skip，不自动重打或爬塔")
        _enum(match["stage_selection"], ("latest", "explicit"), "match_fes.stage_selection")
        stage_id = match["stage_id"]
        if match["stage_selection"] == "latest":
            if stage_id is not None:
                raise ValueError("latest 不得同时指定 stage_id")
        elif stage_id is not None:
            _str(stage_id, "match_fes.stage_id")
            if stage_id.lower() in ("auto", "any", "random"):
                raise ValueError("explicit 需明确 Area/Stage 身份")
        elif match["enabled"]:
            raise ValueError("启用 explicit Match Live 需明确 stage_id")
        _int(match["count"], "match_fes.count", positive=True)
        _int(match["participation_budget"], "match_fes.participation_budget")
        grade = _object(data["grade_fes"], {"enabled", "difficulty_id", "count", "participation_budget"}, "grade_fes")
        _bool(grade["enabled"], "grade_fes.enabled")
        difficulty = grade["difficulty_id"]
        if difficulty is not None:
            _str(difficulty, "grade_fes.difficulty_id")
            if difficulty.lower() in ("auto", "any", "random"):
                raise ValueError("GradeFes 需明确难度身份，不允许自动选择")
        elif grade["enabled"]:
            raise ValueError("启用 GradeFes 需明确 difficulty_id")
        _int(grade["count"], "grade_fes.count", positive=True)
        _int(grade["participation_budget"], "grade_fes.participation_budget")
        return cls(copy.deepcopy(data))


def _observations(value: Mapping[str, Any]) -> dict:
    data = _object(value, {"source", "inventory", *TASK_NAMES}, "observations", required=False)
    if data and data.get("source") != "manual":
        raise ValueError("观测仅接受 source=manual 的人工 JSON；不是实际识别")
    if "inventory" in data:
        inventory = _object(data["inventory"], {"coins", "basic_training_tickets", "fes_participation_rights", "match_fes_participation_rights", "grade_fes_participation_rights"}, "inventory", required=False)
        for key, val in inventory.items():
            _int(val, f"inventory.{key}")
    for name in TASK_NAMES:
        if name not in data:
            continue
        fields = {
            "shop": {"state", "offer_scope", "offers"},
            "training": {"state", "cards"},
            "match_fes": {"state", "resource_pool", "stage_list_complete", "available_stages"},
            "grade_fes": {"state", "resource_pool", "available_difficulties"},
        }[name]
        raw_task = data[name]
        if name in ("match_fes", "grade_fes"):
            if not isinstance(raw_task, Mapping):
                raise ValueError(f"observations.{name} 必须是对象")
            # Missing pool evidence remains unknown; it never implies a shared pool.
            raw_task = {"resource_pool": "unknown", **raw_task}
        task = _object(raw_task, fields, f"observations.{name}")
        if name in ("match_fes", "grade_fes"):
            _enum(task["resource_pool"], ("shared", "match", "grade", "unknown"), "resource_pool")
            data[name] = task
        _enum(task["state"], STATES, "state")
        if name == "shop":
            _enum(task["offer_scope"], ("daily_discount", "regular", "unknown"), "offer_scope")
            ranks, ids = [], []
            for raw in _array(task["offers"], "offers"):
                offer = _object(raw, {"offer_rank", "item_id", "item_type", "currency", "unit_price", "original_unit_price", "discount_evidence", "available_quantity", "purchased"}, "offer")
                ranks.append(_int(offer["offer_rank"], "offer_rank", positive=True))
                ids.append(_str(offer["item_id"], "item_id"))
                _enum(offer["item_type"], ITEM_TYPES, "item_type")
                _str(offer["currency"], "currency")
                _int(offer["unit_price"], "unit_price", positive=True)
                _int(offer["original_unit_price"], "original_unit_price", positive=True)
                _str(offer["discount_evidence"], "discount_evidence")
                _int(offer["available_quantity"], "available_quantity")
                _bool(offer["purchased"], "purchased")
                if offer["purchased"] and offer["available_quantity"] != 0:
                    raise ValueError("已购买售罄证据与剩余库存冲突")
            _unique(ranks, "offers.offer_rank")
            _unique(ids, "offers.item_id")
        elif name == "training":
            ids = []
            for raw in _array(task["cards"], "cards"):
                card = _object(raw, {"card_id", "card_type", "completed", "max_level", "trainable"}, "card")
                ids.append(_str(card["card_id"], "card_id"))
                _enum(card["card_type"], ("support_s",), "card_type")
                for key in ("completed", "max_level", "trainable"):
                    _bool(card[key], key)
            _unique(ids, "cards.card_id")
        elif name == "match_fes":
            _bool(task["stage_list_complete"], "stage_list_complete")
            ids, orders = [], []
            for raw in _array(task["available_stages"], "available_stages"):
                stage = _object(raw, {"stage_id", "stage_order", "skip_available", "participation_cost_per_run"}, "stage")
                ids.append(_str(stage["stage_id"], "stage_id"))
                orders.append(_int(stage["stage_order"], "stage_order", positive=True))
                _bool(stage["skip_available"], "skip_available")
                _int(stage["participation_cost_per_run"], "participation_cost_per_run", positive=True)
            _unique(ids, "available_stages.stage_id")
            _unique(orders, "available_stages.stage_order")
        else:
            ids = []
            for raw in _array(task["available_difficulties"], "available_difficulties"):
                difficulty = _object(raw, {"difficulty_id", "available", "participation_cost_per_run"}, "difficulty")
                ids.append(_str(difficulty["difficulty_id"], "difficulty_id"))
                _bool(difficulty["available"], "available")
                _int(difficulty["participation_cost_per_run"], "participation_cost_per_run", positive=True)
            _unique(ids, "available_difficulties.difficulty_id")
    return data


def plan_daily_tasks(config: DailyConfig | Mapping[str, Any], observations: Mapping[str, Any] | None = None) -> dict:
    """Build reservations for one report; reservations are not actual spending."""
    # Revalidate even manually constructed or subsequently mutated model instances.
    cfg = DailyConfig.from_dict(config.data if isinstance(config, DailyConfig) else config).data
    observed = _observations({} if observations is None else observations)
    reserved = {key: 0 for key in cfg["budgets"]}
    inventory_reserved = {key: 0 for key in observed.get("inventory", {})}
    results = []

    def result(name, status, reason, details=None):
        results.append({"task": name, "status": status, "reason": reason,
                        "input_authorized": False, "input_enabled": False,
                        "details": details or {}})

    def reserve(key, amount, task_budget, inventory_key=None):
        inventory = observed.get("inventory", {})
        inventory_key = inventory_key or key
        if inventory_key not in inventory:
            return "缺少明确库存观测"
        if amount > task_budget:
            return "超出任务独立预算"
        if reserved[key] + amount > cfg["budgets"][key]:
            return "超出全局共享预算（已扣除此前计划预留）"
        if inventory_reserved[inventory_key] + amount > inventory[inventory_key]:
            return "超出观测库存（已扣除此前计划预留）"
        reserved[key] += amount
        inventory_reserved[inventory_key] += amount
        return None

    for name in TASK_NAMES:
        task = cfg[name]
        if not task["enabled"]:
            result(name, "skip", "任务默认关闭或用户选择关闭")
            continue
        state = observed.get(name)
        if state is None:
            result(name, "stop", "缺少人工观测；需要重新观察并人工接管")
            continue
        if state["state"] == "complete":
            result(name, "skip", "已完成，无需投入")
            continue
        if state["state"] != "ready":
            result(name, "stop", "状态未知或更新重置；需要重新观察并人工接管")
            continue
        if name == "shop":
            if state["offer_scope"] != "daily_discount":
                result(name, "stop", "仅允许当天三样每日折扣商品；普通货架或来源不明不能替代")
                continue
            offers = {offer["item_id"]: offer for offer in state["offers"]}
            purchases, failure = [], None
            for item in sorted(task["items"], key=lambda entry: entry["offer_rank"]):
                if item["item_id"] == ANY_DAILY_DISCOUNT:
                    # Rank mode: the observed offer at this rank, whatever its identity.
                    offer = next((o for o in state["offers"] if o["offer_rank"] == item["offer_rank"]), None)
                else:
                    offer = offers.get(item["item_id"])
                if offer is None or offer["offer_rank"] != item["offer_rank"] or offer["item_type"] != item["item_type"]:
                    failure = "商品身份/类型/折扣优先序未与计划一致，不允许按坐标猜测"
                    break
                if offer["currency"] != "coins":
                    failure = "仅允许普通金币；货币不符或付费货币禁止"
                    break
                if offer["purchased"]:
                    continue
                if offer["original_unit_price"] <= offer["unit_price"]:
                    failure = "缺少有效折扣价格证据"
                    break
                if offer["unit_price"] > item["max_unit_price"] or item["quantity"] > offer["available_quantity"]:
                    failure = "单价或购买数量超出明确上限/库存"
                    break
                purchases.append({**item, "unit_price": offer["unit_price"],
                                  "total_price": offer["unit_price"] * item["quantity"],
                                  "discount_evidence": offer["discount_evidence"]})
            cost = sum(entry["total_price"] for entry in purchases)
            if not failure and not purchases:
                result(name, "skip", "所有指定商品已有明确购买完成证据，无需重复投入")
                continue
            if not failure and "coins" in observed.get("inventory", {}):
                if observed["inventory"]["coins"] - reserved["coins"] - cost < task["coin_reserve"]:
                    failure = "购买后的金币余额低于用户保留额度"
            failure = failure or reserve("coins", cost, task["coin_budget"])
            result(name, "stop" if failure else "planned", failure or "商品身份与折扣证据明确，前两项优先；仅产生金币预留", {} if failure else {"purchases": purchases, "coins": cost})
        elif name == "training":
            ids = [task["card_id"]] if task["card_id"] else task["candidate_card_ids"]
            cards = {card["card_id"]: card for card in state["cards"]}
            if any(card_id not in cards for card_id in ids):
                result(name, "stop", "指定卡或候选集合缺少明确观测")
                continue
            eligible = [cards[card_id] for card_id in ids if not cards[card_id]["completed"] and not cards[card_id]["max_level"] and cards[card_id]["trainable"]]
            if not eligible:
                if all(cards[card_id]["completed"] for card_id in ids):
                    result(name, "skip", "指定卡或全部候选已完成训练")
                else:
                    result(name, "stop", "卡片已满级或不可训练，拒绝投入")
                continue
            failure = reserve("basic_training_tickets", 1, task["ticket_budget"])
            result(name, "stop" if failure else "planned", failure or "从明确集合依配置顺序选择可训练 S 卡，恰好预留一张基础票", {} if failure else {"card_id": eligible[0]["card_id"], "ticket_type": "basic_training_ticket", "ticket_quantity": 1})
        else:
            allowed_pools = ("shared", "match") if name == "match_fes" else ("shared", "grade")
            pool = state["resource_pool"]
            if pool not in allowed_pools:
                result(name, "stop", "参赛权资源池缺少明确人工绑定或与模式不符；不推断共享，不使用恢复券")
                continue
            inventory_key = "fes_participation_rights" if pool == "shared" else f"{pool}_fes_participation_rights"
            if name == "match_fes":
                if task["stage_selection"] == "latest":
                    if not state["stage_list_complete"] or not state["available_stages"]:
                        result(name, "stop", "latest 需要完整人工关卡列表与明确全局顺序；不从局部列表猜测最新关")
                        continue
                    selected = max(state["available_stages"], key=lambda entry: entry["stage_order"])
                else:
                    selected = next((entry for entry in state["available_stages"] if entry["stage_id"] == task["stage_id"]), None)
                if selected is None or not selected["skip_available"]:
                    result(name, "stop", "指定 Area/Stage 缺少观测或 skip 不可用；不自动切换关卡、重打或爬塔")
                    continue
            else:
                selected = next((entry for entry in state["available_difficulties"] if entry["difficulty_id"] == task["difficulty_id"]), None)
                if selected is None or not selected["available"]:
                    result(name, "stop", "指定难度不可用或观测不符；不自动切换难度")
                    continue
            participation_cost = selected["participation_cost_per_run"]
            cost = task["count"] * participation_cost
            failure = reserve("fes_participation_rights", cost, task["participation_budget"], inventory_key)
            details = {"count": task["count"], "fes_participation_rights": cost,
                       "participation_cost_per_run": participation_cost,
                       "resource_pool": pool, "inventory_key": inventory_key}
            if name == "match_fes":
                details.update(mode=task["mode"], stage_selection=task["stage_selection"],
                               stage_id=selected["stage_id"], stage_order=selected["stage_order"])
            else:
                details["difficulty_id"] = task["difficulty_id"]
            result(name, "stop" if failure else "planned", failure or "按明确人工池绑定核验参赛权与统一预算；不证明实际共享，不自动恢复", {} if failure else details)
    return {"version": 1, "mode": "read_only_manual_plan", "observation_source": "manual",
            "input_authorized": False, "input_enabled": False, "premium_currency_allowed": False,
            "requires_screen_reobservation_before_execution": True,
            "limitations": "人工 JSON 计划，不是截图识别或 GUI 接入，不执行购买、训练或 Fes；不修改项目实时输入及库存政策；资源池为人工绑定，不证明实际游戏共享；不纳入参赛权恢复券或自动补充。",
            "reservations": reserved, "inventory_reservations": inventory_reserved, "tasks": results}
