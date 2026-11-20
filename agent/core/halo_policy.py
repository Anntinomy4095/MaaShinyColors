"""say "Halo" decisions (H2b): pure functions over the readings of agent/core/halo_screen.

The dig route (掘书线) only (「默认不要强fes线 只做掘书线」; the 强fes线 of 2026-11-02 is gone): collect
advice, raise チーム完成度 with team practice, do 施設効率相談 and its + version, then give up -- a give-up still yields the
book (H0).

Nothing here taps: the navigator turns a choice into one guarded click and re-reads the result.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Mapping, Sequence

ROUTES = ("dig",)
ADVICE_KEYS = ("vo", "da", "vi", "me", "hirameki", "rest", "work", "limit", "best")
# 施設効率相談 / + (wiki ノウハウ table): Me 閃 休 仕 限; ベスト substitutes any kind.
FACILITY_COST = {"me": 4, "hirameki": 4, "rest": 4, "work": 4, "limit": 4}
FACILITY_PLUS_COST = {"me": 8, "hirameki": 8, "rest": 8, "work": 8, "limit": 12}
FACILITY_AFFECTION, FACILITY_PLUS_AFFECTION = 50, 80
REST_STAMINA_PCT = 10          # Halo lessons do not lose stats on trouble and advice still counts (H1 S2W3 at 84 %)
LOW_STAMINA_PCT = 30           # みんなで休憩 (stamina +50) when it is affordable
ROOM_STAMINA_PCT = {"work_magazine": 10}   # 10 stamina vs 20/25 for the others (wiki)


@dataclasses.dataclass(frozen=True)
class Goals:
    """What the dig route still wants from this run (from the user's knowhow targets)."""
    facility: bool = True          # 施設効率相談 (also the precondition of +)
    facility_plus: bool = True     # 施設効率相談+
    completion: int | None = 100   # チーム完成度 to reach (100: 延長+ / 昂揚; 51: 延長); None: not wanted
    # Fans this run may collect (the user's targets exclude ファンの声援, which a run past 10万 fans writes into the
    # book); None: no cap. The schedule page's fan total (H2d live 2026-11-06: 114,204 in S3 week 7, the 目標ライブ
    # included) or, unread, the auditions Maa entered.
    fans_cap: int | None = None

    @classmethod
    def from_dict(cls, value) -> "Goals":
        """RunOptions.halo_goals -> Goals; ValueError on unknown keys or bad values."""
        names = {f.name for f in dataclasses.fields(cls)}
        if not isinstance(value, Mapping) or set(value) - names:
            raise ValueError("halo_goals 只能含 " + "/".join(sorted(names)))
        goals = cls(**value)
        if type(goals.facility) is not bool or type(goals.facility_plus) is not bool:
            raise ValueError("halo_goals facility/facility_plus 须为布尔值")
        if goals.completion is not None and (type(goals.completion) is not int or not 1 <= goals.completion <= 100):
            raise ValueError("halo_goals completion 须为 1..100 或 null")
        if goals.fans_cap is not None and (type(goals.fans_cap) is not int or goals.fans_cap <= 0):
            raise ValueError("halo_goals fans_cap 须为正整数或 null")
        return goals

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    def any(self) -> bool:
        """Something for the dig route to work toward."""
        return self.facility or self.facility_plus or self.completion is not None


FANS_CAP = 100_000             # past 10万 fans the run writes ファンの声援 into the book
# Auditions stop this far under the cap: lessons, work and 目標ライブ add fans too (live 2026-11-15: 99,840 after the
# last audition that fit, 101,050 a week later without one).
FANS_CAP_MARGIN = 10_000
_EXTEND_PLUS = re.compile(r"^(パッシブスキル延長\+|昂揚)")


def goals_from_wanted(wanted) -> Goals | None:
    """The dig route's goals from the user's wanted list (prefs.knowhow_wanted: every Target's wanted items; excluded
    ones are not in it). None when no item is for say "Halo" (S.T.E.P.-only items are ignored).

    施設効率相談 / 施設効率相談+ when listed; チーム完成度 100 for パッシブスキル延長+ or 昂揚, 51 for パッシブスキル延長; a 10万
    fans cap unless ファンの声援 is wanted."""
    names = [str(getattr(w, "name", "") or "") for w in wanted or ()
             if getattr(w, "purpose", "any") in ("halo", "any")]
    names = [n for n in names if n]
    if not names:
        return None
    has = lambda prefix: any(n.startswith(prefix) for n in names)  # noqa: E731
    completion = 100 if any(_EXTEND_PLUS.match(n) for n in names) else 51 if has("パッシブスキル延長") else None
    return Goals(facility=has("施設効率相談"), facility_plus=has("施設効率相談+"), completion=completion,
                 fans_cap=None if has("ファンの声援") else FANS_CAP)


@dataclasses.dataclass
class HaloMemory:
    route: str = "dig"
    goals: Goals = dataclasses.field(default_factory=Goals)
    growth: bool = False           # 成長 automated (wing_nav sets it since H2c batch 3)
    growth_weeks: set = dataclasses.field(default_factory=set)       # (season, weeks_left) with 成長 visited
    team_info_seasons: set = dataclasses.field(default_factory=set)
    completion: int | None = None
    facility_done: bool = False
    facility_plus_done: bool = False
    fans_est: int = 0              # fans of the auditions entered this run (each at 100 %)
    fans_seen: int | None = None   # the schedule page's fan total, last read
    disbanded: bool = False        # S2: every recruited member left (チーム編集, wiki: fewer members raise 完成度 faster)

    def __post_init__(self):
        if self.route not in ROUTES:
            raise ValueError("route 只能为 dig")


# HaloMemory facts kept in the run memory file (wing_nav RunMemory.snapshot, H2d 2026-11-07: an engine restart used to
# forget 施設効率相談 done, the 成長 weeks and the fan count); route, goals and growth come from the run options.
SAVED_FACTS = ("completion", "facility_done", "facility_plus_done", "fans_est", "fans_seen", "growth_weeks",
               "team_info_seasons", "disbanded")


def save_facts(mem: HaloMemory) -> dict:
    out = {k: getattr(mem, k) for k in SAVED_FACTS}
    # the season may read "camp" (合宿, live 2026-11-15): sort as text so int and str keys mix
    out["growth_weeks"] = sorted([list(k) for k in mem.growth_weeks], key=lambda w: (str(w[0]), w[1]))
    out["team_info_seasons"] = sorted(mem.team_info_seasons, key=str)
    return out


def valid_facts(data) -> bool:
    if not isinstance(data, Mapping) or set(data) - set(SAVED_FACTS):
        return False
    ints = lambda v: v is None or type(v) is int  # noqa: E731
    season_ok = lambda v: type(v) is int or v == "camp"  # noqa: E731
    weeks = data.get("growth_weeks", [])
    seasons = data.get("team_info_seasons", [])
    return (ints(data.get("completion")) and ints(data.get("fans_seen")) and type(data.get("fans_est", 0)) is int
            and all(type(data.get(k, False)) is bool for k in ("facility_done", "facility_plus_done", "disbanded"))
            and isinstance(weeks, list) and all(isinstance(w, list) and len(w) == 2 and season_ok(w[0])
                                                and type(w[1]) is int for w in weeks)
            and isinstance(seasons, list) and all(season_ok(v) for v in seasons))


def load_facts(mem: HaloMemory, data: Mapping) -> None:
    for key in SAVED_FACTS:
        if key in data:
            setattr(mem, key, data[key])
    mem.growth_weeks = {tuple(w) for w in data.get("growth_weeks", [])}
    mem.team_info_seasons = set(data.get("team_info_seasons", []))


def week_key(read: Mapping) -> tuple | None:
    season, weeks = read.get("season"), read.get("weeks_left")
    return (season, weeks) if season is not None and isinstance(weeks, int) else None


def goals_met(mem: HaloMemory) -> bool:
    g = mem.goals
    if g.facility and not mem.facility_done or g.facility_plus and not mem.facility_plus_done:
        return False
    return g.completion is None or (mem.completion is not None and mem.completion >= g.completion)


def hub_choice(read: Mapping, mem: HaloMemory) -> str:
    """HaloHub: "give_up" | "live" | "growth" | "team_info" | "rest" | "produce"."""
    button = read.get("hub_button")
    season = read.get("season")
    if goals_met(mem):
        return "give_up"
    if button == "final_live":
        return "give_up"           # the dig route never needs the Final Live; what it got is already on the book
    key = week_key(read)
    if mem.growth and season not in (None, 1) and key is not None and key not in mem.growth_weeks:
        return "growth"            # S1: 成長 is locked; before a live too (H1: the final hub's 成長 first)
    if button in ("first_live", "final_live"):
        return "live"
    if mem.goals.completion is not None and season not in (None, 1) \
            and (season not in mem.team_info_seasons or not mem.disbanded):
        return "team_info"         # read 完成度 once a season; from S2 also disband the recruits (チーム編集)
    stamina = read.get("stamina_pct")
    if isinstance(stamina, int) and stamina <= REST_STAMINA_PCT:
        return "rest"
    return "produce"


def advice_weights(mem: HaloMemory, have: Mapping[str, int] | None) -> dict[str, float]:
    """How much one more advice of each kind is worth now: kinds the route's next practice still lacks count 3, the
    others 1; ベスト substitutes any kind (2)."""
    weights = {k: 1.0 for k in ADVICE_KEYS}
    weights["best"] = 2.0
    cost = FACILITY_COST if not mem.facility_done else FACILITY_PLUS_COST
    if mem.goals.facility_plus or not mem.facility_done:
        for k, n in cost.items():
            if (have or {}).get(k, 0) < n:
                weights[k] = 3.0
    return weights


def lesson_choice(read: Mapping, mem: HaloMemory) -> str | None:
    """HaloLesson: the room whose companions give the most wanted advice; None when the rooms are unread."""
    rooms = read.get("rooms")
    if not rooms:
        return None
    weights = advice_weights(mem, read.get("advice"))
    stamina = read.get("stamina_pct")
    low = isinstance(stamina, int) and stamina <= LOW_STAMINA_PCT

    def score(room):
        kinds = room.get("advice") or []
        if "?" in kinds:
            return (-1.0, 0, 0)
        cheap = 1 if low and room["id"] in ROOM_STAMINA_PCT else 0
        return (sum(weights[k] for k in kinds), len(kinds), cheap)

    best = max(rooms, key=score)
    return best["id"] if score(best)[0] >= 0 else None


_FACILITY = re.compile(r"施設")           # not ビジュアル効率相談 (H1 S2 card)
_REST = re.compile(r"休憩")


def practice_choice(cards: Sequence[Mapping], read: Mapping, mem: HaloMemory) -> int | None:
    """HaloPractice after a carousel survey: index of the card to do, or None (do something else this week).

    A card is doable when its 練習効率 reads 100 % and its note is not 不足 (ベスト代用 is fine)."""
    def doable(card):
        return card.get("rate") == 100 and card.get("note") != "short"

    named = lambda pattern: [c for c in cards if pattern.search(c.get("name") or "")]  # noqa: E731
    stamina = read.get("stamina_pct")
    if mem.goals.facility and not mem.facility_done or mem.goals.facility_plus and not mem.facility_plus_done:
        for card in named(_FACILITY):
            if doable(card):
                return card["index"]
    if isinstance(stamina, int) and stamina <= LOW_STAMINA_PCT:
        for card in named(_REST):
            if doable(card):
                return card["index"]
    facility_pending = (mem.goals.facility and not mem.facility_done
                        or mem.goals.facility_plus and not mem.facility_plus_done)
    if mem.goals.completion is not None and not goals_met(mem) and not facility_pending:
        # Completion rises with team practice (wiki); the other practices spend advice only once 施設効率相談 / + no
        # longer need it.
        others = [c for c in cards if doable(c) and not _FACILITY.search(c.get("name") or "")
                  and not _REST.search(c.get("name") or "")]
        if others:
            return max(others, key=lambda c: c.get("rate") or 0)["index"]
    return None


def audition_choice(cards: Sequence[Mapping], mem: HaloMemory, season=None) -> int | None:
    """HaloAudition after a survey: a 100 % card (+1 ベスト, 親愛度 +10), never one showing 出場条件 (H1: the user passed
    over such a card at 100 %); None when none. dig with a fans cap: only cards that keep the run under it (once the
    total is past the cap, ファンの声援 is already earned and the cap no longer applies); the most fans among them.
    Auditions keep ``FANS_CAP_MARGIN`` under the cap for the fans of the other actions."""
    sure = [c for c in cards if c.get("rate") == 100 and isinstance(c.get("fans"), int) and not c.get("condition")]
    if mem.goals.fans_cap is not None:
        fans = max(mem.fans_est, mem.fans_seen or 0)
        if fans < mem.goals.fans_cap:
            sure = [c for c in sure if fans + c["fans"] <= mem.goals.fans_cap - FANS_CAP_MARGIN]
    return max(sure, key=lambda c: c["fans"])["index"] if sure else None


def note_audition(card: Mapping, mem: HaloMemory) -> None:
    """An audition was entered (決定): its fans count toward the cap while the fan total is unread."""
    if isinstance(card.get("fans"), int):
        mem.fans_est += card["fans"]


def facility_pending(mem: HaloMemory) -> bool:
    g = mem.goals
    return g.facility and not mem.facility_done or g.facility_plus and not mem.facility_plus_done


def affection_at_least(read: Mapping, need: int) -> bool:
    """親愛度 ≥ ``need`` on this read (the Hub's 「Lv.N NEXTあと M」); Lv.5 (no NEXT) is past every target."""
    value = read.get("affection_value")
    if isinstance(value, int):
        return value >= need
    return ((read.get("affection") or {}).get("level") or 0) >= 5


def week_order(read: Mapping, mem: HaloMemory) -> tuple[str, ...]:
    """Tabs to try this week, in order: "practice", "audition", "lesson". S1 has the lesson tab only.

    While 施設効率相談 / + still need advice and 親愛度 already reaches 50, a lesson (2-5 advice) comes before an audition
    (one ベスト). Live 2026-11-15: every S2 week went to auditions, 施設効率相談 waited until S3 and + ended at ~23 of
    its 44 advice; 親愛度 itself was 85 by the end of S2 (成長 buys +10 a week)."""
    if read.get("season") in (None, 1):
        return ("lesson",)
    if facility_pending(mem) and affection_at_least(read, FACILITY_AFFECTION):
        return ("practice", "lesson", "audition")
    return ("practice", "audition", "lesson")


def live_choice(rates: Sequence[int | None], hub_button: str | None, mem: HaloMemory) -> int | None:
    """目標ライブ: index of the option with the highest 成功率 (ties: the upper one). None when no rate reads."""
    known = [(r, i) for i, r in enumerate(rates) if isinstance(r, int)]
    if not known:
        return None
    best_rate, best = max(known, key=lambda t: (t[0], -t[1]))     # ties: the upper card
    return best


def growth_plan(read: Mapping, mem: HaloMemory) -> dict:
    """成長 visit: {"affection": purchases of 親愛度+10 (dig: toward the next 施設効率相談's 親愛度), "stats": True}."""
    value = read.get("affection_value")
    want = 0
    g = mem.goals
    # 施設効率相談+ wanted: buy toward its 80 from the start (lessons replace the auditions' +10 once 50 is reached)
    target = (FACILITY_PLUS_AFFECTION if g.facility_plus and not mem.facility_plus_done
              else FACILITY_AFFECTION if g.facility and not mem.facility_done else 0)
    if isinstance(value, int) and value < target:
        want = 1                    # H1: one purchase registered per visit (the + greyed after it)
    return {"affection": want, "stats": True}


def recruit_choice(candidates: Sequence[Mapping], read: Mapping, mem: HaloMemory) -> int | None:
    """勧誘: the candidate whose advice kinds add the most weight (kinds already plentiful count less); None when the
    candidates' kinds are unread (the caller then keeps the game's default)."""
    if not candidates or any(not c.get("advice") for c in candidates):
        return None
    weights = advice_weights(mem, read.get("advice"))
    return max(candidates, key=lambda c: (sum(weights.get(k, 1.0) for k in c["advice"]), -c["index"]))["index"]
