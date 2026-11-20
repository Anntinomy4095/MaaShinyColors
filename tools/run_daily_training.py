#!/usr/bin/env python3
"""固定S卡单铜券监督训练：默认只读，逐阶段独立执行，不重试。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.browser_bridge import BrowserBridge
from agent.core.daily_config import load_json
from agent.core.foreground_guard import interpret_foreground
from agent.core.legacy_paths import legacy_private_path
from agent.core.training_flow import (ACTION_POINTS, CARD_ID, PREFIX, STAGES,
                                      fixed_pipeline, load_guard, stage_matches,
                                      validate_resource_evidence)
from tools.navigation_protocol import daily_navigation_request, validate_daily_session, validate_navigation_response
from tools.replay_foreground import load_configuration
from tools.replay_ocr import MaaOcrRecognizer, decide_field
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.run_daily_navigation import send_request
from tools.run_supervised_navigation import private_output

PRIVATE = ROOT / "dev_assets/private"
DEFAULT_GUARD = PRIVATE / "in_app_browser/20261001-daily-research/training-flow-20261001"
ACTIONS = {"Zero": "training-bronze-plus", "One": "training-open-confirm",
           "Confirm": "training-confirm-one-bronze", "Result": "training-result-close"}
# Fixed screen coordinates; numeric results always come from the current frame.
OCR_FIELDS = {
    "inventory": ([725, 354, 76, 25], r"[xX×]?\s*[0-9]{1,8}"),
    "level": ([260, 136, 43, 34], r"[0-9]{1,3}"),
    "remaining_exp": ([478, 142, 65, 35], r"[0-9]{1,8}"),
}
NUMERIC_CROPS = {"inventory": [739, 357, 59, 21], "level": [267, 138, 24, 25],
                 "remaining_exp": [479, 149, 38, 25]}
NUMERIC_CUTOFFS = {"inventory": 128, "level": 128, "remaining_exp": 80}
# The OCR model scores a lone level digit such as "2" around 0.93-0.97 under any single
# preprocessing. Level therefore requires every fixed variant to agree on the same text
# and the best variant to reach the unchanged 0.95 threshold.
LEVEL_VARIANTS = ("threshold128", "percentile_gray", "otsu", "bold128")

def resize_numeric(image, scale=3):
    """Deterministic bilinear enlargement without another imaging dependency."""
    import numpy as np
    height, width = image.shape
    ys, xs = np.linspace(0, height - 1, height * scale), np.linspace(0, width - 1, width * scale)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, height - 1), np.minimum(x0 + 1, width - 1)
    wy, wx = ys - y0, xs - x0
    top = image[y0[:, None], x0[None, :]] * (1 - wx)[None, :] + image[y0[:, None], x1[None, :]] * wx[None, :]
    bottom = image[y1[:, None], x0[None, :]] * (1 - wx)[None, :] + image[y1[:, None], x1[None, :]] * wx[None, :]
    return np.round(top * (1 - wy)[:, None] + bottom * wy[:, None]).astype(np.uint8)


def otsu_cutoff(gray):
    import numpy as np
    values = np.round(gray).astype(int).ravel()
    histogram = np.bincount(values, minlength=256).astype(float)
    levels = np.arange(256)
    best, cutoff = -1.0, 128
    for candidate in range(1, 255):
        low, high = histogram[:candidate + 1], histogram[candidate + 1:]
        if low.sum() == 0 or high.sum() == 0:
            continue
        mean_low = (low * levels[:candidate + 1]).sum() / low.sum()
        mean_high = (high * levels[candidate + 1:]).sum() / high.sum()
        spread = low.sum() * high.sum() * (mean_low - mean_high) ** 2
        if spread > best:
            best, cutoff = spread, candidate
    return cutoff


def numeric_pixels(name, image, variant=None):
    import numpy as np
    require(name in NUMERIC_CROPS and image.shape == (720, 1280, 3), "数字OCR字段或画布无效")
    require(variant is None and name != "level" or name == "level" and variant in LEVEL_VARIANTS,
            "数字OCR预处理变体无效")
    x, y, width, height = NUMERIC_CROPS[name]
    crop = image[y:y + height, x:x + width].copy()
    raw = crop.mean(axis=2)
    if name == "inventory":
        raw = 255 - raw
    contrast = float(raw.max() - raw.min())
    if contrast < 20:
        return None, {"crop": NUMERIC_CROPS[name], "crop_sha256": sha256(crop.tobytes()),
                      "reason": "blank_or_low_contrast"}
    gray = (raw - raw.min()) * 255 / contrast
    cutoff = NUMERIC_CUTOFFS[name]
    if variant == "percentile_gray":
        low, high = np.percentile(raw, 2), np.percentile(raw, 98)
        binary, cutoff = np.round(np.clip((raw - low) * 255 / max(1.0, high - low), 0, 255)).astype(np.uint8), None
    else:
        if variant == "otsu":
            cutoff = otsu_cutoff(gray)
        binary = np.where(gray > cutoff, 255, 0)
        if variant == "bold128":
            # Thicken dark strokes by one source pixel to the right and down.
            bold = binary.copy()
            bold[:, 1:] = np.minimum(bold[:, 1:], binary[:, :-1])
            bold[1:, :] = np.minimum(bold[1:, :], bold[:-1, :])
            binary = bold
    enlarged = resize_numeric(binary)
    padded = np.pad(enlarged, ((6, 6), (6, 6)), constant_values=255)
    pixels = np.repeat(padded[:, :, None], 3, axis=2)
    return pixels, {"kind": "fixed_numeric_crop_normalize_threshold_bilinear3_only_rec",
                    "variant": variant or "threshold", "crop": NUMERIC_CROPS[name],
                    "crop_sha256": sha256(crop.tobytes()),
                    "processed_sha256": sha256(pixels.tobytes()), "scale": 3,
                    "padding": 6, "cutoff": cutoff}


class MaaNumericRecognizer(MaaOcrRecognizer):
    """OCR only the fixed visible number; no detector and no guessed values."""
    def __init__(self, model_dir):
        super().__init__(model_dir)
        self.model["numeric_preprocessing"] = {"version": 1, "crops": NUMERIC_CROPS,
            "cutoffs": NUMERIC_CUTOFFS, "level_variants": list(LEVEL_VARIANTS), "scale": 3, "padding": 6,
            "only_rec": True, "threshold": .95}

    def recognize(self, field, image):
        name = field["name"].removeprefix("SC.Training.OCR.")
        require(name in OCR_FIELDS and field["roi"] == OCR_FIELDS[name][0]
                and field["expected"] == OCR_FIELDS[name][1] and field["threshold"] == .95,
                "禁止更改数字OCR字段、区域或阈值")
        if name != "level":
            return self.recognize_variant(field, name, image, None)
        variants = [self.recognize_variant(field, name, image, variant) for variant in LEVEL_VARIANTS]
        texts = {item["results"][0]["text"] if len(item["results"]) == 1 else None for item in variants}
        ranked = sorted((item for item in variants if len(item["results"]) == 1),
                        key=lambda item: item["results"][0]["score"], reverse=True)
        if len(texts) != 1 or None in texts or not ranked:
            return {"hit": False, "results": [], "raw_detail": {"variants": variants},
                    "preprocessing": {"kind": "level_unanimous_variants", "reason": "variants_disagree_or_missing"}}
        best = ranked[0]
        return {"hit": best["hit"], "results": best["results"],
                "raw_detail": {**(best["raw_detail"] or {}), "variants": variants},
                "preprocessing": {**best["preprocessing"], "kind": "level_unanimous_variants",
                                  "variants": list(LEVEL_VARIANTS)}}

    def recognize_variant(self, field, name, image, variant):
        from maa.pipeline import JOCR, JRecognitionType
        pixels, processing = numeric_pixels(name, image, variant)
        if pixels is None:
            return {"hit": False, "results": [], "raw_detail": {}, "preprocessing": processing}
        height, width = pixels.shape[:2]
        param = JOCR(expected=[field["expected"]], roi=[0, 0, width, height], threshold=.95, only_rec=True)
        detail = self.tasker.post_recognition(JRecognitionType.OCR, param, pixels).wait().get()
        require(detail is not None and len(detail.nodes) == 1 and detail.nodes[0].recognition is not None,
                "Maa数字OCR未返回唯一详情")
        result = detail.nodes[0].recognition
        require(len(result.all_results) <= 1 and all(list(item.box) == [0, 0, width, height]
                for item in result.all_results), "数字OCR候选或处理区域不唯一")
        # only_rec returns the entire processed crop as its box. Explicitly
        # retain that raw box and map the region back to the fixed source crop.
        results = [{"text": item.text, "score": item.score, "box": list(NUMERIC_CROPS[name]),
                    "processed_box": list(item.box)} for item in result.all_results]
        return {"hit": result.hit, "results": results, "raw_detail": result.raw_detail,
                "preprocessing": processing}


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def implementation_stamp():
    files = (Path(__file__), ROOT / "agent/core/training_flow.py",
             ROOT / "tools/navigation_protocol.py", ROOT / "tools/replay_screenshots.py",
             ROOT / "tests/fixtures/training_flow/pipeline.json")
    return sha256(b"".join(p.read_bytes() for p in files))


def validate_offline_proof(proof, data, truth, digest):
    """Validate every sample, conjunction and simulated action of the fixed flow."""
    require(proof.get("passed") is True and proof.get("offline_only") is True
            and proof.get("real_input_enabled") is False
            and type(proof.get("real_input_attempts")) is int and proof["real_input_attempts"] == 0
            and proof.get("framework_version") == "v5.12.3"
            and proof.get("configuration_sha256") == digest, "训练离线证明身份或真实输入字段无效")
    samples = proof.get("samples")
    require(type(samples) is list and len(samples) == len(data["samples"])
            and proof.get("sample_count") == len(samples), "训练离线证明样本不完整")
    by_id = {s.get("id"): s for s in samples if type(s) is dict}
    require(len(by_id) == len(samples) and set(by_id) == {s["id"] for s in data["samples"]}, "训练离线证明样本重复或缺失")
    for sample in data["samples"]:
        observed, target = by_id[sample["id"]], truth[sample["image"]]
        stage, expected = target["stage"], target["expected_guard"]
        end = "Click" if expected and stage in ACTIONS else "Guard" if expected else "Unknown"
        clicks = [list(ACTION_POINTS[stage])] if expected and stage in ACTIONS else []
        trace = observed.get("node_trace", [])
        require(observed.get("passed") is True and observed.get("stage") == stage
                and observed.get("expected_guard") is expected and observed.get("guard_proof") is expected
                and stage_matches(stage, observed.get("recognitions")) is expected
                and observed.get("simulated_clicks") == clicks
                and type(observed.get("real_input_attempts")) is int and observed["real_input_attempts"] == 0
                and type(observed.get("other_input_calls")) is int and observed["other_input_calls"] == 0
                and [n.get("name") for n in trace] == [PREFIX + stage + ".Observe", PREFIX + stage + "." + end]
                and all(n.get("completed") is True and n.get("action_success") is True for n in trace),
                "训练离线证明的完整守卫、固定点击或节点轨迹不符")
    fixed_pipeline()  # Invariant check independent of the report.
    return samples


def foreground_allowed(state):
    # Training's own popup is proved by its complete stage anchors. All known
    # unrelated overlays, partial contracts and conflicts block the action.
    return (state.get("state") == "no_known_popup" and state.get("popup") is None
            and state.get("complete_contracts") == [] and state.get("partial_contracts") == [])


def observed_integer(name, recognition):
    roi, pattern = OCR_FIELDS[name]
    require(type(recognition) is dict and type(recognition.get("results")) is list
            and "raw_detail" in recognition and type(recognition.get("hit")) is bool,
            "库存或等级OCR详情缺失")
    field = {"name": "SC.Training.OCR." + name, "roi": roi, "expected": pattern, "threshold": .95}
    decision = decide_field(field, recognition)
    box = decision.get("box")
    require(decision["accepted"] is True and type(box) is list and len(box) == 4
            and all(type(v) is int for v in box) and box[2] > 0 and box[3] > 0
            and box[0] >= roi[0] and box[1] >= roi[1]
            and box[0] + box[2] <= roi[0] + roi[2] and box[1] + box[3] <= roi[1] + roi[3],
            "库存或等级OCR不唯一、低分或超出固定区域")
    text = re.sub(r"^[xX×]\s*", "", decision["text"]).strip()
    return int(text), decision


def observe_resources(stage, image, frame_hash, ocr):
    evidence = {"card_id": CARD_ID, "card_type": "support_s", "ticket_type": "basic_bronze",
                "frame_sha256": frame_hash}
    diagnostics = {}
    names = tuple(OCR_FIELDS) if stage in ("Zero", "After") else ("inventory", "level") if stage == "One" else ("level",)
    for name in names:
        roi, pattern = OCR_FIELDS[name]
        field = {"name": "SC.Training.OCR." + name, "roi": roi, "expected": pattern, "threshold": .95}
        raw = ocr.recognize(field, image)
        diagnostics[name] = {"roi": roi, "recognition": raw}
        try:
            value, decision = observed_integer(name, raw)
            evidence[name] = value
            diagnostics[name]["decision"] = decision
        except ValueError as exc:
            diagnostics[name]["error"] = str(exc)
    if stage in ("Zero", "One", "After"):
        evidence.update(bronze_quantity=1 if stage == "One" else 0, silver_quantity=0)
        if stage == "One":
            evidence["exp"] = 50  # Only after the fixed Exp50 visual anchor matched.
    if stage == "Confirm":
        evidence.update(confirmed_items=[{"ticket_type": "basic_bronze", "quantity": 1}],
            final_cost_proof={"kind": "single_bronze_cost_panel", "frame_sha256": frame_hash,
                              "sole_material": "basic_bronze", "quantity": 1,
                              "complete_panel": True, "guard_verified": True})
    # Inventory is hidden by Confirm/Result. No OCR value is invented for a
    # hidden field; the caller may link the preceding verified One observation.
    return evidence, diagnostics


def replay_numeric_fields(data, truth, base, ocr):
    # Labels are from the hash-bound, visually inspected historical fixtures.
    # They are replay ground truth only and never populate a live observation.
    expected = {"Zero": {"inventory": 22612, "level": 1, "remaining_exp": 50},
                "One": {"inventory": 22612, "level": 1}, "Confirm": {"level": 1},
                "Result": {"level": 2}, "After": {"inventory": 22611, "level": 2, "remaining_exp": 40}}
    results = []
    for stage, numbers in expected.items():
        selected = [s for s in data["samples"] if s["image"] == stage + "-source.png"
                    and truth[s["image"]]["stage"] == stage and truth[s["image"]]["expected_guard"] is True]
        require(len(selected) == 1, "缺少固定历史OCR正样本")
        path = base / selected[0]["image"]
        evidence, diagnostics = observe_resources(stage, read_png(path), sha256(path.read_bytes()), ocr)
        results.append({"stage": stage, "image_sha256": evidence["frame_sha256"],
                        "expected": numbers, "evidence": evidence, "ocr": diagnostics,
                        "passed": all(evidence.get(k) == v for k, v in numbers.items())})
    return results


def current_resources_match(evidence, baseline):
    return all(type(evidence.get(name)) is int and evidence[name] == baseline.get(name)
               for name in ("inventory", "level"))


def replay_numeric_negatives(base, ocr):
    """Actual OCR negatives plus the same before-spend comparison used live."""
    rows = []
    original = read_png(base / "Zero-source.png")
    baseline = {"inventory": 22612, "level": 1}
    for name in OCR_FIELDS:
        x, y, width, height = NUMERIC_CROPS[name]
        image = original.copy()
        image[y:y + height, x:x + width] = 255
        roi, pattern = OCR_FIELDS[name]
        raw = ocr.recognize({"name": "SC.Training.OCR." + name, "roi": roi,
                             "expected": pattern, "threshold": .95}, image)
        accepted = False
        try:
            observed_integer(name, raw)
            accepted = True
        except ValueError:
            pass
        rows.append({"id": "blank-" + name, "recognition": raw, "accepted": accepted,
                     "passed": accepted is False})
    after = read_png(base / "After-source.png")
    actual, diagnostics = observe_resources("After", after, sha256(after.tobytes()), ocr)
    for name in ("inventory", "level"):
        changed = dict(baseline)
        changed[name] = actual.get(name)
        rows.append({"id": "changed-" + name, "evidence": changed, "ocr": diagnostics.get(name),
                     "allowed": current_resources_match(changed, baseline),
                     "passed": type(changed.get(name)) is int and changed[name] != baseline[name]
                               and not current_resources_match(changed, baseline)})
    # Move the observed silver count into the bronze numeric ROI. Even a correct
    # high-score number must fail the bound bronze before-inventory comparison.
    x, y, width, height = NUMERIC_CROPS["inventory"]
    image = original.copy()
    image[y:y + height, x:x + width] = original[492:492 + height, x:x + width]
    roi, pattern = OCR_FIELDS["inventory"]
    raw = ocr.recognize({"name": "SC.Training.OCR.inventory", "roi": roi,
                         "expected": pattern, "threshold": .95}, image)
    silver = None
    try:
        silver, _ = observed_integer("inventory", raw)
    except ValueError:
        pass
    changed = {"inventory": silver, "level": 1}
    rows.append({"id": "wrong-silver-resource", "recognition": raw, "observed_inventory": silver,
                 "allowed": current_resources_match(changed, baseline),
                 "passed": not current_resources_match(changed, baseline)})
    # The complete popup hides inventory; it must never be read as a current count.
    popup = read_png(base / "Confirm-source.png")
    raw = ocr.recognize({"name": "SC.Training.OCR.inventory", "roi": roi,
                         "expected": pattern, "threshold": .95}, popup)
    accepted = False
    try:
        observed_integer("inventory", raw)
        accepted = True
    except ValueError:
        pass
    rows.append({"id": "popup-hidden-inventory", "recognition": raw,
                 "accepted": accepted, "passed": accepted is False})
    return rows


def historical_numeric_replay(ocr):
    """Keep numeric ground truth independent of a newly calibrated live profile."""
    data, truth, digest = load_guard(DEFAULT_GUARD / "manifest.json", DEFAULT_GUARD / "sources.json")
    positives = replay_numeric_fields(data, truth, DEFAULT_GUARD, ocr)
    negatives = replay_numeric_negatives(DEFAULT_GUARD, ocr)
    require(load_guard(DEFAULT_GUARD / "manifest.json", DEFAULT_GUARD / "sources.json")[2] == digest,
            "历史数字数据集在回放期间发生变化")
    return digest, positives, negatives


def private_frame(entry, expected_sha256):
    require(type(entry) is dict and entry.get("sha256") == expected_sha256
            and type(entry.get("image")) is str, "恢复缺少绑定的私有截图")
    try:
        path = legacy_private_path(entry["image"], PRIVATE)
    except ValueError:
        path = None
    require(path is not None and path.is_file()
            and sha256(path.read_bytes()) == expected_sha256, "恢复截图路径或哈希不符")
    return {"image": str(path), "sha256": expected_sha256}


def native_ledger(session, action):
    path = (PRIVATE / "daily_navigation" / (action + ".json")).resolve()
    require(type(session.get("budget_paths")) is dict
            and type(session["budget_paths"].get(action)) is str
            and Path(session["budget_paths"][action]).resolve() == path,
            "恢复必须读取固定原生单次预算，不得改路径")
    require(path.is_file(), "恢复缺少原生单次动作账本")
    blob = path.read_bytes()
    ledger = json.loads(blob.decode("utf-8-sig"))
    require(type(ledger) is dict and set(ledger) == {"Version", "Navigation", "MaxAttempts", "Attempts",
            "LastPid", "LastFrameSha256", "LastNonce"}
            and type(ledger["Version"]) is int and ledger["Version"] == 1
            and ledger["Navigation"] == action and type(ledger["MaxAttempts"]) is int
            and ledger["MaxAttempts"] == 1 and type(ledger["Attempts"]) is int
            and ledger["Attempts"] in (0, 1), "原生恢复账本格式或单次预算无效")
    return ledger, {"path": str(path), "sha256": sha256(blob)}


def require_consumption_unused(session):
    require(not consumption_pending_path().exists(), "Confirm已有runner pending记录，禁止恢复或重发未知消费")
    action = ACTIONS["Confirm"]
    path = (PRIVATE / "daily_navigation" / (action + ".json")).resolve()
    require(type(session.get("budget_paths")) is dict
            and type(session["budget_paths"].get(action)) is str
            and Path(session["budget_paths"][action]).resolve() == path,
            "消费预算必须仍为固定单次原生路径")
    if path.exists():
        ledger, _ = native_ledger(session, action)
        require(ledger["Attempts"] == 0 and ledger["LastPid"] is None
                and ledger["LastFrameSha256"] is None and ledger["LastNonce"] is None,
                "Confirm已尝试或账本不确定，禁止恢复或重发消费")


def consumption_pending_path():
    return PRIVATE / "daily_navigation/training-confirm-one-bronze.runner-pending.json"


def write_consumption_pending(report_path, report):
    """Witness a send even if it never reaches the native attempt ledger."""
    report_path = Path(report_path).resolve()
    binding = report.get("request_binding", {})
    require(report_path.is_relative_to(PRIVATE.resolve()) and report.get("stage") == "Confirm"
            and report.get("input_attempts") == 1 and report.get("uncertain_consumption") is True
            and type(report.get("session_pid")) is int and report["session_pid"] > 0
            and binding.get("action") == ACTIONS["Confirm"]
            and binding.get("frame_sha256") == report.get("fresh_guard", {}).get("frame_sha256")
            and type(binding.get("nonce")) is str and re.fullmatch(r"[0-9a-f]{32}", binding["nonce"]) is not None,
            "消费pending记录必须绑定本次固定Confirm请求和新帧")
    path = consumption_pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    witness = {"version": 1, "runner": "SC.DailyTraining", "stage": "Confirm", "report": str(report_path),
               "session_pid": report["session_pid"], "request_binding": binding, "no_retry": True}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(witness, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": str(path.resolve()), "sha256": sha256(path.read_bytes())}


def validate_preparation_resume(prior, stage, pid, session, model):
    """Only move onward from a proved non-consuming action, never repeat it."""
    require(stage in ("One", "Confirm"), "只允许恢复One或Confirm的非消费准备阶段")
    previous = "Zero" if stage == "One" else "One"
    require(type(prior) is dict and prior.get("runner") == "SC.DailyTraining"
            and prior.get("stage") == previous and prior.get("passed") is False
            and prior.get("reason") in ("postcondition_unverified_no_retry", "refused_or_uncertain_no_retry")
            and prior.get("input_enabled") is True and type(prior.get("input_attempts")) is int
            and prior["input_attempts"] == 1 and type(prior.get("session_pid")) is int
            and prior["session_pid"] == pid and session.get("pid") == pid
            and prior.get("resource_result_verified") is False
            and prior.get("uncertain_consumption") is False and prior.get("ocr_model") == model,
            "恢复仅接受本宿主已尝试一次的非消费准备失败报告")
    response = validate_navigation_response(prior.get("response"))
    require(response["ok"] is True and response["attempted"] is True,
            "准备动作响应丢失、拒绝或未知，不可恢复")
    trace = prior.get("node_trace", [])
    require(type(trace) is list and [node.get("name") for node in trace]
            == [PREFIX + previous + ".Observe", PREFIX + previous + ".Click"]
            and all(node.get("completed") is True and node.get("action_success") is True for node in trace),
            "准备阶段原生响应缺少完整固定Maa动作轨迹")
    guard, baseline = prior.get("fresh_guard", {}), prior.get("baseline", {})
    require(type(guard) is dict and guard.get("stage") == previous and guard.get("guard_verified") is True
            and stage_matches(previous, guard.get("recognitions"))
            and foreground_allowed(guard.get("foreground", {})), "原准备阶段完整新帧守卫或前景证据无效")
    require(type(baseline) is dict and baseline.get("card_id") == CARD_ID
            and baseline.get("card_type") == "support_s" and baseline.get("ticket_type") == "basic_bronze"
            and all(type(baseline.get(name)) is int and baseline[name] > 0
                    for name in ("inventory", "level", "remaining_exp"))
            and type(baseline.get("frame_sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", baseline.get("frame_sha256", "")) is not None,
            "恢复缺少指定S卡的真实OCR初始基线")
    evidence = guard.get("evidence", {})
    require(evidence.get("frame_sha256") == guard.get("frame_sha256")
            and current_resources_match(evidence, baseline), "原准备新帧与初始资源基线不符")
    validate_resource_evidence(previous, evidence)
    for name in ("inventory", "level"):
        number, _ = observed_integer(name, guard.get("ocr", {}).get(name, {}).get("recognition", {}))
        require(number == baseline[name], "原准备库存或等级并非真实唯一OCR基线")
    if previous == "Zero":
        remaining, _ = observed_integer("remaining_exp", guard.get("ocr", {}).get("remaining_exp", {}).get("recognition", {}))
        require(remaining == baseline["remaining_exp"], "原准备剩余经验OCR与基线不符")
    else:
        require(evidence.get("linked_remaining_exp_frame_sha256") == baseline["frame_sha256"],
                "One缺少已观察剩余经验的来源帧")
    frames = prior.get("frames", [])
    require(type(frames) is list, "恢复私有截图列表无效")
    matching = [frame for frame in frames if frame.get("sha256") == guard.get("frame_sha256")]
    require(matching, "原准备新帧未保存")
    guard_frame = private_frame(matching[0], guard["frame_sha256"])
    baseline_entry = prior.get("baseline_frame")
    if baseline_entry is None:
        matching = [frame for frame in frames if frame.get("sha256") == baseline["frame_sha256"]]
        require(matching, "恢复缺少初始OCR基线源图")
        baseline_entry = matching[0]
    baseline_frame = private_frame(baseline_entry, baseline["frame_sha256"])
    action = ACTIONS[previous]
    ledger, ledger_proof = native_ledger(session, action)
    require(ledger["Attempts"] == 1 and type(ledger["LastPid"]) is int and ledger["LastPid"] == pid
            and ledger["LastFrameSha256"] == guard["frame_sha256"]
            and type(ledger["LastNonce"]) is str and re.fullmatch(r"[0-9a-f]{32}", ledger["LastNonce"]) is not None,
            "原生准备账本的次数、PID或新帧不对应原报告")
    binding = prior.get("request_binding")
    if binding is not None:
        require(binding == {"action": action, "frame_sha256": guard["frame_sha256"], "nonce": ledger["LastNonce"]},
                "原报告请求编号与原生准备账本不符")
    require_consumption_unused(session)
    inventory_binding = {"inventory": evidence["inventory"], "frame_sha256": guard["frame_sha256"]} if previous == "One" else None
    return {"baseline": dict(baseline), "baseline_frame": baseline_frame, "inventory_binding": inventory_binding,
            "guard_frame": guard_frame, "ledger": ledger_proof, "previous_stage": previous,
            "previous_configuration_sha256": prior.get("configuration_sha256"),
            "previous_foreground_sha256": prior.get("foreground_sha256"),
            "previous_implementation_sha256": prior.get("implementation_sha256")}


def spend_witness(prior_path, prior, pid, session):
    """Bind the one Confirm send: report, runner pending witness and native ledger agree."""
    pending = consumption_pending_path()
    require(pending.is_file(), "消费后恢复必须保留Confirm runner pending见证")
    witness = json.loads(pending.read_bytes().decode("utf-8-sig"))
    binding = prior.get("request_binding")
    require(type(witness) is dict and witness.get("runner") == "SC.DailyTraining" and witness.get("stage") == "Confirm"
            and witness.get("no_retry") is True and witness.get("session_pid") == pid
            and Path(witness.get("report", "")).resolve() == Path(prior_path).resolve()
            and type(binding) is dict and witness.get("request_binding") == binding
            and binding.get("action") == ACTIONS["Confirm"], "Confirm pending见证与消费报告不符")
    ledger, proof = native_ledger(session, ACTIONS["Confirm"])
    require(ledger["Attempts"] == 1 and ledger["LastPid"] == pid
            and ledger["LastFrameSha256"] == binding.get("frame_sha256")
            and ledger["LastNonce"] == binding.get("nonce"), "Confirm原生账本与消费报告不符")
    return {"pending": {"path": str(pending.resolve()), "sha256": sha256(pending.read_bytes())}, "ledger": proof}


def validate_post_spend_resume(prior_path, prior, stage, pid, session, model):
    """Move past a sent Confirm without ever resending it: Result close, then read-only After."""
    require(stage in ("Result", "After"), "消费后恢复只允许Result关闭或After只读")
    previous = "Confirm" if stage == "Result" else "Result"
    require(type(prior) is dict and prior.get("runner") == "SC.DailyTraining" and prior.get("stage") == previous
            and prior.get("passed") is False and prior.get("reason") == "postcondition_unverified_no_retry"
            and prior.get("input_enabled") is True and prior.get("input_attempts") == 1
            and prior.get("session_pid") == pid and session.get("pid") == pid
            and prior.get("resource_result_verified") is False and prior.get("ocr_model") == model,
            "消费后恢复仅接受本宿主已发送一次且后置未识别的报告")
    response = validate_navigation_response(prior.get("response"))
    require(response["ok"] is True and response["attempted"] is True, "上一动作未确认发送，不可恢复")
    trace = prior.get("node_trace", [])
    require([node.get("name") for node in trace] == [PREFIX + previous + ".Observe", PREFIX + previous + ".Click"]
            and all(node.get("completed") is True and node.get("action_success") is True for node in trace),
            "上一动作缺少完整固定Maa动作轨迹")
    baseline = prior.get("baseline", {})
    require(type(baseline) is dict and baseline.get("card_id") == CARD_ID
            and all(type(baseline.get(name)) is int and baseline[name] > 0 for name in ("inventory", "level", "remaining_exp")),
            "消费后恢复缺少本轮初始资源基线")
    if previous == "Confirm":
        require(prior.get("uncertain_consumption") is True and prior.get("consumption_pending"),
                "Confirm报告必须保留未知消费标记与pending")
        guard = prior.get("fresh_guard", {})
        require(guard.get("stage") == "Confirm" and guard.get("guard_verified") is True
                and stage_matches("Confirm", guard.get("recognitions")), "Confirm缺少完整新帧证明")
        validate_resource_evidence("Confirm", guard.get("evidence", {}))
        spend = spend_witness(prior_path, prior, pid, session)
        source = {"confirm_report_path": str(Path(prior_path).resolve()),
                  "confirm_report_sha256": sha256(Path(prior_path).read_bytes())}
    else:
        chain = prior.get("post_spend_resume", {})
        require(type(chain) is dict and chain.get("current_stage") == "Result"
                and chain.get("baseline") == baseline and chain.get("consumption_retried") is False,
                "After只接受经消费后恢复的Result关闭报告")
        confirm_path = Path(chain.get("confirm_report_path", ""))
        require(confirm_path.is_file() and sha256(confirm_path.read_bytes()) == chain.get("confirm_report_sha256"),
                "原Confirm报告变化")
        spend = spend_witness(confirm_path, load_json(confirm_path), pid, session)
        require(spend == chain.get("spend"), "Confirm见证或账本在Result后变化")
        ledger, _ = native_ledger(session, ACTIONS["Result"])
        guard = prior.get("fresh_guard", {})
        require(ledger["Attempts"] == 1 and ledger["LastPid"] == pid
                and ledger["LastFrameSha256"] == guard.get("frame_sha256"), "Result关闭账本与报告不符")
        source = {"confirm_report_path": chain["confirm_report_path"],
                  "confirm_report_sha256": chain["confirm_report_sha256"],
                  "result_report_path": str(Path(prior_path).resolve()),
                  "result_report_sha256": sha256(Path(prior_path).read_bytes())}
    return {"baseline": dict(baseline), "spend": spend, "previous_stage": previous, **source}


def verify_post_spend_observation(stage, observation, baseline):
    require(observation.get("stage") == stage and observation.get("guard_verified") is True
            and stage_matches(stage, observation.get("recognitions"))
            and foreground_allowed(observation.get("foreground", {})), "消费后当前阶段完整新帧守卫失败")
    evidence = observation.get("evidence", {})
    require(type(evidence.get("level")) is int and evidence["level"] > baseline["level"],
            "消费后结果缺少等级增长证据")
    if stage == "After":
        evidence.update(before_inventory=baseline["inventory"], before_level=baseline["level"],
                        before_remaining_exp=baseline["remaining_exp"])
        validate_resource_evidence("After", evidence)


def verify_resume_resources(stage, observation, baseline):
    require(stage in ("One", "Confirm") and observation.get("stage") == stage
            and observation.get("guard_verified") is True and stage_matches(stage, observation.get("recognitions"))
            and foreground_allowed(observation.get("foreground", {})), "恢复后的当前阶段完整新帧守卫失败")
    evidence = observation.get("evidence", {})
    require(evidence.get("frame_sha256") == observation.get("frame_sha256")
            and current_resources_match(evidence, baseline), "恢复后当前帧、库存或等级变化，停止本轮")
    validate_resource_evidence(stage, evidence)


def verify_baseline_image(baseline, entry, ocr):
    frame = private_frame(entry, baseline["frame_sha256"])
    observed, _ = observe_resources("Zero", read_png(Path(frame["image"])), frame["sha256"], ocr)
    require(current_resources_match(observed, baseline)
            and type(observed.get("remaining_exp")) is int and observed["remaining_exp"] == baseline["remaining_exp"],
            "初始源图的真实OCR无法重新证明资源基线")


def validate_prior(prior, stage, pid, digest, foreground_digest, stamp, model):
    require(stage != "Zero" and prior.get("runner") == "SC.DailyTraining"
            and prior.get("passed") is True and prior.get("input_enabled") is True
            and prior.get("session_pid") == pid and prior.get("configuration_sha256") == digest
            and prior.get("foreground_sha256") == foreground_digest
            and prior.get("implementation_sha256") == stamp and prior.get("ocr_model") == model
            and prior.get("stage") == STAGES[STAGES.index(stage) - 1]
            and prior.get("postcondition", {}).get("stage") == stage
            and prior.get("reason") == "stage_postcondition_verified", "上一阶段报告不属于本轮已验证训练链")
    baseline = prior.get("baseline")
    require(type(baseline) is dict and baseline.get("card_id") == CARD_ID
            and type(baseline.get("inventory")) is int and baseline["inventory"] >= 1
            and type(baseline.get("level")) is int and baseline["level"] > 0,
            "缺少本轮真实OCR初始库存与等级")
    require(type(baseline.get("remaining_exp")) is int and baseline["remaining_exp"] > 0,
            "缺少本轮真实OCR初始剩余经验")
    if stage == "Confirm":
        guard = prior.get("fresh_guard", {})
        require(guard.get("stage") == "One" and guard.get("guard_verified") is True
                and stage_matches("One", guard.get("recognitions")), "最终消费缺少上一阶段完整新帧证明")
        for name in ("inventory", "level"):
            actual, _ = observed_integer(name, guard.get("ocr", {}).get(name, {}).get("recognition", {}))
            require(actual == baseline[name], "上一阶段OCR与本轮初始资源不符")
    return dict(baseline)


def guarded_send(report, execute, stage, point, guard, send, checkpoint=lambda: None):
    if execute is not True or stage not in ACTIONS or point != ACTION_POINTS[stage] or report["input_attempts"]:
        return False
    try:
        request = guard()
        if all(name in request for name in ("action", "frame_sha256", "nonce")):
            report["request_binding"] = {name: request[name] for name in ("action", "frame_sha256", "nonce")}
        report.update(input_attempts=1, reason="input_pending_no_retry",
                      uncertain_consumption=report.get("uncertain_consumption", False) or stage == "Confirm")
        checkpoint()  # Persist before transmission; process death must not hide a spend attempt.
        response = validate_navigation_response(send(request))
        report["response"] = response
        return response["ok"] and response["attempted"]
    except Exception as exc:
        report["input_error"] = str(exc)
        return False


def run(args, report, checkpoint):
    _, frames = private_output(args.report)
    data, truth, digest = load_guard(args.manifest, args.sources)
    proof_path = args.offline_proof.resolve()
    require(proof_path.is_relative_to(PRIVATE.resolve()), "训练离线证明必须位于private")
    validate_offline_proof(load_json(proof_path), data, truth, digest)
    fore_data, fore_rules, fore_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
    reco, ocr = MaaRecognizer(), MaaNumericRecognizer(args.model_dir)
    report.update(configuration_sha256=digest, foreground_sha256=fore_digest,
                  implementation_sha256=implementation_stamp(), ocr_model=ocr.model,
                  framework_version=reco.version, offline_verified=True)
    numeric_digest, report["ocr_offline"], report["ocr_offline_negatives"] = historical_numeric_replay(ocr)
    report.update(ocr_dataset_sha256=numeric_digest, ocr_dataset_directory=str(DEFAULT_GUARD.resolve()))
    report["ocr_offline_verified"] = all(s["passed"] for s in report["ocr_offline"] + report["ocr_offline_negatives"])
    if args.execute:
        require(report["ocr_offline_verified"], "历史库存/等级/剩余经验OCR未通过；仅允许只读采样，禁止训练输入")
    # Replay every unrelated foreground sample with the exact current resources.
    for sample in fore_data["samples"]:
        image = read_png(args.foreground_manifest.resolve().parent / sample["image"])
        hits = [reco.recognize(s, image, args.foreground_manifest.resolve().parent) for s in fore_data["scenes"]]
        state = interpret_foreground(hits, (1280, 720), fore_rules)
        wanted = fore_rules["expected"][sample["id"]]
        require(all(state[k] == v for k, v in wanted.items()), "前景离线回放失败")
    bridge = BrowserBridge.from_file(args.session)
    status = bridge.status()
    require(status["ready"] and status.get("composition_game") is True and status.get("minimized") is False,
            "训练截图宿主未就绪或最小化")
    report["session_pid"] = bridge.session["pid"]
    resume_path = args.resume_preparation_report
    spend_path = args.resume_after_spend_report
    require(sum(bool(p) for p in (args.prior_report, resume_path, spend_path)) <= 1, "上一阶段报告与各类恢复报告不能同时使用")
    session = validate_daily_session(load_json(Path(str(args.session) + ".daily-input.json")), bridge.session["pid"]) if args.execute or resume_path or spend_path else None
    baseline = None
    inventory_binding = None
    resume = None
    post_spend = None
    if spend_path:
        require(spend_path.resolve().is_relative_to(PRIVATE.resolve()), "消费后恢复报告必须private")
        blob = spend_path.read_bytes()
        post_spend = validate_post_spend_resume(spend_path, json.loads(blob.decode("utf-8-sig")), args.stage,
                                                bridge.session["pid"], session, ocr.model)
        baseline = post_spend["baseline"]
        report["uncertain_consumption"] = args.stage == "Result"
        report["post_spend_resume"] = {**post_spend, "source_report_path": str(spend_path.resolve()),
            "source_report_sha256": sha256(blob), "current_stage": args.stage,
            "current_configuration_sha256": digest, "consumption_retried": False}
    if args.prior_report:
        require(args.prior_report.resolve().is_relative_to(PRIVATE.resolve()), "上一阶段报告必须private")
        prior = load_json(args.prior_report)
        baseline = validate_prior(prior, args.stage, bridge.session["pid"], digest,
                                  fore_digest, report["implementation_sha256"], ocr.model)
        report["prior_report_sha256"] = sha256(args.prior_report.read_bytes())
        report["prior_report_path"] = str(args.prior_report.resolve())
        if prior.get("baseline_frame"):
            report["baseline_frame"] = private_frame(prior["baseline_frame"], baseline["frame_sha256"])
        if args.stage == "Confirm":
            source = prior["fresh_guard"]
            inventory_binding = {"inventory": source["evidence"]["inventory"], "frame_sha256": source["frame_sha256"]}
        report["uncertain_consumption"] = prior.get("uncertain_consumption") is True
    if resume_path:
        require(resume_path.resolve().is_relative_to(PRIVATE.resolve()), "显式准备恢复报告必须private")
        blob = resume_path.read_bytes()
        prior = json.loads(blob.decode("utf-8-sig"))
        resume = validate_preparation_resume(prior, args.stage, bridge.session["pid"], session, ocr.model)
        baseline, inventory_binding = resume["baseline"], resume["inventory_binding"]
        verify_baseline_image(baseline, resume["baseline_frame"], ocr)
        report["baseline_frame"] = resume["baseline_frame"]
        report["resume_preparation"] = {**resume, "source_report_path": str(resume_path.resolve()),
            "source_report_sha256": sha256(blob), "current_configuration_sha256": digest,
            "current_foreground_sha256": fore_digest, "current_implementation_sha256": report["implementation_sha256"],
            "current_stage": args.stage, "previous_action_repeated": False, "consumption_retried": False}
    if args.execute and args.stage != "Zero":
        require(baseline is not None, "后续输入必须提供本轮上一阶段报告；禁止从消费弹窗直接开始")
    frames.mkdir(parents=True)
    def capture():
        raw = bridge.screencap()
        path = frames / f"frame-{len(report['frames']) + 1:03d}.png"
        path.write_bytes(raw)
        entry = {"image": str(path), "sha256": sha256(raw)}
        report["frames"].append(entry)
        return read_png(path), entry["sha256"]
    def classify(stage, image, frame_hash):
        hits = [reco.recognize(s, image, args.manifest.resolve().parent) for s in data["scenes"]
                if s["name"].startswith(PREFIX + stage + ".")]
        fore_hits = [reco.recognize(s, image, args.foreground_manifest.resolve().parent) for s in fore_data["scenes"]]
        state = interpret_foreground(fore_hits, (1280, 720), fore_rules)
        positive = stage_matches(stage, hits) and foreground_allowed(state)
        observed = {"stage": stage, "frame_sha256": frame_hash, "recognitions": hits,
                    "foreground": state, "guard_verified": positive}
        if positive:
            observed["evidence"], observed["ocr"] = observe_resources(stage, image, frame_hash, ocr)
            if baseline is not None and stage in ("One", "Confirm"):
                observed["evidence"]["remaining_exp"] = baseline["remaining_exp"]
                observed["evidence"]["linked_remaining_exp_frame_sha256"] = baseline["frame_sha256"]
                if stage == "Confirm" and inventory_binding is not None:
                    observed["evidence"]["inventory"] = inventory_binding["inventory"]
                    observed["evidence"]["linked_inventory_frame_sha256"] = inventory_binding["frame_sha256"]
        return observed
    image, frame_hash = capture()
    initial = classify(args.stage, image, frame_hash)
    report["observation"] = initial
    require(initial["guard_verified"], "完整阶段或前景守卫拒绝")
    if args.stage == "Zero":
        baseline = initial["evidence"]
        report["baseline_frame"] = dict(report["frames"][-1])
    if resume is not None:
        verify_resume_resources(args.stage, initial, baseline)
        report["resume_current_stage_verified"] = True
    if post_spend is not None:
        verify_post_spend_observation(args.stage, initial, baseline)
        report["post_spend_current_stage_verified"] = True
        if args.stage == "After":
            report.update(resource_result_verified=True, uncertain_consumption=False)
    report["baseline"] = baseline
    if not args.execute:
        report.update(passed=True, reason="readonly_stage_verified", resource_contract_verified=False)
        try:
            validate_resource_evidence(args.stage, initial["evidence"])
            report["resource_contract_verified"] = True
        except ValueError as exc:
            report["resource_blocker"] = str(exc)
        return
    require(args.stage in ACTIONS, "After仅供只读，禁止输入")
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    class Controller(CustomController):
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.DailyTraining.OneStage"
        def screencap(self): return capture()[0]
        def click(self, x, y):
            def fresh_guard():
                nonlocal inventory_binding
                require(load_guard(args.manifest, args.sources)[2] == digest, "训练配置变化")
                require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == fore_digest, "前景配置变化")
                require(implementation_stamp() == report["implementation_sha256"], "训练代码发生变化")
                require(load_guard(DEFAULT_GUARD / "manifest.json", DEFAULT_GUARD / "sources.json")[2] == numeric_digest,
                        "历史OCR数据集变化")
                if resume is not None:
                    _, ledger = native_ledger(session, ACTIONS[resume["previous_stage"]])
                    require(ledger == resume["ledger"], "原准备动作账本变化，停止恢复")
                    require_consumption_unused(session)
                current, stamp = capture()
                observation = classify(args.stage, current, stamp)
                report["fresh_guard"] = observation
                require(observation["guard_verified"], "新帧完整阶段或前景守卫拒绝")
                evidence = observation["evidence"]
                if args.stage != "Result":
                    require(type(evidence.get("inventory")) is int and type(evidence.get("level")) is int,
                            "当前库存或等级OCR证据缺失")
                    require(current_resources_match(evidence, baseline),
                            "初始库存或等级已变化，停止本轮")
                # The complete final panel guard is mandatory before a spend.
                # Core owns resource rules; missing OCR refuses consumption.
                if args.stage != "Result":
                    validate_resource_evidence(args.stage, evidence)
                if args.stage == "Confirm":
                    require_consumption_unused(session)
                if resume is not None:
                    verify_resume_resources(args.stage, observation, baseline)
                if post_spend is not None:
                    confirm = Path(post_spend["confirm_report_path"])
                    require(sha256(confirm.read_bytes()) == post_spend["confirm_report_sha256"]
                            and spend_witness(confirm, load_json(confirm), bridge.session["pid"], session)
                            == post_spend["spend"], "Confirm见证、账本或报告变化，停止")
                    verify_post_spend_observation(args.stage, observation, baseline)
                if args.stage == "One":
                    inventory_binding = {"inventory": evidence["inventory"], "frame_sha256": stamp}
                return daily_navigation_request(session, bridge.status(), stamp, ACTIONS[args.stage])
            return guarded_send(report, args.execute, args.stage, (x, y), fresh_guard,
                                lambda request: send_request(session, request), checkpoint)
        def deny(self, *args): return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args): return None
    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            "训练控制器初始化失败")
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix="sc-supervised-training-") as temporary:
        bundle = Path(temporary)
        (bundle / "pipeline").mkdir()
        (bundle / "pipeline/training.json").write_text(json.dumps(fixed_pipeline(True)), encoding="utf-8")
        require(resource.post_bundle(bundle).wait().succeeded, "训练Pipeline加载失败")
        for scene in data["scenes"]:
            require(resource.override_image(scene["template"], read_png(template_path(scene, args.manifest.resolve().parent), expected_size=None)), "训练模板加载失败")
        require(tasker.bind(resource, controller) and tasker.inited, "训练Pipeline绑定失败")
        job = tasker.post_task(PREFIX + args.stage + ".Observe")
        deadline = time.monotonic() + 30
        while not job.done:
            if time.monotonic() >= deadline:
                tasker.post_stop().wait()
                raise RuntimeError("训练任务超时，停止且不重试")
            time.sleep(.02)
        detail = job.get()
        report["node_trace"] = [{"name": n.name, "completed": n.completed,
            "action_success": n.action.success if n.action else None} for n in detail.nodes] if detail else []
        report.update(reason="postcondition_unverified_no_retry", passed=False)
        require(job.succeeded and report.get("response", {}).get("ok") is True
                and report.get("response", {}).get("attempted") is True, "固定输入未确认，禁止重试")
    next_stage = STAGES[STAGES.index(args.stage) + 1]
    # Bounded observation only; no repeated input or reload even on a lost result.
    for _ in range(20):
        image, stamp = capture()
        observed = classify(next_stage, image, stamp)
        report.setdefault("postcondition_checks", []).append(observed)
        if observed["guard_verified"]:
            if next_stage != "Result":
                require(type(observed["evidence"].get("inventory")) is int
                        and type(observed["evidence"].get("level")) is int, "后置库存或等级OCR缺失")
            if next_stage == "After":
                evidence = observed["evidence"]
                evidence.update(before_inventory=baseline["inventory"], before_level=baseline["level"],
                                before_remaining_exp=baseline["remaining_exp"])
                validate_resource_evidence("After", evidence)
                report["resource_result_verified"] = True
            elif next_stage != "Result":
                require(observed["evidence"]["inventory"] == baseline["inventory"]
                        and observed["evidence"]["level"] == baseline["level"], "非消耗阶段资源变化，停止")
            report.update(passed=True, reason="stage_postcondition_verified", postcondition=observed,
                          uncertain_consumption=report["uncertain_consumption"] and next_stage != "After")
            break
        time.sleep(.5)
    require(reco.input_attempts == 0 and ocr.input_attempts == 0, "识别器出现意外输入")


def main(argv=None):
    parser = argparse.ArgumentParser(description="固定芹沢あさひS卡单铜券训练；默认只读，执行须逐阶段指定。")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_GUARD / "manifest.json")
    parser.add_argument("--sources", type=Path, default=DEFAULT_GUARD / "sources.json")
    parser.add_argument("--offline-proof", type=Path, default=PRIVATE / "training-flow-final-offline-20261001.json")
    # PP-OCRv6 small: 2026-10-01 offline comparison (ocr-model-comparison-20261001.json) had the
    # largest margin above 0.95 with no wrong reads; ja_ppocrv3_v4 remains for comparison.
    parser.add_argument("--model-dir", type=Path, default=PRIVATE / "models/ppocr_v6_small")
    for name in ("foreground-manifest", "foreground-contracts", "session", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES, default="Zero")
    chain = parser.add_mutually_exclusive_group()
    chain.add_argument("--prior-report", type=Path)
    chain.add_argument("--resume-preparation-report", type=Path,
                       help="仅显式恢复已确认非消费准备动作后的One或Confirm；不重发旧动作")
    chain.add_argument("--resume-after-spend-report", type=Path,
                       help="Confirm已发后：Result只关闭结果、After只读核验；永不重发消费")
    parser.add_argument("--execute", action="store_true", help="本次仅尝试当前阶段固定动作一次")
    args = parser.parse_args(argv)
    if args.execute and args.stage == "After":
        parser.error("After仅供只读")
    if args.resume_preparation_report and args.stage not in ("One", "Confirm"):
        parser.error("准备恢复仅允许One或Confirm；消费已发或Result未知不能恢复")
    if args.resume_after_spend_report and args.stage not in ("Result", "After"):
        parser.error("消费后恢复仅允许Result或After")
    private_output(args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"runner": "SC.DailyTraining", "passed": False, "stage": args.stage,
              "input_enabled": args.execute, "input_attempts": 0, "frames": [],
              "resource_result_verified": False, "daily_mission_verified": False,
              "uncertain_consumption": False}
    def checkpoint():
        if (report["stage"] == "Confirm" and report["input_attempts"] == 1
                and not report.get("consumption_pending") and not report.get("input_error")):
            report["consumption_pending"] = write_consumption_pending(args.report, report)
        mode = "w" if report.get("checkpoint_written") else "x"
        with args.report.open(mode, encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        report["checkpoint_written"] = True
    try:
        run(args, report, checkpoint)
    except Exception as exc:
        report.update(passed=False, reason="refused_or_uncertain_no_retry", error=str(exc))
    checkpoint()
    print(json.dumps({k: report[k] for k in ("passed", "reason", "input_attempts", "uncertain_consumption")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
