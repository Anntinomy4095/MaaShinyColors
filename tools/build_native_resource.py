#!/usr/bin/env python3
"""构建私有原生模板资源包（dev_assets/private/resource_native），供 SC.Common Pipeline 使用。

模板全部来自本项目已校准的原生 1280x720 识别档案（nav-native-v27、grade-native-v1、
shop-native-v1、training-native-v6），按 Pipeline 友好的名称复制到 ``image/native/...``，并写入哈希清单。
另有若干模板在原生帧上按固定 ROI 裁剪（ミッション達成 OK、フェスについて 页码、标题画面、参加ユニット 等，见 CROPPED）。
B5b 起 スキップ弹窗模板改为本机原生采样帧裁剪（``match_skip/*``），旧机器模板不再使用。

只读写 dev_assets/private；不连接浏览器、宿主或设备，也不发送任何输入。
资源包不入库（dev_assets/private/ 已被 .gitignore 忽略）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets" / "private"
DEFAULT_OUTPUT = PRIVATE / "resource_native"
# Every Pipeline file under base/pipeline (common/ plus task files such as startup.json) is scanned.
PIPELINE_DIR = ROOT / "assets" / "resource" / "base" / "pipeline"
TEMPLATE_PREFIX = "native/"
MANIFEST_NAME = "manifest.json"
CANVAS = (1280, 720)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Calibrated source profiles (repo-relative; all under dev_assets/private).
SOURCES = {
    "nav": "dev_assets/private/in_app_browser/20261001-daily-research/nav-native-v27",
    "grade": "dev_assets/private/in_app_browser/20261002-grade/grade-native-v1",
    "shop": "dev_assets/private/in_app_browser/20261002-shop/shop-native-v1",
    "training": "dev_assets/private/in_app_browser/20261001-daily-research/training-native-v6",
}

# Pipeline template name -> (source profile, file in that profile, calibrated ROI).
COPIED = {
    "home/fes_text.png": ("nav", "navprofile-Home-FesText.png", [1150, 645, 80, 25]),
    "loading/text.png": ("nav", "startup-popup-loading-text-template.png", [826, 660, 306, 47]),
    "loading/feather.png": ("nav", "startup-popup-loading-feather-template.png", [1150, 613, 67, 62]),
    "announcement/title.png": ("nav", "navprofile-Announcement-Title.png", [45, 18, 125, 45]),
    "announcement/list_heading.png": ("nav", "navprofile-Announcement-ListHeading.png", [620, 108, 280, 43]),
    "announcement/topics_selected.png": ("nav", "navigation-announcement-topicsselected-template.png", [15, 87, 176, 86]),
    "announcement/return.png": ("nav", "navprofile-Announcement-ReturnTarget.png", [17, 620, 84, 83]),
    "reward/title.png": ("nav", "navprofile-Reward-Title.png", [408, 151, 184, 37]),
    "reward/body.png": ("nav", "navprofile-Reward-Body.png", [491, 241, 301, 31]),
    "reward/ok.png": ("nav", "navprofile-Reward-OK.png", [556, 481, 169, 81]),
    "mission_completed/title.png": ("nav", "navprofile-MissionCompleted-Title.png", [405, 90, 253, 40]),
    "mission_completed/body.png": ("nav", "navprofile-MissionCompleted-Body.png", [379, 153, 533, 61]),
    "mission_completed/mission_target.png": ("nav", "navprofile-MissionCompleted-MissionTarget.png", [658, 540, 171, 81]),
    "mission_help/title.png": ("nav", "daily-v6-MissionHelp-Title.png", [191, 79, 438, 39]),
    "mission_help/page.png": ("nav", "daily-v6-MissionHelp-Page.png", [1125, 78, 86, 41]),
    "mission_help/close.png": ("nav", "daily-v6-MissionHelp-Close.png", [558, 562, 167, 78]),
    "match_help/title.png": ("nav", "navprofile-MatchHelp-Title.png", [187, 77, 339, 42]),
    "match_help/page.png": ("nav", "navprofile-MatchHelp-Page.png", [1107, 78, 107, 43]),
    "match_help/close.png": ("nav", "navprofile-MatchHelp-Close.png", [558, 562, 167, 78]),
    "training_help/title.png": ("nav", "daily-v13-training-help-Title.png", [190, 80, 342, 37]),
    "training_help/page.png": ("nav", "daily-v13-training-help-Page.png", [1125, 78, 86, 41]),
    "training_help/close.png": ("nav", "daily-v13-training-help-Close.png", [558, 562, 167, 78]),
    "fes_help/title.png": ("grade", "FesHelp-Title.png", [190, 82, 225, 40]),
    "fes_help/icon.png": ("grade", "FesHelp-Icon.png", [68, 60, 100, 74]),
    "fes_help/close.png": ("grade", "FesHelp-Close.png", [558, 562, 167, 78]),
    # 2026-11-23: any first-visit help dialog (SC.Popup.AnyHelp): same crops, ROI widened 6 px for 1 px layout shifts.
    "any_help/icon.png": ("grade", "FesHelp-Icon.png", [62, 54, 112, 86]),
    "any_help/close.png": ("grade", "FesHelp-Close.png", [552, 556, 179, 90]),
    "pickup/title.png": ("nav", "navprofile-Pickup-Title.png", [465, 52, 343, 39]),
    "pickup/close.png": ("nav", "navprofile-Pickup-Close.png", [1091, 48, 42, 38]),
    "pickup/details.png": ("nav", "navprofile-Pickup-Details.png", [552, 558, 101, 33]),
    "story/skip.png": ("nav", "daily-v9-Story-Skip.png", [1180, 17, 82, 80]),
    "story/log.png": ("nav", "daily-v9-Story-Log.png", [1173, 423, 82, 81]),
    "story/hide.png": ("nav", "daily-v9-Story-Hide.png", [1173, 514, 82, 79]),
    "mission/title.png": ("nav", "navprofile-Mission-Title.png", [38, 18, 161, 42]),
    "mission/daily_tab.png": ("nav", "navprofile-Mission-DailyTab.png", [42, 94, 115, 33]),
    "mission/return.png": ("nav", "navprofile-Mission-Return.png", [17, 620, 84, 83]),
    "idol_portal/title.png": ("nav", "navprofile-IdolPortal-Title.png", [40, 18, 141, 44]),
    "idol_portal/training_entry.png": ("nav", "navprofile-IdolPortal-TrainingEntry.png", [409, 285, 218, 50]),
    "idol_portal/training_entry_native.png": ("nav", "navprofile-IdolPortal-TrainingEntryNative.png", [409, 285, 218, 50]),
    "idol_portal/return.png": ("nav", "navprofile-IdolPortal-Return.png", [17, 620, 84, 83]),
    "training_list/title.png": ("nav", "navprofile-TrainingList-Title.png", [43, 17, 328, 48]),
    "training_list/support_tab.png": ("nav", "navprofile-TrainingList-SupportTab.png", [39, 98, 121, 38]),
    "training_list/support_tab_native.png": ("nav", "navprofile-TrainingList-SupportTabNative.png", [39, 98, 121, 38]),
    "training_list/asahi_portrait.png": ("nav", "navprofile-TrainingList-AsahiPortrait.png", [409, 189, 101, 87]),
    "training_selected/title.png": ("nav", "navprofile-TrainingSelected-Title.png", [48, 24, 177, 31]),
    "training_selected/card_name.png": ("nav", "navprofile-TrainingSelected-CardName.png", [159, 96, 451, 31]),
    "training_selected/portrait.png": ("nav", "navprofile-TrainingSelected-Portrait.png", [40, 102, 99, 75]),
    "match_main/title.png": ("nav", "navprofile-MatchMain-Title.png", [49, 23, 170, 34]),
    "match_main/area_prompt.png": ("nav", "navprofile-MatchMain-AreaPrompt.png", [97, 157, 236, 37]),
    "match_main/skip_target.png": ("nav", "navprofile-MatchMain-SkipTarget.png", [1126, 518, 130, 33]),
    "fes_menu/match_label.png": ("grade", "FesMenu-MatchLabel.png", [985, 525, 120, 32]),
    "fes_menu/grade_label.png": ("grade", "FesMenu-GradeLabel.png", [1128, 547, 126, 26]),
    "fes_main/title.png": ("grade", "FesMain-Title.png", [6, 16, 150, 40]),
    "fes_main/back.png": ("grade", "FesMain-Back.png", [20, 628, 74, 66]),
    "fes_main/unit_button.png": ("grade", "FesMain-UnitButton.png", [905, 628, 160, 62]),
    "fes_main/join_label.png": ("grade", "FesMain-JoinLabel.png", [1130, 595, 105, 50]),
    # Shop task (B4), from the calibrated shop-native-v1 profile (same ROIs as the legacy shop runner).
    "home/shop_label.png": ("shop", "Home-ShopLabel.png", [35, 276, 56, 17]),
    "shop_premium/title.png": ("shop", "Premium-Title.png", [6, 16, 150, 40]),
    "shop_premium/moneyidle.png": ("shop", "Premium-MoneyIdle.png", [440, 90, 112, 42]),
    "shop_premium/newbiesubtab.png": ("shop", "Premium-NewbieSubtab.png", [40, 165, 172, 44]),
    "shop_list/banner1.png": ("shop", "List-Banner1.png", [118, 228, 102, 30]),
    "shop_list/monthly1.png": ("shop", "List-MonthlyHeader1.png", [118, 228, 102, 30]),
    "shop_list/limit_label1.png": ("shop", "List-LimitLabel1.png", [60, 516, 126, 28]),
    "shop_list/coin1.png": ("shop", "List-Coin1.png", [94, 598, 20, 20]),
    "shop_list/banner2.png": ("shop", "List-Banner2.png", [388, 228, 102, 30]),
    "shop_list/monthly2.png": ("shop", "List-MonthlyHeader2.png", [388, 228, 102, 30]),
    "shop_list/limit_label2.png": ("shop", "List-LimitLabel2.png", [330, 516, 126, 28]),
    "shop_list/coin2.png": ("shop", "List-Coin2.png", [364, 598, 20, 20]),
    "shop_list/banner3.png": ("shop", "List-Banner3.png", [658, 228, 102, 30]),
    "shop_list/monthly3.png": ("shop", "List-MonthlyHeader3.png", [658, 228, 102, 30]),
    "shop_list/limit_label3.png": ("shop", "List-LimitLabel3.png", [600, 516, 126, 28]),
    "shop_list/coin3.png": ("shop", "List-Coin3.png", [634, 598, 20, 20]),
    "shop_confirm/title.png": ("shop", "Confirm-Title.png", [360, 40, 162, 36]),
    "shop_confirm/labels.png": ("shop", "Confirm-Labels.png", [412, 352, 170, 182]),
    "shop_confirm/controls.png": ("shop", "Confirm-Controls.png", [640, 340, 190, 40]),
    "shop_confirm/cancel.png": ("shop", "Confirm-Cancel.png", [457, 587, 167, 79]),
    "shop_confirm/exchange.png": ("shop", "Confirm-Exchange.png", [657, 587, 167, 79]),
    "shop_result/title.png": ("shop", "Result-Title.png", [360, 170, 162, 36]),
    "shop_result/labels.png": ("shop", "Result-Labels.png", [405, 343, 170, 72]),
    "shop_result/close.png": ("shop", "Result-Close.png", [557, 457, 167, 80]),
    "shop_list/title.png": ("shop", "List-Title.png", [6, 16, 150, 40]),
    "shop_list/money_selected.png": ("shop", "List-MoneySelected.png", [440, 90, 112, 42]),
    "shop_list/money_icon.png": ("shop", "List-MoneyIcon.png", [958, 22, 40, 40]),
    "shop_list/all_selected.png": ("shop", "List-AllSelected.png", [40, 165, 172, 44]),
    "shop_list/back.png": ("shop", "List-Back.png", [20, 628, 74, 66]),
    # Training task (B5), from the calibrated training-native-v6 profile (same ROIs as agent/core/training_flow.ANCHORS).
    # Zero = training detail with no ticket selected (also the After state); One = one Bronze ticket selected.
    **{f"training_detail/{name}.png": ("training", f"Zero-{anchor}.png", roi) for name, anchor, roi in (
        ("title", "Title", [48, 24, 177, 31]), ("card_name", "CardName", [159, 96, 451, 31]),
        ("portrait", "Portrait", [40, 102, 99, 75]), ("bronze_label", "BronzeLabel", [810, 270, 325, 34]),
        ("bronze_zero", "BronzeZero", [939, 322, 62, 44]), ("lv50_zero", "Lv50Zero", [939, 184, 62, 44]),
        ("silver_zero", "SilverZero", [939, 456, 62, 44]), ("clear_disabled", "ClearDisabled", [906, 636, 146, 50]),
        ("plus", "Plus", [1058, 320, 58, 52]))},
    **{f"training_one/{name}.png": ("training", f"One-{anchor}.png", roi) for name, anchor, roi in (
        ("title", "Title", [48, 24, 177, 31]), ("card_name", "CardName", [159, 96, 451, 31]),
        ("portrait", "Portrait", [40, 102, 99, 75]), ("bronze_label", "BronzeLabel", [810, 270, 325, 34]),
        ("bronze_one", "BronzeOne", [939, 322, 62, 44]), ("silver_zero", "SilverZero", [939, 456, 62, 44]),
        ("exp50", "Exp50", [164, 175, 147, 28]), ("submit", "Submit", [1097, 635, 153, 57]))},
    **{f"training_confirm/{name}.png": ("training", f"Confirm-{anchor}.png", roi) for name, anchor, roi in (
        ("title", "Title", [406, 165, 230, 35]), ("card_name", "CardName", [159, 96, 451, 31]),
        ("prompt", "Prompt", [453, 245, 369, 57]), ("only_bronze_one", "OnlyBronzeOne", [366, 219, 548, 220]),
        ("bronze_ticket", "BronzeTicket", [592, 327, 105, 83]), ("quantity_one", "QuantityOne", [672, 412, 24, 18]),
        ("start", "Start", [660, 475, 159, 68]))},
    **{f"training_result/{name}.png": ("training", f"Result-{anchor}.png", roi) for name, anchor, roi in (
        ("title", "Title", [408, 190, 260, 35]), ("card_name_prefix", "CardNamePrefix", [159, 96, 271, 31]),
        ("portrait", "Portrait", [40, 102, 99, 75]), ("stats_labels", "StatsLabels", [374, 328, 40, 88]),
        ("close", "Close", [563, 451, 154, 65]))},
    # GradeFes task (B5), from grade-native-v1 (manual native captures 2026-10-02). Only static parts of the difficulty
    # dialog are copied; the circle labels, score texts and prompt are cropped below (dynamic-element rule, B5g).
    **{f"grade_difficulty/{name}.png": ("grade", f"Difficulty-{anchor}.png", roi) for name, anchor, roi in (
        ("title", "Title", [232, 88, 815, 46]),
        ("checkbox_off", "CheckboxOff", [866, 644, 46, 46]), ("bulk_label", "BulkLabel", [918, 644, 160, 46]),
        ("back", "Back", [20, 628, 74, 66]))},
    "grade_mode/rehearsal_button.png": ("grade", "ModeSelect-RehearsalButton.png", [912, 378, 150, 150]),
    "grade_mode/grade_button.png": ("grade", "ModeSelect-GradeButton.png", [1092, 378, 150, 150]),
}

# Pipeline template name -> (native source frame, crop ROI). Measured for this batch:
# - ミッション達成 OK: button border spans x 453..619 on row 600 of the frame; the ROI keeps the same
#   2 px white margin as the calibrated ミッションへ template ([658, 540, 171, 81], border 660..826).
# - フェスについて page counter: white "1/18" glyphs span x 1115..1201, y 85..109 (same box as MATCH help).
TENSION_FRAME = "dev_assets/private/run_logs/samples/grade-after-spend-20261002T133404973940Z/frame-070.png"
# Grade Fes live (B5d), from the manual native live capture 2026-10-03 (user-played, 396 deduplicated frames).
LIVE = "dev_assets/private/grade-live-manual-20261003-frames/"
LIVE_WAIT = LIVE + "296_20261003-002550-378065_frame-013.png"     # TURN 2/7 waiting for a card
LIVE_TARGET = LIVE + "300_20261003-002550-378065_frame-017.png"   # card 3 selected (スライドで交換 under it)
LIVE_TIMING = LIVE + "229_20261003-002520-569840_frame-001.png"   # APPEAL timing bar
LIVE_REPORT = LIVE + "380_20261003-002639-189647_frame-019.png"   # LIVE REPORT (numbers settled)
# 2026-10-22 official Grade TURN 6/6: after the 思い出アピール judge choice and its Good/Bad film the turn screen waits
# for one tap (no 流行 badges, a yellow arrow at the chosen judge; frames 050..083 identical, 34 s).
LIVE_MEMORY_WAIT = "dev_assets/private/grade-live-memory-20261022-frames/frame-050.png"
LIVE_TINT = "dev_assets/private/grade-live-tint-20261022/"
# B5j: LIVE REPORT of the auto run 2026-10-03 10:16 (taken over at TURN 3, 1位): 「獲得マニー2倍」 campaign badge and the
# ミッション報酬獲得 row; the frame after the dropped OK tap (numbers settled, rewards revealed).
LIVE_REPORT_AUTO = "dev_assets/private/grade-live-auto-20261003-frames/report.png"
# B5k: the second LIVE REPORT page (ranking list) of the auto run 2026-10-03 11:09 (browser_tasks/20261003-111148-904428).
LIVE_RANKING = "dev_assets/private/grade-live-auto-20261003-frames/report-ranking.png"
NEW_GACHA = "dev_assets/private/manual-capture-kept-20261002/new-gacha-cutin-20261003.png"
FES_RESUME = "dev_assets/private/manual-capture-kept-20261002/fes-resume-popup-20261003.png"
# Home without any badge (2026-10-03 05:15 live frame: ミッション/プレゼント badges gone, ガシャ 「1日1回無料」 tag shown).
HOME_NO_BADGE = "dev_assets/private/manual-capture-kept-20261002/home-20261003-nobadge.png"
# Grade difficulty dialog (B5g). 2026-10-02 layout without a campaign (manual capture 066, prompt bubble at its smallest
# pulse phase) and the 2026-10-03 「獲得マニー 2倍」 campaign layout: a starburst badge over the top-right of each circle
# and the whole circle row 22-23 px further left (live frame 05:44, prompt bubble at its largest phase). The prompt bubble
# pulses continuously (left edge x 815..819), so its text is kept at three phases (smallest / middle / largest).
BUSINESS_TOP = "dev_assets/private/business-native-20261003-frames/frame-003.png"
# B6d WORK REPORT after 「営業完了」 of slot 1 (live 2026-10-03 19:10, run_logs/samples/business-top-20261003T091040428732Z,
# copied to claim1/): frame-020 is settled (frames 007..040 differ only in the title glow / Touch to Next shimmer).
BUSINESS_REPORT = "dev_assets/private/business-native-20261003-frames/claim1/frame-020.png"
# B6f paged WORK REPORT after 一括完了 (live 2026-10-03 20:51, browser_tasks/20261003-205131-734283/frame-001.png, copied to
# bulk/report-paged-p2.png): once every page was shown, 「Touch to Next」 is gone and a pink 「次へ」 button sits bottom right.
BUSINESS_REPORT_PAGED = "dev_assets/private/business-native-20261003-frames/bulk/report-paged-p2.png"
# 2026-10-10 live bulk_default: after the paged report's 次へ and the 「ファンからのプレゼント」 page come 「獲得報酬」 (Exスキル
# list, OK / 再営業へ) and 「営業アイドル選択」 (three prefilled 12 h slots, 決定). Single native frames each.
BUSINESS_REWARD = "dev_assets/private/wing_samples/2026-10-10/20261010T121256455696Z-capture.png"
BUSINESS_SELECT = "dev_assets/private/wing_samples/2026-10-10/20261010T121541223722Z-capture.png"
# 2026-10-03 16:31 live 「トレーニング大成功」 result (browser_tasks/20261003-163400-704182/frame-001.png): big 「大成功!!」
# banner over the card-name row (x>=350) and a different title; the dialog frame, icon, Lv/stat rows and 閉じる are
# at the same place as the normal 「トレーニング成功」 dialog.
TRAINING_GREAT = "dev_assets/private/training-native-v6-results/daisuccess-20261003.png"
DIFF_PLAIN = "dev_assets/private/manual-capture-kept-20261002/shop-grade/066.png"
DIFF_MONEY2X = "dev_assets/private/manual-capture-kept-20261002/grade-difficulty-money2x-20261003.png"
DIFF_PROMPT_MID = "dev_assets/private/grade-run-07-normal-frames/frame-001.png"
CROPPED = {
    **{f"training_detail/{name}.png": ("dev_assets/private/sample-live-20261011/22-training-detail-settled.png", roi)
       for name, roi in (("bronze_zero_top", [939, 190, 62, 44]), ("plus_top", [1058, 184, 58, 52]),
                         ("silver_zero_top", [939, 322, 62, 44]), ("gold_zero_top", [939, 456, 62, 44]))},
    **{f"training_one/{name}.png": ("dev_assets/private/sample-live-20261011/23-training-bronze-one-selected.png", roi)
       for name, roi in (("bronze_one_top", [939, 190, 62, 44]), ("silver_zero_top", [939, 322, 62, 44]),
                         ("gold_zero_top", [939, 456, 62, 44]))},
    # Home ミッション button (dynamic-element rule): only the interior below the red count badge
    # (badge x 69..102, y 382..416 when present) and inside the white key (x 28..96, y 395..460): the lower clipboard and the
    # 「ミッション」 label. The old [25,394,76,70] crop contained the badge ("1"/"3") and the room background corners, so a
    # Home without unfinished missions scored 0.82 and was refused (live failure 2026-10-03 05:14).
    "home/mission_label.png": (HOME_NO_BADGE, [31, 418, 62, 36]),
    # B6 営業: only the 「営業」 glyphs at the bottom of the home 営業 button (x ~781..824, y ~672..690). The first crop
    # [778,668,50,26] kept two rows of the 「完了まで あと7:15」 timer pill's dark outline (y 668..669), so it only matched
    # while a business was running: 0.933 on the 2026-10-03 15:22 live Home (plain 営業 icon + red 「3」 badge; the
    # task stopped at the entry, no click) and <0.95 on 224 of 227 undimmed native Homes. y>=671 is below the pill and
    # above the button edge in both layouts (timer / icon), far from the badge corner (x>=828, y<=640).
    "home/business_label.png": (HOME_NO_BADGE, [778, 671, 50, 22]),
    # Shop プレミアム page, layout-independent anchor (batch 4, 2026-10-08): the 「特定商取引法に基づく表示」 legal-notice
    # button. It is on both the 2026-10-02 layout (新人P向け/ALL subtabs) and the 8.5th anniversary layout of 2026-10-07
    # (no subtabs, so the old NewbieSubtab anchor is gone): 1.000 on both, <= 0.39 on the マニー list, < 0.0 on Home.
    "shop_premium/legal_notice.png": ("dev_assets/private/in_app_browser/20261002-shop/shop-native-v1/Premium-005.png",
                                      [764, 166, 230, 40]),
    # 2026-10-22 live failure: the old copied anchor [20, 92, 128, 40] carried the lower edge of a 「NEW!!」 badge above the
    # tab (rows 0..3, x 65..127); with the badge gone (ジュエルSALE layout) it fell below 0.95 and the daily run timed out on
    # the shop. Rows 4.. are identical on both layouts (dev_assets/private/shop-premium-sale-20261022/native-001.png).
    # 2026-10-31 live failure: the copied Confirm-Start [660, 475, 159, 68] carried the lower edge of the 「超成功発生中！」
    # campaign bubble (rows 0..9); after the campaign it scored 0.74 and training stopped at the confirm dialog. The label
    # rows 485.. match the 10-01 bubble frame (0.9956) and the plain one (1.0).
    "training_confirm/start_label.png": ("dev_assets/private/training-confirm-plain-20261031/confirm-no-campaign.png",
                                         [660, 485, 159, 55]),
    "shop_premium/premiumselected.png": ("dev_assets/private/in_app_browser/20261002-shop/shop-native-v1/Premium-005.png",
                                         [20, 96, 128, 36]),
    # Training result of any kind (B6c): the title stem 「トレーニング」 (x 408..567; 成功/大成功/超成功 start at x>=573 and
    # are read by OCR) and the card-name prefix left of the 大成功 banner glow (x 159..348; 1.0 on both dialogs and >=0.9998
    # on the detail/confirm pages, so it is an identity anchor, not a stage anchor).
    "training_result/title_stem.png": (TRAINING_GREAT, [408, 190, 160, 35]),
    "training_result/card_name_short.png": (TRAINING_GREAT, [159, 96, 190, 31]),
    # Training detail of a card without the Lv.50 ticket row (2026-10-11 live, 【アイドルのたまご】大崎甜花): the Bronze label
    # is the first ticket row (y 139 instead of 270). Navigation-only variant (SC.Common.Variant.TrainingSelectedBronze):
    # BackOne/GoHome may leave any training detail; the spending Detail guard keeps its calibrated layout.
    "training_detail/bronze_label_top.png": ("dev_assets/private/sample-live-20261011/28-after-mission-navigation-refusal.png",
                                             [810, 139, 325, 34]),
    "mission_completed/ok.png": ("dev_assets/private/nav-v25-reward-ack2-20261002-frames/frame-023.png",
                                 [451, 540, 171, 81]),
    "fes_help/page.png": ("dev_assets/private/in_app_browser/20261002-grade/grade-native-v1/FesHelp-033.png",
                          [1107, 78, 107, 43]),
    # Title screen (B3 Startup), from a manual native capture. The whole TOUCH TO START button is kept so
    # the Now Loading band that covers its right end after the tap drops the score to 0.93 (< 0.95).
    "start_screen/touch_to_start.png": ("dev_assets/private/manual-capture-kept-20261002/touch-to-start-native-20261002.png",
                                        [430, 604, 425, 52]),
    # お知らせ header buttons (ピックアップコンテンツ / お問い合わせ) are identical on every tab, unlike the tab heading.
    "announcement/header_buttons.png": ("dev_assets/private/manual-capture-kept-20261002/announcement-maintenance-tab-20261002.png",
                                        [823, 16, 442, 60]),
    # Only the white interior of the BANDAI NAMCO bubble (black wordmark on opaque white): the old 218x58 crop also
    # contained the rotating title art around the bubble, so a new illustration dropped the score below 0.95.
    "start_screen/publisher.png": ("dev_assets/private/manual-capture-kept-20261002/touch-to-start-native-20261002.png",
                                   [1056, 30, 192, 24]),
    # 参加ユニット (GradeFes spend point, B5), measured on the live frame after the Normal tap (rights still 20/20):
    # icon + 「参加ユニット」 (the difficulty suffix is read by OCR), the 審査員別行動評価 header and the
    # テンション決定 label (the rights bar below it changes and is read by OCR).
    # Grade difficulty dialog (B5g): only the 「獲得スコア ×0.75/×1.00/×1.25」 glyph boxes inside each circle's lower band
    # (below and left of the campaign badge, no circle labels, no circle gloss above the band), once per layout, and the
    # text of the 「フェス参加権を 1 消費します」 bubble (without the bubble outline and tail) at three pulse phases.
    **{f"grade_difficulty/{name}.png": (frame, roi) for name, frame, roi in (
        ("score_easy", DIFF_PLAIN, [275, 421, 115, 60]), ("score_easy_shifted", DIFF_MONEY2X, [252, 421, 115, 60]),
        ("score_normal", DIFF_PLAIN, [581, 424, 115, 59]), ("score_normal_shifted", DIFF_MONEY2X, [558, 424, 115, 59]),
        ("score_hard", DIFF_PLAIN, [885, 424, 115, 59]), ("score_hard_shifted", DIFF_MONEY2X, [863, 424, 115, 59]),
        ("prompt_small", DIFF_PLAIN, [845, 576, 395, 36]), ("prompt_mid", DIFF_PROMPT_MID, [845, 576, 395, 36]),
        ("prompt_large", DIFF_MONEY2X, [845, 576, 395, 36]),
        # The pink 「1」 alone (mid phase; >= 0.99 at every phase): the full-text crop barely reacts to the digit.
        ("prompt_digit", DIFF_PROMPT_MID, [1040, 578, 50, 32]))},
    # フェスリハーサル difficulty dialog (batch 4): the 「フェスリハーサル」 part of the title (manual capture 060). >= 0.992 on the
    # rehearsal dialog frames 056-063 (incl. the circle-gloss pulse), <= 0.094 on every Grade difficulty dialog and the
    # mode page. The 「推奨ランク」 circle labels pulse with the gloss (0.88-0.95) and are not used.
    "grade_rehearsal/title.png": ("dev_assets/private/manual-capture-kept-20261002/shop-grade/060.png", [215, 92, 215, 40]),
    "grade_unit/title.png": ("dev_assets/private/grade-run-07c-normal-frames/frame-024.png", [6, 18, 196, 40]),
    "grade_unit/judges.png": ("dev_assets/private/grade-run-07c-normal-frames/frame-024.png", [525, 38, 110, 62]),
    "grade_unit/tension_label.png": ("dev_assets/private/grade-run-07c-normal-frames/frame-024.png", [1100, 628, 155, 34]),
    # TensionDecided (B5c), from the live frame 30 s after テンション決定 (rights still 20/20): the 「ライブ開始」 label
    # (same box as テンション決定; byte-stable over frames 002–070) and the no-return / 1-right note (also on 参加ユニット).
    "grade_tension/live_start_label.png": (TENSION_FRAME, [1100, 628, 155, 34]),
    "grade_tension/notice.png": (TENSION_FRAME, [594, 632, 492, 60]),
    # スキップ dialog of Match (B5b), from the native sample run (frame-005, all counters 0, rights 20/20). Only parts
    # that do not change with the counters: icon + title, 参加権回復 (never a target), 絞り込み, キャンセル and the
    # 参加権消費数 label (its "N / M" value is read by OCR). クリア and スキップする change state and are not anchors.
    **{f"match_skip/{name}.png": ("dev_assets/private/match-skip-native-20261002-frames/frame-005.png", roi)
       for name, roi in (("title", [48, 46, 170, 42]), ("recovery", [72, 133, 165, 40]), ("filter", [1020, 132, 166, 42]),
                         ("cancel", [380, 582, 166, 80]), ("cost_label", [1060, 588, 140, 28]))},
    # Grade Fes live turn screen (B5d): parts that stay on every turn frame (TURN label without the number, pause,
    # スキル履歴), the 「流行」 word of the Vo judge badge (waiting/target stage only, gone during the appeal animation
    # and the timing bar), 倍速OFF, the APPEAL timing bar label and 「スライドで交換」 under a selected card.
    "grade_live/turn_label.png": (LIVE_WAIT, [522, 15, 100, 30]),
    "grade_live/pause.png": (LIVE_WAIT, [1205, 12, 62, 60]),
    "grade_live/history.png": (LIVE_WAIT, [10, 655, 50, 45]),
    # 2026-10-22 official Grade, turns 5-6: the stage light tints the pause button and the スキル履歴 label pink (pause
    # 0.83-0.90, history 0.85-0.94 on run_logs/samples/grade-after-takeover-20261022T102524189481Z frames 041..105, so the
    # TURN screen guard flickered). Two tinted variants each, same boxes; the original purple ones stay.
    "grade_live/pause_pink.png": (LIVE_TINT + "takeover-frame-041.png", [1205, 12, 62, 60]),
    "grade_live/pause_pink_mid.png": (LIVE_TINT + "takeover-frame-060.png", [1205, 12, 62, 60]),
    "grade_live/history_pink.png": (LIVE_TINT + "takeover-frame-041.png", [10, 655, 50, 45]),
    "grade_live/history_pink_mid.png": (LIVE_TINT + "takeover-frame-062.png", [10, 655, 50, 45]),
    "grade_live/trend_word.png": (LIVE_WAIT, [16, 12, 32, 14]),
    "grade_live/speed_off.png": (LIVE_WAIT, [1110, 12, 64, 60]),
    "grade_live/swap_hint.png": (LIVE_TARGET, [800, 700, 100, 16]),
    "grade_live/appeal_label.png": (LIVE_TIMING, [392, 485, 140, 38]),
    "grade_live/memory_arrow.png": (LIVE_MEMORY_WAIT, [133, 399, 84, 82]),  # arrow at the Vi judge (x 133..216, y 400..480)
    "grade_report/title.png": (LIVE_REPORT, [430, 20, 415, 55]),
    "grade_report/ok.png": (LIVE_REPORT, [1110, 640, 135, 45]),
    "grade_report/score_label.png": (LIVE_REPORT, [100, 333, 128, 36]),
    # B5j alternatives from the auto-run report (title glow / OK sparkle phase differ by up to 49 levels) and two record-only
    # anchors (never guards): the 「獲得マニー 2倍」 badge interior and the 「ミッション報酬獲得」 ribbon text (no sparkles).
    "grade_report/title_auto.png": (LIVE_REPORT_AUTO, [430, 20, 415, 55]),
    "grade_report/ok_auto.png": (LIVE_REPORT_AUTO, [1110, 640, 135, 45]),
    "grade_report/score_label_auto.png": (LIVE_REPORT_AUTO, [100, 333, 128, 36]),
    "grade_report/money2x_badge.png": (LIVE_REPORT_AUTO, [66, 396, 34, 30]),
    "grade_report/mission_reward.png": (LIVE_REPORT_AUTO, [790, 558, 160, 26]),
    # B5k ranking page: the labels inside the row-1 「スコア詳細」/「ユニット詳細」 buttons (button frames x 893..1034 and
    # 1046..1188, y 203..239; static on all 8 ranking samples of the 11:09 run). The title / OK reuse the report templates.
    "grade_report/ranking_score_detail.png": (LIVE_RANKING, [900, 209, 126, 24]),
    "grade_report/ranking_unit_detail.png": (LIVE_RANKING, [1052, 209, 128, 24]),
    # 新ガシャ開催！ cut-in (shown when a new gacha pool launches), from the native frame 2026-10-03 04:51. The title crop
    # keeps the glyphs and leaves out the two twinkling sparkles (x<420, x>872); the buttons are the interiors inside their
    # dark 2 px outlines (全動画SKIP x 1082..1160, SKIP x 1182..1260, both y 18..96), without the animated stage flare.
    **{f"new_gacha/{name}.png": (NEW_GACHA, roi) for name, roi in (
        ("title", [420, 312, 452, 72]), ("skip_all", [1084, 20, 75, 75]), ("skip", [1184, 20, 75, 75]))},
    # B6b business top (営業), native sample run 2026-10-03 05:50 (frame-003; frames 003..011 differ only in the idol
    # figures and sparkles): the 「営業」 header glyphs (no icon), the 「トーク/コミュ」 button text, and per-slot anchors
    # cropped from slot 1 (slot k = +381 px in x; slots 2/3 score >= 0.983): the CLEAR! stamp and the 「営業完了」 label.
    # The Tea Time layout (smaller 営業完了 button, TeaTime cup instead of CLEAR) scores <= 0.37 on both slot anchors.
    **{f"business_top/{name}.png": (BUSINESS_TOP, roi) for name, roi in (
        ("title", [52, 24, 52, 28]), ("talk", [990, 31, 168, 22]), ("clear", [399, 97, 87, 56]),
        ("complete_label", [250, 514, 120, 38]))},
    # B6d WORK REPORT: the title glyphs (>= 0.996 on the 34 settled frames, mid-animation frame 006 too), the CLEAR! stamp
    # (1.000), the 総獲得Exp / 総獲得ファン人数 labels (1.000) and the 「Touch to Next」 glyphs (>= 0.996 once shown; absent while
    # the numbers count up, so the guard only holds on a settled report). Business top / Home / LIVE REPORT score <= 0.4.
    **{f"business_report/{name}.png": (BUSINESS_REPORT, roi) for name, roi in (
        ("title", [400, 18, 470, 52]), ("clear", [103, 124, 94, 56]), ("stat_labels", [700, 140, 190, 70]),
        ("touch_next", [455, 638, 365, 46]))},
    # B6f: the white 「次へ」 glyphs (x ~1152..1205, y ~651..673) of the paged WORK REPORT's last screen, without the
    # button sparkles; the 一括完了 label at the same place on the business top is a different glyph pair.
    "business_report/next_button.png": (BUSINESS_REPORT_PAGED, [1148, 648, 62, 28]),
    # 2026-10-10 獲得報酬 page (header glyphs, the pink Exスキル tab text, the OK glyphs, the 再営業へ glyphs) and the
    # 営業アイドル選択 page (header glyphs, 決定 glyphs). One native frame each: re-measure when more samples exist.
    **{f"business_reward/{name}.png": (BUSINESS_REWARD, roi) for name, roi in (
        ("title", [58, 40, 104, 32]), ("ex_tab", [48, 125, 125, 40]), ("ok", [955, 655, 62, 40]),
        ("redispatch", [1118, 655, 120, 40]))},
    **{f"business_select/{name}.png": (BUSINESS_SELECT, roi) for name, roi in (
        ("title", [52, 22, 200, 36]), ("decide", [1148, 643, 66, 40]))},
    # フェス再開 dialog (B5h), shown over the dimmed Fes main after a host restart while a live is in progress (native frame
    # 2026-10-03 06:22, rights 19/20 underneath): icon + title (without the corner sparkle), the two-line question, and
    # the labels of 「終了」 (blue, left; never a target: it forfeits the spent right) and 「再開」 (pink, right) without
    # the faint button sparkles.
    **{f"fes_resume/{name}.png": (FES_RESUME, roi) for name, roi in (
        ("title", [358, 193, 190, 45]), ("text", [490, 310, 282, 54]),
        ("end_label", [505, 453, 70, 35]), ("resume_label", [705, 453, 70, 35]))},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path):
    return str(PureWindowsPath(Path(path).resolve().relative_to(ROOT.resolve())))


def png_size(path):
    header = Path(path).read_bytes()[:24]
    require(header[:8] == PNG_MAGIC and header[12:16] == b"IHDR", f"不是PNG: {path}")
    return struct.unpack(">II", header[16:24])


def roi_ok(roi):
    return (isinstance(roi, list) and len(roi) == 4 and all(type(v) is int for v in roi)
            and roi[0] >= 0 and roi[1] >= 0 and roi[2] > 0 and roi[3] > 0
            and roi[0] + roi[2] <= CANVAS[0] and roi[1] + roi[3] <= CANVAS[1])


def pipeline_templates(pipeline_dir=PIPELINE_DIR):
    """{template name: [roi, ...]} for every native/ template referenced by the common Pipeline."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            template = node.get("template")
            # Maa accepts one template or a list of alternatives (best match wins); each must fit the ROI.
            for name in ([template] if isinstance(template, str) else template if isinstance(template, list) else []):
                if isinstance(name, str) and name.startswith(TEMPLATE_PREFIX):
                    found.setdefault(name[len(TEMPLATE_PREFIX):], []).append(node.get("roi"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(Path(pipeline_dir).rglob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def plan(root=ROOT):
    """Resolve every source file and verify sizes before writing anything."""
    root = Path(root).resolve()
    private = (root / "dev_assets" / "private").resolve()
    entries = []
    for name, (profile, filename, roi) in sorted(COPIED.items()):
        source = (root / SOURCES[profile] / filename).resolve()
        require(source.is_relative_to(private) and source.is_file(), f"缺少来源模板: {source}")
        require(roi_ok(roi), f"ROI无效: {name}")
        width, height = png_size(source)
        require(0 < width <= roi[2] and 0 < height <= roi[3], f"模板大于ROI: {name}")
        entries.append({"name": TEMPLATE_PREFIX + name, "kind": "copied", "source": source, "roi": roi,
                        "source_profile": SOURCES[profile]})
    for name, (frame, roi) in sorted(CROPPED.items()):
        source = (root / frame).resolve()
        require(source.is_relative_to(private) and source.is_file(), f"缺少来源帧: {source}")
        require(roi_ok(roi), f"ROI无效: {name}")
        require(png_size(source) == CANVAS, f"来源帧必须是1280x720: {source}")
        entries.append({"name": TEMPLATE_PREFIX + name, "kind": "cropped", "source": source, "roi": roi})
    return entries


def check_pipeline(entries, pipeline_dir=PIPELINE_DIR):
    """Every native template the Pipeline references must be built and fit inside its ROIs."""
    produced = {entry["name"][len(TEMPLATE_PREFIX):]: entry for entry in entries}
    problems = []
    for name, rois in sorted(pipeline_templates(pipeline_dir).items()):
        entry = produced.get(name)
        if entry is None:
            problems.append(f"Pipeline引用了未构建的模板: {name}")
            continue
        size = tuple(entry["roi"][2:]) if entry["kind"] == "cropped" else png_size(entry["source"])
        for roi in rois:
            if not roi_ok(roi) or size[0] > roi[2] or size[1] > roi[3]:
                problems.append(f"模板{name}不适合Pipeline ROI {roi}")
    return problems


def build(output=DEFAULT_OUTPUT, root=ROOT, pipeline_dir=PIPELINE_DIR):
    from PIL import Image

    root = Path(root).resolve()
    output = Path(output).resolve()
    require(output.is_relative_to((root / "dev_assets" / "private").resolve()), "输出目录必须位于dev_assets/private")
    entries = plan(root)
    problems = check_pipeline(entries, pipeline_dir)
    require(not problems, "；".join(problems))
    image_dir = output / "image"
    records = []
    for entry in entries:
        target = image_dir / entry["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(entry["source"]) as image:
            image.load()
            require(image.mode in ("RGB", "RGBA"), f"仅支持RGB/RGBA: {entry['source']}")
            if image.mode == "RGBA":
                require(image.getchannel("A").getextrema() == (255, 255), f"拒绝透明来源: {entry['source']}")
            rgb = image.convert("RGB")
            if entry["kind"] == "cropped":
                x, y, w, h = entry["roi"]
                rgb = rgb.crop((x, y, x + w, y + h))
            rgb.save(target, format="PNG")
        record = {"name": entry["name"], "kind": entry["kind"], "roi": entry["roi"],
                  "sha256": sha256(target), "size": list(png_size(target)),
                  "source": rel(entry["source"]) if entry["source"].is_relative_to(ROOT.resolve()) else str(entry["source"]),
                  "source_sha256": sha256(entry["source"])}
        records.append(record)
    manifest = {"version": 1, "canvas": list(CANVAS), "generated_by": "tools/build_native_resource.py",
                "template_prefix": TEMPLATE_PREFIX, "templates": records}
    (output / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(output=DEFAULT_OUTPUT):
    """Hash-check an existing bundle against its manifest; returns a list of problems."""
    output = Path(output)
    manifest_path = output / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"缺少清单: {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    for record in manifest.get("templates", []):
        path = output / "image" / record["name"]
        if not path.is_file():
            problems.append(f"缺少模板: {record['name']}")
        elif sha256(path) != record["sha256"]:
            problems.append(f"模板哈希不符: {record['name']}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="从私有原生识别档案生成 SC.Common 使用的私有模板资源包（离线）。")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true", help="只校验既有资源包的哈希，不写入")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            problems = verify(args.output)
            print(json.dumps({"passed": not problems, "problems": problems}, ensure_ascii=False))
            return 0 if not problems else 1
        manifest = build(args.output)
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"passed": True, "templates": len(manifest["templates"]),
                      "output": str(Path(args.output).resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
