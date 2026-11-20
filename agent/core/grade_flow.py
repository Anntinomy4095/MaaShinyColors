"""每日グレードフェス（Grade Fes）合同：纯函数，不连接设备，也不授予实时输入权限。

几何来自本机原生 1280x720 样本（2026-10-02 manual-capture-kept-20261002/shop-grade，用户手动操作）。
阶段：Home →（home-fes，由导航运行器负责）→ FesMenu → FesMain → ModeSelect → Difficulty →
AfterStart（未知，无样本）。只有三个 ``grade-difficulty-<id>`` 可能消耗フェス参加権；
「まとめて消費」复选框永远不是目标（本版本要求未勾选，提示必须为「フェス参加権を 1 消費します」）。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "SC.Grade."
STAGES = ("Home", "FesMenu", "FesMain", "FesHelp", "ModeSelect", "Difficulty", "AfterStart")
GUARD_STAGES = ("Home", "FesMenu", "FesMain", "FesHelp", "ModeSelect", "Difficulty")
FIXTURE = ROOT / "tests/fixtures/grade_flow/pipeline.json"
PRESENCE_SCORE = 0.80  # Exclusion only; positive anchors keep 0.95.
THRESHOLD = 0.95

# Anchor groups (fixed ROI = template size). A stage is the conjunction of its groups.
ANCHORS = {
    "Home": {"ShopLabel": [35, 276, 56, 17], "MenuLabel": [33, 348, 60, 24], "PresentLabel": [30, 193, 66, 18]},
    # The フェス popup: 「マッチライブ」 and 「グレード&リハ」 (the 21801 unit score above it is excluded).
    "FesMenu": {"MatchLabel": [985, 525, 120, 32], "GradeLabel": [1128, 547, 126, 26]},
    "FesMain": {"Title": [6, 16, 150, 40], "Back": [20, 628, 74, 66], "UnitButton": [905, 628, 160, 62],
                "JoinLabel": [1130, 595, 105, 50], "ScoreLabel": [30, 288, 285, 32]},
    # First-visit 「フェスについて」 (18 pages; frames 033/034). The page counter changes and is not an anchor.
    "FesHelp": {"Title": [190, 82, 225, 40], "Icon": [68, 60, 100, 74], "Close": [558, 562, 167, 78]},
    "ModeSelect": {"RehearsalButton": [912, 378, 150, 150], "GradeButton": [1092, 378, 150, 150]},
    "Difficulty": {"Title": [232, 88, 815, 46], "Easy": [255, 262, 155, 68], "Normal": [538, 270, 195, 60],
                   "Hard": [858, 265, 170, 65], "ScoreEasy": [280, 420, 110, 65], "ScoreNormal": [585, 420, 110, 65],
                   "ScoreHard": [885, 420, 115, 65], "Prompt": [815, 568, 450, 52], "CheckboxOff": [866, 644, 46, 46],
                   "BulkLabel": [918, 644, 160, 46], "Back": [20, 628, 74, 66]},
}
# Template matching (TM_CCOEFF_NORMED) is invariant to a uniform dimming overlay, so a dimmed Fes main
# still matches its own templates (measured 1.000 under the difficulty dialog). These colour checks
# require the undimmed white header (FesMain/ModeSelect) and the white cost bubble (Difficulty):
# measured 6000/6000 vs 0 when dimmed, and 3434/3800 vs ≤83 without the dialog.
COLOR_CHECKS = {"FesMain": {"Undimmed": {"roi": [150, 22, 200, 30], "lower": [235, 235, 235], "upper": [255, 255, 255],
                                         "count": 5800}},
                "Difficulty": {"BubbleBright": {"roi": [840, 612, 380, 10], "lower": [235, 235, 235],
                                                "upper": [255, 255, 255], "count": 3200}}}
STAGE_GROUPS = {"Home": ("Home",), "FesMenu": ("Home", "FesMenu"), "FesMain": ("FesMain",), "FesHelp": ("FesHelp",),
                "ModeSelect": ("FesMain", "ModeSelect"), "Difficulty": ("Difficulty",)}
# Distinctive anchors of each group; a stage refuses any other group's distinctive anchor.
DISTINCTIVE = {"Home": ("ShopLabel", "PresentLabel"), "FesMenu": ("MatchLabel", "GradeLabel"),
               "FesMain": ("UnitButton", "JoinLabel"), "FesHelp": ("Title", "Icon"), "ModeSelect": ("RehearsalButton", "GradeButton"),
               "Difficulty": ("Title", "BulkLabel")}
# The difficulty dialog dims the mode-select screen; the dimmed グレードフェス button stays visible.
UNDERLYING = {"Difficulty": ("ModeSelect",), "FesHelp": ("FesMain",)}
# Now Loading and the rehearsal difficulty title (rehearsal also consumes the shared rights).
EXCLUSIONS = {"Loading": [837, 662, 294, 47], "RehearsalTitle": [232, 88, 815, 46]}

DIFFICULTIES = ("Easy", "Normal", "Hard")
DIFFICULTY_POINTS = {"Easy": (332, 360), "Normal": (638, 360), "Hard": (942, 360)}
# action -> (stages it may be sent from, fixed point)
ACTIONS = {"fes-grade-entry": (("FesMenu",), (1188, 541)),
           "fes-help-close": (("FesHelp",), (640, 600)),
           "grade-join-open": (("FesMain",), (1183, 620)),
           "grade-mode-grade": (("ModeSelect",), (1167, 453)),
           "grade-back": (("FesMain", "ModeSelect", "Difficulty"), (57, 662)),
           **{f"grade-difficulty-{d.lower()}": (("Difficulty",), DIFFICULTY_POINTS[d]) for d in DIFFICULTIES}}
CONSUMING = tuple(f"grade-difficulty-{d.lower()}" for d in DIFFICULTIES)
REPEATABLE = ("fes-grade-entry", "fes-help-close", "grade-join-open", "grade-mode-grade", "grade-back")
# (action, from stage) -> verified post stages. Observed: 036→037/038→039 (back→Home),
# 069→070/071→072 (back→Home), 063→064 (rehearsal difficulty back → mode select).
POST_STAGES = {("fes-grade-entry", "FesMenu"): ("FesMain", "FesHelp"), ("fes-help-close", "FesHelp"): ("FesMain",),
               ("grade-join-open", "FesMain"): ("ModeSelect",),
               ("grade-mode-grade", "ModeSelect"): ("Difficulty",), ("grade-back", "FesMain"): ("Home",),
               ("grade-back", "ModeSelect"): ("Home",), ("grade-back", "Difficulty"): ("ModeSelect",),
               **{(a, "Difficulty"): ("AfterStart",) for a in CONSUMING}}
POST_OBSERVE_SECONDS = 30

# Rights counter on the 「フェスに参加する」 button (FesMain/ModeSelect/Difficulty): white "6/20" on the bar.
RIGHTS_STAGES = ("FesMain", "ModeSelect", "Difficulty")
OCR_FIELDS = {"rights": ([1145, 665, 42, 16], "light", r"^[0-9]{1,2}/[0-9]{1,2}$")}
# Japanese text boxes (detector + recogniser, every line ≥0.95, NFKC, whitespace removed).
TEXT_BOXES = {"Title": [232, 88, 815, 46], "Prompt": [830, 572, 420, 44]}
EXPECTED_TITLE = "グレードフェスの難易度を選択してください"
EXPECTED_PROMPT = "フェス参加権を1消費します"
RUNNER = "SC.DailyGrade"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def scene_name(group, anchor):
    return PREFIX + group + "." + anchor


def exclusion_scene(name):
    _require(name in EXCLUSIONS, "排除场景无效")
    return PREFIX + "Exclude." + name


def guard_anchors(stage):
    """{scene name: roi} of every template anchor the stage requires."""
    _require(stage in GUARD_STAGES, "Grade 守卫阶段无效")
    return {scene_name(g, a): roi for g in STAGE_GROUPS[stage] for a, roi in ANCHORS[g].items()}


def color_checks(stage):
    """{scene name: colour rule} the stage requires (undimmed proof)."""
    _require(stage in GUARD_STAGES, "Grade 守卫阶段无效")
    return {scene_name(g, n): rule for g in STAGE_GROUPS[stage] for n, rule in COLOR_CHECKS.get(g, {}).items()}


def anchor_names(stage):
    return list(guard_anchors(stage)) + list(color_checks(stage))


def action_stages(action):
    _require(action in ACTIONS, "未知 Grade 动作")
    return ACTIONS[action][0]


def node_name(action, stage):
    _require(stage in action_stages(action), "动作不属于该阶段")
    return PREFIX + "Action." + action + "." + stage


def difficulty_of(action):
    _require(action in CONSUMING, "不是难度动作")
    return {d.lower(): d for d in DIFFICULTIES}[action.rsplit("-", 1)[1]]


# ---------------------------------------------------------------- Pipeline

def _expected_pipeline():
    pipeline = {}
    for group, anchors in ANCHORS.items():
        for anchor, roi in anchors.items():
            pipeline[scene_name(group, anchor)] = {"recognition": "TemplateMatch", "template": group + "-" + anchor + ".png",
                "roi": roi, "threshold": THRESHOLD, "method": 5, "action": "DoNothing", "pre_delay": 0, "post_delay": 0}
    for group, checks in COLOR_CHECKS.items():
        for name, rule in checks.items():
            pipeline[scene_name(group, name)] = {"recognition": "ColorMatch", "roi": rule["roi"], "lower": rule["lower"],
                "upper": rule["upper"], "count": rule["count"], "method": 4, "connected": False, "action": "DoNothing",
                "pre_delay": 0, "post_delay": 0}
    for stage in GUARD_STAGES:
        base = PREFIX + stage + "."
        pipeline[base + "Guard"] = {"recognition": "And", "all_of": anchor_names(stage), "action": "DoNothing",
                                    "pre_delay": 0, "post_delay": 0}
        # Animated pages (button shine/pulse) fail single frames: Maa re-captures every 0.5 s
        # until the full guard matches, and stops via Error if it never does within 8 s.
        pipeline[base + "Observe"] = {"recognition": "DirectHit", "action": "DoNothing",
            "next": [base + "Guard"], "on_error": [PREFIX + "Error"],
            "timeout": 8000, "rate_limit": 500, "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Unknown"] = {"recognition": "Or", "any_of": [base + "Guard"], "inverse": True,
                                      "action": "StopTask", "pre_delay": 0, "post_delay": 0}
    for action, (stages, (x, y)) in ACTIONS.items():
        for stage in stages:
            pipeline[node_name(action, stage)] = {"enabled": False, "recognition": "And", "all_of": anchor_names(stage),
                "action": "Click", "target": [x, y, 1, 1], "on_error": [PREFIX + "Error"], "pre_delay": 0, "post_delay": 0}
    pipeline[PREFIX + "Error"] = {"recognition": "DirectHit", "action": "StopTask", "pre_delay": 0, "post_delay": 0}
    return pipeline


def fixed_pipeline(action=None, stage=None):
    """Validate the immutable on-disk flow; ``action``@``stage`` enables exactly one fixed Click.

    Each Click ends the task; the caller re-captures and classifies before anything else.
    No edge repeats a consuming action.
    """
    pipeline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _require(pipeline == _expected_pipeline(), "Grade Pipeline固定节点、锚点或默认禁用合同变化")
    if action is not None:
        name = node_name(action, stage)
        pipeline[name]["enabled"] = True
        pipeline[PREFIX + stage + ".Observe"]["next"][0] = name
    return pipeline


# ---------------------------------------------------------------- recognition decisions

def _score(item):
    score = item.get("score") if type(item) is dict else None
    return score if type(score) in (int, float) and math.isfinite(score) else None


def exact_match(item, roi):
    score = _score(item)
    matches = item.get("raw_detail", {}).get("filtered") if type(item) is dict else None
    return (item.get("hit") is True and score is not None and THRESHOLD <= score <= 1
            and item.get("roi") == roi and type(matches) is list and len(matches) == 1
            and type(matches[0]) is dict and matches[0].get("box") == roi)


def present(item):
    score = _score(item)
    return type(item) is dict and (item.get("hit") is True or score is not None and score >= PRESENCE_SCORE)


def stage_matches(stage, recognitions):
    """Every fixed anchor of the stage matches uniquely at its exact ROI (≥0.95)."""
    if stage not in GUARD_STAGES or type(recognitions) is not list:
        return False
    expected = guard_anchors(stage)
    rows = [r for r in recognitions if type(r) is dict and r.get("scene") in expected]
    by_name = {r["scene"]: r for r in rows}
    if len(by_name) != len(expected) or len(rows) != len(expected):
        return False
    if not all(exact_match(by_name[name], roi) for name, roi in expected.items()):
        return False
    every = {r.get("scene"): r for r in recognitions if type(r) is dict}
    return all(color_ok(every.get(name), rule) for name, rule in color_checks(stage).items())


def color_ok(item, rule):
    return (type(item) is dict and item.get("hit") is True and item.get("roi") == rule["roi"]
            and type(item.get("matched_pixels")) is int and item["matched_pixels"] >= rule["count"])


def exclusions_clear(stage, recognitions):
    """Refuse loading, tutorial, rehearsal title and any other group's distinctive anchors."""
    if stage not in GUARD_STAGES or type(recognitions) is not list:
        return False
    by_name = {r.get("scene"): r for r in recognitions if type(r) is dict}
    for name in EXCLUSIONS:
        item = by_name.get(exclusion_scene(name))
        if item is None or present(item):
            return False
    allowed = set(STAGE_GROUPS[stage]) | set(UNDERLYING.get(stage, ()))
    for group, anchors in DISTINCTIVE.items():
        if group in allowed:
            continue
        for anchor in anchors:
            item = by_name.get(scene_name(group, anchor))
            if item is None or present(item):
                return False
    return True


