#!/usr/bin/env python3
"""UI engine child (U2): the C# UiEngineSupervisor starts this script, never run_tasks
directly, so the profile -> run config conversion is exactly ``tools/ui_schema.profile_to_run_config``.

The host writes a sanitised UI run profile (task order, enabled flags, option cases) to a fresh private run folder and
passes private ``--events`` / ``--stop-file`` / ``--summary`` paths. This script converts the profile, then calls
``tools/run_tasks.main`` in read-only mode against either the host's capture session (``--session``) or private
offline frames (``--offline-frames``).

U2 boundaries (the UI cannot widen them): no ``--live``, no ``--simulate-input``, and the ``allow_spend`` actions that
``profile_to_run_config`` derives from the per-task spend options are recorded but NOT forwarded (spending from the UI
arrives with U5 and still needs the policy, the host input gate and the per-frame SpendGate). Exit codes follow
run_tasks (0 all succeeded / 1 failed or stopped / 2 configuration refused).

2026-10-11 exception, like the host's 「扫描持有卡」 (CardScan): ``--wing`` turns the profile's AutoProduce settings into
the CLI's own supervised Wing config (tools/ui_wing.py) and runs ``run_tasks --live --supervised-task`` against the
host session. The host passes ``--wing`` only after the page's explicit confirmation and its own readiness gate;
``--allow-produce-spend`` only when the host re-derived the consent from the saved profile (UI-only switch
「开新一局（体力 30）」, default off) AND the page confirmed it for this run. run_tasks still needs the project policy,
the risk reference, the host's pipeline-input gate and a one-shot frame-bound SpendGate grant for the start.
2026-10-11 review P1-4: --wing loops segments (wing_main; rules in tools/wing_segments.py) on one event stream,
attach-only after the first segment; an unresolved spend ledger entry (agent.core.wing_ledger) of an earlier run is
rechecked by the Agent on Home before any new start, one left by this loop ends the loop.

ANN-17: ``--daily`` is the 任务 page's normal 开始. The host passes it only after the page's one
confirmation (which lists the tasks that will spend) and its readiness gate (the same as W.I.N.G.); this script runs the
ticked tasks in the profile's order as ONE ``run_tasks --live --supervised-sequence`` run. Each task keeps its own
supervised scope (families, click cap, its own consuming action only while it runs); ``allow_spend`` holds only the
spend actions ``ui_schema`` derives from the ticked tasks' own spend options. Without ``--daily``/``--wing`` the run
stays read-only as before (developer: 只识别 / offline replay).

Auto 自动编队: when WeeklyProduce is ticked with 编队 = 自动编队, ``--daily`` runs the tasks before it,
then a W.I.N.G. team-edit prep (tools/ui_wing.team_prep_run_config: edit_only, no spend) that writes the
agent/core/auto_team.py team into the chosen ユニット and returns to プロデュース選択, then WeeklyProduce (checking that
P card on the Auto unit page) and the tasks after it -- one event stream, one summary.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE = ROOT / "dev_assets" / "private"

from agent.core.run_control import RunControl  # noqa: E402
MAX_PROFILE_BYTES = 256 * 1024
FORWARDED_CONFIG_KEYS = {"tasks", "task_timeout_s"}


def private_file(raw, suffix):
    path = Path(os.path.abspath(raw))
    root = Path(os.path.abspath(PRIVATE))
    if not path.is_relative_to(root) or path.suffix.lower() != suffix:
        raise ValueError(f"文件须位于 dev_assets/private 且扩展名为 {suffix}")
    return path


def build_run_config(profile, schema=None):
    """profile -> run config via ui_schema.profile_to_run_config; returns (config, deferred_allow_spend)."""
    from tools import ui_schema

    schema = schema if schema is not None else ui_schema.export_schema()
    config = ui_schema.profile_to_run_config(schema, profile)
    deferred = list(config.pop("allow_spend", []))
    unexpected = set(config) - FORWARDED_CONFIG_KEYS
    if unexpected:
        raise ValueError(f"运行配置含不允许的字段: {sorted(unexpected)}")
    if not any(item.get("enabled") for item in config["tasks"]):
        raise ValueError("没有启用的任务")
    return config, deferred


def run_tasks_argv(config_path, args, *, daily=False):
    argv = ["--config", str(config_path), "--events", str(args.events), "--stop-file", str(args.stop_file),
            "--summary", str(args.summary)]
    if args.session is not None:
        argv += ["--session", str(args.session)]
    else:
        argv += ["--offline-frames", str(args.offline_frames)]
    if daily:
        argv += ["--live", "--supervised-sequence"]
    return argv


def daily_run_config(profile, schema=None):
    """ANN-17: profile -> supervised daily sequence config (tools/run_tasks.sequence_run_config). The
    ``allow_spend`` actions are exactly those ``ui_schema`` derives from the ticked tasks' own spend options; every
    consuming click still needs the project policy, the host's pipeline-input gate and a frame-bound SpendGate grant."""
    from tools import run_tasks, ui_schema

    schema = schema if schema is not None else ui_schema.export_schema()
    config = ui_schema.profile_to_run_config(schema, profile)
    unexpected = set(config) - FORWARDED_CONFIG_KEYS - {"allow_spend"}
    if unexpected:
        raise ValueError(f"运行配置含不允许的字段: {sorted(unexpected)}")
    if any(item.get("enabled") and item.get("name") == "AutoProduce" for item in config["tasks"]):
        raise ValueError("W.I.N.G. 需要单独运行：只勾选 W.I.N.G.")
    sequence = run_tasks.sequence_run_config(config)
    run_tasks.supervised_sequence_config(sequence, live=True, offline=False, simulate_input=False)
    return sequence


