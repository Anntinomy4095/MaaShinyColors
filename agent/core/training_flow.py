"""固定单铜券训练合同；不连接设备，也不授予实时输入权限。"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "SC.Training."
CARD_ID = "【くっつく、くっつかない】芹沢あさひ"
STAGES = ("Zero", "One", "Confirm", "Result", "After")
ACTION_POINTS = {"Zero": (1085, 347), "One": (1173, 663),
                 "Confirm": (740, 510), "Result": (640, 486)}
FIXTURE = ROOT / "tests/fixtures/training_flow/pipeline.json"
# Identity crops exclude level and EXP. Popup identity uses its own dimmed crop.
ANCHORS = {
    "Zero": {"Title": [48, 24, 177, 31], "CardName": [159, 96, 451, 31],
             "Portrait": [40, 102, 99, 75], "BronzeLabel": [810, 270, 325, 34],
             "BronzeZero": [939, 322, 62, 44], "Lv50Zero": [939, 184, 62, 44],
             "SilverZero": [939, 456, 62, 44], "ClearDisabled": [906, 636, 146, 50],
             "Plus": [1058, 320, 58, 52]},
    "One": {"Title": [48, 24, 177, 31], "CardName": [159, 96, 451, 31],
            "Portrait": [40, 102, 99, 75], "BronzeLabel": [810, 270, 325, 34],
            "BronzeOne": [939, 322, 62, 44], "SilverZero": [939, 456, 62, 44],
            "Exp50": [164, 175, 147, 28], "Submit": [1097, 635, 153, 57]},
    "Confirm": {"Title": [406, 165, 230, 35], "CardName": [159, 96, 451, 31],
                "Prompt": [453, 245, 369, 57], "OnlyBronzeOne": [366, 219, 548, 220],
                "BronzeTicket": [592, 327, 105, 83], "QuantityOne": [672, 412, 24, 18],
                "Start": [660, 475, 159, 68]},
    "Result": {"Title": [408, 190, 260, 35], "CardNamePrefix": [159, 96, 271, 31],
               "Portrait": [40, 102, 99, 75], "StatsLabels": [374, 328, 40, 88],
               "Close": [563, 451, 154, 65]},
}
ANCHORS["After"] = dict(ANCHORS["Zero"])


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _expected_pipeline():
    pipeline = {}
    for stage in STAGES:
        base = PREFIX + stage + "."
        names = [base + anchor for anchor in ANCHORS[stage]]
        for anchor, roi in ANCHORS[stage].items():
            pipeline[base + anchor] = {"recognition": "TemplateMatch", "template": stage + "-" + anchor + ".png",
                "roi": roi, "threshold": 0.95, "method": 5, "action": "DoNothing", "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Guard"] = {"recognition": "And", "all_of": names,
            "action": "DoNothing", "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Observe"] = {"recognition": "DirectHit", "action": "DoNothing",
            "next": [base + "Guard", base + "Unknown"], "on_error": [PREFIX + "Error"],
            "timeout": 1000, "rate_limit": 0, "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Unknown"] = {"recognition": "Or", "any_of": [base + "Guard"],
            "inverse": True, "action": "StopTask", "pre_delay": 0, "post_delay": 0}
        if stage in ACTION_POINTS:
            x, y = ACTION_POINTS[stage]
            pipeline[base + "Click"] = {"enabled": False, "recognition": "And", "all_of": names,
                "action": "Click", "target": [x, y, 1, 1], "on_error": [PREFIX + "Error"],
                "pre_delay": 0, "post_delay": 0}
    pipeline[PREFIX + "Error"] = {"recognition": "DirectHit", "action": "StopTask", "pre_delay": 0, "post_delay": 0}
    return pipeline


def fixed_pipeline(execute=False):
    """Validate immutable on-disk UI flow; execute is for isolated simulation only.

    Each action ends its task. The caller must capture and verify its result before
    starting a distinct next-stage task; no edge can repeat a consuming action.
    """
    _require(type(execute) is bool, "执行开关必须为布尔值")
    pipeline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _require(pipeline == _expected_pipeline(), "训练Pipeline固定节点、全锚点或默认禁用合同变化")
    if execute:
        for stage in ACTION_POINTS:
            base = PREFIX + stage + "."
            pipeline[base + "Click"]["enabled"] = True
            pipeline[base + "Observe"]["next"][0] = base + "Click"
    return pipeline


def stage_matches(stage, recognitions):
    """Only complete, unique, fixed-ROI matches constitute a stage proof."""
    if stage not in ANCHORS or type(recognitions) is not list:
        return False
    expected = {PREFIX + stage + "." + name: roi for name, roi in ANCHORS[stage].items()}
    by_name = {r.get("scene"): r for r in recognitions if type(r) is dict}
    if len(recognitions) != len(expected) or set(by_name) != set(expected):
        return False
    for name, roi in expected.items():
        item = by_name[name]
        score = item.get("score")
        matches = item.get("raw_detail", {}).get("filtered")
        if (item.get("hit") is not True or type(score) not in (int, float)
                or not math.isfinite(score) or not 0.95 <= score <= 1
                or item.get("roi") != roi or type(matches) is not list or len(matches) != 1
                or matches[0].get("box") != roi):
            return False
    return True


def validate_resource_evidence(stage, evidence):
    """Validate already-observed values; this is not OCR or authorization.

    Zero/One are non-consuming preview stages. Hidden material quantities are not
    inferred from EXP. Consumption requires a complete, single-material final
    cost panel, bound to the same fresh frame that proved the Confirm anchors.
    """
    _require(stage in STAGES and type(evidence) is dict, "训练阶段或资源证据无效")
    _require(evidence.get("card_id") == CARD_ID and evidence.get("card_type") == "support_s", "必须绑定指定S卡")
    _require(evidence.get("ticket_type") == "basic_bronze", "仅允许基础铜券")
    for name in ("inventory", "level"):
        _require(type(evidence.get(name)) is int and evidence[name] >= 0, "缺少整数库存或等级证据")
    _require(evidence['level'] > 0, "卡片等级未知")
    if 'max_level' in evidence:
        _require(type(evidence['max_level']) is int and evidence['level'] < evidence['max_level'], "卡片满级或等级未知")
    else:
        # The selected card shows a numeric positive EXP remaining to the next
        # level. Read that field instead of inventing a hidden maximum level.
        _require(type(evidence.get('remaining_exp')) is int and evidence['remaining_exp'] > 0,
                 "缺少下一等级所需经验，不能证明卡片尚可训练")
    if stage in ("Zero", "One"):
        _require(evidence["inventory"] >= 1, "铜券库存不足")
        expected = 0 if stage == "Zero" else 1
        for name, quantity in (("bronze_quantity", expected), ("silver_quantity", 0)):
            _require(type(evidence.get(name)) is int and evidence[name] == quantity, "缺少铜券及银券数量证据")
        if stage == "One":
            _require(type(evidence.get("exp")) is int and evidence["exp"] == 50, "一基础铜券须与EXP50绑定")
    elif stage == "Confirm":
        items = evidence.get("confirmed_items")
        _require(type(items) is list and len(items) == 1 and type(items[0]) is dict
                 and set(items[0]) == {"ticket_type", "quantity"}
                 and items[0]["ticket_type"] == "basic_bronze" and type(items[0]["quantity"]) is int
                 and items[0]["quantity"] == 1, "最终弹窗必须仅含一基础铜券")
        proof = evidence.get("final_cost_proof")
        _require(type(proof) is dict and set(proof) == {"kind", "frame_sha256", "sole_material", "quantity", "complete_panel", "guard_verified"},
                 "缺少完整最终材料区域识别证据，禁止消耗")
        stamp = proof.get("frame_sha256")
        _require(type(stamp) is str and len(stamp) == 64 and all(c in "0123456789abcdef" for c in stamp)
                 and stamp == evidence.get("frame_sha256"), "最终材料区域必须绑定本次新截图")
        _require(proof["kind"] == "single_bronze_cost_panel" and proof["sole_material"] == "basic_bronze"
                 and type(proof["quantity"]) is int and proof["quantity"] == 1
                 and proof["complete_panel"] is True and proof["guard_verified"] is True,
                 "最终材料区域不证明唯一基础铜券一张")
    elif stage == "After":
        _require(type(evidence.get("before_inventory")) is int and evidence["before_inventory"] - evidence["inventory"] == 1,
                 "训练后库存须净减一张基础铜券")
        before_level=evidence.get('before_level')
        increased=type(before_level) is int and evidence['level'] > before_level
        remaining=evidence.get('remaining_exp'); before_remaining=evidence.get('before_remaining_exp')
        gained_exp=(type(before_level) is int and evidence['level']==before_level
                    and type(remaining) is int and type(before_remaining) is int
                    and 0 < remaining < before_remaining)
        _require(increased or gained_exp, "缺少等级或经验增长证据")
        _require(type(evidence.get("bronze_quantity")) is int and evidence["bronze_quantity"] == 0, "训练后选择数量必须归零")
    return dict(evidence)


def load_guard(manifest, sources):
    """Bind every fixed ROI, template and replay sample to private file hashes."""
    from tools.replay_screenshots import template_path, validate_manifest
    manifest, sources = Path(manifest).resolve(), Path(sources).resolve()
    private = (ROOT / "dev_assets/private").resolve()
    _require(manifest.is_relative_to(private) and sources.is_relative_to(private), "训练守卫及来源只允许private目录")
    data = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")), manifest.parent)
    origin = json.loads(sources.read_text(encoding="utf-8"))
    expected = {PREFIX + stage + "." + anchor: roi for stage in STAGES for anchor, roi in ANCHORS[stage].items()}
    _require(len(data["scenes"]) == len(expected) and {s["name"] for s in data["scenes"]} == set(expected), "训练全阶段锚点必须完整")
    templates = {t["template"]: t for t in origin["templates"]}
    _require(len(templates) == len(expected), "训练模板来源必须完整且唯一")
    def check_origin(item):
        raw = item.get("source")
        _require(type(raw) is str, "缺少训练原始截图路径")
        path = Path(raw)
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        _require(path.is_relative_to(private) and path.is_file(), "训练原始截图必须存在private目录")
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == item.get("source_sha256"), "训练原始截图哈希不符")
    for scene in data["scenes"]:
        _require(scene["recognition"] == "TemplateMatch" and scene["roi"] == expected[scene["name"]]
                 and scene["threshold"] == 0.95, "禁止放宽训练ROI或阈值")
        filename = scene["name"].removeprefix(PREFIX).replace(".", "-") + ".png"
        _require(scene["template"] == filename and filename in templates, "训练模板名称或来源变化")
        _require(hashlib.sha256(template_path(scene, manifest.parent).read_bytes()).hexdigest() == templates[filename]["template_sha256"], "训练模板来源哈希不符")
        check_origin(templates[filename])
    truth = {s["derived"]: s for s in origin["samples"]}
    _require(len(truth) == len(data["samples"]) and set(truth) == {s["image"] for s in data["samples"]}, "训练样本来源不完整")
    for sample in data["samples"]:
        item = truth[sample["image"]]
        _require(item["stage"] in STAGES and type(item["expected_guard"]) is bool, "阶段真值无效")
        _require(hashlib.sha256((manifest.parent / sample["image"]).read_bytes()).hexdigest() == item["derived_sha256"], "训练样本发生变化")
        check_origin(item)
    for stage in STAGES:
        stage_truth = [s for s in truth.values() if s["stage"] == stage]
        _require(any(s["expected_guard"] for s in stage_truth) and any(not s["expected_guard"] for s in stage_truth), "每个训练阶段必须有正负样本")
    digest = hashlib.sha256(manifest.read_bytes() + sources.read_bytes()).hexdigest()
    return data, truth, digest


class TrainingFlow:
    """One-run ordered attempt ledger; failed/lost responses permanently stop it."""
    def __init__(self):
        self.stage = "Zero"
        self.attempts = {}
        self.stopped = False
        self.resource_result_verified = False
        self.daily_mission_verified = False

    def act_once(self, stage, point, fresh_guard, send, *, execute=False):
        if (execute is not True or self.stopped or stage != self.stage or
                stage not in ACTION_POINTS or point != ACTION_POINTS[stage] or stage in self.attempts):
            return False
        try:
            binding = fresh_guard()
            self.attempts[stage] = "pending"
            response = send(binding)
            if type(response) is not dict or response.get("ok") is not True or response.get("attempted") is not True:
                self.stopped = True
                return False
            self.attempts[stage] = "awaiting_postcondition"
            return True
        except Exception:
            self.stopped = True
            return False

    def verify_next(self, stage, recognitions, evidence):
        if self.stopped or self.attempts.get(self.stage) != "awaiting_postcondition":
            return False
        expected = STAGES[STAGES.index(self.stage) + 1]
        try:
            _require(stage == expected and stage_matches(stage, recognitions), "训练后置阶段守卫失败")
            validate_resource_evidence(stage, evidence)
            self.attempts[self.stage] = "verified"
            self.stage = stage
            self.resource_result_verified = stage == "After"
            return True
        except Exception:
            self.stopped = True
            return False
