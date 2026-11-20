#!/usr/bin/env python3
"""Thin PI-v2 task launcher (migration batch B3): run the interface tasks in order on one Maa Tasker.

Why: MFAAvalonia/MXU loading of the custom WebView2 controller is unverified, so this launcher reads the
same ProjectInterface data (assets/interface.json + imported assets/tasks/*.json + assets/presets/*.json),
binds the resource bundles (agent.core.resource_bundles.bundle_paths) to a controller and posts each
selected task's entry with ``tasker.post_task``. A failed or timed-out task is recorded and the NEXT task
still runs. A JSON run summary is written under dev_assets/private/run_logs/.

Input modes (decided once, before any controller exists):

- ``read_only`` (default): every Pipeline Click node stays disabled and the controller refuses clicks.
- ``offline_simulated``: ``--offline-frames`` replays private 1280x720 frames; with ``--simulate-input``
  the disabled Click nodes are enabled through pipeline_override, but clicks reach only the replay
  controller (they change the replayed frame). Never touches a browser, host or device. Task ledgers,
  samples and journals of a replay go to ``dev_assets/private/offline-replay/<run-id>/`` (summary:
  ``offline.isolated_root``), never to the live ``run_logs`` the daily planner reads.
- ``live_input`` (B3.5, ``--live``): needs project_policy.json ``live_input_enabled: true`` with a resolvable
  ``risk_acceptance_reference`` (ADR-011), ``--session`` whose host wrote ``<session>.pipeline-input.json``, and an
  explicit task list (no preset). Click nodes are enabled for the chosen action-table families; Maa's point inside
  the node's rect target is mapped to one action id and sent to the host's ``pipeline-click``; the host picks the
  real point and paces gestures. Per-run/per-task click caps and a session length cap come from the run config
  (conservative defaults). The production frame permit is enabled only for the one-shot ``--supervised-startup``
  or ``--supervised-task`` entry.
- ``CardScan`` (owned-card self-scrolling scan) exists only for ``--live --supervised-task``:
  it is not a ProjectInterface task, so no preset, UI or ordinary run can plan it, and every other launch path keeps the
  ``SC.CardScan.*`` Click nodes disabled. Its inputs are admitted by ACTION ID and node prefix
  (``SUPERVISED_TASK_ACTIONS`` / ``SUPERVISED_TASK_NODE_PREFIXES``), enforced by the WebView2 gate, not by family alone.
- ``KnowhowScan`` (ノウハウブック一覧) is supervised-only the same way: ``mode`` calibrate
  (wheel-step measurement, no sort change) or scan; it refuses to plan until the ``knowhow-scan`` action-table entries
  are switched in (tools/knowhow_scan_build.py).

Spending: the consuming table actions are ``shop-exchange-confirm`` (B4, マニー daily discount cards),
``training-start`` (B5, one basic Bronze ticket), ``grade-tension-confirm`` (B5c: the 参加ユニット primary button;
テンション決定 is irreversible but free, ライブ開始 on the TensionDecided stage consumes one フェス参加権; each press needs
its own grant) and
``match-skip-confirm`` (B5b, one フェス参加権 per selected skip). Each Click node stays disabled unless the run config lists
its action in ``allow_spend`` (CLI ``--allow-shop-spend``, ``--allow-training-spend``, ``--allow-grade-spend``,
``--allow-match-spend``); even then each click needs a one-shot SpendGate grant that the
task's custom recognition obtains for the exact frame after verifying the confirmation screen. Offline replay uses
the same rule against the replayed frame.
"""
from __future__ import annotations

import re
import argparse
import copy
import dataclasses
import datetime as _dt
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import maa_sinks  # noqa: E402
from tools.check_project import interface_documents, load_jsonc  # noqa: E402

PRIVATE = ROOT / "dev_assets" / "private"
RUN_LOG_DIR = PRIVATE / "run_logs"
INTERFACE = ROOT / "assets" / "interface.json"
PIPELINE_DIR = ROOT / "assets" / "resource" / "base" / "pipeline"
POLICY_PATH = ROOT / "project_policy.json"
DEFAULT_PRESET = "daily"
DEFAULT_TASK_TIMEOUT_S = 2400.0  # GradeFes: ≤30 s + ≤1800 s of active live play + ≤60 s after OK (B5j); MatchSkip may press + 20× at ≥5 s plus ≤30 scrolls
FRAMEWORK = "v5.12.3"
CANVAS = (1280, 720)
RUN_CONFIG_KEYS = {"preset", "tasks", "input_enabled", "families", "task_timeout_s", "max_clicks", "task_max_clicks",
                   "session_max_minutes", "allow_spend"}
# The consuming actions a run config may opt into (one per task; each still needs a frame-bound grant).
GRADE_REHEARSAL_SPEND_ACTIONS = tuple("grade-rehearsal-" + d for d in ("easy", "normal", "hard"))
# 2026-10-29 user 「开始周常auto」: the official Auto's プロデュース開始 (stamina 30 × the task's 次数, one press after the
# dialog's before/after check); スキップ stays disabled (cost unverified).
WEEKLY_AUTO_SPEND_ACTION = "weekly-auto-start"
# 2026-10-30 user 「允许」: with the panel's ループ, the recovery dialog's プロデュース開始 (stamina + set sodas, 750; the
# task config picks it instead of weekly-auto-start, like Grade's difficulty buttons).
WEEKLY_AUTO_SODA_SPEND_ACTION = "weekly-auto-soda-start"
# 2026-10-31 user 「让maa自动推进 每天消耗完20」: Match 推进 plays the frontier stage (ライブ開始, 1 right per live) before
# the Skip; a MatchSkip run with 推进 then holds two consuming actions (match_play / match_custom).
MATCH_PLAY_SPEND_ACTION = "match-play-live-start"
MATCH_PLAY_GRANT_LIMIT = 40     # = match_play.MAX_LIVES
# 2026-11-18 user: Collabo Fes テンション決定, one コラボフェスパス per live, up to 6 per run.
COLLABO_SPEND_ACTION = "collabo-tension-decide"
COLLABO_GRANT_LIMIT = 6         # = collabo_task.PASS_MAX
# 2026-11-19 user 「继续做满员和pass等待」: live CollaboFes waits on home for 満員 / Pass recovery inside its own timeout.
COLLABO_TASK_TIMEOUT_S = 4 * 3600
SPEND_ACTIONS_ALLOWED = ("shop-exchange-confirm", "training-start", "grade-tension-confirm", "match-skip-confirm",
                         *GRADE_REHEARSAL_SPEND_ACTIONS, WEEKLY_AUTO_SPEND_ACTION, WEEKLY_AUTO_SODA_SPEND_ACTION,
                         MATCH_PLAY_SPEND_ACTION, COLLABO_SPEND_ACTION)
SPEND_FLAGS = {"allow_shop_spend": "shop-exchange-confirm", "allow_training_spend": "training-start",
               "allow_grade_spend": "grade-tension-confirm", "allow_match_spend": "match-skip-confirm",
               "allow_weekly_auto_spend": WEEKLY_AUTO_SPEND_ACTION, "allow_collabo_spend": COLLABO_SPEND_ACTION}
# No click caps. As in MaaFramework / MAA, loops are bounded per
# node (max_hit, timeout, on_error), a task by its timeout and the stop button, spends by explicit options + frame-bound
# grants. A run config may still name an explicit cap (developer CLI, e.g. supervised Startup 1–4); none by default.
DEFAULT_MAX_CLICKS = None
DEFAULT_TASK_MAX_CLICKS = None
DEFAULT_SESSION_MAX_MINUTES = 240
MAX_SESSION_MINUTES = 720        # = the host's PipelineLimits.SessionMaxMs (12 h)
MAX_TASK_TIMEOUT_S = 4 * 3600
CARD_SCAN_MAX_SCROLLS = 80       # explicit full-library run; Pipeline default remains 30
CARD_SCAN_TABS_MAX = 2


# Tasks that click (None: no cap) and tasks that never click (0: Diagnostics; unlisted placeholders get 0 too).
LIVE_TASK_CLICK_CAPS = {name: None for name in (
    "Startup", "MissionStatus", "ResourceRead", "PresentClaim", "MissionsClaim", "Shop", "Training", "MatchSkip",
    "GradeFes", "Business", "WeeklyProduce", "CardScan", "CollaboFes")}
LIVE_TASK_CLICK_CAPS["Diagnostics"] = 0
# ResourceRead (2026-10-26 「只读取资源」): no click of its own; only shared known-popup closes before the home read.
# Its offline entry timeout (8 s) covers the three unreadable home frames (OCR each) before it gives up.
# Static replay frames never change by themselves; shorten the waits (same values as the B1 harness).
# GoHome 8 s: one BackOne round (~30 page guards, several OCR) takes 1-3 s; 3 s expired before a
# 3-capture frame advanced (Startup login-board replay). 12 s: rounds of ~2.5 s on a busy machine still
# let 8 s expire on the login board's third capture.
OFFLINE_TIMEOUTS = {"SC.Common.GoHome": 12000, "SC.Common.HomeReached": 1500, "SC.Loading.Wait": 3000,
                    "SC.MissionStatus.Entry": 3000, "SC.MissionStatus.AtHome": 1500,
                    "SC.MissionStatus.HomeClean": 1500, "SC.ResourceRead.Entry": 8000, "SC.Shop.Main": 6000, "SC.Shop.AtHome": 1500,
                    "SC.Shop.HomeClean": 1500, "SC.Shop.Finish": 1500, "SC.Shop.FinishClean": 1500,
                    **{f"SC.{task}.{node}": value for task in ("Training", "Grade", "Match", "Business")
                       for node, value in (("Main", 6000), ("AtHome", 1500), ("HomeClean", 1500), ("Finish", 1500),
                                           ("FinishClean", 1500))}}
OFFLINE_CLICK_TIMEOUT = 3000
# Read-only: the MissionStatus report is the result; returning home would need the disabled back key.
READ_ONLY_OVERRIDE = {"SC.MissionStatus.Return": {"next": []}}

READ_ONLY, OFFLINE_SIMULATED, LIVE_INPUT = "read_only", "offline_simulated", "live_input"
SUPERVISED_STARTUP_FAMILIES = frozenset({"startup", "popup", "nav"})
SUPERVISED_STARTUP_MAX_CLICKS = 4
SUPERVISED_TASK_FAMILIES = {
    "MissionStatus": frozenset({"mission", "nav", "popup"}),
    "ResourceRead": frozenset({"popup"}),
    "PresentClaim": frozenset({"present", "nav", "popup"}),
    "MissionsClaim": frozenset({"mission-claim", "mission", "nav", "popup"}),
    "Shop": frozenset({"shop-money", "nav", "popup"}),
    "Training": frozenset({"training-bronze", "nav", "popup"}),
    "MatchSkip": frozenset({"fes-menu", "match-fes", "nav", "popup"}),
    "GradeFes": frozenset({"fes-menu", "grade-fes", "grade-live", "nav", "popup"}),
    "Business": frozenset({"business", "nav", "popup"}),
    # Official Auto (weekly): navigation, ±/決定/キャンセル/開始; スキップ stays disabled.
    "WeeklyProduce": frozenset({"weekly-auto", "nav", "popup"}),
    # Collabo Fes: banner, stages, Hard Lv, unit, テンション決定, the live's skill/drag/confirm, report OK.
    # nav (2026-11-19 live): お知らせ opened on its own on home during a 満員 wait; its close is the shared nav-back.
    "CollaboFes": frozenset({"collabo-fes", "nav", "popup"}),
    # home-idol belongs to training-bronze; SUPERVISED_TASK_ACTIONS narrows CardScan to its own ids below.
    "CardScan": frozenset({"training-bronze", "card-scan", "nav", "popup"}),
}
# Supervised-only tasks (not in the ProjectInterface): name -> (entry, pipeline file, options node).
SUPERVISED_ONLY_TASKS = {"CardScan": ("SC.CardScan.Entry", "card_scan.json", "SC.CardScan.Config")}
# Click nodes under these prefixes are enabled only by the supervised entry that owns them (never by family alone).
SUPERVISED_ONLY_NODE_PREFIXES = ("SC.CardScan.",)
# Exact action ids (and the recognition-node prefixes) a supervised task may click; tasks absent here use families.
SUPERVISED_TASK_ACTIONS = {
    "CardScan": frozenset({"home-idol", "idol-portal-list", "idol-list-tab-support", "idol-list-tab-produce",
                           "idol-list-scroll-down", "idol-list-scroll-up", "nav-back",
                           # the shared known-popup closes every supervised task keeps (SC.Popup.CloseKnown), incl.
                           # the right SKIP of the 新ガシャ/新アイドル登場 cut-in (a seg25 scan stopped on it)
                           "popup-reward-ok", "popup-event-fan-reward-ok", "popup-event-fan-receipt-ok", "popup-fes-period-reward-ok", "popup-mission-completed-ok", "popup-help-close", "popup-pickup-close",
                           "popup-story-skip", "popup-new-gacha-skip"}),
}
SUPERVISED_TASK_NODE_PREFIXES = {"CardScan": ("SC.CardScan.", "SC.Popup.")}
# ---- KnowhowScan supervised entry (2026-10-12) --------------------------------------
# Same pattern as CardScan: supervised-only, admitted by exact action id and the SC.KnowhowScan./SC.Popup. node prefixes.
# The knowhow-scan table entries are staged by tools/knowhow_scan_build.py; until they are in the action table the task
# refuses to plan (knowhow_scan_table_refusal). A full-library segment fits the host's 600-input run window.
KNOWHOW_TASK = "KnowhowScan"
KNOWHOW_TASK_CAP = 600
KNOWHOW_NAV_INPUTS = 5           # home-idol, idol-portal-list, ノウハウブック tab, back from アイドル一覧 and アイドル page
KNOWHOW_WIND_DOWN_S = 240        # scan max_seconds = task timeout - this: restore the sort, leave and reach home in time
KNOWHOW_NEWEST_INPUTS = (8, 12)  # newest-books scan budget: sort switch + restore + leave, plus open + detail wheels per book
KNOWHOW_KEEP_INPUTS = 3          # keep step per locked book: open (+ one re-open) + 保護
KNOWHOW_POPUP_ACTIONS = ("popup-reward-ok", "popup-event-fan-reward-ok", "popup-event-fan-receipt-ok", "popup-mission-completed-ok", "popup-help-close", "popup-pickup-close",
                         "popup-story-skip", "popup-new-gacha-skip")
SUPERVISED_TASK_FAMILIES[KNOWHOW_TASK] = frozenset({"training-bronze", "card-scan", "knowhow-scan", "nav", "popup"})
SUPERVISED_ONLY_TASKS[KNOWHOW_TASK] = ("SC.KnowhowScan.Entry", "knowhow_scan.json", "SC.KnowhowScan.Config")
SUPERVISED_ONLY_NODE_PREFIXES = SUPERVISED_ONLY_NODE_PREFIXES + ("SC.KnowhowScan.",)
SUPERVISED_TASK_NODE_PREFIXES[KNOWHOW_TASK] = ("SC.KnowhowScan.", "SC.Popup.")
LIVE_TASK_CLICK_CAPS[KNOWHOW_TASK] = None


def knowhow_task_actions(lock=False):
    """Action ids a KnowhowScan run may send; the keep step's 保護 only for a run with locking on (keep_lock)."""
    from agent.core import knowhow_scan as ks

    keep = {a["id"] for a in ks.KEEP_ACTIONS}
    return frozenset({"home-idol", "idol-portal-list", "nav-back", *KNOWHOW_POPUP_ACTIONS,
                      *(a["id"] for a in ks.ACTIONS if lock or a["id"] not in keep)})


def knowhow_plan_lock(plan) -> bool:
    """True when this KnowhowScan plan locks new books (SC.KnowhowScan.Config attach keep_lock)."""
    _, _, node = SUPERVISED_ONLY_TASKS[KNOWHOW_TASK]
    return ((plan.pipeline_override or {}).get(node) or {}).get("attach", {}).get("keep_lock") is True


