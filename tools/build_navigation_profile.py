#!/usr/bin/env python3
"""离线构建日常导航识别档案：用新的1280x720原图重新裁剪指定页面组的锚点模板。

只读取dev_assets/private下的截图并写入新的private目录；绝不连接设备、浏览器或宿主。
新样本的动作真值由Maa实际识别 + agent.core.daily_navigation.actionable计算，并与
页面组的预期动作集合核对；基线样本在新模板下重新评估，真值变化的样本被丢弃并记录。
"""
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

from agent.core.daily_config import load_json  # noqa: E402
from agent.core.daily_navigation import ACTION_GROUPS, GROUPS, ROIS, actionable, load_contract  # noqa: E402

CANVAS = (1280, 720)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FOREGROUND = {"state": "no_known_popup"}
NATIVE_SUFFIX = "Native"
DEFAULT_MANIFEST = "daily-navigation-manifest.json"
DEFAULT_SOURCES = "daily-navigation-sources.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_root():
    return (ROOT / "dev_assets/private").resolve()


def rel(path):
    """Repo-relative path with backslashes, like the existing profiles."""
    return str(PureWindowsPath(Path(path).resolve().relative_to(ROOT.resolve())))


def resolve_private(raw, *, directory=False):
    path = Path(raw)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    require(path.is_relative_to(private_root()), f"路径必须位于dev_assets/private: {raw}")
    require(path.is_dir() if directory else path.is_file(), f"{'目录' if directory else '文件'}不存在: {raw}")
    return path


def plain_name(raw, label):
    require(isinstance(raw, str) and raw and Path(raw).name == raw and raw not in (".", ".."), f"{label}必须是目录内文件名: {raw}")
    return raw


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
        return image.convert("RGB"), image.mode


def parse_pairs(items, label, *, unique):
    result = []
    for item in items or []:
        group, sep, raw = item.partition("=")
        require(sep and group in GROUPS and raw, f"{label}格式应为Group=路径，Group属于{tuple(GROUPS)}: {item}")
        result.append((group, raw))
    if unique:
        require(len({g for g, _ in result}) == len(result), f"{label}每个页面组只能指定一次")
    return result


def parse_native(raw):
    if raw is None:
        return None
    groups = [g.strip() for g in raw.split(",") if g.strip()]
    require(groups and len(set(groups)) == len(groups) and all(g in GROUPS for g in groups),
            f"--require-native-groups须为逗号分隔且不重复的页面组: {raw}")
    return groups


def short(scene_name):
    return scene_name.rsplit(".", 1)[1]


def group_actions(group):
    return [a for a, g in ACTION_GROUPS.items() if g == group]


def ordered(actions):
    return [a for a in ACTION_GROUPS if a in set(actions)]


def blacked(image, roi):
    x, y, w, h = roi
    copy = image.copy()
    copy.paste((0, 0, 0), (x, y, x + w, y + h))
    return copy


def stem_of(path):
    return re.sub(r"[^0-9A-Za-z]+", "-", Path(path).stem).strip("-") or "image"


