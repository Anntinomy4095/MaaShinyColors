"""Weekly official Auto Produce (オートスケジュール), first version: rules, screen reading and the Maa runtime.

User decisions (2026-10-22):

* The user configures row オート1 once in the game (scenario, unit, three knowhow books, no items). Maa
  verifies that row オート1 is configured (plus the optional scenario / P idol expectations of the panel), sets the run
  count, keeps 「ループする」 OFF, presses 決定 and then the start dialog.
* 2026-10-29 user (「auto 需要让 maa 移用 wing 的选人，在 auto 选择栏位后选择队伍和书」), setup = "maa": an empty
  オート1 is set by Maa through the game's wizard: 設定 → the panel's
  scenario (W.I.N.G. ミドル / ファン感謝祭 / G.R.A.D. / Landing Point / S.T.E.P.; Auto lists no say "Halo") → the
  panel's unit (1..20, ‹ › pages) → for S.T.E.P. the newest 設定履歴 set or the in-game プリセット1/2 (read back: three
  filled slots) → no items → 決定. A row that is already set is only verified, never edited.
* 2026-10-30: the panel's 育成方針 (Vocal/Dance/Visual, "" = leave it,
  "auto" = the specialty of the P card read on the unit page) is set on the row through 育成方針選択 (設定する; キャンセル
  on any mismatch). User 10-30 「auto每次进入都是空的」: the panel has no 「由 Maa 设定」 switch any more; its
  configuration always sets an empty オート1 (setup = "maa"). The name next to オート1 is the unit
  name: the row must show the name read on the unit page (or the panel's 「核对ユニット名」).
* Completion: the plain 「プロデュース開始」 is the default. 「スキップ」 (残りN回/日) is a separate option, default off,
  because its cost is not verified yet.
* Runs: 1..MAX_RUNS (panel, default 1), 30 stamina each. The start dialog shows 必要スタミナ before → after; Maa reads
  both and requires ``before >= 30 × runs`` and ``after == before − 30 × runs`` before pressing; any mismatch cancels the
  dialog (non-consuming) and stops.
* 2026-10-30 user 「允许」 (ループ with recovery soda): the panel's ループ (default off) turns 「ループする」 ON, which
  repeats オート1 up to 上限回数 25 (750 stamina, row count 1). The start dialog then asks for recovery items; Maa plans
  the fewest リカバリーソーダ 30/60/100 that cover 750 − stamina (exact when possible), at most the panel's ソーダ上限
  and ``project_policy.json`` ``resource_usage.stamina_items.max_per_run`` (stamina points), taps only 「+」 one at a
  time (each checked on セットするスタミナ), and presses プロデュース開始 only when 必要スタミナ shows 750/750 and the
  launcher granted ``weekly-auto-soda-start``; otherwise キャンセル (sodas are consumed only on start).
* 2026-10-31 带书 「ノウハウ页指定」 (books = "target"): the three books the ノウハウ page saved for Auto
  (``prefs.auto_step_books`` of the UI profile: title, Vo/Da/Vi/Me, 評価値) are picked in the book picker. Only the 15
  tiles of its first screen are used (sorted ノウハウ評価値順 ▼降順); a tile is matched by 評価値, tapped, and kept only
  when the detail panel shows the same title and stats (a tie of 評価値 is deselected and the next tile tried); after
  決定 the book page must show the three books' stats.

The Pipeline (``assets/resource/base/pipeline/weekly_produce.json``) owns every click; each click node is
``And[page anchors, SC.WeeklyProduce.Want.<kind>]``. ``WeeklyAutoRuntime.decide`` reads one frame once (cached by its
digest) and names the single next step. Unknown or unsampled screens (an empty row with setup "keep", the knowhow
tutorial, a running schedule) stop with a reason; nothing is retried. The Click nodes ship disabled; the launcher
enables those of the enabled ``weekly-auto`` table actions (``assets/input/pipeline_actions.json``).
"""
from __future__ import annotations

import copy
import dataclasses
import difflib
import itertools
import json
import logging
import re
import time
from pathlib import Path

from agent.core import task_common as tc
from agent.core.produce_config import AUTO_RUN_LIMIT, SKIP_TICKETS_PER_DAY, STAMINA_PER_RUN

LOGGER = logging.getLogger(__name__)

KIND = "weeklyauto"
START_ACTION = "weekly-auto-start"
SKIP_ACTION = "weekly-auto-skip"
SODA_START_ACTION = "weekly-auto-soda-start"   # プロデュース開始 of the recovery dialog (stamina + the set sodas)
POLICY_PATH = Path(__file__).resolve().parents[2] / "project_policy.json"
LOOP_STAMINA = STAMINA_PER_RUN * AUTO_RUN_LIMIT   # 「ループする」 ON: 25 runs, 750 stamina whatever the row count
SODA_SIZES = (30, 60, 100)  # リカバリーソーダ30/60/100, top to bottom in the recovery dialog
SODA_CAPS = (450, 600, 750)  # panel ソーダ上限 (stamina points; 450 = 750 − a full 300)
MAX_SODA_TAPS = 30           # 「+」 taps per run (a plan for ≤ 750 never needs more than 25)
APPROVER = "SC.WeeklyProduce.Dialog"
APPROVER_RECOVERY = "SC.WeeklyProduce.Recovery"
MAX_RUNS = 5               # hard per-task cap (panel offers 1..5); the game allows 25 in total
MAX_TAPS = 24              # count ± taps per task (never more than |target − count| are planned)
MAX_WAIT_FRAMES = 6        # consecutive unreadable/unchanged frames before a page-specific stop
MAX_PENDING_FRAMES = 4     # frames a ± tap may take to show its new count
MAX_RESULT_FRAMES = 60     # frames after the start press until 「オートプロデュース中」 on Home
MAX_COLLECT_FRAMES = 60    # frames of one result-collection step (tap → 終了 OK → リザルト OK → Home)
# After the result OK, Home may come under 「ミッション達成」 and 「イベント累計報酬獲得」 (live 2026-11-01 18:06; its OK opens
# the event page): any other screen hands over to SC.Common.GoHome (known popups, back to Home), at most this often.
MAX_GO_HOME = 2
# A click decided later than this after its frame is refused by the frame guard (frame_permit.MAX_AGE_S = 5 s:
# ``frame_stale_before_permit``) while the run would count it as sent (live 2026-11-01: the unit page's first decision
# read the P card, 7 s, and 次へ never went out → unit_next_page_not_left). Such a decision is re-made on the next
# frame with the run state restored, keeping what it read (wing_custom.DECISION_STALE_S).
DECISION_STALE_S = 3.5
MAX_SLOW_DECISIONS = 6
KEEP_READS = ("p_card", "p_card_frames", "policy_target", "unit_name")
SCENARIOS = ("", "WING", "FANS", "GRAD", "LP", "STEP")   # panel keys ("" = not checked)
TARGET_SCENARIO = "TARGET"   # panel 「按目标词条」, resolved before the run
BANNER_LETTERS = {"WING": "WING", "STEP": "STEP", "GRAD": "GRAD"}   # letters of the row banner (FANS/LP unsampled)
SETUP_MODES = ("keep", "maa")
# 编队: "auto" = tools/ui_engine.py first edits the chosen ユニット into the agent/core/auto_team.py
# team (a W.I.N.G. team-edit prep) and passes its P card (``expect_p_card``); "unit" = the unit as it is.
TEAM_SOURCES = ("auto", "unit")
# Left caption of the Auto scenario page (native 2026-10-30). 「育成プロデュース」 lists TRAIN_ORDER top to bottom; a tap on
# the card below the selected one selects it; 「特化プロデュース」 lists only S.T.E.P.
SETUP_CAPTIONS = {"WING": "W.I.N.G.編", "FANS": "ファン感謝祭編", "GRAD": "G.R.A.D.編", "LP": "LandingPoint編",
                  "STEP": "S.T.E.P.編"}   # read without spaces (OCR gives 「LandingPoint編」)
TRAIN_ORDER = ("WING", "FANS", "GRAD", "LP")
BOOK_SOURCES = ("history", "preset1", "preset2", "target")   # newest 設定履歴 / in-game プリセット1, 2 / ノウハウ页指定
POLICY_CHOICES = ("Vocal", "Dance", "Visual")      # the three choices of 育成方針選択
POLICIES = ("", "auto", *POLICY_CHOICES)            # 育成方針 ("" = leave the row's, "auto" = the P card's specialty)
SPECIALTY_POLICY = {"Vo": "Vocal", "Da": "Dance", "Vi": "Visual"}
SETUP_UNITS = 20           # unit tabs 1..10 on two pages (‹ ›)
UNITS_PER_PAGE = 10
MAX_SETUP_TAPS = 6         # same-page taps per run (scenario tab / list, unit page / tab, 育成方針 choice)
HISTORY_CALL_FRAMES = 6    # 「呼び出し」 that leaves 設定履歴 open this long was refused (greyed: a book of the team's idol)
BOOK_FILL_FRAMES = 6       # frames for the book slots to fill after a source; then おまかせ編成 fills them
MAX_LEAVE_FRAMES = 40      # frames a page-advancing tap may take (the 「Now Loading」 strip held a page ~20 s, 10-30)
MAX_PICKER_TAPS = 12       # book picker tile taps per run (3 books, a few 評価値 ties)
PROFILE_PATH = Path(__file__).resolve().parents[2] / "dev_assets" / "private" / "ui" / "profile.json"
STATS = ("vo", "da", "vi", "me")