# ---------------------------------------------------------------- evidence and rules

def normalize_text(text):
    import unicodedata
    _require(isinstance(text, str), "文本必须为字符串")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def parse_rights(text):
    """``current/max`` from the rights bar; current may not exceed max (the +N extra is separate)."""
    _require(isinstance(text, str), "参加权OCR缺失")
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", text)
    _require(match is not None, "参加权格式无效")
    current, maximum = int(match.group(1)), int(match.group(2))
    _require(maximum > 0 and 0 <= current <= maximum, "参加权数值无效")
    return {"current": current, "max": maximum}


def grade_config(config):
    """The user's explicit grade_fes block. difficulty_id must be Easy/Normal/Hard; bulk never used."""
    from agent.core.daily_config import DailyConfig
    data = DailyConfig.from_dict(config.data if isinstance(config, DailyConfig) else config).data
    _require(data["premium_currency_allowed"] is False, "付费货币始终禁止")
    grade = data["grade_fes"]
    _require(grade["enabled"] is True, "Grade Fes 未启用")
    _require(grade["difficulty_id"] in DIFFICULTIES, "difficulty_id 必须为 Easy、Normal 或 Hard（区分大小写）")
    _require(type(grade["count"]) is int and grade["count"] >= 1, "count 必须为正整数")
    budget = min(grade["participation_budget"], data["budgets"]["fes_participation_rights"])
    _require(budget >= 1, "参加权预算为0（grade_fes.participation_budget 或 budgets.fes_participation_rights）")
    return {"difficulty_id": grade["difficulty_id"], "count": grade["count"], "participation_budget": budget}


