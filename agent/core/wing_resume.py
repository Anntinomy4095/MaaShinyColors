"""W.I.N.G. 「プロデュース再開」: read the home's resume dialog and compare it with the task config.

After a game reload the home shows 「プロデュース中」 where 「プロデュース」 used to be; tapping it opens the dialog
「プロデュース再開」 (native sample ``dev_assets/private/wing-live-20261009/sample-produce-resume-dialog.png``):

* card line 「【ひとりじゃない夜のこと】風野 灯織」 (y ~181), stats in two columns — left Vo / Vi / SP (x ~648),
  right Da / メンタル / fans (x ~833), rows y ~218 / 256 / 295,
* 育成中のシナリオ 「W.I.N.G.編 ミドル」 and 「シーズン 1 / 残り 4週」,
* 使用中のプロデュースアイテム (「使用中のアイテムはありません」 when none),
* buttons キャンセル (x 381–545) / 終了 (557–722) / 再開 (737–900), y 594–668.

終了 ends the Produce and is never a Maa target (no intent exists for it). Pure functions; no input.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Iterable, Mapping, Sequence

Texts = Iterable[tuple]

# Click rects (x, y, w, h) on the 1280x720 canvas, measured on the native frames: home 「プロデュース中」 button
# x 995–1120 / y 605–690 (label box [997,641,120,28] on restart3.png / after-restart.png); dialog buttons above.
RECTS = {
    "home_produce_resume": (1005, 615, 105, 70),
    "resume_ok": (750, 602, 138, 58),
    "resume_cancel": (400, 602, 130, 58),
}
END_RECT = (557, 594, 165, 74)                  # 終了: never tapped; its label must not sit in any target rect
DIFFICULTY = {"ビギナー": "beginner", "ミドル": "middle", "マスター": "master", "ハード": "master"}
_STAT_ROWS = (231, 268, 308)                    # label/number centre y of the three stat rows
_LEFT, _RIGHT = ("Vo", "Vi", "SP"), ("Da", "Me", "fans")


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("．", ".").replace("／", "/")


def _centre(box) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _inside(box, rect, pad: int = 0) -> bool:
    cx, cy = _centre(box)
    x, y, w, h = rect
    return x - pad <= cx <= x + w + pad and y - pad <= cy <= y + h + pad


def parse_card(texts: Texts) -> dict:
    """「【title】idol」 -> {"title", "idol"}; either may be None when unread."""
    for text, _ in texts:
        t = _clean(text)
        m = re.search(r"[【\[](.+?)[】\]](.+)$", t)
        if m and len(m.group(2)) >= 2:
            return {"title": m.group(1), "idol": m.group(2)}
    return {"title": None, "idol": None}


def parse_scenario(texts: Texts) -> dict:
    scenario = difficulty = season = weeks = None
    for text, _ in texts:
        t = _clean(text)
        if re.search(r"W\.?I\.?N\.?G", t, re.I):
            scenario = "wing"
            difficulty = next((v for k, v in DIFFICULTY.items() if k in t), difficulty)
        elif re.search(r"Halo", t, re.I):
            scenario = "halo"                      # say "Halo"編 (H2d 2026-11-06): no difficulty choice
        elif "編" in t and scenario is None:
            scenario = "other:" + t
        m = re.search(r"シ.?ズン(\d)/?残り(\d{1,2})週", t)
        if m:
            season, weeks = int(m.group(1)), int(m.group(2))
    return {"scenario": scenario, "difficulty": difficulty, "season": season, "weeks_left": weeks}


def parse_stats(texts: Texts) -> dict | None:
    """Vo/Da/Vi/Me/SP/fans by position; None unless all six numbers are read exactly once."""
    out: dict = {}
    for text, box in texts:
        t = _clean(text).replace(",", "")
        if not re.fullmatch(r"\d{1,6}", t):
            continue
        cx, cy = _centre(box)
        row = min(range(3), key=lambda i: abs(_STAT_ROWS[i] - cy))
        if abs(_STAT_ROWS[row] - cy) > 14:
            continue
        names = _LEFT if 600 <= cx <= 720 else _RIGHT if 790 <= cx <= 900 else None
        if names is None:
            continue
        if names[row] in out:
            return None
        out[names[row]] = int(t)
    return out if len(out) == 6 else None


def parse_items(texts: Texts) -> list | None:
    """[] when 「使用中のアイテムはありません」; item lines below the header otherwise; None when unread."""
    lines = [(_clean(t), b) for t, b in texts if 470 <= _centre(b)[1] <= 570]
    if any("ありません" in t for t, _ in lines):
        return []
    names = [t for t, _ in lines if len(t) >= 2 and "使用中" not in t]
    return names or None


def parse_resume_dialog(panel: Texts, buttons: Texts) -> dict:
    panel, buttons = list(panel), list(buttons)
    out = {**parse_card(panel), **parse_scenario(panel), "stats": parse_stats(panel), "items": parse_items(panel)}
    out["ok_label"] = any(re.fullmatch(r"再開", _clean(t)) and _inside(b, RECTS["resume_ok"], 6) for t, b in buttons)
    out["cancel_label"] = any(re.search(r"キャンセル", _clean(t)) and _inside(b, RECTS["resume_cancel"], 6)
                              for t, b in buttons)
    # 終了 must be read in its own place and never inside a target rect (layout shift -> stop).
    end = [b for t, b in buttons if re.fullmatch(r"終了", _clean(t))]
    out["end_in_target"] = any(_inside(b, r) for b in end for r in (RECTS["resume_ok"], RECTS["resume_cancel"]))
    out["end_label"] = any(_inside(b, END_RECT, 6) for b in end)
    return out


def _same_name(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None
    a, b = _clean(a), _clean(b)
    return a == b or (len(a) >= 2 and b.endswith(a)) or (len(b) >= 2 and a.endswith(b))


def _same_title(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None
    a, b = _clean(a), _clean(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def compare(dialog: Mapping[str, Any], *, p_idol: str | None, p_title: str | None,
            difficulty: str = "middle", goal: str | None = None, scenario: str = "wing") -> dict:
    """Dialog vs task config. ``verdict``: mismatch (any check False) / unverified (P idol unknown) / match.
    The goal (True End / fans) is not shown on the dialog: recorded as ``not_shown``, never a match. say "Halo"
    (``scenario="halo"``) has no difficulty: that check is left out."""
    checks = {
        "idol": _same_name(dialog.get("idol"), p_idol),
        "card": _same_title(dialog.get("title"), p_title),
        "scenario": None if dialog.get("scenario") is None else dialog.get("scenario") == scenario,
        "difficulty": None if dialog.get("difficulty") is None else dialog.get("difficulty") == difficulty,
    }
    if scenario == "halo":
        checks["difficulty"] = True if dialog.get("difficulty") is None else False
    if any(v is False for v in checks.values()):
        verdict = "mismatch"
    elif checks["idol"] is None or checks["scenario"] is None or checks["difficulty"] is None:
        verdict = "unverified"
    else:
        verdict = "match"
    return {"verdict": verdict, "checks": checks, "seen_idol": dialog.get("idol"), "seen_card": dialog.get("title"),
            "config_idol": p_idol, "config_card": p_title, "difficulty": dialog.get("difficulty"),
            "config_difficulty": difficulty, "goal": goal, "goal_check": "not_shown",
            "season": dialog.get("season"), "weeks_left": dialog.get("weeks_left"), "stats": dialog.get("stats"),
            "items": dialog.get("items")}


def label_at(texts: Texts, pattern: str, rect: Sequence[int], pad: int = 0) -> bool:
    return any(re.search(pattern, _clean(t)) and _inside(b, rect, pad) for t, b in texts)
