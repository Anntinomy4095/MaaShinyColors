"""Daily Grade Fes (migration batch B5): pure rules and per-run state. No Maa import, no input.

Live facts (2026-10-02): フェス -> グレード&リハ -> Fes main (first visit: フェスについて) ->
フェスに参加する -> グレードフェス -> difficulty dialog. Tapping a difficulty does NOT consume anything (rights stayed
20/20): it opens 「参加ユニット(<difficulty>)」 with 「テンション決定」 and the note 「テンション決定後は前の画面には戻れません」
「フェス参加権を1消費します」.

B5c (live 2026-10-02 23:34): テンション決定 does NOT consume either (rights still 20/20) but is irreversible: it reveals
the tension faces and turns the same button into 「ライブ開始」 (TensionDecided stage). ライブ開始 is the consuming
click. Both presses go through the one table action ``grade-tension-confirm`` (same rect; the host requires a
one-shot grant for each); the Agent decides per stage which press it is. With ``live_play`` enabled the live is
played and only a verified Fes-main return allows the next remaining daily live. Otherwise a recognised live
hands control to the player immediately, without further clicks or marking the live complete.

Attempts (one per テンション決定, legacy B5 intents included) are reconstructed from the day's log; an open attempt
that stopped at TensionDecided may be resumed only when a fresh frame shows TensionDecided with unchanged rights.

Rights (フェス参加権) are shared with Match/rehearsal, regenerate 1 per 30 min up to 20.
まとめて消費 (bulk) is never used: the difficulty page must show the unticked box and 「フェス参加権を1消費します」.
"""
from __future__ import annotations

import dataclasses
import re

from agent.core import fes_allocation as fa
from agent.core import task_common as tc

