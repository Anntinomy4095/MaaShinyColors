"""Auto Produce 自动编队: P + 4 S + guest for the game's official Auto, by the Auto rules (not the True End scorer).

Team strength barely changes Auto results, so
the choice serves the knowhow book an Auto run yields:

- the P idol names the new book, so the P comes from an idol outside the user's usual W.I.N.G. team (``avoid_idols``);
  among those the most raised card (level from its limit break), then rarity, then card id;
- S cards with ノウハウ発現マスタリー first (larger value at the card's level first: Auto 記録率 70.6% -> 82.4% at
  Lv5), then level, rarity and card id;
- no idol twice across P, S1–S4 and the guest, and no idol of the books Maa will carry
  (``book_idols``: S.T.E.P. refuses books of the P/S idols);
- the guest is one of the user's own S cards, chosen by the same order after the four S cards;
- 2026-10-31: after the mastery, cards with the support
  skills the Auto row's scenario values (:data:`SCENARIO_SKILLS`, unlocked at the card's level) come first; stamina
  skills lead in every lesson-based scenario, S.T.E.P. adds caps and 施設マスタリー, オーディションマスタリー counts
  only in W.I.N.G.;
- 2026-11-01 (「按目标词条」, agent/core/knowhow_auto_plan.py): in G.R.A.D. each support idol gives ひらめき of one fixed
  colour, so a colour quota (``colors``: {"Vo": n, ...} over the five supports) is filled first, in the same order,
  and the remaining places follow the order as before.

Pure module: owned cards come from the UI profile (``prefs.owned_cards``: ``p`` / ``s`` ids, ``details`` level /
limit break; a scanned level wins over the limit-break cap), card data from ``team_recommend.load_library``
(``idol``, ``kind``, ``rarity``, ``max_levels``, ``support_skills``).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agent.core.knowhow_auto_plan import HIRAMEKI

MASTERY = "ノウハウ発現マスタリー"
RARITY_RANK = {"UR": 4, "SSR": 3, "SR": 2, "R": 1, "N": 0}
# Support-skill kinds (team_recommend.load_library ``support_skills[].kind``) the community rates per scenario
# (research C7, F2, G4, L2, T2; W.I.N.G. Auto: team strength barely matters, the same stamina-first order). "" = the
# scenario is not checked: the lesson-based common ground.
_STAMINA = {"stamina_support": 3, "mastery_stamina": 3, "unit_mastery_stamina": 3}
_SP = {"mastery_sp": 2, "unit_mastery_sp": 2}
_CAP = {"mastery_cap": 1, "unit_mastery_cap": 1}
SCENARIO_SKILLS = {
    "": {**_STAMINA, **_SP, **_CAP},
    "WING": {**_STAMINA, **_SP, **_CAP, "audition_mastery_sp": 1, "audition_mastery_stat": 1},
    "FANS": {**_STAMINA, **_SP, **_CAP},
    "GRAD": {**_STAMINA, **_SP, "stay_mastery": 1},
    "LP": {**_STAMINA, **_SP, **_CAP},
    "STEP": {**_STAMINA, "mastery_cap": 3, "unit_mastery_cap": 3, "facility_mastery": 3,
             "unmodeled_perfect_mastery": 2, **_SP, "stay_mastery": 1},
}
DEFAULT_LB = 0   # a card without a recorded limit break counts as unbroken


def card_level(card: Mapping[str, Any], limit_break: int | None) -> int:
    """Level cap of the card at its limit break (``max_levels``), 0 when unknown."""
    levels = card.get("max_levels") or {}
    lb = DEFAULT_LB if limit_break is None else limit_break
    try:
        return int(levels.get(str(lb)) or 0)
    except (TypeError, ValueError):
        return 0


def mastery(card: Mapping[str, Any], level: int) -> float:
    """ノウハウ発現マスタリー value at ``level`` (the highest unlocked step), 0 without the skill."""
    best = 0.0
    for skill in card.get("support_skills") or ():
        if skill.get("id") != MASTERY:
            continue
        for threshold, value in (skill.get("levels") or {}).items():
            try:
                if int(threshold) <= level:
                    best = max(best, float(value))
            except (TypeError, ValueError):
                continue
    return best


def scenario_value(card: Mapping[str, Any], level: int, scenario: str = "") -> float:
    """Sum of :data:`SCENARIO_SKILLS` weights of the card's support skills unlocked at ``level`` (each kind once)."""
    weights = SCENARIO_SKILLS.get(scenario, SCENARIO_SKILLS[""])
    kinds = set()
    for skill in card.get("support_skills") or ():
        try:
            if any(int(threshold) <= level for threshold in (skill.get("levels") or {})):
                kinds.add(skill.get("kind"))
        except (TypeError, ValueError):
            continue
    return float(sum(weights.get(kind, 0) for kind in kinds))


