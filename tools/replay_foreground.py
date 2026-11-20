#!/usr/bin/env python3
"""Offline foreground-first replay; underlying hits are diagnostic only."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.foreground_guard import interpret_foreground, validate_contracts
from tools.replay_screenshots import CANVAS, MaaRecognizer, png_dimensions, read_png, require, template_path, validate_manifest


def load_configuration(manifest, contracts):
    manifest, contracts = Path(manifest).resolve(), Path(contracts).resolve()
    manifest_blob, contracts_blob = manifest.read_bytes(), contracts.read_bytes()
    data = validate_manifest(json.loads(manifest_blob.decode("utf-8")), manifest.parent)
    rules = validate_contracts(json.loads(contracts_blob.decode("utf-8")),
                               [s["name"] for s in data["scenes"]], [s["id"] for s in data["samples"]])
    identity = {"manifest_sha256": hashlib.sha256(manifest_blob).hexdigest(),
                "contracts_sha256": hashlib.sha256(contracts_blob).hexdigest(),
                "templates": {s["template"]: hashlib.sha256(template_path(s, manifest.parent).read_bytes()).hexdigest()
                              for s in data["scenes"] if s["recognition"] == "TemplateMatch"}}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return data, rules, digest, identity


def replay(manifest, contracts, recognizer):
    manifest, contracts = Path(manifest).resolve(), Path(contracts).resolve()
    data, rules, digest, identity = load_configuration(manifest, contracts)
    samples = []
    for sample in data["samples"]:
        path = manifest.parent / sample["image"]
        image_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        dimensions = png_dimensions(path)
        recognitions = []
        if dimensions == CANVAS:
            image = read_png(path)
            recognitions = [recognizer.recognize(scene, image, manifest.parent) for scene in data["scenes"]]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == image_hash, "识别期间图片发生变化")
        require(load_configuration(manifest, contracts)[2] == digest, "识别期间配置或模板发生变化")
        actual = interpret_foreground(recognitions, dimensions, rules)
        expected = rules["expected"][sample["id"]]
        samples.append({"id": sample["id"], "image": sample["image"], "image_sha256": image_hash,
                        "dimensions": list(dimensions), "recognitions": recognitions,
                        "actual": actual, "expected": expected,
                        "passed": {k: actual[k] for k in expected} == expected})
    attempts = recognizer.input_attempts
    return {"offline_only": True, "recognition_only": True, "input_enabled": False,
            "input_authorized": False, "close_authorized": False, "underlying_allowed": False,
            "configuration_sha256": digest, "configuration": identity,
            "framework_version": recognizer.version, "input_attempts": attempts, "samples": samples,
            "passed": all(s["passed"] for s in samples) and type(attempts) is int and attempts == 0,
            "summary": "前景离线预期比较；未发现已知弹窗不代表无遮挡，不授权任何输入"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地PNG前景优先只读回放；不执行任务或输入。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "dev_assets/private/foreground/report.json")
    args = parser.parse_args(argv)
    report = {"offline_only": True, "recognition_only": True, "input_enabled": False,
              "input_authorized": False, "close_authorized": False, "underlying_allowed": False}
    recognizer = None
    report_path = None
    try:
        output = args.report.resolve()
        require(output.suffix.lower() == ".json", "报告须为JSON文件")
        require(output not in (args.manifest.resolve(), args.contracts.resolve()) and not output.exists(),
                "报告不可覆盖现有文件、清单或合同")
        report_path = output
        load_configuration(args.manifest, args.contracts)
        recognizer = MaaRecognizer()
        report.update(replay(args.manifest, args.contracts, recognizer))
        code = 0 if report["passed"] else 1
    except Exception as exc:
        report.update(passed=False, error=str(exc), input_attempts=recognizer.input_attempts if recognizer is not None else 0)
        code = 2
    if report_path is not None:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        except OSError as exc:
            report.update(passed=False, error="报告保存失败：" + str(exc))
            code = 2
    print(json.dumps({"passed": report["passed"], "report": str(report_path) if report_path else None,
                      "error": report.get("error")}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
