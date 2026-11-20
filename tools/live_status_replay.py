"""Replay recorded W.I.N.G. segment logs through the live status feed (UI 「运行中」 preview data).

Reads the private run summaries of supervised W.I.N.G. segments (``seg*-summary.json``, the ``wing`` report's
decision log), folds them through ``agent.core.live_status`` + ``wing_live_feed`` exactly as a live run would, and
writes the sequence of snapshots as an ES module the browser preview plays back (``?live=wing``):

    .venv\\Scripts\\python.exe tools\\live_status_replay.py dev_assets\\private\\wing-live-20261009\\seg2b-summary.json ^
        dev_assets\\private\\wing-live-20261009\\seg12-summary.json --out ui\\game-assets\\live-replay.js

The default output lives under the gitignored ``ui/game-assets/``; ``--synthetic`` writes the committed synthetic
fallback ``ui/fixtures/live-demo.js`` (made-up numbers, no game capture). Offline only; no input, no host.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core import live_status as ls  # noqa: E402
from agent.core.wing_live_feed import WingFeed  # noqa: E402

DEFAULT_OUT = ROOT / "ui" / "game-assets" / "live-replay.js"
SYNTHETIC_OUT = ROOT / "ui" / "fixtures" / "live-demo.js"
MAX_FRAMES = 160


class _Never:
    def cancel(self):
        pass


class Recorder(ls.LiveStatus):
    """LiveStatus that keeps every snapshot it would write (fake clock: one second per update)."""

    def __init__(self, frames):
        self.t = 0.0
        self.frames = frames
        super().__init__(ls.DEFAULT_PATH, clock=lambda: self.t, wall=lambda: f"2026-10-09T15:{int(self.t // 60) % 60:02d}:{int(self.t) % 60:02d}+00:00",
                         schedule=lambda delay, fn: _Never(), labels=None)

    def update(self, mutate, *, force=False):
        self.t += 1.0
        super().update(mutate, force=force)

    def _write(self):
        s = self.snapshot
        s["updated_at"] = self.wall()
        s["elapsed_s"] = int(self.t)
        self.frames.append(copy.deepcopy(s))
        self._dirty = False
        self._last_write = self.clock()


def wing_logs(paths):
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for task in data.get("tasks") or []:
            for report in task.get("reports") or []:
                if report.get("kind") == "wing" and isinstance(report.get("log"), list):
                    yield report["log"]


def replay(logs, *, tasks=("Startup", "Wing"), cap=170):
    frames = []
    status = Recorder(frames)
    status.start("preview-wing", list(tasks), cap)
    if "Startup" in tasks:
        status.observe("task.started", {"task": "Startup"})
        status.observe("node", {"task": "Startup", "node": "SC.Startup.Entry"})
        status.observe("task.finished", {"task": "Startup", "status": "succeeded"})
    status.observe("task.started", {"task": "Wing"})
    for log in logs:
        feed, merged = WingFeed(), []
        for entry in log:
            merged.append(entry)
            feed.feed(merged, status=status)
            if entry.get("event") in ("act_result", "act") and entry.get("sent") in ("sent", True):
                status.observe("click.result", {"task": "Wing", "result": "sent"})
    status.observe("task.finished", {"task": "Wing", "status": "failed"})
    status.finish("failed")
    status.flush()
    # keep the playback short: every frame with a new week/event/step, at most MAX_FRAMES
    keep, last = [], None
    for f in frames:
        key = json.dumps([f["step"], f["wing"] and f["wing"]["events"][-1:] if f["wing"] else None, f["state"]], ensure_ascii=False)
        if key != last:
            keep.append(f)
            last = key
    return keep[-MAX_FRAMES:]


def synthetic_log():
    """Made-up W.I.N.G. week (not game data) for the committed preview fallback."""
    def row(id_, kind, attr, score, sup, **terms):
        base = {k: 0.0 for k in ("stat_gain", "cap_overflow", "off_stat", "mental", "sp", "bond", "events", "fans",
                                 "audition_progress", "fan_urgency", "audition_stats", "stamina", "trouble_risk",
                                 "promise", "tension")}
        base.update(terms)
        return {"id": id_, "kind": kind, "attribute": attr, "score": score, "terms": base, "unknown": [], "notes": [],
                "trouble": 0.0, "supports": sup}
    week = {"kind": "lesson", "rule": "score_best", "option_id": "lesson_vo", "attribute": "Vo",
            "plan": {"scores": [row("lesson_vo", "lesson", "Vo", 40.0, 3, stat_gain=24.0, bond=9.0, events=3.0, sp=4.0),
                                row("work_radio", "work", None, 21.0, 0, mental=12.0, stat_gain=4.0, sp=4.0, fans=1.0),
                                row("lesson_da", "lesson", "Da", 6.0, 0, off_stat=1.5, sp=4.5),
                                row("lesson_vi", "lesson", "Vi", 5.5, 0, off_stat=1.0, sp=4.5),
                                row("rest", "rest", None, 2.0, None, tension=2.0)],
                     "excluded": [dict(row("s1_1k", "audition", "Vo", None, None), notes=["p_win_unknown"])],
                     "context": {"primary": "Vo"}}}
    read = {"season": 1, "weeks_left": 6, "fans_left": 800, "stamina_pct": 95, "stats": {"Vo": 150, "Da": 100, "Vi": 100, "Me": 130},
            "sp": 60, "fans": 5200}
    return [
        {"event": "begin"},
        {"event": "decide", "screen": "Hub", "decision": {"action": "click", "intent": "hub_schedule", "detail": {}}, "readings": read},
        {"event": "decide", "screen": "Schedule", "decision": {"action": "click", "intent": "schedule_room_1", "detail": {"decision": week}}, "readings": read},
        {"event": "decide", "screen": "Schedule", "decision": {"action": "click", "intent": "schedule_decide", "detail": {"decision": week}}, "readings": read},
        {"event": "decide", "screen": "Choice", "decision": {"action": "click", "intent": "choice_left", "detail": {"text": "（示例选项）"}}, "readings": {}},
        {"event": "sp_session_start", "sp": 64},
        {"event": "sp_learned", "name": "（示例技能）", "cost": 30, "sp": 34},
        {"event": "decide", "screen": "Live", "decision": {"action": "click", "intent": "live_card_1", "detail": {}}, "readings": {}},
        {"event": "win", "season": 1},
    ]


def module(frames, note):
    return (f"// {note}\n// Generated by tools/live_status_replay.py; do not edit.\n"
            "export default " + json.dumps({"version": 1, "frames": frames}, ensure_ascii=False, indent=1) + ";\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="把 W.I.N.G. 分段运行摘要回放成「运行中」预览数据（离线）")
    parser.add_argument("summaries", nargs="*", type=Path, help="私有 seg*-summary.json")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--synthetic", action="store_true", help="写入合成预览数据 ui/fixtures/live-demo.js（可提交）")
    args = parser.parse_args(argv)
    if args.synthetic:
        frames = replay([synthetic_log()])
        out = args.out or SYNTHETIC_OUT
        note = "Synthetic 「运行中」 preview frames (made-up numbers, not game data)."
    else:
        if not args.summaries:
            parser.error("需要至少一个 seg*-summary.json，或 --synthetic")
        frames = replay(list(wing_logs(args.summaries)))
        out = args.out or DEFAULT_OUT
        note = "Private 「运行中」 preview frames replayed from recorded W.I.N.G. segment logs (gitignored)."
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(module(frames, note), encoding="utf-8")
    print(json.dumps({"frames": len(frames), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
