"""Run supervised W.I.N.G. segments back to back (private live helper; no input of its own).

Each segment is ``tools/run_tasks.py --live --supervised-task``. 2026-10-29 user (「maa不能遇到小问题就停止需求用户输入」):
a segment that ended for any reason except the ``HARD_STOP`` ones -- resources and the Produce start, a Produce or team
that is not the configured one, a hand-over the user chose, a guard refusal or a configuration gap -- is started again
from the live screen after the pause (a fresh frame, every click guarded again; the run memory file carries the state).
The same cause ``REPEAT_LIMIT`` times in a row, or ``MAX_IDLE_SEGMENTS`` segments without a sent click, still ends the
loop and is printed for the user. The stop file ends the loop as well.

2026-10-11 review (P0-1 / P1-4 / P2-1 / P2-2), shared with the UI loop (tools/ui_engine.py ``--wing``):

- an uncertain プロデュース開始 (spend ledger, agent.core.wing_ledger) is never resumable: the loop stops;
- only the FIRST segment may carry the start authority; every later segment runs ``mode: attach`` with no
  ``allow_spend`` (one loop starts at most one Produce, whatever happened);
- progress = clicks the host actually sent (``result == "sent"``), not denied/refused records;
- segments are separated by ``SEGMENT_PAUSE_S`` (> the host's 30 s quiet period) so ``pipeline-run-begin`` opens a
  fresh run window (per-run click cap and session clock);
- a total wall-clock cap and a segment cap bound the loop.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 2026-10-27 live: a timed APPEAL tap the host refused because the marker had already passed the target was never
# sent (the game keeps the gauge waiting); the next segment re-observes the Live.
# Routine segment ends (not counted towards REPEAT_LIMIT).
RESUMABLE = ("wing:click_not_sent", "wing:running", "wing:segment_", "click_denied:", "wing:decision_too_slow",
             "wing:screen_unstable", "wing:clickUnconfirmed",
             "click_refused:wing-live-appeal-timing:appeal_timing_no_future_target")
HOST_QUIET_S = 30.0              # browser_host: pipeline-run-begin is refused within 30 s of the last gesture
SEGMENT_PAUSE_S = 35.0           # > HOST_QUIET_S with margin
MAX_IDLE_SEGMENTS = 2            # consecutive segments without one sent click -> not transient
REPEAT_LIMIT = 3                 # the same cause ending this many segments in a row -> not transient
# Causes a new segment would not change, or must not touch without the user (prefix match; see the module docstring).
HARD_STOP = (
    "stop_file_requested", "stop_requested",
    "wing:spend_", "wing:start_", "wing:setup_after_start_attempt", "wing:reroll_", "wing:reward_", "wing:p_items_",
    "wing:produce_end_destructive_dialog",
    "wing:resume_config_mismatch", "wing:resume_identity_unverified", "wing:resume_team_unverified", "wing:team_",
    "wing:target_unit_invalid", "wing:unit_",
    "wing:sp_learn_handover", "wing:sp_still_requested_after_handover", "wing:sp_forget_", "wing:sp_replace_",
    "wing:sp_learn_unconfirmed", "wing:sp_changed_after_cancel",              # SP spent or skills changed: unclear
    "wing:minigame_lead_requires_user", "wing:setup_requires_user", "wing:settings_not_automated",
    "wing:minigame_not_automated", "wing:produce_end_review", "wing:no_produce_in_progress", "wing:outside_produce",
    "wing:produce_scenario_not_wing", "wing:produce_settings_",
    "wing:act_refused:", "wing:scorer_", "wing:intent_not_bound", "wing:live_agent_mode_requires_wing_audition",
    "wing:options_dialog",
)
SAFETY_WORDS = ("spend", "ledger", "uncertain", "jewel", "currency", "purchase", "consum")   # anywhere in a cause


# Post-Produce new-book read (「自动produce结束后读取的书」): after a W.I.N.G. run that Maa itself brought
# back home (outcome ``produce_finished_home``: 獲得報酬 OK on), one supervised KnowhowScan top-up reads the books 所持数
# says are new (agent.core.knowhow_scan.topup_plan). With the default 獲得報酬 handover the user ends the flow; those
# books wait for the next top-up. The top-up passed its supervised live test on 2026-10-26 (所持数 499: 1 book, 15
# inputs): on in the UI loop; the CLI loop runs it with --knowhow-after.
KNOWHOW_AFTER = True
KNOWHOW_AFTER_TIMEOUT_S = 900
KNOWHOW_AFTER_OPTION = {"mode": "scan", "newest": "auto"}


def knowhow_after_wanted(task: dict) -> bool:
    """The W.I.N.G. task ended a Produce at home by itself (not the 獲得報酬 handover, not a stop)."""
    return task.get("status") == "succeeded" and wing_report(task).get("outcome") == "produce_finished_home"


def knowhow_keep_settings(config: dict) -> dict | None:
    """自动锁书 / 自动卖书 of the W.I.N.G. run config (produce_config.wing_params end.keep); None when neither is on or
    the config has no manual W.I.N.G. settings."""
    from agent.core import produce_config as pc

    data = (((config.get("tasks") or [{}])[0] or {}).get("option") or {}).get("produce_config")
    if not isinstance(data, dict):
        return None
    try:
        keep = pc.wing_params(pc.ProduceConfig.from_dict(data))["end"]["keep"]
    except (KeyError, TypeError, ValueError):     # not a manual W.I.N.G. config: no keep step (nothing is pressed)
        return None
    return dict(keep) if keep.get("lock") is True or keep.get("sell") is True else None


def knowhow_after_config(keep: dict | None = None, *, plan: bool = False) -> dict:
    """Run config of the post-Produce top-up: the CLI's supervised KnowhowScan (as the host's 扫描ノウハウ builds it).
    ``keep`` (knowhow_keep_settings) adds the keep step: lock the planned books, report the planned sales. ``plan``
    (W.I.N.G. 连跑): the keep plan is reported even with both off, so target hits are known (no extra input)."""
    from tools import run_tasks

    option = dict(KNOWHOW_AFTER_OPTION)
    if keep:
        option.update(keep_lock=keep.get("lock") is True, keep_sell=keep.get("sell") is True,
                      keep_strategy=keep.get("strategy") or "useless_only")
    if plan:
        option["keep_plan"] = True
    run_tasks.knowhow_scan_options(option, KNOWHOW_AFTER_TIMEOUT_S)   # validated; no click cap
    return {"preset": None, "tasks": [{"name": "KnowhowScan", "option": option}],
            "session_max_minutes": KNOWHOW_AFTER_TIMEOUT_S // 60 + 5,
            "task_timeout_s": KNOWHOW_AFTER_TIMEOUT_S, "allow_spend": []}


def knowhow_after_summary(summary: dict | None) -> dict:
    """{status, outcome, books, added, owned_now, new, state} of the post-Produce top-up run (display / sell executor)."""
    task = ((summary or {}).get("tasks") or [{}])[0]
    reports = [r for r in task.get("reports") or [] if isinstance(r, dict) and r.get("kind") == "knowhow_scan"]
    report = next((r for r in reversed(reports) if not str(r.get("outcome", "")).startswith("stopped:")),
                  reports[-1] if reports else {})
    topup, inventory = report.get("topup") or {}, report.get("inventory") or {}
    out = {"status": task.get("status"), "outcome": report.get("outcome"), "books": report.get("books"),
           "added": inventory.get("topup_added"), "owned_now": topup.get("owned_now"), "new": topup.get("n"),
           "state": (inventory.get("sync") or {}).get("state")}
    if report.get("keep"):
        out["keep"] = report["keep"]
    return out


# W.I.N.G. 连跑: 「Produce次数」 2–5 runs back to back. Each run keeps the one-start rules above; the
# next one starts only after the previous Produce ended at home by Maa itself (獲得報酬 OK), its new books were read
# with the keep plan, no book filled a missing wanted entry, and enough time is left.
RUNS_MAX = 5


def run_count(config: dict) -> int:
    """Planned runs of a Wing run config: produce_config caps.max_runs (「Produce次数」), 1..RUNS_MAX."""
    data = (((config.get("tasks") or [{}])[0] or {}).get("option") or {}).get("produce_config") or {}
    runs = (data.get("caps") or {}).get("max_runs")
    return min(max(runs, 1), RUNS_MAX) if isinstance(runs, int) and not isinstance(runs, bool) else 1


def run_finished(summary: dict | None) -> bool:
    """The run's Wing task ended a Produce at home (the outcome 连跑 continues from)."""
    task = ((summary or {}).get("tasks") or [{}])[0]
    return knowhow_after_wanted(task)


