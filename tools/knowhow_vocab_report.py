"""Snap the row names of a ノウハウ scan record onto the known vocabulary (agent/core/knowhow_vocab.py).

Offline and read-only on the record: writes ``dev_assets/private/knowhow-scan/vocab-<record stem>.json`` with every
book's rows as {reading, name, status, reason} and a review list (rows that are not exact or snapped), then prints a
summary. The effect table is learned from the record's own exact rows.

    python tools/knowhow_vocab_report.py [record.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core import knowhow_vocab as kv  # noqa: E402

SCAN_DIR = ROOT / "dev_assets" / "private" / "knowhow-scan"


def latest_record() -> Path | None:
    records = sorted(SCAN_DIR.glob("knowhow-scan-*.json"), key=lambda p: p.stat().st_mtime)
    records = [p for p in records if not p.name.startswith("vocab-") and "superseded" not in p.name]
    return records[-1] if records else None


def report(record: dict, vocabulary: kv.Vocabulary | None = None) -> dict:
    books = record.get("books") or []
    voc = vocabulary or kv.Vocabulary.load(table=kv.learn_table(books))
    status: Counter = Counter()
    out_books, review = [], []
    for book in books:
        rows = []
        for row in book.get("rows") or ():
            s = voc.snap(row.get("name", ""), row.get("effect"))
            status[s.status] += 1
            rows.append({"reading": s.reading, "name": s.name, "status": s.status, "reason": s.reason,
                         "level": row.get("level")})
            if not s.trusted:
                review.append({"grid_index": book.get("grid_index"), "title": book.get("title"), "level": row.get("level"),
                               "reading": s.reading, "reason": s.reason, "effect": row.get("effect")})
        out_books.append({"grid_index": book.get("grid_index"), "title": book.get("title"), "rows": rows})
    return {"version": 1, "books": out_books, "status": dict(status), "review": review}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("record", nargs="?", type=Path)
    args = parser.parse_args(argv)
    path = args.record or latest_record()
    if path is None or not path.exists():
        print("没有扫描记录", file=sys.stderr)
        return 1
    result = report(json.loads(path.read_text(encoding="utf-8")))
    out = SCAN_DIR / f"vocab-{path.stem}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(result["status"].values()) or 1
    trusted = result["status"].get("exact", 0) + result["status"].get("snapped", 0)
    print(f"{path.name}: {len(result['books'])} 本, {total} 行；可信 {trusted} ({trusted / total:.1%})，"
          f"待确认 {len(result['review'])}；{result['status']}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
