"""End-of-Produce summary, keep/release recommendation and notifications (W.I.N.G. first; pure, offline).

The end of a Produce walks through 社長からの評価 (Evaluation) -> フェスアイドル誕生 (FesBirth) -> 確認 (FesBirthConfirm)
-> 総合評価 (TotalEval) -> プロデュースクリア報酬 (ClearReward) -> 獲得報酬 (Reward: Exスキル / ノウハウブック) ->
フェスアイドル詳細 (FesDetail) -> TrueEnd commu -> home ミッション達成.

This module never clicks and never decides to delete anything:

* ``EndCollector`` accumulates the readings of those screens (the navigation passes what it read; unknown stays
  ``None``) and builds a ``ProduceEndSummary``.
* ``recommend(summary, rules)`` says whether the new Fes idol looks worth keeping. Its output is advice for the user:
  ``default_choice`` is always ``"keep"``, ``requires_user_confirmation`` is always ``True`` and ``auto_action`` is
  always ``None``. Releasing / selling / deleting is done by the user in the game; a future automated path would need
  its own supervised review (deletions and uncertain results are never automatic).
* ``notifications(summary, prefs)`` lists the reminders (虹/金 EX skill, TE, lost SP, unread fields ...).
* ``end_screen_gate(screen, texts, config)`` is the contract for wing_nav: which end screens Maa may pass with a
  non-destructive confirmation, where it hands over to the user, and which dialogs are a hard stop.

wing_nav.py / wing_screen.py are owned by another agent; the hooks they need are listed in ``NAV_HOOKS`` and in the
research document. Nothing here is wired to the live runner.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1

# ---- vocabularies ----------------------------------------------------------------------------------------------
EX_TIERS = ("bronze", "silver", "gold", "rainbow")            # 銅 < 銀 < 金 < 虹 (wiki: 4 grades)
EX_TIER_LABELS = {"bronze": "铜", "silver": "银", "gold": "金", "rainbow": "虹"}
_EX_TIER_WORDS = (  # a tier reading is its own field (frame colour / label), never the skill name
    ("rainbow", ("虹", "レインボー", "rainbow")),
    ("gold", ("金", "ゴールド", "gold")),
    ("silver", ("銀", "银", "シルバー", "silver")),
    ("bronze", ("銅", "铜", "ブロンズ", "bronze")),
)
# Fes idol rank letters, low -> high. 推測: the observed ranks are "B+ RANK" (demo #964); the full ladder below follows the
# usual F..S(+) pattern and SS is accepted if it ever shows. An unlisted text stays unknown (never guessed).
RANKS = ("F", "F+", "E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+", "SS", "SS+")
STATS = ("vo", "da", "vi", "me")

# End-flow screens (same names as wing_nav.END_FLOW) and what Maa may do on each.
END_SCREENS = ("Evaluation", "FesBirth", "FesBirthConfirm", "TotalEval", "ClearReward", "Reward", "FesDetail",
               "TrueEndCommu", "MissionPopup")
# Texts that mark a destructive / spending / storage dialog anywhere in the end flow -> hard stop for the user.
# 売却 (sell knowhow book / Ex skill), 移籍 / 解放 / 削除 / 引退 (let a Fes idol go), 再抽選 (re-roll, costs 体力 30),
# 上限 / 所持枠 / 枠拡張 (storage full or expansion; expansion costs jewels), ジュエル (paid currency).
DESTRUCTIVE_MARKERS = {
    "sell": ("売却",),
    "release": ("移籍", "解放する", "削除", "引退", "お別れ"),
    "reroll": ("再抽選",),
    "storage": ("所持上限", "所持枠", "枠拡張", "上限を超え", "保管"),
    "paid": ("ジュエル", "購入"),
}
# Buttons that are always on the Reward screen (保護 / 売却 / 再抽選 tabs): their *labels* are expected there and are
# excluded by the Reward screen guard; only a confirmation dialog carrying these words is a hard stop. The navigation
# passes ``dialog=True`` when a modal is over the page (wing_screen decides that from the dimmed backdrop).
REWARD_PAGE_BUTTONS = ("保護", "売却", "再抽選")
# Static texts of the 獲得報酬 page itself (third demo #651/#685): the notice 「スタミナが無い場合や、保護・売却を行う
# 場合 再抽選できません」 and the re-roll button 「再抽選 1/1」. They carry marker words but are not a dialog.
REWARD_PAGE_TEXT = re.compile(r"を行う場合|再抽.?選?でき|^再抽.?選\d?/?\d?$")

# Hooks wing_nav.py needs (owned by another agent; contract only —).
NAV_HOOKS = {
    "end_observe": ("agent.core.produce_end", "observe_end_screen"),
    "end_gate": ("agent.core.produce_end", "end_screen_gate"),
    "end_summary": ("agent.core.produce_end", "summary_payload"),
}

# Reading fields per end screen (what wing_screen should read; any may be missing -> unknown).
READING_FIELDS = {
    "Evaluation": ("final_fans", "affection", "sp_gain"),
    "FesBirth": ("stats", "stat_caps", "sp", "fans", "live_skills"),
    "FesBirthConfirm": ("sp_lost",),
    "TotalEval": ("rank",),
    "ClearReward": ("trust_level", "communication_level", "rewards"),
    "Reward": ("reward_tab", "ex_skills", "knowhow_books", "stamina"),
    "FesDetail": ("total_fans", "live_skills"),
    "TrueEndCommu": (),
    "MissionPopup": ("mission_text",),
}


# ---- small parsers ---------------------------------------------------------------------------------------------
def _clean(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = re.fullmatch(r"[+＋]?([\d,，]+)人?", _clean(value))
    return int(re.sub(r"[,，]", "", m.group(1))) if m else None


def parse_rank(value: Any) -> str | None:
    """「総合評価 B+ RANK」/"B+"/"Ｂ＋" -> "B+"; anything not on the ladder -> None (unknown, never guessed)."""
    t = _clean(value).upper().translate(str.maketrans("ＡＢＣＤＥＦＳ＋", "ABCDEFS+"))
    t = re.sub(r"(総合評価|RANK|ランク)", "", t)
    m = re.fullmatch(r"(SS|[SABCDEF])(\+?)", t)
    if not m:
        return None
    rank = m.group(1) + m.group(2)
    return rank if rank in RANKS else None


def rank_index(rank: str | None) -> int | None:
    return RANKS.index(rank) if rank in RANKS else None


def parse_ex_tier(value: Any) -> str | None:
    """Ex skill grade from a reading: a tier key, a Japanese word (銅/銀/金/虹) or a colour name. Unknown -> None."""
    t = _clean(value).lower()
    if not t:
        return None
    if t in EX_TIERS:
        return t
    for tier, words in _EX_TIER_WORDS:
        if any(w.lower() in t for w in words):
            return tier
    return None


def parse_pair(value: Any) -> tuple[int, int] | None:
    """"6/35" -> (6, 35); a 2-sequence of ints -> itself; else None."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool)
                                                                    for v in value):
        return int(value[0]), int(value[1])
    m = re.fullmatch(r"(\d+)/(\d+)", _clean(value))
    return (int(m.group(1)), int(m.group(2))) if m else None


