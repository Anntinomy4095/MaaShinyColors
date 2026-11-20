"""Collabo Fes (コラボフェス): play one stage repeatedly with the dedicated Pass.

「我需要添加新任务collabo fes」; 「一般选择vo 经常遇到满员……期间遇到满员只能刷新等待」;
「不是20是6次」 (a batch is at most the 6-Pass cap); 「不用锁定lv1 往后打 后续maa要自行推关并锁定在尽量不会输的难度
最大化收益」; the battle 「移动到第二位随便出牌都能拿到高连锁奖励」 (drag the own panel to the 2nd place, any skill).

One decision per fresh frame (like ``match_play``): ``CollaboPlay.step`` reads the page and returns a ``Decision``; the
Pipeline node of that decision (``SC.Collabo.Do.<Kind>``: an ``And`` of its page guard and ``SC.Collabo.Want.<Kind>``)
taps or drags it. テンション決定 spends one コラボフェスパス and needs a frame-bound grant (``collabo-tension-decide``).
A run counts only once its LIVE REPORT is read; anything else after a spend is uncertain and ends the task.

Native pages (dev_assets/private/wing_samples/2026-11-18, 1280x720):

* home: the event banner (bottom right) rotates every ~3 s between events; › pages it. 「COLLABO FES」 in the banner.
* event: 「COLLABO FES」 logo, four stages with a 盛り上がり度 bar each (満員御礼 when full), 6 Pass pips, 「あと56:19」.
* difficulty: 「難易度とレベルを選択してください」; Easy / Normal / Hard; the tapped card shows −, 「Lv.1」, ＋ and OK.
* unit: 「コラボフェスユニット編成」, tabs ユニット1–10, 「ライブへ」.
* tension: 「参加ユニット」, 6 players; 「テンション決定」 (spends 1 Pass) turns into 「ライブ開始」.
* battle: 「TURN」; six panels on the right (own panel pink); three skills at the bottom. After a skill: 「ライブスキルと
  アピールの順番を決めてください」+「自分のパネルをつかんで移動」; tapping the own panel: 「もう1度タップして決定」.
* report: 「LIVE REPORT」, 「Hard Lv.1」, クリアボーナス, SCORE, OK.
"""
from __future__ import annotations

import copy
import datetime as dt
import dataclasses
import json
import re

from agent.core import task_common as tc

SPEND_ACTION = "collabo-tension-decide"
APPROVER = "SC.Collabo.Want.Tension"
STAGES = ("vocal", "dance", "visual", "center")
STAGE_WORDS = {"vocal": "Vocal", "dance": "Dance", "visual": "Visual", "center": "Center"}
DIFFICULTY = "hard"
LEVEL_MAX = 30
PASS_MAX = 6
COUNT_CHOICES = ("all", 1, 2, 3, 4, 5, 6)
UNIT_TABS = 10
LEVEL_LEDGER = tc.PRIVATE / "collabo" / "levels.json"

MAX_BANNER_TAPS = 12          # › taps looking for the COLLABO FES banner
MAX_WRONG_EVENTS = 5          # banner taps that opened another event
MAX_BANNER_PROBES = 6         # after a full › round without the text: open the shown banner and check the page
MAX_NAV_TAPS = 24             # non-consuming taps between two lives
MAX_LEVEL_TAPS = LEVEL_MAX + 2
MAX_WAIT_FRAMES = 10          # unread frames of a page before the task ends
# Taps that leave the current page. Until a different page is read, a frame of the same page is the old page still
# fading out (live 2026-11-19: a second OK on the fading difficulty dialog hit the unit page's Visual slot), so the
# run waits instead of deciding again.
LEAVING_TAPS = frozenset({"banner", "wrong_event_back", "event_back", "difficulty_back", "unit_back", "level_ok",
                          "unit_live", "report_ok", "full_close", "stage_vocal", "stage_dance", "stage_visual", "stage_center"})
# LIVE REPORT OK was ignored twice, 10 s apart, on a settled report with 更新!/NEW RECORD (live 2026-11-19); the same
# tap a minute later worked. The result is already recorded and OK spends nothing, so a report still shown gets a
# few more OKs, spaced out.
RETRY_TAPS = frozenset({"report_ok"})
PENDING_RETRY_FRAMES = 6
MAX_PENDING_RETRIES = 3
MAX_TURN_ACTIONS = 6          # taps/drags inside one TURN
MAX_DRAGS_PER_TURN = 2
MAX_LIVE_FRAMES = 400         # frames from ライブ開始 to LIVE REPORT
MAX_UNSENT_TAPS = 3
REPORT_CLEAR_FRAMES = 6       # extra report frames to read the クリアボーナス after SCORE
# 満員: leave, wait, re-enter the event page (the page only refreshes on entry). 2026-11-19 user: 5 min was long
# (Vocal reopened sooner) -> exponential: 1, 2, 4, then 5 min per consecutive full check; a played live resets it.
FULL_RECHECK_S = 60
FULL_RECHECK_MAX_S = 300
PASS_RECHECK_S = 600          # Pass 0 with an unread 「あと mm:ss」
PASS_MARGIN_S = 20
WAIT_END_MARGIN_S = 600       # a wait must end this long before the task's wait budget runs out
IDLE_TAP_ALLOWANCE = 4        # navigation taps granted per wait cycle (event ←, banner, › …)