def daily_main(args, *, run=None, sleep=None, clock=None, interface=None):
    """``--daily``: the 任务 page's normal 开始 -- the ticked daily tasks, in the profile's (preset) order, in one
    ``run_tasks --live --supervised-sequence`` run against the host session. The host passes it only after the page's
    one confirmation (listing the spending tasks) and its own readiness gate. With Auto 自动编队 the run is split
    around the team-edit prep (``daily_team_main``)."""
    try:
        if args.session is None:
            raise ValueError("实机日常只能在游戏窗口中运行")
        profile_path = private_file(args.profile, ".json")
        private_file(args.summary, ".json")
        private_file(args.events, ".jsonl")
        if profile_path.stat().st_size > MAX_PROFILE_BYTES:
            raise ValueError("运行档案过大")
        from tools import ui_schema

        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        # 自动编队 modelling data (the host's saved ownership snapshot); never part of the run config.
        owned_cards = profile.pop("owned_cards", None) if isinstance(profile, dict) else None
        schema = ui_schema.export_schema()
        target = knowhow_target(profile)
        training = training_default_card(schema, profile, owned_cards, interface=interface)
        config = daily_run_config(profile, schema)
        prep = auto_team_prep(schema, profile, config, owned_cards, interface=interface,
                              colors=(target or {}).get("colors"))
        config_path = profile_path.with_name("run-config.json")
        with config_path.open("x", encoding="utf-8") as handle:  # fresh run folder; never overwrite
            json.dump(config, handle, ensure_ascii=False, indent=2)
        if prep is not None and prep.get("config") is not None:
            with profile_path.with_name("team-prep-config.json").open("x", encoding="utf-8") as handle:
                json.dump(prep["config"], handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # refused before anything ran; nothing is retried
        print(json.dumps({"passed": False, "error": str(exc)[:500]}, ensure_ascii=False))
        return 2
    print(json.dumps({"daily": [t["name"] for t in config["tasks"]], "allow_spend": config["allow_spend"],
                      **({"knowhow_target": target} if target else {}),
                      **({"training_card": training} if training else {}),
                      **({"auto_team": {k: prep[k] for k in ("unit", "team", "error") if k in prep}} if prep else {})},
                     ensure_ascii=False), file=sys.stderr)
    from tools import run_tasks

    if prep is None and AUTO_TEAM_TASK not in {t["name"] for t in config["tasks"]}:
        code = run_tasks.main(run_tasks_argv(config_path, args, daily=True))
    else:   # WeeklyProduce runs on its own: a collected Auto result is followed by the new-book read
        code = daily_team_main(args, config, prep, run=run, sleep=sleep, clock=clock)
    note_auto_start(args.summary, target, (prep or {}).get("team"))
    return code


def weekly_collected(summary):
    """Whether WeeklyProduce collected a finished Auto's result in this daily part (its new books are unread)."""
    task = next((t for t in (summary or {}).get("tasks") or () if t.get("name") == AUTO_TEAM_TASK), {})
    return any(isinstance(r, dict) and r.get("kind") == "weeklyauto" and r.get("collected")
               for r in task.get("reports") or ())


def auto_read(args, control, run):
    """The new-book read after a collected Auto: one supervised KnowhowScan top-up (newest, keep plan reported) as a
    side run on the daily stream, then the books go into the Auto 词条记录 (``note_auto_read``). Never retried."""
    from tools import wing_segments as ws

    summary_path = Path(args.summary).with_name("auto-read-summary.json")
    try:
        summary = control.side_run(lambda: run(ws.knowhow_after_config(None, plan=True), session=args.session,
                                               summary_path=summary_path, live=True, supervised_task=True,
                                               control=control, live_status=None))
    except Exception as exc:  # the launcher refused or failed: report it, never retry
        return {"status": "error", "outcome": str(exc)[:200]}
    result = ws.knowhow_after_summary(summary)
    row = note_auto_read(summary)
    result["logged"] = row is not None
    if row is not None:
        result["wanted_found"] = row["wanted_found"]
    return result


def note_auto_read(summary, *, log_path=None, wanted=None, now=None):
    """Auto 词条记录: the books of the newest-book read (its KnowhowScan record) logged against the newest start
    without a read -- the Auto this daily run just collected (its own new start is logged only after the run)."""
    import datetime as _dt

    from agent.core import knowhow_auto_log as kal
    from agent.core import knowhow_books as kb

    try:
        task = ((summary or {}).get("tasks") or [{}])[0]
        report = next((r for r in reversed(task.get("reports") or []) if isinstance(r, dict) and r.get("record")), None)
        if report is None:
            return None
        books = json.loads(Path(report["record"]).read_text(encoding="utf-8")).get("books") or []
        path = log_path or kal.LOG_PATH
        pending = kal.pending(kal.load(path))
        if not books or not pending:
            return None
        stamp = (now or _dt.datetime.now().astimezone()).isoformat(timespec="seconds")
        row = kal.read_entry(pending[-1], books, wanted=kb.load_wanted_from_profile() if wanted is None else wanted,
                             at=stamp)
        kal.append(row, path)
        return row
    except (OSError, ValueError):
        return None


def note_auto_start(summary_path, target, team, *, log_path=None, now=None):
    """Auto 词条记录: a started Auto goes into agent/core/knowhow_auto_log; the books of the next
    newest-book read are matched to it. Best effort: the run result stands either way."""
    import datetime as _dt

    from agent.core import knowhow_auto_log as kal

    try:
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        stamp = (now or _dt.datetime.now().astimezone()).isoformat(timespec="seconds")
        row = kal.start_entry(summary, target=target, team=team, at=stamp)
        if row is not None:
            kal.append(row, log_path or kal.LOG_PATH)
        return row
    except (OSError, ValueError):
        return None


AUTO_TEAM_TASK = "WeeklyProduce"
AUTO_TEAM_PREP_MINUTES = 30.0     # wall-clock cap of the team-edit prep segments
# 剧本「按目标词条」: resolved here into one of the panel's scenario cases before the run config is built.
SCENARIO_OPTION, TARGET_CASE, TARGET_FALLBACK = "Auto核对剧本", "按目标词条", "WING"
SCENARIO_CASES = {"WING": "W.I.N.G.", "FANS": "ファン感謝祭", "GRAD": "G.R.A.D.", "LP": "Landing Point",
                  "STEP": "S.T.E.P."}


def knowhow_target(profile, *, wanted=None, books=None):
    """None unless the ticked WeeklyProduce has 剧本 = 按目标词条; else sets that option in ``profile`` to the scenario
    ``agent/core/knowhow_auto_plan.plan`` picks from the saved wanted list and the scanned library (W.I.N.G. when no
    missing entry is an Auto one) and returns {"scenario", "targets", "colors", "fallback", "manual"}."""
    item = next((t for t in (profile or {}).get("tasks") or () if t.get("name") == AUTO_TEAM_TASK and t.get("enabled")),
                None)
    option = (item or {}).get("option")
    if not isinstance(option, dict) or option.get(SCENARIO_OPTION) != TARGET_CASE:
        return None
    from agent.core import knowhow_auto_plan, knowhow_books

    wanted = knowhow_books.load_wanted_from_profile() if wanted is None else wanted
    books = knowhow_books.load_inventory().books.values() if books is None else books
    result = knowhow_auto_plan.plan(wanted, books)
    scenario = result["scenario"] or TARGET_FALLBACK
    option[SCENARIO_OPTION] = SCENARIO_CASES[scenario]
    return {"scenario": scenario, "targets": result["targets"], "colors": result["colors"],
            "fallback": result["scenario"] is None,
            "manual": [e["name"] for e in result["entries"] if e["status"] == "manual"]}


def training_readings(path=None):
    """{card id: {"level", "limit_break"}} from the private passive reading store; {} when missing."""
    path = Path(path) if path else ROOT / "dev_assets" / "private" / "readings" / "readings.json"
    try:
        cards = json.loads(path.read_text(encoding="utf-8")).get("cards") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(k): v for k, v in cards.items() if isinstance(v, dict)} if isinstance(cards, dict) else {}