# ---- model -----------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class ExSkill:
    name: str | None = None
    tier: str | None = None            # EX_TIERS or None (unread)
    target: str | None = None          # "produce" | "support" | None
    idol: str | None = None

    @classmethod
    def from_reading(cls, value: Any) -> "ExSkill | None":
        if isinstance(value, str):  # a bare name: the tier is unknown (never parsed out of the name)
            return cls(name=value.strip() or None)
        if not isinstance(value, Mapping):
            return None
        target = value.get("target")
        return cls(name=(str(value["name"]).strip() or None) if value.get("name") else None,
                   tier=parse_ex_tier(value.get("tier")),
                   target=target if target in ("produce", "support") else None,
                   idol=str(value["idol"]) if value.get("idol") else None)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class KnowhowBook:
    """One entry: a knowhow line {name, level} (legacy reading of the selected book) or, from the 獲得報酬 review
    (wing_nav, 2026-10-10), a whole book: ``name`` = title 【card】idol, ``level`` = its highest knowhow Lv, plus the
    owner idol, the four stats, the knowhow rows, 保護 state and the grid index."""
    name: str | None = None
    level: int | None = None           # 1–6 (wiki: Lv.1–5, some item knowhow Lv.6)
    count: int = 1
    idol: str | None = None
    kind: str | None = None            # "idol" (【card】idol book) | "generic" (e.g. 283プロのノウハウブック) | None
    stats: Mapping[str, int] | None = None
    rows: tuple = ()                   # ({name, level, effect}, ...)
    protected: bool | None = None
    index: int | None = None           # tile index on 獲得報酬 (0-based)
    rows_partial: bool | None = None   # the knowhow list may continue below the visible rows

    @classmethod
    def from_reading(cls, value: Any) -> "KnowhowBook | None":
        if isinstance(value, str):
            return cls(name=value.strip() or None)
        if not isinstance(value, Mapping):
            return None
        if "rows" in value or "title" in value:
            rows = tuple({"name": str(r.get("name")), "level": r.get("level") if isinstance(r.get("level"), int)
                          else None, "effect": r.get("effect")} for r in value.get("rows") or ()
                         if isinstance(r, Mapping) and r.get("name"))
            levels = [r["level"] for r in rows if isinstance(r["level"], int) and 1 <= r["level"] <= 6]
            title = str(value.get("title") or "").strip() or None
            return cls(name=title, level=max(levels) if levels else None, count=1,
                       idol=str(value["idol"]) if value.get("idol") else None,
                       kind=("idol" if value.get("idol") else "generic") if title else None,
                       stats=_stats(value.get("stats")) or None, rows=rows,
                       protected=value.get("protected") if isinstance(value.get("protected"), bool) else None,
                       index=value.get("index") if isinstance(value.get("index"), int) else None,
                       rows_partial=value.get("rows_partial") if isinstance(value.get("rows_partial"), bool) else None)
        level = parse_int(value.get("level"))
        count = parse_int(value.get("count"))
        return cls(name=(str(value["name"]).strip() or None) if value.get("name") else None,
                   level=level if level is not None and 1 <= level <= 6 else None,
                   count=count if count is not None and count >= 1 else 1)

    def to_dict(self) -> dict:
        out = {"name": self.name, "level": self.level, "count": self.count}
        if self.kind is not None or self.rows:          # a whole book (獲得報酬 review)
            out.update(idol=self.idol, kind=self.kind, stats=dict(self.stats) if self.stats else None,
                       rows=[dict(r) for r in self.rows], protected=self.protected, index=self.index,
                       rows_partial=self.rows_partial)
        return out