# boxes (x, y, w, h) on the 1280x720 canvas
BANNER = (990, 425, 285, 140)                     # event banner text 「COLLABO FES」
EVENT_LOGO = (10, 0, 330, 140)                    # 「COLLABO FES」
EVENT_PASS_LABEL = (970, 615, 215, 40)            # 「コラボフェスパス」
EVENT_DEADLINE = (30, 142, 290, 38)               # 「開催期限11/2713:59まで」
# Another event's page (left with ←): its 開催期限 line sits lower (2026-11-19 復刻 page: y≈195), so a taller box.
OTHER_DEADLINE = (20, 140, 330, 80)
EVENT_RECOVERY = (1125, 652, 135, 36)             # 「あと56:19」
PASS_PIPS = tuple((x, 674) for x in (1036, 1048, 1060, 1073, 1085, 1097))
STAGE_FULL = {"vocal": (940, 330, 290, 90), "dance": (120, 280, 300, 90),
              "visual": (650, 160, 330, 90), "center": (455, 400, 330, 90)}   # 「満員御礼」 ribbon
STAGE_PERCENT = {"vocal": (1165, 428, 75, 40), "dance": (355, 395, 75, 40),
                 "visual": (918, 258, 75, 40), "center": (700, 527, 75, 40)}
DIFF_TITLE = (60, 30, 600, 58)                    # 「難易度とレベルを選択してください」
DIFF_STAGE = (910, 5, 315, 85)                    # 「VocalStage」
HARD_LEVEL = (945, 345, 140, 50)                  # 「Lv.1」 inside the opened Hard card
HARD_PLUS = (1108, 346, 60, 48)                   # 「+」: white when a higher level is open, grey at the highest
HARD_OK = (940, 440, 140, 110)                    # 「OK」
UNIT_TITLE = (40, 12, 320, 45)                    # 「コラボフェスユニット編成」
# 2026-11-19 live: the stage filled while the unit page was open; ライブへ showed 「Vocalステージの盛り上がりが
# 最高潮に達しました」 with 閉じる (no Pass used).
FULL_POPUP = (380, 280, 520, 80)
UNIT_NAME = (145, 148, 230, 45)                   # 「ユニット1」 of the shown unit
UNIT_SLOTS = tuple((40 + 247 * i, 280, 220, 240) for i in range(5))   # an empty slot's 「タッチして編成」-style label
TENSION_TITLE = (40, 12, 180, 45)                 # 「参加ユニット」
TENSION_BUTTON = (1092, 620, 170, 48)             # 「テンション決定」 / 「ライブ開始」
BATTLE_TURN = (0, 0, 180, 48)                     # 「TURN 1」
BATTLE_PROMPT = (430, 170, 460, 200)              # 「…決めてください」「自分のパネルを…」「もう1度タップして決定」
SKILL_BAR = (290, 580, 700, 60)                   # 「PLUS」「LINK」 labels over the three skills
# Role labels under the three skills (「Dance担当」「Leader」…). 2026-11-19 live Lv.5: no skill had PLUS/LINK, so the
# bar alone missed the skill choice and the live waited out MAX_LIVE_FRAMES; the labels are on every choice frame.
SKILL_ROLES = (300, 690, 700, 30)
PANEL_ROWS = tuple((1000, 92 + 81 * i, 270, 75) for i in range(6))
PANEL_SAMPLE = tuple((1150, 100 + 81 * i, 25, 60) for i in range(6))
REPORT_TITLE = (410, 5, 455, 80)                  # 「LIVE REPORT」
REPORT_LEVEL = (240, 250, 175, 45)                # 「Hard Lv.1」
REPORT_CLEAR = (392, 462, 173, 50)                # 「10,000pt」 クリアボーナス; from Lv.10 「Hard(10)」 reaches x≈389
REPORT_SCORE = (350, 525, 215, 65)                # 「10,000pt」 SCORE

CLICK_NODES = {"banner": "SC.Collabo.Do.Banner", "banner_next": "SC.Collabo.Do.BannerNext",
               "wrong_event_back": "SC.Collabo.Do.WrongEventBack",
               **{f"stage_{s}": f"SC.Collabo.Do.Stage{s.capitalize()}" for s in STAGES},
               "hard": "SC.Collabo.Do.Hard", "level_plus": "SC.Collabo.Do.LevelPlus",
               "level_minus": "SC.Collabo.Do.LevelMinus", "level_ok": "SC.Collabo.Do.LevelOk",
               **{f"unit_tab_{n}": f"SC.Collabo.Do.UnitTab{n}" for n in range(1, UNIT_TABS + 1)},
               "unit_live": "SC.Collabo.Do.UnitLive", "tension": "SC.Collabo.Do.Tension",
               "live_start": "SC.Collabo.Do.LiveStart", "skill": "SC.Collabo.Do.Skill",
               "drag_second": "SC.Collabo.Do.DragSecond", "panel_confirm": "SC.Collabo.Do.PanelConfirm",
               "report_ok": "SC.Collabo.Do.ReportOk", "event_back": "SC.Collabo.Do.EventBack",
               "difficulty_back": "SC.Collabo.Do.DifficultyBack", "unit_back": "SC.Collabo.Do.UnitBack",
               "full_close": "SC.Collabo.Do.FullClose"}


