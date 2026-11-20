#!/usr/bin/env python3
"""Grade Fes 识别：档案校验、Maa 模板/颜色识别、参加权数字 OCR 与难度页文字 OCR；只读，从不输入。"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.grade_flow import (ANCHORS, COLOR_CHECKS, EXCLUSIONS, GUARD_STAGES, OCR_FIELDS, PREFIX, RIGHTS_STAGES,
                                   TEXT_BOXES, THRESHOLD, exclusion_scene, exclusions_clear, scene_name, stage_matches)
from tools.replay_ocr import MaaOcrRecognizer, decide_field
from tools.replay_screenshots import require, template_path, validate_manifest

PRIVATE = ROOT / "dev_assets/private"
DEFAULT_PROFILE = PRIVATE / "in_app_browser/20261002-grade/grade-native-v1"
DEFAULT_MODEL = PRIVATE / "models/ppocr_v6_small"
NAVIGATION_PROFILE = PRIVATE / "in_app_browser/20261001-daily-research/nav-native-v27"
OCR_SCALE, OCR_PADDING, OCR_CUTOFF = 3, 6, 128


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def expected_scenes():
    scenes = {scene_name(g, a): ("TemplateMatch", roi) for g, anchors in ANCHORS.items() for a, roi in anchors.items()}
    scenes.update({exclusion_scene(n): ("TemplateMatch", roi) for n, roi in EXCLUSIONS.items()})
    scenes.update({scene_name(g, n): ("ColorMatch", rule) for g, checks in COLOR_CHECKS.items() for n, rule in checks.items()})
    return scenes


def template_file_name(name):
    return name.removeprefix(PREFIX).replace(".", "-") + ".png"


def load_profile(manifest, sources):
    """Bind every fixed ROI, colour rule, template and replay sample to private file hashes."""
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    private = PRIVATE.resolve()
    require(manifest.is_relative_to(private) and sources.is_relative_to(private), "Grade档案只允许private目录")
    data = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")), manifest.parent)
    origin = json.loads(sources.read_text(encoding="utf-8"))
    expected = expected_scenes()
    require(len(data["scenes"]) == len(expected) and {s["name"] for s in data["scenes"]} == set(expected),
            "Grade档案场景必须完整且唯一")
    templates = {t["template"]: t for t in origin["templates"]}
    require(len(templates) == sum(1 for kind, _ in expected.values() if kind == "TemplateMatch"), "Grade模板来源必须完整且唯一")

    def check_origin(item):
        path = Path(item.get("source", ""))
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        require(path.is_relative_to(private) and path.is_file() and sha256(path.read_bytes()) == item.get("source_sha256"),
                "Grade原始截图缺失或哈希不符")

    for scene in data["scenes"]:
        kind, rule = expected[scene["name"]]
        if kind == "ColorMatch":
            require(scene == {"name": scene["name"], "recognition": "ColorMatch", "roi": rule["roi"], "lower": rule["lower"],
                              "upper": rule["upper"], "count": rule["count"]}, "禁止更改Grade颜色守卫")
            continue
        require(scene["recognition"] == "TemplateMatch" and scene["threshold"] == THRESHOLD and scene["roi"] == rule,
                "禁止更改Grade ROI或阈值")
        require(scene["template"] == template_file_name(scene["name"]) and scene["template"] in templates, "Grade模板名称或来源变化")
        require(sha256(template_path(scene, manifest.parent).read_bytes()) == templates[scene["template"]]["template_sha256"],
                "Grade模板哈希不符")
        check_origin(templates[scene["template"]])
    truth = {s["derived"]: s for s in origin["samples"]}
    require(len(truth) == len(data["samples"]) and set(truth) == {s["image"] for s in data["samples"]}, "Grade样本来源不完整")
    for sample in data["samples"]:
        item = truth[sample["image"]]
        require(item["stage"] in GUARD_STAGES and type(item["expected_guard"]) is bool, "Grade阶段真值无效")
        require(sha256((manifest.parent / sample["image"]).read_bytes()) == item["derived_sha256"], "Grade样本发生变化")
        check_origin(item)
    for stage in GUARD_STAGES:
        rows = [s for s in truth.values() if s["stage"] == stage]
        require(any(s["expected_guard"] for s in rows) and any(not s["expected_guard"] for s in rows), "每个Grade阶段必须有正负样本")
    return data, truth, sha256(manifest.read_bytes() + sources.read_bytes())


def ocr_pixels(name, image):
    """Fixed crop → (255 − mean) → normalise → threshold 128 → bilinear ×3 → pad 6; refuses low contrast."""
    import numpy as np
    from tools.run_daily_training import resize_numeric
    require(name in OCR_FIELDS and image.shape == (720, 1280, 3), "Grade OCR字段或画布无效")
    (x, y, w, h), kind, _ = OCR_FIELDS[name]
    crop = image[y:y + h, x:x + w].astype(float)
    raw = 255 - crop.mean(axis=2) if kind == "light" else crop.mean(axis=2)
    contrast = float(raw.max() - raw.min())
    info = {"crop": [x, y, w, h], "kind": kind, "crop_sha256": sha256(image[y:y + h, x:x + w].tobytes())}
    if contrast < 20:
        return None, {**info, "reason": "blank_or_low_contrast"}
    gray = (raw - raw.min()) * 255 / contrast
    binary = np.where(gray > OCR_CUTOFF, 255, 0)
    padded = np.pad(resize_numeric(binary, OCR_SCALE), ((OCR_PADDING,) * 2,) * 2, constant_values=255)
    pixels = np.repeat(padded[:, :, None], 3, axis=2).astype(np.uint8)
    return pixels, {**info, "processed_sha256": sha256(pixels.tobytes()), "scale": OCR_SCALE, "padding": OCR_PADDING,
                    "cutoff": OCR_CUTOFF}


class GradeOcr(MaaOcrRecognizer):
    """MaaFramework OCR: only_rec on the fixed rights crop; detector+recogniser on fixed text boxes."""
    def __init__(self, model_dir=DEFAULT_MODEL):
        super().__init__(model_dir)
        self.model["grade_preprocessing"] = {"version": 1, "fields": {k: v[:2] for k, v in OCR_FIELDS.items()},
            "text_boxes": TEXT_BOXES, "scale": OCR_SCALE, "padding": OCR_PADDING, "cutoff": OCR_CUTOFF,
            "only_rec": True, "threshold": THRESHOLD}

    def read(self, name, image):
        from maa.pipeline import JOCR, JRecognitionType
        roi, _, pattern = OCR_FIELDS[name]
        field = {"name": PREFIX + "OCR." + name, "roi": roi, "expected": pattern, "threshold": THRESHOLD}
        pixels, processing = ocr_pixels(name, image)
        if pixels is None:
            recognition = {"hit": False, "results": [], "raw_detail": {}}
        else:
            height, width = pixels.shape[:2]
            param = JOCR(expected=[pattern], roi=[0, 0, width, height], threshold=THRESHOLD, only_rec=True)
            detail = self.tasker.post_recognition(JRecognitionType.OCR, param, pixels).wait().get()
            require(detail is not None and len(detail.nodes) == 1 and detail.nodes[0].recognition is not None,
                    "Maa Grade OCR未返回唯一详情")
            result = detail.nodes[0].recognition
            require(len(result.all_results) <= 1 and all(list(r.box) == [0, 0, width, height] for r in result.all_results),
                    "Grade OCR候选或处理区域不唯一")
            recognition = {"hit": result.hit, "raw_detail": result.raw_detail,
                           "results": [{"text": r.text, "score": r.score, "box": list(roi)} for r in result.all_results]}
        decision = decide_field(field, recognition)
        text = decision["text"] if decision["accepted"] and decision.get("box") == roi else None
        if text is not None:
            text = re.sub(r"\s+", "", text)
            if re.fullmatch(pattern, text) is None:
                text = None
        return text, {"field": field, "recognition": recognition, "decision": decision, "preprocessing": processing}


def read_text(ocr, roi, image):
    from tools.shop_recognition import read_text_box
    return read_text_box(ocr, roi, image)


def recognize_all(image, reco, data, base):
    return [reco.recognize(s, image, base) if s["recognition"] == "TemplateMatch" else reco.recognize(s, image)
            for s in data["scenes"]]


def observe(stage, image, reco, data, base, ocr=None):
    """Classify one fresh frame for ``stage``: anchors, colour checks, exclusions and (optional) OCR."""
    require(stage in GUARD_STAGES, "Grade观察阶段无效")
    hits = recognize_all(image, reco, data, base)
    matched, clear = stage_matches(stage, hits), exclusions_clear(stage, hits)
    observed = {"stage": stage, "recognitions": hits, "anchors_matched": matched, "exclusions_clear": clear,
                "guard_verified": matched and clear}
    if not observed["guard_verified"] or ocr is None:
        return observed
    texts, details, names, name_detail = {}, {}, {}, {}
    if stage in RIGHTS_STAGES:
        for name in OCR_FIELDS:
            texts[name], details[name] = ocr.read(name, image)
    if stage == "Difficulty":
        for key, roi in TEXT_BOXES.items():
            names[key], name_detail[key] = read_text(ocr, roi, image)
    observed.update(ocr_texts=texts, ocr=details, names=names, name_ocr=name_detail)
    return observed