def target_hits(knowhow_after: dict | None) -> list[dict]:
    """Books of the post-Produce read that fill a wanted entry the library lacked (knowhow_keep.target_hits)."""
    from agent.core import knowhow_keep as kk

    return kk.target_hits((knowhow_after or {}).get("keep"))


def books_read(knowhow_after: dict | None) -> bool:
    """Every new book was read and planned (keep step done). 2026-10-27 live: the read and the plan finished, then a
    new gacha banner on the way home failed the task; the books still count as read (an unknown screen stops the next
    run's own guards before any input)."""
    after = knowhow_after or {}
    keep = after.get("keep")
    if not isinstance(keep, dict) or keep.get("outcome") == "no_library":
        return False
    return after.get("status") == "succeeded" or (after.get("outcome") == "newest_read" and keep.get("outcome") == "done")


def knowhow_after_report(knowhow_after: dict | None) -> dict:
    """Counts of the post-Produce read for the panel (readings keep only flags, counts and ASCII codes; the book names
    and entries stay in the run summary): {status, outcome, read, new, hits, locked, lock_uncertain}."""
    after = knowhow_after or {}
    keep = after.get("keep") if isinstance(after.get("keep"), dict) else {}
    count = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None   # noqa: E731
    return {"status": after.get("status"), "outcome": after.get("outcome"), "read": books_read(after),
            "new": count(after.get("new")) if count(after.get("new")) is not None else count(after.get("added")),
            "hits": len(target_hits(after)), "locked": len(keep.get("locked") or []),
            "lock_uncertain": keep.get("uncertain") is not None}


