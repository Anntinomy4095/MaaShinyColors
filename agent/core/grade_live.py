"""Grade Fes live play after ライブ開始 (B5d, opt-in ``live_play``): runtime half of ``SC.Grade.Live.*``.

The consuming click (ライブ開始) has already happened when this code runs; every live input is non-consuming (card,
judge, timing tap, 倍速, LIVE REPORT OK) and goes through its own table action. Rules:

- each tap needs a positive stage guard on a fresh frame (Pipeline ``SC.Grade.Live.*.Guard``) and the Agent's stage
  machine: wait -> card_tapped -> judge_tapped -> timing_tapped -> (next turn) ... -> report_tapped;
- the waiting-stage state (turn, judges, cards) must read identically on two consecutive frames before a card is chosen;
  the target-stage predictions likewise before a judge is chosen;
- B5h: the live canvas can drop a tap. A card, judge or 倍速 tap without visible effect after ``LIVE_RETAP_SECONDS``
  may be repeated ONCE (same target, logged ``live_retap``) when two consecutive fresh frames still show the same
  stage and state (same TURN, judges and cards; for a judge also the same selected card and predictions). The timing
  tap and the film tap are never repeated; the memory appeal is, like a card (2026-10-24: a dropped memory tap left
  TURN 5/6 unchanged);
- a (re-)tap whose next stage does not appear within ``LIVE_STUCK_SECONDS`` is not repeated again: the live stops
  (``live_unfinished``); so do an unreadable state, a skipped/regressed turn, more than 7 turns, an unknown static
  screen and the overall time cap. ライブ開始 is never pressed again; rights are already spent.
- 再抽選 (reroll) is never pressed: it consumes a フェス参加権 / Fes ticket.
- B5j LIVE REPORT: OK (``live-report-ok``) only once the readings incl. 所持マニー are complete and unchanged for
  ``LIVE_REPORT_STABLE_SECONDS`` (or after ``LIVE_REPORT_SETTLE_SECONDS``); an OK without effect may be repeated ONCE
  after ``LIVE_RETAP_SECONDS`` on two fresh frames with the same readings, then ``report_tapped_no_effect``. After OK
  only the shared popup whitelist acts; Fes main must follow within ``LIVE_AFTER_REPORT_SECONDS``. The time cap counts
  active play only (first live screen -> OK).
- B5k: after that OK a second LIVE REPORT page (ranking list) can follow. Its own row (rank, pt) is recorded; its OK
  (same ``live-report-ok`` button) is pressed once after ``LIVE_RANKING_SETTLE_SECONDS`` on two fresh frames with the
  same reading, may be repeated ONCE like the report OK, then ``ranking_tapped_no_effect``; then the same post-report
  observation (popup whitelist, Fes main with rights within ``LIVE_AFTER_REPORT_SECONDS`` of the last OK).
- 2026-10-12: the live may end before TURN 7 (all judges 審査終了 after TURN 3): after the last timing tap any end
  screen is accepted (end animation, Loading, LIVE REPORT); a TURN screen whose judges are all finished is never
  "stuck". A 「NEW RECORD」 / 「グレードランキング」 overlay over LIVE REPORT (``record``; it does not auto-advance) is
  closed by one tap on its white band (``live-record-dismiss``) after ``LIVE_RECORD_SETTLE_SECONDS`` on two fresh
  frames, repeated ONCE after ``LIVE_RETAP_SECONDS``, then ``record_overlay_no_effect``; the report is then read
  afresh and closed with OK as usual.
- 2026-10-22 思い出アピール (official Grade TURN 6/6): memory tap -> judge (same predictions rule) -> Ready / Good-Bad
  film -> the turn screen without 流行 badges and with a yellow arrow at the chosen judge (``memory_wait``) waits for
  one tap anywhere (user-confirmed). After ``LIVE_MEMORY_GO_SETTLE_SECONDS`` on two fresh frames with the arrow at the
  chosen judge, that judge is tapped once (``live_memory_go``), re-tapped ONCE after ``LIVE_RETAP_SECONDS`` if the
  screen is unchanged; an arrow at another judge stops the live.
- 2026-10-24 film QTE (user: pick a Good memory): the Good/Bad strip scrolls under a fixed cyan frame (``film``,
  ``fes_live_reader.read_film``); without a tap it ends in 「miss」. One tap inside the frame (``live_memory_film``)
  when the tile at the right edge (about where the strip will stand when a tap lands, ~0.3-0.45 s) reads Good, at the
  latest on the ``LIVE_FILM_MAX_ROUNDS``-th film frame / ``LIVE_FILM_DEADLINE_SECONDS`` after the first. The readings
  before and after the tap are logged to calibrate the timing.

Decisions come from the pure ``fes_live_policy`` and are written to the run log (``live_turn`` / ``live_target``).
"""
from __future__ import annotations

