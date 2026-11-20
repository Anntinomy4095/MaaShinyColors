#!/usr/bin/env python3
"""Supervised live session helper for 推荐编队 (W.I.N.G. team_edit): which picker targets are verified, read-only
geometry proof on captured frames, and the reviewed switch that enables a target after its live check.

The picker tile / wheel targets (``agent/core/wing_team_edit.py``: three tap phases x 7 columns, ``picker_scroll_*``)
ship disabled except the four tiles with a clean tap pair in captures-20261011-164549Z. A target becomes tappable
only when ``assets/input/wing_picker_verified.json`` names it with evidence, written by this tool with ``--apply``,
followed by ``tools/wing_nav_build.py --enable-actions`` (done here) and a host action-table reload. No device, no
input; ``--prove`` only reads PNG frames.

    python tools/wing_picker_enable.py --list
    python tools/wing_picker_enable.py --prove dev_assets/private/<session>/frame-000123.png
    python tools/wing_picker_enable.py --verify picker_scroll_down="live 20261015 #12->#13 列表下移 1 行余重叠" --apply
    python tools/wing_picker_enable.py --revoke picker_tile_b_r2c3 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_team_edit as wte  # noqa: E402

RECORDED = {wte.tile_intent(r, c) for r, c in wte.VERIFIED_TILES} | set(wte.SORT_RECORDED)
# 2026-10-14 picker sort targets (agent/core/picker_sort.py): sort button, 並び替え key cells, 決定.
SORT_TARGETS = (wte.SORT_OPEN_INTENT,) + wte.SORT_POPUP_INTENTS


def status(path: Path = wte.VERIFIED_FILE) -> list[dict]:
    """Every picker target with its rect, phase and evidence (None = not verified)."""
    evidence = wte.load_verified(path)
    rows = []
    for phase, (offset, size, n_rows) in wte.PHASES.items():
        for r in range(1, n_rows + 1):
            for c in range(1, wte.GRID_COLS + 1):
                intent = wte.tile_intent(r, c, phase)
                rows.append({"intent": intent, "phase": phase or "top", "offset": offset, "rect": wte.tile_rect(r, c, phase),
                             "evidence": evidence.get(intent)})
    for intent in wte.SCROLL_INTENTS.values():
        rows.append({"intent": intent, "phase": "wheel", "offset": None, "rect": wte.SCROLL_RECT,
                     "wheel": wte.SCROLL_WHEEL[intent], "evidence": evidence.get(intent)})
    from agent.core import picker_sort as ps
    labels = {cell: key for key, cell in ps.KEY_CELLS.items()}
    for intent in SORT_TARGETS:
        if intent == wte.SORT_OPEN_INTENT:
            rect, phase = ps.SORT_OPEN_RECT, "sort"
        elif intent == wte.SORT_APPLY_INTENT:
            rect, phase = ps.SORT_APPLY_RECT, "sort"
        else:
            cell = (int(intent[-3]), int(intent[-1]))
            rect, phase = ps.cell_rect(*cell), labels[cell]
        rows.append({"intent": intent, "phase": phase, "offset": None, "rect": rect, "evidence": evidence.get(intent)})
    return rows


def prove(frame_path: Path) -> dict:
    """Read-only: the picker reading of one frame and, for its grid offset, every tile target whose rect lies inside
    a tile read on that frame (the geometric half of a target's proof; the other half is a tap -> result pair)."""
    import numpy as np
    from PIL import Image
    from agent.core import wing_screen as wsc

    image = np.asarray(Image.open(frame_path).convert("RGB"))
    reading = wsc.read_picker(image)
    offset = reading.get("picker_offset")
    tiles = reading.get("picker_tiles") or []
    fits = []
    if offset is not None:
        for t in tiles:
            try:
                row, col = (int(x) for x in str(t.get("slot") or "")[1:].split("c"))
            except ValueError:
                continue
            for phase in wte.phase_for(offset, row):
                fits.append({"intent": wte.tile_intent(row, col, phase), "card": t.get("card_id"), "status": t.get("status")})
    return {"frame": str(frame_path), "offset": offset, "thumb_top": reading.get("picker_thumb_top"),
            "at_bottom": reading.get("picker_at_bottom"), "scrollbar": reading.get("picker_scrollbar"),
            "tiles_read": len(tiles), "targets_inside_tiles": fits}


def write(changes: dict[str, str | None], path: Path = wte.VERIFIED_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"version": 1, "verified": {}}
    verified = dict(data.get("verified") or {})
    for intent, evidence in changes.items():
        if intent not in wte.PICKER_INTENTS and intent not in wte.SORT_POPUP_INTENTS:
            raise ValueError(f"不是选卡目标：{intent}")
        if intent in RECORDED:
            raise ValueError(f"{intent} 已由录制验证（内置），不需要写入")
        if evidence is None:
            verified.pop(intent, None)
        else:
            if not evidence.strip():
                raise ValueError(f"{intent} 缺少证据（帧号与点击前后对）")
            verified[intent] = evidence.strip()
    data["verified"] = dict(sorted(verified.items()))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8", newline="\n")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="推荐编队选卡目标：列表 / 只读帧核对 / 经实机核对后启用（不发送输入）")
    parser.add_argument("--list", action="store_true", help="列出全部选卡目标与证据")
    parser.add_argument("--prove", type=Path, action="append", default=[], help="只读：在截图上核对哪些目标位于识别到的卡格内")
    parser.add_argument("--verify", action="append", default=[], metavar="INTENT=EVIDENCE", help="记录一项实机核对（需 --apply）")
    parser.add_argument("--revoke", action="append", default=[], metavar="INTENT", help="撤销一项（需 --apply）")
    parser.add_argument("--apply", action="store_true", help="写入 wing_picker_verified.json 并重新生成 wing.json 与动作表")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.list:
        for row in status():
            mark = "verified" if row["evidence"] else "pending "
            extra = f" wheel {row['wheel']}" if "wheel" in row else (f" offset {row['offset']}" if row["offset"] else "")
            print(f"{mark} {row['intent']:<26} {row['phase']:<5} rect {list(row['rect'])}{extra}  {row['evidence'] or ''}")
    for frame in args.prove:
        print(json.dumps(prove(frame), ensure_ascii=False))
    changes: dict[str, str | None] = {}
    for item in args.verify:
        intent, sep, evidence = item.partition("=")
        if not sep:
            parser.error(f"--verify 需要 INTENT=EVIDENCE：{item}")
        changes[intent.strip()] = evidence
    for intent in args.revoke:
        changes[intent.strip()] = None
    if changes and not args.apply:
        print(f"未写入（加 --apply）：{json.dumps(changes, ensure_ascii=False)}")
    elif changes:
        write(changes)
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "tools" / "wing_nav_build.py"), "--enable-actions"], check=True, cwd=ROOT)
        print("已写入；在宿主「重新载入动作表」或重启宿主后生效。提交前确认证据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
