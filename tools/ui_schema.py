#!/usr/bin/env python3
"""UI overhaul U0: export the ProjectInterface task/option/preset metadata as one normalized JSON for the panel UI.

Read-only. Sources: ``assets/interface.json`` + its PI v2 ``import`` files (via ``tools.check_project`` and
``tools.run_tasks.load_interface``), the run-launcher constants ``SPEND_FLAGS`` / ``LIVE_TASK_CLICK_CAPS`` and the
action table ``assets/input/pipeline_actions.json`` (to bind each consuming action to its task). Nothing here
changes the interface, the launcher or any runtime authorization: the ``ui`` extension is display metadata only.

Output goes to stdout, or with ``--out`` to a file under ``dev_assets/private/ui/`` (nothing else is writable).
``--mock-bundle`` prints the ES module that the browser preview (mock mode) imports and ``--protocol-module`` the
ES module mirror of ``ui/protocol.json``, because the panel CSP (``connect-src 'none'``) forbids fetching JSON.

Also provides ``profile_to_run_config``: the UI profile (task order, enabled flags, chosen option cases) becomes the
run config that ``run_tasks.plan_tasks`` already accepts. U0.2: ``allow_spend`` is derived ONLY from the explicit
per-task spend options in ``SPEND_OPTIONS`` (an enabled task whose spend option holds the consenting case); nothing
else (preset, annotations, UI prefs) can produce it, and ``input_enabled`` or any other authorization field is never
emitted. ``spend_flags_for_profile`` returns the same decision as ``run_tasks.SPEND_FLAGS`` keys (the UI sends these
with ``run.start``).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_project import INTERFACE_PATH, load_jsonc  # noqa: E402
from tools import run_tasks  # noqa: E402  (read-only import: constants and load_interface)
from agent.core import fes_allocation  # noqa: E402  (pure rules: shared フェス参加権 split, 2026-10-07)

SCHEMA_VERSION = 1
ACTION_TABLE = ROOT / "assets" / "input" / "pipeline_actions.json"
UI_DIR = ROOT / "ui"
PRIVATE_UI_DIR = ROOT / "dev_assets" / "private" / "ui"
FIXTURE_FILES = {"schema": "schema.json", "demo": "demo-state.json"}
STATIC_FILES = {"annotations": "annotations.json"}
KNOWN_OPTION_TYPES = ("select", "switch", "input")
UNMIGRATED_MARK = "未迁移"
PIPELINE_DIR = ROOT / "assets" / "resource" / "base" / "pipeline"
PROFILE_KEYS = {"schema_version", "preset", "tasks", "task_timeout_s", "removed_tasks"}
# U0.2: the per-task spend switch, MAA style. ``key`` is either an existing
# interface option (Match: 只采样 = No means it really skips) or a UI-only switch exported into the schema with
# ``ui_only: true`` (never sent to run_tasks as an option). ``when`` is the case that consents to the task's own
# --allow-*-spend flag. Ordinary approved daily consumption defaults on; Grade Fes participation rights default off.
SPEND_OPTIONS = {
    "Shop": {"key": "商店兑换", "when": "Yes"},
    "Training": {"key": "训练使用铜券", "when": "Yes"},
    "MatchSkip": {"key": "Match只采样", "when": "No"},
    "GradeFes": {"key": "Grade使用参加权", "when": "Yes"},
    # W.I.N.G. from the UI (tools/ui_wing.py). 2026-10-12: default on; 2026-10-13: this saved switch is the consent.
    "AutoProduce": {"key": "Produce可开新局", "when": "Yes"},
    # 2026-10-29 user 「开始周常auto」: the official Auto's プロデュース開始; off = open the start dialog, read, キャンセル.
    "WeeklyProduce": {"key": "Auto开始消耗体力", "when": "Yes"},
    # 2026-11-18 user: Collabo Fes uses the Passes by default (「默认用完和match一样」).
    "CollaboFes": {"key": "Collabo使用Pass", "when": "Yes"},
}
# Options that pick a task's consuming action: Grade's mode/difficulty button; WeeklyProduce's ループ (the recovery
# dialog's プロデュース開始, weekly-auto-soda-start, instead of weekly-auto-start).
SPEND_ACTION_OPTIONS = {"GradeFes": ("Grade模式", "Grade难度"), "WeeklyProduce": ("Autoループ",),
                        "MatchSkip": ("Match推进",)}
# Tasks whose entry family differs from their consuming action's Pipeline family: the AutoProduce settings run through
# the supervised Wing entry (SC.Wing.*), whose only consuming action is プロデュース開始.
TASK_SPEND_ACTIONS = {"AutoProduce": {"spend_action": "wing-produce-start", "spend_flag": "allow_produce_spend"}}
# 2026-10-22 WeeklyProduce (official Auto): 次数 / スキップ / 核对 are interface options; the
# stamina consent is the UI-only 「Auto开始消耗体力」 (2026-11-21: fixed on, ticking the task consents). weekly-auto-skip
# stays disabled.
UI_ONLY_OPTIONS = {
    "商店兑换": {"label": "兑换", "default_case": "Yes",
                 "description": "关闭则只识别本日限定列表，不兑换。"},
    "训练使用铜券": {"label": "使用铜券", "default_case": "Yes",
                     "description": "关闭则只走到训练确认前，不消耗训练券。"},
    "Grade使用参加权": {"label": "使用 Fes 参加权", "default_case": "No",
                        "description": "默认关闭：不开始演出，不消耗参加权。"},
    # 2026-10-12 (user): one run = one W.I.N.G.; resume an in-progress run, otherwise start one (stamina 30), at most
    # one Produce per run. 2026-10-13 (user): this saved setting itself is the stamina consent (no extra start dialog;
    # the page still sends confirmed:true and the host re-derives the spend flag from the saved profile). Renamed from
    # 「Produce开新一局」 (old saved "No" values are dropped, see REMOVED_OPTIONS). 2026-10-13 (user): the 「开始」 section is
    # gone from the 「Produce」 page; pressing 开始 there is the consent, so the switch is fixed to Yes (only case) and a
    # saved "No" is migrated away (_migrate_options; the host and the page drop an unknown case the same way). An
    # in-progress Produce is continued only after the P idol / team check (tools/ui_wing.py ``verify_resume``).
    "Produce可开新局": {"label": "没有进行中就开新局", "default_case": "Yes", "cases": ("Yes",),
                        "description": "有进行中的一局：核对 P 偶像与编队，一致才接着打，否则停止。没有：开新局。"
                                       "消耗：每局体力 30（按「プロデュース開始」），局数按「次数」。"},
    # 2026-11-19 user: ticking the task is the consent (like W.I.N.G.), so the switch is fixed to Yes and hidden.
    "Collabo使用Pass": {"label": "使用 Pass", "default_case": "Yes", "cases": ("Yes",),
                       "description": "固定开启：勾选任务即同意。消耗：每局コラボフェスパス 1。"},
    # 2026-11-21 user: ticking the task is the consent (like Collabo, W.I.N.G.), so the switch is fixed to Yes and hidden.
    "Auto开始消耗体力": {"label": "开始 Auto", "default_case": "Yes", "cases": ("Yes",),
                         "description": "固定开启：勾选任务即同意。消耗：体力 30×次数。"},
    # 2026-10-13: No = the next run checks the P card only and re-reads the unit (the user changed
    # the S/Guest of that unit in the game since Maa's last 编队快照). Never an authority; tools/ui_wing.py reads it.
    "Produce核对上次编队": {"label": "核对上次读到的 S/Guest", "default_case": "Yes",
                            "description": "开：开局前按上次读到的编队核对 S/Guest，不符就停。关：只核对 P，本次重新读取编队。"},
}
# UI-only options that are not spend switches, appended to the task's options in this order.
UI_ONLY_TASK_OPTIONS = {"AutoProduce": ("Produce核对上次编队",)}


def _family(node_or_entry):
    """``SC.Shop.Confirm.Exchange`` -> ``Shop``: the Pipeline family segment shared by a task entry and its nodes."""
    parts = str(node_or_entry).split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "SC" else None


def spend_actions_by_family(table_path=ACTION_TABLE):
    table = load_jsonc(Path(table_path))
    flag_by_action = {action: flag for flag, action in run_tasks.SPEND_FLAGS.items()}
    result = {}
    for action in table.get("actions", []):
        if not action.get("consuming") or action.get("id") not in flag_by_action:
            continue
        families = {_family(node) for node in action.get("nodes", [])} - {None}
        for family in families:
            result[family] = {"spend_action": action["id"], "spend_flag": flag_by_action[action["id"]]}
    return result


def _option_entry(key, option):
    kind = option.get("type", "select")
    cases = [{"name": case.get("name")} for case in option.get("cases", []) or [] if isinstance(case, dict)]
    entry = {
        "key": key,
        "type": kind if kind in KNOWN_OPTION_TYPES else "unknown",
        "label": option.get("label", key),
        "description": option.get("description", ""),
        "default_case": option.get("default_case"),
        "cases": cases,
    }
    if kind == "input":
        entry["inputs"] = [{k: v for k, v in item.items() if k in ("name", "label", "description", "default",
                                                                    "pipeline_type", "verify")}
                           for item in option.get("inputs", []) or [] if isinstance(item, dict)]
        if len(entry["inputs"]) == 1:  # single-input option: the profile / run config carries one plain string
            entry["default_case"] = entry["inputs"][0].get("default", "")
    return entry


def export_schema(interface_path=INTERFACE_PATH, table_path=ACTION_TABLE):
    interface_path = Path(interface_path)
    root_doc = load_jsonc(interface_path)
    interface = run_tasks.load_interface(interface_path)
    spends = spend_actions_by_family(table_path)
    tasks = []
    for name, task in interface.tasks.items():
        family = _family(task.get("entry"))
        spend = TASK_SPEND_ACTIONS.get(name) or spends.get(family, {})
        text = f"{task.get('label', '')} {task.get('description', '')}"
        tasks.append({
            "name": name,
            "label": task.get("label", name),
            "description": task.get("description", ""),
            "entry": task["entry"],
            "default_check": bool(task.get("default_check", False)),
            "options": list(task.get("option", []) or []),
            "ui": {
                "spend_flag": spend.get("spend_flag"),
                "spend_action": spend.get("spend_action"),
                "spend_option": dict(SPEND_OPTIONS[name]) if spend and name in SPEND_OPTIONS else None,
                "migrated": UNMIGRATED_MARK not in text,
            },
        })
    options = {key: _option_entry(key, option) for key, option in interface.options.items() if isinstance(option, dict)}

    def add_ui_only(task, key):
        _require(key not in interface.options, f"UI 选项与 interface 冲突: {key}")
        meta = UI_ONLY_OPTIONS[key]
        options[key] = {"key": key, "type": "switch", "label": meta["label"], "description": meta["description"],
                        "default_case": meta["default_case"],
                        "cases": [{"name": c} for c in meta.get("cases", ("Yes", "No"))],
                        "ui_only": True}
        task["options"].append(key)

    for task in tasks:
        for key in UI_ONLY_TASK_OPTIONS.get(task["name"], ()):
            add_ui_only(task, key)
        spend_option = task["ui"]["spend_option"]
        if not spend_option:
            continue
        key = spend_option["key"]
        if key in UI_ONLY_OPTIONS:
            add_ui_only(task, key)
        _require(key in task["options"] and spend_option["when"] in {c["name"] for c in options[key]["cases"]},
                 f"任务 {task['name']} 的消耗开关无效: {key}")
    presets = []
    for name, preset in interface.presets.items():
        presets.append({
            "name": name,
            "label": preset.get("label", name),
            "description": preset.get("description", ""),
            "task": [{k: item[k] for k in ("name", "enabled", "option") if k in item}
                     for item in preset.get("task", []) or [] if isinstance(item, dict)],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "assets/interface.json",
        "interface": {k: root_doc.get(k) for k in ("name", "label", "title", "version", "interface_version")},
        "run_config": {
            "default_preset": run_tasks.DEFAULT_PRESET,
            "default_task_timeout_s": run_tasks.DEFAULT_TASK_TIMEOUT_S,
            "spend_flags": dict(run_tasks.SPEND_FLAGS),
            # project_policy.json resource_usage.fes_tickets (display + UI preflight mirror; never an authority)
            "fes_tickets": dataclasses.asdict(fes_allocation.load_policy()),
        },
        "tasks": tasks,
        "options": options,
        "presets": presets,
    }


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def spend_flags_for_profile(schema, profile):
    """``run_tasks.SPEND_FLAGS`` keys consented by the profile: only enabled tasks whose explicit spend option
    (``task.ui.spend_option``; schema default when unset) holds the consenting case."""
    return sorted({flag for flag, _action in _profile_spends(schema, profile)})


def _removed_tasks(schema, profile):
    """Names the user removed from the 方案 (``removed_tasks``). They never run and never consent to a spend."""
    removed = profile.get("removed_tasks", []) if isinstance(profile, dict) else []
    known = {task["name"] for task in schema["tasks"]}
    _require(isinstance(removed, list) and all(isinstance(n, str) and n in known for n in removed)
             and len(set(removed)) == len(removed), "removed_tasks 无效")
    return set(removed)


def _profile_spends(schema, profile):
    tasks_by_name = {task["name"]: task for task in schema["tasks"]}
    removed = _removed_tasks(schema, profile)
    out = []
    for item in profile.get("tasks") or []:
        task = tasks_by_name.get(item.get("name")) if isinstance(item, dict) else None
        if not task or item.get("enabled", True) is not True or task["name"] in removed:
            continue
        spend_option, flag = task["ui"].get("spend_option"), task["ui"].get("spend_flag")
        if not spend_option or flag not in run_tasks.SPEND_FLAGS:
            continue
        key = spend_option["key"]
        value = (item.get("option") or {}).get(key, schema["options"][key]["default_case"])
        if value == spend_option["when"]:
            if task["name"] in SPEND_ACTION_OPTIONS:
                option = {k: (item.get("option") or {}).get(k, schema["options"][k]["default_case"])
                          for k in SPEND_ACTION_OPTIONS[task["name"]]}
                actions = sorted(run_tasks.item_spend_actions({"name": task["name"], "option": option}))
            else:
                actions = [run_tasks.SPEND_FLAGS[flag]]
            out.extend((flag, action) for action in actions)
    return out


task_config = run_tasks.task_config  # retained for existing profile callers


def fes_preflight(schema, profile, observed=None, policy=None, interface=None):
    """The shared フェス参加権 plan (``fes_allocation.Plan``) of a UI profile. Only tasks that will really consume are
    included: Match with 只采样 = No, Grade with its explicit 使用 Fes 参加权 consent. ``observed``: a fresh rights
    reading or None (unknown: never guessed, the tasks re-read on their Fes pages)."""
    config = _profile_to_run_config(schema, profile)
    plans = run_tasks.plan_tasks(interface or run_tasks.load_interface(), config)
    return run_tasks.fes_preflight(plans, config.get("allow_spend", ()), observed, policy)


def profile_to_run_config(schema, profile, observed=None, policy=None):
    """UI profile -> run config for ``run_tasks.plan_tasks``. Only names, enabled flags, known option cases and the
    ``allow_spend`` actions consented by explicit per-task spend options pass; UI-only options are not forwarded.
    2026-10-07: a profile whose shared フェス参加権 plan is refused (recovery tickets against the policy, a rehearsal
    count, an all-in Match ordered before Grade, an over-allocation) raises ``ValueError`` with the reasons."""
    config = _profile_to_run_config(schema, profile)
    fes = fes_preflight(schema, profile, observed, policy)
    _require(not fes.refused, "Fes 参加权分配被拒绝：" + "；".join(fes.messages()[:len(fes.errors)]))
    return config


# 2026-10-08 (mirror of ui/components/store.js migrateLegacyProfile): 周常 left the W.I.N.G. T.E. goal options and became
# the separate WeeklyProduce task; renamed cases keep the user's choice; 25 % (no longer offered) becomes 20 %.
LEGACY_WEEKLY_GOAL = "周常"
# 2026-10-12 (U07/U09): P items not supported -> saved item plans fall back (saved 2/3 runs fell back to 1 until
# 2026-10-26, when 连跑 made 1–5 runs real choices).
LEGACY_CASES = {"Produce目标": {"粉丝": "只过季门槛"},
                "ProduceP道具": {k: "不带" for k in ("手帐+羁绊", "习惯（手帐+絆）", "开局强化", "决赛保险（后续）", "体力（后续）")},
                "Produce休息阈值": {"25%": "20%"},
                # 2026-10-11: the default stage is again the latest stage of the observed progress; the old
                # overall 「最新」 maps to that case. 最新 Vo/Da/Vi stay developer cases until the reward table is verified.
                "Match关卡": {"最新": "最新（全部关卡）"},
                # 2026-10-10: 训练卡 is a card-id input (any confirmed S card); "" = default (first trainable of the sort)
                "训练卡": {"未选择": "", "【くっつく、くっつかない】芹沢あさひ": "2050170010"},
                # 2026-10-11: W.I.N.G. on/off settings became switches (Yes/No)
                **{key: {"开": "Yes", "关": "No"} for key in ("Produce终局清空SP", "Produce报酬读取", "Produce保护Lv5书",
                                                            "Produce再抽选")},
                "Produce报酬后OK": {"按 OK": "Yes", "交给我": "No"},
                # 2026-10-28: the gift box option became on/off (no daily cap, no unknown-item stop)
                "プレゼント领取": {"开启（一括，每日 1 次）": "开启", "开启（一括，含未知物品）": "开启"},
                # 2026-10-30 (user): Auto 育成方針 has no 「不改」 any more (オート1 is always set by Maa)
                "Auto育成方針": {"不改": "自动"},
                # 2026-11-19: Collabo 局数/等级 became steppers like Match 次数
                "Collabo局数": {"用完": "全部用完", **{f"{n} 局": str(n) for n in range(1, 7)}},
                "Collabo等级": {f"Lv.{n}": str(n) for n in range(1, 31)}}
# 2026-10-09: settings removed from the UI (no shop budget at all since 2026-10-28; reserve is visible as of 10-12),
# 训练 once-per-day (always) and list scroll steps (3). A saved profile drops them instead of failing the run config.
REMOVED_OPTIONS = {"Shop": ("商店预算",), "Training": ("训练每日一次", "训练列表滚动"), "GradeFes": ("Grade彩排次数",),
                   "AutoProduce": ("Produce开新一局",),
                   "WeeklyProduce": ("Auto核对P偶像", "Auto设定オート1", "Auto核对ユニット名")}
LEGACY_REHEARSAL = "Grade彩排次数"


def _migrate_options(name, option):
    """One task's saved options -> current keys/cases. Grade 彩排次数 N > 0 becomes 模式 = フェスリハーサル with N lives
    (still refused before any click, as before); 0 is simply dropped."""
    option = {k: LEGACY_CASES.get(k, {}).get(v, v) if isinstance(v, str) else v for k, v in option.items()}
    rehearsal = option.get(LEGACY_REHEARSAL)
    if name == "GradeFes" and isinstance(rehearsal, str) and rehearsal.isdigit() and int(rehearsal) > 0:
        option["Grade模式"] = "フェスリハーサル"
        option["Grade每日次数"] = rehearsal
    for key in REMOVED_OPTIONS.get(name, ()):
        option.pop(key, None)
    for key, meta in UI_ONLY_OPTIONS.items():   # a case a UI-only switch no longer has (Produce可开新局 "No")
        if key in option and "cases" in meta and option[key] not in meta["cases"]:
            option.pop(key)
    return option


def migrate_legacy_profile(schema, profile):
    """Saved UI profile with pre-2026-10-08 Produce cases / pre-2026-10-09 daily settings -> current options (pure; returns
    a new dict; see ``_migrate_options``). An AutoProduce
    entry with 「Produce目标」=「周常」 hands its checkbox and 方案 membership to WeeklyProduce and becomes unchecked
    with the default goal; nothing here enables live input or spending."""
    if not isinstance(profile, dict) or not isinstance(profile.get("tasks"), list):
        return profile
    tasks = []
    for item in profile["tasks"]:
        if isinstance(item, dict) and isinstance(item.get("option"), dict):
            item = {**item, "option": _migrate_options(item.get("name"), item["option"])}
        tasks.append(item)
    removed = list(profile["removed_tasks"]) if isinstance(profile.get("removed_tasks"), list) else None
    has_weekly = any(t["name"] == "WeeklyProduce" for t in schema["tasks"])
    index = next((i for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("name") == "AutoProduce"
                  and (t.get("option") or {}).get("Produce目标") == LEGACY_WEEKLY_GOAL), None)
    if has_weekly and index is not None:
        auto = tasks[index]
        option = {k: v for k, v in auto["option"].items() if k != "Produce目标"}
        weekly = {"name": "WeeklyProduce", "enabled": auto.get("enabled", True) is True, "option": {}}
        tasks = [t for t in tasks if not (isinstance(t, dict) and t.get("name") == "WeeklyProduce")]
        at = next(i for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("name") == "AutoProduce")
        tasks[at:at + 1] = [{**auto, "enabled": False, "option": option}, weekly]
        if removed is not None:
            removed = [n for n in removed if n != "WeeklyProduce"]
            if "AutoProduce" in removed:
                removed.append("WeeklyProduce")
    out = {**profile, "tasks": tasks}
    if removed is not None:
        out["removed_tasks"] = removed
    return out


def _profile_to_run_config(schema, profile):
    _require(isinstance(profile, dict) and set(profile) <= PROFILE_KEYS, "UI 档案字段无效")
    profile = migrate_legacy_profile(schema, profile)
    tasks_by_name = {task["name"]: task for task in schema["tasks"]}
    items = profile.get("tasks")
    _require(isinstance(items, list) and items, "UI 档案需要任务列表")
    removed = _removed_tasks(schema, profile)
    seen, out = set(), []
    for item in items:
        _require(isinstance(item, dict) and set(item) <= {"name", "enabled", "option"}, f"任务项无效: {item}")
        name = item.get("name")
        _require(name in tasks_by_name and name not in seen, f"未知或重复任务: {name}")
        seen.add(name)
        chosen = item.get("option") or {}
        _require(isinstance(chosen, dict), f"选项必须为对象: {name}")
        option = {}
        for key, value in chosen.items():
            if key in run_tasks.RETIRED_OPTIONS.get(name, ()):     # removed option of an older saved profile
                continue
            _require(key in tasks_by_name[name]["options"], f"任务 {name} 没有选项 {key}")
            spec = schema["options"][key]
            if spec["type"] == "input":  # single-input option (e.g. Produce card id): one string matching `verify`
                inputs = spec.get("inputs") or []
                _require(len(inputs) == 1 and isinstance(value, str) and len(value) <= 256, f"输入选项无效: {key}")
                _require(not inputs[0].get("verify") or re.fullmatch(inputs[0]["verify"], value) is not None,
                         f"输入选项 {key} 不符合格式")
                option[key] = value
                continue
            _require(spec["type"] in ("select", "switch"), f"暂不支持的选项类型: {key}")
            if value not in {case["name"] for case in spec["cases"]}:
                continue   # a case renamed or removed since the profile was saved: the option's default applies
            if not spec.get("ui_only"):
                option[key] = value
        entry = {"name": name, "enabled": item.get("enabled", True) is True and name not in removed}
        if option:
            entry["option"] = option
        out.append(entry)
    config = {"tasks": out}
    allow_spend = sorted({action for _flag, action in _profile_spends(schema, profile)})
    if allow_spend:
        config["allow_spend"] = allow_spend
    if "task_timeout_s" in profile:
        config["task_timeout_s"] = profile["task_timeout_s"]
    return config


def dumps(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def mock_bundle(ui_dir=UI_DIR):
    """ES module text with every fixture/static JSON the browser preview needs (CSP forbids fetch)."""
    ui_dir = Path(ui_dir)
    parts = ["// Generated by tools/ui_schema.py --mock-bundle; do not edit. Synthetic preview data only.\n"]
    for name, rel in {**FIXTURE_FILES, **STATIC_FILES}.items():
        folder = ui_dir / ("fixtures" if name in FIXTURE_FILES else "")
        data = json.loads((folder / rel).read_text(encoding="utf-8"))
        parts.append(f"export const {name} = {json.dumps(data, ensure_ascii=False, indent=2)};\n")
    return "".join(parts)


def protocol_module(ui_dir=UI_DIR):
    """ES module mirror of ui/protocol.json (the page CSP forbids fetching it at runtime)."""
    data = json.loads((Path(ui_dir) / "protocol.json").read_text(encoding="utf-8"))
    return ("// Generated by tools/ui_schema.py --protocol-module from ui/protocol.json; do not edit.\n"
            f"export default {json.dumps(data, ensure_ascii=False, indent=2)};\n")


def safe_out_path(raw):
    path = Path(raw)
    path = (path if path.is_absolute() else Path.cwd() / path).resolve()
    allowed = PRIVATE_UI_DIR.resolve()
    _require(path != allowed and allowed in path.parents, f"--out 只能写到 {allowed} 之内")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="write under dev_assets/private/ui/ instead of stdout")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock-bundle", action="store_true", help="print ui/fixtures/mock-bundle.js")
    group.add_argument("--protocol-module", action="store_true", help="print ui/protocol.js")
    args = parser.parse_args(argv)
    if args.mock_bundle:
        text = mock_bundle()
    elif args.protocol_module:
        text = protocol_module()
    else:
        text = dumps(export_schema())
    if args.out:
        try:
            path = safe_out_path(args.out)
        except ValueError as error:
            parser.error(str(error))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
