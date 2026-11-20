#!/usr/bin/env python3
"""W.I.N.G. from the UI: the 任务 page's AutoProduce settings ->
``ProduceConfig.from_task_options`` -> the supervised ``Wing`` launcher config (``tools/run_tasks.py --live
--supervised-task``), plus the private ユニット編成 team snapshot.

Boundaries (the UI cannot widen them):

- The run config is the CLI's own supervised Wing shape (preset null, one task, ≤200 clicks, 60 min, the Wing action
  allowlist enforced by run_tasks); ``run_tasks.supervised_task_config`` validates it before anything starts.
- 2026-10-13 (user): a W.I.N.G. run from the 「Produce」 page always may start one new Produce (``mode: start``,
  プロデュース開始 = stamina 30, at most one per run) -- pressing 开始 there is the consent. The host still passes its
  explicit per-run flag (``--allow-produce-spend``, re-derived from the saved profile); a run without it is refused.
  The old switch 「Produce可开新局」 is fixed to Yes (a saved "No" is migrated away, tools/ui_schema).
- A Produce already in progress is continued only after the Agent verified it is the configured one
  (``verify_resume``: P idol / P card on the プロデュース再開 dialog or the Hub, then the P card and the configured
  S/Guest on the read-only ユニット確認); a mismatch or anything unverifiable stops the run (never abandons).
- Spend ledger (agent.core.wing_ledger, 2026-10-11 review P0-1): an unresolved or uncertain earlier プロデュース開始
  is only noted (``info["spend_uncertain"]``; 不确定只提醒): the Agent presses プロデュース開始 only after
  this run saw Home without a Produce (entry ``rechecked``) or continues the Produce it finds (``confirmed``). An
  unreadable ledger still turns the request into a no-spend ``attach`` run.
- Memory (review P1-1/P1-2/P2-4): one file shared by the UI and the CLI (``SHARED_MEMORY_FILE``); it is never archived
  here for a ``start`` request -- the Agent archives it only when its own start is confirmed, on a Produce identity
  mismatch, or at the confirmed end of the Produce.
- Team sources (2026-10-14): 指定ユニット (+ optional 自动查找编队 = ``unit_search`` over ユニット1–20)
  = the chosen P card (always) + the last complete snapshot of the same P's unit (when present and 「核对上次读到的
  S/Guest」 is on; else developer-registered S/Guest from the profile); 推荐编队 (``自动编队``) = the recommendation
  (P + 4 S + Guest, P chosen by Maa) the UI adopted into the team keys: a new Produce edits the chosen unit into that
  team (``team_edit``, start mode only; the unit is overwritten -- pressing 开始 is the consent), an in-progress one
  is verified against it; never mixed with a snapshot. The snapshot is written only after Maa read all six slots of the unit it used with the P slot
  matching; it lives in ``dev_assets/private/wing-ui/`` (never committed) and a display copy in
  ``ui/game-assets/team-snapshot.js`` (gitignored).
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
WING_UI_DIR = PRIVATE / "wing-ui"
MEMORY_FILE = WING_UI_DIR / "memory.json"   # == agent.core.wing_custom.SHARED_MEMORY_FILE (UI + CLI)
# Auto 自动编队 (team_prep_run_config): its own memory, so the prep never touches a W.I.N.G. Produce's memory.
TEAM_PREP_MEMORY_FILE = WING_UI_DIR / "auto-team-memory.json"
TEAM_PREP_MINUTES = 30
TEAM_PREP_TIMEOUT_S = 30 * 60
SNAPSHOT_FILE = WING_UI_DIR / "team-snapshot.json"
SNAPSHOT_MODULE = ROOT / "ui" / "game-assets" / "team-snapshot.js"
TASK = "AutoProduce"
NEW_RUN_OPTION = "Produce可开新局"     # UI-only spend switch (tools/ui_schema.UI_ONLY_OPTIONS)
SNAPSHOT_CHECK_OPTION = "Produce核对上次编队"   # UI-only: No = check the P card only and re-read the unit
SESSION_MINUTES = 240                # run_tasks.MAX_SESSION_MINUTES (no safety segments)
TASK_TIMEOUT_S = 4 * 3600
SLOTS = ("P", "S1", "S2", "S3", "S4", "guest")
PROPOSAL_MODULE = ROOT / "ui" / "game-assets" / "team-proposal.js"   # tools/auto_team_propose.py output (gitignored)
# 「开局前大富豪」 自动: play one 大富豪 before a new Produce when the adopted 推荐编队 scores below this
# ProxyTERating (ESTIMATED, 0–100) or its 50 万 fan route is not feasible. Same constant in ui/components/team-proposal.js.
MINIGAME_AUTO_MIN_RATING = 75.0
P_ID = re.compile(r"^1\d{9}$")
S_ID = re.compile(r"^2\d{9}$")


def _rel(path):
    return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


# ---- snapshot ---------------------------------------------------------------------------------------------------
def clean_snapshot(data):
    """Validated snapshot dict or None: {version, unit, p_card, s_cards[4], guest, read_at}; every slot a card id."""
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    unit, p, s, guest = data.get("unit"), data.get("p_card"), data.get("s_cards"), data.get("guest")
    if not (type(unit) is int and 1 <= unit <= 20 and isinstance(p, str) and P_ID.match(p) and isinstance(s, list)
            and len(s) == 4 and all(isinstance(c, str) and S_ID.match(c) for c in s)
            and isinstance(guest, str) and S_ID.match(guest)):
        return None
    if len({*s, guest}) != 5:
        return None
    return {"version": 1, "unit": unit, "p_card": p, "s_cards": list(s), "guest": guest,
            "read_at": data.get("read_at") if isinstance(data.get("read_at"), str) else None}


def load_snapshot(path=SNAPSHOT_FILE):
    try:
        return clean_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def save_snapshot(snapshot, path=SNAPSHOT_FILE, module=SNAPSHOT_MODULE):
    snapshot = clean_snapshot(snapshot)
    _require(snapshot is not None, "编队快照无效")
    _atomic_write(path, json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n")
    if module is not None:
        _atomic_write(module, "// Generated by tools/ui_wing.py (private, gitignored): last W.I.N.G. team read on ユニット編成.\n"
                              "export default " + json.dumps(snapshot, ensure_ascii=False) + ";\n")
    return snapshot


def snapshot_from_events(events, p_card):
    """The last ``unit_cards`` decision event (wing_nav._log_unit_cards) as a snapshot, or None: all six slots read
    with confidence, P slot = the configured P card, verdict match (the run used this unit)."""
    found = None
    for event in events:
        if isinstance(event, dict) and event.get("event") == "unit_cards":
            found = event
    if not found or found.get("verdict") != "match":
        return None
    cards = found.get("cards") if isinstance(found.get("cards"), dict) else {}
    if cards.get("P") != p_card or any(not cards.get(slot) for slot in SLOTS):
        return None
    stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return clean_snapshot({"version": 1, "unit": found.get("unit"), "p_card": cards["P"],
                           "s_cards": [cards[s] for s in ("S1", "S2", "S3", "S4")], "guest": cards["guest"],
                           "read_at": stamp})


def record_snapshot(since, p_card, trace_dir=None, path=SNAPSHOT_FILE, module=SNAPSHOT_MODULE):
    """After a UI Wing run: scan the decision traces written since ``since`` (epoch seconds) and save a complete
    snapshot. Returns the snapshot or None (nothing written)."""
    trace_dir = Path(trace_dir or MEMORY_FILE.parent / "decision-log")
    if not trace_dir.is_dir():
        return None
    events = []
    for file in sorted(trace_dir.glob("*.jsonl")):
        try:
            if file.stat().st_mtime < since - 1:
                continue
            for line in file.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
    snapshot = snapshot_from_events(events, p_card)
    return save_snapshot(snapshot, path, module) if snapshot else None


# ---- 开局前大富豪 ---------------------------------------------------------------------------------------------------
def load_proposal(path=PROPOSAL_MODULE):
    """The proposal object of ``ui/game-assets/team-proposal.js`` (``export default {...};``), or None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        body = text[text.index("export default") + len("export default"):].strip().rstrip(";").strip()
        data = json.loads(body)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def minigame_choice(pref, team, proposal, recommend):
    """「开局前大富豪」 for a new Produce -> (play, reason). ``pref`` off/auto/always. 自动 plays only when the
    recommendation the UI adopted (same six cards) has a ProxyTERating below ``MINIGAME_AUTO_MIN_RATING`` or an
    infeasible fan route; without such a score (指定ユニット, no/old proposal) it does not play."""
    if pref == "always":
        return True, "always"
    if pref != "auto":
        return False, "off"
    found = _adopted_candidate(proposal, team) if recommend else None
    rating, feasible = found if found else (None, None)
    if type(rating) not in (int, float):
        return False, "no_rating"
    if rating >= MINIGAME_AUTO_MIN_RATING and feasible is not False:
        return False, "rating_ok"
    # 自动 never stops a run on the unverified home entry (每次 does: the user chose it; outcome text says so)
    return (True, "low_rating") if minigame_entry_verified() else (False, "entry_unverified")


