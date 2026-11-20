#!/usr/bin/env python3
"""Offline replay of the W.I.N.G. demo recording through the navigation framework (read-only, zero input).

Walks every sample of ``dev_assets/private/recordings/wing-demo-20261005-0058-01`` (sequence order) through:

1. the screen classifier (``agent.core.wing_screen``: Pipeline anchors/guards, dispatch order of SC.Wing.Nav.Loop),
   compared with the hand labels in ``tools/wing_demo_labels.json`` -> coverage, confusions;
2. the readers on frames with hand-checked truth -> per-field accuracy;
3. the navigation runtime (``agent.core.wing_nav``) -> the intent each frame would produce, checking that every
   click intent sits on a frame whose label agrees with the guarded screen ("unguarded" must stay 0) and that the
   Act gate refused it (live input disabled; clicks sent = 0);
4. the full stack at every decision point (2026-10-05 integration): run memory from the default True End
   ``produce_config`` (scorer ``vo_score``, ``live_mode`` policy, unit7), the scorer / audition / live / SP hooks, both
   sequentially (memory carried across frames) and isolated (fresh memory per frame); at the frames of the
   hand-written timeline (``timeline.json``) the user's choice and the scorer's choice on the hand-read state are
   added, so stop reasons of the navigator can be compared with the scorer's own output.

Backends: ``boxes`` (default; whole-frame OCR boxes from ocr.jsonl, fast) or ``maa`` (real MaaFramework ROI OCR,
authoritative; ROI results cached in dev_assets/private/wing-demo-analysis/maa_roi_cache.json). ColorMatch is exact
numpy on the PNG in both. Report: dev_assets/private/wing-demo-analysis/nav_replay[_maa].json (private).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_nav as nav  # noqa: E402
from agent.core import wing_screen as wsc  # noqa: E402

LABELS = ROOT / "tools" / "wing_demo_labels.json"
LABELS_DEMO2 = ROOT / "tools" / "wing_demo2_labels.json"     # second demo: items, 大富豪, frames
LABELS_DEMO3 = ROOT / "tools" / "wing_demo3_labels.json"     # third demo (2026-10-06, 3 segments): idol list, end flow
ANALYSIS = ROOT / "dev_assets" / "private" / "wing-demo-analysis"
TIMELINE = ANALYSIS / "timeline.json"
DECISION_KINDS = ("decide",)


def default_memory() -> nav.RunMemory:
    """Run memory of the default manual W.I.N.G. run (True End, vo_score, live_mode policy, unit7)."""
    from agent.core import produce_config as pc
    return nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(pc.default_config("true_end")))


def load_timeline(path=TIMELINE) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for entry in doc.get("entries", []):
        for frame in entry.get("frames", []):
            out[int(str(frame).lstrip("#"))] = entry
    return {"doc": doc, "by_seq": out}


def timeline_scorer(entry: dict, strategy: str) -> dict:
    """The scorer's choice on the hand-read timeline state (same call as tools/wing_score_compare.py)."""
    from agent.core import wing_policy as wp
    from agent.core import wing_state as ws
    try:
        decision = wp.decide(ws.WingState.from_dict(entry["state"]), strategy)
    except Exception as exc:  # report, never crash the replay
        return {"error": f"{type(exc).__name__}: {exc}"}
    info = decision.to_dict()
    return {"kind": info.get("kind"), "option_id": info.get("option_id"), "audition_id": info.get("audition_id"),
            "rule": info.get("rule"), "reasons": list(info.get("reasons") or [])[:4]}


def _brief(decision: dict) -> dict:
    detail = decision.get("detail") or {}
    out = {"action": decision["action"], "intent": decision.get("intent"), "reason": decision.get("reason")}
    week = detail.get("decision")
    if isinstance(week, dict):
        out["scorer"] = {"kind": week.get("kind"), "option_id": week.get("option_id"),
                         "audition_id": week.get("audition_id"), "rule": week.get("rule")}
    for key in ("verdict", "plan", "problems", "step"):
        if key in detail:
            value = detail[key]
            out[key] = {k: v for k, v in value.items() if k != "decision"} if isinstance(value, dict) else value
    return out


