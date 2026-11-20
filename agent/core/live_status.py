"""User-facing live status of the current run (UI 「运行中」 view).

One small JSON snapshot, atomically replaced at ``dev_assets/private/live-status/current.json`` (gitignored) whenever
something a user would care about changes: the task queue and each task's plain result, the current step as one
Chinese sentence, clicks used / cap, elapsed time, the time of the last observed frame and, for W.I.N.G., the
season/week, stats, the latest week decision (from ``wing_score.score_card``) and a short event timeline.

Display only. Nothing here decides, clicks, retries or reads the game; every hook is best-effort and never raises
into the runner (a failed write only leaves the previous snapshot in place). Writes are throttled to at most
``MAX_WRITES_PER_S`` per second; a deferred flush carries the last change, and a slow heartbeat keeps ``updated_at``
fresh while a run is active so the host can tell a crashed run from a quiet one.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "dev_assets" / "private" / "live-status" / "current.json"
VERSION = 1
MAX_WRITES_PER_S = 2
MIN_INTERVAL_S = 1.0 / MAX_WRITES_PER_S
HEARTBEAT_S = 15.0
MAX_EVENTS = 12
MAX_TEXT = 80
MAX_BYTES = 48 * 1024        # well under the UI protocol's 64 KiB message limit

# Plain task names (ui/annotations.json is the source; this is the fallback when it cannot be read).
TASK_LABELS = {"Startup": "启动", "Shop": "商店", "Training": "训练", "MatchSkip": "Match Live", "GradeFes": "Grade Fes",
               "MissionStatus": "任务进度", "MissionsClaim": "任务奖励", "Business": "营业",
               "PresentClaim": "プレゼント", "WeeklyCheck": "周常检查", "AutoProduce": "W.I.N.G.", "Wing": "W.I.N.G.",
               "WeeklyProduce": "Auto Produce·周常", "Diagnostics": "诊断", "ProduceDryRun": "Produce 空跑",
               "CardScan": "扫描持有卡", "CollaboFes": "Collabo Fes"}

# Runner task status -> (queue state, plain text).
TASK_RESULT = {
    "succeeded": ("done", "完成"),
    "failed": ("failed", "未完成"),
    "error": ("failed", "出错"),
    "timeout": ("failed", "超时"),
    "interrupted": ("stopped", "中断"),
    "skipped": ("skipped", "跳过"),
}
RUN_RESULT = {"succeeded": "全部完成", "failed": "有未完成", "stopped": "已停止", "error": "出错停止"}

# Node-name fragments -> generic plain phase (daily tasks do not report their own steps; never shows node names).
NODE_PHASES = (
    ("GoHome", "回主页"), ("HomeReached", "确认主页"), ("Startup", "确认画面"),
    ("Confirm", "核对画面"), ("Scroll", "翻页"), ("Scan", "读列表"), ("Read", "读画面"),
    ("Observe", "读画面"), ("Wait", "等待"), ("Loading", "加载"), ("Popup", "关弹窗"),
    ("Nav", "切页"), ("Open", "打开"), ("Enter", "进入"), ("Back", "返回"),
)


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _text(value, limit=MAX_TEXT):
    return value[:limit] if isinstance(value, str) else None


def task_labels():
    labels = dict(TASK_LABELS)
    try:
        doc = json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))
        for name, entry in (doc.get("tasks") or {}).items():
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                labels[name] = entry["name"][:40]
    except Exception:
        pass
    return labels


def node_phase(node):
    if not isinstance(node, str) or not node.startswith("SC."):
        return None
    for fragment, text in NODE_PHASES:
        if fragment in node:
            return text
    return None


class LiveStatus:
    """Throttled snapshot writer. ``schedule(delay, fn)`` returns an object with ``cancel()`` (default
    ``threading.Timer``); tests inject a fake scheduler and clocks."""

    def __init__(self, path=DEFAULT_PATH, *, clock=time.monotonic, wall=_now_iso, schedule=None,
                 heartbeat_s=HEARTBEAT_S, labels=None):
        from agent.core.run_control import private_path
        self.path = private_path(path)
        self.clock, self.wall = clock, wall
        self.heartbeat_s = heartbeat_s
        self._schedule = schedule or self._timer
        self._lock = threading.RLock()
        self._last_write = None
        self._dirty = False
        self._pending = None
        self._pending_due = 0.0
        self._closed = False
        self.writes = 0
        self.write_failures = 0
        self.labels = labels if labels is not None else task_labels()
        self.started = clock()
        self.snapshot = {"version": VERSION, "run_id": None, "state": "starting", "outcome": None,
                         "outcome_text": None, "started_at": wall(), "updated_at": wall(), "elapsed_s": 0,
                         "tasks": [], "current": None, "step": None, "progress": {"done": 0, "total": 0},
                         "clicks": {"used": 0, "cap": None}, "last_frame_at": None, "wing": None}

    @staticmethod
    def _timer(delay, fn):
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()
        return timer

    # ---- public hooks (never raise) ------------------------------------------------------------------------
    def start(self, run_id, tasks, click_cap=None):
        def apply(s):
            s["run_id"] = _text(run_id, 64)
            s["state"] = "running"
            s["tasks"] = [{"name": _text(n, 40), "label": self.labels.get(n, n)[:40], "state": "pending", "text": "待办"}
                          for n in list(tasks)[:24] if isinstance(n, str)]
            s["progress"] = {"done": 0, "total": len(s["tasks"])}
            if type(click_cap) is int and click_cap >= 0:
                s["clicks"]["cap"] = click_cap
        self.update(apply, force=True)

    def step(self, text):
        if isinstance(text, str) and text:
            self.update(lambda s: s.__setitem__("step", text[:MAX_TEXT]))

    def hold(self, text):
        """A task's sticky step (e.g. Collabo waiting for a full stage): node phases do not overwrite it until the
        task clears it (``None``) or finishes."""
        text = text[:MAX_TEXT] if isinstance(text, str) and text else None

        def apply(s):
            if s.get("hold") == text:
                return
            s["hold"] = text
            if text:
                s["step"] = text
        self.update(apply, force=True)

    def frame_seen(self):
        self.update(lambda s: s.__setitem__("last_frame_at", self.wall()))

    def observe(self, event, fields):
        """``RunControl.emit`` hook: the fixed run-event schema only."""
        try:
            task = fields.get("task")
            if event == "run.started":
                return
            if event == "task.started":
                self.update(lambda s: self._task_started(s, task), force=True)
            elif event == "task.finished":
                self.update(lambda s: self._task_finished(s, task, fields.get("status"), fields.get("reason")), force=True)
            elif event == "node":
                phase = node_phase(fields.get("node"))

                def apply(s):
                    s["last_frame_at"] = self.wall()
                    if s.get("hold"):
                        s["step"] = s["hold"]
                    elif phase and not (s.get("wing") and task in ("Wing", "AutoProduce")):
                        s["step"] = f"{self.labels.get(task, task or '')} · {phase}" if task else phase
                self.update(apply)
            elif event == "click.result" and fields.get("result") == "sent":
                self.update(lambda s: s["clicks"].__setitem__("used", s["clicks"]["used"] + 1))
            elif event == "run.stop_requested":
                def apply(s):
                    s["state"] = "stopping"
                    s["step"] = "停止中"
                self.update(apply, force=True)
            elif event == "run.finished":
                self.finish(fields.get("status"))
        except Exception:
            pass

    def finish(self, status):
        def apply(s):
            s["state"] = "finished"
            s["outcome"] = status if isinstance(status, str) else "error"
            s["outcome_text"] = RUN_RESULT.get(s["outcome"], "运行结束")
            s["current"] = None
            s["step"] = s["outcome_text"]
            for t in s["tasks"]:
                if t["state"] in ("pending", "running"):
                    t["state"], t["text"] = ("skipped", "跳过") if t["state"] == "pending" else ("stopped", "中断")
            s["progress"]["done"] = sum(t["state"] not in ("pending", "running") for t in s["tasks"])
        self.update(apply, force=True)

    def wing(self, mutate):
        """``mutate(wing_dict)`` updates the W.I.N.G. section (agent.core.wing_live_feed)."""
        def apply(s):
            if s.get("wing") is None:
                s["wing"] = {"season": None, "weeks_left": None, "stats": None, "sp": None, "fans": None,
                             "fans_left": None, "stamina": None, "decision": None, "events": [], "live": None}
            mutate(s["wing"])
            s["wing"]["events"] = s["wing"]["events"][-MAX_EVENTS:]
        self.update(apply)

    # ---- state & writing ------------------------------------------------------------------------------------
    def _task_started(self, s, name):
        s["current"] = name
        s["hold"] = None
        for t in s["tasks"]:
            if t["name"] == name:
                t["state"], t["text"] = "running", "进行"
                s["step"] = t["label"]
                return
        s["tasks"].append({"name": _text(name, 40), "label": self.labels.get(name, name or "")[:40],
                           "state": "running", "text": "进行"})
        s["progress"]["total"] = len(s["tasks"])

    def _task_finished(self, s, name, status, reason):
        state, text = TASK_RESULT.get(status, ("failed", "待核对"))
        if status == "skipped" and reason == "run_stopped":
            state, text = "skipped", "跳过"
        for t in s["tasks"]:
            if t["name"] == name:
                t["state"], t["text"] = state, text
        if s["current"] == name:
            s["current"] = None
            s["hold"] = None
        s["progress"]["done"] = sum(t["state"] not in ("pending", "running") for t in s["tasks"])

    def update(self, mutate, *, force=False):
        with self._lock:
            if self._closed:
                return
            try:
                mutate(self.snapshot)
            except Exception:
                return
            self._dirty = True
            now = self.clock()
            if force or self._last_write is None or now - self._last_write >= MIN_INTERVAL_S:
                if force and self._last_write is not None and now - self._last_write < MIN_INTERVAL_S:
                    self._defer(MIN_INTERVAL_S - (now - self._last_write))   # keep the ≤2/s budget
                    return
                self._write()
            else:
                self._defer(MIN_INTERVAL_S - (now - self._last_write))

    def _defer(self, delay):
        delay = max(0.0, delay)
        due = self.clock() + delay
        if self._pending is not None:
            if self._pending_due <= due + 1e-9:
                return
            try:
                self._pending.cancel()      # a heartbeat further out: bring the flush forward
            except Exception:
                pass
        try:
            self._pending, self._pending_due = self._schedule(delay, self._deferred), due
        except Exception:
            self._pending = None

    def _deferred(self):
        with self._lock:
            self._pending = None
            if self._closed:
                return
            if self._dirty:
                self._write()
            elif self.snapshot["state"] in ("running", "stopping"):
                self._write()      # heartbeat

    def flush(self):
        with self._lock:
            if self._dirty and not self._closed:
                self._write()

    def close(self):
        with self._lock:
            self.flush()
            self._closed = True
            if self._pending is not None:
                try:
                    self._pending.cancel()
                except Exception:
                    pass
                self._pending = None

    def _write(self):
        s = self.snapshot
        s["updated_at"] = self.wall()
        s["elapsed_s"] = int(max(0.0, self.clock() - self.started))
        try:
            data = json.dumps(s, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(data.encode("utf-8")) > MAX_BYTES and s.get("wing"):
                s["wing"]["events"] = s["wing"]["events"][-4:]
                data = json.dumps(s, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(data, encoding="utf-8")
            for attempt in range(3):      # a reader holding the file open on Windows: brief retry, then skip
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.02)
            self.writes += 1
            self._dirty = False
        except Exception:
            self.write_failures += 1
        self._last_write = self.clock()
        if s["state"] in ("running", "stopping") and self._pending is None:
            try:
                self._pending = self._schedule(self.heartbeat_s, self._deferred)
                self._pending_due = self.clock() + self.heartbeat_s
            except Exception:
                self._pending = None


# ---- process-wide active instance (the W.I.N.G. agent reports through it; no-op when no run is live) -----------
_ACTIVE: LiveStatus | None = None


def activate(status):
    global _ACTIVE
    _ACTIVE = status


def deactivate(status=None):
    global _ACTIVE
    if status is None or _ACTIVE is status:
        _ACTIVE = None


def current():
    return _ACTIVE


def read(path=DEFAULT_PATH):
    """The last snapshot (or None) -- for tools and tests."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
