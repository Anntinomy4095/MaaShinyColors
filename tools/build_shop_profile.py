#!/usr/bin/env python3
"""离线构建每日商店原生识别档案：从本机1280x720原图裁剪模板与样本；绝不连接设备或浏览器。"""
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

from agent.core.shop_flow import (ANCHORS, BUTTON_COLOR, LIST_COMMON, monthly_roi, monthly_scene, OFFER_RANKS, button_roi, button_scene, CONFIRM_NAME_ROI, EXCLUSIONS, GUARD_STAGES, ITEMS, OCR_FIELDS,  # noqa: E402
                                  PREFIX, THRESHOLD, exclusion_scene, item_scene, list_name_roi, scene_name)

CANVAS = (1280, 720)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_SOURCE = ROOT / "dev_assets/private/manual-capture-kept-20261002/shop-grade"
DEFAULT_OUTPUT = ROOT / "dev_assets/private/in_app_browser/20261002-shop/shop-native-v1"

# Template source frame per stage (settled, no animation).
TEMPLATE_FRAMES = {"Home": "000", "Premium": "005", "List": "007", "Confirm": "009",
                   # Live 交換結果 dialog captured by the runner itself after rank 1.
                   "Result": "../../shop-20261002-04-confirm1-frames/frame-024"}
LOADING_FRAME = "006"
# item_id -> (List frame, rank) and Confirm frame, as visually verified on 2026-10-02.
ITEM_FRAMES = {"skip_ticket": ("007", 1, "009"), "gold_training_ticket": ("007", 2, "013"),
               "illumination_stars_towel": ("007", 3, "016")}
LIST_IDENTITY = {1: "skip_ticket", 2: "gold_training_ticket", 3: "illumination_stars_towel"}
LIST_OCR = {"List.money": "17,678,512", "List.limit1": "0/1", "List.limit2": "0/1", "List.limit3": "0/1",
            "List.price1": "10", "List.price2": "10", "List.price3": "5,000"}
CONFIRM_OCR = {
    "009": {"Confirm.quantity": "1", "Confirm.limit_after": "1", "Confirm.limit_max": "1", "Confirm.required": "10",
            "Confirm.money_before": "17,678,512", "Confirm.money_after": "17,678,502",
            "Confirm.item_before": "608", "Confirm.item_after": "609(+1)"},
    "013": {"Confirm.quantity": "1", "Confirm.limit_after": "1", "Confirm.limit_max": "1", "Confirm.required": "10",
            "Confirm.money_before": "17,678,512", "Confirm.money_after": "17,678,502",
            "Confirm.item_before": "4,788", "Confirm.item_after": "4,789(+1)"},
    "016": {"Confirm.quantity": "1", "Confirm.limit_after": "1", "Confirm.limit_max": "1", "Confirm.required": "5,000",
            "Confirm.money_before": "17,678,512", "Confirm.money_after": "17,673,512",
            "Confirm.item_before": "4", "Confirm.item_after": "5(+1)"},
}
NAMES = {"skip_ticket": "スキップチケット", "gold_training_ticket": "ゴールドトレーニングチケット",
         "illumination_stars_towel": "イルミネーションスターズのタオル"}