@dataclasses.dataclass(frozen=True)
class ProduceEndSummary:
    scenario: str = "wing"
    rank: str | None = None                        # 総合評価 (Fes idol rank)
    final_fans: int | None = None
    affection: int | None = None                   # 親愛度 on 社長からの評価
    stats: Mapping[str, int] = dataclasses.field(default_factory=dict)       # vo/da/vi/me at birth
    stat_caps: Mapping[str, int] = dataclasses.field(default_factory=dict)
    sp_lost: int | None = None                     # remaining SP lost at birth
    live_skills: tuple[str, ...] = ()
    ex_skills: tuple[ExSkill, ...] = ()
    knowhow_books: tuple[KnowhowBook, ...] = ()
    rewards: tuple[str, ...] = ()
    trust_level: int | None = None
    communication_level: tuple[int, int] | None = None
    wing_won: bool | None = None
    true_end: bool | None = None                   # True: TE commu / mission seen; False: flow ended without it
    fes_idol_created: bool | None = None
    screens_seen: tuple[str, ...] = ()
    # 獲得報酬 review (wing_nav, 2026-10-10): what Maa did there (read / protected / rerolled) and its limits.
    reward_review: Mapping[str, Any] | None = None
    unknowns: tuple[str, ...] = ()

    @property
    def best_ex_tier(self) -> str | None:
        tiers = [EX_TIERS.index(e.tier) for e in self.ex_skills if e.tier in EX_TIERS]
        return EX_TIERS[max(tiers)] if tiers else None

    def to_dict(self) -> dict:
        out = dataclasses.asdict(self)
        out["stats"], out["stat_caps"] = dict(self.stats), dict(self.stat_caps)
        out["communication_level"] = list(self.communication_level) if self.communication_level else None
        for key in ("live_skills", "rewards", "screens_seen", "unknowns"):
            out[key] = list(out[key])
        out["ex_skills"] = [e.to_dict() for e in self.ex_skills]
        out["knowhow_books"] = [b.to_dict() for b in self.knowhow_books]
        out["best_ex_tier"] = self.best_ex_tier
        out["reward_review"] = dict(self.reward_review) if self.reward_review else None
        return out