def want_node(kind):
    """"level_plus" -> SC.Collabo.Want.LevelPlus."""
    return "SC.Collabo.Want." + "".join(part.capitalize() for part in kind.split("_"))


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str            # a CLICK_NODES key, "wait" or "none"
    reason: str = ""


# ---------------------------------------------------------------- config
@dataclasses.dataclass(frozen=True)
class CollaboConfig:
    stage: str = "vocal"
    count: object = "all"          # "all" = every Pass on screen at the start (≤ 6), or 1–6
    unit: int = 1
    level: str = "auto"            # "auto" = push the level, lock below the first loss; or "1".."30" fixed
    backup: str = ""               # stage played while ``stage`` shows 満員御礼 ("" = wait for it)

    @classmethod
    def from_params(cls, p):
        p = dict(p or {})
        stage = p.get("stage", "vocal")
        count = p.get("count", "all")
        unit = p.get("unit", 1)
        level = str(p.get("level", "auto"))
        backup = p.get("backup", "")
        tc.require(stage in STAGES, "Collabo 会场无效")
        tc.require(count in COUNT_CHOICES, "Collabo 局数无效")
        tc.require(type(unit) is int and 1 <= unit <= UNIT_TABS, "Collabo 编队无效")
        tc.require(level == "auto" or (level.isdigit() and 1 <= int(level) <= LEVEL_MAX), "Collabo 等级无效")
        tc.require(backup == "" or backup in STAGES, "Collabo 满员时会场无效")
        return cls(stage, count, unit, level, "" if backup == stage else backup)

    def to_dict(self):
        return dataclasses.asdict(self)


# ---------------------------------------------------------------- parsing
def parse_level(text):
    match = re.fullmatch(r"(?:Hard)?Lv\.?([0-9]{1,2})", (text or "").replace(" ", ""))
    return int(match[1]) if match and 1 <= int(match[1]) <= LEVEL_MAX else None


def parse_points(text):
    """'>10,000pt' / '10,000pt' / '0pt' -> int. 2026-11-22 live (Lv.24 loss): OCR reads a lone 「0pt」 as 「Opt」 (letter O)
    in SCORE and クリアボーナス; the report then stayed unread until live_too_long. A lone O/o before pt is 0."""
    text = (text or "").replace(" ", "")
    match = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)pt$", text)
    if match:
        return int(match[1].replace(",", ""))
    return 0 if re.search(r"(?:^|[^0-9A-Za-z,])[Oo]pt$", text) else None


def clear_bonus(level):
    """Hard Lv.X クリアボーナス = 9,500 + 500 × X(X+1)/2 (live Lv.1–11, any stage)."""
    return 9500 + 250 * level * (level + 1)


def check_clear(level, clear):
    """A read clear bonus that is not 0 and not Lv.X's bonus (a stray glyph in front, a misread) counts as unread."""
    if clear is None or not level or clear == 0:
        return clear
    expected = clear_bonus(level)
    return expected if clear == expected or str(clear).endswith(str(expected)) else None


def parse_unit(text):
    match = re.fullmatch(r"ユニット([0-9]{1,2})", text or "")
    return int(match[1]) if match else None


def parse_deadline(text):
    match = re.search(r"([0-9]{1,2}/[0-9]{1,2})", text or "")
    return match[1] if match else None


def parse_turn(text):
    match = re.fullmatch(r"TURN([0-9]{1,2})", (text or "").replace(" ", ""))
    return int(match[1]) if match else None


def parse_recovery(text):
    """'あと56:19' -> seconds; None when unread."""
    match = re.search(r"あと([0-9]{1,2}):([0-9]{2})", (text or "").replace(" ", ""))
    return int(match[1]) * 60 + int(match[2]) if match else None


def pass_count(image):
    """Lit Pass pips on the event page (mint, BGR ~ (230, 255, 92)); None when a pip is neither lit nor dark."""
    count = 0
    for x, y in PASS_PIPS:
        b, g, r = (int(v) for v in image[y, x][:3])
        if g >= 220 and b >= 180 and r <= 150:
            count += 1
        elif not (g <= 190 and b >= 140 and r <= 80):
            return None
    return count


def own_row(image):
    """Index (0–5) of the own (pink) panel; None unless exactly one row is pink."""
    rows = []
    for i, (x, y, w, h) in enumerate(PANEL_SAMPLE):
        patch = image[y:y + h, x:x + w, :3].reshape(-1, 3).astype(int).mean(axis=0)
        b, g, r = patch
        if r - g >= 40 and r >= 150:          # the prompt overlay dims the pink to ~(200,166,215) BGR
            rows.append(i)
    return rows[0] if len(rows) == 1 else None


