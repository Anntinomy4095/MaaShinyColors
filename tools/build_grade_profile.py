#!/usr/bin/env python3
"""离线构建 Grade Fes 原生识别档案：从本机1280x720原图裁剪模板与样本；绝不连接设备或浏览器。"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.grade_flow import (ANCHORS, COLOR_CHECKS, EXCLUSIONS, GUARD_STAGES, OCR_FIELDS, STAGE_GROUPS,  # noqa: E402
                                   THRESHOLD, exclusion_scene, scene_name)

CANVAS = (1280, 720)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_SOURCE = ROOT / "dev_assets/private/manual-capture-kept-20261002/shop-grade"
DEFAULT_OUTPUT = ROOT / "dev_assets/private/in_app_browser/20261002-grade/grade-native-v1"

# Template source frame per anchor group (settled, no animation), visually classified 2026-10-02.
GROUP_FRAMES = {"Home": "026", "FesMenu": "026", "FesMain": "053", "FesHelp": "033", "ModeSelect": "064",
                "Difficulty": "066"}
STAGE_FRAMES = {"Home": "020", "FesMenu": "026", "FesMain": "053", "FesHelp": "033", "ModeSelect": "064",
                "Difficulty": "066"}
EXCLUSION_FRAMES = {"Loading": "037", "RehearsalTitle": "056"}
RIGHTS = "6/20"
TITLE = "グレードフェスの難易度を選択してください"
PROMPT = "フェス参加権を1消費します"
# (stage, frame, expected guard, kind)
NATURAL = [
    ("Home", "020", True, "native_other_frame"), ("Home", "039", True, "native_other_frame"),
    ("Home", "040", True, "native_other_frame"), ("Home", "072", True, "native_other_frame"),
    ("Home", "075", True, "native_other_frame"), ("Home", "082", True, "native_other_frame"),
    ("Home", "026", False, "native_fes_menu_open"), ("Home", "028", False, "native_loading_overlay"),
    ("Home", "036", False, "native_fes_main"),
    ("FesMenu", "024", True, "native_other_frame"), ("FesMenu", "025", True, "native_other_frame"),
    ("FesMenu", "026", True, "source_self_match"), ("FesMenu", "050", True, "native_other_frame"),
    ("FesMenu", "051", True, "native_other_frame"), ("FesMenu", "027", False, "native_button_sparkle"),
    ("FesMenu", "028", False, "native_loading_overlay"), ("FesMenu", "029", False, "native_loading_overlay"),
    ("FesMenu", "052", False, "native_loading_overlay"), ("FesMenu", "020", False, "native_home_without_popup"),
    ("FesMenu", "036", False, "native_fes_main"),
    ("FesMain", "031", True, "native_other_frame"), ("FesMain", "035", True, "native_other_frame"),
    ("FesMain", "036", True, "native_other_frame"), ("FesMain", "053", True, "source_self_match"),
    ("FesMain", "030", False, "native_loading_overlay"), ("FesMain", "032", False, "native_help_fading_in"),
    ("FesMain", "033", False, "native_help_popup"), ("FesMain", "034", False, "native_help_popup"),
    ("FesMain", "037", False, "native_loading_overlay"), ("FesMain", "038", False, "native_loading_overlay"),
    ("FesMain", "054", False, "native_mode_select"), ("FesMain", "055", False, "native_dialog_opening"),
    ("FesMain", "064", False, "native_mode_select"), ("FesMain", "069", False, "native_mode_select"),
    ("FesMain", "066", False, "native_grade_difficulty"), ("FesMain", "056", False, "native_rehearsal_difficulty"),
    ("FesHelp", "033", True, "source_self_match"), ("FesHelp", "034", True, "native_other_page"),
    ("FesHelp", "032", False, "native_help_fading_in"), ("FesHelp", "036", False, "native_fes_main"),
    ("FesHelp", "053", False, "native_fes_main"),
    ("ModeSelect", "054", True, "native_speech_bubble"), ("ModeSelect", "064", True, "source_self_match"),
    ("ModeSelect", "069", True, "native_other_frame"), ("ModeSelect", "055", False, "native_dialog_opening"),
    ("ModeSelect", "065", False, "native_dialog_opening"), ("ModeSelect", "070", False, "native_loading_overlay"),
    ("ModeSelect", "071", False, "native_loading_overlay"), ("ModeSelect", "053", False, "native_fes_main"),
    ("ModeSelect", "066", False, "native_grade_difficulty"), ("ModeSelect", "033", False, "native_help_popup"),
    ("Difficulty", "066", True, "source_self_match"), ("Difficulty", "067", False, "native_circle_shine"),
    ("Difficulty", "068", False, "native_bubble_animation"), ("Difficulty", "056", False, "native_rehearsal_difficulty"),
    ("Difficulty", "057", False, "native_rehearsal_difficulty"), ("Difficulty", "058", False, "native_rehearsal_difficulty"),
    ("Difficulty", "059", False, "native_rehearsal_difficulty"), ("Difficulty", "060", False, "native_rehearsal_difficulty"),
    ("Difficulty", "061", False, "native_rehearsal_difficulty"), ("Difficulty", "062", False, "native_rehearsal_difficulty"),
    ("Difficulty", "063", False, "native_rehearsal_difficulty"), ("Difficulty", "065", False, "native_dialog_opening"),
    ("Difficulty", "064", False, "native_mode_select"), ("Difficulty", "053", False, "native_fes_main"),
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path):
    return str(PureWindowsPath(Path(path).resolve().relative_to(ROOT.resolve())))


def load(path):
    from PIL import Image
    require(path.read_bytes()[:8] == PNG_MAGIC, f"来源必须是PNG: {path.name}")
    with Image.open(path) as image:
        image.load()
        require(image.size == CANVAS and image.mode in ("RGB", "RGBA"), f"来源必须是1280x720 RGB: {path.name}")
        if image.mode == "RGBA":
            require(image.getchannel("A").getextrema() == (255, 255), f"拒绝透明来源: {path.name}")
        return image.convert("RGB")


def crop(image, roi):
    x, y, w, h = roi
    return image.crop((x, y, x + w, y + h))


def blacked(image, roi):
    x, y, w, h = roi
    copy = image.copy()
    copy.paste((0, 0, 0), (x, y, x + w, y + h))
    return copy


def dimmed(image, factor=0.6):
    from PIL import Image
    return Image.eval(image, lambda v: int(v * factor))


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    private = (ROOT / "dev_assets/private").resolve()
    require(source.is_dir() and source.is_relative_to(private), "--source必须是private下的现有目录")
    require(output.is_relative_to(private), "--output必须位于dev_assets/private")
    require(not output.exists(), "--output已存在，拒绝覆盖")
    frames = {}

    def frame(name):
        if name not in frames:
            path = source / (name + ".png")
            require(path.is_file(), f"缺少来源帧 {name}")
            frames[name] = (path, load(path), sha256(path))
        return frames[name]

    output.mkdir(parents=True)
    try:
        return _build(output, frame)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def _build(output, frame):
    scenes, templates = [], []

    def template(name, roi, frame_name):
        path, image, digest = frame(frame_name)
        file_name = name.removeprefix("SC.Grade.").replace(".", "-") + ".png"
        crop(image, roi).save(output / file_name, format="PNG")
        scenes.append({"name": name, "recognition": "TemplateMatch", "roi": list(roi), "template": file_name,
                       "threshold": THRESHOLD})
        templates.append({"template": file_name, "template_sha256": sha256(output / file_name), "roi": list(roi),
                          "source": rel(path), "source_sha256": digest})

    for group, anchors in ANCHORS.items():
        for anchor, roi in anchors.items():
            template(scene_name(group, anchor), roi, GROUP_FRAMES[group])
    for name, roi in EXCLUSIONS.items():
        template(exclusion_scene(name), roi, EXCLUSION_FRAMES[name])
    for group, checks in COLOR_CHECKS.items():
        for name, rule in checks.items():
            scenes.append({"name": scene_name(group, name), "recognition": "ColorMatch", "roi": rule["roi"],
                           "lower": rule["lower"], "upper": rule["upper"], "count": rule["count"]})

    samples, truth = [], []

    def add(sample_id, stage, guard, kind, frame_name, image, *, ocr=None, names=None, evidence_case=None, note=None):
        path, _, digest = frame(frame_name)
        name = sample_id + ".png"
        require(not (output / name).exists(), f"样本重名 {name}")
        image.save(output / name, format="PNG")
        first = scene_name(STAGE_GROUPS[stage][-1], next(iter(ANCHORS[STAGE_GROUPS[stage][-1]])))
        samples.append({"id": sample_id, "image": name, "expected": (
            {"scene": first, "allowed": True, "reason": "positive_guard"} if guard else
            {"scene": None, "allowed": False, "reason": "unknown_state"})})
        row = {"derived": name, "derived_sha256": sha256(output / name), "stage": stage, "expected_guard": guard,
               "kind": kind, "source": rel(path), "source_sha256": digest}
        if ocr is not None:
            row["ocr_expected"] = ocr
        if names is not None:
            row["names_expected"] = names
        if evidence_case:
            row["evidence_case"] = evidence_case
        if note:
            row["note"] = note
        truth.append(row)

    rights_stages = ("FesMain", "ModeSelect", "Difficulty")
    for stage, name, guard, kind in NATURAL:
        ocr = {"rights": RIGHTS} if guard and stage in rights_stages else None
        names = {"Title": TITLE, "Prompt": PROMPT} if guard and stage == "Difficulty" else None
        add(f"{stage}-{name}", stage, guard, kind, name, frame(name)[1], ocr=ocr, names=names)
    # Missing-anchor black negatives (template and colour anchors) and cross-stage rejects.
    for stage in GUARD_STAGES:
        source_name = STAGE_FRAMES[stage]
        image = frame(source_name)[1]
        for group in STAGE_GROUPS[stage]:
            for anchor, roi in ANCHORS[group].items():
                add(f"{stage}-missing-{group}-{anchor}", stage, False, "synthetic_removed_anchor", source_name, blacked(image, roi))
            for check, rule in COLOR_CHECKS.get(group, {}).items():
                add(f"{stage}-missing-{group}-{check}", stage, False, "synthetic_removed_colour", source_name,
                    blacked(image, rule["roi"]))
        for other in GUARD_STAGES:
            if other != stage:
                add(f"{stage}-reject-{other}", stage, False, "other_stage_original", STAGE_FRAMES[other],
                    frame(STAGE_FRAMES[other])[1])
    # A uniform dimming overlay keeps TM_CCOEFF_NORMED scores; only the colour checks refuse it.
    add("FesMain-synthetic-dimmed", "FesMain", False, "synthetic_dim_overlay", "053", dimmed(frame("053")[1]),
        note="整帧×0.6模拟未知压暗弹窗：模板仍匹配，白色标题栏颜色检查必须拒绝。")
    add("ModeSelect-synthetic-dimmed", "ModeSelect", False, "synthetic_dim_overlay", "064", dimmed(frame("064")[1]))
    add("Difficulty-synthetic-dimmed", "Difficulty", False, "synthetic_dim_overlay", "066", dimmed(frame("066")[1]),
        note="难度页再被压暗：白色消耗提示气泡颜色检查必须拒绝。")
    difficulty = frame("066")[1]
    box = difficulty.copy()
    box.paste((255, 140, 0), (874, 652, 904, 682))
    add("Difficulty-synthetic-checkbox-changed", "Difficulty", False, "synthetic_checkbox_changed", "066", box,
        note="「まとめて消費」复选框内容变化（勾选状态未采样）：未勾选模板必须拒绝。")
    # Rights 0/20: paste the "0" glyph of "20" over the "6" (columns measured on frame 066).
    zero = difficulty.copy()
    zero.paste(difficulty.crop((1173, 665, 1184, 681)), (1147, 665))
    add("Difficulty-synthetic-rights-0", "Difficulty", True, "synthetic_digit_paste", "066", zero,
        ocr={"rights": "0/20"}, names={"Title": TITLE, "Prompt": PROMPT}, evidence_case="rights_zero",
        note="把“20”的“0”字形贴到“6”的位置模拟参加权 0/20；必须由规则拒绝。")
    # The cost digit "1" removed (white fill): the prompt no longer proves a single right.
    blank = difficulty.copy()
    blank.paste((255, 255, 255), (1052, 580, 1076, 608))
    # Measured: the Prompt template still scores 0.988 (one glyph); the OCR'd prompt text must refuse it.
    add("Difficulty-synthetic-prompt-digit-removed", "Difficulty", True, "synthetic_prompt_changed", "066", blank,
        ocr={"rights": RIGHTS}, evidence_case="prompt_digit_removed",
        note="消耗数字被抹去：模板不区分单个字形，必须由提示文字 OCR 规则拒绝。")
    (output / "manifest.json").write_text(json.dumps(
        {"version": 1, "canvas": list(CANVAS), "scenes": scenes, "samples": samples},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "sources.json").write_text(json.dumps(
        {"version": 1, "templates": templates, "samples": truth,
         "ocr_fields": {k: v[0] for k, v in OCR_FIELDS.items()},
         "limitations": ("本档案由tools/build_grade_profile.py从2026-10-02用户手动操作的本机原生帧离线生成。"
                         "无点击难度后的任何画面、无勾选「まとめて消費」、无参加权不足、无グレード判定弹窗样本；"
                         "合成负例仅证明拒绝路径。")},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "scenes": len(scenes), "samples": len(samples)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线构建 Grade Fes 识别档案；不连接设备。")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = build(args.source, args.output)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
