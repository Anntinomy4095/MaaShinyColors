#!/usr/bin/env python3
"""Show or resolve the W.I.N.G. プロデュース開始 spend ledger (agent.core.wing_ledger; private, no game input).

``list`` prints every entry; ``resolve KEY --as spent|not_spent --note TEXT`` records the user's own check of the
game (stamina / Produce in progress) for an unresolved entry. Until then every W.I.N.G. run is forced to ``attach``
without spend authority. Nothing here touches the game or retries a spend.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_ledger as wl  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W.I.N.G. 开新一局体力消耗账本（只读游戏外记录）")
    parser.add_argument("--ledger", type=Path, default=wl.DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("key")
    resolve.add_argument("--as", dest="outcome", choices=("spent", "not_spent"), required=True,
                         help="你在游戏里核对的结果：spent=体力已扣/已在培养中；not_spent=体力未扣、没有培养")
    resolve.add_argument("--note", default="")
    args = parser.parse_args(argv)
    ledger = wl.SpendLedger(args.ledger)
    if args.command == "list":
        print(json.dumps(ledger.load(), ensure_ascii=True, indent=1))
        return 0
    entry = ledger.get(args.key)
    if entry is None:
        print(json.dumps({"error": "unknown key"}))
        return 2
    if entry["state"] not in wl.PENDING:
        print(json.dumps({"error": "already resolved", "state": entry["state"]}))
        return 2
    ledger.mark(args.key, "resolved_by_user", f"user:{args.outcome}:{args.note}"[:300])
    print(json.dumps({"resolved": args.key, "as": args.outcome}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
