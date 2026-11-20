"""Auto 词条记录: which knowhow entries each official Auto run brought, per scenario.

A started Auto (WeeklyProduce reason ``started``) is logged with its scenario, unit, P card, run count and the
「按目标词条」 targets. When a later daily run collects the finished Auto, its newest-book read (KnowhowScan
``newest: auto``) is logged against the newest start that has no read yet (tools/ui_engine.note_auto_read); ``stats`` sums books and entries per scenario so a target's drop rate can be measured.
Private account data: dev_assets/private/knowhow-auto/log.jsonl (never committed).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agent.core.knowhow_books import WantedItem, clean_wanted, idol_from_title, repair_reading

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "dev_assets" / "private" / "knowhow-auto" / "log.jsonl"


def start_entry(summary: Mapping | None, *, target: Mapping | None = None, team: Mapping | None = None,
                at: str) -> dict | None:
    """The log row of a daily run whose WeeklyProduce pressed プロデュース開始 and saw 「プロデュース中」; else None."""
    for task in (summary or {}).get("tasks") or ():
        if not isinstance(task, Mapping) or task.get("name") != "WeeklyProduce":
            continue
        for report in task.get("reports") or ():
            if isinstance(report, Mapping) and report.get("kind") == "weeklyauto" and report.get("reason") == "started":
                config = report.get("config") or {}
                # 自动编队: the prep's team; 指定ユニット: the P card the Auto unit page showed (育成方針 自动 reads it)
                p_card = (team or {}).get("p_card") or report.get("p_card")
                p_idol = ((team or {}).get("idols") or [None])[0]
                if p_idol is None and p_card:
                    from agent.core.wing_nav import card_idol_name
                    p_idol = card_idol_name(p_card)
                return {"kind": "start", "at": at, "scenario": config.get("expect_scenario") or None,
                        "runs": config.get("runs"), "unit": config.get("unit"), "p_card": p_card, "p_idol": p_idol,
                        "targets": list((target or {}).get("targets") or [])}
    return None


def read_entry(start: Mapping, books: Iterable[Mapping], *, wanted: Iterable[WantedItem | Mapping | str] = (),
               at: str) -> dict:
    """The log row of the books read after ``start``: rows, whether each title's idol is the start's P idol, and
    which wanted entries (any level) the books contain."""
    wanted = clean_wanted([w.to_dict() if isinstance(w, WantedItem) else w for w in wanted])
    out_books, found = [], []
    for book in books:
        rows = [[repair_reading(r.get("name")), r.get("level")] for r in book.get("rows") or () if r.get("name")]
        title = repair_reading(book.get("title")) or None
        idol = idol_from_title(title)
        out_books.append({"title": title, "idol": idol, "rows": rows,
                          "p_match": None if not start.get("p_idol") or not idol else idol == start["p_idol"]})
        for item in wanted:
            if item.name not in found and any(item.matches_name(name) for name, _ in rows):
                found.append(item.name)
    return {"kind": "read", "at": at, "start": start.get("at"), "scenario": start.get("scenario"),
            "books": out_books, "wanted_found": found}


def load(path: Path = LOG_PATH) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") in ("start", "read"):
            out.append(row)
    return out


def append(row: Mapping, path: Path = LOG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pending(rows: Iterable[Mapping]) -> list[dict]:
    """Starts without a read, oldest first."""
    rows = list(rows)
    read = {r.get("start") for r in rows if r.get("kind") == "read"}
    return [dict(r) for r in rows if r.get("kind") == "start" and r.get("at") not in read]


def stats(rows: Iterable[Mapping]) -> dict[str, dict[str, Any]]:
    """{scenario: {"runs", "books", "entries": {name: books containing it}, "wanted": {name: reads that found it}}}."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != "read":
            continue
        s = out.setdefault(row.get("scenario") or "?", {"runs": 0, "books": 0, "entries": {}, "wanted": {}})
        s["runs"] += 1
        for book in row.get("books") or ():
            s["books"] += 1
            for name in {name for name, _ in book.get("rows") or ()}:
                s["entries"][name] = s["entries"].get(name, 0) + 1
        for name in row.get("wanted_found") or ():
            s["wanted"][name] = s["wanted"].get(name, 0) + 1
    return out


__all__ = ["LOG_PATH", "start_entry", "read_entry", "load", "append", "pending", "stats"]
