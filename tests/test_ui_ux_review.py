"""2026-10-13 新人/老玩家 UX review: player texts for W.I.N.G. stops, the 「消耗：…」 line
that replaced the daily confirmation, the not-ready pre-check and the first-run guide."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def run_node(body):
    result = subprocess.run(["node", "--input-type=module", "-e", body], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class UxReviewJsTests(unittest.TestCase):
    def test_wing_stop_codes_have_player_text(self):
        out = run_node(
            "const O = await import('./ui/components/outcomes.js');"
            "console.log(JSON.stringify({ keys: Object.keys(O.WING_OUTCOME_TEXT),"
            " mini: O.outcomeText('minigame_not_automated', 'AutoProduce'), other: O.outcomeText('brand_new_stop', 'AutoProduce'),"
            " shop: O.outcomeText('brand_new_stop', 'Shop'), common: O.outcomeText('wait_stalled', 'AutoProduce') }));")
        source = (ROOT / "agent/core/wing_nav.py").read_text(encoding="utf-8")
        for code in out["keys"]:
            self.assertIn(f'"{code}"', source, code)
        self.assertIn("大富豪", out["mini"])
        self.assertIn("brand_new_stop", out["other"])
        self.assertIn("W.I.N.G. 已停止", out["other"])
        self.assertEqual(out["shop"], "brand_new_stop")
        self.assertIn("画面长时间未变化", out["common"])

    def test_task_settings_name_the_cost_and_the_cap(self):
        # 2026-10-26 (user): no daily confirmation dialog; each spending task shows 「消耗：…」 with its cap instead.
        out = run_node(
            "import { readFileSync } from 'node:fs';"
            "const D = await import('./ui/components/display.js');"
            "const state = { schema: JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8')),"
            " annotations: JSON.parse(readFileSync('ui/annotations.json', 'utf8')) };"
            "const entry = (name, option = {}) => ({ name, enabled: true, option });"
            "console.log(JSON.stringify({ training: D.costLine(state, 'Training', entry('Training')),"
            " grade: D.costLine(state, 'GradeFes', entry('GradeFes')), match: D.costLine(state, 'MatchSkip', entry('MatchSkip')),"
            " produce: D.costLine(state, 'AutoProduce', entry('AutoProduce')),"
            " shop: D.costLine(state, 'Shop', entry('Shop', { '商店跳过券': 'Yes', '商店金训练券': 'Yes' })),"
            " none: D.costLine(state, 'Mission', entry('Mission')) }));")
        self.assertTrue(all(out[k].startswith("消耗：") for k in ("training", "grade", "match", "produce", "shop")), out)
        self.assertIn("/天", out["training"])
        self.assertIn("不用回复券", out["grade"])
        self.assertIn("不用回复券", out["match"])
        self.assertIn("体力 30/局 · 每次 1 局", out["produce"])   # 2026-10-26 连跑: 「局数」 1 (default)
        self.assertIn("今日限定 2 件", out["shop"])
        self.assertIsNone(out["none"])

    def test_wing_stop_text_follows_the_code_text(self):
        # #12: the host passes "code：detail.text" from the wing report (UiTaskResult.WingReason).
        out = run_node(
            "const O = await import('./ui/components/outcomes.js');"
            "console.log(JSON.stringify({ text: O.outcomeText('produce_settings_stale：体力回復演出 改过', 'AutoProduce'),"
            " detail: O.outcomeText('click_unavailable:home_minigame_menu', 'AutoProduce') }));")
        self.assertTrue(out["text"].startswith("Produce 设置可能改过"))
        self.assertTrue(out["text"].endswith("（体力回復演出 改过）"))
        self.assertIn("大富豪", out["detail"])

    def test_missing_recognition_resources_block_the_start(self):
        # #4: host status recognition_ready=false -> start pre-check and 待处理 say so; old hosts (no field) do not.
        out = run_node(
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {} };"
            "const T = await import('./ui/components/daily-panel.js');"
            "const host = (extra) => ({ connected: true, pipeline_input_enabled: true, ...extra });"
            "const policy = { live_input_enabled: true, risk_reference_resolvable: true };"
            "const missing = { host: host({ recognition_ready: false }), policy };"
            "console.log(JSON.stringify({ missing: T.liveProblems(missing),"
            " ready: T.liveProblems({ host: host({ recognition_ready: true }), policy }),"
            " old: T.liveProblems({ host: host({}), policy }),"
            " start: T.liveStartProblems(missing), mock: T.liveStartProblems({ ...missing, bridgeKind: 'mock' }),"
            " dev: T.liveStartProblems(missing, 'read_only') }));")
        self.assertEqual(out["missing"], ["识别资源缺失：重新解压完整安装包"])
        self.assertEqual(out["ready"], [])
        self.assertEqual(out["old"], [])
        # 2026-10-26: the 开始 buttons are disabled with this reason; the preview and developer runs need no game input
        self.assertEqual(out["start"], out["missing"])
        self.assertEqual((out["mock"], out["dev"]), ([], []))


class UxReviewSourceTests(unittest.TestCase):
    def test_start_checks_live_problems_before_the_confirmation(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        start = app.index("startRun: async")
        check = app.index("liveProblems(s, { start: true })", start)
        # no dialog (W.I.N.G. since 2026-10-13, daily since 2026-10-26): the check still comes before the consent and the save
        self.assertLess(check, app.index("request.confirmed = true;", start))
        self.assertLess(check, app.index("await persist()", start))
        self.assertNotIn("openDailyRunConfirm", app)
        panel = (UI / "components/daily-panel.js").read_text(encoding="utf-8")
        self.assertIn("export function liveProblems(", panel)
        self.assertIn("export function liveStartProblems(", panel)
        self.assertIn("INPUT_OFF_TEXT", panel)
        for page in ("task-panel.js", "wing-panel.js", "home-panel.js"):   # every 开始 is disabled with the reason
            self.assertIn("liveStartProblems(", (UI / "components" / page).read_text(encoding="utf-8"), page)
        settings = (UI / "components/settings-panel.js").read_text(encoding="utf-8")
        self.assertIn("liveProblems(state)", settings)

    def test_home_first_run_guide_and_history_details(self):
        home = (UI / "components/home-panel.js").read_text(encoding="utf-8")
        self.assertIn("export function firstRunSteps(", home)
        # 2026-11-23: Maa closes help dialogs (SC.Popup.AnyHelp); the guide ends with the card-details download tip.
        self.assertNotIn("首次教程", home)
        self.assertIn("export function firstRunTip(", home)
        log = (UI / "components/log-panel.js").read_text(encoding="utf-8")
        self.assertIn("history-tasks", log)
        self.assertIn("dev_assets/private/ui/runs/", log)

    def test_recording_folder_entry_is_removed_and_player_guide_remains(self):
        support = (UI / "components/support-settings.js").read_text(encoding="utf-8")
        self.assertIn('actions.support?.("open_guide")', support)
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        ops = protocol["commands"]["app.support"]["params"]["properties"]["op"]["enum"]
        self.assertNotIn("open_videos", ops)
        self.assertIn("open_guide", ops)
        host = (ROOT / "browser_host/Ui/UiSupport.cs").read_text(encoding="utf-8")
        self.assertNotIn('case "open_videos":', host)
        self.assertIn('Path.Combine(_root, "README.md")', host)


if __name__ == "__main__":
    unittest.main()