def difficulty_evidence(texts, names):
    """Numeric rights plus the OCR'd title and cost prompt of the Grade Fes difficulty dialog."""
    _require(type(texts) is dict and type(names) is dict, "难度页OCR证据无效")
    rights = parse_rights(texts.get("rights"))
    title, prompt = names.get("Title"), names.get("Prompt")
    return {"rights": rights, "title": normalize_text(title) if isinstance(title, str) else None,
            "prompt": normalize_text(prompt) if isinstance(prompt, str) else None}


def validate_difficulty_tap(config, action, evidence, ledger_day):
    """Before tapping a difficulty circle (may consume one フェス参加権).

    ``ledger_day``: {"attempts": int, "rights_used": int, "unresolved": list} from :class:`GradeLedger`.
    """
    _require(action in CONSUMING, "不是难度动作")
    rule = grade_config(config)
    _require(difficulty_of(action) == rule["difficulty_id"], "难度动作与配置的 difficulty_id 不一致")
    _require(type(evidence) is dict, "难度页证据缺失")
    _require(evidence.get("title") == EXPECTED_TITLE, "难度页标题OCR不是「グレードフェスの難易度を選択してください」，停止")
    _require(evidence.get("prompt") == EXPECTED_PROMPT, "消耗提示OCR不是「フェス参加権を 1 消費します」（批量或未知），停止")
    rights = evidence.get("rights")
    _require(type(rights) is dict and type(rights.get("current")) is int, "参加权OCR缺失")
    _require(rights["current"] >= 1, "フェス参加権不足（<1），停止；不使用恢复道具")
    _require(type(ledger_day) is dict and ledger_day.get("unresolved") == [], "存在未解决的Grade pending；先人工核对")
    attempts, used = ledger_day.get("attempts"), ledger_day.get("rights_used")
    _require(type(attempts) is int and type(used) is int and attempts >= 0 and used >= 0, "当日账本无效")
    _require(attempts < rule["count"], "已达当日 Grade 次数上限（count）")
    _require(used + 1 <= rule["participation_budget"], "超出当日参加权预算（含已发送）")
    return {"action": action, "difficulty_id": rule["difficulty_id"], "rights_before": rights,
            "cost": 1, "bulk": False, "attempt_index": attempts + 1, "rights_used_before": used,
            "participation_budget": rule["participation_budget"], "count": rule["count"]}