def _stats(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}
    out = {}
    for key, raw in value.items():
        k = str(key).lower()
        v = parse_int(raw)
        if k in STATS and v is not None and v >= 0:
            out[k] = v
    return out


def _texts(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(v).strip() for v in values if isinstance(v, str) and v.strip())


def _same_entry(a: Any, b: Any) -> bool:
    """Same Ex skill / knowhow book read on two frames (tier and count may differ)."""
    if isinstance(a, ExSkill) and isinstance(b, ExSkill):
        return (a.name, a.idol, a.target) == (b.name, b.idol, b.target)
    if isinstance(a, KnowhowBook) and isinstance(b, KnowhowBook):
        return (a.name, a.level) == (b.name, b.level)
    return a == b


class EndCollector:
    """Accumulates end-flow readings in screen order. ``observe`` never raises on bad input; a later value for the
    same field replaces an unknown one but never an already-read one (first read wins, mismatches are noted)."""

    def __init__(self, scenario: str = "wing", wing_won: bool | None = None, true_end_seen: bool = False):
        self.scenario = scenario
        self.wing_won = wing_won
        self.true_end_seen = bool(true_end_seen)
        self.values: dict[str, Any] = {}
        self.screens: list[str] = []
        self.conflicts: list[str] = []
        self.finished = False

    # Fields that animate on screen (third demo #642 -> #643: コミュニケーションレベル 3/35 -> 4/35 LEVEL UP): the
    # latest read wins instead of being a conflict.
    LATEST_WINS = ("trust_level", "communication_level")
    # Lists read from a scrolling page (獲得報酬 Exスキル): entries are merged over frames, duplicates dropped.
    MERGED = ("ex_skills", "knowhow_books")

    def _put(self, key: str, value: Any) -> None:
        if value is None or value == () or value == {}:
            return
        if key in self.LATEST_WINS:
            self.values[key] = value
            return
        if key in self.MERGED:
            merged = list(self.values.get(key, ()))
            for item in value:
                same = next((i for i, old in enumerate(merged) if _same_entry(old, item)), None)
                if same is None:
                    merged.append(item)
                elif getattr(merged[same], "tier", "x") is None and getattr(item, "tier", None) is not None:
                    merged[same] = item          # a later frame read the tier the earlier one could not
            self.values[key] = tuple(merged)
            return
        if key in self.values and self.values[key] != value:
            self.conflicts.append(key)
            return
        self.values.setdefault(key, value)

    def record_reward(self, review: Mapping[str, Any]) -> None:
        """Result of wing_nav's 獲得報酬 review: whole books (replace the per-line readings of the selected book), the
        Ex skills read on the Exスキル tab (merged), and the review record (protected / rerolls / partial / notes)."""
        books = tuple(b for b in (KnowhowBook.from_reading({**v, "index": int(i)})
                                  for i, v in sorted((review.get("books") or {}).items(), key=lambda kv: int(kv[0])))
                      if b)
        if books:
            self.values["knowhow_books"] = books
        skills = tuple(s for s in (ExSkill.from_reading(v) for v in review.get("ex") or ()) if s)
        self._put("ex_skills", skills)
        self.values["reward_review"] = {k: review.get(k) for k in ("protected", "rerolls", "ex_partial", "notes",
                                                                   "items", "complete", "missing")}

    def observe(self, screen: str, readings: Mapping[str, Any] | None = None) -> None:
        r = dict(readings or {})
        if screen not in END_SCREENS:
            return
        if not self.screens or self.screens[-1] != screen:
            self.screens.append(screen)
        if screen == "Evaluation":
            # 社長からの評価 also follows an abandoned Produce (諦める, third demo #602 -> #616): no win is inferred.
            self._put("final_fans", parse_int(r.get("final_fans")))
            self._put("affection", parse_int(r.get("affection")))
        elif screen == "FesBirth":
            self._put("stats", _stats(r.get("stats")))
            self._put("stat_caps", _stats(r.get("stat_caps")))
            self._put("sp_at_birth", parse_int(r.get("sp")))
            self._put("final_fans", parse_int(r.get("fans")))
            self._put("live_skills", _texts(r.get("live_skills")))
        elif screen == "FesBirthConfirm":
            self._put("sp_lost", parse_int(r.get("sp_lost")))
        elif screen == "TotalEval":
            self.values["fes_idol_created"] = True
            self._put("rank", parse_rank(r.get("rank")))
        elif screen == "ClearReward":
            self.values["fes_idol_created"] = True
            self._put("trust_level", parse_int(r.get("trust_level")))
            self._put("communication_level", parse_pair(r.get("communication_level")))
            self._put("rewards", _texts(r.get("rewards")))
        elif screen == "Reward":
            skills = tuple(s for s in (ExSkill.from_reading(v) for v in r.get("ex_skills") or ()) if s)
            books = tuple(b for b in (KnowhowBook.from_reading(v) for v in r.get("knowhow_books") or ()) if b)
            self._put("ex_skills", skills)
            if "reward_review" not in self.values:      # whole books from the review win over line readings
                self._put("knowhow_books", books)
        elif screen == "FesDetail":
            self._put("total_fans", parse_int(r.get("total_fans")))
            self._put("live_skills", _texts(r.get("live_skills")))
        elif screen == "TrueEndCommu":
            self.true_end_seen = True
        elif screen == "MissionPopup":
            if re.search(r"(?i)true\s*end", str(r.get("mission_text") or "")):
                self.true_end_seen = True
            self.finished = True

    def summary(self) -> ProduceEndSummary:
        v = self.values
        sp_lost = v.get("sp_lost", v.get("sp_at_birth"))
        true_end = True if self.true_end_seen else (False if self.finished else None)
        fields = {
            "rank": v.get("rank"), "final_fans": v.get("final_fans"), "affection": v.get("affection"),
            "stats": v.get("stats", {}), "stat_caps": v.get("stat_caps", {}), "sp_lost": sp_lost,
            "live_skills": v.get("live_skills", ()), "ex_skills": v.get("ex_skills", ()),
            "knowhow_books": v.get("knowhow_books", ()), "rewards": v.get("rewards", ()),
            "trust_level": v.get("trust_level"), "communication_level": v.get("communication_level"),
        }
        unknowns = [k for k in ("rank", "final_fans", "stats", "sp_lost", "ex_skills", "knowhow_books", "trust_level")
                    if fields[k] in (None, {}, ())]
        if fields["ex_skills"] and any(e.tier is None for e in fields["ex_skills"]):
            unknowns.append("ex_tier")
        # A missing TE commu is not an error: the game skips the TE scene for a P card that already
        # reached True End, and the TE commu plays after 獲得報酬 where Maa hands over. ``true_end`` stays None (no
        # evidence) and is reported as info, not as an unread field.
        unknowns += [f"conflict:{c}" for c in dict.fromkeys(self.conflicts)]
        return ProduceEndSummary(scenario=self.scenario, wing_won=self.wing_won, true_end=true_end,
                                 fes_idol_created=v.get("fes_idol_created"), screens_seen=tuple(self.screens),
                                 unknowns=tuple(unknowns), reward_review=v.get("reward_review"), **fields)