def training_name_conflicts(cards, manifest_path=None):
    """Ids whose art-manifest and card-library names cross (training_task.swapped_names); empty without a manifest."""
    from agent.core import training_task as tt

    path = Path(manifest_path) if manifest_path else ROOT / "ui" / "game-assets" / "manifest.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    manifest = {str(c.get("id")): tt.card_name(c) for c in [*doc.get("p_cards", []), *doc.get("cards", [])]
                if isinstance(c, dict) and tt.card_name(c)}
    library = {str(k): v.get("name") for k, v in cards.items() if isinstance(v, dict) and v.get("name")}
    return tt.swapped_names(manifest, library)


def training_default_card(schema, profile, owned_cards, *, interface=None, snapshot=None, cards=None, readings=None):
    """训练卡留空 (agent/core/training_pick.py): writes the chosen S card id into the ticked Training
    item of ``profile`` and returns the pick; None (option untouched, in-game default mode) when Training is not
    ticked, a card is already chosen, or no owned card fits."""
    from agent.core import training_pick
    from agent.core.card_inventory import sanitize_owned_cards
    from tools import ui_wing

    item = next((t for t in profile.get("tasks") or [] if isinstance(t, dict) and t.get("name") == "Training"
                 and t.get("enabled")), None)
    option = item.get("option") if item else None
    if not isinstance(option, dict) or option.get("训练卡"):
        return None
    owned = sanitize_owned_cards(owned_cards)
    if not owned or not owned["s"]:
        return None
    if cards is None:
        from agent.core.team_recommend import load_library
        cards = load_library()["cards"]
    team = list(ui_wing.wing_team_s_cards(schema, profile, interface=interface))
    snap = ui_wing.load_snapshot() if snapshot is None else ui_wing.clean_snapshot(snapshot)
    if snap:
        team += snap["s_cards"]
    chosen = training_pick.pick(owned, cards, team, training_readings() if readings is None else readings,
                                skip=training_name_conflicts(cards))
    if chosen is not None:
        option["训练卡"] = chosen["id"]
    return chosen


def auto_team_prep(schema, profile, config, owned_cards, *, interface=None, snapshot=None, cards=None, colors=None):
    """Auto 自动编队: None unless WeeklyProduce is ticked with 编队 = 自动编队; else {"unit", "team", "config"} (the
    W.I.N.G. team-edit prep run config) or {"unit", "team", "error"} when no team can be built (then WeeklyProduce
    fails without running and the other ticked tasks still run)."""
    from agent.core.weekly_auto import WeeklyAutoConfig
    from tools import run_tasks, ui_wing

    item = next((t for t in config["tasks"] if t.get("name") == AUTO_TEAM_TASK), None)
    if item is None:
        return None
    interface = interface or run_tasks.load_interface()
    plan, = run_tasks.plan_tasks(interface, {"preset": None, "tasks": [item]})
    weekly = run_tasks.task_config(plan, "weekly_produce.json", WeeklyAutoConfig)
    if weekly.team != "auto":
        return None
    team = ui_wing.auto_team_choice(schema, profile, owned_cards, interface=interface, snapshot=snapshot, cards=cards,
                                    scenario=weekly.expect_scenario, colors=colors)
    out = {"unit": weekly.unit, "team": team}
    if team.get("p_card") is None:
        out["error"] = "auto_team_" + ((team.get("notes") or ["unbuilt"])[-1])
        return out
    out["config"] = ui_wing.team_prep_run_config(schema, profile, team, weekly.unit, interface=interface,
                                                 owned_cards=owned_cards)
    return out


