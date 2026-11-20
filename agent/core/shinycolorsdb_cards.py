"""Convert ShinyColorsDB card-detail JSON into True End ``CardData`` dictionaries.

Pure and offline: no network.  The output of :func:`convert_card` is accepted by
``agent.core.true_end.models.CardData.from_dict`` and a list of them wrapped as
``{"parser_version": ..., "cards": [...]}`` can be merged with
``tools/true_end_recommend.py --database``.

Rules ( for the evidence):

* ``card_id`` is the 10-digit Enza ID (the same ID the UI/inventory uses).
* Anything the DB does not carry or we cannot interpret stays unknown: it is
  listed in ``unknown_fields`` and/or kept as ``unsupported`` text, never 0.
* ``cardStatus`` is the stat row at the max level of a fully limit-broken card; only mapped for
  rarities whose cap was cross-checked against Wiki tables (S_SSR Lv80, S_SR Lv70, S_UR Lv90).  It is stored
  under that exact level only: a card owned at another Lv/LB never reads it (no silent Lv90 default).
* SP cost is *derived* from the panel row: 10 x (row + 1) + offset.  The offset is chosen by evidence
  priority (``sp_offset``): per-card in-game readings (``SP_INGAME_VERIFIED`` / ``SP_OFFSET_VERIFIED``) >
  the user-confirmed version rule (2026-10-07, Wiki summary: P-Twilight released 2025+ and S-Twilight
  released 2026+ cost 10 less, earlier Twilight normal; UR cost 10 less) > the DB ``panelSPOffset`` for
  ordinary cards.  The DB offset is not authoritative for Twilight (2040060210 says 0 but costs 10 less);
  other non-zero DB offsets outside the rule (Parallel P, Events S) and a missing release date stay
  contested per panel.
* Panel adjacency is not in the DB.  The branch layout is the triangle drawn by
  the DB front end (rows 1-4 = 1/2/3/4 hexes) and the unlock rule (a node is
  learnable when it is a row-1 root or shares an edge with a learned node of the
  same card, siblings included) was observed in game.
  ``panel_topology`` is emitted per slot-set family (tri10/sr8/r6/row5_13); the
  Twilight P row 5 uses the user-specified row 4 -> row 5 edges.  Memory-boost
  duplicate-slot layouts keep ``prerequisites_known=False`` and no topology.
* ``unknown_fields`` lists only gaps the True End feature extractor would not
  derive by itself; per-role readiness is in ``readiness``
  (``agent.core.card_data_merge.role_readiness``).
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from agent.core.true_end.wiki import MechanicsParser

PARSER_VERSION = "shinycolorsdb-card-v1"
SOURCE_PAGE = {"P": "https://shinycolors.moe/pcardinfo?uuid={}", "S": "https://shinycolors.moe/scardinfo?uuid={}"}
ATTR = {"vocal": "Vo", "dance": "Da", "visual": "Vi", "mental": "Me"}
JP_ATTR = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi", "メンタル": "Me"}
# cardStatus == Wiki stat row at this level (cross-checked on Wiki tables); other rarities stay unknown.
# S_UR: 【HoP PoP らびっつ】三峰結華 wikiwiki Lv90 row 423/212/212/195 == cardStatus.
STATUS_LEVEL = {"S_SSR": 80, "S_SR": 70, "S_UR": 90}
# DB/front-end SP rule conflicts with reviewed Wiki values for these getMethods.
SP_CONTESTED_METHODS = frozenset({"TwilightCollection"})
# In-game W.I.N.G. skill-panel readings (振り返り panel, the 「SP: N」 label under every node), one per card,
# bound to the card through the unit-formation recognition and the branch's icon in the panel centre.  Each
# row lists the node kinds read in that SP tier (cap = 上限UP, live = note icon, passive = arrow icon); all
# observed costs were 10 x (row + 1).  A record only counts when the DB panels have exactly these kinds per
# row, so a wrong binding cannot silently clear a contested card.
_C, _L, _P, _ANY = "cap", "live", "passive", "?"  # "?" = padlocked node: cost visible, icon hidden
SP_INGAME_VERIFIED: dict[str, dict[str, Any]] = {
    "2040040090": {"rows": {1: (_C,), 2: (_C, _P), 3: (_C, _L, _P), 4: (_C, _C, _L, _P)},
                   "evidence": "wing-demo2-20261006-0106-02 #695 left branch (S4, Twilight)"},
    "2040050080": {"rows": {1: (_C,), 2: (_C, _C), 3: (_C, _P, _P), 4: (_C, _C, _L, _L)},
                   "evidence": "wing-demo2-20261006-0106-02 #695 right branch (S1)"},
    "2040060080": {"rows": {1: (_C,), 2: (_C, _C), 3: (_C, _P, _P), 4: (_C, _C, _L, _L)},
                   "evidence": "wing-demo2-20261006-0106-02 #695 lower-right branch (S2)"},
    "2040070080": {"rows": {1: (_L,), 2: (_C, _P), 3: (_C, _C, _P), 4: (_C, _L, _L, _L)},
                   "evidence": "wing-demo2-20261006-0106-02 #695 upper-left branch (guest)"},
    "2040110050": {"rows": {1: (_C,), 2: (_C, _L), 3: (_C, _P, _P), 4: (_C, _C, _L, _ANY)},
                   "evidence": "wing-demo3-20261006-1643-03 #395 lower-right branch (S2)"},
    "2040170060": {"rows": {1: (_P,), 2: (_C, _L), 3: (_P, _P, _P), 4: (_C, _L, _P, _ANY)},
                   "evidence": "wing-demo3-20261006-1643-03 #395 upper-left branch (guest)"},
}
_KIND_OF_TYPE = {"limit_break": _C, "active": _L, "passive": _P}
# Per-card SP offsets verified in game where the DB offset is wrong or contested.  Only these cards are cleared;
# no DB field explains the difference.
SP_OFFSET_VERIFIED: dict[str, dict[str, Any]] = {
    "2040060210": {"offset": -10, "db_offset": 0,
                   "evidence": "IN_GAME_VERIFIED: rows cost 10/20/30/40"},
    "2050070010": {"offset": -10, "db_offset": -10,
                   "evidence": "IN_GAME_VERIFIED: UR rows cost 10/20/30/40"},
    "2040070100": {"offset": 0, "db_offset": 0,
                   "evidence": "IN_GAME_VERIFIED: 2023 Twilight rows cost 20/30/40/50"},
}
# User-confirmed version rule (2026-10-07; the Wiki summary records when lower SP started).  Thresholds compare
# the DB ``releaseDate`` (YYYY-MM-DD, the in-game implementation date).
SP_RULE_SOURCE = "USER_RULE_2026-10-07 (Wiki summary: P-Twilight 2025+, S-Twilight 2026+, UR: SP -10)"
TWILIGHT_LOW_SP_FROM = {"P": "2025-01-01", "S": "2026-01-01"}


def sp_offset(raw: Mapping[str, Any]) -> tuple[int, bool, str]:
    """(offset, contested, provenance) for a raw DB card by evidence priority (module docstring)."""
    enza, db = str(raw.get("enzaId")), raw.get("panelSPOffset")
    db_ok = isinstance(db, int) and not isinstance(db, bool)
    verified = SP_OFFSET_VERIFIED.get(enza)
    if verified:
        if db_ok and db == verified["db_offset"]:
            return verified["offset"], False, f"per-card offset {verified['offset']}; {verified['evidence']}"
        return 0, True, "per-card reading no longer matches the DB offset (DB changed): contested"
    reading = _ingame_sp_match(raw)
    if reading:
        return 0, False, f"IN_GAME_VERIFIED {reading}"
    ctype, date = str(raw.get("cardType") or ""), str(raw.get("releaseDate") or "")[:10]
    dated = re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is not None
    note = f"; DB panelSPOffset {db}" if db_ok else ""
    if ctype.endswith("_UR"):
        return -10, False, f"rule offset -10 (UR); {SP_RULE_SOURCE}{note}"
    if raw.get("getMethod") in SP_CONTESTED_METHODS:
        threshold = TWILIGHT_LOW_SP_FROM.get(ctype[:1])
        if not dated or threshold is None:
            return 0, True, "Twilight card without a usable release date/kind: contested"
        offset = -10 if date >= threshold else 0
        return offset, False, f"rule offset {offset} (Twilight {ctype[:1]}, released {date}); {SP_RULE_SOURCE}{note}"
    if not db_ok:
        return 0, True, "panelSPOffset missing: contested"
    if db != 0:
        return db, True, f"DB panelSPOffset {db} outside the verified rule: contested"
    return 0, False, "DB panelSPOffset 0 (ordinary rule, in-game verified on ordinary SSRs)"
# Branch layout (DataSite panel-info otherAxis, rows 1-4) and in-game unlock rule
STANDARD_SLOTS = (11, 21, 22, 31, 32, 33, 41, 42, 43, 44)
TOPOLOGY_SOURCE = ("https://github.com/ShinyColorsDB/ShinyColorsDB-DataSite/blob/"
                   "2917af4ecd7535de654c757e246988a22ac0ba31/src/app/shared/components/panel-info/panel-info.component.ts")
TOPOLOGY_LOCATOR = ("otherAxis rows 1-4 (flat hex triangle, positions monotone along each row); in-game unlock rule "
                    "wing-demo2-20261006-0106-02 #2628/#2629/#2639 and wing-demo3-20261006-1643-03 #461")
# Cards whose node kinds were matched position by position on in-game frames (not only per row).
TOPOLOGY_CARD_VERIFIED = {
    "2040050080": "wing-demo2-02 #2628/#2639 right branch: sibling 33 opened after 32 only",
    "2040060080": "wing-demo2-02 #2629 lower-right branch: sibling 33 open with 22 unlearned",
    "1040040070": "wing-demo3-03 #461 P branch: 21/32/33 open, 31/42/43/44 unreachable",
}


def branch_edges(slots: tuple[int, ...] = STANDARD_SLOTS) -> list[tuple[int, int]]:
    """Shared hex edges of one card's triangle: row neighbours and the two nodes above in the next row."""
    present = set(slots)
    edges = []
    for slot in sorted(present):
        row, pos = divmod(slot, 10)
        for other in (slot + 1, (row + 1) * 10 + pos, (row + 1) * 10 + pos + 1):
            orow, opos = divmod(other, 10)
            if other in present and 1 <= opos <= orow:
                edges.append((slot, other))
    return edges


