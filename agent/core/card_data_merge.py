"""Field-by-field merge of ShinyColorsDB cards with Wiki-parsed cards, plus per-role readiness.

Pure and offline.  Inputs are ``CardData``-shaped dicts: the ShinyColorsDB converter output (keyed by the 10-digit
Enza ID) and the Wiki parser output (keyed by the page title).  Rules:

* The Enza ID stays the key.  A Wiki card is matched only by an exact (normalised) card name; ambiguity = no merge.
* Fields the DB lacks (per-level stats, ``max_levels``, the owned Live skill) come from the Wiki with Wiki provenance.
* Conflict policy: priority is decided per field and per evidence source, see ``FIELD_POLICY``.
  In-game verification ranks highest (``IN_GAME_STATS_VERIFIED`` and the converter's per-card SP readings).  One
  side is auto-selected only where the field's semantics and that source's reliability for it are established.
  Otherwise both values and both provenances are kept in ``merge.unresolved`` (``status: unresolved``) and the
  model field is unknown / marked unsupported: never a silent pick, especially for SP and per-level stats.
* SP costs are never resolved by agreement or by the Wiki: the converter sets them from in-game readings or the
  user-confirmed version rule; otherwise they stay contested.  Wiki SP values are recorded as evidence only.

Readiness (``role_readiness``) follows how ``agent.core.true_end`` consumes each field.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from agent.core import card_stats
from agent.core.support_effects import support_skill_expectation
from agent.core.true_end.models import SupportSkill

STATS = ("Vo", "Da", "Vi", "Me")
# Per-field conflict policy.  "auto" names the only automatic choice.
FIELD_POLICY = {
    "levels": "in-game > unresolved. DB cardStatus (max-level row) and Wiki tables both plausible, neither verified",
    "max_levels": "explicit card/Wiki caps first; absent SSR/SR/UR caps use the verified rarity rule at evaluation; conflicts stay unresolved",
    "owned_live_skills": "wiki only (DB supportFightSkills is a Fes skill, not the W.I.N.G. Live)",
    "support_skills": "auto: identical ladders, or Wiki missing steps of an equal ladder (coverage, not conflict); "
                      "differing ladders unresolved (skill marked unsupported)",
    "events": "unresolved on any difference",
    "panels.sp_cost": "converter evidence only (in-game reading > user rule > ordinary DB rule); Wiki = evidence",
    "panels.kind_lb": "DB 'unknown' vs Wiki interpreted = evidence only; both interpreted and differing = unresolved",
    "unit": "unresolved on difference",
    "te_eligible": "P only. Wiki only (DB has no field): True when the page states the True End conditions "
                   "(W.I.N.G.優勝 + アイドルランクA達成); otherwise unknown (never False by absence)",
}
# Owned Lv/LB read from the in-game list (agent.core.owned_levels): recognised, not confirmed by the user.
OWNED_LEVEL_SOURCE = "RECOGNISED_NOT_USER_CONFIRMED"
# In-game readings of per-level stats ({card_id: {level: {stat: value}}, "evidence": ...}); highest priority.
IN_GAME_STATS_VERIFIED: dict[str, dict[str, Any]] = {
    # サポートアイドル詳細 (third demo -03 #170): Lv 80/80, 4凸, foil +0 -> equals DB cardStatus and the Wiki Lv80 row.
    "2040010020": {"levels": {80: {"Vo": 206, "Da": 206, "Vi": 188, "Me": 150}},
                   "evidence": "in-game detail page 2026-10-06 (wing-demo3 -03 #170)"},
    # トレーニング大成功 popups (Lv1->2 on 2026-10-01, Lv3->4 on 2026-10-03).
    "2050170010": {"levels": {1: {"Vo": 65, "Da": 65, "Vi": 130, "Me": 60}, 2: {"Vo": 66, "Da": 66, "Vi": 132, "Me": 61},
                              3: {"Vo": 67, "Da": 67, "Vi": 135, "Me": 62}, 4: {"Vo": 69, "Da": 69, "Vi": 138, "Me": 63}},
                   "evidence": "in-game training result popups 2026-10-01 / 2026-10-03"},
}
# Gaps the True End extractor re-derives for the owned level / role; never counted twice.
DYNAMIC_GAPS = frozenset({"levels.per_level", "max_levels", "owned_live_skills", "te_eligible"})
# P fields written by both parsers but read by no True End module (grep of agent/core/true_end, 2026-10-07).
NOT_CONSUMED = frozenset({"initial_stats", "produce_events", "memory_appeal"})


def _norm_name(text: str) -> str:
    table = str.maketrans({"　": "", " ": "", "～": "~", "〜": "~", "！": "!", "？": "?", "＆": "&", "，": ","})
    return (text or "").translate(table).strip()


_DECOR = str.maketrans("", "", "♡♥♪☆★・･~")


def _loose_name(text: str) -> str:
    """NFKC name without decorative symbols (♡♪☆★・ and tildes): DB and Wiki titles differ there (e.g. DB
    【ビ～♡バップ海岸】 vs Wiki 【ビ～バップ海岸】, DB 【２８３プロのヒナ】 vs Wiki 【283プロのヒナ】).  Used only to bind a page fetched for a known Enza ID."""
    import unicodedata
    return unicodedata.normalize("NFKC", _norm_name(text)).translate(_DECOR)


def _levels(card: Mapping[str, Any]) -> dict[int, dict[str, float | None]]:
    return {int(k): dict(v) for k, v in (card.get("levels") or {}).items()}


def _max_levels(card: Mapping[str, Any]) -> dict[int, int]:
    return {int(k): int(v) for k, v in (card.get("max_levels") or {}).items()}


def _panel_gap(unknown: set[str], pid: str) -> bool:
    return f"panels.{pid}" in unknown or f"panels.{pid}.sp_cost" in unknown


def role_readiness(card: Mapping[str, Any], *, source_gaps: Iterable[str] = (),
                   limit_break: int | None = None, level: int | None = None) -> dict[str, Any]:
    """Per-role readiness of one CardData dict.

    Without an ownership the card is assessed at 4 limit breaks and that cap's level (the strictest panel set).
    Critical per role (how True End uses the field):
      active S: stats at the owned level (battle appeal + lesson correction), max_levels (level validation),
                the owned Live skill (battle hand), support skills, events, SP cost and reachability of every panel
                the owned limit break unlocks, the unit when a unit mastery skill exists.
      guest S:  the same minus the owned Live skill (the guest never enters the Live; battle skips guest).
      P:        te_eligible, SP cost and reachability of its panels.  Stats/events/memory are not consumed.
    """
    unknown = set(card.get("unknown_fields") or ())
    lb = 4 if limit_break is None else int(limit_break)
    caps, cap_rule = card_stats.resolve_level_caps(card)
    lv = level if level is not None else caps.get(lb)
    # Without the user's actual Lv/LB this is a hypothetical (strictest panel set); never "current" stats.
    ownership = "OWNED" if limit_break is not None and level is not None else "ASSUMED_LB4_CAP" if (
        limit_break is None and level is None) else "PARTIAL_ASSUMED"
    common: list[str] = []
    topology = card.get("panel_topology")
    for panel in card.get("panels") or ():
        if int(panel.get("required_lb", 0)) > lb:
            continue
        pid = str(panel["id"])
        if _panel_gap(unknown, pid):
            common.append(f"panels.{pid}.sp_cost")
        if topology is None and not panel.get("prerequisites_known"):
            common.append("panel_topology")
    common += sorted(u for u in unknown if u.startswith("panels.") and not u.endswith(".sp_cost")
                     and not any(str(p["id"]) == u.split(".", 1)[1] for p in card.get("panels") or ()))
    open_conflicts = [u for u in (card.get("merge") or {}).get("unresolved", ())
                      if u.get("critical") and u.get("status") != "non_blocking"]
    for item in open_conflicts:
        if not str(item["field"]).startswith("levels."):  # levels: checked per row below
            common.append(f"{item['field']} (unresolved)")
    if "panelSPOffset" in unknown:
        common.append("panelSPOffset")
    common = list(dict.fromkeys(common))
    non_critical = [g for g in dict.fromkeys([*source_gaps, *unknown]) if g not in DYNAMIC_GAPS
                    and not g.startswith(("panels.", "unresolved:")) and g != "panelSPOffset"]
    mechanics = sum(1 for p in card.get("panels") or () if p.get("unsupported") or p.get("kind") == "unknown")
    mechanics += sum(not _support_mechanism_modeled(s, card.get("rarity")) for s in card.get("support_skills") or ())
    result: dict[str, Any] = {"assumed": {"limit_break": lb, "level": lv, "ownership": ownership},
                              "ready_as_active": None,
                              "ready_as_guest": None, "ready_as_p": None, "critical_gaps": {},
                              "unmodeled_mechanics": mechanics}
    if cap_rule:
        result["level_cap_rule"] = cap_rule
    if card.get("kind") == "S":
        growth = list(common)
        if lv is None or caps.get(lb) is None:
            growth.append("max_levels")
        elif int(lv) > caps[lb]:
            growth.append(f"level {lv} exceeds the LB{lb} cap {caps[lb]}")
        else:
            row = _levels(card).get(int(lv))
            open_items = [u for u in open_conflicts if str(u["field"]).startswith(f"levels.{lv}.")]
            if row is None or any(row.get(a) is None for a in STATS):
                growth.append(f"levels.{lv}" + (" (unresolved)" if open_items else ""))
        if not card.get("support_skills"):
            growth.append("support_skills")
        if "events" in unknown:
            growth.append("events")
        if (any(str(s.get("kind", "")).startswith("unit_mastery_") for s in card.get("support_skills") or ())
                and not card.get("unit")):
            growth.append("unit")
        active = list(growth)
        lives = [s for s in card.get("owned_live_skills") or () if lv is None or int(s.get("unlock_level", 1)) <= lv]
        if not lives:
            active.append("owned_live_skills")
        non_critical = [g for g in non_critical if g not in ("events", "support_skills", "unit")]
        result.update(ready_as_active=not active, ready_as_guest=not growth,
                      critical_gaps={"active": active, "guest": growth})
        result["unknown_gaps"] = {k: [g for g in v if "(unresolved)" not in g] for k, v in result["critical_gaps"].items()}
        result["unresolved_gaps"] = {k: [g for g in v if "(unresolved)" in g] for k, v in result["critical_gaps"].items()}
    else:
        p_gaps = list(common)
        if card.get("te_eligible") is not True:
            p_gaps.append("te_eligible")
        result.update(ready_as_p=not p_gaps, critical_gaps={"p": p_gaps})
        result["unknown_gaps"] = {"p": [g for g in p_gaps if "(unresolved)" not in g]}
        result["unresolved_gaps"] = {"p": [g for g in p_gaps if "(unresolved)" in g]}
    result["non_critical_gaps"] = sorted(set(non_critical))
    derived = (card.get("merge") or {}).get("derived_levels") or {}
    result["derived_levels"] = {k: v.get("status") for k, v in derived.items()}
    result["non_blocking_conflicts"] = [u["field"] for u in (card.get("merge") or {}).get("unresolved", ())
                                        if not u.get("critical") or u.get("status") == "non_blocking"]
    return result


def side_variant(card: Mapping[str, Any], side: str) -> dict[str, Any] | None:
    """The card with every unresolved field set to one source's value (``db`` or ``wiki``); None if not buildable."""
    if side not in ("db", "wiki"):
        raise ValueError("side must be db or wiki")
    out = copy.deepcopy(dict(card))
    items = (out.get("merge") or {}).get("unresolved", ())
    unknown = set(out.get("unknown_fields") or ())
    for item in items:
        field = str(item["field"])
        unknown.discard(f"unresolved:{field}")
        if field.startswith("levels."):
            _, lv, stat = field.split(".")
            out["levels"][lv][stat] = item[side]
        elif field.startswith("support_skills."):
            sid = field.split(".", 1)[1]
            for skill in out.get("support_skills") or ():
                if skill["id"] == sid:
                    skill["levels"] = {int(k): v for k, v in item[side]["levels"].items()}
                    skill["unsupported"] = [u for u in skill.get("unsupported") or () if not u.startswith("unresolved DB/Wiki")]
        elif field == "events":
            out["events"] = copy.deepcopy(item[f"{side}_value"])
        elif field in ("max_levels", "unit"):
            out[field] = copy.deepcopy(item[side])
        elif field == "panels.sp_cost" and item.get("variants"):
            if side == "wiki":
                for panel in out.get("panels") or ():
                    panel["sp_cost"] = int(panel["sp_cost"]) - 10
        else:
            return None  # e.g. panel kind/LB disagreement: no faithful single-side card
    out["unknown_fields"] = sorted(unknown)
    out["merge"] = {**out.get("merge", {}), "unresolved": []}
    out["readiness"] = role_readiness(out, source_gaps=(card.get("readiness") or {}).get("non_critical_gaps", ()))
    return out


