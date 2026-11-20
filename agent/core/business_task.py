"""Business (営業) task rules (B6): pure logic, no Maa import, no input.

B6e: ``bulk_default`` is the default mode (~19:30): on a stable business top press
「一括完了」 when a slot shows CLEAR, close each WORK REPORT with 「Touch to Next」, then 「一括営業」 when a slot is empty
(positive 「未選択」/「営業先選択」), and succeed only when the top shows no CLEAR, no empty slot and both bulk buttons
grey (``bulk_decide``). Every press is logged (``bulk_intent``) first and proven by a fresh frame; an unknown screen
or dialog stops (``needs_sampling``), an error dialog stops for the day, a refused tap is ``click_refused_not_sent``.

B6 history (sampling first). Before native frames existed the only live mode was ``sample``:
verified clean Home -> the bottom-bar 「営業」 button (non-consuming) -> save frames of whatever the business top screen
shows (read-only, no click) -> the lower-left back key, only when it is visible and undimmed -> verified Home.

``collect_and_dispatch`` is DESIGNED here (``plan_slots``, ``log_state``) but not wired to any click: the task ends
at once with ``needs_sampling`` (no click) until samples exist and a reviewed change adds the guarded nodes. The rules
the design enforces:

- read every slot (completed / in progress with a timer / empty / locked) before anything; an unreadable or
  ambiguous slot stops the task;
- claim completed slots only; never touch an in-progress slot; never unlock a slot (jewels);
- dispatch only the configured business with the configured team for exactly 12 h, into at most ``slots`` empty
  slots; never substitute another business / team / duration;
- never 差し入れ / gift / stamina / soda (fixed off, not a setting);
- error 1012 or any result not proven by a fresh frame -> uncertain: logged, never retried in that run. A later run
  (结果不确定只提醒) writes a ``recheck`` marker and decides from the live state it observes.
"""
from __future__ import annotations

import dataclasses
import re

from agent.core import task_common as tc

MODES = ("bulk_default", "sample", "sample_collect_one", "sample_dispatch_open", "collect_and_dispatch")
DEFAULT_MODE = "bulk_default"
BUSINESSES = {"雑誌インタビュー": 12}  # the only 12 h business; ミニライブ 3 h / TV出演 6 h are never chosen
DURATION_HOURS = 12
MAX_SLOTS = 3  # the account has all three slots open (2 and 3 were unlocked earlier with jewels; never unlock here)
SLOT_STATES = ("completed", "in_progress", "empty", "locked", "unknown")

# Sampling (B6): frames of the business top screen after the 営業 tap. Static replays shorten both through options.
SAMPLE_FRAMES = 10            # frames to save (>= 1.5 s apart) before returning
SAMPLE_MIN_SECONDS = 15.0     # and at least this long on the screen (animations, result pop-ups, timers)
SAMPLE_INTERVAL_SECONDS = 1.5
SAMPLE_FRAME_LIMIT = 80      # B6d: claim + WORK REPORT + business top (+ dispatch screen) fit in one run
OPEN_SETTLE_SECONDS = 1.0     # the first saved frame is taken at least this long after the screen left Home

# sample_collect_one (B6b): read the business top, press 「営業完了」 of the first CLEAR slot once, observe read-only.
TOP_WAIT_SECONDS = 30.0       # the business top must be recognised this long after leaving Home, else needs_sampling
OBSERVE_SECONDS = 60.0        # read-only observation after the claim tap; unresolved -> claim_uncertain / needs_sampling
OBSERVE_MIN_SECONDS = 5.0     # keep sampling at least this long after the tap before concluding ``claimed``
# Two equal readings this far apart prove a page is still (claimed slot not CLEAR, WORK REPORT, gift, 獲得報酬, 営業アイドル
# 選択). 2026-10-24: 1.5 -> 0.8 s. A round is ~1 s live, so two consecutive captures now suffice instead of three; the
# 10-24 daily (ui-20261024-030724) spent one extra round on each of ~8 stable pages. Animations are still waited out.
CONFIRM_SECONDS = 0.8

# B6d WORK REPORT (shown after 「営業完了」, live 2026-10-03 19:10) and sample_dispatch_open.
REPORT_WAIT_SECONDS = 20.0    # the report readings (Exp / fans) must be the same on two frames within this time
REPORT_NO_EFFECT_SECONDS = 8.0  # still on the report this long after 「Touch to Next」 -> report_next_no_effect (no retry)
DISPATCH_SECONDS = 30.0       # read-only window on the screen opened by the empty-slot tap
DISPATCH_SAMPLE_SECONDS = 8.0  # observe at least this long (and >= 2 frames off the business top) before the back key
DISPATCH_RETURN_SECONDS = 15.0  # after the back key: the business top (or Home) must be seen within this time
# Text inside a slot card that proves the slot is NOT empty: a timer / remaining time (「あと7:15」, 「ティータイム残り
# 1時間36分」), any digit, a business name, the CLEAR stamp / 営業完了, or a lock / jewel unlock. Fail-safe: anything
# readable of this kind -> "other"; only a card with none of it (and no CLEAR / 営業完了 anchor) is a candidate.
BUSY_TEXT = re.compile(r"[0-9０-９]|時間|分|残り|あと|完了|CLEAR|Clear|TeaTime|ティータイム|インタビュー|ライブ|出演|"
                       r"営業中|解放|ジュエル|ロック|鍵")
