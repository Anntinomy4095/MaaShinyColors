"""W.I.N.G. navigation framework: screen rules, decision points and the (input-refusing) runtime. No input is sent.

User goal: 「可以maa自己有了导航框架、评分系统后自动推进，我可以监督」. Maa walks the Produce on its own
under supervision: every frame is classified (``agent.core.wing_screen``), each known screen has exactly one rule
(wait / fixed intent / scorer decision / stop), and every click intent is bound to a positive screen guard, a
per-intent success guard (the screens accepted afterwards) and an explicit stop path.

The Pipeline (``assets/resource/base/pipeline/wing.json``) is *generated* from ``SCREENS`` by
``tools/wing_nav_build.py`` so the two cannot drift; ``tests/test_wing_nav.py`` checks the committed file.

Input: every click intent is a table-bound Pipeline ``Click`` node ``SC.Wing.Click.<Screen>.<intent>``
(``wing-*`` families in ``assets/input/pipeline_actions.json``, shipped ``enabled:false``). Python decides
(``SC.Wing.Decide`` on the click frame), the Pipeline clicks. ``WingNavRuntime.act`` is the Agent-side gate; it refuses
everything unless the run was configured with ``RunOptions(input_enabled=True)`` by the supervised launcher entry
(``tools/run_tasks.py`` ``Wing``). ``LIVE_INPUT_ENABLED`` stays False, so the AgentServer path never sends input.

Decision points call the scorer modules by stable names (``SCORER_HOOKS``); a missing module/function is reported
as ``scorer_missing:<module>:<function>`` and the run stops (or uses the documented fallback). This module never
edits or reimplements wing_score / wing_policy / wing_sp / wing_team / wing_audition.
"""
from __future__ import annotations

import copy
import dataclasses
import importlib
import json
import re
from typing import Any, Callable, Mapping

from agent.core import halo_growth as hg
from agent.core import halo_policy as hp
from agent.core import wing_options as wo
from agent.core import wing_skill_panel as wsp
from agent.core import wing_sp_view as wsv
from agent.core import wing_team_edit as wte
from agent.core import picker_sort as ps

LIVE_INPUT_ENABLED = False          # AgentServer default; the launcher enables a run through RunOptions only
MAX_ACTS_PER_RUN = 3000             # whole Produce ~ 600–900 clicks when commu SKIP is used
MAX_SAME_INTENT_IN_A_ROW = 3        # same intent on an unchanged screen -> stop (never retry blindly)
# Intents that legitimately repeat on one screen with progress between them (the After guard still demands a changed
# frame each time): 大富豪 パス once per own turn (a game has < 13 own turns per round, a few rounds).
# The audition tab's page arrow turns once per page (AUDITION_PAGES = 6; live 2026-10-28: the 強化週間 survey of S1's
# final week was refused at the 4th turn).
# say "Halo" (H2d live 2026-11-06: the 4th card of a チーム合同練習 survey was refused): the carousel arrows turn once
# per card (WingNavRuntime.HALO_SURVEY_MAX = 45 cards a survey, then up to half of them the shorter way to the pick,
# plus a re-tap for a dropped arrow); 成長's 上限 + / >> repeat within the visit's tap budget (halo_growth.MAX_TAPS =
# 120), each tap must lower the points (the growth step checks it).
REPEAT_LIMITS = {"minigame_pass": 60, **{i: wte.MAX_PICKER_SCROLLS + 1 for i in wte.SCROLL_INTENTS.values()},
                 wo.READ_INTENTS["scroll"]: wo.OPTIONS_MAX_SCROLLS + 1, "audition_next": 6,
                 "halo_card_next": 80, "halo_card_prev": 80,
                 **{f"halo_growth_{k}_{s}": 120 for k in ("stat", "cap") for s in ("vo", "da", "vi")}}
# Wheel targets (fixed delta in the action table, no button; wing_nav_build writes the "wheel" field).
WHEEL_INTENTS = {**wte.SCROLL_WHEEL, wo.READ_INTENTS["scroll"]: wo.OPTIONS_SCROLL_WHEEL}
MINIGAME_MAX_PASSES = 80            # passes per 大富豪 game before the run stops (minigame_too_long)
UNIT_SEARCH_MAX_TAPS = 24           # unit tabs + page arrows tapped while looking for the configured team (1–20)
WAIT_LIMIT_DEFAULT = 120            # consecutive polls of one wait screen (~60 s at 500 ms) before wait_stalled
REWARD_MAX_TAPS = 16              # 獲得報酬 review taps (tabs, books, 保護, 再抽選) per run
REWARD_UNSETTLED = 4              # frames a tab / detail / 保護 change may take before the review stops
WAIT_LIMITS = {"Live": 900, "Commu": 600, "TrueEndCommu": 600, "Loading": 240, "ScheduleBusy": 240, "Review": 30,
               "MinigameGame": 600}
# SP learning on the 振り返り panel (sp_mode "auto"): per spending session
SP_SESSION_MAX_TAPS = 60            # node taps (inspect + select) before the session stops
SP_SESSION_MAX_INSPECT = 30         # info-bar reads per session (deep nodes included; each node once per Produce)
SP_INSPECT_MIN, SP_INSPECT_PER_SP = 4, 10   # 2026-10-15 speed: budget = clamp(4 + sp // 10, ..., 30) per session


def sp_inspect_budget(sp) -> int:
    """Info-bar reads allowed in one session: a 43 SP session spent 12 min inspecting (8 s per read) and learned
    nothing; bindings are kept for the whole Produce, so later (richer) sessions inspect more."""
    extra = sp // SP_INSPECT_PER_SP if isinstance(sp, int) and sp > 0 else 0
    return min(SP_SESSION_MAX_INSPECT, SP_INSPECT_MIN + extra)
SP_UNREAD_REREADS = 5             # Schedule frames re-read when the SP counter is missing at a stat cap
STAT_KEYS = ("Vo", "Da", "Vi", "Me")
STAT_JUMP_MAX = 300                 # a week-to-week change beyond this is a misread (live 10-14: Me −2084), not a failure
CATASTROPHE_TROUBLE = 99            # 大失敗 empties stamina: the next week shows the 99 % trouble rate
STAT_RATE_MAX = 60                  # primary gain per week bound for the cap horizon (live 10-25: Vo 904 by week 21)
SP_RUN_WEEKS = 32                   # 4 seasons x 8 weeks; SP sessions before HubSemiFinal/HubFinal have no future
SP_SESSION_MAX_LEARN = 12           # learned nodes per session
SP_LEARN_RETRY_WAITS = 2            # settle frames on 振り返り after 習得する before the one learn retry
SP_UNSTABLE_WAITS = 4               # consecutive unreadable/non-canonical panel frames before re-entering / stopping
SP_MAX_ZOOMS = 3                    # small-view zoom-outs per session (a re-entered panel opens zoomed in again)
SP_MIN_NODE_COST = 10               # 2026-10-14 native discounted inner node (Twilight / UR)

KINDS = ("wait", "fixed", "decide", "stop", "finish")

# Click rects on the 1280x720 canvas. verified=True: measured on native frames (demo 2026-10-05 or the catalog's
# native-scaled samples with an OCR box at that place); False: not observed yet -> Act refuses even when enabled.
TARGETS: dict[str, tuple[tuple[int, int, int, int], bool]] = {
    "commu_skip": ((1175, 35, 85, 60), True),
    "commu_ff_toggle": ((1075, 624, 77, 64), True),
    "commu_ok": ((1117, 630, 70, 45), True),
    "tap_textbox": ((224, 532, 813, 162), True),
    # rank_next corrected 2026-10-06: the 次へ button sits at x ~1110–1210 (old rect started at 1180); 9 clicks on
    # アイドルランク frames, clean pairs #856->#857, #1076->#1077, #2601->transition.
    "rank_next": ((1105, 635, 110, 60), True),
    # Verified 2026-10-10 (W.I.N.G. approval): ResultList is a touch-to-advance list without buttons
    # the guard needs 「エントリーNo」. Native frame to be added from the live trace.
    "result_list_tap": ((540, 560, 200, 80), True),
    # 閉じる of the green help/tutorial layout spans x 558–723, y 565–640 (native #332/#117); widened 2026-10-09 so the
    # last-page close (third demo #120 (689,594) -> overlay closed) lies inside.
    "popup_close": ((585, 570, 120, 64), True),
    # 2026-10-09: the help/tutorial pages (green title bar, n/m) put → at x 795–872, y 565–640 on native 1280x720
    # (second demo #332 (837,596) -> #333 2/4; third demo #117 (859,572) -> #118); the old catalog rect (640,575)
    # came from scaled samples and sat inside 閉じる.
    "tutorial_next": ((800, 568, 66, 66), True),
    "page_return": ((19, 625, 77, 76), True),
    "hub_schedule": ((15, 450, 285, 240), True),
    "hub_rest": ((335, 540, 165, 160), True),
    "hub_review": ((700, 615, 325, 90), True),
    "hub_final_live": ((0, 600, 345, 110), True),
    "schedule_room_1": ((275, 422, 160, 148), True),
    "schedule_room_2": ((441, 422, 161, 148), True),
    "schedule_room_3": ((608, 422, 160, 148), True),
    "schedule_room_4": ((768, 422, 160, 148), True),
    "schedule_room_5": ((934, 422, 160, 148), True),
    "schedule_room_6": ((1098, 422, 163, 148), True),
    "schedule_decide": ((1094, 625, 167, 76), True),
    "schedule_audition_tab": ((13, 435, 217, 64), True),
    "schedule_lesson_tab": ((13, 160, 217, 70), True),   # #1562->#1566, #1704->#1708, #2061->#2065 (Schedule)
    "audition_prev": ((335, 294, 62, 84), True),
    "audition_next": ((1133, 294, 64, 84), True),
    "dialog_ok": ((695, 400, 100, 55), True),    # widened: LiveConfirm OK tapped at (783,433), #2649->#2650
    "dialog_cancel": ((466, 402, 140, 45), True),
    "live_auto": ((1011, 12, 64, 62), True),
    "choice_left": ((64, 157, 352, 180), True),
    "choice_center": ((461, 44, 348, 181), True),
    "choice_right": ((851, 157, 352, 183), True),
    "learn_cancel": ((468, 505, 138, 45), True),
    "replace_cancel": ((470, 620, 136, 40), True),
    "forget_cancel": ((570, 641, 138, 40), True),
    "eval_next": ((1135, 630, 85, 55), True),
    "fes_birth": ((1100, 620, 160, 70), True),
    "fes_birth_ok": ((655, 465, 160, 55), True),
    # スキル一括変更 popup: キャンセル (H1 2026-11-01 115518: the user's tap r16c13 (480-520, 600-640) closed it to FesBirth).
    "fes_skill_bulk_cancel": ((470, 615, 140, 55), True),
    # say "Halo" give-up (2026-11-01 user, RunOptions.halo_giveup): the Hub's 「諦める ホームに戻る」 (live pair
    # 20261101T072141 r03c30) and the dialog's 「諦める」 [735,440,165,75], selectable only by a long press
    # (wing-giveup-hold; host hold protocol v2). Measured on the live H0 frames; the user pressed 諦める by hand.
    "giveup_open": ((1170, 105, 80, 80), True),
    "giveup_hold": ((760, 452, 115, 52), True),
    # say "Halo" in-run taps (H2b 2026-11-02; RunOptions.halo_route only, family wing-halo). Measured on the H1 frames
    # of 2026-11-01 (the user's own taps and the buttons' OCR boxes): Hub プロデュース [26,608,266,80] / ファーストライブ
    # [1,619,324,87] (r16c03 -> スケジュール 22x, r16c06 -> 目標ライブ 2x), 休む [357,628,131,86] (r16c10 -> commu),
    # チーム情報 [509,582,192,66]; tabs レッスン&お仕事 / オーディション&ライブ (r08c03 7x) / チーム合同練習 (r12c03 5x);
    # carousel ‹ › chevrons of both pages (‹ x 560-620 / 592-655, › x 915-975 / 885-945, y 295-380; r08c14/15 30x,
    # r08c23 63x); 決定 [1140,637,76,45] (lesson/practice -> Hub or commu, audition -> REPORT); REPORT 次へ
    # [1141,639,72,42]; プロデュース目標 閉じる [597,616,87,40]; 勧誘 チームに勧誘 [882,481,166,33] (r12c24 4x) and its
    # 決定 [704,418,73,41]; メンバー上限 閉じる [597,456,87,39]; 目標ライブ third card (r13c15 -> 選択中 moved) and
    # 挑戦する [674,616,126,39] on both live pages; チーム情報 ← (r16c01 -> Hub). The first two live cards are the same
    # widget but were never tapped: unverified.
    "halo_main": ((40, 615, 240, 75), True),
    "halo_rest": ((370, 635, 105, 65), True),
    "halo_team_info": ((520, 588, 170, 55), True),
    # S2 disband (live 2026-11-15, user taps via explore_step): チーム情報 「チーム編集」 (text 1093,641 137x34),
    # チーム編集 「離脱させる」 under members 2-5 (text x 348/590/836/1081, y 529, 106x28), チーム離脱確認 「離脱させる」
    # (text 676,475 128x33); ← is the page's halo_back.
    "halo_team_edit": ((1100, 645, 123, 26), True),
    **{f"halo_leave_{i + 1}": ((354 + 245 * i, 532, 94, 22), True) for i in range(4)},
    "halo_leave_ok": ((684, 478, 112, 27), True),
    "halo_tab_lesson": ((40, 140, 175, 85), True),
    "halo_tab_audition": ((60, 305, 165, 85), True),
    "halo_tab_practice": ((70, 475, 150, 80), True),
    "halo_room_1": ((275, 422, 160, 148), True),
    "halo_room_2": ((441, 422, 161, 148), True),
    "halo_room_3": ((608, 422, 160, 148), True),
    "halo_room_4": ((768, 422, 160, 148), True),
    "halo_room_5": ((934, 422, 160, 148), True),
    "halo_room_6": ((1098, 422, 163, 148), True),
    "halo_card_prev": ((594, 305, 26, 62), True),
    "halo_card_next": ((916, 305, 28, 62), True),
    "halo_decide": ((1100, 628, 155, 62), True),
    "halo_report_next": ((1105, 630, 145, 58), True),
    "halo_goals_close": ((575, 605, 130, 60), True),
    "halo_recruit_join": ((865, 474, 205, 52), True),
    # 勧誘 candidate tiles (H2d live 2026-11-05): five tiles 150 px apart from x 450 under the buttons; the H1 user taps
    # r15c16 (tile 2) and r15c27 (tile 5) picked other candidates on this strip. Centre part only (the 勧誘済 label and
    # the S badge stay outside).
    **{f"halo_recruit_tile_{n}": ((475 + 150 * (n - 1), 560, 90, 70), True) for n in range(1, 6)},
    "halo_recruit_ok": ((670, 405, 140, 60), True),
    "halo_team_full_close": ((580, 450, 120, 58), True),
    # cards 1/2: card 3's verified rect moved up by the 175 px row pitch; the selection read after the tap guards it.
    "halo_live_card_1": ((440, 170, 180, 50), True),
    "halo_live_card_2": ((440, 345, 180, 50), True),
    "halo_live_card_3": ((440, 520, 180, 50), True),
    # Halo Hub 「ユニット確認」 (H1 Hub frames: text x10-124 y135-162): resume team check only (read-only page).
    "halo_unit_check": ((20, 138, 90, 21), True),
    "halo_live_challenge": ((672, 608, 130, 50), True),
    "halo_back": ((19, 625, 77, 76), True),
    # 成長 (H2c batch 3, H1 frames 2026-11-01; every target the user tapped there): Hub 成長 [1001,600,181,89] (r16c27,
    # 7 visits); ステータス >> of Vocal/Dance/Visual +10 (r05c14 / r08c14 / r11c14) and + of their 上限+10 (r05c27 /
    # r08c27 / r11c27, 12-16 taps each at S3W8); その他 tab (r03c17) and 親愛度+10 + (r05c28: 団結力 199 -> 169); 習得する
    # (r16c29) and the 成長確認 習得する (r15c18); ← is halo_back (r16c01).
    # 継承一覧 (H1 r03c05 20261101T100723: a tap on the empty upper left -> the S2 tutorial).
    "halo_inheritance_next": ((180, 115, 90, 50), True),
    "halo_growth_open": ((1015, 612, 150, 66), True),
    "halo_growth_stat_vo": ((560, 196, 38, 30), True),
    "halo_growth_stat_da": ((560, 323, 38, 30), True),
    "halo_growth_stat_vi": ((560, 450, 38, 30), True),
    "halo_growth_cap_vo": ((1098, 196, 34, 30), True),
    "halo_growth_cap_da": ((1098, 323, 34, 30), True),
    "halo_growth_cap_vi": ((1098, 450, 34, 30), True),
    "halo_growth_tab_other": ((655, 112, 90, 32), True),
    "halo_growth_affection": ((1140, 198, 36, 30), True),
    "halo_growth_learn": ((1110, 635, 140, 50), True),
    "halo_growth_confirm": ((668, 605, 145, 55), True),
    # say "Halo" start (H2c batch 2, 2026-11-04; RunOptions.halo_route + mode start). プロデュース選択: 特化プロデュース tab
    # and the card below the selected one (weekly Auto live 2026-10-30 on the same page: tab_special, scenario_down on
    # the 育成 list), 次へ (H1 r16c29 20261101T095123 -> ユニット編成). ノウハウブック選択: おまかせ編成 (H1 r16c15 095203 ->
    # Now Loading -> slot 1 filled) and 決定 (H1 r16c29 095219 -> アイテム準備); 設定履歴 / 呼び出し and プリセット / セット
    # use weekly Auto's live-verified rects on the same page (2026-10-30; there with S.T.E.P.'s three slots).
    "halo_tab_special": ((1015, 85, 155, 32), True),
    "halo_scenario_down": ((760, 490, 300, 70), True),
    "halo_scenario_next": ((1110, 632, 135, 60), True),
    "halo_book_auto": ((553, 640, 140, 32), True),
    "halo_book_history": ((718, 636, 162, 42), True),
    "halo_history_call": ((1010, 157, 116, 42), True),
    "halo_book_preset": ((30, 162, 145, 30), True),
    "halo_preset_set_1": ((770, 155, 95, 30), True),
    "halo_preset_set_2": ((770, 340, 95, 30), True),
    "halo_book_decide": ((1110, 632, 135, 60), True),
    # 带书「ノウハウ页指定」: the slot 1 「+」, the picker's first-screen tiles (3 rows x 5) and 決定 use weekly
    # Auto's live-verified rects of the same book page and picker (10-30, ``SC.WeeklyProduce.Setup.BookSlot1`` /
    # ``Picker.Tile<r><c>`` / ``Picker.Decide``); the Halo page shows the same 「+」 box (H2e 2026-11-15 frame).
    "halo_book_slot1": ((590, 300, 110, 100), True),
    **{f"halo_picker_tile_{r}_{c}": ((550 + 138 * c, 215 + 138 * r, 70, 50), True) for r in range(3) for c in range(5)},
    "halo_picker_decide": ((1100, 615, 120, 50), True),
    # Third demo: 総合評価 advances on a tap anywhere: (1130,654) #701 -> #703 FesDetail (clean), also
    # (762,501) #641 -> #642 ClearReward. Rect moved to the empty lower right (clear of the card art).
    "total_eval_tap": ((1060, 610, 140, 80), True),
    "clear_reward_ok": ((590, 585, 100, 60), True),
    "reward_ok": ((1094, 624, 167, 77), True),
    # 獲得報酬 review (2026-10-10, read-only taps + the protective 保護): rects derived from the live frame
    # wing_samples/2026-10-10/20261010T073201859397Z-capture.png (tabs Exスキル x 20–185 / y 90–165, ノウハウブック
    # y 177–252; first book tile (738,197,106,106)) and third demo #658 (detail pane 保護 button x 271–436 / y 645–690,
    # 売却 starts at x 452; 再抽選 button x 912–1077 / y 625–697). Never tapped by Maa yet -> all UNVERIFIED (the
    # Agent gate refuses them until a supervised live frame proves each one). Tiles 2–4 use an estimated 118 px pitch.
    # 再抽選 confirmation: no sample of the dialog at all; its OK rect is a placeholder (consuming: stamina 30).
    # Verified 2026-10-10 on the live 獲得報酬 frame 20261010T073201 (W.I.N.G. approval): Exスキル button x 20–185 /
    # y 90–165, ノウハウブック y 177–252, first book tile 738–844 / 197–303. Tiles 2–4 (118 px pitch) unseen.
    "reward_tab_ex": ((35, 100, 135, 55), True),
    "reward_tab_knowhow": ((35, 187, 135, 55), True),
    **{f"reward_item_{i + 1}": ((750 + 118 * i, 209, 82, 82), i == 0) for i in range(4)},
    "reward_book_protect": ((285, 650, 135, 36), False),
    "reward_reroll": ((930, 632, 130, 45), False),
    "reward_reroll_ok": ((655, 465, 160, 55), False),
    "fes_detail_ok": ((1100, 630, 150, 65), True),   # OK button x 1095–1255; (1118,669) #743 -> #744 (third demo)
    # 所持スキル (skill list + stats/caps) is left with the page's ← (second demo (50,669) #759->#768 clean, also
    # #2080->#2084/#2086): it returns to the page 振り返り was opened from. The old guess (1150,640) was never tapped.
    # Second demo: measured from the OCR boxes; verified only with a clean before/after click pair
    #. Item use is never automated: the confirm's キャンセル is the only
    # item target and stays unverified (no click on it in the recording).
    "cheer_close": ((560, 560, 160, 55), True),          # #203 (594,574) -> #206 ProduceSelect
    "active_skills_close": ((580, 570, 120, 60), True),  # #563 (621,611) -> #568 Schedule
    "item_popup_close": ((560, 555, 160, 60), True),     # #2462 (614,585) -> #2465 Hub
    # 使用確認 キャンセル: button x 462–627, y 534–610 on the native frame (second demo #2495; the user pressed 使う
    # (746,551) -> no clean cancel pair). Unverified: item use is never automated, the dialog only appears when the
    # user opens an item.
    "item_use_cancel": ((472, 550, 140, 40), False),
    # スキル入れ替え確認 (small dialog over 忘れるスキル選択): キャンセル x 517–628 / OK x 652–763, y 524–576 on the native
    # frame (second demo #744); no clean click on either -> both unverified.
    "replace_dialog_cancel": ((525, 532, 95, 38), False),
    "replace_ok": ((662, 532, 92, 38), False),
    # SP learning (2026-10-09b): 振り返り 所持スキル button (x 137–302, y 160–203; (214,186) #746->#748 clean), the info
    # bar's 習得する (x 1082–1245, y 612–688; (1137,629) #1297->#1304 LearnConfirm clean), スキル習得確認 OK (x 657–822,
    # y 488–563; (747,508) #1304->#1306 clean). 忘れるスキル 選択 rows (x 790–952; OCR #428/#1295 at y 109/247/382/521)
    # were never tapped in a clean pair -> unverified (forgetting a skill is irreversible: handed over until sampled).
    "skill_owned": ((145, 163, 150, 36), True),
    "skill_learn": ((1092, 620, 145, 58), True),
    "learn_ok": ((670, 498, 140, 55), True),
    # Verified 2026-10-14 (W.I.N.G. approval, live): 「オーディションへ」 spans x 558-722, y 518-593 on
    # wing_samples/2026-10-14/20261014T011509554503Z-capture.png (fan-shortfall warning popup); rect sits inside.
    "warning_to_audition": ((590, 535, 100, 40), True),
    # Verified 2026-10-14 live: 「振り返りへ」 spans x 558-722, y 518-593 on
    # wing_samples/2026-10-14/20261014T012659428696Z-capture.png (review warning popup); rect sits inside.
    "warning_to_review": ((590, 535, 100, 40), True),
    # Verified 2026-10-14 live (記者 event, wing_events/reporter-20261014/02-choice.png): 「取材を受けない」 OCR box
    # x 912-1140, y 222-258 on a floating button (± a few px); rect sits inside. The tap was sent the same way and
    # the refusal line 「やめておこう。」 followed.
    "reporter_refuse": ((950, 225, 150, 32), True),
    # Verified 2026-10-14 (W.I.N.G. approval, live): same-idol 確認 dialog OK button spans x 659-822, y 403-478 on
    # wing_samples/2026-10-13/20261013T193658889133Z-capture.png (unit20 S3 team edit); rect sits inside.
    "picker_confirm_ok": ((690, 420, 100, 40), True),
    # verified 2026-10-09 on the live frame dev_assets/private/wing-live-20261009/forget-select.png (選択 buttons
    # x 790-952, y 96-170 / 233-307 / 370-444 / 507-581 fully contain each rect)
    **{f"forget_select_{n + 1}": ((805, y, 130, 44), True) for n, y in enumerate((108, 246, 383, 520))},
    # Canonical 振り返り view node slots (agent.core.wing_skill_panel): lattice from 2 teams' native frames, tap rect
    # on the hexagon body. Node taps only select (info bar), e.g. #395 (742,447) -> #397, second demo #683 (702,389) -> #688.
    **{wsp.slot_intent(slot): (wsp.slot_rect(slot), True) for slot in wsp.SLOTS},
    # #624 -> #625 proves the zoom control. New small-slot input stays disabled pending supervised verification.
    # Verified 2026-10-14 live (supervised, user-approved): the zoom-out tap gave the centred small view (all six petals,
    # 43 labels on the small lattice, offset (-0.5,-0.2)); a node tap there opened its info bar; a background tap closed it.
    wsv.ZOOM_OUT: (wsv.ZOOM_RECT, True),
    wsv.INFO_CLOSE: (wsv.INFO_CLOSE_RECT, True),
    **{intent: (wsv.node_rect(slot), True) for intent, slot in wsv.SMALL_INTENTS.items()},
    # ユニット編成: 20 units on 2 tab pages (ユニット1–10 / 11–20). Tabs measured on demo #146 (OCR boxes at
    # x = 26 + 112·i, y 88). Page arrows re-measured on the third demo (#261: ‹ x ~1186–1210, › x ~1222–1250, y 90–120).
    # › was tapped once ((1245,109) -> #261 tabs 11–20) but the frame before the tap was a closing popup (#255), so the
    # pair is not clean; ‹ was never tapped -> stays unverified. 2026-10-12: the supervised recording
    # captures-20261011-164549Z has a clean › pair: #79 (tabs 1–10, no overlay) (1229,106) -> #82 tabs 11–20.
    # 2026-10-13 live (UI run ui-20261013-024420): the game opened on ユニット17 (page 2) and the ‹ glyph sits inside
    # this rect (~1188–1206, 95–115). Enabled with the user's explicit approval; the next round re-reads
    # unit_page and MAX_SAME_INTENT_IN_A_ROW stops a tap that does not turn the page.
    "unit_page_prev": ((1178, 85, 36, 40), True),
    "unit_page_next": ((1218, 85, 40, 40), True),
    # Team edit (RunOptions.team_edit, agent/core/wing_team_edit.py; recording captures-20261011-164549Z): a tap on a
    # slot's card icon opens the picker (P #85 (466,291) -> #88, S1 (592,293) -> #108, S2 (740,301) -> #169,
    # S3 (918,325) -> #177, S4 (1030,300) -> #218, guest (1204,298) -> #204), all clean pairs.
    **{wte.SLOT_INTENTS[slot]: (rect, True) for slot, rect in wte.SLOT_RECTS.items()},
    # Picker tiles (7 columns; list-top phase + two scrolled phases, wing_team_edit.PHASES) and the read-only picker
    # wheel; verified only where the recording has a clean tap or assets/input/wing_picker_verified.json records a
    # supervised live check (tools/wing_picker_enable.py).
    **{wte.tile_intent(r, c, ph): (wte.tile_rect(r, c, ph), wte.tile_intent(r, c, ph) in wte.VERIFIED_INTENTS)
       for ph, (_, _, rows) in wte.PHASES.items() for r in range(1, rows + 1) for c in range(1, wte.GRID_COLS + 1)},
    **{intent: (wte.SCROLL_RECT, intent in wte.VERIFIED_INTENTS) for intent in wte.SCROLL_INTENTS.values()},
    # Picker sort (RunOptions.picker_sort, agent/core/picker_sort.py): sort key button, the 並び替え key cells and 決定.
    # Clean pairs of captures-20261011-164549Z (wte.SORT_RECORDED): open, r2c2, r3c3, 決定; the other cells stay
    # unverified until assets/input/wing_picker_verified.json lists them (tools/wing_picker_enable.py).
    wte.SORT_OPEN_INTENT: (ps.SORT_OPEN_RECT, wte.SORT_OPEN_INTENT in wte.VERIFIED_INTENTS),
    **{wte.sort_key_intent(r, c): (ps.cell_rect(r, c), wte.sort_key_intent(r, c) in wte.VERIFIED_INTENTS)
       for r in range(1, 4) for c in range(1, 4)},
    wte.SORT_APPLY_INTENT: (ps.SORT_APPLY_RECT, wte.SORT_APPLY_INTENT in wte.VERIFIED_INTENTS),
    # Hub side menu ユニット確認 (read-only team view): #238 (83,124) -> #239 プロデュースユニット確認 (clean pair).
    "hub_unit_check": ((40, 95, 80, 60), True),
    # Schedule サポートスキル button -> 発動中スキル popup (second demo #558 (958,671) -> #559, clean pair; read-only).
    "schedule_support_skills": ((910, 630, 150, 62), True),
    # ノウハウブックマーク キャンセル (recording 2026-10-11 #355 (551,635) -> #357, dialog closed; OCR box 468–606 x 628–665).
    "bookmark_cancel": ((476, 616, 124, 54), True),
    **{f"unit_tab_{i + 1}": ((28 + 112 * i, 85, 95, 32), True) for i in range(10)},
    # Live (own policy, Auto OFF): hand cards, judges, 思い出 and the APPEAL timing tap. Positions from the demo's
    # Auto-OFF frames (#485–#498, ±6 px); a card/judge was never tapped in a
    # sample and the selected-card / timing screens are not captured -> all unverified (Act refuses even when enabled).
    # Second demo (manual turns, 2026-10-06): card tap select_card -> select_judge (#1946->#1947 card 1,
    # #2177->#2178 card 2, #2656->#2657 card 3); judge tap select_judge -> timing (#1952->#1953, #1972->#1973,
    # #2667->#2668 judge 2; #2202->#2203 judge 3); timing tap -> appeal (#2305 (683,431) -> #2306). The Vo judge
    # (slot 1) and the 思い出 button were never tapped in a clean pair -> still unverified.
    "live_card_1": ((314, 600, 211, 108), True),
    "live_card_2": ((538, 600, 217, 108), True),
    "live_card_3": ((768, 600, 211, 108), True),
    # 2026-10-09b: the Vo judge (slot 1, never the trend-#1 judge in the demos, so never tapped) is the same widget as
    # the clean-pair-verified judges 2/3: its box spans x 15–140, y 95–243 on every native Live frame (judge boxes
    # 95/262/430 tall 145; LIVE_JUDGE_BOXES) and the rect's inner 60 % (x 38–113, y 113–209) lies inside it. Verified
    # by geometry + the identical sibling widgets; the After guard still requires the timing phase next.
    "live_judge_1": ((13, 80, 125, 162), True),
    "live_judge_2": ((13, 242, 125, 162), True),
    "live_judge_3": ((13, 404, 125, 162), True),
    # 思い出 (memory gauge circle x 1160–1275, y 590–710; 100 % on #2209/#2681) starts a 「Good/Bad」 strip and a
    # Ready timing (only seen under the game's Auto) -> unverified; the focus policy never plays memory.
    "live_memory": ((1152, 586, 122, 122), False),
    "live_appeal_timing": ((440, 300, 400, 160), True),
    # Start flow (2026-10-09): the pink footer button (x 1093–1258, y 625–700) of
    # プロデュース選択 次へ (#206->#211 second demo, #228->#231 third demo), ユニット編成 次へ (#438->#439, #328->#329) and
    # アイテム準備 決定 (#329->#331, #369->#373 -> 使用確認). 使用確認 プロデュース開始 of the item-less layout (button
    # x 658–822, y 460–537; (751,482) #373->#374 Loading, third demo) is the only consuming intent (stamina 30).
    "produce_next": ((1110, 632, 135, 60), True),
    "unit_next": ((1110, 632, 135, 60), True),
    "item_decide": ((1110, 632, 135, 60), True),
    # ユニット編成 ← (button x ~20–96 / y ~625–700 on wing-live-20261025/unit20-s4-pale.png, the same widget and rect
    # as options_back on the Hub). Only the Auto 自动编队 prep (RunOptions.edit_only) taps it, once, to return to
    # プロデュース選択 where Auto starts; first live tap in that supervised run (user-approved W.I.N.G. targets).
    "unit_back": ((32, 638, 52, 50), True),
    "start_confirm_ok": ((668, 468, 144, 62), True),
    # ---- full-auto batch c (2026-10-09c). Home bottom bar (identical on both demos'
    # home frames: second #1, third #190): プロデュース x 995–1120 / y 605–700 — tapped once at (1046,672) while the home
    # was still under Now Loading (third #191 -> ProduceSelect #209), not a clean pair -> unverified; Pデスク・ミニゲーム
    # x 628–742 opens a small popup (never tapped in a recording: the popup was already open on second #1). The
    # popup's ミニゲーム (x 690–828, y 528–600): (741,574) second #1 -> #3 ミニゲーム (clean).
    "home_produce": ((1005, 615, 105, 70), True),   # 2026-10-12 live home frame: same button as home_produce_resume
    "home_minigame_menu": ((640, 615, 95, 75), True),
    "home_minigame": ((705, 540, 110, 50), True),
    # プロデュース選択: 育成プロデュース tab x 835–1005 / y 82–120 (third #209 (925,88) and second #193 (912,110) both
    # led to the 育成 page, but each with a second click before the next frame -> unverified); ミドル tab (x 778–960,
    # y 336–410): (878,379) third #221 -> #224 and (920,379) #227 -> #228, clean.
    "produce_tab_train": ((845, 85, 150, 32), True),   # 2026-10-12 live プロデュース選択 frame: 育成プロデュース tab x~835..1000, y~85..118
    "produce_difficulty_middle": ((800, 350, 140, 50), True),
    # ビギナー tab x ~590–770 / マスター x ~960–1145, y ~340–410 (third demo #227): (751,375) #217 -> #218 and
    # (660,397) #220 -> #221 read beginner; (1060,375) #226 -> #227 reads master (U01, 2026-10-12).
    "produce_difficulty_beginner": ((615, 352, 140, 48), True),
    "produce_difficulty_master": ((980, 352, 140, 48), True),
    # 大富豪 (second demo #3–#192): banner 大富豪で遊ぶ x 205–825 / y 130–435
    # ((655,358) #6 -> #8 clean); course 1 (マメ丸, 初心者はここから) 決定 x 192–352 / y 440–510 ((277,488) #43 -> #44
    # clean); in-game パス circle centre ~(520,470) ((549,448) #84->#85, #92->#93; (527,457) #107->#108, clean); result
    # 次へ x 562–720 / y 592–660 ((677,611) #161 -> #172 clean); プロデュースへ in two layouts: x 560–718 / y 548–618
    # ((651,581) #172 -> #173 clean) and 55 px lower with the 「プロデュース効果発生！」 notice ((639,616) #131 -> #136 clean).
    "minigame_daifugo": ((300, 250, 420, 110), True),
    "minigame_course_1": ((205, 450, 135, 50), True),
    "minigame_pass": ((485, 435, 75, 70), True),
    "minigame_touch_next": ((935, 655, 250, 45), True),   # live 2026-10-15 ゲーム終了 ranking page (TOUCH NEXT)
    "minigame_result_next": ((580, 598, 120, 50), True),
    "minigame_to_produce": ((575, 555, 125, 50), True),
    "minigame_to_produce_low": ((575, 610, 125, 50), True),
    # Full-screen スキル入れ替え確認 (first demo catalog: OK [706,616,66,43]); never tapped -> unverified.
    "replace_confirm_ok": ((710, 620, 58, 35), True),
    # プロデュース再開 (2026-10-10, agent/core/wing_resume.py): after a game reload the home button reads 「プロデュース中」
    # (button x 995–1120 / y 605–690, label [997,641,120,28] on restart3.png / after-restart.png) and opens the dialog
    # (sample-produce-resume-dialog.png): キャンセル x 381–545, 終了 557–722, 再開 737–900, y 594–668. Rects measured on
    # those native frames with >=12 px clearance from 終了 (user approved verifying W.I.N.G. targets from live frames);
    # each tap needs its label read inside the rect on the click frame and the After guard proves the next page.
    # 終了 (ends the Produce) has no intent and is never a target.
    "home_produce_resume": ((1005, 615, 105, 70), True),
    "resume_ok": ((750, 602, 138, 58), True),
    # ログイン ミッション達成 popup over the home (startup7.png): OK x455-618 y543-618 (ミッションへ x662-826 is never a target).
    "mission_popup_ok": ((475, 558, 120, 45), True),
    # キャンセル of the プロデュース再開 dialog (x 381–545 / y 594–668 above; verified 2026-10-09 live, user approved).
    # Not returned by any decision today (_decide_resume only taps 再開 or stops). The old note quoted the OK button's
    # x 658–822, which is not this rect.
    "resume_cancel": ((400, 602, 130, 58), True),
    # オプション read path (2026-10-14, agent/core/wing_options.py; recording captures-20261011-164549Z). Hub side menu
    # オプション icon: circle x ~1170–1250 / y ~207–293 on #294 (label 「オプション」 [1157,262,99,27]); the user's tap
    # (1204,231) after #294 -> #296/#298 Now Loading -> #299 オプション (プロデュース設定 selected, list top), no other
    # event between: clean pair, rect inside the circle -> verified. The list wheel (read-only, no button) and ← (button
    # x ~20–96 / y ~625–700 on #306; the user's (64,666) -> #310 Hub) stay unverified until a supervised live sample:
    # without all three the navigator never opens the page (wing_options.read_path_ready).
    "hub_options": ((1180, 215, 55, 50), True),
    "options_scroll_down": (wo.OPTIONS_SCROLL_RECT, True),  # 2026-10-14 live read (user-approved)
    "options_back": ((32, 638, 52, 50), True),  # clean pair #306 -> #310
    # 2026-10-25 settings fix (wing_options.FIX_VALUES, RunOptions.settings_fix, default off). Measured on the recording,
    # never tapped yet -> unverified until one supervised run (then True + wing_nav_build --enable-actions + new host).
    # OFF halves x 1030–1195: the unread row (label top 280 at the list top, #299) toggle y ~275–340; the popup row
    # (label top 504 at the list end, #306) toggle y ~487–550. 保存 button x ~1090–1252 / y ~625–700 (label
    # [1133,640,75,41]); 初期設定に戻す ends at x ~1057 (rect >= 50 px clear of it).
    # 2026-10-25 afternoon: enabled for the supervised first run (user present, chose 「Start a new W.I.N.G. now」).
    "options_unread_off": ((1055, 290, 115, 36), True),
    "options_popup_off": ((1055, 502, 115, 36), True),
    "options_save": ((1110, 642, 115, 42), True),
    # 2026-10-25 live (wing10 seg2): 保存 -> 「オプション / 設定を保存しました。」 with one button 閉じる (x ~558–722 /
    # y ~403–478, frame 20261025T035257995279Z); the user closed it by hand and asked Maa to close it itself.
    "options_saved_close": ((585, 418, 110, 45), True),
}
# Consuming intents: need RunOptions(mode="start", allow_start_spend=True), a frame-bound SpendGate grant for their
# table action and are attempted at most once per run (never retried, whatever the outcome).
CONSUMING_INTENTS = {"start_confirm_ok": "wing-produce-start", "reward_reroll_ok": "wing-reward-reroll-confirm"}


def action_id(intent: str) -> str:
    """Action-table id of a click intent (``wing-<intent>``; the Produce start has its own reviewed name)."""
    return CONSUMING_INTENTS.get(intent) or "wing-" + intent.replace("_", "-")


# Action-table family of each intent (a supervised run may admit a subset; the start is the only consuming family).
def intent_family(intent: str) -> str:
    if intent.startswith("giveup_"):
        return "wing-giveup"     # admitted only for a run with halo_giveup (tools/run_tasks.wing_task_actions)
    if intent.startswith("halo_"):
        return "wing-halo"       # admitted only for a run with halo_route
    if intent == "start_confirm_ok":
        return "wing-start"
    if intent in ("produce_next", "unit_next", "item_decide", "produce_tab_train")             or intent.startswith(("unit_", "home_", "minigame_", "produce_difficulty_", "picker_")):
        return "wing-setup"
    if intent.startswith("live_"):
        return "wing-live"
    if intent in ("hub_review", "learn_cancel", "replace_cancel", "forget_cancel", "replace_dialog_cancel",
                  "learn_ok", "replace_ok", "replace_confirm_ok") or intent.startswith(("skill_", "forget_select_")):
        return "wing-skill"
    if intent in ("eval_next", "fes_birth", "fes_birth_ok", "total_eval_tap", "clear_reward_ok", "reward_ok",
                  "fes_detail_ok", "fes_skill_bulk_cancel") or intent.startswith("reward_"):
        return "wing-end"
    if intent.startswith(("hub_", "schedule_", "audition_", "dialog_")) or intent == "page_return":
        return "wing-week"
    return "wing-nav"


WING_FAMILIES = ("wing-nav", "wing-week", "wing-live", "wing-skill", "wing-end", "wing-setup", "wing-start",
                 "wing-giveup", "wing-halo")
GIVEUP_HOLD_MS = 2500                # long press of 「諦める」 (the Match ＋ hold length, host range 300–4000 ms)
LIVE_HAND = 3
LIVE_SELECTED_WAITS = 6             # frames to re-read a just-tapped card before live_selected_card_unread stops
LIVE_UNREAD_LIMIT = 80              # consecutive Live frames without a play phase (cut-ins, appeal animations) -> stop
LIVE_JUDGES = 3
# Live play phases the reader must report (``readings["live_phase"]``); anything else stops the run (no guessing).
LIVE_PHASES = ("select_card", "select_judge", "timing")
RANKS = ("F", "E", "D", "C", "B", "A", "S")   # wing_state.RANKS order (low -> high)
UNITS_PER_PAGE = 10
AUDITION_PAGES = 6                  # observed maximum of the audition tab (S4, demo #763: 6 page dots)
UNIT_COUNT = 20
ROOM_SLOTS = ("lesson_vo", "lesson_da", "lesson_vi", "work_radio", "work_talk", "work_magazine")
# 2026-10-10 preview check: at most this many extra non-consuming room taps per week to read a room's real preview
# when its optimistic bound could beat the current choice (wing_score ``preview_check``).
PREVIEW_CHECK_MAX = 3

# Screens accepted after an action (success guard). Unknown frames (white/swan/HCG transitions) never satisfy it;
# the After node keeps polling within its timeout.
COMMU_FLOW = ("Commu", "CommuSkip", "CommuFastForwardOff", "Choice", "PromiseKept", "PromiseBroken", "PassCommu",
              "RankUpCommu", "SeasonEndCommu", "TrueEndCommu", "Loading", "WeekCard", "TimeCard", "NoticeNext",
              "ReporterChoice", "ReporterCommu")
WEEK_PAGES = ("Hub", "HubBanner", "HubSemiFinal", "HubFinal", "Schedule", "AuditionTab", "ScheduleBusy",
              "SupportSkillCutIn")
RESULT_FLOW = ("ResultJudge", "AuditionPass", "ResultList", "OnAir", "RankPyramid", "WingQualified")
LIVE_FLOW = ("Live", "LiveSkillCutIn", "ResultJudge", "Loading")
# Per-intent success guard of the Live play steps (screen + the phase that must follow). The Pipeline After node
# checks the screen family (LIVE_FLOW) and a changed frame; the Agent checks the phase on the next decision.
LIVE_PLAY_EXPECT = {
    "card": {"screens": ("Live",), "next_phase": ("select_judge",)},     # also a switch from select_judge
    "memory": {"screens": ("Live", "LiveSkillCutIn"), "next_phase": ("select_judge", "timing")},
    "judge": {"screens": ("Live",), "next_phase": ("timing",)},
    "timing": {"screens": LIVE_FLOW, "next_phase": ("select_card",)},
}
# Plain advance taps that spend nothing (2026-10-10 user rule: a simple variation -- e.g. the game skipping a scene
# the run has seen before, or a page the user changed by hand -- is re-observed, not a stop). Their success guard
# also accepts any *recognised* screen (an unknown frame still fails); the next round decides on what is shown.
REOBSERVE_INTENTS = frozenset({"commu_skip", "commu_ok", "commu_ff_toggle", "tap_textbox", "rank_next",
                               "result_list_tap", "eval_next", "total_eval_tap", "clear_reward_ok", "fes_detail_ok",
                               "popup_close", "cheer_close", "active_skills_close", "reward_tab_ex",
                               "reward_tab_knowhow", *(f"reward_item_{i}" for i in range(1, 5))})
# FesBirthSkillDialog: asked after フェスアイドル誕生 while a skill can still change (H2d live 2026-11-06: the Halo
# give-up's 誕生 tap stopped as clickUnconfirmed without it).
END_FLOW = ("Evaluation", "FesBirth", "FesBirthConfirm", "FesBirthSkillDialog", "TotalEval", "ClearReward", "Reward",
            "RewardDetail", "FesDetail", "TrueEndCommu", "ProduceEndCommu", "MissionPopup", "Home", "Loading",
            "EndDestructiveDialog")
SKILL_FLOW = ("Review", "LearnConfirm", "ReplaceConfirm", "ReplaceDialog", "ForgetSelect", "Owned", "Loading")


# say "Halo" success guards (H1 sequence 2026-11-01: the screens each tap led to). HaloTutorial frames read as Tutorial,
# 社長からの評価 as Evaluation, the season-end commu partly as SeasonEndCommu / PassCommu.
_HALO_WEEK_END = ("HaloHub", "HaloRecruit", "HaloSeasonEnd", "HaloReport", "HaloGoals", "Tutorial", "Loading",
                  "HubBanner", "ScheduleBusy", "SupportSkillCutIn", "WeekCard", "TimeCard") + COMMU_FLOW
_HALO_TABS = ("HaloLesson", "HaloPractice", "HaloAudition")
HALO_EXPECT = {
    "HaloHub": ("HaloLesson", "HaloLiveSelect", "HaloTeamInfo", "HaloGrowth", "UnitCheck", "AbandonDialog", "Tutorial",
                "Loading", "HubBanner",
                "WeekCard", "TimeCard") + COMMU_FLOW,
    "HaloLesson": _HALO_TABS + _HALO_WEEK_END,
    "HaloPractice": _HALO_TABS + _HALO_WEEK_END,
    "HaloAudition": _HALO_TABS + _HALO_WEEK_END,
    "HaloReport": ("HaloHub", "HaloSeasonEnd", "HaloRecruit", "HaloGoals", "Evaluation", "Loading", "HubBanner",
                   "WeekCard", "TimeCard") + COMMU_FLOW,
    "HaloSeasonEnd": ("HaloRecruit", "HaloHub", "HaloGoals", "Tutorial", "Loading", "HubBanner") + COMMU_FLOW,
    "HaloGoals": ("HaloHub", "HaloLesson", "FesBirth", "FesBirthConfirm", "Loading"),
    "HaloRecruit": ("HaloRecruitConfirm",),
    "HaloRecruitConfirm": ("HaloHub", "HaloTeamFull", "HaloRecruit", "HaloInheritance", "Tutorial", "Loading", "HubBanner") + COMMU_FLOW,
    # H2d live 2026-11-06: the S1-end 勧誘 -> チームメンバー上限 -> ノウハウ継承成功！ (継承一覧)
    "HaloTeamFull": ("HaloRecruit", "HaloHub", "HaloInheritance", "HaloGoals", "Tutorial", "Loading", "HubBanner"),
    "HaloLiveSelect": ("HaloLiveSelect", "HaloLiveConfirm"),
    "HaloLiveConfirm": ("HaloReport", "Loading", "Live", "LiveSkillCutIn") + COMMU_FLOW,
    "HaloTeamInfo": ("HaloHub", "HaloTeamEdit", "Loading"),
    "HaloTeamEdit": ("HaloTeamEdit", "HaloLeaveConfirm", "HaloTeamInfo", "Loading"),
    "HaloLeaveConfirm": ("HaloTeamEdit", "Loading"),
    "HaloGrowth": ("HaloGrowth", "HaloGrowthConfirm", "HaloHub", "Tutorial", "Loading"),
    "HaloInheritance": ("HaloHub", "HaloGoals", "HaloRecruit", "Tutorial", "Loading", "HubBanner") + COMMU_FLOW,
    "HaloGrowthConfirm": ("HaloGrowth", "Loading"),
}


@dataclasses.dataclass(frozen=True)
class ScreenRule:
    screen: str
    kind: str                      # wait | fixed | decide | stop | finish
    intent: str | None = None      # fixed intent (TARGETS key)
    point: str | None = None       # decision point (decide)
    expect: tuple = ()             # success guard screens after the action
    reason: str = ""               # stop / finish reason
    fallback: str = "stop"         # decide: what happens when the decision is "no click" (stop | wait)
    note: str = ""

    @property
    def loose(self) -> bool:
        """Success guard accepts any recognised screen (REOBSERVE_INTENTS; fixed advance taps only)."""
        return self.kind == "fixed" and self.intent in REOBSERVE_INTENTS


def _r(screen, kind, **kw) -> ScreenRule:
    return ScreenRule(screen, kind, **kw)


# Dispatch order = priority (overlays and popups before the page under them; specific commu before generic).
SCREENS: tuple[ScreenRule, ...] = (
    _r("Loading", "wait", note="Now Loading 覆盖层：等待"),
    # 大富豪 (batch c): played only in start mode with RunOptions.minigame="before_start" (one game, pass every turn:
    # finishing even as 大貧民 grants アイドルの応援, second demo #131); otherwise each page stops as before.
    _r("MinigameMenu", "stop", reason="minigame_not_automated",
       note="大富豪 メニュー / 破棄確認：Maa 不中断、不破弃；出现即停止"),
    _r("MinigameGameEnd", "decide", point="minigame_game_end", fallback="wait",
       expect=("MinigameGameEnd", "MinigameResult", "Loading")),
    _r("MinigameResult", "decide", point="minigame_result", fallback="wait",
       expect=("MinigameResult", "ProduceSelect", "ProduceSelectOther", "Loading", "CheerHelp")),
    _r("MinigameRules", "decide", point="minigame_rules", expect=("MinigameRules", "MinigameCourse")),
    _r("MinigameGame", "decide", point="minigame_play", fallback="wait",
       expect=("MinigameGame", "MinigameGameEnd", "MinigameResult", "Loading")),
    _r("MinigameTop", "decide", point="minigame_top", expect=("MinigameCourse", "MinigameRules", "Loading")),
    _r("MinigameCourse", "decide", point="minigame_course", expect=("MinigameGame", "MinigameRules", "Loading")),
    _r("HomeMinigamePopup", "decide", point="home", expect=("MinigameTop", "Loading")),
    # Third demo: modal dialogs that must never be answered by Maa (a false hit only stops).
    _r("AbandonDialog", "decide", point="abandon", expect=("FesBirth", "Loading"),
       note="ホームに戻る・諦める：「諦める」会立即结束 Produce 并诞生 Fes 偶像；只有 halo_giveup 且本次由 Maa 打开时长按"
            "「諦める」一次，否则停止交给用户"),
    _r("UnitEditDialog", "stop", reason="team_edit_not_automated",
       note="ユニット切り替え / ユニットコピー(確認/完了) / おまかせ編成：编队编辑只由用户进行"),
    _r("FesDetailPopup", "stop", reason="end_popup_not_automated",
       note="フェスアイドル詳細 上的 フェスアイドルマーク / プロデュースデッキアイドル詳細 弹窗：Maa 不打开，出现即停止"),
    _r("RewardSellMode", "stop", reason="reward_sell_mode",
       note="獲得報酬 売却模式：「売却する」与 OK 同位置；永不点击"),
    # 2026-10-12 (recording captures-20261011-164549Z): the ノウハウブックマーク dialog read as RewardDetail before
    # (#350–#355); キャンセル discards the mark change and closes it (#355 (551,635) -> #357, clean pair). The picker's
    # 並び替え dialog only has 決定 (changes the list order): Maa never opens it and stops if it shows.
    _r("BookmarkDialog", "fixed", intent="bookmark_cancel", expect=("RewardDetail", "Reward", "RewardSellMode"),
       note="ノウハウブックマーク：只点 キャンセル（不改书签）"),
    # 2026-10-14: answered only when this run opened it (RunOptions.picker_sort, team edit サポート picker): the
    # wanted key once, then 決定 once; a popup Maa did not open still stops (picker_sort_not_automated).
    _r("PickerSort", "decide", point="picker_sort", fallback="wait", expect=("PickerSort", "IdolPicker", "Loading"),
       note="选卡列表 並び替え：仅 picker_sort 时由 Maa 打开，点目标排序键一次再点 決定 一次；否则停止"),
    # say "Halo" in-run pages (H2a 2026-11-02): recognised before the W.I.N.G. pages that
    # share スケジュール選択 / 決定 / オーディション; popups first. H2b: played only with RunOptions.halo_route
    # (掘书线, agent/core/halo_policy.py); without it every page stops by name. 成長 (each stat its own
    # points; stat and cap compete) is not automated: the Hub never opens it and its pages stop.
    *(_r(screen, "decide", point="halo", fallback=fallback, expect=HALO_EXPECT[screen], note=note) if fallback
      else _r(screen, "stop", reason="halo_not_automated", note=note)
      for screen, fallback, note in (
          ("HaloRecruitConfirm", "stop", "勧誘確認：点 決定"),
          ("HaloTeamFull", "stop", "チームメンバー上限：点 閉じる"),
          ("HaloGrowthConfirm", "wait", "成長確認：Maa 自己点的 習得する 后点 習得する"),
          ("HaloLiveConfirm", "stop", "目標ライブ 挑戦確認：Maa 选好后点 挑戦する"),
          ("HaloLiveSelect", "wait", "目標ライブ挑戦確認：选成功率最高的一项，再点 挑戦する"),
          ("HaloGoals", "stop", "プロデュース目標：点 閉じる"),
          ("HaloAudition", "wait", "オーディション：逐张读卡（循环轮播），选成功率 100% 中粉丝最多的"),
          ("HaloPractice", "wait", "チーム合同練習：逐张读卡，按路线选练习（施設効率相談 / 総合 / みんなで休憩）"),
          ("HaloLesson", "wait", "レッスン&お仕事：选同行偶像给的所需アドバイス最多的房间，点房间再点 決定"),
          ("HaloRecruit", "stop", "勧誘：保留游戏默认的候选，点 チームに勧誘"),
          ("HaloGrowth", "wait", "成長：ステータス 按点数补 Vo/Da/Vi（先上限后 >>），その他 按路线买 親愛度+10，习得后返回"),
          ("HaloReport", "stop", "AUDITION REPORT：点 次へ"),
          ("HaloSeasonEnd", "stop", "はづき「シーズンNが終了しました」：点 OK"),
          ("HaloLeaveConfirm", "stop", "チーム離脱確認：Maa 自己点的 離脱させる 才点 離脱させる"),
          ("HaloTeamEdit", "wait", "チーム編集（S2 起）：逐个点 離脱させる 解散勧誘的成员，没有了点 ← 返回"),
          ("HaloTeamInfo", "stop", "チーム情報：读チーム完成度；S2 起没解散过就点 チーム編集，否则点 ← 返回"),
          ("HaloInheritance", "stop", "継承一覧（S1 结束后书的条目一览）：点左上空白处继续"))),
    _r("SkillBulkChange", "fixed", intent="fes_skill_bulk_cancel", expect=("FesBirth", "Loading"),
       note="スキルを一括で変更できます（フェスアイドル誕生 的スキル変更）：Maa 不改技能，点 キャンセル 回到 フェスアイドル誕生"),
    # -- popups / dialogs
    _r("StartConfirm", "decide", point="start_confirm",
       expect=("Loading", "Hub", "HubBanner", "Tutorial", "WeekCard", "TimeCard") + COMMU_FLOW,
       note="使用確認：消耗体力 30。只在 start 模式 + --allow-produce-spend + 无道具版式 + 1倍 + 体力差 30 时点击一次"),
    _r("IdolPickerConfirm", "decide", point="team_confirm", expect=("UnitSelect", "Loading", "IdolPicker"),
       note="同一偶像确认「ユニット内の同一のアイドルが外れてしまいます」：仅 team_edit 刚点的卡、每格一次按 OK"
            "（目标编队无重复偶像，被移出的必是待换格，随后补上）；其余一律停止"),
    _r("RestConfirm", "decide", point="rest_confirm", expect=("Hub", "HubBanner", "Loading", "WeekCard", "TimeCard")
       + COMMU_FLOW),
    _r("LiveConfirm", "decide", point="live_confirm", expect=LIVE_FLOW + ("HubSemiFinal", "HubFinal") + COMMU_FLOW),
    _r("LearnConfirm", "decide", point="sp_confirm", expect=SKILL_FLOW),
    _r("ReplaceConfirm", "decide", point="sp_replace", expect=SKILL_FLOW),
    _r("ReplaceDialog", "decide", point="sp_replace_dialog", expect=SKILL_FLOW),   # small dialog over ForgetSelect (second demo #744)
    _r("ForgetSelect", "decide", point="sp_forget", expect=SKILL_FLOW),
    _r("FesBirthConfirm", "decide", point="fes_birth_confirm", expect=END_FLOW),
    # H1 2026-11-01 (SP 324 left): asked before FesBirthConfirm when a skill can still be changed. Its 誕生 button is not
    # bound in the action table yet (H2c); before this it read as FesBirth and the page's 誕生 tap hit the dim layer.
    _r("FesBirthSkillDialog", "decide", point="fes_birth_skill", expect=("FesBirthConfirm", "FesBirth", "Loading"),
       note="「スキルを変更せずに…誕生させますか」：Maa 的 Halo 局（halo_route / halo_giveup）结束时点 フェスアイドル誕生（不改技能），"
            "否则停止交给用户"),
    # Produce items (second demo): the item popup is closed, use is never confirmed, a used item stops the run.
    _r("ItemUseConfirm", "decide", point="item_use", expect=("ItemPopup", "Hub", "HubBanner", "HubSemiFinal",
                                                               "HubFinal")),
    _r("ItemUsed", "stop", reason="item_used_unexpectedly", note="道具已使用（非 Maa 发起）：停止，不重试"),
    _r("ItemPopup", "fixed", intent="item_popup_close", expect=("Hub", "HubBanner", "HubSemiFinal", "HubFinal")),
    # 2026-10-13 (agent/core/wing_options.py): the page is read (プロデュース設定 rows) and the record updated; Maa never
    # taps on it -- every decision here is a stop with the settings result.
    # 2026-10-14: Maa's own read pass (wing_options.read_step) wheels the list and taps ←; never a toggle / 保存 /
    # 初期設定に戻す. The After guard is narrowed per intent (WingNavRuntime.expect_screens).
    _r("Options", "decide", point="options", fallback="wait", expect=("Options", "Hub", "HubBanner", "Loading"),
       note="オプション：只读取プロデュース設定（滚动列表、点 ←），Maa 不改设置、不点 保存"),
    # Sampled live 2026-10-14 (setting ON, S1 week 1 left, fans short): 「ちょっと待ってください！」 + 「ファン人数が足りず」
    # with one button 「オーディションへ」 -> the audition tab, where the normal audition decision follows. The 振り返り
    # variant is still unsampled: an unknown frame in this context stops with wing_warning_popup_unsampled.
    # Sampled live 2026-10-14 (S1 week 1 left, Schedule audition tab): 「ちょっと待ってください！」 +
    # 「オーディションを受ける前に、必ず振り返りをしてくださいね」 with one button 「振り返りへ」 -> 振り返り (SP), where the
    # normal SP decision runs before the audition. Same button rect as the fan variant.
    _r("WingReviewWarning", "fixed", intent="warning_to_review", expect=("Review", "Loading"),
       note="W.I.N.G.編 振り返り警告：只点「振り返りへ」，随后按正常 SP 决策"),
    _r(wo.WARNING_POPUP_SCREEN, "fixed", intent="warning_to_audition",
       # 2026-10-14 live (S1 last week, new run): オーディションへ led straight to the 振り返り warning
       expect=("AuditionTab", "Schedule", "AuditionLocked", "WingReviewWarning", "Loading"),
       note="W.I.N.G.編 ファン数未達成警告：只点「オーディションへ」，随后按正常试镜决策"),
    _r("CheerHelp", "fixed", intent="cheer_close", expect=("ProduceSelect", "ProduceSelectOther", "Loading")),
    _r("ActiveSkills", "fixed", intent="active_skills_close", expect=("Schedule",)),
    _r("Tutorial", "decide", point="tutorial", expect=("Tutorial", "ProduceSelect", "UnitSelect", "ItemSelect",
                                                         "IdolDetail", "Loading")),
    _r("ModeHelp", "fixed", intent="popup_close", expect=("ProduceSelect", "ProduceSelectOther", "Loading")),
    _r("ItemHelp", "fixed", intent="popup_close", expect=("ItemSelect",)),
    _r("AuditionHelp", "fixed", intent="popup_close", expect=("HelpList", "AuditionTab")),
    _r("BonusList", "fixed", intent="popup_close", expect=("UnitSelect", "UnitCheck")),
    _r("HelpList", "fixed", intent="page_return", expect=WEEK_PAGES + ("ProduceSelect",)),
    _r("Trend", "fixed", intent="popup_close", expect=("Hub", "HubBanner", "HubSemiFinal", "HubFinal"),
       note="流行確認 关闭方式未采样（目标沿用通用閉じる位置，未验证）"),
    _r("MissionPopup", "decide", point="mission_popup", expect=("Home", "Loading"), reason="produce_finished_home",
       note="首页 ミッション達成：「プロデュース中」仍在或 attach+from_home 未见 Produce 结束 -> 点 OK（登录任务）；否则 Produce 结束；不点 ミッションへ"),
    # -- end of produce (third demo: ranked before the commu family; their OK buttons sit where a commu OK sits)
    # The destructive dialog leads the section: after the named help popups (アイドルの応援とは mentions ジュエル),
    # before every end page / commu whose OK sits at the same place.
    # 再抽選 confirmation (no sample): only Maa's own planned re-roll is answered; anything else stops.
    _r("RewardRerollConfirm", "decide", point="reward_reroll_confirm", expect=END_FLOW,
       note="再抽選 确认（消耗体力 30）：只有设置开启、Maa 本次计划的再抽選、读到 30 时才点 OK；无样本，目标未验证"),
    _r("EndDestructiveDialog", "stop", reason="produce_end_destructive_dialog",
       note="结束流程的 売却/移籍/削除/再抽選/所持上限/ジュエル 模态框：停止，不取消、不重试"),
    _r("Evaluation", "fixed", intent="eval_next", expect=END_FLOW),
    _r("FesBirth", "decide", point="fes_birth", expect=END_FLOW),
    _r("TotalEval", "fixed", intent="total_eval_tap", expect=END_FLOW),
    _r("ClearReward", "fixed", intent="clear_reward_ok", expect=END_FLOW),
    _r("Reward", "decide", point="produce_end", expect=END_FLOW + ("RewardRerollConfirm",), fallback="wait",
       note="獲得報酬：读 Exスキル/ノウハウブック（页签与逐本详情，只读）、Lv5 书自动保護；再抽選 仅在设置开启时；売却 永不；"
            "默认读完交给用户（produce_end_review）"),
    _r("RewardDetail", "decide", point="produce_end", expect=END_FLOW + ("RewardRerollConfirm",), fallback="wait",
       note="獲得報酬 的书详情 / Exスキル 页签（用户在操作）：与 Reward 同一闸门"),
    _r("FesDetail", "fixed", intent="fes_detail_ok", expect=END_FLOW),
    _r("ProduceEndCommu", "fixed", intent="tap_textbox", expect=END_FLOW,
       note="Produce 结束后はづき「プロデュースお疲れ様でした」（say \"Halo\" 放弃后 2026-11-01；W.I.N.G. 第三次演示 #744 同一画面）：点文本框"),
    _r("AuditionLocked", "decide", point="audition_locked", expect=("AuditionTab", "AuditionLocked", "Schedule",
                                                                    "ScheduleBusy")),
    # -- cut-ins over pages
    _r("SupportSkillCutIn", "wait"),
    _r("LiveSkillCutIn", "wait"),
    # -- commu family (specific first)
    _r("TrueEndCommu", "wait", note="True End 证据（TrueEnd 徽章）；早送り推进"),
    _r("Choice", "decide", point="event", expect=COMMU_FLOW + WEEK_PAGES),
    _r("PromiseKept", "wait"),
    _r("PromiseBroken", "wait"),
    _r("RankUpCommu", "fixed", intent="commu_ok", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW),
    _r("SeasonEndCommu", "fixed", intent="commu_ok", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW),
    # + END_FLOW: live 2026-10-10 seg41, the commu after the W.I.N.G. final led to 社長からの評価 (clickUnconfirmed).
    _r("CommuSkip", "fixed", intent="commu_skip", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW + LIVE_FLOW + END_FLOW),
    _r("CommuFastForwardOff", "fixed", intent="commu_ff_toggle", expect=COMMU_FLOW),
    _r("Commu", "wait", note="早送り ON：文字自动推进"),
    # 2026-10-14 live (S3 week start): 記者 event 「密着取材の依頼」 is a commu without the commu menu, then a two-button
    # choice. User default: refuse (敏腕/悪徳 reporter is random). Accepting is not automated.
    _r("ReporterChoice", "fixed", intent="reporter_refuse", expect=COMMU_FLOW + WEEK_PAGES,
       note="記者取材依頼：固定点「取材を受けない」"),
    # -- audition / live results
    _r("AuditionPass", "wait"),
    _r("OnAir", "wait"),
    _r("ResultList", "fixed", intent="result_list_tap", expect=RESULT_FLOW + COMMU_FLOW),
    _r("PassCommu", "fixed", intent="commu_ok", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW + END_FLOW),
    _r("ResultJudge", "fixed", intent="tap_textbox", expect=RESULT_FLOW + COMMU_FLOW),
    _r("WingQualified", "fixed", intent="tap_textbox", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW),
    _r("WeekCard", "wait"),
    _r("TimeCard", "wait"),
    _r("RankPyramid", "fixed", intent="rank_next", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW + END_FLOW),
    _r("Live", "decide", point="live", fallback="wait", expect=LIVE_FLOW),
    # -- produce pages
    _r("HubSemiFinal", "decide", point="final", expect=("LiveConfirm", "Review", "Loading")),
    _r("HubFinal", "decide", point="final", expect=("LiveConfirm", "Review", "Loading")),
    _r("HubBanner", "wait", note="「シーズンN 残りN週」横幅：过渡中不点"),
    _r("HaloHub", "decide", point="halo_hub", expect=HALO_EXPECT["HaloHub"],
       note="say \"Halo\" 的 Hub：halo_route 时按路线选 プロデュース / ライブ / 休む / チーム情報 / 諦める；halo_giveup 时打开"
            "「諦める ホームに戻る」一次；都没有则停止"),
    _r("Hub", "decide", point="week", expect=("Schedule", "AuditionTab", "RestConfirm", "Review", "Loading",
                                               "UnitCheck", "Options")),
    _r("Schedule", "decide", point="week", expect=("Schedule", "AuditionTab", "ScheduleBusy", "SupportSkillCutIn",
                                                    "Hub", "HubBanner", "Loading", "ActiveSkills") + COMMU_FLOW),
    # ScheduleBusy: the tab / page slide reads as the busy schedule page for a frame (second demo #1262, #1270).
    _r("AuditionTab", "decide", point="audition", expect=("AuditionTab", "AuditionLocked", "Schedule", "Hub",
                                                          "Loading", "Live", "ScheduleBusy") + COMMU_FLOW),
    _r("ScheduleBusy", "wait", note="课程/工作执行动画（仍显示 スケジュール選択）"),
    _r("Review", "decide", point="sp", fallback="wait", expect=SKILL_FLOW + WEEK_PAGES),
    _r("Owned", "decide", point="owned", expect=WEEK_PAGES + ("Review", "Loading")),
    _r("SkillPanelReadOnly", "fixed", intent="page_return", expect=("UnitCheck",)),
    _r("UnitCheck", "fixed", intent="page_return", expect=WEEK_PAGES + ("HaloHub",)),
    _r("IdolDetail", "fixed", intent="page_return", expect=("UnitCheck", "UnitSelect")),
    _r("SupportDetail", "fixed", intent="page_return", expect=("UnitCheck", "UnitSelect", "Loading")),
    # -- setup (supervised: the user starts the Produce; Maa verifies and stops)
    _r("ProduceSelect", "decide", point="setup", fallback="wait",
       expect=("UnitSelect", "ProduceSelect", "ProduceSelectOther", "Loading")),
    _r("ProduceSelectOther", "decide", point="setup_tab", fallback="wait",
       expect=("ProduceSelect", "ProduceSelectOther", "UnitSelect", "Loading"),
       note="プロデュース選択（特化页等）：start 模式点 育成プロデュース 页签；say \"Halo\" 开局（halo_route）点 特化 → "
            "列表下移到 say \"Halo\" → 次へ；否则停止"),
    # Team edit (start + team_edit only; otherwise the picker stops as before).
    _r("IdolPicker", "decide", point="team_pick", fallback="wait",
       expect=("UnitSelect", "Loading", "IdolPickerConfirm", "IdolPicker"),
       note="编队选卡：team_edit 时按卡面识别找目标卡（只读滚动查找，先核对全部要换的支援卡），点一次；找不到或不可点即停止"),
    _r("UnitSelect", "decide", point="setup", fallback="wait",
       expect=("UnitSelect", "ItemSelect", "BookSelect", "Loading", "IdolPicker", "ProduceSelect",
               "ProduceSelectOther")),
    # say "Halo" book page (H2c batch 2): one ノウハウブック from the game's おまかせ編成 / newest 設定履歴 / プリセット1, 2.
    _r("BookPreset", "decide", point="book", fallback="wait", expect=("BookSelect", "BookPreset", "Loading"),
       note="ノウハウブックプリセット：halo_book=preset1/2 时点该行 セット 一次；否则停止"),
    _r("BookHistory", "decide", point="book", fallback="wait", expect=("BookSelect", "BookHistory", "Loading"),
       note="ノウハウブック設定履歴：halo_book=history 时点最新一组 呼び出し 一次；否则停止"),
    _r("BookPicker", "decide", point="book_picker", fallback="wait", expect=("BookPicker", "BookSelect", "Loading"),
       note="选书（带书「ノウハウ页指定」）：按評価値在第一屏找书，点一次后核对书名与 Vo/Da/Vi/Me，不对就取消再找；"
            "排序不是評価値降順、书不在第一屏或已选的书不对即停止"),
    _r("BookSelect", "decide", point="book", fallback="wait",
       expect=("BookSelect", "BookHistory", "BookPreset", "BookPicker", "ItemSelect", "Loading"),
       note="ノウハウブック選択（say \"Halo\" 1 本）：按 halo_book 放书（おまかせ編成 / 設定履歴 / プリセット），"
            "读到第 1 格已填才点 決定；W.I.N.G. 没有这一页，其他运行停止"),
    _r("ItemSelect", "decide", point="setup", expect=("StartConfirm", "Loading")),
    # -- outside Produce
    _r("ResumeDialog", "decide", point="resume",
       expect=("Loading",) + WEEK_PAGES + ("Review",) + COMMU_FLOW + tuple(s for s in HALO_EXPECT if s.startswith("Halo")),
       note="プロデュース再開：读 P 卡/剧本难度/季周/数值与任务配置比对；一致点 再開，不一致停止（resume_config_mismatch）；永不点 終了"),
    _r("IdolList", "stop", reason="outside_produce", note="首页 アイドル一覧（持有卡牌扫描用页面）"),
    _r("TitleScreen", "stop", reason="outside_produce"),
    _r("Notice", "stop", reason="outside_produce"),
    _r("Home", "decide", point="home", expect=("HomeMinigamePopup", "ProduceSelect", "ProduceSelectOther", "Loading",
                                                "ResumeDialog"),
       note="首页：from_home 时 プロデュース中 → 再開对话框；start 模式 + from_home 才去 大富豪 / プロデュース，否则停止 outside_produce"),
    # はづき notice commu with a 「次へ」 button (live 2026-10-10 S4 end): rank_next's rect lies inside that button.
    # Last in dispatch order: 「次へ」 in that corner also appears on screens with their own, stronger guards.
    # 2026-10-12 (recording captures-20261011-164549Z #78): a setup page mid-slide read only as 「次へ」 and fell to
    # NoticeNext, whose rank_next rect is the setup 次へ (skipping the unit check). Setup headers now wait first.
    _r("SetupTransition", "wait", note="设置页（プロデュース選択/ユニット編成/アイテム準備）过渡帧：等待，不点 次へ"),
    _r("NoticeNext", "fixed", intent="rank_next", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW),
    # 2026-10-14 live (記者 event): a commu text box (speaker label + interview text) without the commu menu. Very
    # last: every page with its own guard wins first; the tap only advances text (spends nothing, re-observed like
    # tap_textbox). Other menu-less lines of the event are still unknown frames (sampled, then a safe stop).
    _r("ReporterCommu", "fixed", intent="tap_textbox", expect=COMMU_FLOW + WEEK_PAGES + RESULT_FLOW,
       note="記者事件剧情（无菜单）：点文字框推进"),
)
RULES = {r.screen: r for r in SCREENS}
# Screens that exist only inside (or at the end of) a running Produce: proof that a プロデュース開始 took effect
# (spend ledger, agent.core.wing_ledger). Loading, Tutorial and the home/setup pages are never proof.
IN_PRODUCE_SCREENS = frozenset(
    set(WEEK_PAGES) | set(COMMU_FLOW) | set(RESULT_FLOW) | set(LIVE_FLOW) | set(END_FLOW)
    | {"Review", "Owned", "UnitCheck", "SkillPanelReadOnly", "LearnConfirm", "ReplaceConfirm", "ReplaceDialog",
       "ForgetSelect", "RestConfirm", "LiveConfirm", "ItemPopup", "ItemUseConfirm", "Trend", "ActiveSkills",
       "AuditionLocked", "Choice", "AuditionHelp", "HelpList"}) - {"Loading", "Home", "MissionPopup"}
# Screens that feed / are gated like another end screen (same readings, same produce_end gate).
END_ALIASES = {"RewardDetail": "Reward"}

# Every click intent a decision point may return, per screen. tools/wing_nav_build.py generates one table-bound Click
# node per (screen, intent); a decision outside this set has no node and stops (``intent_not_bound``).
_POINT_INTENTS = {
    "rest_confirm": ("dialog_ok", "dialog_cancel"),
    "live_confirm": ("dialog_ok",),
    "options": (wo.READ_INTENTS["scroll"], wo.READ_INTENTS["back"], *wo.FIX_INTENTS.values(), wo.SAVE_INTENT,
                wo.SAVED_CLOSE_INTENT),
    "sp_confirm": ("learn_ok", "learn_cancel"),
    "sp_replace": ("replace_confirm_ok", "replace_cancel"),
    "sp_replace_dialog": ("replace_ok", "replace_dialog_cancel"),
    "sp_forget": ("forget_select_1", "forget_select_2", "forget_select_3", "forget_select_4", "forget_cancel"),
    "fes_birth_confirm": ("fes_birth_ok",),
    "halo_hub": ("giveup_open",),
    "halo": (),
    "fes_birth_skill": ("fes_birth_ok",),
    "abandon": ("giveup_hold",),
    "item_use": ("item_use_cancel",),
    "tutorial": ("tutorial_next", "popup_close"),
    "fes_birth": ("fes_birth",),
    "produce_end": ("reward_ok", "reward_tab_ex", "reward_tab_knowhow", *(f"reward_item_{i}" for i in range(1, 5)),
                    "reward_book_protect", "reward_reroll"),
    "reward_reroll_confirm": ("reward_reroll_ok",),
    "audition_locked": ("audition_next", "schedule_lesson_tab"),
    "event": ("choice_left", "choice_center", "choice_right"),
    "live": ("live_auto", "live_card_1", "live_card_2", "live_card_3", "live_memory", "live_judge_1", "live_judge_2",
             "live_judge_3", "live_appeal_timing"),
    "final": ("hub_review", "hub_final_live"),
    "audition": ("schedule_decide", "audition_next", "audition_prev", "schedule_lesson_tab"),
    "sp": ("page_return", "skill_owned", "skill_learn", wsv.ZOOM_OUT, wsv.INFO_CLOSE, *wsp.SLOT_INTENTS,
           *wsv.SMALL_INTENTS),
    "owned": ("page_return",),
    "start_confirm": ("start_confirm_ok",),
    "team_confirm": ("picker_confirm_ok",),
    "picker_sort": wte.SORT_POPUP_INTENTS,
    "home": ("home_produce", "home_minigame_menu", "home_minigame", "home_produce_resume"),
    "resume": ("resume_ok", "resume_cancel"),
    "mission_popup": ("mission_popup_ok",),
    "setup_tab": ("produce_tab_train", "halo_tab_special", "halo_scenario_down", "halo_scenario_next"),
    "book": ("halo_book_auto", "halo_book_history", "halo_book_preset", "halo_book_decide", "halo_book_slot1"),
    "book_picker": (*(f"halo_picker_tile_{r}_{c}" for r in range(3) for c in range(5)), "halo_picker_decide"),
    "minigame_top": ("minigame_daifugo",),
    "minigame_course": ("minigame_course_1",),
    "minigame_rules": ("tutorial_next", "popup_close"),
    "minigame_play": ("minigame_pass",),
    "minigame_game_end": ("minigame_touch_next",),
    "minigame_result": ("minigame_result_next", "minigame_to_produce", "minigame_to_produce_low"),
}
_SCREEN_INTENTS = {
    "Hub": ("hub_schedule", "hub_rest", "hub_review", "hub_unit_check", wo.READ_INTENTS["open"]),
    "Schedule": ("schedule_room_1", "schedule_room_2", "schedule_room_3", "schedule_room_4", "schedule_room_5",
                 "schedule_room_6", "schedule_decide", "schedule_audition_tab", "page_return",
                 "schedule_support_skills"),
    "ProduceSelect": ("produce_difficulty_beginner", "produce_difficulty_middle", "produce_difficulty_master",
                      "produce_next", "halo_tab_special"),
    "BookHistory": ("halo_history_call",),
    "BookPreset": ("halo_preset_set_1", "halo_preset_set_2"),
    "UnitSelect": ("unit_page_prev", "unit_page_next", *(f"unit_tab_{i}" for i in range(1, 11)), "unit_next", "unit_back",
                   *wte.SLOT_INTENTS.values()),
    "IdolPicker": wte.PICKER_INTENTS,
    "Home": ("home_produce", "home_minigame_menu", "home_produce_resume"),
    "HomeMinigamePopup": ("home_minigame",),
    "ItemSelect": ("item_decide",),
    # say "Halo" (H2b, halo_route only)
    "HaloHub": ("halo_main", "halo_rest", "halo_team_info", "giveup_open", "halo_growth_open", "halo_unit_check"),
    "HaloLesson": (*(f"halo_room_{i}" for i in range(1, 7)), "halo_decide", "halo_tab_practice",
                   "halo_tab_audition"),
    "HaloPractice": ("halo_card_prev", "halo_card_next", "halo_decide", "halo_tab_lesson", "halo_tab_audition"),
    "HaloAudition": ("halo_card_prev", "halo_card_next", "halo_decide", "halo_tab_lesson", "halo_tab_practice"),
    "HaloReport": ("halo_report_next",),
    "HaloSeasonEnd": ("commu_ok",),
    "HaloGoals": ("halo_goals_close",),
    "HaloRecruit": ("halo_recruit_join", *(f"halo_recruit_tile_{n}" for n in range(1, 6))),
    "HaloRecruitConfirm": ("halo_recruit_ok",),
    "HaloTeamFull": ("halo_team_full_close",),
    "HaloLiveSelect": ("halo_live_card_1", "halo_live_card_2", "halo_live_card_3", "halo_live_challenge"),
    "HaloLiveConfirm": ("halo_live_challenge",),
    "HaloTeamInfo": ("halo_back", "halo_team_edit"),
    "HaloTeamEdit": ("halo_back", *(f"halo_leave_{i}" for i in range(1, 5))),
    "HaloLeaveConfirm": ("halo_leave_ok",),
    "HaloInheritance": ("halo_inheritance_next",),
    "HaloGrowth": (*(f"halo_growth_{kind}_{c}" for kind in ("stat", "cap") for c in ("vo", "da", "vi")),
                   "halo_growth_tab_other", "halo_growth_affection", "halo_growth_learn", "halo_back"),
    "HaloGrowthConfirm": ("halo_growth_confirm",),
}


def screen_intents(screen: str) -> tuple:
    """Click intents of one screen (empty for wait/stop/finish screens)."""
    rule = RULES[screen]
    if rule.kind == "fixed":
        return (rule.intent,)
    if rule.kind != "decide":
        return ()
    return _SCREEN_INTENTS.get(screen) or _POINT_INTENTS[rule.point]


SP_MODES = ("handover", "skip", "auto")
DIFFICULTY_CHOICES = ("beginner", "middle", "master")
# say "Halo" book source: the game's おまかせ編成 (recommended), the newest 設定履歴, in-game プリセット1/2.
HALO_BOOKS = ("auto", "history", "preset1", "preset2", "target")
HALO_BOOK_KEYS = ("title", "vo", "da", "vi", "me", "eval")
MINIGAME_MODES = ("off", "before_start")


@dataclasses.dataclass(frozen=True)
class RunOptions:
    """How one navigator run may act (set by the supervised launcher entry; the defaults never send input).

    ``mode``: ``attach`` (default; the user started the Produce, Maa takes over on any in-run screen and stops on the
    setup pages) or ``start`` (Maa also walks プロデュース選択 → ユニット編成 → アイテム準備 → 使用確認). ``input_enabled`` and
    ``allow_start_spend`` come from the launcher, never from Pipeline parameters. ``sp_mode``: ``handover`` stops for
    the user's own skill learning; ``skip`` continues without spending SP; ``auto`` learns on the canonical 振り返り
    panel with ``wing_sp.plan_sp`` (P skills > S Live skills > caps > passives, spend all; a forget that is not
    sampled yet, an unreadable panel or an unconfirmed result hands over / stops).
    ``sp_done``: this attach follows the user's own learning — one pending SP request is treated as handled.
    ``segment_acts``: stop at the next week start (Hub) once this many clicks were sent (a clean segment boundary
    below the launcher's task cap).

    Batch c (2026-10-09c, all off by default; ``start`` mode only): ``from_home`` lets a start run begin on the home
    screen (プロデュース → 育成 W.I.N.G. ミドル); ``minigame="before_start"`` first plays one 大富豪 game from the home
    screen (pass every turn; finishing grants アイドルの応援 for the next W.I.N.G. Produce) and continues with
    プロデュースへ; ``unit_search`` looks through units 1–20 for the configured team when the configured unit does not
    hold it (or no unit is configured).

    Mid-Produce detection (2026-10-09c, user: 「加上能识别中途produce的功能…如果和任务配置不一样要给出警告选择继续还是中断重开」):
    the first Hub of a run that did not start the Produce itself (and does not continue its own saved segment) reads
    the P idol from the Hub speech bubble and compares it with the configured P card. A mismatch — or, in start mode,
    an unidentified Produce already in progress — stops with ``resume_config_mismatch`` /
    ``resume_identity_unverified`` and a ``resume`` warning in the report; ``resume_mismatch_ok`` is the user's
    「继续」 answer (the UI prompt is wired later). Maa never abandons a Produce (「中断重开」 is the user's)."""
    mode: str = "attach"
    input_enabled: bool = False
    allow_start_spend: bool = False
    team_confirmed: bool = False
    sp_mode: str = "handover"
    sp_done: bool = False
    max_acts: int = MAX_ACTS_PER_RUN
    segment_acts: int | None = None
    from_home: bool = False
    minigame: str = "off"
    unit_search: bool = False
    resume_mismatch_ok: bool = False
    # 2026-10-12: edit the configured unit to hold the configured team (start mode; agent/core/wing_team_edit.py).
    team_edit: bool = False
    # 2026-10-30 Auto 自动编队 (tools/ui_wing.team_prep_run_config): edit the unit, then stop there (team_edit_done)
    # instead of 次へ; the official Auto then uses that unit. Never reaches the start. With picker_sort the サポート
    # picker is sorted 「Lv順」 (wing_team_edit.EDIT_ONLY_SORT_KEY), not by attribute.
    edit_only: bool = False
    # 2026-10-12: read the formed team once on the first Hub (ユニット確認, read-only) for the support identities.
    read_team: bool = False
    # 2026-10-13 (user, 「Produce」 page): a Produce already in progress is continued only when it is verifiably the
    # configured one -- the P idol (Hub speech bubble / プロデュース再開 card line), the P card and the configured S/Guest
    # (read-only ユニット確認, wing_unit_check). Anything unverifiable stops in every mode (never abandons, never
    # continues on a guess). tools/ui_wing.py always sets it; off keeps the CLI's attach behaviour.
    verify_resume: bool = False
    # Launcher authority (never a Pipeline parameter): the 再抽選 confirmation may be answered (stamina 30). The
    # W.I.N.G. setting 「再抽選」 must also be on; both default off. tools/run_tasks.py derives it from that setting
    # but the targets stay unverified and the table actions disabled, so no re-roll is ever tapped yet.
    allow_reward_reroll: bool = False
    # 2026-10-14 (user): in the team-edit サポート picker, switch the game's sort to the wanted cards' main attribute
    # (Vocal順 / Dance順 / Visual順 / メンタル順 ▼降順) before scanning (agent/core/picker_sort.py). Off by default. The game
    # saves the sort (shared with the training list); the run logs the original order (picker_sort_left).
    picker_sort: bool = False
    # 2026-10-25 (user, W.I.N.G. switch 「自动改游戏设置」): on the first Hub set 未読イベント早送り解除 / 警告ポップアップ
    # OFF, save, re-read and keep them (wing_options.FIX_VALUES). Off by default; needs the verified fix targets.
    settings_fix: bool = False
    # 2026-11-01 (user, say "Halo" knowhow route): on the Halo Hub open 「諦める ホームに戻る」 once and long-press
    # 「諦める」 in the dialog Maa opened (wing-giveup-*, admitted only with this option), then the usual end pages
    # (press_ok) to Home. The Halo run itself is not automated: without this option its Hub stops. Off by default.
    halo_giveup: bool = False
    # 2026-11-02 (H2b): play the say "Halo" run with agent/core/halo_policy.py -- "dig" (掘书线: advice, 施設効率相談 /
    # +, チーム完成度, then the give-up), the only route since 2026-11-07 (user 「默认不要强fes线 只做掘书线」). Halo taps form their own family (wing-halo), admitted only with this option; 成長 stays manual. Not with
    # halo_giveup (the dig route gives up by itself). Off by default.
    halo_route: str | None = None
    # 2026-11-04 (user, H2c batch 2): the say "Halo" start (mode start + halo_route) brings one ノウハウブック from this
    # source (HALO_BOOKS); the P idol and team come from the W.I.N.G. settings. "target": the book the
    # ノウハウ page saved (``halo_book_plan``: title, Vo/Da/Vi/Me, 評価値), found on the picker's first screen.
    halo_book: str = "auto"
    halo_book_plan: dict | None = None
    # H2c batch 5: the dig route's goals (halo_policy.Goals fields; tools/ui_wing.py derives them from the
    # user's wanted list). None: the Goals defaults.
    halo_goals: dict | None = None
    sp_small_view: bool = True           # 2026-10-14 user: zoom out to reach every petal (supervised sample passed)
    live_timed_click: bool = True       # host-clock APPEAL tap (2026-10-15: the sleep-aligned path landed Normal live)
    live_timing_period_s: float = 0.97
    # 2026-10-14 burst: judged on Up; Down->Up hold 149-153ms, hits 155-164ms late -> 150. 2026-10-27/28 (holds
    # 121-160 ms): every Good on a 35 px Perfect zone stopped 50-80 ms early (6/11 Good; 87 px zones 78/79 Perfect,
    # their few measured stops -4..+38 ms) -> 100 (tools/appeal_timing_report.py). 2026-10-29 at 100 (wing5/5b/6, 45 taps):
    # 29 Perfect / 8 Good / 6 Normal / 1 Bad -- every 35 px Good stopped 22-29 px late and six 87 px taps wrapped to the
    # bar start -> back to 150 (93 taps, 84 Perfect); the narrow zone stays about half Good.
    live_timing_event_offset_ms: float = 150.0

    def __post_init__(self):
        if self.mode not in ("attach", "start"):
            raise ValueError("mode 只能为 attach/start")
        if self.sp_mode not in SP_MODES:
            raise ValueError("sp_mode 只能为 handover/skip/auto")
        if self.minigame not in MINIGAME_MODES:
            raise ValueError("minigame 只能为 off/before_start")
        # 2026-10-10: from_home also serves attach (home 「プロデュース中」 -> プロデュース再開 -> Hub).
        if (self.minigame != "off" or self.unit_search or self.team_edit) and self.mode != "start":
            raise ValueError("minigame/unit_search/team_edit 只用于 start 模式")
        if self.team_edit and self.unit_search:
            raise ValueError("team_edit 与 unit_search 不能同时使用")
        if self.edit_only and (not self.team_edit or self.allow_start_spend):
            raise ValueError("edit_only 只用于 team_edit，且不授权开局")
        if self.minigame != "off" and not self.from_home:
            raise ValueError("minigame 需要 from_home（从首页进入小游戏）")
        for name in ("input_enabled", "allow_start_spend", "team_confirmed", "sp_done", "from_home", "unit_search",
                     "resume_mismatch_ok", "allow_reward_reroll", "team_edit", "edit_only", "read_team", "verify_resume",
                     "picker_sort", "sp_small_view", "live_timed_click", "settings_fix", "halo_giveup"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须为布尔值")
        for name, low, high in (("live_timing_period_s", .90, 1.05),
                                ("live_timing_event_offset_ms", 0, 200)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"{name} 超出范围")
        if type(self.max_acts) is not int or not 0 <= self.max_acts <= MAX_ACTS_PER_RUN:
            raise ValueError("max_acts 超出范围")
        if self.segment_acts is not None and (type(self.segment_acts) is not int
                                              or not 0 < self.segment_acts <= self.max_acts):
            raise ValueError("segment_acts 必须为 1..max_acts")
        if self.allow_start_spend and self.mode != "start":
            raise ValueError("allow_start_spend 只用于 start 模式")
        if self.picker_sort and not self.team_edit:
            raise ValueError("picker_sort 只用于 team_edit")
        if self.halo_route is not None and self.halo_route not in hp.ROUTES:
            raise ValueError("halo_route 只能为 dig")
        if self.halo_route is not None and self.halo_giveup:
            raise ValueError("halo_route 与 halo_giveup 不能同时使用")
        if self.halo_book not in HALO_BOOKS:
            raise ValueError("halo_book 只能为 " + "/".join(HALO_BOOKS))
        if self.halo_book != "auto" and self.halo_route is None:
            raise ValueError("halo_book 只用于 halo_route")
        if (self.halo_book == "target") != (self.halo_book_plan is not None):
            raise ValueError("halo_book_plan 只用于且必须用于 halo_book=target")
        if self.halo_book_plan is not None:
            plan = self.halo_book_plan
            if (not isinstance(plan, dict) or set(plan) != set(HALO_BOOK_KEYS) or not isinstance(plan["title"], str)
                    or not 0 < len(plan["title"]) <= 60
                    or not all(type(plan[k]) is int and 0 <= plan[k] <= 2000 for k in HALO_BOOK_KEYS[1:])):
                raise ValueError("halo_book_plan 格式无效")
        if self.halo_goals is not None:
            if self.halo_route is None:
                raise ValueError("halo_goals 只用于 halo_route")
            hp.Goals.from_dict(self.halo_goals)

    PARAM_KEYS = ("mode", "team_confirmed", "sp_mode", "sp_done", "max_acts", "segment_acts", "from_home", "minigame",
                  "unit_search", "resume_mismatch_ok", "team_edit", "edit_only", "read_team", "verify_resume", "picker_sort", "sp_small_view",
                  "live_timed_click", "live_timing_period_s", "live_timing_event_offset_ms", "settings_fix",
                  "halo_giveup", "halo_route", "halo_book", "halo_book_plan", "halo_goals")

    @classmethod
    def from_params(cls, params: Mapping[str, Any] | None, *, input_enabled: bool = False,
                    allow_start_spend: bool = False, allow_reward_reroll: bool = False) -> "RunOptions":
        """Options from Pipeline/launcher parameters; the input/spend authority is passed separately."""
        params = dict(params or {})
        unknown = set(params) - set(cls.PARAM_KEYS)
        if unknown:
            raise ValueError("未知运行选项：" + ",".join(sorted(unknown)))
        mode = params.get("mode", "attach")
        return cls(input_enabled=input_enabled, allow_start_spend=allow_start_spend and mode == "start",
                   allow_reward_reroll=allow_reward_reroll and input_enabled, **params)

# 獲得報酬 handling (produce_config.wing_params()["end"]): read Ex skills / books (read-only taps), 保護 any Lv5 book,
# 再抽選 (off; bounded by the shown count, never with a Lv5 book), press OK after the review (off: hand over).
DEFAULT_END_PARAMS = {"read": True, "protect_lv5": False, "reroll": False, "press_ok": False}

# Stable scorer entry points (module:function). Other agents own these modules; this table is the contract.
SCORER_HOOKS = {
    "week": ("agent.core.wing_policy", "decide"),
    "sp": ("agent.core.wing_sp", "plan_sp"),
    "event": ("agent.core.wing_events", "choose_event_option"),
    "team": ("agent.core.wing_team", "select_team"),
    "audition": ("agent.core.wing_audition", "choose_audition"),
    "live": ("agent.core.wing_audition", "live_plan"),
    "team_idol": ("agent.core.wing_team", "idol_key"),
}


def resolve_hook(point: str) -> tuple[Callable | None, str]:
    module, function = SCORER_HOOKS[point]
    try:
        fn = getattr(importlib.import_module(module), function, None)
    except ImportError:
        fn = None
    return fn, f"{module}:{function}"


@dataclasses.dataclass(frozen=True)
class NavDecision:
    action: str                    # click | wait | stop | finish
    intent: str | None = None
    target: tuple | None = None
    reason: str = ""
    detail: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"action": self.action, "intent": self.intent, "target": list(self.target) if self.target else None,
                "reason": self.reason, "detail": dict(self.detail)}


def _click(intent: str, reason: str = "", **detail) -> NavDecision:
    return NavDecision("click", intent, TARGETS[intent][0], reason, detail)


def _stop(reason: str, **detail) -> NavDecision:
    return NavDecision("stop", None, None, reason, detail)


def _wait(reason: str = "", **detail) -> NavDecision:
    return NavDecision("wait", None, None, reason, detail)


PROMISE_TEXT = re.compile(r"週間後|約束|今度")


_NUMERAL_TAIL = re.compile(r"[IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩl|1-9]+$")


def _same_forget(read: object, learning: Mapping) -> bool:
    """The replace dialog names the skill being forgotten. Live a13: the forget list read 「ボーカルアピールV」 while
    the dialog read 「ボーカルアピールIV」, and the planned 5x skill was cancelled three times. Accept a roman-numeral
    difference only when the base name points to the chosen row alone among the read forget rows."""
    chosen = (learning.get("forget") or {}).get("name")
    if wsp.same_skill(read, chosen):
        return True
    if not isinstance(read, str) or not isinstance(chosen, str):
        return False
    base = lambda name: _NUMERAL_TAIL.sub("", wsp.normalise(name or ""))
    if not base(read) or base(read) != base(chosen):
        return False
    return sum(base(name) == base(read) for name in learning.get("forget_rows") or ()) == 1


@dataclasses.dataclass
class RunMemory:
    """Agent-tracked facts the screen does not show (wins, accepted promise, pending multi-step plan)."""
    strategy: str = "vo_score"
    goal: str = "true_end"
    pending: str | None = None            # rest | spend_sp | audition | survey (audition list) | lesson:<id>
    rest_key: list | None = None          # [season, weeks_left] the planned rest was chosen in (rest 確認画面 check)
    # 2026-10-14 (休む確認画面 OFF): the week key of a 休む tap already sent without a confirmation; the next frame must
    # prove progress (week counter / rest animation) and the rest is never tapped again.
    rest_sent: list | None = None
    # Maa's own オプション read pass (wing_options.read_step state; in-process, rolled back with the memory).
    options_read: dict | None = None
    # The settings fix (RunOptions.settings_fix; in-process): {"phase": fix | verify | verified | kept | unavailable |
    # skipped, "before": {row: value}}.
    options_fix: dict | None = None
    pending_audition: int | None = None   # fan reward the week decision chose
    pending_special: str | None = None    # limited-audition id of that choice (wing_screen.SPECIAL_AUDITION_NAMES)
    audition_pages: int = 0
    wins: list = dataclasses.field(default_factory=list)
    promise: dict | None = None
    live_auto_clicks: int = 0
    wing_won: bool = False
    true_end_seen: bool = False
    last_week_decision: dict | None = None
    target_unit: int | None = 1           # in-game unit (1..20) holding the configured team; user default unit1
    team: list = dataclasses.field(default_factory=list)   # configured cards: P, S1–S4, guest (wing_team records)
    live_mode: str = "policy"             # policy (own play, default) | game_auto (explicit opt-in, still gated)
    live_step: dict | None = None         # current Live turn: {"turn", "step", "option_id", "target", "intent"}
    live_trend: dict | None = None        # this Live's full trend ranks (a judge that left hides its rank)
    live_top_appeal: list | None = None   # confirmed Top Appeal ownership; a last hit alone is not proof
    # Stat caps known for this run: 所持スキル reading, or inferred when a lesson left the stat unchanged (live
    # 2026-10-10: Vo stuck at 536 for weeks 12-15 while the scorer had no cap and kept choosing the Vo lesson).
    stat_caps: dict | None = None
    # {"key", "attribute", "stat", "kind", "stats", "trouble"} of the last confirmed lesson / work week (attribute and
    # stat are None for work): the next week's reading infers a cap or a failure
    lesson_probe: dict | None = None
    live_need_first: bool | None = None   # set by the week/final decision; None = unknown -> treated as True
    # Audition list of the current week, read on the audition tab before the week decision (survey). Without it the
    # scorer sees no auditions (``audition_list_unread``) and can never choose one. {"key": [season, weeks_left],
    # "pages": [{"reward", "difficulty", "recommended", "required_rank", "locked"}], "trend": {...}|None, "done": bool}
    auditions_seen: dict | None = None
    # 2026-10-09: the week decision takes the audition list from the static table (wing_score.WING_AUDITION_TABLE,
    # ``"source": "table"``) and opens the audition tab only to enter the chosen audition; the tab is surveyed only
    # when the table lacks the season/week, or always when this is True.
    audition_survey: bool = False
    entered_audition: dict | None = None  # {"season", "reward"} after 決定 on the planned audition; a win on 合格
    # Limited audition (wing_special id) whose Live is being played: entered with 決定, cleared on the result screens
    # and before the W.I.N.G. stage; its 特殊ルール decide own play or the game's Auto (wing_special.LIVE_MODES).
    live_special: str | None = None
    # End-of-Produce collector (agent.core.produce_end.EndCollector), created on the first Evaluation screen; it
    # gathers the end-flow readings for the user's review.
    end: Any = None
    start_attempted: bool = False         # the consuming プロデュース開始 was attempted (never again in this run)
    sp_handled_once: bool = False         # RunOptions.sp_done: one SP request after the user's own learning is done
    last_sp: int | None = None            # SP read on the last Schedule (week decision); used before the W.I.N.G. stage
    final_sp_checked: str | None = None   # stage (HubSemiFinal/HubFinal) whose SP handover was evaluated
    primary: str | None = None            # lesson attribute of the run (manual.primary_stat / strategy primary)
    last_stats: dict | None = None        # Vo/Da/Vi/Me of the last Schedule reading
    # sp_mode "auto": the current spending session on the 振り返り panel (see WingNavRuntime._sp_review) and the
    # week/stage keys whose session already ended (one session per key; the scorer's later requests are satisfied).
    sp_session: dict | None = None
    sp_done_keys: list = dataclasses.field(default_factory=list)
    live_skills: list | None = None       # the 4 Live skills last read on 忘れるスキル選択 (name/attributes/multiplier)
    # Run-wide SP knowledge (2026-10-10 lookahead): {"catalog": {intent: static node
    # info read once per Produce (name/kind/attributes/value/multiplier/cost/source/condition)}, "spent": SP Maa spent
    # this run, "blocked": [intents seen 未解放 next to a learned node], "plan": last run plan (Live targets, reserve,
    # income estimate)}. Carried in ``sp_context`` across segments.
    sp_run: dict | None = None
    # Static Live card mechanisms per hand-card signature; current appeal/buff are not run-wide facts.
    live_cards: dict = dataclasses.field(default_factory=dict)
    live_auto_secured: bool = False       # the game's Auto was switched on because 1st place was secured this Live
    live_unread: int = 0                  # consecutive Live frames without a readable play phase (animations)
    # Batch c: configured team per ユニット編成 slot ({"P": id, "S": [ids], "guest": id}) for the fingerprint check,
    # the unit search state, the 大富豪 game state and アイドルの応援 (True once seen active before this Produce).
    team_slots: dict = dataclasses.field(default_factory=dict)
    unit_search: dict | None = None
    # Team edit state (in-process): {"pending": slot|None, "card", "intent", "done": {slot: card}, "blank": n}.
    team_edit: dict | None = None
    # Team read on ユニット確認 / ユニット編成 ({"P", "S": [..], "guest"} accepted ids) and the room-avatar references
    # learned for the support identities (agent/core/wing_support_id.py); in-process.
    team_observed: dict | None = None
    support_avatars: dict | None = None
    minigame: dict = dataclasses.field(default_factory=dict)
    giveup: str | None = None             # halo_giveup (in-process): "opened" after the Hub tap, "held" after 諦める
    # say "Halo" route state (halo_route, in-process): the policy memory (halo_policy.HaloMemory) and this week's plan
    # {"key", "order", "tried", "tab_taps", "room_taps", "survey", "decided"} plus the last Hub button and the live step.
    halo: Any = None
    halo_state: dict | None = None
    # Saved say "Halo" facts (halo_policy.SAVED_FACTS) of a restored memory file, applied when ``halo`` is created.
    halo_facts: dict | None = None
    # say "Halo" start (H2c batch 2, in-process): {step: True} per tap sent on the setup pages, "waits" since the last.
    halo_setup: dict = dataclasses.field(default_factory=dict)
    cheer: bool | None = None
    p_idol: str | None = None             # configured P idol name (card manifest), for the mid-Produce check
    difficulty: str = "middle"            # configured W.I.N.G. difficulty (start mode selects it, U01)
    p_title: str | None = None            # configured P card title (card manifest), for the プロデュース再開 check
    segment_resumed: bool = False         # this run continues a saved segment of Maa's own Produce (memory_file)
    # Identity of the Produce this memory belongs to (2026-10-11 review P1-2): {"p_card", "difficulty", "run_id",
    # "started_at", "source": start|attach}. Set when Maa's own start is confirmed (run_id = spend-ledger key) or when
    # an attach run verified the P idol; checked by WingSession.begin before a saved memory is restored.
    produce: dict | None = None
    resume_info: dict | None = None       # mid-Produce detection result (report / UI warning)
    # Schedule room previews of the current week (in-process only, never snapshotted): {"key": [season,
    # weeks_left], "sp": last read SP, "previews": {room_id: {"preview", "sp_gain", "fan_gain"}}, "checked": [ids]}.
    # A new week key resets it; a changed SP (learning on the panel) drops the cached previews.
    room_previews: dict | None = None
    # 獲得報酬 review (2026-10-10, WingNavRuntime._reward_review); in-process only (the page is one segment).
    reward_review: dict | None = None
    # Last accepted number readings (agent/core/wing_numbers.py): {"values": {field: [value, week_index]}, "week": i}.
    numbers: dict | None = None

    SNAPSHOT_KEYS = ("strategy", "goal", "wins", "promise", "wing_won", "true_end_seen", "target_unit", "live_mode",
                     "cheer", "sp_context", "live_trend", "stat_caps", "lesson_probe", "live_cards", "produce",
                     "live_special", "numbers", "giveup", "halo_facts")

    def snapshot(self) -> dict:
        """Facts that survive a segment boundary (attach resume); never a pending click plan. ``sp_context`` (live
        seg6, 2026-10-10: an attach on スキル入れ替え確認 without the in-memory plan cancelled the replace) carries an
        unfinished ``auto`` SP session -- the learning step, its forget choice, the catalog -- so the next segment can
        finish the chain. Every dialog still re-checks the plan against what it reads (cost / SP / names)."""
        out = {k: getattr(self, k) for k in self.SNAPSHOT_KEYS if k != "sp_context"}
        out["wins"] = [list(w) for w in self.wins]
        if self.halo is not None:
            out["halo_facts"] = hp.save_facts(self.halo)
        if self.pending == "spend_sp" and isinstance(self.sp_session, dict):
            out["sp_context"] = json.loads(json.dumps({
                "pending": "spend_sp", "sp_session": self.sp_session, "sp_done_keys": list(self.sp_done_keys),
                "live_skills": self.live_skills, "last_stats": self.last_stats, "sp_run": self.sp_run}, default=list))
        elif self.sp_done_keys or self.sp_run:
            out["sp_context"] = json.loads(json.dumps({
                "pending": None, "sp_session": None, "sp_done_keys": list(self.sp_done_keys),
                "live_skills": self.live_skills, "last_stats": self.last_stats, "sp_run": self.sp_run}, default=list))
        return out

    def _restore_sp_context(self, ctx) -> None:
        if ctx is None:
            return
        if not isinstance(ctx, Mapping) or set(ctx) - {"pending", "sp_session", "sp_done_keys", "live_skills",
                                                       "last_stats", "sp_run"}:
            raise ValueError("sp_context 格式无效")
        pending, session = ctx.get("pending"), ctx.get("sp_session")
        keys = ctx.get("sp_done_keys") or []
        if pending not in (None, "spend_sp") or (pending is None) != (session is None):
            raise ValueError("sp_context.pending 无效")
        if not (isinstance(keys, list) and all(_sp_key_ok(k) for k in keys)):
            raise ValueError("sp_context.sp_done_keys 无效")
        if session is not None:
            key = session.get("key") if isinstance(session, Mapping) else None
            if not (_sp_key_ok(key) and isinstance(session.get("catalog"), Mapping)
                    and isinstance(session.get("learned"), list) and isinstance(session.get("skipped"), list)
                    and (session.get("learning") is None or isinstance(session["learning"], Mapping))):
                raise ValueError("sp_context.sp_session 无效")
        skills = ctx.get("live_skills")
        if skills is not None and not (isinstance(skills, list) and all(isinstance(k, Mapping) for k in skills)):
            raise ValueError("sp_context.live_skills 无效")
        stats = ctx.get("last_stats")
        if stats is not None and not isinstance(stats, Mapping):
            raise ValueError("sp_context.last_stats 无效")
        run = ctx.get("sp_run")
        if run is not None and not (isinstance(run, Mapping) and isinstance(run.get("catalog"), Mapping)
                                    and type(run.get("spent")) is int and isinstance(run.get("blocked"), list)):
            raise ValueError("sp_context.sp_run 无效")
        if run is not None:
            self.sp_run = json.loads(json.dumps(run))
        self.pending = pending
        self.sp_session = dict(session) if session is not None else None
        if self.sp_session is not None:
            # 2026-10-15 live: a bar-loop flag saved by a stopped run ended the next run's session at once (SP unspent).
            # Bar bookkeeping belongs to the open bar of the old frame; a resumed panel starts clean.
            for key in ("bar_loop", "bar_closes", "after_close", "selected"):
                self.sp_session.pop(key, None)
        self.sp_done_keys = [list(k) for k in keys]
        if skills is not None:
            self.live_skills = [dict(k) for k in skills]
        if stats is not None:
            self.last_stats = dict(stats)

    def restore(self, data: Mapping[str, Any]) -> None:
        """Merge a previous segment's ``snapshot`` (validated: unknown keys or bad types are refused)."""
        unknown = set(data) - set(self.SNAPSHOT_KEYS)
        if unknown:
            raise ValueError("未知的运行记忆字段：" + ",".join(sorted(unknown)))
        wins = data.get("wins", [])
        if not (isinstance(wins, list) and all(isinstance(w, (list, tuple)) and len(w) == 2
                                               and all(type(v) is int for v in w) for w in wins)):
            raise ValueError("wins 格式无效")
        if data.get("strategy", self.strategy) != self.strategy or data.get("goal", self.goal) != self.goal:
            raise ValueError("运行记忆的策略/目标与本次设置不一致")
        self.wins = [tuple(w) for w in wins]
        if data.get("promise") is not None and not isinstance(data["promise"], Mapping):
            raise ValueError("promise 格式无效")
        self.promise = dict(data["promise"]) if data.get("promise") else None
        self.wing_won = bool(data.get("wing_won", False))
        self.true_end_seen = bool(data.get("true_end_seen", False))
        if "cheer" in data:
            if data["cheer"] is not None and not isinstance(data["cheer"], bool):
                raise ValueError("cheer 格式无效")
            self.cheer = data["cheer"]
        trend = data.get("live_trend")
        if trend is not None and not (isinstance(trend, Mapping) and set(trend) <= {"Vo", "Da", "Vi"}
                                      and sorted(trend.values()) == [1, 2, 3]):
            raise ValueError("live_trend 格式无效")
        self.live_trend = dict(trend) if trend else None
        caps = data.get("stat_caps")
        if caps is not None and not (isinstance(caps, Mapping) and set(caps) <= {"Vo", "Da", "Vi", "Me"}
                                     and all(type(v) is int and v > 0 for v in caps.values())):
            raise ValueError("stat_caps 格式无效")
        self.stat_caps = dict(caps) if caps else None
        probe = data.get("lesson_probe")
        if probe is not None and not (isinstance(probe, Mapping) and (
                (probe.get("attribute") in ("Vo", "Da", "Vi") and type(probe.get("stat")) is int)
                or (probe.get("attribute") is None and isinstance(probe.get("stats"), Mapping)))):
            raise ValueError("lesson_probe 格式无效")
        self.lesson_probe = dict(probe) if probe else None
        cards = data.get("live_cards")
        if cards is not None and not (isinstance(cards, Mapping) and all(
                isinstance(v, Mapping) and isinstance(v.get("sig"), list) for v in cards.values())):
            raise ValueError("live_cards 格式无效")
        # Keep known skill mechanisms across segments, but migrate legacy caches containing dynamic tooltip values.
        self.live_cards = {key: {k: json.loads(json.dumps(v)) for k, v in info.items() if k not in ("appeal", "buff")}
                           for key, info in (cards or {}).items()}
        produce = data.get("produce")
        if produce is not None and not (isinstance(produce, Mapping) and isinstance(produce.get("p_card"), str)
                                        and produce.get("source") in ("start", "attach")):
            raise ValueError("produce 格式无效")
        self.produce = dict(produce) if produce else None
        special = data.get("live_special")
        if special is not None:
            from agent.core import wing_special as wsp
            if special not in wsp.BY_ID:
                raise ValueError("live_special 格式无效")
        self.live_special = special
        from agent.core import wing_numbers
        numbers = data.get("numbers")
        if not wing_numbers.valid_state(numbers):
            raise ValueError("numbers 格式无效")
        self.numbers = json.loads(json.dumps(numbers)) if numbers else None
        # say "Halo" (H2d 2026-11-07): the give-up state (the end pages after 諦める) and the route facts.
        giveup = data.get("giveup")
        if giveup not in (None, "opened", "held"):
            raise ValueError("giveup 格式无效")
        self.giveup = giveup
        facts = data.get("halo_facts")
        if facts is not None and not hp.valid_facts(facts):
            raise ValueError("halo_facts 格式无效")
        self.halo_facts = json.loads(json.dumps(facts)) if facts else None
        self.halo = None
        self._restore_sp_context(data.get("sp_context"))

    # Facts of one Produce (reset when Maa's own new Produce start is confirmed; review P1-1/P1-2).
    PRODUCE_FACTS = ("pending", "pending_audition", "pending_special", "wins", "promise", "wing_won", "true_end_seen", "last_week_decision",
                     "live_step", "live_trend", "live_top_appeal", "stat_caps", "lesson_probe", "live_need_first",
                     "auditions_seen", "entered_audition", "end", "last_sp", "final_sp_checked", "last_stats",
                     "sp_session", "sp_done_keys", "live_skills", "sp_run", "live_cards", "live_auto_secured",
                     "room_previews", "reward_review", "produce", "segment_resumed", "resume_info", "live_special",
                     "numbers", "giveup", "halo", "halo_state", "halo_facts")

    def reset_for_new_produce(self) -> None:
        """Forget every fact of a previous Produce (dataclass defaults); configuration fields stay."""
        fields = {f.name: f for f in dataclasses.fields(self)}
        for name in self.PRODUCE_FACTS:
            f = fields[name]
            setattr(self, name, f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default)

    @classmethod
    def from_produce_config(cls, config) -> "RunMemory":
        """Run memory for a manual W.I.N.G. run from ``produce_config.ProduceConfig`` (strategy, goal, live mode,
        in-game unit, registered team). Weekly-count (official Auto) configs have no navigator run."""
        data = getattr(config, "data", config)
        manual = data.get("manual")
        if not isinstance(manual, Mapping):
            raise ValueError("weekly_count 使用官方 Auto，没有 W.I.N.G. 导航运行")
        unit = re.fullmatch(r"unit(\d{1,2})", str(data.get("unit_preset") or ""))
        team = data.get("team") or {}
        cards = [c for c in [team.get("p_card"), *(team.get("s_cards") or []), team.get("guest")] if c]
        primary = manual.get("primary_stat")
        slots = {"P": team.get("p_card"), "S": [c for c in team.get("s_cards") or [] if c], "guest": team.get("guest")}
        p_idol = card_idol_name(team.get("p_card"))
        p_title = card_info(team.get("p_card")).get("title") or None
        return cls(strategy=manual["strategy"], goal=data["goal"], live_mode=manual.get("live_mode", "policy"),
                   primary=primary if primary in ("Vo", "Da", "Vi") else None,
                   target_unit=int(unit.group(1)) if unit else None,
                   team=[{"id": c, "kind": "P" if c.startswith("1") else "S"} for c in cards],
                   team_slots={k: v for k, v in slots.items() if v}, p_idol=p_idol, p_title=p_title,
                   difficulty=data.get("difficulty") if data.get("difficulty") in DIFFICULTY_CHOICES else "middle")


def card_info(card_id) -> dict:
    """Manifest entry of a card id (``ui/game-assets/manifest.json``, private): idol / title / ...; {} if absent."""
    if not isinstance(card_id, str):
        return {}
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "ui" / "game-assets" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for card in [*manifest.get("p_cards", []), *manifest.get("cards", [])]:
        if str(card.get("id")) == card_id:
            return dict(card)
    return {}


def card_idol_name(card_id) -> str | None:
    """Idol name of a card id from the local card manifest; None if absent."""
    return card_info(card_id).get("idol") or None


def same_idol(speaker: str | None, idol: str | None) -> bool | None:
    """Hub bubble speaker (given name, e.g. 「灯織」) vs a full idol name (「風野灯織」); None when either is unknown."""
    if not speaker or not idol:
        return None
    a, b = re.sub(r"\s", "", speaker), re.sub(r"\s", "", idol)
    if len(a) >= 2 and (b.endswith(a) or a == b):
        return True
    # 2026-10-14 live: 「灯織」 read as 「灯罐」. A same-length reading that differs from the given name in exactly one
    # character is an OCR misread (unverified), not another idol.
    tail = b[-len(a):] if len(b) >= len(a) else ""
    if len(a) >= 2 and len(tail) == len(a) and sum(x != y for x, y in zip(a, tail)) == 1:
        return None
    return False


class WingNavRuntime:
    """Pure decision runtime shared by the Maa custom recognitions/actions and the offline replay."""

    def __init__(self, memory: RunMemory | None = None, hooks: Mapping[str, Callable] | None = None,
                 policy_params: Mapping[str, Any] | None = None, params: Mapping[str, Any] | None = None,
                 end_config: Any = None, options: RunOptions | None = None):
        """``params`` = ``produce_config.wing_params(cfg)`` (policy / live / sp / event / items). Empty sections keep
        the library defaults: the hooks are then called exactly as before (no extra argument)."""
        self.options = options or RunOptions()
        self.memory = memory or RunMemory()
        self.memory.sp_handled_once = False
        self.overrides = dict(hooks or {})
        self.params = {"policy": {}, "live": {}, "sp": {}, "event": {}, "items": []}
        self.params.update({k: v for k, v in dict(params or {}).items() if v is not None})
        if policy_params is None:
            policy_params = self.params.get("policy")
        self.policy_params = dict(policy_params or {})
        self.profile_report = None         # exact local card/Lv/LB coverage, populated by from_produce_config
        self.hand_cache_path = None        # wing_hand_cache file; set by live runs only (tests never read or write it)
        self.params.setdefault("end", {})
        self.params["end"] = {**DEFAULT_END_PARAMS, **dict(self.params["end"] or {})}
        if end_config is None and self.params["end"]["press_ok"]:
            from agent.core import produce_end as pe
            end_config = pe.EndGateConfig(pause_at_reward=False)
        self.end_config = end_config        # produce_end.EndGateConfig (None = its defaults)
        self.active = False
        self.finished: str | None = None
        self.stop_text: str | None = None   # Chinese ``detail.text`` of the stop decision (UI shows it with the code)
        self.sp_unread_waits = 0            # consecutive Schedule frames without the SP counter at a stat cap
        self.state_unread_waits = 0         # consecutive Hub/Schedule frames whose week state did not read
        self.acts = 0
        self.log: list[dict] = []
        self.end_payload: dict | None = None   # produce_end.summary_payload of the end handover/finish (UI result view)
        self._last_intent: tuple | None = None
        self._repeat = 0
        self.pending_decision: NavDecision | None = None   # set by SC.Wing.Decide, consumed by SC.Wing.Act
        self.acted_digest: str | None = None               # frame digest of the last act (After/progress check)
        self._wait_screen: str | None = None
        self._waits = 0
        # In-game プロデュース設定 record (wing_options.SettingsGate); the launcher's live session sets it, None = no check.
        self.settings = None

    # ---- Maa glue helpers (pure) ------------------------------------------------------------------------------
    def active_for(self, screen: str | None, is_wait: bool = False) -> bool:
        """``SC.Wing.State {want: active}``; a wait screen that never changes stalls the run (no blind waiting)."""
        if not self.is_active():
            return False
        if is_wait:
            self._waits = self._waits + 1 if screen == self._wait_screen else 1
            self._wait_screen = screen
            if self._waits > WAIT_LIMITS.get(screen, WAIT_LIMIT_DEFAULT):
                self.stop(screen, "wait_stalled")
                return False
        elif screen is not None:
            # A guard without a screen (the generic On.* guard) is not progress; a decision on another screen or a
            # click is (see ``decide``), so the Live Hold node stays bounded by WAIT_LIMITS["Live"].
            self._wait_screen, self._waits = None, 0
        return True

    def progress(self, digest: str) -> bool:
        """``SC.Wing.State {want: progress}``: the After guard only accepts a frame different from the act frame."""
        return self.acted_digest is None or digest != self.acted_digest

    # ---- lifecycle --------------------------------------------------------------------------------------------
    def begin(self) -> None:
        self.active, self.finished, self.acts, self.stop_text = True, None, 0, None
        self.log.append({"event": "begin", "live_input": LIVE_INPUT_ENABLED})

    def is_active(self) -> bool:
        return self.active and self.finished is None

    def stop(self, screen: str | None, reason: str) -> bool:
        """Latch a stop. The first reason wins: the Pipeline's generic Stop nodes (NoDecision/ActRefused) run after
        the Agent already stopped with the specific reason and must not overwrite it."""
        if self.finished is None:
            self.finished = reason
        self.active = False
        self.log.append({"event": "stop", "screen": screen, "reason": reason})
        return False

    def hook(self, point: str) -> tuple[Callable | None, str]:
        if point in self.overrides:
            return self.overrides[point], f"override:{point}"
        return resolve_hook(point)

    # ---- decisions --------------------------------------------------------------------------------------------
    def decide(self, screen: str, readings: Mapping[str, Any] | None = None) -> NavDecision:
        """The decision for one classified frame. Never returns a click for an unknown screen."""
        rule = RULES.get(screen)
        if rule is None:
            return _wait("unknown_screen")
        readings = dict(readings or {})
        if screen == "Loading" and self.memory.halo_setup:
            self.memory.halo_setup["loaded"] = True      # say "Halo" book page: おまかせ編成 loads its pick
        gate = self._settings_gate(screen, readings)
        if gate is not None:
            return gate
        self._rest_progress(screen)
        if screen in RESULT_FLOW and screen != "Live":
            self.memory.live_step, self.memory.live_auto_secured = None, False
            self.memory.live_trend = self.memory.live_top_appeal = None
            self.memory.live_special = None
        gated = self._end_gate(screen, readings)
        if gated is not None:
            return gated
        if rule.kind == "wait":
            if screen == "TrueEndCommu":
                self.memory.true_end_seen = True
            if screen == "AuditionPass":
                self._record_win()
            return _wait(rule.note or "wait_screen")
        if rule.kind == "stop":
            return _stop(rule.reason)
        if rule.kind == "finish":
            return NavDecision("finish", reason=rule.reason, detail={"true_end_seen": self.memory.true_end_seen})
        if rule.kind == "fixed":
            if screen == "UnitCheck":
                checked = self._resume_team_verdict(readings)
                if checked is not None:
                    return checked
            if screen in ("UnitCheck", "ActiveSkills"):
                self._observe_supports(screen, readings)
            if screen in ("Evaluation",):
                self.memory.wing_won = True
            return _click(rule.intent, "fixed")
        segment = self._segment_boundary(screen)
        if segment is not None:
            return segment
        if screen in ("Hub", "HubSemiFinal", "HubFinal"):
            resumed = self._resume_gate(screen, readings)
            if resumed is not None:
                return resumed
            team_check = self._resume_team_gate(screen)
            if team_check is not None:
                return team_check
        if screen == "HaloHub" and self.options.halo_route is not None:
            # H2d 2026-11-07: a resumed say "Halo" Produce checks its team on ユニット確認 as W.I.N.G. does.
            team_check = self._resume_team_gate(screen)
            if team_check is not None:
                return team_check
        handler = getattr(self, "_decide_" + rule.point)
        decision = handler(screen, readings)
        if decision.action == "wait" and rule.fallback != "wait" and decision.reason not in self.BOUNDED_REREADS:
            return _stop(decision.reason or "no_decision")
        if decision.action == "click" and decision.intent not in screen_intents(screen):
            return _stop("intent_not_bound", intent=decision.intent, screen=screen)
        if decision.action == "click" or screen != self._wait_screen:
            self._wait_screen, self._waits = None, 0
        return decision

    # Screens on which the run's settings must be known: the Hub (rest / Options) and the W.I.N.G. stage hubs.
    SETTINGS_SCREENS = ("Hub", "HubSemiFinal", "HubFinal")
    # Frames that prove a confirm-less 休む took effect (rest animation / week transition / the next week's commu).
    REST_PROGRESS = ("HubBanner", "WeekCard", "TimeCard", "ScheduleBusy") + tuple(
        s for s in COMMU_FLOW if s not in ("Loading",))

    def setting(self, key: str) -> str:
        """The run's value of a dependent プロデュース設定 row (wing_options.DEPENDENT); without a record the 10-11
        recording's values (offline replays, the AgentServer path)."""
        return self.settings.value(key) if self.settings is not None else wo.ASSUMED[key]

    def _settings_gate(self, screen: str, r: Mapping[str, Any]) -> NavDecision | None:
        """In-game プロデュース設定 (agent/core/wing_options.py): the run needs a complete, fresh reading of the dependent
        rows (either value). Without one, the first Hub opens オプション and reads the page (read-only) when the read
        path's targets are verified; otherwise the run stops before its first decision and asks the user to open the
        page once. The options page itself is always handled by ``_decide_options``."""
        g = self.settings
        if g is None or g.passed or screen == "Options":
            return None
        fix = self._settings_fix(screen, g)
        if fix is not None:
            return fix
        st = g.status()
        if st["ok"]:
            if self.options.settings_fix and (self.memory.options_fix or {}).get("phase") not in self.FIX_DONE:
                return None             # the values apply already; the fix still runs on the first Hub (live 10-25)
            g.passed = True
            self.log.append({"event": "produce_settings_ok", "values": st["values"],
                             "source": g.record.get("source"), "updated_at": g.record.get("updated_at")})
            return None
        m = self.memory
        if wo.read_path_ready(TARGETS):
            if screen not in self.SETTINGS_SCREENS:
                return None                 # read on the first Hub; nothing before it depends on the settings
            if screen == "Hub" and m.options_read is None:
                m.options_read = wo.new_read()
                self.log.append({"event": "produce_settings_read_start", "status": st})
                return _click(wo.READ_INTENTS["open"], "produce_settings_read", settings=st)
            if m.options_read is not None:
                # Back from Maa's own read pass without a complete reading (never re-opened in this run).
                return _stop("produce_settings_unverified", settings=st,
                             text=wo.missing_text(st["missing"] or st["expired"] or wo.REQUIRED))
        if st["stale"]:
            return _stop("produce_settings_stale", settings=st, text=wo.stale_text(st["stale"]))
        if st["missing"]:
            return _stop("produce_settings_unverified", settings=st, text=wo.missing_text(st["missing"]))
        return _stop("produce_settings_expired", settings=st, text=wo.expired_text(st["expired"]))

    FIX_DONE = ("verified", "kept", "unavailable", "skipped")
    # Waits a "stop"-fallback rule still honours: counted re-reads (SP_UNREAD_REREADS) of a Schedule frame that missed a
    # value. Live 2026-10-25 (wing11): the fallback turned the first re-read into a stop (as on 10-15).
    BOUNDED_REREADS = ("state_unreadable_reread", "sp_unread_reread")
    HALO_SETTLE_WAITS = 6               # re-reads of a Halo card / live selection before it must settle
    HALO_DECIDE_WAITS = 40              # frames the schedule page may still show after 決定 (animation)

    def _settings_fix(self, screen: str, g) -> NavDecision | None:
        """``RunOptions.settings_fix``: before the run set ``wing_options.FIX_VALUES`` and keep them.
        First Hub: open オプション (``fix`` pass: OFF taps + 保存), back on the Hub open it again (``verify`` pass, read-only;
        its reading updates the record). No tap when the fresh record or the page already shows every row OFF. With
        the fix targets unverified nothing is attempted (logged) and the read-only gate continues. A click / stop, or
        None when done or not wanted."""
        m = self.memory
        f = m.options_fix
        if not self.options.settings_fix or (f or {}).get("phase") in self.FIX_DONE:
            return None
        if f is None:
            if not wo.fix_path_ready(TARGETS):
                m.options_fix = {"phase": "unavailable"}
                self.log.append({"event": "produce_settings_fix_unavailable", "reason": "target_not_verified"})
                return None
            values = dict(g.record.get("values") or {})
            if wo.fix_satisfied(g.record):
                m.options_fix = {"phase": "kept"}
                self.log.append({"event": "produce_settings_fix_ok", "values": {k: values.get(k) for k in wo.FIX_VALUES}})
                return None
            if screen not in self.SETTINGS_SCREENS:
                return None                 # on the first Hub, like the read pass
            if screen != "Hub":
                m.options_fix = {"phase": "skipped"}     # no detour on the 準決勝/決勝 Hub
                self.log.append({"event": "produce_settings_fix_skipped", "screen": screen})
                return None
            m.options_fix = {"phase": "fix", "before": {k: values.get(k) for k in wo.FIX_VALUES}}
            m.options_read = wo.new_read("fix")
            self.log.append({"event": "produce_settings_fix_start", "before": m.options_fix["before"]})
            return _click(wo.READ_INTENTS["open"], "produce_settings_fix")
        state = m.options_read or {}
        if f["phase"] == "fix" and state.get("phase") == "leaving" and not state.get("saved"):
            f["phase"] = "kept"                 # the page showed every row OFF already: nothing was tapped
            self.log.append({"event": "produce_settings_fix_ok", "values": dict(state.get("values") or {})})
            return None
        if f["phase"] == "fix" and (state.get("saved") or state.get("phase") == "saving"):
            # Back on the Hub after 保存 (← sent, or the game left the page itself): read the page again.
            f["phase"], f["changed"] = "verify", list(state.get("tapped") or ())
            m.options_read = wo.new_read("verify")
            self.log.append({"event": "produce_settings_fix_verify_start", "changed": f["changed"]})
            return _click(wo.READ_INTENTS["open"], "produce_settings_fix_verify")
        if f["phase"] == "verify" and state.get("phase") == "leaving":
            f["phase"] = "verified"
            g.record["fixed"] = {"values": dict(wo.FIX_VALUES), "changed": f.get("changed"), "verified_at": wo._now()}
            g.dirty = True
            self.log.append({"event": "produce_settings_fixed", "changed": f.get("changed"), "before": f.get("before")})
            return None
        return _stop("produce_settings_fix_incomplete", phase=f["phase"],
                     text="自动改游戏设置没有完成：已停止，请在 オプション 确认后重试")

    def _decide_options(self, screen: str, r: dict) -> NavDecision:
        """オプション. Maa's own read pass (opened from the Hub by ``_settings_gate``): two agreeing frames per reading,
        wheel the list down (read-only) until every dependent row was read, then ←; any dialog, an unreadable list or
        the list end without the rows stops (the page stays open for the user). A page the user opened is read into
        the record and the run stops (Maa never leaves a page it did not open)."""
        g = self.settings
        if g is None:
            return _stop("settings_not_automated")
        state = self.memory.options_read
        if state is None or state.get("phase") not in ("reading", "saving"):
            if state is not None:
                return _stop("options_back_unconfirmed", text="点了 ← 但仍在 オプション 页：已停止，请手动返回")
            if r.get("options_dialog") or r.get("options_saved"):
                return _stop("options_dialog", dialog=r.get("options_dialog") or "設定を保存しました")
            read = g.observe(r.get("produce_options") or {})
            self.log.append({"event": "produce_settings_read", "values": read})
            st = g.status()
            if st["missing"]:
                return _stop("produce_settings_incomplete", settings=st, read=read, text=wo.missing_text(st["missing"]))
            return _stop("produce_settings_checked", settings=st,
                         text="Produce 设置已读取，请返回 Produce 画面后重新开始")
        step = wo.read_step(state, {"tops": r.get("produce_options_rows"), "values": r.get("produce_options"),
                                    "dialog": r.get("options_dialog"), "saved": r.get("options_saved")})
        kind = state.get("kind", "read")
        if step.get("observe"):
            # A row the fix pass tapped is recorded by the verify pass only (after 保存 and a fresh open).
            seen = {k: v for k, v in step["observe"].items() if k not in (state.get("tapped") or ())}
            source = {"fix": "options_fix", "verify": "options_fix_verify"}.get(kind, "options_read_path")
            read = g.observe(seen, source=source)
            self.log.append({"event": "produce_settings_read", "values": read, "scrolls": state["scrolls"],
                             "pass": kind})
        if step["action"] == "wait":
            return _wait(step["reason"])
        if step["action"] == "scroll":
            return _click(wo.READ_INTENTS["scroll"], step["reason"], scrolls=state["scrolls"])
        if step["action"] == "toggle":
            return _click(wo.FIX_INTENTS[step["key"]], step["reason"], key=step["key"])
        if step["action"] == "save":
            # Until the verify pass reads the page again the record may be wrong whatever happens next.
            g.mark_stale("settings_fix_saved")
            return _click(wo.SAVE_INTENT, step["reason"], changed=list(state["tapped"]))
        if step["action"] == "close":
            return _click(wo.SAVED_CLOSE_INTENT, step["reason"])
        if step["action"] == "back":
            return _click(wo.READ_INTENTS["back"], step["reason"], values=g.status()["values"])
        texts = {"options_dialog": "オプション 页出现了对话框（如「変更を保存しますか」）：Maa 不回答，已停止",
                 "options_rows_unread": "オプション 页没读到全部 Produce 设置：已停止，请手动返回",
                 "options_unsettled": "オプション 页一直在变化：已停止，请手动返回",
                 "options_scroll_budget": "オプション 页滚动次数用完仍没读全：已停止，请手动返回",
                 "options_value_changed": "オプション 页的设置在读取中变了：已停止，请手动返回",
                 "options_row_moved": "设置行不在核对过的位置，没有点：已停止，请手动返回",
                 "options_toggle_unconfirmed": "点了 OFF 但读数不符：已停止，未保存，请手动处理",
                 "options_save_unconfirmed": "点了 保存 后没看到「設定を保存しました」或读不到设置：结果不确定，已停止",
                 "options_saved_close_unconfirmed": "点了 閉じる 但保存提示还在：已停止，请手动关闭",
                 "options_fix_not_kept": "保存后重新打开仍不是 OFF：已停止，请在游戏里确认"}
        return _stop(step["reason"], text=texts.get(step["reason"], step["reason"]),
                     **{k: v for k, v in step.items() if k in ("dialog", "missing", "key", "changed", "top", "values")})

    def _rest_progress(self, screen: str) -> None:
        """A confirm-less 休む was sent: a rest animation / week transition frame proves it (never tapped again)."""
        m = self.memory
        if m.rest_sent is not None and m.pending == "rest" and screen in self.REST_PROGRESS:
            self.log.append({"event": "rest_done", "proof": screen, "week": list(m.rest_sent)})
            m.pending = m.rest_key = m.rest_sent = None

    def expect_screens(self, screens) -> set:
        """Success guard of the last sent click (SC.Wing.State want=expect): the rule's screens, narrowed or widened
        per intent and per the run's プロデュース設定 (rest / live without 確認画面, the オプション read pass)."""
        out = set(screens or ())
        intent = self._last_intent[1] if self._last_intent else None
        if intent == wo.READ_INTENTS["open"]:
            out &= {"Options", "Loading"}
        elif intent == wo.READ_INTENTS["scroll"] or intent in wo.FIX_INTENTS.values() or intent == wo.SAVED_CLOSE_INTENT:
            out &= {"Options"}
        elif intent == wo.SAVE_INTENT:
            out &= {"Options"}          # live 10-25: 保存 stays on the page under 「設定を保存しました。」
        elif intent == wo.READ_INTENTS["back"]:
            out &= {"Hub", "HubBanner", "Loading"}
        elif intent == wte.SORT_OPEN_INTENT:
            out = {"PickerSort", "Loading"}           # the sort button opens 並び替え (never leaves the picker)
        elif intent in wte.SORT_KEY_INTENTS:
            out = {"PickerSort"}                      # a key only moves the pink mark
        elif intent == wte.SORT_APPLY_INTENT:
            out = {"IdolPicker", "Loading", "PickerSort"}
        elif intent and intent.startswith("halo_recruit_tile_"):
            out = {"HaloRecruit"}                     # a candidate tile only changes the selection
        elif intent == "hub_rest" and self.setting("rest_confirm") == "OFF":
            out |= set(self.REST_PROGRESS)
        elif intent == "hub_final_live" and self.setting("live_confirm") == "OFF":
            out |= set(LIVE_FLOW) | set(COMMU_FLOW)
        return out

    def explain_stop(self, screen: str | None, reason: str) -> str:
        """A generic stop on an unknown frame (lost / uncertain) where the unsampled W.I.N.G. warning popup may show
        (setting ON) gets the clear reason ``wing_warning_popup_unsampled``."""
        # 2026-10-15 live: an unsent click (slow decision) ended as clickUnconfirmed; nothing reached the game, so it
        # is not the popup.
        unsent = any(e.get("event") == "stop" and str(e.get("reason", "")).startswith("click_not_sent")
                     for e in self.log[-5:])
        if (reason in ("lost", "uncertain", "clickUnconfirmed") and screen in wo.WARNING_CONTEXT and not unsent
                and self.setting("wing_warning_popup") == "ON"):
            self.log.append({"event": "warning_popup_suspected", "screen": screen, "stop": reason,
                             "text": wo.WARNING_POPUP_TEXT})
            return "wing_warning_popup_unsampled"
        return reason

    def _resume_gate(self, screen: str, r: Mapping[str, Any]) -> NavDecision | None:
        """Mid-Produce detection on the first Hub of a run that neither started this Produce nor continues its own
        saved segment (see ``RunOptions``). Match -> continue; mismatch / unidentified start-mode Produce -> stop with
        a warning unless ``resume_mismatch_ok``; unidentified in attach mode -> continue with the warning logged."""
        m, o = self.memory, self.options
        if r.get("hub_cheer"):
            m.cheer = True
        # 2026-10-11 (review P1-2): a restored segment no longer skips the idol check; its saved identity was already
        # compared with the config in WingSession.begin, so only an unreadable bubble is tolerated for it.
        if m.resume_info is not None or m.start_attempted:
            return None
        speaker = r.get("hub_speaker")
        verdict = same_idol(speaker, m.p_idol)
        info = {"screen": screen, "season": r.get("season"), "weeks_left": r.get("weeks_left"),
                "seen_idol": speaker, "config_idol": m.p_idol, "mode": o.mode, "cheer": m.cheer,
                "difficulty": "unverified", "verdict": {True: "match", False: "mismatch", None: "unverified"}[verdict]}
        m.resume_info = info
        self.log.append({"event": "resume_detected", **info})
        if verdict is True:
            self.adopt_identity("attach")
            return None
        if o.resume_mismatch_ok:
            info["user_choice"] = "continue"
            return None
        choices = ["继续（resume_mismatch_ok: true）", "中断重开（由用户在游戏中处理）"]
        if verdict is False:
            info["choices"] = choices
            return _stop("resume_config_mismatch", resume=info)
        if (o.mode == "start" or o.verify_resume) and not m.segment_resumed:
            info["choices"] = choices
            return _stop("resume_identity_unverified", resume=info)
        return None

    def _resume_team_needed(self) -> bool:
        """Recheck an unbound legacy identity or a team not yet verified for the current settings."""
        m = self.memory
        return (self.options.verify_resume and not m.start_attempted and isinstance(m.produce, Mapping)
                and (m.produce.get("verified_context") != self.resume_context()
                     or (m.produce.get("source") == "attach" and m.produce.get("team_verified") is not True)))

    def resume_context(self) -> dict:
        """Settings used for the verified team: card slots, difficulty and this run's known Lv/凸 values."""
        m = self.memory
        team = {"P": m.team_slots.get("P"), "S": list(m.team_slots.get("S") or ()),
                "guest": m.team_slots.get("guest")}
        owned = getattr(self, "owned_cards", None)
        details = owned.get("details") if isinstance(owned, Mapping) else None
        ids = [team["P"], *team["S"], team["guest"]]
        levels = {}
        for card in ids:
            detail = details.get(card) if isinstance(details, Mapping) and card else None
            if isinstance(detail, Mapping):
                levels[card] = {k: detail[k] for k in ("level", "limit_break") if type(detail.get(k)) is int}
        return {"team": team, "difficulty": m.difficulty, "levels": levels}

    def _resume_team_gate(self, screen: str) -> NavDecision | None:
        """First Hub of a resumed Produce (``verify_resume``): open ユニット確認 once (read-only, the existing
        ``hub_unit_check`` target) before any other action; ``_resume_team_verdict`` decides on that page. Back on a Hub
        without a verdict, or a Hub without that target (準決勝/決勝 Hub: not sampled) -> stop, never a guess."""
        if not self._resume_team_needed():
            return None
        m = self.memory
        info = {"screen": screen, "config_team": dict(m.team_slots), "p_card": m.produce.get("p_card")}
        if getattr(self, "_resume_team_tried", False):
            return _stop("resume_team_unverified", resume_team={**info, "why": "unit_check_not_read"})
        intent = "halo_unit_check" if screen == "HaloHub" else "hub_unit_check"
        if intent not in screen_intents(screen):
            return _stop("resume_team_unverified", resume_team={**info, "why": "unit_check_not_on_this_hub"})
        self._resume_team_tried = True
        self._unit_check_tried = True       # the read_team probe is the same page; never opened twice
        self.log.append({"event": "resume_team_check_open", **info})
        return _click(intent, "verify_resume_team")

    def _resume_team_verdict(self, r: Mapping[str, Any]) -> NavDecision | None:
        """On ユニット確認 opened by ``_resume_team_gate``: the P slot must be the configured P card and every
        configured S/Guest must be in the team (wing_unit_check, the same proof as the start-mode 次へ). match -> record
        ``team_verified`` on the Produce identity (kept across segments) and continue; mismatch / uncertain -> stop."""
        if not (self._resume_team_needed() and getattr(self, "_resume_team_tried", False)):
            return None
        from agent.core import wing_unit_check as wuc
        m = self.memory
        expected = {"P": m.produce.get("p_card") or m.team_slots.get("P"), "S": list(m.team_slots.get("S") or ()),
                    "guest": m.team_slots.get("guest")}
        check = wuc.compare(r.get("unit_cards"), expected)
        if not expected["P"]:
            check = {"verdict": "uncertain", "reason": "p_not_configured", "slots": {}}
        info = {"verdict": check.get("verdict"), "slots": dict(check.get("slots") or {}), "config_team": expected,
                "checked": [s for s in ("P", "S", "guest") if expected.get(s)]}
        self.log.append({"event": "resume_team_check", **info})
        if check.get("verdict") == "match":
            m.produce = {**m.produce, "team_verified": True, "verified_context": self.resume_context()}
            return None
        reason = "resume_team_mismatch" if check.get("verdict") == "mismatch" else "resume_team_unverified"
        return _stop(reason, resume_team=info)

    def scenario(self) -> str:
        """The Produce scenario this run plays (the memory file identity): "halo" with halo_route, else "wing"."""
        return "halo" if self.options.halo_route else "wing"

    def adopt_identity(self, source: str, *, run_id: str | None = None, difficulty: str | None = None) -> dict | None:
        """Bind the memory to the Produce on screen (``source`` start: Maa's confirmed start; attach: P idol verified).
        An existing identity is kept (never silently replaced)."""
        m = self.memory
        if m.produce is not None or not m.team_slots.get("P"):
            return m.produce
        import datetime as _dt
        stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        m.produce = {"p_card": m.team_slots["P"],
                     "difficulty": difficulty or (m.difficulty if source == "start" else "unverified"),
                     "run_id": run_id or f"attach-{stamp}", "started_at": stamp if source == "start" else None,
                     "source": source, "scenario": self.scenario()}
        if source == "start":
            m.produce["verified_context"] = self.resume_context()
        self.log.append({"event": "produce_identity", **m.produce})
        return m.produce

    def _segment_boundary(self, screen: str) -> NavDecision | None:
        """A clean stop at a week start once the segment's click budget is used (attach resumes from here)."""
        limit = self.options.segment_acts
        if limit is None or self.acts < limit or self.memory.pending is not None:
            return None
        if screen in ("Hub", "HubSemiFinal", "HubFinal"):
            return _stop("segment_budget_reached", acts=self.acts, segment_acts=limit)
        return None

    @classmethod
    def from_produce_config(cls, config, *, owned=None, cards=None, id_map=None, **kw) -> "WingNavRuntime":
        """Runtime of a manual W.I.N.G. run: memory and every decision module's parameters from the user's config
        (``produce_config.wing_params``; a default config yields the library defaults)."""
        from agent.core import produce_config as pc
        from agent.core.wing_team_profile import build_team_profile
        params = pc.wing_params(config)
        data = getattr(config, "data", config)
        built = build_team_profile(data.get("team") or {}, owned, cards=cards, id_map=id_map)
        if built.profile.supports:
            params["policy"]["score_team"] = built.profile
        runtime = cls(RunMemory.from_produce_config(config), policy_params=params["policy"], params=params, **kw)
        runtime.profile_report = {"complete": built.complete, "unknowns": list(built.unknowns),
                                  "sources": dict(built.sources)}
        runtime.owned_cards = owned
        return runtime

    def _end_gate(self, screen: str, readings: Mapping[str, Any]) -> NavDecision | None:
        """End-of-Produce screens: feed the collector, then ``produce_end.end_screen_gate``. ``continue`` -> None (the
        screen's own rule runs: today's fixed taps); ``handover`` / ``hard_stop`` -> stop; ``finish`` -> finish with
        the summary payload. A destructive dialog is never cancelled or retried by Maa."""
        try:
            from agent.core import produce_end as pe
        except ImportError:
            return None
        if screen == "EndDestructiveDialog":
            return None                                  # its own rule stops
        if screen == "MissionPopup" and self._mission_popup_is_login(readings):
            return None                                  # login mission over a running Produce's home: not an end
        screen = END_ALIASES.get(screen, screen)
        if screen not in pe.END_SCREENS:
            return None
        m = self.memory
        if screen == "Evaluation" and m.end is None:
            # 社長からの評価 also follows 諦める (third demo #602 -> #616): reaching it is not a W.I.N.G. win.
            m.end = pe.EndCollector(wing_won=None, true_end_seen=m.true_end_seen)
        if m.end is not None:
            pe.observe_end_screen(m.end, screen, readings)
        gate = pe.end_screen_gate(screen, readings.get("texts") or (), dialog=bool(readings.get("dialog")),
                                  readings=readings, config=self.end_config)
        action = gate.get("action")
        if screen == "Reward" and action in ("handover", "continue"):
            review = self._reward_review(readings)
            if review is not None:
                return review
            missing = self._reward_missing() if action == "continue" else []
            if missing:
                # ANN-29: leave the page automatically only after a complete reading; otherwise stay
                # on 獲得報酬 and hand over with the missing parts, so the user finishes them and presses OK.
                m.reward_review["missing"] = missing
                self.log.append({"event": "reward_incomplete", "missing": list(missing)})
                action = "handover"
                gate = {**dict(gate), "action": "handover", "reason": "reward_incomplete", "missing": list(missing)}
            if m.end is not None and m.reward_review is not None:
                m.end.record_reward(m.reward_review)
        if action == "continue":
            return None
        if action == "hard_stop":
            return _stop("produce_end_destructive_dialog", gate=dict(gate))
        collector = m.end if m.end is not None else pe.EndCollector(wing_won=m.wing_won or None,
                                                                    true_end_seen=m.true_end_seen)
        if action == "handover":
            payload = pe.summary_payload(collector, stop_reason="produce_end_review")
            if gate.get("missing"):
                payload["reward_missing"] = list(gate["missing"])
            self.end_payload = payload
            return _stop("produce_end_review", gate=dict(gate), payload=payload)
        if action == "finish":
            self.end_payload = pe.summary_payload(collector, stop_reason="finished")
            return NavDecision("finish", reason=RULES[screen].reason or gate.get("reason", "finished"),
                               detail={"true_end_seen": m.true_end_seen, "payload": self.end_payload})
        return _stop("produce_end_gate_unknown", gate=dict(gate))

    # ---- 獲得報酬 review -----------------------------------------------------------------------
    def _reward_review(self, r: Mapping[str, Any]) -> NavDecision | None:
        """One round of the 獲得報酬 review; ``None`` = done (the gate then hands over or presses OK).

        Order: Exスキル tab (read the list) -> ノウハウブック tab -> tap each book once (its detail pane is read) ->
        保護 every book with a Lv.5+ knowhow (``protect_lv5``) -> optional 再抽選 (``reroll``: only with no Lv5 book,
        nothing protected, every book fully read, the 「再抽選 N/M」 count read, stamina >= its cost; at most the shown
        count) . Taps are read-only except 保護 (protective) and 再抽選; 売却 is never a target. Every tap is verified
        on the next frames (tab / ✓ + detail / 保護解除); unconfirmed -> stop, never retried. An unverified target
        ends the review (recorded) instead of being refused at the gate."""
        end = self.params["end"]
        if not end.get("read"):
            return None
        m = self.memory
        rv = m.reward_review
        if rv is None:
            rv = m.reward_review = {"ex": None, "ex_partial": None, "books": {}, "items": None, "taps": 0,
                                    "protected": [], "rerolls": 0, "reroll_cap": None, "pending": None,
                                    "unsettled": 0, "notes": [], "complete": False, "done": False}
        if rv["done"]:
            return None
        tab, items, sel, book = r.get("reward_tab"), r.get("reward_items"), r.get("reward_selected"), r.get("reward_book")
        if tab == "knowhow" and isinstance(sel, int) and isinstance(book, Mapping):
            rv["books"][sel] = {**dict(book), "index": sel}   # latest detail of the selected book
            if book.get("protected") and sel not in rv["protected"] and (rv.get("pending") or {}).get("kind") == "protect":
                rv["protected"].append(sel)
        pending = rv.get("pending")
        if pending is not None:
            kind = pending["kind"]
            ok = ((kind == "tab" and tab == pending["want"])
                  or (kind == "item" and tab == "knowhow" and sel == pending["index"] and isinstance(book, Mapping))
                  or (kind == "protect" and sel == pending["index"] and isinstance(book, Mapping)
                      and book.get("protected") is True))
            if kind == "reroll":
                # After the confirmation: a fresh grid (no book selected) and the re-roll count down by one.
                left = (r.get("reroll") or {}).get("left")
                ok = isinstance(left, int) and left == pending["left"] - 1
                if ok:
                    rv["books"], rv["items"] = {}, None
                    self.log.append({"event": "reward_rerolled", "left": left})
            if ok:
                rv["pending"], rv["unsettled"] = None, 0
            else:
                rv["unsettled"] += 1
                if rv["unsettled"] <= REWARD_UNSETTLED:
                    return _wait("reward_settling", pending=dict(pending))
                rv["done"] = True
                return _stop(f"reward_{kind}_unconfirmed", pending=dict(pending), tab=tab, selected=sel)

        def tap(intent: str, kind: str, **extra) -> NavDecision | None:
            if not TARGETS[intent][1]:
                rv["notes"].append(f"target_unverified:{intent}")
                return None
            if rv["taps"] >= REWARD_MAX_TAPS:
                rv["notes"].append("tap_budget_exhausted")
                return None
            rv["taps"] += 1
            rv["pending"] = {"kind": kind, **extra}
            return _click(intent, f"reward_{kind}", **extra)

        def finish() -> None:
            rv["done"] = True
            self.log.append({"event": "reward_review", "books": len(rv["books"]), "ex": len(rv["ex"] or ()),
                             "protected": list(rv["protected"]), "rerolls": rv["rerolls"], "notes": list(rv["notes"])})
            return None

        # 1. Exスキル tab (the Ex skill notice of this page)
        if rv["ex"] is None:
            if tab == "ex":
                rv["ex"] = list(r.get("ex_skills") or ())
                rv["ex_partial"] = r.get("ex_rows_cut")
            else:
                return tap("reward_tab_ex", "tab", want="ex") or finish()
        # 2. ノウハウブック tab and each book's detail
        if tab != "knowhow":
            return tap("reward_tab_knowhow", "tab", want="knowhow") or finish()
        if isinstance(items, int):
            rv["items"] = items
        n = rv["items"] or 0
        if n > 4:
            rv["notes"].append("more_than_4_books")
        for i in range(min(n, 4)):
            if i not in rv["books"]:
                return tap(f"reward_item_{i + 1}", "item", index=i) or finish()
        rv["complete"] = n > 0 and all(i in rv["books"] for i in range(n))
        # 3. 保護 for a Lv5 book
        if end.get("protect_lv5"):
            for i, b in sorted(rv["books"].items()):
                if (b.get("max_level") or 0) >= 5 and b.get("protected") is not True:
                    if sel != i:
                        return tap(f"reward_item_{i + 1}", "item", index=i) or finish()
                    return tap("reward_book_protect", "protect", index=i) or finish()
        # 4. optional 再抽選 (stamina): the setting AND the launcher authority (review P1-7) before any planning
        if end.get("reroll") and not self.options.allow_reward_reroll:
            if "reroll_skipped:not_authorized" not in rv["notes"]:
                rv["notes"].append("reroll_skipped:not_authorized")
        elif end.get("reroll"):
            info = r.get("reroll") if isinstance(r.get("reroll"), Mapping) else {}
            if rv["reroll_cap"] is None and isinstance(info.get("left"), int):
                rv["reroll_cap"] = info["left"]                 # bounded by the count shown at the first read
            lv5 = any((b.get("max_level") or 0) >= 5 or b.get("protected") for b in rv["books"].values())
            reasons = [k for k, bad in (("lv5_book", lv5), ("books_unread", not rv["complete"]),
                                        ("rows_partial", any(b.get("rows_partial") for b in rv["books"].values())),
                                        ("count_unread", not isinstance(info.get("left"), int)),
                                        ("none_left", isinstance(info.get("left"), int) and info["left"] <= 0),
                                        ("cap_reached", rv["rerolls"] >= (rv["reroll_cap"] or 0)),
                                        ("stamina", not (isinstance(r.get("stamina_now"), int)
                                                         and isinstance(info.get("stamina"), int)
                                                         and r["stamina_now"] >= info["stamina"]))) if bad]
            if not reasons:
                return tap("reward_reroll", "reroll", left=info["left"]) or finish()
            note = "reroll_skipped:" + ",".join(reasons)
            if note not in rv["notes"]:
                rv["notes"].append(note)
        return finish()

    def _reward_missing(self) -> list[str]:
        """What the finished 獲得報酬 review could not read (empty = complete). Only when reading is on: with
        「报酬读取」 off the user chose not to read, and OK follows the 「报酬后OK」 setting as before."""
        if not self.params["end"].get("read"):
            return []
        rv = self.memory.reward_review or {}
        missing = []
        if rv.get("ex") is None:
            missing.append("ex_skills")
        elif rv.get("ex_partial"):
            missing.append("ex_rows_partial")
        n = rv.get("items")
        books = rv.get("books") or {}
        if not isinstance(n, int) or n <= 0:
            missing.append("knowhow_books")
        else:
            missing += [f"book_{i + 1}" for i in range(n) if i not in books]
            missing += [f"book_{i + 1}_rows_partial" for i, b in sorted(books.items()) if b.get("rows_partial")]
        if self.params["end"].get("protect_lv5"):
            missing += [f"book_{i + 1}_protect" for i, b in sorted(books.items())
                        if (b.get("max_level") or 0) >= 5 and b.get("protected") is not True]
        if self.params["end"].get("press_ok"):
            # EX skills are not reported for now and scroll-clipped
            # book rows do not hold OK; the new books are read in full from the library after the Produce.
            missing = [k for k in missing if not k.startswith("ex_") and not k.endswith("_rows_partial")]
        return missing

    def _decide_reward_reroll_confirm(self, screen: str, r: dict) -> NavDecision:
        """再抽選 confirmation: OK only for Maa's own planned re-roll with the stamina cost read as 30."""
        rv = self.memory.reward_review or {}
        dialog = r.get("reroll_dialog") if isinstance(r.get("reroll_dialog"), Mapping) else {}
        if (rv.get("pending") or {}).get("kind") != "reroll" or not self.params["end"].get("reroll"):
            return _stop("reroll_dialog_unplanned")
        if dialog.get("stamina") != 30:
            return _stop("reroll_cost_unread", dialog=dict(dialog))
        return _click("reward_reroll_ok", "reward_reroll_confirm")

    def _decide_produce_end(self, screen: str, r: dict) -> NavDecision:
        """Reward after the gate allowed ``continue`` (only when the user turned the review pause off): OK only."""
        return _click("reward_ok", "produce_end_continue")

    def _record_win(self) -> None:
        """オーディション合格 after the planned audition: remember the win (season, fan reward) for the scorer's
        fan plan (the screen does not show the reward). Unknown season or reward -> nothing is recorded."""
        m = self.memory
        entered, m.entered_audition = m.entered_audition, None
        if entered and isinstance(entered.get("season"), int) and isinstance(entered.get("reward"), int):
            m.wins.append((entered["season"], entered["reward"]))
            self.log.append({"event": "win", "season": entered["season"], "reward": entered["reward"]})

    # ---- support identity (2026-10-12, agent/core/wing_support_id.py) ---------------
    SUPPORT_PROBES_MAX = 8          # 発動中スキル opens per run (read-only; each is open + close)

    def _team_supports(self) -> list[str]:
        """S1–S4 + guest card ids of the formed team: the ユニット確認 reading, else the configured team."""
        team = self.memory.team_observed or self.memory.team_slots or {}
        ids = [c for c in [*(team.get("S") or ()), team.get("guest")] if isinstance(c, str)]
        return ids if len(ids) == 5 else []

    def _library_cards(self) -> dict:
        if getattr(self, "_library", None) is None:
            from agent.core.wing_team_profile import _load_library_cards
            self._library = _load_library_cards()
        return self._library

    def _observe_supports(self, screen: str, r: Mapping[str, Any]) -> None:
        """Read-only pages: ユニット確認 -> the formed team (and its TeamProfile when the config has none or differs);
        発動中スキル -> face references of the entries whose active skills name exactly one team support."""
        from agent.core import wing_support_id as wsi
        m = self.memory
        if screen == "UnitCheck":
            team = wsi.team_from_unit_cards(r.get("unit_cards"))
            if team is None and m.team_slots:
                # Some icon not named open-set (#239: S2): the configured team still counts when every slot passes
                # the targeted check of wing_unit_check (the same proof the start-mode 次へ relies on).
                from agent.core import wing_unit_check as wuc
                if wuc.compare(r.get("unit_cards"), m.team_slots, editing=self.options.team_edit).get("verdict") == "match":
                    team = {"P": m.team_slots.get("P"), "S": list(m.team_slots.get("S") or ()),
                            "guest": m.team_slots.get("guest")}
            self.log.append({"event": "team_observed", "team": team})
            if team is None:
                return
            m.team_observed = team
            config = m.team_slots or {}
            same = (config.get("P") == team["P"] and sorted(config.get("S") or []) == sorted(team["S"])
                    and config.get("guest") == team["guest"])
            if config and not same:
                self.log.append({"event": "team_observed_differs", "config": dict(config), "observed": team})
            if not same or self.policy_params.get("score_team") is None:
                from agent.core.wing_team_profile import build_team_profile
                built = build_team_profile({"p_card": team["P"], "s_cards": list(team["S"]), "guest": team["guest"]},
                                           getattr(self, "owned_cards", None))
                if built.profile.supports:
                    self.policy_params["score_team"] = built.profile
                    self.profile_report = {"complete": built.complete, "unknowns": list(built.unknowns),
                                           "sources": dict(built.sources), "source": "unit_check"}
            return
        if screen == "ActiveSkills":
            data = r.get("active_skills") or {}
            team = self._team_supports()
            if not team or not data.get("entries"):
                return
            book = wsi.AvatarBook(m.support_avatars)
            labels = wsi.label_entries(data["entries"], team, self._library_cards())
            for card, sig in labels:
                book.add(card, sig)
            m.support_avatars = book.refs
            self.log.append({"event": "support_avatars_learned", "labels": [c for c, _ in labels],
                             "entries": len(data["entries"]), "known": sorted(book.refs)})

    def _attach_support_ids(self, r: dict) -> dict:
        """Schedule rooms get ``support_ids`` when every face in the room is named (count must match the portraits)."""
        from agent.core import wing_support_id as wsi
        sigs, team = r.get("room_avatar_sigs"), self._team_supports()
        if not sigs or not team or not self.memory.support_avatars:
            return r
        ids = wsi.room_support_ids(sigs, wsi.AvatarBook(self.memory.support_avatars), team)
        if not ids:
            return r
        from agent.core import wing_screen as wscr
        order = [room[0] for room in wscr.ROOMS]
        rooms = []
        for room in r.get("rooms") or []:
            room = dict(room)
            i = order.index(room["id"]) if room.get("id") in order else None
            found = ids[i] if i is not None and i < len(ids) else None
            if found and room.get("supports") == len(found):
                room["support_ids"] = list(found)
            rooms.append(room)
        return {**r, "rooms": rooms}

    def _support_probe(self, r: Mapping[str, Any], key) -> NavDecision | None:
        """``read_team``: open 発動中スキル (read-only) when the selected room has supports whose faces are not named
        yet; once per (week, room), at most SUPPORT_PROBES_MAX per run. The popup opens at the selected room."""
        if not self.options.read_team or self.memory.pending is not None or not self._team_supports():
            return None
        selected = r.get("selected_room")
        room = next((x for x in r.get("rooms") or () if x.get("id") == selected), None)
        if room is None or not room.get("supports") or room.get("support_ids"):
            return None
        done = self.__dict__.setdefault("_support_probes", [])
        probe = [key[0], key[1], selected]
        if probe in done or len(done) >= self.SUPPORT_PROBES_MAX:
            return None
        done.append(probe)
        return _click("schedule_support_skills", "read_support_skills", room=selected)

    def _decide_week(self, screen: str, r: dict) -> NavDecision:
        m = self.memory
        if screen == "Hub":
            if m.pending == "rest":
                week = [r.get("season"), r.get("weeks_left")]
                known = isinstance(m.rest_key, list) and all(isinstance(v, int) for v in week + m.rest_key)
                if known and week != m.rest_key:
                    # The week moved on: the rest already happened. Never rest again.
                    if m.rest_sent is None and self.setting("rest_confirm") == "ON" and self.settings is not None:
                        # Recorded ON, but no 休む確認 came: the setting changed -> re-read it on the next run.
                        self.settings.invalidate("rest_confirm", "OFF", "休む后没有确认画面")
                    self.log.append({"event": "rest_done", "proof": "week_changed", "planned": list(m.rest_key),
                                     "now": week})
                    m.pending = m.rest_key = m.rest_sent = None
                elif m.rest_sent is not None:
                    # 休む (no 確認画面) was sent but the week did not move: never tap it twice.
                    return _stop("rest_unconfirmed", planned=list(m.rest_sent), now=week,
                                 text="点了 休む 但周数没变：结果不确定，已停止（不会再点 休む）")
                elif self.setting("rest_confirm") == "OFF":
                    if not known:
                        return _stop("rest_week_unread", text="没读到周数：无法确认 休む 是否生效，已停止")
                    m.rest_sent = list(m.rest_key)
                    return _click("hub_rest", "pending_rest_no_confirm")
                else:
                    return _click("hub_rest", "pending_rest")
            if m.pending == "spend_sp":
                if isinstance(m.sp_session, dict) and m.sp_session.get("learning") is None:
                    # 2026-10-15 live: re-entering 振り返り from the Hub opens a fresh panel without the old info bar;
                    # a remembered inspect tap then stopped the run (sp_info_unread). An inspect tap spends nothing.
                    m.sp_session["last_tap"], m.sp_session["unstable"] = None, 0
                    m.sp_session["zoom_attempted"] = False   # the fresh panel opens zoomed in (wing7, SP_MAX_ZOOMS)
                return _click("hub_review", "pending_spend_sp")
            if (self.options.read_team and m.pending is None and m.team_observed is None
                    and not getattr(self, "_unit_check_tried", False)):
                # Read-only ユニット確認 once per run: the formed team's card ids (support identities).
                self._unit_check_tried = True
                return _click("hub_unit_check", "read_team")
            return _click("hub_schedule", "open_schedule")
        # Schedule (lesson & work tab)
        if m.pending in ("rest", "spend_sp"):
            return _click("page_return", f"pending_{m.pending}")
        if r.get("weeks_left") is None and r.get("season") == 1 and m.last_week_decision is None:
            r = {**r, "weeks_left": 8, "weeks_left_assumed": True}   # see build_week_state (2026-10-12 live)
        key = [r.get("season"), r.get("weeks_left")]
        seen = m.auditions_seen
        if m.pending is None and isinstance(key[0], int) and isinstance(key[1], int) and not m.audition_survey \
                and not (seen and seen.get("key") == key and seen.get("done")):
            from agent.core import wing_score as wsc
            pages = wsc.table_audition_pages(key[0], key[1])
            if pages is not None and key[1] == 1 and wsc.special_window_active():
                pages = None        # 強化週間: the final week adds a limited audition the table does not know
            if pages is not None:
                # Known in advance: no weekly survey. Rank = projection from the read fans; a wrongly
                # unlocked gate shows as AuditionLocked on entry and stops there (never entered).
                fans = r.get("fans")
                floor = (wsc.rank_from_fans(fans) or "F") if isinstance(fans, int) else None
                m.auditions_seen = {"key": key, "pages": pages, "trend": None, "done": True, "source": "table",
                                    "rank_floor": floor}
                seen = m.auditions_seen
        if m.pending in (None, "survey") and isinstance(key[0], int) and isinstance(key[1], int) \
                and not (seen and seen.get("key") == key and seen.get("done")):
            # Survey first: read the audition list of this week so the scorer can weigh auditions against rooms.
            m.pending, m.audition_pages = "survey", 0
            m.auditions_seen = {"key": key, "pages": [], "trend": None, "done": False}
            if key[1] == 1:
                from agent.core import wing_score as wsc
                m.auditions_seen["special_week"] = wsc.special_window_active()
            return _click("schedule_audition_tab", "survey_auditions", week=key)
        fn, name = self.hook("week")
        if fn is None:
            return _stop(f"scorer_missing:{name}")
        r = self._room_preview_cache(r, key)
        probe, now = m.lesson_probe, (r.get("stats") or {})
        attr = (probe or {}).get("attribute")
        if probe and probe.get("key") != list(key) and (isinstance(now.get(attr), int) if attr
                                                        else all(type(now.get(k)) is int for k in STAT_KEYS)):
            drop = _stat_drop(probe.get("stats"), now)
            if drop:
                # 2026-10-25 user: a failed lesson / work lowers stats (大失敗: more, and stamina to 0, so the next
                # week shows a 99 % trouble rate). Logged for the history and to calibrate the failure cost.
                big = isinstance(r.get("trouble_rate"), (int, float)) and r["trouble_rate"] >= CATASTROPHE_TROUBLE
                self.log.append({"event": "lesson_failure", "kind": "catastrophe" if big else "failure",
                                 "action": probe.get("kind"), "attribute": attr, "drop": drop,
                                 "trouble": probe.get("trouble"), "week": probe.get("key")})
            elif attr and now[attr] == probe["stat"]:
                # A lesson week passed without raising its stat: that stat is at its cap (a drop is a failure).
                m.stat_caps = {**(m.stat_caps or {}), attr: probe["stat"]}
                self.log.append({"event": "cap_inferred", "attribute": attr, "cap": probe["stat"]})
            m.lesson_probe = None
        r = self._attach_support_ids(r)
        probe_open = self._support_probe(r, key)
        if probe_open is not None:
            return probe_open
        state, problems = build_week_state(r, m)
        if state is None:
            # 2026-10-14 live a9: one Schedule frame missed the animated 「残り 8 週」 digit (the next frame read it).
            # Re-read a few frames (nothing was clicked for this decision) before stopping.
            self.state_unread_waits += 1
            if self.state_unread_waits <= SP_UNREAD_REREADS:
                return _wait("state_unreadable_reread", problems=problems)
            return _stop("state_unreadable", problems=problems)
        self.state_unread_waits = 0
        if isinstance(r.get("sp"), int):
            m.last_sp = r["sp"]
        if isinstance(r.get("stats"), Mapping):
            m.last_stats = dict(r["stats"])
        week_params = {"live_mode": m.live_mode, **self.policy_params}
        if "special_window" not in week_params:
            from agent.core import wing_score as wsc
            week_params["special_window"] = wsc.special_window_active()
        if list(key) in [list(k) for k in m.sp_done_keys or ()]:
            # 2026-10-14 live: this week's 振り返り already ended (e.g. sp_route_not_visible with SP left); asking for SP
            # again would loop. Decide the week as if SP could not be spent now (a capped lesson still earns SP).
            week_params["cap_spend_min_sp"] = 10 ** 6
        if isinstance(m.sp_run, dict):
            # An SP session already ran this Produce (it reads 所持スキル caps first): unread caps no longer count as
            # wing_policy's provisional 500, so a failed cap read cannot reopen the panel every week.
            week_params["provisional_cap"] = None
        try:
            decision = fn(state, m.strategy, m.goal, week_params)
        except Exception as exc:  # scorer errors stop the run; never guess
            return _stop(f"scorer_error:{type(exc).__name__}", message=str(exc))
        info = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
        if self.profile_report is not None:
            info.setdefault("plan", {})["team_profile"] = self.profile_report
        m.last_week_decision = info
        if info.get("rule") != "sp_unread_at_cap":
            self.sp_unread_waits = 0
        kind = info.get("kind")
        check = self._preview_check(info, r)     # read uncertain rooms' real previews before SP or the choice
        if check is not None:
            return check
        if "spend_sp" in (info.get("pre_actions") or ()) or kind == "spend_sp":
            # sp_mode: handover (the user learns), skip (SP kept) or auto (Maa learns on the panel).
            handover = self._sp_request("Schedule", key, sp=r.get("sp"), decision=info,
                                        growth_preview=self._sp_growth_preview(info, r))
            if handover is not None:
                return handover
            if kind == "spend_sp":
                return _stop("sp_still_requested_after_handover", decision=info)
        if kind in ("lesson", "work"):
            option = info.get("option_id")
            if option not in ROOM_SLOTS:
                return _stop("option_not_on_screen", decision=info)
            rooms = {room["id"]: room for room in r.get("rooms") or []}
            if option not in rooms or not rooms[option].get("label_ok", True):
                return _stop("room_label_unread", decision=info)
            if r.get("selected_room") == option:
                attr = rooms[option].get("attribute")
                stats = {k: now[k] for k in STAT_KEYS if type(now.get(k)) is int}
                if kind == "lesson" and attr in ("Vo", "Da", "Vi") and isinstance(now.get(attr), int):
                    m.lesson_probe = {"key": list(key), "attribute": attr, "stat": now[attr]}
                elif len(stats) == len(STAT_KEYS):
                    m.lesson_probe = {"key": list(key), "attribute": None, "stat": None}
                else:
                    m.lesson_probe = None
                if m.lesson_probe is not None:
                    m.lesson_probe.update(kind=kind, stats=stats, trouble=r.get("trouble_rate"))
                    # wing_numbers: next week's stats/SP/fans equal these plus the preview (demo 2, unless an event adds more).
                    room = rooms[option]
                    gain = {**{k: v for k, v in (room.get("preview") or {}).items() if type(v) is int},
                            **{k: room.get(g) for k, g in (("sp", "sp_gain"), ("fans", "fan_gain"))
                               if type(room.get(g)) is int}}
                    if gain:
                        m.lesson_probe.update(sp=r.get("sp"), fans=r.get("fans"), gain=gain)
                return _click("schedule_decide", "confirm_room", decision=info)
            return _click(f"schedule_room_{ROOM_SLOTS.index(option) + 1}", "select_room", decision=info)
        if kind == "audition":
            m.pending, m.audition_pages = "audition", 0
            m.pending_audition = _reward_of(info, state)
            m.pending_special = _special_of(info)
            m.live_need_first = False
            return _click("schedule_audition_tab", "audition_planned", decision=info)
        if kind == "rest":
            m.pending, m.rest_key = "rest", list(key)
            return _click("page_return", "rest_planned", decision=info)
        if info.get("rule") == "sp_unread_at_cap":
            # 2026-10-14 live a5: one Schedule frame missed the SP counter (the next frame read 274); re-read a few
            # frames before stopping. Nothing was clicked for this decision.
            self.sp_unread_waits += 1
            if self.sp_unread_waits <= SP_UNREAD_REREADS:
                return _wait("sp_unread_reread", decision=info)
        return _stop(f"week_decision_{kind}", decision=info)

    # Score rules whose choice depends on the room scores (a deadline audition or a condition rest does not).
    PREVIEW_CHECK_RULES = ("score_best", "score_fallback_empty_room", "score_fallback_rest")

    def _room_preview_cache(self, r: Mapping[str, Any], key: list) -> dict:
        """Remember the selected room's read preview for this week and copy remembered previews into the rooms that
        are no longer selected (the screen shows only the selected room's preview). Returns new readings."""
        m = self.memory
        if not (isinstance(key[0], int) and isinstance(key[1], int)):
            return dict(r)
        mem = m.room_previews
        if not isinstance(mem, dict) or mem.get("key") != key:
            mem = m.room_previews = {"key": list(key), "sp": None, "previews": {}, "checked": []}
        sp = r.get("sp")
        if isinstance(sp, int):
            if isinstance(mem.get("sp"), int) and mem["sp"] != sp:
                mem["previews"] = {}         # SP spent/changed: learned skills may change the gains
            mem["sp"] = sp
        selected, read = r.get("selected_room"), r.get("selected_preview")
        if selected in ROOM_SLOTS and isinstance(read, Mapping) and read.get("preview"):
            mem["previews"][selected] = {"preview": dict(read["preview"]), "sp_gain": read.get("sp_gain"),
                                         "fan_gain": read.get("fan_gain")}
        rooms = []
        for room in r.get("rooms") or []:
            cached = mem["previews"].get(room.get("id"))
            if cached and not room.get("preview"):
                room = {**room, "preview": dict(cached["preview"]),
                        **{k: cached[k] for k in ("sp_gain", "fan_gain") if cached.get(k) is not None},
                        "preview_cached": True}
            rooms.append(room)
        return {**r, "rooms": rooms}

    def _preview_check(self, info: Mapping[str, Any], r: Mapping[str, Any]) -> NavDecision | None:
        """Tap (select only, non-consuming) one room whose preview is unread but whose optimistic bound would beat
        the current choice, at most ``PREVIEW_CHECK_MAX`` rooms per week; the next frame reads its preview."""
        mem = self.memory.room_previews
        if not isinstance(mem, dict) or info.get("rule") not in self.PREVIEW_CHECK_RULES:
            return None
        ctx = (info.get("plan") or {}).get("context") or {}
        rooms = {room.get("id"): room for room in r.get("rooms") or []}
        for rid in ctx.get("preview_check") or ():
            if len(mem["checked"]) >= PREVIEW_CHECK_MAX:
                return None
            if rid not in ROOM_SLOTS or rid in mem["checked"] or rid in mem["previews"]:
                continue
            room = rooms.get(rid)
            if room is None or not room.get("label_ok", True) or r.get("selected_room") == rid:
                continue                     # label unread / already selected (preview not read yet): no tap

            mem["checked"].append(rid)
            self.log.append({"event": "preview_check", "room": rid, "week": mem["key"],
                             "bound": (ctx.get("room_bounds") or {}).get(rid), "choice": info.get("option_id")
                             or info.get("audition_id") or info.get("kind")})
            return _click(f"schedule_room_{ROOM_SLOTS.index(rid) + 1}", "preview_check", room=rid, decision=info)
        return None

    def _decide_audition(self, screen: str, r: dict) -> NavDecision:
        m = self.memory
        if m.pending == "survey":
            return self._survey_audition(r)
        if m.pending != "audition":
            if m.pending is None:
                # Attach/segment resume on the audition tab (2026-10-09 live): back to the lesson tab, where the
                # week survey restarts from scratch (read-only navigation).
                return _click("schedule_lesson_tab", "resume_from_audition_tab")
            return _stop("audition_tab_without_plan")
        mismatch = self._table_mismatch(r)
        if mismatch:
            return _stop("audition_table_mismatch", **mismatch)
        fn, name = self.hook("audition")
        if fn is not None:
            try:
                verdict = fn(r, m)
            except Exception as exc:
                return _stop(f"scorer_error:{type(exc).__name__}", message=str(exc))
            if not isinstance(verdict, Mapping):
                return _stop("audition_hook_bad_result", hook=name)
            choice = verdict.get("action")
            if choice == "schedule_decide":
                m.pending, m.audition_pages = None, 0     # the planned audition is entered; the plan is consumed
                season = r.get("season") if isinstance(r.get("season"), int)                     else ((m.auditions_seen or {}).get("key") or [None])[0]
                m.entered_audition = {"season": season, "reward": verdict.get("reward")}
                if m.pending_special is not None:
                    m.entered_audition["special"] = m.pending_special
                m.live_special, m.pending_special = m.pending_special, None
                return _click(choice, "audition_hook", verdict=dict(verdict))
            if choice in ("audition_next", "audition_prev"):
                m.audition_pages += 1
                return _click(choice, "audition_hook", verdict=dict(verdict))
            return _stop(f"audition_declined:{verdict.get('reason') or 'unknown'}", verdict=dict(verdict))
        # Fallback (wing_audition not available): accept only the page whose reward equals the week plan's tier.
        reward = r.get("reward")
        if reward is None:
            return _stop("audition_reward_unread", hook=name)
        if m.pending_audition is not None and reward == m.pending_audition and r.get("special") == m.pending_special:
            m.live_special = m.pending_special
            m.pending, m.pending_special = None, None
            m.entered_audition = {"season": r.get("season"), "reward": reward}
            return _click("schedule_decide", "audition_matches_plan", reward=reward, hook=name)
        if m.audition_pages >= 4:
            return _stop("planned_audition_not_found", wanted=m.pending_audition, hook=name)
        m.audition_pages += 1
        return _click("audition_next", "audition_page", reward=reward, wanted=m.pending_audition, hook=name)

    def _survey_audition(self, r: dict) -> NavDecision:
        """Read one audition page into ``memory.auditions_seen``; next page until all 4 (or a repeat) are read,
        then back to the lesson tab, where the week decision runs with the list. Unread reward -> stop."""
        m = self.memory
        seen = m.auditions_seen or {}
        reward = r.get("reward")
        if not isinstance(reward, int):
            if not seen.get("special_week"):
                return _stop("audition_reward_unread", survey=True)
            # 強化週間 final week: the limited page has no sample yet; an unread page is left out of the list.
            seen["unread_pages"] = seen.get("unread_pages", 0) + 1
            if m.audition_pages + 1 >= AUDITION_PAGES:
                seen["done"], seen["ended_by"] = True, "max_pages"
                m.pending, m.audition_pages = None, 0
                return _click("schedule_lesson_tab", "survey_done",
                              auditions=[p["reward"] for p in seen.get("pages") or ()])
            m.audition_pages += 1
            return _click("audition_next", "survey_page_unread")
        if r.get("season") is not None and r.get("season") != (seen.get("key") or [None])[0]:
            return _stop("survey_week_changed", seen=seen.get("key"), season=r.get("season"))
        pages = seen.setdefault("pages", [])
        if r.get("trend") and not seen.get("trend"):
            seen["trend"] = dict(r["trend"])
        if any(p["reward"] == reward and p.get("special") == r.get("special") for p in pages):
            # Last page (the next arrow does nothing / wraps) or a click without effect. Read-only, so the survey
            # ends here; the list may miss pages before the one the tab opened on (``complete`` False), which only
            # makes the scorer more conservative.
            seen["done"], seen["ended_by"] = True, "repeat"
        elif len(pages) >= AUDITION_PAGES:
            seen["done"], seen["ended_by"] = True, "max_pages"
        else:
            locked = r.get("locked") is True
            pages.append({"reward": reward, "difficulty": r.get("difficulty"),
                          "recommended": dict(r.get("recommended") or {}), "required_rank": r.get("required_rank"),
                          "locked": locked, "special": r.get("special")})
            # A gated tier shown without the 出場条件 overlay (AuditionLocked) is enterable: rank >= its gate.
            gate = r.get("required_rank")
            if not locked and gate in RANKS and (seen.get("rank_floor") is None
                                                 or RANKS.index(gate) > RANKS.index(seen["rank_floor"])):
                seen["rank_floor"] = gate
            seen["done"] = len(pages) >= AUDITION_PAGES
            seen["ended_by"] = "max_pages" if seen["done"] else None
        if seen["done"]:
            m.pending, m.audition_pages = None, 0
            return _click("schedule_lesson_tab", "survey_done", auditions=[p["reward"] for p in pages])
        m.audition_pages += 1
        return _click("audition_next", "survey_page", reward=reward)

    def _table_mismatch(self, r: Mapping[str, Any]) -> dict | None:
        """Entry check for a table-planned week: the shown page must be a table tier of this season and its read
        rank gate must equal the table's. None = consistent (or not a table plan / nothing read to compare)."""
        seen = self.memory.auditions_seen or {}
        if seen.get("source") != "table":
            return None
        reward = r.get("reward")
        if not isinstance(reward, int):
            return None                                  # the hook / fallback stops on an unread reward
        page = next((p for p in seen.get("pages") or () if p.get("reward") == reward), None)
        if page is None:
            return {"reward": reward, "table": [p.get("reward") for p in seen.get("pages") or ()]}
        gate = r.get("required_rank")
        if gate in RANKS and page.get("required_rank") in RANKS and gate != page["required_rank"]:
            return {"reward": reward, "required_rank": gate, "table_rank": page["required_rank"]}
        return None

    def _decide_audition_locked(self, screen: str, r: dict) -> NavDecision:
        m = self.memory
        if m.pending == "survey":
            return self._survey_audition({**r, "locked": True})
        reward = r.get("reward")
        if m.pending == "audition" and isinstance(reward, int) and m.pending_audition is not None \
                and (reward != m.pending_audition or r.get("special") != m.pending_special):
            # A locked tier on the way to the planned page (a table plan no longer surveys): turn the page.
            mismatch = self._table_mismatch({"reward": reward})
            if mismatch:
                return _stop("audition_table_mismatch", **mismatch)
            if m.audition_pages + 1 >= AUDITION_PAGES:
                return _stop("planned_audition_not_found", wanted=m.pending_audition)
            m.audition_pages += 1
            return _click("audition_next", "audition_page_locked", reward=reward, wanted=m.pending_audition)
        return _stop("audition_locked_overlay", reward=reward, wanted=m.pending_audition)

    def _decide_event(self, screen: str, r: dict) -> NavDecision:
        fn, name = self.hook("event")
        if fn is None:
            return _stop(f"scorer_missing:{name}")
        options = [(slot, text) for slot, text in (r.get("options") or {}).items() if text]
        if len(options) < 2:
            return _stop("choice_options_unread")
        order = {"Left": 0, "Center": 1, "Right": 2}
        options.sort(key=lambda st: order[st[0]])
        from agent.core.wing_events import EventOption
        try:
            event_params = dict(self.params.get("event") or {})
            choice = fn([EventOption(text, bool(PROMISE_TEXT.search(text))) for _, text in options], **event_params)
        except Exception as exc:
            return _stop(f"scorer_error:{type(exc).__name__}", message=str(exc))
        if choice.index is None:
            return _stop(f"event_{choice.rule}")
        slot = options[choice.index][0]
        if PROMISE_TEXT.search(options[choice.index][1]):
            self.memory.promise = {"text": options[choice.index][1]}
        return _click("choice_" + slot.lower(), choice.rule, text=options[choice.index][1])

    def _decide_live(self, screen: str, r: dict) -> NavDecision:
        """Live screen. With ``wing_audition.live_plan``: ``live_auto`` -> toggle the game's Auto (only when the
        user opted in and ``should_use_game_auto`` allows it), ``wait`` -> the game's Auto is playing, ``stop`` ->
        stop now with the hook's reason, ``play`` -> one guarded click intent of the current play step. Never waits
        silently on a decision it cannot carry out."""
        m = self.memory
        if m.live_auto_secured:
            # 1st place was secured and the user opted in to the game's Auto: let it finish the Live.
            if r.get("auto") == "ON":
                m.live_auto_clicks = 0
                return _wait("live:game_auto_secured")
            if r.get("auto") == "OFF":
                if m.live_auto_clicks >= 2:
                    return _stop("live_auto_toggle_no_effect")
                m.live_auto_clicks += 1
                return _click("live_auto", "live_first_secured_retry")
            return _wait("live:auto_state_unread")
        special = self._live_special_auto(r)
        if special is not None:
            return special
        fn, name = self.hook("live")
        if fn is None:
            return self._decide_live_fallback(r, name)
        live_in = dict(r)
        plan_info = (m.last_week_decision or {}).get("plan") or {}
        if "need_first" not in live_in:
            live_in["need_first"] = True if m.live_need_first is None else bool(m.live_need_first)
        if "estimate" not in live_in and isinstance(plan_info.get("audition_estimate"), Mapping):
            live_in["estimate"] = dict(plan_info["audition_estimate"])
        try:
            live_params = self.params.get("live")
            plan = fn(live_in, m, dict(live_params)) if live_params else fn(live_in, m)
        except Exception as exc:
            return _stop(f"scorer_error:{type(exc).__name__}", message=str(exc))
        if not isinstance(plan, Mapping):
            return _stop("live_hook_bad_result", hook=name)
        action = plan.get("action")
        if action == "live_auto":
            if m.live_auto_clicks >= 2:
                return _stop("live_auto_toggle_no_effect")
            m.live_auto_clicks += 1
            return _click("live_auto", "live_hook_auto", plan=dict(plan))
        if action == "wait":
            m.live_auto_clicks = 0
            return _wait(f"live:{plan.get('reason') or 'wait'}")
        if action == "play" or (action == "stop" and plan.get("reason") == "policy_no_action"):
            # The numeric scorer cannot rank without card multipliers; the focus policy decides from the screen
            # (an animation frame without hand/phase waits there).
            return self._live_play(r, plan)
        return _stop(f"live:{plan.get('reason') or 'stop'}", plan=dict(plan))

    def _skill_catalog(self) -> dict:
        """The team's Live-skill catalog (wing_skill_catalog), rebuilt when the team or the panel-read skills change."""
        m = self.memory
        team = (((m.produce or {}).get("verified_context") or {}).get("team") or m.team_observed or m.team_slots or {})
        ids = tuple(str(c) for c in (team.get("P"), *(team.get("S") or ()), team.get("guest")) if c)
        key = (ids, json.dumps(m.live_skills or [], sort_keys=True, ensure_ascii=False, default=str))
        if getattr(self, "_catalog_key", None) != key:
            from agent.core import wing_skill_catalog as wsk
            try:
                self._catalog = wsk.build_catalog(ids, m.live_skills)
            except Exception as exc:   # local library trouble only loses the catalog (the description stays)
                self.log.append({"event": "skill_catalog_failed", "error": f"{type(exc).__name__}: {exc}"})
                self._catalog = {}
            self._catalog_key = key
        return self._catalog

    def _hand_portraits(self, catalog: Mapping[str, dict]) -> list:
        """Saved hand portraits usable for this team (wing_hand_cache.resolve), reloaded after a save."""
        if self.hand_cache_path is None:
            return []
        key = getattr(self, "_catalog_key", None)
        if getattr(self, "_portraits_key", None) != key or getattr(self, "_portraits", None) is None:
            from agent.core import wing_hand_cache as whc
            try:
                self._portraits = whc.resolve(whc.load(self.hand_cache_path), catalog)
            except Exception as exc:   # an unreadable cache only means inspect taps as before
                self.log.append({"event": "hand_cache_failed", "error": f"{type(exc).__name__}: {exc}"})
                self._portraits = []
            self._portraits_key = key
        return self._portraits

    def _save_portrait(self, r: Mapping[str, Any], catalog: Mapping[str, dict]) -> None:
        """select_judge with a read tooltip: remember the card's portrait for later Produces (设置 → 存储「手牌识别」)."""
        if self.hand_cache_path is None:
            return
        from agent.core import wing_hand_cache as whc
        from agent.core import wing_live_policy as wlp
        try:
            card = wlp.portrait_record(r, self.memory, catalog)
            if card is not None and whc.record(card, catalog, self.hand_cache_path):
                self._portraits = None
        except Exception as exc:   # never blocks the Live
            self.log.append({"event": "hand_cache_failed", "error": f"{type(exc).__name__}: {exc}"})

    def _live_special_auto(self, r: Mapping[str, Any]) -> NavDecision | None:
        """A limited audition whose 特殊ルール our play cannot follow (wing_special: S2 debuff turns, S4 2way/LA) is
        played by the game's Auto: tap the toggle only when it reads OFF (its button is the guard), then the
        secured-Auto branch waits for the end. None for other Lives, or while the toggle is unread (own play goes on;
        a 1st-place requirement never comes here: the W.I.N.G. stage clears ``live_special``)."""
        from agent.core import wing_special as wsp
        m = self.memory
        special = wsp.BY_ID.get(m.live_special) if m.live_special else None
        if special is None or special.live != "game_auto" or m.live_auto_secured or m.live_need_first:
            return None
        if r.get("auto") == "ON":
            m.live_auto_secured, m.live_auto_clicks = True, 0
            return _wait("live:game_auto_special")
        if r.get("auto") != "OFF":
            return None
        m.live_step, m.live_unread = None, 0
        m.live_auto_secured, m.live_auto_clicks = True, 1
        self.log.append({"event": "live_special_auto", "special": special.id, "rule": special.rule})
        return _click("live_auto", "live_special_auto", special=special.id)

    def _live_unread_auto(self, r: Mapping[str, Any], reason: str) -> NavDecision | None:
        """2026-10-29 user (wing4b stopped on live_selected_card_unread, a two-line tooltip): a Live our play cannot
        read after the bounded re-reads is finished by the game's Auto instead of stopping the run. The Auto toggle
        must read OFF (its button is the guard); then the secured-Auto branch of ``_decide_live`` waits for the end.
        None when the toggle is unread or Auto was already handed over (the caller stops as before)."""
        m = self.memory
        if r.get("auto") != "OFF" or m.live_auto_secured:
            return None
        m.live_step, m.live_unread, self._selected_waits = None, 0, 0
        m.live_auto_secured, m.live_auto_clicks = True, 1
        self.log.append({"event": "live_unread_auto", "reason": reason})
        return _click("live_auto", "live_unread_auto", unread=reason)

    def _live_play(self, r: Mapping[str, Any], plan: Mapping[str, Any]) -> NavDecision:
        """One step of our own Live play with the trend-#1 focus policy (``wing_live_policy.plan_turn``): card
        (inspect / switch / best) -> judge -> APPEAL timing. Each step needs a positive phase reading and the previous
        step of the same turn; an animation frame (no phase) waits. With the user's ``game_auto`` opt-in, a
        mathematically secured 1st place hands the rest of the Live to the game's Auto."""
        from agent.core import wing_live_policy as wlp
        m = self.memory
        live = r.get("live_state") if isinstance(r.get("live_state"), Mapping) else {}
        phase, turn, step = r.get("live_phase"), live.get("turn"), m.live_step
        if phase not in LIVE_PHASES:
            # An animation can prove a secured lead, but a judge leaving after our appeal proves neither TA nor LA.
            # Keep the early Auto path when the observed stars already cover every remaining rival reward.
            auto_ok = m.live_mode == "game_auto" or (m.live_mode == "policy"
                                                     and self.policy_params.get("auto_when_first", True))
            known_live = {**live, "judges": [
                {**j, "trend_rank": j.get("trend_rank") or (m.live_trend or {}).get(j.get("attribute"))}
                for j in live.get("judges") or () if isinstance(j, Mapping)]}
            secured = wlp.secured_first(known_live)
            if auto_ok and r.get("auto") == "OFF" and not m.live_auto_secured and secured.get("secured"):
                m.live_step, m.live_unread = None, 0
                m.live_auto_secured, m.live_auto_clicks = True, 1
                return _click("live_auto", "live_first_secured", secured=secured)
            m.live_unread += 1
            if m.live_unread > LIVE_UNREAD_LIMIT:
                return self._live_unread_auto(r, "live_phase_unread") or _stop("live_phase_unread",
                                                                              polls=m.live_unread)
            return _wait("live:phase_unread")
        m.live_unread = 0
        judges = live.get("judges") if isinstance(live.get("judges"), list) else []
        ranks = {j.get("attribute"): j.get("trend_rank") for j in judges if isinstance(j, Mapping)}
        if sorted(v for v in ranks.values() if isinstance(v, int)) == [1, 2, 3]:
            m.live_trend = dict(ranks)
        elif m.live_trend and any(v is None for v in ranks.values()) and all(
                v is None or m.live_trend.get(a) == v for a, v in ranks.items()):
            # Trends are fixed for the whole Live; after a judge leaves (live seg23 turn 5) some ranks are unread.
            live = {**live, "judges": [{**j, "trend_rank": m.live_trend.get(j.get("attribute"))}
                                       if isinstance(j, Mapping) and j.get("trend_rank") is None else j
                                       for j in judges]}
            r = {**r, "live_state": live, "trend": dict(m.live_trend)}
        if step is not None and turn is not None and step.get("turn") is None:
            step = m.live_step = {**step, "turn": turn}   # the turn counter was hidden when the step began
        if step is not None and phase == "select_card" and (
                (turn is not None and step.get("turn") != turn) or step.get("step") in ("judge", "timing")):
            # A new turn starts on select_card only: the previous turn's plan is spent. The turn counter OCR is
            # unreliable on the other phases (live 2026-10-10: unread on select_judge in seg21, flickering 1/7 during
            # timing in seg29 -> the step was dropped, our APPEAL tap skipped and the game's timeout gave Normal).
            step = m.live_step = None
        auto_ok = m.live_mode == "game_auto" or (m.live_mode == "policy"
                                                 and self.policy_params.get("auto_when_first", True))
        if auto_ok and phase == "select_card" and step is None and r.get("auto") == "OFF":
            secured = wlp.secured_first(live)
            ours, rivals = live.get("our_stars"), live.get("rival_stars") or ()
            trend1 = next((a for a, k in (m.live_trend or {}).items() if k == 1), None)
            leading = isinstance(ours, int) and bool(rivals) and all(isinstance(v, int) and ours > v for v in rivals)
            if secured.get("secured") or (leading and trend1 in (m.live_top_appeal or ())):
                # Hand the rest to the game's Auto once 1st is secured -- mathematically, or most
                # stars after taking the trend-#1 judge's Top Appeal.
                m.live_auto_secured, m.live_auto_clicks = True, 1
                return _click("live_auto", "live_first_secured", secured=secured, ours=ours, rivals=list(rivals),
                              top_appeal=list(m.live_top_appeal or ()))
        if step is None and phase == "select_judge" and r.get("selected_card") in (1, 2, 3):
            # Resumed mid-turn (new segment / reload, live seg22): the card already selected on screen is the turn's
            # card; the card branch may still switch or inspect from here.
            step = m.live_step = {"turn": turn, "step": "card", "slot": r.get("selected_card"), "switches": 0,
                                  "inspected": [r.get("selected_card")], "resumed": True,
                                  "sigs": list(r.get("hand_sigs") or [None, None, None])}
        plan_info = (m.last_week_decision or {}).get("plan") or {}
        estimate = plan_info.get("audition_estimate") if isinstance(plan_info.get("audition_estimate"),
                                                                    Mapping) else None
        catalog = self._skill_catalog()
        from agent.core import wing_special as wsp
        special = wsp.BY_ID.get(m.live_special) if m.live_special else None
        turn_plan = wlp.plan_turn(r, m, primary=self._primary(), estimate=estimate, stats=m.last_stats,
                                  catalog=catalog, portraits=self._hand_portraits(catalog),
                                  special_rule=special.live if special is not None else None)
        self._save_portrait(r, catalog)
        action = turn_plan.get("action")
        if action == "stop":
            # 2026-10-14 live: the frame after the card tap was still mid-lift (no raised card read); the next frame
            # showed card 1 raised. Any turn-plan gap re-reads a few frames (own bounded counter) first.
            self._selected_waits = getattr(self, "_selected_waits", 0) + 1
            if self._selected_waits <= LIVE_SELECTED_WAITS:
                return _wait("live:selected_card_settling" if turn_plan.get("reason") == "live_selected_card_unread"
                             else "live:turn_reread")
        else:
            self._selected_waits = 0
        if action == "stop":
            auto = self._live_unread_auto(r, turn_plan["reason"])
            if auto is not None:
                return auto
            return _stop(turn_plan["reason"], plan={k: v for k, v in turn_plan.items() if k != "sigs"},
                         scorer=(plan.get("decision") or {}).get("option_id"))
        if action == "timing":
            if step is None or step.get("step") != "judge":
                # 2026-10-14 live a7: a segment attached while the bar ran (card + judge chosen by the previous
                # segment). The game never fires the appeal itself (9 minutes of waiting); the timing phase proves a
                # card and a judge are chosen, so the appeal is the only way forward and spends nothing.
                self.log.append({"event": "live_timing_resumed", "turn": turn, "step": dict(step or {})})
                step = {**(step or {"turn": turn, "slot": r.get("selected_card"), "switches": 0}), "resumed": True}
            m.live_step = {**step, "step": "timing", "intent": "live_appeal_timing"}
            return _click("live_appeal_timing", "live_timing", expect=_expect("timing"), step=_brief_step(m.live_step))
        if action == "card":
            slot = turn_plan["slot"]
            if phase == "select_card":
                if step is not None:
                    # 2026-10-12 live seg4: tapping another card while one is selected deselects it (nothing selected,
                    # back to select_card). Re-tap the intended card once; a second miss still stops.
                    if (step.get("step") == "card" and step.get("slot") in (1, 2, 3) and not step.get("reselected")
                            and r.get("selected_card") is None):
                        m.live_step = {**step, "reselected": True}
                        return _click(f"live_card_{step['slot']}", "live_reselect_after_deselect",
                                      step=_brief_step(m.live_step))
                    return _stop("live_step_not_confirmed", step=_brief_step(step), phase=phase)
                m.live_step = {"turn": turn, "step": "card", "slot": slot, "sigs": turn_plan.get("sigs"),
                               "switches": 0, "inspected": [slot], "target": turn_plan["target"]["attribute"]}
            else:
                if step is None or step.get("step") != "card" or r.get("selected_card") != step.get("slot"):
                    return _stop("live_step_out_of_order", step=_brief_step(step), phase=phase,
                                 selected=r.get("selected_card"))
                m.live_step = {**step, "slot": slot, "switches": step.get("switches", 0) + 1,
                               "inspected": list(step.get("inspected") or ()) + [slot],
                               "reads": turn_plan.get("reads") or step.get("reads") or {}}
            intent = f"live_card_{slot}"
            m.live_step["intent"] = intent
            return _click(intent, "live_" + turn_plan["reason"], expect=_expect("card"), step=_brief_step(m.live_step))
        if action == "judge":
            if step is None or step.get("step") != "card" or r.get("selected_card") != step.get("slot"):
                return _stop("live_step_out_of_order", step=_brief_step(step), phase=phase,
                             selected=r.get("selected_card"))
            intent = f"live_judge_{turn_plan['index']}"
            m.live_step = {**step, "step": "judge", "intent": intent, "target": turn_plan["target"]["attribute"]}
            return _click(intent, "live_" + turn_plan["reason"], expect=_expect("judge"),
                          step=_brief_step(m.live_step))
        return _stop("live_plan_unknown_action", action=action)

    def _decide_live_fallback(self, r: Mapping[str, Any], name: str) -> NavDecision:
        """wing_audition unavailable: only the user's explicit game_auto opt-in can proceed (Auto toggle / wait)."""
        mode = self.policy_params.get("live_mode", self.memory.live_mode)
        if mode != "game_auto":
            return _stop("live_agent_mode_requires_wing_audition", hook=name)
        if r.get("auto") == "OFF":
            if self.memory.live_auto_clicks >= 2:
                return _stop("live_auto_toggle_no_effect")
            self.memory.live_auto_clicks += 1
            return _click("live_auto", "game_auto_on", hook=name)
        if r.get("auto") == "ON":
            self.memory.live_auto_clicks = 0
            return _wait("game_auto_running")
        return _wait("auto_state_unread")

    def _sp_growth_preview(self, decision: Mapping, readings: Mapping):
        """Known gains of the action that prompted learning; None keeps the existing near-cap fallback."""
        if decision.get("kind") == "audition":
            return {"Vo": 0, "Da": 0, "Vi": 0}
        if decision.get("kind") in ("lesson", "work"):
            room = next((v for v in readings.get("rooms") or () if v.get("id") == decision.get("option_id")), None)
        elif decision.get("rule") == "primary_at_cap_spend_sp_first":
            primary = self._primary()
            rooms = [v for v in readings.get("rooms") or () if v.get("attribute") == primary
                     and isinstance((v.get("preview") or {}).get(primary), int)
                     and v["preview"][primary] > 0]
            room = max(rooms, key=lambda v: v["preview"][primary], default=None)
        else:
            room = None
        preview = room.get("preview") if room else None
        return dict(preview) if isinstance(preview, Mapping) and preview and all(
            type(v) is int and v >= 0 for v in preview.values()) else None

    def _sp_request(self, screen: str, key, **detail) -> NavDecision | None:
        """SP spending the scorer asked for: ``None`` = continue without spending (``skip`` mode, the one request the
        user already handled before this attach, or an ``auto`` session already run for this week/stage); ``auto``
        -> open 振り返り (pending ``spend_sp``, a new session); else the handover stop."""
        m = self.memory
        if self.options.sp_mode == "skip":
            self.log.append({"event": "sp_skipped", **{k: v for k, v in detail.items() if k == "sp"}})
            return None
        if self.options.sp_done and not m.sp_handled_once:
            m.sp_handled_once = True
            self.log.append({"event": "sp_handled_by_user"})
            return None
        if self.options.sp_mode == "auto":
            key = list(key)
            sp = detail.get("sp")
            if key in m.sp_done_keys:
                return None
            if isinstance(sp, int) and sp < SP_MIN_NODE_COST:
                m.sp_done_keys.append(key)
                self.log.append({"event": "sp_nothing_affordable", "sp": sp, "key": key})
                return None
            m.pending = "spend_sp"
            m.sp_session = {"key": key, "sp_start": sp, "taps": 0, "learned": [], "skipped": [], "catalog": {},
                            "last_tap": None, "learning": None, "owned": "todo", "stats": None, "caps": None,
                            "reentered": False, "unstable": 0, "inspected": 0}
            m.sp_session["growth_preview"] = detail.get("growth_preview")
            if not isinstance(m.sp_run, dict):
                m.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None}
            self.log.append({"event": "sp_session_start", "key": key, "sp": sp})
            return _click("page_return" if screen == "Schedule" else "hub_review", "spend_sp_auto", key=key, sp=sp)
        return _stop("sp_learn_handover", **detail)

    def _decide_final(self, screen: str, r: dict) -> NavDecision:
        m = self.memory
        if m.pending == "spend_sp":
            return _click("hub_review", "spend_sp_before_final")
        if not m.final_sp_checked or m.final_sp_checked != screen:
            m.final_sp_checked = screen
            if m.last_sp is None or m.last_sp > 0:
                handover = self._sp_request(screen, [screen], sp=m.last_sp, before=screen)
                if handover is not None:
                    return handover
        m.live_need_first = True            # semifinal / final: only 1st passes
        m.live_special = None
        return _click("hub_final_live", "start_wing_live")

    def _decide_sp(self, screen: str, r: dict) -> NavDecision:
        fn, name = self.hook("sp")
        m = self.memory
        if (m.pending is None and self.options.sp_mode == "auto" and self._last_intent
                and self._last_intent[1] == "warning_to_review"):
            # 2026-10-15: the audition's 「必ず振り返りをしてくださいね」 warning opened 振り返り without a session; spend
            # SP here for this week (the audition follows on the page this returns to).
            key = (m.auditions_seen or {}).get("key")
            if _sp_key_ok(key) and self._sp_request("Review", key, sp=r.get("sp")) is None and m.pending is None:
                self.log.append({"event": "sp_warning_no_session", "key": key})
        if m.pending != "spend_sp":
            return _click("page_return", "review_without_plan")
        if fn is None:
            return _stop(f"scorer_missing:{name}")
        if self.options.sp_mode == "skip":
            # SP kept on purpose (live 2026-10-10 seg34: an SP session left pending by an auto segment stopped the
            # skip run here). Leave the panel without marking the week done: a later auto segment of the same week
            # must still be able to learn (seg36 stopped on sp_still_requested_after_handover after a skip mark).
            m.pending, m.sp_session = None, None
            return _click("page_return", "sp_skipped")
        if self.options.sp_mode != "auto" or m.sp_session is None:
            return _stop("sp_panel_reader_missing", sp=r.get("sp"), hook=name,
                         sp_params=dict(self.params.get("sp") or {}))
        s = m.sp_session
        if s.get("bar_loop") and not (r.get("skill_info") or r.get("skill_text")):
            # 2026-10-15 live: select c08_r06 -> its bar covers the next node c09_r15 -> close (select undone) ->
            # select c08_r06 again, for minutes. After the second identical close the session ends; SP stays for later.
            # Not a planned end: the first loop of a week leaves it open so a later visit can
            # continue; a second loop in the same week marks it done (no endless re-entry).
            looped = self._sp_run().setdefault("bar_loop_keys", [])
            again = s["key"] in looped
            if not again:
                looped.append(list(s["key"]))
            return self._sp_finish(s, "sp_info_bar_loop", mark_done=again, loop=list(s["bar_loop"]))
        after = s.get("after_close") if not (r.get("skill_info") or r.get("skill_text")) else None
        if after:
            s.pop("after_close")
            panel = r.get("panel") if isinstance(r.get("panel"), Mapping) else {}
            slot = (panel.get("slots") or {}).get(after["node"]) if panel.get("canonical") else None
            if not isinstance(r.get("sp"), int) or not isinstance(slot, Mapping) or (
                    slot.get("action_intent", after["intent"]) != after["intent"]
                    or after["intent"] not in wsv.SMALL_INTENTS):
                # the covered target must still be on this frame's readable panel;
                # otherwise re-plan normally.
                after = None
        if after:
            # The bar of the previous node covered this round's target and was just closed: tap that target now
            # instead of re-planning on the bar-less frame (2026-10-15 live: the re-plan picked the covering node again).
            if s["taps"] >= SP_SESSION_MAX_TAPS:
                return _stop("sp_tap_budget_exhausted", taps=s["taps"])
            s["last_tap"] = after["node"]
            s["taps"] += 1
            return _click(after["intent"], "sp_select_after_close", node=after["node"])
        before = (s.get("taps"), s.get("inspected"), s.get("last_tap"))
        decision = self._sp_review(r, s, fn)
        slot = wsv.SMALL_INTENTS.get(decision.intent or "")
        if (decision.action == "click" and slot is not None and wsv.under_info_bar(slot)
                and (r.get("skill_info") or r.get("skill_text"))):
            # The info bar of the last tapped node covers this one: close it first, then re-decide on the next frame.
            # The previous node's info was already read this round; nothing is pending after the bar closes
            # (live a11: restoring last_tap made the closed bar read as sp_info_unread).
            s["taps"], s["inspected"] = before[0], before[1]
            s["last_tap"] = None
            pair = f"{before[2]}>{decision.intent}"
            closes = s.setdefault("bar_closes", {})
            closes[pair] = closes.get(pair, 0) + 1
            if closes[pair] >= 2:
                s["bar_loop"] = [before[2], decision.intent]
            else:
                s["after_close"] = {"intent": decision.intent, "node": wsp.slot_intent(tuple(slot))}
            if isinstance(s.get("learning"), dict) and not s["learning"].get("confirmed"):
                s["learning"] = None
            return _click(wsv.INFO_CLOSE, "sp_close_info_bar", wanted=decision.intent)
        return decision

    # ---- SP learning (sp_mode "auto") --------------------------------------------
    def _primary(self) -> str | None:
        m = self.memory
        if m.primary in ("Vo", "Da", "Vi"):
            return m.primary
        try:
            from agent.core.wing_policy import STRATEGIES
        except ImportError:
            return None
        strategy = STRATEGIES.get(m.strategy)
        return getattr(strategy, "primary", None)

    def _sp_finish(self, s: dict, reason: str, mark_done: bool = True, **detail) -> NavDecision:
        """End the session: the week/stage key is satisfied, back to the page 振り返り was opened from."""
        m = self.memory
        m.pending, m.sp_session = None, None
        if mark_done and s["key"] not in m.sp_done_keys:
            m.sp_done_keys.append(s["key"])
        self.log.append({"event": "sp_session_end", "key": s["key"], "reason": reason, "sp_start": s.get("sp_start"),
                         "learned": list(s["learned"]), "skipped": list(s["skipped"]), "taps": s["taps"],
                         "catalog": copy.deepcopy(s.get("catalog") or {})})
        return _click("page_return", reason, **{**detail, "learned": len(s["learned"])})

    def _sp_review(self, r: Mapping[str, Any], s: dict, plan_fn) -> NavDecision:
        """One 振り返り round of an ``auto`` session: verify the previous step, read the selected node's info bar,
        inspect each affordable open node once, then learn the first ``wing_sp.plan_sp`` step (select -> 習得する).

        Only the canonical view is acted on; every click is a fixed table slot. Never guesses: an unreadable or
        mismatching info bar waits a few frames, then re-enters once / stops; an unconfirmed learn stops."""
        m = self.memory
        sp = r.get("sp")
        panel = r.get("panel") if isinstance(r.get("panel"), Mapping) else {}
        brief = {k: panel.get(k) for k in ("canonical", "reason", "labels", "on_lattice", "offset", "width")}
        if panel.get("view") == "small" and not self.options.sp_small_view:
            panel = {**panel, "canonical": False, "reason": "small_view_not_enabled"}
        if not isinstance(sp, int) or not panel.get("canonical"):
            s["unstable"] += 1
            if s["unstable"] <= SP_UNSTABLE_WAITS:
                return _wait("sp_panel_settling", sp=sp, panel=brief)
            if isinstance(sp, int) and not s["reentered"] and s.get("learning") is None:
                s.update(reentered=True, unstable=0, last_tap=None)
                return _click("page_return", "sp_panel_reenter", panel=brief)
            if s.get("learning") is None:
                # 2026-10-29 user (wing5b S4: Schedule misread SP 17 as 717, the panel opened in an unmeasured zoom):
                # learning is optional, so an unreadable panel ends this week's session unspent and the run goes on.
                return self._sp_finish(s, "sp_panel_unreadable_skip", sp=sp, panel=brief)
            return _stop("sp_panel_unreadable", sp=sp, panel=brief)
        if panel.get("view") == "small" and s.get("zoom_attempted"):
            # 2026-10-25 live (wing7): 振り返り re-entered from the Hub (by Maa or by hand) opens zoomed in again, but the
            # session had already zoomed once; the default view then scrolled itself under the info bar (off_lattice).
            # The zoom-out worked: a later default-view panel zooms out again (bounded by SP_MAX_ZOOMS).
            s["zoom_attempted"] = False
        info = r.get("skill_info") if isinstance(r.get("skill_info"), Mapping) else {}
        text = r.get("skill_text") if isinstance(r.get("skill_text"), Mapping) else {}
        slots = dict(panel.get("slots") or {})
        # The selected node's 「SP: N」 label often drops out of OCR under its highlight (second demo #677, third demo
        # #494): keep the last reading of the tapped slot so the selection stays a candidate.
        seen = s.setdefault("slots_seen", {})
        seen.update({intent: dict(sl) for intent, sl in slots.items()})
        if s.get("last_tap") in seen and s["last_tap"] not in slots:
            slots[s["last_tap"]] = seen[s["last_tap"]]
        if panel.get("view") == "small" and (r.get("skill_info") or r.get("skill_text")):
            # Small view: the open info bar hides the lower petals' outer nodes; keep their last reading (they are
            # tapped after closing the bar, see _decide_review).
            for intent, sl in seen.items():
                small = wsv.SMALL_INTENTS.get(sl.get("action_intent") or "")
                if intent not in slots and small is not None and wsv.under_info_bar(small):
                    slots[intent] = sl
        learning = s.get("learning")
        if learning is not None:
            if learning.get("cancelled"):
                if sp != learning["sp_before"]:
                    return _stop("sp_changed_after_cancel", sp=sp, learning=dict(learning))
                s["skipped"].append(learning["intent"])
                s["learning"] = None
                self.log.append({"event": "sp_skip", "intent": learning["intent"], "why": learning.get("why")})
                if learning.get("kind") == "live_skill" and len(s["key"]) != 1:
                    # 2026-10-14 live a8/a9: after the 3.5x 「にゃんにゃんにゃ～ん♡」 was cancelled, the session spent its
                    # 80 SP on two weak passives. Hold that skill's cost for the next session and spend only the rest
                    # (a13: ending the session instead left 200+ SP with Vo at its cap before the final). The last
                    # chance (stage key: semifinal / final) holds nothing.
                    s["hold_sp"] = max(int(s.get("hold_sp") or 0), int(learning.get("cost") or 0))
            elif (info.get("state") in ("learned", "delete") or learning.get("confirmed"))\
                    and sp == learning["sp_before"] - learning["cost"]:
                # live 2026-10-10 seg37: after 入れ替え OK the info box still showed the stale 「learnable」 state.
                # 2026-10-09 live seg12: after 入れ替え OK the node label was unread while SP dropped by exactly the cost;
                # a confirmed learn (OK pressed) plus the exact SP drop is proof enough.
                new_skill = {"name": learning.get("name"), "attributes": list(learning.get("attributes") or ()),
                             "multiplier": learning.get("multiplier"), "kind": "live_skill",
                             "skill_identity": learning.get("skill_identity")}
                if learning.get("forget") and m.live_skills:
                    row = learning["forget"].get("row")
                    m.live_skills = [k for k in m.live_skills if k.get("row") != row] + [dict(new_skill, row=row)]
                elif learning.get("kind") == "live_skill" and m.live_skills is not None and len(m.live_skills) < 4:
                    m.live_skills.append(dict(new_skill, row=len(m.live_skills) + 1))
                s["learned"].append({k: learning.get(k) for k in ("intent", "name", "kind", "cost", "tier")})
                run = self._sp_run()
                if learning.get("kind") == "live_skill" and learning.get("skill_identity"):
                    mastered = run.setdefault("mastered_skills", [])
                    if learning["skill_identity"] not in mastered:
                        mastered.append(learning["skill_identity"])
                run["spent"] = int(run.get("spent") or 0) + int(learning["cost"])
                if learning.get("kind") == "cap_up" and isinstance(learning.get("value"), (int, float)):
                    # live 2026-10-10: the cap read on 所持スキル stayed 536 after each learned cap, so every round
                    # still saw the primary at its cap and bought ten caps; raise the session's caps by the amount.
                    for attr in learning.get("attributes") or ():
                        for caps in (s.get("caps"), m.stat_caps):
                            if isinstance(caps, dict) and isinstance(caps.get(attr), int):
                                caps[attr] += int(learning["value"])
                s["catalog"][learning["intent"]]["state"] = "learned"
                run.setdefault("learned", [])
                if learning["intent"] not in run["learned"]:
                    run["learned"].append(learning["intent"])
                run["catalog"][learning["intent"]] = copy.deepcopy(s["catalog"][learning["intent"]])
                s["learning"], s["unstable"] = None, 0
                # live 2026-10-10 seg13/seg17: the just-learned node shows no info box; don't re-inspect it.
                s["last_tap"] = None
                self.log.append({"event": "sp_learned", "intent": learning["intent"], "name": learning.get("name"),
                                 "cost": learning["cost"], "sp": sp})
            elif (sp == learning["sp_before"] and not learning.get("confirmed") and not learning.get("replace_sent")
                  and info.get("state") == "learnable" and wsp.same_skill(text.get("name"), learning.get("name"))
                  and s["unstable"] >= SP_LEARN_RETRY_WAITS and s.get("learn_retries", 0) < 1):
                # Live seg8: 習得する never opened スキル習得確認 (the tap did not reach the game). 習得確認 OK
                # was never sent (``confirmed`` False), SP is unchanged and the same node is still learnable -> nothing
                # was spent: retry the learn chain once from this frame (select is kept, 習得する again).
                s["learn_retries"] = s.get("learn_retries", 0) + 1
                s["learning"], s["unstable"] = None, 0
                self.log.append({"event": "sp_learn_retry", "intent": learning["intent"], "sp": sp})
            elif sp == learning["sp_before"] and s["unstable"] < SP_UNSTABLE_WAITS:
                s["unstable"] += 1                       # the learn animation may still cover the info bar
                return _wait("sp_learn_settling", learning=dict(learning))
            else:
                return _stop("sp_learn_unconfirmed", sp=sp, state=info.get("state"), learning=dict(learning))
        last = s.get("last_tap")
        if last is not None and not info and not text and s.get("learning") is None and (
                s.get("resumed_tap") or panel.get("view") == "small"):
            # 2026-10-14 live a11/a12: no info bar at all (closed, or a new segment resumed after the bar was gone).
            # An inspect tap spends nothing; forget it and re-plan (the node is read again if still needed).
            s["unstable"] += 1
            if s["unstable"] <= SP_UNSTABLE_WAITS:
                return _wait("sp_info_settling", slot=last)
            self.log.append({"event": "sp_inspect_dropped", "slot": last})
            s["last_tap"], s["unstable"], last = None, 0, None
        s.pop("resumed_tap", None)        # only the first tap of a resumed segment
        if last is not None:
            slot = slots.get(last)
            if not info.get("state") or not text.get("name"):
                s["unstable"] += 1
                if s["unstable"] <= SP_UNSTABLE_WAITS:
                    return _wait("sp_info_settling", slot=last)
                return _stop("sp_info_unread", slot=last, info=dict(info), text=dict(text))
            label_cost = slot.get("cost") if slot else None
            if label_cost not in (None, 0) and info.get("cost") is not None and info["cost"] != label_cost:
                return _stop("sp_info_slot_mismatch", slot=last, label_cost=label_cost, info_cost=info.get("cost"))
            entry = s["catalog"].setdefault(last, {})
            if entry.get("state") != "learned":
                entry.update(name=text.get("name"), kind=text.get("kind"),
                             attributes=list(text.get("attributes") or ()), value=text.get("value"),
                             multiplier=text.get("multiplier"), cost=label_cost or info.get("cost"),
                             state=info.get("state"), condition=text.get("condition"),
                             source=wsp.slot_source(tuple(slot["slot"])) if slot else "unknown")
                self._sp_remember(last, entry, slot, slots, info.get("state"))
        s["unstable"] = 0
        run = self._sp_run()
        chosen = s.get("selected")
        if (isinstance(chosen, Mapping) and last is not None and chosen.get("intent") == last and s.get("learning") is None
                and info.get("state") == "learnable" and wsp.same_skill(text.get("name"), chosen.get("name"))
                and info.get("cost") == chosen.get("cost") and isinstance(sp, int) and sp >= chosen["cost"]):
            # 2026-10-15 live (Vo 1114/1114, 84 SP): the planned Vo上限+50 node was selected, but its own info bar
            # covers it, so the re-plan on that frame lost the node, picked another one and closed the bar; nothing was
            # learned in three sessions. The step was planned on the bar-less frame: learn it now.
            s.pop("selected")
            entry = s["catalog"].setdefault(last, {})
            s["learning"] = {"intent": last, "cost": int(chosen["cost"]), "sp_before": sp, "name": chosen.get("name"),
                             "kind": entry.get("kind"), "multiplier": entry.get("multiplier"),
                             "attributes": list(entry.get("attributes") or ()), "forget": None, "cancelled": False,
                             "confirmed": False, "tier": chosen.get("tier"), "value": entry.get("value"),
                             "skill_identity": chosen.get("skill_identity")}
            return _click("skill_learn", "sp_learn", step=chosen.get("step"), selected_frame=True)
        learned = {e["intent"] for e in s["learned"]} | set(run.get("learned") or ())
        learned.update(intent for intent, slot in slots.items()
                       if slot.get("state") == "learned" or (slot.get("cost") == 0
                           and slot.get("state") not in ("open", "learnable")))
        run["learned"] = sorted(learned)
        for intent in learned & set(slots):                # learned this session: the label may still lag
            slots[intent] = {**slots[intent], "state": "learned", "cost": 0}
        candidates = [intent for intent, sl in slots.items()
                      if sl.get("state") == "open" and isinstance(sl.get("cost"), int) and 0 < sl["cost"] <= sp
                      and intent not in s["skipped"] and intent not in learned]
        if s["owned"] == "todo" and candidates:
            s["owned"] = "opened"                        # stats / caps for the cap rule, once per session
            return _click("skill_owned", "sp_read_caps")
        if (self.options.sp_small_view and panel.get("view", "canonical") == "canonical"
                and not s.get("zoom_attempted") and s.get("learning") is None and s.get("zooms", 0) < SP_MAX_ZOOMS):
            # 2026-10-14 user + supervised sample: the small view shows all six petals (outer gold skills included);
            # plan on it from the start instead of only when a route leaves the default view.
            s["zoom_attempted"], s["last_tap"], s["zooms"] = True, None, s.get("zooms", 0) + 1
            return _click(wsv.ZOOM_OUT, "sp_zoom_out_session")
        sp_params = dict(self.params.get("sp") or {})
        sp_params.setdefault("p_card_weak", p_card_weak((m.team_observed or m.team_slots or {}).get("P")))
        lookahead = sp_params.get("lookahead", True) is not False
        income = _sp_future_income(s["key"], sp, int(run.get("spent") or 0))
        graph = wsp.panel_paths(slots, blocked=list(run.get("blocked") or ()) + list(s["skipped"]))
        known = {**run["catalog"], **{k: v for k, v in s["catalog"].items() if v.get("kind") or v.get("name")}}
        routes = self._sp_local_routes(slots, known, learned)
        if routes["graph"]:
            graph.update(routes["graph"])
            for intent, detail in routes["details"].items():
                observed = known.get(intent) or {}
                merged = {**detail, **observed}
                # The observation keeps the name/state; identity and pricing are recalculated from the
                # current mastered history. An earlier inspect may predate learning the same skill elsewhere.
                for field in ("skill_identity", "effects", "duplicate_skill", "cost"):
                    if field in detail:
                        merged[field] = copy.deepcopy(detail[field])
                known[intent] = merged
            s["route_summary"] = [{k: route.get(k) for k in ("node_id", "card_id", "name", "multiplier",
                                                            "remaining_cost", "petal")} for route in routes["routes"]]
        # Pricing a duplicate bridge is known; clicking an unopened zero-SP node has no supervised sample yet.
        # Keep it in the route summary, exclude only its execution path, and continue other learnable targets.
        zero_bridges = {k for k in graph if known.get(k, {}).get("duplicate_skill") and k not in learned}
        s["zero_cost_bridges_unverified"] = sorted(zero_bridges)
        if zero_bridges:
            blocked_zero = zero_bridges | {k for k, g in graph.items()
                                           if any(r in zero_bridges for r in g.get("requires") or ())}
            graph = {k: g for k, g in graph.items() if k not in blocked_zero}
        # 2026-10-14 live a8: a node cancelled at 習得確認 (name mismatch) came back through the catalog route graph and
        # was re-tapped 20 times. Skipped nodes and everything that needs them leave this session's graph.
        if s["skipped"]:
            dropped = set(s["skipped"])
            while True:
                more = {k for k, g in graph.items() if k not in dropped
                        and any(r in dropped for r in (g.get("requires") or ()))}
                if not more:
                    break
                dropped |= more
            for k in dropped:
                graph.pop(k, None)
        # Inspect each node once per Produce (info bar of a 未解放 node shows name/description/cost too: third demo
        # #402/#498/#548): open affordable nodes and, with the lookahead, every node whose whole path fits the SP now
        # plus the safe future income -- cheapest path first, bounded per session.
        horizon = sp + (int(income["future"] * float(sp_params.get("income_safety", 0.7))) if lookahead else 0)
        unseen = [c for c in candidates if c not in s["catalog"]]
        if lookahead and candidates:
            unseen += [c for c, g in graph.items() if not g["blocked"] and c not in known and c not in unseen
                       and c not in learned and g["cost"] <= horizon]
        unseen = [c for c in unseen if c not in s["catalog"] and c not in run["catalog"]]
        if unseen and not routes["graph"] and s.get("inspected", 0) < sp_inspect_budget(sp):
            if s["taps"] >= SP_SESSION_MAX_TAPS:
                return _stop("sp_tap_budget_exhausted", taps=s["taps"])
            target = min(unseen, key=lambda c: ((graph.get(c) or {}).get("cost") or 0, _slot_distance(slots[c]), c))
            s["last_tap"] = target
            s["taps"] += 1
            s["inspected"] = s.get("inspected", 0) + 1
            return _click(slots[target].get("action_intent", target), "sp_inspect", slot=slots[target]["slot"], cost=slots[target]["cost"],
                          path_cost=(graph.get(target) or {}).get("cost"))
        primary = self._primary()
        if primary is None:
            return _stop("sp_primary_unknown", strategy=m.strategy)
        from agent.core import wing_sp
        stats = s.get("stats") or m.last_stats or {}
        caps = s.get("caps") or m.stat_caps or {}
        skills = []
        for k in m.live_skills or ():
            attrs = k.get("attributes") or ()
            if k.get("multiplier") is not None:
                skills.append(wing_sp.LiveSkill(k.get("name") or f"row{k.get('row')}",
                                                attrs[0] if len(attrs) == 1 else None, float(k["multiplier"]),
                                                k.get("skill_identity")))
        nodes = self._sp_nodes(graph, slots, known, skills, sp, lookahead)
        extra = {}
        if plan_fn is wing_sp.plan_sp:
            extra = {"stats": dict(stats)} if stats else {}
            if lookahead:
                extra["future_sp"] = int(income["future"])
            # Stage key (HubSemiFinal / HubFinal): the last panel visits of the run. Spending down there is the
            # opt-in setting ``sp.spend_down_last_chance`` (default off: leftover SP at the end is acceptable).
            extra["last_chance"] = len(s["key"]) == 1
            preview = s.get("growth_preview")
            if isinstance(preview, Mapping) and isinstance(stats.get(primary), int):
                extra["projected_stat"] = stats[primary] + int(preview.get(primary, 0))
            growth = _stat_growth_left(s["key"], stats.get(primary), run)
            if growth is not None:
                extra["final_stat"] = extra.get("projected_stat", stats[primary]) + growth
            extra["mastered_skills"] = run.get("mastered_skills") or ()
        hold = int(s.get("hold_sp") or 0)
        try:
            plan = plan_fn(nodes, sp=max(0, sp - hold), primary=primary, stat=stats.get(primary), cap=caps.get(primary),
                           skills=skills, wing_won=m.wing_won, params=sp_params or None, **extra)
        except Exception as exc:  # scorer errors stop the run; never guess
            return _stop(f"scorer_error:{type(exc).__name__}", message=str(exc))
        target_ids = tuple(getattr(plan, "target", ()) or ())
        run["plan"] = {"key": list(s["key"]), "sp": sp, "reserve": int(getattr(plan, "reserve", 0) or 0),
                       "income": income, "final_stat": extra.get("final_stat"),
                       "target": [{"intent": t, "name": (known.get(t) or {}).get("name"),
                                  "value": (known.get(t) or {}).get("value"),
                                  "path_cost": (graph.get(t) or {}).get("cost")}
                                 for t in target_ids]}
        # Local routes are planning data. Inspect only a required unknown step, or
        # an unbound card's petal; never hand a catalog ID to the click executor.
        if routes["graph"]:
            # 2026-10-14 live (S3, 274 SP): only the P petal was bound, so every S-card node (Vo上限 SP20, the gold
            # 4-5x skills) planned as an unknown passive worth 0 and nothing was learned for weeks. The scouts below
            # only looked at the route's own petal; first read one node of each unbound petal (cheapest first).
            unbound = [c for c in unseen if c in slots and slots[c].get("petal") and slots[c]["petal"] not in routes["bindings"]]
            if unbound and s.get("inspected", 0) < sp_inspect_budget(sp):
                if s["taps"] >= SP_SESSION_MAX_TAPS:
                    return _stop("sp_tap_budget_exhausted", taps=s["taps"])
                target = min(unbound, key=lambda c: ((graph.get(c) or {}).get("cost") or 0, _slot_distance(slots[c]), c))
                s["last_tap"], s["taps"] = target, s["taps"] + 1
                s["inspected"] = s.get("inspected", 0) + 1
                return _click(slots[target].get("action_intent", target), "sp_bind_inspect", slot=slots[target]["slot"],
                              petal=slots[target]["petal"], cost=slots[target].get("cost"))
            first = plan.steps[0].node_id if plan.steps else None
            # 2026-10-15: with no step yet, the plan may still reserve SP for a target whose route
            # has no tap target in this view (a catalog-only node or one behind it). Scout that target's petal (bound
            # or not) like a hidden first step; only a scouted target releases its reserve to the visible nodes.
            hidden = [] if first else [x for t in target_ids for x in (t, *((graph.get(t) or {}).get("requires") or ()))
                                       if str(x).startswith("catalog:")]
            hidden_target = bool(hidden)
            focus = first if first and first.startswith("catalog:") else (hidden[0] if hidden else None)
            wanted = known.get(focus or first) or {}
            scouts = [c for c in unseen if (slots[c].get("petal") == wanted.get("petal")
                      if focus else slots[c].get("petal") not in routes["bindings"])]
            if focus:
                scouts.sort(key=lambda c: (slots[c].get("cost") != wanted.get("cost"), _slot_distance(slots[c]), c))
            else:
                scouts.sort(key=lambda c: ((graph.get(c) or {}).get("cost") or 0, _slot_distance(slots[c]), c))
            if scouts and (first is None or first.startswith("catalog:")) and s.get("inspected", 0) < sp_inspect_budget(sp):
                if s["taps"] >= SP_SESSION_MAX_TAPS:
                    return _stop("sp_tap_budget_exhausted", taps=s["taps"])
                target = scouts[0]
                s["last_tap"], s["taps"] = target, s["taps"] + 1
                s["inspected"] = s.get("inspected", 0) + 1
                return _click(slots[target].get("action_intent", target), "sp_route_inspect", slot=slots[target]["slot"],
                              target=focus or first, expected_name=wanted.get("name"), routes=s.get("route_summary"))
            if hidden_target and scouts:
                # The inspect budget ran out before the target's petal was read: not proven unreachable, keep the reserve.
                self.log.append({"event": "sp_reserve_unverified", "target": focus, "scouts_left": len(scouts)})
                hidden_target = False
            if (first and first.startswith("catalog:")) or hidden_target:
                if (self.options.sp_small_view and panel.get("view", "canonical") == "canonical"
                        and not s.get("zoom_attempted") and s.get("zooms", 0) < SP_MAX_ZOOMS):
                    s["zoom_attempted"], s["last_tap"], s["zooms"] = True, None, s.get("zooms", 0) + 1
                    return _click(wsv.ZOOM_OUT, "sp_show_deep_route", target=first, name=wanted.get("name"))
                # 2026-10-14 live (W.I.N.G. week 4-7, SP 152): the local route wanted a planning-only P-card node, so the
                # session ended with an open 50 SP 「Vocal上限UP +100」 on screen and Vo at its cap. Re-plan with the
                # on-screen nodes only; stop as before only when that plan has nothing to learn either. This holds for a
                # hidden Live-skill route too: the scouts above already inspected its petal, so the node has no tap
                # target in this view (live 4-5/4-4: 「HoPPoPらびっつ」 at slot (-1,6) held 77 SP for weeks).
                visible = {k: g for k, g in graph.items() if not k.startswith("catalog:")
                           and not any(str(r).startswith("catalog:") for r in g.get("requires") or ())}
                try:
                    plan = plan_fn(self._sp_nodes(visible, slots, known, skills, sp, lookahead), sp=max(0, sp - hold), primary=primary,
                                   stat=stats.get(primary), cap=caps.get(primary), skills=skills, wing_won=m.wing_won,
                                   params=sp_params or None, **extra)
                except Exception as exc:  # scorer errors stop the run; never guess
                    return _stop(f"scorer_error:{type(exc).__name__}", message=str(exc))
                if not plan.steps or plan.steps[0].node_id not in slots:
                    return self._sp_finish(s, "sp_route_not_visible", target=first, name=wanted.get("name"),
                                           sp_left=sp, route=run["plan"])
                self.log.append({"event": "sp_route_fallback", "route_target": focus or first, "name": wanted.get("name"),
                                 "step": plan.steps[0].node_id})
                nodes = self._sp_nodes(visible, slots, known, skills, sp, lookahead)
                run["plan"]["reserve"] = int(getattr(plan, "reserve", 0) or 0)
                run["plan"]["target"] = [{"intent": t, "name": (known.get(t) or {}).get("name"),
                                          "value": (known.get(t) or {}).get("value"),
                                          "path_cost": (graph.get(t) or {}).get("cost")}
                                         for t in tuple(getattr(plan, "target", ()) or ())]
        # Decision trace (live 2026-10-10: the learned node could not be explained after the run).
        sp_trace = {"nodes": [[n.id, n.kind, n.cost, n.value, list(n.attributes), n.source, list(n.requires)]
                              for n in nodes if n.value or n.kind == "live_skill"],
                    "skills": [[k.id, k.attribute, k.multiplier] for k in skills], "primary": primary,
                    "stat": stats.get(primary), "cap": caps.get(primary), "reasons": list(plan.reasons),
                    "steps": [[st.node_id, st.category, st.tier, st.forget] for st in plan.steps],
                    "plan": {k: run["plan"][k] for k in ("target", "reserve", "income")}}
        if not plan.steps:
            return self._sp_finish(s, "sp_session_done", reasons=list(plan.reasons), sp_left=sp, sp_trace=sp_trace)
        if len(s["learned"]) >= SP_SESSION_MAX_LEARN:
            return self._sp_finish(s, "sp_session_learn_cap", sp_left=sp)
        step = plan.steps[0]
        target = step.node_id
        s["catalog"][target] = dict(known.get(target) or s["catalog"].get(target) or {})
        entry = s["catalog"][target]
        if s.get("last_tap") != target:
            if s["taps"] >= SP_SESSION_MAX_TAPS:
                return _stop("sp_tap_budget_exhausted", taps=s["taps"])
            s["last_tap"] = target
            s["taps"] += 1
            cost = (slots.get(target) or {}).get("cost")
            s["selected"] = {"intent": target, "name": entry.get("name"), "cost": cost if isinstance(cost, int) and cost > 0 else entry.get("cost"),
                             "tier": step.tier, "skill_identity": entry.get("skill_identity"), "step": dataclasses.asdict(step)}
            return _click(slots[target].get("action_intent", target), "sp_select", step=dataclasses.asdict(step), sp_trace=sp_trace)
        if info.get("state") != "learnable" or not wsp.same_skill(text.get("name"), entry.get("name")):
            s["skipped"].append(target)
            return _wait("sp_target_not_learnable", slot=target, state=info.get("state"))
        s["learning"] = {"intent": target, "cost": int(entry["cost"]), "sp_before": sp, "name": entry.get("name"),
                         "kind": entry.get("kind"), "multiplier": entry.get("multiplier"),
                         "attributes": list(entry.get("attributes") or ()), "forget": None, "cancelled": False,
                         "confirmed": False, "tier": step.tier, "value": entry.get("value"),
                         "skill_identity": entry.get("skill_identity")}
        return _click("skill_learn", "sp_learn", step=dataclasses.asdict(step), sp_trace=sp_trace)

    def _sp_run(self) -> dict:
        m = self.memory
        if not isinstance(m.sp_run, dict):
            m.sp_run = {"catalog": {}, "spent": 0, "blocked": [], "plan": None}
        m.sp_run.setdefault("catalog", {})
        m.sp_run.setdefault("blocked", [])
        m.sp_run.setdefault("spent", 0)
        mastered = set(m.sp_run.get("mastered_skills") or ())
        mastered.update(k["skill_identity"] for k in m.live_skills or () if k.get("skill_identity"))
        m.sp_run["mastered_skills"] = sorted(mastered)
        return m.sp_run

    def _sp_local_routes(self, slots: Mapping, known: Mapping, learned) -> dict:
        """Resolve six-card local routes once; observed names bind their real petals."""
        from agent.core.wing_sp_catalog import load_sp_catalog, bind_sp_catalog
        m = self.memory
        team = m.team_observed or m.team_slots or {}
        ids = [team.get("P"), *(team.get("S") or ()), team.get("guest")]
        if len(ids) != 6 or not all(ids):
            return {"graph": {}, "details": {}, "bindings": {}, "routes": []}
        signature = json.dumps({"team": team, "owned": getattr(self, "owned_cards", None)}, sort_keys=True)
        cache = getattr(self, "_sp_local_cache", None)
        if cache is None or cache["signature"] != signature:
            cache = {"signature": signature,
                     "catalog": load_sp_catalog(team, getattr(self, "owned_cards", None), cards=self._library_cards())}
            self._sp_local_cache = cache
        run = self._sp_run()
        bindings = run.get("petal_cards") or {}
        if run.get("team_signature") != signature:
            bindings = {}
        if team.get("P"):
            bindings = {**bindings, wsp.P_PETAL: team["P"]}
        bound = bind_sp_catalog(cache["catalog"], slots, known, petal_cards=bindings, learned=learned,
                                mastered_skills=run.get("mastered_skills") or ())
        run["mastered_skills"] = bound.get("mastered_skills") or []
        run["team_signature"], run["petal_cards"] = signature, dict(bound["bindings"])
        return bound

    def _sp_remember(self, intent: str, entry: Mapping[str, Any], slot, slots: Mapping, state) -> None:
        """Keep a node's static info for the whole Produce; a 未解放 node next to the cluster or a learned node is
        blocked (padlock ☆ / event) and never planned through."""
        run = self._sp_run()
        if entry.get("name") and entry.get("kind"):
            run["catalog"][intent] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in entry.items()
                                      if k in ("name", "kind", "attributes", "value", "multiplier", "cost", "source",
                                               "condition", "skill_identity", "effects", "duplicate_skill")}
        if state == "locked" and slot:
            here = tuple(slot["slot"])
            learned = {tuple(sl["slot"]) for sl in slots.values() if sl.get("state") == "learned" or sl.get("cost") == 0}
            if (wsp.hex_distance(here) == 2 or any(n in learned for n in wsp.neighbours(here))) \
                    and intent not in run["blocked"]:
                run["blocked"].append(intent)
                self.log.append({"event": "sp_node_blocked", "intent": intent, "name": entry.get("name")})

    def _sp_nodes(self, graph: Mapping, slots: Mapping, known: Mapping, skills, sp: int, lookahead: bool) -> list:
        """``wing_sp.SPNode`` per reachable node: its path (``requires``), info-bar kind/value (passives whose
        condition cannot be met in W.I.N.G. are worth 0). The legacy ``upgrades`` relation does not replace a base
        skill; strict duplicate identity controls free bridges. Uninspected path nodes have zero modeled value.
        Their info is read when they are selected. Without the
        lookahead only open nodes take part (legacy)."""
        from agent.core import wing_sp
        nodes = []
        for intent, g in graph.items():
            if g["blocked"] or g["requires"] is None or (not lookahead and g["requires"]):
                continue
            e = known.get(intent) or {}
            sl = slots.get(intent) or {}
            cost = sl.get("cost") if isinstance(sl.get("cost"), int) and sl["cost"] > 0 else e.get("cost")
            duplicate = bool(e.get("duplicate_skill") and e.get("skill_identity"))
            if duplicate:
                cost = 0
            elif not isinstance(cost, int) or cost <= 0:
                cost = wsp.ring_cost(tuple(sl["slot"])) if sl.get("slot") else None
            if cost is None:
                continue
            source = e.get("source") or (wsp.slot_source(tuple(sl["slot"])) if sl.get("slot") else "unknown")
            if e.get("kind") not in wing_sp.NODE_KINDS or e.get("value") is None:
                if not lookahead:
                    continue
                nodes.append(wing_sp.SPNode(intent, "passive", int(cost), 0.0, (), requires=tuple(g["requires"]),
                                            source=source))
                continue
            value = float(e["value"])
            members = [card_idol_name(c) for c in ((self.memory.team_observed or self.memory.team_slots or {})
                                                   .get("S") or ())]
            if e["kind"] == "passive" and wsp.condition_met(e.get("condition"), self.memory.p_idol,
                                                            [n for n in members if n]) is False:
                value = 0.0
            upgrades = None
            name = e.get("name") or ""
            if e["kind"] == "live_skill" and name.endswith("+"):
                base = name[:-1]
                held = next((k.id for k in skills if wsp.same_skill(k.id, base)), None)
                on_path = next((r for r in g["requires"] if wsp.same_skill((known.get(r) or {}).get("name"), base)),
                               None)
                upgrades = held or on_path
            nodes.append(wing_sp.SPNode(intent, e["kind"], int(cost), value,
                                        tuple(a for a in e.get("attributes") or () if a in ("Vo", "Da", "Vi", "Me")),
                                        requires=tuple(g["requires"]), upgrades=upgrades, source=source,
                                        skill_identity=e.get("skill_identity"), duplicate_skill=duplicate))
        return nodes

    def _decide_owned(self, screen: str, r: dict) -> NavDecision:
        """所持スキル: read stats/caps for the SP session, then leave with ← (returns to the page 振り返り came from)."""
        s = self.memory.sp_session
        if s is not None and s.get("owned") == "opened":
            owned = r.get("owned")
            if isinstance(owned, Mapping) and set(owned) >= {"Vo", "Da", "Vi", "Me"}:
                s["stats"] = {k: int(v[0]) for k, v in owned.items()}
                s["caps"] = {k: int(v[1]) for k, v in owned.items()}
                s["owned"] = "read"
                self.memory.stat_caps = dict(s["caps"])     # the schedule scorer needs them too
            else:
                s["owned"] = "unread"
            s["last_tap"], s["unstable"] = None, 0
            self.log.append({"event": "sp_owned", "state": s["owned"], "caps": s["caps"]})
        return _click("page_return", "owned_return")

    def _sp_learning(self) -> dict | None:
        s = self.memory.sp_session
        if self.options.sp_mode != "auto" or self.memory.pending != "spend_sp" or s is None:
            return None
        return s.get("learning")

    def _decide_sp_confirm(self, screen: str, r: dict) -> NavDecision:
        """スキル習得確認: OK only for the planned step whose cost / SP before ▶ after / name match the dialog."""
        learning = self._sp_learning()
        if learning is None or learning.get("confirmed") or learning.get("cancelled"):
            return _click("learn_cancel", "no_planned_sp_step")
        lc = r.get("learn_confirm") if isinstance(r.get("learn_confirm"), Mapping) else {}
        problems = []
        if lc.get("cost") != learning["cost"]:
            problems.append("cost")
        if lc.get("sp_before") is not None and lc["sp_before"] != learning["sp_before"]:
            problems.append("sp_before")
        if lc.get("sp_after") is not None and lc["sp_after"] != learning["sp_before"] - learning["cost"]:
            problems.append("sp_after")
        if lc.get("name") and learning.get("name") and not wsp.same_skill(lc["name"], learning["name"]):
            problems.append("name")
        if problems:
            learning.update(cancelled=True, why="confirm_mismatch:" + ",".join(problems))
            return _click("learn_cancel", "sp_confirm_mismatch", problems=problems, read=dict(lc))
        learning["confirmed"] = True
        return _click("learn_ok", "sp_learn_confirmed", cost=learning["cost"], name=learning.get("name"))

    def _decide_sp_replace(self, screen: str, r: dict) -> NavDecision:
        """Full-screen スキル入れ替え確認 (first demo layout): OK only for the planned forget/new pair, else キャンセル."""
        learning = self._sp_learning()
        rep = r.get("replace") if isinstance(r.get("replace"), Mapping) else {}
        if (learning is not None and learning.get("forget") and not learning.get("cancelled")
                and _same_forget(rep.get("forget"), learning)
                and wsp.same_skill(rep.get("new"), learning.get("name"))):
            return self._replace_confirmed(learning, "replace_confirm_ok")
        if learning is not None and learning.get("forget"):
            learning.update(cancelled=True, why="replace_confirm_mismatch")
        return _click("replace_cancel", "no_planned_sp_step")

    def _replace_confirmed(self, learning: dict, intent: str) -> NavDecision:
        learning["replace_sent"] = True
        return _click(intent, "sp_replace_confirmed", forget=dict(learning["forget"]), new=learning.get("name"))

    def _decide_sp_replace_dialog(self, screen: str, r: dict) -> NavDecision:
        """スキル入れ替え確認: OK only when it names exactly the planned forget and the planned new skill, and the read
        OK button lies inside the measured ``replace_ok`` rect (the dialog height follows the descriptions: second demo
        #744 OK at y 524–576; the third demo's taller dialog was tapped around y 616)."""
        learning = self._sp_learning()
        if learning is None or not learning.get("forget") or learning.get("cancelled"):
            return _click("replace_dialog_cancel", "no_planned_sp_step")
        rep = r.get("replace") if isinstance(r.get("replace"), Mapping) else {}
        if _same_forget(rep.get("forget"), learning) and wsp.same_skill(rep.get("new"), learning.get("name")):
            if not wsp.box_inside(rep.get("ok_box"), TARGETS["replace_ok"][0]):
                return _stop("sp_replace_ok_layout_unmeasured", ok_box=rep.get("ok_box"),
                             forget=dict(learning["forget"]), new=learning.get("name"))
            return self._replace_confirmed(learning, "replace_ok")
        learning.update(cancelled=True, why="replace_mismatch")
        if not wsp.box_inside(rep.get("cancel_box"), TARGETS["replace_dialog_cancel"][0]):
            return _stop("sp_replace_cancel_layout_unmeasured", read={k: rep.get(k) for k in ("forget", "new")})
        return _click("replace_dialog_cancel", "sp_replace_mismatch", read=dict(rep))

    def _decide_item_use(self, screen: str, r: dict) -> NavDecision:
        """使用確認 of a Produce item: never confirmed (items are not automated; AGENTS: no generic item use)."""
        return _click("item_use_cancel", "item_use_disabled")

    def _decide_sp_forget(self, screen: str, r: dict) -> NavDecision:
        """忘れるスキル選択 (4 Live skills held): forget the weakest (off-attribute first) only when it is strictly weaker
        than the planned skill. Forgetting is irreversible: an unsampled 選択 target hands over (the dialog stays open
        for the user); no weaker skill / unreadable rows -> キャンセル and the node is skipped."""
        learning = self._sp_learning()
        if learning is None or learning.get("cancelled") or not learning.get("confirmed"):
            return _click("forget_cancel", "no_planned_sp_step")
        rows = r.get("forget_rows")
        if not rows:
            learning.update(cancelled=True, why="forget_rows_unread")
            return _click("forget_cancel", "sp_forget_rows_unread")
        self.memory.live_skills = [{k: row.get(k) for k in ("row", "name", "attributes", "multiplier", "kind",
                                                            "description")} for row in rows]
        sp_params = dict(self.params.get("sp") or {})
        stats = (self.memory.sp_session or {}).get("stats") or self.memory.last_stats
        choice = wsp.choose_forget(rows, learning.get("multiplier"), self._primary() or "Vo",
                                   new_attributes=tuple(learning.get("attributes") or ()), stats=stats,
                                   margin=float(sp_params.get("forget_margin", wsp.FORGET_MARGIN)),
                                   offattr_weight=float(sp_params.get("offattr_weight", wsp.OFFATTR_WEIGHT)))
        if choice["row"] is None:
            learning.update(cancelled=True, why="forget_" + choice["reason"])
            return _click("forget_cancel", "sp_forget_" + choice["reason"])
        intent = f"forget_select_{choice['row']}"
        if not TARGETS[intent][1]:
            return _stop("sp_forget_requires_user", new=learning.get("name"), forget=choice)
        if learning.get("forget"):
            # ForgetSelect again after a 選択 tap: the confirm dialog never showed -> never tap another row
            return _stop("sp_forget_select_no_dialog", forget=dict(learning["forget"]))
        learning["forget"] = choice
        learning["forget_rows"] = [row.get("name") for row in rows]
        return _click(intent, "sp_forget_" + choice["reason"], choice=choice)

    def _decide_rest_confirm(self, screen: str, r: dict) -> NavDecision:
        if self.memory.pending == "rest":
            if self.memory.rest_sent is not None and self.settings is not None:
                # Recorded OFF, but the 確認画面 showed: the setting changed -> answer it, re-read on the next run.
                self.settings.invalidate("rest_confirm", "ON", "休む后出现了确认画面")
            self.memory.pending = self.memory.rest_key = self.memory.rest_sent = None
            return _click("dialog_ok", "planned_rest")
        return _click("dialog_cancel", "rest_not_planned")

    def _decide_live_confirm(self, screen: str, r: dict) -> NavDecision:
        return _click("dialog_ok", "wing_stage_live")

    def _decide_fes_birth(self, screen: str, r: dict) -> NavDecision:
        if not (self.memory.wing_won or self.memory.giveup == "held"):
            return _stop("fes_birth_without_evaluation")
        return _click("fes_birth", "produce_end")

    def _decide_fes_birth_confirm(self, screen: str, r: dict) -> NavDecision:
        if not (self.memory.wing_won or self.memory.giveup == "held"):
            return _stop("fes_birth_without_evaluation")
        return _click("fes_birth_ok", "produce_end")

    def _decide_halo_hub(self, screen: str, r: dict) -> NavDecision:
        """say "Halo" Hub. ``halo_route``: the route's week choice (halo_policy.hub_choice); ``halo_giveup``: open
        「諦める ホームに戻る」 once; neither: stop (not automated)."""
        if self.options.halo_route is not None:
            return self._halo_hub(r)
        if not self.options.halo_giveup:
            return _stop("halo_not_automated", screen=screen)
        if self.memory.giveup is not None:
            return _stop("giveup_open_no_effect", state=self.memory.giveup)
        self.memory.giveup = "opened"
        return _click("giveup_open", "halo_giveup")

    def _decide_abandon(self, screen: str, r: dict) -> NavDecision:
        """「ホームに戻る・諦める」: only the dialog this run opened for a Halo give-up is answered, by one long press of
        「諦める」 (wing_custom stages the hold); its 長押し hint and the button label must read on this frame."""
        if not (self.options.halo_giveup or self.options.halo_route == "dig") or self.memory.giveup != "opened":
            return _stop("abandon_dialog_not_automated")
        if r.get("abandon_hold_hint") is not True or r.get("abandon_button") is not True:
            return _stop("abandon_dialog_unread")
        self.memory.giveup = "held"
        return _click("giveup_hold", "halo_giveup")

    def _decide_fes_birth_skill(self, screen: str, r: dict) -> NavDecision:
        """「スキルを変更せずにフェスアイドルを誕生させますか」 at the end of Maa's own Halo run: フェスアイドル誕生 (no skill
        change, as the user did on H1); any other run stops."""
        if self.options.halo_route is None and not self.options.halo_giveup:
            return _stop("fes_birth_skill_dialog")
        if not (self.memory.wing_won or self.memory.giveup == "held"):
            return _stop("fes_birth_without_evaluation")
        return _click("fes_birth_ok", "halo_end_no_skill_change")

    # ---- say "Halo" (H2b, RunOptions.halo_route; agent/core/halo_policy.py) ------------------------------------------
    HALO_TAB_INTENTS = {"lesson": "halo_tab_lesson", "audition": "halo_tab_audition", "practice": "halo_tab_practice"}
    HALO_TAB_OF = {"HaloLesson": "lesson", "HaloAudition": "audition", "HaloPractice": "practice"}
    HALO_FIXED = {"HaloReport": "halo_report_next", "HaloSeasonEnd": "commu_ok", "HaloGoals": "halo_goals_close",
                  "HaloRecruitConfirm": "halo_recruit_ok",
                  "HaloTeamFull": "halo_team_full_close", "HaloInheritance": "halo_inheritance_next"}
    HALO_SURVEY_MAX = 45                # cards read on one carousel (S3 auditions: ~35)
    HALO_CAROUSEL_RETAPS = 1            # an arrow the game dropped (same card and page dot on two frames) is tapped
                                        # once more (H2d live 2026-11-06: card 10 of 13 stayed after ›)
    HALO_TAB_TAPS = 4                   # tab switches per week (practice -> audition -> lesson)
    HALO_ROOM_TAPS = 2                  # room taps per week before the room must read selected

    def _halo(self):
        m = self.memory
        if m.halo is None:
            goals = hp.Goals.from_dict(self.options.halo_goals) if self.options.halo_goals else hp.Goals()
            m.halo = hp.HaloMemory(route=self.options.halo_route, goals=goals, growth=True)   # 成長 since H2c batch 3
            if m.halo_facts:
                hp.load_facts(m.halo, m.halo_facts)       # a saved segment of this Produce (memory_file)
        if m.halo_state is None:
            m.halo_state = {"week": None, "hub_button": None, "live": None, "settle": 0}
        return m.halo, m.halo_state

    def _halo_settle(self, reason: str, limit: int | None = None, **detail) -> NavDecision:
        st = self.memory.halo_state
        st["settle"] += 1
        if st["settle"] > (limit or self.HALO_SETTLE_WAITS):
            return _stop("halo_reading_unsettled", wait=reason, **detail)
        return _wait(reason, **detail)

    def _halo_hub(self, r: dict) -> NavDecision:
        mem, st = self._halo()
        st["settle"], st["live"], st["week"], st["growth"] = 0, None, None, None
        st.pop("recruit_ok", None)
        st.pop("team_edit", None)
        st.pop("leave", None)
        st["hub_button"], st["season"] = r.get("hub_button"), r.get("season")
        choice = hp.hub_choice(r, mem)
        if choice == "give_up":
            if self.memory.giveup is not None:
                return _stop("giveup_open_no_effect", state=self.memory.giveup)
            self.memory.giveup = "opened"
            return _click("giveup_open", "halo_route_give_up", route=mem.route, completion=mem.completion,
                          facility=mem.facility_done, facility_plus=mem.facility_plus_done)
        if choice in ("produce", "live"):
            if r.get("hub_button") is None:
                return _stop("halo_hub_button_unread")
            if choice == "produce":
                self._halo_new_week(r, mem, st)
            return _click("halo_main", "halo_" + choice, button=r.get("hub_button"))
        if choice == "growth":
            key = hp.week_key(r)
            mem.growth_weeks.add(key)
            plan = hp.growth_plan(r, mem)
            st["growth"] = {"plan": plan, "stage": "stats", "phase": None, "taps": 0, "waits": 0, "expect": None}
            return _click("halo_growth_open", "halo_growth", week=list(key), plan=plan)
        if choice == "team_info":
            mem.team_info_seasons.add(r.get("season"))
            return _click("halo_team_info", "halo_team_info", season=r.get("season"))
        if choice == "rest":
            return _click("halo_rest", "halo_rest", stamina=r.get("stamina_pct"))
        return _stop("halo_hub_choice_unbound", choice=choice)

    HALO_RECRUIT_TAPS = 3               # candidate tiles tried on one 勧誘 page
    HALO_RECRUIT_WAITS = 6              # frames for the header to follow a tile tap / the page to leave after 勧誘

    def _halo_recruit(self, r: dict, st: dict) -> NavDecision:
        """勧誘 (H2d live 2026-11-05). The game keeps the first candidate selected. Not yet in the team -> チームに勧誘
        once. Already joined (header 勧誘済, the button greyed) -> tap the first tile without 勧誘済 (each tile once; the
        header name must change before anything else), then チームに勧誘. Unread fields stop."""
        rec = st.setdefault("recruit", {"tapped": [], "name": None, "waits": 0, "joined": False})
        recruited, name = r.get("recruited"), r.get("recruit_name")
        if rec["joined"]:
            rec["waits"] += 1
            if rec["waits"] > self.HALO_RECRUIT_WAITS:
                return _stop("halo_recruit_join_no_effect", name=name)
            return _wait("halo_recruit_join_pending")
        if rec["tapped"] and name == rec["name"]:
            rec["waits"] += 1
            if rec["waits"] > self.HALO_RECRUIT_WAITS:
                return _stop("halo_recruit_tile_no_effect", tile=rec["tapped"][-1], name=name)
            return _wait("halo_recruit_tile_pending", tile=rec["tapped"][-1])
        rec["waits"] = 0
        if recruited is False and name:
            rec["joined"] = True
            return _click("halo_recruit_join", "halo_recruit_candidate", name=name, tiles=list(rec["tapped"]))
        tiles = r.get("recruit_tiles")
        if recruited is not True or not isinstance(tiles, list) or len(tiles) != 5:
            return _stop("halo_recruit_unread", recruited=recruited, name=name)
        if len(rec["tapped"]) >= self.HALO_RECRUIT_TAPS:
            return _stop("halo_recruit_no_candidate", tried=list(rec["tapped"]), tiles=tiles)
        for n, joined in enumerate(tiles, 1):
            if not joined and n not in rec["tapped"]:
                rec["tapped"].append(n)
                rec["name"] = name
                return _click(f"halo_recruit_tile_{n}", "halo_recruit_pick", tile=n, selected=name)
        return _stop("halo_recruit_no_candidate", tried=list(rec["tapped"]), tiles=tiles)

    def _decide_halo(self, screen: str, r: dict) -> NavDecision:
        """say "Halo" in-run pages (H2b): only with ``halo_route``; 成長 stays manual."""
        if self.options.halo_route is None:
            return _stop("halo_not_automated", screen=screen)
        mem, st = self._halo()
        if screen == "HaloRecruitConfirm":
            st["recruit_ok"] = 0                      # the page after OK is the Hub (H2d); see below
        elif screen == "HaloRecruit" and "recruit_ok" in st:
            # live 2026-11-15: a fading 勧誘 frame right after 勧誘確認 OK read as the page again (the new member
            # 勧誘済) and a tile tap went to the next screen -> wait for it to leave instead of picking.
            st["recruit_ok"] += 1
            if st["recruit_ok"] > self.HALO_RECRUIT_WAITS:
                return _stop("halo_recruit_page_stayed")
            return _wait("halo_recruit_ok_pending")
        else:
            st.pop("recruit_ok", None)
        if screen != "HaloRecruit":
            st.pop("recruit", None)                   # each 勧誘 page starts fresh
        else:
            return self._halo_recruit(r, st)
        if screen in self.HALO_TAB_OF:
            return self._halo_schedule(screen, r, mem, st)
        if screen in ("HaloGrowth", "HaloGrowthConfirm"):
            return self._halo_growth(screen, r, st)
        if screen == "HaloLiveSelect":
            return self._halo_live_select(r, mem, st)
        if screen == "HaloLiveConfirm":
            if st["live"] != "challenged":
                return _stop("halo_live_confirm_not_opened")
            st["live"] = "confirmed"
            return _click("halo_live_challenge", "halo_live_confirm")
        if screen == "HaloTeamInfo":
            if not isinstance(r.get("completion"), int):
                return _stop("halo_completion_unread")
            mem.completion = r["completion"]
            if (mem.goals.completion is not None and not mem.disbanded and isinstance(st.get("season"), int)
                    and st["season"] >= 2):
                if st.get("team_edit"):
                    return _stop("halo_team_edit_no_effect")
                st["team_edit"] = True
                return _click("halo_team_edit", "halo_disband_open", completion=mem.completion)
            return _click("halo_back", "halo_completion_read", completion=mem.completion)
        if screen in ("HaloTeamEdit", "HaloLeaveConfirm"):
            return self._halo_disband(screen, r, mem, st)
        if screen in self.HALO_FIXED:
            return _click(self.HALO_FIXED[screen], "halo_page")
        return _stop("halo_not_automated", screen=screen)

    HALO_LEAVE_WAITS = 8                # frames a left member's 離脱させる may take to vanish

    def _halo_disband(self, screen: str, r: dict, mem, st: dict) -> NavDecision:
        """チーム編集 opened by Maa from チーム情報 (S2 on): 離脱させる on the leftmost member that has it, its チーム離脱確認
        「離脱させる」 once, until no member has the button (the P idol never does); then ←. A button that stays after
        its confirm (the count of buttons not dropping) stops."""
        if not st.get("team_edit"):
            return _stop("halo_team_edit_not_opened", screen=screen)
        leave = st.get("leave")
        if screen == "HaloLeaveConfirm":
            if not leave or leave.get("confirmed"):
                return _stop("halo_leave_confirm_unexpected")
            if r.get("leave_question") is not True:
                return _stop("halo_leave_confirm_unread")
            leave["confirmed"] = True
            return _click("halo_leave_ok", "halo_disband_confirm", slot=leave["slot"], name=r.get("leave_name"))
        buttons = r.get("leave_buttons")
        if not isinstance(buttons, list) or len(buttons) != 4:
            return _stop("halo_team_edit_unread")
        count = sum(1 for present in buttons if present)
        if leave:                       # the members after a leaver shift left: the button count must drop
            if count >= leave["count"] and leave["frames"] < self.HALO_LEAVE_WAITS:
                leave["frames"] += 1
                return _wait("halo_leave_pending", slot=leave["slot"])
            if count >= leave["count"]:
                return _stop("halo_leave_no_effect", slot=leave["slot"], confirmed=leave["confirmed"])
            st["leave"] = None
        slot = next((i + 1 for i, present in enumerate(buttons) if present), None)
        if slot is None:
            mem.disbanded = True
            return _click("halo_back", "halo_disbanded")
        st["leave"] = {"slot": slot, "count": count, "frames": 0, "confirmed": False}
        return _click(f"halo_leave_{slot}", "halo_disband_member", slot=slot)

    # ---- 成長 (H2c batch 3, agent/core/halo_growth.py) ----------------------------------------------------------------
    HALO_GROWTH_COLOURS = ("vo", "da", "vi")      # Vo/Da/Vi stats and caps; 集中力 unspent

    def _halo_growth(self, screen: str, r: dict, st: dict) -> NavDecision:
        """One 成長 visit Maa opened from the Hub: ステータス (per colour: + on 上限 when the points also fill the stat up to
        it, else >> on the stat) → 習得する → 成長確認 習得する; then その他 親愛度+10 when the route wants it → 習得; then ←.
        Every tap must show its effect (points down, tab switched, dialog) before the next; an unchanged page is
        re-read a few frames, then the run stops."""
        v = st.get("growth")
        if v is None:
            return _stop("halo_growth_not_opened", screen=screen)
        g = r.get("growth") or {}
        if screen == "HaloGrowthConfirm":
            if v["phase"] == "confirm":
                return self._growth_wait(v, "halo_growth_confirm_no_effect")     # the dialog is closing
            if v["phase"] != "learn":
                return _stop("halo_growth_confirm_unexpected")
            v["phase"] = "confirm"
            return self._growth_tap(v, "halo_growth_confirm", "halo_growth_confirm", None)
        if v["phase"] == "learn":
            return self._growth_wait(v, "halo_growth_learn_no_effect")
        if v["phase"] == "confirm":
            if g.get("learn") is not False:
                return self._growth_wait(v, "halo_growth_learned_unread", self.HALO_DECIDE_WAITS)
            v["phase"], v["expect"] = None, None
            v["stage"] = "affection" if v["stage"] == "stats" else "done"
        if v["expect"] is not None:
            if not self._growth_effect(v["expect"], g):
                return self._growth_wait(v, "halo_growth_tap_no_effect", expect=list(v["expect"]))
            v["expect"] = None
        points = dict(g.get("points") or {})
        # A colour's points only drop when Maa taps that colour: a single digit (live 2026-11-15 S3: Vi 「1」) that OCR
        # misses on a later frame keeps its earlier value until Maa taps that colour.
        known, tapped = v.setdefault("known", {}), v.setdefault("tapped", [])
        for c, value in points.items():
            if isinstance(value, int):
                known[c] = value
            elif c in known and c not in tapped:
                points[c] = known[c]
        g = {**g, "points": points}
        if v["stage"] == "stats":
            if g.get("tab") != "status" or any(not isinstance(points.get(c), int) or not (g.get("bottom") or {}).get(c)
                                               for c in self.HALO_GROWTH_COLOURS):
                return self._growth_wait(v, "halo_growth_status_unread", tab=g.get("tab"))
            for colour in self.HALO_GROWTH_COLOURS:
                step = hg.stat_step(colour, g)
                if step is not None:
                    kind = "cap" if step == "cap_plus" else "stat"
                    tapped.append(colour)
                    return self._growth_tap(v, f"halo_growth_{kind}_{colour}", f"halo_growth_{step}",
                                            ("points", colour, points[colour]), colour=colour, points=points[colour],
                                            stat=g["bottom"][colour])
            if g.get("learn") is True:
                v["phase"] = "learn"
                return self._growth_tap(v, "halo_growth_learn", "halo_growth_learn_stats", None)
            v["stage"] = "affection"
        if v["stage"] == "affection":
            wanted = v["plan"].get("affection", 0) > 0
            if not wanted or v.get("affection_learned"):
                v["stage"] = "done"
            elif g.get("tab") != "other":
                if v.get("tab_other"):
                    return self._growth_wait(v, "halo_growth_tab_unread", tab=g.get("tab"))
                v["tab_other"] = True
                return self._growth_tap(v, "halo_growth_tab_other", "halo_growth_tab_other", ("tab", "other", None))
            elif v.get("affection_sent"):
                if g.get("learn") is not True:
                    return self._growth_wait(v, "halo_growth_affection_unread")
                v["phase"], v["affection_learned"] = "learn", True
                return self._growth_tap(v, "halo_growth_learn", "halo_growth_learn_affection", None)
            elif g.get("affection_plus") is True and isinstance(points.get("unity"), int) \
                    and points["unity"] >= hg.AFFECTION_COST:
                v["affection_sent"] = True
                return self._growth_tap(v, "halo_growth_affection", "halo_growth_affection",
                                        ("points", "unity", points["unity"]), unity=points["unity"])
            else:
                v["stage"] = "done"     # bought already this week, or not enough 団結力
                self.log.append({"event": "halo_growth_affection_skipped", "plus": g.get("affection_plus"),
                                 "unity": points.get("unity")})
        if v.get("back"):
            return self._growth_wait(v, "halo_growth_back_no_effect")
        v["back"] = True
        return self._growth_tap(v, "halo_back", "halo_growth_done", None, taps=v["taps"])

    @staticmethod
    def _growth_effect(expect: tuple, g: Mapping) -> bool:
        kind, key, before = expect
        if kind == "tab":
            return g.get("tab") == key
        now = (g.get("points") or {}).get(key)
        return isinstance(now, int) and now < before

    def _growth_tap(self, v: dict, intent: str, reason: str, expect, **detail) -> NavDecision:
        v["taps"] += 1
        if v["taps"] > hg.MAX_TAPS:
            return _stop("halo_growth_tap_limit", taps=v["taps"])
        v["expect"], v["waits"] = expect, 0
        return _click(intent, reason, **detail)

    def _growth_wait(self, v: dict, reason: str, limit: int | None = None, **detail) -> NavDecision:
        v["waits"] += 1
        if v["waits"] > (self.HALO_SETTLE_WAITS if limit is None else limit):
            return _stop(reason, **detail)
        return _wait(reason.replace("_no_effect", "_pending").replace("_unread", "_settling"), **detail)

    @staticmethod
    def _halo_new_week(r: dict, mem, st) -> dict:
        """This week's plan, made on the Hub (the schedule header's week digit misreads: H1 「残り1週」 read as 7)."""
        st["week"] = {"season": r.get("season"), "order": list(hp.week_order(r, mem)), "tried": [], "tab_taps": 0,
                      "room_taps": 0, "survey": None, "decided": None}
        return st["week"]

    def _halo_schedule(self, screen: str, r: dict, mem, st) -> NavDecision:
        """スケジュール選択 (three tabs): try the week's tabs in the route's order; a tab whose cards offer nothing the route
        wants is left for the next one; レッスン&お仕事 always has a choice."""
        week = st["week"]
        if week is None:
            # attach on the schedule page: plan from its own season reading
            if r.get("season") is None:
                return self._halo_settle("halo_season_settling")
            week = self._halo_new_week(r, mem, st)
        if week["decided"] is not None:
            # 決定 was sent: the lesson / practice animation may still show the page for a while
            if st["settle"] >= self.HALO_DECIDE_WAITS:
                return _stop("halo_decide_no_effect", decided=dict(week["decided"]))
            return self._halo_settle("halo_decide_settling", limit=self.HALO_DECIDE_WAITS)
        want = next((tab for tab in week["order"] if tab not in week["tried"]), "lesson")
        tab = self.HALO_TAB_OF[screen]
        if tab != want:
            if week["tab_taps"] >= self.HALO_TAB_TAPS:
                return _stop("halo_tab_budget", want=want, shown=tab)
            week["tab_taps"] += 1
            week["survey"] = None
            return _click(self.HALO_TAB_INTENTS[want], "halo_week_tab", want=want, shown=tab)
        if tab == "lesson":
            return self._halo_lesson(r, mem, week)
        return self._halo_carousel(tab, r, mem, st, week)

    def _halo_lesson(self, r: dict, mem, week: dict) -> NavDecision:
        room = hp.lesson_choice(r, mem)
        if room is None:
            return self._halo_settle("halo_rooms_settling")
        self.memory.halo_state["settle"] = 0
        rooms = {x["id"]: x for x in r["rooms"]}
        if rooms[room].get("selected") is True:
            week["decided"] = {"tab": "lesson", "room": room}
            return _click("halo_decide", "halo_lesson", room=room, advice=list(rooms[room].get("advice") or ()),
                          have=r.get("advice"))
        if week["room_taps"] >= self.HALO_ROOM_TAPS:
            return _stop("halo_room_not_selected", room=room)
        week["room_taps"] += 1
        return _click(f"halo_room_{ROOM_SLOTS.index(room) + 1}", "halo_lesson_room", room=room)

    @staticmethod
    def _halo_card(tab: str, r: dict) -> dict | None:
        name = r.get("card_name")
        if not name:
            return None
        card = {"name": name, "rate": r.get("card_rate"), "condition": "出場条件" in name}
        if tab == "audition":
            card["fans"] = r.get("card_fans")
        else:
            card["note"] = r.get("card_note")
        return card

    @staticmethod
    def _halo_same_card(a: Mapping, b: Mapping) -> bool:
        """Same carousel card: equal 成功率 / fans / note and the same name up to OCR box order (H1: 「練習歌唱力強化」 /
        「歌唱力強化練習」) or a glyph or two (「SPOT LIGHTDEUE」 / 「…DUEL」). 歌唱力強化練習 and 表現力強化練習 differ (0.71)."""
        import difflib
        if any(a.get(k) != b.get(k) for k in ("rate", "fans", "note")):
            return False
        return difflib.SequenceMatcher(None, sorted(a["name"]), sorted(b["name"])).ratio() >= .85

    def _halo_carousel(self, tab: str, r: dict, mem, st, week: dict) -> NavDecision:
        """オーディション / チーム合同練習: the carousel is circular and longer than its dots in S3, so every card is read
        in turn (› until the first card shows again), then the chosen one is reached by the shorter way round and
        checked again before 決定. A reading must repeat on two frames (no mid-slide card) unless the frame after an
        arrow shows what it should (H2d 2026-11-06: ~10 s a card read twice, ~5 s once)."""
        if isinstance(r.get("fans"), int):
            mem.fans_seen = r["fans"]
        card = self._halo_card(tab, r)
        last, st["last_card"] = st.get("last_card"), card
        dots = r.get("dots") or {}
        dot = dots.get("selected")
        s = week["survey"]
        if not (last is None and self._halo_card_as_expected(s, tab, card, dot, dots.get("count"))) and (
                card is None or last is None or not self._halo_same_card(card, last)):
            return self._halo_settle("halo_card_settling", tab=tab)
        st["settle"] = 0
        if s is None or s["tab"] != tab:
            s = week["survey"] = {"tab": tab, "cards": [card], "pos": 0, "done": False, "target": None, "moved": False,
                                  "step": 1, "dot": dot, "dot0": dot, "retaps": 0}
        elif s["moved"]:
            s["moved"] = False
            n = len(s["cards"])
            before = s["cards"][-1] if not s["done"] else s["cards"][(s["pos"] - s["step"]) % n]
            if (self._halo_same_card(card, before) and (dot is None or dot == s["dot"])
                    and (n > 1 or (dots.get("count") or 0) > 1)):
                # The arrow was dropped: two frames after it still show the card (and page dot) it left.
                if s["retaps"] >= self.HALO_CAROUSEL_RETAPS:
                    return _stop("halo_carousel_no_effect", tab=tab, seen=n, dot=dot)
                s["retaps"] += 1
                return self._halo_carousel_tap(st, s, s["step"], "halo_carousel_retap", tab=tab, seen=n, dot=dot)
            moved_dot = dot is not None and s["dot"] is not None and dot != s["dot"]
            s["retaps"], s["dot"] = 0, dot
            if not s["done"]:
                # Two cards may read alike (S3: locked 出場条件 cards with the same fan bubble); a page dot that moved
                # tells them apart.
                if self._halo_same_card(card, s["cards"][-1]) and len(s["cards"]) > 1 and not moved_dot:
                    return _stop("halo_carousel_no_effect", tab=tab, seen=len(s["cards"]))
                if self._halo_same_card(card, s["cards"][0]) and (dot is None or s["dot0"] is None
                                                                   or dot == s["dot0"]):
                    s["done"], s["pos"] = True, 0
                else:
                    s["cards"].append(card)
                    s["pos"] = len(s["cards"]) - 1
            elif not self._halo_same_card(card, s["cards"][s["pos"]]):
                return _stop("halo_carousel_lost", tab=tab, expected=s["cards"][s["pos"]], read=card)
        if not s["done"]:
            if len(s["cards"]) >= self.HALO_SURVEY_MAX:
                return _stop("halo_carousel_too_long", tab=tab, seen=len(s["cards"]))
            return self._halo_carousel_tap(st, s, 1, "halo_survey", tab=tab, seen=len(s["cards"]))
        if s["target"] is None:
            cards = [{**c, "index": i} for i, c in enumerate(s["cards"])]
            pick = (hp.practice_choice(cards, r, mem) if tab == "practice"
                    else hp.audition_choice(cards, mem, week.get("season")))
            if pick is None:
                week["tried"].append(tab)
                week["survey"] = None
                want = next((t for t in week["order"] if t not in week["tried"]), "lesson")
                if week["tab_taps"] >= self.HALO_TAB_TAPS:
                    return _stop("halo_tab_budget", want=want, shown=tab)
                week["tab_taps"] += 1
                return _click(self.HALO_TAB_INTENTS[want], "halo_nothing_on_tab", tab=tab, want=want,
                              cards=len(cards))
            s["target"] = pick
        n, pos, target = len(s["cards"]), s["pos"], s["target"]
        if pos == target:
            chosen = s["cards"][target]
            week["decided"] = {"tab": tab, "card": dict(chosen)}
            if tab == "practice":
                self._halo_practice_done(mem, chosen["name"])
            elif tab == "audition":
                hp.note_audition(chosen, mem)
            return _click("halo_decide", "halo_" + tab, card=dict(chosen), cards=n)
        forward = (target - pos) % n
        step = 1 if forward <= n - forward else -1
        s["pos"] = (pos + step) % n
        return self._halo_carousel_tap(st, s, step, "halo_carousel_move", tab=tab, to=target, at=pos)

    def _halo_card_as_expected(self, s, tab: str, card, dot, count) -> bool:
        """The first frame after an arrow (taken >= 2 s after it: After guard + Loop) is taken without a second one when
        it shows what the tap should show: on the way to the pick, the known card; in the survey, a whole card other
        than the one left, one page dot on when both dots are read. Anything else is read again (a dropped arrow, a
        card mid-slide)."""
        if card is None or s is None or s["tab"] != tab or not s["moved"]:
            return False
        if s["done"]:
            return self._halo_same_card(card, s["cards"][s["pos"]])
        if not (isinstance(card.get("rate"), int) or card["condition"]) or self._halo_same_card(card, s["cards"][-1]):
            return False
        return dot is None or s["dot"] is None or not count or dot == (s["dot"] + s["step"]) % count

    @staticmethod
    def _halo_carousel_tap(st, s, step: int, reason: str, **detail) -> NavDecision:
        """› / ‹ once; the next frame is checked by ``_halo_card_as_expected`` (else read twice)."""
        s["moved"], s["step"], st["last_card"] = True, step, None
        return _click("halo_card_next" if step > 0 else "halo_card_prev", reason, **detail)

    @staticmethod
    def _halo_practice_done(mem, name: str) -> None:
        if re.search(r"施設", name):
            if re.search(r"[+＋十]", name):
                mem.facility_plus_done = True
            else:
                mem.facility_done = True
        elif re.search(r"総合", name):
            mem.sogo_done += 1

    def _halo_live_select(self, r: dict, mem, st) -> NavDecision:
        """目標ライブ挑戦確認: the route's live (halo_policy.live_choice); select it, then 挑戦する (once)."""
        if st["live"] is not None:
            return self._halo_settle("halo_live_challenge_settling", live=st["live"])
        pick = hp.live_choice(r.get("live_rates") or (), st.get("hub_button"), mem)
        selected = r.get("live_selected")
        if pick is None or selected is None:
            return self._halo_settle("halo_live_settling", rates=r.get("live_rates"), selected=selected)
        st["settle"] = 0
        if selected == pick:
            st["live"] = "challenged"
            return _click("halo_live_challenge", "halo_live", pick=pick, rates=r.get("live_rates"))
        if st.get("live_taps", 0) >= 2:
            return _stop("halo_live_not_selected", pick=pick, selected=selected)
        st["live_taps"] = st.get("live_taps", 0) + 1
        return _click(f"halo_live_card_{pick + 1}", "halo_live_select", pick=pick, selected=selected)

    def _decide_tutorial(self, screen: str, r: dict) -> NavDecision:
        page = r.get("tutorial_page")
        if not page:
            return _stop("tutorial_page_unread")
        current, total = page
        return _click("tutorial_next" if current < total else "popup_close", "tutorial_page")

    def _decide_setup(self, screen: str, r: dict) -> NavDecision:
        """Setup pages. ``attach`` (default): the user starts the Produce -> stop. ``start``: walk the pages with
        positive readings only (W.I.N.G. ミドル selected, the configured unit, both item slots empty)."""
        if self.options.mode != "start":
            if screen == "UnitSelect":
                # Read-only (review P1-3): the user's own ユニット編成 page still feeds the UI team snapshot.
                self._log_unit_cards(r, r.get("unit_index"), self._unit_fingerprint(r))
            return _stop("setup_requires_user", screen=screen, mode=self.options.mode)
        if self.memory.start_attempted:
            return _stop("setup_after_start_attempt", screen=screen)
        back = self._edit_only_back(screen)
        if back is not None:
            return back
        if screen == "UnitSelect":
            return self._decide_unit(r)
        if screen == "ItemSelect":
            # P items are never brought (AGENTS: no generic inventory use); both slots must read empty.
            if self.params.get("items"):
                return _stop("p_items_not_supported", items=list(self.params["items"]))
            if r.get("items_empty") is not True:
                return _stop("item_slots_not_empty_or_unread", items_empty=r.get("items_empty"))
            return _click("item_decide", "start_mode_no_items")
        if screen == "ProduceSelect" and self.options.halo_route is not None:
            # say "Halo" sits under 特化プロデュース (H2c batch 2): the W.I.N.G. page only switches tabs.
            if r.get("special_tab") is not True:
                return _stop("produce_tab_unread", screen=screen, tab="special")
            return self._halo_tap("tab", "halo_tab_special", "halo_select_special_tab")
        if screen == "ProduceSelect":
            if r.get("cheer") is not None:
                self.memory.cheer = bool(r["cheer"])
            # U01: the configured difficulty, never a silent substitute. One tab tap per run; the next
            # frame must read the chosen tab or the run stops.
            want, current = self.memory.difficulty, r.get("difficulty")
            if current in DIFFICULTY_CHOICES and current != want:
                if getattr(self, "_difficulty_taps", 0) >= 1:
                    return _stop("difficulty_not_selected", difficulty=current, wanted=want)
                self._difficulty_taps = getattr(self, "_difficulty_taps", 0) + 1
                return _click(f"produce_difficulty_{want}", f"select_wing_{want}", difficulty=current)
            if current != want:
                return _stop("difficulty_unread", difficulty=current, wanted=want)
            return _click("produce_next", f"start_mode_wing_{want}", cheer=self.memory.cheer)
        return _stop("setup_requires_user", screen=screen)

    UNIT_BACK_WAITS = 3

    def _edit_only_back(self, screen: str) -> NavDecision | None:
        """``edit_only`` after its one ← (Auto 自动编队): プロデュース選択 is the end (``team_edit_done``); the unit page
        again waits a few frames for the transition, then stops. None = no ← sent yet (the normal setup rules)."""
        state = self.memory.team_edit
        if not self.options.edit_only or not state or not state.get("back"):
            return None
        if screen in ("ProduceSelect", "ProduceSelectOther"):
            return _stop("team_edit_done", unit=state.get("unit"), edits=dict(state.get("done") or {}), screen=screen)
        if screen == "UnitSelect":
            state["back_waits"] = state.get("back_waits", 0) + 1
            if state["back_waits"] > self.UNIT_BACK_WAITS:
                return _stop("unit_back_no_effect", unit=state.get("unit"))
            return _wait("unit_back_pending")
        return _stop("unit_back_unexpected_screen", screen=screen)

    def _decide_setup_tab(self, screen: str, r: dict) -> NavDecision:
        """プロデュース選択 on another tab (特化プロデュース, third demo #209): start mode taps 育成プロデュース once."""
        if self.options.mode != "start" or self.memory.start_attempted:
            return _stop("setup_requires_user", screen=screen, mode=self.options.mode)
        back = self._edit_only_back(screen)
        if back is not None:
            return back
        if self.options.halo_route is not None:
            return self._decide_halo_scenario(screen, r)
        if r.get("train_tab") is not True:
            return _stop("produce_tab_unread", screen=screen)
        # 2026-10-12 live: one tap only; still not the W.I.N.G. ProduceSelect afterwards (another 育成 scenario shown,
        # e.g. the carousel left on a different scenario) -> stop, never loop on the tab.
        taps = getattr(self, "_train_tab_taps", 0)
        if taps >= 1:
            return _stop("produce_scenario_not_wing", screen=screen)
        self._train_tab_taps = taps + 1
        return _click("produce_tab_train", "select_train_tab")

    # ---- say "Halo" start (H2c batch 2; RunOptions.halo_route + mode start) -----------------
    HALO_SETUP_WAITS = 6                # re-reads of a setup page after a same-page tap (carousel, tab, dialog)
    HALO_BOOK_WAITS = 40                # re-reads of the book page while the chosen book loads in
    # step -> the stop once a tapped page has not changed after the bounded re-reads (outcomes.js has text for each)
    HALO_TAP_STOPS = {"tab": "halo_tab_no_effect", "down": "halo_down_no_effect", "next": "halo_next_no_effect",
                      "book": "halo_book_no_effect", "history": "halo_history_no_effect",
                      "history_call": "halo_history_call_no_effect", "preset": "halo_preset_no_effect",
                      "preset_set": "halo_preset_set_no_effect", "book_decide": "halo_book_decide_no_effect",
                      "slot": "halo_book_slot_no_effect", "picker_decide": "halo_picker_decide_no_effect"}
    HALO_PICKER_TAPS = 6                # picker tile taps per start (one book; a few 評価値 ties)
    HALO_PICKER_WAITS = 6               # frames a tapped tile's ✓ / the detail panel may take

    def _halo_tap(self, step: str, intent: str, reason: str, limit: int | None = None, **detail) -> NavDecision:
        """One tap per say "Halo" setup step. A step already tapped means the page has not changed yet: re-read a
        bounded number of frames, then stop (never a second tap)."""
        s = self.memory.halo_setup
        if s.get(step):
            return self._halo_wait(self.HALO_TAP_STOPS[step], limit, **detail)
        s[step], s["waits"], s["loaded"] = True, 0, False
        return _click(intent, reason, **detail)

    def _halo_wait(self, reason: str, limit: int | None = None, **detail) -> NavDecision:
        s = self.memory.halo_setup
        s["waits"] = s.get("waits", 0) + 1
        if s["waits"] > (self.HALO_SETUP_WAITS if limit is None else limit):
            return _stop(reason, **detail)
        return _wait(reason.replace("_no_effect", "_pending"), **detail)

    def _decide_halo_scenario(self, screen: str, r: dict) -> NavDecision:
        """プロデュース選択 of a say "Halo" start: 特化プロデュース tab, the card below S.T.E.P. (the 特化 list holds
        S.T.E.P. and say "Halo"), then 次へ on a frame whose caption reads say "Halo"編."""
        caption = r.get("caption")
        if caption == "halo":
            if r.get("next_label") is not True:
                return _stop("produce_next_unread", screen=screen)
            return self._halo_tap("next", "halo_scenario_next", "halo_scenario_selected")
        if caption == "step":
            return self._halo_tap("down", "halo_scenario_down", "halo_scenario_down", caption=caption)
        if caption in ("wing", "fans", "grad", "lp"):
            if r.get("special_tab") is not True:
                return _stop("produce_tab_unread", screen=screen, tab="special")
            return self._halo_tap("tab", "halo_tab_special", "halo_select_special_tab", caption=caption)
        return self._halo_wait("produce_caption_unread", screen=screen)

    BOOK_SOURCE_STEPS = {"auto": ("book", "halo_book_auto", "auto"), "history": ("history", "halo_book_history", "history"),
                         "preset1": ("preset", "halo_book_preset", "preset"),
                         "preset2": ("preset", "halo_book_preset", "preset")}
    BOOK_APPLIED = {"auto": "book", "history": "history_call", "preset1": "preset_set", "preset2": "preset_set",
                    "target": "picker_decide"}

    def _decide_book(self, screen: str, r: dict) -> NavDecision:
        """ノウハウブック選択 (say "Halo": one book): the game's おまかせ編成 by default, else the newest
        設定履歴 set or in-game プリセット1/2 (``halo_book``). 決定 only on a frame that reads slot 1 filled after that
        source was applied; a page that stays unchanged stops."""
        o, s = self.options, self.memory.halo_setup
        if o.mode != "start" or o.halo_route is None:
            return _stop("setup_requires_user", screen=screen, mode=o.mode)
        if self.memory.start_attempted:
            return _stop("setup_after_start_attempt", screen=screen)
        source = o.halo_book
        if screen == "BookPicker":
            return self._decide_book_picker(screen, r)
        if screen == "BookHistory":
            if source != "history" or not s.get("history"):
                return _stop("book_page_unexpected", screen=screen, source=source)
            history = r.get("history") or {}
            if not history.get("date") or history.get("call") is not True:
                return self._halo_wait("book_history_unread")
            return self._halo_tap("history_call", "halo_history_call", "halo_book_history_call", date=history["date"])
        if screen == "BookPreset":
            slot = {"preset1": 1, "preset2": 2}.get(source)
            if slot is None or not s.get("preset"):
                return _stop("book_page_unexpected", screen=screen, source=source)
            row = (list(r.get("presets") or ()) + [{}, {}])[slot - 1]
            if row.get("label") is not True or row.get("set") is not True:
                return self._halo_wait("book_preset_unread", preset=slot)
            return self._halo_tap("preset_set", f"halo_preset_set_{slot}", "halo_book_preset_set", preset=slot)
        book = r.get("book") or {}
        if book.get("layout") != "halo":
            return self._halo_wait("book_layout_unread", layout=book.get("layout"))
        buttons = book.get("buttons") or {}
        if source == "target":
            return self._book_target(book, buttons, s)
        if s.get(self.BOOK_APPLIED[source]):
            # おまかせ編成 over a filled slot: only a Now Loading frame since the tap proves the new pick is shown.
            fresh = source != "auto" or s.get("loaded") or s.get("before") is None
            if book.get("slot1") != "filled" or not fresh:
                return self._halo_wait("book_not_filled", self.HALO_BOOK_WAITS, source=source)
            if buttons.get("decide") is not True:
                return _stop("book_decide_unread")
            return self._halo_tap("book_decide", "halo_book_decide", "halo_book_decided", self.HALO_BOOK_WAITS,
                                  source=source, stats=book.get("stats"))
        step, intent, button = self.BOOK_SOURCE_STEPS[source]
        if buttons.get(button) is not True:
            return _stop("book_button_unread", button=button)
        if not s.get(step):
            s["before"] = book.get("stats")
        return self._halo_tap(step, intent, f"halo_book_{source}", self.HALO_BOOK_WAITS, slot1=book.get("slot1"))

    def _book_target(self, book: dict, buttons: dict, s: dict) -> NavDecision:
        """Book page with 带书「ノウハウ页指定」: open the empty slot 1 (「+」) once; back from the picker,
        決定 only when slot 1 shows the planned book's Vo/Da/Vi/Me. A slot filled before Maa opened it stops."""
        plan = self.options.halo_book_plan
        if s.get("picker_decide"):
            want = {k: plan[k] for k in ("vo", "da", "vi", "me")}
            if book.get("slot1") != "filled":
                return self._halo_wait("book_not_filled", self.HALO_BOOK_WAITS, source="target")
            if book.get("stats") != want:
                return _stop("target_book_mismatch", shown=book.get("stats"), plan=want)
            if buttons.get("decide") is not True:
                return _stop("book_decide_unread")
            return self._halo_tap("book_decide", "halo_book_decide", "halo_book_decided", self.HALO_BOOK_WAITS,
                                  source="target", stats=book.get("stats"), title=plan["title"])
        if book.get("slot1") == "filled" and not s.get("slot"):
            return _stop("book_slot_prefilled", stats=book.get("stats"))
        if book.get("slot1") is None:
            return self._halo_wait("book_slot_unread")
        return self._halo_tap("slot", "halo_book_slot1", "halo_book_target_open", self.HALO_BOOK_WAITS,
                              title=plan["title"])

    def _decide_book_picker(self, screen: str, r: dict) -> NavDecision:
        """Book picker opened by ``_book_target``: the first screen sorted by ノウハウ評価値 (descending), the planned
        book among the tiles with its 評価値; tap one, check the detail panel (title + Vo/Da/Vi/Me, weekly_auto
        .book_matches), take a wrong same-score book off again, then 決定. Unread / wrong sort / blocked or absent book /
        an unexpected ✓ stop; every tap is bounded."""
        from agent.core import weekly_auto as wa
        o, s = self.options, self.memory.halo_setup
        if o.mode != "start" or o.halo_book != "target" or not s.get("slot") or self.memory.start_attempted:
            return _stop("book_picker_not_automated", source=o.halo_book)
        plan = o.halo_book_plan
        p = s.setdefault("picker", {"pending": None, "frames": 0, "taps": 0, "done": None, "not": []})
        if s.get("picker_decide"):
            return self._halo_wait("halo_picker_decide_no_effect", self.HALO_BOOK_WAITS)
        view = r.get("picker") or {}
        tiles = {(t["r"], t["c"]): t for t in view.get("tiles") or ()}
        if (not view.get("sort") or not view.get("order") or len(tiles) != 15
                or any(t.get("eval") is None or t.get("grey") is None or t.get("selected") is None
                       for t in tiles.values())):
            return self._halo_wait("picker_unread", self.HALO_BOOK_WAITS)
        if "評価値" not in view["sort"] or "降順" not in view["order"]:
            return _stop("picker_sort", sort=view["sort"], order=view["order"])
        if p["pending"] is not None:
            (pr, pc), select = tuple(p["pending"]["tile"]), p["pending"]["select"]
            if tiles[(pr, pc)]["selected"] != select:
                p["frames"] += 1
                if p["frames"] > self.HALO_PICKER_WAITS:
                    return _stop("picker_tile_no_effect", tile=[pr, pc])
                return _wait("picker_tile_pending", tile=[pr, pc])
            if select:
                detail = r.get("picker_detail") or {}
                if not wa.book_matches(plan, detail.get("title"), detail.get("stats") or {}):
                    if p["frames"] < self.HALO_PICKER_WAITS:      # the panel may follow the ✓ a frame later
                        p["frames"] += 1
                        return _wait("picker_detail_pending", tile=[pr, pc])
                    p["not"].append([pr, pc])
                    p["pending"] = None
                    self.log.append({"event": "halo_picker_other_book", "tile": [pr, pc], "detail": detail})
                    return self._picker_tap(p, pr, pc, False)
                p["done"] = [pr, pc]
                self.log.append({"event": "halo_picker_book", "tile": [pr, pc], "title": detail.get("title")})
            p["pending"], p["frames"] = None, 0
        selected = sorted([list(k) for k, t in tiles.items() if t["selected"]])
        if selected != ([p["done"]] if p["done"] else []):
            return _stop("picker_selection_unexpected", selected=selected)
        if p["done"]:
            return self._halo_tap("picker_decide", "halo_picker_decide", "halo_picker_decided", self.HALO_BOOK_WAITS,
                                  tile=p["done"])
        same = [t for t in tiles.values() if t["eval"] == plan["eval"] and [t["r"], t["c"]] not in p["not"]]
        free = [t for t in same if not t["grey"]]
        if not free:
            return _stop("target_book_blocked" if same else "target_book_not_visible", eval=plan["eval"])
        return self._picker_tap(p, free[0]["r"], free[0]["c"], True)

    def _picker_tap(self, p: dict, row: int, col: int, select: bool) -> NavDecision:
        if p["taps"] >= self.HALO_PICKER_TAPS:
            return _stop("picker_tap_budget", taps=p["taps"])
        p["taps"] += 1
        p["pending"], p["frames"] = {"tile": [row, col], "select": select}, 0
        return _click(f"halo_picker_tile_{row}_{col}", "halo_picker_pick" if select else "halo_picker_unpick",
                      tile=[row, col])

    # ---- home -> プロデュース / 大富豪 (batch c) ---------------------------------------
    def _decide_home(self, screen: str, r: dict) -> NavDecision:
        """Home (and its Pデスク/ミニゲーム popup): only ``start`` + ``from_home`` before the Produce start. With
        ``minigame="before_start"`` one 大富豪 game first; then プロデュース. Every tap needs its label read inside its
        rect on this frame; after the start (or without the options) the home screen stops as before."""
        o, m = self.options, self.memory
        if screen == "Home" and r.get("home_produce_resume_label") is True:
            # A suspended Produce (after a reload the user used to tap プロデュース中 by hand, 2026-10-10): open the
            # プロデュース再開 dialog once; the dialog decision compares it with the config.
            if not o.from_home:
                return _stop("outside_produce", screen=screen, produce_in_progress=True)
            if m.minigame.get("home_resume"):
                return _stop("home_resume_no_effect")
            m.minigame["home_resume"] = True
            return _click("home_produce_resume", "home_to_produce_resume")
        if o.mode != "start" and o.from_home and r.get("home_produce_label") is True:
            return _stop("no_produce_in_progress", screen=screen)
        if o.mode != "start" or not o.from_home or m.start_attempted:
            return _stop("outside_produce", screen=screen)
        want_game = o.minigame == "before_start" and not m.minigame.get("done")
        if screen == "HomeMinigamePopup":
            if not want_game:
                return _stop("minigame_popup_unexpected")
            if r.get("popup_minigame") is not True:
                return _stop("home_label_unread", label="ミニゲーム")
            m.minigame.setdefault("phase", "entering")
            return _click("home_minigame", "minigame_before_start")
        if want_game:
            if m.minigame.get("phase"):
                return _stop("minigame_left_unfinished", state=dict(m.minigame))
            if r.get("home_minigame_label") is not True:
                return _stop("home_label_unread", label="Pデスク・ミニゲーム")
            return _click("home_minigame_menu", "open_minigame_popup")
        if m.minigame.get("home_produce"):
            return _stop("home_produce_no_effect")
        if r.get("home_produce_label") is not True:
            return _stop("home_label_unread", label="プロデュース")
        m.minigame["home_produce"] = True
        return _click("home_produce", "home_to_produce_select")

    def _mission_popup_is_login(self, r: Mapping[str, Any]) -> bool:
        """The home ミッション達成 popup is NOT a Produce end when the home button under it still reads 「プロデュース中」,
        in start mode before this run started or adopted a Produce, or (attach + from_home, 「プロデュース」 not read) when this run has not seen any end-of-Produce screen."""
        o, m = self.options, self.memory
        if r.get("home_produce_resume_label") is True:
            return True
        if (o.mode == "start" and not m.start_attempted and m.produce is None and m.end is None
                and not m.true_end_seen and not m.wing_won):
            return True              # H2d 2026-11-07: a popup over the home before this run started its Produce
        if r.get("home_produce_label") is True:
            return False
        return (o.mode == "attach" and o.from_home and m.end is None and not m.true_end_seen
                and not m.wing_won)

    def _decide_mission_popup(self, screen: str, r: dict) -> NavDecision:
        """Login-mission popup over a home with a Produce in progress (live seg15): tap OK once (never ミッションへ)
        and continue on the home; otherwise the Produce has finished (the old behaviour)."""
        m = self.memory
        if not self._mission_popup_is_login(r):
            return NavDecision("finish", reason=RULES[screen].reason, detail={"true_end_seen": m.true_end_seen})
        if m.minigame.get("mission_popup_ok"):
            return _stop("mission_popup_no_effect")
        m.minigame["mission_popup_ok"] = True
        return _click("mission_popup_ok", "mission_popup_login_ok", mission=r.get("mission_text"))

    def _decide_resume(self, screen: str, r: dict) -> NavDecision:
        """プロデュース再開 (wing_resume): read card / scenario / season / stats, compare with the config, then 再開
        (match, or the user's ``resume_mismatch_ok``) or stop with a warning block (继续 / 中断). Never 終了; one tap."""
        from agent.core import wing_resume
        o, m = self.options, self.memory
        d = r.get("resume_dialog") or {}
        if not d.get("ok_label") or d.get("end_in_target") or not d.get("end_label"):
            return _stop("resume_dialog_unreadable", dialog={k: d.get(k) for k in ("ok_label", "end_label",
                                                                                   "end_in_target")})
        if isinstance(m.resume_info, Mapping) and m.resume_info.get("tapped"):
            return _stop("resume_no_effect")
        info = wing_resume.compare(d, p_idol=m.p_idol, p_title=m.p_title, difficulty=m.difficulty, goal=m.goal,
                                   scenario="halo" if o.halo_route else "wing")
        info.update(screen=screen, source="dialog", mode=o.mode)
        if o.verify_resume:
            # 2026-10-14 (user): the dialog's P card portrait must be the configured P card (local card icons, the
            # strict wing_unit_check slot check). A different card -> mismatch; unread / low confidence -> unverified.
            from agent.core import wing_unit_check as wuc
            portrait = wuc.slot_verdict(d.get("portrait"), m.team_slots.get("P"))
            info["checks"] = {**info["checks"], "portrait": {"match": True, "mismatch": False}.get(portrait)}
            info["seen_portrait"] = (d.get("portrait") or {}).get("card_id") if isinstance(d.get("portrait"), Mapping) else None
            if portrait == "mismatch":
                info["verdict"] = "mismatch"
            elif portrait != "match" and info["verdict"] == "match":
                info["verdict"] = "unverified"
        m.resume_info = info
        self.log.append({"event": "resume_dialog", **info})
        choices = ["继续（resume_mismatch_ok: true）", "中断（用户在游戏中处理；Maa 永不点 終了）"]
        if d.get("idol") is None or d.get("scenario") is None:
            info["choices"] = choices
            return _stop("resume_dialog_unreadable", resume=info)
        if info["verdict"] == "mismatch" and not o.resume_mismatch_ok:
            info["choices"] = choices
            return _stop("resume_config_mismatch", resume=info)
        if info["verdict"] == "unverified" and (o.mode == "start" or o.verify_resume) and not o.resume_mismatch_ok:
            info["choices"] = choices
            return _stop("resume_identity_unverified", resume=info)
        if info["verdict"] != "match":
            info["user_choice"] = "continue" if o.resume_mismatch_ok else "attach_unverified"
        if isinstance(d.get("stats"), Mapping):
            m.last_stats = {k: d["stats"][k] for k in ("Vo", "Da", "Vi", "Me") if k in d["stats"]}
        info["tapped"] = True
        if info["verdict"] == "match":
            diff = info.get("difficulty") if isinstance(info.get("difficulty"), str) else None
            self.adopt_identity("attach", difficulty=diff)
        return _click("resume_ok", "resume_produce", verdict=info["verdict"])

    def _minigame_allowed(self) -> bool:
        o = self.options
        return (o.mode == "start" and o.minigame == "before_start" and not self.memory.start_attempted
                and not self.memory.minigame.get("done"))

    def _decide_minigame_top(self, screen: str, r: dict) -> NavDecision:
        if not self._minigame_allowed():
            return _stop("minigame_not_automated")
        if self.memory.minigame.get("course_sent"):
            return _stop("minigame_course_left_without_game")
        return _click("minigame_daifugo", "minigame_open_daifugo")

    def _decide_minigame_course(self, screen: str, r: dict) -> NavDecision:
        """コース選択: the first course (マメ丸, 「初心者はここから」) only, when the carousel shows it at the start."""
        if not self._minigame_allowed():
            return _stop("minigame_not_automated")
        if r.get("course_first") is not True:
            return _stop("minigame_course_not_at_start")
        if self.memory.minigame.get("course_sent"):
            return _stop("minigame_course_repeat")
        self.memory.minigame["course_sent"] = True
        return _click("minigame_course_1", "minigame_course_beginner")

    def _decide_minigame_rules(self, screen: str, r: dict) -> NavDecision:
        """大富豪について (first visit, 4 pages): page through and close, like the Produce tutorials."""
        if not self._minigame_allowed():
            return _stop("minigame_not_automated")
        page = r.get("rules_page")
        if not page:
            return _stop("minigame_rules_page_unread")
        current, total = page
        return _click("tutorial_next" if current < total else "popup_close", "minigame_rules_page", page=list(page))

    def _decide_minigame_play(self, screen: str, r: dict) -> NavDecision:
        """In a game: pass on every own turn (the user's own game ended 大貧民 with 13 cards and still produced the
        cheer, second demo #131/#161). A turn bar whose パス is not the enabled blue (e.g. a forced lead) stops."""
        g = self.memory.minigame
        if not self._minigame_allowed() or not g.get("course_sent"):
            return _stop("minigame_not_automated")
        if r.get("turn_bar") is not True:
            return _wait("minigame_others_playing")
        if r.get("pass_enabled") is not True:
            return _stop("minigame_lead_requires_user", passes=g.get("passes", 0))
        if g.get("passes", 0) >= MINIGAME_MAX_PASSES:
            return _stop("minigame_too_long", passes=g.get("passes", 0))
        g["passes"] = g.get("passes", 0) + 1
        return _click("minigame_pass", "minigame_pass", passes=g["passes"])

    def _decide_minigame_game_end(self, screen: str, r: dict) -> NavDecision:
        """「ゲーム終了 結果」 ranking page (live 2026-10-15): TOUCH NEXT once; the guard read it inside the target.
        Like プレイ結果 it only leaves a finished game, so a run started on it (minigame on) continues too."""
        g = self.memory.minigame
        if not self._minigame_allowed():
            return _stop("minigame_not_automated")
        if g.get("game_end_sent"):
            # the page can stay a moment after the tap; never tap twice, stop if it does not move on
            g["game_end_waits"] = g.get("game_end_waits", 0) + 1
            return (_wait("minigame_game_end_pending") if g["game_end_waits"] <= 5
                    else _stop("minigame_game_end_no_effect"))
        g["game_end_sent"] = True
        return _click("minigame_touch_next", "minigame_game_end", passes=g.get("passes", 0))

    def _decide_minigame_result(self, screen: str, r: dict) -> NavDecision:
        """プレイ結果: 次へ on the rank page, then プロデュースへ (the button's read position picks its layout). A finished
        game's pages need no course_sent: a run restarted on them (minigame on) only leaves the game."""
        g = self.memory.minigame
        if not self._minigame_allowed():
            return _stop("minigame_not_automated")
        for intent in ("minigame_result_next", "minigame_to_produce", "minigame_to_produce_low"):
            label = "next_box" if intent == "minigame_result_next" else "produce_box"
            if wsp.box_inside(r.get(label), TARGETS[intent][0]):
                if intent != "minigame_result_next" and not g.get("produce_settled"):
                    # live 2026-10-15: プロデュースへ tapped 0.4 s after the panel appeared did nothing; wait one frame
                    g["produce_settled"] = True
                    return _wait("minigame_result_settle")
                if intent != "minigame_result_next":
                    g.update(done=True, cheer_notice=bool(r.get("cheer_notice")))
                    if r.get("cheer_notice"):
                        self.memory.cheer = True
                return _click(intent, "minigame_result", passes=g.get("passes", 0))
        return _stop("minigame_result_button_unread", next_box=r.get("next_box"), produce_box=r.get("produce_box"))

    def _decide_start_confirm(self, screen: str, r: dict) -> NavDecision:
        """使用確認 (stamina 30): only the item-less layout at 1倍 with a 30-point stamina drop, once per run."""
        o = self.options
        if o.mode != "start" or not o.allow_start_spend:
            return _stop("spend_not_enabled", mode=o.mode)
        if self.memory.start_attempted:
            return _stop("start_already_attempted")
        confirm = r.get("start_confirm") or {}
        if confirm.get("layout") != "plain":
            return _stop("start_confirm_layout_not_plain", layout=confirm.get("layout"))
        before, after = confirm.get("stamina_before"), confirm.get("stamina_after")
        if confirm.get("multiplier") != 1:
            return _stop("start_multiplier_not_1", multiplier=confirm.get("multiplier"))
        if not (isinstance(before, int) and isinstance(after, int) and before - after == 30 and after >= 0):
            return _stop("start_stamina_unread_or_unexpected", before=before, after=after)
        return _click("start_confirm_ok", "start_produce", stamina=[before, after])

    def _decide_unit(self, r: dict) -> NavDecision:
        """ユニット編成: reach the configured unit (20 units, 2 tab pages), then verify the team and stop.

        The team is never edited here. The configured team must not repeat an idol (P, S1–S4 and guest; user rule
        2026-10-05). On-screen card identity needs avatar templates (not captured), so a correct unit still stops
        with ``team_fingerprint_unverified`` until the fingerprint reader exists."""
        m = self.memory
        target = m.target_unit
        if target is None and self.options.unit_search and m.team_slots and m.unit_search is None:
            m.unit_search = {"checked": {}, "taps": 0}
        if m.unit_search is None and (not isinstance(target, int) or not 1 <= target <= UNIT_COUNT):
            return _stop("target_unit_invalid", target=target)
        conflicts = self.team_conflicts(m.team)
        if conflicts:
            return _stop("team_duplicate_idol", idols=conflicts)
        current, total = r.get("unit_index"), r.get("unit_total")
        if current is None:
            # 2026-10-09 live: the slide-in transition can hide the counter; re-read a settled frame, never page.
            return _wait("unit_counter_unread")
        if total is not None and total != UNIT_COUNT:
            return _stop("unit_count_unexpected", total=total)
        search = m.unit_search
        if search is not None:
            return self._unit_search_step(r, current)
        if current == target:
            check = self._unit_fingerprint(r)
            verdict = check.get("verdict")
            if self.options.team_edit and self.options.mode == "start":
                edit = self._team_edit_step(r, current, check)
                if edit is not None:
                    return edit
            self._log_unit_cards(r, current, check)
            if verdict == "match" and self.options.edit_only:
                # Auto 自动编队: never 次へ; ← once to プロデュース選択 (Auto starts there), then team_edit_done.
                state = self.memory.team_edit
                if state is None:
                    return _stop("team_edit_state_missing", unit=current)
                state.update(back=True, unit=current)
                return _click("unit_back", "team_edit_done_back", unit=current, check=check)
            if verdict == "match" and self.options.mode == "start":
                return _click("unit_next", "unit_fingerprint_match", unit=current, check=check)
            if verdict == "mismatch" and self.options.unit_search:
                m.unit_search = {"checked": {str(current): verdict}, "taps": 0}
                return self._unit_search_step(r, current)
            if verdict == "mismatch":
                return _stop("team_fingerprint_mismatch", unit=current, check=check)
            # Fingerprint unreadable / no team configured: only the user's explicit confirmation of this unit's team
            # lets a start-mode run continue.
            if self.options.mode == "start" and self.options.team_confirmed:
                return _click("unit_next", "unit_confirmed_by_user", unit=current, check=check)
            return _stop("team_fingerprint_unverified", unit=current, check=check)
        page = r.get("unit_page")
        if page is None:
            return _stop("unit_page_unread", unit=current)
        want = (target - 1) // UNITS_PER_PAGE
        if page < want:
            return _click("unit_page_next", "unit_page", page=page, want=want)
        if page > want:
            return _click("unit_page_prev", "unit_page", page=page, want=want)
        return _click(f"unit_tab_{(target - 1) % UNITS_PER_PAGE + 1}", "select_unit", current=current, target=target)

    # ---- team edit (RunOptions.team_edit, agent/core/wing_team_edit.py) -----------
    TEAM_EDIT_BLANK_WAITS = 8
    TEAM_EDIT_VACATED_READS = 2

    def _decide_team_confirm(self, screen: str, r: dict) -> NavDecision:
        """同一偶像確認 after a team_edit tile tap: OK once for the slot just picked. The configured team has no idol
        twice (team_rule_problems), so the idol the game moves out sits in a slot that still has to change; it is
        filled afterwards (next_slot fill_blank). Anything else stops (never confirm outside team_edit)."""
        o, state = self.options, self.memory.team_edit
        if not (o.team_edit and o.mode == "start") or not state or state.get("pending") is None:
            return _stop("team_edit_not_automated")
        slot = state["pending"]
        if state.get("picked") != slot:
            return _stop("team_edit_confirm_unexpected", slot=slot)
        confirmed = state.setdefault("confirmed", [])
        if slot in confirmed:
            return _stop("team_edit_confirm_repeat", slot=slot)
        confirmed.append(slot)
        state["vacated"], state["blank"] = True, 0
        self.log.append({"event": "team_edit_same_idol_ok", "slot": slot, "card": state.get("card")})
        return _click("picker_confirm_ok", "team_edit_same_idol_ok", slot=slot, card=state.get("card"))

    def _team_edit_step(self, r: Mapping[str, Any], unit, check: Mapping[str, Any]) -> NavDecision | None:
        """On the configured unit: verify the last pick, then open the next slot whose card differs from the
        configured team. None = nothing to edit (the caller's fingerprint verdict decides 次へ / stop)."""
        m = self.memory
        expected = wte.expected_slots(m.team_slots or {})
        state = m.team_edit
        if state is None:
            problems = wte.team_rule_problems(expected, getattr(self, "owned_cards", None))
            if problems:
                return _stop("team_rules_violated", problems=problems)
            state = m.team_edit = {"pending": None, "card": None, "done": {}, "blank": 0}
        cards = r.get("unit_cards")
        # After a confirmed same-idol swap the game empties that idol's old slot: once it stays empty, fill it.
        # An empty slot that stays empty (any run, e.g. a slot emptied before this run) is filled too, after the
        # loading bound; filling an empty slot moves nothing out unless the game asks (same-idol dialog, handled).
        fill = state.get("blank", 0) >= (self.TEAM_EDIT_VACATED_READS if state.get("vacated")
                                         else self.TEAM_EDIT_BLANK_WAITS)
        step = wte.next_slot(cards, expected, state["done"], fill_blank=fill)
        if step["action"] == "wait":
            state["blank"] += 1
            if state["blank"] > self.TEAM_EDIT_BLANK_WAITS:
                return _stop("team_edit_slot_blank", slots=step.get("slots"), unit=unit)
            return _wait("unit_slot_blank", slots=step.get("slots"))
        state["blank"] = 0
        pending = state.get("pending")
        if pending is not None and state.get("picked") != pending:
            # The slot was tapped but no tile was picked yet (the picker has not opened): re-read, bounded.
            state["open_waits"] = state.get("open_waits", 0) + 1
            if state["open_waits"] > self.TEAM_EDIT_BLANK_WAITS:
                return _stop("team_edit_picker_not_opened", slot=pending)
            return _wait("team_edit_picker_opening", slot=pending)
        if pending is not None:
            # Back from the picker: the picked slot must now read as the wanted card, on this frame.
            from agent.core import wing_unit_check as wuc
            reading = (cards or {}).get(pending)
            if wuc.edit_slot_verdict(reading, state["card"]) != "match":
                return _stop("team_edit_slot_unverified", slot=pending, card=state["card"],
                             read=(reading or {}).get("card_id") if isinstance(reading, Mapping) else None)
            state["done"][pending] = state["card"]
            self.log.append({"event": "team_edit_slot", "unit": unit, "slot": pending, "card": state["card"]})
            state["pending"], state["card"] = None, None
            step = wte.next_slot(cards, expected, state["done"], fill_blank=fill)
        if step["action"] == "stop":
            return _stop(step["reason"], **{k: v for k, v in step.items() if k not in ("action", "reason")})
        if step["action"] == "done":
            if check.get("verdict") != "match":
                return _stop("team_edit_unverified", unit=unit, check=dict(check), states=step.get("states"))
            if state["done"]:
                self.log.append({"event": "team_edit_done", "unit": unit, "edits": dict(state["done"])})
            sort = state.get("sort")
            if sort and sort.get("changes"):
                # The game saves the picker sort (shared with the training list): report what the run left behind.
                self.log.append({"event": "picker_sort_left", "original": sort.get("original"),
                                 "current": sort.get("current"), "changes": sort["changes"]})
            return None
        if step["action"] != "edit":
            return _stop("team_edit_no_step")
        state["pending"], state["card"], state["picked"], state["open_waits"] = step["slot"], step["card"], None, 0
        state["todo"] = dict(step.get("todo") or {step["slot"]: step["card"]})
        return _click(wte.SLOT_INTENTS[step["slot"]], "team_edit_open", slot=step["slot"], card=step["card"])

    def card_attributes(self, cards) -> dict:
        """{card: main attribute} for the picker sort (picker_sort.card_attributes; tests may preset
        ``self.attributes``)."""
        cache = getattr(self, "attributes", None)
        if cache is None:
            cache = self.attributes = {}
        missing = [c for c in cards if c not in cache]
        if missing:
            cache.update(ps.card_attributes(missing))
        return {c: cache.get(c) for c in cards}

    def _team_pick_sort(self, state: dict, visit: dict, r: Mapping[str, Any], slot: str, card: str,
                        kind: str) -> NavDecision | None:
        """RunOptions.picker_sort (サポート picker only): verify a sort Maa applied, else plan one key change before the
        scan of this visit. None = scan on the current order."""
        if not self.options.picker_sort or kind != "support":
            return None
        sort = state.setdefault("sort", wte.new_sort_state())
        checked = wte.sort_verify(sort, visit, r)
        if checked is not None:
            if checked["action"] == "wait":
                return _wait(checked["reason"], slot=slot)
            self.log.append({"event": "picker_sort_stop", "slot": slot, **checked})
            return _stop(checked["reason"], slot=slot, card=card,
                         **{k: v for k, v in checked.items() if k not in ("action", "reason")})
        applied = (visit.get("sort_plan") or {}).get("applied")
        if applied and not visit.get("sort_applied_logged"):
            visit["sort_applied_logged"] = True
            self.log.append({"event": "picker_sort_applied", "slot": slot, "key": applied})
        remaining = [c for s, c in (state.get("todo") or {slot: card}).items()
                     if wte.slot_kind(s) == kind and s not in (state.get("done") or {})]
        focus = None if visit.get("survey") and not visit.get("surveyed") else card
        fixed = wte.EDIT_ONLY_SORT_KEY if self.options.edit_only else None   # Auto 自动编队: always Lv順
        step = wte.sort_step(sort, visit, r, focus, remaining, self.card_attributes(remaining), fixed_key=fixed)
        if visit.get("sort_plan") is not None and not visit.get("sort_logged"):
            visit["sort_logged"] = True
            self.log.append({"event": "picker_sort_plan", "slot": slot, "plan": visit["sort_plan"]})
        if step is None:
            return None
        return _click(step["intent"], "picker_sort_open", slot=slot, card=card, key=step["key"])

    def _decide_picker_sort(self, screen: str, r: dict) -> NavDecision:
        """並び替え popup: answered only when this run opened it for the team-edit picker (wte.sort_popup_step)."""
        state = self.memory.team_edit
        sort = state.get("sort") if isinstance(state, dict) and self.options.picker_sort else None
        items = r.get("sort_popup_items")
        popup = ps.parse_popup(items) if items is not None else None
        shares = r.get("sort_selected") or {}
        step = wte.sort_popup_step(sort, popup, lambda cell: float(shares.get(f"r{cell[0]}c{cell[1]}", 0.0)))
        if step["action"] == "wait":
            return _wait(step["reason"])
        if step["action"] == "stop":
            if sort:
                self.log.append({"event": "picker_sort_stop", **{k: v for k, v in step.items() if k != "action"}})
            return _stop(step["reason"], **{k: v for k, v in step.items() if k not in ("action", "reason")})
        self.log.append({"event": "picker_sort_tap", "intent": step["intent"], "key": step["key"]})
        return _click(step["intent"], "picker_sort", key=step["key"])

    def _decide_team_pick(self, screen: str, r: dict) -> NavDecision:
        """プロデュース/サポートアイドル編成 picker: find the wanted card (read-only scroll scan, wing_team_edit.picker_step)
        and tap its tile once, else stop. The first サポート visit surveys every wanted support card before any swap."""
        o, m = self.options, self.memory
        state = m.team_edit
        if not (o.team_edit and o.mode == "start") or not state or state.get("pending") is None:
            return _stop("team_edit_not_automated")
        slot, card = state["pending"], state["card"]
        if state.get("picked") == slot:
            # The tap was sent but the picker is still shown (or shown again): never tap twice.
            return _stop("team_edit_pick_unconfirmed", slot=slot, card=card)
        kind = r.get("picker_kind")
        if kind != wte.slot_kind(slot):
            return _stop("team_edit_picker_kind", slot=slot, kind=kind)
        visit = state.get("visit")
        if not isinstance(visit, dict) or visit.get("slot") != slot:
            todo = {s: c for s, c in (state.get("todo") or {slot: card}).items() if wte.slot_kind(s) == kind}
            survey = todo if not state.get("surveyed", {}).get(kind) and len(todo) > 1 else {}
            visit = state["visit"] = wte.new_visit(slot, survey)
        sorted_step = self._team_pick_sort(state, visit, r, slot, card, kind)
        if sorted_step is not None:
            return sorted_step
        step = wte.picker_step(visit, r, card)
        if visit.get("surveyed") and visit.get("survey"):
            state.setdefault("surveyed", {})[kind] = True
        if step["action"] == "wait":
            return _wait(step["reason"], slot=slot)
        if step["action"] == "scroll":
            self.log.append({"event": "team_edit_scroll", "slot": slot, "dir": step["dir"], "why": step["why"],
                             "pos": visit["pos"], "scrolls": visit["scrolls"]})
            return _click(step["intent"], "team_edit_scroll", slot=slot, card=card, dir=step["dir"])
        if step["action"] == "stop":
            reason = step["reason"]
            detail = {k: v for k, v in step.items() if k not in ("action", "reason")}
            if state.get("done") and reason in ("team_edit_card_not_in_list", "team_edit_tile_not_enabled",
                                                "team_edit_scroll_not_enabled"):
                reason += "_partial"          # earlier slots of this unit were already changed (kept)
            self.log.append({"event": "team_edit_picker_stop", "slot": slot, "reason": reason, **detail})
            return _stop(reason, slot=slot, card=card, edited=dict(state.get("done") or {}), **detail)
        state["picked"] = slot
        return _click(step["intent"], "team_edit_pick", slot=slot, card=card, tile=[step["row"], step["col"]],
                      phase=step["phase"], verified=step["verified"])

    def _log_unit_cards(self, r: Mapping[str, Any], unit, check: Mapping[str, Any]) -> None:
        """Decision-log event ``unit_cards`` (2026-10-11 UI team snapshot, tools/ui_wing.py): the accepted card id per
        ユニット編成 slot of the unit Maa is about to use (None = not read with confidence) and the verdict. Read-only."""
        cards = r.get("unit_cards")
        if not isinstance(cards, Mapping):
            return
        slots = {}
        for slot in ("P", "S1", "S2", "S3", "S4", "guest"):
            reading = cards.get(slot)
            ok = isinstance(reading, Mapping) and reading.get("status") == "accepted" and isinstance(reading.get("card_id"), str)
            slots[slot] = reading["card_id"] if ok else None
        self.log.append({"event": "unit_cards", "unit": unit, "cards": slots, "verdict": check.get("verdict"),
                         "slots": dict(check.get("slots") or {})})

    def _unit_fingerprint(self, r: Mapping[str, Any]) -> dict:
        """Verdict of the unit on screen against the configured team (``wing_unit_check.compare``)."""
        if not self.memory.team_slots:
            return {"verdict": "uncertain", "reason": "team_not_configured"}
        from agent.core import wing_unit_check as wuc
        editing = self.options.team_edit and self.options.mode == "start"
        return wuc.compare(r.get("unit_cards"), self.memory.team_slots, editing=editing)

    def _unit_search_step(self, r: Mapping[str, Any], current) -> NavDecision:
        """Unit search (``unit_search``): fingerprint units 1–20 one tab at a time (the current page first, then the
        other page via ‹/›, both verified since 2026-10-13); the first unit that matches the configured team is used
        (``target_unit`` is set to it); none -> stop ``team_not_found_in_units``."""
        m = self.memory
        search = m.unit_search
        if not isinstance(current, int) or not 1 <= current <= UNIT_COUNT:
            return _stop("unit_index_unread")
        page = r.get("unit_page")
        if page is None:
            page = (current - 1) // UNITS_PER_PAGE
        # 2026-10-14 live: after ‹/› the tabs show the other page while the selected unit (counter) stays on the old
        # one; the cards on screen still belong to that already-checked unit, so only pick a tab on the shown page.
        turned = page in range(UNIT_COUNT // UNITS_PER_PAGE) and page != (current - 1) // UNITS_PER_PAGE
        if page != (current - 1) // UNITS_PER_PAGE and not (turned and str(current) in search["checked"]):
            return _stop("unit_page_mismatch", unit=current, page=page)
        checked = search["checked"]
        if not turned and str(current) not in checked:
            check = self._unit_fingerprint(r)
            checked[str(current)] = check.get("verdict")
            if check.get("verdict") == "match":
                self._log_unit_cards(r, current, check)
                m.target_unit, m.unit_search = current, None
                self.log.append({"event": "unit_found", "unit": current, "checked": dict(checked)})
                return _click("unit_next", "unit_search_match", unit=current, check=check)
        if not turned and checked.get(str(current)) == "match":
            m.target_unit, m.unit_search = current, None
            return _click("unit_next", "unit_search_match", unit=current)
        rest = [u for u in range(1, UNIT_COUNT + 1) if str(u) not in checked]
        if not rest:
            return _stop("team_not_found_in_units", checked=dict(checked))
        if search["taps"] >= UNIT_SEARCH_MAX_TAPS:
            return _stop("unit_search_budget", checked=dict(checked))
        search["taps"] += 1
        here = [u for u in rest if (u - 1) // UNITS_PER_PAGE == page]
        if here:
            return _click(f"unit_tab_{(here[0] - 1) % UNITS_PER_PAGE + 1}", "unit_search", unit=here[0],
                          checked=dict(checked))
        want = (rest[0] - 1) // UNITS_PER_PAGE
        return _click("unit_page_next" if want > page else "unit_page_prev", "unit_search_page", page=page, want=want,
                      checked=dict(checked))

    def team_conflicts(self, team) -> list:
        """Idols that appear more than once among the configured P / S / guest cards ([] = ok or no team set)."""
        if not team:
            return []
        fn, _ = self.hook("team_idol")
        keys = []
        for card in team:
            if fn is not None and not isinstance(card, Mapping):
                keys.append(fn(card))
            elif fn is not None:
                from agent.core.wing_team import CardRecord
                keys.append(fn(CardRecord.from_mapping(card)))
            else:
                keys.append(card.get("idol") if isinstance(card, Mapping) else getattr(card, "idol", None))
        seen, dup = set(), []
        for key in keys:
            if key is not None and key in seen and key not in dup:
                dup.append(key)
            seen.add(key)
        return dup

    # ---- action gate ------------------------------------------------------------------------------------------
    def act(self, screen: str, decision: NavDecision) -> tuple[bool, str]:
        """Agent-side gate of one click (the Pipeline Click node and the WebView2 gate check again)."""
        if decision.action != "click" or decision.intent not in TARGETS:
            return False, "not_a_click"
        if not self.is_active():
            return False, "run_not_active"
        if decision.intent not in screen_intents(screen):
            return False, "intent_not_bound"
        _, verified = TARGETS[decision.intent]
        key = (screen, decision.intent)
        self._repeat = self._repeat + 1 if key == self._last_intent else 1
        self._last_intent = key
        if self._repeat > REPEAT_LIMITS.get(decision.intent, MAX_SAME_INTENT_IN_A_ROW):
            return False, "repeat_without_progress"
        if self.acts >= min(MAX_ACTS_PER_RUN, self.options.max_acts):
            return False, "act_budget_exhausted"
        if not verified:
            return False, "target_not_verified"
        if not (LIVE_INPUT_ENABLED or self.options.input_enabled):
            return False, "live_input_disabled"
        if decision.intent == "reward_reroll_ok":
            rv = self.memory.reward_review or {}
            if not (self.options.allow_reward_reroll and self.params["end"].get("reroll")):
                return False, "reroll_not_enabled"
            if (rv.get("pending") or {}).get("kind") != "reroll" or rv.get("rerolls", 0) >= (rv.get("reroll_cap") or 0):
                return False, "reroll_not_planned"
            rv["rerolls"] = rv.get("rerolls", 0) + 1        # counted when sent, whatever happens next
            self.acts += 1
            return True, "sent"
        if decision.intent in CONSUMING_INTENTS:
            if not (self.options.mode == "start" and self.options.allow_start_spend):
                return False, "spend_not_enabled"
            if self.memory.start_attempted:
                return False, "start_already_attempted"
            self.memory.start_attempted = True     # whatever happens next, never attempted twice
        self.acts += 1
        return True, "sent"


def p_card_weak(card_id) -> bool:
    """SR (and R) P cards have weak skills. The ID's third digit is the rarity (local SCDB, 1576
    cards: 2 R, 3 SR, 4 SSR, 5 UR); an unknown ID is not weak (old behaviour)."""
    text = str(card_id or "")
    return len(text) == 10 and text.isdigit() and text[0] == "1" and text[2] in "123"


def _sp_key_ok(key) -> bool:
    """SP session key: [season, weeks_left] of a Schedule week or [stage screen] before the W.I.N.G. stage."""
    return isinstance(key, list) and ((len(key) == 2 and all(type(v) is int for v in key))
                                      or (len(key) == 1 and isinstance(key[0], str)))


def _sp_future_income(key, sp: int | None, spent: int, safety_weeks: int = SP_RUN_WEEKS) -> dict:
    """Expected SP income until the last spending chance (the W.I.N.G. stage): the run's average SP per week so far
    (SP now + SP Maa spent, over the weeks already played) times the weeks left including this one. A stage key or an unread
    week -> 0 (spend everything now). Bounded to 0..40 SP/week (live 2026-10-10: ~349 SP by S3 with nothing spent)."""
    if not (isinstance(key, list) and len(key) == 2 and all(type(v) is int for v in key) and isinstance(sp, int)):
        return {"future": 0, "rate": None, "week": None, "weeks_left": 0}
    season, weeks_left = key
    week = (season - 1) * 8 + (8 - weeks_left) + 1
    remaining = max(0, safety_weeks - week + 1)
    rate = max(0.0, min(40.0, (sp + max(0, spent)) / max(1, week - 1)))     # SP so far came from the weeks played
    return {"future": int(rate * remaining), "rate": round(rate, 2), "week": week, "weeks_left": remaining}


def _stat_drop(before, after) -> dict | None:
    """{stat: negative change} when a week lowered any stat (a failed lesson / work), else None. All four stats must be
    read on both sides; a change beyond ``STAT_JUMP_MAX`` marks a misread and reports nothing."""
    if not (isinstance(before, Mapping) and isinstance(after, Mapping)
            and all(type(before.get(k)) is int and type(after.get(k)) is int for k in STAT_KEYS)):
        return None
    delta = {k: after[k] - before[k] for k in STAT_KEYS}
    if any(abs(v) > STAT_JUMP_MAX for v in delta.values()):
        return None
    drop = {k: v for k, v in delta.items() if v < 0}
    return drop or None


def _stat_growth_left(key, stat: int | None, run: dict, safety_weeks: int = SP_RUN_WEEKS) -> int | None:
    """Primary growth expected after this week until the W.I.N.G. stage (caps are valued against it).
    Rate: the gain per week since the run's first SP session (``run["growth_mark"]`` = [week, stat], recorded here), or,
    within two weeks of it, the stat over the weeks played (counts the starting stat, so it runs high; caps rank above
    passives anyway). Bounded to 0..STAT_RATE_MAX per week. A stage key or an unread week/stat -> None."""
    if not (isinstance(key, list) and len(key) == 2 and all(type(v) is int for v in key) and type(stat) is int):
        return None
    season, weeks_left = key
    week = (season - 1) * 8 + (8 - weeks_left) + 1
    mark = run.get("growth_mark")
    if not (isinstance(mark, list) and len(mark) == 2 and all(type(v) is int for v in mark) and mark[0] <= week):
        mark = run["growth_mark"] = [week, stat]
    span = week - mark[0]
    rate = (stat - mark[1]) / span if span >= 2 else stat / max(1, week - 1)
    return int(max(0.0, min(STAT_RATE_MAX, rate)) * max(0, safety_weeks - week))


def _slot_distance(slot: Mapping[str, Any]) -> float:
    x, y = wsp.slot_label(tuple(slot["slot"]))
    return round(((x - wsp.CENTER[0]) ** 2 + (y - wsp.CENTER[1]) ** 2) ** 0.5, 1)


def _brief_step(step: Mapping[str, Any] | None) -> dict | None:
    return None if step is None else {k: v for k, v in step.items() if k != "sigs"}


def _expect(kind: str) -> dict:
    spec = LIVE_PLAY_EXPECT[kind]
    return {"screens": list(spec["screens"]), "next_phase": list(spec["next_phase"])}


def _special_of(info: Mapping[str, Any]) -> str | None:
    """Limited-audition id of the chosen audition (``s2_100k_ereebest`` -> ``ereebest``), None for a regular one."""
    from agent.core import wing_screen as wsc
    audition = str(info.get("audition_id") or "")
    return next((k for k in wsc.SPECIAL_AUDITION_NAMES if audition.endswith("_" + k)), None)


def _reward_of(info: Mapping[str, Any], state) -> int | None:
    audition = info.get("audition_id")
    for a in getattr(state, "auditions", ()) or ():
        if a.id == audition:
            return a.fan_reward
    m = re.search(r"(\d{4,6})", str(audition or ""))
    return int(m.group(1)) if m else None


def build_week_state(r: Mapping[str, Any], memory: RunMemory):
    """WingState from schedule readings + run memory; (None, problems) when a required value is unread."""
    from agent.core import wing_state as ws
    problems = []
    season, weeks_left = r.get("season"), r.get("weeks_left")
    if weeks_left is None and season == 1 and memory.last_week_decision is None:
        # 2026-10-12 live: the outlined yellow 「8」 of a fresh Produce's first week does not OCR; season 1 opens with
        # 残り8週 and no week decision exists yet in this Produce's memory.
        weeks_left = 8
        r = {**r, "weeks_left": 8}
    if not isinstance(season, int):
        problems.append("season_unread")
    if not isinstance(weeks_left, int):
        problems.append("weeks_left_unread")
    if not r.get("stats"):
        problems.append("stats_unread")
    if r.get("fans") is None:
        problems.append("fans_unread")
    if problems:
        return None, problems
    options = []
    for room in r.get("rooms") or []:
        fields = {key: room[key] for key in ("id", "kind", "attribute", "label", "available", "preview",
                  "stamina_cost", "supports", "stamina_recovery", "fan_gain", "support_ids", *ws.ScheduleOption._SCORER_KEYS)
                  if key in room}
        fields.setdefault("available", True)
        # The current reader sees a room badge, not a count of Excellent supports.
        if isinstance(fields.get("excellent"), bool):
            fields["excellent_present"] = fields.pop("excellent")
        if fields.get("level") == "MAX":
            fields["level"] = 5
        try:
            options.append(ws.ScheduleOption.from_dict(fields))
        except (KeyError, TypeError, ValueError) as exc:
            return None, [f"room_error:{exc}"]
    options.append(ws.ScheduleOption("rest", "rest", available=True))
    seen = memory.auditions_seen if isinstance(memory.auditions_seen, Mapping) else {}
    auditions = []
    if seen.get("done") and seen.get("key") == [season, weeks_left]:
        for page in seen.get("pages") or ():
            recommended = {k: v for k, v in (page.get("recommended") or {}).items()
                           if k in ws.STATS and isinstance(v, int)}
            special = page.get("special")
            # A limited audition is planned only on its own 推奨能力値: the season table does not cover it.
            auditions.append(ws.Audition(f"s{season}_{page['reward'] // 1000}k" + (f"_{special}" if special else ""),
                                         f"+{page['reward']}",
                                         page["reward"], available=not page.get("locked")
                                         and not (special and not recommended),
                                         difficulty=page.get("difficulty"), recommended=recommended,
                                         required_rank=page.get("required_rank")))
    try:
        state = ws.WingState(
            phase="week", season=season, week=ws.WEEKS_PER_SEASON - weeks_left + 1,
            stats=dict(r["stats"]), fans=r["fans"], stamina=r.get("stamina_pct"),
            max_stamina=100 if r.get("stamina_pct") is not None else None, sp=r.get("sp"),
            stat_caps=({**(memory.stat_caps or {}), **dict(r["stat_caps"])} if r.get("stat_caps")
                       else dict(memory.stat_caps) if memory.stat_caps else None), condition=r.get("condition"),
            trouble_rate=r.get("trouble_rate"), promise=memory.promise if isinstance(memory.promise, Mapping)
            and "season" in memory.promise else None,
            options=tuple(options), auditions=tuple(auditions), trend=seen.get("trend") if auditions else None,
            rank=seen.get("rank_floor") if auditions else None,
            wins=tuple(ws.AuditionWin(s, f) for s, f in memory.wins),
            confidence=1.0, recognized=True, evidence=("wing_nav",))
    except (TypeError, ValueError) as exc:
        return None, [f"state_error:{exc}"]
    return state, []