SUPERVISED_TASK_ACTIONS[KNOWHOW_TASK] = knowhow_task_actions()
SUPERVISED_TASK_SPEND = {"Shop": "shop-exchange-confirm", "Training": "training-start",
                        "MatchSkip": "match-skip-confirm", "GradeFes": "grade-tension-confirm",
                        "WeeklyProduce": WEEKLY_AUTO_SPEND_ACTION, "CollaboFes": COLLABO_SPEND_ACTION}

# ---- W.I.N.G. supervised entry -------------------------------
# ``Wing`` is supervised-only like CardScan (not the ProjectInterface AutoProduce placeholder). One launcher run is one
# bounded segment of a Produce: ``attach`` (default) takes over an already running Produce, ``start`` also walks the
# setup pages and may press プロデュース開始 once (stamina 30) only with --allow-produce-spend. Inputs are admitted by
# action id (every wing-* table action; the consuming wing-produce-start only when spending is allowed) and only from
# SC.Wing.Click.* nodes. The Agent stops at the next week start once ``segment_acts`` clicks were sent.
WING_TASK = "Wing"
WING_SPEND_ACTION = "wing-produce-start"
WING_TASK_CAP = 1500            # W.I.N.G. segment length in acts (Agent-side; not a click cap): normally the whole Produce
WING_SEGMENT_RESERVE = 30       # clicks kept after the segment boundary to finish the current week cleanly
WING_OPTION_KEYS = {"mode", "team_confirmed", "sp_mode", "sp_done", "live_mode", "segment_acts", "memory_file",
                    "produce_config", "from_home", "minigame", "unit_search", "resume_mismatch_ok", "ocr",
                    "allow_reward_reroll", "team_edit", "edit_only", "read_team", "verify_resume", "owned_cards", "sp_small_view",
                    "picker_sort", "live_timed_click", "live_timing_period_s", "live_timing_event_offset_ms",
                    "settings_fix", "halo_giveup", "halo_route", "halo_book", "halo_book_plan", "halo_goals"}
# Options removed from a task (2026-11-07 user 「只做掘书线」: Halo路线); a saved profile may still carry them.
RETIRED_OPTIONS = {"AutoProduce": ("Halo路线",)}
# Durable プロデュース開始 ledger of live runs (agent.core.wing_ledger, review P0-1); offline replays use an in-memory one.
WING_SPEND_LEDGER = PRIVATE / "wing-spend-ledger.json"
# In-game プロデュース設定 record of live runs (agent.core.wing_options, 2026-10-13); offline replays skip the check.
WING_SETTINGS_FILE = PRIVATE / "wing-ui" / "produce-settings.json"


def wing_reroll_option(item):
    """The Wing item's 再抽選 launcher authority (review P1-7): the task setting 「重新抽选」 as passed by tools/ui_wing.py
    (default off). It is NOT a spend grant: wing-reward-reroll-confirm is not in SPEND_ACTIONS_ALLOWED and its targets
    stay unverified, so the Agent only plans with it; nothing is tapped."""
    option = (item or {}).get("option") if isinstance(item, dict) else None
    return isinstance(option, dict) and option.get("allow_reward_reroll") is True
SUPERVISED_TASK_FAMILIES[WING_TASK] = frozenset({"wing-nav", "wing-week", "wing-live", "wing-skill", "wing-end",
                                                 "wing-setup", "wing-start", "wing-giveup", "wing-halo"})
SUPERVISED_ONLY_TASKS[WING_TASK] = ("SC.Wing.Nav.Start", "wing.json", None)
SUPERVISED_ONLY_NODE_PREFIXES = SUPERVISED_ONLY_NODE_PREFIXES + ("SC.Wing.",)
SUPERVISED_TASK_NODE_PREFIXES[WING_TASK] = ("SC.Wing.Click.",)
# Wing spend is checked by wing_options (mode: start only), not through SUPERVISED_TASK_SPEND.
LIVE_TASK_CLICK_CAPS[WING_TASK] = None
SPEND_ACTIONS_ALLOWED = SPEND_ACTIONS_ALLOWED + (WING_SPEND_ACTION,)
SPEND_FLAGS["allow_produce_spend"] = WING_SPEND_ACTION


def wing_task_actions(allow_spend=(), giveup=False, halo=False):
    """Action ids the Wing entry may click: every wing-* action, the consuming start only when it is allowed, the
    say "Halo" give-up (wing-giveup) only for a run with ``halo_giveup`` or the 掘书线 route, the Halo in-run taps
    (wing-halo) only for a run with ``halo_route``."""
    from agent.core.pipeline_actions import default_table

    return frozenset(a.id for a in default_table().actions if a.family in SUPERVISED_TASK_FAMILIES[WING_TASK]
                     and (not a.consuming or a.id in allow_spend) and (giveup or a.family != "wing-giveup")
                     and (halo or a.family != "wing-halo"))


def _wing_run_param(plan) -> dict:
    param = ((plan.pipeline_override or {}).get("SC.Wing.Nav.Start") or {}).get("custom_action_param") or {}
    return param.get("run") or {}


def wing_plan_giveup(plan) -> bool:
    """True when this Wing plan may give up a say "Halo" run: RunOptions.halo_giveup or the 掘书线 route."""
    run = _wing_run_param(plan)
    return run.get("halo_giveup") is True or run.get("halo_route") == "dig"


def wing_plan_halo(plan) -> bool:
    """True when this Wing plan plays a say "Halo" run (RunOptions.halo_route)."""
    return _wing_run_param(plan).get("halo_route") in ("dig", "fes")


def wing_options(option=None, *, allow_spend=(), task_cap=WING_TASK_CAP):
    """Validated Wing task options -> (``SC.Wing.Begin`` parameters, run options). Unknown keys are refused."""
    from agent.core import produce_config as pc
    from agent.core import wing_nav as nav
    from agent.core.wing_custom import private_path

    option = {} if option is None else option
    require(isinstance(option, dict) and set(option) <= WING_OPTION_KEYS,
            f"Wing option 只能包含 {sorted(WING_OPTION_KEYS)}")
    data = copy.deepcopy(option.get("produce_config") or pc.default_config("true_end"))
    require(isinstance(data, dict) and isinstance(data.get("manual"), dict), "Wing 只支持手动 W.I.N.G.（True End/粉丝）设置")
    if "live_mode" in option:
        require(option["live_mode"] in ("policy", "game_auto"), "live_mode 只能为 policy/game_auto")
        data["manual"]["live_mode"] = option["live_mode"]
    try:
        config = pc.ProduceConfig.from_dict(data)
        pc.wing_params(config)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"Wing produce_config 无效: {exc}") from None
    require(type(task_cap) is int and 0 < task_cap <= WING_TASK_CAP, f"Wing 点击上限须为 1–{WING_TASK_CAP}")
    # 2026-10-12: ``team_edit`` (start mode: put the configured team into the configured unit, wing_team_edit) and
    # ``read_team`` (read-only ユニット確認 / 発動中スキル for the support identities); both default off.
    # ``verify_resume`` (2026-10-13, tools/ui_wing.py): continue an in-progress Produce only after its P idol / P card /
    # configured S/Guest were verified (wing_nav.RunOptions); default off.
    run = {k: option[k] for k in ("mode", "team_confirmed", "sp_mode", "sp_done", "from_home", "minigame", "unit_search",
                                  "resume_mismatch_ok", "team_edit", "edit_only", "read_team", "verify_resume", "sp_small_view", "picker_sort",
                                  "live_timed_click", "live_timing_period_s", "live_timing_event_offset_ms",
                                  "settings_fix", "halo_giveup", "halo_route", "halo_book", "halo_book_plan", "halo_goals")
           if k in option}
    # ``ocr`` (roi|frame) is a Begin parameter of the session, not a RunOptions key (2026-10-10: it used to be put
    # into ``run`` and refused as an unknown run option).
    require(option.get("ocr", "roi") in ("roi", "frame"), "Wing ocr 只能为 roi/frame")
    require(type(option.get("allow_reward_reroll", False)) is bool, "Wing allow_reward_reroll 须为布尔值")
    require(not option.get("allow_reward_reroll") or pc.wing_params(config)["end"].get("reroll") is True,
            "allow_reward_reroll 需要任务设置「重新抽选」开启")
    run["max_acts"] = task_cap
    run["segment_acts"] = option.get("segment_acts", max(1, task_cap - WING_SEGMENT_RESERVE))
    require(type(run["segment_acts"]) is int and 0 < run["segment_acts"] <= task_cap,
            "segment_acts 须为 1..任务点击上限")
    spending = WING_SPEND_ACTION in allow_spend
    try:
        options = nav.RunOptions.from_params(run, input_enabled=True, allow_start_spend=spending)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Wing 运行选项无效: {exc}") from None
    require(not spending or options.mode == "start", "--allow-produce-spend 只用于 mode: start")
    # 2026-10-09: start mode verifies the team by avatar fingerprint (wing_unit_check); team_confirmed only lets an
    # "uncertain" fingerprint continue.
    params = {"produce_config": config.data if isinstance(config.data, dict) else dict(config.data), "run": run}
    if "owned_cards" in option:
        from agent.core.card_inventory import sanitize_owned_cards

        owned = sanitize_owned_cards(option["owned_cards"])
        require(owned is not None, "Wing owned_cards 须为对象")
        params["owned_cards"] = owned
    if "ocr" in option:
        params["ocr"] = option["ocr"]
    if option.get("memory_file"):
        params["memory_file"] = str(private_path(option["memory_file"]).relative_to(ROOT)).replace("\\", "/")
    return params, options


def wing_flow_nodes(pipeline_file=None):
    """Non-Click SC.Wing.* nodes reachable through next/on_error from SC.Wing.Nav.Start (the flow to enable for the
    task). Recognition-only nodes keep their shipped state (the frame guard checks it); Click nodes are enabled only
    by ``mode_override`` with the Wing action/node allowlist."""
    nodes = load_jsonc(pipeline_file or PIPELINE_DIR / "wing.json")
    seen, todo = set(), ["SC.Wing.Nav.Start"]
    while todo:
        name = todo.pop()
        if name in seen or name not in nodes:
            continue
        seen.add(name)
        node = nodes[name]
        todo += list(node.get("next") or []) + list(node.get("on_error") or [])
    return sorted(n for n in seen if nodes[n].get("action") != "Click")


def wing_plan(item, allow_spend=(), task_cap=WING_TASK_CAP):
    params, options = wing_options(item.get("option"), allow_spend=allow_spend, task_cap=task_cap)
    override = {name: {"enabled": True} for name in wing_flow_nodes()}
    override["SC.Wing.Nav.Start"] = {"enabled": True, "custom_action_param": params}
    if options.live_timed_click:
        override["SC.Wing.Click.Live.live_appeal_timing"] = {"pre_delay": 0}
    return TaskPlan(WING_TASK, "SC.Wing.Nav.Start", override)


def run_wing_offline(images, advances, option=None, *, allow_spend=(), task_cap=WING_TASK_CAP, task_options=None,
                     timeout_s=900.0, save_diagnostics=False, extra_override=None):
    """Simulated-input replay of the Wing entry (tests / tools/wing_click_agreement.py): the same plan, action/node
    allowlist, runtimes, spend authority and frame binder as ``--live --supervised-task``; clicks reach only the
    ReplayScenario. The table in use must have the wing-* actions ENABLED (tests enable a copy; the shipped table
    keeps them disabled, so this refuses every click there)."""
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker

    from agent.core.task_actions import ReportSink

    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    allow_spend = tuple(allow_spend)
    plan = wing_plan({"name": WING_TASK, "option": option}, allow_spend=allow_spend, task_cap=task_cap)
    actions = wing_task_actions(allow_spend, giveup=wing_plan_giveup(plan), halo=wing_plan_halo(plan))
    sink = ReportSink()
    scenario = ReplayScenario(images, advances, allow_input=True, allow_spend=allow_spend,
                              families=SUPERVISED_TASK_FAMILIES[WING_TASK], actions=actions)
    runtimes = task_runtimes(sink, ReplaySpendAuthority(scenario), task_options, wing_input=True,
                             wing_spend=WING_SPEND_ACTION in allow_spend,
                             wing_reroll=wing_reroll_option({"option": option}))
    tasker = build_tasker(create_replay_controller(scenario), sink, save_diagnostics=save_diagnostics,
                          runtimes=runtimes)

    def after_task(_plan):
        runtimes["wing"].report()

    base = mode_override(OFFLINE_SIMULATED, None, allow_spend, actions, SUPERVISED_TASK_NODE_PREFIXES[WING_TASK])
    merge_override(base, extra_override)   # tests: shorter After timeouts on static frames
    try:
        results = execute_plan([plan], tasker, sink=sink, base_override=base, timeout_s=timeout_s, after_task=after_task)
    finally:
        maa_sinks.release(tasker)
    return {"result": results[0], "scenario": scenario, "session": runtimes["wing"]}


def require(condition, message):
    if not condition:
        raise ValueError(message)


# ---- ProjectInterface ---------------------------------------------------------------------------------
@dataclass
class Interface:
    tasks: dict  # name -> task declaration (ordered as in the interface)
    options: dict
    presets: dict


def load_interface(path=INTERFACE):
    tasks, options, presets = {}, {}, {}
    for doc_path, document in interface_documents(Path(path)):
        require(isinstance(document, dict), f"PI 文件缺失或不是对象: {doc_path}")
        for task in document.get("task", []) or []:
            require(isinstance(task, dict) and isinstance(task.get("name"), str) and isinstance(task.get("entry"), str),
                    f"任务声明无效: {doc_path}")
            require(task["name"] not in tasks, f"任务重名: {task['name']}")
            tasks[task["name"]] = task
        options.update(document.get("option", {}) or {})
        for preset in document.get("preset", []) or []:
            require(isinstance(preset, dict) and isinstance(preset.get("name"), str), f"预设无效: {doc_path}")
            presets[preset["name"]] = preset
    return Interface(tasks, options, presets)


def merge_override(target, extra):
    for node, fields in (extra or {}).items():
        require(isinstance(fields, dict), f"pipeline_override 节点必须为对象: {node}")
        target.setdefault(node, {}).update(copy.deepcopy(fields))
    return target


INPUT_MAX_CHARS = 256


def _input_values(key, option, value):
    """Validated {input name: typed value} for a PI v2 ``input`` option. A single-input option also accepts a plain
    string (the UI profile / run config carries strings only); missing fields use the declared default."""
    inputs = option.get("inputs")
    require(isinstance(inputs, list) and inputs and all(isinstance(i, dict) and isinstance(i.get("name"), str)
                                                        for i in inputs), f"输入选项定义无效: {key}")
    names = [item["name"] for item in inputs]
    if value is None:
        given = {}
    elif isinstance(value, str):
        require(len(inputs) == 1, f"输入选项 {key} 有多个字段，需要对象")
        given = {names[0]: value}
    else:
        require(isinstance(value, dict) and set(value) <= set(names), f"输入选项 {key} 的字段无效")
        given = value
    resolved = {}
    for item in inputs:
        raw = given.get(item["name"], item.get("default", ""))
        require(isinstance(raw, str) and len(raw) <= INPUT_MAX_CHARS, f"输入选项 {key}.{item['name']} 必须为短字符串")
        if item.get("verify"):
            require(re.fullmatch(item["verify"], raw) is not None, f"输入选项 {key}.{item['name']} 不符合格式")
        kind = item.get("pipeline_type", "string")
        if kind == "int":
            require(re.fullmatch(r"-?\d{1,9}", raw) is not None, f"输入选项 {key}.{item['name']} 必须为整数")
            resolved[item["name"]] = int(raw)
        elif kind == "bool":
            require(raw in ("true", "false"), f"输入选项 {key}.{item['name']} 必须为 true/false")
            resolved[item["name"]] = raw == "true"
        else:
            resolved[item["name"]] = raw
    return resolved