DIFFICULTIES = ("Easy", "Normal", "Hard")
# The table action of the 参加ユニット primary button: テンション決定 (irreversible, non-consuming) and, on the
# TensionDecided stage, ライブ開始 (consumes 1 フェス参加権). Each press needs its own one-shot grant.
SPEND_ACTION = "grade-tension-confirm"
REHEARSAL_SPEND_ACTIONS = {d: "grade-rehearsal-" + d.lower() for d in ("Easy", "Normal", "Hard")}
REHEARSAL_APPROVERS = {d: "SC.Grade.Check.Rehearsal" + d for d in ("Easy", "Normal", "Hard")}
REHEARSAL_STAGE = "RehearsalDifficulty"
APPROVER = "SC.Grade.Check.Unit"
LIVE_APPROVER = "SC.Grade.Check.LiveStart"
MAX_UNREADABLE_FRAMES = 3
# 2026-10-12 (grade-auto2): after 「フェスに参加する」 and before テンション決定 the run is inside Grade's own dialogs. A frame
# no Grade node accepts there (pop-in, an OCR retry) must wait here, not reach the shared SC.Nav.BackOne, whose Grade
# difficulty / 参加ユニット back keys would otherwise leave the flow. Bounded; then the run stops (no click).
FLOW_PHASES = ("joined", "mode_tapped", "difficulty_tapped")
FLOW_HOLD_FRAMES = 8
FLOW_HOLD_SAMPLE_LIMIT = FLOW_HOLD_FRAMES + 1
STOP_SAMPLE_FRAMES = 4       # recent distinct frames kept in memory and saved when a run ends without success
TENSION_WATCH_SECONDS = 30   # テンション決定 -> TensionDecided (observed within ~1.5 s live)
TENSION_FRAME_LIMIT = 20
WATCH_SECONDS = 300          # ライブ開始 -> a recognisable screen with rights - 1 (performance/result unknown)
WATCH_FRAME_LIMIT = 200      # frames are saved every >=1.5 s and only when the screen changed (dedupe)
LIVE_FRAME_LIMIT = 500       # live play: up to LIVE_WATCH_SECONDS of evidence
SAVE_INTERVAL_SECONDS = 1.5
STATIC_SECONDS = 20          # an unrecognised screen that does not change for 20 s may wait for input: stop
CHANGE_THRESHOLD = 1.0       # mean abs difference (0-255) of the 1/8 grey thumbnail that counts as a change
LIVE_STAGE = "TensionDecided"
# B5d live play (opt-in ``live_play``): after ライブ開始 the task plays the live turn by turn (card -> judge -> timing tap,
# all non-consuming), presses OK on LIVE REPORT and verifies Fes main with rights - 1.
# B5j: the cap counts active play only — from the first recognised live screen (turn or LIVE REPORT) until this run's
# LIVE REPORT OK; waiting before it (loading, フェス再開, the takeover check) is excluded. After OK, separate bounds apply.
LIVE_WATCH_SECONDS = 1800
LIVE_AFTER_REPORT_SECONDS = 60   # LIVE REPORT OK -> Fes main with readable rights (popups via the shared whitelist only)
LIVE_STUCK_SECONDS = 8       # a tap whose next stage did not appear within this time: stop, never repeat it
LIVE_REPORT_SETTLE_SECONDS = 10  # LIVE REPORT numbers count up: press OK on stable complete readings or after this time
# B5j (live 2026-10-03 10:16): OK pressed ~1 s after the report appeared (所持マニー still counting, mission rewards still
# being revealed) had no effect. The readings (incl. 所持マニー) must be complete and unchanged for this long first.
LIVE_REPORT_STABLE_SECONDS = 3.0
LIVE_MAX_UNREADABLE = 12     # waiting-stage frames whose state could not be read (animations) before giving up
LIVE_SPEEDS = ("on", "keep")
REHEARSAL_MAX_RUNS = 20
MAX_RUNS_PER_DAY = 20        # UI daily target; a batch plays the remaining verified target, within the sample cap
MAX_SAMPLE_RUNS = 20
MODES = ("grade", "rehearsal")
BULK_COUNTS = (1, 2, 3, 4, 5)  # まとめて消費 1-5 rights per live; only 1 (unticked) is sampled
NOT_SPENT_TENSION_DECIDED = "not_spent_tension_decided"
# B5h: after a host restart the game shows 「フェス再開」 (a live in progress, its right already spent) when Fes main opens.
# Only 「再開」 (``grade-fes-resume``, non-consuming) is pressed, once per run; 「終了」 forfeits the spent right and is not
# an action. The resumed live is played by the same live loop; it must show a live screen within RESUME_WATCH_SECONDS.
RESUME_STAGE = "FesResume"
RESUME_WATCH_SECONDS = 15
RESUME_PHASES = ("active", "joined", "mode_tapped", "difficulty_tapped")  # no テンション決定 sent by this run
# B5i: GradeFes started while a live turn screen is already shown (e.g. a previous run stopped mid-live): with no click
# the run takes the live over (log ``live_takeover``, like ``resume``: its right was spent before) and plays it on.
TAKEOVER_STAGE = "LiveTurn"
# B5j: GradeFes started on LIVE REPORT (a previous run's OK had no effect): take over at the report (log
# ``report_takeover``, recorded like ``resume``), then only OK and the way back to Fes main.
REPORT_TAKEOVER_STAGE = "LiveReport"
# B5k (live 2026-10-03 11:09): after the first LIVE REPORT OK a second LIVE REPORT page lists the ranking (rows with
# スコア詳細/ユニット詳細, OK at the same place). GradeFes started on that page is taken over the same way (log
# ``ranking_takeover``): only its OK and the way back to Fes main.
RANKING_TAKEOVER_STAGE = "LiveRanking"
TAKEOVER_EVENTS = ("resume", "live_takeover", "report_takeover", "ranking_takeover")
# B5k: the ranking page's OK (same ``live-report-ok`` action) only after the page has been shown this long and the own
# row reads the same on two fresh frames.
LIVE_RANKING_SETTLE_SECONDS = 2.0
# B5h live robustness: the live canvas can drop a tap. A card, judge or 倍速 tap without visible effect may be repeated
# ONCE after LIVE_RETAP_SECONDS, when two fresh frames still show the same stage and state (never the timing tap).
LIVE_RETAP_SECONDS = 3.0
# 2026-10-22: the post-memory-appeal turn screen (arrow at the chosen judge) must show this long before its one tap.
LIVE_MEMORY_GO_SETTLE_SECONDS = 2.0
# 2026-10-24 memory film (~2.5 s, no tap = miss): tap when the tile about to reach the frame reads Good, at the latest on
# the LIVE_FILM_MAX_ROUNDS-th film frame or LIVE_FILM_DEADLINE_SECONDS after the first one.
LIVE_FILM_MAX_ROUNDS = 3
LIVE_FILM_DEADLINE_SECONDS = 1.0
# 2026-10-12: 「NEW RECORD」/「グレードランキング」 overlay over LIVE REPORT (unchanged for 105 s live: needs a tap);
# tapped once it has been shown this long on two fresh frames.
LIVE_RECORD_SETTLE_SECONDS = 2.0

