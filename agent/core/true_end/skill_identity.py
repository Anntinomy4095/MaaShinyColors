"""Strict acquired Live-skill identities and zero-cost duplicate panel bridges."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import re
import unicodedata


def strict_live_identity(name, effects, *, unsupported=()) -> str | None:
    """Unknown names/effects never merge. Panel IDs and unlock levels are metadata."""
    if unsupported or effects.get("unsupported"):
        return None
    name = name or effects.get("skill_name") or effects.get("name") or effects.get("id")
    if not isinstance(name, str) or not name.strip() or re.fullmatch(r"scdb-\d+(?:-mb)?(?:-dup)*", name):
        return None
    allowed = {"id", "name", "skill_name", "unlock_level", "unsupported", "components",
               "buff_attribute", "buff_amount", "buff_turns", "heal_fraction", "excellent"}
    if set(effects) - allowed:
        return None
    parts = effects.get("components") or {}
    if not parts and not effects.get("heal_fraction") and not effects.get("excellent"):
        return None
    try:
        body = {"components": {a: float(v) for a, v in parts.items()},
                "buff_attribute": effects.get("buff_attribute"),
                "buff_amount": float(effects.get("buff_amount", 0)),
                "buff_turns": float(effects.get("buff_turns", 0)),
                "heal_fraction": float(effects.get("heal_fraction", 0)),
                "excellent": float(effects.get("excellent", 0))}
        return json.dumps([unicodedata.normalize("NFKC", "".join(name.split())), body],
                          ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError, AttributeError):
        return None


def panel_skill_identity(panel) -> str | None:
    return strict_live_identity(None, panel.effects, unsupported=panel.unsupported) if panel.kind == "live" else None


def with_live_skill_names(card, raw):
    """Hydrate only this verified card's raw panel titles; never guess from slot IDs."""
    if str(raw.get("enzaId")) != card.card_id:
        return card
    names = {}
    for node in raw.get("cardPanels") or ():
        slot, title = node.get("panelSlot"), node.get("skillTitle")
        if type(slot) is int and isinstance(title, str) and title.strip():
            key = f"scdb-{slot}" + ("-mb" if title.startswith("[MB]") else "")
            if key in names:
                names[key] = None   # ambiguous source positions stay anonymous
            else:
                names[key] = title
    panels = tuple(replace(p, effects={**p.effects, "skill_name": names[p.id]})
                   if p.kind == "live" and names.get(p.id) else p for p in card.panels)
    return replace(card, panels=panels)


def mastered_identities(cards, learned=(), skills=()) -> set[str]:
    """Panel history survives forgetting; equipped skills only add known initial identities."""
    learned = set(learned)
    result = {key for skill in skills if (key := strict_live_identity(skill.id, asdict(skill))) is not None}
    result.update(key for card in cards for panel in card.panels
                  if f"{card.card.card_id}:{panel.id}" in learned
                  and (key := panel_skill_identity(panel)) is not None)
    return result


def priced_panel_sequence(purchases, mastered=()):
    """Yield ordered panel costs, sharing skills acquired earlier in this sequence."""
    history = set(mastered)
    for key, panel in purchases:
        identity = panel_skill_identity(panel)
        duplicate = identity is not None and identity in history
        yield key, panel, 0 if duplicate else panel.sp_cost, duplicate, identity
        if identity is not None:
            history.add(identity)