def _same_team(pp, team):
    return isinstance(pp, dict) and pp.get("p_card") == team.get("p_card") and pp.get("guest") == team.get("guest") \
        and list(pp.get("s_cards") or []) == list(team.get("s_cards") or [])


def _adopted_candidate(proposal, team):
    """(rating, route_feasible) of the proposal candidate whose six cards the UI adopted: the main team
    (``recommender``) or one of the optional ``alternatives`` (own ``recommender`` or ``rating``/``route_feasible``)."""
    proposal = proposal if isinstance(proposal, dict) else {}
    rows = [proposal] + [a for a in proposal.get("alternatives") or [] if isinstance(a, dict)]
    for row in rows:
        if _same_team(row.get("proposal"), team):
            rec = row.get("recommender") if isinstance(row.get("recommender"), dict) else {}
            rating = rec.get("ProxyTERating", row.get("rating"))
            feasible = (rec.get("growth") or {}).get("route_feasible", row.get("route_feasible"))
            return rating, feasible
    return None


def minigame_entry_verified():
    """Whether the home Pデスク・ミニゲーム tap (wing_nav target ``home_minigame_menu``) is live-verified (enabled)."""
    from agent.core import wing_nav as nav

    return bool(nav.TARGETS.get("home_minigame_menu", (None, False))[1])


