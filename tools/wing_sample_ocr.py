#!/usr/bin/env python3
"""Offline OCR feasibility check for the W.I.N.G. draft pipeline (read-only, never sends input).

Side-browser samples (dev_assets/private/wing_samples/browser-20261004/, JPEG, 350x197..800x450,
all 16:9 views of the same game canvas) are upscaled to the canonical 1280x720 canvas. Every
SC.Wing.Anchor.* node of assets/resource/base/pipeline/wing_draft.json (OCR or ColorMatch) is run
through MaaFramework v5.12.3 (PP-OCR v6 small) on every listed sample; the SC.Wing.Screen.* And
guards are then evaluated from those anchor results, giving per-anchor hit rates, per-screen recall
and cross-screen false positives. SC.Wing.Read.* nodes are scored against READ truth values in
tools/wing_ocr_samples.json. The report goes to dev_assets/private/wing_samples/ocr_report.json.


--dump <sample>... prints whole-frame OCR (text, score, 1280x720 box) for coordinate work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE = ROOT / "dev_assets/private"
DEFAULT_MODEL = PRIVATE / "models/ppocr_v6_small"
DEFAULT_REPORT = PRIVATE / "wing_samples/ocr_report.json"
PIPELINE = ROOT / "assets/resource/base/pipeline/wing_draft.json"
SAMPLE_MAP = ROOT / "tools/wing_ocr_samples.json"
CANVAS = (1280, 720)
ANCHOR = "SC.Wing.Anchor."
READ = "SC.Wing.Read."
SCREEN = "SC.Wing.Screen."


def is_canvas_ratio(size, tolerance=0.02):
    width, height = size
    return width > 0 and height > 0 and abs(width / height - 16 / 9) <= 16 / 9 * tolerance


def load_canvas(path):
    """Decode a JPEG/PNG sample and resize it to 1280x720 BGR (Maa's image order)."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as source:
        if not is_canvas_ratio(source.size):
            raise ValueError(f"not a 16:9 canvas sample: {Path(path).name} {source.size}")
        image = source.convert("RGB").resize(CANVAS, Image.LANCZOS)
    return np.ascontiguousarray(np.asarray(image)[:, :, ::-1])


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_draft(pipeline):
    """Draft contract: SC.Wing.* names, every node disabled, recognition only, ROIs inside 1280x720."""
    errors = []
    for name, node in pipeline.items():
        if not name.startswith("SC.Wing."):
            errors.append(f"{name}: name must start with SC.Wing.")
        if node.get("enabled") is not False:
            errors.append(f"{name}: must be enabled=false")
        if node.get("action", "DoNothing") != "DoNothing" or "next" in node or "on_error" in node:
            errors.append(f"{name}: draft nodes must not act or chain")
        if "template" in node:
            errors.append(f"{name}: draft must not reference templates (native frames not captured yet)")
        roi = node.get("roi")
        if roi is not None:
            x, y, w, h = roi
            if not (x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= CANVAS[0] and y + h <= CANVAS[1]):
                errors.append(f"{name}: roi outside 1280x720")
        for ref in node.get("all_of", []):
            if not isinstance(ref, str) or ref not in pipeline or not ref.startswith(ANCHOR):
                errors.append(f"{name}: all_of must reference SC.Wing.Anchor.* nodes ({ref})")
        if name.startswith(SCREEN) and node.get("recognition") != "And":
            errors.append(f"{name}: screen guards must be And nodes")
    return errors


def screen_guards(pipeline):
    return {name[len(SCREEN):]: list(node["all_of"]) for name, node in pipeline.items() if name.startswith(SCREEN)}


def sample_path(sample_map, sample_id):
    return ROOT / sample_map["sample_dir"] / sample_map["name_pattern"].format(id=sample_id)


def validate_sample_map(sample_map, pipeline):
    errors = []
    guards = screen_guards(pipeline)
    for kind in sample_map["kinds"]:
        if kind not in guards:
            errors.append(f"sample kind without screen guard: {kind}")
    for kind in guards:
        if kind not in sample_map["kinds"]:
            errors.append(f"screen guard without samples: {kind}")
    for anchor in sample_map.get("extra_anchor_samples", {}):
        if ANCHOR + anchor not in pipeline:
            errors.append(f"extra samples for unknown anchor: {anchor}")
    for field in sample_map["read_truth"]:
        if READ + field not in pipeline:
            errors.append(f"read truth for unknown node: {field}")
    return errors


def read_match(text, truth):
    """Exact match after removing OCR spacing."""
    return re.sub(r"\s+", "", text or "") == truth


class WingRecognizer:
    """Read-only wrapper over tools.replay_ocr.MaaOcrRecognizer (screenshot-only controller)."""

    def __init__(self, model_dir=DEFAULT_MODEL):
        from tools.replay_ocr import MaaOcrRecognizer
        self._ocr = MaaOcrRecognizer(model_dir)
        self.model = self._ocr.model
        self.version = self._ocr.version

    @property
    def input_attempts(self):
        return self._ocr.input_attempts

    def run(self, image, node):
        from maa.pipeline import JColorMatch, JOCR, JRecognitionType
        kind = node["recognition"]
        if kind == "OCR":
            param = JOCR(expected=list(node.get("expected", [])), roi=tuple(node["roi"]),
                         threshold=float(node.get("threshold", 0.3)),
                         replace=[list(pair) for pair in node.get("replace", [])])
            rtype = JRecognitionType.OCR
        elif kind == "ColorMatch":
            param = JColorMatch(lower=[node["lower"]], upper=[node["upper"]], roi=tuple(node["roi"]),
                                method=node.get("method", 4), count=node.get("count", 1),
                                connected=node.get("connected", False))
            rtype = JRecognitionType.ColorMatch
        else:
            raise ValueError(f"unsupported recognition {kind}")
        detail = self._ocr.tasker.post_recognition(rtype, param, image).wait().get()
        if detail is None or len(detail.nodes) != 1 or detail.nodes[0].recognition is None:
            raise RuntimeError("Maa returned no recognition detail")
        rec = detail.nodes[0].recognition
        texts = [{"text": r.text, "score": round(float(r.score), 3), "box": list(r.box)}
                 for r in rec.all_results if hasattr(r, "text")]
        count = max((int(getattr(r, "count", 0)) for r in rec.all_results), default=0) if kind == "ColorMatch" else None
        return {"hit": bool(rec.hit), "texts": texts, "count": count}


def evaluate(pipeline, sample_map, recognizer):
    guards = screen_guards(pipeline)
    anchors = sorted(name for name in pipeline if name.startswith(ANCHOR))
    extra = {ANCHOR + a: set(ids) for a, ids in sample_map.get("extra_anchor_samples", {}).items()}
    reads = {field: READ + field for field in sample_map["read_truth"]}
    hits = {}  # (sample, anchor) -> bool
    anchor_rows = {a: {"positives": [], "false_hits": []} for a in anchors}
    read_rows = {field: [] for field in reads}
    kind_of = {}
    for kind, ids in sample_map["kinds"].items():
        for sample in ids:
            kind_of.setdefault(sample, []).append(kind)
    extra_only = sorted({s for ids in extra.values() for s in ids} - set(kind_of))
    order = list(kind_of) + extra_only
    for number, sample in enumerate(order, 1):
        kinds = kind_of.get(sample, ["extra"])
        print(f"[{number}/{len(order)}] {kinds[0]} {sample}", file=sys.stderr, flush=True)
        image = load_canvas(sample_path(sample_map, sample))
        positive_anchors = {a for k in kinds for a in guards.get(k, [])}
        positive_anchors |= {a for a, ids in extra.items() if sample in ids}
        for anchor in anchors:
            result = recognizer.run(image, pipeline[anchor])
            hits[(sample, anchor)] = result["hit"]
            row = {"sample": sample, "kind": kinds[0], "hit": result["hit"],
                   "texts": [t["text"] for t in result["texts"]][:4], "count": result["count"]}
            if anchor in positive_anchors:
                anchor_rows[anchor]["positives"].append(row)
            elif result["hit"] and sample in kind_of:
                anchor_rows[anchor]["false_hits"].append(row)
        for field, node_name in reads.items():
            truth = sample_map["read_truth"][field].get(sample)
            if truth is None:
                continue
            result = recognizer.run(image, pipeline[node_name])
            texts = [t["text"] for t in result["texts"]]
            best = texts[0] if len(texts) == 1 else "".join(texts)
            for pattern, repl in pipeline[node_name].get("replace", []):
                best = re.sub(pattern, repl, best)
            read_rows[field].append({"sample": sample, "truth": truth, "texts": texts, "hit": result["hit"],
                                     "exact": result["hit"] and read_match(best, truth)})
    screens = {}
    for kind, refs in guards.items():
        own = sample_map["kinds"][kind]
        recall = sum(all(hits[(s, a)] for a in refs) for s in own)
        confused = sorted({k for s, ks in kind_of.items() if kind not in ks and all(hits[(s, a)] for a in refs) for k in ks})
        screens[kind] = {"anchors": refs, "recall": f"{recall}/{len(own)}", "recall_ratio": recall / len(own),
                         "false_positive_kinds": confused}
    anchor_summary = {}
    for anchor, rows in anchor_rows.items():
        pos = rows["positives"]
        anchor_summary[anchor] = {"hit_rate": f"{sum(r['hit'] for r in pos)}/{len(pos)}",
                                  "misses": [r for r in pos if not r["hit"]],
                                  "false_hits": rows["false_hits"]}
    read_summary = {field: {"exact": f"{sum(r['exact'] for r in rows)}/{len(rows)}", "rows": rows}
                    for field, rows in read_rows.items()}
    totals = {
        "samples": len(kind_of) + len(extra_only), "screens": len(guards), "anchors": len(anchors),
        "screens_full_recall": sum(v["recall_ratio"] == 1 for v in screens.values()),
        "screens_with_false_positive": sum(bool(v["false_positive_kinds"]) for v in screens.values()),
        "anchor_positive_hits": sum(sum(r["hit"] for r in v["positives"]) for v in anchor_rows.values()),
        "anchor_positives": sum(len(v["positives"]) for v in anchor_rows.values()),
        "anchor_false_hits": sum(len(v["false_hits"]) for v in anchor_rows.values()),
        "read_exact": sum(sum(r["exact"] for r in rows) for rows in read_rows.values()),
        "read_total": sum(len(rows) for rows in read_rows.values()),
    }
    return {"readonly": True, "input_attempts": getattr(recognizer, "input_attempts", None),
            "framework_version": getattr(recognizer, "version", None), "model": getattr(recognizer, "model", None),
            "canvas": list(CANVAS), "resize": "PIL LANCZOS upscale of side-browser JPEG to 1280x720",
            "summary": totals, "screens": screens, "anchors": anchor_summary, "reads": read_summary}


def dump(names, recognizer, sample_map):
    for name in names:
        path = Path(name)
        if not path.is_file():
            path = sample_path(sample_map, name)
        result = recognizer.run(load_canvas(path), {"recognition": "OCR", "roi": [0, 0, *CANVAS], "threshold": 0.0})
        print(f"== {path.name}")
        for item in result["texts"]:
            x, y, w, h = item["box"]
            print(f"  [{x},{y},{w},{h}] {item['score']:.2f} {item['text']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="W.I.N.G. 侧边浏览器样本 OCR 可行性检查（只读，不发送输入）")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--pipeline", type=Path, default=PIPELINE)
    parser.add_argument("--samples", type=Path, default=SAMPLE_MAP)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dump", nargs="+", metavar="SAMPLE", help="整帧 OCR 输出（1280x720 坐标），用于取坐标")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pipeline, sample_map = load_json(args.pipeline), load_json(args.samples)
    errors = validate_draft(pipeline) + validate_sample_map(sample_map, pipeline)
    if errors:
        print(json.dumps({"errors": errors}, ensure_ascii=False, indent=1))
        return 2
    recognizer = WingRecognizer(args.model_dir)
    if args.dump:
        dump(args.dump, recognizer, sample_map)
        return 0
    report = evaluate(pipeline, sample_map, recognizer)
    if report["input_attempts"]:
        raise RuntimeError("input attempted during OCR replay")
    report["pipeline_sha256"] = hashlib.sha256(Path(args.pipeline).read_bytes()).hexdigest()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"report": str(args.report), **report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
