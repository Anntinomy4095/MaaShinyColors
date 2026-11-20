#!/usr/bin/env python3
"""Offline comparison of OCR model dirs on the exact numeric pixels used by run_daily_training.

``--wing [options]``: W.I.N.G. per-field number accuracy on the labelled private frames instead
(tools/wing_ocr_eval.py; ``--model`` may repeat to compare model dirs)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.replay_screenshots import read_png
from tools.run_daily_training import (LEVEL_VARIANTS, MaaNumericRecognizer, NUMERIC_CROPS,
                                      OCR_FIELDS, numeric_pixels)

PRIVATE = ROOT / "dev_assets/private"
BASE = PRIVATE / "in_app_browser/20261001-daily-research"
MODELS = {
    "current_ja_v3_det_v4": PRIVATE / "models/ja_ppocrv3_v4",
    "ppocr_v5_zh_cn": PRIVATE / "models/ppocr_v5_zh_cn",
    "ppocr_v6_medium": PRIVATE / "models/ppocr_v6_medium",
    "ppocr_v6_small": PRIVATE / "models/ppocr_v6_small",
}
REPORT = PRIVATE / "ocr-model-comparison-20261001.json"
THRESHOLD = 0.95
STAGE_FIELDS = {"Zero": ("inventory", "level", "remaining_exp"), "One": ("inventory", "level"),
                "Confirm": ("level",), "Result": ("level",), "After": ("inventory", "level", "remaining_exp")}


def stage_truth(inventory, levels, exp):
    """levels: dict stage -> level; inventory/exp: {stage: value}."""
    truth = {}
    for stage, level in levels.items():
        truth[stage] = {"level": level}
        if "inventory" in STAGE_FIELDS[stage]:
            truth[stage]["inventory"] = inventory[stage]
        if "remaining_exp" in STAGE_FIELDS[stage]:
            truth[stage]["remaining_exp"] = exp[stage]
    return truth


def build_samples():
    samples = []
    hist = stage_truth({"Zero": 22612, "One": 22612, "After": 22611},
                       {"Zero": 1, "One": 1, "Confirm": 1, "Result": 2, "After": 2},
                       {"Zero": 50, "After": 40})
    nat = stage_truth({"Zero": 22614, "One": 22614, "After": 22613},
                      {"Zero": 2, "One": 2, "Confirm": 2, "Result": 3, "After": 3},
                      {"Zero": 40, "After": 40})
    for group, truth in (("training-flow-20261001", hist), ("training-native-v6", nat)):
        for stage, fields in truth.items():
            samples.append((f"{group}/{stage}", BASE / group / f"{stage}-source.png", fields))
    samples.append(("training-native-v1/NativeZero", BASE / "training-native-v1/NativeZero-source.png",
                    {"inventory": 22614, "level": 2, "remaining_exp": 40}))
    return samples


def summarize(values, truth_text, wrong, below):
    scores = [v for v in values if v is not None]
    return {"n": len(values), "min": min(scores) if scores else None,
            "mean": sum(scores) / len(scores) if scores else None,
            "wrong_text": wrong, "below_0.95": below}


def read_text(recognizer, field, image, variant):
    """Run only_rec on the exact numeric pixels; return (text, score)."""
    name = field
    roi, pattern = OCR_FIELDS[name]
    spec = {"name": "SC.Training.OCR." + name, "roi": roi, "expected": pattern, "threshold": THRESHOLD}
    out = recognizer.recognize_variant(spec, name, image, variant)
    results = out["results"]
    if not results:
        return None, None, out["preprocessing"].get("reason", "no_text")
    return results[0]["text"], results[0]["score"], None


def normalize(name, text):
    return re.sub(r"^[xX×]\s*", "", text).strip() if text is not None else None


def evaluate_model(recognizer, samples):
    rows = []
    for sample_id, path, truth in samples:
        image = read_png(path)
        for name, expected in truth.items():
            if name == "level":
                items = []
                for variant in LEVEL_VARIANTS:
                    text, score, reason = read_text(recognizer, name, image, variant)
                    items.append({"variant": variant, "text": text, "score": score, "reason": reason})
                texts = {i["text"] for i in items}
                agree = len(texts) == 1 and None not in texts
                best = max((i["score"] for i in items if i["score"] is not None), default=None)
                rows.append({"sample": sample_id, "field": name, "truth": str(expected), "variants": items,
                             "text": items[0]["text"] if agree else None, "agree": agree,
                             "score": best, "accepted": agree and best is not None and best >= THRESHOLD})
            else:
                text, score, reason = read_text(recognizer, name, image, None)
                rows.append({"sample": sample_id, "field": name, "truth": str(expected), "text": text,
                             "score": score, "reason": reason,
                             "accepted": text is not None and score is not None and score >= THRESHOLD})
    return rows


def negatives(recognizer):
    import numpy as np
    out = []
    for label, value in (("white", 255), ("black", 0)):
        canvas = np.full((720, 1280, 3), value, dtype=np.uint8)
        for name in NUMERIC_CROPS:
            for variant in (LEVEL_VARIANTS if name == "level" else (None,)):
                pixels, _ = numeric_pixels(name, canvas, variant)
                out.append({"canvas": label, "field": name, "variant": variant,
                            "preprocessed_none": pixels is None, "text": None, "accepted": False})
    # Defensive: also push all-white processed-size pixels through the recognizer itself.
    white = np.full((720, 1280, 3), 255, dtype=np.uint8)
    for name, (roi, pattern) in OCR_FIELDS.items():
        pixels = np.full((NUMERIC_CROPS[name][3] * 3 + 12, NUMERIC_CROPS[name][2] * 3 + 12, 3), 255, np.uint8)
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[pattern], roi=[0, 0, pixels.shape[1], pixels.shape[0]], threshold=THRESHOLD, only_rec=True)
        detail = recognizer.tasker.post_recognition(JRecognitionType.OCR, param, pixels).wait().get()
        texts = [r.text for r in detail.nodes[0].recognition.all_results] if detail and detail.nodes else []
        out.append({"canvas": "white_processed_size", "field": name, "variant": None,
                    "model_texts": texts, "accepted": bool(texts) and detail.nodes[0].recognition.hit})
    return out


def stats(rows):
    table = {}
    for field in ("inventory", "level", "remaining_exp"):
        fr = [r for r in rows if r["field"] == field]
        if not fr:
            continue
        scores = [r["score"] for r in fr if r["score"] is not None]
        wrong = sum(1 for r in fr if r["text"] is not None and normalize(field, r["text"]) != r["truth"])
        entry = {"n": len(fr), "min": min(scores) if scores else None,
                 "mean": sum(scores) / len(scores) if scores else None, "wrong_text": wrong,
                 "below_0.95": sum(1 for r in fr if r["score"] is None or r["score"] < THRESHOLD),
                 "not_accepted": sum(1 for r in fr if not r["accepted"])}
        if field == "level":
            entry["all_variants_agree"] = all(r["agree"] for r in fr)
            entry["wrong_variant_reads"] = sum(1 for r in fr for v in r["variants"]
                                               if v["text"] is not None and v["text"] != r["truth"])
            vs = [v["score"] for r in fr for v in r["variants"] if v["score"] is not None]
            entry["variant_min"] = min(vs) if vs else None
        table[field] = entry
    return table


def main():
    if sys.argv[1:2] == ["--wing"]:
        from tools.wing_ocr_eval import main as wing_main
        return wing_main(sys.argv[2:])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    samples = build_samples()
    report = {"offline_only": True, "threshold": THRESHOLD, "models": {}}
    for label, directory in MODELS.items():
        entry = {"model_dir": str(directory.relative_to(ROOT))}
        try:
            recognizer = MaaNumericRecognizer(directory)
            entry["framework_version"] = recognizer.version
            entry["rows"] = evaluate_model(recognizer, samples)
            entry["stats"] = stats(entry["rows"])
            entry["negatives"] = negatives(recognizer)
            entry["negative_false_accepts"] = sum(1 for n in entry["negatives"] if n["accepted"])
            entry["input_attempts"] = recognizer.input_attempts
        except Exception as exc:  # record, do not work around
            entry["error"] = f"{type(exc).__name__}: {exc}"
        report["models"][label] = entry
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fmt = lambda v: "-" if v is None else f"{v:.4f}"
    print(f"{'model':22} {'field':14} {'n':>3} {'min':>7} {'mean':>7} {'wrong':>5} {'<.95':>4} extra")
    for label, entry in report["models"].items():
        if "error" in entry:
            print(f"{label:22} LOAD/RUN ERROR: {entry['error']}")
            continue
        for field, s in entry["stats"].items():
            extra = ""
            if field == "level":
                extra = f"agree={s['all_variants_agree']} var_min={fmt(s['variant_min'])} wrong_var={s['wrong_variant_reads']}"
            print(f"{label:22} {field:14} {s['n']:>3} {fmt(s['min']):>7} {fmt(s['mean']):>7} "
                  f"{s['wrong_text']:>5} {s['below_0.95']:>4} {extra}")
        print(f"{label:22} negatives false accepts: {entry['negative_false_accepts']}, input_attempts={entry['input_attempts']}")
    print(f"report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