def assess_unresolved(card: Mapping[str, Any], outcome) -> dict[str, Any]:
    """Dual-value sensitivity.

    ``outcome(card_dict) -> (decision, score)``: ``decision`` is everything a conflict may change (rank, selected
    roles, readiness); ``score`` orders favourability (higher = better).  Same decision with either source's value ->
    the card participates with the less favourable side and the conflict stays as a non-blocking note.  Different
    decision (or a side that cannot be built) -> the conflict blocks; the card keeps the field unknown and both
    outcomes are recorded.
    """
    items = (card.get("merge") or {}).get("unresolved", ())
    if not items:
        return copy.deepcopy(dict(card))
    db_card, wiki_card = side_variant(card, "db"), side_variant(card, "wiki")
    result = copy.deepcopy(dict(card))
    if db_card is None or wiki_card is None:
        report = {"verdict": "blocker", "reason": "a side cannot be built (e.g. panel kind/LB disagreement)"}
    else:
        (d_db, s_db), (d_wiki, s_wiki) = outcome(db_card), outcome(wiki_card)
        report = {"db": {"decision": d_db, "score": s_db}, "wiki": {"decision": d_wiki, "score": s_wiki}}
        if d_db == d_wiki:
            side = "db" if s_db <= s_wiki else "wiki"
            result = copy.deepcopy(db_card if side == "db" else wiki_card)
            report.update(verdict="non_blocking", value_used=side,
                          reason="same rank/roles/readiness with either value; less favourable side used")
        else:
            report.update(verdict="blocker", reason="the conflict changes rank, role selection or readiness")
    notes = []
    for item in items:
        note = dict(item)
        note["status"] = "non_blocking" if report["verdict"] == "non_blocking" else "unresolved"
        if report["verdict"] == "non_blocking":
            note["value_used"] = report["value_used"]
        notes.append(note)
    result["merge"] = {**result.get("merge", {}), "unresolved": notes, "sensitivity": report}
    result["readiness"] = role_readiness(result, source_gaps=(card.get("readiness") or {}).get("non_critical_gaps", ()))
    return result