# Positive evidence of an EMPTY slot (native 2026-10-03 20:07, bulk/top-slot1-empty.png): header 「未選択」 and the white
# 「営業先選択」 button. A card without either text is never empty (B6e; the blank synthetic card reads ``other`` now).
EMPTY_TEXT = re.compile(r"未選択|営業先選択|先選択")
# Tea Time card (native 2026-10-01 teatime-20261001.png and live 2026-10-12 business-top-20261012T012554057901Z): the
# business is finished (smaller pink 「営業完了」 lower in the card, 一括完了 pink) but the card shows the TeaTime cup instead
# of the CLEAR stamp and 「ティータイム残り H時間MM分」. 2026-10-12 timing (12 h dispatch at 12:55 UTC, read at 01:25 UTC
# with 2:33 left) puts the window at ~3 h right after completion; afterwards the card reads CLEAR. 2026-10-01 一括完了 in
# this state ended with error 1012, so Tea Time is recognised positively but never pressed: ``tea_time_wait``.
TEA_TIME_TEXT = re.compile(r"ティータイム|TeaTime|TEATIME|Teatime")
TEA_TIME_LEFT = re.compile(r"(?:(\d{1,2})時間)?\s*(\d{1,2})分")

# B6e bulk_default (~19:30: press the lower-right buttons to collect and re-dispatch).
# One run: 一括完了 at most once, each WORK REPORT closed with 「Touch to Next」 (at most BULK_MAX_REPORTS), 一括営業 at
# most once, then the business top must show no CLEAR, no empty slot and both bulk buttons grey (nothing left to do).
BULK_MAX_REPORTS = 4
BULK_OBSERVE_SECONDS = 30.0   # read-only after a bulk press / Touch to Next before an unknown screen stops the task
BUTTON_STATES = ("enabled", "disabled", "unknown")
# Words that make a dialog after 一括営業 never confirmable (resources): jewels, stamina, 差し入れ, soda, recovery.
FORBIDDEN_DIALOG_WORDS = ("ジュエル", "スタミナ", "差し入れ", "ソーダ", "回復")

# Batch 4: 一括営業 must be bound to the user's business / team / 12 h before anything is confirmed.
# ``bulk_dispatch``: ``off`` (default: 一括完了 and its reports only; empty slots are left, ``collected_dispatch_disabled``)
# or ``verify`` (explicit; needs ``team``): press 一括営業 once, then the dispatch confirmation text must name the
# configured business, 「12時間」 and every configured team name, with no resource word. No confirm action exists in the
# action table yet (no native sample of that dialog), so a matching preview stops ``dispatch_confirm_not_enabled`` and a
# different one ``dispatch_binding_mismatch`` -- nothing is confirmed either way. If the game dispatched without any
# confirmation dialog, the binding was never checked before the dispatch: ``dispatched_binding_unverified`` (failed,
# logged, never retried today).
BULK_DISPATCH_MODES = ("off", "verify")
DEFAULT_BULK_DISPATCH = "off"
# Text that marks a screen after 一括営業 as a dispatch confirmation / preview (rather than an unknown screen).
DISPATCH_PREVIEW_CUE = re.compile(r"営業(開始|確認|する|先)|一括営業|[0-9０-９]+時間")
TEAM_SPLIT = re.compile(r"[、,，/／・\s]+")

# 2026-10-10 live bulk_default: after the last report page (and the ファンからのプレゼント page) come 獲得報酬 (Exスキル list,
# OK / 再営業へ) and 営業アイドル選択 (three prefilled 12 h slots, 決定). this sequence is the same
# every day; ``redispatch=decide`` (opt-in) presses OK, binds the page and presses 決定 once; ``back`` presses 再営業へ, reads
# and binds the page, and leaves via ←.
# 2026-10-11 user direction: press 再営業へ (never OK), then 決定 by default. Both actions (business-reward-redispatch,
# business-dispatch-decide) are unverified until one supervised run.
REDISPATCH_MODES = ("decide", "back")
DEFAULT_REDISPATCH = "decide"  # 2026-10-11 user approved: 再営業へ then 決定 by default; "back" stays available
SELECT_SLOTS = 3
SELECT_CARDS_PER_SLOT = 5
REWARD_MAX_ENTRIES = 12  # the visible part of the Exスキル list (6 rows x 2 columns, rows 5-6 clipped)