def _substitute(node, values):
    """Replace ``{name}`` placeholders: a whole-string placeholder keeps the typed value, embedded ones become text."""
    if isinstance(node, dict):
        return {k: _substitute(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values) for v in node]
    if isinstance(node, str):
        whole = re.fullmatch(r"\{([^{}]+)\}", node)
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return re.sub(r"\{([^{}]+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), node)
    return node


def option_override(interface, key, value):
    option = interface.options.get(key)
    require(isinstance(option, dict), f"缺少选项定义: {key}")
    kind = option.get("type", "select")
    if kind == "input":
        return _substitute(copy.deepcopy(option.get("pipeline_override", {})), _input_values(key, option, value))
    require(kind in ("select", "switch"), f"启动器暂只支持 select/switch 选项: {key}")
    case_name = option.get("default_case") if value is None else value
    cases = {case.get("name"): case for case in option.get("cases", [])}
    if case_name not in cases and value is not None:
        # A profile saved before a case was renamed or removed (2026-10-28: 「开启（一括，每日 1 次）」) runs with the
        # option's default, as the UI's sanitizeProfile does, instead of failing the whole run.
        print(f"[run_tasks] 选项 {key} 没有分支 {case_name}，改用默认 {option.get('default_case')}", file=sys.stderr)
        case_name = option.get("default_case")
    require(case_name in cases, f"选项 {key} 没有分支 {case_name}")
    return cases[case_name].get("pipeline_override", {})


@dataclass(frozen=True)
class TaskPlan:
    name: str
    entry: str
    pipeline_override: dict = field(default_factory=dict)


def validate_run_config(config):
    require(isinstance(config, dict) and set(config) <= RUN_CONFIG_KEYS, "运行配置字段无效")
    require(type(config.get("input_enabled", False)) is bool, "input_enabled 必须为布尔值")
    families = config.get("families", [])
    require(isinstance(families, list) and all(isinstance(f, str) for f in families), "families 必须为字符串列表")
    timeout = config.get("task_timeout_s", DEFAULT_TASK_TIMEOUT_S)
    require(type(timeout) in (int, float) and 0 < timeout <= MAX_TASK_TIMEOUT_S, f"task_timeout_s 必须在 (0, {MAX_TASK_TIMEOUT_S}] 内")
    tasks = config.get("tasks")
    require(tasks is None or isinstance(tasks, list), "tasks 必须为列表")
    max_clicks = config.get("max_clicks")   # optional explicit cap; none by default
    require(max_clicks is None or (type(max_clicks) is int and max_clicks >= 0), "max_clicks 必须为非负整数（不填即不限）")
    caps = config.get("task_max_clicks", {})
    require((type(caps) is int and caps >= 0) or (isinstance(caps, dict) and all(
        isinstance(k, str) and type(v) is int and v >= 0 for k, v in caps.items())),
        "task_max_clicks 必须为非负整数或 {任务名: 整数}（不填即不限）")
    spend = config.get("allow_spend", [])
    require(isinstance(spend, list) and len(set(spend)) == len(spend) and set(spend) <= set(SPEND_ACTIONS_ALLOWED),
            f"allow_spend 只能列出 {list(SPEND_ACTIONS_ALLOWED)}")
    minutes = config.get("session_max_minutes", DEFAULT_SESSION_MAX_MINUTES)
    require(type(minutes) in (int, float) and 0 < minutes <= MAX_SESSION_MINUTES,
            f"session_max_minutes 必须在 (0, {MAX_SESSION_MINUTES}] 内")
    return config


def task_click_cap(config, name):
    """Explicit per-task cap of the run config (one int for every task, or a per-name dict); else None (no cap), or 0
    for a task that never clicks (Diagnostics, placeholders)."""
    caps = config.get("task_max_clicks", {})
    if type(caps) is int or name in caps:
        return caps if type(caps) is int else caps[name]
    return LIVE_TASK_CLICK_CAPS.get(name, 0)


def wing_task_cap(config):
    """W.I.N.G. segment length in acts (the Agent ends a segment at a week start after it; not a click cap). An explicit
    task cap of the run config shortens it."""
    cap = task_click_cap(config or {}, WING_TASK)
    return WING_TASK_CAP if cap is None else min(WING_TASK_CAP, max(1, cap))


def grade_batch_plan(plans, options=None):
    """Plan the same mode/day ledger as the runtime and scale only existing node hit counts."""
    from agent.core import grade_task as gt
    out, progress = [], None
    kwargs = (options or {}).get("grade", {})
    for plan in plans:
        if plan.name == "GradeFes":
            selected = task_config(plan, "grade.json", gt.GradeConfig)
            progress = gt.daily_progress(selected, **{k: kwargs[k] for k in ("log_root", "now") if k in kwargs})
            runs = progress.planned_runs if selected.live_play else min(1, progress.planned_runs)
            plan = TaskPlan(plan.name, plan.entry, merge_override(copy.deepcopy(plan.pipeline_override),
                gt.batch_pipeline_overrides(load_jsonc(PIPELINE_DIR / "grade.json"), runs)))
        out.append(plan)
    return out, progress


def spend_grant_limits(config):
    """Grade's shared button needs two frame grants per formal live, one per rehearsal; Match 推进 one per live."""
    from agent.core import grade_task as gt
    if supervised_only_item(config) is not None:   # Wing/CardScan/KnowhowScan are not interface tasks (no Grade)
        return {}
    plans = plan_tasks(load_interface(), config)
    limits = {}
    match = next((p for p in plans if p.name == "MatchSkip"), None)
    if (match is not None and MATCH_PLAY_SPEND_ACTION in task_spend_actions(match)
            and MATCH_PLAY_SPEND_ACTION in config.get("allow_spend", ())):
        limits[MATCH_PLAY_SPEND_ACTION] = MATCH_PLAY_GRANT_LIMIT
    if any(p.name == "CollaboFes" for p in plans) and COLLABO_SPEND_ACTION in config.get("allow_spend", ()):
        limits[COLLABO_SPEND_ACTION] = COLLABO_GRANT_LIMIT
    plan = next((p for p in plans if p.name == "GradeFes"), None)
    if plan is None:
        return limits
    selected = task_config(plan, "grade.json", gt.GradeConfig)
    if selected.spend_action not in config.get("allow_spend", ()):
        return limits
    runs = min(selected.runs_per_day, selected.attempt_limit) if selected.live_play else 1
    return {**limits, selected.spend_action: runs * (2 if selected.mode == "grade" else 1)}


def plan_tasks(interface, config):
    """Ordered TaskPlan list: run config ``tasks`` if given, else the preset (``enabled: false`` skipped)."""
    validate_run_config(config)
    preset_name = config.get("preset", DEFAULT_PRESET)
    preset = interface.presets.get(preset_name) if preset_name else None
    preset_items = {item["name"]: item for item in (preset or {}).get("task", [])}
    items = config.get("tasks")
    if items is None:
        require(preset is not None, f"缺少预设: {preset_name}")
        items = preset.get("task", [])
    plans = []
    seen = set()
    for item in items:
        item = {"name": item} if isinstance(item, str) else item
        require(isinstance(item, dict) and set(item) <= {"name", "enabled", "option"}, f"任务项无效: {item}")
        name = item.get("name")
        require(name in interface.tasks, f"未知任务: {name}")
        if item.get("enabled", True) is False:
            continue
        require(name not in seen, f"同一运行不能重复任务：{name}")
        seen.add(name)
        task = interface.tasks[name]
        override = merge_override({}, task.get("pipeline_override"))
        chosen = {**(preset_items.get(name, {}).get("option") or {}), **(item.get("option") or {})}
        for key in RETIRED_OPTIONS.get(name, ()):       # removed from the task: an older saved profile still runs
            chosen.pop(key, None)
        # 2026-10-11: a mangled key ("???" from a non-UTF-8 shell) used to be ignored silently -> default card ran.
        unknown = sorted(set(chosen) - set(task.get("option", []) or []))
        require(not unknown, f"任务 {name} 的未知选项: {unknown}")
        for key in task.get("option", []) or []:
            merge_override(override, option_override(interface, key, chosen.get(key)))
        plans.append(TaskPlan(name, task["entry"], override))
    return plans


def select_tasks(config, names):
    """``--tasks`` order/filter; keep a config item's options (2026-10-22: bare names dropped Grade每日次数=2)."""
    items = {(i if isinstance(i, str) else i.get("name")): i for i in config.get("tasks") or []}
    picked = [items.get(name, name) for name in (n.strip() for n in names.split(",")) if name]
    return [{k: v for k, v in item.items() if k != "enabled"} if isinstance(item, dict) else item for item in picked]


def task_config(plan, pipeline_file, config_class):
    """Merge an Entry's config-node defaults with the selected PI option overrides."""
    doc = load_jsonc(PIPELINE_DIR / pipeline_file)
    params = {}
    for node in ((doc.get(plan.entry) or {}).get("custom_action_param") or {}).get("config_nodes", []):
        attach = dict((doc.get(node) or {}).get("attach") or {})
        attach.update((plan.pipeline_override.get(node) or {}).get("attach") or {})
        params.update(attach)
    return config_class.from_params(params)


def task_spend_action(plan):
    """The selected task's single consuming action; rehearsal spends on its selected difficulty button."""
    if plan.name == "GradeFes":
        from agent.core.grade_task import GradeConfig

        return task_config(plan, "grade.json", GradeConfig).spend_action
    if plan.name == "WeeklyProduce":
        from agent.core.weekly_auto import WeeklyAutoConfig

        return task_config(plan, "weekly_produce.json", WeeklyAutoConfig).spend_action
    return SUPERVISED_TASK_SPEND.get(plan.name)


def task_spend_actions(plan):
    """Every consuming action the selected task may use: its single action, plus ライブ開始 for Match 推进."""
    action = task_spend_action(plan)
    actions = {action} if action is not None else set()
    if plan.name == "MatchSkip":
        from agent.core.match_task import MatchConfig

        if task_config(plan, "match.json", MatchConfig).play == "advance":
            actions.add(MATCH_PLAY_SPEND_ACTION)
    return frozenset(actions)


def item_spend_action(item):
    """Resolve one task item's mode/difficulty without broadening the other task scopes."""
    item = {"name": item} if isinstance(item, str) else item
    if item.get("name") in ("GradeFes", "WeeklyProduce"):
        return task_spend_action(plan_tasks(load_interface(), {"preset": None, "tasks": [item]})[0])
    return SUPERVISED_TASK_SPEND.get(item.get("name"))


def item_spend_actions(item):
    item = {"name": item} if isinstance(item, str) else item
    if item.get("name") == "MatchSkip":
        return task_spend_actions(plan_tasks(load_interface(), {"preset": None, "tasks": [item]})[0])
    action = item_spend_action(item)
    return frozenset() if action is None else frozenset({action})


def selected_spend_actions(items):
    return {action for item in items for action in item_spend_actions(item)}


def spend_action_for_flag(flag, config, interface=None):
    """CLI flags authorize the saved task selection, not all Grade/rehearsal entry buttons."""
    task = {"allow_grade_spend": "GradeFes", "allow_weekly_auto_spend": "WeeklyProduce"}.get(flag)
    if task is not None:
        plans = plan_tasks(interface or load_interface(), config)
        selected = next((p for p in plans if p.name == task), None)
        if selected is not None:
            return task_spend_action(selected)
    return SPEND_FLAGS[flag]


def spend_actions_for_flag(flag, config, interface=None):
    """The actions one CLI flag authorizes: Match 推进 adds ライブ開始 to the Skip's スキップ開始."""
    task = {"allow_grade_spend": "GradeFes", "allow_weekly_auto_spend": "WeeklyProduce",
            "allow_match_spend": "MatchSkip"}.get(flag)
    if task is not None:
        plans = plan_tasks(interface or load_interface(), config)
        selected = next((p for p in plans if p.name == task), None)
        if selected is not None:
            return task_spend_actions(selected)
    return frozenset({SPEND_FLAGS[flag]})


def fes_preflight(plans, allow_spend=(), observed=None, policy=None, grade_progress=None):
    """Shared rights plan for the tasks allowed to consume; never a grant or a guessed resource reading."""
    from agent.core import fes_allocation as fa, grade_task, match_task

    specs = {"MatchSkip": ("match-skip-confirm", "match.json", match_task.MatchConfig),
             "GradeFes": ("grade-tension-confirm", "grade.json", grade_task.GradeConfig)}
    configs = {}
    for plan in plans:
        if plan.name in specs:
            _action, filename, config_class = specs[plan.name]
            if task_spend_action(plan) in allow_spend:
                configs[plan.name] = task_config(plan, filename, config_class)
    if grade_progress is None and "GradeFes" in configs:
        grade_progress = grade_task.daily_progress(configs["GradeFes"])
    allocation = fa.Allocation.from_configs(configs.get("MatchSkip"), configs.get("GradeFes"),
                                           order=[p.name for p in plans], grade_progress=grade_progress)
    return fa.plan(allocation, observed, policy)


# ---- input mode ---------------------------------------------------------------------------------------
def load_policy(path=POLICY_PATH):
    return load_jsonc(Path(path))


def risk_reference_present(policy, root=ROOT):
    from tools.check_project import _risk_decision_exists

    return isinstance(policy, dict) and _risk_decision_exists(root, policy.get("risk_acceptance_reference"))


def resolve_input_mode(config, policy, offline, simulate_input, live=False):
    """Return (mode, reason). Raises when input is requested but not permitted."""
    requested = config.get("input_enabled", live)
    if offline:
        require(not live and not config.get("input_enabled", False),
                "离线回放不接受 --live/input_enabled；模拟点击请用 --simulate-input")
        return (OFFLINE_SIMULATED, "simulated clicks reach only the replay controller") if simulate_input \
            else (READ_ONLY, "offline replay without simulated input")
    require(not simulate_input, "--simulate-input 只用于离线回放")
    if not live:
        require(not requested, "实机输入需要显式 --live；运行配置 input_enabled 本身不开启输入")
        return READ_ONLY, "no --live"
    require(requested is True, "--live 与运行配置 input_enabled: false 矛盾")
    require(isinstance(policy, dict) and policy.get("live_input_enabled") is True,
            "project_policy.json 未允许实时输入；拒绝启动")
    require(risk_reference_present(policy), "project_policy.json 缺少可解析的 risk_acceptance_reference（ADR-011）；拒绝启动")
    tasks = config.get("tasks")
    require(isinstance(tasks, list) and tasks, "--live 需要显式任务列表（--tasks 或运行配置 tasks），不使用预设")
    return LIVE_INPUT, "--live with policy live_input_enabled and the ADR-011 risk acceptance"


def live_families(config):
    from agent.core.webview_controller import PIPELINE_FAMILIES

    families = config.get("families") or sorted(PIPELINE_FAMILIES)
    require(set(families) <= PIPELINE_FAMILIES and len(set(families)) == len(families),
            f"--live 只接受动作表族 {sorted(PIPELINE_FAMILIES)}")
    return sorted(families)


def supervised_startup_config(config, *, live, offline, simulate_input):
    """Validate the per-run exception before a controller/host is connected. Never persist authorization."""
    validate_run_config(config)
    require(live and not offline and not simulate_input, "--supervised-startup 仅用于 --live 监督启动")
    require(config.get("tasks") == ["Startup"], "监督启动必须显式指定唯一任务 tasks: [Startup]")
    require(not config.get("allow_spend"), "监督启动不允许任何 allow_spend")
    max_clicks, startup_cap = config.get("max_clicks"), task_click_cap(config, "Startup")
    require(type(max_clicks) is int and 0 < max_clicks <= SUPERVISED_STARTUP_MAX_CLICKS,
            "监督启动须显式设置 max_clicks 为 1–4")
    require(type(startup_cap) is int and 0 < startup_cap <= SUPERVISED_STARTUP_MAX_CLICKS,
            "监督启动须显式设置 Startup 任务点击上限为 1–4")
    scoped = copy.deepcopy(config)
    if "families" not in scoped:
        scoped["families"] = sorted(SUPERVISED_STARTUP_FAMILIES)
    families = live_families(scoped)
    require("startup" in families and set(families) <= SUPERVISED_STARTUP_FAMILIES,
            "监督启动动作族仅限 startup/popup/nav，且须包含 startup")
    return scoped


def supervised_task_config(config, *, live, offline, simulate_input):
    """One explicitly budgeted task, no preset inheritance or cross-task authority."""
    validate_run_config(config)
    require(live and not offline and not simulate_input, "--supervised-task 仅用于 --live")
    require("preset" in config and config["preset"] is None, "监督单任务须显式 preset: null，不能继承 daily")
    tasks = config.get("tasks")
    require(isinstance(tasks, list) and len(tasks) == 1, "监督单任务须显式指定唯一任务")
    item = {"name": tasks[0]} if isinstance(tasks[0], str) else tasks[0]
    require(isinstance(item, dict) and item.get("enabled", True) is True, "监督任务必须启用")
    name = item.get("name")
    require(name in SUPERVISED_TASK_FAMILIES,
            "监督任务仅限 MissionStatus/ResourceRead/PresentClaim/MissionsClaim/Shop/Training/MatchSkip/GradeFes/Business/WeeklyProduce/CollaboFes/"
            "CardScan/KnowhowScan/Wing")
    if name == KNOWHOW_TASK:
        require(set(item) <= {"name", "enabled", "option"}, f"任务项无效: {item}")
        refusal = knowhow_scan_table_refusal()
        require(refusal is None, f"KnowhowScan 尚不能运行：{refusal}")
        knowhow_scan_options(item.get("option"))   # its own max_inputs bounds the scan; no click cap
    elif name == WING_TASK:
        require(set(item) <= {"name", "enabled", "option"}, f"任务项无效: {item}")
        wing_options(item.get("option"), allow_spend=config.get("allow_spend", []), task_cap=wing_task_cap(config))
    elif name in SUPERVISED_ONLY_TASKS:
        require(set(item) <= {"name", "enabled", "option"}, f"任务项无效: {item}")
        card_scan_options(item.get("option"))
    caps = config.get("task_max_clicks")
    require(caps is None or type(caps) is int or isinstance(caps, dict) and set(caps) <= {name},
            "task_max_clicks 只能写本任务")
    allowed_spend = selected_spend_actions([item])
    if name == WING_TASK:
        allowed_spend = {WING_SPEND_ACTION}
    require(set(config.get("allow_spend", [])) <= allowed_spend, "消费动作必须属于唯一监督任务")
    scoped = copy.deepcopy(config)
    if "families" not in scoped:
        scoped["families"] = sorted(SUPERVISED_TASK_FAMILIES[name])
    require(bool(scoped["families"]) and set(live_families(scoped)) <= SUPERVISED_TASK_FAMILIES[name],
            "动作族必须限定在唯一监督任务内")
    return scoped


# ---- supervised daily sequence (ANN-17, 2026-10-11: the GUI 「开始」 runs the ticked daily tasks for real) -----------
# Several ordinary daily tasks in their given (preset) order in ONE launcher run, each with exactly the scope a
# ``--supervised-task`` run of it would have: its own families (other tasks' click nodes are disabled while it runs)
# and only its own consuming action in the SpendGate while it runs (no click caps since 2026-10-28). The run's ``allow_spend`` lists
# only the spend actions of ticked tasks (tools/ui_schema derives it from each task's own spend option).
SEQUENCE_TASK_FAMILIES = {"Startup": SUPERVISED_STARTUP_FAMILIES,
                          **{name: families for name, families in SUPERVISED_TASK_FAMILIES.items()
                             if name not in SUPERVISED_ONLY_TASKS}}


def sequence_items(config):
    """Enabled task items of a supervised sequence, in order (disabled rows dropped)."""
    tasks = config.get("tasks")
    require(isinstance(tasks, list) and tasks, "监督日常须显式给出任务列表")
    items = []
    for raw in tasks:
        item = {"name": raw} if isinstance(raw, str) else raw
        require(isinstance(item, dict) and set(item) <= {"name", "enabled", "option"}, f"任务项无效: {item}")
        require(type(item.get("enabled", True)) is bool, f"任务启用标记无效: {item.get('name')}")
        if item.get("enabled", True):
            items.append(item)
    return items


def sequence_run_config(config):
    """UI run config (tools/ui_schema.profile_to_run_config) -> supervised sequence config: preset null, only the ticked
    tasks, no click caps. ``allow_spend`` is kept only for spend actions of ticked tasks. Raises before anything runs
    when a ticked task has no supervised daily scope."""
    items = sequence_items(config)
    names = [item["name"] for item in items]
    require(names, "没有勾选的任务")
    unsupported = [name for name in names if name not in SEQUENCE_TASK_FAMILIES]
    require(not unsupported, f"这些任务还不能在实机日常中运行：{unsupported}")
    own_spend = selected_spend_actions(items)
    out = {"preset": None, "input_enabled": True, "tasks": items,
           "allow_spend": sorted(set(config.get("allow_spend", [])) & own_spend)}
    if "task_timeout_s" in config:
        out["task_timeout_s"] = config["task_timeout_s"]
    return out


def supervised_sequence_config(config, *, live, offline, simulate_input):
    """Validate a supervised daily sequence: explicit tasks (no preset inheritance), each with a supervised daily scope,
    spend only for the listed tasks' own actions."""
    validate_run_config(config)
    require(live and not offline and not simulate_input, "监督日常仅用于 --live")
    require("preset" in config and config["preset"] is None, "监督日常须显式 preset: null，不能继承 daily")
    items = sequence_items(config)
    names = [item["name"] for item in items]
    require(names and len(set(names)) == len(names), "监督日常须列出不重复的启用任务")
    require(all(name in SEQUENCE_TASK_FAMILIES for name in names),
            f"监督日常仅限 {sorted(SEQUENCE_TASK_FAMILIES)}")
    caps = config.get("task_max_clicks")
    require(caps is None or type(caps) is int or isinstance(caps, dict) and set(caps) <= set(names),
            "task_max_clicks 只能写本次的任务")
    own_spend = selected_spend_actions(items)
    require(set(config.get("allow_spend", [])) <= own_spend, "消费动作必须属于本次勾选的任务")
    scoped = copy.deepcopy(config)
    union = sorted(set().union(*(SEQUENCE_TASK_FAMILIES[n] for n in names)))
    scoped.setdefault("families", union)
    require(set(live_families(scoped)) == set(union), "监督日常动作族须等于所选任务的动作族")
    return scoped


def sequence_task_override(name, families, allow_spend, plan=None):
    """Per-task scope inside a sequence; guarded preview SKIP is ordinary non-consuming navigation."""
    from agent.core.pipeline_actions import default_table

    table = default_table()
    own = SEQUENCE_TASK_FAMILIES[name]
    own_spend = task_spend_actions(plan) if plan is not None else {SUPERVISED_TASK_SPEND.get(name)}
    override = supervised_startup_override()
    override.pop("SC.Startup.Entry")   # Startup in a sequence also accepts an already-open home screen
    for node, action in click_node_actions().items():
        spec = table.get(action)
        if node.startswith(SUPERVISED_ONLY_NODE_PREFIXES) or spec.family not in families:
            continue
        if spec.family not in own or (spec.consuming and (action not in own_spend or action not in allow_spend)):
            override[node] = {"enabled": False}
    return override


def card_scan_options(option=None):
    """Validated ``card_list_scan.ScanOptions``: the shipped SC.CardScan.Config defaults overlaid by the task item's
    ``option`` object (tabs / max_scrolls_per_tab / max_frames / max_holds / required_order), scroll budget <=80."""
    from agent.core import card_list_scan as cs

    _, filename, node = SUPERVISED_ONLY_TASKS["CardScan"]
    require(option is None or isinstance(option, dict), "CardScan option 必须为扫描选项对象")
    defaults = dict((load_jsonc(PIPELINE_DIR / filename).get(node) or {}).get("attach") or {})
    try:
        options = cs.ScanOptions.from_params({**defaults, **(option or {})})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CardScan 选项无效: {exc}") from None
    require(options.max_scrolls_per_tab <= CARD_SCAN_MAX_SCROLLS, f"CardScan max_scrolls_per_tab 须 ≤{CARD_SCAN_MAX_SCROLLS}")
    return options


def knowhow_scan_table_refusal(table=None):
    """None when every KnowhowScan action id is an enabled entry of the action table, else the reason (the staged
    knowhow-scan entries were not switched in yet: tools/knowhow_scan_build.py --apply)."""
    from agent.core.pipeline_actions import default_table

    table = default_table() if table is None else table
    known = {a.id: a for a in table.actions}
    wanted = knowhow_task_actions(lock=True)
    missing = sorted(a for a in wanted if a not in known)
    if missing:
        return "动作表缺少 knowhow-scan 条目（待切换 dev_assets/private/pending-20261012-knowhow）：" + ",".join(missing[:6])
    disabled = sorted(a for a in wanted if not known[a].enabled)
    if disabled:
        return "动作表条目未启用：" + ",".join(disabled[:6])
    return None


def knowhow_scan_options(option=None, timeout_s=None):
    """Validated KnowhowScan item option -> {mode, attach (the SC.KnowhowScan.Config attach), cap (click cap)}.
    ``mode``: calibrate (knowhow_calibrate.CalibrationOptions under ``calibration``) or scan (knowhow_scan.ScanOptions,
    ``resume_record``: a private record name or "latest"). Defaults come from the shipped SC.KnowhowScan.Config.
    ``timeout_s`` (the task timeout): a scan without its own ``max_seconds`` winds down KNOWHOW_WIND_DOWN_S before it."""
    from agent.core import knowhow_calibrate as kc
    from agent.core import knowhow_filter as kf
    from agent.core import knowhow_scan as ks

    _, filename, node = SUPERVISED_ONLY_TASKS[KNOWHOW_TASK]
    require(option is None or isinstance(option, dict), "KnowhowScan option 必须为选项对象")
    defaults = dict((load_jsonc(PIPELINE_DIR / filename).get(node) or {}).get("attach") or {})
    merged = {**defaults, **(option or {})}
    if isinstance((option or {}).get("calibration"), dict):
        merged["calibration"] = {**(defaults.get("calibration") or {}), **option["calibration"]}
    if isinstance((option or {}).get("filter_calibration"), dict):
        merged["filter_calibration"] = {**(defaults.get("filter_calibration") or {}), **option["filter_calibration"]}
    mode = merged.pop("mode", "calibrate")
    calibration = merged.pop("calibration", None) or {}
    filter_calibration = merged.pop("filter_calibration", None) or {}
    resume = merged.pop("resume_record", None)
    scan_id = ks.validate_scan_id(merged.pop("scan_id", None))
    require(mode in ("calibrate", "scan", "filter_calibrate"), "KnowhowScan mode 须为 calibrate、scan 或 filter_calibrate")
    if merged.get("newest") in ("auto", "pending"):
        # Top-up after a Produce (knowhow_scan.topup_plan): the scan reads 所持数 and decides how many of the newest
        # books to read (baseline count, else the Produce-end pending books); it never scans the whole library.
        merged["newest"], merged["topup"] = 0, "only"
    elif mode == "scan" and resume is None and not merged.get("newest") and "topup" not in (option or {}):
        merged["topup"] = "prefer"      # a fresh library scan reads only the new books when 所持数 says so
    if mode == "scan" and (merged.get("topup") == "only" or (isinstance(merged.get("newest"), int) and merged["newest"] > 0)):
        require(resume is None and scan_id is None, "读新书不续扫（resume_record / scan_id 须为空）")
        if "max_inputs" not in (option or {}):
            merged["max_inputs"] = (KNOWHOW_NEWEST_INPUTS[0] + KNOWHOW_NEWEST_INPUTS[1] * (merged["newest"] or ks.NEWEST_MAX)
                                    + (KNOWHOW_KEEP_INPUTS * ks.NEWEST_MAX if merged.get("keep_lock") is True else 0))
    if mode == "scan" and timeout_s is not None and not merged.get("max_seconds"):
        merged["max_seconds"] = max(60, int(timeout_s) - KNOWHOW_WIND_DOWN_S)
    require(resume is None or (isinstance(resume, str) and (resume == "latest" or (
        Path(resume).name == resume and resume.startswith("knowhow-scan-") and resume.endswith(".json")))),
        "resume_record 须为 latest 或 dev_assets/private/knowhow-scan/ 下的记录文件名")
    try:
        scan = ks.ScanOptions.from_params(merged)
        calib = kc.CalibrationOptions.from_params(calibration)
        fcalib = kf.FilterCalibrationOptions.from_params(filter_calibration)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"KnowhowScan 选项无效: {exc}") from None
    if mode == "calibrate":
        cap = calib.max_inputs()
    elif mode == "filter_calibrate":
        cap = fcalib.max_inputs()
    else:
        cap = scan.max_inputs + KNOWHOW_NAV_INPUTS
    require(cap <= KNOWHOW_TASK_CAP, f"KnowhowScan 本次输入上限 {cap} 超过 {KNOWHOW_TASK_CAP}，请减小选项")
    attach = {"mode": mode, **dataclasses.asdict(scan), "resume_record": resume if mode == "scan" else None,
              "calibration": dataclasses.asdict(calib), "filter_calibration": dataclasses.asdict(fcalib)}
    if mode == "scan" and scan_id:
        attach["scan_id"] = scan_id
    return {"mode": mode, "attach": attach, "cap": cap}


