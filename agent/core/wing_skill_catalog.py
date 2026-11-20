"""Live-skill catalog of the Produce's own team (2026-10-29 user: 「不用读技能说明 … 已经有S卡库了」).

The Live tooltip's name line identifies the selected card; its multiplier then comes from this catalog instead of
OCR of the small description lines (wing4b: 「かわいいひと」's two-line description read as garbage and stopped the run).
Sources: the team's cards in the local ShinyColorsDB snapshots (``cardPanels`` active skills: ``skillTitle`` +
``skillDesc``) and the Live skills read on this Produce's SP panel (``RunMemory.live_skills``, which win). Local data
only (``dev_assets/private/shinycolorsdb``), never committed; a missing snapshot only leaves the catalog smaller."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent.core.wing_skill_panel import classify_skill, normalise

SNAPSHOTS = Path(__file__).resolve().parents[2] / "dev_assets" / "private" / "shinycolorsdb"


def card_file(card_id: str, root: Path | None = None) -> Path | None:
    """The card's file (``<kind>-<id>-<hash>.json``) in the newest snapshot folder that has it, or None."""
    for folder in sorted((p for p in (root or SNAPSHOTS).glob("snapshot-*") if p.is_dir()), reverse=True):
        hit = next(iter(sorted(folder.glob(f"*-{card_id}-*.json"))), None)
        if hit is not None:
            return hit
    return None


def card_live_skills(card_id: str, root: Path | None = None) -> list[dict]:
    """Live skills on the card's panel: [{name, multiplier, attributes, source}] (unreadable file -> [])."""
    path = card_file(card_id, root)
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for panel in (raw.get("cardPanels") if isinstance(raw, Mapping) else None) or ():
        if not isinstance(panel, Mapping) or panel.get("skillType") != "active":
            continue
        name = normalise(panel.get("skillTitle") or "")
        info = classify_skill(name, normalise(panel.get("skillDesc") or ""))
        if name and info.get("kind") == "live_skill" and info.get("multiplier") is not None:
            out.append({"name": name, "multiplier": float(info["multiplier"]),
                        "attributes": list(info.get("attributes") or ()), "source": f"library:{card_id}"})
    return out


def build_catalog(card_ids: Iterable[str], observed: Iterable[Mapping[str, Any]] | None = None,
                  root: Path | None = None) -> dict[str, dict]:
    """{normalised name: entry}; a skill read on this Produce's panel replaces the library entry of that name."""
    catalog: dict[str, dict] = {}
    for card_id in card_ids:
        for entry in card_live_skills(str(card_id), root):
            catalog.setdefault(entry["name"], entry)
    for skill in observed or ():
        name = normalise((skill or {}).get("name") or "")
        mult = (skill or {}).get("multiplier")
        if name and isinstance(mult, (int, float)) and not isinstance(mult, bool):
            catalog[name] = {"name": name, "multiplier": float(mult),
                             "attributes": list(skill.get("attributes") or ()), "source": "panel"}
    return catalog


def _within_one(a: str, b: str) -> bool:
    """Edit distance <= 1 (one OCR slip in a name of 3+ characters)."""
    if min(len(a), len(b)) < 3 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def lookup(catalog: Mapping[str, dict] | None, name: str | None) -> dict | None:
    """Entry for an OCR'd tooltip name: exact, else the single entry one character away; None when unknown or
    ambiguous (e.g. 「かわいいひと」 vs 「かわいいひと+」 with the plus misread)."""
    key = normalise(name or "")
    if not key or not catalog:
        return None
    if key in catalog:
        return catalog[key]
    near = [entry for other, entry in catalog.items() if _within_one(key, other)]
    return near[0] if len(near) == 1 else None
