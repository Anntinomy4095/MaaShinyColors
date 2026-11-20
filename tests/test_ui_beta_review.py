"""Beta UI regressions: explicit read-only mode, reconciled outcomes and failed plan writes."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class BetaUiReviewTests(unittest.TestCase):
    def run_js(self, body):
        setup = """
import './tests/fixtures/ui/fake_dom.mjs';
globalThis.HTMLElement ||= class {};
globalThis.customElements ||= { define() {}, get() { return null; } };
globalThis.window ||= {};
import { readFileSync } from 'node:fs';
const app = readFileSync('ui/app.js', 'utf8');
"""
        result = subprocess.run(["node", "--input-type=module", "-e", setup + body],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_wing_and_single_wing_keep_explicit_developer_run_mode(self):
        data = self.run_js("""
const D = await import('./ui/components/display.js');
const S = await import('./ui/components/store.js');
const schema = JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));
const annotations = JSON.parse(readFileSync('ui/annotations.json','utf8'));
const profile = S.addTaskToPlan(schema, S.profileFromPreset(schema,'daily'), 'AutoProduce');
const base = {schema, annotations, profile, prefs:{developer:true}, run:{active:false}, taskStatus:{}};
// 2026-10-13: W.I.N.G. starts from the 「Produce」 tab (startRun(WING_TASK) -> wingRun) and its run bar is wingRunBar.
const section = app.slice(app.indexOf('      const profile = s.profile;'), app.indexOf('      const config = profileToRunConfig'));
const resolve = new Function('s','wingRun','wingOnly','devMode',section + 'return {mode,wing};');
const out = [];
for (const runMode of ['read_only','offline_replay','supervised_daily']) {
  const selected = D.singleTaskState({...base,runMode},'AutoProduce');
  out.push({runMode,...resolve(selected,true,D.wingOnly,D.devMode), summary:D.wingRunBar(selected)});
}
console.log(JSON.stringify(out));
""")
        self.assertEqual([row["mode"] for row in data], ["read_only", "offline_replay", "supervised_wing"])
        self.assertEqual([row["wing"] for row in data], [False, False, True])
        self.assertNotIn("实机", data[0]["summary"]["main"])
        self.assertEqual(data[0]["summary"]["devRun"], "read_only")
        self.assertEqual(data[1]["summary"]["devRun"], "offline_replay")
        self.assertIsNone(data[2]["summary"]["devRun"])            # 2026-10-24: the live run bar names the goal, not 「实机」
        self.assertTrue(data[2]["summary"]["main"].startswith("W.I.N.G."))

    def test_record_outcome_overrides_successful_task_rows_and_preserves_takeover(self):
        data = self.run_js("""
const {runOutcome} = await import('./ui/components/daily-panel.js');
const {STATUS} = await import('./ui/components/common.js');
const tasks = [{name:'Training',status:'succeeded'}];
const values = ['uncertain','failed','refused','stopped','succeeded',undefined,'unknown']
  .map(outcome=>runOutcome({outcome,tasks}));
const takeover = runOutcome({outcome:'stopped',tasks:[{status:'takeover'},{status:'skipped'}]});
console.log(JSON.stringify({values,takeover,stopped:STATUS.stopped}));
""")
        self.assertEqual(data["values"], ["uncertain", "failed", "guard_refused", "stopped", "succeeded", "succeeded", "succeeded"])
        self.assertEqual(data["takeover"], "takeover")
        self.assertEqual(data["stopped"]["label"], "已停止")

    def test_plan_storage_failure_keeps_state_and_returns_dialog_error(self):
        data = self.run_js("""
const P = await import('./ui/components/plans.js');
const S = await import('./ui/components/store.js');
const schema = JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));
const original = {plans:[], activePlan:null, profile:S.profileFromPreset(schema,'daily')};
let state = original;
const messages = [];
const store = {get:()=>state,set:patch=>state={...state,...patch}};
const source = app.slice(app.indexOf('savePlanAs: (rawName) =>') + 'savePlanAs: '.length,app.indexOf('    updatePlan:')).trim().replace(/,$/,'');
const save = new Function('store','upsertPlan','savePlanStore','toast','return (' + source + ')')
  (store,P.upsertPlan,P.savePlanStore,(text)=>messages.push(text));
window.localStorage = {setItem(){throw new Error('quota exceeded');}};
const failed = save('每日');
const unchanged = state === original;
const failedStore = P.savePlanStore([],null);
let written = null;
window.localStorage = {setItem(key,value){written={key,value:JSON.parse(value)};}};
const saved = save('每日');
console.log(JSON.stringify({failed,unchanged,failedStore,messages,savedName:saved.name,
  active:state.activePlan,written}));
""")
        self.assertIn("保存失败", data["failed"]["error"])
        self.assertTrue(data["unchanged"])
        self.assertFalse(data["failedStore"])
        self.assertIn("保存失败", data["messages"][0])
        self.assertEqual(data["messages"][1], "已保存方案「每日」")
        self.assertEqual(data["savedName"], "每日")
        self.assertEqual(data["active"], "每日")
        self.assertEqual(data["written"]["value"]["active"], "每日")


if __name__ == "__main__":
    unittest.main()