def next_run_verdict(summary: dict | None, knowhow_after: dict | None, *, remaining_s: float, min_s: float,
                     stop: bool = False) -> tuple[bool, str | None]:
    """(start the next 连跑 run?, why not). Any doubt ends the loop; nothing is retried."""
    if stop:
        return False, "stop_requested"
    if not run_finished(summary):
        return False, "run_not_home"          # 獲得報酬 handover / stop: the user takes over
    after = knowhow_after or {}
    if not books_read(after):
        return False, "books_unread"          # the new books (and target hits) are unknown
    if after["keep"].get("uncertain") is not None:
        return False, "keep_uncertain"
    if target_hits(after):
        return False, "target_hit"
    if remaining_s < min_s:
        return False, "time_cap"
    return True, None


# Failure-snapshot notes (agent.core.guard_diagnosis, 2026-10-23) describe the last frame, not why the segment ended
# (2026-10-25 live: a denied picker tap was not resumed because near_miss notes came with it).
DIAGNOSTIC = ("near_miss:",)


def causes(reasons: list[str]) -> list[str]:
    return [r for r in reasons if not r.startswith(DIAGNOSTIC)]


def hard_cause(reasons: list[str]) -> str | None:
    """The first cause a new segment must not try to get past (None: the next segment re-observes and goes on)."""
    return next((r for r in causes(reasons) if r.startswith(HARD_STOP) or any(w in r for w in SAFETY_WORDS)), None)


def resumable(reasons: list[str]) -> bool:
    """A new segment may continue from the live screen: some cause, none of them hard."""
    return bool(causes(reasons)) and hard_cause(reasons) is None


def cause_key(task: dict) -> str:
    """What ended the segment, for the REPEAT_LIMIT count; "" for a routine end (act budget, a tap not sent, a slow
    decision -- RESUMABLE), which MAX_IDLE_SEGMENTS bounds instead."""
    found = causes(list(task.get("fail_reasons") or []))
    return "" if all(r.startswith(RESUMABLE) for r in found) else ",".join(found)


