"""本地六卡 SP 路线与已读格子的绑定；只规划，不生成坐标或输入许可。"""
from __future__ import annotations

import heapq
import json
from hashlib import sha256
from typing import Any, Mapping

from agent.core.true_end.models import CardData
from agent.core.true_end.skill_identity import strict_live_identity
from agent.core.wing_skill_panel import SLOT_INTENTS, classify_skill, normalise, petal_of, same_skill


def _team_ids(team: Mapping) -> list[tuple[str, str]]:
    p = team.get("p_card", team.get("P"))
    supports = team.get("s_cards", team.get("S")) or ()
    return [("p_card", str(p or "")), *[("unknown", str(c)) for c in supports],
            ("unknown", str(team.get("guest") or ""))]


def _local_texts(ids: set[str], *, root=None) -> dict[str, Mapping]:
    """仅从本地快照取六卡原文，且沿用快照 SHA256 合同。"""
    if not ids:
        return {}
    from agent.core.team_recommend import SCDB_ROOT, discover_snapshots

    found = {}
    for snapshot in discover_snapshots(root or SCDB_ROOT):
        folder = snapshot.get("dir")
        # discover_snapshots exposes the converted path even for older manifests.
        if folder is None:
            converted = snapshot.get("converted")
            if converted is None:
                continue
            folder = converted.parent
        manifest_path = folder / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in manifest.get("entries") or ():
            cid = str(entry.get("enza_id") or "")
            if cid not in ids or cid in found:
                continue
            try:
                path = (folder / entry["file"]).resolve()
                if not path.is_relative_to(folder.resolve()):
                    continue
                body = path.read_bytes()
                if sha256(body).hexdigest() != entry.get("sha256"):
                    continue
                raw = json.loads(body.decode("utf-8"))
                if str(raw.get("enzaId")) == cid:
                    found[cid] = raw
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if ids <= found.keys():
            break
    return found


def _panel_detail(panel, text: Mapping) -> dict:
    name = text.get("skillTitle") or text.get("name")
    description = text.get("skillDesc") or text.get("description")
    info = classify_skill(name, description)
    effects = panel.effects
    # The converter cross-checks text against effect JSON; unparsed/compound
    # appeals cannot become a single fixed multiplier merely from a text regex.
    expected_kind = {"live": "live_skill", "cap": "cap_up", "passive": "passive"}.get(panel.kind)
    if info["kind"] != expected_kind:
        info.update(kind=None, attributes=(), value=None, multiplier=None)
    compound_live = panel.kind == "live" and len(effects.get("components") or {}) > 1
    if compound_live:
        info.update(kind=None, attributes=(), value=None, multiplier=None)
    if info["kind"] is None:
        if panel.kind == "live":
            parts = effects.get("components") or {}
            if len(parts) == 1:
                attr, value = next(iter(parts.items()))
                info.update(kind="live_skill", attributes=(attr,), value=value, multiplier=value)
            elif effects.get("excellent", 0) > 0:
                value = effects["excellent"]
                info.update(kind="live_skill", attributes=(), value=value, multiplier=value)
            elif effects.get("heal_fraction", 0) > 0 and not parts:
                info.update(kind="live_skill", attributes=(), value=0.0, multiplier=0.0)
        elif panel.kind == "cap" and len(set(effects.values())) == 1:
            info.update(kind="cap_up", attributes=tuple(effects), value=next(iter(effects.values())))
        elif panel.kind == "passive":
            buffs = effects.get("buffs") or ({effects["attribute"]: effects.get("buff", 0)}
                                            if effects.get("attribute") else {})
            chance = effects.get("probability")
            if buffs and len(set(buffs.values())) == 1 and chance is not None:
                info.update(kind="passive", attributes=tuple(buffs),
                            value=next(iter(buffs.values())) * chance, condition=effects.get("condition"))
    return {**info, "name": normalise(name) if name else None,
            "description": normalise(description) if description else None,
            "cost": panel.sp_cost, "required_lb": panel.required_lb, "event": panel.event,
            "effects": dict(effects),
            "skill_identity": strict_live_identity(name, effects, unsupported=panel.unsupported)
                              if panel.kind == "live" else None,
            "unsupported": list(panel.unsupported)}