# Skill-panel topology families (keyed by the slot set).  All are subsets of one flat-hex triangle per card
# (row r has positions 1..r; DataSite panel-info draws SR row 4 at positions 2-3 and Twilight P row 5 at 2-4).
# tri10 was observed in game; the others reuse the same template and carry a lower confidence.
SR_SLOTS = (11, 21, 22, 31, 32, 33, 42, 43)
R_SLOTS = (11, 21, 22, 31, 32, 33)
ROW5_SLOTS = STANDARD_SLOTS + (52, 53, 54)
# Twilight P row 5: three nodes laid out like row 3.  An outer row-4 node opens only the
# row-5 node on its side; each middle row-4 node opens the two row-5 nodes next to it.  No row-5 sibling edge
# is assumed (the user described only row 4 -> row 5 unlocks; "only" is read conservatively).
ROW5_EDGES = ((41, 52), (42, 52), (42, 53), (43, 53), (43, 54), (44, 54))
ROW5_CONFIDENCE = "USER_SPECIFIED_2026-10-07"
TOPOLOGY_FAMILIES = {
    STANDARD_SLOTS: ("tri10", "IN_GAME_RULE_STANDARD_LAYOUT"),
    SR_SLOTS: ("sr8", "TEMPLATE_SUBSET_DATASITE_GEOMETRY"),
    R_SLOTS: ("r6", "TEMPLATE_SUBSET_DATASITE_GEOMETRY"),
    ROW5_SLOTS: ("row5_13", ROW5_CONFIDENCE),
}


