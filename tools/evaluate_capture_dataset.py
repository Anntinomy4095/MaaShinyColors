#!/usr/bin/env python3
"""Evaluate labelled private captures with recognition only, without live input."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

if __package__:
    from .manage_capture_session import private_path, require, validate_dataset
    from .replay_screenshots import CANVAS, MaaRecognizer, decide, read_png, template_path, validate_manifest
else:
    from manage_capture_session import private_path, require, validate_dataset
    from replay_screenshots import CANVAS, MaaRecognizer, decide, read_png, template_path, validate_manifest


def evaluate(dataset_root, configuration_path, recognizer_factory=None):
    """Return an evaluation report; all preflight checks precede Maa construction."""
    report = {
        "version": 1, "offline_only": True, "recognition_only": True,
        "input_authorized": False, "passed": False, "framework_version": None,
        "configuration_sha256": None, "template_sha256": {}, "samples": [], "errors": [],
        "notes": ["场景识别通过不构成点击或其他输入授权。", "识别分数不是概率。"],
        "splits": {split: {"samples": 0, "false_accepts": 0, "false_refusals": 0,
                           "wrong_scene": 0, "faileds": 0} for split in ("train", "holdout")},
    }
    try:
        root = private_path(dataset_root)
        validation = validate_dataset(root)
        report["dataset_validation"] = validation
        require(validation["passed"], "数据集验收失败：存在待标签样本或错误")
        require(validation["evaluation_ready"], "评测必须同时包含已标签的train和holdout样本")
        configuration = Path(configuration_path).resolve()
        blob = configuration.read_bytes()
        report["configuration_sha256"] = hashlib.sha256(blob).hexdigest()
        config = validate_manifest(json.loads(blob.decode("utf-8-sig")), configuration.parent)
        templates = {}
        for scene in config["scenes"]:
            if scene["recognition"] == "TemplateMatch":
                path = template_path(scene, configuration.parent)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                templates[path] = digest
                report["template_sha256"][scene["template"]] = digest
        names = {scene["name"] for scene in config["scenes"]}
        sessions = []
        for manifest in sorted(root.glob("*/session.json")):
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            for sample in data["samples"]:
                require(sample["expected"]["scene"] is None or sample["expected"]["scene"] in names,
                        f"{data['id']}：标签引用未知场景{sample['expected']['scene']}")
            sessions.append((manifest.parent, data))
        recognizer = (recognizer_factory or MaaRecognizer)()
        report["framework_version"] = recognizer.version
        for directory, session in sessions:
            for sample in session["samples"]:
                path = directory / sample["file"]
                require(hashlib.sha256(path.read_bytes()).hexdigest() == sample["sha256"], "评测期间样本发生变化")
                require(all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in templates.items()), "评测期间模板发生变化")
                image = read_png(path)
                require(hashlib.sha256(path.read_bytes()).hexdigest() == sample["sha256"], "解码期间样本发生变化")
                recognitions = [recognizer.recognize(scene, image, configuration.parent)
                                for scene in config["scenes"]]
                actual = decide(recognitions, CANVAS)
                expected = sample["expected"]
                passed = actual == expected
                report["samples"].append({
                    "session": session["id"], "split": session["split"],
                    "image": sample["file"], "sha256": sample["sha256"],
                    "dimensions": list(CANVAS), "recognitions": recognitions,
                    "actual": actual, "expected": expected, "passed": passed,
                })
                metrics = report["splits"][session["split"]]
                metrics["samples"] += 1
                metrics["false_accepts"] += int(not expected["allowed"] and actual["allowed"])
                metrics["false_refusals"] += int(expected["allowed"] and not actual["allowed"])
                metrics["wrong_scene"] += int(expected["allowed"] and actual["allowed"]
                                              and expected["scene"] != actual["scene"])
                metrics["faileds"] += int(not passed)
        report["passed"] = bool(report["samples"]) and all(sample["passed"] for sample in report["samples"])
    except Exception as exc:
        report["errors"].append(str(exc))
        report["passed"] = False
    return report


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="使用Maa识别评测私有已标签train/holdout截图；不执行输入。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--recognition-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = private_path(args.report)
        require(output.suffix.lower() == ".json" and output.name != "session.json", "报告须为JSON且不能覆盖会话清单")
        require(output != args.recognition_manifest.resolve(), "报告不能覆盖识别清单")
        require(not args.report.is_symlink(), "报告路径不允许链接")
        report = evaluate(args.dataset, args.recognition_manifest)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "report": str(output), "errors": report["errors"]}, ensure_ascii=False))
        return 2 if report["errors"] else (0 if report["passed"] else 1)
    except (ValueError, OSError) as exc:
        print(f"评测报告写入失败：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