# ---- fixed 1280x720 boxes (native capture 2026-10-22)
HOME_STAMINA = (1005, 668, 110, 26)        # 「300/300」 under the Home プロデュース button
HOME_LABEL = (1000, 638, 120, 34)          # 「プロデュース」 / 「プロデュース中」 (running Auto)
TITLE = (38, 10, 330, 52)                  # page title
ROW1_BANNER = (40, 118, 220, 54)           # scenario banner of row オート1
ROW1_UNITS = tuple((270 + 75 * i, 114, 68, 64) for i in range(6))   # P + 4 S + guest
ROW1_BOOKS = tuple((x, 114, 64, 64) for x in (729, 803, 877))
ROW1_ITEMS = tuple((x, 114, 64, 64) for x in (955, 1030))
# Rows of the other scenarios carry no books: their two item slots sit further left (G.R.A.D. row, live 2026-10-30).
ROW1_TRAIN_ITEMS = tuple((x, 114, 64, 64) for x in (840, 914))
ROW1_EDIT = (1115, 100, 95, 42)
ROW1_CLEAR = (1115, 150, 95, 42)
ROW1_EMPTY_TEXT = (480, 124, 220, 50)      # 「未設定」 of an empty row (row 2 at y+171)
ROW1_SET_BUTTON = (1115, 127, 95, 42)      # 「設定」 of an empty row
ROW1_UNIT_NAME = (150, 205, 440, 36)       # unit name next to オート1 (「G.R.A.D.」 live 10-30; 「結華」 demo #149)
ROW1_POLICY = (733, 205, 125, 40)          # 育成方針 button label 「Vocal」
ROW1_COUNT = (1110, 205, 52, 40)           # プロデュース回数 of row オート1
LOOP_BUTTON = (125, 635, 170, 56)          # 「ループする」 button; pink border + tick when ON
TOTAL_STAMINA = (810, 648, 115, 42)        # 必要スタミナ box (30 × total runs)
TOTAL_RUNS = (945, 648, 115, 42)           # 上限回数 box 「5 /25」
DIALOG_BEFORE = (575, 415, 105, 38)        # 必要スタミナ before (= current stamina)
DIALOG_AFTER = (760, 415, 95, 38)          # 必要スタミナ after
DIALOG_SKIP_LEFT = (565, 540, 150, 32)     # 「残り5回/日」
LOADING_TEXT = (826, 650, 330, 62)         # 「Now Loading」 bottom right (home-loading.png: x 840..1120, y 662..698)
# Setup wizard (native samples 2026-10-30, wing_samples/2026-10-29 13:24..13:28Z; item page captures-20261022 #148)
SCENARIO_CAPTION = (150, 362, 300, 44)     # 「W.I.N.G.編」 / 「S.T.E.P.編」 under the stage picture
UNIT_COUNTER = (60, 155, 80, 36)           # 「1/20」: the shown unit
BOOK_PROMPT = (630, 455, 525, 50)          # 「ノウハウブックを3つ選択してください」 while a slot is empty
BOOK_VO = tuple((x, 524, 75, 38) for x in (580, 824, 1068))   # 「+100」: Vo. bonus of a filled book slot
# A filled slot's four bonuses (live 2026-10-30 15:10Z: +60/+20/+20/+30 for 【オイサラバエル】樋口円香).
BOOK_STATS = tuple({"vo": (x, 524, 75, 38), "da": (x + 100, 524, 75, 38), "vi": (x, 556, 75, 38),
                    "me": (x + 100, 556, 75, 38)} for x in (580, 824, 1068))
# Book picker (live 2026-10-30 15:04–15:10Z, 0 spend): 5 × 3 tiles from (535, 180), pitch 138, a tap selects
# (pink ✓, 選択数 N/3) and shows the book on the left, a second tap deselects; books of the team's idols are grey
# (detail only); two books of one idol may be chosen; 決定 lights at 3/3. The game keeps the sort (ノウハウ評価値順 ▼降順).
PICKER_COLS, PICKER_ROWS, PICKER_PITCH, PICKER_ORIGIN = 5, 3, 138, (535, 180)
PICKER_EVAL = (48, 75, 64, 36)             # tile-relative 「1020」
PICKER_BG = (4, 50, 10, 30)                # tile-relative background: ~249 selectable, ~149 grey
PICKER_CHECK = (76, -6, 38, 36)            # tile-relative ✓: pink ~750–800 px selected, <= 47 otherwise
PICKER_BG_MIN = 200
PICKER_CHECK_PINK = 300
PICKER_COUNT_LABEL = (545, 100, 95, 45)    # 「選択数」: tells the picker from the book page (same title)
PICKER_SORT = (965, 100, 165, 40)          # 「ノウハウ評価値順」
PICKER_ORDER = (1140, 98, 100, 45)         # 「降順」
DETAIL_TITLE = (145, 92, 300, 36)          # 「【オイサラバエル】樋口円香」 of the last tapped book
DETAIL_STATS = {"vo": (195, 134, 70, 34), "da": (350, 134, 70, 34), "vi": (195, 170, 70, 32), "me": (350, 170, 70, 32)}
HISTORY_DATE = (950, 106, 215, 32)         # 「2026/10/22 22:05」 of the newest 設定履歴 set
HISTORY_CALL = (1010, 157, 116, 42)        # its 「呼び出し」
ITEM_SLOTS = ((400, 140, 180, 60), (400, 232, 180, 60))      # 「アイテムを選択していません」 ×2
UNIT_TAB1 = (28, 85, 95, 32)               # first unit tab: 「ユニット1」 (page 1) / 「ユニット11」 (page 2)
UNIT_NAME = (150, 155, 300, 40)            # unit name right of 「1/20」
PRESET_TITLE = (355, 25, 340, 45)          # 「ノウハウブックプリセット」 dialog over the book page
PRESET_LABEL = ((332, 98, 150, 30), (332, 283, 150, 30))     # 「プリセット1」/「プリセット2」
PRESET_NAME = ((490, 98, 380, 30), (490, 283, 380, 30))      # the user's preset names
PRESET_SET = ((765, 152, 105, 36), (765, 337, 105, 36))      # 「セット」
POLICY_TITLE = (365, 70, 300, 45)          # 「育成方針選択」 dialog over the schedule
POLICY_TEXT = (390, 440, 495, 80)          # 「Vocalに関するスキルパネルを…」 of the highlighted choice
# 「ループする」 ON repeats the schedule up to 上限回数 25 (25 h, 750 stamina; live 2026-10-30 with 回数 1), and the start
# dialog then asks for recovery items (300 / 750). Without the panel's ループ Maa turns it off (weekly-auto-loop-off);
# with it, on (weekly-auto-loop-on).
LOOP_ON_PINK = 800          # pink pixels in LOOP_BUTTON: ON ~1545, OFF 0 (frames 158/160)
LOOP_OFF_PINK = 50
PRESENT_STD = 35.0          # card/book tile std: present 55..72, empty striped slot ~11
EMPTY_STD = 20.0
# Recovery dialog 「回復アイテムをセットしてオートプロデュースを開始します」 (live 2026-10-30 07:04Z, ループ ON, stamina 300):
# rows リカバリーソーダ30/60/100 133 px apart, each 「x162」 owned, − / count / + / ».
RECOVERY_SET = ((222, 276, 105, 55), (390, 276, 105, 55))   # セットするスタミナ 「300 » 300」: stamina → with sodas
RECOVERY_REQUIRED = (255, 416, 210, 52)    # 必要スタミナ 「300/750」: stamina with sodas / needed
SODA_ROW_STEP = 133
SODA_OWNED = tuple((630, 218 + SODA_ROW_STEP * i, 75, 38) for i in range(3))     # 「x162」
SODA_COUNT = tuple((800, 196 + SODA_ROW_STEP * i, 125, 48) for i in range(3))    # 「0」 between − and +

# Auto 结束 (live 2026-11-01 15:02, wing_samples/2026-11-01/20261101T0402…–0404…): after its runs Home keeps
# 「オートプロデュース中」 until the button is tapped: 「オートプロデュース終了」 OK → Now Loading → 「オートリザルト」 OK → Home
# 「プロデュース」 (+ 「ミッション達成」). The result page's 保護/移籍/売却 boxes and buttons are never touched.
# Before the end (live 2026-11-01 17:05, 20261101T060532…): the tap opens 「オートプロデュース確認」 (実行中, 残り時間,
# 育成シナリオ) with キャンセル / 終了 (ends the Auto: never) / スキップ (残りN回/日: never); Home has no プロデュース
# button while an Auto runs, so no other Produce can start.
END_TITLE = (395, 195, 330, 50)            # 「オートプロデュース終了」
END_OK = (560, 428, 162, 75)
RESULT_OK = (1095, 625, 165, 75)
RUNNING_LEFT = (533, 195, 80, 40)          # 残り時間 「56:44」 of row 1

WIZARD_PAGES = {"ユニット編成": "w_unit", "ノウハウブック選択": "w_books", "ノウハウブック設定履歴": "w_history",
                "プロデュースアイテム準備": "w_item", "ノウハウブックについて": "w_tutorial"}

# ---- Pipeline anchor nodes used by the page classifier (weekly_produce.json)
NODE_LOADING = "SC.Loading.Present"
NODE_DIALOG = "SC.WeeklyProduce.Dialog.Guard"
NODE_RECOVERY = "SC.WeeklyProduce.Recovery.Guard"
NODE_SCHEDULE = "SC.WeeklyProduce.Schedule.Guard"
NODE_SELECT = "SC.Nav.ProduceSelect.Guard"
NODE_AUTO_ENTRY = "SC.WeeklyProduce.Anchor.AutoEntry"
NODE_HOME = "SC.Common.Home.Guard"
NODE_DIALOG_SKIP = "SC.WeeklyProduce.Anchor.DialogSkip"
NODE_END = "SC.WeeklyProduce.End.Guard"
NODE_RUNNING = "SC.WeeklyProduce.Running.Guard"
NODE_RESULT = "SC.WeeklyProduce.Result.Guard"
# Decision kind -> its Click node.
CLICK_NODES = {"home_open": "SC.WeeklyProduce.Home.OpenProduce", "open_auto": "SC.WeeklyProduce.Select.OpenAuto",
               "home_running": "SC.WeeklyProduce.Home.OpenRunning", "end_ok": "SC.WeeklyProduce.End.Ok",
               "result_ok": "SC.WeeklyProduce.Result.Ok", "running_cancel": "SC.WeeklyProduce.Running.Cancel",
               "plus": "SC.WeeklyProduce.Schedule.Plus", "minus": "SC.WeeklyProduce.Schedule.Minus",
               "decide": "SC.WeeklyProduce.Schedule.Decide", "cancel": "SC.WeeklyProduce.Dialog.Cancel",
               "start": "SC.WeeklyProduce.Dialog.Start", "skip": "SC.WeeklyProduce.Dialog.Skip",
               "row_set": "SC.WeeklyProduce.Schedule.RowSet",
               "tab_train": "SC.WeeklyProduce.Setup.TabTrain", "tab_special": "SC.WeeklyProduce.Setup.TabSpecial",
               "scenario_next": "SC.WeeklyProduce.Setup.ScenarioNext",
               **{f"unit_tab_{n}": f"SC.WeeklyProduce.Setup.UnitTab{n}" for n in range(1, UNITS_PER_PAGE + 1)},
               "unit_next": "SC.WeeklyProduce.Setup.UnitNext", "book_history": "SC.WeeklyProduce.Setup.BookHistory",
               "history_call": "SC.WeeklyProduce.Setup.HistoryCall", "book_next": "SC.WeeklyProduce.Setup.BookNext",
               "history_back": "SC.WeeklyProduce.Setup.HistoryBack", "book_omakase": "SC.WeeklyProduce.Setup.BookOmakase",
               "item_decide": "SC.WeeklyProduce.Setup.ItemDecide",
               "scenario_down": "SC.WeeklyProduce.Setup.ScenarioDown",
               "unit_page_prev": "SC.WeeklyProduce.Setup.UnitPagePrev",
               "unit_page_next": "SC.WeeklyProduce.Setup.UnitPageNext",
               "book_preset": "SC.WeeklyProduce.Setup.BookPreset",
               "preset_set_1": "SC.WeeklyProduce.Setup.PresetSet1", "preset_set_2": "SC.WeeklyProduce.Setup.PresetSet2",
               "policy_open": "SC.WeeklyProduce.Schedule.PolicyOpen",
               **{f"policy_{p.lower()}": f"SC.WeeklyProduce.Policy.{p}" for p in POLICY_CHOICES},
               "policy_set": "SC.WeeklyProduce.Policy.Set", "policy_cancel": "SC.WeeklyProduce.Policy.Cancel",
               "loop_off": "SC.WeeklyProduce.Schedule.LoopOff", "loop_on": "SC.WeeklyProduce.Schedule.LoopOn",
               **{f"soda_{size}": f"SC.WeeklyProduce.Recovery.Soda{size}" for size in SODA_SIZES},
               "recovery_cancel": "SC.WeeklyProduce.Recovery.Cancel",
               "recovery_start": "SC.WeeklyProduce.Recovery.Start",
               "book_slot1": "SC.WeeklyProduce.Setup.BookSlot1", "picker_decide": "SC.WeeklyProduce.Picker.Decide",
               **{f"picker_tile_{r}_{c}": f"SC.WeeklyProduce.Picker.Tile{r}{c}"
                  for r in range(PICKER_ROWS) for c in range(PICKER_COLS)}}
