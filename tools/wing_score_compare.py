"""Offline comparison: W.I.N.G. scorer / policy choices vs. a hand-written timeline of a user's run.

No game, host, input or network. Reads a timeline JSON:

    {"version": 1, "primary": "Vo", "strategy": "vo_score", "source": "...",
     "entries": [{"id": "s2w1", "frames": ["#325"], "state": {<WingState JSON>},
                  "user_choice": "<option or audition id>", "choice_certain": true,
                  "assumptions": ["..."], "note": "..."}]}

For every entry it runs ``wing_score.score_week`` and ``wing_policy.decide`` and reports whether the top choice
equals the user's choice, the rank of the user's choice in the scorer ranking, and whether the entry is
"contested" (the user's choice and at least one alternative were both scorable). Agreement rates count only
``choice_certain`` entries. Usage:

    .venv\\Scripts\\python.exe tools\\wing_score_compare.py dev_assets\\private\\wing-demo-analysis\\timeline.json [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core import wing_policy as wp  # noqa: E402
from agent.core import wing_score as wsc  # noqa: E402
from agent.core import wing_state as ws  # noqa: E402


def compare(timeline: dict, score_params: dict | None = None) -> dict:
    if timeline.get("version") != 1:
        raise ValueError("timeline version 必须为 1")
    primary = timeline.get("primary", "Vo")
    strategy = timeline.get("strategy", "vo_score")
    rows = []
    for entry in timeline["entries"]:
        state = ws.WingState.from_dict(entry["state"])
        result = wsc.score_week(state, primary, params=score_params)
        decision = wp.decide(state, strategy, params={"score_params": score_params} if score_params else None)
        user = entry.get("user_choice")
        ranked = [c.id for c in result.ranked]
        policy_pick = decision.audition_id or decision.option_id or decision.kind
        rows.append({
            "id": entry["id"], "frames": entry.get("frames", []), "user_choice": user,
            "certain": bool(entry.get("choice_certain", False)),
            "scorer_rule": result.rule, "scorer_choice": result.choice.id if result.choice else None,
            "policy_kind": decision.kind, "policy_rule": decision.rule, "policy_choice": policy_pick,
            "user_rank": ranked.index(user) + 1 if user in ranked else None,
            "user_excluded": next((list(c.notes) for c in result.excluded if c.id == user), None),
            "contested": user in ranked and len(ranked) >= 2,
            "scorer_match": result.choice is not None and result.choice.id == user,
            "policy_match": policy_pick == user,
            "top3": [(c.id, round(c.score, 1)) for c in result.ranked[:3]],
            "assumptions": entry.get("assumptions", []), "note": entry.get("note", ""),
        })
    certain = [r for r in rows if r["certain"]]
    contested = [r for r in certain if r["contested"]]

    def rate(items, key):
        return None if not items else round(sum(r[key] for r in items) / len(items), 3)

    return {"entries": rows,
            "summary": {"entries": len(rows), "certain": len(certain), "contested_certain": len(contested),
                        "scorer_agreement_certain": rate(certain, "scorer_match"),
                        "policy_agreement_certain": rate(certain, "policy_match"),
                        "scorer_agreement_contested": rate(contested, "scorer_match"),
                        "policy_agreement_contested": rate(contested, "policy_match")}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("timeline", type=Path)
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    parser.add_argument("--score-params", type=Path, help="JSON file with ScoreParams overrides")
    args = parser.parse_args(argv)
    timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
    params = json.loads(args.score_params.read_text(encoding="utf-8")) if args.score_params else None
    report = compare(timeline, params)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for r in report["entries"]:
        flag = "=" if r["scorer_match"] else "x"
        cert = "" if r["certain"] else " (uncertain)"
        print(f"{flag} {r['id']:<8} user={r['user_choice']}{cert} scorer={r['scorer_choice']} [{r['scorer_rule']}] "
              f"policy={r['policy_choice']} [{r['policy_rule']}] rank={r['user_rank']} top3={r['top3']}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
