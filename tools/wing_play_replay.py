#!/usr/bin/env python3
"""W.I.N.G. SP-learning and Live-play replay on the recorded demos (read-only, zero input).

Measures the 2026-10-09b SP / Live wiring against what the user did in the second and third demo recordings:

SP (振り返り visits = runs of Review / LearnConfirm / ForgetSelect / ReplaceDialog / Owned frames):

- ``entry_canonical``: is the first Review frame of each visit the canonical view Maa acts on;
- ``slot_taps``: every clean user tap on a canonical frame that lands on a node slot -> does the next frame's info
  bar show that slot's cost (the lattice -> node mapping the fixed tap rects rely on);
- ``info_truth``: info-bar / dialog parsers against hand-checked frames (``INFO_TRUTH`` below);
- ``learn_agreement``: for every SP drop (the user learned a node) whose node is identified (LearnConfirm name or the
  info bar showing 習得済み, the last info bar of that cost, or the only inspected node of that cost), the
  ``wing_sp.plan_sp`` first step over the nodes the user had inspected in that visit (same SP) vs the user's choice.
  Stats/caps come from the nearest 所持スキル reading of the recording (none -> caps are not valued). The petal owner
  of an inspected node is unknown here (the user zoomed/panned), so P-card skills rank as ordinary Live skills.

Live (second demo own-play turns, Auto OFF):

- ``phase``: the play phase read on each clean card / judge / timing tap frame;
- ``judge_agreement``: the focus policy's target judge vs the judge the user tapped;
- ``card_agreement``: at the user's judge tap, would the policy play the card the user selected (with the card
  multipliers learned so far from the tooltips the user opened), and the first card tap of each turn;
- ``secured_first``: own-play frames where 1st place was mathematically secured (Auto would take over with the
  user's ``game_auto`` opt-in).

    .venv\\Scripts\\python.exe tools\\wing_play_replay.py [--backend boxes|maa]

Report: ``dev_assets/private/wing-play-20261009/play-<backend>.json`` (private, never committed).
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

from agent.core import wing_live_policy as wlp  # noqa: E402
from agent.core import wing_nav as nav  # noqa: E402
from agent.core import wing_screen as wsc  # noqa: E402
from agent.core import wing_skill_panel as wsp  # noqa: E402
from agent.core import wing_sp  # noqa: E402
from tools import wing_demo_replay as replay  # noqa: E402

OUT = ROOT / "dev_assets" / "private" / "wing-play-20261009"
SKILL_LABELS = {"Review", "LearnConfirm", "ForgetSelect", "ReplaceDialog", "Owned"}
RECORDINGS = (("demo2", replay.LABELS_DEMO2), ("demo3", replay.LABELS_DEMO3))
# Hand-checked info bars (frame image read by eye, 2026-10-09b): (recording, segment, frame) -> expected fields.
INFO_TRUTH = {
    ("demo3", "03", 397): {"kind": "cap_up", "attributes": ("Vo",), "value": 25.0, "cost": 20, "state": "learnable"},
    ("demo3", "03", 402): {"kind": "live_skill", "attributes": ("Vo",), "value": 2.5, "cost": 30, "state": "locked"},
    ("demo3", "03", 424): {"kind": "live_skill", "attributes": ("Vi",), "value": 3.0, "cost": 20, "state": "learnable"},
    ("demo3", "03", 434): {"kind": "live_skill", "attributes": ("Vi",), "value": 3.0, "cost": 0, "state": "learned"},
    ("demo3", "03", 436): {"kind": "passive", "attributes": ("Vo", "Vi"), "cost": 30, "state": "learnable"},
    ("demo3", "03", 500): {"kind": "cap_up", "attributes": ("Vi",), "value": 50.0, "cost": 50, "state": "locked"},
    ("demo3", "03", 548): {"kind": "live_skill", "attributes": ("Vo",), "value": 5.0, "cost": 50, "state": "locked"},
    ("demo3", "03", 561): {"kind": "live_skill", "attributes": ("Vo",), "value": 4.5, "cost": 50, "state": "sp_short"},
    ("demo2", None, 671): {"kind": "live_skill", "attributes": ("Vo",), "value": 3.5, "cost": 20, "state": "learnable"},
    ("demo2", None, 745): {"kind": "live_skill", "attributes": ("Vo",), "value": 5.0, "cost": 50, "state": "learnable"},
    ("demo2", None, 1288): {"kind": "live_skill", "attributes": ("Vo",), "value": 5.0, "cost": 50, "state": "locked"},
    ("demo2", None, 1296): {"kind": "live_skill", "attributes": ("Vo",), "value": 3.5, "cost": 40, "state": "learnable"},
}
DIALOG_TRUTH = {
    ("demo3", "03", 426): {"learn_confirm": {"cost": 20, "sp_before": 86, "sp_after": 66}},
    ("demo2", None, 1304): {"learn_confirm": {"cost": 50, "sp_before": 151, "sp_after": 101}},
    ("demo3", "03", 428): {"forget_multipliers": [1.0, 1.0, 1.0, 0.0]},
    ("demo2", None, 1295): {"forget_multipliers": [3.5, 3.0, 3.5, 5.0]},
    ("demo2", None, 744): {"replace": {"forget": "ボーカルアピール", "new": "DIVEIN!+"}},
}


def frames_of(sub: dict, backend):
    samples = {s["seq"]: s for s in replay.load_samples(sub)}
    evaluator = wsc.Evaluator(wsc.load_nodes(), backend, {"SC.Wing.State": lambda p, f: True})
    cache = {}

    def read(seq, screen):
        key = (seq, screen)
        if key not in cache:
            sample = samples[seq]
            frame = wsc.Frame(sample["texts"], lambda p=sample["file"]: replay.load_rgb(p), key=sample["file"].name)
            cache[key] = wsc.read_screen(screen, evaluator, frame)
        return cache[key]
    return samples, read


def _visits(labels: dict, samples: dict) -> list[list[int]]:
    out, cur = [], []
    for seq in sorted(samples):
        label = labels.get(str(seq), "")
        if label in SKILL_LABELS:
            cur.append(seq)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _slot_at(panel: dict, x: int, y: int):
    best = None
    for intent, slot in (panel.get("slots") or {}).items():
        lx, ly = wsp.slot_label(tuple(slot["slot"]))
        d = ((x - lx) ** 2 + (y - (ly - wsp.HEX_LIFT)) ** 2) ** 0.5
        if d <= 42 and (best is None or d < best[0]):
            best = (d, intent, slot)
    return best


def sp_section(name: str, seg, sub: dict, backend) -> dict:
    labels = sub["labels"]
    samples, read = frames_of(sub, backend)
    visits = _visits(labels, samples)
    entry = collections.Counter()
    entry_rows = []
    canonical_frames = review_frames = 0
    for visit in visits:
        reviews = [s for s in visit if labels[str(s)] == "Review"]
        review_frames += len(reviews)
        canonical_frames += sum(1 for s in reviews if read(s, "Review")["panel"]["canonical"])
        if reviews:
            panel = read(reviews[0], "Review")["panel"]
            entry["canonical" if panel["canonical"] else panel["reason"]] += 1
            entry_rows.append({"frame": reviews[0], "canonical": panel["canonical"], "reason": panel["reason"],
                               "offset": panel["offset"]})
    taps = collections.Counter()
    tap_rows = []
    for click in replay.load_clicks(sub):
        if not click["clean"] or labels.get(str(click["before"])) != "Review":
            continue
        panel = read(click["before"], "Review")["panel"]
        if not panel["canonical"]:
            continue
        hit = _slot_at(panel, click["x"], click["y"])
        if hit is None:
            taps["not_a_slot"] += 1
            continue
        _, intent, slot = hit
        after_label = labels.get(str(click["after"]))
        info = read(click["after"], "Review").get("skill_info") if after_label == "Review" else None
        if info is None:
            kind = "no_info_after"
        elif info.get("cost") is None or slot.get("cost") is None:
            kind = "cost_unread"
        elif info["cost"] == slot["cost"] or (slot["cost"] == 0 and info.get("state") in ("learned", "delete")):
            kind = "agree"
        else:
            kind = "mismatch"
        taps[kind] += 1
        tap_rows.append({"before": click["before"], "after": click["after"], "xy": [click["x"], click["y"]],
                         "slot": intent, "label_cost": slot.get("cost"), "info": info, "kind": kind})
    learn = _learn_agreement(name, seg, visits, labels, read)
    truth = []
    for (rec, s, frame), want in INFO_TRUTH.items():
        if rec != name or s != seg or str(frame) not in labels:
            continue
        got = read(frame, "Review")
        text, info = got.get("skill_text") or {}, got.get("skill_info") or {}
        fields = {"kind": text.get("kind"), "attributes": tuple(text.get("attributes") or ()),
                  "value": text.get("value"), "cost": info.get("cost"), "state": info.get("state")}
        bad = {k: (fields[k], v) for k, v in want.items() if fields[k] != v}
        truth.append({"frame": frame, "ok": not bad, "wrong": bad})
    for (rec, s, frame), want in DIALOG_TRUTH.items():
        if rec != name or s != seg or str(frame) not in labels:
            continue
        label = labels[str(frame)]
        got = read(frame, label)
        if "learn_confirm" in want:
            lc = got.get("learn_confirm") or {}
            bad = {k: (lc.get(k), v) for k, v in want["learn_confirm"].items() if lc.get(k) != v}
        elif "forget_multipliers" in want:
            rows = got.get("forget_rows") or []
            mults = [r.get("multiplier") for r in rows]
            bad = {} if mults == want["forget_multipliers"] else {"multipliers": (mults, want["forget_multipliers"])}
        else:
            rep = got.get("replace") or {}
            bad = {k: (rep.get(k), v) for k, v in want["replace"].items() if not wsp.same_skill(rep.get(k), v)}
        truth.append({"frame": frame, "screen": label, "ok": not bad, "wrong": bad})
    return {"visits": len(visits), "entry": dict(entry), "entry_rows": entry_rows, "review_frames": review_frames,
            "canonical_frames": canonical_frames, "slot_taps": dict(taps), "slot_tap_rows": tap_rows,
            "truth": truth, "learn": learn}


def _learn_agreement(name, seg, visits, labels, read) -> dict:
    rows = []
    owned = []                                   # (frame, stats, caps) of every 所持スキル reading in the recording
    for visit in visits:
        for seq in visit:
            if labels[str(seq)] == "Owned" and read(seq, "Owned").get("owned"):
                got = read(seq, "Owned")["owned"]
                owned.append((seq, {k: v[0] for k, v in got.items()}, {k: v[1] for k, v in got.items()}))
    for visit in visits:
        catalog: dict = {}
        learned: list = []
        skills = None
        near = min(owned, key=lambda o: (o[0] > visit[0], abs(o[0] - visit[0]))) if owned else None
        stats, caps = (near[1], near[2]) if near else (None, None)
        last_sp = None
        last_confirm = None
        last_bar = None                         # the info bar the user looked at last (state learnable)
        for seq in visit:
            label = labels[str(seq)]
            r = read(seq, label)
            if label == "Owned" and r.get("owned"):
                stats = {k: v[0] for k, v in r["owned"].items()}
                caps = {k: v[1] for k, v in r["owned"].items()}
            if label == "ForgetSelect" and r.get("forget_rows"):
                skills = r["forget_rows"]
            if label == "LearnConfirm" and (r.get("learn_confirm") or {}).get("cost"):
                last_confirm = r["learn_confirm"]
            sp = r.get("sp")
            text, info = r.get("skill_text") or {}, r.get("skill_info") or {}
            if label == "Review" and text.get("name") and info.get("state") in ("learnable", "sp_short", "locked"):
                catalog.setdefault(text["name"], {**text, "cost": info.get("cost"), "state": info.get("state")})
                if info.get("state") == "learnable":
                    last_bar = {"name": text["name"], "cost": info.get("cost")}
            if isinstance(sp, int) and isinstance(last_sp, int) and sp < last_sp:
                drop = last_sp - sp
                who = None
                if label == "Review" and info.get("state") in ("learned", "delete") and text.get("name"):
                    who = text["name"]
                elif last_confirm and last_confirm.get("cost") == drop and last_confirm.get("name"):
                    who = last_confirm["name"]
                elif last_bar and last_bar.get("cost") == drop:
                    who = last_bar["name"]          # the node whose 習得する the user had open, same cost
                else:                               # the only inspected, not yet learned node of that cost
                    same = [k for k, e in catalog.items() if e.get("cost") == drop and e.get("state") == "learnable"
                            and not any(wsp.same_skill(k, d) for d in learned)]
                    who = same[0] if len(same) == 1 else None
                last_bar = None
                rows.append(_compare(name, seg, seq, last_sp, drop, who, catalog, learned, skills, stats, caps))
                if who:
                    learned.append(who)
                last_confirm = None
            if isinstance(sp, int):
                last_sp = sp
    counts = collections.Counter(r["kind"] for r in rows)
    comparable = counts["agree"] + counts["disagree"]
    return {"events": len(rows), "kinds": dict(counts),
            "agreement_pct": round(100.0 * counts["agree"] / comparable, 1) if comparable else None, "rows": rows}


def _compare(name, seg, seq, sp_before, drop, who, catalog, learned, skills, stats, caps) -> dict:
    row = {"recording": name if seg is None else f"{name}-{seg}", "frame": seq, "sp_before": sp_before,
           "cost": drop, "user": who}
    if who is None:
        row["kind"] = "user_node_unidentified"
        return row
    nodes = []
    for key, e in catalog.items():
        if any(wsp.same_skill(key, done) for done in learned) or e.get("kind") not in wing_sp.NODE_KINDS:
            continue
        if not isinstance(e.get("cost"), int) or e["cost"] > sp_before or e.get("value") is None:
            continue
        if e.get("state") == "locked":
            continue
        nodes.append(wing_sp.SPNode(key, e["kind"], e["cost"], float(e["value"]), tuple(e.get("attributes") or ())))
    if not any(wsp.same_skill(n.id, who) for n in nodes):
        row["kind"] = "user_node_not_inspected"
        row["inspected"] = [n.id for n in nodes]
        return row
    live = [wing_sp.LiveSkill(s["name"] or "?", s["attributes"][0] if len(s.get("attributes") or ()) == 1 else None,
                              float(s["multiplier"])) for s in (skills or []) if s.get("multiplier") is not None]
    stat = (stats or {}).get("Vo")
    cap = (caps or {}).get("Vo")             # nearest 所持スキル reading; none in the recording -> caps not valued
    plan = wing_sp.plan_sp(nodes, sp=sp_before, primary="Vo", stat=stat, cap=cap, skills=live)
    first = plan.steps[0] if plan.steps else None
    row.update(maa=first.node_id if first else None, maa_tier=first.tier if first else None,
               plan=[s.node_id for s in plan.steps], options=[n.id for n in nodes],
               user_kind=next((n.kind for n in nodes if wsp.same_skill(n.id, who)), None))
    row["kind"] = "agree" if first and wsp.same_skill(first.node_id, who) else (
        "agree_later_in_plan" if any(wsp.same_skill(s.node_id, who) for s in plan.steps) else "disagree")
    if row["kind"] == "agree_later_in_plan":
        row["kind"] = "disagree"
        row["note"] = "user's node is in Maa's plan, not first"
    return row


# ---- Live -------------------------------------------------------------------------------------------------------
def _tap_kind(x, y) -> str:
    for n in (1, 2, 3):
        if replay._in_rect(x, y, nav.TARGETS[f"live_card_{n}"][0]):
            return f"card{n}"
    for n in (1, 2, 3):
        if replay._in_rect(x, y, nav.TARGETS[f"live_judge_{n}"][0]):
            return f"judge{n}"
    if replay._in_rect(x, y, nav.TARGETS["live_appeal_timing"][0]):
        return "timing"
    if replay._in_rect(x, y, nav.TARGETS["live_auto"][0]):
        return "auto"
    return "other"


def live_section(name: str, seg, sub: dict, backend) -> dict:
    labels = sub["labels"]
    samples, read = frames_of(sub, backend)
    clicks = [c for c in replay.load_clicks(sub) if c["clean"] and labels.get(str(c["before"]), "").startswith("Live")]
    phase = collections.Counter()
    judge = collections.Counter()
    card_commit = collections.Counter()
    first_card = collections.Counter()
    rows = []
    cards: dict = {}
    turn_state = {"turn": None, "sigs": None, "first": True, "selected": None, "inspected": []}
    for click in clicks:
        r = read(click["before"], "Live")
        tap = _tap_kind(click["x"], click["y"])
        live = r.get("live_state") or {}
        p = r.get("live_phase")
        expected = {"card": ("select_card", "select_judge"), "judge": ("select_judge", "timing"),
                    "timing": ("timing",)}.get(tap.rstrip("123"))
        if expected:
            phase["ok" if p in expected else ("unread" if p is None else "wrong")] += 1
        if p is None:
            continue
        if live.get("turn") != turn_state["turn"]:
            turn_state = {"turn": live.get("turn"), "sigs": None, "first": True, "selected": None, "inspected": []}
        if p == "select_card" and turn_state["sigs"] is None:
            turn_state["sigs"] = r.get("hand_sigs")
        if p == "select_judge" and r.get("tooltip") and turn_state["sigs"] and r.get("selected_card") in (1, 2, 3):
            wlp.remember(cards, turn_state["sigs"][r["selected_card"] - 1], r["tooltip"])
        judges = [{"attribute": j.get("attribute"), "trend_rank": j.get("trend_rank"), "left": j.get("left"),
                   "present": not j.get("left")} for j in live.get("judges") or ()]
        target = wlp.target_judge(judges, primary="Vo", estimate=None)
        hand = []
        for slot in (1, 2, 3):
            attr = next((o["attribute"] for o in live.get("options") or () if o["id"] == f"card{slot}"), None)
            info = wlp.lookup(cards, (turn_state["sigs"] or [None] * 3)[slot - 1]) or {}
            hand.append({"slot": slot, "attribute": info.get("attribute") or attr, "multiplier": info.get("multiplier"),
                         "appeal": info.get("appeal"), "buff": info.get("buff")})
        row = {"frame": click["before"], "tap": tap, "phase": p, "turn": live.get("turn"), "target": target}
        if tap.startswith("judge") and p == "select_judge" and target["attribute"]:
            user_attr = ("Vo", "Da", "Vi")[int(tap[-1]) - 1]
            judge["agree" if user_attr == target["attribute"] else "disagree"] += 1
            selected = r.get("selected_card")
            tip = r.get("tooltip") or {}
            if selected in (1, 2, 3) and tip.get("multiplier") is not None:
                hand[selected - 1].update({k: tip[k] for k in ("multiplier", "appeal", "buff") if tip.get(k)})
                choice = wlp.choose_card(hand, target["attribute"], None, selected=selected,
                                         inspected=turn_state["inspected"], switches=wlp.MAX_SWITCHES)
                card_commit["agree" if choice["action"] == "play" else "disagree"] += 1
                row["card_choice"] = choice
            row["user_judge"] = user_attr
        if tap.startswith("card") and p in ("select_card", "select_judge"):
            slot = int(tap[-1])
            if turn_state["first"] and p == "select_card":
                choice = wlp.choose_card(hand, target["attribute"] or "Vo", None) if target["attribute"] else None
                if choice:
                    first_card["agree" if choice["slot"] == slot else "disagree"] += 1
                    row["maa_first_card"] = choice
                turn_state["first"] = False
            turn_state["inspected"].append(slot)
        secured = wlp.secured_first(live)
        row["secured"] = secured.get("secured")
        rows.append(row)
    secured_frames = 0
    own_frames = 0
    for seq in sorted(samples):
        if labels.get(str(seq), "") != "Live":
            continue
        r = read(seq, "Live")
        if r.get("auto") != "OFF" or r.get("live_phase") is None:
            continue
        own_frames += 1
        secured_frames += bool(wlp.secured_first(r.get("live_state") or {}).get("secured"))
    pct = lambda c: round(100.0 * c["agree"] / (c["agree"] + c["disagree"]), 1) if c["agree"] + c["disagree"] else None  # noqa: E731
    return {"clean_clicks": len(clicks), "phase": dict(phase), "judge": dict(judge), "judge_pct": pct(judge),
            "card_commit": dict(card_commit), "card_commit_pct": pct(card_commit), "first_card": dict(first_card),
            "first_card_pct": pct(first_card), "cards_learned": len(cards), "own_play_frames": own_frames,
            "secured_frames": secured_frames, "rows": rows}


def analyse(backend_name: str = "boxes") -> dict:
    out = {"readonly": True, "backend": backend_name, "sp": {}, "live": {}}
    for name, path in RECORDINGS:
        doc = replay.load_labels(path)
        for seg, sub in replay.segment_docs(doc):
            key = name if seg is None else f"{name}-{seg}"
            cache = replay.analysis_dir(sub) / sub.get("roi_cache", "maa_roi_cache.json")
            backend = replay.MaaBackend(cache) if backend_name == "maa" else wsc.BoxBackend()
            out["sp"][key] = sp_section(name, seg, sub, backend)
            out["live"][key] = live_section(name, seg, sub, backend)
            if isinstance(backend, replay.MaaBackend):
                backend.save()
    sp_tot = collections.Counter()
    learn_tot = collections.Counter()
    truth_ok = truth_n = 0
    for part in out["sp"].values():
        sp_tot.update(part["slot_taps"])
        learn_tot.update(part["learn"]["kinds"])
        truth_ok += sum(t["ok"] for t in part["truth"])
        truth_n += len(part["truth"])
    entry = collections.Counter()
    for part in out["sp"].values():
        entry.update(part["entry"])
    comparable = learn_tot["agree"] + learn_tot["disagree"]
    live_tot = {k: collections.Counter() for k in ("phase", "judge", "card_commit", "first_card")}
    for part in out["live"].values():
        for k in live_tot:
            live_tot[k].update(part[k])
    pct = lambda c: round(100.0 * c["agree"] / (c["agree"] + c["disagree"]), 1) if c["agree"] + c["disagree"] else None  # noqa: E731
    out["summary"] = {
        "sp_entry": dict(entry), "sp_slot_taps": dict(sp_tot), "sp_truth": f"{truth_ok}/{truth_n}",
        "sp_learn": dict(learn_tot),
        "sp_learn_agreement_pct": round(100.0 * learn_tot["agree"] / comparable, 1) if comparable else None,
        "live_phase": dict(live_tot["phase"]), "live_judge_pct": pct(live_tot["judge"]),
        "live_judge": dict(live_tot["judge"]), "live_card_commit": dict(live_tot["card_commit"]),
        "live_card_commit_pct": pct(live_tot["card_commit"]), "live_first_card": dict(live_tot["first_card"]),
        "live_first_card_pct": pct(live_tot["first_card"]),
        "live_secured_frames": sum(p["secured_frames"] for p in out["live"].values()),
        "live_own_play_frames": sum(p["own_play_frames"] for p in out["live"].values()),
    }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W.I.N.G. SP 学习 / Live 出牌录制回放（只读，0 输入）")
    parser.add_argument("--backend", choices=("boxes", "maa"), default="boxes")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = analyse(args.backend)
    out = args.report or OUT / f"play-{args.backend}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
