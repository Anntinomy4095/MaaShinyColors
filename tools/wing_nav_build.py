#!/usr/bin/env python3
"""Generate assets/resource/base/pipeline/wing.json (W.I.N.G. navigation framework) from agent.core.wing_nav.

Recognition library: anchors/readers/screens that the 2026-10-05 demo recording added to the draft catalog
(``wing_draft.json`` keeps the original 174 nodes). Navigation: one handler per ``wing_nav.SCREENS`` rule, in
dispatch order, each with a positive screen guard, an Act node (guard + Agent decision), a success guard (After)
and explicit stop paths. Every node is ``enabled: false``; Act nodes use the Agent's ``SC.Wing.Act`` custom action,
which refuses all input while ``wing_nav.LIVE_INPUT_ENABLED`` is False.

    .venv\\Scripts\\python.exe tools\\wing_nav_build.py          # write wing.json
    .venv\\Scripts\\python.exe tools\\wing_nav_build.py --check  # exit 1 when the committed file differs

Offline only; never connects to a device and never sends input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import wing_nav as nav  # noqa: E402
from agent.core.wing_screen import PRIORITY as NAV_PRIORITY  # noqa: E402
from agent.core import wing_team_edit as wte  # noqa: E402
from agent.core import wing_sp_view as wsv  # noqa: E402

OUTPUT = ROOT / "assets" / "resource" / "base" / "pipeline" / "wing.json"
ACTION_TABLE = ROOT / "assets" / "input" / "pipeline_actions.json"
A = "SC.Wing.Anchor."
S = "SC.Wing.Screen."
PICK = "SC.Wing.Pick."
CLICK = "SC.Wing.Click."
REQUEUE = "SC.Wing.Requeue"
IS = "SC.Wing.Is."
HALTED = "SC.Wing.Halted"


def ocr(roi, expected, threshold=0.5, replace=None):
    node = {"recognition": "OCR", "roi": list(roi), "expected": list(expected), "threshold": threshold}
    if replace:
        node["replace"] = [list(p) for p in replace]
    node["enabled"] = False
    return node


def reader(roi):
    return {"recognition": "OCR", "roi": list(roi), "threshold": 0.3, "enabled": False}


# New anchors measured on the native 1280x720 demo frames.
# Only static header/button/label text; never portraits, numbers or animated badges.
ANCHORS = {
    "Loading.Text": ocr([800, 625, 480, 95], [r"Loading"]),
    "Title.Touch": ocr([430, 590, 420, 70], [r"TOUCH ?TO ?START"]),
    "Notice.Title": ocr([30, 8, 160, 50], [r"^お知らせ$"]),
    "Notice.Topics": ocr([20, 95, 170, 60], [r"トピックス"]),
    "Home.Business": ocr([760, 655, 90, 50], [r"^営業$"]),
    "Home.Idol": ocr([500, 655, 120, 50], [r"^アイドル$"]),
    "Mission.Title": ocr([380, 80, 290, 60], [r"ミッション達成"]),
    "Mission.Goto": ocr([650, 548, 190, 60], [r"ミッションへ"]),
    "ProduceSelect.TrainTab": ocr([835, 72, 180, 50], [r"育成(プ|ブ)ロデュース"]),
    "IdolPicker.Title": ocr([40, 8, 330, 52], [r"アイドル(編|網|通)成"]),
    "IdolPicker.Filter": ocr([760, 100, 160, 45], [r"絞り込み"]),
    "IdolPicker.ConfirmTitle": ocr([395, 215, 90, 55], [r"^確認$"]),
    "IdolPicker.ConfirmBody": ocr([370, 305, 540, 70], [r"外れてしまいます"]),
    "Header.Fans": ocr([700, 5, 150, 40], [r"ファン人数"]),
    "TimeCard.Clock": ocr([300, 250, 700, 200], [r"(AM|PM) ?\d{1,2}:\d{2}"]),
    "Promise.Broken": ocr([300, 500, 620, 120], [r"約束を守れなかった"]),
    "Commu.FastForwardOff": ocr([1068, 648, 92, 48], [r"^OFF$"]),
    "TotalEval.Title": ocr([100, 95, 450, 175], [r"(総|然|絵|縦)合評", r"合評価"]),  # #701 read 「然合評価」
    "TrueEnd.Badge": ocr([200, 15, 320, 90], [r"True ?End"]),
    # Second demo (2026-10-06): 大富豪 minigame pages, アイドルの応援 help,
    # support-idol detail, 発動中スキル popup, オプション, Produce item popup / use confirm / used, small skill-swap dialog.
    "Minigame.Title": ocr([30, 8, 200, 50], [r"^ミニゲーム$", r"^コース(選|遥|退)択$"]),
    "Minigame.Play": ocr([300, 250, 440, 120], [r"大富豪で遊"]),
    "Minigame.CommuPoint": ocr([110, 620, 370, 45], [r"コミュニケーションポイント上限"]),
    "Minigame.Rules": ocr([170, 65, 260, 60], [r"大富豪について"]),
    # はづき notice commu with a pink 「次へ」 button (live 2026-10-10 S4 end, 20261010T071804 capture: button
    # x 1078–1238 / y 625–695; the verified rank_next rect (1105,635,110,60) lies inside it).
    "Notice.NextButton": ocr([1075, 620, 165, 75], [r"^次へ$"]),
    # Setup page header / breadcrumb (プロデュース選択 > ユニット編成 > アイテム選択; recording 2026-10-11 #73/#79/#222):
    # a setup page caught mid-slide (#78: UnitSelect anchors not read yet) must wait, never fall to NoticeNext.
    "Setup.Header": ocr([40, 8, 1100, 55], [r"プロデュース選択|ユニット(編|網|通)成|アイテム(選択|準備)"]),
    "Minigame.Revolution": ocr([580, 15, 110, 60], [r"^革命$"]),
    "Minigame.Speed": ocr([1095, 25, 95, 60], [r"倍速|^通常$"]),
    # 2026-10-09c: taller — the second result page (コミュニケーションポイント, second demo #172) puts the title at y 92–170.
    # 2026-10-15 live (beginner course, all パス): 「ゲーム終了 結果」 ranking page between the last round and プレイ結果.
    "Minigame.GameEnd": ocr([440, 30, 560, 110], [r"ゲーム終了"]),
    "Minigame.TouchNext": ocr([920, 650, 300, 60], [r"TOUCH ?NEXT"]),
    "Minigame.Result": ocr([520, 15, 340, 170], [r"(プ|ブ)レイ結果"]),
    "Minigame.Menu": ocr([340, 95, 150, 50], [r"^メニュー$"]),
    "Minigame.MenuPoint": ocr([415, 420, 350, 45], [r"コミュニケーションポイント上限"]),
    "Minigame.Discard": ocr([385, 145, 150, 60], [r"破棄確認"]),
    "CheerHelp.Title": ocr([240, 60, 300, 55], [r"アイドルの応援とは"]),
    "SupportDetail.Title": ocr([40, 10, 280, 50], [r"サ(ポ|ボ)ートアイドル詳"]),
    "ActiveSkills.Title": ocr([290, 50, 260, 140], [r"発動中スキル"]),
    "ActiveSkills.Close": ocr([580, 495, 120, 135], [r"^閉じ?る$"]),
    "Options.Title": ocr([30, 8, 180, 50], [r"^オ(プ|ブ)ション$"]),
    "Options.Reset": ocr([880, 630, 190, 50], [r"初期設定に戻す"]),
    # Placeholder: the W.I.N.G. ファン数未達成・振り返り警告 popup is not sampled; this guard never matches
    # ("(?!)") until its frames arrive (replace roi/expected then; checklist).
    # 2026-10-14 live (wing_samples/2026-10-14/20261014T011509554503Z): fan-shortfall warning popup.
    "WingWarning.Title": ocr([420, 110, 480, 65], [r"ちょっと待って"]),
    "WingWarning.FanBody": ocr([190, 195, 900, 80], [r"ファン人数が足りず"]),
    "WingWarning.AuditionButton": ocr([560, 525, 160, 60], [r"オーディションへ"]),
    # 2026-10-14 live (wing_samples/2026-10-14/20261014T012659428696Z): review-before-audition warning popup.
    "WingWarning.ReviewBody": ocr([190, 195, 900, 60], [r"必ず振り返り"]),
    "WingWarning.ReviewButton": ocr([560, 525, 160, 60], [r"振り返りへ"]),
    # 2026-10-12 recording captures-20261011-164549Z: ノウハウブックマーク dialog over 獲得報酬 (#350–#355: title
    # (277,39), キャンセル (468,628), 確定 (704,624)) and the picker 並び替え dialog (#113–#161: title (406,125), 決定 (602,518)).
    # 2026-10-14 live 記者 event (dev_assets/private/wing_events/reporter-20261014): the two choice buttons
    # (「取材を受ける」 (141,222), 「取材を受けない」 (912,222)) and a menu-less commu text box: speaker label
    # 「プロデューサー」 (273,541) + text lines from (257,585).
    "Reporter.Accept": ocr([110, 200, 260, 80], [r"取材を受ける$"]),
    "Reporter.Refuse": ocr([880, 200, 300, 80], [r"取材を受けない"]),
    # Text lines must name the interview: a generic menu-less text box also matches transition frames of ordinary
    # commus (demo replays #303/#307/#350), so only the sampled 記者 lines qualify.
    "ReporterCommu.Speaker": ocr([262, 530, 270, 50], [r"^[ぁ-ヿ一-鿿]{2,}$"]),
    "ReporterCommu.Text": ocr([245, 575, 780, 100], [r"取材|記者|お話を伺|ご活躍を拝見|都合がつかない"]),
    "Bookmark.Title": ocr([260, 28, 320, 52], [r"ノウハウブックマーク"]),
    "Bookmark.Cancel": ocr([455, 615, 165, 62], [r"^キャンセル$"]),
    "PickerSort.Title": ocr([395, 115, 140, 52], [r"^並び替え$"]),
    "PickerSort.Decide": ocr([590, 505, 100, 70], [r"^決定$"]),
    "ItemPopup.Title": ocr([395, 55, 350, 60], [r"^(プ|ブ)ロデュースアイテム$"]),
    "ItemPopup.Uses": ocr([410, 420, 370, 40], [r"使用可能数"]),
    "ItemUse.Title": ocr([395, 75, 160, 55], [r"^使用確認$"]),
    "ItemUse.Body": ocr([440, 150, 400, 45], [r"アイテムを使用しますか"]),
    "ItemUsed.Title": ocr([395, 75, 420, 55], [r"アイテム使用$"]),
    "ItemUsed.Body": ocr([440, 150, 400, 45], [r"使用しました"]),
    "ReplaceDialog.Title": ocr([440, 120, 260, 45], [r"スキル入れ替え確認"]),
    "ReplaceDialog.Warning": ocr([470, 470, 340, 40], [r"再度習得できません"]),
    # End-of-Produce destructive / storage / paid dialog (agent/core/produce_end.DESTRUCTIVE_MARKERS; no native sample
    # yet): marker words in the modal body plus a modal button. Ranked before Evaluation/Reward/FesDetail -> stop.
    "EndDialog.Marker": ocr([280, 110, 720, 420], [r"売却|移籍|削除|再抽選|所持上限|所持枠|枠拡張|ジュエル"]),
    "EndDialog.Button": ocr([380, 380, 520, 320], [r"キャンセル|^OK$|^はい$|^いいえ$|^閉じ?る$"]),
    # Third demo (2026-10-06): home アイドル一覧, the Hub's ホームに戻る・諦める
    # dialog, team-edit dialogs on ユニット編成, popups over フェスアイドル詳細, the 獲得報酬 page variants (book selected /
    # Ex tab) and its 売却 mode (売却する sits exactly where OK is).
    "IdolList.Title": ocr([30, 8, 250, 50], [r"^アイ(ド|ト)ル(一|ー|-)"]),
    "IdolList.KnowhowTab": ocr([15, 515, 175, 60], [r"ノウハウブック"]),
    # 2026-11-01 say "Halo" dialog: title at y 193-225 and body line 3 at y 333-360, so both ROIs reach lower.
    "Abandon.Title": ocr([380, 165, 520, 70], [r"ホームに戻る.?諦める"]),
    "Abandon.Body": ocr([360, 300, 560, 75], [r"フェスアイドルを誕生させ"]),   # Halo line 3 ends at y 360
    # say "Halo" (live H0 frames 2026-11-01): the Hub (goal bar, チーム情報, 諦める button); the post-Produce はづき line
    # follows any Produce end (W.I.N.G. third demo #744 too).
    "HaloHub.Goal": ocr([190, 88, 420, 42], [r"^(プ|ブ)ロデュ.ス目標"]),
    "HaloHub.Team": ocr([490, 570, 230, 90], [r"チ.ム情"]),     # H1: 「チ一ム情報」 / 「チーム情报」
    "HaloHub.GiveUp": ocr([1165, 135, 85, 40], [r"^諦める$"]),
    # say "Halo" H1 frames (2026-11-01; halo-samples manifest). The three schedule tabs
    # share W.I.N.G.'s スケジュール選択 title, 決定, lesson-room names and トラブル率, so each keeps one Halo-only label
    # (lesson: the 「アドバイス確認」 footer button; its x moves with the season's footer). Maa ROI OCR reads ー as 一.
    "HaloLesson.Advice": ocr([500, 620, 580, 55], [r"^アドバイス"]),
    "HaloAudition.Genre": ocr([970, 95, 200, 55], [r"有利ジャンル"]),
    "HaloPractice.Title": ocr([360, 90, 240, 60], [r"チ.ム合同練習"]),
    "HaloPractice.Fame": ocr([460, 145, 145, 45], [r"チ.ム知名度"]),
    "HaloRecruit.Title": ocr([35, 10, 100, 50], [r"^勧誘$"]),
    "HaloRecruit.Join": ocr([15, 620, 115, 55], [r"チ.ム加入"]),
    "HaloRecruitConfirm.Title": ocr([390, 215, 150, 60], [r"勧誘確認"]),
    "HaloRecruitConfirm.Body": ocr([450, 330, 380, 55], [r"勧誘しますか"]),
    "HaloTeamFull.Title": ocr([395, 180, 275, 55], [r"メンバ.上限"]),
    "HaloTeamFull.Close": ocr([570, 440, 140, 70], [r"^閉じ?る$"]),
    "HaloGrowth.Title": ocr([35, 10, 100, 50], [r"^成長$"]),
    "HaloGrowth.Status": ocr([75, 105, 140, 50], [r"ステ.タス"]),
    "HaloGrowth.Learn": ocr([1095, 625, 165, 75], [r"習得"]),
    "HaloGrowthConfirm.Title": ocr([180, 25, 150, 55], [r"成長確認"]),
    "HaloGrowthConfirm.Learn": ocr([655, 600, 165, 65], [r"習得"]),
    "HaloGoals.Title": ocr([185, 25, 245, 45], [r"^(プ|ブ)ロデュ.ス目標$"]),
    "HaloSeasonEnd.Text": ocr([250, 580, 700, 50], [r"シーズン.{1,3}終了"]),   # W.I.N.G.: 「シーズン終了です」
    "HaloReport.Title": ocr([300, 0, 680, 90], [r"REPORT"]),
    "HaloLiveSelect.Title": ocr([140, 25, 270, 50], [r"目標ライブ挑戦確認"]),
    "HaloLiveConfirm.Title": ocr([140, 22, 145, 50], [r"^挑戦確認$"]),
    "HaloLive.Challenge": ocr([655, 600, 170, 65], [r"挑戦する"]),
    # 「スキルが変更可能です / スキルを変更せずに…誕生させますか?」 over フェスアイドル誕生 (H1 with SP 324 left): it read as
    # the FesBirth page under it. Title [418,183,302,35], body line 2 [533,322,211,31].
    "HaloTeamInfo.Title": ocr([35, 10, 190, 50], [r"^チ.ム情"]),
    "HaloTeamInfo.Completion": ocr([400, 80, 370, 50], [r"完成度"]),
    # S2 disband pages (live 2026-11-15)
    "HaloTeamEdit.Title": ocr([35, 10, 190, 50], [r"^チ.ム編集$"]),
    "HaloTeamEdit.Message": ocr([270, 80, 730, 45], [r"離脱"]),
    "HaloLeaveConfirm.Title": ocr([390, 165, 230, 50], [r"離脱確認"]),
    "HaloLeaveConfirm.Question": ocr([420, 275, 440, 50], [r"離脱させますか"]),
    # say "Halo" start (H2c batch 2): ノウハウブック選択 (H1 frames: title [54,22,222,28], おまかせ編成 [548,640,144,28]);
    # its 設定履歴 page and プリセット dialog (weekly Auto frames 2026-10-29, the same widgets as weekly_auto's anchors).
    # 継承一覧 after the S1 end (H1 one frame 20261101T100723): the book's knowhow list (「Lv.N」 column), the stat bar
    # (「D/ 328」) and the Halo header; スキル一括変更 at the end (H1 20261101T115518; Maa never opens it).
    "HaloInheritance.Levels": ocr([730, 215, 90, 290], [r"^Lv\.?[1-5]$"]),
    # H2d live 2026-11-06: ROI OCR can merge the row (「V D/ 328 Da E/ 173 vi」): no end anchor
    "HaloInheritance.Stats": ocr([220, 590, 300, 45], [r"/\s*\d{2,4}"]),
    "HaloInheritance.Season": ocr([320, 3, 140, 40], [r"シ.ズン"]),
    "SkillBulk.Title": ocr([420, 18, 440, 48], [r"スキルを一括で変更"]),
    "SkillBulk.Apply": ocr([585, 548, 170, 35], [r"変更を反映"]),
    "BookSelect.Title": ocr([38, 10, 330, 52], [r"^ノウハウブック選択$"]),
    "BookSelect.Auto": ocr([535, 630, 180, 50], [r"おまかせ"]),
    # 带书「ノウハウ页指定」 picker (weekly Auto 10-30: same title as the book page, 「選択数」 tells them apart).
    "BookPicker.Count": ocr([545, 100, 95, 45], [r"^選択数"]),
    "BookHistory.Title": ocr([38, 10, 400, 52], [r"^ノウハウブック設定履"]),
    "BookHistory.Call": ocr([1000, 150, 140, 55], [r"^呼び出し$"]),
    "BookPreset.Title": ocr([355, 25, 340, 45], [r"^ノウハウブックプリセット$"]),
    "BookPreset.Close": ocr([560, 605, 165, 70], [r"^閉じる$"]),
    "FesBirth.SkillTitle": ocr([400, 170, 340, 60], [r"フェスアイドル誕生確認"]),
    "FesBirth.SkillBody": ocr([440, 310, 400, 50], [r"スキルを変更せず"]),
    "EndCommu.Speaker": ocr([265, 528, 125, 50], [r"^はづき$"]),
    "EndCommu.Text": ocr([250, 580, 520, 45], [r"プロデュースお疲れ様"]),
    "UnitSwitch.Title": ocr([140, 0, 450, 70], [r"^ユニット(切り替え|コピー)$"]),
    "UnitDialog.Cancel": ocr([520, 590, 240, 110], [r"キャンセル"]),
    "UnitCopyConfirm.Title": ocr([390, 165, 300, 50], [r"ユニットコピー確認"]),
    "UnitCopyDone.Title": ocr([380, 220, 300, 50], [r"ユニットコピー完"]),
    "AutoForm.Title": ocr([290, 70, 200, 45], [r"^おまかせ編成$"]),
    "AutoForm.Body": ocr([380, 155, 600, 60], [r"編成の方針|編成で設定しました"]),
    "FesMark.Title": ocr([220, 0, 360, 80], [r"フェスアイドルマーク"]),
    "DeckDetail.Title": ocr([180, 25, 440, 45], [r"デッキアイドル詳"]),
    "Reward.OK": ocr([1095, 615, 165, 70], [r"^OK$"]),
    "Reward.ExTab": ocr([20, 90, 170, 55], [r"^Exスキル$"]),
    "Reward.BookSelected": ocr([380, 210, 120, 45], [r"^ノウハウ$"]),
    "Reward.SellButton": ocr([1095, 615, 165, 70], [r"売却する"]),
    "Reward.SellCount": ocr([705, 590, 200, 45], [r"ノウハウブック数"]),
    # 再抽選 confirmation over 獲得報酬 (2026-10-10, NO native sample): a modal whose upper half names 再抽選 and whose
    # body states the stamina cost. Guessed text areas; a miss falls through to EndDestructiveDialog (stop).
    "RewardReroll.Title": ocr([380, 90, 520, 200], [r"再抽.?選"]),
    "RewardReroll.Cost": ocr([380, 200, 520, 300], [r"スタミナ.*30|30.*スタミナ"]),
    # Batch c (2026-10-09c): the home Pデスク/ミニゲーム popup (second demo #1: Pデスク
    # x 545–680 / ミニゲーム x 690–828, y 528–600).
    "HomePopup.Minigame": ocr([680, 525, 160, 70], [r"^ミニゲーム$"]),
    "HomePopup.PDesk": ocr([535, 525, 160, 70], [r"^(P|Ｐ)デスク$"]),
    # プロデュース再開 (2026-10-10, wing-live-20261009/sample-produce-resume-dialog.png: title [409,39,219,32], question
    # [477,127,316,26]); the dialog covers the home bar, so the Home anchors (営業 / アイドル) are not read under it.
    "Resume.Title": ocr([395, 30, 260, 50], [r"(プ|ブ)ロデュース再開"]),
    "Resume.Question": ocr([420, 85, 440, 75], [r"再開しますか"]),
}

READS = {
    # say "Halo" (H2b, agent/core/halo_screen.py; H1 frames 2026-11-01): header advice bar (nine 「xN」), 親愛度
    # 「Lv.N NEXTあと M」, the Hub's main button, the carousel's centre card (成功率 / 練習効率, name, 不足・代用 note), the
    # selected audition's fan bubble, the three 目標ライブ 成功率 and チーム情報 「チーム完成度：N%」.
    "Halo.Advice": reader([620, 40, 325, 38]),
    "Halo.Affection": reader([1090, 30, 180, 45]),
    "Halo.HubButton": reader([0, 600, 330, 110]),
    "Halo.CardRate": reader([740, 290, 160, 80]),
    "Halo.CardName": reader([640, 195, 250, 90]),
    "Halo.CardNote": reader([640, 425, 260, 50]),
    "Halo.CardFans": reader([1070, 495, 180, 55]),
    "Halo.Fans": reader([1090, 555, 165, 45]),        # fan total 「114,204」 right of the people icon (H2d 2026-11-06)
    "Halo.LiveRates": reader([985, 140, 135, 450]),
    "Halo.LiveSelected": reader([180, 110, 100, 470]),
    "Halo.Completion": reader([400, 80, 370, 50]),
    # 勧誘 (H2d live 2026-11-05): the selected candidate's header badge 「勧誘済」 (already in the team; チームに勧誘 greyed)
    "Halo.RecruitStatus": reader([1010, 115, 175, 60]),
    "Halo.RecruitName": reader([460, 125, 560, 50]),
    "Halo.RecruitTiles": reader([445, 620, 755, 45]),      # 勧誘済 labels under the five candidate tiles
    # Setup (H2c batch 2): scenario caption under the stage picture (H1 「say "Halo"編」 [208,365,172,40]), 次へ, the
    # book page (prompt, slot 1 bonuses, the two locked slots, footer buttons), 設定履歴 top set, プリセット rows.
    "Halo.Caption": reader([150, 355, 300, 55]),
    "Halo.SetupNext": reader([1100, 625, 160, 75]),
    "Halo.BookPrompt": reader([560, 440, 680, 80]),
    "Halo.BookSlot": reader([540, 515, 225, 85]),
    "Halo.BookLocked": reader([775, 440, 480, 75]),
    "Halo.BookFooter": reader([530, 625, 740, 75]),
    "Halo.BookTop": reader([20, 150, 180, 50]),
    "Halo.PickerHeader": reader([960, 95, 285, 50]),
    "Halo.LeaveButtons": reader([330, 515, 880, 60]),
    "Halo.LeaveQuestion": reader([420, 275, 440, 50]),
    "Halo.PickerTiles": reader([535, 180, 690, 420]),
    "Halo.PickerDetail": reader([140, 88, 320, 120]),
    # 成長 (H2c batch 3, agent/core/halo_growth.py): the five points left, the four rows (labels, 「N回」, costs), the
    # projected stat / cap bar.
    "Halo.GrowthTop": reader([620, 35, 640, 45]),
    "Halo.GrowthRows": reader([50, 180, 1160, 430]),
    "Halo.GrowthBottom": reader([120, 625, 720, 70]),
    "Halo.HistoryTop": reader([940, 95, 330, 115]),
    "Halo.PresetRows": reader([325, 90, 560, 370]),
    "Abandon.Hold": reader([470, 375, 360, 40]),         # 「「諦める」は長押しで選択できます」
    "Abandon.Button": reader([740, 445, 165, 60]),       # 「諦める」 button label
    "Schedule.StatusLesson": reader([380, 375, 900, 45]),
    "Schedule.StatusBusy": reader([270, 595, 850, 45]),
    "Schedule.Rooms": reader([270, 480, 990, 60]),
    "Schedule.RoomLevels": reader([270, 400, 990, 100]),
    "Schedule.Promise": reader([260, 95, 1000, 110]),
    "Audition.Status": reader([350, 545, 850, 45]),
    "Audition.Panel": reader([740, 95, 530, 450]),
    "Audition.Recommended": reader([400, 390, 760, 50]),
    "Live.State": reader([1000, 30, 190, 50]),
    "Live.Full": reader([0, 0, 1280, 720]),
    "Schedule.RoomBand": reader([260, 390, 1010, 160]),
    "Review.Info": reader([760, 530, 510, 140]),
    "Review.Full": reader([0, 0, 1280, 720]),       # skill-node 「SP: N」 labels (third demo)
    # SP learning (2026-10-09b): info-bar name/description of the selected node, the
    # スキル習得確認 body (cost, SP before ▶ after), the four 忘れるスキル rows and the スキル入れ替え確認 names.
    "Review.InfoText": reader([575, 555, 500, 145]),
    "LearnConfirm.Body": reader([360, 130, 570, 330]),
    "ForgetSelect.Rows": reader([300, 60, 680, 560]),
    # 2026-10-09c: taller (to y 640) so the dialog's OK / キャンセル boxes are read (their height follows the text).
    "ReplaceDialog.Body": reader([440, 170, 400, 470]),
    "End.Full": reader([0, 0, 1280, 720]),
    # 獲得報酬 「再抽選 1/1」 + 「消費スタミナ：30」 (ROI OCR reads 「1/1」, whole-frame OCR does not; live 2026-10-10).
    "Reward.Reroll": reader([915, 625, 165, 75]),
    "FesBirth.SP": reader([330, 85, 80, 40]),
    "Tutorial.Page": reader([1110, 65, 120, 65]),
    "UnitSelect.Counter": reader([55, 145, 85, 45]),
    "UnitSelect.Tabs": reader([15, 80, 1135, 42]),
    # 2026-10-12 (recording captures-20261011-164549Z, second demo #559): picker title for the team edit, and the
    # 発動中スキル list (section headers / skill names / Lv) for the support identities.
    "IdolPicker.Title": reader([40, 8, 330, 52]),
    # 2026-10-14 picker sort (agent/core/picker_sort.py): header boxes shared with the training list
    # (training_select FILTER_BOX / SORT_BOX / DIRECTION_BOX) and the 並び替え popup (SORT_POPUP_ROI).
    "IdolPicker.SortKey": reader([968, 108, 168, 44]),
    "IdolPicker.Direction": reader([1144, 108, 100, 44]),
    "IdolPicker.FilterText": reader([760, 108, 170, 44]),
    "PickerSort.Popup": reader([350, 105, 582, 505]),
    "ActiveSkills.List": reader([265, 125, 750, 425]),
    "Options.List": reader([455, 95, 400, 510]),          # プロデュース設定 row labels (wing_options.LIST_ROI)
    "Options.Dialog": reader([455, 150, 440, 420]),       # dialog text over the page (wing_options.DIALOG_ROI)
    # Start flow: item slots of アイテム準備, 使用確認 body (layout) and stamina/倍率 panel (both layouts).
    "ItemSelect.Slots": reader([385, 110, 205, 190]),
    "StartConfirm.Body": reader([360, 170, 580, 130]),
    "StartConfirm.Panel": reader([380, 320, 520, 200]),
    # Batch c: home bottom bar / popup labels, プロデュース選択 tabs and the アイドルの応援 banner (second demo #195:
    # 「アイドルの応援発生中！あと1/4回」 at (44,296)), 大富豪 course header, rules page (「114」 = 1/4 at (1126,72)), the
    # in-game turn bar (パス (482,447) / キャンセル / 出す), result buttons, full-screen スキル入れ替え確認 names.
    "Home.Bar": reader([380, 600, 900, 110]),
    "Home.Popup": reader([530, 520, 320, 90]),
    "ProduceSelect.Tabs": reader([830, 70, 350, 55]),
    "ProduceSelect.Cheer": reader([25, 280, 350, 40]),
    "Minigame.CourseHead": reader([100, 80, 1100, 45]),
    "Minigame.RulesPage": reader([1095, 55, 80, 50]),
    "Minigame.Bar": reader([450, 420, 360, 70]),
    "Minigame.ResultButtons": reader([280, 480, 720, 220]),
    "ReplaceConfirm.Body": reader([400, 140, 500, 240]),
    # Mid-Produce detection: Hub bubble speaker line (「灯織」 at (837,342), the bubble's message lines are above it) and
    # the アイドルの応援発生中！ banner (49,427).
    "Hub.Speaker": reader([825, 335, 160, 40]),
    "Hub.Cheer": reader([30, 415, 270, 45]),
    # プロデュース再開 dialog: card line / stats / scenario / season / items, and the three buttons.
    "Resume.Panel": reader([380, 165, 520, 410]),
    "Resume.Buttons": reader([370, 588, 545, 85]),
}

SCREENS = {
    "Loading": ["Loading.Text"],
    "TitleScreen": ["Title.Touch"],
    "Notice": ["Notice.Title", "Notice.Topics"],
    "Home": ["Home.Business", "Home.Idol"],
    "MissionPopup": ["Mission.Title", "Mission.Goto"],
    "ProduceSelectOther": ["ProduceSelect.Title", "ProduceSelect.TrainTab"],
    "IdolPicker": ["IdolPicker.Title", "IdolPicker.Filter"],
    "IdolPickerConfirm": ["IdolPicker.ConfirmTitle", "IdolPicker.ConfirmBody"],
    "HubBanner": ["Hub.Title", "Hub.Stamina", "Hub.SeasonLeftBanner"],
    "ScheduleBusy": ["Schedule.Title", "Header.Fans"],
    "TimeCard": ["TimeCard.Clock"],
    "PromiseBroken": ["Promise.Broken"],
    "CommuSkip": ["Commu.FastForward", "Commu.Skip"],
    "CommuFastForwardOff": ["Commu.FastForwardOff"],
    "TotalEval": ["TotalEval.Title"],
    "TrueEndCommu": ["TrueEnd.Badge", "Commu.FastForward"],
    # 大富豪 pages, one screen each (batch c; formerly one stop screen with 7 variants).
    "MinigameMenu": {"any": [["Minigame.Menu", "Minigame.MenuPoint"], ["Minigame.Menu", "Minigame.Discard"]]},
    "MinigameGameEnd": ["Minigame.GameEnd", "Minigame.TouchNext"],
    "MinigameResult": ["Minigame.Result"],
    "NoticeNext": ["Notice.NextButton"],
    "SetupTransition": ["Setup.Header"],
    "MinigameRules": ["Minigame.Rules", "Popup.Close"],
    "MinigameGame": ["Minigame.Revolution", "Minigame.Speed"],
    "MinigameTop": ["Minigame.Title", "Minigame.Play"],
    "MinigameCourse": ["Minigame.Title", "Minigame.CommuPoint"],
    "HomeMinigamePopup": ["HomePopup.Minigame", "HomePopup.PDesk"],
    "ResumeDialog": ["Resume.Title", "Resume.Question"],
    "CheerHelp": ["CheerHelp.Title", "Popup.Close"],
    "SupportDetail": ["SupportDetail.Title", "IdolDetail.ExTab"],
    "ActiveSkills": ["ActiveSkills.Title", "ActiveSkills.Close"],
    "Options": ["Options.Title", "Options.Reset"],
    "WingWarningPopup": ["WingWarning.Title", "WingWarning.FanBody", "WingWarning.AuditionButton"],
    "WingReviewWarning": ["WingWarning.Title", "WingWarning.ReviewBody", "WingWarning.ReviewButton"],
    "ReporterChoice": ["Reporter.Accept", "Reporter.Refuse"],
    "ReporterCommu": ["ReporterCommu.Speaker", "ReporterCommu.Text"],
    "BookmarkDialog": ["Bookmark.Title", "Bookmark.Cancel"],
    "PickerSort": ["PickerSort.Title", "PickerSort.Decide", "IdolPicker.Title"],
    "ItemPopup": ["ItemPopup.Title", "ItemPopup.Uses"],
    "ItemUseConfirm": ["ItemUse.Title", "ItemUse.Body"],
    "ItemUsed": ["ItemUsed.Title", "ItemUsed.Body"],
    "ReplaceDialog": ["ReplaceDialog.Title", "ReplaceDialog.Warning"],
    "EndDestructiveDialog": ["EndDialog.Marker", "EndDialog.Button"],
    "IdolList": ["IdolList.Title", "IdolList.KnowhowTab"],
    "AbandonDialog": ["Abandon.Title", "Abandon.Body"],
    "HaloHub": ["HaloHub.Goal", "HaloHub.Team", "HaloHub.GiveUp"],
    "HaloLesson": ["Schedule.Title", "HaloLesson.Advice"],
    "HaloAudition": ["Schedule.Title", "HaloAudition.Genre"],
    "HaloPractice": ["HaloPractice.Title", "HaloPractice.Fame"],
    "HaloRecruit": ["HaloRecruit.Title", "HaloRecruit.Join"],
    "HaloRecruitConfirm": ["HaloRecruitConfirm.Title", "HaloRecruitConfirm.Body"],
    "HaloTeamFull": ["HaloTeamFull.Title", "HaloTeamFull.Close"],
    "HaloGrowth": ["HaloGrowth.Title", "HaloGrowth.Status", "HaloGrowth.Learn"],
    "HaloGrowthConfirm": ["HaloGrowthConfirm.Title", "HaloGrowthConfirm.Learn"],
    "HaloGoals": ["HaloGoals.Title", "Popup.Close"],
    "HaloSeasonEnd": ["EndCommu.Speaker", "HaloSeasonEnd.Text", "Commu.OK"],
    "HaloReport": ["HaloReport.Title", "Footer.Next"],
    "HaloLiveSelect": ["HaloLiveSelect.Title", "HaloLive.Challenge"],
    "HaloLiveConfirm": ["HaloLiveConfirm.Title", "HaloLive.Challenge"],
    "HaloTeamInfo": ["HaloTeamInfo.Title", "HaloTeamInfo.Completion"],
    "HaloLeaveConfirm": ["HaloLeaveConfirm.Title", "HaloLeaveConfirm.Question"],
    "HaloTeamEdit": ["HaloTeamEdit.Title", "HaloTeamEdit.Message"],
    "HaloInheritance": ["HaloInheritance.Levels", "HaloInheritance.Stats", "HaloInheritance.Season"],
    "SkillBulkChange": ["SkillBulk.Title", "SkillBulk.Apply"],
    "BookPreset": ["BookPreset.Title", "BookPreset.Close"],
    "BookHistory": ["BookHistory.Title", "BookHistory.Call"],
    "BookPicker": ["BookSelect.Title", "BookPicker.Count"],
    "BookSelect": ["BookSelect.Title", "BookSelect.Auto"],
    "FesBirthSkillDialog": ["FesBirth.SkillTitle", "FesBirth.SkillBody"],
    "ProduceEndCommu": ["EndCommu.Speaker", "EndCommu.Text"],
    "UnitEditDialog": {"any": [["UnitSwitch.Title", "UnitDialog.Cancel"], ["UnitCopyConfirm.Title"],
                               ["UnitCopyDone.Title"], ["AutoForm.Title", "AutoForm.Body"]]},
    "FesDetailPopup": {"any": [["FesMark.Title"], ["DeckDetail.Title"]]},
    "RewardSellMode": ["Reward.Title", "Reward.SellButton", "Reward.SellCount"],
    "RewardRerollConfirm": ["RewardReroll.Title", "RewardReroll.Cost", "EndDialog.Button"],
    "RewardDetail": {"any": [["Reward.Title", "Reward.StaminaLabel", "Reward.BookSelected", "Reward.OK"],
                             ["Reward.Title", "Reward.ExTab", "Reward.OK"]]},
}


def custom_reco(name, **param):
    return {"recognition": "Custom", "custom_recognition": name, "custom_recognition_param": param, "enabled": False}


def custom_act(name, recognition="DirectHit", **param):
    return {"recognition": recognition, "action": "Custom", "custom_action": name, "custom_action_param": param,
            "enabled": False}


# Handler order of SC.Wing.Nav.Loop: screens by how often a round opened on them (live 2026-10-27/28 runs).
HOT_SCREENS = ("Review", "Live", "Schedule", "Hub", "ScheduleBusy", "OnAir", "Loading", "CommuSkip", "Commu",
               "LearnConfirm", "AuditionTab", "Choice", "RankPyramid", "ResultJudge", "CommuFastForwardOff",
               "PassCommu", "Owned", "ResultList", "WeekCard", "AuditionPass", "RankUpCommu", "TimeCard")

def build() -> dict:
    out: dict = {}
    for name, node in ANCHORS.items():
        out[A + name] = node
    for name, node in READS.items():
        out["SC.Wing.Read." + name] = node
    for name, refs in SCREENS.items():
        if isinstance(refs, dict):
            # Several layouts of one screen (e.g. the 大富豪 minigame pages): Or of per-layout And guards.
            variants = []
            for i, group in enumerate(refs["any"], 1):
                variant = f"SC.Wing.Variant.{name}.{i}"
                out[variant] = {"recognition": "And", "all_of": [A + r for r in group], "enabled": False}
                variants.append(variant)
            out[S + name] = {"recognition": "Or", "any_of": variants, "enabled": False}
        else:
            out[S + name] = {"recognition": "And", "all_of": [A + r for r in refs], "enabled": False}

    out["SC.Wing.State.Active"] = custom_reco("SC.Wing.State", want="active")
    out["SC.Wing.State.Progress"] = custom_reco("SC.Wing.State", want="progress")
    # Last guard of every Click: waits out the rest of the page-settle time (wing_custom.SETTLE_S) since the screen
    # was first classified, instead of a fixed pre_delay after the decision.
    out["SC.Wing.State.Settled"] = custom_reco("SC.Wing.State", want="settled")
    out["SC.Wing.Nav.Start"] = {"recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Wing.Begin",
                                "custom_action_param": {}, "next": ["SC.Wing.Nav.Loop"],
                                "on_error": ["SC.Wing.Stop.Begin"], "enabled": False}
    # Speed (2026-10-09c): Maa's default pre_delay/post_delay (200 ms each) on every pass-through node cost ~1.2 s per
    # click cycle (Loop, On, After); they only wait, never guard, so they are 0 (the Click keeps its post_delay).
    # Speed: the loop asks the most frequent screens first (wing_screen.PRIORITY keeps the classification
    # order); every handler hits only for the classified screen, so the order saves Agent calls, not decisions.
    priority = [r.screen for r in nav.SCREENS]
    hot = [s for s in HOT_SCREENS if s in priority]
    out[NAV_PRIORITY] = {"recognition": "DirectHit", "action": "DoNothing",
                         "next": ["SC.Wing.On." + s for s in priority], "enabled": False}
    out["SC.Wing.Nav.Loop"] = {"recognition": "DirectHit", "action": "DoNothing", "pre_delay": 0, "post_delay": 0,
                               "next": ["SC.Wing.On." + s for s in hot + [s for s in priority if s not in hot]]
                               + [HALTED],
                               "timeout": 30000, "on_error": ["SC.Wing.Stop.Lost"], "enabled": False}
    for reason in ("Begin", "Lost", "ActRefused", "Uncertain", "NoDecision", "ClickUnconfirmed"):
        out["SC.Wing.Stop." + reason] = custom_act("SC.Wing.Stop", reason=reason[0].lower() + reason[1:])
    # The screen vanished between the handler and the click frame: nothing was clicked, classify again (bounded by the
    # Agent's consecutive-requeue limit). A stopped runtime ends the task at once instead of polling to the timeout.
    out[REQUEUE] = {"recognition": "Custom", "custom_recognition": "SC.Wing.State",
                    "custom_recognition_param": {"want": "requeue"}, "action": "DoNothing", "post_delay": 300,
                    "next": ["SC.Wing.Nav.Loop"], "enabled": False}
    out[HALTED] = {"recognition": "Custom", "custom_recognition": "SC.Wing.State",
                   "custom_recognition_param": {"want": "halted"}, "action": "Custom",
                   "custom_action": "SC.Wing.Stop", "custom_action_param": {"reason": "halted"}, "enabled": False}

    for rule in nav.SCREENS:
        screen = rule.screen
        on = {"recognition": "And", "all_of": [S + screen, "SC.Wing.State.Active"]}
        if rule.kind == "wait":
            # Per-screen wait guard: the Agent counts consecutive polls and stops with wait_stalled.
            out["SC.Wing.State.Wait." + screen] = custom_reco("SC.Wing.State", want="active", screen=screen, wait=True)
            # Speed: no native guard here (Is = the classification); a wait sends nothing.
            on["all_of"] = ["SC.Wing.State.Wait." + screen]
            on.update(action="DoNothing", post_delay=500, next=["SC.Wing.Nav.Loop"])
        elif rule.kind in ("stop", "finish"):
            on.update(action="Custom", custom_action="SC.Wing.Stop" if rule.kind == "stop" else "SC.Wing.Finish",
                      custom_action_param={"screen": screen, "reason": rule.reason})
        else:
            # Handler: a new decision round. Next: one table-bound Click per intent (Python decides on the click frame,
            # the Pipeline clicks), then the hold/requeue/halted fallbacks.
            hold = "SC.Wing.Hold." + screen
            out["SC.Wing.State.Round." + screen] = custom_reco("SC.Wing.State", want="round", screen=screen)
            # Speed: the round opens on the classification (Is) alone; the native screen guard runs once,
            # in each Click node on the frame that is tapped (it ran twice per round before: here and there).
            on["all_of"] = ["SC.Wing.State.Round." + screen]
            clicks = []
            for intent in nav.screen_intents(screen):
                pick, click = PICK + f"{screen}.{intent}", CLICK + f"{screen}.{intent}"
                out[pick] = custom_reco("SC.Wing.Decide", screen=screen, intent=intent)
                out[click] = {
                    # Decision first (cached per round; a miss is cheap), then the native positive screen guard.
                    "recognition": "And", "all_of": [pick, S + screen, "SC.Wing.State.Settled"],
                    "action": "Click", "target": list(nav.TARGETS[intent][0]), "pre_delay": 0, "post_delay": 200,
                   
                    "max_hit": 1 if intent in ("fes_birth", "fes_birth_ok", *nav.CONSUMING_INTENTS) else 999,
                    # on_error: the click was refused (gate/host) or no expected screen followed within the timeout;
                    # either way the result is not proven -> stop, never retry.
                    "timeout": 20000, "next": ["SC.Wing.After." + screen],
                    "on_error": ["SC.Wing.Stop.ClickUnconfirmed"],
                    "enabled": False,
                }
                clicks.append(click)
            on.update(action="DoNothing", pre_delay=0, post_delay=0, next=clicks + [hold, REQUEUE, HALTED],
                      on_error=["SC.Wing.Stop.NoDecision"])
            # Speed (2026-10-09c): the success guard is the Agent's classification of the new frame from one
            # whole-frame OCR (SC.Wing.State want=expect) instead of an Or of up to ~20 native screen guards.
            # Plain advance taps (wing_nav.REOBSERVE_INTENTS) also accept any recognised screen: re-observe, no stop.
            expect_param = {"screens": list(rule.expect), **({"loose": True} if rule.loose else {})}
            out["SC.Wing.Expect." + screen] = custom_reco("SC.Wing.State", want="expect", **expect_param)
            out["SC.Wing.After." + screen] = {
                "recognition": "And", "all_of": ["SC.Wing.Expect." + screen, "SC.Wing.State.Progress"],
                "action": "DoNothing", "pre_delay": 0, "post_delay": 0, "timeout": 20000, "next": ["SC.Wing.Nav.Loop"],
                "on_error": ["SC.Wing.Stop.Uncertain"], "enabled": False,
            }
            if rule.fallback == "wait":
                # Bounded: the per-screen wait guard counts polls (wait_stalled) and fails once the Agent stopped.
                out["SC.Wing.State.Wait." + screen] = custom_reco("SC.Wing.State", want="active", screen=screen,
                                                                  wait=True)
                out[hold] = {"recognition": "And", "all_of": [IS + screen, "SC.Wing.State.Wait." + screen],
                             "action": "DoNothing", "post_delay": 500, "next": ["SC.Wing.Nav.Loop"], "enabled": False}
            else:
                # The round's decision was not a click (stop): end with its latched reason.
                out["SC.Wing.State.Held." + screen] = custom_reco("SC.Wing.State", want="held", screen=screen)
                out[hold] = {"recognition": "And", "all_of": [S + screen, "SC.Wing.State.Held." + screen],
                             "action": "Custom", "custom_action": "SC.Wing.Stop",
                             "custom_action_param": {"screen": screen, "reason": "no_decision"}, "enabled": False}
        # Speed (2026-10-09c): a cheap pre-guard first — the Agent classifies the frame once from one whole-frame OCR
        # (SC.Wing.State want=is, cached per frame), so the native screen guard after it runs only for that screen
        # instead of ~70 ROI OCR calls per Loop pass (live seg2b: Loop 5.1 s average). Stop/finish handlers keep the
        # native guard as well (they end the run); decision/wait handlers rely on Is.
        out[IS + screen] = custom_reco("SC.Wing.State", want="is", screen=screen)
        on["all_of"] = [IS + screen] + on["all_of"]
        on["enabled"] = False
        out["SC.Wing.On." + screen] = on
    return out


def click_nodes(nodes: dict | None = None) -> dict:
    """{Click node: intent} of the generated navigation."""
    nodes = build() if nodes is None else nodes
    return {name: name.rsplit(".", 1)[1] for name, node in nodes.items()
            if name.startswith(CLICK) and node.get("action") == "Click"}


# Picker targets verified in a supervised live session (tools/wing_picker_enable.py -> wing_picker_verified.json).
PICKER_EVIDENCE = wte.load_verified()

# オプション read path (2026-10-14, agent/core/wing_options.py): read-only; never toggles or saves.
OPTIONS_READ_SOURCES = {
    "hub_options": "recording captures-20261011-164549Z: #294 Hub tap (1204,231) -> #296/#298 Now Loading -> #299 "
                   "オプション, clean pair; read-only (opens the settings page)",
    "options_scroll_down": "read-only wheel inside the プロデュース設定 list; live 2026-10-14 (live-20261012k/"
                           "wing-attach1): two wheel steps, each settled on two equal frames, rows shifted as read",
    "options_back": "オプション ← (recording #306 (64,666) -> #310 Hub); live 2026-10-14 wing-attach1: ← back to Hub",
    # 2026-10-25 settings fix (RunOptions.settings_fix, user-approved switch, default off); supervised live run wing10.
    "options_unread_off": "OFF half of 未読イベント早送り解除 at the list top (recording #299: label top 280, toggle "
                          "y ~275-340, OFF x 1030-1195); live 2026-10-25 wing10 seg2: ON -> OFF read back",
    "options_popup_off": "OFF half of the W.I.N.G.編 警告ポップアップ row at the list end (recording #306: label top 504, "
                         "toggle y ~487-550); live 2026-10-25 wing10 seg2: ON -> OFF read back",
    "options_save": "オプション 保存 (recording #299/#306: button x ~1090-1252 / y ~625-700, label [1133,640,75,41]); "
                    "live 2026-10-25 wing10 seg2: -> 「設定を保存しました。」, wing11 re-opened the page and read both OFF",
    "options_saved_close": "閉じる of 「オプション / 設定を保存しました。」 (live frame 2026-10-25 20261025T035257995279Z: "
                           "button x ~558-722 / y ~403-478); the user closed it by hand once and asked Maa to do it",
}

# Targets verified on a live frame (user W.I.N.G. approval) but not yet tapped by Maa.
FRAME_VERIFIED = frozenset({"reward_tab_ex", "reward_tab_knowhow", "reward_item_1"})


# say "Halo" in-run taps (H2b 2026-11-02) shipped disabled; H2c (2026-11-04, after the H1 replay) enables the verified
# ones like every other wing-* target. They stay admitted only for a run with RunOptions.halo_route.
HALO_DISABLED: frozenset = frozenset()

# say "Halo" start taps (H2c batch 2, 2026-11-04): evidence per target.
HALO_SETUP_SOURCES = {
    "halo_tab_special": "プロデュース選択 特化プロデュース tab; weekly Auto live 2026-10-30 (weekly-auto-setup-tab-special, "
                        "same page and rect); H1 frames show the label at [1013,86,159,26]",
    "halo_scenario_down": "card below the selected scenario; weekly Auto live 2026-10-30 on the 育成 list "
                          "(weekly-auto-setup-scenario-down); on the 特化 list S.T.E.P. sits above say \"Halo\"",
    "halo_scenario_next": "プロデュース選択 次へ; H1 r16c29 20261101T095123 -> ユニット編成",
    "halo_book_auto": "ノウハウブック選択 おまかせ編成 [548,640,144,28]; H1 r16c15 20261101T095203 -> Now Loading -> slot 1 "
                      "filled",
    "halo_book_history": "ノウハウブック選択 設定履歴; weekly Auto live (weekly-auto-setup-book-history, same rect)",
    "halo_history_call": "設定履歴 newest 呼び出し; weekly Auto live (weekly-auto-setup-history-call, same rect)",
    "halo_book_preset": "ノウハウブック選択 プリセット; weekly Auto live (weekly-auto-setup-book-preset, same rect)",
    "halo_preset_set_1": "プリセット dialog row 1 セット; weekly Auto live (weekly-auto-setup-preset-set-1, same rect)",
    "halo_preset_set_2": "プリセット dialog row 2 セット; weekly Auto live (weekly-auto-setup-preset-set-2, same rect)",
    "halo_book_decide": "ノウハウブック選択 決定; H1 r16c29 20261101T095219 -> プロデュースアイテム準備",
}
# 成長 taps (H2c batch 3): each one the user tapped on H1.
HALO_GROWTH_SOURCES = {
    "halo_growth_open": "Hub 成長 [1001,600,181,89]; H1 r16c27 x7 -> 成長",
    **{f"halo_growth_stat_{c}": f"ステータス {n}+10 >>; H1 {cell} -> 「N回」 and the points dropped"
       for c, n, cell in (("vo", "Vocal", "r05c14"), ("da", "Dance", "r08c14"), ("vi", "Visual", "r11c14"))},
    **{f"halo_growth_cap_{c}": f"ステータス {n}上限+10 +; H1 {cell} x12-16 at S3W8, each +1回"
       for c, n, cell in (("vo", "Vocal", "r05c27"), ("da", "Dance", "r08c27"), ("vi", "Visual", "r11c27"))},
    "halo_growth_tab_other": "その他 tab; H1 r03c17 20261101T102816 -> テンション+10 / 親愛度+10",
    "halo_growth_affection": "その他 親愛度+10 +; H1 r05c28 20261101T102836 -> 団結力 199 -> 169",
    "halo_growth_learn": "成長 習得する; H1 r16c29 x9 -> 成長確認",
    "halo_growth_confirm": "成長確認 習得する; H1 r15c18 x9 -> 成長 (points spent)",
}


def action_catalog(nodes: dict | None = None, enabled: bool = False) -> list[dict]:
    """``wing-*`` entries of assets/input/pipeline_actions.json (shipped ``enabled: false``): one action per intent,
    bound to every Click node of that intent, rect = ``wing_nav.TARGETS``. ``enabled=True`` is the reviewed live-run
    variant, committed since the 2026-10-11 user approval."""
    by_intent: dict = {}
    for name, intent in sorted(click_nodes(nodes).items()):
        by_intent.setdefault(intent, []).append(name)
    out = []
    for intent in nav.TARGETS:
        if intent not in by_intent:
            continue
        rect, verified = nav.TARGETS[intent]
        consuming = intent in nav.CONSUMING_INTENTS
        if intent in FRAME_VERIFIED:
            source = (f"wing_nav.TARGETS[{intent}] (verified 2026-10-10 on the live 獲得報酬 frame 20261010T073201 "
                      "under the user W.I.N.G. approval; read-only tap)")
        elif verified and intent in wte.PICKER_INTENTS and intent in PICKER_EVIDENCE                 and not PICKER_EVIDENCE[intent].startswith("captures-20261011-164549Z"):
            source = f"wing_nav.TARGETS[{intent}] (live-verified: {PICKER_EVIDENCE[intent]}; assets/input/wing_picker_verified.json)"
        elif not verified and intent in wte.PICKER_INTENTS:
            source = (f"wing_nav.TARGETS[{intent}] (picker grid geometry of captures-20261011-164549Z / card_inventory "
                      "idol_list; UNVERIFIED: Agent gate refuses; enable with "
                      "tools/wing_picker_enable.py after a supervised live check)")
        elif intent == wte.SORT_OPEN_INTENT or intent in wte.SORT_POPUP_INTENTS:
            evidence = PICKER_EVIDENCE.get(intent)
            source = (f"wing_nav.TARGETS[{intent}] (picker 並び替え, agent/core/picker_sort.py; RunOptions.picker_sort "
                      + (f"only; {evidence})" if evidence else
                         "only; UNVERIFIED: no clean before/after pair in captures-20261011-164549Z; "
                         "enable with tools/wing_picker_enable.py after a supervised live check)"))
        elif intent == wsv.ZOOM_OUT:
            source = ("wing_sp_view.ZOOM_RECT (third demo #624 -> #625; supervised live tap 2026-10-14 gave the centred "
                      "small view, user-approved)")
        elif intent == wsv.INFO_CLOSE:
            source = ("wing_sp_view.INFO_CLOSE_RECT (empty small-view background; supervised live tap 2026-10-14 closed "
                      "the info bar, frame identical to the unselected view)")
        elif intent in wsv.SMALL_INTENTS:
            source = ("wing_sp_view.node_rect (small lattice 58.5x33.75; supervised live 2026-10-14: 43 labels on the "
                      "lattice, a node tap under the info-bar area opened that node's info)")
        elif intent in OPTIONS_READ_SOURCES:
            source = f"wing_nav.TARGETS[{intent}] ({OPTIONS_READ_SOURCES[intent]})"
        elif intent in ("giveup_open", "giveup_hold"):
            source = (f"wing_nav.TARGETS[{intent}] (say \"Halo\" H0 live frames 2026-11-01: Hub 「諦める ホームに戻る」 "
                      "tap pair 20261101T072141 r03c30 -> dialog; dialog 「諦める」 [735,440,165,75], long press only "
                      "(hold_ms, host hold v2), pressed by the user that run); RunOptions.halo_giveup only")
        elif intent in HALO_SETUP_SOURCES:
            source = (f"wing_nav.TARGETS[{intent}] ({HALO_SETUP_SOURCES[intent]}); say \"Halo\" start only "
                      "(RunOptions.halo_route + mode start, H2c batch 2)")
        elif intent in HALO_GROWTH_SOURCES:
            source = (f"wing_nav.TARGETS[{intent}] ({HALO_GROWTH_SOURCES[intent]}); 成長 visit Maa opened "
                      "(RunOptions.halo_route, H2c batch 3)")
        elif intent.startswith("halo_"):
            source = (f"wing_nav.TARGETS[{intent}] (say \"Halo\" H1 frames 2026-11-01: the user's taps and the buttons' OCR "
                      "boxes; RunOptions.halo_route only); new feature: disabled until the H2c "
                      "self-test and the user's go-ahead for the supervised run"
                      + ("" if verified else "; UNVERIFIED: never tapped on H1"))
        elif intent == "unit_back":
            source = (f"wing_nav.TARGETS[{intent}] (ユニット編成 ← on wing-live-20261025/unit20-s4-pale.png, the same "
                      "widget and rect as options_back); Auto 自动编队 prep only (edit_only), enabled 2026-10-30 with the "
                      "user's W.I.N.G. live approval; the After guard needs プロデュース選択")
        elif intent == "unit_page_prev":
            source = (f"wing_nav.TARGETS[{intent}] (W.I.N.G. demo recordings, native 1280x720); enabled 2026-10-13 "
                      "with the user's approval: live frame ユニット17 page 2 shows ‹ inside the rect; the page is re-read "
                      "after the tap")
        else:
            source = (f"wing_nav.TARGETS[{intent}] (W.I.N.G. demo recordings, native 1280x720; "
                      f"{'clean before/after click pair' if verified else 'UNVERIFIED: Agent gate refuses'})"
                      + ("; consumes stamina 30 (Produce start)" if consuming else ""))
        if enabled and not verified:
            source += "; disabled 2026-10-11 until verified live"
        # 2026-10-11 review P1-5: the live-run variant enables only targets verified live (wing_nav.TARGETS flag).
        entry = {"id": nav.action_id(intent), "family": nav.intent_family(intent), "consuming": consuming,
                 "enabled": bool(enabled) and verified and intent not in HALO_DISABLED, "rect": list(rect)}
        if intent in nav.WHEEL_INTENTS:
            entry["wheel"] = nav.WHEEL_INTENTS[intent]    # read-only picker wheel (no button), wing_team_edit.picker_step
        out.append({**entry, "source": source, "nodes": by_intent[intent]})
    return out


def sync_action_table(text: str, enabled: bool = False) -> str:
    """The action-table JSON text with its ``wing-*`` families/entries replaced by ``action_catalog()`` (other
    entries untouched, same 4-space layout as the committed file)."""
    data = json.loads(text)
    data["families"] = [f for f in data["families"] if not f.startswith("wing-")] + list(nav.WING_FAMILIES)
    kept = [a for a in data["actions"] if not a["family"].startswith("wing-")]
    data["actions"] = kept + action_catalog(enabled=enabled)
    return render_table(data)


def render_table(data: dict) -> str:
    """The committed layout of pipeline_actions.json: one line per top-level scalar/list, one line per action field."""
    dump = lambda value: json.dumps(value, ensure_ascii=False)  # noqa: E731
    head = [f'    "{key}": {dump(value)}' for key, value in data.items() if key != "actions"]
    actions = ["        {\n" + ",\n".join(f'            "{key}": {dump(value)}' for key, value in action.items())
               + "\n        }" for action in data["actions"]]
    return "{\n" + ",\n".join(head + ['    "actions": [\n' + ",\n".join(actions) + "\n    ]"]) + "\n}\n"


def render(nodes: dict) -> str:
    return json.dumps(nodes, ensure_ascii=False, indent=4) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 W.I.N.G. 导航框架 Pipeline（全部禁用，不发送输入）")
    parser.add_argument("--check", action="store_true", help="只比较，不写入")
    # 2026-10-11 user approval of the W.I.N.G. operations: the committed table enables every live-verified target
    # (test_pipeline_actions); --check compares against that variant.
    parser.add_argument("--enable-actions", action="store_true",
                        help="兼容旧用法：已验证的 wing-* 动作默认即启用，此参数不再改变结果")
    parser.add_argument("--disable-actions", action="store_true",
                        help="生成全部禁用的 wing-* 动作（离线草稿用，不提交）")
    parser.add_argument("--out-dir", type=Path,
                        help="把 wing.json 与 pipeline_actions.json 写到该目录（dev_assets/private 下的待切换目录），不改仓库文件")
    args = parser.parse_args(argv)
    text = render(build())
    table_text = ACTION_TABLE.read_bytes().decode("utf-8")
    table_new = sync_action_table(table_text, enabled=not args.disable_actions or args.check)
    if args.out_dir is not None and not args.check:
        out = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
        out = out.resolve()
        if not out.is_relative_to((ROOT / "dev_assets" / "private").resolve()):
            print("--out-dir 必须位于 dev_assets/private", file=sys.stderr)
            return 2
        out.mkdir(parents=True, exist_ok=True)
        (out / "wing.json").write_bytes(text.encode("utf-8"))
        (out / "pipeline_actions.json").write_bytes(table_new.encode("utf-8"))
        print(f"wrote {out} (wing.json, pipeline_actions.json enabled={not args.disable_actions})")
        return 0
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != text:
            print("wing.json 与生成结果不一致；运行 tools/wing_nav_build.py", file=sys.stderr)
            return 1
        if table_new != table_text:
            print("pipeline_actions.json 的 wing-* 条目与生成结果不一致；运行 tools/wing_nav_build.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_bytes(text.encode("utf-8"))
    if table_new != table_text:
        ACTION_TABLE.write_bytes(table_new.encode("utf-8"))
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(build())} nodes); action table wing-* entries: "
          f"{len(action_catalog())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