# Light text on the rights bar ("20/20 +0"); only the "current/max" part is read.
RIGHTS_FIELDS = {"fes_main": [1145, 665, 42, 16], "difficulty": [1145, 665, 42, 16], "unit": [1137, 669, 48, 18]}
RIGHTS_PATTERN = r"[0-9]{1,2}/[0-9]{1,2}"
TEXT_BOXES = {"difficulty_title": [232, 88, 815, 46], "difficulty_prompt": [830, 572, 420, 44],
              "unit_title": [50, 18, 290, 40], "unit_notice": [628, 632, 460, 60],
              # Batch 4: the own unit's evaluation under the rank badge (「21801」, x ~52..95, y ~415..429 on the
              # 2026-10-02 参加ユニット frame); the user's chosen Fes unit is bound by this number.
              "unit_score": [40, 408, 72, 26]}
# The Fes unit's evaluation must be a plain 3-6 digit number (no comma on 参加ユニット; the Fes main shows 「21,801」).
UNIT_SCORE_PATTERN = r"^[0-9]{3,6}$"
DIFFICULTY_TITLE = "グレードフェスの難易度を選択してください"
REHEARSAL_DIFFICULTY_TITLE = "フェスリハーサルの難易度を選択してください"
ONE_RIGHT = "フェス参加権を1消費します"
NO_RETURN = "テンション決定後は前の画面には戻れません"

# 2026-10-12: ``played_reported`` = the right was spent and this run read the LIVE REPORT and pressed its OK, but the
# way back to Fes main (rights - 1) was not observed: the live is played (counted via ``live_report``), not retried.
SUCCESS_OUTCOMES = frozenset({"played", "played_reported", "nothing_to_do", "skipped_rights"})
# Contradictions that keep a reported live a failure (the evidence disagrees with the spend).
REPORTED_FAILURE_REASONS = frozenset({"fes_main_rights_not_decremented", "log_write_failed"})
FAILURE_OUTCOMES = frozenset({"config_invalid", "rehearsal_not_sampled", "bulk_not_sampled", "recovery_policy_disabled",
                              "recovery_cap_exceeds_policy", "recovery_not_implemented", "rights_unreadable", "difficulty_mismatch", "unit_mismatch",
                              "spend_not_enabled", "unexpected_unit", "spend_uncertain", "spend_refused",
                              "log_write_failed", "timeout", "tension_uncertain", "unexpected_tension_decided",
                              "attempt_unresolved", "live_unfinished", "fes_resume_pending",
                              "unexpected_fes_resume", "live_takeover_refused"})
HALT_OUTCOMES = frozenset({"spend_uncertain", "spend_refused", "timeout", "tension_uncertain", "live_unfinished", "played_reported", "manual_takeover",
                           "fes_resume_pending", "unexpected_fes_resume", "live_takeover_refused"})


