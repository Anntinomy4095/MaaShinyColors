"""SC.Common shared Pipeline: static safety contracts and private-frame offline replay."""
import json
import unittest
from pathlib import Path

from agent.core.pipeline_actions import default_table
from tools import build_native_resource as bundle_tool
from tools import replay_common_pipeline as harness
from tools.check_project import inspect_project, load_jsonc

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "assets/resource/base/pipeline/common"
DEFAULT_PIPELINE = ROOT / "assets/resource/base/default_pipeline.json"
POLICY = ROOT / "project_policy.json"
FAIL = ["SC.Common.FailTask"]
# The only clicks the shared Pipeline may ever send (1280x720), per popup/page: the rect of one action in
# assets/input/pipeline_actions.json (the host picks the point inside it, away from the edges).
EXPECTED_TARGETS = {
    "SC.Popup.Announcement.Close": [20, 628, 74, 66],
    "SC.Popup.Reward.Close": [606, 481, 119, 81],
    "SC.Popup.MissionCompleted.Close": [451, 540, 105, 81],
    "SC.Popup.MissionHelp.Close": [640, 562, 85, 78],
    "SC.Popup.MatchHelp.Close": [640, 562, 85, 78],
    # 2026-10-31 「マッチライブ更新」 season notice: inside its 閉じる (tests/test_match_season_popup.py).
    "SC.Popup.MatchSeason.Close": [600, 526, 80, 44],
    "SC.Popup.TrainingHelp.Close": [640, 562, 85, 78],
    "SC.Popup.FesHelp.Close": [640, 562, 85, 78],
    # 2026-11-23: any first-visit help (assistant icon + 閉じる), e.g. Shop, 営業, Collabo, on a new browser profile.
    "SC.Popup.AnyHelp.Close": [640, 562, 85, 78],
    "SC.Popup.Pickup.Close": [1091, 48, 42, 38],
    "SC.Popup.Story.Close": [1193, 33, 56, 45],
    # 新ガシャ開催！ cut-in: the lower part of the right-hand SKIP only (never 全動画SKIP or Touch).
    "SC.Popup.NewGacha.Close": [1186, 78, 70, 15],
    **{f"SC.Nav.{page}.Back": [20, 628, 74, 66] for page in
       ("Mission", "IdolPortal", "TrainingList", "TrainingSelected", "MatchMain", "FesMain", "ShopList")},
    # B5b (coordinator request): GoHome may close a left-over Match スキップ dialog with キャンセル (non-consuming).
    # It is the only common click whose guard uses native/match_skip/ templates; スキップする is never a common target.
    "SC.Nav.MatchSkip.Cancel": [382, 621, 69, 38],
    # 2026-10-19/20 supervised samples: non-consuming closes on the business-return event chain
    # (fan-reward notice OK → Producers Cup help 閉じる → receipt OK → event page back), the Fes season-end reward OK
    # (never ショップへ), and the birthday cut-in SKIP (the story SKIP rect). Receipt OK is clipped to x<606 so the
    # nav/popup families stay coordinate-disjoint.
    "SC.Popup.EventFanReward.Close": [558, 391, 164, 76],
    "SC.Popup.ProduceEventHelp.Close": [640, 562, 85, 78],
    "SC.Popup.EventFanReceipt.Close": [562, 550, 42, 64],
    # 2026-10-26 live: the 育成イベント 「ミッション達成累積報酬」 receipt (items already sent) closes on the reward OK.
    "SC.Popup.EventMissionReceipt.Close": [606, 481, 119, 81],
    "SC.Nav.ProduceEvent.Back": [20, 628, 74, 66],
    # 2026-11-01 live: the OK can open a シナリオイベント page instead: 「シナリオ再生確認」 closes on キャンセル (never
    # 再生する), then the page ← returns home.
    "SC.Popup.ScenarioPlay.Cancel": [470, 436, 140, 60],
    "SC.Nav.ScenarioEvent.Back": [20, 628, 74, 66],
    "SC.Popup.FesPeriodReward.Close": [470, 627, 65, 18],
    "SC.Popup.Birthday.Close": [1193, 33, 56, 45],
    # 2026-10-27 user: the full-screen gacha announcement closes on one tap below its 開催期間 pill.
    "SC.Popup.GachaAnnouncement.Close": [560, 640, 160, 22],
}
# Reasoned stops: known pages/dialogs GoHome never clicks or leaves; they end the task with a specific SC.FailTask reason.
STOPS = {"SC.Common.Stop.ProduceInProgress": "produce_in_progress", "SC.Common.Stop.BusinessReport": "business_report_pending",
         "SC.Common.Stop.GradeLive": "grade_live_in_progress", "SC.Common.Stop.Maintenance": "maintenance",
         "SC.Common.Stop.CommunicationError": "communication_error",
         "SC.Common.Stop.StoryUnverified": "story_overlay_unverified"}
