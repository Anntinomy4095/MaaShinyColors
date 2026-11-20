"""自动编队 engine adapter: the True End recommender first, ``wing_team.select_team`` as the fallback.

``produce_config.auto_team_plan`` calls :func:`recommend` when no explicit scorer is given. The adapter

1. turns 「持有卡牌」 (10-digit wiki ids, ``owned["details"] = {id: {level, limit_break}}``) into the True End input
   through the exact :mod:`agent.core.card_id_map` and the locally parsed card database (no network);
2. calls the public ``agent.core.true_end`` API (``find_best_teams`` with ``GrowthConfig`` / ``SearchConfig`` /
   ``scenario_from_dict``) once per usable owned P card and keeps the best ``ProxyTERating``;
3. maps the result back to wiki ids and returns the same fields ``auto_team_plan`` reads from a scorer
   (``status`` / ``p_card`` / ``starters`` / ``guest`` / ``confidence`` / ``reasons`` / ``rejected_cards``) plus
   ``engine``, ``fallback_reason``, per-card ``coverage``, ``needs_parsing`` and the recommender ``summary``
   (growth, conditional battle results, assumptions, ``ProxyTERating`` ESTIMATED, ``true_end_probability`` None).

Card library (2026-10-07, schedule item 1): by default the whole local library is used, not a sample list.
:func:`load_library` unions, per 10-digit Enza ID, (a) DB+Wiki merged cards (``merged-*.json``), (b) converted
ShinyColorsDB cards (``converted-cards.json``) of every ``dev_assets/private/shinycolorsdb/snapshot-*`` folder, newest
manifest first, and (c) the older reviewed Wiki databases through the exact id map. Every owned ID is looked up; the
per-card, per-role data gaps (``card_gaps``: unknown vs unresolved, from ``card_data_merge.role_readiness``) are
returned so the UI can show what is missing. Unknown Lv/凸 or P TE eligibility are never filled in silently: only the
explicit ``assume`` switches do it, and every assumed card is labelled.

When the True End path cannot produce a team (no parsed cards, no mapping, P card unusable, too few usable S cards,
blocked data, invalid settings) it falls back to ``wing_team.select_team`` and says why. Owned cards the recommender
could not use (unmapped, not parsed, Lv/凸 unknown, unknown mechanics) are excluded from its search and listed; the
summary states that the team was chosen among the usable cards only. Either way the result is only a proposal: the
user must accept it into the slots (``team_needs_confirmation``). No device, no input, no resource use.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.core.card_id_map import DEFAULT_MAP, PRIVATE, CardIdMap

DEFAULT_DATABASES = (PRIVATE / "true_end" / "cards.json", PRIVATE / "true_end" / "verified-cache-cards.json")
SCDB_ROOT = PRIVATE / "shinycolorsdb"
# Hypothetical level per rarity when the LB4 cap is not in the data (same table as card_data_merge.SCREEN_LEVEL).
ASSUMED_LEVEL = {"S_SSR": 80, "S_SR": 70, "S_UR": 90, "S_R": 1, "SSR": 80, "SR": 70, "UR": 90, "R": 1}
ASSUME_KEYS = {"lb4_cap", "p_te_eligible", "recognised_levels"}
# Owned Lv/凸 recognised from the in-game list (tools/owned_levels_scan.py); not imported/confirmed by the user.
RECOGNISED_LEVELS = PRIVATE.parent.parent / "ui" / "game-assets" / "owned-levels.js"
ROLE_LABELS = ("active", "guest", "p")
DEFAULT_SETTINGS = PRIVATE / "true_end" / "ui-recommender.json"
SETTINGS_KEYS = {"growth", "search", "battle_scenarios"}
STRATEGY = {"Vo": "VO", "Da": "DA", "Vi": "VI"}
# Smaller than the CLI defaults so a UI proposal stays quick; a settings file may raise them within the bounds.
UI_SEARCH = {"candidate_per_role": 10, "beam_width": 60, "projection_budget": 3000, "evaluate_top": 3,
             "screening_trials": 16, "confirmation_trials": 128}
MAX_P_CANDIDATES = 6
OWNED_LIVE_RULE = "owned-live-rule-20261031"   # agent.core.owned_live_rule.RULE
# 推荐编队 (2026-10-14, user): Maa picks the P card too -- every data-ready owned P card (newest SSR first) up to this
# bound is searched, each with its own specialty as 主属性 when the 主属性 is 「自动」.
MAX_P_CANDIDATES_PICK = 8         # ~12 s per P card on the user library (2026-10-14: 12 cards took 163 s)
MAX_LEVEL = 100
MAX_LIMIT_BREAK = 4
MIN_SUPPORTS = 5   # four active + one guest, all different idols

ENGINE_TRUE_END = "true_end"
ENGINE_WING = "wing_team"


def clean_details(owned: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    """``{id: {level?, limit_break?}}`` for owned ids only; a missing field means unknown. Raises on malformed values
    (the host sanitiser drops them before they reach here, so a bad value is a programming error, not user data)."""
    if not owned:
        return {}
    have = set(owned.get("p") or []) | set(owned.get("s") or [])
    details = owned.get("details")
    if details is None:
        return {}
    if not isinstance(details, Mapping):
        raise ValueError("owned.details 必须是对象")
    out: dict[str, dict[str, int]] = {}
    for card, entry in details.items():
        if card not in have:
            continue
        if not isinstance(entry, Mapping) or set(entry) - {"level", "limit_break"}:
            raise ValueError(f"owned.details.{card} 只允许 level / limit_break")
        clean = {}
        for key, low, high in (("level", 1, MAX_LEVEL), ("limit_break", 0, MAX_LIMIT_BREAK)):
            value = entry.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"owned.details.{card}.{key} 必须是 {low}..{high} 的整数")
            clean[key] = value
        if clean:
            out[card] = clean
    return out


def details_as_features(owned: Mapping[str, Any] | None, features: Mapping[str, Mapping[str, Any]] | None) -> dict:
    """wing_team features with the user's Lv/凸 filled in where no other source gave them (``level`` / ``uncap``)."""
    merged = {k: dict(v) for k, v in (features or {}).items() if isinstance(v, Mapping)}
    for card, entry in clean_details(owned).items():
        row = merged.setdefault(card, {})
        if "level" in entry:
            row.setdefault("level", entry["level"])
        if "limit_break" in entry:
            row.setdefault("uncap", entry["limit_break"])
    return merged