def supervised_only_item(config):
    """The single task item when it names a supervised-only task (CardScan / KnowhowScan / Wing), else None."""
    tasks = config.get("tasks")
    if not (isinstance(tasks, list) and len(tasks) == 1):
        return None
    item = {"name": tasks[0]} if isinstance(tasks[0], str) else tasks[0]
    return item if isinstance(item, dict) and item.get("name") in SUPERVISED_ONLY_TASKS else None


def supervised_only_plan(item, config=None):
    """TaskPlan of a supervised-only task: its entry with the validated options written into its config node."""
    name = item["name"]
    if name == WING_TASK:
        config = config or {}
        return wing_plan(item, allow_spend=config.get("allow_spend", []), task_cap=wing_task_cap(config))
    entry, _, node = SUPERVISED_ONLY_TASKS[name]
    if name == KNOWHOW_TASK:
        timeout = (config or {}).get("task_timeout_s", DEFAULT_TASK_TIMEOUT_S)
        return TaskPlan(name, entry, {node: {"attach": knowhow_scan_options(item.get("option"), timeout)["attach"]}})
    options = card_scan_options(item.get("option"))
    attach = {"tabs": list(options.tabs), "max_scrolls_per_tab": options.max_scrolls_per_tab,
              "max_frames": options.max_frames, "max_holds": options.max_holds,
              "required_order": None if options.required_order is None else list(options.required_order)}
    return TaskPlan(name, entry, {node: {"attach": attach}})


def supervised_startup_override():
    """Require the native title on entry, then prove the click result and a clean home in that order. The right SKIP
    of the 新ガシャ開催/新アイドル登場 cut-in is ordinary guarded navigation, as in a sequence."""
    return {"SC.Startup.Entry": {"recognition": "And", "all_of": [
                "SC.Startup.Anchor.TouchToStart", "SC.Startup.Anchor.Publisher"],
                "next": ["SC.Startup.TouchToStart"], "timeout": 1500,
                # not the title: a full-screen gacha announcement gets its own reason (no click), else the unknown one
                "on_error": ["SC.Common.Stop.GachaAnnouncement", "SC.Common.FailTask"]},
            "SC.Startup.Started": {"next": ["SC.Common.GoHome"]}}


def click_node_actions(pipeline_dir=PIPELINE_DIR, table=None):
    """Validate every shipped Click; return only bindings whose catalog action is enabled."""
    from agent.core.pipeline_actions import default_table, is_drag_node, is_match_row_target

    table = default_table() if table is None else table
    bound, nodes = table.node_actions(include_disabled=True), {}
    for path in sorted(Path(pipeline_dir).rglob("*.json")):
        for name, node in load_jsonc(path).items():
            if isinstance(node, dict) and node.get("action") == "Swipe":
                require(node.get("enabled") is False, f"拖动节点必须默认禁用: {name}")
                require(name in bound and is_drag_node(node, table.get(bound[name])), f"拖动节点须绑定可拖动动作: {name}")
                if table.get(bound[name]).enabled:
                    nodes[name] = bound[name]
            if isinstance(node, dict) and node.get("action") == "Click":
                require(node.get("enabled") is False, f"点击节点必须默认禁用: {name}")
                require(name in bound, f"点击节点未绑定动作表: {name}")
                require(table.get(bound[name]).drag_rect is None, f"可拖动动作不能点击: {name}")
                require(node.get("target") == list(table.get(bound[name]).rect)
                        or is_match_row_target(name, node, bound[name]), f"点击节点 target 必须等于动作框: {name}")
                if table.get(bound[name]).enabled:
                    nodes[name] = bound[name]
    require(set(nodes) == set(table.node_actions()), "动作表列出的节点与 Pipeline 点击节点不一致")
    return dict(sorted(nodes.items()))


