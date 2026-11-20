"""ノウハウ条目 -> Auto 剧本 (其他剧本的 Auto「现在应该主要是为了词条」).

From the user's wanted entries (``prefs.knowhow_wanted``) and the scanned library: which entries are still missing,
which of those the official Auto can find and in which scenario, and which need a manual G.R.A.D. or say "Halo" run.
The facts come from ``knowhow_sources.json``: the wiki's Auto column
(unreliable by its own note, so an unknown cell stays unknown and is never planned), 転記, the G.R.A.D. ひらめき cost.

- An entry is missing while no book holds it at the wanted Lv (the ノウハウ page's matcher, ``WantedItem``).
- The scenario is the Auto scenario that serves the most missing Auto entries (an entry's ``prefer`` list first:
  思い出(ノウハウ) -> Landing Point, ファン感謝祭, research K4); ties follow :data:`SCENARIO_ORDER`. Without an Auto
  target :func:`plan` returns ``scenario`` None and the caller keeps its fallback.
- In G.R.A.D. a support's ひらめき colour is fixed per idol (:data:`HIRAMEKI`), so the targets'
  ひらめき costs become an idol-colour quota for the five supports (:func:`support_colors`).
- 育成方針 is not planned: no source shows it changes G.R.A.D. ひらめき or abilities (research K6).

Pure apart from :func:`load_sources`; no game input.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent.core.knowhow_books import KnowhowBookRecord, WantedItem, normalize_name

SOURCES_PATH = Path(__file__).with_name("knowhow_sources.json")
AUTO_SCENARIOS = ("WING", "FANS", "GRAD", "LP", "STEP")      # weekly_auto.SCENARIOS without "" (say "Halo" has no Auto)
SCENARIO_ORDER = ("GRAD", "LP", "FANS", "STEP", "WING")      # tie order: abilities first, then the 思い出 routes
COLORS = ("Vo", "Da", "Vi", "Me")
SUPPORT_SLOTS = 5          # S1–S4 and the guest all give ひらめき in G.R.A.D.
# G.R.A.D.編 idol -> ひらめき colour (wiki table, checked against two community line-ups).
HIRAMEKI = {
    **dict.fromkeys(("櫻木真乃", "幽谷霧子", "小宮果穂", "桑山千雪", "黛冬優子", "樋口円香", "斑鳩ルカ"), "Vo"),
    **dict.fromkeys(("八宮めぐる", "三峰結華", "西城樹里", "杜野凛世", "芹沢あさひ", "市川雛菜", "七草にちか"), "Da"),
    **dict.fromkeys(("風野灯織", "田中摩美々", "白瀬咲耶", "大崎甘奈", "大崎甜花", "浅倉透", "緋田美琴", "鈴木羽那"), "Vi"),
    **dict.fromkeys(("月岡恋鐘", "園田智代子", "有栖川夏葉", "和泉愛依", "福丸小糸", "郁田はるき", "七草はづき"), "Me"),
}
# :func:`classify` statuses: owned / auto (an Auto scenario finds it) / auto_unknown (the wiki's Auto cell is blank) /
# manual (Auto never finds it: G.R.A.D. by hand or say "Halo") / not_on_books (never written to a book) / unknown.
STATUSES = ("owned", "auto", "auto_unknown", "manual", "not_on_books", "unknown")


@dataclasses.dataclass(frozen=True)
class Source:
    entry: str
    match: str
    scenarios: tuple[str, ...]
    auto: bool | None
    transferable: bool | None
    hirameki: Mapping[str, int]
    prefer: tuple[str, ...] = ()
    confidence: str = "low"

    def covers(self, name: str) -> bool:
        key, have = normalize_name(self.entry), normalize_name(name)
        return have == key or (self.match == "prefix" and have.startswith(key))

    def auto_scenarios(self) -> tuple[str, ...]:
        """Where Auto can look for it, preferred routes first; () unless the wiki marks Auto ○."""
        if self.auto is not True:
            return ()
        own = tuple(s for s in self.scenarios if s in AUTO_SCENARIOS)
        return tuple(s for s in self.prefer if s in own) or own


def load_sources(path: Path = SOURCES_PATH) -> tuple[Source, ...]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    return tuple(Source(r["entry"], r["match"], tuple(r["scenarios"]), r["auto"], r["transferable"],
                        dict(r.get("hirameki") or {}), tuple(r.get("prefer") or ()), r.get("confidence", "low"))
                 for r in rows)


def source_for(name: str, sources: Iterable[Source]) -> Source | None:
    """The table row of a wanted name: an exact row first, else the longest prefix row that covers it."""
    rows = [s for s in sources if s.covers(name)]
    exact = [s for s in rows if normalize_name(s.entry) == normalize_name(name)]
    return (exact or sorted(rows, key=lambda s: -len(s.entry)) or [None])[0]


def owned_level(item: WantedItem, books: Iterable[KnowhowBookRecord]) -> int:
    """Highest Lv of the entry in the library (0 when no book holds it)."""
    return max((row.level or 0 for book in books for row in book.rows if item.matches_name(row.name)), default=0)


def classify(item: WantedItem, books: Iterable[KnowhowBookRecord], sources: Iterable[Source]) -> dict:
    level = owned_level(item, books)
    src = source_for(item.name, sources)
    out = {"name": item.name, "level": level, "scenarios": list(src.scenarios) if src else []}
    if level >= item.min_level:
        status = "owned"
    elif src is None:
        status = "unknown"
    elif src.transferable is False:
        status = "not_on_books"
    elif src.auto_scenarios():
        status, out["scenarios"] = "auto", list(src.auto_scenarios())
    elif src.auto is None and any(s in AUTO_SCENARIOS for s in src.scenarios):
        status = "auto_unknown"
    else:
        status = "manual"
    return {**out, "status": status}


def support_colors(hirameki: Mapping[str, int], slots: int = SUPPORT_SLOTS) -> dict[str, int]:
    """ひらめき cost -> how many of the five supports should be idols of each colour (largest remainder; ALL comes
    from SSR/UR training at random and is not a colour to pick). Empty without a Vo/Da/Vi/Me cost."""
    need = {c: hirameki.get(c, 0) for c in COLORS if hirameki.get(c, 0) > 0}
    total = sum(need.values())
    if not total:
        return {}
    shares = {c: v * slots / total for c, v in need.items()}
    out = {c: int(v) for c, v in shares.items()}
    for c in sorted(shares, key=lambda c: (-(shares[c] - out[c]), COLORS.index(c)))[:slots - sum(out.values())]:
        out[c] += 1
    return {c: n for c, n in out.items() if n}


def plan(wanted: Iterable[WantedItem], books: Iterable[KnowhowBookRecord], sources: Iterable[Source] | None = None) -> dict:
    """{"scenario" (None without an Auto target), "targets" (missing entries that scenario can find), "entries"
    (every wanted entry with its status), "colors" (G.R.A.D. support colour quota or {})}."""
    sources = tuple(load_sources() if sources is None else sources)
    books = tuple(books)
    entries = [classify(item, books, sources) for item in wanted]
    score: dict[str, int] = {}
    for e in entries:
        if e["status"] == "auto":
            score[e["scenarios"][0]] = score.get(e["scenarios"][0], 0) + 1
            for s in e["scenarios"][1:]:
                score.setdefault(s, 0)
    if not any(score.values()):
        return {"scenario": None, "targets": [], "entries": entries, "colors": {}}
    scenario = min(score, key=lambda s: (-score[s], SCENARIO_ORDER.index(s)))
    targets = [e["name"] for e in entries if e["status"] == "auto" and scenario in e["scenarios"]]
    colors = {}
    if scenario == "GRAD":
        cost: dict[str, int] = {}
        for name in targets:
            for color, n in (source_for(name, sources).hirameki or {}).items():
                cost[color] = cost.get(color, 0) + n
        colors = support_colors(cost)
    return {"scenario": scenario, "targets": targets, "entries": entries, "colors": colors}