def rarity(card: Mapping[str, Any]) -> int:
    return RARITY_RANK.get(str(card.get("rarity") or "").rsplit("_", 1)[-1], 0)


def _limit_break(details: Mapping[str, Any], card_id: str) -> int | None:
    value = (details.get(card_id) or {}).get("limit_break")
    return value if type(value) is int and 0 <= value <= 4 else None


def choose(owned: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]], *, avoid_idols: Iterable[str] = (),
           book_idols: Iterable[str] = (), scenario: str = "", colors: Mapping[str, int] | None = None) -> dict:
    """{"p_card", "s_cards" (4), "guest", "idols", "mastery" {card: value}, "notes"}; ``p_card`` is None (with a
    note) when no team can be built. Never invents a card: every id comes from ``owned``."""
    details = owned.get("details") or {}
    avoid, books = set(avoid_idols), set(book_idols)
    notes = []

    def rows(kind):
        out = []
        for card_id in owned.get(kind.lower()) or ():
            card = cards.get(str(card_id))
            if not card or card.get("kind") != kind or not card.get("idol"):
                continue
            scanned = (details.get(str(card_id)) or {}).get("level")
            level = (scanned if type(scanned) is int and 1 <= scanned <= 100
                     else card_level(card, _limit_break(details, str(card_id))))
            out.append({"id": str(card_id), "idol": card["idol"], "level": level, "rarity": rarity(card),
                        "mastery": mastery(card, level) if kind == "S" else 0.0,
                        "skills": scenario_value(card, level, scenario) if kind == "S" else 0.0})
        return out

    ps = [r for r in rows("P") if r["idol"] not in books]
    fresh = [r for r in ps if r["idol"] not in avoid]
    if ps and not fresh:
        notes.append("p_only_usual_idols")
    ps = sorted(fresh or ps, key=lambda r: (-r["level"], -r["rarity"], r["id"]))
    ss = sorted((r for r in rows("S") if r["idol"] not in books),
                key=lambda r: (-r["mastery"], -r["skills"], -r["level"], -r["rarity"], r["id"]))
    for p in ps:
        used, picked, quota = {p["idol"]}, [], dict(colors or {})
        for s in ss if quota else ():
            color = HIRAMEKI.get(s["idol"])
            if s["idol"] not in used and quota.get(color, 0) > 0 and len(picked) < 5:
                picked.append(s)
                used.add(s["idol"])
                quota[color] -= 1
        for s in ss:
            if len(picked) == 5:
                break
            if s["idol"] not in used:
                picked.append(s)
                used.add(s["idol"])
        picked.sort(key=ss.index)       # S1–S4 and the guest keep the usual order
        if len(picked) == 5:
            team = [p, *picked]
            return {"p_card": p["id"], "s_cards": [s["id"] for s in picked[:4]], "guest": picked[4]["id"],
                    "idols": [r["idol"] for r in team], "mastery": {s["id"]: s["mastery"] for s in picked if s["mastery"]},
                    "notes": notes}
    notes.append("no_p_card" if not ps else "not_enough_s_cards")
    return {"p_card": None, "s_cards": [], "guest": None, "idols": [], "mastery": {}, "notes": notes}