def click_nodes(pipeline_dir=PIPELINE_DIR):
    return list(click_node_actions(pipeline_dir))


def mode_override(mode, families=None, allow_spend=(), actions=None, node_prefixes=None):
    """Click nodes to enable. A consuming node is enabled only when its action is in ``allow_spend``; its click
    still needs a frame-bound SpendGate grant from the task's custom recognition. ``actions``/``node_prefixes`` (a
    supervised task's exact allowlist) narrow the nodes further; without them, supervised-only nodes stay disabled."""
    from agent.core.pipeline_actions import default_table

    table = default_table()
    require((actions is None) == (node_prefixes is None), "动作白名单与节点前缀须同时给出")

    def admitted(name, action):
        if actions is None:
            return not name.startswith(SUPERVISED_ONLY_NODE_PREFIXES)
        return action in actions and name.startswith(tuple(node_prefixes))

    if mode == OFFLINE_SIMULATED:
        override = {name: {"timeout": value} for name, value in OFFLINE_TIMEOUTS.items()}
        for name, action in click_node_actions().items():
            if admitted(name, action) and (not table.get(action).consuming or action in allow_spend):
                override[name] = {"enabled": True, "timeout": OFFLINE_CLICK_TIMEOUT}
        return override
    if mode == LIVE_INPUT:
        require(families, "实机模式需要动作族")
        return {name: {"enabled": True} for name, action in click_node_actions().items()
                if admitted(name, action) and table.get(action).family in families
                and (not table.get(action).consuming or action in allow_spend)}
    require(mode == READ_ONLY, f"未知输入模式: {mode}")
    return copy.deepcopy(READ_ONLY_OVERRIDE)


# ---- offline replay controller ------------------------------------------------------------------------
STAY = 10 ** 9


class ReplayScenario:
    """Pure frame stream: ``advance="click"`` switches on an accepted click, int N after N captures.

    Attached Maa replays use the live recognition-frame binder to resolve overlapping action rects by node.
    Standalone probes still require an unambiguous point. A consuming action is accepted only with a one-shot
    grant issued (``ReplaySpendAuthority``) for the frame currently shown, exactly like the live SpendGate."""

    def __init__(self, images, advances, allow_input, table=None, allow_spend=(), run_control=None, families=None,
                 actions=None):
        from agent.core.frame_permit import FramePermitStore
        from agent.core.pipeline_actions import default_table, _disjoint

        self.table = default_table() if table is None else table
        self.holds = []                  # (action, hold_ms) staged by ReplaySpendAuthority.stage_hold
        self.families = None if families is None else frozenset(families)
        self.action_allowlist = None if actions is None else frozenset(actions)   # same rule as the live gate
        enabled = [action for action in self.table.actions if action.enabled
                   and (self.families is None or action.family in self.families)
                   and (self.action_allowlist is None or action.id in self.action_allowlist)]
        self.node_binding_required = any(not _disjoint(action.rect, other.rect)
                                         for index, action in enumerate(enabled) for other in enabled[:index])
        self.allow_spend = frozenset(allow_spend)
        self.run_control = run_control
        self.frame_binding = self.controller_worker_thread = self._frame = None
        self.frame_guard_enabled = True
        # Replay time is a capture counter, independent of OCR speed; there is no host clock or transport.
        self.frame_permits = FramePermitStore(enabled=True, table=self.table,
                                             current_frame=lambda: self._frame, clock=lambda: float(self.captures))
        self.reset_frames(images, advances, allow_input)

    def reset_frames(self, images, advances, allow_input):
        require(images and len(images) == len(advances), "回放帧为空或与推进方式数量不符")
        self.stop_input()
        self.images, self.advances, self.allow_input = list(images), list(advances), bool(allow_input)
        self.index = self.captures_here = self.captures = 0
        self.clicks, self.actions, self.refused, self.other_calls = [], [], [], 0
        self.drags = []            # (action, start, end, duration) of every accepted drag
        self.grant = None          # (action, frame index) issued by the authority, single use
        self.spends = []           # (action, result) for every consuming click attempt
        self._digests = {}
        self.guard_events = []

    def _log(self, record):
        self.guard_events.append(record)

    def stop_input(self):
        self.grant = self._frame = None
        self.frame_permits.invalidate()
        if self.frame_binding is not None:
            self.frame_binding.reset()

    def digest(self, index):
        import hashlib
        if index not in self._digests:
            self._digests[index] = hashlib.sha256(self.images[index].tobytes()).hexdigest()
        return self._digests[index]

    def screencap(self, source="direct_call"):
        from agent.core.frame_permit import FrameCapture

        self._frame = None
        self.frame_permits.invalidate("capture")
        cookie = self.frame_binding.capture_started(source) if self.frame_binding is not None else None
        self.captures += 1
        advance = self.advances[self.index]
        if isinstance(advance, int) and self.captures_here >= advance and self.index + 1 < len(self.images):
            self.index, self.captures_here = self.index + 1, 0
        self.captures_here += 1
        image = self.images[self.index].copy()
        digest = self.digest(self.index)
        self._frame = FrameCapture(digest, float(self.captures), tuple(image.shape), digest,
                                   self.captures, self.captures, self.captures)
        if self.frame_binding is not None:
            self.frame_binding.capture_finished(cookie, self._frame)
        return image

    def click(self, x, y):
        return self._input(x, y)

    def swipe(self, x1, y1, x2, y2, duration):
        """SC.PipelineDrag.v1 replay: a drag action only, start inside its rect, end inside drag_rect, bounded time."""
        from agent.core.webview_controller import DRAG_MAX_MS, DRAG_MIN_MS

        if not (type(duration) is int and DRAG_MIN_MS <= duration <= DRAG_MAX_MS):
            self.refused.append((int(x1), int(y1)))
            return False
        return self._input(x1, y1, drag=((int(x2), int(y2)), duration))

    def _input(self, x, y, drag=None):
        if self.run_control is not None and self.run_control.poll():
            self.stop_input()
            self.refused.append((int(x), int(y)))
            return False
        permit = self.frame_permits.take() if self.frame_binding is not None else None
        if self.frame_binding is not None:
            if (permit is None or self.frame_permits.refusal(permit, permit.action) is not None
                    or not self.frame_binding.allows_click(permit)):
                self.refused.append((int(x), int(y)))
                return False
        hits = self.table.at((int(x), int(y)), families=self.families,
                             node=permit.node_name if permit is not None else None)
        if self.action_allowlist is not None:
            hits = [action for action in hits if action.id in self.action_allowlist]
        if not self.allow_input or len(hits) != 1:
            self.refused.append((int(x), int(y)))
            return False
        if (hits[0].drag_rect is not None) != (drag is not None) or (
                drag is not None and (hits[0].consuming or not hits[0].drag_contains(drag[0]))):
            self.refused.append((int(x), int(y)))   # a drag action is never clicked; a click is never dragged
            return False
        if hits[0].consuming:
            grant, self.grant = self.grant, None
            if hits[0].id not in self.allow_spend or grant != (hits[0].id, self.index):
                self.spends.append((hits[0].id, "denied"))
                self.refused.append((int(x), int(y)))
                return False
        if self.run_control is not None:
            self.run_control.input_event({"kind": "click", "action": hits[0].id, "result": "intent",
                                          "spend": hits[0].consuming})
            if self.run_control.poll():
                self.stop_input()
                self.refused.append((int(x), int(y)))
                return False
        if self.frame_binding is not None and (
                self.frame_permits.refusal(permit, hits[0].id) is not None
                or not self.frame_binding.allows_click(permit)):
            self.refused.append((int(x), int(y)))
            return False
        if hits[0].consuming:
            self.spends.append((hits[0].id, "sent"))
        self.clicks.append((int(x), int(y)))
        self.actions.append(hits[0].id)
        if drag is not None:
            self.drags.append((hits[0].id, (int(x), int(y)), drag[0], drag[1]))
        if self.advances[self.index] == "click" and self.index + 1 < len(self.images):
            self.index, self.captures_here = self.index + 1, 0
        if self.run_control is not None:
            self.run_control.input_event({"kind": "click", "action": hits[0].id, "result": "sent",
                                          "spend": hits[0].consuming})
        return True

    def deny(self, *args):
        self.other_calls += 1
        return False


class ReplaySpendAuthority:
    """Offline counterpart of ``webview_controller.LiveSpendAuthority``: a grant binds to the replayed frame."""

    def __init__(self, scenario):
        self.scenario = scenario

    def can_spend(self, action):
        return (not (self.scenario.run_control is not None and self.scenario.run_control.poll())
                and self.scenario.allow_input and action in self.scenario.allow_spend)

    def authorize(self, action, image, approver):
        import hashlib
        scenario = self.scenario
        if not self.can_spend(action):
            return "spend_action_not_allowed"
        if not (isinstance(approver, str) and approver.startswith("SC.")):
            return "approver_invalid"
        if hashlib.sha256(image.tobytes()).hexdigest() != scenario.digest(scenario.index):
            return "analysed_image_is_not_the_latest_frame"
        scenario.grant = (action, scenario.index)
        if scenario.run_control is not None and scenario.run_control.poll():
            scenario.grant = None
            return "run_stopped"
        return None

    def last_outcome(self, action):
        for spent, result in reversed(self.scenario.spends):
            if spent == action:
                return result
        return "denied"

    def stage_hold(self, action, hold_ms):
        """Replay of the host hold v2 for the say "Halo" give-up only (scenario.holds); the Match ＋ keeps its replayed
        plain taps (no hold host, as before)."""
        from agent.core.webview_controller import HOLD_MAX_MS, HOLD_MIN_MS

        if action != "wing-giveup-hold":
            return "hold_host_unavailable"
        if not (type(hold_ms) is int and HOLD_MIN_MS <= hold_ms <= HOLD_MAX_MS):
            return "hold_not_allowed"
        self.scenario.holds.append((action, hold_ms))
        return None

    def click_mark(self):
        return len(self.scenario.clicks)

    def clicks_since(self, mark):
        """``not_sent`` unless the scenario accepted a click after ``mark`` (refused points never advance a frame)."""
        return "sent" if len(self.scenario.clicks) > mark else "not_sent"


def create_replay_controller(scenario):
    from maa.controller import CustomController

    class ReplayController(CustomController):
        def __init__(self):
            self.scenario = scenario
            super().__init__()

        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return "SC.Offline.TaskLauncher"
        def screencap(self):
            self.scenario.controller_worker_thread = threading.get_ident()
            return self.scenario.screencap(source="maa_custom_controller")
        def click(self, x, y): return self.scenario.click(x, y)
        def swipe(self, x1, y1, x2, y2, duration): return self.scenario.swipe(x1, y1, x2, y2, duration)

        def deny(self, *args): return self.scenario.deny(*args)
        start_app = stop_app = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny

        def shell(self, *args):
            self.scenario.other_calls += 1
            return None

    return ReplayController()


def private_png(raw):
    path = Path(raw)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    require(path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".png" and path.is_file(),
            f"回放帧须为 dev_assets/private 下的 PNG: {raw}")
    return path


def parse_offline_frames(spec):
    """``dir`` (sorted *.png), ``a.png,b.png`` or a JSON file {"steps": [[png, "click"|N], ...]}.
    Directory and list frames advance on a click; the last frame stays."""
    path = Path(spec)
    path = path if path.is_absolute() else ROOT / path
    if path.is_dir():
        frames = sorted(path.glob("*.png"))
        require(frames, f"目录中没有 PNG: {spec}")
        return [(private_png(f), "click") for f in frames]
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        steps = data.get("steps") if isinstance(data, dict) else None
        require(isinstance(steps, list) and steps, "回放清单需要非空 steps")
        out = []
        for step in steps:
            require(isinstance(step, list) and len(step) == 2, f"回放步骤无效: {step}")
            image, advance = step
            require(advance == "click" or (type(advance) is int and advance > 0), f"推进方式无效: {advance}")
            out.append((private_png(image), advance))
        return out
    return [(private_png(item.strip()), "click") for item in spec.split(",") if item.strip()]


def load_frames(steps):
    from tools.replay_screenshots import read_png

    images = []
    for image_path, _ in steps:
        image = read_png(image_path)
        require(tuple(image.shape[:2]) == (CANVAS[1], CANVAS[0]), f"回放帧必须为 1280x720: {image_path}")
        images.append(image)
    return images, [advance for _, advance in steps]


# ---- execution ----------------------------------------------------------------------------------------
def settle_stop(tasker, job, *, clock, sleep, timeout_s, input_idle=None):
    """One stop request, then bounded polling. Native terminal alone is insufficient."""
    deadline = clock() + timeout_s
    try:
        stop_job = tasker.post_stop()
        if stop_job.job_id <= 0:
            return False
        while clock() < deadline:
            if (stop_job.done and (job is None or job.done) and not tasker.running and not tasker.stopping
                    and (input_idle is None or input_idle())):
                return True
            sleep(0.02)
    except Exception:
        pass
    return False


