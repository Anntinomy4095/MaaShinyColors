"""Read-only item / resource-counter recognition (「识别持有」道具). Offline only.

Recognises what the user owns from saved canonical 1280x720 frames; no input, no device, no network. Two layouts were
measured on the user's own captures (WING demo #233/#239 and demo2 #442–#454, home #1/#984…):

* ``produce_item_select`` — プロデュースアイテム準備 / アイテム選択 grid (5 columns x 122.5 px, rows 118 px apart, tiles
  108x112 with a dark count band ``x1874`` 87 px below the tile top). The grid scrolls, so rows are found from the count
  bands, and only rows fully inside the list viewport are read. Each tile = icon template match + count OCR.
* ``home_counters`` — fixed OCR counters on the home screen: マニー, ジュエル (indicator only, never spent), プロデュース
  stamina ``300/300`` and フェス participation rights ``20/20+0``.

The in-game アイテム一覧 (main menu, account items such as training / skip tickets) has **no sample yet**; it is not a
layout here. Account-item references (pinned enza atlas tiles) can still be loaded into ``ItemIndex`` so the same matcher
works once that screen is sampled; until then their real-screen accuracy is unmeasured.

Rules (same spirit as ``card_inventory``): every tile must pass coarse score, a margin against the best *other* item,
a finer colour check, a structure (gradient) check and a strict count regex. Anything below a threshold is ``rejected``
with a reason; a wrong screen, a loading overlay or a non-1280x720 frame refuses the whole frame. Nothing is guessed,
and a missing value is absent (never 0). Results are suggestions for display; they never authorise anything.

OCR is pluggable: the caller passes ``texts`` = ``[(text, score_or_None, (x, y, w, h)), ...]`` for the whole frame
(``tools/item_inventory_scan.py`` runs MaaFramework OCR on each ROI, read-only, or reuses a saved whole-frame dump).
"""
from __future__ import annotations

import dataclasses
import re
from typing import Iterable, Mapping, Sequence

CANVAS = (1280, 720)
COARSE = 16
FINE = 40
EDGE = 32
# Fractions of the 108x112 tile: icon area (avoids the favourite star, the check mark of a selected tile, the count band).
ICON_CROP = (0.10, 0.20, 0.90, 0.76)
FINE_CROP = (0.08, 0.17, 0.92, 0.77)

DEFAULT_THRESHOLDS = {
    "min_score": 0.80,        # coarse 16x16 NCC with the best reference
    "min_margin": 0.04,       # fine score of the winner minus the best *different* item
    "min_verify": 0.80,       # fine 40x40 colour NCC of the winner
    "min_edge": 0.45,         # 32x32 gradient-magnitude NCC of the winner
    "min_ocr": 0.60,          # OCR score of the count (only when the backend reports a score)
    "min_confidence": 0.80,   # final per-item confidence
}

COUNT_RE = re.compile(r"^[xX×]\s*(\d{1,6})$")
MONEY_RE = re.compile(r"^\d{1,3}(?:,\d{3}){0,4}$")
PAIR_RE = re.compile(r"^(\d{1,3})/(\d{1,3})$")
FES_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:\+(\d{1,3}))?$")
LOADING_RE = re.compile(r"Now\s*Load|Loading", re.I)

