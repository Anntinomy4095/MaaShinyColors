"""ANN-17 / ANN-29 page helpers: the daily start (no dialog since 2026-10-26: 开始 is the consent and the
host re-derives spending), passive reading tiles for the 资源 page and the real Produce end result (reward parts that
were not read)."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DailyRunPageTests(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_first_run_waits_for_saved_settings_and_save_failure_never_starts(self):
        out = self.run_node("""
import './tests/fixtures/ui/fake_dom.mjs';
globalThis.HTMLElement ||= class {};
globalThis.customElements ||= {define() {}, get() { return null; }};
globalThis.window ||= {};
import {readFileSync} from 'node:fs';
const S = await import('./ui/components/store.js');
const D = await import('./ui/components/display.js');
const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));
const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));
const app = readFileSync('ui/app.js', 'utf8');
const persistSource = app.slice(app.indexOf('  const persist = () => {'), app.indexOf('  // U17: undoable'));
const startSource = app.slice(app.indexOf('startRun: async') + 'startRun: '.length, app.indexOf('    startWing:'))
  .trim().replace(/,$/, '');
async function attempt({fail = false, blocked = false, single = false} = {}) {
  // A clean install has no saved tasks, while the page already shows the default daily plan.
  const profile = S.applyTaskPlacement(S.sanitizeProfile(schema, {schema_version: 1}, 'daily'),
    D.taskPlacement(annotations, schema));
  if (single) profile.tasks.find(t => t.name === 'Training').enabled = false;
  const original = JSON.stringify(profile);
  let state = {schema, annotations, profile, prefs: {}, dailyManual: {}, taskStatus: {},
    saveStatus: {state: 'saved'}, run: {active: false}, runMode: 'supervised_daily'};
  const calls = [], messages = [];
  let saved = null, releaseSave;
  const saveReady = new Promise(resolve => releaseSave = resolve);
  const store = {get: () => state, set: patch => state = {...state, ...patch}};
  const bridge = {kind: 'host', async request(command, params) {
    calls.push({command, params: structuredClone(params)});
    if (command === 'profile.save') {
      await saveReady;
      if (fail) throw new Error('disk full');
      saved = structuredClone(params.profile);
      return {saved: true};
    }
    if (command === 'run.start') return {run_id: 'daily-test'};
    throw new Error(command);
  }};
  const start = new Function('env', `const {store, bridge, schema, PROFILE_VERSION, PREFS_KEY, formatTime,
    saveLocal, toast, singleTaskState, dailyRunState, WING_TASK, wingBlockers, startBlockers, devMode, wingOnly,
    profileToRunConfig, profileSpendFlags, liveProblems, saveAcks, startNotice, gameDay, taskInQueue, openConfirm} = env;
    ${persistSource}; return (${startSource});`)({
      store, bridge, schema, PROFILE_VERSION: S.PROFILE_VERSION, PREFS_KEY: 'prefs', formatTime: () => 'now',
      saveLocal() {}, toast: text => messages.push(text), ...D,
      profileToRunConfig: S.profileToRunConfig, profileSpendFlags: S.profileSpendFlags,
      liveProblems: () => blocked ? ['游戏窗口未连接'] : [], saveAcks() {},
      // the same-day warning (startNotice) has its own tests; here it has nothing to say
      startNotice: () => ({lines: []}), gameDay: () => '2026-10-28', taskInQueue: () => true, openConfirm: async () => true,
    });
  const pending = start(single ? 'Training' : null);
  await new Promise(resolve => setImmediate(resolve));
  const beforeSave = calls.map(c => c.command);
  releaseSave();
  await pending;
  return {beforeSave, commands: calls.map(c => c.command), saved, original: JSON.parse(original),
    unchanged: JSON.stringify(state.profile) === original, run: state.run, status: state.saveStatus,
    request: calls.find(c => c.command === 'run.start')?.params, messages};
}
console.log(JSON.stringify({fresh: await attempt(), failed: await attempt({fail: true}),
  blocked: await attempt({blocked: true}), single: await attempt({single: true})}));
