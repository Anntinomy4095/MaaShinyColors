"""Home counts the selected plan and verified daily targets, not a fixed checklist."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class DailyProgressTests(unittest.TestCase):
    def run_js(self, body):
        setup = """
import {readFileSync} from 'node:fs';
import {profileFromPreset} from './ui/components/store.js';
import {dailyPlanProgress} from './ui/components/daily-panel.js';
const schema=JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));
const annotations=JSON.parse(readFileSync('ui/annotations.json','utf8'));
const now=new Date('2026-10-12T03:00:00Z');
const profile=profileFromPreset(schema,'daily');
const state={schema,annotations,profile,prefs:{},runs:[],runReadings:[]};
const select=(names)=>profile.tasks.forEach(t=>t.enabled=names.includes(t.name));
const run=(tasks,mode='supervised_daily',at='2026-10-12T01:00:00Z')=>({mode,started_at:at,tasks});
const task=(name,status='succeeded',reason=null)=>({name,status,reason});
const grade=(completed,mode='grade')=>({kind:'grade',mode:'supervised_daily',ts:'2026-10-12T02:00:00Z',
  readings:{daily_progress:{mode,completed,attempts:completed,target:3}}});
"""
        result = subprocess.run(['node', '--input-type=module', '-e', setup + body], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_only_selected_tasks_count_and_rewards_have_their_own_row(self):
        data = self.run_js("""
select(['MissionStatus','MissionsClaim']);
state.runs=[run([task('MissionStatus')])];
console.log(JSON.stringify(dailyPlanProgress(state,now)));
""")
        # 2026-10-13: 每日任务进度 is an automatic read of the daily run, not a 任务 row: it is not counted here.
        self.assertEqual((data['done'], data['total']), (0, 1))
        self.assertEqual([row['key'] for row in data['items']], ['MissionsClaim'])
        claim = next(row for row in data['items'] if row['key'] == 'MissionsClaim')
        self.assertFalse(claim['done'])

    def test_grade_requires_current_mode_target_and_real_count(self):
        data = self.run_js("""
select(['GradeFes']);
Object.assign(profile.tasks.find(t=>t.name==='GradeFes').option, {'Grade每日次数':'3','Grade使用参加权':'Yes'});
state.runs=[run([task('GradeFes')])];
state.runReadings=[grade(1)]; const partial=dailyPlanProgress(state,now);
state.runReadings=[grade(3)]; const full=dailyPlanProgress(state,now);
state.runReadings=[grade(3,'rehearsal')]; const other=dailyPlanProgress(state,now);
console.log(JSON.stringify({partial,full,other}));
""")
        self.assertEqual(data['partial']['done'], 0)
        self.assertEqual(data['partial']['items'][0]['text'], '1 / 3 局')
        self.assertEqual(data['full']['done'], 1)
        self.assertEqual(data['other']['done'], 0)
        self.assertEqual(data['other']['items'][0]['text'], '— / 3 局')

    def test_offline_old_future_and_unidentified_runs_do_not_complete_today(self):
        data = self.run_js("""
select(['Training']);
state.runs=[run([task('Training')],'offline_replay'),run([task('Training')],'read_only'),
run([task('Training')],null),run([task('Training')],'supervised_daily','2026-10-11T19:59:00Z'),
run([task('Training')],'supervised_daily','2026-10-12T04:00:00Z')];
console.log(JSON.stringify(dailyPlanProgress(state,now)));
""")
        self.assertEqual((data['done'], data['total']), (0, 1))
        self.assertIsNone(data['items'][0]['status'])

    def test_partial_gifts_and_budget_skip_do_not_claim_full_completion(self):
        data = self.run_js("""
select(['PresentClaim','Shop']);
state.runs=[run([task('PresentClaim','succeeded','received_partial'),
  task('Shop','succeeded','skipped_rules: coin_reserve')])];
const pending=dailyPlanProgress(state,now);
state.runs.push(run([task('PresentClaim','succeeded','box_empty'),task('Shop','succeeded','bought')],
  'supervised_daily','2026-10-12T02:00:00Z'));
console.log(JSON.stringify({pending,complete:dailyPlanProgress(state,now)}));
""")
        self.assertEqual(data['pending']['done'], 0)
        self.assertEqual([row['text'] for row in data['pending']['items']], ['仍有未领取礼物', '未购买'])
        self.assertEqual(data['complete']['done'], 2)

    def test_current_actual_run_shows_running_and_uncertainty_without_stale_tasks(self):
        data = self.run_js("""
select(['Training','Shop']); state.runs=[run([task('Training'),task('Shop')])];
state.run={mode:'supervised_daily',startedAt:'2026-10-12T02:00:00Z',tasks:['Training']};
state.taskStatus={Training:{status:'uncertain'},Shop:{status:'failed'}};
console.log(JSON.stringify(dailyPlanProgress(state,now)));
""")
        self.assertEqual(data['done'], 1)
        self.assertEqual(next(row for row in data['items'] if row['key'] == 'Training')['status'], 'uncertain')
        self.assertEqual(next(row for row in data['items'] if row['key'] == 'Shop')['status'], 'succeeded')


    def test_done_today_lists_succeeded_tasks_for_the_rerun_warning(self):
        # 2026-10-28 user: a task that already succeeded today may run again after one 「已完成，是否继续」 warning.
        data = self.run_js("""
import {doneToday} from './ui/components/daily-panel.js';
const runs=[run([task('Shop'),task('Training','failed')]),run([task('PresentClaim')],'supervised_daily','2026-10-11T01:00:00Z')];
console.log(JSON.stringify([doneToday(runs,'2026-10-12',['Shop','Training','PresentClaim']),doneToday([],'2026-10-12',['Shop'])]));
""")
        self.assertEqual(data, [["Shop"], []])

    def test_start_notice_lists_done_and_uncertain_results_without_blocking(self):
        # 2026-10-28 user: 结果不确定 also only warns; 已核对 tasks drop out; W.I.N.G. lists only its uncertain result.
        data = self.run_js("""
import {startNotice} from './ui/components/daily-panel.js';
state.runs=[run([task('Shop'),task('Training','uncertain'),task('BusinessClaim','uncertain'),task('AutoProduce','uncertain')])];
state.acks={day:'2026-10-12',tasks:['BusinessClaim'],ledger:[]};
state.ledger=[{ts:'t1',task:'Shop',status:'uncertain'}];
const daily=startNotice(state,'2026-10-12',['Shop','Training','BusinessClaim']);
const wing=startNotice({...state,ledger:[]},'2026-10-12',['AutoProduce'],{done:false});
const none=startNotice({...state,runs:[],ledger:[]},'2026-10-12',['Shop']);
console.log(JSON.stringify([daily.done,daily.uncertain,daily.lines.length,daily.title,wing.uncertain,wing.done,none.lines]));
""")
        self.assertEqual(data, [["Shop"], ["Training"], 3, "继续运行？", ["AutoProduce"], [], []])


if __name__ == '__main__':
    unittest.main()