def build(base, base_manifest, base_sources, output, group_sources, negatives=(), limitations="",
          native_groups=None, output_manifest=DEFAULT_MANIFEST, output_sources=DEFAULT_SOURCES, recognizer=None,
          skip_scenes=()):
    base = resolve_private(base, directory=True)
    manifest_path = base / plain_name(base_manifest, "--base-manifest")
    sources_path = base / plain_name(base_sources, "--base-sources")
    output = Path(output)
    output = ((ROOT / output) if not output.is_absolute() else output).resolve()
    require(output.is_relative_to(private_root()) and output != private_root(), "--output-dir必须位于dev_assets/private内")
    require(not output.exists(), "--output-dir已存在，拒绝覆盖")
    output_manifest = plain_name(output_manifest, "--output-manifest")
    output_sources = plain_name(output_sources, "--output-sources")
    require(output_manifest != output_sources, "输出清单与来源文件名不能相同")
    pairs = parse_pairs(group_sources, "--group-source", unique=True)
    negative_pairs = parse_pairs(negatives, "--negative", unique=False)
    require(pairs or negative_pairs, "至少需要一个--group-source或--negative")
    native = [] if native_groups is None else list(native_groups)
    require(set(native) <= {g for g, _ in pairs}, "--require-native-groups只能声明本次用--group-source重新校准的页面组")
    skip = set(skip_scenes)
    require(skip <= {n for g, _ in pairs for n in GROUPS[g]}, "--skip-scenes只能列出本次重新校准页面组内的锚点")
    # Anchors registered after the base was built must be cropped from a --group-source of their group.
    base_names = {scene.get("name") for scene in load_json(manifest_path).get("scenes", [])}
    missing = [n for n in ROIS if n not in base_names]
    calibrated = {n for g, _ in pairs for n in GROUPS[g]}
    unprovided = [n for n in missing if n not in calibrated or n in skip]
    require(not unprovided, "基线缺少锚点且未通过--group-source提供其页面组: " + ", ".join(unprovided))
    # The base must itself be a valid, hash-bound navigation contract (apart from those new anchors).
    rebased = [s["name"] for s in load_json(manifest_path).get("scenes", []) if s.get("name") in ROIS and s.get("roi") != ROIS[s["name"]]]
    unrebased = [n for n in rebased if n not in calibrated or n in skip]
    require(not unrebased, "代码ROI已变更的基线锚点须通过--group-source重新校准: " + ", ".join(unrebased))
    data, truth, _ = load_contract(manifest_path, sources_path, missing_scenes=missing, rebased_scenes=rebased)
    origin = load_json(sources_path)
    overrides = {}
    for group, raw in pairs:
        path = resolve_private(raw)
        image, mode = load_source(path)
        overrides[group] = {"path": path, "image": image, "mode": mode, "sha": sha256(path)}
    extra = []
    for group, raw in negative_pairs:
        path = resolve_private(raw)
        image, mode = load_source(path)
        extra.append({"group": group, "path": path, "image": image, "mode": mode, "sha": sha256(path)})
    if recognizer is None:
        from tools.replay_screenshots import MaaRecognizer
        recognizer = MaaRecognizer()
    output.mkdir(parents=True)
    try:
        return _build(base, manifest_path, sources_path, data, truth, origin, output, overrides, extra,
                      limitations, native, output_manifest, output_sources, recognizer, skip, missing)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def _build(base, manifest_path, sources_path, data, truth, origin, output, overrides, extra,
           limitations, native, output_manifest, output_sources, recognizer, skip=frozenset(), missing=()):
    import numpy as np
    from tools.replay_screenshots import decide, read_png

    def bgr(image):
        return np.ascontiguousarray(np.asarray(image)[:, :, ::-1])

    template_origin = {t["template"]: t for t in origin["templates"]}
    # A skipped scene keeps its base template, e.g. a tab that is not selected on this source frame.
    overridden_scenes = {n for g in overrides for n in GROUPS[g] if n not in skip}
    new_templates = {}
    for group in overrides:
        for name in GROUPS[group]:
            if name in skip:
                continue
            new_templates[name] = f"navprofile-{group}-{short(name)}.png"
    kept_template_names = {s["template"] for s in data["scenes"] if s["name"] not in overridden_scenes}
    require(not kept_template_names & set(new_templates.values()), "新模板文件名与保留模板冲突")

    # Templates: overridden groups are cropped from the new source; native variants share
    # the primary ROI and receive the same crop so either variant matches the new rendering.
    out_scenes, out_templates = [], []

    def recrop(scene):
        name = scene["name"]
        group = next(g for g in overrides if name in GROUPS[g])
        o = overrides[group]
        x, y, w, h = ROIS[name]
        crop = o["image"].crop((x, y, x + w, y + h))
        require(len(set(crop.get_flattened_data())) > 1, f"新模板为恒色，拒绝: {name}")
        file = new_templates[name]
        crop.save(output / file, format="PNG")
        variant_of = name[:-len(NATIVE_SUFFIX)] if name.endswith(NATIVE_SUFFIX) else None
        out_scenes.append(dict(scene, template=file, roi=list(ROIS[name])))
        out_templates.append({"template": file, "sha256": sha256(output / file), "scene": name, "roi": list(ROIS[name]),
                              "source": rel(o["path"]), "source_sha256": o["sha"],
                              **({"variant_of": variant_of} if variant_of in GROUPS[group] else {})})

    for scene in data["scenes"]:
        name = scene["name"]
        require(scene["roi"] == ROIS[name] or name in overridden_scenes, f"基线ROI与代码锚点不一致: {name}")
        if name in overridden_scenes:
            recrop(scene)
        else:
            file = scene["template"]
            if not (output / file).exists():
                shutil.copyfile(base / file, output / file)
                require(sha256(output / file) == template_origin[file]["sha256"], f"基线模板哈希不符: {file}")
                out_templates.append(dict(template_origin[file]))
            out_scenes.append(dict(scene))
    # Anchors absent from the base are added with the fixed guard contract (ROI and threshold from code).
    for name in missing:
        require(name in overridden_scenes, f"基线缺少的锚点必须重新裁剪: {name}")
        recrop({"name": name, "roi": list(ROIS[name]), "recognition": "TemplateMatch", "template": "", "threshold": .95})
    old_scenes, new_scenes = data["scenes"], out_scenes

    def hits_for(image, old_hits=None):
        if old_hits is None:
            return [recognizer.recognize(s, image, base) for s in old_scenes]
        by_name = {s["name"]: h for s, h in zip(old_scenes, old_hits)}
        return [recognizer.recognize(s, image, output) if s["name"] in overridden_scenes else by_name[s["name"]]
                for s in new_scenes]

    def computed(hits):
        return [a for a in ACTION_GROUPS if actionable(a, hits, FOREGROUND)]

    # New samples (positives, synthetic missing-anchor negatives, explicit negatives).
    new_samples, new_truth = [], []

    def add(file, image, source, kind, group, expected, synthetic, copy_from=None):
        require(not (output / file).exists(), f"样本文件名重复: {file}")
        if copy_from is not None:
            shutil.copyfile(copy_from, output / file)
        else:
            image.save(output / file, format="PNG")
        frame = read_png(output / file)
        # Every kept template was copied into the output, so the new profile is evaluated as a whole.
        hits = [recognizer.recognize(s, frame, output) for s in new_scenes]
        actions = computed(hits)
        require(set(actions) == set(expected),
                f"{file}识别真值{actions}与预期{ordered(expected)}不符；拒绝生成档案")
        new_samples.append({"id": Path(file).stem, "image": file, "expected": decide(hits, CANVAS)})
        new_truth.append({"image": file, "sha256": sha256(output / file), "original": rel(source["path"]),
                          "original_sha256": source["sha"], "actions": ordered(actions), "synthetic": synthetic,
                          "group": group, "kind": kind})

    for group, o in overrides.items():
        add(f"navprofile-{group}-positive.png", o["image"], o, "group_source_positive", group, group_actions(group), False,
            copy_from=o["path"] if o["mode"] == "RGB" else None)
        seen = set()
        for name in GROUPS[group]:
            roi = tuple(ROIS[name])
            if name in skip or roi in seen:
                continue
            seen.add(roi)
            add(f"navprofile-{group}-missing-{short(name)}.png", blacked(o["image"], roi), o,
                "synthetic_removed_anchor", group, [], True)
    for item in extra:
        file = f"navprofile-{item['group']}-negative-{stem_of(item['path'])}.png"
        add(file, item["image"], item, "explicit_negative", item["group"], [], False,
            copy_from=item["path"] if item["mode"] == "RGB" else None)

    # Base samples: re-evaluate under the new templates and keep only unchanged truths.
    vocabulary = {a for proof in truth.values() for a in proof["actions"]}
    taken = {s["image"] for s in new_samples}
    kept_samples, kept_truth, dropped, superseded, extended = [], [], [], [], []
    for sample in data["samples"]:
        proof = truth[sample["image"]]
        if sample["image"] in taken or sample["id"] in {s["id"] for s in new_samples}:
            superseded.append(sample["image"])
            continue
        frame = read_png(base / sample["image"])
        old_hits = hits_for(frame)
        old, new = set(computed(old_hits)), set(computed(hits_for(frame, old_hits)))
        base_actions = list(proof["actions"])
        groups = {ACTION_GROUPS[a] for a in base_actions}
        # Actions registered after the base was built inherit truth from a sibling of the same guard group.
        additions = [a for a in ordered(new - set(base_actions)) if a not in vocabulary and ACTION_GROUPS[a] in groups]
        target = set(base_actions) | set(additions)
        if new != old and new != target:
            dropped.append(f"{sample['image']}(基线{ordered(base_actions)}→新模板{ordered(new)})")
            continue
        shutil.copyfile(base / sample["image"], output / sample["image"])
        require(sha256(output / sample["image"]) == proof["sha256"], f"基线样本哈希不符: {sample['image']}")
        entry = json.loads(json.dumps(proof))
        if additions:
            entry["actions"] = base_actions + additions
            extended.append(f"{sample['image']}+{additions}")
        kept_samples.append(json.loads(json.dumps(sample)))
        kept_truth.append(entry)

    samples, sample_truth = kept_samples + new_samples, kept_truth + new_truth
    require(len({s["id"] for s in samples}) == len(samples), "样本ID重复")
    require(len({s["image"] for s in samples}) == len(samples), "样本文件重复")
    base_native = origin.get("native_groups", [])
    require(type(base_native) is list and set(base_native) <= set(GROUPS), "基线native_groups无效")
    native_out = sorted((set(base_native) - set(overrides)) | set(native))

    notes = []
    if overrides:
        notes.append("本档案由tools/build_navigation_profile.py离线生成；已重新裁剪页面组: " + ", ".join(overrides) +
                     "（Native变体写入相同新裁剪）；其余模板与样本与基线逐字节相同。")
    if missing:
        notes.append("基线缺少而本次新增的锚点: " + ", ".join(missing) + "（基线样本在新增锚点下重新识别）。")
    if extra:
        notes.append("显式负样本: " + ", ".join(s["image"] for s in new_samples if s["image"].count("-negative-")) + "。")
    if dropped:
        notes.append("新模板下真值改变而被丢弃的基线样本: " + "; ".join(dropped) + "。")
    if superseded:
        notes.append("被同名新样本取代的基线样本: " + ", ".join(superseded) + "。")
    if extended:
        notes.append("按同组已有真值补入新登记动作的基线样本: " + "; ".join(extended) + "。")
    text = " ".join(filter(None, [origin.get("limitations", ""), *notes, (limitations or "").strip()]))
    manifest_out = output / output_manifest
    sources_out = output / output_sources
    manifest_out.write_text(json.dumps({"version": data["version"], "canvas": data["canvas"], "scenes": out_scenes,
                                        "samples": samples}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sources_out.write_text(json.dumps({"templates": out_templates, "samples": sample_truth, "native_groups": native_out,
                                       "base": {"manifest": rel(manifest_path), "manifest_sha256": sha256(manifest_path),
                                                "sources": rel(sources_path), "sources_sha256": sha256(sources_path)},
                                       "limitations": text}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _, out_truth, digest = load_contract(manifest_out, sources_out)
    require({k: v["actions"] for k, v in out_truth.items()} == {t["image"]: t["actions"] for t in sample_truth},
            "输出真值与写入内容不一致")
    return {"output": str(output), "manifest": str(manifest_out), "sources": str(sources_out),
            "configuration_sha256": digest, "overridden": list(overrides), "native_groups": native_out,
            "samples": len(samples), "new_samples": [s["image"] for s in new_samples],
            "dropped": dropped, "superseded": superseded, "extended": extended, "templates": len(out_templates),
            "added_scenes": list(missing)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线构建日常导航识别档案；不连接设备或宿主。")
    parser.add_argument("--base", type=Path, required=True, help="包含基线清单与来源的private目录")
    parser.add_argument("--base-manifest", required=True)
    parser.add_argument("--base-sources", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--output-sources", default=DEFAULT_SOURCES)
    parser.add_argument("--group-source", action="append", default=[], metavar="Group=PNG")
    parser.add_argument("--negative", action="append", default=[], metavar="Group=PNG")
    parser.add_argument("--require-native-groups", default=None, metavar="G1,G2")
    parser.add_argument("--skip-scenes", default="", metavar="SCENE1,SCENE2",
                        help="重新校准组内保留基线模板的锚点（如本帧未选中的页签）")
    parser.add_argument("--limitations", default="")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = build(args.base, args.base_manifest, args.base_sources, args.output_dir, args.group_source,
                       args.negative, args.limitations, parse_native(args.require_native_groups),
                       args.output_manifest, args.output_sources,
                       skip_scenes=[s for s in args.skip_scenes.split(",") if s])
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
