#!/usr/bin/env python3
"""SC.Common 共享 Pipeline 的离线回放：真实 Maa Tasker + 私有原生帧 + 模拟点击。

控制器只回放 dev_assets/private 下的 1280x720 截图并记录点击；绝不连接浏览器、宿主或设备。
资源加载顺序与运行时相同：assets/resource/base，然后是私有模板包
dev_assets/private/resource_native（由 tools/build_native_resource.py 生成并校验哈希）。

每个用例是一串帧：``advance="click"`` 的帧在收到一次点击后切到下一帧，整数 N 表示被截图 N 次后
切换（模拟加载/过场）。点击节点默认禁用；回放只通过 pipeline_override 启用共享 Pipeline 中
默认禁用的 SC.Popup.*.Close / SC.Nav.*.Back（它们只会到达这个模拟控制器），并缩短超时。
project_policy.json 不因此改变：live_input_enabled 仍为 false，实机允许列表仍为空。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core import maa_sinks  # noqa: E402
from agent.core.pipeline_actions import default_table, parse_table  # noqa: E402
from agent.core.resource_bundles import BASE_BUNDLE, PRIVATE_NATIVE_BUNDLE as PRIVATE_BUNDLE, ocr_model_dir  # noqa: E402
from agent.core.task_failure import fail_reason  # noqa: E402
from tools.build_native_resource import verify as verify_bundle  # noqa: E402
from tools.replay_screenshots import read_png, require  # noqa: E402
from tools.run_tasks import ReplayScenario, create_replay_controller  # noqa: E402

FRAMEWORK = "v5.12.3"
PRIVATE = ROOT / "dev_assets" / "private"
COMMON_PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline" / "common"
ENTRY = "SC.Common.GoHome"
HOME_END = "SC.Common.HomeClean"
FAIL_NODE = "SC.Common.FailTask"
STOP_PREFIX = "SC.Common.Stop."  # reasoned stops (also end the task through SC.FailTask, no input)
CASE_SECONDS = 40.0  # 2026-10-27: GoHome rounds of ~2.4 s on a busy machine; 25 s cut off a 9-round case
TOTAL_SECONDS = 600.0
# Static frames never change by themselves, so the live timeouts only add waiting time.
# A Maa timeout belongs to the node whose `next` list is being waited on.
# GoHome 12 s (2026-10-23 8 s, 2026-10-27 12 s, as tools/run_tasks.py): one BackOne round can take 1-3 s; 3 s expired before a
# 3-capture frame advanced.
OFFLINE_TIMEOUTS = {"SC.Common.GoHome": 12000, "SC.Common.HomeReached": 1500, "SC.Loading.Wait": 3000}
OFFLINE_CLICK_TIMEOUT = 3000

NAV = "in_app_browser/20261001-daily-research/nav-native-v27/"
GRADE = "in_app_browser/20261002-grade/grade-native-v1/"
# A real clean Home frame from the same day; used where the true successor frame was never captured.
HOME = "nav-v27-pickup-close-20261002-frames/frame-005.png"
# 新ガシャ開催！ cut-in (native, 2026-10-03 04:51) and a synthetic copy without its two top-right buttons.
NEW_GACHA = "manual-capture-kept-20261002/new-gacha-cutin-20261003.png"
NEW_GACHA_NO_SKIP = "manual-capture-kept-20261002/new-gacha-cutin-no-skip-synthetic-20261003.png"
# Dynamic-element regression (2026-10-03 05:15 live Home): no ミッション/プレゼント badge, ガシャ 「1日1回無料」, プロデュース
# 「はづきさん出現」, 営業 timer, Fes 20/20; plus earlier Home frames whose ミッション badge shows 1 / 3, and a dimmed copy.
HOME_NO_BADGE = "manual-capture-kept-20261002/home-20261003-nobadge.png"
HOME_NO_BADGE_DIMMED = "manual-capture-kept-20261002/home-20261003-nobadge-dimmed-synthetic.png"
HOME_BADGE_1 = "in_app_browser/20261002-grade/grade-native-v1/Home-040.png"
HOME_BADGE_3 = "grade-run-02-home-fes-frames/frame-001.png"
# 2026-10-03 15:22 live Home (Business stopped at the 営業 entry): plain 営業 icon + red 「3」 badge, ショップ 「ジュエルセール中」,
# プレゼント 9, ミッション 2; and the 06:21 Home: 営業 timer 6:09, ミッション 1, プレゼント 2, Fes 19/20 with recovery timer.
HOME_BUSINESS_BADGE = "manual-capture-kept-20261002/home-20261003-business-badge.png"
HOME_0621 = "manual-capture-kept-20261002/home-20261003-0621-timer.png"
STORY_VARIANT = "manual-capture-kept-20261002/story-variant-scaled-synthetic-20261013.png"
# 2026-10-19/20 live cut-ins with the same 全動画SKIP/SKIP/Touch layout: HAPPY BIRTHDAY and 新アイドル登場！ (whose title art
# does not match the 10-03 title.png). Their SKIP equals the login-board / story SKIP.
BIRTHDAY = "live-20261019/birthday-cutin.png"
NEW_IDOL = "live-20261020/login-anniversary.png"
# Full-screen gacha announcements (letterboxed art, 「…ガシャ」 title line + 開催期間 pill): 2026-10-27 banner left after a
# new-gacha SKIP, and the 10-20 new-card promo of the same layout. The gacha page itself (draw button near the tap spot).
GACHA_BANNER = "live-20261027/gacha-announcement.png"
PROMO = "live-20261020/new-card-promo.png"
GACHA_PAGE = "captures-20261011-164549Z/frame-000821.png"


@dataclass(frozen=True)
class Case:
    id: str
    steps: tuple  # ((image, advance), ...); advance is "click" or a capture count
    clicks: tuple  # exact expected action ids (agent/core/pipeline_actions.py), in order
    outcome: str  # "home" or "fail"
    enable_input: bool = True
    note: str = ""
    reason: str = ""  # expected SC.FailTask reason of an "fail" outcome ("" = not checked)


STAY = 10 ** 9  # the frame never changes, whatever is clicked


def _s(*items):
    return tuple(items)


PAGES_APPLIED = (Path(__file__).resolve().parent.parent / "assets/resource/base/pipeline/common/pages.json").is_file()
_GRADE_BACK = ("nav-back",) if PAGES_APPLIED else ()

CASES = (
    Case("home-direct", _s((HOME, "click")), (), "home"),
    Case("home-no-badge-free-gacha", _s((HOME_NO_BADGE, "click"),), (), "home",
         note="实机失败帧：无ミッション角标、ガシャ「1日1回无料」；必须通过 Home 守卫与 HomeClean"),
    Case("home-mission-badge-1", _s((HOME_BADGE_1, "click"),), (), "home", note="ミッション角标「1」"),
    Case("home-business-badge-jewel-sale", _s((HOME_BUSINESS_BADGE, "click"),), (), "home",
         note="15:22 实机帧：営業 无计时、红色角标「3」，ショップ「ジュエルセール中」，プレゼント 9，ミッション 2"),
    Case("home-0621-business-timer", _s((HOME_0621, "click"),), (), "home",
         note="06:21 实机帧：営業 计时 6:09，ミッション 1，Fes 19/20 恢复计时"),
    Case("home-mission-badge-3", _s((HOME_BADGE_3, "click"),), (), "home", note="ミッション角标「3」"),
    Case("reward-board-reward-missioncompleted",
         _s(("nav-v25-reward-ack-20261002-frames/frame-003.png", "click"),
            ("nav-v25-reward-ack-20261002-frames/frame-004.png", 3),
            ("nav-v25-reward-ack-20261002-frames/frame-013.png", "click"),
            ("nav-v25-reward-ack2-20261002-frames/frame-004.png", 3),
            ("nav-v25-reward-ack2-20261002-frames/frame-005.png", "click"),
            (HOME, "click")),
         ("popup-reward-ok", "popup-reward-ok", "popup-mission-completed-ok"), "home",
         note="两次登录奖励与ミッション達成均为真实后继帧；最后的首页为同日替代帧"),
    Case("missioncompleted-ok", _s(("shop-20261002-10-home-frames/frame-024.png", "click"), (HOME, "click")),
         ("popup-mission-completed-ok",), "home", note="首页为同日替代帧"),
    Case("pickup-close", _s(("nav-v27-pickup-close-20261002-frames/frame-003.png", "click"), (HOME, "click")),
         ("popup-pickup-close",), "home", note="真实后继帧"),
    Case("announcement-back",
         _s(("nav-v17-announcement-back-execute-frames/frame-003.png", "click"),
            ("nav-v17-announcement-back-execute-frames/frame-004.png", 2),
            ("nav-v17-announcement-back-execute-frames/frame-005.png", "click"),
            (HOME, "click")),
         ("nav-back", "popup-mission-completed-ok"), "home", note="公告→ミッション達成为真实后继帧"),
    Case("match-help-close-then-back",
         _s(("nav-v21-match-help-close-execute-frames/frame-003.png", "click"),
            ("nav-v21-match-help-close-execute-frames/frame-005.png", "click"),
            (HOME, "click")),
         ("popup-help-close", "nav-back"), "home", note="教程→MATCH 主页为真实后继帧；返回后的首页为替代帧"),
    Case("training-help-chain",
         _s(("daily-ready-idol-training-v12-frames/frame-010.png", "click"),
            ("nav-v15-training-list-back-execute-frames/frame-005.png", "click"),
            ("nav-v15-training-list-back-execute-frames/frame-006.png", "click"),
            ("nav-v16-idol-back-execute-frames/frame-013.png", 2),
            ("nav-v16-idol-back-execute-frames/frame-021.png", "click"),
            ("nav-v17-announcement-back-execute-frames/frame-005.png", "click"),
            (HOME, "click")),
         ("popup-help-close", "nav-back", "nav-back", "nav-back", "popup-mission-completed-ok"), "home",
         note="训练列表→偶像页→公告→ミッション達成为真实后继；教程后的列表来自另一次访问"),
    Case("mission-help-chain",
         _s((NAV + "daily-v6-MissionHelp-positive.png", "click"),
            ("nav-v19-mission-back-execute-frames/frame-009.png", "click"),
            ("nav-v19-mission-back-execute-frames/frame-010.png", "click")),
         ("popup-help-close", "nav-back"), "home", note="任务页→首页为真实后继；教程后的任务页来自另一次访问"),
    # 2026-10-27 live: MissionsClaim stopped on the ウィークリー tab and the next task could not leave the page.
    Case("mission-weekly-tab-back", _s(("live-20261027/mission-weekly-selected.png", "click"), (HOME, "click")),
         ("nav-back",), "home", note="首页为替代帧"),
    Case("fes-help-chain",
         _s((GRADE + "FesHelp-033.png", "click"),
            ("grade-run-04-join-frames/frame-001.png", "click"),
            ("grade-run-02-home-fes-frames/frame-001.png", "click")),
         ("popup-help-close", "nav-back"), "home", note="Fes 主页与首页为同日替代帧"),
    Case("story-skip", _s((NAV + "daily-v9-Story-positive.png", "click"), (HOME, "click")),
         ("popup-story-skip",), "home", note="首页为替代帧"),
    # 2026-10-19 live: after the birthday SKIP a static promo without a back key appeared and the task stopped; that
    # promo was not saved, so the 10-20 new-card promo (same kind of page) stands in.
    Case("birthday-skip-then-promo", _s((BIRTHDAY, "click"), (PROMO, "click"), (HOME, "click")),
         ("popup-story-skip", "popup-gacha-announcement-close"), "home",
         note="宣传图为 10-20 新卡宣传页替代帧，首页为替代帧（关闭后的真实后继帧尚无样本）"),
    # 2026-10-27 user: Maa closes the gacha announcement itself — one tap below the 開催期間 pill, never repeated.
    Case("gacha-banner-close", _s((GACHA_BANNER, "click"), (HOME, "click")), ("popup-gacha-announcement-close",),
         "home", note="首页为替代帧"),
    Case("new-card-promo-close", _s((PROMO, "click"), (HOME, "click")), ("popup-gacha-announcement-close",), "home",
         note="同版式宣传页（标题行没有「開催！」）；首页为替代帧"),
    Case("new-gacha-skip-then-banner-close", _s((NEW_GACHA, "click"), (GACHA_BANNER, "click"), (HOME, "click")),
         ("popup-new-gacha-skip", "popup-gacha-announcement-close"), "home", note="实机顺序：SKIP 后留下公告；首页为替代帧"),
    Case("gacha-banner-to-gacha-page", _s((GACHA_BANNER, "click"), (GACHA_PAGE, "click"), (HOME, "click")),
         ("popup-gacha-announcement-close", "nav-back"), "home", note="假设点公告进了ガシャ页：只按返回键，不点页面"),
    Case("gacha-banner-click-no-effect", _s((GACHA_BANNER, STAY),), ("popup-gacha-announcement-close",), "fail",
         reason="gacha_announcement_unhandled", note="点后公告没变：不重复点击"),
    Case("gacha-banner-input-disabled", _s((GACHA_BANNER, "click"),), (), "fail", enable_input=False,
         reason="gacha_announcement_unhandled"),
    # 新ガシャ開催！ cut-in (new gacha pool launch): only the right-hand SKIP, never 全動画SKIP or Touch.
    Case("new-gacha-skip", _s((NEW_GACHA, "click"), (HOME, "click")), ("popup-new-gacha-skip",), "home",
         note="首页为替代帧（SKIP 的真实后继帧尚无样本）"),
    Case("loading-then-home", _s(("startup-game-visible-execute-20261001-frames/frame-004.png", 3), (HOME, "click")),
         (), "home"),
    Case("training-selected-back-chain",
         _s(("training-selection-execute-20261001f-frames/frame-006.png", "click"),
            ("training-selection-execute-20261001f-frames/frame-005.png", "click"),
            ("nav-v15-training-list-back-execute-frames/frame-006.png", "click"),
            (HOME, "click")),
         ("nav-back", "nav-back", "nav-back"), "home"),
    Case("shop-list-back", _s(("shop-live-money-tab-20261002e-frames/frame-006.png", "click"),
                              ("shop-20261002-01-home-frames/frame-001.png", "click")),
         ("nav-back",), "home"),
    # Never auto-closed: consumption confirmations, results owned by their task, unknown or error states.
    Case("shop-exchange-confirm", _s(("shop-20261002-03-open1-frames/frame-006.png", "click"),), (), "fail"),
    Case("shop-exchange-result", _s(("shop-20261002-04-confirm1-frames/frame-006.png", "click"),), (), "fail"),
    # 2026-10-11: after tools/go_home_pages.py --apply (common/pages.json) these pages get only the back key (the
    # single-frame replay then times out on the same page); the difficulty choice / まとめて消費 are never touched.
    Case("grade-unit-tension", _s(("grade-run-07c-normal-frames/frame-024.png", "click"),), _GRADE_BACK, "fail"),
    Case("grade-difficulty", _s(("grade-run-05-mode-frames/frame-007.png", "click"),), _GRADE_BACK, "fail"),
    Case("grade-difficulty-money2x", _s(("manual-capture-kept-20261002/grade-difficulty-money2x-20261003.png", "click"),),
         _GRADE_BACK, "fail", note="2026-10-03 「獲得マニー 2倍」 活动角标的难度页（消费入口：只按返回键，不改难度）"),
    Case("training-confirm-dialog", _s(("training-v4-confirm-execute-20261001b-frames/frame-001.png", "click"),),
         (), "fail"),
    Case("home-with-fes-menu", _s(("grade-run-02-home-fes-frames/frame-008.png", "click"),), (), "fail",
         reason="fes_menu_open"),
    Case("home-no-badge-dimmed", _s((HOME_NO_BADGE_DIMMED, "click"),), (), "fail",
         note="合成帧：无角标首页整体变暗（未知遮罩），HomeUndimmed 拒绝"),
    Case("communication-error",
         _s(("in_app_browser/20261001-daily-research/communication-error-020-training-list.png", "click"),),
         (), "fail", reason="communication_error"),
    Case("start-screen", _s(("nav-v23-game-start-execute-frames/frame-001.png", "click"),), (), "fail"),
    Case("fes-help-fading-in", _s((GRADE + "FesHelp-032.png", "click"),), (), "fail"),
    Case("match-skip-submitted", _s((NAV + "daily-navigation-sample-08.png", "click"),), (), "fail"),
    Case("training-submit-response", _s((NAV + "daily-navigation-sample-07.png", "click"),), (), "fail"),
    # After SKIP the cut-in must give way to a different known screen; a purchase dialog (stand-in for a gacha page) or a
    # follow-up promo page without the title is unknown: no further click, the task fails.
    Case("new-gacha-skip-then-purchase-dialog",
         _s((NEW_GACHA, "click"), ("shop-20261002-03-open1-frames/frame-006.png", "click")),
         ("popup-new-gacha-skip",), "fail", note="交換確認作为ガシャ购买页的替代帧"),
    Case("new-gacha-skip-then-unknown-promo", _s((NEW_GACHA, "click"), (NEW_GACHA_NO_SKIP, "click")),
         ("popup-new-gacha-skip",), "fail", note="合成帧：同一过场去掉右上两个按钮，模拟未知的后续宣传页"),
    Case("new-gacha-without-skip-buttons", _s((NEW_GACHA_NO_SKIP, "click"),), (), "fail", note="合成帧"),
    # A click that changes nothing is never repeated: one attempt, then the task fails.
    Case("pickup-click-no-effect", _s(("nav-v27-pickup-close-20261002-frames/frame-003.png", STAY),),
         ("popup-pickup-close",), "fail"),
    Case("reward-click-no-effect", _s(("nav-v25-reward-ack-20261002-frames/frame-003.png", STAY),),
         ("popup-reward-ok",), "fail", note="弹窗下的登录板 SKIP 变暗，不算作已离开弹窗"),
    Case("new-gacha-click-no-effect", _s((NEW_GACHA, STAY),), ("popup-new-gacha-skip",), "fail",
         note="SKIP 下方的 SKIP 与登录板 SKIP 相同，但不算作已离开过场"),
    Case("birthday-click-no-effect", _s((BIRTHDAY, STAY),), ("popup-story-skip",), "fail",
         note="生日过场 SKIP 与登录板/预告 SKIP 相同，但不算作已离开过场"),
    Case("new-idol-click-no-effect", _s((NEW_IDOL, STAY),), ("popup-new-gacha-skip",), "fail",
         note="新アイドル登場！预告：标题不同的同一过场，只点 SKIP 一次"),
    Case("mission-back-no-effect", _s(("nav-v19-mission-back-execute-frames/frame-009.png", STAY),),
         ("nav-back",), "fail"),
    Case("missioncompleted-animated-button",
         _s(("nav-v18-mc2m-execute-frames/frame-004.png", "click"), (HOME, "click")), ("popup-mission-completed-ok",), "home",
         note="「ミッションへ」闪光动画中；仍只点 OK"),
    # Shipped resources keep every click node disabled: a known popup is recognised but never clicked.
    Case("reward-input-disabled", _s(("nav-v25-reward-ack-20261002-frames/frame-003.png", "click"),), (),
         "fail", enable_input=False),
)


# ---- 2026-10-09 go-home coverage: native frames of every page the user's flows pass through ---------------------
KNOW = "live-capture-20261008-knowhow/"
MATCH_READER = "fes-match-reader-20261007/"
SUPERVISED = "supervised-tasks-20261007/"
BUSINESS = "business-native-20261003-frames/"
REC = "recordings/wing-demo-20261005-0058-01/"
STOP_CASES = (
    # Known but unsafe states stop with a specific reason, never a click.
    Case("stop-produce-hub", _s(("recordings/wing-demo2-20261006-0106-02/frame-002506.png", "click"),), (), "fail",
         reason="produce_in_progress", note="进行中的 Produce Hub（ファン人数 + プロデュース标题）：交给 W.I.N.G."),
    Case("stop-produce-schedule", _s(("recordings/wing-demo2-20261006-0106-02/frame-001865.png", "click"),), (), "fail",
         reason="produce_in_progress", note="スケジュール選択（进行中）"),
    Case("stop-produce-review", _s(("recordings/wing-demo3-20261006-1643-03/frame-000401.png", "click"),), (), "fail",
         reason="produce_in_progress", note="振り返り（进行中的技能树）"),
    # 2026-10-10: after a reload the home shows 「プロデュース中」 (still a clean home) and its プロデュース再開 dialog
    # stops for the W.I.N.G. task (from_home); GoHome never taps キャンセル / 終了 / 再開.
    Case("home-produce-in-progress", _s(("wing-live-20261009/restart3.png", "click"),), (), "home",
         note="首页「プロデュース中」：仍是首页（日常任务可继续），W.I.N.G. 由 from_home 接管"),
    Case("stop-produce-resume-dialog", _s(("wing-live-20261009/sample-produce-resume-dialog.png", "click"),), (), "fail",
         reason="produce_in_progress", note="プロデュース再開 对话框：交给 W.I.N.G.，不点任何按钮"),
    Case("stop-maintenance",_s((KNOW + "mission_bronze_final_state.png", "click"),), (), "fail", reason="maintenance"),
    Case("stop-error-1012", _s((BUSINESS + "error-1012-20261001.png", "click"),), (), "fail", reason="communication_error",
         note="营业错误 1012：结果不明，不点「お問い合わせ」"),
    Case("stop-business-report", _s((BUSINESS + "bulk/run2050/frame-009.png", "click"),), (), "fail",
         reason="business_report_pending", note="营业结果报告页：不点 Touch to Next，不返回"),
    Case("stop-fes-menu-open", _s(("grade-run-02-home-fes-frames/frame-008.png", "click"),), (), "fail",
         reason="fes_menu_open", note="首页フェス菜单展开：尚无收起动作样本"),
    Case("stop-grade-live", _s(("grade-live-manual-20261003-frames/001_20261003-002301-971089_frame-001.png", "click"),),
         (), "fail", reason="grade_live_in_progress"),
    # 2026-10-13: an event-skinned idle story (e.g. the Halloween home story) whose SKIP/LOG/HIDE only resemble the
    # verified buttons is never skipped: reasoned stop, no click. Synthetic: the verified story frame with the three
    # buttons shrunk to 95 % (scores ~0.86–0.91, below the 0.95 Story guard, above the 0.8 presence anchors).
    Case("stop-story-variant", _s((STORY_VARIANT, "click"),), (), "fail", reason="story_overlay_unverified",
         note="合成帧：剧情按钮外观与已验证模板不一致（活动换皮）；不点 SKIP"),
)
# Pages that already have a back node in the shipped Pipeline, replayed on the newer supervised-run frames.
PAGE_CASES = (
    Case("page-idol-portal", _s((KNOW + "idol_portal_after_lists.png", "click"), (HOME, "click")), ("nav-back",), "home"),
    Case("page-mission-list", _s((MATCH_READER + "missions_before_bulk.png", "click"), (HOME, "click")), ("nav-back",), "home",
         note="任务页（デイリー）；领取一括确认框是另一帧，不在此列"),
    Case("page-match-main", _s((MATCH_READER + "match_main_top.png", "click"), (HOME, "click")), ("nav-back",), "home"),
    Case("page-match-skip-dialog", _s((MATCH_READER + "match_skip_top.png", "click"),
                                      (MATCH_READER + "match_skip_cancelled.png", "click"), (HOME, "click")),
         ("match-skip-cancel", "nav-back"), "home", note="遗留的スキップ弹窗：只点キャンセル（非消费）再返回"),
    Case("page-training-list", _s((MATCH_READER + "training_list_descending.png", "click"),
                                  (KNOW + "idol_portal_after_lists.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back"), "home", note="训练列表 → アイドル页 → 首页"),
    Case("page-shop-list", _s((SUPERVISED + "after_shop_r6.png", "click"), (HOME, "click")), ("nav-back",), "home"),
    # 2026-11-23: a new browser profile shows each page's first-visit help again; the generic help close (assistant icon,
    # counter n/m, 閉じる) handles help pages without their own nodes. MATCH LIVE help page 5/17 is outside MatchHelp
    # (its page template is 1/17), so only the generic close can take it.
    Case("help-page-5-generic-close",
         _s(("fes-match-reader-20261007/match_help_05.png", "click"),
            ("nav-v21-match-help-close-execute-frames/frame-005.png", "click"), (HOME, "click")),
         ("popup-help-close", "nav-back"), "home", note="教程第 5/17 页 → 閉じる → MATCH 主页 → 首页（后继帧为替代帧）"),
)
# Pages whose back node needs the table binding (docs/proposals/go_home_pages_20261009.json): replayed with it merged.
PROPOSAL_CASES = (
    # 2026-11-22 live: Collabo pages left after a leftover LIVE REPORT (Startup unknown_or_unhandled_state); each back
    # key lands one page up: 編成 -> 難易度とレベル -> 活動页 -> home (frames of the 2026-11-18 Collabo samples).
    Case("collabo-unit-to-home", _s(("wing_samples/2026-11-18/20261118T110023328229Z-wait.png", "click"),
                                    ("wing_samples/2026-11-18/20261118T105944670693Z-wait.png", "click"),
                                    ("wing_samples/2026-11-18/20261118T105939862547Z-tap-before.png", "click"),
                                    ("wing_samples/2026-11-18/20261118T105524186004Z-look.png", "click")),
         ("nav-back", "nav-back", "nav-back"), "home"),
    # 2026-10-22 live: the shop opened on プレミアム (ジュエルSALE, no NEW badge over the tab); a failed Shop left every
    # later task there. Only the back key.
    Case("shop-premium-sale-back", _s(("shop-20261022-sale/premium.png", "click"),
                                      ("shop-20261002-01-home-frames/frame-001.png", "click")),
         ("nav-back",), "home"),
    # 2026-10-25 live: the WeeklyProduce read-only sample stops on オートスケジュール; ← lands on プロデュース選択
    # (stand-in frame: the landing after ← is unsampled).
    Case("auto-schedule-to-home", _s(("weekly-auto-20261025/schedule-empty.png", "click"),
                                     ("captures-20261022-110140Z/frame-000097.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back"), "home", note="オートスケジュール → プロデュース選択 → 首页"),
    Case("present-unclaimed-to-home", _s((MATCH_READER + "present_unclaimed_initial.png", "click"), (HOME, "click")),
         ("nav-back",), "home"),
    Case("present-received-tab-to-home", _s((MATCH_READER + "present_received_tab.png", "click"), (HOME, "click")),
         ("nav-back",), "home", note="受取済タブ（无页脚文字）也能识别"),
    Case("present-empty-after-claim", _s((KNOW + "present_live_empty_after_receive.png", "click"), (HOME, "click")),
         ("nav-back",), "home"),
    Case("present-bulk-confirm-never-clicked", _s((MATCH_READER + "present_bulk_confirm.png", "click"),), (), "fail",
         note="一括受取确认框：返回键变暗，不满足 Present 守卫；キャンセル/OK 都不点"),
    Case("present-bulk-receipt-never-clicked", _s((MATCH_READER + "present_bulk_receipt.png", "click"),), (), "fail"),
    Case("idol-list-to-home", _s((KNOW + "idol_list_produce_top.png", "click"), (KNOW + "idol_portal_after_lists.png", "click"),
                                 (HOME, "click")), ("nav-back", "nav-back"), "home", note="一覧 → アイドル页 → 首页"),
    Case("idol-list-support-tab-to-home", _s((KNOW + "idol_list_support_top.png", "click"),
                                             (KNOW + "idol_portal_after_lists.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back"), "home"),
    Case("knowhow-list-to-home", _s((KNOW + "knowhow_default_grid.png", "click"), (KNOW + "idol_list_fes_initial.png", "click"),
                                    (KNOW + "idol_portal_after_lists.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back", "nav-back"), "home", note="ノウハウブック一覧 → アイドル一覧 → アイドル页 → 首页"),
    Case("knowhow-sort-popup-never-clicked", _s((KNOW + "knowhow_sort_options.png", "click"),), (), "fail",
         note="並び替え弹窗遮暗返回键：不满足 KnowhowList 守卫，不点"),
    Case("business-top-to-home", _s((BUSINESS + "all-empty.png", "click"), (HOME, "click")), ("nav-back",), "home"),
    Case("business-top-active-slots-to-home", _s((BUSINESS + "bulk/run2050/frame-004.png", "click"), (HOME, "click")),
         ("nav-back",), "home"),
    Case("match-stage-select-to-home", _s((MATCH_READER + "match_stage20_detail.png", "click"),
                                          (MATCH_READER + "match_main_top.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back"), "home", note="ステージ選択 → MATCH 主页 → 首页"),
    Case("grade-difficulty-to-home", _s(("grade-run-07c-normal-frames/frame-013.png", "click"),
                                        ("grade-run-04-join-frames/frame-001.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back"), "home", note="难度选择只按返回键（不改难度/まとめて消費）→ Fes 主页 → 首页"),
    Case("grade-unit-to-home", _s(("grade-run-07c-normal-frames/frame-016.png", "click"),
                                  ("grade-run-07c-normal-frames/frame-013.png", "click"),
                                  ("grade-run-04-join-frames/frame-001.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back", "nav-back"), "home", note="参加ユニット（テンション決定之前）→ 难度 → Fes 主页 → 首页"),
    Case("produce-setup-chain-to-home", _s((REC + "frame-000239.png", "click"), (REC + "frame-000164.png", "click"),
                                           (REC + "frame-000115.png", "click"), (HOME, "click")),
         ("nav-back", "nav-back", "nav-back"), "home", note="开始前的 アイテム選択 → ユニット編成 → プロデュース選択 → 首页"),
    Case("produce-setup-unit-copy-dialog-never-clicked",
         _s(("recordings/wing-demo3-20261006-1643-03/frame-000280.png", "click"),), (), "fail",
         note="ユニットコピー弹窗压在编成页上：返回键变暗，不点"),
    # 2026-10-13 (pending --apply): pages a user may leave the game on, back key only.
    Case("item-list-to-home", _s(("item-list-20261012/frames/017.png", "click"), (HOME, "click")), ("nav-back",), "home",
         note="アイテム一覧（消費アイテム）：只按返回键，不点「使う」"),
    Case("item-list-other-tab-to-home", _s(("item-list-20261012/frames/033.png", "click"), (HOME, "click")),
         ("nav-back",), "home", note="アイテム一覧其他标签页"),
    Case("gacha-top-to-home", _s((GACHA_PAGE, "click"), (HOME, "click")),
         ("nav-back",), "home", note="ガシャ页（8.5周年 10連無料 已抽完）：只按返回键，不点抽卡/スタンプ"),
)


def offline_input_nodes(pipeline_dir=COMMON_PIPELINE):
    """The disabled SC.Popup.*/SC.Nav.* Click nodes; only the simulated controller ever enables them."""
    names = []
    for path in sorted(Path(pipeline_dir).glob("*.json")):
        for name, node in json.loads(path.read_text(encoding="utf-8")).items():
            if node.get("action") == "Click":
                require(name.startswith(("SC.Popup.", "SC.Nav.")) and node.get("enabled") is False,
                        f"共享点击节点必须默认禁用: {name}")
                names.append(name)
    return sorted(names)


def build_override(node_names, enable_input):
    override = {name: {"timeout": value} for name, value in OFFLINE_TIMEOUTS.items()}
    for name in node_names:
        override[name] = {"timeout": OFFLINE_CLICK_TIMEOUT}
        if enable_input:
            override[name]["enabled"] = True
    return override


def private_frame(relative):
    path = (PRIVATE / relative).resolve()
    require(path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".png", f"帧须位于dev_assets/private: {relative}")
    return path


def missing_frames(cases=None):
    cases = (CASES + STOP_CASES + PAGE_CASES + PROPOSAL_CASES) if cases is None else cases
    return sorted({image for case in cases for image, _ in case.steps if not private_frame(image).is_file()})


def proposal_context(proposal=None):
    """(pipeline_override nodes, extra click node names, patched table) of the go-home pages proposal, or empty."""
    from tools import go_home_pages as pages

    if proposal is None:
        return {}, [], default_table()
    nodes = pages.merged_pipeline(proposal)
    clicks = sorted(name for name, node in nodes.items() if node.get("action") == "Click")
    require(clicks and all(name.startswith("SC.Nav.") and nodes[name].get("enabled") is False for name in clicks),
            "提案中的点击节点须为默认禁用的 SC.Nav.*")
    patched = pages.patch_table_text(pages.TABLE.read_text(encoding="utf-8"), proposal["table_bindings"])
    return nodes, clicks, parse_table(patched.encode("utf-8"))


def replay(cases=None, bundle=PRIVATE_BUNDLE, proposal=None):
    cases = (CASES + STOP_CASES + PAGE_CASES) if cases is None else cases
    from agent.core.pipeline_frame_guard import install
    from maa.custom_action import CustomAction
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker

    require(Library.version() == FRAMEWORK, f"回放要求MaaFramework {FRAMEWORK}")
    require(cases, "回放用例为空")
    problems = verify_bundle(bundle)
    require(not problems, "私有模板包校验失败：" + "；".join(problems[:5]))
    require(not missing_frames(cases), "缺少私有回放帧：" + ", ".join(missing_frames(cases)[:5]))
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    input_nodes = offline_input_nodes()
    require(input_nodes, "共享 Pipeline 中没有点击节点")
    proposal_nodes, proposal_clicks, table = proposal_context(proposal)
    input_nodes = sorted(set(input_nodes) | set(proposal_clicks))

    class RecordFail(CustomAction):
        calls = 0
        reasons = []

        def run(self, context, argv):
            RecordFail.calls += 1
            RecordFail.reasons.append(fail_reason(argv.custom_action_param))
            return False

    scenario = ReplayScenario([read_png(private_frame(cases[0].steps[0][0]))], [1], allow_input=False, table=table)
    controller = create_replay_controller(scenario)
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            "离线控制器初始化失败")
    resource = Resource()
    require(resource.post_bundle(BASE_BUNDLE).wait().succeeded, "基础资源载入失败")
    require(resource.post_bundle(Path(bundle)).wait().succeeded, "私有模板包载入失败")
    model = ocr_model_dir()
    if model is not None:  # OCR guards (Present, setup pages, stops) need the same model the launcher loads
        require(resource.post_ocr_model(model).wait().succeeded, "OCR 模型载入失败")
    if proposal_nodes:  # new nodes must exist in the resource before the frame binder snapshots the click guards
        require(resource.override_pipeline(proposal_nodes), "提案节点载入失败")
    fail_action = RecordFail()
    require(resource.register_custom_action("SC.FailTask", fail_action), "SC.FailTask 注册失败")
    binder = install(resource, scenario)
    tasker = Tasker()
    require(tasker.bind(resource, controller) and tasker.inited, "离线 Tasker 绑定失败")
    binder.attach(tasker)
    started = time.monotonic()
    results = []
    for case in cases:
        require(time.monotonic() - started < TOTAL_SECONDS, "回放总预算超时")
        images = [read_png(private_frame(image)) for image, _ in case.steps]
        scenario.reset_frames(images, [a for _, a in case.steps], allow_input=case.enable_input)
        RecordFail.calls = 0
        RecordFail.reasons = []
        case_started = time.monotonic()
        job = tasker.post_task(ENTRY, build_override(input_nodes, case.enable_input))
        while not job.done:
            if time.monotonic() - case_started > CASE_SECONDS:
                tasker.post_stop().wait()
                raise RuntimeError(f"用例 {case.id} 超时；已停止且不重试")
            time.sleep(0.01)
        detail = job.get()
        trace = [node.name for node in detail.nodes] if detail else []
        scenario = controller.scenario
        if case.outcome == "home":
            reached = bool(job.succeeded) and bool(trace) and trace[-1] == HOME_END and FAIL_NODE not in trace
        else:
            ended_in_fail = bool(trace) and (FAIL_NODE in trace or trace[-1].startswith(STOP_PREFIX))
            reached = (not job.succeeded) and ended_in_fail and RecordFail.calls == 1
            if case.reason:
                reached = reached and RecordFail.reasons == [case.reason]
        clicks_ok = tuple(scenario.actions) == tuple(case.clicks)
        passed = reached and clicks_ok and not scenario.refused and scenario.other_calls == 0 and scenario.captures > 0
        results.append({"id": case.id, "passed": passed, "expected_outcome": case.outcome,
                        "framework_succeeded": bool(job.succeeded), "outcome_matched": reached,
                        "expected_actions": list(case.clicks), "simulated_actions": list(scenario.actions),
                        "simulated_clicks": [list(c) for c in scenario.clicks],
                        "other_input_calls": scenario.other_calls, "captures": scenario.captures,
                        "final_step": scenario.index, "steps": len(case.steps), "node_trace": trace,
                        "input_nodes_enabled": case.enable_input, "note": case.note,
                        "fail_reasons": list(RecordFail.reasons),
                        "elapsed_seconds": round(time.monotonic() - case_started, 3)})
    maa_sinks.release(tasker)   # the binder's sinks would keep this tasker and its OCR model for the process
    return {"passed": all(r["passed"] for r in results), "offline_only": True, "real_input_attempts": 0,
            "framework_version": Library.version(), "entry": ENTRY, "case_count": len(results),
            "simulated_click_attempts": sum(len(r["simulated_clicks"]) for r in results),
            "offline_timeouts": {**OFFLINE_TIMEOUTS, "click nodes": OFFLINE_CLICK_TIMEOUT},
            "cases": results, "elapsed_seconds": round(time.monotonic() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="SC.Common 共享 Pipeline 离线回放（模拟点击，不连接任何宿主）。")
    parser.add_argument("--report", type=Path, default=None, help="写入 dev_assets/private 下的 JSON 报告")
    parser.add_argument("--case", action="append", default=None, help="只运行指定用例 ID（可重复）")
    parser.add_argument("--proposal", action="store_true",
                        help="合并 docs/proposals/go_home_pages_20261009.json（新增页面的返回节点与动作表绑定）后回放其用例")
    args = parser.parse_args(argv)
    proposal = None
    pool = CASES + STOP_CASES + PAGE_CASES
    if args.proposal:
        from tools import go_home_pages as pages
        proposal = json.loads(pages.PROPOSAL.read_text(encoding="utf-8"))
        pool = PROPOSAL_CASES
    cases = tuple(c for c in pool if not args.case or c.id in args.case)
    try:
        report = replay(cases, proposal=proposal)
    except Exception as exc:  # report every failure as a refusal; never retry
        report = {"passed": False, "offline_only": True, "real_input_attempts": 0, "error": str(exc)}
    if args.report:
        path = args.report.resolve()
        require(path.is_relative_to(PRIVATE.resolve()) and path.suffix.lower() == ".json", "报告须写入dev_assets/private")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"passed": report["passed"], "error": report.get("error"),
               "cases": [(c["id"], c["passed"], c["simulated_actions"], c["node_trace"][-1:] if c["node_trace"] else [])
                         for c in report.get("cases", [])]}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