# ---- profile -> Wing run config ---------------------------------------------------------------------------------
def produce_attach(item, interface=None):
    """Merged ``SC.Produce.Config.*`` attach of the AutoProduce task item, exactly as run_tasks resolves it."""
    from tools import run_tasks

    interface = interface or run_tasks.load_interface()
    plan, = run_tasks.plan_tasks(interface, {"preset": None, "tasks": [{"name": TASK, "option": item.get("option") or {}}]})
    return {k: v for node, fields in plan.pipeline_override.items() if node.startswith("SC.Produce.Config.")
            for k, v in (fields.get("attach") or {}).items()}


def _raw_option(schema, profile, key, fallback="No"):
    """A UI-only option of the AutoProduce entry (never part of the run config), after the legacy migration."""
    from tools import ui_schema

    migrated = ui_schema.migrate_legacy_profile(schema, profile)
    item = next((t for t in migrated.get("tasks") or [] if isinstance(t, dict) and t.get("name") == TASK), None)
    default = (schema["options"].get(key) or {}).get("default_case", fallback)
    return ((item or {}).get("option") or {}).get(key, default)


def _raw_new_run(schema, profile):
    """Always True since 2026-10-13 (the switch has no 「No」 case any more; a saved "No" is migrated away)."""
    return _raw_option(schema, profile, NEW_RUN_OPTION, "Yes") == "Yes"


