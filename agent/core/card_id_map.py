"""Exact mapping from shinycolors.wiki 10-digit CardIcon ids to True End card ids (wikiwiki page titles).

The UI and 持有卡牌 use the 10-digit ids from the shinycolors.wiki card lists; ``agent/core/true_end`` keys cards by
the wikiwiki page title ``【<title>】<idol>``. This module builds a reviewed mapping offline from local files only:

* the local wiki lists (``dev_assets/private/game-assets-src/wiki-{support,produce}/cards.json``: ``card_id``,
  ``title``, ``idol``), and
* the True End card databases already parsed locally (``CardData`` dicts with ``card_id`` / ``name`` / ``idol`` /
  ``kind``).

A list entry is mapped only when ``【title】idol`` equals a parsed card id character for character, the idol and the
kind (support -> S, produce -> P) agree, and the title is unique on both sides. No Unicode normalisation, no fuzzy
match, no rarity or name guessing: ambiguous or unmatched entries stay unmapped. Every list entry also keeps its
``candidate_title`` (the exact wikiwiki title to parse next); a candidate is **not** a mapping. The mapping file is
derived from wiki data and lives under ``dev_assets/private`` (regenerate with ``tools/card_id_map_build.py``).
No network, no game input.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets" / "private"
DEFAULT_MAP = PRIVATE / "true_end" / "card_id_map.json"
DEFAULT_LISTS = (PRIVATE / "game-assets-src" / "wiki-support" / "cards.json",
                 PRIVATE / "game-assets-src" / "wiki-produce" / "cards.json")
WIKI_ID = re.compile(r"^[12]\d{9}$")
MAP_VERSION = 1
LIST_KINDS = {"support": "S", "produce": "P"}


def candidate_title(title: str, idol: str) -> str:
    """The wikiwiki page title for a card: 【title】idol, exactly as written in the list (no normalisation)."""
    return f"【{title}】{idol}"


def _digest(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def read_list(path: Path) -> tuple[str, list[dict]]:
    """(kind "P"/"S", rows) from a local shinycolors.wiki list export."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    kind = LIST_KINDS.get(data.get("kind")) if isinstance(data, dict) else None
    if kind is None or not isinstance(data.get("cards"), list):
        raise ValueError(f"{path}: 不是 wiki-support / wiki-produce 卡牌列表")
    return kind, data["cards"]