def build_summary(observations: Iterable[tuple[str, Mapping[str, Any]]], *, scenario: str = "wing",
                  wing_won: bool | None = None, true_end_seen: bool = False) -> ProduceEndSummary:
    collector = EndCollector(scenario, wing_won, true_end_seen)
    for screen, readings in observations:
        collector.observe(screen, readings)
    return collector.summary()


# ---- recommendation --------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class EndRules:
    """User rules for the keep/release *suggestion*. The default never suggests a release (``release_below_rank``
    None): with no rule configured every Fes idol is reported as keep."""

    keep_if_ex_tier_at_least: str | None = "gold"     # keep when an Ex skill of this tier or better was obtained
    keep_if_rank_at_least: str | None = "B"           # keep when 総合評価 >= this rank
    keep_if_true_end: bool = True
    release_below_rank: str | None = None              # suggest a release only when rank < this (opt-in)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "EndRules":
        value = dict(value or {})
        unknown = set(value) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"unknown produce-end rule keys: {sorted(unknown)}")
        tier = value.get("keep_if_ex_tier_at_least", cls.keep_if_ex_tier_at_least)
        if tier is not None and tier not in EX_TIERS:
            raise ValueError(f"keep_if_ex_tier_at_least must be one of {EX_TIERS} or null")
        for key in ("keep_if_rank_at_least", "release_below_rank"):
            rank = value.get(key, getattr(cls, key))
            if rank is not None and rank not in RANKS:
                raise ValueError(f"{key} must be one of {RANKS} or null")
        keep_te = value.get("keep_if_true_end", True)
        if not isinstance(keep_te, bool):
            raise ValueError("keep_if_true_end must be a boolean")
        return cls(tier, value.get("keep_if_rank_at_least", cls.keep_if_rank_at_least), keep_te,
                   value.get("release_below_rank"))