# The target anchor of a wizard/方針 click node (its And guard, read again before the tap is decided): a dialog that is
# still animating in (育成方針選択, live 2026-10-30) waits instead of a tap the node then never sends.
TARGET_ANCHORS = {kind: "SC.WeeklyProduce.Anchor." + anchor for kind, anchor in (
    ("row_set", "RowSet"), ("tab_train", "TabTrain"), ("tab_special", "TabSpecial"), ("scenario_next", "SetupNext"),
    ("scenario_down", "ScenarioNextCard"), ("unit_next", "SetupNext"), ("book_history", "BookHistory"),
    ("history_call", "HistoryCall"), ("book_next", "SetupNext"), ("book_preset", "PresetButton"),
    ("history_back", "HistoryTitle"), ("book_omakase", "BookOmakase"),
    ("preset_set_1", "PresetSet1"), ("preset_set_2", "PresetSet2"), ("item_decide", "SetupDecide"),
    ("policy_open", "PolicyButton"), ("policy_vocal", "PolicyVocal"), ("policy_dance", "PolicyDance"),
    ("policy_visual", "PolicyVisual"), ("policy_set", "PolicySet"), ("policy_cancel", "PolicyCancel"),
    ("loop_off", "LoopButton"), ("loop_on", "LoopButton"),
    *((f"soda_{size}", f"Soda{size}") for size in SODA_SIZES), ("book_slot1", "BookSlot1"),
    ("picker_decide", "PickerDecide"), ("end_ok", "EndOk"), ("result_ok", "ResultOk"),
    ("running_cancel", "RunningCancel"))}


def want_node(kind):
    """The Want recognition of a decision kind: "home_open" -> SC.WeeklyProduce.Want.HomeOpen."""
    return "SC.WeeklyProduce.Want." + "".join(part.capitalize() for part in kind.split("_"))


# ---------------------------------------------------------------- config
@dataclasses.dataclass(frozen=True)
class WeeklyAutoConfig:
    runs: int = 1
    use_skip: bool = False
    expect_scenario: str = ""
    expect_unit: str = ""
    setup: str = "keep"
    unit: int = 1
    books: str = "history"
    policy: str = ""
    team: str = "unit"
    loop: bool = False
    soda_cap: int = 0        # stamina points the recovery sodas may add (ループ only; 0 = none)

    @classmethod
    def from_params(cls, p):
        known = {"runs", "use_skip", "expect_scenario", "expect_unit", "setup", "unit", "books", "policy", "team",
                 "loop", "soda_cap"}
        unknown = set(p) - known
        tc.require(not unknown, f"未知参数：{sorted(unknown)}")
        runs = p.get("runs", 1)
        if isinstance(runs, str) and runs.strip().isdigit():
            runs = int(runs.strip())
        tc.require(type(runs) is int and 1 <= runs <= MAX_RUNS, f"次数须为 1..{MAX_RUNS}")
        use_skip = p.get("use_skip", False)
        tc.require(type(use_skip) is bool, "use_skip 须为布尔值")
        if use_skip:
            tc.require(runs <= SKIP_TICKETS_PER_DAY, f"スキップ 每日最多 {SKIP_TICKETS_PER_DAY} 次")
        scenario = p.get("expect_scenario", "")
        # 「按目标词条」 is replaced by a scenario before the run (tools/ui_engine.knowhow_target)
        tc.require(scenario != TARGET_SCENARIO, "剧本「按目标词条」须由界面开始，换成具体剧本后再运行")
        tc.require(scenario in SCENARIOS, f"剧本只支持：{list(SCENARIOS)}")
        unit_name = p.get("expect_unit", "")
        tc.require(isinstance(unit_name, str) and len(unit_name) <= 20, "ユニット名须为不超过 20 字的文本")
        setup = p.get("setup", "keep")
        tc.require(setup in SETUP_MODES, f"オート1 设定方式只支持：{list(SETUP_MODES)}")
        if setup == "maa":
            tc.require(scenario in SETUP_CAPTIONS, "由 Maa 设定オート1 时须选剧本")
        unit = p.get("unit", 1)
        if isinstance(unit, str) and unit.strip().isdigit():
            unit = int(unit.strip())
        tc.require(type(unit) is int and 1 <= unit <= SETUP_UNITS, f"ユニット须为 1..{SETUP_UNITS}")
        books = p.get("books", "history")
        tc.require(books in BOOK_SOURCES, f"带书只支持：{list(BOOK_SOURCES)}")
        policy = p.get("policy", "")
        tc.require(policy in POLICIES, f"育成方針只支持：{list(POLICIES)}")
        team = p.get("team", "unit")
        tc.require(team in TEAM_SOURCES, f"编队只支持：{list(TEAM_SOURCES)}")
        loop = p.get("loop", False)
        tc.require(type(loop) is bool, "loop 须为布尔值")
        soda_cap = 0
        if loop:
            # ループ repeats the row to 上限回数 25 whatever its count: the row keeps count 1; the panel hides 次数 and
            # スキップ (5/day) while ループ is on, so their saved values are ignored
            runs, use_skip = 1, False
            soda_cap = p.get("soda_cap", SODA_CAPS[0])
            if isinstance(soda_cap, str) and soda_cap.strip().isdigit():
                soda_cap = int(soda_cap.strip())
            tc.require(soda_cap in SODA_CAPS, f"ソーダ上限只支持：{list(SODA_CAPS)}")
        return cls(runs, use_skip, scenario, tc.normalize_text(unit_name), setup, unit, books, policy, team, loop,
                   soda_cap)

    @property
    def stamina(self):
        return LOOP_STAMINA if self.loop else STAMINA_PER_RUN * self.runs

    @property
    def spend_action(self):
        if self.loop:
            return SODA_START_ACTION
        return SKIP_ACTION if self.use_skip else START_ACTION

    def to_dict(self):
        return dataclasses.asdict(self)


# ---------------------------------------------------------------- pure rules
def policy_for_card(card_id):
    """育成方針 for 「自动」: the P card's main specialty (produce_config.card_specialty), None when unknown/balanced."""
    from agent.core.produce_config import card_specialty
    try:
        return SPECIALTY_POLICY.get(card_specialty(card_id))
    except (OSError, ValueError):
        return None


def parse_int(text):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"[0-9]{1,4}", text)
    return int(text) if match else None


def parse_ratio(text):
    """「300/300」/「5/25」 -> (a, b); None when malformed."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"([0-9]{1,4})/([0-9]{1,4})", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_skip_left(text):
    """「残り5回/日」 -> 5."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"残り([0-9])回/日", text)
    return int(match.group(1)) if match else None


def loop_state(pink_pixels):
    if pink_pixels >= LOOP_ON_PINK:
        return "on"
    if pink_pixels <= LOOP_OFF_PINK:
        return "off"
    return None


def count_step(count, target):
    """Next count tap toward ``target``: "plus" / "minus" / None (equal). One step per tap, never past the target."""
    if count < target:
        return "plus"
    if count > target:
        return "minus"
    return None


def schedule_reasons(config, state, set_by_maa=False, unit_name=None):
    """Stop reasons of a fully read schedule state (empty list = the row may be used). ``state`` keys: row, layout
    ("step": 3 books + 2 items; "train": 2 items only), items, books, loop, scenario, unit_name, count. ``set_by_maa``: this run set the row and read the scenario caption in the
    wizard, so the banner (unsampled for most scenarios) is not read again; ``unit_name`` is the name read on its unit
    page."""
    reasons = []
    if state["row"] == "empty":
        return ["row1_empty"]
    if state["row"] != "configured":
        return ["row1_unread"]
    if state["items"] != 0:
        reasons.append("row1_items_set")
    if state.get("layout", "step") == "step" and state["books"] != 3:
        reasons.append(f"row1_books_{state['books']}")
    wanted_loop = "on" if config.loop else "off"
    if state["loop"] not in ("on", "off"):
        reasons.append("loop_unread")
    elif state["loop"] != wanted_loop:
        reasons.append("loop_" + state["loop"])
    if config.expect_scenario and not set_by_maa:
        expected = BANNER_LETTERS.get(config.expect_scenario)
        # The stylized banner reads e.g. 「SS.T.E.P.C」 (frame 149): letters only, the expected name must appear in it.
        if expected is None:
            reasons.append("scenario_banner_unsampled")
        elif expected not in (state.get("scenario") or ""):
            reasons.append("scenario_mismatch")
    wanted = unit_name or config.expect_unit
    if wanted and state.get("unit_name") != wanted:
        reasons.append("unit_mismatch")
    count = state.get("count")
    if count is None or not 1 <= count <= AUTO_RUN_LIMIT:
        reasons.append("count_unread")
    return reasons


def decide_totals(config, total_runs, total_stamina):
    """Before 決定: 上限回数 must equal the row-1 count (no other row, also the rows below the fold, adds runs) and
    必要スタミナ must equal 30 × runs."""
    reasons = []
    expected_runs = AUTO_RUN_LIMIT if config.loop else config.runs
    if total_runs is None or total_runs[1] != AUTO_RUN_LIMIT:
        reasons.append("total_runs_unread")
    elif total_runs[0] != expected_runs:
        reasons.append(f"other_rows_scheduled_{total_runs[0]}")
    if total_stamina is None:
        reasons.append("required_stamina_unread")
    elif total_stamina != config.stamina:
        reasons.append(f"required_stamina_{total_stamina}")
    return reasons


def dialog_reasons(config, before, after, skip_left=None):
    """Start dialog check: 必要スタミナ before → after must show exactly 30 × runs being spent."""
    if before is None or after is None:
        return ["dialog_stamina_unread"]
    reasons = []
    if before < config.stamina:
        reasons.append(f"stamina_short_{before}")
    if after != before - config.stamina:
        reasons.append(f"stamina_mismatch_{before}_{after}")
    if config.use_skip:
        if skip_left is None:
            reasons.append("skip_left_unread")
        elif skip_left < config.runs:
            reasons.append(f"skip_left_{skip_left}")
    return reasons