def read_database_cards(path: Path) -> list[dict]:
    """Card dicts of a parsed True End database ({"cards": [...]}); entries without card_id/kind/idol are skipped."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        raise ValueError(f"{path}: 缺少 cards 数组")
    return [c for c in cards if isinstance(c, dict) and all(isinstance(c.get(k), str) and c[k] for k in ("card_id", "kind", "idol"))]


def build_map(lists: Iterable[tuple[str, list[Mapping[str, Any]]]], parsed: Iterable[Mapping[str, Any]], *,
              sources: list[dict] | None = None) -> dict:
    """Mapping document from list rows and parsed card dicts. Exact-only; see the module docstring."""
    rows: dict[str, dict] = {}
    by_title: dict[str, list[str]] = {}
    problems: dict[str, str] = {}
    for kind, entries in lists:
        for row in entries:
            wiki_id, title, idol = row.get("card_id"), row.get("title"), row.get("idol")
            if not (isinstance(wiki_id, str) and WIKI_ID.match(wiki_id)) or (wiki_id[0] == "1") != (kind == "P"):
                continue
            if wiki_id in rows:
                problems[wiki_id] = "duplicate_list_id"
                continue
            if not (isinstance(title, str) and title and isinstance(idol, str) and idol):
                problems[wiki_id] = "list_title_or_idol_missing"
                rows[wiki_id] = {"kind": kind, "idol": idol if isinstance(idol, str) else None, "candidate_title": None}
                continue
            name = candidate_title(title, idol)
            rows[wiki_id] = {"kind": kind, "idol": idol, "candidate_title": name}
            by_title.setdefault(name, []).append(wiki_id)
    targets: dict[str, list[Mapping[str, Any]]] = {}
    for card in parsed:
        bucket = targets.setdefault(card["card_id"], [])
        if not any(c.get("kind") == card.get("kind") and c.get("idol") == card.get("idol") for c in bucket):
            bucket.append(card)
    mapped: dict[str, dict] = {}
    unmapped: dict[str, dict] = {}
    for wiki_id in sorted(rows):
        row = rows[wiki_id]
        name = row["candidate_title"]
        entry = {"kind": row["kind"], "candidate_title": name}
        reason = problems.get(wiki_id)
        if reason is None and len(by_title.get(name, ())) > 1:
            reason = "ambiguous_list_title"
            entry["same_title"] = sorted(i for i in by_title[name] if i != wiki_id)
        if reason is None:
            found = targets.get(name, [])
            if not found:
                reason = "not_parsed"
            elif len(found) > 1:
                reason = "ambiguous_parsed_card"
            elif found[0].get("kind") != row["kind"]:
                reason = "kind_mismatch"
            elif found[0].get("idol") != row["idol"]:
                reason = "idol_mismatch"
        if reason is None:
            mapped[wiki_id] = {"true_end_id": name, "kind": row["kind"], "idol": row["idol"],
                               "match": "exact_title_idol_kind"}
        else:
            unmapped[wiki_id] = {**entry, "reason": reason}
    return {"version": MAP_VERSION, "generated_by": "tools/card_id_map_build.py",
            "rule": "【title】idol 与已解析 True End 卡 ID 逐字相等，偶像与 P/S 一致且双方唯一；不做规范化或模糊匹配",
            "sources": sources or [], "counts": {"mapped": len(mapped), "unmapped": len(unmapped)},
            "mapped": mapped, "unmapped": unmapped}


def build_from_files(list_paths: Iterable[Path] = DEFAULT_LISTS, database_paths: Iterable[Path] = ()) -> dict:
    lists, parsed, sources = [], [], []
    for path in list_paths:
        lists.append(read_list(path))
        sources.append({"kind": "wiki_list", "path": _relative(path), "sha256": _digest(path)})
    for path in database_paths:
        parsed.extend(read_database_cards(path))
        sources.append({"kind": "true_end_database", "path": _relative(path), "sha256": _digest(path)})
    return build_map(lists, parsed, sources=sources)


def _relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


class CardIdMap:
    """Read-only view of a mapping document; ``true_end_id`` returns None for anything not exactly mapped."""

    def __init__(self, document: Mapping[str, Any] | None = None):
        doc = document or {}
        if doc and doc.get("version") != MAP_VERSION:
            raise ValueError("card_id_map version 不受支持")
        self._mapped: dict[str, dict] = {}
        for wiki_id, entry in (doc.get("mapped") or {}).items():
            if not (isinstance(wiki_id, str) and WIKI_ID.match(wiki_id) and isinstance(entry, Mapping)):
                raise ValueError(f"card_id_map: 无效映射 {wiki_id!r}")
            target = entry.get("true_end_id")
            if not isinstance(target, str) or not target or entry.get("match") != "exact_title_idol_kind":
                raise ValueError(f"card_id_map: {wiki_id} 不是精确匹配")
            if entry.get("kind") != ("P" if wiki_id[0] == "1" else "S"):
                raise ValueError(f"card_id_map: {wiki_id} P/S 不一致")
            self._mapped[wiki_id] = dict(entry)
        targets = [e["true_end_id"] for e in self._mapped.values()]
        if len(set(targets)) != len(targets):
            raise ValueError("card_id_map: 同一 True End 卡被映射到多个 ID")
        self._unmapped = {k: dict(v) for k, v in (doc.get("unmapped") or {}).items() if isinstance(v, Mapping)}

    @classmethod
    def load(cls, path: Path = DEFAULT_MAP) -> "CardIdMap":
        if not Path(path).is_file():
            return cls()
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def __len__(self) -> int:
        return len(self._mapped)

    def true_end_id(self, wiki_id: str) -> str | None:
        entry = self._mapped.get(wiki_id)
        return entry["true_end_id"] if entry else None

    def wiki_id(self, true_end_id: str) -> str | None:
        return next((k for k, v in self._mapped.items() if v["true_end_id"] == true_end_id), None)

    def unmapped_reason(self, wiki_id: str) -> str:
        return self._unmapped.get(wiki_id, {}).get("reason", "not_in_map")

    def candidate_title(self, wiki_id: str) -> str | None:
        entry = self._mapped.get(wiki_id) or self._unmapped.get(wiki_id) or {}
        return entry.get("true_end_id") or entry.get("candidate_title")
