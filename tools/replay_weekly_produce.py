#!/usr/bin/env python3
"""周常官方 Auto Produce（WeeklyProduce）的离线回放：真实 Maa Tasker + 私有原生帧 + 模拟点击。

只回放 dev_assets/private 下的 1280x720 截图（2026-10-22 用户手动示范 captures-20261022-110140Z）并记录点击；
绝不连接浏览器、宿主或设备。点击节点与动作表 ``weekly-auto`` 族在仓库中全部禁用：回放只在内存里
启用它们（pipeline_override + 解析后的动作表副本），并只允许 ``weekly-auto`` 族的模拟点击。
消耗动作（プロデュース開始 / スキップ）仍需 ``ReplaySpendAuthority`` 针对当前帧发出的一次性授权，
且只在用例显式给出 ``allow_spend`` 时可授权。运行日志写入 dev_assets/private 下的临时目录，用后删除，
从不写 dev_assets/private/run_logs。

用例中 ``advance="click"`` 的帧在收到一次点击后切到下一帧，整数 N 表示被截图 N 次后切换，``STAY`` 不变。
标注「替代后继」的帧不是该点击的真实后继（例如已保存スケジュール时按「オートプロデュース」的结果尚无样本）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import maa_sinks  # noqa: E402
from agent.core.pipeline_actions import parse_table  # noqa: E402
from agent.core.task_failure import fail_reason  # noqa: E402
from tools.replay_screenshots import read_png, require  # noqa: E402

FRAMEWORK = "v5.12.3"
SPEND_IDS = ("weekly-auto-start", "weekly-auto-skip", "weekly-auto-soda-start")
PRIVATE = ROOT / "dev_assets" / "private"
TABLE_PATH = ROOT / "assets" / "input" / "pipeline_actions.json"
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "weekly_produce.json"
ENTRY = "SC.WeeklyProduce.Entry"
FAMILY = "weekly-auto"
CAPTURE = "captures-20261022-110140Z/"
STAY = 10 ** 9
POPUP_ACTION, POPUP_NODE = "popup-mission-completed-ok", "SC.Popup.MissionCompleted.Close"   # after an Auto's result
CASE_SECONDS = 240.0       # OCR runs ~10x slower while the game host renders (10-30: 112 s for one setup case)
# Static frames never change by themselves; shorter waits keep the replay fast (the runtime's frame counters bound
# every wait independently of these timeouts).
OFFLINE_TIMEOUTS = {"SC.WeeklyProduce.Main": 20000, "SC.Loading.Wait": 4000}   # Main: slow OCR beside the game host
OFFLINE_CLICK_TIMEOUT = 4000


def frame(n):
    return f"{CAPTURE}frame-{n:06d}.png"


HOME = frame(80)              # Home, 「プロデュース 300/300」
HOME_LOADING = frame(82)      # Home under 「Now Loading」 right after the Home プロデュース tap
SELECT = frame(97)            # プロデュース選択 with 「オートプロデュース」
WIZARD = frame(101)           # auto-mode scenario select (no 「オートプロデュース」 button): wizard step 1
SCHEDULE_1 = frame(149)       # オートスケジュール, オート1 = S.T.E.P. / 結華, count 1, loop off
SCHEDULE_2 = frame(151)       # count 2 (after one +)
SCHEDULE_5 = frame(153)       # count 5, 必要スタミナ 150, 上限回数 5/25
SCHEDULE_LOOP = frame(160)    # count 5 with 「ループする」 ON (25/25, 750)
DIALOG = frame(164)           # オートプロデュース開始: 必要スタミナ 300 → 150, スキップ 残り5回/日
AFTER_START = frame(167)      # schedule under 「Now Loading」 right after プロデュース開始
HOME_RUNNING = frame(175)     # Home 「オートプロデュース中 150/300」
LIVE_HOME_LOADING = "weekly-auto-20261025/home-loading.png"   # 「Now Loading」 over the Fes area: no guard hits
SCHEDULE_EMPTY = "weekly-auto-20261025/schedule-empty.png"    # live: オート1–3 all 「未設定」, 上限回数 0/25
EMPTY_ROW1 = "synthetic:empty-row1"
# Setup wizard, supervised live sampling 2026-10-30 (0 spend, not saved): each frame is the real successor of the tap
# before it, except W_ITEM (the 10-22 demo item page, whose real successor is SCHEDULE_1).
WIZ = "wing_samples/2026-10-29/20261029T13"
W_SCHEDULE_EMPTY = WIZ + "2406887237Z-r03c29-before.png"   # オート1 「未設定」 + 「設定」, 上限回数 0/25
W_SELECT_WING = WIZ + "2406887237Z-r03c29-after.png"       # Auto scenario page, 育成 tab: 「W.I.N.G.編」
W_SELECT_STEP = WIZ + "2421084599Z-r02c27-after.png"       # 特化 tab: 「S.T.E.P.編」 (no say "Halo")
W_UNIT = WIZ + "2438672778Z-r16c29-after.png"              # ユニット編成, 「1/20」
W_BOOKS_EMPTY = WIZ + "2454137887Z-r16c29-after.png"       # ノウハウブック選択, 3 empty slots
W_HISTORY = WIZ + "2510406009Z-r16c19-after.png"           # 設定履歴, newest 2026/10/22 22:05 + 呼び出し
W_BOOKS_FILLED = WIZ + "2542403417Z-r04c26-after.png"      # after 呼び出し: +100 / +100 / +90
W_ITEM = frame(148)                                        # プロデュースアイテム準備, both slots empty
# Auto settings sampling 2026-10-30 (JST 01:22–01:27, 0 spend, nothing saved).
S30 = "wing_samples/2026-10-29/20261029T16"
R_SCHEDULE_SET = S30 + "2235045053Z-r05c19-before.png"     # オート1 = S.T.E.P. / unit 「G.R.A.D.」 / Vocal, 1 run, 1/25, 30
R_POLICY_VOCAL = S30 + "2235045053Z-r05c19-after.png"      # 育成方針選択, Vocal highlighted
R_POLICY_DANCE = S30 + "2249376853Z-r07c16-after.png"      # Dance highlighted
R_SCHEDULE_AFTER_POLICY = S30 + "2301571434Z-r14c13-after.png"   # after キャンセル: the row still shows Vocal
R_SELECT_FANS = S30 + "2453501288Z-r13c22-after.png"       # Auto list: 「ファン感謝祭編」 selected
R_SELECT_GRAD = S30 + "2507260355Z-r13c22-after.png"       # 「G.R.A.D.編」
R_SELECT_LP = S30 + "2520461785Z-r13c22-after.png"         # 「Landing Point編」 (last card; OCR 「LandingPoint編」)
R_BOOKS_EMPTY = S30 + "2603134688Z-r16c29-after.png"       # S.T.E.P. book page (row オート3, nothing chosen)
R_GRAD_ROW = "wing_samples/2026-10-29/20261029T180813808700Z-capture.png"   # live 05:08: Maa set オート1 = G.R.A.D. / ユニット1: no books
R_PRESET = S30 + "2634789438Z-capture.png"                 # ノウハウブックプリセット: プリセット1 / 2 + セット
# 「ループする」 live sampling 2026-10-30 (07:03–07:04Z, 0 spend): オート1 = W.I.N.G. / ユニット20 / Visual, 1 run.
L30 = "wing_samples/2026-10-30/20261030T070"
L_LOOP_OFF = L30 + "331726096Z-r16c06-before.png"   # ループ off: 1時間 / 30 / 1/25
L_LOOP_ON = L30 + "331726096Z-r16c06-after.png"     # its real successor: ループ on, 25時間 / 750 / 25/25
L_RECOVERY = L30 + "409275027Z-capture.png"          # 決定 with ループ on: 回復アイテム dialog, 300 » 300, 300/750
# Book picker sampling 2026-10-30 15:09–15:10Z (0 spend, nothing saved); each frame is the real successor of the one
# before it: book page → slot 1 「+」 → picker 0/3 → 1020 → 1010 → 1000 (3/3) → 決定 → slots filled.
P30 = "wing_samples/2026-10-30/20261030T15"
P_BOOKS_EMPTY = P30 + "0920487222Z-capture.png"
P_PICKER_0 = P30 + "0939060568Z-capture.png"
P_PICKER_1 = P30 + "0947579341Z-capture.png"       # ✓ 1020, detail 【オイサラバエル】樋口円香 60/20/20/30
P_PICKER_2 = P30 + "0956490247Z-capture.png"
P_PICKER_3 = P30 + "1005404871Z-capture.png"       # 3/3, the other tiles grey, 決定 lit
P_BOOKS_FILLED = P30 + "1026564111Z-capture.png"   # slots +60/+20/+20/+30, +110/+30/+30/+40, +100/+30/+40/+40
# End of an Auto, live 2026-11-01 (LP, host-ui-20261101b, 0 spend): each frame is the real successor of the one before.
E01 = "wing_samples/2026-11-01/20261101T0"
E_HOME_DONE = E01 + "40145521090Z-capture.png"            # Home 「オートプロデュース中 283/300」 after the hour
E_END = E01 + "40244893470Z-r16c26-after.png"             # 「オートプロデュース終了」 OK
E_END_LOADING = E01 + "40258434294Z-r11c15-after.png"     # Now Loading over Home
E_RESULT = E01 + "40308326850Z-capture.png"               # 「オートリザルト」 (フェスアイドル tab) + OK
E_RESULT_LOADING = E01 + "40404877550Z-r16c29-after.png"  # Now Loading over the result page
E_MISSION = E01 + "40414965231Z-capture.png"              # 「ミッション達成」 over Home 「プロデュース 284/300」
E_HOME = E01 + "40445499066Z-capture.png"                 # Home 「プロデュース 284/300」
E_HOME_RUNNING = E01 + "60532146232Z-r16c26-before.png"   # 17:05, an Auto 3 min in
E_RUNNING = E01 + "60532146232Z-r16c26-after.png"         # 「オートプロデュース確認」 実行中 56:44 + キャンセル/終了/スキップ
E_RUNNING_CLOSED = E01 + "60549211019Z-r15c11-after.png"  # キャンセル → Home 「オートプロデュース中」
PICKER_PLAN = ({"title": "【オイサラバエル】樋口円香", "vo": 60, "da": 20, "vi": 20, "me": 30, "eval": 1020},
               {"title": "【ポシェットの中には】福丸小糸", "vo": 110, "da": 30, "vi": 30, "me": 40, "eval": 1010},
               {"title": "【ポシェットの中には】福丸小糸", "vo": 100, "da": 30, "vi": 40, "me": 40, "eval": 1000})


def synthetic_empty_row1():
    """Frame 149 with row オート1's content area replaced by row オート2's (「未設定」 + 「設定」), bottom bar kept.
    In-memory only: an empty-row-1 schedule has no native sample yet (the demo started from an empty schedule but went
    through the wizard before the schedule page was first shown)."""
    image = read_png(private_frame(SCHEDULE_1)).copy()
    image[95:199, 26:1224] = image[266:370, 26:1224]
    return image


SYNTHETIC = {EMPTY_ROW1: synthetic_empty_row1}


@dataclass(frozen=True)
class Case:
    id: str
    steps: tuple                 # ((image, advance), ...)
    clicks: tuple                # exact simulated action ids, in order
    outcome: str                 # WeeklyAutoRun outcome: started / stopped / spend_uncertain ...
    reason: str = ""             # exact run.reason ("" = not checked)
    runs: int = 1
    use_skip: bool = False
    allow_spend: tuple = ()
    enable_input: bool = True
    scenario: str = ""           # Auto核对剧本 attach (""/"WING"/"FANS"/"GRAD"/"LP"/"STEP")
    unit_name: str = ""          # Auto核对ユニット名 attach
    setup: str = "keep"          # Auto设定オート1 attach ("keep"/"maa")
    unit: int = 1                # Auto编队 attach
    books: str = "history"       # Auto带书 attach
    policy: str = ""             # Auto育成方針 attach
    team: str = "unit"           # Auto编队来源 attach ("auto" needs expect_p_card, the prep's P card)
    loop: bool = False           # Autoループ attach
    soda_cap: int = 450          # Autoソーダ上限 attach
    book_plan: tuple = ()        # 带书 「ノウハウ页指定」: the books the ノウハウ page saved (runtime option)
    expect_p_card: str | None = None
    note: str = ""


CASES = (
    Case("happy-schedule-start", ((SCHEDULE_5, "click"), (DIALOG, "click"), (AFTER_START, 2), (HOME_RUNNING, STAY)),
         ("weekly-auto-decide", "weekly-auto-start"), "started", "started", runs=5, allow_spend=("weekly-auto-start",),
         note="真实后继：決定→开始确认（300→150）→プロデュース開始→Now Loading→首页「オートプロデュース中」"),
    Case("happy-home-to-start", ((HOME, "click"), (HOME_LOADING, 2), (SELECT, "click"), (SCHEDULE_5, "click"),
                                 (DIALOG, "click"), (AFTER_START, 2), (HOME_RUNNING, STAY)),
         ("weekly-auto-home-produce", "weekly-auto-entry", "weekly-auto-decide", "weekly-auto-start"), "started", "started",
         runs=5, allow_spend=("weekly-auto-start",),
         note="首页→プロデュース選択→オートスケジュール（替代后继：已保存スケジュール时的结果尚无样本）→开始"),
    Case("dry-run-cancels-without-grant", ((SCHEDULE_5, "click"), (DIALOG, "click"), (SCHEDULE_5, STAY)),
         ("weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped", "spend_not_enabled", runs=5,
         note="无消耗授权：核对通过后按キャンセル（不消耗）并停止；取消后的スケジュール为替代后继"),
    Case("count-up-then-stamina-mismatch", ((SCHEDULE_1, "click"), (SCHEDULE_2, "click"), (DIALOG, "click"),
                                            (SCHEDULE_5, STAY)),
         ("weekly-auto-count-plus", "weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped",
         "stamina_mismatch_300_150", runs=2, allow_spend=("weekly-auto-start",),
         note="1→2 只点一次 +；开始确认显示 300→150（应为 240）→ キャンセル并停止（对话框为 5 次时的真实帧）"),
    Case("expectations-match-start", ((SCHEDULE_5, "click"), (DIALOG, "click"), (AFTER_START, 2), (HOME_RUNNING, STAY)),
         ("weekly-auto-decide", "weekly-auto-start"), "started", "started", runs=5, allow_spend=("weekly-auto-start",),
         scenario="STEP", unit_name="結華", note="核对剧本 S.T.E.P. 与ユニット名「結華」均符合"),
    Case("unit-name-mismatch-stops", ((SCHEDULE_5, STAY),), (), "stopped", "unit_mismatch", runs=5, unit_name="灯織",
         note="オート1 的ユニット名与面板所填不同：不点击"),
    Case("loop-on-turned-off", ((SCHEDULE_LOOP, "click"), (SCHEDULE_5, "click"), (DIALOG, "click"), (SCHEDULE_5, STAY)),
         ("weekly-auto-loop-off", "weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped", "spend_not_enabled",
         runs=5, note="「ループする」为开：点一次关掉（真实后继 #160→#164 为关），再走无授权的核对与キャンセル"),
    Case("loop-stays-on-stops", ((SCHEDULE_LOOP, STAY),), ("weekly-auto-loop-off",), "stopped", "loop_not_off", runs=5,
         note="点「ループする」后仍为开：只点一次，等待有限帧后停止"),
    Case("loop-on-soda-tap-cancels", ((L_LOOP_OFF, "click"), (L_LOOP_ON, "click"), (L_RECOVERY, STAY)),
         ("weekly-auto-loop-on", "weekly-auto-decide", "weekly-auto-soda-30", "weekly-auto-recovery-cancel"), "stopped",
         "soda_30_not_changed", scenario="WING", loop=True,
         note="ループ：点开（真实后继）、決定 后回復アイテム框（真实后继）：读 300 / 300/750 与持有数，计划 30×1+60×2+100×3；"
              "点一次「+」后画面不变（静帧）→ キャンセル后停止"),
    Case("loop-off-recovery-dialog-cancels", ((L_LOOP_ON, "click"), (L_LOOP_OFF, "click"), (L_RECOVERY, "click"),
                                              (L_LOOP_OFF, STAY)),
         ("weekly-auto-loop-off", "weekly-auto-decide", "weekly-auto-recovery-cancel"), "stopped", "stamina_short_300",
         scenario="WING",
         note="未开ループ：点关（L_LOOP_OFF 为替代后继）；決定 后若出回復アイテム框（替代后继）直接キャンセル，不设ソーダ"),
    Case("empty-row1-stops", ((EMPTY_ROW1, STAY),), (), "stopped", "row1_empty", runs=1,
         note="オート1 未设定：不进入设定向导"),
    Case("live-empty-schedule-stops", ((HOME, "click"), (LIVE_HOME_LOADING, 3), (SELECT, "click"),
                                       (SCHEDULE_EMPTY, STAY)),
         ("weekly-auto-home-produce", "weekly-auto-entry"), "stopped", "row1_empty", runs=1,
         note="2026-10-25 只读实机：首页加载帧不被任何守卫识别时继续等待；スケジュール全部未設定→停止"),
    Case("count-jump-stops", ((SCHEDULE_1, "click"), (SCHEDULE_5, STAY)), ("weekly-auto-count-plus",), "stopped",
         "count_unexpected_1_5", runs=2, note="点一次 + 后读到 5（非 2）：停止，不再点 −"),
    Case("count-unchanged-stops", ((SCHEDULE_1, STAY),), ("weekly-auto-count-plus",), "stopped", "count_not_changed",
         runs=2, note="点一次 + 后回数不变：只点一次，等待有限帧后停止"),
    Case("count-down-unchanged-stops", ((SCHEDULE_5, STAY),), ("weekly-auto-count-minus",), "stopped",
         "count_not_changed", runs=4, note="目标 4、当前 5：只点一次 −"),
    Case("collect-finished-auto", ((E_HOME_DONE, "click"), (E_END, "click"), (E_END_LOADING, 2), (E_RESULT, "click"),
                                   (E_RESULT_LOADING, 2), (E_MISSION, "click"), (E_HOME, STAY)),
         ("weekly-auto-home-running", "weekly-auto-end-ok", "weekly-auto-result-ok", "popup-mission-completed-ok",
          "weekly-auto-home-produce"), "stopped", "home_after_to_select", runs=1,
         note="Auto 结束后收取：「オートプロデュース中」→終了 OK→オートリザルト OK→ミッション達成 OK→首页「プロデュース」，"
              "接着照常开下一局（プロデュース選択 无后继帧，停在等待）"),
    Case("running-auto-is-left-alone", ((E_HOME_RUNNING, "click"), (E_RUNNING, "click"), (E_RUNNING_CLOSED, STAY)),
         ("weekly-auto-home-running", "weekly-auto-running-cancel"), "stopped", "auto_produce_running", runs=1,
         note="Auto 未结束：点开「オートプロデュース確認」只按キャンセル（不按終了/スキップ），然后停止"),
    Case("wizard-after-entry-stops", ((SELECT, "click"), (WIZARD, STAY)), ("weekly-auto-entry",), "stopped",
         "row1_setup_off", runs=1, note="按「オートプロデュース」后出现剧本选择向导，未开启由 Maa 设定：停止，不操作向导"),
    Case("step-setup-from-empty-row", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_WING, "click"), (W_SELECT_STEP, "click"),
                                       (W_UNIT, "click"), (W_BOOKS_EMPTY, "click"), (W_HISTORY, "click"),
                                       (W_BOOKS_FILLED, "click"), (W_ITEM, "click"), (R_SCHEDULE_SET, "click"),
                                       (DIALOG, "click"), (R_SCHEDULE_SET, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-tab-special", "weekly-auto-setup-scenario-next",
          "weekly-auto-setup-unit-next", "weekly-auto-setup-book-history", "weekly-auto-setup-history-call",
          "weekly-auto-setup-book-next", "weekly-auto-setup-item-decide", "weekly-auto-decide",
          "weekly-auto-dialog-cancel"), "stopped", "stamina_mismatch_300_150", runs=1, scenario="STEP", setup="maa",
         note="空オート1：設定→特化 S.T.E.P.→ユニット1→設定履歴 呼び出し→3 本→道具空 決定→スケジュール（10-30 实机设定后的"
              "行：ユニット名与向导读到的一致）決定；开始确认为 5 次时的帧（300→150≠30）→キャンセル"),
    Case("stitched-unit-name-mismatch-stops", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_STEP, "click"), (W_UNIT, "click"),
                                               (W_BOOKS_EMPTY, "click"), (W_HISTORY, "click"), (W_BOOKS_FILLED, "click"),
                                               (W_ITEM, "click"), (SCHEDULE_1, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-next",
          "weekly-auto-setup-book-history", "weekly-auto-setup-history-call", "weekly-auto-setup-book-next",
          "weekly-auto-setup-item-decide"), "stopped", "unit_mismatch", runs=1, scenario="STEP", setup="maa",
         note="设定后的行显示的ユニット名（10-22 示范「結華」）与向导读到的不同（替代后继）：不按決定"),
    Case("grad-setup-steps-down-the-list", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_WING, "click"),
                                            (R_SELECT_FANS, "click"), (R_SELECT_GRAD, "click"), (W_UNIT, "click"),
                                            (W_ITEM, "click"), (R_SCHEDULE_SET, "click"), (DIALOG, "click"),
                                            (R_SCHEDULE_SET, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-down", "weekly-auto-setup-scenario-down",
          "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-next", "weekly-auto-setup-item-decide",
          "weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped", "stamina_mismatch_300_150", runs=1,
         scenario="GRAD", setup="maa", policy="auto", team="auto", expect_p_card="1040080070",
         note="G.R.A.D.：W.I.N.G.→点下一张→ファン感謝祭→点下一张→G.R.A.D.→次へ（真实后继）；ユニット页读出 P 卡"
              "（自动编队的 P 卡，Vo → Vocal，行上已是 Vocal 不改）；之后的页、设定后的行为替代后继"),
    Case("auto-team-p-card-mismatch-stops", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_WING, "click"),
                                             (R_SELECT_FANS, "click"), (R_SELECT_GRAD, "click"), (W_UNIT, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-down", "weekly-auto-setup-scenario-down",
          "weekly-auto-setup-scenario-next"), "stopped", "auto_team_p_card_mismatch:1040080070", runs=1,
         scenario="GRAD", setup="maa", policy="auto", team="auto", expect_p_card="1040010010",
         note="自动编队写入的 P 卡（1040010010）与 Auto ユニット页读到的不同：不按次へ，停止"),
    Case("grad-row-has-no-books", ((R_GRAD_ROW, "click"), (DIALOG, "click"), (R_GRAD_ROW, STAY)),
         ("weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped", "stamina_mismatch_300_150", runs=1,
         scenario="GRAD", unit_name="G.R.A.D.",
         note="G.R.A.D. 行（实机 10-30 由 Maa 设定）：没有书格，道具两格在左移的位置；横幅读出 GRAD；开始确认为 5 次时的帧→キャンセル"),
    Case("lp-is-the-last-card", ((W_SCHEDULE_EMPTY, "click"), (R_SELECT_LP, STAY)), ("weekly-auto-row-set",),
         "stopped", "scenario_above:LandingPoint編", runs=1, scenario="GRAD", setup="maa",
         note="显示的剧本在目标下面：列表向上点未采样，停止"),
    Case("step-books-from-preset-1", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_STEP, "click"), (W_UNIT, "click"),
                                      (R_BOOKS_EMPTY, "click"), (R_PRESET, "click"), (W_BOOKS_FILLED, "click"),
                                      (W_ITEM, "click"), (R_SCHEDULE_SET, "click"), (DIALOG, "click"),
                                      (R_SCHEDULE_SET, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-next",
          "weekly-auto-setup-book-preset", "weekly-auto-setup-preset-set-1", "weekly-auto-setup-book-next",
          "weekly-auto-setup-item-decide", "weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped",
         "stamina_mismatch_300_150", runs=1, scenario="STEP", setup="maa", books="preset1",
         note="带书=プリセット1：プリセット→「セット」（替代后继：設定履歴 呼び出し 后的 3 本）→次へ"),
    Case("policy-dance-not-applied-stops", ((R_SCHEDULE_SET, "click"), (R_POLICY_VOCAL, "click"),
                                            (R_POLICY_DANCE, "click"), (R_SCHEDULE_AFTER_POLICY, STAY)),
         ("weekly-auto-policy-open", "weekly-auto-policy-dance", "weekly-auto-policy-set"), "stopped",
         "policy_not_applied_Vocal", runs=1, policy="Dance",
         note="育成方針=Dance：打开→选 Dance（真实后继）→設定する；之后的行仍是 Vocal（替代后继：实机按的是キャンセル）→停止"),
    Case("setup-off-live-empty-row-stops", ((W_SCHEDULE_EMPTY, STAY),), (), "stopped", "row1_empty", runs=1,
         scenario="STEP", note="未开启由 Maa 设定：空オート1 停止，不按「設定」"),
    Case("direct-wizard-unit-tab-unchanged-stops", ((SELECT, "click"), (WIZARD, "click"), (W_SELECT_STEP, "click"),
                                                    (W_UNIT, STAY)),
         ("weekly-auto-entry", "weekly-auto-setup-tab-special", "weekly-auto-setup-scenario-next",
          "weekly-auto-setup-unit-2"), "stopped", "unit_tab_2_not_changed", runs=1, scenario="STEP", setup="maa",
         unit=2, note="按「オートプロデュース」直接进向导（10-22 示范）：切特化→次へ→点ユニット2 后仍显示 1/20：只点一次后停止"),
    Case("step-setup-target-books", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_STEP, "click"), (W_UNIT, "click"),
                                     (P_BOOKS_EMPTY, "click"), (P_PICKER_0, "click"), (P_PICKER_1, "click"),
                                     (P_PICKER_2, "click"), (P_PICKER_3, "click"), (P_BOOKS_FILLED, "click"),
                                     (W_ITEM, "click"), (R_SCHEDULE_SET, "click"), (DIALOG, "click"),
                                     (R_SCHEDULE_SET, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-next",
          "weekly-auto-book-slot1", "weekly-auto-picker-tile-r0c0", "weekly-auto-picker-tile-r0c1",
          "weekly-auto-picker-tile-r0c2", "weekly-auto-picker-decide", "weekly-auto-setup-book-next",
          "weekly-auto-setup-item-decide", "weekly-auto-decide", "weekly-auto-dialog-cancel"), "stopped",
         "stamina_mismatch_300_150", runs=1, scenario="STEP", setup="maa", books="target", book_plan=PICKER_PLAN,
         note="带书 ノウハウ页指定：书页→「+」→选书（1020/1010/1000 按評価値点，逐本核对书名与属性）→決定→书页三本属性核对→次へ"
              "（10-30 15:09 实机连续帧；設定前后与道具页为替代帧）"),
    Case("target-book-not-visible-stops", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_STEP, "click"), (W_UNIT, "click"),
                                           (P_BOOKS_EMPTY, "click"), (P_PICKER_0, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-next",
          "weekly-auto-book-slot1"), "stopped", "target_book_not_visible_1", runs=1, scenario="STEP", setup="maa",
         books="target", book_plan=({**PICKER_PLAN[0], "eval": 900}, *PICKER_PLAN[1:]),
         note="指定的书（評価値 900）不在选书第一屏：不点，停止"),
    Case("history-call-without-books-stops", ((W_SCHEDULE_EMPTY, "click"), (W_SELECT_STEP, "click"), (W_UNIT, "click"),
                                              (W_BOOKS_EMPTY, "click"), (W_HISTORY, "click"), (W_BOOKS_EMPTY, "click"),
                                              (W_BOOKS_EMPTY, STAY)),
         ("weekly-auto-row-set", "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-next",
          "weekly-auto-setup-book-history", "weekly-auto-setup-history-call", "weekly-auto-setup-book-omakase"),
         "stopped", "books_not_filled_0", runs=1, scenario="STEP", setup="maa",
         note="設定 后直接是特化页（替代后继）；呼び出し 后仍是 3 个空槽（替代后继）→ 兜底按一次おまかせ編成；仍空：停止"),
    Case("shipped-resource-has-no-input", ((HOME, STAY),), (), "stopped", "input_disabled:home_open", runs=1,
         enable_input=False, note="仓库默认（点击节点禁用）：只读取并停止"),
    Case("skip-option-uses-skip", ((SCHEDULE_5, "click"), (DIALOG, "click"), (AFTER_START, 2), (HOME_RUNNING, STAY)),
         ("weekly-auto-decide", "weekly-auto-skip"), "started", "started", runs=5, use_skip=True,
         allow_spend=("weekly-auto-skip",),
         note="スキップ选项（默认关）：残り5回/日 ≥ 5；按スキップ后的画面为替代后继（示范按的是プロデュース開始）"),
)


def weekly_auto_module():
    from agent.core import weekly_auto
    return weekly_auto


def private_frame(relative):
    path = (PRIVATE / relative).resolve()
    require(path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".png", f"帧须位于dev_assets/private: {relative}")
    return path


def missing_frames(cases=CASES):
    needed = {image for case in cases for image, _ in case.steps if image not in SYNTHETIC}
    if any(image == EMPTY_ROW1 for case in cases for image, _ in case.steps):
        needed.add(SCHEDULE_1)
    return sorted(image for image in needed if not private_frame(image).is_file())


def load_image(image):
    return SYNTHETIC[image]() if image in SYNTHETIC else read_png(private_frame(image))


def enabled_table():
    """The shipped action table with only the weekly-auto family (and the ミッション達成 OK) enabled (in memory)."""
    data = json.loads(TABLE_PATH.read_bytes())
    for action in data["actions"]:
        if action["family"] == FAMILY or action["id"] == POPUP_ACTION:
            action["enabled"] = True
    return parse_table(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def click_nodes():
    nodes = json.loads(PIPELINE.read_text(encoding="utf-8"))
    names = sorted(name for name, node in nodes.items() if node.get("action") == "Click")
    require(names and all(nodes[n].get("enabled") is False for n in names), "WeeklyProduce 点击节点必须默认禁用")
    return names + [POPUP_NODE]


def build_override(case, nodes):
    override = {name: {"timeout": value} for name, value in OFFLINE_TIMEOUTS.items()}
    for name in nodes:
        override[name] = {"timeout": OFFLINE_CLICK_TIMEOUT, **({"enabled": True} if case.enable_input else {})}
    override["SC.WeeklyProduce.Config.Runs"] = {"attach": {"runs": case.runs}}
    override["SC.WeeklyProduce.Config.Skip"] = {"attach": {"use_skip": case.use_skip}}
    override["SC.WeeklyProduce.Config.Scenario"] = {"attach": {"expect_scenario": case.scenario}}
    override["SC.WeeklyProduce.Config.UnitName"] = {"attach": {"expect_unit": case.unit_name}}
    override["SC.WeeklyProduce.Config.Setup"] = {"attach": {"setup": case.setup}}
    override["SC.WeeklyProduce.Config.Unit"] = {"attach": {"unit": case.unit}}
    override["SC.WeeklyProduce.Config.Books"] = {"attach": {"books": case.books}}
    override["SC.WeeklyProduce.Config.Policy"] = {"attach": {"policy": case.policy}}
    override["SC.WeeklyProduce.Config.Team"] = {"attach": {"team": case.team}}
    override["SC.WeeklyProduce.Config.Loop"] = {"attach": {"loop": case.loop}}
    override["SC.WeeklyProduce.Config.SodaCap"] = {"attach": {"soda_cap": case.soda_cap}}
    return override


def replay(cases=CASES):
    from maa.custom_action import CustomAction
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker

    from agent.core import weekly_auto
    from agent.core.pipeline_frame_guard import install
    from agent.core.resource_bundles import bundle_paths, ocr_model_dir
    from tools.build_native_resource import verify as verify_bundle
    from tools.run_tasks import ReplayScenario, ReplaySpendAuthority, create_replay_controller

    require(Library.version() == FRAMEWORK, f"回放要求MaaFramework {FRAMEWORK}")
    require(cases, "回放用例为空")
    problems = verify_bundle()
    require(not problems, "私有模板包校验失败：" + "；".join(problems[:5]))
    require(not missing_frames(cases), "缺少私有回放帧：" + ", ".join(missing_frames(cases)[:5]))
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    nodes = click_nodes()
    table = enabled_table()

    class RecordFail(CustomAction):
        reasons = []

        def run(self, context, argv):
            RecordFail.reasons.append(fail_reason(argv.custom_action_param))
            return False

    scenario = ReplayScenario([load_image(cases[0].steps[0][0])], [1], allow_input=False, table=table,
                              families={FAMILY, "popup"})   # popup: only the ミッション達成 OK is enabled
    controller = create_replay_controller(scenario)
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            "离线控制器初始化失败")
    resource = Resource()
    for bundle in bundle_paths():
        require(resource.post_bundle(bundle).wait().succeeded, f"资源载入失败: {bundle}")
    model = ocr_model_dir()
    if model is not None:
        require(resource.post_ocr_model(model).wait().succeeded, "OCR 模型载入失败")
    log_dir = Path(tempfile.mkdtemp(prefix="weekly-auto-replay-", dir=PRIVATE))
    try:
        runtime = weekly_auto.WeeklyAutoRuntime(authority=ReplaySpendAuthority(scenario), log_root=log_dir)
        recognitions, actions = weekly_auto.registrations(runtime)
        fail_action = RecordFail()
        require(resource.register_custom_action("SC.FailTask", fail_action), "SC.FailTask 注册失败")
        for name, action in actions.items():
            require(resource.register_custom_action(name, action), f"自定义动作注册失败: {name}")
        for name, recognition in recognitions.items():
            require(resource.register_custom_recognition(name, recognition), f"自定义识别注册失败: {name}")
        binder = install(resource, scenario)
        tasker = Tasker()
        require(tasker.bind(resource, controller) and tasker.inited, "离线 Tasker 绑定失败")
        binder.attach(tasker)
        results = [_run_case(tasker, controller, runtime, case, nodes, RecordFail) for case in cases]
        maa_sinks.release(tasker)   # the binder's sinks would keep this tasker and its OCR model for the process
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)
    return {"passed": all(r["passed"] for r in results), "offline_only": True, "real_input_attempts": 0,
            "framework_version": Library.version(), "entry": ENTRY, "case_count": len(results), "cases": results}


def _run_case(tasker, controller, runtime, case, nodes, record_fail):
    scenario = controller.scenario
    scenario.reset_frames([load_image(image) for image, _ in case.steps], [a for _, a in case.steps],
                          allow_input=case.enable_input)
    scenario.allow_spend = frozenset(case.allow_spend)
    record_fail.reasons = []
    runtime.run = None
    runtime.expect_p_card = case.expect_p_card   # the UI engine passes the prep's P card (自动编队)
    runtime.book_plan = weekly_auto_module().clean_book_plan(list(case.book_plan)) if case.book_plan else None
    started = time.monotonic()
    job = tasker.post_task(ENTRY, build_override(case, nodes))
    while not job.done:
        if time.monotonic() - started > CASE_SECONDS:
            tasker.post_stop().wait()
            raise RuntimeError(f"用例 {case.id} 超时；已停止且不重试")
        time.sleep(0.01)
    detail = job.get()
    trace = [node.name for node in detail.nodes] if detail else []
    run = runtime.run
    outcome = run.outcome if run is not None else None
    reason = run.reason if run is not None else None
    expected_success = case.outcome == "started"
    passed = (outcome == case.outcome and (not case.reason or reason == case.reason)
              and bool(job.succeeded) == expected_success
              and tuple(scenario.actions) == tuple(case.clicks) and not scenario.refused
              and scenario.other_calls == 0 and scenario.captures > 0 and not record_fail.reasons
              and all(result == "sent" for _, result in scenario.spends)
              and len(scenario.spends) == sum(1 for a in case.clicks if a in SPEND_IDS))
    return {"id": case.id, "passed": passed, "expected": [case.outcome, case.reason, list(case.clicks)],
            "outcome": outcome, "reason": reason, "framework_succeeded": bool(job.succeeded),
            "simulated_actions": list(scenario.actions), "simulated_clicks": [list(c) for c in scenario.clicks],
            "refused": [list(c) for c in scenario.refused], "spends": [list(s) for s in scenario.spends],
            "other_input_calls": scenario.other_calls, "captures": scenario.captures, "final_step": scenario.index,
            "fail_reasons": list(record_fail.reasons), "node_trace": trace[-12:], "note": case.note,
            "decisions": list(run.decisions) if run is not None else [],
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="WeeklyProduce 离线回放（模拟点击，不连接任何宿主）。")
    parser.add_argument("--case", action="append", default=None, help="只运行指定用例 ID（可重复）")
    parser.add_argument("--report", type=Path, default=None, help="写入 dev_assets/private 下的 JSON 报告")
    args = parser.parse_args(argv)
    cases = tuple(c for c in CASES if not args.case or c.id in args.case)
    try:
        report = replay(cases)
    except Exception as exc:  # report every failure as a refusal; never retry
        report = {"passed": False, "offline_only": True, "real_input_attempts": 0, "error": str(exc)}
    if args.report:
        path = args.report.resolve()
        require(path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".json", "报告须写入dev_assets/private")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary = {"passed": report["passed"], "error": report.get("error"),
               "cases": [(c["id"], c["passed"], c["outcome"], c["reason"], c["simulated_actions"])
                         for c in report.get("cases", [])]}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