@dataclasses.dataclass(frozen=True)
class GradeConfig:
    """Daily completion target and optional developer attempt cap (0 follows the daily target)."""
    difficulty: str = "Normal"
    runs_per_day: int = 1
    rights_reserve: int = 0
    bulk: bool = False
    samples_runs_per_day: int = 0
    live_play: bool = False      # B5d: play the live after ライブ開始 (off: read-only observation as in B5c)
    live_speed: str = "on"       # turn 倍速 ON once when it shows 倍速OFF ("keep": never touch it)
    # Rehearsal spends one right at difficulty selection and directly enters a ten-turn live; select one mode.
    rehearsal_runs: int = 0
    use_recovery_tickets: bool = False   # separate resource; refused while project_policy fes_tickets is disabled
    recovery_cap: int = 1
    # Formal Grade only: the user's chosen Fes unit, bound by its evaluation on 参加ユニット (e.g. 「21801」).
    # "" = not bound (read and reported only). Any other value must equal the fresh reading before テンション決定,
    # else the run goes back without spending (``unit_mismatch`` / ``unit_score_mismatch``); re-enter it after the
    # unit, its cards or their levels change (the number changes with them).
    unit_score: str = ""
    # 2026-10-09 (UI: one mode selector + one count): ``mode`` "grade" plays グレードフェス ``runs_per_day`` times;
    # "rehearsal" plays the same count as フェスリハーサル (``rehearsal_runs``).
    mode: str = "grade"
    # まとめて消費: rights per live, 1-5. Only 1 (box unticked, verified on the difficulty page) is sampled; any larger
    # value stops before the first click (``bulk_not_sampled``) and is planned as ``runs × bulk_count`` rights.
    bulk_count: int = 1
    # 2026-10-24 (grade_timing): host-timed APPEAL stop tap; off = the untimed tap. Needs a SC.AppealTiming.v2 host.
    live_timed_click: bool = False

    @property
    def spend_action(self):
        return REHEARSAL_SPEND_ACTIONS[self.difficulty] if self.mode == "rehearsal" else SPEND_ACTION

    @property
    def grade_runs(self):
        """グレードフェス lives wanted today (0 in rehearsal mode)."""
        return self.runs_per_day if self.mode == "grade" else 0

    @property
    def attempt_limit(self):
        return self.samples_runs_per_day or self.runs_per_day

    @classmethod
    def from_params(cls, params):
        tc.require(isinstance(params, dict), "Grade 配置必须为对象")
        allowed = {"difficulty", "runs_per_day", "rights_reserve", "bulk", "samples_runs_per_day", "live_play",
                   "live_speed", "rehearsal_runs", "use_recovery_tickets", "recovery_cap", "unit_score", "mode",
                   "bulk_count", "live_timed_click"}
        tc.require(set(params) <= allowed, "Grade 配置含未知字段: " + ",".join(sorted(set(params) - allowed)))
        difficulty = params.get("difficulty", "Normal")
        tc.require(difficulty in DIFFICULTIES, "难度只能为 Easy/Normal/Hard（区分大小写）")
        runs = params.get("runs_per_day", 1)
        tc.require(type(runs) is int and 1 <= runs <= MAX_RUNS_PER_DAY, f"runs_per_day 必须为 1–{MAX_RUNS_PER_DAY} 的整数")
        reserve = params.get("rights_reserve", 0)
        tc.require(type(reserve) is int and 0 <= reserve <= 19, "rights_reserve 必须为 0–19 的整数")
        tc.require(params.get("bulk", False) is False, "まとめて消費 永远关闭")
        samples = params.get("samples_runs_per_day", 0)
        tc.require(type(samples) is int and 0 <= samples <= MAX_SAMPLE_RUNS,
                   f"samples_runs_per_day 必须为 0–{MAX_SAMPLE_RUNS} 的整数（0 跟随每日局数）")
        live = params.get("live_play", False)
        tc.require(type(live) is bool, "live_play 必须为布尔值")
        speed = params.get("live_speed", "on")
        tc.require(speed in LIVE_SPEEDS, "live_speed 只能为 on/keep")
        rehearsal = params.get("rehearsal_runs", 0)
        tc.require(type(rehearsal) is int and 0 <= rehearsal <= REHEARSAL_MAX_RUNS,
                   f"rehearsal_runs 必须为 0–{REHEARSAL_MAX_RUNS} 的整数")
        recovery = params.get("use_recovery_tickets", False)
        tc.require(type(recovery) is bool, "use_recovery_tickets 必须为布尔值")
        cap = params.get("recovery_cap", 1)
        tc.require(type(cap) is int and cap in fa.RECOVERY_CAPS, "recovery_cap 只能为 1/2/3")
        unit_score = params.get("unit_score", "")
        tc.require(isinstance(unit_score, str) and (unit_score == "" or re.fullmatch(UNIT_SCORE_PATTERN, unit_score)),
                   "unit_score 只能为空（不绑定）或 3–6 位数字（参加ユニット 显示的编队评价值）")
        mode = params.get("mode", "grade")
        tc.require(mode in MODES, "mode 只能为 grade/rehearsal")
        tc.require(mode != "grade" or rehearsal == 0,
                   "正式 Fes 与彩排须分开任务；彩排请设置 mode=rehearsal")
        if mode == "rehearsal":
            tc.require(rehearsal in (0, runs), "彩排次数请统一使用 runs_per_day")
            rehearsal = runs
            # Rehearsal spends at difficulty selection, before a unit-score page is available.
            # The UI keeps the formal-mode preference, but it is not an effective rehearsal constraint.
            unit_score = ""
        bulk_count = params.get("bulk_count", 1)
        tc.require(type(bulk_count) is int and bulk_count in BULK_COUNTS, "bulk_count 必须为 1–5 的整数")
        timed = params.get("live_timed_click", False)
        tc.require(type(timed) is bool, "live_timed_click 必须为布尔值")
        return cls(difficulty, runs, reserve, False, samples, live, speed, rehearsal, recovery, cap, unit_score, mode,
                   bulk_count, timed)

    def to_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class LogState:
    attempts: int = 0      # every テンション決定 sent today (legacy B5 intents count as one attempt each)
    uncertain: int = 0     # attempts whose result is still uncertain
    open: dict = None      # the last attempt when it has no conclusion (see ``resume_kind``)
    verified: int = 0      # attempts concluded with rights - 1 on Fes main
    reported: int = 0      # 2026-10-03: other attempts whose LIVE REPORT was observed (the live ended; right spent)

    @property
    def played(self):
        """Lives finished today: verified on Fes main, or LIVE REPORT observed after the spend (even when the run
        then stopped before Fes main, e.g. the unknown ranking page of 11:09). Counts toward ``runs_per_day``."""
        return self.verified + self.reported


