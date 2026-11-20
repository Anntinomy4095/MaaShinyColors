"""Maa custom recognitions/actions of the Business (営業) task (B6, sampling first). Rules: ``agent.core.business_task``.

Sample mode clicks at most twice: 「営業」 on a verified clean Home (non-consuming) and, after the frames are saved,
the lower-left back key when it is visible and undimmed on a non-Home screen. Nothing on the business screen itself
is ever tapped; a pop-up over it (dimmed back key) ends the task with ``sampled_not_returned`` and no click.

``sample_collect_one`` (B6b) adds exactly one tap on the business top: 「営業完了」 of the FIRST slot that shows the
CLEAR stamp, the 「営業完了」 label and the pink (enabled) button, on a stable, undimmed business top (title + トーク/コミュ +
back key). ``claim_intent`` is logged before the tap; then the screen is only observed (frames saved, <= 60 s): the
business top with that slot no longer CLEAR -> ``claimed`` (back key -> Home); otherwise ``claim_uncertain`` (never
retried today). Only the whitelisted reward / ミッション達成 pop-ups may be closed after the tap; an error dialog
(エラー / 1012) or any other screen is never tapped. sample_collect_one never presses 一括完了 / 一括営業 (B6e
bulk_default below); 差し入れ作成 is never an action.

B6d: the 「営業完了」 tap opens 「WORK REPORT」 (live 2026-10-03 19:10). A settled report (title + CLEAR! + 総獲得 labels +
「Touch to Next」) read the same on two frames proves the claim: ``work_report`` (Exp / fans by OCR) + ``claimed`` are
logged, then the 「Touch to Next」 text is tapped once (``business-report-next``) and the screen is observed read-only
(<= 60 s) until the business top is back (slot readings recorded). A run that STARTS on a WORK REPORT (takeover,
explicit modes only) does the same; the claim is resolved only when today's log has exactly one uncertain slot.
``sample_dispatch_open`` (explicit): on a stable business top, tap the card body of the first EMPTY slot once
(``business-slot-open-K``; empty = no CLEAR / 営業完了 anchor and no timer / digit / business text, on two frames), save
frames of the opened screen read-only (<= 30 s), press the back key once only on positive dispatch-page evidence
(B6h review #3: none exists yet, so the run stops with ``dispatch_not_returned`` after sampling), else stop.
B6h review: an unknown slot reading never resolves an uncertain claim (#1), an unscoped uncertain record blocks
dispatch sampling (#2), and a WORK REPORT counts as read / claimed only with valid Exp and fan readings (#4).

B6e ``bulk_default`` (default mode; ~19:30): on a stable business top (slot kinds and the two
bulk button colours the same on two frames) press 「一括完了」 (``business-bulk-complete``) when a slot shows CLEAR, read
and close every WORK REPORT with 「Touch to Next」 (<= 4), re-read the top, press 「一括営業」 (``business-bulk-dispatch``)
when a slot is positively empty, and re-read the top again. Each bulk action is pressed at most once per run, logged
first (``bulk_intent``) and proven by a fresh frame (``bulk_completed`` / ``bulk_dispatched``). Unknown screens or
dialogs after a press are only sampled (dialog OCR recorded, no click) -> ``needs_sampling``; error dialogs log
``business_error`` (no retry today). A tap the controller refused is ``click_refused_not_sent`` (``claim_refused`` /
``bulk_refused``), never an uncertain claim.
"""
from __future__ import annotations

import re
import time

from agent.core import business_task as bt
from agent.core import task_common as tc
from agent.core import task_custom

PREFIX = "SC.Business"
CONFIG_NODES = ("SC.Business.Config.Mode", "SC.Business.Config.Slots", "SC.Business.Config.Target")
HOME_GUARD = "SC.Common.Home.Guard"
BACK_READY = "SC.Business.BackKey.Ready"
# The bottom-bar 営業 button text above the label (「完了まで あと7:15」 on 2026-10-03), recorded as evidence only.
HOME_BUTTON_TEXT_BOX = [752, 628, 105, 42]
TOP_GUARD = "SC.Business.Top.Guard"
SLOT_NODES = tuple((f"SC.Business.Anchor.Slot{k}.Clear", f"SC.Business.Anchor.Slot{k}.Label",
                    f"SC.Business.Color.Slot{k}.Pink") for k in (1, 2, 3))
OBSERVE_POPUPS = ("SC.Popup.Reward.Guard", "SC.Popup.MissionCompleted.Guard")  # the only pop-ups closed after the tap
# Error dialogs (通信エラー / エラーコード: 1012) appear centred over the dimmed screen; their title and body lie here.
ERROR_BOX = [340, 110, 600, 380]
ERROR_WORDS = ("エラー", "1012")
REPORT_GUARD = "SC.Business.Report.Guard"
# B6f: the paged WORK REPORT's last screen (live 2026-10-03 20:51): same title / CLEAR! / 総獲得 labels, ‹ › page arrows,
# page dots, no 「Touch to Next」 and a pink 「次へ」 button (business-report-next-page) that closes the report.
PAGED_GUARD = "SC.Business.Report.Paged"
# 2026-10-10 live: after the paged report's 次へ the game shows a further WORK REPORT page 「ファンからのプレゼント」
# (title + banner + a scrollable grid of item tiles ×1 + the overflow message + the same pink 「次へ」). Items are icons
# only. Guard = title + 次へ templates (SC.Business.Report.Gift) AND the banner OCR; closed with the same 次へ action.
GIFT_GUARD = "SC.Business.Report.Gift"
GIFT_BANNER_BOX = [380, 120, 530, 75]
GIFT_TILES_BOX = [200, 245, 860, 250]
GIFT_MESSAGE_BOX = [300, 555, 700, 40]
GIFT_BANNER_WORDS = ("プレゼント", "ファンから")
# 2026-10-10 live: 次へ of the gift page leads to 獲得報酬 (Exスキル list with OK and 再営業へ); the user pressed OK -> 営業アイドル選択
# (three slots prefilled with the game's own 12 h team, 決定 at the lower right). 2026-10-11 user direction: keep pressing the
# bottom-right button, i.e. 再営業へ then 決定 (never OK); ← only as the fallback.
REWARD_GUARD = "SC.Business.Reward.Guard"
SELECT_GUARD = "SC.Business.Select.Guard"
REWARD_COLUMNS = ([290, 118, 430, 495], [763, 118, 430, 495])
REWARD_BADGE_XY = ((405, 147), (878, 147))  # centre of the P/S badge of entry row 0 per column (+106 px per row)
SELECT_NAME_BOX = lambda k: [30, 96 + 174 * k, 235, 36]       # 「雑誌インタビュー」 header of slot k (0..2)
SELECT_DURATION_BOX = lambda k: [370, 98 + 174 * k, 135, 30]  # 「12時間00分」
SELECT_LV_BOX = lambda k: [30, 226 + 174 * k, 570, 28]        # the five 「Lv.NN」 labels of the slot's cards
LV_TEXT = re.compile(r"[Ll][Vv][.．]?\s*\d+")
PAGE_VARIANTS = ("paged", "gift")  # variants closed with 「次へ」 (business-report-next-page)
MAX_REPORT_PRESSES = 5
# B6g: a bulk press the controller refused before any gesture (frame binding changed) is re-guarded on fresh frames and
# pressed again at most this many times in the same run; at most one press is ever actually sent.
MAX_BULK_REFUSALS = 2  # Touch to Next + 次へ presses per run (3 slots + 次へ + 1 spare)
# WORK REPORT readings (measured on business-native-20261003-frames/claim1/frame-020.png): business name, 総獲得Exp
# value (「500」 x ~1125..1185) and 総獲得ファン人数 value (「237,500人」 x ~1030..1185).
REPORT_BOXES = {"business": [210, 128, 270, 40], "exp": [960, 132, 230, 36], "fans": [960, 176, 230, 40]}
# Slot cards of the business top (slot 1 x ~130..487, y ~85..585; +381 px per slot): OCR for the empty-slot check.
SLOT_CARD_BOXES = tuple([130 + 381 * k, 85, 357, 500] for k in range(3))
SLOT_ANCHORS = tuple((f"SC.Business.Anchor.Slot{k}.Clear", f"SC.Business.Anchor.Slot{k}.Label") for k in (1, 2, 3))
# B6e bulk buttons (lower right of the business top): 一括営業 x ~892..1056, 一括完了 x ~1087..1251, y ~622..698.
# B6g: a disabled 一括完了 is a dark, muted pink (native all-empty top 2026-10-03 21:25), not grey.
BULK_COLORS = {"complete": ("SC.Business.Color.BulkComplete.Pink", "SC.Business.Color.BulkComplete.Dim"),
               "dispatch": ("SC.Business.Color.BulkDispatch.White", "SC.Business.Color.BulkDispatch.Grey")}
BULK_ACTION_IDS = {"complete": "business-bulk-complete", "dispatch": "business-bulk-dispatch"}
# A dialog shown after a bulk press (no sample yet): its text is recorded for the next design batch, never tapped.
DIALOG_BOX = [240, 110, 800, 460]
# B6h review #3: the Pipeline node that positively recognises the dispatch (営業先選択) page. None until a native sample
# exists and a reviewed template is added; until then DispatchBack never fires (the back key is not pressed on an
# unknown page that merely shows an undimmed back key).
DISPATCH_PAGE_GUARD = None
HOLD_PHASES = ("top_wait", "claim_ready", "claim_sent", "report_wait", "report_ready", "report_sent", "open_ready",
               "dispatch_sent", "dispatch_back_ready", "dispatch_returning", "bulk_ready", "bulk_sent",
               "bulk_report_wait", "bulk_report_ready", "bulk_report_sent",
               "reward_wait", "reward_ready", "reward_sent", "select_wait", "select_ready", "select_sent",
               "select_back_ready", "select_back_sent")
POST_OPEN_PHASES = ("sampling", "sampled") + HOLD_PHASES


