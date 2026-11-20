#!/usr/bin/env python3
"""重建 10 位 Wiki 卡 ID → True End 卡 ID（wikiwiki 页面标题）的精确映射（离线，只读本地文件）。

    python tools/card_id_map_build.py [--list PATH ...] [--database PATH ...] [--output PATH] [--check]

默认读取 dev_assets/private/game-assets-src/wiki-{support,produce}/cards.json 与
agent/core/team_recommend.DEFAULT_DATABASES 中存在的已解析卡库，写入 dev_assets/private/true_end/card_id_map.json
（由 Wiki 数据派生，只放 private）。只有【标题】偶像名逐字相等、P/S 与偶像一致且唯一时才映射；其余保持未映射，
并给出待解析的 wikiwiki 标题（candidate_title，不是映射）。不联网、不连接游戏。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import card_id_map  # noqa: E402
from agent.core.team_recommend import DEFAULT_DATABASES  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", type=Path, action="append", help="本地 wiki 卡牌列表 cards.json（可重复）")
    parser.add_argument("--database", type=Path, action="append", help="已解析 True End 卡库 JSON（可重复）")
    parser.add_argument("--output", type=Path, default=card_id_map.DEFAULT_MAP)
    parser.add_argument("--check", action="store_true", help="只比较现有文件是否最新，不写入")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lists = args.list or [p for p in card_id_map.DEFAULT_LISTS if p.is_file()]
    databases = args.database or [p for p in DEFAULT_DATABASES if p.is_file()]
    if not lists:
        print("没有本地 wiki 卡牌列表：先运行 tools/ui_game_assets_fetch.py", file=sys.stderr)
        return 2
    try:
        document = card_id_map.build_from_files(lists, databases)
    except (ValueError, OSError) as error:
        print(f"映射未生成：{error}", file=sys.stderr)
        return 2
    text = json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        print("映射已是最新" if current == text else "映射已过期：重新运行本工具")
        return 0 if current == text else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    reasons: dict[str, int] = {}
    for entry in document["unmapped"].values():
        reasons[entry["reason"]] = reasons.get(entry["reason"], 0) + 1
    print(json.dumps({"output": str(args.output), **document["counts"], "unmapped_reasons": reasons}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
