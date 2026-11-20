#!/usr/bin/env python3
"""Offline OCR field replay through Maa post_recognition; never execute input."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

try:
    from tools.replay_screenshots import CANVAS, ROOT, png_dimensions, read_png, require
except ModuleNotFoundError:
    from replay_screenshots import CANVAS, ROOT, png_dimensions, read_png, require

MODEL_FILES = ("det.onnx", "rec.onnx", "keys.txt")


def image_path(name, base):
    require(isinstance(name, str) and bool(name), "图像路径无效")
    relative = Path(name)
    require(not relative.is_absolute() and not relative.drive, "图像不允许绝对路径")
    base = Path(base).resolve()
    path = (base / relative).resolve()
    require(path.is_relative_to(base) and path.suffix.lower() == ".png" and path.is_file(), "图像须为清单目录内的现有PNG")
    return path


def validate_manifest(data, base):
    """Validate the strict data-only manifest before importing Maa or numpy."""
    require(isinstance(data, dict) and set(data) == {"version", "canvas", "fields", "samples"}, "清单字段无效")
    require(type(data["version"]) is int and data["version"] == 1, "仅支持版本1")
    require(isinstance(data["canvas"], list) and all(type(v) is int for v in data["canvas"]) and data["canvas"] == list(CANVAS), "仅支持1280x720画布")
    require(isinstance(data["fields"], list) and data["fields"], "OCR字段不能为空")
    names = set()
    for field in data["fields"]:
        require(isinstance(field, dict) and set(field) == {"name", "roi", "expected", "threshold"}, "OCR字段仅允许name、roi、expected、threshold")
        name = field["name"]
        require(isinstance(name, str) and name.startswith("SC.") and 3 < len(name) <= 128 and name not in names, "OCR字段须使用唯一SC.名称")
        names.add(name)
        roi = field["roi"]
        require(isinstance(roi, list) and len(roi) == 4 and all(type(v) is int for v in roi), "ROI必须是四个整数")
        x, y, w, h = roi
        require(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 1280 and y + h <= 720, "ROI超出画布")
        threshold = field["threshold"]
        require(type(threshold) in (int, float) and math.isfinite(threshold) and 0 <= threshold <= 1, "OCR阈值须为有限数且在[0,1]内")
        pattern = field["expected"]
        require(isinstance(pattern, str) and 2 <= len(pattern) <= 256 and pattern.startswith("^") and pattern.endswith("$"), "expected须为最多256字符且以^和$锚定的正则表达式")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("expected正则表达式无效") from exc
    require(isinstance(data["samples"], list) and data["samples"], "样本不能为空")
    identifiers = set()
    for sample in data["samples"]:
        require(isinstance(sample, dict) and set(sample) == {"id", "image", "expected"}, "样本字段无效")
        identifier = sample["id"]
        require(isinstance(identifier, str) and 0 < len(identifier) <= 128 and identifier not in identifiers, "样本ID须唯一")
        identifiers.add(identifier)
        image_path(sample["image"], base)
        expected = sample["expected"]
        require(isinstance(expected, dict) and set(expected) == names, "样本expected须覆盖所有OCR字段")
        require(all(v is None or (isinstance(v, str) and len(v) <= 512) for v in expected.values()), "样本期望值须为字符串或null")
    return data


def model_metadata(model_dir):
    directory = Path(model_dir).resolve()
    require(directory.is_dir(), "必须提供OCR模型目录")
    files = {}
    for name in MODEL_FILES:
        path = (directory / name).resolve()
        require(path.is_relative_to(directory) and path.is_file() and path.stat().st_size > 0, "OCR模型缺少目录内的非空" + name)
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"files": files}


class MaaOcrRecognizer:
    """Use only an isolated resource's OCR model and post_recognition."""
    def __init__(self, model_dir):
        self.model = model_metadata(model_dir)
        from maa.controller import CustomController
        from maa.define import LoggingLevelEnum
        from maa.library import Library
        from maa.resource import Resource
        from maa.tasker import Tasker

        class ScreenshotOnlyController(CustomController):
            input_attempts = 0
            def connect(self): return True
            def request_uuid(self): return "SC.Offline.OcrReplay"
            def screencap(self): return None
            def deny(self, *args):
                self.input_attempts += 1
                return False
            start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = deny
            click_key = input_text = key_down = key_up = scroll = relative_move = deny
            def shell(self, *args):
                self.input_attempts += 1
                return None

        self.version = Library.version()
        require(self.version == "v5.12.3", "OCR回放要求固定MaaFramework v5.12.3")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        self.controller = ScreenshotOnlyController()
        require(self.controller.post_connection().wait().succeeded, "只读截图控制器初始化失败")
        self.resource = Resource()
        require(self.resource.post_ocr_model(Path(model_dir).resolve()).wait().succeeded, "OCR模型加载失败")
        self.tasker = Tasker()
        require(self.tasker.bind(self.resource, self.controller) and self.tasker.inited, "OCR识别器绑定失败")

    @property
    def input_attempts(self):
        return self.controller.input_attempts

    def recognize(self, field, image):
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[field["expected"]], roi=field["roi"], threshold=field["threshold"])
        detail = self.tasker.post_recognition(JRecognitionType.OCR, param, image).wait().get()
        require(detail is not None and len(detail.nodes) == 1 and detail.nodes[0].recognition is not None, "Maa未返回OCR识别详情")
        result = detail.nodes[0].recognition
        return {"hit": result.hit, "results": [{"text": item.text, "score": item.score, "box": list(item.box)} for item in result.all_results], "raw_detail": result.raw_detail}


