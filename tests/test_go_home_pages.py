"""Go-home page coverage: reasoned stops, the table-binding proposal and offline replays on native frames.

The page back nodes (Present, アイドル一覧, 営業 Top, Match ステージ選択, Grade 难度/参加ユニット, Produce 开始前三页) ship as
``docs/proposals/go_home_pages_20261009.json`` until the action table can be rebuilt (the host compares its table hash
with the file). Until ``tools/go_home_pages.py --apply`` landed them, the replay merges the proposal in memory.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent.core.pipeline_actions import default_table, parse_table
from tools import build_native_resource as bundle_tool
from tools import go_home_pages as pages
from tools import replay_common_pipeline as harness
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets/resource/base/pipeline"
APPLIED = (PIPELINE / "common" / "pages.json").is_file()
STOPS = {"SC.Common.Stop.ProduceInProgress": "produce_in_progress", "SC.Common.Stop.BusinessReport": "business_report_pending",
         "SC.Common.Stop.GradeLive": "grade_live_in_progress", "SC.Common.Stop.Maintenance": "maintenance",
         "SC.Common.Stop.CommunicationError": "communication_error", "SC.Common.Stop.FesMenuOpen": "fes_menu_open",
         "SC.Common.Stop.StoryUnverified": "story_overlay_unverified"}

try:
    from maa.library import Library
    HAVE_MAA = Library.version() == harness.FRAMEWORK
except Exception:  # pragma: no cover
    HAVE_MAA = False


def pipeline_nodes():
    nodes = {}
    for path in sorted(PIPELINE.rglob("*.json")):
        nodes.update(load_jsonc(path))
    return nodes


class ReasonedStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = pipeline_nodes()

    def test_stops_end_with_a_bounded_reason_and_never_click(self):
        for name, reason in STOPS.items():
            with self.subTest(name=name):
                node = self.nodes[name]
                self.assertEqual((node["action"], node["custom_action"]), ("Custom", "SC.FailTask"))
                self.assertEqual(node["custom_action_param"], {"reason": reason})
                self.assertNotIn("next", node)
                self.assertNotIn("target", node)

    def test_back_one_dispatches_clicks_first_then_reasoned_stops_then_unknown(self):
        back = self.nodes["SC.Nav.BackOne"]
        stops = [n for n in back["next"] if n.startswith("SC.Common.Stop.")]
        self.assertEqual(set(stops), {n for n in STOPS if n != "SC.Common.Stop.FesMenuOpen"})
        index = back["next"].index(stops[0])
        self.assertEqual(back["next"][index:], stops + ["SC.Nav.NotHandled"])
        self.assertTrue(all(self.nodes[n].get("action") == "Click" for n in back["next"][:index]))
        self.assertTrue(set(stops) <= set(back["any_of"]))

    def test_home_with_open_fes_menu_stops_with_its_reason(self):
        reached = self.nodes["SC.Common.HomeReached"]
        self.assertEqual(reached["on_error"], ["SC.Common.Stop.FesMenuOpen", "SC.Common.FailTask"])

    def test_produce_active_uses_header_text_only(self):
        active = self.nodes["SC.Common.Presence.ProduceActive"]
        for name in active["any_of"]:
            node = self.nodes[name]
            self.assertEqual(node["recognition"], "OCR")
            self.assertLessEqual(node["roi"][1] + node["roi"][3], 70)  # header strip only: no portraits/counters below it

    def test_present_and_missions_routes_back_out_through_the_shared_dispatcher(self):
        for route in ("SC.Present.Route", "SC.Missions.Route"):
            self.assertEqual(self.nodes[route]["next"][-1], "[JumpBack]SC.Nav.BackOne", route)


class ProposalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not pages.PROPOSAL.is_file():
            raise unittest.SkipTest("需要 docs/proposals 下的回首页提案文件")
        cls.proposal = json.loads(pages.PROPOSAL.read_text(encoding="utf-8"))
        cls.merged = pages.merged_pipeline(cls.proposal)
        cls.clicks = {n: v for n, v in cls.merged.items() if v.get("action") == "Click"}

    def test_proposal_file_is_current(self):
        self.assertEqual(pages.PROPOSAL.read_text(encoding="utf-8"), pages._dump(pages.build_proposal()))

    def test_every_page_has_guard_back_and_settled(self):
        self.assertEqual(set(self.clicks), {f"SC.Nav.{p.key}.Back" for p in pages.PAGES})
        for page in pages.PAGES:
            back, guard = self.clicks[f"SC.Nav.{page.key}.Back"], self.merged[f"SC.Nav.{page.key}.Guard"]
            settled = self.merged[f"SC.Nav.{page.key}.Settled"]
            with self.subTest(page=page.key):
                self.assertIs(back["enabled"], False)
                self.assertEqual(back["target"], pages.BACK_RECT)
                self.assertEqual(back["all_of"], guard["all_of"])
                self.assertGreaterEqual(len(back["all_of"]), 3)
                self.assertIn(pages.BACK_UNDIMMED, back["all_of"])
                self.assertTrue(1 <= back["max_hit"] <= 3 and 0 < back["timeout"] <= 30000)
                self.assertEqual(back["on_error"], ["SC.Common.FailTask"])
                self.assertEqual(back["next"], [f"SC.Nav.{page.key}.Settled"])
                self.assertNotIn(guard_name := f"SC.Nav.{page.key}.Guard", settled["any_of"], guard_name)
                self.assertNotIn("next", settled)
                for landing in page.lands:
                    self.assertIn(landing, settled["any_of"])
                self.assertIn("SC.Loading.Present", settled["any_of"])

    def test_back_one_keeps_stops_last_and_lists_every_new_page_once(self):
        back = self.proposal["pipeline"][pages.NAVIGATION_FILE]["SC.Nav.BackOne"]
        for key in ("any_of", "next"):
            self.assertEqual(len(back[key]), len(set(back[key])), key)
        stops = [n for n in back["next"] if n.startswith("SC.Common.Stop.")]
        self.assertEqual(back["next"][-(len(stops) + 1):], stops + ["SC.Nav.NotHandled"])
        for page in pages.PAGES:
            self.assertIn(f"SC.Nav.{page.key}.Back", back["next"][:-len(stops) - 1])
            self.assertIn(f"SC.Nav.{page.key}.Guard", back["any_of"])

    def test_no_consuming_or_dialog_targets(self):
        """Back is the only click; no page of the proposal may be a purchase/consume/start confirmation."""
        table = default_table()
        action = table.get(pages.BACK_ACTION)
        self.assertEqual(list(action.rect), pages.BACK_RECT)
        self.assertFalse(action.consuming)
        text = json.dumps(self.merged, ensure_ascii=False)
        for forbidden in ("native/shop_confirm/", "native/shop_result/", "native/match_skip/", "StartConfirm"):
            self.assertNotIn(forbidden, text)

    def test_table_patch_only_appends_node_names_to_nav_back(self):
        raw = pages.TABLE.read_text(encoding="utf-8")
        patched = pages.patch_table_text(raw, self.proposal["table_bindings"])
        before, after = json.loads(raw), json.loads(patched)
        names = self.proposal["table_bindings"][pages.BACK_ACTION]
        for item in after["actions"]:
            old = next(a for a in before["actions"] if a["id"] == item["id"])
            if item["id"] == pages.BACK_ACTION:
                self.assertEqual(item["nodes"], old["nodes"] + [n for n in names if n not in old["nodes"]])
                item = {**item, "nodes": old["nodes"]}
            self.assertEqual(item, old)
        self.assertEqual({k: v for k, v in after.items() if k != "actions"}, {k: v for k, v in before.items() if k != "actions"})
        table = parse_table(patched.encode("utf-8"))
        self.assertTrue(set(names) <= set(table.node_actions()))
        self.assertEqual({table.node_actions()[n] for n in names}, {pages.BACK_ACTION})
        self.assertEqual(pages.patch_table_text(patched, self.proposal["table_bindings"]), patched)  # idempotent

    def test_apply_on_a_copy_yields_a_consistent_pipeline_and_table(self):
        from tools.run_tasks import click_node_actions

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copytree(PIPELINE, tmp / "pipeline")
            shutil.copy(pages.TABLE, tmp / "table.json")
            if APPLIED:  # already applied: apply is a no-op rewrite of the same content
                (tmp / "pipeline" / pages.PAGES_FILE).unlink()
            pages.apply(self.proposal, tmp / "pipeline", tmp / "table.json")
            table = parse_table((tmp / "table.json").read_bytes())
            bound = click_node_actions(tmp / "pipeline", table)  # every Click bound, target == action rect, default-disabled
            for page in pages.PAGES:
                self.assertEqual(bound[f"SC.Nav.{page.key}.Back"], pages.BACK_ACTION)
            written = json.loads((tmp / "pipeline" / pages.NAVIGATION_FILE).read_text(encoding="utf-8"))
            self.assertEqual(written["SC.Nav.BackOne"], self.proposal["pipeline"][pages.NAVIGATION_FILE]["SC.Nav.BackOne"])
            self.assertEqual(json.loads((tmp / "pipeline" / pages.PAGES_FILE).read_text(encoding="utf-8")),
                             self.proposal["pipeline"][pages.PAGES_FILE])

    def test_apply_refuses_without_the_table_confirmation_flag(self):
        self.assertEqual(pages.main(["--apply"]), 2)


PRIVATE_READY = HAVE_MAA and not bundle_tool.verify() and not harness.missing_frames()


@unittest.skipUnless(PRIVATE_READY, "需要MaaFramework v5.12.3、私有模板包与私有原生帧")
class ProposalReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Always merge the proposal: pages added after the last --apply (ItemList, Gacha) live only there.
        proposal = json.loads(pages.PROPOSAL.read_text(encoding="utf-8"))
        cls.report = harness.replay(harness.PROPOSAL_CASES, proposal=proposal)
        cls.by_id = {c["id"]: c for c in cls.report["cases"]}

    def test_every_page_walks_home_with_only_back_clicks(self):
        self.assertEqual([c["id"] for c in self.report["cases"] if not c["passed"]], [])
        self.assertEqual(self.report["real_input_attempts"], 0)
        for case in harness.PROPOSAL_CASES:
            result = self.by_id[case.id]
            self.assertEqual(result["simulated_actions"], list(case.clicks), case.id)
            self.assertTrue(set(result["simulated_actions"]) <= {"nav-back"}, case.id)
            if case.outcome == "home":
                self.assertEqual(result["node_trace"][-1], harness.HOME_END, case.id)

    def test_dialogs_over_the_pages_are_never_clicked(self):
        for case_id in ("present-bulk-confirm-never-clicked", "present-bulk-receipt-never-clicked",
                        "produce-setup-unit-copy-dialog-never-clicked"):
            self.assertEqual(self.by_id[case_id]["simulated_clicks"], [], case_id)
            self.assertFalse(self.by_id[case_id]["framework_succeeded"], case_id)

    def test_every_replay_frame_exists_and_reports_one_real_path_per_page(self):
        covered = {p.key for p in pages.PAGES}
        self.assertEqual(covered, {"Present", "IdolList", "KnowhowList", "Business", "MatchStages", "GradeDifficulty", "GradeUnit",
                                   "ProduceSelect", "ProduceUnit", "ProduceItem", "ItemList", "Gacha",
                                   "ShopPremium", "AutoSchedule", "TrainingEvent",
                                   "CollaboEvent", "CollaboDifficulty", "CollaboUnit"})
        self.assertGreaterEqual(len([c for c in harness.PROPOSAL_CASES if c.outcome == "home"]), len(covered))


if __name__ == "__main__":
    unittest.main()
