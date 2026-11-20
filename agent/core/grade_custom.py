"""Maa custom recognitions/actions of the GradeFes task (B5/B5c). Rules live in ``agent.core.grade_task``.

B5c: the 参加ユニット primary button is pressed twice through the one table action ``grade-tension-confirm``:
テンション決定 (``Check.Unit``; irreversible, non-consuming, logged as ``tension_intent``) and, once a fresh frame shows
the TensionDecided stage with unchanged rights, ライブ開始 (``Check.LiveStart``; consumes 1, logged as ``intent``).
Each press gets its own one-shot grant bound to the analysed frame.

B5h: 「フェス再開」 (a live in progress after a host restart; its right already spent) is met at Fes main or anywhere
before this run's テンション決定: ``Check.Resume`` reads the rights under the dialog, logs ``resume`` and the Pipeline
presses 「再開」 (``grade-fes-resume``, non-consuming) once; the live loop then plays the resumed live. 「終了」 is never
pressed. Without a live screen within 15 s the run stops as ``live_unfinished``.

B5i: a live turn screen already shown when the run begins (no dialog) is taken over without a click
(``Check.Takeover``, log ``live_takeover``) and played on from its current turn.

B5j: LIVE REPORT already shown when the run begins (an earlier OK had no effect) is taken over the same way
(``Check.ReportTakeover``, log ``report_takeover``): only OK (``live-report-ok``) and the way back to Fes main.
B5k: the second LIVE REPORT page (ranking list) shown when the run begins is taken over likewise (``Check.RankingTakeover``,
log ``ranking_takeover``): only its OK (same action) and the way back to Fes main.
"""
from __future__ import annotations

import time
from pathlib import Path

from agent.core import fes_allocation as fa
from agent.core import grade_task as gt
from agent.core.grade_live import LiveMixin
from agent.core import task_common as tc
from agent.core import task_custom

PREFIX = "SC.Grade"
CONFIG_NODES = ("SC.Grade.Config.Difficulty", "SC.Grade.Config.Runs", "SC.Grade.Config.Reserve",
                "SC.Grade.Config.Fixed", "SC.Grade.Config.Samples", "SC.Grade.Config.Live",
                "SC.Grade.Config.Speed", "SC.Grade.Config.Rehearsal", "SC.Grade.Config.Recovery", "SC.Grade.Config.RecoveryCap",
                "SC.Grade.Config.Timing")
TENSION_GUARD = "SC.Grade.Tension.Guard"
BACK_UNDIMMED = "SC.Common.Color.BackUndimmed"


