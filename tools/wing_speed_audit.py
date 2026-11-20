#!/usr/bin/env python3
"""W.I.N.G. speed audit (2026-10-25, offline, zero input): where the classification shortcuts may answer.

The live loop classifies each new frame from one whole-frame OCR and the guards in ``SC.Wing.Nav.Loop`` dispatch order
(first hit wins). agent/core/wing_speed.py describes two shortcuts; this audit replays both against that full
classification on every hand-labelled demo frame (tools/wing_demo*_labels.json, private recordings):

A. Hint (one screen + its confusers, native ROI OCR guards): for screen S the confusers are every screen the full
   classifier chose on a frame where S's native guard also hit, plus Loading. The hint answers S only when S's guard
   hits and no confuser's does. S qualifies when the hint never answers S on a frame the full classifier calls
   anything else, S has ``MIN_SAMPLES`` frames, at most ``MAX_CONFUSERS`` confusers, and the hint answers at least
   ``MIN_HINT_RATE`` of S's own frames (otherwise it only adds guard checks).
B. Same view (``wing_speed.same_view``): no two frames of one recording that pass it may be classified differently
   in a way their labels reject (the reused answer would be wrong).

Backends: the full classification uses whole-frame OCR boxes (what live ``classify`` uses); the hint uses native ROI
OCR (``--hint-backend maa``, the replay ROI caches; uncached ROIs run MaaFramework offline) or the boxes (``boxes``,
quick look). Reports: dev_assets/private/wing-speed-audit/. ``--emit`` prints ``HINT_CONFUSERS`` for wing_speed.py.
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
from agent.core import wing_screen as wsc  # noqa: E402
from agent.core import wing_speed as ws  # noqa: E402
from tools import wing_demo_replay as wdr  # noqa: E402

OUT = ROOT / "dev_assets" / "private" / "wing-speed-audit"
DOCS = (wdr.LABELS, wdr.LABELS_DEMO2, wdr.LABELS_DEMO3)
MIN_SAMPLES = 3
MAX_CONFUSERS = 5
MIN_HINT_RATE = 0.5          # share of the screen's own frames the hint answers (a lower one only adds checks)
ALWAYS_CONFUSERS = ("Loading",)


def label_accepts(label: str, screen: str | None, doc: dict) -> bool:
    """``wing_demo_replay.accepts``; a ``…|Loading`` overlay frame accepts only Loading for another real screen."""
    if screen is not None and "Loading" in label.split("|") and screen != "Loading":
        return False
    return wdr.accepts(label, screen, doc)


def frames_of(doc: dict, hint_backend: str = "maa"):
    """(recording, sub-document, rows) per recording; a row: seq, label, screen (full), hits (full), native (hint
    backend hits of every screen guard) and thumb."""
    nodes = wsc.load_nodes()
    customs = {"SC.Wing.State": lambda p, f: True}
    for _, sub in wdr.segment_docs(doc):
        full = wsc.ScreenClassifier(nodes, wsc.BoxBackend(), customs)
        backend = (wdr.MaaBackend(wdr.analysis_dir(sub) / sub.get("roi_cache", "maa_roi_cache.json"))
                   if hint_backend == "maa" else None)
        native = wsc.ScreenClassifier(nodes, backend, customs) if backend is not None else None
        rows = []
        for sample in wdr.load_samples(sub):
            label = sub["labels"].get(str(sample["seq"]))
            if label is None:
                continue
            rgb = wdr.load_rgb(sample["file"])
            frame = wsc.Frame(sample["texts"], lambda r=rgb: r, key=sample["file"].name)
            result = full.classify(frame, all_hits=True)
            hits = native.classify(frame, all_hits=True).hits if native is not None else result.hits
            rows.append({"seq": sample["seq"], "label": label, "screen": result.screen, "hits": list(result.hits),
                         "native": list(hits), "thumb": ws.thumb(rgb)})
        if backend is not None:
            backend.save()
        yield Path(sub["recording"]).name, sub, rows


def hint_audit(recs) -> dict:
    samples = collections.Counter()
    seen = collections.defaultdict(list)          # screen -> rows where its native guard hit
    for rec, sub, rows in recs:
        for row in rows:
            if row["screen"] is not None:
                samples[row["screen"]] += 1
            for screen in row["native"]:
                seen[screen].append((rec, row))
    confusers, unresolved, rates = {}, {}, {}
    for rule in nav.SCREENS:
        s = rule.screen
        conf = set(ALWAYS_CONFUSERS) - {s}
        conf |= {row["screen"] for _, row in seen[s] if row["screen"] not in (None, s)}
        bad = [{"rec": rec, "seq": row["seq"], "label": row["label"], "full": row["screen"]}
               for rec, row in seen[s] if row["screen"] != s and not any(t in row["native"] for t in conf)]
        confusers[s] = sorted(conf)
        if bad:
            unresolved[s] = bad
        own = [row for _, _, rows in recs for row in rows if row["screen"] == s]
        answered = sum(1 for row in own if s in row["native"] and not any(t in row["native"] for t in conf))
        rates[s] = round(answered / len(own), 3) if own else 0.0
    eligible = {s: tuple(c) for s, c in confusers.items()
                if samples[s] >= MIN_SAMPLES and s not in unresolved and len(c) <= MAX_CONFUSERS
                and rates[s] >= MIN_HINT_RATE}
    return {"samples": dict(samples), "confusers": confusers, "eligible": eligible, "hint_rates": rates,
            "unresolved_counts": {k: len(v) for k, v in sorted(unresolved.items())},
            "unresolved": {k: v[:12] for k, v in sorted(unresolved.items())}}


def view_audit(recs) -> dict:
    """Pairs that pass ``same_view`` but whose full classifications differ where the labels disagree too."""
    import numpy as np
    bad, passed = [], 0
    for rec, sub, rows in recs:
        thumbs = [r["thumb"] for r in rows]
        for i, row in enumerate(rows):
            if row["screen"] is None:
                continue
            means = [float(np.abs(thumbs[i] - t).mean()) for t in thumbs]
            for j, mean in enumerate(means):
                if j == i or mean > ws.VIEW_MEAN_DIFF or not ws.same_view(thumbs[i], thumbs[j]):
                    continue
                passed += 1
                other = rows[j]
                if other["screen"] != row["screen"] and not label_accepts(other["label"], row["screen"], sub):
                    bad.append({"rec": rec, "from": row["seq"], "to": other["seq"], "reused": row["screen"],
                                "full": other["screen"], "label": other["label"],
                                "diff": [round(v, 2) for v in ws.view_diff(thumbs[i], thumbs[j])]})
    return {"pairs_passing": passed, "bad": bad[:40], "bad_count": len(bad)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W.I.N.G. 提速审计（离线，0 输入）")
    parser.add_argument("--hint-backend", choices=("boxes", "maa"), default="maa")
    parser.add_argument("--emit", action="store_true", help="打印 wing_speed.HINT_CONFUSERS")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    recs = [rec for path in DOCS for rec in frames_of(wdr.load_labels(path), args.hint_backend)]
    hint, view = hint_audit(recs), view_audit(recs)
    report = {"hint_backend": args.hint_backend, "hint": hint, "view": view}
    (OUT / f"report-{args.hint_backend}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
    print(f"[{args.hint_backend}] frames {sum(len(r) for _, _, r in recs)}; hint eligible {len(hint['eligible'])}; "
          f"unresolved {hint['unresolved_counts']}; same-view pairs {view['pairs_passing']}, bad {view['bad_count']}")
    if args.emit:
        lines = ",\n".join(f"    {json.dumps(s)}: {json.dumps(list(c)).replace('[', '(').replace(']', ',)' if len(c) == 1 else ')')}"
                           for s, c in sorted(hint["eligible"].items()))
        print("HINT_CONFUSERS: dict[str, tuple[str, ...]] = {\n" + lines + ",\n}")
    return 0 if view["bad_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