LAYOUTS = {
    "produce_item_select": {
        "kind": "grid", "x0": 626.0, "dx": 122.5, "cols": 5, "tile": (108, 112), "dy": 118,
        "band_offset": 87, "band_h": 22, "viewport": (196, 566),
        "anchors": {"title": ((30, 10, 360, 56), r"プロデュースアイテム準備"), "tab": ((930, 8, 250, 56), r"アイテム選択")},
        "filter_roi": (628, 128, 120, 50),
        # Any of these anywhere means a dialog is over the list (e.g. 使用確認 before プロデュース開始): refuse the frame.
        "blockers": (r"使用確認", r"キャンセル", r"閉じる", r"プロデュース開始"),
        "source": "WING demo #233/#239, demo2 #442–#454 (アイテム選択, 2026-10-05/06)",
    },
    "home_counters": {
        "kind": "counters",
        "anchors": {"produce": ((985, 628, 150, 52), r"プロデュース"), "fes": ((1128, 628, 120, 52), r"フ[ェエ]ス"),
                    "business": ((760, 655, 90, 45), r"営業")},
        "blockers": (r"キャンセル", r"閉じる", r"^OK$", r"確認$"),
        "counters": {
            "money": {"roi": (330, 8, 145, 40), "kind": "money"},
            "jewel": {"roi": (478, 8, 130, 40), "kind": "money", "indicator_only": True},
            "stamina": {"roi": (988, 660, 125, 34), "kind": "pair"},
            "fes": {"roi": (1128, 660, 115, 34), "kind": "fes"},
        },
        "source": "home frames (WING demo2 #1, demo #984–#988)",
    },
}

# Item ids for the Produce items seen so far. ``name`` only when the game itself showed it (left info panel of the
# selected tile, or the in-Produce アイテム popup); appearance descriptions are not names.
# 2026-10-08d: label shown above the tile in アイテム一覧 → プロデュースアイテム (tile matched to the pinned enza tile).
_LIST_EVIDENCE = "アイテム一覧 tile label (2026-10-08d)"
PRODUCE_ITEMS = {
    "p_tart_yellow_3": {"name": "ヒーリングタルト(3個入り)", "evidence": "demo2 #2458 アイテム popup", "look": "黄盒 3 个塔"},
    "p_tart_blue_2": {"name": "ヒーリングフルーツタルト(2個入り)", "evidence": _LIST_EVIDENCE, "look": "蓝条纹盒 2 个草莓塔"},
    "p_tart_blue_3": {"name": "ヒーリングフルーツタルト(3個入り)", "evidence": _LIST_EVIDENCE, "look": "蓝条纹盒 3 个草莓塔"},
    "p_tart_black_2": {"name": "高級ヒーリングフルーツタルト(2個入り)", "evidence": _LIST_EVIDENCE, "look": "黑盒 2 个水果塔"},
    "p_tart_black_3": {"name": "高級ヒーリングフルーツタルト(3個入り)", "evidence": _LIST_EVIDENCE, "look": "黑盒 3 个水果塔"},
    "p_candle_1": {"name": "プチテンションアロマ", "evidence": _LIST_EVIDENCE, "look": "蓝色蜡烛 ×1"},
    "p_candle_2": {"name": "プチテンションアロマ(2個入り)", "evidence": _LIST_EVIDENCE, "look": "蓝色蜡烛 ×2"},
    "p_candle_3": {"name": "プチテンションアロマ(3個入り)", "evidence": _LIST_EVIDENCE, "look": "蓝色蜡烛 ×3"},
    "p_magazine_1": {"name": "流行雑誌", "evidence": _LIST_EVIDENCE, "look": "「アイグラ」杂志 ×1"},
    "p_magazine_2": {"name": "流行雑誌(2冊入り)", "evidence": _LIST_EVIDENCE, "look": "「アイグラ」杂志 ×2"},
    "p_magazine_vocal": {"name": "Vocal流行雑誌", "evidence": _LIST_EVIDENCE, "look": "Vocal 杂志"},
    "p_magazine_dance": {"name": "Dance流行雑誌", "evidence": _LIST_EVIDENCE, "look": "Dance 杂志"},
    "p_magazine_visual": {"name": "Visual流行雑誌", "evidence": _LIST_EVIDENCE, "look": "Visual 杂志"},
    "p_secret_memo": {"name": "秘密のメモ帳", "evidence": "アイテム選択 left panel (selected tile)", "look": "蓝色手帐"},
    "p_book_magnifier": {"name": "ノウハウ目録", "evidence": _LIST_EVIDENCE, "look": "书 + 放大镜"},
    "p_bookmark_orange": {"name": "ノウハウのしおり(1葉)", "evidence": _LIST_EVIDENCE, "look": "橙色书签"},
    "p_bookmark_rainbow": {"name": "高レベルノウハウのしおり(1葉)", "evidence": _LIST_EVIDENCE, "look": "彩虹书签"},
    "p_charm_pouch": {"name": "親愛のお守り", "evidence": _LIST_EVIDENCE, "look": "红色小袋挂饰"},
}
# Counters / account items the UI lists even when never observed (shown as 未观测, never 0).
ACCOUNT_ITEMS = ("money", "stamina", "fes", "bronze_ticket", "silver_ticket", "gold_ticket", "platinum_ticket",
                 "lv50_ticket", "skip_ticket", "jewel")