import dataclasses
import hashlib

from agent.core import fes_live_policy as policy
from agent.core import fes_live_reader as reader
from agent.core import grade_task as gt
from agent.core import grade_timing

SCREEN_GUARD = "SC.Grade.Live.Screen.Guard"      # TURN label + pause + スキル履歴 (every live turn frame)
TREND_WORD = "SC.Grade.Live.Anchor.TrendWord"    # 「流行」 badge of the Vo judge: waiting / target stage only
SWAP_HINT = "SC.Grade.Live.Anchor.SwapHint"      # 「スライドで交換」 under the selected card
APPEAL_BAR = "SC.Grade.Live.Anchor.AppealLabel"  # APPEAL timing bar
MEMORY_ARROW = "SC.Grade.Live.Anchor.MemoryArrow"  # yellow arrow at the chosen judge after the memory appeal's film
JUDGE_BANDS = (("Vo", 0, 180), ("Da", 180, 345), ("Vi", 345, 520))  # judge panel rows (y), arrow centre -> judge
SPEED_OFF = "SC.Grade.Live.Anchor.SpeedOff"      # 倍速OFF button face
REPORT_GUARD = "SC.Grade.Live.Report.Guard"      # LIVE REPORT title + SCORE + OK
REPORT_MONEY2X = "SC.Grade.Live.Anchor.ReportMoney2x"  # 「獲得マニー 2倍」 campaign badge (record only)
REPORT_MISSION = "SC.Grade.Live.Anchor.ReportMission"  # 「ミッション報酬獲得」 row (record only)
RANKING_GUARD = "SC.Grade.Live.Ranking.Guard"    # B5k: LIVE REPORT title + OK + row 1 スコア詳細/ユニット詳細
RECORD_GUARD = "SC.Grade.Live.Record.Guard"      # LIVE REPORT title + 「NEW RECORD」/「グレードランキング」 overlay banner
STUCK_PAIRS = {("card_tapped", "wait"), ("judge_tapped", "target"), ("timing_tapped", "timing"),
               ("memory_go_tapped", "memory_wait"), ("film_tapped", "film"),
               ("report_tapped", "report"), ("ranking_tapped", "ranking")}
OK_STAGES = ("report_tapped", "ranking_tapped")
REPORT_FIELDS = ("rank", "stars", "score", "money", "held")


@dataclasses.dataclass
class LivePlay:
    started: float
    stage: str = "wait"           # wait | card_tapped | judge_tapped | [film_tapped] | timing_tapped | memory_go_tapped | report_tapped | ranking_tapped
    stage_since: float = 0.0
    turn: int = 0                 # turn of the last committed card/memory choice
    max_turns: int = None         # TURN total read from the screen (official Grade: 6 or 7), kept for the whole live
    seen: bool = False            # a live turn screen was recognised (the live certainly started)
    plan: object = None           # fes_live_policy.Decision of the current turn
    plan_state: object = None     # fes_live_policy.TurnState the plan was made on
    rounds: int = 0               # recognition rounds (one screenshot each; counted by the watch step)
    prev_sig: object = None
    sig_round: int = -1
    stable: bool = False
    prev_pred: object = None
    pred_round: int = -1
    pred_stable: bool = False
    unreadable: int = 0
    unreadable_digests: set = dataclasses.field(default_factory=set)
    frame: str = None             # digest of the frame the cache below belongs to
    cache: dict = dataclasses.field(default_factory=dict)
    pair: tuple = None            # (stage, screen kind) and since when it holds (stuck detection)
    pair_since: float = 0.0
    speed_tapped: bool = False
    speed_result: str = None
    report: dict = None
    report_first: float = None
    report_prev: dict = None
    report_stable_since: float = None  # since when the complete readings are unchanged
    report_tapped_at: float = None
    report_flags: dict = dataclasses.field(default_factory=dict)  # record-only anchors seen on any report frame
    ok_pressed: bool = False      # this run pressed a LIVE REPORT OK (report or ranking page)
    ranking: dict = None          # B5k: own row of the ranking page {rank, pt, row} when its OK was pressed
    ranking_first: float = None
    ranking_reading: dict = None
    active_since: float = None    # first recognised live screen (turn or LIVE REPORT): the time cap starts here
    fes_main_since: float = None  # Fes main seen after the live without rights - 1
    turns: list = dataclasses.field(default_factory=list)
    taps: int = 0
    speed_since: float = None     # time of the (last) 倍速 tap
    retaps: set = dataclasses.field(default_factory=set)     # {(turn, "card"|"judge"|"speed")}: one re-tap per decision
    confirm: dict = dataclasses.field(default_factory=dict)  # key -> (value, round, equal to the previous round)
    judge_target: str = None      # the judge tapped this turn and the predictions it was chosen on
    judge_pred: dict = None
    record_first: float = None    # 「NEW RECORD」/ranking overlay first seen
    record_taps: int = 0          # overlay taps (first + at most one re-tap)
    record_tapped_at: float = None
    film_first: float = None      # memory film: first readable frame, film frames seen, readings before / after the tap
    film_rounds: int = 0
    film_seen: list = dataclasses.field(default_factory=list)
    film_after: list = dataclasses.field(default_factory=list)
    timing: dict = None           # grade_timing (opt-in): this turn's bar frames and plan or fallback

    def summary(self):
        return {"turns_played": len(self.turns), "last_turn": self.turn, "stage": self.stage,
                "speed": self.speed_result or ("tapped" if self.speed_tapped else "untouched"),
                "report": self.report, "ranking": self.ranking, "record_taps": self.record_taps, "taps": self.taps, "retaps": sorted(f"{t}:{k}" for t, k in self.retaps),
                "film": {"seen": self.film_seen[-4:], "after": self.film_after} if self.film_seen else None,
                "decisions": self.turns[-7:]}


