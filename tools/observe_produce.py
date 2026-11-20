#!/usr/bin/env python3
"""Turn one offline OCR frame and its manual annotation into a read-only advice."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def observe(report, sample_id, annotation, image_path, profile="balanced", low_stamina_threshold=50):
    from agent.core.produce_input import decide_from_params
    from agent.core.produce_observation import build_observed_scenario

    if not isinstance(report, dict) or report.get("framework_version") != "v5.12.3":
        raise ValueError("要求MaaFramework v5.12.3的离线OCR报告")
    samples = report.get("samples")
    if not isinstance(samples, list) or not samples or any(not isinstance(s, dict) for s in samples):
        raise ValueError("OCR报告样本列表无效")
    identifiers = [s.get("id") for s in samples]
    if any(not isinstance(i, str) or not i for i in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("OCR报告样本ID须为唯一字符串")
    matches = [s for s in samples if s.get("id") == sample_id]
    if len(matches) != 1:
        raise ValueError("指定OCR样本不存在或不唯一")
    sample = matches[0]
    if sample.get("passed") is False:
        raise ValueError("指定OCR样本与人工期望标签不一致，拒绝建议")
    from tools.replay_screenshots import png_dimensions
    image_path = Path(image_path)
    if image_path.suffix.lower() != ".png" or png_dimensions(image_path) != (1280, 720):
        raise ValueError("原始证据必须是1280×720 PNG")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if sample.get("image_sha256") != digest:
        raise ValueError("当前证据图像与OCR报告不匹配")
    scenario = build_observed_scenario(sample, annotation)
    decision = decide_from_params({"scenario": scenario, "profile": profile, "low_stamina_threshold": low_stamina_threshold})
    return {"passed": True, "readonly": True, "input_enabled": False, "sample_id": sample_id,
            "image_sha256": digest, "availability_source": "manual_annotation",
            "scene_source": "manual_annotation", "numeric_source": "maa_ocr",
            "confidence_kind": "ocr_score_not_probability", "scenario": scenario,
            "decision": decision.to_dict()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线OCR数值与当前帧人工标注生成Produce建议；不会输入。")
    parser.add_argument("--ocr-report", type=Path, required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--profile", choices=("balanced", "score", "safe"), default="balanced")
    parser.add_argument("--low-stamina-threshold", type=float, default=50)
    parser.add_argument("--report", type=Path, default=ROOT / "dev_assets/private/produce_observation/report.json")
    args = parser.parse_args(argv)
    try:
        result = observe(json.loads(args.ocr_report.read_text(encoding="utf-8")), args.sample_id,
                         json.loads(args.annotation.read_text(encoding="utf-8")), args.image,
                         args.profile, args.low_stamina_threshold)
        code = 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"passed": False, "readonly": True, "input_enabled": False, "error": str(exc)}
        code = 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "report": str(args.report), "error": result.get("error")}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
