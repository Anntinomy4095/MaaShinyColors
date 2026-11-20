"""W.I.N.G. weekly state model (Produce P2 foundation): typed, strict JSON (de)serialisation, no recognition.

Rules of the model:

* A value that was not read is ``None`` (unknown), never 0. Validation reports it; the policy decides whether the
  unknown blocks a decision.
* ``week`` is the week index inside the season (1..8). The game shows 「残り N 週」; the reader converts with
  ``week = 9 - N``. Week count and season gates come from community wikis and still need native samples.
* ``wins`` is tracked by the agent from verified audition result screens (not read from the weekly page); it is the
  only history the policy uses to know which planned auditions of the season are done.
* Stats use the screen abbreviations Vo / Da / Vi plus Me (メンタル), unlike the legacy ``models.ATTRIBUTES``.
"""
from __future__ import annotations

import dataclasses
import json
import math
from typing import Any, Mapping

ATTRIBUTES = ("Vo", "Da", "Vi")
STATS = ATTRIBUTES + ("Me",)
CONDITIONS = ("絶好調", "好調", "普通", "不調", "絶不調")   # 体調; display names, levels unverified
BAD_CONDITIONS = ("不調", "絶不調")
PHASES = ("week", "semifinal", "final", "result")
OPTION_KINDS = ("lesson", "work", "rest", "outing", "audition", "other")
# W.I.N.G. has no お出かけ (wiki プロデュース + first TE run HUD). "outing" stays in the schema for other scenarios,
# but a W.I.N.G. week state containing one is treated as an unknown screen (validate -> outing_not_in_wing).
WING_EXCLUDED_KINDS = ("outing",)
SEASONS = 4
WEEKS_PER_SEASON = 8
# Season-end promotion gates (cumulative fans; wiki, unverified) and the True End requirement.
SEASON_FAN_GATES = {1: 1_000, 2: 10_000, 3: 50_000, 4: 100_000}
TRUE_END_FANS = 500_000
# Rank letters shown for audition entry (「ランクD以上」); order low -> high. Observed: S2 15k needs D, S3 50k needs C,
# S4 100k (THE LEGEND) needs B (unit7 run, 2026-10-04).
RANKS = ("F", "E", "D", "C", "B", "A", "S")
SCHEMA_VERSION = 1


def _check_keys(value: Any, required: set, optional: set, name: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是对象")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown or missing:
        raise ValueError(f"{name} 字段错误：未知={sorted(unknown)}，缺少={sorted(missing)}")
    return dict(value)


def _opt_int(value: Any, name: str, low: int = 0, high: int | None = None) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ValueError(f"{name} 必须是 {low}..{high if high is not None else '∞'} 的整数或 null")
    return value


def _opt_float(value: Any, name: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low}..{high} 的有限数或 null")
    return float(value)


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} 必须是非空且无首尾空格的字符串")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} 必须是布尔值")
    return value


def _enum(value: Any, choices: tuple, name: str, *, nullable: bool = False) -> Any:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} 必须为 {choices} 之一{'或 null' if nullable else ''}")
    return value


def _support_ids(value: Any) -> tuple:
    if (not isinstance(value, list) or len(value) > 5 or len(value) != len(set(map(str, value)))
            or not all(isinstance(v, str) and v and v.strip() == v for v in value)):
        raise ValueError("option.support_ids 必须是至多 5 个不重复的非空字符串")
    return tuple(value)


