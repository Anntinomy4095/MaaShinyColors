"""Read-only card inventory recognition (「持有卡牌」自动识别). Offline only.

Matches card thumbnails in a canonical 1280x720 frame against the local reference icons ``ui/game-assets/cards/<id>.png``
(via ``manifest.json``; private and gitignored): since 2026-10-06 the ShinyColorsDB game-extracted icon per card id, with
the Shinycolors Wiki ``CardIcon`` as fallback (``thumb_source`` in the manifest). No input, no device, no network.

Method (per tile):

1. **Coarse**: centre crop of the tile (avoids the in-game rarity frame, P/S badge, stars, ``Lv.80`` bar and gear icon),
   resized to 16x16 RGB, zero-mean / unit-norm -> cosine = normalised cross-correlation against every reference.
2. **Margin**: the best card must beat the best card of a *different idol* by ``min_margin`` (variants of the same idol
   look alike; another idol winning by a hair means we do not know).
3. **Verify**: the winner is re-checked at 40x40 colour on a slightly larger crop (``min_verify``) and on 32x32 gradient
   magnitude (``min_edge``: line structure, which smooth backgrounds and partial tiles lack). Variants of the same idol
   must also be separated (``min_variant_margin``). Only then is it accepted. In list layouts the tile's rarity frame
   (``frame_tier``) first restricts the same-idol variants to matching rarities: some art exists twice (R【白いツバサ】 and
   its SR 93… re-release), and only the frame tells them apart; no fitting variant -> ``rarity_mismatch``.

Thresholds were calibrated on the user's captures: 67 of 69 real tiles accepted, 0 of 360
random 108 px crops from non-list frames accepted.

Anything below a threshold is reported as ``rejected`` with the reason; the module never guesses. Results are suggestions
for a reviewed import in the UI (the user confirms before 持有卡牌 is saved); they never authorise anything.

Layouts are fixed 1280x720 geometry measured on the user's own captures (WING demo #130/#143/#157/#159 アイドル選択 grid,
#163 ユニット編成, and the training list): the selection grids are 7 columns x 138 px, rows 154 px apart, 108 px tiles;
grids scroll, so the row offset is searched (the offset with the best mean coarse score wins) and only rows fully inside
the list viewport are read.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / "ui" / "game-assets"
CANVAS = (1280, 720)
COARSE = 16
FINE = 40
# Fractions of the tile side: coarse centre crop and the wider verification crop.
COARSE_CROP = (0.22, 0.18, 0.86, 0.80)   # left, top, right, bottom
FINE_CROP = (0.14, 0.12, 0.90, 0.84)
REF_INSET = (0.04, 0.04, 0.96, 0.96)     # wiki CardIcon -> art inside its rounded border

DEFAULT_THRESHOLDS = {"min_score": 0.80, "min_margin": 0.06, "min_verify": 0.60, "min_edge": 0.30,
                      "min_variant_margin": 0.02}
# Rarity frame of a list tile (third demo 2026-10-06, home アイドル一覧 P/S tabs):
# R/N silver (low saturation, one hue), SR gold (saturated, one hue), SSR/UR rainbow (hue spread). Measured on 164
# accepted tiles: silver sat ~0.15 / hue-std <= 0.04, gold sat >= 0.58 / hue-std <= 0.03, rainbow hue-std >= 0.14.
# A winner whose rarity disagrees with the frame is a variant missing from the reference set -> rejected.
FRAME_TIERS = {"N": "silver", "R": "silver", "SR": "gold", "SSR": "rainbow", "UR": "rainbow"}
EDGE = 32

# kind: "grid" (scrolling selection list) or "boxes" (fixed slots). Boxes are (x, y, w, h) of the outer tile.
LAYOUTS = {
    # プロデュースアイドル編成 / サポートアイドル編成 selection list and the training list share this grid.
    # The home アイドル一覧 (P / S tabs, third demo) uses the same grid geometry.
    "idol_list": {"kind": "grid", "x0": 267, "dx": 138, "cols": 7, "tile": 108, "dy": 154,
                  "viewport": (170, 680), "card_kind": None, "frame_check": True},
    # ユニット編成: P slot, four supports, guest (demo #163).
    "unit_formation": {"kind": "boxes", "card_kind": None, "boxes": {
        "P": (404, 250, 104, 104), "S1": (538, 250, 104, 104), "S2": (686, 250, 104, 104),
        "S3": (834, 250, 104, 104), "S4": (982, 250, 104, 104), "guest": (1132, 250, 104, 104)}},
    # プロデュース再開 dialog: the P card portrait left of the stats (native sample-produce-resume-dialog.png, 2026-10-14:
    # 【ひとりじゃない夜のこと】 1040020080 accepted at (402,224) with score 0.987 / margin 0.15).
    "resume_dialog": {"kind": "boxes", "card_kind": None, "boxes": {"P": (402, 224, 104, 104)}},
}


def sanitize_owned_cards(source: Mapping | None) -> dict | None:
    """Copy only owned P/S ids and optional integer Lv/LB; mirror UiProfileStore.SanitizeOwnedCards.

    This snapshot is modelling data, never input or spend authority. Missing fields stay unknown.
    """
    if not isinstance(source, Mapping):
        return None
    cards = {}
    for kind, lead in (("p", "1"), ("s", "2")):
        values = source.get(kind)
        if not isinstance(values, list):
            values = []
        cards[kind] = sorted({v for v in values if isinstance(v, str) and len(v) == 10
                              and v.startswith(lead) and v.isascii() and v.isdigit()})[:2000]
    known = set(cards["p"]) | set(cards["s"])
    details = source.get("details")
    if isinstance(details, Mapping):
        kept = {}
        for card, entry in details.items():
            if len(kept) >= 1000 or card not in known or not isinstance(entry, Mapping):
                continue
            item = {key: entry[key] for key, low, high in (("level", 1, 100), ("limit_break", 0, 4))
                    if type(entry.get(key)) is int and low <= entry[key] <= high}
            if item:
                kept[card] = item
        if kept:
            cards["details"] = dict(sorted(kept.items()))
    return cards


@dataclasses.dataclass(frozen=True)
class TileMatch:
    slot: str
    box: tuple
    status: str                 # accepted | rejected
    card_id: str | None
    score: float
    margin: float
    verify: float
    edge: float
    variant_margin: float
    reason: str
    runner_up: str | None = None
    variant: str | None = None  # the closest same-idol card even when rejected (never an identity by itself)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"box": list(self.box)}


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

    rgb = _crop(image.convert("RGB"), frac).resize((size, size), Image.BILINEAR)
    return np.asarray(rgb, dtype=np.float32)


def _edges(image, frac, size=EDGE):
    import numpy as np
    from PIL import Image

    gray = np.asarray(_crop(image.convert("L"), frac).resize((size + 1, size + 1), Image.BILINEAR), dtype=np.float32)
    gx = np.diff(gray, axis=1)[:-1]
    gy = np.diff(gray, axis=0)[:, :-1]
    return np.sqrt(gx * gx + gy * gy)


def idol_of(card_id: str | None) -> str | None:
    return card_id[3:6] if isinstance(card_id, str) and len(card_id) == 10 and card_id.isdigit() else None


class CardIndex:
    """Reference vectors for the local wiki icons. ``kind`` "P" / "S" / None (both)."""

    def __init__(self, ids: Sequence[str], images: Sequence, meta: Mapping[str, dict] | None = None):
        import numpy as np

        if not ids:
            raise ValueError("没有参考卡图：先运行 tools/ui_game_assets_fetch.py 与 tools/ui_game_assets.py")
        self.ids = list(ids)
        self.meta = dict(meta or {})
        refs = [_crop(img.convert("RGB"), REF_INSET) for img in images]
        self.coarse = _normalise(np.stack([_vector(r, COARSE_CROP, COARSE) for r in refs]))
        self.fine = _normalise(np.stack([_vector(r, FINE_CROP, FINE) for r in refs]))
        self.edge = _normalise(np.stack([_edges(r, FINE_CROP) for r in refs]))
        self.idols = [idol_of(i) for i in self.ids]

    @classmethod
    def from_assets(cls, asset_dir: Path = ASSET_DIR, kind: str | None = None) -> "CardIndex":
        from PIL import Image

        manifest = json.loads((Path(asset_dir) / "manifest.json").read_text(encoding="utf-8"))
        cards = [*manifest.get("p_cards", []), *manifest.get("cards", [])]
        ids, images, meta = [], [], {}
        for card in cards:
            card_id = str(card.get("id", ""))
            if not (len(card_id) == 10 and card_id.isdigit()) or (kind and card_id[0] != {"P": "1", "S": "2"}[kind]):
                continue
            path = Path(asset_dir) / "cards" / f"{card_id}.png"
            if not path.is_file() or card_id in meta:
                continue
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
            ids.append(card_id)
            meta[card_id] = {"idol": card.get("idol", ""), "title": card.get("title", ""), "rarity": card.get("rarity", "")}
        return cls(ids, images, meta)

    def coarse_scores(self, tiles):
        """(len(tiles), N) cosine scores for already cropped tile images."""
        import numpy as np

        vectors = _normalise(np.stack([_vector(t, COARSE_CROP, COARSE) for t in tiles]))
        return vectors @ self.coarse.T

    def tier_of(self, index: int) -> str | None:
        return FRAME_TIERS.get(str(self.meta.get(self.ids[index], {}).get("rarity", "")))

    def match(self, tile, slot: str, box: tuple, thresholds: Mapping[str, float] | None = None,
              scores=None, tier: str | None = None) -> TileMatch:
        """``tier`` is the tile's observed rarity frame (``frame_tier``) or None. When known, same-idol variants whose
        rarity contradicts it are dropped before the variant decision: the game reuses identical art across rarities
        (R【白いツバサ】 vs the SR 93… re-release in the ShinyColorsDB set), which only the frame separates."""
        import numpy as np

        th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        row = self.coarse_scores([tile])[0] if scores is None else scores
        best = int(np.argmax(row))
        best_idol = self.idols[best]
        order = np.argsort(row)[::-1][:256]
        others = [i for i in order if self.idols[i] != best_idol]
        runner = int(others[0]) if others else None
        margin = float(row[best] - row[runner]) if runner is not None else 1.0
        # Same-idol variants: decide between them on the finer colour vector.
        fine = _normalise(_vector(tile, FINE_CROP, FINE)[None])[0]
        same = [int(i) for i in order if self.idols[i] == best_idol][:8]
        tier_conflict = False
        if tier is not None:
            fitting = [i for i in same if self.tier_of(i) in (None, tier)]
            tier_conflict = not fitting      # no variant of this idol has the observed frame -> never accepted
            same = fitting or same
        fine_scores = {i: float(fine @ self.fine[i]) for i in same}
        best = max(same, key=fine_scores.get)
        ranked = sorted(fine_scores.values(), reverse=True)
        variant_margin = ranked[0] - ranked[1] if len(ranked) > 1 else 1.0
        verify = fine_scores[best]
        edge = float(_normalise(_edges(tile, FINE_CROP)[None])[0] @ self.edge[best])
        score = float(row[best])
        reason = "ok"
        if score < th["min_score"]:
            reason = "low_score"
        elif margin < th["min_margin"]:
            reason = "ambiguous_other_idol"
        elif verify < th["min_verify"]:
            reason = "verify_failed"
        elif edge < th["min_edge"]:
            reason = "structure_mismatch"
        elif tier_conflict:
            reason = "rarity_mismatch"
        elif variant_margin < th["min_variant_margin"]:
            reason = "ambiguous_variant"
        status = "accepted" if reason == "ok" else "rejected"
        return TileMatch(slot, tuple(box), status, self.ids[best] if status == "accepted" else None, round(score, 4),
                         round(margin, 4), round(verify, 4), round(edge, 4), round(variant_margin, 4), reason,
                         self.ids[runner] if runner is not None else None, self.ids[best])


def frame_tier(frame, box) -> str | None:
    """Rarity frame colour of a list tile: ``silver`` / ``gold`` / ``rainbow`` or None (unclear). Samples the right
    border and the top border right of the P/S badge (the left border carries the rank stars)."""
    import numpy as np

    x, y, w, h = box
    hsv = frame.convert("HSV")
    parts = [np.asarray(hsv.crop((x + w - 5, y + 25, x + w - 1, y + h - 25)), dtype=np.float32).reshape(-1, 3),
             np.asarray(hsv.crop((x + 40, y + 1, x + w - 10, y + 4)), dtype=np.float32).reshape(-1, 3)]
    px = np.concatenate(parts) / 255.0
    if px.size == 0:
        return None
    sat, hue_std = float(px[:, 1].mean()), float(px[:, 0].std())
    if hue_std >= 0.10 and sat >= 0.08:
        return "rainbow"
    if hue_std <= 0.06 and sat >= 0.45:
        return "gold"
    if hue_std <= 0.06 and sat <= 0.25:
        return "silver"
    return None


def _rarity_check(match: "TileMatch", frame, index: "CardIndex") -> "TileMatch":
    """Reject an accepted tile whose matched rarity contradicts the tile's frame colour (unknown -> unchanged)."""
    if match.status != "accepted":
        return match
    want = FRAME_TIERS.get(str(index.meta.get(match.card_id, {}).get("rarity", "")))
    got = frame_tier(frame, match.box)
    if want is None or got is None or want == got:
        return match
    return dataclasses.replace(match, status="rejected", card_id=None, reason="rarity_mismatch")