def wing_report(task: dict) -> dict:
    """The task's ``wing`` report payload ({} when absent)."""
    for report in task.get("reports") or []:
        if isinstance(report, dict) and report.get("kind") == "wing":
            return report
    return {}


def sent_clicks(task: dict) -> int:
    """Clicks the host sent in this segment (P2-1: denied/refused records are not progress)."""
    return sum(1 for c in task.get("live_clicks") or [] if isinstance(c, dict) and c.get("result") == "sent")


def spend_uncertain(task: dict) -> bool:
    """True when this segment left a プロデュース開始 unresolved/uncertain (or the report is missing it)."""
    ledger = wing_report(task).get("spend_ledger")
    if not isinstance(ledger, dict):
        # No report: a segment that may have carried spend authority cannot be proven clean.
        return True
    return bool(ledger.get("uncertain"))


def start_untouched(task: dict) -> bool:
    """True only when the segment's report proves no プロデュース開始 was attempted or recorded (a setup segment that
    stopped before the start, e.g. a denied unit-tab click): the next segment may keep the start authority."""
    report = wing_report(task)
    ledger = report.get("spend_ledger")
    return (report.get("start_attempted") is False and isinstance(ledger, dict) and ledger.get("key") is None
            and not ledger.get("uncertain") and not ledger.get("unresolved_before"))


def segment_verdict(task: dict, *, spend_authorized: bool) -> tuple[str, str]:
    """(``continue`` | ``done`` | ``stop``, reason) for one finished segment."""
    reasons = list(task.get("fail_reasons") or [])
    if task.get("status") == "succeeded":
        return "done", "succeeded"
    if "stop_file_requested" in reasons:
        return "stop", "stop_file_requested"
    ledger = wing_report(task).get("spend_ledger")
    if (spend_authorized and spend_uncertain(task)) or (isinstance(ledger, dict) and ledger.get("uncertain")):
        return "stop", "spend_uncertain"
    if not resumable(reasons):
        return "stop", ",".join(reasons) or str(task.get("status"))
    return "continue", ",".join(reasons)


def attach_config(config: dict) -> dict:
    """The config of every segment after the first: attach, no spend authority, no start-only options."""
    out = copy.deepcopy(config)
    out["allow_spend"] = []
    for item in out.get("tasks") or []:
        option = item.get("option") if isinstance(item, dict) else None
        if isinstance(option, dict):
            option["mode"] = "attach"
            for key in ("unit_search", "minigame", "team_confirmed", "team_edit", "edit_only", "picker_sort"):   # start-only
                option.pop(key, None)
    return out