PROMPT = "マニーを消費して以下のアイテムと交換しますか?"
LIST_NAMES = {str(r): NAMES[i] for r, i in LIST_IDENTITY.items()}
CONFIRM_IDENTITY = {"009": "skip_ticket", "013": "gold_training_ticket", "016": "illumination_stars_towel"}
# Natural samples (visually classified 2026-10-02). guard: expected stage guard.
NATURAL = [
    ("Home", "000", True, "source_self_match"), ("Home", "001", True, "native_other_frame"),
    ("Home", "002", True, "native_other_frame"), ("Home", "020", True, "native_other_frame"),
    ("Home", "024", True, "native_other_frame"), ("Home", "040", True, "native_other_frame"),
    ("Home", "072", True, "native_other_frame"), ("Home", "082", True, "native_other_frame"),
    ("Home", "003", False, "native_loading_overlay"), ("Home", "004", False, "native_loading_overlay"),
    ("Home", "052", False, "native_loading_overlay"), ("Home", "036", False, "native_fes_main"),
    ("Home", "056", False, "native_fes_difficulty"), ("Home", "034", False, "native_help_popup"),
    ("Premium", "005", True, "source_self_match"), ("Premium", "006", False, "native_loading_overlay"),
    ("List", "007", True, "source_self_match"), ("List", "011", True, "native_button_sparkle"),
    ("List", "014", True, "native_other_frame"), ("List", "018", True, "native_other_frame"),
    ("List", "012", False, "native_dialog_opening"), ("List", "017", False, "native_dialog_opening"),
    ("List", "019", False, "native_loading_overlay"),
    ("Confirm", "009", True, "source_self_match"), ("Confirm", "013", True, "native_other_item"),
    ("Confirm", "016", True, "native_other_item"),
    ("Confirm", "008", False, "native_dialog_animation"), ("Confirm", "010", False, "native_dialog_animation"),
    ("Confirm", "015", False, "native_dialog_animation"),
]
OCR_REQUIRED = {"007", "009", "013", "016"}


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


def pasted(target, donor, roi, at=None):
    x, y, w, h = roi
    copy = target.copy()
    copy.paste(donor.crop((x, y, x + w, y + h)), tuple(at or (x, y)))
    return copy


def money_replaced(result):
    """Result frame with the money value blanked (table colour) and the item count pasted in its place."""
    copy = result.copy()
    mx, my, mw, mh = OCR_FIELDS["Result.money"][0]
    copy.paste(result.getpixel((mx + 2, my + 2)), (mx, my, mx + mw, my + 22))
    ix, iy, iw, ih = OCR_FIELDS["Result.items"][0]
    copy.paste(result.crop((ix, iy, ix + iw, iy + 22)), (mx, my))
    return copy


