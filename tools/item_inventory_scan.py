#!/usr/bin/env python3
"""Offline 「识别持有」 scan: items, resource counters and cards from saved 1280x720 frames.

Reads only local files: frames the user already captured, the private item templates, the pinned enza atlases and the
wiki card icons. OCR (counts) uses MaaFramework on the saved image only (screenshot-free recogniser, zero input). No
device, no input, no network. Writes ``ui/game-assets/item-scan.js`` (gitignored, CSP-safe ES module) for the 资源 tab
and, for card layouts with ``--card-import``, merges into ``ui/game-assets/inventory-scan.js`` (the reviewed 持有卡牌
import; nothing is added there until the user presses 确定).

Usage::

    python tools/item_inventory_scan.py --build-templates                      # crop labelled reference tiles
    python tools/item_inventory_scan.py --layout produce_item_select FRAME...  # items (アイテム選択 grid)
    python tools/item_inventory_scan.py --layout home_counters FRAME...        # マニー / stamina / Fes rights
    python tools/item_inventory_scan.py --layout card:idol_list --card-import FRAME...
    python tools/item_inventory_scan.py --layout item_list --append FRAME...    # アイテム一覧 (counts by name; provisional)
    python tools/item_inventory_scan.py --dry-run --truth TRUTH.json --layout produce_item_select FRAME...
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import item_inventory as ii  # noqa: E402

OUT_FILE = ROOT / "ui" / "game-assets" / "item-scan.js"
ICON_DIR = ROOT / "ui" / "game-assets" / "item-scan"
TEMPLATE_DIR = ROOT / "dev_assets" / "private" / "item_inventory" / "templates"
LABELS = ROOT / "tools" / "item_inventory_labels.json"
MAX_FRAMES = 400
CARD_LAYOUTS = ("card:idol_list", "card:unit_formation")
ITEM_LIST = "item_list"   # agent/core/item_list.py: アイテム一覧, names + 所持数 by OCR (no icon templates)


def is_gitignored(path: Path) -> bool:
    from tools.local_output import portable_output_allowed
    if portable_output_allowed(path, ROOT):
        return True
    try:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def rel(path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def load_image(path):
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGB")


# ---------------------------------------------------------------------------------------------------------------- templates
def build_templates(labels_path=LABELS, out_dir=TEMPLATE_DIR) -> dict:
    """Crop each labelled tile (108x112) from its private reference frame into ``out_dir/<id>.png``."""
    out_dir = Path(out_dir)
    if not is_gitignored(out_dir / "x.png"):
        raise ValueError(f"{rel(out_dir)} 未被 .gitignore 忽略，拒绝写入游戏图像")
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = {}, []
    for ref in labels["references"]:
        layout = ii.LAYOUTS[ref["layout"]]
        path = ROOT / ref["frame"]
        if not path.is_file():
            skipped.append({"frame": ref["frame"], "reason": "缺少参考帧"})
            continue
        frame = load_image(path)
        if frame.size != ii.CANVAS:
            skipped.append({"frame": ref["frame"], "reason": "不是 1280×720"})
            continue
        for slot, item_id in ref["tiles"].items():
            if item_id in written:
                continue
            r, c = (int(v) for v in slot[1:].split("c"))
            x = round(layout["x0"] + layout["dx"] * (c - 1))
            y = ref["row_tops"][r - 1]
            frame.crop((x, y, x + layout["tile"][0], y + layout["tile"][1])).save(out_dir / f"{item_id}.png")
            written[item_id] = {"frame": rel(path), "slot": slot,
                                "frame_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (out_dir / "templates.json").write_text(json.dumps({"version": 1, "templates": written, "skipped": skipped},
                                                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"templates": len(written), "skipped": skipped}


def atlas_refs() -> dict:
    """Account-item references from the pinned enza atlases (sha256-checked); {} when unavailable."""
    try:
        from tools.ui_game_assets import ATLAS_ITEMS, load_atlas
    except ImportError:
        return {}
    refs, atlases = {}, {}
    for key, spec in ATLAS_ITEMS.items():
        if spec.get("mode") != "tile" or spec.get("indicator_only"):
            continue
        try:
            atlas = atlases.get(spec["atlas"]) or atlases.setdefault(spec["atlas"], load_atlas(spec["atlas"]))
        except (OSError, ValueError):
            continue
        x, y, w, h = spec["rect"]
        refs[key] = ii.atlas_tile_as_grid_tile(atlas.crop((x, y, x + w, y + h)))
    return refs


def load_index(template_dir=TEMPLATE_DIR, with_atlas=True):
    refs, meta = {}, {}
    for path in sorted(Path(template_dir).glob("*.png")):
        item_id = path.stem
        if item_id not in ii.PRODUCE_ITEMS:
            continue
        refs[item_id] = load_image(path)
        meta[item_id] = {"kind": "produce_item", **ii.PRODUCE_ITEMS[item_id]}
    if with_atlas:
        for key, image in atlas_refs().items():
            refs[key] = image
            meta[key] = {"kind": "account_item"}
    return ii.ItemIndex(refs, meta) if refs else None


# ---------------------------------------------------------------------------------------------------------------- OCR
class MaaOcr:
    """Whole-frame MaaFramework OCR on a saved image (screenshot-only recogniser; never sends input)."""

    def __init__(self):
        from tools.wing_sample_ocr import WingRecognizer
        self.rec = WingRecognizer()

    def __call__(self, image):
        import numpy as np

        bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
        result = self.rec.run(bgr, {"recognition": "OCR", "roi": [0, 0, *ii.CANVAS], "threshold": 0.3})
        return [(t["text"], t["score"], t["box"]) for t in result["texts"]]

    @property
    def input_attempts(self):
        return self.rec.input_attempts


def observed_at(path) -> str:
    return dt.datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------------------------- scan
def scan_items(frames, layout, index, ocr) -> dict:
    results, observed, summary = [], [], []
    for path in frames:
        image = load_image(path)
        texts = ocr(image) if ocr else []
        when = observed_at(path)
        result = ii.recognise(image, index, layout, texts, frame_name=rel(path))
        results.append(result)
        observed.append(when)
        summary.append({"file": rel(path), "observed_at": when, "layout": layout, "screen_ok": result["screen_ok"],
                        "refused": result["refused"], "favorites_only": result.get("favorites_only"),
                        "accepted": len(result["items"]), "rejected": result["rejected"],
                        "reasons": sorted({t["reason"] for t in result["tiles"] if t["status"] == "rejected"}
                                          | {c["reason"] for c in result["counters"].values() if c["status"] == "rejected"})})
    return {"results": results, "frames": summary, "items": ii.merge_reads(results, observed)}


def scan_item_list(frames, ocr) -> dict:
    from agent.core import item_list as il

    results, observed, summary = [], [], []
    for path in frames:
        image = load_image(path)
        result = il.recognise(image, ocr(image) if ocr else [], frame_name=rel(path))
        when = observed_at(path)
        results.append(result)
        observed.append(when)
        summary.append({"file": rel(path), "observed_at": when, "layout": ITEM_LIST, "screen_ok": result["screen_ok"],
                        "refused": result["refused"], "tab": result.get("tab"), "accepted": len(result["items"]),
                        "rejected": len(result["rejected"]), "unmatched": [u["name"] for u in result["unmatched"]][:12],
                        "calibrated": il.CALIBRATED})
    return {"results": results, "frames": summary, "items": il.merge_reads(results, observed)}


def scan_cards(frames, layout):
    from agent.core import card_inventory as ci

    index = ci.CardIndex.from_assets()
    cards, summary, import_cards = {}, [], {}
    for path in frames:
        image = load_image(path)
        result = ci.recognise(image, index, layout)
        when = observed_at(path)
        summary.append({"file": rel(path), "observed_at": when, "layout": f"card:{layout}",
                        "accepted": len(result["accepted"]), "rejected": result["rejected"]})
        for tile in result["tiles"]:
            if tile["status"] != "accepted":
                continue
            cards[tile["card_id"]] = {"id": tile["card_id"], "score": tile["score"], "frame": rel(path), "observed_at": when}
            entry = import_cards.setdefault(tile["card_id"], {"id": tile["card_id"], "score": 0.0, "verify": 0.0, "frames": 0})
            entry["score"], entry["verify"] = max(entry["score"], tile["score"]), max(entry["verify"], tile["verify"])
            entry["frames"] += 1
    card_scan = {"version": 1, "generated_by": "tools/item_inventory_scan.py", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "layout": layout, "frames": [{"file": Path(s["file"]).name, "layout": layout, "accepted": s["accepted"],
                                               "rejected": s["rejected"]} for s in summary],
                 "cards": sorted(import_cards.values(), key=lambda c: c["id"]),
                 "rejected_tiles": sum(s["rejected"] for s in summary)}
    return {"frames": summary, "cards": cards, "card_scan": card_scan}


def evaluate(results, frames, truth) -> dict:
    """Compare per-frame accepted items with private truth {file: {slot: [item_id, count]}} -> accuracy summary."""
    report = {"tiles_truth": 0, "accepted": 0, "correct": 0, "wrong": [], "refused_frames": [], "missed": 0}
    for path, result in zip(frames, results):
        key = rel(path)
        expected = truth.get(key) or truth.get(Path(path).name)
        if expected is None:
            continue
        if not result["screen_ok"]:
            report["refused_frames"].append({"file": key, "reason": result["refused"]})
            continue
        by_slot = {t["slot"]: t for t in result["tiles"]}
        for slot, (item_id, count) in expected.items():
            report["tiles_truth"] += 1
            tile = by_slot.get(slot)
            if not tile or tile["status"] != "accepted":
                report["missed"] += 1
                continue
            report["accepted"] += 1
            if tile["item_id"] == item_id and tile["count"] == count:
                report["correct"] += 1
            else:
                report["wrong"].append({"file": key, "slot": slot, "got": [tile["item_id"], tile["count"]],
                                        "want": [item_id, count]})
    return report


# ---------------------------------------------------------------------------------------------------------------- output
def read_module(out_file=OUT_FILE):
    path = Path(out_file)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    start = text.find("export default ")
    return json.loads(text[start + len("export default "):].rstrip().rstrip(";")) if start >= 0 else None


def item_rows(items: dict, index) -> list:
    rows = []
    for item_id, value in sorted(items.items()):
        meta = (index.meta.get(item_id) if index else None) or {}
        row = {"id": item_id, "count": value["count"], "confidence": value["confidence"], "frame": value["frame"],
               "observed_at": value.get("observed_at"), "frames": value.get("frames", 1)}
        for key in ("max", "plus", "indicator_only"):
            if key in value:
                row[key] = value[key]
        if meta.get("kind") == "produce_item":
            row["kind"] = "produce_item"
            row["name"] = meta.get("name")
            row["look"] = meta.get("look")
            row["icon"] = f"game-assets/item-scan/{item_id}.png"
        rows.append(row)
    return rows


def merge_module(old, new):
    if not old:
        return new
    items = {i["id"]: i for i in old.get("items", [])}
    for item in new.get("items", []):
        cur = items.get(item["id"])
        if cur is None or (item.get("observed_at") or "") >= (cur.get("observed_at") or ""):
            items[item["id"]] = item
    cards = {c["id"]: c for c in old.get("cards", [])}
    for card in new.get("cards", []):
        cur = cards.get(card["id"])
        if cur is None or (card.get("observed_at") or "") >= (cur.get("observed_at") or ""):
            cards[card["id"]] = card
    return {**new, "frames": [*old.get("frames", []), *new["frames"]][-MAX_FRAMES:],
            "items": sorted(items.values(), key=lambda i: i["id"]), "cards": sorted(cards.values(), key=lambda c: c["id"])}


def write_icons(rows):
    """UI icons for scanned Produce items: the private template tile, fitted to 64 px (gitignored folder only)."""
    from PIL import Image

    if not is_gitignored(ICON_DIR / "x.png"):
        raise ValueError("ui/game-assets/ 未被 .gitignore 忽略，拒绝写入")
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if row.get("kind") != "produce_item":
            continue
        source = TEMPLATE_DIR / f"{row['id']}.png"
        if not source.is_file():
            row.pop("icon", None)
            continue
        with Image.open(source) as image:
            art = image.convert("RGB").crop((6, 4, 102, 84)).resize((64, 53), Image.LANCZOS)
        art.save(ICON_DIR / f"{row['id']}.png")


def write_module(data, out_file=OUT_FILE):
    out_file = Path(out_file).resolve()
    if out_file != OUT_FILE.resolve():
        raise ValueError(f"只能写到 {OUT_FILE.relative_to(ROOT).as_posix()}")
    if not is_gitignored(out_file):
        raise ValueError("ui/game-assets/ 未被 .gitignore 忽略，拒绝写入")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    out_file.write_text("// Generated by tools/item_inventory_scan.py; local, gitignored. Observations for display only.\n"
                        f"export default {text};\n", encoding="utf-8", newline="\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("frames", nargs="*", help="saved 1280x720 PNG frames")
    parser.add_argument("--layout", choices=[*sorted(ii.LAYOUTS), *CARD_LAYOUTS, ITEM_LIST], default="produce_item_select")
    parser.add_argument("--build-templates", action="store_true", help="crop labelled reference tiles (private)")
    parser.add_argument("--no-ocr", action="store_true", help="skip OCR (every count is then unread -> refused)")
    parser.add_argument("--no-atlas", action="store_true", help="do not add pinned atlas tiles as references")
    parser.add_argument("--truth", help="private truth JSON {file: {slot: [item_id, count]}} -> accuracy report")
    parser.add_argument("--card-import", action="store_true", help="card layouts: also merge into inventory-scan.js")
    parser.add_argument("--dry-run", action="store_true", help="print the result; write nothing")
    parser.add_argument("--append", action="store_true", help="merge into the existing item-scan.js")
    args = parser.parse_args(argv)
    if args.build_templates:
        report = build_templates()
        sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if not args.frames:
            return 0
    if not args.frames:
        parser.error("需要帧文件")
    if len(args.frames) > MAX_FRAMES:
        parser.error(f"最多 {MAX_FRAMES} 帧")
    out = {"version": 1, "generated_by": "tools/item_inventory_scan.py", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "frames": [], "items": [], "cards": []}
    summary = {}
    try:
        if args.layout in CARD_LAYOUTS:
            cards = scan_cards(args.frames, args.layout.split(":", 1)[1])
            out["frames"], out["cards"] = cards["frames"], sorted(cards["cards"].values(), key=lambda c: c["id"])
            if args.card_import and not args.dry_run:
                from tools import card_inventory_scan as cis
                cis.write_module(cis.merge(cis.read_module(), cards["card_scan"]))
                summary["card_import"] = cis.OUT_FILE.relative_to(ROOT).as_posix()
        elif args.layout == ITEM_LIST:
            ocr = None if args.no_ocr else MaaOcr()
            scanned = scan_item_list(args.frames, ocr)
            out["frames"], out["items"] = scanned["frames"], item_rows(scanned["items"], None)
            if ocr is not None:
                summary["input_attempts"] = ocr.input_attempts
        else:
            index = load_index(with_atlas=not args.no_atlas) if args.layout != "home_counters" else None
            ocr = None if args.no_ocr else MaaOcr()
            scanned = scan_items(args.frames, args.layout, index, ocr)
            out["frames"], out["items"] = scanned["frames"], item_rows(scanned["items"], index)
            if args.truth:
                truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
                summary["accuracy"] = evaluate(scanned["results"], args.frames, truth)
            if ocr is not None:
                summary["input_attempts"] = ocr.input_attempts
        if args.append:
            out = merge_module(read_module(), out)
        if not args.dry_run:
            write_icons(out["items"])
            write_module(out)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    summary.update({"items": {i["id"]: i["count"] for i in out["items"]}, "cards": len(out["cards"]), "frames": out["frames"],
                    "written": None if args.dry_run else OUT_FILE.relative_to(ROOT).as_posix()})
    sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