class GradeRuntime(LiveMixin, task_custom.TaskRuntime):
    KIND = "grade"
    RUN = gt.GradeRun
    CONFIG = gt.GradeConfig
    @property
    def SPEND_ACTION(self):
        config = self.run.config if self.run is not None else None
        return config.spend_action if config is not None else gt.SPEND_ACTION

    def __init__(self, *args, clock=time.monotonic, live_decide=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clock = clock
        # Live turn policy (``fes_live_policy.decide`` by default); offline replays of a manual live may substitute the
        # user's recorded choices so the rest of the flow (guards, targets from predictions, timing, OK) stays real.
        self.live_decide = live_decide

    def state_now(self):
        mode = self.run.config.mode if self.run is not None else "grade"
        # Historical entries were all formal Grade. A paused formal live must not
        # become a rehearsal attempt or use the rehearsal daily allowance.
        return gt.log_state(event for event in self.log.events() if event.get("mode", "grade") == mode)

    def _log(self, record):
        mode = self.run.config.mode if self.run is not None else "grade"
        return super()._log({**record, "mode": mode})

    def started(self, run):
        if run.config.mode == "grade" and run.config.rehearsal_runs > 0:
            run.finish("config_invalid", fa.MESSAGES["mixed_mode_not_supported"])
            return
        if run.config.bulk_count > 1:  # 2026-10-09: まとめて消費 2-5 is not sampled; stop before any click
            run.finish("bulk_not_sampled", fa.MESSAGES["bulk_not_sampled"])
            return
        refusal = fa.recovery_refusal(run.config.use_recovery_tickets, run.config.recovery_cap, fa.load_policy())
        if refusal:
            run.finish(refusal, fa.MESSAGES[refusal])
            return
        state = self.state_now()
        # A later run of the same day plays its count again; shifted from the configured count (idempotent).
        run.base_config = getattr(run, "base_config", None) or run.config
        run.config = gt.per_run_config(run.base_config, state)
        run.daily_start = run.daily_progress = gt.progress_from_state(run.config, state)
        kind = gt.resume_kind(state)
        if kind is not None:
            raw = state.open.get("rights_before")
            before = tc.ratio(f"{raw[0]}/{raw[1]}") if type(raw) is list and len(raw) == 2 else None
            run.resume = {"kind": kind, "rights_before": before, "difficulty": state.open.get("difficulty")}
            run.note({"resume": kind, "attempts_today": state.attempts,
                      "rights_before": list(before) if before else None})
            return
        # A live whose LIVE REPORT was seen but that never reached Fes main may still show its report / ranking page:
        # leave that to the screen (takeover, or 「フェスに参加する」 refused with runs_done_today on Fes main).
        played = state.verified if (state.open or {}).get("reported") else state.played
        report_pending = (state.open or {}).get("reported")
        live_pending = bool((state.open or {}).get("spent"))
        done = (played >= run.config.runs_per_day or
                state.attempts >= run.config.attempt_limit and not (report_pending or live_pending))
        if done:
            run.finish("nothing_to_do", f"runs_today_{state.attempts}_verified_{state.verified}"
                       + (f"_reported_{state.reported}" if state.reported else "")
                       + ("_with_uncertain" if state.uncertain else ""))

    def rights(self, context, image, where):
        text = self.vision(context).number(image, gt.RIGHTS_FIELDS[where], "light", gt.RIGHTS_PATTERN)
        return text, tc.ratio(text)

    def back_disabled(self, context, image):
        """TensionDecided: the back key is greyed out (0 of the 2322 white pixels it has on 参加ユニット)."""
        return not self.vision(context).hit(BACK_UNDIMMED, image)

    def unit_texts(self, context, image):
        vision = self.vision(context)
        title = vision.text(image, gt.TEXT_BOXES["unit_title"])
        notice = vision.text(image, gt.TEXT_BOXES["unit_notice"])
        text, rights = self.rights(context, image, "unit")
        return title, notice, text, rights

    def _unreadable(self, run, where):
        run.unreadable += 1
        if run.unreadable > gt.MAX_UNREADABLE_FRAMES:
            run.finish("rights_unreadable", where)
        return False

    def _retry(self, run, *values):
        """Pop-in/animation: read another frame while something is unreadable (bounded)."""
        if run.unreadable < gt.MAX_UNREADABLE_FRAMES and any(v is None for v in values):
            run.unreadable += 1
            return True
        return False

    def _start_watch(self, run, name, limit):
        run.watch_started = run.last_change = self.clock()
        run.saver = tc.FrameSaver(name, self.sample_root, self.now, limit)
        run.last_save = run.saved_sig = run.ref_sig = None
        run.skipped_duplicates = 0

    @staticmethod
    def _signature(image):
        try:
            return image[::8, ::8].astype("float32").mean(axis=2)
        except Exception:
            return None

    @staticmethod
    def _changed(a, b):
        return a is None or b is None or a.shape != b.shape or float(abs(a - b).mean()) >= gt.CHANGE_THRESHOLD

    def _save(self, run, image):
        """Track screen changes and save a frame every >=1.5 s when it differs from the last saved one."""
        now = self.clock()
        sig = self._signature(image)
        if self._changed(sig, run.ref_sig):
            run.ref_sig, run.last_change = sig, now
        if run.last_save is None or now - run.last_save >= gt.SAVE_INTERVAL_SECONDS:
            run.last_save = now
            if not self._changed(sig, run.saved_sig):
                run.skipped_duplicates += 1
                return now
            try:
                path = run.saver.save(image)
                if path is not None:
                    run.saved_sig = sig
                    if str(run.saver.directory) not in run.samples:
                        run.samples.append(str(run.saver.directory))
            except Exception:  # sampling is evidence only; never blocks the conclusion
                pass
        return now

    def _remember(self, image):
        """Keep the last few distinct frames (in memory) so a run that stops leaves evidence of where it stopped."""
        run = self.run
        if run is None or image is None or run.reported:
            return
        try:
            if run.recent and run.recent[-1][1] is image:
                return
            sig = self._signature(image)
            if sig is None or (run.recent and not self._changed(sig, run.recent[-1][0])):
                return
            run.recent.append((sig, image.copy()))
            del run.recent[:-gt.STOP_SAMPLE_FRAMES]
        except Exception:  # evidence only
            pass

    def _remember_cached(self, context):
        """Actions get no frame: take the controller's last capture (the page the run stops on) as evidence."""
        try:
            self._remember(context.tasker.controller.cached_image)
        except Exception:
            pass

    def _save_stop_samples(self, run):
        if run.succeeded or not run.recent:
            return
        try:
            saver = tc.FrameSaver("grade-stopped", self.sample_root, self.now, gt.STOP_SAMPLE_FRAMES)
            for _, frame in run.recent:
                saver.save(frame)
            if saver.directory is not None and str(saver.directory) not in run.samples:
                run.samples.append(str(saver.directory))
        except Exception:  # sampling never changes the outcome
            pass
        run.recent = []

    def _flow_hold(self, run, image):
        """SC.Grade.Flow.Hold: inside Grade's dialogs (or a Fes-main rights retry) no Grade node accepted this frame.
        Wait for another frame instead of the shared BackOne; after FLOW_HOLD_FRAMES stop without a click."""
        if run.finished:
            return False
        if not (run.phase in gt.FLOW_PHASES or (run.phase == "active" and run.unreadable > 0)):
            return False
        if run.hold_phase != run.phase:
            run.hold_phase, run.hold_frames = run.phase, 0
            run.hold_saver = tc.FrameSaver("grade-unrecognized", self.sample_root, self.now, gt.FLOW_HOLD_SAMPLE_LIMIT)
        run.hold_frames += 1
        try:
            if image is not None and run.hold_saver.save(image) is not None:
                if str(run.hold_saver.directory) not in run.samples:
                    run.samples.append(str(run.hold_saver.directory))
        except Exception:  # evidence only
            pass
        if run.hold_frames > gt.FLOW_HOLD_FRAMES:
            run.finish("page_unrecognized", "grade_flow_left_at_" + str(run.phase),
                       f"unrecognized_frames {run.hold_frames - 1}")
        return True

    # -- recognitions
    def state(self, context, image, p):
        run = self.run
        self._remember(image)
        if (p.get("is") == "active" and run is not None and run.phase == "active" and not run.finished
                and run.resume is not None):
            # A resumable attempt, but this (Home / Fes popup) frame is not TensionDecided. (不确定只提醒):
            # the attempt is concluded as left and this run starts from the screen (フェス再開 etc. stay screen-guarded).
            if not self._log({"event": "observed_home", "reason": "open_attempt_left:" + run.resume["kind"]}):
                run.finish("log_write_failed", "open_attempt_not_concluded")
                return False
            run.notes.append("open_attempt_left:" + run.resume["kind"])
            run.resume = None
            state = self.state_now()
            run.config = gt.per_run_config(run.base_config, state)
            run.daily_start = run.daily_progress = gt.progress_from_state(run.config, state)
        if p.get("is") == "active":
            # Home / Fes popup entry only before this run tapped anything of the Grade flow: a run that already
            # pressed 「フェスに参加する」 and lands on Home / the Fes popup again never re-enters (Flow.Hold stops it).
            return run is not None and run.phase == "active"
        return super().state(context, image, p)

    def extra_state(self, want, context, image, p):
        run = self.run
        if want == "observed":  # concluded after ライブ開始: end here, never navigate (no click after a spend)
            return run.finished and run.outcome == "played"
        if want == "flow_hold":
            return self._flow_hold(run, image)
        if want == "resume_wait":  # フェス再開 with the rights under it not yet readable: read another frame (bounded)
            return not run.finished and not run.confirm_rejected and run.phase in gt.RESUME_PHASES
        if want == "unit_wait":  # a transient OCR miss on 参加ユニット: stay here for the existing bounded retry
            return (not run.finished and not run.confirm_rejected and run.phase == "difficulty_tapped"
                    and run.unreadable > 0)
        if want == "resume_hold":  # フェス再開 that this run may not resume: stop, never click (never 終了)
            if not run.finished:
                run.finish("fes_resume_pending", *(run.confirm_rejected or ["fes_resume_not_resumable"]))
            else:
                run.notes.append("fes_resume_dialog_left_open")
            return True
        if want == "tension_hold":  # TensionDecided on screen but ライブ開始 not pressed: stop, never click back
            if run.finished:
                return True
            if run.phase == "tension_decided":
                if not self.can_spend():
                    run.finish("spend_not_enabled", "tension_decided_waiting_for_live_start")
                    return True
                if run.confirm_rejected:
                    outcome = ("spend_refused" if any(reason.startswith("spend_grant_refused:")
                               for reason in run.confirm_rejected) else "unit_mismatch")
                    run.finish(outcome, *run.confirm_rejected)
                    return True
                return False
            if run.phase == "active" and run.resume is not None:
                return False  # Check.TensionObserve decides (and finishes on a mismatch)
            if run.phase in ("tension_tapped", "awaiting_result"):
                return False
            run.finish("unexpected_tension_decided", "phase_" + str(run.phase))
            return True
        if want == "unit_cancel":
            if run.phase in ("awaiting_result", "tension_tapped", "tension_decided"):
                return False
            if run.finished:
                return True
            reasons = run.confirm_rejected or (["unexpected_unit"] if run.phase != "difficulty_tapped" else
                                               ["spend_not_enabled"] if not self.can_spend() else None)
            if not reasons:
                return False
            outcome = ("unexpected_unit" if reasons == ["unexpected_unit"] else
                       "spend_not_enabled" if reasons == ["spend_not_enabled"] else
                       "attempt_unresolved" if reasons == ["attempt_unresolved"] else "unit_mismatch")
            run.finish(outcome, *reasons)
            return True
        return False

    def plan(self, context, image, p):
        run, step = self.run, p.get("step")
        self._remember(image)
        if run is None or run.finished:
            return False
        if step == "join" and run.phase == "active":
            text, rights = self.rights(context, image, "fes_main")
            reasons = gt.rights_reasons(run.config, self.state_now(), rights)
            run.note({"stage": "FesMain", "rights": text, "reasons": reasons})
            if reasons == ["rights_unreadable"]:
                return self._unreadable(run, "fes_main")
            if reasons:
                outcome = {"runs_done_today": "nothing_to_do", "samples_cap_reached": "nothing_to_do",
                           "attempt_unresolved": "attempt_unresolved"}
                run.finish(outcome.get(reasons[0], "skipped_rights"), *reasons, f"rights {text}")
                return False
            if not self.can_spend():
                run.finish("spend_not_enabled", f"rights {text}")
                return False
            run.rights, run.unreadable, run.phase = rights, 0, "joined"
            return True
        if step == "mode" and run.phase == "joined":
            if p.get("mode", "grade") != run.config.mode:
                return False
            run.phase = "mode_tapped"
            return True
        if step == "difficulty" and run.phase == "mode_tapped" and run.config.mode == "grade":
            wanted = p.get("difficulty")
            if wanted != run.config.difficulty:
                return False  # another difficulty node; only the configured one may match
            vision = self.vision(context)
            title = vision.text(image, gt.TEXT_BOXES["difficulty_title"])
            prompt = vision.text(image, gt.TEXT_BOXES["difficulty_prompt"])
            text, rights = self.rights(context, image, "difficulty")
            reasons = gt.difficulty_reasons(run.config, wanted, title, prompt, rights, run.rights)
            run.note({"stage": "Difficulty", "title": title, "prompt": prompt, "rights": text, "reasons": reasons})
            if reasons and self._retry(run, title, prompt, rights):
                return False
            if reasons:
                run.finish("difficulty_mismatch", *reasons)
                return False
            run.rights, run.unreadable, run.phase = rights, 0, "difficulty_tapped"
            return True
        return False

    def check(self, context, image, p):
        run, stage = self.run, p.get("stage")
        self._remember(image)
        if run is None:
            return False
        if stage == "rehearsal":
            return self.check_rehearsal(context, image, p)
        if stage == "unit":
            return self.check_unit(context, image)
        if stage == "tension_observe":
            return self.check_tension_observe(context, image)
        if stage == "live_start":
            return self.check_live_start(context, image)
        if stage == "resume":
            return self.check_resume(context, image)
        if stage == "takeover":
            return self.check_takeover(context, image)
        if stage == "report_takeover":
            return self.check_takeover(context, image, report=True)
        if stage == "ranking_takeover":
            return self.check_takeover(context, image, report="ranking")
        return False

    def check_rehearsal(self, context, image, p):
        """Rehearsal begins and spends one right directly at its difficulty button."""
        run = self.run
        wanted = p.get("difficulty")
        if run.finished or run.phase != "mode_tapped" or run.config.mode != "rehearsal":
            return False
        if wanted != run.config.difficulty:
            return False
        vision = self.vision(context)
        title = vision.text(image, gt.TEXT_BOXES["difficulty_title"])
        prompt = vision.text(image, gt.TEXT_BOXES["difficulty_prompt"])
        text, rights = self.rights(context, image, "difficulty")
        reasons = gt.difficulty_reasons(run.config, wanted, title, prompt, rights, run.rights)
        reasons += gt.rights_reasons(run.config, self.state_now(), rights) if not reasons else []
        if not self.can_spend():
            reasons.append("spend_not_enabled")
        run.note({"stage": "RehearsalDifficulty", "title": title, "prompt": prompt, "rights": text,
                  "reasons": reasons})
        if reasons and self._retry(run, title, prompt, rights):
            return False
        if reasons:
            run.finish("spend_not_enabled" if reasons == ["spend_not_enabled"] else "difficulty_mismatch", *reasons)
            return False
        intent = {"difficulty": wanted, "rights_before": list(rights), "stage": gt.REHEARSAL_STAGE,
                  "button": wanted}
        if not self._log({"event": "intent", **intent}):
            run.finish("log_write_failed", "rehearsal_intent")
            return False
        refusal = self.authorize(image, gt.REHEARSAL_APPROVERS[wanted])
        if refusal:
            self._log({"event": "refused", **intent, "reason": refusal})
            run.finish("spend_refused", str(refusal))
            return False
        run.intent, run.rights, run.unreadable, run.phase = intent, rights, 0, "awaiting_result"
        self._start_watch(run, "grade-after-rehearsal", gt.LIVE_FRAME_LIMIT if run.config.live_play else gt.WATCH_FRAME_LIMIT)
        return True

    def check_takeover(self, context, image, report=False):
        """B5i: the run began on a live turn screen (no フェス再開 dialog): take the live over without any click.

        Only before this run pressed anything of the Grade flow, with live play enabled and no attempt waiting at
        TensionDecided. Its right was spent earlier (the log's open attempt, or an unknown live counted as a new
        uncertain attempt); ライブ開始 / テンション決定 are never pressed. Success = LIVE REPORT OK, then Fes main.
        ``report``: the run began on LIVE REPORT (B5j, log ``report_takeover``); ``"ranking"``: on its second page,
        the ranking list (B5k, log ``ranking_takeover``; the live starts at the ranking OK)."""
        run = self.run
        if run.finished:
            return False
        reasons = []
        if run.phase != "active":
            reasons.append("grade_flow_already_started:" + str(run.phase))
        if run.resume is not None:
            reasons.append("open_attempt_not_live_started:" + run.resume["kind"])
        if not run.config.live_play:
            reasons.append("live_play_disabled")
        state = self.state_now()
        open_attempt = state.open
        ranking = report == "ranking"
        stage = gt.RANKING_TAKEOVER_STAGE if ranking else gt.REPORT_TAKEOVER_STAGE if report else gt.TAKEOVER_STAGE
        run.note({"stage": stage, "phase": run.phase, "reasons": reasons,
                  "open_attempt_spent": bool(open_attempt and open_attempt.get("spent"))})
        if reasons:
            run.finish("live_takeover_refused", *reasons)
            return False
        intent = {"difficulty": (open_attempt or {}).get("difficulty") or run.config.difficulty, "rights_before": None,
                  "stage": stage, "resumed": True, "takeover": True, "rights_observed": None,
                  "attempt_known": bool(open_attempt and open_attempt.get("spent"))}
        event = "ranking_takeover" if ranking else "report_takeover" if report else "live_takeover"
        if not self._log({"event": event, **intent, "frame_sha256": tc.image_digest(image)}):
            run.finish("log_write_failed", event)
            return False
        run.intent, run.unreadable, run.phase = intent, 0, "awaiting_result"
        self._start_watch(run, f"grade-after-{event.replace('_', '-')}" if report else "grade-after-takeover",
                          gt.LIVE_FRAME_LIMIT)
        if ranking:  # the report OK happened before this run: wait for the ranking OK (no report reading here)
            live = self._live()
            live.stage = "report_tapped"
            live.seen, live.active_since, live.report_tapped_at = True, self.clock(), self.clock()
        return True

    def check_resume(self, context, image):
        """フェス再開: allow 「再開」 (non-consuming) once, then play the resumed live (its right was already spent)."""
        run = self.run
        if run.finished:
            return False
        reasons = []
        if run.phase not in gt.RESUME_PHASES:
            reasons.append("tension_already_sent_by_this_run:" + str(run.phase))
        if run.resume is not None:
            reasons.append("open_attempt_not_live_started:" + run.resume["kind"])
        if not run.config.live_play:
            reasons.append("live_play_disabled")
        text, rights = self.rights(context, image, "fes_main")  # the dimmed Fes main bar under the dialog
        run.note({"stage": gt.RESUME_STAGE, "rights": text, "phase": run.phase, "reasons": reasons})
        if not reasons and rights is None:
            if self._retry(run, rights):
                return False
            reasons.append("rights_unreadable")
        if reasons:
            run.confirm_rejected = reasons
            if reasons[0].startswith(("tension_already", "open_attempt")):
                run.finish("unexpected_fes_resume", *reasons)
            return False  # Resume.Hold stops the task without a click
        intent = {"difficulty": run.config.difficulty, "rights_before": None, "stage": gt.RESUME_STAGE,
                  "button": "再開", "resumed": True, "rights_observed": list(rights)}
        if not self._log({"event": "resume", **intent, "frame_sha256": tc.image_digest(image)}):
            run.confirm_rejected = ["log_write_failed"]
            return False
        run.intent, run.rights, run.unreadable, run.phase = intent, rights, 0, "awaiting_result"
        self._start_watch(run, "grade-after-resume", gt.LIVE_FRAME_LIMIT)
        return True

    def check_unit(self, context, image):
        """参加ユニット: grant テンション決定 (irreversible, non-consuming) once per attempt."""
        run = self.run
        if run.finished or run.phase != "difficulty_tapped":
            run.confirm_rejected = run.confirm_rejected or ["unexpected_unit"]
            return False
        title, notice, text, rights = self.unit_texts(context, image)
        # Batch 4: the chosen Fes unit is bound by the evaluation number on this page (read every time, reported).
        score_text = self.vision(context).text(image, gt.TEXT_BOXES["unit_score"])
        reasons = gt.unit_reasons(run.config, title, notice, rights, run.rights)
        reasons += gt.unit_binding_reasons(run.config, score_text)
        reasons += gt.rights_reasons(run.config, self.state_now(), rights) if not reasons else []
        if not self.can_spend():
            reasons.append("spend_not_enabled")
        run.note({"stage": "Unit", "title": title, "notice": notice, "rights": text,
                  "unit_score": gt.unit_score_reading(score_text), "unit_score_bound": run.config.unit_score or None,
                  "reasons": reasons})
        if reasons and self._retry(run, title, notice, rights):
            return False
        if reasons:
            run.confirm_rejected = reasons
            return False
        intent = {"difficulty": run.config.difficulty, "rights_before": list(rights)}
        if not self._log({"event": "tension_intent", **intent}):
            run.confirm_rejected = ["log_write_failed"]
            return False
        refusal = self.authorize(image, gt.APPROVER)
        if refusal:
            self._log({"event": "tension_refused", **intent, "reason": refusal})
            run.confirm_rejected = ["spend_grant_refused:" + str(refusal)]
            return False
        run.rights, run.unreadable, run.phase = rights, 0, "tension_tapped"
        self._start_watch(run, "grade-after-tension", gt.TENSION_FRAME_LIMIT)
        return True

    def check_tension_observe(self, context, image):
        """Resume (read-only): a fresh TensionDecided frame of today's open attempt with unchanged rights."""
        run = self.run
        if run.finished or run.phase != "active" or run.resume is None:
            return False
        title, notice, text, rights = self.unit_texts(context, image)
        expected = run.resume.get("rights_before")
        reasons = gt.tension_reasons(run.config, title, notice, rights, expected)
        if not self.back_disabled(context, image):
            reasons.append("back_not_disabled")
        if run.resume.get("difficulty") != run.config.difficulty:
            reasons.append("attempt_difficulty_differs")
        run.note({"stage": gt.LIVE_STAGE, "resume": run.resume["kind"], "title": title, "notice": notice,
                  "rights": text, "expected": list(expected) if expected else None, "reasons": reasons})
        if reasons and self._retry(run, title, notice, rights):
            return False
        if reasons:
            if rights is not None and "rights_changed" in reasons:
                run.finish("spend_uncertain", "tension_decided_rights_changed", *reasons)
            else:
                run.finish("unit_mismatch", *reasons)
            return False
        evidence = {"difficulty": run.config.difficulty, "rights_before": list(rights)}
        if run.resume["kind"] == "legacy_uncertain":
            if not self._log({"event": "resolved", "result": gt.NOT_SPENT_TENSION_DECIDED, **evidence,
                              "rights_observed": list(rights), "observed_stage": gt.LIVE_STAGE,
                              "frame_sha256": tc.image_digest(image)}):
                run.finish("log_write_failed", "resolution")
                return False
            run.notes.append(gt.NOT_SPENT_TENSION_DECIDED)
        if not self._log({"event": "tension_decided", **evidence, "resumed": run.resume["kind"]}):
            run.finish("log_write_failed", "tension_decided")
            return False
        run.rights, run.unreadable, run.phase = rights, 0, "tension_decided"
        return True

    def check_live_start(self, context, image):
        """TensionDecided: grant ライブ開始 (consumes 1 フェス参加権) once."""
        run = self.run
        if run.finished or run.phase != "tension_decided":
            return False
        title, notice, text, rights = self.unit_texts(context, image)
        reasons = gt.unit_reasons(run.config, title, notice, rights, run.rights)
        reasons += gt.live_reasons(run.config, self.state_now(), rights)
        if not self.back_disabled(context, image):
            reasons.append("back_not_disabled")
        if not self.can_spend():
            reasons.append("spend_not_enabled")
        reasons = list(dict.fromkeys(reasons))
        run.note({"stage": gt.LIVE_STAGE, "title": title, "notice": notice, "rights": text, "reasons": reasons})
        if reasons and self._retry(run, title, notice, rights):
            return False
        if reasons:
            run.confirm_rejected = reasons
            return False
        intent = {"difficulty": run.config.difficulty, "rights_before": list(rights), "stage": gt.LIVE_STAGE,
                  "button": "ライブ開始"}
        if not self._log({"event": "intent", **intent}):
            run.confirm_rejected = ["log_write_failed"]
            return False
        refusal = self.authorize(image, gt.LIVE_APPROVER)
        if refusal:
            self._log({"event": "refused", **intent, "reason": refusal})
            run.confirm_rejected = ["spend_grant_refused:" + str(refusal)]
            return False
        run.intent, run.phase = intent, "awaiting_result"
        self._start_watch(run, "grade-after-live", gt.LIVE_FRAME_LIMIT if run.config.live_play else gt.WATCH_FRAME_LIMIT)
        return True

    def watch(self, context, image, p):
        run = self.run
        if run is None:
            return False
        if p.get("after") == "tension":
            return self.watch_tension(context, image)
        if run.finished and run.live is not None:
            return True  # the live play stopped in another step: end the live loop
        if run.phase != "awaiting_result":
            return False
        return self.watch_live(context, image)

    def watch_tension(self, context, image):
        """Read-only after テンション決定: the TensionDecided stage with unchanged rights (≤30 s)."""
        run = self.run
        if run.phase != "tension_tapped":
            return False
        now = self._save(run, image)
        if self.vision(context).hit(TENSION_GUARD, image):
            title, notice, text, rights = self.unit_texts(context, image)
            reasons = gt.tension_reasons(run.config, title, notice, rights, run.rights)
            if not self.back_disabled(context, image):
                reasons.append("back_not_disabled")
            run.note({"watch": gt.LIVE_STAGE, "title": title, "notice": notice, "rights": text, "reasons": reasons})
            if not reasons:
                if not self._log({"event": "tension_decided", "difficulty": run.config.difficulty,
                                  "rights_before": list(rights), "frames": run.saver.saved[-2:]}):
                    run.finish("log_write_failed", "tension_decided")
                    return True
                run.phase, run.unreadable = "tension_decided", 0
                return True
            if rights is not None and "rights_changed" in reasons:
                self._log({"event": "uncertain", "difficulty": run.config.difficulty,
                           "rights_before": list(run.rights), "rights_after": list(rights),
                           "reason": "rights_changed_after_tension_confirm"})
                run.finish("spend_uncertain", "rights_changed_after_tension_confirm", f"rights {text}")
                return True
        if now - run.watch_started > gt.TENSION_WATCH_SECONDS:
            self._log({"event": "tension_uncertain", "difficulty": run.config.difficulty,
                       "rights_before": list(run.rights), "reason": "tension_decided_not_observed"})
            run.finish("tension_uncertain", "tension_decided_not_observed")
            return True
        return False

    def watch_live(self, context, image):
        """After ライブ開始: only a Fes main with rights - 1 is success. Read-only unless ``live_play`` (B5d), in which
        case the ``SC.Grade.Live.*`` steps play the live and this watch enforces its time/stuck bounds."""
        run = self.run
        now = self._save(run, image)
        vision = self.vision(context)
        resumed = bool(run.intent.get("resumed"))
        takeover = bool(run.intent.get("takeover"))
        if takeover:  # B5i: no rights reading before the live: Fes main after this run's LIVE REPORT OK concludes it
            expected = None
        elif resumed:  # the right was spent before the dialog: Fes main must show the rights read under it
            expected = tuple(run.intent["rights_observed"])
        else:
            before = tuple(run.intent["rights_before"])
            expected = (before[0] - 1, before[1])
        live = run.live if run.config.live_play else None
        if not run.config.live_play and any(vision.hit(node, image) for node in
                                            ("SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Report.Guard",
                                             "SC.Grade.Live.Ranking.Guard")):
            # The configured mode starts the live, then immediately hands it to the player. Do not wait for a
            # static-screen timeout, count it as played, press OK, or start the remaining daily lives.
            self._log({"event": "manual_takeover", **run.intent, "frames": run.saver.saved[-3:]})
            run.finish("manual_takeover", "live_started_manual_takeover_required")
            self.report()
            return True
        if vision.hit("SC.Nav.FesMain.Guard", image):
            text, rights = self.rights(context, image, "fes_main")
            if resumed and not (live is not None and live.seen):
                if now - run.watch_started > gt.RESUME_WATCH_SECONDS:
                    return self.live_unfinished("resumed_live_not_observed_15s", rights=text)
                return False
            reported = live is not None and live.ok_pressed  # this run pressed a LIVE REPORT / ranking OK
            if rights is not None and (rights == expected or (takeover and reported)):
                extra = {"live": live.summary()} if live is not None else {}
                if not self._log({"event": "verified", **run.intent, "rights_after": list(rights),
                                  "frames": run.saver.saved[-3:], **extra}):
                    run.finish("log_write_failed", "verified_result_not_saved")
                    return True
                run.note({"watch": "fes_main", "rights_after": list(rights)})
                run.finish("played", f"rights {rights[0]} (live taken over)" if takeover
                           else f"rights {expected[0]} (resumed)" if resumed else f"rights {before[0]}->{rights[0]}")
                if live is not None and live.report:
                    run.notes.append("live_report " + ", ".join(f"{k}={v}" for k, v in live.report.items()))
                self._complete_live(rights, extra)
                return True
            run.note({"watch": "fes_main", "rights": text})
            if live is not None and live.seen:
                live.fes_main_since = live.fes_main_since or now
                if now - live.fes_main_since > gt.LIVE_STUCK_SECONDS:
                    return self.live_unfinished("fes_main_rights_not_decremented", rights=text)
            return False
        if vision.hit("SC.Common.Home.Guard", image):
            if resumed or (live is not None and live.seen):
                return self.live_unfinished("home_observed_rights_not_verified")
            self._log({"event": "uncertain", **run.intent, "reason": "home_observed_rights_not_verified",
                       "frames": run.saver.saved[-3:]})
            run.finish("spend_uncertain", "home_observed_rights_not_verified")
            return True
        if run.config.live_play:
            if self.live_watch(context, image, now):
                return True
            live = run.live
        if resumed and live is not None and not live.seen and now - run.watch_started > gt.RESUME_WATCH_SECONDS:
            return self.live_unfinished("resumed_live_not_observed_15s")
        if live is not None and live.seen and now - run.last_change >= gt.STATIC_SECONDS:
            return self.live_unfinished("unrecognised_screen_static_20s")
        if not run.config.live_play and run.watch_started is not None and now - run.watch_started > gt.WATCH_SECONDS:
            self._log({"event": "uncertain", **run.intent, "reason": "watch_time_cap", "frames": run.saver.saved[-3:]})
            run.finish("spend_uncertain", "watch_time_cap")
            return True
        if self.clock() - run.last_change >= gt.STATIC_SECONDS:
            # Unrecognised and unchanged for 20 s: the live may be waiting for input. Stop observing, never click.
            self._log({"event": "uncertain", **run.intent, "reason": "unrecognised_screen_static_20s",
                       "frames": run.saver.saved[-3:]})
            run.finish("spend_uncertain", "unrecognised_screen_static_20s_possible_input_wait")
            return True
        return False

    def _complete_live(self, rights, extra):
        """Continue only from a fresh, guarded Fes main with this live's right reduction verified."""
        run = self.run
        state = self.state_now()
        run.daily_progress = gt.progress_from_state(run.config, state)
        run.batch_results.append({"outcome": "played", "mode": run.config.mode,
                                  "difficulty": run.config.difficulty, "rights_after": list(rights), **extra})
        reasons = gt.rights_reasons(run.config, state, rights)
        # Read-only/manual takeover mode must never start another live automatically.
        if run.daily_start is None or not run.config.live_play or reasons:
            if reasons:
                run.note({"batch_stop": reasons, "daily_progress": run.daily_progress.to_dict()})
            return
        run.note({"batch_next": run.daily_progress.to_dict()})
        run.restart_live()

    # -- actions
    def awaiting_spend(self):
        return self.run is not None and self.run.phase in ("awaiting_result", "tension_tapped")

    def finish_action(self, context, p):
        self._remember_cached(context)
        return super().finish_action(context, p)

    def abort(self, context, p):
        run = self.run
        self._remember_cached(context)
        if run is not None and not run.finished:
            if self.awaiting_spend():
                return self.spend_error(context, p)
            run.finish("timeout", "unknown_or_unhandled_screen")
        self.report()
        return bool(run is not None and run.succeeded)  # halted but played (played_reported): the task succeeded

    def spend_error(self, context, p):
        run = self.run
        if run is None:
            return False
        self._remember_cached(context)
        if run.phase == "tension_tapped":
            outcome = self.authority.last_outcome(self.SPEND_ACTION) if self.authority is not None else None
            intent = {"difficulty": run.config.difficulty, "rights_before": list(run.rights or ())}
            if outcome == "denied":
                self._log({"event": "tension_refused", **intent, "reason": "click_denied_before_send"})
                run.phase = "spend_error"
                run.finish("spend_refused", "tension_click_denied_before_send")
            else:
                self._log({"event": "tension_uncertain", **intent, "reason": "result_not_observed:" + str(outcome)})
                run.phase = "spend_error"
                run.finish("tension_uncertain", "tension_decided_not_observed")
            self.report()
            return False
        if not run.finished and ((run.live is not None and run.live.seen) or (run.intent or {}).get("resumed")):
            self.live_unfinished("live_stage_timeout")  # rights are spent: never retried, never ライブ開始 again
            self.report()
            return run.succeeded  # 2026-10-12: played_reported (LIVE REPORT read and closed) is a played live
        if not run.finished:
            return super().spend_error(context, p)
        self.report()
        return run.succeeded

    def report(self):
        run = self.run
        if run is not None and not run.reported:
            run.daily_progress = gt.progress_from_state(run.config, self.state_now())
        if run is not None and not run.reported and run.live is not None:
            run.note({"live_summary": run.live.summary()})
        if run is not None and not run.reported:
            self._save_stop_samples(run)
        if run is not None and not run.reported and run.samples:
            run.note({"sampled_frames": sum(len(list(Path(d).glob("frame-*.png"))) for d in run.samples),
                      "skipped_duplicates": getattr(run, "skipped_duplicates", 0), "directories": list(run.samples)})
        super().report()


RUNTIME = GradeRuntime()
RECOGNITIONS = {"State": "state", "Plan": "plan", "Check": "check", "Watch": "watch", "Live": "live"}
ACTIONS = {"Begin": "begin", "Finish": "finish_action", "Abort": "abort", "SpendError": "spend_error"}


def grade_registrations(runtime):
    return task_custom.registrations(runtime, PREFIX, RECOGNITIONS, ACTIONS)
