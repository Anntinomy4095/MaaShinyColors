#!/usr/bin/env python3
"""Inspect private screenshot ROIs through recognition only, at native pixels."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

try:
    from tools.replay_ocr import MaaOcrRecognizer, decide_field, model_metadata
    from tools.replay_screenshots import CANVAS, ROOT, png_dimensions, read_png, require
except ModuleNotFoundError:
    from replay_ocr import MaaOcrRecognizer, decide_field, model_metadata
    from replay_screenshots import CANVAS, ROOT, png_dimensions, read_png, require

PRIVATE = ROOT / "dev_assets/private"


def private_path(path):
    resolved = Path(path).resolve()
    require(resolved.is_relative_to(PRIVATE.resolve()), "路径须位于dev_assets/private内")
    return resolved


def validate_paths(image, manifest, report):
    image, manifest, report = (private_path(p) for p in (image, manifest, report))
    require(image.is_file() and image.suffix.lower() == ".png", "输入须为私有PNG截图")
    require(manifest.is_file() and manifest.suffix.lower() == ".json", "清单须为私有JSON")
    require(report.suffix.lower() == ".json" and report.name.lower() != "session.json", "报告须为私有JSON且不可为session.json")
    require(report not in (image, manifest) and not report.exists(), "报告不可覆盖现有文件、截图或清单")
    return image, manifest, report


def validate_manifest(data, image):
    require(isinstance(data, dict) and set(data) == {"version", "image_sha256", "dimensions", "fields"}, "清单仅允许版本、截图哈希、尺寸和字段")
    require(type(data["version"]) is int and data["version"] == 1, "仅支持版本1")
    digest = data["image_sha256"]
    require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest) is not None, "截图哈希须为小写SHA256")
    require(hashlib.sha256(image.read_bytes()).hexdigest() == digest, "截图哈希不匹配")
    dimensions = data["dimensions"]
    require(isinstance(dimensions, list) and len(dimensions) == 2 and all(type(v) is int for v in dimensions), "尺寸须为两个整数")
    width, height = dimensions
    require(0 < width <= CANVAS[0] and 0 < height <= CANVAS[1] and tuple(dimensions) == png_dimensions(image), "原图尺寸不匹配或超出支持范围")
    require(isinstance(data["fields"], list) and data["fields"], "字段不可为空")
    names = set()
    for field in data["fields"]:
        require(isinstance(field, dict) and set(field) == {"name", "roi", "expected", "threshold"}, "字段仅允许name、roi、expected、threshold；拒绝动作和Pipeline")
        name = field["name"]
        require(isinstance(name, str) and name.startswith("SC.") and 3 < len(name) <= 128 and name not in names, "字段须使用唯一SC.名称")
        names.add(name)
        roi = field["roi"]
        require(isinstance(roi, list) and len(roi) == 4 and all(type(v) is int for v in roi), "ROI须为四个原图整数坐标")
        x, y, w, h = roi
        require(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= width and y + h <= height, "ROI超出原图")
        threshold = field["threshold"]
        require(type(threshold) in (int, float) and math.isfinite(threshold) and 0.95 <= threshold <= 1, "OCR阈值须为[0.95,1]的有限数")
        pattern = field["expected"]
        require(isinstance(pattern, str) and 2 <= len(pattern) <= 256 and pattern.startswith("^") and pattern.endswith("$"), "expected须为以^和$锚定的正则")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("expected正则无效") from exc
    return data


def load_manifest(path):
    def strict_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON拒绝重复字段")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)


def inspect_capture(data, image_path, recognizer, decoded=None):
    validate_manifest(data, image_path)
    image = decoded if decoded is not None else read_png(image_path, expected_size=None)
    width, height = data["dimensions"]
    require(tuple(image.shape[:2]) == (height, width), "解码尺寸必须保持原图尺寸")
    fields = []
    for field in data["fields"]:
        recognition = recognizer.recognize(field, image)
        decision = decide_field(field, recognition)
        decision.update(roi=field["roi"], expected=field["expected"], threshold=field["threshold"], results=recognition["results"])
        for item in recognition["results"]:
            box = item.get("box")
            valid = isinstance(box, (list, tuple)) and len(box) == 4 and all(type(v) is int for v in box)
            if valid:
                x, y, w, h = box
                valid = x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= width and y + h <= height
            if not valid:
                decision.update(accepted=False, text=None, reason="box_coordinate_mismatch")
        fields.append(decision)
    require(hashlib.sha256(image_path.read_bytes()).hexdigest() == data["image_sha256"], "识别期间截图发生变化")
    attempts = getattr(recognizer, "input_attempts", None)
    require(type(attempts) is int and attempts == 0, "只读检查要求运行时input_attempts严格等于0")
    return {"inspection_complete": True, "canonical_ready": (width, height) == CANVAS,
            "pipeline_eligible": False, "readonly": True, "input_enabled": False,
            "input_attempts": attempts, "image_sha256": data["image_sha256"],
            "dimensions": data["dimensions"], "coordinate_space": "source_pixels_no_resize",
            "framework_version": recognizer.version, "model": recognizer.model,
            "score_kind": "ocr_score_not_probability", "fields": fields}


def main(argv=None):
    parser = argparse.ArgumentParser(description="私有截图只读ROI研究；非规范截图不可进入Pipeline。")
    for name in ("image", "manifest", "model-dir", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    report_path = None
    report = {"inspection_complete": False, "canonical_ready": False, "pipeline_eligible": False, "readonly": True, "input_enabled": False}
    try:
        image, manifest, report_path = validate_paths(args.image, args.manifest, args.report)
        manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        data = validate_manifest(load_manifest(manifest), image)
        decoded = read_png(image, expected_size=None)
        model_metadata(args.model_dir)
        report = inspect_capture(data, image, MaaOcrRecognizer(args.model_dir), decoded)
        require(hashlib.sha256(manifest.read_bytes()).hexdigest() == manifest_digest, "检查期间清单发生变化")
        report["manifest_sha256"] = manifest_digest
        code = 0
    except Exception as exc:
        report.update(inspection_complete=False, error=str(exc), error_type=type(exc).__name__)
        code = 2
    if report_path is not None:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        except Exception:
            code = 2
            report["inspection_complete"] = False
            report["error_type"] = "ReportWriteError"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"inspection_complete": report["inspection_complete"], "canonical_ready": report["canonical_ready"], "report": str(report_path) if report_path else None, "error": report.get("error_type")}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