def config_spends(config: dict) -> bool:
    return bool(config.get("allow_spend"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--start", type=int, required=True, help="first segment number (file names segNN-*)")
    ap.add_argument("--max-segments", type=int, default=8)
    ap.add_argument("--max-minutes", type=float, default=240.0, help="total wall-clock cap of the loop")
    ap.add_argument("--pause", type=float, default=SEGMENT_PAUSE_S, help=f"pause between segments (≥{HOST_QUIET_S:.0f} s)")
    ap.add_argument("--knowhow-after", action="store_true",
                    help="Produce 在首页结束后，再监督运行一次读新ノウハウ（KnowhowScan newest auto）")
    args = ap.parse_args(argv)
    if args.pause < HOST_QUIET_S:
        ap.error(f"--pause 须 ≥ {HOST_QUIET_S:.0f} 秒（宿主静默期，否则不会开启新运行窗口）")
    out = Path(args.out_dir)
    stop = out / "wing.stop"
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    from agent.core.wing_custom import SHARED_MEMORY_FILE
    memory = next((((t.get("option") or {}).get("memory_file")) for t in config.get("tasks") or []
                   if isinstance(t, dict)), None)
    if memory and (ROOT / memory).resolve() != SHARED_MEMORY_FILE.resolve():
        print(json.dumps({"note": "memory_file_not_shared", "memory_file": memory,
                          "shared": str(SHARED_MEMORY_FILE.relative_to(ROOT))}, ensure_ascii=False), flush=True)
    attach_path = out / "wing-attach-config.json"
    config_path = Path(args.config)
    deadline = time.monotonic() + args.max_minutes * 60
    idle, repeats, last_key = 0, 0, None
    for index, n in enumerate(range(args.start, args.start + args.max_segments)):
        if stop.exists():
            print(json.dumps({"segment": n, "ended": "stop_file"}, ensure_ascii=False))
            return 0
        if time.monotonic() > deadline:
            print(json.dumps({"ended": "time_cap"}))
            return 3
        spend = index == 0 and config_spends(config)
        if index == 1:   # every later segment: attach without spend authority (never a second start)
            out.mkdir(parents=True, exist_ok=True)
            attach_path.write_text(json.dumps(attach_config(config), ensure_ascii=False, indent=2), encoding="utf-8")
            config_path = attach_path
        summary = out / f"seg{n}-summary.json"
        cmd = [sys.executable, str(ROOT / "tools/run_tasks.py"), "--live", "--supervised-task", "--config", str(config_path),
               "--session", args.session, "--summary", str(summary), "--events", str(out / f"seg{n}-events.jsonl"),
               "--stop-file", str(stop)]
        subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            task = json.loads(summary.read_text(encoding="utf-8"))["tasks"][0]
        except Exception as exc:
            print(json.dumps({"segment": n, "ended": f"summary_unreadable:{exc}"}, ensure_ascii=False))
            return 1
        sent = sent_clicks(task)
        verdict, why = segment_verdict(task, spend_authorized=spend)
        key = cause_key(task)
        repeats = repeats + 1 if key and key == last_key else 1
        last_key = key
        if verdict == "continue" and key and repeats >= REPEAT_LIMIT:
            verdict, why = "stop", f"repeated:{key}"
        line = {"segment": n, "status": task.get("status"), "sent_clicks": sent,
                "reasons": list(task.get("fail_reasons") or []), "verdict": verdict, "why": why}
        print(json.dumps(line, ensure_ascii=False), flush=True)
        if verdict == "done":
            if args.knowhow_after and knowhow_after_wanted(task):
                return knowhow_after_cli(out, args.session, stop, args.pause, keep=knowhow_keep_settings(config))
            return 0
        if verdict == "stop":
            return 5 if why == "spend_uncertain" else 2
        idle = idle + 1 if sent == 0 else 0
        if idle >= MAX_IDLE_SEGMENTS:
            print(json.dumps({"ended": "no_progress"}))
            return 4
        waited = 0.0
        while waited < args.pause:   # the stop file also ends the pause
            if stop.exists():
                print(json.dumps({"segment": n, "ended": "stop_file"}, ensure_ascii=False))
                return 0
            time.sleep(0.5)
            waited += 0.5
    print(json.dumps({"ended": "max_segments"}))
    return 3


def knowhow_after_cli(out: Path, session: str, stop: Path, pause: float, keep: dict | None = None) -> int:
    """CLI post-Produce top-up: one more run window after the pause; never fails the finished W.I.N.G. run."""
    waited = 0.0
    while waited < pause:
        if stop.exists():
            print(json.dumps({"knowhow_after": "stop_file"}))
            return 0
        time.sleep(0.5)
        waited += 0.5
    config_path, summary = out / "knowhow-after-config.json", out / "knowhow-after-summary.json"
    config_path.write_text(json.dumps(knowhow_after_config(keep), ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "tools/run_tasks.py"), "--live", "--supervised-task", "--config", str(config_path),
           "--session", session, "--summary", str(summary), "--events", str(out / "knowhow-after-events.jsonl"),
           "--stop-file", str(stop)]
    subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        result = knowhow_after_summary(json.loads(summary.read_text(encoding="utf-8")))
    except Exception as exc:
        result = {"status": f"summary_unreadable:{exc}"}
    print(json.dumps({"knowhow_after": result}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