def recommend(summary: ProduceEndSummary, rules: EndRules | Mapping[str, Any] | None = None) -> dict:
    """Keep / release *suggestion* for the new Fes idol. Always user-confirmed; never an action.

    ``suggestion`` is ``keep`` (a keep rule matched or no release rule applies), ``release_candidate`` (only when the
    user opted into ``release_below_rank`` and the rank is known and below it, with no keep rule matching) or
    ``undecided`` (a release rule is set but the rank is unread). ``default_choice`` stays ``keep`` in every case."""
    rules = rules if isinstance(rules, EndRules) else EndRules.from_mapping(rules)
    reasons: list[str] = []
    best = summary.best_ex_tier
    if rules.keep_if_ex_tier_at_least and best and EX_TIERS.index(best) >= EX_TIERS.index(rules.keep_if_ex_tier_at_least):
        reasons.append(f"ex_tier_{best}")
    rank_i = rank_index(summary.rank)
    if rules.keep_if_rank_at_least and rank_i is not None and rank_i >= RANKS.index(rules.keep_if_rank_at_least):
        reasons.append(f"rank_{summary.rank}")
    if rules.keep_if_true_end and summary.true_end is True:
        reasons.append("true_end")
    if reasons:
        suggestion = "keep"
    elif rules.release_below_rank is None:
        suggestion, reasons = "keep", ["no_release_rule"]
    elif rank_i is None:
        suggestion, reasons = "undecided", ["rank_unread"]
    elif rank_i < RANKS.index(rules.release_below_rank):
        suggestion, reasons = "release_candidate", [f"rank_{summary.rank}_below_{rules.release_below_rank}"]
    else:
        suggestion, reasons = "keep", [f"rank_{summary.rank}_not_below_{rules.release_below_rank}"]
    # Ex skills and knowhow books are separate items (推測): releasing the idol is not needed to
    # keep them, and keeping the idol is never needed to keep them. The book's own 売却 is a separate user decision.
    return {
        "suggestion": suggestion,
        "reasons": reasons,
        "default_choice": "keep",
        "choices": ["keep", "later_in_game"],
        "requires_user_confirmation": True,
        "auto_action": None,
        "knowhow_book": {"suggestion": "keep", "auto_action": None,
                         "note": "ノウハウブック 売却/保護 只由用户在游戏内决定"},
    }


# ---- notifications ---------------------------------------------------------------------------------------------
NOTIFY_PREFS = {"ex_rainbow": "produce_end_ex_rainbow", "ex_gold": "produce_end_ex_gold"}