def _provenance(wiki: Mapping[str, Any], field: str) -> dict[str, Any]:
    prov = dict((wiki.get("provenance") or {}).get(field) or {})
    prov.setdefault("url", wiki.get("source_url"))
    prov["source"] = "wiki"
    return prov


def _unmodeled(skill: Mapping[str, Any]) -> bool:
    kind = str(skill.get("kind"))
    return kind == "unknown" or kind.startswith("unmodeled_")


def _support_mechanism_modeled(raw: Mapping[str, Any], rarity: str | None) -> bool:
    """Mechanism coverage only; current ownership still determines whether the skill is unlocked."""
    if _unmodeled(raw):
        return False
    if not raw.get("unsupported"):
        return True
    if raw.get("kind") == "rest_support":
        return True   # valued at zero by the recommender, never a gap
    if raw.get("kind") == "stamina_support":
        skill = SupportSkill.from_dict(raw)
        return support_skill_expectation(skill, max(skill.levels.values(), default=0), rarity) is not None
    return False


def _kinds_compatible(a: list[tuple[int, int, str]], b: list[tuple[int, int, str]]) -> bool:
    """Same (SP, LB) slots carry the same kinds once 'unknown' (one parser could not interpret it) is a wildcard."""
    groups: dict[tuple[int, int], tuple[list[str], list[str]]] = {}
    for cost, lb, kind in a:
        groups.setdefault((cost, lb), ([], []))[0].append(kind)
    for cost, lb, kind in b:
        groups.setdefault((cost, lb), ([], []))[1].append(kind)
    for left, right in groups.values():
        known_l = sorted(k for k in left if k != "unknown")
        known_r = sorted(k for k in right if k != "unknown")
        rest = list(known_r)
        for k in known_l:
            if k in rest:
                rest.remove(k)
            elif right.count("unknown") == 0:
                return False
        if len(rest) > left.count("unknown"):
            return False
    return True