# ---------------------------------------------------------------- level ledger
class LevelLedger:
    """Per event (deadline text) and stage: highest cleared and lowest failed level. Local, never committed."""

    def __init__(self, path=LEVEL_LEDGER):
        self.path = path

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, event, stage):
        entry = self._load().get(f"{event}|{stage}") or {}
        return int(entry.get("cleared") or 0), entry.get("failed")

    def record(self, event, stage, level, cleared):
        data = self._load()
        key = f"{event}|{stage}"
        entry = dict(data.get(key) or {})
        if cleared:
            entry["cleared"] = max(int(entry.get("cleared") or 0), level)
        else:
            failed = entry.get("failed")
            entry["failed"] = level if failed is None else min(int(failed), level)
        self._write(data, key, entry)

    def _write(self, data, key, entry):
        data[key] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def target_level(config, cleared, failed):
    """Fixed level, or auto: the highest level the game has open (read from 「+」 on the Hard card), never at or above
    the lowest loss. For auto this is the upper bound; the difficulty page settles the actual level."""
    if config.level != "auto":
        return int(config.level)
    level = LEVEL_MAX if failed is None else min(LEVEL_MAX, int(failed) - 1)
    return level if level >= 1 else None


def plus_lit(image):
    """「+」 on the Hard card: True = white (a higher level is open), False = grey (highest open), None = unclear.
    2026-11-20 live Vocal: Lv.11 white (61% of pixels > 230), Lv.12 grey (0%); 11-18 Lv.1 grey."""
    if image is None:
        return None
    x, y, w, h = HARD_PLUS
    bright = float((image[y:y + h, x:x + w].mean(axis=2) > 230).mean())
    return True if bright > 0.3 else False if bright < 0.05 else None


# ---------------------------------------------------------------- reading
class Reader:
    """Fixed-box OCR and pixel reads through the task Vision (``task_custom.Vision``)."""

    def __init__(self, vision):
        self.vision = vision

    def text(self, image, box, threshold=0.8):
        """Lines scoring >= ``threshold`` inside ``box``, in reading order; low-score lines are dropped, not fatal
        (the banner art and the battle stage put stray glyphs into most boxes)."""
        try:
            context = getattr(self.vision, "context", None)
            if context is None:
                return self.vision.text(image, box, threshold) or ""
            from maa.pipeline import JOCR, JRecognitionType
            detail = context.run_recognition_direct(JRecognitionType.OCR, JOCR(expected=[".+"], roi=tuple(box),
                                                                                  threshold=threshold), image)
            rows = [r for r in (getattr(detail, "all_results", None) or []) if detail is not None
                    and isinstance(r.text, str) and r.text.strip() and float(r.score) >= threshold]
            rows.sort(key=lambda r: (round((r.box[1] + r.box[3] / 2) / 12), r.box[0]))
            return tc.normalize_text("".join(r.text for r in rows)) or ""
        except Exception:
            return ""

    def page(self, image):
        if "LIVEREPORT" in self.text(image, REPORT_TITLE):
            return "report"
        if self.text(image, BATTLE_TURN).startswith("TURN"):
            return "battle"
        if "参加ユニット" in self.text(image, TENSION_TITLE):
            return "tension"
        if "最高潮" in self.text(image, FULL_POPUP):
            return "stage_full"
        if "ユニット編成" in self.text(image, UNIT_TITLE):
            return "unit"
        if "難易度とレベル" in self.text(image, DIFF_TITLE):
            return "difficulty"
        # The stylised 「COLLABO FES」 logo does not OCR; the Pass label is only on this event page.
        if "コラボフェスパス" in self.text(image, EVENT_PASS_LABEL):
            return "event"
        # Not while the Collabo page itself is still loading (its 開催期限 sits higher, before the Pass label shows).
        if "開催期限" in self.text(image, OTHER_DEADLINE) and "開催期限" not in self.text(image, EVENT_DEADLINE):
            return "other_event"
        if self.vision.hit("SC.Collabo.Home.Guard", image):
            return "home"
        return "unknown"

    def event(self, image, stages):
        """``full``: {stage: 満員御礼 shown} for each of ``stages`` (the target, then the backup if any)."""
        return {"pass": pass_count(image), "deadline": parse_deadline(self.text(image, EVENT_DEADLINE)),
                "full": {stage: "満員" in self.text(image, STAGE_FULL[stage]) for stage in stages},
                "percent": self.text(image, STAGE_PERCENT[stages[0]]),
                "recovery": self.text(image, EVENT_RECOVERY)}

    def banner(self, image):
        # 2026-11-18 frames: 「COLLABOFE」 at a low OCR score, with the banner's own 「PASS +0」 gauge.
        text = self.text(image, BANNER, threshold=0.4)
        return "COLLABO" in text or "PASS+" in text

    def difficulty(self, image):
        return {"stage": self.text(image, DIFF_STAGE), "ok": "OK" in self.text(image, HARD_OK),
                "level": parse_level(self.text(image, HARD_LEVEL)), "plus": plus_lit(image)}

    def unit(self, image):
        empty = sum(bool(re.search(r"タッチ|編成|未設定", self.text(image, box, 0.3))) for box in UNIT_SLOTS)
        return {"shown": parse_unit(self.text(image, UNIT_NAME)), "empty": empty}

    def tension(self, image):
        button = self.text(image, TENSION_BUTTON)
        return "decide" if "テンション決定" in button else "start" if "ライブ開始" in button else None

    def battle(self, image):
        prompt = self.text(image, BATTLE_PROMPT)
        return {"turn": parse_turn(self.text(image, BATTLE_TURN)), "prompt": prompt,
                "skills": bool(re.search(r"PLUS|LINK", self.text(image, SKILL_BAR))
                               or re.search(r"担当|Leader", self.text(image, SKILL_ROLES))), "own": own_row(image)}

    def report(self, image):
        return {"level": parse_level(self.text(image, REPORT_LEVEL)), "clear": parse_points(self.text(image, REPORT_CLEAR)),
                "score": parse_points(self.text(image, REPORT_SCORE))}