def split_sequence(config, name=AUTO_TEAM_TASK):
    """(before, task, after) supervised sequence configs around ``name`` (None for an empty part; ``task`` holds only
    ``name``); each part keeps only its own tasks' spend actions and derives its own families."""
    from tools import run_tasks

    names = [t["name"] for t in config["tasks"]]
    index = names.index(name)

    def part(items):
        if not items:
            return None
        out = {k: copy.deepcopy(v) for k, v in config.items() if k not in ("tasks", "families", "allow_spend")}
        out["tasks"] = copy.deepcopy(items)
        out["allow_spend"] = sorted(set(config.get("allow_spend") or ()) & run_tasks.selected_spend_actions(items))
        return out

    return part(config["tasks"][:index]), part(config["tasks"][index:index + 1]), part(config["tasks"][index + 1:])


def daily_team_main(args, config, prep, *, run=None, sleep=None, clock=None):
    """``--daily`` with WeeklyProduce ticked: the tasks before it, with Auto 自动编队 (``prep``) the team-edit prep
    (W.I.N.G. segments, ``_wing_run`` rules, no spend), then WeeklyProduce alone (``expect_p_card``), the new-book read
    when it collected a finished Auto (``auto_read``), and the tasks after it. A prep that did not end with
    ``team_edit_done`` fails WeeklyProduce and skips the later tasks (the game may still be inside the W.I.N.G.
    setup); a team that could not be built fails WeeklyProduce only. Runs are ``WING_PAUSE_S`` apart (the host opens
    a fresh run window only after its quiet period)."""
    import time

    from agent.core.wing_custom import archive_memory
    from tools import run_tasks, ui_wing
    from tools import wing_segments as ws

    run = run or run_tasks.run
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    before, weekly, after = split_sequence(config)
    control = DailyControl(events_path=args.events, stop_file=args.stop_file)
    folder = Path(args.summary).parent
    parts, tail = [], []
    report = {"kind": "auto_team", "unit": prep["unit"], "team": prep["team"]} if prep is not None else {}

    def pause():
        waited = 0.0
        while waited < WING_PAUSE_S and not control.poll():
            sleep(0.5)
            waited += 0.5
        return not control.poll()

    def daily(part, index, **kw):
        return run(part, session=args.session, summary_path=folder / f"daily-{index}-summary.json", live=True,
                   supervised_sequence=True, control=control, live_status=True, **kw)

    def weekly_then_after(**kw):
        parts.append(daily(weekly, 2, **kw))
        if weekly_collected(parts[-1]) and pause():
            with control.reading():
                result = auto_read(args, control, run)
            control.note_auto_read(result)
        if after is not None and pause():
            parts.append(daily(after, 3))

    try:
        if before is not None:
            parts.append(daily(before, 1))
        startup = next((t for s in parts for t in s.get("tasks") or [] if t.get("name") == "Startup"), None)
        if prep is None:
            if before is None or pause():
                weekly_then_after()
        elif startup is not None and startup.get("status") != "succeeded":
            # not on Home (maintenance, title not passed): the prep's W.I.N.G. navigation starts from Home
            report["result"] = "startup_failed"
            tail += [control.task_skipped(name, "startup_failed")
                     for name in [AUTO_TEAM_TASK, *(t["name"] for t in (after or {}).get("tasks", []))]]
        elif prep.get("error"):
            report["result"] = prep["error"]
            control.note_auto_team(report)
            tail.append(control.task_failed(AUTO_TEAM_TASK, prep["error"], report))
            if after is not None and pause():
                parts.append(daily(after, 2))
        elif before is None or pause():
            archive_memory(ui_wing.TEAM_PREP_MEMORY_FILE, "previous")   # each prep starts from a fresh memory
            with control.prep():
                segments, last, _verdict, why, _ = _wing_run(args, prep["config"], control, run, sleep, clock,
                                                             clock() + AUTO_TEAM_PREP_MINUTES * 60, ws)
            task = ((last or {}).get("tasks") or [{}])[0]
            done = "wing:team_edit_done" in (task.get("fail_reasons") or [])
            report.update(result="team_edit_done" if done else why, segments=len(segments))
            control.note_auto_team(report)
            if not done:
                tail.append(control.task_failed(AUTO_TEAM_TASK, f"auto_team:{why}", report))
                tail += [control.task_skipped(t["name"], "auto_team_failed") for t in (after or {}).get("tasks", [])]
            elif pause():
                weekly_then_after(task_options={"weekly_auto": {"expect_p_card": prep["team"]["p_card"]}})
    except Exception as exc:  # a launcher refusal or error ends the run; nothing is retried
        print(json.dumps({"daily_team_error": str(exc)[:300]}, ensure_ascii=False), file=sys.stderr)
        control.request_stop("runner_error")
    finally:
        summary = merge_daily(parts, tail, report or None, order=[t["name"] for t in config["tasks"]])
        if control.auto_read is not None:
            summary["auto_read"] = control.auto_read
        status = control.finish_daily(summary)
        summary["run_control"] = control.summary()
        run_tasks.write_summary(summary, args.summary)
    print(json.dumps({"order": summary["order"], "tasks": [(t["name"], t["status"], t.get("fail_reasons"))
                                                           for t in summary["tasks"]],
                      "auto_team": report.get("result"), "auto_read": summary.get("auto_read"), "status": status},
                     ensure_ascii=False))
    return 0 if (summary["failed"] == 0 and not summary["run_control"]["stop_requested"]
                 and summary["run_control"]["events_complete"]) else 1