SUCCESS_OUTCOMES = frozenset({"sampled", "claimed", "no_completed_slot", "report_closed", "dispatch_sampled",
                              "no_empty_slot", "collected_and_dispatched", "nothing_to_do",
                              "collected_dispatch_disabled", "collected_and_redispatched"})
# Batch 4: not success, not halt (the back key still returns Home).
RETURN_FAILURE_OUTCOMES = frozenset({"team_not_configured", "dispatched_binding_unverified",
                                     "redispatch_binding_mismatch", "tea_time_wait"})
HALT_OUTCOMES = frozenset({"needs_sampling", "sampled_not_returned", "return_unverified", "not_opened", "timeout",
                           "spend_uncertain", "spend_refused", "claim_uncertain", "business_error_uncertain",
                           "uncertain_slot_today", "uncertain_result_today", "slot_changed", "log_failed",
                           "report_unstable", "report_next_no_effect", "report_next_unverified",
                           "work_report_on_screen", "dispatch_screen_not_opened", "dispatch_not_returned",
                           "dispatch_return_unverified", "click_refused_not_sent", "bulk_result_uncertain",
                           "bulk_button_unavailable", "bulk_complete_incomplete", "bulk_dispatch_incomplete",
                           "bulk_report_limit", "slots_unverified", "dispatch_confirm_not_enabled",
                           "dispatch_binding_mismatch", "reward_ok_no_effect", "reward_unverified",
                           "reward_unstable", "select_unstable", "redispatch_unverified", "select_back_unverified"})


@dataclasses.dataclass(frozen=True)
class BusinessConfig:
    """``mode``: ``bulk_default`` (default, B6e: 一括完了 -> WORK REPORTs -> 一括営業 -> verify), ``sample`` (read-only
    frames), ``sample_collect_one`` (B6b: claim the first CLEAR slot once, then observe read-only; B6d: close its WORK REPORT once), ``sample_dispatch_open`` (B6d: open the first empty slot
    once, sample the screen behind it read-only, back key once; never dispatches) or ``collect_and_dispatch`` (designed, ends with ``needs_sampling``). ``slots``: how many empty slots a dispatch may fill (default 1). ``business`` / ``team``:
    what to dispatch (``team`` is the user's team name as shown in the game; empty = not chosen -> never dispatch).
    ``duration_hours`` and ``gift`` are fixed (12 h; no 差し入れ)."""
    mode: str = DEFAULT_MODE
    slots: int = 1
    business: str = "雑誌インタビュー"
    team: str = ""
    duration_hours: int = DURATION_HOURS
    gift: bool = False
    bulk_dispatch: str = DEFAULT_BULK_DISPATCH
    redispatch: str = DEFAULT_REDISPATCH

    @classmethod
    def from_params(cls, params):
        allowed = {"mode", "slots", "business", "team", "duration_hours", "gift", "bulk_dispatch", "redispatch"}
        tc.require(set(params) <= allowed, "営業 配置含未知字段: " + ",".join(sorted(set(params) - allowed)))
        mode = params.get("mode", DEFAULT_MODE)
        tc.require(mode in MODES, "business_mode 只能为 bulk_default / sample / sample_collect_one / "
                                  "sample_dispatch_open / collect_and_dispatch")
        slots = params.get("slots", 1)
        tc.require(type(slots) is int and 1 <= slots <= MAX_SLOTS, "slots 必须为 1–3 的整数")
        business = params.get("business", "雑誌インタビュー")
        tc.require(business in BUSINESSES, "营业只能为 12 小时的 雑誌インタビュー")
        team = params.get("team", "")
        tc.require(isinstance(team, str) and len(team) <= 40, "team 必须为字符串")
        team = team.strip()
        bulk_dispatch = params.get("bulk_dispatch", DEFAULT_BULK_DISPATCH)
        tc.require(bulk_dispatch in BULK_DISPATCH_MODES, "bulk_dispatch 只能为 off / verify")
        redispatch = params.get("redispatch", DEFAULT_REDISPATCH)
        tc.require(redispatch in REDISPATCH_MODES, "redispatch 只能为 decide / back")
        tc.require(params.get("duration_hours", DURATION_HOURS) == DURATION_HOURS
                   and type(params.get("duration_hours", DURATION_HOURS)) is int, "时长固定 12 小时")
        tc.require(params.get("gift", False) is False, "差し入れ（体力/苏打）永远关闭")
        return cls(mode, slots, business, team, DURATION_HOURS, False, bulk_dispatch, redispatch)

    def to_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Slot:
    """One observed slot of the business top screen (design; reader not implemented until samples exist)."""
    index: int                  # 1..3, screen order
    state: str                  # one of SLOT_STATES
    business: str = None        # in progress / completed: the business shown
    team: str = None
    remaining: str = None       # in progress: the timer text ("あと7:15"); None otherwise