def execute_plan(plans, tasker, sink=None, base_override=None, timeout_s=DEFAULT_TASK_TIMEOUT_S,
                 clock=time.monotonic, sleep=time.sleep, observer=None, before_task=None, after_task=None,
                 control=None, on_stop=None, input_idle=None, stop_timeout_s=5.0, failure_snapshot=False,
                 task_timeouts=None):
    """Ordinary failures continue; a latched run stop skips every remaining task.
    ``before_task(plan)`` may return a reason to skip the task (e.g. the session length cap).
    ``after_task(plan)`` runs once the job ended, before the reports are drained (flushes unreported task runs).
    ``task_timeouts``: {task name: seconds} replacing ``timeout_s`` for that task (live CollaboFes waits).
    ``failure_snapshot``: after a failed/timed-out task whose job has ended (and no run stop), one read-only
    ``SC.Diagnostics.FailureSnapshot`` (frame + page-guard near misses) is attached to the task entry."""
    results = []
    if sink is not None:
        sink.drain()
    cleanup_done = False
    takeover_pending = False

    def stopped():
        nonlocal cleanup_done
        if not takeover_pending and (control is None or not control.poll()):
            return False
        if not cleanup_done:
            cleanup_done = True
            if on_stop is not None:
                try:
                    on_stop()  # must not wait for a callback/native job
                except Exception:
                    if control is not None:
                        control.stop_verified = False
        return True

    for plan in plans:
        if control is not None:
            control.task = plan.name
        skip = (control.reason if control is not None else "manual_takeover") if stopped() else (before_task(plan) if before_task else None)
        if stopped():
            skip = control.reason if control is not None else "manual_takeover"
        if skip:
            results.append({"name": plan.name, "entry": plan.entry, "status": "skipped", "node_trace": [],
                            "reports": [], "fail_reasons": [skip], "elapsed_seconds": 0.0})
            if control is not None:
                control.emit("task.finished", task=plan.name, status="skipped", reason="run_stopped" if control.reason else "task_not_started")
            continue
        override = merge_override(copy.deepcopy(base_override or {}), plan.pipeline_override)
        started = clock()
        limit_s = (task_timeouts or {}).get(plan.name, timeout_s)
        before = observer() if observer else None
        status, trace, error, job, stop_sent = "failed", [], None, None, False

        def stop_current():
            nonlocal stop_sent
            if job is not None and not stop_sent:
                stop_sent = True
                control.stop_verified = settle_stop(tasker, job, clock=clock, sleep=sleep,
                                                    timeout_s=stop_timeout_s, input_idle=input_idle)
            if on_stop is not None:
                try:
                    on_stop()  # nonblocking cleanup may now acquire the released input lock
                except Exception:
                    control.stop_verified = False

        try:
            if control is not None:
                control.emit("task.started", task=plan.name, entry=plan.entry)
            if stopped():
                status = "skipped"
            else:
                job = tasker.post_task(plan.entry, override)
            while job is not None:
                if stopped():
                    status = "interrupted"
                    stop_current()
                    break
                if job.done:
                    break
                if clock() - started > limit_s:
                    if control is None:
                        tasker.post_stop().wait()  # legacy callers retain their contract
                    else:
                        stop_sent = True
                        verified = settle_stop(tasker, job, clock=clock, sleep=sleep,
                                               timeout_s=stop_timeout_s, input_idle=input_idle)
                        if not verified:
                            control.request_stop("stop_failed")
                            control.stop_verified = False
                            stopped()
                    status = "timeout"
                    error = (f"任务超过 {limit_s} s，停止未确认，不重试且跳过剩余任务"
                             if control is not None and control.stop_verified is False else
                             f"任务超过 {limit_s} s，已确认停止且不重试")
                    break
                sleep(0.02)
            if job is not None and status not in ("timeout", "interrupted"):
                detail = job.get()
                trace = [node.name for node in detail.nodes] if detail else []
                status = "succeeded" if job.succeeded else "failed"
        except Exception as exc:  # isolate: record and continue with the next task
            status, error = "error", str(exc)[:300]
            if control is not None and job is not None:
                control.request_stop("runner_error")
        except KeyboardInterrupt:
            if control is None:
                raise
            control.request_stop("runner_error")
            status, error = "interrupted", "用户中断运行，不重试"
        if stopped() and job is not None and status not in ("interrupted", "timeout"):
            status = "interrupted"
        if stopped():
            stop_current()
        quiescent = control is None or control.stop_verified is not False
        if after_task is not None and quiescent:
            try:
                after_task(plan)
            except Exception:  # reporting only; never changes the task status
                pass
        if stopped():
            stop_current()
        quiescent = control is None or control.stop_verified is not False
        reports = sink.drain() if sink is not None else []
        if quiescent and any(r.get("takeover_required") is True for r in reports):
            status = "takeover"
            takeover_pending = True
            if control is not None:
                control.request_stop("manual_takeover")
        entry = {"name": plan.name, "entry": plan.entry, "status": status, "node_trace": trace[-80:],
                 "reports": reports, "fail_reasons": [r.get("reason") for r in reports if r.get("kind") == "fail"],
                 "elapsed_seconds": round(clock() - started, 3)}
        for r in reports:   # a CardScan stop / swallowed exception must never leave fail_reasons empty
            if r.get("kind") == "card_scan" and str(r.get("outcome", "")).startswith("stopped:"):
                entry["fail_reasons"].append(f"card_scan_{r['outcome']}" + (f":{r['session_outcome']}" if r.get("session_outcome") else ""))
                entry["fail_reasons"].extend(f"card_scan_error:{e}" for e in r.get("errors") or [])
            if r.get("kind") == "knowhow_scan" and str(r.get("outcome", "")).startswith(("stopped:", "config_invalid")):
                entry["fail_reasons"].append(f"knowhow_scan_{r['outcome']}"
                                             + (f":{r['previous']}" if r.get("previous") else ""))
        if error:
            entry["error"] = error
        if failure_snapshot and status in ("failed", "timeout", "error") and quiescent and not stopped():
            snapshot = run_failure_snapshot(tasker, sink, plan.name, clock=clock, sleep=sleep)
            if snapshot is not None:
                entry["failure_snapshot"] = snapshot
                entry["fail_reasons"].extend(guard_summary_lines(snapshot))
                hint = layout_hint(snapshot)
                if hint is not None:
                    entry["layout_hint"] = hint  # the host shows 「画面与已知布局不一致（page）」 for this task
        if observer:
            entry.update(observer(before))
        if control is not None:
            if control.reason:
                entry["fail_reasons"].append(control.reason)
            if not quiescent:
                entry["reports_incomplete"] = True
            control.emit("task.finished", task=plan.name, status=status, reports_complete=quiescent)
            if stopped():
                stop_current()
                if control.stop_verified is False:
                    entry["reports_incomplete"] = True
        results.append(entry)
    return results


FAILURE_SNAPSHOT_TIMEOUT_S = 40.0


def guard_summary_lines(snapshot):
    from agent.core import guard_diagnosis as gd
    return gd.summary_lines(snapshot) if snapshot.get("ok") else []


def layout_hint(snapshot):
    from agent.core import guard_diagnosis as gd
    return gd.layout_hint(snapshot) if snapshot.get("ok") else None


def run_failure_snapshot(tasker, sink, task_name, clock=time.monotonic, sleep=time.sleep,
                         timeout_s=FAILURE_SNAPSHOT_TIMEOUT_S):
    """One read-only diagnosis job after a failed task; its report, or None (never fails the run)."""
    try:
        job = tasker.post_task("SC.Diagnostics.FailureSnapshot",
                               {"SC.Diagnostics.FailureSnapshot": {"custom_action_param": {"task": task_name}}})
        started = clock()
        while not job.done:
            if clock() - started > timeout_s:
                tasker.post_stop().wait()
                break
            sleep(0.02)
        reports = sink.drain() if sink is not None else []
        found = [r for r in reports if r.get("kind") == "failure_snapshot"]
        return found[-1] if found else None
    except Exception:
        return None


def default_summary_path(now):
    return RUN_LOG_DIR / ("tasks-" + now.strftime("%Y%m%dT%H%M%SZ") + ".json")


OFFLINE_REPLAY_DIR = PRIVATE / "offline-replay"


def offline_replay_root(now):
    """Fresh isolated root of one offline replay (path only; every writer creates it lazily)."""
    return OFFLINE_REPLAY_DIR / (now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])


def offline_replay_options(options, shop_options, root):
    """Task options of an offline replay: every ledger, sample, journal and scan store the tasks would write defaults
    to ``root`` instead of the live files (``dev_assets/private/run_logs`` & co.), which the live planner reads as
    real attempts. Explicit entries of ``options`` / ``shop_options`` win (tests pass their own temp roots)."""
    root = Path(root)
    logs, samples = root / "run_logs", root / "samples"
    defaults = {
        **{kind: {"log_root": logs, "sample_root": samples} for kind in ("grade", "match", "training", "business")},
        "shop": {"log_root": logs},
        "missions": {"sample_root": samples, "journal_path": root / "daily-login.sqlite3",
                     "bulk_ledger_path": root / "bulk-ledger.json"},
        "present": {"ledger_path": root / "present-ledger.json"},
        "card_scan": {"run_dir": root / "card-scan", "write_import": False},
        "knowhow_scan": {"run_dir": root / "knowhow-scan", "write_inventory": False},
        "weekly_auto": {"log_root": logs},
        "collabo": {"log_root": logs, "ledger_path": root / "collabo-levels.json"},
    }
    merged = dict(options or {})
    for kind, values in defaults.items():
        given = shop_options if kind == "shop" and shop_options else merged.get(kind, {})
        merged[kind] = {**values, **given}
    return merged


def write_summary(summary, path):
    path = Path(path).resolve()
    require(path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".json", "运行摘要须写入 dev_assets/private")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def task_runtimes(sink, authority=None, options=None, *, present_input=False, missions_input=False,
                  wing_input=False, wing_spend=False, wing_reroll=False, wing_ledger=None, wing_settings=None):
    """One runtime per custom task for this launcher process; ``options`` = {kind: runtime kwargs} (tests).
    ``wing_input`` is set only for the supervised Wing entry in live or simulated-offline mode."""
    from agent.core import business_custom, grade_custom, match_custom, shop_custom, training_custom
    from agent.core.present_actions import PresentRuntime
    from agent.core.mission_claim_actions import MissionRuntime
    from agent.core.wing_custom import WingSession

    options = options or {}
    wing_kw = {"allow_reward_reroll": wing_input and wing_reroll, "ledger": wing_ledger, "settings": wing_settings,
               **options.get("wing", {})}
    return {"wing": WingSession(sink, authority, input_enabled=wing_input, allow_start_spend=wing_input and wing_spend,
                                **wing_kw),
            "shop": shop_custom.ShopRuntime(sink, authority, **options.get("shop", {})),
            "training": training_custom.TrainingRuntime(sink, authority, **options.get("training", {})),
            "grade": grade_custom.GradeRuntime(sink, authority, **options.get("grade", {})),
            "match": match_custom.MatchRuntime(sink, authority, **options.get("match", {})),
            "business": business_custom.BusinessRuntime(sink, authority, **options.get("business", {})),
            "present": PresentRuntime(sink, input_enabled=present_input, **options.get("present", {})),
            "missions": MissionRuntime(sink, input_enabled=missions_input,
                                       **{"authority": authority, **options.get("missions", {})}),
            "card_scan": _card_scan_runtime(sink, options.get("card_scan", {})),
            "knowhow_scan": _knowhow_scan_runtime(sink, options.get("knowhow_scan", {})),
            "weekly_auto": _weekly_auto_runtime(sink, authority, options.get("weekly_auto", {})),
            "collabo": _collabo_runtime(sink, authority, options.get("collabo", {}))}


def collabo_task_timeouts(config, mode):
    """Live CollaboFes may wait for 満員 / Pass recovery: its task timeout is at least COLLABO_TASK_TIMEOUT_S (the play
    ends its waits in time). Offline replays and read-only runs never wait."""
    if mode != LIVE_INPUT:
        return {}
    return {"CollaboFes": max(float(config.get("task_timeout_s", DEFAULT_TASK_TIMEOUT_S)), COLLABO_TASK_TIMEOUT_S)}


def _collabo_runtime(sink, authority, options):
    from agent.core.collabo_custom import CollaboRuntime

    return CollaboRuntime(sink, authority, **options)


def _weekly_auto_runtime(sink, authority, options):
    from agent.core.weekly_auto import WeeklyAutoRuntime

    return WeeklyAutoRuntime(sink, authority, **options)


def _knowhow_scan_runtime(sink, options):
    from agent.core.knowhow_scan_actions import KnowhowScanRuntime

    return KnowhowScanRuntime(sink=sink, **options)


def _card_scan_runtime(sink, options):
    from agent.core.card_scan_actions import CardScanRuntime

    return CardScanRuntime(sink=sink, **options)


def build_tasker(controller, sink, save_diagnostics=True, shop_runtime=None, runtimes=None, frame_gate=None,
                 weekly_store=None):
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker

    from agent.core.resource_bundles import bundle_paths, ocr_model_dir
    from agent.core.task_actions import launcher_actions

    require(Library.version() == FRAMEWORK, f"启动器要求 MaaFramework {FRAMEWORK}")
    resource = Resource()
    for bundle in bundle_paths():
        require(resource.post_bundle(bundle).wait().succeeded, f"资源载入失败: {bundle}")
    model = ocr_model_dir()
    if model is not None:
        require(resource.post_ocr_model(model).wait().succeeded, f"OCR 模型载入失败: {model}")
    from agent.core import popup_fallback

    popup_fallback.install(resource)  # before the frame guard snapshots node definitions
    from agent.core import business_custom, grade_custom, match_custom, shop_custom, training_custom

    runtimes = dict(runtimes or task_runtimes(sink))
    if shop_runtime is not None:
        runtimes["shop"] = shop_runtime
    actions = launcher_actions(sink, save_diagnostics=save_diagnostics)
    actions.update(shop_custom.shop_actions(runtimes["shop"]))
    recognitions = shop_custom.shop_recognitions(runtimes["shop"])
    from agent.core import mission_claim_actions, present_actions
    actions.update(present_actions.present_registrations(
        runtime=runtimes["present"], sink=sink, **({} if weekly_store is None else {"weekly_store": weekly_store})))
    recognitions["SC.Present.Ready"] = present_actions.PresentReadyRecognition(runtimes["present"])
    for register, kind in ((training_custom.training_registrations, "training"),
                           (grade_custom.grade_registrations, "grade"), (match_custom.match_registrations, "match"),
                           (business_custom.business_registrations, "business"),
                           (mission_claim_actions.mission_registrations, "missions")):
        task_recognitions, task_actions = register(runtimes[kind])
        recognitions.update(task_recognitions)
        actions.update(task_actions)
    from agent.core import resource_read
    read_recognitions, read_actions = resource_read.registrations(
        runtimes.get("resource_read") or resource_read.ResourceReadRuntime(sink))
    recognitions.update(read_recognitions)
    actions.update(read_actions)
    from agent.core.card_scan_actions import card_scan_registrations
    scan_runtime = runtimes.get("card_scan") or _card_scan_runtime(sink, {})
    scan_recognitions, scan_actions = card_scan_registrations(scan_runtime)
    recognitions.update(scan_recognitions)
    actions.update(scan_actions)
    from agent.core.knowhow_scan_actions import knowhow_scan_registrations
    knowhow_recognitions, knowhow_actions = knowhow_scan_registrations(
        runtimes.get("knowhow_scan") or _knowhow_scan_runtime(sink, {}))
    recognitions.update(knowhow_recognitions)
    actions.update(knowhow_actions)
    from agent.core.wing_custom import WingSession, registrations as wing_registrations
    wing_recognitions, wing_actions = wing_registrations(runtimes.get("wing") or WingSession(sink))
    from agent.core import weekly_auto
    weekly_recognitions, weekly_actions = weekly_auto.registrations(
        runtimes.get("weekly_auto") or _weekly_auto_runtime(sink, None, {}))
    recognitions.update(weekly_recognitions)
    actions.update(weekly_actions)
    from agent.core import collabo_custom
    collabo_recognitions, collabo_actions = collabo_custom.collabo_registrations(
        runtimes.get("collabo") or _collabo_runtime(sink, None, {}))
    recognitions.update(collabo_recognitions)
    actions.update(collabo_actions)
    recognitions.update(wing_recognitions)
    actions.update(wing_actions)
    for name, action in actions.items():
        require(resource.register_custom_action(name, action), f"自定义动作注册失败: {name}")
    for name, recognition in recognitions.items():
        require(resource.register_custom_recognition(name, recognition), f"自定义识别注册失败: {name}")
    from agent.core.resource_observer import install as install_observer
    # Passive readings persist only for real (non-replay) controllers: offline replays and tests never touch the private store.
    replay_controller = isinstance(getattr(controller, "scenario", None), ReplayScenario)
    observer = install_observer(resource, sink, **({"store": None} if replay_controller else {}))
    frame_binding = None
    if (frame_gate is None and isinstance(getattr(controller, "scenario", None), ReplayScenario)
            and controller.scenario.node_binding_required):
        frame_gate = controller.scenario
    if frame_gate is not None:
        from agent.core.pipeline_frame_guard import install
        frame_binding = install(resource, frame_gate)
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            "控制器连接失败")
    tasker = Tasker()
    require(tasker.bind(resource, controller) and tasker.inited, "Tasker 绑定失败")
    if frame_binding is not None:
        frame_binding.attach(tasker)
    tasker._sc_keepalive = (resource, controller, actions, recognitions, frame_binding, observer)
    tasker._sc_resource_observer = observer
    return tasker


HOST_SESSION_MARGIN_S = 120.0  # refuse to start when the host's own session cap is this close (or already passed)
HOST_RUN_BEGIN_COMMAND = "pipeline-run-begin"


