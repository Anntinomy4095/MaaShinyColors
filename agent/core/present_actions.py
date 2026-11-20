"""Maa custom actions for the プレゼント bulk receive (SC.Present.*) and the weekly check (SC.Weekly.Read).

Decisions live in the pure modules ``present_claim`` and ``weekly_missions``. These classes only take a
screenshot, run named recognitions on that same image and record JSON-safe reports.

Input stays in guarded Pipeline Click nodes. The launcher explicitly enables this runtime only for an input
controller; AgentServer and read-only callers remain closed. The ledger is persisted before the bulk click.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import threading
import time

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import present_claim as pc
from agent.core import weekly_missions as wm
from agent.core.task_actions import SINK, _latest_image, _ocr_items, _params

LOGGER = logging.getLogger(__name__)
PRESENT_LIVE_INPUT = False   # default for AgentServer / standalone registrations; launcher supplies its controller mode
# Enabled 「一括受取」 pink (RGB): 4469 px of bulk_roi on the native present list (2026-10-07; the mission page's
# button gives 4376 px). The native empty box has zero pink pixels; anything below the minimum stops.
BULK_PINK_LOWER = pc.PINK_LOWER
BULK_PINK_UPPER = pc.PINK_UPPER
BULK_PINK_MIN = 2000
SUCCESS_OUTCOMES = frozenset({"box_empty", "bulk_disabled", "received", "received_partial"})
SCROLL_ACTION = "present-list-scroll-down"   # reserved wheel action (2026-10-10, unverified until a supervised run)


def scroll_wired(table=None):
    """The list wheel action is enabled in the action table (a disabled catalog entry never scrolls)."""
    from agent.core import pipeline_actions

    try:
        table = pipeline_actions.default_table() if table is None else table
        return table.get(SCROLL_ACTION).enabled
    except (ValueError, FileNotFoundError):
        return False


def frame_ref(image, seq):
    data = image.tobytes() if hasattr(image, "tobytes") else bytes(image)
    return pc.FrameRef(hashlib.sha256(data).hexdigest(), seq, time.monotonic())


def bulk_enabled(image_bgr, layout=pc.LAYOUT):
    import numpy as np

    image = np.asarray(image_bgr)
    if image.ndim != 3 or (image.shape[1], image.shape[0]) != pc.CANVAS:
        return None
    return pc.pink_count(image[:, :, 2::-1], layout["bulk_roi"]) >= BULK_PINK_MIN


def page_tab(image_bgr):
    """"unclaimed" / "received" / None from the tab label pixels of a BGR frame."""
    import numpy as np

    image = np.asarray(image_bgr)
    if image.ndim != 3:
        return None
    return pc.tab_selection(image[:, :, 2::-1])["selected"]


def row_pixels_digest(image_bgr, page):
    """Exact text-panel proof of the wholly visible rows; leave the animated empty-list background outside it.

    A scrolled list shows cut rows at its edges; only complete rows are bound. A page whose rows were not read binds
    nothing here (the bulk guard still re-reads the counter and the pink button on the fresh frame)."""
    import numpy as np

    image = np.asarray(image_bgr)
    if image.ndim != 3 or (image.shape[1], image.shape[0]) != pc.CANVAS or page is None:
        return None
    left, right = pc.LAYOUT["row_text_x"], pc.LAYOUT["row_button_x"]
    digest = hashlib.sha256()
    for row in (row for row in page.rows if pc.row_complete(row)):
        top, bottom = row["y"] - 62, row["y"] + 62
        digest.update(image[top:bottom, left:right].tobytes())
    return digest.hexdigest()


def page_thumb(image_bgr):
    import numpy as np

    image = np.asarray(image_bgr)
    if image.ndim != 3:
        return None
    return pc.scroll_thumb(image[:, :, 2::-1])


def _hit(context, node, image):
    try:
        detail = context.run_recognition(node, image)
    except Exception:  # an unloadable node is not evidence
        return None
    return bool(detail is not None and getattr(detail, "hit", True))


class PresentRuntime:
    def __init__(self, sink=SINK, ledger_path=pc.LEDGER_PATH, clock=None, input_enabled=False):
        self.sink = sink
        self.ledger_path = ledger_path
        self.clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self.input_enabled = input_enabled is True
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.options = pc.PresentOptions()
        self.ledger = None
        self.decision = None
        self.page_before = None
        self.record = None
        self.receipt = None
        self.outcome = None
        self.seq = 0
        self.phase = "initial"
        self.claim_rows_digest = None
        self.scan = None           # pc.PresentScan while a box longer than one screen is read
        self.page_visible = None   # the visible page the claim decision is bound to (== page_before unless scrolled)
        self.receipt_items = None  # 「<item> ×N」 lines read from the result dialog (evidence for the report)

    def next_frame(self, image):
        self.seq += 1
        return frame_ref(image, self.seq)

    def report(self, outcome, **extra):
        self.outcome = outcome
        if "page" not in extra and self.page_before is not None:
            extra = {**extra, "page": self.page_before.to_dict()}  # rows read by this run: the passive reading store
        if self.scan is not None and "scan" not in extra:
            extra = {**extra, "scan": self.scan.to_dict()}
        if self.receipt_items is not None and "receipt_items" not in extra:
            extra = {**extra, "receipt_items": self.receipt_items}
        payload = {"outcome": outcome, "options": vars(self.options) if self.options else None,
                   "decision": None if self.decision is None else self.decision.to_dict(),
                   "record": self.record, "live_input": self.input_enabled, **extra}
        self.sink.add("present", payload)
        LOGGER.info("Present: %s", json.dumps(payload, ensure_ascii=False, default=str))


RUNTIME = PresentRuntime()


class _PresentAction(CustomAction):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.rt = runtime


class PresentBeginAction(_PresentAction):
    def run(self, context, argv):
        rt = self.rt
        rt.reset()
        p = _params(argv.custom_action_param)
        try:
            merged = {}
            for name in p.get("config_nodes", []):
                merged.update((context.get_node_data(name) or {}).get("attach") or {})
            rt.options = pc.PresentOptions.from_params(merged)
            rt.ledger = pc.PresentLedger(rt.ledger_path)
        except Exception as exc:
            rt.report("config_invalid", error=str(exc)[:200])
            return False
        if not rt.options.enabled:
            rt.report("disabled")
            return False
        return True


class PresentDecideAction(_PresentAction):
    """Read the present page on one fresh frame and decide; True only for a ``claim`` decision."""

    def run(self, context, argv):
        rt = self.rt
        p = _params(argv.custom_action_param)
        try:
            image = _latest_image(context)
            frame = rt.next_frame(image)
            page = pc.parse_page(_ocr_items(context.run_recognition(p.get("page_node", "SC.Present.PageOCR"), image)),
                                 tab=page_tab(image))
            overlay = _hit(context, p.get("overlay_node", "SC.Present.Overlay"), image)
            rt.ledger = rt.ledger or pc.PresentLedger(rt.ledger_path)
            decision = pc.decide(page, options=rt.options, page_guard=True, overlay=overlay is not False,
                                 bulk_enabled=bulk_enabled(image), frame=frame)
        except Exception as exc:
            LOGGER.exception("Present decide failed")
            rt.report("decide_error", error=str(exc)[:200])
            return False
        rt.decision, rt.page_before, rt.page_visible = decision, page, page
        if decision.kind != "claim":
            # A grey / missing 一括受取 is a skip, reported as a success.
            outcome = decision.reasons[0] if decision.kind == "empty" else "stopped"
            rt.report(outcome, page=page.to_dict())
            return False
        rt.phase = "decided"
        return True


def _stop_scan(rt, page, *reasons):
    rt.decision = pc.dataclasses.replace(rt.decision, kind="stop", target=None, reasons=reasons)
    rt.report("stopped", page=page.to_dict())
    return False


class PresentScanAction(_PresentAction):
    """After one guarded wheel step: read the fresh page, extend the complete list, decide once it is complete."""

    def run(self, context, argv):
        rt = self.rt
        p = _params(argv.custom_action_param)
        if rt.scan is None or rt.phase != "scroll_requested":
            rt.report("scan_without_scroll")
            return False
        try:
            image = _latest_image(context)
            frame = rt.next_frame(image)
            page = pc.parse_page(_ocr_items(context.run_recognition(p.get("page_node", "SC.Present.PageOCR"), image)),
                                 tab=page_tab(image))
            if not rt.scan.add(page, page_thumb(image)):
                return _stop_scan(rt, page, "rows_not_all_visible", f"scan:{rt.scan.reason}")
            if not rt.scan.complete:
                if not rt.scan.wants_scroll():
                    return _stop_scan(rt, page, "rows_not_all_visible", f"scan:{rt.scan.reason}")
                rt.phase = "scanning"
                return True
            merged = rt.scan.merged_page(page)
            overlay = _hit(context, p.get("overlay_node", "SC.Present.Overlay"), image)
            decision = pc.decide(merged, options=rt.options, page_guard=True, overlay=overlay is not False,
                                 bulk_enabled=bulk_enabled(image), frame=frame)
        except Exception as exc:
            LOGGER.exception("Present scan failed")
            rt.report("decide_error", error=str(exc)[:200])
            return False
        rt.decision, rt.page_before, rt.page_visible = decision, merged, page
        if decision.kind != "claim":
            rt.report("stopped", page=merged.to_dict())
            return False
        rt.phase = "decided"
        return True


class PresentIntentAction(_PresentAction):
    """Persist the intent before entering the one-shot, frame-guarded bulk click."""

    def run(self, context, argv):
        rt = self.rt
        try:
            if rt.decision is None or rt.decision.frame is None:
                rt.report("intent_rejected", reason="no_claim_decision")
                return False
            if rt.input_enabled:
                image = _latest_image(context)
                page = pc.parse_page(_ocr_items(context.run_recognition("SC.Present.PageOCR", image)), tab=page_tab(image))
                if (page != rt.page_visible or bulk_enabled(image) is not True
                        or _hit(context, "SC.Present.Overlay", image) is not False):
                    rt.report("intent_rejected", reason="claim_list_changed")
                    return False
                # Animated home/avatar pixels may change; bind the unchanged receivable rows to this fresh frame.
                rt.decision = pc.dataclasses.replace(rt.decision, frame=rt.next_frame(image))
                rt.claim_rows_digest = row_pixels_digest(image, rt.page_visible)
                if rt.claim_rows_digest is None:
                    rt.report("intent_rejected", reason="claim_rows_unbound")
                    return False
            # Same pixels as the decision frame (sha), age measured from the decision capture.
            sha = frame_ref(_latest_image(context), 1).sha256
            current = pc.FrameRef(sha, rt.decision.frame.seq, rt.decision.frame.captured_at)
            problem = pc.frame_matches(rt.decision, current, time.monotonic())
            if problem:
                rt.report("intent_rejected", reason=problem)
                return False
            now = rt.clock()
            rt.record = rt.ledger.begin(pc.cycle_id(now), rt.decision, now)
            if not rt.input_enabled:
                rt.record = rt.ledger.transition(rt.record["id"], "refused", now, "live_input_closed")
                rt.report("not_sent_live_input_closed")
                return False
            rt.phase = "bulk_ready"
            return True
        except Exception as exc:
            LOGGER.exception("Present intent failed")
            if rt.record and rt.record.get("state") == "pending":
                rt.record = rt.ledger.transition(rt.record["id"], "uncertain", rt.clock(), f"intent_error:{exc}")
            rt.report("intent_error", error=str(exc)[:200])
            return False


class PresentReadyRecognition(CustomRecognition):
    """Bind each of the three clicks to its recorded attempt and its observed stage."""

    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.rt = runtime

    def analyze(self, context, argv):
        rt = self.rt
        stage = _params(argv.custom_recognition_param).get("stage")
        if stage == "intent":
            # Only a claim decision (on one screen or after a complete scan) may enter the intent node.
            if rt.decision is None or rt.decision.kind != "claim" or rt.phase != "decided":
                return None
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage})
        if stage == "scroll":
            if not rt.input_enabled or rt.scan is None or rt.phase != "scanning" or not rt.scan.wants_scroll():
                return None
            rt.scan.steps += 1
            rt.phase = "scroll_requested"
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage, "step": rt.scan.steps})
        expected = {"bulk": ("bulk_ready", "pending"),
                    "confirmation": ("bulk_requested", "pending"),
                    "close": ("result_observed", "receipt_observed"),
                    "settled": ("close_requested", "receipt_observed")}.get(stage)
        if (not rt.input_enabled or expected is None or rt.record is None
                or (rt.phase, rt.record.get("state")) != expected):
            return None
        try:
            if stage == "settled":
                count = pc.parse_count(_ocr_items(context.run_recognition("SC.Present.CountOCR", argv.image)))
                before = None if rt.page_before is None else rt.page_before.count
                if count is None or count < 0 or (before is not None and count >= before):
                    return None
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage, "count": count})
            if stage == "bulk":
                image = argv.image
                count = pc.parse_count(_ocr_items(context.run_recognition("SC.Present.CountOCR", image)))
                # Re-OCR of the entire page exceeded the host's five-second permit on the live device.
                # The fresh parent still proves title/tab/footer/undimmed back/bulk; verify the already-read
                # item text exactly and only OCR the small counter on that same fresh image.
                if (rt.claim_rows_digest is None or row_pixels_digest(image, rt.page_visible) != rt.claim_rows_digest
                        or count != rt.page_before.count or bulk_enabled(image) is not True):
                    return None
            rt.phase = {"bulk": "bulk_requested", "confirmation": "confirmation_requested",
                        "close": "close_requested"}[stage]
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage})
        except Exception:
            LOGGER.exception("Present click guard failed")
            return None


class PresentResultAction(_PresentAction):
    """Classify the dialog after the press (never presses anything)."""

    def run(self, context, argv):
        rt = self.rt
        p = _params(argv.custom_action_param)
        if not rt.record or rt.record.get("state") != "pending" or rt.phase != "confirmation_requested":
            rt.report("result_without_pending")
            return False
        try:
            image = _latest_image(context)
            items = _ocr_items(context.run_recognition(p.get("dialog_node", "SC.Present.DialogOCR"), image))
            receipt, why = pc.classify_result(items)
            if receipt == "receipt":
                # The item lines are evidence only; the fresh counter / empty box decides the result (``confirm``).
                rt.receipt_items = pc.receipt_lines(items)
        except Exception as exc:
            receipt, why = "uncertain", [f"read_error:{type(exc).__name__}"]
        rt.receipt = receipt
        state = "receipt_observed" if receipt == "receipt" else receipt
        rt.record = rt.ledger.transition(rt.record["id"], state, rt.clock(), ",".join(why) or None)
        if receipt != "receipt":
            rt.report(f"result_{receipt}")
            return False
        rt.phase = "result_observed"
        return True


class PresentConfirmAction(_PresentAction):
    """After the dialog closed: re-read the page on a fresh frame and finish the record."""

    def run(self, context, argv):
        rt = self.rt
        p = _params(argv.custom_action_param)
        if not rt.record or rt.record.get("state") != "receipt_observed":
            rt.report("confirm_without_receipt")
            return False
        try:
            image = _latest_image(context)
            after = pc.parse_page(_ocr_items(context.run_recognition(p.get("page_node", "SC.Present.PageOCR"), image)),
                                  tab=page_tab(image))
            final = pc.confirm(rt.page_before, after, rt.receipt)
        except Exception as exc:
            final, after = "uncertain", None
            LOGGER.exception("Present confirm failed: %s", exc)
        rt.record = rt.ledger.transition(rt.record["id"], final, rt.clock())
        rt.report(final, page_after=None if after is None else after.to_dict())
        return final in ("received", "received_partial")


class PresentStopAction(_PresentAction):
    """Terminal node of every failure path: report once; success only for an already-final good outcome."""

    def run(self, context, argv):
        del context
        rt = self.rt
        reason = _params(argv.custom_action_param).get("reason", "stop")
        if rt.record and rt.record.get("state") in ("pending", "receipt_observed"):
            rt.record = rt.ledger.transition(rt.record["id"], "uncertain", rt.clock(), reason)
            rt.report("uncertain", reason=reason)
        if rt.outcome is None:
            rt.report(f"stopped:{reason}")
        return rt.outcome in SUCCESS_OUTCOMES


class WeeklyReadAction(CustomAction):
    """Read the weekly tab on one frame, store the status; True only for a trusted count."""

    def __init__(self, sink=SINK, store_path=wm.STORE_PATH, clock=None):
        super().__init__()
        self.sink = sink
        self.store_path = store_path
        self.clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))

    def run(self, context, argv):
        p = _params(argv.custom_action_param)
        try:
            image = _latest_image(context)
            sha = hashlib.sha256(image.tobytes()).hexdigest()
            list_results = _ocr_items(context.run_recognition(p.get("list_node", "SC.Weekly.ListOCR"), image))
            timer_results = _ocr_items(context.run_recognition(p.get("timer_node", "SC.Weekly.TimerOCR"), image))
            overlay = _hit(context, p.get("overlay_node", "SC.Weekly.Overlay"), image)
            status = wm.evaluate(list_results=list_results, timer_results=timer_results, now=self.clock(),
                                 tab=wm.tab_selection(image[:, :, 2::-1]), page_guard=True,
                                 overlay=overlay is not False, frame_sha256=sha)
            wm.save_status(status, self.store_path)
        except Exception as exc:
            LOGGER.exception("Weekly read failed")
            self.sink.add("weekly", {"status": "unknown", "error": str(exc)[:200]})
            return False
        result = wm.weekly_remaining(status, now=self.clock())
        self.sink.add("weekly", result.to_dict())
        LOGGER.info("Weekly: %s", json.dumps(result.to_dict(), ensure_ascii=False))
        return result.remaining is not None


def present_registrations(sink=SINK, ledger_path=pc.LEDGER_PATH, weekly_store=wm.STORE_PATH, *, runtime=None):
    """{name: instance} for registering on a Resource outside the AgentServer (tests / launcher)."""
    runtime = runtime or PresentRuntime(sink, ledger_path)
    return {
        "SC.Present.Begin": PresentBeginAction(runtime),
        "SC.Present.Decide": PresentDecideAction(runtime),
        "SC.Present.Scan": PresentScanAction(runtime),
        "SC.Present.Intent": PresentIntentAction(runtime),
        "SC.Present.Result": PresentResultAction(runtime),
        "SC.Present.Confirm": PresentConfirmAction(runtime),
        "SC.Present.Stop": PresentStopAction(runtime),
        "SC.Weekly.Read": WeeklyReadAction(sink, weekly_store),
    }