def _panel_rows(card: Mapping[str, Any]) -> list[tuple[int, int, str]]:
    return sorted((int(p["sp_cost"]), int(p.get("required_lb", 0)), str(p.get("kind"))) for p in card.get("panels") or ())


def merge_card(db_card: Mapping[str, Any], wiki_card: Mapping[str, Any]) -> dict[str, Any]:
    """Merge one Wiki card into one DB card (same card, matched by name).  Returns a new dict."""
    merged = copy.deepcopy(dict(db_card))
    conflicts: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    prov = merged.setdefault("provenance", {})
    unknown = list(merged.get("unknown_fields") or ())

    unresolved: list[dict[str, Any]] = []
    db_prov = {"source": "db", **dict((db_card.get("provenance") or {}).get("levels") or {})}
    verified = IN_GAME_STATS_VERIFIED.get(str(db_card.get("card_id"))) or {}
    db_levels, wiki_levels = _levels(db_card), _levels(wiki_card)
    levels: dict[int, dict[str, float | None]] = {}
    for lv in sorted(set(db_levels) | set(wiki_levels)):
        a, b = db_levels.get(lv), wiki_levels.get(lv)
        if a and b:
            row = {}
            for stat in STATS:
                if a.get(stat) == b.get(stat):
                    row[stat] = a.get(stat)
                    continue
                item = {"field": f"levels.{lv}.{stat}", "db": a.get(stat), "wiki": b.get(stat),
                        "db_provenance": db_prov, "wiki_provenance": _provenance(wiki_card, "levels")}
                in_game = (verified.get("levels") or {}).get(lv, {}).get(stat)
                if in_game is not None:
                    row[stat] = float(in_game)
                    conflicts.append({**item, "status": "resolved_in_game", "kept": in_game,
                                      "evidence": verified.get("evidence")})
                else:
                    row[stat] = None  # unknown to the model; both values stay in merge.unresolved
                    item.update(status="unresolved", critical=True,
                                reason="per-level stat: no in-game reading, neither source established")
                    conflicts.append(item)
                    unresolved.append(item)
            levels[lv] = row
            sources[f"levels.{lv}"] = "db+wiki agree" if row == a else "conflict"
        else:
            levels[lv] = dict(a or b)
            sources[f"levels.{lv}"] = "db" if a else "wiki"
    merged["levels"] = {str(k): v for k, v in levels.items()}
    if wiki_levels:
        prov["levels"] = {"db": db_card.get("provenance", {}).get("levels"), "wiki": _provenance(wiki_card, "levels"),
                          "rule": "per level: both equal -> kept; differ -> unresolved (both kept; dual-value sensitivity decides blocker)"}

    if not merged.get("max_levels") and wiki_card.get("max_levels"):
        merged["max_levels"] = dict(wiki_card["max_levels"])
        prov["max_levels"] = _provenance(wiki_card, "max_levels")
        sources["max_levels"] = "wiki"
    elif merged.get("max_levels") and wiki_card.get("max_levels") and _max_levels(merged) != _max_levels(wiki_card):
        item = {"field": "max_levels", "db": merged["max_levels"], "wiki": wiki_card["max_levels"],
                "status": "unresolved", "critical": True, "wiki_provenance": _provenance(wiki_card, "max_levels")}
        conflicts.append(item)
        unresolved.append(item)
        merged["max_levels"] = {}

    if not merged.get("owned_live_skills") and wiki_card.get("owned_live_skills"):
        merged["owned_live_skills"] = copy.deepcopy(list(wiki_card["owned_live_skills"]))
        prov["owned_live_skills"] = _provenance(wiki_card, "owned_live_skills")
        sources["owned_live_skills"] = "wiki"

    db_support = {s["id"]: s for s in db_card.get("support_skills") or ()}
    wiki_support = {s["id"]: s for s in wiki_card.get("support_skills") or ()}
    agree = 0
    coverage: list[dict[str, Any]] = []  # one source lacks an entry/step: not a value disagreement
    for sid in sorted(set(db_support) | set(wiki_support)):
        a, b = db_support.get(sid), wiki_support.get(sid)
        if a and b:
            la = {int(k): float(v) for k, v in a.get("levels", {}).items()}
            lb_ = {int(k): float(v) for k, v in b.get("levels", {}).items()}
            same_kind = a.get("kind") == b.get("kind") or {_unmodeled(a), _unmodeled(b)} == {True}
            if la == lb_ and same_kind:
                agree += 1
            elif same_kind and lb_ and all(la.get(k) == v for k, v in lb_.items()):
                coverage.append({"field": f"support_skills.{sid}", "type": "wiki_missing_steps",
                                 "db_only_levels": sorted(set(la) - set(lb_))})
                agree += 1
            else:
                # Differing ladders: keep both, mark the skill unsupported (never applied) instead of picking one.
                modeled = (_support_mechanism_modeled(a, db_card.get("rarity"))
                           and _support_mechanism_modeled(b, wiki_card.get("rarity") or db_card.get("rarity")))
                item = {"field": f"support_skills.{sid}", "db": {"kind": a.get("kind"), "levels": la},
                        "wiki": {"kind": b.get("kind"), "levels": lb_}, "status": "unresolved",
                        "critical": modeled, "wiki_provenance": _provenance(wiki_card, "support_skills"),
                        "reason": "ladder differs; no in-game reading" + ("" if modeled else
                                  " (mechanic not modeled by growth: non-critical)")}
                conflicts.append(item)
                unresolved.append(item)
                for skill in merged.get("support_skills") or ():
                    if skill["id"] == sid:
                        skill["unsupported"] = list(skill.get("unsupported") or ()) + [
                            "unresolved DB/Wiki ladder conflict; see merge.unresolved"]
        elif wiki_card.get("support_skills"):
            coverage.append({"field": f"support_skills.{sid}", "type": "only_in_db" if a else "only_in_wiki"})
    sources["support_skills"] = f"db (wiki agrees on {agree}/{len(db_support)})"

    def _event_key(e: Mapping[str, Any]) -> tuple:
        gains = e.get("stats") or e.get("gains") or {}
        return tuple(int(gains.get(k) or 0) for k in (*STATS, "SP"))
    if wiki_card.get("events"):
        a = sorted(_event_key(e) for e in db_card.get("events") or ())
        b = sorted(_event_key(e) for e in wiki_card["events"])
        sources["events"] = "db (wiki agrees)" if a == b else "unresolved"
        if a != b:
            item = {"field": "events", "db": a, "wiki": b, "status": "unresolved", "critical": True,
                    "db_value": copy.deepcopy(list(db_card.get("events") or ())),
                    "wiki_value": [{"id": e.get("id"), "stats": dict(e.get("stats") or e.get("gains") or {})}
                                   for e in wiki_card["events"]],
                    "wiki_provenance": _provenance(wiki_card, "events")}
            conflicts.append(item)
            unresolved.append(item)

    if wiki_card.get("panels"):
        a, b = _panel_rows(db_card), _panel_rows(wiki_card)
        contested = any(u.endswith(".sp_cost") for u in unknown)
        same_cost = sorted(x[0] for x in a) == sorted(x[0] for x in b)
        shifted = sorted(x[0] - 10 for x in a) == sorted(x[0] for x in b)
        kinds_a, kinds_b = sorted(x[2] for x in a), sorted(x[2] for x in b)
        evidence = {"wiki_sp": sorted(x[0] for x in b), "db_sp": sorted(x[0] for x in a),
                    "agrees": same_cost, "wiki_10_lower": shifted,
                    "lb_agree": sorted(x[:2] for x in a) == sorted(x[:2] for x in b),
                    "db_uninterpreted": kinds_a.count("unknown"), "wiki_uninterpreted": kinds_b.count("unknown"),
                    "kind_agree_where_both_interpreted": _kinds_compatible(a, b)}
        evidence["lb_kind_agree"] = evidence["lb_agree"] and evidence["kind_agree_where_both_interpreted"]
        sources["panels"] = "db" + (" (contested SP kept unknown; wiki evidence only)" if contested else "")
        sp_source = str((db_card.get("provenance") or {}).get("panels", {}).get("sp_cost") or "")
        sp_established = "IN_GAME" in sp_source or "USER_RULE" in sp_source  # outranks both DB offset and Wiki
        if not evidence["lb_kind_agree"]:
            # Both parsers interpreted the node and disagree on kind/LB: no variant is built -> always a blocker.
            item = {"field": "panels", **evidence, "status": "unresolved", "critical": True,
                    "db_sp_provenance": sp_source, "variants": False}
            conflicts.append(item)
            unresolved.append(item)
        elif not same_cost and not contested and not sp_established:
            # Ordinary DB rule vs a different Wiki SP: keep both; the Wiki side is evaluable only as a uniform shift.
            item = {"field": "panels.sp_cost", **evidence, "status": "unresolved", "critical": True,
                    "db_sp_provenance": sp_source, "variants": shifted}
            conflicts.append(item)
            unresolved.append(item)
        elif not same_cost:
            conflicts.append({"field": "panels.sp_cost", **evidence, "status": "evidence_only",
                              "kept": "converter", "db_sp_provenance": sp_source,
                              "reason": "SP from in-game reading / user rule outranks the Wiki"})
        elif evidence["db_uninterpreted"] != evidence["wiki_uninterpreted"]:
            conflicts.append({"field": "panels", **evidence, "status": "evidence_only", "kept": "db"})
        prov.setdefault("panels", {})["wiki_evidence"] = {**evidence, "url": wiki_card.get("source_url")}

    if wiki_card.get("unit") and merged.get("unit") and wiki_card["unit"] != merged["unit"]:
        item = {"field": "unit", "db": merged["unit"], "wiki": wiki_card["unit"], "status": "unresolved",
                "critical": any(str(s.get("kind", "")).startswith("unit_mastery_") for s in merged.get("support_skills") or ())}
        conflicts.append(item)
        unresolved.append(item)
        merged["unit"] = None

    if db_card.get("kind") == "P":
        if merged.get("te_eligible") is None and wiki_card.get("te_eligible") is True:
            merged["te_eligible"] = True
            unknown = [u for u in unknown if u != "te_eligible"]
            prov["te_eligible"] = {**_provenance(wiki_card, "te_eligible"), "condition": wiki_card.get("te_condition")}
            sources["te_eligible"] = "wiki"
        elif wiki_card.get("te_eligible") is None:
            condition = wiki_card.get("te_condition")
            sources["te_eligible"] = (f"unknown (non-standard True End condition: {condition})" if condition
                                      else "unknown (wiki page states no True End condition)")

    # The model has one gap list; "unresolved:" entries keep conflicts distinct from missing data (unknown).
    unknown += [f"unresolved:{u['field']}" for u in unresolved
                if u.get("critical") and not str(u["field"]).startswith(("levels.", "support_skills."))]
    merged["unknown_fields"] = sorted(set(unknown))
    merged["merge"] = {"wiki_card_id": wiki_card.get("card_id"), "wiki_url": wiki_card.get("source_url"),
                       "sources": sources, "conflicts": conflicts, "unresolved": unresolved,
                       "coverage_gaps": coverage, "policy": "per-field evidence (FIELD_POLICY), 2026-10-07",
                       # stored rows off the per-level formula (card_stats): evidence of a typo, values unchanged
                       "formula_mismatches": card_stats.formula_mismatches(
                           {lv: row for lv, row in levels.items()}, rarity=merged.get("rarity"),
                           max_levels=merged.get("max_levels"))}
    gaps = [g for g in (merged.get("readiness") or {}).get("non_critical_gaps", ())]
    merged["readiness"] = role_readiness(merged, source_gaps=gaps)
    return merged