def log_state(events):
    """Rebuild today's attempts from the log.

    Events: ``tension_intent`` (before the テンション決定 grant), ``tension_refused``, ``tension_uncertain``,
    ``tension_decided`` (TensionDecided observed, rights unchanged), ``intent`` with ``stage == "TensionDecided"``
    (before the ライブ開始 grant; consuming), ``refused`` (certainly not sent), ``uncertain``, ``verified`` /
    ``observed_home`` (concluded), ``resume`` (B5h: 「再開」 pressed on フェス再開; the open spent attempt, or a new one
    when the log has none, is marked resumed — never resumed at TensionDecided), ``live_takeover`` (B5i: a live
    turn screen already shown when the run began; recorded like ``resume``; B5j ``report_takeover``: LIVE REPORT
    shown when the run began, the same; B5k ``ranking_takeover``: the ranking page, the same) and ``resolved`` (``result == "not_spent_tension_decided"``: a legacy B5 intent,
    written before テンション決定 when it was wrongly thought to consume, observed afterwards at TensionDecided with
    unchanged rights). A legacy ``intent`` has no ``stage``.
    """
    attempts = []
    for event in events:
        kind = event.get("event")
        current = attempts[-1] if attempts and not attempts[-1]["concluded"] else None
        if kind == "tension_intent":
            attempts.append({"kind": "tension", "difficulty": event.get("difficulty"),
                             "rights_before": event.get("rights_before"), "decided": False, "spent": False,
                             "uncertain": False, "resolved": False, "concluded": False})
        elif kind == "tension_refused" and current is not None and current["kind"] == "tension" and not current["decided"]:
            attempts.pop()
        elif kind == "tension_uncertain" and current is not None:
            current["uncertain"] = True
        elif kind == "tension_decided" and current is not None and not current["spent"]:
            current.update(decided=True, uncertain=False)
        elif kind == "resolved" and event.get("result") == NOT_SPENT_TENSION_DECIDED and current is not None \
                and current["kind"] == "legacy" and current["spent"] and not current["resolved"]:
            current.update(spent=False, uncertain=False, decided=True, resolved=True)
        elif kind == "intent" and event.get("stage") in (LIVE_STAGE, REHEARSAL_STAGE):
            if current is None:  # no テンション決定 recorded: count it, never resume it
                attempts.append({"kind": "orphan", "difficulty": event.get("difficulty"),
                                 "rights_before": event.get("rights_before"), "decided": True, "spent": True,
                                 "uncertain": False, "resolved": False, "concluded": False})
            else:
                current.update(spent=True, uncertain=False)
        elif kind == "intent":
            attempts.append({"kind": "legacy", "difficulty": event.get("difficulty"),
                             "rights_before": event.get("rights_before"), "decided": False, "spent": True,
                             "uncertain": False, "resolved": False, "concluded": False})
        elif kind == "refused" and current is not None and current["spent"]:
            if current["kind"] == "legacy" and not current["resolved"]:
                attempts.pop()            # the legacy grant was refused before テンション決定
            else:
                current["spent"] = False  # ライブ開始 certainly not sent: still at TensionDecided
        elif kind in TAKEOVER_EVENTS:
            if current is not None and current["spent"]:
                current["resumed"] = True
            else:  # a live this log does not know (another day, manual): count it, never resume it at TensionDecided
                attempts.append({"kind": "resumed", "difficulty": event.get("difficulty"),
                                 "rights_before": event.get("rights_before"), "decided": True, "spent": True,
                                 "uncertain": True, "resolved": False, "concluded": False, "resumed": True})
        elif kind == "live_report" and current is not None and current["spent"]:
            current["reported"] = True  # the live ended with a report: played, though the attempt stays open
        elif kind in ("uncertain", "live_unfinished") and current is not None:
            current["uncertain"] = True
        elif kind in ("verified", "observed_home") and current is not None:
            current["concluded"] = True
        if kind == "verified" and current is not None:
            current["verified"] = True
    last = attempts[-1] if attempts and not attempts[-1]["concluded"] else None
    uncertain = sum(1 for a in attempts if a["uncertain"] and not a["concluded"])
    verified = sum(1 for a in attempts if a.get("verified"))
    reported = sum(1 for a in attempts if a.get("reported") and not a.get("verified"))
    return LogState(len(attempts), uncertain, dict(last) if last is not None else None, verified, reported)


