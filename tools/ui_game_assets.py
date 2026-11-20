#!/usr/bin/env python3
"""UI U0.2/U0.3: build local item icons and S-card thumbnails for the panel UI (private, gitignored, never committed).

Sources, all local (no network here — ``tools/ui_game_assets_fetch.py`` downloads the user-authorised files):

* the user hand-picked enza static atlases under ``dev_assets/private/game-assets-src/enza-N.webp`` and the pinned single
  item tiles under ``enza-tiles/<key>.webp`` (``TILE_ITEMS``; Fes entry ticket, recovery soda, Produce items); each is used
  only when its sha256 equals the value pinned in ``ui_game_assets_fetch.ENZA_SOURCES`` and its size matches, and is
  sliced with the explicit rectangles in ``ATLAS_ITEMS`` (item tiles lose their white tile background by an edge flood
  fill over bright, unsaturated pixels);
* the ShinyColorsDB card list and game-extracted card icons under ``dev_assets/private/game-assets-src/shinycolorsdb/``
  (preferred per card id since 2026-10-06; no licence — local private cache only), with the Shinycolors Wiki
  support-card list and thumbnails under ``wiki-support/`` and the P-card list under ``wiki-produce/`` as fallback and
  metadata (manifest ``cards`` = S, ``p_cards`` = P; each card records ``thumb_source``);
* S-card thumbnails only: guarded 1280x720 training-list frames from the user's own captures, used only when neither
  card list exists. Item icons no longer fall back to screenshot crops; the UI draws its line icon.

Writes only to ``ui/game-assets/`` and only after ``git check-ignore`` confirms that folder is ignored. Output:
``items/<id>.png`` (32 px) + ``items/<id>@2x.png`` (64 px), ``icons/<key>.png`` (+ ``@2x``; Produce rank badges,
Vo/Da/Vi/Me attribute tiles and ``ui_*`` task / tab icons, manifest ``icons``), ``cards/<card id>.png`` (96 px), ``manifest.json`` and
the CSP-safe ES module ``manifest.js`` (the UI cannot fetch JSON). No game process, no input.

Usage::

    python tools/ui_game_assets.py            # build ui/game-assets/
    python tools/ui_game_assets.py --check    # report which sources exist, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.ui_game_assets_fetch import (DB_CARD_ID, DB_CARDS_FILE, ENZA_SOURCES, ENZA_TILES, SRC_DIR,  # noqa: E402
                                        WIKI_CARDS_FILE, WIKI_P_CARDS_FILE, db_icon_file, decoded_tile_file, tile_file)

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
NATIVE = PRIVATE / "resource_native" / "image" / "native"
OUT_DIR = ROOT / "ui" / "game-assets"
CANVAS = (1280, 720)
MATCH_MAX_MAD = 6.0
ITEM_SIZES = {"src": 32, "src2x": 64}
CARD_SIZE = 96
ATLAS_SIZES = {"enza-1": (1085, 1085), "enza-2": (1326, 1326), "enza-3": (148, 148), "enza-4": (148, 148),
               "enza-5": (148, 148), "enza-6": (148, 148), "enza-7": (148, 148), "enza-8": (148, 148),
               "enza-9": (340, 340), "enza-10": (1607, 1607)}

# Logical item id -> atlas slice. rect = (x, y, w, h) in atlas pixels, found by inspecting the atlases (alpha components).
# mode "tile": a 150x150 in-game item tile; its white tile background is removed. mode "crop": already transparent.
# indicator_only: shown to mark something the tool never spends or automates (jewels, gacha).
ATLAS_ITEMS = {
    "money": {"atlas": "enza-2", "rect": (304, 456, 150, 150), "mode": "tile", "label": "マニー"},
    "jewel": {"atlas": "enza-2", "rect": (0, 760, 150, 150), "mode": "tile", "label": "ジュエル", "indicator_only": True},
    "gold_ticket": {"atlas": "enza-4", "rect": (0, 0, 148, 148), "mode": "tile", "label": "ゴールドトレーニングチケット"},
    "platinum_ticket": {"atlas": "enza-3", "rect": (0, 0, 148, 148), "mode": "tile", "label": "プラチナトレーニングチケット"},
    "skip_ticket": {"atlas": "enza-5", "rect": (0, 0, 148, 148), "mode": "tile", "label": "スキップチケット"},
    "bronze_ticket": {"atlas": "enza-6", "rect": (0, 0, 148, 148), "mode": "tile", "label": "ブロンズトレーニングチケット"},
    "silver_ticket": {"atlas": "enza-7", "rect": (0, 0, 148, 148), "mode": "tile", "label": "シルバートレーニングチケット"},
    "lv50_ticket": {"atlas": "enza-8", "rect": (0, 0, 148, 148), "mode": "tile", "label": "Lv50 トレーニングチケット"},
}
# 2026-10-08b (「营业 每日任务等这些目前截取的按钮 是否可能仅有图标icon+透明背景」): the home-screen buttons
# (営業/ミッション/ショップ/プレゼント/お知らせ/ガシャ) are no longer item crops with their plate and label; their pictograms
# are lifted as ``ui_*`` glyphs below (ATLAS_ICONS) and the UI draws every task / tab icon as one tinted glyph set.
# UI badges (not items): Produce rank badges and Vo/Da/Vi/Me attribute tiles, all already transparent ("crop").
# Output icons/<key>.png (32 px) + @2x (64 px), manifest ``icons``; the UI keeps its text label when one is missing.
# enza-9 also has an "S++"-style badge at (146, 70, 94, 95); unused because the summary payload has no such rank.
ATLAS_ICONS = {
    "rank_ss": {"atlas": "enza-9", "rect": (0, 0, 100, 98), "label": "SS", "group": "rank"},
    "rank_s_plus": {"atlas": "enza-9", "rect": (2, 154, 94, 95), "label": "S+", "group": "rank"},
    "rank_s": {"atlas": "enza-9", "rect": (102, 169, 94, 95), "label": "S", "group": "rank"},
    "rank_a": {"atlas": "enza-9", "rect": (202, 169, 89, 82), "label": "A", "group": "rank"},
    "rank_b": {"atlas": "enza-9", "rect": (5, 254, 89, 82), "label": "B", "group": "rank"},
    "rank_c": {"atlas": "enza-9", "rect": (202, 257, 91, 76), "label": "C", "group": "rank"},
    "rank_d": {"atlas": "enza-9", "rect": (246, 2, 91, 76), "label": "D", "group": "rank"},
    "rank_e": {"atlas": "enza-9", "rect": (246, 84, 91, 76), "label": "E", "group": "rank"},
    "rank_f": {"atlas": "enza-9", "rect": (102, 268, 70, 70), "label": "F", "group": "rank"},
    "attr_vo": {"atlas": "enza-2", "rect": (1097, 277, 74, 74), "label": "Vo", "group": "attr"},
    "attr_da": {"atlas": "enza-2", "rect": (942, 356, 74, 74), "label": "Da", "group": "attr"},
    "attr_vi": {"atlas": "enza-2", "rect": (942, 432, 74, 74), "label": "Vi", "group": "attr"},
    "attr_me": {"atlas": "enza-2", "rect": (1021, 277, 74, 74), "label": "Me", "group": "attr"},
    # UI icons (2026-10-08, 「任务相关icon尽量找合适的游戏内icon」): task / tab icons. mode "tile" = 150x150 item tile
    # (white frame removed), "glyph" = a single-colour glyph lifted off a light game button (dark pixels kept, see
    # ``lift_glyph``). The UI tints glyphs as a CSS mask in tabs and shows them as-is on a pastel plate in task lists.
    "ui_home": {"atlas": "enza-10", "rect": (1506, 1116, 56, 54), "label": "ホーム", "group": "ui", "mode": "glyph"},
    "ui_settings": {"atlas": "enza-10", "rect": (362, 250, 62, 52), "label": "オプション", "group": "ui", "mode": "glyph"},
    "ui_items": {"atlas": "enza-10", "rect": (18, 1188, 56, 52), "label": "アイテム一覧", "group": "ui", "mode": "glyph"},
    # Pictograms of the labelled enza-1 home buttons: the rect is cut tightly around the pictogram (no label row) and
    # ``lift`` keeps the lighter gradient of these glyphs (luminance < 185) while dropping the button outline at the rect
    # edge; sparkles (> 200) stay out. Verified visually (dev_assets/private/ui-icons-20261008b/glyph-sheet.png).
    "ui_shop": {"atlas": "enza-1", "rect": (789, 817, 44, 36), "label": "ショップ", "group": "ui", "mode": "glyph",
                "lift": {"lo": 185, "hi": 220, "min_size": 10, "drop_edge": True}},
    "ui_mission": {"atlas": "enza-1", "rect": (1009, 750, 38, 37), "label": "ミッション", "group": "ui", "mode": "glyph",
                   "lift": {"lo": 185, "hi": 220, "min_size": 10, "drop_edge": True}},
    "ui_business": {"atlas": "enza-1", "rect": (634, 824, 50, 38), "label": "営業", "group": "ui", "mode": "glyph",
                    "lift": {"lo": 185, "hi": 220, "min_size": 10, "drop_edge": True}},
    "ui_present": {"atlas": "enza-1", "rect": (778, 754, 50, 33), "label": "プレゼント", "group": "ui", "mode": "glyph",
                   "lift": {"lo": 195, "hi": 225, "min_size": 10, "drop_edge": True}},
    "ui_notice": {"atlas": "enza-1", "rect": (722, 822, 50, 37), "label": "お知らせ", "group": "ui", "mode": "glyph",
                  "lift": {"lo": 185, "hi": 220, "min_size": 10, "drop_edge": True}},
    # The W.I.N.G. swan tile (enza-2, same tile as ui_wing) as a single-colour glyph: dark teal kept, light inner swirl
    # and the white tile frame dropped. Used where the task / tab set is drawn as tinted glyphs.
    "ui_wing_mark": {"atlas": "enza-2", "rect": (310, 614, 138, 138), "label": "W.I.N.G.", "group": "ui", "mode": "glyph",
                     "lift": {"lo": 158, "hi": 185, "drop_edge": True}},
    "ui_gasha": {"atlas": "enza-1", "rect": (1005, 324, 46, 42), "label": "ガシャ", "group": "ui", "mode": "glyph",
                 "lift": {"lo": 185, "hi": 215, "drop_edge": True}},
    # 2026-10-09 (icon unification): the アイドル button microphone (enza-1, stars dropped by drop_edge) for Match Live and
    # the camera button (enza-10) for 诊断; same lifted single-colour glyph treatment. Checked visually
    # (dev_assets/private/ui-style-20261009/glyph-sheet.png).
    "ui_mic": {"atlas": "enza-1", "rect": (908, 674, 36, 40), "label": "アイドル", "group": "ui", "mode": "glyph",
               "lift": {"lo": 170, "hi": 210, "min_size": 10, "drop_edge": True}},
    "ui_camera": {"atlas": "enza-10", "rect": (540, 527, 58, 56), "label": "カメラ", "group": "ui", "mode": "glyph",
                  "lift": {"lo": 170, "hi": 210, "min_size": 10, "drop_edge": True}},
    "ui_fes_p": {"atlas": "enza-2", "rect": (0, 152, 150, 150), "label": "フェスP", "group": "ui", "mode": "tile"},
    "ui_match_p": {"atlas": "enza-2", "rect": (152, 608, 150, 150), "label": "マッチライブP", "group": "ui", "mode": "tile"},
    "ui_produce_p": {"atlas": "enza-2", "rect": (456, 456, 150, 150), "label": "育成P", "group": "ui", "mode": "tile"},
    "ui_wing": {"atlas": "enza-2", "rect": (304, 608, 150, 150), "label": "W.I.N.G.", "group": "ui", "mode": "tile"},
}

# 2026-10-08c: single pinned 148x148 item tiles (ui_game_assets_fetch.ENZA_TILES, chosen from the user's chrome-urls.txt).
# item key -> label. Every tile is a "tile" (white frame and its faint star pattern removed by ``strip_tile``). The
# Produce item keys are the ids of tools/item_inventory_scan.py, so the 资源 tab shows these clean tiles instead of the
# screenshot crops. Names / confidence: ui/components/game-assets.js P_ITEM_NAMES.
TILE_ITEMS = {t["item"]: {"tile": t["key"], "label": t["purpose"]} for t in ENZA_TILES}
TILE_SIZE = (148, 148)

# Items the UI wants but no authorised source provides yet: the UI keeps its line icon for these.
# 2026-10-08c: Fes entry ticket, recovery soda and the 17 Produce items now come from pinned tiles (TILE_ITEMS).
# 2026-10-08d: お手製差し入れ is pinned too (アイテム一覧 消費アイテム tile), so nothing is missing for now.
MISSING_ITEMS = {}

# Fallback item sources from the user's own captures. Emptied 2026-10-08 (「尽量不用截图」): every item now comes from a
# pinned atlas, and a missing atlas falls back to the UI's self-drawn line icon instead of a screenshot crop.
CAPTURE_ITEMS = {}

# Training card option cases -> wiki card id (verified visually against training_list/asahi_portrait.png).
CALIBRATED_WIKI = {"2050170010": "【くっつく、くっつかない】芹沢あさひ"}

# Fallback S-card thumbnails: training selection list 7 x 3 grid in the user's own 1280x720 frames.
CARD_FRAMES = (
    PRIVATE / "nav-v15-training-list-back-readonly-frames" / "frame-001.png",
    PRIVATE / "training-selection-readonly-20261001e-frames" / "frame-001.png",
    PRIVATE / "training-select-asahi-native-v14-frames" / "frame-001.png",
)
CARD_GUARD = (NATIVE / "training_list" / "asahi_portrait.png", (409, 189, 101, 87))
CARD_GRID = {"x0": 268, "y0": 184, "dx": 138, "dy": 154, "cols": 7, "rows": 3, "w": 108, "h": 108}
CALIBRATED = {(0, 1): "【くっつく、くっつかない】芹沢あさひ"}
RARITIES = ("UR", "SSR", "SR", "R", "N")
# Thumbnail preference per card id: "db" = ShinyColorsDB game-extracted icon first, wiki fallback.
PREFER = ("db", "wiki")
DEFAULT_PREFER = "db"


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_gitignored(path):
    from tools.local_output import portable_output_allowed
    if portable_output_allowed(Path(path), ROOT):
        return True
    try:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def load_rgb(path):
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGB")


def load_atlas(atlas_id):
    """The pinned atlas as RGBA, or raise ValueError when missing / changed."""
    from PIL import Image

    source = next(s for s in ENZA_SOURCES if s["id"] == atlas_id)
    path = SRC_DIR / source["path"]
    if not path.is_file():
        raise ValueError(f"缺少 {rel(path)}（先运行 tools/ui_game_assets_fetch.py --fetch）")
    if sha256(path) != source["sha256"]:
        raise ValueError(f"{rel(path)} 的 sha256 与固定值不符")
    with Image.open(path) as image:
        atlas = image.convert("RGBA")
    if atlas.size != ATLAS_SIZES[atlas_id]:
        raise ValueError(f"{atlas_id} 尺寸 {atlas.size} 与记录的 {ATLAS_SIZES[atlas_id]} 不符")
    return atlas


def load_tile(key):
    """A pinned single item tile as RGBA, or raise ValueError when missing / changed."""
    from PIL import Image

    source = next(t for t in ENZA_TILES if t["key"] == key)
    path = tile_file(key)
    if not path.is_file():
        path = decoded_tile(key, source)
    elif sha256(path) != source["sha256"]:
        raise ValueError(f"{rel(path)} 的 sha256 与固定值不符")
    with Image.open(path) as image:
        tile = image.convert("RGBA")
    if tile.size != TILE_SIZE:
        raise ValueError(f"{key} 尺寸 {tile.size} 不是 {TILE_SIZE}")
    return tile


def decoded_tile(key, source):
    """2026-10-09: the decoded chrome/<key>.png of a pinned tile, accepted only when its catalog record
    (chrome/<key>.json, written when the user's URL list was downloaded) names the same raw sha256 and size."""
    path = decoded_tile_file(key)
    record_path = path.with_suffix(".json")
    if not path.is_file() or not record_path.is_file():
        raise ValueError(f"缺少 {rel(tile_file(key))}（先运行 tools/ui_game_assets_fetch.py --fetch）")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("key") != key or record.get("sha256") != source["sha256"] or record.get("size") != list(TILE_SIZE):
        raise ValueError(f"{rel(record_path)} 与固定的 sha256/尺寸不符")
    return path


def strip_tile(image, max_spread=28, min_value=170):
    """Remove an item tile's white frame: flood from the edges over bright, unsaturated (or transparent) pixels."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    data = np.asarray(image.convert("RGBA")).astype(np.int16)
    rgb, alpha = data[:, :, :3], data[:, :, 3]
    background = (alpha < 8) | (((rgb.max(2) - rgb.min(2)) <= max_spread) & (rgb.min(2) >= min_value))
    labels, _ = ndimage.label(background)
    edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]).tolist())) - {0}
    removed = np.isin(labels, sorted(edge))
    fringe = ndimage.binary_dilation(removed) & ~removed
    out = data.copy()
    out[:, :, 3] = np.where(removed, 0, np.where(fringe, alpha // 2, alpha))
    result = Image.fromarray(out.astype(np.uint8))
    box = result.getbbox()
    if not box:
        raise ValueError("去背景后为空")
    return result.crop(box)


def lift_glyph(image, lo=150, hi=200, min_size=40, drop_edge=False, invert=False):
    """A single-colour glyph from a light game button: keep dark components (luminance < ``lo``, at least ``min_size``
    pixels; drops sparkles and the light button face), with an anti-aliased edge up to ``hi``; crop to the glyph.
    ``drop_edge`` also drops components touching the crop border (the button outline / label when the rect is cut
    tightly around the pictogram of a labelled home-screen button). ``invert`` lifts a light glyph off a dark/saturated
    button instead (luminance measured as 255 - lum)."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    data = np.asarray(image.convert("RGBA")).astype(np.float64)
    lum = data[:, :, :3] @ np.array([0.299, 0.587, 0.114])
    if invert:
        lum = 255 - lum
    alpha = data[:, :, 3] / 255
    core = (lum < lo) & (alpha > 0.5)
    labels, count = ndimage.label(core)
    sizes = ndimage.sum(core, labels, range(1, count + 1)) if count else []
    edge_ids = set()
    if drop_edge and count:
        edge_ids = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]).tolist())) - {0}
    keep = np.isin(labels, [i + 1 for i, size in enumerate(sizes) if size >= min_size and i + 1 not in edge_ids])
    if not keep.any():
        raise ValueError("字形为空")
    edge = np.clip((hi - lum) / (hi - lo), 0, 1) * ndimage.binary_dilation(keep, iterations=2) * alpha
    out = data.copy()
    out[:, :, 3] = np.where(keep, alpha, edge) * 255
    result = Image.fromarray(out.astype(np.uint8))
    return result.crop(result.getbbox())