@dataclasses.dataclass(frozen=True)
class SlotPlan:
    claims: tuple = ()          # slot indexes to claim (completed only)
    dispatches: tuple = ()      # slot indexes to fill with (business, team, 12 h)
    stop: tuple = ()            # reasons: when non-empty nothing at all is clicked


def plan_slots(config, slots, state=None):
    """Decide claims / dispatches for one observation of all slots (design for ``collect_and_dispatch``).

    Claims come first and are re-observed before any dispatch (the caller re-reads the slots after the claims and
    calls this again); dispatches fill the lowest empty slots, at most ``config.slots`` minus slots already running
    the configured business. Any unknown slot, a duplicate index, a missing team, or an uncertain result today stops
    everything (``stop``)."""
    state = state or LogState()
    stop = []
    if state.uncertain:
        stop.append("uncertain_result_today_never_retried")
    indexes = [s.index for s in slots]
    if sorted(indexes) != list(range(1, len(slots) + 1)) or not 1 <= len(slots) <= MAX_SLOTS:
        stop.append("slot_list_incomplete")
    if any(s.state not in SLOT_STATES for s in slots) or any(s.state == "unknown" for s in slots):
        stop.append("slot_state_unknown")
    if any(s.state == "in_progress" and not s.remaining for s in slots):
        stop.append("in_progress_without_timer")
    if stop:
        return SlotPlan(stop=tuple(stop))
    claims = tuple(s.index for s in slots if s.state == "completed")
    if claims:
        return SlotPlan(claims=claims)  # re-observe before dispatching
    if not config.team:
        return SlotPlan(stop=("team_not_configured",))
    running = sum(1 for s in slots if s.state == "in_progress" and s.business == config.business)
    want = max(0, config.slots - running - state.dispatched_pending)
    empty = [s.index for s in slots if s.state == "empty"]
    return SlotPlan(dispatches=tuple(empty[:want]))


@dataclasses.dataclass(frozen=True)
class LogState:
    claimed: int = 0
    dispatched: int = 0
    dispatched_pending: int = 0   # dispatches sent but not yet seen as in-progress slots
    uncertain: int = 0            # claim/dispatch results not proven (includes error 1012)


UNCERTAIN_EVENTS = ("uncertain", "claim_uncertain", "error_1012", "business_error")


def log_state(events):
    """Events: ``claim_intent`` -> ``claimed`` | ``claim_uncertain`` (B6b) or ``uncertain``; ``dispatch_intent`` ->
    ``dispatched`` | ``uncertain`` (design); ``error_1012`` / ``business_error`` are always uncertain. An intent
    without a conclusion is uncertain."""
    events = list(events)
    claimed = dispatched = uncertain = open_intents = 0
    for event in events:
        kind = event.get("event")
        if kind in ("claim_intent", "dispatch_intent"):
            open_intents += 1
        elif kind == "claimed" and event.get("resolved") is True:
            claimed, uncertain = claimed + 1, max(0, uncertain - 1)  # proof seen later (WORK REPORT takeover)
        elif kind == "claimed":
            claimed, open_intents = claimed + 1, max(0, open_intents - 1)
        elif kind == "claim_refused":  # never sent: closes the intent (or corrects its earlier claim_uncertain)
            if open_intents:
                open_intents -= 1
            else:
                uncertain = max(0, uncertain - 1)
        elif kind == "dispatched":
            dispatched, open_intents = dispatched + 1, max(0, open_intents - 1)
        elif kind in UNCERTAIN_EVENTS:
            uncertain, open_intents = uncertain + 1, max(0, open_intents - 1)
    return LogState(claimed, dispatched, 0, uncertain + open_intents + len(bulk_pending(events)))


RECHECK = "recheck"


def since_recheck(events):
    """Events after the last ``recheck`` marker: a run that begins over earlier uncertain records writes one, and only
    what happens after it can stop that run again (the earlier records only warn, in the panel)."""
    events = list(events)
    last = max((i for i, e in enumerate(events) if e.get("event") == RECHECK), default=-1)
    return events[last + 1:]