def frame_digest(image):
    try:
        return hashlib.sha1(image[::12, ::12].tobytes()).hexdigest()
    except Exception:
        return None


class LiveMixin:
    """Mixed into ``GradeRuntime`` (uses ``self.run``, ``self.vision``, ``self.clock``, ``self._log``)."""

    # -- helpers
    def report_guard(self):
        return "SC.Grade.Rehearsal.Report.Guard" if self.run.config.mode == "rehearsal" else REPORT_GUARD

    def live_enabled(self):
        run = self.run
        return run is not None and run.config.live_play and run.phase == "awaiting_result" and not run.finished

    def _live(self):
        run = self.run
        if run.live is None:
            now = self.clock()
            run.live = LivePlay(started=now, stage_since=now, pair_since=now)
        return run.live

    def _cached(self, live, image, key, compute):
        digest = frame_digest(image)
        if digest != live.frame:
            live.frame, live.cache = digest, {}
        if key not in live.cache:
            live.cache[key] = compute()
        return live.cache[key]

    def _commit(self, live, stage):
        live.stage, live.stage_since = stage, self.clock()
        live.taps += 1

    def _twice(self, live, key, value):
        """True when ``value`` was also seen for ``key`` on the previous recognition round (two fresh frames)."""
        previous = live.confirm.get(key)
        if previous is None or previous[1] != live.rounds:
            same = previous is not None and previous[0] == value and previous[1] == live.rounds - 1
            live.confirm[key] = (value, live.rounds, same)
        return live.confirm[key][2]

    def _retap(self, live, kind, turn=None, **details):
        """Record the one allowed re-tap of this decision; the stuck timer restarts from it."""
        turn = live.turn if turn is None else turn
        if not self._log({"event": "live_retap", "turn": turn, "tap": kind, **details}):
            self.live_unfinished("log_write_failed")
            return False
        live.retaps.add((turn, kind))
        live.stage_since = live.pair_since = self.clock()
        live.taps += 1
        self.run.note({"live": "retap", "tap": kind, "turn": turn, **details})
        return True

    @staticmethod
    def _state_sig(state):
        return (state.turn, tuple(dataclasses.astuple(j) for j in state.judges), tuple(c.appeal for c in state.cards))

    def live_unfinished(self, reason, **extra):
        run = self.run
        live = run.live
        record = {"event": "live_unfinished", **(run.intent or {}), "reason": reason,
                  "live": live.summary() if live else None, **extra}
        if run.saver is not None:
            record["frames"] = run.saver.saved[-3:]
        self._log(record)
        if live is not None and live.report is not None and live.ok_pressed and reason not in gt.REPORTED_FAILURE_REASONS:
            # The LIVE REPORT of this spent right was read and closed: the live is played; only the way back is
            # unverified (logged above as live_unfinished, never retried).
            run.finish("played_reported", reason, "live_report " + ", ".join(f"{k}={v}" for k, v in live.report.items()))
            return True
        run.finish("live_unfinished", reason)
        return True

    def screen_kind(self, live, image):
        """film | report | ranking | record | timing | memory_wait | target | wait | anim | None (cached per frame)."""
        def classify():
            film = reader.read_film(image)
            if film is not None:
                live.cache["film"] = film
                return "film"
            vision = self.vision(self.context_for_live)
            if vision.hit(self.report_guard(), image):
                return "report"
            if vision.hit(RANKING_GUARD, image):
                return "ranking"
            if vision.hit(RECORD_GUARD, image):
                return "record"
            if not vision.hit(SCREEN_GUARD, image):
                return None
            trend = vision.hit(TREND_WORD, image)
            if vision.hit(APPEAL_BAR, image) and not trend:
                return "timing"
            if not trend:
                return "memory_wait" if vision.hit(MEMORY_ARROW, image) else "anim"
            return "target" if self.swap_slot(image) is not None else "wait"
        return self._cached(live, image, "kind", classify)

    def memory_arrow_judge(self, image):
        """Judge whose panel row holds the memory arrow's centre, or None."""
        detail = self.context_for_live.run_recognition(MEMORY_ARROW, image)
        if detail is None or not detail.hit or detail.box is None:
            return None
        y = int(detail.box[1]) + int(detail.box[3]) // 2
        return next((name for name, top, bottom in JUDGE_BANDS if top <= y < bottom), None)

    def swap_slot(self, image):
        detail = self.context_for_live.run_recognition(SWAP_HINT, image)
        if detail is None or not detail.hit or detail.box is None:
            return None
        return reader.swap_hint_slot(int(detail.box[0]))

    # -- recognition entry point (``SC.Grade.Live``)
    def live(self, context, image, p):
        run = self.run
        if run is None:
            return False
        step = p.get("step")
        if step == "enter":
            return run.phase == "awaiting_result" and not run.finished
        if not self.live_enabled():
            return False
        self.context_for_live = context
        live = self._live()
        if step == "after_report":
            return live.stage in OK_STAGES
        if step == "report":
            if live.stage in OK_STAGES and self.screen_kind(live, image) == "ranking":
                return self.live_ranking(live, image)
            return self.live_report(live, image)
        if step == "record":
            return self.live_record(live, image)
        kind = self.screen_kind(live, image)
        if kind is None or kind in ("report", "ranking", "record"):
            return False
        live.seen = True
        if step in ("speed", "card", "memory"):
            return self.live_wait_step(live, image, kind, step, p)
        if step == "memory_go":
            return self.live_memory_go(live, image, kind, p.get("judge"))
        if step == "film":
            return self.live_memory_film(live, kind)
        if step == "judge":
            return self.live_judge(live, image, kind, p.get("judge"))
        if step == "timing":
            memory_direct = live.stage == "card_tapped" and live.plan is not None and live.plan.kind == "memory"
            if kind != "timing" or not (live.stage == "judge_tapped" or memory_direct):
                return False  # (memory appeal may skip the judge choice: its flow is not sampled yet)
            if run.config.live_timed_click and not grade_timing.prepare(
                    self, live, image, live.frame, memory=live.plan is not None and live.plan.kind == "memory"):
                return False  # pool another frame of the same bar before the host-timed tap
            self._commit(live, "timing_tapped")
            run.note({"live": "timing", "turn": live.turn})
            return True
        return False

    def turn_limit(self, live=None):
        """Rehearsal 10; official Grade: the total already read in this live, else the candidates (new season 6, earlier 7)."""
        if self.run.config.mode == "rehearsal":
            return 10
        return live.max_turns if live is not None and live.max_turns else reader.GRADE_TURN_TOTALS

    def _wait_state(self, live, image):
        result = self._cached(live, image, "state", lambda: reader.read_turn(self.vision(self.context_for_live), image, max_turns=self.turn_limit(live)))
        state = result[0]
        if state is not None and not live.max_turns:
            live.max_turns = state.max_turns
            self.run.note({"live": "turn_total", "max_turns": state.max_turns})
        return result

    def live_wait_step(self, live, image, kind, step, p):
        run = self.run
        if kind == "wait" and live.stage == "card_tapped" and step in ("card", "memory"):
            return self.live_card_retap(live, image, p if step == "card" else None)
        if kind != "wait" or live.stage not in ("wait", "timing_tapped", "memory_go_tapped"):
            return False
        state, raw, reasons = self._wait_state(live, image)
        digest = live.frame
        if state is None:
            if digest not in live.unreadable_digests:
                live.unreadable_digests.add(digest)
                live.unreadable += 1
                run.note({"live": "unreadable", "reasons": reasons})
            if live.unreadable > gt.LIVE_MAX_UNREADABLE:
                self.live_unfinished("turn_state_unreadable", reasons=reasons)
            live.prev_sig = None
            return False
        if state.turn <= live.turn:
            return False  # still the previous turn on screen (stuck detection decides)
        if live.turn and state.turn != live.turn + 1:
            self.live_unfinished("turn_skipped", seen=state.turn, expected=live.turn + 1)
            return False
        if len(live.turns) >= (live.max_turns or policy.MAX_TURNS):
            self.live_unfinished("turn_cap_reached")
            return False
        # Stability: the same state (labels LINK/PLUS sparkle and the memory gauge animates: not compared) in two
        # consecutive recognition rounds (two screenshots).
        sig = self._state_sig(state)
        if live.sig_round != live.rounds:
            live.stable = live.prev_sig == sig and live.sig_round == live.rounds - 1
            live.prev_sig, live.sig_round = sig, live.rounds
        if not live.stable:
            return False
        speed_off = self._cached(live, image, "speed_off",
                                 lambda: self.vision(self.context_for_live).hit(SPEED_OFF, image))
        now = self.clock()
        speed_retapped = any(k == "speed" for _, k in live.retaps)
        if step == "speed":
            if run.config.live_speed != "on" or live.speed_result is not None:
                return False
            if not live.speed_tapped:
                if not speed_off:
                    live.speed_result = "already_on"
                    return False
                live.speed_tapped, live.speed_since, live.taps = True, now, live.taps + 1
                run.note({"live": "speed_toggle", "turn": state.turn})
                return True
            # 倍速 tapped but 「倍速OFF」 still shown: one re-tap after LIVE_RETAP_SECONDS on two fresh unchanged frames.
            if (speed_off and not speed_retapped and now - live.speed_since >= gt.LIVE_RETAP_SECONDS
                    and self._twice(live, "speed", (sig, True))):
                if self._retap(live, "speed", turn=state.turn):
                    live.speed_since = now
                    return True
            return False
        if live.speed_tapped and live.speed_result is None:
            if not speed_off:
                live.speed_result = "changed"
            elif now - live.speed_since < gt.LIVE_RETAP_SECONDS or not speed_retapped:
                return False  # wait for the 倍速 effect (the Speed step may re-tap once) before choosing a card
            else:
                live.speed_result = "off_still_shown"
        decision = live.cache.get("decision")
        if decision is None:
            decide = getattr(self, "live_decide", None) or policy.decide
            decision = live.cache["decision"] = decide(state)
        if decision.kind == "none":
            self.live_unfinished("policy_refused", reasons=list(decision.reasons))
            return False
        wanted = (step == "memory" and decision.kind == "memory") or (
            step == "card" and decision.kind == "card" and decision.slot == p.get("slot"))
        if not wanted:
            return False
        explanation = policy.explain(state, decision)
        record = {"event": "live_turn", "turn": state.turn, "decision": decision.to_dict(), "explain": explanation,
                  "memory_gauge": state.memory_gauge, "cards": raw.get("cards")}
        if not self._log(record):
            self.live_unfinished("log_write_failed")
            return False
        live.turn, live.plan, live.plan_state, live.prev_pred, live.unreadable = state.turn, decision, state, None, 0
        live.turns.append({"turn": state.turn, "kind": decision.kind, "slot": decision.slot, "plan": decision.target})
        run.note({"live": "turn", "explain": explanation})
        self._commit(live, "card_tapped")
        return True

    def live_judge(self, live, image, kind, attribute):
        run = self.run
        if live.stage == "judge_tapped" and kind == "target":
            return self.live_judge_retap(live, image, attribute)
        if live.stage != "card_tapped" or kind not in ("target", "wait"):
            return False
        plan, state = live.plan, live.plan_state
        if plan.kind == "card":
            if kind != "target":
                return False
            slot = self._cached(live, image, "slot", lambda: self.swap_slot(image))
            if slot != plan.slot:
                self.live_unfinished("selected_card_differs", selected=slot, planned=plan.slot)
                return False
        predicted = self._cached(live, image, "pred",
                                 lambda: reader.read_predictions(self.vision(self.context_for_live), image))
        if plan.kind == "memory" and not any(
                predicted.get(j.attribute) is not None and predicted[j.attribute] > (j.interest or 0)
                for j in state.alive()):
            return False  # memory: no prediction shown yet (target stage not proven)
        if live.pred_round != live.rounds:  # same predictions in two consecutive rounds
            live.pred_stable = live.prev_pred == predicted and live.pred_round == live.rounds - 1
            live.prev_pred, live.pred_round = predicted, live.rounds
        if not live.pred_stable:
            return False
        target = live.cache.get("target")
        if target is None:
            target = live.cache["target"] = policy.choose_target(state, plan, predicted)
        chosen, explanation = target
        if attribute != chosen:
            return False
        if not self._log({"event": "live_target", "turn": live.turn, "target": chosen, **explanation}):
            self.live_unfinished("log_write_failed")
            return False
        live.turns[-1]["target"] = chosen
        run.note({"live": "target", "turn": live.turn, "target": chosen, "rule": explanation["rule"]})
        live.judge_target, live.judge_pred = chosen, dict(predicted)
        self._commit(live, "judge_tapped")
        return True

    def live_memory_film(self, live, kind):
        """Memory film: one tap that stops the strip, when the tile reaching the frame next reads Good (or at the deadline)."""
        plan = live.plan
        if kind != "film" or live.stage != "judge_tapped" or plan is None or plan.kind != "memory":
            return False
        film = live.cache.get("film")
        now = self.clock()
        if live.cache.get("film_round") is None:  # one film frame counted once per screenshot
            live.cache["film_round"] = live.film_rounds = live.film_rounds + 1
            if live.film_first is None:
                live.film_first = now
            live.film_seen.append(dict(film))
        if film.get("right2") == "good":
            rule = "lookahead_good"
        elif live.film_rounds >= gt.LIVE_FILM_MAX_ROUNDS or now - live.film_first >= gt.LIVE_FILM_DEADLINE_SECONDS:
            rule = "deadline"
        else:
            return False
        record = {"event": "live_memory_film", "turn": live.turn, "rule": rule, "tiles": film,
                  "film_rounds": live.film_rounds, "since_first": round(now - live.film_first, 2)}
        if not self._log(record):
            self.live_unfinished("log_write_failed")
            return False
        self.run.note({"live": "memory_film", "turn": live.turn, "rule": rule})
        live.turns[-1]["film"] = rule
        self._commit(live, "film_tapped")
        return True

    def live_memory_go(self, live, image, kind, attribute):
        """After the memory appeal's judge and film the turn waits for one tap: tap the arrowed (= chosen) judge once."""
        plan = live.plan
        if kind != "memory_wait" or plan is None or plan.kind != "memory" or attribute != live.judge_target:
            return False
        arrow = self._cached(live, image, "arrow", lambda: self.memory_arrow_judge(image))
        if live.stage in ("judge_tapped", "film_tapped"):
            if arrow != attribute:
                self.live_unfinished("memory_arrow_differs", arrow=arrow, target=attribute)
                return False
            if self.clock() - live.stage_since < gt.LIVE_MEMORY_GO_SETTLE_SECONDS or not self._twice(live, "memory_go", arrow):
                return False
            if not self._log({"event": "live_memory_go", "turn": live.turn, "target": attribute}):
                self.live_unfinished("log_write_failed")
                return False
            self.run.note({"live": "memory_go", "turn": live.turn, "target": attribute})
            self._commit(live, "memory_go_tapped")
            return True
        if (live.stage != "memory_go_tapped" or arrow != attribute or (live.turn, "memory_go") in live.retaps
                or self.clock() - live.stage_since < gt.LIVE_RETAP_SECONDS):
            return False
        if not self._twice(live, "memory_go_retap", arrow):
            return False
        return self._retap(live, "memory_go", target=attribute)

    def live_card_retap(self, live, image, p):
        """The planned card (``p``) or memory appeal (``p`` None) tap left the waiting screen unchanged (same TURN,
        judges without predictions, cards): re-tap it once (two fresh frames)."""
        plan = live.plan
        tap = "card" if p is not None else "memory"
        if (plan is None or plan.kind != tap or (p is not None and p.get("slot") != plan.slot)
                or (live.turn, tap) in live.retaps or self.clock() - live.stage_since < gt.LIVE_RETAP_SECONDS):
            return False
        state = self._wait_state(live, image)[0]
        if state is None or self._state_sig(state) != self._state_sig(live.plan_state):
            return False
        if not self._twice(live, tap, self._state_sig(state)):
            return False
        return self._retap(live, tap, **({"slot": plan.slot} if tap == "card" else {"target": plan.target}))

    def live_judge_retap(self, live, image, attribute):
        """The judge tap left the target stage unchanged: re-tap the same judge once (two fresh frames)."""
        plan = live.plan
        if (plan is None or plan.kind != "card" or attribute != live.judge_target or (live.turn, "judge") in live.retaps
                or self.clock() - live.stage_since < gt.LIVE_RETAP_SECONDS):
            return False
        slot = self._cached(live, image, "slot", lambda: self.swap_slot(image))
        predicted = self._cached(live, image, "pred",
                                 lambda: reader.read_predictions(self.vision(self.context_for_live), image))
        if slot != plan.slot or predicted != live.judge_pred:
            return False
        if not self._twice(live, "judge", (slot, tuple(sorted(predicted.items())))):
            return False
        return self._retap(live, "judge", target=attribute)

    def live_report(self, live, image):
        run = self.run
        vision = self.vision(self.context_for_live)
        if live.stage in OK_STAGES:
            return self.live_report_retap(live, image) if live.stage == "report_tapped" else False
        if not vision.hit(self.report_guard(), image):
            return False
        live.seen = True
        now = self.clock()
        values, raw = self._cached(live, image, "report", lambda: reader.read_report(vision, image, mode=run.config.mode))
        if live.report_first is None:
            live.report_first = now
        for flag, node in (("money_2x_badge", REPORT_MONEY2X), ("mission_reward_row", REPORT_MISSION)):
            # The badge sparkles (misses on ~1 frame in 5): a flag seen on any report frame counts.
            live.report_flags[flag] = live.report_flags.get(flag, False) or self._cached(
                live, image, flag, lambda node=node: vision.hit(node, image))
        complete = all(values.get(k) is not None for k in REPORT_FIELDS)
        if not complete or values != live.report_prev:
            live.report_stable_since = now if complete else None
        live.report_prev = values
        settled = complete and now - live.report_stable_since >= gt.LIVE_REPORT_STABLE_SECONDS
        if not settled and now - live.report_first < gt.LIVE_REPORT_SETTLE_SECONDS:
            return False
        extras = {**live.report_flags, "settled": "stable" if settled else "settle_time_elapsed"}
        live.report = {**values, **extras}
        if not self._log({"event": "live_report", **values, **extras, "raw": raw, "turns": len(live.turns),
                          "report_takeover": (run.intent or {}).get("stage") == gt.REPORT_TAKEOVER_STAGE}):
            self.live_unfinished("log_write_failed")
            return False
        run.note({"live": "report", **values, **extras})
        self._commit(live, "report_tapped")
        live.report_tapped_at, live.ok_pressed = self.clock(), True
        return True

    def live_record(self, live, image):
        """「NEW RECORD」/「グレードランキング」 overlay over LIVE REPORT: one tap on its band, one re-tap at most."""
        run = self.run
        if live.stage in OK_STAGES or self.screen_kind(live, image) != "record":
            return False
        live.seen = True
        now = self.clock()
        if live.record_first is None:
            live.record_first = now
            run.note({"live": "record_overlay_seen"})
        if not self._twice(live, "record", "record"):
            return False
        if live.record_taps == 0:
            if now - live.record_first < gt.LIVE_RECORD_SETTLE_SECONDS:
                return False  # it may still be animating in (never seen to auto-advance)
            if not self._log({"event": "live_record_overlay", "turns": len(live.turns),
                              "seen_seconds": round(now - live.record_first, 1)}):
                self.live_unfinished("log_write_failed")
                return False
            live.taps += 1
        elif (live.record_taps == 1 and now - live.record_tapped_at >= gt.LIVE_RETAP_SECONDS
              and not any(k == "record" for _, k in live.retaps)):
            if not self._retap(live, "record"):
                return False
        else:
            return False
        live.record_taps += 1
        live.record_tapped_at = now
        # The report under the overlay is read afresh (stable readings again) once the overlay is gone.
        live.report_first = live.report_prev = live.report_stable_since = None
        return True

    def live_report_retap(self, live, image):
        """OK left LIVE REPORT unchanged: press OK once more (>= LIVE_RETAP_SECONDS, two fresh frames, same readings)."""
        if any(k == "report" for _, k in live.retaps) or self.clock() - live.stage_since < gt.LIVE_RETAP_SECONDS:
            return False
        vision = self.vision(self.context_for_live)
        if not vision.hit(self.report_guard(), image):
            return False
        values = self._cached(live, image, "report", lambda: reader.read_report(vision, image, mode=self.run.config.mode))[0]
        if any(values.get(k) is not None and values.get(k) != live.report.get(k) for k in REPORT_FIELDS):
            return False  # other numbers than the ones OK was pressed on: never press it again
        if not self._twice(live, "report", tuple(values.get(k) for k in REPORT_FIELDS)):
            return False
        return self._retap(live, "report")

    def live_ranking(self, live, image):
        """B5k: the ranking page after the report OK (or at a ranking takeover): record the own row, press OK once."""
        run = self.run
        reading, raw = self._cached(live, image, "ranking", lambda: reader.read_ranking(
            self.vision(self.context_for_live), image))
        signature = tuple(reading.get(k) for k in ("rank", "pt", "row"))
        now = self.clock()
        if live.stage == "ranking_tapped":
            # OK left the ranking page unchanged: once more after LIVE_RETAP_SECONDS on two fresh frames, same row.
            if (any(k == "ranking" for _, k in live.retaps) or now - live.stage_since < gt.LIVE_RETAP_SECONDS
                    or reading != live.ranking_reading):
                return False
            if not self._twice(live, "ranking_retap", signature):
                return False
            return self._retap(live, "ranking")
        if live.ranking_first is None:
            live.ranking_first = now
            run.note({"live": "ranking_seen"})
        stable = self._twice(live, "ranking", signature)
        if now - live.ranking_first < gt.LIVE_RANKING_SETTLE_SECONDS or not stable:
            return False
        live.ranking, live.ranking_reading = dict(reading), dict(reading)
        score = (live.report or {}).get("score")
        record = {"event": "live_ranking", **reading, "raw": raw, "score": score,
                  "pt_matches_score": None if score is None or reading["pt"] is None else reading["pt"] == score,
                  "ranking_takeover": (run.intent or {}).get("stage") == gt.RANKING_TAKEOVER_STAGE}
        if not self._log(record):
            self.live_unfinished("log_write_failed")
            return False
        run.note({"live": "ranking", **reading})
        self._commit(live, "ranking_tapped")
        live.report_tapped_at, live.ok_pressed = self.clock(), True
        return True

    # -- observation (called from watch_live on every frame after ライブ開始)
    def live_watch(self, context, image, now):
        """True when the live ended this task (finished); False to keep observing."""
        self.context_for_live = context
        live = self._live()
        live.rounds += 1
        kind = self.screen_kind(live, image)
        if kind == "film" and live.stage == "film_tapped" and len(live.film_after) < 4:
            live.film_after.append(dict(live.cache["film"], t=round(now - live.stage_since, 2)))
        if kind is not None:
            live.seen = True
            if live.active_since is None:
                live.active_since = now
            if kind not in ("report", "ranking") or live.stage not in OK_STAGES:
                self.run.last_change = now  # the live screen moves anyway; the static rule is for unknown screens
        pair = (live.stage, kind)
        if pair != live.pair:
            live.pair, live.pair_since = pair, now
        if kind == "ranking" and live.stage not in OK_STAGES:
            # The ranking page follows this run's report OK (or starts a ranking takeover); anything else is unknown.
            return self.live_unfinished("ranking_without_report_ok", stage=live.stage)
        stuck = pair in STUCK_PAIRS
        if live.stage in ("timing_tapped", "memory_go_tapped") and kind == "wait":
            state = self._wait_state(live, image)[0]
            # All judges 審査終了: the live ends early (before TURN 7); the end screens follow, nothing is stuck.
            stuck = state is not None and state.turn == live.turn and bool(state.alive())
        if stuck and now - live.pair_since > gt.LIVE_STUCK_SECONDS:
            return self.live_unfinished(f"{live.stage}_no_effect", screen=kind)
        if (kind == "record" and live.record_taps >= 2
                and now - live.record_tapped_at > gt.LIVE_STUCK_SECONDS):
            return self.live_unfinished("record_overlay_no_effect", screen=kind)
        if live.stage in OK_STAGES:
            # After OK (the ranking page's OK restarts the bound): only the shared popup whitelist / Loading act (AfterReport); unknown screens are never clicked.
            if now - live.report_tapped_at > gt.LIVE_AFTER_REPORT_SECONDS:
                return self.live_unfinished("fes_main_not_reached_after_report", screen=kind)
            return False
        since = live.active_since if live.active_since is not None else live.started
        if now - since > gt.LIVE_WATCH_SECONDS:
            return self.live_unfinished("live_time_cap", active_seconds=round(now - since, 1))
        return False