def load_card_database(paths=DEFAULT_DATABASES) -> tuple[dict[str, dict], list[str]]:
    """Raw card dicts by True End id from the existing parsed databases. The first file wins on a duplicate id."""
    from agent.core.true_end.models import CardData
    cards: dict[str, dict] = {}
    notes: list[str] = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw in data.get("cards", []) if isinstance(data, dict) else []:
            try:
                card = CardData.from_dict(raw)
            except (ValueError, TypeError, KeyError) as error:
                notes.append(f"{path.name}: 卡数据未通过校验（{error}）")
                continue
            if card.card_id in cards:
                notes.append(f"{path.name}: {card.card_id} 已由前一个卡库提供，忽略重复")
                continue
            cards[card.card_id] = raw
    return cards, notes


def _manifest_time(folder: Path) -> str:
    try:
        doc = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        return str(doc.get("generated_at") or "")
    except (OSError, ValueError):
        return ""


def discover_snapshots(root: Path = SCDB_ROOT) -> list[dict]:
    """ShinyColorsDB snapshot folders with a manifest, newest ``generated_at`` first (folder name breaks ties)."""
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for folder in root.iterdir():
        if not (folder.is_dir() and folder.name.startswith("snapshot-") and (folder / "manifest.json").is_file()):
            continue
        converted = folder / "converted-cards.json"
        found.append({"name": folder.name, "dir": folder, "generated_at": _manifest_time(folder),
                      "converted": converted if converted.is_file() else None,
                      "merged": sorted(folder.glob("merged-*.json"))})
    found.sort(key=lambda f: (f["generated_at"], f["name"]), reverse=True)
    return found


def _valid_cards(path: Path, notes: list[str]) -> list[dict]:
    from agent.core.true_end.models import CardData
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        notes.append(f"{Path(path).name}: 无法读取（{error}）")
        return []
    out = []
    for raw in data.get("cards", []) if isinstance(data, dict) else []:
        try:
            CardData.from_dict(raw)
        except (ValueError, TypeError, KeyError) as error:
            notes.append(f"{Path(path).name}: {raw.get('card_id') if isinstance(raw, dict) else '?'} 未通过校验（{error}）")
            continue
        out.append(raw)
    return out


def load_library(root: Path = SCDB_ROOT, legacy=DEFAULT_DATABASES, id_map: CardIdMap | None = None) -> dict:
    """The local P/S library keyed by 10-digit Enza ID: ``{cards, sources, snapshots, notes}``.

    Precedence per ID: merged DB+Wiki cards (newest snapshot first) > reviewed Wiki cards through the exact id map >
    converted DB cards (newest snapshot first). A legacy card is re-keyed to its Enza ID (``card_id``) and keeps its
    title in ``name``. A snapshot without ``converted-cards.json`` (e.g. a fetch still running) is listed, not read."""
    notes: list[str] = []
    snapshots = discover_snapshots(root)
    cards: dict[str, dict] = {}
    sources: dict[str, str] = {}

    def take(raw: dict, key: str, label: str) -> None:
        if key in cards:
            return
        cards[key] = raw if raw.get("card_id") == key else {**raw, "card_id": key, "name": raw.get("name") or raw["card_id"]}
        sources[key] = label

    for snap in snapshots:
        for path in snap["merged"]:
            for raw in _valid_cards(path, notes):
                take(raw, str(raw["card_id"]), f"merged:{snap['name']}")
    if id_map is None:
        id_map = CardIdMap.load(DEFAULT_MAP)
    legacy_cards, legacy_notes = load_card_database(legacy)
    notes += legacy_notes
    for title, raw in legacy_cards.items():
        enza = id_map.wiki_id(title)
        if enza is not None:
            take(raw, enza, "wiki_reviewed")
    for snap in snapshots:
        if snap["converted"] is None:
            notes.append(f"{snap['name']}: 还没有 converted-cards.json，本次未读取")
            continue
        for raw in _valid_cards(snap["converted"], notes):
            take(raw, str(raw["card_id"]), f"db:{snap['name']}")
    # 2026-10-31 (user): an S card without a Wiki own-Live skill gets the rarity/source rule's (owned_live_rule).
    from agent.core.owned_live_rule import with_derived_owned_live
    cards, derived_live = with_derived_owned_live(cards)
    if derived_live:
        notes.append(f"{len(derived_live)} 张 S 卡的自带 Live 技能按稀有度/获取方式规则推算（Wiki 未收录）")
    counts: dict[str, int] = {}
    for key in cards:
        kind = "P" if key.startswith("1") else "S"
        counts[kind] = counts.get(kind, 0) + 1
    return {"cards": cards, "sources": sources, "notes": notes, "counts": counts,
            "snapshots": [{"name": s["name"], "generated_at": s["generated_at"], "converted": s["converted"] is not None,
                           "merged": [m.name for m in s["merged"]]} for s in snapshots]}


def owned_from_snapshot(folder: Path) -> dict:
    """``{p, s}`` from a ShinyColorsDB snapshot manifest (the IDs that snapshot was fetched for, e.g. ``*-owned``)."""
    doc = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    ids = sorted({str(e.get("enza_id")) for e in doc.get("entries") or [] if isinstance(e, Mapping)})
    return {"p": [i for i in ids if i[:1] == "1" and len(i) == 10], "s": [i for i in ids if i[:1] == "2" and len(i) == 10]}