def with_owned_level(card: Mapping[str, Any], level: int, limit_break: int | None = None) -> dict[str, Any]:
    """Copy of an S card whose ``levels`` holds a row for the owned ``level`` (the True End extractor reads only the
    exact owned row).  Priority: an in-game reading (``IN_GAME_STATS_VERIFIED``) > a stored row > the
    ``card_stats`` formula between two stored rows (``derived``, recorded in ``merge.derived_levels`` with method,
    anchors and evidence).  Nothing is estimated: without both anchors the row stays absent (unknown).  A level above
    the owned limit break's cap is refused (``merge.owned_level_error``) and nothing is added."""
    out = copy.deepcopy(dict(card))
    if out.get("kind") != "S":
        return out
    level = int(level)
    meta = out.setdefault("merge", {})
    cap = card_stats.level_cap(out.get("rarity"), limit_break) if limit_break is not None else None
    caps = _max_levels(out)
    if limit_break is not None and caps.get(int(limit_break)) is not None:
        cap = caps[int(limit_break)]
    if cap is not None and level > cap:
        meta["owned_level_error"] = f"level {level} exceeds the LB{limit_break} cap {cap}"
        return out
    verified = (IN_GAME_STATS_VERIFIED.get(str(out.get("card_id"))) or {})
    row = (verified.get("levels") or {}).get(level)
    levels = out.setdefault("levels", {})
    if row is not None:
        levels[str(level)] = {s: float(row[s]) for s in STATS}
        meta.setdefault("derived_levels", {})[str(level)] = {"status": "in_game", "evidence": verified.get("evidence")}
        return out
    value = card_stats.stats_at_level(levels, level, rarity=out.get("rarity"), max_levels=out.get("max_levels"))
    if value.status == "derived":
        levels[str(level)] = {s: float(v) for s, v in value.stats.items()}
        meta.setdefault("derived_levels", {})[str(level)] = {
            "status": "derived", "method": value.method, "anchors": list(value.anchors), "evidence": value.evidence}
    elif value.status == "unknown":
        meta.setdefault("derived_levels", {})[str(level)] = {"status": "unknown", "method": value.method}
    return out


