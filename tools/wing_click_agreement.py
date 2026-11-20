#!/usr/bin/env python3
"""W.I.N.G. click agreement: Maa's decision on each recorded frame vs the user's actual click (read-only, zero input).

For every click the recorder logged (second and third demo; the first demo has no click log), the frame right before
the click is classified and decided by the same stack ``tools/wing_demo_replay.py`` replays (Pipeline guards,
readers, ``WingNavRuntime`` with memory carried across the recording, default True End config), and the result is
compared with the user's click:

- ``agree``: Maa would click and the user's point lies inside Maa's target rect (same button);
- ``agree_effect``: Maa would tap a "tap anywhere" target (result text box, 総合評価) and the user tapped elsewhere on
  the same page with the same effect (the after frame is one of the rule's expected screens);
- ``maa_waits``: Maa waits on that screen (commu fast-forward on, cut-ins, results that advance by themselves) while
  the user tapped to hurry it — not a conflict;
- ``maa_stops``: Maa stops there by design (setup in attach mode, SP handover, team edit, item use, unknown page);
- ``disagree``: Maa would click another target than the user;
- ``unknown_screen``: no screen guard hit on the frame (transition / animation): Maa never acts there;
- ``user_detour``: a page a supervised Maa run never opens (skill panel while SP is handed over, setup detail/help).

On スケジュール the sequential replay first surveys the audition tab; the compared choice is then the week decision on
the same frame after that survey (the replay's isolated decision with the auditions the recording shows).

Agreement % = (agree + agree_effect) / (agree + agree_effect + disagree) over the frames where both acted; the report
also lists every disagreement grouped by (screen, Maa intent, user target) with the frame numbers, and the
unrecognised screens by the hand label. Report: ``dev_assets/private/wing-agreement-20261009/agreement.json``.

    .venv\\Scripts\\python.exe tools\\wing_click_agreement.py [--backend boxes|maa] [--clean-only]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_nav as nav  # noqa: E402
from tools import wing_demo_replay as replay  # noqa: E402

OUT = ROOT / "dev_assets" / "private" / "wing-agreement-20261009"
TAP_ANYWHERE = {"tap_textbox", "total_eval_tap", "rank_next", "commu_ok", "eval_next", "result_list_tap"}
# Pages a supervised Maa run never opens (skill learning is handed over, setup/detail/help pages are the user's):
# a user click there is a detour, not a decision Maa would take differently.
USER_DETOUR = {"Review", "ForgetSelect", "LearnConfirm", "ReplaceConfirm", "ReplaceDialog", "Owned", "SupportDetail",
               "IdolDetail", "UnitCheck", "SkillPanelReadOnly", "HelpList", "ItemPopup", "ItemUseConfirm", "Trend",
               "ActiveSkills", "CheerHelp", "Options", "AuditionHelp", "ModeHelp", "ItemHelp", "BonusList"}
RECORDINGS = (("demo2", replay.LABELS_DEMO2), ("demo3", replay.LABELS_DEMO3))


def user_target(screen: str | None, x: int, y: int) -> str:
    """The screen's click intent whose rect holds the user's point (``other`` when none)."""
    if screen is None or screen not in nav.RULES:
        return "other"
    for intent in nav.screen_intents(screen):
        if replay._in_rect(x, y, nav.TARGETS[intent][0]):
            return intent
    for intent, (rect, _) in nav.TARGETS.items():   # a target of another screen (mis-classification hint)
        if replay._in_rect(x, y, rect):
            return "foreign:" + intent
    return "other"


def maa_room(decision: dict) -> str | None:
    """The room the week decision chose (sequential full decision or the replay's isolated brief)."""
    week = (decision.get("detail") or {}).get("decision") or decision.get("scorer") or {}
    return week.get("option_id") if week.get("kind") in ("lesson", "work") else None


def classify_click(row: dict, click: dict, labels: dict, point: dict | None = None) -> dict:
    screen = row.get("screen") if row else None
    decision = (row or {}).get("decision") or {}
    if (screen == "Schedule" and decision.get("reason") == "survey_auditions" and point
            and point.get("isolated", {}).get("action")):
        # Maa surveys the audition list first; the week choice it then makes is the isolated decision of this frame
        # (memory = the auditions the recording shows for this week).
        decision = {**point["isolated"], "survey_first": True}
    after_label = labels.get(str(click["after"]), "")
    target = user_target(screen, click["x"], click["y"])
    out = {"seq": click["before"], "after": click["after"], "x": click["x"], "y": click["y"], "clean": click["clean"],
           "label": labels.get(str(click["before"]), ""), "after_label": after_label, "screen": screen,
           "maa_action": decision.get("action"), "maa_intent": decision.get("intent"),
           "maa_reason": decision.get("reason"), "user_target": target}
    if screen is None:
        out["kind"] = "unknown_screen"
    elif screen in USER_DETOUR:
        out["kind"] = "user_detour"
    elif decision.get("action") == "click":
        rect = nav.TARGETS[decision["intent"]][0]
        room = maa_room(decision)
        out["maa_room"] = room
        if replay._in_rect(click["x"], click["y"], rect):
            out["kind"] = "agree"
        elif (screen == "Schedule" and room and target.startswith("schedule_room_")
              and nav.ROOM_SLOTS[int(target.rsplit("_", 1)[1]) - 1] == room):
            # Same room chosen: the user taps the (already selected) room tile, Maa presses 決定 — same week action.
            out["kind"] = "agree"
        elif decision["intent"] in TAP_ANYWHERE and target not in nav.screen_intents(screen) and (
                click["after"] != click["before"]):
            # Tap-anywhere pages advance on any tap; the user tapped elsewhere and the page moved on.
            out["kind"] = "agree_effect"
        else:
            out["kind"] = "disagree"
    elif decision.get("action") == "wait":
        out["kind"] = "maa_waits"
    else:
        out["kind"] = "maa_stops"
    return out


def analyse(backend: str = "boxes", clean_only: bool = False) -> dict:
    rows_out, per_recording = [], {}
    for name, labels_path in RECORDINGS:
        doc = replay.load_labels(labels_path)
        for seg, sub in replay.segment_docs(doc):
            report = replay.replay_doc(sub, backend)
            frames = {r["seq"]: r for r in report["frames"]}
            points = {p["seq"]: p for p in report["decision_points"]}
            key = name if seg is None else f"{name}-{seg}"
            counts = collections.Counter()
            for click in replay.load_clicks(sub):
                if click["before"] is None or (clean_only and not click["clean"]):
                    continue
                result = classify_click(frames.get(click["before"]), click, sub["labels"], points.get(click["before"]))
                result["recording"] = key
                counts[result["kind"]] += 1
                rows_out.append(result)
            per_recording[key] = dict(counts)
    totals = collections.Counter(r["kind"] for r in rows_out)
    acted = totals["agree"] + totals["agree_effect"] + totals["disagree"]
    groups = collections.defaultdict(list)
    for r in rows_out:
        if r["kind"] == "disagree":
            groups[(r["screen"], r["maa_intent"], r["user_target"])].append(f"{r['recording']}#{r['seq']}")
    stops = collections.Counter((r["screen"], r["maa_reason"]) for r in rows_out if r["kind"] == "maa_stops")
    unknown = collections.Counter(r["label"] for r in rows_out if r["kind"] == "unknown_screen")
    return {
        "readonly": True, "backend": backend, "clean_only": clean_only,
        "clicks": len(rows_out), "kinds": dict(totals),
        "agreement_pct": round(100.0 * (totals["agree"] + totals["agree_effect"]) / acted, 1) if acted else None,
        "per_recording": per_recording,
        "disagreements": [{"screen": s, "maa_intent": m, "user_target": u, "count": len(v), "frames": v[:12]}
                          for (s, m, u), v in sorted(groups.items(), key=lambda kv: -len(kv[1]))],
        "stops": [{"screen": s, "reason": r, "count": n} for (s, r), n in stops.most_common()],
        "unknown_by_label": dict(unknown.most_common()),
        "rows": rows_out,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W.I.N.G. 录制点击一致率（只读，0 输入）")
    parser.add_argument("--backend", choices=("boxes", "maa"), default="boxes")
    parser.add_argument("--clean-only", action="store_true", help="只统计干净的点击前后对")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = analyse(args.backend, args.clean_only)
    out = args.report or OUT / f"agreement-{args.backend}{'-clean' if args.clean_only else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("clicks", "kinds", "agreement_pct", "per_recording")},
                     ensure_ascii=False))
    for row in result["disagreements"][:25]:
        print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({"stops": result["stops"][:20], "unknown_by_label": result["unknown_by_label"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
