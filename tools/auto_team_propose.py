#!/usr/bin/env python3
"""Offline 自动编队 proposal: ``produce_config.auto_team_plan`` over 「持有卡牌」 — the True End recommender
(``agent/core/team_recommend.py``; needs the private card database, ``card_id_map.json`` and per-card Lv/凸 in
``prefs.owned_cards.details``), falling back to ``wing_team.select_team`` with a stated ``fallback_reason``.

Reads only local files: the owned-card list (a UI profile JSON with ``prefs.owned_cards`` or a plain
``{"p": [...], "s": [...]}`` file) and optional per-card features (``--features``: ``{card_id: {level, te_flag,
live_skill_attr, ...}}``; plus rarity from the local wiki manifest ``ui/game-assets/manifest.json`` when present).
Unknown features stay unknown: with ids only, the fallback ``select_team`` rejects (levels / TE flags unread) and nothing is
proposed. Writes ``ui/game-assets/team-proposal.js`` (gitignored, CSP-safe ES module) that the Produce team field
shows in 自动编队 mode; the run stays refused (``team_needs_confirmation``) until the user accepts the proposal into the
slots in the UI. No device, no input, no network.

    python tools/auto_team_propose.py --owned PROFILE_OR_OWNED.json [--features FEATURES.json] [--primary Vo] [--dry-run]
    python tools/auto_team_propose.py --owned-snapshot dev_assets/private/shinycolorsdb/snapshot-20261006-owned         [--assume-lb4-cap] [--assume-p-te]

Card library: the whole local library (``team_recommend.load_library``: every ShinyColorsDB snapshot's
converted / merged cards, newest manifest first, plus the reviewed Wiki cards) and every owned ID. The output lists each
owned card's missing data per role (``card_gaps``: unknown vs unresolved). ``--assume-lb4-cap`` / ``--assume-p-te``
fill unknown Lv/凸 or P TE eligibility with an explicit, labelled assumption; both are off by default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import produce_config as pc  # noqa: E402

OUT_FILE = ROOT / "ui" / "game-assets" / "team-proposal.js"
MANIFEST = ROOT / "ui" / "game-assets" / "manifest.json"


def load_owned(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("prefs"), dict):
        data = data["prefs"].get("owned_cards") or {}
    if not isinstance(data, dict):
        raise ValueError("持有卡牌文件格式错误")
    owned = {"p": list(data.get("p") or []), "s": list(data.get("s") or [])}
    if isinstance(data.get("details"), dict):
        owned["details"] = data["details"]   # per-card Lv/凸; validated by team_recommend.clean_details
    return owned


def load_features(path: Path | None, manifest: Path | None = MANIFEST) -> dict:
    features: dict = {}
    if manifest is not None and Path(manifest).is_file():
        doc = json.loads(Path(manifest).read_text(encoding="utf-8"))
        for row in (doc.get("cards") or []) + (doc.get("p_cards") or []):
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("rarity") in ("UR", "SSR", "SR", "R"):
                features.setdefault(row["id"], {})["rarity"] = row["rarity"]
    if path is not None:
        extra = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(extra, dict):
            raise ValueError("--features 必须是 {card_id: {...}}")
        for card, value in extra.items():
            if isinstance(value, dict):
                features.setdefault(card, {}).update(value)
    return features


def _delta_text(delta: dict, sp: float, sign: int) -> str:
    parts = [f"{k} {v:+.0f}" for k, v in (delta or {}).items() if sign * v >= 1]
    if sign * (sp or 0) >= 1:
        parts.append(f"SP {sp:+.0f}")
    return "、".join(parts)[:60]


def alternatives(candidates: list, main: dict | None) -> list:
    """「其他推荐」 for the UI (cleanAlternatives): the recommender's other teams (different card sets, 2026-10-14) with a
    short label, the projected gain / loss against the main team and the per-card tags."""
    out = []
    if not main:
        return out
    base = set(main.get("s_cards") or ()) | {main.get("guest")}
    for c in candidates[1:3]:
        team = {"p_card": c.get("p_card"), "s_cards": list(c.get("starters") or ()), "guest": c.get("guest")}
        if len(team["s_cards"]) != 4 or not team["guest"]:
            continue
        added = [k for k in (*team["s_cards"], team["guest"]) if k not in base]
        guest_only = set(team["s_cards"]) == set(main.get("s_cards") or ()) and team["guest"] != main.get("guest")
        t = c.get("tradeoffs") or {}
        out.append({"id": c.get("id"), "label": "换 Guest" if guest_only else f"换 {len(added) or 1} 张 S 卡",
                    "feature": _delta_text(t.get("stat_delta"), t.get("sp_delta"), 1),
                    "tradeoff": _delta_text(t.get("stat_delta"), t.get("sp_delta"), -1),
                    "primary": c.get("primary"), "rating": c.get("rating"), "proposal": team,
                    "card_tags": c.get("card_tags") or {}, "style": c.get("style")})
    return out


def propose(owned: dict, features: dict, primary: str, recommender: dict | None = None, p_card: str | None = None) -> dict:
    """``p_card`` = a P card id (only its teams), "auto" (推荐编队: Maa picks the P card) or None (legacy: best of the
    first owned P cards). ``primary`` = Vo / Da / Vi, or "auto" with ``p_card="auto"`` (each P card's specialty)."""
    team = {"mode": "auto", "primary_stat": primary, **({"p_card": p_card} if p_card else {})}
    plan = pc.auto_team_plan(team, owned, features=features, recommender=recommender)
    if owned.get("source"):
        plan.setdefault("notes", []).insert(0, f"持有卡来源：{owned['source']}")
    digest = hashlib.sha256(json.dumps(owned, sort_keys=True).encode()).hexdigest()[:16]
    counts: dict = {}
    for value in (plan.get("coverage") or {}).values():
        key = value.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return {"version": 1, "generated_by": "tools/auto_team_propose.py",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "owned_digest": digest,
            "requested_p_card": p_card or None, "requested_primary": primary,
            "primary": plan.get("primary", primary), "status": plan.get("status"), "reason": plan["reason"],
            "confidence": plan.get("confidence"), "reasons": plan.get("reasons", []), "proposal": plan.get("proposal"),
            "engine": plan.get("engine"), "fallback_reason": plan.get("fallback_reason"),
            "recommender": plan.get("recommender"), "coverage_counts": counts,
            "candidates": plan.get("candidates", []),
            "candidate_id": (plan.get("candidates") or [{}])[0].get("id"),
            "card_tags": (plan.get("candidates") or [{}])[0].get("card_tags") or {},
            "style": (plan.get("candidates") or [{}])[0].get("style"),
            "alternatives": alternatives(plan.get("candidates") or [], plan.get("proposal")),
            "training_hints": plan.get("training_hints", []),
            "needs_parsing": plan.get("needs_parsing", []), "notes": plan.get("notes", []),
            "card_gaps": plan.get("card_gaps", []), "gap_counts": plan.get("gap_counts", {}),
            "library": plan.get("library"), "assume": plan.get("assume", {}),
            # the owned list this proposal was computed from (may differ from the UI profile, e.g. --owned-snapshot)
            "owned_counts": {"p": len(owned.get("p") or []), "s": len(owned.get("s") or []),
                             "details": len(owned.get("details") or {})},
            "owned_source": str(owned.get("source") or "profile")[:120]}


def print_progress(done: int, total: int) -> None:
    """One stdout line per recommender step; UiTeamPropose reads it into status.update.team_propose.progress."""
    print(json.dumps({"progress": [done, total]}), flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="自动编队提案（离线，只写本地 UI 文件，不授权运行）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--owned", type=Path, help="UI profile (prefs.owned_cards) or {p, s, details}")
    source.add_argument("--owned-snapshot", type=Path,
                        help="ShinyColorsDB snapshot folder whose manifest lists the owned IDs (e.g. *-owned)")
    parser.add_argument("--assume-lb4-cap", action="store_true",
                        help="unknown Lv/凸 -> 4 凸 + that cap's level (labelled ASSUMED_LB4_CAP)")
    parser.add_argument("--assume-p-te", action="store_true", help="P cards with unknown TE eligibility -> eligible")
    parser.add_argument("--use-recognised-levels", action="store_true",
                        help="Lv/凸 recognised from the in-game list (ui/game-assets/owned-levels.js) for cards without "
                             "user details; labelled RECOGNISED_NOT_USER_CONFIRMED (an assumption)")
    parser.add_argument("--features", type=Path)
    parser.add_argument("--primary", choices=(*pc.PRIMARY_STATS, "auto"), default="Vo",
                        help="主属性; auto (with --p-card): the P card's own specialty")
    parser.add_argument("--p-card", help="fix the P card (10-digit id): only teams for this P are proposed; "
                                         "auto = 推荐编队, Maa picks the P card among the owned ones")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress", action="store_true",
                        help='print {"progress": [done, total]} lines while comparing P cards (the host\'s 推荐进度条)')
    args = parser.parse_args(argv)
    if args.primary == "auto" and not args.p_card:
        parser.error("--primary auto 需要 --p-card（指定 P 卡或 auto）")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.owned_snapshot:
        from agent.core.team_recommend import owned_from_snapshot
        owned = {**owned_from_snapshot(args.owned_snapshot), "source": f"snapshot manifest {args.owned_snapshot.name}"}
    else:
        owned = load_owned(args.owned)
    assume = {"lb4_cap": args.assume_lb4_cap, "p_te_eligible": args.assume_p_te,
              "recognised_levels": args.use_recognised_levels}
    recommender = {"assume": assume, **({"progress": print_progress} if args.progress else {})}
    result = propose(owned, load_features(args.features), args.primary, recommender=recommender, p_card=args.p_card)
    print(json.dumps({k: result[k] for k in ("engine", "fallback_reason", "status", "reason", "confidence", "proposal",
                                             "coverage_counts", "gap_counts", "assume")}, ensure_ascii=False))
    if not args.dry_run:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text("// Generated by tools/auto_team_propose.py; local, gitignored. A proposal for the user to "
                            "confirm; never a run authorisation.\nexport default "
                            + json.dumps(result, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