def host_session_age(pipeline_session, now=None):
    """Seconds since the first accepted gesture of the host's current run window, from its audit (``host-<game day>.jsonl``
    under the session's ``audit_root``, records of the session ``pid``); None when that window has sent nothing yet.

    The host refuses every click once ``limits.session_max_ms`` has passed since that first gesture (in memory). Until
    2026-10-10 the window was the whole host lifetime (2026-10-03 14:46: PID 42436 had first sent at about 03:46, so
    GradeFes' only click came back ``refused: session_time_cap_reached``). From 2026-10-11 a launcher's
    ``pipeline-run-begin`` opens a new window; the host audits it as ``stage: run_begin``, so only gestures after the
    latest such record of this pid count. Hosts without that handshake never write it (lifetime window as before)."""
    data = json.loads(Path(pipeline_session).read_text(encoding="utf-8-sig"))
    pid, root = data.get("pid"), Path(str(data.get("audit_root") or RUN_LOG_DIR))
    require(type(pid) is int, "pipeline-input 会话缺少 pid")
    now = now or _dt.datetime.now(_dt.timezone.utc)
    gestures, window_start = [], None
    for day in (now - _dt.timedelta(days=1), now, now + _dt.timedelta(days=1)):  # game days around now (JST 05:00)
        path = root / f"host-{(day + _dt.timedelta(hours=4)).strftime('%Y-%m-%d')}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("pid") != pid or record.get("stage") not in ("intent", "sent", "run_begin"):
                continue
            # .NET writes 7 fractional digits; Python 3.10 fromisoformat accepts at most 6.
            raw = re.sub(r"(\.\d{6})\d+", r"\1", record["time"])
            stamp = _dt.datetime.fromisoformat(raw)
            if record["stage"] == "run_begin":
                window_start = stamp if window_start is None or stamp > window_start else window_start
            else:
                gestures.append(stamp)
    first = min((g for g in gestures if window_start is None or g >= window_start), default=None)
    return None if first is None else (now - first).total_seconds()


def check_host_session(pipeline_session, now=None):
    """Refuse ``--live`` when the host would refuse every click (its session cap from the window's first gesture)."""
    data = json.loads(Path(pipeline_session).read_text(encoding="utf-8-sig"))
    cap_s = (data.get("limits") or {}).get("session_max_ms", 3_600_000) / 1000.0
    age = host_session_age(pipeline_session, now)
    hint = ("宿主拒绝了新运行窗口（上一运行 30 秒内仍有点击）：请等上一运行结束 30 秒后重试"
            if data.get("run_begin_command") == HOST_RUN_BEGIN_COMMAND else "请由用户关闭并重启宿主后再运行")
    require(age is None or age < cap_s - HOST_SESSION_MARGIN_S,
            f"宿主（PID {data.get('pid')}）本次运行窗口自首次手势起已 {0 if age is None else age / 60:.0f} 分钟，"
            f"达到或接近宿主会话上限 {cap_s / 60:.0f} 分钟（session_time_cap_reached，宿主会拒绝所有点击）：" + hint)
    return age


def begin_host_run(endpoint_session, send=None):
    """Launcher-only handshake (never the Agent mid-run): ask the host to open a new run window, so its run click cap,
    session clock count from this run (the host spend cap stays per host lifetime) instead of from the host start. ``endpoint_session`` is the
    validated pipeline session. Returns the host reply, or None for a host without the handshake (lifetime window).
    A refusal (for example ``run_begin_refused_recent_input``: another run sent a click within 30 s) is not fatal: the
    current window simply continues, and :func:`check_host_session` still applies to it."""
    import secrets

    from agent.core import webview_controller as wc

    if endpoint_session.get("run_begin_command") != HOST_RUN_BEGIN_COMMAND:
        return None
    request = {"command": HOST_RUN_BEGIN_COMMAND, "protocol_version": wc.PIPELINE_PROTOCOL_VERSION,
               "token": endpoint_session["token"], "nonce": secrets.token_hex(16)}
    reply = (send or wc.send_line)(endpoint_session, request)
    require(isinstance(reply, dict) and type(reply.get("ok")) is bool and isinstance(reply.get("reason"), str),
            "宿主运行开始应答无效")
    require(reply.get("attempted") is False, "宿主运行开始应答不应包含输入")
    if not reply["ok"]:
        print(f"[run_tasks] 宿主未开启新运行窗口（{reply['reason']}）：沿用当前窗口的点击与时长上限", file=sys.stderr)
    return reply


def build_live_gate(config, session):
    """WebView2 gate for ``--live``: pipeline families only, controller-side caps; the SpendGate allows only the
    run config's ``allow_spend`` actions (default none), with Grade grants derived from the selected batch."""
    from agent.core.browser_bridge import BrowserBridge
    from agent.core import webview_controller as wc

    session = Path(session)
    require(Path(str(session) + wc.PIPELINE_SUFFIX).is_file(),
            "--live 需要宿主以 --enable-pipeline-input 启动并写出 <session>.pipeline-input.json")
    bridge = BrowserBridge.from_file(session)
    run_config = wc.RunConfig(True, frozenset(live_families(config)))
    endpoints = wc.load_endpoints(session, bridge.session["pid"], run_config)
    require(set(endpoints) == {wc.ENDPOINT_PIPELINE}, "Pipeline 端点会话无效")
    # New run window on the host (per-run caps), then refuse a window that is already at its session cap.
    begin_host_run(endpoints[wc.ENDPOINT_PIPELINE].session)
    check_host_session(Path(str(session) + wc.PIPELINE_SUFFIX))
    limits = wc.ClickLimits(run_max=config.get("max_clicks"), task_max=None,   # no click caps unless named explicitly
                            session_max_s=60.0 * config.get("session_max_minutes", DEFAULT_SESSION_MAX_MINUTES))
    allow_spend = config.get("allow_spend", [])
    # W.I.N.G.: one Produce start per launcher run (the Agent also attempts it at most once).
    per_action = 1 if WING_SPEND_ACTION in allow_spend else wc.MAX_GRANTS_PER_ACTION
    spend_gate = wc.SpendGate(allow_spend, max_per_action=per_action,
                             action_limits=spend_grant_limits(config)) if allow_spend else wc.SpendGate()
    return wc.WebViewInputGate(bridge, run_config, endpoints, spend_gate, limits=limits)


def _run(config, *, offline_steps=None, offline_images=None, simulate_input=False, session=None, policy=None,
        interface=None, summary_path=None, now=None, save_diagnostics=True, live=False, gate=None, shop_options=None,
        task_options=None, frame_guard_enabled=False, supervised_startup=False, supervised_task=False, control=None,
        supervised_sequence=False, taskers=None):
    """Plan, decide the input mode, build the controller, execute and write the summary. ``taskers`` collects the
    built tasker so the caller can release its event sinks (maa_sinks.release)."""
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker

    from agent.core.task_actions import ReportSink

    now = now or _dt.datetime.now(_dt.timezone.utc)
    interface = interface or load_interface()
    policy = load_policy() if policy is None else policy
    offline = offline_steps is not None or offline_images is not None
    require(type(supervised_startup) is bool, "监督启动开关须为布尔值")
    require(type(supervised_task) is bool and not (supervised_task and supervised_startup), "监督单任务与监督启动不能同时选择")
    require(type(supervised_sequence) is bool and not (supervised_sequence and (supervised_task or supervised_startup)),
            "监督日常不能与监督单任务/监督启动同时选择")
    if supervised_sequence:
        config = supervised_sequence_config(config, live=live, offline=offline, simulate_input=simulate_input)
    if supervised_startup:
        config = supervised_startup_config(config, live=live, offline=offline, simulate_input=simulate_input)
    if supervised_task:
        config = supervised_task_config(config, live=live, offline=offline, simulate_input=simulate_input)
    only = supervised_only_item(config) if supervised_task else None
    plans = [supervised_only_plan(only, config)] if only is not None else plan_tasks(interface, config)
    if supervised_startup:
        require(len(plans) == 1 and plans[0].entry == "SC.Startup.Entry" and not plans[0].pipeline_override,
                "监督启动只接受原始 Startup 入口，不允许任务选项覆盖")
        plans = [TaskPlan("Startup", "SC.Startup.Entry", supervised_startup_override())]
    if supervised_task:
        require(len(plans) == 1, "监督单任务计划须唯一")
        plans = [TaskPlan(plans[0].name, plans[0].entry,
                          merge_override(plans[0].pipeline_override, supervised_startup_override()))]
    if supervised_sequence:
        run_families = frozenset(config["families"])
        plans = [TaskPlan(p.name, p.entry, merge_override(
            sequence_task_override(p.name, run_families, config.get("allow_spend", ()), plan=p), p.pipeline_override))
            for p in plans]
    require(offline or session is not None or gate is not None, "需要 --offline-frames 或 --session")
    mode, mode_reason = resolve_input_mode(config, policy, offline, simulate_input, live=live)
    replay_root = None
    if offline:  # a replay never touches the live ledgers/samples the daily planner counts as real attempts
        replay_root = offline_replay_root(now)
        task_options, shop_options = offline_replay_options(task_options, shop_options, replay_root), None
    plans, grade_progress = grade_batch_plan(plans, task_options)
    from agent.core import fes_allocation as fa

    fes = fes_preflight(plans, config.get("allow_spend", ()) if mode != READ_ONLY else (),
                        policy=fa.TicketPolicy.from_document(policy), grade_progress=grade_progress)
    require(not fes.refused, "Fes 参加权分配被拒绝：" + "；".join(fes.messages()[:len(fes.errors)]))
    require(type(frame_guard_enabled) is bool, "开发帧许可开关须为布尔值")
    require(not frame_guard_enabled or mode == LIVE_INPUT, "帧许可只用于 Pipeline 输入路径")
    frame_guard_enabled = frame_guard_enabled or supervised_startup or supervised_task or supervised_sequence
    if gate is not None:
        gate.run_control = control
    if control is not None and control.poll():
        control.stop_verified = gate.stop_input() and gate.input_idle() if gate is not None else True
        return {"started_at": now.isoformat(), "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "controller": "not_connected", "input_mode": mode, "input_mode_reason": mode_reason,
                "policy_live_input_enabled": policy.get("live_input_enabled") is True,
                "run_config_input_enabled": bool(config.get("input_enabled", live)),
                "real_input_attempts": 0, "allow_spend": list(config.get("allow_spend", [])),
                "preset": config.get("preset", DEFAULT_PRESET) if config.get("tasks") is None else None,
                "fes_allocation": fes.to_dict(),
                "order": [p.name for p in plans], "tasks": execute_plan(plans, None, control=control),
                "succeeded": 0, "failed": len(plans)}
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    sink = ReportSink(on_report=control.report if control is not None else None)
    scenario = None
    families = None
    allow_spend = tuple(config.get("allow_spend", []))
    authority = None
    task_actions = SUPERVISED_TASK_ACTIONS.get(plans[0].name) if supervised_task else None
    if supervised_task and plans[0].name == WING_TASK:
        task_actions = wing_task_actions(allow_spend, giveup=wing_plan_giveup(plans[0]), halo=wing_plan_halo(plans[0]))
    if supervised_task and plans[0].name == KNOWHOW_TASK and knowhow_plan_lock(plans[0]):
        task_actions = knowhow_task_actions(lock=True)
    node_prefixes = SUPERVISED_TASK_NODE_PREFIXES.get(plans[0].name) if supervised_task else None
    if mode == LIVE_INPUT:
        from agent.core import webview_controller

        families = live_families(config)
        gate = gate or build_live_gate(config, session)
        if supervised_startup or supervised_task or supervised_sequence:
            require(gate.config.families == frozenset(families) and gate.limits.run_max == config.get("max_clicks"),
                    "监督控制器动作族或运行点击上限与配置不符")
        gate.run_control = control
        gate.configure_frame_guard(frame_guard_enabled)
        if task_actions is not None:
            gate.restrict_actions(task_actions, node_prefixes)
        controller = webview_controller.create_webview_controller(gate)
        authority = webview_controller.LiveSpendAuthority(gate)
    elif offline:
        images, advances = offline_images if offline_images is not None else load_frames(offline_steps)
        # New claim targets overlap old dialog buttons. Include them only for their selected task.
        from agent.core.pipeline_actions import default_table
        replay_families = {action.family for action in default_table().actions
                           if action.enabled and action.family not in {"present", "mission-claim"}}
        for plan in plans:
            replay_families.update(SUPERVISED_TASK_FAMILIES.get(plan.name, ()))
            if plan.name == "Startup":
                replay_families.update(SUPERVISED_STARTUP_FAMILIES)
        scenario = ReplayScenario(images, advances, allow_input=(mode == OFFLINE_SIMULATED), allow_spend=allow_spend,
                                  run_control=control, families=replay_families)
        controller = create_replay_controller(scenario)
        authority = ReplaySpendAuthority(scenario)
    else:
        from agent.core.browser_bridge import BrowserBridge
        from agent.core import webview_controller

        gate = webview_controller.WebViewInputGate(BrowserBridge.from_file(session), webview_controller.RunConfig(),
                                                 run_control=control)
        controller = webview_controller.create_webview_controller(gate)
    options = dict(task_options or {})
    if shop_options:
        options["shop"] = shop_options
    source = scenario if scenario is not None else gate
    if source is not None:
        # W.I.N.G. hashes each capture once (its checks of one Nav.Loop pass share the capture number).
        options["wing"] = {"capture_seq": (lambda: scenario.captures) if scenario is not None else (lambda: gate.capture_seq),
                           **options.get("wing", {})}
        options["missions"] = dict(options.get("missions", {}))
        options["missions"].update(frame_source=lambda: source._frame,
                                   frame_clock=(lambda: float(scenario.captures)) if scenario is not None else gate.clock)
    collabo_timeouts = collabo_task_timeouts(config, mode)
    options["collabo"] = {"max_wait_s": collabo_timeouts.get("CollaboFes", 0), **options.get("collabo", {})}
    if mode == LIVE_INPUT and gate is not None:
        # 2026-11-19 live: a 満員 wait ran into the session cap (the banner tap was refused); waits end before it.
        options["collabo"].setdefault("time_left", lambda: gate.limits.session_max_s - (gate.clock() - gate.started))
    runtimes = task_runtimes(sink, authority, options, present_input=mode in (LIVE_INPUT, OFFLINE_SIMULATED),
                             missions_input=mode in (LIVE_INPUT, OFFLINE_SIMULATED),
                             wing_input=supervised_task and plans[0].name == WING_TASK
                             and mode in (LIVE_INPUT, OFFLINE_SIMULATED),
                             wing_spend=WING_SPEND_ACTION in allow_spend,
                             wing_reroll=supervised_task and wing_reroll_option(only),
                             wing_ledger=WING_SPEND_LEDGER if mode == LIVE_INPUT else None,
                             wing_settings=WING_SETTINGS_FILE if mode == LIVE_INPUT else None)
    if replay_root is not None and getattr(runtimes.get("wing"), "unknown_dir", None) is not None:
        runtimes["wing"].unknown_dir = replay_root / "wing_unknown"   # simulated Wing input samples unknown frames
    if gate is not None and runtimes.get("knowhow_scan") is not None:
        runtimes["knowhow_scan"].click_history = lambda: gate.history   # read-only: proves a denied click was not sent
    tasker = build_tasker(controller, sink, save_diagnostics=save_diagnostics, runtimes=runtimes,
                         frame_gate=gate if mode == LIVE_INPUT and frame_guard_enabled else None,
                         **({"weekly_store": replay_root / "weekly_status.json"} if replay_root is not None else {}))
    if taskers is not None:
        taskers.append(tasker)
    if control is not None and control.events_path is not None:
        control.attach(tasker)

    def observe(before=None):
        if mode == LIVE_INPUT:
            if before is None:
                return {"history": len(gate.history)}
            return {"live_clicks": gate.history[before["history"]:]}
        if scenario is None:
            return {} if before is None else {}
        if before is None:
            return {"clicks": len(scenario.clicks), "refused": len(scenario.refused), "spends": len(scenario.spends)}
        return {"simulated_clicks": [list(c) for c in scenario.clicks[before["clicks"]:]],
                "simulated_actions": scenario.actions[before["clicks"]:],
                "refused_clicks": [list(c) for c in scenario.refused[before["refused"]:]],
                "simulated_spends": [list(s) for s in scenario.spends[before.get("spends", 0):]]}

    def before_task(plan):
        if control is not None and plan.name == "GradeFes":
            _, current = grade_batch_plan([plan], task_options)
            if current is not None:
                control.report({"kind": "grade", "daily_progress": current.to_dict()})
        if mode != LIVE_INPUT:
            return None
        if gate.clock() - gate.started > gate.limits.session_max_s:
            return "session_time_cap_reached"
        if any(h.get("result") == "refused" and h.get("reason") == "session_time_cap_reached" for h in gate.history):
            return "host_session_time_cap_reached"  # the host refuses every further click in this run window
        gate.begin_task(plan.name, task_click_cap(config, plan.name))
        if supervised_sequence:
            # only this task's own consuming action may receive a grant while it runs (never widens the run allowlist)
            gate.spend_gate.revoke()
            gate.spend_gate.allowed = frozenset(allow_spend) & task_spend_actions(plan)
        return None

    def after_task(plan):
        if mode == LIVE_INPUT:
            gate.frame_permits.invalidate("task_ended")
            if gate.frame_binding is not None:
                gate.frame_binding.reset()
        if plan.name == "MissionsClaim":
            runtimes["missions"].flush_on_task_end()
        if plan.name == KNOWHOW_TASK and "knowhow_scan" in runtimes:
            streak = getattr(gate, "capture_fail_streak", 0) if gate is not None else 0
            runtimes["knowhow_scan"].flush_on_task_end(streak if type(streak) is int else 0,
                                                       None if control is None else control.reason)
        # A shared failure node (e.g. SC.Common.FailTask after a refused click) ends the job without the task's own
        # report: flush every unreported run so its outcome and decisions still reach the summary.
        for kind, runtime in runtimes.items():
            if kind == "missions":
                continue
            run = getattr(runtime, "run", None)
            if run is not None and not getattr(run, "reported", True):
                if isinstance(getattr(run, "notes", None), list):
                    run.notes.append("task_ended_before_report")
                    streak = getattr(gate, "capture_fail_streak", 0) if gate is not None else 0
                    if type(streak) is int and streak > 0:
                        # Maa cannot evaluate any node (not even the task's own stop) without a frame
                        run.notes.append(f"host_capture_failed_streak={streak}")
                    if control is not None and control.reason:
                        run.notes.append("stopped_before_result_observed")
                runtime.report()

    def stop_input():
        if gate is not None:
            gate.stop_input()
        if scenario is not None:
            scenario.stop_input()

    if control is not None:
        control.stop_cleanup = stop_input

    results = execute_plan(plans, tasker, sink=sink,
                           base_override=mode_override(mode, families, allow_spend, task_actions, node_prefixes),
                           timeout_s=float(config.get("task_timeout_s", DEFAULT_TASK_TIMEOUT_S)), observer=observe,
                           before_task=before_task, after_task=after_task, control=control, on_stop=stop_input,
                           input_idle=gate.input_idle if gate is not None else None, failure_snapshot=not offline,
                           task_timeouts=collabo_timeouts)
    for result in results:  # name the input refusals next to the generic failure reason
        if result["status"] != "succeeded":
            for click in result.get("live_clicks", []):
                if click.get("result") in ("refused", "denied", "unknown"):
                    result["fail_reasons"].append(f"click_{click['result']}:{click.get('action')}:{click.get('reason')}")
    summary = {
        "started_at": now.isoformat(), "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "controller": "offline_replay" if offline else ("webview2_pipeline" if mode == LIVE_INPUT else "webview2_read_only"),
        "input_mode": mode, "input_mode_reason": mode_reason,
        "policy_live_input_enabled": bool(isinstance(policy, dict) and policy.get("live_input_enabled") is True),
        "run_config_input_enabled": bool(config.get("input_enabled", live)),
        "real_input_attempts": gate.run_clicks if mode == LIVE_INPUT else 0,
        "allow_spend": list(allow_spend),
        "fes_allocation": fes.to_dict(),
        "preset": config.get("preset", DEFAULT_PRESET) if config.get("tasks") is None else None,
        "order": [p.name for p in plans],
        "tasks": results,
        "succeeded": sum(r["status"] == "succeeded" for r in results),
        "failed": sum(r["status"] != "succeeded" for r in results),
    }
    observer = getattr(tasker, "_sc_resource_observer", None)
    if observer is not None:
        summary["resource_observer"] = observer.summary()
    if mode == LIVE_INPUT:
        summary["live"] = {"families": families, "max_clicks": gate.limits.run_max,
                           "session_max_s": gate.limits.session_max_s,
                           "task_caps": {p.name: task_click_cap(config, p.name) for p in plans},
                           "frame_guard_enabled": frame_guard_enabled,
                           "supervised_startup": supervised_startup,
                           "supervised_task": plans[0].name if supervised_task else None,
                           "supervised_sequence": [p.name for p in plans] if supervised_sequence else None,
                           "actions": None if task_actions is None else sorted(task_actions),
                           "node_prefixes": None if node_prefixes is None else list(node_prefixes)}
    if scenario is not None:
        summary["offline"] = {"frames": len(scenario.images), "final_frame": scenario.index,
                              "captures": scenario.captures, "other_input_calls": scenario.other_calls,
                              "isolated_root": str(replay_root)}   # ledgers/samples of this replay (never the live ones)
    if summary_path is not False:
        summary["summary_path"] = str(write_summary(summary, summary_path or default_summary_path(now)))
    return summary