def wing_team_s_cards(schema, profile, *, interface=None):
    """The S cards of the profile's W.I.N.G. (AutoProduce) team, ticked or not; () when it has none or is invalid."""
    from agent.core import produce_config as pc
    from tools import ui_schema

    item = next((t for t in profile.get("tasks") or [] if isinstance(t, dict) and t.get("name") == TASK), None)
    if item is None:
        return ()
    try:
        config = ui_schema._profile_to_run_config(schema, {**profile, "tasks": [{**item, "enabled": True}]})
        team = pc.ProduceConfig.from_task_options(dict(produce_attach(config["tasks"][0], interface))).team
    except ValueError:
        return ()
    return tuple(c for c in team.get("s_cards") or () if c)


def auto_team_choice(schema, profile, owned_cards, *, interface=None, snapshot=None, cards=None, scenario="",
                     colors=None):
    """Auto 自动编队: the agent/core/auto_team.py team from the scanned owned cards. The P avoids the
    user's usual W.I.N.G. P idols (the AutoProduce P card and the last W.I.N.G. team read). ``p_card`` None with a note
    when no team can be built (``owned_cards_missing`` before a 持有卡 scan)."""
    from agent.core import auto_team
    from agent.core import produce_config as pc
    from agent.core.card_inventory import sanitize_owned_cards
    from tools import ui_schema

    owned = sanitize_owned_cards(owned_cards)
    if not owned or not owned["p"] or not owned["s"]:
        return {"p_card": None, "s_cards": [], "guest": None, "idols": [], "mastery": {}, "notes": ["owned_cards_missing"]}
    if cards is None:
        from agent.core.team_recommend import load_library
        cards = load_library()["cards"]
    usual = set()
    item = next((t for t in profile.get("tasks") or [] if isinstance(t, dict) and t.get("name") == TASK), None)
    if item is not None:
        try:
            config = ui_schema._profile_to_run_config(schema, {**profile, "tasks": [{**item, "enabled": True}]})
            usual.add(pc.ProduceConfig.from_task_options(dict(produce_attach(config["tasks"][0], interface))).team
                      .get("p_card"))
        except ValueError:
            pass
    snap = load_snapshot() if snapshot is None else clean_snapshot(snapshot)
    if snap:
        usual.add(snap["p_card"])
    avoid = {cards[c]["idol"] for c in usual if c in cards and cards[c].get("idol")}
    return auto_team.choose(owned, cards, avoid_idols=avoid, scenario=scenario, colors=colors)


def team_prep_run_config(schema, profile, team, unit, *, interface=None, owned_cards=None,
                         memory_file=TEAM_PREP_MEMORY_FILE):
    """Auto Produce 自动编队 (agent/core/auto_team.py): a supervised W.I.N.G. run from Home that only
    edits ユニット``unit`` into ``team`` (P / 4 S / guest, the W.I.N.G. team edit) and stops on that unit page
    (RunOptions.edit_only -> ``team_edit_done``). It never reaches the start: no spend action, no spend authority. The
    W.I.N.G. settings (difficulty, SP mode) come from the profile's AutoProduce item, ticked or not."""
    from agent.core import produce_config as pc
    from agent.core.card_inventory import sanitize_owned_cards
    from tools import run_tasks, ui_schema

    _require(isinstance(team, dict) and P_ID.match(str(team.get("p_card") or ""))
             and len(team.get("s_cards") or ()) == 4 and all(S_ID.match(str(c)) for c in team["s_cards"])
             and S_ID.match(str(team.get("guest") or "")), "自动编队未选出完整队伍")
    _require(type(unit) is int and 1 <= unit <= 20, "ユニット须为 1–20")
    prep = copy.deepcopy(profile)
    item = next((t for t in prep.get("tasks") or [] if isinstance(t, dict) and t.get("name") == TASK),
                {"name": TASK, "option": {}})
    prep["tasks"] = [{**item, "enabled": True}]
    config = ui_schema._profile_to_run_config(schema, prep)
    attach = produce_attach(config["tasks"][0], interface)
    produce = pc.ProduceConfig.from_task_options(dict(attach))
    launch = pc.launch_options(attach)
    data = copy.deepcopy(dict(produce.data))
    data["team"] = {**dict(produce.team), "mode": "manual", "p_card": str(team["p_card"]),
                    "s_cards": [str(c) for c in team["s_cards"]], "guest": str(team["guest"])}
    data["unit_preset"] = f"unit{unit}"
    data = pc.ProduceConfig.from_dict(data).data
    option = {"mode": "start", "from_home": True, "sp_mode": launch["sp_mode"], "ocr": "roi", "produce_config": data,
              "memory_file": _rel(memory_file), "team_edit": True, "edit_only": True, "picker_sort": True}
    _require(owned_cards is None or isinstance(owned_cards, dict), "持有卡资料须为对象")
    owned = sanitize_owned_cards(owned_cards)
    if owned is not None:
        option["owned_cards"] = owned
    run = {"preset": None, "input_enabled": True, "tasks": [{"name": run_tasks.WING_TASK, "option": option}],
           "session_max_minutes": TEAM_PREP_MINUTES, "task_timeout_s": TEAM_PREP_TIMEOUT_S, "allow_spend": []}
    run_tasks.supervised_task_config(run, live=True, offline=False, simulate_input=False)
    return run