@dataclasses.dataclass(frozen=True)
class TileRead:
    slot: str
    box: tuple
    status: str                  # accepted | rejected | empty
    item_id: str | None
    count: int | None
    confidence: float
    score: float
    margin: float
    verify: float
    edge: float
    ocr_score: float | None
    count_text: str | None
    reason: str
    runner_up: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"box": list(self.box)}


# --------------------------------------------------------------------------------------------------------------- vectors
def _normalise(array):
    import numpy as np

    flat = array.reshape(array.shape[0], -1).astype(np.float32)
    flat -= flat.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(flat, axis=1, keepdims=True)
    norm[norm < 1e-6] = 1e-6
    return flat / norm


def _crop(image, frac):
    w, h = image.size
    return image.crop((round(w * frac[0]), round(h * frac[1]), round(w * frac[2]), round(h * frac[3])))


def _vector(image, frac, size):
    import numpy as np
    from PIL import Image

    return np.asarray(_crop(image.convert("RGB"), frac).resize((size, size), Image.BILINEAR), dtype=np.float32)


def _edges(image, frac, size=EDGE):
    import numpy as np
    from PIL import Image

    gray = np.asarray(_crop(image.convert("L"), frac).resize((size + 1, size + 1), Image.BILINEAR), dtype=np.float32)
    gx = np.diff(gray, axis=1)[:-1]
    gy = np.diff(gray, axis=0)[:, :-1]
    return np.sqrt(gx * gx + gy * gy)


def atlas_tile_as_grid_tile(tile, size=(108, 112)):
    """An enza 148x148 item tile (atlas) laid out like an in-game 108x112 grid tile (art scaled to 108x108 at the top).
    Used for account-item references and synthetic tests; the real アイテム一覧 geometry is not sampled yet."""
    from PIL import Image

    canvas = Image.new("RGB", size, (255, 255, 255))
    art = tile.convert("RGBA").resize((size[0], size[0]), Image.LANCZOS)
    canvas.paste(art, (0, 0), art)
    return canvas


def is_empty_tile(tile) -> bool:
    import numpy as np

    data = np.asarray(_crop(tile.convert("L"), ICON_CROP), dtype=np.float32)
    return float(data.std()) < 10.0 and float(data.mean()) > 215.0


class ItemIndex:
    """Reference icons as 108x112 grid tiles: ``{item_id: PIL image}``. ``meta`` adds labels (name/look/source)."""

    def __init__(self, refs: Mapping[str, object], meta: Mapping[str, dict] | None = None):
        import numpy as np

        if not refs:
            raise ValueError("没有道具参考图：先用 tools/item_inventory_scan.py --build-templates 生成")
        self.ids = list(refs)
        self.meta = {k: dict(v) for k, v in (meta or {}).items()}
        images = [refs[i].convert("RGB") for i in self.ids]
        self.coarse = _normalise(np.stack([_vector(r, ICON_CROP, COARSE) for r in images]))
        self.fine = _normalise(np.stack([_vector(r, FINE_CROP, FINE) for r in images]))
        self.edge = _normalise(np.stack([_edges(r, FINE_CROP) for r in images]))

    def match(self, tile):
        """(best id, coarse, margin, verify, edge, runner-up id) — margin is on the fine vector vs a different item."""
        import numpy as np

        coarse = (_normalise(_vector(tile, ICON_CROP, COARSE)[None]) @ self.coarse.T)[0]
        fine = (_normalise(_vector(tile, FINE_CROP, FINE)[None]) @ self.fine.T)[0]
        # Rank by the sum (coarse picks the family, fine separates 2- vs 3-piece variants).
        combined = coarse + fine
        order = np.argsort(combined)[::-1]
        best = int(order[0])
        runner = int(order[1]) if len(order) > 1 else None
        margin = float(fine[best] - fine[runner]) if runner is not None else 1.0
        edge = float(_normalise(_edges(tile, FINE_CROP)[None])[0] @ self.edge[best])
        return (self.ids[best], float(coarse[best]), margin, float(fine[best]), edge,
                self.ids[runner] if runner is not None else None)


