"""Maa glue of the CollaboFes task (rules in ``agent.core.collabo_task``); no input happens here.

``SC.Collabo.Check`` with ``{"stage": "observe"}`` decides once per fresh frame and never hits; ``{"stage": <kind>}`` hits
only when this frame's decision is that kind, so exactly one ``SC.Collabo.Do.*`` node runs. ``drag_second`` returns the
own panel's box as its recognition target (the Swipe start); every other kind returns the fixed node target.
"""
from __future__ import annotations

from agent.core import collabo_task as ct
from agent.core import live_status
from agent.core import task_common as tc
from agent.core import task_custom

PREFIX = "SC.Collabo"
CONFIG_NODES = ("SC.Collabo.Config.Stage", "SC.Collabo.Config.Count", "SC.Collabo.Config.Unit",
                "SC.Collabo.Config.Level", "SC.Collabo.Config.Backup")


class CollaboRun(tc.TaskRun):
    SUCCESS = frozenset({"done"})
    HALT = frozenset({"spend_uncertain", "spend_refused", "timeout"})


class CollaboRuntime(task_custom.TaskRuntime):
    KIND = "collabo"
    RUN = CollaboRun
    CONFIG = ct.CollaboConfig
    SPEND_ACTION = ct.SPEND_ACTION

    def __init__(self, sink=None, authority=None, ledger=None, ledger_path=None, max_wait_s=0, time_left=None, **kwargs):
        super().__init__(sink, authority, **kwargs)
        self.ledger = ledger if ledger is not None or ledger_path is None else ct.LevelLedger(ledger_path)
        self.max_wait_s = max_wait_s     # 満員 / Pass waits end this long after the start (the task timeout's share)
        self.time_left = time_left       # live: seconds left in the run session (its click cap) -- waits end before it
        self.play = None
        self._cache, self._unsent, self._unsent_runs = (None, None), None, 0

    def started(self, run):
        max_wait_s = self.max_wait_s
        if self.time_left is not None:
            try:
                max_wait_s = max(0.0, min(max_wait_s, float(self.time_left())))
            except Exception:
                max_wait_s = 0
        self.play = ct.CollaboPlay(run.config, self.log, self.authority, self.now, self.ledger, max_wait_s)
        self._cache, self._unsent, self._unsent_runs = (None, None), None, 0
        self._last_tap = None            # (kind, state before it) of the Do node now running: a refused tap retries
        self._report_saver = None

    def summary_into_run(self):
        run, play = self.run, self.play
        if run is None or play is None:
            return
        run.collabo = play.summary()
        run.notes.append(f"lives_{len(play.lives)}:{play.reason}")
        if play.outcome is not None:
            run.finish(play.outcome, play.reason)
        elif play.phase == "ended":
            run.finish("done", play.reason)

    # -- recognitions
    def check(self, context, image, p):
        run, play = self.run, self.play
        if run is None or play is None or run.finished:
            return False
        decision = self.decide(context, image)
        stage = p.get("stage")
        if stage == "wait" and self._unsent is not None:
            # Wait follows every Do node: this frame's node did not run (its guard or max_hit). Undo and read again.
            self._rollback()
            self._cache = (None, None)
            return True
        if stage == "observe" or decision.kind != stage:
            return False
        self._last_tap = self._unsent
        self._cache, self._unsent = (None, None), None
        self._unsent_runs = 0
        return True

    def ended(self, context, image, p):
        """Hits once the play is over (``SC.Collabo.Done``)."""
        return self.run is not None and self.play is not None and (self.play.phase == "ended" or self.run.finished)

    def decide(self, context, image):
        digest = tc.image_digest(image)
        if (self._cache[0] == digest and self._cache[1].kind != "idle"   # an idle frame re-checks the clock
                and self._cache[1].reason != "report_clear_unread"):     # a still report frame counts down too
            return self._cache[1]
        if self._unsent is not None:
            self._rollback()
        play = self.play
        state = play.snapshot()
        decision = play.step(ct.Reader(self.vision(context)), image)
        self._cache = (digest, decision)
        self._show_hold(play.status_text())
        if decision.kind not in ("wait", "idle"):
            self.run.note({"phase": play.phase, "page": play.last_page, "decision": decision.kind,
                           "reason": decision.reason})
        if decision.kind in ct.CLICK_NODES:
            self._unsent = (decision.kind, state)
        if decision.kind == "report_ok" and decision.reason == "clear_unread":
            self._save_report(image)    # 2026-11-20 live: Lv.11 クリアボーナス unread twice -> keep the frame
        if play.phase == "ended":
            self.summary_into_run()
        return decision

    def _save_report(self, image):
        try:
            if self._report_saver is None:
                self._report_saver = tc.FrameSaver("collabo-report-unread", self.sample_root, self.now, 6)
            path = self._report_saver.save(image)
            if path is not None:
                self._log({"event": "collabo_report_sample", "path": str(path)})
        except Exception:
            pass

    def _show_hold(self, text):
        """Display only: the waiting line in the 运行中 view (agent.core.live_status); never raises."""
        if text == getattr(self, "_hold", None):
            return
        self._hold = text
        try:
            status = live_status.current()
            if status is not None:
                status.hold(text)
        except Exception:
            pass

    def _rollback(self):
        kind, state = self._unsent
        self._unsent = None
        play = self.play
        if kind == "tension" and play.lives:
            self._log({"event": "collabo_not_spent", **play.lives[-1], "reason": "tap_not_sent"})
            if self.authority is not None and hasattr(self.authority, "revoke"):
                self.authority.revoke()
        play.restore(state)
        self._unsent_runs += 1
        self._log({"event": "collabo_tap_not_sent", "kind": kind, "count": self._unsent_runs})
        if self._unsent_runs >= ct.MAX_UNSENT_TAPS:
            if play.phase == "live":
                play._fail("spend_uncertain", "tap_not_sent_in_live")
            else:
                play._fail("collabo_tap_not_sent", kind)
            self.summary_into_run()

    # -- actions
    def failed(self, context, p):
        """on_error of a non-consuming Do node (refused or failed). Outside a live, a navigation tap restores the
        state before it and decides again on a fresh frame (2026-11-20 live: ライブへ refused with
        frame_binding_guard_failed ended the task); after MAX_UNSENT_TAPS in a row, or in a live: stop."""
        play = self.play
        if self.run is None or play is None:
            return False
        last, self._last_tap = self._last_tap, None
        if play.phase != "live" and last is not None and last[0] != "tension" and play.outcome is None:
            kind, state = last
            play.restore(state)
            self._unsent_runs += 1
            self._log({"event": "collabo_tap_failed_retry", "kind": kind, "count": self._unsent_runs})
            if self._unsent_runs < ct.MAX_UNSENT_TAPS:
                self._cache = (None, None)
                return True
        if play.phase == "live":
            play._fail("spend_uncertain", "live_tap_failed")
        else:
            play._fail("collabo_tap_failed", str(p.get("kind", "")))
        self._cache, self._unsent = (None, None), None
        self.summary_into_run()
        self.report()
        return False

    def tension_error(self, context, p):
        """on_error of テンション決定: denied before sending ends cleanly; anything else is uncertain."""
        play = self.play
        if self.run is None or play is None or not play.lives:
            return False
        outcome = self.authority.last_outcome(ct.SPEND_ACTION) if self.authority is not None else None
        record = play.lives[-1]
        self._cache, self._unsent = (None, None), None
        if outcome == "denied":
            play.lives.pop()
            play.phase, play.live = "navigate", None
            self._log({"event": "collabo_refused", **record, "reason": "click_denied_before_send"})
            play._end("spend_refused")
            return True
        self._log({"event": "collabo_uncertain", **record, "reason": "result_not_observed:" + str(outcome)})
        play._fail("spend_uncertain", "tension_click_" + str(outcome))
        self.summary_into_run()
        self.report()
        return False

    def abort(self, context, p):
        """Unknown screen / timeout: inside a live (after テンション決定) the Pass use is uncertain."""
        play = self.play
        if self.run is not None and play is not None and not self.run.finished:
            if play.phase == "live":
                play._fail("spend_uncertain", "abort_in_live")
            elif play.outcome is None:
                play._fail("timeout", "unknown_or_unhandled_screen")
            self.summary_into_run()
        self.report()
        return False

    def finish_action(self, context, p):
        self.summary_into_run()
        return super().finish_action(context, p)


class CollaboCheckRecognition(task_custom.RuntimeRecognition):
    """``SC.Collabo.Check``; the drag decision returns the own panel row as the Swipe start box."""

    def analyze(self, context, argv):
        result = super().analyze(context, argv)
        if result is None or tc.params(argv.custom_recognition_param).get("stage") != "drag_second":
            return result
        play = self.runtime.play
        row = getattr(play, "drag_row", None)
        if row is None:
            return None
        return task_custom.CustomRecognition.AnalyzeResult(box=tuple(ct.PANEL_ROWS[row]), detail=result.detail)


RUNTIME = CollaboRuntime()
RECOGNITIONS = {"State": "state", "Check": "check", "Ended": "ended"}
ACTIONS = {"Begin": "begin", "Finish": "finish_action", "Abort": "abort", "Failed": "failed",
           "TensionError": "tension_error"}


def collabo_registrations(runtime):
    recognitions, actions = task_custom.registrations(runtime, PREFIX, RECOGNITIONS, ACTIONS)
    recognitions[PREFIX + ".Check"] = CollaboCheckRecognition(runtime, "check")
    return recognitions, actions