def glyph_columns(image, roi, dark=180, rows=None):
    """Dark-glyph column runs inside a numeric crop (for synthetic digit pasting)."""
    import numpy as np
    x, y, w, h = roi
    h = rows or h  # exclude dotted separators below the digits
    pixels = np.asarray(image.crop((x, y, x + w, y + h))).astype(int).mean(axis=2)
    columns = (pixels < dark).any(axis=0)
    runs, start = [], None
    for index, value in enumerate(list(columns) + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((x + start, x + index))
            start = None
    return runs


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

    def template(name, image_name, roi, frame_name):
        path, image, digest = frame(frame_name)
        crop(image, roi).save(output / image_name, format="PNG")
        scenes.append({"name": name, "recognition": "TemplateMatch", "roi": list(roi), "template": image_name,
                       "threshold": THRESHOLD})
        templates.append({"template": image_name, "template_sha256": sha256(output / image_name), "roi": list(roi),
                          "source": rel(path), "source_sha256": digest})

    for stage in GUARD_STAGES:
        for anchor, roi in ANCHORS[stage].items():
            template(scene_name(stage, anchor), f"{stage}-{anchor}.png", roi, TEMPLATE_FRAMES[stage])
    for name, roi in EXCLUSIONS.items():
        template(exclusion_scene(name), f"Exclude-{name}.png", roi, LOADING_FRAME)
    # Grey monthly-card header, cropped at position 2 of the live after-purchase frame (layout is
    # translation-invariant at 270 px; positions 1 and 3 reuse the same pixels).
    bought_path, bought_image, bought_sha = frame("../shop-after-two-bought-20261002")
    header = crop(bought_image, monthly_roi(2))
    for position in OFFER_RANKS:
        name = f"List-MonthlyHeader{position}.png"
        header.save(output / name, format="PNG")
        scenes.append({"name": monthly_scene(position), "recognition": "TemplateMatch", "roi": monthly_roi(position),
                       "template": name, "threshold": THRESHOLD})
        templates.append({"template": name, "template_sha256": sha256(output / name), "roi": monthly_roi(position),
                          "source": rel(bought_path), "source_sha256": bought_sha, "source_roi": monthly_roi(2)})
    for position in OFFER_RANKS:
        scenes.append({"name": button_scene(position), "recognition": "ColorMatch", "roi": button_roi(position),
                       "lower": BUTTON_COLOR["lower"], "upper": BUTTON_COLOR["upper"], "count": BUTTON_COLOR["count"]})
    for item, (list_frame, rank, confirm_frame) in ITEM_FRAMES.items():
        template(item_scene(item, "List"), f"Item-{item}-List.png", list_name_roi(rank), list_frame)
        template(item_scene(item, "Confirm"), f"Item-{item}-Confirm.png", CONFIRM_NAME_ROI, confirm_frame)

    samples, truth = [], []

    def add(sample_id, stage, guard, kind, frame_name, image, *, ocr=None, ocr_required=False, identity=None,
            evidence_case=None, note=None, names=None, slots=None):
        path, _, digest = frame(frame_name)
        name = sample_id + ".png"
        require(not (output / name).exists(), f"样本重名 {name}")
        image.save(output / name, format="PNG")
        samples.append({"id": sample_id, "image": name, "expected": (
            {"scene": scene_name("List" if stage == "After" else stage, next(iter(ANCHORS["List" if stage == "After" else stage]))),
             "allowed": True, "reason": "positive_guard"} if guard else
            {"scene": None, "allowed": False, "reason": "unknown_state"})})
        row = {"derived": name, "derived_sha256": sha256(output / name), "stage": stage, "expected_guard": guard,
               "kind": kind, "source": rel(path), "source_sha256": digest}
        if ocr is not None:
            row.update(ocr_expected=ocr, ocr_required=ocr_required)
        if identity is not None:
            row["identity_expected"] = identity
            if names is None:
                names = dict(LIST_NAMES) if "1" in identity else {
                    "Confirm": NAMES[identity["Confirm"]] if identity["Confirm"] else None, "CurrencyPrompt": PROMPT}
            row["names_expected"] = names
        elif names is not None:
            row["names_expected"] = names
        if slots is not None or (stage in ("List", "After") and guard):
            row["slots_expected"] = 3 if slots is None else slots
        if evidence_case is not None:
            row["evidence_case"] = evidence_case
        if note:
            row["note"] = note
        truth.append(row)

    for stage, name, guard, kind in NATURAL:
        image = frame(name)[1]
        ocr = identity = None
        if guard and stage == "List":
            ocr = dict(LIST_OCR)
            identity = {str(r): item for r, item in LIST_IDENTITY.items()}
        if guard and stage == "Confirm":
            ocr = dict(CONFIRM_OCR[name])
            identity = {"Confirm": CONFIRM_IDENTITY[name]}
        add(f"{stage}-{name}", stage, guard, kind, name, image, ocr=ocr, ocr_required=name in OCR_REQUIRED,
            identity=identity)
    # Missing-anchor black negatives and cross-stage rejects from each template frame.
    for stage in GUARD_STAGES:
        source_name = TEMPLATE_FRAMES[stage]
        image = frame(source_name)[1]
        for anchor, roi in ANCHORS[stage].items():
            add(f"{stage}-missing-{anchor}", stage, False, "synthetic_removed_anchor", source_name, blacked(image, roi))
        for other in GUARD_STAGES:
            if other != stage:
                add(f"{stage}-reject-{other}", stage, False, "other_stage_original", TEMPLATE_FRAMES[other],
                    frame(TEMPLATE_FRAMES[other])[1])
    # After uses the List anchors; Premium (wrong tab) and Confirm overlays must not be After.
    add("After-reject-Premium", "After", False, "other_stage_original", "005", frame("005")[1])
    add("After-reject-Confirm", "After", False, "other_stage_original", "009", frame("009")[1])
    add("After-reject-Result", "After", False, "other_stage_original", TEMPLATE_FRAMES["Result"],
        frame(TEMPLATE_FRAMES["Result"])[1])
    result = frame(TEMPLATE_FRAMES["Result"])[1]
    add("Result-live-confirm1", "Result", True, "source_self_match", TEMPLATE_FRAMES["Result"], result,
        ocr={"Result.money": "17678482", "Result.items": "610"}, ocr_required=True,
        names={"ResultSentence": "スキップチケットと交換しました"}, evidence_case="result_rank1")
    # Wrong money: the item-count digits pasted over the money value.
    add("Result-synthetic-money-610", "Result", True, "synthetic_value_paste", TEMPLATE_FRAMES["Result"],
        money_replaced(result),
        ocr={"Result.money": "610", "Result.items": "610"}, ocr_required=False,
        names={"ResultSentence": "スキップチケットと交換しました"}, evidence_case="result_money_mismatch")

    # Synthetic evidence negatives on settled native frames (guard stays positive).
    listing = frame("007")[1]
    limit_roi = OCR_FIELDS["List.limit1"][0]
    runs = glyph_columns(listing, limit_roi, rows=22)
    require(len(runs) == 3, f"0/1 字形分段异常: {runs}")
    (zero_l, zero_r), (slash_l, slash_r), (one_l, one_r) = runs
    x, y, w, h = limit_roi
    done = listing.copy()
    done.paste((255, 255, 255), (zero_l - 1, y, zero_r + 1, y + 22))
    # Mirror the native "/1" spacing so the synthetic "1/1" keeps the game's glyph rhythm.
    new_right = slash_l - (one_l - slash_r)
    done.paste(listing.crop((one_l, y, one_r, y + 22)), (new_right - (one_r - one_l), y))
    ocr = dict(LIST_OCR, **{"List.limit1": "1/1"})
    # Measured: Maa OCR scores this pasted "1/1" 0.948 (<0.95), so it is refused, never misread.
    add("List-synthetic-limit1-reached", "List", True, "synthetic_digit_paste", "007", done, ocr=ocr,
        ocr_required=False, identity={str(r): i for r, i in LIST_IDENTITY.items()}, evidence_case="already_complete_rank1",
        note="把同帧 0/1 的“1”字形贴到“0”的位置，模拟当日已交换；仍需实机 1/1 样本。")
    price_roi = OCR_FIELDS["List.price1"][0]
    tampered = pasted(listing, listing, OCR_FIELDS["List.price3"][0], at=price_roi[:2])
    add("List-synthetic-price1-5000", "List", True, "synthetic_price_paste", "007", tampered,
        ocr=dict(LIST_OCR, **{"List.price1": "5,000"}), ocr_required=True,
        identity={str(r): i for r, i in LIST_IDENTITY.items()}, evidence_case="price_over_max_rank1",
        note="把第3张卡的 5,000 价格区域贴到第1张卡。")
    after_like = done
    add("After-synthetic-limit-without-spend", "After", True, "synthetic_digit_paste", "007", after_like,
        ocr=ocr, ocr_required=False, identity={str(r): i for r, i in LIST_IDENTITY.items()},
        evidence_case="after_money_unchanged_rank1", note="交换次数+1但マニー未减少，必须保持未知。")
    dialog, towel = frame("009")[1], frame("016")[1]
    add("Confirm-synthetic-required-5000", "Confirm", True, "synthetic_value_paste", "009",
        pasted(dialog, towel, OCR_FIELDS["Confirm.required"][0]),
        ocr=dict(CONFIRM_OCR["009"], **{"Confirm.required": "5,000"}), ocr_required=True,
        identity={"Confirm": "skip_ticket"}, evidence_case="required_mismatch_rank1")
    add("Confirm-synthetic-money-after", "Confirm", True, "synthetic_value_paste", "009",
        pasted(dialog, towel, OCR_FIELDS["Confirm.money_after"][0]),
        ocr=dict(CONFIRM_OCR["009"], **{"Confirm.money_after": "17,673,512"}), ocr_required=True,
        identity={"Confirm": "skip_ticket"}, evidence_case="money_after_mismatch_rank1")
    qty_roi = OCR_FIELDS["Confirm.quantity"][0]
    four = glyph_columns(towel, OCR_FIELDS["Confirm.item_before"][0], rows=22)
    one = glyph_columns(dialog, qty_roi, rows=26)
    require(len(four) == 1 and len(one) == 1, f"数量字形分段异常: {four} {one}")
    qx, qy, qw, qh = qty_roi
    changed = dialog.copy()
    changed.paste(changed.crop((qx, qy, qx + 4, qy + qh)).resize((one[0][1] - one[0][0], qh)), (one[0][0], qy))
    iy = OCR_FIELDS["Confirm.item_before"][0][1]
    changed.paste(towel.crop((four[0][0], iy, four[0][1], iy + qh)), (one[0][0] - 2, qy))
    # Template matching at 0.95 does not resolve a single digit; quantity is proved by OCR + rule.
    add("Confirm-synthetic-quantity-4", "Confirm", True, "synthetic_digit_paste", "009", changed,
        ocr=dict(CONFIRM_OCR["009"], **{"Confirm.quantity": "4"}), ocr_required=True,
        identity={"Confirm": "skip_ticket"}, evidence_case="quantity_not_one_rank1",
        note="交換数改为4：模板守卫不区分单个数字，必须由OCR与规则拒绝。")

    # Live 2026-10-02: after buying ranks 1 and 2 those cards vanished; the towel (rank 3) is at position 1,
    # followed by monthly items (position 3 has a grey, disabled 5,000 button).
    bought_name = "../shop-after-two-bought-20261002"
    bought = frame(bought_name)[1]
    add("List-after-two-bought", "List", True, "native_after_purchase", bought_name, bought,
        ocr={"List.money": "17,678,492", "List.limit1": "0/1", "List.price1": "5,000"}, ocr_required=True,
        identity={"1": "illumination_stars_towel", "2": "gold_training_ticket", "3": "gold_training_ticket"},
        names={"1": NAMES["illumination_stars_towel"], "2": NAMES["gold_training_ticket"], "3": NAMES["gold_training_ticket"]},
        slots=1, evidence_case="banner_count_vs_ledger")
    grey = pasted(listing, bought, button_roi(3), at=button_roi(2)[:2])
    add("List-synthetic-grey-button2", "List", False, "synthetic_disabled_button", "007", grey,
        note="把灰色(不可兑换)按钮底色贴到第2张本日限定卡：横幅在但按钮不可用，布局不一致，List守卫拒绝。")

    # Rank mode: the dialog shows a different name than the opened card -> must stop.
    gold, = [frame("013")[1]]
    add("Confirm-synthetic-name-gold-on-skip", "Confirm", True, "synthetic_name_paste", "009",
        pasted(dialog, gold, CONFIRM_NAME_ROI), ocr=dict(CONFIRM_OCR["009"]), ocr_required=True,
        identity={"Confirm": "gold_training_ticket"}, evidence_case="dialog_name_differs_rank1",
        note="把013弹窗的商品名贴到009（スキップ）弹窗。")
    add("Confirm-synthetic-name-towel-on-gold", "Confirm", True, "synthetic_name_paste", "013",
        pasted(gold, towel, CONFIRM_NAME_ROI), ocr=dict(CONFIRM_OCR["013"]), ocr_required=True,
        identity={"Confirm": "illumination_stars_towel"}, evidence_case="dialog_name_differs_rank2",
        note="把016弹窗的商品名贴到013（ゴールド）弹窗。")
    nx, ny, nw, nh = CONFIRM_NAME_ROI
    blank = dialog.copy()
    blank.paste((255, 255, 255), (nx, ny, nx + nw, ny + nh))
    add("Confirm-synthetic-name-blank", "Confirm", True, "synthetic_name_removed", "009", blank,
        ocr=dict(CONFIRM_OCR["009"]), ocr_required=True, identity={"Confirm": None},
        names={"Confirm": None, "CurrencyPrompt": PROMPT}, evidence_case="dialog_name_unreadable_rank1")

    (output / "manifest.json").write_text(json.dumps(
        {"version": 1, "canvas": list(CANVAS), "scenes": scenes, "samples": samples},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "sources.json").write_text(json.dumps(
        {"version": 1, "templates": templates, "samples": truth,
         "limitations": ("本档案由tools/build_shop_profile.py从2026-10-02用户手动操作的本机原生帧离线生成。"
                         "无交换后结果弹窗、真实1/1、售罄与マニー不足样本；合成负例仅证明拒绝路径。")},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "scenes": len(scenes), "samples": len(samples)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线构建每日商店识别档案；不连接设备。")
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
