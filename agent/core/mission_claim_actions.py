"""One daily-login claim through guarded Pipeline clicks and an existing private journal.

This adapter never sends input. Only the launcher supplies the original controller FrameCapture
and enables readiness. A pending event commits before the single claim click; a matching single
selected-ticket receipt and the same row's positive CLEAR marker are both required for completion.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import time
from pathlib import Path

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from agent.core import mission_bulk as mb
from agent.core import mission_claim_reader as reader
from agent.core import present_claim as pc
from agent.core import task_common as tc
from agent.core.frame_permit import CANONICAL_SHAPE, FrameCapture, MAX_AGE_S
from agent.core.mission_claim import ClaimReceipt
from agent.core.mission_claim_store import PRIVATE, DurableClaimTracker
from agent.core.task_actions import SINK, _latest_image, _ocr_items, _params

LOGGER = logging.getLogger(__name__)
JOURNAL_PATH = PRIVATE / "mission-claim" / "daily-login.sqlite3"
TEMPLATE_PATH = PRIVATE / "mission-claim" / "bronze-reference.png"
SILVER_TEMPLATE_PATH = PRIVATE / "mission-claim" / "silver-reference.png"
TEMPLATE_PATHS = {"bronze_ticket": TEMPLATE_PATH, "silver_ticket": SILVER_TEMPLATE_PATH}
TEMPLATE_DIGEST = reader.TEMPLATE_DIGESTS["bronze_ticket"]
CLAIM_RECT = (1042, 246, 168, 80)
RECEIPT_RECT = (350, 130, 580, 455)
RESULT_OK_RECT = (557, 480, 166, 80)
# 2026-10-12: frames of a stopped read / receipt / terminal check are saved as evidence (as Business does), at most
# EVIDENCE_FRAMES per run under dev_assets/private/run_logs/samples/missions-<stage>-<UTC>/.
EVIDENCE_FRAMES = 6


def _hit(context, node, image):
    detail = context.run_recognition(node, image)
    return detail is not None and getattr(detail, "hit", True) is True


def _overlay_clear(context, image):
    # run_recognition exposes raw recognition results, before node-level inverse is applied.
    return (not _hit(context, "SC.Weekly.Overlay", image)
            and not _hit(context, "SC.Missions.ErrorOCR", image))


def _digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def _inside(box, rect):
    return reader._inside(box, rect)


def load_ticket_template(expected_reward="bronze_ticket", path=None):
    """Fixed reviewed private reference, never an on-the-fly crop of the candidate row."""
    import numpy as np
    from PIL import Image

    if expected_reward not in TEMPLATE_PATHS:
        raise ValueError("mission_reward_unsupported")
    path = Path(TEMPLATE_PATHS[expected_reward] if path is None else path).resolve()
    if not path.is_relative_to(PRIVATE.resolve()):
        raise ValueError("mission_template_not_private")
    with Image.open(path) as source:
        template = np.asarray(source.convert("RGB")).copy()
    if not reader.template_verified(template, expected_reward):
        raise ValueError("mission_template_unverified")
    return template


def load_silver_template(path=SILVER_TEMPLATE_PATH):
    """Historical silver replay must continue to select its reward kind explicitly."""
    return load_ticket_template("silver_ticket", path)


def receipt_matches(image_bgr, results, template, *, expected_reward="bronze_ticket"):
    """Positive single-reward popup, one selected training ticket, and its enabled OK.

    The older jewel x100 single receipt and training-ticket x2 bulk receipt are negatives.
    The icon is compared in colour to the independently calibrated list reference.
    """
    import numpy as np
    from PIL import Image

    image = np.asarray(image_bgr)
    if image.shape != CANONICAL_SHAPE or image.dtype != np.uint8:
        return False
    items = reader._items(results)
    def exact(text, rect):
        return len([item for item in items if item["text"].replace(" ", "") == text
                    and _inside(item["box"], rect)]) == 1
    if (not exact("報酬受け取り", (398, 144, 290, 48))
            or not exact("以下の報酬を受け取りました", (468, 230, 355, 49))
            or not exact("OK", RESULT_OK_RECT)):
        return False
    quantity_lines = [item["text"].replace(" ", "") for item in items
                      if _inside(item["box"], (505, 309, 380, 131))
                      and re.search(r"[×x]\d+", item["text"].replace(" ", ""))]
    if len(quantity_lines) != 1 or not re.fullmatch(r"トレーニングチケット[×x]1", quantity_lines[0]):
        return False
    rgb = image[:, :, ::-1]
    if pc.pink_count(rgb, RESULT_OK_RECT) < 2000:
        return False
    reference = np.asarray(template)
    if not reader.template_verified(reference, expected_reward):
        return False
    region = rgb[316:422, 394:502].astype(np.float32)
    for scale in (1.0, 1.02, 0.98):
        size = (round(80 * scale), round(66 * scale))
        target = np.asarray(Image.fromarray(reference).resize(size)).astype(np.float32)
        energy = float(np.square(target).sum())
        for y in range(region.shape[0] - target.shape[0] + 1):
            for x in range(region.shape[1] - target.shape[1] + 1):
                patch = region[y:y + target.shape[0], x:x + target.shape[1]]
                denominator = (float(np.square(patch).sum()) * energy) ** .5
                if denominator and float(np.square(patch - target).sum()) / denominator <= reader.TEMPLATE_MAX_ERROR:
                    return True
    return False


class MissionRuntime:
    def __init__(self, sink=SINK, *, input_enabled=False, frame_source=None, frame_clock=None,
                 clock=None, journal_path=JOURNAL_PATH, expected_reward="bronze_ticket",
                 ticket_template_path=None, silver_template_path=None, bulk_ledger_path=mb.LEDGER_PATH,
                 sample_root=tc.SAMPLE_ROOT, authority=None):
        if expected_reward not in TEMPLATE_PATHS:
            raise ValueError("mission_reward_unsupported")
        if silver_template_path is not None:
            if expected_reward != "silver_ticket" or ticket_template_path is not None:
                raise ValueError("mission_reward_template_mismatch")
            ticket_template_path = silver_template_path
        self.sink, self.input_enabled = sink, input_enabled is True
        self.frame_source = frame_source
        self.frame_clock = frame_clock or time.monotonic
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.journal_path = journal_path
        self.expected_reward = expected_reward
        self.ticket_template_path = (TEMPLATE_PATHS[expected_reward] if ticket_template_path is None
                                     else ticket_template_path)
        self.bulk_ledger_path = bulk_ledger_path
        self.sample_root = sample_root   # None: never save evidence frames
        # click_mark()/clicks_since() of the live/replay controller (business_custom B6e): proves a press never left it
        self.authority = authority
        self.started = False
        self.reset()

    def reset(self):
        self.phase = "initial"
        self.reading = self.template = self.journal = self.attempt = self.receipt = None
        self.row_digest = self.receipt_digest = None
        self.seek = None
        self.top_used = self.bottom_used = False
        self.reported = False
        self.outcome = self.reason = None
        self.mode = "single"
        self.bulk = None            # mission_bulk.DailyList read before the bulk press
        self.tab, self.next_tab, self.tabs_done, self.received_any = None, None, set(), False   # デイリー / ウィークリー
        self.allowed_tabs = {"daily", "weekly"}
        self.bulk_decision = None
        self.bulk_ledger = None
        self.bulk_ledger_start = 0    # records before this run: they only warn, never block
        self.bulk_record = None
        self.bulk_seq = 0
        self.bulk_receipt = None
        self.bulk_click_mark = None   # controller mark taken when the 一括受取 guard passed (phase bulk_requested)
        self.bulk_resolved = []       # uncertain records of this cycle resolved by this run's fresh page
        self.other_templates = {}   # reviewed templates of the other ticket kinds (diagnostics only)
        self.saver = None
        self.samples = []

    def save_evidence(self, image, stage):
        """Evidence only: the frame a stop was decided on. Failures never change the task result."""
        if self.sample_root is None or image is None:
            return None
        try:
            if self.saver is None:
                self.saver = tc.FrameSaver("missions-" + stage, self.sample_root, limit=EVIDENCE_FRAMES)
            path = self.saver.save(image)
        except Exception:
            LOGGER.exception("Mission evidence frame not saved")
            return None
        if path is not None and str(self.saver.directory) not in self.samples:
            self.samples.append(str(self.saver.directory))
        return path

    def report(self, outcome, reason=None):
        self.outcome, self.reason = outcome, reason
        self.reported = True
        if self.mode == "bulk":
            self.sink.add("missions_claim", {
                "outcome": outcome, "reason": reason, "phase": self.phase, "mode": "bulk",
                "live_input": self.input_enabled,
                "daily": self.bulk.to_dict() if self.bulk else None,
                "decision": self.bulk_decision.to_dict() if self.bulk_decision else None,
                "record": self.bulk_record, "receipt": self.bulk_receipt,
                "resolved": [{"id": i, "state": s} for i, s in self.bulk_resolved], "samples": list(self.samples)})
            return
        self.sink.add("missions_claim", {
            "outcome": outcome, "reason": reason, "phase": self.phase,
            "mission_id": reader.LOGIN_ID, "live_input": self.input_enabled,
            "expected_reward": (self.expected_reward, 1),
            "reading": self.reading.to_dict() if self.reading else None,
            "history": [item.to_dict() for item in (self.journal.history or ())] if self.journal else None,
            "samples": list(self.samples),
        })

    def bulk_send_state(self):
        """Whether the 一括受取 press of the pending record left the controller: ``not_sent`` / ``sent`` / ``unknown``.

        ``bulk_ready``: the Click node's own guard (SC.Missions.Ready.bulk_click) never passed, so Maa issued no click
        (node disabled, guard failed, rows/button changed). ``bulk_requested``: the guard passed; the controller history
        since the mark decides (denied / refused before any gesture -> ``not_sent``); without a controller authority
        it stays ``unknown``. Any later phase means the press was sent."""
        if self.phase == "bulk_ready":
            return "not_sent"
        if self.phase != "bulk_requested":
            return "sent"
        since = getattr(self.authority, "clicks_since", None)
        if self.bulk_click_mark is None or not callable(since):
            return "unknown"
        try:
            outcome = since(self.bulk_click_mark)
        except Exception:
            return "unknown"
        return outcome if outcome in ("not_sent", "sent") else "unknown"

    def bound_frame(self, image):
        frame = self.frame_source() if callable(self.frame_source) else None
        if (not isinstance(frame, FrameCapture) or frame.shape != CANONICAL_SHAPE
                or frame.pixel_digest != _digest(image)
                or not 0 <= self.frame_clock() - frame.captured_at <= MAX_AGE_S):
            raise ValueError("mission_original_frame_missing_or_stale")
        return frame

    def read(self, context, image):
        frame = self.bound_frame(image)
        result = reader.read_login(image_rgb=image[:, :, ::-1],
            list_results=_ocr_items(context.run_recognition("SC.Missions.ListOCR", image)),
            timer_results=_ocr_items(context.run_recognition("SC.Missions.TimerOCR", image)),
            observed_at=self.clock(), ticket_template=self.template, expected_reward=self.expected_reward,
            page_guard=_hit(context, "SC.Missions.DailyPage", image),
            overlay=not _overlay_clear(context, image), frame=frame, other_templates=self.other_templates)
        return result, frame

    def defer_seek(self, reading, *, terminal=False):
        self.reading = reading
        if not any(reason in reading.reasons for reason in ("login_not_visible", "login_row_clipped")):
            self.seek = None
            return False
        if not terminal and not self.top_used:
            self.seek = "top"
        elif not self.bottom_used:
            self.seek = "bottom"
        else:
            self.seek = None
        return False

    def flush_on_task_end(self):
        if not self.started:
            return
        if self.mode == "bulk":
            if self.bulk_record is not None and self.bulk_record.get("state") in ("pending", "receipt_observed"):
                sent = self.bulk_send_state() if self.bulk_record["state"] == "pending" else "sent"
                target = "not_sent" if sent == "not_sent" else "uncertain"
                try:
                    self.bulk_record = self.bulk_ledger.transition(self.bulk_record["id"], target, self.clock(),
                                                                   self.reason or "task_ended")
                except Exception:
                    LOGGER.exception("Mission bulk ledger flush failed")
                if target == "not_sent":
                    self.report("bulk_not_sent", self.reason or "mission_task_ended_before_press")
                else:
                    self.report("uncertain", self.reason or "mission_task_ended_before_terminal")
            elif not self.reported:
                self.report("stopped", self.reason or "mission_task_ended_before_home")
            return
        if self.attempt is not None and self.phase not in ("claimed", "done"):
            try:
                self.journal.interrupt()
            except Exception:
                LOGGER.exception("Mission pending journal flush failed")
            self.attempt = None
            self.report("uncertain", self.reason or "mission_task_ended_before_terminal")
        elif not self.reported:
            self.report("stopped", self.reason or "mission_task_ended_before_home")


RUNTIME = MissionRuntime()


class MissionAction(CustomAction):
    operation = ""

    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.rt = runtime

    def run(self, context, argv):
        rt = self.rt
        try:
            return getattr(self, self.operation)(context, _params(argv.custom_action_param))
        except Exception as exc:
            LOGGER.exception("Mission %s failed", self.operation)
            rt.reason = f"{self.operation}:{type(exc).__name__}:{str(exc)[:100]}"
            return False

    def begin(self, context, params):
        rt = self.rt
        if rt.attempt is not None:
            rt.flush_on_task_end()
            return False
        rt.reset()
        rt.started = True
        config = (context.get_node_data("SC.Missions.Config") or {}).get("attach") or {}
        if config.get("mode") == "bulk":
            if (config.get("enabled") is not True or config.get("max_attempts") != 1
                    or not set(config) <= {"enabled", "mode", "max_attempts", "mission_id", "weekly"}):
                rt.report("disabled", "mission_claim_option_disabled")
                return False
            rt.mode, rt.phase = "bulk", "bulk_reading"
            if config.get("weekly") is not True:      # 仅デイリー
                rt.allowed_tabs = {"daily"}
            rt.bulk_ledger = mb.MissionBulkLedger(rt.bulk_ledger_path)
            rt.bulk_ledger_start = len(rt.bulk_ledger.records)
            return True
        if (config.get("enabled") is not True or config.get("mission_id") != reader.LOGIN_ID
                or config.get("max_attempts") != 1):
            rt.report("disabled", "mission_claim_option_disabled")
            return False
        if not rt.input_enabled or not callable(rt.frame_source):
            rt.report("disabled", "mission_input_controller_required")
            return False
        rt.template = load_ticket_template(rt.expected_reward, rt.ticket_template_path)
        for kind in TEMPLATE_PATHS:  # diagnostics: name the ticket actually shown when it is not the expected one
            if kind != rt.expected_reward:
                try:
                    rt.other_templates[kind] = load_ticket_template(kind)
                except Exception:
                    pass
        rt.phase = "reading"
        return True

    def read(self, context, params):
        rt = self.rt
        image = _latest_image(context)
        reading, frame = rt.read(context, image)
        rt.reading = reading
        observation = reading.observation(frame)
        if observation is None:
            rt.reason = ",".join(reading.reasons) or "mission_observation_unverified"
            rt.save_evidence(image, "read")
            return rt.defer_seek(reading)
        if rt.journal is None:
            # No auto-create, alternate filename, or recovery initialization here.
            rt.journal = DurableClaimTracker(rt.journal_path, reading.cycle_id, enabled=rt.input_enabled,
                                             max_attempts=1)
        if rt.journal.history is None:
            rt.reason = "mission_journal_unavailable"
            return False
        if reading.state == "claimed":
            decision = rt.journal.begin(observation, reader.LOGIN_ID, frame=frame, now=rt.frame_clock())
            if decision.state == "already_claimed":
                rt.phase = "already_claimed"
                rt.seek = None
                return True
            rt.reason = decision.reason
            return False
        if reading.state != "claimable":
            rt.reason = "mission_login_not_claimable"
            return False
        rt.phase, rt.seek = "candidate", None
        return True

    def intent(self, context, params):
        rt = self.rt
        image = _latest_image(context)
        reading, frame = rt.read(context, image)
        rt.reading = reading
        observation = reading.observation(frame)
        if (rt.phase != "candidate" or observation is None or rt.journal is None
                or reading.target is None or reading.target[0::2] != CLAIM_RECT[0::2]
                or reading.target[3] != CLAIM_RECT[3] or abs(reading.target[1] - CLAIM_RECT[1]) > 6):
            rt.reason = "mission_claim_position_unverified"
            rt.save_evidence(image, "intent")
            return False
        decision = rt.journal.begin(observation, reader.LOGIN_ID, frame=frame, now=rt.frame_clock())
        if decision.state != "prepared":
            rt.reason = decision.reason
            return False
        rt.attempt = decision.attempt
        ty = reading.target[1] + 4
        rt.row_digest = _digest(image[ty - 27:ty + 97, 255:1215])
        rt.phase = "prepared"
        return True

    def receipt(self, context, params):
        rt = self.rt
        if rt.phase != "claim_requested" or rt.attempt is None:
            rt.reason = "mission_receipt_without_attempt"
            return False
        image = _latest_image(context)
        frame = rt.bound_frame(image)
        items = _ocr_items(context.run_recognition("SC.Missions.ReceiptOCR", image))
        if not receipt_matches(image, items, rt.template, expected_reward=rt.expected_reward):
            rt.reason = "mission_single_" + rt.expected_reward + "_receipt_unverified"
            rt.save_evidence(image, "receipt")
            return False
        # The result belongs to the still-live attempt; reject a daily reset while it was open.
        local = rt.clock().astimezone(reader.wm.JST)
        day = (local - dt.timedelta(hours=5)).date().isoformat()
        if rt.attempt.cycle_id != "daily:" + day:
            rt.reason = "mission_cycle_changed_during_receipt"
            return False
        receipt = ClaimReceipt(rt.attempt.cycle_id, reader.LOGIN_ID, frame,
                               "daily_mission_reward", True, True, True)
        decision = rt.journal.accept_receipt(rt.attempt, receipt, frame=frame, now=rt.frame_clock())
        if decision.state != "receipt_observed":
            rt.reason = decision.reason
            return False
        rt.receipt = receipt
        rt.receipt_digest = _digest(image[286:453, 382:899])
        rt.phase = "receipt_observed"
        return True

    def confirm(self, context, params):
        rt = self.rt
        if rt.phase != "close_requested" or rt.attempt is None or rt.receipt is None:
            rt.reason = "mission_confirm_without_receipt"
            return False
        image = _latest_image(context)
        reading, frame = rt.read(context, image)
        rt.reading = reading
        observation = reading.observation(frame)
        if observation is None:
            rt.reason = ",".join(reading.reasons)
            rt.save_evidence(image, "confirm")
            return rt.defer_seek(reading, terminal=True)
        decision = rt.journal.finish(rt.attempt, observation, frame=frame, now=rt.frame_clock())
        if decision.state != "claimed":
            rt.reason = decision.reason
            return False
        rt.phase, rt.seek, rt.attempt = "claimed", None, None
        return True

    def done(self, context, params):
        rt = self.rt
        if rt.phase not in ("claimed", "already_claimed", "bulk_received", "bulk_nothing"):
            rt.reason = "mission_home_without_terminal"
            return False
        if rt.mode == "bulk" and rt.received_any:
            rt.phase = "bulk_received"
        result = {"bulk_received": "bulk_received", "bulk_nothing": "bulk_nothing_to_claim"}.get(rt.phase, rt.phase)
        rt.phase = "done"
        rt.report(result)
        return True

    # -- 2026-10-10: デイリー 一括受取 (mission_bulk) -----------------------------------------------------------
    def _bulk_page(self, context, image):
        from agent.core import present_actions as pa

        daily = mb.read_list(_ocr_items(context.run_recognition("SC.Missions.ListOCR", image)),
                             _ocr_items(context.run_recognition("SC.Missions.Bulk.PageOCR", image)),
                             titles=mb.TAB_TITLES[self.rt.tab or "daily"])
        return daily, pa.bulk_enabled(image)

    def _cycle(self, now):
        return mb.weekly_cycle_id(now) if self.rt.tab == "weekly" else pc.cycle_id(now)

    def _after_tab(self):
        """2026-10-15 user: デイリー and ウィークリー in one visit. Next tab to open, or None when both are done."""
        rt = self.rt
        rt.tabs_done.add(rt.tab)
        if rt.phase == "bulk_received":
            rt.received_any = True
        rest = [t for t in ("daily", "weekly") if t in rt.allowed_tabs and t not in rt.tabs_done]
        if rest:
            rt.next_tab, rt.phase = rest[0], "tab_switch"
        elif rt.received_any:
            rt.phase = "bulk_received"

    def _bulk_frame(self, image):
        rt = self.rt
        rt.bulk_seq += 1
        return pc.FrameRef(_digest(image), rt.bulk_seq, time.monotonic())

    def bulk_read(self, context, params):
        rt = self.rt
        if rt.mode != "bulk" or rt.phase != "bulk_reading":
            rt.reason = "mission_bulk_out_of_order"
            return False
        image = _latest_image(context)
        rt.tab = mb.selected_tab(image[:, :, ::-1])
        if rt.tab is not None and rt.tab not in rt.allowed_tabs:
            rest = [t for t in ("daily", "weekly") if t in rt.allowed_tabs and t not in rt.tabs_done]
            if rest:
                rt.next_tab, rt.phase = rest[0], "tab_switch"
                return True
        if rt.tab is None or rt.tab in rt.tabs_done:
            rt.reason = "mission_tab_unknown" if rt.tab is None else "mission_tab_repeated"
            rt.save_evidence(image, "bulk-read")
            rt.report("stopped", rt.reason)
            return False
        daily, enabled = self._bulk_page(context, image)
        cycle = self._cycle(rt.clock())
        page_guard = _hit(context, "SC.Missions.TabPage", image)
        overlay = not _overlay_clear(context, image)
        if page_guard and not overlay:
            # 2026-10-12: an uncertain record of this cycle is resolved only by this fresh, guarded page
            rt.bulk_resolved = rt.bulk_ledger.reobserve(cycle, daily, enabled, rt.clock())
        decision = mb.decide(daily, page_guard=page_guard, overlay=overlay, bulk_enabled=enabled,
                             ledger_block=rt.bulk_ledger.block_reason(cycle, rt.bulk_ledger_start),
                             frame=self._bulk_frame(image),
                             titles=mb.TAB_TITLES[rt.tab])
        rt.bulk, rt.bulk_decision = daily, decision
        if page_guard and not overlay and daily is not None:
            # 2026-10-14 user: one visit — this guarded read doubles as the 每日任务进度 report (MissionStatus is
            # skipped when 任务奖励 runs).
            rows = [dict(r) for r in daily.rows]
            rt.sink.add("mission_status", {
                "page": "mission_" + rt.tab, "counter": None, "rows": rows, "source": "missions_claim",
                "top_incomplete": mb.top_incomplete(rows),
                "complete": [r["title"] for r in rows if r["status"] in ("claimable", "completed", "claimed")],
                "claimed": [r["title"] for r in rows if r["status"] == "claimed"],
                "claimable": [r["title"] for r in rows if r["status"] == "claimable"],
                "incomplete": [r["title"] for r in rows if r["status"] == "incomplete"],
                "unknown": [r["title"] for r in rows if r["status"] == "unknown"],
                "visible_rows_only": True, "ok": any(r["status"] != "unknown" for r in rows)})
        if decision.kind == "empty":
            rt.phase = "bulk_nothing"
            self._after_tab()
            return True
        if decision.kind != "claim":
            rt.reason = ",".join(decision.reasons)
            rt.save_evidence(image, "bulk-read")
            rt.report("stopped", rt.reason)
            return False
        rt.phase = "bulk_candidate"
        return True

    def bulk_intent(self, context, params):
        rt = self.rt
        if rt.phase != "bulk_candidate" or rt.bulk_decision is None or rt.bulk_decision.kind != "claim":
            rt.reason = "mission_bulk_intent_without_decision"
            return False
        image = _latest_image(context)
        daily, enabled = self._bulk_page(context, image)
        if daily != rt.bulk or enabled is not True or not _overlay_clear(context, image):
            rt.reason = "mission_bulk_list_changed"
            rt.save_evidence(image, "bulk-intent")
            rt.report("stopped", rt.reason)
            return False
        now = rt.clock()
        rt.bulk_decision = pc.dataclasses.replace(rt.bulk_decision, frame=self._bulk_frame(image))
        rt.bulk_record = rt.bulk_ledger.begin_bulk(self._cycle(now), rt.bulk_decision, now, rt.bulk.claimable)
        if not rt.input_enabled:
            rt.bulk_record = rt.bulk_ledger.transition(rt.bulk_record["id"], "refused", now, "live_input_closed")
            rt.report("not_sent_live_input_closed")
            return False
        rt.phase = "bulk_ready"
        return True

    def bulk_result(self, context, params):
        rt = self.rt
        if rt.phase != "bulk_confirm_requested" or rt.bulk_record is None:
            rt.reason = "mission_bulk_result_without_attempt"
            return False
        image = None
        try:
            image = _latest_image(context)
            items = _ocr_items(context.run_recognition("SC.Missions.Bulk.ResultOCR", image))
            state, lines, why = mb.classify_result(items)
        except Exception as exc:
            state, lines, why = "uncertain", [], [f"read_error:{type(exc).__name__}"]
        rt.bulk_receipt = {"state": state, "items": lines, "reasons": why}
        target = "receipt_observed" if state == "receipt" else ("overflow" if state == "overflow" else "uncertain")
        rt.bulk_record = rt.bulk_ledger.transition(rt.bulk_record["id"], target, rt.clock(), ",".join(why) or None)
        if state != "receipt":
            rt.reason = "mission_bulk_result_" + state
            rt.save_evidence(image, "bulk-result")
            rt.report(state, rt.reason)
            return False
        rt.phase = "bulk_result_observed"
        return True

    def bulk_confirm(self, context, params):
        rt = self.rt
        if rt.phase != "bulk_close_requested" or rt.bulk_record is None:
            rt.reason = "mission_bulk_confirm_without_receipt"
            return False
        image = _latest_image(context)
        daily, enabled = self._bulk_page(context, image)
        final = mb.terminal(rt.bulk, daily, enabled)
        rt.bulk_record = rt.bulk_ledger.transition(rt.bulk_record["id"], final if final == "received" else "uncertain",
                                                   rt.clock())
        if final != "received":
            rt.reason = "mission_bulk_terminal_unverified"
            rt.save_evidence(image, "bulk-terminal")
            rt.report("uncertain", rt.reason)
            return False
        rt.phase = "bulk_received"
        self._after_tab()
        return True

    def stop(self, context, params):
        rt = self.rt
        rt.reason = rt.reason or params.get("reason", "mission_stopped")
        rt.flush_on_task_end()
        return False


class MissionBeginAction(MissionAction):
    operation = "begin"
class MissionReadAction(MissionAction):
    operation = "read"
class MissionIntentAction(MissionAction):
    operation = "intent"
class MissionReceiptAction(MissionAction):
    operation = "receipt"
class MissionConfirmAction(MissionAction):
    operation = "confirm"
class MissionDoneAction(MissionAction):
    operation = "done"
class MissionStopAction(MissionAction):
    operation = "stop"
class MissionBulkReadAction(MissionAction):
    operation = "bulk_read"
class MissionBulkIntentAction(MissionAction):
    operation = "bulk_intent"
class MissionBulkResultAction(MissionAction):
    operation = "bulk_result"
class MissionBulkConfirmAction(MissionAction):
    operation = "bulk_confirm"


class MissionReadyRecognition(CustomRecognition):
    def __init__(self, runtime=RUNTIME):
        super().__init__()
        self.rt = runtime

    def analyze(self, context, argv):
        rt = self.rt
        stage = _params(argv.custom_recognition_param).get("stage")
        # Mode routing (no input): the single-claim reader never runs in bulk mode and vice versa.
        if stage == "bulk_read" and rt.mode == "bulk" and rt.phase == "tab_requested":
            # the tab tap is proven only by the requested tab label turning pink on a fresh frame
            if mb.selected_tab(argv.image[:, :, ::-1]) != rt.next_tab:
                return None
            rt.phase, rt.bulk, rt.bulk_decision, rt.bulk_record, rt.bulk_receipt = "bulk_reading", None, None, None, None
        if stage in ("single", "bulk_read", "bulk_candidate"):
            want = {"single": ("single", "reading"), "bulk_read": ("bulk", "bulk_reading"),
                    "bulk_candidate": ("bulk", "bulk_candidate")}[stage]
            if (rt.mode, rt.phase) != want and not (stage == "single" and rt.mode == "single"):
                return None
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage})
        if not rt.input_enabled:
            return None
        if rt.mode == "bulk":
            try:
                return self._bulk_ready(context, argv.image, stage)
            except Exception:
                LOGGER.exception("Mission bulk readiness failed")
                return None
        try:
            image = argv.image
            rt.bound_frame(image)
            # Test positive overlay detections directly, then negate them in Python on this frame.
            if stage != "close" and not _overlay_clear(context, image):
                return None
            if stage == "claim" and rt.phase == "prepared" and rt.attempt is not None:
                ty = rt.attempt.target.rect[1] + 4
                if (rt.row_digest != _digest(image[ty - 27:ty + 97, 255:1215])
                        or pc.pink_count(image[:, :, ::-1], CLAIM_RECT) < 2000):
                    return None
                rt.phase = "claim_requested"
            elif stage == "close" and rt.phase == "receipt_observed":
                if rt.receipt_digest != _digest(image[286:453, 382:899]):
                    return None
                rt.phase = "close_requested"
            elif stage in ("top", "bottom") and rt.seek == stage:
                if stage == "top":
                    if rt.top_used:
                        return None
                    rt.top_used = True
                else:
                    if rt.bottom_used:
                        return None
                    rt.bottom_used = True
                rt.seek = None
            elif stage == "return" and rt.phase in ("claimed", "already_claimed"):
                pass
            elif stage == "candidate" and rt.phase == "candidate":
                pass
            elif stage == "already_claimed" and rt.phase == "already_claimed":
                pass
            elif stage == "terminal" and rt.phase == "close_requested":
                pass
            else:
                return None
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage})
        except Exception:
            LOGGER.exception("Mission readiness failed")
            return None


def _bulk_ready(self, context, image, stage):
    """Bulk clicks: each stage once, in order; the press re-binds the read rows and the pink 一括受取 on its frame."""
    from agent.core import present_actions as pa

    rt = self.rt
    if stage in ("bulk_click", "bulk_confirm") and not _overlay_clear(context, image):
        return None
    if stage == "bulk_click" and rt.phase == "bulk_ready" and rt.bulk_record is not None:
        # 2026-10-12 live: a raw pixel hash of the rows area never repeated (the シャイニーパス banner above the rows is
        # animated), so the press re-reads the rows on its own frame and requires the same claimable rows instead.
        if pa.bulk_enabled(image) is not True:
            return None
        try:
            daily = mb.read_list(_ocr_items(context.run_recognition("SC.Missions.ListOCR", image)),
                                 _ocr_items(context.run_recognition("SC.Missions.Bulk.PageOCR", image)),
                                 titles=mb.TAB_TITLES[rt.tab or "daily"])
        except Exception:
            return None
        if daily != rt.bulk:
            return None
        mark = getattr(rt.authority, "click_mark", None)
        try:
            rt.bulk_click_mark = mark() if callable(mark) else None
        except Exception:
            rt.bulk_click_mark = None
        rt.phase = "bulk_requested"
    elif stage == "bulk_confirm" and rt.phase == "bulk_requested":
        rt.phase = "bulk_confirm_requested"
    elif stage == "bulk_close" and rt.phase == "bulk_result_observed":
        rt.phase = "bulk_close_requested"
    elif stage == "bulk_terminal" and rt.phase == "bulk_close_requested":
        pass
    elif stage in ("tab_daily", "tab_weekly") and rt.phase == "tab_switch" and stage == "tab_" + rt.next_tab:
        rt.phase = "tab_requested"
    elif stage == "return" and rt.phase in ("bulk_received", "bulk_nothing"):
        pass
    else:
        return None
    return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"stage": stage})


MissionReadyRecognition._bulk_ready = _bulk_ready


def mission_registrations(runtime=RUNTIME):
    actions = {"SC.Missions." + name: action(runtime) for name, action in (
        ("Begin", MissionBeginAction), ("Read", MissionReadAction), ("Intent", MissionIntentAction),
        ("Receipt", MissionReceiptAction), ("Confirm", MissionConfirmAction),
        ("Done", MissionDoneAction), ("Stop", MissionStopAction),
        ("BulkRead", MissionBulkReadAction), ("BulkIntent", MissionBulkIntentAction),
        ("BulkResult", MissionBulkResultAction), ("BulkConfirm", MissionBulkConfirmAction))}
    return {"SC.Missions.Ready": MissionReadyRecognition(runtime)}, actions
