"""「只读取资源」 (ResourceRead, role-play D6/A8, 2026-10-26): one read of the home counters, no input of its own.

The task runs after Startup (which leaves a clean home). ``SC.ResourceRead.Read`` is a custom recognition on the frame
Maa already captured: it hits only on a clean home (``SC.Common.Home.Guard`` hit, ``SC.Common.HomeClean`` raw miss) whose
マニー / 体力 / フェス参加権 all pass the same strict OCR gate as the passive observer (resource_observer.readings_from_result,
0.90). After ``MAX_UNREADABLE`` clean-home frames without a complete read it hits with ``ok: False`` so the task fails
instead of polling until its timeout. Any other screen is a miss: the shared known-popup closes / loading wait of the
task's next list handle it, anything else times out and fails (no navigation of its own).

``SC.ResourceRead.Report`` (custom action) adds one ``resources`` report (``source: resource_read``) with the counters it
read; the reading store keeps them like the passive readings. Values are display readings, never input or spend
authority; ジュエル is shown only.
"""
from __future__ import annotations

import json
import logging

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import item_inventory as ii
from agent.core import resource_observer as ro

LOGGER = logging.getLogger(__name__)
RECOGNITION = "SC.ResourceRead.Home"
ACTION = "SC.ResourceRead.Report"
SOURCE = "resource_read"
REQUIRED = ("money", "stamina", "fes")
MAX_UNREADABLE = 3


class ResourceReadRuntime:
    """One per launcher run. ``recognise`` decides on a frame (no side effect but its own counters); ``report`` emits."""

    def __init__(self, sink, *, vision=ro.MaaVision, now=ro.utc_now, min_ocr=ro.DEFAULTS["min_ocr"],
                 max_unreadable=MAX_UNREADABLE):
        self.sink, self.vision, self.now = sink, vision, now
        self.min_ocr, self.max_unreadable = min_ocr, max_unreadable
        self.unreadable = 0
        self.result = None

    def recognise(self, context, image):
        """A detail dict when the task should report (complete read, or too many unreadable clean-home frames), else None."""
        if not ro._image_ok(image):
            return None
        vision = self.vision(context)
        if not vision.hit(ro.HOME_GUARD, image) or vision.hit(ro.HOME_FOREIGN, image):
            return None
        texts = []
        for roi in ro.home_rois():
            texts.extend(vision.ocr(image, roi))
        result = ii.recognise(ro._pil(image), None, "home_counters", texts)
        items, rejected = ro.readings_from_result(result, self.min_ocr) if result.get("screen_ok") else ({}, [])
        missing = [key for key in REQUIRED if key not in items]
        if missing:
            self.unreadable += 1
            if self.unreadable < self.max_unreadable:
                return None
        detail = {"ok": not missing, "items": items, "rejected": rejected, "missing": missing,
                  "frames": self.unreadable + (0 if missing else 1)}
        if not result.get("screen_ok"):
            detail["refused"] = str(result.get("refused") or "screen_guard_failed")
        gacha = ro.gacha_reading(vision, image)
        if gacha is not None:
            detail["gacha"] = gacha
        self.result = detail
        return detail

    def report(self):
        """Emit the read (``resources`` report) and return whether all required counters were read."""
        detail, self.result, self.unreadable = self.result, None, 0
        if detail is None:
            self.sink.add("resources", {"ok": False, "source": SOURCE, "layout": "home_counters", "error": "no_read"})
            return False
        payload = {"ok": detail["ok"], "source": SOURCE, "layout": "home_counters", "observed_at": self.now().isoformat(),
                   **{key: detail[key] for key in ("items", "rejected", "missing", "frames", "refused", "gacha")
                      if key in detail}}
        self.sink.add("resources", payload)
        LOGGER.info("ResourceRead: %s", json.dumps(payload, ensure_ascii=False))
        return detail["ok"]


_DEFAULT = None


def default_runtime():
    """The AgentServer (PI shell) instance, reporting to the shared task_actions sink."""
    global _DEFAULT
    if _DEFAULT is None:
        from agent.core.task_actions import SINK

        _DEFAULT = ResourceReadRuntime(SINK)
    return _DEFAULT


class ResourceReadHome(CustomRecognition):
    """Clean home with a complete (or given-up) counter read: hit with the read as detail. Never captures or clicks."""

    def __init__(self, runtime=None):
        super().__init__()
        self.runtime = runtime

    def analyze(self, context, argv):
        try:
            detail = (self.runtime or default_runtime()).recognise(context, argv.image)
        except Exception:  # a broken read never clicks or retries: no hit, the task times out and fails
            LOGGER.exception("ResourceRead recognition failed")
            return None
        return None if detail is None else CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail=detail)


class ResourceReadReport(CustomAction):
    """Emit the ``resources`` report of the read the recognition kept; fails the task on a partial read."""

    def __init__(self, runtime=None):
        super().__init__()
        self.runtime = runtime

    def run(self, context, argv):
        del context, argv
        return (self.runtime or default_runtime()).report()


def registrations(runtime):
    """({recognition name: instance}, {action name: instance}) for the launcher's Resource."""
    return {RECOGNITION: ResourceReadHome(runtime)}, {ACTION: ResourceReadReport(runtime)}
