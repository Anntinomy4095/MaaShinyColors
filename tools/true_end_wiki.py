"""显式公开Wiki读取或离线卡页解析；只输出结构化JSON。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.core.true_end.wiki import CardDatabase, SnapshotManager, parse_card_page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--html", type=Path, help="离线HTML；默认不发网络请求")
    parser.add_argument("--fetch", action="store_true", help="仅日文wikiwiki.jp：显式读取公开页面，遵守robots/限速；英文页请用--html")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "dev_assets/private/true_end/wiki_cache")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("P", "S"), help="可选；由英文卡页URL或日文卡章节推断，证据不足须明确指定")
    parser.add_argument("--rarity")
    parser.add_argument("--card-id", help="人工审核的内部卡ID映射；默认Wiki页面标题")
    parser.add_argument("--database", type=Path, action="append", default=[], help="合并既有结构化卡库，可重复")
    parser.add_argument("--overrides", type=Path, help="JSON数组，每项card_id/field/value/source_url/reason/reviewed_at")
    args = parser.parse_args()
    if bool(args.html) == args.fetch:
        parser.error("必须且只能选择--html或--fetch")
    fetched_at = None
    if args.fetch:
        if urlsplit(args.url).hostname == "shinycolors.wiki":
            parser.error("英文shinycolors.wiki抓取尚未支持，请提供已有公开HTML缓存：--html")
        snapshot = SnapshotManager(args.cache_dir).fetch(args.url, allow_network=True)
        html = (args.cache_dir / snapshot["file"]).read_bytes().decode("utf-8")
        fetched_at = snapshot["fetched_at"]
    else:
        html = args.html.read_bytes().decode("utf-8")
    db = CardDatabase()
    for path in args.database:
        db.merge(path)
    db.add(parse_card_page(html, source_url=args.url, card_id=args.card_id, kind=args.kind, rarity=args.rarity, fetched_at=fetched_at))
    if args.overrides:
        for override in json.loads(args.overrides.read_text(encoding="utf-8")):
            db.override(**override)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    db.export(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