def uncertain_slots(events, include_bulk=True):
    """Today's claims that must never be retried: ``(slots, unscoped)``. ``slots``: indexes with a ``claim_intent``
    not followed by ``claimed`` (or ``claim_refused``: the controller proved nothing was sent) for the same slot, or
    with ``claim_uncertain``; ``unscoped``: an uncertain event or an open intent without a slot index (blocks every
    claim today). ``include_bulk``: an unresolved bulk press (``bulk_pending``) also counts as unscoped; bulk_default
    itself passes False and re-observes the business top instead. Only events after the last ``recheck`` count."""
    events = since_recheck(events)
    open_slots, blocked, unscoped = set(), set(), False
    for event in events:
        kind, slot = event.get("event"), event.get("slot")
        scoped = type(slot) is int and 1 <= slot <= MAX_SLOTS
        if kind == "claim_intent":
            if scoped:
                open_slots.add(slot)
            else:
                unscoped = True
        elif kind == "claim_refused" and scoped:  # the tap never left the controller (host / gate refusal)
            open_slots.discard(slot)
            blocked.discard(slot)  # also corrects an earlier claim_uncertain written for that refused tap
        elif kind == "claimed" and scoped:
            open_slots.discard(slot)
            if event.get("resolved") is True:  # a later WORK REPORT proved the uncertain claim of this slot
                blocked.discard(slot)
        elif kind in UNCERTAIN_EVENTS or kind == "dispatch_intent":
            if scoped:
                blocked.add(slot)
                open_slots.discard(slot)
            else:
                unscoped = True
    if include_bulk and bulk_pending(events):
        unscoped = True
    return frozenset(blocked | open_slots), unscoped


BULK_ACTIONS = ("complete", "dispatch")


def bulk_pending(events):
    """Bulk presses of today whose result is not proven: ``{action: reason}``. ``bulk_intent`` opens one; it is closed
    by ``bulk_completed`` / ``bulk_dispatched`` (proof), ``bulk_refused`` (never sent) or ``bulk_resolved`` (a later
    stable business top proved the state); ``bulk_uncertain`` keeps it open with its reason. Only events after the
    last ``recheck`` count."""
    pending = {}
    for event in since_recheck(events):
        kind, action = event.get("event"), event.get("action")
        if action not in BULK_ACTIONS:
            continue
        if kind == "bulk_intent":
            pending[action] = "intent_without_result"
        elif kind == "bulk_uncertain":
            pending[action] = str(event.get("reason") or "uncertain")
        elif kind in ("bulk_completed", "bulk_dispatched", "bulk_refused", "bulk_resolved"):
            pending.pop(action, None)
    return pending


def bulk_resolution(pending, kinds):
    """Re-observation of unresolved bulk presses on a stable business top: ``(resolved, stop)``. A pending 一括完了
    is resolved when no slot reads CLEAR, a pending 一括営業 when no slot reads empty; otherwise the task stops
    (``uncertain_result_today``) -- an uncertain press is never repeated."""
    resolved, stop = [], None
    for action in sorted(pending):
        done = ("completed" not in kinds) if action == "complete" else ("empty" not in kinds)
        if done:
            resolved.append(action)
        else:
            stop = "uncertain_result_today"
    return tuple(resolved), stop


def bulk_decide(kinds, complete_state, dispatch_state, done=frozenset(), blocked=frozenset()):
    """One decision of ``bulk_default`` on a stable business top. ``kinds``: ``completed`` / ``empty`` / ``other`` per
    slot; ``complete_state`` / ``dispatch_state``: ``enabled`` / ``disabled`` / ``unknown`` (一括完了 pink / 一括営業
    white = enabled, grey = disabled); ``done``: bulk actions already pressed in this run (each at most once);
    ``blocked``: slots with an uncertain single claim today. Returns ``(press, outcome)``: ``press`` in
    ("complete", "dispatch") or ``None`` with the final / stop outcome."""
    if "tea_time" in kinds and complete_state == "enabled":
        # 一括完了 would also take the Tea Time slots (2026-10-01: error 1012 in this state): wait for CLEAR instead.
        return None, "tea_time_wait"
    if "completed" in kinds:
        if any(i + 1 in blocked for i, k in enumerate(kinds) if k == "completed"):
            return None, "uncertain_slot_today"
        if "complete" in done:
            return None, "bulk_complete_incomplete"
        return ("complete", None) if complete_state == "enabled" else (None, "bulk_button_unavailable")
    if "empty" in kinds:
        if "dispatch" in done:
            return None, "bulk_dispatch_incomplete"
        return ("dispatch", None) if dispatch_state == "enabled" else (None, "bulk_button_unavailable")
    if complete_state == "disabled" and dispatch_state == "disabled":
        return None, ("collected_and_dispatched" if done else "nothing_to_do")
    return None, "slots_unverified"  # e.g. Tea Time (一括完了 still pink) or an unread button state