# ---------------------------------------------------------------- game day and ledger

def game_day(now=None):
    """JST (UTC+9) calendar date with the 05:00 rollover (RepeatableNavigationBudget.GameDayOf)."""
    now = _dt.datetime.now(_dt.timezone.utc) if now is None else now
    _require(isinstance(now, _dt.datetime) and now.tzinfo is not None, "时间必须带时区")
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=9 - 5)).strftime("%Y-%m-%d")


def _sha(blob):
    return hashlib.sha256(blob).hexdigest()


class GradeLedger:
    """Per-game-day attempt ledger under dev_assets/private/daily_grade/<day>/.

    ``attempt-<n>.pending.json`` is created exclusively before the difficulty tap and never deleted.
    The post-tap state has no sample yet, so this version never writes a resolution: every pending
    stays unresolved and blocks all further Grade attempts until a reviewed resolution exists.
    """
    def __init__(self, root=None):
        private = (ROOT / "dev_assets/private").resolve()
        self.root = Path(root or private / "daily_grade").resolve()
        _require(self.root.is_relative_to(private), "Grade账本必须位于private")

    def _day(self, day):
        _require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") is not None, "账本游戏日无效")
        return self.root / day

    def pending_path(self, day, index):
        _require(type(index) is int and 1 <= index <= 99, "尝试序号无效")
        return self._day(day) / f"attempt-{index}.pending.json"

    def resolved_path(self, day, index):
        return self.pending_path(day, index).with_name(f"attempt-{index}.resolved.json")

    def _load(self, path):
        data = json.loads(path.read_bytes().decode("utf-8"))
        _require(type(data) is dict and data.get("version") == 1 and data.get("runner") == RUNNER, "Grade账本文件损坏")
        return data

    def attempts(self, day):
        folder = self._day(day)
        return sorted(folder.glob("attempt-*.pending.json")) if folder.exists() else []

    def unresolved(self):
        rows = []
        if self.root.exists():
            for path in sorted(self.root.glob("*/attempt-*.pending.json")):
                index = int(path.name.split("-")[1].split(".")[0])
                if not self.resolved_path(path.parent.name, index).exists():
                    rows.append({"day": path.parent.name, "index": index, "path": str(path)})
        return rows

    def summary(self, day):
        paths = self.attempts(day)
        used = 0
        for path in paths:
            tap = self._load(path).get("tap", {})
            _require(tap.get("cost") == 1, "pending见证缺少消耗数")
            used += 1
        return {"attempts": len(paths), "rights_used": used, "unresolved": self.unresolved()}

    def write_pending(self, day, witness):
        _require(type(witness) is dict and witness.get("runner") == RUNNER and witness.get("version") == 1
                 and witness.get("no_retry") is True and type(witness.get("tap")) is dict
                 and witness["tap"].get("cost") == 1, "pending见证字段无效")
        index = len(self.attempts(day)) + 1
        _require(witness["tap"].get("attempt_index") == index, "pending见证的尝试序号与账本不符")
        path = self.pending_path(day, index)
        path.parent.mkdir(parents=True, exist_ok=True)
        import os
        with path.open("x", encoding="utf-8") as stream:
            json.dump(witness, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": str(path), "sha256": _sha(path.read_bytes()), "index": index}


# ---------------------------------------------------------------- navigation-profile overlay exclusion

# Navigation-profile groups that may be visible in each Grade stage (everything else is an overlay).
NAVIGATION_ALLOWED = {"Home": ("Home",), "FesMenu": ("Home", "FesMenu")}


def navigation_clear(stage, hits):
    """nav-native-v27 overlay exclusion (presence 0.80): only refuses more.

    Live 2026-10-02: 「ミッション達成」 opened over an undimmed Home and the shop's own Home anchors still
    matched. Every Grade stage refuses any navigation group except the ones it is drawn on.
    """
    from agent.core.daily_navigation import GROUPS, any_group
    _require(stage in GUARD_STAGES, "Grade 守卫阶段无效")
    allowed = NAVIGATION_ALLOWED.get(stage, ())
    return type(hits) is list and bool(hits) and not any(any_group(g, hits) for g in GROUPS if g not in allowed)


def navigation_blockers(stage, hits):
    from agent.core.daily_navigation import GROUPS, any_group
    allowed = NAVIGATION_ALLOWED.get(stage, ())
    return [g for g in GROUPS if g not in allowed and any_group(g, hits)] if type(hits) is list else None