def with_recognised_levels(owned: Mapping[str, Any] | None, path: Path | None = None) -> tuple[dict, str]:
    """``owned`` plus the recognised Lv/凸 (explicit ``assume.recognised_levels`` only).  The user's own details win per
    card; recognised cards missing from the owned lists are added (they were seen in the owned list).  Each added
    detail is listed in ``owned["recognised"]`` so it is labelled; nothing is written to the profile."""
    from agent.core.card_data_merge import load_owned_details
    owned = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, Mapping) else v)
             for k, v in (owned or {}).items()}
    path = Path(path or RECOGNISED_LEVELS)
    if not path.is_file():
        return owned, f"未找到识别 Lv/凸 文件 {path.name}，本次未使用"
    found = load_owned_details(path)
    details = dict(owned.get("details") or {})
    recognised = []
    for card, entry in found.items():
        kind = "p" if card.startswith("1") else "s"
        if card not in (owned.get(kind) or []):
            owned[kind] = [*(owned.get(kind) or []), card]
        if card not in details and entry.get("limit_break") is not None:
            details[card] = {k: v for k, v in entry.items() if k in ("level", "limit_break")}
            recognised.append(card)
    owned["details"] = details
    owned["recognised"] = sorted(recognised)
    return owned, (f"{len(recognised)} 张卡使用识别出的 Lv/凸（{path.name}，RECOGNISED_NOT_USER_CONFIRMED，"
                   "未经用户导入确认，属假设）")


def load_settings(path: Path | None = DEFAULT_SETTINGS) -> dict:
    """Optional private recommender settings ({growth, search, battle_scenarios}); missing file -> {}."""
    if path is None or not Path(path).is_file():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - SETTINGS_KEYS - {"notes"}:
        raise ValueError(f"推荐器设置只允许 {sorted(SETTINGS_KEYS)}")
    return data


def _assumed_level(raw: Mapping[str, Any], lb: int) -> int:
    from .card_stats import resolve_level_caps
    caps, _ = resolve_level_caps(raw)
    cap = caps.get(str(lb), caps.get(lb)) if isinstance(caps, Mapping) else None
    if isinstance(cap, int) and not isinstance(cap, bool) and 1 <= cap <= MAX_LEVEL:
        return cap
    return ASSUMED_LEVEL.get(str(raw.get("rarity")), 1)


def _card_coverage(owned: Mapping[str, Any], details: Mapping[str, Mapping[str, int]], cards: Mapping[str, dict],
                   id_map: CardIdMap, assume: Mapping[str, bool] | None = None
                   ) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    """(coverage {wiki id: "usable" | "usable_assumed" | reason}, inventory {true_end id: ownership}, needs_parsing).

    A card is found by its own 10-digit ID in the library first, then through the exact id map (older Wiki keys)."""
    assume = assume or {}
    coverage, inventory, needs = {}, {}, []
    for kind in ("p", "s"):
        for wiki_id in owned.get(kind) or []:
            target = wiki_id if wiki_id in cards else id_map.true_end_id(wiki_id)
            if target is None:
                reason = id_map.unmapped_reason(wiki_id) if len(id_map) else "not_in_library"
                coverage[wiki_id] = f"unmapped:{reason}" if len(id_map) else "not_parsed"
                needs.append({"id": wiki_id, "kind": kind.upper(), "reason": reason,
                              "candidate_title": id_map.candidate_title(wiki_id)})
                continue
            if target not in cards:
                coverage[wiki_id] = "not_parsed"
                needs.append({"id": wiki_id, "kind": kind.upper(), "reason": "not_parsed", "candidate_title": target})
                continue
            entry = dict(details.get(wiki_id, {}))
            assumed = False
            if kind == "p" and "limit_break" in entry and "level" not in entry:
                entry["level"] = 1   # P cards have no level (only stars); the True End model does not read it
            if assume.get("lb4_cap") and ("level" not in entry or "limit_break" not in entry):
                entry.setdefault("limit_break", MAX_LIMIT_BREAK)
                entry.setdefault("level", _assumed_level(cards[target], entry["limit_break"]))
                assumed = True
            if "level" not in entry:
                coverage[wiki_id] = "level_unknown"
                continue
            if "limit_break" not in entry:
                coverage[wiki_id] = "limit_break_unknown"
                continue
            coverage[wiki_id] = "usable_assumed" if assumed else "usable"
            inventory[target] = {"card_id": target, "level": entry["level"], "limit_break": entry["limit_break"],
                                 "wiki_id": wiki_id, "assumed": assumed}
    return coverage, inventory, needs


def card_gaps(owned: Mapping[str, Any], cards: Mapping[str, dict], inventory: Mapping[str, Mapping[str, Any]],
              id_map: CardIdMap, sources: Mapping[str, str] | None = None) -> list[dict]:
    """Per owned card: library source, Lv/凸 basis and, per role, the critical gaps split into ``unknown`` (no data)
    and ``unresolved`` (DB/Wiki disagree), via ``card_data_merge.role_readiness`` (read-only use of the API)."""
    from agent.core.card_data_merge import role_readiness
    by_wiki = {o["wiki_id"]: (key, o) for key, o in inventory.items()}
    rows = []
    for kind in ("p", "s"):
        for wiki_id in owned.get(kind) or []:
            key, own = by_wiki.get(wiki_id, (None, None))
            if key is None:
                key = wiki_id if wiki_id in cards else id_map.true_end_id(wiki_id)
            raw = cards.get(key) if key else None
            row = {"id": wiki_id, "kind": kind.upper(), "name": None, "source": None, "ownership": None,
                   "ready": {}, "unknown": {}, "unresolved": {}, "non_blocking": 0, "unmodeled": 0, "assumed": []}
            if raw is None:
                row["source"] = "missing"
                rows.append(row)
                continue
            row["name"] = str(raw.get("name") or key)[:80]
            row["source"] = (sources or {}).get(key, "library")
            if own is not None and own.get("assumed"):
                # Only missing fields were assumed; keep an explicitly known lower limit break and its cap.
                ready = role_readiness(raw, limit_break=own["limit_break"], level=own["level"])
                row["ownership"] = "ASSUMED_LB4_CAP"
                row["level"], row["limit_break"] = own["level"], own["limit_break"]
            elif own is not None:
                ready = role_readiness(raw, limit_break=own["limit_break"], level=own["level"])
                row["ownership"] = "OWNED"
                row["level"], row["limit_break"] = own["level"], own["limit_break"]
            else:
                ready = role_readiness(raw)
                row["ownership"] = "UNKNOWN"   # assessed at the LB4 cap only to list gaps; not used for the search
            for role in ROLE_LABELS:
                flag = ready.get(f"ready_as_{role}")
                if flag is not None:
                    row["ready"][role] = bool(flag)
                unknown = (ready.get("unknown_gaps") or {}).get(role)
                unresolved = (ready.get("unresolved_gaps") or {}).get(role)
                if unknown is not None:
                    row["unknown"][role] = [str(g)[:80] for g in unknown][:24]
                if unresolved is not None:
                    row["unresolved"][role] = [str(g)[:80] for g in unresolved][:24]
            row["non_blocking"] = len(ready.get("non_blocking_conflicts") or ())
            row["unmodeled"] = int(ready.get("unmodeled_mechanics") or 0)
            rows.append(row)
    return rows