def match_by_name(db_cards: Iterable[Mapping[str, Any]], wiki_cards: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """{enza_id: wiki_card} for exact normalised-name matches of the same kind; ambiguous names are dropped."""
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for w in wiki_cards:
        by_name.setdefault(_norm_name(str(w.get("name") or w.get("card_id"))), []).append(w)
    out: dict[str, Mapping[str, Any]] = {}
    for c in db_cards:
        hits = [w for w in by_name.get(_norm_name(str(c.get("name"))), ()) if w.get("kind") == c.get("kind")]
        if len(hits) == 1:
            out[str(c["card_id"])] = hits[0]
    return out


def bind_wiki_cards(db_cards: Iterable[Mapping[str, Any]], wiki_cards: Iterable[Mapping[str, Any]]
                    ) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    """({enza_id: wiki_card}, notes).  Duplicate pages (same ``source_url``) are read once.  A page fetched for an
    Enza ID (``fetch_enza_id``) binds to that card only when the kind matches and the parsed name equals the DB name
    (normalised); a mismatch is reported and not merged.  Other Wiki cards fall back to :func:`match_by_name`."""
    db_cards = list(db_cards)
    by_id = {str(c["card_id"]): c for c in db_cards}
    seen: set[str] = set()
    out: dict[str, Mapping[str, Any]] = {}
    notes: list[dict[str, Any]] = []
    rest = []
    for w in wiki_cards:
        url = str(w.get("source_url") or "")
        if url and url in seen:
            continue
        seen.add(url)
        eid = str(w.get("fetch_enza_id") or "")
        c = by_id.get(eid)
        if c is None:
            rest.append(w)
            continue
        wname = str(w.get("name") or w.get("card_id"))
        if w.get("kind") != c.get("kind") or _loose_name(wname) != _loose_name(str(c.get("name"))):
            notes.append({"card_id": eid, "wiki_name": w.get("name"), "db_name": c.get("name"), "wiki_kind": w.get("kind"),
                          "reason": "fetched page does not match the DB card; not merged"})
            continue
        if eid in out:
            notes.append({"card_id": eid, "reason": "two different pages bound to one card; not merged"})
            out.pop(eid)
            continue
        if _norm_name(wname) != _norm_name(str(c.get("name"))):
            notes.append({"card_id": eid, "wiki_name": wname, "db_name": c.get("name"),
                          "reason": "bound by Enza-ID fetch; names differ only in decorative symbols"})
        out[eid] = w
    for cid, w in match_by_name([c for c in db_cards if str(c["card_id"]) not in out], rest).items():
        out[cid] = w
    return out, notes


def merge_database(db_payload: Mapping[str, Any], wiki_cards: Iterable[Mapping[str, Any]],
                   only: Iterable[str] | None = None) -> dict[str, Any]:
    cards = list(db_payload["cards"])
    if only is not None:
        keep = set(only)
        cards = [c for c in cards if c["card_id"] in keep]
    matches, notes = bind_wiki_cards(cards, wiki_cards)
    merged = [merge_card(c, matches[c["card_id"]]) if c["card_id"] in matches else copy.deepcopy(c) for c in cards]
    return {"parser_version": "card-data-merge-v1", "db_parser_version": db_payload.get("parser_version"),
            "matched": sorted(matches), "binding_notes": notes, "cards": merged}


def apply_owned_levels(cards: Iterable[Mapping[str, Any]], details: Mapping[str, Mapping[str, Any]],
                       source: str = OWNED_LEVEL_SOURCE) -> list[dict[str, Any]]:
    """Cards with the owned Lv row added (:func:`with_owned_level`) and readiness evaluated at the owned LB/Lv.

    ``details`` is ``prefs.owned_cards.details``-shaped (``{id: {limit_break, level?}}``); P cards have no level and
    are evaluated at the owned LB.  Each touched card records ``merge.owned_level`` with ``source`` (default:
    recognised from the in-game list, not user-confirmed).  Cards without a reading are returned unchanged."""
    out = []
    for card in cards:
        own = details.get(str(card.get("card_id"))) or {}
        if own.get("limit_break") is None:
            out.append(copy.deepcopy(dict(card)))
            continue
        lb, level = int(own["limit_break"]), own.get("level")
        level = None if level is None else int(level)
        new = with_owned_level(card, level, lb) if card.get("kind") == "S" and level is not None else copy.deepcopy(dict(card))
        new.setdefault("merge", {})["owned_level"] = {"limit_break": lb, "level": level, "source": source}
        gaps = (card.get("readiness") or {}).get("non_critical_gaps", ())
        new["readiness"] = role_readiness(new, source_gaps=gaps, limit_break=lb, level=level)
        new["readiness"]["assumed"]["ownership_source"] = source
        out.append(new)
    return out


def load_owned_details(path: Path) -> dict[str, dict[str, int]]:
    """``details`` from ``ui/game-assets/owned-levels.js`` (``export default {...}``) or a JSON file."""
    text = Path(path).read_text(encoding="utf-8")
    doc = json.loads(text[text.index("{"):text.rindex("}") + 1])
    details = doc.get("details", doc)
    return {str(k): {f: int(v[f]) for f in ("limit_break", "level") if v.get(f) is not None}
            for k, v in details.items()}


def readiness_counts(cards: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"S": 0, "P": 0, "ready_as_active": 0, "ready_as_guest": 0, "ready_as_p": 0}
    for c in cards:
        r = c.get("readiness") or role_readiness(c)
        counts[c["kind"]] += 1
        for key in ("ready_as_active", "ready_as_guest", "ready_as_p"):
            counts[key] += bool(r.get(key))
    return counts


# Hypothetical ownership for screening only (the user's real Lv/LB are not in the DB or the profile yet).
SCREEN_LEVEL = {"S_SSR": 80, "S_SR": 70, "S_UR": 90, "S_R": 1}


def _screen_features(cards: Iterable[Mapping[str, Any]]):
    """S-card features at an ASSUMED 4 LB and the LB4 cap level (never presented as owned stats)."""
    from agent.core.true_end.models import CardData, Ownership, extract_card_features
    out = []
    for c in cards:
        if c.get("kind") != "S":
            continue
        card = CardData.from_dict(c)
        level = card.max_levels.get(4) or SCREEN_LEVEL.get(str(c.get("rarity")), 1)
        out.append(extract_card_features(card, Ownership(card.card_id, 4, level)))
    return out


def screen_candidates(cards: Iterable[Mapping[str, Any]], strategy: str = "VO", per_role: int = 8
                      ) -> dict[str, list[str]]:
    """{card_id: roles} retained by the recommender's own pruning (``prune_candidates``)."""
    from agent.core.true_end.features import GUEST_ROLES, ROLES, score_card_prior
    from agent.core.true_end.optimizer import SearchConfig, prune_candidates
    feats = tuple(_screen_features(cards))
    cfg = SearchConfig(candidate_per_role=per_role)
    roles: dict[str, list[str]] = {}
    for slot, names in (("active", ROLES), ("guest", GUEST_ROLES)):
        kept = {f.card.card_id for f in prune_candidates(feats, strategy, slot, cfg)}
        for role in names:
            scored = [(score_card_prior(f, strategy, role, slot), f.card.card_id) for f in feats]
            for score, cid in sorted((v for v in scored if v[0] > 0), key=lambda v: (-v[0], v[1]))[:per_role]:
                if cid in kept:
                    roles.setdefault(cid, []).append(("G:" if slot == "guest" else "") + role)
        for cid in kept:
            roles.setdefault(cid, ["idol-retention"])
    return roles


def rank_candidates(cards: Iterable[Mapping[str, Any]], strategy: str = "VO") -> list[dict[str, Any]]:
    """Rank by the active HYBRID prior (the order ``prune_candidates`` returns), with readiness attached."""
    from agent.core.true_end.features import score_card_prior
    cards = list(cards)
    feats = {f.card.card_id: f for f in _screen_features(cards)}
    rows = []
    for c in cards:
        f = feats.get(c["card_id"])
        if f is None:
            continue
        r = c.get("readiness") or role_readiness(c)
        rows.append({"card_id": c["card_id"], "name": c.get("name"), "rarity": c.get("rarity"),
                     "hybrid": round(score_card_prior(f, strategy, "HYBRID", "active"), 4),
                     "ready_as_active": r.get("ready_as_active"), "ready_as_guest": r.get("ready_as_guest"),
                     "features_ready": f.ready, "features_ready_as_guest": f.ready_as_guest})
    rows.sort(key=lambda x: (-x["hybrid"], x["card_id"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def screening_outcome(pool: Iterable[Mapping[str, Any]], strategy: str = "VO", per_role: int = 8):
    """``outcome`` for :func:`assess_unresolved`: (rank, roles, readiness) of the card inside ``pool``."""
    pool = [copy.deepcopy(dict(c)) for c in pool]

    def outcome(card: Mapping[str, Any]):
        cards = [card if c["card_id"] == card["card_id"] else c for c in pool]
        rows = {r["card_id"]: r for r in rank_candidates(cards, strategy)}
        roles = tuple(sorted(screen_candidates(cards, strategy, per_role).get(card["card_id"], ())))
        row = rows[card["card_id"]]
        return (row["rank"], roles, row["ready_as_active"], row["ready_as_guest"]), row["hybrid"]
    return outcome


# The True End condition the W.I.N.G. model assumes (the same strings the parser requires).
STANDARD_TE_CONDITION = "アイドルランクA達成 かつ W.I.N.G.優勝"


def te_condition_from_page(html: str) -> str | None:
    """The P page's True End condition text (e.g. ``アイドルランクA達成 かつ W.I.N.G.優勝``), or None.

    Accepts the ``True End`` and ``TrueEnd`` spellings seen on wikiwiki.jp."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    hit = re.search(r"True ?End 条件 (.+?) (?:プロデュースイベント|思い出アピール|スキルパネル|&nbsp;)", text)
    return re.sub(r"\s+", " ", hit[1]).strip() if hit else None


def _apply_te_condition(row: dict[str, Any], html: str) -> None:
    """Record the page's TE condition; te_eligible True only for the standard condition.  A different condition
    (e.g. a fan-count goal) stays unknown: the True End model does not represent it."""
    condition = te_condition_from_page(html)
    row["te_condition"] = condition
    if condition is not None and condition != STANDARD_TE_CONDITION:
        row["te_eligible"] = None  # a non-standard condition is not the modelled True End
        return
    if row.get("te_eligible") is None and condition == STANDARD_TE_CONDITION:
        row["te_eligible"] = True
        row.setdefault("provenance", {})["te_eligible"] = {
            "url": row.get("source_url"), "locator": "TrueEnd 条件", "status": "PARSED",
            "reader": "card_data_merge.te_condition_from_page (parser missed the 'TrueEnd' spelling)"}


def _load_wiki_cache(entries_path: Path, errors: list | None = None) -> list[dict[str, Any]]:
    """Parse cached Wiki HTML listed in a JSON list or JSONL log (url + file) with the offline parser."""
    from dataclasses import asdict
    from agent.core.true_end.wiki import parse_card_page
    text = entries_path.read_text(encoding="utf-8")
    entries = json.loads(text) if text.lstrip().startswith("[") else [json.loads(l) for l in text.splitlines() if l.strip()]
    cards = []
    for e in entries:
        # 404s, errors and the robots.txt line have no page file; challenge pages are never parsed.
        if (e.get("challenge") or not e.get("file") or e.get("status", 200) != 200
                or e.get("kind", "S") not in ("P", "S")):  # e.g. list pages fetched to resolve titles
            continue
        path = Path(e["file"])
        if not path.is_absolute():
            path = entries_path.parent / path
        try:
            card = parse_card_page(path.read_text(encoding="utf-8"), source_url=e["url"], kind=e.get("kind", "S"),
                                   fetched_at=e.get("fetched_at"))
        except ValueError as error:  # parser refusal (e.g. duplicate panel id): recorded, the card stays DB-only
            if errors is None:
                raise
            errors.append({"enza_id": e.get("enza_id"), "url": e["url"], "error": str(error)})
            continue
        row = json.loads(json.dumps(asdict(card), ensure_ascii=False, default=list))
        if row.get("kind") == "P":
            _apply_te_condition(row, path.read_text(encoding="utf-8"))
        if e.get("enza_id"):
            row["fetch_enza_id"] = str(e["enza_id"])  # the page URL was built from this DB card's name
        cards.append(row)
    return cards


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Merge a private ShinyColorsDB card database with cached Wiki cards")
    ap.add_argument("--db", type=Path, required=True, help="converted-cards.json")
    ap.add_argument("--wiki-cache", type=Path, action="append", default=[], help="JSON list / JSONL of url+file")
    ap.add_argument("--wiki-cards", type=Path, action="append", default=[],
                    help="already parsed/reviewed Wiki CardData payload {cards: [...]} (e.g. the reviewed cache)")
    ap.add_argument("--only", help="comma-separated Enza IDs to keep")
    ap.add_argument("--sensitivity", action="store_true",
                    help="evaluate unresolved DB/Wiki conflicts with each value inside the merged pool (VO screening)")
    ap.add_argument("--owned-levels", type=Path,
                    help="owned Lv/LB details (ui/game-assets/owned-levels.js or JSON): add the owned-level row and "
                         "evaluate readiness there (recognised, not user-confirmed); never writes a profile")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    db = json.loads(args.db.read_text(encoding="utf-8"))
    parse_errors: list[dict[str, Any]] = []
    wiki = [c for path in args.wiki_cache for c in _load_wiki_cache(path, parse_errors)]
    for path in args.wiki_cards:
        wiki += [c for c in json.loads(path.read_text(encoding="utf-8"))["cards"] if c.get("kind") in ("S", "P")]
    payload = merge_database(db, wiki, args.only.split(",") if args.only else None)
    if args.sensitivity:
        outcome = screening_outcome(payload["cards"])
        payload["cards"] = [assess_unresolved(c, outcome) for c in payload["cards"]]
    if args.owned_levels:
        payload["cards"] = apply_owned_levels(payload["cards"], load_owned_details(args.owned_levels))
        payload["owned_level_source"] = OWNED_LEVEL_SOURCE
    payload["readiness_counts"] = readiness_counts(payload["cards"])
    payload["wiki_parse_errors"] = parse_errors
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged {len(payload['matched'])}/{len(payload['cards'])} card(s): {payload['readiness_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