def load_sp_catalog(team: Mapping, owned: Mapping | None = None, *,
                    cards: Mapping[str, Any] | None = None,
                    raw_cards: Mapping[str, Mapping] | None = None) -> dict:
    """六卡离线资料。owned 接受清洗后的详情映射或 {details: 映射}。

    cards 是 numeric ID→CardData/字典；raw_cards 是可选原始 SCDB 记录。
    Lv/凸从 owned 原样保留；面板只按凸解锁，不默认满级满凸。
    原始名称缺失仍保留语义供规划，但不会假造技能名用于确认。
    """
    ids = _team_ids(team)
    if cards is None:
        from agent.core.team_recommend import load_library
        cards = load_library().get("cards") or {}
    if raw_cards is None:
        raw_cards = _local_texts({cid for _, cid in ids if cid})
    ownership = (owned or {}).get("details", owned or {})
    result = {"cards": {}, "unknowns": []}
    for source, cid in ids:
        if not cid:
            continue
        raw = cards.get(cid)
        if raw is None:
            result["unknowns"].append(f"{cid}:card_missing")
            continue
        try:
            card = raw if isinstance(raw, CardData) else CardData.from_dict(raw)
        except (ValueError, TypeError, KeyError) as exc:
            result["unknowns"].append(f"{cid}:card_invalid:{exc}")
            continue
        if card.card_id != cid:
            result["unknowns"].append(f"{cid}:identity_mismatch")
            continue
        texts = {}
        for p in (raw_cards.get(cid) or {}).get("cardPanels") or ():
            pid = f"scdb-{p.get('panelSlot')}"
            if str(p.get("skillTitle") or "").startswith("[MB]"):
                pid += "-mb"
            texts[pid] = p
        entry = dict(ownership.get(cid) or {})
        lb = entry.get("limit_break")
        if type(lb) is not int or not 0 <= lb <= 4:
            lb = None
            result["unknowns"].append(f"{cid}:limit_break_unknown")
        details = {}
        for panel in card.panels:
            d = _panel_detail(panel, texts.get(panel.id) or {})
            slot = (texts.get(panel.id) or {}).get("panelSlot")
            d["position"] = {"row": slot // 10, "column": slot % 10} if type(slot) is int else None
            d.update(card_id=cid, panel_id=panel.id, source=source,
                     unlocked_lb=panel.required_lb == 0 or (lb is not None and lb >= panel.required_lb))
            details[panel.id] = d
        topology = card.panel_topology
        contested = any(g.endswith(".sp_cost") for g in card.unknown_fields)
        result["cards"][cid] = {"source": source, "ownership": entry, "nodes": details,
                                "topology": dict(topology) if topology else None,
                                "costs_known": not contested}
        if not topology or not topology.get("complete"):
            result["unknowns"].append(f"{cid}:topology_incomplete")
        if contested:
            result["unknowns"].append(f"{cid}:cost_contested")
    # No screen relation is implied by these per-card paths. They help choose which
    # unknown petal to inspect, and cannot be handed to the executor.
    result["offline_routes"] = bind_sp_catalog(
        result, {}, {}, petal_cards={f"offline:{cid}": cid for cid in result["cards"]})["routes"]
    return result


def _matches(observed: Mapping, node: Mapping, slot: Mapping) -> bool:
    """信息栏的名称与已读语义一致；费用使用实读值，学习后的 0 不作身份特征。"""
    compared = False
    if observed.get("name") and node.get("name"):
        if not same_skill(observed["name"], node["name"]):
            return False
        compared = True
    kind = observed.get("kind")
    if kind:
        if kind != node.get("kind"):
            return False
        compared = True
    if observed.get("attributes"):
        if set(observed["attributes"]) != set(node.get("attributes") or ()):
            return False
    value = observed.get("value")
    if value is None:
        value = observed.get("multiplier")
    if value is not None and node.get("value") is not None:
        if abs(float(value) - float(node["value"])) > 0.001:
            return False
    cost = observed.get("cost")
    if type(cost) is not int or cost <= 0:
        cost = slot.get("cost")
    label_cost = slot.get("cost")
    if (type(cost) is int and cost > 0 and type(label_cost) is int and label_cost > 0
            and cost != label_cost):
        return False
    # Generic kind alone cannot identify a card or a node.
    return compared and bool(observed.get("name") or value is not None)


def bind_sp_catalog(catalog: Mapping, slots: Mapping, known: Mapping, *,
                    petal_cards: Mapping | None = None, learned=(), mastered_skills=()) -> dict:
    """绑定已有信息栏读数并计算完整未学习路线；不按槽位顺序推测花瓣。

    返回 graph/details 可接现有规划器。ID 默认 catalog:<card>:<panel>；
    只有唯一信息栏匹配才能改用已观察的屏幕 intent。screen_map 与
    screen_nodes 才是已绑定格子，学习前仍须读回名称、费用及 learnable。
    graph 中的其余节点纯属规划，不能直接执行。
    """
    cards = catalog.get("cards") or {}
    bindings = {p: cid for p, cid in (petal_cards or {}).items() if cid in cards}
    matches = {}
    # 2026-10-14 live: a learned node whose label this frame did not read (c08_r04, P-card 「Vocal上限UP」) still
    # identifies its node; without it the planner kept routing to the already-learned node. Binding only.
    learned_ids = set(learned)
    seen = {intent: {"slot": list(SLOT_INTENTS[intent]), "petal": petal_of(SLOT_INTENTS[intent]), "cost": 0, "state": "learned"}
            for intent, observation in known.items()
            if intent not in slots and intent in SLOT_INTENTS
            and (intent in learned_ids or observation.get("state") == "learned")}
    seen.update(slots)
    for _ in range(2):
        for intent, observation in known.items():
            slot = seen.get(intent) or {}
            petal = slot.get("petal")
            if not petal:
                continue
            candidates = [(cid, pid) for cid, c in cards.items()
                          if cid == bindings.get(petal, cid)
                          for pid, n in c["nodes"].items() if _matches(observation, n, slot)]
            # Cost may distinguish otherwise identical generic labels. A unique
            # name/semantic match uses the game's current cost even if the local cost differs.
            actual = slot.get("cost") or observation.get("cost")
            exact_cost = [(cid, pid) for cid, pid in candidates if cards[cid]["nodes"][pid]["cost"] == actual]
            if len(candidates) > 1 and exact_cost:
                candidates = exact_cost
            owners = {cid for cid, _ in candidates}
            if len(owners) == 1:
                cid = next(iter(owners))
                if cid not in bindings.values() or bindings.get(petal) == cid:
                    bindings[petal] = cid
            if len(candidates) == 1 and bindings.get(petal) == candidates[0][0]:
                matches[intent] = candidates[0]
    # A repeated ambiguous label must never alias two real slots to one local node.
    counts = {}
    for pair in matches.values():
        counts[pair] = counts.get(pair, 0) + 1
    matches = {i: pair for i, pair in matches.items() if counts[pair] == 1}
    screen_map = {f"catalog:{cid}:{pid}": intent for intent, (cid, pid) in matches.items()}
    mastered = set(mastered_skills)
    for cid, card in cards.items():
        for pid, node in card["nodes"].items():
            key = f"catalog:{cid}:{pid}"
            intent = screen_map.get(key, key)
            slot, observation = slots.get(intent) or {}, known.get(intent) or {}
            if (key in learned_ids or f"{cid}:{pid}" in learned_ids or intent in learned_ids
                    or observation.get("state") in ("learned", "delete")
                    or slot.get("state") == "learned"
                    or (slot.get("cost") == 0 and (observation.get("state") or slot.get("state")) not in ("open", "learnable"))):
                if node.get("skill_identity"):
                    mastered.add(node["skill_identity"])
    graph, details, routes, screen_nodes = {}, {}, [], {}
    learned = set(learned)
    for petal, cid in bindings.items():
        card = cards[cid]
        topology = card.get("topology") or {}
        if not topology.get("complete") or not card.get("costs_known"):
            continue
        nodes = card["nodes"]
        by_panel = {pid: screen_map.get(f"catalog:{cid}:{pid}", f"catalog:{cid}:{pid}") for pid in nodes}
        usable, done, costs = set(), set(), {}
        for pid, n in nodes.items():
            key = by_panel[pid]
            observation, slot = known.get(key) or {}, slots.get(key) or {}
            state = observation.get("state") or slot.get("state")
            is_done = (state in ("learned", "delete") or key in learned
                       or f"catalog:{cid}:{pid}" in learned or f"{cid}:{pid}" in learned
                       or (slot.get("cost") == 0 and state not in ("open", "learnable")))
            if not n["unlocked_lb"] or (n.get("event") and state not in ("open", "learnable", "learned", "delete")):
                continue
            if state == "locked":  # 「event」 (deep gold) is a not-yet-reachable gold node, planned like unreachable
                continue
            usable.add(pid)
            if is_done:
                done.add(pid)
            actual_cost = slot.get("cost")
            if type(actual_cost) is not int or actual_cost <= 0:
                actual_cost = observation.get("cost")
            duplicate = bool(n.get("skill_identity") and n["skill_identity"] in mastered and not is_done)
            costs[pid] = 0 if duplicate else actual_cost if type(actual_cost) is int and actual_cost > 0 else n["cost"]
            entry = {**n, "state": "learned" if is_done else state or "unobserved",
                     "cost": costs[pid], "duplicate_skill": duplicate,
                     "petal": petal, "planning_only": key not in slots}
            details[key] = entry
            if key in slots:
                screen_nodes[key] = entry
        adjacent = {pid: set() for pid in usable}
        for a, b in topology.get("edges") or ():
            if a in usable and b in usable:
                adjacent[a].add(b)
                adjacent[b].add(a)
        frontier = (set(topology.get("roots") or ()) | done) & usable
        heap = [(0 if pid in done else costs[pid], (pid,), pid) for pid in frontier]
        heapq.heapify(heap)
        best, visited = {}, set()
        def path_cost(path):
            history, total = set(mastered), 0
            for step in path:
                identity = nodes[step].get("skill_identity")
                total += 0 if step in done or (identity and identity in history) else costs[step]
                if identity:
                    history.add(identity)
            return total
        while heap:
            cost, path, pid = heapq.heappop(heap)
            state = (pid, frozenset(nodes[p].get("skill_identity") for p in path if nodes[p].get("skill_identity")))
            if state in visited:
                continue
            visited.add(state)
            best.setdefault(pid, (cost, path))
            for other in sorted(adjacent[pid]):
                if other not in path:
                    next_path = path + (other,)
                    heapq.heappush(heap, (path_cost(next_path), next_path, other))
        for pid, (cost, path) in best.items():
            key = by_panel[pid]
            requires = tuple(by_panel[p] for p in path[:-1] if p not in done)
            if pid in done:
                continue
            graph[key] = {"requires": requires, "cost": cost, "petal": petal, "blocked": False,
                          "path_cost": cost, "node_cost": costs[pid],
                          "skill_identity": nodes[pid].get("skill_identity"),
                          "duplicate_skill": details[key]["duplicate_skill"],
                          "state": details[key]["state"], "planning_only": details[key]["planning_only"]}
            if nodes[pid].get("kind") == "live_skill" and pid not in done and not details[key]["duplicate_skill"]:
                routes.append({"node_id": key, "card_id": cid, "name": nodes[pid].get("name"),
                               "multiplier": nodes[pid].get("multiplier"), "cost": cost,
                               "remaining_cost": cost, "node_cost": costs[pid], "requires": list(requires),
                               "path": [by_panel[p] for p in path if p not in done],
                               "petal": petal, "topology_confidence": topology.get("confidence"),
                               "planning_only": details[key]["planning_only"]})
    return {"bindings": bindings, "details": details, "screen_nodes": screen_nodes,
            "screen_map": screen_map, "graph": graph,
            "mastered_skills": sorted(mastered),
            "routes": sorted(routes, key=lambda r: (-(r.get("multiplier") or 0), r["cost"], r["node_id"])),
            "unknowns": list(catalog.get("unknowns") or ())}