def _gap_counts(rows: list[dict]) -> dict:
    out = {"cards": len(rows), "missing": 0, "ready_active": 0, "ready_guest": 0, "ready_p": 0, "with_unresolved": 0,
           "top_unknown": {}}
    top: dict[str, int] = {}
    for row in rows:
        if row["source"] == "missing":
            out["missing"] += 1
            continue
        for role in ROLE_LABELS:
            out[f"ready_{role}"] += bool(row["ready"].get(role))
        if any(row["unresolved"].values()):
            out["with_unresolved"] += 1
        for gap in {g.split(".")[0] if g.startswith(("panels.", "levels.")) else g
                    for gaps in row["unknown"].values() for g in gaps}:
            top[gap] = top.get(gap, 0) + 1
    out["top_unknown"] = dict(sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
    return out


def _battle_summary(rec: Mapping[str, Any]) -> dict:
    battle = rec.get("battle") or {}
    selected = rec["strategy"].get("evaluated_policy", rec["strategy"]["strategy"])
    profile = (battle.get("policies") or {}).get(selected)
    if not profile:
        return {"status": battle.get("status", "NOT_SIMULATED"), "policy": selected, "rows": [],
                "note": "未提供显式战斗情景：只有成长推荐，没有条件胜率"}
    rows = []
    for audition in ("s3_odotte", "legend", "semifinal", "final"):
        found = [r for r in profile["rows"] if r["audition"] == audition]
        if not found:
            continue
        rows.append({"audition": audition,
                     "worst_probability": round(min(s["win_probability"] or 0 for r in found for s in r["scenarios"]), 4),
                     "worst_lower_bound": round(min(r["worst_lower_bound"] for r in found), 4)})
    return {"status": battle.get("status"), "policy": selected, "rows": rows,
            "mean_probability": round(profile["mean_probability"], 4), "trials_per_scenario": battle.get("trials_per_scenario"),
            "confidence": battle.get("confidence"), "assumptions": list(profile.get("assumptions", []))[:12]}


def _summary(result: Mapping[str, Any], *, digest: str, excluded: int, usable: int, settings_used: bool,
             assumed: Mapping[str, int] | None = None) -> dict:
    rec = result["recommendation"]
    projection = rec["projection"]
    assumed = assumed or {}
    adapter_notes = [
        f"仅在 {usable} 张可计算的持有卡中搜索；{excluded} 张因未映射/未解析/Lv凸未知/机制未知未纳入。" if excluded
        else f"全部 {usable} 张持有卡均已纳入搜索。",
        *([f"{assumed['lb4_cap']} 张卡补全了缺失的 Lv/凸：未填凸数按4凸，未填等级按该凸上限；保留已填写值。"]
          if assumed.get("lb4_cap") else []),
        *([f"{assumed['p_te_eligible']} 张 P 卡的 True End 资格未知，按「有 TE」假设计算（显式开关）。"]
          if assumed.get("p_te_eligible") else []),
        *([f"{assumed['recognised_levels']} 张卡的 Lv/凸 来自游戏一览识别（RECOGNISED_NOT_USER_CONFIRMED，未经用户导入确认）。"]
          if assumed.get("recognised_levels") else []),
        *([f"{assumed['derived_levels']} 张卡的持有等级四维由 card_stats 两段线性公式推导（derived，非实机读数）。"]
          if assumed.get("derived_levels") else []),
        *([f"{assumed['owned_live_rule']} 张 S 卡的自带 Live 技能按稀有度/获取方式规则推算（Wiki 未收录，单卡可能有偏差）。"]
          if assumed.get("owned_live_rule") else []),
        "使用本地已解析卡库与私有推荐器设置；未提供的开局属性/四个装备技能/行动表采用 True End 推荐器的显式默认假设。"
        if settings_used else "没有私有推荐器设置：开局属性、四个装备技能和战斗情景均未提供，只做成长推荐。",
        "ProxyTERating 为 ESTIMATED 估计值，不是 True End 概率；采用前请核对。",
    ]
    return {
        "status": result.get("status"), "evaluation": rec.get("evaluation"),
        "strategy": rec["strategy"].get("strategy"), "evaluated_policy": rec["strategy"].get("evaluated_policy"),
        "ProxyTERating": rec.get("ProxyTERating"), "rating_label": rec.get("rating_label", "ESTIMATED"),
        "growth_readiness": rec.get("growth_readiness"), "true_end_probability": None,
        "growth": {k: projection.get(k) for k in ("effective_stats", "caps", "sp", "sp_required", "route_feasible",
                                                  "fans_by_season")},
        "battle": _battle_summary(rec),
        "assumptions": adapter_notes + list(projection.get("assumptions", [])),
        "unknowns": list(projection.get("unknowns", []))[:20],
        "warnings": list(projection.get("warnings", []))[:20],
        "limitations": list(result.get("limitations", [])),
        "data_snapshot_sha256": digest, "assumed": dict(assumed),
    }


def with_owned_rows(cards: Mapping[str, dict], inventory: Mapping[str, Mapping[str, Any]]
                    ) -> tuple[dict[str, dict], dict[str, str]]:
    """Cards whose owned (non-assumed) S level has no stored row get it from ``card_data_merge.with_owned_level``
    (in-game reading > stored row > the card_stats formula between two stored rows; never estimated).  Returns the new
    card map and ``{wiki_id: status}`` for each added row (``derived`` / ``in_game``)."""
    from agent.core.card_data_merge import with_owned_level
    out, added = dict(cards), {}
    for key, own in inventory.items():
        raw = cards.get(key)
        if raw is None or raw.get("kind") != "S" or own.get("assumed"):
            continue
        if str(own["level"]) in (raw.get("levels") or {}):
            continue
        new = with_owned_level(raw, own["level"], own["limit_break"])
        status = ((new.get("merge") or {}).get("derived_levels") or {}).get(str(own["level"]), {}).get("status")
        if status in ("derived", "in_game"):
            out[key] = new
            added[own["wiki_id"]] = status
    return out, added


def _level_upgrades(cards: Mapping[str, dict], inventory: Mapping[str, Mapping[str, Any]],
                    raw_cards: Mapping[str, Mapping] | None = None) -> dict:
    """练级提示 inputs: ``{true_end id: (CardData with the cap-level row, Ownership at that cap)}`` for each owned,
    non-assumed S card below its current 凸's level cap. A cap row that is neither stored nor derivable
    (``with_owned_level``) leaves the card out; 凸 never changes."""
    from agent.core.card_data_merge import with_owned_level
    from agent.core.card_stats import resolve_level_caps
    from agent.core.true_end.models import CardData, Ownership
    from agent.core.true_end.skill_identity import with_live_skill_names
    out = {}
    for key, own in inventory.items():
        raw = cards.get(key)
        if raw is None or raw.get("kind") != "S" or own.get("assumed"):
            continue
        cap = resolve_level_caps(raw)[0].get(own["limit_break"])
        if not isinstance(cap, int) or isinstance(cap, bool) or own["level"] >= cap:
            continue
        has_row = lambda card: any(str(k) == str(cap) for k in (card.get("levels") or {}))
        if not has_row(raw):
            raw = with_owned_level(raw, cap, own["limit_break"])
            if not has_row(raw):
                continue
        try:
            card = with_live_skill_names(CardData.from_dict(raw), (raw_cards or {}).get(key) or {})
        except (ValueError, TypeError, KeyError):
            continue
        out[key] = (card, Ownership(key, own["limit_break"], cap))
    return out


def _p_order(parsed: Mapping[str, Any], inventory: Mapping[str, Mapping[str, Any]], limit: int = MAX_P_CANDIDATES,
             ready_only: bool = False) -> list[str]:
    """P candidates: data-ready first, then SSR before other rarities, then newer IDs; at most ``limit``."""
    from agent.core.true_end.models import Ownership, extract_card_features
    rank = []
    for key, card in parsed.items():
        if card.kind != "P":
            continue
        own = inventory[key]
        try:
            ready = extract_card_features(card, Ownership(key, own["limit_break"], own["level"])).ready
        except ValueError:
            ready = False
        rarity = str(getattr(card, "rarity", "") or "")
        if ready or not ready_only:
            rank.append((not ready, 0 if "SSR" in rarity else 1, [-ord(c) for c in key], key))
    return [r[-1] for r in sorted(rank)][:limit]


def p_specialty(wiki_id: str) -> str | None:
    """Vo / Da / Vi of a P card (produce_config.card_specialty, the same rule as the UI 「自动」 主属性), None when unknown."""
    from agent.core.produce_config import card_specialty
    try:
        return card_specialty(wiki_id)
    except (OSError, ValueError):
        return None


# 推荐编队 主属性「自动」 (2026-10-31: the S cards decide the build
# attribute, the P card's does not matter): per Vo/Da/Vi, the pruning prior of the five strongest data-ready S cards of
# different idols; every attribute within this share of the best is searched.
ATTRIBUTE_TIE = .95


def _live_by_rule(card: Mapping[str, Any] | None) -> bool:
    """The card's own Live comes from :mod:`agent.core.owned_live_rule`, not its Wiki page."""
    origin = ((card or {}).get("provenance") or {}).get("owned_live_skills")
    return isinstance(origin, Mapping) and origin.get("source") == OWNED_LIVE_RULE


def support_attributes(parsed: Mapping[str, Any], inventory: Mapping[str, Any]) -> list[str]:
    """Build attributes (best first) the owned S cards support; [] when no S card is data-ready."""
    from agent.core.true_end.features import score_card_prior
    from agent.core.true_end.models import extract_card_features
    ready = []
    for key, card in parsed.items():
        if card.kind != "S" or key not in inventory:
            continue
        try:
            feature = extract_card_features(card, inventory[key])
        except ValueError:
            continue
        if feature.ready:
            ready.append(feature)
    strength = {}
    for stat, code in STRATEGY.items():
        ranked = sorted(ready, key=lambda f: (-score_card_prior(f, code, "HYBRID", "active"), f.card.card_id))
        idols, total = set(), 0.
        for feature in ranked:
            if feature.card.idol not in idols and len(idols) < MIN_SUPPORTS:
                idols.add(feature.card.idol)
                total += score_card_prior(feature, code, "HYBRID", "active")
        strength[stat] = total
    top = max(strength.values(), default=0.)
    if top <= 0:
        return []
    return sorted((s for s, v in strength.items() if v >= ATTRIBUTE_TIE * top), key=lambda s: -strength[s])


def strategy_stat(recommendation: Mapping[str, Any]) -> str | None:
    """Vo / Da / Vi of a single-attribute recommendation (its strategy, e.g. ``VO_SAFE``), else None."""
    name = str((recommendation.get("strategy") or {}).get("strategy") or "")
    stats = [s for s, code in STRATEGY.items() if code in name.split("_")]
    return stats[0] if len(stats) == 1 else None


def _true_end(owned: Mapping[str, Any], primary: str | None, *, cards: Mapping[str, dict], id_map: CardIdMap,
              settings: Mapping[str, Any], assume: Mapping[str, bool] | None = None,
              sources: Mapping[str, str] | None = None, p_card: str | None = None, pick_p: bool = False,
              specialty: Callable[[str], str | None] | None = None,
              raw_cards: Mapping[str, Mapping] | None = None,
              progress: Callable[[int, int], None] | None = None) -> dict:
    """True End attempt. Returns {"ok": bool, ...}; on failure ``fallback_reason`` explains why.

    ``pick_p`` (推荐编队): no P card is fixed; every data-ready owned P card (``MAX_P_CANDIDATES_PICK``) is searched, a P
    card with unknown TE eligibility counts as eligible (labelled assumption, as for a user-chosen P), and with
    ``primary == "auto"`` each P card is searched with its own specialty (``specialty``, default :func:`p_specialty`;
    unknown -> all three). The team with the highest ProxyTERating wins; its 主属性 is returned as ``primary``.

    ``progress(done, total)`` (推荐进度条): one step per searched P card plus the 练级提示 step."""
    from agent.core.true_end.growth import GrowthConfig
    from agent.core.true_end.models import CardData, load_inventory
    from agent.core.true_end.skill_identity import with_live_skill_names
    from agent.core.true_end.optimizer import SearchConfig, find_best_teams, scenario_from_dict, training_hints

    assume = {k: bool(v) for k, v in (assume or {}).items() if k in ASSUME_KEYS}
    details = clean_details(owned)
    base = {"ok": False, "coverage": {}, "needs_parsing": [], "rejected_cards": {}, "notes": [], "card_gaps": []}
    if not cards:
        return {**base, "fallback_reason": "card_db_empty", "notes": ["本地没有已解析的 True End 卡库"]}
    owned_ids = list(owned.get("p") or []) + list(owned.get("s") or [])
    if not len(id_map) and not any(i in cards for i in owned_ids):
        return {**base, "fallback_reason": "id_map_missing",
                "notes": ["卡库中没有任何持有卡 ID，也没有 10 位卡 ID → 旧 Wiki 卡的映射：运行 tools/card_id_map_build.py"]}
    coverage, inventory, needs = _card_coverage(owned, details, cards, id_map, assume)
    cards, derived = with_owned_rows(cards, inventory)
    base = {**base, "coverage": coverage, "needs_parsing": needs,
            "card_gaps": card_gaps(owned, cards, inventory, id_map, sources)}
    recognised = set(owned.get("recognised") or ())
    assumed = {"lb4_cap": sum(1 for o in inventory.values() if o.get("assumed")), "p_te_eligible": 0,
               "recognised_levels": sum(1 for o in inventory.values() if o["wiki_id"] in recognised and not o.get("assumed")),
               "derived_levels": len(derived),
               "owned_live_rule": sum(1 for k in inventory if _live_by_rule(cards.get(k)))}
    try:
        growth = GrowthConfig.from_dict(settings.get("growth", {}))
        search = {**UI_SEARCH, **settings.get("search", {})}
        fixed_strategies = "strategies" in settings.get("search", {})
        if not fixed_strategies:
            search["strategies"] = [STRATEGY[primary]] if primary in STRATEGY else list(STRATEGY.values())
        cfg = SearchConfig.from_dict(search)
        per_p_cfg = {stat: SearchConfig.from_dict({**search, "strategies": [STRATEGY[stat]]}) for stat in STRATEGY} \
            if pick_p and primary == "auto" and not fixed_strategies else {}
        scenarios = tuple(scenario_from_dict(s) for s in settings.get("battle_scenarios", ()))
        if len(scenarios) > 8:
            raise ValueError("at most eight explicit battle scenarios")
        used = {}
        for key, raw in cards.items():
            if key not in inventory:
                continue
            # The user's own selected P counts as TE-eligible when the data has no flag (shown as an assumption).
            chosen = (p_card is not None and (key == p_card or inventory[key].get("wiki_id") == p_card)) or pick_p
            if (assume.get("p_te_eligible") or chosen) and raw.get("kind") == "P" and raw.get("te_eligible") is None:
                raw = {**raw, "te_eligible": True}
                assumed["p_te_eligible"] += 1
            used[key] = raw
        parsed = {key: with_live_skill_names(CardData.from_dict(raw), (raw_cards or {}).get(key) or {})
                  for key, raw in used.items()}
        owned_true_end = load_inventory([{k: v for k, v in o.items() if k not in ("wiki_id", "assumed")}
                                         for o in inventory.values()])
    except (ValueError, TypeError, KeyError) as error:
        return {**base, "fallback_reason": "true_end_settings_invalid", "notes": [str(error)]}
    te_assumed = {inventory[k]["wiki_id"] for k, raw in used.items() if raw is not cards[k]}
    for row in base["card_gaps"]:
        row["assumed"] = [*(["lb4_cap"] if row.get("ownership") == "ASSUMED_LB4_CAP" else []),
                          *(["p_te_eligible"] if row["id"] in te_assumed else []),
                          *(["recognised_levels"] if row["id"] in recognised and row.get("ownership") == "OWNED" else []),
                          *([f"derived_level:{derived[row['id']]}"] if row["id"] in derived else [])]
    if p_card:
        # U05: the user's selected P card is fixed; only its teams are searched. Filter before the
        # MAX_P_CANDIDATES cut (2026-10-13 live: an owned, parsed P outside the newest six was reported unavailable).
        p_cards = _p_order({k: c for k, c in parsed.items()
                            if inventory.get(k, {}).get("wiki_id") == p_card or k == p_card}, inventory)
        if not p_cards:
            return {**base, "fallback_reason": "selected_p_unavailable",
                    "notes": ["所选 P 卡不在可计算的持有卡里（需在卡库中且已填写 Lv/凸）"]}
    elif pick_p:
        p_cards = _p_order(parsed, inventory, MAX_P_CANDIDATES_PICK, ready_only=True) or _p_order(parsed, inventory)
    else:
        p_cards = _p_order(parsed, inventory)
    supports = [c for c in parsed.values() if c.kind == "S"]
    if not p_cards:
        return {**base, "fallback_reason": "p_card_unavailable",
                "notes": ["没有可计算的持有 P 卡（需在卡库中且已填写 Lv/凸）"]}
    if len({c.idol for c in supports}) < MIN_SUPPORTS:
        return {**base, "fallback_reason": "too_few_s_cards",
                "notes": [f"可计算的持有 S 卡只有 {len(supports)} 张（{len({c.idol for c in supports})} 名偶像），至少需要 5 名不同偶像"]}
    best, best_stat, best_cfg, blocked, rejected, tried = None, None, cfg, [], {}, []
    specialty = specialty or p_specialty
    report = progress or (lambda done, total: None)
    steps = len(p_cards) + 1
    team_stats = support_attributes(parsed, owned_true_end) if per_p_cfg else []
    for index, p_card in enumerate(p_cards):
        report(index, steps)
        stat = primary if primary in STRATEGY else None
        p_cfg = cfg
        if team_stats:
            stat = team_stats[0] if len(team_stats) == 1 else None
            p_cfg = SearchConfig.from_dict({**search, "strategies": [STRATEGY[s] for s in team_stats]})
        elif per_p_cfg:
            stat = specialty(inventory[p_card]["wiki_id"]) if p_card in inventory else None
            p_cfg = per_p_cfg.get(stat, cfg)
        try:
            result = find_best_teams(parsed, owned_true_end, p_card, growth=growth, config=p_cfg, scenarios=scenarios)
        except ValueError as error:
            blocked.append(f"{p_card}: {error}")
            continue
        for key, why in (result.get("rejected_cards") or {}).items():
            rejected[inventory[key]["wiki_id"] if key in inventory else key] = "; ".join(map(str, why))[:200]
        if not result.get("recommendation"):
            blocked.append(f"{p_card}: {result.get('reason', result.get('status'))}")
            continue
        stat = stat or strategy_stat(result["recommendation"])
        tried.append({"p_card": inventory[p_card]["wiki_id"] if p_card in inventory else p_card, "primary": stat,
                      "rating": result["recommendation"]["ProxyTERating"]})
        if best is None or result["recommendation"]["ProxyTERating"] > best["recommendation"]["ProxyTERating"]:
            best, best_stat, best_cfg = result, stat, p_cfg
    for wiki_id in rejected:
        if coverage.get(wiki_id) in ("usable", "usable_assumed"):
            coverage[wiki_id] = "not_ready"
    base["rejected_cards"] = rejected
    for row in base["card_gaps"]:
        if row["id"] in rejected:
            row["rejected"] = rejected[row["id"]]
    if best is None:
        return {**base, "fallback_reason": "true_end_blocked", "notes": blocked[:6]}
    rec = best["recommendation"]
    to_wiki = {key: o["wiki_id"] for key, o in inventory.items()}
    # 练级提示: which owned S cards, levelled to their current 凸's cap, would raise this team most.
    report(steps - 1, steps)
    try:
        hints = training_hints(parsed, owned_true_end, rec, _level_upgrades(cards, inventory, raw_cards),
                               growth=growth, config=best_cfg, scenarios=scenarios)
    except (ValueError, KeyError, TypeError) as error:
        hints = []
        blocked.append(f"练级提示未计算：{str(error)[:120]}")
    hints = [{**h, "card_id": to_wiki.get(h["card_id"], h["card_id"]), "replaces": to_wiki.get(h["replaces"], h["replaces"])}
             for h in hints]
    report(steps, steps)
    usable = sum(1 for v in coverage.values() if v in ("usable", "usable_assumed"))
    snapshot = json.dumps({"inventory": [inventory[k] for k in sorted(inventory)], "cards": [asdict(parsed[k]) for k in sorted(parsed)],
                           "growth": asdict(growth), "search": asdict(cfg), "scenarios": [asdict(s) for s in scenarios],
                           "primary": primary, "pick_p": pick_p, "assume": assume}, sort_keys=True, ensure_ascii=False,
                          default=str).encode("utf-8")
    summary = _summary(best, digest=sha256(snapshot).hexdigest(), excluded=len(coverage) - usable, usable=usable,
                       settings_used=bool(settings), assumed={k: v for k, v in assumed.items() if v})
    if pick_p:
        summary["p_candidates"] = sorted(tried, key=lambda t: -t["rating"])[:MAX_P_CANDIDATES_PICK]
        summary["assumptions"].insert(0, f"P 卡由 Maa 选：比较了 {len(tried)} 张可计算的持有 P 卡"
                                         + (f"（主属性按持有 S 卡定为 {'/'.join(team_stats)}）" if team_stats
                                            else "（各按自身特长为主属性）" if per_p_cfg else "") + "，取评分最高的一队。")
    candidates = []
    for choice in (rec, *(best.get("alternatives") or ())):
        team = {"p_card": to_wiki[choice["p_card"]], "starters": [to_wiki[k] for k in choice["active"]],
                "guest": to_wiki[choice["guest"]]}
        identity = json.dumps({**team, "strategy": choice["strategy"]["strategy"]},
                              sort_keys=True, ensure_ascii=False).encode("utf-8")
        projection = choice["projection"]
        candidates.append({**team, "id": sha256(identity).hexdigest()[:16],
                           "primary": best_stat if best_stat else (primary if primary in STRATEGY else None),
                           "strategy": choice["strategy"],
                           "rating": choice["ProxyTERating"], "evaluation": choice.get("evaluation", "NO_BATTLE"),
                           "growth": {"stats": projection["effective_stats"], "sp": projection["sp"],
                                      "sp_required": projection["sp_required"], "stamina": projection["stamina"]},
                           "tradeoffs": choice.get("tradeoffs"), "style": choice.get("style"),
                           "card_tags": {to_wiki.get(k, k): v for k, v in (choice.get("card_tags") or {}).items()},
                           "unknowns": projection["unknowns"], "assumptions": projection["assumptions"]})
    return {**base, "ok": True, "p_card": to_wiki[rec["p_card"]], "starters": [to_wiki[k] for k in rec["active"]],
            "guest": to_wiki[rec["guest"]], "summary": summary, "notes": blocked[:6],
            "candidates": candidates, "training_hints": hints,
            "primary": best_stat if best_stat else (primary if primary in STRATEGY else None)}


def recommend(owned: Mapping[str, Any], primary: str | None, *, features: Mapping[str, Mapping[str, Any]] | None = None,
              cards: Mapping[str, dict] | None = None, id_map: CardIdMap | None = None,
              settings: Mapping[str, Any] | None = None, fallback: Callable | None = None,
              assume: Mapping[str, bool] | None = None, library_root: Path | None = None,
              p_card: str | None = None, pick_p: bool = False,
              specialty: Callable[[str], str | None] | None = None,
              raw_cards: Mapping[str, Mapping] | None = None,
              progress: Callable[[int, int], None] | None = None) -> dict:
    """Scorer-shaped result for ``auto_team_plan``. ``cards`` / ``id_map`` / ``settings`` default to the private files
    (the whole local library from :func:`load_library` / ``card_id_map.DEFAULT_MAP`` / ``DEFAULT_SETTINGS``); tests
    inject their own. ``assume`` = ``{"lb4_cap": bool, "p_te_eligible": bool}``, both off by default."""
    notes: list[str] = []
    library = None
    if (assume or {}).get("recognised_levels"):
        owned, note = with_recognised_levels(owned)
        notes.append(note)
    try:
        clean_details(owned)
    except ValueError as error:   # never trust malformed Lv/凸: drop them for both engines
        owned = {k: v for k, v in (owned or {}).items() if k != "details"}
        notes.append(f"Lv/凸 数据无效，已忽略：{error}")
    try:
        if id_map is None:
            id_map = CardIdMap.load(DEFAULT_MAP)
        if cards is None:
            library = load_library(library_root or SCDB_ROOT, id_map=id_map)
            cards = library["cards"]
            notes += library["notes"][:4]
            if raw_cards is None:
                from agent.core.wing_sp_catalog import _local_texts
                owned_ids = {str(cid) for cid in (*owned.get("p", ()), *owned.get("s", ())) if str(cid) in cards}
                raw_cards = _local_texts(owned_ids, root=library_root)
        if settings is None:
            settings = load_settings()
        attempt = _true_end(owned, primary, cards=cards, id_map=id_map, settings=settings, assume=assume,
                            sources=(library or {}).get("sources"), p_card=None if pick_p else p_card,
                            pick_p=pick_p, specialty=specialty, raw_cards=raw_cards, progress=progress)
    except (ValueError, TypeError, KeyError, OSError) as error:
        attempt = {"ok": False, "fallback_reason": "true_end_error", "notes": [str(error)[:200]], "coverage": {},
                   "needs_parsing": [], "rejected_cards": {}, "card_gaps": []}
    gaps = attempt.get("card_gaps", [])
    common = {"coverage": attempt["coverage"], "needs_parsing": attempt["needs_parsing"],
              "true_end_rejected": attempt.get("rejected_cards", {}), "card_gaps": gaps, "gap_counts": _gap_counts(gaps),
              "library": {k: library[k] for k in ("counts", "snapshots")} if library else None,
              "assume": {k: bool(v) for k, v in (assume or {}).items() if k in ASSUME_KEYS and v},
              "notes": notes[:6] + attempt.get("notes", [])}
    if attempt["ok"]:
        return {**common, "engine": ENGINE_TRUE_END, "fallback_reason": None, "status": "needs_confirmation",
                "p_card": attempt["p_card"], "starters": attempt["starters"], "guest": attempt["guest"],
                "confidence": None, "reasons": ["true_end_recommender", f"ProxyTERating {attempt['summary']['ProxyTERating']:.1f} (ESTIMATED)"],
                "rejected_cards": attempt["rejected_cards"], "summary": attempt["summary"],
                "candidates": attempt.get("candidates", []), "training_hints": attempt.get("training_hints", []),
                "primary": attempt.get("primary")}
    if fallback is None:
        from agent.core.wing_team import select_team as fallback
    from agent.core.produce_config import owned_card_records
    records = owned_card_records(owned, details_as_features(owned, features))
    if pick_p and primary not in STRATEGY:
        # 推荐编队 + 「自动」主属性 on the old scorer: try each stat, keep a team whose P card's specialty is that stat
        # (so the run's 「自动」 resolves to the stat the team was built for); else the Vo team.
        info, stat = None, "Vo"
        for cand in STRATEGY:
            got = fallback(records, cand)
            got = got.to_dict() if hasattr(got, "to_dict") else dict(got)
            if got.get("status") in ("ok", "needs_confirmation") and got.get("p_card") \
                    and (specialty or p_specialty)(got["p_card"]) == cand:
                info, stat = got, cand
                break
        if info is None:
            got = fallback(records, "Vo")
            info = got.to_dict() if hasattr(got, "to_dict") else dict(got)
    else:
        stat = primary if primary in STRATEGY else "Vo"
        result = fallback(records, stat)
        info = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    info = {**info, "primary": stat}
    return {**common, **info, "engine": ENGINE_WING, "fallback_reason": attempt["fallback_reason"], "summary": None,
            "reasons": [f"fallback:{attempt['fallback_reason']}", *list(info.get("reasons") or [])]}