def wing_run_config(schema, profile, *, allow_produce_spend=False, interface=None, snapshot=None,
                    memory_file=MEMORY_FILE, ledger=None, team_edit=False, read_team=False, owned_cards=None,
                    proposal=None, wanted=None, halo_book_plan=None):
    """UI profile -> (run config for ``run_tasks --live --supervised-task``, info). Raises ValueError with a short
    Chinese reason when the profile cannot start W.I.N.G. (nothing has run)."""
    from agent.core import produce_config as pc
    from tools import run_tasks, ui_schema

    config = ui_schema._profile_to_run_config(schema, profile)
    enabled = [t for t in config["tasks"] if t.get("enabled")]
    _require([t["name"] for t in enabled] == [TASK], "W.I.N.G. 需要单独运行：只勾选 W.I.N.G.")
    item = enabled[0]
    attach = produce_attach(item, interface)
    produce = pc.ProduceConfig.from_task_options({k: v for k, v in attach.items()})
    launch = pc.launch_options(attach)
    halo = launch["scenario"] == "halo"
    _require(produce.goal != "weekly_count", "周常用「Auto Produce·周常」任务")
    team = dict(produce.team)
    # 推荐编队 (2026-10-14): the UI adopted the recommendation (P + 4 S + Guest) into the team keys;
    # a new Produce edits the chosen unit into it. Incomplete = the recommendation is not ready.
    recommend = team.get("mode") == "auto"
    if recommend:
        _require(team.get("p_card") and len([c for c in team.get("s_cards") or () if c]) == 4 and team.get("guest"),
                 "推荐编队未生成：等推荐完成，或改用指定ユニット")
        team["mode"] = "manual"
    _require(team.get("p_card"), "未选择 P 偶像")
    new_run = _raw_new_run(schema, profile)
    _require(new_run and bool(allow_produce_spend), "未授权开新局（消耗：体力 30）：在「Produce」页点开始")
    snap = load_snapshot() if snapshot is None else clean_snapshot(snapshot)
    snapshot_check = _raw_option(schema, profile, SNAPSHOT_CHECK_OPTION, "Yes") != "No"
    used_snapshot = None
    searching = new_run and launch["unit_search"] and not recommend
    # Team editing must use the current selection; the saved unit describes the team before that edit. 用推荐 checks
    # the recommended cards; 「核对上次读到的 S/Guest」= No checks the P card only (the unit is read again).
    if not team_edit and not recommend and snapshot_check and snap and snap["p_card"] == team["p_card"] \
            and (searching or f"unit{snap['unit']}" == produce.data["unit_preset"]):
        team.update(s_cards=list(snap["s_cards"]), guest=snap["guest"])
        used_snapshot = snap
    data = copy.deepcopy(dict(produce.data))
    data["team"] = team
    data = pc.ProduceConfig.from_dict(data).data
    # 连跑 (「Produce次数」 > 1): the next run starts only after Maa itself pressed 獲得報酬 OK and
    # reached home (tools/wing_segments.next_run_verdict); with the handover every run would stop there.
    if halo:
        data["caps"]["max_runs"] = 1           # say "Halo" (H2c batch 5): one Produce per start; 局数 is hidden there
    runs = data["caps"]["max_runs"]
    _require(runs == 1 or pc.wing_params(pc.ProduceConfig.from_dict(data))["end"]["press_ok"] is True,
             "连跑需开启「读取后自动确认」")
    pending = spend_pending(ledger)
    spend = new_run and "unreadable" not in pending
    mode = "start" if spend else "attach"
    option = {"mode": mode, "from_home": True, "sp_mode": launch["sp_mode"], "ocr": "roi", "produce_config": data,
              "memory_file": _rel(memory_file), "verify_resume": True}
    # The host's saved ownership snapshot is separate from generic UI prefs / task options.
    from agent.core.card_inventory import sanitize_owned_cards

    _require(owned_cards is None or isinstance(owned_cards, dict), "持有卡资料须为对象")
    owned = sanitize_owned_cards(owned_cards)
    if owned is not None:
        option["owned_cards"] = owned
    # 推荐编队: a new Produce overwrites the chosen unit (team_edit); attach (uncertain earlier start) only verifies.
    team_edit = bool(team_edit or (recommend and mode == "start"))
    if mode == "start" and launch["unit_search"] and not recommend and not team_edit:
        option["unit_search"] = True
    if team_edit:
        _require(mode == "start", "编队编辑只用于开新一局（mode: start）")
        _require(len([c for c in team.get("s_cards") or () if c]) == 4 and team.get("guest"),
                 "编队编辑需要 4 张 S 卡与ゲスト")
        option["team_edit"] = True
        # 2026-10-14: sort the サポート picker by the wanted card's attribute before scanning (wing_nav picker_sort;
        # disabled sort targets make the planner skip sorting, so this never adds an unverified tap)
        option["picker_sort"] = True
    if read_team:
        option["read_team"] = True
    # 开局前大富豪 (start mode only; wing_nav plays it from the home screen before プロデュース, from_home is always set)
    minigame, minigame_reason = False, "attach"
    if mode == "start":
        if launch["minigame"] == "auto" and recommend and proposal is None:
            proposal = load_proposal()
        minigame, minigame_reason = minigame_choice(launch["minigame"], team, proposal, recommend)
    if minigame:
        option["minigame"] = "before_start"
    if launch["settings_fix"]:
        option["settings_fix"] = True             # 自动改游戏设置 (wing_nav; not attempted while its targets are unverified)
    if pc.wing_params(pc.ProduceConfig.from_dict(data))["end"].get("reroll") is True:
        option["allow_reward_reroll"] = True      # planning authority only; the 再抽選 targets stay disabled
    if halo:
        # say "Halo" (H2c batch 5, 2026-11-04): the dig route (the only one since 2026-11-07) and the book source; its
        # goals come from the wanted list (prefs.knowhow_wanted).
        option["halo_route"], option["halo_book"] = launch["halo_route"], launch["halo_book"]
        if launch["halo_book"] == "target":
            # 带书「ノウハウ页指定」: the book saved on the ノウハウ page (prefs.halo_book)
            from agent.core import weekly_auto

            plan = weekly_auto.load_halo_book() if halo_book_plan is None else halo_book_plan
            _require(plan is not None, "带书「ノウハウ页指定」需要先在ノウハウ页点「设为 Halo 带书」")
            option["halo_book_plan"] = plan
        if launch["halo_route"] == "dig":
            from agent.core import halo_policy, knowhow_books

            goals = halo_policy.goals_from_wanted(knowhow_books.load_wanted_from_profile() if wanted is None else wanted)
            _require(goals is None or goals.any(),
                     "say \"Halo\" 需要想要列表里有 施設効率相談、パッシブスキル延長 或 昂揚")
            if goals is not None:
                option["halo_goals"] = goals.as_dict()
    run = {"preset": None, "input_enabled": True, "tasks": [{"name": run_tasks.WING_TASK, "option": option}],
           "session_max_minutes": SESSION_MINUTES, "task_timeout_s": TASK_TIMEOUT_S,
           "allow_spend": [run_tasks.WING_SPEND_ACTION] if spend else []}
    run_tasks.supervised_task_config(run, live=True, offline=False, simulate_input=False)
    info = {"mode": mode, "scenario": launch["scenario"], "halo_route": option.get("halo_route"),
            "halo_book": option.get("halo_book"), "halo_goals": option.get("halo_goals"),
            "p_card": team["p_card"], "unit": data["unit_preset"], "runs": runs,
            "difficulty": data["difficulty"], "primary_stat": (data.get("manual") or {}).get("primary_stat"),
            "primary_stat_auto": team.get("primary_stat") == "auto", "unit_search": "unit_search" in option,
            "team_edit": bool(option.get("team_edit")), "read_team": bool(option.get("read_team")),
            "picker_sort": bool(option.get("picker_sort")),
            "snapshot": used_snapshot is not None, "spend": spend, "sp_mode": option["sp_mode"],
            "team_source": "recommend" if recommend else "search" if "unit_search" in option else "unit",
            "overwrite_unit": data["unit_preset"] if option.get("team_edit") else None,
            "snapshot_check": snapshot_check, "verify_resume": True,
            "minigame": "before_start" if minigame else "off", "minigame_reason": minigame_reason,
            "settings_fix": bool(option.get("settings_fix")),
            "spend_uncertain": bool(new_run and pending), "ledger_pending": pending,
            # P1-3: the unverified home/tab targets (wing_nav home_produce / produce_tab_train) stop a new run from the
            # home screen; and an attach run never sees ユニット編成 unless the user leaves the game on it.
            "new_run_needs_produce_select": spend,
            "team_snapshot_source": "unit_select" if mode == "start" else "unit_select_if_open"}
    return run, info