HOME_STOPS = {"SC.Common.Stop.FesMenuOpen": "fes_menu_open"}
# 2026-10-27: the static gacha announcement left after a cut-in SKIP (or already open) ends with its own reason; it is
# tried only when the wait for a known screen failed (no per-round cost, no click). tests/test_gacha_announcement_stop.py.
GACHA_STOP_ERROR = ["SC.Common.Stop.GachaAnnouncement"] + FAIL
GACHA_CUTIN_CLICKS = ("SC.Popup.NewGacha.Close", "SC.Popup.Birthday.Close", "SC.Popup.GachaAnnouncement.Close")
DIALOG_EXCEPTIONS = {"SC.Nav.MatchSkip.Cancel": "native/match_skip/"}
# Template groups that must never be part of an automatic click guard.
FORBIDDEN_TEMPLATE_GROUPS = ("native/shop_confirm/", "native/shop_result/", "native/difficulty/",
                             "native/match_skip/", "native/start_screen/")

try:
    from maa.library import Library
    HAVE_MAA = Library.version() == harness.FRAMEWORK
except Exception:  # pragma: no cover - environment without MaaFramework
    HAVE_MAA = False


def expected_targets():
    """The whitelist plus the page back nodes ``tools/go_home_pages.py --apply`` landed in ``common/pages.json`` (pages
    added to the registry later stay in the proposal until the next --apply)."""
    from tools import go_home_pages as pages

    applied = load_jsonc(COMMON / "pages.json") if (COMMON / "pages.json").is_file() else {}
    return {**EXPECTED_TARGETS, **{name: list(pages.BACK_RECT) for name, node in applied.items()
                                   if node.get("action") == "Click"}}


def load_common():
    nodes = {}
    for path in sorted(COMMON.glob("*.json")):
        for name, node in load_jsonc(path).items():
            assert name not in nodes, name
            nodes[name] = node
    return nodes


def sub_names(node):
    return node.get("all_of", node.get("any_of", []))


def templates_of(name, nodes, seen=None):
    seen = set() if seen is None else seen
    if name in seen:
        return set()
    seen.add(name)
    node = nodes[name]
    template = node.get("template")
    found = set(template) if isinstance(template, list) else ({template} if template else set())
    for item in sub_names(node):
        found |= templates_of(item, nodes, seen)
    return found


class CommonPipelineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = load_common()
        cls.clicks = {n: v for n, v in cls.nodes.items() if v.get("action") == "Click"}

    def test_every_node_uses_project_prefix(self):
        self.assertTrue(all(name.startswith("SC.") for name in self.nodes))

    def test_default_pipeline_has_finite_timeout_and_rate_limit(self):
        default = json.loads(DEFAULT_PIPELINE.read_text(encoding="utf-8"))["Default"]
        self.assertTrue(0 < default["timeout"] <= 30000)
        self.assertTrue(0 < default["rate_limit"] <= 2000)
        self.assertFalse((ROOT / "assets/resource/base/pipeline/defaults.json").exists())

    def test_click_nodes_are_exactly_the_whitelist_and_disabled(self):
        targets = expected_targets()
        self.assertEqual(set(self.clicks), set(targets))
        for name, node in self.clicks.items():
            with self.subTest(name=name):
                self.assertIs(node["enabled"], False)
                self.assertEqual(node["target"], targets[name])
                self.assertEqual(node["recognition"], "And")
                self.assertGreaterEqual(len(node["all_of"]), 3)
                self.assertTrue(1 <= node["max_hit"] <= 8)
                self.assertEqual(node["on_error"], GACHA_STOP_ERROR if name in GACHA_CUTIN_CLICKS else FAIL)
                self.assertTrue(0 < node["timeout"] <= 30000)

    def test_after_click_requires_a_different_known_state(self):
        for name, node in self.clicks.items():
            with self.subTest(name=name):
                guard = name.rsplit(".", 1)[0] + ".Guard"
                self.assertEqual(node["next"], [name.rsplit(".", 1)[0] + ".Settled"])
                settled = self.nodes[node["next"][0]]
                self.assertEqual(settled["recognition"], "Or")
                self.assertNotIn(guard, settled["any_of"])
                self.assertNotIn("next", settled)
        # The login-board SKIP is the story SKIP; skipping a story must not count it as progress.
        self.assertNotIn("SC.Popup.LoginBoard", self.nodes["SC.Popup.Story.Settled"]["any_of"])
        self.assertIn("SC.Common.Color.SkipUndimmed", self.nodes["SC.Popup.LoginBoard"]["all_of"])

    def test_click_guard_equals_recognition_guard(self):
        for name, node in self.clicks.items():
            guard = self.nodes[name.rsplit(".", 1)[0] + ".Guard"]
            self.assertEqual(node["all_of"], guard["all_of"], name)

    def test_popup_whitelist_contracts(self):
        # Applied go-home pages (common/pages.json) reuse task page guards (present/business/grade/card_scan files).
        everything = {}
        for path in sorted(COMMON.parent.rglob("*.json")):
            everything.update(load_jsonc(path))
        everything.update(self.nodes)
        templates = {n: templates_of(n, everything) for n in self.clicks}
        for kind in ("MissionHelp", "MatchHelp", "TrainingHelp", "FesHelp"):
            found = templates[f"SC.Popup.{kind}.Close"]
            prefix = "native/" + {"MissionHelp": "mission_help", "MatchHelp": "match_help",
                                  "TrainingHelp": "training_help", "FesHelp": "fes_help"}[kind]
            self.assertTrue({prefix + "/title.png", prefix + "/page.png", prefix + "/close.png"} <= found, kind)
        self.assertEqual(templates["SC.Popup.Reward.Close"],
                         {"native/reward/title.png", "native/reward/body.png", "native/reward/ok.png"})
        self.assertIn("native/mission_completed/ok.png", templates["SC.Popup.MissionCompleted.Close"])
        self.assertNotIn("native/mission_completed/mission_target.png", templates["SC.Popup.MissionCompleted.Close"])
        self.assertIn("native/pickup/close.png", templates["SC.Popup.Pickup.Close"])
        for name, found in templates.items():
            for template in found:
                if name in DIALOG_EXCEPTIONS and template.startswith(DIALOG_EXCEPTIONS[name]):
                    continue
                self.assertFalse(template.startswith(FORBIDDEN_TEMPLATE_GROUPS), (name, template))
        self.assertIn("native/match_skip/cancel.png", templates["SC.Nav.MatchSkip.Cancel"])

    def test_new_gacha_cutin_presses_only_skip(self):
        node = self.clicks["SC.Popup.NewGacha.Close"]
        # 2026-10-20 live: the per-period title art changed (新アイドル登場！ preview did not match title.png), so the
        # guard binds to the cut-in's own button pair instead: 全動画SKIP + SKIP (neither on the login board or a story)
        # plus the undimmed SKIP colour.
        self.assertEqual(templates_of("SC.Popup.NewGacha.Close", self.nodes),
                         {"native/new_gacha/skip_all.png", "native/new_gacha/skip.png"})
        self.assertIn("SC.Common.Color.SkipUndimmed", node["all_of"])
        self.assertEqual(node["max_hit"], 4)
        x, y, w, h = node["target"]
        skip, skip_all, touch = (1182, 18, 79, 79), (1082, 18, 79, 79), (561, 549, 152, 45)
        self.assertTrue(skip[0] <= x and x + w <= skip[0] + skip[2] and skip[1] <= y and y + h <= skip[1] + skip[3])
        for other in (skip_all, touch):
            self.assertTrue(x + w <= other[0] or other[0] + other[2] <= x or y + h <= other[1] or other[1] + other[3] <= y)
        # The cut-in's SKIP matches the login-board SKIP (score 0.994), so neither may count as leaving the cut-in;
        # every other general Settled list (those that know the story popup) knows the cut-in.
        settled = self.nodes["SC.Popup.NewGacha.Settled"]["any_of"]
        self.assertNotIn("SC.Popup.LoginBoard", settled)
        self.assertNotIn("SC.Popup.NewGacha.Guard", settled)
        # The birthday cut-in has the same 全動画SKIP/SKIP pair, so both also match an unchanged birthday frame.
        birthday = self.nodes["SC.Popup.Birthday.Settled"]["any_of"]
        self.assertTrue({"SC.Popup.LoginBoard", "SC.Popup.NewGacha.Guard"}.isdisjoint(birthday), birthday)
        for name, value in self.nodes.items():
            if (name.endswith(".Settled") and name != "SC.Popup.NewGacha.Settled"
                    and "SC.Popup.Story.Guard" in value.get("any_of", [])):
                self.assertIn("SC.Popup.NewGacha.Guard", value["any_of"], name)

    def test_undimmed_proofs_guard_full_pages_and_home(self):
        for name in self.clicks:
            if (name.startswith("SC.Nav.") or name == "SC.Popup.Announcement.Close") and name not in DIALOG_EXCEPTIONS:
                self.assertIn("SC.Common.Color.BackUndimmed", self.clicks[name]["all_of"], name)
        self.assertIn("SC.Common.Color.FesMainUndimmed", self.clicks["SC.Nav.FesMain.Back"]["all_of"])
        self.assertIn("SC.Common.Color.HomeUndimmed", self.nodes["SC.Common.Home.Guard"]["all_of"])
        self.assertEqual(self.nodes["SC.Common.HomeReached"]["all_of"], self.nodes["SC.Common.Home.Guard"]["all_of"])

    def test_home_mission_anchor_avoids_the_badge_and_background(self):
        # Dynamic-element rule: the ミッション count badge (x 69..102, y 382..416) comes and goes, and the room
        # background around the white key changes; the Home guard keeps two static anchors plus the undimmed check.
        guard = self.nodes["SC.Common.Home.Guard"]["all_of"]
        self.assertEqual(guard, ["SC.Common.Anchor.Home.FesText", "SC.Common.Variant.HomeMission",
                                 "SC.Common.Color.HomeUndimmed"])
        self.assertEqual(self.nodes["SC.Common.Variant.HomeMission"]["any_of"], ["SC.Common.Anchor.Home.MissionLabel"])
        x, y, w, h = self.nodes["SC.Common.Anchor.Home.MissionLabel"]["roi"]
        self.assertTrue(y >= 417 and x >= 28 and x + w <= 96 and y + h <= 460, (x, y, w, h))
        self.assertNotIn("SC.Common.Anchor.Home.MissionNative", self.nodes)
        fes = self.nodes["SC.Common.Anchor.Home.FesText"]["roi"]
        self.assertLessEqual(fes[1] + fes[3], 672)  # above the Fes rights gauge / recovery timer

    def test_go_home_order_and_failure_paths(self):
        go_home = self.nodes["SC.Common.GoHome"]
        self.assertEqual(go_home["next"], ["[JumpBack]SC.Popup.CloseKnown", "[JumpBack]SC.Loading.Wait",
                                           "SC.Common.HomeReached", "[JumpBack]SC.Nav.BackOne"])
        self.assertEqual(go_home["on_error"], GACHA_STOP_ERROR)
        self.assertTrue(0 < go_home["timeout"] <= 60000)
        # 2026-10-12 live: 「ミッション達成」 can pop up just after home matched (after training); close it via the
        # whitelist and re-check, instead of failing the task.
        self.assertEqual(self.nodes["SC.Common.HomeReached"]["next"],
                         ["SC.Common.HomeClean", "[JumpBack]SC.Popup.CloseKnown", "[JumpBack]SC.Loading.Wait"])
        clean = self.nodes["SC.Common.HomeClean"]
        self.assertIs(clean["inverse"], True)
        self.assertIn("SC.Common.Presence.FesMenu.MatchLabel", clean["any_of"])
        for dispatcher, prefix in (("SC.Popup.CloseKnown", "SC.Popup."), ("SC.Nav.BackOne", "SC.Nav.")):
            node = self.nodes[dispatcher]
            self.assertEqual(node["next"][-1], prefix + "NotHandled")
            # FesResume stops without input; B6c borrows Training's one guarded, non-consuming result close.
            # The gacha-announcement stop follows its own close (input disabled or the close already used).
            exceptions = ({"SC.Popup.FesResume.Stop", "SC.Training.Result.Close", "SC.Common.Stop.GachaAnnouncement"}
                          if prefix == "SC.Popup." else set(STOPS))
            self.assertEqual(set(node["next"][:-1]), {n for n in self.clicks if n.startswith(prefix)} | exceptions)
            self.assertEqual(self.nodes[prefix + "NotHandled"]["next"], FAIL)
        fail = self.nodes["SC.Common.FailTask"]
        self.assertEqual((fail["action"], fail["custom_action"]), ("Custom", "SC.FailTask"))
        self.assertNotIn("next", fail)

    def test_unverified_story_overlay_stops_without_skip(self):
        """2026-10-13: an event-skinned idle story (Halloween home story; no native sample yet) only near-matches the
        verified SKIP/LOG/HIDE. It stops with its own reason; the SKIP click stays bound to the full 0.95 Story guard."""
        stop = self.nodes["SC.Common.Stop.StoryUnverified"]
        self.assertEqual(stop["all_of"], ["SC.Common.Presence.Story.Skip", "SC.Common.Presence.StoryControls"])
        self.assertEqual(self.nodes["SC.Common.Presence.StoryControls"]["any_of"],
                         ["SC.Common.Presence.Story.Log", "SC.Common.Presence.Story.Hide"])
        # The login board shares the SKIP button but has no LOG/HIDE column, so it never reaches this stop.
        self.assertNotIn("SC.Common.Color.SkipUndimmed", json.dumps(stop))
        for part in ("Skip", "Log", "Hide"):
            presence, anchor = (self.nodes[f"SC.Common.{kind}.Story.{part}"] for kind in ("Presence", "Anchor"))
            self.assertEqual((presence["template"], presence["roi"]), (anchor["template"], anchor["roi"]))
            self.assertLess(presence["threshold"], anchor["threshold"])
        self.assertEqual(self.nodes["SC.Popup.Story.Close"]["all_of"],
                         ["SC.Common.Anchor.Story.Skip", "SC.Common.Anchor.Story.Log", "SC.Common.Anchor.Story.Hide"])
        back = self.nodes["SC.Nav.BackOne"]
        self.assertIn("SC.Common.Stop.StoryUnverified", back["any_of"])
        self.assertEqual(back["next"][-2:], ["SC.Common.Stop.StoryUnverified", "SC.Nav.NotHandled"])

    def test_shared_training_result_close_keeps_its_guard_and_failure_path(self):
        training = load_jsonc(ROOT / "assets/resource/base/pipeline/training.json")
        name = "SC.Training.Result.Close"
        self.assertNotIn(name, self.nodes)  # reuse the Training node; no second generic close click
        self.assertEqual(self.nodes["SC.Popup.CloseKnown"]["next"][-2:], [name, "SC.Popup.NotHandled"])
        self.assertIn("SC.Training.ResultAny.Guard", self.nodes["SC.Popup.CloseKnown"]["any_of"])
        close = training[name]
        action = default_table().get("training-result-close")
        self.assertFalse(action.consuming)
        self.assertEqual(action.nodes, (name,))
        self.assertEqual((close["enabled"], close["recognition"], close["action"], close["target"]),
                         (False, "And", "Click", list(action.rect)))
        self.assertEqual(close["all_of"], ["SC.Training.ResultAny.Guard", "SC.Training.Check.Result"])
        self.assertEqual(training["SC.Training.Check.Result"]["custom_recognition_param"], {"stage": "result"})
        self.assertEqual(close["on_error"], ["SC.Training.Abort"])
        self.assertTrue(0 < close["timeout"] <= 30000 and 1 <= close["max_hit"] <= 2)
        self.assertEqual(close["next"], ["SC.Training.Result.Closed"])
        self.assertNotIn("SC.Training.ResultAny.Guard", training["SC.Training.Result.Closed"]["any_of"])

    def test_template_thresholds(self):
        for name, node in self.nodes.items():
            if node.get("recognition") == "TemplateMatch":
                expected = 0.80 if name.startswith("SC.Common.Presence.") else 0.95
                self.assertEqual(node["threshold"], expected, name)
                self.assertTrue(node["template"].startswith(bundle_tool.TEMPLATE_PREFIX), name)

    def test_every_pipeline_template_is_built_by_the_bundle_tool(self):
        built = set(bundle_tool.COPIED) | set(bundle_tool.CROPPED)
        self.assertLessEqual(set(bundle_tool.pipeline_templates(COMMON)), built)
        for name, rois in bundle_tool.pipeline_templates(COMMON).items():
            calibrated = bundle_tool.COPIED[name][2] if name in bundle_tool.COPIED else bundle_tool.CROPPED[name][1]
            self.assertTrue(all(roi == calibrated for roi in rois), name)

    def test_policy_keeps_live_input_closed(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        # Live input may be enabled by the user (ADR-011); no node is enabled or allowlisted statically.
        self.assertEqual(policy["allowed_live_input_nodes"], [])
        self.assertTrue(set(expected_targets()).isdisjoint(policy["offline_harness"]["allowed_input_nodes"]))
        self.assertEqual(set(harness.offline_input_nodes()), set(expected_targets()))

    def test_runtime_bundle_order(self):
        from agent.core import resource_bundles
        paths = resource_bundles.bundle_paths()
        self.assertEqual(paths[0], ROOT / "assets/resource/base")
        self.assertEqual(resource_bundles.bundle_paths(include_private=False), paths[:1])
        if len(paths) > 1:
            self.assertEqual(paths[1], ROOT / "dev_assets/private/resource_native")

    def test_project_check_accepts_common_pipeline(self):
        result = inspect_project(ROOT)
        self.assertEqual(result.errors, [])

    def test_fail_reason_is_bounded(self):
        from agent.core.task_failure import fail_reason
        self.assertEqual(fail_reason('{"reason": "unknown"}'), "unknown")
        self.assertEqual(fail_reason("not json"), "unspecified")
        self.assertEqual(fail_reason(None), "unspecified")
        self.assertEqual(len(fail_reason(json.dumps({"reason": "x" * 500}))), 120)


ALL_CASES = harness.CASES + harness.STOP_CASES + harness.PAGE_CASES
PRIVATE_READY = HAVE_MAA and not bundle_tool.verify() and not harness.missing_frames()


@unittest.skipUnless(PRIVATE_READY, "需要MaaFramework v5.12.3、私有模板包（tools/build_native_resource.py）与私有原生帧")
class CommonPipelineReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = harness.replay()
        cls.by_id = {case["id"]: case for case in cls.report["cases"]}

    def test_all_cases_pass_without_real_input(self):
        failed = [c["id"] for c in self.report["cases"] if not c["passed"]]
        self.assertEqual(failed, [])
        self.assertEqual(self.report["real_input_attempts"], 0)
        self.assertEqual(len(self.report["cases"]), len(ALL_CASES))

    def test_known_popups_get_exactly_their_close_click_then_home(self):
        for case in ALL_CASES:
            if case.outcome == "home":
                result = self.by_id[case.id]
                self.assertEqual(result["simulated_actions"], list(case.clicks), case.id)
                self.assertEqual(result["node_trace"][-1], harness.HOME_END, case.id)

    def test_consumption_and_unknown_frames_never_click(self):
        # Once common/pages.json is applied the Grade 难度 / 参加ユニット pages get only the back key (harness.PAGES_APPLIED).
        grade = () if harness.PAGES_APPLIED else ("grade-unit-tension", "grade-difficulty", "grade-difficulty-money2x")
        for case_id in ("shop-exchange-confirm", "shop-exchange-result", *grade,
                        "training-confirm-dialog", "home-with-fes-menu", "communication-error", "start-screen",
                        "fes-help-fading-in", "match-skip-submitted", "training-submit-response",
                        "reward-input-disabled", "new-gacha-without-skip-buttons", "home-no-badge-dimmed",
                        "stop-story-variant"):
            result = self.by_id[case_id]
            self.assertEqual(result["simulated_clicks"], [], case_id)
            self.assertTrue(harness.FAIL_NODE in result["node_trace"]
                            or result["node_trace"][-1].startswith(harness.STOP_PREFIX), case_id)
            self.assertFalse(result["framework_succeeded"], case_id)

    def test_ineffective_click_is_not_repeated(self):
        for case_id in ("pickup-click-no-effect", "reward-click-no-effect", "mission-back-no-effect",
                        "new-gacha-click-no-effect", "new-gacha-skip-then-purchase-dialog",
                        "new-gacha-skip-then-unknown-promo", "birthday-click-no-effect", "new-idol-click-no-effect",
                        "gacha-banner-click-no-effect"):
            result = self.by_id[case_id]
            self.assertEqual(len(result["simulated_clicks"]), 1, case_id)
            self.assertTrue(harness.FAIL_NODE in result["node_trace"]
                            or result["node_trace"][-1].startswith(harness.STOP_PREFIX), case_id)


if __name__ == "__main__":
    unittest.main()