def tea_time_left_minutes(texts):
    """The longest 「ティータイム残り H時間MM分」 among the Tea Time cards' OCR lines, in minutes (None if unreadable)."""
    best = None
    for lines in texts or ():
        joined = "".join(str(t).replace(" ", "") for t in (lines or ()))
        if not TEA_TIME_TEXT.search(joined):
            continue
        m = TEA_TIME_LEFT.search(joined)
        if m:
            minutes = int(m.group(1) or 0) * 60 + int(m.group(2))
            best = minutes if best is None else max(best, minutes)
    return best


def team_names(team):
    """The configured team as a list of names (「芹沢あさひ、黛冬優子」 / 「A/B」 -> two names)."""
    return [n for n in TEAM_SPLIT.split(str(team or "").strip()) if n]


def bulk_dispatch_gate(config):
    """Before pressing 一括営業 in bulk_default: ``None`` (press allowed) or the outcome that replaces the press."""
    if config.bulk_dispatch != "verify":
        return "collected_dispatch_disabled"
    if not team_names(config.team):
        return "team_not_configured"
    return None


def dispatch_binding(texts, config):
    """Batch 4: the binding of a screen read after 一括営業. ``None`` when the text is not a dispatch preview at all
    (unknown screen -> ``needs_sampling`` as before); otherwise ``{"matched", "missing", "forbidden"}``: matched only
    when the configured business, 「<12>時間」 and every team name are all present and no resource word is."""
    joined = "".join(str(t) for t in texts or ()).replace(" ", "").replace("\u3000", "")
    joined = joined.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not (DISPATCH_PREVIEW_CUE.search(joined) or config.business in joined):
        return None
    missing = []
    if config.business not in joined:
        missing.append("business")
    if f"{config.duration_hours}時間" not in joined:
        missing.append("duration")
    names = team_names(config.team)
    if not names or any(n not in joined for n in names):
        missing.append("team")
    forbidden = dialog_forbidden(texts or ())
    return {"matched": not missing and not forbidden, "missing": missing, "forbidden": forbidden}


def select_loading(slots) -> bool:
    """営業アイドル選択 still loading: every slot reads 「未選択」 with no card and no duration (2026-10-12 live)."""
    return bool(slots) and all("未選択" in (s.get("name") or "") and not s.get("filled") and not s.get("duration")
                               for s in slots)


def select_binding(slots, config, forbidden=()):
    """2026-10-10: the binding of the 営業アイドル選択 page before 決定. ``slots``: one dict per visible slot with ``name``
    (header OCR), ``duration`` (「12時間00分」 OCR) and ``filled`` (cards read, 0..5). Matched only when exactly three slots
    are shown, every slot names the configured business and reads 12時間, every slot has all five cards filled, and no
    resource word / dialog is present. Returns ``{"matched", "problems", "slots"}``; the team itself is the game's own
    prefill (icons only) and is recorded by the caller, never compared."""
    problems = []
    slots = list(slots or ())
    if len(slots) != SELECT_SLOTS:
        problems.append("slot_count")
    norm = str.maketrans("０１２３４５６７８９", "0123456789")
    for index, slot in enumerate(slots, 1):
        name = str(slot.get("name") or "").replace(" ", "")
        duration = str(slot.get("duration") or "").replace(" ", "").translate(norm)
        if config.business not in name:
            problems.append(f"business_slot{index}")
        if f"{config.duration_hours}時間00分" not in duration:
            problems.append(f"duration_slot{index}")
        if slot.get("filled") != SELECT_CARDS_PER_SLOT:
            problems.append(f"not_filled_slot{index}")
    if forbidden:
        problems.append("forbidden_word")
    return {"matched": not problems, "problems": problems, "slots": slots}


def badge_kind(bgr):
    """P / S badge of one Ex skill entry from its centre pixel (BGR): the P badge is pink, the S badge blue; else None."""
    try:
        b, g, r = (int(v) for v in bgr[:3])
    except Exception:
        return None
    if r > b + 40 and r > g + 60:
        return "P"
    if b > r + 40 and b > g:
        return "S"
    return None