def _readings_brief(reading: dict) -> dict:
    keep = ("season", "weeks_left", "stats", "fans", "sp", "stamina_pct", "selected_room", "reward", "auto",
            "unit_index", "unit_page", "options", "tutorial_page")
    return {k: reading[k] for k in keep if reading.get(k) is not None}
CACHE = ANALYSIS / "maa_roi_cache.json"


def load_labels(path=LABELS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_samples(doc: dict) -> list[dict]:
    """[{seq, file, texts}] for every recorded sample (duplicates map to their stored file)."""
    rec = ROOT / doc["recording"]
    files = {}
    for line in (rec / "index.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("file"):
            files[row["sequence"]] = rec / row["file"]
    texts = {}
    for line in (ROOT / doc["ocr"]).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        texts[row["seq"]] = [(t, tuple(b)) for t, b in row["texts"]]
    first: dict = {}
    for s in sorted(files):
        first.setdefault(files[s], s)
    # An exact duplicate capture has no OCR row of its own: it uses the OCR of the stored frame it points to.
    return [{"seq": s, "file": files[s], "texts": texts.get(s) or texts.get(first[files[s]], [])}
            for s in sorted(files)]


def load_rgb(path: Path):
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        if image.size != wsc.CANVAS:
            raise ValueError(f"not 1280x720: {path.name}")
        return np.ascontiguousarray(np.asarray(image.convert("RGB")))


class MaaBackend(wsc.BoxBackend):
    """Real MaaFramework ROI OCR (screenshot-only controller, zero input) with a persistent per-ROI cache."""
    name = "maa"

    def __init__(self, cache_path=CACHE):
        from tools.wing_sample_ocr import WingRecognizer
        self.rec = WingRecognizer()
        self.cache_path = Path(cache_path)
        self.cache = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path.is_file() else {}
        model = f"{self.rec.version}|{self.rec.model}"
        if self.cache.get("_model") != model:
            self.cache = {"_model": model}
        self.dirty = 0

    def ocr(self, node, frame):
        roi = tuple(node.get("roi", [0, 0, *wsc.CANVAS]))
        bucket = self.cache.setdefault(frame.key, {})
        key = ",".join(map(str, roi))
        if key not in bucket:
            image = frame.image[:, :, ::-1].copy()  # Maa expects BGR
            result = self.rec.run(image, {"recognition": "OCR", "roi": list(roi), "expected": [], "threshold": 0.0})
            bucket[key] = [[t["text"], t["score"], t["box"]] for t in result["texts"]]
            self.dirty += 1
            if self.dirty % 500 == 0:
                self.save()
        threshold = float(node.get("threshold", 0.3))
        return [(wsc.apply_replace(t, node.get("replace")), tuple(b)) for t, s, b in bucket[key] if s >= threshold]

    def save(self):
        if self.dirty:
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    @property
    def input_attempts(self):
        return self.rec.input_attempts


def accepts(label: str, screen: str | None, doc: dict) -> bool:
    if label in doc["wait_labels"]:
        return screen is None or nav.RULES[screen].kind == "wait"
    for option in label.split("|"):
        if screen == option or screen in doc["families"].get(option, ()):
            return True
    return False


def compare(field: str, got, want) -> bool:
    if field == "stamina_pct":
        return got is not None and abs(got - want) <= 5
    if field == "owned":
        return got is not None and {k: list(v) for k, v in got.items()} == want
    if field == "options":
        return got is not None and all(re.sub(r"\s", "", got.get(k) or "") == v for k, v in want.items())
    return got == want


def analysis_dir(doc: dict) -> Path:
    """Private analysis directory of a labels document (OCR, ROI cache, timeline, reports)."""
    return ROOT / doc["analysis"] if doc.get("analysis") else ANALYSIS


def segment_docs(doc: dict) -> list[tuple[str | None, dict]]:
    """A labels document with ``segments`` (one recording per segment, third demo) -> one sub-document per segment
    (shared keys + the segment's recording/ocr/labels/truth/roi_cache); a plain document -> itself."""
    if "segments" not in doc:
        return [(None, doc)]
    shared = {k: v for k, v in doc.items() if k != "segments"}
    return [(seg["id"], {**shared, **seg}) for seg in doc["segments"]]


def _merge_counts(into: dict, part: dict) -> None:
    for key, value in part.items():
        a, b = (int(x) for x in value.split("/"))
        if key in into:
            c, d = (int(x) for x in into[key].split("/"))
            a, b = a + c, b + d
        into[key] = f"{a}/{b}"


def combine_reports(parts: list[tuple[str, dict]]) -> dict:
    """Sum of per-segment replay reports (summary counts, per-label, reads, intents, gates, click targets)."""
    summary: dict = {}
    for _, rep in parts:
        for key, value in rep["summary"].items():
            if isinstance(value, int) and not isinstance(value, bool):
                summary[key] = summary.get(key, 0) + value
            else:
                summary.setdefault(key, value)
    labels_all = set()
    per_label: dict = {}
    reads: dict = {}
    for seg, rep in parts:
        _merge_counts(per_label, rep["per_label"])
        labels_all |= set(rep["per_label"])
        for field, value in rep["reads"].items():
            cur = reads.setdefault(field, {"exact": "0/0", "misses": []})
            merged = {field: cur["exact"]}
            _merge_counts(merged, {field: value["exact"]})
            cur["exact"] = merged[field]
            cur["misses"] += [{"segment": seg, **m} for m in value["misses"]]
    summary["screen_types_labelled"] = len(labels_all)
    summary["screen_types_fully_recognised"] = sum(1 for v in per_label.values()
                                                   if v.split("/")[0] == v.split("/")[1])
    out = {"readonly": True, "segments": {seg: rep["summary"] for seg, rep in parts}, "summary": summary,
           "per_label": dict(sorted(per_label.items())), "reads": reads}
    for key in ("act_gate", "stops"):
        merged: dict = {}
        for _, rep in parts:
            for k, v in rep[key].items():
                merged[k] = merged.get(k, 0) + v
        out[key] = merged
    for key in ("confusions", "intents", "unguarded", "crashes", "decision_points", "frames"):
        out[key] = [{"segment": seg, **row} for seg, rep in parts for row in rep[key]]
    targets: dict = {}
    clicks = clean = 0
    for seg, rep in parts:
        ct = rep.get("click_targets")
        if not ct:
            continue
        clicks, clean = clicks + ct["clicks"], clean + ct["clean"]
        for intent, info in ct["targets"].items():
            cur = targets.setdefault(intent, {"verified": info["verified"], "screens": info["screens"], "clicks": 0,
                                              "changed": 0, "after_labels": {}, "examples": []})
            cur["clicks"] += info["clicks"]
            cur["changed"] += info["changed"]
            for k, v in info["after_labels"].items():
                cur["after_labels"][k] = cur["after_labels"].get(k, 0) + v
            cur["examples"] += [{"segment": seg, **e} for e in info["examples"]][:6 - len(cur["examples"])]
    if targets:
        out["click_targets"] = {"clicks": clicks, "clean": clean, "targets": targets}
    return out


def replay(backend_name: str = "boxes", limit: int | None = None, labels=LABELS) -> dict:
    doc = load_labels(labels)
    parts = segment_docs(doc)
    if parts[0][0] is None:
        return replay_doc(doc, backend_name, limit)
    return combine_reports([(seg, replay_doc(sub, backend_name, limit)) for seg, sub in parts])


def replay_doc(doc: dict, backend_name: str = "boxes", limit: int | None = None) -> dict:
    samples = load_samples(doc)[:limit]
    analysis = analysis_dir(doc)
    cache = analysis / doc.get("roi_cache", "maa_roi_cache.json")
    backend = MaaBackend(cache) if backend_name == "maa" else wsc.BoxBackend()
    nodes = wsc.load_nodes()
    customs = {"SC.Wing.State": lambda p, f: True}
    classifier = wsc.ScreenClassifier(nodes, backend, customs)
    runtime = nav.WingNavRuntime(default_memory())
    runtime.begin()
    timeline = load_timeline(analysis / "timeline.json")
    # Pre-pass: the audition pages the demo shows per week (season, 残り週), i.e. what the navigator's audition survey
    # would have read; used for the isolated week decisions ("after survey").
    survey: dict = {}
    for sample in samples:
        if doc["labels"][str(sample["seq"])].split("|")[0] != "AuditionTab":
            continue
        frame = wsc.Frame(sample["texts"], lambda p=sample["file"]: load_rgb(p), key=sample["file"].name)
        page = wsc.read_screen("AuditionTab", classifier.evaluator, frame)
        if isinstance(page.get("season"), int) and isinstance(page.get("weeks_left"), int)                 and isinstance(page.get("reward"), int):
            week = survey.setdefault((page["season"], page["weeks_left"]), {"pages": [], "trend": None})
            if all(p["reward"] != page["reward"] for p in week["pages"]):
                week["pages"].append({"reward": page["reward"], "difficulty": page.get("difficulty"),
                                      "recommended": dict(page.get("recommended") or {}),
                                      "required_rank": page.get("required_rank"), "locked": False})
            week["trend"] = week["trend"] or page.get("trend")
    points = []
    crashes = []
    rows, confusion = [], collections.Counter()
    per_label = collections.defaultdict(lambda: [0, 0])
    reads = collections.defaultdict(lambda: [0, 0, []])
    intents = collections.Counter()
    refusals = collections.Counter()
    stops = collections.Counter()
    unguarded = []
    for sample in samples:
        seq = sample["seq"]
        frame = wsc.Frame(sample["texts"], lambda p=sample["file"]: load_rgb(p), key=sample["file"].name)
        label = doc["labels"][str(seq)]
        result = classifier.classify(frame, all_hits=True)
        ok = accepts(label, result.screen, doc)
        per_label[label][0] += ok
        per_label[label][1] += 1
        if not ok:
            confusion[(label, result.screen)] += 1
        truth = doc["truth"].get(str(seq))
        reading = {}
        if truth:
            read_as = label.split("|")[0]
            reading = wsc.read_screen(read_as, classifier.evaluator, frame)
            for field, want in truth.items():
                got = reading.get(field)
                good = compare(field, got, want)
                reads[field][0] += good
                reads[field][1] += 1
                if not good:
                    reads[field][2].append({"seq": seq, "got": got, "want": want})
        row = {"seq": seq, "label": label, "screen": result.screen, "hits": list(result.hits), "ok": ok}
        if result.screen is not None:
            if not truth:
                reading = wsc.read_screen(result.screen, classifier.evaluator, frame)
            try:
                decision = runtime.decide(result.screen, reading)
            except Exception as exc:  # an integration bug: record it, keep replaying
                crashes.append({"seq": seq, "screen": result.screen, "where": "sequential",
                                "error": f"{type(exc).__name__}: {exc}"})
                decision = nav.NavDecision("stop", reason=f"crash:{type(exc).__name__}")
            row["decision"] = decision.to_dict()
            rule = nav.RULES.get(result.screen)
            if rule is not None and rule.kind in DECISION_KINDS:
                memory = default_memory()
                week = (reading.get("season"), reading.get("weeks_left"))
                if result.screen == "Schedule" and isinstance(week[0], int) and isinstance(week[1], int):
                    # isolated = after the survey: the pages the demo showed for this week (may be none)
                    known = survey.get(week, {"pages": [], "trend": None})
                    gates = [pg["required_rank"] for pg in known["pages"] if pg["required_rank"] in nav.RANKS]
                    memory.auditions_seen = {"key": list(week), "pages": known["pages"], "trend": known["trend"],
                                             "done": True,
                                             "rank_floor": max(gates, key=nav.RANKS.index) if gates else None}
                isolated_rt = nav.WingNavRuntime(memory)
                isolated_rt.begin()
                try:
                    isolated = isolated_rt.decide(result.screen, reading).to_dict()
                except Exception as exc:
                    crashes.append({"seq": seq, "screen": result.screen, "where": "isolated",
                                    "error": f"{type(exc).__name__}: {exc}"})
                    isolated = {"action": "stop", "intent": None, "reason": f"crash:{type(exc).__name__}"}
                point = {"seq": seq, "label": label, "screen": result.screen, "point": rule.point,
                         "auditions_known": len((memory.auditions_seen or {}).get("pages") or []),
                         "readings": _readings_brief(reading), "sequential": _brief(row["decision"]),
                         "isolated": _brief(isolated)}
                entry = timeline.get("by_seq", {}).get(seq) if timeline else None
                if entry is not None:
                    point["timeline"] = {"id": entry["id"], "user_choice": entry.get("user_choice"),
                                         "choice_certain": entry.get("choice_certain"),
                                         "scorer_on_hand_state": timeline_scorer(
                                             entry, timeline["doc"].get("strategy", "vo_score"))}
                points.append(point)
            if decision.action == "click":
                sent, why = runtime.act(result.screen, decision)
                runtime.active, runtime.finished = True, None   # replay continues after a refusal
                refusals[why] += 1
                intents[(result.screen, decision.intent)] += 1
                if sent:
                    raise RuntimeError("input was sent during offline replay")
                if not accepts(label, result.screen, doc):
                    unguarded.append({"seq": seq, "label": label, "screen": result.screen, "intent": decision.intent})
            elif decision.action == "stop":
                stops[decision.reason] += 1
        rows.append(row)
    if isinstance(backend, MaaBackend):
        backend.save()
    labelled = [r for r in rows]
    actionable = [r for r in labelled if r["label"] not in doc["wait_labels"]]
    summary = {
        "backend": backend.name,
        "samples": len(rows),
        "accepted": sum(r["ok"] for r in rows),
        "actionable_frames": len(actionable),
        "actionable_accepted": sum(r["ok"] for r in actionable),
        "screen_types_labelled": len({r["label"] for r in rows}),
        "screen_types_fully_recognised": sum(1 for v in per_label.values() if v[0] == v[1]),
        "click_intents": sum(intents.values()),
        "unguarded_click_intents": len(unguarded),
        "clicks_sent": 0,
        "input_attempts": getattr(backend, "input_attempts", 0),
        "decision_points": len(points),
        "crashes": len(crashes),
    }
    stack = collections.Counter((p["screen"], p["isolated"]["action"], p["isolated"]["reason"]) for p in points)
    report = {
        "readonly": True,
        "pipeline_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wsc.PIPELINE_FILES},
        "summary": summary,
        "per_label": {k: f"{v[0]}/{v[1]}" for k, v in sorted(per_label.items())},
        "confusions": [{"label": a, "screen": b, "count": n} for (a, b), n in confusion.most_common()],
        "reads": {k: {"exact": f"{v[0]}/{v[1]}", "misses": v[2]} for k, v in sorted(reads.items())},
        "intents": [{"screen": s, "intent": i, "count": n} for (s, i), n in intents.most_common()],
        "act_gate": dict(refusals),
        "stops": dict(stops),
        "unguarded": unguarded,
        "crashes": crashes,
        "decision_points": points,
        "stack_summary": [{"screen": a, "action": b, "reason": c, "count": n} for (a, b, c), n in stack.most_common()],
        "frames": rows,
    }
    if any('"event"' in line for line in (ROOT / doc["recording"] / "index.jsonl").read_text(encoding="utf-8")
           .splitlines()[:400]):
        report["click_targets"] = click_report(doc)
    return report


def load_clicks(doc: dict) -> list[dict]:
    """Manual clicks logged by the recorder (``event: click``, canvas x/y) paired with the stored frame right before
    and the first frame after (>= 0.3 s later). ``clean``: the after frame comes before the next click and the before
    frame is at most 3 s old, so the pair shows the effect of exactly this click. [] for a recording without a log."""
    rec = ROOT / doc["recording"]
    rows = [json.loads(line) for line in (rec / "index.jsonl").read_text(encoding="utf-8").splitlines() if line]
    caps = [r for r in rows if r.get("file")]
    canon: dict = {}
    for row in caps:
        canon.setdefault(row["file"], row["sequence"])
    seen, clicks = set(), []
    for row in rows:
        if row.get("event") == "click":
            key = (row["elapsed_seconds"], row["x"], row["y"])
            if key not in seen:          # the recorder writes a click row before and after the paired capture
                seen.add(key)
                clicks.append(row)
    out = []
    for i, click in enumerate(clicks):
        t = click["elapsed_seconds"]
        before = [c for c in caps if c["elapsed_seconds"] <= t]
        after = [c for c in caps if c["elapsed_seconds"] > t + 0.3]
        nxt = clicks[i + 1]["elapsed_seconds"] if i + 1 < len(clicks) else float("inf")
        clean = bool(before and after) and after[0]["elapsed_seconds"] < nxt and t - before[-1]["elapsed_seconds"] <= 3.0
        out.append({"x": click["x"], "y": click["y"], "t": t,
                    "before": canon[before[-1]["file"]] if before else None,
                    "after": canon[after[0]["file"]] if after else None, "clean": clean})
    return out


def _in_rect(x: int, y: int, rect) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


# Screens on which each click target is used (fixed intents from the rules, decision intents by handler).
TARGET_SCREENS = {
    "schedule_room_": ("Schedule",), "schedule_decide": ("Schedule", "AuditionTab"),
    "schedule_audition_tab": ("Schedule",), "schedule_lesson_tab": ("AuditionTab", "AuditionLocked"),
    "audition_": ("AuditionTab", "AuditionLocked"), "hub_": ("Hub", "HubSemiFinal", "HubFinal"),
    "page_return": ("Schedule", "Review", "HelpList", "SkillPanelReadOnly", "UnitCheck", "IdolDetail"),
    "dialog_": ("RestConfirm", "LiveConfirm"), "live_": ("Live",), "unit_": ("UnitSelect",),
    "choice_": ("Choice",), "learn_cancel": ("LearnConfirm",), "replace_cancel": ("ReplaceConfirm",),
    "forget_cancel": ("ForgetSelect",), "item_popup_close": ("ItemPopup",), "item_use_cancel": ("ItemUseConfirm",),
    # decision intents of the end flow (third demo); fes_birth_ok before its prefix fes_birth
    "fes_birth_ok": ("FesBirthConfirm",), "fes_birth": ("FesBirth",), "reward_ok": ("Reward", "RewardDetail"),
}


def target_screens(intent: str) -> tuple:
    for prefix, screens in TARGET_SCREENS.items():
        if intent.startswith(prefix):
            return screens
    return tuple(r.screen for r in nav.SCREENS if r.intent == intent)


def click_report(doc: dict) -> dict:
    """Clicks of the recording mapped onto ``wing_nav.TARGETS``: for every target, the clean clicks inside its rect on
    a frame labelled with one of the target's screens, and the labels after the click. Evidence for ``verified``."""
    labels = doc["labels"]
    report: dict = {}
    clicks = load_clicks(doc)
    for intent, (rect, verified) in nav.TARGETS.items():
        screens = target_screens(intent)
        hits = []
        for c in clicks:
            if not c["clean"] or not _in_rect(c["x"], c["y"], rect):
                continue
            before = labels.get(str(c["before"]), "")
            if not any(option in screens for option in before.split("|")):
                continue
            hits.append({"x": c["x"], "y": c["y"], "before": c["before"], "after": c["after"],
                         "before_label": before, "after_label": labels.get(str(c["after"]))})
        changed = [h for h in hits if h["after_label"] != h["before_label"] or h["after"] != h["before"]]
        report[intent] = {"verified": verified, "screens": list(screens), "clicks": len(hits),
                          "changed": len(changed),
                          "after_labels": dict(collections.Counter(h["after_label"] for h in hits)),
                          "examples": hits[:6]}
    return {"clicks": len(clicks), "clean": sum(c["clean"] for c in clicks), "targets": report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W.I.N.G. 演示录制离线回放（只读，0 输入）")
    parser.add_argument("--backend", choices=("boxes", "maa"), default="boxes")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--labels", type=Path, default=LABELS, help="标注文件（默认第一次示范录制）")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = replay(args.backend, args.limit, args.labels)
    analysis = analysis_dir(load_labels(args.labels))
    out = args.report or analysis / ("nav_replay.json" if args.backend == "boxes" else "nav_replay_maa.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    shown = out.relative_to(ROOT) if out.resolve().is_relative_to(ROOT) else out
    print(json.dumps({"report": str(shown), **report["summary"]}, ensure_ascii=False))
    print(json.dumps({"per_label": report["per_label"], "reads": {k: v["exact"] for k, v in report["reads"].items()},
                      "act_gate": report["act_gate"], "stops": report["stops"]}, ensure_ascii=False))
    return 0 if report["summary"]["unguarded_click_intents"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
