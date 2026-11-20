"""Maa custom recognitions/actions of the MatchSkip task (B5 sampling, B5b skipping). Rules in ``agent.core.match_task``."""
from __future__ import annotations

import dataclasses
import time

import numpy as np

from agent.core import fes_allocation as fa
from agent.core import match_task as mt
from agent.core import task_common as tc
from agent.core import task_custom
from agent.core import match_progress as mp
from agent.core import match_reader as mr
from agent.core import match_scan as ms
from agent.core.match_observed import ObservedSkipPlanner
from agent.core import match_latest as ml
from agent.core import match_play as mpl

PREFIX = "SC.Match"
CONFIG_NODES = ("SC.Match.Config.Stage", "SC.Match.Config.Count", "SC.Match.Config.Reserve",
                "SC.Match.Config.Recovery", "SC.Match.Config.RecoveryCap", "SC.Match.Config.Sample",
                "SC.Match.Config.Play", "SC.Match.Config.Unit", "SC.Match.Config.Retries")
DIALOG_GUARD = "SC.Nav.MatchSkip.Guard"
# 2026-10-24: the Skip wheel waits 1 s instead of 5.2 s (that only respected the old host limit of 12 inputs per action and
# minute; it is 30 now and a run sends at most 16 wheels). A list that may still coast is read only once two consecutive
# captures show the same rows (native static dialogs: 0 changed pixels; frames after a wheel: ~77 %).
LIST_REGION = (60, 180, 1180, 360)        # both stage columns, three rows, counts and ＋ (x, y, w, h)
SETTLE_CHANGED_MAX = 0.002                # fraction of region pixels changed by > 24 levels
SETTLE_FRAMES_MAX = 4                     # captures after one wheel before scroll_not_settled


class ListSettle:
    """Two consecutive identical captures of the list region after a wheel."""

    def __init__(self):
        self.prev, self.frames = None, 0

    def observe(self, image) -> str:
        """"settled" | "moving" | "exhausted" for this capture."""
        x, y, w, h = LIST_REGION
        region = np.asarray(image[y:y + h, x:x + w, :3], dtype=np.int16)
        prev, self.prev = self.prev, region
        self.frames += 1
        if prev is not None and prev.shape == region.shape and                 float((np.abs(region - prev).max(axis=-1) > 24).mean()) <= SETTLE_CHANGED_MAX:
            return "settled"
        return "exhausted" if self.frames > SETTLE_FRAMES_MAX else "moving"


def thumb_span(image):
    """(top, bottom) rows of the white scrollbar thumb in the skip dialog list; None when there is no thumb."""
    column = image[mt.SCROLLBAR_TOP:mt.SCROLLBAR_BOTTOM + 1, mt.SCROLLBAR_X].astype(int)
    white = [i for i, (b, g, r) in enumerate(column) if min(b, g, r) >= 245]
    if not white:
        return None
    return mt.SCROLLBAR_TOP + white[0], mt.SCROLLBAR_TOP + white[-1]


def header_bar_present(image, slot):
    x, y = mt.SLOT_BAR[slot]
    b, g, r = (int(v) for v in image[y, x])
    return b >= 200 and r <= 120 and 100 <= g <= 210  # blue header bar of a stage row


def plus_enabled(image, slot):
    x, y, w, h = mt.SLOT_PLUS[slot]
    patch = image[y:y + h, x:x + w].astype(int)
    pink = (patch[:, :, 2] >= 200) & (patch[:, :, 1] <= 90) & (patch[:, :, 0] >= 100)
    return int(pink.sum()) >= 25


def progress_candidate(scan, main):
    """Untrusted progress candidate for later review: the highest *skippable* stage of a complete survey plus the
    main-page ranking deadline as a cycle hint. Never MatchProgress, never input authority (a stage that is open but
    not skippable is absent from this list, and a ranking deadline is not reset-cycle proof)."""
    complete = scan.complete and scan.reason is None
    deadline = main.ranking_deadline if main is not None else None
    return {"trusted": False, "input_authorized": False, "skip_list_complete": complete,
            "latest_skippable_stage": (f"A{max(scan.stages)[0]}-S{max(scan.stages)[1]}"
                                       if complete and scan.stages else None),
            "stage_count": len(scan.stages) if complete else None,
            "cycle_hint": (f"ranking_until_{deadline.month:02d}-{deadline.day:02d}T{deadline.hour:02d}:{deadline.minute:02d}"
                           if deadline is not None else None),
            "main_frame_digest": main.frame_digest if main is not None else None}