# ---------------------------------------------------------------- the run
class CollaboPlay:
    """Navigation, one live per Pass, level ledger. ``phase``: navigate → live → (navigate …) → leaving → ended."""

    def __init__(self, config, log, authority, now, ledger=None, max_wait_s=0):
        self.config, self.log, self.authority, self.now = config, log, authority, now
        self.ledger = ledger or LevelLedger()
        self.max_wait_s = max_wait_s     # waits (満員 / Pass) must end within this many seconds of the start
        self.started_at = now().timestamp()
        self.wait = None                 # {"reason", "until"} while waiting on home
        self.waits = 0
        self.phase = "navigate"
        self.outcome = None
        self.reason = ""
        self.lives = []
        self.budget = None           # lives allowed this run (min(count, Pass on the first event read))
        self.event = None            # deadline text: the ledger key of this event
        self.level = None            # target level of the next live
        self.plus_from, self.plus_stuck = None, 0   # level read before the last 「+」, and how often it stayed
        self.counters = {"banner": 0, "probe": 0, "wrong": 0, "nav": 0, "level": 0, "wait": 0, "unsent": 0}
        self.live = None
        self.last_page = None
        self.drag_row = None         # own panel row (0-5) of the current drag decision (the Swipe start box)
        self.pending = None          # {"kind", "page"} of the last page-leaving tap until another page is read
        self.stage_full = False      # the 満員 popup was closed: go back to the event page and wait there
        self.full_seen = []          # stages whose 満員 popup was seen since the last event-page read
        self.full_streak = 0         # consecutive 満員 waits since the last live (exponential recheck)
        self.stage = config.stage    # the stage of the current / next live (the backup while the target is full)

    # -- bookkeeping
    def snapshot(self):
        return copy.deepcopy({k: v for k, v in vars(self).items()
                              if k not in ("log", "authority", "now", "ledger", "max_wait_s", "started_at")})

    def restore(self, state):
        for key, value in state.items():
            setattr(self, key, value)

    def _log(self, record):
        try:
            self.log.append({"task": "collabo", **record})
            return True
        except Exception:
            return False

    def summary(self):
        return {"phase": self.phase, "outcome": self.outcome, "reason": self.reason, "lives": list(self.lives),
                "budget": self.budget, "event": self.event, "level": self.level, "waits": self.waits}

    def status_text(self):
        """The 运行中 view's sticky line while waiting (None otherwise): what it waits for and when it looks again."""
        if self.wait is None or self.phase in ("leaving", "ended"):
            return None
        try:
            at = dt.datetime.fromtimestamp(self.wait["until"]).strftime("%H:%M")
        except (OverflowError, OSError, ValueError, TypeError):
            return None
        stages = "、".join(STAGE_WORDS.get(x, x) for x in (self.config.stage, self.config.backup) if x)
        what = (f"{stages} 满员，等重开" if self.wait["reason"] == "full"
                else "Pass 不足，等恢复")
        done = f"已打 {len(self.lives)}/{self.budget} 局" if self.budget else f"已打 {len(self.lives)} 局"
        return f"Collabo Fes · {what} · {at} 再看 · {done}"

    def _end(self, reason):
        """Stop playing; leave the event page for home (no outcome = the task still succeeds)."""
        if self.phase not in ("leaving", "ended"):
            self.reason = reason
            self.phase = "leaving"
            self._log({"event": "collabo_end", "reason": reason, "lives": len(self.lives)})

    def _fail(self, outcome, reason):
        self.outcome, self.reason, self.phase = outcome, reason, "ended"
        self._log({"event": "collabo_fail", "outcome": outcome, "reason": reason})

    def _tap(self, kind, reason=""):
        if kind not in ("banner_next", "level_plus", "level_minus", "drag_second", "panel_confirm", "skill"):
            self.counters["nav"] += 1
        if self.counters["nav"] > MAX_NAV_TAPS * (len(self.lives) + 1) + IDLE_TAP_ALLOWANCE * self.waits:
            self._fail("collabo_navigation_exhausted", kind)
            return Decision("none", "navigation_exhausted")
        self.counters["wait"] = 0
        self.pending = {"kind": kind, "page": self.last_page} if kind in LEAVING_TAPS else None
        return Decision(kind, reason)

    def _wait(self, reason):
        self.counters["wait"] += 1
        if self.counters["wait"] > MAX_WAIT_FRAMES:
            if self.phase == "live":
                self._fail("spend_uncertain", "live_unreadable:" + reason)
            else:
                self._fail("collabo_page_unread", reason)
            return Decision("none", reason)
        return Decision("wait", reason)

    # -- one frame
    def step(self, reader, image):
        if self.phase == "ended":
            return Decision("none", self.reason)
        page = reader.page(image)
        if self.pending is not None:
            if page == self.pending["page"]:
                kind = self.pending["kind"]
                retries = self.pending.get("retries", 0)
                if (kind in RETRY_TAPS and retries < MAX_PENDING_RETRIES
                        and self.counters["wait"] >= PENDING_RETRY_FRAMES):
                    decision = self._tap(kind, "retry_" + kind)
                    self.pending["retries"] = retries + 1
                    return decision
                return self._wait("pending_" + kind)
            if page != "unknown":
                self.pending = None
        self.last_page = page
        if self.phase == "live":
            return self._live(reader, image, page)
        if self.phase == "leaving":
            return self._leave(page)
        if self.wait is not None:
            decision = self._waiting(page)
            if decision is not None:
                return decision
        handler = {"home": self._home, "other_event": self._other_event, "event": self._event,
                   "difficulty": self._difficulty, "unit": self._unit, "tension": self._tension,
                   "report": self._report_after_spend_gap, "stage_full": self._stage_full,
                   "battle": self._adopt_live}.get(page)
        return handler(reader, image) if handler else self._wait("page_" + page)

    def _start_wait(self, reason, seconds):
        """Leave the event page and wait on home; the page only shows a new 満員 / Pass state when entered again."""
        now = self.now().timestamp()
        if now + seconds > self.started_at + self.max_wait_s - WAIT_END_MARGIN_S:
            self._end("wait_time_up:" + reason)
            return self._leave("event")
        self.wait = {"reason": reason, "until": now + seconds}
        self.waits += 1
        self._log({"event": "collabo_wait", "reason": reason, "seconds": int(seconds), "lives": len(self.lives)})
        return self._tap("event_back", "wait_" + reason)

    def _waiting(self, page):
        """While waiting: idle on home (or an unread page), leave the event page; afterwards enter it again."""
        if self.now().timestamp() < self.wait["until"]:
            if page == "event":
                return self._tap("event_back", "wait_leave")
            if page in ("home", "unknown"):
                self.counters["wait"] = 0
                return Decision("idle", "wait_" + self.wait["reason"])
            return None
        self._log({"event": "collabo_wait_over", "reason": self.wait["reason"]})
        self.wait = None
        self.counters.update(banner=0, probe=0, wrong=0)
        return None

    def _home(self, reader, image):
        if reader.banner(image):
            return self._tap("banner", "collabo_banner")
        self.counters["banner"] += 1
        if self.counters["banner"] > MAX_BANNER_TAPS:
            # A new edition's banner art may not OCR: open the shown banner; the event page itself (コラボフェスパス)
            # decides, and another event's page is left with ← (_other_event).
            self.counters["probe"] += 1
            if self.counters["probe"] > MAX_BANNER_PROBES:
                self._fail("collabo_banner_not_found", "banner_pages_exhausted")
                return Decision("none", "banner_not_found")
            return self._tap("banner", "banner_probe")
        return Decision("banner_next", "banner_page")

    def _other_event(self, reader, image):
        self.counters["wrong"] += 1
        if self.counters["wrong"] > MAX_WRONG_EVENTS:
            self._fail("collabo_banner_not_found", "other_event_pages")
            return Decision("none", "other_event")
        return self._tap("wrong_event_back", "banner_rotated")

    def _event(self, reader, image):
        stages = (self.config.stage, self.config.backup) if self.config.backup else (self.config.stage,)
        read = reader.event(image, stages)
        if read["pass"] is None or read["deadline"] is None:
            return self._wait("event_unread")
        self.event = read["deadline"]
        if self.budget is None:
            # 用完: the Pass on screen now; a set count: that many lives, waiting for Pass to recover when short.
            self.budget = min(PASS_MAX, read["pass"]) if self.config.count == "all" else int(self.config.count)
            self._log({"event": "collabo_begin", "pass": read["pass"], "budget": self.budget, "event_deadline": self.event,
                       "stage": self.config.stage, "backup": self.config.backup, "unit": self.config.unit,
                       "level": self.config.level})
        if len(self.lives) >= self.budget:
            self._end("count_done" if self.lives else "no_pass:" + read["recovery"])
            return self._leave("event")
        if read["pass"] < 1:
            if self.config.count == "all":
                self._end("no_pass:" + read["recovery"])
                return self._leave("event")
            seconds = parse_recovery(read["recovery"])
            return self._start_wait("pass", PASS_RECHECK_S if seconds is None else seconds + PASS_MARGIN_S)
        full = read["full"] if isinstance(read["full"], dict) else {stages[0]: bool(read["full"])}
        open_stages = [s for s in stages if not full.get(s) and s not in self.full_seen]
        full_now = [s for s in stages if s not in open_stages]
        self.stage_full, self.full_seen = False, []
        if full_now:
            self._log({"event": "collabo_stages_full", "full": full_now, "open": open_stages})
        if not open_stages:
            seconds = min(FULL_RECHECK_MAX_S, FULL_RECHECK_S * 2 ** self.full_streak)
            self.full_streak += 1
            return self._start_wait("full", seconds)
        if open_stages[0] != self.stage:
            self._log({"event": "collabo_stage_switch", "from": self.stage, "to": open_stages[0]})
        self.stage = open_stages[0]
        cleared, failed = self.ledger.get(self.event, self.stage)
        self.level = target_level(self.config, cleared, failed)
        if self.level is None:
            self._end("level_none_clearable")
            return self._leave("event")
        self.counters["level"] = 0
        self.plus_from, self.plus_stuck = None, 0
        return self._tap(f"stage_{self.stage}", f"pass_{read['pass']}_lv{self.level}")

    def _stage_full(self, reader, image):
        self.stage_full = True
        self.full_seen.append(self.stage)
        self._log({"event": "collabo_stage_full_popup", "stage": self.stage})
        return self._tap("full_close", "stage_full")

    def _difficulty(self, reader, image):
        if self.stage_full:
            return self._tap("difficulty_back", "stage_full")
        read = reader.difficulty(image)
        if STAGE_WORDS[self.stage] not in read["stage"]:
            return self._tap("difficulty_back", "wrong_stage:" + read["stage"])
        if not read["ok"]:
            return self._tap("hard", "open_hard")
        level = read["level"]
        if level is None:
            return self._wait("level_unread")
        if self.level is None:
            return self._tap("difficulty_back", "no_target_level")
        self.counters["level"] += 1
        if self.counters["level"] > MAX_LEVEL_TAPS:
            self._fail("collabo_level_not_set", f"lv{level}->{self.level}")
            return Decision("none", "level_not_set")
        if self.config.level == "auto" and level < self.level:
            # Auto: press 「+」 while it is white (a higher level is open), stop at grey. 2026-11-20 live: the card opens
            # at the last played level, so the ledger alone missed open levels.
            plus = read.get("plus")
            if plus is None:
                return self._wait("plus_unread")
            if plus and self.plus_from == level:        # white but the level did not move: a stale frame, then stop
                self.plus_stuck += 1
                plus = self.plus_stuck < 2
            elif plus:
                self.plus_from, self.plus_stuck = level, 0
            if plus:
                return self._tap("level_plus", f"lv{level}_plus_open")
            self._log({"event": "collabo_level_highest", "stage": self.stage, "level": level, "cap": self.level})
            self.level = level
            return self._tap("level_ok", f"lv{level}_highest_open")
        if level < self.level:
            return self._tap("level_plus", f"lv{level}->{self.level}")
        if level > self.level:
            return self._tap("level_minus", f"lv{level}->{self.level}")
        return self._tap("level_ok", f"lv{level}")

    def _unit(self, reader, image):
        if self.stage_full:
            return self._tap("unit_back", "stage_full")
        read = reader.unit(image)
        shown = read["shown"]
        if shown is None:
            return self._wait("unit_unread")
        if shown != self.config.unit:
            return self._tap(f"unit_tab_{self.config.unit}", f"unit_{shown}->{self.config.unit}")
        if read["empty"]:
            # The game's recommended fill of an empty Collabo unit is not sampled yet: stop before any Pass is used.
            self._log({"event": "collabo_unit_empty", "unit": shown, "empty_slots": read["empty"]})
            self._end("unit_empty")
            return self._tap("unit_back", "unit_empty")
        return self._tap("unit_live", f"unit_{shown}")

    def _tension(self, reader, image):
        button = reader.tension(image)
        if button is None:
            return self._wait("tension_button_unread")
        if button == "start":
            # Only reachable right after this run's own テンション決定 (phase live); a stale page is not ours to start.
            self._fail("collabo_unexpected_start", "start_without_own_decide")
            return Decision("none", "unexpected_start")
        if self.budget is None or len(self.lives) >= self.budget:
            self._fail("collabo_unexpected_tension", "no_budget")
            return Decision("none", "no_budget")
        intent = {"live": len(self.lives) + 1, "stage": self.stage, "level": self.level, "event_deadline": self.event,
                  "unit": self.config.unit, "intent_time": self.now().isoformat(), "frame": tc.image_digest(image)}
        if self.authority is None or not self.authority.can_spend(SPEND_ACTION):
            self._end("spend_not_enabled")
            return Decision("none", "spend_not_enabled")
        if not self._log({"event": "collabo_intent", **intent}):
            self._fail("collabo_log_failed", "intent")
            return Decision("none", "log_failed")
        refusal = self.authority.authorize(SPEND_ACTION, image, APPROVER)
        if refusal:
            self._log({"event": "collabo_refused", **intent, "reason": refusal})
            self._end("spend_refused:" + str(refusal))
            return Decision("none", "spend_refused")
        self.lives.append(intent)
        self.full_streak = 0
        self.phase = "live"
        self.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": False}
        return Decision("tension", f"live_{intent['live']}")

    def _adopt_live(self, reader, image):
        """A live already under way on a fresh run (an earlier run stopped mid-live; its Pass is spent): play it out.
        It is not one of this run's lives and, without an event read, not written to the level ledger."""
        self._log({"event": "collabo_adopt_live"})
        self.phase = "live"
        self.live = {"frames": 0, "turn": None, "turn_actions": 0, "drags": 0, "started": True, "adopted": True}
        return self._battle(reader, image)

    def _report_after_spend_gap(self, reader, image):
        # A report on a fresh navigation frame (an earlier run's live): close it, it is not counted.
        return self._tap("report_ok", "stale_report")

    def _live(self, reader, image, page):
        live = self.live
        live["frames"] += 1
        if live["frames"] > MAX_LIVE_FRAMES:
            self._fail("spend_uncertain", "live_too_long")
            return Decision("none", "live_too_long")
        if page == "tension":
            button = reader.tension(image)
            if button == "start" and not live["started"]:
                live["started"] = True
                return self._tap("live_start", "after_decide")
            return Decision("wait", "tension_" + str(button))
        if page == "battle":
            live["started"] = True
            return self._battle(reader, image)
        if page == "report":
            return self._report(reader, image)
        return Decision("wait", "live_" + page)

    def _battle(self, reader, image):
        live = self.live
        read = reader.battle(image)
        if read["turn"] is not None and read["turn"] != live["turn"]:
            live["turn"], live["turn_actions"], live["drags"] = read["turn"], 0, 0
        prompt, own = read["prompt"], read["own"]
        if live["turn_actions"] >= MAX_TURN_ACTIONS:
            return Decision("wait", "turn_actions_used")
        if "もう1度" in prompt or "タップして決定" in prompt:
            if own != 1:
                return Decision("wait", f"confirm_own_row_{own}")
            live["turn_actions"] += 1
            return Decision("panel_confirm", f"turn_{live['turn']}_decide")
        if "決めてください" in prompt or "パネルをつかんで" in prompt:
            if own is None:
                return Decision("wait", "own_panel_unread")
            if own != 1 and live["drags"] < MAX_DRAGS_PER_TURN:
                live["turn_actions"] += 1
                live["drags"] += 1
                self.drag_row = own
                return Decision("drag_second", f"turn_{live['turn']}_row{own + 1}->2")
            live["turn_actions"] += 1
            return Decision("panel_confirm", f"turn_{live['turn']}_row{own + 1}")
        if read["skills"] and read["turn"] is not None and own is not None:
            live["turn_actions"] += 1
            return Decision("skill", f"turn_{live['turn']}_skill")
        return Decision("wait", "battle_animating")

    def _report(self, reader, image):
        read = reader.report(image)
        adopted = self.live.get("adopted")
        record = {"live": 0, "adopted": True} if adopted else self.lives[-1]
        if read["score"] is None:               # the report animates in: level first, SCORE last
            return Decision("wait", "report_unread")
        # 2026-11-20 live: at Lv.11 the クリアボーナス stayed unread when SCORE was read, so the level never rose.
        # Give it a few more frames; still unread -> report_ok with clear_unread (the runtime keeps the frame).
        level = read["level"] or self.level
        read = {**read, "clear": check_clear(level, read["clear"])}
        # 2026-11-21 live Lv.22 losses: クリアボーナス 「0pt」 stayed unread (SCORE 1,052). A win's SCORE includes the
        # bonus, so a SCORE below Lv.X's bonus is a loss.
        if read["clear"] is None and level and read["score"] < clear_bonus(level):
            read["clear"] = 0
        if read["clear"] is None and self.counters.get("report_clear", 0) < REPORT_CLEAR_FRAMES:
            self.counters["report_clear"] = self.counters.get("report_clear", 0) + 1
            return Decision("wait", "report_clear_unread")
        self.counters["report_clear"] = 0
        cleared = None if read["clear"] is None else read["clear"] > 0
        result = {**record, "event": "collabo_result", "report_level": read["level"], "clear_bonus": read["clear"],
                  "score": read["score"], "cleared": cleared}
        self._log(result)
        record.update(report_level=read["level"], clear_bonus=read["clear"], score=read["score"], cleared=cleared)
        level = read["level"] or record.get("level")
        if self.event and level and self.config.level == "auto" and cleared is not None and not adopted:
            try:
                self.ledger.record(self.event, self.stage, int(level), cleared)
            except OSError:
                self._log({"event": "collabo_ledger_failed"})
        self.phase, self.live = "navigate", None
        self.counters["nav"] = 0
        return self._tap("report_ok", {True: "cleared", False: "not_cleared", None: "clear_unread"}[cleared])

    def _leave(self, page):
        if page == "home":
            self.phase = "ended"
            return Decision("none", self.reason)
        if page == "event":
            return self._tap("event_back", "leave")
        if page == "difficulty":
            return self._tap("difficulty_back", "leave")
        if page == "unit":
            return self._tap("unit_back", "leave")
        if page == "report":
            return self._tap("report_ok", "leave")
        if page == "stage_full":
            return self._tap("full_close", "leave")
        return self._wait("leave_" + str(page))