# --------------------------------------------------------------------------------------------------------------- OCR helpers
def _centre_in(box, roi) -> bool:
    x, y, w, h = box
    rx, ry, rw, rh = roi
    cx, cy = x + w / 2, y + h / 2
    return rx <= cx < rx + rw and ry <= cy < ry + rh


def normalise_texts(texts: Iterable) -> list[tuple]:
    """Accept ``(text, box)`` or ``(text, score, box)`` rows -> ``[(text, score|None, (x, y, w, h))]``."""
    out = []
    for row in texts or ():
        if len(row) == 2:
            text, box = row
            score = None
        else:
            text, score, box = row[0], row[1], row[2]
        if isinstance(text, str) and isinstance(box, (list, tuple)) and len(box) == 4:
            out.append((text, None if score is None else float(score), tuple(int(v) for v in box)))
    return out


def texts_in(texts, roi) -> list[tuple]:
    return [t for t in texts if t[0].strip() and _centre_in(t[2], roi)]


def anchor_hits(texts, anchors: Mapping[str, tuple]) -> dict:
    return {name: any(re.search(pattern, t[0]) for t in texts_in(texts, roi)) for name, (roi, pattern) in anchors.items()}


def parse_count(text: str | None) -> int | None:
    m = COUNT_RE.match(re.sub(r"\s+", "", text or ""))
    return int(m.group(1)) if m else None


def parse_money(text: str | None) -> int | None:
    t = re.sub(r"\s+", "", text or "")
    return int(t.replace(",", "")) if MONEY_RE.match(t) else None


def parse_pair(text: str | None):
    m = PAIR_RE.match(re.sub(r"\s+", "", text or ""))
    if not m:
        return None
    value, cap = int(m.group(1)), int(m.group(2))
    return (value, cap) if 0 < cap and value <= max(cap * 4, cap) else None


def parse_fes(text: str | None):
    m = FES_RE.match(re.sub(r"\s+", "", text or ""))
    if not m:
        return None
    value, cap = int(m.group(1)), int(m.group(2))
    if cap <= 0 or value > cap:
        return None
    return value, cap, (int(m.group(3)) if m.group(3) is not None else None)


# --------------------------------------------------------------------------------------------------------------- grid
def find_band_rows(frame, layout) -> list[int]:
    """Tile tops (y) of rows whose dark count band (22 px run) is found in at least two columns, fully inside the
    viewport. Each column votes on its own (a dark icon above the band only spoils that column's run)."""
    import numpy as np

    data = np.asarray(frame.convert("RGB"), dtype=np.int16)
    top, bottom = layout["viewport"]
    tile_w, tile_h = layout["tile"]
    dark = data.max(axis=2) < 125
    votes: dict = {}
    for c in range(layout["cols"]):
        x = round(layout["x0"] + layout["dx"] * c)
        # Left part of the band (right of its slanted end, left of the right-aligned digits).
        on = dark[:, x + 20: x + 38].mean(axis=1) > 0.8
        run_start = None
        for y in range(CANVAS[1] + 1):
            value = y < CANVAS[1] and bool(on[y])
            if value and run_start is None:
                run_start = y
            elif not value and run_start is not None:
                if abs((y - run_start) - layout["band_h"]) <= 3:
                    tile_top = run_start - layout["band_offset"]
                    key = next((k for k in votes if abs(k - tile_top) <= 2), tile_top)
                    votes[key] = votes.get(key, 0) + 1
                run_start = None
    strong = [y for y, n in votes.items() if n >= 2]
    # A row with one item (the last, partly filled row) has a single vote: keep it only on the lattice of the strong
    # rows (multiples of dy, +-2 px), or when no row has two votes at all (a list with one item per row).
    def on_lattice(y):
        return any(abs((y - s) - round((y - s) / layout["dy"]) * layout["dy"]) <= 2 for s in strong)
    rows = [y for y in votes if y in strong or not strong or on_lattice(y)]
    return sorted(y for y in rows if y >= top and y + tile_h <= bottom)