def decide_field(field, recognition):
    output = {"name": field["name"], "accepted": False, "text": None, "score": None, "box": None, "reason": "no_match", "raw_detail": recognition["raw_detail"]}
    candidates = recognition["results"]
    if len(candidates) > 1:
        output["reason"] = "ambiguous_text"
        return output
    if not candidates:
        return output
    item = candidates[0]
    score = item.get("score")
    output.update(score=score if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1 else None, box=item.get("box"))
    text = item.get("text")
    if not isinstance(text, str) or len(text) > 512 or re.fullmatch(field["expected"], text) is None:
        output["reason"] = "unexpected_text"
    elif output["score"] is None or score < field["threshold"]:
        output["reason"] = "low_score"
    elif recognition["hit"]:
        output.update(accepted=True, text=text, reason="accepted")
    return output


def replay(data, base, recognizer):
    validate_manifest(data, base)
    samples = []
    metrics = {"samples": len(data["samples"]), "fields": 0, "expected_values": 0, "expected_refusals": 0, "false_accepts": 0, "wrong_values": 0, "false_refusals": 0}
    for sample in data["samples"]:
        path = image_path(sample["image"], base)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if png_dimensions(path) == CANVAS:
            image = read_png(path)
            fields = [decide_field(field, recognizer.recognize(field, image)) for field in data["fields"]]
        else:
            fields = [{"name": field["name"], "accepted": False, "text": None, "score": None, "box": None, "reason": "invalid_dimensions", "raw_detail": None} for field in data["fields"]]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "OCR回放期间样本发生变化")
        passed = all(field["text"] == sample["expected"][field["name"]] for field in fields)
        for field in fields:
            expected = sample["expected"][field["name"]]
            metrics["fields"] += 1
            metrics["expected_values" if expected is not None else "expected_refusals"] += 1
            if expected is None and field["accepted"]:
                metrics["false_accepts"] += 1
            elif expected is not None and not field["accepted"]:
                metrics["false_refusals"] += 1
            elif expected is not None and field["text"] != expected:
                metrics["wrong_values"] += 1
        samples.append({"id": sample["id"], "image": sample["image"], "image_sha256": digest, "fields": fields, "passed": passed})
    attempts = getattr(recognizer, "input_attempts", None)
    return {"readonly": True, "input_enabled": False, "input_attempts": attempts, "framework_version": recognizer.version, "model": recognizer.model, "score_kind": "ocr_score_not_probability", "passed": all(s["passed"] for s in samples) and attempts in (None, 0), "metrics": metrics, "samples": samples}


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地截图只读OCR字段回放；OCR分数不是概率。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True, help="含det.onnx、rec.onnx和keys.txt的OCR模型目录")
    parser.add_argument("--report", type=Path, default=ROOT / "dev_assets/private/ocr/report.json")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        manifest = args.manifest.resolve()
        data = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")), manifest.parent)
        report = replay(data, manifest.parent, MaaOcrRecognizer(args.model_dir))
        exit_code = 0 if report["passed"] else 1
    except Exception as exc:
        report = {"passed": False, "error": str(exc)}
        exit_code = 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(args.report), "error": report.get("error")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