def family_edges(slots: tuple[int, ...]) -> list[tuple[int, int]]:
    """Edges of one card's branch: rows 1-4 by the shared-hex rule, row 5 by the user-specified unlocks."""
    if tuple(sorted(slots)) == ROW5_SLOTS:
        return branch_edges(STANDARD_SLOTS) + list(ROW5_EDGES)
    return branch_edges(tuple(slots))


def topology_family(raw_panels: list[Mapping[str, Any]]) -> tuple[str, str | None]:
    """(family, confidence) of a card's raw panel slots; ("anomaly:...", None) when no verified family fits."""
    slots = [p.get("panelSlot") for p in raw_panels]
    if any(not isinstance(x, int) or isinstance(x, bool) for x in slots):
        return "anomaly:non-integer slot", None
    if len(set(slots)) != len(slots):
        return "anomaly:duplicate slot (memory-boost variant)", None
    family = TOPOLOGY_FAMILIES.get(tuple(sorted(slots)))
    return family if family else ("anomaly:slot set " + ",".join(map(str, sorted(slots))), None)


def _topology(raw_panels: list[Mapping[str, Any]], panels: list[dict[str, Any]], enza: str) -> dict[str, Any] | None:
    family, confidence = topology_family(raw_panels)
    if confidence is None:
        return None
    slots = tuple(sorted(p["panelSlot"] for p in raw_panels))
    if {p["id"] for p in panels} != {f"scdb-{x}" for x in slots}:
        return None  # a panel was dropped (unknown cost/LB): edges would point at a missing node
    if family == "tri10" and enza in TOPOLOGY_CARD_VERIFIED:
        confidence = "IN_GAME_CARD_VERIFIED"
    return {"roots": ["scdb-11"], "edges": [[f"scdb-{a}", f"scdb-{b}"] for a, b in family_edges(slots)],
            "complete": True, "source_url": TOPOLOGY_SOURCE, "locator": TOPOLOGY_LOCATOR,
            "family": family, "confidence": confidence, "evidence": TOPOLOGY_CARD_VERIFIED.get(enza),
            "rule": "learnable when root (row 1) or sharing an edge with a learned node of the same card"}