class MatchRuntime(task_custom.TaskRuntime):
    KIND = "match"
    RUN = mt.MatchRun
    CONFIG = mt.MatchConfig
    SPEND_ACTION = mt.SPEND_ACTION

    def __init__(self, *args, clock=time.monotonic, progress=None, progress_reader=None,
                 scan_sample=True, **kwargs):
        super().__init__(*args, **kwargs)
        tc.require(progress is None or isinstance(progress, mp.MatchProgress), "Match 进度合同类型无效")
        tc.require(progress_reader is None or callable(progress_reader), "Match 周期证据读取器类型无效")
        # Legacy full-progress fixtures remain supported. Explicit live selections
        # use a fresh Skip dialog survey instead of a fictitious current-cycle ID.
        self.progress, self.progress_reader = progress, progress_reader
        self.clock = clock
        self._cache = {}
        tc.require(type(scan_sample) is bool, "Match 完整列表采样开关须为布尔值")
        # 2026-10-11: sample-only runs survey the whole skippable list by default (bounded wheel, zero selections).
        # It only changes sample_only; actual skip planning never reads this survey as progress proof.
        self.scan_sample = scan_sample
        self.play = None
        self._play_cache, self._play_unsent, self._play_unsent_runs = (None, None), None, 0

    def started(self, run):
        self._cache = {}
        self.play = None
        self._play_cache, self._play_unsent, self._play_unsent_runs = (None, None), None, 0
        self._observed_page = None
        run.progress = run.planner.progress = self.progress
        # For an explicit selection the actual Skip dialog is authoritative for
        # current eligibility. Full open-stage history is only needed for latest.
        if self.progress is None and run.config.explicit() is not None and not run.config.sample_only:
            run.planner = ObservedSkipPlanner(run.config)
        elif self.progress is None and run.config.stage == "latest" and not run.config.sample_only:
            # 2026-10-14 user: 「最新」 = the last stage of the Skip list, read in the dialog itself (no area scroll).
            run.planner = ml.SkipListLatestPlanner(run.config)
        self.sample_scan = ms.SkipListScan()
        self.sample_scrolls = self.sample_waits = 0
        self.sample_pending_thumb = None
        self.sample_settle = None
        self.sample_scan_ready = False
        self.sample_main = None
        self.latest_navigation = None   # area-list navigation (LatestNavigation) is no longer needed for 「最新」
        self._latest_decision = None
        refusal = fa.recovery_refusal(run.config.use_recovery_tickets, run.config.recovery_cap, fa.load_policy())
        if refusal:  # a separate resource: refused before any click while the policy / live path does not allow it
            run.finish(refusal, fa.MESSAGES[refusal])
            return
        if run.config.sample_only:
            return
        events = self.log.events()
        settled = {e.get("intent_time") for e in events if e.get("event") in ("verified", "not_spent")
                   and e.get("intent_time")}
        # A denied grant/click was never sent; its timestamp identifies the logged intent.
        settled.update(e.get("time") for e in events if e.get("event") == "refused" and e.get("time"))
        intents = [e for e in events if e.get("event") == "intent" and e.get("time") not in settled]
        if intents:  # Noted; this run plans from the rights it reads on screen
            run.notes.append(f"unresolved_skip_batches_{len(intents)}")
        if run.config.play == "advance":
            # 2026-10-31 user 「让maa自动推进」: play the frontier stage first, the Skip takes the rights left.
            if self.authority is None or not self.authority.can_spend(mpl.SPEND_ACTION):
                run.notes.append("play_not_enabled")
            else:
                self.play = mpl.MatchPlay(run.config, self.log, self.authority, self.now)
                run.phase = "play"
                self._log({"event": "play_begin", "unit": run.config.unit or "current", "retries": run.config.retries})

    # -- play (match_play): one decision per fresh frame, tapped by its SC.Match.Play.* node
    def play_check(self, context, image, p):
        """``stage`` = "observe" (decide once, never hits) or a decision kind (hits iff this frame's decision)."""
        run, play = self.run, self.play
        if run is None or play is None or run.phase != "play":
            return False
        decision = self.play_decide(context, image)
        stage = p.get("stage")
        if stage == "wait" and self._play_unsent is not None:
            # Wait follows every play click node: this frame's tap node did not run (its guard or max_hit; live 10-31
            # AreaNext's 20th hit fell through to SC.Nav.BackOne). Undo the decision and read another frame.
            self._play_rollback()
            self._play_cache = (None, None)
            return True
        if stage == "observe" or decision.kind != stage:
            return False
        # The node of this decision runs now; the next frame is decided afresh even with identical pixels.
        self._play_cache, self._play_unsent = (None, None), None
        if decision.kind in mpl.CLICK_NODES:
            self._play_unsent_runs = 0
        return True

    def _play_rollback(self):
        run, play = self.run, self.play
        kind, state = self._play_unsent
        self._play_unsent = None
        if kind == "live_start" and play.lives:
            self._log({"event": "play_not_spent", **play.lives[-1], "reason": "tap_not_sent"})
        play.restore(state)
        self._play_unsent_runs += 1
        self._log({"event": "play_tap_not_sent", "kind": kind, "count": self._play_unsent_runs})
        if self._play_unsent_runs >= mpl.MAX_UNSENT_TAPS:
            if play.phase == "navigate":
                play._end("tap_not_sent")
            elif play.phase == "returning":
                play._fail("play_return_failed", "tap_not_sent")
                self._play_ended(run, play)

    def play_decide(self, context, image):
        digest = tc.image_digest(image)
        if self._play_cache[0] == digest:
            return self._play_cache[1]
        run, play = self.run, self.play
        if self._play_unsent is not None:
            # The decided tap's node did not run on its frame and Wait was not reached either: undo it now.
            self._play_rollback()
            if run.phase != "play":
                return mpl.Decision("none", "play_ended")
        state = play.snapshot()
        decision = play.step(mpl.Reader(self.vision(context)), image)
        self._play_cache = (digest, decision)
        if decision.kind != "idle":
            run.note({"play": play.phase, "decision": decision.kind, "reason": decision.reason})
        if decision.kind in mpl.CLICK_NODES:
            self._play_unsent = (decision.kind, state)
        if play.phase == "ended":
            self._play_ended(run, play)
        return decision

    def _play_ended(self, run, play):
        run.play = play.summary()
        if play.outcome is not None:
            run.finish(play.outcome, play.reason)
            return
        run.phase = "active"
        run.notes.append(f"played_{len(play.lives)}:{play.reason}")
        if run.config.count_mode == "fixed" and play.consumed:
            left = run.config.count - play.consumed
            if left <= 0:
                run.finish("played", f"count_{run.config.count}_used_by_lives")
                return
            run.config = run.planner.config = dataclasses.replace(run.config, count=left)

    def play_failed(self, context, p):
        """on_error of a non-consuming play tap (refused or failed): the play part ends; nothing is tapped again."""
        run, play = self.run, self.play
        if run is None or play is None or run.phase != "play":
            return False
        if play.phase == "returning":
            play._fail("play_return_failed", "tap_failed")
            self._play_ended(run, play)
        else:
            play._end("tap_failed")
        self._play_cache, self._play_unsent = (None, None), None
        return True

    def play_spend_error(self, context, p):
        """on_error of ライブ開始: a click denied before sending ends the play part; anything else is uncertain."""
        run, play = self.run, self.play
        if run is None or play is None or play.phase != "live" or not play.lives:
            return False
        outcome = self.authority.last_outcome(mpl.SPEND_ACTION) if self.authority is not None else None
        record = play.lives[-1]
        self._play_cache, self._play_unsent = (None, None), None
        if outcome == "denied":
            play.lives.pop()
            play.verify, play.live, play.phase = None, None, "navigate"
            self._log({"event": "play_refused", **record, "reason": "click_denied_before_send"})
            play._end("spend_refused")
            return True
        self._log({"event": "play_uncertain", **record, "reason": "result_not_observed:" + str(outcome)})
        run.play = play.summary()
        run.finish("spend_uncertain", "play_click_" + str(outcome))
        self.report()
        return False

    # -- reading
    def read_dialog(self, context, image):
        key = tc.image_digest(image)
        if key in self._cache:
            return self._cache[key]
        if isinstance(self.run.planner, ObservedSkipPlanner):
            page = ms.read_skip_page(self.vision(context), image, self.clock())
            self._observed_page = page
            view = mt.DialogView(tuple(mt.Slot(index, True, row.stage, row.count, row.plus_enabled)
                                      for index, row in enumerate(page.rows, 1)), page.cost, page.thumb) if page else mt.DialogView(())
            self._cache = {key: view}
            return view
        vision = self.vision(context)
        slots = []
        for slot in mt.SLOTS:
            if not header_bar_present(image, slot):
                slots.append(mt.Slot(slot, False))
                continue
            # White header text on the blue bar: fixed crop + only_rec scores ~0.997 (det+rec only ~0.948).
            stage = mt.parse_header(vision.number(image, mt.SLOT_HEADER[slot], "light", mt.HEADER_PATTERN.pattern))
            count = tc.to_int(vision.number(image, mt.SLOT_COUNT[slot], "light", r"[0-9]{1,2}", unanimous=True))
            slots.append(mt.Slot(slot, True, stage, count, plus_enabled(image, slot)))
        # The large spaced "0 / 20": det+rec on the raw box scores ~0.994 (only_rec on the processed crop ~0.90).
        cost = ms.read_cost(vision, image)
        view = mt.DialogView(tuple(slots), cost, thumb_span(image))
        self._cache = {key: view}
        return view

    def decision(self, context, image):
        """Share one decision across node probes; Changed/Hold discard it before the next observation."""
        run = self.run
        key = ("decision", tc.image_digest(image))
        if getattr(run, "_decision_key", None) == key:
            return run._decision
        view = self.read_dialog(context, image)
        settle = getattr(run, "settle", None)
        state = settle.observe(image) if settle is not None else "settled"
        if state == "settled":
            run.settle = None
        if state == "moving":
            decision = mt.Decision("wait", notes=("scroll_settling",))
        elif state == "exhausted":
            decision = mt.Decision("abort", outcome="scroll_not_settled")
        elif run.rights is None:
            decision = mt.Decision("abort", outcome="rights_unreadable", notes=("main_rights_not_observed",))
        elif view.cost is not None and view.cost[1] != run.rights[0]:
            decision = mt.Decision("abort", outcome="rights_changed_or_insufficient",
                                   notes=(f"main_rights_{run.rights}", f"dialog_available_{view.cost[1]}"))
        elif failure := self._progress_failure(context, image):
            decision = mt.Decision("abort", outcome=failure)
        else:
            decision = (run.planner.step(self._observed_page) if isinstance(run.planner, ObservedSkipPlanner)
                        else run.planner.step(view))
        run.note({"dialog": {"stages": [list(s) if s else None for s in view.stages()],
                             "counts": [s.count for s in view.slots if s.present], "cost": view.cost,
                             "thumb": view.thumb}, "decision": decision.to_dict()})
        if decision.kind == "abort":
            run.phase, run.abort_outcome = "aborting", decision.outcome
            run.notes.extend(decision.notes)
        if decision.kind in ("scroll_down", "scroll_up"):
            run.settle = ListSettle()
        run._decision_key, run._decision = key, decision
        return decision

    def _progress_failure(self, context, image):
        run = self.run
        if isinstance(run.planner, ObservedSkipPlanner):
            # No calendar inference: every preparation/confirmation is on a
            # freshly recognized dialog, and step checks the scanned rows/rights.
            return None
        if run.progress != self.progress or run.planner.progress != run.progress:
            return "progress_changed"
        try:
            observation = self.progress_reader(context, image) if self.progress_reader is not None else None
            failure = mp.progress_failure(run.progress, observation, tc.image_digest(image), self.clock())
        except Exception:
            return "progress_unreadable"
        if not failure:
            run.progress_proof = observation
        return failure

    def advance(self, context, p):
        """A guarded dialog action or Hold has completed; identical next pixels must still be re-evaluated."""
        run = self.run
        if run is not None and self.scan_sample and run.config.sample_only and run.phase == "skip_opened":
            return True  # the already guarded sample wheel finished; next frame must prove movement
        if run is None or run.phase not in ("planning", "aborting"):
            return False
        run._decision_key, run._decision = None, None
        return True

    # -- recognitions
    def extra_state(self, want, context, image, p):
        run = self.run
        if want == "pending_receipt":
            if run.phase != "awaiting_result" or not run.intent or not self.vision(context).hit("SC.Match.Result.Guard", image):
                return False
            run.receipt_seen = True
            return True
        if want == "observed":
            return run.finished and run.outcome == "skipped"
        if want == "waiting":
            # A planner may reject this frame after Cancel was already probed.
            # Re-enter Main on the same guarded dialog so the task-owned Cancel records that outcome.
            if run.phase == "aborting":
                return True
            return run.phase == "planning" and (self.decision(context, image).kind == "wait" or run.phase == "aborting")
        if want == "need_clear":
            if run.phase != "aborting" or run.cleared:
                return False
            view = self.read_dialog(context, image)
            if view.cost is not None and view.cost[0] == 0:
                return False
            run.cleared = True  # one クリア at most; キャンセル discards any remaining selection anyway
            return True
        if want == "cancel_skip":
            if run.phase == "awaiting_result" and run.receipt_seen:
                return True  # Close only the non-consuming list after the known receipt.
            if run.phase == "sampled":
                run.finish("sampled", f"{run.samples} frames", *(mt.plan_notes(run.config, run.rights) if run.rights else ()))
                return True
            if run.phase == "aborting":
                run.finish(run.abort_outcome or "counter_mismatch", "dialog_cancelled")
                return True
            # A skip dialog this run did not open (left over): キャンセル is non-consuming, close it and go on.
            return run.phase in ("active", "finished", "play") and not run.halted
        if want == "cancel_review":
            # 「加上自动点cancel」: スキップ確認 this run rejected (aborting), or one left over from an
            # earlier run; never this run's own pending review or a started skip.
            return run.phase == "aborting" or (run.phase in ("active", "finished", "play") and not run.halted)
        return False

    def plan(self, context, image, p):
        run, step = self.run, p.get("step")
        if run is None or run.finished:
            return False
        if step == "open_skip":
            return self._open_skip(context, image)
        if self.scan_sample and run.config.sample_only and run.phase == "skip_opened":
            return step == "scroll_down" and self._sample_scroll(context, image)
        if run.phase == "skip_opened" and not run.config.sample_only:
            run.phase = "planning"
        if run.phase != "planning":
            return False
        decision = self.decision(context, image)
        if step == "plus":
            if decision.kind != "plus" or decision.slot != p.get("slot"):
                return False
            return True
        if step in ("scroll_down", "scroll_up"):
            return decision.kind == step
        return False

    def _open_skip(self, context, image):
        run = self.run
        if run.phase != "active":
            return False
        navigation = getattr(self, "latest_navigation", None)
        if navigation is not None and navigation.phase != "ready":
            return False
        vision = self.vision(context)
        text = vision.number(image, mt.RIGHTS_FIELD, "light", mt.RIGHTS_PATTERN)
        rights = tc.ratio(text)
        run.note({"stage": "MatchMain", "rights": text})
        observation = mr.read_main(vision, image, self.clock(), rights_text=text)
        if observation is not None:
            run.note({"main_observation": observation.to_dict()})
            self.sample_main = observation
        if rights is None or rights[1] != mt.RIGHTS_MAXIMUM:
            run.unreadable += 1
            if run.unreadable > mt.MAX_UNREADABLE_FRAMES:
                run.finish("rights_unreadable")
            return False
        run.rights = rights
        self._log({"event": "rights_observed", "rights": list(rights), "config": run.config.to_dict()})
        if not run.config.sample_only:
            if not self.can_spend():
                run.finish("skip_not_enabled", "needs_--allow-match-spend", *mt.plan_notes(run.config, rights))
                return False
            resolved = run.config.resolved(rights)
            if resolved is None:  # 「全部用完」 with nothing left after the reserve: a normal no-op
                played = self.play is not None and self.play.lives
                run.finish("played" if played else "nothing_to_do", "all_mode_no_rights_left",
                           *mt.plan_notes(run.config, rights))
                return False
            if resolved is not run.config:
                run.note({"count_mode": "all", "resolved_count": resolved.count, "rights": list(rights)})
                run.config = run.planner.config = resolved
            if rights[0] - run.config.rights_reserve < run.config.count:
                run.finish("rights_insufficient", *mt.plan_notes(run.config, rights))
                return False
            if failure := self._progress_failure(context, image):
                run.finish(failure, "latest_open_stage_not_observed" if run.config.explicit() is None
                           else "independent_current_cycle_proof_required")
                return False
        run.phase = "skip_opened"
        run.saver = tc.FrameSaver("match-skip-dialog", self.sample_root, self.now,
                                 mt.MAX_SCROLLS + 2 if self.scan_sample else mt.SAMPLE_FRAMES + 2)
        return True

    def latest_plan(self, context, image, p):
        """Read a new native frame; ordinary navigation input belongs to Pipeline."""
        run = self.run
        navigation = getattr(self, "latest_navigation", None)
        if run is None or run.finished or run.phase != "active" or navigation is None:
            return False
        if navigation.phase == "ready":
            return False
        key = tc.image_digest(image)
        if self._latest_decision is None or self._latest_decision[0] != key:
            vision, stamp = self.vision(context), self.clock()
            if navigation.phase == "areas":
                # Off the area page (home, menus): no hold and no unreadable-wait count, so Pipeline navigates there.
                decision = (navigation.area_step(ml.read_area_page(vision, image, stamp))
                            if vision.hit(ml.AREA_GUARD, image) else "away")
            elif navigation.phase == "stages":
                # Do not reclassify the still-visible area page as a missing stage page.
                if vision.hit(ml.AREA_GUARD, image):
                    decision = "hold"
                else:
                    decision = navigation.stage_step(ml.read_stage_boundary(
                        vision, image, stamp, navigation.areas[-1]))
            elif navigation.phase == "return":
                if vision.hit(ml.STAGE_GUARD, image):
                    decision = "hold"
                else:
                    decision = navigation.returned(ml.read_area_page(vision, image, stamp))
            else:
                decision = "failed"
            self._latest_decision = (key, decision)
            if decision != "away":
                run.note({"latest_navigation": navigation.evidence(), "decision": decision})
            if decision == "failed":
                run.finish(navigation.reason or "latest_progress_unreadable", "no_older_stage_fallback")
                return False
            if decision == "ready":
                run.planner = ml.LatestSkipPlanner(run.config, navigation)
                self._log({"event": "latest_open_observed", **navigation.evidence()})
                return False
        return self._latest_decision[1] == p.get("step")

    def latest_advance(self, context, p):
        self._latest_decision = None
        return True

    def latest_error(self, context, p):
        if self.run is not None:
            self.run.finish("latest_navigation_failed", "non_consuming_navigation_not_retried")
        return True

    def _sample_scroll(self, context, image):
        """Survey only: zero selections, bounded wheel, overlap and resource checks; never prepares a spend."""
        run = self.run
        if self.sample_pending_thumb is not None:   # a wheel was sent: read only a settled list (ListSettle)
            settle = self.sample_settle = self.sample_settle or ListSettle()
            state = settle.observe(image)
            if state == "moving":
                return False
            self.sample_settle = None
            if state == "exhausted":
                run.abort_outcome = "scroll_not_settled"
                self.sample_scan_ready = True
                return False
        page = ms.read_skip_page(self.vision(context), image, self.clock())
        if page is not None:
            run.note({"skip_page_observation": page.to_dict()})
            run.saver.save(image)  # retain the post-wheel frame even when coverage/resource checks refuse it
        if page is not None and page.readable and (
                page.cost != (0, run.rights[0]) or any(row.count != 0 for row in page.rows)):
            run.abort_outcome = "rights_changed_or_insufficient" if page.cost[1] != run.rights[0] else "counts_not_zero"
            self.sample_scan.reason = run.abort_outcome
            self.sample_scan_ready = True
            return False
        if page is not None and page.readable and self.sample_pending_thumb == page.thumb:
            self.sample_waits += 1
            if self.sample_waits > mt.MAX_UNREADABLE_FRAMES:
                run.abort_outcome = "scroll_no_effect"
                self.sample_scan_ready = True
            return False
        if not self.sample_scan.add(page):
            run.abort_outcome = "dialog_unreadable" if self.sample_scan.reason == "dialog_unreadable" else "stage_evidence_incomplete"
            self.sample_scan_ready = True
            return False
        self.sample_pending_thumb, self.sample_waits = None, 0
        run.samples += 1
        if self.sample_scan.complete:
            self.sample_scan_ready = True
            return False
        if self.sample_scrolls >= mt.MAX_SCROLLS:
            run.abort_outcome = "list_end_not_reached"
            self.sample_scan_ready = True
            return False
        self.sample_scrolls += 1
        self.sample_pending_thumb = page.thumb
        return True

    def sample(self, context, image, p):
        run = self.run
        if run is None or run.phase != "skip_opened" or not run.config.sample_only:
            return False
        if self.scan_sample:
            if self.sample_scan_ready:
                record = {"event": "sampled_list", "frames": list(run.saver.saved),
                          "skip_list": self.sample_scan.to_dict(), "scrolls": self.sample_scrolls,
                          "progress_candidate": progress_candidate(self.sample_scan, self.sample_main)}
                self._log(record)
                run.note({"skip_list_observation": record["skip_list"]})
                run.note({"progress_candidate": record["progress_candidate"]})
                run.phase = "aborting" if run.abort_outcome else "sampled"
            return True  # wait/reobserve or proceed to the existing guarded cancel; no selection or confirmation
        try:
            run.saver.save(image)
        except Exception:
            pass
        run.samples += 1
        vision = self.vision(context)
        run.dialog_hits += vision.hit(DIALOG_GUARD, image)
        observation = ms.read_skip_page(vision, image, self.clock())
        if observation is not None:
            run.note({"skip_page_observation": observation.to_dict()})
        if run.samples < mt.SAMPLE_FRAMES:
            return True  # keep sampling (the node waits, then the Main loop takes another frame)
        self._log({"event": "sampled", "frames": list(run.saver.saved), "dialog_hits": run.dialog_hits})
        if run.dialog_hits:
            run.phase = "sampled"  # the next Main round presses キャンセル (Skip.Cancel precedes the shared BackOne)
        else:
            run.finish("sample_dialog_unrecognised", f"frames in {run.saver.directory}", "dialog_left_open_no_click")
        return True

    def check(self, context, image, p):
        run = self.run
        if run is None:
            return False
        if p.get("stage") == "start":
            return self._check_start(context, image)
        if p.get("stage") != "confirm":
            return False
        if run.phase == "skip_opened" and not run.config.sample_only:
            run.phase = "planning"  # Confirm is before Clear/Cancel, so first-frame rejection can clean up this round
        if run.phase != "planning":
            return False
        decision = self.decision(context, image)
        if decision.kind != "confirm":
            return False
        view = self.read_dialog(context, image)
        planner = run.planner
        slot = next(s for s in view.slots if s.slot == decision.slot)
        reasons = []
        if not (slot.count == planner.pressed == run.config.count and view.cost[0] == run.config.count):
            reasons.append("counter_not_count")
        # A changed available count invalidates the main-page resource proof; cancel and re-observe in a later run.
        if (view.cost[1] - run.config.rights_reserve < run.config.count
                or view.cost[1] != run.rights[0]):
            reasons.append("rights_changed_or_insufficient")
        if not self.can_spend():
            reasons.append("spend_not_enabled")
        if failure := self._progress_failure(context, image):
            run.phase, run.abort_outcome = "aborting", failure
            run.notes.append("progress_invalid_before_confirm")
            return False
        if reasons:
            run.phase, run.abort_outcome = "aborting", "counter_mismatch"
            run.notes.extend(reasons)
            return False
        observed = isinstance(planner, ObservedSkipPlanner)
        intent = {"stage": f"A{slot.stage[0]}-S{slot.stage[1]}", "count": run.config.count, "rights_before": list(view.cost),
                  "target_rule": run.config.stage,
                  "progress": planner.evidence() if observed else run.progress.to_dict(),
                  "progress_frame_digest": tc.image_digest(image) if observed else run.progress_proof.frame_digest}
        if not self._log({"event": "prepared", **intent}):
            run.phase, run.abort_outcome = "aborting", "log_write_failed"
            return False
        run.prepared, run.phase = intent, "review"
        return True

    def read_review(self, context, image):
        vision = self.vision(context)
        if not vision.hit(mt.REVIEW_GUARD, image):
            return None
        # The central review lists selected stages only. A second colored row means multiple selections.
        if len(ms._header_spans(image, 600)) != 1:
            return None
        stage = mt.parse_header(vision.number(image, mt.REVIEW_HEADER, "light", mt.HEADER_PATTERN.pattern))
        x, y, width, height = mt.REVIEW_COUNT
        count = tc.to_int(vision.rec(image[y:y + height, x:x + width], r"[0-9]{1,2}"))
        cost = ms.read_cost(vision, image, mt.REVIEW_COST)
        return stage, count, cost

    def _check_start(self, context, image):
        run = self.run
        if run.phase != "review" or run.prepared is None:
            return False
        review = self.read_review(context, image)
        if review is None:
            return False
        stage, count, cost = review
        prepared = run.prepared
        if (stage is None or f"A{stage[0]}-S{stage[1]}" != prepared["stage"]
                or count != run.config.count or cost != tuple(prepared["rights_before"])
                or cost[0] != count or cost[1] != run.rights[0]
                or cost[1] - run.config.rights_reserve < count or not self.can_spend()):
            run.phase, run.abort_outcome = "aborting", "counter_mismatch"
            run.notes.append("final_review_changed")
            return False
        intent = {**prepared, "time": self.now().isoformat(), "review_frame_digest": tc.image_digest(image)}
        if not self._log({"event": "intent", **intent}):
            run.phase, run.abort_outcome = "aborting", "log_write_failed"
            return False
        refusal = self.authorize(image, mt.APPROVER)
        if refusal:
            self._log({"event": "refused", **intent, "reason": refusal})
            run.phase, run.abort_outcome = "aborting", "spend_refused"
            run.notes.append("spend_grant_refused:" + str(refusal))
            return False
        run.intent, run.phase = intent, "awaiting_result"
        run.saver = tc.FrameSaver("match-after-skip", self.sample_root, self.now, mt.WATCH_FRAME_LIMIT)
        return True

    def watch(self, context, image, p):
        """Read-only after スキップする: save frames; Match main or the dialog showing rights -count concludes."""
        run = self.run
        if run is None or run.phase != "awaiting_result":
            return False
        now = self.clock()
        if run.last_save is None or now - run.last_save >= 1.5:
            try:
                run.saver.save(image)
            except Exception:
                pass
            run.last_save = now
        vision, before = self.vision(context), run.intent["rights_before"][1]
        expected = before - run.config.count
        if vision.hit("SC.Nav.MatchMain.Guard", image):
            rights = tc.ratio(vision.number(image, mt.RIGHTS_FIELD, "light", mt.RIGHTS_PATTERN))
            if rights is not None and rights[0] == expected and rights[1] == run.rights[1]:
                self._log({"event": "verified", **run.intent, "intent_time": run.intent.get("time"),
                           "rights_after": list(rights), "frames": run.saver.saved[-3:]})
                run.rights = rights
                run.note({"stage": "AfterSkip", "rights_after": list(rights)})
                run.finish("skipped", f"rights {before}->{rights[0]}")
                return True
        return False

    def mark_abort(self, context, p):
        """on_error of a non-consuming dialog click (refused/failed): clear and cancel instead of leaving counts."""
        run = self.run
        if run is not None and run.phase == "planning":
            run.phase, run.abort_outcome = "aborting", "dialog_click_failed"
            return True
        return False

    def spend_error(self, context, p):
        run = self.run
        if run is not None and run.saver is not None:
            run.notes.append(f"sampled_frames {len(run.saver.saved)} in {run.saver.directory}")
        return super().spend_error(context, p)

    def abort(self, context, p):
        run = self.run
        play = self.play
        if run is not None and run.phase == "play" and play is not None and play.phase == "live" and play.lives:
            self._log({"event": "play_uncertain", **play.lives[-1], "reason": "unknown_or_unhandled_screen"})
            run.play = play.summary()
            run.finish("spend_uncertain", "play_live_unresolved")
        elif run is not None and run.phase == "play":
            run.play = play.summary() if play is not None else None
            run.finish("timeout", "play_" + (play.phase if play is not None else "unknown"))
        if run is not None and run.phase == "skip_opened" and run.samples == 0:
            run.finish("skip_open_no_effect")
        elif run is not None and run.phase == "aborting":
            run.finish(run.abort_outcome or "counter_mismatch", "dialog_left_open")
        return super().abort(context, p)


