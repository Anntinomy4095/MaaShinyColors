#!/usr/bin/env python3
"""离线构建训练识别档案：用新的1280x720原图替换指定阶段；绝不连接设备或浏览器。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.training_flow import ANCHORS, PREFIX, STAGES  # noqa: E402

CANVAS = (1280, 720)
REJECT_STAGES = ("One", "Confirm", "Result")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_root():
    return (ROOT / "dev_assets/private").resolve()


def rel(path):
    """Repo-relative path with backslashes, like the existing profile."""
    return str(PureWindowsPath(Path(path).resolve().relative_to(ROOT.resolve())))


def resolve_private(raw):
    path = Path(raw)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    require(path.is_relative_to(private_root()), f"路径必须位于dev_assets/private: {raw}")
    require(path.is_file(), f"文件不存在: {raw}")
    return path


def load_source(path):
    """Open a screenshot as opaque 1280x720 RGB; reject transparency and other sizes."""
    from PIL import Image
    require(path.read_bytes()[:8] == PNG_MAGIC, f"来源必须是PNG: {path.name}")
    with Image.open(path) as image:
        image.load()
        require(image.size == CANVAS, f"来源必须是1280x720: {path.name} 实际{image.size}")
        require(image.mode in ("RGB", "RGBA"), f"仅支持8位RGB/RGBA PNG: {path.name} ({image.mode})")
        if image.mode == "RGBA":
            require(image.getchannel("A").getextrema() == (255, 255), f"拒绝带透明度的来源: {path.name}")
        return image.convert("RGB")


def parse_pairs(items, label, *, unique):
    result = []
    for item in items or []:
        stage, sep, raw = item.partition("=")
        require(sep and stage in STAGES and raw, f"{label}格式应为Stage=路径，Stage属于{STAGES}: {item}")
        result.append((stage, raw))
    if unique:
        require(len({s for s, _ in result}) == len(result), f"{label}每个阶段只能指定一次")
    return result


def write_png(image, path):
    image.save(path, format="PNG")


def copy_source(info, path):
    """Byte copy when the file is already opaque RGB, otherwise a lossless RGB re-encode."""
    from PIL import Image
    with Image.open(info["path"]) as image:
        mode = image.mode
    if mode == "RGB":
        shutil.copyfile(info["path"], path)
    else:
        write_png(info["image"], path)


def blacked(image, roi):
    x, y, w, h = roi
    copy = image.copy()
    copy.paste((0, 0, 0), (x, y, x + w, y + h))
    return copy


def build(base, output, stage_sources, extra_rejects, limitations=""):
    base, output = Path(base).resolve(), Path(output).resolve()
    require(base.is_dir() and base.is_relative_to(private_root()), "--base必须是private下的现有目录")
    require(output.is_relative_to(private_root()), "--output必须位于dev_assets/private")
    require(not output.exists(), "--output已存在，拒绝覆盖")
    pairs = parse_pairs(stage_sources, "--stage-source", unique=True)
    extra_pairs = parse_pairs(extra_rejects, "--extra-reject", unique=False)
    require(pairs or extra_pairs, "至少需要一个--stage-source或--extra-reject")
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    origin = json.loads((base / "sources.json").read_text(encoding="utf-8"))
    truth = {t["derived"]: t for t in origin["samples"]}
    templates = {t["template"]: t for t in origin["templates"]}
    require(len(truth) == len(manifest["samples"]) and set(truth) == {s["image"] for s in manifest["samples"]},
            "基线样本与来源不一致")
    overrides = {}
    for stage, raw in pairs:
        path = resolve_private(raw)
        overrides[stage] = {"path": path, "image": load_source(path), "sha": sha256(path)}
    extras = []
    for stage, raw in extra_pairs:
        path = resolve_private(raw)
        extras.append((stage, path, load_source(path), sha256(path)))
    output.mkdir(parents=True)
    try:
        return _build(base, output, manifest, origin, truth, templates, overrides, extras, limitations)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def _build(base, output, manifest, origin, truth, templates, overrides, extras, limitations):
    out_templates, out_scenes = [], []
    for scene in manifest["scenes"]:
        stage, anchor = scene["name"].removeprefix(PREFIX).split(".", 1)
        name = scene["template"]
        if stage in overrides:
            o = overrides[stage]
            roi = ANCHORS[stage][anchor]
            require(scene["roi"] == roi, f"基线ROI与代码锚点不一致: {scene['name']}")
            x, y, w, h = roi
            write_png(o["image"].crop((x, y, x + w, y + h)), output / name)
            out_templates.append({"template": name, "template_sha256": sha256(output / name), "roi": list(roi),
                                  "source": rel(o["path"]), "source_sha256": o["sha"]})
        else:
            shutil.copyfile(base / name, output / name)
            entry = dict(templates[name])
            require(sha256(output / name) == entry["template_sha256"], f"基线模板哈希不符: {name}")
            out_templates.append(entry)
        out_scenes.append(dict(scene))

    samples, derived, dropped = [], [], []

    def add(sample_id, image_name, stage, guard, kind, provenance, sha_source, write):
        write(output / image_name)
        samples.append({"id": sample_id, "image": image_name, "expected": (
            {"scene": PREFIX + stage + ".Title", "allowed": True, "reason": "positive_guard"} if guard else
            {"scene": None, "allowed": False, "reason": "unknown_state"})})
        derived.append({"derived": image_name, "derived_sha256": sha256(output / image_name), "stage": stage,
                        "expected_guard": guard, "kind": kind, "source": provenance, "source_sha256": sha_source})

    def copy_through(sample):
        item = truth[sample["image"]]
        shutil.copyfile(base / sample["image"], output / sample["image"])
        require(sha256(output / sample["image"]) == item["derived_sha256"], f"基线样本哈希不符: {sample['image']}")
        samples.append(json.loads(json.dumps(sample)))
        derived.append(dict(item))

    def reject_source(other):
        """(provenance, sha, file) for a stage's current positive source."""
        if other in overrides:
            o = overrides[other]
            return rel(o["path"]), o["sha"], o["path"]
        entry = truth[f"{other}-source.png"]
        return entry["source"], entry["source_sha256"], base / entry["derived"]

    for sample in manifest["samples"]:
        item = truth[sample["image"]]
        stage, name = item["stage"], sample["image"]
        reject = re.fullmatch(r"(\w+?)-reject-(\w+)\.png", name)
        missing = re.fullmatch(rf"{stage}-missing-(\w+)\.png", name)
        if reject:
            target, other = reject.groups()
            if target in overrides or other in overrides:
                prov, sha, src = reject_source(other)
                add(sample["id"], name, target, False, "other_stage_original", prov, sha,
                    lambda p, src=src: shutil.copyfile(src, p))
            else:
                copy_through(sample)
        elif stage in overrides:
            o = overrides[stage]
            if missing and missing.group(1) in ANCHORS[stage]:
                roi = ANCHORS[stage][missing.group(1)]
                add(sample["id"], name, stage, False, "synthetic_removed_anchor", rel(o["path"]), o["sha"],
                    lambda p, roi=roi, o=o: write_png(blacked(o["image"], roi), p))
            elif name == f"{stage}-source.png":
                add(sample["id"], name, stage, True, "source_self_match", rel(o["path"]), o["sha"],
                    lambda p, o=o: copy_source(o, p))
            elif item["kind"] == "historical_other_rendering":
                copy_through(sample)
            else:
                dropped.append(name)
        else:
            copy_through(sample)

    present = {s["image"] for s in samples}
    for stage, o in overrides.items():
        if f"{stage}-source.png" not in present:
            add(f"{stage}-source", f"{stage}-source.png", stage, True, "source_self_match", rel(o["path"]), o["sha"],
                lambda p, o=o: copy_source(o, p))
    variants = []
    for stage, path, image, sha in extras:
        stem = re.sub(r"[^0-9A-Za-z]+", "-", path.stem).strip("-")
        name = f"{stage}-variant-{stem}.png"
        require(name not in {s["image"] for s in samples}, f"额外拒绝样本重名: {name}")
        variants.append(name)
        add(f"{stage}-variant-{stem}", name, stage, False, "extra_reject_variant", rel(path), sha,
            lambda p, image=image: write_png(image, p))

    require(len({s["id"] for s in samples}) == len(samples), "样本ID重复")
    notes = []
    if overrides:
        notes.append("本档案由tools/build_training_profile.py离线生成；已替换阶段: " + ", ".join(sorted(overrides)) +
                     "，其余阶段与基线逐字节相同并保留原始来源。")
    if dropped:
        notes.append("已替换阶段中无法由新原图重建的基线样本被丢弃: " + ", ".join(dropped) +
                     "（含NativeZero-*；如需保留旧渲染请用--extra-reject显式加入）。")
    text = " ".join(filter(None, [origin.get("limitations", ""), *notes, limitations.strip()]))
    (output / "manifest.json").write_text(json.dumps(
        {"version": manifest["version"], "canvas": manifest["canvas"], "scenes": out_scenes, "samples": samples},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "sources.json").write_text(json.dumps(
        {"version": origin["version"], "templates": out_templates, "samples": derived, "limitations": text},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "overridden": sorted(overrides), "samples": len(samples),
            "templates": len(out_templates), "dropped": dropped, "extra_rejects": variants}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线构建训练识别档案；不连接设备。")
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage-source", action="append", default=[], metavar="Stage=PNG")
    parser.add_argument("--extra-reject", action="append", default=[], metavar="Stage=PNG")
    parser.add_argument("--limitations", default="")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = build(args.base, args.output, args.stage_source, args.extra_reject, args.limitations)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
