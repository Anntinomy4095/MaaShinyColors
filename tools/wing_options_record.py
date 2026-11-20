#!/usr/bin/env python3
"""Write the in-game プロデュース設定 record (agent/core/wing_options.py) from recorded オプション frames.

Offline only: reads private PNG frames plus their whole-frame OCR (jsonl rows ``{"seq", "texts"}``), never touches the
game. Default = the supervised recording captures-20261011-164549Z (#299 top, #303 middle, #306 bottom of the list).

    .venv\\Scripts\\python.exe tools\\wing_options_record.py            # print the record (dry run)
    .venv\\Scripts\\python.exe tools\\wing_options_record.py --write    # write dev_assets/private/wing-ui/produce-settings.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_options as wo  # noqa: E402
from agent.core.wing_screen import box_in_roi  # noqa: E402

PRIVATE = ROOT / "dev_assets" / "private"
REC = PRIVATE / "captures-20261011-164549Z"
OCR = PRIVATE / "wing-demo4-analysis" / "ocr.jsonl"
FRAMES = (299, 303, 306)


def read_frames(rec: Path, ocr: Path, frames) -> dict:
    """Merged {key: ON/OFF} of the given frames; a row read differently on two frames is an error."""
    from tools.wing_demo_replay import load_rgb
    texts = {}
    for line in ocr.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("seq") in frames:
            texts[row["seq"]] = [(t, tuple(b)) for t, b in row["texts"]]
    merged: dict = {}
    for n in frames:
        if n not in texts:
            raise SystemExit(f"OCR 中没有帧 #{n}")
        image = load_rgb(rec / f"frame-{n:06d}.png")
        read = wo.read_toggles([tb for tb in texts[n] if box_in_roi(tb[1], wo.LIST_ROI)], image)
        for key, value in read.items():
            if value is None:
                continue
            if merged.get(key, value) != value:
                raise SystemExit(f"{key} 在不同帧读数不一致")
            merged[key] = value
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从录制的 オプション 帧生成 Produce 设置记录（离线）")
    parser.add_argument("--rec", type=Path, default=REC)
    parser.add_argument("--ocr", type=Path, default=OCR)
    parser.add_argument("--frames", type=int, nargs="+", default=list(FRAMES))
    parser.add_argument("--observed-at", default="2026-10-11T16:50:45+00:00",
                        help="帧的拍摄时间（默认：录制开始 16:45:49Z + #299 的 296 s）")
    parser.add_argument("--write", action="store_true", help=f"写入 {wo.SETTINGS_FILE.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    values = read_frames(args.rec, args.ocr, set(args.frames))
    gate = wo.SettingsGate()
    gate.observe(values, source=f"recording {args.rec.name} #" + " #".join(map(str, args.frames)),
                 at=args.observed_at)
    print(json.dumps({"record": gate.record, "status": gate.status()}, ensure_ascii=False, indent=2))
    if args.write:
        wo.SettingsStore().save(gate.record)
        print(f"wrote {wo.SETTINGS_FILE}")
    return 0 if gate.status()["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