def notifications(summary: ProduceEndSummary, prefs: Mapping[str, Any] | None = None,
                  recommendation: Mapping[str, Any] | None = None) -> list[dict]:
    """Reminders for the UI. ``prefs`` is the profile ``prefs`` (``notify.<key> is False`` turns a reminder off; absent
    = on, same as the panel's other notifications). Safety reminders (destructive dialog, unread fields) are not
    switchable."""
    notify = (prefs or {}).get("notify") if isinstance((prefs or {}).get("notify"), Mapping) else {}
    on = lambda key: notify.get(NOTIFY_PREFS.get(key, key)) is not False  # noqa: E731
    out: list[dict] = []
    for skill in summary.ex_skills:
        if skill.tier == "rainbow" and on("ex_rainbow"):
            out.append({"key": "ex_rainbow", "tone": "accent", "message": f"获得虹色 EX 技能：{skill.name or '名称未读'}"})
        elif skill.tier == "gold" and on("ex_gold"):
            out.append({"key": "ex_gold", "tone": "success", "message": f"获得金色 EX 技能：{skill.name or '名称未读'}"})
    if any(s.tier is None for s in summary.ex_skills):
        out.append({"key": "ex_tier_unread", "tone": "warning", "message": "有 EX 技能稀有度未读，请在游戏内确认"})
    if summary.knowhow_books:
        count = sum(b.count for b in summary.knowhow_books)
        levels = [b.level for b in summary.knowhow_books if b.level is not None]
        lv = f"（Lv.{max(levels)}）" if levels else ""
        out.append({"key": "knowhow_book", "tone": "info", "message": f"获得ノウハウブック {count} 冊{lv}；売却/保護 请在游戏内自行决定"})
    if summary.true_end is True:
        out.append({"key": "true_end", "tone": "success", "message": "已看到 True End"})
    elif summary.scenario == "wing":
        # Not a warning: the game skips the TE scene for a P card that already reached it, and it plays after
        # 獲得報酬 (where Maa normally hands over).
        out.append({"key": "true_end_unseen", "tone": "info",
                    "message": "未看到 True End 剧情（已达成过 TE 的 P 卡会被游戏自动跳过；TE 剧情在獲得報酬之后）"})
    review = summary.reward_review or {}
    for book in summary.knowhow_books:
        if book.protected and book.level is not None and book.level >= 5:
            out.append({"key": "knowhow_lv5_protected", "tone": "success",
                        "message": f"Lv{book.level} ノウハウブック 已保護：{book.name or '名称未读'}"})
    if review.get("rerolls"):
        out.append({"key": "reward_rerolled", "tone": "info", "message": f"已再抽選 {review['rerolls']} 次（体力各 30）"})
    if review.get("ex_partial"):
        out.append({"key": "ex_partial", "tone": "warning",
                    "message": "Exスキル 列表需滚动，只读到可见部分，请在游戏内查看其余"})
    if summary.sp_lost:
        out.append({"key": "sp_lost", "tone": "info", "message": f"誕生时剩余 SP {summary.sp_lost} 已失去"})
    if recommendation and recommendation.get("suggestion") == "release_candidate":
        out.append({"key": "release_candidate", "tone": "warning",
                    "message": "按你的规则这张フェスアイドル可考虑在游戏内移籍；Maa 不会执行，默认保留"})
    shown = [u for u in summary.unknowns if u not in ("ex_tier",)]
    if shown:
        out.append({"key": "unknowns", "tone": "warning", "message": "未读字段：" + "、".join(shown)})
    return out


# ---- navigation contract ---------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class EndGateConfig:
    pause_at_reward: bool = True       # hand over on 獲得報酬 so the user can 保護/売却 in game (default: yes)
    max_sp_lost: int | None = None     # stop before 誕生 when more SP would be lost (None = only report it)


def destructive_markers(texts: Iterable[Any]) -> list[str]:
    """Kinds of DESTRUCTIVE_MARKERS found in OCR texts (``"text"`` or ``(text, box)`` items)."""
    joined = "".join(_clean(t[0] if isinstance(t, (list, tuple)) else t) for t in texts or ())
    return [kind for kind, words in DESTRUCTIVE_MARKERS.items() if any(w in joined for w in words)]


