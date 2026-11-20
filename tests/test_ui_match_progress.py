"""Match's last observed open and Skip boundaries stay distinct in the task UI."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class MatchProgressPageTests(unittest.TestCase):
    def run_js(self, body):
        setup = """
import { matchProgressSummary } from './ui/components/match-progress.js';
const now = new Date('2026-10-12T08:00:00Z');
const reading = (progress = {}, extra = {}) => ({kind:'match', run_id:'actual', mode:'supervised_daily',
  ts:'2026-10-12T07:00:00Z', readings:{progress:{latest_open_stage:'A7-S20',
    latest_skippable_stage:'A5-S15', skip_list_complete:true, ...progress}}, ...extra});
const state = {runReadings:[reading()]};
"""
        result = subprocess.run(['node', '--input-type=module', '-e', setup + body], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_open20_and_skip15_are_distinct(self):
        out = self.run_js('console.log(JSON.stringify(matchProgressSummary(state,now)));')
        self.assertIn('最新开放 7-20', out)
        self.assertIn('最新可跳过 5-15', out)
        self.assertIn('上次读取', out)

    def test_explicit_and_partial_surveys_do_not_claim_open_or_complete_skip(self):
        out = self.run_js("""
const explicit = matchProgressSummary({...state,runReadings:[reading({latest_open_stage:null})]},now);
const partial = matchProgressSummary({...state,runReadings:[reading({skip_list_complete:false})]},now);
console.log(JSON.stringify({explicit,partial}));
""")
        self.assertIn('最新开放 尚未读取', out['explicit'])
        self.assertIn('最新可跳过 列表未读完', out['partial'])
        self.assertNotIn('5-15', out['partial'])

    def test_current_day_live_readings_only_and_old_history_compatibility(self):
        out = self.run_js("""
const excluded = [reading({}, {mode:'offline_replay'}), reading({}, {mode:'readonly_live'}),
  reading({}, {ts:'2026-10-11T19:59:59Z'}), reading({}, {ts:'2026-10-12T09:00:00Z'})];
const old = reading(); delete old.mode;
console.log(JSON.stringify({excluded:excluded.map(r=>matchProgressSummary({runReadings:[r]},now)),
  history:matchProgressSummary({runReadings:[old],runs:[{id:'actual',mode:'supervised_daily'}]},now),
  missing:matchProgressSummary({runReadings:[]},now)}));
""")
        self.assertEqual(out['excluded'], [''] * 4)
        self.assertEqual(out['missing'], '')
        self.assertIn('最新开放 7-20', out['history'])


if __name__ == '__main__':
    unittest.main()