def run(config, *, offline_steps=None, offline_images=None, simulate_input=False, session=None, policy=None,
        interface=None, summary_path=None, now=None, save_diagnostics=True, live=False, gate=None, shop_options=None,
        task_options=None, frame_guard_enabled=False, supervised_startup=False, supervised_task=False, events_path=None, stop_file=None,
        control=None, live_status=None, supervised_sequence=False):
    from agent.core.run_control import RunControl

    require(control is None or events_path is None and stop_file is None, "控制对象与控制路径不能同时传入")
    control = control or RunControl(events_path=events_path, stop_file=stop_file)
    live_feed = start_live_status(live_status, control, config, interface)
    control.emit("run.started")
    taskers = []
    try:
        summary = _run(config, offline_steps=offline_steps, offline_images=offline_images,
                       simulate_input=simulate_input, session=session, policy=policy, interface=interface,
                       summary_path=False, now=now, save_diagnostics=save_diagnostics, live=live, gate=gate,
                       shop_options=shop_options, task_options=task_options, frame_guard_enabled=frame_guard_enabled,
                       supervised_startup=supervised_startup, supervised_task=supervised_task, control=control,
                       supervised_sequence=supervised_sequence, taskers=taskers)
        control.emit("run.finished", status="stopped" if control.poll() else
                     ("succeeded" if summary["failed"] == 0 else "failed"),
                     succeeded=summary["succeeded"], failed=summary["failed"])
        control.close()
        if control.poll() and getattr(control, "stop_cleanup", None) is not None:
            control.stop_cleanup()
            if control.stop_verified is None:
                control.stop_verified = False
        summary["run_control"] = control.summary()
        summary["readings"] = []
        for task in summary["tasks"]:
            task["readings"] = [payload for report in task["reports"]
                                if (payload := control.readings(report)) is not None]
            summary["readings"].extend({"task": task["name"], **payload} for payload in task["readings"])
        if summary_path is not False:
            start = _dt.datetime.fromisoformat(summary["started_at"])
            summary["summary_path"] = str(write_summary(summary, summary_path or default_summary_path(start)))
        return summary
    except BaseException:
        control.request_stop("runner_error")
        if gate is not None:
            gate.stop_input()
        if getattr(control, "stop_cleanup", None) is not None:
            control.stop_cleanup()
        control.emit("run.finished", status="error")
        raise
    finally:
        control.close()
        stop_live_status(live_feed, control)
        for tasker in taskers:
            maa_sinks.release(tasker)


# ---- live status (UI 「运行中」 view; display only) -----------------------------------
def live_task_names(config, interface=None):
    """Planned task order for the queue display (best effort; the real plan is validated later by _run)."""
    try:
        return [p.name for p in plan_tasks(interface or load_interface(), config)]
    except Exception:
        tasks = config.get("tasks") if isinstance(config, dict) else None
        return [t if isinstance(t, str) else t.get("name") for t in tasks or []
                if isinstance(t, str) or isinstance(t, dict) and isinstance(t.get("name"), str)]


def start_live_status(path, control, config, interface=None):
    """``path``: None/False = off, True = the default private file, else a private path. Never blocks a run."""
    if path is None or path is False:
        return None
    from agent.core import live_status as ls
    try:
        live = ls.LiveStatus(ls.DEFAULT_PATH if path is True else path)
        cap = config.get("max_clicks", DEFAULT_MAX_CLICKS) if isinstance(config, dict) else None
        live.start(control.run_id, live_task_names(config, interface), cap if type(cap) is int else None)
        control.live = live
        ls.activate(live)
        return live
    except Exception:
        return None


def stop_live_status(live, control):
    if live is None:
        return
    from agent.core import live_status as ls
    if live.snapshot.get("state") != "finished":
        live.finish("stopped" if control.reason else "error")
    live.close()
    ls.deactivate(live)
    control.live = None


_FAULT_LOG = None


def _enable_fault_log(summary):
    """A native crash (access violation in MaaFramework, 2026-10-13 kh-full5/7: rc 127, empty log) leaves no Python
    traceback; dump the Python stacks of every thread next to the run summary so the next one is diagnosable."""
    global _FAULT_LOG
    if summary is None or _FAULT_LOG is not None:
        return
    try:
        import faulthandler

        path = Path(summary).with_suffix(".fault.log")
        _FAULT_LOG = open(path, "w", encoding="utf-8")
        faulthandler.enable(file=_FAULT_LOG, all_threads=True)
    except Exception:       # diagnostics only: never blocks a run
        _FAULT_LOG = None


def _opt_out_of_power_throttling() -> bool:
    """2026-10-15 speed: the UI engine starts this process without a window, and Windows 11 may run such background
    processes in efficiency mode (EcoQoS: E-cores, low clock). Live W.I.N.G. OCR measured 5-7x slower than the same
    frames offline. Opt this process out (PROCESS_POWER_THROTTLING_EXECUTION_SPEED, state off); best effort."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        state = State(1, 0x1, 0x0)   # PROCESS_POWER_THROTTLING_CURRENT_VERSION, EXECUTION_SPEED, not throttled
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.SetProcessInformation.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        return bool(kernel32.SetProcessInformation(handle, 4, ctypes.byref(state), ctypes.sizeof(state)))  # 4 = ProcessPowerThrottling
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="按 PI v2 任务声明顺序运行任务（默认只读；离线回放不连接任何宿主）。")
    parser.add_argument("--config", type=Path, help="运行配置 JSON（preset/tasks/input_enabled/families/task_timeout_s）")
    parser.add_argument("--preset", default=None, help=f"预设名（默认 {DEFAULT_PRESET}）")
    parser.add_argument("--tasks", default=None, help="逗号分隔的任务名，覆盖预设顺序")
    parser.add_argument("--offline-frames", default=None, help="私有帧目录、逗号分隔的 PNG 列表或 steps JSON")
    parser.add_argument("--simulate-input", action="store_true", help="离线回放时启用点击节点（只到达回放控制器）")
    parser.add_argument("--session", type=Path, default=None, help="宿主截图会话文件（只读或 --live）")
    parser.add_argument("--live", action="store_true",
                        help="实机输入：需策略 live_input_enabled、ADR-011、宿主 pipeline-input 会话与显式 --tasks")
    parser.add_argument("--supervised-startup", action="store_true",
                        help="仅本轮监督 Startup 开启原生帧许可；需 --live、唯一 Startup、运行/任务上限 1–4、无消费")
    parser.add_argument("--supervised-task", action="store_true",
                        help="仅本轮监督唯一日常任务开启帧许可；需 preset:null、显式点击上限、限定动作族/消费动作")
    parser.add_argument("--supervised-sequence", action="store_true",
                        help="监督日常（界面「开始」）：按给定顺序运行多个日常任务；每个任务各自的动作族、点击上限与消费动作")
    parser.add_argument("--allow-shop-spend", action="store_true",
                        help="允许 Shop 任务消费マニー（仅本日限定、每次交换需核对交換確認并签发一次性授权）")
    parser.add_argument("--allow-training-spend", action="store_true",
                        help="允许 Training 任务消耗基础铜券×1（トレーニング確認核对后签发一次性授权）")
    parser.add_argument("--allow-grade-spend", action="store_true",
                        help="允许所选 Grade/彩排模式和难度消耗参加权×1；正式为ライブ開始，彩排为所选难度按钮")
    parser.add_argument("--allow-match-spend", action="store_true",
                        help="允许 MatchSkip 在关闭“只采样”时消耗フェス参加権×次数（スキップ弹窗核对后签发一次性授权）")
    parser.add_argument("--allow-weekly-auto-spend", action="store_true",
                        help="允许 WeeklyProduce 按一次「プロデュース開始」（体力 30×次数，开始确认框前后体力核对后签发一次性授权）")
    parser.add_argument("--allow-collabo-spend", action="store_true",
                        help="允许 CollaboFes 按「テンション決定」（每局コラボフェスパス 1，最多 6 局；该帧核对后签发一次性授权）")
    parser.add_argument("--allow-produce-spend", action="store_true",
                        help="仅监督 Wing（mode: start）：允许按一次「プロデュース開始」（体力 30，使用確認核对后签发一次性授权）")
    parser.add_argument("--summary", type=Path, default=None, help="运行摘要路径（须在 dev_assets/private 下）")
    parser.add_argument("--events", type=Path, default=None, help="独占创建的私有 JSONL 事件流，不覆盖既有文件")
    parser.add_argument("--stop-file", type=Path, default=None, help="私有停止文件；出现后锁存整轮停止，不读取文件内容")
    parser.add_argument("--live-status", default="default",
                        help="界面「运行中」实时状态文件（私有、原子替换、≤2 次/秒）；default=dev_assets/private/live-status/current.json，off=不写")
    parser.add_argument("--list", action="store_true", help="只列出任务与预设，不运行")
    args = parser.parse_args(argv)
    _enable_fault_log(args.summary)
    _opt_out_of_power_throttling()
    try:
        interface = load_interface()
        if args.list:
            print(json.dumps({"tasks": [{"name": n, "entry": t["entry"], "default_check": t.get("default_check", False)}
                                        for n, t in interface.tasks.items()],
                              "presets": {n: [i["name"] for i in p.get("task", []) if i.get("enabled", True)]
                                          for n, p in interface.presets.items()}}, ensure_ascii=False, indent=2))
            return 0
        config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
        if args.preset:
            config["preset"] = args.preset
        if args.tasks:
            config["tasks"] = select_tasks(config, args.tasks)
        for flag, action in SPEND_FLAGS.items():
            if getattr(args, flag):
                actions = spend_actions_for_flag(flag, config, interface)
                config["allow_spend"] = sorted(set(config.get("allow_spend", [])) | actions)
        steps = parse_offline_frames(args.offline_frames) if args.offline_frames else None
        require(not args.live or (args.session is not None and steps is None),
                "--live 需要 --session 且不能与 --offline-frames 同用")
        summary = run(config, offline_steps=steps, simulate_input=args.simulate_input, session=args.session,
                      interface=interface, summary_path=args.summary, live=args.live,
                      supervised_startup=args.supervised_startup, supervised_task=args.supervised_task,
                      supervised_sequence=args.supervised_sequence,
                      events_path=args.events, stop_file=args.stop_file,
                      live_status=None if args.live_status == "off" else
                      (True if args.live_status == "default" else Path(args.live_status)))
    except Exception as exc:  # every refusal is reported; nothing is retried
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"order": summary["order"], "input_mode": summary["input_mode"],
                      "fes_allocation": summary["fes_allocation"],
                      "tasks": [(t["name"], t["status"], t.get("fail_reasons")) for t in summary["tasks"]],
                      "summary": summary.get("summary_path"), "run_control": summary["run_control"],
                      "offline_isolated_root": (summary.get("offline") or {}).get("isolated_root")}, ensure_ascii=False))
    return 0 if (summary["failed"] == 0 and not summary["run_control"]["stop_requested"]
                 and summary["run_control"]["events_complete"]) else 1


if __name__ == "__main__":
    # Only the real command-line entry (not imports/tests calling main()) persists passive readings; replays never do.
    os.environ.setdefault("SC_READINGS_PERSIST", "1")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
