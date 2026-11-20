"""Private, bounded run events and a latched cooperative stop. No input authority."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from agent.core import business_task, grade_task, match_task, shop_task, training_task

PRIVATE = Path(__file__).resolve().parents[2] / "dev_assets" / "private"
MAX_EVENTS = 100_000
MAX_EVENT_BYTES = 32 * 1024 * 1024
# Recognition timing (2026-10-25 W.I.N.G. speed audit): Node.Recognition.* of SC.* nodes, aggregated in memory and written
# as one ``recognition_stats`` row per window. A W.I.N.G. loop tries ~60 candidates per frame, so a row per attempt
# would reach MAX_EVENTS (and stop the run) within a segment.
RECO_WINDOW_S = 60.0
RECO_TOP = 40                       # slowest nodes per row (a row stays far below the 16 KiB line limit)
STOP_REASONS = {"stop_file_requested", "stop_file_unavailable", "event_log_unavailable",
                "stop_failed", "runner_error", "manual_takeover", "emergency_stop"}
STATES = {"starting", "succeeded", "failed", "error", "timeout", "interrupted", "skipped",
          "sent", "refused", "denied", "unknown", "intent", "not_sent", "uncertain", "verified",
          "takeover", "completed", "claimable", "incomplete", "nothing_to_do", "stopped"}
STATES |= {"bought", "trained", "played", "skipped", "claimed", "dispatched", "sampled",
           "claim_uncertain", "dispatch_uncertain", "spend_uncertain", "spend_refused", "skipped_rules", "skipped_inconsistent"}
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,159}\Z")
TASK_OUTCOMES = {
    "shop": shop_task.SUCCESS_OUTCOMES | shop_task.FAILURE_OUTCOMES,
    "training": training_task.SUCCESS_OUTCOMES | training_task.FAILURE_OUTCOMES,
    "grade": grade_task.SUCCESS_OUTCOMES | grade_task.FAILURE_OUTCOMES | grade_task.HALT_OUTCOMES,
    "match": match_task.SUCCESS_OUTCOMES | match_task.FAILURE_OUTCOMES,
    "business": business_task.SUCCESS_OUTCOMES | business_task.HALT_OUTCOMES | business_task.RETURN_FAILURE_OUTCOMES,
    "missions_claim": {"claimed", "already_claimed", "bulk_received", "bulk_nothing_to_claim",
                       "disabled", "stopped", "uncertain", "overflow", "not_sent_live_input_closed",
                       "bulk_not_sent"},
    "present": {"box_empty", "bulk_disabled", "claimed_today", "received", "received_partial", "config_invalid", "disabled", "stopped",
                "decide_error", "scan_without_scroll", "intent_rejected", "intent_error",
                "not_sent_live_input_closed", "result_without_pending", "result_overflow",
                "result_error", "result_uncertain", "confirm_without_receipt", "uncertain"},
}


def private_path(raw):
    path = Path(os.path.abspath(raw))
    root = Path(os.path.abspath(PRIVATE))
    if not path.is_relative_to(root) or path == root:
        raise ValueError("运行控制文件须位于 dev_assets/private")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
            raise ValueError("运行控制文件不能使用链接或重解析路径")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("运行控制路径越界")
    return path


def identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


class RunControl:
    """Stop is irreversible for this instance; callbacks never wait for a task or input lock."""

    def __init__(self, *, events_path=None, stop_file=None, clock=time.monotonic,
                 max_events=MAX_EVENTS, max_bytes=MAX_EVENT_BYTES):
        if type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS:
            raise ValueError("事件数量上限无效")
        if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_EVENT_BYTES:
            raise ValueError("事件容量上限无效")
        self.clock, self.started = clock, clock()
        self.run_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self.reason, self.task = None, None
        self.stop_verified = None
        self.sequence = self.bytes_written = 0
        self.max_events, self.max_bytes = max_events, max_bytes
        self.events_path = private_path(events_path) if events_path is not None else None
        self.stop_file = private_path(stop_file) if stop_file is not None else None
        self._stream, self._event_failed = None, False
        self.live = None   # optional agent.core.live_status.LiveStatus: display-only observer of the same events
        self._reco_open, self._reco_stats, self._reco_since = {}, {}, self.started
        if self.events_path is not None:
            if self.events_path.suffix.lower() != ".jsonl" or self.events_path == self.stop_file:
                raise ValueError("事件文件须为独立的新 JSONL")
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self.events_path.open("xb")

    def emit(self, event, **fields):
        """Only callers' fixed schema fields; raw Maa/audit/report dictionaries never enter here."""
        with self._lock:
            if self.live is not None:
                try:
                    self.live.observe(event, fields)
                except Exception:
                    pass
            if self._event_failed:
                return False
            if self._stream is None:
                return True
            row = {"version": 1, "run_id": self.run_id, "sequence": self.sequence + 1,
                   "event": event, "elapsed_seconds": round(self.clock() - self.started, 6),
                   "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), **fields}
            try:
                encoded = (json.dumps(row, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
                           + "\n").encode("utf-8")
                if (len(encoded) > 16_384 or self.sequence >= self.max_events
                        or self.bytes_written + len(encoded) > self.max_bytes):
                    raise ValueError("event_limit")
                if self._stream.write(encoded) != len(encoded):
                    raise OSError("event_short_write")
                self._stream.flush()
                self.sequence += 1
                self.bytes_written += len(encoded)
                return True
            except Exception:
                self._event_failed = True
                if self.reason is None:
                    self.reason = "event_log_unavailable"
                return False

    def request_stop(self, reason="stop_file_requested"):
        with self._lock:
            if self.reason is None:
                self.reason = reason if reason in STOP_REASONS else "runner_error"
                self.emit("run.stop_requested", task=self.task, reason=self.reason)
            return True

    def poll(self):
        with self._lock:
            if self.reason is not None:
                return True
            if self.stop_file is not None:
                try:
                    path = private_path(self.stop_file)
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        return self.request_stop("stop_file_unavailable")
                    return self.request_stop()
                except FileNotFoundError:
                    pass
                except Exception:
                    return self.request_stop("stop_file_unavailable")
            return False

    @staticmethod
    def readings(report):
        kind = report.get("kind")
        if kind == "resources":
            fields = {"items": {}}
            layout = identifier(report.get("layout"))
            if layout is not None:
                fields["layout"] = layout
            items = report.get("items")
            for key, value in items.items() if isinstance(items, dict) else ():
                if len(fields["items"]) >= 32:
                    break
                if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", key) or not isinstance(value, dict):
                    continue
                count = value.get("count")
                if type(count) is not int or not 0 <= count <= 10**9:
                    continue
                entry = {"count": count}
                for extra in ("max", "plus", "confidence_pm"):
                    number = value.get(extra)
                    if type(number) is int and 0 <= number <= (1000 if extra == "confidence_pm" else 10**9):
                        entry[extra] = number
                if type(value.get("indicator_only")) is bool:
                    entry["indicator_only"] = value["indicator_only"]
                fields["items"][key] = entry
            return {"kind": kind, "readings": fields}
        if kind not in {*TASK_OUTCOMES, "mission_status", "diagnostics"}:
            return None
        fields = {}
        for key in ("ok", "home_clean", "available", "capacity", "quantity", "price", "runs_done",
                    "runs_done_today", "remaining", "level", "stage", "latest_stage", "total", "count", "succeeded"):
            value = report.get(key)
            if type(value) is bool or type(value) is int and 0 <= value <= 10**9:
                fields[key] = value
        for key in ("status", "outcome", "conclusion", "log_state"):
            if isinstance(report.get(key), str) and report[key] in STATES:
                fields[key] = report[key]
        outcome = report.get("outcome")
        if kind == "present" and isinstance(outcome, str) and outcome.startswith("stopped:"):
            outcome = "stopped"
        if isinstance(outcome, str) and outcome in TASK_OUTCOMES.get(kind, ()):
            fields["outcome"] = outcome
        if kind == "shop":
            known = {"coin_reserve", "coin_budget_exceeded", "price_over_max", "price_unreadable"}
            fields["skip_reasons"] = sorted({note.split(":")[-1] for note in report.get("notes", ())
                if isinstance(note, str) and note.split(":")[-1] in known})
        if kind == "grade" and isinstance(report.get("daily_progress"), dict):
            progress = report["daily_progress"]
            fields["daily_progress"] = {key: value for key, value in progress.items()
                if key in {"target", "completed", "remaining", "planned_runs", "attempts", "samples_remaining",
                           "verified", "reported", "uncertain"} and type(value) is int and 0 <= value <= 10**9}
            if progress.get("mode") in {"grade", "rehearsal"}:
                fields["daily_progress"]["mode"] = progress["mode"]
            fields["takeover_required"] = report.get("takeover_required") is True
        if kind == "match":
            progress = report.get("progress")
            if isinstance(progress, dict):
                fields["progress"] = {
                    "skip_list_complete": progress.get("skip_list_complete") is True,
                    **{key: value for key, value in progress.items()
                       if key in {"latest_open_stage", "latest_skippable_stage", "selected_stage"}
                       and isinstance(value, str) and re.fullmatch(r"A[1-8]-S([1-9]|1[0-9]|2[0-5])", value)},
                }
        counter = report.get("counter")
        if isinstance(counter, (list, tuple)) and len(counter) == 2 and all(type(v) is int and 0 <= v <= 10**9 for v in counter):
            fields["counter"] = list(counter)
        if isinstance(report.get("rows"), list):
            fields["row_states"] = [row.get("status", "unknown") if isinstance(row.get("status"), str) and row["status"] in STATES else "unknown"
                                    for row in report["rows"][:100] if isinstance(row, dict)]
        observations = []
        decisions = report.get("decisions")
        for decision in decisions[-12:] if isinstance(decisions, (list, tuple)) else []:
            if not isinstance(decision, dict):
                continue
            numbers = {}
            for key in ("money", "money_before", "money_after", "quantity", "price", "level", "stage", "count"):
                value = decision.get(key)
                if type(value) is int and 0 <= value <= 10**9:
                    numbers[key] = value
                elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
                    numbers[key] = int(value)
            for key in ("rights", "rights_before", "rights_after"):
                value = decision.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 2 and all(type(v) is int and 0 <= v <= 99 for v in value):
                    numbers[key] = list(value)
                elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}", value):
                    numbers[key] = [int(v) for v in value.split("/")]
            if numbers:
                observations.append(numbers)
        if observations:
            fields["observations"] = observations
        return {"kind": kind, "readings": fields}

    def report(self, report):
        if report.get("kind") == "fail":
            self.emit("refusal", task=self.task, node=identifier(report.get("node")), reason="task_reported_failure")
            return
        payload = self.readings(report)
        if payload is not None:
            self.emit("readings", task=self.task, **payload)

    def input_event(self, record):
        if record.get("kind") not in {"click", "scroll"}:
            return True
        result = record.get("result")
        if result not in STATES:
            return True
        event = "click.request" if result == "intent" else "click.result"
        return self.emit(event, task=identifier(record.get("task")) or self.task,
                         action=identifier(record.get("action")), result=result,
                         consuming=record.get("spend") is True,
                         attempted=record.get("response", {}).get("attempted") is True
                         if isinstance(record.get("response"), dict) else None)

    def recognition(self, message, name):
        """One Node.Recognition.* notification of an SC.* node: [attempts, hits, total s, max s] per node."""
        now = self.clock()
        with self._lock:
            if message.endswith(".Starting"):
                self._reco_open[name] = now
                return
            started = self._reco_open.pop(name, None)
            if started is None:
                return
            stat = self._reco_stats.setdefault(name, [0, 0, 0.0, 0.0])
            took = now - started
            stat[0] += 1
            stat[1] += message.endswith(".Succeeded")
            stat[2] += took
            stat[3] = max(stat[3], took)
            due = now - self._reco_since >= RECO_WINDOW_S
        if due:
            self.flush_recognition()

    def flush_recognition(self):
        with self._lock:
            stats, since = self._reco_stats, self._reco_since
            self._reco_stats, self._reco_since = {}, self.clock()
        if not stats:
            return True
        slowest = sorted(stats.items(), key=lambda item: -item[1][2])[:RECO_TOP]
        return self.emit("recognition_stats", task=self.task, window_s=round(self._reco_since - since, 3),
                         nodes=[[name, n, hits, round(total, 4), round(longest, 4)]
                                for name, (n, hits, total, longest) in slowest])

    def attach(self, tasker):
        from maa.context import ContextEventSink

        control = self

        class Sink(ContextEventSink):
            def on_raw_notification(self, context, message, details):
                if message in {"Node.Recognition.Starting", "Node.Recognition.Succeeded", "Node.Recognition.Failed"}:
                    name = identifier(details.get("name"))
                    if name is not None and name.startswith("SC."):
                        control.recognition(message, name)
                    return
                if message not in {"Node.PipelineNode.Starting", "Node.PipelineNode.Succeeded", "Node.PipelineNode.Failed"}:
                    return
                name = identifier(details.get("name"))
                if name is not None and name.startswith("SC."):
                    control.emit("node", task=control.task, node=name, status=message.rsplit(".", 1)[1].lower())

        from agent.core import maa_sinks

        observer = Sink()
        sink_id = tasker.add_context_sink(observer)
        if sink_id is None:
            raise ValueError("运行事件监听注册失败")
        maa_sinks.track(tasker, "remove_context_sink", sink_id)   # released when the run ends (maa_sinks.release)
        tasker._sc_run_event_sink = observer

    def close(self):
        if self._stream is not None:
            self.flush_recognition()
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    self._event_failed = True
                    self.request_stop("event_log_unavailable")
                self._stream = None

    def summary(self):
        return {"run_id": self.run_id, "stop_requested": self.reason is not None,
                "stop_reason": self.reason, "stop_verified": self.stop_verified,
                "events_complete": not self._event_failed, "events_written": self.sequence}
