#!/usr/bin/env python3
"""Derive the offline Shop replay frames (B4) from private native captures; offline, writes only dev_assets/private.

The live sequence 2026-10-02 (home → プレミアム → マニー list → 交換確認 → 交換結果) exists as real frames, but the
list right after closing 交換結果 was never captured. This tool composes it and the negative variants:

- ``list-after-rank1.png``: the real list with card 1 removed (cards 2.. shifted left by 270 px, the back key and the
  right arrow kept in place) and the top-bar balance taken from the real 交換結果 frame (17,678,482).
- ``list-grey-button1.png``: the real list with position 1's price button replaced by a real disabled (grey) button.
- ``confirm-wrong-currency.png``: the real 交換確認 with 「マニー」 in the prompt overdrawn as 「ジュエル」.
- ``confirm-name-mismatch.png``: the real 交換確認 with the item name overdrawn as another item's name.

Synthetic frames are evidence for the decision logic only; they never replace live samples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
OUTPUT = PRIVATE / "shop-b4-replay"
LIST = PRIVATE / "shop-20261002-03-open1-frames" / "frame-001.png"
CONFIRM = PRIVATE / "shop-20261002-04-confirm1-frames" / "frame-001.png"
RESULT = PRIVATE / "shop-20261002-04-confirm1-frames" / "frame-024.png"
AFTER_TWO = PRIVATE / "manual-capture-kept-20261002" / "shop-after-two-bought-20261002.png"
FONT = Path("C:/Windows/Fonts/YuGothB.ttc")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    from PIL import Image
    with Image.open(path) as image:
        return image.convert("RGB")


def overdraw(image, box, fill_from, text, font_size, origin, colour):
    """Paint ``box`` with the colour sampled at ``fill_from`` and draw ``text`` at ``origin``."""
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, fill=image.getpixel(fill_from))
    draw.text(origin, text, font=ImageFont.truetype(str(FONT), font_size), fill=colour)
    return image


def build(output=OUTPUT):
    output = Path(output).resolve()
    if not output.is_relative_to(PRIVATE.resolve()):
        raise ValueError("输出目录必须位于 dev_assets/private")
    for source in (LIST, CONFIRM, RESULT, AFTER_TWO):
        if not source.is_file():
            raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)
    frames = {}

    base = load(LIST)
    shifted = base.copy()
    shifted.paste(base.crop((308, 222, 1250, 664)), (38, 222))
    shifted.paste(base.crop((10, 618, 104, 704)), (10, 618))      # back key drawn over card 1
    shifted.paste(base.crop((1196, 350, 1280, 450)), (1196, 350))  # scroll arrow
    shifted.paste(load(RESULT).crop((950, 16, 1192, 66)), (950, 16))  # balance after the exchange
    frames["list-after-rank1.png"] = shifted

    grey = base.copy()
    grey.paste(load(AFTER_TWO).crop((598, 562, 816, 652)), (58, 562))
    grey.paste(base.crop((10, 618, 104, 704)), (10, 618))
    frames["list-grey-button1.png"] = grey

    confirm = load(CONFIRM)
    frames["confirm-wrong-currency.png"] = overdraw(confirm.copy(), (590, 134, 690, 160), (585, 147), "ジュエル", 21,
                                                   (602, 133), (90, 82, 104))
    frames["confirm-name-mismatch.png"] = overdraw(confirm.copy(), (518, 236, 812, 284), (515, 230),
                                                  "ゴールドトレーニングチケット", 20, (524, 246), (90, 82, 104))
    records = []
    for name, image in frames.items():
        path = output / name
        image.save(path, format="PNG")
        records.append({"image": name, "sha256": sha256(path)})
    manifest = {"generated_by": "tools/build_shop_replay_frames.py", "synthetic": True,
                "sources": {str(p.relative_to(ROOT)): sha256(p) for p in (LIST, CONFIRM, RESULT, AFTER_TWO)},
                "frames": records}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 Shop 离线回放用的合成帧（只写 dev_assets/private）。")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    try:
        manifest = build(args.output)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"passed": True, "frames": [r["image"] for r in manifest["frames"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