class BusinessRuntime(task_custom.TaskRuntime):
    KIND = "business"
    RUN = bt.BusinessRun
    CONFIG = bt.BusinessConfig

    def __init__(self, *args, clock=time.monotonic, sample_frames=bt.SAMPLE_FRAMES,
                 sample_seconds=bt.SAMPLE_MIN_SECONDS, sample_interval=bt.SAMPLE_INTERVAL_SECONDS,
                 settle_seconds=bt.OPEN_SETTLE_SECONDS, top_wait_seconds=bt.TOP_WAIT_SECONDS,
                 observe_seconds=bt.OBSERVE_SECONDS, observe_min_seconds=bt.OBSERVE_MIN_SECONDS,
                 confirm_seconds=bt.CONFIRM_SECONDS, report_wait_seconds=bt.REPORT_WAIT_SECONDS,
                 report_no_effect_seconds=bt.REPORT_NO_EFFECT_SECONDS, dispatch_seconds=bt.DISPATCH_SECONDS,
                 dispatch_sample_seconds=bt.DISPATCH_SAMPLE_SECONDS,
                 dispatch_return_seconds=bt.DISPATCH_RETURN_SECONDS, bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.clock = clock
        self.sample_frames, self.sample_seconds = sample_frames, sample_seconds
        self.sample_interval, self.settle_seconds = sample_interval, settle_seconds
        self.top_wait_seconds, self.observe_seconds = top_wait_seconds, observe_seconds
        self.observe_min_seconds, self.confirm_seconds = observe_min_seconds, confirm_seconds
        self.report_wait_seconds, self.report_no_effect_seconds = report_wait_seconds, report_no_effect_seconds
        self.dispatch_seconds, self.dispatch_sample_seconds = dispatch_seconds, dispatch_sample_seconds
        self.dispatch_return_seconds = dispatch_return_seconds
        self.bulk_observe_seconds = bulk_observe_seconds

    def started(self, run):
        if run.config.mode == "bulk_default":
            # In the daily mode earlier uncertain results only warn (panel); this run
            # decides from the business top it observes. The sampling modes keep their per-day evidence rules.
            try:
                blocked, unscoped = bt.uncertain_slots(self.log.events())
            except Exception as exc:  # unreadable log: never press
                run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
                return
            if blocked or unscoped:
                if not self._log_or_fail(run, {"event": bt.RECHECK, "slots": sorted(blocked), "unscoped": unscoped}):
                    return
                run.notes.append("uncertain_result_earlier_today")
        if run.config.mode == "collect_and_dispatch":
            # Designed (business_task.plan_slots) but not wired: dispatch screens have no native sample yet.
            run.finish("needs_sampling", "collect_and_dispatch_needs_business_screen_samples",
                       "run business_mode=sample / sample_collect_one first")
        elif run.config.mode == "sample_collect_one":
            try:
                _, unscoped = bt.uncertain_slots(self.log.events())
            except Exception as exc:  # unreadable log: never claim
                run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
                return
            if unscoped:
                run.finish("uncertain_result_today", "uncertain_business_result_today_never_retried")
        elif run.config.mode == "sample_dispatch_open":
            try:
                _, unscoped = bt.uncertain_slots(self.log.events())
            except Exception as exc:  # unreadable log: never tap
                run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
                return
            if unscoped:  # B6h review #2: an unscoped uncertain record is never cleared by another slot's empty card
                run.finish("uncertain_result_today", "unscoped_uncertain_business_result_today",
                           "re-observe its owner before sampling dispatch")
        elif run.config.mode == "bulk_default":
            try:
                _, unscoped = bt.uncertain_slots(self.log.events(), include_bulk=False)
            except Exception as exc:  # unreadable log: never press
                run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
                return
            if unscoped:  # error 1012 / business error / an intent without slot today: no retry today
                run.finish("uncertain_result_today", "uncertain_business_result_today_never_retried")

    def _collect(self, run):
        return run.config.mode == "sample_collect_one"

    def _dispatch(self, run):
        return run.config.mode == "sample_dispatch_open"

    def _bulk(self, run):
        return run.config.mode == "bulk_default"

    def _reads_top(self, run):
        return run.config.mode in ("sample_collect_one", "sample_dispatch_open", "bulk_default")

    # -- controller outcome of the last tap (B6e: a refused tap is not an uncertain claim)
    def _mark(self, run):
        mark = getattr(self.authority, "click_mark", None)
        try:
            run.click_mark = mark() if callable(mark) else None
        except Exception:
            run.click_mark = None

    def _click_outcome(self, run):
        """``not_sent`` (the controller / host refused before any gesture, or no click call happened since the mark),
        ``sent`` or ``unknown`` (no authority, e.g. the AgentServer path: treat as possibly sent)."""
        since = getattr(self.authority, "clicks_since", None)
        if run.click_mark is None or not callable(since):
            return "unknown"
        try:
            return since(run.click_mark)
        except Exception:
            return "unknown"

    def bulk_states(self, vision, image):
        """(一括完了, 一括営業): ``enabled`` (pink / white), ``disabled`` (grey) or ``unknown``."""
        out = []
        for key in ("complete", "dispatch"):
            on, off = BULK_COLORS[key]
            out.append("enabled" if vision.hit(on, image) else "disabled" if vision.hit(off, image) else "unknown")
        return tuple(out)

    def dialog_texts(self, context, image):
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[".+"], roi=tuple(DIALOG_BOX), threshold=0.3)
        detail = context.run_recognition_direct(JRecognitionType.OCR, param, image)
        results = (getattr(detail, "all_results", None) or []) if detail is not None else []
        return [r.text for r in results if isinstance(r.text, str) and r.text.strip()][:12]

    def _maybe_save(self, run, image, now):
        if run.last_save is None or now - run.last_save >= self.sample_interval:
            run.last_save = now
            self._save(run, image)

    def slot_states(self, vision, image):
        """("completed" | "other",) * 3: completed = CLEAR stamp + 「営業完了」 label + pink (enabled) button of that
        slot. Only the CLEAR layout is sampled; Tea Time, in progress, empty or anything else is ``other``."""
        return tuple("completed" if all(vision.hit(node, image) for node in nodes) else "other"
                     for nodes in SLOT_NODES)

    def card_texts(self, context, image, box):
        """Every OCR line (any score >= 0.3) inside one slot card; used only to EXCLUDE a slot from ``empty``."""
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[".+"], roi=tuple(box), threshold=0.3)
        detail = context.run_recognition_direct(JRecognitionType.OCR, param, image)
        results = (getattr(detail, "all_results", None) or []) if detail is not None else []
        return [r.text for r in results if isinstance(r.text, str) and r.text.strip()]

    def slot_kinds(self, context, vision, image):
        """(kinds, texts): ``completed`` / ``empty`` / ``other`` per slot (``bt.classify_slot``) and the card OCR
        lines read for the non-completed slots (evidence for the run report)."""
        completed = self.slot_states(vision, image)
        kinds, texts = [], []
        for k in range(3):
            if completed[k] == "completed":
                kinds.append("completed")
                texts.append([])
                continue
            anchor = any(vision.hit(node, image) for node in SLOT_ANCHORS[k])
            lines = [] if anchor else self.card_texts(context, image, SLOT_CARD_BOXES[k])
            kinds.append(bt.classify_slot(False, anchor, lines))
            texts.append([str(t)[:40] for t in lines[:8]])
        return tuple(kinds), texts

    def report_values(self, vision, image):
        """WORK REPORT readings for the log: business name, Exp and fans (int or None)."""
        values = {}
        for key, box in REPORT_BOXES.items():
            try:
                text = vision.text(image, box)
            except Exception:
                text = None
            values[key] = text if key == "business" else bt.report_number(text)
        return values

    def error_text(self, context, image):
        """Text of an error dialog (エラー / 1012) inside ERROR_BOX, or None. Detection only; never a target."""
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[".+"], roi=tuple(ERROR_BOX), threshold=0.3)
        detail = context.run_recognition_direct(JRecognitionType.OCR, param, image)
        results = (getattr(detail, "all_results", None) or []) if detail is not None else []
        texts = [r.text for r in results if isinstance(r.text, str)]
        hits = [t for t in texts if any(word in t.replace(" ", "") for word in ERROR_WORDS)]
        return " / ".join(hits)[:120] if hits else None

    def _log_or_fail(self, run, record):
        if self._log(record):
            return True
        run.finish("log_failed", "business_log_write_failed")
        return False

    def _claim_uncertain(self, run, reason, outcome="claim_uncertain"):
        self._log({"event": "claim_uncertain", "slot": run.claim_slot, "reason": reason})
        run.finish(outcome, f"slot {run.claim_slot}", reason)

    def _save(self, run, image):
        if run.saver is None:
            run.saver = tc.FrameSaver("business-top", self.sample_root, self.now, bt.SAMPLE_FRAME_LIMIT)
        try:
            path = run.saver.save(image)
        except Exception as exc:  # evidence only
            run.note({"error": "sample_save", "detail": str(exc)[:120]})
            return None
        if path is not None and str(run.saver.directory) not in run.samples:
            run.samples.append(str(run.saver.directory))
        return path

    # -- recognitions
    def extra_state(self, want, context, image, p):
        run = self.run
        if want == "fresh":  # nothing of the business flow pressed yet in this run
            return run.phase == "active"
        if want == "returning":
            return run.phase == "returning"
        if want == "claim_sent":  # post-tap guard of Claim.SlotK: the tap was bound to a logged claim_intent
            return run.phase == "claim_sent"
        if want == "hold":  # sample_collect_one on the business screen: never fall through to pop-up/back navigation
            return run.phase in HOLD_PHASES
        if want == "report_sent":  # post-tap guard of Report.Next
            return run.phase == "report_sent"
        if want == "dispatch_sent":  # post-tap guard of Open.SlotK
            return run.phase == "dispatch_sent"
        if want == "dispatch_returning":  # post-tap guard of DispatchBack
            return run.phase == "dispatch_returning"
        if want == "bulk_sent":  # post-tap guard of Bulk.Complete / Bulk.Dispatch
            return run.phase == "bulk_sent"
        if want == "bulk_report_sent":  # post-tap guard of Bulk.ReportNext
            return run.phase == "bulk_report_sent"
        if want == "page_sent":  # post-tap guard of Report.PageNext (any mode)
            return run.phase in ("report_sent", "bulk_report_sent")
        if want == "reward_sent":  # post-tap guard of Reward.Redispatch
            return run.phase == "reward_sent"
        if want == "select_sent":  # post-tap guard of Select.Decide
            return run.phase == "select_sent"
        if want == "select_back_sent":  # post-tap guard of Select.Back
            return run.phase == "select_back_sent"
        if want == "return_blocked":
            # Sampled, but no visible undimmed back key on a non-Home screen (pop-up / unknown layout): stop, no click.
            if run.phase == "sampled":
                run.finish("sampled_not_returned", "back_key_not_ready_after_sampling",
                           *([f"pending {run.pending}"] if run.pending else []))
                return True
            return False
        return False

    def plan(self, context, image, p):
        run, step = self.run, p.get("step")
        if run is None or run.finished:
            return False
        if step == "open" and run.phase == "active" and run.config.mode in ("sample", "sample_collect_one",
                                                                         "sample_dispatch_open", "bulk_default"):
            try:
                run.home_text = self.vision(context).text(image, HOME_BUTTON_TEXT_BOX)
            except Exception:
                run.home_text = None
            run.note({"stage": "Home", "button_text": run.home_text})
            self._save(run, image)  # frame-001: the Home right before the tap
            run.phase = "opening"
            return True
        if step == "claim" and run.phase == "claim_ready" and self._collect(run):
            return self._claim(context, image, p)
        if step == "page_next" and run.report_variant in PAGE_VARIANTS and run.phase in ("report_ready",
                                                                                        "bulk_report_ready"):
            self._save(run, image)
            bulk = run.phase == "bulk_report_ready"
            run.phase = "bulk_report_sent" if bulk else "report_sent"
            run.report_at = run.bulk_at = self.clock()
            run.bulk_gap = run.after_gap = False
            run.report_presses += 1
            run.note({"stage": "PageNext", "bulk": bulk})
            self._mark(run)
            return True
        if step == "reward_redispatch" and run.phase == "reward_ready" and self._bulk(run):
            self._save(run, image)  # the exact 獲得報酬 frame the OK tap is bound to
            run.phase, run.post_at = "reward_sent", self.clock()
            run.note({"stage": "RewardRedispatch"})
            self._mark(run)
            return True
        if step == "select_decide" and run.phase == "select_ready" and self._bulk(run):
            return self._select_decide(image)
        if step == "select_back" and run.phase == "select_back_ready" and self._bulk(run):
            self._save(run, image)
            run.phase, run.post_at = "select_back_sent", self.clock()
            run.note({"stage": "SelectBack", "outcome": run.back_outcome})
            self._mark(run)
            return True
        if step == "report_next" and run.phase == "report_ready" and run.report_variant == "touch":
            run.after_gap = False
            run.report_presses += 1
            self._save(run, image)  # the exact report frame the tap is bound to
            run.phase, run.report_at = "report_sent", self.clock()
            run.note({"stage": "ReportNext", "slot": run.claim_slot})
            self._mark(run)
            return True
        if step == "open_slot" and run.phase == "open_ready" and self._dispatch(run):
            return self._open_slot(context, image, p)
        if step in ("bulk_complete", "bulk_dispatch") and run.phase == "bulk_ready" and self._bulk(run):
            return self._bulk_press(context, image, step[len("bulk_"):])
        if (step == "bulk_report_next" and run.phase == "bulk_report_ready" and self._bulk(run)
                and run.report_variant == "touch"):
            run.report_presses += 1
            self._save(run, image)
            run.phase, run.bulk_at, run.bulk_gap = "bulk_report_sent", self.clock(), False
            run.note({"stage": "BulkReportNext", "report": run.bulk_reports})
            self._mark(run)
            return True
        return False

    def _bulk_press(self, context, image, action):
        """Plan.BulkComplete / Plan.BulkDispatch on the exact frame of the tap (template checks only, no OCR, so the
        tap follows the frame quickly): the decision of the stable reading must still hold; ``bulk_intent`` is logged
        before the tap (no log line -> no tap)."""
        run = self.run
        if run.bulk_next != action or action in run.bulk_done:
            return False
        if action == "dispatch" and bt.bulk_dispatch_gate(run.config) is not None:  # batch 4: never unbound
            return False
        vision = self.vision(context)
        states = self.slot_states(vision, image)
        if not vision.hit(TOP_GUARD, image) or ("completed" in states) != (action == "complete"):
            run.phase, run.top_reading = "top_wait", None
            return False
        kinds = list(run.top_reading[0][0]) if run.top_reading else None
        self._save(run, image)
        if not self._log_or_fail(run, {"event": "bulk_intent", "action": action, "kinds": kinds,
                                       "states": list(states), "frame": tc.image_digest(image),
                                       "tap": BULK_ACTION_IDS[action]}):
            return False
        run.bulk_done.add(action)
        run.phase, run.bulk_action, run.bulk_at, run.bulk_dialog = "bulk_sent", action, self.clock(), None
        run.note({"stage": "BulkPress", "action": action, "kinds": kinds})
        self._mark(run)
        return True

    def _open_slot(self, context, image, p):
        """Plan.OpenK: re-read the business top on this exact frame; the planned slot must still be the first empty,
        not-uncertain slot. Non-consuming (opens the screen behind the empty slot); nothing is logged as a dispatch."""
        run, slot = self.run, p.get("slot")
        if slot != run.open_slot:
            return False
        vision = self.vision(context)
        if not vision.hit(TOP_GUARD, image):
            run.phase, run.top_reading = "top_wait", None
            return False
        kinds, texts = self.slot_kinds(context, vision, image)
        first, reason = bt.first_empty(kinds, *bt.uncertain_slots(self.log.events()))
        if first != slot:
            run.finish(reason if reason not in (None, "no_empty_slot") else "slot_changed",
                       f"planned slot {slot}", f"now {first} {reason or ''}".strip(), "kinds " + ",".join(kinds))
            return False
        self._save(run, image)
        run.phase, run.dispatch_at, run.dispatch_seen, run.dispatch_page_seen = "dispatch_sent", self.clock(), 0, 0
        run.note({"stage": "OpenSlot", "slot": slot, "kinds": list(kinds), "frame": tc.image_digest(image)})
        self._mark(run)
        return True

    def _claim(self, context, image, p):
        """Plan.Claim of one Claim.SlotK node: re-verify the business top and the slot reading on this exact frame,
        then log ``claim_intent`` (no log line -> no tap) and let the node tap that slot's 「営業完了」 once."""
        run, slot = self.run, p.get("slot")
        if slot != run.claim_slot:
            return False
        vision = self.vision(context)
        if not vision.hit(TOP_GUARD, image):
            run.phase, run.top_reading = "top_wait", None  # transient; TopWait re-reads from the next frame
            return False
        states = self.slot_states(vision, image)
        first, reason = bt.first_claim(states, *bt.uncertain_slots(self.log.events()))
        if first != slot:
            run.finish(reason if reason not in (None, "no_completed_slot") else "slot_changed",
                       f"planned slot {slot}", f"now {first} {reason or ''}".strip(), "states " + ",".join(states))
            return False
        self._save(run, image)  # the exact frame the tap is bound to
        if not self._log_or_fail(run, {"event": "claim_intent", "slot": slot, "states": list(states),
                                       "frame": tc.image_digest(image), "action": f"business-slot-complete-{slot}"}):
            return False
        run.phase, run.claim_at, run.cleared_at = "claim_sent", self.clock(), None
        run.note({"stage": "ClaimIntent", "slot": slot, "states": list(states)})
        self._mark(run)
        return True

    def check(self, context, image, p):
        run, stage = self.run, p.get("stage")
        if run is None or run.finished:
            return False
        vision = self.vision(context)
        if stage == "left_home":
            if run.phase != "opening" or vision.hit(HOME_GUARD, image):
                return False
            run.phase, run.left_home_at = ("top_wait" if self._reads_top(run) else "sampling"), self.clock()
            run.note({"stage": "Opened"})
            return True
        if stage == "error":
            if run.phase not in POST_OPEN_PHASES:
                return False
            text = self.error_text(context, image)
            if text is None:
                return False
            self._save(run, image)
            run.note({"stage": "ErrorDialog", "text": text})
            if self._bulk(run):  # B6e: an error dialog blocks every business press for the rest of the game day
                after = run.bulk_action if run.phase in ("bulk_sent", "bulk_report_sent") else None
                if run.phase == "select_sent":
                    after = "dispatch"
                self._log({"event": "business_error", "text": text, "phase": run.phase, "action": after})
                run.finish("business_error_uncertain", "error_dialog", text, *([f"after {after}"] if after else []))
                return True
            if run.phase == "claim_sent" or (run.phase in ("report_wait", "report_ready") and not run.takeover
                                             and not run.claim_logged):
                self._claim_uncertain(run, "error_dialog:" + text, "business_error_uncertain")
            else:
                run.finish("business_error_uncertain", "error_dialog", text)
            return True  # Halt on the next round; nothing is tapped
        if stage == "top":
            return self._top_wait(run, vision, image, context)
        if stage == "observe":
            return self._observe(run, vision, image)
        if stage == "observe_popup":
            return (run.phase in ("claim_sent", "report_sent", "bulk_sent", "bulk_report_sent")
                    and any(vision.hit(node, image) for node in OBSERVE_POPUPS))
        if stage == "bulk_observe":
            return self._bulk_observe(context, run, vision, image)
        if stage == "bulk_refused":
            return self._bulk_refused(run)
        if stage == "top_takeover":
            # B6g: the run starts on the business top (e.g. a previous run stopped there): read it like after 営業.
            if run.phase != "active" or not self._reads_top(run) or not vision.hit(TOP_GUARD, image):
                return False
            self._save(run, image)
            run.phase, run.left_home_at, run.top_reading = "top_wait", self.clock() - self.settle_seconds, None
            run.note({"stage": "TopTakeover"})
            return False  # TopWait reads this frame
        if stage == "bulk_report":
            return self._bulk_report_wait(run, vision, image)
        if stage == "reward":
            return self._reward_wait(run, vision, image)
        if stage == "select":
            return self._select_wait(context, run, vision, image)
        if stage == "post_observe":
            return self._post_observe(context, run, vision, image)
        if stage == "report_takeover":
            return self._report_takeover(run, vision, image)
        if stage == "report":
            return self._report_wait(run, vision, image)
        if stage == "after_report":
            return self._after_report(context, run, vision, image)
        if stage == "dispatch":
            return self._dispatch_observe(run, vision, image)
        if stage == "dispatch_back":
            if (run.phase != "dispatch_back_ready" or vision.hit(TOP_GUARD, image) or vision.hit(HOME_GUARD, image)
                    or not self._dispatch_page(vision, image)):  # B6h review #3: positive dispatch-page evidence
                return False
            run.phase, run.back_at = "dispatch_returning", self.clock()
            return True
        if stage == "dispatch_page":  # Pipeline guard of DispatchBack (B6h review #3)
            return run.phase == "dispatch_back_ready" and self._dispatch_page(vision, image)
        if stage == "dispatch_returned":
            return self._dispatch_returned(context, run, vision, image)
        if stage == "sample":
            if run.phase != "sampling":
                return False
            now = self.clock()
            if now - run.left_home_at < self.settle_seconds:
                return True  # let the transition finish before the first saved frame
            if run.last_save is None or now - run.last_save >= self.sample_interval:
                run.last_save = now
                self._save(run, image)
            saved = len(run.saver.saved) - 1 if run.saver is not None else 0  # minus the Home frame
            if saved >= self.sample_frames and now - run.left_home_at >= self.sample_seconds:
                run.phase = "sampled"
                run.note({"stage": "Sampled", "frames": saved, "seconds": round(now - run.left_home_at, 1)})
                return False  # let the return node decide on this frame
            return True
        if stage == "return":
            if run.phase != "sampled" or vision.hit(HOME_GUARD, image) or not vision.hit(BACK_READY, image):
                return False
            run.phase = "returning"
            return True
        if stage == "returned":
            if run.phase != "returning" or not vision.hit(HOME_GUARD, image):
                return False
            frames = len(run.saver.saved) if run.saver is not None else 0
            run.finish(run.pending or "sampled", f"frames {frames}", *(f"samples {d}" for d in run.samples))
            return True
        return False

    def _top_wait(self, run, vision, image, context=None):
        """sample_collect_one before the tap: read-only until the business top reads the same slot states on two
        frames (>= settle_seconds apart), then decide: claim the first CLEAR slot / nothing to claim / stop."""
        if run.phase == "bulk_ready" and not run.ready_seen:
            # B6g: the decision was taken on the previous (OCR-heavy) frame; let Bulk.X re-verify THIS fresh frame with
            # templates / colours only and tap at once, so the screencap-to-tap interval stays short.
            run.ready_seen = True
            return False
        if run.phase in ("claim_ready", "open_ready", "bulk_ready"):
            # The Claim/Open node did not fire on the previous frame (guard lost): read again, never tap blindly.
            run.phase, run.top_reading = "top_wait", None
        if run.phase != "top_wait":
            return False
        now = self.clock()
        self._maybe_save(run, image, now)
        if now - run.left_home_at >= self.top_wait_seconds:
            run.finish("needs_sampling", "business_top_not_recognised")
            return True
        if now - run.left_home_at < self.settle_seconds or not vision.hit(TOP_GUARD, image):
            run.top_reading = None
            return True
        if self._dispatch(run):
            return self._top_dispatch(context, run, vision, image, now)
        if self._bulk(run):
            return self._top_bulk(context, run, vision, image, now)
        try:
            blocked, _ = bt.uncertain_slots(self.log.events())
        except Exception as exc:
            run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
            return True
        if blocked:
            # B6h review #1: an uncertain slot is resolved only by a positive reading (empty card text), so the
            # stable reading includes the card OCR whenever today has a slot-scoped uncertain claim.
            kinds, _ = self.slot_kinds(context, vision, image)
            states = tuple("completed" if k == "completed" else "other" for k in kinds)
        else:
            kinds = states = self.slot_states(vision, image)
        previous = run.top_reading
        if previous is None or previous[0] != kinds:
            run.top_reading = (kinds, now)
            return True
        if now - previous[1] < self.settle_seconds:
            return True
        try:
            if not self._resolve_reobserved(run, kinds):
                return True
            first, reason = bt.first_claim(states, *bt.uncertain_slots(self.log.events()))
        except Exception as exc:
            run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
            return True
        run.note({"stage": "Top", "states": list(states), "first": first, "reason": reason})
        if first is not None:
            run.phase, run.claim_slot = "claim_ready", first
            return False  # the matching Claim.SlotK node re-verifies this frame and taps
        if reason == "no_completed_slot":
            run.phase, run.pending = "sampled", "no_completed_slot"
            return False  # back key on this frame
        run.finish(reason, "states " + ",".join(states))
        return True

    def _resolve_reobserved(self, run, kinds, include_bulk=True):
        """Re-observation of today's uncertain claims on a stable business top (an uncertain result is not
        retried until the live state is re-observed). ``kinds``: ``completed`` / ``empty`` / ``other`` per slot.
        B6h review #1 (``bt.reobserve_blocked``): a slot whose claim is uncertain and that now reads positively EMPTY
        was claimed (by the earlier tap or by the user) -> ``claimed`` with ``resolved``/``top_reobserved``; a slot
        that still reads CLEAR stays blocked; any other reading (``other``: unknown) keeps the record uncertain and
        stops the run (``uncertain_slot_today``). Unscoped uncertainty is never resolved here.
        False: the run was finished (unknown slot state or log write failed)."""
        blocked, unscoped = bt.uncertain_slots(self.log.events(), include_bulk)
        if unscoped:
            return True
        resolved, unknown = bt.reobserve_blocked(blocked, kinds)
        if unknown:
            run.note({"stage": "Unresolved", "slots": list(unknown), "kinds": list(kinds)})
            run.finish("uncertain_slot_today", "slots " + ",".join(map(str, unknown)),
                       "state_unknown_after_uncertain_result", "kinds " + ",".join(kinds))
            return False
        for slot in resolved:
            if not self._log_or_fail(run, {"event": "claimed", "slot": slot, "resolved": True,
                                           "via": "top_reobserved", "kinds": list(kinds)}):
                return False
            run.note({"stage": "Resolved", "slot": slot, "via": "top_reobserved"})
        return True

    def _top_dispatch(self, context, run, vision, image, now):
        """sample_dispatch_open: the slot kinds must read the same on two frames (>= settle apart) before the first
        empty slot is chosen; no empty slot -> back key -> Home (``no_empty_slot``)."""
        kinds, texts = self.slot_kinds(context, vision, image)
        previous = run.top_reading
        if previous is None or previous[0] != kinds:
            run.top_reading = (kinds, now)
            return True
        if now - previous[1] < self.settle_seconds:
            return True
        try:
            if not self._resolve_reobserved(run, kinds):
                return True
            first, reason = bt.first_empty(kinds, *bt.uncertain_slots(self.log.events()))
        except Exception as exc:
            run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
            return True
        run.note({"stage": "Top", "kinds": list(kinds), "texts": texts, "first_empty": first, "reason": reason})
        if first is not None:
            run.phase, run.open_slot = "open_ready", first
            return False  # the matching Open.SlotK node re-verifies this frame and taps
        if reason == "no_empty_slot":
            run.phase, run.pending = "sampled", "no_empty_slot"
            return False
        run.finish(reason, "kinds " + ",".join(kinds))
        return True

    def _top_bulk(self, context, run, vision, image, now):
        """bulk_default on the business top: slot kinds + both bulk button colours must read the same on two frames
        (>= settle apart). Then: prove this run's last press, resolve earlier uncertain presses by re-observation,
        and decide the next press (``bt.bulk_decide``) or the final outcome."""
        kinds, texts = self.slot_kinds(context, vision, image)
        buttons = self.bulk_states(vision, image)
        reading = (kinds, buttons)
        previous = run.top_reading
        if previous is None or previous[0] != reading:
            run.top_reading = (reading, now)
            return True
        if now - previous[1] < self.settle_seconds:
            return True
        self._save(run, image)
        note = {"stage": "Top", "kinds": list(kinds), "buttons": list(buttons), "texts": texts}
        try:
            if run.bulk_action is not None:  # this run's press, not yet proven: the stable top decides it now
                action, run.bulk_action = run.bulk_action, None
                done = ("completed" not in kinds) if action == "complete" else ("empty" not in kinds)
                event = {"complete": "bulk_completed", "dispatch": "bulk_dispatched"}[action]
                if not done:
                    reason = "still_clear_after_press" if action == "complete" else "still_empty_after_press"
                    self._log({"event": "bulk_uncertain", "action": action, "reason": reason, "kinds": list(kinds)})
                    run.note(note)
                    run.finish("bulk_result_uncertain", f"{action}: {reason}", "kinds " + ",".join(kinds))
                    return True
                record = {"event": event, "action": action, "via": "top", "kinds": list(kinds),
                          "buttons": list(buttons)}
                if action == "dispatch":
                    # Batch 4: the game dispatched without a confirmation this run could read and bind: the team and
                    # the 12 h duration were never checked before the dispatch. Logged, reported, never retried.
                    before = run.dispatch_kinds or ()
                    record.update(binding="not_verified_no_confirmation",
                                  business_missing_slots=bt.dispatch_readback(before, texts, run.config))
                    run.dispatch_unverified = True
                if not self._log_or_fail(run, record):
                    return True
            if not self._resolve_reobserved(run, kinds, include_bulk=False):
                return True
            events = self.log.events()
            blocked, unscoped = bt.uncertain_slots(events, include_bulk=False)
            if unscoped:
                run.note(note)
                run.finish("uncertain_result_today", "uncertain_business_result_today_never_retried")
                return True
            resolved, stop = bt.bulk_resolution(bt.bulk_pending(events), kinds)
            for action in resolved:
                if not self._log_or_fail(run, {"event": "bulk_resolved", "action": action, "via": "top_reobserved",
                                               "kinds": list(kinds)}):
                    return True
            if stop:
                run.note(note)
                run.finish(stop, "bulk press uncertain today", "kinds " + ",".join(kinds))
                return True
            press, outcome = bt.bulk_decide(kinds, *buttons, frozenset(run.bulk_done), blocked)
        except Exception as exc:
            run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
            return True
        if press == "dispatch":
            gate = bt.bulk_dispatch_gate(run.config)
            if gate is not None:  # batch 4: 一括営業 off (default) or no team to bind -> leave the empty slots
                press, outcome = None, gate
            else:
                run.dispatch_kinds = tuple(kinds)
        if outcome == "collected_and_dispatched" and run.dispatch_unverified:
            outcome = "dispatched_binding_unverified"
        note.update(press=press, outcome=outcome)
        if outcome == "tea_time_wait":  # 2026-10-12: finished slots in Tea Time; leave via ← and rerun once CLEAR
            left = bt.tea_time_left_minutes(texts)
            note["tea_time_left_minutes"] = left
            run.notes.extend(["kinds " + ",".join(kinds), "buttons " + ",".join(buttons),
                              f"tea_time_left_minutes {left if left is not None else 'unread'}",
                              "rerun after Tea Time ends (slots show CLEAR); 一括完了 is not pressed in Tea Time"])
        run.note(note)
        if outcome in bt.RETURN_FAILURE_OUTCOMES:
            run.phase, run.pending = "sampled", outcome
            return False  # back key -> Home on this frame; the run ends failed with this outcome
        if press is not None:
            run.phase, run.bulk_next, run.ready_seen = "bulk_ready", press, False
            return True  # B6g: Bulk.Complete / Bulk.Dispatch re-verifies the NEXT fresh frame and taps
        if outcome in bt.SUCCESS_OUTCOMES:
            run.phase, run.pending = "sampled", outcome
            return False  # back key -> Home on this frame
        run.finish(outcome, "kinds " + ",".join(kinds), "buttons " + ",".join(buttons))
        return True

    def _bulk_refused(self, run):
        """on_error of Bulk.Complete / Bulk.Dispatch: the controller refused the tap before any gesture (``not_sent``,
        e.g. the host's latest frame changed). Log it and re-read the business top on fresh frames; the same press
        may be retried at most MAX_BULK_REFUSALS times. Anything else (sent / unknown) falls through to Abort."""
        if run.phase != "bulk_sent" or run.bulk_action is None or run.bulk_refusals >= MAX_BULK_REFUSALS:
            return False
        if self._click_outcome(run) != "not_sent":
            return False
        action = run.bulk_action
        run.bulk_refusals += 1
        if not self._log_or_fail(run, {"event": "bulk_refused", "action": action, "reason": "click_refused_not_sent",
                                       "attempt": run.bulk_refusals, "retry": True}):
            return True
        run.bulk_done.discard(action)
        run.bulk_action = None
        run.phase, run.left_home_at, run.top_reading = "top_wait", self.clock() - self.settle_seconds, None
        run.note({"stage": "BulkRefused", "action": action, "attempt": run.bulk_refusals})
        return True

    def _bulk_observe(self, context, run, vision, image):
        """Read-only after a bulk press or 「Touch to Next」 (<= bulk_observe_seconds): a WORK REPORT after 一括完了 ->
        read it; the undimmed business top -> re-read it (TopWait); anything else is only sampled (dialog text
        recorded), then ``needs_sampling``. Error dialogs are handled before this by SC.Business.Error."""
        if run.phase not in ("bulk_sent", "bulk_report_sent"):
            return False
        now = self.clock()
        elapsed = now - run.bulk_at
        self._maybe_save(run, image, now)
        if run.phase == "bulk_report_sent" and vision.hit(REWARD_GUARD, image):
            # 2026-10-10: the last 次へ (gift page) leads to 獲得報酬: read it, press 再営業へ
            self._save(run, image)
            run.phase, run.reward_reading, run.report_since = "reward_wait", None, now
            run.note({"stage": "RewardSeen", "seconds": round(elapsed, 1)})
            return False  # RewardWait reads this frame
        variant = self._report_variant(vision, image)
        report = variant is not None
        if run.phase == "bulk_report_sent":
            if report:
                # A new screen: a non-report frame was seen since the tap, or the variant changed (the paged
                # report's 次へ screen follows the last 「Touch to Next」 directly).
                if not run.bulk_gap and variant == run.report_variant:  # the same report is still shown
                    if elapsed >= self.report_no_effect_seconds:
                        run.finish("report_next_no_effect", f"report {run.bulk_reports}")
                    return True
                if run.report_presses >= MAX_REPORT_PRESSES or (
                        variant == "touch" and run.bulk_reports >= bt.BULK_MAX_REPORTS):
                    run.finish("bulk_report_limit", f"reports {run.bulk_reports}", f"presses {run.report_presses}")
                    return True
                run.report_variant = variant
                run.phase, run.report_since, run.report_reading = "bulk_report_wait", now, None
                return False  # BulkReportWait reads this frame
            run.bulk_gap = True
        elif report and run.bulk_action == "complete":
            run.report_variant = variant
            self._save(run, image)
            # B6h review #4: the report frame alone proves nothing; ``bulk_completed`` is logged by BulkReportWait only
            # after two equal VALID readings (bt.report_valid). Until then the press stays pending (bulk_action).
            run.phase, run.report_since, run.report_reading = "bulk_report_wait", now, None
            run.note({"stage": "BulkReportSeen", "seconds": round(elapsed, 1)})
            return False
        if any(vision.hit(node, image) for node in OBSERVE_POPUPS):
            run.note({"stage": "ObservePopup"})
            return False  # Observe.Popup closes a whitelisted pop-up
        if vision.hit(TOP_GUARD, image) and not report:
            if elapsed >= self.observe_min_seconds:
                run.phase, run.left_home_at, run.top_reading = "top_wait", now, None
                return True
            return True
        if (not report and (run.bulk_dialog_at is None or now - run.bulk_dialog_at >= 2.0)
                and not vision.hit(HOME_GUARD, image)):
            run.bulk_dialog_at = now
            try:
                texts = self.dialog_texts(context, image)
            except Exception:
                texts = []
            if texts and texts != run.bulk_dialog:
                run.bulk_dialog = texts
                self._save(run, image)
                binding = (bt.dispatch_binding(texts, run.config)
                           if run.phase == "bulk_sent" and run.bulk_action == "dispatch" else None)
                if binding is not None:
                    run.dispatch_binding = binding
                run.note({"stage": "BulkDialog", "after": run.bulk_action or "report_next", "texts": texts,
                          "forbidden": bt.dialog_forbidden(texts), **({"binding": binding} if binding else {})})
        if elapsed >= self.bulk_observe_seconds:
            if run.phase == "bulk_sent" and run.bulk_action == "dispatch" and run.dispatch_binding is not None:
                # Batch 4: a dispatch preview was read and bound; there is no confirm action, so it is never
                # confirmed. Its effect is not proven either (no native sample): uncertain, never retried today.
                binding = run.dispatch_binding
                self._log({"event": "bulk_uncertain", "action": "dispatch", "reason": "dispatch_preview_not_confirmed",
                           "binding": binding, "dialog": run.bulk_dialog})
                outcome = "dispatch_confirm_not_enabled" if binding["matched"] else "dispatch_binding_mismatch"
                run.finish(outcome, "missing " + ",".join(binding["missing"]) if binding["missing"] else "",
                           "forbidden " + ",".join(binding["forbidden"]) if binding["forbidden"] else "",
                           "dialog " + " / ".join(run.bulk_dialog)[:160])
            elif run.phase == "bulk_sent" and run.bulk_action is not None:
                reason = "dialog_after_press" if run.bulk_dialog else "unknown_screen_after_press"
                self._log({"event": "bulk_uncertain", "action": run.bulk_action, "reason": reason,
                           **({"dialog": run.bulk_dialog} if run.bulk_dialog else {})})
                run.finish("needs_sampling", f"{run.bulk_action}: {reason}",
                           *(["dialog " + " / ".join(run.bulk_dialog)[:160]] if run.bulk_dialog else []))
            else:
                run.finish("report_next_unverified", f"report {run.bulk_reports}")
        return True

    def _bulk_report_wait(self, run, vision, image):
        """A WORK REPORT after 一括完了: two equal readings (guard on both) -> ``work_report`` (bulk, no slot) ->
        Bulk.ReportNext taps 「Touch to Next」 once."""
        if run.phase == "bulk_report_ready":
            run.phase, run.report_reading = "bulk_report_wait", None  # Bulk.ReportNext did not fire: read again
        if run.phase != "bulk_report_wait":
            return False
        now = self.clock()
        self._maybe_save(run, image, now)
        if now - run.report_since >= self.report_wait_seconds:
            self._bulk_report_unread(run, "report_readings_invalid")
            return True
        if self._report_variant(vision, image) != run.report_variant:
            run.report_reading = None
            return True
        gift = run.report_variant == "gift"
        values = {"gift": True} if gift else self.report_values(vision, image)  # gift: the banner guard already held
        if not gift and not bt.report_valid(values):  # B6h review #4: an unknown reading is never a stable report
            run.report_reading = None
            return True
        previous = run.report_reading
        if previous is None or previous[0] != values:
            run.report_reading = (values, now)
            return True
        if now - previous[1] < self.confirm_seconds:
            return True
        self._save(run, image)
        if "complete" not in run.bulk_done:  # ownership: only a report after this run's 一括完了 is read here
            self._bulk_report_unread(run, "report_without_bulk_complete")
            return True
        if run.bulk_action == "complete":  # the first valid report proves this run's 一括完了
            if not self._log_or_fail(run, {"event": "bulk_completed", "action": "complete", "via": "work_report",
                                           "seconds": round(now - run.bulk_at, 1)}):
                return True
            run.bulk_action = None
        if gift:  # 2026-10-10: 「ファンからのプレゼント」 page: log what is visible, then 次へ (not a counted report)
            details = self.gift_details(vision, image)
            if not self._log_or_fail(run, {"event": "work_report_gift", "bulk": True, **details,
                                           "frame": tc.image_digest(image)}):
                return True
            run.phase = "bulk_report_ready"
            run.note({"stage": "ReportGift", "bulk": True, **details})
            return False  # Report.PageNext taps 「次へ」 on this frame
        if run.report_variant == "paged":  # B6f: the closing 次へ screen repeats the totals; not a new report
            run.phase = "bulk_report_ready"
            run.note({"stage": "ReportPaged", "bulk": True, **values})
            return False  # Report.PageNext taps 「次へ」 on this frame
        run.bulk_reports += 1
        if not self._log_or_fail(run, {"event": "work_report", "slot": None, "bulk": True, "index": run.bulk_reports,
                                       **values, "frame": tc.image_digest(image)}):
            return True
        run.phase = "bulk_report_ready"
        run.note({"stage": "WorkReport", "bulk": True, "index": run.bulk_reports, **values})
        return False  # Bulk.ReportNext taps 「Touch to Next」 on this frame

    def _reward_wait(self, run, vision, image):
        """獲得報酬 page: the four-template guard on two frames (>= confirm apart), then the Ex skill entries visible
        without scrolling are logged (``reward_page``) and Reward.Redispatch presses 再営業へ once. The grey OK is not used."""
        if run.phase == "reward_ready":
            run.phase, run.reward_reading = "reward_wait", None  # Reward.Redispatch did not fire: read again
        if run.phase != "reward_wait":
            return False
        now = self.clock()
        self._maybe_save(run, image, now)
        if now - run.report_since >= self.report_wait_seconds:
            run.finish("reward_unstable", "reward_page_not_stable")
            return True
        if not vision.hit(REWARD_GUARD, image):
            run.reward_reading = None
            return True
        previous = run.reward_reading
        if previous is None:
            run.reward_reading = (True, now)
            return True
        if now - previous[1] < self.confirm_seconds:
            return True
        self._save(run, image)
        details = self.reward_details(vision, image)
        if not self._log_or_fail(run, {"event": "reward_page", "bulk": True, **details, "frame": tc.image_digest(image)}):
            return True
        run.phase = "reward_ready"
        run.note({"stage": "RewardPage", "entries": details["count"], "partial": details["partial"]})
        return False  # Reward.Redispatch presses 再営業へ on this frame

    def _select_wait(self, context, run, vision, image):
        """営業アイドル選択: three slots read on two equal frames, bound (``bt.select_binding``); matched + ``decide`` ->
        Select.Decide (bulk_intent first); anything else -> Select.Back (←) with the reason as the outcome."""
        if run.phase in ("select_ready", "select_back_ready"):
            run.phase, run.select_reading = "select_wait", None  # the node did not fire: read again
        if run.phase != "select_wait":
            return False
        now = self.clock()
        self._maybe_save(run, image, now)
        if not vision.hit(SELECT_GUARD, image):
            run.select_reading = None
            if now - run.report_since >= self.report_wait_seconds:
                run.finish("select_unstable", "select_page_not_recognised")
            return True
        if now - run.report_since >= self.report_wait_seconds:  # never read stably: leave without pressing 決定
            return self._select_leave(run, "redispatch_binding_mismatch", {"problems": ["unreadable"]})
        slots = self.select_slots(vision, image)
        try:
            forbidden = bt.dialog_forbidden(" ".join(f"{s['name']} {s['duration']}" for s in slots).split())
            error = self.error_text(context, image)
        except Exception:
            forbidden, error = [], None
        binding = bt.select_binding(slots, run.config, forbidden + (["error_dialog"] if error else []))
        if bt.select_loading(slots):
            # 2026-10-12 live (samples business-top-20261012T064115785252Z #046–#049): after 再営業へ the page shows
            # three empty 「未選択」 slots (決定 grey) for a few seconds before the previous teams appear. Keep reading
            # until the page timeout instead of binding the empty page.
            run.select_reading = None
            return True
        previous = run.select_reading
        if previous is None or previous[0] != slots:
            run.select_reading = (slots, now)
            return True
        if now - previous[1] < self.confirm_seconds:
            return True
        self._save(run, image)
        run.select_binding = binding
        if not self._log_or_fail(run, {"event": "select_page", "bulk": True, "matched": binding["matched"],
                                       "problems": binding["problems"], "slots": slots,
                                       "redispatch": run.config.redispatch, "frame": tc.image_digest(image)}):
            return True
        run.note({"stage": "SelectPage", "matched": binding["matched"], "problems": binding["problems"],
                  "slots": slots})
        if not binding["matched"]:
            return self._select_leave(run, "redispatch_binding_mismatch", binding)
        if run.config.redispatch != "decide":
            return self._select_leave(run, "collected_dispatch_disabled", binding)
        try:
            pending = bt.bulk_pending(self.log.events())
        except Exception as exc:
            run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
            return True
        if "dispatch" in run.bulk_done or "dispatch" in pending:  # never repeat a dispatch whose result is uncertain
            return self._select_leave(run, "uncertain_result_today", binding)
        run.phase = "select_ready"
        return False  # Select.Decide presses 決定 on this frame

    def _select_leave(self, run, outcome, binding):
        run.back_outcome, run.phase = outcome, "select_back_ready"
        run.note({"stage": "SelectLeave", "outcome": outcome, "problems": binding.get("problems")})
        return False  # Select.Back presses ← on this frame

    def _select_decide(self, image):
        """Plan.SelectDecide on the exact frame of the tap: ``bulk_intent`` (dispatch) is logged first (no log line -> no
        tap); a later uncertain result is never repeated (bt.bulk_pending)."""
        run = self.run
        binding = run.select_binding
        if (run.config.redispatch != "decide" or binding is None or not binding.get("matched")
                or "dispatch" in run.bulk_done):
            return False
        self._save(run, image)
        if not self._log_or_fail(run, {"event": "bulk_intent", "action": "dispatch", "via": "redispatch_decide",
                                       "slots": binding["slots"], "frame": tc.image_digest(image),
                                       "tap": "business-dispatch-decide"}):
            return False
        run.bulk_done.add("dispatch")
        run.phase, run.post_at, run.home_seen, run.post_dialog = "select_sent", self.clock(), 0, None
        run.note({"stage": "SelectDecide", "slots": binding["slots"]})
        self._mark(run)
        return True

    def _post_observe(self, context, run, vision, image):
        """Read-only after OK / 決定 / ← (<= bulk_observe_seconds). OK -> 営業アイドル選択 (or the business top);
        決定 -> Home (two frames) or the business top (re-read by TopWait); ← -> the business top / Home."""
        if run.phase not in ("reward_sent", "select_sent", "select_back_sent"):
            return False
        now = self.clock()
        elapsed = now - run.post_at
        self._maybe_save(run, image, now)
        home, top = vision.hit(HOME_GUARD, image), vision.hit(TOP_GUARD, image)
        if run.phase == "reward_sent":
            if vision.hit(SELECT_GUARD, image):
                run.phase, run.select_reading, run.report_since = "select_wait", None, now
                run.note({"stage": "SelectSeen", "seconds": round(elapsed, 1)})
                return False  # SelectWait reads this frame
            if top and not home:
                # 再営業へ went straight back to the business top: whatever it dispatched was never bound to the
                # 営業アイドル選択 check -> TopWait re-reads the slots and the run ends dispatched_binding_unverified.
                run.dispatch_unverified = True
                run.phase, run.left_home_at, run.top_reading = "top_wait", now, None
                return True
            if vision.hit(REWARD_GUARD, image):
                if elapsed >= self.report_no_effect_seconds:
                    run.finish("reward_ok_no_effect", "reward_page_still_shown_after_redispatch")
                return True
            self._post_dialog(context, run, now, image)
            if elapsed >= self.bulk_observe_seconds:
                run.finish("reward_unverified", "unknown_screen_after_reward_redispatch",
                           *(["dialog " + " / ".join(run.post_dialog)[:160]] if run.post_dialog else []))
            return True
        if run.phase == "select_sent":
            run.home_seen = run.home_seen + 1 if home else 0
            if run.home_seen >= 2:
                if not self._log_or_fail(run, {"event": "bulk_dispatched", "action": "dispatch",
                                               "via": "redispatch_home", "binding": "verified_before_decide"}):
                    return True
                run.finish("collected_and_redispatched", "home_after_decide")
                return True
            if top and not home:
                run.phase, run.left_home_at, run.top_reading = "top_wait", now, None  # _top_bulk proves / resolves it
                return True
            if home:
                return True
            if vision.hit(SELECT_GUARD, image) and elapsed >= self.bulk_observe_seconds:
                # 2026-10-14 live: Now Loading and the 「営」 start animation are drawn over 営業アイドル選択 (title and
                # 決定 stay visible) for more than 8 s after 決定; only the full observe window proves no effect.
                self._redispatch_uncertain(run, "select_page_still_shown_after_decide")
                return True
            self._post_dialog(context, run, now, image)
            if elapsed >= self.bulk_observe_seconds:
                self._redispatch_uncertain(run, "unknown_screen_after_decide")
            return True
        # select_back_sent
        if home:
            run.finish(run.back_outcome or "redispatch_binding_mismatch", "home_after_select_back")
            return True
        if top:
            run.phase, run.pending = "sampled", run.back_outcome or "redispatch_binding_mismatch"
            return False  # the back key leaves the business top on this frame
        if elapsed >= self.bulk_observe_seconds:
            run.finish("select_back_unverified", "unknown_screen_after_select_back")
        return True

    def _redispatch_uncertain(self, run, reason):
        """決定 was sent but its result is not proven: logged as an uncertain dispatch (never repeated today)."""
        self._log({"event": "bulk_uncertain", "action": "dispatch", "reason": reason})
        run.finish("redispatch_unverified", reason,
                   *(["dialog " + " / ".join(run.post_dialog)[:160]] if run.post_dialog else []))

    def _post_dialog(self, context, run, now, image):
        """Record the text of an unknown screen after OK / 決定 (evidence only, at most every 2 s)."""
        if run.bulk_dialog_at is not None and now - run.bulk_dialog_at < 2.0:
            return
        run.bulk_dialog_at = now
        try:
            texts = self.dialog_texts(context, image)
        except Exception:
            texts = []
        if texts and texts != run.post_dialog:
            run.post_dialog = texts
            self._save(run, image)
            run.note({"stage": "PostDialog", "texts": texts, "forbidden": bt.dialog_forbidden(texts)})

    def _bulk_report_unread(self, run, reason):
        """A bulk WORK REPORT that was never read validly: stop without a tap. A 一括完了 of this run not yet proven
        stays uncertain (``bulk_uncertain``; a later run re-observes the business top), never ``bulk_completed``."""
        if run.bulk_action is not None:
            self._log({"event": "bulk_uncertain", "action": run.bulk_action, "reason": reason})
            run.bulk_action = None
        run.finish("report_unstable", f"report {run.bulk_reports + 1}", reason)

    def box_rows(self, vision, image, box, threshold=0.3):
        """OCR rows ``{"text", "box"}`` (score >= threshold) inside ``box``; evidence reading, lenient on purpose."""
        from maa.pipeline import JOCR, JRecognitionType
        param = JOCR(expected=[".+"], roi=tuple(box), threshold=threshold)
        detail = vision.context.run_recognition_direct(JRecognitionType.OCR, param, image)
        results = (getattr(detail, "all_results", None) or []) if detail is not None else []
        return [{"text": r.text, "box": list(r.box)} for r in results if isinstance(r.text, str) and r.text.strip()]

    def box_texts(self, vision, image, box, threshold=0.3):
        """Every OCR line (score >= threshold) inside ``box``."""
        return [r["text"] for r in self.box_rows(vision, image, box, threshold)]

    def reward_details(self, vision, image):
        """Evidence of the 獲得報酬 page: the Ex skill entries visible without scrolling (idol, P/S badge, effect text)."""
        entries = []
        for column, box in enumerate(REWARD_COLUMNS):
            try:
                rows = self.box_rows(vision, image, box)
            except Exception:
                rows = []
            x, y0 = REWARD_BADGE_XY[column]
            badges = []
            for index in range(6):
                try:
                    badges.append(self.badge_at(image, x, y0 + 106 * index))
                except Exception:
                    badges.append(None)
            entries += [{"column": column, **entry} for entry in bt.ex_skill_entries(rows, badges)]
        return {"entries": entries[:bt.REWARD_MAX_ENTRIES * 2], "count": len(entries),
                "partial": True, "scrolled": False}  # the list scrolls; only the visible part is read

    @staticmethod
    def badge_at(image, x, y):
        """P / S of the round badge centred at (x, y): the majority of six probe points around the centre (the white
        letter hides the centre pixel); None when fewer than three agree or the probes leave the frame."""
        if y + 10 >= 720:
            return None
        kinds = [bt.badge_kind(image[y + dy, x + dx]) for dx, dy in ((-10, 0), (10, 0), (0, -10), (0, 10), (-7, -7), (7, 7))]
        for kind in ("P", "S"):
            if kinds.count(kind) >= 3:
                return kind
        return None

    def select_slots(self, vision, image):
        """The three slots of 営業アイドル選択: header, duration and how many 「Lv.NN」 card labels were read."""
        slots = []
        for k in range(bt.SELECT_SLOTS):
            try:
                name = " ".join(self.box_texts(vision, image, SELECT_NAME_BOX(k)))
                duration = " ".join(self.box_texts(vision, image, SELECT_DURATION_BOX(k)))
                lv = [t for t in self.box_texts(vision, image, SELECT_LV_BOX(k)) if LV_TEXT.search(t)]
                # one OCR line may hold several labels (「Lv.62 Lv.60」): count the labels, not the lines
                lv_count = sum(len(LV_TEXT.findall(t)) for t in lv)
            except Exception:
                name, duration, lv_count = "", "", 0
            badges = self.select_badges(image, k)
            slots.append({"name": name[:30], "duration": duration[:20], "lv": lv_count, "badges": badges,
                          "filled": min(bt.SELECT_CARDS_PER_SLOT, max(lv_count, badges))})
        return slots

    @staticmethod
    def select_badges(image, k):
        """Cards of slot ``k`` showing the blue S badge (top-left of each card, x ~52 + 113.7 i, y ~158 + 174 k): a card
        counts when >= 3 of 6 probe points around the badge centre read blue (the white S glyph hides the centre)."""
        count = 0
        try:
            for i in range(bt.SELECT_CARDS_PER_SLOT):
                x, y = 52 + int(113.7 * i), 158 + 174 * k
                blue = sum(bt.badge_kind(image[y + dy, x + dx]) == "S"
                           for dx, dy in ((-9, 0), (9, 0), (0, -9), (0, 9), (-7, -7), (7, 7)))
                count += blue >= 3
        except Exception:
            return 0
        return count

    def _gift_banner(self, vision, image):
        try:
            texts = self.box_texts(vision, image, GIFT_BANNER_BOX)
        except Exception:
            return False
        joined = "".join(texts).replace(" ", "")
        return any(word in joined for word in GIFT_BANNER_WORDS)

    def gift_details(self, vision, image):
        """Evidence of the gift page: visible tile multipliers (icons carry no text) and the overflow message."""
        try:
            tiles = self.box_texts(vision, image, GIFT_TILES_BOX)
            message = self.box_texts(vision, image, GIFT_MESSAGE_BOX)
        except Exception:
            tiles, message = [], []
        counts = [t.replace(" ", "") for t in tiles if re.fullmatch(r"[×xX＊*]\s*\d+", t.strip().replace(" ", ""))]
        return {"tiles_visible": len(counts), "counts": counts[:40], "message": " ".join(message)[:80],
                "overflow_notice": any("所持枠" in m for m in message)}

    def _report_variant(self, vision, image):
        """``touch`` (settled report page with 「Touch to Next」), ``paged`` (last screen with 「次へ」), ``gift``
        (「ファンからのプレゼント」 page with 「次へ」) or None."""
        if vision.hit(REPORT_GUARD, image):
            return "touch"
        if vision.hit(PAGED_GUARD, image):
            return "paged"
        return "gift" if vision.hit(GIFT_GUARD, image) and self._gift_banner(vision, image) else None

    def _report_takeover(self, run, vision, image):
        """A run that starts on a WORK REPORT (nothing pressed yet in this run). Explicit modes take it over
        read-only first (two stable readings in ``report``); ``sample`` stops without a click."""
        variant = self._report_variant(vision, image) if run.phase == "active" else None
        if variant is None:
            return False
        run.report_variant = variant
        self._save(run, image)
        if not self._reads_top(run):
            run.finish("work_report_on_screen", "run business_mode=sample_collect_one to close it")
            return True
        now = self.clock()
        run.phase, run.takeover, run.report_since, run.left_home_at = "report_wait", True, now, now
        run.report_reading = None
        run.note({"stage": "ReportTakeover", "variant": variant})
        return False  # ReportWait reads this frame

    def _report_wait(self, run, vision, image):
        """WORK REPORT on screen (after this run's claim or at a takeover): read Exp / fans on two frames (>= confirm
        apart, same values, guard on both), then log ``work_report`` + ``claimed`` and let Report.Next tap once."""
        if run.phase == "report_ready":
            run.phase, run.report_reading = "report_wait", None  # Report.Next did not fire: read again
        if run.phase != "report_wait":
            return False
        now = self.clock()
        self._maybe_save(run, image, now)
        if now - run.report_since >= self.report_wait_seconds:
            if not run.takeover and not run.claim_logged:
                # B6h review #4: a report never read validly proves nothing: the claim stays uncertain, no tap.
                self._claim_uncertain(run, "report_readings_invalid", "report_unstable")
            if not run.finished:
                run.finish("report_unstable", f"slot {run.claim_slot}", "report_readings_invalid")
            return True
        if self._report_variant(vision, image) != run.report_variant:
            run.report_reading = None
            return True
        gift = run.report_variant == "gift" and run.report_logged  # only a page AFTER a logged report of this run
        values = {"gift": True} if gift else self.report_values(vision, image)
        if not gift and not bt.report_valid(values):  # B6h review #4: an unknown reading is never a stable report
            run.report_reading = None
            return True
        previous = run.report_reading
        if previous is None or previous[0] != values:
            run.report_reading = (values, now)
            return True
        if now - previous[1] < self.confirm_seconds:
            return True
        self._save(run, image)
        if gift:
            details = self.gift_details(vision, image)
            if not self._log_or_fail(run, {"event": "work_report_gift", "slot": run.claim_slot, **details,
                                           "frame": tc.image_digest(image)}):
                return True
            run.phase = "report_ready"
            run.note({"stage": "ReportGift", **details})
            return False
        if run.report_logged:  # a later page / the 次へ screen of a report already logged in this run
            run.phase = "report_ready"
            run.note({"stage": "ReportPage", "variant": run.report_variant, **values})
            return False
        digest = tc.image_digest(image)
        try:
            slot = bt.takeover_slot(self.log.events()) if run.takeover else run.claim_slot
        except Exception as exc:
            run.finish("log_failed", "business_log_unreadable", str(exc)[:120])
            return True
        record = {"event": "work_report", "slot": slot, **values, "frame": digest,
                  **({"takeover": True} if run.takeover else {}),
                  **({"paged": True} if run.report_variant == "paged" else {})}
        if not self._log_or_fail(run, record):
            return True
        run.report_logged = True
        if slot is not None:
            claimed = {"event": "claimed", "slot": slot, "via": "work_report"}
            if run.takeover:
                claimed.update(via="work_report_takeover", resolved=True)
            if not self._log_or_fail(run, claimed):
                return True
            run.claim_slot, run.claim_logged = slot, True
        run.phase = "report_ready"
        run.note({"stage": "WorkReport", "slot": slot, "takeover": run.takeover, **values})
        return False  # Report.Next taps 「Touch to Next」 on this frame

    def _after_report(self, context, run, vision, image):
        """Read-only after 「Touch to Next」 (<= observe_seconds): back on the business top (same slot readings on two
        frames) -> record the slots; collect -> back key -> Home; dispatch_open -> choose an empty slot."""
        if run.phase != "report_sent":
            return False
        now = self.clock()
        elapsed = now - run.report_at
        self._maybe_save(run, image, now)
        if any(vision.hit(node, image) for node in OBSERVE_POPUPS):
            run.popups += 1
            run.note({"stage": "ObservePopup", "count": run.popups})
            run.after_reading = None
            return False
        variant = self._report_variant(vision, image)
        if variant is not None:
            run.after_reading = None
            if (variant in PAGE_VARIANTS and (run.after_gap or run.report_variant != variant)
                    and run.report_presses < MAX_REPORT_PRESSES):
                # B6f: the paged report's closing screen (次へ) after the last 「Touch to Next」: read it, press 次へ.
                run.phase, run.report_variant, run.report_since, run.report_reading = "report_wait", variant, now, None
                return False
            if elapsed >= self.report_no_effect_seconds:
                run.finish("report_next_no_effect", f"slot {run.claim_slot}",
                           *(["claimed logged"] if run.claim_logged else []))
            return True
        run.after_gap = True
        if vision.hit(TOP_GUARD, image):
            kinds, texts = self.slot_kinds(context, vision, image)
            previous = run.after_reading
            if previous is None or previous[0] != kinds:
                run.after_reading = (kinds, now)
                return True
            if now - previous[1] < self.confirm_seconds:
                return True
            self._save(run, image)
            run.note({"stage": "TopAfterReport", "slot": run.claim_slot, "kinds": list(kinds), "texts": texts})
            self._log({"event": "top_after_report", "slot": run.claim_slot, "kinds": list(kinds)})  # evidence only
            if self._dispatch(run) or self._bulk(run):
                run.phase, run.left_home_at, run.top_reading = "top_wait", now, None
                return True
            run.phase, run.pending = "sampled", ("claimed" if run.claim_logged else "report_closed")
            return False  # back key on this frame
        run.after_reading = None
        if elapsed >= self.observe_seconds:
            run.finish("report_next_unverified", f"slot {run.claim_slot}",
                       *(["claimed logged"] if run.claim_logged else []))
        return True

    def _dispatch_observe(self, run, vision, image):
        """Read-only after the empty-slot tap (<= dispatch_seconds): save frames; after dispatch_sample_seconds and
        >= 2 frames off the business top, the back key (visible, undimmed) is pressed once by DispatchBack."""
        if run.phase == "dispatch_back_ready":
            run.phase = "dispatch_sent"  # DispatchBack did not fire on the previous frame
        if run.phase != "dispatch_sent":
            return False
        now = self.clock()
        elapsed = now - run.dispatch_at
        self._maybe_save(run, image, now)
        on_top = vision.hit(TOP_GUARD, image)
        if not on_top:
            run.dispatch_seen += 1
        page = not on_top and self._dispatch_page(vision, image)
        if page:
            run.dispatch_page_seen += 1
        if (page and run.dispatch_page_seen >= 2 and elapsed >= self.dispatch_sample_seconds
                and not vision.hit(HOME_GUARD, image) and vision.hit(BACK_READY, image)):
            self._save(run, image)
            run.phase = "dispatch_back_ready"
            run.note({"stage": "DispatchScreen", "slot": run.open_slot, "seconds": round(elapsed, 1),
                      "frames_off_top": run.dispatch_seen})
            return False  # DispatchBack presses the back key on this frame
        if elapsed >= self.dispatch_seconds:
            if run.dispatch_seen == 0:
                run.finish("dispatch_screen_not_opened", f"slot {run.open_slot}")
            elif run.dispatch_page_seen < 2:  # B6h review #3: an unknown page is never left with the back key
                run.finish("dispatch_not_returned", f"slot {run.open_slot}", "dispatch_page_unverified",
                           f"frames_off_top {run.dispatch_seen}")
            else:
                run.finish("dispatch_not_returned", f"slot {run.open_slot}", "back_key_not_ready")
        return True

    def _dispatch_page(self, vision, image):
        """B6h review #3: positive evidence of the dispatch (営業先選択) page. No native sample exists yet, so there is no
        guard node and this is always False: after the empty-slot tap the page is only sampled, never left with the
        back key (``dispatch_not_returned`` / ``dispatch_page_unverified``)."""
        return DISPATCH_PAGE_GUARD is not None and vision.hit(DISPATCH_PAGE_GUARD, image)

    def _dispatch_returned(self, context, run, vision, image):
        """After the back key on the dispatch screen: the business top -> record slots, back key -> Home; Home ->
        done; nothing within dispatch_return_seconds -> stop (no further click)."""
        if run.phase != "dispatch_returning":
            return False
        now = self.clock()
        self._maybe_save(run, image, now)
        if vision.hit(HOME_GUARD, image):
            run.finish("dispatch_sampled", f"slot {run.open_slot}", "home_after_back")
            return True
        if vision.hit(TOP_GUARD, image):
            kinds, texts = self.slot_kinds(context, vision, image)
            self._save(run, image)
            run.note({"stage": "TopAfterDispatch", "slot": run.open_slot, "kinds": list(kinds)})
            run.phase, run.pending = "sampled", "dispatch_sampled"
            return False  # back key -> Home
        if now - run.back_at >= self.dispatch_return_seconds:
            run.finish("dispatch_return_unverified", f"slot {run.open_slot}")
        return True

    def _observe(self, run, vision, image):
        """Read-only after the tap (<= observe_seconds): the business top with the claimed slot not CLEAR on two frames
        -> ``claimed``; a whitelisted pop-up -> let Observe.Popup close it; anything else -> keep observing, then stop."""
        if run.phase != "claim_sent":
            return False
        now = self.clock()
        elapsed = now - run.claim_at
        self._maybe_save(run, image, now)
        variant = self._report_variant(vision, image)
        if variant is not None:  # B6d: the settled WORK REPORT of this claim (B6f: or its 次へ screen)
            run.report_variant = variant
            self._save(run, image)
            run.phase, run.report_since, run.report_reading = "report_wait", now, None
            run.note({"stage": "ReportSeen", "slot": run.claim_slot, "seconds": round(elapsed, 1)})
            return False  # ReportWait reads this frame
        if any(vision.hit(node, image) for node in OBSERVE_POPUPS):
            run.popups += 1
            run.note({"stage": "ObservePopup", "count": run.popups})
            run.cleared_at = None
            return False
        on_top = vision.hit(TOP_GUARD, image)
        if on_top and self.slot_states(vision, image)[run.claim_slot - 1] != "completed":
            if run.cleared_at is None:
                run.cleared_at = now
            elif now - run.cleared_at >= self.confirm_seconds and elapsed >= self.observe_min_seconds:
                self._save(run, image)
                states = self.slot_states(vision, image)
                if not self._log_or_fail(run, {"event": "claimed", "slot": run.claim_slot, "states": list(states),
                                               "seconds": round(elapsed, 1)}):
                    return True
                run.phase, run.pending = "sampled", "claimed"
                run.note({"stage": "Claimed", "slot": run.claim_slot, "states": list(states)})
                return False  # back key on this frame
        else:
            run.cleared_at = None
        if elapsed >= self.observe_seconds:
            if on_top:
                self._claim_uncertain(run, "slot_still_clear_after_tap")
            else:
                self._claim_uncertain(run, "unknown_screen_after_tap", "needs_sampling")
        return True

    def abort(self, context, p):
        run = self.run
        if run is not None and not run.finished and run.phase in ("claim_sent", "report_sent", "dispatch_sent",
                                                                   "bulk_sent", "bulk_report_sent", "reward_sent",
                                                                   "select_sent", "select_back_sent"):
            if self._click_outcome(run) == "not_sent":
                # The controller / host refused the tap before any gesture (e.g. frame binding): nothing happened in
                # the game. Close the intent; a later run may press again after re-observing the screen.
                if run.phase == "claim_sent":
                    self._log({"event": "claim_refused", "slot": run.claim_slot, "reason": "click_refused_not_sent"})
                elif run.phase == "bulk_sent" and run.bulk_action is not None:
                    self._log({"event": "bulk_refused", "action": run.bulk_action, "reason": "click_refused_not_sent"})
                elif run.phase == "select_sent":  # 決定 never left the controller: the dispatch intent is closed
                    self._log({"event": "bulk_refused", "action": "dispatch", "reason": "click_refused_not_sent"})
                run.finish("click_refused_not_sent", f"phase {run.phase}", "retry allowed in a later run")
        if run is not None and not run.finished:
            if run.phase == "opening":
                run.finish("not_opened", "home_still_shown_after_business_tap")
            elif run.phase == "returning":
                run.finish("return_unverified", "home_not_observed_after_back")
            elif run.phase == "claim_sent":
                self._claim_uncertain(run, "unhandled_screen_after_tap")
            elif run.phase in ("report_wait", "report_ready") and not run.takeover and not run.claim_logged:
                self._claim_uncertain(run, "unhandled_screen_on_report")
            elif run.phase in ("report_wait", "report_ready"):
                run.finish("report_unstable", "unhandled_screen_on_report",
                           *(["claimed logged"] if run.claim_logged else []))
            elif run.phase == "report_sent":
                run.finish("report_next_unverified", "unhandled_screen_after_report_next",
                           *(["claimed logged"] if run.claim_logged else []))
            elif run.phase in ("dispatch_sent", "dispatch_back_ready"):
                run.finish("dispatch_not_returned", "unhandled_screen_after_slot_open")
            elif run.phase == "dispatch_returning":
                run.finish("dispatch_return_unverified", "unhandled_screen_after_dispatch_back")
            elif run.phase == "bulk_sent":
                if run.bulk_action is not None:
                    self._log({"event": "bulk_uncertain", "action": run.bulk_action,
                               "reason": "unhandled_screen_after_press"})
                run.finish("bulk_result_uncertain", "unhandled_screen_after_press")
            elif run.phase == "bulk_report_sent":
                run.finish("report_next_unverified", "unhandled_screen_after_report_next")
            elif run.phase in ("bulk_report_wait", "bulk_report_ready"):
                self._bulk_report_unread(run, "unhandled_screen_on_report")
            elif run.phase in ("reward_wait", "reward_ready"):
                run.finish("reward_unstable", "unhandled_screen_on_reward")
            elif run.phase == "reward_sent":
                run.finish("reward_unverified", "unhandled_screen_after_reward_redispatch")
            elif run.phase in ("select_wait", "select_ready", "select_back_ready"):
                run.finish("select_unstable", "unhandled_screen_on_select")
            elif run.phase == "select_sent":
                self._redispatch_uncertain(run, "unhandled_screen_after_decide")
            elif run.phase == "select_back_sent":
                run.finish("select_back_unverified", "unhandled_screen_after_select_back")
            elif run.phase in ("top_wait", "claim_ready", "open_ready", "bulk_ready"):
                run.finish("needs_sampling", "unhandled_screen_before_tap")
            elif run.phase in ("sampling", "sampled"):
                run.finish("sampled_not_returned", "unhandled_screen_after_sampling",
                           *([f"pending {run.pending}"] if run.pending else []))
        if run is not None and run.samples and not any(str(n).startswith("samples ") for n in run.notes):
            run.notes.extend(f"samples {d}" for d in run.samples)
        return super().abort(context, p)


RUNTIME = BusinessRuntime()
RECOGNITIONS = {"State": "state", "Plan": "plan", "Check": "check"}
ACTIONS = {"Begin": "begin", "Finish": "finish_action", "Abort": "abort"}


def business_registrations(runtime):
    return task_custom.registrations(runtime, PREFIX, RECOGNITIONS, ACTIONS)