def merge_daily(parts, tail, report, order=()):
    """One run summary from the daily parts plus the tasks this script itself finished (``tail``), in ``order``."""
    rank = {name: index for index, name in enumerate(order)}
    tasks = sorted([t for s in parts for t in s.get("tasks") or []] + list(tail),
                   key=lambda task: rank.get(task.get("name"), len(rank)))
    for task in tasks:
        if task.get("name") == AUTO_TEAM_TASK and report:
            task.setdefault("reports", [])
            if not any(isinstance(r, dict) and r.get("kind") == "auto_team" for r in task["reports"]):
                task["reports"].append(dict(report))
    base = {k: v for k, v in (parts[-1] if parts else {}).items() if k not in ("tasks", "order", "succeeded", "failed")}
    return {**base, "order": [t["name"] for t in tasks], "tasks": tasks, **({"auto_team": dict(report)} if report else {}),
            "parts": [{"order": s.get("order"), "summary": s.get("summary_path")} for s in parts],
            "succeeded": sum(t.get("status") == "succeeded" for t in tasks),
            "failed": sum(t.get("status") != "succeeded" for t in tasks)}


END_SUMMARY_MODULE = ROOT / "ui" / "game-assets" / "produce-end-last.js"


def end_summary_of(summary):
    """The last ``end_summary`` (produce_end.summary_payload) of a Wing run summary's ``wing`` reports, or None."""
    found = None
    for task in (summary or {}).get("tasks") or []:
        for report in task.get("reports") or [] if isinstance(task, dict) else []:
            if isinstance(report, dict) and report.get("kind") == "wing" and isinstance(report.get("end_summary"), dict):
                found = report["end_summary"]
    return found


def publish_end_summary(summary, path=END_SUMMARY_MODULE, now=None):
    """Write the end payload for the UI result view (ui/components/produce-end.js; local, gitignored; the host copies
    it into its ui folder after the run). Display only: nothing in it authorises any action."""
    import datetime as _dt

    payload = end_summary_of(summary)
    if payload is None:
        return None
    stamp = (now or _dt.datetime.now().astimezone()).isoformat(timespec="seconds")
    data = {"written_at": stamp, "payload": json.loads(json.dumps(payload, ensure_ascii=False, default=str))}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("// Generated by tools/ui_engine.py (local, gitignored): last W.I.N.G. end summary.\nexport default "
                    + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)
    return data