def _read_tile(frame, index: ItemIndex | None, texts, slot, box, layout, th) -> TileRead:
    x, y, w, h = box
    tile = frame.crop((x, y, x + w, y + h))
    band = (x, y + layout["band_offset"] - 4, w, layout["band_h"] + 8)
    count_rows = [t for t in texts_in(texts, band) if parse_count(t[0]) is not None]
    raw_rows = texts_in(texts, band)
    if is_empty_tile(tile) and not raw_rows:
        return TileRead(slot, box, "empty", None, None, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, "empty")
    if index is None:
        best, score, margin, verify, edge, runner = None, 0.0, 0.0, 0.0, 0.0, None
    else:
        best, score, margin, verify, edge, runner = index.match(tile)
    count = None
    count_text = None
    ocr_score = None
    if len(count_rows) == 1:
        count_text, ocr_score = count_rows[0][0], count_rows[0][1]
        count = parse_count(count_text)
    elif raw_rows:
        count_text = " ".join(t[0] for t in raw_rows)[:40]
    reason = "ok"
    if index is None:
        reason = "no_reference"
    elif score < th["min_score"]:
        reason = "low_score"
    elif verify < th["min_verify"]:
        reason = "verify_failed"
    elif margin < th["min_margin"]:
        reason = "ambiguous_item"
    elif edge < th["min_edge"]:
        reason = "structure_mismatch"
    elif len(count_rows) > 1:
        reason = "count_ambiguous"
    elif count is None:
        reason = "count_unread"
    elif ocr_score is not None and ocr_score < th["min_ocr"]:
        reason = "count_low_ocr"
    confidence = min(score, verify, 1.0) if index is not None else 0.0
    if ocr_score is not None:
        confidence = min(confidence, ocr_score)
    if reason == "ok" and confidence < th["min_confidence"]:
        reason = "low_confidence"
    status = "accepted" if reason == "ok" else "rejected"
    return TileRead(slot, tuple(box), status, best if status == "accepted" else None,
                    count if status == "accepted" else None, round(confidence, 4), round(score, 4), round(margin, 4),
                    round(verify, 4), round(edge, 4), None if ocr_score is None else round(ocr_score, 4), count_text,
                    reason, runner)


def _check_frame(frame):
    if frame.size != CANVAS:
        raise ValueError(f"画面必须是 1280×720，实际 {frame.size}")
    return frame.convert("RGB")


def _refused(layout_name, reason, **extra) -> dict:
    return {"layout": layout_name, "screen_ok": False, "refused": reason, "tiles": [], "counters": {}, "items": {},
            "rejected": 0, **extra}