class MatchPlanRecognition(task_custom.RuntimeRecognition):
    """Return the observed + glyph as the Pipeline Click's native recognition target."""
    def analyze(self, context, argv):
        result = super().analyze(context, argv)
        params = tc.params(argv.custom_recognition_param)
        if result is None or params.get("step") != "plus":
            return result
        slot = params["slot"]
        planner = self.runtime.run.planner
        if isinstance(planner, ObservedSkipPlanner) and planner.last == "hold":
            from agent.core.match_observed import HOLD_MS
            stage = getattr(self.runtime.authority, "stage_hold", None)
            refusal = stage(f"match-skip-plus-{slot}", HOLD_MS) if callable(stage) else "hold_host_unavailable"
            self.runtime.run.note({"stage": "PlusHold", "slot": slot, "hold_ms": HOLD_MS, "refusal": refusal})
            if refusal:
                planner.hold_unavailable()
        box = (planner.current_page.rows[slot - 1].plus_box if isinstance(planner, ObservedSkipPlanner)
               else mt.SLOT_PLUS[slot])
        return task_custom.CustomRecognition.AnalyzeResult(box=tuple(box), detail=result.detail)


RUNTIME = MatchRuntime()
RECOGNITIONS = {"State": "state", "Plan": "plan", "Sample": "sample", "Check": "check", "Watch": "watch",
                "Latest.Plan": "latest_plan", "Play.Check": "play_check"}
ACTIONS = {"Begin": "begin", "Finish": "finish_action", "Abort": "abort", "SpendError": "spend_error",
           "MarkAbort": "mark_abort", "Advance": "advance", "Latest.Advance": "latest_advance",
           "Latest.Error": "latest_error", "Play.Failed": "play_failed", "Play.SpendError": "play_spend_error"}


def match_registrations(runtime):
    recognitions, actions = task_custom.registrations(runtime, PREFIX, RECOGNITIONS, ACTIONS)
    recognitions[PREFIX + ".Plan"] = MatchPlanRecognition(runtime, "plan")
    return recognitions, actions