def stamina_item_cap(path=POLICY_PATH):
    """``project_policy.json`` ``resource_usage.stamina_items``: the stamina points recovery items may add per run;
    0 when disabled, missing or malformed."""
    try:
        setting = json.loads(Path(path).read_text(encoding="utf-8"))["resource_usage"]["stamina_items"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if not isinstance(setting, dict):
        return 0
    cap = setting.get("max_per_run")
    return cap if setting.get("enabled") is True and type(cap) is int and cap > 0 else 0


def plan_sodas(need, owned, cap):
    """Counts (30, 60, 100) of リカバリーソーダ that add at least ``need`` stamina: the smallest total (no overshoot
    when an exact sum exists), then the fewest sodas; never more than ``owned`` (None = unread) or a total over
    ``cap``. None when no such plan exists; (0, 0, 0) when nothing is needed."""
    if need <= 0:
        return (0, 0, 0)
    if any(n is None for n in owned):
        return None
    best = None
    ranges = [range(min(n, need // size + 1) + 1) for size, n in zip(SODA_SIZES, owned)]
    for counts in itertools.product(*ranges):
        total = sum(c * size for c, size in zip(counts, SODA_SIZES))
        if total < need or total > cap:
            continue
        key = (total, sum(counts))
        if best is None or key < best[0]:
            best = (key, counts)
    return best[1] if best else None


def clean_book_plan(raw):
    """``prefs.auto_step_books`` -> three {"title", "vo", "da", "vi", "me", "eval"} (title without spaces), or None."""
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    out = []
    for book in raw:
        if not isinstance(book, dict):
            return None
        title = tc.normalize_text(book.get("title")) if isinstance(book.get("title"), str) else None
        values = [book.get(k) for k in (*STATS, "eval")]
        if not title or len(title) > 60 or not all(type(v) is int and 0 <= v <= 2000 for v in values):
            return None
        out.append({"title": title, **dict(zip((*STATS, "eval"), values))})
    return tuple(out)


def load_halo_book(path=PROFILE_PATH):
    """The one book the ノウハウ page saved for say "Halo" (UI profile ``prefs.halo_book``, a one-item list of the
    ``auto_step_books`` shape); None when unset or invalid."""
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return halo_book_of(profile)


def halo_book_of(profile):
    """``prefs.halo_book`` of a UI profile dict -> the one book plan, or None."""
    raw = (((profile if isinstance(profile, dict) else {}).get("prefs")) or {}).get("halo_book")
    plan = clean_book_plan(list(raw) * 3) if isinstance(raw, list) and len(raw) == 1 else None
    return dict(plan[0]) if plan else None


def load_book_plan(path=PROFILE_PATH):
    """The three books the ノウハウ page saved for Auto (UI profile ``prefs.auto_step_books``); None when unset."""
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return clean_book_plan(((profile or {}).get("prefs") or {}).get("auto_step_books"))


_SMALL_KANA = str.maketrans("ァィゥェォッャュョヮぁぃぅぇぉっゃゅょ", "アイウエオツヤユヨワあいうえおつやゆよ")
TITLE_MATCH = 0.85   # OCR reads small kana as large (「ポシェット」 -> 「ポシエット」, live 10-30); stats stay exact


def book_matches(book, title, stats):
    """The detail panel (title, {vo, da, vi, me}) shows ``book`` of the plan: the same four stats and a title equal
    after folding small kana, or at least TITLE_MATCH similar."""
    if not title or not all(stats.get(k) == book[k] for k in STATS):
        return False
    shown, wanted = (tc.normalize_text(t).translate(_SMALL_KANA) for t in (title, book["title"]))
    return shown == wanted or difflib.SequenceMatcher(None, shown, wanted).ratio() >= TITLE_MATCH


def recovery_reasons(config, values, plan_total):
    """Recovery dialog after the planned sodas: セットするスタミナ must read stamina → stamina + the sodas, 必要スタミナ
    that sum over the run's need (750 for ループ)."""
    before, after, required = values["before"], values["after"], values["required"]
    if before is None or after is None or required is None:
        return ["recovery_unread"]
    reasons = []
    if after != before + plan_total:
        reasons.append(f"soda_total_{after - before}")
    if required != (after, config.stamina):
        reasons.append(f"required_stamina_{required[0]}_{required[1]}")
    return reasons


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str            # home_open / open_auto / plus / minus / decide / start / skip / cancel / wait / none
    reason: str = ""


# ---------------------------------------------------------------- screen reading
def _patch(image, box):
    x, y, w, h = box
    return image[y:y + h, x:x + w]


def tile_std(image, box):
    return float(_patch(image, box).astype(float).std())


def pink_pixels(image, box):
    """Pink border pixels (RGB R>220, G<140, B>150) in a BGR image."""
    patch = _patch(image, box).astype(int)
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    return int(((r > 220) & (g < 140) & (b > 150)).sum())


def check_pixels(image, box):
    """Pixels of the picker's pink ✓ circle (RGB ~240/64/112) in a BGR image."""
    patch = _patch(image, box).astype(int)
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    return int(((r > 200) & (g < 120) & (b > 80) & (b < 180)).sum())


def presence(image, boxes):
    """Number of present tiles, or None when any tile is between the present/empty thresholds."""
    count = 0
    for box in boxes:
        std = tile_std(image, box)
        if std >= PRESENT_STD:
            count += 1
        elif std > EMPTY_STD:
            return None
    return count


class Reader:
    """Fixed-box reads over a ``task_custom.Vision``-like object (``text(image, roi, threshold)``, ``hit(node, image)``)."""

    def __init__(self, vision):
        self.vision = vision

    def text(self, image, box, threshold=0.8):
        try:
            return self.vision.text(image, box, threshold)
        except Exception:  # unreadable box -> None
            return None

    def hit(self, node, image):
        try:
            return bool(self.vision.hit(node, image))
        except Exception:
            return False

    # -- pages
    def page(self, image):
        if self.hit(NODE_LOADING, image):
            return "loading"
        if self.hit(NODE_DIALOG, image):
            return "dialog"
        if self.hit(NODE_END, image):
            return "end"
        if self.hit(NODE_RESULT, image):
            return "result"
        if self.hit(NODE_RUNNING, image):
            return "running"
        if self.hit(NODE_RECOVERY, image):
            return "recovery"
        if self.hit(NODE_SCHEDULE, image):
            return "schedule"
        title = self.text(image, TITLE)
        # Dialogs dim the page: 育成方針選択 over the schedule, ノウハウブックプリセット over the book page (whose title then
        # reads e.g. 「)シウハウブック選択」).
        if title in (None, "オートスケジュール") and self.text(image, POLICY_TITLE) == "育成方針選択":
            return "policy"
        if (title is None or "ウハウブック" in title) and self.text(image, PRESET_TITLE) == "ノウハウブックプリセット":
            return "w_preset"
        if title in WIZARD_PAGES:
            if WIZARD_PAGES[title] == "w_books" and self.text(image, PICKER_COUNT_LABEL) == "選択数":
                return "w_picker"
            return WIZARD_PAGES[title]
        if self.hit(NODE_SELECT, image):
            # The auto-mode scenario select (wizard step 1) has no 「オートプロデュース」 button (frame 101/105).
            return "select" if self.hit(NODE_AUTO_ENTRY, image) else "w_select"
        if self.hit(NODE_HOME, image):
            return "home"
        # 2026-10-25 live: 「Now Loading」 over the dimmed Home misses both loading templates (cut from the title page's
        # red background); read the words, only on frames no page claimed.
        if re.search(r"Load", self.text(image, LOADING_TEXT, 0.5) or ""):
            return "loading"
        return "unknown"

    def running_left(self, image):
        text = self.text(image, RUNNING_LEFT)
        return text if text and re.fullmatch(r"\d{1,2}:\d{2}", text) else None

    def home(self, image):
        label = self.text(image, HOME_LABEL)
        stamina = parse_ratio(self.text(image, HOME_STAMINA))
        # The running label 「プロデュース中」 reads e.g. 「プロデュー-ス中」 (frame 175): ignore ASCII/fullwidth hyphens.
        plain = re.sub(r"[-‐－]", "", label) if label else None
        return {"label": label, "running": plain == "プロデュース中", "ready": label == "プロデュース", "stamina": stamina}

    def schedule(self, image):
        edit, clear = self.text(image, ROW1_EDIT), self.text(image, ROW1_CLEAR)
        banner_std = tile_std(image, ROW1_BANNER)
        units = presence(image, ROW1_UNITS)
        if edit == "編集" and clear == "クリア" and banner_std >= PRESENT_STD and units == 6:
            row = "configured"
        elif self.text(image, ROW1_EMPTY_TEXT) == "未設定" and self.text(image, ROW1_SET_BUTTON) == "設定":
            row = "empty"
        else:
            row = "unknown"
        state = {"row": row, "banner_std": round(banner_std, 1), "units": units}
        if row != "configured":
            return state
        banner = self.text(image, ROW1_BANNER, threshold=0.5)
        letters = re.sub(r"[^A-Z]", "", banner.upper()) if banner else None
        # Only S.T.E.P. rows carry 3 books; an unread banner reads the other layout, whose item boxes then cover books.
        step = "STEP" in (letters or "")
        state.update(
            layout="step" if step else "train", items=presence(image, ROW1_ITEMS if step else ROW1_TRAIN_ITEMS),
            books=presence(image, ROW1_BOOKS) if step else None,
            loop=loop_state(pink_pixels(image, LOOP_BUTTON)), scenario=letters,
            unit_name=self.text(image, ROW1_UNIT_NAME), policy=self.text(image, ROW1_POLICY),
            count=parse_int(self.text(image, ROW1_COUNT)),
            total_runs=parse_ratio(self.text(image, TOTAL_RUNS)),
            total_stamina=parse_int(self.text(image, TOTAL_STAMINA)))
        return state

    # -- setup wizard
    def caption(self, image):
        text = self.text(image, SCENARIO_CAPTION)
        return re.sub(r"\s+", "", text) if text else None

    def unit(self, image):
        shown = parse_ratio(self.text(image, UNIT_COUNTER))
        return shown[0] if shown and shown[1] == 20 else None

    def unit_tab_page(self, image):
        """0 when the tabs show ユニット1–10, 1 for ユニット11–20 (read from the first tab)."""
        match = re.fullmatch(r"ユニット([0-9]{1,2})", self.text(image, UNIT_TAB1) or "")
        return {1: 0, 11: 1}.get(int(match.group(1))) if match else None

    def unit_name(self, image):
        return self.text(image, UNIT_NAME)

    def presets(self, image):
        return [{"label": self.text(image, label), "name": self.text(image, name), "set": self.text(image, button) == "セット"}
                for label, name, button in zip(PRESET_LABEL, PRESET_NAME, PRESET_SET)]

    def p_card(self, image):
        """Card id of the P slot on ユニット編成 (wing_unit_check, the W.I.N.G. formation reader); None unless accepted."""
        from agent.core.wing_unit_check import read_unit_cards
        try:
            tile = (read_unit_cards(image) or {}).get("P") or {}
        except Exception:
            return None
        return tile.get("card_id") if tile.get("status") == "accepted" and not tile.get("blank") else None

    def policy_choice(self, image):
        """The highlighted choice of 育成方針選択, from its description 「Vocalに関する…」."""
        match = re.match(r"(Vocal|Dance|Visual)に関する", self.text(image, POLICY_TEXT) or "")
        return match.group(1) if match else None

    def books(self, image):
        """Filled book slots (a Vo. bonus 「+N」 under the book) and whether the 「3つ選択」 prompt still shows."""
        filled = sum(bool(re.fullmatch(r"\+[0-9]{1,3}", self.text(image, box) or "")) for box in BOOK_VO)
        prompt = "選択してください" in (self.text(image, BOOK_PROMPT, 0.5) or "")
        return {"filled": filled, "prompt": prompt}

    def book_stats(self, image):
        """The four bonuses of each book slot ({vo, da, vi, me}; None per unread value)."""
        out = []
        for boxes in BOOK_STATS:
            values = {}
            for key, box in boxes.items():
                match = re.fullmatch(r"\+([0-9]{1,3})", self.text(image, box) or "")
                values[key] = int(match.group(1)) if match else None
            out.append(values)
        return out

    def picker(self, image):
        """Book picker: sort header and the 15 tiles (row, col, 評価値, grey, selected)."""
        tiles = []
        for r in range(PICKER_ROWS):
            for c in range(PICKER_COLS):
                x, y = PICKER_ORIGIN[0] + PICKER_PITCH * c, PICKER_ORIGIN[1] + PICKER_PITCH * r
                ex, ey, ew, eh = PICKER_EVAL
                bx, by, bw, bh = PICKER_BG
                cx, cy, cw, ch = PICKER_CHECK
                tiles.append({"r": r, "c": c, "eval": parse_int(self.text(image, (x + ex, y + ey, ew, eh))),
                              "grey": float(_patch(image, (x + bx, y + by, bw, bh)).mean()) < PICKER_BG_MIN,
                              "selected": check_pixels(image, (x + cx, y + cy, cw, ch)) >= PICKER_CHECK_PINK})
        return {"sort": self.text(image, PICKER_SORT), "order": self.text(image, PICKER_ORDER), "tiles": tiles}

    def picker_detail(self, image):
        """Title and Vo/Da/Vi/Me of the book last tapped in the picker."""
        return {"title": self.text(image, DETAIL_TITLE),
                "stats": {k: parse_int(self.text(image, box)) for k, box in DETAIL_STATS.items()}}

    def history(self, image):
        date = self.text(image, HISTORY_DATE)
        return {"date": date if date and re.fullmatch(r"[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}[0-9: ]*", date) else None,
                "call": self.text(image, HISTORY_CALL) == "呼び出し"}

    def items(self, image):
        return sum(self.text(image, box) == "アイテムを選択していません" for box in ITEM_SLOTS)

    def dialog(self, image, use_skip):
        before = parse_int(self.text(image, DIALOG_BEFORE))
        after = parse_int(self.text(image, DIALOG_AFTER))
        skip_left = None
        if use_skip and self.hit(NODE_DIALOG_SKIP, image):
            skip_left = parse_skip_left(self.text(image, DIALOG_SKIP_LEFT))
        return {"before": before, "after": after, "skip_left": skip_left}

    def recovery(self, image):
        """Recovery dialog: セットするスタミナ before/after, 必要スタミナ (have, need), owned and set count per soda."""
        owned = []
        for box in SODA_OWNED:
            match = re.fullmatch(r"[xX×]([0-9]{1,4})", self.text(image, box, 0.5) or "")
            owned.append(int(match.group(1)) if match else None)
        return {"before": parse_int(self.text(image, RECOVERY_SET[0])),
                "after": parse_int(self.text(image, RECOVERY_SET[1])),
                "required": parse_ratio(self.text(image, RECOVERY_REQUIRED)),
                "owned": owned, "counts": [parse_int(self.text(image, box, 0.5)) for box in SODA_COUNT]}


# ---------------------------------------------------------------- runtime
class WeeklyAutoRun(tc.TaskRun):
    SUCCESS = frozenset({"started"})
    HALT = tc.TaskRun.HALT | frozenset({"stopped"})

    def __init__(self, config, log):
        super().__init__(config, log)
        self.phase = "start"
        self.reason = None
        self.waits = 0
        self.taps = 0
        self.pending = None      # (count before the tap, expected count after it)
        self.pending_frames = 0
        self.after_frames = 0
        self.stop_after_cancel = None
        self.setup_taps = 0
        self.setup_pending = None   # (decision kind, value shown before it) while a same-page tap is awaited
        self.leaving = None         # (page, decision kind) while a page-advancing tap is awaited
        self.leave_frames = 0
        self.history_called = False
        self.history_refused = False   # 「呼び出し」 did not take (the set holds a book of the team's idol)
        self.omakase_called = False    # fallback: the book page's おまかせ編成
        self.book_frames = 0           # frames the slots stayed short after a source was applied
        self.preset_called = False
        self.setup_done = False
        self.unit_name = None       # read on the unit page when this run sets the row
        self.policy_done = False
        # 育成方針 to set: the panel's choice, or for "auto" the P card's specialty once read on the unit page
        self.policy_target = config.policy if config.policy in POLICY_CHOICES else ""
        self.p_card = None
        self.p_card_frames = 0
        self.loop_tapped = False    # the one 「ループする」 tap that sets it to the panel's ループ
        self.soda_plan = None       # (c30, c60, c100) planned on the recovery dialog's first read
        self.soda_base = None       # セットするスタミナ before any soda (= current stamina)
        self.soda_done = [0, 0, 0]  # confirmed 「+」 taps per soda
        self.soda_pending = None    # (added before the tap, expected after it, soda index)
        self.soda_taps = 0
        self.recovery_read = None   # the last recovery-dialog reading (logged with its キャンセル)
        self.book_plan = None       # 带书 「ノウハウ页指定」: three books (clean_book_plan)
        self.picker_done = {}       # plan index -> (row, col) of its confirmed tile
        self.picker_not = set()     # (row, col, plan index): a tapped tile that showed another book
        self.picker_pending = None  # ((row, col), plan index, select?) of the last tile tap
        self.picker_taps = 0
        self.picker_decided = False
        self.collect = None         # result collection: None → "tapped" → "end_ok" → "result_ok" → "collected"
        self.collect_frames = 0     # (mid-run: "tapped" → "running" after キャンセル)
        self.running_left = None    # 残り時間 read mid-run, e.g. "56:44"
        self.go_home = 0            # SC.Common.GoHome hand-overs after the result OK

    @property
    def active(self):
        return self.phase != "finished"

    def summary(self):
        return {**super().summary(), "reason": self.reason, "taps": self.taps,
                "collected": self.collect == "collected", "p_card": self.p_card, "running_left": self.running_left}


class WeeklyAutoRuntime:
    """One runtime per launcher process / AgentServer. ``authority`` (launcher only) grants the consuming press."""
    KIND = KIND
    RUN = WeeklyAutoRun
    CONFIG = WeeklyAutoConfig

    def __init__(self, sink=None, authority=None, log_root=tc.LOG_ROOT, now=None, vision=None, expect_p_card=None,
                 book_plan=None, clock=time.monotonic):
        if vision is None:
            from agent.core.task_custom import Vision as vision
        self.sink, self.authority, self.vision = sink, authority, vision
        # Auto 自动编队 (tools/ui_engine.py): the P card the prep just edited into the unit; the Auto unit page must
        # show it, else the run stops before 次へ.
        tc.require(expect_p_card is None or isinstance(expect_p_card, str) and expect_p_card.isdigit(),
                   "expect_p_card 须为卡 ID")
        self.expect_p_card = expect_p_card
        # 带书 「ノウハウ页指定」: None = read the UI profile at begin (tests and replays pass the books)
        self.book_plan = clean_book_plan(list(book_plan)) if book_plan is not None else None
        self.now = now or tc.utc_now
        self.log = tc.JsonlLog(KIND, log_root, self.now)
        self.run = None
        self._cache = (None, None)
        self._unsent = None   # (kind, run state before deciding it) until that tap's node runs
        self._context = None
        self._frame = None   # (reader, image) of the frame being decided
        self.clock = clock
        self._slow = 0        # consecutive click decisions re-made for being too slow

    # -- helpers
    @property
    def SPEND_ACTION(self):  # noqa: N802 (TaskRuntime-compatible name)
        return self.run.config.spend_action if self.run is not None else START_ACTION

    def can_spend(self):
        return self.authority is not None and self.authority.can_spend(self.SPEND_ACTION)

    def _log(self, record):
        try:
            self.log.append(record)
            return True
        except Exception:
            LOGGER.exception("weekly auto log write failed")
            return False

    def report(self):
        run = self.run
        if run is None or run.reported:
            return
        run.reported = True
        payload = run.summary()
        if self.sink is not None:
            self.sink.add(KIND, payload)
        LOGGER.info("%s: %s", KIND, payload)

    def stop(self, reason, outcome="stopped"):
        run = self.run
        if run is not None and not run.finished:
            run.reason = reason
            run.finish(outcome, reason)
            self._log({"event": outcome, "reason": reason})
        return Decision("none", reason)

    # -- actions
    def begin(self, context, p):
        merged = {}
        try:
            for name in p.get("config_nodes", []):
                data = context.get_node_data(name) or {}
                merged.update(data.get("attach") or {})
            config = WeeklyAutoConfig.from_params(merged)
        except Exception as exc:
            self.run = WeeklyAutoRun(WeeklyAutoConfig(), self.log)
            self.run.reason = "config_invalid"
            self.run.finish("config_invalid", str(exc)[:200])
            self.report()
            return False
        self.run = WeeklyAutoRun(config, self.log)
        self._cache = (None, None)
        self._unsent = None   # (kind, run state before deciding it) until that tap's node runs
        self._log({"event": "begin", **config.to_dict(), "expect_p_card": self.expect_p_card})
        if config.team == "auto" and self.expect_p_card is None:
            self.stop("auto_team_not_prepared")   # 自动编队 runs only after the UI engine's team-edit prep
            self.report()
            return False
        if config.books == "target" and config.expect_scenario == "STEP":
            self.run.book_plan = self.book_plan or load_book_plan()
            if self.run.book_plan is None:
                self.stop("target_books_not_set")   # the ノウハウ page has not saved three books for Auto
                self.report()
                return False
            self._log({"event": "book_plan", "books": [dict(b) for b in self.run.book_plan]})
        if config.loop and config.soda_cap > stamina_item_cap():
            self.stop("soda_cap_over_policy")   # project_policy.json stamina_items bounds the panel's ソーダ上限
            self.report()
            return False
        return True

    def finish_action(self, context, p):
        run = self.run
        self.report()
        return bool(run is not None and run.succeeded)

    def abort(self, context, p):
        run = self.run
        if run is not None and not run.finished:
            if run.phase == "awaiting_result":
                return self.spend_error(context, p)
            self.stop("unknown_or_unhandled_screen", "timeout")
        self.report()
        return False

    def spend_error(self, context, p):
        run = self.run
        if run is None:
            return False
        outcome = self.authority.last_outcome(self.SPEND_ACTION) if self.authority is not None else None
        if outcome == "denied":
            self.stop("click_denied_before_send", "spend_refused")
        else:
            self.stop("result_not_observed:" + str(outcome), "spend_uncertain")
        self.report()
        return False

    # -- recognitions
    def state(self, context, image, p):
        run, want = self.run, p.get("is")
        if run is None:
            return False
        if want == "halted":
            return run.halted
        if want == "finished":
            return run.succeeded
        return False

    def check(self, context, image, p):
        """``stage`` = "observe" (decide once, never hits) or a decision kind (hits iff this frame's decision)."""
        if self.run is None:
            return False
        decision = self.decide(context, image)
        stage = p.get("stage")
        if stage == "observe" or decision.kind != stage:
            return False
        # The node of this decision runs now: the next frame is decided afresh even when its pixels are identical
        # (a tap that changed nothing must count as an unchanged frame, never repeat the cached tap).
        self._cache = (None, None)
        self._unsent = None
        return True

    # -- the one decision per frame
    def decide(self, context, image):
        digest = tc.image_digest(image)
        if self._cache[0] == digest:
            return self._cache[1]
        self._context = context
        run = self.run
        if run is not None and self._unsent is not None and not run.finished:
            # Live 2026-10-31: 次へ was decided on a frame where its node's guards did not match, so the tap never ran,
            # yet the run waited for the page to leave and stopped (unit_next_page_not_left). Undo what deciding an
            # unsent tap changed (phase, awaited page or count) before this frame is decided.
            kind, state = self._unsent
            run.__dict__.update(state)
            self._log({"event": "tap_not_sent", "kind": kind})
        self._unsent = None
        state = self._run_state(run)
        started = self.clock()
        decision = self._decide(Reader(self.vision(context)), image)
        if (run is not None and decision.kind in CLICK_NODES and not run.finished
                and self.clock() - started > DECISION_STALE_S):
            self._slow += 1
            if self._slow > MAX_SLOW_DECISIONS:
                decision = self.stop("decision_too_slow")
            else:
                reads = {k: getattr(run, k) for k in KEEP_READS}
                run.__dict__.update(state)
                run.__dict__.update(reads)
                self._log({"event": "decision_too_slow", "kind": decision.kind,
                           "seconds": round(self.clock() - started, 2)})
                return Decision("wait", "decision_too_slow")   # not cached: the next frame is decided afresh
        elif decision.kind in CLICK_NODES:
            self._slow = 0
        self._cache = (digest, decision)
        if run is not None:
            run.note({"phase": run.phase, "decision": decision.kind, "reason": decision.reason})
            if decision.kind in CLICK_NODES and not run.finished:
                self._unsent = (decision.kind, state)
        return decision

    @staticmethod
    def _run_state(run):
        """The run's decision state (not its config, log or diagnostics) to restore when a decided tap never runs."""
        if run is None:
            return None
        return {k: copy.deepcopy(v) for k, v in vars(run).items() if k not in ("config", "log", "notes", "decisions")}

    def _wait(self, reason, limit=MAX_WAIT_FRAMES, outcome="stopped"):
        run = self.run
        run.waits += 1
        if run.waits > limit:
            return self.stop(reason, outcome)
        return Decision("wait", reason)

    def _node_enabled(self, kind):
        """Whether the Click node of ``kind`` is enabled in this run (the shipped resource keeps them all disabled; only
        an explicit launcher/replay override enables them)."""
        try:
            data = self._context.get_node_data(CLICK_NODES[kind]) or {}
        except Exception:
            return False
        return data.get("enabled") is True

    def _go(self, kind, phase=None, reason=""):
        """A click decision; stops instead when its Click node is not enabled (no input in this run)."""
        run = self.run
        if not self._node_enabled(kind):
            return self.stop(reason if kind in ("cancel", "recovery_cancel") and reason else f"input_disabled:{kind}")
        anchor = TARGET_ANCHORS.get(kind)
        if anchor is not None and self._frame is not None and not self._frame[0].hit(anchor, self._frame[1]):
            if run.phase == "policy" and kind != "policy_cancel" and run.waits >= MAX_WAIT_FRAMES:
                return self._policy_cancel(f"{kind}_target_unread")   # never leave 育成方針選択 open
            if run.phase == "soda" and run.waits >= MAX_WAIT_FRAMES:
                return self._recovery_cancel(f"{kind}_target_unread")   # nor the recovery dialog with sodas set
            return self._wait(f"{kind}_target_unread")
        if phase is not None:
            run.phase, run.waits = phase, 0
        return Decision(kind, reason)

    def _decide(self, reader, image):
        run = self.run
        if run is None or run.finished:
            return Decision("none")
        self._frame = (reader, image)
        page = reader.page(image)
        if run.phase == "awaiting_result":
            return self._after_start(reader, image, page)
        if run.phase == "collect":
            return self._collect(reader, image, page)
        if page == "loading":
            return Decision("none", "loading")   # [JumpBack]SC.Loading.Wait
        if run.leaving is not None:
            left, kind = run.leaving
            if page in (left, "unknown"):
                run.leave_frames += 1
                # 2026-11-21 live: every 設定履歴 set held 霧子's book, 霧子 was in ユニット20, so 「呼び出し」 was greyed and
                # the page never left. Back to the book page; おまかせ編成 fills it there.
                if kind == "history_call" and page == "w_history" and run.leave_frames > HISTORY_CALL_FRAMES:
                    run.leaving, run.leave_frames = None, 0
                    run.history_called, run.history_refused = False, True
                    self._log({"event": "history_call_refused"})
                    return self._advance("history_back", "setup_books", page)
                if run.leave_frames > MAX_LEAVE_FRAMES:
                    return self.stop(f"{kind}_page_not_left")
                return Decision("wait", f"{kind}_leaving")
            run.leaving, run.leave_frames = None, 0
        if page.startswith("w_"):
            return self._wizard(reader, image, page)
        handler = {"home": self._home, "select": self._select, "schedule": self._schedule,
                   "dialog": self._dialog, "recovery": self._recovery, "policy": self._policy}.get(page)
        if handler is None:
            return self._wait("unknown_screen")
        return handler(reader, image)

    def _home(self, reader, image):
        run = self.run
        home = reader.home(image)
        run.note({"home": home})
        if home["running"]:
            if run.collect is None:      # one tap: the result of a finished Auto (mid-run: see _collect)
                decision = self._go("home_running", "collect")
                if decision.kind == "home_running":
                    run.collect, run.collect_frames = "tapped", 0
                return decision
            return self.stop("auto_produce_running")
        if run.phase != "start":
            return self._wait(f"home_after_{run.phase}")
        if not home["ready"] or home["stamina"] is None:
            return self._wait("home_unread")
        stamina = home["stamina"][0]
        if stamina + run.config.soda_cap < run.config.stamina:   # soda_cap is 0 without ループ
            return self.stop(f"stamina_short_{stamina}")
        return self._go("home_open", "to_select")

    def _select(self, reader, image):
        run = self.run
        if run.phase in ("start", "to_select"):
            return self._go("open_auto", "to_schedule")
        if run.phase == "to_schedule":
            return self._wait("auto_schedule_not_shown", limit=MAX_WAIT_FRAMES * 2)
        return self.stop(f"select_after_{run.phase}")

    def _schedule(self, reader, image):
        run = self.run
        if run.phase in ("cancelled", "policy_cancelled"):
            return self.stop(run.stop_after_cancel or "dialog_cancelled")
        if run.phase == "to_dialog":
            return self._wait("dialog_not_shown")
        if run.phase not in ("start", "to_schedule", "schedule", "policy_set"):
            return self.stop(f"schedule_after_{run.phase}")
        run.phase = "schedule"
        state = reader.schedule(image)
        run.note({"schedule": state})
        if state["row"] == "empty" and run.config.setup == "maa" and not run.setup_done:
            self._log({"event": "setup_begin", "scenario": run.config.expect_scenario, "unit": run.config.unit})
            return self._advance("row_set", "setup_select", "schedule")
        reasons = schedule_reasons(run.config, state, run.setup_done, run.unit_name)
        if reasons in (["loop_on"], ["loop_off"]):
            kind = "loop_on" if run.config.loop else "loop_off"
            if run.loop_tapped:
                run.pending_frames += 1
                if run.pending_frames > MAX_PENDING_FRAMES:
                    return self.stop("loop_not_" + kind[5:])
                return Decision("wait", "loop_pending")
            decision = self._go(kind)
            if decision.kind == kind:
                run.loop_tapped, run.pending_frames = True, 0
            return decision
        if reasons:
            if reasons in (["row1_unread"], ["count_unread"], ["loop_unread"]):
                return self._wait(reasons[0])
            return self.stop(",".join(reasons))
        policy = run.policy_target   # "auto" on a row this run did not set: no P card read, the row's is kept
        if policy and state.get("policy") != policy:
            if run.policy_done:
                return self.stop(f"policy_not_applied_{state.get('policy')}")
            if state.get("policy") not in POLICY_CHOICES:
                return self._wait("policy_unread")
            return self._advance("policy_open", "policy", "schedule")
        count = state["count"]
        if run.pending is not None:
            before, expected = run.pending
            if count == expected:
                run.pending, run.pending_frames = None, 0
            elif count == before:
                run.pending_frames += 1
                if run.pending_frames > MAX_PENDING_FRAMES:
                    return self.stop("count_not_changed")
                return Decision("wait", "count_pending")
            else:
                return self.stop(f"count_unexpected_{before}_{count}")
        run.waits = 0
        step = count_step(count, run.config.runs)
        if step is not None:
            if run.taps >= MAX_TAPS:
                return self.stop("count_tap_budget")
            decision = self._go(step)
            if decision.kind == step:
                run.taps += 1
                run.pending = (count, count + (1 if step == "plus" else -1))
                run.pending_frames = 0
            return decision
        reasons = decide_totals(run.config, state.get("total_runs"), state.get("total_stamina"))
        if reasons:
            return self.stop(",".join(reasons))
        self._log({"event": "schedule_ready", "count": count, "scenario": state.get("scenario"),
                   "unit_name": state.get("unit_name"), "policy": state.get("policy")})
        return self._go("decide", "to_dialog")

    # -- 育成方針選択 (config.policy set and the row shows another one)
    def _policy(self, reader, image):
        run = self.run
        if run.phase != "policy":
            return self.stop(f"policy_dialog_after_{run.phase}")
        if run.stop_after_cancel:   # a decided キャンセル whose button was not readable yet
            return self._policy_cancel(run.stop_after_cancel)
        chosen = reader.policy_choice(image)
        run.note({"policy_choice": chosen})
        if run.setup_pending is not None:
            kind, before = run.setup_pending
            if chosen == before:
                run.pending_frames += 1
                if run.pending_frames > MAX_PENDING_FRAMES:
                    return self._policy_cancel(f"{kind}_not_changed")
                return Decision("wait", f"{kind}_pending")
            run.setup_pending, run.pending_frames = None, 0
        if chosen is None:
            run.waits += 1
            return self._policy_cancel("policy_unread") if run.waits > MAX_WAIT_FRAMES else Decision("wait", "policy_unread")
        if chosen == run.policy_target:
            decision = self._advance("policy_set", "policy_set", "policy")
            if decision.kind == "policy_set":
                run.policy_done = True
                self._log({"event": "policy_set", "policy": chosen})
            return decision
        if run.setup_taps >= MAX_SETUP_TAPS:
            return self._policy_cancel("setup_tap_budget")
        return self._tap("policy_" + run.policy_target.lower(), chosen)

    def _policy_cancel(self, reason):
        """Close 育成方針選択 unchanged with キャンセル, then stop on the schedule."""
        self.run.stop_after_cancel = reason
        self.run.setup_pending = None
        return self._advance("policy_cancel", "policy_cancelled", "policy")

    # -- setup wizard (setup = "maa", an empty オート1 only)
    def _pending(self, shown):
        """While the change of the last setup tap is not visible: wait (bounded); once it shows, clear it."""
        run = self.run
        if run.setup_pending is None:
            return None
        kind, before = run.setup_pending
        if shown == before:
            run.pending_frames += 1
            if run.pending_frames > MAX_PENDING_FRAMES:
                return self.stop(f"{kind}_not_changed")
            return Decision("wait", f"{kind}_pending")
        run.setup_pending, run.pending_frames = None, 0
        return None

    def _tap(self, kind, before):
        """A same-page tap (scenario tab, unit tab): its change is awaited through ``_pending``."""
        run = self.run
        if run.setup_taps >= MAX_SETUP_TAPS:
            return self.stop(f"setup_tap_budget:{kind}")
        decision = self._go(kind)
        if decision.kind == kind:
            run.setup_taps += 1
            run.setup_pending, run.pending_frames = (kind, before), 0
        return decision

    def _advance(self, kind, phase, page):
        """A page-advancing tap: until another page shows, frames of ``page`` (or unknown ones) only wait."""
        decision = self._go(kind, phase)
        if decision.kind == kind:
            self.run.leaving, self.run.leave_frames = (page, kind), 0
        return decision

    def _wizard(self, reader, image, page):
        run = self.run
        config = run.config
        if page == "w_tutorial":
            return self.stop("auto_tutorial_shown")
        if config.setup != "maa":
            return self.stop("row1_setup_off")
        if page == "w_select" and run.phase == "to_schedule" and not run.setup_done:
            # 10-22 demo (frame 101): with no Auto row ever set, 「オートプロデュース」 opens the wizard at once.
            self._log({"event": "setup_begin", "scenario": config.expect_scenario, "unit": config.unit, "direct": True})
            run.phase = "setup_select"
        expected = {"w_select": "setup_select", "w_unit": "setup_unit", "w_books": "setup_books",
                    "w_history": "setup_history", "w_preset": "setup_preset", "w_item": "setup_item",
                    "w_picker": "setup_picker"}[page]
        if run.phase != expected:
            return self.stop(f"{page}_after_{run.phase}")
        if page == "w_select":
            caption = reader.caption(image)
            run.note({"caption": caption})
            pending = self._pending(caption)
            if pending is not None:
                return pending
            if caption is None:
                return self._wait("scenario_unread")
            target = config.expect_scenario
            if caption == SETUP_CAPTIONS[target]:
                return self._advance("scenario_next", "setup_unit", page)
            shown = next((k for k, v in SETUP_CAPTIONS.items() if v == caption), None)
            if shown is None:
                return self.stop("scenario_unknown:" + caption)
            if target == "STEP":
                return self._tap("tab_special", caption)
            if shown == "STEP":
                return self._tap("tab_train", caption)
            if TRAIN_ORDER.index(shown) > TRAIN_ORDER.index(target):
                return self.stop("scenario_above:" + caption)   # the list's upward tap is unsampled
            return self._tap("scenario_down", caption)
        if page == "w_unit":
            shown, tab_page = reader.unit(image), reader.unit_tab_page(image)
            run.note({"unit": shown, "tab_page": tab_page})
            pending = self._pending((shown, tab_page))
            if pending is not None:
                return pending
            if shown is None:
                return self._wait("unit_unread")
            if shown == config.unit:
                run.unit_name = reader.unit_name(image)
                if run.unit_name is None:
                    return self._wait("unit_name_unread")
                read_p = config.policy == "auto" or self.expect_p_card is not None
                if read_p and run.p_card is None and run.p_card_frames < MAX_WAIT_FRAMES:
                    run.p_card = reader.p_card(image)
                    if run.p_card is None:
                        run.p_card_frames += 1
                        return Decision("wait", "p_card_unread")
                    if self.expect_p_card is not None and run.p_card != self.expect_p_card:
                        return self.stop(f"auto_team_p_card_mismatch:{run.p_card}")
                    if config.policy == "auto":
                        run.policy_target = policy_for_card(run.p_card) or ""
                        self._log({"event": "policy_auto", "p_card": run.p_card, "policy": run.policy_target or None})
                elif self.expect_p_card is not None and run.p_card is None:
                    return self.stop("auto_team_p_card_unread")
                elif config.policy == "auto" and run.p_card is None and run.p_card_frames == MAX_WAIT_FRAMES:
                    run.p_card_frames += 1
                    self._log({"event": "policy_auto", "p_card": None, "policy": None})   # unread: leave the row's
                self._log({"event": "setup_unit", "unit": shown, "unit_name": run.unit_name})
                return self._advance("unit_next", "setup_books" if config.expect_scenario == "STEP" else "setup_item",
                                     page)
            if tab_page is None:
                return self._wait("unit_tabs_unread")
            want = (config.unit - 1) // UNITS_PER_PAGE
            if tab_page != want:
                return self._tap("unit_page_next" if want > tab_page else "unit_page_prev", (shown, tab_page))
            return self._tap(f"unit_tab_{(config.unit - 1) % UNITS_PER_PAGE + 1}", (shown, tab_page))
        if page == "w_books":
            books = reader.books(image)
            run.note({"books": books})
            called = run.history_called or run.preset_called or run.picker_decided or run.omakase_called
            # Only the configured source fills the slots: books already there on arrival are replaced, never used.
            if called and books["filled"] == 3 and not books["prompt"]:
                if run.picker_decided:
                    shown = reader.book_stats(image)
                    run.note({"book_stats": shown})
                    key = lambda b: tuple(b[k] for k in STATS)   # noqa: E731
                    if sorted(map(key, shown), key=str) != sorted(map(key, run.book_plan), key=str):
                        if any(None in key(b) for b in shown):
                            return self._wait("book_stats_unread", limit=MAX_PENDING_FRAMES)
                        return self.stop("target_books_mismatch")
                return self._advance("book_next", "setup_item", page)
            if called and (run.omakase_called or run.picker_decided):
                return self._wait(f"books_not_filled_{books['filled']}", limit=MAX_PENDING_FRAMES)
            if called and run.book_frames < BOOK_FILL_FRAMES:
                run.book_frames += 1
                return Decision("wait", f"books_filling_{books['filled']}")
            if (called or run.history_refused) and config.books != "target":
                # Fallback (a book that cannot be set must not block): the game's おまかせ編成 picks three
                # books the team allows.
                run.history_called = run.preset_called = False
                self._log({"event": "books_omakase", "filled": books["filled"],
                           "source": "history_refused" if run.history_refused else config.books})
                decision = self._go("book_omakase", "setup_books")
                run.omakase_called = decision.kind == "book_omakase"
                run.book_frames = 0
                return decision
            if config.books == "target":
                if books["filled"]:
                    return self.stop(f"books_preset_{books['filled']}")   # the picker's ✓ would start from them
                return self._advance("book_slot1", "setup_picker", page)
            if config.books == "history":
                return self._advance("book_history", "setup_history", page)
            return self._advance("book_preset", "setup_preset", page)
        if page == "w_picker":
            return self._picker(reader, image)
        if page == "w_preset":
            slot = int(config.books[-1])
            presets = reader.presets(image)
            run.note({"presets": presets})
            preset = presets[slot - 1]
            if preset["label"] != f"プリセット{slot}" or not preset["set"]:
                return self._wait("preset_unread")
            self._log({"event": "setup_books", "preset": slot, "name": preset["name"]})
            decision = self._advance(f"preset_set_{slot}", "setup_books", page)
            run.preset_called = decision.kind == f"preset_set_{slot}"
            return decision
        if page == "w_history":
            if run.history_refused:
                return self._advance("history_back", "setup_books", page)
            history = reader.history(image)
            run.note({"history": history})
            if history["date"] is None or not history["call"]:
                return self._wait("history_unread")
            self._log({"event": "setup_books", "history": history["date"]})
            decision = self._advance("history_call", "setup_books", page)
            run.history_called = decision.kind == "history_call"
            return decision
        # w_item: Auto restricts every item (制限中); both slots must stay empty.
        empty = reader.items(image)
        run.note({"items_empty": empty})
        if empty != 2:
            return self._wait(f"items_set_{2 - empty}")
        decision = self._advance("item_decide", "to_schedule", page)
        if decision.kind == "item_decide":
            run.setup_done = True
            self._log({"event": "setup_done", "scenario": config.expect_scenario, "unit": config.unit})
        return decision

    def _picker(self, reader, image):
        """Book picker for 带书 「ノウハウ页指定」: select the plan's three books among the first screen's tiles."""
        run, plan = self.run, self.run.book_plan
        view = reader.picker(image)
        run.note({"picker": view})
        if view["sort"] is None or view["order"] is None or any(t["eval"] is None for t in view["tiles"]):
            return self._wait("picker_unread")
        if view["sort"] != "ノウハウ評価値順" or "降順" not in view["order"]:
            return self.stop(f"picker_sort:{view['sort']}/{view['order']}")
        tiles = {(t["r"], t["c"]): t for t in view["tiles"]}
        if run.picker_pending is not None:
            (r, c), index, select = run.picker_pending
            if tiles[(r, c)]["selected"] != select:
                run.pending_frames += 1
                if run.pending_frames > MAX_PENDING_FRAMES:
                    return self.stop(f"picker_tile_{r}{c}_not_changed")
                return Decision("wait", "picker_pending")
            if select:
                detail = reader.picker_detail(image)
                run.note({"detail": detail})
                if not book_matches(plan[index], detail["title"], detail["stats"]):
                    if run.pending_frames < MAX_PENDING_FRAMES:   # the panel may follow the ✓ a frame later
                        run.pending_frames += 1
                        return Decision("wait", "picker_detail_pending")
                    run.picker_not.add((r, c, index))   # same 評価値, another book: take the ✓ off again
                    self._log({"event": "picker_other_book", "tile": [r, c], "plan": index, "detail": detail})
                    run.picker_pending = None
                    return self._picker_tap(r, c, index, False)
                run.picker_done[index] = (r, c)
                self._log({"event": "picker_book", "plan": index, "tile": [r, c], "title": detail["title"]})
            run.picker_pending, run.pending_frames = None, 0
        selected = {key for key, t in tiles.items() if t["selected"]}
        if selected != set(run.picker_done.values()):
            return self.stop("picker_selection_unexpected")
        for index, book in enumerate(plan):
            if index in run.picker_done:
                continue
            same = [t for t in view["tiles"] if t["eval"] == book["eval"] and not t["selected"]
                    and (t["r"], t["c"], index) not in run.picker_not]
            free = [t for t in same if not t["grey"]]
            if not free:
                return self.stop(f"target_book_{'blocked' if same else 'not_visible'}_{index + 1}")
            return self._picker_tap(free[0]["r"], free[0]["c"], index, True)
        decision = self._advance("picker_decide", "setup_books", "w_picker")
        run.picker_decided = decision.kind == "picker_decide"
        return decision

    def _picker_tap(self, r, c, index, select):
        run = self.run
        if run.picker_taps >= MAX_PICKER_TAPS:
            return self.stop("picker_tap_budget")
        kind = f"picker_tile_{r}_{c}"
        decision = self._go(kind)
        if decision.kind == kind:
            run.picker_taps += 1
            run.picker_pending, run.pending_frames = ((r, c), index, select), 0
        return decision

    def _dialog(self, reader, image):
        run = self.run
        if run.phase == "cancelled":
            return self._wait(run.stop_after_cancel or "dialog_cancelled")
        if run.phase != "to_dialog":
            return self.stop(f"dialog_after_{run.phase}")
        config = run.config
        if config.loop:   # ループ with 750+ stamina on hand: this plain dialog is unsampled
            run.stop_after_cancel = "loop_plain_dialog"
            self._log({"event": "cancel", "reason": run.stop_after_cancel})
            return self._go("cancel", "cancelled", run.stop_after_cancel)
        values = reader.dialog(image, config.use_skip)
        run.note({"dialog": values})
        reasons = dialog_reasons(config, values["before"], values["after"], values["skip_left"])
        if reasons == ["dialog_stamina_unread"] and run.waits < 2:
            run.waits += 1
            return Decision("wait", reasons[0])
        if config.use_skip and not reader.hit(NODE_DIALOG_SKIP, image):
            reasons.append("skip_button_missing")
        intent = {"action": config.spend_action, "runs": config.runs, "stamina_before": values["before"],
                  "stamina_after": values["after"], "skip_left": values["skip_left"]}
        if not reasons:
            press = "skip" if config.use_skip else "start"
            if not self._node_enabled(press):
                reasons.append("input_disabled:" + press)
            elif not self.can_spend():
                reasons.append("spend_not_enabled")
            elif not self._log({"event": "intent", **intent}):
                reasons.append("log_write_failed")
            else:
                refusal = self.authority.authorize(config.spend_action, image, APPROVER)
                if refusal:
                    self._log({"event": "refused", **intent, "reason": refusal})
                    reasons.append("spend_grant_refused:" + str(refusal))
        if reasons:
            run.stop_after_cancel = ",".join(reasons)
            self._log({"event": "cancel", **intent, "reason": run.stop_after_cancel})
            return self._go("cancel", "cancelled", run.stop_after_cancel)
        run.intent = intent
        return self._go("skip" if config.use_skip else "start", "awaiting_result")

    # -- recovery dialog (stamina below the need: always for ループ)
    def _recovery(self, reader, image):
        run = self.run
        if run.phase == "cancelled":
            return self._wait(run.stop_after_cancel or "dialog_cancelled")
        if run.phase not in ("to_dialog", "soda"):
            return self.stop(f"recovery_after_{run.phase}")
        config = run.config
        values = reader.recovery(image)
        run.note({"recovery": values})
        run.recovery_read = values
        if not config.loop:   # stamina below 30 × runs: only ループ may set recovery items
            return self._recovery_cancel(f"stamina_short_{values['before']}")
        if values["before"] is None or values["after"] is None or values["required"] is None:
            run.waits += 1
            if run.waits > MAX_PENDING_FRAMES:
                return self._recovery_cancel("recovery_unread")
            return Decision("wait", "recovery_unread")
        if run.phase == "to_dialog":
            before = values["before"]
            if values["after"] != before:
                return self._recovery_cancel(f"soda_preset_{values['after'] - before}")
            if values["required"] != (before, config.stamina):
                return self._recovery_cancel("required_stamina_{}_{}".format(*values["required"]))
            cap = min(config.soda_cap, stamina_item_cap())
            if config.stamina - before > cap:
                return self._recovery_cancel(f"soda_cap_short_{before}")
            plan = plan_sodas(config.stamina - before, values["owned"], cap)
            if plan is None:
                return self._recovery_cancel("soda_owned_unread" if None in values["owned"] else "soda_short")
            run.soda_plan, run.soda_base, run.phase, run.waits = plan, before, "soda", 0
            self._log({"event": "soda_plan", "stamina": before, "need": config.stamina - before, "cap": cap,
                       "plan": dict(zip(SODA_SIZES, plan)), "owned": values["owned"]})
        if values["before"] != run.soda_base:
            return self._recovery_cancel(f"stamina_changed_{run.soda_base}_{values['before']}")
        added = values["after"] - run.soda_base
        if run.soda_pending is not None:
            shown, expected, index = run.soda_pending
            if added == expected:
                run.soda_pending, run.pending_frames, run.waits = None, 0, 0
                run.soda_done[index] += 1
            elif added == shown:
                run.pending_frames += 1
                if run.pending_frames > MAX_PENDING_FRAMES:
                    return self._recovery_cancel(f"soda_{SODA_SIZES[index]}_not_changed")
                return Decision("wait", "soda_pending")
            else:
                return self._recovery_cancel(f"soda_unexpected_{shown}_{added}")
        done = run.soda_done
        if added != sum(c * size for c, size in zip(done, SODA_SIZES)):
            return self._recovery_cancel(f"soda_total_{added}")
        if any(count is not None and count != c for count, c in zip(values["counts"], done)):
            return self._recovery_cancel("soda_count_mismatch")
        index = next((i for i, (c, p) in enumerate(zip(done, run.soda_plan)) if c < p), None)
        if index is not None:
            kind = f"soda_{SODA_SIZES[index]}"
            if run.soda_taps >= MAX_SODA_TAPS:
                return self._recovery_cancel("soda_tap_budget")
            if not self._node_enabled(kind):
                return self._recovery_cancel("input_disabled:" + kind)
            decision = self._go(kind)
            if decision.kind == kind:
                run.soda_taps += 1
                run.soda_pending, run.pending_frames = (added, added + SODA_SIZES[index], index), 0
            return decision
        plan_total = sum(c * size for c, size in zip(run.soda_plan, SODA_SIZES))
        reasons = recovery_reasons(config, values, plan_total)
        if values["required"][0] < config.stamina:
            reasons.append(f"stamina_still_short_{values['required'][0]}")
        intent = {"action": config.spend_action, "runs": AUTO_RUN_LIMIT, "stamina_before": run.soda_base,
                  "stamina_set": values["after"], "sodas": dict(zip(SODA_SIZES, run.soda_plan))}
        if not reasons:
            if not self._node_enabled("recovery_start"):
                reasons.append("input_disabled:recovery_start")
            elif not self.can_spend():
                reasons.append("spend_not_enabled")
            elif not self._log({"event": "intent", **intent}):
                reasons.append("log_write_failed")
            else:
                refusal = self.authority.authorize(config.spend_action, image, APPROVER_RECOVERY)
                if refusal:
                    self._log({"event": "refused", **intent, "reason": refusal})
                    reasons.append("spend_grant_refused:" + str(refusal))
        if reasons:
            return self._recovery_cancel(",".join(reasons))
        run.intent = intent
        return self._go("recovery_start", "awaiting_result")

    def _recovery_cancel(self, reason):
        """キャンセル of the recovery dialog (nothing is consumed before プロデュース開始), then stop with ``reason``."""
        run = self.run
        run.stop_after_cancel = reason
        self._log({"event": "cancel", "dialog": "recovery", "reason": reason, "soda_plan": run.soda_plan,
                   "soda_done": run.soda_done, "read": run.recovery_read})
        return self._go("recovery_cancel", "cancelled", reason)

    def _collect(self, reader, image, page):
        """Collect a finished Auto: 終了 OK, リザルト OK, then Home 「プロデュース」 (the run then starts as usual)."""
        run = self.run
        run.collect_frames += 1
        step = run.collect
        if page == "end" and step == "tapped":
            decision = self._go("end_ok")
            if decision.kind == "end_ok":
                run.collect, run.collect_frames = "end_ok", 0
            return decision
        if page == "running" and step == "tapped":
            left = reader.running_left(image)
            decision = self._go("running_cancel")
            if decision.kind == "running_cancel":
                run.collect, run.collect_frames, run.running_left = "running", 0, left
                self._log({"event": "auto_running", "left": left})
            return decision
        if page == "result" and step in ("tapped", "end_ok"):
            decision = self._go("result_ok")
            if decision.kind == "result_ok":
                run.collect, run.collect_frames = "result_ok", 0
            return decision
        if page == "home":
            home = reader.home(image)
            run.note({"home": home})
            if step == "result_ok" and home["ready"]:
                run.collect, run.phase, run.waits = "collected", "start", 0
                self._log({"event": "collected", "home_stamina": home["stamina"]})
                return self._home(reader, image)
            if step == "running" and home["running"]:
                return self.stop("auto_produce_running")   # not finished yet: nothing to collect
            if step == "tapped" and home["running"] and run.collect_frames > MAX_WAIT_FRAMES:
                return self.stop("collect_not_opened")      # the tap opened nothing: never tap again
        if step == "result_ok" and page == "unknown" and run.collect_frames >= 2:
            if run.go_home >= MAX_GO_HOME:
                return self.stop("collect_home_not_reached")
            run.go_home += 1
            run.collect_frames = 0
            return Decision("go_home", "after_result")      # [JumpBack]SC.WeeklyProduce.Collect.GoHome
        if run.collect_frames > MAX_COLLECT_FRAMES:
            return self.stop(f"collect_{step}_timeout")
        return Decision("wait", f"collect_{step}")

    def _after_start(self, reader, image, page):
        run = self.run
        run.after_frames += 1
        if page == "home":
            home = reader.home(image)
            run.note({"home": home})
            if home["running"]:
                self._log({"event": "started", **(run.intent or {}), "home_stamina": home["stamina"]})
                run.reason = "started"
                run.finish("started")
                return Decision("none", "started")
        if run.after_frames > MAX_RESULT_FRAMES:
            self._log({"event": "uncertain", **(run.intent or {}), "reason": "running_not_observed"})
            return self.stop("running_not_observed", "spend_uncertain")
        return Decision("wait", "awaiting_result")


def registrations(runtime):
    """({name: recognition}, {name: action}) for the launcher (tools/run_tasks.py) and the AgentServer."""
    from agent.core.task_custom import RuntimeAction, RuntimeRecognition

    return ({"SC.WeeklyProduce.Check": RuntimeRecognition(runtime, "check"),
             "SC.WeeklyProduce.State": RuntimeRecognition(runtime, "state")},
            {"SC.WeeklyProduce.Begin": RuntimeAction(runtime, "begin"),
             "SC.WeeklyProduce.Finish": RuntimeAction(runtime, "finish_action"),
             "SC.WeeklyProduce.Abort": RuntimeAction(runtime, "abort"),
             "SC.WeeklyProduce.SpendError": RuntimeAction(runtime, "spend_error")})