def _check_frame(frame):
    if frame.size != CANVAS:
        raise ValueError(f"画面必须是 1280×720，实际 {frame.size}")
    return frame.convert("RGB")


def _grid_rows(layout, offset):
    top, bottom = layout["viewport"]
    rows, y = [], offset
    while y + layout["tile"] <= bottom:
        if y >= top:
            rows.append(y)
        y += layout["dy"]
    return rows


def recognise(frame, index: CardIndex, layout_name: str = "idol_list", thresholds: Mapping[str, float] | None = None) -> dict:
    """Recognise every tile of ``layout_name`` in a 1280x720 PIL image. Returns
    {layout, offset, tiles: [TileMatch dict], accepted: [ids], rejected: n}. Raises ValueError for a wrong canvas."""
    import numpy as np

    frame = _check_frame(frame)
    layout = LAYOUTS[layout_name]
    offset = None
    if layout["kind"] == "boxes":
        boxes = list(layout["boxes"].items())
    else:
        top = layout["viewport"][0]
        best = (-2.0, None, None)
        for candidate in range(top, top + layout["dy"], 2):
            cand = [(f"r{r + 1}c{c + 1}", (layout["x0"] + layout["dx"] * c, y, layout["tile"], layout["tile"]))
                    for r, y in enumerate(_grid_rows(layout, candidate)) for c in range(layout["cols"])]
            if not cand:
                continue
            tiles = [frame.crop((x, y, x + w, y + h)) for _, (x, y, w, h) in cand]
            mean = float(np.mean(np.max(index.coarse_scores(tiles), axis=1)))
            if mean > best[0]:
                best = (mean, candidate, cand)
        _, offset, boxes = best
        boxes = boxes or []
    tiles = [frame.crop((x, y, x + w, y + h)) for _, (x, y, w, h) in boxes]
    scores = index.coarse_scores(tiles) if tiles else []
    tiers = [frame_tier(frame, box) if layout.get("frame_check") else None for _, box in boxes]
    results = [index.match(tile, slot, box, thresholds, scores[i], tiers[i])
               for i, (tile, (slot, box)) in enumerate(zip(tiles, boxes))]
    if layout.get("frame_check"):
        results = [_rarity_check(m, frame, index) for m in results]
    accepted = [m.card_id for m in results if m.status == "accepted"]
    return {"layout": layout_name, "offset": offset, "tiles": [m.to_dict() for m in results],
            "accepted": list(dict.fromkeys(accepted)), "rejected": sum(m.status == "rejected" for m in results)}


def merge_scans(results: Iterable[Mapping]) -> dict:
    """Union of accepted ids over several frames (e.g. a scrolled list): {p: [...], s: [...], frames: n}."""
    found, frames = [], 0
    for result in results:
        frames += 1
        found.extend(result.get("accepted", []))
    unique = list(dict.fromkeys(found))
    return {"p": sorted(i for i in unique if i.startswith("1")), "s": sorted(i for i in unique if i.startswith("2")),
            "frames": frames}