@dataclasses.dataclass(frozen=True)
class ScheduleOption:
    """One weekly action button. ``preview`` is the on-screen gain preview per stat (unknown stats omitted)."""
    id: str
    kind: str
    attribute: str | None = None       # lesson / work attribute when shown, e.g. "Vo"
    label: str = ""
    available: bool = True
    preview: Mapping[str, int] = dataclasses.field(default_factory=dict)
    stamina_cost: int | None = None
    supports: int | None = None          # support idols shown under the room this week (None = unread)
    stamina_recovery: bool = False       # a present support has a stamina-recovery passive for this room
    fan_gain: int | None = None          # fans shown on a work preview (S1 can reach 1k via work)
    # ---- optional scorer inputs (2026-10-05, wing_score v1); None = unread, never guessed ----
    level: int | None = None             # facility Lv shown on the schedule screen (1..5)
    excellent: int | None = None         # supports in this room showing an Excellent marker
    sp_gain: int | None = None           # SP shown on the preview
    trouble_rate: float | None = None    # this option's own トラブル率 (magazine uses a different base than lessons)
    stamina_gain: int | None = None      # known stamina recovery in this room (e.g. マスタリー体力 Lv6 -> 18)
    support_ids: tuple = ()              # identified supports under the room (team-profile ids); () = unread
    excellent_present: bool | None = None  # room badge only; does not reveal the number of Excellent supports

    _SCORER_KEYS = ("level", "excellent", "sp_gain", "trouble_rate", "stamina_gain", "excellent_present")

    def to_dict(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "attribute": self.attribute, "label": self.label,
               "available": self.available, "preview": dict(self.preview), "stamina_cost": self.stamina_cost,
               "supports": self.supports, "stamina_recovery": self.stamina_recovery, "fan_gain": self.fan_gain}
        for key in self._SCORER_KEYS:              # emitted only when read, so older JSON stays byte-identical
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.support_ids:
            out["support_ids"] = list(self.support_ids)
        return out

    @classmethod
    def from_dict(cls, value: Mapping) -> "ScheduleOption":
        data = _check_keys(value, {"id", "kind", "available"},
                           {"attribute", "label", "preview", "stamina_cost", "supports", "stamina_recovery",
                            "fan_gain", "support_ids"} | set(cls._SCORER_KEYS), "option")
        preview = data.get("preview", {})
        if not isinstance(preview, Mapping) or set(preview) - set(STATS):
            raise ValueError("option.preview 只能包含 Vo/Da/Vi/Me")
        for key, gain in preview.items():
            if type(gain) is not int:
                raise ValueError(f"option.preview.{key} 必须是整数")
        label = data.get("label", "")
        if not isinstance(label, str):
            raise ValueError("option.label 必须是字符串")
        return cls(_str(data["id"], "option.id"), _enum(data["kind"], OPTION_KINDS, "option.kind"),
                   _enum(data.get("attribute"), ATTRIBUTES, "option.attribute", nullable=True), label,
                   _bool(data["available"], "option.available"), dict(preview),
                   _opt_int(data.get("stamina_cost"), "option.stamina_cost"),
                   _opt_int(data.get("supports"), "option.supports", 0, 5),
                   _bool(data.get("stamina_recovery", False), "option.stamina_recovery"),
                   _opt_int(data.get("fan_gain"), "option.fan_gain"),
                   _opt_int(data.get("level"), "option.level", 1, 5),
                   _opt_int(data.get("excellent"), "option.excellent", 0, 5),
                   _opt_int(data.get("sp_gain"), "option.sp_gain"),
                   _opt_float(data.get("trouble_rate"), "option.trouble_rate", 0.0, 100.0),
                   _opt_int(data.get("stamina_gain"), "option.stamina_gain"),
                   _support_ids(data.get("support_ids", [])),
                   None if data.get("excellent_present") is None else
                   _bool(data["excellent_present"], "option.excellent_present"))


@dataclasses.dataclass(frozen=True)
class Audition:
    """One audition offered this week (the list shows 4 per season).

    ``fan_reward`` on a win; ``min_fans`` fan requirement (None = unknown); ``difficulty`` the shown level;
    ``recommended`` the 推奨能力値 per stat (primary + Me, e.g. {"Vo": 450, "Me": 450}); ``required_rank`` the rank
    gate of the top tier (e.g. "D"), None when the audition has no rank gate."""
    id: str
    name: str
    fan_reward: int
    min_fans: int | None = None
    available: bool = True
    difficulty: int | None = None
    recommended: Mapping[str, int] = dataclasses.field(default_factory=dict)
    required_rank: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "fan_reward": self.fan_reward, "min_fans": self.min_fans,
                "available": self.available, "difficulty": self.difficulty, "recommended": dict(self.recommended),
                "required_rank": self.required_rank}

    @classmethod
    def from_dict(cls, value: Mapping) -> "Audition":
        data = _check_keys(value, {"id", "name", "fan_reward", "available"},
                           {"min_fans", "difficulty", "recommended", "required_rank"}, "audition")
        reward = _opt_int(data["fan_reward"], "audition.fan_reward", 1)
        if reward is None:
            raise ValueError("audition.fan_reward 必须读到")
        recommended = data.get("recommended", {})
        if not isinstance(recommended, Mapping) or set(recommended) - set(STATS):
            raise ValueError("audition.recommended 只能包含 Vo/Da/Vi/Me")
        for key, need in recommended.items():
            if type(need) is not int or need < 0:
                raise ValueError(f"audition.recommended.{key} 必须是非负整数")
        return cls(_str(data["id"], "audition.id"), _str(data["name"], "audition.name"), reward,
                   _opt_int(data.get("min_fans"), "audition.min_fans"), _bool(data["available"], "audition.available"),
                   _opt_int(data.get("difficulty"), "audition.difficulty", 1, 30), dict(recommended),
                   _enum(data.get("required_rank"), RANKS, "audition.required_rank", nullable=True))


