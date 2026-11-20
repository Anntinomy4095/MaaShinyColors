#!/usr/bin/env python3
"""Fake UI engine child for the C# supervisor self-test (--self-test-ui, phase E) and Python tests.

Accepts the same arguments as tools/ui_engine.py plus ``--scenario`` and writes run events in the exact
agent/core/run_control.RunControl JSONL format (version, run_id, sequence, event, elapsed_seconds, timestamp, fields).
It never imports Maa, never connects to a host and never clicks. Scenarios:

ok          every enabled task succeeds; summary agrees; exit 0
fail_one    the second enabled task fails (fail_reasons: a diagnostic line, then "maintenance"); exit 1
stop        first task waits for the stop file, then interrupted; the rest skipped; exit 1
crash       first task starts, then the process exits 3 without finishing
hang        first task starts, then ignores the stop file and sleeps (the supervisor must kill it)
refuse      prints {"passed": false, "error": ...} and exits 2 before any event
mismatch    events say succeeded but the summary says failed for the first task; exit 0
garbage     writes a malformed event line, then continues as ok
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path


class Events:
    def __init__(self, path):
        self.stream = Path(path).open("xb")
        self.run_id = uuid.uuid4().hex
        self.sequence = 0
        self.started = time.monotonic()

    def emit(self, event, **fields):
        self.sequence += 1
        row = {"version": 1, "run_id": self.run_id, "sequence": self.sequence, "event": event,
               "elapsed_seconds": round(time.monotonic() - self.started, 6),
               "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), **fields}
        self.stream.write((json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"))
        self.stream.flush()

    def raw(self, text):
        self.stream.write(text.encode("utf-8"))
        self.stream.flush()


def summary(path, tasks, run_id, stop=False):
    data = {"order": [t["name"] for t in tasks], "tasks": tasks,
            "succeeded": sum(t["status"] == "succeeded" for t in tasks),
            "failed": sum(t["status"] != "succeeded" for t in tasks),
            "run_control": {"run_id": run_id, "stop_requested": stop, "stop_reason": "stop_file_requested" if stop else None,
                            "stop_verified": True if stop else None, "events_complete": True}}
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--session")
    parser.add_argument("--offline-frames")
    args = parser.parse_args(argv)
    scenario = args.scenario
    if scenario == "refuse":
        print(json.dumps({"passed": False, "error": r"没有启用的任务 C:\secret\path.json"}, ensure_ascii=False))
        return 2
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    names = [t["name"] for t in profile["tasks"] if t.get("enabled")]
    events = Events(args.events)
    events.emit("run.started")
    print(r"stderr line with a path C:\Users\public\secret.txt", file=sys.stderr, flush=True)
    results = []
    if scenario == "garbage":
        events.raw("{not json\n")
    for index, name in enumerate(names):
        if scenario in ("stop", "crash", "hang") and index == 0:
            events.emit("task.started", task=name, entry=f"SC.{name}.Entry")
            if scenario == "crash":
                sys.stdout.flush()
                return 3
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if scenario == "stop" and Path(args.stop_file).exists():
                    break
                time.sleep(0.05)
            if scenario == "hang":
                time.sleep(3600)
            events.emit("run.stop_requested", task=name, reason="stop_file_requested")
            events.emit("task.finished", task=name, status="interrupted", reports_complete=True)
            results.append({"name": name, "status": "interrupted", "fail_reasons": ["stop_file_requested"]})
            for rest in names[1:]:
                events.emit("task.finished", task=rest, status="skipped", reason="run_stopped")
                results.append({"name": rest, "status": "skipped", "fail_reasons": ["stop_file_requested"]})
            events.emit("run.finished", status="stopped", succeeded=0, failed=len(names))
            summary(args.summary, results, events.run_id, stop=True)
            print(json.dumps({"order": names, "summary": r"C:\private\summary.json"}))
            return 1
        events.emit("task.started", task=name, entry=f"SC.{name}.Entry")
        events.emit("node", task=name, node=f"SC.{name}.Entry", status="succeeded")
        if name == "Shop":
            events.emit("readings", task=name, kind="shop", readings={"ok": True, "observations": [{"money_before": 123456, "money_after": 113456}]})
        if name == "MatchSkip":
            events.emit("readings", task=name, kind="match", readings={"stage": 12, "observations": [{"rights": [14, 20]}]})
        status = "failed" if scenario == "fail_one" and index == 1 else "succeeded"
        events.emit("task.finished", task=name, status=status, reports_complete=True)
        summary_status = "failed" if scenario == "mismatch" and index == 0 else status
        # fail_one: a diagnostic line first, then the SC.FailTask-style stop code the host forwards as the row's reason.
        reasons = ["near_miss:SC.Shop.Premium.Guard/SC.Shop.Anchor@0.91", "maintenance"] if status == "failed" else []
        results.append({"name": name, "status": summary_status, "fail_reasons": reasons})
    failed = sum(r["status"] != "succeeded" for r in results)
    events.emit("run.finished", status="failed" if failed else "succeeded", succeeded=len(results) - failed, failed=failed)
    summary(args.summary, results, events.run_id)
    print(json.dumps({"order": names, "summary": r"C:\private\summary.json"}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