def _ingame_sp_match(raw: Mapping[str, Any]) -> str | None:
    """Evidence string when the card's in-game panel reading matches its DB rows, else None."""
    record = SP_INGAME_VERIFIED.get(str(raw.get("enzaId")))
    if not record or raw.get("panelSPOffset") != 0:
        return None
    rows: dict[int, list[str]] = {}
    for panel in raw.get("cardPanels") or []:
        slot = panel.get("panelSlot")
        if not isinstance(slot, int) or str(panel.get("skillTitle") or "").startswith("[MB]"):
            return None
        rows.setdefault(slot // 10, []).append(_KIND_OF_TYPE.get(panel.get("skillType"), "other"))
    if set(rows) != set(record["rows"]):
        return None
    for row, seen in record["rows"].items():
        db = list(rows[row])
        if len(db) != len(seen):
            return None
        for kind in seen:
            if kind != _ANY:
                if kind not in db:
                    return None
                db.remove(kind)
    return record["evidence"]
# DB unitInfo.unitHiragana; equals the unit names used by reviewed Wiki records.
UNIT_NAMES = {1: "イルミネーションスターズ", 2: "アンティーカ", 3: "放課後クライマックスガールズ",
              4: "アルストロメリア", 5: "ストレイライト", 6: "ノクチル", 7: "シーズ", 8: "コメティック"}
# desc number = skillLevel x factor for these support kinds (checked per record).
SUPPORT_FACTOR = {"bond": 5, "mastery_stamina": 3, "mastery_sp": 1, "mastery_stat": 1, "mastery_cap": 1,
                  "unit_mastery_sp": 1, "unit_mastery_stat": 1, "unit_mastery_cap": 1, "unit_mastery_stamina": 3}
UNSUPPORTED_SUPPORT = {"unknown", "stamina_support", "rest_support", "trouble_guard", "facility_mastery",
                       "stay_mastery", "promise_recovery"}
EVENT_UNSPECIFIED = "event-unspecified"


def _norm(text: str | None) -> str:
    """Full-width punctuation to the ASCII forms the Wiki parser expects."""
    table = str.maketrans({"％": "%", "＋": "+", "＆": "&", "　": " ", "－": "-"})
    return re.sub(r"\s+", " ", (text or "").translate(table)).strip()


def _sp_cost(slot: Any, offset: int) -> tuple[int, int, int] | None:
    if not isinstance(slot, int) or isinstance(slot, bool):
        return None
    row, col = divmod(slot, 10)
    if not (1 <= row <= 5 and 1 <= col <= 4):
        return None
    cost = 10 * (row + 1) + offset
    return (cost, row, col) if cost > 0 else None


def _cap_effects(panel: Mapping[str, Any]) -> dict[str, float] | None:
    effects: dict[str, float] = {}
    for e in panel.get("skillEffects") or []:
        if e.get("effectType") != "limit_break" or e.get("attribute") not in ATTR:
            return None
        effects[ATTR[e["attribute"]]] = float(e["value"])
    desc = _norm(panel.get("skillDesc"))
    m = re.fullmatch(r"((?:Vocal|Dance|Visual|メンタル)(?:&(?:Vocal|Dance|Visual|メンタル))*)上限\+(\d+)", desc)
    if not effects or not m:
        return None
    named = {JP_ATTR[a] for a in m[1].split("&")}
    if named != set(effects) or any(v != float(m[2]) for v in effects.values()):
        return None  # JSON and text disagree: do not pick one silently
    return effects


def _live_effects(panel: Mapping[str, Any], panel_id: str) -> dict[str, Any] | None:
    desc = _norm(panel.get("skillDesc"))
    live = MechanicsParser.live(desc, panel_id)
    if not live:
        return None
    damage: dict[str, float] = {}
    excellent = 0.0
    buffs: set[tuple[str, float, int]] = set()
    for e in panel.get("skillEffects") or []:
        if e.get("effectType") in ("damage", "direct_damage"):  # S cards use "damage", P cards "direct_damage"
            if e.get("attribute") == "excellent":  # "Excellent N倍アピール": target attribute, always Excellent
                if excellent:
                    return None
                excellent = float(e["value"]) / 100
                continue
            if e.get("attribute") not in ("vocal", "dance", "visual"):
                return None
            key = ATTR[e["attribute"]]
            damage[key] = damage.get(key, 0) + float(e["value"]) / 100
        elif e.get("effectType") == "appeal_up" and e.get("attribute") in ATTR:
            buffs.add((ATTR[e["attribute"]], float(e["value"]) / 100, int(e.get("turn") or 0)))
    if damage != live["components"] or excellent != float(live.get("excellent", 0)):
        return None  # e.g. multi-hit: keep unknown, not a partial number
    if live.get("buff_attribute") and (live["buff_attribute"], live["buff_amount"], live["buff_turns"]) not in buffs:
        return None  # text buff not confirmed by effect JSON
    live["unsupported"] = list(live.get("unsupported", ()))
    return live


_COND_SUPPORTED = (
    (re.compile(r"(\d+)ターン以前"), lambda m: {"end_turn": int(m[1])}),
    (re.compile(r"(\d+)ターン以降"), lambda m: {"start_turn": int(m[1])}),
    (re.compile(r"メンタル(\d+)%以上"), lambda m: {"mental_ratio_min": int(m[1]) / 100}),
    (re.compile(r"メンタル(\d+)%以下"), lambda m: {"mental_ratio_max": int(m[1]) / 100}),
)


def _passive_effects(panel: Mapping[str, Any], panel_id: str) -> tuple[dict[str, Any], list[str]]:
    title, desc = _norm(panel.get("skillTitle")), _norm(panel.get("skillDesc"))
    buffs: dict[str, float] = {}
    for attrs, pct in re.findall(r"((?:Vocal|Dance|Visual|メンタル)(?:&(?:Vocal|Dance|Visual|メンタル))*)(\d+)%UP", title):
        for a in attrs.split("&"):
            buffs[JP_ATTR[a]] = int(pct) / 100
    cond = re.search(r"\[条件:(.*?)\]", desc)
    chance = re.search(r"\[確率:(\d+)%\]", desc)
    count = re.search(r"\[最大:(\d+)回\]", desc)
    extra_title = re.sub(r"(?:(?:Vocal|Dance|Visual|メンタル)(?:&(?:Vocal|Dance|Visual|メンタル))*)\d+%UP", "", title).strip(" /")
    simple: dict[str, Any] | None = None
    if (len(buffs) == 1 and not extra_title and chance and count and "[発動制限]" not in desc
            and set(buffs) <= {"Vo", "Da", "Vi"}):
        simple = {"id": panel_id, "attribute": next(iter(buffs)), "buff": next(iter(buffs.values())),
                  "probability": int(chance[1]) / 100, "max_activations": int(count[1])}
        if cond:
            for pattern, build in _COND_SUPPORTED:
                m = pattern.fullmatch(cond[1])
                if m:
                    simple.update(build(m))
                    break
            else:
                simple = None
    if simple is not None:
        return simple, []
    effects = dict(buffs=buffs, condition=cond[1] if cond else None,
                   probability=int(chance[1]) / 100 if chance else None,
                   max_activations=int(count[1]) if count else None)
    return effects, [f"passive requires battle interpreter: {title} / {desc}"]


def _panel(panel: Mapping[str, Any], offset: int, contested: bool, unknown: list[str],
           seen: set[str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    slot = panel.get("panelSlot")
    title = _norm(panel.get("skillTitle"))
    pid = f"scdb-{slot}" + ("-mb" if title.startswith("[MB]") else "")
    while pid in seen:
        pid += "-dup"
    seen.add(pid)
    geometry = dict(panel_id=pid, panel_slot=slot, db_panel_id=panel.get("panelId"),
                    is_gold=panel.get("panelIsGold"), release_desc=panel.get("panelReleaseDesc"))
    cost = _sp_cost(slot, offset)
    lb = panel.get("panelReleaseEvolution")
    if cost is None or not isinstance(lb, int) or not 0 <= lb <= 4:
        unknown.append(f"panels.{pid}")
        return None, geometry
    geometry.update(row=cost[1], col=cost[2])
    if contested:
        unknown.append(f"panels.{pid}.sp_cost")
    release = _norm(panel.get("panelReleaseDesc"))
    event = EVENT_UNSPECIFIED if (panel.get("panelReleaseByEvent") or "イベント" in release) else None
    kind, effects, unsupported = "unknown", {}, []
    stype = panel.get("skillType")
    extra = panel.get("extraEffect")
    extra_note = [f"extraEffect {extra.get('extraEffectType')}: {_norm(extra.get('extraEffectDesc'))}"] if extra else []
    if title.startswith("[MB]") or "メモリーブースト" in release:
        unsupported.append(f"memory boost variant: {title} / {_norm(panel.get('skillDesc'))}")
    elif stype == "limit_break":
        caps = _cap_effects(panel)
        if caps:
            kind, effects = "cap", caps
        else:
            unsupported.append(f"cap text/JSON mismatch: {title} / {_norm(panel.get('skillDesc'))}")
    elif stype == "active":
        live = _live_effects(panel, pid)
        if live:
            live["unsupported"] += extra_note
            live["skill_name"] = title
            kind, effects = "live", live
        else:
            unsupported.append(f"active not interpretable: {title} / {_norm(panel.get('skillDesc'))}")
    elif stype == "passive":
        kind = "passive"
        effects, unsupported = _passive_effects(panel, pid)
    else:
        unsupported.append(f"{stype}: {title} / {_norm(panel.get('skillDesc'))}")
    if kind != "live":
        unsupported += extra_note
    if event and "特定のアイドルイベント" in release and not panel.get("panelReleaseByEvent"):
        geometry["release_note"] = "panelReleaseByEvent false but panelReleaseDesc names an idol event; event-gated"
    return dict(id=pid, kind=kind, sp_cost=cost[0], required_lb=lb, prerequisites=[],
                prerequisites_known=False, effects=effects, event=event, unsupported=unsupported), geometry


def _support_skills(raw: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for s in raw:
        grouped.setdefault(str(s.get("skillName")), []).append(s)
    result = []
    for name, rows in grouped.items():
        kind, attr, room = MechanicsParser.support(name)
        levels: dict[int, float] = {}
        unsupported: list[str] = []
        for r in sorted(rows, key=lambda r: (r.get("gainedAt") or 0)):
            gained, lv = r.get("gainedAt"), r.get("skillLevel")
            if not isinstance(gained, int) or gained < 1 or not isinstance(lv, int) or lv < 0:
                unsupported.append(f"unreadable step: {r.get('skillDesc')}")
                continue
            levels[gained] = float(lv)
            factor = SUPPORT_FACTOR.get(kind)
            numbers = [int(n) for n in re.findall(r"(\d+)", _norm(r.get("skillDesc")).replace("1人につき", ""))]
            if factor and (not numbers or numbers[-1] != lv * factor):
                unsupported.append(f"description amount differs from skillLevel x{factor}: {r.get('skillDesc')}")
        if kind == "unknown" and rows[0].get("skillType"):
            kind = f"unmodeled_{rows[0]['skillType']}"  # the DB names the mechanic; growth does not model it
            unsupported.append(f"mechanic not modeled: {_norm(rows[-1].get('skillDesc'))}")
        elif kind in UNSUPPORTED_SUPPORT:
            unsupported.append(_norm(rows[-1].get("skillDesc")))
        result.append(dict(id=name, kind=kind, levels=levels, attribute=attr, room=room,
                           unsupported=unsupported, scdb_type=rows[0].get("skillType"),
                           scdb_attribute=rows[0].get("skillAttribute")))
    return result


def _p_extras(raw: Mapping[str, Any]) -> dict[str, Any]:
    memory = [dict(id=m.get("memoryId"), title=m.get("memoryTitle"), desc=_norm(m.get("memoryDesc")),
                   effects=[dict(type=e.get("effectType"), attribute=e.get("attribute"), value=e.get("value"))
                            for e in m.get("memoryEffects") or []],
                   extra=_norm((m.get("extraEffect") or {}).get("extraEffectDesc")) or None)
              for m in raw.get("cardMemoryAppeals") or []]
    aptitude = {f"{a.get('aptClass')}.{a.get('aptType')}": a.get("aptStepName")
                for a in raw.get("cardProduceAptitude") or []}
    events = [dict(id=e.get("eventId"), category=e.get("eventCategory"), title=e.get("eventTitle"))
              for e in raw.get("cardIdolEvents") or []]
    return dict(memory_appeals=memory, aptitude=aptitude, idol_events=events,
                memory_charge_skills=raw.get("memoryChargeSkills") or [],
                card_status_raw=raw.get("cardStatus"))


def convert_card(raw: Mapping[str, Any], *, content_sha256: str | None = None,
                 retrieved_at: str | None = None, api_url: str | None = None) -> dict[str, Any]:
    enza, ctype = raw.get("enzaId"), str(raw.get("cardType") or "")
    if not (isinstance(enza, str) and re.fullmatch(r"\d{10}", enza)) or ctype[:2] not in ("P_", "S_"):
        raise ValueError("not a ShinyColorsDB P/S card detail")
    kind = ctype[0]
    idol = (raw.get("idol") or {}).get("idolName") or ""
    source_url = SOURCE_PAGE[kind].format(raw.get("cardUuid"))
    unknown: list[str] = []
    raw_offset = raw.get("panelSPOffset")
    if not isinstance(raw_offset, int) or isinstance(raw_offset, bool):
        unknown.append("panelSPOffset")
    offset, contested, ingame = sp_offset(raw)
    prov_base = dict(url=api_url or source_url, page_url=source_url, retrieved_at=retrieved_at,
                     content_sha256=content_sha256, parser_version=PARSER_VERSION,
                     card_hash=raw.get("cardHash"), status="SCDB_PARSED")
    provenance: dict[str, dict[str, Any]] = {}

    levels: dict[int, dict[str, float]] = {}
    status = raw.get("cardStatus") or {}
    if kind == "S":
        level = STATUS_LEVEL.get(ctype)
        if level and all(isinstance(status.get(k), int) for k in ATTR):
            levels[level] = {ATTR[k]: float(status[k]) for k in ATTR}
            provenance["levels"] = {**prov_base, "locator": "cardStatus", "status": "SCDB_INTERPRETED",
                                    "confidence": "high", "note": f"cardStatus = Lv{level} (max) row"}
    # Per-level stats, max_levels, the owned Live skill (supportFightSkills is a Fes fight skill) and P te_eligible
    # are not in the DB.  They stay empty but are not listed in unknown_fields: extract_card_features derives exactly
    # those gaps for the owned level.  P initial_stats/produce_events/memory_appeal are not consumed by True End.
    missing = (["levels.per_level", "max_levels", "owned_live_skills"] if kind == "S"
               else ["initial_stats", "produce_events", "memory_appeal", "te_eligible"])

    panels, geometry, seen = [], [], set()
    for p in sorted(raw.get("cardPanels") or [], key=lambda p: (p.get("panelSlot") or 0, str(p.get("panelId")))):
        converted, geo = _panel(p, offset, contested, unknown, seen)
        geometry.append(geo)
        if converted:
            panels.append(converted)
    family = topology_family(raw.get("cardPanels") or [])[0]
    provenance["panels"] = {**prov_base, "locator": "cardPanels", "status": "SCDB_PARSED",
                            "sp_cost": f"DERIVED 10*(row+1){offset:+d}" + (" (contested)" if contested else "")
                            + f"; {ingame}",
                            "prerequisites": "UNKNOWN (grid only)"}
    topology = _topology(raw.get("cardPanels") or [], panels, enza)
    if topology:
        provenance["panels"]["prerequisites"] = f"panel_topology {topology['confidence']}"

    support, events = [], []
    if kind == "S":
        support = _support_skills(raw.get("cardSupportSkills") or [])
        provenance["support_skills"] = {**prov_base, "locator": "cardSupportSkills",
                                        "note": "levels = {gainedAt: skillLevel}"}
        events = [dict(id=str(e.get("eventName")), stats={"Vo": e.get("eventAddVo"), "Da": e.get("eventAddDa"),
                       "Vi": e.get("eventAddVi"), "Me": e.get("eventAddMe"), "SP": e.get("eventAddSp")})
                  for e in raw.get("cardSupportEvents") or []]
        if any(not isinstance(v, int) for e in events for v in e["stats"].values()):
            unknown.append("events")
            events = []
        provenance["events"] = {**prov_base, "locator": "cardSupportEvents"}
        if not support:
            unknown.append("support_skills")
    unit_id = (raw.get("idol") or {}).get("unitId")
    unit = UNIT_NAMES.get(unit_id)
    if unit:
        provenance["unit"] = {**prov_base, "locator": "idol.unitId", "unit_id": unit_id}
    if not idol:
        unknown.append("idol")

    card = dict(card_id=enza, name=str(raw.get("cardName") or enza), idol=idol or "UNKNOWN", kind=kind,
                status="PARTIAL", levels=levels, max_levels={}, owned_live_skills=[],
                support_skills=support, panels=panels, events=events, provenance=provenance,
                source_url=source_url, rarity=ctype, implementation_date=raw.get("releaseDate"),
                initial_stats={}, unknown_fields=sorted(set(unknown)), unsupported=[], unit=unit,
                te_eligible=None, panel_topology=topology)
    card["scdb"] = dict(enza_id=enza, card_uuid=raw.get("cardUuid"), card_type=ctype,
                        get_method=raw.get("getMethod"), panel_sp_offset=raw.get("panelSPOffset"),
                        last_modified=raw.get("lastModified"), idea_mark=raw.get("ideaMark"),
                        panel_geometry=geometry, topology_family=family,
                        fight_skill=_norm((raw.get("supportFightSkills") or {}).get("ftComment")) or None,
                        proficiencies={p.get("proficiency"): p.get("value") for p in raw.get("cardProficiencies") or []},
                        **(_p_extras(raw) if kind == "P" else {}))
    from agent.core.card_data_merge import role_readiness  # local import: the merge module has no converter import
    card["readiness"] = role_readiness(card, source_gaps=missing)
    return card


def convert_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    """Convert every manifest entry; files whose sha256 drifted are refused."""
    snapshot_dir = Path(snapshot_dir)
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    cards = []
    for entry in manifest.get("entries", []):
        body = (snapshot_dir / entry["file"]).read_bytes()
        if sha256(body).hexdigest() != entry.get("sha256"):
            raise ValueError(f"snapshot checksum mismatch: {entry['file']}")
        cards.append(convert_card(json.loads(body.decode("utf-8")), content_sha256=entry["sha256"],
                                  retrieved_at=entry.get("retrieved_at"), api_url=entry.get("url")))
    if len({c["card_id"] for c in cards}) != len(cards):
        raise ValueError("duplicate card in snapshot")
    return {"parser_version": PARSER_VERSION, "source": "https://api.shinycolors.moe",
            "snapshot": str(snapshot_dir.name), "cards": sorted(cards, key=lambda c: c["card_id"])}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Convert a private ShinyColorsDB snapshot to a True End card database")
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    payload = convert_snapshot(args.snapshot)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(payload['cards'])} card(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