""")
        fresh = out["fresh"]
        self.assertEqual(fresh["beforeSave"], ["profile.save"])
        self.assertEqual(fresh["commands"], ["profile.save", "run.start"])
        self.assertEqual(fresh["saved"]["tasks"], fresh["original"]["tasks"])
        self.assertEqual(fresh["saved"]["removed_tasks"], fresh["original"]["removed_tasks"])
        self.assertIn("allow_training_spend", fresh["request"]["spend_flags"])
        self.assertIn("allow_match_spend", fresh["request"]["spend_flags"])
        self.assertIs(fresh["request"]["confirmed"], True)   # 开始 is the consent; no dialog in between
        self.assertTrue(fresh["run"]["active"])
        self.assertTrue(fresh["unchanged"])
        self.assertEqual(out["failed"]["commands"], ["profile.save"])
        self.assertEqual(out["failed"]["status"]["state"], "failed")
        self.assertFalse(out["failed"]["run"]["active"])
        self.assertEqual(out["blocked"]["commands"], [])   # nothing saved or sent while the game cannot be operated
        self.assertEqual(out["blocked"]["messages"], ["无法开始：游戏窗口未连接"])
        single = out["single"]
        self.assertTrue(single["unchanged"])
        self.assertFalse(next(t for t in single["saved"]["tasks"] if t["name"] == "Training")["enabled"])
        self.assertEqual([t["name"] for t in single["request"]["config"]["tasks"] if t["enabled"]], ["Training"])

    def test_passive_tiles_and_newest_wins(self):
        out = self.run_node(
            "import { passiveTiles, mergeTiles } from './ui/components/passive-readings.js';"
            "const data = { readings: {"
            " 'resource.money': { value: { count: 120000 }, observed_at: '2026-10-11T10:00:00+00:00', source: 'shop', stale: false },"
            " 'resource.fes_rights': { value: { count: 19, max: 20 }, observed_at: '2026-10-11T09:00:00+00:00', source: 'grade' },"
            " 'resource.jewel': { value: { count: 5, indicator_only: true }, observed_at: '2026-10-11T09:00:00+00:00', source: 'x' },"
            " 'shop.offers': { value: { bought_ranks: [1] }, observed_at: '2026-10-11T09:00:00+00:00', source: 'shop' } } };"
            "const tiles = passiveTiles(data);"
            "const run = [{ key: 'money', item: 'money', value: 1, observed_at: '2026-10-11T11:00:00+00:00' }];"
            "console.log(JSON.stringify({ tiles, merged: mergeTiles(run, tiles), bad: passiveTiles(null) }));")
        items = {t["item"]: t for t in out["tiles"]}
        self.assertEqual(set(items), {"money", "fes", "jewel"})   # 2026-10-12: ジュエル shown (indicator only, never spent)
        self.assertEqual((items["fes"]["value"], items["fes"]["max"]), (19, 20))
        merged = {t["item"]: t for t in out["merged"]}
        self.assertEqual(merged["money"]["value"], 1)          # the newer run reading wins
        self.assertEqual(merged["fes"]["value"], 19)
        self.assertEqual(out["bad"], [])

    def test_end_result_shows_the_unread_reward_parts(self):
        out = self.run_node(
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { cleanEndPayload, cleanEndResult, missingText } from './ui/components/produce-end.js';"
            "const payload = { version: 1, summary: { rank: 'B+' }, recommendation: { auto_action: 'delete' },"
            " reward_missing: ['book_2', 'book_1_protect', 'ex_skills', 'BAD CODE'] };"
            "const clean = cleanEndPayload(payload);"
            "console.log(JSON.stringify({ missing: clean.rewardMissing, auto: clean.autoAction,"
            " texts: clean.rewardMissing.map(missingText),"
            " result: cleanEndResult({ written_at: '2026-10-11T10:00:00+09:00', payload }) !== null,"
            " noStamp: cleanEndResult({ payload }) }));")
        self.assertEqual(out["missing"], ["book_2", "book_1_protect", "ex_skills"])
        self.assertIsNone(out["auto"])
        self.assertEqual(out["texts"][0], "第 2 本ノウハウブック")
        self.assertIn("保護", out["texts"][1])
        self.assertTrue(out["result"])
        self.assertIsNone(out["noStamp"])


if __name__ == "__main__":
    unittest.main()
