"""Plausibility and two-frame agreement for the W.I.N.G. number readings (offline-testable, no Maa, no input).

A misread counter is usually a digit too many (a badge edge read as a digit: SP 17 -> 717 on wing5b S4 week 8, the
SP badge glued to メンタル 358 -> 3581), a digit too few (fans 15451 -> 1545) or a shifted stat cell. Each reading of
season, weeks_left, Vo/Da/Vi/Me, SP and fans is compared with the last accepted value of the same field:

* impossible (outside the game's range or a jump no week can produce): dropped to None; the decision re-reads the next
  frame (bounded). The same value on ``RESET_CAPTURES`` captures replaces a history that was wrong (e.g. a new Produce);
* expected (unchanged, exactly the confirmed room's preview gain, one week later, SP spent on the skill panel):
  accepted at once;
* any other change: accepted when a later capture reads the same value (two agreeing frames), else re-read; after
  ``MAX_HOLDS`` re-reads the field is unread (None) for this decision. A field's first reading starts its history.

Only ``HOLD_SCREENS`` wait for a re-read; elsewhere an impossible value is dropped and a changed value passes through
without becoming history. The accepted values live in ``RunMemory.numbers`` (snapshot: survives segment boundaries).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

STATS = ("Vo", "Da", "Vi", "Me")
FIELDS = ("season", "weeks_left", *STATS, "sp", "fans")
HOLD_SCREENS = frozenset({"Schedule", "Hub", "HubSemiFinal", "HubFinal", "Review", "Owned", "ReplaceConfirm",
                          "LearnConfirm"})
SP_PANEL_SCREENS = frozenset({"Review", "Owned", "ReplaceConfirm", "LearnConfirm"})
MAX_HOLDS = 3                 # re-reads of one decision before an unconfirmed field is left unread
RESET_CAPTURES = 4            # captures agreeing on an "impossible" value replace the history
# Ranges and per-week gains, from the demo recordings and live runs 10-09..10-29:
# stats <= 1347 by week 30, weekly stat gains <= ~120 (lesson + supports + events), SP +117 over the S1 final week,
# fans +302k over three S4 weeks; a failed lesson lowers stats by tens.
STAT_MAX = 3000
STAT_GAIN_WEEK = 300          # = wing_nav.STAT_JUMP_MAX
STAT_DROP_MAX = 300
STAT_KEEP_MIN = 0.3            # a lost digit keeps <= ~1/3 (358 -> 35 / 58); a failed lesson keeps far more
SP_MAX = 3000
SP_GAIN_WEEK = 200
FANS_MAX = 5_000_000
FAN_GAIN_WEEK = 600_000


def week_index(season, weeks_left) -> int | None:
    """0-based week of the Produce (シーズン1 残り8週 = 0); the W.I.N.G. stage counts as season 5."""
    s = 5 if season == "wing" else season
    if type(s) is not int or type(weeks_left) is not int or not (1 <= s <= 5 and 1 <= weeks_left <= 8):
        return None
    return (s - 1) * 8 + (8 - weeks_left)


def flatten(readings: Mapping[str, Any]) -> dict:
    """{field: int|str} of the number fields present in ``readings`` (stats unnested)."""
    out = {k: readings[k] for k in ("season", "weeks_left", "sp", "fans") if k in readings}
    stats = readings.get("stats")
    if isinstance(stats, Mapping):
        out.update({k: stats.get(k) for k in STATS})
    return out


def _season_no(value):
    return 5 if value == "wing" else value


@dataclasses.dataclass
class Verdict:
    readings: dict                     # readings with dropped/unconfirmed fields set to None (a stat: all stats)
    hold: list                         # fields waiting for the next capture (re-read before deciding)
    events: list                       # log events (rejected / confirmed / unconfirmed / reset)


class NumberGuard:
    """Checks one decision's readings. ``state`` is the persisted ``RunMemory.numbers`` dict ({"values": {field:
    [value, week_index]}, "week": index}); pending votes and impossible repeats are in-process."""

    def __init__(self):
        self.pending: dict = {}        # field -> {"value", "capture"}
        self.rejects: dict = {}        # field -> {"value", "captures": set}
        self.holds = 0
        self.hold_screen = None

    # ---- rules --------------------------------------------------------------------------------------------------
    @staticmethod
    def impossible(field: str, value, prev, elapsed: int, same_season: bool | None) -> bool:
        if field == "season":
            v, p = _season_no(value), _season_no(prev)
            return type(v) is not int or not 1 <= v <= 5 or (type(p) is int and not p <= v <= p + 1)
        if type(value) is not int or value < 0:
            return True
        weeks = max(1, elapsed)
        if field == "weeks_left":
            return not 1 <= value <= 8 or (same_season is True and type(prev) is int and value > prev)
        if field in STATS:
            return value > STAT_MAX or (type(prev) is int and (value - prev > STAT_GAIN_WEEK * weeks
                                                               or prev - value > STAT_DROP_MAX or value < prev * STAT_KEEP_MIN))
        if field == "sp":
            return value > SP_MAX or (type(prev) is int and value - prev > SP_GAIN_WEEK * weeks)
        if field == "fans":
            return value > FANS_MAX or (type(prev) is int and (value < prev or value - prev > FAN_GAIN_WEEK * weeks))
        return False

    @staticmethod
    def expected(field: str, value, prev, screen: str, ctx: Mapping[str, Any]) -> bool:
        if value == prev:
            return True
        if field == "weeks_left":
            season = ctx.get("season_change")
            return (season is False and type(prev) is int and value == prev - 1) or (season is True and value == 8)
        if field == "season":
            return type(_season_no(prev)) is int and _season_no(value) == _season_no(prev) + 1
        if field == "sp" and screen in SP_PANEL_SCREENS and type(prev) is int and value < prev:
            return True        # SP only goes down on the skill panel (learning)
        probe = ctx.get("probe")
        if probe:
            before = probe["before"].get(field)
            gain = probe["gain"].get(field, 0)
            return type(before) is int and type(gain) is int and value == before + gain
        return False

    # ---- one decision -------------------------------------------------------------------------------------------
    def check(self, screen: str, readings: Mapping[str, Any], state: dict, capture, probe=None) -> Verdict:
        """Readings of ``screen`` from ``capture`` (any hashable id of the frame: a new capture = a new id). ``state``
        is updated in place; ``probe`` is ``RunMemory.lesson_probe`` (the confirmed room and its preview gains)."""
        if screen != self.hold_screen:
            self.holds, self.hold_screen = 0, screen
        out = {k: (dict(v) if k == "stats" and isinstance(v, Mapping) else v) for k, v in readings.items()}
        values = state.setdefault("values", {})
        read = flatten(readings)
        prev_season = (values.get("season") or [None])[0]
        same_season = None if prev_season is None or read.get("season") is None else \
            _season_no(read["season"]) == _season_no(prev_season)
        ctx = {"season_change": None if same_season is None else not same_season,
               "probe": self._probe(probe, read)}
        hold, events, accepted = [], [], {}
        week = state.get("week")
        for field in FIELDS:
            if field == "Vo":
                # The week of this frame from the accepted (or unchanged) counters; else the last known week.
                current = {k: accepted.get(k, read.get(k)) for k in ("season", "weeks_left")
                           if k in accepted or (values.get(k) or [None])[0] == read.get(k)}
                now = week_index(current.get("season"), current.get("weeks_left"))
                week = now if now is not None else week
            value = read.get(field)
            if value is None or field not in read:
                continue
            prev, prev_week = (values.get(field) or [None, None])[:2]
            elapsed = week - prev_week if week is not None and prev_week is not None else 1
            if self.impossible(field, value, prev, elapsed, same_season if field == "weeks_left" else None):
                if self._reset(field, value, capture, prev, events):
                    accepted[field] = value
                    continue
                self._drop(out, field)
                events.append({"event": "number_rejected", "screen": screen, "field": field, "read": value,
                               "prev": prev})
                if screen in HOLD_SCREENS:
                    hold.append(field)
                continue
            self.rejects.pop(field, None)
            if prev is None or self.expected(field, value, prev, screen, ctx):
                self.pending.pop(field, None)      # the first reading starts the history (it persists per Produce)
                accepted[field] = value
                continue
            if screen not in HOLD_SCREENS:
                continue                           # passes through; a hold screen confirms it later
            pend = self.pending.get(field)
            if pend is not None and pend["value"] == value and pend["capture"] != capture:
                self.pending.pop(field)
                accepted[field] = value
                events.append({"event": "number_confirmed", "screen": screen, "field": field, "read": value,
                               "prev": prev})
                continue
            self.pending[field] = {"value": value, "capture": capture}
            hold.append(field)
        for field, value in accepted.items():
            values[field] = [value, week]
        if week is not None:
            state["week"] = week
        if hold and screen in HOLD_SCREENS and self.holds < MAX_HOLDS:
            self.holds += 1
            for field in hold:
                self._drop(out, field)
            return Verdict(out, hold, events)
        if hold:
            for field in hold:
                self._drop(out, field)
                self.pending.pop(field, None)
            events.append({"event": "number_unconfirmed", "screen": screen, "fields": hold})
        self.holds = 0
        return Verdict(out, [], events)

    def _reset(self, field, value, capture, prev, events) -> bool:
        if field != "season" and (type(value) is not int or value < 0 or value > {
                "weeks_left": 8, "sp": SP_MAX, "fans": FANS_MAX}.get(field, STAT_MAX)) or (
                field == "weeks_left" and value < 1):
            return False                          # out of the game's range: never history
        seen = self.rejects.get(field)
        if seen is None or seen["value"] != value:
            seen = self.rejects[field] = {"value": value, "captures": set()}
        seen["captures"].add(capture)
        if len(seen["captures"]) < RESET_CAPTURES:
            return False
        self.rejects.pop(field)
        events.append({"event": "number_reset", "field": field, "read": value, "prev": prev})
        return True

    @staticmethod
    def _probe(probe, read) -> dict | None:
        """Before-values and preview gains of the room confirmed last week, once the week counter moved on."""
        if not isinstance(probe, Mapping) or not isinstance(probe.get("gain"), Mapping):
            return None
        key = probe.get("key")
        if not isinstance(key, list) or [read.get("season"), read.get("weeks_left")] == key:
            return None
        before = dict(probe.get("stats") or {})
        before.update({k: probe.get(k) for k in ("sp", "fans")})
        return {"before": before, "gain": dict(probe["gain"])}

    @staticmethod
    def _drop(out: dict, field: str) -> None:
        # One doubtful stat makes the row unread: the scorers take all four stats or none (wing_nav.build_week_state).
        out["stats" if field in STATS else field] = None


# Non-numeric season readings: the W.I.N.G. stage, and say "Halo"'s 合宿 week (H2d live 2026-11-06: a saved "camp"
# made every later segment refuse its memory file, "numbers 格式无效").
SEASON_WORDS = ("wing", "camp")


def valid_state(data) -> bool:
    """Shape check for a restored ``RunMemory.numbers``."""
    if data is None:
        return True
    if not isinstance(data, Mapping) or set(data) - {"values", "week"}:
        return False
    if data.get("week") is not None and type(data["week"]) is not int:
        return False
    values = data.get("values") or {}
    return isinstance(values, Mapping) and all(
        k in FIELDS and isinstance(v, list) and len(v) == 2 and (type(v[0]) is int or v[0] in SEASON_WORDS)
        and (v[1] is None or type(v[1]) is int) for k, v in values.items())