def ex_skill_entries(rows, badges=()):
    """Ex skill entries of the 獲得報酬 list from OCR rows ``{"text", "box"}`` of one column (pairs of lines: idol name
    above, effect text below; entry k starts at y ~ 122 + 106*k). ``badges``: optional P/S per entry index. The
    entries are only those visible without scrolling (the caller flags ``partial``)."""
    entries = {}
    for row in rows:
        text = str(row.get("text") or "").strip()
        box = row.get("box") or ()
        if not text or len(box) != 4:
            continue
        index = int((box[1] + box[3] / 2 - 122) // 106)
        if index < 0:
            continue
        entries.setdefault(index, []).append((box[1], text))
    out = []
    for index in sorted(entries):
        lines = [t for _, t in sorted(entries[index])]
        out.append({"index": index, "idol": lines[0], "effect": " ".join(lines[1:])[:60],
                    "badge": badges[index] if index < len(badges) else None})
    return out


def dispatch_readback(kinds_before, texts, config):
    """After an unconfirmed (no dialog) 一括営業: which formerly empty slots now show the configured business name.
    ``texts``: per-slot card texts of the new top. Returns the slot indexes WITHOUT the business name (record only:
    the team and the 12 h duration cannot be read from the slot card)."""
    out = []
    for i, kind in enumerate(kinds_before or ()):
        if kind != "empty":
            continue
        card = "".join(str(t) for t in (texts[i] if texts and i < len(texts) else ())).replace(" ", "")
        if config.business not in card:
            out.append(i + 1)
    return out


def dialog_forbidden(texts):
    """The resource words found in a dialog's text (any -> the dialog is never confirmed)."""
    joined = "".join(str(t) for t in texts).replace(" ", "")
    return [w for w in FORBIDDEN_DIALOG_WORDS if w in joined]


def takeover_slot(events):
    """The slot a WORK REPORT found on screen at the start of a run can be attributed to: exactly one slot is
    uncertain today (open ``claim_intent`` or ``claim_uncertain``), nothing unscoped is uncertain, and the latest
    slot-scoped claim event refers to that slot. Otherwise ``None`` (the report is logged without a slot and nothing
    is resolved)."""
    blocked, unscoped = uncertain_slots(events)
    if unscoped or len(blocked) != 1:
        return None
    slot, = blocked
    last = next((e for e in reversed(list(events)) if e.get("event") in ("claim_intent", "claim_uncertain", "claimed")
                 and type(e.get("slot")) is int), None)
    return slot if last is not None and last.get("slot") == slot and last.get("event") != "claimed" else None


def classify_slot(completed, anchor_hit, texts):
    """One slot of the business top: ``completed`` (CLEAR + 営業完了 + pink), ``empty`` (no CLEAR / 営業完了 anchor, no
    readable timer / digit / business name / lock text, AND positive 「未選択」 / 「営業先選択」 text; native sample
    2026-10-03 20:07), ``tea_time`` (TeaTime / ティータイム text together with 営業完了 text and no CLEAR anchor) or
    ``other``. Must be read on two frames before any tap."""
    if completed:
        return "completed"
    lines = [str(t).replace(" ", "") for t in texts]
    if not anchor_hit and any(TEA_TIME_TEXT.search(t) for t in lines) and any("完了" in t for t in lines):
        return "tea_time"  # finished, in the Tea Time window (TeaTime cup + 「ティータイム残り」 + 営業完了)
    if anchor_hit or any(BUSY_TEXT.search(t) for t in lines):
        return "other"
    return "empty" if any(EMPTY_TEXT.search(t) for t in lines) else "other"


def first_empty(kinds, blocked=frozenset(), unscoped=False):
    """``(slot, reason)`` for sample_dispatch_open: the first ``empty`` slot; a first empty slot whose claim is
    uncertain today stops (never skip to a later slot); no empty slot -> ``(None, "no_empty_slot")``. B6h review
    #2: an unscoped uncertain record (no slot: error 1012, business_error, an open bulk press) stops before any
    slot is chosen -- a positive empty reading of some slot never proves which slot that record belonged to."""
    if unscoped:
        return None, "uncertain_result_today"
    first = next((i + 1 for i, k in enumerate(kinds) if k == "empty"), None)
    if first is None:
        return None, "no_empty_slot"
    if first in blocked:
        return None, "uncertain_slot_today"
    return first, None


def reobserve_blocked(blocked, kinds):
    """B6h review #1: re-observation of today's slot-scoped uncertain claims on a stable business top (``kinds``:
    ``completed`` / ``empty`` / ``other`` per slot). Returns ``(resolved, unknown)``: a blocked slot that now reads
    positively ``empty`` (「未選択」/「営業先選択」) was claimed -> ``resolved``; one that still reads ``completed`` stays
    blocked (neither list); any other reading (``other``: Tea Time, in progress, unreadable, missing) is ``unknown``
    and must stop the run -- an unknown slot state never resolves an uncertain record."""
    resolved, unknown = [], []
    for slot in sorted(blocked):
        kind = kinds[slot - 1] if 1 <= slot <= len(kinds) else None
        if kind == "empty":
            resolved.append(slot)
        elif kind != "completed":
            unknown.append(slot)
    return tuple(resolved), tuple(unknown)


def report_valid(values):
    """B6h review #4: the minimum WORK REPORT reading that may count as a read report (and prove a claim): both
    総獲得Exp and 総獲得ファン人数 read as positive integers. A reading with either missing is unknown: never stable,
    never ``work_report`` / ``claimed`` / ``bulk_completed``."""
    values = values or {}
    return all(type(values.get(key)) is int and values[key] > 0 for key in ("exp", "fans"))


def report_number(text):
    """Digits of a WORK REPORT reading (「500」, 「237,500人」) as int, or None."""
    digits = re.sub(r"[^0-9]", "", str(text or "").translate(str.maketrans("０１２３４５６７８９", "0123456789")))
    return int(digits) if digits and len(digits) <= 12 else None


def first_claim(states, blocked=frozenset(), unscoped=False):
    """``states``: tuple of ``completed`` / ``other`` per slot 1..n (screen order). Returns ``(slot, reason)``: the
    first completed slot, or ``(None, "no_completed_slot")``; a first completed slot that is uncertain today (or any
    unscoped uncertainty) stops everything -- never skip it to claim a later slot."""
    first = next((i + 1 for i, s in enumerate(states) if s == "completed"), None)
    if first is None:
        return None, "no_completed_slot"
    if unscoped:
        return None, "uncertain_result_today"
    if first in blocked:
        return None, "uncertain_slot_today"
    return first, None


class BusinessRun(tc.TaskRun):
    SUCCESS = SUCCESS_OUTCOMES
    HALT = HALT_OUTCOMES

    def __init__(self, config, log):
        super().__init__(config, log)
        self.saver = None
        self.left_home_at = None
        self.last_save = None
        self.samples = []
        self.home_text = None
        # sample_collect_one
        self.top_reading = None       # (states, clock) of the last business-top reading (stability check)
        self.claim_slot = None
        self.claim_at = None
        self.cleared_at = None        # first clock the claimed slot read "not CLEAR" on the business top
        self.pending = None           # outcome to report once Home is verified after the back key
        self.popups = 0
        # B6d WORK REPORT / sample_dispatch_open
        self.takeover = False         # the run started on a WORK REPORT (no tap of this run caused it)
        self.report_since = None
        self.report_reading = None    # (values, clock) of the last report reading
        self.report_at = None         # clock of the 「Touch to Next」 tap
        self.claim_logged = False     # ``claimed`` written in this run (own claim or resolved takeover)
        self.after_reading = None     # (kinds, clock) of the business top after the report
        self.open_slot = None
        self.dispatch_at = None
        self.dispatch_seen = 0        # frames observed off the business top after the empty-slot tap
        self.dispatch_page_seen = 0   # B6h: of those, frames with positive dispatch-page evidence
        self.back_at = None
        # B6e bulk_default
        self.click_mark = None        # controller mark taken right before a tap (refused-vs-sent after a failure)
        self.bulk_done = set()        # bulk actions pressed in this run ("complete" / "dispatch"; each at most once)
        self.bulk_action = None       # the bulk action whose result is being observed
        self.bulk_at = None           # clock of the last bulk press / 「Touch to Next」
        self.bulk_reports = 0         # WORK REPORTs read in this run after 一括完了
        self.bulk_gap = False         # a non-report frame was seen after 「Touch to Next」 (a new report may follow)
        self.bulk_dialog = None       # OCR text of an unknown dialog after a bulk press (evidence only)
        self.dispatch_kinds = None    # batch 4: slot kinds of the stable top that decided 一括営業
        self.dispatch_binding = None  # batch 4: bt.dispatch_binding of the preview read after 一括営業
        self.dispatch_unverified = False  # batch 4: 一括営業 proven without any bound confirmation
        self.bulk_dialog_at = None    # clock of the last dialog OCR (at most every 2 s)
        self.bulk_next = None         # the press decided on the last stable top reading
        # B6f paged WORK REPORT
        self.report_variant = None    # "touch" (Touch to Next) / "paged" (次へ) of the report being handled
        self.report_logged = False    # work_report already written for the report on screen (later pages: no log)
        self.after_gap = False        # a non-report frame was seen after Touch to Next / 次へ (collect / takeover)
        self.report_presses = 0       # Touch to Next + 次へ presses in this run
        # B6g
        # 2026-10-10 reward / select pages after the last report
        self.reward_reading = None    # (True, clock) of the stable 獲得報酬 guard
        self.select_reading = None    # (binding key, clock) of the stable 営業アイドル選択 reading
        self.select_binding = None    # bt.select_binding of the stable reading
        self.post_at = None           # clock of the last OK / 決定 / ← press
        self.post_dialog = None       # texts of an unknown screen after those presses (evidence only)
        self.home_seen = 0            # consecutive Home frames after 決定
        self.back_outcome = None      # outcome to report after the ← of the select page
        self.ready_seen = False       # bulk_ready: the fresh frame after the decision was handed to Bulk.X
        self.bulk_refusals = 0        # bulk presses refused by the controller before any gesture (retried)
