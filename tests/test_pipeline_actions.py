"""B3.5: the single-source pipeline action table shared by the Python controller and the C# host."""
import hashlib
import json
import random
import re
import unittest
from pathlib import Path

from agent.core import pipeline_actions as pa

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "assets/input/pipeline_actions.json"
CSPROJ = ROOT / "browser_host/MaaShinyColors.BrowserHost.csproj"
SELFTEST = ROOT / "browser_host/PipelineInputSelfTest.cs"


class ActionTableTests(unittest.TestCase):
    def setUp(self):
        self.table = pa.default_table()

    def test_host_reads_the_table_file_instead_of_embedding_it(self):
        # 2026-10-10: the host reads and validates assets/input/pipeline_actions.json at startup and on
        # 「重新载入动作表」 (PipelineActionTable.Load/Reload), so a table change needs a reload, never a rebuild.
        self.assertEqual(pa.table_path(), TABLE_PATH)
        self.assertEqual(self.table.sha256, hashlib.sha256(TABLE_PATH.read_bytes()).hexdigest())
        csproj = CSPROJ.read_text(encoding="utf-8")
        self.assertNotIn("SC.PipelineActions.json", csproj)
        self.assertIsNone(re.search(r'<EmbeddedResource Include="[^"]*pipeline_actions\.json"', csproj))
        source = (CSPROJ.parent / "PipelineActionTable.cs").read_text(encoding="utf-8-sig")
        self.assertIn('Path.Combine("assets", "input", "pipeline_actions.json")', source)
        self.assertIn("RequireNodeBinding", source)

    def test_vocabulary_keeps_consuming_actions_and_disabled_training_catalog(self):
        ids = ["game-start", "startup-collabo-report-ok", "popup-reward-ok", "popup-event-fan-reward-ok", "popup-event-fan-receipt-ok",
               "popup-fes-period-reward-ok", "popup-mission-completed-ok", "popup-help-close", "popup-pickup-close",
               "popup-story-skip", "popup-new-gacha-skip", "popup-gacha-announcement-close", "popup-match-season-close",
               "popup-scenario-play-cancel", "nav-back", "home-mission", "home-shop", "shop-money-tab", "shop-offer-open-1",
               "shop-offer-open-2", "shop-offer-open-3", "shop-exchange-cancel", "shop-exchange-confirm",
               "shop-result-close", "home-idol", "idol-training", "training-select-card", "training-bronze-plus", "training-bronze-plus-top",
               "training-submit", "training-confirm-cancel", "training-start", "training-result-close", "home-fes",
               "fes-match-entry", "fes-grade-entry", "grade-join-open", "grade-mode-grade", "grade-mode-rehearsal",
               "grade-rehearsal-easy", "grade-rehearsal-normal", "grade-rehearsal-hard", "grade-difficulty-easy",
               "grade-difficulty-normal", "grade-difficulty-hard", "grade-tension-confirm", "grade-fes-resume", "match-latest-area-up", "match-latest-area-down", "match-latest-area-open", "match-latest-back", "match-skip-open",
               "match-skip-cancel", "match-skip-plus-1", "match-skip-plus-2", "match-skip-plus-3", "match-skip-plus-4",
               "match-skip-plus-5", "match-skip-plus-6", "match-skip-clear", "match-skip-confirm", "match-skip-review", "match-skip-review-cancel",
               "match-skip-result-close", "match-skip-scroll-down",
               "match-skip-scroll-up",
               # 2026-10-31 Match 推进 (match_play): node-bound; ライブ開始 consumes one フェス参加権.
               "match-play-area-next", "match-play-area-open", "match-play-stage-decide", "match-play-back", "match-play-unit-open",
               *(f"match-play-unit-{n}" for n in range(1, 11)), "match-play-omakase", "match-play-live-start", "match-play-touch",
               "match-play-skip", "match-play-next-stage", "match-play-area-select", "match-play-lose-close", "live-card-1", "live-card-2", "live-card-3", "live-memory-appeal", "live-memory-film", "live-judge-vo",
               "live-judge-da", "live-judge-vi", "live-appeal-timing", "live-speed-toggle", "live-report-ok", "live-record-dismiss",
               "home-business", "business-slot-complete-1", "business-slot-complete-2", "business-slot-complete-3",
               "business-report-next", "business-slot-open-1", "business-slot-open-2", "business-slot-open-3",
               "business-bulk-complete", "business-bulk-dispatch", "business-report-next-page",
               "business-reward-redispatch", "business-dispatch-decide"]
        reserved = [f"training-list-slot-r{r}c{c}" for r in range(1, 4) for c in range(1, 8)
                    if (r, c) != (1, 2)] + [f"training-list-slot-{ph}-r{r}c{c}" for ph, rows in (("b", 3), ("c", 2))
                                           for r in range(1, rows + 1) for c in range(1, 8)] + ["training-list-scroll-down", "training-list-scroll-up", "training-list-direction-toggle",
                                           "training-list-sort-open",
                                           *[f"training-sort-key-r{r}c{c}" for r in range(1, 4) for c in range(1, 4)],
                                           "training-sort-apply",
                                           "idol-portal-list", "idol-list-tab-produce", "idol-list-tab-support",
                                           "idol-list-scroll-down", "idol-list-scroll-up"]
        knowhow = ["idol-list-tab-knowhow", "knowhow-sort-open", "knowhow-sort-key-default", "knowhow-sort-key-eval",
                   "knowhow-sort-key-date",
                   "knowhow-sort-apply", "knowhow-grid-scroll-down", "knowhow-grid-scroll-up", "knowhow-detail-scroll-down",
                   *[f"knowhow-tile-r{r}c{c}" for r in range(3) for c in range(5)],   # 2026-10-12 KnowhowScan (supervised-only)
                   *[f"knowhow-slot-y{k}c{c}" for k in range(6) for c in range(5)],   # drifting-grid slot taps
                   "knowhow-filter-open", "knowhow-filter-scroll-down", "knowhow-filter-scroll-up",
                   "knowhow-filter-cancel",   # 絞り込み popup calibration (never ticks, resets or applies)
                   "knowhow-grid-jump-down", "knowhow-grid-jump-up",          # 2026-10-25 jump probe (calibration only)
                   "knowhow-grid-track-upper", "knowhow-grid-track-lower",
                   "knowhow-detail-protect"]   # 2026-10-26 lock new books after W.I.N.G. (admitted only for a run with locking on)
        present = ["home-present", "present-unclaimed-tab", "present-bulk-receive", "present-confirm", "present-result-close",
                   "present-list-scroll-down"]
        mission = ["mission-login-claim", "mission-result-close", "mission-list-top", "mission-list-bottom", "mission-tab-weekly", "mission-tab-daily",
                   "mission-bulk-receive", "mission-bulk-confirm", "mission-bulk-result-close"]
        # 2026-10-22 weekly official Auto Produce: node-bound, shipped disabled; 2026-10-25
        # the two navigation actions are enabled for the supervised read-only sample (±/決定/dialog stay off).
        weekly = ["weekly-auto-home-produce",
                  # 2026-11-01: collect a finished Auto (オートプロデュース終了 / オートリザルト OK), leave a running one.
                  "weekly-auto-home-running", "weekly-auto-end-ok", "weekly-auto-result-ok", "weekly-auto-running-cancel",
                  "weekly-auto-entry", "weekly-auto-count-plus", "weekly-auto-count-minus",
                  "weekly-auto-decide", "weekly-auto-dialog-cancel", "weekly-auto-skip", "weekly-auto-start",
                  # 2026-10-30: Maa sets an empty オート1 through the game's wizard (non-consuming).
                  "weekly-auto-row-set", "weekly-auto-setup-tab-train", "weekly-auto-setup-tab-special",
                  "weekly-auto-setup-scenario-next", *[f"weekly-auto-setup-unit-{n}" for n in range(1, 11)],
                  "weekly-auto-setup-unit-next", "weekly-auto-setup-book-history", "weekly-auto-setup-history-call",
                  "weekly-auto-setup-history-back", "weekly-auto-setup-book-omakase",
                  "weekly-auto-setup-book-next", "weekly-auto-setup-item-decide",
                  # 2026-10-30 Auto settings: scenario list, units 11–20, プリセット, 育成方針.
                  "weekly-auto-setup-scenario-down", "weekly-auto-setup-unit-page-prev",
                  "weekly-auto-setup-unit-page-next", "weekly-auto-setup-book-preset", "weekly-auto-setup-preset-set-1",
                  "weekly-auto-setup-preset-set-2", "weekly-auto-policy-open", "weekly-auto-policy-vocal",
                  "weekly-auto-policy-dance", "weekly-auto-policy-visual", "weekly-auto-policy-set",
                  "weekly-auto-policy-cancel", "weekly-auto-loop-off",
                  # 2026-10-30 ループ with リカバリーソーダ (ADR-013); 2026-10-31 带书「ノウハウ页指定」 book picker.
                  "weekly-auto-loop-on", "weekly-auto-soda-30", "weekly-auto-soda-60", "weekly-auto-soda-100",
                  "weekly-auto-recovery-cancel", "weekly-auto-soda-start", "weekly-auto-book-slot1",
                  "weekly-auto-picker-decide", *(f"weekly-auto-picker-tile-r{r}c{c}" for r in range(3) for c in range(5))]
        # 2026-11-18 Collabo Fes: node-bound, shipped disabled; テンション決定 consumes one コラボフェスパス, the panel
        # move is the first drag-only action (SC.PipelineDrag.v1).
        collabo = [a["id"] for a in json.loads(TABLE_PATH.read_text(encoding="utf-8"))["actions"]
                   if a["family"] == "collabo-fes"]
        self.assertEqual(len(collabo), 32)   # 2026-11-19: + collabo-unit-back, collabo-full-close
        self.assertEqual([i for i in collabo if self.table.get(i).consuming], ["collabo-tension-decide"])
        self.assertEqual([i for i in collabo if self.table.get(i).drag_rect], ["collabo-panel-drag"])
        self.assertEqual([i for i in self.table.ids if not i.startswith("wing-")],
                         ids + reserved + knowhow + present + mission + weekly + collabo)
        for action_id in weekly:
            action = self.table.get(action_id)
            self.assertEqual(action.enabled, action_id != "weekly-auto-skip", action_id)   # 2026-10-29: all but スキップ
            self.assertEqual(action.family, "weekly-auto", action_id)
            self.assertEqual(len(action.nodes), 1, action_id)
            self.assertTrue(action.nodes[0].startswith("SC.WeeklyProduce."), action_id)
            self.assertEqual(action.consuming, action_id in ("weekly-auto-skip", "weekly-auto-start", "weekly-auto-soda-start"),
                             action_id)
        # W.I.N.G.: generated catalog appended last, bound to SC.Wing.Click.* only. 2026-10-10/11: after the
        # supervised live win the user approved the W.I.N.G. operations, so live-verified wing-* actions ship enabled;
        # the rule (not a hard-coded list): a target whose source is still UNVERIFIED stays disabled.
        from tools import wing_nav_build
        catalog = wing_nav_build.action_catalog()
        self.assertEqual([i for i in self.table.ids if i.startswith("wing-")], [a["id"] for a in catalog])
        raw_actions = {a["id"]: a for a in json.loads(TABLE_PATH.read_bytes())["actions"]}
        from agent.core import wing_nav as wnav
        verified = {wnav.action_id(i): v for i, (_, v) in wnav.TARGETS.items()}
        for action in catalog:
            entry = self.table.get(action["id"])
            self.assertEqual(entry.nodes, tuple(action["nodes"]), action["id"])
            self.assertTrue(all(n.startswith("SC.Wing.Click.") for n in entry.nodes), action["id"])
            if "UNVERIFIED" in str(raw_actions[action["id"]].get("source", "")):
                self.assertFalse(entry.enabled, action["id"])
            # 2026-10-11 review P1-5: enabled exactly when the Agent target is verified live (2026-11-04 H2c: the say "Halo"
            # in-run taps too; the two untapped 目標ライブ cards stay unverified).
            self.assertEqual(entry.enabled, verified[action["id"]], action["id"])
        self.assertEqual([a for a in raw_actions.values() if a["id"].startswith("wing-")],
                         wing_nav_build.action_catalog(enabled=True))
        for unverified in ("wing-reward-reroll", "wing-reward-reroll-confirm", "wing-reward-book-protect",
                           ):   # wing-home-produce / wing-produce-tab-train verified on 2026-10-12 live frames
            self.assertFalse(self.table.get(unverified).enabled, unverified)
        # Consuming wing-* actions are gated: the start only by --allow-produce-spend (one grant per launcher run),
        # the re-roll OK never (not a launcher spend action, and disabled).
        from tools import run_tasks
        self.assertTrue(self.table.get("wing-produce-start").consuming)
        self.assertNotIn("wing-produce-start", run_tasks.wing_task_actions(()))
        self.assertIn("wing-produce-start", run_tasks.wing_task_actions(("wing-produce-start",)))
        self.assertNotIn("wing-reward-reroll-confirm", run_tasks.SPEND_ACTIONS_ALLOWED)
        self.assertNotIn("wing-reward-reroll-confirm", run_tasks.wing_task_actions(("wing-produce-start",)))
        self.assertEqual(self.table.version, 2)
        card_scan = ["idol-portal-list", "idol-list-tab-produce", "idol-list-tab-support", "idol-list-scroll-down",
                     "idol-list-scroll-up"]   # enabled 2026-10-08 for the supervised CardScan entry only
        self.assertTrue(all(self.table.get(i).enabled and not self.table.get(i).consuming for i in card_scan))
        # 2026-10-11: the user approved the training list scroll (∓120 wheel) and the direction toggle.
        training_list = ["training-list-scroll-down", "training-list-scroll-up", "training-list-direction-toggle",
                         # 2026-10-11: sort key popup calibrated on native sample-live-20261011/15-20
                         "training-list-sort-open", *[f"training-sort-key-r{r}c{c}" for r in range(1, 4) for c in range(1, 4)],
                         "training-sort-apply"]
        self.assertTrue(all(self.table.get(i).enabled and not self.table.get(i).consuming for i in training_list))
        self.assertEqual(self.table.get("training-list-direction-toggle").nodes, ("SC.Training.List.SortToggle",))
        training_grid = [i for i in reserved if i.startswith("training-list-slot-")]
        self.assertTrue(all(self.table.get(i).enabled and not self.table.get(i).consuming for i in training_grid))
        reserved = [i for i in reserved if i not in card_scan + training_list + training_grid]
        self.assertTrue(all(not self.table.get(i).enabled and not self.table.get(i).consuming for i in reserved), reserved)
        self.assertTrue(all(i not in self.table.node_actions().values() for i in reserved))
        self.assertIn('"game-start", "startup-collabo-report-ok", "popup-reward-ok", "popup-event-fan-reward-ok", "popup-event-fan-receipt-ok", '
                      '"popup-fes-period-reward-ok", "popup-mission-completed-ok", "popup-help-close"',
                      SELFTEST.read_text(encoding="utf-8"))  # the C# self-test pins the same ids
        # B4: マニー exchange; B5: one basic Bronze ticket, one フェス参加権. The difficulty tap does not consume.
        # W.I.N.G.: プロデュース開始 (stamina 30) and the 獲得報酬 再抽選 OK (stamina 30, opt-in, launcher-gated).
        self.assertEqual(sorted(self.table.consuming_ids), sorted(["shop-exchange-confirm", "training-start",
                                                                 "grade-rehearsal-easy", "grade-rehearsal-normal", "grade-rehearsal-hard",
                                                                   "grade-tension-confirm", "match-skip-confirm",
                                                                   "match-play-live-start", "weekly-auto-skip", "weekly-auto-start", "weekly-auto-soda-start",
                                                                   "wing-produce-start", "wing-reward-reroll-confirm", "collabo-tension-decide"]))
        self.assertEqual(self.table.get("match-skip-scroll-down").wheel, -60)
        self.assertIsNone(self.table.get("match-skip-plus-1").wheel)
        self.assertEqual(set(self.table.families), {"startup", "popup", "nav", "mission", "shop-money", "training-bronze",
                                                    "fes-menu", "grade-fes", "match-fes", "grade-live", "business", "card-scan", "knowhow-scan",
                                                    "present", "mission-claim", "weekly-auto", "wing-nav", "wing-week", "wing-live",
                                                    "wing-skill", "wing-end", "wing-setup", "wing-start", "wing-giveup",
                                                    "wing-halo", "collabo-fes"})
        self.assertEqual(self.table.get("training-start").nodes, ("SC.Training.Confirm.Start",))
        self.assertEqual(self.table.get("grade-tension-confirm").nodes, ("SC.Grade.Unit.Confirm",))
        self.assertFalse(self.table.get("grade-difficulty-normal").consuming)
        self.assertEqual(self.table.get("shop-exchange-confirm").family, "shop-money")
        self.assertEqual(self.table.get("shop-exchange-confirm").nodes, ("SC.Shop.Confirm.Exchange",))
        # Overlapping enabled rects are allowed only when recognition nodes tell them apart: a click resolves through
        # the armed node (PipelineActionTable.at(node=...)), so no node may be bound to two overlapping actions.
        enabled = [a for a in self.table.actions if a.enabled]
        for index, first in enumerate(enabled):
            for second in enabled[index + 1:]:
                if not pa._disjoint(first.rect, second.rect):
                    self.assertFalse(set(first.nodes) & set(second.nodes), (first.id, second.id))
                    self.assertTrue(first.nodes and second.nodes, (first.id, second.id))
        # Within the non-W.I.N.G. daily vocabulary rects stay disjoint, as before.
        # The 並び替え popup keys/決定 sit over the centre of the screen like the present/mission dialogs:
        # they are node-bound (SC.Training.SortPopup.*), so they follow the overlap rule above, not this one.
        popup = {"training-sort-apply", *[f"training-sort-key-r{r}c{c}" for r in range(1, 4) for c in range(1, 4)],
                 *[f"match-skip-plus-{i}" for i in range(1, 7)],
                 "match-skip-confirm", "match-skip-review", "match-skip-review-cancel", "match-skip-result-close",
                 "grade-mode-rehearsal", "grade-rehearsal-easy", "grade-rehearsal-normal", "grade-rehearsal-hard",
                 # 2026-10-11 live: the edge strips missed (an off-button tap closed 獲得報酬 like OK), so the business
                 # bottom-right buttons now sit on the button body; node-bound (SC.Business.*), overlap rule above.
                 "business-reward-redispatch", "business-dispatch-decide"}
        daily = [a for a in enabled if not a.family.startswith("wing-") and a.family not in {"present", "mission-claim", "knowhow-scan"}
                 and a.id not in popup]
        for index, first in enumerate(daily):
            for second in daily[index + 1:]:
                self.assertTrue(pa._disjoint(first.rect, second.rect) or self.table.version == 2
                                and first.nodes and second.nodes and not set(first.nodes).intersection(second.nodes),
                                (first.id, second.id))
        for legacy in ("reward-ack", "announcement-back", "fes-open", "shop-exchange-confirm-1",
                       "training-confirm-one-bronze", "grade-difficulty-expert", "mission-scroll-down"):
            with self.assertRaises(ValueError):
                self.table.get(legacy)

    def test_mission_claim_catalog_is_node_bound_and_non_consuming(self):
        expected = (
            ("mission-login-claim", (1042, 246, 168, 80), "SC.Missions.Claim"),
            ("mission-result-close", (557, 480, 166, 80), "SC.Missions.ResultClose"),
            ("mission-list-top", (1235, 114, 16, 18), "SC.Missions.SeekTop"),
            ("mission-list-bottom", (1235, 542, 16, 18), "SC.Missions.SeekBottom"),
            ("mission-tab-weekly", (30, 183, 130, 48), "SC.Missions.Tab.Weekly"),           # 2026-10-15 supervised
            ("mission-tab-daily", (30, 98, 130, 48), "SC.Missions.Tab.Daily"),
            ("mission-bulk-receive", (1110, 650, 130, 38), "SC.Missions.Bulk.Click"),          # 2026-10-10, reserved
            ("mission-bulk-confirm", (661, 419, 158, 72), "SC.Missions.Bulk.ConfirmClick"),
            ("mission-bulk-result-close", (561, 502, 158, 72), "SC.Missions.Bulk.ResultClose"),
        )
        self.assertEqual([a.id for a in self.table.actions if a.family == "mission-claim"],
                         [action_id for action_id, _, _ in expected])
        # Exercise the enabled vocabulary offline even while the shipped catalog remains disabled.
        data = json.loads(TABLE_PATH.read_bytes())
        for action in data["actions"]:
            if action["family"] == "mission-claim":
                action["enabled"] = True
        enabled = pa.parse_table(json.dumps(data).encode())
        for action_id, rect, node in expected:
            with self.subTest(action=action_id):
                action = self.table.get(action_id)
                self.assertEqual((action.rect, action.nodes, action.consuming, action.wheel),
                                 (rect, (node,), False, None))
                point = enabled.centre(action_id)
                self.assertEqual([a.id for a in enabled.at(point, {"mission-claim"}, node=node)], [action_id])
                self.assertEqual(enabled.at(point, {"nav"}, node=node), [])
                self.assertEqual(enabled.at(point, {"mission-claim"}, node="SC.Startup.TouchToStart"), [])
        self.assertIn("SC.Missions.Open", self.table.get("home-mission").nodes)
        self.assertIn("SC.Missions.Return", self.table.get("nav-back").nodes)

    def test_inner_rect_vectors_match_the_host_self_test(self):
        # Same vectors as browser_host/PipelineInputSelfTest.cs.
        self.assertEqual(self.table.get("nav-back").rect, (20, 628, 74, 66))
        self.assertEqual(self.table.inner_rect("nav-back"), (35, 642, 44, 38))
        self.assertEqual(self.table.inner_rect("game-start"), (751, 615, 78, 30))
        self.assertEqual(self.table.inner_rect("popup-pickup-close"), (1100, 56, 24, 22))
        self.assertEqual(self.table.get("popup-new-gacha-skip").rect, (1186, 78, 70, 15))
        self.assertEqual(self.table.inner_rect("popup-new-gacha-skip"), (1200, 81, 42, 9))
        self.assertFalse(self.table.get("popup-new-gacha-skip").consuming)
        self.assertEqual(self.table.centre("home-mission"), (63, 429))
        # B5j: LIVE REPORT OK has its own action, well inside the pink OK button; training-submit keeps the left part.
        self.assertEqual(self.table.get("live-report-ok").rect, (1141, 640, 34, 50))
        self.assertEqual(self.table.inner_rect("live-report-ok"), (1148, 650, 20, 30))
        self.assertEqual(self.table.get("training-submit").rect, (1097, 635, 44, 57))
        self.assertEqual(self.table.inner_rect("training-submit"), (1106, 647, 26, 33))
        # B6: the 営業 entry, inside the left padding of the home 営業 button (x ~745..864, y ~622..703).
        self.assertEqual(self.table.get("home-business").rect, (747, 658, 28, 40))
        self.assertEqual(self.table.inner_rect("home-business"), (753, 666, 16, 24))
        self.assertFalse(self.table.get("home-business").consuming)
        self.assertEqual(self.table.get("home-business").nodes, ("SC.Business.OpenBusiness",))
        self.assertIn("SC.Business.Return", self.table.get("nav-back").nodes)
        # B6b: one 「営業完了」 action per business slot (non-consuming); B6e adds 一括完了 / 一括営業 (non-consuming);
        # 差し入れ作成 / stamina / soda are never actions.
        self.assertEqual([a.id for a in self.table.actions if a.family == "business"],
                         ["home-business", "business-slot-complete-1", "business-slot-complete-2", "business-slot-complete-3",
                          "business-report-next", "business-slot-open-1", "business-slot-open-2", "business-slot-open-3",
                          "business-bulk-complete", "business-bulk-dispatch", "business-report-next-page",
                          "business-reward-redispatch", "business-dispatch-decide"])
        for k, rect, inner in ((1, (245, 518, 130, 44), (271, 527, 78, 26)), (2, (726, 544, 42, 24), (735, 549, 24, 14)),
                               (3, (1112, 512, 18, 33), (1116, 519, 10, 19))):
            action = self.table.get(f"business-slot-complete-{k}")
            self.assertEqual((action.rect, self.table.inner_rect(action.id), action.consuming, action.nodes),
                             (rect, inner, False, (f"SC.Business.Claim.Slot{k}",)))
        # B6d: WORK REPORT 「Touch to Next」 and the empty-slot card bodies (same vectors as the C# self-test).
        for action_id, rect, inner, node in (
                ("business-report-next", (558, 657, 99, 10), (578, 659, 59, 6),
                 ("SC.Business.Report.Next", "SC.Business.Bulk.ReportNext")),
                ("business-slot-open-1", (270, 180, 120, 140), (294, 208, 72, 84), ("SC.Business.Open.Slot1",)),
                ("business-slot-open-2", (640, 180, 200, 140), (680, 208, 120, 84), ("SC.Business.Open.Slot2",)),
                ("business-slot-open-3", (915, 180, 200, 130), (955, 206, 120, 78), ("SC.Business.Open.Slot3",)),
                # B6e (same vectors as the C# self-test)
                ("business-bulk-complete", (1096, 625, 34, 10), (1103, 627, 20, 6), ("SC.Business.Bulk.Complete",)),
                ("business-bulk-dispatch", (900, 626, 104, 33), (921, 633, 62, 19), ("SC.Business.Bulk.Dispatch",)),
                # B6f: 次へ of the paged WORK REPORT
                ("business-report-next-page", (1141, 690, 35, 9), (1148, 692, 21, 5), ("SC.Business.Report.PageNext",)),
                # 2026-10-11 (unverified, single-frame derived): 再営業へ of 獲得報酬 and 決定 of 営業アイドル選択
                ("business-reward-redispatch", (1145, 664, 85, 34), (1162, 671, 51, 20), ("SC.Business.Reward.Redispatch",)),
                ("business-dispatch-decide", (1145, 650, 85, 34), (1162, 657, 51, 20), ("SC.Business.Select.Decide",))):
            action = self.table.get(action_id)
            self.assertEqual((action.rect, self.table.inner_rect(action_id), action.consuming, action.nodes),
                             (rect, inner, False, node))
        self.assertIn("SC.Business.DispatchBack", self.table.get("nav-back").nodes)

    def test_sampled_points_respect_the_margin(self):
        rng = random.Random(20261002)
        for action in self.table.actions:
            x, y, w, h = action.rect
            ix, iy, iw, ih = self.table.inner_rect(action.id)
            self.assertTrue(ix - x >= w / 5 and (x + w) - (ix + iw) >= w / 5, action.id)
            self.assertTrue(iy - y >= h / 5 and (y + h) - (iy + ih) >= h / 5, action.id)
            self.assertEqual(self.table.sample_point(action.id, lambda n: 0), (ix, iy))
            self.assertEqual(self.table.sample_point(action.id, lambda n: n - 1), (ix + iw - 1, iy + ih - 1))
            for _ in range(500):
                px, py = self.table.sample_point(action.id, rng.randrange)
                self.assertTrue(ix <= px < ix + iw and iy <= py < iy + ih)
                if action.enabled:
                    self.assertEqual([a.id for a in self.table.at((px, py), node=action.nodes[0])], [action.id])
                else:
                    self.assertEqual(self.table.at((px, py), node=action.nodes[0]), [])

    def test_small_rect_falls_back_to_centre(self):
        raw = TABLE_PATH.read_bytes().replace(b"[1091, 48, 42, 38]", b"[1091, 48, 6, 6]")
        table = pa.parse_table(raw)
        self.assertIsNone(table.inner_rect("popup-pickup-close"))
        self.assertEqual(table.sample_point("popup-pickup-close", lambda n: 1 / 0), (1094, 51))

    def test_validation_rejects_the_same_mutations_as_the_host(self):
        text = TABLE_PATH.read_text(encoding="utf-8")
        mutations = [("[1091, 48, 42, 38]", '[1091, 48, 42, 38], "extra": 1'),
                     ("[1091, 48, 42, 38]", "[1260, 48, 42, 38]"), ("[1091, 48, 42, 38]", "[1091, 48, 42.0, 38]"),
                     ("[1091, 48, 42, 38]", "[-1, 48, 42, 38]"), ('"margin_percent": 20', '"margin_percent": 10'),
                     ('"id": "popup-pickup-close"', '"id": "popup-story-skip"'),
                     ('"id": "popup-pickup-close"', '"id": "Popup_Pickup"'), ('"family": "mission"', '"family": "shop"'),
                     ('"consuming": false', '"consuming": 0'),
                     ('"SC.MissionStatus.OpenMission"', '"SC.Startup.TouchToStart"'), ('"version": 2', '"version": 3'),
                     ('"enabled": false', '"enabled": 0')]
        for old, new in mutations:
            with self.subTest(new=new):
                self.assertIn(old, text)
                with self.assertRaises(ValueError):
                    pa.parse_table(text.replace(old, new).encode("utf-8"))

    def test_overlap_requires_v2_and_unique_nonempty_node_bindings(self):
        data = {"version": 1, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4,
                "families": ["test"], "actions": [
                    {"id": "a", "family": "test", "consuming": False, "rect": [0, 0, 100, 100],
                     "source": "fixture", "nodes": ["SC.Test.A"]},
                    {"id": "b", "family": "test", "consuming": False, "rect": [0, 0, 100, 100],
                     "source": "fixture", "nodes": ["SC.Test.B"]}]}
        def parse():
            return pa.parse_table(json.dumps(data).encode())
        with self.assertRaisesRegex(ValueError, "重叠"):
            parse()
        data["version"] = 2
        table = parse()
        self.assertEqual([a.id for a in table.at((50, 50))], ["a", "b"])
        self.assertEqual([a.id for a in table.at((50, 50), node="SC.Test.B")], ["b"])
        data["actions"][1]["nodes"] = []
        with self.assertRaisesRegex(ValueError, "必须绑定节点"):
            parse()
        data["actions"][1]["nodes"] = ["SC.Test.A"]
        with self.assertRaisesRegex(ValueError, "多个动作"):
            parse()

    def test_all_training_tile_sampling_stays_inside_native_grid(self):
        from agent.core import training_select as ts
        data = json.loads(TABLE_PATH.read_bytes())
        for action in data["actions"]:
            action["enabled"] = True
        table = pa.parse_table(json.dumps(data).encode())
        self.assertTrue(ts.scroll_wired(table))
        self.assertTrue(ts.scroll_wired(self.table))       # the wheel pair was enabled live on 2026-10-11
        for row in range(1, 4):
            for column in range(1, 8):
                box = (267 + 138 * (column - 1), 184 + 154 * (row - 1), 108, 108)
                expected = "training-select-card" if (row, column) == (1, 2) else f"training-list-slot-r{row}c{column}"
                self.assertEqual(ts.click_action(box, table), expected)
                self.assertEqual(ts.click_action(box, self.table), expected if self.table.get(expected).enabled else None)


if __name__ == "__main__":
    unittest.main()