def resume_kind(state):
    """How the open attempt may continue from a fresh TensionDecided frame (None: never).

    ``legacy_uncertain``: a B5 intent + uncertain from テンション決定 (read-only resolution first);
    ``tension``: テンション決定 sent (TensionDecided observed or not) and ライブ開始 not sent (or certainly refused).
    An attempt whose ライブ開始 was sent is never resumed.
    """
    attempt = state.open
    if attempt is None:
        return None
    if attempt["kind"] == "legacy" and attempt["spent"] and attempt["uncertain"] and not attempt["resolved"]:
        return "legacy_uncertain"
    if not attempt["spent"] and attempt["kind"] in ("tension", "legacy"):
        return "tension"
    return None


def per_run_config(config, state):
    """「Grade每日次数」 is the count of lives per run, not per day. The day's ledger
    keeps its role (resume, uncertain, attempts), so when a run begins its targets are shifted by what the ledger already
    holds. An open attempt (resumable, or a live still being reported) belongs to this run, not to the baseline."""
    open_attempt = state.open is not None
    played_before = state.played - (1 if open_attempt and state.open.get("reported") else 0)
    attempts_before = state.attempts - (1 if open_attempt else 0)
    return dataclasses.replace(config, runs_per_day=played_before + config.runs_per_day,
                               samples_runs_per_day=attempts_before + config.attempt_limit)


@dataclasses.dataclass(frozen=True)
class DailyProgress:
    """Selected mode's daily target and the lives this batch can still start. No resource authorization."""
    mode: str
    target: int
    completed: int
    remaining: int
    planned_runs: int
    attempts: int
    samples_remaining: int
    verified: int
    reported: int
    uncertain: int

    def to_dict(self):
        return dataclasses.asdict(self)