def recognise(frame, index: ItemIndex | None, layout_name: str, texts: Iterable = (),
              thresholds: Mapping[str, float] | None = None, frame_name: str = "") -> dict:
    """Read one 1280x720 PIL frame. ``texts`` = whole-frame OCR rows. Returns
    ``{layout, screen_ok, refused, favorites_only, rows, tiles, counters, items: {id: {count, confidence, frame}}, rejected}``.
    Raises ValueError for a wrong canvas or an unknown layout."""
    if layout_name not in LAYOUTS:
        raise ValueError(f"未知版式 {layout_name}（アイテム一覧尚无样本）")
    frame = _check_frame(frame)
    layout = LAYOUTS[layout_name]
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    texts = normalise_texts(texts)
    if any(LOADING_RE.search(t[0]) for t in texts):
        return _refused(layout_name, "loading_overlay")
    blocked = [t[0] for t in texts if any(re.search(p, t[0]) for p in layout.get("blockers", ()))]
    if blocked:
        return _refused(layout_name, "dialog_overlay", blockers=blocked[:4])
    hits = anchor_hits(texts, layout["anchors"])
    if not all(hits.values()):
        return _refused(layout_name, "screen_guard_failed", anchors=hits)
    if layout["kind"] == "counters":
        return _read_counters(layout_name, layout, texts, th, frame_name)

    favorites = [t[0].strip().upper() for t in texts_in(texts, layout["filter_roi"])]
    favorites_only = True if "ON" in favorites else False if "OFF" in favorites else None
    rows = find_band_rows(frame, layout)
    tiles = []
    for r, y in enumerate(rows):
        for c in range(layout["cols"]):
            x = round(layout["x0"] + layout["dx"] * c)
            tiles.append(_read_tile(frame, index, texts, f"r{r + 1}c{c + 1}", (x, y, *layout["tile"]), layout, th))
    items = {}
    for tile in tiles:
        if tile.status != "accepted":
            continue
        if tile.item_id in items:   # the same item twice in one frame is impossible -> refuse both
            items[tile.item_id] = None
            continue
        items[tile.item_id] = {"count": tile.count, "confidence": tile.confidence, "frame": frame_name, "slot": tile.slot}
    duplicated = sorted(k for k, v in items.items() if v is None)
    items = {k: v for k, v in items.items() if v is not None}
    return {"layout": layout_name, "screen_ok": True, "refused": None, "favorites_only": favorites_only,
            "rows": rows, "tiles": [t.to_dict() for t in tiles], "counters": {}, "items": items,
            "duplicated": duplicated, "rejected": sum(t.status == "rejected" for t in tiles)}


def _read_counters(layout_name, layout, texts, th, frame_name) -> dict:
    counters = {}
    items = {}
    rejected = 0
    for key, spec in layout["counters"].items():
        rows = texts_in(texts, spec["roi"])
        joined = "".join(t[0] for t in sorted(rows, key=lambda t: t[2][0]))
        scores = [t[1] for t in rows if t[1] is not None]
        ocr_score = min(scores) if scores else None
        value = {"money": parse_money, "pair": parse_pair, "fes": parse_fes}[spec["kind"]](joined)
        entry = {"text": joined[:40] or None, "ocr_score": ocr_score, "status": "rejected", "reason": "ok"}
        if not rows:
            entry["reason"] = "unread"
        elif value is None:
            entry["reason"] = "format_mismatch"
        elif ocr_score is not None and ocr_score < th["min_ocr"]:
            entry["reason"] = "low_ocr"
        if entry["reason"] == "ok":
            entry["status"] = "accepted"
            confidence = 1.0 if ocr_score is None else round(ocr_score, 4)
            item = {"count": value if isinstance(value, int) else value[0], "confidence": confidence, "frame": frame_name}
            if isinstance(value, tuple):
                item["max"] = value[1]
                if len(value) > 2 and value[2] is not None:
                    item["plus"] = value[2]
            if spec.get("indicator_only"):
                item["indicator_only"] = True
            items[key] = item
        else:
            rejected += 1
        counters[key] = entry
    return {"layout": layout_name, "screen_ok": True, "refused": None, "favorites_only": None, "rows": [],
            "tiles": [], "counters": counters, "items": items, "duplicated": [], "rejected": rejected}


def merge_reads(results: Sequence[Mapping], observed: Sequence[str] | None = None) -> dict:
    """Latest accepted value per item over frames in observation order: {item_id: {count, confidence, frame, observed_at,
    frames}}. ``observed`` gives the observation time of each result (same order)."""
    out: dict = {}
    for i, result in enumerate(results):
        when = observed[i] if observed and i < len(observed) else None
        for item_id, value in (result.get("items") or {}).items():
            seen = out.get(item_id, {}).get("frames", 0) + 1
            out[item_id] = {**value, "observed_at": when, "frames": seen}
    return out