def main(argv=None):
    # 2026-10-15: the host starts this process without a window; Windows 11 EcoQoS ran its OCR 5-10x slower on the
    # i7-12700 E-cores (live Schedule reads 7-13 s vs 0.85 s offline). W.I.N.G. segments run here, not via run_tasks.main.
    from tools.run_tasks import _opt_out_of_power_throttling
    _opt_out_of_power_throttling()
    parser = argparse.ArgumentParser(description="UI 引擎子进程：档案 -> 运行配置 -> run_tasks（只读）。")
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session", type=Path)
    source.add_argument("--offline-frames", type=Path)
    parser.add_argument("--wing", action="store_true", help="监督运行 W.I.N.G.（AutoProduce 设置 -> Wing），需 --session")
    parser.add_argument("--allow-produce-spend", action="store_true", help="仅 --wing：本次允许开新一局（体力 30）")
    parser.add_argument("--daily", action="store_true",
                        help="实机日常（界面「开始」）：按方案顺序监督运行勾选的日常任务，需 --session")
    args = parser.parse_args(argv)
    if args.daily and (args.wing or args.allow_produce_spend):
        print(json.dumps({"passed": False, "error": "--daily 与 --wing 不能同时使用"}, ensure_ascii=False))
        return 2
    if args.daily:
        return daily_main(args)
    if args.wing or args.allow_produce_spend:
        return wing_main(args)
    try:
        profile_path = private_file(args.profile, ".json")
        private_file(args.summary, ".json")
        private_file(args.events, ".jsonl")
        if profile_path.stat().st_size > MAX_PROFILE_BYTES:
            raise ValueError("运行档案过大")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        config, deferred = build_run_config(profile)
        config_path = profile_path.with_name("run-config.json")
        with config_path.open("x", encoding="utf-8") as handle:  # fresh run folder; never overwrite
            json.dump(config, handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # refused before anything ran; nothing is retried
        print(json.dumps({"passed": False, "error": str(exc)[:500]}))
        return 2
    if deferred:
        # stderr note only; the host shows it as a log line. Not an authorization of any kind.
        print(json.dumps({"spend_deferred": sorted(deferred)}, ensure_ascii=False), file=sys.stderr)
    from tools import run_tasks

    return run_tasks.main(run_tasks_argv(config_path, args))


WING_PAUSE_S = 35.0          # between segments: > the host's 30 s quiet period (tools/wing_segments.SEGMENT_PAUSE_S)
WING_TOTAL_MINUTES = 210.0   # loop wall-clock cap; the host allows 4 h + margin for a supervised_wing run
WING_MAX_SEGMENTS = 12
WING_MIN_SEGMENT_S = 300.0   # no new segment with less time than this left
# W.I.N.G. 连跑 (「Produce次数」 2–5): each further run adds WING_EXTRA_RUN_MINUTES to the wall-clock
# cap, never past WING_LOOP_MAX_MINUTES (the host's 8 h run limit, UiEngineSupervisor MaxRunMs, with margin); a run
# starts only with WING_RUN_MIN_MINUTES left.
WING_EXTRA_RUN_MINUTES = 120.0
WING_LOOP_MAX_MINUTES = 465.0
WING_RUN_MIN_MINUTES = 90.0


def wing_main(args, *, run=None, sleep=None, clock=None, ledger=None):
    """``--wing``: profile -> supervised Wing run config -> segments of ``run_tasks --live --supervised-task`` against
    ONE event stream and stop file (2026-10-11 review P1-4), then the team snapshot.

    Segment rules (tools/wing_segments.py): the start authority stays only until a segment attempts (or may have
    attempted) the start, every later one is ``attach`` without spend; an uncertain プロデュース開始 or any non-resumable reason stops the loop; two segments
    without a sent click stop it; segments are ``WING_PAUSE_S`` apart; total time and segment count are capped. A
    resumable segment end is not shown as a failure: the run keeps running, and a loop that ends at its caps reports
    the task as ``paused`` (本段完成，可继续).

    连跑 (「Produce次数」 > 1): after a run Maa itself brought home (獲得報酬 OK) the new books are read
    with the keep plan; a book that fills a wanted entry the library lacked ends the loop (``target_hit``), so does any
    run that did not end at home, an unread or uncertain book step, or too little time. Each further run is a fresh
    config with its own one-start authority, and only when the spend ledger has no unresolved start
    (``wing_segments.next_run_verdict``)."""
    import time

    from tools import wing_segments as ws

    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    try:
        if not args.wing:
            raise ValueError("--allow-produce-spend 只用于 --wing")
        if args.session is None:
            raise ValueError("W.I.N.G. 只能在游戏窗口中运行")
        profile_path = private_file(args.profile, ".json")
        private_file(args.summary, ".json")
        private_file(args.events, ".jsonl")
        if profile_path.stat().st_size > MAX_PROFILE_BYTES:
            raise ValueError("运行档案过大")
        from tools import ui_schema, ui_wing

        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        # Wing-only modelling data; the generic ui_schema profile contract remains unchanged.
        owned_cards = profile.pop("owned_cards", None) if isinstance(profile, dict) else None
        schema = ui_schema.export_schema()

        def build(index):
            config, info = ui_wing.wing_run_config(schema, profile, allow_produce_spend=args.allow_produce_spend,
                                                  ledger=ledger, owned_cards=owned_cards)
            ui_wing.check_memory(config["tasks"][0]["option"]["produce_config"], info["mode"])
            path = profile_path.with_name("run-config.json" if index == 0 else f"run-config-{index + 1}.json")
            with path.open("x", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=2)
            return config, info

        config, info = build(0)
        planned = ws.run_count(config)
        control = SegmentControl(events_path=args.events, stop_file=args.stop_file)
    except Exception as exc:  # refused before anything ran; nothing is retried
        print(json.dumps({"passed": False, "error": str(exc)[:500]}))
        return 2
    print(json.dumps({"wing": info, "runs": planned}, ensure_ascii=False), file=sys.stderr)
    if info.get("spend_uncertain"):
        print(json.dumps({"wing_note": "spend_uncertain: rechecked on Home before any new Produce"}), file=sys.stderr)
    from tools import run_tasks

    run = run or run_tasks.run
    started = time.time()
    total = min(WING_TOTAL_MINUTES + (planned - 1) * WING_EXTRA_RUN_MINUTES, max(WING_TOTAL_MINUTES, WING_LOOP_MAX_MINUTES))
    deadline = clock() + total * 60
    keep = ws.knowhow_keep_settings(config)
    played, hits, loop_why = [], [], None
    if planned > 1:
        control.run_boundary(1, planned, 0)
    last, segments, verdict, why, knowhow_after = None, [], "stop", "not_started", None
    try:
        for index in range(planned):
            if index:
                remaining = deadline - clock()
                go, loop_why = ws.next_run_verdict(played[-1], knowhow_after, remaining_s=remaining,
                                                   min_s=WING_RUN_MIN_MINUTES * 60, stop=control.poll())
                if go:
                    try:
                        config, info = build(index)
                    except Exception as exc:   # refused before this run started: the loop ends, nothing is retried
                        go, loop_why = False, "config_refused"
                        print(json.dumps({"wing_loop_error": str(exc)[:300]}, ensure_ascii=False), file=sys.stderr)
                    else:
                        if not (info.get("mode") == "start" and info.get("spend")) or info.get("ledger_pending"):
                            go, loop_why = False, "spend_pending"   # a start of this loop is unresolved
                if not go:
                    break
                control.run_boundary(index + 1, planned, len(played))
                print(json.dumps({"wing_run": index + 1, "of": planned}), file=sys.stderr)
            segments, last, verdict, why, knowhow_after = _wing_run(args, config, control, run, sleep, clock, deadline,
                                                                    ws, keep=keep, plan=planned > 1)
            played.append(last)
            hits = ws.target_hits(knowhow_after)
            if planned > 1 and hits:
                loop_why = "target_hit"
            if verdict != "done" or loop_why == "target_hit":
                break
        else:
            loop_why = loop_why or "planned_runs"
    finally:
        paused = verdict in ("continue", "cap")
        if planned > 1:   # display readings carry identifiers and counts only (the summary keeps the hit entries)
            control.note_wing_loop({"planned": planned, "finished": sum(1 for s in played if ws.run_finished(s)),
                                    "ended": loop_why or why, "hits": len(hits)})
        status = control.finish(paused=paused)
        if last is not None:
            last = dict(last)
            last["wing_segments"] = {"segments": segments, "ended": why, "verdict": verdict}
            if knowhow_after is not None:
                last["knowhow_after"] = knowhow_after
            if planned > 1:
                last["wing_loop"] = {"planned": planned, "played": len(played),
                                     "finished": sum(1 for s in played if ws.run_finished(s)),
                                     "ended": loop_why or why, "hits": hits}
            if paused and last.get("tasks"):
                last["tasks"] = [dict(last["tasks"][0], status="paused")] + list(last["tasks"][1:])
                last["failed"], last["succeeded"] = 0, 1
            last["run_control"] = control.summary()
            run_tasks.write_summary(last, args.summary)
            try:  # ANN-29: the end payload for the UI result view (display only)
                if publish_end_summary(last) is not None:
                    print(json.dumps({"wing_end_summary": "published"}), file=sys.stderr)
            except Exception as exc:
                print(json.dumps({"end_summary_error": str(exc)[:200]}), file=sys.stderr)
        try:
            ui_wing.record_snapshot(started, info["p_card"])
        except Exception as exc:  # display data only; never changes the run result
            print(json.dumps({"snapshot_error": str(exc)[:200]}), file=sys.stderr)
    print(json.dumps({"wing_segments": segments, "ended": why, "status": status, "wing_loop": loop_why,
                      "runs": len(played)}, ensure_ascii=False))
    if control.reason:
        return 1
    return 0 if verdict == "done" or paused else 1


def _wing_run(args, config, control, run, sleep, clock, deadline, ws, *, keep=None, plan=False):
    """One W.I.N.G. run as segments (the rules of ``wing_main``) and, after a Produce Maa brought home, the new-book
    read. Returns (segments, last summary, verdict, why, knowhow_after)."""
    segments, last, idle, verdict, why = [], None, 0, "stop", "not_started"
    repeats, last_key = 0, None
    knowhow_after = None
    spend_first = bool(config.get("allow_spend"))
    start_pending = True
    for index in range(WING_MAX_SEGMENTS):
        # The start authority stays only while every earlier segment proved no start was attempted (setup only).
        seg_config = config if start_pending else ws.attach_config(config)
        remaining = deadline - clock()
        if index and remaining < WING_MIN_SEGMENT_S:
            verdict, why = "cap", "time_cap"
            break
        seg_config["task_timeout_s"] = min(float(config.get("task_timeout_s", 3600)), max(60.0, remaining))
        seg_summary = Path(args.summary).with_name(f"segment-{len(control.segment_files) + 1}-summary.json")
        control.segment_files.append(seg_summary.name)
        try:
            summary = run(seg_config, session=args.session, summary_path=seg_summary, live=True,
                          supervised_task=True, control=control, live_status=True)
        except Exception as exc:  # the launcher refused or failed this segment: stop, never retry
            verdict, why = "stop", "segment_error"
            print(json.dumps({"wing_segment_error": str(exc)[:300]}), file=sys.stderr)
            break
        last = summary
        task = summary["tasks"][0] if summary.get("tasks") else {}
        sent = ws.sent_clicks(task)
        verdict, why = ws.segment_verdict(task, spend_authorized=start_pending and spend_first)
        key = ws.cause_key(task)
        repeats = repeats + 1 if key and key == last_key else 1
        last_key = key
        if verdict == "continue" and key and repeats >= ws.REPEAT_LIMIT:
            verdict, why = "stop", f"repeated:{key}"     # the same cause again and again is not a small problem
        start_pending = start_pending and ws.start_untouched(task)
        if control.poll():
            verdict, why = "stop", "stop_requested"
        segments.append({"segment": index + 1, "status": task.get("status"), "sent_clicks": sent,
                         "reasons": list(task.get("fail_reasons") or []), "verdict": verdict, "why": why})
        if verdict == "done" and ws.KNOWHOW_AFTER and ws.knowhow_after_wanted(task):
            knowhow_after = knowhow_after_run(args, control, run, sleep, deadline - clock(), ws, keep=keep, plan=plan)
        if verdict != "continue":
            break
        idle = idle + 1 if sent == 0 else 0
        if idle >= ws.MAX_IDLE_SEGMENTS:
            verdict, why = "stop", "no_progress"
            break
        if index + 1 >= WING_MAX_SEGMENTS:
            verdict, why = "cap", "max_segments"
            break
        control.segment_boundary(index + 1, sent)
        waited = 0.0
        while waited < WING_PAUSE_S and not control.poll():
            sleep(0.5)
            waited += 0.5
        if control.poll():
            verdict, why = "stop", "stop_requested"
            break
    return segments, last, verdict, why, knowhow_after


def knowhow_after_run(args, control, run, sleep, remaining_s, ws, keep=None, plan=False):
    """The post-Produce new-book read (tools/wing_segments.KNOWHOW_AFTER): one supervised KnowhowScan top-up in its own
    run window after the pause, on the same event stream; it never changes the finished W.I.N.G. result. ``plan``
    (连跑): the keep plan is reported even with locking and selling off (target hits end the loop)."""
    if remaining_s < ws.KNOWHOW_AFTER_TIMEOUT_S:
        return {"status": "skipped", "outcome": "time_cap"}
    waited = 0.0
    while waited < WING_PAUSE_S and not control.poll():
        sleep(0.5)
        waited += 0.5
    if control.poll():
        return {"status": "skipped", "outcome": "stop_requested"}
    summary_path = Path(args.summary).with_name(f"knowhow-after-{len(control.segment_files)}-summary.json")
    try:
        summary = control.side_run(lambda: run(ws.knowhow_after_config(keep, plan=plan), session=args.session,
                                               summary_path=summary_path, live=True, supervised_task=True,
                                               control=control, live_status=None))
    except Exception as exc:  # the launcher refused or failed: report it, never retry
        return {"status": "error", "outcome": str(exc)[:200]}
    result = ws.knowhow_after_summary(summary)
    control.note_knowhow_after(result)
    return result


class SegmentControl(RunControl):
    """One event stream for several launcher runs (segments): ``run.started`` once; each segment's ``task.finished``
    / ``run.finished`` are held back until the loop knows whether it continues; ``finish`` emits the final pair. The
    stop file is latched across segments (RunControl semantics)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._final = False
        self._started = False
        self._held_task = None
        self._held_run = None
        self.segment_files = []           # summary file names across 连跑 runs (unique per segment)

    def emit(self, event, **fields):
        if not self._final:
            if event == "run.started":
                if self._started:
                    return True
                self._started = True
            elif event == "task.finished":
                self._held_task = dict(fields)
                return True
            elif event == "run.finished":
                self._held_run = dict(fields)
                return True
        return super().emit(event, **fields)

    def close(self):
        if self._final:
            super().close()

    def segment_boundary(self, segment, sent):
        """A resumable segment end: shown as continuing (a readings row), never as a task failure."""
        self._held_task = self._held_run = None
        super().emit("readings", task="Wing", kind="wing_segment",
                     readings={"segment": segment, "sent_clicks": sent, "continuing": True})

    def side_run(self, run_once):
        """A follow-up run on this stream (the post-Produce new-book read): its task / run events never replace the
        W.I.N.G. result held for ``finish``."""
        held = self._held_task, self._held_run
        try:
            return run_once()
        finally:
            self._held_task, self._held_run = held

    def note_knowhow_after(self, result):
        from tools import wing_segments as ws

        # counts only (2026-10-27 user: knowhow must report target hits and locks); names stay in the run summary
        super().emit("readings", task="Wing", kind="knowhow_after", readings=ws.knowhow_after_report(result))

    def run_boundary(self, run, planned, finished):
        """连跑: run ``run`` of ``planned`` starts (a finished run's held result is replaced by the next one's)."""
        self._held_task = self._held_run = None
        super().emit("readings", task="Wing", kind="wing_loop", readings={"run": run, "planned": planned, "finished": finished})

    def note_wing_loop(self, result):
        super().emit("readings", task="Wing", kind="wing_loop", readings=dict(result))

    def finish(self, *, paused=False):
        """Emit the final task.finished / run.finished (``paused``: the loop ended at its caps, resumable)."""
        self._final = True
        task, held = self._held_task, dict(self._held_run or {})
        status = held.pop("status", "error")
        if task is not None:
            if paused:
                task["status"] = "paused"
            super().emit("task.finished", **task)
        if paused:
            status, held = "succeeded", {**held, "succeeded": 1, "failed": 0}
        if self.poll():
            status = "stopped"
        super().emit("run.finished", status=status, **held)
        super().close()
        return status


class DailyControl(SegmentControl):
    """``--daily`` with Auto 自动编队: several launcher runs on one event stream. ``run.started`` once; every run's
    ``run.finished`` is held (``finish_daily`` emits the final one); task events pass through, except the prep's
    ``task.finished`` (its result is WeeklyProduce's). The prep's "Wing" events are shown as WeeklyProduce."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._in_prep = False
        self._in_read = False
        self.auto_read = None

    def _scope(self, flag):
        import contextlib

        @contextlib.contextmanager
        def scope():
            setattr(self, flag, True)
            try:
                yield
            finally:
                setattr(self, flag, False)
        return scope()

    def prep(self):
        return self._scope("_in_prep")

    def reading(self):
        """The new-book read after a collected Auto: shown under WeeklyProduce (already finished), no task rows."""
        return self._scope("_in_read")

    def emit(self, event, **fields):
        if self._final:
            return RunControl.emit(self, event, **fields)
        if event == "run.started":
            if self._started:
                return True
            self._started = True
        elif event == "run.finished":
            self._held_run = dict(fields)
            return True
        elif self._in_prep:
            if event == "task.finished":
                return True
            if fields.get("task") == "Wing":
                fields["task"] = AUTO_TEAM_TASK
        elif self._in_read:
            if event in ("task.started", "task.finished"):
                return True
            if fields.get("task") == "KnowhowScan":
                fields["task"] = AUTO_TEAM_TASK
        return RunControl.emit(self, event, **fields)

    def segment_boundary(self, segment, sent):
        self._held_run = None
        RunControl.emit(self, "readings", task=AUTO_TEAM_TASK, kind="auto_team",
                        readings={"segment": segment, "sent_clicks": sent, "continuing": True})

    def note_auto_team(self, report):
        team = report.get("team") or {}
        RunControl.emit(self, "readings", task=AUTO_TEAM_TASK, kind="auto_team",
                        readings={"unit": report.get("unit"), "p_card": team.get("p_card"),
                                  "s_cards": list(team.get("s_cards") or []), "guest": team.get("guest"),
                                  "result": report.get("result")})

    def note_auto_read(self, result):
        self.auto_read = dict(result)
        RunControl.emit(self, "readings", task=AUTO_TEAM_TASK, kind="auto_read",
                        readings={k: result.get(k) for k in ("status", "outcome", "new", "logged", "wanted_found")})

    def task_failed(self, name, reason, report):
        """A task this script ends itself (自动编队 failed): its events and its summary entry."""
        RunControl.emit(self, "task.started", task=name, entry=None)
        RunControl.emit(self, "task.finished", task=name, status="failed", reports_complete=True)
        return {"name": name, "status": "failed", "fail_reasons": [reason], "reports": [dict(report)]}

    def task_skipped(self, name, reason):
        RunControl.emit(self, "task.finished", task=name, status="skipped", reason=reason)
        return {"name": name, "status": "skipped", "fail_reasons": [reason]}

    def finish_daily(self, summary):
        self._final = True
        status = "stopped" if self.poll() else ("succeeded" if summary["failed"] == 0 else "failed")
        RunControl.emit(self, "run.finished", status=status, succeeded=summary["succeeded"], failed=summary["failed"])
        RunControl.close(self)
        return status


if __name__ == "__main__":
    # ANN-17: like the run_tasks CLI entry, the UI engine persists passive readings (agent/core/
    # reading_store -> dev_assets/private/readings + ui/game-assets/passive-readings.js, which the host publishes to the
    # 资源 page). run_tasks.build_tasker still never persists for offline replay controllers.
    os.environ.setdefault("SC_READINGS_PERSIST", "1")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
