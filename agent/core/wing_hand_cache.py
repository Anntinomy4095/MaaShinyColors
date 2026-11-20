"""Hand-card portrait cache (2026-10-29 user: 识别手牌头像，免去 Live 第一回合的查看点击；设置 → 存储「手牌识别」可清理).

A Live hand card shows its idol's portrait and an attribute icon, not the skill name. Each time a selected card's
tooltip name is read, the lowered card's portrait signature (wing_live_policy.card_signature) and its attribute icon
are saved with that name. Later Lives match a hand card against these entries and take the multiplier from the
team's skill catalog (wing_skill_catalog), so a known card is played without an inspect tap. The P idol shows on
several cards and a skill's base / ＋ versions share a portrait: two entries that look alike but name different skills
are marked ``ambiguous`` and never used (the card is inspected as before), and every later tooltip read corrects
the cache. Derived data only: deleting the file just means one inspect tap per card again."""
from __future__ import annotations

import datetime
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent.core import wing_live_policy as wlp
from agent.core import wing_skill_catalog as wsk

PATH = Path(__file__).resolve().parents[2] / "dev_assets" / "private" / "wing-cache" / "hand-cards.json"
MAX_ENTRIES = 300
# The P idol's starting appeals are not on any card panel; their multiplier is the one read with the name.
# 2026-10-29 wing6 decision logs (19 Produces): skills learned from S cards and the P idol's own appeals mostly show the
# P idol's portrait (one picture, many names: always ambiguous), while the 「ボーカルアピールV+++++」-style appeals
# carry a support idol's portrait that maps to one name and multiplier in every run. Numerals I-VII, ＋ suffix.
GENERIC = re.compile(r"(ボーカル|ダンス|ビジュアル)アピール(?:[IV]{1,3})?\+*")


def load(path: Path | None = None) -> list[dict]:
    """Well-formed entries of the cache file ([] when missing or unreadable)."""
    try:
        data = json.loads((path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for e in (data.get("cards") if isinstance(data, Mapping) else None) or ():
        if (isinstance(e, Mapping) and isinstance(e.get("sig"), list) and isinstance(e.get("name"), str)
                and isinstance(e.get("attribute"), str)):
            out.append(dict(e))
    return out


def _save(entries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({"version": 1, "cards": entries}, ensure_ascii=False), encoding="utf-8")
    for attempt in range(3):
        try:
            os.replace(temp, path)
            return
        except PermissionError:      # Windows: a scanner may hold the file just written (2026-10-29 replay)
            if attempt == 2:
                raise
            time.sleep(0.05)


def canonical(name: str | None, catalog: Mapping[str, dict] | None) -> str | None:
    """The catalog's spelling of an OCR'd name, a generic starting appeal as read, else None (an unknown or misread
    name is not saved: it could not be used and would only collide with the right one)."""
    raw = wsk.normalise(name or "")
    entry = wsk.lookup(catalog, raw)
    if entry is not None:
        return entry["name"]
    return raw if GENERIC.fullmatch(raw) else None


def record(card: Mapping[str, Any], catalog: Mapping[str, dict] | None, path: Path | None = None,
           today: str | None = None) -> bool:
    """Save one read card {sig, attribute, name, multiplier}; True when the file changed. A look-alike entry with
    another name marks both ambiguous (never matched again until the file is cleared). A name that cannot be saved
    (unknown to this team's catalog) still marks look-alikes ambiguous: the P idol's portrait carries generic and
    S-card skills alike, so an unsaved name must not leave that portrait mapped to the generic appeal. A ``shared``
    picture (on two cards of one hand, wing_live_policy.portrait_record) is never saved either."""
    sig, attr, name = card.get("sig"), card.get("attribute"), canonical(card.get("name"), catalog)
    if not sig or not attr or not (name or wsk.normalise(card.get("name") or "")):
        return False
    path = path or PATH
    today = today or datetime.date.today().isoformat()
    entries = load(path)
    alike = [e for e in entries if e["attribute"] == attr and wlp.same_card(e["sig"], sig)]
    if not name or card.get("shared"):
        fresh = [e for e in alike if not e.get("ambiguous")]
        for e in fresh:
            e["ambiguous"] = True
        if fresh:
            _save(entries, path)
        return bool(fresh)
    same = next((e for e in alike if e["name"] == name), None)
    if same is not None and all(e["name"] == name for e in alike):
        mult = card.get("multiplier")
        fill = isinstance(mult, (int, float)) and same.get("multiplier") != mult
        if same.get("seen") == today and not fill:
            return False
        same["seen"] = today
        if fill:
            same["multiplier"] = mult         # a generic appeal first saved without its read multiplier
    else:
        conflict = any(e["name"] != name for e in alike)
        for e in alike:
            e["ambiguous"] = e.get("ambiguous") or conflict
        if same is None:
            entries.append({"sig": list(sig), "attribute": attr, "name": name, "multiplier": card.get("multiplier"),
                            "seen": today, **({"ambiguous": True} if conflict else {})})
        else:
            same["seen"] = today
    entries.sort(key=lambda e: e.get("seen") or "", reverse=True)
    _save(entries[:MAX_ENTRIES], path)
    return True


def resolve(entries: Iterable[Mapping[str, Any]], catalog: Mapping[str, dict] | None) -> list[dict]:
    """Entries usable for this team: a name in the team's skill catalog (its multiplier) or a generic starting appeal
    (the saved multiplier); ambiguous entries and look-alikes naming different skills are dropped."""
    out = []
    for e in entries:
        if e.get("ambiguous"):
            continue
        entry = wsk.lookup(catalog, e["name"])
        if entry is not None:
            mult = entry["multiplier"]
        elif GENERIC.fullmatch(e["name"]) and isinstance(e.get("multiplier"), (int, float)):
            mult = float(e["multiplier"])
        else:
            continue
        out.append({"sig": e["sig"], "attribute": e["attribute"], "name": e["name"], "multiplier": mult})
    return [a for a in out if not any(b is not a and b["attribute"] == a["attribute"] and b["name"] != a["name"]
                                      and wlp.same_card(a["sig"], b["sig"]) for b in out)]


def match(portraits: Iterable[Mapping[str, Any]] | None, sig, attribute) -> dict | None:
    """The resolved entry for a lowered hand card (portrait + attribute icon), or None."""
    if not sig or not attribute:
        return None
    for e in portraits or ():
        if e["attribute"] == attribute and wlp.same_card(e["sig"], sig):
            return {"name": e["name"], "multiplier": e["multiplier"], "attribute": attribute, "source": "portrait"}
    return None