def spend_pending(ledger=None):
    """Keys of unresolved プロデュース開始 entries (unreadable ledger -> ["unreadable"], never spend on doubt)."""
    from agent.core import wing_ledger as wl
    from tools import run_tasks

    ledger = ledger if isinstance(ledger, wl.SpendLedger) else wl.SpendLedger(run_tasks.WING_SPEND_LEDGER)
    try:
        return [e["key"] for e in ledger.pending()]
    except wl.LedgerError:
        return ["unreadable"]


def check_memory(config_data, mode, memory_file=MEMORY_FILE):
    """Before a UI run (any mode; review P1-1: a ``start`` request no longer archives, the Agent archives only after
    its own start is confirmed). A memory bound to another P card belongs to another Produce: archived here (the Agent
    would do the same). A memory of the same Produce written for another strategy/goal is refused (RunMemory.restore
    would refuse it). Returns the archive path or None."""
    path = Path(memory_file)
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("W.I.N.G. 运行记录无法读取") from None
    from agent.core import produce_config as pc
    from agent.core import wing_custom as wcu
    from agent.core import wing_nav as nav

    memory = nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(config_data))
    ident = saved.get("produce") if isinstance(saved, dict) and isinstance(saved.get("produce"), dict) else None
    if ident is not None and memory.team_slots.get("P") and ident.get("p_card") != memory.team_slots["P"]:
        return wcu.archive_memory(path, "mismatch")
    # 2026-10-14: a record never bound to a Produce and holding no progress (left by a run that never started one)
    # carries nothing to resume; archive it instead of refusing every later run over its old strategy/goal.
    differs = saved.get("strategy", memory.strategy) != memory.strategy or saved.get("goal", memory.goal) != memory.goal
    if differs and ident is None and not any(saved.get(k) for k in ("wins", "promise", "wing_won", "true_end_seen", "live_cards",
                                                       "stat_caps", "lesson_probe", "cheer", "live_trend")):
        return wcu.archive_memory(path, "unbound")
    if saved.get("strategy", memory.strategy) != memory.strategy or saved.get("goal", memory.goal) != memory.goal:
        raise ValueError("进行中的 W.I.N.G. 与当前主属性/目标不同：改回原设置；那一局已结束时，"
                         "把 dev_assets/private/wing-ui/memory.json 移走后再开始")
    return None
