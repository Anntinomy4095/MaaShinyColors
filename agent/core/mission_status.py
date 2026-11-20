"""Pure parser for the ミッション デイリー page (read-only MissionStatus task).

Input is the OCR result list of two Pipeline nodes on one fresh frame that already passed
``SC.Nav.Mission.Guard`` (title + selected デイリー tab + undimmed back key):

- ``SC.MissionStatus.ListOCR``   roi [255, 225, 975, 370]: the visible rows (title, 達成度 a/b, button)
- ``SC.MissionStatus.CounterOCR`` roi [950, 572, 320, 32]: 「ミッション達成数 a/b」

Measured on 1280x720 native frames (2026-10-01/02): title text starts at x≈390; the progress
"a/b" sits at x≈600–645, 45–65 px below the title; the row button (「受け取る」 / 「詳細」) is at
x≥1040 within the row. Rows clipped by the list edge are reported as ``unknown``; the page is not
scrolled (scrolling is input). No Maa import, so this stays unit-testable.

2026-10-07 calibration: an already-received daily mission shows a red 「CLEAR!!」 stamp in the button column
(native ``in_app_browser/20260930-session01/replay/007-mission-list.png``: three rows 1/1 with CLEAR!!, counter 5/5,
一括受取 dimmed). OCR reads it as 「CLEAR!!」 at x≈1082, 24 px above the title line; such a row is ``claimed``.
"""
from __future__ import annotations

import re

PROGRESS = re.compile(r"^(\d{1,4})\s*/\s*(\d{1,4})$")
TITLE_X = (370, 430)
PROGRESS_X = (560, 700)
BUTTON_X = 1030
PROGRESS_DY = (35, 80)
BUTTON_DY = (-35, 85)
CLAIM_TEXT = "受け取る"
DETAIL_TEXT = "詳細"
CLAIMED = re.compile(r"^CLEAR!*$")
LABELS = {"達成度", "期限"}


def _box(item):
    box = item.get("box") if isinstance(item, dict) else None
    if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    return [int(v) for v in box]


def _items(results):
    out = []
    for item in results or []:
        box = _box(item)
        text = item.get("text") if isinstance(item, dict) else None
        if box is None or not isinstance(text, str) or not text.strip():
            continue
        out.append({"text": text.strip(), "box": box, "score": float(item.get("score", 0.0))})
    return out


def parse_counter(results):
    """Return (done, total) from 「ミッション達成数 a/b」 or None."""
    candidates = [PROGRESS.match(item["text"]) for item in _items(results) if PROGRESS.match(item["text"])]
    if len(candidates) == 1:
        match = candidates[0]
        if int(match.group(2)) > 0 and int(match.group(1)) <= int(match.group(2)):
            return int(match.group(1)), int(match.group(2))
    return None


def parse_rows(results):
    """Group the visible list OCR into rows ordered top to bottom."""
    items = _items(results)
    titles = [i for i in items if TITLE_X[0] <= i["box"][0] <= TITLE_X[1] and i["text"] not in LABELS
              and not PROGRESS.match(i["text"])]
    titles.sort(key=lambda i: i["box"][1])
    rows = []
    for title in titles:
        ty = title["box"][1]
        progress = [i for i in items if PROGRESS_X[0] <= i["box"][0] <= PROGRESS_X[1] and PROGRESS.match(i["text"])
                    and PROGRESS_DY[0] <= i["box"][1] - ty <= PROGRESS_DY[1]]
        buttons = [i for i in items if i["box"][0] >= BUTTON_X and BUTTON_DY[0] <= i["box"][1] - ty <= BUTTON_DY[1]
                   and (i["text"] in (CLAIM_TEXT, DETAIL_TEXT) or CLAIMED.match(i["text"]))]
        done = total = None
        if len(progress) == 1:
            match = PROGRESS.match(progress[0]["text"])
            done, total = int(match.group(1)), int(match.group(2))
        button = buttons[0]["text"] if len(buttons) == 1 else None
        if button is not None and CLAIMED.match(button):
            button = "CLEAR!!"
        rows.append({"title": title["text"], "progress": None if total is None else [done, total],
                     "button": button, "status": "unknown" if len(buttons) > 1 else classify(done, total, button),
                     "title_y": ty})
    return rows


def classify(done, total, button):
    """claimable: completed and the 受け取る button is shown; claimed: completed with the 「CLEAR!!」 stamp (reward
    already received); completed: a=b without any button read; incomplete: a<b; unknown: progress not readable or
    contradictory."""
    if button == "CLEAR!!":
        return "claimed" if total is not None and total > 0 and done == total else "unknown"
    if total is None or total <= 0 or done is None or done < 0 or done > total:
        return "unknown"
    if done == total:
        return "claimable" if button == CLAIM_TEXT else ("unknown" if button == DETAIL_TEXT else "completed")
    return "unknown" if button == CLAIM_TEXT else "incomplete"


def build_report(list_results, counter_results):
    rows = parse_rows(list_results)
    counter = parse_counter(counter_results)
    complete = [r["title"] for r in rows if r["status"] in ("claimable", "completed", "claimed")]
    return {
        "page": "mission_daily",
        "counter": None if counter is None else list(counter),
        "rows": rows,
        "complete": complete,
        "claimed": [r["title"] for r in rows if r["status"] == "claimed"],
        "claimable": [r["title"] for r in rows if r["status"] == "claimable"],
        "incomplete": [r["title"] for r in rows if r["status"] == "incomplete"],
        "unknown": [r["title"] for r in rows if r["status"] == "unknown"],
        "visible_rows_only": True,
        "ok": any(r["status"] != "unknown" for r in rows) and counter is not None,
    }
