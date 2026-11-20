"""Read-only アイテム一覧 reader (main menu → アイテム一覧). Pure: OCR rows in, counts out.

The in-game item list shows a 4-column grid of item cards: name on top (one or two lines), the icon, 「所持数 N」 and a
pink 「使う」 button. The left column holds six tabs (消費アイテム / プロデュースアイテム / トレーニングチケット / 特訓アイテム /
ガシャ関連アイテム / 記念アイテム); the selected tab is drawn with pink text. **This module never taps anything** -- in
particular never 「使う」 -- it only parses a frame that is already on screen.

Calibration status: ONE sample, a 715x402 side-browser JPEG of the 消費アイテム tab
(``dev_assets/private/sidebrowser-knowhow-20261008/item_list_consumables.jpg``) upscaled to 1280x720. Geometry is
therefore provisional (``CALIBRATED = False``) until native 1280x720 frames of every tab and of a scrolled grid exist. Rules:

* screen guard: the title 「アイテム一覧」 (OCR reads 「アイテムー覧」) in the title box and exactly one pink tab;
* a card is read only when its name band AND its 「所持数」 row are both fully visible (cards cut by the grid edge are
  skipped, never guessed); the count must be a clean integer right of 「所持数」 on the same line;
* the count needs OCR >= 0.85; names need >= 0.70 per row and map to item ids only through :data:`ITEM_NAMES` after normalisation (OCR confusions 卜→ト, 一→ー, 害→書);
  an unknown name is reported in ``unmatched`` and never imported;
* any dialog / loading text refuses the whole frame (e.g. the 「使う」 confirmation).
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

CALIBRATED = False
CANVAS = (1280, 720)
TITLE_ROI = (30, 5, 220, 55)
TITLE_RE = re.compile(r"アイテム[一ー-]覧")
GRID = {"x0": 262, "dx": 243.5, "cols": 4, "top": 120, "bottom": 690}
NAME_BAND = (-268, -190)          # name rows relative to the 所持数 row (centre y), measured: -235..-219 (one/two lines)
COUNT_ROW_DY = 16
TABS = {  # (y0, y1) of each left tab box at x 24..182 (pink text = selected)
    "consumable": (100, 150), "produce_item": (175, 240), "training_ticket": (258, 330),
    "special_training": (355, 410), "gacha": (430, 500), "memorial": (527, 580),
}
TAB_LABEL = {"consumable": "消費アイテム", "produce_item": "プロデュースアイテム", "training_ticket": "トレーニングチケット",
             "special_training": "特訓アイテム", "gacha": "ガシャ関連アイテム", "memorial": "記念アイテム"}
TAB_X = (24, 182)
TAB_PINK_MIN = 0.02               # selected tab: 0.043 on the sample, others 0.000
ANCHOR_RE = re.compile(r"持数")
COUNT_RE = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})*$|^[0-9]{1,7}$")
BLOCKERS = re.compile(r"キャンセル|使用しますか|使用する|閉じる|通信中|Loading|NOW LOADING", re.I)
MIN_OCR = 0.85                    # the count
MIN_NAME_OCR = 0.70               # each name row (plus an exact ITEM_NAMES match)

# In-game names (normalised) -> holding ids used by the 资源 page (ui/components/resource-panel.js HOLDING_ITEMS).
ITEM_NAMES = {
    "リカバリーソーダ30": "stamina_drink",
    "リカバリーソーダ60": "stamina_drink_60",
    "リカバリーソーダ100": "stamina_drink_100",
    "フェスエントリーチケット": "fes_ticket",
    "フェスエントリーチケット5": "fes_ticket_5",
    "コンティニューチケット": "continue_ticket",
    "ブロンズトレーニングチケット": "bronze_ticket",
    "シルバートレーニングチケット": "silver_ticket",
    "ゴールドトレーニングチケット": "gold_ticket",
    "プラチナトレーニングチケット": "platinum_ticket",
    "スキップチケット": "skip_ticket",
}
# 2026-10-12 (user): only the items a task uses or the 资源 page shows are tracked. They sit on the first screen of two
# tabs (消費アイテム: ソーダ / フェスチケット; トレーニングチケット), so no scrolling is needed; other tabs (collab /
# 記念 / 特訓 / ガシャ / P items) and other names are ignored, not reported as unmatched.
TRACKED_TABS = frozenset({"consumable", "training_ticket"})
_FIX = str.maketrans({"卜": "ト", "一": "ー", "－": "ー", "害": "書", "　": "", " ": "", "「": "", "」": ""})


def normalise_name(text: str) -> str:
    return (text or "").translate(_FIX)


def _rows(texts: Iterable) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    out = []
    for row in texts or ():
        try:
            text, score, box = row[0], row[1], row[2]
            out.append((str(text), float(score) if score is not None else 0.0, tuple(int(v) for v in list(box)[:4])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _centre(box):
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def selected_tabs(frame) -> list[str]:
    """Tabs whose box holds pink label pixels (PIL RGB image or HxWx3 RGB array)."""
    if hasattr(frame, "convert"):
        frame = frame.convert("RGB")
        pixels = frame.load()
        get = lambda x, y: pixels[x, y]  # noqa: E731
    else:
        get = lambda x, y: tuple(int(v) for v in frame[y, x, :3])  # noqa: E731
    picked = []
    for key, (y0, y1) in TABS.items():
        hit = total = 0
        for y in range(y0, y1, 2):
            for x in range(TAB_X[0], TAB_X[1], 2):
                r, g, b = get(x, y)[:3]
                total += 1
                hit += r > 215 and g < 150 and 120 < b < 230
        if total and hit / total >= TAB_PINK_MIN:
            picked.append(key)
    return picked


def column_of(x: float) -> int | None:
    col = int((x - GRID["x0"]) // GRID["dx"])
    return col if 0 <= col < GRID["cols"] else None


def recognise(frame, texts: Iterable, *, frame_name: str = "", min_ocr: float = MIN_OCR) -> dict:
    """One アイテム一覧 frame -> ``{screen_ok, refused, tab, items: {id: {count, confidence, frame, name}}, unmatched,
    rejected}``. ``texts`` = whole-frame OCR rows ``(text, score, (x, y, w, h))``."""
    shape = getattr(frame, "shape", None)
    size = (shape[1], shape[0]) if shape is not None else getattr(frame, "size", (0, 0))
    if tuple(size) != CANVAS:
        return {"screen_ok": False, "refused": "frame_not_1280x720", "tab": None, "items": {}, "unmatched": [], "rejected": []}
    rows = _rows(texts)
    base = {"screen_ok": False, "tab": None, "items": {}, "unmatched": [], "rejected": [], "calibrated": CALIBRATED}
    if any(BLOCKERS.search(t) for t, _, _ in rows):
        return {**base, "refused": "dialog_overlay"}
    x0, y0, w, h = TITLE_ROI
    if not any(TITLE_RE.search(t) and x0 <= _centre(b)[0] < x0 + w and y0 <= _centre(b)[1] < y0 + h for t, _, b in rows):
        return {**base, "refused": "screen_guard_failed"}
    tabs = selected_tabs(frame)
    if len(tabs) != 1:
        return {**base, "refused": "tab_unknown", "tabs": tabs}
    if tabs[0] not in TRACKED_TABS:
        return {**base, "screen_ok": True, "refused": "tab_not_tracked", "tab": tabs[0], "ignored": 0}
    items, unmatched, rejected = {}, [], []
    for anchor, _, abox in rows:
        if not ANCHOR_RE.search(anchor):
            continue
        ax, ay = _centre(abox)
        col = column_of(ax)
        if col is None or not GRID["top"] <= ay <= GRID["bottom"]:
            continue
        count_rows = [(t, s, b) for t, s, b in rows if COUNT_RE.match(t.strip()) and abs(_centre(b)[1] - ay) <= COUNT_ROW_DY
                      and _centre(b)[0] > ax and column_of(_centre(b)[0]) == col]
        names = sorted(((t, s, b) for t, s, b in rows if column_of(_centre(b)[0]) == col
                        and NAME_BAND[0] <= _centre(b)[1] - ay <= NAME_BAND[1]), key=lambda r: r[2][1])
        name = normalise_name("".join(t for t, _, _ in names))
        slot = f"c{col + 1}y{int(ay)}"
        if len(count_rows) != 1 or not names or ay + NAME_BAND[0] < GRID["top"] - 40:
            rejected.append({"slot": slot, "reason": "count_or_name_unread", "name": name or None})
            continue
        text, score, _ = count_rows[0]
        name_score = min(s for _, s, _ in names)
        # The count must be clean; a name only has to clear MIN_NAME_OCR because it must also match ITEM_NAMES exactly
        # (a two-line name ends in a lone glyph that OCR scores low: 「チケッ」+「卜」 0.78 on the sample).
        if score < min_ocr or name_score < MIN_NAME_OCR:
            rejected.append({"slot": slot, "reason": "low_ocr", "name": name})
            continue
        score = min(score, name_score)
        item_id = ITEM_NAMES.get(name)
        if item_id is None:
            unmatched.append(slot)      # not tracked (whitelist): counted only, never imported or reported by name
            continue
        if item_id in items:
            items[item_id] = None   # one item twice on one frame is impossible -> refuse both
            continue
        items[item_id] = {"count": int(text.replace(",", "")), "confidence": round(score, 3), "frame": frame_name,
                          "slot": slot, "name": name}
    dup = sorted(k for k, v in items.items() if v is None)
    rejected += [{"slot": None, "reason": "duplicate_item", "name": k} for k in dup]
    return {**base, "screen_ok": True, "refused": None, "tab": tabs[0],
            "items": {k: v for k, v in items.items() if v is not None}, "unmatched": [], "ignored": len(unmatched),
            "rejected": [r for r in rejected if r.get("name") is None or normalise_name(r["name"]) in ITEM_NAMES]}


def merge_reads(results: Iterable[Mapping], observed: Iterable[str] | None = None) -> dict:
    """Latest accepted value per item over frames in observation order (same shape as item_inventory.merge_reads)."""
    out: dict = {}
    observed = list(observed or ())
    for i, result in enumerate(results):
        when = observed[i] if i < len(observed) else None
        for item_id, value in (result.get("items") or {}).items():
            seen = out.get(item_id, {}).get("frames", 0) + 1
            out[item_id] = {**value, "observed_at": when, "frames": seen}
    return out