def progress_from_state(config, state):
    remaining = max(0, config.runs_per_day - state.played)
    samples_remaining = max(0, config.attempt_limit - state.attempts)
    # An existing unspent tension attempt uses a sample slot already but still needs one right to complete.
    resumable = int(resume_kind(state) is not None)
    return DailyProgress(config.mode, config.runs_per_day, state.played, remaining,
                         min(remaining, samples_remaining + resumable), state.attempts, samples_remaining,
                         state.verified, state.reported, state.uncertain)


def daily_progress(config, log_root=tc.LOG_ROOT, now=None):
    """Same JST daily ledger and mode isolation used by GradeRuntime; usable by launcher/UI preflight."""
    log = tc.JsonlLog("grade", log_root, now or tc.utc_now)
    state = log_state(event for event in log.events() if event.get("mode", "grade") == config.mode)
    return progress_from_state(per_run_config(config, state), state)


def batch_pipeline_overrides(nodes, planned_runs):
    """Scale the existing per-task hit limits to a batch; keep every click's enablement and guards unchanged."""
    tc.require(type(planned_runs) is int and 0 <= planned_runs <= MAX_RUNS_PER_DAY,
               "Grade 本轮局数无效")
    return {name: {"max_hit": node["max_hit"] * max(1, planned_runs)}
            for name, node in nodes.items()
            if name.startswith("SC.Grade.") and type(node.get("max_hit")) is int and node["max_hit"] > 0}


def rights_reasons(config, state, rights):
    """Reasons not to start a new attempt now; ``rights`` is the (current, max) reading.

    A sent-and-uncertain ライブ開始 counts toward ``samples_runs_per_day`` but does not block a new attempt (it is
    not retried); an attempt that may still be at TensionDecided (``resume_kind``) blocks until resumed."""
    if state.played >= config.runs_per_day:
        return ["runs_done_today"]
    if state.attempts >= config.attempt_limit:
        return ["samples_cap_reached"]
    if resume_kind(state) is not None:
        return ["attempt_unresolved"]
    if rights is None:
        return ["rights_unreadable"]
    if rights[0] < 1 + config.rights_reserve:
        return ["rights_below_reserve"]
    return []


def tension_reasons(config, title, notice, rights, expected):
    """TensionDecided: configured difficulty, the 1-right / no-return note and rights unchanged since テンション決定."""
    reasons = []
    if title != f"参加ユニット({config.difficulty})":
        reasons.append("unit_title_not_configured_difficulty")
    if not (isinstance(notice, str) and ONE_RIGHT in notice and NO_RETURN in notice):
        reasons.append("notice_not_one_right")
    if rights is None:
        reasons.append("rights_unreadable")
    elif expected is None or tuple(rights) != tuple(expected):
        reasons.append("rights_changed")
    return reasons


def live_reasons(config, state, rights):
    """Before granting ライブ開始 (consumes 1): an open, unspent attempt of today and rights >= 1 + reserve."""
    reasons = []
    if state.open is None or state.open["spent"] or not state.open["decided"]:
        reasons.append("no_open_tension_decided_attempt")
    if state.played >= config.runs_per_day:
        reasons.append("runs_done_today")
    if state.attempts > config.attempt_limit:
        reasons.append("samples_cap_reached")
    if rights is None:
        reasons.append("rights_unreadable")
    elif rights[0] < 1 + config.rights_reserve:
        reasons.append("rights_below_reserve")
    return reasons


def difficulty_reasons(config, wanted, title, prompt, rights, previous):
    """Difficulty dialog: grade title, single-right prompt (bulk unticked is a template), the chosen difficulty."""
    reasons = []
    if wanted != config.difficulty:
        reasons.append("difficulty_not_configured")
    expected_title = REHEARSAL_DIFFICULTY_TITLE if config.mode == "rehearsal" else DIFFICULTY_TITLE
    # Native rehearsal frame 60: high-confidence OCR reads the final い as し.
    # The full rehearsal title template is independently required by its Pipeline guard.
    accepted_titles = {expected_title}
    if config.mode == "rehearsal":
        accepted_titles.add(expected_title[:-1] + "し")
    if title not in accepted_titles:
        reasons.append("title_not_rehearsal" if config.mode == "rehearsal" else "title_not_grade_fes")
    if prompt != ONE_RIGHT:
        reasons.append("prompt_not_one_right")
    if rights is None:
        reasons.append("rights_unreadable")
    elif previous is not None and rights != previous:
        reasons.append("rights_changed")
    return reasons


