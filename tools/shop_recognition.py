#!/usr/bin/env python3
"""每日商店识别：档案校验、Maa模板识别与固定裁剪数字OCR；只读，从不输入。"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.shop_flow import (RESULT_SENTENCE_ROI, ANCHORS, BUTTON_COLOR, monthly_roi, monthly_scene, button_roi, button_scene, CONFIRM_NAME_ROI, CURRENCY_PROMPT_ROI, daily_slots, normalize_name, EXCLUSIONS, GUARD_STAGES, ITEMS, OCR_FIELDS,
                                  OFFER_RANKS, PREFIX, THRESHOLD, anchor_stage, exclusion_scene, exclusions_clear,
                                  identify_item, item_scene, list_name_roi, scene_name, stage_matches)
from tools.replay_ocr import MaaOcrRecognizer, decide_field
from tools.replay_screenshots import require, template_path, validate_manifest

PRIVATE = ROOT / "dev_assets/private"
DEFAULT_PROFILE = PRIVATE / "in_app_browser/20261002-shop/shop-native-v1"
DEFAULT_MODEL = PRIVATE / "models/ppocr_v6_small"
OCR_SCALE, OCR_PADDING, OCR_CUTOFF = 3, 6, 128


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def expected_scenes():
    scenes = {scene_name(stage, anchor): roi for stage in GUARD_STAGES for anchor, roi in ANCHORS[stage].items()}
    scenes.update({exclusion_scene(name): roi for name, roi in EXCLUSIONS.items()})
    scenes.update({button_scene(p): button_roi(p) for p in OFFER_RANKS})
    scenes.update({monthly_scene(p): monthly_roi(p) for p in OFFER_RANKS})
    for item in ITEMS:
        scenes[item_scene(item, "Confirm")] = CONFIRM_NAME_ROI
        scenes[item_scene(item, "List")] = None  # Observed rank; matched at every rank's ROI.
    return scenes


def template_file_name(name):
    rest = name.removeprefix(PREFIX)
    if rest.startswith("Item."):
        _, item, where = rest.split(".")
        return f"Item-{item}-{where}.png"
    return rest.replace(".", "-") + ".png"


def load_profile(manifest, sources):
    """Bind every fixed ROI, template and replay sample to private file hashes."""
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    private = PRIVATE.resolve()
    require(manifest.is_relative_to(private) and sources.is_relative_to(private), "商店档案只允许private目录")
    data = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")), manifest.parent)
    origin = json.loads(sources.read_text(encoding="utf-8"))
    expected = expected_scenes()
    require(len(data["scenes"]) == len(expected) and {s["name"] for s in data["scenes"]} == set(expected),
            "商店档案场景必须完整且唯一")
    templates = {t["template"]: t for t in origin["templates"]}
    require(len(templates) == len(expected) - len(OFFER_RANKS), "商店模板来源必须完整且唯一")

    def check_origin(item):
        path = Path(item.get("source", ""))
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        require(path.is_relative_to(private) and path.is_file()
                and sha256(path.read_bytes()) == item.get("source_sha256"), "商店原始截图缺失或哈希不符")

    list_rois = [list_name_roi(rank) for rank in OFFER_RANKS]
    for scene in data["scenes"]:
        roi = expected[scene["name"]]
        if scene["name"].startswith(PREFIX + "List.ButtonEnabled"):
            require(scene == {"name": scene["name"], "recognition": "ColorMatch", "roi": roi, "lower": BUTTON_COLOR["lower"],
                              "upper": BUTTON_COLOR["upper"], "count": BUTTON_COLOR["count"]}, "禁止更改按钮颜色守卫")
            continue
        require(scene["recognition"] == "TemplateMatch" and scene["threshold"] == THRESHOLD
                and (scene["roi"] == roi if roi is not None else scene["roi"] in list_rois), "禁止更改商店ROI或阈值")
        require(scene["template"] == template_file_name(scene["name"]) and scene["template"] in templates,
                "商店模板名称或来源变化")
        require(sha256(template_path(scene, manifest.parent).read_bytes()) == templates[scene["template"]]["template_sha256"],
                "商店模板哈希不符")
        check_origin(templates[scene["template"]])
    truth = {s["derived"]: s for s in origin["samples"]}
    require(len(truth) == len(data["samples"]) and set(truth) == {s["image"] for s in data["samples"]}, "商店样本来源不完整")
    for sample in data["samples"]:
        item = truth[sample["image"]]
        require(item["stage"] in GUARD_STAGES + ("After",) and type(item["expected_guard"]) is bool, "商店阶段真值无效")
        require(sha256((manifest.parent / sample["image"]).read_bytes()) == item["derived_sha256"], "商店样本发生变化")
        check_origin(item)
    for stage in GUARD_STAGES:
        rows = [s for s in truth.values() if s["stage"] == stage]
        require(any(s["expected_guard"] for s in rows) and any(not s["expected_guard"] for s in rows),
                "每个商店阶段必须有正负样本")
    digest = sha256(manifest.read_bytes() + sources.read_bytes())
    return data, truth, digest


def ocr_pixels(name, image):
    """Fixed crop → channel → normalise → threshold → bilinear ×3 → pad; refuses low contrast."""
    import numpy as np
    from tools.run_daily_training import resize_numeric
    require(name in OCR_FIELDS and image.shape == (720, 1280, 3), "商店OCR字段或画布无效")
    (x, y, w, h), kind, _ = OCR_FIELDS[name]
    crop = image[y:y + h, x:x + w].astype(float)  # BGR
    raw = 255 - crop[:, :, 0] if kind == "blue_inv" else 255 - crop.mean(axis=2) if kind == "light" else crop.mean(axis=2)
    contrast = float(raw.max() - raw.min())
    info = {"crop": [x, y, w, h], "kind": kind, "crop_sha256": sha256(image[y:y + h, x:x + w].tobytes())}
    if contrast < 20:
        return None, {**info, "reason": "blank_or_low_contrast"}
    gray = (raw - raw.min()) * 255 / contrast
    binary = np.where(gray > OCR_CUTOFF, 255, 0)
    padded = np.pad(resize_numeric(binary, OCR_SCALE), ((OCR_PADDING,) * 2,) * 2, constant_values=255)
    pixels = np.repeat(padded[:, :, None], 3, axis=2).astype(np.uint8)
    return pixels, {**info, "processed_sha256": sha256(pixels.tobytes()), "scale": OCR_SCALE,
                    "padding": OCR_PADDING, "cutoff": OCR_CUTOFF}


class ShopOcr(MaaOcrRecognizer):
    """MaaFramework OCR (only_rec) on fixed crops; no detector and no guessed values."""
    def __init__(self, model_dir=DEFAULT_MODEL):
        super().__init__(model_dir)
        self.model["shop_numeric_preprocessing"] = {"version": 1, "fields": {k: v[:2] for k, v in OCR_FIELDS.items()},
            "scale": OCR_SCALE, "padding": OCR_PADDING, "cutoff": OCR_CUTOFF, "only_rec": True, "threshold": THRESHOLD}

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
                    "Maa商店OCR未返回唯一详情")
            result = detail.nodes[0].recognition
            require(len(result.all_results) <= 1 and all(list(r.box) == [0, 0, width, height] for r in result.all_results),
                    "商店OCR候选或处理区域不唯一")
            recognition = {"hit": result.hit, "raw_detail": result.raw_detail,
                           "results": [{"text": r.text, "score": r.score, "box": list(roi)} for r in result.all_results]}
        decision = decide_field(field, recognition)
        text = decision["text"] if decision["accepted"] and decision.get("box") == roi else None
        if text is not None:
            text = re.sub(r"\s+", "", text)
            if re.fullmatch(pattern, text) is None:
                text = None
        return text, {"field": field, "recognition": recognition, "decision": decision, "preprocessing": processing}


def read_text_box(ocr, roi, image):
    """Japanese text OCR (detector + recogniser) on one fixed box; every line must score >= 0.95.

    Lines are ordered top-to-bottom (same row within 10 px), then left-to-right, joined and
    normalised (NFKC, no whitespace). Returns (text or None, detail).
    """
    from maa.pipeline import JOCR, JRecognitionType
    param = JOCR(expected=[".+"], roi=list(roi), threshold=THRESHOLD)
    detail = ocr.tasker.post_recognition(JRecognitionType.OCR, param, image).wait().get()
    require(detail is not None and len(detail.nodes) == 1 and detail.nodes[0].recognition is not None,
            "Maa名称OCR未返回唯一详情")
    rows = [{"text": r.text, "score": r.score, "box": list(r.box)} for r in detail.nodes[0].recognition.all_results]
    x, y, w, h = roi
    inside = all(b[0] >= x and b[1] >= y and b[0] + b[2] <= x + w and b[1] + b[3] <= y + h for b in (r["box"] for r in rows))
    ok = bool(rows) and inside and all(isinstance(r["text"], str) and r["text"].strip() and r["score"] >= THRESHOLD for r in rows)
    lines = []
    for row in sorted(rows, key=lambda r: r["box"][1] + r["box"][3] / 2):
        center = row["box"][1] + row["box"][3] / 2
        if lines and abs(lines[-1][0] - center) < 10:
            lines[-1][1].append(row)
        else:
            lines.append([center, [row]])
    text = "".join(r["text"] for _, line in lines for r in sorted(line, key=lambda r: r["box"][0]))
    text = normalize_name(text) if ok else None
    return text or None, {"roi": list(roi), "results": rows, "inside_roi": inside, "accepted": text or None}


def observe(stage, image, reco, data, base, ocr=None):
    """Classify one fresh frame for ``stage``: anchors, exclusions, identity and (optional) OCR."""
    require(stage in GUARD_STAGES + ("After",), "商店观察阶段无效")
    by_name = {s["name"]: s for s in data["scenes"]}
    hits = [reco.recognize(s, image, base) if s["recognition"] == "TemplateMatch" else reco.recognize(s, image)
            for s in data["scenes"] if not s["name"].startswith(PREFIX + "Item.")]
    slots = daily_slots(hits) if anchor_stage(stage) == "List" else None
    # A List page is only proved when its daily-slot layout is consistent (no covered/grey card).
    guard = (stage_matches(stage, hits) and exclusions_clear(stage, hits)
             and (anchor_stage(stage) != "List" or slots is not None))
    observed = {"stage": stage, "recognitions": hits, "anchors_matched": stage_matches(stage, hits),
                "exclusions_clear": exclusions_clear(stage, hits), "guard_verified": guard}
    if anchor_stage(stage) == "List":
        observed["daily_slots"] = slots
    if not guard:
        return observed
    group = anchor_stage(stage)
    identity, identity_detail = {}, {}

    def identify(key, roi, where):
        results = {}
        for item in ITEMS:
            scene = dict(by_name[item_scene(item, where)], roi=list(roi))
            results[item] = reco.recognize(scene, image, base)
        identity[key] = identify_item(results, roi)
        identity_detail[key] = {item: {"score": r.get("score"), "hit": r.get("hit")} for item, r in results.items()}

    if group == "List":
        for rank in OFFER_RANKS:
            identify(str(rank), list_name_roi(rank), "List")
    elif group == "Confirm":
        identify("Confirm", CONFIRM_NAME_ROI, "Confirm")
    observed.update(identity=identity, identity_detail=identity_detail)
    if ocr is not None:
        names, name_detail = {}, {}
        if group == "List":
            for rank in OFFER_RANKS:
                names[str(rank)], name_detail[str(rank)] = read_text_box(ocr, list_name_roi(rank), image)
        elif group == "Confirm":
            names["Confirm"], name_detail["Confirm"] = read_text_box(ocr, CONFIRM_NAME_ROI, image)
            names["CurrencyPrompt"], name_detail["CurrencyPrompt"] = read_text_box(ocr, CURRENCY_PROMPT_ROI, image)
        else:
            names["ResultSentence"], name_detail["ResultSentence"] = read_text_box(ocr, RESULT_SENTENCE_ROI, image)
        observed.update(names=names, name_ocr=name_detail)
        texts, details = {}, {}
        for name in OCR_FIELDS:
            if name.startswith(group + "."):
                texts[name], details[name] = ocr.read(name, image)
        observed.update(ocr_texts=texts, ocr=details)
    return observed


def identity_of(observed, key):
    """Identity evidence for one card ("1".."3") or the dialog ("Confirm"): catalog match + OCR name."""
    return {"catalog": observed.get("identity", {}).get(key), "name": observed.get("names", {}).get(key)}


def confirm_evidence_of(observed):
    """Numeric dialog evidence plus the OCR'd currency prompt ("マニー を消費して…")."""
    from agent.core.shop_flow import confirm_evidence
    evidence = confirm_evidence(observed["ocr_texts"])
    evidence["currency_prompt"] = observed.get("names", {}).get("CurrencyPrompt")
    return evidence


def slots_of(observed):
    """Visible leading 本日限定 slots with their identity evidence (catalog match + OCR name)."""
    count = observed.get("daily_slots")
    require(type(count) is int, "本日限定槽位布局不一致（灰色按钮、缺横幅或间断），停止")
    return {"count": count, "cards": {p: identity_of(observed, str(p)) for p in range(1, count + 1)}}


def result_evidence_of(observed):
    from agent.core.shop_flow import result_evidence
    return result_evidence(observed["ocr_texts"], observed.get("names", {}).get("ResultSentence"))