def end_screen_gate(screen: str | None, texts: Iterable[Any] = (), *, dialog: bool = False,
                    readings: Mapping[str, Any] | None = None, config: EndGateConfig | None = None) -> dict:
    """What the navigation may do on one end-flow frame.

    Returns ``{"action": "continue" | "handover" | "hard_stop" | "finish", "reason": ...}``. ``continue`` only allows
    the screen's existing non-destructive intent (次へ / OK / 誕生 / 誕生 confirm); it never names a new target.
    ``hard_stop``: a modal with 売却/移籍/削除/再抽選/上限/ジュエル text, or an unknown modal in the end flow — the user
    acts, Maa does not retry or cancel on its own. ``handover``: stop and show the summary (the run is not failed)."""
    config = config or EndGateConfig()
    texts = list(texts or ())
    markers = destructive_markers(texts)
    if screen == "Reward" and not dialog:
        # The page itself carries 保護/売却/再抽選 buttons: a text that is exactly one of those labels is expected here.
        loose = [c for c in (_clean(t[0] if isinstance(t, (list, tuple)) else t) for t in texts)
                 if c not in REWARD_PAGE_BUTTONS and not REWARD_PAGE_TEXT.search(c)]
        markers = destructive_markers(loose)
    if markers:
        return {"action": "hard_stop", "reason": "destructive_dialog", "markers": markers}
    if dialog and screen not in ("FesBirthConfirm",):
        return {"action": "hard_stop", "reason": "unknown_end_dialog", "screen": screen}
    if screen not in END_SCREENS:
        return {"action": "hard_stop", "reason": "not_an_end_screen", "screen": screen}
    if screen == "FesBirthConfirm" and config.max_sp_lost is not None:
        lost = parse_int((readings or {}).get("sp_lost"))
        if lost is None:
            return {"action": "handover", "reason": "sp_lost_unread"}
        if lost > config.max_sp_lost:
            return {"action": "handover", "reason": "sp_would_be_lost", "sp_lost": lost}
    if screen == "Reward" and config.pause_at_reward:
        return {"action": "handover", "reason": "produce_end_review"}
    if screen == "MissionPopup":
        return {"action": "finish", "reason": "produce_finished_home"}
    if screen == "TrueEndCommu":
        return {"action": "continue", "reason": "wait_commu"}
    return {"action": "continue", "reason": "non_destructive_confirmation"}


def observe_end_screen(collector: EndCollector, screen: str, readings: Mapping[str, Any] | None = None) -> None:
    """wing_nav hook: feed one classified end-flow frame (call before ``end_screen_gate``)."""
    collector.observe(screen, readings)


def summary_payload(collector: EndCollector, rules: Mapping[str, Any] | EndRules | None = None,
                    prefs: Mapping[str, Any] | None = None, *, stop_reason: str | None = None) -> dict:
    """The JSON the UI receives (proposed protocol event ``produce.end_summary``)."""
    summary = collector.summary()
    rec = recommend(summary, rules)
    return {
        "version": SCHEMA_VERSION,
        "summary": summary.to_dict(),
        "recommendation": rec,
        "notifications": notifications(summary, prefs, rec),
        "stop_reason": stop_reason,
        "auto_delete": False,
    }


def payload_is_safe(payload: Mapping[str, Any]) -> bool:
    """Invariant check used by tests and the UI adapter: nothing in a payload can authorise a deletion."""
    rec = payload.get("recommendation") or {}
    return (payload.get("auto_delete") is False and rec.get("auto_action") is None
            and rec.get("default_choice") == "keep" and rec.get("requires_user_confirmation") is True
            and (rec.get("knowhow_book") or {}).get("auto_action") is None)


def demo_observations() -> list[tuple[str, dict]]:
    """Readings of the 2026-10-05 demo end flow (#959–#985; Reward/FesBirthConfirm were not captured)."""
    return [
        ("Evaluation", {"affection": 25}),
        ("FesBirth", {"stats": {"Vo": 1492, "Da": 400, "Vi": 339, "Me": 656},
                      "stat_caps": {"Vo": 1492, "Da": 545, "Vi": 595, "Me": 675}, "sp": 248, "fans": "529,253",
                      "live_skills": ["cheer+", "DIVEIN!", "DIVEIN!+"]}),
        ("TotalEval", {"rank": "総合評価 B+ RANK"}),
        ("ClearReward", {"trust_level": 7, "communication_level": "6/35"}),
        ("TrueEndCommu", {}),
        ("MissionPopup", {"mission_text": "【cheer+】有栖川夏葉のTrueEndを見よう"}),
    ]


__all__: Sequence[str] = (
    "EX_TIERS", "RANKS", "END_SCREENS", "NAV_HOOKS", "READING_FIELDS", "ExSkill", "KnowhowBook", "ProduceEndSummary",
    "EndCollector", "build_summary", "EndRules", "recommend", "notifications", "EndGateConfig", "destructive_markers",
    "end_screen_gate", "observe_end_screen", "summary_payload", "payload_is_safe", "demo_observations",
)
