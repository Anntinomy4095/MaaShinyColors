#!/usr/bin/env python3
"""W.I.N.G. per-field number OCR accuracy on labelled private frames (read-only; never sends input).

Samples (under dev_assets/private, never committed):
  * demo: frames with hand-checked ``truth`` in tools/wing_demo*_labels.json (native 1280x720 recordings);
  * eval: dev_assets/private/ocr-eval/**/<name>.json + <name>.png -- frames WingSession keeps in live runs (a number
    rejected or left unconfirmed by agent/core/wing_numbers.py, a stop/wait on an unread field) and hand-labelled
    recording frames. A sample is scored once its JSON has a ``truth`` mapping; the others are listed for labelling.
Readers (wing_screen.read_numbers, i.e. exactly what the runtime reads):
  * after:  the current readers (badge-free status ROIs, one-line SP digits, one-line weeks retry);
  * before: the readers before 2026-10-29 (whole status row, widened SP sub-ROI, no one-line reads).
OCR: Maa ROI OCR as the live MaaContextBackend does it (all results, ``only_rec`` honoured), per ``--model`` dir.
``guard`` rows replay each recorded sequence (frames in order, one capture each) through wing_numbers: a wrong
value it lets through would reach a decision; a caught one is re-read or left unread.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_numbers as wn  # noqa: E402
from agent.core import wing_screen as wsc  # noqa: E402

PRIVATE = ROOT / "dev_assets" / "private"
EVAL_DIR = PRIVATE / "ocr-eval"
DEFAULT_MODEL = PRIVATE / "models" / "ppocr_v6_small"
DEFAULT_REPORT = EVAL_DIR / "report.json"
FIELDS = ("season", "weeks_left", "fans_left", "Vo", "Da", "Vi", "Me", "sp", "fans")
NUMBER_SCREENS = (*wsc.HEADER_SCREENS, *wsc.STATUS_NODES, *wsc.SP_PANEL_SCREENS, "RankPyramid", "FesBirth")
BEFORE_ROIS = {"SC.Wing.Read.Hub.Weeks": [478, 5, 70, 65]}     # reader ROIs before 2026-10-29 (``before`` reader)


def truth_fields(truth) -> dict:
    out = {k: truth[k] for k in ("season", "weeks_left", "fans_left", "sp", "fans") if k in truth}
    if isinstance(truth.get("stats"), dict):
        out.update({k: truth["stats"][k] for k in wn.STATS if k in truth["stats"]})
    return out


def demo_samples() -> list[dict]:
    from tools import wing_demo_replay as wdr
    out = []
    for path in (wdr.LABELS, wdr.LABELS_DEMO2, wdr.LABELS_DEMO3):
        for seg, doc in wdr.segment_docs(wdr.load_labels(path)):
            truth = doc.get("truth") or {}
            if not truth or not (ROOT / doc["recording"] / "index.jsonl").is_file():
                continue
            files = {s["seq"]: s["file"] for s in wdr.load_samples(doc)}
            for seq, value in sorted(truth.items(), key=lambda kv: int(kv[0])):
                screen = doc["labels"][seq].split("|")[0]
                fields = {k: v for k, v in truth_fields(value).items() if k in wsc.number_rois(screen, {})}
                if screen in NUMBER_SCREENS and fields and int(seq) in files:
                    out.append({"id": f"{path.stem}{':' + seg if seg else ''}:{seq}", "sequence": f"{path.stem}{seg or ''}",
                                "order": int(seq), "screen": screen, "image": files[int(seq)], "truth": fields})
    return out


def eval_samples(folder: Path = EVAL_DIR) -> tuple[list[dict], list[dict]]:
    labelled, unlabelled = [], []
    for path in sorted(folder.rglob("*.json")) if folder.is_dir() else ():
        if path.name == DEFAULT_REPORT.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        image = path.with_suffix(".png")
        if not isinstance(data, dict) or not image.is_file() or data.get("screen") not in NUMBER_SCREENS:
            continue
        rel = path.relative_to(folder).with_suffix("").as_posix()
        stem = path.stem.rsplit("-", 1)[0]
        row = {"id": f"eval:{rel}", "sequence": f"{path.parent.name}/{stem}", "order": path.stem,
               "screen": data["screen"], "image": image}
        if isinstance(data.get("truth"), dict):
            labelled.append({**row, "truth": truth_fields(data["truth"])})
        else:
            unlabelled.append({**row, "read": data.get("read"), "events": data.get("events")})
    return labelled, unlabelled


class MaaRoiBackend(wsc.BoxBackend):
    """Live-like ROI OCR on a Maa tasker (screenshot-only controller, zero input); cached per frame/ROI."""
    name = "maa_offline"

    def __init__(self, recognizer, one_line: bool):
        self.rec, self.one_line, self.cache = recognizer, one_line, {}

    def ocr(self, node, frame):
        from maa.pipeline import JOCR, JRecognitionType
        roi = tuple(int(v) for v in node.get("roi", [0, 0, *wsc.CANVAS]))
        only_rec = self.one_line and bool(node.get("only_rec"))
        key = (frame.key, roi, only_rec)
        if key not in self.cache:
            param = JOCR(expected=[], roi=roi, threshold=float(node.get("threshold", 0.3)), only_rec=only_rec)
            detail = self.rec.tasker.post_recognition(JRecognitionType.OCR, param, frame.image_bgr).wait().get()
            results = detail.nodes[0].recognition.all_results if detail and detail.nodes else []
            self.cache[key] = [(r.text, tuple(r.box)) for r in results if getattr(r, "text", None)]
        return [(wsc.apply_replace(t, node.get("replace")), b) for t, b in self.cache[key]]


def load_frame(sample) -> wsc.Frame | None:
    import numpy as np
    from PIL import Image
    with Image.open(sample["image"]) as image:
        if image.size != wsc.CANVAS:
            return None
        rgb = np.ascontiguousarray(np.asarray(image.convert("RGB")))
    frame = wsc.Frame((), lambda: rgb, key=str(sample["image"]))
    frame.image_bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    return frame


def read(sample, frame, backend, nodes, reader: str) -> dict:
    if reader == "before":
        nodes = {**nodes, **{name: {**nodes[name], "roi": roi} for name, roi in BEFORE_ROIS.items()}}
    evaluator = wsc.Evaluator(nodes, backend, {"SC.Wing.State": lambda p, f: True})
    if reader == "before":
        with mock.patch.object(wsc, "status_digit_rois", return_value=None):
            out = wsc.read_numbers(sample["screen"], evaluator, frame)
    else:
        out = wsc.read_numbers(sample["screen"], evaluator, frame)
    return {**wn.flatten(out), **({"fans_left": out["fans_left"]} if "fans_left" in out else {})}


def score(rows) -> dict:
    table = {}
    for row in rows:
        for field, want in row["truth"].items():
            if field not in FIELDS:
                continue
            got = row["read"].get(field)
            entry = table.setdefault(field, {"n": 0, "right": 0, "wrong": 0, "unread": 0, "misses": []})
            entry["n"] += 1
            if got == want:
                entry["right"] += 1
            else:
                entry["unread" if got is None else "wrong"] += 1
                entry["misses"].append({"id": row["id"], "got": got, "want": want})
    return {f: table[f] for f in FIELDS if f in table}


def guard_replay(rows) -> dict:
    """Wrong values the number guard lets through (they would reach a decision) vs catches, per field; frames whose
    decision would wait for one more capture (``frames_held``: the cost of the two-frame rule)."""
    out = {"wrong_passed": 0, "wrong_caught": 0, "right_held": 0, "right_passed": 0, "frames": 0, "frames_held": 0,
           "passed": []}
    sequences: dict = {}
    for row in rows:
        sequences.setdefault(row["sequence"], []).append(row)
    for seq_rows in sequences.values():
        guard, state = wn.NumberGuard(), {}
        for i, row in enumerate(sorted(seq_rows, key=lambda r: str(r["order"]).zfill(12))):
            readings = {k: row["read"].get(k) for k in ("season", "weeks_left", "sp", "fans")}
            stats = {k: row["read"].get(k) for k in wn.STATS}
            readings["stats"] = stats if all(v is not None for v in stats.values()) else None
            verdict = guard.check(row["screen"], readings, state, (row["sequence"], i))
            guard.holds = 0                                   # one capture per point: no re-read exists offline
            out["frames"] += 1
            out["frames_held"] += bool(verdict.hold)
            after = wn.flatten(verdict.readings)
            for field, want in row["truth"].items():
                if field not in wn.FIELDS or row["read"].get(field) is None:
                    continue
                wrong, kept = row["read"][field] != want, after.get(field) is not None and field not in verdict.hold
                key = ("wrong_" if wrong else "right_") + ("passed" if kept else ("caught" if wrong else "held"))
                out[key] += 1
                if wrong and kept:
                    out["passed"].append({"id": row["id"], "field": field, "got": row["read"][field], "want": want})
    return out


def clip_audit(min_frames: int = 1) -> list[dict]:
    """OCR nodes whose ROI cuts text: on the demo recordings' whole-frame OCR, boxes that overlap a node's ROI but
    stick out of it by more than 2 px (for a node with ``expected``: only boxes whose text matches). Live ROI OCR
    sees only the inside of such a box (wing4d 2026-10-28: 「出場条件」 20 px lower on limited pages, bottom half cut)."""
    import re
    from tools import wing_demo_replay as wdr
    nodes = wsc.load_nodes()
    ocr_nodes = {name: node for name, node in nodes.items()
                 if node.get("recognition") == "OCR" and "roi" in node and list(node["roi"]) != [0, 0, *wsc.CANVAS]}
    hits: dict = {}
    for path in (wdr.LABELS, wdr.LABELS_DEMO2, wdr.LABELS_DEMO3):
        for _, doc in wdr.segment_docs(wdr.load_labels(path)):
            if not (ROOT / doc["ocr"]).is_file():
                continue
            seen_texts: dict = {}
            for line in (ROOT / doc["ocr"]).read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                seen_texts[row["seq"]] = row["texts"]
            for seq, texts in seen_texts.items():
                for name, node in ocr_nodes.items():
                    rx, ry, rw, rh = node["roi"]
                    for text, (x, y, w, h) in texts:
                        if w <= 0 or h <= 0 or x >= rx + rw or y >= ry + rh or x + w <= rx or y + h <= ry:
                            continue
                        over = (rx - x, ry - y, x + w - rx - rw, y + h - ry - rh)   # left, top, right, bottom
                        if max(over) <= 2:
                            continue
                        inside = (min(x + w, rx + rw) - max(x, rx)) * (min(y + h, ry + rh) - max(y, ry)) / (w * h)
                        if inside < 0.3:
                            continue                       # a neighbour grazing the edge, not this node's text
                        expected = node.get("expected") or []
                        if expected and not any(re.search(p, wsc.apply_replace(text, node.get("replace")))
                                                for p in expected):
                            continue
                        entry = hits.setdefault(name, {"node": name, "roi": list(node["roi"]), "frames": set(),
                                                       "overflow": [0, 0, 0, 0], "texts": set()})
                        entry["frames"].add((path.stem, seq))
                        entry["overflow"] = [max(a, int(b)) for a, b in zip(entry["overflow"], over)]
                        entry["texts"].add(text)
    out = []
    for entry in hits.values():
        if len(entry["frames"]) >= min_frames:
            out.append({**entry, "frames": len(entry["frames"]), "texts": sorted(entry["texts"])[:6]})
    return sorted(out, key=lambda e: -e["frames"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W.I.N.G. 数字字段 OCR 逐字段准确率（私有标注帧，只读）")
    parser.add_argument("--model", type=Path, action="append", help="OCR 模型目录（可多次；默认 ppocr_v6_small）")
    parser.add_argument("--reader", choices=("before", "after"), action="append", help="读取器（默认两者）")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clip-audit", action="store_true", help="只列出截断文字的 ROI（示范录制的整帧 OCR 框）")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.clip_audit:
        for e in clip_audit():
            print(f"{e['node']:48} roi {e['roi']} frames {e['frames']:4} overflow l/t/r/b {e['overflow']} {e['texts']}")
        return 0
    from tools.replay_ocr import MaaOcrRecognizer
    labelled, unlabelled = eval_samples()
    samples = (demo_samples() + labelled)[:args.limit]
    nodes = wsc.load_nodes()
    report = {"readonly": True, "input_attempts": 0, "samples": len(samples), "unlabelled": [
        {k: (str(v) if isinstance(v, Path) else v) for k, v in u.items()} for u in unlabelled], "models": {}}
    started = time.time()
    for model in args.model or [DEFAULT_MODEL]:
        recognizer = MaaOcrRecognizer(model)
        entry = report["models"][model.name] = {"framework_version": recognizer.version, "readers": {}}
        for reader in args.reader or ["before", "after"]:
            backend = MaaRoiBackend(recognizer, one_line=reader == "after")
            rows, skipped = [], []
            for sample in samples:
                frame = load_frame(sample)
                if frame is None:
                    skipped.append(sample["id"])
                    continue
                rows.append({**sample, "image": str(sample["image"]), "read": read(sample, frame, backend, nodes, reader)})
            entry["readers"][reader] = {"fields": score(rows), "guard": guard_replay(rows), "skipped": skipped}
        entry["input_attempts"] = recognizer.input_attempts
        report["input_attempts"] += recognizer.input_attempts or 0
    report["seconds"] = round(time.time() - started, 1)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"samples {len(samples)} (unlabelled eval frames: {len(unlabelled)}), {report['seconds']} s")
    print(f"{'model':16} {'reader':7} {'field':10} {'right':>9} {'wrong':>5} {'unread':>6}")
    for model, entry in report["models"].items():
        for reader, data in entry["readers"].items():
            for field, s in data["fields"].items():
                print(f"{model:16} {reader:7} {field:10} {s['right']:>4}/{s['n']:<4} {s['wrong']:>5} {s['unread']:>6}")
            g = data["guard"]
            print(f"{model:16} {reader:7} guard: wrong passed {g['wrong_passed']}, wrong caught {g['wrong_caught']}, "
                  f"frames held {g['frames_held']}/{g['frames']}")
    print(f"report: {args.report}")
    return 0 if report["input_attempts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