def unit_score_reading(text):
    """The own unit's evaluation read on 参加ユニット as a digit string, or None when not a clean number."""
    digits = str(text or "").replace(" ", "").replace(",", "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return digits if re.fullmatch(UNIT_SCORE_PATTERN, digits) else None


def unit_binding_reasons(config, score_text):
    """Batch 4: the chosen Fes unit. Not bound -> no reason (the reading is only reported). Bound -> the fresh
    reading must be a clean number equal to the configured one; unreadable or different stops before テンション決定."""
    if not config.unit_score:
        return []
    score = unit_score_reading(score_text)
    if score is None:
        return ["unit_score_unreadable"]
    return [] if score == config.unit_score else ["unit_score_mismatch"]


def unit_reasons(config, title, notice, rights, previous):
    """参加ユニット: the configured difficulty, the 1-right / no-return notice and unchanged rights >= 1 + reserve.
    The chosen unit (batch 4) is checked separately before テンション決定 only (``unit_binding_reasons``)."""
    reasons = []
    if title != f"参加ユニット({config.difficulty})":
        reasons.append("unit_title_not_configured_difficulty")
    if not (isinstance(notice, str) and ONE_RIGHT in notice and NO_RETURN in notice):
        reasons.append("notice_not_one_right")
    if rights is None:
        reasons.append("rights_unreadable")
    else:
        if previous is not None and rights != previous:
            reasons.append("rights_changed")
        if rights[0] < 1 + config.rights_reserve:
            reasons.append("rights_below_reserve")
    return reasons


class GradeRun(tc.TaskRun):
    """Phases: active -> joined -> mode_tapped -> difficulty_tapped -> tension_tapped -> tension_decided ->
    awaiting_result -> active (another remaining live) or finished. A resumed run goes active -> tension_decided
    (read-only observation first). Only a verified return from one live resets the per-live state."""
    SUCCESS = SUCCESS_OUTCOMES
    HALT = HALT_OUTCOMES

    def __init__(self, config, log):
        super().__init__(config, log)
        self.rights = None          # last (current, max) reading of this run
        self.resume = None          # resume_kind() at Begin, with the open attempt's rights_before
        self.watch_started = None
        self.saver = None           # FrameSaver of the current read-only observation
        self.samples = []           # every sample directory of this run
        self.last_save = None
        self.last_change = None     # monotonic time of the last screen change seen while observing
        self.ref_sig = self.saved_sig = None
        self.skipped_duplicates = 0
        self.live = None            # LivePlay while the live after ライブ開始 is played (B5d)
        self.hold_frames = 0        # consecutive FLOW_PHASES frames no Grade node accepted (SC.Grade.Flow.Hold)
        self.hold_phase = None
        self.hold_saver = None
        self.recent = []            # [(signature, frame copy)] of the last STOP_SAMPLE_FRAMES distinct frames
        self.daily_start = None
        self.daily_progress = None
        self.batch_results = []

    def restart_live(self):
        """After verified Fes-main return only: discard single-live timing/grants, retain batch evidence."""
        preserved = {name: getattr(self, name) for name in
                     ("notes", "decisions", "samples", "daily_start", "daily_progress", "batch_results")}
        self.__init__(self.config, self.log)
        self.__dict__.update(preserved)

    def summary(self):
        result = super().summary()
        if self.daily_progress is not None:
            result["daily_progress"] = self.daily_progress.to_dict()
            result["daily_start"] = self.daily_start.to_dict() if self.daily_start else None
            result["completed_in_run"] = max(0, self.daily_progress.completed -
                                             (self.daily_start.completed if self.daily_start else 0))
        result["lives"] = list(self.batch_results)
        result["takeover_required"] = self.outcome == "manual_takeover"
        if result["takeover_required"]:
            result["status"] = "takeover"
        return result