@dataclasses.dataclass(frozen=True)
class AuditionWin:
    """A verified audition win (agent-tracked from the result screen)."""
    season: int
    fan_reward: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping) -> "AuditionWin":
        data = _check_keys(value, {"season", "fan_reward"}, set(), "win")
        season = _opt_int(data["season"], "win.season", 1, SEASONS)
        reward = _opt_int(data["fan_reward"], "win.fan_reward", 1)
        if season is None or reward is None:
            raise ValueError("win.season / win.fan_reward 不能为 null")
        return cls(season, reward)


@dataclasses.dataclass(frozen=True)
class WingState:
    phase: str
    season: int | None
    week: int | None
    stats: Mapping[str, int | None]
    fans: int | None
    stamina: int | None = None
    max_stamina: int | None = None
    condition: str | None = None
    sp: int | None = None
    memory_level: int | None = None
    trend: Mapping[str, int] | None = None      # {"Vo": 1, "Da": 2, "Vi": 3} or None when unread
    trouble_rate: float | None = None            # % shown on the action preview, if any
    stat_caps: Mapping[str, int] | None = None   # 所持スキル current caps per stat (base 500); None = unread
    rank: str | None = None                      # current rank letter (gates the top audition tier)
    promise: Mapping[str, Any] | None = None     # agent-tracked accepted 約束: {"season", "week", "attribute"}
    options: tuple = ()
    auditions: tuple = ()
    wins: tuple = ()
    confidence: float = 0.0
    recognized: bool = False
    evidence: tuple = ()
    cheer: bool | None = None                    # アイドルの応援 (大富豪) active this Produce; None = unknown

    # ---- derived ------------------------------------------------------------------------------------------------
    @property
    def weeks_left(self) -> int | None:
        """Weeks including the current one (残り N 週)."""
        return None if self.week is None else WEEKS_PER_SEASON - self.week + 1

    @property
    def season_gate(self) -> int | None:
        return SEASON_FAN_GATES.get(self.season) if self.season else None

    def season_wins(self, season: int | None = None) -> list[int]:
        season = self.season if season is None else season
        return [w.fan_reward for w in self.wins if w.season == season]

    def option(self, kind: str, attribute: str | None = None) -> ScheduleOption | None:
        """The unique available option of ``kind`` (and ``attribute``); None when absent or ambiguous."""
        found = [o for o in self.options if o.kind == kind and o.available
                 and (attribute is None or o.attribute == attribute)]
        return found[0] if len(found) == 1 else None

    def rank_at_least(self, required: str | None) -> bool | None:
        """True/False when the rank gate is decidable; None when the gate exists but the rank is unread."""
        if required is None:
            return True
        if self.rank is None:
            return None
        return RANKS.index(self.rank) >= RANKS.index(required)

    def promise_due(self) -> str | None:
        """Lesson attribute promised for this exact season/week, if any."""
        p = self.promise
        if p and p.get("season") == self.season and p.get("week") == self.week:
            return p.get("attribute")
        return None

    def validate(self) -> list[str]:
        """Reasons the state cannot drive a decision; unknown optional fields are left to the policy."""
        reasons = []
        if not self.recognized:
            reasons.append("not_recognized")
        if self.phase == "week":
            if self.season is None or self.week is None:
                reasons.append("season_or_week_unread")
            if self.fans is None:
                reasons.append("fans_unread")
            if any(self.stats.get(a) is None for a in STATS):
                reasons.append("stats_unread")
            if any(o.kind in WING_EXCLUDED_KINDS for o in self.options):
                reasons.append("outing_not_in_wing")
        if self.trend is not None and sorted(self.trend.values()) != [1, 2, 3]:
            reasons.append("trend_not_a_permutation")
        if self.stamina is not None and self.max_stamina is not None and self.stamina > self.max_stamina:
            reasons.append("stamina_above_max")
        ids = [o.id for o in self.options] + [a.id for a in self.auditions]
        if len(ids) != len(set(ids)):
            reasons.append("duplicate_option_id")
        return reasons

    # ---- JSON ---------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION, "phase": self.phase, "season": self.season, "week": self.week,
            "stats": {k: self.stats.get(k) for k in STATS}, "fans": self.fans, "stamina": self.stamina,
            "max_stamina": self.max_stamina, "condition": self.condition, "sp": self.sp,
            "memory_level": self.memory_level, "trend": None if self.trend is None else dict(self.trend),
            "trouble_rate": self.trouble_rate,
            "stat_caps": None if self.stat_caps is None else dict(self.stat_caps), "rank": self.rank,
            "promise": None if self.promise is None else dict(self.promise),
            "options": [o.to_dict() for o in self.options],
            "auditions": [a.to_dict() for a in self.auditions], "wins": [w.to_dict() for w in self.wins],
            "confidence": self.confidence, "recognized": self.recognized, "evidence": list(self.evidence),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, value: Mapping) -> "WingState":
        required = {"version", "phase", "season", "week", "stats", "fans", "confidence", "recognized"}
        optional = {"stamina", "max_stamina", "condition", "sp", "memory_level", "trend", "trouble_rate",
                    "stat_caps", "rank", "promise", "options", "auditions", "wins", "evidence"}
        data = _check_keys(value, required, optional, "wing_state")
        if data["version"] != SCHEMA_VERSION:
            raise ValueError(f"仅支持 wing_state version={SCHEMA_VERSION}")
        stats = _check_keys(data["stats"], set(STATS), set(), "stats")
        stats = {k: _opt_int(v, f"stats.{k}") for k, v in stats.items()}
        trend = data.get("trend")
        if trend is not None:
            trend = _check_keys(trend, set(ATTRIBUTES), set(), "trend")
            trend = {k: _opt_int(v, f"trend.{k}", 1, 3) for k, v in trend.items()}
            if any(v is None for v in trend.values()):
                raise ValueError("trend 要么整体 null，要么三项都读到")
        caps = data.get("stat_caps")
        if caps is not None:
            if not isinstance(caps, Mapping) or not caps or set(caps) - set(STATS):
                raise ValueError("stat_caps 只能包含 Vo/Da/Vi/Me 且不能为空对象")
            caps = {k: _opt_int(v, f"stat_caps.{k}", 1) for k, v in caps.items()}
            if any(v is None for v in caps.values()):
                raise ValueError("stat_caps 的值不能为 null（未读到就省略该项）")
        promise = data.get("promise")
        if promise is not None:
            promise = _check_keys(promise, {"season", "week", "attribute"}, set(), "promise")
            if (_opt_int(promise["season"], "promise.season", 1, SEASONS) is None
                    or _opt_int(promise["week"], "promise.week", 1, WEEKS_PER_SEASON) is None):
                raise ValueError("promise.season / promise.week 不能为 null")
            _enum(promise["attribute"], ATTRIBUTES, "promise.attribute")
        evidence = data.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
            raise ValueError("evidence 必须是字符串数组")
        for key in ("options", "auditions", "wins"):
            if not isinstance(data.get(key, []), list):
                raise ValueError(f"{key} 必须是数组")
        confidence = _opt_float(data["confidence"], "confidence", 0.0, 1.0)
        if confidence is None:
            raise ValueError("confidence 必须显式提供")
        return cls(
            phase=_enum(data["phase"], PHASES, "phase"),
            season=_opt_int(data["season"], "season", 1, SEASONS),
            week=_opt_int(data["week"], "week", 1, WEEKS_PER_SEASON),
            stats=stats, fans=_opt_int(data["fans"], "fans"),
            stamina=_opt_int(data.get("stamina"), "stamina"),
            max_stamina=_opt_int(data.get("max_stamina"), "max_stamina", 1),
            condition=_enum(data.get("condition"), CONDITIONS, "condition", nullable=True),
            sp=_opt_int(data.get("sp"), "sp"),
            memory_level=_opt_int(data.get("memory_level"), "memory_level", 0, 5),
            trend=trend, trouble_rate=_opt_float(data.get("trouble_rate"), "trouble_rate", 0.0, 100.0),
            stat_caps=caps, rank=_enum(data.get("rank"), RANKS, "rank", nullable=True), promise=promise,
            options=tuple(ScheduleOption.from_dict(o) for o in data.get("options", [])),
            auditions=tuple(Audition.from_dict(a) for a in data.get("auditions", [])),
            wins=tuple(AuditionWin.from_dict(w) for w in data.get("wins", [])),
            confidence=confidence, recognized=_bool(data["recognized"], "recognized"), evidence=tuple(evidence),
        )

    @classmethod
    def from_json(cls, text: str) -> "WingState":
        def pairs(items):
            out = {}
            for key, val in items:
                if key in out:
                    raise ValueError(f"JSON 字段重复：{key}")
                out[key] = val
            return out

        def constant(val):
            raise ValueError(f"JSON 数值非法：{val}")

        return cls.from_dict(json.loads(text, object_pairs_hook=pairs, parse_constant=constant))