def trim(image):
    box = image.getbbox()
    if not box:
        raise ValueError("裁剪区域为空")
    return image.crop(box)


def guard_frame(frame, guard):
    """Raise ValueError unless the frame is 1280x720 and the guard template matches at its ROI."""
    import numpy as np

    if frame.size != CANVAS:
        raise ValueError(f"画面尺寸不是 {CANVAS}: {frame.size}")
    template_path, (x, y, w, h) = guard
    template = load_rgb(template_path)
    if template.size != (w, h):
        raise ValueError(f"模板尺寸与 ROI 不符: {rel(template_path)}")
    crop = np.asarray(frame.crop((x, y, x + w, y + h)), dtype=np.float32)
    mad = float(np.abs(crop - np.asarray(template, dtype=np.float32)).mean())
    if mad > MATCH_MAX_MAD:
        raise ValueError(f"守卫模板不匹配（MAD {mad:.1f}）: {rel(template_path)}")
    return mad


def fit(image, size):
    from PIL import Image

    copy = image.convert("RGBA")
    scale = min(size / copy.width, size / copy.height)
    copy = copy.resize((max(1, round(copy.width * scale)), max(1, round(copy.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(copy, ((size - copy.width) // 2, (size - copy.height) // 2))
    return canvas


def card_file(card_id):
    return "card-" + hashlib.sha1(card_id.encode("utf-8")).hexdigest()[:10] + ".png"


def plan():
    """Sources that exist and their status (no writes)."""
    report = {}
    for atlas_id in ATLAS_SIZES:
        try:
            load_atlas(atlas_id)
            report[f"atlas:{atlas_id}"] = True
        except (OSError, ValueError) as error:
            report[f"atlas:{atlas_id}"] = str(error)
    for key, spec in TILE_ITEMS.items():
        try:
            load_tile(spec["tile"])
            report[f"tile:{key}"] = True
        except (OSError, ValueError) as error:
            report[f"tile:{key}"] = str(error)
    for key, spec in CAPTURE_ITEMS.items():
        report[f"capture:{key}"] = spec["source"].is_file() and (spec["guard"] is None or spec["guard"][0].is_file())
    report["wiki_cards"] = WIKI_CARDS_FILE.is_file()
    report["wiki_p_cards"] = WIKI_P_CARDS_FILE.is_file()
    report["db_cards"] = DB_CARDS_FILE.is_file()
    report["capture_cards"] = [rel(f) for f in CARD_FRAMES if f.is_file()] if CARD_GUARD[0].is_file() else []
    report["missing_items"] = MISSING_ITEMS
    return report


def _save_item(out_dir, key, image, entry, manifest):
    names = {}
    for field, size in ITEM_SIZES.items():
        name = f"{key}.png" if field == "src" else f"{key}@2x.png"
        fit(image, size).save(out_dir / "items" / name)
        names[field] = f"game-assets/items/{name}"
    manifest["items"][key] = {**names, **entry}


def build_icons(out_dir, manifest, atlases=None):
    atlases = {} if atlases is None else atlases
    for key, spec in ATLAS_ICONS.items():
        try:
            if spec["atlas"] not in atlases:
                atlases[spec["atlas"]] = load_atlas(spec["atlas"])
            x, y, w, h = spec["rect"]
            piece = atlases[spec["atlas"]].crop((x, y, x + w, y + h))
            mode = spec.get("mode", "crop")
            image = (strip_tile(piece) if mode == "tile" else lift_glyph(piece, **spec.get("lift", {})) if mode == "glyph"
                     else trim(piece))
        except (OSError, ValueError) as error:
            manifest["skipped"].append({"icon": key, "reason": str(error)})
            continue
        names = {}
        for field, size in ITEM_SIZES.items():
            name = f"{key}.png" if field == "src" else f"{key}@2x.png"
            fit(image, size).save(out_dir / "icons" / name)
            names[field] = f"game-assets/icons/{name}"
        manifest["icons"][key] = {**names, "label": spec["label"], "group": spec["group"], "source": spec["atlas"],
                                  "rect": list(spec["rect"]), "mode": spec.get("mode", "crop")}


def build_items(out_dir, manifest):
    atlases = {}
    for key, spec in ATLAS_ITEMS.items():
        try:
            if spec["atlas"] not in atlases:
                atlases[spec["atlas"]] = load_atlas(spec["atlas"])
            x, y, w, h = spec["rect"]
            piece = atlases[spec["atlas"]].crop((x, y, x + w, y + h))
            image = strip_tile(piece) if spec["mode"] == "tile" else trim(piece)
        except (OSError, ValueError) as error:
            manifest["skipped"].append({"item": key, "reason": str(error)})
            continue
        entry = {"label": spec["label"], "source": spec["atlas"], "rect": list(spec["rect"]), "mode": spec["mode"]}
        if spec.get("indicator_only"):
            entry["indicator_only"] = True
        _save_item(out_dir, key, image, entry, manifest)

    for key, spec in TILE_ITEMS.items():
        if key in manifest["items"]:
            continue
        try:
            image = strip_tile(load_tile(spec["tile"]))
        except (OSError, ValueError) as error:
            manifest["skipped"].append({"item": key, "reason": str(error)})
            continue
        _save_item(out_dir, key, image, {"label": spec["label"], "source": f"tile:{spec['tile']}", "mode": "tile"}, manifest)

    for key, spec in CAPTURE_ITEMS.items():
        if key in manifest["items"]:
            continue
        source = spec["source"]
        if not source.is_file():
            manifest["skipped"].append({"item": key, "reason": "缺少来源", "source": rel(source)})
            continue
        try:
            image = load_rgb(source)
            entry = {"source": rel(source), "source_sha256": sha256(source)}
            if spec["guard"]:
                entry["guard_mad"] = round(guard_frame(image, spec["guard"]), 2)
            if spec["box"]:
                image = image.crop(spec["box"])
                entry["box"] = list(spec["box"])
            if spec.get("strip"):
                image = strip_tile(image)
        except (OSError, ValueError) as error:
            manifest["skipped"].append({"item": key, "reason": str(error), "source": rel(source)})
            continue
        _save_item(out_dir, key, image, entry, manifest)
    manifest["missing_items"] = {k: v for k, v in MISSING_ITEMS.items() if k not in manifest["items"]}


SPECIALTY = re.compile(r"^[A-Za-z &()]{0,40}$")
IDEAS = ("Vocal", "Dance", "Visual", "Mental")


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _thumb(path):
    from PIL import Image

    with Image.open(path) as image:
        thumb = image.convert("RGBA")
    if thumb.size != (CARD_SIZE, CARD_SIZE):
        thumb = thumb.resize((CARD_SIZE, CARD_SIZE), Image.LANCZOS)
    return thumb


def build_cards(out_dir, manifest, kind="S", prefer=DEFAULT_PREFER):
    """S cards (``kind`` S -> manifest ``cards``) or P cards (``kind`` P -> manifest ``p_cards``).

    Card ids are the union of the wiki list and the ShinyColorsDB list (exact 10-digit ids only, no name matching).
    The thumbnail comes from ``prefer`` ("db": ShinyColorsDB game-extracted icon) and falls
    back to the other source; each entry records ``thumb_source``. Wiki metadata (English names, specialty, idea) is
    kept where the wiki lists the card; DB-only cards carry the DB name and rarity only (no skill/stat data).
    Missing thumbnails retain the directory entry with src=None; the return value counts directory cards, not images."""
    wiki_file, prefix, folder, field = ((WIKI_CARDS_FILE, "2", "wiki-support", "cards") if kind == "S"
                                        else (WIKI_P_CARDS_FILE, "1", "wiki-produce", "p_cards"))
    wiki = _load_json(wiki_file) if wiki_file.is_file() else {}
    db = _load_json(DB_CARDS_FILE) if DB_CARDS_FILE.is_file() else {}
    wiki_cards, db_cards = {}, {}
    for card in wiki.get("cards", []):
        card_id = str(card.get("card_id", ""))
        if DB_CARD_ID.match(card_id) and card_id.startswith(prefix) and card.get("rarity") in RARITIES:
            wiki_cards.setdefault(card_id, card)
    for card in db.get("cards", []):
        card_id = str(card.get("card_id", ""))
        if (DB_CARD_ID.match(card_id) and card_id.startswith(prefix) and card.get("kind") == {"1": "P", "2": "S"}[prefix]
                and card.get("rarity") in RARITIES):
            db_cards.setdefault(card_id, card)
    manifest.setdefault(field, [])
    counts = {"shinycolorsdb": 0, "wiki": 0}
    order = [*wiki_cards, *sorted(i for i in db_cards if i not in wiki_cards)]
    for card_id in order:
        sources = {"shinycolorsdb": db_icon_file(card_id) if card_id in db_cards else None,
                   "wiki": SRC_DIR / folder / "icons" / f"CardIcon-{card_id}.png"}
        if sources["wiki"] is not None and not sources["wiki"].is_file():
            sources["wiki"] = None
        ranked = ["shinycolorsdb", "wiki"] if prefer == "db" else ["wiki", "shinycolorsdb"]
        thumb, used, errors = None, None, []
        for name in ranked:
            if sources[name] is None:
                continue
            try:
                thumb, used = _thumb(sources[name]), name
                break
            except OSError as error:
                errors.append(f"{name}: {error}")
        if thumb is None:
            manifest["skipped"].append({"card": card_id, "reason": "; ".join(errors) or "缩略图未下载"})
        else:
            thumb.save(out_dir / "cards" / f"{card_id}.png")
            counts[used] += 1
        base = wiki_cards.get(card_id) or db_cards[card_id]
        entry = {"id": card_id, "idol": str(base.get("idol", ""))[:40], "title": str(base.get("title", ""))[:80],
                 "idol_en": base.get("idol_en", ""), "title_en": base.get("title_en", ""), "rarity": base["rarity"],
                 "src": f"game-assets/cards/{card_id}.png" if thumb is not None else None, "thumb_source": used,
                 "thumbnail_missing": thumb is None}
        if card_id in db_cards and card_id in wiki_cards and db_cards[card_id]["rarity"] != wiki_cards[card_id]["rarity"]:
            entry["rarity_conflict"] = {"wiki": wiki_cards[card_id]["rarity"], "shinycolorsdb": db_cards[card_id]["rarity"]}
        specialty = base.get("specialty", "")
        if isinstance(specialty, str) and specialty and SPECIALTY.match(specialty):
            entry["specialty"] = specialty
        if base.get("idea") in IDEAS:
            entry["idea"] = base["idea"]
        if kind == "S" and card_id in CALIBRATED_WIKI:
            entry["option"] = CALIBRATED_WIKI[card_id]
            entry["calibrated"] = True
        manifest[field].append(entry)
    key = "card_source" if kind == "S" else "p_card_source"
    manifest[key] = {"kind": "shinycolorsdb" if counts["shinycolorsdb"] or (not counts["wiki"] and db_cards) else "wiki", "prefer": prefer,
                     "thumbs": counts, "wiki_listed": len(wiki_cards), "db_listed": len(db_cards),
                     "missing_thumbnails": len(order) - sum(counts.values()),
                     "wiki": {"source": wiki.get("source"), "fetched_at": wiki.get("fetched_at"),
                              "list": rel(wiki_file)} if wiki else None,
                     "shinycolorsdb": {"fetched_at": db.get("fetched_at"), "list": rel(DB_CARDS_FILE),
                                       "license": "none; game-extracted; local private cache only"} if db else None}
    return len(order)


build_wiki_cards = build_cards  # backwards-compatible name


def build_capture_cards(out_dir, manifest):
    chosen = None
    for candidate in CARD_FRAMES if CARD_GUARD[0].is_file() else ():
        if not candidate.is_file():
            continue
        try:
            frame = load_rgb(candidate)
            mad = guard_frame(frame, CARD_GUARD)
        except (OSError, ValueError) as error:
            manifest["skipped"].append({"cards": rel(candidate), "reason": str(error)})
            continue
        chosen = candidate
        break
    if not chosen:
        manifest["skipped"].append({"cards": "训练列表", "reason": "没有通过守卫的来源帧"})
        return
    g = CARD_GRID
    for row in range(g["rows"]):
        for col in range(g["cols"]):
            x, y = g["x0"] + g["dx"] * col, g["y0"] + g["dy"] * row
            option = CALIBRATED.get((row, col))
            card_id = option or f"capture-r{row + 1}c{col + 1}"
            name = card_file(card_id)
            fit(frame.crop((x, y, x + g["w"], y + g["h"])), CARD_SIZE).save(out_dir / "cards" / name)
            entry = {"id": card_id, "src": f"game-assets/cards/{name}", "calibrated": option is not None,
                     "slot": f"r{row + 1}c{col + 1}", "box": [x, y, g["w"], g["h"]]}
            if option:
                entry["option"] = option
            manifest["cards"].append(entry)
    manifest["card_source"] = {"kind": "capture", "source": rel(chosen), "source_sha256": sha256(chosen), "guard_mad": round(mad, 2)}


def build(out_dir=OUT_DIR, prefer=DEFAULT_PREFER):
    if prefer not in PREFER:
        raise ValueError(f"prefer 只能是 {PREFER}")
    out_dir = Path(out_dir).resolve()
    if out_dir != OUT_DIR.resolve():
        raise ValueError(f"只能写到 {rel(OUT_DIR)}")
    if not is_gitignored(out_dir / "manifest.json"):
        raise ValueError("ui/game-assets/ 未被 .gitignore 忽略，拒绝写入游戏图像")
    for sub in ("items", "cards", "icons"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
        for stale in (out_dir / sub).glob("*.png"):
            stale.unlink()
    manifest = {"version": 2, "generated_by": "tools/ui_game_assets.py", "private": True,
                "note": "本机私有：来自用户授权的 enza 静态资源、ShinyColorsDB（游戏提取、无许可）与 Shinycolors Wiki 缩略图及用户自己的截图；已被 .gitignore 忽略，不得提交。",
                "items": {}, "icons": {}, "cards": [], "p_cards": [], "skipped": []}
    build_items(out_dir, manifest)
    build_icons(out_dir, manifest)
    have_lists = WIKI_CARDS_FILE.is_file() or DB_CARDS_FILE.is_file()
    if not (have_lists and build_cards(out_dir, manifest, prefer=prefer)):
        build_capture_cards(out_dir, manifest)
    if WIKI_P_CARDS_FILE.is_file() or DB_CARDS_FILE.is_file():
        build_cards(out_dir, manifest, kind="P", prefer=prefer)

    text = json.dumps(manifest, ensure_ascii=False, indent=1) + "\n"
    (out_dir / "manifest.json").write_text(text, encoding="utf-8", newline="\n")
    (out_dir / "manifest.js").write_text(
        "// Generated by tools/ui_game_assets.py; private local assets (gitignored). Do not commit.\n"
        f"export default {text.rstrip()};\n", encoding="utf-8", newline="\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report available sources; write nothing")
    parser.add_argument("--prefer", choices=PREFER, default=DEFAULT_PREFER,
                        help="card thumbnail source tried first per card id (default db = ShinyColorsDB)")
    args = parser.parse_args(argv)
    if args.check:
        sys.stdout.buffer.write((json.dumps(plan(), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return 0
    try:
        manifest = build(prefer=args.prefer)
    except ValueError as error:
        parser.error(str(error))
    summary = {"items": sorted(manifest["items"]), "icons": sorted(manifest["icons"]), "cards": len(manifest["cards"]), "p_cards": len(manifest["p_cards"]),
               "card_source": manifest.get("card_source", {}).get("kind"),
               "thumbs": {"S": manifest.get("card_source", {}).get("thumbs"), "P": manifest.get("p_card_source", {}).get("thumbs")},
               "missing_items": manifest["missing_items"],
               "skipped": manifest["skipped"][:20], "skipped_total": len(manifest["skipped"])}
    sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
