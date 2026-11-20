"""Custom actions of the B3 PI tasks (Startup / MissionStatus / Diagnostics / placeholders).

The classes import only ``maa.custom_action`` so the thin launcher (tools/run_tasks.py) can register
instances directly on its Resource, while ``agent/custom/action/tasks.py`` registers the same classes
with the AgentServer for PI shells. None of these actions sends input: they read the latest frame,
run named recognitions and record a JSON-safe report.

Reports go to an in-process ``ReportSink`` (the launcher drains it per task) and to the log.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from pathlib import Path

from maa.custom_action import CustomAction

from agent.core.mission_status import build_report
from agent.core.task_failure import fail_reason

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets" / "private"
DIAGNOSTICS_DIR = PRIVATE / "run_logs" / "diagnostics"
MAX_CANDIDATES = 64


def _params(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class ReportSink:
    """Thread-safe list of task reports; ``drain()`` returns and clears them."""

    def __init__(self, on_report=None):
        self._lock = threading.Lock()
        self._items = []
        self._on_report = on_report
        self._listeners = []

    def add_listener(self, listener):
        """Passive consumers of reports (e.g. the reading store); a failing listener never affects the task."""
        self._listeners.append(listener)

    def add(self, kind, payload):
        report = {"kind": kind, **payload}
        with self._lock:
            self._items.append(report)
        if self._on_report is not None:
            self._on_report(report)
        for listener in list(self._listeners):
            try:
                listener(report)
            except Exception:
                logging.getLogger(__name__).exception("report listener failed")

    def drain(self):
        with self._lock:
            items, self._items = self._items, []
        return items


SINK = ReportSink()


def _ocr_items(detail):
    if detail is None:
        return []
    out = []
    for result in getattr(detail, "all_results", None) or []:
        text = getattr(result, "text", None)
        box = getattr(result, "box", None)
        if isinstance(text, str) and box is not None:
            out.append({"text": text, "box": [int(v) for v in list(box)[:4]],
                        "score": float(getattr(result, "score", 0.0))})
    return out


def _latest_image(context):
    controller = context.tasker.controller
    image = controller.cached_image
    if image is None or getattr(image, "size", 0) == 0:
        image = controller.post_screencap().wait().get()
    return image


class EnvironmentCheckAction(CustomAction):
    """SC.Diagnostics: resource/agent presence and the runtime mode; no screenshot, no input."""

    def run(self, context, argv):
        del context, argv
        from agent.core.runtime_state import get_runtime_mode

        resource_exists = any(path.exists() for path in (ROOT / "assets" / "resource" / "base", ROOT / "resource"))
        mode = get_runtime_mode()
        checks = {"project_root": str(ROOT), "resource_root": resource_exists,
                  "agent_core": (ROOT / "agent" / "core").exists(), "runtime_mode": mode.mode,
                  "input_enabled": mode.allow_input}
        LOGGER.info("Diagnostics: %s", json.dumps(checks, ensure_ascii=False, sort_keys=True))
        return bool(resource_exists and checks["agent_core"])


class FailTaskAction(CustomAction):
    """Same contract as SC.FailTask in agent/custom/action/common.py, plus a sink record."""

    def __init__(self, sink=SINK):
        super().__init__()
        self.sink = sink

    def run(self, context, argv):
        del context
        reason = fail_reason(argv.custom_action_param)
        self.sink.add("fail", {"node": argv.node_name, "reason": reason})
        LOGGER.warning("SC.FailTask: node=%s reason=%s", argv.node_name, reason)
        return False


class MissionStatusReadAction(CustomAction):
    """Read the visible デイリー rows; never clicks or scrolls."""

    def __init__(self, sink=SINK):
        super().__init__()
        self.sink = sink

    def run(self, context, argv):
        params = _params(argv.custom_action_param)
        list_node = params.get("list_node", "SC.MissionStatus.ListOCR")
        counter_node = params.get("counter_node", "SC.MissionStatus.CounterOCR")
        try:
            image = _latest_image(context)
            rows = _ocr_items(context.run_recognition(list_node, image))
            counter = _ocr_items(context.run_recognition(counter_node, image))
            report = build_report(rows, counter)
        except Exception as exc:  # report and fail the task; never retry
            LOGGER.exception("MissionStatus read failed")
            self.sink.add("mission_status", {"ok": False, "error": str(exc)[:200]})
            return False
        self.sink.add("mission_status", report)
        LOGGER.info("MissionStatus: %s", json.dumps(report, ensure_ascii=False))
        return bool(report["ok"])


def save_frame(save_dir, now, image):
    """BGR frame -> ``<save_dir>/<UTC stamp>.png`` (dev_assets/private only)."""
    from PIL import Image

    root = Path(save_dir).resolve()
    if not root.is_relative_to(PRIVATE.resolve()):
        raise ValueError("诊断截图只能写入 dev_assets/private")
    root.mkdir(parents=True, exist_ok=True)
    path = root / (now.strftime("%Y%m%dT%H%M%S%fZ") + ".png")
    Image.fromarray(image[:, :, ::-1].copy()).save(path, format="PNG")
    return str(path)


class ClassifyScreenAction(CustomAction):
    """Screenshot once, run each candidate guard, report every hit; optionally save the frame."""

    def __init__(self, sink=SINK, save_dir=DIAGNOSTICS_DIR, save=True, now=None):
        super().__init__()
        self.sink, self.save_dir, self.save = sink, Path(save_dir), save
        self.now = now or (lambda: _dt.datetime.now(_dt.timezone.utc))

    def _save(self, image):
        return save_frame(self.save_dir, self.now(), image)

    def run(self, context, argv):
        params = _params(argv.custom_action_param)
        candidates = [c for c in params.get("candidates", []) if isinstance(c, str)][:MAX_CANDIDATES]
        home_clean = params.get("home_clean")
        try:
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None or getattr(image, "size", 0) == 0:
                raise ValueError("截图失败")
            hits = []
            for name in candidates:
                detail = context.run_recognition(name, image)
                if detail is not None and detail.hit:
                    hits.append(name)
            clean = None
            if isinstance(home_clean, str) and "SC.Common.Home.Guard" in hits:
                # Context.run_recognition evaluates the node's algorithm only; the node-level `inverse` of
                # SC.Common.HomeClean is not applied (measured with MaaFramework 5.12.3). A hit therefore
                # means "a foreign page/popup anchor is present", i.e. the Home is NOT clean.
                detail = context.run_recognition(home_clean, image)
                clean = bool(detail is not None and not detail.hit)
            report = {"shape": list(image.shape), "hits": hits, "home_clean": clean,
                      "screen": classify_hits(hits, clean),
                      "screenshot": self._save(image) if self.save else None}
        except Exception as exc:
            LOGGER.exception("Diagnostics classify failed")
            self.sink.add("diagnostics", {"ok": False, "error": str(exc)[:200]})
            return False
        self.sink.add("diagnostics", {"ok": True, **report})
        LOGGER.info("Diagnostics screen: %s", json.dumps(report, ensure_ascii=False))
        return True


class FailureSnapshotAction(CustomAction):
    """After a failed task (launcher only): one screenshot, every page guard evaluated, near misses reported.

    Read-only. The frame is saved under ``save_dir`` (dev_assets/private) so an unknown layout can be fixed from it."""

    def __init__(self, sink=SINK, save_dir=DIAGNOSTICS_DIR / "failures", save=True, now=None,
                 budget_s=None):
        super().__init__()
        from agent.core import guard_diagnosis as gd
        self.sink, self.save_dir, self.save = sink, Path(save_dir), save
        self.now = now or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self.budget_s = gd.DEFAULT_BUDGET_S if budget_s is None else budget_s
        self._nodes = self._guards = None

    def run(self, context, argv):
        from agent.core import guard_diagnosis as gd
        params = _params(argv.custom_action_param)
        try:
            if self._guards is None:
                self._nodes = gd.pipeline_nodes()
                self._guards = gd.page_guards(self._nodes)
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None or getattr(image, "size", 0) == 0:
                raise ValueError("截图失败")
            report = gd.evaluate(context, image, self._nodes, self._guards, budget_s=self.budget_s)
            report["task"] = params.get("task")
            report["screenshot"] = save_frame(self.save_dir, self.now(), image) if self.save else None
        except Exception as exc:
            LOGGER.exception("Failure snapshot failed")
            self.sink.add("failure_snapshot", {"ok": False, "error": str(exc)[:200]})
            return True
        self.sink.add("failure_snapshot", {"ok": True, **report})
        return True


def classify_hits(hits, home_clean):
    """One label for the current screen; ``unknown`` and ``ambiguous`` are explicit."""
    hits = list(hits)
    if not hits:
        return "unknown"
    if hits == ["SC.Common.Home.Guard"]:
        return "home_clean" if home_clean else "home_with_overlay"
    if len(hits) == 1:
        return hits[0]
    # Popups dim the page under them: a popup guard together with its background page is expected.
    popups = [h for h in hits if h.startswith("SC.Popup.") and h != "SC.Popup.LoginBoard"]
    if len(popups) == 1:
        return popups[0]
    return "ambiguous"


def launcher_actions(sink=SINK, save_diagnostics=True):
    """{name: instance} for registering on a Resource outside the AgentServer."""
    return {
        "SC.FailTask": FailTaskAction(sink),
        "SC.MissionStatus.Read": MissionStatusReadAction(sink),
        "SC.Diagnostics.ClassifyScreen": ClassifyScreenAction(sink, save=save_diagnostics),
        "SC.Diagnostics.FailureSnapshot": FailureSnapshotAction(sink, save=save_diagnostics),
        "SC.Diagnostics": EnvironmentCheckAction(),
    }
